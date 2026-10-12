"""Invariants compiled to SQL that returns the rows violating them (layer C).

An invariant's `expr` is a restricted expression in Lattice/Python syntax, parsed with
`ast`. Tables are named by their profile schema name (`Product`, not `products`); the SQL
uses the profile's table names. Three forms are allowed:

- **row rule** over one table: comparisons (`==`, `!=`, `<`, `<=`, `>`, `>=`) of
  `Table.column`s, literals and arithmetic (`+ - * /`), joined by `and` / `or` / `not`,
  plus `is_null(Table.column)`, `Table.column in (...)` / `not in`, and a bare Bool
  column. A row violates the rule unless the rule is known true for it: a comparison
  with a NULL is unknown, so it violates (write `is_null(T.c) or ...` to allow NULL).
  The violating rows are returned whole (`SELECT t.*`).
- **aggregate equality**: `P.col == sum(<arithmetic over C's columns>, by="fk")`
  (either side), where `C.fk` references P's one-column key. A P row with no C rows
  sums to 0. The two sides are equal within a relative tolerance of 1e-9; a NULL
  `P.col` violates. Violating P rows are returned whole plus an `expected` column (the
  sum).
- **count bound**: `count(T, by="col") <op> n` (either side) with an int literal `n`.
  `by` may instead be a tuple of columns, `by=("a", "b")`: the group is keyed on all of
  them together. An optional `where=<row rule over T>` (the row rule grammar above, same
  table) restricts the rows counted, before grouping. Rows with NULL in `col` (or in any
  column of a tuple `by`) belong to no group. Violating groups are returned as (the `by`
  columns, `count`). Only groups that exist are checked: a lower bound never flags a
  value with no rows.

One-of columns whose values are stored under other spellings (`Schema.one_of_stored`)
compare the stored spelling: `Order.status == "pending"` compiles to `status =
'PENDING'`. A value outside the column's `one_of` is refused, and so is ordering such a
column (it would order the stored spellings). Uuid and DateTime columns, whose stored
spellings depend on the profile, compare only with columns of their own type.

**Dialects.** `compile_invariant(inv, profile, dialect)` writes the SQL of `sqlite` (the
default: the profile backend's quoting, `1`/`0` booleans), `postgres` (`"..."`, `TRUE`/
`FALSE`: a Bool column is a native boolean), `mysql` (backticks, `TRUE`/`FALSE`, a
backslash in a text literal doubled) or `clickhouse` (`"..."`, `true`/`false`, a
backslash doubled). The forms use no other dialect-specific SQL: no string
concatenation, no `LIMIT`, `IS NULL` only, `GROUP BY ... HAVING` with `COUNT(*)`.
MySQL compares text under the column's collation (case-insensitive by default), so a
one-of or text comparison there is case-insensitive.

`compile_invariant` raises `InvariantError` (codes as in `lattice.spec.validate`): S002
unknown table, S003 unknown column, S007 does not parse, S008 not an allowed form, S009
a type error.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from lattice.api.backend import dialect_for
from lattice.profile import Profile
from lattice.spec import engines as _engines
from lattice.spec.model import Invariant, tables_of
from lattice.typesys import NUMERIC, Scalar, Schema, unify

TOLERANCE = 1e-9

ALLOWED_FORMS = (
    "allowed forms: a row rule over one table (`Product.stock_quantity >= 0`, with "
    "comparisons, arithmetic, `and`/`or`/`not`, `is_null(Table.column)` and "
    "`Table.column in (...)`); an aggregate equality (`Order.total_amount == "
    'sum(OrderItem.quantity * OrderItem.price_at_purchase, by="order_id")`); a count '
    'bound (`count(Vendor, by="user_id") <= 1`, `by=` a column or a tuple of columns, '
    "with an optional `where=<row rule>` restricting the rows counted)"
)

_COMPARE_SQL: dict[type[ast.cmpop], str] = {
    ast.Eq: "=",
    ast.NotEq: "<>",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}
_FLIPPED: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.Eq,
    ast.NotEq: ast.NotEq,
    ast.Lt: ast.Gt,
    ast.LtE: ast.GtE,
    ast.Gt: ast.Lt,
    ast.GtE: ast.LtE,
}
_ARITH_SQL: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}


class InvariantError(ValueError):
    """An invariant that cannot be compiled: `code` is its lattice.spec.validate code."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(f"{message} (hint: {hint})" if hint else message)


