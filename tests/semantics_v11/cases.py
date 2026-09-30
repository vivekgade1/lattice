"""The shared v1.1 semantics suite (spec 2026-09-28 go-target §3.1): ~150 small
@procedure cases, each pinning one piece of Lattice's meaning to what Python 3.14
actually returns. `run_python` runs a case on the Python target; Task 8's Go runner
runs the same `CASES` and must get the same `expect`.

Every `expect` value here was verified against real Python 3.14 output (Step 3);
see task-6-report.md for the corrections made to the brief's excerpt values.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from datetime import date, datetime
from textwrap import dedent

from lattice.t2 import T2Fuel
from lattice.t2.support import T2Error
from tests.t2.util import load

INT_MAX = 2**63 - 1
INT_MIN = -(2**63)


@dataclass(frozen=True)
class Err:
    """A Lattice runtime diagnostic escaping a procedure (its code, e.g. "R623")."""

    code: str


@dataclass(frozen=True)
class Exc:
    """A plain Python exception escaping a procedure (its class name, e.g. "KeyError")."""

    kind: str


@dataclass(frozen=True)
class Case:
    name: str
    source: str
    proc: str
    args: tuple[object, ...]
    expect: object
    budget: int = 100_000

    @property
    def source_with_header(self) -> str:
        """Task 8's Go runner needs the whole, final program text."""
        return "# lattice: v1\n" + dedent(self.source).lstrip("\n")


# ---- source builder -----------------------------------------------------------------

_IMPORT_RULES: tuple[tuple[_re.Pattern[str], str], ...] = (
    (_re.compile(r"\bre\."), "import re"),
    (_re.compile(r"\bjson\."), "import json"),
    (_re.compile(r"\bmath\."), "import math"),
)


def P(sig: str, body: str) -> str:
    """`"@procedure\\ndef " + sig + ":\\n    " + body`, with the stdlib import lines the
    body needs added automatically (it mentions `re.`, `json.`, `math.`, `Counter(`,
    `defaultdict(`, `timedelta(` or `date(`)."""
    lines: list[str] = [line for pattern, line in _IMPORT_RULES if pattern.search(body)]
    collections_names = [
        name
        for name in ("Counter", "defaultdict")
        if _re.search(rf"\b{name}\(", body)
    ]
    if collections_names:
        lines.append(f"from collections import {', '.join(collections_names)}")
    datetime_names = [
        name for name in ("timedelta", "date") if _re.search(rf"\b{name}\(", body)
    ]
    if datetime_names:
        lines.append(f"from datetime import {', '.join(datetime_names)}")
    header = "".join(f"{line}\n" for line in lines)
    if header:
        header += "\n"
    return f"{header}@procedure\ndef {sig}:\n    {body}\n"


def D(s: str) -> date:
    """An ISO date string, parsed once here (not inside the Lattice program: T2 has no
    way to parse a Date/DateTime string; a value of that type can only arrive as an
    argument)."""
    return date.fromisoformat(s)


def DT(s: str) -> datetime:
    """An ISO datetime string, parsed the same way (see `D`)."""
    return datetime.fromisoformat(s)


# ---- groups ---------------------------------------------------------------------

