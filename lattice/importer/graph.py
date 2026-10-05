"""S2 of the importer's evidence sources (spec 2026-09-27 importer §3.6): a repo-wide
symbol and call graph, and per-route **dependency slices**.

:func:`build_graph` indexes every module of an application package through a
:class:`GraphBackend`; the one backend today is :class:`PythonAstBackend` (Python's
``ast``). A tree-sitter backend for other languages only has to produce the same
:class:`CodeGraph`.

The graph's nodes are symbols named ``"module:Name"`` (``"module:Class.method"`` for a
method). External names keep the module they were imported from (``"jwt:decode"``,
``"sqlmodel:Session.exec"``). Edges (:class:`Edge`) are:

* ``call``: a call of a package function or method, or of a ``@property`` read;
* ``depends``: a FastAPI ``Depends(x)`` (a parameter default, an ``Annotated[...]``
  alias, or a decorator's ``dependencies=[...]``); ``Depends()`` depends on the
  annotated class;
* ``model``: a reference to an ORM table class (``table=True``);
* ``type``: a reference to any other package class;
* ``ref``: a module variable read, or a package function passed as a value (a callback,
  kept in slices because it may be called);
* ``external``: a call of something outside the package.

Per function the graph also keeps its **direct effects** (a heuristic: ORM session
writes, a ``delete``/``insert``/``update`` statement executed, network/mail/subprocess
modules, ``open``/``print``, mutation of module state) and its **dynamic-dispatch
flags**: ``getattr``/``setattr`` with a computed name, ``eval``/``exec``, dynamic
imports, ``app.dependency_overrides``, and calls through a local variable or parameter
(or a subscript) that cannot be resolved. ``app.dependency_overrides[a] = b`` is also
recorded in :attr:`CodeGraph.overrides`, and a slice reaching ``a`` is flagged.

:func:`route_slice` walks the graph from a route; :func:`slice_source` renders the
slice's source in dependency order (models, variables, then functions callees first)
for an LLM prompt.

What is a route, a table, a ``depends`` edge, an override and a SQL write comes from the
front end as :class:`GraphHints` (``build_graph(..., hints=)``); the default,
:data:`FASTAPI_HINTS`, is FastAPI + SQLModel's: a decorator ``@x.get(...)`` (any HTTP
method or ``api_route``) is a route, a ``table=True`` / ``__tablename__`` class a table,
``Depends`` / ``Security`` a dependency, ``app.dependency_overrides`` an override, and
``sqlmodel`` / ``sqlalchemy`` the SQL modules.
"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from lattice.importer.appmodel import Route

__all__ = [
    "CodeGraph",
    "DynamicFlag",
    "Edge",
    "GraphBackend",
    "PythonAstBackend",
    "Slice",
    "Symbol",
    "build_graph",
    "route_slice",
    "slice_source",
]

EDGE_KINDS = ("call", "depends", "model", "type", "ref", "external")
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "api_route"})
SESSION_WRITES = frozenset(
    {
        "add",
        "add_all",
        "delete",
        "commit",
        "flush",
        "merge",
        "bulk_save_objects",
        "bulk_insert_mappings",
        "bulk_update_mappings",
    }
)
SESSION_NAMES = frozenset({"session", "db", "sess", "db_session"})
WRITE_STATEMENTS = frozenset({"delete", "insert", "update"})
SQL_MODULES = ("sqlmodel", "sqlalchemy")
EFFECT_MODULES = (
    "smtplib",
    "aiosmtplib",
    "emails",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "socket",
    "subprocess",
    "boto3",
    "shutil",
)
EFFECT_BUILTINS = frozenset({"open", "print", "input"})
MUTATORS = frozenset(
    {"append", "extend", "add", "update", "insert", "pop", "remove", "clear", "setdefault",
     "discard", "popitem"}
)  # fmt: skip
PROPERTY_DECORATORS = frozenset({"property", "cached_property", "computed_field"})
DYNAMIC_IMPORTS = frozenset({"importlib:import_module", "builtins:__import__"})
BUILTIN_NAMES = frozenset(dir(builtins))


# ---- the graph -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    qual: str
    kind: str  # module | function | method | class | variable
    module: str
    name: str  # the qualified name inside the module ("f", "Cls", "Cls.m")
    line: int = 0
    end_line: int = 0
    is_route: bool = False
    is_table: bool = False
    is_property: bool = False


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: str  # one of EDGE_KINDS
    line: int = 0


@dataclass(frozen=True)
class DynamicFlag:
    function: str
    kind: str  # getattr | eval | dynamic_import | dependency_overrides | unresolved_call
    line: int
    detail: str


@dataclass
class CodeGraph:
    package: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    edges: dict[str, list[Edge]] = field(default_factory=dict)  # by source symbol
    effects: dict[str, tuple[str, ...]] = field(default_factory=dict)  # direct effects
    dynamic: dict[str, list[DynamicFlag]] = field(default_factory=dict)
    overrides: dict[str, tuple[str, ...]] = field(default_factory=dict)  # dep -> overrides
    sources: dict[str, str] = field(default_factory=dict)  # module -> source text

    def out(self, qual: str, *kinds: str) -> list[Edge]:
        return [e for e in self.edges.get(qual, ()) if not kinds or e.kind in kinds]

    def callers(self, qual: str) -> list[str]:
        return sorted({e.src for es in self.edges.values() for e in es if e.dst == qual})

    def routes(self) -> list[str]:
        return [q for q, s in self.symbols.items() if s.is_route]

    def add_edge(self, edge: Edge) -> None:
        bucket = self.edges.setdefault(edge.src, [])
        if all((e.dst, e.kind) != (edge.dst, edge.kind) for e in bucket):
            bucket.append(edge)


@runtime_checkable
class GraphBackend(Protocol):
    """Builds the :class:`CodeGraph` of ``package`` (dotted, under ``repo_root``)."""

    def build(self, repo_root: Path, package: str) -> CodeGraph: ...


def build_graph(
    repo_root: Path | str,
    package: str,
    backend: GraphBackend | None = None,
    *,
    hints: GraphHints | None = None,
) -> CodeGraph:
    """The graph of ``package``; ``hints``: the front end's (default FastAPI's), for the
    default backend."""
    return (backend or PythonAstBackend(hints)).build(Path(repo_root), package)


# ---- the Python ast backend ------------------------------------------------------------------


@dataclass
class _Module:
    name: str
    tree: ast.Module
    imports: dict[str, str]  # local name -> "module" or "module:attr"
    defs: dict[str, ast.AST]
    classes: dict[str, ast.ClassDef]
    source: str


class PythonAstBackend:
    """The ``ast`` backend: static resolution of imports, attributes and simple local
    types (parameter annotations, ``x = Cls(...)``, ``x = f(...)`` with a return
    annotation, ``with Cls(...) as x``). ``hints``: the front end's (default FastAPI's)."""

    def __init__(self, hints: GraphHints | None = None) -> None:
        self.hints = hints if hints is not None else FASTAPI_HINTS

    def build(self, repo_root: Path, package: str) -> CodeGraph:
        return _Builder(repo_root, package, self.hints).build()


class _Builder:
    def __init__(self, repo_root: Path, package: str, hints: GraphHints | None = None) -> None:
        self.hints = hints if hints is not None else FASTAPI_HINTS
        self.package = package
        self.modules: dict[str, _Module] = {}
        pkg_dir = repo_root.joinpath(*package.split("."))
        if not pkg_dir.is_dir():
            raise FileNotFoundError(f"package {package!r} not found under {repo_root}")
        paths = sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)
        names = []
        for path in paths:
            parts = path.relative_to(repo_root).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            names.append(".".join(parts))
        known = set(names)
        for path, name in zip(paths, names, strict=True):
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            is_pkg = path.name == "__init__.py"
            defs, classes = _defs(tree)
            self.modules[name] = _Module(
                name, tree, _imports(tree, name, is_pkg, known), defs, classes, source
            )
        self.g = CodeGraph(package=package)
        self.var_types: dict[str, str] = {}  # module variable -> type qual

    # -- resolution --

    def chase(self, qual: str, depth: int = 0) -> str:
        """Follow re-exports: ``m:N`` where module m only imports N."""
        if ":" not in qual or depth > 10:
            return qual
        mod, name = qual.split(":", 1)
        head, _, rest = name.partition(".")
        m = self.modules.get(mod)
        if m is None or head in m.defs:
            return qual
        if head in m.imports:
            target = self.chase(m.imports[head], depth + 1)
            if not rest:
                return target
            return self.member(target, rest)
        if f"{mod}.{head}" in self.modules:
            sub = f"{mod}.{head}"
            return self.member(sub, rest) if rest else sub
        return qual

    def member(self, base: str, attr: str) -> str:
        """``base.attr``: a module attribute, a class member or a variable's member."""
        if ":" not in base:  # a module
            if f"{base}.{attr}" in self.modules:
                return f"{base}.{attr}"
            return self.chase(f"{base}:{attr}")
        return f"{base}.{attr}"

    def resolve(self, mod: str, expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Name):
            m = self.modules[mod]
            if expr.id in m.defs:
                return f"{mod}:{expr.id}"
            if expr.id in m.imports:
                return self.chase(m.imports[expr.id])
            if expr.id in BUILTIN_NAMES:
                return f"builtins:{expr.id}"
            return None
        if isinstance(expr, ast.Attribute):
            base = self.resolve(mod, expr.value)
            if base is None or base.startswith("builtins:"):
                return None
            return self.member(base, expr.attr)
        return None

    def in_package(self, qual: str) -> bool:
        return qual.split(":", 1)[0] in self.modules

    def class_node(self, qual: str) -> ast.ClassDef | None:
        if ":" not in qual:
            return None
        mod, name = qual.split(":", 1)
        m = self.modules.get(mod)
        return m.classes.get(name) if m else None

    def find_method(self, cls_qual: str, attr: str, depth: int = 0) -> str | None:
        """The package method ``attr`` of class ``cls_qual`` or of a package base."""
        node = self.class_node(cls_qual)
        if node is None or depth > 10:
            return None
        if f"{cls_qual}.{attr}" in self.g.symbols:
            return f"{cls_qual}.{attr}"
        mod = cls_qual.split(":", 1)[0]
        for base in node.bases:
            b = self.resolve(mod, base)
            if b and self.in_package(b):
                found = self.find_method(b, attr, depth + 1)
                if found:
                    return found
        return None

    def type_of_annotation(self, mod: str, ann: ast.expr | None, depth: int = 0) -> str | None:
        """The class an annotation denotes (``Annotated[T, ...]`` -> T, ``T | None`` -> T,
        a module alias ``X = Annotated[...]`` followed)."""
        if ann is None or depth > 5:
            return None
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            for side in (ann.left, ann.right):
                if not (isinstance(side, ast.Constant) and side.value is None):
                    return self.type_of_annotation(mod, side, depth + 1)
            return None
        if isinstance(ann, ast.Subscript):
            head = self.resolve(mod, ann.value)
            args = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            if head and head.rsplit(":", 1)[-1] in ("Annotated", "Optional") and args:
                return self.type_of_annotation(mod, args[0], depth + 1)
            return head
        q = self.resolve(mod, ann)
        if q is None:
            return None
        if self.in_package(q) and ":" in q:
            m, name = q.split(":", 1)
            value = self.modules[m].defs.get(name)
            if isinstance(value, ast.expr) and not isinstance(value, ast.Call):
                return self.type_of_annotation(m, value, depth + 1)  # a type alias
        return q

    # -- indexing --

    def build(self) -> CodeGraph:
        for mod, m in self.modules.items():
            self.g.sources[mod] = m.source
            self.g.symbols[mod] = Symbol(mod, "module", mod, mod, 1, len(m.source.splitlines()))
            for name, node in m.defs.items():
                qual = f"{mod}:{name}"
                if isinstance(node, ast.ClassDef):
                    self.g.symbols[qual] = Symbol(
                        qual, "class", mod, name, _first_line(node), node.end_lineno or 0,
                        is_table=self.hints.is_table(node),
                    )  # fmt: skip
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                            mq = f"{qual}.{item.name}"
                            self.g.symbols[mq] = Symbol(
                                mq, "method", mod, f"{name}.{item.name}", _first_line(item),
                                item.end_lineno or 0, is_property=_is_property(item),
                            )  # fmt: skip
                elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    self.g.symbols[qual] = Symbol(
                        qual, "function", mod, name, _first_line(node), node.end_lineno or 0,
                        is_route=self.hints.is_route(node),
                    )  # fmt: skip
        for mod, m in self.modules.items():
            for stmt in m.tree.body:
                if isinstance(stmt, ast.Assign | ast.AnnAssign):
                    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                    for t in targets:
                        if isinstance(t, ast.Name) and stmt.value is not None:
                            qual = f"{mod}:{t.id}"
                            if qual not in self.g.symbols:
                                self.g.symbols[qual] = Symbol(
                                    qual, "variable", mod, t.id, stmt.lineno,
                                    stmt.end_lineno or stmt.lineno,
                                )  # fmt: skip
                            vt = self.value_type(mod, stmt.value, {})
                            if vt:
                                self.var_types[qual] = vt
        for mod, m in self.modules.items():
            _Analyzer(self, mod, mod, None, m.tree.body, top_level=True).run()
            for name, node in m.defs.items():
                qual = f"{mod}:{name}"
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    _Analyzer(self, mod, qual, None, [node]).run()
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                            _Analyzer(self, mod, f"{qual}.{item.name}", qual, [item]).run()
        return self.g

    def value_type(self, mod: str, value: ast.expr, local: dict[str, str]) -> str | None:
        """The class of a value: a constructor call, a call of an annotated package
        function, a typed local or module variable."""
        if isinstance(value, ast.Name) and value.id in local:
            return local[value.id]
        if isinstance(value, ast.Call):
            q = self.resolve(mod, value.func)
            if q is None:
                return None
            if self.class_node(q) is not None or (not self.in_package(q) and _looks_class(q)):
                return q
            fn = self._function_node(q)
            if fn is not None:
                return self.type_of_annotation(q.split(":", 1)[0], fn.returns)
            return None
        q = self.resolve(mod, value)
        if q is not None:
            return self.var_types.get(q)
        return None

    def _function_node(self, qual: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        if ":" not in qual:
            return None
        mod, name = qual.split(":", 1)
        m = self.modules.get(mod)
        if m is None:
            return None
        head, _, rest = name.partition(".")
        node = m.defs.get(head)
        if rest and isinstance(node, ast.ClassDef):
            node = next((i for i in node.body if getattr(i, "name", None) == rest), None)
        return node if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else None


class _Analyzer:
    """Edges, effects and dynamic flags of one function (or a module's top level)."""

    def __init__(
        self,
        b: _Builder,
        mod: str,
        qual: str,
        cls: str | None,
        body: list[ast.stmt],
        *,
        top_level: bool = False,
    ) -> None:
        self.b = b
        self.g = b.g
        self.hints = b.hints
        self.mod = mod
        self.qual = qual
        self.cls = cls
        self.body = body
        self.top_level = top_level
        self.local: dict[str, str] = {}  # local name -> type qual
        self.locals: set[str] = set()  # every local binding (params, assignments, loops)
        self.effects: list[str] = []
        self.statements: dict[str, str] = {}  # local -> the write statement it holds

    # -- helpers --

    def edge(self, dst: str, kind: str, node: ast.AST) -> None:
        if dst == self.qual:
            return
        self.g.add_edge(Edge(self.qual, dst, kind, getattr(node, "lineno", 0)))

    def flag(self, kind: str, node: ast.AST, detail: str) -> None:
        f = DynamicFlag(self.qual, kind, getattr(node, "lineno", 0), detail)
        bucket = self.g.dynamic.setdefault(self.qual, [])
        if f not in bucket:
            bucket.append(f)

    def effect(self, reason: str) -> None:
        if reason not in self.effects:
            self.effects.append(reason)

    def target(self, q: str, node: ast.AST, *, called: bool) -> None:
        """An edge for a resolved name ``q``."""
        if q.startswith("builtins:"):
            return
        if not self.b.in_package(q):
            if called:
                self.edge(q, "external", node)
            return
        sym = self.g.symbols.get(q)
        if sym is None:
            # a member the package does not define (an inherited method, a field)
            mod, name = q.split(":", 1)
            head = f"{mod}:{name.split('.', 1)[0]}"
            owner = self.g.symbols.get(head)
            if owner is not None and owner.kind == "class":
                self.edge(head, "model" if owner.is_table else "type", node)
            elif owner is not None and owner.kind == "variable":
                self.edge(head, "ref", node)
            return
        if sym.kind == "class":
            self.edge(q, "model" if sym.is_table else "type", node)
            if called:
                init = self.b.find_method(q, "__init__")
                if init:
                    self.edge(init, "call", node)
        elif sym.kind in ("function", "method"):
            self.edge(q, "call" if called or sym.is_property else "ref", node)
        elif sym.kind == "variable":
            self.edge(q, "ref", node)

    # -- the walk --

    def run(self) -> None:
        if self.top_level:
            stmts = [
                s for s in self.body
                if not isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            ]  # fmt: skip
            for s in stmts:
                self.walk_module_stmt(s)
            self.finish()
            return
        fn = self.body[0]
        assert isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        self.signature(fn)
        for stmt in fn.body:
            self.walk(stmt)
        self.finish()

    def walk_module_stmt(self, stmt: ast.stmt) -> None:
        """Module level: a variable's value belongs to the variable's symbol."""
        if isinstance(stmt, ast.Assign | ast.AnnAssign) and stmt.value is not None:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names:
                for name in names:
                    sub = _Analyzer(self.b, self.mod, f"{self.mod}:{name}", None, [])
                    sub.expr(stmt.value)
                    sub.finish()
                return
        self.walk(stmt)

    def finish(self) -> None:
        if self.effects:
            self.g.effects[self.qual] = tuple(self.effects)

    def signature(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for dec in fn.decorator_list:
            self.decorator(dec)
        args = fn.args
        positional = [*args.posonlyargs, *args.args]
        defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
        pairs = [
            *zip(positional, [*defaults, *args.defaults], strict=True),
            *zip(args.kwonlyargs, args.kw_defaults, strict=True),
        ]
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                self.locals.add(extra.arg)
        static = any(getattr(d, "id", None) == "staticmethod" for d in fn.decorator_list)
        first = positional[0] if positional and self.cls is not None and not static else None
        for arg, default in pairs:
            self.locals.add(arg.arg)
            if arg is first and self.cls is not None:
                self.local[arg.arg] = self.cls  # self / cls
                continue
            t = self.b.type_of_annotation(self.mod, arg.annotation)
            if t:
                self.local[arg.arg] = t
            if arg.annotation is not None:
                self.annotation(arg.annotation)
            if default is not None:
                if self.hints.is_depends(default):
                    self.depends(default, arg.annotation)
                else:
                    self.expr(default)
        if fn.returns is not None:
            self.annotation(fn.returns)

    def decorator(self, dec: ast.expr) -> None:
        if isinstance(dec, ast.Call):
            for kw in dec.keywords:
                if kw.arg == "dependencies" and isinstance(kw.value, ast.List | ast.Tuple):
                    for d in kw.value.elts:
                        if self.hints.is_depends(d):
                            self.depends(d, None)
                else:
                    self.expr(kw.value)
            for a in dec.args:
                self.expr(a)
        else:
            self.expr(dec)

    def depends(self, call: ast.expr, annotation: ast.expr | None) -> None:
        assert isinstance(call, ast.Call)
        target = (
            call.args[0]
            if call.args
            else next((k.value for k in call.keywords if k.arg == "dependency"), None)
        )
        if target is None:
            t = self.b.type_of_annotation(self.mod, annotation) if annotation is not None else None
            if t:
                self.edge(t, "depends", call)
            return
        q = self.b.resolve(self.mod, target)
        if q is None:
            self.flag("unresolved_call", call, f"Depends({ast.unparse(target)})")
            return
        self.edge(q, "depends", call)

    def annotation(self, ann: ast.expr) -> None:
        for node in ast.walk(ann):
            if isinstance(node, ast.Call) and self.hints.is_depends(node):
                self.depends(node, _annotated_type(ann))
            elif isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load):
                q = self.b.resolve(self.mod, node)
                if q is not None:
                    self.target(q, node, called=False)

    def walk(self, stmt: ast.stmt) -> None:
        match stmt:
            case ast.Assign(targets=targets, value=value):
                self.expr(value)
                for t in targets:
                    if isinstance(t, ast.Subscript | ast.Attribute):
                        self.expr(t.value)
                        if isinstance(t, ast.Subscript):
                            self.expr(t.slice)
                    self.bind_target(t, value)
                    self.store(t, value)
            case ast.AnnAssign(target=target, value=value, annotation=ann):
                self.annotation(ann)
                if value is not None:
                    self.expr(value)
                self.bind_target(target, value)
                t = self.b.type_of_annotation(self.mod, ann)
                if isinstance(target, ast.Name) and t:
                    self.local[target.id] = t
                if value is not None:
                    self.store(target, value)
            case ast.AugAssign(target=target, value=value):
                self.expr(value)
                self.store(target, value)
            case ast.With(items=items) | ast.AsyncWith(items=items):
                for item in items:
                    self.expr(item.context_expr)
                    if isinstance(item.optional_vars, ast.Name):
                        name = item.optional_vars.id
                        self.locals.add(name)
                        t = self.b.value_type(self.mod, item.context_expr, self.local)
                        if t:
                            self.local[name] = t
                for s in stmt.body:
                    self.walk(s)
            case ast.For(target=target, iter=it) | ast.AsyncFor(target=target, iter=it):
                self.expr(it)
                self.bind_names(target)
                for s in [*stmt.body, *stmt.orelse]:
                    self.walk(s)
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                self.locals.add(stmt.name)
                self.signature(stmt)
                for s in stmt.body:
                    self.walk(s)
            case ast.ClassDef():
                self.locals.add(stmt.name)
            case ast.Global(names=names):
                for n in names:
                    q = f"{self.mod}:{n}"
                    if q in self.g.symbols:
                        self.effect(f"rebinds module state {n}")
            case _:
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, ast.stmt):
                        self.walk(child)
                    elif isinstance(child, ast.expr):
                        self.expr(child)
                    elif isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            self.expr(child.type)
                        if child.name:
                            self.locals.add(child.name)
                        for s in child.body:
                            self.walk(s)
                    elif isinstance(child, ast.withitem | ast.match_case):
                        for sub in ast.iter_child_nodes(child):
                            if isinstance(sub, ast.stmt):
                                self.walk(sub)
                            elif isinstance(sub, ast.expr):
                                self.expr(sub)

    def bind_target(self, target: ast.expr, value: ast.expr | None) -> None:
        self.bind_names(target)
        if isinstance(target, ast.Name) and value is not None:
            t = self.b.value_type(self.mod, value, self.local)
            if t:
                self.local[target.id] = t
            stmt = self.write_statement(value)
            if stmt:
                self.statements[target.id] = stmt

    def bind_names(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.locals.add(target.id)
        elif isinstance(target, ast.Tuple | ast.List):
            for elt in target.elts:
                self.bind_names(elt)
        elif isinstance(target, ast.Starred):
            self.bind_names(target.value)

    def store(self, target: ast.expr, value: ast.expr) -> None:
        """A write into module state: ``NAME[k] = v``, ``NAME.attr = v``, and
        ``app.dependency_overrides[a] = b``."""
        if isinstance(target, ast.Subscript):
            base = target.value
            if isinstance(base, ast.Attribute) and base.attr == self.hints.overrides_attr:
                key = self.b.resolve(self.mod, target.slice)
                val = self.b.resolve(self.mod, value)
                if key is not None:
                    have = self.g.overrides.get(key, ())
                    shown = val or ast.unparse(value)
                    if shown not in have:
                        self.g.overrides[key] = (*have, shown)
                return
            self.module_mutation(base, "item assignment")
        elif isinstance(target, ast.Attribute):
            self.module_mutation(target.value, "attribute assignment")

    def module_mutation(self, base: ast.expr, how: str) -> None:
        if isinstance(base, ast.Name) and base.id not in self.locals:
            q = self.b.resolve(self.mod, base)
            if q and self.b.in_package(q) and self.g.symbols.get(q, None) is not None:
                if self.g.symbols[q].kind == "variable":
                    self.effect(f"mutates module state {base.id} ({how})")

    def write_statement(self, value: ast.expr) -> str | None:
        """``delete(Item)...`` (a SQL write statement, possibly with .where chained)."""
        node = value
        while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            node = node.func.value
        if isinstance(node, ast.Call):
            q = self.b.resolve(self.mod, node.func)
            if q and q.split(":", 1)[0].split(".")[0] in self.hints.sql_modules:
                short = q.rsplit(":", 1)[-1]
                if short in WRITE_STATEMENTS:
                    return short
        return None

    # -- expressions --

    def expr(self, e: ast.expr) -> None:
        for node in _walk_expr(e):
            if isinstance(node, ast.Call):
                self.call(node)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                self.attribute(node)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in self.locals:
                    continue
                q = self.b.resolve(self.mod, node)
                if q is not None:
                    self.target(q, node, called=False)
            elif isinstance(node, ast.Lambda | ast.comprehension):
                for n in ast.walk(node.args if isinstance(node, ast.Lambda) else node.target):
                    if isinstance(n, ast.Name | ast.arg):
                        self.locals.add(n.id if isinstance(n, ast.Name) else n.arg)

    def receiver_type(self, value: ast.expr) -> str | None:
        if isinstance(value, ast.Name):
            if value.id in self.local:
                return self.local[value.id]
            if value.id in self.locals:
                return None
            q = self.b.resolve(self.mod, value)
            return self.b.var_types.get(q) if q else None
        if isinstance(value, ast.Attribute):
            q = self.b.resolve(self.mod, value) if not _rooted_in(value, self.locals) else None
            return self.b.var_types.get(q) if q else None
        return None

    def attribute(self, node: ast.Attribute) -> None:
        if node.attr == self.hints.overrides_attr:
            self.flag("dependency_overrides", node, ast.unparse(node))
        if not _rooted_in(node, self.locals):
            q = self.b.resolve(self.mod, node)
            if q is not None:
                self.target(q, node, called=False)
        t = self.receiver_type(node.value)
        if t is not None and self.b.in_package(t):
            m = self.b.find_method(t, node.attr)
            if m and self.g.symbols[m].is_property:
                self.edge(m, "call", node)

    def call(self, node: ast.Call) -> None:
        func = node.func
        if self.hints.is_depends(node):
            self.depends(node, None)
            return
        if isinstance(func, ast.Name):
            if func.id in self.locals:
                self.flag("unresolved_call", node, f"call through variable {func.id!r}")
                return
            q = self.b.resolve(self.mod, func)
            if q is None:
                self.flag("unresolved_call", node, f"unresolved name {func.id!r}")
                return
            self.resolved_call(q, node)
            return
        if isinstance(func, ast.Attribute):
            self.method_call(func, node)
            return
        if isinstance(func, ast.Subscript):
            self.flag("unresolved_call", node, f"call through subscript {ast.unparse(func)}")
        # a call of a call's result, a lambda...: nothing static to resolve

    def resolved_call(self, q: str, node: ast.Call) -> None:
        if q in DYNAMIC_IMPORTS:
            self.flag("dynamic_import", node, ast.unparse(node.func))
        elif q in ("builtins:getattr", "builtins:setattr", "builtins:delattr"):
            name = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                self.flag("getattr", node, ast.unparse(node))
            if q != "builtins:getattr" and node.args:
                self.module_mutation(node.args[0], q.rsplit(":", 1)[1])
        elif q in ("builtins:eval", "builtins:exec", "builtins:compile"):
            self.flag("eval", node, ast.unparse(node.func))
        elif q.startswith("builtins:") and q.split(":", 1)[1] in EFFECT_BUILTINS:
            self.effect(f"calls {q.split(':', 1)[1]}()")
        if not q.startswith("builtins:") and not self.b.in_package(q):
            root = q.split(":", 1)[0].split(".")[0]
            if root in EFFECT_MODULES:
                self.effect(f"calls {q}")
        self.target(q, node, called=True)

    def method_call(self, func: ast.Attribute, node: ast.Call) -> None:
        recv = func.value
        attr = func.attr
        t = self.receiver_type(recv)
        name = _last_name(recv)
        if (t and t.rsplit(":", 1)[-1].rsplit(".", 1)[-1] in ("Session", "AsyncSession")) or (
            t is None and name in SESSION_NAMES
        ):
            if attr in SESSION_WRITES:
                self.effect(f"session.{attr}")
            elif attr in ("exec", "execute") and node.args:
                arg = node.args[0]
                stmt = self.statements.get(arg.id) if isinstance(arg, ast.Name) else None
                stmt = stmt or self.write_statement(arg)
                if stmt:
                    self.effect(f"session.{attr}({stmt}(...))")
        if isinstance(recv, ast.Name) and recv.id not in self.local and recv.id not in self.locals:
            if attr in MUTATORS:
                self.module_mutation(recv, f".{attr}()")
        if t is not None:
            if self.b.in_package(t):
                m = self.b.find_method(t, attr)
                if m:
                    self.edge(m, "call", node)
                else:
                    self.target(t, node, called=False)
            else:
                self.edge(f"{t}.{attr}", "external", node)
            return
        if _rooted_in(recv, self.locals):
            return  # a method of a local whose type is unknown
        q = self.b.resolve(self.mod, func)
        if q is not None:
            self.resolved_call(q, node)


# ---- slices ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Slice:
    """What a route depends on. ``functions`` are in dependency order (callees first,
    the route last); ``dependencies`` are the targets of ``Depends`` edges;
    ``effects`` (function, reason) are the effectful functions of the slice."""

    route: str
    functions: tuple[str, ...]
    dependencies: tuple[str, ...]
    models: tuple[str, ...]
    types: tuple[str, ...]
    variables: tuple[str, ...]
    effects: tuple[tuple[str, str], ...]
    dynamic: tuple[DynamicFlag, ...]
    external: tuple[str, ...]
    graph: CodeGraph = field(repr=False, compare=False, hash=False)


def _route_qual(graph: CodeGraph, route: Route | str) -> str:
    if isinstance(route, Route):
        return f"{route.module}:{route.name}"
    if ":" in route:
        if route not in graph.symbols:
            raise KeyError(f"no symbol {route!r} in the graph")
        return route
    found = [q for q in graph.routes() if q.rsplit(":", 1)[1] == route]
    if len(found) != 1:
        raise KeyError(f"route {route!r} matches {found or 'nothing'}")
    return found[0]


def route_slice(graph: CodeGraph, route: Route | str) -> Slice:
    """The transitive closure of a route: the functions it calls or depends on (through
    calls, ``Depends``, variables and callbacks), the models and classes touched, the
    effectful functions, the dynamic-dispatch flags (including a ``Depends`` target
    overridden through ``app.dependency_overrides``) and the external calls."""
    root = _route_qual(graph, route)
    order: list[str] = []
    seen: set[str] = set()
    deps: list[str] = []
    external: list[str] = []

    def visit(q: str) -> None:
        if q in seen:
            return
        seen.add(q)
        for e in graph.out(q):
            if e.kind == "external":
                if e.dst not in external:
                    external.append(e.dst)
                continue
            if e.kind == "depends" and e.dst not in deps:
                deps.append(e.dst)
            if e.dst in graph.symbols:
                sym = graph.symbols[e.dst]
                if sym.kind == "class":
                    if e.dst not in seen:
                        seen.add(e.dst)
                        order.append(e.dst)
                    continue
                visit(e.dst)
        order.append(q)

    visit(root)
    functions = tuple(q for q in order if graph.symbols[q].kind in ("function", "method"))
    classes = [graph.symbols[q] for q in order if graph.symbols[q].kind == "class"]
    variables = tuple(q for q in order if graph.symbols[q].kind == "variable")
    dynamic: list[DynamicFlag] = []
    for q in (*functions, *variables):
        dynamic += graph.dynamic.get(q, ())
    for d in deps:
        if d in graph.overrides:
            dynamic.append(
                DynamicFlag(d, "dependency_overrides", graph.symbols[d].line if d in
                            graph.symbols else 0,
                            f"{d} is overridden by {', '.join(graph.overrides[d])}")
            )  # fmt: skip
    effects = tuple((q, "; ".join(graph.effects[q])) for q in functions if q in graph.effects)
    return Slice(
        route=root,
        functions=functions,
        dependencies=tuple(deps),
        models=tuple(s.qual for s in classes if s.is_table),
        types=tuple(s.qual for s in classes if not s.is_table),
        variables=variables,
        effects=effects,
        dynamic=tuple(dynamic),
        external=tuple(sorted(external)),
        graph=graph,
    )


def slice_source(s: Slice) -> str:
    """The source of a slice for an LLM prompt: the models touched, the module variables
    used, then the functions callees first (the route last), each under a
    ``# <qualified name>`` header. Methods are shown as their own ``def``."""
    g = s.graph
    parts: list[str] = []
    for q in (*s.models, *s.variables, *s.functions):
        sym = g.symbols[q]
        lines = g.sources[sym.module].splitlines()
        text = "\n".join(lines[sym.line - 1 : sym.end_line])
        if sym.kind == "method":
            text = _dedent(text)
        parts.append(f"# {q}\n{text}")
    return "\n\n\n".join(parts) + "\n"


# ---- small helpers ---------------------------------------------------------------------------


def _dedent(text: str) -> str:
    import textwrap

    return textwrap.dedent(text)


def _first_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    lines = [getattr(node, "lineno", 1), *(d.lineno for d in decorators)]
    return min(lines)


def _is_table(node: ast.ClassDef) -> bool:
    return any(
        k.arg == "table" and isinstance(k.value, ast.Constant) and k.value.value is True
        for k in node.keywords
    ) or any(
        isinstance(s, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets)
        for s in node.body
    )


def _is_property(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for d in fn.decorator_list:
        name = d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", None)
        if isinstance(d, ast.Call):
            f = d.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name in PROPERTY_DECORATORS:
            return True
    return False


def _is_route(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for d in fn.decorator_list:
        if (
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in HTTP_METHODS
        ):
            return True
    return False


def _is_depends(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
    return name in ("Depends", "Security")


def _annotated_type(ann: ast.expr) -> ast.expr | None:
    if isinstance(ann, ast.Subscript) and isinstance(ann.slice, ast.Tuple) and ann.slice.elts:
        return ann.slice.elts[0]
    return None


def _looks_class(q: str) -> bool:
    last = q.rsplit(":", 1)[-1].rsplit(".", 1)[-1]
    return last[:1].isupper()


def _last_name(e: ast.expr) -> str | None:
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        return e.attr
    return None


def _rooted_in(e: ast.expr, names: set[str]) -> bool:
    while isinstance(e, ast.Attribute | ast.Subscript | ast.Call):
        e = e.func if isinstance(e, ast.Call) else e.value
    return isinstance(e, ast.Name) and e.id in names


def _walk_expr(e: ast.expr) -> Iterable[ast.AST]:
    """``ast.walk`` in source order, binding comprehension/lambda variables first."""
    todo: list[ast.AST] = [e]
    out: list[ast.AST] = []
    while todo:
        node = todo.pop(0)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            out += node.generators  # bind the targets before the element is visited
        out.append(node)
        todo[:0] = list(ast.iter_child_nodes(node))
    return out


def _defs(tree: ast.Module) -> tuple[dict[str, ast.AST], dict[str, ast.ClassDef]]:
    defs: dict[str, ast.AST] = {}
    classes: dict[str, ast.ClassDef] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            defs[stmt.name] = stmt
            classes[stmt.name] = stmt
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            defs[stmt.name] = stmt
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    defs[target.id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.value is not None:
                defs[stmt.target.id] = stmt.value
    return defs, classes


def _imports(tree: ast.Module, mod: str, is_pkg: bool, known: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.asname:
                    out[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    out[top] = top
        elif isinstance(stmt, ast.ImportFrom):
            base = stmt.module or ""
            if stmt.level:
                parts = mod.split(".")
                if not is_pkg:
                    parts = parts[:-1]
                parts = parts[: len(parts) - (stmt.level - 1)] if stmt.level > 1 else parts
                base = ".".join([*parts, *([base] if base else [])])
            for alias in stmt.names:
                local = alias.asname or alias.name
                sub = f"{base}.{alias.name}"
                out[local] = sub if sub in known else f"{base}:{alias.name}"
    return out


# ---- front-end hints -------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphHints:
    """What the graph needs from a front end: ``is_route`` (a function definition is a
    route), ``is_table`` (a class definition is an ORM table), ``is_depends`` (an
    expression is a dependency declaration: a ``depends`` edge to its target),
    ``overrides_attr`` (the attribute whose item assignment overrides a dependency;
    None: none) and ``sql_modules`` (the modules whose ``insert``/``update``/``delete``
    statements are SQL writes). The defaults are FastAPI + SQLModel's."""

    is_route: Callable[[ast.FunctionDef | ast.AsyncFunctionDef], bool] = _is_route
    is_table: Callable[[ast.ClassDef], bool] = _is_table
    is_depends: Callable[[ast.expr], bool] = _is_depends
    overrides_attr: str | None = "dependency_overrides"
    sql_modules: tuple[str, ...] = SQL_MODULES


FASTAPI_HINTS = GraphHints()
