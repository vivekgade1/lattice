"""Request suites and seeded databases for verifying an import (spec 2026-09-27 importer
§3.3).

The input is deliberately plain data, so the extractor's ``RepoModel`` can be adapted to
it:

* :class:`TableSpec` -- one SQL table: its :class:`ColumnSpec` columns (type, nullability,
  key, uniqueness, foreign key, length/value bounds) and its ``owner`` column (the column
  holding the owning user's id), if any.
* :class:`AuthSpec` -- the users table and its email / password-hash / active / admin
  columns, the form login route, and two hooks from the original's security module:
  ``hash_password`` (to seed passwords) and ``mint_token`` (a bearer token for a user id,
  to act as a user that cannot log in -- inactive, or absent from the database).
* :class:`RouteSpec` -- one route: method, full path, ``kind`` (``list``, ``read``,
  ``create``, ``update``, ``delete``, ``me``, ``update_me``, ``login``, ``other``), the
  table its rows come from, ``auth`` (``public`` / ``user`` / ``admin``), its
  :class:`ParamSpec` parameters (path, query or body fields with their bounds), the body
  encoding (``json`` / ``form``), the pagination parameters and the declared errors
  (:class:`ErrorSpec`: status, detail and the ``cause`` that triggers it).

:func:`generate_suite` turns routes + tables + auth into a :class:`GeneratedSuite`:
deterministic :class:`~lattice.importer.verify.Case` lists (named ``<route>.<what>``,
``Case.route`` set, identical requests deduplicated), the :class:`SeedPlan` behind them,
the credentials for :func:`~lattice.importer.verify.form_login` and a ``db_factory`` for
:func:`~lattice.importer.verify.verify`.

The seeded database holds a superuser, two active users and an inactive one; rows of
every owned table spread over all of them; nullable columns both null and set. It is
written through the original app's own models when an ``insert_hook`` is given (see
:func:`sqlmodel_insert_hook`), otherwise with plain SQL from the table specs in the given
storage spellings (:func:`sql_seed`).

**Hypothesis's local constants.** The generated bodies (``hypothesis_bodies``) come from
Hypothesis, which now and then draws a value from its pool of *local constants*: every
short (<= 20 characters) string literal, and every int with |value| >= 100 and every
float, in the source of every non-test, non-site-packages module loaded in the process
(docstrings and f-strings excepted). So the suite -- and the importer's golden outputs
(tests/importer/test_frontend_goldens.py) -- change when a module the import loads gains
or loses such a literal, even one unrelated to the suite. Code on the FastAPI import path
therefore avoids new short literals (keyword ``dict(...)``, ``import X as X`` instead of
``__all__``, spellings the pool already has); a module only another framework needs
(``lattice.importer.wsgi``) is imported lazily.
"""

from __future__ import annotations

import random
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from lattice.importer.transport import json_login
from lattice.importer.verify import Case, Login, form_login

ColumnType = Literal["str", "int", "float", "bool", "uuid", "datetime", "email"]
MISSING_ID = "00000000-0000-4000-8000-000000000000"
MISSING_INT = 999_999  # an integer key no seeded row has
NOT_A_CHOICE = "not-a-choice"
MALFORMED = {"uuid": "not-a-uuid", "int": "not-an-int"}
HUGE_LIMIT = 1_000_000
SEED_EPOCH = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------- the specs --


@dataclass(frozen=True)
class ColumnSpec:
    """One column. Bounds: ``min_len``/``max_len`` for text, ``ge``/``le`` for numbers.
    ``foreign_key`` is ``"table.column"``."""

    name: str
    type: ColumnType
    nullable: bool = False
    primary_key: bool = False
    unique: bool = False
    foreign_key: str | None = None
    min_len: int | None = None
    max_len: int | None = None
    ge: float | None = None
    le: float | None = None
    choices: tuple[str | int, ...] = ()  # an enum column's allowed values
    # how the app stores each of ``choices`` (SQLAlchemy's Enum: the member name), when
    # it differs; () when values are stored as they are. The raw-SQL seed writes these
    stored: tuple[str, ...] = ()
    # the text format the column validates (FieldSpec.format: a slug, a URL...): the seed
    # writes values of it (lattice.importer.formats)
    format: str | None = None


@dataclass(frozen=True)
class TableSpec:
    """One SQL table (``name`` as stored) and its owner column, if rows are owned."""

    name: str
    columns: tuple[ColumnSpec, ...]
    owner: str | None = None

    def column(self, name: str) -> ColumnSpec:
        return next(c for c in self.columns if c.name == name)

    @property
    def key(self) -> ColumnSpec:
        return next(c for c in self.columns if c.primary_key)


