"""Semantics cases of plan 5a Task 10 (the collections, math, datetime and json surface
of T2), in the shared suite (`cases.CASES`). Every `expect` was checked against real
Python 3.14 (tests/semantics_v11/test_python.py)."""

# ruff: noqa: E501 - data: long program texts and answers read better on one line

from __future__ import annotations

import datetime as dt

from tests.semantics_v11.cases import (  # noqa: F401 - cases.py imports this module after defining them
    DT,
    Case,
    D,
    Err,
    Exc,
    P,
)

N53 = 2**53
# A str of every character json.dumps escapes differently, and its JSON text.
_ESC = 'q"\\/\b\f\n\r\t\x00\x1f\x7f\u00e9\u20ac\U0001f44b\u2028'
_ESC_TEXT = r'"q\"\\/\b\f\n\r\t\u0000\u001f\u007f\u00e9\u20ac\ud83d\udc4b\u2028"'


def B(*lines: str) -> str:
    """A procedure body of several lines."""
    return "\n    ".join(lines)


COLL = [
    Case(
        "list_sort_key_sees_the_list_empty",
        P(
            "f(xs: list[Int]) -> list[Int]",
            B("ys = list(xs)", "ys.sort(key=lambda v: len(ys) - v)", "return ys"),
        ),
        "f",
        ([3, 1, 2],),
        [3, 2, 1],
    ),
    Case(
        "list_extend_itself",
        P("f(xs: list[Int]) -> list[Int]", B("ys = list(xs)", "ys.extend(ys)", "return ys")),
        "f",
        ([1, 2],),
        [1, 2, 1, 2],
    ),
    Case(
        "list_extend_generator_over_itself_runs_out_of_fuel",
        P(
            "f(xs: list[Int]) -> Int",
            B("ys = list(xs)", "ys.extend(y for y in ys)", "return len(ys)"),
        ),
        "f",
        ([1],),
        Err("R620"),
        budget=300,
    ),
    Case(
        "list_pop_positions",
        P(
            "f(xs: list[Int]) -> list[Int]",
            B("ys = list(xs)", "a = ys.pop(0)", "b = ys.pop(-1)", "return [a, b, len(ys)]"),
        ),
        "f",
        ([5, 6, 7, 8],),
        [5, 8, 2],
    ),
    Case(
        "list_pop_index_out_of_range",
        P("f(xs: list[Int]) -> Int", B("ys = list(xs)", "return ys.pop(5)")),
        "f",
        ([1],),
        Exc("IndexError"),
    ),
    Case(
        "list_insert_clamps",
        P(
            "f(xs: list[Int]) -> list[Int]",
            B("ys = list(xs)", "ys.insert(-100, 0)", "ys.insert(100, 9)", "return ys"),
        ),
        "f",
        ([1, 2],),
        [0, 1, 2, 9],
    ),
    Case(
        "list_remove_first_only",
        P("f(xs: list[Int]) -> list[Int]", B("ys = list(xs)", "ys.remove(2)", "return ys")),
        "f",
        ([1, 2, 3, 2],),
        [1, 3, 2],
    ),
    Case(
        "list_reverse_clear_copy",
        P(
            "f(xs: list[Int]) -> list[Int]",
            B(
                "ys = list(xs)",
                "zs = ys.copy()",
                "ys.reverse()",
                "zs.clear()",
                "return ys + zs + [len(zs)]",
            ),
        ),
        "f",
        ([1, 2, 3],),
        [3, 2, 1, 0],
    ),
    Case(
        "list_sort_plain_and_reverse",
        P(
            "f(xs: list[Int]) -> list[Int]",
            B(
                "ys = list(xs)",
                "zs = list(xs)",
                "ys.sort()",
                "zs.sort(reverse=True)",
                "return ys + zs",
            ),
        ),
        "f",
        ([3, 1, 2],),
        [1, 2, 3, 3, 2, 1],
    ),
    # an Int looked up among Floats is compared exactly, as == (never widened)
    Case(
        "exact_int_in_float_list",
        P("f(xs: list[Float], n: Int) -> list[Bool]", "return [n in xs, n - 1 in xs, n not in xs]"),
        "f",
        ([float(N53)], N53 + 1),
        [False, True, True],
    ),
    Case(
        "exact_int_in_mixed_tuple",
        P("f(n: Int, m: Int) -> Bool", "return n in (m, 1.5)"),
        "f",
        (N53, N53 + 1),
        False,
    ),
    Case(
        "exact_float_in_mixed_tuple",
        P("f(x: Float, m: Int) -> Bool", "return x in (m, 1.5)"),
        "f",
        (float(N53), N53 + 1),
        False,
    ),
    Case(
        "exact_int_in_float_set",
        P(
            "f(xs: list[Float], n: Int) -> list[Bool]",
            B("s = set(xs)", "return [n in s, n - 1 in s]"),
        ),
        "f",
        ([float(N53)], N53 + 1),
        [False, True],
    ),
    Case(
        "exact_int_list_count_index",
        P(
            "f(xs: list[Float], n: Int) -> list[Int]",
            "return [xs.count(n), xs.count(n - 1), xs.index(n - 1)]",
        ),
        "f",
        ([1.0, float(N53), float(N53)], N53 + 1),
        [0, 2, 1],
    ),
    Case(
        "exact_int_list_index_missing",
        P("f(xs: list[Float], n: Int) -> Int", "return xs.index(n)"),
        "f",
        ([float(N53)], N53 + 1),
        Exc("ValueError"),
    ),
    Case(
        "exact_int_dict_get",
        P(
            "f(n: Int) -> list[Int]",
            B(
                "d: dict[Float, Int] = {9007199254740992.0: 1}",
                "return [d.get(n, -1), d.get(n - 1, -1)]",
            ),
        ),
        "f",
        (N53 + 1,),
        [-1, 1],
    ),
    Case(
        "exact_int_dict_index_keyerror",
        P(
            "f(n: Int) -> Str",
            B(
                "d: dict[Float, Int] = {9007199254740992.0: 1}",
                "try:",
                "    return str(d[n])",
                "except KeyError as e:",
                "    return str(e)",
            ),
        ),
        "f",
        (N53 + 1,),
        "9007199254740993",
    ),
    Case(
        "exact_int_dict_pop_and_in",
        P(
            "f(n: Int) -> list[Int]",
            B(
                "d: dict[Float, Int] = {9007199254740992.0: 1, 3.0: 2}",
                "a = d.pop(n, -1)",
                "b = d.pop(3)",
                "return [a, b, len(d), 1 if n in d else 0, 1 if n - 1 in d else 0]",
            ),
        ),
        "f",
        (N53 + 1,),
        [-1, 2, 1, 0, 1],
    ),
    Case(
        "exact_int_set_remove_keyerror",
        P(
            "f(xs: list[Float], n: Int) -> Int",
            B("s = set(xs)", "s.discard(n)", "s.remove(n)", "return len(s)"),
        ),
        "f",
        ([float(N53)], N53 + 1),
        Exc("KeyError"),
    ),
    Case(
        "defaultdict_stores_an_int_key_widened",
        P(
            "f(n: Int) -> list[Str]",
            B(
                "d: dict[Float, list[Int]] = defaultdict(list)",
                "d[n].append(1)",
                "d[n].append(2)",
                "return [str(k) + ':' + str(len(v)) for k, v in d.items()]",
            ),
        ),
        "f",
        (3,),
        ["3.0:2"],
    ),
    Case(
        "counter_missing_key_reads_zero_without_storing",
        P(
            "f(xs: list[Str]) -> list[Int]",
            B("c = Counter(xs)", 'v = c["zz"]', "return [v, len(c), c['a']]"),
        ),
        "f",
        (["a", "b", "a"],),
        [0, 2, 2],
    ),
    Case(
        "counter_get_is_dict_get",
        P(
            "f(xs: list[Str]) -> list[Int]",
            B("c = Counter(xs)", 'return [c.get("a", -1), c.get("zz", -1)]'),
        ),
        "f",
        (["a"],),
        [1, -1],
    ),
    Case(
        "counter_update_adds_counts",
        P(
            "f(xs: list[Str]) -> list[tuple[Str, Int]]",
            B("c = Counter(xs)", 'c.update({"a": 2, "z": 5})', "return list(c.items())"),
        ),
        "f",
        (["a", "b"],),
        [("a", 3), ("b", 1), ("z", 5)],
    ),
    Case(
        "counter_update_of_an_empty_counter_copies",
        P(
            "f(xs: list[Str]) -> list[tuple[Str, Int]]",
            B("c = Counter(xs)", 'c.update({"a": 2, "b": 0})', "return list(c.items())"),
        ),
        "f",
        ([],),
        [("a", 2), ("b", 0)],
    ),
    Case(
        "counter_update_overflow_r623",
        P("f(n: Int) -> Int", B('c = Counter(["a"])', 'c.update({"a": n})', 'return c["a"]')),
        "f",
        (2**63 - 1,),
        Err("R623"),
    ),
    Case(
        "counter_equality_treats_missing_as_zero",
        P(
            "f(xs: list[Str]) -> list[Bool]",
            B(
                "a = Counter(xs)",
                "b = Counter(xs)",
                'b["q"] = 0',
                "d: dict[Str, Int] = dict(b.items())",
                "return [a == b, a == d]",
            ),
        ),
        "f",
        (["x", "y", "x"],),
        [True, False],
    ),
    Case(
        "counter_most_common",
        P(
            "f(xs: list[Str]) -> list[tuple[Str, Int]]",
            B("c = Counter(xs)", "return c.most_common(2) + c.most_common(-1)"),
        ),
        "f",
        (["b", "a", "c", "a", "c"],),
        [("a", 2), ("c", 2)],
    ),
    Case(
        "counter_of_a_dict_counts_its_values",
        P(
            "f() -> list[tuple[Str, Int]]",
            B('c = Counter({"a": 3, "b": 1})', 'c["a"] += 1', "return c.most_common()"),
        ),
        "f",
        (),
        [("a", 4), ("b", 1)],
    ),
    Case(
        "counter_union_through_dict_variables",
        P(
            "f(xs: list[Str], ys: list[Str]) -> list[tuple[Str, Int]]",
            B(
                "a: dict[Str, Int] = Counter(xs)",
                "b: dict[Str, Int] = Counter(ys)",
                'b["neg"] = -1',
                "u = a | b",
                "a |= b",
                "return list(u.items()) + list(a.items()) + [('zero', u['absent'])]",
            ),
        ),
        "f",
        (["a", "b", "b"], ["a", "a", "c"]),
        [("a", 2), ("b", 2), ("c", 1), ("a", 2), ("b", 2), ("c", 1), ("zero", 0)],
    ),
    Case(
        "counter_or_plain_dict_is_a_plain_dict",
        P(
            "f(xs: list[Str]) -> Str",
            B(
                "a: dict[Str, Int] = Counter(xs)",
                'b = {"z": 1}',
                "u = a | b",
                "try:",
                '    return str(u["absent"])',
                "except KeyError:",
                '    return "plain"',
            ),
        ),
        "f",
        (["a"],),
        "plain",
    ),
    Case(
        "defaultdict_union_and_copy_keep_the_factory",
        P(
            "f(xs: list[Str]) -> list[Int]",
            B(
                "d: dict[Str, list[Int]] = defaultdict(list)",
                "for i, x in enumerate(xs):",
                "    d[x].append(i)",
                'p = {"q": [9]}',
                "u = p | d",
                "c = d.copy()",
                'return [len(u["new"]), len(c["new2"]), len(u), len(c), len(d)]',
            ),
        ),
        "f",
        (["a", "b", "a"],),
        [0, 0, 4, 3, 2],
    ),
    Case(
        "defaultdict_get_does_not_store",
        P(
            "f() -> list[Int]",
            B(
                "d: dict[Str, Int] = defaultdict(int)",
                'd["a"] += 2',
                'v = d.get("b", -1)',
                'return [v, len(d), d["a"]]',
            ),
        ),
        "f",
        (),
        [-1, 1, 2],
    ),
    Case(
        "defaultdict_factories",
        P(
            "f() -> list[Str]",
            B(
                "a: dict[Str, Float] = defaultdict(float)",
                "b: dict[Str, Str] = defaultdict(str)",
                "c: dict[Str, set[Int]] = defaultdict(set)",
                'c["x"].add(1)',
                'return [str(a["k"]), "[" + b["k"] + "]", str(len(c["x"]))]',
            ),
        ),
        "f",
        (),
        ["0.0", "[]", "1"],
    ),
    Case(
        "dict_views_are_live",
        P(
            "f() -> list[Str]",
            B(
                'd = {"a": 1}',
                "ks = d.keys()",
                "vs = d.values()",
                'd["b"] = 2',
                "return list(ks) + [str(v) for v in vs]",
            ),
        ),
        "f",
        (),
        ["a", "b", "1", "2"],
    ),
    Case(
        "dict_items_view_and_membership",
        P(
            "f() -> list[Bool]",
            B(
                'd = {"a": 1, "b": 2}',
                'return ["a" in d.keys(), 2 in d.values(), ("b", 2) in d.items(), ("b", 3) in d.items()]',
            ),
        ),
        "f",
        (),
        [True, True, True, False],
    ),
    Case(
        "sorted_dict_view_charged_up_front",
        P("f() -> list[Str]", B('d = {"b": 1, "a": 2}', "return sorted(d.keys())")),
        "f",
        (),
        ["a", "b"],
    ),
    Case(
        "dict_setdefault_returns_the_value",
        P(
            "f() -> list[Int]",
            B(
                'd = {"a": 1}',
                'x = d.setdefault("a", 5)',
                'y = d.setdefault("b", 7)',
                "return [x, y, len(d)]",
            ),
        ),
        "f",
        (),
        [1, 7, 2],
    ),
    Case(
        "dict_update_itself_and_copy",
        P(
            "f() -> list[Int]",
            B(
                'd = {"a": 1}',
                "d.update(d)",
                "c = d.copy()",
                'c["b"] = 2',
                "return [len(d), len(c)]",
            ),
        ),
        "f",
        (),
        [1, 2],
    ),
    Case(
        "dict_get_of_optional_values",
        P(
            "f() -> list[Bool]",
            B(
                'd: dict[Str, Int | None] = {"a": None, "b": 1}',
                'return [d.get("a") is None, d.get("z") is None, d.get("b") == 1]',
            ),
        ),
        "f",
        (),
        [True, True, True],
    ),
    Case(
        "dict_literal_with_spread",
        P(
            "f() -> list[Str]",
            B(
                'a = {"x": 1, "y": 2}',
                'b = {"y": 20, **a, "z": 3}',
                "return [k + str(v) for k, v in b.items()]",
            ),
        ),
        "f",
        (),
        ["y2", "x1", "z3"],
    ),
    Case(
        "dict_clear_while_iterating_a_copy",
        P(
            "f() -> Int",
            B('d = {"a": 1, "b": 2}', "for k in d.copy():", "    d.clear()", "return len(d)"),
        ),
        "f",
        (),
        0,
    ),
    # a dict (or set) whose keys are replaced while it is iterated: CPython's layout
    # decides where its iterator stops or raises (rt/omap.go reproduces it)
    Case(
        "dict_keys_replaced_while_iterating_stop",
        P(
            "f() -> list[Str]",
            B(
                'd = {"a": 0, "b": 0, "c": 0}',
                "seen: list[Str] = []",
                "for k in d:",
                "    seen.append(k)",
                "    d.pop(k)",
                '    d[k + "x"] = 1',
                "return seen + list(d)",
            ),
        ),
        "f",
        (),
        ["a", "b", "c", "ax", "bx", "cx"],
    ),
    Case(
        "dict_keys_replaced_while_iterating_raises",
        P(
            "f() -> Int",
            B(
                'd = {"a": 0, "b": 0, "c": 0}',
                "n = 0",
                "for k in d:",
                "    n += 1",
                "    if n == 1:",
                "        d.pop(k)",
                '        d[k + "x"] = 1',
                "return n",
            ),
        ),
        "f",
        (),
        Exc("RuntimeError"),
    ),
    Case(
        "big_dict_literal_keys_replaced_while_iterating",
        P(
            "f() -> list[Int]",
            B(
                "d = {" + ", ".join(f"{i}: 0" for i in range(20)) + "}",
                "seen: list[Int] = []",
                "for k in d:",
                "    seen.append(k)",
                "    if k % 3 == 0:",
                "        d.pop(k)",
                "        d[k + 100] = 1",
                "return seen",
            ),
        ),
        "f",
        (),
        Exc("RuntimeError"),
    ),
    Case(
        "set_elements_replaced_while_iterating",
        P(
            "f(xs: list[Int]) -> list[Int]",
            B(
                "s = set(xs)",
                "seen: list[Int] = []",
                "for x in s:",
                "    seen.append(x)",
                "    s.discard(x)",
                "    s.add(x + 10)",
                "return seen",
            ),
        ),
        "f",
        ([1, 2, 3],),
        [1, 2, 3],
    ),
    Case(
        "set_methods_with_iterables",
        P(
            "f(xs: list[Int], ys: list[Int]) -> list[Int]",
            B(
                "s = set(xs)",
                "s.update(y * 2 for y in ys)",
                "u = s.union(ys)",
                "i = s.intersection(ys)",
                "d = s.difference(y for y in ys)",
                "c = s.copy()",
                "c.clear()",
                "return list(s) + [-1] + list(u) + [-1] + list(i) + [-1] + list(d) + [len(c)]",
            ),
        ),
        "f",
        ([1, 2, 3], [2, 5]),
        [1, 2, 3, 4, 10, -1, 1, 2, 3, 4, 10, 5, -1, 2, -1, 1, 3, 4, 10, 0],
    ),
    Case(
        "set_issuperset_stops_early",
        P(
            "f(xs: list[Int], ys: list[Int]) -> list[Bool]",
            B(
                "s = set(xs)",
                "return [s.issuperset(y for y in ys), s.issubset(ys + xs), s.issuperset(ys[0:0])]",
            ),
        ),
        "f",
        ([1, 2], [9, 1, 2, 3, 4, 5]),
        [False, True, True],
    ),
    Case(
        "set_of_a_dict_and_of_a_counter",
        P(
            "f(xs: list[Str]) -> list[Str]",
            B(
                'd = {"b": 1, "a": 2}',
                "s = set(d)",
                "t = set(Counter(xs))",
                "return list(s) + list(t)",
            ),
        ),
        "f",
        (["z", "y", "z"],),
        ["b", "a", "z", "y"],
    ),
    Case(
        "counter_of_a_string_and_a_generator",
        P(
            "f(s: Str) -> list[tuple[Str, Int]]",
            B(
                "a = Counter(s)",
                "b = Counter(c.upper() for c in s)",
                "return a.most_common(1) + b.most_common(1)",
            ),
        ),
        "f",
        ("abca",),
        [("a", 2), ("A", 2)],
    ),
]

