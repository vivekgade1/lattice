"""T2 procedure bodies: subset validation and typing in one forward pass.

Every expression's type goes into a side table (id(node) -> T2Type). Locals get the
type of their first assignment (or annotation); later assignments must fit it
(Int -> Float widening only). Flow facts live in an `Env`: the current (possibly
narrowed) type of each bound local and of immutable attribute paths such as
`item.due`, and the locals that may be unbound. Branches join; loops start from a
widened environment (every local the loop assigns reset to its declared type), so
one pass is sound without a fixpoint. A rejected construct yields one diagnostic
and the checker never descends into it; an expression that produced a diagnostic
has type UNK, which is compatible with everything, so mistakes do not cascade.

Parameter immutability (L145) is checked after typing: a flow-insensitive alias
analysis over the collected assignments marks the locals that may hold (part of) a
value passed in, and every mutation site (mutating method, item assignment,
in-place operator) whose receiver may alias one is reported.
"""

from __future__ import annotations

import ast
import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache

from lattice.catalog import (
    NATIVE_NS,
    T2_ATTRS,
    T2_BROAD_ERRORS,
    T2_BUILTINS,
    T2_DEFAULTDICT_FACTORIES,
    T2_ERRORS,
    T2_HELPERS,
    T2_IO_CALLS,
    T2_METHODS,
    T2_REFLECTION_CALLS,
    T2_STDLIB,
    StdSig,
)
from lattice.check.astutil import INT64_MAX, INT64_MIN, literal_value
from lattice.check.context import CheckContext
from lattice.check.loop_hints import t1_shape_warning
from lattice.check.re2 import re2_problem
from lattice.check.redos import redos_risk
from lattice.check.t2 import types as T
from lattice.check.t2.model import ProcSig, RecordDef
from lattice.check.t2.types import UNK, T2Type
from lattice.profile import ApiProfile
from lattice.typesys import Scalar


@cache
def sig_type(text: str) -> T2Type:
    return T.parse_sig(text)


def prim(s: Scalar) -> T2Type:
    return T.PRIMS[s.value]


@dataclass
class ModuleInfo:
    """What a procedure body sees at module level."""

    api: ApiProfile
    sigs: Mapping[str, ProcSig]
    records: Mapping[str, RecordDef]
    imports: Mapping[str, str]  # local name -> "module" or "module.name"
    constants: Mapping[str, T2Type]


@dataclass
class Env:
    types: dict[str, T2Type] = field(default_factory=dict)
    maybe: set[str] = field(default_factory=set)

    def copy(self) -> Env:
        return Env(dict(self.types), set(self.maybe))

    def drop_paths(self, base: str) -> None:
        prefix = base + "."
        for k in [k for k in self.types if k.startswith(prefix)]:
            del self.types[k]


@dataclass
class _Loop:
    breaks: list[Env] = field(default_factory=list)
    continues: list[Env] = field(default_factory=list)


_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPES = (*_COMPS, ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# Calls whose result is a new top-level container (its elements may still alias).
_COPIES = frozenset(
    {
        "builtin:list",
        "builtin:set",
        "builtin:dict",
        "builtin:sorted",
        "std:collections.Counter",
        "method:list.copy",
        "method:dict.copy",
        "method:set.copy",
        "method:set.union",
        "method:set.intersection",
        "method:set.difference",
    }
)
_ORDERING = frozenset({"builtin:sorted", "builtin:min", "builtin:max", "method:list.sort"})
_OPS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.MatMult: "@",
}
_STMT_HINTS: dict[type[ast.AST], tuple[str, str]] = {
    ast.With: ("'with' is not allowed in a procedure", "procedures hold no resources"),
    ast.Raise: (
        "'raise' is not allowed in a procedure",
        "return a value that signals the failure (e.g. None) and let the endpoint answer",
    ),
    ast.Assert: ("'assert' is not allowed in a procedure", "use 'if ...: return ...'"),
    ast.Delete: (
        "'del' is not allowed in a procedure",
        "build a new value; d.pop(k) removes a key from a local dict",
    ),
    ast.Match: ("'match' is not allowed in a procedure", "use if/elif/else"),
    ast.TryStar: ("'except*' is not allowed in a procedure", "use a plain try/except"),
    ast.FunctionDef: (
        "nested functions are not allowed",
        "define helper @procedures at module level and call them",
    ),
    ast.ClassDef: ("classes are defined at module level", "declare a @record at module level"),
    ast.Import: ("imports go at the top of the module", "move the import out of the procedure"),
    ast.ImportFrom: ("imports go at the top of the module", "move the import out of the procedure"),
}


def assigned_names(nodes: Iterable[ast.AST]) -> set[str]:
    """Names a statement list binds (Python's local-variable rule), not counting
    comprehension, lambda or nested-definition scopes."""
    out: set[str] = set()
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if isinstance(n, _SCOPES):
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        stack.extend(ast.iter_child_nodes(n))
    return out


