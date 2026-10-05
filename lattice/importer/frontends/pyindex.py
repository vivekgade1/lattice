"""The shared Python package index of the importer's front ends (Phase 2 plan §2.2),
moved unchanged from ``extract.py``: every module of an app package parsed once
(:class:`_Index`: its imports, top-level definitions and name resolution), the
annotation reading :class:`_Type`, and the literal / AST helpers every Python front end
uses. Nothing here is specific to a web framework or an ORM."""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
from pathlib import Path

SCALAR_TYPES = {
    "builtins:str": "Str",
    "builtins:int": "Int",
    "builtins:float": "Float",
    "builtins:bool": "Bool",
    "uuid:UUID": "Uuid",
    "datetime:datetime": "DateTime",
    "datetime:date": "Date",
}
SCALARS = frozenset({"Str", "Email", "Int", "Float", "Bool", "Date", "DateTime", "Uuid"})
BUILTIN_NAMES = frozenset(dir(builtins))


# ---- the package index ----------------------------------------------------------------------


@dataclass
class _Module:
    name: str
    tree: ast.Module
    imports: dict[str, str]  # local name -> "module" or "module:attr"
    defs: dict[str, ast.AST]  # top-level name -> ClassDef / FunctionDef / Assign value


class _Index:
    def __init__(self, repo_root: Path, package: str) -> None:
        self.package = package
        self.modules: dict[str, _Module] = {}
        pkg_dir = repo_root.joinpath(*package.split("."))
        if not pkg_dir.is_dir():
            raise FileNotFoundError(f"app package {package!r} not found under {repo_root}")
        paths = sorted(pkg_dir.rglob("*.py"))
        names = []
        for path in paths:
            parts = path.relative_to(repo_root).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            names.append(".".join(parts))
        known = set(names)
        for path, name in zip(paths, names, strict=True):
            tree = ast.parse(path.read_text(), filename=str(path))
            is_pkg = path.name == "__init__.py"
            self.modules[name] = _Module(
                name, tree, _imports(tree, name, is_pkg, known), _defs(tree)
            )

    # -- resolution --

    def resolve(self, mod: str, expr: ast.expr | None) -> str | None:
        """The qualified name an expression denotes: "module" or "module:Name[.attr]"."""
        if isinstance(expr, ast.Name):
            m = self.modules.get(mod)
            if m is None:
                return None
            if expr.id in m.defs:
                return f"{mod}:{expr.id}"
            if expr.id in m.imports:
                return self.chase(m.imports[expr.id])
            return f"builtins:{expr.id}"
        if isinstance(expr, ast.Attribute):
            base = self.resolve(mod, expr.value)
            if base is None or base.startswith("builtins:"):
                return None
            if ":" not in base:
                return self.chase(f"{base}:{expr.attr}")
            return f"{base}.{expr.attr}"
        return None

    def chase(self, qual: str, depth: int = 0) -> str:
        """Follow re-exports: "m:N" where m imports N from elsewhere."""
        if ":" not in qual or depth > 10:
            return qual
        mod, name = qual.split(":", 1)
        if "." in name:
            return qual
        m = self.modules.get(mod)
        if m is None:
            return qual
        if name not in m.defs and name in m.imports:
            return self.chase(m.imports[name], depth + 1)
        if name not in m.defs and f"{mod}.{name}" in self.modules:
            return f"{mod}.{name}"
        return qual

    def node(self, qual: str | None) -> tuple[str, ast.AST] | None:
        if qual is None or ":" not in qual:
            return None
        mod, name = qual.split(":", 1)
        m = self.modules.get(mod)
        if m is None or name not in m.defs:
            return None
        return mod, m.defs[name]

    def import_line(self, mod: str, name: str) -> str | None:
        """An import statement binding ``name`` as module ``mod`` binds it."""
        m = self.modules.get(mod)
        if m is None:
            return None
        if name in m.defs:
            return f"from {mod} import {name}"
        qual = m.imports.get(name)
        if qual is None:
            return None
        return _render_import(qual, name)


def _render_import(qual: str, local: str) -> str:
    alias = ""
    if ":" in qual:
        module, attr = qual.split(":", 1)
        if attr != local:
            alias = f" as {local}"
        return f"from {module} import {attr}{alias}"
    if "." in qual:
        parent, last = qual.rsplit(".", 1)
        if last != local:
            alias = f" as {local}"
        return f"from {parent} import {last}{alias}"
    if qual != local:
        alias = f" as {local}"
    return f"import {qual}{alias}"


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


def _defs(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            out[stmt.name] = stmt
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.value is not None:
                out[stmt.target.id] = stmt.value
    return out


def _short(qual: str | None) -> str:
    if qual is None:
        return ""
    return qual.rsplit(":", 1)[-1].rsplit(".", 1)[-1]


def _ends(qual: str | None, *names: str) -> bool:
    return qual is not None and any(qual.endswith(f":{n}") for n in names)


# ---- types -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Type:
    type: str | None
    item: str | None = None
    nullable: bool = False
    choices: tuple[str | int, ...] = ()
    names: tuple[str, ...] = ()  # an Enum's member names, in ``choices``' order


# ---- literal and AST helpers ---------------------------------------------------------------


def _is_doc(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)


def _choices_type(values: list[object]) -> _Type:
    """Allowed values: all text -> Str, all integers -> Int, otherwise unreadable."""
    if values and all(isinstance(v, str) for v in values):
        return _Type("Str", choices=tuple(values))  # type: ignore[arg-type]
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return _Type("Int", choices=tuple(values))  # type: ignore[arg-type]
    return _Type(None)


def _lit(expr: ast.expr | None) -> object:
    if expr is None:
        return None
    try:
        return ast.literal_eval(expr)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _num(expr: ast.expr | None) -> int | float | None:
    value = _lit(expr)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _flatten_union(expr: ast.expr) -> list[ast.expr]:
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return [*_flatten_union(expr.left), *_flatten_union(expr.right)]
    return [expr]


def _ann_head(ann: ast.expr | None) -> ast.expr | None:
    return ann.value if isinstance(ann, ast.Subscript) else ann


def _args_with_defaults(args: ast.arguments) -> list[tuple[ast.arg, ast.expr | None]]:
    positional = [*args.posonlyargs, *args.args]
    pad: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    out = list(zip(positional, [*pad, *args.defaults], strict=True))
    out += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
    return out


def _arg_at(call: ast.Call, name: str, pos: int | None, kw_only: bool) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    if not kw_only and pos is not None and pos < len(call.args):
        arg = call.args[pos]
        return None if isinstance(arg, ast.Starred) else arg
    return None


def _single_assignment(fn: ast.AST, name: str) -> ast.expr | None:
    found = [
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    return found[0] if len(found) == 1 else None


def _local_names(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
        out |= {a.arg for a, _ in _args_with_defaults(fn.args)}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
    return out
