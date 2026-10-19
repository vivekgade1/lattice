"""Round 5, E5: tables and writes from database/sql statements, read lexically in the gRPC walk
only. ``fixtures/sqlraw5/truth.json`` was hashed before any round-5 extractor code."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

pytestmark = needs_go

ROOT = oracle.FIXTURES / "sqlraw5"
TRUTH_SHA = "b06e045c685f69bdfe4606d0a9302f0393a2cee53c908f4d2ce781b2ce358d99"
CASES = ["main"]


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
    assert f"{TRUTH_SHA}  tests/grpc/fixtures/sqlraw5/truth.json" in frozen.read_text()


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


def test_e5_a_statement_that_is_not_a_literal_is_unread_and_names_no_table(
    ex: dict[str, Any],
) -> None:
    r = rpcs(ex["main"])["sqlraw5.v1.Cat/Dyn"]
    assert r.get("tables", []) == []
    assert "sql_not_literal" in [u["what"] for u in r["unread"]]


def test_e5_a_store_reached_through_an_interface_reads_the_built_implementer_only(
    ex: dict[str, Any],
) -> None:
    r = rpcs(ex["main"])["sqlraw5.v1.Ifc/Find"]
    assert r["tables"] == ["parts"]  # SQLParts is built; MemParts (table ghost) never is


def test_e5_the_writes_have_the_shape_the_flow_rules_consume(ex: dict[str, Any]) -> None:
    w = rpcs(ex["main"])["sqlraw5.v1.Txn/Move"]["writes"]
    assert {k for x in w for k in x} == {"table", "op", "file", "line"}
    assert [(x["table"], x["op"]) for x in w] == [("accounts", "update"), ("ledger", "create")]


def test_e5_cte_names_and_ddl_are_no_tables(ex: dict[str, Any]) -> None:
    assert "recent" not in rpcs(ex["main"])["sqlraw5.v1.Cte/Recent"]["tables"]


def test_e5_upserts_in_three_spellings_are_save(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    assert {got[f"sqlraw5.v1.Ups/{m}"]["writes"][0]["op"] for m in ("Pref", "Flag", "Cache")} == {
        "save"
    }


def test_e5_4_the_http_walk_never_reads_sql(front_end: Any) -> None:
    """A Gin handler over database/sql keeps the HTTP extract it had: no tables, no writes."""
    ex = front_end.extract_json(ROOT, "ginsql")
    assert "grpc" not in ex
    for route in ex["routes"]:
        assert not route.get("tables") and not route.get("state_writes")
    assert ex["tables"] == [] and not any(w.get("table") for w in ex.get("status_writes", []))
