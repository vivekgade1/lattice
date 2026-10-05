"""Spec validation: `validate(spec)` lists every problem that makes a spec inconsistent
with its own profile, each with a code, a message and a hint saying how to fix it.

Codes:

- S001: the profile has no API profile (`Profile.api`): a spec needs its tables, roles
  and users table. Nothing else is checked.
- S002: an unknown table -- in a machine, an invariant, a scenario's `given` or a step's
  `expect_db`. Machines and scenarios may use the schema name ("Order") or the SQL
  table name ("orders"); invariants use the schema name.
- S003: an unknown column of a known table (same places, plus `by=` in an invariant).
- S004: a machine's column is not a one_of column (its states are the one_of values).
- S005: a machine names a state (in an edge or `initial`) outside the column's one_of.
- S006: two machines for the same table column.
- S007: an invariant's expression does not parse.
- S008: an invariant's expression uses a form outside the allowed ones (a row rule, an
  aggregate equality, a count bound; see `lattice.spec.invariants`).
- S009: an invariant's expression is ill-typed: operands of different types, a value
  outside a one_of column's values, ordering a column that has no order (or is stored
  under other spellings), a non-numeric sum.
- S010: two invariants, or two scenarios, share a name.
- S011: a scenario step signs in as a user handle missing from `Spec.users`.
- S012: a malformed scenario: a step's method is not GET/POST/PUT/PATCH/DELETE, its
  path does not start with "/", its `expect_status` is not an HTTP status, or its
  `expect_db` is not of the form {table: {"count": n}} / {table: {"where": {...},
  "count": n}} / {table: {"where": {...}, "equals": {...}}}; or `given` is not
  {table: [row dict, ...]}.
- S013: a program name is given twice (`Spec.programs` given as pairs).
- S014: two programs define the same endpoint: the same method and path, or the same
  function name.
- S015: a `Spec.users` entry is not handle -> (login, password), two texts.
- S016: an unchecked requirement concerns something the spec does not name: not a
  table, a `Table.column`, a program or an endpoint (a program's or a contract's,
  "METHOD /path").

Contracts (`Spec.contracts`, `lattice.spec.model.Contract`); an unknown table or column
they name is S002 / S003, like everywhere else:

- S017: a malformed contract: its method is not GET/POST/PUT/PATCH/DELETE, its path
  does not start with "/", its `kind` is not one of CONTRACT_KINDS, or its
  `owner_scope` is not one of OWNER_SCOPES.
- S018: two contracts for the same endpoint (method and path).
- S019: a contract's auth names a role the profile does not have (`Roles(...)` or a
  bare role name; the keywords are Public, Authenticated, Admin).
- S020: a malformed contract parameter: its source is not path/query/body/form, a path
  parameter the path has no `{hole}` for, a name given twice, a bound of the wrong kind
  (`min_len`/`max_len` on a non-Str, `ge`/`gt`/`le`/`lt` on a non-number), an empty
  `one_of`.
- S021: a malformed contract error: a status outside 100-599 (None is allowed: unknown),
  a trigger (`when`) outside ERROR_TRIGGERS, or an `example` with keys other than
  user/path/params/json/form.
- S022: a contract's response model is not one of the profile's responses.
- S023: a contract's owner scope ("owner" / "any") needs a table that is owned
  (`owner` or `owner_via`); it names no table, or a table nobody owns.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lattice.spec.invariants import InvariantError, compile_invariant
from lattice.spec.model import (
    AUTH_KEYWORDS,
    CONTRACT_KINDS,
    ERROR_TRIGGERS,
    EXAMPLE_KEYS,
    OWNER_SCOPES,
    PARAM_SOURCES,
    Contract,
    ContractParam,
    Machine,
    Scenario,
    Spec,
    Step,
    resolve_table,
    tables_of,
)
from lattice.typesys import Scalar, Schema

CODES: Mapping[str, str] = {
    "S001": "the profile has no API profile",
    "S002": "unknown table",
    "S003": "unknown column",
    "S004": "machine column is not a one_of column",
    "S005": "machine state outside the column's one_of",
    "S006": "two machines for one column",
    "S007": "invariant does not parse",
    "S008": "invariant form not allowed",
    "S009": "invariant type error",
    "S010": "duplicate invariant or scenario name",
    "S011": "unknown scenario user",
    "S012": "malformed scenario",
    "S013": "duplicate program name",
    "S014": "endpoint defined by two programs",
    "S015": "malformed user credentials",
    "S016": "unchecked requirement concerns nothing known",
    "S017": "malformed contract",
    "S018": "endpoint given two contracts",
    "S019": "contract auth names an unknown role",
    "S020": "malformed contract parameter",
    "S021": "malformed contract error",
    "S022": "unknown contract response model",
    "S023": "contract owner scope on a table nobody owns",
}
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
EXPECT_DB_KEYS = frozenset({"where", "count", "equals"})


@dataclass(frozen=True)
class SpecProblem:
    """One spec inconsistency: `code` (CODES), what is wrong, and how to fix it."""

    code: str
    message: str
    hint: str | None = None

    def render(self) -> str:
        text = f"{self.code}: {self.message}"
        return f"{text} (hint: {self.hint})" if self.hint else text


def validate(spec: Spec) -> list[SpecProblem]:
    """Every problem of `spec`, in the order: users, programs, machines, invariants,
    scenarios, contracts, unchecked requirements. Empty when the spec is consistent."""
    if spec.profile.api is None:
        return [
            SpecProblem(
                "S001",
                "the spec's profile has no API profile",
                "give Profile(api=ApiProfile(tables=..., table_names=..., ...))",
            )
        ]
    problems: list[SpecProblem] = []
    problems += _users(spec)
    problems += _programs(spec)
    problems += _machines(spec)
    problems += _invariants(spec)
    problems += _scenarios(spec)
    problems += _contracts(spec)
    problems += _unchecked(spec)
    return problems


# ---- users and programs ------------------------------------------------------------------


def _users(spec: Spec) -> list[SpecProblem]:
    problems = []
    for handle, login in spec.users.items():
        ok = (
            isinstance(handle, str)
            and isinstance(login, tuple)
            and len(login) == 2
            and all(isinstance(part, str) for part in login)
        )
        if not ok:
            problems.append(
                SpecProblem(
                    "S015",
                    f"user {handle!r} -> {login!r} is not (login, password)",
                    'write users={"alice": ("alice@example.com", "secret")}',
                )
            )
    return problems


def _endpoints(source: str) -> list[tuple[str, str, str]]:
    """(function name, METHOD, path) of each @endpoint("METHOD", "/path") in `source`;
    none when it does not parse (the Lattice checker reports that)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "endpoint"
                and len(decorator.args) >= 2
            ):
                continue
            method, path = decorator.args[0], decorator.args[1]
            if isinstance(method, ast.Constant) and isinstance(path, ast.Constant):
                found.append((node.name, str(method.value).upper(), str(path.value)))
    return found


