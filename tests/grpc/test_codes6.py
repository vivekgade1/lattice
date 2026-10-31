"""GA2 (2026-10-30): status codes the round-5 extractor lost on SG and HO5, as generic fixtures
(``docs/results/2026-10-30-grpc-codes-diagnosis.md``): constructor code maps (SG-1/2), error
parameters passed through helpers and translators (SG-3, HO5-3/4), interceptor codes of
interceptors that are not literal auth gates (SG-3), descriptor tables (HO5-1).
``fixtures/codes6/truth.json`` was written and hashed before any GA2 extractor change."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

pytestmark = needs_go

ROOT = oracle.FIXTURES / "codes6"
TRUTH_SHA = "b163cc245b4cedfb4b199f8cfff36dfd41f65d33b2b7f6eef3240b8cdd2f63d3"
CASES = ["ctormap", "passthru", "icodes", "desctable"]


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(ROOT / "truth.json")["cases"]


@pytest.fixture(scope="module")
def ex(front_end: Any) -> dict[str, dict[str, Any]]:
    return {c: front_end.extract_json(ROOT, c) for c in CASES}


def rpcs(extract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{s['name']}/{r['name']}": r for s in extract["grpc"]["services"] for r in s["rpcs"]}


def test_the_truth_was_hashed_before_the_library_changed() -> None:
    assert hashlib.sha256((ROOT / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


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
                    {c["name"] for c in r.get("codes", []) if c["kind"] != "unimplemented_stub"}
                ),
                "unread": any(u["what"] == "grpc_code_not_literal" for u in r.get("unread", [])),
            }[field]
            assert have == (sorted(value) if field == "codes" else value), (key, field, have)
    assert set(got) == set(truth[case]["rpcs"])


def test_a_constructor_code_is_a_status_code_at_the_call_site(ex: dict[str, Any]) -> None:
    put = rpcs(ex["ctormap"])["codes6.v1.Map/Put"]
    assert [(c["name"], c["kind"], c["line"]) for c in put["codes"]] == [
        ("INVALID_ARGUMENT", "status_error", 20)
    ]


def test_interceptor_codes_are_labelled_with_the_interceptor(ex: dict[str, Any]) -> None:
    pay = rpcs(ex["icodes"])["codes6.v1.Till/Pay"]
    kinds = {c["name"]: (c["kind"], c["in"]) for c in pay["codes"]}
    assert kinds["INVALID_ARGUMENT"] == ("status_error", "app.tillImpl.Pay")
    assert kinds["UNAVAILABLE"] == ("interceptor", "app.tillGate")
    assert kinds["RESOURCE_EXHAUSTED"] == ("interceptor", "app.quotaGate")
    assert kinds["FAILED_PRECONDITION"] == ("interceptor", "app.versionGate")


def test_a_table_registration_is_listed(ex: dict[str, Any]) -> None:
    regs = {r["service"]: r for r in ex["desctable"]["grpc"]["registrations"]}
    assert set(regs) == {"codes6.v1.Bill", "codes6.v1.Tab"}
    assert not [u for u in ex["desctable"]["unread"] if u["what"].startswith("registration")]


# ---- HO5-2: a typed error translated by an interceptor's map (truth written later, see its note) ----

AS_ROOT = oracle.FIXTURES / "codes6as"
AS_SHA = "b4c218ef90bb7cff095f216d9d3e5b71f653c8b1458b18f0c0475087f8d47f85"


def test_the_typed_error_truth_is_the_one_written_before_the_tree_changed() -> None:
    assert hashlib.sha256((AS_ROOT / "truth.json").read_bytes()).hexdigest() == AS_SHA


def test_a_typed_error_takes_the_code_its_interceptor_maps_it_to(front_end: Any) -> None:
    got = rpcs(front_end.extract_json(AS_ROOT, "main"))
    for key, want in oracle.load(AS_ROOT / "truth.json")["cases"]["main"]["rpcs"].items():
        have = sorted({c["name"] for c in got[key].get("codes", []) if c["kind"] != "unimplemented_stub"})
        assert (got[key]["state"], have) == (want["state"], sorted(want["codes"])), key