@dataclass(frozen=True)
class AuthSpec:
    """How callers are users. ``login_path`` is a form login answering
    ``{token_field: ...}`` (``login_encoding`` ``json``: a JSON login, its token at the
    dotted ``token_field`` path); ``mint_token(user_id)`` makes a bearer token without logging
    in; ``hash_password`` hashes a seed password as the original stores it.
    ``mint_claims(claims)`` (when there is no ``mint_token``) signs arbitrary claims as
    the original's token decode expects them; ``claim`` is the claim naming the caller
    and ``subject_column`` the users column it holds (None: the key)."""

    users_table: str
    email_column: str = "email"
    password_column: str = "hashed_password"
    active_column: str | None = "is_active"
    admin_column: str | None = "is_superuser"
    login_path: str | None = None
    username_field: str = "username"
    password_field: str = "password"
    token_field: str = "access_token"
    login_encoding: str = "form"  # form | json (the front end's AuthModel)
    hash_password: Callable[[str], str] | None = None
    mint_token: Callable[[Any], str] | None = None
    mint_claims: Callable[[Mapping[str, Any]], str] | None = None
    claim: str = "sub"
    subject_column: str | None = None
    bad_token: str = "not-a-jwt"
    key_type: ColumnType = "uuid"  # the users key: "uuid", or "int" (1, 2, ... in the seed)
    # an enum role column (RoleColumn(values=...)): (value as the seed writes it, role
    # name) per role, in the enum's order; the seed gives every role a user
    role_column: str | None = None
    role_values: tuple[tuple[str, str], ...] = ()
    admin_role: str = "Admin"

    def role_users(self) -> tuple[tuple[str, str, str], ...]:
        """(handle, role value, role name) of the seed's users by role: ``admin`` holds
        the admin role, ``alice``/``bob``/``carol`` the first other role, and one
        ``role_<name>`` user each further role."""
        if self.role_column is None:
            return ()
        admin = next((v for v, n in self.role_values if n == self.admin_role), None)
        others = [(v, n) for v, n in self.role_values if n != self.admin_role]
        out: list[tuple[str, str, str]] = []
        if admin is not None:
            out.append((ADMIN, admin, self.admin_role))
        if others:
            out += [(h, others[0][0], others[0][1]) for h in (ACTOR, OTHER, INACTIVE)]
            out += [(f"role_{n.lower()}", v, n) for v, n in others[1:]]
        return tuple(out)

    @property
    def missing_user(self) -> Any:
        """A users key no seeded user has."""
        return MISSING_INT if self.key_type == "int" else uuid.UUID(MISSING_ID)


@dataclass(frozen=True)
class ParamSpec:
    """A route parameter: ``source`` is ``path``, ``query`` or ``body`` (a JSON or form
    field, per the route). ``column`` is the table column a body field writes (for
    ``unique`` errors). Bounds as in :class:`ColumnSpec`."""

    name: str
    type: ColumnType
    source: Literal["path", "query", "body"]
    required: bool = True
    nullable: bool = False
    min_len: int | None = None
    max_len: int | None = None
    ge: float | None = None
    le: float | None = None
    default: Any = None
    column: str | None = None
    choices: tuple[str | int, ...] = ()  # an enum parameter's allowed values


ErrorCause = Literal[
    "not_found", "not_owner", "not_admin", "unique", "bad_credentials", "inactive_login", "auth"
]


@dataclass(frozen=True)
class ErrorSpec:
    """A declared error and what triggers it: ``not_found`` (a missing id), ``not_owner``
    (another user's row), ``not_admin`` (a normal user on an admin route), ``unique``
    (``column`` set to another row's value), ``bad_credentials`` / ``inactive_login``
    (login), ``auth`` (a bad or unusable token)."""

    status: int
    detail: str | None
    cause: ErrorCause
    column: str | None = None


RouteKind = Literal[
    "list", "read", "create", "update", "delete", "me", "update_me", "login", "other"
]


@dataclass(frozen=True)
class RouteSpec:
    """One route of the original. ``path`` is the full path with ``{param}`` holes;
    ``tables`` are snapshotted after each request (default: ``table``)."""

    name: str
    method: str
    path: str
    kind: RouteKind
    table: str | None
    auth: Literal["public", "user", "admin", "roles"] = "user"
    params: tuple[ParamSpec, ...] = ()
    body: Literal["json", "form"] = "json"
    pagination: tuple[str, str] | None = None
    errors: tuple[ErrorSpec, ...] = ()
    tables: tuple[str, ...] = ()
    # auth="roles": the role names that may call it (auth=Roles(...)); the suite calls
    # it as a user of every role (AuthSpec.role_values)
    roles: tuple[str, ...] | None = None

    def of(self, source: str) -> list[ParamSpec]:
        return [p for p in self.params if p.source == source]

    @property
    def id_param(self) -> ParamSpec | None:
        return next(iter(self.of("path")), None)

    @property
    def snapshot(self) -> tuple[str, ...]:
        return self.tables or ((self.table,) if self.table else ())


# ------------------------------------------------------------------------------ the seed --


@dataclass(frozen=True)
class SeedUser:
    handle: str
    email: str
    password: str
    active: bool
    admin: bool
    id: Any  # uuid.UUID, or an int for an integer users key
    role: str | None = None  # the role column's value (AuthSpec.role_column)


