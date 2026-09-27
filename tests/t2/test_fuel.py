"""T2b: fuel-instrumented compilation of checked procedures (spec 1c §4).

Procedures are compiled from their checked AST into a restricted namespace and run
with a fuel budget: every loop iteration, call and comprehension element costs one
unit; recursion is bounded by a depth. Running out is R620."""

from __future__ import annotations

import datetime as dt

import pytest

from lattice.t2 import T2Fuel, T2FuelExhausted, compile_t2, t2_load
from lattice.t2.support import T2_MAX_DEPTH, T2Row
from tests.t2.util import checked, load


def run(src: str, proc: str, *args: object, budget: int = 1_000_000, natives=None):
    ns = load(src, natives)
    return ns[proc](T2Fuel(budget), *args)


# ---- semantics --------------------------------------------------------------------------


def test_a_procedure_runs_as_python():
    src = """
    @procedure
    def f(xs: list[Int], k: Int) -> Int:
        total = 0
        for x in xs:
            if x > k:
                total += x
        return total
    """
    assert run(src, "f", [1, 5, 7], 4) == 12


def test_records_are_frozen_dataclasses():
    src = """
    @record
    class P:
        x: Int
        tag: Str | None = None

    @procedure
    def mk(n: Int) -> P:
        return P(x=n)
    """
    p = run(src, "mk", 3)
    assert p.x == 3 and p.tag is None
    with pytest.raises(AttributeError):
        p.x = 4  # frozen


def test_helpers_std_imports_and_constants():
    src = """
    import math
    from datetime import timedelta

    LIMIT = 10
    NAMES = ["a", "b"]

    @procedure
    def f(x: Float, now: DateTime) -> Str:
        later = now + hours(2) + timedelta(minutes=math.floor(x))
        return f"{later:%H:%M} {LIMIT} {len(NAMES)}"
    """
    now = dt.datetime(2000, 1, 1, 10, 0, tzinfo=dt.UTC)
    assert run(src, "f", 5.9, now) == "12:05 10 2"


def test_procedures_call_each_other_and_natives():
    src = """
    @procedure
    def inc(x: Int) -> Int:
        return x + 1

    @procedure
    def g(s: Str) -> Str:
        return native.slugify(s) + str(inc(1))
    """
    assert run(src, "g", "A B", natives={"slugify": lambda s: s.lower().replace(" ", "-")}) == (
        "a-b2"
    )


def test_try_except_and_comprehensions():
    src = """
    @procedure
    def parse(xs: list[Str]) -> list[Int]:
        out: list[Int] = []
        for x in xs:
            try:
                out.append(int(x))
            except ValueError:
                out.append(-1)
        return sorted([v * 2 for v in out if v != 0], key=lambda v: -v)
    """
    assert run(src, "parse", ["3", "x", "0", "5"]) == [10, 6, -2]


def test_rows_are_immutable_values():
    src = """
    @procedure
    def title(item: Row[Items]) -> Str:
        return item.title.upper()
    """
    row = T2Row("Items", {"id": 1, "title": "abc"})
    assert run(src, "title", row) == "ABC"
    with pytest.raises(AttributeError):
        row.title = "x"


# ---- the restricted namespace -----------------------------------------------------------


def test_no_ambient_builtins():
    src = """
    @procedure
    def f(x: Int) -> Int:
        return x
    """
    ns = load(src)
    assert set(ns["__builtins__"]) == {"__import__"}  # for the stdlib's own imports
    with pytest.raises(ImportError):
        ns["__builtins__"]["__import__"]("os")
    for name in ("open", "eval", "exec", "__import__", "getattr", "print", "type"):
        assert name not in ns
    # the loader helpers are gone after loading
    assert not any(k.startswith("t2_") for k in ns)
    # a module import is a namespace with only its allowlisted names
    src2 = """
    import math

    @procedure
    def g(x: Float) -> Int:
        return math.floor(x)
    """
    math_ns = load(src2)["math"]
    assert hasattr(math_ns, "floor") and not hasattr(math_ns, "__loader__")


def test_only_the_checked_ast_is_compiled():
    code = compile_t2(
        checked(
            """
    @procedure
    def f(x: Int) -> Int:
        y: Int = x * 2
        return y
    """
        ).program.t2
    )
    assert "@procedure" not in code.source and ": Int" not in code.source
    assert code.procedures == (("f", ("x",)),)


# ---- fuel ---------------------------------------------------------------------------------


INFINITE = """
@procedure
def spin(n: Int) -> Int:
    s = 0
    while True:
        s += n
    return s
"""


