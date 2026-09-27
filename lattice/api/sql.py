"""Compile relational pipelines and the API expression subset to parameterized SQLite.

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
always quoted. The SQL uses four deterministic functions that `register_functions`
installs on a connection (Unicode-correct lower/upper/strip, floored modulo).
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

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


# ---- values -------------------------------------------------------------------------


def sql_value(v: object) -> object:
    """A Python value as SQLite stores it (Bool 0/1, Date ISO text)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    return v


def decode(v: object, scalar: Scalar) -> object:
    """A raw SQLite value as the Lattice scalar (Date stays ISO text)."""
    if v is None:
        return None
    match scalar:
        case Scalar.INT:
            return int(v)  # type: ignore[call-overload]
        case Scalar.FLOAT:
            return float(v)  # type: ignore[arg-type]
        case Scalar.BOOL:
            return bool(v)
        case _:
            return v


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
) -> dict[str, object]:
    """The named parameters to execute `query` with."""
    out: dict[str, object] = {name: sql_value(v) for name, v in query.binds}
    for p in query.params:
        out[f"p_{p}"] = sql_value((params or {})[p])
    for bind, step, columns in query.rows:
        out[bind] = rows_json(columns, (results or {})[step])
    if query.user:
        out[USER_ID] = user_id
        out[IS_ADMIN] = int(is_admin)
    return out


def _lower(x: object) -> object:
    return x.lower() if isinstance(x, str) else x


def _upper(x: object) -> object:
    return x.upper() if isinstance(x, str) else x


def _strip(x: object) -> object:
    return x.strip() if isinstance(x, str) else x


def _mod(a: object, b: object) -> object:
    if a is None or b is None or b == 0:
        return None
    return a % b  # type: ignore[operator]


def register_functions(conn: sqlite3.Connection) -> None:
    """Install the deterministic functions the compiled SQL calls."""
    conn.create_function("lattice_lower", 1, _lower, deterministic=True)
    conn.create_function("lattice_upper", 1, _upper, deterministic=True)
    conn.create_function("lattice_strip", 1, _strip, deterministic=True)
    conn.create_function("lattice_mod", 2, _mod, deterministic=True)