def _programs(spec: Spec) -> list[SpecProblem]:
    problems = [
        SpecProblem(
            "S013",
            f"program {name!r} is given more than once",
            "give each program one name; merge or rename the sources",
        )
        for name in spec.duplicate_programs
    ]
    routes: dict[tuple[str, str], str] = {}
    functions: dict[str, str] = {}
    for program, source in spec.programs.items():
        for function, method, path in _endpoints(source):
            first = routes.setdefault((method, path), program)
            if first != program:
                problems.append(
                    SpecProblem(
                        "S014",
                        f"{method} {path} is defined by programs {first!r} and {program!r}",
                        "define each endpoint in one program",
                    )
                )
                continue
            first = functions.setdefault(function, program)
            if first != program:
                problems.append(
                    SpecProblem(
                        "S014",
                        f"endpoint {function!r} is defined by programs {first!r} and {program!r}",
                        "rename one of them",
                    )
                )
    return problems


# ---- machines ----------------------------------------------------------------------------


def _unknown_table(name: object, where: str, spec: Spec) -> SpecProblem:
    tables, sql_names = tables_of(spec.profile)
    known = ", ".join(f"{t} ({sql_names.get(t, t)})" for t in sorted(tables))
    return SpecProblem("S002", f"{where}: unknown table {name!r}", f"tables: {known}")


def _unknown_column(table: str, column: object, schema: Schema, where: str) -> SpecProblem:
    return SpecProblem(
        "S003",
        f"{where}: unknown column {column!r} of {table}",
        f"columns of {table}: {', '.join(schema.names())}",
    )


def _machines(spec: Spec) -> list[SpecProblem]:
    problems: list[SpecProblem] = []
    seen: set[tuple[str, str]] = set()
    for machine in spec.machines:
        problems += _machine(spec, machine, seen)
    return problems