def path_of(node: ast.AST) -> str | None:
    """`x` or `x.a.b`: an access path whose type may be narrowed."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = path_of(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _empty_kind(node: ast.AST) -> bool:
    match node:
        case ast.List(elts=[]) | ast.Set(elts=[]) | ast.Dict(keys=[]):
            return True
        case ast.Call(func=ast.Name(id="list" | "set" | "dict"), args=[], keywords=[]):
            return True
    return False


def kind_of(t: T2Type) -> str | None:
    match t:
        case T.ListT():
            return "list"
        case T.DictT():
            return "dict"
        case T.SetT():
            return "set"
        case T.CounterT():
            return "Counter"
        case T.Prim(name=name) if name in ("Str", "Date", "DateTime", "Duration", "Float"):
            return name
        case T.Named(name="Match"):
            return "Match"
    return None


def _render_sig(label: str, sig: StdSig) -> str:
    parts = [*sig.params, *(f"{p}?" for p in sig.optional)]
    parts += [f"{k}={v}" for k, v in sig.kwargs.items()]
    return f"{label}({', '.join(parts)}) -> {sig.result}"


class BodyChecker:
    def __init__(
        self,
        info: ModuleInfo,
        ctx: CheckContext,
        fn: ast.FunctionDef | None = None,
        sig: ProcSig | None = None,
    ) -> None:
        self.info = info
        self.ctx = ctx
        self.fn = fn
        self.sig = sig or ProcSig("<module>", (), T.NONE)
        self.types: dict[int, T2Type] = {}
        self.targets: dict[int, str] = {}
        self.calls: set[str] = set()
        self.natives: set[str] = set()
        self.const_uses: set[str] = set()
        self.declared: dict[str, T2Type] = dict(self.sig.params)
        self.params = frozenset(n for n, _ in self.sig.params)
        self.local_names: set[str] = set(self.params)
        if fn is not None:
            self.local_names |= assigned_names(fn.body)
        self.loops: list[_Loop] = []
        self.assigns: list[tuple[str, ast.expr, bool]] = []
        self.mutations: list[tuple[ast.AST, ast.expr]] = []
        self.reported: set[tuple[str, str]] = set()

    # ---- entry points -----------------------------------------------------------

    def run(self) -> None:
        assert self.fn is not None
        env = Env(dict(self.sig.params))
        end = self.block(self.fn.body, env)
        ret = self.sig.returns
        if end is not None and ret != T.NONE and ret is not UNK:
            self.err(
                "L907",
                self.fn,
                f"not every path of '{self.fn.name}' returns a value ({ret})",
                "end every path with 'return ...' (a procedure that returns nothing is "
                "declared '-> None')",
            )
        self._check_mutations()

    def constant(self, node: ast.expr) -> T2Type:
        """The type of a module constant's value (module constants only)."""
        return self.expr(node, Env())

    # ---- diagnostics -------------------------------------------------------------

    def err(self, code: str, node: ast.AST, message: str, hint: str | None = None) -> T2Type:
        self.ctx.error(code, node, message, hint)
        return UNK

    def warn(self, code: str, node: ast.AST, message: str, hint: str | None = None) -> None:
        self.ctx.warn(code, node, message, hint)

    def _once(self, key: str, code: str) -> bool:
        if (key, code) in self.reported:
            return False
        self.reported.add((key, code))
        return True

    def _maybe_none(self, node: ast.AST) -> T2Type:
        text = ast.unparse(node)
        return self.err(
            "L906",
            node,
            f"'{text}' may be None here",
            f"check 'if {text} is not None:' first (or return early when it is None)",
        )

    def _mismatch(
        self, code: str, node: ast.AST, got: T2Type, want: T2Type, message: str, hint: str | None
    ) -> None:
        """A value of `got` where `want` is expected: L906 when only None is in the way."""
        if isinstance(got, T.Opt) and T.assignable(got.inner, want):
            self._maybe_none(node)
        else:
            self.err(code, node, message, hint)

    # ---- environments ----------------------------------------------------------

    def join(self, a: Env | None, b: Env | None) -> Env | None:
        if a is None:
            return b.copy() if b is not None else None
        if b is None:
            return a.copy()
        out = Env({}, a.maybe | b.maybe)
        for k in set(a.types) | set(b.types):
            ta, tb = a.types.get(k), b.types.get(k)
            if "." in k:
                if ta is not None and ta == tb:
                    out.types[k] = ta
                continue
            if ta is None or tb is None:
                out.types[k] = self.declared.get(k, ta if ta is not None else tb)  # type: ignore[arg-type]
                out.maybe.add(k)
            elif ta == tb:
                out.types[k] = ta
            else:
                j = T.join(ta, tb)
                out.types[k] = j if j is not None else self.declared.get(k, UNK)
        return out

    def join_all(self, envs: Iterable[Env | None]) -> Env | None:
        out: Env | None = None
        for e in envs:
            out = self.join(out, e) if out is not None else (e.copy() if e else None)
        return out

    def widen(self, env: Env, names: set[str]) -> Env:
        out = env.copy()
        for n in names:
            if n in out.types:
                out.types[n] = self.declared.get(n, out.types[n])
            out.drop_paths(n)
        return out

    # ---- statements ------------------------------------------------------------

    def block(self, stmts: Sequence[ast.stmt], env: Env | None) -> Env | None:
        for s in stmts:
            if env is None:
                break  # unreachable code is not checked
            env = self.stmt(s, env)
        return env

    def stmt(self, s: ast.stmt, env: Env) -> Env | None:
        match s:
            case ast.Expr(value=ast.Constant(value=str())):
                return env  # a docstring / comment string
            case ast.Expr(value=ast.Call() as call):
                self.expr(call, env)
                return env
            case ast.Expr():
                self.err(
                    "L146",
                    s,
                    "a bare expression has no effect",
                    "assign it to a name, or remove it",
                )
                return env
            case ast.Assign():
                return self._assign(s, env)
            case ast.AnnAssign():
                return self._ann_assign(s, env)
            case ast.AugAssign():
                return self._aug_assign(s, env)
            case ast.If():
                self.expr(s.test, env)
                then_env, else_env = self.narrow(s.test, env)
                a = self.block(s.body, then_env)
                b = self.block(s.orelse, else_env) if s.orelse else else_env
                return self.join(a, b)
            case ast.For():
                return self._for(s, env)
            case ast.While():
                return self._while(s, env)
            case ast.Break() | ast.Continue():
                if not self.loops:
                    word = "break" if isinstance(s, ast.Break) else "continue"
                    self.err("L919", s, f"'{word}' outside a loop")
                    return env
                frame = self.loops[-1]
                (frame.breaks if isinstance(s, ast.Break) else frame.continues).append(env.copy())
                return None
            case ast.Return():
                self._return(s, env)
                return None
            case ast.Pass():
                return env
            case ast.Try():
                return self._try(s, env)
            case ast.Global() | ast.Nonlocal():
                self.err(
                    "L130",
                    s,
                    "'global'/'nonlocal' are not allowed",
                    "procedures cannot change module state; return the new value instead",
                )
                return env
            case ast.AsyncFor() | ast.AsyncWith() | ast.AsyncFunctionDef():
                self.err(
                    "L131", s, "async constructs are not allowed", "procedures are synchronous"
                )
                return env
        msg, hint = _STMT_HINTS.get(
            type(s), (f"'{type(s).__name__}' is not allowed in a procedure", None)
        )
        self.err("L146", s, msg, hint)
        return env

    def _assign(self, s: ast.Assign, env: Env) -> Env:
        expected = None
        if len(s.targets) == 1 and isinstance(s.targets[0], ast.Name):
            expected = self.declared.get(s.targets[0].id)
        vt = self.expr(s.value, env, expected)
        for tgt in s.targets:
            env = self.assign_target(tgt, vt, s.value, env, elem=False)
        return env

    def assign_target(
        self, tgt: ast.expr, vt: T2Type, value: ast.expr, env: Env, elem: bool
    ) -> Env:
        match tgt:
            case ast.Name(id=name):
                self.bind(name, vt, tgt, env)
                self.assigns.append((name, value, elem))
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                parts = self._unpack(tgt, vt, len(elts))
                for sub, t in zip(elts, parts, strict=True):
                    env = self.assign_target(sub, t, value, env, elem=True)
                self.types[id(tgt)] = vt
            case ast.Subscript():
                self._store_subscript(tgt, vt, env)
            case ast.Attribute():
                self.err(
                    "L145",
                    tgt,
                    f"'{ast.unparse(tgt)}' cannot be assigned: records and rows are immutable",
                    "build a new record with the changed field: Name(field=..., ...)",
                )
            case _:
                self.err("L147", tgt, f"cannot assign to '{ast.unparse(tgt)}'")
        return env

    def _unpack(self, tgt: ast.expr, vt: T2Type, n: int) -> list[T2Type]:
        if any(isinstance(e, ast.Starred) for e in getattr(tgt, "elts", [])):
            self.err("L147", tgt, "starred unpacking is not allowed", "unpack each value")
            return [UNK] * n
        match vt:
            case T.TupleT(elems=es):
                if len(es) != n:
                    self.err(
                        "L917",
                        tgt,
                        f"cannot unpack {vt} into {n} names",
                        f"use exactly {len(es)} names",
                    )
                    return [UNK] * n
                return list(es)
            case T.Opt():
                self._maybe_none(tgt)
                return [UNK] * n
        if vt is UNK:
            return [UNK] * n
        el = T.elem_of(vt)
        if el is None:
            self.err("L909", tgt, f"cannot unpack a value of type {vt}")
            return [UNK] * n
        return [el] * n

    def bind(self, name: str, vt: T2Type, node: ast.AST, env: Env) -> None:
        d = self.declared.get(name)
        if d is None:
            self.declared[name] = vt
            d = vt
        elif not T.assignable(vt, d):
            self._mismatch(
                "L901",
                node,
                vt,
                d,
                f"'{name}' is {d} but is assigned {vt}",
                self._widen_hint(name, d, vt),
            )
            vt = d
        env.types[name] = self._narrowed(vt, d)
        env.maybe.discard(name)
        env.drop_paths(name)
        self.types[id(node)] = vt

    @staticmethod
    def _widen_hint(name: str, d: T2Type, vt: T2Type) -> str:
        if d == T.INT and vt == T.FLOAT:
            return (
                f"'{name}' became an Int at its first assignment: write a Float there "
                f"(e.g. 0.0), or annotate '{name}: Float = ...'"
            )
        if d == T.NONE:
            return f"annotate the first assignment: '{name}: {vt} | None = None'"
        return f"assign a {d}, or annotate the first assignment with the type you mean"

    @staticmethod
    def _narrowed(vt: T2Type, d: T2Type) -> T2Type:
        if vt is UNK:
            return d
        if isinstance(d, T.Opt):
            if vt == T.NONE:
                return T.NONE
            if T.assignable(vt, d.inner):
                return d.inner
        return d

    def _ann_assign(self, s: ast.AnnAssign, env: Env) -> Env:
        if not (s.simple and isinstance(s.target, ast.Name)):
            self.err("L146", s, "only a plain name can be annotated", "write 'name: Type = value'")
            return env
        name = s.target.id
        ann = self.annotation(s.annotation)
        old = self.declared.get(name)
        if old is not None and not T.same(old, ann):
            self.err(
                "L901",
                s.target,
                f"'{name}' already has type {old}",
                "annotate a variable once, at its first assignment",
            )
            ann = old
        self.declared[name] = ann
        if s.value is None:
            self.types[id(s.target)] = ann
            return env
        vt = self.expr(s.value, env, ann)
        self.bind(name, vt, s.target, env)
        self.assigns.append((name, s.value, False))
        return env

    def annotation(self, node: ast.expr) -> T2Type:
        try:
            return T.parse_annotation(node, self.type_env())
        except T.AnnotationError as e:
            return self.err("L902", e.node, e.message, e.hint)

    def type_env(self) -> T.TypeEnv:
        return T.TypeEnv(frozenset(self.info.api.tables), frozenset(self.info.records))

    def _aug_assign(self, s: ast.AugAssign, env: Env) -> Env:
        tgt = s.target
        rt = self.expr(s.value, env)
        match tgt:
            case ast.Name(id=name):
                cur = self.expr(tgt, env)  # a load: must be bound
                res = self.binop(s.op, cur, rt, s, tgt, s.value)
                d = self.declared.get(name, UNK)
                if not T.assignable(res, d):
                    self.err(
                        "L901",
                        s,
                        f"'{name}' is {d} but '{ast.unparse(s)}' makes it {res}",
                        self._widen_hint(name, d, res),
                    )
                    res = d
                if T.is_mutable(cur):
                    self.mutations.append((s, tgt))  # in place: xs += [...]
                env.types[name] = self._narrowed(res, d)
                env.drop_paths(name)
            case ast.Subscript(slice=ast.Slice()):
                self._load_subscript(tgt, env)  # types the operands (no cascade)
                # v1.1: `xs[a:b] op= v` assigns a slice, rejected like `xs[a:b] = v`
                self.err("L913", tgt, "slice assignment is not supported", "build a new list")
            case ast.Subscript():
                cur = self._load_subscript(tgt, env)
                res = self.binop(s.op, cur, rt, s, tgt, s.value)
                if not T.assignable(res, cur):
                    self.err("L901", s, f"the item is {cur} but '{ast.unparse(s)}' makes it {res}")
                self.mutations.append((s, tgt.value))
            case ast.Attribute():
                self.err(
                    "L145",
                    tgt,
                    f"'{ast.unparse(tgt)}' cannot be assigned: records and rows are immutable",
                    "build a new record with the changed field: Name(field=..., ...)",
                )
            case _:
                self.err("L147", tgt, f"cannot assign to '{ast.unparse(tgt)}'")
        return env

    def _store_subscript(self, tgt: ast.Subscript, vt: T2Type, env: Env) -> None:
        ct = self.expr(tgt.value, env)
        self.types[id(tgt)] = vt
        if isinstance(tgt.slice, ast.Slice):
            self.err("L913", tgt, "slice assignment is not supported", "build a new list")
            return
        kt = self.expr(tgt.slice, env)
        self.mutations.append((tgt, tgt.value))
        if isinstance(ct, T.ListT):
            if not T.assignable(kt, T.INT):
                self.err("L913", tgt.slice, f"a list index is an Int, not {kt}")
            elif not T.assignable(vt, ct.elem):
                self._mismatch(
                    "L901", tgt, vt, ct.elem, f"the list holds {ct.elem}, not {vt}", None
                )
        if isinstance(ct, T.DictT | T.CounterT):
            k = ct.key
            v = ct.val if isinstance(ct, T.DictT) else T.INT
            if not T.assignable(kt, k):
                self._mismatch("L913", tgt.slice, kt, k, f"the dict's keys are {k}, not {kt}", None)
            elif not T.assignable(vt, v):
                self._mismatch("L901", tgt, vt, v, f"the dict's values are {v}, not {vt}", None)
        elif isinstance(ct, T.Opt):
            self._maybe_none(tgt.value)
        elif ct is not UNK and not isinstance(ct, T.ListT):
            self.err("L913", tgt, f"items of a {ct} cannot be assigned")

    def _t1_shape_warn(self, s: ast.For | ast.While) -> None:
        """spec 2026-09-27 lattice-t2-design §5: a loop shaped like window/traverse/
        for_each gets a warning naming the T1 construct (L924) -- not an error, since
        T2 permits the loop."""
        hint = t1_shape_warning(s)
        if hint is not None:
            self.warn("L924", s, "this loop matches a T1 construct", hint)

    def _for(self, s: ast.For, env: Env) -> Env | None:
        self._t1_shape_warn(s)
        it = self.expr(s.iter, env)
        el = self._elem(it, s.iter)
        assigned = assigned_names([*s.body, s.target])
        start = self.widen(env, assigned)
        start = self.assign_target(s.target, el, s.iter, start, elem=True)
        self.loops.append(_Loop())
        end = self.block(s.body, start)
        frame = self.loops.pop()
        self._loop_else(s)
        return self.join_all([env, end, *frame.continues, *frame.breaks])

    def _elem(self, it: T2Type, node: ast.expr) -> T2Type:
        if isinstance(it, T.Opt):
            self._maybe_none(node)
            return UNK
        el = T.elem_of(it)
        if el is None:
            self.err(
                "L909",
                node,
                f"cannot iterate over a value of type {it}",
                "iterate a list, set, dict, str or range(...)",
            )
            return UNK
        return el

    def _while(self, s: ast.While, env: Env) -> Env | None:
        self._t1_shape_warn(s)
        start = self.widen(env, assigned_names(s.body))
        self.expr(s.test, start)
        then_env, else_env = self.narrow(s.test, start)
        self.loops.append(_Loop())
        self.block(s.body, then_env)
        frame = self.loops.pop()
        self._loop_else(s)
        forever = isinstance(s.test, ast.Constant) and s.test.value is True
        return self.join_all([*frame.breaks, *([] if forever else [else_env])])

    def _loop_else(self, s: ast.For | ast.While) -> None:
        if s.orelse:
            self.err(
                "L146",
                s.orelse[0],
                "a loop 'else' is not allowed in a procedure",
                "set a flag in the loop and test it after",
            )

    def _return(self, s: ast.Return, env: Env) -> None:
        rt = self.sig.returns
        if s.value is None:
            if rt not in (T.NONE, UNK):
                self.err("L908", s, f"'return' without a value in a procedure returning {rt}")
            return
        vt = self.expr(s.value, env, rt)
        if not T.assignable(vt, rt):
            self._mismatch(
                "L908",
                s.value,
                vt,
                rt,
                f"returns {vt}, but '{self.sig.name}' is declared to return {rt}",
                "return a value of the declared type, or change the return annotation",
            )

    def _try(self, s: ast.Try, env: Env) -> Env | None:
        if s.finalbody:
            self.err(
                "L146",
                s.finalbody[0],
                "'finally' is not allowed in a procedure",
                "procedures hold no resources; put the code after the try",
            )
        end_body = self.block(s.body, env.copy())
        handler_start = self.join(self.widen(env, assigned_names(s.body)), end_body)
        assert handler_start is not None
        outs: list[Env | None] = [
            self.block(s.orelse, end_body) if s.orelse and end_body is not None else end_body
        ]
        for h in s.handlers:
            henv = handler_start.copy()
            self._handler_types(h)
            if h.name:
                self.declared.setdefault(h.name, T.ERROR)
                henv.types[h.name] = T.ERROR
                henv.maybe.discard(h.name)
            outs.append(self.block(h.body, henv))
        return self.join_all(outs)

    def _handler_types(self, h: ast.ExceptHandler) -> None:
        errors = ", ".join(T2_ERRORS)
        if h.type is None:
            self.err(
                "L149",
                h,
                "a bare 'except:' also catches fuel exhaustion (R620)",
                f"name the errors you expect: {errors}",
            )
            return
        names = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        for n in names:
            self.types[id(n)] = T.ERROR
            if isinstance(n, ast.Name) and n.id in T2_ERRORS:
                continue
            if isinstance(n, ast.Name) and n.id in T2_BROAD_ERRORS:
                self.err(
                    "L149",
                    n,
                    f"'except {n.id}' also catches fuel exhaustion (R620)",
                    f"name the errors you expect: {errors}",
                )
                continue
            self.err(
                "L918",
                n,
                f"'{ast.unparse(n)}' is not an error a procedure can catch",
                f"catch one of: {errors}",
            )
        if isinstance(h.type, ast.Tuple):
            self.types[id(h.type)] = T.ERROR

    # ---- narrowing ------------------------------------------------------------------

    def narrow(self, test: ast.expr, env: Env) -> tuple[Env | None, Env | None]:
        """The environments where `test` is true / false (types narrowed)."""
        match test:
            case ast.Compare(left=left, ops=[op], comparators=[right]) if isinstance(
                op, ast.Is | ast.IsNot | ast.Eq | ast.NotEq
            ):
                target = None
                if isinstance(right, ast.Constant) and right.value is None:
                    target = left
                elif isinstance(left, ast.Constant) and left.value is None:
                    target = right
                path = path_of(target) if target is not None else None
                if target is None or path is None or path.split(".")[0] not in env.types:
                    return env.copy(), env.copy()
                cur = self.types.get(id(target), UNK)
                is_none, not_none = env.copy(), env.copy()
                is_none.types[path] = T.NONE
                not_none.types[path] = T.strip_opt(cur)
                if isinstance(op, ast.Is | ast.Eq):
                    return is_none, not_none
                return not_none, is_none
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                a, b = self.narrow(operand, env)
                return b, a
            case ast.BoolOp(op=op, values=values):
                cur: Env | None = env
                others: list[Env | None] = []
                for v in values:
                    if cur is None:
                        break
                    t, f = self.narrow(v, cur)
                    if isinstance(op, ast.And):
                        others.append(f)
                        cur = t
                    else:
                        others.append(t)
                        cur = f
                if isinstance(op, ast.And):
                    return cur, self.join_all(others)
                return self.join_all(others), cur
            case ast.Name() | ast.Attribute():
                path = path_of(test)
                if path is None or path.split(".")[0] not in env.types:
                    return env.copy(), env.copy()
                cur = self.types.get(id(test), UNK)
                truthy = env.copy()
                truthy.types[path] = T.strip_opt(cur)
                return truthy, env.copy()
        return env.copy(), env.copy()

    # ---- expressions ----------------------------------------------------------------

    def expr(self, node: ast.expr, env: Env, expected: T2Type | None = None) -> T2Type:
        t = self._expr(node, env, expected)
        self.types[id(node)] = t
        return t

    def _expr(self, node: ast.expr, env: Env, expected: T2Type | None) -> T2Type:
        match node:
            case ast.Constant(value=value):
                if isinstance(value, bool):
                    return T.BOOL
                if isinstance(value, int):
                    return self._int_literal(node, value)
                if isinstance(value, float):
                    return T.FLOAT
                if isinstance(value, str):
                    return T.STR
                if value is None:
                    return T.NONE
                return self.err("L147", node, f"literal {value!r} is not supported")
            case ast.Name():
                return self._load_name(node, env)
            case ast.Attribute():
                return self._attribute(node, env)
            case ast.Call():
                return self._call(node, env, expected)
            case ast.BinOp(left=left, op=op, right=right):
                lt = self.expr(left, env)
                rt = self.expr(right, env)
                return self.binop(op, lt, rt, node, left, right)
            case ast.UnaryOp():
                return self._unary(node, env)
            case ast.BoolOp():
                return self._boolop(node, env)
            case ast.Compare():
                return self._compare(node, env)
            case ast.IfExp(test=test, body=body, orelse=orelse):
                self.expr(test, env)
                te, ee = self.narrow(test, env)
                a = self.expr(body, te or env, expected)
                b = self.expr(orelse, ee or env, expected)
                j = T.join(a, b)
                if j is None:
                    return self.err(
                        "L903",
                        node,
                        f"the branches of the conditional have different types: {a} and {b}",
                    )
                return j
            case ast.List() | ast.Set() | ast.Tuple() | ast.Dict():
                return self._literal(node, env, expected)
            case ast.ListComp() | ast.SetComp() | ast.DictComp():
                return self._comprehension(node, env)
            case ast.GeneratorExp():
                return self.err(
                    "L147",
                    node,
                    "a generator expression is only allowed as a call argument",
                    "use a list comprehension [...], or pass it directly: sum(x for x in xs)",
                )
            case ast.JoinedStr(values=values):
                for v in values:
                    self.expr(v, env)
                return T.STR
            case ast.FormattedValue(value=value, format_spec=spec):
                self.expr(value, env)
                if spec is not None:
                    self.expr(spec, env)
                return T.STR
            case ast.Subscript():
                return self._load_subscript(node, env)
            case ast.Lambda():
                return self.err(
                    "L147",
                    node,
                    "lambda is only allowed as key= of sorted/min/max/list.sort",
                    "define a @procedure and call it",
                )
            case ast.NamedExpr(target=target):
                self.err(
                    "L147", node, "assignment expressions (:=) are not allowed", "assign first"
                )
                self.declared.setdefault(target.id, UNK)
                env.types[target.id] = UNK
                return UNK
            case ast.Await() | ast.Yield() | ast.YieldFrom():
                return self.err("L131", node, "async/yield are not allowed in a procedure")
            case ast.Starred():
                return self.err("L147", node, "starred expressions are not allowed")
        return self.err("L147", node, f"'{type(node).__name__}' is not allowed in a procedure")

    def _int_literal(self, node: ast.expr, value: int) -> T2Type:
        """Range-check a (possibly negated as a unit, see `_unary`) Int literal;
        same code and message style as T0's `p1_nodes._check_int_range`."""
        if not INT64_MIN <= value <= INT64_MAX:
            return self.err(
                "L404",
                node,
                f"integer literal {value} does not fit in a 64-bit Int",
                f"use a value between {INT64_MIN} and {INT64_MAX}",
            )
        return T.INT

    def _load_name(self, node: ast.Name, env: Env) -> T2Type:
        name = node.id
        if name in env.types:
            if name in env.maybe and self._once(name, "L910"):
                self.err(
                    "L910",
                    node,
                    f"'{name}' may be unbound here: it is not assigned on every path",
                    f"assign it before the branch or loop, e.g. '{name} = <default>'",
                )
            return env.types[name]
        if name in self.local_names:
            if self._once(name, "L910"):
                self.err(
                    "L910",
                    node,
                    f"'{name}' is used before it is assigned",
                    f"assign '{name}' first",
                )
            return self.declared.get(name, UNK)
        info = self.info
        if name in info.constants:
            self.const_uses.add(name)
            return info.constants[name]
        imported = info.imports.get(name)
        if imported is not None and "." in imported:
            mod, attr = imported.split(".", 1)
            entry = T2_STDLIB[mod][attr]
            if isinstance(entry, str):
                return sig_type(entry)
        if name in T2_IO_CALLS:
            return self.err("L140", node, f"'{name}' is not allowed: procedures do no I/O")
        if name in T2_REFLECTION_CALLS:
            return self.err("L141", node, f"'{name}' is reflection, not allowed in a procedure")
        callable_kind = (
            "procedure"
            if name in info.sigs
            else "record type"
            if name in info.records
            else "module"
            if imported is not None and "." not in imported
            else "function"
            if imported is not None or name in T2_BUILTINS or name in T2_HELPERS
            else None
        )
        if callable_kind == "module":
            return self.err(
                "L147",
                node,
                f"module '{name}' is used as {name}.<name>",
                f"e.g. {name}.<name>(...)",
            )
        if callable_kind is not None:
            return self.err(
                "L147",
                node,
                f"{callable_kind} '{name}' can only be called",
                f"call it: {name}(...)",
            )
        if name == NATIVE_NS:
            return self.err("L147", node, "natives can only be called: native.<name>(...)")
        if not self._once(name, "L900"):
            return UNK
        known = [*env.types, *info.constants, *info.sigs, *info.records, *info.imports]
        close = difflib.get_close_matches(name, known, n=1)
        return self.err(
            "L900",
            node,
            f"unknown name '{name}'",
            f"did you mean '{close[0]}'?" if close else "assign it first, or pass it in",
        )

    def _is_global(self, node: ast.expr, env: Env) -> str | None:
        """The name of a module-level name (not shadowed by a local)."""
        if isinstance(node, ast.Name) and node.id not in env.types:
            if node.id not in self.local_names:
                return node.id
        return None

    def _attribute(self, node: ast.Attribute, env: Env) -> T2Type:
        attr = node.attr
        if attr.startswith("__") and attr.endswith("__"):
            return self.err(
                "L141",
                node,
                f"'.{attr}' is reflection, not allowed in a procedure",
                "use the value's fields directly",
            )
        g = self._is_global(node.value, env)
        if g is not None:
            mod = self.info.imports.get(g)
            if mod is not None and "." not in mod:
                self.types[id(node.value)] = T.Named("module")
                entry = T2_STDLIB[mod].get(attr)
                if entry is None:
                    return self._not_allowlisted(node, mod, attr)
                if isinstance(entry, str):
                    return sig_type(entry)
                return self.err("L147", node, f"'{mod}.{attr}' can only be called")
            if g == NATIVE_NS:
                self.types[id(node.value)] = T.Named("native")
                return self.err("L147", node, "natives can only be called: native.<name>(...)")
        vt = self.expr(node.value, env)
        path = path_of(node)
        if path is not None and path in env.types:
            return env.types[path]
        return self.attr_of(vt, attr, node)

    def _not_allowlisted(self, node: ast.AST, mod: str, attr: str) -> T2Type:
        names = ", ".join(sorted(T2_STDLIB[mod]))
        return self.err(
            "L142",
            node,
            f"'{mod}.{attr}' is not in the procedure allowlist",
            f"allowed from {mod}: {names}",
        )

    def attr_of(self, vt: T2Type, attr: str, node: ast.Attribute) -> T2Type:
        match vt:
            case T.Opt():
                return self._maybe_none(node.value)
            case T.RowT():
                return self._row_attr(vt, attr, node)
            case T.RecordT(name=name):
                rec = self.info.records.get(name)
                ft = rec.field_type(attr) if rec is not None else UNK
                if ft is None:
                    assert rec is not None
                    fields = ", ".join(n for n, _ in rec.fields)
                    return self.err(
                        "L905", node, f"record {name} has no field '{attr}'", f"fields: {fields}"
                    )
                return ft
            case T.Named(name="Caller"):
                return self._caller_attr(attr, node)
        if vt is UNK:
            return UNK
        kind = kind_of(vt)
        attrs = T2_ATTRS.get(kind or "", {})
        if attr in attrs:
            return sig_type(attrs[attr])
        methods = T2_METHODS.get(kind or "", {})
        if attr in methods:
            return self.err(
                "L147", node, f"'.{attr}' is a method", f"call it: {ast.unparse(node)}(...)"
            )
        close = difflib.get_close_matches(attr, [*attrs, *methods], n=1)
        return self.err(
            "L905",
            node,
            f"a {vt} has no attribute '{attr}'",
            f"did you mean '{close[0]}'?" if close else None,
        )

    def _row_attr(self, vt: T.RowT, attr: str, node: ast.Attribute) -> T2Type:
        schema = self.info.api.tables[vt.table]
        col = schema.get(attr)
        if col is None:
            visible = [c for c in schema.names() if vt.private or c not in schema.private]
            close = difflib.get_close_matches(attr, visible, n=1)
            return self.err(
                "L905",
                node,
                f"table {vt.table} has no column '{attr}'",
                f"did you mean '{close[0]}'?" if close else f"columns: {', '.join(visible)}",
            )
        if attr in schema.private and not vt.private:
            return self.err(
                "L803",
                node,
                f"'{ast.unparse(node)}' reads the private column {attr} of {vt.table}",
                f"declare the parameter Row({vt.table}, private=True) (Admin endpoints only), "
                "or use a public column",
            )
        t = prim(col)
        return T.opt(t) if attr in schema.nullable else t

    def _caller_attr(self, attr: str, node: ast.Attribute) -> T2Type:
        api = self.info.api
        users = api.tables.get(api.users_table)
        if attr == "is_admin":
            return T.BOOL
        if attr == "role":
            return T.STR
        if users is None:
            return UNK
        if attr == "id" and users.keys and len(users.keys[0]) == 1:
            key = users.get(users.keys[0][0])
            if key is not None:
                return prim(key)
        col = users.get(attr)
        if col is None:
            public = ["id", "is_admin", "role"] + [
                c for c in users.names() if c not in users.private and c not in ("id", "role")
            ]
            close = difflib.get_close_matches(attr, public, n=1)
            return self.err(
                "L905",
                node,
                f"unknown caller attribute 'caller.{attr}'",
                f"did you mean '{close[0]}'?" if close else f"the caller has {', '.join(public)}",
            )
        if attr in users.private:
            return self.err(
                "L803",
                node,
                f"'{ast.unparse(node)}' reads the private column {attr} of the caller",
                "private columns never leave the database; use a public column",
            )
        return T.opt(prim(col)) if attr in users.nullable else prim(col)

    def _load_subscript(self, node: ast.Subscript, env: Env) -> T2Type:
        vt = self.expr(node.value, env)
        sl = node.slice
        if isinstance(sl, ast.Slice):
            for part in (sl.lower, sl.upper, sl.step):
                if part is not None:
                    pt = self.expr(part, env)
                    if not T.assignable(pt, T.opt(T.INT)):
                        self.err("L913", part, f"a slice bound is an Int, not {pt}")
            self.types[id(sl)] = vt
            if vt is UNK or isinstance(vt, T.ListT) or vt == T.STR:
                return vt
            if isinstance(vt, T.Opt):
                return self._maybe_none(node.value)
            return self.err("L913", node, f"a {vt} cannot be sliced", "slice a list or a str")
        kt = self.expr(sl, env)
        match vt:
            case T.Opt():
                return self._maybe_none(node.value)
            case T.ListT(elem=el):
                if not T.assignable(kt, T.INT):
                    return self.err("L913", sl, f"a list index is an Int, not {kt}")
                return el
            case T.TupleT(elems=es):
                i = literal_value(sl)
                if isinstance(i, int) and not isinstance(i, bool) and -len(es) <= i < len(es):
                    return es[i]
                if isinstance(i, int) and not isinstance(i, bool):
                    return self.err("L913", sl, f"index {i} is out of range for {vt}")
                el = T.elem_of(vt)
                if el is None or not T.assignable(kt, T.INT):
                    return self.err(
                        "L913", sl, f"index a {vt} with a constant", "e.g. pair[0], pair[1]"
                    )
                return el
            case T.DictT(key=k, val=v):
                if not T.assignable(kt, k):
                    self._mismatch("L913", sl, kt, k, f"the dict's keys are {k}, not {kt}", None)
                    return UNK
                return v
            case T.CounterT(key=k):
                if not T.assignable(kt, k):
                    self._mismatch("L913", sl, kt, k, f"the Counter's keys are {k}, not {kt}", None)
                return T.INT
        if vt == T.STR:
            if not T.assignable(kt, T.INT):
                return self.err("L913", sl, f"a str index is an Int, not {kt}")
            return T.STR
        if vt == T.JSON:
            if kt not in (T.STR, T.INT, UNK):
                return self.err("L913", sl, f"index Json with a Str or an Int, not {kt}")
            return T.JSON
        if vt is UNK:
            return UNK
        return self.err("L913", node, f"a {vt} cannot be indexed")

    # ---- operators -----------------------------------------------------------------

    def binop(
        self,
        op: ast.operator,
        lt: T2Type,
        rt: T2Type,
        node: ast.AST,
        left: ast.AST,
        right: ast.AST,
    ) -> T2Type:
        if lt is UNK or rt is UNK:
            return UNK
        if isinstance(lt, T.Opt):
            return self._maybe_none(left)
        if isinstance(rt, T.Opt):
            return self._maybe_none(right)
        res = _binop_result(op, lt, rt, right)
        if res is not None:
            return res
        sym = _OPS.get(type(op), "?")
        hint = None
        if T.STR in (lt, rt) and (lt in T.NUMERIC or rt in T.NUMERIC):
            hint = "convert with str(x) or build the text with an f-string"
        elif lt == T.STR and isinstance(op, ast.Mod):
            hint = "use an f-string"
        return self.err("L903", node, f"unsupported operand types for {sym}: {lt} and {rt}", hint)

    def _unary(self, node: ast.UnaryOp, env: Env) -> T2Type:
        operand = node.operand
        if (
            isinstance(node.op, ast.USub)
            and isinstance(operand, ast.Constant)
            and isinstance(operand.value, int)
            and not isinstance(operand.value, bool)
        ):
            # `-9223372036854775808` parses as USub(Constant(2**63)); the positive
            # magnitude alone overflows Int64 even though the negated literal (Int64
            # MIN) does not, so range-check the literal as one signed unit, mirroring
            # T0's p1_nodes._check_expr (Constant | UnaryOp(USub)).
            self.types[id(operand)] = T.INT
            return self._int_literal(node, -operand.value)
        t = self.expr(node.operand, env)
        if isinstance(node.op, ast.Not):
            return T.BOOL
        if t is UNK:
            return UNK
        if isinstance(t, T.Opt):
            return self._maybe_none(node.operand)
        if isinstance(node.op, ast.USub | ast.UAdd) and (t in T.NUMERIC or t == T.DURATION):
            return t
        if isinstance(node.op, ast.Invert) and t == T.INT:
            return T.INT
        return self.err("L903", node, f"unsupported operand type for unary operator: {t}")

    def _boolop(self, node: ast.BoolOp, env: Env) -> T2Type:
        cur: Env = env
        ts: list[T2Type] = []
        for v in node.values:
            ts.append(self.expr(v, cur))
            t_env, f_env = self.narrow(v, cur)
            nxt = t_env if isinstance(node.op, ast.And) else f_env
            cur = nxt if nxt is not None else cur
        if all(t in (T.BOOL, UNK) for t in ts):
            return T.BOOL
        if isinstance(node.op, ast.Or):
            ts = [T.strip_opt(t) for t in ts[:-1]] + [ts[-1]]
        out: T2Type | None = ts[0]
        for t in ts[1:]:
            out = T.join(out, t) if out is not None else None
        if out is None:
            word = "and" if isinstance(node.op, ast.And) else "or"
            return self.err(
                "L903",
                node,
                f"the operands of '{word}' have different types: " + ", ".join(str(t) for t in ts),
                "compare explicitly so that each operand is a Bool",
            )
        return out

    def _compare(self, node: ast.Compare, env: Env) -> T2Type:
        lt = self.expr(node.left, env)
        prev_t, prev_n = lt, node.left
        for op, comp in zip(node.ops, node.comparators, strict=True):
            rt = self.expr(comp, env)
            self._compare_pair(op, prev_t, rt, prev_n, comp, node)
            prev_t, prev_n = rt, comp
        return T.BOOL

    def _compare_pair(
        self, op: ast.cmpop, lt: T2Type, rt: T2Type, ln: ast.expr, rn: ast.expr, node: ast.AST
    ) -> None:
        if lt is UNK or rt is UNK:
            return
        if isinstance(op, ast.Is | ast.IsNot):
            none_side = any(isinstance(n, ast.Constant) and n.value is None for n in (ln, rn))
            if not none_side:
                self.err("L903", node, "'is' compares only with None", "compare values with ==")
            return
        if isinstance(op, ast.Eq | ast.NotEq):
            if T.NONE in (lt, rt) or T.join(T.strip_opt(lt), T.strip_opt(rt)) is not None:
                return
            self.err("L903", node, f"comparing {lt} with {rt} is always unequal")
            return
        if isinstance(op, ast.In | ast.NotIn):
            if isinstance(rt, T.Opt):
                self._maybe_none(rn)
                return
            if rt == T.STR:
                if lt != T.STR:
                    self.err("L903", node, f"'in' a Str needs a Str, not {lt}")
                return
            el = T.elem_of(rt)
            if el is None or isinstance(rt, T.Prim):
                self.err("L903", node, f"'in' needs a container, not {rt}")
                return
            if T.join(T.strip_opt(lt), T.strip_opt(el)) is None and T.NONE not in (lt, el):
                self.err("L903", node, f"a {lt} is never in a container of {el}")
            return
        for t, n in ((lt, ln), (rt, rn)):
            if isinstance(t, T.Opt):
                self._maybe_none(n)
                return
        if not (T.is_ordered(lt) and T.is_ordered(rt) and T.join(lt, rt) is not None):
            self.err(
                "L903",
                node,
                f"{lt} and {rt} cannot be ordered",
                "order numbers, strings, dates, times or durations of the same kind",
            )

    # ---- literals and comprehensions -------------------------------------------------

    def _literal(self, node: ast.expr, env: Env, expected: T2Type | None) -> T2Type:
        exp = T.strip_opt(expected) if expected is not None else None
        json = exp == T.JSON
        match node:
            case ast.Tuple(elts=elts):
                exps: list[T2Type | None] = (
                    list(exp.elems)
                    if isinstance(exp, T.TupleT) and len(exp.elems) == len(elts)
                    else [T.JSON if json else None] * len(elts)
                )
                ts = [self._elt(e, env, x) for e, x in zip(elts, exps, strict=True)]
                return T.TupleT(tuple(ts))
            case ast.List(elts=elts) | ast.Set(elts=elts):
                is_list = isinstance(node, ast.List)
                want = T.ListT if is_list else T.SetT
                if not elts:
                    if isinstance(exp, want):
                        return exp
                    return self._empty(node, "[]" if is_list else "set()", expected)
                el_exp = exp.elem if isinstance(exp, want) else (T.JSON if json else None)
                ts = [self._elt(e, env, el_exp) for e in elts]
                el = self._join_elems(node, ts, json, "elements")
                if el is None:
                    return T.JSON
                if not is_list and not T.is_hashable(el):
                    self.err("L923", node, f"set elements must be hashable, not {el}")
                if el_exp is not None and T.assignable(el, el_exp) and el_exp != T.JSON:
                    el = el_exp
                return want(el)
            case ast.Dict(keys=keys, values=values):
                if not keys:
                    if isinstance(exp, T.DictT | T.CounterT):
                        return exp
                    return self._empty(node, "{}", expected)
                kx = exp.key if isinstance(exp, T.DictT) else None
                vx = exp.val if isinstance(exp, T.DictT) else (T.JSON if json else None)
                kts: list[T2Type] = []
                vts: list[T2Type] = []
                for k, v in zip(keys, values, strict=True):
                    if k is None:  # {**d}
                        dt = self.expr(v, env, exp)
                        if isinstance(dt, T.DictT):
                            kts.append(dt.key)
                            vts.append(dt.val)
                        elif dt is not UNK:
                            self.err("L903", v, f"'**' needs a dict, not {dt}")
                        continue
                    kts.append(self.expr(k, env, kx))
                    vts.append(self.expr(v, env, vx))
                kt = self._join_elems(node, kts, False, "keys") if kts else UNK
                vt = self._join_elems(node, vts, json, "values") if vts else UNK
                if kt is None or vt is None:
                    return T.JSON
                if not T.is_hashable(kt):
                    self.err("L923", node, f"dict keys must be hashable, not {kt}")
                if vx is not None and T.assignable(vt, vx) and vx != T.JSON:
                    vt = vx
                if kx is not None and T.assignable(kt, kx):
                    kt = kx
                return T.DictT(kt, vt)
        return UNK  # pragma: no cover

    def _elt(self, e: ast.expr, env: Env, expected: T2Type | None) -> T2Type:
        if isinstance(e, ast.Starred):
            return self.err("L147", e, "starred elements are not allowed", "use + to concatenate")
        return self.expr(e, env, expected)

    def _join_elems(self, node: ast.AST, ts: list[T2Type], json: bool, what: str) -> T2Type | None:
        """The common type of literal elements; None means "Json" (mixed values
        where Json is expected); UNK after an error."""
        out: T2Type | None = ts[0] if ts else UNK
        for t in ts[1:]:
            out = T.join(out, t) if out is not None else None
        if out is not None:
            return out
        if json and all(T.assignable(t, T.JSON) for t in ts):
            return None
        shown = ", ".join(dict.fromkeys(str(t) for t in ts))
        self.err(
            "L903",
            node,
            f"the {what} have different types: {shown}",
            "use a tuple or a @record for mixed values",
        )
        return UNK

    def _empty(self, node: ast.AST, text: str, expected: T2Type | None = None) -> T2Type:
        if expected is UNK:
            return UNK  # the annotation already produced a diagnostic
        return self.err(
            "L911",
            node,
            f"cannot infer the element type of {text}",
            "annotate the variable, e.g. 'xs: list[Int] = []'",
        )

    def _comprehension(self, node: ast.expr, env: Env) -> T2Type:
        scope = env.copy()
        assert isinstance(node, _COMPS)
        for gen in node.generators:
            if gen.is_async:
                return self.err("L131", node, "async comprehensions are not allowed")
            it = self.expr(gen.iter, scope)
            el = self._elem(it, gen.iter)
            self._bind_scoped(gen.target, el, scope)
            for cond in gen.ifs:
                self.expr(cond, scope)
                then_env, _ = self.narrow(cond, scope)
                scope = then_env if then_env is not None else scope
        match node:
            case ast.ListComp(elt=elt):
                return T.ListT(self.expr(elt, scope))
            case ast.GeneratorExp(elt=elt):
                return T.IterT(self.expr(elt, scope))
            case ast.SetComp(elt=elt):
                el = self.expr(elt, scope)
                if not T.is_hashable(el):
                    self.err("L923", elt, f"set elements must be hashable, not {el}")
                return T.SetT(el)
            case ast.DictComp(key=key, value=value):
                kt = self.expr(key, scope)
                if not T.is_hashable(kt):
                    self.err("L923", key, f"dict keys must be hashable, not {kt}")
                return T.DictT(kt, self.expr(value, scope))
        return UNK  # pragma: no cover

    def _bind_scoped(self, tgt: ast.expr, t: T2Type, scope: Env) -> None:
        """Bind a comprehension / lambda target in its own scope (no declared type)."""
        match tgt:
            case ast.Name(id=name):
                scope.types[name] = t
                scope.maybe.discard(name)
                scope.drop_paths(name)
                self.types[id(tgt)] = t
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                for sub, st in zip(elts, self._unpack(tgt, t, len(elts)), strict=True):
                    self._bind_scoped(sub, st, scope)
                self.types[id(tgt)] = t
            case _:
                self.err("L147", tgt, f"cannot bind '{ast.unparse(tgt)}'")

    # ---- calls ------------------------------------------------------------------------

    def _call(self, node: ast.Call, env: Env, expected: T2Type | None) -> T2Type:
        for a in node.args:
            if isinstance(a, ast.Starred):
                return self.err("L147", a, "starred arguments are not allowed", "pass each one")
        for kw in node.keywords:
            if kw.arg is None:
                return self.err("L147", kw.value, "'**' arguments are not allowed")
        f = node.func
        g = self._is_global(f, env)
        if g is not None:
            return self._call_global(node, g, env, expected)
        if isinstance(f, ast.Attribute):
            if f.attr.startswith("__") and f.attr.endswith("__"):
                return self.err("L141", f, f"'.{f.attr}' is reflection, not allowed")
            base = self._is_global(f.value, env)
            if base == NATIVE_NS:
                self.types[id(f.value)] = T.Named("native")
                self.types[id(f)] = UNK
                return self._call_native(node, f.attr, env)
            mod = self.info.imports.get(base) if base is not None else None
            if mod is not None and "." not in mod:
                self.types[id(f.value)] = T.Named("module")
                self.types[id(f)] = UNK
                entry = T2_STDLIB[mod].get(f.attr)
                if entry is None:
                    return self._not_allowlisted(f, mod, f.attr)
                if isinstance(entry, str):
                    return self.err("L912", node, f"'{mod}.{f.attr}' is not callable")
                return self._std(node, f"{mod}.{f.attr}", entry, env, expected)
            recv = self.expr(f.value, env)
            self.types[id(f)] = UNK
            return self._call_method(node, recv, f.attr, env, expected)
        ft = self.expr(f, env)
        if ft is UNK:
            return self._type_args_silently(node, env)
        return self.err("L912", node, f"a {ft} is not callable")

    def _type_args_silently(self, node: ast.Call, env: Env) -> T2Type:
        """Arguments of a call that already failed: still checked, never cascading."""
        for a in [*node.args, *(k.value for k in node.keywords)]:
            if not isinstance(a, ast.Lambda) and not _empty_kind(a):
                self._arg(a, env, None)
        return UNK

    def _arg(self, a: ast.expr, env: Env, expected: T2Type | None) -> T2Type:
        if isinstance(a, ast.GeneratorExp):
            t = self._comprehension(a, env)
            self.types[id(a)] = t
            return t
        return self.expr(a, env, expected)

    def _call_global(self, node: ast.Call, name: str, env: Env, expected: T2Type | None) -> T2Type:
        info = self.info
        self.types[id(node.func)] = UNK
        if name in T2_IO_CALLS:
            return self.err(
                "L140",
                node,
                f"'{name}()' is not allowed: procedures do no I/O and run no code",
                "compute a value and return it; the endpoint does the I/O",
            )
        if name in T2_REFLECTION_CALLS:
            hint = (
                "types are static; test 'x is None' for optional values"
                if name in ("isinstance", "type", "issubclass")
                else "use the value's fields directly"
            )
            return self.err("L141", node, f"'{name}()' is reflection, not allowed", hint)
        if name in info.sigs:
            return self._call_proc(node, name, env)
        if name in info.records:
            return self._construct(node, info.records[name], env)
        imported = info.imports.get(name)
        if imported is not None:
            if "." not in imported:
                return self.err("L912", node, f"module '{name}' is not callable")
            mod, attr = imported.split(".", 1)
            entry = T2_STDLIB[mod][attr]
            if isinstance(entry, str):
                return self.err("L912", node, f"'{name}' is not callable")
            return self._std(node, imported, entry, env, expected)
        if name in T2_BUILTINS:
            return self.apply(node, T2_BUILTINS[name], {}, env, expected, name, f"builtin:{name}")
        if name in T2_HELPERS:
            return self.apply(node, T2_HELPERS[name], {}, env, expected, name, f"helper:{name}")
        if name in info.constants:
            return self.err("L912", node, f"constant '{name}' is not callable")
        if name in T2_ERRORS:
            return self.err(
                "L912",
                node,
                f"'{name}' cannot be built in a procedure",
                "errors are only named in except clauses",
            )
        if name == NATIVE_NS:
            return self.err("L912", node, "call a native as native.<name>(...)")
        self._type_args_silently(node, env)
        known = [*info.sigs, *info.records, *T2_BUILTINS, *T2_HELPERS, *info.imports]
        close = difflib.get_close_matches(name, known, n=1)
        return self.err(
            "L900",
            node.func,
            f"unknown function '{name}'",
            f"did you mean '{close[0]}'?"
            if close
            else "call a @procedure, a record, a builtin or an allowlisted function",
        )

    def _std(
        self,
        node: ast.Call,
        qual: str,
        overloads: tuple[StdSig, ...],
        env: Env,
        expected: T2Type | None,
    ) -> T2Type:
        if qual == "collections.defaultdict":
            return self._defaultdict(node, env, expected)
        label = qual.rsplit(".", 1)[-1] if qual.startswith("collections.") else qual
        return self.apply(node, overloads, {}, env, expected, label, f"std:{qual}")

    def _defaultdict(self, node: ast.Call, env: Env, expected: T2Type | None) -> T2Type:
        self.targets[id(node)] = "std:collections.defaultdict"
        factories = ", ".join(sorted(T2_DEFAULTDICT_FACTORIES))
        match node:
            case ast.Call(args=[ast.Name(id=fac) as arg], keywords=[]) if (
                fac in T2_DEFAULTDICT_FACTORIES
            ):
                self.types[id(arg)] = T.Named("factory")
            case _:
                self._type_args_silently(node, env)
                return self.err("L904", node, f"defaultdict takes one factory: {factories}")
        exp = T.strip_opt(expected) if expected is not None else None
        if exp is UNK:
            return UNK
        if not isinstance(exp, T.DictT):
            return self.err(
                "L911",
                node,
                "cannot infer the key and value types of defaultdict(...)",
                f"annotate it: 'd: dict[Str, list[Int]] = defaultdict({fac})'",
            )
        ok = {
            "list": isinstance(exp.val, T.ListT),
            "set": isinstance(exp.val, T.SetT),
            "int": exp.val == T.INT,
            "float": exp.val == T.FLOAT,
            "str": exp.val == T.STR,
        }[fac]
        if not ok:
            return self.err("L904", node, f"defaultdict({fac}) does not make {exp.val} values")
        return exp

    def _call_proc(self, node: ast.Call, name: str, env: Env) -> T2Type:
        sig = self.info.sigs[name]
        self.calls.add(name)
        self.targets[id(node)] = f"proc:{name}"
        pairs = self._bind_args(node, [n for n, _ in sig.params], set(), f"'{name}'")
        if pairs is None:
            self._type_args_silently(node, env)
        else:
            want = dict(sig.params)
            for pname, a in pairs:
                self._check_arg(a, env, want[pname], f"'{name}' parameter '{pname}'")
        missing = sig.effects - self.sig.effects
        if missing:
            shown = ", ".join(sorted(missing))
            if not self.sig.effects:
                msg = f"pure procedure '{self.sig.name}' calls '{name}', which has effects {shown}"
            else:
                msg = (
                    f"'{self.sig.name}' calls '{name}', which needs effects it does not "
                    f"declare: {shown}"
                )
            want_all = ", ".join(f'"{e}"' for e in sorted(self.sig.effects | sig.effects))
            self.err("L922", node, msg, f"declare them: @procedure(effects=[{want_all}])")
        return sig.returns

    def _bind_args(
        self, node: ast.Call, names: list[str], optional: set[str], label: str
    ) -> list[tuple[str, ast.expr]] | None:
        """Positional + keyword arguments -> (parameter, argument) pairs; L904 on a
        shape error (then every argument is still typed, silently)."""
        pairs: list[tuple[str, ast.expr]] = []
        problem = None
        if len(node.args) > len(names):
            problem = f"{label} takes {len(names)} argument(s), got {len(node.args)}"
        else:
            pairs = list(zip(names, node.args, strict=False))
            given = {n for n, _ in pairs}
            for kw in node.keywords:
                assert kw.arg is not None
                if kw.arg not in names:
                    problem = f"{label} has no parameter '{kw.arg}'"
                    break
                if kw.arg in given:
                    problem = f"{label} got '{kw.arg}' twice"
                    break
                given.add(kw.arg)
                pairs.append((kw.arg, kw.value))
            else:
                missing = [n for n in names if n not in given and n not in optional]
                if missing:
                    problem = f"{label} is missing {', '.join(missing)}"
        if problem is not None:
            self.err("L904", node, problem, f"expected: {', '.join(names)}")
            return None
        return pairs

    def _check_arg(self, a: ast.expr, env: Env, want: T2Type, what: str) -> None:
        vt = self._arg(a, env, want)
        if not T.assignable(vt, want):
            self._mismatch("L904", a, vt, want, f"{what} is {want}, got {vt}", None)

    def _construct(self, node: ast.Call, rec: RecordDef, env: Env) -> T2Type:
        self.targets[id(node)] = f"record:{rec.name}"
        names = [n for n, _ in rec.fields]
        pairs = self._bind_args(node, names, set(rec.defaults), f"record {rec.name}")
        if pairs is None:
            self._type_args_silently(node, env)
        else:
            want = dict(rec.fields)
            for fname, a in pairs:
                self._check_arg(a, env, want[fname], f"field {rec.name}.{fname}")
        return T.RecordT(rec.name)

    def _call_native(self, node: ast.Call, name: str, env: Env) -> T2Type:
        api = self.info.api
        sig = api.natives.get(name)
        if sig is None:
            self._type_args_silently(node, env)
            natives = ", ".join(sorted(api.natives)) or "(none)"
            return self.err(
                "L905", node.func, f"unknown native 'native.{name}'", f"natives: {natives}"
            )
        self.natives.add(name)
        self.targets[id(node)] = f"native:{name}"
        if node.keywords or len(node.args) != len(sig.params):
            self._type_args_silently(node, env)
            shown = ", ".join(p.value for p in sig.params)
            self.err(
                "L904",
                node,
                f"native.{name} takes {len(sig.params)} positional argument(s)",
                f"native.{name}({shown})",
            )
        else:
            for i, (a, p) in enumerate(zip(node.args, sig.params, strict=True)):
                self._check_arg(a, env, prim(p), f"native.{name} argument {i + 1}")
        if sig.effect:
            cap = sig.capability or name
            if cap not in self.sig.effects:
                self.err(
                    "L921",
                    node,
                    f"native.{name} is an effect (capability '{cap}') and "
                    f"'{self.sig.name}' does not declare it",
                    f'declare it: @procedure(effects=["{cap}"])',
                )
            return T.NONE
        assert sig.result is not None
        t = prim(sig.result)
        return T.opt(t) if sig.nullable else t

    def _call_method(
        self, node: ast.Call, recv: T2Type, name: str, env: Env, expected: T2Type | None
    ) -> T2Type:
        if recv is UNK:
            return self._type_args_silently(node, env)
        f = node.func
        assert isinstance(f, ast.Attribute)
        if isinstance(recv, T.Opt):
            self._type_args_silently(node, env)
            return self._maybe_none(f.value)
        kind = kind_of(recv)
        methods = dict(T2_METHODS.get(kind or "", {}))
        if kind == "Counter":
            methods = {**T2_METHODS["dict"], **methods}
        overloads = methods.get(name)
        if overloads is None:
            self._type_args_silently(node, env)
            attrs = T2_ATTRS.get(kind or "", {})
            if name in attrs:
                return self.err("L912", node, f"'.{name}' is an attribute, not a method")
            close = difflib.get_close_matches(name, list(methods), n=1)
            return self.err(
                "L905",
                f,
                f"a {recv} has no method '{name}'",
                f"did you mean '{close[0]}'?" if close else None,
            )
        b: dict[str, T2Type] = {}
        match recv:
            case T.ListT(elem=e) | T.SetT(elem=e):
                b = {"T": e}
            case T.DictT(key=k, val=v):
                b = {"K": k, "V": v}
            case T.CounterT(key=k):
                b = {"T": k, "K": k, "V": T.INT}
        owner = "dict" if kind == "Counter" and name not in T2_METHODS["Counter"] else kind
        res = self.apply(node, overloads, b, env, expected, f".{name}", f"method:{owner}.{name}")
        if any(s.mutates for s in overloads):
            self.mutations.append((node, f.value))
        return res

    # ---- overload resolution ------------------------------------------------------------

    def apply(
        self,
        node: ast.Call,
        overloads: tuple[StdSig, ...],
        fixed: Mapping[str, T2Type],
        env: Env,
        expected: T2Type | None,
        label: str,
        target: str,
    ) -> T2Type:
        self.targets[id(node)] = target
        single = overloads[0] if len(overloads) == 1 else None

        def concrete(p: str) -> T2Type | None:
            t = sig_type(p)
            return t if _is_concrete(t) else None

        arg_types: list[T2Type | None] = []
        for i, a in enumerate(node.args):
            if isinstance(a, ast.Lambda) or _empty_kind(a):
                arg_types.append(None)
                continue
            exp = None
            if single is not None:
                ps = [*single.params, *single.optional]
                exp = concrete(ps[i]) if i < len(ps) else None
            arg_types.append(self._arg(a, env, exp))
        kw_types: dict[str, T2Type | None] = {}
        for kw in node.keywords:
            assert kw.arg is not None
            if isinstance(kw.value, ast.Lambda) or _empty_kind(kw.value):
                kw_types[kw.arg] = None
            else:
                exp = None
                if single is not None and kw.arg in single.kwargs:
                    exp = concrete(single.kwargs[kw.arg])
                kw_types[kw.arg] = self._arg(kw.value, env, exp)

        chosen: StdSig | None = None
        bindings = T.Bindings(dict(fixed))
        for sig in overloads:
            b = T.Bindings(dict(fixed))
            if _fits(sig, arg_types, kw_types, b, frozenset(fixed)):
                chosen, bindings = sig, b
                break
        if chosen is None:
            stripped = [T.strip_opt(t) if t is not None else None for t in arg_types]
            for sig in overloads:
                if _fits(sig, stripped, kw_types, T.Bindings(dict(fixed)), frozenset(fixed)):
                    i = next(i for i, t in enumerate(arg_types) if isinstance(t, T.Opt))
                    return self._maybe_none(node.args[i])
            shown = ", ".join(
                "?" if t is None else str(t) for t in [*arg_types, *kw_types.values()]
            )
            sigs = " | ".join(_render_sig(label, s) for s in overloads)
            return self.err(
                "L904", node, f"{label}() does not accept ({shown})", f"signature: {sigs}"
            )

        # deferred arguments: empty containers and key= lambdas
        params = [*chosen.params, *chosen.optional]
        for i, a in enumerate(node.args):
            if arg_types[i] is None:
                self._deferred(a, sig_type(params[i]), bindings, env)
        for kw in node.keywords:
            if kw_types.get(kw.arg or "") is None:
                self._deferred(kw.value, sig_type(chosen.kwargs[kw.arg or ""]), bindings, env)

        for i in chosen.literal:
            self._regex_literal(node.args[i] if i < len(node.args) else node, label)
        grouped = _regex_result(target, node)
        if grouped is not None:
            return grouped

        if target in _ORDERING and "key" not in kw_types:
            el = bindings.get("T")
            if el is not None and not T.is_ordered(el):
                self.err(
                    "L903",
                    node,
                    f"{el} values cannot be ordered",
                    "pass key=lambda x: <an orderable value>",
                )

        res_pat = sig_type(chosen.result)
        if expected is not None:
            trial = T.Bindings(dict(bindings.vars))
            if T.match(res_pat, T.strip_opt(expected), trial):
                for k, v in trial.vars.items():
                    bindings.vars.setdefault(k, v)
        unresolved: list[str] = []

        def missing(name: str) -> T2Type:
            unresolved.append(name)
            return UNK

        res = T.substitute(res_pat, bindings, missing)
        if unresolved:
            self._empty(node, f"{label}()", expected)
            return UNK if isinstance(res_pat, T.TypeVar) else res
        return res

    def _deferred(self, a: ast.expr, pattern: T2Type, b: T.Bindings, env: Env) -> None:
        if isinstance(pattern, T.Pseudo) and pattern.name == "Key":
            self._key_lambda(a, T.substitute(pattern.arg, b, lambda _: UNK), env)
            return
        if isinstance(a, ast.Lambda):
            self.err("L147", a, "lambda is only allowed as key= of sorted/min/max/list.sort")
            return
        want = T.substitute(pattern, b, lambda _: UNK)
        if isinstance(want, T.Pseudo) and want.name == "Iterable":
            want = T.ListT(want.arg)
        if not _is_concrete(want) or T.has_unk(want):
            self.expr(a, env)  # reports L911
            return
        vt = self.expr(a, env, want)
        if not T.assignable(vt, want):
            self.err("L904", a, f"expected {want}, got {vt}")

    def _key_lambda(self, a: ast.expr, el: T2Type, env: Env) -> None:
        if not isinstance(a, ast.Lambda):
            self.expr(a, env)
            self.err("L147", a, "key= takes a lambda", "write key=lambda x: <value to order by>")
            return
        args = a.args
        if (
            len(args.args) != 1
            or args.posonlyargs
            or args.kwonlyargs
            or args.vararg
            or args.kwarg
            or args.defaults
        ):
            self.err("L147", a, "a key lambda takes exactly one parameter", "key=lambda x: ...")
            self.types[id(a)] = UNK
            return
        scope = env.copy()
        pname = args.args[0].arg
        scope.types[pname] = el
        scope.maybe.discard(pname)
        scope.drop_paths(pname)
        bt = self.expr(a.body, scope)
        self.types[id(a)] = bt
        if not T.is_ordered(bt):
            self.err(
                "L903",
                a.body,
                f"a sort key must be orderable, not {bt}",
                "return a number, string, date, time, duration or a tuple of them",
            )

    def _regex_literal(self, node: ast.AST, label: str) -> None:
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            self.err(
                "L916",
                node,
                f"the pattern of {label}() must be a string literal",
                'write the regular expression inline, e.g. r"\\d+"',
            )
            return
        try:
            re.compile(node.value)
        except re.error as e:
            self.err("L916", node, f"invalid regular expression: {e}", "fix the pattern")
            return
        risk = redos_risk(node.value)
        if risk is not None:
            self.err(
                "L925",
                node,
                f"the pattern of {label}() may backtrack catastrophically on request input: {risk}",
                "make the repeated pieces disjoint, bound the repeat ({0,50}) or make it "
                "possessive (a++)",
            )
        problem = re2_problem(node.value)
        if problem is not None:
            self.err(
                "L926",
                node,
                f"the pattern of {label}(): {problem}",
                "rewrite without lookaround/backreferences, e.g. two matches or a split",
            )

    # ---- parameter immutability (L145) -------------------------------------------------

    def _check_mutations(self) -> None:
        flags: dict[str, tuple[bool, bool]] = {p: (True, True) for p in self.params}
        changed = True
        while changed:
            changed = False
            for name, value, elem in self.assigns:
                if name in self.params:
                    continue
                w, i = self._alias(value, flags)
                if elem:
                    w = i = w or i
                if not self._contains_mutable(self.declared.get(name, UNK)):
                    continue
                old = flags.get(name, (False, False))
                new = (old[0] or w, old[1] or i)
                if new != old:
                    flags[name] = new
                    changed = True
        for node, recv in self.mutations:
            whole, _ = self._alias(recv, flags)
            if not whole:
                continue
            root = _root_name(recv)
            if root in self.params:
                what = f"the parameter '{root}'"
            elif root is not None and root in self.info.constants and root not in self.local_names:
                what = f"the module constant '{root}'"
            else:
                what = "a value passed in"
            self.err(
                "L145",
                node,
                f"'{ast.unparse(recv)}' may be (part of) {what}: values passed in are immutable",
                "copy it into a new name first (e.g. ys = list(xs)) and change the copy, "
                "or build a new value",
            )

    def _alias(self, e: ast.AST, flags: Mapping[str, tuple[bool, bool]]) -> tuple[bool, bool]:
        """(may be a passed-in object itself, may contain passed-in objects)."""
        t = self.types.get(id(e), UNK)
        match e:
            case ast.Name(id=name):
                if name in flags:
                    return flags[name]
                if name in self.info.constants and name not in self.local_names:
                    return (
                        (True, True) if T.is_mutable(self.info.constants[name]) else (False, False)
                    )
                return False, False
            case ast.Subscript(value=v) | ast.Attribute(value=v):
                w, i = self._alias(v, flags)
                a = (w or i) and self._contains_mutable(t)
                return a, a
            case ast.Call(func=func, args=args, keywords=kws):
                parts: list[ast.AST] = [*args, *(k.value for k in kws)]
                if isinstance(func, ast.Attribute):
                    parts.append(func.value)
                any_alias = any(any(self._alias(p, flags)) for p in parts)
                if self.targets.get(id(e), "") in _COPIES:
                    return False, any_alias and self._inner_mutable(t)
                if self._contains_mutable(t):
                    return any_alias, any_alias
                return False, False
            case ast.List(elts=elts) | ast.Tuple(elts=elts) | ast.Set(elts=elts):
                a = any(any(self._alias(x, flags)) for x in elts)
                return False, a and self._inner_mutable(t)
            case ast.Dict(keys=keys, values=values):
                a = any(any(self._alias(x, flags)) for x in [*keys, *values] if x is not None)
                return False, a and self._inner_mutable(t)
            case ast.ListComp() | ast.SetComp() | ast.DictComp() | ast.GeneratorExp():
                a = any(any(self._alias(g.iter, flags)) for g in e.generators) or any(
                    any(self._alias(n, flags))
                    for n in ast.walk(e)
                    if isinstance(n, ast.Name) and n.id in flags
                )
                return False, a and self._inner_mutable(t)
            case ast.IfExp(body=body, orelse=orelse):
                a, b = self._alias(body, flags), self._alias(orelse, flags)
                return a[0] or b[0], a[1] or b[1]
            case ast.BoolOp(values=values):
                rs = [self._alias(v, flags) for v in values]
                return any(r[0] for r in rs), any(r[1] for r in rs)
            case ast.BinOp(left=left, right=right):
                a = any(self._alias(left, flags)) or any(self._alias(right, flags))
                return False, a and self._inner_mutable(t)
        return False, False

    def _contains_mutable(self, t: T2Type, seen: frozenset[str] = frozenset()) -> bool:
        match t:
            case T.ListT() | T.SetT() | T.DictT() | T.CounterT():
                return True
            case T.Opt(inner=i):
                return self._contains_mutable(i, seen)
            case T.IterT(elem=e):
                return self._contains_mutable(e, seen)
            case T.TupleT(elems=es):
                return any(self._contains_mutable(x, seen) for x in es)
            case T.RecordT(name=name) if name not in seen:
                rec = self.info.records.get(name)
                return rec is not None and any(
                    self._contains_mutable(ft, seen | {name}) for _, ft in rec.fields
                )
        return t is UNK

    def _inner_mutable(self, t: T2Type) -> bool:
        match t:
            case T.ListT(elem=e) | T.SetT(elem=e) | T.IterT(elem=e):
                return self._contains_mutable(e)
            case T.DictT(key=k, val=v):
                return self._contains_mutable(k) or self._contains_mutable(v)
            case T.CounterT(key=k):
                return self._contains_mutable(k)
        return self._contains_mutable(t)


