"""What every lifter shares: the route as lifters see it, the fragment a lifter returns,
and the AST patterns the idioms are made of (spec 2026-09-27 importer §3.2).

A lifter is a module with ``match(route, model) -> LiftFragment | None``. ``route`` is a
:class:`RouteSource`: the extracted :class:`~lattice.importer.extract.Route` plus its
function's AST, the package index (to resolve names to what they denote) and the
generated profile's API settings (owner columns, private columns...). A lifter matches
AST *shapes* -- which statement calls what, which variable holds the caller, the body or
a row -- never variable or route names, and resolves every callee through the imports
(``sqlmodel:select``, ``fastapi:HTTPException``, a table class, a package helper).

A :class:`LiftFragment` claims some top-level statements of the route body (by index,
docstring excluded) and says what they become in Lattice: steps (one binding each),
the endpoint parameters they use, the errors they raise, policy deviations they adopt
(:mod:`lattice.importer.policy`) and behaviour changes they accept. The composer
(:func:`lattice.importer.lifters.lift_route`) accepts a route only when the fragments
of all lifters claim every statement exactly once and every request parameter of the
original is used; anything else is left to the LLM (I5), never guessed.

Step expressions are text with two kinds of holes: ``{@var}`` is the current Lattice
binding of the original's Python variable ``var`` (the caller's variable starts as
``user``) and ``{#name}`` the binding of this fragment's own step named ``name``.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lattice.importer.extract import DataModel, FieldSpec, RepoModel, Route, TableModel

if TYPE_CHECKING:
    from lattice.importer.extract import _Index
    from lattice.importer.policy import Policy
    from lattice.profile import ApiProfile

# The Lattice error for each HTTP status a `raise HTTPException` may carry.
ERROR_KINDS: Mapping[int, str] = {
    404: "NotFound",
    403: "Forbidden",
    409: "Conflict",
    400: "BadRequest",
}
PAGE_LIMIT_MAX = 100  # Lattice's bound on a page size parameter (L831)
USER = "user"  # the Lattice name of the caller


@dataclass(frozen=True)
class Step:
    """One Lattice statement ``name = op(args..., key=value...)``. ``binds``: the
    original's variable that now denotes this step's result. ``writes``: the columns an
    ``insert``/``update`` writes (a ``unique_or=`` modifier attaches to it)."""

    name: str
    op: str
    args: tuple[str, ...] = ()
    kwargs: tuple[tuple[str, str], ...] = ()
    binds: str | None = None
    writes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Param:
    """An endpoint parameter: ``source`` is ``path``, ``query`` or ``body``;
    ``annotation`` and ``default`` are Lattice source text."""

    name: str
    source: str
    annotation: str
    default: str | None = None


@dataclass(frozen=True)
class Modifier:
    """A keyword added to the first later write step of ``subject`` writing ``column``
    (e.g. ``unique_or=Conflict("...")`` from a uniqueness pre-check)."""

    keyword: str
    value: str
    subject: str
    column: str


@dataclass(frozen=True)
class LiftFragment:
    """What one lifter makes of some statements of a route (see the module docstring).

    ``stmts``: indices into the route body (docstring excluded); ``consumes``: the
    original's request parameters (path, query, body or form) the fragment reads;
    ``returns``: the returned binding (``{@var}`` / ``{#name}``) when the fragment holds
    the ``return``; ``success``: ``Page`` for a page; ``endpoint``: extra ``@endpoint``
    keywords (``body='"form"'``); ``response``: the response model when it is not the
    route's own (a page's item model); ``notes``: behaviour changes accepted."""

    lifter: str
    idiom: str
    stmts: tuple[int, ...]
    steps: tuple[Step, ...] = ()
    params: tuple[Param, ...] = ()
    consumes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    returns: str | None = None
    success: str | None = None
    endpoint: tuple[tuple[str, str], ...] = ()
    response: str | None = None
    modifiers: tuple[Modifier, ...] = ()
    policies: tuple[Policy, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteSource:
    """A route as the lifters see it (see the module docstring)."""

    route: Route
    fn: ast.FunctionDef | ast.AsyncFunctionDef
    index: _Index
    api: ApiProfile
    model: RepoModel
    body: tuple[ast.stmt, ...] = field(default=())

    def __post_init__(self) -> None:
        stmts = list(self.fn.body)
        if stmts and _is_docstring(stmts[0]):
            stmts = stmts[1:]
        object.__setattr__(self, "body", tuple(stmts))

    # -- names --

    def resolve(self, node: ast.expr | None, module: str | None = None) -> str | None:
        return self.index.resolve(module or self.route.module, node)

    def resolves_to(self, node: ast.expr | None, *quals: str, module: str | None = None) -> bool:
        q = self.resolve(node, module)
        return q is not None and any(q == x or q.endswith(x) for x in quals)

    def table(self, node: ast.expr | None, module: str | None = None) -> TableModel | None:
        """The table class a name denotes."""
        q = self.resolve(node, module)
        return next((t for t in self.model.tables if f"{t.module}:{t.name}" == q), None)

    def data_model(self, name: str | None) -> DataModel | None:
        return next((m for m in self.model.models if m.name == name), None)

    # -- the route's parameters --

    def _dep_param(self, *kinds: str) -> str | None:
        deps = {d.name: d for d in self.model.dependencies}
        for p in self.route.params:
            if p.source == "dependency" and p.dependency in deps:
                if deps[p.dependency].kind in kinds:
                    return p.name
        return None

    @property
    def session(self) -> str | None:
        return self._dep_param("session")

    @property
    def user(self) -> str | None:
        """The parameter holding the caller (a current-user dependency)."""
        return self._dep_param("current_user", "active_user", "admin", "roles")

    def params_of(self, source: str) -> list[str]:
        return [p.name for p in self.route.params if p.source == source]

    def param(self, name: str):  # noqa: ANN201 -- RouteParam
        return next((p for p in self.route.params if p.name == name), None)

    @property
    def users_table(self) -> TableModel | None:
        name = self.api.users_table
        return next((t for t in self.model.tables if t.name == name), None)

    @property
    def admin_column(self) -> str | None:
        return next((d.admin_column for d in self.model.dependencies if d.kind == "admin"), None)

    def owner(self, table: TableModel) -> str | None:
        schema = self.api.tables.get(table.name)
        return schema.owner if schema is not None else None

    def private(self, table: TableModel) -> tuple[str, ...]:
        schema = self.api.tables.get(table.name)
        return tuple(schema.private) if schema is not None else ()

    def key(self, table: TableModel) -> FieldSpec | None:
        keys = [c for c in table.columns if c.primary_key]
        return keys[0] if len(keys) == 1 else None

    def row_table(self, var: str) -> TableModel | None:
        """The table a variable holds a row of: the caller's variable (the users table),
        or a variable assigned ``session.get(T, ...)`` / ``T.model_validate(...)``."""
        if var == self.user:
            return self.users_table
        for stmt in self.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=name)], value=value) if name == var:
                    if (t := self.session_get(value)) is not None:
                        return t[0]
                    if (m := model_validate(value)) is not None:
                        return self.table(m[0])
        return None

    # -- ORM calls --

    def is_session(self, node: ast.expr | None) -> bool:
        return isinstance(node, ast.Name) and node.id == self.session

    def session_call(self, node: ast.AST | None, method: str) -> ast.Call | None:
        """``session.<method>(...)``."""
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == method
            and self.is_session(node.func.value)
        ):
            return node
        return None

    def session_stmt(self, stmt: ast.stmt, method: str, var: str | None = None) -> bool:
        """The statement ``session.<method>(var)`` (``session.<method>()`` if no var)."""
        if not isinstance(stmt, ast.Expr):
            return False
        call = self.session_call(stmt.value, method)
        if call is None or call.keywords:
            return False
        if var is None:
            return not call.args
        return len(call.args) == 1 and is_name(call.args[0], var)

    def session_get(self, node: ast.expr | None) -> tuple[TableModel, ast.expr] | None:
        """``session.get(T, key)``: (T, the key expression)."""
        call = self.session_call(node, "get")
        if call is None or len(call.args) != 2 or call.keywords:
            return None
        table = self.table(call.args[0])
        return (table, call.args[1]) if table is not None else None

    def http_raise(self, stmt: ast.stmt, module: str | None = None) -> tuple[int, str] | None:
        """``raise HTTPException(status_code=<int or status.HTTP_...>, detail="...")``
        with a literal detail: (status, detail)."""
        if not (isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call)) or stmt.cause:
            return None
        call = stmt.exc
        if not self.resolves_to(call.func, ":HTTPException", module=module):
            return None
        kw = {k.arg: k.value for k in call.keywords}
        if None in kw or set(kw) - {"status_code", "detail"}:
            return None
        status = kw.get("status_code", call.args[0] if call.args else None)
        detail = kw.get("detail", call.args[1] if len(call.args) > 1 else None)
        if len(call.args) > 2:
            return None
        code = status_of(status)
        if code is None or not (isinstance(detail, ast.Constant) and isinstance(detail.value, str)):
            return None
        return code, detail.value

    def guard_raise(self, stmt: ast.stmt) -> tuple[ast.expr, int, str] | None:
        """``if <test>: raise HTTPException(...)`` with no else: (test, status, detail)."""
        if not isinstance(stmt, ast.If) or stmt.orelse or len(stmt.body) != 1:
            return None
        raised = self.http_raise(stmt.body[0])
        return (stmt.test, *raised) if raised is not None else None


# ---- AST helpers --------------------------------------------------------------------------


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def status_of(node: ast.expr | None) -> int | None:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.Attribute) and node.attr.startswith("HTTP_"):
        digits = node.attr[5:8]
        return int(digits) if digits.isdigit() else None
    return None


def is_name(node: ast.AST | None, name: str | None) -> bool:
    return name is not None and isinstance(node, ast.Name) and node.id == name


def attr_of(node: ast.AST | None, var: str | None) -> str | None:
    """``var.<attr>``: the attribute name."""
    if isinstance(node, ast.Attribute) and is_name(node.value, var):
        return node.attr
    return None


def is_falsy_test(test: ast.expr, var: str) -> bool:
    """``not var`` or ``var is None``."""
    match test:
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            return is_name(operand, var)
        case ast.Compare(left=left, ops=[ast.Is()], comparators=[ast.Constant(value=None)]):
            return is_name(left, var)
    return False


def model_validate(node: ast.expr | None) -> tuple[ast.expr, ast.Call] | None:
    """``M.model_validate(...)``: (M, the call)."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_validate"
    ):
        return node.func.value, node
    return None


