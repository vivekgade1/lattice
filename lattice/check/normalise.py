"""N1 — normalisation: ordinary Python spellings that have exactly one Lattice meaning are
rewritten to canonical Lattice before P1 (spec 2026-09-27 lattice-surface-design §1-2).

"The checker canonicalises, it does not reject": `normalise` is a pure AST -> AST rewrite
of the T0 statements (API and tabular programs; `@procedure` bodies are T2 and already
Python, so the caller never passes them). Every rewritten node carries the original
node's location, so later diagnostics point at the user's text.

| spelling                                   | canonical                                   |
|--------------------------------------------|---------------------------------------------|
| `t.x` / `T.x` where `t` is the op's input  | `col("x")` (`"x"` inside by=/cols=/order=)  |
| `a and b`, `a or b`, `not a`               | `a & b`, `a | b`, `~a` (left-folded)         |
| `x if c else y`                            | `when(c, then=x, otherwise=y)`              |
| `s.lower()` `.upper()` `.strip()`          | `fn.lower(s)` ...                           |
| `s.startswith(p)`, `s.replace(a, b)`       | `fn.starts_with(s, prefix=p)`, `fn.replace` |
| `len(s)`, `abs(x)`, `round(x, n)`          | `fn.len(s)`, `fn.abs(x)`, `fn.round(x, digits=n)` |
| `s in t` / `s not in t`                    | `fn.contains(t, sub=s)` / `~fn.contains(...)` |
| `x in [a, b]` / `x not in [a, b]`          | `(x == a) | (x == b)` / `(x != a) & (x != b)` |
| `x is None` / `x is not None`              | `is_null(x)` / `~is_null(x)`                 |
| `x == None` / `x != None`                  | the same, with an L306 warning               |
| `f"{a}-{b}"` (at least one text part)      | `a + "-" + b` (then concat, below)           |
| `when(c, x, y)`, `then_=` / `otherwise_=` / `else_=` | `when(c, then=x, otherwise=y)`     |
| `a < x < b`                                | `(a < x) & (x < b)`                          |
| `page(sort(where(fetch(T), ...)))`         | one statement per op, fresh names            |

Type-directed rewrites. The normaliser sees no types, so it only rewrites where the
spelling alone fixes the meaning, and leaves the one truly type-directed case to P4:

* `a + b` is NOT rewritten here. P4 types it: when both operands are `Str` it is a
  concatenation (recorded in `CheckContext.concats`), and `resolve_concats` then
  replaces it by `fn.concat(a, b)` (left-folded, since `+` is). Any other mix (`Str +
  Int`) stays P4's L402 at the `+` itself. No guess is ever made, so a wrong guess is
  impossible.
* The other rewrites are syntactic and produce typed calls: `s.lower()` on an `Int`,
  `"x" in col("qty")`, `a and b` on non-Bool operands, `len(n)` on a number all become
  an `fn.*` / `&` / `~` whose typing (P4) reports the L402/L403 at the user's span.
  Python's own meaning of those spellings on such operands (truthiness, `str(x)`) is
  never assumed.
* `t.x` becomes `col("x")` only when `t` is the input table of the enclosing op (or a
  name the input was filtered from, e.g. `T` of `fetch(T)` through where/sort/one/...);
  the column is still resolved against that table's schema (L401). An attribute of any
  other name stays P1's L106.

Split statements. A table op nested in a table-argument position is lifted into its own
statement bound to a fresh name `_s<k>_<line>` (k counts across the program, so names
are deterministic; a name the program already uses is skipped). Diagnostics never show
these names: `humanise` rewrites them to "part of the statement at line N".

Named column expressions (N1b). `x = <expr>` at statement level whose value reads
columns (`col(...)`, `t.x`, `agg.*`) but calls no op binds a *column expression*, not a
table: it is a macro. Its value is substituted at every use (typed there, so an unknown
column is P4's L401 at the use) and the definition is dropped. Every substituted node
carries the use's location; `annotate` adds "'x' is defined at line N" to a diagnostic at
a use. Only a name bound exactly once, never read before its definition and read at
least once is a macro: anything else stays as written (P2's L201, P3's L302).

Row values (N1b). Inside an endpoint's insert/update/derive keyword values, `r.x` where
`r` is not the op's input names a column of another table value: the value of column x
of r's one row. It stays `r.x` (canonical, like `user.x`); its node id is recorded in
`Normalised.row_reads` so P1 accepts it, P4 types it against r's schema and P6 requires
r to have exactly one row, to have passed an ownership check (L802), and x to be public
outside Admin endpoints (L803).

Every rewrite is recorded as (kind, span) for the benchmark's metrics.
"""

