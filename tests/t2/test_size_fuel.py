"""Size-aware fuel: an operation whose result grows with an operand is charged its
result's size *before* it runs (R620 when the budget cannot pay), and no value may be
larger than the fuel's `max_size` (ApiProfile.max_value_size; R622) -- so `s.ljust(n)`,
`"-" * n`, `f"{s:>{n}}"`, `[0] * n`, `2 ** n`, `1 << n`, `s + s`, `json.dumps(indent=)`
and regular expressions cannot allocate or compute without bound on a few units of fuel.

The inputs here are large enough to exceed a small budget or cap, and small enough to be
harmless if they were computed (the unbounded repros are at the end: they only run once
the charging is in place)."""

from __future__ import annotations

import time

import pytest

from lattice.t2 import T2Fuel, T2FuelExhausted
from lattice.t2.support import T2_SIZE_UNIT, T2Error
from tests.t2.util import checked, load


def call(src: str, proc: str, *args: object, budget: int = 1_000_000, max_size: int = 10**7):
    ns = load(src)
    return ns[proc](T2Fuel(budget, max_size=max_size), *args)


def code_of(src: str, proc: str, *args: object, **kw: int) -> str:
    with pytest.raises(T2Error) as info:
        call(src, proc, *args, **kw)
    return info.value.code


def spent(src: str, proc: str, *args: object) -> int:
    ns = load(src)
    fuel = T2Fuel(10**9)
    ns[proc](fuel, *args)
    return fuel.budget - fuel.left


GROWERS = {
    "ljust": ("(s: Str, n: Int) -> Str", "return s.ljust(n)", ("ab",)),
    "rjust": ("(s: Str, n: Int) -> Str", 'return s.rjust(n, "*")', ("ab",)),
    "center": ("(s: Str, n: Int) -> Str", "return s.center(n)", ("ab",)),
    "zfill": ("(s: Str, n: Int) -> Str", "return s.zfill(n)", ("7",)),
    "str_mul": ("(s: Str, n: Int) -> Str", 'return "-" * n', ("",)),
    "str_rmul": ("(s: Str, n: Int) -> Str", "return n * s", ("x",)),
    "list_mul": ("(s: Str, n: Int) -> Int", "return len([0] * n)", ("",)),
    "fmt_width": ("(s: Str, n: Int) -> Str", 'return f"{s:>{n}}"', ("ab",)),
    "fmt_precision": ("(s: Str, n: Int) -> Str", 'return f"{1.5:.{n}f}"', ("",)),
    "aug_mul": ("(s: Str, n: Int) -> Str", "t = s\n    t *= n\n    return t", ("ab",)),
    "replace": ("(s: Str, n: Int) -> Str", 'return s.replace("a", "b" * 1000)', None),
}


def _src(name: str) -> str:
    sig, body, _ = GROWERS[name]
    return f"@procedure\ndef f{sig}:\n    {body}\n"


@pytest.mark.parametrize("name", [n for n in GROWERS if GROWERS[n][2] is not None])
def test_a_growing_operation_is_charged_its_size_up_front(name):
    """n = 200_000 under a budget of 1_000: charged before it runs -> R620."""
    s = GROWERS[name][2][0]
    assert code_of(_src(name), "f", s, 200_000, budget=1_000) == "R620"


@pytest.mark.parametrize("name", [n for n in GROWERS if GROWERS[n][2] is not None])
def test_a_value_larger_than_the_cap_is_r622(name):
    s = GROWERS[name][2][0]
    assert code_of(_src(name), "f", s, 200_000, max_size=10_000) == "R622"


@pytest.mark.parametrize("name", [n for n in GROWERS if GROWERS[n][2] is not None])
def test_small_values_keep_python_semantics(name):
    sig, body, args = GROWERS[name]
    ns: dict = {}
    exec(
        f"def f{sig.split('->')[0].replace('Str', 'str').replace('Int', 'int')}:\n    {body}\n", ns
    )
    want = ns["f"](args[0], 7)
    assert call(_src(name), "f", args[0], 7) == want


def test_replace_is_charged_by_the_grown_size():
    src = _src("replace")
    assert call(src, "f", "xax", 0) == "x" + "b" * 1000 + "x"
    assert code_of(src, "f", "a" * 200, 0, max_size=100_000) == "R622"


