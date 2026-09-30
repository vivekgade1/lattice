"""Regenerates time_answers.json: what Python 3.14's datetime answers for the Date,
DateTime (an aware UTC datetime) and Duration (timedelta) operations T2 procedures
use: timedelta(...) of Ints and Floats, the arithmetic, comparisons, text (str, repr,
isoformat, strftime of the directives Go implements), attributes and errors.
times_test.go asserts against it. Run from the repository root:

    uv run python lattice/gogen/rt/testdata/gen_time_answers.py

Each answer is {"op": name, "args": [...], "out": text}. Arguments: a Duration is
["D", days, seconds, microseconds], a Date ["d", ordinal], a DateTime ["t",
microseconds since the epoch], an Int ["i", decimal], a Float ["f", float.hex], a str
["s", text], an absent timedelta argument None. `out` renders a Duration as
"D:days,seconds,us", a Date as "d:ordinal", a DateTime as "t:us", an int, a float
repr, a bool True/False, a str as itself; "!<Kind>: <message>" for an exception.
"""

# ruff: noqa: E501 - data: long program texts and answers read better on one line

from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

OUT = Path(__file__).with_name("time_answers.json")
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
TD = dt.timedelta
UNITS = ("seconds", "minutes", "hours", "days", "weeks")
FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%a %A %b %B", "%j|%p|%y|%f|%%", "%Y%m%d", "x%%Y%%", "%H:%M:%S.%f %p",
    "", "no directives", "%d/%m/%y é👋", "%%%Y%%",
]  # fmt: skip


def enc(v: object) -> list:
    if isinstance(v, TD):
        return ["D", v.days, v.seconds, v.microseconds]
    if isinstance(v, dt.datetime):
        return ["t", (v - EPOCH) // TD(microseconds=1)]
    if isinstance(v, dt.date):
        return ["d", v.toordinal()]
    if isinstance(v, bool):
        raise TypeError(v)
    if isinstance(v, int):
        return ["i", str(v)]
    if isinstance(v, float):
        return ["f", float.hex(v)]
    if isinstance(v, str):
        return ["s", v]
    if v is None:
        return None  # type: ignore[return-value]
    raise TypeError(v)


def render(v: object) -> str:
    if isinstance(v, TD):
        return f"D:{v.days},{v.seconds},{v.microseconds}"
    if isinstance(v, dt.datetime):
        return f"t:{(v - EPOCH) // TD(microseconds=1)}"
    if isinstance(v, dt.date):
        return f"d:{v.toordinal()}"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int) and not (-(2**63) <= v < 2**63):
        return "!R623"
    return str(v)


def outcome(f) -> str:  # noqa: ANN001
    try:
        return render(f())
    except Exception as e:  # noqa: BLE001 - the answer is the exception
        return f"!{type(e).__name__}: {e}"