INT = [
    Case("int_add_overflow", P("f(x: Int) -> Int", "return x + 1"), "f", (INT_MAX,), Err("R623")),
    Case("int_neg_min", P("f(x: Int) -> Int", "return -x"), "f", (INT_MIN,), Err("R623")),
    Case("int_floordiv_neg", P("f(a: Int, b: Int) -> Int", "return a // b"), "f", (-7, 2), -4),
    Case("int_mod_neg", P("f(a: Int, b: Int) -> Int", "return a % b"), "f", (-7, 2), 1),
    Case("int_mod_neg_divisor", P("f(a: Int, b: Int) -> Int", "return a % b"), "f", (7, -2), -1),
    Case("int_div_zero", P("f(a: Int) -> Int", "return a // 0"), "f", (1,), Exc("ZeroDivisionError")),
    Case("int_pow_overflow", P("f(a: Int) -> Int", "return a ** 63"), "f", (2,), Err("R623")),
    Case(
        "int_parse_big",
        P("f(s: Str) -> Int", "return int(s)"),
        "f",
        ("9223372036854775808",),
        Err("R623"),
    ),
    Case("int_parse_space", P("f(s: Str) -> Int", "return int(s)"), "f", (" 42 ",), 42),
    Case("int_parse_bad", P("f(s: Str) -> Int", "return int(s)"), "f", ("4x",), Exc("ValueError")),
    Case("true_div_is_float", P("f(a: Int, b: Int) -> Float", "return a / b"), "f", (7, 2), 3.5),
    Case("round_half_even", P("f(x: Float) -> Int", "return round(x)"), "f", (2.5,), 2),
    Case("int_sub_overflow", P("f(x: Int) -> Int", "return x - 1"), "f", (INT_MIN,), Err("R623")),
    Case("int_abs_overflow", P("f(x: Int) -> Int", "return abs(x)"), "f", (INT_MIN,), Err("R623")),
    Case(
        "int_lshift_overflow",
        P("f(x: Int) -> Int", "return x << 40"),
        "f",
        (2**30,),
        Err("R623"),
    ),
    Case("int_invert", P("f(x: Int) -> Int", "return ~x"), "f", (5,), -6),
    Case("int_bitwise_and", P("f(a: Int, b: Int) -> Int", "return a & b"), "f", (0b1100, 0b1010), 0b1000),
    Case("int_bitwise_or", P("f(a: Int, b: Int) -> Int", "return a | b"), "f", (0b1100, 0b1010), 0b1110),
    Case("int_bitwise_xor", P("f(a: Int, b: Int) -> Int", "return a ^ b"), "f", (0b1100, 0b1010), 0b0110),
    Case("int_pow_zero", P("f(x: Int) -> Int", "return x ** 0"), "f", (0,), 1),
    Case("int_floordiv_pos", P("f(a: Int, b: Int) -> Int", "return a // b"), "f", (7, 2), 3),
    Case("int_rshift", P("f(x: Int) -> Int", "return x >> 2"), "f", (20,), 5),
    Case(
        "int_floordiv_min_by_neg_one",
        P("f(a: Int, b: Int) -> Int", "return a // b"),
        "f",
        (INT_MIN, -1),
        Err("R623"),
    ),
    Case(
        "sum_final_overflow",
        P("f(xs: list[Int]) -> Int", "return sum(xs)"),
        "f",
        ([INT_MAX, 1],),
        Err("R623"),
    ),
    Case(
        # Python's intermediate sums are exact (unbounded ints): only the final
        # value is range-checked, so a partial sum that would itself overflow
        # Int64 (INT_MAX + 1, after the first two elements) does not fail here,
        # as long as the final result (INT_MAX again) fits.
        "sum_intermediate_overflow_final_fits",
        P("f(xs: list[Int]) -> Int", "return sum(xs)"),
        "f",
        ([INT_MAX, 1, -1],),
        INT_MAX,
    ),
]
FLOAT_TEXT = [  # str() and f"{x}" of floats: Python repr
    Case(f"float_str_{i}", P("f(x: Float) -> Str", "return str(x)"), "f", (v,), s)
    for i, (v, s) in enumerate(
        [
            (1.0, "1.0"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (1000000.0, "1000000.0"),
            (-0.0, "-0.0"),
            (0.0001, "0.0001"),
            (1e-05, "1e-05"),
            (0.1 + 0.2, "0.30000000000000004"),
            (float("inf"), "inf"),
            (float("nan"), "nan"),
        ]
    )
] + [
    Case(
        "fstr_spec",
        P("f(x: Float) -> Str", 'return f"{x:.2f}|{x:>8.1f}|{x:e}"'),
        "f",
        (3.14159,),
        "3.14|     3.1|3.141590e+00",
    ),
    Case("float_floordiv", P("f(a: Float, b: Float) -> Float", "return a // b"), "f", (7.5, 2.0), 3.0),
    Case("float_mod", P("f(a: Float, b: Float) -> Float", "return a % b"), "f", (7.5, 2.0), 1.5),
    Case(
        "float_nan_not_equal_self",
        P("f(x: Float) -> Bool", "return x != x"),
        "f",
        (float("nan"),),
        True,
    ),
    Case("math_sqrt", P("f(x: Float) -> Float", "return math.sqrt(x)"), "f", (2.0,), 1.4142135623730951),
    Case("math_floor_negative", P("f(x: Float) -> Int", "return math.floor(x)"), "f", (-1.5,), -2),
    Case("math_ceil_negative", P("f(x: Float) -> Int", "return math.ceil(x)"), "f", (-1.5,), -1),
    Case("math_trunc_negative", P("f(x: Float) -> Int", "return math.trunc(x)"), "f", (-1.7,), -1),
    Case(
        "math_isclose",
        P("f(a: Float, b: Float) -> Bool", "return math.isclose(a, b)"),
        "f",
        (0.1 + 0.2, 0.3),
        True,
    ),
    Case("math_log_default", P("f(x: Float) -> Float", "return math.log(x)"), "f", (1.0,), 0.0),
    Case("math_pow", P("f(a: Float, b: Float) -> Float", "return math.pow(a, b)"), "f", (2.0, 10.0), 1024.0),
    Case("float_parse_sci", P("f(s: Str) -> Float", "return float(s)"), "f", ("1e3",), 1000.0),
]
STR = [
    Case("len_emoji", P("f(s: Str) -> Int", "return len(s)"), "f", ("héllo👋",), 6),
    Case("slice_runes", P("f(s: Str) -> Str", "return s[1:4]"), "f", ("héllo👋",), "éll"),
    Case("neg_index", P("f(s: Str) -> Str", "return s[-1]"), "f", ("héllo👋",), "👋"),
    Case("step_slice", P("f(s: Str) -> Str", "return s[::-2]"), "f", ("abcdef",), "fdb"),
    Case("upper_sz", P("f(s: Str) -> Str", "return s.upper()"), "f", ("straße",), "STRASSE"),
    Case("lower_dotted_i", P("f(s: Str) -> Str", "return s.lower()"), "f", ("İ",), "i̇"),
    Case("casefold", P("f(s: Str) -> Str", "return s.casefold()"), "f", ("Straße",), "strasse"),
    Case(
        "title",
        P("f(s: Str) -> Str", "return s.title()"),
        "f",
        ("hello wORLD o'neil",),
        "Hello World O'Neil",
    ),
    Case(
        "split_none", P("f(s: Str) -> list[Str]", "return s.split()"), "f", ("  a \t b\n",), ["a", "b"]
    ),
    Case("strip_chars", P("f(s: Str) -> Str", 'return s.strip("xy")'), "f", ("xyaxy",), "a"),
    Case("isdigit_unicode", P("f(s: Str) -> Bool", "return s.isdigit()"), "f", ("²",), True),
    Case("index_error", P("f(s: Str) -> Str", "return s[10]"), "f", ("abc",), Exc("IndexError")),
    Case("str_find", P("f(s: Str) -> Int", 'return s.find("lo")'), "f", ("hello",), 3),
    Case("str_find_not_found", P("f(s: Str) -> Int", 'return s.find("zz")'), "f", ("hello",), -1),
    Case(
        "str_replace_count_limit",
        P("f(s: Str) -> Str", 'return s.replace("a", "X", 2)'),
        "f",
        ("banana",),
        "bXnXna",
    ),
    Case("str_count", P("f(s: Str) -> Int", 'return s.count("ana")'), "f", ("banana",), 1),
    Case("str_join", P("f(xs: list[Str]) -> Str", 'return ", ".join(xs)'), "f", (["a", "b", "c"],), "a, b, c"),
    Case(
        "str_partition",
        P("f(s: Str) -> list[Str]", 'a, b, c = s.partition("=")\n    return [a, b, c]'),
        "f",
        ("key=value",),
        ["key", "=", "value"],
    ),
    Case(
        "str_partition_not_found",
        P("f(s: Str) -> list[Str]", 'a, b, c = s.partition("=")\n    return [a, b, c]'),
        "f",
        ("novalue",),
        ["novalue", "", ""],
    ),
    Case("str_zfill_negative", P("f(s: Str) -> Str", "return s.zfill(6)"), "f", ("-42",), "-00042"),
    Case("str_ljust_fill", P("f(s: Str) -> Str", 'return s.ljust(6, "*")'), "f", ("ab",), "ab****"),
    Case("str_center_odd_padding", P("f(s: Str) -> Str", 'return s.center(6, "*")'), "f", ("ab",), "**ab**"),
    Case("str_startswith", P("f(s: Str) -> Bool", 'return s.startswith("he")'), "f", ("hello",), True),
    Case("str_endswith", P("f(s: Str) -> Bool", 'return s.endswith("lo")'), "f", ("hello",), True),
    Case(
        "str_splitlines",
        P("f(s: Str) -> list[Str]", "return s.splitlines()"),
        "f",
        ("a\nb\r\nc",),
        ["a", "b", "c"],
    ),
    Case("str_capitalize", P("f(s: Str) -> Str", "return s.capitalize()"), "f", ("hELLO world",), "Hello world"),
]
LIST = [
    Case(
        "slice_copies",
        P(
            "f(xs: list[Int]) -> list[Int]",
            "ys = list(xs[0:2])\n    ys.append(9)\n    return xs",
        ),
        "f",
        ([1, 2, 3],),
        [1, 2, 3],
    ),
    Case(
        "alias_mutates",
        P("f(n: Int) -> list[Int]", "a = [1]\n    b = a\n    b.append(n)\n    return a"),
        "f",
        (2,),
        [1, 2],
    ),
    Case("neg_index_list", P("f(xs: list[Int]) -> Int", "return xs[-2]"), "f", ([1, 2, 3],), 2),
    Case(
        "sorted_key_stable",
        P("f(xs: list[Str]) -> list[Str]", "return sorted(xs, key=lambda s: len(s))"),
        "f",
        (["bb", "a", "cc", "d"],),
        ["a", "d", "bb", "cc"],
    ),
    Case(
        "pop_empty",
        P("f(n: Int) -> Int", "xs: list[Int] = []\n    return xs.pop()"),
        "f",
        (0,),
        Exc("IndexError"),
    ),
    Case(
        "list_extend",
        P("f(xs: list[Int]) -> list[Int]", "ys = list(xs)\n    ys.extend([9, 9])\n    return ys"),
        "f",
        ([1, 2],),
        [1, 2, 9, 9],
    ),
    Case(
        "list_insert",
        P("f(xs: list[Int]) -> list[Int]", "ys = list(xs)\n    ys.insert(1, 9)\n    return ys"),
        "f",
        ([1, 2, 3],),
        [1, 9, 2, 3],
    ),
    Case(
        "list_remove_missing",
        P("f(xs: list[Int]) -> list[Int]", "ys = list(xs)\n    ys.remove(9)\n    return ys"),
        "f",
        ([1, 2, 3],),
        Exc("ValueError"),
    ),
    Case(
        "list_sort_reverse_stable",
        P(
            "f(xs: list[Str]) -> list[Str]",
            "ys = list(xs)\n    ys.sort(key=lambda s: len(s), reverse=True)\n    return ys",
        ),
        "f",
        (["bb", "a", "cc", "d"],),
        ["bb", "cc", "a", "d"],
    ),
    Case(
        "list_index_not_found",
        P("f(xs: list[Int]) -> Int", "return xs.index(9)"),
        "f",
        ([1, 2, 3],),
        Exc("ValueError"),
    ),
    Case("list_count", P("f(xs: list[Int]) -> Int", "return xs.count(2)"), "f", ([1, 2, 2, 3, 2],), 3),
    Case("list_mult", P("f(xs: list[Int]) -> list[Int]", "return xs * 3"), "f", ([1, 2],), [1, 2, 1, 2, 1, 2]),
    Case(
        "list_index_assign",
        P("f(xs: list[Int]) -> list[Int]", "ys = list(xs)\n    ys[-1] = 99\n    return ys"),
        "f",
        ([1, 2, 3],),
        [1, 2, 99],
    ),
]
DICT_SET = [
    Case(
        "dict_order",
        P(
            "f(n: Int) -> list[Str]",
            'd = {"b": 1, "a": 2}\n    d["c"] = n\n    return list(d.keys())',
        ),
        "f",
        (3,),
        ["b", "a", "c"],
    ),
    Case(
        "set_order",
        P("f(xs: list[Str]) -> list[Str]", "return list(set(xs))"),
        "f",
        (["pear", "apple", "pear", "fig"],),
        ["pear", "apple", "fig"],
    ),
    Case(
        "key_error",
        P("f(k: Str) -> Int", 'd = {"a": 1}\n    return d[k]'),
        "f",
        ("z",),
        Exc("KeyError"),
    ),
    Case(
        "counter_ties",
        P(
            "f(xs: list[Str]) -> list[Str]",
            "return [k for k, _ in Counter(xs).most_common()]",
        ),
        "f",
        (["b", "a", "b", "a", "c"],),
        ["b", "a", "c"],
    ),
    Case(
        "defaultdict_list",
        P(
            "f(xs: list[Str]) -> list[Int]",
            "d: dict[Str, list[Int]] = defaultdict(list)\n"
            "    for i, x in enumerate(xs):\n"
            "        d[x].append(i)\n"
            "    return d[xs[0]]",
        ),
        "f",
        (["a", "b", "a"],),
        [0, 2],
    ),
    Case(
        "dict_get_default",
        P("f(k: Str) -> Int", 'd = {"a": 1}\n    return d.get(k, -1)'),
        "f",
        ("z",),
        -1,
    ),
    Case(
        "dict_setdefault",
        P(
            "f() -> list[Int]",
            'd: dict[Str, Int] = {"a": 1}\n'
            '    d.setdefault("a", 9)\n'
            '    d.setdefault("b", 9)\n'
            '    return [d["a"], d["b"]]',
        ),
        "f",
        (),
        [1, 9],
    ),
    Case(
        "dict_update",
        P(
            "f() -> list[Int]",
            'd: dict[Str, Int] = {"a": 1, "b": 2}\n'
            '    d.update({"b": 20, "c": 3})\n'
            '    return [d["a"], d["b"], d["c"]]',
        ),
        "f",
        (),
        [1, 20, 3],
    ),
    Case(
        "dict_pop_default",
        P("f() -> Int", 'd = {"a": 1}\n    return d.pop("z", -1)'),
        "f",
        (),
        -1,
    ),
    Case(
        "dict_pop_missing_keyerror",
        P("f() -> Int", 'd: dict[Str, Int] = {"a": 1}\n    return d.pop("z")'),
        "f",
        (),
        Exc("KeyError"),
    ),
    Case(
        "set_union_method",
        P("f(a: list[Str], b: list[Str]) -> list[Str]", "return list(set(a).union(b))"),
        "f",
        (["x", "y"], ["y", "z"]),
        ["x", "y", "z"],
    ),
    Case(
        "set_intersection_method",
        P("f(a: list[Str], b: list[Str]) -> list[Str]", "return list(set(a).intersection(b))"),
        "f",
        (["x", "y", "z"], ["y", "z", "w"]),
        ["y", "z"],
    ),
    Case(
        "set_difference_method",
        P("f(a: list[Str], b: list[Str]) -> list[Str]", "return list(set(a).difference(b))"),
        "f",
        (["x", "y", "z"], ["y"]),
        ["x", "z"],
    ),
    Case(
        "set_issubset",
        P("f(a: list[Str], b: list[Str]) -> Bool", "return set(a).issubset(b)"),
        "f",
        (["x", "y"], ["x", "y", "z"]),
        True,
    ),
    Case(
        "set_discard_missing_noop",
        P("f(xs: list[Str]) -> list[Str]", 's = set(xs)\n    s.discard("zz")\n    return list(s)'),
        "f",
        (["a", "b"],),
        ["a", "b"],
    ),
    Case(
        "set_remove_missing_keyerror",
        P("f(xs: list[Str]) -> list[Str]", 's = set(xs)\n    s.remove("zz")\n    return list(s)'),
        "f",
        (["a", "b"],),
        Exc("KeyError"),
    ),
]

TIME = [
    Case(
        "dt_plus_hours",
        P("f(dt: DateTime) -> Str", "return (dt + hours(3)).isoformat()"),
        "f",
        (DT("2024-03-15T10:00:00+00:00"),),
        "2024-03-15T13:00:00+00:00",
    ),
    Case(
        "dt_minus_dt_total_seconds",
        P("f(a: DateTime, b: DateTime) -> Float", "return (a - b).total_seconds()"),
        "f",
        (DT("2024-03-15T12:00:00+00:00"), DT("2024-03-15T10:30:00+00:00")),
        5400.0,
    ),
    Case(
        "duration_negative_days_seconds",
        P(
            "f() -> Float",
            "d = timedelta(days=-1, seconds=-5)\n    return d.total_seconds()",
        ),
        "f",
        (),
        -86405.0,
    ),
    Case(
        "dt_isoformat_no_micro",
        P("f(dt: DateTime) -> Str", "return dt.isoformat()"),
        "f",
        (DT("2024-01-01T00:00:00+00:00"),),
        "2024-01-01T00:00:00+00:00",
    ),
    Case(
        "dt_isoformat_micro",
        P("f(dt: DateTime) -> Str", "return dt.isoformat()"),
        "f",
        (DT("2024-01-01T00:00:00.123456+00:00"),),
        "2024-01-01T00:00:00.123456+00:00",
    ),
    Case(
        "date_isoformat",
        P("f(d: Date) -> Str", "return d.isoformat()"),
        "f",
        (D("2024-02-29"),),
        "2024-02-29",
    ),
    Case(
        "date_weekday",
        P("f(d: Date) -> Int", "return d.weekday()"),
        "f",
        (D("2024-03-15"),),
        4,
    ),
    Case(
        "date_isoweekday",
        P("f(d: Date) -> Int", "return d.isoweekday()"),
        "f",
        (D("2024-03-15"),),
        5,
    ),
    Case(
        "strftime_ymd_hms",
        P("f(dt: DateTime) -> Str", 'return dt.strftime("%Y-%m-%d %H:%M:%S")'),
        "f",
        (DT("2024-03-15T13:45:07+00:00"),),
        "2024-03-15 13:45:07",
    ),
    Case(
        "strftime_a_b_j",
        P("f(dt: DateTime) -> Str", 'return dt.strftime("%a %b %j")'),
        "f",
        (DT("2024-03-15T13:45:07+00:00"),),
        "Fri Mar 075",
    ),
    Case(
        "strftime_p_y",
        P("f(dt: DateTime) -> Str", 'return dt.strftime("%p %y")'),
        "f",
        (DT("2024-03-15T13:45:07+00:00"),),
        "PM 24",
    ),
    Case(
        "timedelta_kwargs_weeks",
        P("f() -> Float", "d = timedelta(weeks=2)\n    return d.total_seconds()"),
        "f",
        (),
        1209600.0,
    ),
    Case(
        "date_constructor",
        P("f() -> Str", "d = date(2024, 3, 15)\n    return d.isoformat()"),
        "f",
        (),
        "2024-03-15",
    ),
    Case(
        "dt_date",
        P("f(dt: DateTime) -> Str", "return dt.date().isoformat()"),
        "f",
        (DT("2024-03-15T13:45:07+00:00"),),
        "2024-03-15",
    ),
    Case(
        "dt_timestamp",
        P("f(dt: DateTime) -> Float", "return dt.timestamp()"),
        "f",
        (DT("2024-03-15T13:45:07+00:00"),),
        1710510307.0,
    ),
    Case(
        "date_minus_date_days",
        P("f(a: Date, b: Date) -> Int", "return (a - b).days"),
        "f",
        (D("2024-03-15"), D("2024-03-01")),
        14,
    ),
    Case(
        "date_plus_duration",
        P("f(d: Date) -> Str", "return (d + days(10)).isoformat()"),
        "f",
        (D("2024-03-25"),),
        "2024-04-04",
    ),
    Case(
        "dt_attrs_year_month_day",
        P(
            "f(dt: DateTime) -> list[Int]",
            "return [dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second]",
        ),
        "f",
        (DT("2024-03-15T13:45:07+00:00"),),
        [2024, 3, 15, 13, 45, 7],
    ),
]

REGEX = [
    Case(
        "fullmatch_named_group",
        P(
            "f(s: Str) -> Str",
            'm = re.fullmatch(r"(?P<word>[a-z]+)", s)\n'
            "    if m is None:\n"
            '        return ""\n'
            '    w = m.group("word")\n'
            "    if w is None:\n"
            '        return ""\n'
            "    return w",
        ),
        "f",
        ("hello",),
        "hello",
    ),
    Case(
        "match_prefix_only",
        P(
            "f(s: Str) -> Str",
            'm = re.match(r"[0-9]+", s)\n'
            "    if m is None:\n"
            '        return ""\n'
            "    return m.group()",
        ),
        "f",
        ("123abc",),
        "123",
    ),
    Case(
        "search_start_end_codepoints",
        P(
            "f(s: Str) -> list[Int]",
            'm = re.search(r"wor", s)\n'
            "    if m is None:\n"
            "        return [-1, -1]\n"
            "    return [m.start(), m.end()]",
        ),
        "f",
        ("héllo world",),
        [6, 9],
    ),
    Case(
        "sub_backref_1",
        P("f(s: Str) -> Str", r'return re.sub(r"(\w+)@(\w+)", r"\2@\1", s)'),
        "f",
        ("alice@example",),
        "example@alice",
    ),
    Case(
        "sub_named_g",
        P(
            "f(s: Str) -> Str",
            r'return re.sub(r"(?P<a>[a-z]+)-(?P<b>[a-z]+)", r"\g<b>-\g<a>", s)',
        ),
        "f",
        ("foo-bar",),
        "bar-foo",
    ),
    Case(
        "sub_dollar_literal",
        P("f(s: Str) -> Str", 'return re.sub(r"x", "$1", s)'),
        "f",
        ("x",),
        "$1",
    ),
    Case(
        "findall_no_group",
        P("f(s: Str) -> list[Str]", 'return re.findall(r"[0-9]+", s)'),
        "f",
        ("a1 b22 c333",),
        ["1", "22", "333"],
    ),
    Case(
        "findall_one_group",
        P("f(s: Str) -> list[Str]", r'return re.findall(r"([a-z]+)\d+", s)'),
        "f",
        ("a1 bb22 ccc333",),
        ["a", "bb", "ccc"],
    ),
    Case(
        "split_keeps_empty",
        P("f(s: Str) -> list[Str]", 'return re.split(r",", s)'),
        "f",
        (",a,,b,",),
        ["", "a", "", "b", ""],
    ),
    Case(
        "digit_class_ascii_only",
        P("f(s: Str) -> list[Str]", r'return re.findall(r"\d+", s)'),
        "f",
        ("12٣45",),  # ٣ = Arabic-indic three: not ASCII, must not join the runs
        ["12", "45"],
    ),
    Case(
        "fullmatch_no_match_is_none",
        P("f(s: Str) -> Bool", 'm = re.fullmatch(r"[0-9]+", s)\n    return m is None'),
        "f",
        ("12a",),
        True,
    ),
    Case(
        "search_none_when_absent",
        P("f(s: Str) -> Bool", 'm = re.search(r"zz", s)\n    return m is None'),
        "f",
        ("hello",),
        True,
    ),
    Case(
        "findall_empty_list_when_no_match",
        P("f(s: Str) -> list[Str]", 'return re.findall(r"[0-9]+", s)'),
        "f",
        ("abc",),
        [],
    ),
]

# Task 9 (strings and regular expressions on Go): the Str methods' edge cases, and the
# places Python's `re` differs from Go's RE2 (every expected value is Python 3.14's).
STR_MORE = [
    Case("str_split_sep_maxsplit", P("f(s: Str) -> list[Str]", 'return s.split(",", 2)'), "f", ("a,b,c,d",), ["a", "b", "c,d"]),
    Case("str_split_sep_keeps_empty", P("f(s: Str) -> list[Str]", 'return s.split(",")'), "f", (",a,,b,",), ["", "a", "", "b", ""]),
    Case("str_split_negative_maxsplit", P("f(s: Str) -> list[Str]", 'return s.split(",", -1)'), "f", ("a,b,c",), ["a", "b", "c"]),
    Case("str_split_empty_separator", P("f(s: Str) -> list[Str]", 'return s.split("")'), "f", ("abc",), Exc("ValueError")),
    Case("str_rsplit_maxsplit", P("f(s: Str) -> list[Str]", 'return s.rsplit(",", 1)'), "f", ("a,b,c",), ["a,b", "c"]),
    Case("str_rsplit_whitespace", P("f(s: Str) -> list[Str]", "return s.rsplit()"), "f", ("  a b\u3000 c ",), ["a", "b", "c"]),
    Case(
        "str_splitlines_all_boundaries",
        P("f(s: Str) -> list[Str]", "return s.splitlines()"),
        "f",
        ("a\x0bb\x0cc\x1cd\x1de\x1ef\x85g\u2028h\u2029i\rj\r\n\nk\n",),
        ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "", "k"],
    ),
    Case("str_splitlines_empty", P("f(s: Str) -> list[Str]", "return s.splitlines()"), "f", ("",), []),
    Case(
        "str_rpartition",
        P("f(s: Str) -> list[Str]", 'a, b, c = s.rpartition("=")\n    return [a, b, c]'),
        "f",
        ("a=b=c",),
        ["a=b", "=", "c"],
    ),
    Case(
        "str_rpartition_not_found",
        P("f(s: Str) -> list[Str]", 'a, b, c = s.rpartition("=")\n    return [a, b, c]'),
        "f",
        ("abc",),
        ["", "", "abc"],
    ),
    Case("str_rfind_codepoints", P("f(s: Str) -> Int", 'return s.rfind("l")'), "f", ("héllo héllo",), 9),
    Case("str_find_after_emoji", P("f(s: Str) -> Int", 'return s.find("b")'), "f", ("👋ab",), 2),
    Case("str_count_empty", P("f(s: Str) -> Int", 'return s.count("")'), "f", ("héllo",), 6),
    Case("str_replace_empty_old", P("f(s: Str) -> Str", 'return s.replace("", "-")'), "f", ("abc",), "-a-b-c-"),
    Case("str_replace_zero_count", P("f(s: Str) -> Str", 'return s.replace("a", "b", 0)'), "f", ("aaa",), "aaa"),
    Case("str_zfill_plus", P("f(s: Str) -> Str", "return s.zfill(4)"), "f", ("+7",), "+007"),
    Case("str_zfill_wider_input", P("f(s: Str) -> Str", "return s.zfill(3)"), "f", ("12345",), "12345"),
    Case("str_center_bias", P("f(s: Str) -> Str", "return s.center(5)"), "f", ("ab",), "  ab "),
    Case("str_center_odd_length", P("f(s: Str) -> Str", 'return s.center(6, "*")'), "f", ("abc",), "*abc**"),
    Case("str_rjust_codepoints", P("f(s: Str) -> Str", 'return s.rjust(3, "👋")'), "f", ("é",), "👋👋é"),
    Case("str_ljust_bad_fill", P("f(s: Str) -> Str", 'return s.ljust(5, "ab")'), "f", ("x",), Exc("TypeError")),
    Case("str_center_width_r622", P("f(n: Int) -> Str", 'return "a".center(n)'), "f", (10**8,), Err("R622")),
    Case("str_strip_unicode_space", P("f(s: Str) -> Str", "return s.strip()"), "f", ("\u3000a b\xa0",), "a b"),
    Case("str_lstrip_rstrip_chars", P("f(s: Str) -> Str", 'return s.lstrip("xy") + "|" + s.rstrip("yx")'), "f", ("xyaxy",), "axy|xya"),
    Case(
        "str_predicates",
        P(
            "f(s: Str) -> list[Bool]",
            "return [s.isalpha(), s.isalnum(), s.isdigit(), s.isspace(), s.islower(), s.isupper()]",
        ),
        "f",
        ("héllo2",),
        [False, True, False, False, True, False],
    ),
    Case("str_isspace_empty", P("f(s: Str) -> Bool", "return s.isspace()"), "f", ("",), False),
    Case("str_title_digits", P("f(s: Str) -> Str", "return s.title()"), "f", ("hello2world ǆx",), "Hello2World ǅx"),
    Case("str_lower_final_sigma", P("f(s: Str) -> Str", "return s.lower()"), "f", ("ΟΔΟΣ ΣΑ",), "οδος σα"),
    Case("str_startswith_empty", P("f(s: Str) -> Bool", 'return s.startswith("") and s.endswith("")'), "f", ("",), True),
    Case(
        "str_join_generator",
        P("f(xs: list[Str]) -> Str", 'return "-".join(x.upper() for x in xs)'),
        "f",
        (["a", "b", "c"],),
        "A-B-C",
    ),
    Case("str_join_chars", P("f(s: Str) -> Str", 'return ".".join(s)'), "f", ("hé👋",), "h.é.👋"),
    Case(
        "str_constants",
        P(
            "f(n: Int) -> Str",
            "return string.digits + string.ascii_lowercase[:n] + string.punctuation[:n]",
        ).replace("@procedure", "import string\n\n@procedure", 1),
        "f",
        (3,),
        "0123456789abc!\"#",
    ),
]

