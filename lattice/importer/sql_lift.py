"""S6 of the importer's evidence sources (spec 2026-09-27 importer §3.6): lift SQL back
into Lattice steps -- the inverse of the SQL compiler (:mod:`lattice.api.sql`).

The input is one SQLite statement as SQLAlchemy emits it (``?`` placeholders with their
values, or values inlined), or as Lattice's own compiler emits it (a ``WITH`` chain of
CTEs carrying ``__ord``, named binds ``:p_<param>``, ``:__user_id``, ...). The output is
a :class:`Lifted` -- a chain of Lattice steps (fetch/rows/user, where, owned, derive,
select, sort, limit, page, count, join, insert, update, delete) plus the ``require``
conditions the statement computes as flag columns -- or a :class:`NotLiftable` naming
the construct that stops it.

The parser (:func:`parse_sql`) is a small recursive-descent parser for the subset these
emitters produce (no dependency; ``sqlglot`` is not installed): SELECT with CTEs,
joins, WHERE, GROUP BY/HAVING (parsed, not lifted), ORDER BY, LIMIT/OFFSET, window
calls; INSERT ... VALUES; UPDATE ... SET [FROM] WHERE; DELETE ... WHERE; RETURNING.

A :class:`LiftContext` says what the SQL alone cannot: the Lattice class of each table,
the tables' columns (to tell "every column" from a projection), owner columns, the
users table and its key, and **bindings** -- which request input each bound value came
from (``{uuid: "id", 100: "limit", <caller id>: "user"}``), so ``WHERE item.id = ?``
lifts to ``where(t, col("id") == id)``.

Step arguments are Lattice source text (``ast.unparse``), so two lifts -- or a lift and
a hand-written program (:func:`program_steps`) -- compare as plain data.
"""

from __future__ import annotations

import ast
import re
import uuid
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from lattice.catalog import API_OP_ALIASES

__all__ = [
    "LiftContext",
    "Lifted",
    "NotLiftable",
    "SqlSyntaxError",
    "Step",
    "binding_key",
    "consolidate",
    "lift_sql",
    "lift_trace",
    "parse_sql",
    "program_steps",
    "shape",
    "source_chain",
    "step",
    "with_total",
]

# ================================================================================ tokens ==

