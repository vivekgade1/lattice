from textwrap import dedent

import pytest

from bench.api_datasets import api_profile
from lattice.check import check_source
from lattice.runtime import LatticeInternalError
from lattice.targets import INT_MAX, INT_MIN, TARGETS, supports_call, target_named
from tests.check.t2_support import PROFILE as T2_PROFILE

PLAIN = dedent("""\
    # lattice: v1
    @endpoint("GET", "/products", auth=Public)
    def products(limit: Int(ge=1, le=100) = 20) -> Page:
        rows = fetch(Products)
        return page(rows, limit=limit)
""")


def codes(result):
    return [d.code for d in result.diagnostics]


def test_int_bounds():
    assert INT_MIN == -(2**63) and INT_MAX == 2**63 - 1


def test_known_targets():
    assert set(TARGETS) == {"python", "go"}
    assert target_named("go").natives is False
    with pytest.raises(LatticeInternalError):
        target_named("cobol")


def test_supports_call_prefixes_always():
    go = target_named("go")
    assert supports_call(go, "proc:anything")
    assert supports_call(go, "record:R")
    assert supports_call(go, "helper:days")
    assert supports_call(target_named("python"), "std:json.dumps")


def test_default_target_is_python_and_plain_program_passes_on_go():
    assert check_source(PLAIN, api_profile()).ok
    assert check_source(PLAIN, api_profile(), target="go").ok


def test_l890_native_call_rejected_on_go(realapps_source, realapps_profile):
    ok = check_source(realapps_source, realapps_profile)
    assert ok.ok
    go = check_source(realapps_source, realapps_profile, target="go")
    assert not go.ok
    assert "L890" in codes(go)


def test_a_native_inside_a_procedure_is_only_l890_on_go():
    """The `native` namespace a procedure reads is the native itself (L890), not a
    second L892 for a value kind: a program with natives is rejected for Go with
    L890 only (plan 5a Task 13: the gate's exclusions are the L890-only programs)."""
    from tests.api import t2prog

    go = check_source(t2prog.PROGRAM, t2prog.PROFILE, target="go")
    assert not go.ok
    errors = {d.code for d in go.diagnostics if d.severity.name == "ERROR"}
    assert errors == {"L890"}, [d.render() for d in go.diagnostics]


def test_l892_unsupported_t2_call_on_go(monkeypatch):
    monkeypatch.setattr("lattice.targets.GO_T2_CALLS", set())
    src = dedent("""\
        # lattice: v1
        import json

        @procedure
        def enc(x: Int) -> Str:
            return json.dumps(x)
    """)
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert "L892" in codes(go)


def _t2(body: str, sig: str = "f(xs: list[Int], n: Int) -> Str") -> str:
    return f"# lattice: v1\n@procedure\ndef {sig}:\n" + "".join(
        f"    {line}\n" for line in dedent(body).strip("\n").splitlines()
    )


@pytest.mark.parametrize(
    "body",
    [
        "return str(xs)",
        'return f"{xs}"',
        'return f"{xs!r:>9}"',
        'return str({"a": n})',
        "return str({n})",
        "return str((n, n))",
        'return f"{(n, 1)}"',
    ],
)
def test_l892_str_of_a_container_on_go(body):
    src = _t2(body)
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert codes(go) == ["L892"]


@pytest.mark.parametrize(
    "body", ["return str(n)", 'return f"{n:>4}|{xs[0]!r}"', "return str(len(xs))"]
)
def test_str_of_a_scalar_passes_on_go(body):
    assert check_source(_t2(body), T2_PROFILE, target="go").ok


@pytest.mark.parametrize(
    "body",
    [
        # R-widen (2026-09-28): each was L892 on Go while Python kept an exact int in
        # its Float-typed result; the Int is now converted on every target
        "return str(2 ** n)",
        "return str(max(1.5, n))",
        "return str(round(n, 1))",
        "return str(2 ** 3)",
        "return str(2 ** -1)",
        "return str(2.0 ** n)",
        "return str(max(1.5, float(n)))",
        "return str(min(n, 2))",
        "return str(max(xs))",
        "return str(round(n / 3, 1))",
    ],
)
def test_int_in_float_forms_pass_on_go(body):
    src = _t2(body)
    assert check_source(src, T2_PROFILE).ok
    assert check_source(src, T2_PROFILE, target="go").ok


