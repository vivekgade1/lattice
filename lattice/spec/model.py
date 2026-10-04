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
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from lattice.profile import Profile
from lattice.typesys import Schema


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
    reason; the check reports them as accepted."""

    profile: Profile
    programs: Mapping[str, str] = field(default_factory=dict)
    machines: tuple[Machine, ...] = ()
    invariants: tuple[Invariant, ...] = ()
    scenarios: tuple[Scenario, ...] = ()
    unchecked: tuple[Unchecked, ...] = ()
    users: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    approved: tuple[Any, ...] = ()
    # program names given more than once (programs given as pairs); see validate S013
    duplicate_programs: tuple[str, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        programs, duplicates = _programs(self.programs)
        object.__setattr__(self, "programs", MappingProxyType(programs))
        object.__setattr__(self, "duplicate_programs", duplicates)
        users = {handle: _user(login) for handle, login in dict(self.users).items()}
        object.__setattr__(self, "users", MappingProxyType(users))
        for name in ("machines", "invariants", "scenarios", "unchecked", "approved"):
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
