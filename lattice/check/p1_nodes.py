"""P1 — node whitelist and step signature shape.

Banned constructs get one diagnostic each; the checker never descends into a
banned node, so one mistake yields one diagnostic.
"""

from __future__ import annotations

import ast
import difflib

from lattice.catalog import (
    API_AMBIENT,
    CURRENT_USER,
    FN_ALIASES,
    FOR_EACH,
    IMPORTABLE,
    NAMESPACES,
    NATIVE_NS,
    OP_ALIASES,
    OPS_MODULE,
    SCHEMAS_MODULE,
    USER_PARAM,
)
from lattice.check.api_info import (
    Decl,
    ParamType,
    default_problem,
    endpoint_decorator,
    param_type,
    parse_decl,
    parse_returns,
)
from lattice.check.astutil import (
    INT64_MAX,
    INT64_MIN,
    is_constant_expr,
    is_list_constant_assign,
    is_table_annotation,
    is_valid_annotation,
    literal_value,
)
from lattice.check.context import CheckContext
from lattice.check.loop_hints import comprehension_hint, loop_hint

_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.BitAnd, ast.BitOr)
_CMP_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)

# Banned expression nodes: (code, message, hint)
_BANNED_EXPRS: dict[type[ast.AST], tuple[str, str, str | None]] = {
    ast.Lambda: ("L120", "lambda is not allowed", 'use a column expression such as col("x") * 2'),
    ast.ListComp: ("L121", "comprehensions are not allowed", "use a table op (derive/where/group)"),
    ast.SetComp: ("L121", "comprehensions are not allowed", "use a table op (derive/where/group)"),
    ast.DictComp: ("L121", "comprehensions are not allowed", "use a table op (derive/where/group)"),
    ast.GeneratorExp: (
        "L121",
        "comprehensions are not allowed",
        "use a table op (derive/where/group)",
    ),
    ast.IfExp: (
        "L122",
        "conditional expressions are not allowed",
        "use when(cond, then=..., otherwise=...)",
    ),
    ast.Subscript: ("L123", "subscripts are not allowed", 'reference columns with col("name")'),
    ast.JoinedStr: ("L124", "f-strings are not allowed", "use fn.concat(a, b)"),
    ast.NamedExpr: ("L125", "assignment expressions (:=) are not allowed", None),
    ast.BoolOp: (
        "L107",
        "'and'/'or' are not allowed",
        "combine Bool columns with & and | (parenthesize each side)",
    ),
    ast.Await: ("L131", "async constructs are not allowed", None),
    ast.Yield: ("L131", "yield is not allowed", None),
    ast.YieldFrom: ("L131", "yield is not allowed", None),
}

_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

# Banned statements: (code, message, hint); a loop's hint names the matching construct
# (lattice.check.loop_hints), an if-statement's stays this one
_LOOP = ("L127", "loops and if-statements are not allowed", "use where/derive/when instead")
_BANNED_STMTS: dict[type[ast.AST], tuple[str, str, str | None]] = {
    ast.AugAssign: ("L126", "augmented assignment is not allowed", "bind a new name with ="),
    ast.AnnAssign: ("L126", "annotated assignment is not allowed", "write 'name = op(...)'"),
    ast.For: _LOOP,
    ast.While: _LOOP,
    ast.If: _LOOP,
    ast.AsyncFor: _LOOP,
    ast.ClassDef: ("L128", "class definitions are not allowed", None),
    ast.Try: ("L129", "'try' is not allowed", None),
    ast.TryStar: ("L129", "'try' is not allowed", None),
    ast.With: ("L129", "'with' is not allowed", None),
    ast.AsyncWith: ("L129", "'with' is not allowed", None),
    ast.Raise: ("L129", "'raise' is not allowed", None),
    ast.Assert: ("L129", "'assert' is not allowed", None),
    ast.Delete: ("L129", "'del' is not allowed", None),
    ast.Pass: ("L129", "'pass' is not allowed", None),
    ast.Break: ("L129", "'break' is not allowed", None),
    ast.Continue: ("L129", "'continue' is not allowed", None),
    ast.Global: ("L130", "'global'/'nonlocal' are not allowed", None),
    ast.Nonlocal: ("L130", "'global'/'nonlocal' are not allowed", None),
    ast.AsyncFunctionDef: ("L131", "async functions are not allowed", "use a plain @step def"),
}


