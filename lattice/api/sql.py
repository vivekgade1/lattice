"""Compile relational pipelines and the API expression subset to parameterized SQL.

A pipeline is a DAG of `Rel` nodes — the tabular table ops (where, derive, select,
rename, group, join, sort, limit) plus the API read ops (fetch, owned, mine, page), the
caller's row (`user`) and materialized rows of an earlier step (`rows`, bound as one
JSON parameter). `compile_select` turns the ancestors of one node into ONE statement:
a chain of CTEs ending in a single SELECT.

Semantics follow the tabular (polars) runtime exactly:

* Row order is explicit. Every CTE carries a hidden `__ord` column: the table key
  (or rowid) for fetch, the JSON array index for materialized rows, `MIN(__ord)`
  for a group (groups in order of first appearance), a fresh `ROW_NUMBER()` for a
  join (left order, then right order) and for a sort (the sort keys with nulls
  last, ties in input order — a stable sort). Limit/page/the final SELECT order by
  `__ord`, so pages are stable even without a sort.
* where() drops rows whose predicate is null (SQL WHERE does the same); null group
  keys form their own group; joins never match null keys.
* `/` is true division, null on a zero divisor; `%` is floored modulo (Python's,
  as polars), null on a zero divisor.
* agg.sum over no/only-null values is 0; agg.count() counts rows, agg.count(x) the
  non-null values; agg.at_min/at_max(x, of=o) take x on the row with the
  smallest/largest non-null o (ties: the earliest row), null if every o is null.
* Dates are ISO-8601 text; Bool is 0/1.

Every endpoint parameter, constant and literal is a bind parameter; identifiers are
always quoted.

The compiler is parameterized by a `lattice.api.backend.Dialect` (default: SQLite): it
builds the dialect-neutral CTE skeleton and asks the dialect for identifier quoting,
bind placeholders, the JSON-rows relation, row numbers, division/modulo, the `fn.*`
functions, INSERT/UPDATE ... RETURNING and value encoding. No backend-specific SQL text
is written here. The SQLite dialect's SQL uses four deterministic functions that
`register_functions` installs on a connection (Unicode-correct lower/upper/strip,
floored modulo).
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lattice.api.backend import SQLITE, Dialect
from lattice.catalog import NATIVE_NS
from lattice.check.api_info import UNSET
from lattice.check.astutil import literal_value
from lattice.runtime import LatticeInternalError
from lattice.typesys import Scalar, Schema

SqlExpr = str  # a SQL expression fragment

ORD = "__ord"  # hidden row-order column carried by every CTE
USER_ID = "__user_id"  # bind name: the caller's id
IS_ADMIN = "__is_admin"  # bind name: 1 when the caller has the admin role
NOW = "__now"  # bind name: fn.now(), the request's one current instant
TOTAL = "__total"  # page(total=True): the row count before limit/offset
IN_PAGE = "__in_page"  # page(total=True): 0 on the placeholder row of an empty page


def sent_bind(name: str) -> str:
    """Bind name of the 0/1 flag "optional parameter `name` was sent"."""
    return f"__sent_{name}"


class ParamRef(ast.expr):
    """An endpoint parameter inside a lowered expression (bound as `:p_<name>`).
    `optional`: its default is UNSET (an update() value that may be "not sent")."""

    _fields = ("name",)

    def __init__(self, name: str, optional: bool = False) -> None:
        super().__init__()
        self.name = name
        self.optional = optional


class OwnerIs(ast.expr):
    """`owner_is(user)` lowered: the row's owner (column `owner`, or through
    `via` = (foreign key, parent table, parent key, parent owner)) is the caller, or the
    caller is an Admin -- `owned()`'s predicate as a condition."""

    _fields = ()

    def __init__(self, owner: str | None, via: tuple[str, str, str, str] | None = None) -> None:
        super().__init__()
        self.owner = owner
        self.via = via


@dataclass(frozen=True)
class SqlQuery:
    """One parameterized statement.

    `params`: endpoint parameters referenced (bound as `:p_<name>`); `binds`: literal
    and constant values (bind name -> value); `rows`: materialized step results
    referenced (bind name, step id, columns), each bound as a JSON array of rows;
    `user`: whether `:__user_id` / `:__is_admin` are referenced; `now`: whether
    `:__now` (fn.now()) is; `sent`: optional parameters whose "was sent" flag
    (`sent_bind(name)`, 0/1) is referenced; `total`: the statement is a page read with
    its total (two trailing columns `__in_page`, `__total`; an empty page is one
    placeholder row with `__in_page` 0)."""

    text: str
    params: tuple[str, ...] = ()
    binds: tuple[tuple[str, object], ...] = ()
    rows: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    user: bool = False
    now: bool = False
    sent: tuple[str, ...] = ()
    total: bool = False


@dataclass(frozen=True)
class Rel:
    """One relational node. `op` is a tabular table op or fetch/owned/mine/page/user/rows."""

    id: str
    op: str
    inputs: tuple[str, ...]
    params: Mapping[str, Any]
    schema: Schema


