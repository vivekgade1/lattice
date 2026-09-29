"""The Go T2 emitter (plan 5a Task 8): generated Go compiles, and runs like Python.

The shared semantics suite (tests/semantics_v11/test_go.py) is the main proof; the
tests here pin the emitter's shape and the programs the suite does not cover:
control flow through try/except in loops, records, module constants, keyword
arguments, optional values and comprehension scoping, each run on both targets."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from textwrap import dedent

import pytest

from lattice.check import check_source
from lattice.gogen.t2 import emit_go_t2, go_float, go_string
from lattice.t2 import compile_program, t2_load
from lattice.t2ir.lower import lower_t2
from tests.check.t2_support import PROFILE
from tests.semantics_v11.cases import CASES, Case
from tests.semantics_v11.gorun import run_go, run_go_budgets, run_python_budget
from tests.t2.util import checked
from tests.t2ir.corpus import all_t2_calls


def test_emits_compilable_go(tmp_path):
    program = checked("""
        @procedure
        def total(xs: list[Int]) -> Int:
            s = 0
            for x in xs:
                if x > 0:
                    s += x
            return s
    """).program
    src = emit_go_t2(lower_t2(program.t2, None))
    assert "func P_total(fuel *rt.Fuel, xs *rt.List[int64]) int64" in src
    assert "fuel.Tick()" in src


def test_go_implements_every_t2_call():
    """Plan 5a Task 10: every T2 call target (builtin, method, stdlib function) is
    implemented on Go; what Go still refuses is a construct (L892 gates), not a call."""
    from lattice.catalog import T2_METHODS, T2_STDLIB
    from lattice.t2.support import T2_BUILTIN_NAMES
    from lattice.targets import GO_T2_CALLS

    wanted = {f"builtin:{b}" for b in T2_BUILTIN_NAMES if b[0].islower()}
    wanted |= {f"method:{k}.{m}" for k, ms in T2_METHODS.items() for m in ms}
    wanted |= {f"std:{mod}.{name}" for mod, names in T2_STDLIB.items() for name in names}
    assert wanted - GO_T2_CALLS == set()


def test_go_literals():
    assert go_string('a"b\\c\né👋\x01') == '"a\\"b\\\\c\\n\\u00e9\\U0001f44b\\x01"'
    assert go_float(float("inf")) == "math.Inf(1)"
    assert go_float(-0.0) == "math.Copysign(0, -1)"
    assert go_float(0.1) == "math.Float64frombits(0x3fb999999999999a)"


def test_a_charged_method_call_evaluates_its_receiver_first(monkeypatch):
    """emit_py's `<fuel>.call(recv.m)(args)`: receiver, then the step, then the
    arguments (`method` is stubbed, so only the order is looked at)."""
    from lattice.gogen import t2 as gen

    monkeypatch.setattr(gen._Proc, "method", lambda self, c, recv: f"strings.ToUpper({recv})")
    program = checked("""
        @procedure
        def f(xs: list[Str]) -> Str:
            return xs[0].upper()
    """).program
    src = emit_go_t2(lower_t2(program.t2, None))
    receiver = src.index("xs.Get(int64(0))")
    assert receiver < src.index("fuel.Tick()") < src.index("strings.ToUpper(")
    assert "rt.After(fuel.Step(), strings" not in src


def test_none_procedures_return_a_value():
    program = checked("""
        @procedure
        def g(n: Int) -> None:
            return

        @procedure
        def f(n: Int) -> Str:
            return str(g(n))
    """).program
    src = emit_go_t2(lower_t2(program.t2, None))
    assert "func P_g(fuel *rt.Fuel, n int64) struct{}" in src
    assert "return struct{}{}" in src


def test_float_arithmetic_is_rounded_per_operation():
    """Go may fuse x * y + z into one multiply-add; every Float + - * is converted
    explicitly (float64(...)), which rounds and prevents fusion."""
    program = checked("""
        @procedure
        def f(x: Float, y: Float, z: Float) -> Float:
            a = x * y
            return x * y + z - a
    """).program
    src = emit_go_t2(lower_t2(program.t2, None))
    assert "a = float64(x * y)" in src
    assert "float64(float64(float64(x * y) + z) - a)" in src


def test_go_names_never_clash():
    program = checked("""
        @procedure
        def f(type: Int, fuel: Int, P_f: Int, t_1: Int) -> Int:
            len = type + fuel
            return len + P_f + t_1
    """).program
    src = emit_go_t2(lower_t2(program.t2, None))
    assert "x_type_1 int64" in src and "x_fuel_1 int64" in src and "x_P_f_1 int64" in src


def P(src: str) -> str:
    return dedent(src).lstrip("\n")


# Programs beyond the shared suite, run on Python first (the reference) and then on Go.
PROGRAMS = [
    Case(
        "try_return_in_loop",
        P("""
            @procedure
            def f(xs: list[Str]) -> Int:
                n = 0
                for s in xs:
                    n += 1
                    try:
                        v = int(s)
                        if v < 0:
                            return v * 100 + n
                    except ValueError:
                        if s == "end":
                            return -n
                        continue
                    n += 10
                return n
        """),
        "f",
        (["1", "x", "2", "end", "-3"],),
        None,
    ),
    Case(
        "nested_try",
        P("""
            @procedure
            def f(d: dict[Str, Int], k: Str) -> Str:
                out = ""
                try:
                    try:
                        out += str(d[k])
                    except KeyError as e:
                        out += "inner " + str(e)
                        out += str(1 // d["zero"])
                    else:
                        out += " ok"
                except ZeroDivisionError as e:
                    out += " outer " + str(e)
                return out
        """),
        "f",
        ({"a": 1, "zero": 0}, "b"),
        None,
    ),
    Case(
        "records_and_constants",
        P("""
            LIMIT = 3
            NAMES = ["x", "y"]

            @record
            class Point:
                x: Int
                y: Int = 0
                label: Str | None = None

            @procedure
            def f(n: Int) -> list[Str]:
                ps = [Point(y=i, x=i * LIMIT) for i in range(n)]
                q = Point(1, label="q")
                out = [str(q), f"{q!r}", str(q == Point(1, 0, "q")), str(q.label)]
                for p in ps:
                    if p.label is None and p.x > 0:
                        out += [NAMES[p.y % 2] + str(p.x)]
                return out
        """),
        "f",
        (3,),
        None,
    ),
    Case(
        "keyword_arguments_in_written_order",
        P("""
            @procedure
            def g(a: Str, b: Str) -> Str:
                return a + b

            @procedure
            def f(xs: list[Int], s: Str) -> Str:
                # b is evaluated first: int(s) fails before xs[10] would
                return g(b=str(int(s)), a=str(xs[10]))
        """),
        "f",
        ([1, 2, 3], "x"),
        None,
    ),
    Case(
        "optional_values",
        P("""
            @procedure
            def f(xs: list[Int | None]) -> list[Str]:
                out: list[Str] = []
                best: Int | None = None
                for x in xs:
                    if x is not None and (best is None or x > best):
                        best = x
                    out += [f"{x}|{x!r}|{x == 3}|{x is None}"]
                return out + [str(best)]
        """),
        "f",
        ([None, 3, 1],),
        None,
    ),
    Case(
        "comprehension_scopes_and_generators",
        P("""
            @procedure
            def f(x: Str) -> list[Str]:
                xs = [x + str(i) for i in range(3)]
                pairs = [(a, b) for a in range(3) for b in range(a) if a != b]
                total = sum(a * b for a, b in pairs)
                return xs + [x, str(total), str(len(pairs)), str(all(a > b for a, b in pairs))]
        """),
        "f",
        ("k",),
        None,
    ),
    Case(
        "min_max_sorted_sum_mixed",
        P("""
            @procedure
            def f(xs: list[Float], ns: list[Int]) -> list[Float]:
                return [min(xs), max(xs, key=lambda v: -v), max(2.0, 3.5), sum(ns, 0.5),
                        sum(xs), round(sum(sorted(xs, reverse=True)[:2]), 3), float(min(ns))]
        """),
        "f",
        ([2.5, -1.25, 7.0, 0.1], [3, 1, 2]),
        None,
    ),
    Case(
        "strings_and_truth",
        P("""
            @procedure
            def f(s: Str, n: Int) -> list[Str]:
                t = s * n
                parts = [c for c in t if c]
                empty = "" or "fallback"
                both = s and t
                return [t[1:-1], t[::-1], str(len(parts)), empty, both, str(not s),
                        str("é" in t), f"{n:08.3f}|{s!a}|{s:*>6}"]
        """),
        "f",
        ("hé", 3),
        None,
    ),
    Case(
        "while_break_else_less",
        P("""
            @procedure
            def f(n: Int) -> Int:
                i = 0
                total = 0
                while True:
                    i += 1
                    if i % 2 == 0:
                        continue
                    if i > n:
                        break
                    total += i
                return total
        """),
        "f",
        (9,),
        None,
    ),
    Case(
        "dict_items_order_and_membership",
        P("""
            @procedure
            def f(ws: list[Str]) -> list[Int]:
                counts: dict[Str, Int] = {}
                for w in ws:
                    counts[w] = counts[w] + 1 if w in counts else 1
                counts["b"] += 10
                return [counts[w] for w in counts] + [len(counts), 1 if "z" not in counts else 0]
        """),
        "f",
        (["b", "a", "b", "c"],),
        None,
    ),
    Case(
        "fuel_exhaustion_point",
        P("""
            @procedure
            def f(n: Int) -> Int:
                xs = [i for i in range(n)]
                return len(xs)
        """),
        "f",
        (100,),
        None,
        budget=60,
    ),
    Case(
        "augmented_items_and_in_place_containers",
        P("""
            @procedure
            def f(ws: list[Str], n: Int) -> list[Str]:
                d: dict[Str, Int] = {w: 0 for w in ws}
                for w in ws:
                    d[w] += len(w)
                xs = [1, 2, 3]
                xs[1] *= n
                xs[-1] -= 10
                ys = xs
                ys *= 2
                ys += [n]
                s = {1, 2}
                s |= {3, 1}
                s -= {2}
                s ^= {5, 1}
                e = {"a": 1}
                e |= {"b": 2, "a": 3}
                t = "ab"
                t += "c" * n
                t *= 2
                g = {k: v for k, v in zip(ws, range(10, 20))}
                return [str(d[w]) for w in d] + [str(x) for x in xs] + [str(v) for v in s] + [
                    str(e["a"]), str(len(e)), t, str(len(g)), str(g[ws[0]])]
        """),
        "f",
        (["aa", "b", "aa", "ccc"], 2),
        None,
    ),
    Case(
        "numbers_and_formats",
        P("""
            @procedure
            def f(n: Int, x: Float, s: Str) -> list[Str]:
                r = range(2, 20, 3)
                w = 7
                return [
                    str(5 in r), str(6 in r), str(n in (1, 2, 3)), str(min(n, 4, -2)),
                    str(max(x, float(n))), str(n < x), str(x >= n), str((1, "b") < (1, "c")),
                    str(int(-x)), str(float("  1_0.5 ")), str(int(" -12 ")), str(abs(-x)),
                    str(round(x)), str(round(-x, 1)), str(n / 3), str(x // 0.5), str(-7 % 3),
                    f"{s:>{w}}|{s:^{n}.2}|{x:+.3e}|{n:b}|{n:x}|{s!a}|{x!r}|{True:>3}",
                    str(2 ** 10), str(2 ** -2), str(x ** 2), str(1 << n), str(n >> 1),
                    str(-n // 2), str(~n), str(n & 6 | 1 ^ 3),
                ]
        """),
        "f",
        (5, 2.75, "héllo"),
        None,
    ),
    Case(
        "iterators_consumed_once",
        P("""
            @procedure
            def f(xs: list[Int]) -> list[Int]:
                z = zip(xs, reversed(xs), range(100))
                first = [a * 100 + b * 10 + c for a, b, c in z]
                again = list(z)
                e = enumerate(xs, 5)
                head = [i for i, _ in e if i < 7]
                rest = [v for _, v in e]
                return first + [len(again)] + head + rest + sorted(set(xs), reverse=True)
        """),
        "f",
        ([3, 1, 2, 3],),
        None,
    ),
    Case(
        "recursion_and_optional_parameters",
        P("""
            @procedure
            def fib(n: Int, memo: dict[Int, Int] | None) -> Int:
                if n < 2:
                    return n
                if memo is not None and n in memo:
                    return memo[n]
                return fib(n - 1, None) + fib(n - 2, memo)

            @procedure
            def f(n: Int) -> list[Int]:
                return [fib(i, {5: 500}) for i in range(n)]
        """),
        "f",
        (8,),
        None,
    ),
    Case(
        # the value of a procedure declared `-> None` is used: str(), return, a local, `is`
        "none_procedure_values",
        P("""
            @procedure
            def g(n: Int) -> None:
                if n > 0:
                    return
                return None

            @procedure
            def h(n: Int) -> None:
                return g(n)

            @procedure
            def f(n: Int) -> list[Str]:
                x = g(n)
                g(n)
                return [str(g(n)), str(h(n)), str(x), str(g(n) is None), f"{h(-n)}"]
        """),
        "f",
        (1,),
        None,
    ),
    Case(
        # R-widen: every kind of place an Int value takes a Float type (Task 8b)
        "widening_sites",
        P("""
            @record
            class Box:
                w: Float
                h: Float = 1

            @procedure
            def half(x: Float) -> Float:
                return x / 2

            @procedure
            def back(n: Int) -> Float:
                return n

            @procedure
            def f(n: Int, m: Int | None, flag: Bool, i: Int, xs: list[Int]) -> list[Str]:
                x: Float = n
                o: Float | None = m
                x2 = 0.5
                x2 = y = n
                a = 0.5
                a, b = n, i
                c = 0.5
                for c in xs:
                    pass
                ys = [n, 2.5]
                d = {n: n, 2.5: 0.5}
                e = {**{"k": n}, "j": 2.5}
                ys[0] = n
                d[n] = n + 1
                t = [v for v in (n, 2.5)]
                bx = Box(n)
                return [
                    str(x), str(o), str(x2), str(y), str(a), str(b), str(c), str(ys[0]),
                    str(d[n]), f"{e['k']}", str(x or n), str(0.0 or n), str(n and 2.5),
                    str(2.5 if flag else n), str((1, 2.5, 3)[i]), str(t[0]), str(half(n)),
                    str(back(n)), str(bx.w), str(bx.h), str(sum(ys[:0])), str(sum(ys[:0], n)),
                    str(max(n, 2.5)), str(min((n, 2.5))), str(round(n, 1)), str(n ** i),
                    str(round(n)), str(n in ys), str(float(n) in xs), str(len([k for k in d])),
                ]
        """),
        "f",
        (3, 4, False, 0, [1, 2]),
        None,
    ),
    Case(
        "index_error_inside_comprehension",
        P("""
            @procedure
            def f(xs: list[Int]) -> list[Int]:
                return [xs[i + 1] for i in range(len(xs))]
        """),
        "f",
        ([1, 2, 3],),
        None,
    ),
    # Task 9: strings and regular expressions in loops (the fuel sweep charges them)
    Case(
        "text_report",
        P("""
            import re
            import string
            import math
            from re import findall
            from string import digits

            @procedure
            def f(text: Str, width: Int) -> list[Str]:
                out: list[Str] = []
                re_1 = 0
                for line in text.splitlines():
                    key, sep, value = line.partition(":")
                    if not sep:
                        out += [line.strip().center(width, ".")]
                        continue
                    words = value.lower().split()
                    re_1 += len(findall(r"\\d+", value))
                    tag = "/".join(w.capitalize() for w in words if w.isalpha())
                    out += [f"{key.rstrip():>6}|{tag}|{value.count('a')}|{value.rfind('é')}"]
                    m = re.search(r"(?P<n>\\d+)(?:-(\\d+))?", value)
                    if m is not None:
                        out += [str(m) + (m.group(2) or "-") + str(m.group("n"))]
                        out += [re.sub(r"(\\d+)", r"<\\1>", value, )]
                out += [str(re_1) + digits[:2] + string.ascii_uppercase[-2:] + str(math.pi)]
                out += [",".join(re.split(r"\\s*;\\s*", "a ; b;c;;"))]
                out += ["|".join(text.rsplit("\\n", 1)).zfill(width).replace("a", "ä", 2)]
                return out
        """),
        "f",
        ("  intro  \nfirst: alpha 12 beta\nsecond: Été 3-4 à\nno colon a\n", 12),
        None,
    ),
    Case(
        "regex_scan_in_loop",
        P("""
            import re

            @procedure
            def f(xs: list[Str]) -> list[Str]:
                out: list[Str] = []
                for x in xs:
                    for w in re.findall(r"\\w+|$", x):
                        if re.fullmatch(r"(?i)[a-f]+", w) is not None:
                            out += [w.upper()]
                        elif w == "":
                            out += ["<end>"]
                    m = re.match(r"(a)?b", x)
                    out += ["none" if m is None else f"{m.start()}:{m.end()}:{m.group(1)}"]
                return out
        """),
        "f",
        (["bad cafe\n", "ab éb", "Bee FACE b", ""],),
        None,
    ),
    Case(
        # Task 10: records and tuples holding dates, durations and Json; sorting, sums of
        # durations, json.dumps of what the procedure built, str() of a record
        "events_with_time_and_json",
        P("""
            import json
            from collections import Counter

            @record
            class Event:
                at: DateTime
                took: Duration
                meta: Json

            @procedure
            def f(start: DateTime, spec: Str) -> list[Str]:
                data = json.loads(spec)
                events: list[Event] = []
                total = seconds(0)
                kinds: list[Str] = []
                for i, name in enumerate(["b", "a", "c"]):
                    took = minutes(i * 7.5) + seconds(0.25)
                    total = total + took
                    events.append(Event(at=start - hours(i * 5), took=took, meta=data[name]))
                    kinds.append(str(data[name]["kind"]))
                events.sort(key=lambda e: e.at)
                firsts = [(e.at.date(), e.took) for e in events]
                c: dict[Str, Int] = Counter(kinds)
                out = [str(events[0]), str(total), str(total / 3),
                       str(firsts[0][0]) + str(firsts[-1][1]),
                       json.dumps([e.meta for e in events], sort_keys=True),
                       json.dumps(c, indent=1), f"{events[-1].at:%a %j %p}"]
                return out
        """),
        "f",
        (
            datetime(2024, 3, 1, 2, 30, tzinfo=UTC),
            '{"a": {"kind": "x", "n": [1, 2.5]}, "b": {"kind": "y"}, '
            '"c": {"kind": "x", "z": null}}',
        ),
        None,
    ),
]


@pytest.fixture(scope="module")
def go_programs():
    return run_go(PROGRAMS)  # a Case's `expect` is unused here: Python gives it below


@pytest.mark.parametrize("case", PROGRAMS, ids=lambda c: c.name)
def test_program_runs_like_python(case, go_programs):
    assert check_source(case.source_with_header, PROFILE, target="go").ok
    expected, _ = run_python_budget(case, case.budget)  # strict form, as Go's
    assert expected[0] not in ("err", "exc") or case.name in (
        "fuel_exhaustion_point",
        "index_error_inside_comprehension",
        "keyword_arguments_in_written_order",
    ), expected
    assert go_programs[case.name] == expected


# ---- fuel: every budget up to what a run needs ------------------------------------------

SWEPT = [
    *PROGRAMS,
    *(c for c in CASES if check_source(c.source_with_header, PROFILE, target="go").ok),
]


def _budgets(case: Case) -> list[int]:
    """Every budget from 0 to what the case uses (at most 40), and the budgets just
    around what it uses: on each, Go must stop (R620) or finish exactly as Python."""
    _, left = run_python_budget(case, case.budget)
    used = case.budget - left if left >= 0 else case.budget
    return sorted(set(range(min(used, 40) + 1)) | {max(used - 1, 0), used, used + 1})


@pytest.fixture(scope="module")
def go_sweep():
    return run_go_budgets(SWEPT, {c.name: _budgets(c) for c in SWEPT})


@pytest.mark.parametrize("case", SWEPT, ids=lambda c: c.name)
def test_fuel_exhausts_at_the_same_point(case, go_sweep):
    diffs = []
    for b in _budgets(case):
        want = run_python_budget(case, b)
        got = go_sweep[(case.name, b)]
        if got != want:
            diffs.append((b, want, got))
    assert not diffs, diffs[:5]


# ---- the T2 IR corpus (tests/t2ir/corpus.py): every call Go accepts ------------------------


@dataclass(frozen=True)
class _CorpusCase:
    """A corpus call as gorun runs it (with the program's own profile)."""

    name: str
    source_with_header: str
    proc: str
    args: tuple
    budget: int
    profile: object
    program: object = field(compare=False)

    def load(self) -> dict:
        code = compile_program(self.program)  # type: ignore[arg-type]
        assert code is not None
        return t2_load(code.source)


def _corpus_cases() -> tuple[list[_CorpusCase], dict[str, list[int]]]:
    cases: dict[tuple, _CorpusCase] = {}
    budgets: dict[str, list[int]] = {}
    go_ok: dict[str, bool] = {}  # by source (each corpus source has one profile)
    for name, program, proc, args, budget in all_t2_calls():
        if program.source not in go_ok:
            go_ok[program.source] = check_source(program.source, program.profile, target="go").ok
        if not go_ok[program.source]:
            continue
        key = (program.source, proc, repr(args))
        if key not in cases:
            base = name.removesuffix("@10")
            cases[key] = _CorpusCase(
                base, program.source, proc, args, budget, program.profile, program
            )
        budgets.setdefault(cases[key].name, []).append(budget)
    return list(cases.values()), budgets


def test_corpus_calls_run_like_python():
    """Every corpus call Go accepts gives the same (result, fuel left) on both targets.
    Importing the corpus's loop fixtures registers their SQLModel tables in the global
    metadata, which tests/importer (run later) builds its database from: the tables
    this adds are taken out again (the fixtures build their own databases in SQL)."""
    from sqlmodel import SQLModel  # noqa: PLC0415

    before = set(SQLModel.metadata.tables)
    try:
        corpus, budgets = _corpus_cases()
    finally:
        for name in set(SQLModel.metadata.tables) - before:
            SQLModel.metadata.remove(SQLModel.metadata.tables[name])
    assert len(corpus) >= 60
    go = run_go_budgets(corpus, budgets)
    diffs = []
    for case in corpus:
        for b in budgets[case.name]:
            result, left = run_python_budget(case, b)
            if go[(case.name, b)] != (result, left):
                diffs.append((case.name, b, (result, left), go[(case.name, b)]))
    assert not diffs, diffs[:5]


# ---- Task 9: regular expressions ----------------------------------------------------------


def test_each_pattern_is_compiled_once_as_python_compiles_it():
    import re  # noqa: PLC0415

    from lattice.gogen.regex import compile_regex  # noqa: PLC0415

    program = checked(r"""
        import re

        @procedure
        def f(s: Str) -> Int:
            n = len(re.findall(r"\d+", s))
            if re.search(r"\d+", s) is None:
                return n
            return n + len(re.split(r"\s", s))
    """).program
    src = emit_go_t2(lower_t2(program.t2, None))
    assert src.count("rt.NewRegex(") == 2
    assert "rt.ReFindall(re_1, rt.Scan(fuel, s))" in src
    assert "rt.ReSearch(re_1, rt.Scan(fuel, s))" in src
    code = compile_regex(r"\d+").code
    assert f"[]uint32{{{', '.join(map(str, code))}}}" in src
    assert compile_regex(r"\d+").groups == re.compile(r"\d+").groups


@pytest.mark.parametrize(
    ("pattern", "repl"),
    [
        (r"(\w+)@(\w+)", r"\2@\1"),
        (r"(?P<a>[a-z]+)-(?P<b>[a-z]+)", r"\g<b>-\g<a>\g<0>"),
        (r"(a)(b)?", r"[\2|\g<1>|\\|\n|\t|\x41|\101|$1|\$]"),
        (r"x", "$1 ${1} $$"),
        (r"(a)", "\\"),
        (r"(a)", r"\3"),
        (r"(a)", r"\q"),
        (r"(a)", r"\g<nope>"),
    ],
)
def test_replacements_compile_as_python_expands_them(pattern, repl):
    """compile_template's chunks, expanded over each match, give re.sub's result (or its
    exception)."""
    import re  # noqa: PLC0415

    from lattice.gogen.regex import compile_template  # noqa: PLC0415

    t = compile_template(pattern, repl)
    s = "alice@example ab a foo-bar x"
    try:
        want = re.sub(pattern, repl, s, flags=re.ASCII)
    except (re.error, IndexError) as e:  # \g<nope> is an IndexError (catchable in T2)
        kind = "PatternError" if isinstance(e, re.error) else type(e).__name__
        assert t.error == (kind, str(e))
        return
    assert t.error is None

    def expand(m: re.Match[str]) -> str:
        out = t.lits[0]
        for g, lit in zip(t.groups, t.lits[1:], strict=True):
            out += (m.group(g) or "") + lit
        return out

    assert re.sub(pattern, expand, s, flags=re.ASCII) == want