def run(module: ast.Module, ctx: CheckContext) -> None:
    seen_other = False
    for stmt in module.body:
        if isinstance(stmt, ast.ImportFrom):
            if seen_other:
                ctx.error("L102", stmt, "imports must come first", "move this import to the top")
            _check_import_from(stmt, ctx)
            continue
        seen_other = True
        api = ctx.profile.api is not None
        match stmt:
            case ast.Assign() if (
                api and not is_constant_expr(stmt.value) and not is_list_constant_assign(stmt)
            ):
                _top_level_statement(stmt, ctx)
            case ast.Expr() if api:
                _top_level_statement(stmt, ctx)
            case ast.Assign() | ast.Expr() if _is_for_each(stmt.value):
                ctx.error(
                    "L437",
                    stmt.value,
                    "for_each(...) is only available in API programs; a pipeline's only "
                    "effect is write(...)",
                    'write the rows out instead: write(table, "out.parquet")',
                )
            case ast.Assign():
                _check_assign(stmt, ctx)
            case ast.Expr():
                _check_expr(stmt.value, ctx, discarded=True)
            case ast.FunctionDef() if api and endpoint_decorator(stmt) is not None:
                _check_endpoint(stmt, ctx)
            case ast.FunctionDef():
                _check_step(stmt, ctx)
            case ast.Return():
                ctx.error(
                    "L101",
                    stmt,
                    "'return' is only allowed as the last statement of a @step",
                )
            case _:
                _check_other_stmt(stmt, ctx)


# ---- statements -------------------------------------------------------------


def _check_other_stmt(stmt: ast.stmt, ctx: CheckContext) -> None:
    """Statements that are never allowed anywhere (or, in a step, imports)."""
    if isinstance(stmt, ast.Import):
        names = ", ".join(a.name for a in stmt.names)
        ctx.error(
            "L103",
            stmt,
            f"'import {names}' is not allowed",
            f"import only with 'from {OPS_MODULE} import ...' or "
            f"'from {SCHEMAS_MODULE} import ...'",
        )
        return
    banned = _BANNED_STMTS.get(type(stmt))
    if banned is not None:
        code, msg, hint = banned
        if isinstance(stmt, ast.For | ast.AsyncFor | ast.While):
            hint = loop_hint(stmt, api=ctx.profile.api is not None)
        ctx.error(code, stmt, msg, hint)
        return
    ctx.error("L101", stmt, f"'{type(stmt).__name__}' is not allowed in a Lattice program")


def _check_import_from(stmt: ast.ImportFrom, ctx: CheckContext) -> None:
    if stmt.level != 0 or stmt.module not in (OPS_MODULE, SCHEMAS_MODULE):
        where = "." * stmt.level + (stmt.module or "")
        ctx.error(
            "L103",
            stmt,
            f"imports from '{where}' are not allowed",
            f"import only from '{OPS_MODULE}' or '{SCHEMAS_MODULE}'",
        )
        return
    for alias in stmt.names:
        if alias.name == "*":
            ctx.error("L103", stmt, "wildcard imports are not allowed", "list each name you use")
            continue
        if alias.asname is not None:
            ctx.error(
                "L104",
                alias,
                f"import alias '{alias.name} as {alias.asname}' is not allowed",
                f"import '{alias.name}' under its own name",
            )
            continue
        if stmt.module == OPS_MODULE:
            _check_ops_name(alias, ctx)
        else:
            _check_schema_name(alias, ctx)


def _check_ops_name(alias: ast.alias, ctx: CheckContext) -> None:
    name = alias.name
    if name in IMPORTABLE or (ctx.profile.api is not None and name in API_AMBIENT):
        return
    if name in OP_ALIASES:
        hint = f"the op is called '{OP_ALIASES[name]}'"
    elif name in FN_ALIASES:
        hint = f"use 'fn.{FN_ALIASES[name]}' (import 'fn')"
    else:
        close = difflib.get_close_matches(name, sorted(IMPORTABLE), n=1)
        hint = f"did you mean '{close[0]}'?" if close else None
    ctx.error("L112", alias, f"'{name}' is not provided by {OPS_MODULE}", hint)


def _check_schema_name(alias: ast.alias, ctx: CheckContext) -> None:
    api_tables = ctx.profile.api.tables if ctx.profile.api is not None else {}
    schemas = sorted({*ctx.profile.schemas, *api_tables})
    if alias.name in schemas:
        return
    ctx.error(
        "L112",
        alias,
        f"unknown schema '{alias.name}'",
        "available schemas: " + (", ".join(schemas) if schemas else "(none)"),
    )