_RE_REPR = P("f(s: Str) -> Str", 'return str(re.fullmatch(r"(?s).+", s))')

REGEX_MORE = [
    # fix round 1: findall/split follow the groups (R3/R9); Match repr is %.50R; a repeat
    # of millions of iterations (Go's first engine overflowed its stack at ~1M)
    Case(
        "re_findall_two_groups_tuples",
        P(
            "f(s: Str) -> list[Str]",
            'return [a + "|" + b for a, b in re.findall(r"(\\w)(\\d)?", s)]',
        ),
        "f",
        ("a1 b c2",),
        ["a|1", "b|", "c|2"],
    ),
    Case(
        "re_split_groups_none",
        P("f(s: Str) -> list[Str]", 'return [x or "<None>" for x in re.split(r"(,)|;", s)]'),
        "f",
        ("a,b;c",),
        ["a", ",", "b", "<None>", "c"],
    ),
    Case("re_match_repr_cut_60", _RE_REPR, "f", ("x" * 60,),
         "<re.Match object; span=(0, 60), match='" + "x" * 49 + ">"),
    Case("re_match_repr_exactly_50", _RE_REPR, "f", ("x" * 48,),
         "<re.Match object; span=(0, 48), match='" + "x" * 48 + "'>"),
    Case("re_match_repr_49", _RE_REPR, "f", ("x" * 49,),
         "<re.Match object; span=(0, 49), match='" + "x" * 49 + ">"),
    Case("re_match_repr_quotes_escapes", _RE_REPR, "f", ("it's\n" + "y" * 50,),
         "<re.Match object; span=(0, 55), match=\"it's\\n" + "y" * 43 + ">"),
    Case("re_match_repr_non_ascii", _RE_REPR, "f", ("é" * 55,),
         "<re.Match object; span=(0, 55), match='" + "é" * 49 + ">"),
    Case(
        "re_deep_repeat_end_to_end",
        P(
            "f(n: Int) -> list[Str]",
            's = "ab" * n\n    m = re.fullmatch(r"(?:ab)*", s)\n'
            '    g = re.fullmatch(r"(a|b)*", s)\n'
            "    if m is None or g is None:\n        return []\n"
            '    return [str(m.end()), g.group(1) or "", str(len(re.findall(r"(?:ab)+c?", s)))]',
        ),
        "f",
        (1_500_000,),
        ["3000000", "b", "1"],
        budget=2_000_000,
    ),
]