KEYWORDS = frozenset(
    """SELECT FROM WHERE AND OR NOT AS ON JOIN INNER LEFT RIGHT FULL OUTER CROSS NATURAL
    USING GROUP BY HAVING ORDER ASC DESC LIMIT OFFSET WITH RECURSIVE INSERT INTO VALUES
    UPDATE SET DELETE RETURNING DISTINCT UNION ALL EXCEPT INTERSECT IS NULL IN LIKE GLOB
    BETWEEN CASE WHEN THEN ELSE END CAST EXISTS NULLS FIRST LAST OVER PARTITION ESCAPE
    DEFAULT REPLACE""".split()
)
_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<str>'(?:[^']|'')*')
  | (?P<qid>"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<param>\?\d*|:[A-Za-z_]\w*|\$\d+)
  | (?P<id>[A-Za-z_][\w$]*)
  | (?P<op><=|>=|<>|!=|==|\|\||[-+*/%=<>(),.;])
    """,
    re.VERBOSE,
)


class SqlSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Tok:
    kind: str  # kw | id | str | num | param | op | eof
    value: str
    pos: int


def tokenize(sql: str) -> list[Tok]:
    out: list[Tok] = []
    pos = 0
    while pos < len(sql):
        m = _TOKEN.match(sql, pos)
        if m is None:
            raise SqlSyntaxError(f"unexpected {sql[pos : pos + 10]!r} at {pos}")
        kind = m.lastgroup
        text = m.group()
        pos = m.end()
        if kind == "ws":
            continue
        if kind == "id" and text.upper() in KEYWORDS:
            out.append(Tok("kw", text.upper(), m.start()))
        elif kind == "qid":
            inner = text[1:-1]
            out.append(Tok("id", inner.replace('""', '"') if text[0] == '"' else inner, m.start()))
        elif kind == "str":
            out.append(Tok("str", text[1:-1].replace("''", "'"), m.start()))
        else:
            out.append(Tok(kind or "", text, m.start()))
    out.append(Tok("eof", "", len(sql)))
    return out


# =================================================================================== AST ==


@dataclass(frozen=True)
class Col:
    table: str | None
    name: str


@dataclass(frozen=True)
class Star:
    table: str | None


@dataclass(frozen=True)
class Param:
    name: str | None  # a named bind (":p_x" -> "p_x"); None for a positional ?
    index: int  # the positional index (for ?)


@dataclass(frozen=True)
class Lit:
    value: Any


@dataclass(frozen=True)
class Unary:
    op: str  # NOT | - | +
    operand: Any


@dataclass(frozen=True)
class Binary:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class IsNull:
    expr: Any
    negated: bool


@dataclass(frozen=True)
class InExpr:
    expr: Any
    items: tuple[Any, ...] | None
    select: Select | None
    negated: bool


@dataclass(frozen=True)
class Between:
    expr: Any
    lo: Any
    hi: Any
    negated: bool


@dataclass(frozen=True)
class Order:
    expr: Any
    desc: bool = False
    nulls: str | None = None


@dataclass(frozen=True)
class Over:
    partition: tuple[Any, ...]
    order: tuple[Order, ...]


@dataclass(frozen=True)
class Func:
    name: str  # lower case
    args: tuple[Any, ...]
    star: bool = False
    distinct: bool = False
    over: Over | None = None


@dataclass(frozen=True)
class Case:
    operand: Any
    whens: tuple[tuple[Any, Any], ...]
    else_: Any


@dataclass(frozen=True)
class Cast:
    expr: Any
    type: str


@dataclass(frozen=True)
class Exists:
    select: Select


@dataclass(frozen=True)
class SubQuery:
    select: Select


@dataclass(frozen=True)
class TableRef:
    name: str
    alias: str | None


@dataclass(frozen=True)
class SubRef:
    select: Select
    alias: str | None


@dataclass(frozen=True)
class FuncRef:
    func: Func
    alias: str | None


@dataclass(frozen=True)
class Join:
    kind: str  # inner | left | right | full | cross
    item: Any
    on: Any


@dataclass(frozen=True)
class Select:
    ctes: tuple[tuple[str, Select], ...]
    distinct: bool
    items: tuple[tuple[Any, str | None], ...]
    from_: tuple[Any, ...]
    joins: tuple[Join, ...]
    where: Any
    group_by: tuple[Any, ...]
    having: Any
    order_by: tuple[Order, ...]
    limit: Any
    offset: Any
    compound: str | None = None


@dataclass(frozen=True)
class Insert:
    ctes: tuple[tuple[str, Select], ...]
    table: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    select: Select | None
    returning: tuple[tuple[Any, str | None], ...]
    verb: str = "INSERT"


@dataclass(frozen=True)
class Update:
    ctes: tuple[tuple[str, Select], ...]
    table: str
    sets: tuple[tuple[str, Any], ...]
    from_: Any
    where: Any
    returning: tuple[tuple[Any, str | None], ...]


@dataclass(frozen=True)
class Delete:
    ctes: tuple[tuple[str, Select], ...]
    table: str
    where: Any
    returning: tuple[tuple[Any, str | None], ...]


Statement = Select | Insert | Update | Delete

# ================================================================================ parser ==


class _Parser:
    def __init__(self, sql: str) -> None:
        self.toks = tokenize(sql)
        self.i = 0
        self.positional = 0

    # -- token helpers --

    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, *values: str) -> bool:
        t = self.tok
        return t.kind in ("kw", "op") and t.value in values

    def take(self, *values: str) -> bool:
        if self.at(*values):
            self.i += 1
            return True
        return False

    def expect(self, *values: str) -> Tok:
        if not self.at(*values):
            raise SqlSyntaxError(
                f"expected {'/'.join(values)} at {self.tok.pos}, got {self.tok.value!r}"
            )
        t = self.tok
        self.i += 1
        return t

    def ident(self) -> str:
        t = self.tok
        if t.kind == "id" or (
            t.kind == "kw" and t.value in ("REPLACE", "DEFAULT", "FIRST", "LAST")
        ):
            self.i += 1
            return t.value
        raise SqlSyntaxError(f"expected an identifier at {t.pos}, got {t.value!r}")

    # -- statements --

    def statement(self) -> Statement:
        ctes: tuple[tuple[str, Select], ...] = ()
        if self.take("WITH"):
            self.take("RECURSIVE")
            items = []
            while True:
                name = self.ident()
                if self.take("("):  # column list
                    while not self.take(")"):
                        self.i += 1
                self.expect("AS")
                self.expect("(")
                items.append((name, self.select()))
                self.expect(")")
                if not self.take(","):
                    break
            ctes = tuple(items)
        if self.at("SELECT"):
            s = self.select()
            stmt: Statement = replace(s, ctes=ctes + s.ctes)
        elif self.at("INSERT", "REPLACE"):
            stmt = self.insert(ctes)
        elif self.at("UPDATE"):
            stmt = self.update(ctes)
        elif self.at("DELETE"):
            stmt = self.delete(ctes)
        else:
            raise SqlSyntaxError(f"unsupported statement starting {self.tok.value!r}")
        self.take(";")
        if self.tok.kind != "eof":
            raise SqlSyntaxError(f"trailing input at {self.tok.pos}: {self.tok.value!r}")
        return stmt

    def select(self) -> Select:
        ctes: tuple[tuple[str, Select], ...] = ()
        if self.at("WITH"):
            self.i += 1
            self.take("RECURSIVE")
            items = []
            while True:
                name = self.ident()
                self.expect("AS")
                self.expect("(")
                items.append((name, self.select()))
                self.expect(")")
                if not self.take(","):
                    break
            ctes = tuple(items)
        self.expect("SELECT")
        distinct = self.take("DISTINCT")
        self.take("ALL")
        items = [self.select_item()]
        while self.take(","):
            items.append(self.select_item())
        from_: list[Any] = []
        joins: list[Join] = []
        if self.take("FROM"):
            from_.append(self.from_item())
            while True:
                if self.take(","):
                    from_.append(self.from_item())
                    continue
                kind = self.join_kind()
                if kind is None:
                    break
                item = self.from_item()
                on = None
                if self.take("ON"):
                    on = self.expr()
                elif self.take("USING"):
                    self.expect("(")
                    cols = [self.ident()]
                    while self.take(","):
                        cols.append(self.ident())
                    self.expect(")")
                    on = ("USING", tuple(cols))
                joins.append(Join(kind, item, on))
        where = self.expr() if self.take("WHERE") else None
        group: list[Any] = []
        having = None
        if self.take("GROUP"):
            self.expect("BY")
            group.append(self.expr())
            while self.take(","):
                group.append(self.expr())
            if self.take("HAVING"):
                having = self.expr()
        compound = None
        if self.at("UNION", "EXCEPT", "INTERSECT"):
            compound = self.tok.value
            self.i += 1
            self.take("ALL")
            self.select()  # parsed, not kept: compound selects are not lifted
        order = self.order_by() if self.at("ORDER") else ()
        limit = offset = None
        if self.take("LIMIT"):
            limit = self.expr()
            if self.take("OFFSET"):
                offset = self.expr()
            elif self.take(","):  # LIMIT offset, count
                offset, limit = limit, self.expr()
        return Select(ctes, distinct, tuple(items), tuple(from_), tuple(joins), where,
                      tuple(group), having, order, limit, offset, compound)  # fmt: skip

    def join_kind(self) -> str | None:
        if self.take("JOIN"):
            return "inner"
        for word in ("INNER", "LEFT", "RIGHT", "FULL", "CROSS", "NATURAL"):
            kind = word.lower()
            if self.at(word):
                self.i += 1
                self.take("OUTER")
                self.expect("JOIN")
                return kind
        return None

    def order_by(self) -> tuple[Order, ...]:
        self.expect("ORDER")
        self.expect("BY")
        out = []
        while True:
            e = self.expr()
            desc = False
            if self.take("DESC"):
                desc = True
            else:
                self.take("ASC")
            nulls = None
            if self.take("NULLS"):
                nulls = self.expect("FIRST", "LAST").value.lower()
            out.append(Order(e, desc, nulls))
            if not self.take(","):
                return tuple(out)

    def alias(self) -> str | None:
        if self.take("AS"):
            return self.ident()
        if self.tok.kind == "id":
            return self.ident()
        return None

    def select_item(self) -> tuple[Any, str | None]:
        if self.take("*"):
            return Star(None), None
        e = self.expr()
        return e, self.alias()

    def from_item(self) -> Any:
        if self.take("("):
            if self.at("SELECT", "WITH"):
                s = self.select()
                self.expect(")")
                return SubRef(s, self.alias())
            item = self.from_item()
            self.expect(")")
            return item
        name = self.ident()
        if self.take("."):
            name = self.ident()  # schema.table: keep the table
        if self.take("("):
            args = [] if self.at(")") else self.exprs()
            self.expect(")")
            return FuncRef(Func(name.lower(), tuple(args)), self.alias())
        return TableRef(name, self.alias())

    def exprs(self) -> list[Any]:
        out = [self.expr()]
        while self.take(","):
            out.append(self.expr())
        return out

    def returning(self) -> tuple[tuple[Any, str | None], ...]:
        if not self.take("RETURNING"):
            return ()
        items = [self.select_item()]
        while self.take(","):
            items.append(self.select_item())
        return tuple(items)

    def insert(self, ctes: tuple[tuple[str, Select], ...]) -> Insert:
        verb = "INSERT"
        if self.take("REPLACE"):
            verb = "REPLACE"
        else:
            self.expect("INSERT")
            if self.take("OR"):
                verb = f"INSERT OR {self.ident().upper()}"
        self.expect("INTO")
        table = self.ident()
        cols: list[str] = []
        if self.take("("):
            cols.append(self.ident())
            while self.take(","):
                cols.append(self.ident())
            self.expect(")")
        rows: list[tuple[Any, ...]] = []
        select = None
        if self.take("VALUES"):
            while True:
                self.expect("(")
                rows.append(tuple(self.exprs()))
                self.expect(")")
                if not self.take(","):
                    break
        elif self.take("DEFAULT"):
            self.expect("VALUES")
        else:
            select = self.select()
        return Insert(ctes, table, tuple(cols), tuple(rows), select, self.returning(), verb)

    def update(self, ctes: tuple[tuple[str, Select], ...]) -> Update:
        self.expect("UPDATE")
        table = self.ident()
        sets = []
        self.expect("SET")
        while True:
            col = self.ident()
            if self.take("."):
                col = self.ident()
            self.expect("=")
            sets.append((col, self.expr()))
            if not self.take(","):
                break
        from_ = self.from_item() if self.take("FROM") else None
        where = self.expr() if self.take("WHERE") else None
        return Update(ctes, table, tuple(sets), from_, where, self.returning())

    def delete(self, ctes: tuple[tuple[str, Select], ...]) -> Delete:
        self.expect("DELETE")
        self.expect("FROM")
        table = self.ident()
        where = self.expr() if self.take("WHERE") else None
        return Delete(ctes, table, where, self.returning())

    # -- expressions --

    def expr(self) -> Any:
        return self.or_()

    def or_(self) -> Any:
        e = self.and_()
        while self.take("OR"):
            e = Binary("OR", e, self.and_())
        return e

    def and_(self) -> Any:
        e = self.not_()
        while self.take("AND"):
            e = Binary("AND", e, self.not_())
        return e

    def not_(self) -> Any:
        if self.take("NOT"):
            return Unary("NOT", self.not_())
        return self.comparison()

    def comparison(self) -> Any:
        e = self.additive()
        while True:
            if self.at("=", "==", "!=", "<>", "<", "<=", ">", ">="):
                op = {"==": "=", "<>": "!="}.get(self.tok.value, self.tok.value)
                self.i += 1
                e = Binary(op, e, self.additive())
            elif self.take("IS"):
                negated = self.take("NOT")
                if self.take("NULL"):
                    e = IsNull(e, negated)
                else:
                    e = Binary("IS NOT" if negated else "IS", e, self.additive())
            elif self.at("NOT") and self.peek().value in ("IN", "LIKE", "GLOB", "BETWEEN"):
                self.i += 1
                e = self.postfix(e, negated=True)
            elif self.at("IN", "LIKE", "GLOB", "BETWEEN"):
                e = self.postfix(e, negated=False)
            else:
                return e

    def postfix(self, e: Any, negated: bool) -> Any:
        word = self.expect("IN", "LIKE", "GLOB", "BETWEEN").value
        if word == "IN":
            self.expect("(")
            if self.at("SELECT", "WITH"):
                s = self.select()
                self.expect(")")
                return InExpr(e, None, s, negated)
            items = () if self.at(")") else tuple(self.exprs())
            self.expect(")")
            return InExpr(e, items, None, negated)
        if word == "BETWEEN":
            lo = self.additive()
            self.expect("AND")
            return Between(e, lo, self.additive(), negated)
        right = self.additive()
        if self.take("ESCAPE"):
            self.additive()
        out = Binary(word, e, right)
        return Unary("NOT", out) if negated else out

    def additive(self) -> Any:
        e = self.multiplicative()
        while self.at("+", "-"):
            op = self.tok.value
            self.i += 1
            e = Binary(op, e, self.multiplicative())
        return e

    def multiplicative(self) -> Any:
        e = self.concat()
        while self.at("*", "/", "%"):
            op = self.tok.value
            self.i += 1
            e = Binary(op, e, self.concat())
        return e

    def concat(self) -> Any:
        e = self.unary()
        while self.take("||"):
            e = Binary("||", e, self.unary())
        return e

    def unary(self) -> Any:
        if self.at("-", "+"):
            op = self.tok.value
            self.i += 1
            operand = self.unary()
            if isinstance(operand, Lit) and isinstance(operand.value, int | float) and op == "-":
                return Lit(-operand.value)
            return Unary(op, operand)
        return self.primary()

    def primary(self) -> Any:
        t = self.tok
        if t.kind == "num":
            self.i += 1
            return Lit(float(t.value) if any(c in t.value for c in ".eE") else int(t.value))
        if t.kind == "str":
            self.i += 1
            return Lit(t.value)
        if t.kind == "param":
            self.i += 1
            if t.value.startswith("?"):
                index = int(t.value[1:]) - 1 if len(t.value) > 1 else self.positional
                self.positional += 1
                return Param(None, index)
            return Param(t.value[1:], -1)
        if t.kind == "kw":
            if self.take("NULL"):
                return Lit(None)
            if self.take("CASE"):
                operand = None if self.at("WHEN") else self.expr()
                whens = []
                while self.take("WHEN"):
                    cond = self.expr()
                    self.expect("THEN")
                    whens.append((cond, self.expr()))
                else_ = self.expr() if self.take("ELSE") else None
                self.expect("END")
                return Case(operand, tuple(whens), else_)
            if self.take("CAST"):
                self.expect("(")
                e = self.expr()
                self.expect("AS")
                words = []
                while not self.at(")"):
                    words.append(self.tok.value)
                    self.i += 1
                self.expect(")")
                return Cast(e, " ".join(words))
            if self.take("EXISTS"):
                self.expect("(")
                s = self.select()
                self.expect(")")
                return Exists(s)
            if t.value in ("REPLACE",) and self.peek().value == "(":
                self.i += 1
                return self.call(t.value.lower())
        if self.take("("):
            if self.at("SELECT", "WITH"):
                s = self.select()
                self.expect(")")
                return SubQuery(s)
            e = self.expr()
            if self.take(","):  # a row value
                items = [e, *self.exprs()]
                self.expect(")")
                return ("ROW", tuple(items))
            self.expect(")")
            return e
        if self.take("*"):
            return Star(None)
        if t.kind == "id":
            self.i += 1
            if self.at("(") and not (self.peek().kind == "kw" and self.peek().value == "SELECT"):
                self.i += 1
                return self.call(t.value.lower())
            if self.take("."):
                if self.take("*"):
                    return Star(t.value)
                return Col(t.value, self.ident())
            return Col(None, t.value)
        raise SqlSyntaxError(f"unexpected {t.value!r} at {t.pos}")

    def call(self, name: str) -> Func:
        star = distinct = False
        args: list[Any] = []
        if self.take("*"):
            star = True
        elif not self.at(")"):
            distinct = self.take("DISTINCT")
            args = self.exprs()
        self.expect(")")
        over = None
        if self.take("OVER"):
            self.expect("(")
            partition: list[Any] = []
            if self.take("PARTITION"):
                self.expect("BY")
                partition = self.exprs()
            order = self.order_by() if self.at("ORDER") else ()
            while not self.at(")"):  # a frame clause: parsed over, not kept
                self.i += 1
            self.expect(")")
            over = Over(tuple(partition), order)
        return Func(name, tuple(args), star, distinct, over)


def parse_sql(sql: str) -> Statement:
    """Parse one statement of the supported subset (see the module docstring)."""
    return _Parser(sql).statement()


# ================================================================================ shapes ==

_STRING = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w.\"])-?\d+(?:\.\d+)?(?![\w\"])")
_PARAM = re.compile(r"(?:\?\d*|:[A-Za-z_]\w*|\$\d+|%\(\w+\)s)")
_IN_LIST = re.compile(r"IN \(\?(?:, \?)*\)", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


def shape(sql: str) -> str:
    """A statement with every literal and placeholder replaced by ``?``, an ``IN`` list
    collapsed to one ``?``, and whitespace collapsed: statements of one shape differ
    only in values."""
    s = _STRING.sub("?", sql)
    s = _PARAM.sub("?", s)
    s = _NUMBER.sub("?", s)
    s = _SPACE.sub(" ", s).strip()
    return _IN_LIST.sub("IN (?)", s)


# ================================================================================= steps ==


def _canon(node: ast.AST) -> str:
    """Lattice source of an expression; Bool constants as 0/1 (SQLite stores them so)."""

    class _Bools(ast.NodeTransformer):
        def visit_Constant(self, n: ast.Constant) -> ast.AST:
            if isinstance(n.value, bool):
                return ast.copy_location(ast.Constant(int(n.value)), n)
            return n

    return ast.unparse(_Bools().visit(ast.parse(ast.unparse(node), mode="eval")).body)


def _src(value: Any) -> str:
    if isinstance(value, ast.AST):
        return _canon(value)
    if isinstance(value, str):
        return value
    return (
        _canon(ast.Constant(value))
        if not isinstance(value, list | tuple)
        else _canon(ast.List([ast.Constant(v) for v in value]))
    )


@dataclass(frozen=True)
class Step:
    """One Lattice step: ``op`` and its arguments as Lattice source, positional ones
    under the key ``""`` first, then keywords sorted by name."""

    op: str
    args: tuple[tuple[str, str], ...] = ()

    def arg(self, key: str, default: str | None = None) -> str | None:
        for k, v in self.args:
            if k == key:
                return v
        return default

    def render(self, input_var: str | None = None) -> str:
        parts = [input_var] if input_var else []
        parts += [v if k == "" else f"{k}={v}" for k, v in self.args]
        return f"{self.op}({', '.join(parts)})"


def step(op: str, *positional: Any, **keywords: Any) -> Step:
    """A :class:`Step`; values are Lattice source text or ``ast`` expressions or
    constants. ``sort(desc=False)``, ``page(offset=0)``, ``page(total=False)`` are
    dropped (the defaults)."""
    kws = {k: _src(v) for k, v in keywords.items()}
    if op == "sort" and kws.get("desc") in ("0", "False"):
        kws.pop("desc")
    if op == "page":
        if kws.get("offset") == "0":
            kws.pop("offset")
        if kws.get("total") in ("0", "False"):
            kws.pop("total")
    if op == "sort" and kws.get("desc") == "True":
        kws["desc"] = "1"
    args = tuple(("", _src(v)) for v in positional) + tuple(sorted(kws.items()))
    return Step(op, args)


SOURCES = frozenset({"fetch", "rows", "user"})


@dataclass(frozen=True)
class Lifted:
    """A lifted statement: ``kind`` (read | count | insert | update | delete), the step
    chain (its first step is the source: ``fetch(T)``, ``rows(step)`` or ``user``),
    the ``require`` conditions it computes as flag columns, the output columns (reads),
    and notes on what the lift assumed."""

    kind: str
    steps: tuple[Step, ...]
    checks: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    table: str | None = None  # the SQL table read or written
    sql: str = field(default="", compare=False)

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(s.op for s in self.steps)

    @property
    def source(self) -> Step:
        return self.steps[0]

    def render(self, prefix: str = "t") -> str:
        """The chain as Lattice statements (``t0 = fetch(Item)``, ``t1 = where(t0,
        ...)``); ``require`` checks follow the chain."""
        lines = []
        prev: str | None = None
        for i, s in enumerate(self.steps):
            name = f"{prefix}{i}"
            if s.op == "user":
                lines.append(f"{name} = user")
            elif s.op in ("fetch", "insert"):
                lines.append(f"{name} = {s.render()}")
            else:
                lines.append(f"{name} = {s.render(prev)}")
            prev = name
        for i, c in enumerate(self.checks):
            lines.append(f"{prefix}_ok{i} = require({prev}, {c}, else_=...)")
        return "\n".join(lines)


@dataclass(frozen=True)
class NotLiftable:
    """Why a statement is not lifted: ``construct`` names the SQL feature."""

    construct: str
    reason: str
    sql: str = field(default="", compare=False)


class _Stop(Exception):
    def __init__(self, construct: str, reason: str = "") -> None:
        super().__init__(construct)
        self.construct = construct
        self.reason = reason or f"{construct} has no Lattice step in the lifted subset"


# =============================================================================== context ==


def binding_key(value: Any) -> Any:
    """The key a value is bound under: a UUID in any spelling is one key; a bool is 0/1
    (SQLite binds them so)."""
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        compact = value.replace("-", "")
        if (
            len(compact) == 32
            and re.fullmatch(r"[0-9a-fA-F]{32}", compact)
            and ("-" not in value or len(value) == 36)
        ):
            return uuid.UUID(compact)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


@dataclass(frozen=True)
class LiftContext:
    """What SQL alone does not say.

    * ``tables``: SQL table -> Lattice class name (default: the table name capitalised);
    * ``columns``: SQL table -> its columns (a read of all of them is no ``select``);
    * ``owners``: SQL table -> owner column (``owner_id = <caller>`` is ``accessible``);
    * ``users_table`` / ``user_key``: the caller's table and key (``WHERE user.id =
      <caller>`` reads the caller's row: the source ``user``);
    * ``keys``: SQL table -> generated key column (an unbound fresh UUID written there is
      dropped, as ``insert`` generates it);
    * ``bindings``: :func:`binding_key` of a bound value -> the request input it came from
      (``"user"`` is the caller's key);
    * ``binds``: the named binds of our own compiler's SQL (``SqlQuery.binds``);
    * ``rows``: bind name -> the step whose rows it carries (``SqlQuery.rows``);
    * ``params``: the positional values of ``?`` placeholders;
    * ``null_order``: the profile's ``ApiProfile.null_order`` (where a ``sort`` without
      ``nulls=`` puts null keys): ``"smallest"`` is SQLite's own ORDER BY order.
    """

    tables: Mapping[str, str] = field(default_factory=dict)
    columns: Mapping[str, Sequence[str]] = field(default_factory=dict)
    owners: Mapping[str, str] = field(default_factory=dict)
    users_table: str | None = None
    user_key: str = "id"
    keys: Mapping[str, str] = field(default_factory=dict)
    bindings: Mapping[Any, str] = field(default_factory=dict)
    binds: Mapping[str, Any] = field(default_factory=dict)
    rows: Mapping[str, str] = field(default_factory=dict)
    params: Sequence[Any] = ()
    null_order: str = "last"

    def cls(self, table: str) -> str:
        return self.tables.get(table) or table[:1].upper() + table[1:]


# ================================================================================ lifter ==

CALLER = "__caller__"  # marker source for the caller's key
ADMIN = "__admin__"
NOW = ast.Call(ast.Attribute(ast.Name("fn"), "now"), [], [])
CMP = {"=": ast.Eq, "!=": ast.NotEq, "<": ast.Lt, "<=": ast.LtE, ">": ast.Gt, ">=": ast.GtE}
FLIP = {"=": "=", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
ARITH = {"+": ast.Add, "-": ast.Sub, "*": ast.Mult, "/": ast.Div, "%": ast.Mod}
FN_NAMES = {
    "lattice_to_uuid": "to_uuid",
    "lattice_lower": "lower",
    "lattice_upper": "upper",
    "lattice_strip": "strip",
    "lower": "lower",
    "upper": "upper",
    "trim": "strip",
    "abs": "abs",
    "round": "round",
    "length": "len",
}
ORD = "__ord"
DATETIME_LIT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


@dataclass
class _Pipe:
    steps: list[Step]
    columns: list[str] | None
    table: str | None
    checks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    kind: str = "read"


def _name(n: str) -> ast.expr:
    return ast.Name(n)


def _call(func: ast.expr, *args: ast.expr, **kw: ast.expr) -> ast.Call:
    return ast.Call(func, list(args), [ast.keyword(k, v) for k, v in kw.items()])


def _col(name: str) -> ast.Call:
    return _call(_name("col"), ast.Constant(name))


def _ns(ns: str, name: str) -> ast.expr:
    return ast.Attribute(_name(ns), name)


class _Lifter:
    def __init__(self, ctx: LiftContext) -> None:
        self.ctx = ctx
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    # -- values --

    def param_value(self, p: Param) -> Any:
        """A bound parameter: a Lattice expression, or the CALLER / ADMIN markers."""
        if p.name is None:
            if p.index >= len(self.ctx.params):
                raise _Stop("unbound parameter", f"no value for placeholder #{p.index + 1}")
            return self.bound(self.ctx.params[p.index])
        n = p.name
        if n.startswith("p_"):
            return _name(n[2:])
        if n == "__user_id":
            return CALLER
        if n == "__is_admin":
            return ADMIN
        if n == "__now":
            return NOW
        if n.startswith("__sent_"):
            return ("SENT", n.removeprefix("__sent_"))
        if n in self.ctx.binds:
            return ast.Constant(self.ctx.binds[n])
        if n in self.ctx.rows:
            return ("ROWS", self.ctx.rows[n])
        raise _Stop("unknown bind", f"no value for bind :{n}")

    def bound(self, value: Any) -> Any:
        name = self.ctx.bindings.get(binding_key(value))
        if name == "user":
            return CALLER
        if name is not None:
            return ast.parse(name, mode="eval").body
        return ast.Constant(value)

    def value(self, e: Any) -> Any:
        """A SQL expression as a Lattice expression (``ast``), or a marker."""
        match e:
            case Col(name=name):
                if name == ORD:
                    raise _Stop("__ord", "the row-order column outside ORDER BY")
                return _col(name)
            case Lit(value=v):
                return ast.Constant(v)
            case Param():
                return self.param_value(e)
            case Unary(op="NOT", operand=x):
                return ast.UnaryOp(ast.Invert(), self.expr(x))
            case Unary(op="-", operand=x):
                return ast.UnaryOp(ast.USub(), self.expr(x))
            case Unary(op="+", operand=x):
                return self.expr(x)
            case Binary(op=op, left=a, right=b) if op in CMP:
                left, right = self.value(a), self.value(b)
                if not isinstance(a, Col) and isinstance(b, Col):  # ? = col
                    left, right, op = right, left, FLIP[op]
                return ast.Compare(self.caller_expr(left), [CMP[op]()], [self.caller_expr(right)])
            case Binary(op="AND", left=a, right=b):
                return ast.BinOp(self.expr(a), ast.BitAnd(), self.expr(b))
            case Binary(op="OR", left=a, right=b):
                return ast.BinOp(self.expr(a), ast.BitOr(), self.expr(b))
            case Binary(op=op, left=a, right=b) if op in ARITH:
                return ast.BinOp(self.expr(a), ARITH[op](), self.expr(b))
            case IsNull(expr=x, negated=neg):
                test = _call(_name("is_null"), self.expr(x))
                return ast.UnaryOp(ast.Invert(), test) if neg else test
            case Func():
                return self.func(e)
            case Binary(op=op):
                raise _Stop(f"operator {op}")
            case InExpr():
                raise _Stop("IN", "IN lists and subqueries (other than an owner check)")
            case Between():
                raise _Stop("BETWEEN")
            case Case():
                raise _Stop("CASE")
            case Cast():
                raise _Stop("CAST")
            case Exists():
                raise _Stop("EXISTS")
            case SubQuery():
                raise _Stop("scalar subquery")
            case Star():
                raise _Stop("*", "a star outside the select list")
        raise _Stop(type(e).__name__)

    def expr(self, e: Any) -> ast.expr:
        v = self.value(e)
        return self.caller_expr(v)

    def caller_expr(self, v: Any) -> ast.expr:
        if v is CALLER:
            return ast.Attribute(_name("user"), self.ctx.user_key)
        if v is ADMIN:
            raise _Stop("admin flag", "the caller's admin flag outside an owner check")
        if isinstance(v, tuple):
            raise _Stop(v[0].lower(), f"{v[0]} marker in an expression")
        return v

    def func(self, f: Func) -> ast.expr:
        if f.over is not None:
            raise _Stop("window function", f"{f.name}() OVER (...) other than the sort idiom")
        if f.star or f.distinct:
            raise _Stop(f"{f.name}(*)" if f.star else f"{f.name}(DISTINCT ...)")
        args = [self.expr(a) for a in f.args]
        if f.name.startswith("native_"):
            return _call(_ns("native", f.name.removeprefix("native_")), *args)
        if f.name == "lattice_to_datetime" and len(args) == 2:
            return _call(_ns("fn", "to_datetime"), args[0], format=args[1])
        if f.name == "substr" and len(f.args) == 3 and f.args[1:] == (Lit(1), Lit(10)):
            return _call(_ns("fn", "date"), args[0])
        if f.name == "coalesce":
            return _call(_name("coalesce"), *args)
        if f.name in FN_NAMES:
            return _call(_ns("fn", FN_NAMES[f.name]), *args)
        raise _Stop(f"function {f.name}()", f"no Lattice function for SQL {f.name}()")

    # -- owner patterns --

    def owner_check(self, e: Any, table: str | None) -> bool:
        """``(:__is_admin OR <owner> = :__user_id)`` or the via-parent form
        ``(:__is_admin OR <fk> IN (SELECT <key> FROM parent WHERE <owner> = :__user_id))``."""
        if not (isinstance(e, Binary) and e.op == "OR"):
            return False
        a, b = e.left, e.right
        if not (isinstance(a, Param) and self.param_value(a) is ADMIN):
            return False
        if isinstance(b, Binary) and b.op == "=" and isinstance(b.left, Col):
            return isinstance(b.right, Param) and self.param_value(b.right) is CALLER
        if isinstance(b, InExpr) and b.select is not None and not b.negated:
            w = b.select.where
            return (
                isinstance(w, Binary) and w.op == "=" and isinstance(w.right, Param)
                and self.param_value(w.right) is CALLER
            )  # fmt: skip
        return False

    def caller_filter(self, e: Any, table: str | None) -> str | None:
        """``<col> = <caller>`` in a traced statement: ``"user_row"`` for the caller's
        own row, ``"owned"`` for the table's owner column (lifted as ``accessible``)."""
        if not (isinstance(e, Binary) and e.op == "="):
            return None
        col, val = (e.left, e.right) if isinstance(e.left, Col) else (e.right, e.left)
        if not isinstance(col, Col) or isinstance(val, Col):
            return None
        try:
            v = self.value(val)
        except _Stop:
            return None
        if v is not CALLER:
            return None
        if table is not None and table == self.ctx.users_table and col.name == self.ctx.user_key:
            return "user_row"
        if table is not None and self.ctx.owners.get(table) == col.name:
            return "owned"
        return None

    # -- selects --

    def select(self, s: Select, env: Mapping[str, Select]) -> _Pipe:
        if s.compound:
            raise _Stop(s.compound, "a compound SELECT")
        env = {**env, **dict(s.ctes)}
        total = self.total_form(s, env)
        if total is not None:
            return total
        if s.distinct:
            raise _Stop("DISTINCT")
        if s.group_by or s.having:
            raise _Stop("GROUP BY", "grouping and aggregates other than count(*)")
        if len(s.from_) != 1:
            raise _Stop("comma join" if s.from_ else "SELECT without FROM")
        pipe, _ = self.from_source(s.from_[0], env)
        for j in s.joins:
            self.join(pipe, j, env)
        if s.where is not None:
            self.where(pipe, s.where)
        count = self.items(pipe, s)
        self.order(pipe, s.order_by)
        self.window_limit(pipe, s)
        if count:
            pipe.steps.append(step("count"))
            pipe.kind = "count"
            pipe.columns = ["count"]
        return pipe

    def total_form(self, s: Select, env: Mapping[str, Select]) -> _Pipe | None:
        """Our compiler's page(total=True): ``... FROM (SELECT COUNT(*) AS __total FROM
        tN) AS n LEFT JOIN tM AS f ON 1``."""
        if not (len(s.from_) == 1 and isinstance(s.from_[0], SubRef) and len(s.joins) == 1):
            return None
        counted = s.from_[0].select
        j = s.joins[0]
        if not (
            j.kind == "left" and j.on == Lit(1) and isinstance(j.item, TableRef)
            and any(a == "__total" for _, a in counted.items)
        ):  # fmt: skip
            return None
        pipe = self.select(env[j.item.name], env) if j.item.name in env else None
        if pipe is None or not pipe.steps or pipe.steps[-1].op != "page":
            raise _Stop("page total", "a total over something other than a page")
        last = pipe.steps[-1]
        pipe.steps[-1] = Step("page", tuple(sorted((*last.args, ("total", "1")))))
        pipe.columns = [
            (a or e.name) for e, a in s.items
            if isinstance(e, Col) and e.name not in (ORD, "__total")
        ]  # fmt: skip
        return pipe

    def from_source(self, item: Any, env: Mapping[str, Select]) -> tuple[_Pipe, str | None]:
        if isinstance(item, TableRef):
            if item.name in env:
                return self.select(env[item.name], env), item.alias
            cols = self.ctx.columns.get(item.name)
            fetch = step("fetch", self.ctx.cls(item.name))
            return _Pipe([fetch], list(cols) if cols else None, item.name), item.alias
        if isinstance(item, SubRef):
            return self.select(item.select, env), item.alias
        if isinstance(item, FuncRef) and item.func.name == "json_each" and item.func.args:
            v = self.value(item.func.args[0])
            if isinstance(v, tuple) and v[0] == "ROWS":
                source = Step("user") if v[1] == "user" else step("rows", v[1])
                return _Pipe([source], None, None), item.alias
            raise _Stop("json_each", "json_each over something other than a step's rows")
        if isinstance(item, FuncRef):
            raise _Stop(f"table function {item.func.name}()")
        raise _Stop(type(item).__name__)

    def join(self, pipe: _Pipe, j: Join, env: Mapping[str, Select]) -> None:
        if j.kind not in ("inner", "left"):
            raise _Stop(f"{j.kind.upper()} JOIN")
        if not isinstance(j.item, TableRef) or j.item.name in env:
            raise _Stop("join of a derived table", "a join whose right side is not a table")
        right = j.item
        pairs = []
        for c in _conjuncts(j.on):
            if not (isinstance(c, Binary) and c.op == "=" and isinstance(c.left, Col)
                    and isinstance(c.right, Col)):  # fmt: skip
                raise _Stop("join condition", "a join condition other than column equalities")
            left_c, right_c = c.left, c.right
            if left_c.table in (right.name, right.alias):
                left_c, right_c = right_c, left_c
            pairs.append((left_c.name, right_c.name))
        on: ast.expr
        if all(a == b for a, b in pairs):
            on = ast.List([ast.Constant(a) for a, _ in pairs])
        else:
            on = ast.Dict([ast.Constant(a) for a, _ in pairs], [ast.Constant(b) for _, b in pairs])
        kw: dict[str, Any] = {"on": on}
        if j.kind == "left":
            kw["how"] = ast.Constant("left")
        right_src = f"fetch({self.ctx.cls(right.name)})"
        pipe.steps.append(step("join", right_src, **kw))
        rcols = self.ctx.columns.get(right.name)
        if pipe.columns is not None and rcols:
            pipe.columns = [*pipe.columns, *(c for c in rcols if c not in pipe.columns)]
        else:
            pipe.columns = None
        pipe.table = None

    def where(self, pipe: _Pipe, e: Any) -> None:
        for c in _conjuncts(e):
            if self.owner_check(c, pipe.table):
                pipe.steps.append(step("accessible", by="user"))
                continue
            kind = self.caller_filter(c, pipe.table)
            if kind == "user_row" and pipe.steps == [step("fetch", self.ctx.cls(pipe.table or ""))]:
                pipe.steps = [Step("user")]
                continue
            if kind == "owned":
                pipe.steps.append(step("accessible", by="user"))
                self.note("accessible(by=user) from an owner-column filter on the caller's key; "
                          "the admin bypass is not visible in one statement")  # fmt: skip
                continue
            pipe.steps.append(step("where", self.expr(c)))

    def items(self, pipe: _Pipe, s: Select) -> bool:
        """The select list: derive/select steps, sort (ROW_NUMBER() AS __ord), require
        flags; True for a lone count(*)."""
        items = list(s.items)
        if len(items) == 1 and isinstance(items[0][0], Func) and items[0][0].name == "count":
            f = items[0][0]
            if f.star and f.over is None:
                return True
        inputs = pipe.columns
        out: list[str] = []
        derived: list[tuple[str, ast.expr]] = []
        for e, alias in items:
            if alias == ORD or (isinstance(e, Col) and e.name == ORD and alias is None):
                if isinstance(e, Func) and e.name == "row_number" and e.over is not None:
                    self.sort(pipe, e.over.order)
                continue
            if isinstance(e, Star):
                out += list(inputs or [])
                if inputs is None:
                    raise _Stop("*", "SELECT * over a table whose columns are not known")
                continue
            if isinstance(e, FuncRef):
                raise _Stop("function in select list")
            if isinstance(e, Func) and e.name == "json_extract":
                if alias is None:
                    raise _Stop("json_extract", "an unnamed json_extract")
                out.append(alias)
                continue
            if isinstance(alias, str) and alias.startswith("__ok_"):
                pipe.checks.append(self.check(e, pipe.table))
                continue
            if isinstance(e, Col) and (
                alias is None or alias == e.name or alias == f"{e.table}_{e.name}"
                or (pipe.table is not None and alias == f"{pipe.table}_{e.name}")
            ):  # fmt: skip
                out.append(e.name)
                continue
            if alias is None:
                raise _Stop("unnamed expression", "a computed column without a name")
            derived.append((alias, self.expr(e)))
            out.append(alias)
        if pipe.steps and pipe.steps[0].op in ("rows", "user") and pipe.columns is None:
            pipe.columns = out  # the json_each relation defines its columns
            return False
        if derived:
            pipe.steps.append(step("derive", **dict(derived)))
            if inputs is not None:
                inputs = [*inputs, *(n for n, _ in derived)]
        if inputs is None:
            pipe.columns = out
        elif out != list(inputs):
            is_fetch = [s.op for s in pipe.steps] == ["fetch"]
            if not (is_fetch and set(out) == set(inputs)):
                pipe.steps.append(step("select", cols=out))
            pipe.columns = out
        return False

    def check(self, e: Any, table: str | None) -> str:
        if self.owner_check(e, table):
            return "owner_is(user)"
        return _canon(self.expr(e))

    def sort(self, pipe: _Pipe, order: Sequence[Order]) -> None:
        keys: list[str] = []
        flags: list[bool] = []
        nulls_last = set()
        asc_last: set[str] = set()  # null_order "smallest": ascending keys, NULLs last
        asc_first: set[str] = set()  # ... and ascending keys, NULLs first
        for o in order:
            e = o.expr
            if isinstance(e, Col) and e.name == ORD:
                continue  # the tie breaker: a stable sort
            if isinstance(e, IsNull) and not e.negated and isinstance(e.expr, Col):
                nulls_last.add(e.expr.name)  # "(c IS NULL), c": nulls last
                continue
            if not isinstance(e, Col):
                raise _Stop("sort by expression", "ORDER BY something other than a column")
            keys.append(e.name)
            flags.append(o.desc)
            wants_last = e.name in nulls_last or o.nulls == "last"
            if self.ctx.null_order == "smallest":
                # the profile sorts like SQLite: only an ascending key that puts NULLs
                # last differs (sort(..., nulls="last") below when every one does)
                if not o.desc and wants_last:
                    asc_last.add(e.name)
                elif not o.desc:
                    asc_first.add(e.name)
            elif not o.desc and not wants_last:
                self.note(f"ORDER BY {e.name} ASC: SQLite sorts NULLs first; Lattice's "
                          "sort puts them last (equal when the column has no nulls)")  # fmt: skip
        if not keys:
            return
        desc: Any = flags[0] if len(set(flags)) == 1 else ast.List([ast.Constant(f) for f in flags])
        if asc_last and asc_first:
            self.note(f"ORDER BY mixes NULLS FIRST ({', '.join(sorted(asc_first))}) and "
                      f"NULLS LAST ({', '.join(sorted(asc_last))}) ascending keys; the sort "
                      "puts nulls first (equal when the columns have no nulls)")  # fmt: skip
        if asc_last and not asc_first:
            pipe.steps.append(step("sort", by=keys, desc=desc, nulls=ast.Constant("last")))
            return
        pipe.steps.append(step("sort", by=keys, desc=desc))

    def order(self, pipe: _Pipe, order: Sequence[Order]) -> None:
        if order:
            self.sort(pipe, order)

    def window_limit(self, pipe: _Pipe, s: Select) -> None:
        if s.limit is None:
            if s.offset is not None:
                raise _Stop("OFFSET without LIMIT")
            return
        limit = self.expr(s.limit)
        if isinstance(limit, ast.Constant) and limit.value == -1:
            raise _Stop("LIMIT -1", "an unbounded window")
        if s.offset is None:
            pipe.steps.append(step("limit", n=limit))
        else:
            pipe.steps.append(step("page", limit=limit, offset=self.expr(s.offset)))

    # -- writes --

    def write_value(self, table: str, column: str, e: Any) -> tuple[str, Any] | None:
        """(keyword, value) of one written column; None to drop it (a generated key)."""
        if isinstance(e, Func) and e.name == "lattice_uuid4":
            return None
        v = self.value(e)
        if v is CALLER:
            if self.ctx.owners.get(table) == column:
                return "owner", "user"
            return column, ast.Attribute(_name("user"), self.ctx.user_key)
        if isinstance(v, ast.Constant) and isinstance(e, Param) and e.name is None:
            raw = v.value
            if self.ctx.keys.get(table) == column and binding_key(raw) != raw:
                self.note(f"{table}.{column}: an unbound fresh key, generated by insert()")
                return None
            if isinstance(raw, str) and DATETIME_LIT.match(raw):
                self.note(f"{table}.{column}: an unbound timestamp, taken as fn.now()")
                return column, NOW
        return column, self.caller_expr(v)

    def insert(self, s: Insert) -> _Pipe:
        if s.verb != "INSERT":
            raise _Stop(s.verb)
        if s.select is not None:
            raise _Stop("INSERT ... SELECT")
        if len(s.rows) != 1:
            raise _Stop("multi-row INSERT", "several rows in one INSERT (insert() writes one)")
        if len(s.columns) != len(s.rows[0]):
            raise _Stop("INSERT without a column list")
        values = {}
        for c, e in zip(s.columns, s.rows[0], strict=True):
            kv = self.write_value(s.table, c, e)
            if kv is not None:
                values[kv[0]] = kv[1]
        return _Pipe(
            [step("insert", self.ctx.cls(s.table), **values)], None, s.table, kind="insert"
        )

    def update(self, s: Update, env: Mapping[str, Select]) -> _Pipe:
        env = {**env, **dict(s.ctes)}
        if s.from_ is not None:
            return self.update_from(s, env)
        pipe = _Pipe([step("fetch", self.ctx.cls(s.table))], None, s.table)
        if s.where is not None:
            self.where(pipe, s.where)
        values = {}
        for c, e in s.sets:
            kv = self.write_value(s.table, c, e)
            if kv is not None:
                values[kv[0]] = kv[1]
        pipe.steps.append(step("update", **values))
        pipe.kind = "update"
        return pipe

    def update_from(self, s: Update, env: Mapping[str, Select]) -> _Pipe:
        """Our compiler's form: ``SET c = CASE WHEN :__sent_c THEN s."__vN" ELSE t.c END
        FROM (SELECT key AS "__k0", value AS "__vN" FROM tK) AS s WHERE t.key = s."__k0"``."""
        src = s.from_
        if not isinstance(src, SubRef) or len(src.select.from_) != 1:
            raise _Stop("UPDATE ... FROM", "an UPDATE FROM other than the compiler's form")
        sub = src.select
        pipe, _ = self.from_source(sub.from_[0], env)
        by_alias = {a: e for e, a in sub.items if a}
        values = {}
        for c, e in s.sets:
            if isinstance(e, Case) and len(e.whens) == 1:
                e = e.whens[0][1]  # CASE WHEN <sent> THEN s."__vN" ELSE t.c END
            if isinstance(e, Col) and e.name in by_alias:
                e = by_alias[e.name]
            kv = self.write_value(s.table, c, e)
            if kv is not None:
                values[kv[0]] = kv[1]
        pipe.steps.append(step("update", **values))
        pipe.kind = "update"
        pipe.table = s.table
        return pipe

    def delete(self, s: Delete, env: Mapping[str, Select]) -> _Pipe:
        env = {**env, **dict(s.ctes)}
        w = s.where
        if (
            isinstance(w, InExpr) and w.select is not None and not w.negated
            and len(w.select.from_) == 1 and isinstance(w.select.from_[0], TableRef)
            and w.select.from_[0].name in env
        ):  # fmt: skip
            pipe = self.select(env[w.select.from_[0].name], env)
        else:
            pipe = _Pipe([step("fetch", self.ctx.cls(s.table))], None, s.table)
            if w is not None:
                self.where(pipe, w)
        pipe.steps.append(step("delete"))
        pipe.kind = "delete"
        pipe.table = s.table
        return pipe


def _conjuncts(e: Any) -> list[Any]:
    if isinstance(e, Binary) and e.op == "AND":
        return [*_conjuncts(e.left), *_conjuncts(e.right)]
    return [] if e is None else [e]


def lift_sql(
    sql: str, params: Sequence[Any] | None = None, ctx: LiftContext | None = None
) -> Lifted | NotLiftable:
    """Lift one statement (see the module docstring); ``params`` are the values of its
    ``?`` placeholders (they override ``ctx.params``)."""
    ctx = ctx or LiftContext()
    if params is not None:
        ctx = replace(ctx, params=tuple(params))
    try:
        stmt = parse_sql(sql)
    except SqlSyntaxError as exc:
        return NotLiftable("syntax", f"outside the parsed subset: {exc}", sql)
    lifter = _Lifter(ctx)
    try:
        match stmt:
            case Select():
                pipe = lifter.select(stmt, {})
            case Insert():
                pipe = lifter.insert(stmt)
            case Update():
                pipe = lifter.update(stmt, {})
            case Delete():
                pipe = lifter.delete(stmt, {})
    except _Stop as stop:
        return NotLiftable(stop.construct, stop.reason, sql)
    return Lifted(
        kind=pipe.kind,
        steps=tuple(pipe.steps),
        checks=tuple(pipe.checks),
        columns=tuple(pipe.columns or ()),
        notes=tuple(lifter.notes),
        table=pipe.table,
        sql=sql,
    )


def lift_trace(
    statements: Sequence[tuple[str, Sequence[Any] | None]], ctx: LiftContext | None = None
) -> list[Lifted | NotLiftable]:
    """Lift one request's statements in order. A read of one row by a value the same
    request just wrote to that table (an INSERT's or UPDATE's bound values) is an ORM
    refresh, not logic: its kind is ``"refresh"``."""
    ctx = ctx or LiftContext()
    written: dict[str, set[Any]] = {}
    out: list[Lifted | NotLiftable] = []
    for sql, params in statements:
        got = lift_sql(sql, params or (), ctx)
        if isinstance(got, Lifted) and got.table is not None:
            values = {binding_key(v) for v in (params or ()) if v is not None}
            if got.kind in ("insert", "update"):
                written.setdefault(got.table, set()).update(values)
            elif (
                got.kind == "read" and got.table in written and values
                and values <= written[got.table]
                and [x.op for x in got.steps if x.op != "select"] == ["fetch", "where"]
            ):  # fmt: skip
                note = "re-reads the row this request just wrote (an ORM refresh)"
                got = replace(got, kind="refresh", notes=(*got.notes, note))
        out.append(got)
    return out


# ======================================================================== combining lifts ==

FILTERS = frozenset({"fetch", "rows", "user", "where", "accessible", "owned", "join"})


def with_total(count: Lifted, rows: Lifted) -> Lifted | None:
    """A count(*) and a page read over the same filters: the page with ``total=True``
    (what ``page(..., total=True)`` compiles to in one statement)."""
    if count.kind != "count" or rows.kind != "read" or not rows.steps:
        return None
    if rows.steps[-1].op != "page":
        return None
    filters = tuple(s for s in rows.steps if s.op in FILTERS)
    if count.steps[:-1] != filters:
        return None
    last = rows.steps[-1]
    paged = Step("page", tuple(sorted((*last.args, ("total", "1")))))
    return replace(rows, steps=(*rows.steps[:-1], paged), notes=(*rows.notes, *count.notes))


def consolidate(lifts: Sequence[Lifted]) -> list[Lifted]:
    """One lift per statement role across many requests of a route: a count(*) folded
    into the page read it counts (:func:`with_total`); per statement :func:`shape`, the
    chain most of its lifts agree on; a chain equal to another but for its ``owned``
    step dropped (an admin's request, where the app skips the owner filter); then the
    distinct chains, most frequent first."""
    lifts = list(lifts)
    folded: list[Lifted] = []
    i = 0
    while i < len(lifts):
        a = lifts[i]
        if i + 1 < len(lifts) and a.kind == "count":
            combined = with_total(a, lifts[i + 1])
            if combined is not None:
                folded.append(combined)
                i += 2
                continue
        folded.append(a)
        i += 1
    # one statement shape: the chain most requests agree on (a value two inputs shared,
    # like skip == limit, can bind to the wrong name in a few requests)
    groups: dict[str, list[Lifted]] = {}
    for x in folded:
        groups.setdefault(shape(x.sql) if x.sql else repr(x.steps), []).append(x)
    counts: Counter[tuple[Step, ...]] = Counter()
    for group in groups.values():
        winner, n = Counter(x.steps for x in group).most_common(1)[0]
        counts[winner] += len(group)
    owned = step("accessible", by="user")
    keep = []
    for steps in counts:
        variant = any(
            len(other) == len(steps) + 1 and owned in other
            and tuple(s for s in other if s != owned) == steps
            for other in counts
        )  # fmt: skip
        if not variant:
            keep.append(steps)
    keep.sort(key=lambda s: -counts[s])
    by_steps = {x.steps: x for x in folded}
    return [by_steps[s] for s in keep]


# ============================================================ steps of a Lattice program ==

PIPE_OPS = frozenset(
    {"where", "accessible", "owned", "derive", "select", "sort", "limit", "page", "join", "rename"}
)
ALIAS_OPS = frozenset({"one", "require"})
WRITE_OPS = frozenset({"insert", "update", "delete"})
DROP_KWARGS = frozenset({"else_", "unique_or"})


@dataclass(frozen=True)
class ProgramStep:
    var: str
    input: str | None  # the input variable (None for fetch/insert)
    step: Step


def program_steps(source: str) -> dict[str, list[ProgramStep]]:
    """Each ``@endpoint`` of a Lattice program as its assignments ``var = op(input,
    ...)``, with arguments as canonical source: module constants and plain expression
    variables (``hashed = native.hash_password(password)``) substituted, the error
    keywords (``else_``, ``unique_or``) dropped."""
    tree = ast.parse(source)
    consts = {
        t.id: s.value
        for s in tree.body if isinstance(s, ast.Assign)
        for t in s.targets if isinstance(t, ast.Name)
    }  # fmt: skip
    out: dict[str, list[ProgramStep]] = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        exprs = dict(consts)
        steps: list[ProgramStep] = []
        for stmt in fn.body:
            if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                continue
            call = _Subst(exprs).visit(stmt.value)
            op = (
                call.func.id
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                else None
            )
            if op is None or op not in {*PIPE_OPS, *ALIAS_OPS, *WRITE_OPS, "fetch", "count"}:
                exprs[target.id] = call
                continue
            args = list(call.args)
            kws = {k.arg: k.value for k in call.keywords if k.arg and k.arg not in DROP_KWARGS}
            input_var = None
            if op not in ("fetch", "insert") and args and isinstance(args[0], ast.Name):
                input_var = args.pop(0).id
            op = API_OP_ALIASES.get(op, op)  # owned() is accessible() (the lifted name)
            steps.append(ProgramStep(target.id, input_var, step(op, *args, **kws)))
        out[fn.name] = steps
    return out


class _Subst(ast.NodeTransformer):
    def __init__(self, exprs: Mapping[str, ast.expr]) -> None:
        self.exprs = exprs

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.exprs:
            return self.visit(ast.parse(ast.unparse(self.exprs[node.id]), mode="eval").body)
        return node


def source_chain(
    steps: Sequence[ProgramStep], var: str, materialized: Collection[str] = ()
) -> tuple[tuple[Step, ...], tuple[str, ...]]:
    """The chain of steps producing ``var`` as a lift of its SQL would show it -- the
    source (``fetch(T)``, ``rows(v)`` for rows an earlier statement produced, ``user``
    for the caller) then the pipeline steps -- and the ``require`` conditions over it.
    ``one``/``require`` pass their input's rows through; a write's result, and every
    variable in ``materialized`` (the relations earlier statements read), is
    ``rows(<var>)``; the chain of a write ends with the write step."""
    by_var = {s.var: s for s in steps}
    done = set(materialized)

    def base(v: str) -> str:
        while v in by_var and by_var[v].step.op in ALIAS_OPS and by_var[v].input:
            v = by_var[v].input  # type: ignore[assignment]
        return v

    def chain(v: str, top: bool) -> list[Step]:
        v = base(v)
        s = by_var.get(v)
        if not top and v in done:
            return [step("rows", v)]
        if s is None:
            return [Step("user")] if v == "user" else [step("rows", v)]
        if s.step.op == "fetch":
            return [s.step]
        if s.step.op == "insert":
            return [s.step] if top else [step("rows", v)]
        if s.step.op in WRITE_OPS:
            if not top:
                return [step("rows", v)]
            return [*chain(s.input or "", False), s.step]
        return [*chain(s.input or "", False), s.step]

    found = chain(var, True)
    target = base(var)
    checks = tuple(
        s.step.args[0][1]
        for s in steps
        if s.step.op == "require" and s.input is not None and base(s.input) == target
    )
    return tuple(found), checks
