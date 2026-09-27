"""Request suite for the vendored FastAPI template (items, users, login routes).

``TEMPLATE_SUITE`` is a list of :class:`TemplateCase`. ``run_template_suite(app_factory,
db_path)`` issues each case against a fresh copy of the seeded database (built by
:func:`bench.template_app.seed.build_template_db`) through Starlette's ``TestClient``,
logging in first through ``/login/access-token`` when the case acts as a user. For each
case it records the status, the JSON body and a snapshot of the affected tables *after*
the request, read with plain ``sqlite3`` (raw stored values, rows in ``rowid`` order).

Values generated during the request are replaced by placeholders so two backends can be
compared exactly:

* UUIDs (dashed or 32-hex) not present in the seed database -> ``<uuid:N:SPELLING>``;
* datetimes not present in the seed database -> ``<datetime:N:SPELLING>``;
* JWT-shaped strings -> ``<token>``;
* ``hashed_password`` values -> ``<hash:seed>`` if unchanged from the seed, else
  ``<hash:new>``.

``N`` numbers distinct values in order of first appearance (body first, then tables in
case order), and the same value gets the same number in the body and in the tables
(a dashed UUID in JSON and its 32-hex form in SQLite are the same value). ``SPELLING``
keeps how the value was written, so two backends must also agree on it: ``dashed`` /
``hex32`` (``-upper`` when not lowercase) for a UUID; ``T`` / ``space`` (the date-time
separator) then ``Z`` / ``+offset`` / nothing (the zone) for a datetime, e.g.
``<uuid:1:hex32>``, ``<datetime:1:space>``, ``<datetime:2:TZ>``.

The runner and the normaliser are the importer's generic ones
(:mod:`lattice.importer.verify`); this module keeps the template's hand-written cases.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.template_app.seed import TEMPLATE_ITEMS, TEMPLATE_USERS
from lattice.importer.verify import (
    Case,
    CaseRecord,
    Normalizer,
    datetime_spelling,
    form_login,
    run_suite,
    snapshot_tables,
    uuid_spelling,
)

__all__ = [
    "API",
    "BAD_TOKEN",
    "MISSING_ID",
    "TEMPLATE_SUITE",
    "CaseRecord",
    "TemplateCase",
    "datetime_spelling",
    "run_template_suite",
    "snapshot_tables",
    "template_login",
    "uuid_spelling",
]

API = "/api/v1"
MISSING_ID = "00000000-0000-4000-8000-000000000000"
BAD_TOKEN = "not-a-jwt"

AppFactory = Callable[[Path], Any]


@dataclass(frozen=True)
class TemplateCase(Case):
    """A :class:`lattice.importer.verify.Case` whose snapshot defaults to ``item``;
    ``user`` is a ``TEMPLATE_USERS`` handle."""

    tables: tuple[str, ...] = ("item",)


def _own(handle: str, k: int = 0) -> str:
    """The k-th seeded item (insertion order) owned by ``handle``, as a dashed UUID."""
    return str([it.id for it in TEMPLATE_ITEMS if it.owner == handle][k])


def _email(handle: str) -> str:
    return TEMPLATE_USERS[handle][0]


def _password(handle: str) -> str:
    return TEMPLATE_USERS[handle][1]


ALICE_ITEM = _own("alice")
ALICE_ITEM_2 = _own("alice", 1)
BOB_ITEM = _own("bob")
CAROL_ITEM = _own("carol")

_C = TemplateCase
TEMPLATE_SUITE: list[TemplateCase] = [
    # ---- login
    _C("login_ok", "POST", "/login/access-token", tables=("user",),
       form={"username": _email("alice"), "password": _password("alice")}),
    _C("login_ok_superuser", "POST", "/login/access-token", tables=("user",),
       form={"username": _email("admin"), "password": _password("admin")}),
    _C("login_wrong_password", "POST", "/login/access-token", tables=("user",),
       form={"username": _email("alice"), "password": "wrong-password"}),
    _C("login_unknown_email", "POST", "/login/access-token", tables=("user",),
       form={"username": "nobody@example.com", "password": "whatever123"}),
    _C("login_missing_password", "POST", "/login/access-token", tables=("user",),
       form={"username": _email("alice")}),
    # ---- read_items
    _C("items_list_user", "GET", "/items/", user="alice"),
    _C("items_list_user_page", "GET", "/items/", user="alice", params={"skip": 2, "limit": 3}),
    _C("items_list_user_limit1", "GET", "/items/", user="bob", params={"limit": 1}),
    _C("items_list_superuser", "GET", "/items/", user="admin"),
    _C("items_list_superuser_page", "GET", "/items/", user="admin",
       params={"skip": 5, "limit": 5}),
    _C("items_list_superuser_skip_past_end", "GET", "/items/", user="admin",
       params={"skip": 50}),
    _C("items_list_limit0", "GET", "/items/", user="admin", params={"limit": 0}),
    _C("items_list_unbounded_limit", "GET", "/items/", user="alice",
       params={"limit": 1000000},
       note="policy finding: no upper bound on limit; 1000000 is accepted"),
    _C("items_list_bad_limit", "GET", "/items/", user="alice", params={"limit": "many"}),
    _C("items_list_no_token", "GET", "/items/"),
    _C("items_list_bad_token", "GET", "/items/", token=BAD_TOKEN),
    # ---- read_item
    _C("item_read_own", "GET", f"/items/{ALICE_ITEM}", user="alice"),
    _C("item_read_other_user", "GET", f"/items/{BOB_ITEM}", user="alice",
       note="policy finding: 403 (not 404) discloses that the id exists"),
    _C("item_read_superuser_other", "GET", f"/items/{BOB_ITEM}", user="admin"),
    _C("item_read_missing", "GET", f"/items/{MISSING_ID}", user="alice"),
    _C("item_read_bad_uuid", "GET", "/items/not-a-uuid", user="alice"),
    # ---- create_item
    _C("item_create", "POST", "/items/", user="bob",
       json={"title": "Fresh item", "description": "made by bob"}),
    _C("item_create_title_only", "POST", "/items/", user="carol", json={"title": "Bare"}),
    _C("item_create_missing_title", "POST", "/items/", user="bob",
       json={"description": "no title"}),
    _C("item_create_empty_title", "POST", "/items/", user="bob", json={"title": ""}),
    # ---- update_item
    _C("item_update_own_title_only", "PUT", f"/items/{ALICE_ITEM}", user="alice",
       json={"title": "Renamed"}, note="partial update: description kept"),
    _C("item_update_own_description_only", "PUT", f"/items/{ALICE_ITEM_2}", user="alice",
       json={"description": "new description"}),
    _C("item_update_own_description_null", "PUT", f"/items/{ALICE_ITEM}", user="alice",
       json={"description": None}, note="explicit null clears the field"),
    _C("item_update_own_empty_body", "PUT", f"/items/{ALICE_ITEM}", user="alice", json={}),
    _C("item_update_other_user", "PUT", f"/items/{BOB_ITEM}", user="alice",
       json={"title": "Hijacked"}),
    _C("item_update_superuser_other", "PUT", f"/items/{CAROL_ITEM}", user="admin",
       json={"title": "Admin edit"}),
    _C("item_update_missing", "PUT", f"/items/{MISSING_ID}", user="alice",
       json={"title": "Ghost"}),
    _C("item_update_bad_uuid", "PUT", "/items/12345", user="alice", json={"title": "X"}),
    _C("item_update_empty_title", "PUT", f"/items/{ALICE_ITEM}", user="alice",
       json={"title": ""}),
    # ---- delete_item
    _C("item_delete_own", "DELETE", f"/items/{ALICE_ITEM}", user="alice"),
    _C("item_delete_other_user", "DELETE", f"/items/{BOB_ITEM}", user="alice"),
    _C("item_delete_superuser_other", "DELETE", f"/items/{BOB_ITEM}", user="admin"),
    _C("item_delete_missing", "DELETE", f"/items/{MISSING_ID}", user="alice"),
    _C("item_delete_bad_uuid", "DELETE", "/items/xyz", user="alice"),
    # ---- users
    _C("users_me", "GET", "/users/me", user="alice", tables=("user",)),
    _C("users_me_null_full_name", "GET", "/users/me", user="bob", tables=("user",)),
    _C("users_me_no_token", "GET", "/users/me", tables=("user",)),
    _C("users_me_bad_token", "GET", "/users/me", token=BAD_TOKEN, tables=("user",)),
    _C("users_update_me_full_name", "PATCH", "/users/me", user="alice", tables=("user",),
       json={"full_name": "Alice Renamed"}, note="partial update: email kept"),
    _C("users_update_me_email", "PATCH", "/users/me", user="carol", tables=("user",),
       json={"email": "carol.new@example.com"}),
    _C("users_update_me_email_taken", "PATCH", "/users/me", user="carol", tables=("user",),
       json={"email": _email("alice")}),
    _C("users_update_me_same_email", "PATCH", "/users/me", user="carol", tables=("user",),
       json={"email": _email("carol"), "full_name": None}),
    _C("users_update_me_bad_email", "PATCH", "/users/me", user="carol", tables=("user",),
       json={"email": "not-an-email"}),
    _C("users_update_me_ignores_is_superuser", "PATCH", "/users/me", user="bob",
       tables=("user",), json={"is_superuser": True},
       note="UserUpdateMe has no is_superuser field; it is ignored"),
    _C("users_list_superuser", "GET", "/users/", user="admin", tables=("user",)),
    _C("users_list_superuser_page", "GET", "/users/", user="admin", tables=("user",),
       params={"skip": 1, "limit": 2}),
    _C("users_list_unbounded_limit", "GET", "/users/", user="admin", tables=("user",),
       params={"limit": 1000000}),
    _C("users_list_normal_user", "GET", "/users/", user="alice", tables=("user",)),
]


# ------------------------------------------------------------------------- runner --

# The normaliser is lattice.importer.verify's (kept under its old name for callers).
_Normalizer = Normalizer

template_login = form_login(
    f"{API}/login/access-token", {h: (email, pw) for h, (email, pw, _) in TEMPLATE_USERS.items()}
)


def run_template_suite(
    app_factory: AppFactory,
    db_path: str | Path,
    cases: list[TemplateCase] | None = None,
) -> list[CaseRecord]:
    """Run ``cases`` (default: ``TEMPLATE_SUITE``), each on a fresh copy of ``db_path``.

    ``app_factory(path)`` must return an ASGI app serving the template's API on the
    SQLite file at ``path``; if the app has ``state.engine`` it is disposed afterwards.
    """
    cases = cases or TEMPLATE_SUITE
    records = run_suite(
        app_factory, {"seed": Path(db_path)}, cases, login=template_login, prefix=API
    )
    return [records[c.name] for c in cases]
