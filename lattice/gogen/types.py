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
| Row[Table] / Row(Table, private=True) | a generated `row_Table` / `rowp_Table` struct |
| Caller | the generated `caller_T` struct |

Row and Caller are the endpoint layer's values (plan 5a Task 12): a T2Row
(lattice/t2/support.py) with a fixed set of columns, known from the API profile
(`api_types`). A Row's fields are the table's columns (the public ones unless
private=True), each its column's scalar (optional when the column is nullable); the
Caller's are the users table's public columns, then `id` (the users key), `is_admin` and
`role` where the table has no such column (`T2Request.caller`'s dict, in its order).
The endpoint emitter (lattice/gogen/endpoints.py) builds them from stored cells.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING

from lattice.catalog import CALLER_TYPE
from lattice.check.t2 import types as T
from lattice.check.t2.types import T2Type
from lattice.runtime import LatticeInternalError

if TYPE_CHECKING:
    from lattice.profile import ApiProfile

__all__ = ["GoTypes", "api_types", "kinds"]

Fields = tuple[tuple[str, T2Type], ...]
TableFields = tuple[tuple[str, T2Type, bool], ...]  # (column, type, private)


def api_types(api: ApiProfile, user_key: str = "id") -> tuple[dict[str, TableFields], Fields]:
    """(each table's columns as Row fields, the Caller's fields) of an API profile."""
    tables: dict[str, TableFields] = {}
    for name, schema in api.tables.items():
        tables[name] = tuple(
            (c, _column_type(s.value, c in schema.nullable), c in schema.private)
            for c, s in schema.columns
        )
    users = api.tables.get(api.users_table)
    caller: dict[str, T2Type] = {}
    if users is not None:
        for c, s in users.columns:
            if c not in users.private:
                caller[c] = _column_type(s.value, c in users.nullable)
        key = users.get(user_key)
        caller["id"] = T.PRIMS[key.value] if key is not None else T.INT
    else:
        caller["id"] = T.INT
    caller["is_admin"] = T.BOOL
    caller["role"] = T.STR
    return tables, tuple(caller.items())


def _column_type(scalar: str, nullable: bool) -> T2Type:
    t = T.PRIMS[scalar]
    return T.opt(t) if nullable else t


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

    def __init__(
        self,
        records: Iterable[tuple[str, tuple[tuple[str, T2Type], ...]]] = (),
        tables: Mapping[str, TableFields] | None = None,
        caller: Fields | None = None,
    ) -> None:
        self.records: dict[str, tuple[tuple[str, T2Type], ...]] = dict(records)
        # the endpoint layer's values (plan 5a Task 12): each table's columns (a
        # Row[Table]) and the Caller's fields, from the API profile (`api_types`)
        self.tables: dict[str, TableFields] = dict(tables or {})
        self.caller: Fields | None = caller
        self._decls: dict[str, str] = {}  # name -> Go source, in first-use order

    def row_fields(self, t: T.RowT) -> Fields:
        """The fields of a Row[Table] (its public columns; every column with private=)."""
        columns = self.tables.get(t.table)
        if columns is None:
            raise LatticeInternalError(f"gogen: no columns for table {t.table}")
        return tuple((c, ct) for c, ct, private in columns if t.private or not private)

    def fields(self, t: T2Type) -> Fields:
        """The fields of a record, a Row or the Caller (Go struct fields F_<name>)."""
        match t:
            case T.RecordT(name=name):
                return self.records[name]
            case T.RowT():
                return self.row_fields(t)
            case T.Named(name=name) if name == CALLER_TYPE:
                if self.caller is None:
                    raise LatticeInternalError("gogen: the Caller's fields are unknown")
                return self.caller
        raise LatticeInternalError(f"gogen: {t.render()} has no fields")

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
            case T.RowT(table=table, private=private):
                name = f"{'rowp' if private else 'row'}_{table}"
                self._row_struct(name, table, self.row_fields(t))
                return name
            case T.Named(name=name) if name == CALLER_TYPE:
                self._row_struct("caller_T", CALLER_TYPE, self.fields(t))
                return "caller_T"
        raise LatticeInternalError(f"gogen: no Go type for {t.render()}")

    def _row_struct(self, key: str, label: str, fields: Fields) -> None:
        """A Row[Table] or the Caller: a T2Row (lattice/t2/support.py) as a struct,
        whose repr is `Label(column=value, ...)` with each value's repr."""
        if key in self._decls:
            return
        self._decls[key] = ""
        body = "".join(f"\tF_{f} {self.spell(ft)}\n" for f, ft in fields)
        reprs = [f'"{f}=" + {self.field_repr(f"r.F_{f}", ft)}' for f, ft in fields]
        text = f'"{label}(" + ' + ' + ", " + '.join(reprs) + ' + ")"' if reprs else f'"{label}()"'
        self._decls[key] = (
            f"// {key} is a {label} row (a T2Row).\ntype {key} struct {{\n{body}}}\n\n"
            f"func (r {key}) PyRepr() string {{ return {text} }}\n"
        )

    def field_repr(self, go: str, t: T2Type) -> str:
        """repr() of a row's value (a Uuid is a uuid.UUID in Python: UUID('...'))."""
        if t == T.UUID:
            return f'("UUID(\'" + {go} + "\')")'
        if isinstance(t, T.Opt) and t.inner == T.UUID:
            return f'rt.ReprOpt({go}, func(v string) string {{ return "UUID(\'" + v + "\')" }})'
        return f"rt.Repr(any({go}))"

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
            case T.RecordT() | T.RowT() | T.Named(name="Caller"):
                fields = self.fields(t)
                if not fields:
                    return "true"
                return " && ".join(f"{self.eq(ft)}({a}.F_{f}, {b}.F_{f})" for f, ft in fields)
            case T.IterT() | T.Named(name="Match"):
                return f"{a} == {b}"  # identity: iterators and matches compare by identity
        raise LatticeInternalError(f"gogen: no equality for {t.render()}")

    def exact(self, src: T2Type, dst: T2Type) -> str:
        """A Go func `func(x src) (dst, bool)`: x as a dst value equal to it, exactly --
        an Int as a Float only when the Float is exact, a Float as an Int only when
        integral and in range (false: no dst value equals x), through tuples and
        optional values."""
        if src == dst or self.spell(src) == self.spell(dst):
            go = self.spell(src)
            return f"func(x {go}) ({go}, bool) {{ return x, true }}"
        if src == T.INT and dst == T.FLOAT:
            return "rt.IntAsFloat"
        if src == T.FLOAT and dst == T.INT:
            return "rt.FloatAsInt"
        s, d = self.spell(src), self.spell(dst)
        name = f"xc_{_digest(T.TupleT((src, dst)))}"

        def make() -> str:  # noqa: PLR0911
            head = f"func {name}(x {s}) ({d}, bool) {{\n"
            match src, dst:
                case T.TupleT(elems=xs), T.TupleT(elems=ys) if len(xs) == len(ys):
                    lines = [f"\tvar out {d}\n", "\tvar ok bool\n"]
                    for i, (a, b) in enumerate(zip(xs, ys, strict=True)):
                        sub = self.exact(a, b)
                        lines.append(
                            f"\tif out.F{i}, ok = ({sub})(x.F{i}); !ok {{ return out, false }}\n"
                        )
                    return head + "".join(lines) + "\treturn out, true\n}\n"
                case T.Opt(inner=a), T.Opt(inner=b):
                    none = f"rt.None[{self.spell(b)}]()"
                    return (
                        f"{head}\tif !x.Ok {{ return {none}, true }}\n"
                        f"\tv, ok := ({self.exact(a, b)})(x.V)\n"
                        f"\treturn rt.Some(v), ok\n}}\n"
                    )
                case _, T.Opt(inner=b) if src == T.NONE:
                    return f"{head}\treturn rt.None[{self.spell(b)}](), true\n}}\n"
                case _, T.Opt(inner=b):
                    return (
                        f"{head}\tv, ok := ({self.exact(src, b)})(x)\n\treturn rt.Some(v), ok\n}}\n"
                    )
            raise LatticeInternalError(f"gogen: no exact {src.render()} -> {dst.render()}")

        return self.helper(name, make)

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
