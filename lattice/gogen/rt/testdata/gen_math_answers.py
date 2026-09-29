"""Regenerates math_answers.json: what Python 3.14 answers for the `math` functions T2
procedures call (catalog T2_STDLIB["math"]), for Int and Float arguments: special
values, random values over the whole range, and values near the edges (overflow of
exp, int conversion of floor/ceil/trunc beyond 2**63). mathx_test.go asserts against
it. Run from the repository root:

    uv run python lattice/gogen/rt/testdata/gen_math_answers.py

Each answer is {"fn": name, "args": [[type, text], ...], "out": text}: an argument is
("i", decimal) or ("f", float.hex); `out` is the result (an int in decimal, a float as
its repr, a bool as True/False) or "!<Kind>: <message>". An int result outside the
64-bit range is "!R623" (T2 range-checks it). A finite exp / log / log10 / pow also
has "cr": the correctly rounded result, which Go gives (`correctly_rounded`).
"""

from __future__ import annotations

import decimal
import json
import math
import random
from pathlib import Path

OUT = Path(__file__).with_name("math_answers.json")
INT_MIN, INT_MAX = -(2**63), 2**63 - 1

SPECIAL = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    0.5,
    -0.5,
    2.0,
    10.0,
    0.1,
    1e-300,
    -1e-300,
    5e-324,
    -5e-324,
    1.7976931348623157e308,
    -1.7976931348623157e308,
    math.inf,
    -math.inf,
    math.nan,
    2.0**52,
    2.0**53,
    2.0**63,
    -(2.0**63),
    2.0**63 - 1024,
    1.5,
    -1.5,
    2.5,
    -2.5,
    709.782712893384,
    709.79,
    -745.1,
    -745.2,
    1e308,
    3.0,
    100.0,
    1e-5,
    0.9999999999999999,
    1.0000000000000002,
]
INTS = [0, 1, -1, 2, 3, 10, 100, 1000, -4, 7, 12, 18, 2**53, 2**53 + 1, INT_MAX, INT_MIN, 36, 48]


def render(v: object) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int) and not (INT_MIN <= v <= INT_MAX):
        return "!R623"
    return str(v)


def outcome(f) -> str:  # noqa: ANN001
    try:
        return render(f())
    except Exception as e:  # noqa: BLE001 - the answer is the exception
        return f"!{type(e).__name__}: {e}"


def arg(v: object) -> list[str]:
    return ["i", str(v)] if isinstance(v, int) else ["f", float.hex(v)]


def rand_floats(rng: random.Random, n: int) -> list[float]:
    out = []
    for _ in range(n):
        r = rng.random()
        if r < 0.4:
            out.append(rng.uniform(-10, 10))
        elif r < 0.7:
            out.append(math.ldexp(rng.random(), rng.randint(-1074, 1024)) * rng.choice([1, -1]))
        elif r < 0.85:
            out.append(rng.uniform(-750, 750))
        else:
            out.append(float(rng.randint(-(10**6), 10**6)) + rng.choice([0, 0.5, 0.25]))
    return out


def correctly_rounded(fn: str, *args: object) -> object:
    """The correctly rounded result (decimal at 80 digits): Go computes exp, log, log10
    and pow correctly rounded, while CPython calls the platform's libm, which is off by
    one in the last place for rare inputs (the Go test compares with `cr` when given)."""
    with decimal.localcontext() as ctx:
        ctx.prec = 80
        ctx.Emax, ctx.Emin = 10**6, -(10**6)
        xs = [decimal.Decimal(float(x)) for x in args]  # the float the C code sees
        if any(not x.is_finite() for x in xs):
            return "skip"
        if fn == "exp":
            return float(xs[0].exp())
        if fn == "log10":
            return float(xs[0].log10())
        if fn == "log":
            if len(xs) == 1:
                return float(xs[0].ln())
            return float(xs[0].ln()) / float(xs[1].ln())
        x, y = xs
        if x == 0 or y == 0 or x == 1:
            return "skip"
        if x < 0:
            if y != y.to_integral_value():
                return "skip"
            r = (-x).ln() * y
            mag = float(r.exp())
            return -mag if int(y) % 2 else mag
        return float((x.ln() * y).exp())


def main() -> None:
    rng = random.Random(20260929)
    answers: list[dict] = []

    def add(fn: str, f, *args: object) -> None:  # noqa: ANN001
        a = {"fn": fn, "args": [arg(x) for x in args], "out": outcome(lambda: f(*args))}
        if fn in ("exp", "log", "log10", "pow") and not a["out"].startswith("!"):
            cr = outcome(lambda: correctly_rounded(fn, *args))
            if cr != "skip":
                a["cr"] = cr
        answers.append(a)

    floats = SPECIAL + rand_floats(rng, 1500)
    ints = INTS + [rng.randint(INT_MIN, INT_MAX) for _ in range(100)]
    ints += [rng.randint(-1000, 1000) for _ in range(100)]
    for x in floats + ints:
        for fn in ("floor", "ceil", "trunc", "sqrt", "exp", "log", "log10", "fabs"):
            add(fn, getattr(math, fn), x)
    pos = [abs(x) for x in floats if x == x] + [abs(i) for i in ints if i and abs(i) <= INT_MAX]
    for _ in range(1500):
        add("log", math.log, rng.choice(pos + floats), rng.choice(pos + floats + [1.0, 1, 2, 0]))
    for _ in range(3000):
        a = rng.choice(floats + ints)
        b = rng.choice([*floats, *ints, 0.5, 2.0, -1.0, 3.0, 0.0, -0.0, 1.0 / 3])
        add("pow", math.pow, a, b)
    for x in SPECIAL:
        for y in SPECIAL:
            add("pow", math.pow, x, y)
            add("isclose", math.isclose, x, y)
    for _ in range(500):
        a = rng.choice(floats)
        add("isclose", math.isclose, a, a * (1 + rng.choice([1e-9, 1e-10, 2e-9, -1e-9, 0.99e-9])))
    for _ in range(600):
        add("gcd", math.gcd, rng.choice(ints), rng.choice(ints))
    for a in (0, INT_MIN, INT_MAX, 12, -18):
        for b in (0, INT_MIN, INT_MAX, 12, -18):
            add("gcd", math.gcd, a, b)
    OUT.write_text(json.dumps(answers, separators=(",", ":")) + "\n")
    print(f"{len(answers)} answers -> {OUT}")


if __name__ == "__main__":
    main()
