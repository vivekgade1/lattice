"""T2 at module level: which statements are T2, allowlisted imports, @record
declarations, procedure signatures and declared effects, name clashes, module
constants, then each body (lattice.check.t2.body) and the call graph."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence

from lattice.catalog import (
    EFFECTS_KW,
    OPS_MODULE,
    PROCEDURE,
    RECORD,
    SCHEMAS_MODULE,
    T2_BUILTINS,
    T2_HELPERS,
    T2_OPS_NAMES,
    T2_STDLIB,
    T2_TYPE_ALIASES,
    T2_TYPE_NAMES,
)
from lattice.check.astutil import is_constant_expr, is_list_constant_assign, literal_value
from lattice.check.context import CheckContext
from lattice.check.t2 import types as T
from lattice.check.t2.body import BodyChecker, ModuleInfo
from lattice.check.t2.model import CheckedProcedure, ProcSig, RecordDef, T2Program
from lattice.check.t2.types import UNK, T2Type
from lattice.diagnostics import span_of
from lattice.profile import ApiProfile

_ALLOWED_MODULES = ", ".join(sorted(T2_STDLIB))
_IMPORT_HINT = f"procedures may import only: {_ALLOWED_MODULES} (see catalog.T2_STDLIB)"


def _deco_name(d: ast.expr) -> str | None:
    if isinstance(d, ast.Name):
        return d.id
    if isinstance(d, ast.Call) and isinstance(d.func, ast.Name):
        return d.func.id
    return None


def is_procedure(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and any(
        _deco_name(d) == PROCEDURE for d in stmt.decorator_list
    )


def is_record(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.ClassDef) and any(
        _deco_name(d) == RECORD for d in stmt.decorator_list
    )


def _is_foreign_import(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Import):
        return True
    return isinstance(stmt, ast.ImportFrom) and (
        stmt.level != 0 or stmt.module not in (OPS_MODULE, SCHEMAS_MODULE)
    )


def split(module: ast.Module, api: bool) -> tuple[list[ast.stmt], list[ast.stmt]]:
    """(T0 statements, T2 statements). T2: @procedure functions and @record classes;
    in API programs also every class definition (L128 there, with a @record hint)
    and -- once the program has a procedure or record -- every import that is not
    from lattice.ops / lattice.schemas (checked against the stdlib allowlist). A
    program without procedures keeps every other statement in T0, as before."""
    has_t2 = any(is_procedure(s) or is_record(s) for s in module.body)
    t0: list[ast.stmt] = []
    t2: list[ast.stmt] = []
    for s in module.body:
        if is_procedure(s) or is_record(s):
            t2.append(s)
        elif api and (isinstance(s, ast.ClassDef) or (has_t2 and _is_foreign_import(s))):
            t2.append(s)
        else:
            t0.append(s)
    return t0, t2


def run(stmts: Sequence[ast.stmt], t0: Sequence[ast.stmt], ctx: CheckContext) -> T2Program | None:
    """Check the T2 statements; diagnostics go to `ctx`. None for a non-API program."""
    api = ctx.profile.api
    if api is None:
        for s in stmts:
            ctx.error(
                "L148",
                s,
                "@procedure and @record are only available in API programs",
                "write the logic as @step pipelines",
            )
        return None
    return _Module(stmts, t0, ctx, api).run()


class _Module:
    def __init__(
        self, stmts: Sequence[ast.stmt], t0: Sequence[ast.stmt], ctx: CheckContext, api: ApiProfile
    ) -> None:
        self.stmts = stmts
        self.t0 = t0
        self.ctx = ctx
        self.api = api
        self.imports: dict[str, str] = {}
        self.records: dict[str, RecordDef] = {}
        self.sigs: dict[str, ProcSig] = {}
        self.fns: dict[str, ast.FunctionDef] = {}
        self.constants: dict[str, T2Type] = {}
        self.top: dict[str, ast.AST] = {}  # T2 top-level names -> defining node

    def run(self) -> T2Program:
        self._imports()
        record_nodes = self._record_nodes()
        env = T.TypeEnv(frozenset(self.api.tables), frozenset(record_nodes))
        for name, node in record_nodes.items():
            self.records[name] = self._record(node, env)
        self._signatures(env)
        self._clashes()
        info = ModuleInfo(self.api, self.sigs, self.records, self.imports, self.constants)
        self._constants(info)
        procedures: dict[str, CheckedProcedure] = {}
        uses: set[str] = set()
        for name, fn in self.fns.items():
            body = BodyChecker(info, self.ctx, fn, self.sigs[name])
            body.run()
            uses |= body.const_uses
            procedures[name] = CheckedProcedure(
                name=name,
                node=fn,
                sig=self.sigs[name],
                types=body.types,
                targets=body.targets,
                locals=dict(body.declared),
                calls=frozenset(body.calls),
                natives=frozenset(body.natives),
                is_recursive=False,
                span=span_of(fn),
            )
        self.ctx.external_uses |= uses
        recursive = _recursive({n: p.calls for n, p in procedures.items()})
        for n in recursive:
            p = procedures[n]
            procedures[n] = CheckedProcedure(
                p.name,
                p.node,
                p.sig,
                p.types,
                p.targets,
                p.locals,
                p.calls,
                p.natives,
                True,
                p.span,
            )
        return T2Program(
            procedures=procedures,
            records=dict(self.records),
            imports=dict(self.imports),
            constants=dict(self.constants),
            nodes=tuple(self.stmts),
        )

    # ---- imports ------------------------------------------------------------------

    def _imports(self) -> None:
        for s in self.stmts:
            match s:
                case ast.Import(names=names):
                    for alias in names:
                        if alias.name not in T2_STDLIB:
                            self.ctx.error(
                                "L142", s, f"'import {alias.name}' is not allowed", _IMPORT_HINT
                            )
                        elif alias.asname is not None:
                            self._alias_error(s, alias)
                        else:
                            self._bind_import(alias.name, alias.name, s)
                case ast.ImportFrom(module=mod, level=level, names=names):
                    if level != 0 or mod not in T2_STDLIB:
                        where = "." * level + (mod or "")
                        self.ctx.error(
                            "L142", s, f"imports from '{where}' are not allowed", _IMPORT_HINT
                        )
                        continue
                    for alias in names:
                        if alias.name == "*":
                            self.ctx.error(
                                "L142", s, "wildcard imports are not allowed", "list each name"
                            )
                        elif alias.name not in T2_STDLIB[mod]:
                            allowed = ", ".join(sorted(T2_STDLIB[mod]))
                            self.ctx.error(
                                "L142",
                                s,
                                f"'{mod}.{alias.name}' is not in the procedure allowlist",
                                f"allowed from {mod}: {allowed}",
                            )
                        elif alias.asname is not None:
                            self._alias_error(s, alias)
                        else:
                            self._bind_import(alias.name, f"{mod}.{alias.name}", s)

    def _alias_error(self, s: ast.stmt, alias: ast.alias) -> None:
        self.ctx.error(
            "L142",
            s,
            f"import alias '{alias.name} as {alias.asname}' is not allowed",
            f"import '{alias.name}' under its own name",
        )

    def _bind_import(self, name: str, qual: str, node: ast.AST) -> None:
        if self.imports.get(name, qual) != qual:
            self.ctx.error("L914", node, f"'{name}' is imported twice with different meanings")
            return
        self.imports[name] = qual
        self.top.setdefault(name, node)

    # ---- records --------------------------------------------------------------------

    def _record_nodes(self) -> dict[str, ast.ClassDef]:
        out: dict[str, ast.ClassDef] = {}
        for s in self.stmts:
            if not isinstance(s, ast.ClassDef):
                continue
            if not is_record(s):
                self.ctx.error(
                    "L128",
                    s,
                    "class definitions are not allowed",
                    "declare a value type with @record (annotated fields only), e.g. "
                    "'@record' above 'class Point:' with fields 'x: Int'",
                )
                continue
            if s.name in out or s.name in self.top:
                self._clash(s, s.name, "is defined twice")
                continue
            out[s.name] = s
            self.top[s.name] = s
        return out

    def _record(self, node: ast.ClassDef, env: T.TypeEnv) -> RecordDef:
        if len(node.decorator_list) != 1 or not isinstance(node.decorator_list[0], ast.Name):
            self.ctx.error(
                "L144",
                node,
                f"record '{node.name}' must have exactly one decorator, '@record'",
                "remove the other decorators",
            )
        if node.bases or node.keywords:
            self.ctx.error(
                "L144",
                node,
                f"record '{node.name}' cannot have base classes",
                "list every field in the record itself",
            )
        fields: list[tuple[str, T2Type]] = []
        defaults: dict[str, object] = {}
        for i, item in enumerate(node.body):
            match item:
                case ast.Expr(value=ast.Constant(value=str())) if i == 0:
                    continue
                case ast.AnnAssign(target=ast.Name(id=fname), annotation=ann, value=value):
                    try:
                        ft = T.parse_annotation(ann, env)
                    except T.AnnotationError as e:
                        self.ctx.error("L902", e.node, e.message, e.hint)
                        ft = UNK
                    if any(f == fname for f, _ in fields):
                        self.ctx.error("L144", item, f"field '{fname}' is declared twice")
                        continue
                    fields.append((fname, ft))
                    if value is not None:
                        self._default(node, fname, ft, value, defaults)
                    elif defaults:
                        self.ctx.error(
                            "L144",
                            item,
                            f"field '{fname}' without a default follows a field with one",
                            "put the fields with defaults last",
                        )
                case ast.FunctionDef() | ast.AsyncFunctionDef():
                    self.ctx.error(
                        "L144",
                        item,
                        f"record '{node.name}' cannot have methods",
                        f"write a @procedure that takes a {node.name}",
                    )
                case _:
                    self.ctx.error(
                        "L144",
                        item,
                        f"record '{node.name}' may contain only annotated fields",
                        "write 'name: Type' (optionally '= <literal>')",
                    )
        if not fields and not any(
            isinstance(i, ast.FunctionDef | ast.AnnAssign) for i in node.body
        ):
            self.ctx.error("L144", node, f"record '{node.name}' has no fields", "add 'name: Type'")
        return RecordDef(node.name, node, tuple(fields), defaults)

    def _default(
        self,
        rec: ast.ClassDef,
        fname: str,
        ft: T2Type,
        value: ast.expr,
        defaults: dict[str, object],
    ) -> None:
        if isinstance(value, ast.Constant) and value.value is None:
            lit: object = None
            lt: T2Type = T.NONE
        else:
            v = literal_value(value)
            if v is None:
                self.ctx.error(
                    "L144",
                    value,
                    f"the default of {rec.name}.{fname} must be a literal",
                    "use a number, string, True/False or None",
                )
                return
            lit = v
            lt = {bool: T.BOOL, int: T.INT, float: T.FLOAT, str: T.STR}[type(v)]
        if not T.assignable(lt, ft):
            self.ctx.error("L901", value, f"{rec.name}.{fname} is {ft}, its default is {lt}")
            return
        defaults[fname] = lit

    # ---- procedure signatures ------------------------------------------------------

    def _signatures(self, env: T.TypeEnv) -> None:
        for s in self.stmts:
            if isinstance(s, ast.AsyncFunctionDef) and is_procedure(s):
                self.ctx.error("L131", s, "async procedures are not allowed", "write a plain 'def'")
                continue
            if not (isinstance(s, ast.FunctionDef) and is_procedure(s)):
                continue
            effects = self._decorator(s)
            params = self._params(s, env)
            if s.returns is None:
                self.ctx.error(
                    "L148",
                    s,
                    f"procedure '{s.name}' needs a return annotation",
                    "add '-> Type' ('-> None' when it returns nothing)",
                )
                returns: T2Type = UNK
            else:
                returns = self._ann(s.returns, env)
            if s.name in self.top:
                self._clash(s, s.name, "is defined twice")
                continue
            self.top[s.name] = s
            self.sigs[s.name] = ProcSig(s.name, params, returns, effects)
            self.fns[s.name] = s

    def _ann(self, node: ast.expr, env: T.TypeEnv) -> T2Type:
        try:
            return T.parse_annotation(node, env)
        except T.AnnotationError as e:
            self.ctx.error("L902", e.node, e.message, e.hint)
            return UNK

    def _decorator(self, fn: ast.FunctionDef) -> frozenset[str]:
        hint = '@procedure, or @procedure(effects=["mail"])'
        if len(fn.decorator_list) != 1:
            self.ctx.error(
                "L148", fn, f"procedure '{fn.name}' must have exactly one decorator", hint
            )
        deco = next(d for d in fn.decorator_list if _deco_name(d) == PROCEDURE)
        if isinstance(deco, ast.Name):
            return frozenset()
        assert isinstance(deco, ast.Call)
        if deco.args or any(kw.arg != EFFECTS_KW for kw in deco.keywords):
            self.ctx.error("L148", deco, "@procedure takes only effects=[...]", hint)
            return frozenset()
        effects: set[str] = set()
        for kw in deco.keywords:
            value = kw.value
            if not isinstance(value, ast.List | ast.Tuple) or not all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
            ):
                self.ctx.error("L148", value, "effects= must be a list of capability names", hint)
                continue
            caps = self.api.effect_capabilities()
            for e in value.elts:
                assert isinstance(e, ast.Constant)
                if e.value in effects:
                    self.ctx.error("L148", e, f"effect '{e.value}' is declared twice")
                    continue
                effects.add(e.value)
                if e.value not in caps:
                    shown = ", ".join(sorted(caps)) or "(none)"
                    self.ctx.error(
                        "L920",
                        e,
                        f"'{e.value}' is not a capability of this profile",
                        f"capabilities: {shown}",
                    )
        return frozenset(effects)

    def _params(self, fn: ast.FunctionDef, env: T.TypeEnv) -> tuple[tuple[str, T2Type], ...]:
        args = fn.args
        problems = []
        if args.posonlyargs:
            problems.append("positional-only parameters")
        if args.kwonlyargs:
            problems.append("keyword-only parameters")
        if args.vararg:
            problems.append("*args")
        if args.kwarg:
            problems.append("**kwargs")
        if args.defaults or any(d is not None for d in args.kw_defaults):
            problems.append("default values")
        if fn.type_params:
            problems.append("type parameters")
        if problems:
            self.ctx.error(
                "L148",
                fn,
                f"procedure '{fn.name}' uses " + ", ".join(problems),
                "list plain annotated parameters, e.g. (item: Row[Items], now: DateTime)",
            )
        out: list[tuple[str, T2Type]] = []
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if a.annotation is None:
                self.ctx.error(
                    "L148",
                    a,
                    f"parameter '{a.arg}' needs a type annotation",
                    f"e.g. '{a.arg}: Int'",
                )
                out.append((a.arg, UNK))
            else:
                out.append((a.arg, self._ann(a.annotation, env)))
        return tuple(out)

    # ---- names ---------------------------------------------------------------------

    def _clash(self, node: ast.AST, name: str, what: str) -> None:
        self.ctx.error("L914", node, f"'{name}' {what}", "give each definition its own name")

    def _clashes(self) -> None:
        t0_names: set[str] = set()
        for s in self.t0:
            if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef):
                t0_names.add(s.name)
            elif isinstance(s, ast.Assign):
                t0_names |= {t.id for t in s.targets if isinstance(t, ast.Name)}
        reserved = (
            set(T2_BUILTINS)
            | set(T2_HELPERS)
            | set(T2_TYPE_NAMES)
            | set(T2_TYPE_ALIASES)
            | set(T2_OPS_NAMES)
            | set(self.api.tables)
        )
        for name, node in self.top.items():
            if name in t0_names:
                self._clash(node, name, "is also a step, endpoint or module variable")
            elif name not in self.imports and name in reserved:
                self._clash(node, name, "is a built-in name, a type or a table")

    def _constants(self, info: ModuleInfo) -> None:
        """Module constants procedures may read: `NAME = <constant expression>`."""
        for s in self.t0:
            if not (
                isinstance(s, ast.Assign)
                and len(s.targets) == 1
                and isinstance(s.targets[0], ast.Name)
                and (is_constant_expr(s.value) or is_list_constant_assign(s))
            ):
                continue
            scratch = CheckContext(self.ctx.profile)
            t = BodyChecker(info, scratch, None, None).constant(s.value)
            if scratch.diagnostics or T.has_unk(t):
                continue
            self.constants[s.targets[0].id] = t


def _recursive(graph: Mapping[str, frozenset[str]]) -> set[str]:
    """Procedures on a cycle of the call graph."""
    out: set[str] = set()
    for start in graph:
        seen: set[str] = set()
        stack = list(graph.get(start, ()))
        while stack:
            n = stack.pop()
            if n == start:
                out.add(start)
                break
            if n in seen:
                continue
            seen.add(n)
            stack.extend(graph.get(n, ()))
    return out