REGEX_MORE += [
    Case(
        "re_dollar_before_trailing_newline",
        P("f(s: Str) -> Bool", 'return re.search(r"a$", s) is not None'),
        "f",
        ("a\n",),
        True,
    ),
    Case("re_findall_empty_matches", P("f(s: Str) -> list[Str]", 'return re.findall(r"a*", s)'), "f", ("baac",), ["", "aa", "", ""]),
    Case("re_findall_alternative_after_empty", P("f(s: Str) -> list[Str]", 'return re.findall(r"|a", s)'), "f", ("a",), ["", "a", ""]),
    Case("re_sub_empty_matches", P("f(s: Str) -> Str", 'return re.sub(r"x*", "-", s)'), "f", ("abc",), "-a-b-c-"),
    Case("re_split_empty_matches", P("f(s: Str) -> list[Str]", 'return re.split(r"x*", s)'), "f", ("ab",), ["", "a", "b", ""]),
    Case("re_space_class_has_vt", P("f(s: Str) -> list[Str]", r'return re.findall(r"\s", s)'), "f", ("a\x0bb",), ["\x0b"]),
    Case("re_ignorecase_is_ascii", P("f(s: Str) -> Bool", 'return re.fullmatch(r"(?i)é", s) is None'), "f", ("É",), True),
    Case("re_ignorecase_letters", P("f(s: Str) -> Bool", 'return re.fullmatch(r"(?i)abc", s) is not None'), "f", ("AbC",), True),
    Case(
        "re_unmatched_group_is_none",
        P(
            "f(s: Str) -> Bool",
            'm = re.search(r"(a)(b)?", s)\n    if m is None:\n        return False\n    return m.group(2) is None',
        ),
        "f",
        ("xa",),
        True,
    ),
    Case(
        "re_group_no_such_group",
        P(
            "f(s: Str) -> Str",
            'm = re.search(r"(a)", s)\n    if m is None:\n        return ""\n    return m.group(3) or ""',
        ),
        "f",
        ("a",),
        Exc("IndexError"),
    ),
    Case(
        "re_group_no_such_name_caught",
        P(
            "f(s: Str) -> Str",
            'm = re.search(r"(?P<x>a)", s)\n    if m is None:\n        return ""\n'
            '    try:\n        return m.group("y") or ""\n    except IndexError as e:\n        return str(e)',
        ),
        "f",
        ("a",),
        "no such group",
    ),
    Case(
        "re_start_end_after_emoji",
        P(
            "f(s: Str) -> list[Int]",
            'm = re.search(r"b+", s)\n    if m is None:\n        return []\n    return [m.start(), m.end()]',
        ),
        "f",
        ("👋abb",),
        [2, 4],
    ),
    Case("re_sub_unmatched_group_empty", P("f(s: Str) -> Str", r'return re.sub(r"(a)|b", r"[\1]", s)'), "f", ("ab",), "[a][]"),
    Case("re_sub_escapes", P("f(s: Str) -> Str", r'return re.sub(r"a", r"\\\n\g<0>", s)'), "f", ("xa",), "x\\\na"),
    Case(
        "re_sub_bad_group_reference",
        P("f(s: Str) -> Str", r'return re.sub(r"(a)", r"\2", s)'),
        "f",
        ("a",),
        Exc("PatternError"),
    ),
    Case(
        "re_fullmatch_backtracks",
        P(
            "f(s: Str) -> Str",
            'm = re.fullmatch(r"a|ab", s)\n    if m is None:\n        return ""\n    return m.group()',
        ),
        "f",
        ("ab",),
        "ab",
    ),
    Case("re_word_boundary", P("f(s: Str) -> list[Str]", r'return re.findall(r"\bfoo\b", s)'), "f", ("foo foobar foo_ foo",), ["foo", "foo"]),
    Case(
        "re_match_repr",
        P("f(s: Str) -> Str", 'm = re.search(r"b", s)\n    return str(m) + f"|{m}"'),
        "f",
        ("éb",),
        "<re.Match object; span=(1, 2), match='b'>|<re.Match object; span=(1, 2), match='b'>",
    ),
    Case("re_multiline_flag", P("f(s: Str) -> list[Str]", r'return re.findall(r"(?m)^\w", s)'), "f", ("ab\ncd",), ["a", "c"]),
    Case(
        "re_lazy_repeat",
        P(
            "f(s: Str) -> Str",
            'm = re.search(r"<.+?>", s)\n    if m is None:\n        return ""\n    return m.group()',
        ),
        "f",
        ("<a><b>",),
        "<a>",
    ),
    Case(
        "re_unicode_flag_value_error",
        P(
            "f(s: Str) -> Str",
            'try:\n        m = re.search(r"(?u)a", s)\n        return "found"\n'
            "    except ValueError as e:\n        return str(e)",
        ),
        "f",
        ("a",),
        "ASCII and UNICODE flags are incompatible",
    ),
    Case(
        "re_repeated_group_keeps_last",
        P(
            "f(s: Str) -> list[Str]",
            'm = re.fullmatch(r"(?:(a)|b)*", s)\n    if m is None:\n        return []\n'
            '    return [m.group(1) or "-", m.group()]',
        ),
        "f",
        ("ab",),
        ["a", "ab"],
    ),
]

