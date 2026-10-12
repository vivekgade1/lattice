"""R-widen (docs/lattice-language-rules.md, 2026-09-28): after lowering, no Float-typed
place holds an Int-typed value. An independent audit of the lowered IR of every T2
program the suites check (the T2 IR corpus, the shared semantics suite, the Go
programs): each place whose type the IR states -- a local, an item, a return, a
procedure or record argument, a literal element, an if-expression branch, an
`and`/`or` operand, a tuple indexed or iterated, `min`/`max` arguments, a record
default -- is checked against the value put there. Plus the forms whose Python result
was an int typed Float: `Int ** n` and an empty `sum` of Floats."""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass

import pytest

from lattice.check.t2 import types as T
from lattice.t2ir import nodes as n
from lattice.t2ir.lower import _widened, lower_t2
from tests.t2.util import checked


def _leaks(module: n.T2Module) -> list[str]:  # noqa: C901 - one case per kind of place
    """The places of `module` where an Int-typed value sits in a Float slot."""
    procs = {f"proc:{p.name}": p.params for p in module.procedures}
    procs |= {f"record:{r.name}": r.fields for r in module.records}
    out: list[str] = []

    def check(e: n.Expr | None, slot: T.T2Type, where: str) -> None:
        if e is not None and _widened(e.type, slot) != e.type:
            out.append(f"{where}: {e.type.render()} in a {slot.render()} place")

    def item(t: T.T2Type) -> T.T2Type:
        match t:
            case T.ListT(elem=e):
                return e
            case T.DictT(val=v):
                return v
        return T.UNK

    def walk(node: object, proc: n.Procedure) -> None:  # noqa: C901, PLR0912
        match node:
            case n.Assign(target=n.NameT(name=x), value=v):
                check(v, proc.locals.get(x, T.UNK), f"{proc.name}: {x} =")
            case n.Assign(target=n.IndexT(obj=o, index=i), value=v):
                check(v, item(o.type), f"{proc.name}: item store")
                if isinstance(o.type, T.DictT):
                    check(i, o.type.key, f"{proc.name}: key store")
            case n.Return(value=v):
                check(v, proc.returns, f"{proc.name}: return")
            case n.Call(target=t, args=args, kwargs=kwargs) if t in procs:
                params = dict(procs[t])
                for a, p in zip(args, params, strict=False):
                    check(a, params[p], f"{proc.name}: {t} {p}")
                for k, v in kwargs:
                    check(v, params[k], f"{proc.name}: {t} {k}")
            case n.Call(target="builtin:min" | "builtin:max", args=[_, _, *_] as args, type=t):
                for a in args:
                    check(a, t, f"{proc.name}: min/max")
            case n.Call(target="builtin:sum", args=[_], type=T.FLOAT):
                out.append(f"{proc.name}: sum of Floats without a start")
            case (
                n.Grow(op="**", left=a, right=b, type=T.FLOAT)
                | n.Binary(op="**", left=a, right=b, type=T.FLOAT)
            ) if a.type == T.INT and b.type == T.INT:
                out.append(f"{proc.name}: Int ** Int typed Float")
            case (
                n.ListLit(elts=elts, type=T.ListT(elem=el))
                | n.SetLit(elts=elts, type=T.SetT(elem=el))
            ):
                for x in elts:
                    check(x, el, f"{proc.name}: element")
            case n.DictLit(keys=keys, values=values, type=T.DictT() as t):
                for k, v in zip(keys, values, strict=True):
                    if k is None:
                        check(v, t, f"{proc.name}: **spread")
                    else:
                        check(k, t.key, f"{proc.name}: key")
                        check(v, t.val, f"{proc.name}: value")
            case n.IfExp(body=b, orelse=o, type=t):
                check(b, t, f"{proc.name}: if-expression")
                check(o, t, f"{proc.name}: if-expression")
            case n.BoolOp(values=values, type=t) if t != T.BOOL:
                for v in values:
                    check(v, t, f"{proc.name}: and/or")
            case n.Index(obj=o, index=i, type=t) if isinstance(o.type, T.TupleT):
                if not isinstance(i, n.Const):
                    for x in o.type.elems:
                        if _widened(x, t) != x:
                            out.append(f"{proc.name}: tuple[i] of {o.type.render()}")
            case n.For(iter=it) | n.Gen(iter=it):
                inner = it.iter if isinstance(it, n.Each) else it
                if isinstance(inner.type, T.TupleT):
                    el = T.elem_of(inner.type)
                    if el is not None and any(_widened(x, el) != x for x in inner.type.elems):
                        out.append(f"{proc.name}: iterated {inner.type.render()}")
        if is_dataclass(node):
            for f in fields(node):
                walk(getattr(node, f.name), proc)
        elif isinstance(node, tuple):
            for x in node:
                walk(x, proc)

    for rec in module.records:
        types = dict(rec.fields)
        for f, v in rec.defaults.items():
            if T.strip_opt(types[f]) == T.FLOAT and type(v) is int:
                out.append(f"record {rec.name}: default {f}={v!r}")
    for proc in module.procedures:
        walk(proc.body, proc)
    return out


def _programs():
    from tests.semantics_v11.cases import CASES
    from tests.t2ir.corpus import all_t2_programs

    yield from all_t2_programs()
    for case in CASES:
        yield case.name, checked(case.source).program


def test_no_int_value_in_a_float_place_after_lowering():
    leaks = []
    count = 0
    for name, program in _programs():
        module = lower_t2(program.t2, ast.parse(program.source))
        leaks += [f"{name}: {x}" for x in _leaks(module)]
        count += 1
    assert count > 250
    assert not leaks, leaks[:20]


def test_the_audit_sees_an_unwidened_place():
    """The audit is not vacuous: a hand-built procedure that stores an Int in a Float
    local, returns one as a Float, and sums Floats from an int start is flagged."""
    n_int = n.Var("n", T.INT)
    proc = n.Procedure(
        name="f",
        params=(("n", T.INT),),
        returns=T.FLOAT,
        locals={"n": T.INT, "x": T.FLOAT},
        body=(
            n.Assign(n.NameT("x"), n_int),
            n.Return(n.Call("builtin:sum", (n.Var("xs", T.ListT(T.FLOAT)),), (), T.FLOAT)),
            n.Return(n_int),
        ),
        recursive=False,
    )
    module = n.T2Module((proc,), (), (), {}, ())
    assert len(_leaks(module)) == 3


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("x: Float = n\n    return x", "Widen : Float\n      Var n : Int"),
        ("return 2.5 if n > 0 else 1", "orelse: Const 1.0 : Float"),
        ("return (1, 2.5)[n]", "TupleLit : tuple[Float, Float]"),
        ("return round(n)", "Call builtin:round : Int\n          Var n : Int"),
        ("return n / 2", "Binary(/) : Float\n      Var n : Int"),
    ],
)
def test_widening_shapes(body, fragment):
    from lattice.t2ir.dump import dump

    program = checked(f"@procedure\ndef f(n: Int) -> Float:\n    {body}\n").program
    assert fragment in dump(lower_t2(program.t2, None))