def test_an_infinite_while_runs_out_of_fuel_at_the_budget():
    fuel = T2Fuel(5_000)
    with pytest.raises(T2FuelExhausted) as err:
        load(INFINITE)["spin"](fuel, 1)
    assert err.value.code == "R620"
    assert "budget of 5000" in err.value.message
    assert fuel.left < 0


def test_the_default_budget_bounds_an_infinite_loop():
    with pytest.raises(T2FuelExhausted):
        run(INFINITE, "spin", 1)


def test_deep_recursion_is_r620_with_a_depth_message():
    src = """
    @procedure
    def down(n: Int) -> Int:
        if n == 0:
            return 0
        return 1 + down(n - 1)
    """
    assert run(src, "down", T2_MAX_DEPTH - 1) == T2_MAX_DEPTH - 1
    with pytest.raises(T2FuelExhausted) as err:
        run(src, "down", 10_000)
    assert err.value.code == "R620"
    assert f"deeper than {T2_MAX_DEPTH}" in err.value.message


def test_depth_is_restored_after_a_caught_error():
    src = """
    @procedure
    def boom(xs: list[Int]) -> Int:
        return xs[5]

    @procedure
    def safe(xs: list[Int]) -> Int:
        total = 0
        for _ in range(3):
            try:
                total += boom(xs)
            except IndexError:
                total += 1
        return total
    """
    fuel = T2Fuel(1000)
    assert load(src)["safe"](fuel, [1]) == 3
    assert fuel.depth == 0


def cost(src: str, proc: str, *args: object) -> int:
    fuel = T2Fuel(10**9)
    load(src)[proc](fuel, *args)
    return fuel.budget - fuel.left


def test_loop_iterations_and_calls_cost_one_each():
    src = """
    @procedure
    def f(n: Int) -> Int:
        s = 0
        for i in range(n):
            s += i
        return s
    """
    # enter (1) + range() call (1) + n iterations
    assert cost(src, "f", 10) == 12
    assert cost(src, "f", 20) == 22


def test_comprehension_elements_cost_one_each():
    src = """
    @procedure
    def f(xs: list[Int]) -> list[Int]:
        return [x + 1 for x in xs if x > 0]
    """
    assert cost(src, "f", [1] * 10) == 1 + 10
    assert cost(src, "f", [1] * 100) == 1 + 100
    nested = """
    @procedure
    def g(n: Int) -> Int:
        return len([i * j for i in range(n) for j in range(n)])
    """
    # enter + len + range(n) + n elements, and per outer element range(n) + n elements
    assert cost(nested, "g", 5) == 1 + 1 + 1 + 5 + 5 * (1 + 5)


def test_builtins_are_charged_per_element_they_consume():
    src = """
    @procedure
    def f(n: Int) -> Int:
        return sum(range(n))
    """
    assert cost(src, "f", 1000) == 1 + 1 + 1 + 1000  # enter, sum, range, n elements
    with pytest.raises(T2FuelExhausted):
        run(src, "f", 10**12)  # never runs the C loop: charged up front


def test_generator_arguments_are_charged_lazily():
    src = """
    @procedure
    def f(xs: list[Int]) -> Bool:
        return any(x > 2 for x in xs)
    """
    # the generator stops at the first match: 3 elements, not 1000
    assert cost(src, "f", [1, 2, 3] + [0] * 997) <= 1 + 1 + 3 + 3


def test_the_budget_is_configurable_and_shared_across_calls():
    src = """
    @procedure
    def f(n: Int) -> Int:
        s = 0
        for i in range(n):
            s += i
        return s
    """
    ns = load(src)
    fuel = T2Fuel(30)
    ns["f"](fuel, 10)  # 12 units
    ns["f"](fuel, 10)  # 24 units
    with pytest.raises(T2FuelExhausted):
        ns["f"](fuel, 10)
    assert run(src, "f", 10, budget=12) == 45
    with pytest.raises(T2FuelExhausted):
        run(src, "f", 10, budget=11)


def test_fuel_exhaustion_is_not_caught_by_except():
    src = """
    @procedure
    def f(n: Int) -> Int:
        try:
            while True:
                n += 1
        except ValueError:
            return -1
        return n
    """
    with pytest.raises(T2FuelExhausted):
        run(src, "f", 0, budget=100)


def test_extend_with_itself_keeps_python_semantics():
    src = """
    @procedure
    def f(xs: list[Int]) -> list[Int]:
        ys = list(xs)
        ys.extend(ys)
        return ys
    """
    assert run(src, "f", [1, 2]) == [1, 2, 1, 2]


def test_loading_twice_gives_independent_namespaces():
    src = """
    @procedure
    def f(x: Int) -> Int:
        return x
    """
    code = compile_t2(checked(src).program.t2)
    a, b = t2_load(code.source), t2_load(code.source)
    assert a["f"] is not b["f"]
