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

CASES: tuple[Case, ...] = tuple(
    INT + FLOAT_TEXT + STR + LIST + DICT_SET + TIME + REGEX + JSON + FUEL
)

_NAMES = [c.name for c in CASES]
assert len(_NAMES) == len(set(_NAMES)), "duplicate case name in CASES"

_EXC_TYPES: tuple[type[Exception], ...] = (KeyError, IndexError, ValueError, ZeroDivisionError)


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
    """Case names `CASES` has not yet been confirmed to run on `target` (Task 8 calls
    this with "go"). Empty for "python": this suite runs every case against it."""
    if target == "python":
        return []
    return [c.name for c in CASES]
