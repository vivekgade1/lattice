"""Round-2 fixes end to end on the runtime (tests/api/round2.py). The generated FastAPI
app is compared with the runtime in test_codegen_differential ("round2")."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.ir import lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.profile import ApiProfile, Profile
from tests.api import round2 as R


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {R.TOKENS[who]}"}


def _client(tmp_path: Path, profile: Profile) -> TestClient:
    db = tmp_path / "app.db"
    R.build_db(db)
    check = check_source(R.PROGRAM, profile)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return TestClient(build_app(lower_api(check.program), db, natives=R.NATIVES))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with _client(tmp_path, R.PROFILE) as c:
        yield c


def _titles(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [r["title"] for r in resp.json()["data"]]


# ---- 1. ApiProfile.null_order ----------------------------------------------------------------


def test_null_order_is_validated() -> None:
    with pytest.raises(ValueError, match="null_order"):
        ApiProfile(tables={}, table_names={}, roles=frozenset({"Public"}), null_order="first")


def test_smallest_puts_nulls_first_ascending_and_last_descending(client: TestClient) -> None:
    # Beta's description is null; Epsilon's is "" (the smallest text)
    up = _titles(client.get("/sorted/descriptions", headers=_h("admin")))
    assert up == ["Beta", "Epsilon", "Alpha", "Delta", "Zeta", "Gamma"]
    down = _titles(client.get("/sorted/descriptions", params={"newest": True}, headers=_h("admin")))
    assert down == ["Gamma", "Zeta", "Delta", "Alpha", "Epsilon", "Beta"]


def test_an_explicit_nulls_wins(client: TestClient) -> None:
    got = _titles(client.get("/sorted/descriptions/last", headers=_h("admin")))
    assert got == ["Epsilon", "Alpha", "Delta", "Zeta", "Gamma", "Beta"]


def test_the_default_profile_keeps_nulls_last(tmp_path: Path) -> None:
    with _client(tmp_path, R.LAST_PROFILE) as c:
        up = _titles(c.get("/sorted/descriptions", headers=_h("admin")))
        assert up == ["Epsilon", "Alpha", "Delta", "Zeta", "Gamma", "Beta"]
        down = _titles(c.get("/sorted/descriptions", params={"newest": True}, headers=_h("admin")))
        assert down == ["Gamma", "Zeta", "Delta", "Alpha", "Epsilon", "Beta"]
        rows = {
            r["title"]: r["n"]
            for r in c.get("/sorted/numbered", headers=_h("admin")).json()["data"]
        }
        assert rows["Beta"] == 6 and rows["Epsilon"] == 1


def test_window_order_follows_the_profile(client: TestClient) -> None:
    rows = {
        r["title"]: (r["n"], r["m"])
        for r in client.get("/sorted/numbered", headers=_h("admin")).json()["data"]
    }
    assert rows["Beta"] == (1, 6)
    assert rows["Epsilon"] == (2, 5)
    assert rows["Gamma"] == (6, 1)


# ---- 2. caller references ----------------------------------------------------------------


def test_user_id_filters_the_callers_own_rows(client: TestClient) -> None:
    got = client.get("/me/items", headers=_h("admin")).json()
    assert [r["title"] for r in got["data"]] == ["Zeta"] and got["count"] == 1
    got = client.get("/me/items", headers=_h("alice")).json()
    assert [r["title"] for r in got["data"]] == ["Alpha", "Beta", "Delta"]


def test_caller_values_in_derive(client: TestClient) -> None:
    resp = client.get("/me/flags", headers=_h("bob"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert [r["title"] for r in rows] == ["Epsilon", "Gamma"]
    assert rows[0] == {
        "title": "Epsilon",
        "mine": True,
        "admin": False,
        "admin2": False,
        "who": "bob@example.com",
        "name": "?",
        "since": "2000-01-03T09:00:00.000000Z",
        "scope": "own",
    }
    admin = client.get("/me/flags", headers=_h("admin")).json()["data"]
    assert {r["title"]: r["mine"] for r in admin}["Zeta"] is True
    assert all(r["admin"] and r["admin2"] and r["scope"] == "all" for r in admin)
    assert {r["name"] for r in admin} == {"Admin Istrator"}


def test_caller_values_as_write_values_and_checks(client: TestClient) -> None:
    resp = client.put(f"/items/{R.ALPHA}/stamp", headers=_h("alice"))
    assert resp.status_code == 200 and resp.json()["description"] == "alice@example.com"
    denied = client.put(f"/items/{R.ZETA}/stamp", headers=_h("admin"))
    assert denied.status_code == 403 and denied.json()["detail"] == "Admins do not stamp"
    other = client.put(f"/items/{R.GAMMA}/stamp", headers=_h("alice"))
    assert other.status_code == 404
    made = client.post("/items/signed", json={"title": "New"}, headers=_h("bob"))
    assert made.status_code == 200 and made.json()["description"] == "bob@example.com"
    assert made.json()["owner_id"] == R.BOB


def test_caller_references_cost_no_statement(tmp_path: Path) -> None:
    from lattice.api.ir import ReadStep

    check = check_source(R.PROGRAM, R.PROFILE)
    assert check.program is not None
    plans = {p.name: p for p in lower_api(check.program)}
    flags = plans["flags"]
    reads = [s for s in flags.steps if isinstance(s, ReadStep)]
    assert len(reads) == 1
    assert set(reads[0].sql.caller) == {"email", "full_name", "created_at"}
    assert reads[0].sql.user  # user.id / is_admin bind :__user_id / :__is_admin


# ---- 3. accessible -------------------------------------------------------------------------


def test_accessible_is_owned(client: TestClient) -> None:
    # /me/flags reads accessible(items): an admin sees every item, bob his own
    assert len(client.get("/me/flags", headers=_h("admin")).json()["data"]) == 6
    assert len(client.get("/me/flags", headers=_h("bob")).json()["data"]) == 2


# ---- 4. owner transfer ---------------------------------------------------------------------


def _owners(db: Path) -> dict[str, str]:
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT title, owner_id FROM item").fetchall())
    finally:
        conn.close()


def test_transfer_by_email(tmp_path: Path) -> None:
    with _client(tmp_path, R.PROFILE) as c:
        db = tmp_path / "app.db"
        body = {"new_owner_email": "bob@example.com"}
        resp = c.post(f"/items/{R.ALPHA}/transfer", json=body, headers=_h("alice"))
        assert resp.status_code == 200 and resp.json()["owner_id"] == R.BOB
        assert _owners(db)["Alpha"] == R.BOB
        # bob now owns it: alice may no longer transfer it (403 before the lookup)
        again = c.post(f"/items/{R.ALPHA}/transfer", json=body, headers=_h("alice"))
        assert again.status_code == 403
        same = c.post(f"/items/{R.ALPHA}/transfer", json=body, headers=_h("bob"))
        assert same.status_code == 400 and same.json()["detail"] == "Already theirs"
        nobody = {"new_owner_email": "nobody@example.com"}
        missing = c.post(f"/items/{R.ALPHA}/transfer", json=nobody, headers=_h("bob"))
        assert missing.status_code == 404 and missing.json()["detail"] == "User not found"
        carol = {"new_owner_email": "carol@example.com"}
        inactive = c.post(f"/items/{R.ALPHA}/transfer", json=carol, headers=_h("bob"))
        assert inactive.status_code == 400 and inactive.json()["detail"] == "Inactive user"
        admin = c.post(
            f"/items/{R.GAMMA}/transfer",
            json=body | {"new_owner_email": "alice@example.com"},
            headers=_h("admin"),
        )
        assert admin.status_code == 200 and _owners(db)["Gamma"] == R.ALICE


def test_transfer_by_id_needs_an_existing_user(tmp_path: Path) -> None:
    with _client(tmp_path, R.PROFILE) as c:
        db = tmp_path / "app.db"
        before = _owners(db)
        gone = c.put(f"/items/{R.ALPHA}/owner", json={"owner_id": R.NOBODY}, headers=_h("alice"))
        assert gone.status_code == 404 and gone.json()["detail"] == "User not found"
        assert _owners(db) == before  # nothing written (not even the title)
        ok = c.put(f"/items/{R.ALPHA}/owner", json={"owner_id": R.CAROL}, headers=_h("alice"))
        assert ok.status_code == 200
        assert ok.json()["owner_id"] == R.CAROL and ok.json()["title"] == "ALPHA"
        other = c.put(f"/items/{R.GAMMA}/owner", json={"owner_id": R.ALICE}, headers=_h("alice"))
        assert other.status_code == 404 and other.json()["detail"] == "Item not found"


def test_an_admin_claims_an_item(tmp_path: Path) -> None:
    with _client(tmp_path, R.PROFILE) as c:
        resp = c.put(f"/items/{R.GAMMA}/claim", headers=_h("admin"))
        assert resp.status_code == 200 and resp.json()["owner_id"] == R.ADMIN
        assert c.put(f"/items/{R.GAMMA}/claim", headers=_h("alice")).status_code == 403


def test_a_many_row_transfer_is_all_or_nothing(tmp_path: Path) -> None:
    with _client(tmp_path, R.PROFILE) as c:
        db = tmp_path / "app.db"
        before = _owners(db)
        gone = c.put("/items/mine/owner", json={"owner_id": R.NOBODY}, headers=_h("alice"))
        assert gone.status_code == 409 and gone.json()["detail"] == "No such user"
        assert _owners(db) == before
        ok = c.put("/items/mine/owner", json={"owner_id": R.BOB}, headers=_h("alice"))
        assert ok.status_code == 204
        assert {t for t, o in _owners(db).items() if o == R.BOB} == {
            "Alpha",
            "Beta",
            "Delta",
            "Gamma",
            "Epsilon",
        }
        # nobody left for alice: an empty transfer writes nothing and succeeds
        assert (
            c.put("/items/mine/owner", json={"owner_id": R.NOBODY}, headers=_h("alice")).status_code
            == 204
        )


def test_a_transfer_is_one_statement(tmp_path: Path) -> None:
    from lattice.api.ir import WriteStep

    check = check_source(R.PROGRAM, R.PROFILE)
    assert check.program is not None
    plans = {p.name: p for p in lower_api(check.program)}
    for name in ("transfer_item", "give_item", "claim_item", "give_all"):
        writes = [s for s in plans[name].steps if isinstance(s, WriteStep)]
        assert len(writes) == 1 and writes[0].kind == "update", name
    assert "EXISTS" in [s for s in plans["give_item"].steps if isinstance(s, WriteStep)][0].sql.text
    assert (
        "EXISTS"
        not in [s for s in plans["transfer_item"].steps if isinstance(s, WriteStep)][0].sql.text
    )
