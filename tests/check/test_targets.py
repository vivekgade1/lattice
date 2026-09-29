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
        ("f(d: Date) -> Int", "return d.year"),
        ("f(a: Date, b: Date) -> Int", "return (a - b).days"),
        ("f(n: Int) -> Int", "d = days(n)\nreturn n"),
    ],
)
def test_l892_values_of_types_go_lacks(sig, body):
    src = _t2(body, sig)
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert "L892" in codes(go) and set(codes(go)) == {"L892"}


def _re(body: str, sig: str = "f(s: Str, n: Int) -> Str") -> str:
    return "# lattice: v1\nimport re\nimport string\n" + _t2(body, sig).removeprefix(
        "# lattice: v1\n"
    )


@pytest.mark.parametrize(
    "body",
    [
        # the replacement is compiled when the Go code is generated
        'return re.sub(r"a", s, s)',
        # Python returns tuples, which the checker types as Str
        'return ", ".join(re.findall(r"(a)(b)", s))',
        # Python inserts the groups (None when unmatched)
        'return ", ".join(re.split(r"(a)|b", s))',
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
