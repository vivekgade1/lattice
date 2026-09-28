"""Every T2 program the existing test suites check, gathered in one place so the T2 IR
tests can lower (and, later, emit and run) each of them.

`all_t2_programs()` yields `(name, CheckedProgram)` for:

* `tests/api/t2prog.py` and `tests/api/hardening.py` (their `PROGRAM`, with their own
  `PROFILE`);
* every `tests/fixtures/loops/*.py` item whose `LATTICE` program has a `@procedure`
  (with the item's `PROFILE`);
* every source in `SOURCES` of `tests/t2/test_fuel.py` and `tests/t2/test_size_fuel.py`
  (with the T2 test profile, as `tests.t2.util.checked` checks them).
"""

from __future__ import annotations

import importlib
import random
from collections.abc import Callable, Iterator
from pathlib import Path

from lattice.check import CheckedProgram, check_source
from lattice.check.t2 import types as T
from lattice.profile import Profile
from tests.t2.util import checked

_LOOPS = Path(__file__).resolve().parents[1] / "fixtures" / "loops"


def _program(source: str, profile: Profile) -> CheckedProgram:
    result = check_source(source, profile)
    assert result.ok, "\n".join(d.render() for d in result.diagnostics)
    assert result.program is not None and result.program.t2 is not None
    return result.program


def _api_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for module_name in ("t2prog", "hardening"):
        module = importlib.import_module(f"tests.api.{module_name}")
        yield f"api/{module_name}", _program(module.PROGRAM, module.PROFILE)


def _loop_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for path in sorted(_LOOPS.glob("l*.py")):
        module = importlib.import_module(f"tests.fixtures.loops.{path.stem}")
        source = getattr(module, "LATTICE", None)
        if isinstance(source, str) and "@procedure" in source:
            yield f"loops/{path.stem}", _program(source, module.PROFILE)


def _t2_test_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for module_name in ("test_fuel", "test_size_fuel"):
        module = importlib.import_module(f"tests.t2.{module_name}")
        for i, source in enumerate(module.SOURCES):
            program = checked(source).program
            assert program is not None and program.t2 is not None
            yield f"t2/{module_name}[{i}]", program


# Each collector yields (name, program); add a collector here to widen the corpus.
_COLLECTORS: tuple[Callable[[], Iterator[tuple[str, CheckedProgram]]], ...] = (
    _api_programs,
    _loop_programs,
    _t2_test_programs,
)


def all_t2_programs() -> Iterator[tuple[str, CheckedProgram]]:
    """Every checked T2 program of the corpus, named by where it comes from."""
    for collect in _COLLECTORS:
        yield from collect()


# ---- calls: (name, program, procedure, arguments, budget) ----------------------------------

_GOLDEN = Path(__file__).resolve().parent / "golden"

# Extra procedures for the emitter's differential test: the constructs the suites above
# use little or not at all (sets, Counter, dicts, f-strings, chained and tuple
# assignment, while/break/continue, try, nested comprehensions).
EXTRA_SOURCES: tuple[str, ...] = (
    """
from collections import Counter, defaultdict

@procedure
def sets(xs: list[Str], ys: list[Str]) -> list[Str]:
    s = set(xs)
    t = {y for y in ys if y != ""}
    u = s | t
    u |= {"z"}
    v = s & t
    w = s - t
    x = s ^ t
    s.add("new")
    s.discard("new")
    s.update(ys)
    both = s.union(ys).intersection(xs).difference(["a"])
    flags = [s.issubset(u), s.issuperset(w), "a" in s, len(v) > 0]
    out = [f"{len(u)}{len(v)}{len(w)}{len(x)}{len(both)}{flags}"]
    out.extend(sorted(u))
    out.extend(z for z in x)
    return out


@procedure
def counts(xs: list[Str], n: Int) -> list[Str]:
    c = Counter(xs)
    c["extra"] += n
    groups: dict[Str, list[Str]] = defaultdict(list)
    for x in xs:
        groups[x[:1]].append(x)
    top = [f"{k}={v}" for k, v in c.most_common(2)]
    return top + [f"{k}:{len(v)}" for k, v in groups.items()]
""",
    """
import json
import math

LIMIT = 3
WORDS = ["a", "bb", "ccc"]

@procedure
def misc(n: Int, s: Str, flag: Bool) -> Str:
    a = b = n * 2
    p, q = divmod_like(a, 3)
    d = {"k": p, "j": q}
    e = {**d, "m": b}
    total = 0
    i = 0
    while i < n:
        i += 1
        if i % 2 == 0:
            continue
        if i > LIMIT * 5:
            break
        total += i
    else_part = "yes" if flag and total > 0 or not s else "no"
    try:
        v = e[s]
    except KeyError:
        v = -1
    m = len(s)
    if m > 2:
        total += m
    squares = {k: k * k for k in range(n % 7)}
    pairs = [(x, y) for x in range(3) for y in WORDS if len(y) > x]
    text = json.dumps(e, sort_keys=True)
    root = math.floor(math.sqrt(abs(n)))
    parts = [str(total), else_part, str(v), str(len(squares)), str(len(pairs)), text, str(root)]
    return "|".join(parts) + f"{s!r:>{LIMIT}}" + f"{n:05d}" + f"{1.5 * n:.1f}"


@procedure
def divmod_like(a: Int, b: Int) -> tuple[Int, Int]:
    return a // b, a % b
""",
    """
@procedure
def strs(s: Str, xs: list[Str]) -> list[Str]:
    out = [s.strip(), s.lower(), s.upper(), s.title(), s[::-1], s[1:3], s * 2]
    out += s.split(" ") + s.rsplit(" ", 1)
    out.append(s.replace("a", "") + s.zfill(4) + s.rjust(3, "*"))
    out.append(str(s.find("a")) + str(s.count("a")) + str(s.startswith("a")))
    head, sep, tail = s.partition(" ")
    out.append(head + sep + tail)
    for i, x in enumerate(xs):
        out.append(f"{i}:{x}")
    for x, y in zip(xs, reversed(xs)):
        out.append(x + y)
    ys = sorted(xs, key=lambda v: len(v), reverse=True)
    if xs:
        out.append(min(xs) + max(xs, key=lambda v: len(v)))
        out.append(xs[-1] + xs[0])
    out.append(",".join(ys))
    out.append(str(any(x == s for x in xs)) + str(all(x != "" for x in xs)))
    return out


@procedure
def nums(xs: list[Int], k: Int) -> list[Int]:
    ys = xs.copy()
    ys.sort()
    ys.reverse()
    if ys:
        ys.pop()
    ys.insert(0, k)
    zs = [x * k - 1 for x in ys if x != k] + [-k, abs(k), k // 3 if k else 0]
    zs.append(sum(xs) + len(xs) + round(k / 2))
    zs.append(int(k * 1.5) + (k << 2) + (k >> 1) + (k & 3) + (k | 1) + (k ^ 5) + ~k)
    zs.append(k ** 2 + sum(xs, 10))
    return zs
""",
)


