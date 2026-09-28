"""Lowering checked T2 procedures to T2 IR: one golden dump per construct, every T2
program the test suites check lowers, and the module-level parts (constants, natives,
records) come out as `lattice.t2.compile` builds them."""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from lattice.runtime import LatticeInternalError
from lattice.t2.compile import _constants
from lattice.t2ir import nodes as n
from lattice.t2ir.dump import dump
from lattice.t2ir.lower import lower_t2
from tests.t2.util import checked

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted(p.name.removesuffix(".lattice.py") for p in GOLDEN.glob("*.lattice.py"))


@pytest.mark.parametrize("name", CASES)
def test_golden(name, request):
    src = (GOLDEN / f"{name}.lattice.py").read_text()
    program = checked(src).program
    text = dump(lower_t2(program.t2, None))
    expected = GOLDEN / f"{name}.txt"
    if request.config.getoption("--update-golden", default=False):
        expected.write_text(text)
    assert text == expected.read_text()


def test_every_existing_t2_program_lowers():
    """Coverage: every procedure the T2 test suites check lowers without error."""
    from tests.t2ir.corpus import all_t2_programs

    names = []
    for name, program in all_t2_programs():
        module = lower_t2(program.t2, ast.parse(program.source))  # raises on an unsupported node
        assert [p.name for p in module.procedures] == list(program.t2.procedures), name
        names.append(name)
    assert len(names) > 40


_GROW_METHOD = {"+": "add", "*": "mul", "**": "pow", "<<": "lshift"}


def _ir_fuel_sites(node: object, counts: Counter[str]) -> None:
    """The `<fuel>.<method>(` call each IR fuel node stands for (see lower.py)."""
    match node:
        case n.Tick():
            counts["tick"] += 1
        case n.Each():
            counts["each"] += 1
        case n.Bulk():
            counts["bulk"] += 1
        case n.Charged():
            counts["call"] += 1
        case n.Grow(op=op):
            counts[_GROW_METHOD[op]] += 1
        case n.AugAssign(grow=True):
            counts["aug"] += 1
        case n.Sized(kind="pad"):
            counts["width"] += 1
        case n.Sized():
            counts["call"] += 1  # <fuel>.call(<fuel>.<kind>)(...)
        case n.Scan():
            counts["scan"] += 1
        case n.FPart(spec=spec) if spec != "":
            counts["fmt"] += 1
    if is_dataclass(node):
        for f in fields(node):
            _ir_fuel_sites(getattr(node, f.name), counts)
    elif isinstance(node, tuple):
        for x in node:
            _ir_fuel_sites(x, counts)


def test_fuel_sites_match_the_instrumented_source():
    """Across the corpus, the IR has exactly as many fuel sites of each kind as
    `lattice.t2.compile` instruments (every `<fuel>.<method>(` in its source)."""
    from lattice.t2 import compile_t2
    from tests.t2ir.corpus import all_t2_programs

    goldens = [(c, checked((GOLDEN / f"{c}.lattice.py").read_text()).program) for c in CASES]
    total: Counter[str] = Counter()
    for name, program in [*all_t2_programs(), *goldens]:
        source = ast.parse(program.source)
        compiled = compile_t2(program.t2, source).source
        want = Counter(re.findall(r"\b_lt_fuel\d*\.(\w+)\(", compiled))
        del want["enter"], want["leave"]  # the procedure's entry, not in its body
        got: Counter[str] = Counter()
        _ir_fuel_sites(lower_t2(program.t2, source).procedures, got)
        assert +got == +want, name
        total += got
    # every kind of fuel site is exercised somewhere
    kinds = {"tick", "each", "bulk", "call", "aug", "width", "scan", "fmt"}
    assert set(total) == {*_GROW_METHOD.values(), *kinds}