from __future__ import annotations

import ast
import builtins
import copy
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from lattice.catalog import (
    API_OP_SHAPES,
    ELSE_KW,
    EXPR_FUNCS,
    FOR_EACH,
    ITERATE,
    NAMESPACES,
    NATIVE_NS,
    OP_SHAPES,
    T2_T0_SHAPES,
    UNIQUE_OR,
    USER_PARAM,
)
from lattice.check.astutil import has_endpoint_decorator, step_param_kinds
from lattice.diagnostics import Diagnostic, Severity, Span, span_of

# ---- normalisation kinds (CheckResult.normalisations) ------------------------------------
ATTRIBUTE = "attribute"
CONCAT = "concat"
BOOL_OP = "bool_op"
NOT = "not"
IF_EXP = "if_exp"
METHOD = "method"
BUILTIN = "builtin"
IN_SUBSTRING = "in_substring"
IN_LIST = "in_list"
IS_NONE = "is_none"
EQ_NONE = "eq_none"
F_STRING = "f_string"
WHEN_KEYWORDS = "when_keywords"
CHAINED_COMPARE = "chained_compare"
SPLIT = "split"
COLUMN_EXPR = "column_expr"

Record = tuple[str, Span]

# `s.<method>(...)` -> `fn.<name>(s, ...)`: (fn name, keyword names for the positional args)
_METHODS: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "lower": ("lower", ()),
    "upper": ("upper", ()),
    "strip": ("strip", ()),
    "startswith": ("starts_with", ("prefix",)),
    "replace": ("replace", ("old", "new")),
}
# `<builtin>(x, ...)` -> `fn.<name>(x, ...)`: (fn name, keyword names for the other args)
_BUILTINS: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "len": ("len", ()),
    "abs": ("abs", ()),
    "round": ("round", ("digits",)),  # only round(x, n): round(x) is an Int in Python
}
# when() keyword spellings -> the canonical keyword
_WHEN_THEN = frozenset({"then", "then_"})
_WHEN_OTHERWISE = frozenset({"otherwise", "otherwise_", "else_"})
# keywords whose value is a list of column names: `t.x` there is the string "x"
_COLUMN_LISTS = frozenset({"by", "cols", "order"})
# ops whose result has exactly the rows' columns of their (one) input: a name bound by
# one of them may be read through the input's names (`T.x` after `fetch(T)`)
_FILTERS = frozenset(
    {"where", "sort", "limit", "accessible", "owned", "mine", "one", "require", "page"}
)
_CMP = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
EQ_NONE_CODE = "L306"
# ops whose keyword values may read a column of another one-row table value (`r.x`)
_ROW_VALUE_OPS = frozenset({"insert", "update", "derive"})
_NOT_VALUES = frozenset({"owner", ELSE_KW, UNIQUE_OR})
# a macro's value (every macro it uses substituted) may be at most this large
_MACRO_MAX_NODES = 1000


@dataclass(frozen=True)
class Normalised:
    body: list[ast.stmt]
    records: tuple[Record, ...] = ()
    warnings: tuple[Diagnostic, ...] = ()
    fresh: Mapping[str, int] = field(default_factory=dict)  # fresh name -> statement line
    # span of a column-expression use -> (its name, the line defining it)
    macro_uses: Mapping[Span, tuple[str, int]] = field(default_factory=dict)
    # ids of the `r.x` row-value reads (nodes of `body`)
    row_reads: frozenset[int] = frozenset()


