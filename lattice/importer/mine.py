"""Invariant and state-machine mining (Phase 2 plan 2026-10-05 §4): candidates from the
app's source and from the rows the app wrote while probes ran, each checked on every
database snapshot, the survivors turned into pending proposals.

**Input.** :func:`mine` takes the candidate spec's ``Profile`` (it names the tables and
types the expressions), the observed :class:`Run` s, and optionally the ``AppModel``
(``lattice.importer.appmodel.RepoModel``) and :class:`MiningHints` (what a front end read
that the AppModel has no field for yet).

* A :class:`Run` is one sequence of requests on one database: ``snapshots[0]`` is the
  SQLite file before the first request (the seed, plus a scenario's given rows: the
  *pre-probe* snapshot), ``snapshots[i]`` the file after request ``i``
  (``requests[i-1]``, a JSON-able record of it). A layer R probe is a run of two
  snapshots; a scenario of one more than its steps. :class:`Recorder` makes runs from an
  ASGI app factory; other transports call :meth:`Recorder.begin` / :meth:`RunRecorder.after`.
* **App-written rows**: a row of a later snapshot whose content (every column, key
  included) is not that of a row of its run's pre-probe snapshot. Seed rows the app left
  as they were are never support: a template over them would be a tautology of our own
  seed. Only app-written rows propose observed candidates.

**Candidates** (``source`` says which):

* ``static``: the AppModel's column bounds (``FieldSpec.ge/gt/le/lt`` of a table column,
  or of a request model named after its table -- ``ProductCreate.price gt=0`` is
  ``Product.price > 0``); ``FieldSpec.unique`` columns (``count(T, by=c) <= 1``);
  ``FieldSpec.choices`` of a column the profile has no ``one_of`` for (``T.c in (...)``);
  the hints' check constraints, unique constraints, validators and transition maps. A
  static candidate the expression language cannot state becomes an ``unchecked``
  proposal quoting its source.
* ``observed`` (Daikon-style templates over app-written rows; ``min_support`` rows at
  least): ``T.c > 0``, else ``T.c >= 0``, for a numeric column that is no key or foreign
  key; ``T.c != ""`` for a text column; ``not is_null(T.c)`` for a column the schema
  allows NULL in; ``count(T, by=fk) <= 1`` for a foreign key (support: distinct values);
  ``P.c == sum(C.x * C.y, by=fk)`` (and ``sum(C.x)``) for a numeric parent column named
  like a total (``total``, ``amount``, ``sum``, ``subtotal``, ``balance``, ``cost``) and
  a child table with a foreign key to it (support: parent rows with lines the app wrote).
* ``machine``: per ``one_of`` column, the transitions seen between consecutive snapshots
  of a run (same key, the column changed) and the states rows are created in. A static
  transition map survives when every observed move is one of its edges (its initial
  states, when it names none, are the observed ones) and is then proposed in place of
  the observed machine.

**Checking.** Every invariant candidate is compiled (``compile_invariant``) and run on
every distinct snapshot, pre-probe snapshots included (a candidate our seed contradicts
is not proposed either). Any violating row refutes it: it is dropped and kept in
``MiningResult.refuted`` with its witness. A candidate that does not compile is dropped
(observed) or proposed as ``unchecked`` (static).

**Proposals** (:class:`Proposal`): ``kind`` (``invariant`` / ``machine`` /
``unchecked``), ``spec_object`` (the ``Invariant`` / ``Machine`` / ``Unchecked``),
``evidence`` (sample rows, runs and requests; transitions), ``support``, ``source``; a
stable ``id`` and ``sentence``. ``status`` is always ``pending``: nothing here approves
anything; the review CLI records decisions elsewhere (``review.jsonl``).

**What front ends are expected to hand over** (:class:`MiningHints`): the
:class:`CheckConstraint` SQL text per table (SQLAlchemy ``CheckConstraint.sqltext``;
Django ``CheckConstraint.check`` compiled to SQL on the SQLite connection, or
``PositiveIntegerField`` as ``"<col> >= 0"``), :class:`Unique` constraints (columns,
optional SQL condition), :class:`Validator` s (the Python source of the condition that
makes a ``clean()`` / ``validate_<f>`` / ``validate()`` / ``@validates`` reject, with
the column it validates for a per-field validator) and :class:`TransitionMap` s (or
:func:`transition_maps` reads module-level ``{State.A: {State.B, ...}}`` dicts from the
package). Choices and bounds come from the AppModel's ``FieldSpec`` s.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import FieldSpec, RepoModel, TableModel
from lattice.profile import Profile
from lattice.spec.invariants import InvariantError, compile_invariant
from lattice.spec.model import Invariant, Machine, Unchecked, tables_of
from lattice.typesys import NUMERIC, Scalar, Schema

KINDS = ("invariant", "machine", "unchecked")
# the observed templates (mine(templates=...)): sign (> 0 / >= 0), non_empty (!= ""),
# not_null (beyond the schema), one_per_fk (count(T, by=fk) <= 1), sum (P.c == sum(...)),
# machine (observed transitions)
TEMPLATES = ("sign", "non_empty", "not_null", "one_per_fk", "sum", "machine")
TOTAL_NAMES = ("total", "amount", "sum", "subtotal", "balance", "cost")
SAMPLE = 5

# ---- inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """One run of requests on one database: ``snapshots[0]`` before the first request
    (the pre-probe snapshot), ``snapshots[i]`` after ``requests[i-1]``."""

    label: str
    snapshots: tuple[Path, ...]
    requests: tuple[Mapping[str, Any] | None, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshots", tuple(Path(p) for p in self.snapshots))
        object.__setattr__(self, "requests", tuple(self.requests))

    def request(self, index: int) -> Mapping[str, Any] | None:
        """The request that led to snapshot ``index`` (None for the pre-probe one)."""
        if 1 <= index <= len(self.requests):
            return self.requests[index - 1]
        return None


@dataclass(frozen=True)
class CheckConstraint:
    """A database CHECK constraint: ``table`` (schema/class or SQL name), ``sql`` the
    condition's SQL text over the table's column names (``"price > 0 AND stock >= 0"``)."""

    table: str
    sql: str
    name: str | None = None
    source: str = ""


@dataclass(frozen=True)
class Unique:
    """A unique constraint: ``columns`` of ``table``; ``condition`` the SQL text of a
    conditional (partial) constraint's condition."""

    table: str
    columns: tuple[str, ...]
    condition: str | None = None
    source: str = ""

    def __post_init__(self) -> None:
        cols = (self.columns,) if isinstance(self.columns, str) else tuple(self.columns)
        object.__setattr__(self, "columns", cols)