MATH = [
    Case(
        "math_floor_ceil_trunc_of_ints_and_floats",
        P(
            "f(n: Int, x: Float) -> list[Int]",
            "return [math.floor(n), math.ceil(n), math.trunc(n), math.floor(x), math.ceil(x), math.trunc(x)]",
        ),
        "f",
        (-7, -2.5),
        [-7, -7, -7, -3, -2, -2],
    ),
    Case(
        "math_floor_inf",
        P("f(x: Float) -> Int", "return math.floor(x)"),
        "f",
        (float("inf"),),
        Exc("OverflowError"),
    ),
    Case(
        "math_ceil_nan",
        P("f(x: Float) -> Int", "return math.ceil(x)"),
        "f",
        (float("nan"),),
        Exc("ValueError"),
    ),
    Case(
        "math_floor_out_of_range_r623",
        P("f(x: Float) -> Int", "return math.floor(x)"),
        "f",
        (1e19,),
        Err("R623"),
    ),
    Case(
        "math_log_errors_caught",
        P(
            "f(x: Float, n: Int) -> list[Str]",
            B(
                "out: list[Str] = []",
                "for v in [x, 0.0, -1.0]:",
                "    try:",
                "        out.append(str(math.log(v)))",
                "    except ValueError as e:",
                "        out.append(str(e))",
                "try:",
                "    out.append(str(math.log(n)))",
                "except ValueError as e:",
                "    out.append(str(e))",
                "return out",
            ),
        ),
        "f",
        (8.0, -3),
        [
            "2.0794415416798357",
            "expected a positive input, got 0.0",
            "expected a positive input, got -1.0",
            "expected a positive input",
        ],  # fmt: skip
    ),
    Case(
        "math_log_bases_and_log10",
        P(
            "f(x: Float, n: Int) -> list[Float]",
            "return [math.log(x, 2), math.log(n, 10), math.log10(n), math.log(x, n)]",
        ),
        "f",
        (8.0, 1000),
        [3.0, 2.9999999999999996, 3.0, 0.3010299956639812],
    ),
    Case(
        "math_log_base_one",
        P("f(x: Float) -> Float", "return math.log(x, 1)"),
        "f",
        (8.0,),
        Exc("ZeroDivisionError"),
    ),
    Case(
        "math_sqrt_negative_message",
        P(
            "f(n: Int) -> Str",
            B(
                "try:",
                "    return str(math.sqrt(n))",
                "except ValueError as e:",
                "    return str(e)",
            ),
        ),
        "f",
        (-4,),
        "expected a nonnegative input, got -4.0",
    ),
    Case(
        "math_exp_values",
        P(
            "f(x: Float) -> list[Str]",
            B(
                "return [str(math.exp(x)), str(math.exp(-1000.0)), str(math.exp(0)), str(math.exp(-x))]",
            ),
        ),
        "f",
        (1.0,),
        ["2.718281828459045", "0.0", "1.0", "0.36787944117144233"],
    ),
    Case(
        "math_exp_overflow_escapes",
        P("f(x: Float) -> Float", "return math.exp(x)"),
        "f",
        (1000.0,),
        Exc("OverflowError"),
    ),
    Case(
        "math_pow_special_values",
        P(
            "f(x: Float, n: Int) -> list[Float]",
            "return [math.pow(x, 0.5), math.pow(n, 2), math.pow(-x, 3.0), math.pow(0.0, 0.0), math.pow(x, x * 1e9 - x * 1e9)]",
        ),
        "f",
        (2.0, -3),
        [1.4142135623730951, 9.0, -8.0, 1.0, 1.0],
    ),
    Case(
        "math_pow_domain",
        P("f(x: Float) -> Float", "return math.pow(x, 0.5)"),
        "f",
        (-2.0,),
        Exc("ValueError"),
    ),
    Case(
        "math_pow_zero_negative",
        P("f(x: Float) -> Float", "return math.pow(x, -1.0)"),
        "f",
        (0.0,),
        Exc("ValueError"),
    ),
    Case(
        "math_pow_overflow",
        P("f(x: Float) -> Float", "return math.pow(x, 10000.0)"),
        "f",
        (2.0,),
        Exc("OverflowError"),
    ),
    Case(
        "math_fabs_isclose_gcd",
        P(
            "f(x: Float, a: Int, b: Int) -> list[Str]",
            "return [str(math.fabs(-x)), str(math.fabs(a)), str(math.isclose(x, x + 1e-10)), "
            "str(math.isclose(x, x + 1e-3)), str(math.isclose(a, a)), str(math.gcd(a, b)), str(math.gcd(0, 0))]",
        ),
        "f",
        (1.5, -12, 18),
        ["1.5", "12.0", "True", "False", "True", "6", "0"],
    ),
    Case(
        "math_gcd_of_int_min_r623",
        P("f(a: Int) -> Int", "return math.gcd(a, 0)"),
        "f",
        (-(2**63),),
        Err("R623"),
    ),
    Case(
        "math_constants_exact",
        P("f() -> list[Float]", "return [math.pi, math.e, math.inf, -math.inf]"),
        "f",
        (),
        [3.141592653589793, 2.718281828459045, float("inf"), float("-inf")],
    ),
]

