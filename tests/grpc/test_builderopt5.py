"""Round 5, E2: services registered by a builder that ranges over a field its option functions
fill. ``fixtures/builderopt5/truth.json`` was hashed before any round-5 extractor code."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

pytestmark = needs_go

ROOT = oracle.FIXTURES / "builderopt5"
TRUTH_SHA = "f7eb1e743f217f06f5a7076024eb35eacb64adcd9760dfe5057affd0188eaf65"
CASES = ["main", "unres"]


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
    assert f"{TRUTH_SHA}  tests/grpc/fixtures/builderopt5/truth.json" in frozen.read_text()


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


def test_e2_each_option_call_site_is_one_registration(ex: dict[str, Any]) -> None:
    regs = {r["service"]: r for r in ex["main"]["grpc"]["registrations"]}
    assert set(regs) == {"builderopt5.v1.One", "builderopt5.v1.Two"}
    assert (
        regs["builderopt5.v1.One"]["line"] != regs["builderopt5.v1.Two"]["line"]
    )  # two option calls
    assert all(r["via"].startswith("RegisterService via ") for r in regs.values())


def test_e2_an_option_nobody_ranges_over_registers_nothing(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    assert got["builderopt5.v1.Parked/Get"]["state"] == "not_registered"
    assert got["builderopt5.v1.Absent/Get"]["state"] == "not_registered"


def test_e2_the_impl_of_each_entry_is_read_from_its_own_call(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    assert [c["name"] for c in got["builderopt5.v1.One/Get"]["codes"]] == ["NOT_FOUND"]
    assert [c["name"] for c in got["builderopt5.v1.Two/Get"]["codes"]] == ["INVALID_ARGUMENT"]


def test_e2_an_unresolvable_stored_entry_keeps_the_module_wide_safety_rule(
    ex: dict[str, Any],
) -> None:
    got = rpcs(ex["unres"])
    assert got["builderopt5.v1.Fine/Get"]["state"] == "implemented"
    for s in ("Dyn", "Orphaned"):
        assert got[f"builderopt5.v1.{s}/Get"]["state"] == "unread"  # never not_registered beside it
        assert [u["what"] for u in got[f"builderopt5.v1.{s}/Get"]["unread"]] == [
            "registration_reachability_unknown"
        ]


def test_e2_no_service_is_called_not_registered_while_a_registration_is_unresolved(
    ex: dict[str, Any],
) -> None:
    assert {r["state"] for r in rpcs(ex["unres"]).values()} <= {"implemented", "unread"}