def _form(message: str) -> InvariantError:
    return InvariantError("S008", message, ALLOWED_FORMS)


def _text(node: ast.AST) -> str:
    return ast.unparse(node)


class _Operand:
    """A compiled operand: its SQL, its scalar type, and, for a column, where it comes
    from (for a literal, its value)."""

    def __init__(
        self,
        sql: str,
        scalar: Scalar,
        *,
        column: tuple[str, str, Schema] | None = None,
        literal: object = None,
        is_literal: bool = False,
    ) -> None:
        self.sql = sql
        self.scalar = scalar
        self.column = column  # (schema name, column, schema)
        self.literal = literal
        self.is_literal = is_literal

    @property
    def label(self) -> str:
        if self.column is not None:
            return f"{self.column[0]}.{self.column[1]}"
        return self.sql

    def stored(self) -> dict[str, str] | None:
        """value -> stored spelling, for a one_of column stored differently."""
        if self.column is None:
            return None
        _, name, schema = self.column
        for column, pairs in schema.stored_spellings():
            if column == name:
                return dict(pairs)
        return None

    def choices(self) -> tuple[str, ...] | None:
        if self.column is None:
            return None
        return self.column[2].choices(self.column[1])


class _Compiler:
    def __init__(self, inv: Invariant, profile: Profile, dialect: str = "sqlite") -> None:
        self.inv = inv
        self.dialect = dialect
        self.tables, self.sql_names = tables_of(profile)
        # a server engine's SQL flags come from its descriptor (lattice.spec.engines)
        self.engine = None if dialect == "sqlite" else _engines.find(dialect)
        if dialect == "sqlite":
            backend = profile.api.backend if profile.api is not None else "sqlite"
            self.quote: Callable[[str], str] = dialect_for(backend).quote
            true, false = _SQLITE_BOOLS
        elif self.engine is not None and self.engine.name == dialect:
            self.quote = self.engine.quote_ident
            true, false = self.engine.sql.true_literal, self.engine.sql.false_literal
        else:
            raise ValueError(f"unknown SQL dialect {dialect!r}")
        self.true, self.false = true, false
        # a condition that is NULL (unknown) does not hold
        self.unknown = "0" if dialect == "sqlite" else false

    def text(self, value: str) -> str:
        return _sql_text(value, self.dialect)

    def num(self, sql: str) -> str:
        """A number as a float where the dialect mixes Decimal and Float only so
        (ClickHouse: the engine's ``float_cast``); as it is elsewhere."""
        if self.engine is None:
            return sql
        return _engines.fill(self.engine.sql.float_cast, x=sql)

    # ---- names -----------------------------------------------------------------------

    def table(self, name: str) -> Schema:
        schema = self.tables.get(name)
        if schema is None:
            known = ", ".join(sorted(self.tables))
            raise InvariantError(
                "S002",
                f"unknown table {name!r}",
                f"name a table by its profile schema name: {known}",
            )
        return schema

    def sql_table(self, name: str) -> str:
        return self.quote(self.sql_names.get(name, name))

    def column(self, table: str, column: str) -> Scalar:
        scalar = self.table(table).get(column)
        if scalar is None:
            known = ", ".join(self.table(table).names())
            raise InvariantError(
                "S003", f"unknown column {column!r} of {table}", f"columns of {table}: {known}"
            )
        return scalar

    def by_column(self, call: ast.Call, table: str) -> str:
        by = [kw for kw in call.keywords if kw.arg == "by"]
        others = [kw for kw in call.keywords if kw.arg != "by"]
        if len(by) != 1 or others:
            raise _form(f'{_text(call)} needs exactly one keyword, by="<column of {table}>"')
        value = by[0].value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            raise _form(f'{_text(call)}: by= must name a column as text, by="<column>"')
        self.column(table, value.value)
        return value.value

    # ---- operands --------------------------------------------------------------------

    def operand(self, node: ast.expr, alias: str, tables: set[str]) -> _Operand:
        """A value: a column, a literal, or arithmetic. `tables` collects the tables
        named."""
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name):
                raise _form(f"`{_text(node)}` is not a column: a column is written Table.column")
            table, name = node.value.id, node.attr
            scalar = self.column(table, name)
            tables.add(table)
            sql = f"{alias}.{self.quote(name)}"
            return _Operand(sql, scalar, column=(table, name, self.table(table)))
        if isinstance(node, ast.Constant):
            return self.literal(node.value, node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            inner = self.operand(node.operand, alias, tables)
            if inner.scalar not in NUMERIC:
                raise InvariantError(
                    "S009", f"`{_text(node)}`: a sign applies only to numbers", "use Int or Float"
                )
            sign = "-" if isinstance(node.op, ast.USub) else "+"
            return _Operand(f"({sign}{inner.sql})", inner.scalar)
        if isinstance(node, ast.BinOp) and type(node.op) in _ARITH_SQL:
            left = self.operand(node.left, alias, tables)
            right = self.operand(node.right, alias, tables)
            for side in (left, right):
                if side.scalar not in NUMERIC:
                    raise InvariantError(
                        "S009",
                        f"`{_text(node)}`: arithmetic on {side.label} ({side.scalar.value}); "
                        "arithmetic needs numeric operands",
                        "use Int or Float columns and literals",
                    )
            scalar = unify(left.scalar, right.scalar) or Scalar.FLOAT
            if isinstance(node.op, ast.Div):  # SQL divides ints as ints
                # the engine's divide template over its float casts (ClickHouse: no
                # Decimal-with-Float arithmetic there)
                template = _SQLITE_DIVIDE if self.engine is None else self.engine.sql.divide
                return _Operand(_engines.fill(template, a=self.num(left.sql),
                                              b=self.num(right.sql)), Scalar.FLOAT)  # fmt: skip
            op = _ARITH_SQL[type(node.op)]
            return _Operand(f"({left.sql} {op} {right.sql})", scalar)
        if isinstance(node, ast.Name):
            raise _form(f"`{node.id}` is not a column: a column is written Table.column")
        raise _form(f"`{_text(node)}` is not a value of an allowed form")

    def literal(self, value: object, node: ast.AST) -> _Operand:
        if value is None:
            raise _form("a comparison with None is never true: use is_null(Table.column)")
        if isinstance(value, bool):
            sql = self.true if value else self.false
            return _Operand(sql, Scalar.BOOL, literal=value, is_literal=True)
        if isinstance(value, int):
            return _Operand(str(value), Scalar.INT, literal=value, is_literal=True)
        if isinstance(value, float):
            return _Operand(repr(value), Scalar.FLOAT, literal=value, is_literal=True)
        if isinstance(value, str):
            return _Operand(self.text(value), Scalar.STR, literal=value, is_literal=True)
        raise _form(f"`{_text(node)}` is not an allowed literal (int, float, text, bool)")

    # ---- row rules -------------------------------------------------------------------

    def condition(self, node: ast.expr, alias: str, tables: set[str]) -> str:
        if isinstance(node, ast.BoolOp):
            joiner = " AND " if isinstance(node.op, ast.And) else " OR "
            return "(" + joiner.join(self.condition(v, alias, tables) for v in node.values) + ")"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return f"(NOT {self.condition(node.operand, alias, tables)})"
        if isinstance(node, ast.Call):
            return self.is_null(node, alias, tables)
        if isinstance(node, ast.Compare):
            return self.compare(node, alias, tables)
        if isinstance(node, ast.Attribute):
            value = self.operand(node, alias, tables)
            if value.scalar is not Scalar.BOOL:
                raise InvariantError(
                    "S009",
                    f"`{_text(node)}` is a {value.scalar.value} column, not a condition",
                    "compare it, e.g. Table.column > 0",
                )
            return f"({value.sql} = {'1' if self.dialect == 'sqlite' else self.true})"
        if isinstance(node, ast.Name):
            raise _form(f"`{node.id}` is not a column: a column is written Table.column")
        raise _form(f"`{_text(node)}` is not an allowed form")

    def is_null(self, node: ast.Call, alias: str, tables: set[str]) -> str:
        name = node.func.id if isinstance(node.func, ast.Name) else _text(node.func)
        if name != "is_null":
            raise _form(f"`{name}(...)` is not allowed here")
        if len(node.args) != 1 or node.keywords or not isinstance(node.args[0], ast.Attribute):
            raise _form("is_null takes one column: is_null(Table.column)")
        value = self.operand(node.args[0], alias, tables)
        return f"({value.sql} IS NULL)"

    def compare(self, node: ast.Compare, alias: str, tables: set[str]) -> str:
        if len(node.ops) != 1:
            raise _form(f"`{_text(node)}`: a chained comparison is not an allowed form")
        op, right_node = node.ops[0], node.comparators[0]
        left = self.operand(node.left, alias, tables)
        if isinstance(op, ast.In | ast.NotIn):
            return self.membership(node, left, right_node, isinstance(op, ast.NotIn))
        if type(op) not in _COMPARE_SQL:
            raise _form(f"`{_text(node)}`: use ==, !=, <, <=, >, >=, in or not in")
        if isinstance(right_node, ast.Constant) and right_node.value is None:
            raise _form(f"`{_text(node)}`: a comparison with None: use is_null(Table.column)")
        right = self.operand(right_node, alias, tables)
        ordered = not isinstance(op, ast.Eq | ast.NotEq)
        left_sql, right_sql = self.typed_pair(node, left, right, ordered)
        return f"({left_sql} {_COMPARE_SQL[type(op)]} {right_sql})"

    def typed_pair(
        self, node: ast.Compare, left: _Operand, right: _Operand, ordered: bool
    ) -> tuple[str, str]:
        """The two sides' SQL once their types are checked; a text literal compared with
        a one_of column becomes its stored spelling."""
        if unify(left.scalar, right.scalar) is None:
            raise InvariantError(
                "S009",
                f"`{_text(node)}`: cannot compare {left.label} ({left.scalar.value}) "
                f"with {right.label} ({right.scalar.value})",
                "compare values of the same type",
            )
        for side in (left, right):
            if ordered and side.scalar in (Scalar.UUID, Scalar.BOOL):
                raise InvariantError(
                    "S009",
                    f"`{_text(node)}`: {side.label} ({side.scalar.value}) has no order",
                    "use == or !=",
                )
            if ordered and side.stored() is not None:
                raise InvariantError(
                    "S009",
                    f"`{_text(node)}`: {side.label} is stored under other spellings, so "
                    "ordering it would order the stored spellings",
                    "use ==, != or in",
                )
        return self.spelled(node, left, right), self.spelled(node, right, left)

    def spelled(self, node: ast.AST, side: _Operand, other: _Operand) -> str:
        """`side`'s SQL; a text literal checked against (and stored as) `other`'s
        one_of spelling."""
        if not (side.is_literal and isinstance(side.literal, str)):
            return side.sql
        choices = other.choices()
        if choices is None:
            return side.sql
        if side.literal not in choices:
            raise InvariantError(
                "S009",
                f"`{_text(node)}`: {side.literal!r} is not a value of {other.label} "
                f"(one_of: {', '.join(choices)})",
                f"use one of {', '.join(choices)}",
            )
        stored = other.stored()
        return self.text(stored[side.literal]) if stored else side.sql

    def membership(self, node: ast.Compare, left: _Operand, right: ast.expr, negated: bool) -> str:
        if not isinstance(right, ast.Tuple | ast.List | ast.Set) or not right.elts:
            raise _form(f"`{_text(node)}`: `in` takes a non-empty tuple or list of literals")
        values: list[str] = []
        for element in right.elts:
            if not isinstance(element, ast.Constant):
                raise _form(f"`{_text(node)}`: `in` takes literals only")
            value = self.literal(element.value, element)
            check = ast.Compare(left=node.left, ops=[ast.Eq()], comparators=[element])
            values.append(self.typed_pair(check, left, value, ordered=False)[1])
        keyword = "NOT IN" if negated else "IN"
        return f"({left.sql} {keyword} ({', '.join(values)}))"

    # ---- the three forms -------------------------------------------------------------

    def compile(self) -> str:
        try:
            tree = ast.parse(self.inv.expr.strip(), mode="eval")
        except SyntaxError as exc:
            raise InvariantError(
                "S007",
                f"the expression does not parse: {exc.msg}",
                ALLOWED_FORMS,
            ) from None
        body = tree.body
        if isinstance(body, ast.Compare) and len(body.ops) == 1:
            left, right = body.left, body.comparators[0]
            for call, other, flip in ((left, right, False), (right, left, True)):
                kind = _aggregate(call)
                if kind == "count":
                    op = _FLIPPED[type(body.ops[0])] if flip else type(body.ops[0])
                    return self.count_bound(body, call, other, op)  # type: ignore[arg-type]
                if kind == "sum":
                    return self.sum_equality(body, call, other)  # type: ignore[arg-type]
        return self.row_rule(body)

    def row_rule(self, body: ast.expr) -> str:
        tables: set[str] = set()
        condition = self.condition(body, "t", tables)
        if len(tables) != 1:
            if not tables:
                raise _form(f"`{_text(body)}` names no column")
            raise _form(
                f"`{_text(body)}`: a row rule reads one table, not {', '.join(sorted(tables))}"
            )
        (table,) = tables
        return (
            f"SELECT t.* FROM {self.sql_table(table)} AS t "
            f"WHERE NOT COALESCE({condition}, {self.unknown})"
        )

    def count_bound(
        self, node: ast.Compare, call: ast.Call, bound: ast.expr, op: type[ast.cmpop]
    ) -> str:
        if op not in _COMPARE_SQL:
            raise _form(f"`{_text(node)}`: a count bound uses ==, !=, <, <=, > or >=")
        if not (
            isinstance(bound, ast.Constant)
            and isinstance(bound.value, int)
            and not isinstance(bound.value, bool)
        ):
            raise _form(f"`{_text(node)}`: a count bound compares count(...) with an int literal")
        if len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
            raise _form(f'`{_text(call)}`: write count(Table, by="<column>")')
        table = call.args[0].id
        self.table(table)
        by_cols, where_sql = self.count_keywords(call, table)
        cols = [self.quote(c) for c in by_cols]
        select_list = ", ".join(f"t.{c} AS {c}" for c in cols)
        group_list = ", ".join(f"t.{c}" for c in cols)
        null_guard = " AND ".join(f"t.{c} IS NOT NULL" for c in cols)
        where_clause = null_guard if where_sql is None else f"{null_guard} AND ({where_sql})"
        return (
            f"SELECT {select_list}, COUNT(*) AS {self.quote('count')} "
            f"FROM {self.sql_table(table)} AS t WHERE {where_clause} "
            f"GROUP BY {group_list} HAVING NOT (COUNT(*) {_COMPARE_SQL[op]} {bound.value})"
        )

    def count_keywords(self, call: ast.Call, table: str) -> tuple[list[str], str | None]:
        kws = {kw.arg: kw.value for kw in call.keywords}
        extra = set(kws) - {"by", "where"}
        if extra or "by" not in kws:
            raise _form(
                f'{_text(call)}: write count(Table, by="<column>") (a column or a tuple '
                'of columns), with an optional where=<row rule>'
            )
        by = self.by_value(kws["by"], call, table)
        where_sql: str | None = None
        if "where" in kws:
            tables: set[str] = set()
            where_sql = self.condition(kws["where"], "t", tables)
            other = tables - {table}
            if other:
                raise _form(
                    f"{_text(call)}: where= reads {table}, not {', '.join(sorted(other))}"
                )
        return by, where_sql

    def by_value(self, value: ast.expr, call: ast.Call, table: str) -> list[str]:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            self.column(table, value.value)
            return [value.value]
        if isinstance(value, ast.Tuple | ast.List):
            if not value.elts:
                raise _form(
                    f'{_text(call)}: by= needs at least one column, by=("<column>", ...)'
                )
            names: list[str] = []
            for elt in value.elts:
                if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                    raise _form(
                        f'{_text(call)}: by= columns must be text, by=("<column>", ...)'
                    )
                self.column(table, elt.value)
                names.append(elt.value)
            return names
        raise _form(
            f'{_text(call)}: by= must name a column as text, by="<column>", or a tuple of '
            'columns, by=("<column>", ...)'
        )

    def sum_equality(self, node: ast.Compare, call: ast.Call, other: ast.expr) -> str:
        if not isinstance(node.ops[0], ast.Eq):
            raise _form(f"`{_text(node)}`: an aggregate rule is an equality, P.column == sum(...)")
        if not isinstance(other, ast.Attribute):
            raise _form(f"`{_text(node)}`: compare sum(...) with a column, P.column == sum(...)")
        parent_tables: set[str] = set()
        parent = self.operand(other, "p", parent_tables)
        (parent_table,) = parent_tables
        if parent.scalar not in NUMERIC:
            raise InvariantError(
                "S009",
                f"`{_text(node)}`: {parent.label} is {parent.scalar.value}; a sum is "
                "compared with a numeric column",
                "use an Int or Float column",
            )
        if len(call.args) != 1:
            raise _form(f'`{_text(call)}`: write sum(<arithmetic>, by="<column>")')
        child_tables: set[str] = set()
        summed = self.operand(call.args[0], "c", child_tables)
        if len(child_tables) != 1:
            raise _form(f"`{_text(call)}`: sum(...) reads the columns of one table")
        (child,) = child_tables
        if summed.scalar not in NUMERIC:
            raise InvariantError(
                "S009",
                f"`{_text(call)}`: the summed value must be numeric, not {summed.scalar.value}",
                "sum Int or Float columns",
            )
        by = self.quote(self.by_column(call, child))
        key = self.quote(_one_column_key(self.table(parent_table), parent_table))
        total = self.num(f"COALESCE(s.{self.quote('total')}, 0)")
        parent_sql = self.num(parent.sql)
        diff = f"ABS({parent_sql} - {total})"
        tol = repr(TOLERANCE)
        close = f"({diff} <= {tol} * ABS({parent_sql}) OR {diff} <= {tol} * ABS({total}))"
        return (
            f"SELECT p.*, {total} AS {self.quote('expected')} "
            f"FROM {self.sql_table(parent_table)} AS p "
            f"LEFT JOIN (SELECT c.{by} AS {self.quote('k')}, SUM({summed.sql}) AS "
            f"{self.quote('total')} FROM {self.sql_table(child)} AS c GROUP BY c.{by}) AS s "
            f"ON s.{self.quote('k')} = p.{key} "
            f"WHERE NOT COALESCE({close}, {self.unknown})"
        )


def _aggregate(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in ("sum", "count"):
            return node.func.id
    return None


def _one_column_key(schema: Schema, table: str) -> str:
    for key in schema.keys:
        if len(key) == 1:
            return key[0]
    if "id" in schema:
        return "id"
    raise _form(f"{table} has no one-column key for sum(..., by=...) to reference")


# SQLite's (true, false) literals and division; a server engine's are in its descriptor
_SQLITE_BOOLS = ("1", "0")
_SQLITE_DIVIDE = "(({a}) * 1.0 / ({b}))"


def _sql_text(value: str, dialect: str = "sqlite") -> str:
    """A text literal; where the engine reads a backslash as an escape (its
    ``backslash_escapes``: MySQL, ClickHouse) it is doubled."""
    engine = None if dialect == "sqlite" else _engines.find(dialect)
    if engine is not None and engine.sql.backslash_escapes:
        value = value.replace("\\", "\\\\")
    return "'" + value.replace("'", "''") + "'"


def compile_invariant(inv: Invariant, profile: Profile, dialect: str = "sqlite") -> str:
    """SQL returning the rows that violate `inv` (none when it holds), in `dialect`'s SQL
    (``sqlite`` -- the profile's backend --, ``postgres``, ``mysql`` or ``clickhouse``).
    Raises InvariantError for an expression outside the allowed forms."""
    return _Compiler(inv, profile, dialect).compile()
