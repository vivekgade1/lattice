"""The edited app as pycheck sees it: the importer's ``RepoModel`` plus the ASTs.

:class:`AppView` wraps :func:`lattice.importer.extract.extract` (tables, response and
body models, dependencies, routes with their tables, raise sites and owner evidence --
``Route.owner_uses``, followed into the helpers and repository methods a route hands
the caller) and the extractor's package index (the parsed modules and name
resolution), and adds the small AST utilities the checks share: a route's function and
caller parameters, table resolution, calls into package helpers and methods, and the
lines a constant-false ``if`` makes dead.

Nothing in the app is imported or run.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from lattice.importer.extract import (
    DataModel,
    Dependency,
    RepoModel,
    Route,
    TableModel,
    _Index,
    extract,
    route_function,
)

USER_KINDS = frozenset({"current_user", "active_user", "admin"})
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class Callee:
    module: str
    fn: FunctionNode
    cls: str | None  # "module:Class" for a method (its first parameter is self)


def package_root(app_dir: Path, package: str) -> Path:
    """The directory ``package`` (dotted) is importable from, given its own directory."""
    parts = package.split(".")
    app_dir = Path(app_dir).resolve()
    if tuple(app_dir.parts[-len(parts) :]) != tuple(parts):
        raise ValueError(f"{app_dir} is not the directory of the package {package!r}")
    return app_dir.parents[len(parts) - 1]


class AppView:
    def __init__(self, app_dir: Path, package: str) -> None:
        self.app_dir = Path(app_dir).resolve()
        self.package = package
        self.root = package_root(self.app_dir, package)
        self.model: RepoModel = extract(self.root, package)
        self.index = _Index(self.root, package)
        self.tables: dict[str, TableModel] = {t.name: t for t in self.model.tables}
        self._table_quals = {f"{t.module}:{t.name}": t.name for t in self.model.tables}
        self.models: dict[str, DataModel] = {m.name: m for m in self.model.models}
        self.deps: dict[str, Dependency] = {d.name: d for d in self.model.dependencies}
        self.classes: dict[str, tuple[str, ast.ClassDef]] = {}
        for mod, m in self.index.modules.items():
            for stmt in m.tree.body:
                if isinstance(stmt, ast.ClassDef):
                    self.classes[f"{mod}:{stmt.name}"] = (mod, stmt)
        found = sorted(
            {d.returns for d in self.model.dependencies if d.kind == "current_user" and d.returns}
        )
        self.users_table: str | None = found[0] if len(found) == 1 else None

    # -- files and functions --

    def file(self, module: str) -> str:
        """The module's file, relative to the app package directory."""
        rel = module[len(self.package) + 1 :].replace(".", "/") if module != self.package else ""
        if not rel:
            return "__init__.py"
        if (self.app_dir / rel).is_dir():
            return f"{rel}/__init__.py"
        return f"{rel}.py"

    def route_fn(self, route: Route) -> FunctionNode | None:
        return route_function(self.index, route)

    def functions(self) -> Iterator[tuple[str, FunctionNode, str | None]]:
        """Every function of the package: (module, node, "module:Class" for a method)."""
        for mod, m in self.index.modules.items():
            for node in ast.walk(m.tree):
                if isinstance(node, ast.ClassDef):
                    for stmt in node.body:
                        if isinstance(stmt, FunctionNode):
                            yield mod, stmt, f"{mod}:{node.name}"
            for stmt in m.tree.body:
                if isinstance(stmt, FunctionNode):
                    yield mod, stmt, None
                    for inner in ast.walk(stmt):
                        if inner is not stmt and isinstance(inner, FunctionNode):
                            yield mod, inner, None

    # -- names --

    def resolve(self, mod: str, expr: ast.expr | None) -> str | None:
        return self.index.resolve(mod, expr)

    def table_of(self, mod: str, expr: ast.expr | None) -> str | None:
        """The table class ``expr`` names (``Item``, ``models.Item``), or None."""
        if not isinstance(expr, ast.Name | ast.Attribute):
            return None
        return self._table_quals.get(self.resolve(mod, expr) or "")

    def callers(self, route: Route) -> set[str]:
        """The route's parameters holding the authenticated caller."""
        return {
            p.name
            for p in route.params
            if p.source == "dependency"
            and p.dependency in self.deps
            and self.deps[p.dependency].kind in USER_KINDS
        }

    def local_classes(self, mod: str, fn: FunctionNode, cls: str | None = None) -> dict[str, str]:
        """Names in ``fn`` bound to an instance of a package class: annotated parameters
        (``crud: NoteCRUD = Depends(...)``), ``x = Class(...)`` and ``self``."""
        out: dict[str, str] = {}
        args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
        if cls is not None and args:
            out[args[0].arg] = cls
        for a in args:
            qual = self.resolve(mod, _ann_head(a.annotation))
            if qual in self.classes:
                out[a.arg] = qual
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and (qual := self.resolve(mod, node.value.func)) in self.classes
            ):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = qual  # type: ignore[assignment]
        return out

    def callee(self, mod: str, call: ast.Call, local: dict[str, str]) -> Callee | None:
        """The package function or method ``call`` runs, when it can be told statically."""
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in local
        ):
            cls = local[func.value.id]
            cmod, node = self.classes[cls]
            for stmt in node.body:
                if isinstance(stmt, FunctionNode) and stmt.name == func.attr:
                    return Callee(cmod, stmt, cls)
            return None
        found = self.index.node(self.resolve(mod, func))
        if found and isinstance(found[1], FunctionNode):
            return Callee(found[0], found[1], None)
        return None


