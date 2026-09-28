"""emit_py must reproduce the instrumented compiler: same values, same R620/R621/R622
at the same points, over every T2 program the test suites use. Deleted with the old
path at the end of Task 5 (then the regular suites guard behaviour).

It compares pre-v1.1 behaviour: `lattice.t2.support.V11` is off, because R623 and
ordered sets intentionally differ from the legacy compiler."""

import ast
import dataclasses

import pytest

from lattice.t2 import T2Fuel, support, t2_load
from lattice.t2.compile import _legacy_compile_t2  # renamed old entry point, deleted in Step 7
from lattice.t2.emit_py import emit_python
from lattice.t2ir.lower import lower_t2
from tests.t2ir.corpus import all_t2_calls

CALLS = list(all_t2_calls())


def _norm(v: object) -> object:
    """A value comparable across two namespaces (records are per-namespace classes)."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return (type(v).__name__, tuple(_norm(getattr(v, f.name)) for f in dataclasses.fields(v)))
    if isinstance(v, list | tuple):
        return (type(v).__name__, [_norm(x) for x in v])
    if isinstance(v, dict):
        return ("dict", [(_norm(k), _norm(x)) for k, x in v.items()])
    return (type(v).__name__, v)


def outcome(ns: dict, proc: str, args: tuple, budget: int) -> tuple:
    fuel = T2Fuel(budget)
    fresh = tuple(list(a) if isinstance(a, list) else a for a in args)
    try:
        value = ns[proc](fuel, *fresh)
    except Exception as e:  # compare the error kind/code, not the message text
        return ("err", getattr(e, "code", type(e).__name__), fuel.left, fuel.depth)
    return ("ok", _norm(value), fuel.left, fuel.depth)


def test_the_corpus_is_large():
    assert len(CALLS) > 500


@pytest.mark.parametrize("name,program,proc,args,budget", CALLS, ids=[c[0] for c in CALLS])
def test_same_outcome(name, program, proc, args, budget, monkeypatch):
    monkeypatch.setattr(support, "V11", False)
    module = ast.parse(program.source)
    old = t2_load(_legacy_compile_t2(program.t2, module).source)
    new = t2_load(emit_python(lower_t2(program.t2, module)).source)
    assert outcome(new, proc, args, budget) == outcome(old, proc, args, budget)
