"""Regenerates t2core_answers.json: what Python 3.14 answers for the core T2 operations
the Go emitter compiles to runtime calls (t2core.go, t2fmt.go): format() of an int,
bool, str and None, ascii(), float(str), round(float, n), sum() of floats, int / int,
float ** float, int-float comparisons and int(float). t2core_test.go asserts against
it. Run from the repository root:

    uv run python lattice/gogen/rt/testdata/gen_t2core_answers.py

Each answer is {"fn": name, "args": [...], "out": text}: `out` is the result rendered
as the Go test renders it (a str as itself, an int in decimal, a float as its repr, a
bool as True/False) or "!<Kind>: <message>" for an exception.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

OUT = Path(__file__).with_name("t2core_answers.json")


def render(v: object) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def outcome(f) -> str:  # noqa: ANN001
    try:
        return render(f())
    except Exception as e:  # noqa: BLE001 - the answer is the exception
        return f"!{type(e).__name__}: {e}"


INT_SPECS = [
    "",
    "d",
    "5",
    "<5",
    "^5",
    ">5",
    "=5",
    "*^7",
    "05",
    "+",
    " ",
    "-",
    "+5",
    "+05",
    "x",
    "X",
    "#x",
    "#X",
    "o",
    "#o",
    "b",
    "#b",
    "#010b",
    ",",
    "_",
    ",d",
    "_x",
    "_b",
    "#_x",
    "10,",
    "010,",
    "c",
    "n",
    ",n",
    "e",
    ".2f",
    "%",
    ".1%",
    "g",
    "E",
    "10.3e",
    ".3",
    "z",
    "#c",
    "+c",
    "q",
    "5.5",
    "__",
    ",_",
    "=^10",
    "0=10",
    "010x",
    "#010x",
    "-#06x",
    "x<10",
    "é>6",
    ">+8",
]
INTS = [0, 7, -7, 42, 255, -255, 1234567, -1234567, 2**63 - 1, -(2**63), 65, 0x1F600, -1]
STR_SPECS = [
    "",
    "s",
    "5",
    "<5",
    ">5",
    "^5",
    "^6",
    "*^7",
    ".2",
    "5.2",
    ">5.1",
    "05",
    "010",
    "=5",
    "+",
    " ",
    "#",
    "d",
    ",",
    "_",
    "z",
    ".0",
    "10",
    "x^4",
    "é<4",
    ".",
    "2.",
]
STRS = ["", "a", "abc", "héllo", "👋x"]
ASCII = ["", "abc", "héllo", "👋", "tab\t", "q'\"", "\x7f\x80", "é'", "ÿĀ"]
FLOAT_TEXTS = [
    "1e3",
    " 42 ",
    "4x",
    "",
    " ",
    "inf",
    "-inf",
    "+Infinity",
    "nan",
    "-nan",
    "NaN",
    "1_000.5",
    "1__0",
    "_1",
    "1_",
    "0x10",
    "1e400",
    "-1e400",
    "1e-400",
    ".5",
    "5.",
    ".",
    "1.5e+3",
    "1.5E-3",
    "١٢٣",
    "  7.25\n",
    "1e",
    "e5",
    "+-1",
    "--1",
    "0001.100",
    "infinit",
    "infinityx",
    "1.5j",
    " 12",
    "3.4028236692093846346337460743176821145600000000001e38",
    "2.2250738585072011e-308",
    "9007199254740993",
    "0.1",
    "-0",
    "1_2.3_4e1_0",
]
ROUNDS = [
    (2.5, 0),
    (3.5, 0),
    (2.675, 2),
    (1.005, 2),
    (0.125, 2),
    (0.375, 2),
    (-0.5, 0),
    (-2.5, 0),
    (1234.5678, -2),
    (1250.0, -2),
    (1350.0, -2),
    (-1250.0, -2),
    (5.5, -1),
    (1e300, 2),
    (1e-300, 2),
    (123.456, 10),
    (0.0, 3),
    (-0.0, 3),
    (float("inf"), 2),
    (float("nan"), 1),
    (float("-inf"), 0),
    (1e20, -25),
    (-1e20, -25),
    (0.5, 400),
    (0.5, -400),
    (7.0, 308),
    (123.456, -309),
    (99.99, 1),
    (2.5e-5, 5),
    (15.0, -1),
    (25.0, -1),
    (0.285, 2),
    (1.0, 2),
    (-0.4, 0),
    (1e16, -16),
    (5e15, -16),
]
SUMS = [
    ([], None),
    ([0.1] * 10, None),
    ([1e100, 1.0, -1e100], None),
    ([0.1, 0.2, 0.3], None),
    ([-0.0], None),
    ([-0.0, -0.0], None),
    ([float("inf"), 1.0], None),
    ([float("inf"), float("-inf")], None),
    ([1e308, 1e308], None),
    ([1.5], 2.5),
    ([0.1, 0.2], 0.3),
    ([], 1.5),
    ([1e16, 1.0, 1.0], None),
    ([3.0, 1e-16, 1e-16], None),
    ([float("nan"), 1.0], None),
    ([1e308, 1e308, -1e308], None),
    ([-0.0], -0.0),
    ([0.1] * 10, 5),
    ([], 5),
    ([-0.0], 0),
    ([2.5, -1e-17, 1e-17], 1),
]
DIVS = [
    (7, 2),
    (-7, 2),
    (1, 3),
    (0, 5),
    (0, -5),
    (1, 0),
    (2**53 + 1, 1),
    (2**63 - 1, 3),
    (-(2**63), 7),
    (2**63 - 1, 2**63 - 2),
    (10**18 + 1, 10**17),
    (-(2**63), -1),
    (3, 2**62),
    (2**60 + 3, 2**55 + 1),
    (1, -(2**63)),
]
POWS = [
    (2.0, 10.0),
    (2.0, -1.0),
    (0.0, -1.0),
    (-0.0, -1.0),
    (0.0, 0.0),
    (10.0, 400.0),
    (-10.0, 401.0),
    (1e-300, 400.0),
    (-2.0, 3.0),
    (-2.0, 0.5),
    (float("inf"), -1.0),
    (float("nan"), 0.0),
    (1.0, float("nan")),
    (-1.0, float("inf")),
    (0.5, float("inf")),
    (2.0, float("-inf")),
    (-8.0, 1.0 / 3),
    (float("-inf"), 3.0),
    (float("-inf"), 2.0),
    (-0.0, 3.0),
    (-0.0, -3.0),
    (-0.0, 0.5),
    (0.0, float("-inf")),
    (4.0, 0.5),
    (1.0000001, 1e10),
    (-1.0, 1e300),
    (2.0, 1024.0),
    (2.0, 1023.5),
    (1.1, 2.0),
    (3.0, 0.5),
    (-3.0, 3.0),
    (1.5, -2.0),
    (10.0, -3.0),
    (2.0, -1074.0),
    (2.0, -1075.0),
    (0.1, 3.0),
    (7.0, 1.0 / 3),
    (1e-5, 0.25),
]
_RNG = random.Random(8)
POWS += [(_RNG.uniform(0.001, 100.0), _RNG.uniform(-40.0, 40.0)) for _ in range(40)] + [
    (_RNG.uniform(-50.0, 50.0), float(_RNG.randint(-20, 20))) for _ in range(20)
]
CMPS = [
    (1, 1.0),
    (1, 1.5),
    (2, 1.5),
    (0, -0.0),
    (2**53 + 1, 2.0**53),
    (2**53, 2.0**53),
    (2**63 - 1, 2.0**63),
    (-(2**63), -(2.0**63)),
    (-(2**63), -(2.0**63) - 2048),
    (5, float("nan")),
    (5, float("inf")),
    (5, float("-inf")),
    (-(2**53) - 1, -(2.0**53)),
    (3, 2.9999999999999996),
    (9007199254740993, 9007199254740992.0),
    (9007199254740993, 9007199254740994.0),
]
TRUNCS = [
    0.0,
    -0.0,
    1.9,
    -1.9,
    1e18,
    9.2e18,
    -9.3e18,
    2.0**63,
    -(2.0**63),
    float("nan"),
    float("inf"),
    float("-inf"),
    1e300,
    -0.5,
]


def cmp_answer(a: int, b: float) -> str:
    if a < b:
        return "-1"
    if a > b:
        return "1"
    if a == b:
        return "0"
    return "unordered"


def sum_of(xs: list[float], start: float | int | None) -> object:
    return sum(xs) if start is None else sum(xs, start)


def tagged(v: object) -> dict[str, object]:
    if isinstance(v, bool):
        return {"t": "bool", "v": v}
    if isinstance(v, int):
        return {"t": "int", "v": str(v)}
    if isinstance(v, float):
        return {"t": "float", "v": repr(v)}
    if v is None:
        return {"t": "none"}
    return {"t": "str", "v": v}


def main() -> None:
    answers: list[dict[str, object]] = []

    def add(fn: str, args: list[object], f) -> None:  # noqa: ANN001
        answers.append({"fn": fn, "args": [tagged(a) for a in args], "out": outcome(f)})

    for i in INTS:
        for spec in INT_SPECS:
            add("format_int", [i, spec], lambda i=i, spec=spec: format(i, spec))
    for b in (True, False):
        for spec in ["", "5", "d", "x", "q", "^7", "s"]:
            add("format_bool", [b, spec], lambda b=b, spec=spec: format(b, spec))
    for s in STRS:
        for spec in STR_SPECS:
            add("format_str", [s, spec], lambda s=s, spec=spec: format(s, spec))
    for spec in ["", "5", "s"]:
        add("format_none", [spec], lambda spec=spec: format(None, spec))
    for s in ASCII:
        add("ascii", [s], lambda s=s: ascii(s))
    for s in FLOAT_TEXTS:
        add("parse_float", [s], lambda s=s: float(s))
    for x, n in ROUNDS:
        add("round_f", [x, n], lambda x=x, n=n: round(x, n))
    for x in [2.5, 3.5, -2.5, 0.5, 1e18, 9.3e18, float("nan"), float("inf")]:
        add("round0", [x], lambda x=x: round(x))
    for xs, start in SUMS:
        args: list[object] = [json.dumps([repr(x) for x in xs]), start]
        add("sum_float", args, lambda xs=xs, start=start: sum_of(xs, start))
    for a, b in DIVS:
        add("truediv_int", [a, b], lambda a=a, b=b: a / b)
    for a, b in POWS:
        add("pow_f", [a, b], lambda a=a, b=b: a**b)
    for a, b in CMPS:
        add("cmp_if", [a, b], lambda a=a, b=b: cmp_answer(a, b))
    for x in TRUNCS:
        add("int_of_float", [x], lambda x=x: int(x))
    for a, b in [(7.0, 2.0), (1.0, 0.0), (-1.0, 0.0), (0.0, 0.0), (1.0, -0.0)]:
        add("div_f", [a, b], lambda a=a, b=b: a / b)
    for a, b in [(7, 2.0), (1, 0.0), (7.5, 2)]:
        add("div_mixed", [a, b], lambda a=a, b=b: a / b)
    OUT.write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(answers)} answers to {OUT}")


if __name__ == "__main__":
    main()
    assert math.isfinite(1.0)
