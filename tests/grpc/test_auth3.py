"""Round 3, D2: an interceptor the extractor cannot read is never taken for no interceptor.

``tests/grpc/fixtures/authwire/<case>`` are small servers whose authentication is written nine
ways (benign logger, recover-to-INTERNAL, a gate whose refusal is not traced, an N-shaped literal
allowlist, no interceptor, a kilnworks-shaped gate, options assembled in another function, a
handler-level check, a literal gate beside an unread one); ``truth.json`` (hashed before the
extractor changed) says what each rpc's auth is and how each interceptor classifies. ``Public``
needs a literal allowlist naming the method, or nothing that may refuse the caller."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

AUTHWIRE = oracle.FIXTURES / "authwire"
TRUTH_SHA = "35a3139d798141618feef56fe05498c6113e71a4a9c191d8ba5849c3a71b0695"
CASES = [
    "logger",
    "recover",
    "gate_unknown",
    "literal_logger",
    "none",
    "gate_traced",
    "opts_split",
    "handler_check",
    "mixed",
]

pytestmark = needs_go


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(AUTHWIRE / "truth.json")


@pytest.fixture(scope="module")
def extracts(front_end: Any) -> dict[str, dict[str, Any]]:
    return {c: front_end.extract_json(AUTHWIRE, c) for c in CASES}


def auth_of(extract: dict[str, Any]) -> dict[str, str]:
    return {r["name"]: r["auth"] for s in extract["grpc"]["services"] for r in s["rpcs"]}


def test_the_truth_was_hashed_before_the_extractor_changed() -> None:
    assert hashlib.sha256((AUTHWIRE / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


def test_the_truth_names_every_case(truth: Any) -> None:
    assert sorted(truth["cases"]) == sorted(CASES)


@pytest.mark.parametrize("case", CASES)
def test_the_auth_of_each_rpc_equals_the_truth(
    extracts: dict[str, dict[str, Any]], truth: Any, case: str
) -> None:
    assert auth_of(extracts[case]) == truth["cases"][case]["auth"]


@pytest.mark.parametrize("case", CASES)
def test_the_interceptor_kinds_equal_the_truth(
    extracts: dict[str, dict[str, Any]], truth: Any, case: str
) -> None:
    kinds = [i["kind"] for i in extracts[case]["grpc"]["interceptors"]]
    assert kinds == truth["cases"][case]["kinds"]


def test_no_rpc_behind_an_unread_interceptor_is_public(extracts: dict[str, dict[str, Any]]) -> None:
    for case, ex in extracts.items():
        kinds = {i["kind"] for i in ex["grpc"]["interceptors"]}
        if kinds & {"auth_unknown", "auth_not_literal"}:
            public = [k for k, v in auth_of(ex).items() if v == "Public"]
            literal = "auth_literal" in kinds
            # only a literal allowlist naming the method may still say Public -- and then not
            # beside an interceptor that may refuse (the mixed case answers unclear for Ping)
            assert not public, (case, public, literal)


def test_d2_1_the_unknown_reason_names_the_first_untraced_way_out(
    extracts: dict[str, dict[str, Any]],
) -> None:
    (gate,) = extracts["gate_unknown"]["grpc"]["interceptors"]
    assert gate["kind"] == "auth_unknown"
    assert gate["reason"].startswith("auth.go:") and "g.verify(ctx)" in gate["reason"]
    assert gate["func"] == "main.gate.unary"


def test_d2_1_an_unclear_rpc_carries_the_unread_item(extracts: dict[str, dict[str, Any]]) -> None:
    rpcs = {r["name"]: r for s in extracts["gate_unknown"]["grpc"]["services"] for r in s["rpcs"]}
    for r in rpcs.values():
        assert r["auth"] == "unclear"
        assert [u["what"] for u in r["unread"]] == ["interceptor_not_literal"]


def test_d2_1_a_logger_and_a_recover_are_benign(extracts: dict[str, dict[str, Any]]) -> None:
    for case in ("logger", "recover"):
        (ic,) = extracts[case]["grpc"]["interceptors"]
        assert ic["kind"] == "other" and "reason" not in ic
        assert set(auth_of(extracts[case]).values()) == {"Public"}


def test_d2_2_public_needs_an_allowlist_or_no_interceptor_that_may_refuse(
    extracts: dict[str, dict[str, Any]],
) -> None:
    ex = extracts["literal_logger"]
    (auth,) = [i for i in ex["grpc"]["interceptors"] if i["kind"] == "auth_literal"]
    assert auth["public"] == ["/aw.v1.Desk/Ping"]
    assert auth_of(ex) == {"Ping": "Public", "Read": "Member"}
    assert set(auth_of(extracts["none"]).values()) == {"Public"}


def test_d2_2_a_literal_gate_beside_an_unread_one_decides_only_what_it_refuses(
    extracts: dict[str, dict[str, Any]],
) -> None:
    assert auth_of(extracts["mixed"]) == {"Ping": "unclear", "Read": "Member"}


def test_d2_3_a_sentinel_returned_through_a_value_receiver_method_is_traced(
    extracts: dict[str, dict[str, Any]],
) -> None:
    kinds = {i["func"]: i["kind"] for i in extracts["gate_traced"]["grpc"]["interceptors"]}
    assert kinds == {"main.steady": "other", "main.gate.unary": "auth_literal"}
    assert auth_of(extracts["gate_traced"]) == {"Ping": "Public", "Read": "Member"}


def test_server_options_built_in_another_function_are_found(
    extracts: dict[str, dict[str, Any]],
) -> None:
    (ic,) = extracts["opts_split"]["grpc"]["interceptors"]
    assert ic["func"] == "main.authUnary" and ic["kind"] == "auth_literal"


def test_a_handler_level_refusal_still_makes_the_rpc_member(
    extracts: dict[str, dict[str, Any]],
) -> None:
    assert auth_of(extracts["handler_check"]) == {"Ping": "Public", "Read": "Member"}