def assigned(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    """``name = value``: (name, value)."""
    match stmt:
        case ast.Assign(targets=[ast.Name(id=name)], value=value):
            return name, value
    return None


def method_chain(node: ast.expr) -> list[tuple[str, ast.Call]]:
    """``a(...).b(...).c(...)`` from the outside in: [("c", call), ("b", call), ...]
    down to the innermost call, which is included with its function's text."""
    out: list[tuple[str, ast.Call]] = []
    while isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            out.append((node.func.attr, node))
            node = node.func.value
        else:
            out.append(("", node))
            break
    return out


def lit(value: object) -> str:
    """A Python/Lattice literal (strings double-quoted)."""
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return repr(value)


def str_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(lit(v) for v in values) + "]"


def plural(table: TableModel) -> str:
    return table.name.lower() + "s"


def error(kind: str, detail: str) -> str:
    return f"{kind}({lit(detail)})"


def field_annotation(f: FieldSpec) -> str | None:
    """The Lattice parameter type of a body field (with its bounds), or None when the
    field's type or bounds have no Lattice spelling."""
    name = {
        "Str": "Str",
        "Email": "Email",
        "Int": "Int",
        "Float": "Float",
        "Bool": "Bool",
        "Uuid": "Uuid",
        "DateTime": "DateTime",
        "Date": "Date",
    }.get(f.type or "")
    if name is None:
        return None
    if f.choices:
        # an Enum / Literal field: OneOf of its values (the length bounds a Str field
        # may also carry are implied by the listed values)
        if name != "Str" or not all(isinstance(c, str) for c in f.choices):
            return None
        return f"OneOf({', '.join(lit(c) for c in f.choices)})"
    bounds: list[str] = []
    if name in ("Str", "Email"):
        if f.ge is not None or f.le is not None or f.gt is not None or f.lt is not None:
            return None
        if f.min_length is not None:
            bounds.append(f"min_len={f.min_length}")
        if f.max_length is not None:
            bounds.append(f"max_len={f.max_length}")
    elif name in ("Int", "Float"):
        if f.min_length is not None or f.max_length is not None:
            return None
        if (f.gt is not None or f.lt is not None) and name == "Float":
            return None
        ge = f.ge if f.ge is not None else (f.gt + 1 if f.gt is not None else None)
        le = f.le if f.le is not None else (f.lt - 1 if f.lt is not None else None)
        if ge is not None:
            bounds.append(f"ge={ge!r}")
        if le is not None:
            bounds.append(f"le={le!r}")
    elif any(v is not None for v in (f.min_length, f.max_length, f.ge, f.gt, f.le, f.lt)):
        return None
    return f"{name}({', '.join(bounds)})" if bounds else name


def body_fields(src: RouteSource, var: str) -> tuple[FieldSpec, ...] | None:
    """The fields of the request body model a body parameter holds."""
    p = src.param(var)
    if p is None or p.source != "body":
        return None
    model = src.data_model(p.type)
    return model.fields if model is not None else None


__all__ = [
    "ERROR_KINDS",
    "LiftFragment",
    "Modifier",
    "Param",
    "RouteSource",
    "Step",
]