@dataclass
class SeedPlan:
    """The seed as Python values (``uuid.UUID``, aware ``datetime``, ``None``...): the
    users, and every table's rows in insertion order."""

    users: tuple[SeedUser, ...]
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    owner_columns: dict[str, str] = field(default_factory=dict)

    def user(self, handle: str) -> SeedUser:
        return next(u for u in self.users if u.handle == handle)

    def owned(self, table: str, handle: str) -> list[dict[str, Any]]:
        col = self.owner_columns[table]
        uid = self.user(handle).id
        return [r for r in self.rows[table] if r[col] == uid]


# handle, admin, active
SEED_USERS: tuple[tuple[str, bool, bool], ...] = (
    ("admin", True, True),
    ("alice", False, True),
    ("bob", False, True),
    ("carol", False, False),
)
ACTOR, OTHER, ADMIN, INACTIVE = "alice", "bob", "admin", "carol"


def _text(col: ColumnSpec | ParamSpec, stem: str) -> str:
    lo, hi = col.min_len or 0, col.max_len
    s = stem if hi is None else stem[:hi]
    return s + "x" * max(0, lo - len(s))


def _number(col: ColumnSpec | ParamSpec, i: int) -> float:
    lo = col.ge if col.ge is not None else 0
    hi = col.le if col.le is not None else lo + 1000
    return lo + i % int(hi - lo + 1)


def seed_plan(
    tables: Sequence[TableSpec], auth: AuthSpec, *, seed: int = 31, rows_per_table: int = 8
) -> SeedPlan:
    """The seed: :data:`SEED_USERS` in the users table, ``rows_per_table`` rows in every
    other table (owned rows round-robin over all users), nullable columns null on every
    third row, distinct datetimes in a shuffled order. A pure function of ``seed``."""
    rng = random.Random(seed)
    ns = uuid.uuid5(uuid.NAMESPACE_URL, f"https://lattice.invalid/importer-seed/{seed}")
    roles = {h: v for h, v, _ in auth.role_users()}
    people = [*SEED_USERS, *((h, False, True) for h in roles if h not in dict.fromkeys(
        u[0] for u in SEED_USERS))]  # fmt: skip
    users = tuple(
        SeedUser(h, f"{h}@example.com", f"{h}-pass-{seed:04d}", active, admin,
                 k + 1 if auth.key_type == "int" else uuid.uuid5(ns, f"user/{h}"),
                 roles.get(h))
        for k, (h, admin, active) in enumerate(people)
    )  # fmt: skip
    plan = SeedPlan(users, owner_columns={t.name: t.owner for t in tables if t.owner})
    by_name = {t.name: t for t in tables}
    for t in tables:
        n = len(users) if t.name == auth.users_table else rows_per_table
        if any(c.unique and c.foreign_key and c.foreign_key.split(".")[0] == auth.users_table
               for c in t.columns):  # fmt: skip
            n = min(n, len(users))  # one row per user at most (a unique users foreign key)
        hours = list(range(n))
        rng.shuffle(hours)
        rows = []
        for i in range(n):
            u = users[i % len(users)]
            row: dict[str, Any] = {}
            for c in t.columns:
                row[c.name] = _seed_value(t, c, i, u, hours[i], ns, auth, by_name, users)
            rows.append(row)
        plan.rows[t.name] = rows
    return plan


def _seed_value(
    t: TableSpec,
    c: ColumnSpec,
    i: int,
    u: SeedUser,
    hour: int,
    ns: uuid.UUID,
    auth: AuthSpec,
    tables: Mapping[str, TableSpec],
    users: Sequence[SeedUser],
) -> Any:
    is_users = t.name == auth.users_table
    if is_users:
        if c.name == auth.email_column:
            return u.email
        if c.name == auth.password_column:
            return auth.hash_password(u.password) if auth.hash_password else u.password
        if c.name == auth.active_column:
            return u.active
        if c.name == auth.admin_column:
            return u.admin
        if c.name == auth.role_column and u.role is not None:
            return u.role
        if c.primary_key:
            return u.id
    if c.primary_key:
        return uuid.uuid5(ns, f"{t.name}/{i}") if c.type == "uuid" else i + 1
    if c.name == t.owner or (c.foreign_key and c.foreign_key.split(".")[0] == auth.users_table):
        return u.id
    if c.foreign_key:
        target = tables.get(c.foreign_key.split(".")[0])
        if target is not None and target.key.type == "uuid":
            return uuid.uuid5(ns, f"{target.name}/{i % 2}")
        return 1 + i % 2
    if c.nullable and i % 3 == 2:
        return None
    if c.choices:
        return c.choices[i % len(c.choices)]
    if c.type == "datetime":
        return SEED_EPOCH + timedelta(days=30 * (t.name != auth.users_table), hours=hour)
    if c.type == "bool":
        return i % 2 == 0
    if c.type in ("int", "float"):
        v = _number(c, i)
        return int(v) if c.type == "int" else float(v)
    if c.type == "uuid":
        return uuid.uuid5(ns, f"{t.name}/{c.name}/{i}")
    if c.type == "email":
        return f"{t.name}.{i}@example.com"
    if c.format:  # a slug, a URL...: a value the field (and a router) accepts
        from lattice.importer.formats import seed_value

        return seed_value(c.format, f"{c.name.replace('_', ' ').title()} {i + 1:02d}", i,
                          c.max_len)  # fmt: skip
    return _text(c, f"{c.name.replace('_', ' ').title()} {i + 1:02d}")


