"""Round 5, E3: the GRPCStatus() error type, parameter-code wrappers and WithDetails (E3a-c), and
the interceptor's code on the rpcs it gates (E3d, cases icode*). ``fixtures/status5/truth.json``
was hashed before any round-5 extractor code."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

pytestmark = needs_go

ROOT = oracle.FIXTURES / "status5"
TRUTH_SHA = "b47d3da1f176bf1061ccc4759e9f7cefded341caa73f29abb8b8c93459b26424"
CASES = ["main", "icodelit", "icodeunclear"]


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(ROOT / "truth.json")["cases"]


@pytest.fixture(scope="module")
def ex(front_end: Any) -> dict[str, dict[str, Any]]:
    return {c: front_end.extract_json(ROOT, c) for c in CASES}


def rpcs(extract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{s['name']}/{r['name']}": r for s in extract["grpc"]["services"] for r in s["rpcs"]}


def unread_whats(extract: dict[str, Any]) -> set[str]:
    return {u["what"] for u in extract["unread"]}


def test_the_truth_was_hashed_before_the_library_changed() -> None:
    assert hashlib.sha256((ROOT / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA
    frozen = (
        oracle.FIXTURES.parent.parent.parent
        / "docs/results/data/2026-10-18-grpc-round5/freeze-dev5.sha256"
    )
    assert f"{TRUTH_SHA}  tests/grpc/fixtures/status5/truth.json" in frozen.read_text()


@pytest.mark.parametrize("case", CASES)
def test_every_listed_fact_equals_the_truth(ex: dict[str, Any], truth: Any, case: str) -> None:
    got = rpcs(ex[case])
    for key, want in truth[case]["rpcs"].items():
        want = {"state": want} if isinstance(want, str) else want
        r = got[key]
        for field, value in want.items():
            have = {
                "state": r.get("state"),
                "codes": sorted(
                    c["name"] for c in r.get("codes", []) if c["kind"] != "unimplemented_stub"
                ),
                "unread": any(u["what"] == "grpc_code_not_literal" for u in r.get("unread", [])),
                "auth": r.get("auth"),
                "tables": sorted(r.get("tables", [])),
                "writes": sorted(f"{w['table']}:{w['op']}" for w in r.get("writes", [])),
                "custom_options": r.get("custom_options", {}),
            }[field]
            assert have == (sorted(value) if field in ("codes", "writes") else value), (key, field)
    assert set(got) == set(truth[case]["rpcs"])


def kinds(r: dict[str, Any]) -> list[tuple[str, str]]:
    return [(c["name"], c["kind"]) for c in r.get("codes", [])]


def test_e3a_the_code_is_the_literal_of_grpcstatus(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    for m in ("Get", "Put", "Del"):
        assert kinds(got[f"status5.v1.Lit/{m}"]) == [("NOT_FOUND", "status_error")]


def test_e3a_a_field_code_is_read_at_each_construction_site_not_unioned(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    assert [c["name"] for c in got["status5.v1.Fld/Get"]["codes"]] == ["INVALID_ARGUMENT"]
    assert [c["name"] for c in got["status5.v1.Fld/Put"]["codes"]] == ["ALREADY_EXISTS"]
    assert [c["name"] for c in got["status5.v1.Fld/Del"]["codes"]] == [
        "NOT_FOUND"
    ]  # a constructor argument
    assert [c["name"] for c in got["status5.v1.Fld/Var"]["codes"]] == [
        "UNAVAILABLE"
    ]  # a package error value


def test_e3a_a_field_code_that_is_not_a_literal_is_unread_not_unknown(ex: dict[str, Any]) -> None:
    r = rpcs(ex["main"])["status5.v1.Fld/Dyn"]
    assert r.get("codes", []) == [] and [u["what"] for u in r["unread"]] == [
        "grpc_code_not_literal"
    ]


def test_e3a_percent_w_keeps_the_wrapped_status(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    assert [c["name"] for c in got["status5.v1.Wrp/Get"]["codes"]] == ["ABORTED"]
    assert [c["name"] for c in got["status5.v1.Wrp/Put"]["codes"]] == ["NOT_FOUND"]


def test_e3b_a_parameter_code_is_taken_from_each_call_site(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    want = {
        "Get": "PERMISSION_DENIED",
        "Put": "OUT_OF_RANGE",
        "Del": "FAILED_PRECONDITION",
        "Wrap": "INTERNAL",
    }
    for m, name in want.items():
        assert [c["name"] for c in got[f"status5.v1.Par/{m}"]["codes"]] == [name]


def test_e3b_a_parameter_code_nothing_constant_passes_is_unread(ex: dict[str, Any]) -> None:
    r = rpcs(ex["main"])["status5.v1.Par/Mystery"]
    assert r.get("codes", []) == [] and [u["what"] for u in r["unread"]] == [
        "grpc_code_not_literal"
    ]


def test_e3c_withdetails_reads_the_code_of_the_status_it_wraps(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    assert [c["name"] for c in got["status5.v1.Det/Get"]["codes"]] == [
        "NOT_FOUND"
    ]  # if withInfo, err := ..; st = withInfo
    assert [c["name"] for c in got["status5.v1.Det/Put"]["codes"]] == [
        "ABORTED"
    ]  # s, _ := status.New(..).WithDetails(..)
    assert [c["name"] for c in got["status5.v1.Det/Del"]["codes"]] == [
        "PERMISSION_DENIED"
    ]  # through a helper


def test_an_untraced_error_is_unread_and_never_an_unknown_placeholder(ex: dict[str, Any]) -> None:
    r = rpcs(ex["main"])["status5.v1.Unt/Get"]
    assert r.get("codes", []) == []
    assert "grpc_error_not_traced" in {u["what"] for u in r["unread"]}


def test_icode_the_gate_code_goes_to_the_gated_rpcs_with_the_gates_site(ex: dict[str, Any]) -> None:
    got = rpcs(ex["icodelit"])
    shut = [c for c in got["status5.v1.Shut/Get"]["codes"] if c["kind"] == "interceptor"]
    assert [c["name"] for c in shut] == ["UNAUTHENTICATED"]
    gate = [i for i in ex["icodelit"]["grpc"]["interceptors"] if i["kind"] == "auth_literal"][0]
    assert (shut[0]["file"], shut[0]["line"]) == (gate["file"], gate["line"])
    assert not any(
        c["kind"] == "interceptor" for c in got["status5.v1.Open/Hello"].get("codes", [])
    )  # admitted


def test_icode_a_generated_stub_behind_the_gate_is_gated_too(ex: dict[str, Any]) -> None:
    r = rpcs(ex["icodelit"])["status5.v1.Shut/Del"]
    assert r["state"] == "unimplemented" and "UNAUTHENTICATED" in {c["name"] for c in r["codes"]}


def test_icode_an_unclear_gate_adds_no_code(ex: dict[str, Any]) -> None:
    got = rpcs(ex["icodeunclear"])
    assert not any(c["kind"] == "interceptor" for r in got.values() for c in r.get("codes", []))
    assert {r["auth"] for r in got.values()} == {"unclear"}