StepRows = tuple[Sequence[str], Sequence[Sequence[object]]]  # (columns, raw rows)


# ---- what a dialect compiles -------------------------------------------------------


@dataclass(frozen=True)
class ReadPipeline:
    """A read: `target`'s rows in order (its schema's columns, then one column per
    `extra` (name, expression over target's columns)), `materialized` naming the rels
    already read back as a step's rows (rel id -> (step id, columns)). With
    `total_of` (the input of a page(total=True)), the statement also counts that
    relation's rows (see `SqlQuery.total`)."""

    rels: Mapping[str, Rel]
    target: str
    materialized: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    extra: Sequence[tuple[str, ast.expr]] = ()
    total_of: str | None = None


@dataclass(frozen=True)
class InsertWrite:
    """INSERT one row (column-free value expressions) returning `returning`; with
    `owner`, that column is set to the caller's id; with `generate` (an omitted Uuid
    key), that column is set to a fresh uuid4."""

    table: str
    values: Sequence[tuple[str, ast.expr]]
    returning: Sequence[str]
    owner: str | None = None
    generate: str | None = None


@dataclass(frozen=True)
class UpdateWrite:
    """UPDATE the rows of `table` whose key matches a row of `source` (a materialized
    step: (step id, columns)), returning the key and the updated columns."""

    table: str
    key: Sequence[str]
    source: tuple[str, tuple[str, ...]]
    values: Sequence[tuple[str, ast.expr]]


@dataclass(frozen=True)
class DeleteWrite:
    """DELETE the rows of `table` whose key matches a row of `source`."""

    table: str
    key: Sequence[str]
    source: tuple[str, tuple[str, ...]]


WriteSpec = InsertWrite | UpdateWrite | DeleteWrite


# ---- values -------------------------------------------------------------------------


def sql_value(v: object, dialect: Dialect = SQLITE) -> object:
    """A Python value as the store keeps it (SQLite: Bool 0/1, Date ISO text)."""
    return dialect.to_db(v)


def decode(v: object, scalar: Scalar, dialect: Dialect = SQLITE) -> object:
    """A raw stored value as the Lattice scalar (SQLite: Date stays ISO text)."""
    return dialect.from_db(v, scalar)


def rows_json(columns: Sequence[str], result: StepRows) -> str:
    """Rows of a step result, restricted to `columns`, as the JSON bound for `rows`."""
    cols, rows = result
    index = [list(cols).index(c) for c in columns]
    return json.dumps([[r[i] for i in index] for r in rows])


def bind_values(
    query: SqlQuery,
    *,
    params: Mapping[str, object] | None = None,
    results: Mapping[str, StepRows] | None = None,
    user_id: object = None,
    is_admin: bool = False,
    now: object = None,
    dialect: Dialect = SQLITE,
) -> dict[str, object]:
    """The named parameters to execute `query` with. An optional parameter that was not
    sent (UNSET) binds null, and its "sent" flag 0; `now` is the request's instant."""
    values = params or {}
    out: dict[str, object] = {name: dialect.to_db(v) for name, v in query.binds}
    for p in query.params:
        v = values[p]
        out[f"p_{p}"] = None if v is UNSET else dialect.to_db(v)
    for p in query.sent:
        out[sent_bind(p)] = int(values[p] is not UNSET)
    for bind, step, columns in query.rows:
        out[bind] = rows_json(columns, (results or {})[step])
    if query.user:
        out[USER_ID] = user_id
        out[IS_ADMIN] = int(is_admin)
    if query.now:
        out[NOW] = dialect.to_db(now)
    return out


def register_functions(conn: Any) -> None:
    """Install the deterministic functions the SQLite dialect's SQL calls."""
    SQLITE.register_functions(conn)


def q(name: str) -> str:
    """A quoted SQL identifier (the SQLite dialect's quoting)."""
    return SQLITE.quote(name)


# ---- the builder -------------------------------------------------------------------