def _root_name(e: ast.AST) -> str | None:
    while True:
        match e:
            case ast.Name(id=name):
                return name
            case ast.Subscript(value=v) | ast.Attribute(value=v):
                e = v
            case ast.Call(func=ast.Attribute(value=v)):
                e = v
            case _:
                return None


def _regex_result(target: str, node: ast.Call) -> T2Type | None:
    """The result of `re.findall` / `re.split` follows the literal pattern's capturing
    groups (R3/R9, docs/lattice-language-rules.md §6): findall of k >= 2 groups gives
    tuples of k Strs; split by a pattern with groups gives the groups too, None for
    one that did not participate. None: the catalog's `list[Str]` holds."""
    if target not in ("std:re.findall", "std:re.split") or not node.args:
        return None
    match node.args[0]:
        case ast.Constant(value=str() as pattern):
            try:
                groups = re.compile(pattern).groups
            except re.error:
                return None  # L916 reported
        case _:
            return None
    if target == "std:re.findall" and groups >= 2:
        return T.ListT(T.TupleT((T.STR,) * groups))
    if target == "std:re.split" and groups >= 1:
        return T.ListT(T.opt(T.STR))
    return None


def _is_concrete(t: T2Type) -> bool:
    match t:
        case T.TypeVar() | T.Pseudo():
            return False
        case T.Opt(inner=i):
            return _is_concrete(i)
        case T.ListT(elem=e) | T.SetT(elem=e) | T.IterT(elem=e):
            return _is_concrete(e)
        case T.CounterT(key=k):
            return _is_concrete(k)
        case T.DictT(key=k, val=v):
            return _is_concrete(k) and _is_concrete(v)
        case T.TupleT(elems=es):
            return all(_is_concrete(e) for e in es)
    return True


