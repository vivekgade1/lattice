"""The T2 type language: values of procedures, and the rules relating them.

    Int Float Str Bool Date DateTime Duration Uuid Json None
    T | None                 Opt(T)
    list[T] set[T] dict[K,V] tuple[A, B, ...]
    Row[Schema]              RowT(schema, private=False)
    Row(Schema, private=True)
    <record name>            RecordT(name)
    Caller                   CALLER

Internal forms: `Iter[T]` (a one-shot iterable: range, zip, dict views, generators),
`Counter[T]` (a dict[T, Int] with most_common), `Match` (re.Match), `Error` (the value
bound by `except E as e`) and `UNK` (an expression that already produced a diagnostic;
compatible with everything so that one mistake yields one diagnostic).
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from lattice.catalog import (
    CALLER_TYPE,
    ROW_PRIVATE,
    ROW_TYPE,
    T2_GENERICS,
    T2_TYPE_ALIASES,
    T2_TYPE_HINTS,
    T2_TYPE_NAMES,
)


class T2Type:
    """Base of every T2 type."""

    def render(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def __str__(self) -> str:
        return self.render()


@dataclass(frozen=True)
class Prim(T2Type):
    name: str

    def render(self) -> str:
        return self.name


INT, FLOAT, STR, BOOL = Prim("Int"), Prim("Float"), Prim("Str"), Prim("Bool")
DATE, DATETIME, DURATION, UUID = Prim("Date"), Prim("DateTime"), Prim("Duration"), Prim("Uuid")
JSON, NONE = Prim("Json"), Prim("None")
PRIMS: Mapping[str, Prim] = {
    p.name: p for p in (INT, FLOAT, STR, BOOL, DATE, DATETIME, DURATION, UUID, JSON, NONE)
}


@dataclass(frozen=True)
class Opt(T2Type):
    """T | None (inner is never None, Opt or UNK: build with `opt`)."""

    inner: T2Type

    def render(self) -> str:
        return f"{self.inner.render()} | None"


@dataclass(frozen=True)
class ListT(T2Type):
    elem: T2Type

    def render(self) -> str:
        return f"list[{self.elem.render()}]"


@dataclass(frozen=True)
class SetT(T2Type):
    elem: T2Type

    def render(self) -> str:
        return f"set[{self.elem.render()}]"


@dataclass(frozen=True)
class DictT(T2Type):
    key: T2Type
    val: T2Type

    def render(self) -> str:
        return f"dict[{self.key.render()}, {self.val.render()}]"


@dataclass(frozen=True)
class CounterT(T2Type):
    key: T2Type

    def render(self) -> str:
        return f"Counter[{self.key.render()}]"


@dataclass(frozen=True)
class TupleT(T2Type):
    elems: tuple[T2Type, ...]

    def render(self) -> str:
        return "tuple[" + ", ".join(e.render() for e in self.elems) + "]"


@dataclass(frozen=True)
class IterT(T2Type):
    elem: T2Type

    def render(self) -> str:
        return f"Iter[{self.elem.render()}]"


@dataclass(frozen=True)
class RowT(T2Type):
    table: str
    private: bool = False

    def render(self) -> str:
        if self.private:
            return f"{ROW_TYPE}({self.table}, {ROW_PRIVATE}=True)"
        return f"{ROW_TYPE}[{self.table}]"


@dataclass(frozen=True)
class RecordT(T2Type):
    name: str

    def render(self) -> str:
        return self.name


@dataclass(frozen=True)
class Named(T2Type):
    """Caller, Match, Error: opaque named types with attributes/methods."""

    name: str

    def render(self) -> str:
        return self.name


CALLER, MATCH, ERROR = Named(CALLER_TYPE), Named("Match"), Named("Error")


@dataclass(frozen=True)
class TypeVar(T2Type):
    """Signature-only: a type variable, or a pseudo-type (Num, Sized, Any, Factory)."""

    name: str

    def render(self) -> str:
        return self.name


@dataclass(frozen=True)
class Pseudo(T2Type):
    """Signature-only: Iterable[T], Key[T]."""

    name: str
    arg: T2Type

    def render(self) -> str:
        return f"{self.name}[{self.arg.render()}]"


class _Unknown(T2Type):
    _instance: _Unknown | None = None

    def __new__(cls) -> _Unknown:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def render(self) -> str:
        return "?"

    def __repr__(self) -> str:
        return "UNK"


UNK = _Unknown()


def opt(t: T2Type) -> T2Type:
    if t is UNK or t == NONE or isinstance(t, Opt):
        return t
    return Opt(t)


def strip_opt(t: T2Type) -> T2Type:
    return t.inner if isinstance(t, Opt) else t


# ---- relations --------------------------------------------------------------------


def assignable(src: T2Type, dst: T2Type) -> bool:
    """A value of `src` may be stored where `dst` is expected: equal types, Int -> Float
    widening, T and None -> T | None, a private row as a public one, a Counter as a
    dict[K, Int], tuples element-wise. Mutable containers are invariant."""
    if src is UNK or dst is UNK or src == dst:
        return True
    if src == INT and dst == FLOAT:
        return True
    if dst == JSON:
        return is_jsonable(src)
    if isinstance(dst, Opt):
        if src == NONE:
            return True
        return assignable(strip_opt(src), dst.inner)
    match src, dst:
        case RowT(), RowT():
            return src.table == dst.table and (src.private or not dst.private)
        case CounterT(), DictT():
            return same(src.key, dst.key) and same(INT, dst.val)
        case TupleT(), TupleT():
            return len(src.elems) == len(dst.elems) and all(
                assignable(a, b) for a, b in zip(src.elems, dst.elems, strict=True)
            )
        case IterT(), IterT():
            return assignable(src.elem, dst.elem)
        case ListT(), ListT():
            return same(src.elem, dst.elem)
        case SetT(), SetT():
            return same(src.elem, dst.elem)
        case DictT(), DictT():
            return same(src.key, dst.key) and same(src.val, dst.val)
    return False


def is_jsonable(t: T2Type) -> bool:
    """Values json.dumps writes: numbers, text, bools, None, Json, and lists, tuples
    and Str-keyed dicts of those."""
    match t:
        case Opt(inner=i):
            return is_jsonable(i)
        case ListT(elem=e):
            return is_jsonable(e)
        case TupleT(elems=es):
            return all(is_jsonable(e) for e in es)
        case DictT(key=k, val=v):
            return k == STR and is_jsonable(v)
    return t is UNK or t in (INT, FLOAT, STR, BOOL, NONE, JSON)


def same(a: T2Type, b: T2Type) -> bool:
    """Equal up to UNK (invariant positions)."""
    if a is UNK or b is UNK or a == b:
        return True
    match a, b:
        case Opt(), Opt():
            return same(a.inner, b.inner)
        case ListT(), ListT() | SetT(), SetT() | IterT(), IterT():
            return same(a.elem, b.elem)  # type: ignore[union-attr]
        case DictT(), DictT():
            return same(a.key, b.key) and same(a.val, b.val)
        case TupleT(), TupleT():
            return len(a.elems) == len(b.elems) and all(
                same(x, y) for x, y in zip(a.elems, b.elems, strict=True)
            )
    return False


def join(a: T2Type, b: T2Type) -> T2Type | None:
    """The common type of two values (branches of an if-expression, list elements):
    one assignable to the other, or T and None -> T | None. None if incompatible."""
    if a is UNK:
        return b
    if b is UNK:
        return a
    if assignable(a, b):
        return b
    if assignable(b, a):
        return a
    if a == NONE:
        return opt(b)
    if b == NONE:
        return opt(a)
    if isinstance(a, Opt) or isinstance(b, Opt):
        j = join(strip_opt(a), strip_opt(b))
        return opt(j) if j is not None else None
    return None


NUMERIC = frozenset({INT, FLOAT})
_ORDERED = frozenset({INT, FLOAT, STR, BOOL, DATE, DATETIME, DURATION})


def is_ordered(t: T2Type) -> bool:
    if t is UNK or t in _ORDERED:
        return True
    return isinstance(t, TupleT) and all(is_ordered(e) for e in t.elems)


def is_hashable(t: T2Type) -> bool:
    match t:
        case Prim():
            return t != JSON
        case Opt():
            return is_hashable(t.inner)
        case TupleT():
            return all(is_hashable(e) for e in t.elems)
        case RecordT() | RowT():
            return True
    return t is UNK


def is_mutable(t: T2Type) -> bool:
    return isinstance(t, ListT | SetT | DictT | CounterT)


def elem_of(t: T2Type) -> T2Type | None:
    """The element type iterating a value of `t` yields (None: not iterable)."""
    match t:
        case ListT() | SetT() | IterT():
            return t.elem
        case DictT() | CounterT():
            return t.key
        case TupleT() if t.elems and all(e == t.elems[0] for e in t.elems):
            return t.elems[0]
        case TupleT():
            j: T2Type | None = t.elems[0] if t.elems else UNK
            for e in t.elems[1:]:
                j = join(j, e) if j is not None else None
            return j
    if t == STR:
        return STR
    if t is UNK:
        return UNK
    return None


def is_sized(t: T2Type) -> bool:
    return t is UNK or t == STR or isinstance(t, ListT | SetT | DictT | CounterT | TupleT)


# ---- annotations and signature strings ------------------------------------------------

_SIG_VARS = frozenset({"T", "U", "W", "K", "V", "Num", "Sized", "Any", "Factory"})
_PSEUDO = frozenset({"Iterable", "Key", "Iter", "Counter"})


class AnnotationError(Exception):
    def __init__(self, node: ast.AST, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.node = node
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class TypeEnv:
    """What annotation names resolve to: the API tables (Row[...]) and the records."""

    tables: frozenset[str]
    records: frozenset[str]


def parse_annotation(node: ast.expr, env: TypeEnv) -> T2Type:
    """An annotation expression -> a T2 type; raises AnnotationError (L902)."""
    return _Parser(env, sig=False).parse(node)


def parse_sig(text: str) -> T2Type:
    """A catalog signature string -> a T2 type (with type variables / pseudo-types)."""
    return _Parser(TypeEnv(frozenset(), frozenset()), sig=True).parse(
        ast.parse(text, mode="eval").body
    )


class _Parser:
    def __init__(self, env: TypeEnv, sig: bool) -> None:
        self.env = env
        self.sig = sig

    def parse(self, node: ast.expr) -> T2Type:
        match node:
            case ast.Constant(value=None):
                return NONE
            case ast.Name(id=name):
                return self._name(node, name)
            case ast.BinOp(op=ast.BitOr()):
                return self._union(node)
            case ast.Subscript(value=ast.Name(id=name), slice=sl):
                args = list(sl.elts) if isinstance(sl, ast.Tuple) else [sl]
                return self._generic(node, name, args)
            case ast.Call(func=ast.Name(id=name)) if name == ROW_TYPE:
                return self._row_call(node)
            case ast.Constant(value=str()):
                raise AnnotationError(
                    node, "string annotations are not supported", "write the type"
                )
        raise AnnotationError(node, f"'{ast.unparse(node)}' is not a type", _TYPES_HINT)

    def _name(self, node: ast.expr, name: str) -> T2Type:
        name = T2_TYPE_ALIASES.get(name, name)
        if name in T2_TYPE_NAMES:
            return PRIMS[name]
        if name == CALLER_TYPE:
            return CALLER
        if name in self.env.records:
            return RecordT(name)
        if self.sig:
            if name in _SIG_VARS:
                return TypeVar(name)
            if name == "Match":
                return MATCH
        if name in T2_GENERICS or name == ROW_TYPE:
            raise AnnotationError(node, f"'{name}' needs type arguments", _generic_hint(name))
        hint = T2_TYPE_HINTS.get(name, _TYPES_HINT)
        raise AnnotationError(node, f"unknown type '{name}'", hint)

    def _union(self, node: ast.BinOp) -> T2Type:
        parts: list[ast.expr] = []

        def flatten(n: ast.expr) -> None:
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                flatten(n.left)
                flatten(n.right)
            else:
                parts.append(n)

        flatten(node)
        types = [self.parse(p) for p in parts]
        nones = [t for t in types if t == NONE]
        rest = [t for t in types if t != NONE]
        if len(rest) != 1 or not nones:
            raise AnnotationError(
                node, "only 'T | None' unions are supported", T2_TYPE_HINTS["Union"]
            )
        return opt(rest[0])

    def _generic(self, node: ast.expr, name: str, args: list[ast.expr]) -> T2Type:
        if name == ROW_TYPE:
            if len(args) != 1:
                raise AnnotationError(node, "Row takes one schema", _row_hint(self.env))
            return self._row(args[0], private=False)
        if self.sig and name in _PSEUDO:
            (arg,) = [self.parse(a) for a in args]
            if name == "Iter":
                return IterT(arg)
            if name == "Counter":
                return CounterT(arg)
            return Pseudo(name, arg)
        want = T2_GENERICS.get(name)
        if want is None:
            hint = T2_TYPE_HINTS.get(name, _TYPES_HINT)
            raise AnnotationError(node, f"unknown generic type '{name}'", hint)
        parsed = [self.parse(a) for a in args]
        if want != -1 and len(parsed) != want:
            raise AnnotationError(
                node, f"{name}[...] takes {want} type argument(s)", _generic_hint(name)
            )
        match name:
            case "list":
                return ListT(parsed[0])
            case "set":
                return SetT(parsed[0])
            case "dict":
                return DictT(parsed[0], parsed[1])
        return TupleT(tuple(parsed))

    def _row_call(self, node: ast.Call) -> T2Type:
        private = False
        for kw in node.keywords:
            if (
                kw.arg == ROW_PRIVATE
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, bool)
            ):
                private = kw.value.value
            else:
                raise AnnotationError(
                    kw, "Row(...) takes only private=True/False", _row_hint(self.env)
                )
        if len(node.args) != 1:
            raise AnnotationError(node, "Row takes one schema", _row_hint(self.env))
        return self._row(node.args[0], private)

    def _row(self, arg: ast.expr, private: bool) -> T2Type:
        if isinstance(arg, ast.Name) and arg.id in self.env.tables:
            return RowT(arg.id, private)
        raise AnnotationError(
            arg, f"Row needs a database table, not '{ast.unparse(arg)}'", _row_hint(self.env)
        )


_TYPES_HINT = (
    "use Int, Float, Str, Bool, Date, DateTime, Duration, Uuid, Json, T | None, list[T], "
    "dict[K, V], set[T], tuple[...], Row[Table], Caller or a @record name"
)


def _generic_hint(name: str) -> str:
    return {
        "list": "write list[T]",
        "set": "write set[T]",
        "dict": "write dict[K, V]",
        "tuple": "write tuple[A, B]",
        ROW_TYPE: "write Row[Table] (or Row(Table, private=True))",
    }.get(name, _TYPES_HINT)


def _row_hint(env: TypeEnv) -> str:
    tables = ", ".join(sorted(env.tables)) or "(none)"
    return f"write Row[Table] or Row(Table, private=True); tables: {tables}"


# ---- signature matching -----------------------------------------------------------


@dataclass
class Bindings:
    vars: dict[str, T2Type]

    def get(self, name: str) -> T2Type | None:
        return self.vars.get(name)


def match(pattern: T2Type, arg: T2Type, b: Bindings) -> bool:
    """Does an argument of type `arg` fit the signature `pattern` (binding type
    variables in `b`)?"""
    if arg is UNK:
        return True
    match pattern:
        case TypeVar(name="Any"):
            return True
        case TypeVar(name="Sized"):
            return is_sized(arg)
        case TypeVar(name="Num"):
            if arg not in NUMERIC:
                return False
            return _bind(b, "Num", arg)
        case TypeVar(name="Factory"):
            return False  # handled by the checker (defaultdict)
        case TypeVar(name=name):
            return _bind(b, name, arg)
        case Pseudo(name="Iterable", arg=inner):
            el = elem_of(arg)
            return el is not None and match(inner, el, b)
        case Pseudo(name="Key"):
            return False  # handled by the checker (lambda)
        case Opt(inner=inner):
            if arg == NONE:
                return True
            return match(inner, strip_opt(arg), b)
        case ListT(elem=e):
            return isinstance(arg, ListT) and _match_inv(e, arg.elem, b)
        case SetT(elem=e):
            return isinstance(arg, SetT) and _match_inv(e, arg.elem, b)
        case DictT(key=k, val=v):
            if isinstance(arg, CounterT):
                return _match_inv(k, arg.key, b) and _match_inv(v, INT, b)
            return (
                isinstance(arg, DictT) and _match_inv(k, arg.key, b) and _match_inv(v, arg.val, b)
            )
        case TupleT(elems=es):
            return (
                isinstance(arg, TupleT)
                and len(arg.elems) == len(es)
                and all(match(p, a, b) for p, a in zip(es, arg.elems, strict=True))
            )
    return assignable(arg, pattern)


def _match_inv(pattern: T2Type, arg: T2Type, b: Bindings) -> bool:
    if isinstance(pattern, TypeVar) and pattern.name not in b.vars:
        b.vars[pattern.name] = arg
        return True
    if isinstance(pattern, TypeVar):
        return same(b.vars[pattern.name], arg)
    return match(pattern, arg, b)


def _bind(b: Bindings, name: str, arg: T2Type) -> bool:
    bound = b.vars.get(name)
    if bound is None:
        b.vars[name] = arg
        return True
    j = join(bound, arg)
    if j is None:
        return False
    b.vars[name] = j
    return True


def substitute(t: T2Type, b: Bindings, unresolved: Callable[[str], T2Type]) -> T2Type:
    """Replace the type variables of a result type by their bindings."""
    match t:
        case TypeVar(name=name):
            bound = b.get(name)
            return bound if bound is not None else unresolved(name)
        case Opt(inner=i):
            return opt(substitute(i, b, unresolved))
        case ListT(elem=e):
            return ListT(substitute(e, b, unresolved))
        case SetT(elem=e):
            return SetT(substitute(e, b, unresolved))
        case IterT(elem=e):
            return IterT(substitute(e, b, unresolved))
        case CounterT(key=k):
            return CounterT(substitute(k, b, unresolved))
        case DictT(key=k, val=v):
            return DictT(substitute(k, b, unresolved), substitute(v, b, unresolved))
        case TupleT(elems=es):
            return TupleT(tuple(substitute(e, b, unresolved) for e in es))
    return t


def has_unk(t: T2Type) -> bool:
    match t:
        case Opt(inner=i):
            return has_unk(i)
        case ListT(elem=e) | SetT(elem=e) | IterT(elem=e):
            return has_unk(e)
        case CounterT(key=k):
            return has_unk(k)
        case DictT(key=k, val=v):
            return has_unk(k) or has_unk(v)
        case TupleT(elems=es):
            return any(has_unk(e) for e in es)
    return t is UNK