def q(name: str) -> str:
    """A quoted SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


# ---- the builder -------------------------------------------------------------------


@dataclass
class _Builder:
    rels: Mapping[str, Rel]
    # rel id -> (step id, columns): rels already materialized, read back as rows
    materialized: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    ctes: list[tuple[str, str]] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    params: list[str] = field(default_factory=list)
    binds: list[tuple[str, object]] = field(default_factory=list)
    rows: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    row_binds: dict[tuple[str, tuple[str, ...]], str] = field(default_factory=dict)
    user: bool = False

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
        self.binds.append((name, sql_value(v)))
        return ":" + name

    def param(self, name: str) -> str:
        if name not in self.params:
            self.params.append(name)
        return f":p_{name}"

    def value(self, v: int | ParamRef) -> str:
        """A page limit/offset: a literal int or an endpoint parameter."""
        if isinstance(v, ParamRef):
            return self.param(v.name)
        return self.lit(v)

    def user_ref(self) -> tuple[str, str]:
        self.user = True
        return f":{USER_ID}", f":{IS_ADMIN}"

    def rows_cte(self, step: str, columns: tuple[str, ...]) -> str:
        """A CTE over a step's materialized rows (one JSON parameter)."""
        key = (step, columns)
        if key in self.row_binds:
            return self.row_binds[key]
        bind = f"__r{len(self.rows)}"
        self.rows.append((bind, step, columns))
        cols = ", ".join(
            f"json_extract(j.value, '$[{i}]') AS {q(c)}" for i, c in enumerate(columns)
        )
        sep = ", " if cols else ""
        name = self._add(f"SELECT {cols}{sep}j.key AS {ORD} FROM json_each(:{bind}) AS j")
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
                    ordexpr = f"ROW_NUMBER() OVER (ORDER BY {', '.join(q(c) for c in order)})"
                else:
                    ordexpr = "rowid"
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
                return (
                    f"SELECT {listed}, ROW_NUMBER() OVER (ORDER BY {keys}, {ORD}) AS {ORD} "
                    f"FROM {ins[0]}"
                )
            case "limit":
                return f"SELECT {keep} FROM {ins[0]} ORDER BY {ORD} LIMIT {self.lit(int(p['n']))}"
            case "page":
                limit = self.value(p["limit"])
                offset = self.value(p.get("offset", 0))
                return f"SELECT {keep} FROM {ins[0]} ORDER BY {ORD} LIMIT {limit} OFFSET {offset}"
            case _:
                raise LatticeInternalError(f"no SQL for op '{rel.op}'")

    def _join(self, rel: Rel, left: str, right: str) -> str:
        on: tuple[str, ...] = tuple(rel.params["on"])
        how = rel.params.get("how", "inner")
        if how not in ("inner", "left"):
            raise LatticeInternalError(f"unsupported join how={how!r}")
        left_cols = set(self.cols_of(rel.inputs[0]))
        listed = [f"{'l' if c in on or c in left_cols else 'r'}.{q(c)}" for c in rel.schema.names()]
        cond = " AND ".join(f"l.{q(c)} = r.{q(c)}" for c in on) or "1"
        kind = "LEFT JOIN" if how == "left" else "JOIN"
        return (
            f"SELECT {', '.join(listed)}, ROW_NUMBER() OVER (ORDER BY l.{ORD}, r.{ORD}) "
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
                return q(name)
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

    @staticmethod
    def _binop(a: str, op: ast.operator, b: str) -> str:
        match op:
            case ast.Add():
                return f"({a} + {b})"
            case ast.Sub():
                return f"({a} - {b})"
            case ast.Mult():
                return f"({a} * {b})"
            case ast.Div():
                return f"(CAST({a} AS REAL) / {b})"  # SQLite: x / 0 is NULL
            case ast.Mod():
                return f"lattice_mod({a}, {b})"
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
        kw = {k.arg: k.value for k in node.keywords}
        build = _FN.get(name)
        if build is None:
            raise LatticeInternalError(f"fn.{name} has no SQL translation")
        return build(self, x, kw)


_CMP: Mapping[type, str] = {
    ast.Eq: "=",
    ast.NotEq: "<>",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}


def _date_part(fmt: str) -> Callable[[_Builder, str, Mapping[str | None, ast.expr]], str]:
    return lambda b, x, k: f"CAST(strftime('{fmt}', {x}) AS INTEGER)"


def _starts_with(b: _Builder, x: str, k: Mapping[str | None, ast.expr]) -> str:
    prefix = b.expr(k["prefix"])
    return f"(substr({x}, 1, length({prefix})) = {prefix})"


_FN: Mapping[str, Callable[[_Builder, str, Mapping[str | None, ast.expr]], str]] = {
    "lower": lambda b, x, k: f"lattice_lower({x})",
    "upper": lambda b, x, k: f"lattice_upper({x})",
    "strip": lambda b, x, k: f"lattice_strip({x})",
    "len": lambda b, x, k: f"length({x})",
    "contains": lambda b, x, k: f"(instr({x}, {b.expr(k['sub'])}) > 0)",
    "starts_with": _starts_with,
    "year": _date_part("%Y"),
    "month": _date_part("%m"),
    "day": _date_part("%d"),
}


# ---- entry points ------------------------------------------------------------------


def expr_sql(node: ast.expr) -> SqlExpr:
    """The SQL of one expression (its values as bind names), for plans and reports."""
    return _Builder({}).expr(node)


def compile_select(
    rels: Mapping[str, Rel],
    target: str,
    *,
    materialized: Mapping[str, tuple[str, tuple[str, ...]]] | None = None,
    extra: Sequence[tuple[str, ast.expr]] = (),
) -> SqlQuery:
    """ONE SELECT returning `target`'s rows in order: its schema's columns, then one
    column per `extra` (name, expression over target's columns)."""
    b = _Builder(rels, dict(materialized or {}))
    source = b.cte(target)
    cols = b.cols_of(target)
    listed = [q(c) for c in cols] + [f"{b.expr(e)} AS {q(n)}" for n, e in extra]
    return b.query(f"SELECT {', '.join(listed)} FROM {source} ORDER BY {ORD}")


def compile_insert(
    table: str,
    values: Sequence[tuple[str, ast.expr]],
    returning: Sequence[str],
    *,
    owner: str | None = None,
) -> SqlQuery:
    """INSERT one row (values are column-free expressions) RETURNING `returning`.
    With `owner`, that column is set to the caller's id."""
    b = _Builder({})
    cols = [q(c) for c, _ in values]
    exprs = [b.expr(e) for _, e in values]
    if owner is not None:
        uid, _ = b.user_ref()
        cols.append(q(owner))
        exprs.append(uid)
    ret = ", ".join(q(c) for c in returning)
    if cols:
        listed, vals = ", ".join(cols), ", ".join(exprs)
        text = f"INSERT INTO {q(table)} ({listed}) VALUES ({vals}) RETURNING {ret}"
    else:
        text = f"INSERT INTO {q(table)} DEFAULT VALUES RETURNING {ret}"
    return b.query(text)


def compile_update(
    table: str,
    key: Sequence[str],
    source: tuple[str, tuple[str, ...]],
    values: Sequence[tuple[str, ast.expr]],
) -> SqlQuery:
    """UPDATE the rows of `table` whose key matches a row of `source` (a materialized
    step: (step id, columns)); each value is evaluated against that source row.
    RETURNING the key and the updated columns."""
    b = _Builder({})
    src = b.rows_cte(*source)
    keys = [f"{q(k)} AS {q(f'__k{i}')}" for i, k in enumerate(key)]
    vals = [f"{b.expr(e)} AS {q(f'__v{i}')}" for i, (_, e) in enumerate(values)]
    sets = ", ".join(f"{q(c)} = s.{q(f'__v{i}')}" for i, (c, _) in enumerate(values))
    match = " AND ".join(f"{q(table)}.{q(k)} = s.{q(f'__k{i}')}" for i, k in enumerate(key))
    ret = ", ".join(q(c) for c in [*key, *(c for c, _ in values)])
    return b.query(
        f"UPDATE {q(table)} SET {sets} FROM (SELECT {', '.join(keys + vals)} FROM {src}) AS s "
        f"WHERE {match} RETURNING {ret}"
    )


def compile_delete(table: str, key: Sequence[str], source: tuple[str, tuple[str, ...]]) -> SqlQuery:
    """DELETE the rows of `table` whose key matches a row of `source`."""
    b = _Builder({})
    src = b.rows_cte(*source)
    cols = ", ".join(q(k) for k in key)
    target = f"({cols})" if len(key) > 1 else cols
    return b.query(f"DELETE FROM {q(table)} WHERE {target} IN (SELECT {cols} FROM {src})")


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