@dataclass
class _Builder:
    rels: Mapping[str, Rel]
    # rel id -> (step id, columns): rels already materialized, read back as rows
    materialized: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    d: Dialect = SQLITE
    ctes: list[tuple[str, str]] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    params: list[str] = field(default_factory=list)
    binds: list[tuple[str, object]] = field(default_factory=list)
    rows: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    row_binds: dict[tuple[str, tuple[str, ...]], str] = field(default_factory=dict)
    user: bool = False
    now: bool = False
    sent: list[str] = field(default_factory=list)
    recursive: bool = False  # some CTE refers to itself (traverse)

    def q(self, name: str) -> str:
        return self.d.quote(name)

    # ---- output ------------------------------------------------------------------

    def query(self, final: str, *, total: bool = False) -> SqlQuery:
        text = final
        if self.ctes:
            ctes = ",\n".join(f"{n} AS ({s})" for n, s in self.ctes)
            text = f"{self.d.with_clause(self.recursive)} {ctes}\n{final}"
        return SqlQuery(
            text,
            tuple(self.params),
            tuple(self.binds),
            tuple(self.rows),
            self.user,
            self.now,
            tuple(self.sent),
            total,
        )

    def _add(self, sql: str) -> str:
        name = f"t{len(self.ctes)}"
        self.ctes.append((name, sql))
        return name

    # ---- bindings ------------------------------------------------------------------

    def lit(self, v: object) -> str:
        name = f"__l{len(self.binds)}"
        self.binds.append((name, self.d.to_db(v)))
        return self.d.bind(name)

    def param(self, name: str) -> str:
        if name not in self.params:
            self.params.append(name)
        return self.d.bind(f"p_{name}")

    def value(self, v: int | ParamRef) -> str:
        """A page limit/offset: a literal int or an endpoint parameter."""
        if isinstance(v, ParamRef):
            return self.param(v.name)
        return self.lit(v)

    def user_ref(self) -> tuple[str, str]:
        self.user = True
        return self.d.bind(USER_ID), self.d.bind(IS_ADMIN)

    def now_ref(self) -> str:
        """fn.now(): one bind for the whole request (every use is the same instant)."""
        self.now = True
        return self.d.bind(NOW)

    def sent_ref(self, name: str) -> str:
        """The 0/1 "optional parameter `name` was sent" flag, as a condition."""
        if name not in self.sent:
            self.sent.append(name)
        return self.d.flag(self.d.bind(sent_bind(name)))

    def owner_cond(
        self, owner: str | None, via: tuple[str, str, str, str] | None, *, admin: bool = True
    ) -> str:
        """The caller owns the row (directly, or through its parent row), or -- with
        `admin` -- is an Admin: owned()'s filter and owner_is()'s condition. Without
        `admin` it is mine()'s filter: the caller's own rows, for every role."""
        q = self.q
        uid, is_admin = self.user_ref()
        if via is not None:
            # owned through a parent: the foreign key names a parent row the caller
            # owns (a subquery of the same statement)
            fk, parent, key, parent_owner = via
            mine = f"SELECT {q(key)} FROM {q(parent)} WHERE {q(parent_owner)} = {uid}"
            own = f"{q(fk)} IN ({mine})"
        elif owner is None:
            raise LatticeInternalError("an ownership condition without an owner column")
        else:
            own = f"{q(owner)} = {uid}"
        return f"({self.d.flag(is_admin)} OR {own})" if admin else f"({own})"

    def rows_cte(self, step: str, columns: tuple[str, ...]) -> str:
        """A CTE over a step's materialized rows (one JSON parameter)."""
        key = (step, columns)
        if key in self.row_binds:
            return self.row_binds[key]
        bind = f"__r{len(self.rows)}"
        self.rows.append((bind, step, columns))
        name = self._add(self.d.rows_relation(bind, columns, ORD))
        self.row_binds[key] = name
        return name

    # ---- relations -------------------------------------------------------------------

    def cte(self, rel_id: str) -> str:
        if rel_id in self.names:
            return self.names[rel_id]
        if rel_id in self.materialized:
            step, columns = self.materialized[rel_id]
            name = self.rows_cte(step, columns)
        else:
            rel = self.rels.get(rel_id)
            if rel is None:
                raise LatticeInternalError(f"unknown relation '{rel_id}'")
            name = self._add(self._rel(rel))
        self.names[rel_id] = name
        return name

    def _rel(self, rel: Rel) -> str:
        q = self.q
        p = rel.params
        ins = [self.cte(i) for i in rel.inputs]
        cols = rel.schema.names()
        keep = ", ".join([*(q(c) for c in cols), ORD])
        match rel.op:
            case "fetch":
                order: tuple[str, ...] = tuple(p.get("order", ()))
                if len(order) == 1:
                    ordexpr = q(order[0])
                elif order:
                    ordexpr = self.d.row_number(", ".join(q(c) for c in order))
                else:
                    ordexpr = self.d.natural_order()
                listed = ", ".join(q(c) for c in cols)
                return f"SELECT {listed}, {ordexpr} AS {ORD} FROM {q(p['table'])}"
            case "user":
                uid, _ = self.user_ref()
                listed = ", ".join(q(c) for c in cols)
                return (
                    f"SELECT {listed}, 0 AS {ORD} FROM {q(p['table'])} WHERE {q(p['key'])} = {uid}"
                )
            case "rows":
                return f"SELECT {keep} FROM {self.rows_cte(p['step'], cols)}"
            case "where":
                return f"SELECT {keep} FROM {ins[0]} WHERE {self.expr(p['predicate'])}"
            case "owned" | "mine":
                cond = self.owner_cond(p.get("owner"), p.get("via"), admin=rel.op == "owned")
                return f"SELECT {keep} FROM {ins[0]} WHERE {cond}"
            case "derive":
                exprs = {name: self.expr(node) for name, node in p["columns"]}
                listed = ", ".join(f"{exprs[c]} AS {q(c)}" if c in exprs else q(c) for c in cols)
                return f"SELECT {listed}, {ORD} FROM {ins[0]}"
            case "select":
                return f"SELECT {keep} FROM {ins[0]}"
            case "rename":
                back = {new: old for old, new in p["mapping"]}
                listed = ", ".join(f"{q(back[c])} AS {q(c)}" if c in back else q(c) for c in cols)
                return f"SELECT {listed}, {ORD} FROM {ins[0]}"
            case "group":
                return self._group(ins[0], tuple(p["by"]), p["aggs"])
            case "join":
                return self._join(rel, ins[0], ins[1])
            case "sort":
                by: tuple[str, ...] = tuple(p["by"])
                desc = p["desc"]
                flags = list(desc) if isinstance(desc, tuple | list) else [desc] * len(by)
                nulls = p.get("nulls", "last")
                keys = ", ".join(
                    self._sort_key(q(c), d, nulls) for c, d in zip(by, flags, strict=True)
                )
                listed = ", ".join(q(c) for c in cols)
                rownum = self.d.row_number(f"{keys}, {ORD}")
                return f"SELECT {listed}, {rownum} AS {ORD} FROM {ins[0]}"
            case "window":
                return self._window(rel, ins[0])
            case "traverse":
                return self._traverse(rel, ins[0], ins[1])
            case "limit":
                return f"SELECT {keep} FROM {ins[0]} ORDER BY {ORD} LIMIT {self.lit(int(p['n']))}"
            case "page":
                limit = self.value(p["limit"])
                offset = self.value(p.get("offset", 0))
                return f"SELECT {keep} FROM {ins[0]} ORDER BY {ORD} LIMIT {limit} OFFSET {offset}"
            case _:
                raise LatticeInternalError(f"no SQL for op '{rel.op}'")

    def _sort_key(self, c: str, desc: bool | ast.expr, nulls: str) -> str:
        """The ORDER BY keys of one sort column: a null flag, then the value. A
        direction computed per request (`desc` an expression) orders by two keys, one
        for each direction, of which only the chosen one is not null (a null direction
        is ascending)."""
        if isinstance(desc, bool):
            first = nulls == "first" or (nulls == "smallest" and not desc)
            flag = f"({c} IS NOT NULL)" if first else f"({c} IS NULL)"
            return f"{flag}, {c}{' DESC' if desc else ''}"
        d = self.expr(desc)
        match nulls:
            case "last":
                flag = f"({c} IS NULL)"
            case "first":
                flag = f"({c} IS NOT NULL)"
            case "smallest":
                flag = f"(CASE WHEN {d} THEN ({c} IS NULL) ELSE ({c} IS NOT NULL) END)"
            case _:
                raise LatticeInternalError(f"unknown sort nulls= {nulls!r}")
        return (
            f"{flag}, (CASE WHEN {d} THEN {c} END) DESC, (CASE WHEN {d} THEN NULL ELSE {c} END)"
        )

    def _join(self, rel: Rel, left: str, right: str) -> str:
        q = self.q
        on: tuple[str, ...] = tuple(rel.params["on"])
        # on={"a": "b"}: the right key columns have other names; the output keeps
        # the left key columns only
        right_on: tuple[str, ...] = tuple(rel.params.get("right_on", on))
        how = rel.params.get("how", "inner")
        if how not in ("inner", "left"):
            raise LatticeInternalError(f"unsupported join how={how!r}")
        left_cols = set(self.cols_of(rel.inputs[0]))
        listed = [f"{'l' if c in on or c in left_cols else 'r'}.{q(c)}" for c in rel.schema.names()]
        pairs = zip(on, right_on, strict=True)
        cond = " AND ".join(f"l.{q(a)} = r.{q(b)}" for a, b in pairs) or self.d.true_condition()
        kind = "LEFT JOIN" if how == "left" else "JOIN"
        rownum = self.d.row_number(f"l.{ORD}, r.{ORD}")
        return (
            f"SELECT {', '.join(listed)}, {rownum} "
            f"AS {ORD} FROM {left} AS l {kind} {right} AS r ON {cond}"
        )

    def _window(self, rel: Rel, source: str) -> str:
        """window(): one window function per new column, over the partition `by`,
        ordered by `order` (nulls last) and then by the input order (`__ord`: tied rows
        keep their input order, as polars' stable sort). rank/dense_rank order by
        `order` alone, so tied rows are peers. The rows keep the input order."""
        q = self.q
        p = rel.params
        partition = ", ".join(q(c) for c in p["by"])
        keys = ", ".join(
            f"({q(c)} IS NULL), {q(c)}{' DESC' if d else ''}"
            for c, d in zip(p["order"], p["desc"], strict=True)
        )
        stable = f"{keys}, {ORD}" if keys else ORD
        new = {name for name, _, _, _ in p["columns"]}
        listed = [q(c) for c in rel.schema.names() if c not in new]
        for name, func, arg, count in p["columns"]:
            x = None if arg is None else self.expr(arg)
            zero = 0.0 if rel.schema.get(name) is Scalar.FLOAT else 0
            listed.append(
                f"{self._win(func, x, count, zero, partition, keys, stable)} AS {q(name)}"
            )
        return f"SELECT {', '.join(listed)}, {ORD} FROM {source}"

    def _win(
        self,
        func: str,
        x: str | None,
        count: int | None,
        zero: float,
        partition: str,
        keys: str,
        stable: str,
    ) -> str:
        over = self.d.window
        running = self.d.rows_frame(None)
        match func:
            case "row_number":
                return over("ROW_NUMBER()", partition, stable, None)
            case "rank":
                return over("RANK()", partition, keys, None)
            case "dense_rank":
                return over("DENSE_RANK()", partition, keys, None)
            case "cum_count" if x is None:
                return over("COUNT(*)", partition, stable, running)
        if x is None:
            raise LatticeInternalError(f"win.{func} needs an argument")
        match func:
            case "lag" | "lead":
                return over(f"{func.upper()}({x}, {self.lit(count or 1)})", partition, stable, None)
            case "cum_sum":
                total = over(f"SUM({x})", partition, stable, running)
                return f"COALESCE({total}, {self.lit(zero)})"
            case "cum_count":
                return over(f"COUNT({x})", partition, stable, running)
            case "cum_min" | "cum_max":
                return over(f"{func[4:].upper()}({x})", partition, stable, running)
            case "mean" | "sum":
                if count is None or count < 1:
                    raise LatticeInternalError("a moving window needs rows >= 1")
                frame = self.d.rows_frame(self.lit(count - 1))
                if func == "mean":
                    return over(f"AVG({x})", partition, stable, frame)
                total = over(f"SUM({x})", partition, stable, frame)
                return f"COALESCE({total}, {self.lit(zero)})"
            case _:
                raise LatticeInternalError(f"win.{func} has no SQL translation")

    def _traverse(self, rel: Rel, edges: str, start: str) -> str:
        """traverse(): a recursive CTE walks (node, depth) pairs from the start rows'
        child values -- UNION drops repeated pairs and the depth counter stops at
        max_depth, so cycles end -- then MIN(depth) per node, and the edges rows of the
        reached nodes in (depth, child) order."""
        q = self.q
        p = rel.params
        parent, child, depth = q(p["parent"]), q(p["child"]), p["depth"]
        node, level = q("__node"), q("__depth")
        limit = self.value(p["max_depth"])
        walk = f"t{len(self.ctes)}"  # the name _add gives the recursive CTE below
        anchor = f"SELECT {child} AS {node}, 0 AS {level} FROM {start} WHERE {child} IS NOT NULL"
        if p["direction"] == "down":
            follow, to = parent, child
        elif p["direction"] == "up":
            follow, to = child, parent
        else:
            raise LatticeInternalError(f"unknown traverse direction {p['direction']!r}")
        step = (
            f"SELECT e.{to} AS {node}, w.{level} + 1 AS {level} FROM {walk} AS w "
            f"JOIN {edges} AS e ON e.{follow} = w.{node} "
            f"WHERE w.{level} < {limit} AND e.{to} IS NOT NULL"
        )
        self.recursive = True
        if self._add(f"{anchor} UNION {step}") != walk:
            raise LatticeInternalError("recursive CTE name mismatch")
        best = self._add(f"SELECT {node}, MIN({level}) AS {level} FROM {walk} GROUP BY {node}")
        listed = ", ".join(
            f"b.{level} AS {q(c)}" if c == depth else f"e.{q(c)}" for c in rel.schema.names()
        )
        rownum = self.d.row_number(f"b.{level}, e.{child}")
        return (
            f"SELECT {listed}, {rownum} AS {ORD} FROM {edges} AS e "
            f"JOIN {best} AS b ON e.{child} = b.{node}"
        )

    def cols_of(self, rel_id: str) -> tuple[str, ...]:
        """The visible columns of a relation (a materialized one: as materialized)."""
        if rel_id in self.materialized:
            return self.materialized[rel_id][1]
        return self.rels[rel_id].schema.names()

    def _group(
        self,
        source: str,
        by: tuple[str, ...],
        aggs: Sequence[tuple[str, str, ast.expr | None, ast.expr | None]],
    ) -> str:
        q = self.q
        windows: list[str] = []
        outs: list[str] = []
        partition = f"PARTITION BY {', '.join(q(c) for c in by)} " if by else ""
        for name, func, arg, order in aggs:
            if func in ("at_min", "at_max"):
                if arg is None or order is None:
                    raise LatticeInternalError(f"agg.{func} needs an argument and of=")
                x, o = self.expr(arg), self.expr(order)
                direction = " DESC" if func == "at_max" else ""
                w = f"__w{len(windows)}"
                windows.append(
                    f"FIRST_VALUE(CASE WHEN {o} IS NOT NULL THEN {x} END) OVER ("
                    f"{partition}ORDER BY ({o} IS NULL), {o}{direction}, {ORD} "
                    f"ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS {w}"
                )
                outs.append(f"MIN({w}) AS {q(name)}")
            else:
                outs.append(f"{self._agg(func, arg)} AS {q(name)}")
        if windows:
            source = self._add(f"SELECT *, {', '.join(windows)} FROM {source}")
        keys = [q(c) for c in by]
        ordexpr = f"MIN({ORD})" if by else "0"
        select = ", ".join([*keys, *outs, f"{ordexpr} AS {ORD}"])
        grouping = f" GROUP BY {', '.join(keys)}" if by else ""
        return f"SELECT {select} FROM {source}{grouping}"

    def _agg(self, func: str, arg: ast.expr | None) -> str:
        if func == "count" and arg is None:
            return "COUNT(*)"
        if arg is None:
            raise LatticeInternalError(f"agg.{func} needs an argument")
        x = self.expr(arg)
        match func:
            case "sum":
                return f"COALESCE(SUM({x}), 0)"
            case "mean":
                return f"AVG({x})"
            case "min":
                return f"MIN({x})"
            case "max":
                return f"MAX({x})"
            case "count":
                return f"COUNT({x})"
            case "count_distinct":
                return f"COUNT(DISTINCT {x})"
            case _:
                raise LatticeInternalError(f"agg.{func} has no SQL translation")

    # ---- expressions -------------------------------------------------------------------

    def expr(self, node: ast.expr) -> SqlExpr:
        if isinstance(node, ast.UnaryOp):
            folded = literal_value(node)
            if folded is not None:
                node = ast.Constant(folded)
        match node:
            case ast.Constant(value=v) if isinstance(v, bool | int | float | str):
                return self.lit(v)
            case ast.Constant(value=None):  # update(t, c=None) / insert(T, c=None)
                return "NULL"
            case ParamRef(name=name):
                return self.param(name)
            case ast.Call(func=ast.Name(id="sent"), args=[ParamRef(name=name)]):
                return self.sent_ref(name)
            case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=str(name))]):
                return self.q(name)
            case ast.Call(func=ast.Name(id="when"), args=[cond], keywords=kws):
                k = {kw.arg: kw.value for kw in kws}
                if set(k) != {"then", "otherwise"}:
                    raise LatticeInternalError("when() without then=/otherwise=")
                return (
                    f"(CASE WHEN {self.expr(cond)} THEN {self.expr(k['then'])} "
                    f"ELSE {self.expr(k['otherwise'])} END)"
                )
            case ast.Call(func=ast.Name(id="coalesce"), args=[a, b]):
                return f"COALESCE({self.expr(a)}, {self.expr(b)})"
            case ast.Call(func=ast.Name(id="is_null"), args=[a]):
                return f"({self.expr(a)} IS NULL)"
            case ast.Call(func=ast.Attribute(value=ast.Name(id="fn"), attr=name)):
                return self._fn(name, node)
            case ast.Call(func=ast.Attribute(value=ast.Name(id=ns), attr=name), args=args) if (
                ns == NATIVE_NS
            ):
                return self.d.native(name, [self.expr(a) for a in args])
            case OwnerIs(owner=owner, via=via):
                return self.owner_cond(owner, via)
            case ast.BinOp(left=left, op=op, right=right):
                return self._binop(self.expr(left), op, self.expr(right))
            case ast.Compare(left=left, ops=[op], comparators=[right]):
                return f"({self.expr(left)} {_CMP[type(op)]} {self.expr(right)})"
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return f"(-{self.expr(operand)})"
            case ast.UnaryOp(op=ast.Invert(), operand=operand):
                return f"(NOT {self.expr(operand)})"
            case _:
                raise LatticeInternalError(f"cannot compile expression to SQL: {ast.dump(node)}")

    def _binop(self, a: str, op: ast.operator, b: str) -> str:
        match op:
            case ast.Add():
                return f"({a} + {b})"
            case ast.Sub():
                return f"({a} - {b})"
            case ast.Mult():
                return f"({a} * {b})"
            case ast.Div():
                return self.d.divide(a, b)
            case ast.Mod():
                return self.d.modulo(a, b)
            case ast.BitAnd():
                return f"({a} AND {b})"
            case ast.BitOr():
                return f"({a} OR {b})"
            case _:
                raise LatticeInternalError(f"unsupported operator {type(op).__name__}")

    def _fn(self, name: str, node: ast.Call) -> str:
        if name == "now" and not node.args and not node.keywords:
            return self.now_ref()
        if len(node.args) != (2 if name == "datetime_diff" else 1):
            raise LatticeInternalError(f"fn.{name} has the wrong number of arguments")
        x = self.expr(node.args[0])
        keywords = list(node.keywords)
        if name == "contains":  # ignore_case= is a literal: it picks the function
            flag = next((k for k in keywords if k.arg == "ignore_case"), None)
            if flag is not None:
                keywords.remove(flag)
                if literal_value(flag.value) is True:
                    name = "icontains"
        # keyword arguments compile after the argument (bind numbering), each once;
        # datetime_diff's second argument (the start) is passed as "start"
        kw = {str(k.arg): self.expr(k.value) for k in keywords}
        if name == "datetime_diff":
            kw["start"] = self.expr(node.args[1])
        out = self.d.function(name, x, kw)
        if out is None:
            raise LatticeInternalError(f"fn.{name} has no SQL translation")
        return out