def _stored(value: Any, uuid_storage: str, datetime_storage: str) -> Any:
    if isinstance(value, uuid.UUID):
        return value.hex if uuid_storage == "hex32" else str(value)
    if isinstance(value, datetime):
        v = value.astimezone(UTC)
        if datetime_storage == "sql_space":
            return v.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
        return v.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"
    if isinstance(value, bool):
        return int(value)
    return value


@dataclass(frozen=True)
class FkOrder:
    """The order to insert tables in (every table after the tables its foreign keys
    reference), the ``deferred`` foreign keys (table, column) breaking a cycle -- each
    nullable: inserted NULL, set once every row is in -- and the ``cycle`` no nullable
    foreign key breaks (its tables, sorted; None when there is none): those follow in
    declaration order, and the caller reports it."""

    order: list[str]
    deferred: list[tuple[str, str]]
    cycle: list[str] | None = None


def fk_order(tables: Sequence[TableSpec]) -> FkOrder:
    """:class:`FkOrder` of ``tables`` (a self-reference never blocks; a foreign key to a
    table not among ``tables`` is ignored). Stable: declaration order among tables free
    to go."""
    names = [t.name for t in tables]
    edges: dict[str, dict[str, tuple[str, bool]]] = {n: {} for n in names}  # t -> col -> dep
    for t in tables:
        for c in t.columns:
            target = c.foreign_key.split(".")[0] if c.foreign_key else None
            if target in edges and target != t.name:
                edges[t.name][c.name] = (target, c.nullable)  # type: ignore[index]
    order: list[str] = []
    deferred: list[tuple[str, str]] = []
    left = list(names)
    while left:
        free = [n for n in left if all(dep in order for dep, _ in edges[n].values())]
        if free:
            order.append(free[0])
            left.remove(free[0])
            continue
        # a cycle: drop one nullable foreign key among the tables left (the first one)
        cut = next(((n, col) for n in left for col, (dep, null) in edges[n].items()
                    if null and dep in left), None)  # fmt: skip
        if cut is None:
            return FkOrder(order + left, deferred, sorted(left))
        deferred.append(cut)
        del edges[cut[0]][cut[1]]
    return FkOrder(order, deferred)


_SQL_TYPE = {"int": "INTEGER", "bool": "BOOLEAN", "float": "REAL"}


def sql_seed(
    path: str | Path,
    tables: Sequence[TableSpec],
    plan: SeedPlan,
    *,
    uuid_storage: str = "hex32",
    datetime_storage: str = "sql_space",
    schema_hook: Callable[[Path], None] | None = None,
) -> Path:
    """Write ``plan`` to a new SQLite file with plain SQL. ``schema_hook(path)`` creates
    the tables (e.g. the original's metadata); otherwise ``CREATE TABLE`` from the specs.
    Values are stored in the given spellings (``hex32``/``dashed``; ``sql_space``/
    ``iso_z``)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    if schema_hook is not None:
        schema_hook(path)
    conn = sqlite3.connect(path)
    try:
        for t in tables:
            cols = ", ".join(
                f'"{c.name}" {_SQL_TYPE.get(c.type, "TEXT")}'
                + (" PRIMARY KEY" if c.primary_key else "")
                + ("" if c.nullable or c.primary_key else " NOT NULL")
                for c in t.columns
            )
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{t.name}" ({cols})')
            names = [c.name for c in t.columns]
            marks = ", ".join("?" for _ in names)
            quoted = ", ".join(f'"{n}"' for n in names)
            # an enum column in the app's stored spelling (a value the plan holds as
            # one of ``choices``; one already stored as spelled stays as it is)
            spell = {c.name: dict(zip(c.choices, c.stored, strict=True))
                     for c in t.columns
                     if c.stored and len(c.stored) == len(c.choices)}  # fmt: skip
            conn.executemany(
                f'INSERT INTO "{t.name}" ({quoted}) VALUES ({marks})',
                [
                    tuple(
                        _stored(
                            spell[n].get(r[n], r[n]) if n in spell else r[n],
                            uuid_storage,
                            datetime_storage,
                        )
                        for n in names
                    )  # fmt: skip
                    for r in plan.rows[t.name]
                ],
            )
        conn.commit()
    finally:
        conn.close()
    return path


def sqlmodel_insert_hook(
    models: Mapping[str, type], engine_factory: Callable[[Path], Any]
) -> Callable[[Path, SeedPlan], None]:
    """An ``insert_hook`` writing the seed through the original's SQLModel classes (table
    name -> model), so the original's own column types choose the storage spellings.

    A column the plan leaves null but the model fills on insert (a ``default_factory``
    such as ``created_at``) is set back to NULL afterwards, so nullable columns really
    are both null and set."""

    def hook(path: Path, plan: SeedPlan) -> None:
        from sqlalchemy import update
        from sqlmodel import Session, SQLModel

        engine = engine_factory(path)
        try:
            # every model's own MetaData (an app may keep its tables in its own registry)
            metadatas = {id(m.metadata): m.metadata for m in models.values()  # type: ignore[attr-defined]
                         if getattr(m, "metadata", None) is not None}  # fmt: skip
            for metadata in metadatas.values() or [SQLModel.metadata]:
                metadata.create_all(engine)
            for table, model in models.items():
                with Session(engine) as session:
                    for row in plan.rows[table]:
                        session.add(model(**_members(model, row)))
                    session.commit()
                    key = next(c for c in model.__table__.primary_key.columns)  # type: ignore[attr-defined]
                    for row in plan.rows[table]:
                        nulls = {k: None for k, v in row.items() if v is None}
                        if nulls:
                            session.execute(
                                update(model).where(key == row[key.name]).values(**nulls)
                            )
                    session.commit()
        finally:
            engine.dispose()

    return hook


def _members(model: type, row: Mapping[str, Any]) -> dict[str, Any]:
    """``row`` with every value of an Enum-typed field as that Enum's member (a table
    model does not validate, and SQLAlchemy's Enum type wants members)."""
    import enum
    import types
    import typing

    fields = getattr(model, "model_fields", {})
    out = dict(row)
    for name, value in row.items():
        info = fields.get(name)
        if value is None or info is None:
            continue
        ann = info.annotation
        if typing.get_origin(ann) in (typing.Union, types.UnionType):
            ann = next((a for a in typing.get_args(ann) if a is not type(None)), ann)
        if isinstance(ann, type) and issubclass(ann, enum.Enum) and not isinstance(value, ann):
            try:
                out[name] = ann(value)
            except ValueError:
                pass
    return out