TIME_MORE = [
    Case(
        "duration_str_and_repr",
        P(
            "f() -> list[Str]",
            B(
                "a = timedelta(days=-1, seconds=5)",
                "b = timedelta(days=2) + seconds(0.000005)",
                "c = timedelta()",
                'return [str(a), str(b), str(c), f"{a!r}", f"{c!r}", f"{hours(25)}"]',
            ),
        ),
        "f",
        (),
        [
            "-1 day, 0:00:05",
            "2 days, 0:00:00.000005",
            "0:00:00",
            "datetime.timedelta(days=-1, seconds=5)",
            "datetime.timedelta(0)",
            "1 day, 1:00:00",
        ],  # fmt: skip
    ),
    Case(
        "duration_normalisation_attributes",
        P("f(n: Int) -> list[Int]", B("d = seconds(n)", "return [d.days, d.seconds]")),
        "f",
        (-1,),
        [-1, 86399],
    ),
    Case(
        "duration_float_arguments_round_half_even",
        P(
            "f() -> list[Str]",
            "return [str(seconds(0.0000005)), str(seconds(0.0000015)), str(seconds(0.0000025)), "
            "str(days(1.5)), str(timedelta(hours=1.5, minutes=0.5, weeks=0.1))]",
        ),
        "f",
        (),
        ["0:00:00", "0:00:00.000002", "0:00:00.000002", "1 day, 12:00:00", "18:18:30"],
    ),
    Case(
        "duration_arithmetic",
        P(
            "f(a: Duration, b: Duration) -> list[Str]",
            "return [str(a + b), str(a - b), str(-a), str(a * 3), str(3 * a), str(a * 0.5), str(a / 4), "
            "str(a / 0.3), str(a / b), str(a // b), str(a // 7), str(a % b), str(+b)]",
        ),
        "f",
        (dt.timedelta(hours=10, microseconds=3), dt.timedelta(hours=3)),
        [
            "13:00:00.000003",
            "7:00:00.000003",
            "-1 day, 13:59:59.999997",
            "1 day, 6:00:00.000009",
            "1 day, 6:00:00.000009",
            "5:00:00.000002",
            "2:30:00.000001",
            "1 day, 9:20:00.000010",
            "3.3333333336111113",
            "3",
            "1:25:42.857143",
            "1:00:00.000003",
            "3:00:00",
        ],  # fmt: skip,
    ),
    Case(
        "duration_compare_sort_and_truth",
        P(
            "f(a: Duration, b: Duration) -> list[Bool]",
            B(
                "z = a - a",
                "return [a < b, a >= b, a == b, max(a, b) == b, True if z else False, True if a else False, sorted([b, a])[0] == a]",
            ),
        ),
        "f",
        (dt.timedelta(days=-1), dt.timedelta(seconds=1)),
        [True, False, False, True, False, True, True],
    ),
    Case(
        "duration_overflow",
        P("f(n: Int) -> Duration", "return days(n) * 2"),
        "f",
        (999999999,),
        Exc("OverflowError"),
    ),
    Case(
        "duration_days_beyond_c_int",
        P("f(n: Int) -> Duration", "return days(n)"),
        "f",
        (2**40,),
        Exc("OverflowError"),
    ),
    Case(
        "duration_division_by_zero",
        P("f(a: Duration) -> Duration", "return a / 0"),
        "f",
        (dt.timedelta(1),),
        Exc("ZeroDivisionError"),
    ),
    Case(
        "duration_nan_argument",
        P("f(x: Float) -> Duration", "return hours(x)"),
        "f",
        (float("nan"),),
        Exc("ValueError"),
    ),
    Case(
        "duration_total_seconds_exact",
        P("f(a: Duration) -> Float", "return a.total_seconds()"),
        "f",
        (dt.timedelta(days=999999, microseconds=1),),
        86399913600.000001,
    ),
    Case(
        "date_errors_caught",
        P(
            "f(y: Int, m: Int, d: Int) -> list[Str]",
            B(
                "out: list[Str] = []",
                "for args in [(y, m, d), (y, 13, 1), (0, 1, 1), (2023, 2, 29)]:",
                "    try:",
                "        out.append(date(args[0], args[1], args[2]).isoformat())",
                "    except ValueError as e:",
                "        out.append(str(e))",
                "return out",
            ),
        ),
        "f",
        (2024, 2, 29),
        [
            "2024-02-29",
            "month must be in 1..12, not 13",
            "year must be in 1..9999, not 0",
            "day 29 must be in range 1..28 for month 2 in year 2023",
        ],  # fmt: skip
    ),
    Case(
        "date_huge_year_overflow",
        P("f(y: Int) -> Date", "return date(y, 1, 1)"),
        "f",
        (2**40,),
        Exc("OverflowError"),
    ),
    Case(
        "date_arithmetic_and_bounds",
        P(
            "f(d: Date) -> list[Str]",
            "return [str(d + hours(47)), str(d - hours(1)), str(d - date(2000, 1, 1)), str(days(1) + d), f'{d!r}']",
        ),
        "f",
        (dt.date(2024, 3, 1),),
        [
            "2024-03-02",
            "2024-03-01",
            "8826 days, 0:00:00",
            "2024-03-02",
            "datetime.date(2024, 3, 1)",
        ],
    ),
    Case(
        "date_out_of_range",
        P("f(d: Date) -> Date", "return d + days(1)"),
        "f",
        (dt.date(9999, 12, 31),),
        Exc("OverflowError"),
    ),
    Case(
        "date_strftime_every_directive",
        P(
            "f(d: Date) -> Str",
            'return d.strftime("%Y|%m|%d|%H|%M|%S|%f|%y|%a|%A|%b|%B|%j|%p|%%|é")',
        ),
        "f",
        (dt.date(999, 1, 2),),
        "0999|01|02|00|00|00|000000|99|Wed|Wednesday|Jan|January|002|AM|%|é",
    ),
    Case(
        "date_in_sets_dicts_and_sorted",
        P(
            "f(a: Date, b: Date) -> list[Str]",
            B(
                "s = {a, b, a}",
                "d = {a: 1}",
                "d[b] = 2",
                "return [str(len(s)), str(d[a]), str(sorted([b, a])[0]), str(a < b)]",
            ),
        ),
        "f",
        (dt.date(2024, 1, 2), dt.date(2023, 5, 6)),
        ["2", "1", "2023-05-06", "False"],
    ),
    Case(
        "datetime_text_and_attributes",
        P(
            "f(t: DateTime) -> list[Str]",
            'return [str(t), f"{t!r}", t.isoformat(), f"{t:%H.%M.%S.%f %p}", f"{t}", str(t.microsecond), '
            "str(t.weekday()), str(t.date())]",
        ),
        "f",
        (dt.datetime(2024, 3, 15, 23, 5, 0, 120, tzinfo=dt.UTC),),
        [
            "2024-03-15 23:05:00.000120+00:00",
            "datetime.datetime(2024, 3, 15, 23, 5, 0, 120, tzinfo=datetime.timezone.utc)",
            "2024-03-15T23:05:00.000120+00:00",
            "23.05.00.000120 PM",
            "2024-03-15 23:05:00.000120+00:00",
            "120",
            "4",
            "2024-03-15",
        ],  # fmt: skip
    ),
    Case(
        "datetime_arithmetic",
        P(
            "f(a: DateTime, b: DateTime) -> list[Str]",
            "return [str(a - b), str(b - a), str(a + minutes(90.5)), str(a - weeks_of(b)), str(a.timestamp())]".replace(
                "weeks_of(b)", "timedelta(weeks=1)"
            ),
        ),
        "f",
        (
            dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
            dt.datetime(2023, 12, 31, 12, 0, 0, 1, tzinfo=dt.UTC),
        ),
        [
            "11:59:59.999999",
            "-1 day, 12:00:00.000001",
            "2024-01-01 01:30:30+00:00",
            "2023-12-25 00:00:00+00:00",
            "1704067200.0",
        ],  # fmt: skip,
    ),
    Case(
        "datetime_out_of_range",
        P("f(t: DateTime) -> DateTime", "return t + seconds(1)"),
        "f",
        (dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC),),
        Exc("OverflowError"),
    ),
    Case(
        "datetime_timestamp_before_epoch",
        P("f(t: DateTime) -> Float", "return t.timestamp()"),
        "f",
        (dt.datetime(1, 1, 1, 0, 0, 0, 1, tzinfo=dt.UTC),),
        -62135596799.999999,
    ),
    Case(
        "returns_date_datetime_duration",
        P(
            "f(t: DateTime) -> tuple[Date, DateTime, Duration]",
            "return (t.date(), t + hours(1), t - t + seconds(1.25))",
        ),
        "f",
        (dt.datetime(2024, 3, 15, 13, 45, 7, tzinfo=dt.UTC),),
        (
            dt.date(2024, 3, 15),
            dt.datetime(2024, 3, 15, 14, 45, 7, tzinfo=dt.UTC),
            dt.timedelta(seconds=1.25),
        ),
    ),
]