_CMP: Mapping[type, str] = {
    ast.Eq: "=",
    ast.NotEq: "<>",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}


# ---- dialect implementations of compile_read / compile_write ------------------------


def build_read(pipeline: ReadPipeline, dialect: Dialect) -> SqlQuery:
    """ONE SELECT returning the pipeline target's rows in order (the shared compiler
    behind `Dialect.compile_read`)."""
    b = _Builder(pipeline.rels, dict(pipeline.materialized), dialect)
    source = b.cte(pipeline.target)
    cols = b.cols_of(pipeline.target)
    if pipeline.total_of is None:
        listed = [b.q(c) for c in cols] + [f"{b.expr(e)} AS {b.q(n)}" for n, e in pipeline.extra]
        return b.query(f"SELECT {', '.join(listed)} FROM {source} ORDER BY {ORD}")
    # page(total=True): the page's rows next to the row count of the page's input, in
    # the same statement. The count is a one-row relation LEFT JOINed to the page, so
    # an empty page (an offset past the end) still yields the total, on one
    # placeholder row whose __in_page is 0.
    counted = b.cte(pipeline.total_of)
    listed = [f"f.{b.q(c)}" for c in cols] + [f"{b.expr(e)} AS {b.q(n)}" for n, e in pipeline.extra]
    listed += [f"(f.{ORD} IS NOT NULL) AS {IN_PAGE}", f"n.{TOTAL}"]
    count = f"(SELECT COUNT(*) AS {TOTAL} FROM {counted}) AS n"
    return b.query(
        f"SELECT {', '.join(listed)} FROM {count} LEFT JOIN {source} AS f "
        f"ON {dialect.true_condition()} ORDER BY f.{ORD}",
        total=True,
    )


