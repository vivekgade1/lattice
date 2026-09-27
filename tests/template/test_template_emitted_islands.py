"""The emitted app keeps the template's routes the Lattice program does not define
(``lattice.emit.thin``: import semantics -- only the lifted routes are replaced, matched
by method and normalized path; every other original route stays, as a native island).

* **Kept routes answer as in the template.** ``POST /users/signup``,
  ``GET /users/{user_id}``, ``DELETE /users/me``, ``POST /login/test-token`` and a few
  more run on the emitted app exactly as on the plain template, compared with the
  importer's differential (``lattice.importer.verify``).
* **Precedence.** A new literal Lattice route (``GET /users/item-counts``,
  ``GET /items/stats``) is not shadowed by a kept or lifted parameter route
  (``/users/{user_id}``, ``/items/{id}``), and a kept original is not shadowed by a
  Lattice route.
* **Removal.** A lifted route the edited program no longer defines is gone from the app.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_template_differential import RULES, template_dbs

from bench.template_app import make_app
from bench.template_app.app import utils as template_utils
from bench.template_app.seed import TEMPLATE_USERS, build_template_db
from bench.template_profile import template_profile, template_sources
from bench.template_suite import ALICE_ITEM, API, MISSING_ID, TemplateCase, template_login
from lattice.emit import EmitResult, emit_into_template
from lattice.emit.thin import make_emitted_app, merge_routes, program_routes
from lattice.importer.verify import VerifyResult, verify

ALICE = str(TEMPLATE_USERS["alice"][2])
BOB = str(TEMPLATE_USERS["bob"][2])
ADMIN_ID = str(TEMPLATE_USERS["admin"][2])
_C = TemplateCase

KEPT_CASES: list[TemplateCase] = [
    _C("signup_new", "POST", "/users/signup", tables=("user",),
       json={"email": "dora@example.com", "password": "dorapass1234", "full_name": "Dora"}),
    _C("signup_taken", "POST", "/users/signup", tables=("user",),
       json={"email": "alice@example.com", "password": "whatever1234"}),
    _C("signup_short_password", "POST", "/users/signup", tables=("user",),
       json={"email": "eve@example.com", "password": "short"}),
    _C("user_by_id_admin", "GET", f"/users/{ALICE}", user="admin", tables=("user",)),
    _C("user_by_id_self", "GET", f"/users/{ALICE}", user="alice", tables=("user",)),
    _C("user_by_id_other", "GET", f"/users/{BOB}", user="alice", tables=("user",)),
    _C("user_by_id_missing", "GET", f"/users/{MISSING_ID}", user="admin", tables=("user",)),
    _C("user_by_id_malformed", "GET", "/users/not-a-uuid", user="admin", tables=("user",)),
    _C("delete_me", "DELETE", "/users/me", user="alice", tables=("user", "item")),
    _C("delete_me_superuser", "DELETE", "/users/me", user="admin", tables=("user",)),
    _C("delete_user_admin", "DELETE", f"/users/{BOB}", user="admin", tables=("user", "item")),
    _C("delete_user_not_admin", "DELETE", f"/users/{BOB}", user="alice", tables=("user",)),
    _C("update_user_admin", "PATCH", f"/users/{BOB}", user="admin", tables=("user",),
       json={"full_name": "Robert"}),
    _C("create_user_admin", "POST", "/users/", user="admin", tables=("user",),
       json={"email": "fay@example.com", "password": "faypass12345"}),
    _C("password_me", "PATCH", "/users/me/password", user="alice", tables=("user",),
       json={"current_password": "alicepass123", "new_password": "alicepass456"}),
    _C("password_me_wrong", "PATCH", "/users/me/password", user="alice", tables=("user",),
       json={"current_password": "nope12345678", "new_password": "alicepass456"}),
    _C("test_token", "POST", "/login/test-token", user="alice", tables=("user",)),
    _C("test_token_admin", "POST", "/login/test-token", user="admin", tables=("user",)),
    _C("test_token_anonymous", "POST", "/login/test-token", tables=("user",)),
    # the lifted routes next to them still answer (Lattice /users/me is not shadowed by
    # the kept /users/{user_id}, and vice versa)
    _C("users_me", "GET", "/users/me", user="alice", tables=("user",)),
    _C("admin_reads_admin_by_id", "GET", f"/users/{ADMIN_ID}", user="admin", tables=("user",)),
]  # fmt: skip


@pytest.fixture(scope="module")
def emitted(tmp_path_factory: pytest.TempPathFactory) -> EmitResult:
    out = tmp_path_factory.mktemp("islands") / "template_emit"
    return emit_into_template(template_sources(), template_profile(), out)


def _verify(emitted_factory: Any) -> VerifyResult:
    template_utils.OUTBOX.clear()
    return verify(
        make_app,
        emitted_factory,
        template_dbs,
        KEPT_CASES,
        RULES,
        {},
        login=template_login,
        prefix=API,
    )


def test_the_emit_result_reports_replaced_and_kept_routes(emitted: EmitResult):
    replaced, kept = set(emitted.routes_replaced), set(emitted.routes_kept)
    # exactly the template routes the program defines (template paths: {user_id} etc.)
    assert replaced == set(emitted.routes_emitted)
    assert not replaced & kept
    for route in (
        "POST /api/v1/users/signup",
        "GET /api/v1/users/{user_id}",
        "PATCH /api/v1/users/{user_id}",
        "DELETE /api/v1/users/{user_id}",
        "DELETE /api/v1/users/me",
        "PATCH /api/v1/users/me/password",
        "POST /api/v1/users/",
        "POST /api/v1/login/test-token",
        "POST /api/v1/password-recovery/{email}",
        "POST /api/v1/reset-password/",
        "GET /api/v1/utils/health-check/",
    ):
        assert route in kept, route


def test_kept_routes_answer_as_in_the_template(emitted: EmitResult):
    result = _verify(lambda path: make_emitted_app(emitted.out_dir, path))
    assert result.mismatches == {}, result.render()
    assert {n: o.kind for n, o in result.outcomes.items()} == {
        c.name: "equal" for c in KEPT_CASES
    }, result.render()
    # the cases really exercise the kept routes (not a 404/405 on both sides)
    statuses = {n: r.status for n, r in result.original.items()}
    assert statuses["signup_new"] == 200
    assert statuses["user_by_id_admin"] == 200
    assert statuses["delete_me"] == 200
    assert statuses["test_token"] == 200
    assert statuses["users_me"] == 200


# ---- precedence -----------------------------------------------------------------------------

ITEM_COUNTS = """

