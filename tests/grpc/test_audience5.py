"""Round 5, E4: the auth audience from a proto method option (E4b, E4c) and the safety rule for any
other skip condition (E4a). ``fixtures/audience5/truth.json`` was hashed before any round-5
extractor code."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

pytestmark = needs_go

ROOT = oracle.FIXTURES / "audience5"
TRUTH_SHA = "63106d120255c2055776985404c88064d4de89e4e43aea6c01790145c5f7f18b"
CASES = ["opt", "optne", "skipregistry", "skipflow", "noskip"]


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
    assert f"{TRUTH_SHA}  tests/grpc/fixtures/audience5/truth.json" in frozen.read_text()


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


def gate(extract: dict[str, Any]) -> dict[str, Any]:
    return [
        i
        for i in extract["grpc"]["interceptors"]
        if i["func"].endswith("Unary.func1") or i["kind"] != "other"
    ][0]


def test_e4b_the_option_gate_is_recorded_with_the_extension_and_the_enum_value(
    ex: dict[str, Any],
) -> None:
    opt = gate(ex["opt"])
    assert opt["kind"] == "auth_literal"
    assert opt["option"] == {
        "extension": "audience5.options.v1.audience",
        "op": "==",
        "value": "AUDIENCE_ANONYMOUS",
        "zero": "AUDIENCE_UNSPECIFIED",
        "guard_prefix": "audience5.",
        "guard_negated": True,
    }


def test_e4b_a_not_equal_gate_inverts_and_the_zero_value_decides_the_absent_option(
    ex: dict[str, Any],
) -> None:
    got = rpcs(ex["optne"])
    assert got["audience5.v1.Hall/Lobby"]["auth"] == "Public"  # no option: zero != MEMBER
    assert got["audience5.v1.Hall/Members"]["auth"] == "Member"


def test_e4b_an_absent_option_is_the_zero_value_never_the_public_one(ex: dict[str, Any]) -> None:
    assert rpcs(ex["opt"])["audience5.v1.Door/Info"]["auth"] == "Member"


def test_e4b_an_option_the_reader_could_not_record_is_unclear_for_that_rpc_only(
    ex: dict[str, Any],
) -> None:
    got = rpcs(ex["opt"])
    assert got["audience5.v1.Door/Odd"]["auth"] == "unclear"
    assert got["audience5.v1.Door/Login"]["auth"] == "Public"


def test_e4c_only_the_rpcs_own_scalar_option_is_recorded(ex: dict[str, Any]) -> None:
    got = rpcs(ex["opt"])
    assert got["audience5.v1.Door/Login"]["custom_options"] == {
        "(audience5.options.v1.audience)": "AUDIENCE_ANONYMOUS"
    }
    assert "custom_options" not in got["audience5.v1.Door/Info"]
    assert "custom_options" not in got["audience5.v1.Door/Odd"]  # an aggregate stays an unread item
    assert "proto_custom_option" in unread_whats(ex["opt"])


def test_e4a_a_method_handed_to_a_helper_that_decides_is_never_member(ex: dict[str, Any]) -> None:
    ic = gate(ex["skipflow"])
    assert ic["kind"] == "auth_not_literal" and "flows into" in ic["reason"]
    assert {r["auth"] for r in rpcs(ex["skipflow"]).values()} == {"unclear"}


def test_e4a_a_registry_lookup_on_the_method_is_not_a_literal_set(ex: dict[str, Any]) -> None:
    assert {r["auth"] for r in rpcs(ex["skipregistry"]).values()} == {"unclear"}


def test_e4a_an_empty_allowlist_is_literal_only_without_any_skip_condition(
    ex: dict[str, Any],
) -> None:
    assert gate(ex["noskip"])["kind"] == "auth_literal" and not gate(ex["noskip"]).get("public")
    assert {r["auth"] for r in rpcs(ex["noskip"]).values()} == {"Member"}


def test_e4a_no_member_stands_behind_an_unenumerated_bypass(ex: dict[str, Any]) -> None:
    for case in ("skipflow", "skipregistry"):
        assert "Member" not in {r["auth"] for r in rpcs(ex[case]).values()}
