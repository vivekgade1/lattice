from textwrap import dedent

import pytest

from bench.api_datasets import api_profile
from lattice.check import check_source
from lattice.runtime import LatticeInternalError
from lattice.targets import INT_MAX, INT_MIN, TARGETS, supports_call, target_named
from tests.check.t2_support import PROFILE as T2_PROFILE

PLAIN = dedent('''\
    # lattice: v1
    @endpoint("GET", "/products", auth=Public)
    def products(limit: Int(ge=1, le=100) = 20) -> Page:
        rows = fetch(Products)
        return page(rows, limit=limit)
''')


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
    src = dedent('''\
        # lattice: v1
        import json

        @procedure
        def enc(x: Int) -> Str:
            return json.dumps(x)
    ''')
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert "L892" in codes(go)