@endpoint("GET", "/api/v1/users/item-counts", auth=Admin, response="UserPublic")
def read_user_item_counts(user: CurrentUser) -> Page:
    users = fetch(User)
    public = select(users, cols=USER_PUBLIC)
    ordered = sort(public, by=["email"])
    result = page(ordered, limit=100, offset=0, total=True)
    return result
"""

ITEM_STATS = """

@endpoint("GET", "/api/v1/items/stats", auth=Customer, response="ItemPublic")
def read_item_stats(user: CurrentUser) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["title"])
    result = page(ordered, limit=100, offset=0, total=True)
    return result
"""


def _added_sources() -> dict[str, str]:
    sources = dict(template_sources())
    sources["users.lattice.py"] = sources["users.lattice.py"].rstrip("\n") + "\n" + ITEM_COUNTS
    sources["items.lattice.py"] = sources["items.lattice.py"].rstrip("\n") + "\n" + ITEM_STATS
    return sources


def _token(client: TestClient, handle: str) -> dict[str, str]:
    email, password, _ = TEMPLATE_USERS[handle]
    r = client.post(f"{API}/login/access-token", data={"username": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_new_literal_routes_are_not_shadowed_and_do_not_shadow(tmp_path: Path):
    out = emit_into_template(_added_sources(), template_profile(), tmp_path / "added")
    assert "GET /api/v1/users/item-counts" in out.routes_emitted
    assert "GET /api/v1/users/item-counts" not in out.routes_replaced  # new, not a template route
    assert "GET /api/v1/users/{user_id}" in out.routes_kept
    app = make_emitted_app(out.out_dir, build_template_db(tmp_path / "seed.db"))
    try:
        with TestClient(app) as client:
            admin, alice = _token(client, "admin"), _token(client, "alice")
            counts = client.get(f"{API}/users/item-counts", headers=admin)
            assert counts.status_code == 200, counts.text
            assert counts.json()["count"] == len(TEMPLATE_USERS)
            assert [u["email"] for u in counts.json()["data"]] == sorted(
                e for e, _, _ in TEMPLATE_USERS.values()
            )
            # the kept original still answers /users/{uuid} (the template's own handler)
            by_id = client.get(f"{API}/users/{ALICE}", headers=admin)
            assert by_id.status_code == 200, by_id.text
            assert by_id.json()["email"] == "alice@example.com"
            missing = client.get(f"{API}/users/{uuid.UUID(MISSING_ID)}", headers=admin)
            assert missing.status_code == 404
            assert missing.json() == {"detail": "User not found"}
            # the lifted /users/me is not shadowed by the kept /users/{user_id}
            assert client.get(f"{API}/users/me", headers=alice).json()["id"] == ALICE
            # /items/stats next to the lifted /items/{id}
            stats = client.get(f"{API}/items/stats", headers=alice)
            assert stats.status_code == 200, stats.text
            assert stats.json()["count"] > 0
            item = client.get(f"{API}/items/{ALICE_ITEM}", headers=alice)
            assert item.status_code == 200 and item.json()["id"] == ALICE_ITEM
    finally:
        app.state.engine.dispose()


def test_a_lifted_route_the_program_drops_is_removed(tmp_path: Path):
    sources = dict(template_sources())
    items = sources["items.lattice.py"]
    start = items.index('@endpoint("DELETE", "/api/v1/items/{id}"')
    sources["items.lattice.py"] = items[:start].rstrip("\n") + "\n"
    lifted = program_routes(template_sources(), template_profile())
    assert "DELETE /api/v1/items/{id}" in lifted
    out = emit_into_template(sources, template_profile(), tmp_path / "dropped", lifted=lifted)
    assert "DELETE /api/v1/items/{id}" not in out.routes_emitted
    assert "DELETE /api/v1/items/{id}" in out.routes_replaced
    assert "DELETE /api/v1/items/{id}" not in out.routes_kept
    app = make_emitted_app(out.out_dir, build_template_db(tmp_path / "seed.db"))
    try:
        with TestClient(app) as client:
            alice = _token(client, "alice")
            gone = client.delete(f"{API}/items/{ALICE_ITEM}", headers=alice)
            assert gone.status_code == 405  # the path is still routed (GET, PUT)
            assert client.get(f"{API}/items/{ALICE_ITEM}", headers=alice).status_code == 200
    finally:
        app.state.engine.dispose()

    # without the lifted set, the template's own route is kept (nothing says it was lifted)
    kept = emit_into_template(sources, template_profile(), tmp_path / "kept")
    assert "DELETE /api/v1/items/{id}" in kept.routes_kept


# ---- the merge rule, on paths alone ---------------------------------------------------------


def _merge(original: list[str], lattice: list[str], replaced: set[str]) -> list[str]:
    def split(r: str) -> tuple[str, str]:
        m, p = r.split(" ")
        return m, p

    return [
        f"{m} {p}"
        for m, p in merge_routes(
            [split(r) for r in original], [split(r) for r in lattice], {split(r) for r in replaced}
        )
    ]


def test_merge_substitutes_in_place_and_orders_new_routes_by_specificity():
    original = [
        "GET /u/me",
        "DELETE /u/me",
        "POST /u/signup",
        "GET /u/{user_id}",
        "DELETE /u/{user_id}",
        "GET /i/",
        "GET /i/{id}",
        "GET /x/",
    ]
    lattice = ["GET /i/stats", "GET /i/", "GET /i/{item}", "GET /u/count", "GET /u/me"]
    merged = _merge(original, lattice, {"GET /u/me", "GET /i/", "GET /i/{id}"})
    assert merged == [
        "GET /u/me",  # lattice, in the original's place
        "DELETE /u/me",
        "POST /u/signup",
        "GET /u/count",  # new: before the kept /u/{user_id} it would be shadowed by
        "GET /u/{user_id}",
        "DELETE /u/{user_id}",
        "GET /i/",  # lattice
        "GET /i/stats",  # new: before the lifted /i/{item}
        "GET /i/{item}",  # lattice, in /i/{id}'s place
        "GET /x/",
    ]


def test_merge_puts_a_less_specific_new_route_after_the_originals_it_overlaps():
    original = ["GET /u/me", "GET /u/{user_id}/items", "GET /h/"]
    merged = _merge(original, ["GET /u/{id}", "GET /u/{id}/{what}"], set())
    # a new parameter route never shadows a kept literal one
    assert merged.index("GET /u/{id}") > merged.index("GET /u/me")
    assert merged.index("GET /u/{id}/{what}") > merged.index("GET /u/{user_id}/items")


def test_merge_drops_replaced_routes_the_program_no_longer_has():
    merged = _merge(
        ["GET /i/{id}", "DELETE /i/{id}"], ["GET /i/{x}"], {"GET /i/{id}", "DELETE /i/{id}"}
    )
    assert merged == ["GET /i/{x}"]