def normalise(body: Sequence[ast.stmt], *, api: bool) -> Normalised:
    """The canonical form of `body` (the program's T0 statements). Pure: `body` is not
    modified; when nothing needs rewriting the same statements are returned."""
    work = copy.deepcopy(list(body))
    n = _Normaliser(work, api)
    out = n.run(n.macros(work))
    if not n.records and not n.row_reads:
        return Normalised(list(body))
    return Normalised(
        out,
        tuple(n.records),
        tuple(n.warnings),
        dict(n.fresh),
        dict(n.macro_uses),
        frozenset(n.row_reads),
    )


def resolve_concats(
    module: ast.Module, nodes: Mapping[int, ast.BinOp], types: dict
) -> list[Record]:
    """Replace every `a + b` that P4 typed as a Str concatenation (`nodes`, by id) by
    `fn.concat(a, b)`, carrying its recorded type over; returns the records."""
    if not nodes:
        return []
    records: list[Record] = []

    class _Resolve(ast.NodeTransformer):
        def visit_BinOp(self, node: ast.BinOp) -> ast.expr:
            self.generic_visit(node)
            if id(node) not in nodes:
                return node
            call = _fn_call(node, "concat", [node.left, node.right])
            if id(node) in types:
                types[id(call)] = types[id(node)]
            records.append((CONCAT, span_of(node)))
            return call

    _Resolve().visit(module)
    return records


def annotate(diag: Diagnostic, uses: Mapping[Span, tuple[str, int]]) -> Diagnostic:
    """`diag` at a column-expression use, naming the line that defines it."""
    use = uses.get(diag.span)
    if use is None:
        return diag
    name, line = use
    return replace(diag, message=f"{diag.message} ('{name}' is defined at line {line})")


def humanise(diag: Diagnostic, fresh: Mapping[str, int]) -> Diagnostic:
    """`diag` with every fresh name replaced by "part of the statement at line N"."""
    if not fresh:
        return diag

    def fix(text: str | None) -> str | None:
        if text is None:
            return None

        def sub(m: re.Match[str]) -> str:
            name = m.group(1)
            return f"part of the statement at line {fresh[name]}"

        names = "|".join(re.escape(n) for n in sorted(fresh, key=len, reverse=True))
        return re.sub(rf"'?\b({names})\b'?", sub, text)

    message, hint = fix(diag.message), fix(diag.hint)
    if message == diag.message and hint == diag.hint:
        return diag
    return replace(diag, message=message or diag.message, hint=hint)


# ---- helpers ------------------------------------------------------------------------------


def _at(new: ast.AST, old: ast.AST) -> ast.AST:
    """`new` (and every node inside it without a position) at `old`'s location."""
    ast.copy_location(new, old)
    for n in ast.walk(new):
        if "lineno" in n._attributes and getattr(n, "lineno", None) is None:
            ast.copy_location(n, old)
    return new


def _fn_call(
    old: ast.AST, name: str, args: list[ast.expr], kws: Iterable[tuple[str, ast.expr]] = ()
) -> ast.Call:
    func = ast.Attribute(value=ast.Name(id="fn", ctx=ast.Load()), attr=name, ctx=ast.Load())
    call = ast.Call(func=func, args=args, keywords=[ast.keyword(arg=k, value=v) for k, v in kws])
    return _at(call, old)  # type: ignore[return-value]


def _call(
    old: ast.AST, name: str, args: list[ast.expr], kws: Iterable[tuple[str, ast.expr]] = ()
) -> ast.Call:
    call = ast.Call(
        func=ast.Name(id=name, ctx=ast.Load()),
        args=args,
        keywords=[ast.keyword(arg=k, value=v) for k, v in kws],
    )
    return _at(call, old)  # type: ignore[return-value]


def _binop(old: ast.AST, left: ast.expr, op: ast.operator, right: ast.expr) -> ast.BinOp:
    return _at(ast.BinOp(left=left, op=op, right=right), old)  # type: ignore[return-value]


def _invert(old: ast.AST, operand: ast.expr) -> ast.UnaryOp:
    return _at(ast.UnaryOp(op=ast.Invert(), operand=operand), old)  # type: ignore[return-value]


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _names_in(body: Sequence[ast.stmt]) -> frozenset[str]:
    """Every identifier the program binds or reads (fresh names must avoid them all)."""
    found: set[str] = set()
    for stmt in body:
        for n in ast.walk(stmt):
            match n:
                case ast.Name(id=name):
                    found.add(name)
                case ast.arg(arg=name):
                    found.add(name)
                case ast.FunctionDef(name=name) | ast.ClassDef(name=name):
                    found.add(name)
                case ast.alias(name=name, asname=asname):
                    found.add(asname or name)
                case _:
                    pass
    return frozenset(found)