def build_write(step: WriteSpec, dialect: Dialect) -> SqlQuery:
    """ONE INSERT/UPDATE/DELETE (the shared compiler behind `Dialect.compile_write`)."""
    b = _Builder({}, {}, dialect)
    q = b.q
    match step:
        case InsertWrite(
            table=table, values=values, returning=returning, owner=owner, generate=generate
        ):
            cols = [q(c) for c, _ in values]
            exprs = [b.expr(e) for _, e in values]
            if owner is not None:
                uid, _ = b.user_ref()
                cols.append(q(owner))
                exprs.append(uid)
            if generate is not None:
                cols.append(q(generate))
                exprs.append(dialect.new_uuid())
            ret = ", ".join(q(c) for c in returning)
            return b.query(dialect.insert(q(table), cols, exprs, ret))
        case UpdateWrite(table=table, key=key, source=source, values=values):
            src = b.rows_cte(*source)
            keys = [f"{q(k)} AS {q(f'__k{i}')}" for i, k in enumerate(key)]
            vals = [f"{b.expr(e)} AS {q(f'__v{i}')}" for i, (_, e) in enumerate(values)]
            sets = ", ".join(_set(b, table, c, e, i) for i, (c, e) in enumerate(values))
            match = " AND ".join(f"{q(table)}.{q(k)} = s.{q(f'__k{i}')}" for i, k in enumerate(key))
            ret = ", ".join(q(c) for c in [*key, *(c for c, _ in values)])
            select = f"SELECT {', '.join(keys + vals)} FROM {src}"
            return b.query(dialect.update_from(q(table), sets, select, match, ret))
        case DeleteWrite(table=table, key=key, source=source):
            src = b.rows_cte(*source)
            cols_sql = ", ".join(q(k) for k in key)
            target = f"({cols_sql})" if len(key) > 1 else cols_sql
            return b.query(
                f"DELETE FROM {q(table)} WHERE {target} IN (SELECT {cols_sql} FROM {src})"
            )
        case _:
            raise LatticeInternalError(f"unknown write {step!r}")