# ----------------------------------------------------------------------------- the suite --


@dataclass
class GeneratedSuite:
    cases: list[Case]
    plan: SeedPlan
    credentials: dict[str, tuple[str, str]]
    db_factory: Callable[[str, Path], Path]
    login: Login | None

    def by_route(self) -> dict[str, list[Case]]:
        out: dict[str, list[Case]] = {}
        for c in self.cases:
            out.setdefault(c.route or "", []).append(c)
        return out


def _email_of_len(n: int, stem: str = "a") -> str:
    """An address exactly ``n`` characters long (local part <= 64, labels <= 63)."""
    tail = "@example.com"
    local = (stem * 64)[: max(1, min(64, n - len(tail)))]
    rest = n - len(local) - len(tail)
    labels = []
    while rest > 0:
        k = min(63, rest - 1) if rest > 1 else 0
        if k <= 0:  # one character left: lengthen the local part instead
            local = local + stem if len(local) < 64 else local
            rest -= 1
            continue
        labels.append("d" * k)
        rest -= k + 1
    domain = ".".join([*labels, "example.com"])
    return f"{local}@{domain}"


def _valid(p: ParamSpec, route: RouteSpec, i: int = 0) -> Any:
    if p.choices:
        return p.choices[i % len(p.choices)]
    if p.type == "email":
        return f"fresh.{route.name.replace('_', '.')}{i or ''}@example.com"
    if p.type == "str":
        return _text(p, f"Valid {p.name.replace('_', ' ')} {i}".strip())
    if p.type == "int":
        return int(_number(p, 1))
    if p.type == "float":
        return float(_number(p, 1))
    if p.type == "bool":
        return True
    if p.type == "uuid":
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"valid/{p.name}"))
    if p.type == "datetime":
        return "2026-06-01T12:00:00"
    raise ValueError(p.type)


def _boundaries(p: ParamSpec) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if p.choices:
        # a value outside the enum: the original refuses it (422)
        other = NOT_A_CHOICE if p.type == "str" else max(int(c) for c in p.choices) + 1
        return [("not_a_choice", other)]
    if p.type in ("str", "email"):
        make = _email_of_len if p.type == "email" else (lambda n: "b" * n)
        if p.min_len:
            out += [("min", make(p.min_len)), ("below_min", make(p.min_len - 1))]
        if p.max_len is not None:
            out += [("max", make(p.max_len)), ("above_max", make(p.max_len + 1))]
    elif p.type in ("int", "float"):
        cast = int if p.type == "int" else float
        if p.ge is not None:
            out += [("min", cast(p.ge)), ("below_min", cast(p.ge) - 1)]
        if p.le is not None:
            out += [("max", cast(p.le)), ("above_max", cast(p.le) + 1)]
    return out


def _extra_value(c: ColumnSpec, plan: SeedPlan) -> Any:
    if c.type == "uuid":
        return (
            str(plan.user(OTHER).id)
            if c.foreign_key
            else str(uuid.uuid5(uuid.NAMESPACE_URL, "extra/" + c.name))
        )
    if c.type == "datetime":
        return "2030-01-01T00:00:00"
    if c.type == "bool":
        return True
    if c.type in ("int", "float"):
        return 3
    if c.type == "email":
        return "extra@example.com"
    return "extra"