def _fits(
    sig: StdSig,
    args: Sequence[T2Type | None],
    kws: Mapping[str, T2Type | None],
    b: T.Bindings,
    fixed: frozenset[str],
) -> bool:
    n = len(args)
    if n < len(sig.params) or n > len(sig.params) + len(sig.optional):
        return False
    patterns = [*sig.params, *sig.optional][:n]
    for t, p in zip(args, patterns, strict=True):
        if t is not None and not _match(sig_type(p), t, b, fixed):
            return False
    for k, t in kws.items():
        if k not in sig.kwargs:
            return False
        if t is not None and not _match(sig_type(sig.kwargs[k]), t, b, fixed):
            return False
    return True


def _match(p: T2Type, t: T2Type, b: T.Bindings, fixed: frozenset[str]) -> bool:
    """match(), with receiver-bound type variables fixed (list[Int].append(1.5) fails)."""
    if isinstance(p, T.TypeVar) and p.name in fixed:
        return T.assignable(t, b.vars[p.name])
    if isinstance(p, T.Pseudo) and p.name == "Iterable" and isinstance(p.arg, T.TypeVar):
        if p.arg.name in fixed:
            el = T.elem_of(t)
            return el is not None and T.assignable(el, b.vars[p.arg.name])
    return T.match(p, t, b)