def _check_assign(stmt: ast.Assign, ctx: CheckContext) -> None:
    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
        ctx.error(
            "L108",
            stmt,
            "assignment must bind exactly one name",
            "write one 'name = op(...)' statement per result",
        )
    _check_expr(stmt.value, ctx)


# ---- steps ------------------------------------------------------------------


def _check_step(fn: ast.FunctionDef, ctx: CheckContext) -> None:
    decos = fn.decorator_list
    if not (len(decos) == 1 and isinstance(decos[0], ast.Name) and decos[0].id == "step"):
        ctx.error(
            "L110",
            decos[0] if decos else fn,
            f"function '{fn.name}' must be decorated with exactly '@step'",
            "write '@step' on the line above 'def'",
        )
    _check_signature(fn, ctx)
    _check_step_body(fn, ctx)


def _check_signature(fn: ast.FunctionDef, ctx: CheckContext) -> None:
    args = fn.args
    shape_problems = []
    if args.posonlyargs:
        shape_problems.append("positional-only parameters")
    if args.kwonlyargs:
        shape_problems.append("keyword-only parameters")
    if args.vararg:
        shape_problems.append("*args")
    if args.kwarg:
        shape_problems.append("**kwargs")
    if args.defaults or any(d is not None for d in args.kw_defaults):
        shape_problems.append("default values")
    if fn.type_params:
        shape_problems.append("type parameters")
    if shape_problems:
        ctx.error(
            "L601",
            fn,
            f"step '{fn.name}' uses " + ", ".join(shape_problems),
            "list plain annotated parameters only, e.g. (orders: Table[Orders], n: Int)",
        )
    for a in args.args:
        ann = a.annotation
        if ann is None or not is_valid_annotation(ann):
            ctx.error(
                "L601",
                a,
                f"parameter '{a.arg}' needs a profile type annotation",
                "annotate with Table[Schema] or a scalar type (Int, Float, Str, Bool, Date)",
            )
        elif isinstance(ann, ast.Name) and ann.id == "Table":
            ctx.error(
                "L601",
                a,
                f"table parameter '{a.arg}' must name its schema",
                "write Table[SchemaName]",
            )
        elif isinstance(ann, ast.Name) and ann.id == "Date":
            ctx.error(
                "L601",
                a,
                f"step parameter '{a.arg}' cannot be Date: there is no Date literal to pass",
                "take a Str parameter and convert it in the step with "
                'fn.to_date(s, format="%Y-%m-%d")',
            )
    if not is_table_annotation(fn.returns):
        ctx.error(
            "L601",
            fn.returns if fn.returns is not None else fn,
            f"step '{fn.name}' must declare a table return type",
            "add '-> Table' or '-> Table[Schema]'",
        )


def _check_step_body(fn: ast.FunctionDef, ctx: CheckContext, kind: str = "step") -> None:
    body = fn.body
    if all(isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) for s in body):
        ctx.error(
            "L602",
            fn,
            f"{kind} '{fn.name}' has no body",
            "write one or more op statements and end with 'return <name>'",
        )
        return
    last = len(body) - 1
    assigns = 0
    for i, stmt in enumerate(body):
        match stmt:
            case ast.Assign():
                assigns += 1
                _check_assign(stmt, ctx)
            case ast.Return() if i != last:
                ctx.error(
                    "L602",
                    stmt,
                    f"'return' must be the last statement of a {kind}",
                    "remove the statements after it",
                )
            case ast.Return():
                if not isinstance(stmt.value, ast.Name):
                    ctx.error(
                        "L602",
                        stmt,
                        f"a {kind} must end with 'return <name>'",
                        "assign the result to a name first, then return that name",
                    )
            case ast.FunctionDef():
                ctx.error(
                    "L111",
                    stmt,
                    "nested function definitions are not allowed",
                    "define steps and endpoints at module level",
                )
            case ast.ImportFrom():
                ctx.error(
                    "L102",
                    stmt,
                    "imports must come first",
                    "move this import to the top of the file",
                )
            case ast.Expr(value=value) if _is_for_each(value) and i != last:
                if kind == "endpoint":
                    _check_expr(value, ctx)  # a statement: one outbox row per input row
                else:
                    ctx.error(
                        "L503",
                        stmt,
                        "steps must be pure: for_each(...) is only allowed in @endpoint "
                        "functions",
                        "call for_each in the endpoint, after the writes it reports",
                    )
            case ast.Expr():
                ctx.error(
                    "L602",
                    stmt,
                    f"a {kind} body may contain only assignments and a final 'return <name>'",
                    "assign the result to a name"
                    if kind == "endpoint"
                    else "assign the result to a name; call write() outside the step",
                )
            case _:
                _check_other_stmt(stmt, ctx)
    if not isinstance(body[-1], ast.Return):
        if any(isinstance(s, ast.Return) for s in body):
            return  # the misplaced return was already reported
        ctx.error(
            "L602",
            fn,
            f"{kind} '{fn.name}' must end with 'return <name>'",
            "return the final table name",
        )
    elif assigns == 0 and kind == "step":
        ctx.error(
            "L602",
            fn,
            f"step '{fn.name}' must contain at least one assignment before 'return'",
            "a step is a pipeline of one or more op statements",
        )