class _Builder:
    def __init__(self, route: RouteSpec, plan: SeedPlan, tables: Mapping[str, TableSpec],
                 auth: AuthSpec) -> None:  # fmt: skip
        self.route, self.plan, self.tables, self.auth = route, plan, tables, auth
        self.cases: list[Case] = []
        self._seen: set[str] = set()

    # -- requests
    def own_id(self, handle: str) -> str | None:
        r = self.route
        if r.table is None or r.id_param is None:
            return None
        t = self.tables[r.table]
        if t.owner:
            return str(self.plan.owned(r.table, handle)[0][t.key.name])
        if r.table == self.auth.users_table:
            return str(self.plan.user(handle).id)
        return str(self.plan.rows[r.table][0][t.key.name])

    def other_id(self) -> str | None:
        return self.own_id(OTHER)

    def missing_id(self) -> str:
        p = self.route.id_param
        return str(MISSING_INT) if p is not None and p.type == "int" else MISSING_ID

    def path(self, id_value: str | None) -> str:
        p = self.route.id_param
        if p is None:
            return self.route.path
        return self.route.path.replace("{" + p.name + "}", id_value or self.missing_id())

    def happy_actor(self) -> str | None:
        if self.route.auth == "roles":
            allowed = set(self.route.roles or ())
            return next((h for h, _, n in self.auth.role_users() if n in allowed), ACTOR)
        return None if self.route.auth == "public" else (
            ADMIN if self.route.auth == "admin" else ACTOR)  # fmt: skip

    def happy_body(self) -> dict[str, Any] | None:
        r = self.route
        body = r.of("body")
        if not body:
            return None
        if r.kind == "login":
            u = self.plan.user(ACTOR)
            names = [p.name for p in body]
            return {names[0]: u.email, names[1]: u.password}
        return {p.name: _valid(p, r) for p in body}

    def add(self, what: str, *, user: str | None = "<happy>", token: str | None = None,
            id_value: str | None = "<own>", body: Any = "<happy>",
            params: dict[str, Any] | None = None, note: str = "") -> None:  # fmt: skip
        r = self.route
        if user == "<happy>":
            user = self.happy_actor()
        if id_value == "<own>":
            id_value = self.own_id(user or ACTOR)
        if body == "<happy>":
            body = self.happy_body()
        if body is not None and r.body == "form":
            body = {k: str(v) for k, v in body.items()}
        case = Case(
            name=f"{r.name}.{what}",
            method=r.method,
            path=self.path(id_value),
            user=user if token is None else None,
            token=token,
            params=params,
            json=body if r.body == "json" else None,
            form=body if r.body == "form" else None,
            tables=r.snapshot,
            note=note,
            route=r.name,
        )
        key = repr((case.method, case.path, case.user, case.token, case.params, case.json,
                    case.form))  # fmt: skip
        if key in self._seen:
            return
        self._seen.add(key)
        self.cases.append(case)


