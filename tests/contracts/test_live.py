"""The live contract check (lattice/contracts/live.py): a synthetic provider (a tiny FastAPI app on
SQLite, its spec and a manifest, all under tmp_path) for the unit cases, and the real pair
(job-scheduler at ef17cf8 cloned into tmp_path, the prereg's L-series) for the integration ones.

The provider app logs every request it gets (``$LIVE_REQUEST_LOG``) so that a test can see what
the replay sent, and in which order."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as wsmod
from lattice.contracts import check as ck
from lattice.contracts import export as ex
from lattice.contracts import live
from lattice.contracts.live import LiveError

from .helpers import copy_data, imports_text, st, write_spec

APP = """\
import json
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent


def _behavior():
    f = HERE / "behavior.json"
    return json.loads(f.read_text()) if f.is_file() else {}


def build_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        "create table runs (id integer primary key, job integer, status text);"
        "create table notes (id integer primary key, user_id integer, text text);"
    )
    con.commit()
    con.close()
    return Path(path)


def login(client, handle):
    if handle == "ghost":
        raise RuntimeError("no such login")
    return "tok-" + handle


def err(code, detail):
    return JSONResponse({"detail": detail}, status_code=code)


def make_app(db_path):
    b = _behavior()
    if b.get("crash"):
        raise RuntimeError("the app does not start")
    app = FastAPI()

    def sql(query, *args):
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(query, args).fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    @app.middleware("http")
    async def log(request, call_next):
        target = os.environ.get("LIVE_REQUEST_LOG")
        if target:
            with open(target, "a") as f:
                f.write(request.method + " " + request.url.path + "\\n")
        return await call_next(request)

    def who(request):
        h = request.headers.get("authorization", "")
        return h[len("Bearer tok-"):] if h.startswith("Bearer tok-") else None

    @app.post("/auth/login")
    async def do_login(request: Request):
        body = await request.json()
        if body.get("password") == "no":
            return err(401, "bad credentials")
        fields = b.get("login_fields", ["access_token", "token_type"])
        return JSONResponse({f: "x" for f in fields}, status_code=b.get("login_status", 200))

    @app.get("/jobs")
    def jobs(request: Request):
        if who(request) is None:
            return err(401, "no token")
        if b.get("jobs_error"):
            return err(b["jobs_error"], "boom")
        if b.get("jobs_empty"):
            return []
        return [{f: 1 for f in b.get("job_fields", ["id", "name"])}]

    @app.get("/jobs/{job_id}")
    def one_job(job_id: int, request: Request):
        if who(request) is None:
            return err(401, "no token")
        if b.get("job_forbidden"):
            return err(403, "denied")
        if job_id != 7:
            return err(404, "no such job")
        return {"id": 7, "name": "nightly"}

    @app.post("/jobs/{job_id}/runs")
    def start_run(job_id: int, request: Request):
        user = who(request)
        if user is None:
            return err(401, "no token")
        if user == "bob":
            return err(403, "denied")
        if sql("select id from runs where job = ?", job_id):
            return err(409, "a run exists")
        sql("insert into runs (job, status) values (?, 'queued')", job_id)
        rid = sql("select id from runs where job = ?", job_id)[0][0]
        return JSONResponse({"id": rid, "status": "queued"}, status_code=b.get("runs_status", 201))

    @app.get("/jobs/{job_id}/runs")
    def list_runs(job_id: int, request: Request):
        if who(request) is None:
            return err(401, "no token")
        rows = sql("select id, status from runs where job = ?", job_id)
        return [{"id": i, "status": s} for i, s in rows]

    @app.get("/admin")
    def admin(request: Request):
        user = who(request)
        if user is None:
            return err(401, "no token")
        if user != b.get("admin_for", "eve"):
            return err(403, "denied")
        return {"ok": True}

    @app.get("/notes/{note_id}")
    def note(note_id: int, request: Request):
        if who(request) is None:
            return err(401, "no token")
        rows = sql("select id, text from notes where id = ?", note_id)
        if not rows:
            return err(404, "no such note")
        return {"id": rows[0][0], "text": rows[0][1]}

    return app
