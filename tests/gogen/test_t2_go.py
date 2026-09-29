"""The Go T2 emitter (plan 5a Task 8): generated Go compiles, and runs like Python.

The shared semantics suite (tests/semantics_v11/test_go.py) is the main proof; the
tests here pin the emitter's shape and the programs the suite does not cover:
control flow through try/except in loops, records, module constants, keyword
arguments, optional values and comprehension scoping, each run on both targets."""

import dataclasses
from dataclasses import dataclass, field
from textwrap import dedent

import pytest

from lattice.check import check_source
from lattice.gogen.t2 import emit_go_t2, go_float, go_string
from lattice.t2 import compile_program, t2_load
from lattice.t2.support import T2Set
from lattice.t2ir.lower import lower_t2
from tests.check.t2_support import PROFILE
from tests.semantics_v11.cases import CASES, Case, Err, Exc, run_python
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


def test_go_literals():
    assert go_string('a"b\\c\né👋\x01') == '"a\\"b\\\\c\\n\\u00e9\\U0001f44b\\x01"'
    assert go_float(float("inf")) == "math.Inf(1)"
    assert go_float(-0.0) == "math.Copysign(0, -1)"
    assert go_float(0.1) == "math.Float64frombits(0x3fb999999999999a)"


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
]


@pytest.fixture(scope="module")
def go_programs():
    return run_go(PROGRAMS)  # a Case's `expect` is unused here: Python gives it below


@pytest.mark.parametrize("case", PROGRAMS, ids=lambda c: c.name)
def test_program_runs_like_python(case, go_programs):
    assert check_source(case.source_with_header, PROFILE, target="go").ok
    expected = run_python(case)
    assert not isinstance(expected, Err | Exc) or case.name in (
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


def _norm(v: object) -> object:
    """A Python result in the form gorun decodes Go's to (records as tuples)."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type | Err | Exc):
        fields = tuple(_norm(getattr(v, f.name)) for f in dataclasses.fields(v))
        return ("record", type(v).__name__, fields)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_norm(x) for x in v)
    if isinstance(v, dict):
        return {_norm(k): _norm(x) for k, x in v.items()}
    if isinstance(v, T2Set):
        return set(v)
    return v


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
            if go[(case.name, b)] != (_norm(result), left):
                diffs.append((case.name, b, (_norm(result), left), go[(case.name, b)]))
    assert not diffs, diffs[:5]