JSON = [
    Case(
        "dumps_ensure_ascii",
        P("f(s: Str) -> Str", "return json.dumps(s)"),
        "f",
        ("héllo",),
        '"h\\u00e9llo"',
    ),
    Case(
        "dumps_astral_surrogates",
        P("f(s: Str) -> Str", "return json.dumps(s)"),
        "f",
        ("👋",),
        '"\\ud83d\\udc4b"',
    ),
    Case(
        "dumps_separators",
        P("f() -> Str", 'return json.dumps({"a": 1, "b": 2})'),
        "f",
        (),
        '{"a": 1, "b": 2}',
    ),
    Case(
        "dumps_sort_keys",
        P("f() -> Str", 'return json.dumps({"b": 1, "a": 2}, sort_keys=True)'),
        "f",
        (),
        '{"a": 2, "b": 1}',
    ),
    Case(
        "dumps_indent_2",
        P("f() -> Str", 'return json.dumps({"a": 1, "b": [1, 2]}, indent=2)'),
        "f",
        (),
        '{\n  "a": 1,\n  "b": [\n    1,\n    2\n  ]\n}',
    ),
    Case(
        "dumps_float_repr",
        P("f() -> Str", "return json.dumps(1e16)"),
        "f",
        (),
        "1e+16",
    ),
    Case(
        "dumps_nan_infinity",
        P(
            "f() -> Str",
            'return json.dumps([float("nan"), float("inf"), float("-inf")])',
        ),
        "f",
        (),
        "[NaN, Infinity, -Infinity]",
    ),
    Case(
        "loads_int_vs_float",
        P("f(s: Str) -> Str", "return json.dumps(json.loads(s))"),
        "f",
        ("[1, 2.5, 3]",),
        "[1, 2.5, 3]",
    ),
    Case(
        "loads_key_order",
        P("f(s: Str) -> Str", "return json.dumps(json.loads(s))"),
        "f",
        ('{"z": 1, "a": 2, "m": 3}',),
        '{"z": 1, "a": 2, "m": 3}',
    ),
    Case(
        "loads_invalid_value_error",
        P("f(s: Str) -> Str", "return json.dumps(json.loads(s))"),
        "f",
        ("{invalid}",),
        Exc("ValueError"),
    ),
    Case(
        "dumps_nested_list_dict",
        P(
            "f() -> Str",
            'return json.dumps({"list": [1, {"x": True, "y": None}], "n": 2})',
        ),
        "f",
        (),
        '{"list": [1, {"x": true, "y": null}], "n": 2}',
    ),
    Case(
        "dumps_empty_containers",
        P(
            "f() -> Str",
            "xs: list[Int] = []\n"
            "    d2: dict[Str, Int] = {}\n"
            '    return json.dumps({"a": xs, "b": d2})',
        ),
        "f",
        (),
        '{"a": [], "b": {}}',
    ),
    Case(
        "loads_scientific_number",
        P("f(s: Str) -> Str", "return json.dumps(json.loads(s))"),
        "f",
        ("1.5e3",),
        "1500.0",
    ),
    Case(
        "dumps_bool_null",
        P("f() -> Str", "return json.dumps([True, False, None])"),
        "f",
        (),
        "[true, false, null]",
    ),
]