def _golden_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for path in sorted(_GOLDEN.glob("*.lattice.py")):
        program = checked(path.read_text()).program
        assert program is not None and program.t2 is not None
        yield f"golden/{path.name.removesuffix('.lattice.py')}", program


def _extra_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for i, source in enumerate(EXTRA_SOURCES):
        program = checked(source).program
        assert program is not None and program.t2 is not None
        yield f"extra[{i}]", program


_INTS = (0, 1, -1, 2, 3, -7, 10, 42, 1000)
_STRS = ("", "a", "ab c", "héllo wörld", "日本", "a,b", "x1 y2", "ABC def", "  pad  ")


def _sample(t: object, rng: random.Random) -> object:
    if t == T.INT:
        return rng.choice(_INTS)
    if t == T.FLOAT:
        return rng.choice((0.0, 1.5, -2.25, 10.0, 3.75))
    if t == T.BOOL:
        return rng.choice((True, False))
    if t == T.STR:
        return rng.choice(_STRS)
    if t == T.ListT(T.INT):
        return [rng.choice(_INTS) for _ in range(rng.randrange(0, 6))]
    if t == T.ListT(T.STR):
        return [rng.choice(_STRS) for _ in range(rng.randrange(0, 6))]
    raise TypeError(t)


_SAMPLED = (T.INT, T.FLOAT, T.BOOL, T.STR, T.ListT(T.INT), T.ListT(T.STR))


def _generated(program: CheckedProgram, rng: random.Random) -> Iterator[tuple[str, tuple]]:
    """5 argument tuples for each procedure whose parameters are all simple."""
    assert program.t2 is not None
    for proc in program.t2.procedures.values():
        types = [t for _, t in proc.sig.params]
        if all(t in _SAMPLED for t in types):
            for _ in range(5):
                yield proc.name, tuple(_sample(t, rng) for t in types)


def _test_calls() -> Iterator[tuple[str, CheckedProgram, str, tuple, int]]:
    for module_name in ("test_fuel", "test_size_fuel"):
        module = importlib.import_module(f"tests.t2.{module_name}")
        for i, (source, proc, args, budget) in enumerate(module.CALLS):
            program = checked(source).program
            assert program is not None
            yield f"t2/{module_name}.CALLS[{i}]", program, proc, args, budget


def all_t2_calls() -> Iterator[tuple[str, CheckedProgram, str, tuple, int]]:
    """Calls to run every corpus program with: the calls the fuel tests make (at their
    budget and at 10), and 5 generated argument tuples (a fixed seed) for every
    procedure whose parameters are Int, Float, Bool, Str, list[Int] or list[Str] (at
    budgets 100_000 and 10). Each is (name, program, procedure, arguments, budget)."""
    for name, program, proc, args, budget in _test_calls():
        yield name, program, proc, args, budget
        yield f"{name}@10", program, proc, args, 10
    rng = random.Random(0)
    sources = (*_COLLECTORS, _golden_programs, _extra_programs)
    for collect in sources:
        for name, program in collect():
            for j, (proc, args) in enumerate(_generated(program, rng)):
                yield f"{name}:{proc}#{j}", program, proc, args, 100_000
                yield f"{name}:{proc}#{j}@10", program, proc, args, 10