@pytest.mark.parametrize(
    ("sig", "body"),
    [
        # the endpoint layer's values: L892 on Go until plan 5a Task 12 (generated structs)
        ("f(c: Caller) -> Str", "return c.role"),
        ("f(r: Row[Items]) -> Int", "return 1"),
    ],
)
def test_row_and_caller_values_pass_on_go(sig, body):
    src = _t2(body, sig)
    assert check_source(src, T2_PROFILE).ok
    assert check_source(src, T2_PROFILE, target="go").ok


_TIME_SIG = "f(d: Date, t: DateTime, s: Str) -> Str"


@pytest.mark.parametrize(
    "body",
    [
        # a directive Go does not implement, or a format that is not a literal (Task 10)
        'return t.strftime("%c")',
        'return d.strftime("%U %Y")',
        'return d.strftime("100%")',
        "return t.strftime(s)",
        'return f"{d:%x}"',
        'return f"{t:{s}}"',
    ],
)
def test_l892_strftime_formats_go_does_not_run(body):
    src = _t2(body, _TIME_SIG)
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert codes(go) == ["L892"], go.diagnostics


@pytest.mark.parametrize(
    "body",
    [
        'return t.strftime("%Y-%m-%d %H:%M:%S.%f %y %a %A %b %B %j %p %%") + d.strftime("")',
        'return f"{d:%Y}{t:%H}{d}{t!r}" + str(t - t) + d.isoformat()',
        "return str((d + days(1.5)).weekday()) + str(t.timestamp())",
    ],
)
def test_time_values_pass_on_go(body):
    src = _t2(body, _TIME_SIG)
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert go.ok, go.diagnostics


def test_json_math_and_collections_pass_on_go():
    src = dedent("""\
        # lattice: v1
        import json
        import math
        from collections import Counter, defaultdict

        @procedure
        def f(s: Str, xs: list[Str]) -> Str:
            j = json.loads(s)
            c = Counter(xs)
            d: dict[Str, list[Int]] = defaultdict(list)
            d["a"].append(math.floor(2.5))
            return json.dumps([j, c.most_common(1), d], indent=2) + str(math.sqrt(2.0))
    """)
    assert check_source(src, T2_PROFILE, target="go").ok


def _re(body: str, sig: str = "f(s: Str, n: Int) -> Str") -> str:
    return "# lattice: v1\nimport re\nimport string\n" + _t2(body, sig).removeprefix(
        "# lattice: v1\n"
    )


@pytest.mark.parametrize(
    "body",
    [
        # the replacement is compiled when the Go code is generated
        'return re.sub(r"a", s, s)',
    ],
)
def test_l892_regular_expressions_go_does_not_run(body):
    src = _re(body)
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert codes(go) == ["L892"], go.diagnostics


@pytest.mark.parametrize(
    "body",
    [
        'return re.sub(r"(a)", r"<\\1>", s)',
        'return ", ".join(re.findall(r"(a)b", s))',
        'return ", ".join(re.findall(r"(?:a)(?:b)", s))',
        'return ", ".join(re.split(r"(?:a)|b", s))',
        # findall of several groups gives tuples, split by groups Str | None (2026-09-29)
        'return ", ".join(a + b for a, b in re.findall(r"(a)(b)", s))',
        'return ", ".join(x or "-" for x in re.split(r"(a)|b", s))',
        'm = re.search(r"(?P<x>a)", s)\nif m is None:\n    return ""\n'
        'return m.group("x") or str(m)',
        'return string.digits + string.ascii_letters + s.upper().center(n, "*")',
        'return "".join(s.split(",", 2)) + str(s.rfind("a")) + s.zfill(n)',
    ],
)
def test_strings_and_regular_expressions_pass_on_go(body):
    src = _re(body)
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert go.ok, go.diagnostics