def _set(b: _Builder, table: str, column: str, value: ast.expr, i: int) -> str:
    """One SET item of an update. An optional (UNSET-able) parameter's column keeps
    its value unless the request sent the parameter (a bound "sent" flag), so one
    precompiled statement serves every combination of sent fields."""
    q = b.q
    new = f"s.{q(f'__v{i}')}"
    if isinstance(value, ParamRef) and value.optional:
        new = f"CASE WHEN {b.sent_ref(value.name)} THEN {new} ELSE {q(table)}.{q(column)} END"
    return f"{q(column)} = {new}"


# ---- entry points ------------------------------------------------------------------


def expr_sql(node: ast.expr, dialect: Dialect = SQLITE) -> SqlExpr:
    """The SQL of one expression (its values as bind names), for plans and reports."""
    return _Builder({}, {}, dialect).expr(node)


def compile_select(
    rels: Mapping[str, Rel],
    target: str,
    *,
    materialized: Mapping[str, tuple[str, tuple[str, ...]]] | None = None,
    extra: Sequence[tuple[str, ast.expr]] = (),
    total_of: str | None = None,
    dialect: Dialect = SQLITE,
) -> SqlQuery:
    """ONE SELECT returning `target`'s rows in order: its schema's columns, then one
    column per `extra` (name, expression over target's columns). With `total_of`, also
    the row count of that relation (see `SqlQuery.total`)."""
    return dialect.compile_read(
        ReadPipeline(rels, target, dict(materialized or {}), extra, total_of)
    )


