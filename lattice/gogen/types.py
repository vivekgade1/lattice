"""T2 types as Go types (plan 5a Task 8), and the Go helper functions the emitter
needs per type: equality, Python's ordering and repr.

| T2 | Go |
|---|---|
| Int / Float / Str / Bool / Uuid | int64 / float64 / string / bool / string |
| None | struct{} |
| T | None | rt.Opt[T] |
| list[T] / set[T] / dict[K, V] | *rt.List[T] / *rt.Set[T] / *rt.Dict[K, V] |
| tuple[...] | a generated `tup_<hash>` struct (fields F0, F1, ...), a valid map key |
| Iter[T] | *rt.Iter[T] |
| a record R | a generated `rec_R` struct (fields F_<name>) |
| Error (the value `except E as e` binds) | *rt.Error |
| Match (re.search, ...) | *rt.Match |
| Date / DateTime / Duration | rt.Date / rt.DateTime (UTC) / rt.Duration (Task 10) |
| Json | any: the Python-shaped value itself, shared (rt/jsonv.go) |
| Counter[T] (a defaultdict is a dict) | *rt.Dict[T, int64] of the Counter kind |

Row and Caller (the endpoint layer's values, plan 5a Task 12) are not implemented yet
(`lattice.targets.GO_T2_TYPES`): `p8_target` rejects them on Go with L892.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping

from lattice.check.t2 import types as T
from lattice.check.t2.types import T2Type
from lattice.runtime import LatticeInternalError

__all__ = ["GoTypes", "kinds"]

_SCALARS: Mapping[T2Type, str] = {
    T.INT: "int64",
    T.FLOAT: "float64",
    T.STR: "string",
    T.BOOL: "bool",
    T.UUID: "string",
    T.NONE: "struct{}",
    T.DATE: "rt.Date",
    T.DATETIME: "rt.DateTime",
    T.DURATION: "rt.Duration",
    T.JSON: "any",
}
# Values compared with Go's == (a Json is compared as Python compares, rt.JsonEq).
_COMPARABLE = frozenset(t for t in _SCALARS if t != T.JSON)


def kinds(t: T2Type) -> set[str]:
    """The kinds of value a type is made of ("Int", "list", "record", "Date", ...),
    as `lattice.targets.GO_T2_TYPES` names them."""
    out: set[str] = set()
    _kinds(t, out)
    return out


def _kinds(t: T2Type, out: set[str]) -> None:  # noqa: PLR0911 - one case per type
    match t:
        case T.Prim(name=name):
            out.add(name)
        case T.Opt(inner=inner):
            _kinds(inner, out)
        case T.ListT(elem=e):
            out.add("list")
            _kinds(e, out)
        case T.SetT(elem=e):
            out.add("set")
            _kinds(e, out)
        case T.IterT(elem=e):
            out.add("Iter")
            _kinds(e, out)
        case T.DictT(key=k, val=v):
            out.add("dict")
            _kinds(k, out)
            _kinds(v, out)
        case T.CounterT(key=k):
            out.add("Counter")
            _kinds(k, out)
        case T.TupleT(elems=es):
            out.add("tuple")
            for e in es:
                _kinds(e, out)
        case T.RecordT():
            out.add("record")
        case T.RowT():
            out.add("Row")
        case T.Named(name=name):
            out.add(name)
        case T.TypeVar() | T.Pseudo():
            pass  # a signature's placeholder (a callee's own entry), not a value
        case _ if t is T.UNK:
            pass
        case _:
            out.add(t.render())


def is_container(t: T2Type, records: Mapping[str, tuple[tuple[str, T2Type], ...]]) -> bool:
    """A value whose str() prints a container: a list, set, dict, tuple, Counter or
    iterator, alone or inside an optional value or a record's fields."""
    match t:
        case T.Opt(inner=inner):
            return is_container(inner, records)
        case T.ListT() | T.SetT() | T.DictT() | T.CounterT() | T.TupleT() | T.IterT():
            return True
        case T.RecordT(name=name):
            return any(is_container(ft, records) for _, ft in records.get(name, ()))
    return False


