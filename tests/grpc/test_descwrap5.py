"""Round 5, E1: a decorated copy of a generated ServiceDesc (value copy, literal ServiceName,
rebuilt Methods, wrapped handlers). ``fixtures/descwrap5/truth.json`` was hashed before any
round-5 extractor code; it says the state and codes of every rpc."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

pytestmark = needs_go

ROOT = oracle.FIXTURES / "descwrap5"
TRUTH_SHA = "daabff1b81d78a84c77fab4780f122af4c0f9cb0b2a532bf53f15a53c4bee8ba"
CASES = ["main", "namedyn", "methods", "wrapper"]


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
    assert f"{TRUTH_SHA}  tests/grpc/fixtures/descwrap5/truth.json" in frozen.read_text()


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


def test_e1_1_the_copy_registers_under_its_assigned_name_and_the_template_does_not(
    ex: dict[str, Any],
) -> None:
    regs = {r["service"] for r in ex["main"]["grpc"]["registrations"]}
    assert regs == {"descwrap5.v1.Ptr", "descwrap5.v1.Copy", "descwrap5.v1.Alias"}


def test_e1_2_a_name_read_at_run_time_is_never_the_originals(ex: dict[str, Any]) -> None:
    got = rpcs(ex["namedyn"])
    assert (
        got["descwrap5.v1.Dyn/Get"]["state"] == "unread"
    )  # not "Dyn", the descriptor's original name
    assert {r["service"] for r in ex["namedyn"]["grpc"]["registrations"]} == {"descwrap5.v1.Plain"}
    assert "descriptor_name_not_literal" in unread_whats(ex["namedyn"])


def test_e1_3_a_methods_literal_serves_only_its_methods(ex: dict[str, Any]) -> None:
    got = rpcs(ex["methods"])
    assert got["descwrap5.v1.Lit/Get"]["state"] == "implemented"
    assert got["descwrap5.v1.Lit/Put"]["state"] == "not_registered"


def test_e1_3_methods_rebuilt_by_range_keep_the_generated_set(ex: dict[str, Any]) -> None:
    got = rpcs(ex["methods"])
    assert {
        got[f"descwrap5.v1.{s}/{m}"]["state"] for s in ("Rng", "Apd") for m in ("Get", "Put")
    } == {"implemented"}


def test_e1_3_methods_built_otherwise_are_unread_with_a_reason(ex: dict[str, Any]) -> None:
    got = rpcs(ex["methods"])
    for m in ("Get", "Put"):
        assert [u["what"] for u in got[f"descwrap5.v1.Opaque/{m}"]["unread"]] == [
            "descriptor_methods_not_literal"
        ]


def test_e1_4_the_wrapper_translates_its_service_only(ex: dict[str, Any]) -> None:
    got = rpcs(ex["wrapper"])
    eq = got["descwrap5.v1.Eq/Get"]["codes"]
    assert [c["name"] for c in eq] == ["NOT_FOUND"] and eq[0]["kind"] == "status_error"
    raw = got["descwrap5.v1.Raw/Get"]["codes"]
    assert [(c["name"], c["kind"]) for c in raw] == [
        ("UNKNOWN", "plain_error")
    ]  # the same sentinel, untranslated


def test_a_package_error_file_without_functions_is_still_read(ex: dict[str, Any]) -> None:
    """An errors.go of only `var Err.. = errors.New(..)` is read through a synthetic host."""
    assert "grpc_error_not_traced" not in unread_whats(ex["wrapper"])


def test_no_confident_wrong_state_against_the_truth(ex: dict[str, Any], truth: Any) -> None:
    for case in CASES:
        got = rpcs(ex[case])
        for key, want in truth[case]["rpcs"].items():
            state = want if isinstance(want, str) else want["state"]
            assert not (
                got[key]["state"] == "implemented" and state in ("not_registered", "unimplemented")
            )
            assert not (
                got[key]["state"] in ("not_registered", "unimplemented") and state == "implemented"
            )