SRC_CONCATENATION_DOUBLING_IS_BOUNDED = """
    @procedure
    def f(s: Str, k: Int) -> Int:
        for _ in range(k):
            s = s + s
        return len(s)
"""


SRC_CONCATENATION_DOUBLING_IS_BOUNDED_LISTS = """
    @procedure
    def g(k: Int) -> Int:
        xs = [1, 2]
        for _ in range(k):
            xs += xs
        return len(xs)
"""


def test_concatenation_doubling_is_bounded():
    src = SRC_CONCATENATION_DOUBLING_IS_BOUNDED
    assert call(src, "f", "ab", 3) == 16
    assert code_of(src, "f", "ab", 40, max_size=10**6) == "R622"
    lists = SRC_CONCATENATION_DOUBLING_IS_BOUNDED_LISTS
    assert call(lists, "g", 3) == 16
    assert code_of(lists, "g", 40, max_size=10**6) == "R622"


SRC_BIG_INTS_ARE_BOUNDED_SQ = """
    @procedure
    def f(b: Int, n: Int) -> Int:
        for _ in range(n):
            b = b * b
        return b
"""


SRC_BIG_INTS_ARE_BOUNDED_PW = "@procedure\ndef f(b: Int, n: Int) -> Float:\n    return b ** n\n"
SRC_BIG_INTS_ARE_BOUNDED_SH = "@procedure\ndef f(b: Int, n: Int) -> Int:\n    return b << n\n"


def test_big_ints_are_bounded():
    pw = SRC_BIG_INTS_ARE_BOUNDED_PW
    sh = SRC_BIG_INTS_ARE_BOUNDED_SH
    sq = SRC_BIG_INTS_ARE_BOUNDED_SQ
    assert call(pw, "f", 2, 10) == 1024 and call(pw, "f", 1, 10**12) == 1
    assert call(sh, "f", 3, 4) == 48
    assert call(sq, "f", 3, 2) == 81
    for src in (pw, sh):
        assert code_of(src, "f", 2, 10**6) == "R622"  # a million-bit Int
    assert code_of(sq, "f", 3, 40) == "R622"


SRC_JSON_DUMPS_INDENT_IS_BOUNDED = """
    import json

    @procedure
    def f(xs: list[Int], n: Int) -> Str:
        return json.dumps(xs, indent=n)
"""


def test_json_dumps_indent_is_bounded():
    src = SRC_JSON_DUMPS_INDENT_IS_BOUNDED
    assert call(src, "f", [1, 2], 2) == "[\n  1,\n  2\n]"
    assert code_of(src, "f", list(range(50)), 10_000, max_size=100_000) == "R622"


SRC_A_REGEX_IS_CHARGED_ITS_INPUT_LENGTH = """
    import re

    @procedure
    def f(s: Str) -> Bool:
        return re.search(r"z", s) is not None
"""


def test_a_regex_is_charged_its_input_length():
    src = SRC_A_REGEX_IS_CHARGED_ITS_INPUT_LENGTH
    small = spent(src, "f", "a" * 10)
    big = spent(src, "f", "a" * 100_000)
    assert big - small >= (100_000 - 10) // T2_SIZE_UNIT
    assert code_of(src, "f", "a" * 200_000, budget=1_000) == "R620"


SRC_SMALL_OPERATIONS_COST_NOTHING_EXTRA = """
    @procedure
    def f(n: Int) -> Int:
        s = 0
        for i in range(n):
            s += i
        return s
"""


def test_small_operations_cost_nothing_extra():
    """Charges are by size units: a loop of small additions costs what it did."""
    src = SRC_SMALL_OPERATIONS_COST_NOTHING_EXTRA
    assert spent(src, "f", 10) == 12


def test_the_default_cap_is_the_profiles():
    from lattice.catalog import DEFAULT_MAX_VALUE_SIZE
    from lattice.profile import ApiProfile
    from lattice.t2.support import T2_DEFAULT_MAX_SIZE

    assert T2_DEFAULT_MAX_SIZE == DEFAULT_MAX_VALUE_SIZE
    assert T2Fuel(10).max_size == DEFAULT_MAX_VALUE_SIZE
    api = ApiProfile(tables={}, table_names={}, roles=frozenset())
    assert api.max_value_size == DEFAULT_MAX_VALUE_SIZE
    with pytest.raises(ValueError, match="max_value_size"):
        ApiProfile(tables={}, table_names={}, roles=frozenset(), max_value_size=0)