def _is_for_each(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == FOR_EACH
    )


# ---- endpoints (API profile) ----------------------------------------------------


def _top_level_statement(stmt: ast.Assign | ast.Expr, ctx: CheckContext) -> None:
    ctx.error(
        "L840",
        stmt,
        "API programs contain only constants (a scalar, or a list of column names), "
        "@step and @endpoint functions",
        "move this statement into an @endpoint function",
    )


def _check_endpoint(fn: ast.FunctionDef, ctx: CheckContext) -> None:
    deco = endpoint_decorator(fn)
    assert deco is not None
    if len(fn.decorator_list) != 1:
        ctx.error(
            "L110",
            fn,
            f"endpoint '{fn.name}' must have exactly one decorator, '@endpoint(...)'",
            "remove the other decorators",
        )
    decl = parse_decl(deco)
    if not isinstance(decl, Decl):
        ctx.error("L601", deco, decl[0], decl[1])
    _check_endpoint_signature(fn, ctx)
    _check_step_body(fn, ctx, "endpoint")


def _check_endpoint_signature(fn: ast.FunctionDef, ctx: CheckContext) -> None:
    args = fn.args
    shape_problems = []
    if args.posonlyargs:
        shape_problems.append("positional-only parameters")
    if args.kwonlyargs:
        shape_problems.append("keyword-only parameters")
    if args.vararg:
        shape_problems.append("*args")
    if args.kwarg:
        shape_problems.append("**kwargs")
    if fn.type_params:
        shape_problems.append("type parameters")
    if shape_problems:
        ctx.error(
            "L601",
            fn,
            f"endpoint '{fn.name}' uses " + ", ".join(shape_problems),
            "list plain annotated parameters, e.g. (order_id: Int(ge=1), user: CurrentUser)",
        )
    first_default = len(args.args) - len(args.defaults)
    for i, a in enumerate(args.args):
        pt = param_type(a.arg, a.annotation)
        if not isinstance(pt, ParamType):
            ctx.error("L601", a, pt[0], pt[1])
            continue
        if pt.scalar is None and a.arg != USER_PARAM:
            ctx.error(
                "L601",
                a,
                f"the {CURRENT_USER} parameter must be named '{USER_PARAM}'",
                f"write {USER_PARAM}: {CURRENT_USER}",
            )
        elif pt.scalar is not None and a.arg == USER_PARAM:
            ctx.error(
                "L601",
                a,
                f"parameter '{USER_PARAM}' is reserved for the caller",
                f"write {USER_PARAM}: {CURRENT_USER}, or rename this parameter",
            )
        default = args.defaults[i - first_default] if i >= first_default else None
        problem = default_problem(a.arg, pt, default)
        if problem is not None:
            ctx.error("L601", default if default is not None else a, problem[0], problem[1])
    ret = parse_returns(fn.returns)
    if not isinstance(ret[1], tuple):
        ctx.error("L601", fn.returns if fn.returns is not None else fn, ret[0], ret[1])


# ---- expressions ------------------------------------------------------------


