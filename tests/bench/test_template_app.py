"""The vendored FastAPI template (``bench/template_app``) as a proof target.

Checks that the upstream app builds in-process on SQLite, that the seed database is
deterministic, and that the request suite (``bench/template_suite.py``) reproduces the
stored golden snapshot ``tests/fixtures/template/golden.json`` exactly (status, JSON
body and post-request table contents, with generated values as placeholders).
Regenerate the golden with ``LATTICE_UPDATE_GOLDEN=1 uv run pytest
tests/bench/test_template_app.py``.

Observed policy findings in the upstream template (kept as-is, recorded in the golden;
a faithful Lattice port must reproduce them or list them as intentional deviations):

* **Unbounded page size.** ``GET /items/`` and ``GET /users/`` take ``skip``/``limit``
  as plain ints with no upper bound: ``limit=1000000`` is accepted (200) and returns
  every row. ``limit=0`` returns an empty page with the full ``count``; a ``skip`` past
  the end also returns ``{"data": [], "count": N}``.
* **403 discloses existence.** ``GET/PUT/DELETE /items/{id}`` answer another user's
  item with ``403 Not enough permissions`` but a missing id with ``404 Item not found``,
  so a normal user can probe which item ids exist. (Superusers get 200 on any item.)
* Invalid bearer tokens get ``403 Could not validate credentials`` (not 401); a missing
  token gets ``401 Not authenticated`` from ``OAuth2PasswordBearer``.
* ``PATCH /users/me`` silently ignores fields outside ``UserUpdateMe`` (e.g.
  ``is_superuser``) and treats explicit ``null`` as "set to null".
* On SQLite, ``created_at`` round-trips as a naive datetime (``DateTime(timezone=True)``
  has no zone storage there), so responses carry no UTC offset; ids are stored as
  32-char hex (``CHAR(32)``).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench.template_app import make_app
from bench.template_app.app import utils as template_utils
from bench.template_app.app.core import security
from bench.template_app.app.core.config import settings
from bench.template_app.seed import (
    N_ITEMS,
    TEMPLATE_ITEMS,
    TEMPLATE_USERS,
    build_template_db,
)
from bench.template_suite import (
    ALICE_ITEM,
    BOB_ITEM,
    MISSING_ID,
    TEMPLATE_SUITE,
    run_template_suite,
)

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "template" / "golden.json"


def _dump(db: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db)
    try:
        return {
            t: conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()
            for t in ("user", "item")
        }
    finally:
        conn.close()


@pytest.fixture(scope="module")
def seed_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_template_db(tmp_path_factory.mktemp("template") / "seed.db")


@pytest.fixture(scope="module")
def records(seed_db: Path) -> list[dict]:
    template_utils.OUTBOX.clear()
    return [r.to_dict() for r in run_template_suite(make_app, seed_db)]


# ------------------------------------------------------------------------ app --


def test_settings_are_test_safe() -> None:
    assert settings.DATABASE_URL.startswith("sqlite")
    assert settings.SECRET_KEY == "lattice-template-app-test-secret-key-000000000000"
    assert not settings.emails_enabled


def test_app_builds_on_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    app = make_app(db)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/utils/health-check/").json() is True
            assert client.get("/api/v1/users/me").status_code == 401
            paths = set(client.get("/api/v1/openapi.json").json()["paths"])
    finally:
        app.state.engine.dispose()
    assert {"/api/v1/items/", "/api/v1/items/{id}", "/api/v1/login/access-token",
            "/api/v1/users/me", "/api/v1/users/"} <= paths
    tables = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"user", "item"} <= tables


# ----------------------------------------------------------------------- seed --


def test_seed_is_deterministic(tmp_path: Path) -> None:
    a = _dump(build_template_db(tmp_path / "a.db"))
    b = _dump(build_template_db(tmp_path / "b.db"))
    assert a == b
    assert len(a["user"]) == 4 and len(a["item"]) == N_ITEMS
    other = _dump(build_template_db(tmp_path / "c.db", seed=32))
    assert other["item"] != a["item"]


def test_seed_users_and_passwords(seed_db: Path) -> None:
    rows = {r[0]: r for r in sqlite3.connect(seed_db).execute(
        "SELECT email, id, hashed_password, is_superuser FROM user")}
    assert set(TEMPLATE_USERS) == {"admin", "alice", "bob", "carol"}
    for handle, (email, password, uid) in TEMPLATE_USERS.items():
        _, stored_id, hashed, is_super = rows[email]
        assert stored_id == uid.hex
        assert security.verify_password(password, hashed)[0]
        assert not security.verify_password(password + "x", hashed)[0]
        assert bool(is_super) == (handle == "admin")
    owners = {it.owner for it in TEMPLATE_ITEMS}
    assert owners == set(TEMPLATE_USERS)
    assert len({it.created_at for it in TEMPLATE_ITEMS}) == N_ITEMS


# ---------------------------------------------------------------------- suite --


def test_suite_names_unique() -> None:
    names = [c.name for c in TEMPLATE_SUITE]
    assert len(names) == len(set(names))


def test_suite_matches_golden(records: list[dict]) -> None:
    if os.environ.get("LATTICE_UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(records, indent=1, ensure_ascii=False) + "\n")
    golden = json.loads(GOLDEN.read_text())
    assert [r["name"] for r in records] == [r["name"] for r in golden]
    for got, want in zip(records, golden, strict=True):
        assert got == want, got["name"]


def test_no_email_sent(records: list[dict]) -> None:
    assert template_utils.OUTBOX == []


def test_policy_findings(records: list[dict]) -> None:
    by = {r["name"]: r for r in records}
    # unbounded limit accepted
    unbounded = by["items_list_unbounded_limit"]
    assert unbounded["status"] == 200
    alice_items = sum(1 for it in TEMPLATE_ITEMS if it.owner == "alice")
    assert len(unbounded["body"]["data"]) == unbounded["body"]["count"] == alice_items
    assert by["users_list_unbounded_limit"]["status"] == 200
    # 403 on another user's item vs 404 on a missing one: existence is disclosed
    for verb in ("read", "update", "delete"):
        assert by[f"item_{verb}_other_user"]["status"] == 403
        assert by[f"item_{verb}_missing"]["status"] == 404
        assert by[f"item_{verb}_bad_uuid"]["status"] == 422
    assert by["item_read_other_user"]["path"] == f"/items/{BOB_ITEM}"
    assert MISSING_ID not in {str(it.id) for it in TEMPLATE_ITEMS}


def test_partial_updates_and_writes(records: list[dict]) -> None:
    by = {r["name"]: r for r in records}
    seeded = next(it for it in TEMPLATE_ITEMS if str(it.id) == ALICE_ITEM)

    def row(name: str) -> dict | None:
        rows = by[name]["db"]["item"]
        return next((r for r in rows if r["id"] == seeded.id.hex), None)

    renamed = by["item_update_own_title_only"]["body"]
    assert (renamed["title"], renamed["description"]) == ("Renamed", seeded.description)
    assert row("item_update_own_title_only")["description"] == seeded.description
    assert row("item_update_own_description_null")["description"] is None
    assert row("item_delete_own") is None
    assert len(by["item_delete_own"]["db"]["item"]) == N_ITEMS - 1
    created = by["item_create"]
    # the same generated value (same number), spelled as the API and SQLite spell it
    assert created["body"]["id"] == "<uuid:1:dashed>"
    assert created["db"]["item"][-1]["id"] == "<uuid:1:hex32>"
    assert created["body"]["created_at"] == "<datetime:1:T>"
    assert created["db"]["item"][-1]["created_at"] == "<datetime:1:space>"
    assert by["login_ok"]["body"] == {"access_token": "<token>", "token_type": "bearer"}


def test_placeholders_keep_the_spelling(seed_db: Path) -> None:
    """A generated value's placeholder keeps its spelling class, so a backend storing a
    new id or instant in another spelling than the database's own is caught by the
    table snapshots (a canonical-spelled id written into this hex32 database)."""
    import uuid

    from bench.template_suite import _Normalizer

    n = _Normalizer(seed_db).fresh()
    new = uuid.uuid4()
    assert n.value(new.hex) == "<uuid:1:hex32>"
    assert n.value(str(new)) == "<uuid:1:dashed>"
    assert n.value(str(new)) != n.value(new.hex)
    assert n.value(str(new).upper()) == "<uuid:1:dashed-upper>"
    assert n.value("2031-05-06 07:08:09.123456") == "<datetime:1:space>"
    assert n.value("2031-05-06T07:08:09.123456Z") == "<datetime:1:TZ>"
    assert n.value("2031-05-06T09:08:09.123456+02:00") == "<datetime:1:T+offset>"
    assert n.value("2031-05-06T07:08:09.123456") == "<datetime:1:T>"
    # seeded values stay as they are
    seeded = next(iter(TEMPLATE_ITEMS)).id
    assert n.value(seeded.hex) == seeded.hex