@dataclass(frozen=True)
class Validator:
    """A model or serializer validator of ``table``: ``condition`` is the Python source
    of the condition under which it rejects (raises); it names columns as ``self.c`` /
    ``obj.c`` / ``instance.c`` / ``attrs["c"]`` / ``data.get("c")`` / a bare column
    name, and ``value`` for ``column`` (a per-field validator's field)."""

    table: str
    condition: str
    column: str | None = None
    source: str = ""


@dataclass(frozen=True)
class TransitionMap:
    """A transition map in code: ``edges`` maps a state to the states it may move to,
    each spelled as the column's value, its stored spelling or its enum member name;
    ``initial`` the states a new row may start in (empty: not stated)."""

    table: str
    column: str
    edges: Mapping[str, Sequence[str]]
    initial: tuple[str, ...] = ()
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "edges", {k: tuple(v) for k, v in self.edges.items()})
        object.__setattr__(self, "initial", tuple(self.initial))


@dataclass(frozen=True)
class MiningHints:
    """What a front end read that the AppModel has no field for."""

    checks: tuple[CheckConstraint, ...] = ()
    uniques: tuple[Unique, ...] = ()
    validators: tuple[Validator, ...] = ()
    transitions: tuple[TransitionMap, ...] = ()


# ---- outputs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """A mined candidate that survived every snapshot, for the user to review."""

    kind: str
    spec_object: Invariant | Machine | Unchecked
    evidence: Mapping[str, Any] = field(hash=False)
    support: int
    source: str

    @property
    def status(self) -> str:
        return "pending"  # never approved here: decisions live in the review log

    @property
    def text(self) -> str:
        """The checked form: an invariant's expression, a machine's edges, the
        unchecked requirement's text."""
        return _object_text(self.spec_object)

    @property
    def id(self) -> str:
        digest = hashlib.sha1(f"{self.kind}\0{self.text}".encode()).hexdigest()[:10]
        return f"{self.kind[:3]}-{digest}"

    @property
    def sentence(self) -> str:
        obj = self.spec_object
        if isinstance(obj, Invariant):
            return obj.sentence
        if isinstance(obj, Machine):
            moves = "; ".join(f"{a} -> {b}" for a, b in obj.edges)
            start = f", starting in {' or '.join(obj.initial)}" if obj.initial else ""
            return f"{obj.table}.{obj.column} moves only {moves}{start}."
        return obj.text

    def to_dict(self) -> dict[str, Any]:
        obj = self.spec_object
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "sentence": self.sentence,
            "source": self.source,
            "support": self.support,
        }
        if isinstance(obj, Invariant):
            out["name"], out["expr"] = obj.name, obj.expr
        elif isinstance(obj, Machine):
            out["machine"] = {
                "table": obj.table,
                "column": obj.column,
                "edges": [list(e) for e in obj.edges],
                "initial": list(obj.initial),
            }
        else:
            out["unchecked"] = {"text": obj.text, "concerns": list(obj.concerns)}
        out["evidence"] = _jsonable(self.evidence)
        return out


@dataclass(frozen=True)
class Refuted:
    """A dropped candidate and the first snapshot that contradicted it."""

    kind: str
    expr: str
    source: str
    witness: Mapping[str, Any] = field(hash=False)


@dataclass
class MiningResult:
    proposals: list[Proposal]
    refuted: list[Refuted]
    snapshots: int  # distinct snapshots every candidate was checked on
    runs: int
    app_rows: int  # distinct app-written rows observed
    dropped: list[str] = field(default_factory=list)  # did not compile (observed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": {
                "proposed": len(self.proposals),
                "refuted": len(self.refuted),
                "uncompiled": len(self.dropped),
                "snapshots": self.snapshots,
                "runs": self.runs,
                "app_rows": self.app_rows,
            },
            "proposals": [p.to_dict() for p in self.proposals],
            "refuted": [
                {"kind": r.kind, "expr": r.expr, "source": r.source, "witness": r.witness}
                for r in self.refuted
            ],
            "uncompiled": list(self.dropped),
        }


def _object_text(obj: Invariant | Machine | Unchecked) -> str:
    if isinstance(obj, Invariant):
        return obj.expr
    if isinstance(obj, Machine):
        edges = ", ".join(f"{a}->{b}" for a, b in obj.edges)
        return f"{obj.table}.{obj.column}: {edges} | initial {', '.join(obj.initial)}"
    return obj.text


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


# ---- snapshots ------------------------------------------------------------------------------