def compile_insert(
    table: str,
    values: Sequence[tuple[str, ast.expr]],
    returning: Sequence[str],
    *,
    owner: str | None = None,
    generate: str | None = None,
    dialect: Dialect = SQLITE,
) -> SqlQuery:
    """INSERT one row (values are column-free expressions) RETURNING `returning`.
    With `owner`, that column is set to the caller's id; with `generate`, that (Uuid
    key) column to a fresh uuid4."""
    return dialect.compile_write(InsertWrite(table, values, returning, owner, generate))


def compile_update(
    table: str,
    key: Sequence[str],
    source: tuple[str, tuple[str, ...]],
    values: Sequence[tuple[str, ast.expr]],
    *,
    dialect: Dialect = SQLITE,
) -> SqlQuery:
    """UPDATE the rows of `table` whose key matches a row of `source` (a materialized
    step: (step id, columns)); each value is evaluated against that source row.
    RETURNING the key and the updated columns."""
    return dialect.compile_write(UpdateWrite(table, key, source, values))


def compile_delete(
    table: str,
    key: Sequence[str],
    source: tuple[str, tuple[str, ...]],
    *,
    dialect: Dialect = SQLITE,
) -> SqlQuery:
    """DELETE the rows of `table` whose key matches a row of `source`."""
    return dialect.compile_write(DeleteWrite(table, key, source))


