"""The Lattice spec model: an application's profile plus its state machines, invariants,
scenarios, endpoint programs and unchecked requirements (direction draft §4).

Every record is a frozen dataclass. Collections are normalised on construction:
sequences (and a Mapping of edges) become tuples, so Machine, Invariant, Unchecked, Step
and Scenario are hashable. JSON payloads (`Step.json`, `.params`, `.expect_body`,
`.expect_db`, `Scenario.given`) are kept as given -- they must stay JSON-serialisable --
and take no part in the hash (they still take part in equality). `Spec.programs` and
`Spec.users` become read-only mappings; a Spec is not hashable (its Profile is not).

Additions to the plan's interface (no deviation from it):
- `Machine.states()`: the states the machine's edges and initial states name.
- `Spec.table(name)`: resolves a schema name ("Order") or SQL table name ("orders") to
  (schema name, SQL table name, Schema); scenarios' `given` and `expect_db` may use
  either spelling.
- `Spec.programs` may also be given as a sequence of (name, source) pairs; a name given
  twice is kept once (the last) and reported by `validate` (S013).
- `Step` fields after `path` have defaults (`expect_status=200`).

Contracts (Phase 2 plan 2026-10-05, §2.4): `Contract` is a route's rule layer with no
code generation -- its auth, the table it reads or writes, its kind, its ownership
scope, its parameters (`ContractParam`) and its error answers (`ContractError`). The
rule oracle (`lattice.spec.probes`) turns contracts and the profile into probes with
predicted answers; layer R of `lattice.spec.check` runs them. No target compiles a
contract (docs/lattice-language-rules.md §6). An unknown (None / "unknown") field is
allowed: the rule item it decides is then `unclear`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from lattice.profile import Profile
from lattice.typesys import Scalar, Schema


def _strings(value: str | Iterable[str]) -> tuple[str, ...]:
    """One name, or a sequence of names, as a tuple."""
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _edges(value: Mapping[str, Any] | Iterable[Iterable[str]]) -> tuple[tuple[str, str], ...]:
    """Edges as (from, to) pairs: from pairs, or from a Mapping from -> to | (to, ...)."""
    if isinstance(value, Mapping):
        return tuple((src, dst) for src, targets in value.items() for dst in _strings(targets))
    return tuple(tuple(edge) for edge in value)  # type: ignore[misc]


@dataclass(frozen=True)
class Machine:
    """A state machine over a one_of column: `edges` are the legal (from, to) moves
    between its values; `initial` the states a new row may start in (empty: any)."""

    table: str
    column: str
    edges: tuple[tuple[str, str], ...]
    initial: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "edges", _edges(self.edges))
        object.__setattr__(self, "initial", _strings(self.initial))

    def states(self) -> tuple[str, ...]:
        """Every state the machine names, in first-mention order (initial first)."""
        named = [*self.initial, *(s for edge in self.edges for s in edge)]
        return tuple(dict.fromkeys(named))


@dataclass(frozen=True)
class Invariant:
    """A rule over the whole database that must hold after every request. `sentence`
    is the plain-language rule the user approves; `expr` the checked form (see
    `lattice.spec.invariants` for the allowed forms)."""

    name: str
    sentence: str
    expr: str


@dataclass(frozen=True)
class Step:
    """One request of a scenario and what it must answer.

    `user` is a handle of `Spec.users` (None: no caller). `expect_body` None: any body.
    `expect_db` (None: not checked) maps a table to one of
    `{"count": n}` (the table has n rows), `{"where": {col: v}, "count": n}` (n rows
    match), or `{"where": {col: v}, "equals": {col: v}}` (every matching row -- and at
    least one -- has these values)."""

    method: str
    path: str
    user: str | None = None
    json: Any = field(default=None, hash=False)
    params: Mapping[str, Any] | None = field(default=None, hash=False)
    expect_status: int = 200
    expect_body: Any | None = field(default=None, hash=False)
    expect_db: Mapping[str, Any] | None = field(default=None, hash=False)


@dataclass(frozen=True)
class Scenario:
    """A concrete example the user approves: rows `given` (table -> rows as dicts of
    column values, one_of columns by value) before `steps` run in order."""

    name: str
    given: Mapping[str, list[dict[str, Any]]] | None = field(default=None, hash=False)
    steps: tuple[Step, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))


@dataclass(frozen=True)
class Unchecked:
    """A prose requirement the spec cannot express yet, reported as not checked.
    `concerns` names what it is attached to: tables ("Order" or "orders"), columns
    ("Order.status"), programs or endpoints."""

    text: str
    concerns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "concerns", _strings(self.concerns))


CONTRACT_KINDS = ("read", "list", "create", "update", "delete", "action")
# "owner_or_admin": the caller's own rows, and -- as the admin role -- anyone's (an
# owner-or-staff object permission: DRF's `request.user.is_staff or obj.user ==
# request.user`; post-hoc round 2026-10-05)
OWNER_SCOPES = ("owner", "owner_or_admin", "any", "unknown")
# the owner scopes naming a column (Phase 2 W3b): "owner(<column>)" -- the rows whose
# <column> is the caller (a link between users read from its other side) -- and
# "owner_or_public(<column>)" -- the owner's rows, and anyone's whose Bool <column> is
# true for reading (writes stay the owner's)
COLUMN_SCOPES = ("owner", "owner_or_public")
_COLUMN_SCOPE = re.compile(r"^(owner|owner_or_public)\(([A-Za-z_][A-Za-z0-9_]*)\)$")


def scope_parts(scope: str | None) -> tuple[str | None, str | None]:
    """An owner scope as (form, column): "owner" -> ("owner", None), "owner(c)" ->
    ("owner", "c"), "owner_or_public(c)" -> ("owner_or_public", "c"), "any" -> ("any",
    None); anything else (malformed) as it is, with no column."""
    if scope is None:
        return None, None
    m = _COLUMN_SCOPE.match(scope)
    return (m.group(1), m.group(2)) if m else (scope, None)


def valid_scope(scope: str) -> bool:
    """Whether ``scope`` is an owner scope: OWNER_SCOPES, or a COLUMN_SCOPES form with
    its column."""
    return scope in OWNER_SCOPES or _COLUMN_SCOPE.match(scope) is not None


PARAM_SOURCES = ("path", "query", "body", "form")
ERROR_TRIGGERS = (
    "unauthenticated",
    "forbidden",
    "inactive",
    "not_found",
    "not_owned",
    "invalid",
    "duplicate",
    "other",
)
EXAMPLE_KEYS = frozenset({"user", "path", "params", "json", "form"})
AUTH_KEYWORDS = ("Public", "Authenticated", "Admin")


@dataclass(frozen=True)
class ContractParam:
    """One parameter of a contract route. `name` is a dotted path into a nested JSON
    body ("items.0.quantity": key `items`, its first element, key `quantity`). Bounds:
    numeric `ge`/`gt`/`le`/`lt`, text `min_len`/`max_len`; `one_of` the accepted wire
    values. `column` ("Table.column") is where the value is stored: its bounds and
    one_of are that column's table items (T3, T4). `ref` names the table whose key the
    value is (probes take a seeded row's key). `example`: a valid value (None: one is
    generated)."""

    name: str
    source: str = "body"
    scalar: Scalar = Scalar.STR
    required: bool = True
    ge: float | None = None
    gt: float | None = None
    le: float | None = None
    lt: float | None = None
    min_len: int | None = None
    max_len: int | None = None
    one_of: tuple[str, ...] | None = None
    column: str | None = None
    ref: str | None = None
    example: Any = field(default=None, hash=False)

    def __post_init__(self) -> None:
        if self.one_of is not None:
            object.__setattr__(self, "one_of", _strings(self.one_of))


@dataclass(frozen=True)
class ContractError:
    """An error answer of a contract route: `status` (None: unknown -- the item is
    unclear), `detail` when the code has a literal one, and `when`, what triggers it
    (ERROR_TRIGGERS). `example` is a request that triggers it -- any of `user` (a
    handle), `path`, `params`, `json`, `form` -- for a trigger the probes cannot make
    on their own (`other`)."""

    status: int | None
    detail: str | None = None
    when: str = "other"
    example: Mapping[str, Any] | None = field(default=None, hash=False)


def _auth(value: Any) -> str | None:
    """None, a keyword, a role name, or "Roles(A, B)"; a sequence of roles becomes
    "Roles(A, B)"."""
    if value is None or isinstance(value, str):
        return value
    return f"Roles({', '.join(value)})"


@dataclass(frozen=True)
class Contract:
    """A route's rule layer (the contract-only route of the direction draft §6.1).

    `auth`: "Public", "Authenticated", "Admin", a role name (that role or the admin
    role, as `@endpoint(auth=<role>)`), "Roles(A, B)" (exactly those roles; a tuple of
    role names is accepted), or None (unknown). `table`: the table the route reads or
    writes (schema or SQL name). `kind`: CONTRACT_KINDS. `owner_scope`: "owner" (the
    route acts on the caller's rows of an owned table only), "owner(<column>)" (on the
    rows whose <column> -- a users key, not the owner column -- is the caller: a link
    between users read from its other side, microblog's followers by `followed_id`),
    "owner_or_public(<column>)" (reads the caller's own rows and anyone's whose Bool
    <column> is true; writes the caller's own only: JustNotes' `isPublic`),
    "owner_or_admin" (a non-admin acts on its own rows only, the admin role on anyone's:
    an owner-or-staff object permission), "any" (on anyone's rows, stated), "unknown",
    or None (the table's ownership decides: "owner"
    for an owned table unless the route is admin-only, else not applicable).
    `response`: a response model of the profile."""

    method: str
    path: str
    auth: str | None
    table: str | None = None
    kind: str | None = None
    owner_scope: str | None = None
    params: tuple[ContractParam, ...] = ()
    errors: tuple[ContractError, ...] = ()
    response: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", str(self.method).upper())
        object.__setattr__(self, "auth", _auth(self.auth))
        object.__setattr__(self, "params", tuple(self.params))
        object.__setattr__(self, "errors", tuple(self.errors))

    @property
    def endpoint(self) -> str:
        """``METHOD /path``."""
        return f"{self.method} {self.path}"


def _programs(value: Mapping[str, str] | Iterable[tuple[str, str]]) -> tuple[dict, tuple]:
    pairs = list(value.items()) if isinstance(value, Mapping) else [tuple(p) for p in value]
    names = [name for name, _ in pairs]
    duplicates = tuple(dict.fromkeys(n for n in names if names.count(n) > 1))
    return dict(pairs), duplicates


def _user(value: Any) -> Any:
    if isinstance(value, str) or not isinstance(value, Iterable):
        return value
    return tuple(value)


@dataclass(frozen=True)
class Spec:
    """An application spec: the profile (data, ownership, roles), endpoint `programs`
    (name -> Lattice source: the executable behaviour), state machines, invariants,
    scenarios, unchecked requirements, `users` (handle -> (login, password)) the
    scenarios sign in as, and `approved`: checker findings the spec states as intended
    (``lattice.spec.Accepted``, e.g. a public aggregate over owned rows), each with the
    reason; the check reports them as accepted. `contracts`: the rule layer of routes
    (`Contract`), which layer R probes."""

    profile: Profile
    programs: Mapping[str, str] = field(default_factory=dict)
    machines: tuple[Machine, ...] = ()
    invariants: tuple[Invariant, ...] = ()
    scenarios: tuple[Scenario, ...] = ()
    unchecked: tuple[Unchecked, ...] = ()
    users: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    approved: tuple[Any, ...] = ()
    contracts: tuple[Contract, ...] = ()
    # program names given more than once (programs given as pairs); see validate S013
    duplicate_programs: tuple[str, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        programs, duplicates = _programs(self.programs)
        object.__setattr__(self, "programs", MappingProxyType(programs))
        object.__setattr__(self, "duplicate_programs", duplicates)
        users = {handle: _user(login) for handle, login in dict(self.users).items()}
        object.__setattr__(self, "users", MappingProxyType(users))
        for name in ("machines", "invariants", "scenarios", "unchecked", "approved", "contracts"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    def table(self, name: str) -> tuple[str, str, Schema] | None:
        """(schema name, SQL table name, Schema) of a table named by its schema name or
        its SQL table name, or None."""
        return resolve_table(self.profile, name)


def tables_of(profile: Profile) -> tuple[Mapping[str, Schema], Mapping[str, str]]:
    """The profile's tables (schema name -> Schema) and SQL names (schema name -> SQL
    name): the API profile's, else the tabular schemas named as themselves."""
    if profile.api is not None:
        return profile.api.tables, profile.api.table_names
    return profile.schemas, {name: name for name in profile.schemas}


def resolve_table(profile: Profile, name: str) -> tuple[str, str, Schema] | None:
    """(schema name, SQL table name, Schema) of `name` (either spelling), or None."""
    tables, sql_names = tables_of(profile)
    if name in tables:
        return name, sql_names.get(name, name), tables[name]
    for schema_name, sql_name in sql_names.items():
        if sql_name == name and schema_name in tables:
            return schema_name, sql_name, tables[schema_name]
    return None
