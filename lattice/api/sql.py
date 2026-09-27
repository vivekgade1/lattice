"""Compile relational pipelines and the API expression subset to parameterized SQL.

A pipeline is a DAG of `Rel` nodes — the tabular table ops (where, derive, select,
rename, group, join, sort, limit) plus the API read ops (fetch, owned, page), the
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
from lattice.check.astutil import literal_value
from lattice.runtime import LatticeInternalError
from lattice.typesys import Scalar, Schema

SqlExpr = str  # a SQL expression fragment

ORD = "__ord"  # hidden row-order column carried by every CTE
USER_ID = "__user_id"  # bind name: the caller's id
IS_ADMIN = "__is_admin"  # bind name: 1 when the caller has the admin role


class ParamRef(ast.expr):
    """An endpoint parameter inside a lowered expression (bound as `:p_<name>`)."""

    _fields = ("name",)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


@dataclass(frozen=True)
class SqlQuery:
    """One parameterized statement.

    `params`: endpoint parameters referenced (bound as `:p_<name>`); `binds`: literal
    and constant values (bind name -> value); `rows`: materialized step results
    referenced (bind name, step id, columns), each bound as a JSON array of rows;
    `user`: whether `:__user_id` / `:__is_admin` are referenced."""

    text: str
    params: tuple[str, ...] = ()
    binds: tuple[tuple[str, object], ...] = ()
    rows: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    user: bool = False


@dataclass(frozen=True)
class Rel:
    """One relational node. `op` is a tabular table op or fetch/owned/page/user/rows."""

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
    already read back as a step's rows (rel id -> (step id, columns))."""

    rels: Mapping[str, Rel]
    target: str
    materialized: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    extra: Sequence[tuple[str, ast.expr]] = ()


@dataclass(frozen=True)
class InsertWrite:
    """INSERT one row (column-free value expressions) returning `returning`; with
    `owner`, that column is set to the caller's id."""

    table: str
    values: Sequence[tuple[str, ast.expr]]
    returning: Sequence[str]
    owner: str | None = None


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
    dialect: Dialect = SQLITE,
) -> dict[str, object]:
    """The named parameters to execute `query` with."""
    out: dict[str, object] = {name: dialect.to_db(v) for name, v in query.binds}
    for p in query.params:
        out[f"p_{p}"] = dialect.to_db((params or {})[p])
    for bind, step, columns in query.rows:
        out[bind] = rows_json(columns, (results or {})[step])
    if query.user:
        out[USER_ID] = user_id
        out[IS_ADMIN] = int(is_admin)
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

    def q(self, name: str) -> str:
        return self.d.quote(name)

    # ---- output ------------------------------------------------------------------

    def query(self, final: str) -> SqlQuery:
        text = final
        if self.ctes:
            text = "WITH " + ",\n".join(f"{n} AS ({s})" for n, s in self.ctes) + "\n" + final
        return SqlQuery(text, tuple(self.params), tuple(self.binds), tuple(self.rows), self.user)

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
            case "owned":
                uid, admin = self.user_ref()
                admin = self.d.flag(admin)
                if "via" in p:
                    # owned through a parent: the foreign key names a parent row the
                    # caller owns (a subquery of the same statement)
                    fk, parent, key, owner = p["via"]
                    mine = f"SELECT {q(key)} FROM {q(parent)} WHERE {q(owner)} = {uid}"
                    return f"SELECT {keep} FROM {ins[0]} WHERE ({admin} OR {q(fk)} IN ({mine}))"
                return f"SELECT {keep} FROM {ins[0]} WHERE ({admin} OR {q(p['owner'])} = {uid})"
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
                flags = [desc] * len(by) if isinstance(desc, bool) else list(desc)
                keys = ", ".join(
                    f"({q(c)} IS NULL), {q(c)}{' DESC' if d else ''}"
                    for c, d in zip(by, flags, strict=True)
                )
                listed = ", ".join(q(c) for c in cols)
                rownum = self.d.row_number(f"{keys}, {ORD}")
                return f"SELECT {listed}, {rownum} AS {ORD} FROM {ins[0]}"
            case "limit":
                return f"SELECT {keep} FROM {ins[0]} ORDER BY {ORD} LIMIT {self.lit(int(p['n']))}"
            case "page":
                limit = self.value(p["limit"])
                offset = self.value(p.get("offset", 0))
                return f"SELECT {keep} FROM {ins[0]} ORDER BY {ORD} LIMIT {limit} OFFSET {offset}"
            case _:
                raise LatticeInternalError(f"no SQL for op '{rel.op}'")

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
            case ParamRef(name=name):
                return self.param(name)
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
        if len(node.args) != 1:
            raise LatticeInternalError(f"fn.{name} takes one argument")
        x = self.expr(node.args[0])
        # keyword arguments compile after the argument (bind numbering), each once
        kw = {str(k.arg): self.expr(k.value) for k in node.keywords}
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
    listed = [b.q(c) for c in cols] + [f"{b.expr(e)} AS {b.q(n)}" for n, e in pipeline.extra]
    return b.query(f"SELECT {', '.join(listed)} FROM {source} ORDER BY {ORD}")


def build_write(step: WriteSpec, dialect: Dialect) -> SqlQuery:
    """ONE INSERT/UPDATE/DELETE (the shared compiler behind `Dialect.compile_write`)."""
    b = _Builder({}, {}, dialect)
    q = b.q
    match step:
        case InsertWrite(table=table, values=values, returning=returning, owner=owner):
            cols = [q(c) for c, _ in values]
            exprs = [b.expr(e) for _, e in values]
            if owner is not None:
                uid, _ = b.user_ref()
                cols.append(q(owner))
                exprs.append(uid)
            ret = ", ".join(q(c) for c in returning)
            return b.query(dialect.insert(q(table), cols, exprs, ret))
        case UpdateWrite(table=table, key=key, source=source, values=values):
            src = b.rows_cte(*source)
            keys = [f"{q(k)} AS {q(f'__k{i}')}" for i, k in enumerate(key)]
            vals = [f"{b.expr(e)} AS {q(f'__v{i}')}" for i, (_, e) in enumerate(values)]
            sets = ", ".join(f"{q(c)} = s.{q(f'__v{i}')}" for i, (c, _) in enumerate(values))
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
    dialect: Dialect = SQLITE,
) -> SqlQuery:
    """ONE SELECT returning `target`'s rows in order: its schema's columns, then one
    column per `extra` (name, expression over target's columns)."""
    return dialect.compile_read(ReadPipeline(rels, target, dict(materialized or {}), extra))


def compile_insert(
    table: str,
    values: Sequence[tuple[str, ast.expr]],
    returning: Sequence[str],
    *,
    owner: str | None = None,
    dialect: Dialect = SQLITE,
) -> SqlQuery:
    """INSERT one row (values are column-free expressions) RETURNING `returning`.
    With `owner`, that column is set to the caller's id."""
    return dialect.compile_write(InsertWrite(table, values, returning, owner))


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


def compile_lookup(
    table: str, columns: Sequence[str], column: str, *, dialect: Dialect = SQLITE
) -> str:
    """SELECT `columns` of `table`'s rows whose `column` equals the one positional
    parameter (the runtime's authentication statement)."""
    q = dialect.quote
    cols = ", ".join(q(c) for c in columns)
    return f"SELECT {cols} FROM {q(table)} WHERE {q(column)} = {dialect.positional_bind()}"


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