def _bound_in(body: Sequence[ast.stmt]) -> frozenset[str]:
    """Names the program itself binds (a builtin spelling of one is not rewritten)."""
    found: set[str] = set()
    for stmt in body:
        for n in ast.walk(stmt):
            match n:
                case ast.Name(id=name, ctx=ast.Store()):
                    found.add(name)
                case ast.arg(arg=name):
                    found.add(name)
                case ast.FunctionDef(name=name):
                    found.add(name)
                case _:
                    pass
    return frozenset(found)


def _stores(nodes: Iterable[ast.AST]) -> Counter[str]:
    """How often each name is bound (assignment targets, parameters, defs, imports)."""
    found: Counter[str] = Counter()
    for root in nodes:
        for n in ast.walk(root):
            match n:
                case ast.Name(id=name, ctx=ast.Store()):
                    found[name] += 1
                case ast.arg(arg=name):
                    found[name] += 1
                case ast.FunctionDef(name=name) | ast.ClassDef(name=name):
                    found[name] += 1
                case ast.alias(name=name, asname=asname):
                    found[asname or name] += 1
                case _:
                    pass
    return found


def _reads(name: str, nodes: Iterable[ast.AST]) -> bool:
    return any(
        isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
        for root in nodes
        for n in ast.walk(root)
    )


def _module_stores(body: Sequence[ast.stmt]) -> Counter[str]:
    """Names bound at module level (not inside function bodies)."""
    found: Counter[str] = Counter()
    for stmt in body:
        if isinstance(stmt, ast.FunctionDef):
            found[stmt.name] += 1
        else:
            found.update(_stores([stmt]))
    return found


def _at_use(value: ast.expr, use: ast.AST) -> ast.expr:
    """A copy of `value` with every node at `use`'s location."""
    out = copy.deepcopy(value)
    for n in ast.walk(out):
        if "lineno" in n._attributes:
            ast.copy_location(n, use)
    return out


class _Substitute(ast.NodeTransformer):
    def __init__(self, owner: _Normaliser, macros: Mapping[str, tuple[ast.expr, int]]) -> None:
        self.n = owner
        self.macros = macros

    def visit_Name(self, node: ast.Name) -> ast.expr:
        macro = self.macros.get(node.id)
        if macro is None or not isinstance(node.ctx, ast.Load):
            return node
        value, line = macro
        self.n.record(COLUMN_EXPR, node)
        self.n.macro_uses[span_of(node)] = (node.id, line)
        return _at_use(value, node)


# ---- the rewrite ------------------------------------------------------------------------------