def _binop_result(op: ast.operator, lt: T2Type, rt: T2Type, right: ast.AST) -> T2Type | None:
    num = lt in T.NUMERIC and rt in T.NUMERIC
    both_int = lt == T.INT and rt == T.INT
    arith = T.INT if both_int else T.FLOAT
    D, DT, DA = T.DURATION, T.DATETIME, T.DATE
    match op:
        case ast.Add():
            if num:
                return arith
            if lt == rt and (lt == T.STR or lt == D):
                return lt
            if isinstance(lt, T.ListT) and isinstance(rt, T.ListT) and T.same(lt, rt):
                return lt
            if isinstance(lt, T.TupleT) and isinstance(rt, T.TupleT):
                return T.TupleT(lt.elems + rt.elems)
            if lt in (DT, DA) and rt == D:
                return lt
            if lt == D and rt in (DT, DA):
                return rt
        case ast.Sub():
            if num:
                return arith
            if lt == rt and lt in (DT, DA, D):
                return D
            if lt in (DT, DA) and rt == D:
                return lt
            if isinstance(lt, T.SetT) and isinstance(rt, T.SetT) and T.same(lt, rt):
                return lt
        case ast.Mult():
            if num:
                return arith
            if (lt == T.STR and rt == T.INT) or (lt == T.INT and rt == T.STR):
                return T.STR
            if isinstance(lt, T.ListT) and rt == T.INT:
                return lt
            if lt == D and rt in T.NUMERIC:
                return D
            if lt in T.NUMERIC and rt == D:
                return D
        case ast.Div():
            if num:
                return T.FLOAT
            if lt == D and rt in T.NUMERIC:
                return D
            if lt == D and rt == D:
                return T.FLOAT
        case ast.FloorDiv():
            if num:
                return arith
            if lt == D and rt == D:
                return T.INT
            if lt == D and rt == T.INT:
                return D
        case ast.Mod():
            if num:
                return arith
            if lt == D and rt == D:
                return D
        case ast.Pow():
            if num:
                v = literal_value(right)
                if both_int and isinstance(v, int) and v >= 0:
                    return T.INT
                return T.FLOAT
        case ast.BitAnd() | ast.BitOr() | ast.BitXor():
            if both_int or (lt == T.BOOL and rt == T.BOOL):
                return lt
            if isinstance(lt, T.SetT) and isinstance(rt, T.SetT) and T.same(lt, rt):
                return lt
            if isinstance(op, ast.BitOr) and isinstance(lt, T.DictT) and T.same(lt, rt):
                return lt
        case ast.LShift() | ast.RShift():
            if both_int:
                return T.INT
    return None