def _check_expr(node: ast.expr, ctx: CheckContext, discarded: bool = False) -> None:
    match node:
        case ast.Constant() | ast.UnaryOp(op=ast.USub()) if isinstance(
            literal_value(node), int
        ) and not isinstance(literal_value(node), bool):
            _check_int_range(node, ctx)
        case ast.Constant() | ast.Name():
            return
        case ast.List(elts=elts) | ast.Tuple(elts=elts):
            for e in elts:
                _check_expr(e, ctx)
        case ast.Call():
            _check_call(node, ctx)
        case ast.Attribute(value=ast.Name(id=name)) if (
            name == USER_PARAM and ctx.profile.api is not None
        ):
            return  # user.<column>: a caller reference (P4 types it)
        case ast.Attribute():
            if not (isinstance(node.value, ast.Name) and node.value.id in _namespaces(ctx)):
                hint = f'reference columns with col("{node.attr}"); only agg.* and fn.* use dots'
                if ctx.profile.api is not None:
                    hint += f", and {USER_PARAM}.<column> for the caller's values (e.g. user.id)"
                ctx.error("L106", node, f"attribute access '.{node.attr}' is not allowed", hint)
        case ast.BinOp():
            if not isinstance(node.op, _BIN_OPS):
                ctx.error(
                    "L101",
                    node,
                    f"operator '{type(node.op).__name__}' is not allowed",
                    "allowed operators: + - * / % & |",
                )
            _check_expr(node.left, ctx)
            _check_expr(node.right, ctx)
        case ast.UnaryOp():
            if isinstance(node.op, ast.Not):
                ctx.error("L107", node, "'not' is not allowed", "negate a Bool column with ~")
            elif isinstance(node.op, ast.UAdd):
                ctx.error("L101", node, "unary '+' is not allowed", "remove the '+'")
            else:
                _check_expr(node.operand, ctx)
        case ast.Compare():
            if len(node.ops) != 1:
                ctx.error(
                    "L109",
                    node,
                    "chained comparisons are not allowed",
                    "combine two comparisons with &, e.g. (a < b) & (b < c)",
                )
            elif not isinstance(node.ops[0], _CMP_OPS):
                ctx.error(
                    "L113",
                    node,
                    f"comparison '{type(node.ops[0]).__name__}' is not allowed",
                    "use is_null(...) for null tests and == combined with | for membership",
                )
            _check_expr(node.left, ctx)
            for c in node.comparators:
                _check_expr(c, ctx)
        case _:
            banned = _BANNED_EXPRS.get(type(node))
            if banned is not None:
                code, msg, hint = banned
                if isinstance(node, _COMPREHENSIONS) and hint is not None:
                    api = ctx.profile.api is not None
                    hint = comprehension_hint(node, api, hint, discarded=discarded)
                ctx.error(code, node, msg, hint)
            else:
                ctx.error(
                    "L101", node, f"'{type(node).__name__}' is not allowed in a Lattice program"
                )


def _namespaces(ctx: CheckContext) -> frozenset[str]:
    """`agg.*` and `fn.*`; API programs also call `native.*` (ApiProfile.natives)."""
    return NAMESPACES | {NATIVE_NS} if ctx.profile.api is not None else NAMESPACES


def _check_int_range(node: ast.expr, ctx: CheckContext) -> None:
    v = literal_value(node)
    if isinstance(v, int) and not INT64_MIN <= v <= INT64_MAX:
        ctx.error(
            "L404",
            node,
            f"integer literal {v} does not fit in a 64-bit Int",
            f"use a value between {INT64_MIN} and {INT64_MAX}",
        )


def _check_call(node: ast.Call, ctx: CheckContext) -> None:
    func = node.func
    if isinstance(func, ast.Attribute):
        if not (isinstance(func.value, ast.Name) and func.value.id in _namespaces(ctx)):
            ctx.error(
                "L105",
                node,
                f"method call '.{func.attr}(...)' is not allowed",
                f"call the op as a function: {func.attr}(table, ...)",
            )
    elif not isinstance(func, ast.Name):
        ctx.error("L101", node, "calls must name an op or function directly")
    for a in node.args:
        if isinstance(a, ast.Starred):
            ctx.error("L101", a, "starred arguments are not allowed", "pass each argument")
        else:
            _check_expr(a, ctx)
    for kw in node.keywords:
        if kw.arg is None:
            ctx.error("L101", kw, "'**' arguments are not allowed", "pass each keyword explicitly")
        elif kw.arg == "on" and isinstance(kw.value, ast.Dict) and _is_join(func):
            _check_join_mapping(kw.value, ctx)
        else:
            _check_expr(kw.value, ctx)


def _is_join(func: ast.expr) -> bool:
    return isinstance(func, ast.Name) and func.id == "join"


def _check_join_mapping(node: ast.Dict, ctx: CheckContext) -> None:
    """join(..., on={"left_col": "right_col"}): the one place a dict literal is allowed
    (P4 checks that every key and value is a column name string)."""
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            ctx.error("L101", value, "'**' is not allowed in on=", "write each key pair")
            continue
        _check_expr(key, ctx)
        _check_expr(value, ctx)