def _route_cases(b: _Builder, hypothesis_bodies: int, seed: int) -> None:
    r, plan, auth = b.route, b.plan, b.auth
    b.add("happy")
    idp = r.id_param
    owned = r.table is not None and b.tables[r.table].owner is not None
    if idp is not None and owned:
        b.add("other_user_row", user=ACTOR, id_value=b.other_id())
    if r.auth != "public":
        b.add("admin", user=ADMIN, id_value=b.other_id() if idp is not None else "<own>")
        if r.kind in ("me", "update_me", "list", "read"):
            b.add("other_user", user=OTHER)
        # a user of every role (RoleColumn(values=...)): a wrong role set cannot verify
        for handle, _, name in auth.role_users():
            if handle != INACTIVE:
                b.add(f"role.{name}.{handle}", user=handle)
    if idp is not None:
        b.add("missing_id", id_value=b.missing_id())
        b.add("malformed_id", id_value=MALFORMED.get(idp.type, "%%"))
    happy = b.happy_body()
    # boundaries of query and body parameters
    for p in r.params:
        if p.source == "path":
            continue
        for label, value in _boundaries(p):
            if p.source == "query":
                b.add(f"boundary.{p.name}.{label}", params={p.name: value})
            else:
                b.add(f"boundary.{p.name}.{label}", body={**(happy or {}), p.name: value})
    body_params = r.of("body")
    if body_params and r.kind != "login":
        for p in body_params:
            if p.required:
                b.add(f"missing.{p.name}",
                      body={k: v for k, v in (happy or {}).items() if k != p.name})  # fmt: skip
        if r.body == "json":
            b.add("extra.unknown", body={**(happy or {}), "unexpected_field": "x"})
            if r.table is not None:
                names = {p.name for p in r.params}
                extra = {c.name: _extra_value(c, plan)
                         for c in b.tables[r.table].columns if c.name not in names}  # fmt: skip
                b.add(
                    "extra.columns",
                    body={**(happy or {}), **extra},
                    note="columns the route does not take: ignored or refused, never written",
                )
    if r.kind in ("update", "update_me"):
        for p in body_params:
            b.add(f"partial.{p.name}", body={p.name: _valid(p, r, 1)})
            if p.nullable:
                b.add(f"partial.{p.name}_null", body={p.name: None})
        b.add("partial.empty", body={})
    if r.pagination is not None:
        off, lim = r.pagination
        n = len(plan.rows.get(r.table or "", []))
        for label, params in (
            ("limit_1", {lim: 1}),
            ("window", {off: 2, lim: 3}),
            ("limit_0", {lim: 0}),
            ("skip_past_end", {off: n + 10}),
            ("limit_huge", {lim: HUGE_LIMIT}),
            ("skip_negative", {off: -1}),
            ("limit_negative", {lim: -1}),
            ("limit_malformed", {lim: "many"}),
        ):
            b.add(f"page.{label}", params=params)
            if r.auth == "user":
                b.add(f"page.{label}.admin", user=ADMIN, params=params)
    if r.kind == "login":
        names = [p.name for p in body_params]
        for p in body_params:
            b.add(f"missing.{p.name}",
                  body={k: v for k, v in (happy or {}).items() if k != p.name})  # fmt: skip
        admin = plan.user(ADMIN)
        b.add("admin", body={names[0]: admin.email, names[1]: admin.password})
        b.add("unknown_user", body={names[0]: "nobody@example.com", names[1]: "whatever123"})
    if r.auth != "public":
        b.add("no_token", user=None)
        b.add("bad_token", token=auth.bad_token)
        if auth.mint_token is not None:
            b.add("inactive_token", token=auth.mint_token(plan.user(INACTIVE).id),
                  id_value=b.own_id(INACTIVE) if idp is not None else None)  # fmt: skip
            b.add("unknown_user_token", token=auth.mint_token(auth.missing_user))
        elif auth.mint_claims is not None:
            _minted_cases(b, auth.mint_claims, idp)
    for e in r.errors:
        _error_case(b, e, happy)
    if hypothesis_bodies and body_params and r.kind != "login":
        for k, body in enumerate(hypothesis_bodies_for(r, hypothesis_bodies, seed)):
            b.add(f"hyp.{k}", body=body)


MISSING_SUBJECT = "no-such-user"  # a login name no seeded user has


def _minted_cases(b: _Builder, mint: Callable[[Mapping[str, Any]], str], idp: Any) -> None:
    """The token cases a single-argument mint would give, from signed claims: a valid
    token naming no user, one without the claim, and (with an active column) one naming
    the inactive user."""
    auth, plan = b.auth, b.plan

    def subject(u: SeedUser | None) -> Any:
        if auth.subject_column is None:
            return str(u.id if u is not None else auth.missing_user)
        if auth.subject_column == auth.email_column:
            return u.email if u is not None else MISSING_SUBJECT
        return None

    if auth.active_column is not None and (value := subject(plan.user(INACTIVE))) is not None:
        b.add("inactive_token", token=mint({auth.claim: value}),
              id_value=b.own_id(INACTIVE) if idp is not None else None)  # fmt: skip
    if (value := subject(None)) is not None:
        b.add("unknown_user_token", token=mint({auth.claim: value}))
    b.add("claimless_token", token=mint({}))


def _error_case(b: _Builder, e: ErrorSpec, happy: dict[str, Any] | None) -> None:
    r, plan = b.route, b.plan
    what = f"error.{e.status}.{e.cause}"
    if e.cause == "not_found":
        b.add(what, id_value=b.missing_id())
    elif e.cause == "not_owner":
        b.add(what, user=ACTOR, id_value=b.other_id())
    elif e.cause == "not_admin":
        b.add(what, user=ACTOR)
    elif e.cause == "unique":
        p = next(p for p in r.of("body") if (p.column or p.name) == e.column)
        column = e.column or p.name
        taken = plan.user(OTHER).email if p.type == "email" else None
        if taken is None and r.table is not None:
            taken = plan.rows[r.table][-1][column]
        if taken is None and column == b.auth.email_column:
            taken = plan.user(OTHER).email  # the users' login column
        if taken is None:  # the table that has the column (the route's is not known)
            rows = next((rs for rs in plan.rows.values() if rs and column in rs[-1]), None)
            taken = rows[-1][column] if rows else None
        b.add(what, user=ACTOR, body={**(happy or {}), p.name: taken})
    elif e.cause in ("bad_credentials", "inactive_login"):
        names = [p.name for p in r.of("body")]
        u = plan.user(ACTOR if e.cause == "bad_credentials" else INACTIVE)
        password = "wrong-password" if e.cause == "bad_credentials" else u.password
        b.add(what, body={names[0]: u.email, names[1]: password})
    elif e.cause == "auth":
        b.add(what, token=b.auth.bad_token)