"""

DATA: list[tuple[str, list[dict]]] = [
    ("login", [
        st("POST", "/auth/login", None, 200, json={"email": "a", "password": "pw"},
           expect_body={"access_token": "x", "token_type": "bearer"}),
        st("POST", "/auth/login", None, 401, json={"email": "a", "password": "no"}),
    ]),
    ("jobs", [
        st("GET", "/jobs", None, 401),
        st("GET", "/jobs", "ann", 200, params={"limit": 5},
           expect_body=[{"id": 1, "name": "nightly"}]),
        st("GET", "/jobs/7", "ann", 200, expect_body={"id": 7, "name": "nightly"}),
        st("GET", "/jobs/8", "ann", 404),
    ]),
    ("runs", [
        st("POST", "/jobs/7/runs", "ann", 201, expect_body={"id": 1, "status": "queued"}),
        st("GET", "/jobs/7/runs", "ann", 200, expect_body=[{"id": 1, "status": "queued"}]),
        st("POST", "/jobs/7/runs", "bob", 403),
        st("POST", "/jobs/7/runs", "ann", 409),
    ]),
    ("admin", [
        st("GET", "/admin", "ann", 403),
        st("GET", "/admin", "eve", 200, expect_body={"ok": True}),
    ]),
]  # fmt: skip

NOTES_SPEC = """\
from lattice.profile import ApiProfile, Profile
from lattice.spec import Scenario, Spec, Step
from lattice.typesys import Scalar, Schema