def _digest(t: T2Type) -> str:
    return hashlib.sha1(t.render().encode()).hexdigest()[:10]  # noqa: S324 - a name, not security


class GoTypes:
    """Spells T2 types in Go and collects the declarations they need (tuple structs,
    record structs, helper functions), each once, in `decls()`."""

    def __init__(self, records: Iterable[tuple[str, tuple[tuple[str, T2Type], ...]]] = ()) -> None:
        self.records: dict[str, tuple[tuple[str, T2Type], ...]] = dict(records)
        self._decls: dict[str, str] = {}  # name -> Go source, in first-use order

    # ---- spelling ----------------------------------------------------------------------

    def spell(self, t: T2Type) -> str:  # noqa: PLR0911 - one case per type
        scalar = _SCALARS.get(t)
        if scalar is not None:
            return scalar
        match t:
            case T.Opt(inner=inner):
                return f"rt.Opt[{self.spell(inner)}]"
            case T.ListT(elem=e):
                return f"*rt.List[{self.spell(e)}]"
            case T.SetT(elem=e):
                return f"*rt.Set[{self.spell(e)}]"
            case T.DictT(key=k, val=v):
                return f"*rt.Dict[{self.spell(k)}, {self.spell(v)}]"
            case T.CounterT(key=k):
                return f"*rt.Dict[{self.spell(k)}, int64]"  # a Counter kind of dict
            case T.IterT(elem=e):
                return f"*rt.Iter[{self.spell(e)}]"
            case T.TupleT():
                return self.tuple_name(t)
            case T.RecordT(name=name):
                self.record_struct(name)
                return f"rec_{name}"
            case T.Named(name="Error"):
                return "*rt.Error"
            case T.Named(name="Match"):
                return "*rt.Match"
        raise LatticeInternalError(f"gogen: no Go type for {t.render()}")

    def tuple_name(self, t: T.TupleT) -> str:
        name = f"tup_{_digest(t)}"
        if name not in self._decls:
            self._decls[name] = ""  # reserve (recursive spelling of the fields)
            fields = "".join(f"\tF{i} {self.spell(e)}\n" for i, e in enumerate(t.elems))
            reprs = [f"rt.Repr(any(t.F{i}))" for i in range(len(t.elems))]
            if not reprs:
                join = '"()"'
            elif len(reprs) == 1:
                join = f'"(" + {reprs[0]} + ",)"'
            else:
                join = '"(" + ' + ' + ", " + '.join(reprs) + ' + ")"'
            items = ", ".join(f"t.F{i}" for i in range(len(t.elems)))
            self._decls[name] = (
                f"// {name} is {t.render()}.\ntype {name} struct {{\n{fields}}}\n\n"
                f"func (t {name}) PyRepr() string {{ return {join} }}\n\n"
                f"// PyTuple is the tuple's items (a Json value's view of it).\n"
                f"func (t {name}) PyTuple() []any {{ return []any{{{items}}} }}\n"
            )
        return name

    def record_struct(self, name: str) -> None:
        key = f"rec_{name}"
        if key in self._decls:
            return
        if name not in self.records:
            raise LatticeInternalError(f"gogen: unknown record {name}")
        self._decls[key] = ""
        fields = self.records[name]
        body = "".join(f"\tF_{f} {self.spell(ft)}\n" for f, ft in fields)
        parts = ' + ", " + '.join(f'"{f}=" + rt.Repr(any(r.F_{f}))' for f, _ in fields)
        text = f'"{name}(" + {parts} + ")"' if fields else f'"{name}()"'
        self._decls[key] = (
            f"// {key} is the @record {name}.\ntype {key} struct {{\n{body}}}\n\n"
            f"func (r {key}) PyRepr() string {{ return {text} }}\n"
        )

    def decls(self) -> str:
        return "\n".join(src for src in self._decls.values() if src)

    # ---- helper functions ----------------------------------------------------------------

    def helper(self, name: str, make: Callable[[], str]) -> str:
        if name not in self._decls:
            self._decls[name] = ""
            self._decls[name] = make()
        return name

    def eq(self, t: T2Type) -> str:
        """A Go func value `func(a, b T) bool`: Python's ==."""
        if t == T.JSON:
            return "rt.JsonEq"
        if t in _COMPARABLE or t == T.MATCH:  # a Match compares by identity, as in Python
            return f"rt.Eq[{self.spell(t)}]"
        go = self.spell(t)
        name = f"eq_{_digest(t)}"

        def make() -> str:
            return f"func {name}(a, b {go}) bool {{ return {self.eq_expr('a', 'b', t)} }}\n"

        return self.helper(name, make)

    def eq_expr(self, a: str, b: str, t: T2Type) -> str:  # noqa: PLR0911
        """Go bool expression: a == b (a and b are evaluated once each: plain names)."""
        if t == T.JSON:
            return f"rt.JsonEq({a}, {b})"
        if t in _COMPARABLE:
            return f"{a} == {b}"
        match t:
            case T.Opt(inner=inner):
                return f"rt.OptEq({a}, {b}, {self.eq(inner)})"
            case T.ListT(elem=e):
                return f"rt.ListEq({a}, {b}, {self.eq(e)})"
            case T.SetT():
                return f"{a}.Equal({b})"
            case T.DictT(val=v):
                return f"rt.DictEq({a}, {b}, {self.eq(v)})"
            case T.CounterT():
                return f"rt.DictEq({a}, {b}, rt.Eq[int64])"
            case T.TupleT(elems=es):
                if not es:
                    return "true"
                return " && ".join(f"{self.eq(e)}({a}.F{i}, {b}.F{i})" for i, e in enumerate(es))
            case T.RecordT(name=name):
                fields = self.records[name]
                if not fields:
                    return "true"
                return " && ".join(f"{self.eq(ft)}({a}.F_{f}, {b}.F_{f})" for f, ft in fields)
            case T.IterT() | T.Named(name="Match"):
                return f"{a} == {b}"  # identity: iterators and matches compare by identity
        raise LatticeInternalError(f"gogen: no equality for {t.render()}")

    def order(self, t: T2Type, op: str) -> str:
        """A Go func value `func(a, b T) bool`: Python's `a op b` for op in < <= > >=."""
        go = self.spell(t)
        if t in (T.INT, T.FLOAT, T.STR, T.DATE, T.DATETIME):
            return f"func(a, b {go}) bool {{ return a {op} b }}"
        if t == T.DURATION:
            return f"func(a, b {go}) bool {{ return a.Cmp(b) {op} 0 }}"
        if t == T.BOOL:
            return f"func(a, b bool) bool {{ return rt.B2I(a) {op} rt.B2I(b) }}"
        if isinstance(t, T.TupleT):
            names = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
            name = f"{names[op]}_{_digest(t)}"

            def make() -> str:
                # CPython's tuple comparison: the first index whose items differ (==)
                # decides with op; all equal (same length) -> op on the lengths
                lines = [f"func {name}(a, b {go}) bool {{"]
                for i, e in enumerate(t.elems):
                    lines.append(
                        f"\tif !{self.eq(e)}(a.F{i}, b.F{i}) {{ return ({self.order(e, op)})"
                        f"(a.F{i}, b.F{i}) }}"
                    )
                lines.append(f"\treturn {'true' if op in ('<=', '>=') else 'false'}")
                lines.append("}")
                return "\n".join(lines) + "\n"

            return self.helper(name, make)
        raise LatticeInternalError(f"gogen: {t.render()} is not ordered")