def _machine(spec: Spec, machine: Machine, seen: set[tuple[str, str]]) -> list[SpecProblem]:
    where = f"machine {machine.table}.{machine.column}"
    found = resolve_table(spec.profile, machine.table)
    if found is None:
        return [_unknown_table(machine.table, where, spec)]
    table, _, schema = found
    if machine.column not in schema:
        return [_unknown_column(table, machine.column, schema, where)]
    if (table, machine.column) in seen:
        return [
            SpecProblem(
                "S006",
                f"{where}: a second machine for {table}.{machine.column}",
                "merge the two machines' edges into one",
            )
        ]
    seen.add((table, machine.column))
    choices = schema.choices(machine.column)
    if choices is None:
        return [
            SpecProblem(
                "S004",
                f"{where}: {table}.{machine.column} is not a one_of column",
                f"declare its states: one_of={{{machine.column!r}: (...)}} in {table}'s schema",
            )
        ]
    outside = [state for state in machine.states() if state not in choices]
    return [
        SpecProblem(
            "S005",
            f"{where}: state {state!r} is not a value of {table}.{machine.column}",
            f"states: {', '.join(choices)}",
        )
        for state in outside
    ]


# ---- invariants --------------------------------------------------------------------------


def _invariants(spec: Spec) -> list[SpecProblem]:
    problems = _duplicates([inv.name for inv in spec.invariants], "invariant")
    for inv in spec.invariants:
        try:
            compile_invariant(inv, spec.profile)
        except InvariantError as exc:
            problems.append(
                SpecProblem(exc.code, f"invariant {inv.name!r}: {exc.message}", exc.hint)
            )
    return problems


def _duplicates(names: Sequence[str], what: str) -> list[SpecProblem]:
    twice = dict.fromkeys(n for n in names if names.count(n) > 1)
    return [
        SpecProblem("S010", f"two {what}s are named {name!r}", f"give each {what} its own name")
        for name in twice
    ]


# ---- scenarios ---------------------------------------------------------------------------


def _scenarios(spec: Spec) -> list[SpecProblem]:
    problems = _duplicates([s.name for s in spec.scenarios], "scenario")
    for scenario in spec.scenarios:
        problems += _given(spec, scenario)
        for index, step in enumerate(scenario.steps, 1):
            problems += _step(spec, f"scenario {scenario.name!r} step {index}", step)
    return problems


def _malformed(where: str, message: str, hint: str) -> SpecProblem:
    return SpecProblem("S012", f"{where}: {message}", hint)


def _given(spec: Spec, scenario: Scenario) -> list[SpecProblem]:
    where = f"scenario {scenario.name!r} given"
    if scenario.given is None:
        return []
    shape = 'write given={"orders": [{"id": 1, ...}, ...]}'
    if not isinstance(scenario.given, Mapping):
        return [_malformed(where, "is not {table: [row, ...]}", shape)]
    problems: list[SpecProblem] = []
    for name, rows in scenario.given.items():
        found = resolve_table(spec.profile, name)
        if found is None:
            problems.append(_unknown_table(name, where, spec))
            continue
        table, _, schema = found
        if not isinstance(rows, list | tuple) or not all(isinstance(r, Mapping) for r in rows):
            problems.append(_malformed(where, f"{name!r} is not a list of row dicts", shape))
            continue
        unknown = dict.fromkeys(c for row in rows for c in row if c not in schema)
        problems += [_unknown_column(table, c, schema, where) for c in unknown]
    return problems


def _step(spec: Spec, where: str, step: Step) -> list[SpecProblem]:
    problems: list[SpecProblem] = []
    if step.method not in HTTP_METHODS:
        problems.append(
            _malformed(where, f"method {step.method!r}", f"use one of {', '.join(HTTP_METHODS)}")
        )
    if not (isinstance(step.path, str) and step.path.startswith("/")):
        problems.append(_malformed(where, f"path {step.path!r}", 'a path starts with "/"'))
    status = step.expect_status
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        problems.append(_malformed(where, f"expect_status {status!r}", "an HTTP status, 100-599"))
    if step.user is not None and step.user not in spec.users:
        known = ", ".join(sorted(spec.users)) or "none"
        problems.append(
            SpecProblem(
                "S011",
                f"{where}: unknown user {step.user!r}",
                f"add it to Spec.users (handle -> (login, password)); known: {known}",
            )
        )
    if step.expect_db is not None:
        problems += _expect_db(spec, where, step.expect_db)
    return problems