FUEL = [
    Case(
        "while_true_r620",
        P("f() -> Int", "x = 0\n    while True:\n        x += 1\n    return x"),
        "f",
        (),
        Err("R620"),
        budget=50,
    ),
    Case(
        "recursion_depth_r620",
        P("f(n: Int) -> Int", "return f(n + 1)"),
        "f",
        (0,),
        Err("R620"),
        budget=10_000,
    ),
    Case(
        "string_repeat_r622",
        P("f() -> Int", 's = "x" * 20000000\n    return len(s)'),
        "f",
        (),
        Err("R622"),
    ),
    Case(
        "list_repeat_r622",
        P("f() -> Int", "xs = [0] * 20000000\n    return len(xs)"),
        "f",
        (),
        Err("R622"),
    ),
    Case(
        "sum_bulk_charged",
        P("f() -> Int", "return sum(range(1000000))"),
        "f",
        (),
        Err("R620"),
        budget=10,
    ),
    Case(
        "comprehension_each_charged",
        P("f() -> Int", "return len([x for x in range(1000000)])"),
        "f",
        (),
        Err("R620"),
        budget=10,
    ),
]

# The core language (statements, operators, core builtins) beyond the groups above:
# evaluation order, iteration while mutating, truthiness values, exact mixed
# comparisons, and the lazy charging of generator arguments (plan 5a Task 8).
CORE = [
    Case(
        # CPython's dict iterator refuses a change of size (the checker allows it)
        "dict_changed_size_during_iteration",
        P(
            "f(n: Int) -> Int",
            'd = {"a": 1, "b": 2}\n    for k in d:\n        d[k + "x"] = n\n    return len(d)',
        ),
        "f",
        (1,),
        Exc("RuntimeError"),
    ),
    Case(
        "dict_value_update_during_iteration",
        P(
            "f(n: Int) -> list[Int]",
            'd = {"a": 1, "b": 2}\n    for k in d:\n        d[k] = d[k] * n\n'
            '    return [d["a"], d["b"]]',
        ),
        "f",
        (10,),
        [10, 20],
    ),
    Case(
        # a list iterator is live: elements appended in place (`+=`) are visited
        "list_grows_while_iterating",
        P(
            "f(n: Int) -> list[Int]",
            "ys = [1, 2]\n    for x in ys:\n        if len(ys) < n:\n"
            "            ys += [x * 10]\n    return ys",
        ),
        "f",
        (5,),
        [1, 2, 10, 20, 100],
    ),
    Case(
        "try_except_value_error",
        P(
            "f(s: Str) -> Int",
            "try:\n        return int(s)\n    except ValueError:\n        return -1",
        ),
        "f",
        ("x1",),
        -1,
    ),
    Case(
        "try_in_loop_break_continue",
        P(
            "f(xs: list[Str]) -> Int",
            "total = 0\n"
            "    for s in xs:\n"
            "        try:\n"
            '            if s == "stop":\n'
            "                break\n"
            "            total += int(s)\n"
            "        except ValueError:\n"
            "            continue\n"
            "        else:\n"
            "            total += 100\n"
            "    return total",
        ),
        "f",
        (["1", "x", "2", "stop", "5"],),
        203,
    ),
    Case(
        "except_message",
        P(
            "f(k: Str) -> Str",
            'd = {"a": 1}\n    try:\n        return str(d[k])\n    except KeyError as e:\n'
            '        return "missing " + str(e)',
        ),
        "f",
        ("zz",),
        "missing 'zz'",
    ),
    Case(
        "or_returns_the_value",
        P("f(x: Int | None, y: Int) -> Int", "z = x or y\n    return z if z is not None else -1"),
        "f",
        (0, 7),
        7,
    ),
    Case(
        "enumerate_zip",
        P(
            "f(xs: list[Int], ys: list[Str]) -> list[Str]",
            "return [s * i for i, (x, s) in enumerate(zip(xs, ys), 1) if x > 0]",
        ),
        "f",
        ([1, 0, 3], ["a", "b", "c", "d"]),
        ["a", "ccc"],
    ),
    Case(
        "min_max_key",
        P(
            "f(xs: list[Str]) -> list[Str]",
            "return [min(xs, key=lambda s: len(s)), max(xs, key=lambda s: len(s))]",
        ),
        "f",
        (["bb", "a", "ccc", "d", "eee"],),
        ["a", "ccc"],
    ),
    Case(
        "range_iterates_again",
        P(
            "f(n: Int) -> Int",
            "r = range(n)\n    a = list(r)\n    b = list(r)\n    return len(a) + len(b)",
        ),
        "f",
        (3,),
        6,
    ),
    Case(
        "enumerate_is_one_shot",
        P(
            "f(xs: list[Int]) -> Int",
            "e = enumerate(xs)\n    a = list(e)\n    b = list(e)\n    return len(a) * 10 + len(b)",
        ),
        "f",
        ([4, 5],),
        20,
    ),
    Case(
        "float_div_by_zero",
        P("f(a: Float, b: Float) -> Float", "return a / b"),
        "f",
        (1.0, 0.0),
        Exc("ZeroDivisionError"),
    ),
    Case(
        # Python compares an int with a float exactly (2**53 + 1 is not 2.0**53)
        "int_float_compare_exact",
        P("f(n: Int, x: Float) -> list[Bool]", "return [n == x, n > x, n - 1 == x]"),
        "f",
        (2**53 + 1, 2.0**53),
        [False, True, True],
    ),
    Case(
        # sum() of floats is compensated (Neumaier) since Python 3.12
        "sum_floats_compensated",
        P("f(xs: list[Float]) -> Str", "return str(sum(xs))"),
        "f",
        ([0.1] * 10,),
        "1.0",
    ),
    Case(
        # each Float operation rounds: never one fused multiply-add (Go on arm64 may)
        "float_mul_add_not_fused",
        P("f(x: Float, y: Float, z: Float) -> Float", "return x * y + z"),
        "f",
        (0.1, 10.0, -1.0),
        0.0,
    ),
    Case(
        "float_mul_then_sub_not_fused",
        P("f(x: Float, y: Float, z: Float) -> Float", "a = x * y\n    return a - z"),
        "f",
        (0.1, 10.0, 1.0),
        0.0,
    ),
    Case(
        "int_true_div_exact",
        P("f(a: Int, b: Int) -> Float", "return a / b"),
        "f",
        (2**63 - 1, 3),
        3.0744573456182584e18,
    ),
    Case(
        # a generator argument is charged as any() consumes it, and any() stops early
        "any_generator_charged_lazily",
        P("f(n: Int) -> Bool", "return any(x > 2 for x in range(n))"),
        "f",
        (1_000_000,),
        True,
        budget=20,
    ),
    Case(
        "set_operators_order",
        P(
            "f(a: list[Int], b: list[Int]) -> list[list[Int]]",
            "s = set(a)\n    t = set(b)\n"
            "    return [list(s | t), list(s & t), list(s - t), list(s ^ t)]",
        ),
        "f",
        ([3, 1, 2], [2, 4, 3]),
        [[3, 1, 2, 4], [3, 2], [1], [1, 4]],
    ),
    Case(
        "unpack_too_many",
        P("f(xs: list[Int]) -> Int", "a, b = xs\n    return a + b"),
        "f",
        ([1, 2, 3],),
        Exc("ValueError"),
    ),
    Case(
        "chained_compare",
        P("f(a: Int, b: Int, c: Int) -> list[Bool]", "return [a < b < c, a < b > c, a == b == c]"),
        "f",
        (1, 2, 3),
        [True, False, False],
    ),
    Case(
        "fstr_scalars",
        P(
            "f(n: Int, s: Str, x: Int | None) -> Str",
            'return f"{n:>4}|{n:#x}|{n:,}|{True}|{x}|{s!r}|{s:^7}|{-n:+05d}|{n / 8:.1%}"',
        ),
        "f",
        (1234, "héllo", None),
        "1234|0x4d2|1,234|True|None|'héllo'| héllo |-1234|15425.0%",
    ),
    Case(
        "round_float_digits",
        P("f(x: Float) -> list[Float]", "return [round(x, 2), round(x, -1), round(-x, 0)]"),
        "f",
        (2.675,),
        [2.67, 0.0, -3.0],
    ),
    Case(
        "sorted_tuples_reverse",
        P(
            "f(xs: list[Int]) -> list[Int]",
            "ps = sorted([(x % 3, x) for x in xs], reverse=True)\n    return [p[1] for p in ps]",
        ),
        "f",
        ([5, 3, 9, 4, 1],),
        [5, 4, 1, 9, 3],
    ),
    Case(
        "dict_comprehension_last_wins",
        P(
            "f(xs: list[Str]) -> list[Int]",
            "d = {s: i for i, s in enumerate(xs)}\n    return [d[s] for s in d]",
        ),
        "f",
        (["a", "b", "a", "c"],),
        [2, 1, 3],
    ),
    Case(
        "str_iteration_code_points",
        P("f(s: Str) -> list[Str]", "return [c for c in s if c != 'l']"),
        "f",
        ("héllo👋",),
        ["h", "é", "o", "👋"],
    ),
    Case(
        "tuple_concat_and_in",
        P("f(n: Int) -> list[Bool]", "t = (1, 2) + (n,)\n    return [n in t, 5 in t, len(t) == 3]"),
        "f",
        (7,),
        [True, False, True],
    ),
]