def test_module_parts_match_the_compiled_module():
    from tests.api import t2prog

    program = checked(t2prog.PROCEDURES.removeprefix("# lattice: v1\n"), t2prog.PROFILE).program
    source = ast.parse(program.source)
    module = lower_t2(program.t2, source)
    assert [c for c, _, _ in module.constants] == [c for c, _ in _constants(program.t2, source)]
    assert [c for c, _, _ in module.constants] == ["LOCK_AFTER", "LOCK_MINUTES", "PREFIX"]
    assert module.constants[0][2] == n.Const(3, program.t2.constants["LOCK_AFTER"])
    assert module.natives == ("send_digest", "slugify")
    assert dict(module.imports) == {"math": "math"}
    assert [r.name for r in module.records] == list(program.t2.records)
    procs = {p.name: p for p in module.procedures}
    assert procs["depth"].recursive and not procs["label"].recursive
    # no source: no constants (as compile_t2 without a module)
    assert lower_t2(program.t2, None).constants == ()


def test_unreachable_statements_are_not_lowered():
    """The checker leaves code after a return (or an endless loop) untyped; it never
    runs, so it is dropped instead of lowered."""
    program = checked(
        """
    @procedure
    def spin(n: Int) -> Int:
        total = 0
        while True:
            total += n
        return total
    """
    ).program
    (proc,) = lower_t2(program.t2, None).procedures
    assert [type(s) for s in proc.body] == [n.Assign, n.While]
    program = checked(
        """
    @procedure
    def f(xs: list[Int]) -> Int:
        for x in xs:
            if x > 0:
                continue
                pass
            break
            pass
        return 0
        try:
            return 1
        except ValueError:
            return 2
    """
    ).program
    (proc,) = lower_t2(program.t2, None).procedures
    assert dump(lower_t2(program.t2, None)).count("Pass") == 0
    assert [type(s) for s in proc.body] == [n.For, n.Return]
    (loop,) = [s for s in proc.body if isinstance(s, n.For)]
    assert [type(s) for s in loop.body] == [n.Tick, n.If, n.Break]


def test_chained_assignments_and_dict_unpacking():
    program = checked(
        """
    @procedure
    def f(d: dict[Str, Int]) -> Int:
        e = {**d, "x": 1}
        e["a"] = y = 3
        z = w = y
        return e["a"] + y + z + w
    """
    ).program
    (proc,) = lower_t2(program.t2, None).procedures
    dict_lit = proc.body[0].value
    assert isinstance(dict_lit, n.DictLit) and dict_lit.keys[0] is None
    assert dict_lit.values[0] == n.Var("d", program.t2.procedures["f"].locals["d"])
    # e["a"] = y = 3: the value goes through a temporary, assigned left to right
    temp, to_item, to_y = proc.body[1:4]
    assert temp.target == n.NameT("_lt_t1") and "_lt_t1" in proc.locals
    assert isinstance(to_item.target, n.IndexT) and to_item.value.name == "_lt_t1"
    assert to_y.target == n.NameT("y") and to_y.value.name == "_lt_t1"
    # z = w = y: the first name is read back
    assert proc.body[4].target == n.NameT("z") and proc.body[5].value.name == "z"
    assert "None" in dump(lower_t2(program.t2, None))  # the ** entry's key


def test_an_unsupported_node_is_an_internal_error():
    from lattice.t2ir.lower import _Proc

    program = checked(
        """
    @procedure
    def f(x: Int) -> Int:
        return x
    """
    ).program
    lowering = _Proc(program.t2.procedures["f"])
    with pytest.raises(LatticeInternalError, match="T2 IR: unsupported Raise"):
        lowering.stmt(ast.parse("raise ValueError('x')").body[0])
    with pytest.raises(LatticeInternalError, match="T2 IR: unsupported Starred"):
        lowering.expr(ast.Starred(ast.Name("x", ast.Load()), ast.Load()))
    with pytest.raises(LatticeInternalError, match="T2 IR: no type"):
        lowering.expr(ast.Name("x", ast.Load()))  # not a node the checker typed