JSON_MORE = [
    Case(
        "loads_error_messages",
        P(
            "f(xs: list[Str]) -> list[Str]",
            B(
                "out: list[Str] = []",
                "for s in xs:",
                "    try:",
                "        out.append(json.dumps(json.loads(s)))",
                "    except ValueError as e:",
                "        out.append(str(e))",
                "return out",
            ),
        ),
        "f",
        (["[1,]", '{"a" 1}', "", '"abc', "[1] x", "\n\n  {", '"\\ud800"', "1" * 4400, "NaN"],),
        [
            "Illegal trailing comma before end of array: line 1 column 3 (char 2)",
            "Expecting ':' delimiter: line 1 column 6 (char 5)",
            "Expecting value: line 1 column 1 (char 0)",
            "Unterminated string starting at: line 1 column 1 (char 0)",
            "Extra data: line 1 column 5 (char 4)",
            "Expecting property name enclosed in double quotes: line 3 column 4 (char 5)",
            '"\\ud800"',
            "Exceeds the limit (4300 digits) for integer string conversion: value has 4400 digits; "
            "use sys.set_int_max_str_digits() to increase the limit",
            "NaN",
        ],
    ),
    Case(
        "json_values_str_repr_truth",
        P(
            "f(s: Str) -> list[Str]",
            B(
                "j = json.loads(s)",
                'return [str(j), f"{j!r}", str(j["a"]), str(j["b"][1]), str(True if j["c"] else False), '
                'str(j["d"] is None), f"{j[\'e\']:>5}", str(j["a"] == 1.0)]',
            ),
        ),
        "f",
        ('{"a": 1, "b": [true, "x\'y"], "c": {}, "d": null, "e": "hi"}',),
        [
            "{'a': 1, 'b': [True, \"x'y\"], 'c': {}, 'd': None, 'e': 'hi'}",
            "{'a': 1, 'b': [True, \"x'y\"], 'c': {}, 'd': None, 'e': 'hi'}",
            "1",
            "x'y",
            "False",
            "True",
            "   hi",
            "True",
        ],  # fmt: skip,
    ),
    Case(
        "json_index_errors",
        P(
            "f(s: Str) -> list[Str]",
            B(
                "j = json.loads(s)",
                "out: list[Str] = []",
                'for k in ["z", "a"]:',
                "    try:",
                "        out.append(str(j[k]))",
                "    except KeyError as e:",
                "        out.append('KeyError ' + str(e))",
                "try:",
                '    out.append(str(j["a"][5]))',
                "except IndexError as e:",
                "    out.append(str(e))",
                "return out",
            ),
        ),
        "f",
        ('{"a": [1, 2]}',),
        ["KeyError 'z'", "[1, 2]", "list index out of range"],
    ),
    Case(
        "json_index_type_error_escapes",
        P("f(s: Str) -> Json", B("j = json.loads(s)", 'return j["a"]')),
        "f",
        ("[1, 2]",),
        Exc("TypeError"),
    ),
    Case(
        "json_equality_is_pythons",
        P(
            "f(a: Str, b: Str) -> list[Bool]",
            B(
                "x = json.loads(a)",
                "y = json.loads(b)",
                "return [x == y, x[0] == 1, x[0] == True, x == x, x[1] == 1]",
            ),
        ),
        "f",
        ("[1.0, [2]]", "[1, [2.0]]"),
        [True, True, True, True, False],
    ),
    Case(
        "json_argument_and_result",
        P("f(j: Json) -> Json", B("return [j, j, None]")),
        "f",
        ({"k": [1, 2.5, None, True, "é"]},),
        [{"k": [1, 2.5, None, True, "é"]}, {"k": [1, 2.5, None, True, "é"]}, None],
    ),
    Case(
        "dumps_shares_the_value",
        P(
            "f(n: Int) -> Str",
            B("xs = [n]", "j: Json = xs", "xs.append(n + 1)", "return json.dumps(j)"),
        ),
        "f",
        (1,),
        "[1, 2]",
    ),
    Case(
        "dumps_indent_zero_negative_and_sorted",
        P(
            "f(s: Str) -> list[Str]",
            B(
                "d = json.loads(s)",
                "return [json.dumps(d, indent=0), json.dumps(d, indent=-3, sort_keys=True), "
                "json.dumps(d, sort_keys=True, indent=1)]",
            ),
        ),
        "f",
        ('{"b": [1, {}], "a": []}',),
        [
            '{\n"b": [\n1,\n{}\n],\n"a": []\n}',
            '{\n"a": [],\n"b": [\n1,\n{}\n]\n}',
            '{\n "a": [],\n "b": [\n  1,\n  {}\n ]\n}',
        ],  # fmt: skip
    ),
    Case(
        "dumps_of_tuples_optionals_and_counters",
        P(
            "f(xs: list[Str], n: Int | None) -> Str",
            B(
                "c: dict[Str, Int] = Counter(xs)",
                'return json.dumps([(1, "a"), n, c, -0.0, 1e308 * 10])',
            ),
        ),
        "f",
        (["b", "a", "b"], None),
        '[[1, "a"], null, {"b": 2, "a": 1}, -0.0, Infinity]',
    ),
    Case(
        "dumps_escapes",
        P("f(s: Str) -> Str", "return json.dumps({s: s})"),
        "f",
        (_ESC,),
        "{" + _ESC_TEXT + ": " + _ESC_TEXT + "}",
    ),
    Case(
        "dumps_charges_nodes_and_size",
        P(
            "f(n: Int) -> Int",
            B("xs = [(i, str(i)) for i in range(n)]", "return len(json.dumps(xs, indent=4))"),
        ),
        "f",
        (50,),
        1882,
        budget=1000,
    ),
    Case(
        "loads_big_int_and_float_text",
        P("f(s: Str) -> Str", B("j = json.loads(s)", 'return json.dumps(j) + " " + str(j)')),
        "f",
        ("[123456789012345678901234567890, 1E400, -0, 0.5e-3]",),
        "[123456789012345678901234567890, Infinity, 0, 0.0005] [123456789012345678901234567890, inf, 0, 0.0005]",
    ),
    Case(
        # T2Fuel.dumps charges each node as it counts it: a Json holding itself runs out
        # of fuel (it used to walk forever)
        "dumps_of_a_list_holding_itself_runs_out_of_fuel",
        P(
            "f(n: Int) -> Str",
            B("xs: list[Json] = []", "xs.append(n)", "xs.append(xs)", "return json.dumps(xs)"),
        ),
        "f",
        (1,),
        Err("R620"),
        budget=5000,
    ),
    Case(
        "loads_duplicate_keys_keep_the_first_place",
        P("f(s: Str) -> Str", "return json.dumps(json.loads(s))"),
        "f",
        ('{"a": 1, "b": 2, "a": 3}',),
        '{"a": 3, "b": 2}',
    ),
]