class _Normaliser:
    def __init__(self, body: list[ast.stmt], api: bool) -> None:
        self.api = api
        self.shapes = dict(OP_SHAPES)
        if api:
            self.shapes.update(API_OP_SHAPES)
            self.shapes.update(T2_T0_SHAPES)
        self.steps = {
            s.name: s
            for s in body
            if isinstance(s, ast.FunctionDef) and not has_endpoint_decorator(s)
        }
        self.taken = set(_names_in(body))
        self.bound = _bound_in(body)
        self.namespaces = NAMESPACES | {NATIVE_NS} if api else NAMESPACES
        self.records: list[Record] = []
        self.warnings: list[Diagnostic] = []
        self.fresh: dict[str, int] = {}
        self.counter = 0
        self.macro_uses: dict[Span, tuple[str, int]] = {}
        self.row_reads: set[int] = set()
        # names a column expression may not take (P2 reports them as shadowing)
        self.reserved = (
            frozenset(self.shapes)
            | EXPR_FUNCS
            | self.namespaces
            | {USER_PARAM, "step", ITERATE, FOR_EACH}
            | frozenset(dir(builtins))
        )

    def record(self, kind: str, node: ast.AST) -> None:
        self.records.append((kind, span_of(node)))

    # ---- named column expressions (macros) ----------------------------------------------

    def macros(self, body: list[ast.stmt]) -> list[ast.stmt]:
        """`body` with every named column expression substituted at its uses and its
        definition dropped (see the module docstring)."""
        everywhere = _stores(body)
        module = _module_stores(body)
        return self._macro_block(body, {}, lambda name: everywhere[name] == 1, module)

    def _macro_block(
        self,
        stmts: list[ast.stmt],
        visible: Mapping[str, tuple[ast.expr, int]],
        once: Callable[[str], bool],
        module: Counter[str],
    ) -> list[ast.stmt]:
        macros = dict(visible)
        out: list[ast.stmt] = []
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, ast.FunctionDef):
                local = _stores(stmt.body) + _stores([stmt.args])
                once_here = lambda name, local=local: local[name] + module[name] == 1  # noqa: E731
                stmt.body = self._macro_block(stmt.body, macros, once_here, module)
                out.append(stmt)
                continue
            if isinstance(stmt, ast.Assign | ast.Expr | ast.Return) and stmt.value is not None:
                if macros:
                    stmt.value = _Substitute(self, macros).visit(stmt.value)
            name = self._macro_name(stmt)
            if (
                name is not None
                and once(name)
                and not _reads(name, stmts[:i])
                and _reads(name, stmts[i + 1 :])
            ):
                assert isinstance(stmt, ast.Assign)
                macros[name] = (stmt.value, stmt.lineno)
                continue
            out.append(stmt)
        return out

    def _macro_name(self, stmt: ast.stmt) -> str | None:
        """The name `stmt` binds if it binds a column expression: a value that reads a
        column and calls no op, step or loop."""
        match stmt:
            case ast.Assign(targets=[ast.Name(id=name)], value=value):
                pass
            case _:
                return None
        if name in self.reserved or name in self.steps:
            return None
        reads = False
        size = 0
        for n in ast.walk(value):
            size += 1
            if self._unit(n) is not None:
                return None
            match n:
                case ast.Call(func=ast.Name(id="col")):
                    reads = True
                case ast.Attribute(value=ast.Name(id=base)) if base == "agg" or (
                    base not in self.namespaces and base != USER_PARAM
                ):
                    reads = True
                case _:
                    pass
        return name if reads and size <= _MACRO_MAX_NODES else None

    # ---- statements ---------------------------------------------------------------------

    def run(self, body: list[ast.stmt]) -> list[ast.stmt]:
        module_scope: dict[str, frozenset[str]] = {}
        out: list[ast.stmt] = []
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef):
                endpoint = has_endpoint_decorator(stmt)
                stmt.body = self._block(stmt.body, {}, function=True, endpoint=endpoint)
                out.append(stmt)
            elif isinstance(stmt, ast.Assign | ast.Expr):
                out.extend(self._block([stmt], module_scope, function=False, endpoint=False))
            else:
                out.append(stmt)
        return out

    def _block(
        self,
        stmts: list[ast.stmt],
        aliases: dict[str, frozenset[str]],
        *,
        function: bool,
        endpoint: bool,
    ) -> list[ast.stmt]:
        """Normalise `stmts`; `r.x` row values are recorded only inside an endpoint body
        (`endpoint`), where P6 checks them -- in a step they stay L106."""
        out: list[ast.stmt] = []
        for stmt in stmts:
            if not isinstance(stmt, ast.Assign | ast.Expr | ast.Return) or stmt.value is None:
                out.append(stmt)
                continue
            if isinstance(stmt, ast.Return) and not function:
                out.append(stmt)
                continue
            lifted: list[ast.Assign] = []
            value = stmt.value
            if self._unit(value) is not None:
                assert isinstance(value, ast.Call)
                self._split(value, stmt.lineno, lifted)
                if isinstance(stmt, ast.Return):
                    name = self._fresh(stmt.lineno)
                    lifted.append(self._bind(name, value))
                    stmt.value = _at(ast.Name(id=name, ctx=ast.Load()), value)  # type: ignore[assignment]
                    self.record(SPLIT, value)
            for s in [*lifted, stmt]:
                assert s.value is not None
                s.value = self._statement_value(s.value, aliases, endpoint=endpoint)
                if (
                    isinstance(s, ast.Assign)
                    and len(s.targets) == 1
                    and isinstance(s.targets[0], ast.Name)
                ):
                    aliases[s.targets[0].id] = self._aliases_of(s.targets[0].id, s.value, aliases)
                out.append(s)
        return out

    def _aliases_of(
        self, name: str, value: ast.expr, aliases: Mapping[str, frozenset[str]]
    ) -> frozenset[str]:
        """The names `name`'s rows may be read through: itself, plus -- when it is bound
        by fetch(T) / load(..., schema=T) or by a filter of another name -- those."""
        own = frozenset({name})
        match value:
            case ast.Call(func=ast.Name(id="fetch"), args=[ast.Name(id=table)]):
                return own | {table}
            case ast.Call(func=ast.Name(id="load"), keywords=kws):
                schema = next((k.value for k in kws if k.arg == "schema"), None)
                if isinstance(schema, ast.Name):
                    return own | {schema.id}
            case ast.Call(func=ast.Name(id=op), args=[ast.Name(id=src), *_]) if op in _FILTERS:
                return own | aliases.get(src, frozenset({src}))
            case _:
                pass
        return own

    # ---- splitting nested ops -------------------------------------------------------------

    def _unit(self, node: ast.AST) -> tuple[int, ...] | None:
        """The table-argument positions of a unit call (op, step, iterate, for_each)."""
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            return None
        name = node.func.id
        if name == ITERATE:
            return (1,)
        if name == FOR_EACH:
            return (0,)
        if name in self.shapes:
            return tuple(range(self.shapes[name].tables))
        if name in self.steps:
            kinds = step_param_kinds(self.steps[name])
            return tuple(i for i, (_, k) in enumerate(kinds) if k == "table")
        return None

    def _split(self, call: ast.Call, line: int, lifted: list[ast.Assign]) -> None:
        positions = self._unit(call) or ()
        for i in positions:
            if i >= len(call.args):
                continue
            arg = call.args[i]
            if self._unit(arg) is None:
                continue
            assert isinstance(arg, ast.Call)
            self._split(arg, line, lifted)
            name = self._fresh(line)
            lifted.append(self._bind(name, arg))
            call.args[i] = _at(ast.Name(id=name, ctx=ast.Load()), arg)  # type: ignore[call-overload]
            self.record(SPLIT, arg)

    def _fresh(self, line: int) -> str:
        while True:
            self.counter += 1
            name = f"_s{self.counter}_{line}"
            if name not in self.taken:
                self.taken.add(name)
                self.fresh[name] = line
                return name

    @staticmethod
    def _bind(name: str, value: ast.expr) -> ast.Assign:
        target = ast.Name(id=name, ctx=ast.Store())
        return _at(ast.Assign(targets=[target], value=value), value)  # type: ignore[return-value]

    # ---- expressions ------------------------------------------------------------------------

    def _statement_value(
        self, value: ast.expr, aliases: Mapping[str, frozenset[str]], *, endpoint: bool
    ) -> ast.expr:
        """Normalise one statement's value; inside a unit call over one input table, the
        input's names read its columns."""
        positions = self._unit(value)
        if positions is None or not isinstance(value, ast.Call):
            return _Expr(self, frozenset()).visit(value)
        context: frozenset[str] = frozenset()
        if len(positions) == 1 and positions[0] < len(value.args):
            first = value.args[positions[0]]
            if isinstance(first, ast.Name) and first.id != USER_PARAM:
                context = aliases.get(first.id, frozenset({first.id}))
        rewrite = _Expr(self, context)
        value.args = [a if i in positions else rewrite.visit(a) for i, a in enumerate(value.args)]
        rows = (
            self.api
            and endpoint
            and isinstance(value.func, ast.Name)
            and value.func.id in _ROW_VALUE_OPS
        )
        for kw in value.keywords:
            if kw.arg in _COLUMN_LISTS and isinstance(kw.value, ast.List | ast.Tuple):
                kw.value.elts = [self._column_name(e, context) for e in kw.value.elts]
            kw.value = rewrite.visit(kw.value)
            if rows and kw.arg not in _NOT_VALUES:
                self._row_reads(kw.value)
        return value

    def _row_reads(self, node: ast.expr) -> None:
        """Record every `r.x` left in a write/derive value (not the op's input: that one
        became col("x")) as a row-value read."""
        for n in ast.walk(node):
            match n:
                case ast.Attribute(value=ast.Name(id=name)) if (
                    name not in self.namespaces and name != USER_PARAM
                ):
                    self.row_reads.add(id(n))
                case _:
                    pass

    def _column_name(self, node: ast.expr, context: frozenset[str]) -> ast.expr:
        match node:
            case ast.Attribute(value=ast.Name(id=name), attr=attr) if name in context:
                self.record(ATTRIBUTE, node)
                return _at(ast.Constant(value=attr), node)  # type: ignore[return-value]
            case _:
                return node


