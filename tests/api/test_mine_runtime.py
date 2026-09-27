"""`mine(t, by=user)` end to end on the runtime (tests/api/mine_owner.py): the caller's
own rows for every role -- an Admin's `mine()` excludes other users' rows, where its
`owned()` keeps them -- directly and through a parent. The generated FastAPI app is
compared with the runtime in test_codegen_differential."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.ir import lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from tests.api import mine_owner as M
from tests.api.realapps import ADMIN, ALICE, BOB


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {M.TOKENS[who]}"}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = tmp_path / "app.db"
    M.build_db(db)
    check = check_source(M.PROGRAM, M.PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    with TestClient(build_app(lower_api(check.program), db, natives=M.NATIVES)) as c:
        yield c


def _owners(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {r["owner_id"] for r in resp.json()["data"]}


def test_an_admins_mine_excludes_other_users_rows(client: TestClient) -> None:
    mine = client.get("/items/mine", headers=_h("admin")).json()
    assert [r["title"] for r in mine["data"]] == ["Zeta"] and mine["count"] == 1
    assert _owners(client.get("/items/visible", headers=_h("admin"))) == {ADMIN, ALICE, BOB}


def test_a_customers_mine_equals_owned(client: TestClient) -> None:
    for who, owner in (("alice", ALICE), ("bob", BOB)):
        mine = client.get("/items/mine", headers=_h(who)).json()
        visible = client.get("/items/visible", headers=_h(who)).json()
        assert mine == visible and {r["owner_id"] for r in mine["data"]} == {owner}
    assert client.get("/items/mine", headers=_h("carol")).json()["data"] == []


def test_mine_stats_count_only_the_admins_items(client: TestClient) -> None:
    got = client.get("/items/mine/stats", headers=_h("admin")).json()
    assert got["count"] == 1 and got["latest_created_at"].startswith("2000-01-06")
    assert client.get("/items/mine/stats", headers=_h("alice")).json()["count"] == 3


def test_mine_through_a_parent_follows_the_owned_chain(client: TestClient) -> None:
    def bodies(path: str, who: str) -> list[str]:
        resp = client.get(path, headers=_h(who))
        assert resp.status_code == 200, resp.text
        return [r["body"] for r in resp.json()["data"]]

    assert bodies("/notes/mine", "admin") == ["note on zeta"]
    assert bodies("/notes/visible", "admin") == ["note on alpha", "note on gamma", "note on zeta"]
    assert bodies("/notes/mine", "alice") == ["note on alpha"]
    assert bodies("/notes/mine", "carol") == []


def test_an_admin_cannot_write_another_users_row_through_mine(client: TestClient) -> None:
    body = {"title": "hijacked"}
    resp = client.put(f"/items/mine/{M.ALPHA}", json=body, headers=_h("admin"))
    assert resp.status_code == 404 and resp.json()["detail"] == "Item not found"
    own = client.put(f"/items/mine/{M.ZETA}", json={"title": "Zeta 2"}, headers=_h("admin"))
    assert own.status_code == 200 and own.json()["title"] == "Zeta 2"
    alice = client.put(f"/items/mine/{M.ALPHA}", json={"title": "A2"}, headers=_h("alice"))
    assert alice.status_code == 200 and alice.json()["title"] == "A2"