NOTE = Schema.from_pairs(
    [("id", Scalar.INT), ("user_id", Scalar.INT), ("text", Scalar.STR)],
    keys=[("id",)],
    owner="user_id",
)
GIVEN = {GIVEN!r}
SPEC = Spec(
    Profile(
        schemas={{}},
        api=ApiProfile(tables={{"Note": NOTE}}, table_names={{"Note": "notes"}},
                       roles=frozenset({{"Public"}}), error_body="fastapi"),
    ),
    users={{"ann": ("ann@x.io", "pw")}},
    scenarios=(
        Scenario("read_a_note", given=GIVEN, steps=(
            Step("GET", "/notes/10", "ann", expect_status=200,
                 expect_body={{"id": 10, "text": "milk"}}),
        )),
    ),
)
"""

# every claim a consumer of DATA could make, all held by the provider's spec (the app agrees)
CLEAN_IMPORTS: list[dict[str, Any]] = [
    {"endpoint": "POST /auth/login", "status": [200], "auth": "public",
     "request_fields": ["email", "password"], "response_fields": ["access_token"]},
    {"endpoint": "GET /jobs", "status": [200], "auth": "token", "as_user": "ann",
     "response_fields": ["id", "name"]},
    {"endpoint": "GET /jobs/{id}", "status": [200, 404], "auth": "token",
     "response_fields": ["name"]},
    {"endpoint": "POST /jobs/{id}/runs", "status": [201], "auth": "token", "as_user": "ann",
     "response_fields": ["id", "status"]},
    {"endpoint": "GET /jobs/{id}/runs", "status": [200], "auth": "token", "as_user": "ann",
     "response_fields": ["id", "status"]},
    {"endpoint": "GET /admin", "status": [200], "auth": "token", "as_user": "eve",
     "response_fields": ["ok"]},
]  # fmt: skip


class Live:
    """A workspace with a synthetic provider ``prov`` (manifest keys app/db/login/env) and a
    consumer ``cons`` that links to it with ``contract = "live"``."""

    def __init__(self, root: Path, *, data: list[tuple[str, list[dict]]] | None = None,
                 spec_text: str | None = None, imports: list[dict] | None = None,
                 behavior: dict | None = None, live_url: str | None = None,
                 project_keys: str | None = None, studio: str | None = None) -> None:  # fmt: skip
        self.root = root
        self.prov = root / "prov"
        self.cons = root / "cons"
        if spec_text is not None:
            self.prov.mkdir(parents=True)
            (self.prov / "spec.py").write_text(spec_text)
        else:
            write_spec(self.prov, DATA if data is None else data)
        (self.prov / "prov_app.py").write_text(APP)
        if behavior is not None:
            self.behave(behavior)
        if studio is not None:
            (self.prov / "studio.toml").write_text(studio)
        self.cons.mkdir()
        wanted = CLEAN_IMPORTS if imports is None else imports
        (self.cons / ck.IMPORTS_FILE).write_text(imports_text(*wanted))
        keys = project_keys
        if keys is None:
            keys = ('app = "prov_app:make_app"\ndb = "prov_app:build_db"\n'
                    'login = "prov_app:login"\n')  # fmt: skip
        url = f'live_url = "{live_url}"\n' if live_url else ""
        (root / wsmod.MANIFEST).write_text(
            '[workspace]\nname = "w"\n\n'
            '[[project]]\nname = "prov"\npath = "prov"\nrole = "provider"\n'
            f'spec = "spec:SPEC"\n{keys}'
            'env = { LIVE_REQUEST_LOG = "$PROJECT/requests.log" }\n\n'
            '[[project]]\nname = "cons"\npath = "cons"\nrole = "consumer"\n\n'
            f'[[link]]\nfrom = "cons"\nto = "prov"\nkind = "uses"\ncontract = "live"\n{url}'
        )

    def behave(self, behavior: dict) -> None:
        (self.prov / "behavior.json").write_text(json.dumps(behavior))

    @property
    def log(self) -> list[str]:
        f = self.prov / "requests.log"
        return f.read_text().splitlines() if f.is_file() else []

    def workspace(self) -> wsmod.Workspace:
        return wsmod.load(self.root / wsmod.MANIFEST)

    def export(self) -> dict:
        return ex.compute_export(self.prov, "spec:SPEC")["export"]

    def run(self, export: dict | None = None, **kw: Any) -> dict:
        ws = self.workspace()
        imports = ck.parse_imports(self.cons / ck.IMPORTS_FILE)
        return live.run_live(ws, ws.links[0], imports, export or self.export(), **kw)


def keys(res: dict) -> list[tuple[str, str, str]]:
    return [(f["kind"], f["endpoint"], f["item"]) for f in res["findings"]]


def only(imports: list[dict], endpoint: str) -> list[dict]:
    return [i for i in imports if i["endpoint"] == endpoint]


# ---- unit: the synthetic provider ------------------------------------------------------------


def test_clean_provider_has_no_findings(tmp_path: Path) -> None:
    w = Live(tmp_path)
    res = w.run()
    assert res["status"] == "ok" and res["findings"] == []
    assert res["replayed"] == 6 and res["not_replayed"] == []
    assert res["not_checked"] == ["request_fields"]
    assert res["fields_unobservable"] == []
    assert [(r["endpoint"], r["auth"], r["answered"]) for r in res["replays"]] == [
        ("GET /admin", "as_user:eve", 200),
        ("GET /jobs", "as_user:ann", 200),
        ("GET /jobs/{}", "token", 200),
        ("GET /jobs/{}/runs", "as_user:ann", 200),
        ("POST /auth/login", "public", 200),
        ("POST /jobs/{}/runs", "as_user:ann", 201),
    ]
    assert not (tmp_path / "prov" / "__pycache__").exists()  # the provider dir stays clean


def test_the_replay_sends_the_scenarios_earlier_steps_in_order(tmp_path: Path) -> None:
    w = Live(tmp_path, imports=only(CLEAN_IMPORTS, "GET /jobs/{id}/runs"))
    res = w.run()
    # the target is runs/step 2: the POST before it ran on this fresh seed, so the list has rows
    assert w.log == ["POST /jobs/7/runs", "GET /jobs/7/runs"]
    assert res["replays"][0]["scenario"] == "runs" and res["replays"][0]["step"] == 2
    assert res["findings"] == [] and res["fields_unobservable"] == []


def test_each_replay_has_a_fresh_seed(tmp_path: Path) -> None:
    for order in (["GET /jobs/{id}/runs", "POST /jobs/{id}/runs"],
                  ["POST /jobs/{id}/runs", "GET /jobs/{id}/runs"]):  # fmt: skip
        sub = tmp_path / str(len(order[0]))  # one workspace per order
        imps = [only(CLEAN_IMPORTS, e)[0] for e in order]
        w = Live(sub, imports=imps)
        res = w.run()
        # a shared database would answer the POST with 409 (a run exists) after the GET's replay
        assert res["findings"] == [] and res["replayed"] == 2
        assert sorted(r["answered"] for r in res["replays"]) == [200, 201]


def test_status_drift_is_status_not_observed(tmp_path: Path) -> None:
    w = Live(tmp_path, behavior={"runs_status": 200})
    res = w.run()
    assert keys(res) == [("status_not_observed", "POST /jobs/{}/runs", "201")]
    f = res["findings"][0]
    assert f["class"] == "BROKEN" and f["provider"] == "prov"
    assert f["detail"] == "the live answer was 200; the import handles 201"


def test_a_refusal_where_the_import_wants_a_2xx_is_auth_changed(tmp_path: Path) -> None:
    w = Live(tmp_path, behavior={"admin_for": "nobody", "job_forbidden": True})
    res = w.run()
    assert keys(res) == [
        ("auth_changed", "GET /admin", "as_user:eve"),
        ("auth_changed", "GET /jobs/{}", "token"),
    ]


def test_a_public_endpoint_that_wants_a_token_now_is_auth_changed_public(tmp_path: Path) -> None:
    # the login answers 401 to the spec's own good credentials: public -> refused
    w = Live(tmp_path, behavior={"login_status": 401})
    res = w.run()
    assert keys(res) == [("auth_changed", "POST /auth/login", "public")]


def test_any_other_status_is_status_not_observed(tmp_path: Path) -> None:
    w = Live(tmp_path, behavior={"jobs_error": 500})
    res = w.run()
    assert keys(res) == [("status_not_observed", "GET /jobs", "200")]
    assert res["findings"][0]["detail"].startswith("the live answer was 500")


def test_an_answer_in_the_imports_statuses_is_not_a_finding(tmp_path: Path) -> None:
    # eve's 2xx step now answers 403, which this import handles
    imps = [{"endpoint": "GET /admin", "status": [200, 403], "auth": "token", "as_user": "eve"}]
    w = Live(tmp_path, imports=imps, behavior={"admin_for": "nobody"})
    res = w.run()
    assert res["findings"] == [] and res["replays"][0]["answered"] == 403


def test_a_refusal_the_import_does_not_expect_a_success_for_is_a_status_finding(
    tmp_path: Path,
) -> None:
    imps = [{"endpoint": "GET /admin", "status": [404], "auth": "token", "as_user": "eve"}]
    w = Live(tmp_path, imports=imps, behavior={"admin_for": "nobody"})
    assert keys(w.run()) == [("status_not_observed", "GET /admin", "404")]


def test_a_missing_relied_response_field_is_response_field_missing(tmp_path: Path) -> None:
    w = Live(tmp_path, behavior={"login_fields": ["token_type"], "job_fields": ["id"]})
    res = w.run()
    assert keys(res) == [
        ("response_field_missing", "GET /jobs", "name"),
        ("response_field_missing", "POST /auth/login", "access_token"),
    ]
    assert res["verified_live"] == []
    assert "fields ['id']" in res["findings"][0]["detail"]


def test_an_empty_list_answer_is_unobservable_never_a_finding(tmp_path: Path) -> None:
    w = Live(tmp_path, behavior={"jobs_empty": True},
             imports=only(CLEAN_IMPORTS, "GET /jobs"))  # fmt: skip
    res = w.run()
    assert res["findings"] == [] and res["replayed"] == 1
    assert res["fields_unobservable"] == [
        {
            "endpoint": "GET /jobs",
            "fields": ["id", "name"],
            "reason": "the live answer is an empty list",
        }
    ]


def test_a_gap_the_live_answer_satisfies_is_verified_live(tmp_path: Path) -> None:
    imps = [dict(CLEAN_IMPORTS[0], response_fields=["access_token", "refresh_token"])]
    w = Live(tmp_path, imports=imps, behavior={"login_fields": ["access_token", "refresh_token"]})
    res = w.run()
    assert res["findings"] == []
    # the spec's expect_body shows access_token: only the unshown field is a static gap
    assert res["verified_live"] == [
        {
            "provider": "prov",
            "endpoint": "POST /auth/login",
            "kind": "response_field_unobserved",
            "item": "refresh_token",
        }
    ]


def test_the_same_gap_the_live_answer_lacks_is_broken_not_verified(tmp_path: Path) -> None:
    imps = [dict(CLEAN_IMPORTS[0], response_fields=["access_token", "refresh_token"])]
    res = Live(tmp_path, imports=imps).run()
    assert keys(res) == [("response_field_missing", "POST /auth/login", "refresh_token")]
    assert res["verified_live"] == []


def test_a_status_the_spec_never_showed_is_verified_by_the_answer(tmp_path: Path) -> None:
    # the spec's 2xx step expects 201; the import handles 200, which the app answers
    imps = [dict(only(CLEAN_IMPORTS, "POST /jobs/{id}/runs")[0], status=[200])]
    res = Live(tmp_path, imports=imps, behavior={"runs_status": 200}).run()
    assert res["findings"] == []
    assert res["verified_live"] == [
        {
            "provider": "prov",
            "endpoint": "POST /jobs/{}/runs",
            "kind": "status_unobserved",
            "item": "200",
        }
    ]


def test_request_fields_are_not_checked(tmp_path: Path) -> None:
    imps = [dict(CLEAN_IMPORTS[0], request_fields=["email", "password", "otp"])]
    res = Live(tmp_path, imports=imps).run()
    assert res["findings"] == [] and res["verified_live"] == []
    assert res["not_checked"] == ["request_fields"]


def test_imports_without_a_2xx_step_are_not_replayed(tmp_path: Path) -> None:
    imps = [
        {"endpoint": "GET /admin", "status": [200], "auth": "token", "as_user": "ann"},
        {"endpoint": "GET /jobs", "status": [200], "auth": "public"},
        {"endpoint": "GET /nothing", "status": [200], "auth": "token"},
        {"endpoint": "GET /jobs/{id}", "status": [200], "auth": "token"},
    ]
    w = Live(tmp_path, imports=imps)
    res = w.run()
    assert res["replayed"] == 1 and res["findings"] == []
    assert res["not_replayed"] == [
        {"endpoint": "GET /admin", "reason": "no 2xx step for as_user ann"},
        {"endpoint": "GET /jobs", "reason": "no 2xx step for public"},
        {
            "endpoint": "GET /nothing",
            "reason": "the endpoint is not in the provider's current spec",
        },
    ]
    # only the one replay's own steps (its scenario's two GET /jobs, then the target): nothing was
    # sent for the three imports that were not replayed
    assert w.log == ["GET /jobs", "GET /jobs", "GET /jobs/7"]


def test_the_first_2xx_step_of_the_callers_is_the_target(tmp_path: Path) -> None:
    data = [
        ("first", [st("GET", "/admin", "ann", 403), st("GET", "/admin", "eve", 200),
                   st("GET", "/admin", "eve", 200)]),
        ("second", [st("GET", "/admin", "eve", 200)]),
    ]  # fmt: skip
    imps = [{"endpoint": "GET /admin", "status": [200], "auth": "token", "as_user": "eve"}]
    w = Live(tmp_path, data=data, imports=imps)
    res = w.run()
    assert (res["replays"][0]["scenario"], res["replays"][0]["step"]) == ("first", 2)
    assert w.log == ["GET /admin", "GET /admin"]  # ann's 403 step, then the target


def test_a_token_import_without_as_user_takes_any_handle(tmp_path: Path) -> None:
    data = [("s", [st("GET", "/admin", None, 401), st("GET", "/admin", "eve", 200)])]
    imps = [{"endpoint": "GET /admin", "status": [200], "auth": "token"}]
    res = Live(tmp_path, data=data, imports=imps).run()
    assert res["replays"] == [
        {"endpoint": "GET /admin", "auth": "token", "scenario": "s", "step": 2, "answered": 200}
    ]  # the anonymous 401 step is not it


def test_earlier_answers_are_not_asserted_and_awaits_are_skipped(tmp_path: Path) -> None:
    data = [("s", [
        st("GET", "/admin", "ann", 200),  # the app answers 403: ignored
        {"service": "worker", "expect_db": {"runs": {"count": 1}}},
        st("GET", "/jobs", "ann", 200, expect_body=[{"id": 1}]),
    ])]  # fmt: skip
    imps = [{"endpoint": "GET /jobs", "status": [200], "auth": "token", "as_user": "ann",
             "response_fields": ["id"]}]  # fmt: skip
    res = Live(tmp_path, data=data, imports=imps).run()
    assert res["findings"] == []
    assert res["replays"][0]["awaits_skipped"] == 1 and res["replays"][0]["step"] == 3


def test_a_login_that_fails_for_the_target_user_is_not_replayed(tmp_path: Path) -> None:
    data = [("s", [st("GET", "/admin", "ghost", 200)])]
    imps = [{"endpoint": "GET /admin", "status": [200], "auth": "token", "as_user": "ghost"}]
    res = Live(tmp_path, data=data, imports=imps).run()
    assert res["replayed"] == 0 and res["findings"] == []
    assert res["not_replayed"][0]["reason"].startswith("the request as ghost could not be sent")


def test_given_rows_are_inserted_before_the_replay(tmp_path: Path) -> None:
    given = {"notes": [{"id": 10, "user_id": 1, "text": "milk"}]}
    imps = [{"endpoint": "GET /notes/{id}", "status": [200], "auth": "token", "as_user": "ann",
             "response_fields": ["id", "text"]}]  # fmt: skip
    w = Live(tmp_path, spec_text=NOTES_SPEC.format(GIVEN=given), imports=imps)
    res = w.run()
    assert res["findings"] == [] and res["replays"][0]["answered"] == 200


def test_given_rows_the_app_refuses_are_not_replayed(tmp_path: Path) -> None:
    given = {"ghosts": [{"id": 1}]}  # a table the spec's profile does not know
    imps = [{"endpoint": "GET /notes/{id}", "status": [200], "auth": "token", "as_user": "ann"}]
    res = Live(tmp_path, spec_text=NOTES_SPEC.format(GIVEN=given), imports=imps).run()
    assert res["replayed"] == 0
    assert res["not_replayed"][0]["reason"].startswith("the scenario's given rows were refused")


def test_output_is_deterministic(tmp_path: Path) -> None:
    w = Live(tmp_path, behavior={"runs_status": 200, "job_fields": ["id"]})
    runs = [json.dumps(w.run(), sort_keys=True) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert "elapsed" not in runs[0] and "time" not in runs[0].replace("ontime", "")


def test_nothing_is_left_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("TMPDIR", str(scratch))
    w = Live(tmp_path / "w", behavior={"runs_status": 200})
    assert keys(w.run()) == [("status_not_observed", "POST /jobs/{}/runs", "201")]
    assert list(scratch.iterdir()) == []  # no lattice-live-* and no lattice-sec-* directory


# ---- unit: how the provider's settings are found ---------------------------------------------


def test_studio_toml_wins_over_the_manifest(tmp_path: Path) -> None:
    studio = ('[app]\napp = "prov_app:make_app"\ndb = "prov_app:build_db"\n'
              'login = "prov_app:login"\n')  # fmt: skip
    keys_wrong = 'app = "nowhere:make_app"\ndb = "nowhere:build_db"\n'
    w = Live(tmp_path, studio=studio, project_keys=keys_wrong,
             imports=only(CLEAN_IMPORTS, "GET /admin"))  # fmt: skip
    res = w.run()
    assert res["replayed"] == 1 and res["findings"] == []


def test_manifest_keys_are_used_without_a_studio_toml(tmp_path: Path) -> None:
    res = Live(tmp_path, imports=only(CLEAN_IMPORTS, "GET /admin")).run()
    assert res["replayed"] == 1


def test_a_provider_without_an_app_is_no_harness(tmp_path: Path) -> None:
    w = Live(tmp_path, project_keys="")
    with pytest.raises(LiveError) as e:
        w.run()
    assert e.value.code == "no_harness" and "app" in e.value.message


def test_an_app_that_does_not_start_is_provider_start_failed(tmp_path: Path) -> None:
    w = Live(tmp_path, behavior={"crash": True})
    with pytest.raises(LiveError) as e:
        w.run()
    assert e.value.code == "provider_start_failed" and "does not start" in e.value.message


def test_a_spec_that_does_not_load_is_spec_failed(tmp_path: Path) -> None:
    w = Live(tmp_path, spec_text="raise RuntimeError('broken spec')\n")
    with pytest.raises(LiveError) as e:
        w.run(export={"endpoints": {}})  # the static export cannot be computed either
    assert e.value.code == "spec_failed" and "broken spec" in e.value.message


def test_env_of_the_manifest_and_clone_env(tmp_path: Path) -> None:
    w = Live(tmp_path, imports=only(CLEAN_IMPORTS, "GET /admin"))
    w.run()
    assert w.log == ["GET /admin", "GET /admin"]  # the manifest's $PROJECT/requests.log
    other = tmp_path / "other.log"
    (tmp_path / "prov" / "requests.log").unlink()
    w.run(clone_env={"LIVE_REQUEST_LOG": str(other)})  # clone_env wins
    assert other.read_text().splitlines() == ["GET /admin", "GET /admin"] and w.log == []


# ---- unit: live_url is never a target (L5, on the synthetic provider) ------------------------


@pytest.mark.parametrize(
    "url", ["http://10.0.0.1:8000", "http://example.com", "http://0.0.0.0:8000", "not a url"]
)
def test_a_non_loopback_live_url_is_refused_before_start(
    tmp_path: Path, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = Live(tmp_path, live_url=url)
    monkeypatch.setattr(live, "_spawn", lambda *a, **k: pytest.fail("nothing may be started"))
    with pytest.raises(LiveError) as e:
        w.run()
    assert e.value.code == "not_loopback"
    assert w.log == []


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8000", "http://localhost:8000/", "http://[::1]:9"]
)
def test_a_loopback_live_url_is_unsupported(
    tmp_path: Path, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = Live(tmp_path, live_url=url)
    monkeypatch.setattr(live, "_spawn", lambda *a, **k: pytest.fail("nothing may be started"))
    with pytest.raises(LiveError) as e:
        w.run()
    assert e.value.code == "live_url_unsupported" and "never targets a URL" in e.value.message
    assert w.log == []


# ---- unit: through the whole check -----------------------------------------------------------


def test_run_check_reports_a_drifted_app_while_static_stays_clean(tmp_path: Path) -> None:
    w = Live(tmp_path)
    ws = w.workspace()
    assert ck.run_pin(ws, "cons").exit_code == 0
    w.behave({"runs_status": 200})  # the app drifts, the spec does not
    rep = ck.run_check(ws, "cons")
    link = rep.links[0]
    assert rep.exit_code == 1 and link["status"] == "broken"
    assert link["findings"] == [] and link["gaps"] == []  # static is blind to it
    assert [(f["kind"], f["item"]) for f in link["live"]["findings"]] == [
        ("status_not_observed", "201")
    ]
    static = ck.run_check(ws, "cons", static_only=True)
    assert static.exit_code == 0 and static.links[0]["live"] == {
        "status": "not_run",
        "reason": "static-only",
    }


def test_run_check_with_a_live_url_is_exit_2(tmp_path: Path) -> None:
    w = Live(tmp_path, live_url="http://example.com")
    ws = w.workspace()
    assert ck.run_pin(ws, "cons").exit_code == 0
    rep = ck.run_check(ws, "cons")
    assert rep.exit_code == 2
    assert rep.links[0]["status"] == "error" and rep.links[0]["error"]["code"] == "not_loopback"
    assert w.log == []


# ---- unit: the pure parts --------------------------------------------------------------------


def _imp(**kw: Any) -> ck.Import:
    base = {"provider": "p", "endpoint": "GET /x", "status": (200,), "auth": "token"}
    return ck.Import(**{**base, **kw})


def test_judge_orders_and_items() -> None:
    imp = _imp(status=(200, 201), response_fields=("a", "b"))
    ok = {"answered": 201, "shape": "object", "fields": ["a", "b"]}
    assert live.judge(imp, ok) == []
    odd = live.judge(imp, {"answered": 202, "shape": "object", "fields": ["a"]})
    assert [(f["kind"], f["item"]) for f in odd] == [
        ("status_not_observed", "200"),
        ("response_field_missing", "b"),
    ]  # the import's lowest 2xx status is the item
    denied = live.judge(_imp(as_user="eve"), {"answered": 403, "shape": "object", "fields": []})
    assert [(f["kind"], f["item"]) for f in denied] == [("auth_changed", "as_user:eve")]
    non2xx = {"answered": 500, "shape": "object", "fields": []}
    assert [f["kind"] for f in live.judge(_imp(response_fields=("a",)), non2xx)] == [
        "status_not_observed"
    ]  # no field check on a non-2xx answer


def test_fields_of_shapes() -> None:
    assert live._fields_of({"b": 1, "a": 2}) == ("object", ["a", "b"])
    assert live._fields_of([{"x": 1}, {"y": 2}, 3]) == ("list", ["x", "y"])
    assert live._fields_of([]) == ("empty_list", [])
    assert live._fields_of("text") == ("other", [])
    assert live._fields_of(None) == ("other", [])


def test_select_step_reasons() -> None:
    from .helpers import make_spec

    spec = make_spec(copy_data(DATA))
    got = live.select_step(spec, "GET /admin", "token", "eve")
    assert got[0].name == "admin" and got[1] == 1  # type: ignore[index]
    assert live.select_step(spec, "GET /admin", "token", "ann") == "no 2xx step for as_user ann"
    assert live.select_step(spec, "GET /admin", "public", None) == "no 2xx step for public"
    assert live.select_step(spec, "GET /nope", "token", None) == (
        "the endpoint is not in the provider's current spec"
    )


# ---- integration: job-scheduler at ef17cf8 (the prereg's L-series) ---------------------------

REAL = Path("/Users/vivekgade/Documents/job-scheduler")
COMMIT = "ef17cf8"
CI_BOT = Path(__file__).resolve().parents[2] / "dogfood" / "contracts" / "ci-bot"
needs_real = pytest.mark.skipif(
    not (REAL / ".git").exists() or not CI_BOT.is_dir(),
    reason="the job-scheduler repository is not on this machine",
)

REAL_MANIFEST = """\
[workspace]
name = "probe"