# ---- the review's repros: unbounded inputs, answered at once ----------------------------------


@pytest.mark.parametrize("name", [n for n in GROWERS if GROWERS[n][2] is not None])
def test_the_repro_inputs_fail_fast(name):
    s = GROWERS[name][2][0]
    started = time.perf_counter()
    assert code_of(_src(name), "f", s, 10**11) in ("R620", "R622")
    assert time.perf_counter() - started < 1.0


SRC_HUGE_POWER = "@procedure\ndef f(n: Int) -> Float:\n    return 2 ** n\n"
SRC_HUGE_SHIFT = "@procedure\ndef f(n: Int) -> Int:\n    return 1 << n\n"


def test_huge_powers_and_shifts_fail_fast():
    started = time.perf_counter()
    assert code_of(SRC_HUGE_POWER, "f", 10**9) == "R622"
    assert code_of(SRC_HUGE_SHIFT, "f", 10**9) == "R622"
    assert time.perf_counter() - started < 1.0


# ---- static: catastrophic regular expressions --------------------------------------------------


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(a*)*b", r"(a|a)*c", r"(\w+\s?)*$", r"(x+x+)+y"])
def test_nested_unbounded_quantifiers_are_refused(pattern):
    from tests.check.t2_support import errors

    src = f"""
import re

@procedure
def f(s: Str) -> Bool:
    return re.fullmatch(r"{pattern}", s) is not None
"""
    assert "L925" in errors(src)


@pytest.mark.parametrize(
    "pattern", [r"[a-z]+", r"(ab)+", r"(a|b)*c", r"\d{3}-\d{4}", r"(\w+)@(\w+)"]
)
def test_ordinary_patterns_are_accepted(pattern):
    checked(
        f"""
import re

@procedure
def f(s: Str) -> Bool:
    return re.fullmatch(r"{pattern}", s) is not None
"""
    )


def test_a_parameter_pattern_is_checked_too():
    from tests.check.t2_support import diags

    src = """
@endpoint("GET", "/x", auth=Customer)
def x(user: CurrentUser, q: Str(max_len=50, pattern="(a+)+$")) -> One:
    out = select(user, cols=["id"])
    return out
"""
    found = [d for d in diags(src) if "backtrack catastrophically" in d.message]
    assert found, [d.render() for d in diags(src)]


def test_fuel_exhaustion_from_a_size_charge_is_r620():
    with pytest.raises(T2FuelExhausted):
        call(_src("str_mul"), "f", "", 10**6, budget=10)


SRC_AUGMENTED_ASSIGNMENTS_KEEP_PYTHON_SEMANTICS = """
    @procedure
    def f(xs: list[Int], k: Int) -> list[Int]:
        ys = list(xs)
        zs = ys
        ys += [k]
        ys[0:1] += [k, k]
        d: dict[Str, Str] = {"a": "x"}
        d["a"] *= k
        ys[1 + 0] **= 2
        ys[0] <<= 1
        return zs + [len(d["a"])]
"""


def test_augmented_assignments_keep_python_semantics():
    src = SRC_AUGMENTED_ASSIGNMENTS_KEEP_PYTHON_SEMANTICS
    assert call(src, "f", [5, 6], 3) == [10, 9, 3, 6, 3, 3]  # as Python computes it


# Every procedure source this module loads (tests/t2ir/corpus.py lowers each one).
SOURCES: tuple[str, ...] = (
    *(_src(name) for name in GROWERS),
    SRC_CONCATENATION_DOUBLING_IS_BOUNDED,
    SRC_CONCATENATION_DOUBLING_IS_BOUNDED_LISTS,
    SRC_BIG_INTS_ARE_BOUNDED_PW,
    SRC_BIG_INTS_ARE_BOUNDED_SH,
    SRC_BIG_INTS_ARE_BOUNDED_SQ,
    SRC_JSON_DUMPS_INDENT_IS_BOUNDED,
    SRC_A_REGEX_IS_CHARGED_ITS_INPUT_LENGTH,
    SRC_SMALL_OPERATIONS_COST_NOTHING_EXTRA,
    SRC_HUGE_POWER,
    SRC_HUGE_SHIFT,
    SRC_AUGMENTED_ASSIGNMENTS_KEEP_PYTHON_SEMANTICS,
)