# R-widen (docs/lattice-language-rules.md, 2026-09-28): an Int value used where the
# checker's types say Float is converted to a float at that point, on every target;
# and R-pow: a negative Float base with a non-integral exponent raises ValueError.
_G_FLOAT_STR = "@procedure\ndef g(x: Float) -> Str:\n    return str(x)\n\n\n"
_G_TWO = '@procedure\ndef g(a: Float, b: Int) -> Str:\n    return f"{a}|{b}"\n\n\n'
_G_RET = "@procedure\ndef g(n: Int) -> Float:\n    return n\n\n\n"
_REC = "@record\nclass R:\n    x: Float\n    y: Float = 0\n\n\n"

WIDEN = [
    Case(
        "widen_annotated_assign",
        P("f(n: Int) -> Str", "x: Float = n\n    return str(x)"),
        "f",
        (1,),
        "1.0",
    ),
    Case(
        "widen_reassign",
        P("f(n: Int) -> Str", "x = 0.5\n    x = n\n    return str(x)"),
        "f",
        (3,),
        "3.0",
    ),
    Case(
        "widen_optional_assign",
        P("f(n: Int) -> Str", "x: Float | None = n\n    return str(x)"),
        "f",
        (1,),
        "1.0",
    ),
    Case(
        "widen_optional_to_optional",
        P(
            "f(m: Int | None) -> list[Str]",
            "x: Float | None = m\n    y: Float | None = None\n    return [str(x), str(y)]",
        ),
        "f",
        (4,),
        ["4.0", "None"],
    ),
    Case(
        "widen_chained_assign",
        P("f(n: Int) -> Str", 'x = 0.5\n    x = y = n\n    return f"{x}|{y}"'),
        "f",
        (3,),
        "3.0|3",
    ),
    Case(
        "widen_tuple_unpack",
        P("f(n: Int) -> Str", 'a = 0.5\n    a, b = n, n + 1\n    return f"{a}|{b}"'),
        "f",
        (3,),
        "3.0|4",
    ),
    Case(
        "widen_for_target",
        P("f(xs: list[Int]) -> Str", "x = 0.5\n    for x in xs:\n        pass\n    return str(x)"),
        "f",
        ([1, 2],),
        "2.0",
    ),
    Case("widen_call_arg", _G_FLOAT_STR + P("f(n: Int) -> Str", "return g(n)"), "f", (2,), "2.0"),
    Case(
        "widen_keyword_args",
        _G_TWO + P("f(n: Int) -> Str", "return g(b=n, a=n)"),
        "f",
        (4,),
        "4.0|4",
    ),
    Case("widen_return", P("f(n: Int) -> Float", "return n"), "f", (5,), 5.0),
    Case("widen_return_used", _G_RET + P("f(n: Int) -> Str", "return str(g(n))"), "f", (5,), "5.0"),
    Case(
        "widen_int_times_int_as_float",
        # the product is Float arithmetic (rounded), not an exact Int product
        P("f(n: Int) -> Int", "x: Float = n\n    return int(x * x)"),
        "f",
        (3037000499,),
        9223372030926248960,
    ),
    Case(
        "widen_list_elements",
        P(
            "f(n: Int) -> Str",
            "xs = [n, 2.5]\n    ys: list[Float] = [n, n + 1]\n"
            '    return f"{xs[0]}|{ys[0]}|{ys[1]}"',
        ),
        "f",
        (1,),
        "1.0|1.0|2.0",
    ),
    Case(
        "widen_set_elements",
        P("f(n: Int) -> list[Float]", "s = {n, 2.5}\n    return [x for x in s]"),
        "f",
        (1,),
        [1.0, 2.5],
    ),
    Case(
        "widen_dict_keys_and_values",
        P(
            "f(n: Int) -> list[Float]",
            "d = {n: n, 2.5: 0.5}\n    return [k + d[k] * 0 for k in d] + [d[n]]",
        ),
        "f",
        (1,),
        [1.0, 2.5, 1.0],
    ),
    Case(
        "widen_dict_spread",
        P("f(n: Int) -> Str", 'd = {"a": n}\n    e = {**d, "b": 2.5}\n    return f"{e[\'a\']}"'),
        "f",
        (1,),
        "1.0",
    ),
    Case(
        "widen_item_stores",
        P(
            "f(n: Int) -> Str",
            "xs = [0.5, 1.5]\n    xs[0] = n\n    d: dict[Str, Float] = {}\n"
            '    d["a"] = n\n    e: dict[Float, Str] = {}\n    e[n] = "x"\n'
            "    return f\"{xs[0]}|{d['a']}|{[k for k in e][0]}\"",
        ),
        "f",
        (7,),
        "7.0|7.0|7.0",
    ),
    Case(
        "widen_tuples_in_a_list",
        P("f(n: Int) -> Str", 'ps = [(n, 2.5), (1.5, 0.5)]\n    return f"{ps[0][0]}|{ps[1][1]}"'),
        "f",
        (3,),
        "3.0|0.5",
    ),
    Case(
        "widen_or",
        P("f(x: Float, n: Int) -> Str", "y = x or n\n    return str(y)"),
        "f",
        (0.0, 2),
        "2.0",
    ),
    Case(
        "widen_or_optional",
        P("f(x: Float | None, n: Int) -> Str", "return str(x or n)"),
        "f",
        (None, 2),
        "2.0",
    ),
    Case("widen_and", P("f(n: Int, x: Float) -> Str", "return str(n and x)"), "f", (0, 2.5), "0.0"),
    Case(
        "widen_if_expression",
        P("f(b: Bool, n: Int) -> Str", "return str(2.5 if b else n)"),
        "f",
        (False, 3),
        "3.0",
    ),
    Case(
        "widen_if_expression_in_comprehension",
        P("f(xs: list[Float]) -> list[Float]", "return [x if x > 1 else 0 for x in xs]"),
        "f",
        ([0.5, 2.5],),
        [0.0, 2.5],
    ),
    Case(
        "widen_tuple_index", P("f(i: Int) -> Str", "return str((1, 2.5, 3)[i])"), "f", (0,), "1.0"
    ),
    Case(
        "widen_tuple_iteration",
        P(
            "f(n: Int) -> list[Str]",
            "out = [str(x) for x in (n, 2.5)]\n    for y in (2.5, n):\n"
            "        out = out + [str(y)]\n    return out",
        ),
        "f",
        (1,),
        ["1.0", "2.5", "2.5", "1.0"],
    ),
    Case(
        "widen_tuple_membership",
        # an Int probe of Floats is widened; a Float probe of Ints stays exact
        P(
            "f(n: Int, x: Float) -> list[Bool]",
            "return [n in (1.5, 2.0), n in [1.5, 2.0], x in (1, 2), x in [1, 2], x in {1, 2},"
            " x + 0.5 in [1, 2], x * 1e30 in {1, 2}]",
        ),
        "f",
        (2, 2.0),
        [True, True, True, True, True, False, False],
    ),
    Case(
        "widen_sum_empty", P("f(xs: list[Float]) -> Str", "return str(sum(xs))"), "f", ([],), "0.0"
    ),
    Case(
        "widen_sum_int_start",
        P(
            "f(xs: list[Float], n: Int) -> list[Str]",
            "return [str(sum(xs, n)), str(sum(xs + [0.25], n))]",
        ),
        "f",
        ([], 2),
        ["2.0", "2.25"],
    ),
    Case(
        "widen_sum_ints_with_a_float_start",
        P("f(xs: list[Int]) -> list[Float]", "return [sum(xs, 0.5), sum(xs, 0.0)]"),
        "f",
        ([1, 2],),
        [3.5, 3.0],
    ),
    Case(
        "widen_min_max_mixed",
        P(
            "f(n: Int, x: Float) -> list[Str]",
            "return [str(min(n, x)), str(max(n, x)), str(max(n, x, 0))]",
        ),
        "f",
        (1, 2.5),
        ["1.0", "2.5", "2.5"],
    ),
    Case(
        "widen_min_of_a_tuple",
        P("f(n: Int) -> list[Str]", "return [str(min((n, 2.5, 3))), str(sorted((n, 0.5))[1])]"),
        "f",
        (1,),
        ["1.0", "1.0"],
    ),
    Case(
        "widen_int_pow_non_literal",
        P(
            "f(a: Int, n: Int) -> list[Str]",
            "return [str(a ** n), str(a ** -n), str(a ** (n - n))]",
        ),
        "f",
        (2, 3),
        ["8.0", "0.125", "1.0"],
    ),
    Case(
        "widen_int_pow_is_float_math",
        P("f(a: Int, n: Int) -> Float", "return a ** n"),
        "f",
        (3, 40),
        1.2157665459056929e19,
    ),
    Case(
        "widen_round_int_digits",
        P("f(n: Int) -> list[Str]", "return [str(round(n, -2)), str(round(n, 1)), str(round(n))]"),
        "f",
        (1250,),
        ["1200.0", "1250.0", "1250"],
    ),
    Case(
        "widen_record_fields",
        _REC + P("f(n: Int) -> Str", 'r = R(x=n)\n    return f"{r.x}|{r.y}"'),
        "f",
        (1,),
        "1.0|0.0",
    ),
    Case(
        "widen_list_append",
        P(
            "f(n: Int) -> Str",
            'xs = [0.5]\n    xs.append(n)\n    xs.extend([n, n])\n    return f"{xs[1]}|{xs[3]}"',
        ),
        "f",
        (1,),
        "1.0|1.0",
    ),
    Case(
        "widen_dict_get_default",
        P("f(n: Int) -> Str", 'd: dict[Str, Float] = {}\n    return str(d.get("a", n))'),
        "f",
        (1,),
        "1.0",
    ),
    # R-pow: a negative base with a non-integral exponent has no Float value
    Case(
        "float_pow_negative_fractional",
        P("f(a: Float, b: Float) -> Float", "return a ** b"),
        "f",
        (-4.0, 0.5),
        Exc("ValueError"),
    ),
    Case(
        "float_pow_negative_fractional_message",
        P(
            "f(a: Float, b: Float) -> Str",
            "try:\n        return str(a ** b)\n    except ValueError as e:\n        return str(e)",
        ),
        "f",
        (-8.0, 1.0 / 3.0),
        "math domain error",
    ),
    Case(
        "float_pow_negative_fractional_augmented",
        P("f(a: Float, b: Float) -> Float", "x = a\n    x **= b\n    return x"),
        "f",
        (-2.0, 1.5),
        Exc("ValueError"),
    ),
    Case(
        "float_pow_int_base_fractional",
        P("f(n: Int, b: Float) -> Float", "return n ** b"),
        "f",
        (-8, 0.5),
        Exc("ValueError"),
    ),
    Case(
        "float_pow_negative_integral_and_infinite",
        P(
            "f(a: Float, b: Float) -> list[Float]",
            "return [a ** 3.0, a ** -1.0, (a * 1e308 * 10) ** b]",
        ),
        "f",
        (-2.0, 0.5),
        [-8.0, -0.5, float("inf")],
    ),
]