OUTBOX_ENDPOINT = "__ob_endpoint"  # bind names of the for_each outbox INSERT
OUTBOX_NATIVE = "__ob_native"
OUTBOX_TYPES = "__ob_types"
OUTBOX_ROWS = "__ob_rows"


def compile_outbox(
    table: str,
    endpoint: str,
    native: str,
    types: Sequence[str],
    source: tuple[str, tuple[str, ...]],
    *,
    dialect: Dialect = SQLITE,
) -> SqlQuery:
    """for_each: ONE INSERT of an outbox row per row of `source` (a materialized step:
    (step id, the argument columns, in the native's parameter order)), each with the
    endpoint, the native, the arguments' scalar `types`, and the request's instant.
    RETURNING (id, native, args), dispatched after COMMIT."""
    text = dialect.outbox_insert(
        table, OUTBOX_ROWS, OUTBOX_TYPES, OUTBOX_ENDPOINT, OUTBOX_NATIVE, NOW
    )
    binds = (
        (OUTBOX_ENDPOINT, endpoint),
        (OUTBOX_NATIVE, native),
        (OUTBOX_TYPES, json.dumps(list(types))),
    )
    return SqlQuery(text, binds=binds, rows=((OUTBOX_ROWS, source[0], source[1]),), now=True)


def compile_lookup(
    table: str,
    columns: Sequence[str],
    column: str,
    *,
    native: str | None = None,
    dialect: Dialect = SQLITE,
) -> str:
    """SELECT `columns` of `table`'s rows whose `column` equals the one positional
    parameter (the runtime's authentication statement) -- or, with `native`, equals
    that native applied to the parameter (a bearer token decoded to a key)."""
    q = dialect.quote
    cols = ", ".join(q(c) for c in columns)
    value = dialect.positional_bind()
    if native is not None:
        value = dialect.native(native, [value])
    return f"SELECT {cols} FROM {q(table)} WHERE {q(column)} = {value}"


def rels_from_plan(
    nodes: Sequence[Any], tables: Mapping[str, tuple[str, Schema]]
) -> dict[str, Rel]:
    """Relations for a tabular `lattice.ir.Plan`'s nodes, reading each load() path from
    the SQL table `tables[path]` = (table name, schema, whose keys give the row order)
    instead of a file. Write nodes are skipped."""
    rels: dict[str, Rel] = {}
    for node in nodes:
        if node.op == "write":
            continue
        if node.schema is None:
            raise LatticeInternalError(f"node '{node.id}' has no schema")
        if node.op == "load":
            table, schema = tables[node.params["path"]]
            order = schema.keys[0] if schema.keys else ()
            rels[node.id] = Rel(node.id, "fetch", (), {"table": table, "order": order}, node.schema)
        else:
            rels[node.id] = Rel(node.id, node.op, node.inputs, node.params, node.schema)
    return rels