# The constructs Go still refuses (L892; docs/lattice-language-rules.md §6): each case runs
# on Python, and tests/semantics_v11/test_go.py checks Go refuses exactly these, by prefix.
GO_GATED_PREFIXES = ("repr_container_", "strftime_gated_", "re_sub_gated_")
GATED = [
    Case(
        "repr_container_list",
        P("f(xs: list[Int]) -> Str", "return str(xs)"),
        "f",
        ([1, 2],),
        "[1, 2]",
    ),
    Case(
        "repr_container_fstring_dict",
        P("f(n: Int) -> Str", 'd = {"a": n}\n    return f"{d}!"'),
        "f",
        (1,),
        "{'a': 1}!",
    ),
    Case(
        "strftime_gated_unsupported_directive",
        P("f(t: DateTime) -> Str", 'return t.strftime("%c")'),
        "f",
        (dt.datetime(2024, 3, 15, 13, 45, 7, tzinfo=dt.UTC),),
        "Fri Mar 15 13:45:07 2024",
    ),
    Case(
        "strftime_gated_non_literal_format",
        P("f(t: Date, fmt: Str) -> Str", "return t.strftime(fmt)"),
        "f",
        (dt.date(2024, 3, 15), "%Y"),
        "2024",
    ),
    Case(
        "strftime_gated_fstring_spec",
        P("f(t: Date) -> Str", 'return f"{t:%U}"'),
        "f",
        (dt.date(2024, 3, 15),),
        "10",
    ),
    Case(
        "re_sub_gated_non_literal_replacement",
        P("f(s: Str, r: Str) -> Str", 'return re.sub("a", r, s)'),
        "f",
        ("banana", "o"),
        "bonono",
    ),
]