def bind_args(call: ast.Call, callee: Callee) -> dict[str, ast.expr]:
    """The callee's parameter names bound to the call's argument expressions."""
    fn = callee.fn
    positional = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    if callee.cls is not None:
        positional = positional[1:]
    names = set(positional) | {a.arg for a in fn.args.kwonlyargs}
    out: dict[str, ast.expr] = {}
    for i, arg in enumerate(call.args):
        if isinstance(arg, ast.Starred):
            break
        if i < len(positional):
            out[positional[i]] = arg
    for kw in call.keywords:
        if kw.arg in names:
            out[kw.arg] = kw.value
    return out


def dead_lines(fn: ast.AST) -> set[int]:
    """The lines of ``fn`` inside the body of ``if <constant false>`` (or the ``else`` of
    ``if <constant true>``): code that never runs."""
    out: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            dead = node.body if not node.test.value else node.orelse
            for stmt in dead:
                out |= set(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
    return out


def raises_http(stmts: list[ast.stmt]) -> ast.Raise | None:
    """The first ``raise`` among ``stmts`` (not descending into nested functions)."""
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise):
                return node
    return None


def http_error(mod: str, view: AppView, node: ast.Raise | None) -> tuple[int | None, str | None]:
    """(status, detail) of ``raise HTTPException(...)``; (None, None) otherwise."""
    if node is None or not isinstance(node.exc, ast.Call):
        return None, None
    call = node.exc
    if not (view.resolve(mod, call.func) or "").endswith("HTTPException"):
        return None, None
    kws = {k.arg: k.value for k in call.keywords if k.arg}
    status_expr = kws.get("status_code", call.args[0] if call.args else None)
    detail_expr = kws.get("detail", call.args[1] if len(call.args) > 1 else None)
    return _status(status_expr), (
        detail_expr.value
        if isinstance(detail_expr, ast.Constant) and isinstance(detail_expr.value, str)
        else None
    )


def _status(expr: ast.expr | None) -> int | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, int):
        return expr.value
    if isinstance(expr, ast.Attribute):  # status.HTTP_404_NOT_FOUND
        parts = expr.attr.split("_")
        if len(parts) > 1 and parts[0] == "HTTP" and parts[1].isdigit():
            return int(parts[1])
    return None


def _ann_head(ann: ast.expr | None) -> ast.expr | None:
    """``Annotated[X, ...]`` / ``X | None`` -> X (as far as a class reference goes)."""
    if isinstance(ann, ast.Subscript) and isinstance(ann.slice, ast.Tuple) and ann.slice.elts:
        return ann.slice.elts[0]
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return ann.left
    return ann


def route_key(route: Route) -> str:
    return f"{route.method} {route.path}"


__all__ = [
    "AppView",
    "Callee",
    "bind_args",
    "dead_lines",
    "http_error",
    "package_root",
    "raises_http",
    "route_key",
]
