"""Semantics v1.1 on the Python target (spec 2026-09-28 go-target §3.1): Int is 64-bit
(overflow is R623), sets iterate in insertion order, and `Counter.most_common` keeps
ties in insertion order -- the same answers the Go runtime (lattice/gogen/rt) gives."""

import subprocess
import sys
from pathlib import Path

import pytest

from lattice.t2 import T2Fuel
from lattice.t2.support import T2Error, T2Set
from tests.t2.util import load

INT_MAX = 2**63 - 1
ROOT = Path(__file__).resolve().parents[2]


def run(src, fn, *args):
    return load(src)[fn](T2Fuel(100_000), *args)


def r623(src, fn, *args):
    with pytest.raises(T2Error) as e:
        run(src, fn, *args)
    return e.value.code


def test_overflow_is_r623():
    src = """
        @procedure
        def inc(x: Int) -> Int:
            return x + 1
    """
    assert run(src, "inc", 1) == 2
    with pytest.raises(T2Error) as e:
        run(src, "inc", INT_MAX)
    assert e.value.code == "R623"
    assert e.value.message == f"integer {INT_MAX + 1} is outside the 64-bit range"


@pytest.mark.parametrize(
    "body",
    ["return -x", "return abs(x)", "return x // -1", "return x - 1"],
)
def test_int_min_edges(body):
    src = f"""
        @procedure
        def f(x: Int) -> Int:
            {body}
    """
    assert r623(src, "f", -(2**63)) == "R623"


@pytest.mark.parametrize(
    "body",
    [
        "return x * x",
        "return x ** 3",
        "return x << 40",
        "y = x\n            y += x\n            return y",
        "y = x\n            y *= 3\n            return y",
        "y = x\n            y -= -x\n            return y",
        "ys = [x]\n            ys[0] += x\n            return ys[0]",
        "d = {'a': x}\n            d['a'] -= -x\n            return d['a']",
        "return sum([x, x, -x, -x, x, x])",
        "return int(float(x) * 4)",
        "return round(float(x) * 4)",
    ],
)
def test_every_int_producing_operation_is_range_checked(body):
    src = f"""
        @procedure
        def f(x: Int) -> Int:
            {body}
    """
    assert r623(src, "f", 2**62) == "R623"


def test_sum_checks_only_its_result():
    """Python's intermediate sums are exact: only the final value must fit."""
    src = """
        @procedure
        def f(x: Int) -> Int:
            return sum([x, x, -x, -x])
    """
    assert run(src, "f", INT_MAX) == 0


def test_int_parse_out_of_range_is_r623():
    src = """
        @procedure
        def p(s: Str) -> Int:
            return int(s)
    """
    assert run(src, "p", str(INT_MAX)) == INT_MAX
    with pytest.raises(T2Error) as e:
        run(src, "p", "9223372036854775808")
    assert e.value.code == "R623"


def test_in_range_values_are_unchanged():
    src = """
        @procedure
        def f(x: Int) -> list[Int]:
            y = x
            y += 1
            return [x + 1, x - 1, x * 2, x // 2, x % 7, -x, abs(-x), x ** 2, x << 3, y]
    """
    assert run(src, "f", 5) == [6, 4, 10, 2, 5, -5, 5, 25, 40, 6]


def test_set_order_is_insertion_order_across_hash_seeds():
    code = (
        "from tests.t2.util import load\n"
        "from lattice.t2 import T2Fuel\n"
        "ns = load('''\n"
        "@procedure\n"
        "def order(xs: list[Str]) -> list[Str]:\n"
        "    s = set(xs)\n"
        "    s.add('zzz')\n"
        "    t = {x for x in xs}\n"
        "    return [x for x in set(xs)] + list(s) + list(t) + list({'q', 'b', 'a'})\n"
        "''')\n"
        "print(ns['order'](T2Fuel(10_000), ['pear', 'apple', 'fig', 'kiwi', 'apple']))\n"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={"PYTHONHASHSEED": str(seed), "PYTHONPATH": str(ROOT)},
            check=True,
        ).stdout
        for seed in (1, 2, 3, 4)
    }
    want = ["pear", "apple", "fig", "kiwi"]
    assert outs == {f"{want + want + ['zzz'] + want + ['q', 'b', 'a']}\n"}


def test_set_algebra_orders_like_the_go_runtime():
    """Left operand's elements first, in its order; then the right's new ones in its
    order (lattice/gogen/rt/sets.go)."""
    src = """
        @procedure
        def f(a: list[Str], b: list[Str]) -> list[list[Str]]:
            s = set(a)
            t = set(b)
            u = set(a)
            u |= t
            v = set(a)
            v ^= t
            return [list(s | t), list(s & t), list(s - t), list(s ^ t),
                    list(s.union(b)), list(s.intersection(b)), list(s.difference(b)),
                    list(u), list(v)]
    """
    got = run(src, "f", ["d", "b", "a", "c"], ["c", "e", "a", "f"])
    assert got == [
        ["d", "b", "a", "c", "e", "f"],
        ["a", "c"],
        ["d", "b"],
        ["d", "b", "e", "f"],
        ["d", "b", "a", "c", "e", "f"],
        ["a", "c"],
        ["d", "b"],
        ["d", "b", "a", "c", "e", "f"],
        ["d", "b", "e", "f"],
    ]


def test_t2set_behaves_as_a_set():
    s = T2Set(["b", "a", "b"])
    assert list(s) == ["b", "a"] and len(s) == 2 and "a" in s and "z" not in s
    assert s == T2Set(["a", "b"]) and s == {"a", "b"} and s != T2Set(["a"])
    assert repr(s) == "{'b', 'a'}" and repr(T2Set()) == "set()" and str(s) == repr(s)
    s.discard("b")
    s.add("b")
    assert list(s) == ["a", "b"]
    with pytest.raises(KeyError):
        s.remove("zz")
    assert T2Set(["a"]) <= s and T2Set(["a"]) < s and s >= T2Set(["b"]) and not s > s
    assert s.issubset(["a", "b", "c"]) and s.issuperset(["a"])
    c = s.copy()
    c.clear()
    assert len(c) == 0 and len(s) == 2 and not c
    with pytest.raises(TypeError):
        hash(s)


def test_counter_most_common_ties_in_insertion_order():
    src = """
        from collections import Counter

        @procedure
        def top(xs: list[Str]) -> list[Str]:
            return [k for k, _ in Counter(xs).most_common()]

        @procedure
        def top2(xs: list[Str]) -> list[Str]:
            return [k for k, _ in Counter(xs).most_common(2)]
    """
    assert run(src, "top", ["b", "a", "b", "a", "c"]) == ["b", "a", "c"]
    assert run(src, "top", ["c", "b", "a", "a"]) == ["a", "c", "b"]
    assert run(src, "top2", ["c", "b", "a", "a"]) == ["a", "c"]