class _Expr(ast.NodeTransformer):
    """The expression rewrites (bottom-up), with the enclosing op's input names."""

    def __init__(self, owner: _Normaliser, context: frozenset[str]) -> None:
        self.n = owner
        self.context = context

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        match node:
            case ast.Attribute(value=ast.Name(id=name), attr=attr) if (
                name in self.context and name not in self.n.namespaces and name != USER_PARAM
            ):
                self.n.record(ATTRIBUTE, node)
                return _call(node, "col", [_at(ast.Constant(value=attr), node)])  # type: ignore[list-item]
            case _:
                self.generic_visit(node)
                return node

    def visit_Call(self, node: ast.Call) -> ast.expr:
        func = node.func
        if isinstance(func, ast.Attribute) and not (
            isinstance(func.value, ast.Name) and func.value.id in self.n.namespaces
        ):
            method = _METHODS.get(func.attr)
            if method is not None and len(node.args) == len(method[1]) and not node.keywords:
                receiver = self.visit(func.value)
                args = [self.visit(a) for a in node.args]
                self.n.record(METHOD, node)
                return _fn_call(node, method[0], [receiver], zip(method[1], args, strict=True))
            # any other method stays a method call (P1's L105); only its parts normalise
            func.value = self.visit(func.value)
            node.args = [self.visit(a) for a in node.args]
            for kw in node.keywords:
                kw.value = self.visit(kw.value)
            return node
        self.generic_visit(node)
        if isinstance(func, ast.Name):
            builtin = _BUILTINS.get(func.id)
            if (
                builtin is not None
                and func.id not in self.n.bound
                and len(node.args) == 1 + len(builtin[1])
                and not node.keywords
            ):
                first, *rest = node.args
                self.n.record(BUILTIN, node)
                return _fn_call(node, builtin[0], [first], zip(builtin[1], rest, strict=True))
            if func.id == "when":
                return self._when(node)
        return node

    def _when(self, node: ast.Call) -> ast.Call:
        """when(c, x, y) / then_= / otherwise_= / else_= -> when(c, then=x, otherwise=y)."""
        if not node.args or any(isinstance(a, ast.Starred) for a in node.args):
            return node
        cond, *pos = node.args
        then: list[ast.expr] = pos[:1]
        other: list[ast.expr] = pos[1:2]
        if len(pos) > 2:
            return node
        spelled_canonically = len(pos) == 0
        for kw in node.keywords:
            if kw.arg in _WHEN_THEN:
                then.append(kw.value)
                spelled_canonically &= kw.arg == "then"
            elif kw.arg in _WHEN_OTHERWISE:
                other.append(kw.value)
                spelled_canonically &= kw.arg == "otherwise"
            else:
                return node  # an unknown keyword: P4's L404
        if spelled_canonically or len(then) != 1 or len(other) != 1:
            return node
        self.n.record(WHEN_KEYWORDS, node)
        return _call(node, "when", [cond], [("then", then[0]), ("otherwise", other[0])])

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.expr:
        self.generic_visit(node)
        op: ast.operator = ast.BitAnd() if isinstance(node.op, ast.And) else ast.BitOr()
        result = node.values[0]
        for v in node.values[1:]:
            result = _binop(node, result, op, v)
        self.n.record(BOOL_OP, node)
        return result

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.expr:
        self.generic_visit(node)
        if isinstance(node.op, ast.Not):
            self.n.record(NOT, node)
            return _invert(node, node.operand)
        return node

    def visit_IfExp(self, node: ast.IfExp) -> ast.expr:
        self.generic_visit(node)
        self.n.record(IF_EXP, node)
        return _call(node, "when", [node.test], [("then", node.body), ("otherwise", node.orelse)])

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.expr:
        """f"{a}-{b}" -> a + "-" + b, only with a text part (so the result must be a Str:
        `+` of the parts is then typed by P4) and plain `{x}` fields."""
        parts: list[ast.expr] = []
        for v in node.values:
            match v:
                case ast.Constant(value=str(text)):
                    if text:
                        parts.append(v)
                case ast.FormattedValue(value=inner, conversion=-1, format_spec=None):
                    parts.append(self.visit(inner))
                case _:
                    return node
        if not any(isinstance(p, ast.Constant) for p in parts) or len(parts) < 2:
            return node
        result = parts[0]
        for p in parts[1:]:
            result = _binop(node, result, ast.Add(), p)
        self.n.record(F_STRING, node)
        return result

    def visit_Compare(self, node: ast.Compare) -> ast.expr:
        self.generic_visit(node)
        if len(node.ops) == 1:
            return self._single(node, node.left, node.ops[0], node.comparators[0])
        operands = [node.left, *node.comparators]
        if not all(isinstance(op, _CMP) for op in node.ops):
            return node  # e.g. `a < b in c`: P1 reports it
        self.n.record(CHAINED_COMPARE, node)
        result: ast.expr | None = None
        for i, op in enumerate(node.ops):
            left = operands[i] if i == 0 else copy.deepcopy(operands[i])
            pair = _at(ast.Compare(left=left, ops=[op], comparators=[operands[i + 1]]), node)
            result = pair if result is None else _binop(node, result, ast.BitAnd(), pair)  # type: ignore[arg-type]
        assert result is not None
        return result

    def _single(
        self, node: ast.Compare, left: ast.expr, op: ast.cmpop, right: ast.expr
    ) -> ast.expr:
        match op:
            case ast.Is() | ast.IsNot() if _is_none(right) or _is_none(left):
                subject = left if _is_none(right) else right
                self.n.record(IS_NONE, node)
                test = _call(node, "is_null", [subject])
                return _invert(node, test) if isinstance(op, ast.IsNot) else test
            case ast.Eq() | ast.NotEq() if _is_none(right) or _is_none(left):
                subject = left if _is_none(right) else right
                self.n.record(EQ_NONE, node)
                negated = isinstance(op, ast.NotEq)
                spelled = "is not None" if negated else "is None"
                self.n.warnings.append(
                    Diagnostic(
                        EQ_NONE_CODE,
                        Severity.WARNING,
                        span_of(node),
                        f"'{'!=' if negated else '=='} None' is read as a null test",
                        f"write {'~' if negated else ''}is_null(x) (Python: x {spelled})",
                    )
                )
                test = _call(node, "is_null", [subject])
                return _invert(node, test) if negated else test
            case ast.In() | ast.NotIn() if isinstance(right, ast.List | ast.Tuple):
                if not right.elts or any(isinstance(e, ast.Starred) for e in right.elts):
                    return node
                self.n.record(IN_LIST, node)
                negated = isinstance(op, ast.NotIn)
                cmp: ast.cmpop = ast.NotEq() if negated else ast.Eq()
                join: ast.operator = ast.BitAnd() if negated else ast.BitOr()
                result: ast.expr | None = None
                for i, e in enumerate(right.elts):
                    subject = left if i == 0 else copy.deepcopy(left)
                    test = _at(ast.Compare(left=subject, ops=[cmp], comparators=[e]), node)
                    result = test if result is None else _binop(node, result, join, test)  # type: ignore[arg-type]
                assert result is not None
                return result
            case ast.In() | ast.NotIn():
                self.n.record(IN_SUBSTRING, node)
                test = _fn_call(node, "contains", [right], [("sub", left)])
                return _invert(node, test) if isinstance(op, ast.NotIn) else test
            case _:
                return node