def _expect_db(spec: Spec, where: str, expect: Any) -> list[SpecProblem]:
    shape = (
        'use {"orders": {"count": 9}} or '
        '{"products": {"where": {"id": 2}, "equals": {"stock_quantity": 30}}}'
    )
    if not isinstance(expect, Mapping):
        return [_malformed(where, "expect_db is not {table: check}", shape)]
    problems: list[SpecProblem] = []
    for name, check in expect.items():
        found = resolve_table(spec.profile, name)
        if found is None:
            problems.append(_unknown_table(name, f"{where} expect_db", spec))
            continue
        table, _, schema = found
        if (
            not isinstance(check, Mapping)
            or not set(check) <= EXPECT_DB_KEYS
            or not set(check) & {"count", "equals"}
        ):
            problems.append(_malformed(where, f"expect_db[{name!r}] = {check!r}", shape))
            continue
        count = check.get("count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            problems.append(_malformed(where, f"expect_db[{name!r}] count {count!r}", shape))
        for key in ("where", "equals"):
            columns = check.get(key, {})
            if not isinstance(columns, Mapping):
                problems.append(_malformed(where, f"expect_db[{name!r}] {key} {columns!r}", shape))
                continue
            problems += [
                _unknown_column(table, c, schema, f"{where} expect_db {key}")
                for c in columns
                if c not in schema
            ]
    return problems


# ---- contracts ---------------------------------------------------------------------------

ROLES_RE = re.compile(r"^Roles\((.*)\)$")


def auth_roles(auth: str) -> tuple[str, ...] | None:
    """The role names a contract auth string names: those of "Roles(A, B)", or a bare
    role name; None for a keyword (Public, Authenticated, Admin)."""
    if auth in AUTH_KEYWORDS:
        return None
    m = ROLES_RE.match(auth)
    if m:
        return tuple(r.strip() for r in m.group(1).split(",") if r.strip())
    return (auth,)


def _contracts(spec: Spec) -> list[SpecProblem]:
    problems: list[SpecProblem] = []
    seen: set[str] = set()
    for contract in spec.contracts:
        where = f"contract {contract.endpoint}"
        if contract.endpoint in seen:
            problems.append(
                SpecProblem(
                    "S018", f"{where}: a second contract", "give each endpoint one contract"
                )
            )
            continue
        seen.add(contract.endpoint)
        problems += _contract(spec, contract, where)
    return problems


def _contract(spec: Spec, c: Contract, where: str) -> list[SpecProblem]:
    api = spec.profile.api
    assert api is not None
    problems: list[SpecProblem] = []

    def bad(message: str, hint: str, code: str = "S017") -> None:
        problems.append(SpecProblem(code, f"{where}: {message}", hint))

    if c.method not in HTTP_METHODS:
        bad(f"method {c.method!r}", f"use one of {', '.join(HTTP_METHODS)}")
    if not (isinstance(c.path, str) and c.path.startswith("/")):
        bad(f"path {c.path!r}", 'a path starts with "/"')
    if c.kind is not None and c.kind not in CONTRACT_KINDS:
        bad(f"kind {c.kind!r}", f"use one of {', '.join(CONTRACT_KINDS)}")
    if c.owner_scope is not None and c.owner_scope not in OWNER_SCOPES:
        bad(f"owner_scope {c.owner_scope!r}", f"use one of {', '.join(OWNER_SCOPES)} or None")
    if c.auth is not None:
        roles = auth_roles(c.auth)
        unknown = [r for r in roles or () if r not in api.roles or r == "Public"]
        if roles is not None and (unknown or not roles):
            known = ", ".join(sorted(r for r in api.roles if r != "Public"))
            bad(
                f"auth {c.auth!r} names unknown roles {unknown}",
                f"Public, Authenticated, Admin, a role or Roles(...) of: {known}",
                "S019",
            )
    schema: Schema | None = None
    if c.table is not None:
        found = resolve_table(spec.profile, c.table)
        if found is None:
            problems.append(_unknown_table(c.table, where, spec))
        else:
            schema = found[2]
    if c.owner_scope in ("owner", "any") and (
        c.table is None or (schema is not None and not (schema.owner or schema.owner_via))
    ):
        bad(
            f"owner_scope {c.owner_scope!r} on {c.table or 'no table'}",
            "an owner scope needs a table with owner= or owner_via=; leave it None",
            "S023",
        )
    if c.response is not None and c.response not in api.responses:
        bad(
            f"response {c.response!r} is not a response model of the profile",
            f"models: {', '.join(sorted(api.responses))}",
            "S022",
        )
    problems += _contract_params(spec, c, where)
    problems += _contract_errors(c, where)
    return problems


def _column_ref(spec: Spec, ref: str, where: str) -> list[SpecProblem]:
    table, _, column = ref.partition(".")
    found = resolve_table(spec.profile, table)
    if found is None:
        return [_unknown_table(table, where, spec)]
    name, _, schema = found
    if not column or column not in schema:
        return [_unknown_column(name, column, schema, where)]
    return []


def _contract_params(spec: Spec, c: Contract, where: str) -> list[SpecProblem]:
    problems: list[SpecProblem] = []
    holes = set(re.findall(r"\{([^}]+)\}", c.path)) if isinstance(c.path, str) else set()
    names: set[tuple[str, str]] = set()
    numeric = (Scalar.INT, Scalar.FLOAT)

    def bad(p: ContractParam, message: str, hint: str) -> None:
        problems.append(SpecProblem("S020", f"{where} parameter {p.name!r}: {message}", hint))

    for p in c.params:
        if p.source not in PARAM_SOURCES:
            bad(p, f"source {p.source!r}", f"use one of {', '.join(PARAM_SOURCES)}")
        if p.source == "path" and p.name not in holes:
            bad(p, "the path has no hole for it", f"write {{{p.name}}} in the path")
        if (p.source, p.name) in names:
            bad(p, "given twice", "give each parameter once")
        names.add((p.source, p.name))
        if (p.min_len is not None or p.max_len is not None) and p.scalar is not Scalar.STR:
            bad(p, "a length bound on a non-Str parameter", "use ge/gt/le/lt for numbers")
        bounds = (p.ge, p.gt, p.le, p.lt)
        if any(b is not None for b in bounds) and p.scalar not in numeric:
            bad(p, "a numeric bound on a non-number parameter", "use min_len/max_len for text")
        if p.one_of is not None and not p.one_of:
            bad(p, "an empty one_of", "list the accepted values")
        if p.column is not None:
            problems += _column_ref(spec, p.column, f"{where} parameter {p.name!r} column")
        if p.ref is not None and resolve_table(spec.profile, p.ref) is None:
            problems.append(_unknown_table(p.ref, f"{where} parameter {p.name!r} ref", spec))
    return problems


def _contract_errors(c: Contract, where: str) -> list[SpecProblem]:
    problems: list[SpecProblem] = []
    for e in c.errors:
        status = e.status
        if status is not None and (
            isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599
        ):
            problems.append(
                SpecProblem("S021", f"{where}: error status {status!r}", "an HTTP status, 100-599")
            )
        if e.when not in ERROR_TRIGGERS:
            problems.append(
                SpecProblem(
                    "S021",
                    f"{where}: error trigger {e.when!r}",
                    f"one of {', '.join(ERROR_TRIGGERS)}",
                )
            )
        if e.example is not None and (
            not isinstance(e.example, Mapping) or not set(e.example) <= EXAMPLE_KEYS
        ):
            problems.append(
                SpecProblem(
                    "S021",
                    f"{where}: error example {e.example!r}",
                    f"an example has keys of {', '.join(sorted(EXAMPLE_KEYS))}",
                )
            )
    return problems


# ---- unchecked requirements --------------------------------------------------------------


def _unchecked(spec: Spec) -> list[SpecProblem]:
    tables, sql_names = tables_of(spec.profile)
    known = set(tables) | set(sql_names.values()) | set(spec.programs)
    known |= {f"{t}.{c}" for t, schema in tables.items() for c in schema.names()}
    known |= {f for source in spec.programs.values() for f, _, _ in _endpoints(source)}
    known |= {f"{m} {p}" for source in spec.programs.values() for _, m, p in _endpoints(source)}
    known |= {c.endpoint for c in spec.contracts}
    return [
        SpecProblem(
            "S016",
            f"unchecked requirement {item.text!r} concerns {concern!r}, which the spec "
            "does not name",
            "name a table, a Table.column, a program or an endpoint",
        )
        for item in spec.unchecked
        for concern in item.concerns
        if concern not in known
    ]