def hypothesis_bodies_for(route: RouteSpec, n: int, seed: int) -> list[dict[str, Any]]:
    """``n`` request bodies within the declared bounds, drawn by hypothesis with a fixed
    seed (deterministic). Required fields are always present; optional ones may be absent
    and nullable ones null."""
    from hypothesis import HealthCheck, Phase, given, settings
    from hypothesis import seed as hseed
    from hypothesis import strategies as st

    def field_strategy(p: ParamSpec) -> st.SearchStrategy[Any]:
        if p.choices:
            s: st.SearchStrategy[Any] = st.sampled_from(p.choices)
        elif p.type == "str":
            s = st.text(
                st.characters(codec="utf-8", exclude_categories=("Cs",)),
                min_size=p.min_len or 0,
                max_size=p.max_len if p.max_len is not None else 64,
            )
        elif p.type == "email":
            s = st.from_regex(r"\A[a-z][a-z0-9]{0,15}\Z").map(lambda x: f"{x}@example.com")
        elif p.type == "int":
            s = st.integers(
                int(p.ge) if p.ge is not None else -(10**6),
                int(p.le) if p.le is not None else 10**6,
            )
        elif p.type == "float":
            s = st.floats(p.ge, p.le, allow_nan=False, allow_infinity=False)
        elif p.type == "bool":
            s = st.booleans()
        elif p.type == "uuid":
            s = st.uuids().map(str)
        else:
            s = st.datetimes(datetime(2000, 1, 1), datetime(2100, 1, 1)).map(
                lambda d: d.isoformat()
            )
        return st.one_of(st.none(), s) if p.nullable else s

    fields = route.of("body")
    required = {p.name: field_strategy(p) for p in fields if p.required}
    optional = {p.name: field_strategy(p) for p in fields if not p.required}
    strategy = st.fixed_dictionaries(required, optional=optional)
    out: list[dict[str, Any]] = []

    @hseed(seed)
    @settings(
        max_examples=n,
        database=None,
        phases=[Phase.generate],
        suppress_health_check=list(HealthCheck),
        deadline=None,
    )
    @given(strategy)
    def collect(body: dict[str, Any]) -> None:
        if len(out) < n and body not in out:
            out.append(body)

    with _no_local_constants():
        collect()
    return out


@contextmanager
def _no_local_constants() -> Iterator[None]:
    """Hypothesis (>= 6.13x) also draws literals mined from every loaded local module, so
    its examples change whenever any code in the process changes. Turn that off while
    drawing bodies: the suite then depends on ``seed`` and the route specs alone."""
    try:
        from hypothesis.internal.conjecture import providers
    except ImportError:  # pragma: no cover -- an older hypothesis has no constant pool
        yield
        return
    original = getattr(providers, "_get_local_constants", None)
    if original is None:  # pragma: no cover
        yield
        return
    empty = type(providers._local_constants)(
        integers=type(providers._local_constants.integers)(),
        floats=type(providers._local_constants.floats)(key=providers.float_to_int),
        bytes=type(providers._local_constants.bytes)(),
        strings=type(providers._local_constants.strings)(),
    )
    providers._get_local_constants = lambda: empty
    try:
        yield
    finally:
        providers._get_local_constants = original


def generate_suite(
    routes: Sequence[RouteSpec],
    tables: Sequence[TableSpec],
    auth: AuthSpec,
    *,
    seed: int = 31,
    rows_per_table: int = 8,
    hypothesis_bodies: int = 5,
    insert_hook: Callable[[Path, SeedPlan], None] | None = None,
    schema_hook: Callable[[Path], None] | None = None,
    uuid_storage: str = "hex32",
    datetime_storage: str = "sql_space",
) -> GeneratedSuite:
    """The request suite and seeded database for ``routes`` (see the module docstring)."""
    plan = seed_plan(tables, auth, seed=seed, rows_per_table=rows_per_table)
    by_name = {t.name: t for t in tables}
    cases: list[Case] = []
    for r in routes:
        b = _Builder(r, plan, by_name, auth)
        _route_cases(b, hypothesis_bodies, seed)
        cases += b.cases

    def db_factory(variant: str, path: Path) -> Path:
        if variant != "seed":
            raise ValueError(f"unknown database variant {variant!r}")
        if insert_hook is not None:
            if path.exists():
                path.unlink()
            insert_hook(path, plan)
            return path
        return sql_seed(path, tables, plan, uuid_storage=uuid_storage,
                        datetime_storage=datetime_storage, schema_hook=schema_hook)  # fmt: skip

    credentials = {u.handle: (u.email, u.password) for u in plan.users}
    login = None
    if auth.login_path is not None and auth.login_encoding == "json":
        login = json_login(
            auth.login_path,
            credentials,
            username_field=auth.username_field,
            password_field=auth.password_field,
            token_path=tuple(auth.token_field.split(".")),
        )
    elif auth.login_path is not None:
        login = form_login(
            auth.login_path,
            credentials,
            username_field=auth.username_field,
            password_field=auth.password_field,
            token_field=auth.token_field,
        )
    return GeneratedSuite(cases, plan, credentials, db_factory, login)