[[project]]
name = "scheduler"
path = "scheduler"
role = "provider"
spec = "spec.tickets:t5"
app = "job_scheduler:make_app"
db = "spec.seed:build_seed_db"
login = "spec.seed:login"
src = "src"
env = {{ JOB_SCHEDULER_REPO_ROOT = "$PROJECT" }}

[[project]]
name = "ci-bot"
path = "ci-bot"
role = "consumer"

[[link]]
from = "ci-bot"
to = "scheduler"
kind = "uses"
contract = "live"
{extra}"""


@pytest.fixture(scope="module")
def clone(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dst = tmp_path_factory.mktemp("js") / "scheduler"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(REAL), str(dst)], check=True)
    subprocess.run(["git", "-C", str(dst), "checkout", "-q", COMMIT], check=True)
    return dst


def real_world(root: Path, clone: Path, extra: str = "") -> wsmod.Workspace:
    """A workspace of a fresh copy of the clean clone and a copy of ci-bot, pinned."""
    shutil.copytree(clone, root / "scheduler", symlinks=True)
    shutil.copytree(CI_BOT, root / "ci-bot")
    (root / wsmod.MANIFEST).write_text(REAL_MANIFEST.format(extra=extra))
    ws = wsmod.load(root / wsmod.MANIFEST)
    assert ck.run_pin(ws, "ci-bot").exit_code == 0
    return ws


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new))


def link_of(ws: wsmod.Workspace) -> dict:
    return ck.run_check(ws, "ci-bot").links[0]


def live_only(ws: wsmod.Workspace) -> dict:
    imports = ck.parse_imports(ws.project("ci-bot").path / ck.IMPORTS_FILE)
    export = ex.compute_export(ws.project("scheduler").path, "spec.tickets:t5")["export"]
    return live.run_live(ws, ws.links[0], imports, export)


@needs_real
def test_l1_clean_clone_replays_seven_of_seven(tmp_path: Path, clone: Path) -> None:
    ws = real_world(tmp_path, clone)
    t0 = time.monotonic()
    res = live_only(ws)
    took = time.monotonic() - t0
    assert res["findings"] == [] and res["replayed"] == 7 and res["not_replayed"] == []
    assert res["verified_live"] == [
        {
            "provider": "scheduler",
            "endpoint": "POST /auth/login",
            "kind": "response_field_unobserved",
            "item": "access_token",
        }
    ]
    assert res["fields_unobservable"] == [] and res["not_checked"] == ["request_fields"]
    assert took < 15, took
    again = [live_only(ws) for _ in range(2)]
    assert json.dumps(again[0], sort_keys=True) == json.dumps(res, sort_keys=True)
    assert json.dumps(again[1], sort_keys=True) == json.dumps(res, sort_keys=True)
    link = link_of(ws)
    assert link["status"] == "ok" and link["findings"] == [] and len(link["gaps"]) == 1
    assert link["live"]["verified_live"] == res["verified_live"]


@needs_real
def test_l2_status_in_the_app(tmp_path: Path, clone: Path) -> None:
    ws = real_world(tmp_path, clone)
    runs = tmp_path / "scheduler" / "src" / "job_scheduler" / "routers" / "runs.py"
    edit(runs, "    status_code=201,", "    status_code=200,")
    link = link_of(ws)
    assert link["findings"] == [] and len(link["gaps"]) == 1  # static: blind (B0's one gap)
    assert [
        (f["class"], f["kind"], f["endpoint"], f["item"]) for f in link["live"]["findings"]
    ] == [("BROKEN", "status_not_observed", "POST /applications/{}/jobs/{}/runs", "201")]
    assert link["live"]["findings"][0]["detail"].startswith("the live answer was 200")
    assert link["status"] == "broken"


@needs_real
def test_l3_role_in_the_app(tmp_path: Path, clone: Path) -> None:
    ws = real_world(tmp_path, clone)
    runs = tmp_path / "scheduler" / "src" / "job_scheduler" / "routers" / "runs.py"
    edit(runs, "    require_membership,\n)", "    require_app_admin,\n    require_membership,\n)")
    edit(runs, "    require_membership(session, application, current_user)\n"
               "    return get_job_run_or_404(session, application, run_id)",
         "    require_app_admin(session, application, current_user)\n"
         "    return get_job_run_or_404(session, application, run_id)")  # fmt: skip
    link = link_of(ws)
    assert link["findings"] == []  # static: blind
    assert [
        (f["class"], f["kind"], f["endpoint"], f["item"]) for f in link["live"]["findings"]
    ] == [("BROKEN", "auth_changed", "GET /applications/{}/runs/{}", "as_user:eve")]
    assert link["live"]["findings"][0]["detail"].startswith("the live answer was 403")


@needs_real
def test_l4_response_field_in_the_app(tmp_path: Path, clone: Path) -> None:
    ws = real_world(tmp_path, clone)
    edit(tmp_path / "scheduler" / "src" / "job_scheduler" / "schemas.py",
         "    exit_code: int | None\n", "")  # fmt: skip
    link = link_of(ws)
    assert link["findings"] == []  # static: blind
    assert [
        (f["class"], f["kind"], f["endpoint"], f["item"]) for f in link["live"]["findings"]
    ] == [("BROKEN", "response_field_missing", "GET /applications/{}/runs/{}", "exit_code")]


@needs_real
@pytest.mark.parametrize(("url", "code"), [
    ("http://10.0.0.1:8000", "not_loopback"),
    ("http://example.com", "not_loopback"),
    ("http://0.0.0.0:8000", "not_loopback"),
    ("http://127.0.0.1:8000", "live_url_unsupported"),
])  # fmt: skip
def test_l5_live_url_is_refused_and_nothing_is_sent(tmp_path: Path, clone: Path, url: str,
                                                    code: str, monkeypatch) -> None:  # fmt: skip
    ws = real_world(tmp_path, clone, extra=f'live_url = "{url}"\n')
    monkeypatch.setattr(live, "_spawn", lambda *a, **k: pytest.fail("a request would be sent"))
    rep = ck.run_check(ws, "ci-bot")
    assert rep.exit_code == 2
    assert rep.links[0]["status"] == "error" and rep.links[0]["error"]["code"] == code


@needs_real
def test_the_real_repository_is_never_written(tmp_path: Path, clone: Path) -> None:
    def status() -> str:
        return subprocess.run(["git", "-C", str(REAL), "status", "--short"], text=True,
                              capture_output=True, check=True).stdout  # fmt: skip

    before = status()
    root = tmp_path / "w"
    root.mkdir()
    ws = real_world(root, clone)
    assert live_only(ws)["replayed"] == 7
    assert status() == before
