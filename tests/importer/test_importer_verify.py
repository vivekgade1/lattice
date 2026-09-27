"""lattice.importer.verify: the differential of an original app against a Lattice import.

Two tiny FastAPI apps over one SQLite table stand in for the original and the import;
a bearer token is simply the caller's handle.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from lattice.importer.verify import (
    Case,
    DatetimeWire,
    Deviation,
    ErrorEnvelope,
    Normalizer,
    ValidationEnvelope,
    raise_sites,
    verify,
)

T1 = str(uuid.uuid5(uuid.NAMESPACE_URL, "thing/1"))
T2 = str(uuid.uuid5(uuid.NAMESPACE_URL, "thing/2"))
MISSING = "00000000-0000-4000-8000-000000000000"


def make_db(variant: str, path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE thing (id TEXT PRIMARY KEY, owner TEXT, title TEXT)")
    conn.executemany(
        "INSERT INTO thing VALUES (?, ?, ?)",
        [(uuid.UUID(T1).hex, "alice", "one"), (uuid.UUID(T2).hex, "bob", "two")],
    )
    if variant == "empty":
        conn.execute("DELETE FROM thing")
    conn.commit()
    conn.close()
    return path


class NewThing(BaseModel):
    title: str = Field(min_length=1)


def _caller(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return authorization.removeprefix("Bearer ")


def read_thing(id: uuid.UUID, db: Path, authorization: str | None) -> Any:
    user = _caller(authorization)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT owner, title FROM thing WHERE id = ?", (id.hex,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Thing not found")
    if row[0] != user:
        raise HTTPException(status_code=403, detail="Not yours")
    return {"id": str(id), "owner": row[0], "title": row[1]}


def original_factory(path: Path) -> FastAPI:
    app = FastAPI()

    @app.get("/things/{id}")
    def get_thing(id: uuid.UUID, authorization: str | None = Header(None)) -> Any:
        return read_thing(id, path, authorization)

    @app.post("/things/")
    def create_thing(body: NewThing, authorization: str | None = Header(None)) -> Any:
        user = _caller(authorization)
        new = uuid.uuid4()
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO thing VALUES (?, ?, ?)", (new.hex, user, body.title))
        conn.commit()
        conn.close()
        return {"id": str(new), "owner": user, "title": body.title}

    return app


def lattice_factory(*, spelling: str = "hex32", forbid_as_404: bool = False):
    """A stand-in import: same routes with Lattice's 422 wording, optionally a different
    stored uuid spelling, or 404 instead of 403."""

    def factory(path: Path) -> FastAPI:
        app = FastAPI()

        @app.get("/things/{id}")
        def get_thing(id: str, authorization: str | None = Header(None)) -> Any:
            try:
                u = uuid.UUID(id)
            except ValueError:
                body = {"detail": [{"loc": ["path", "id"], "msg": "not a uuid"}]}
                return JSONResponse(status_code=422, content=body)
            try:
                return read_thing(u, path, authorization)
            except HTTPException as e:
                if forbid_as_404 and e.status_code == 403:
                    raise HTTPException(status_code=404, detail="Thing not found") from None
                raise

        @app.post("/things/")
        def create_thing(body: NewThing, authorization: str | None = Header(None)) -> Any:
            user = _caller(authorization)
            new = uuid.uuid4()
            stored = new.hex if spelling == "hex32" else str(new)
            conn = sqlite3.connect(path)
            conn.execute("INSERT INTO thing VALUES (?, ?, ?)", (stored, user, body.title))
            conn.commit()
            conn.close()
            return {"id": str(new), "owner": user, "title": body.title}

        return app

    return factory


def login(client: TestClient, handle: str) -> str:
    return handle


CASES = [
    Case("read_own", "GET", f"/things/{T1}", user="alice", tables=("thing",), route="get"),
    Case("read_other", "GET", f"/things/{T2}", user="alice", route="get"),
    Case("read_missing", "GET", f"/things/{MISSING}", user="alice", route="get"),
    Case("read_bad_id", "GET", "/things/not-a-uuid", user="alice", route="get"),
    Case("read_no_token", "GET", f"/things/{T1}", route="get"),
    Case("read_empty_db", "GET", f"/things/{T1}", user="alice", db="empty", route="get"),
    Case("create", "POST", "/things/", user="bob", json={"title": "x"}, tables=("thing",)),
]
VE = ValidationEnvelope()


def run(lattice=None, cases=CASES, rules=(VE,), deviations=None, branches=None):
    return verify(
        original_factory,
        lattice or lattice_factory(),
        make_db,
        cases,
        rules,
        deviations or {},
        login=login,
        branches=branches if branches is not None else {"get": [read_thing, _caller]},
    )


# ---- raise sites --------------------------------------------------------------------------


def test_raise_sites_lists_each_http_exception_with_its_status():
    sites = raise_sites(read_thing)
    assert [(s.function, s.status, s.detail) for s in sites] == [
        ("read_thing", 404, "Thing not found"),
        ("read_thing", 403, "Not yours"),
    ]
    src = Path(__file__).read_text().splitlines()
    for s in sites:
        assert src[s.line - 1].strip().startswith("raise HTTPException(")


# ---- outcomes -----------------------------------------------------------------------------


def test_an_identical_import_is_equal_on_every_case():
    result = run(original_factory)
    kinds = {n: o.kind for n, o in result.outcomes.items()}
    assert set(kinds.values()) == {"equal"}, kinds
    assert result.unneeded_rules == (VE.name,)  # no case needed the rule
    assert not result.ok  # an unneeded rule is a failure of the declaration
    assert result.tally() == (7, 0, 0, 0)


def test_a_rule_is_applied_and_recorded_as_needed():
    result = run(lattice_factory())
    assert result.outcomes["read_bad_id"].kind == "equal_under_rule"
    assert result.outcomes["read_bad_id"].rules == (VE.name,)
    assert result.unneeded_rules == ()
    assert result.ok, result.render()


def test_without_the_rule_the_wording_is_a_mismatch_with_a_diff():
    result = run(lattice_factory(), rules=())
    out = result.outcomes["read_bad_id"]
    assert out.kind == "mismatch"
    assert out.diff is not None and out.diff.body is not None and out.diff.status is None
    assert not result.ok


def test_a_declared_deviation_is_asserted_exactly():
    dev = Deviation("hide-existence", "policy", "404 for another user's row", 404,
                    {"detail": "Thing not found"})  # fmt: skip
    result = run(lattice_factory(forbid_as_404=True),
                 deviations={"read_other": dev})  # fmt: skip
    assert result.outcomes["read_other"].kind == "deviation"
    assert result.outcomes["read_other"].deviation == "hide-existence"
    assert result.ok


def test_a_deviation_that_does_not_happen_is_a_mismatch():
    dev = Deviation("hide-existence", "policy", "r", 404, {"detail": "Thing not found"})
    result = run(lattice_factory(), deviations={"read_other": dev})
    out = result.outcomes["read_other"]
    assert out.kind == "mismatch" and "agrees" in out.reason


def test_a_deviation_with_another_answer_is_a_mismatch():
    dev = Deviation("hide-existence", "policy", "r", 404, {"detail": "Gone"})
    result = run(lattice_factory(forbid_as_404=True),
                 deviations={"read_other": dev})  # fmt: skip
    out = result.outcomes["read_other"]
    assert out.kind == "mismatch" and "declared" in out.reason


def test_an_accept_original_deviation_accepts_either_answer():
    dev = Deviation("hide-existence", "policy", "either is fine", 404,
                    {"detail": "Thing not found"}, accept_original=True)  # fmt: skip
    agrees = run(lattice_factory(), deviations={"read_other": dev})
    assert agrees.outcomes["read_other"].kind == "equal"
    assert agrees.outcomes["read_other"].deviation is None
    assert agrees.ok
    differs = run(lattice_factory(forbid_as_404=True), deviations={"read_other": dev})
    assert differs.outcomes["read_other"].kind == "deviation"
    assert differs.outcomes["read_other"].deviation == "hide-existence"
    other = Deviation("hide-existence", "policy", "r", 404, {"detail": "Gone"},
                      accept_original=True)  # fmt: skip
    wrong = run(lattice_factory(forbid_as_404=True), deviations={"read_other": other})
    assert wrong.outcomes["read_other"].kind == "mismatch"


def test_a_deviation_naming_no_case_is_reported():
    dev = Deviation("x", "policy", "r", 404, None)
    result = run(lattice_factory(), deviations={"no_such_case": dev})
    assert result.unknown_deviations == ("no_such_case",)
    assert not result.ok


def test_a_write_in_another_spelling_is_a_db_mismatch():
    result = run(lattice_factory(spelling="dashed"))
    out = result.outcomes["create"]
    assert out.kind == "mismatch"
    assert out.diff.status is None and out.diff.body is None
    assert set(out.diff.db) == {"thing"}
    # the placeholders keep spelling: <uuid:1:hex32> vs <uuid:1:dashed>
    orig_rows, lat_rows = out.diff.db["thing"]
    assert orig_rows[-1]["id"] == "<uuid:1:hex32>" and lat_rows[-1]["id"] == "<uuid:1:dashed>"


# ---- branch coverage ----------------------------------------------------------------------


def test_every_raise_site_hit_is_attributed_to_its_cases():
    result = run(lattice_factory())
    hits = {(s.function, s.status): names for s, names in result.hits.items()}
    assert hits[("read_thing", 404)] == ("read_missing", "read_empty_db")
    assert hits[("read_thing", 403)] == ("read_other",)
    assert hits[("_caller", 401)] == ("read_no_token",)
    assert result.unverified == ()


def test_a_site_no_case_hits_is_an_unverified_branch():
    cases = [c for c in CASES if c.name != "read_other"]
    result = run(lattice_factory(), cases=cases)
    assert [(route, s.status) for route, s in result.unverified] == [("get", 403)]
    assert not result.ok


def test_a_hit_from_another_routes_case_does_not_count():
    cases = [*CASES[:1], Case("x", "GET", f"/things/{T2}", user="alice", route="other")]
    result = run(lattice_factory(), cases=cases,
                 branches={"get": [read_thing]})  # fmt: skip
    assert {s.status for _, s in result.unverified} == {404, 403}


# ---- representation rules as data ---------------------------------------------------------


def test_error_envelope_compares_the_message_only():
    rule = ErrorEnvelope(original=("detail",), lattice=("error", "message"))
    assert rule.canon({"detail": "Nope"}, 404, "original") == rule.canon(
        {"error": {"message": "Nope", "code": "not_found"}}, 404, "lattice"
    )
    assert rule.canon({"detail": "Nope"}, 200, "original") == {"detail": "Nope"}


def test_datetime_wire_drops_the_spelling_only():
    rule = DatetimeWire()
    a = rule.canon({"at": "<datetime:1:TZ>", "s": "2026-01-01T09:00:00Z"}, 200, "lattice")
    b = rule.canon({"at": "<datetime:1:T>", "s": "2026-01-01T09:00:00"}, 200, "original")
    assert a == b
    c = rule.canon({"at": "<datetime:2:T>"}, 200, "original")
    assert c != rule.canon({"at": "<datetime:1:T>"}, 200, "original")


def test_a_crash_in_the_original_is_recorded_as_a_500():
    def crashing(path: Path) -> FastAPI:
        app = original_factory(path)

        @app.get("/boom")
        def boom() -> None:
            raise RuntimeError("boom")

        return app

    case = Case("boom", "GET", "/boom")
    result = verify(crashing, crashing, make_db, [case], (), {})
    assert result.original["boom"].status == 500
    assert result.outcomes["boom"].kind == "equal"


def test_the_normaliser_keeps_an_impossible_datetime_shaped_string(tmp_path):
    db = make_db("seed", tmp_path / "seed.db")
    n = Normalizer(db).fresh()
    body = {"detail": [{"input": "2026-02-30T00:00:00", "loc": ["query", "since"]}]}
    assert n.value(body) == body
    assert n.value("2026-02-01T00:00:00Z") == "<datetime:1:TZ>"
