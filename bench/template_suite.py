"""Request suite for the vendored FastAPI template (items, users, login routes).

``TEMPLATE_SUITE`` is a list of :class:`TemplateCase`. ``run_template_suite(app_factory,
db_path)`` issues each case against a fresh copy of the seeded database (built by
:func:`bench.template_app.seed.build_template_db`) through Starlette's ``TestClient``,
logging in first through ``/login/access-token`` when the case acts as a user. For each
case it records the status, the JSON body and a snapshot of the affected tables *after*
the request, read with plain ``sqlite3`` (raw stored values, rows in ``rowid`` order).

Values generated during the request are replaced by placeholders so two backends can be
compared exactly:

* UUIDs (dashed or 32-hex) not present in the seed database -> ``<uuid:N>``;
* datetimes not present in the seed database -> ``<datetime:N>``;
* JWT-shaped strings -> ``<token>``;
* ``hashed_password`` values -> ``<hash:seed>`` if unchanged from the seed, else
  ``<hash:new>``.

``N`` numbers distinct values in order of first appearance (body first, then tables in
case order), and the same value gets the same number in the body and in the tables
(a dashed UUID in JSON and its 32-hex form in SQLite are the same value).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bench.template_app.seed import TEMPLATE_ITEMS, TEMPLATE_USERS

API = "/api/v1"
MISSING_ID = "00000000-0000-4000-8000-000000000000"
BAD_TOKEN = "not-a-jwt"

AppFactory = Callable[[Path], Any]


@dataclass(frozen=True)
class TemplateCase:
    name: str
    method: str
    path: str
    user: str | None = None  # TEMPLATE_USERS handle to log in as
    token: str | None = None  # raw bearer token (overrides ``user``)
    params: dict[str, Any] | None = None
    json: Any = None
    form: dict[str, str] | None = None
    tables: tuple[str, ...] = ("item",)
    note: str = ""


@dataclass
class CaseRecord:
    name: str
    method: str
    path: str
    status: int
    body: Any
    db: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


# ------------------------------------------------------------------ normalization --

_UUID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_DT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
_JWT_RE = re.compile(r"^eyJ[\w-]*\.[\w-]+\.[\w-]+$")


def _dt_key(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


class _Normalizer:
    def __init__(self, seed_db: Path) -> None:
        self.known_uuids: set[uuid.UUID] = set()
        self.known_dts: set[datetime] = set()
        self.seed_hashes: set[str] = set()
        conn = sqlite3.connect(seed_db)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master "
                                                 "WHERE type='table'")]
            for t in tables:
                cur = conn.execute(f'SELECT * FROM "{t}"')
                cols = [d[0] for d in cur.description]
                for row in cur:
                    for c, v in zip(cols, row, strict=True):
                        if not isinstance(v, str):
                            continue
                        if c == "hashed_password":
                            self.seed_hashes.add(v)
                        elif _UUID_RE.match(v):
                            self.known_uuids.add(uuid.UUID(v))
                        elif _DT_RE.match(v):
                            self.known_dts.add(_dt_key(v))
        finally:
            conn.close()

    def fresh(self) -> _CaseNormalizer:
        return _CaseNormalizer(self)


class _CaseNormalizer:
    def __init__(self, base: _Normalizer) -> None:
        self.base = base
        self.uuids: dict[uuid.UUID, int] = {}
        self.dts: dict[datetime, int] = {}

    def value(self, v: Any, key: str | None = None) -> Any:
        if isinstance(v, dict):
            return {k: self.value(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if not isinstance(v, str):
            return v
        if key == "hashed_password":
            return "<hash:seed>" if v in self.base.seed_hashes else "<hash:new>"
        if _UUID_RE.match(v):
            u = uuid.UUID(v)
            if u in self.base.known_uuids:
                return v
            return f"<uuid:{self.uuids.setdefault(u, len(self.uuids) + 1)}>"
        if _DT_RE.match(v):
            d = _dt_key(v)
            if d in self.base.known_dts:
                return v
            return f"<datetime:{self.dts.setdefault(d, len(self.dts) + 1)}>"
        if _JWT_RE.match(v):
            return "<token>"
        return v


def snapshot_tables(db_path: Path, tables: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    """Raw rows of ``tables`` (in ``rowid`` order) as stored by SQLite."""
    conn = sqlite3.connect(db_path)
    try:
        out: dict[str, list[dict[str, Any]]] = {}
        for t in tables:
            cur = conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')
            cols = [d[0] for d in cur.description]
            out[t] = [dict(zip(cols, row, strict=True)) for row in cur]
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------------- runner --


def _login(client: TestClient, handle: str) -> str:
    email, password, _ = TEMPLATE_USERS[handle]
    r = client.post(f"{API}/login/access-token", data={"username": email, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text}")
    return r.json()["access_token"]


def _body(response: Any) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text


def run_case(
    case: TemplateCase, app_factory: AppFactory, db_path: Path, norm: _Normalizer
) -> CaseRecord:
    with tempfile.TemporaryDirectory(prefix="template-case-") as tmp:
        case_db = Path(tmp) / "case.db"
        shutil.copyfile(db_path, case_db)
        app = app_factory(case_db)
        try:
            with TestClient(app) as client:
                headers = {}
                token = case.token or (_login(client, case.user) if case.user else None)
                if token is not None:
                    headers["Authorization"] = f"Bearer {token}"
                response = client.request(
                    case.method,
                    API + case.path,
                    params=case.params,
                    json=case.json,
                    data=case.form,
                    headers=headers,
                )
        finally:
            engine = getattr(getattr(app, "state", None), "engine", None)
            if engine is not None:
                engine.dispose()
        raw_db = snapshot_tables(case_db, case.tables)
    n = norm.fresh()
    body = n.value(_body(response))
    db = {t: [n.value(row) for row in rows] for t, rows in raw_db.items()}
    return CaseRecord(case.name, case.method, case.path, response.status_code, body, db)


def run_template_suite(
    app_factory: AppFactory,
    db_path: str | Path,
    cases: list[TemplateCase] | None = None,
) -> list[CaseRecord]:
    """Run ``cases`` (default: ``TEMPLATE_SUITE``), each on a fresh copy of ``db_path``.

    ``app_factory(path)`` must return an ASGI app serving the template's API on the
    SQLite file at ``path``; if the app has ``state.engine`` it is disposed afterwards.
    """
    db_path = Path(db_path)
    norm = _Normalizer(db_path)
    return [run_case(c, app_factory, db_path, norm) for c in (cases or TEMPLATE_SUITE)]
