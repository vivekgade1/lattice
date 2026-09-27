"""Thin-slice plan node E2 gates: the hand-written template Lattice, emitted by
``lattice.emit.thin`` and mounted into the vendored template app in place of its
login/users/items routers, passes the template differential exactly as the Lattice
runtime does (the same tally, the same policy deviations), and a Lattice schema edit
(a nullable ``Item.deleted_at``) runs on an existing template database.

The cases, rules, deviations and expected tally are the template proof's
(``test_template_differential.py``); only the Lattice side's app factory differs.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_template_differential import (
    ALL_CASES,
    DEVIATIONS,
    EXPECTED_TALLY,
    EXTRA_CASES,
    RULES,
    VALIDATION_ENVELOPE,
    runtime_factory,
    template_dbs,
)

from bench.template_app import make_app
from bench.template_app.app import utils as template_utils
from bench.template_app.seed import TEMPLATE_USERS, build_template_db
from bench.template_profile import template_profile, template_sources
from bench.template_suite import API, TEMPLATE_SUITE, template_login
from lattice.emit import EmitResult, emit_into_template
from lattice.emit.thin import make_emitted_app
from lattice.importer.verify import VerifyResult, verify
from lattice.typesys import Scalar, Schema


@pytest.fixture(scope="module")
def emitted(tmp_path_factory: pytest.TempPathFactory) -> EmitResult:
    out = tmp_path_factory.mktemp("emitted") / "template_emit"
    return emit_into_template(template_sources(), template_profile(), out)


def _verify(original: Any, lattice: Any, rules=RULES, deviations=None) -> VerifyResult:
    return verify(
        original,
        lattice,
        template_dbs,
        ALL_CASES,
        rules,
        DEVIATIONS if deviations is None else deviations,
        login=template_login,
        prefix=API,
    )


@pytest.fixture(scope="module")
def result(emitted: EmitResult) -> VerifyResult:
    template_utils.OUTBOX.clear()
    return _verify(make_app, lambda path: make_emitted_app(emitted.out_dir, path))


def test_the_emitted_app_gives_the_runtimes_tally(result: VerifyResult):
    tally = {
        "suite": result.tally([c.name for c in TEMPLATE_SUITE])[:3],
        "extra": result.tally([c.name for c in EXTRA_CASES])[:3],
    }
    assert tally == EXPECTED_TALLY  # suite: 40 + 9 = 49/53 equal, 4 policy deviations
    assert result.mismatches == {}
    assert result.rules_used == {VALIDATION_ENVELOPE.name}
    assert result.ok, result.render()


def test_the_emitted_app_has_the_same_deviations(result: VerifyResult):
    deviations = {name: o.deviation for name, o in result.outcomes.items() if o.kind == "deviation"}
    assert deviations == {name: d.id for name, d in DEVIATIONS.items()}
    for name, dev in DEVIATIONS.items():
        lat = result.lattice[name]
        assert (lat.status, lat.body) == (dev.status, dev.body)


def test_the_emitted_app_answers_exactly_like_the_runtime(emitted: EmitResult):
    same = _verify(
        runtime_factory,
        lambda path: make_emitted_app(emitted.out_dir, path),
        rules=(),
        deviations={},
    )
    assert {n: o.kind for n, o in same.outcomes.items()} == {c.name: "equal" for c in ALL_CASES}
    for name in same.outcomes:
        assert same.lattice[name].to_dict() == same.original[name].to_dict()


# ---- a schema edit: soft delete through a new nullable DateTime column ---------------------

ARCHIVE = """

@endpoint("POST", "/api/v1/items/{id}/archive", auth=Customer, response="ItemPublic")
def archive_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    allowed = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    archived = update(allowed, deleted_at=fn.now())
    return archived


@endpoint("GET", "/api/v1/items/live", auth=Customer, response="ItemPublic")
def read_live_items(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=0, le=100) = 100) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    live = where(mine, is_null(col("deleted_at")))
    ordered = sort(live, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result
"""


def _soft_delete_profile():
    profile = template_profile()
    assert profile.api is not None
    item = profile.api.tables["Item"]
    new = replace(
        item,
        columns=(*item.columns, ("deleted_at", Scalar.DATETIME)),
        nullable=item.nullable | {"deleted_at"},
    )
    assert isinstance(new, Schema)
    # ItemPublic (the response model every item route returns) shows the new column
    responses = {
        **profile.api.responses,
        "ItemPublic": (*profile.api.responses["ItemPublic"], "deleted_at"),
    }
    api = replace(profile.api, tables={**profile.api.tables, "Item": new}, responses=responses)
    return replace(profile, api=api)


def test_a_new_nullable_column_and_endpoint_run_on_an_existing_db(tmp_path: Path):
    sources = dict(template_sources())
    sources["items.lattice.py"] = sources["items.lattice.py"].rstrip("\n") + "\n" + ARCHIVE
    out = emit_into_template(sources, _soft_delete_profile(), tmp_path / "soft_delete")
    assert {"POST /api/v1/items/{id}/archive", "GET /api/v1/items/live"} <= set(out.routes_emitted)

    db = build_template_db(tmp_path / "seed.db")  # the template's schema: no deleted_at
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute('PRAGMA table_info("item")')}
    conn.close()
    assert "deleted_at" not in cols

    app = make_emitted_app(out.out_dir, db)
    try:
        assert app.state.schema_changes == ['ALTER TABLE "item" ADD COLUMN "deleted_at" DATETIME']
        email, password, _ = TEMPLATE_USERS["alice"]
        with TestClient(app) as client:
            token = client.post(
                f"{API}/login/access-token", data={"username": email, "password": password}
            ).json()["access_token"]
            auth = {"Authorization": f"Bearer {token}"}
            before = client.get(f"{API}/items/live", headers=auth).json()
            assert before["count"] == client.get(f"{API}/items/", headers=auth).json()["count"]
            assert before["count"] > 0
            assert all(i["deleted_at"] is None for i in before["data"])
            victim = before["data"][0]["id"]
            archived = client.post(f"{API}/items/{victim}/archive", headers=auth)
            assert archived.status_code == 200, archived.text
            assert archived.json()["id"] == victim
            assert archived.json()["deleted_at"] is not None
            after = client.get(f"{API}/items/live", headers=auth).json()
            assert after["count"] == before["count"] - 1
            assert victim not in {i["id"] for i in after["data"]}
            # the template's untouched routes still run on the altered table
            assert client.get(f"{API}/utils/health-check/").json() is True
        conn = sqlite3.connect(db)
        (stamp,) = conn.execute(
            'SELECT deleted_at FROM "item" WHERE id = ?', (victim.replace("-", ""),)
        ).fetchone()
        conn.close()
        assert isinstance(stamp, str) and len(stamp) == len("2026-01-01 00:00:00.000000")
    finally:
        app.state.engine.dispose()