def _read(db: Path) -> dict[str, list[dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        out: dict[str, list[dict[str, Any]]] = {}
        for t in names:
            cur = conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')
            cols = [d[0] for d in cur.description]
            out[t] = [dict(zip(cols, row, strict=True)) for row in cur]
        return out
    finally:
        conn.close()


def _row_key(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


@dataclass
class _Written:
    """An app-written row: where it was first seen."""

    row: dict[str, Any]
    run: str
    request: Mapping[str, Any] | None


@dataclass
class _Observed:
    """What the runs show: app-written rows per SQL table (row key -> first sighting),
    transitions and creations per (SQL table, column), and the distinct snapshots."""

    written: dict[str, dict[str, _Written]] = field(default_factory=dict)
    moves: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    created: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    snapshots: dict[str, tuple[Path, str, int, Mapping[str, Any] | None]] = field(
        default_factory=dict
    )  # file digest -> (path, run, index, request)

    def rows(self, table: str) -> list[_Written]:
        return list(self.written.get(table, {}).values())


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _observe(
    runs: Sequence[Run], keys: Mapping[str, str], machine_cols: Mapping[str, list[str]]
) -> _Observed:
    obs = _Observed()
    for run in sorted(runs, key=lambda r: r.label):
        states = [_read(p) for p in run.snapshots]
        for i, path in enumerate(run.snapshots):
            obs.snapshots.setdefault(_digest(path), (path, run.label, i, run.request(i)))
        if not states:
            continue
        pre = {t: {_row_key(r) for r in rows} for t, rows in states[0].items()}
        for i in range(1, len(states)):
            request = run.request(i)
            for table, rows in states[i].items():
                seen = obs.written.setdefault(table, {})
                for row in rows:
                    k = _row_key(row)
                    if k not in pre.get(table, set()) and k not in seen:
                        seen[k] = _Written(row, run.label, request)
            for table, cols in machine_cols.items():
                key = keys.get(table)
                if key is None:
                    continue
                before = {r.get(key): r for r in states[i - 1].get(table, [])}
                for row in states[i].get(table, []):
                    old = before.get(row.get(key))
                    for col in cols:
                        where = {"run": run.label, "key": row.get(key), "request": request}
                        if old is None:
                            obs.created.setdefault((table, col), []).append(
                                {**where, "state": row.get(col)}
                            )
                        elif old.get(col) != row.get(col):
                            obs.moves.setdefault((table, col), []).append(
                                {**where, "from": old.get(col), "to": row.get(col)}
                            )
    return obs


# ---- candidates -----------------------------------------------------------------------------


@dataclass
class _Candidate:
    name: str
    sentence: str
    expr: str
    source: str
    table: str  # schema name of the table whose rows support it
    support: Callable[[], tuple[int, list[_Written]]] | None = None
    static: bool = False
    # observed lower bounds: (table, column, strength) -- a weaker survivor is dropped
    bound: tuple[str, str, int] | None = None


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class _Miner:
    def __init__(
        self,
        profile: Profile,
        runs: Sequence[Run],
        model: RepoModel | None,
        hints: MiningHints,
        min_support: int,
        templates: Iterable[str] = TEMPLATES,
    ) -> None:
        self.profile = profile
        self.model = model
        self.hints = hints
        self.min_support = min_support
        self.templates = frozenset(templates)
        unknown = self.templates - set(TEMPLATES)
        if unknown:
            raise ValueError(f"unknown templates {sorted(unknown)}; known: {', '.join(TEMPLATES)}")
        self.tables, self.sql = tables_of(profile)
        self.schema_of_sql = {self.sql.get(t, t): t for t in self.tables}
        self.keys = {
            self.sql.get(t, t): k for t, s in self.tables.items() if (k := _key_of(s)) is not None
        }
        self.machine_cols = {
            self.sql.get(t, t): [c for c, _ in s.one_of] for t, s in self.tables.items() if s.one_of
        }
        self.obs = _observe(runs, self.keys, self.machine_cols)
        self.runs = runs
        self.table_models = {tm.name: tm for tm in (model.tables if model else ())}
        self.refuted: list[Refuted] = []
        self.dropped: list[str] = []
        self.unchecked: list[Proposal] = []

    # -- naming helpers --

    def schema_name(self, name: str) -> str | None:
        if name in self.tables:
            return name
        if name in self.schema_of_sql:
            return self.schema_of_sql[name]
        lowered = {t.lower(): t for t in self.tables}
        return lowered.get(name.lower())

    def written(self, table: str) -> list[_Written]:
        return self.obs.rows(self.sql.get(table, table))

    def field(self, table: str, column: str) -> FieldSpec | None:
        tm = self.table_models.get(table)
        if tm is None:
            return None
        return next((c for c in tm.columns if c.name == column), None)

    def foreign_keys(self, table: str) -> dict[str, str]:
        """Column -> parent schema name, for the table's foreign keys."""
        schema = self.tables[table]
        out: dict[str, str] = {}
        tm = self.table_models.get(table)
        for c in tm.columns if tm else ():
            if c.foreign_key:
                parent = self.schema_name(c.foreign_key.split(".")[0])
                if parent is not None:
                    out[c.name] = parent
        if schema.owner_via is not None:
            out.setdefault(schema.owner_via[0], schema.owner_via[1])
        if schema.owner is not None and self.profile.api is not None:
            out.setdefault(schema.owner, self.profile.api.users_table)
        if tm is None:
            by_snake = {_snake(t): t for t in self.tables}
            for col in schema.names():
                if col.endswith("_id") and col[:-3] in by_snake:
                    out.setdefault(col, by_snake[col[:-3]])
        return out

    def plain_columns(self, table: str) -> list[tuple[str, Scalar]]:
        """Columns no key, foreign key or one_of column."""
        schema = self.tables[table]
        skip = {c for key in schema.keys for c in key} | set(self.foreign_keys(table))
        skip |= {c for c, _ in schema.one_of}
        if (tm := self.table_models.get(table)) is not None:
            skip |= {c.name for c in tm.columns if c.primary_key}
        if not schema.keys and "id" in schema.names():
            skip.add("id")
        return [(c, schema.get(c)) for c in schema.names() if c not in skip]

    # -- support --

    def rows_with(self, table: str, column: str) -> Callable[[], tuple[int, list[_Written]]]:
        def count() -> tuple[int, list[_Written]]:
            rows = [w for w in self.written(table) if w.row.get(column) is not None]
            return len(rows), rows

        return count

    def distinct_values(self, table: str, column: str) -> Callable[[], tuple[int, list[_Written]]]:
        def count() -> tuple[int, list[_Written]]:
            rows = [w for w in self.written(table) if w.row.get(column) is not None]
            return len({_row_key({"v": w.row[column]}) for w in rows}), rows

        return count

    # -- templates --

    def observed(self) -> list[_Candidate]:
        out: list[_Candidate] = []
        for table in sorted(self.tables):
            schema = self.tables[table]
            if not self.written(table):
                continue
            for col, scalar in self.plain_columns(table):
                nullable = col in schema.nullable
                ref = f"{table}.{col}"
                guard = f"is_null({ref}) or " if nullable else ""
                support = self.rows_with(table, col)
                if scalar in NUMERIC and "sign" in self.templates:
                    signs = (
                        (2, ">", "positive", "positive"),
                        (1, ">=", "never negative", "non_neg"),
                    )
                    for strength, op, word, tag in signs:
                        out.append(
                            _Candidate(
                                f"{_snake(table)}_{col}_{tag}",
                                f"{table}'s {col} is {word}.",
                                f"{guard}{ref} {op} 0",
                                f"observed: {ref} {op} 0 on the rows the app wrote",
                                table,
                                support,
                                bound=(table, col, strength),
                            )
                        )
                if scalar is Scalar.STR and "non_empty" in self.templates:
                    out.append(
                        _Candidate(
                            f"{_snake(table)}_{col}_not_empty",
                            f"{table}'s {col} is never empty.",
                            f'{guard}{ref} != ""',
                            f"observed: {ref} non-empty on the rows the app wrote",
                            table,
                            support,
                        )
                    )
            for col in sorted(schema.nullable):
                if col not in schema.names() or "not_null" not in self.templates:
                    continue
                out.append(
                    _Candidate(
                        f"{_snake(table)}_{col}_set",
                        f"{table}'s {col} is always set.",
                        f"not is_null({table}.{col})",
                        f"observed: {table}.{col} (nullable in the schema) set on every row "
                        "the app wrote",
                        table,
                        self.all_rows(table),
                    )
                )
            for fk, parent in sorted(self.foreign_keys(table).items()):
                if "one_per_fk" not in self.templates:
                    break
                out.append(
                    _Candidate(
                        f"one_{_snake(table)}_per_{fk}",
                        f"A {parent} has at most one {table} ({table}.{fk}).",
                        f'count({table}, by="{fk}") <= 1',
                        f"observed: no two {table} rows share {fk}",
                        table,
                        self.distinct_values(table, fk),
                    )
                )
        if "sum" in self.templates:
            out.extend(self.aggregates())
        return out

    def all_rows(self, table: str) -> Callable[[], tuple[int, list[_Written]]]:
        def count() -> tuple[int, list[_Written]]:
            rows = self.written(table)
            return len(rows), rows

        return count

    def aggregates(self) -> list[_Candidate]:
        out: list[_Candidate] = []
        for child in sorted(self.tables):
            for fk, parent in sorted(self.foreign_keys(child).items()):
                if parent not in self.tables or parent == child:
                    continue
                totals = [
                    c
                    for c, s in self.plain_columns(parent)
                    if s in NUMERIC and any(n in c.lower() for n in TOTAL_NAMES)
                ]
                nums = [c for c, s in self.plain_columns(child) if s in NUMERIC]
                terms = [f"{child}.{c}" for c in nums]
                terms += [
                    f"{child}.{a} * {child}.{b}" for i, a in enumerate(nums) for b in nums[i + 1 :]
                ]
                for total in totals:
                    for term in terms:
                        out.append(
                            _Candidate(
                                f"{_snake(parent)}_{total}_sum",
                                f"{parent}'s {total} is the sum of {term.replace(child + '.', '')}"
                                f" over its {child} rows.",
                                f'{parent}.{total} == sum({term}, by="{fk}")',
                                f"observed: {parent}.{total} equals the sum over {child}.{fk}",
                                parent,
                                self.parents_with_lines(parent, child, fk),
                            )
                        )
        return out

    def parents_with_lines(
        self, parent: str, child: str, fk: str
    ) -> Callable[[], tuple[int, list[_Written]]]:
        def count() -> tuple[int, list[_Written]]:
            key = self.keys.get(self.sql.get(parent, parent))
            if key is None:
                return 0, []
            child_rows = self.written(child)
            touched = {_row_key({"v": w.row.get(fk)}) for w in child_rows}
            rows = [w for w in self.written(parent) if _row_key({"v": w.row.get(key)}) in touched]
            return len(rows), rows

        return count

    # -- static candidates --

    def static(self) -> list[_Candidate]:
        out: list[_Candidate] = []
        for table, tm in sorted(self.table_models.items()):
            if table not in self.tables:
                continue
            for f in tm.columns:
                out.extend(self.field_bounds(table, f, f"{table}.{f.name}"))
                out.extend(self.field_choices(table, f))
                if f.unique and not f.primary_key and f.name in self.tables[table].names():
                    source = f"static: {table}.{f.name} unique=True"
                    out.append(self.unique_candidate(table, f.name, source))
        for dm in self.model.models if self.model else ():
            table = self.table_for_model(dm.name)
            if table is None:
                continue
            for f in dm.fields:
                if f.name in self.tables[table].names():
                    out.extend(self.field_bounds(table, f, f"{dm.name}.{f.name}"))
        for check in self.hints.checks:
            out.extend(self.check_candidate(check))
        for u in self.hints.uniques:
            out.extend(self.unique_hint(u))
        for v in self.hints.validators:
            out.extend(self.validator_candidate(v))
        return out

    def table_for_model(self, name: str) -> str | None:
        """The table a request model is named after (``OrderItemCreate`` -> OrderItem)."""
        best = None
        for t in self.tables:
            if name.startswith(t) and name != t and (best is None or len(t) > len(best)):
                best = t
        return best

    def field_bounds(self, table: str, f: FieldSpec, where: str) -> list[_Candidate]:
        scalar = self.tables[table].get(f.name)
        if scalar not in NUMERIC:
            return []
        out = []
        for attr, op, word in (
            ("ge", ">=", "at least"),
            ("gt", ">", "more than"),
            ("le", "<=", "at most"),
            ("lt", "<", "less than"),
        ):
            bound = getattr(f, attr)
            if bound is None or isinstance(bound, bool):
                continue
            ref = f"{table}.{f.name}"
            guard = f"is_null({ref}) or " if f.name in self.tables[table].nullable else ""
            out.append(
                _Candidate(
                    f"{_snake(table)}_{f.name}_{attr}_{_num_name(bound)}",
                    f"{table}'s {f.name} is {word} {bound}.",
                    f"{guard}{ref} {op} {bound!r}",
                    f"static: {where} {attr}={bound!r}",
                    table,
                    self.rows_with(table, f.name),
                    static=True,
                )
            )
        return out

    def field_choices(self, table: str, f: FieldSpec) -> list[_Candidate]:
        schema = self.tables[table]
        if not f.choices or f.name in dict(schema.one_of) or f.name not in schema.names():
            return []
        stored = f.choice_names or f.choices
        listed = ", ".join(json.dumps(v) for v in stored)
        ref = f"{table}.{f.name}"
        guard = f"is_null({ref}) or " if f.name in schema.nullable else ""
        return [
            _Candidate(
                f"{_snake(table)}_{f.name}_choices",
                f"{table}'s {f.name} is one of {listed}.",
                f"{guard}{ref} in ({listed}{',' if len(stored) == 1 else ''})",
                f"static: {table}.{f.name} choices",
                table,
                self.rows_with(table, f.name),
                static=True,
            )
        ]

    def unique_candidate(self, table: str, column: str, source: str) -> _Candidate:
        return _Candidate(
            f"one_{_snake(table)}_per_{column}",
            f"No two {table} rows share {column}.",
            f'count({table}, by="{column}") <= 1',
            source,
            table,
            self.distinct_values(table, column),
            static=True,
        )

    def unique_hint(self, u: Unique) -> list[_Candidate]:
        table = self.schema_name(u.table)
        concern = table or u.table
        if table is not None and len(u.columns) == 1 and u.condition is None:
            return [self.unique_candidate(table, u.columns[0], f"static: {u.source or 'unique'}")]
        what = f"No two {concern} rows share ({', '.join(u.columns)})"
        if u.condition:
            what += f" where {u.condition}"
        self.add_unchecked(f"{what}.", concern, u.source, "unique constraint")
        return []

    def check_candidate(self, check: CheckConstraint) -> list[_Candidate]:
        table = self.schema_name(check.table)
        label = f"CheckConstraint {check.name or ''}".rstrip()
        source = f"static: {label} ({check.sql})" + (f" at {check.source}" if check.source else "")
        expr = _sql_to_expr(check.sql, table, self.tables[table]) if table else None
        if table is None or expr is None:
            self.add_unchecked(
                f"{check.table}: CHECK ({check.sql})", table or check.table, check.source, label
            )
            return []
        return [
            _Candidate(
                f"{_snake(table)}_{_slug(check.name or check.sql)}",
                f"{table} rows satisfy CHECK ({check.sql}).",
                expr,
                source,
                table,
                self.all_rows(table),
                static=True,
            )
        ]

    def validator_candidate(self, v: Validator) -> list[_Candidate]:
        table = self.schema_name(v.table)
        cond = _py_to_expr(v.condition, table, self.tables[table], v.column) if table else None
        if table is None or cond is None:
            self.add_unchecked(
                f"{v.table}: a validator rejects when {v.condition}",
                table or v.table,
                v.source,
                "validator",
            )
            return []
        return [
            _Candidate(
                f"{_snake(table)}_{_slug(v.condition)}",
                f"No {table} row has {v.condition} (a validator rejects it).",
                f"not ({cond})",
                f"static: validator `{v.condition}`" + (f" at {v.source}" if v.source else ""),
                table,
                self.all_rows(table),
                static=True,
            )
        ]

    def add_unchecked(self, text: str, concern: str, where: str, what: str) -> None:
        source = f"static: {what}" + (f" at {where}" if where else "")
        self.unchecked.append(
            Proposal("unchecked", Unchecked(text, (concern,)), {"quote": text}, 0, source)
        )

    # -- checking --

    def check(self, candidates: Sequence[_Candidate]) -> list[Proposal]:
        merged: dict[str, _Candidate] = {}
        for c in candidates:
            if c.expr in merged:
                prev = merged[c.expr]
                if c.source not in prev.source:
                    prev.source = f"{prev.source}; {c.source}"
                prev.static = prev.static or c.static
                prev.bound = prev.bound if c.bound is None else (prev.bound or c.bound)
            else:
                merged[c.expr] = dataclasses.replace(c)
        survivors: list[tuple[_Candidate, int, list[_Written]]] = []
        for c in merged.values():
            support, rows = c.support() if c.support else (0, [])
            if not c.static and support < self.min_support:
                continue
            inv = Invariant(c.name, c.sentence, c.expr)
            try:
                sql = compile_invariant(inv, self.profile)
            except InvariantError as err:
                if c.static:
                    self.add_unchecked(
                        f"{c.table}: {c.sentence} ({c.expr}; not checkable: {err.message})",
                        c.table,
                        "",
                        c.source.removeprefix("static: "),
                    )
                else:
                    self.dropped.append(f"{c.expr}: {err}")
                continue
            witness = self.refute(sql)
            if witness is not None:
                self.refuted.append(Refuted("invariant", c.expr, c.source, witness))
                continue
            survivors.append((c, support, rows))
        strongest: dict[tuple[str, str], int] = {}
        for c, _, _ in survivors:
            if c.bound is not None:
                t, col, s = c.bound
                strongest[(t, col)] = max(strongest.get((t, col), 0), s)
        out = []
        for c, support, rows in survivors:
            if c.bound is not None and not c.static:
                t, col, s = c.bound
                if s < strongest[(t, col)]:
                    continue
            evidence = _evidence(rows)
            out.append(
                Proposal(
                    "invariant", Invariant(c.name, c.sentence, c.expr), evidence, support, c.source
                )
            )
        return out

    def refute(self, sql: str) -> dict[str, Any] | None:
        for path, run, index, request in sorted(
            self.obs.snapshots.values(), key=lambda snap: (snap[1], snap[2])
        ):
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = [dict(r) for r in conn.execute(sql).fetchmany(3)]
            except sqlite3.Error as err:
                rows = [{"error": str(err)}]
            finally:
                conn.close()
            if rows:
                return {
                    "run": run,
                    "snapshot": index,
                    "pre_probe": index == 0,
                    "request": request,
                    "rows": _jsonable(rows),
                }
        return None

    # -- machines --

    def machines(self) -> list[Proposal]:
        out: list[Proposal] = []
        statics: dict[tuple[str, str], list[TransitionMap]] = {}
        for tmap in self.hints.transitions:
            table = self.schema_name(tmap.table)
            if table is not None:
                statics.setdefault((table, tmap.column), []).append(tmap)
        for table in sorted(self.tables):
            schema = self.tables[table]
            for col, values in schema.one_of:
                out.extend(self.machine(table, col, values, statics.get((table, col), [])))
        return out

    def machine(
        self, table: str, col: str, values: tuple[str, ...], maps: list[TransitionMap]
    ) -> list[Proposal]:
        sql_table = self.sql.get(table, table)
        stored = dict(self.tables[table].stored_spellings()).get(col)
        to_value = {s: v for v, s in stored} if stored else {}
        names = self.enum_names(table, col, values)

        def value(spelling: Any) -> str | None:
            if spelling in values:
                return spelling
            if spelling in to_value:
                return to_value[spelling]
            return names.get(spelling)

        moves = [
            {**m, "from": value(m["from"]), "to": value(m["to"])}
            for m in self.obs.moves.get((sql_table, col), [])
        ]
        created = [
            {**c, "state": value(c["state"])} for c in self.obs.created.get((sql_table, col), [])
        ]
        edges = sorted({(m["from"], m["to"]) for m in moves if m["from"] and m["to"]})
        initial = tuple(sorted({c["state"] for c in created if c["state"]}))
        evidence = {
            "transitions": _jsonable(moves[:20]),
            "created": _jsonable(created[:SAMPLE]),
            "transitions_seen": len(moves),
            "rows_created": len(created),
        }
        for tmap in maps:
            static_edges = []
            unknown = []
            for src, dsts in tmap.edges.items():
                for dst in dsts:
                    a, b = value(src), value(dst)
                    if a is None or b is None:
                        unknown.append(f"{src}->{dst}")
                    else:
                        static_edges.append((a, b))
            static_initial = tuple(v for s in tmap.initial if (v := value(s)) is not None)
            label = f"static: transition map {tmap.source}".rstrip()
            text = f"{table}.{col}: " + ", ".join(f"{a}->{b}" for a, b in static_edges)
            if unknown:
                self.refuted.append(Refuted("machine", text, label, {"unknown_states": unknown}))
                continue
            outside = [m for m in moves if (m["from"], m["to"]) not in set(static_edges)]
            bad_start = [c for c in created if static_initial and c["state"] not in static_initial]
            if outside or bad_start:
                self.refuted.append(
                    Refuted(
                        "machine",
                        text,
                        label,
                        _jsonable({"moves": outside[:3], "created": bad_start[:3]}),
                    )
                )
                continue
            start = static_initial or initial
            source = label if static_initial or not initial else f"{label}; observed initial states"
            exercised = sorted(set(static_edges) & set(edges))
            machine = Machine(table, col, tuple(sorted(set(static_edges))), tuple(sorted(start)))
            ev = {**evidence, "edges_exercised": [list(e) for e in exercised]}
            return [Proposal("machine", machine, ev, len(moves), source)]
        if not edges or "machine" not in self.templates:
            return []
        machine = Machine(table, col, tuple(edges), initial)
        return [
            Proposal(
                "machine",
                machine,
                evidence,
                len(moves),
                f"observed: {table}.{col} transitions between snapshots",
            )
        ]

    def enum_names(self, table: str, col: str, values: tuple[str, ...]) -> dict[str, str]:
        f = self.field(table, col)
        if f is None or not f.choice_names:
            return {}
        return {
            n: str(v) for n, v in zip(f.choice_names, f.choices, strict=False) if str(v) in values
        }


def _key_of(schema: Schema) -> str | None:
    for key in schema.keys:
        if len(key) == 1:
            return key[0]
    return "id" if "id" in schema.names() else None


def _num_name(value: float) -> str:
    return str(value).replace("-", "neg").replace(".", "_")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "rule"


def _evidence(rows: Sequence[_Written]) -> dict[str, Any]:
    runs = list(dict.fromkeys(w.run for w in rows))
    requests = [w.request for w in rows if w.request is not None]
    unique_requests = list({_row_key(r): r for r in requests}.values())
    return {
        "rows": _jsonable([w.row for w in rows[:SAMPLE]]),
        "runs": runs[:SAMPLE],
        "requests": _jsonable(unique_requests[:SAMPLE]),
        "app_rows": len(rows),
    }


# ---- translating source conditions ------------------------------------------------------------

_SQL_TOKEN = re.compile(
    r"""\s*(?:
        (?P<num>\d+(?:\.\d+)?)
      | (?P<str>'(?:[^']|'')*')
      | (?P<qid>"[^"]+"|`[^`]+`|\[[^\]]+\])
      | (?P<id>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)
      | (?P<op><>|!=|<=|>=|==|=|<|>|\(|\)|,|\+|-|\*|/)
    )""",
    re.VERBOSE,
)
_SQL_WORDS = {"and": "and", "or": "or", "not": "not", "in": "in", "true": "True", "false": "False"}


def _sql_to_expr(sql: str, table: str, schema: Schema) -> str | None:
    """A CHECK condition's SQL as an invariant row rule over ``table``, or None."""
    text = sql.strip()
    out: list[str] = []
    pos = 0
    tokens: list[tuple[str, str]] = []
    while pos < len(text):
        m = _SQL_TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            return None
        pos = m.end()
        kind = m.lastgroup or ""
        tokens.append((kind, m.group(kind)))
    i = 0
    while i < len(tokens):
        kind, tok = tokens[i]
        if kind in ("id", "qid"):
            name = tok.strip('"`[]').split(".")[-1]
            word = name.lower()
            if kind == "id" and word == "is":
                # <column> IS [NOT] NULL: rewritten around the column already emitted
                nxt = [t.lower() for _, t in tokens[i + 1 : i + 3]]
                if not out or not out[-1].startswith(f"{table}."):
                    return None
                col = out.pop()
                if nxt[:1] == ["null"]:
                    out.append(f"is_null({col})")
                    i += 2
                    continue
                if nxt == ["not", "null"]:
                    out.append(f"not is_null({col})")
                    i += 3
                    continue
                return None
            if kind == "id" and word in _SQL_WORDS:
                if word == "not" and i + 1 < len(tokens) and tokens[i + 1][1].lower() == "in":
                    out.append("not in")
                    i += 2
                    continue
                out.append(_SQL_WORDS[word])
            elif name in schema.names():
                out.append(f"{table}.{name}")
            else:
                return None
        elif kind == "str":
            out.append(json.dumps(tok[1:-1].replace("''", "'")))
        elif kind == "op":
            out.append({"=": "==", "<>": "!="}.get(tok, tok))
        else:
            out.append(tok)
        i += 1
    expr = " ".join(out).replace("( ", "(").replace(" )", ")").replace(" ,", ",")
    try:
        ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    return expr


_ROW_NAMES = ("self", "obj", "instance", "attrs", "data", "validated_data", "values", "target")


class _ToColumns(ast.NodeTransformer):
    def __init__(self, table: str, schema: Schema, column: str | None) -> None:
        self.table = table
        self.schema = schema
        self.column = column
        self.ok = True

    def col(self, name: str, node: ast.AST) -> ast.AST:
        if name not in self.schema.names():
            self.ok = False
            return node
        return ast.Attribute(ast.Name(self.table, ast.Load()), name, ast.Load())

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if isinstance(node.value, ast.Name) and node.value.id in _ROW_NAMES:
            return self.col(node.attr, node)
        self.ok = False
        return node

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        key = node.slice
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in _ROW_NAMES
            and isinstance(key, ast.Constant)
            and isinstance(key.value, str)
        ):
            return self.col(key.value, node)
        self.ok = False
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "get"
            and isinstance(f.value, ast.Name)
            and f.value.id in _ROW_NAMES
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return self.col(node.args[0].value, node)
        self.ok = False
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == "value" and self.column is not None:
            return self.col(self.column, node)
        if node.id in ("True", "False", "None"):
            return node
        return self.col(node.id, node)

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        # x is None / x is not None -> is_null(x) / not is_null(x)
        if (
            len(node.ops) == 1
            and isinstance(node.ops[0], ast.Is | ast.IsNot)
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value is None
        ):
            inner = self.visit(node.left)
            call = ast.Call(ast.Name("is_null", ast.Load()), [inner], [])
            if isinstance(node.ops[0], ast.IsNot):
                return ast.UnaryOp(ast.Not(), call)
            return call
        return self.generic_visit(node)


def _py_to_expr(source: str, table: str, schema: Schema, column: str | None) -> str | None:
    """A validator's rejecting condition as an expression over ``table``, or None."""
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError:
        return None
    t = _ToColumns(table, schema, column)
    body = t.visit(tree.body)
    if not t.ok:
        return None
    return ast.unparse(body)


# ---- static transition maps -------------------------------------------------------------------


def transition_maps(root: Path | str, package: str, model: RepoModel) -> list[TransitionMap]:
    """Module-level dicts of the package mapping a state to the states it may move to
    (``{Status.A: {Status.B, ...}, ...}``; members or text), matched to the table column
    whose enum member names (or values) include every state named."""
    base = Path(root) / Path(*package.split("."))
    columns: list[tuple[TableModel, FieldSpec]] = [
        (tm, c) for tm in model.tables for c in tm.columns if c.choices
    ]
    found: list[TransitionMap] = []
    for path in sorted(base.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            target, value = _assignment(node)
            if target is None or not isinstance(value, ast.Dict) or not value.keys:
                continue
            edges = _edge_map(value)
            if edges is None:
                continue
            states = set(edges) | {s for dsts in edges.values() for s in dsts}
            for tm, col in columns:
                spellings = {str(v) for v in col.choices} | set(col.choice_names)
                if states <= spellings:
                    rel = path.relative_to(Path(root)).as_posix()
                    found.append(
                        TransitionMap(
                            tm.name,
                            col.name,
                            edges,
                            source=f"{rel}:{node.lineno} {target}",
                        )
                    )
                    break
    return found


def _assignment(node: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        return (t.id if isinstance(t, ast.Name) else None), node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None, None


def _state(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _edge_map(value: ast.Dict) -> dict[str, tuple[str, ...]] | None:
    edges: dict[str, tuple[str, ...]] = {}
    for k, v in zip(value.keys, value.values, strict=True):
        src = _state(k) if k is not None else None
        if src is None:
            return None
        if isinstance(v, ast.Set | ast.List | ast.Tuple):
            dsts = [_state(e) for e in v.elts]
        elif (
            isinstance(v, ast.Call)
            and isinstance(v.func, ast.Name)
            and v.func.id in ("set", "list", "tuple", "frozenset")
            and not v.args
        ):
            dsts = []
        else:
            return None
        if any(d is None for d in dsts):
            return None
        edges[src] = tuple(sorted(d for d in dsts if d is not None))
    return edges


# ---- the recorder ---------------------------------------------------------------------------


def _copy_db(src: Path, dst: Path) -> None:
    """A consistent copy of a live SQLite database (the backup API)."""
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


class RunRecorder:
    """One run being recorded: :meth:`after` snapshots the database after a request."""

    def __init__(self, recorder: Recorder, db: Path, label: str) -> None:
        self.db = Path(db)
        self.label = label
        self.dir = recorder.directory / label
        self.dir.mkdir(parents=True, exist_ok=True)
        self.snapshots: list[Path] = []
        self.requests: list[Mapping[str, Any] | None] = []
        self._snap()

    def _snap(self) -> None:
        path = self.dir / f"{len(self.snapshots)}.db"
        if self.db.exists():
            _copy_db(self.db, path)
        else:
            shutil.copyfile(self.db, path)
        self.snapshots.append(path)

    def after(self, request: Mapping[str, Any] | None) -> None:
        self.requests.append(request)
        self._snap()

    def run(self) -> Run:
        return Run(self.label, tuple(self.snapshots), tuple(self.requests))


class _Recorded:
    """An ASGI wrapper snapshotting the database after each HTTP request; every other
    attribute is the wrapped app's."""

    def __init__(self, app: Any, run: RunRecorder) -> None:
        self._app = app
        self._run = run

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        status: list[int] = []

        async def send_status(message: Any) -> None:
            if message.get("type") == "http.response.start":
                status.append(message.get("status"))
            await send(message)

        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send_status)
        finally:
            request = {"method": scope.get("method"), "path": scope.get("path")}
            if status:
                request["status"] = status[0]
            self._run.after(request)


class Recorder:
    """Records runs into ``directory``: :meth:`wrap` turns an ASGI app factory into one
    whose every app is a run (the database when the app is built, then after each HTTP
    request); :meth:`begin` starts a run for another transport."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._runs: list[RunRecorder] = []

    def begin(self, db: Path | str, label: str | None = None) -> RunRecorder:
        run = RunRecorder(self, Path(db), label or f"run-{len(self._runs) + 1:04d}")
        self._runs.append(run)
        return run

    def wrap(self, app_factory: Callable[[Path], Any]) -> Callable[[Path], Any]:
        def factory(db_path: Path) -> Any:
            app = app_factory(db_path)
            return _Recorded(app, self.begin(Path(db_path)))

        return factory

    @property
    def runs(self) -> tuple[Run, ...]:
        return tuple(r.run() for r in self._runs)


# ---- entry point ----------------------------------------------------------------------------


def mine(
    profile: Profile,
    runs: Iterable[Run],
    *,
    model: RepoModel | None = None,
    hints: MiningHints | None = None,
    min_support: int = 2,
    templates: Iterable[str] = TEMPLATES,
) -> MiningResult:
    """Mine invariants and state machines: static candidates from ``model`` and
    ``hints``, observed ones from the rows the app wrote in ``runs`` (``min_support``
    rows at least; ``templates`` the observed templates run, default all of
    :data:`TEMPLATES`), every one checked on every snapshot. Survivors are pending
    proposals, sorted by kind and text."""
    runs = list(runs)
    miner = _Miner(profile, runs, model, hints or MiningHints(), min_support, templates)
    candidates = miner.static() + miner.observed()
    proposals = miner.check(candidates) + miner.machines()
    seen: set[str] = set()
    unchecked = []
    for p in miner.unchecked:
        if p.id not in seen:
            seen.add(p.id)
            unchecked.append(p)
    proposals += unchecked
    proposals.sort(key=lambda p: (KINDS.index(p.kind), p.text))
    app_rows = sum(len(v) for v in miner.obs.written.values())
    return MiningResult(
        proposals,
        miner.refuted,
        snapshots=len(miner.obs.snapshots),
        runs=len(runs),
        app_rows=app_rows,
        dropped=miner.dropped,
    )


__all__ = [
    "TEMPLATES",
    "CheckConstraint",
    "MiningHints",
    "MiningResult",
    "Proposal",
    "Recorder",
    "Refuted",
    "Run",
    "RunRecorder",
    "TransitionMap",
    "Unique",
    "Validator",
    "mine",
    "transition_maps",
]