from tests.semantics_v11.cases_task10 import COLL, EXACT, GATED, JSON_MORE, MATH, TIME_MORE  # noqa: E402

CASES: tuple[Case, ...] = tuple(
    INT + FLOAT_TEXT + STR + LIST + DICT_SET + TIME + REGEX + JSON + FUEL + CORE + WIDEN
    + STR_MORE
    + REGEX_MORE
    + COLL
    + MATH
    + TIME_MORE
    + JSON_MORE
    + EXACT
    + GATED
)

_NAMES = [c.name for c in CASES]
assert len(_NAMES) == len(set(_NAMES)), "duplicate case name in CASES"

_EXC_TYPES: tuple[type[Exception], ...] = (
    KeyError,
    IndexError,
    ValueError,
    ZeroDivisionError,
    RuntimeError,  # a dict changed size while iterated: not catchable in T2, escapes
    TypeError,  # e.g. a fill character of two characters: not catchable in T2
    OverflowError,  # math.exp(1000.0), a date out of range: not catchable in T2
    _re.PatternError,  # an invalid re.sub replacement: not catchable in T2
)


def run_python(case: Case) -> object:
    """Run one case on the Python target: `T2Error` -> `Err(code)`; a
    KeyError/IndexError/ValueError/ZeroDivisionError escaping the procedure (or one of
    their subclasses, e.g. json's `JSONDecodeError` is a `ValueError`) -> `Exc(name)`."""
    ns = load(case.source)
    fn = ns[case.proc]
    try:
        return fn(T2Fuel(case.budget), *case.args)
    except T2Error as e:
        return Err(e.code)
    except Exception as e:  # noqa: BLE001 - re-raised below when it isn't one of ours
        for exc_type in _EXC_TYPES:
            if isinstance(e, exc_type):
                return Exc(exc_type.__name__)
        raise


def semantics_cases_missing_on(target: str) -> list[str]:
    """Case names `CASES` does not run on `target`. Empty for "python": test_python.py
    runs every case. For "go": the cases the checker refuses for Go (p8_target's L892
    gates -- constructs the Go target does not implement yet); test_go.py runs every
    other case, and test_go.py checks nothing is missing for another reason."""
    if target == "python":
        return []
    from lattice.check import check_source  # noqa: PLC0415 - the checker is only needed here
    from tests.check.t2_support import PROFILE  # noqa: PLC0415

    return [
        c.name for c in CASES if not check_source(c.source_with_header, PROFILE, target=target).ok
    ]