def main() -> None:  # noqa: C901, PLR0915
    rng = random.Random(20260929)
    answers: list[dict] = []

    def add(op: str, f, *args: object) -> None:  # noqa: ANN001
        answers.append({"op": op, "args": [enc(a) for a in args], "out": outcome(lambda: f(*args))})

    def num(big: bool = False) -> object:
        r = rng.random()
        if r < 0.3:
            return rng.randint(-100, 100)
        if r < 0.45:
            return rng.randint(-(2**40), 2**40) if big else rng.randint(-100000, 100000)
        if r < 0.75:
            return rng.uniform(-1000, 1000)
        if r < 0.85:
            return rng.choice([0.5, -0.5, 1.5, 2.5, 1e-7, 5e-7, -5e-7, 0.4999999, 1e-6 / 3, 0.1])
        if r < 0.9:
            return rng.choice([float("nan"), float("inf"), -float("inf"), 1e300, 2**62, -(2**62)])
        return rng.uniform(-1e9, 1e9)

    def durations(n: int) -> list[TD]:
        out = [TD(0), TD(1), TD(-1), TD(seconds=1), TD(microseconds=1), TD(microseconds=-1),
               TD(days=999999999, hours=23, minutes=59, seconds=59, microseconds=999999),
               TD(days=-999999999), TD(days=1, seconds=5), TD(days=-1, seconds=5)]  # fmt: skip
        while len(out) < n:
            try:
                out.append(TD(days=rng.randint(-10**6, 10**6), seconds=rng.randint(0, 86399),
                              microseconds=rng.randint(0, 999999)))  # fmt: skip
                if rng.random() < 0.3:
                    out.append(TD(microseconds=rng.randint(-(10**7), 10**7)))
            except OverflowError:
                pass
        return out

    # timedelta(...) of Int and Float arguments
    for _ in range(3000):
        kw = {u: num(big=rng.random() < 0.2) for u in UNITS if rng.random() < 0.4}
        args = [kw.get(u) for u in UNITS]
        add(
            "timedelta",
            lambda *a: TD(**{u: x for u, x in zip(UNITS, a, strict=True) if x is not None}),
            *args,
        )
    for u in UNITS:
        for x in (0.5, 1.5, 2.5, -0.5, -1.5, 1e-6, 5e-7, 1.5e-6, 2.5e-6, 1 / 3, 2 / 3):
            add("timedelta", lambda *a: TD(**{u2: x2 for u2, x2 in zip(UNITS, a, strict=True) if x2 is not None}),
                *[x if u2 == u else None for u2 in UNITS])  # fmt: skip

    ds = durations(300)
    for _ in range(2500):
        a, b = rng.choice(ds), rng.choice(ds)
        op = rng.choice(["add", "sub", "neg", "mulint", "mulfloat", "divint", "divfloat",
                         "div", "floordiv", "floordivint", "mod", "cmp", "total_seconds",
                         "str", "repr", "bool"])  # fmt: skip
        if op == "add":
            add(op, lambda x, y: x + y, a, b)
        elif op == "sub":
            add(op, lambda x, y: x - y, a, b)
        elif op == "neg":
            add(op, lambda x: -x, a)
        elif op == "mulint":
            add(op, lambda x, n: x * n, a, rng.choice([0, 1, -1, 2, 3, 1000, -(10**9), 10**12]))
        elif op == "mulfloat":
            add(
                op,
                lambda x, f: x * f,
                a,
                rng.choice([0.5, -0.5, 1 / 3, 2.5, 1e-7, 1e10, float("nan"), float("inf")]),
            )
        elif op == "divint":
            add(op, lambda x, n: x / n, a, rng.choice([0, 1, -1, 2, 3, 7, 1000, 10**12]))
        elif op == "divfloat":
            add(
                op,
                lambda x, f: x / f,
                a,
                rng.choice([0.0, 0.5, -0.5, 1 / 3, 2.5, 1e-7, float("nan"), float("inf")]),
            )
        elif op == "div":
            add(op, lambda x, y: x / y, a, b)
        elif op == "floordiv":
            add(op, lambda x, y: x // y, a, b)
        elif op == "floordivint":
            add(op, lambda x, n: x // n, a, rng.choice([0, 1, -1, 2, 3, 7, 1000]))
        elif op == "mod":
            add(op, lambda x, y: x % y, a, b)
        elif op == "cmp":
            add(op, lambda x, y: (x > y) - (x < y), a, b)
        elif op == "total_seconds":
            add(op, lambda x: x.total_seconds(), a)
        elif op == "str":
            add(op, str, a)
        elif op == "repr":
            add(op, repr, a)
        elif op == "bool":
            add(op, bool, a)

    # dates
    for y, m, d in [(2024, 2, 29), (2023, 2, 29), (0, 1, 1), (10000, 1, 1), (2024, 13, 1), (2024, 0, 5),
                    (2024, 4, 31), (1, 1, 1), (9999, 12, 31), (2**40, 1, 1), (-(2**40), 1, 1),
                    (2024, 2**31, 1), (2024, 1, -(2**31) - 1), (2024, 1, 0)]:  # fmt: skip
        add("date", dt.date, y, m, d)
    dates = [dt.date(1, 1, 1), dt.date(9999, 12, 31), dt.date(2024, 3, 15), dt.date(999, 1, 2),
             dt.date(5, 1, 2), dt.date(2000, 12, 31)]  # fmt: skip
    dates += [dt.date.fromordinal(rng.randint(1, 3652059)) for _ in range(200)]
    for _ in range(1500):
        a, b = rng.choice(dates), rng.choice(dates)
        dur = rng.choice(ds)
        op = rng.choice(["dadd", "dsub", "ddiff", "weekday", "isoweekday", "iso", "repr", "strftime",
                         "attrs"])  # fmt: skip
        if op == "dadd":
            add(op, lambda x, y: x + y, a, dur)
        elif op == "dsub":
            add(op, lambda x, y: x - y, a, dur)
        elif op == "ddiff":
            add(op, lambda x, y: x - y, a, b)
        elif op == "weekday":
            add(op, lambda x: x.weekday(), a)
        elif op == "isoweekday":
            add(op, lambda x: x.isoweekday(), a)
        elif op == "iso":
            add(op, lambda x: x.isoformat(), a)
        elif op == "repr":
            add(op, repr, a)
        elif op == "strftime":
            add(op, lambda x, f: x.strftime(f), a, rng.choice(FORMATS))
        elif op == "attrs":
            add(op, lambda x: f"{x.year},{x.month},{x.day}", a)

    # datetimes (aware UTC)
    lo = (dt.datetime(1, 1, 1, tzinfo=dt.UTC) - EPOCH) // TD(microseconds=1)
    hi = (dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC) - EPOCH) // TD(
        microseconds=1
    )
    times = [
        EPOCH + TD(microseconds=us) for us in (lo, hi, 0, -1, 1, 1710510307000000, 1710510307123456)
    ]
    times += [EPOCH + TD(microseconds=rng.randint(lo, hi)) for _ in range(200)]
    times += [EPOCH + TD(seconds=rng.randint(-(10**9), 10**9)) for _ in range(50)]
    for _ in range(2000):
        a, b = rng.choice(times), rng.choice(times)
        dur = rng.choice(ds)
        op = rng.choice(["tadd", "tsub", "tdiff", "weekday", "iso", "str", "repr", "strftime", "attrs",
                         "timestamp", "date"])  # fmt: skip
        if op == "tadd":
            add(op, lambda x, y: x + y, a, dur)
        elif op == "tsub":
            add(op, lambda x, y: x - y, a, dur)
        elif op == "tdiff":
            add(op, lambda x, y: x - y, a, b)
        elif op == "weekday":
            add(op, lambda x: x.weekday(), a)
        elif op == "iso":
            add(op, lambda x: x.isoformat(), a)
        elif op == "str":
            add(op, str, a)
        elif op == "repr":
            add(op, repr, a)
        elif op == "strftime":
            add(op, lambda x, f: x.strftime(f), a, rng.choice(FORMATS))
        elif op == "attrs":
            add(
                op,
                lambda x: (
                    f"{x.year},{x.month},{x.day},{x.hour},{x.minute},{x.second},{x.microsecond}"
                ),
                a,
            )
        elif op == "timestamp":
            add(op, lambda x: x.timestamp(), a)
        elif op == "date":
            add(op, lambda x: x.date(), a)
    OUT.write_text(json.dumps(answers, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"{len(answers)} answers -> {OUT}")


if __name__ == "__main__":
    main()
