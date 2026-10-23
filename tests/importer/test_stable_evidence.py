"""Stable evidence (``lattice.importer.stable``): the values an app mints at run time are replaced
by placeholders where the evidence is written, so the same app imported twice gives the same
``report.json`` and ``proposals.json`` (node F3; the H1 import was identical 1 of 10 times)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lattice.importer import rules
from lattice.importer.stable import stabilise, stabilise_text

H1_TOKEN = "djEuMS4xNzkyODA1NDgw.r0_wv82bTSURbzw_3KcNn5zHO7nRfEjZG6JkI0v3B0c"
TRACE = "00-b37d0ba8d443a5a58b20adbd617a051f-e5f2125e7a0e5437-00"
BCRYPT = "$2b$12$" + "a" * 53
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
GUID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


@pytest.mark.parametrize(
    "value,masked",
    [
        (H1_TOKEN, "<token>"),
        (JWT, "<token>"),
        ("Bearer " + JWT, "Bearer <token>"),
        (BCRYPT, "<hash>"),
        (TRACE, "<trace-id>"),
        ("2026-10-23T17:31:26.986077Z", "<timestamp>"),
        ("2026-10-23 17:31:53.657712", "<timestamp>"),
        ("2026-10-23T17:31:26+02:00", "<timestamp>"),
        ("a" * 16 + "z" * 16 + "C" * 8 + "9", "<token>"),
        ("0123456789abcdef0123456789abcdef", "<hex>"),
    ],
)
def test_minted_values_become_placeholders(value, masked):
    assert stabilise_text(value) == masked


@pytest.mark.parametrize(
    "value",
    [
        "ExpenseReportService.CreateExpenseReport",  # a dotted name has no digit
        "expense_report_line_amount_exceeds_the_limit",  # snake_case: no upper case
        "b" * 200,  # a boundary probe's value
        "2026-10-23",  # a date alone is not a stamp
        "Draft",
        "/reports/{reportId}",
        "R1 POST /review/reports/{reportId}/approve",
        "the answer carries a token",
        GUID,  # a Guid is masked only when the app created its row
    ],
)
def test_real_evidence_is_kept(value):
    assert stabilise_text(value) == value


def test_credential_keys_are_masked_whatever_the_value_looks_like():
    body = {"access_token": "abc", "token_type": "Bearer", "refreshToken": "x1", "token": ""}
    assert stabilise(body) == {
        "access_token": "<token>", "token_type": "Bearer", "refreshToken": "<token>", "token": "",
    }  # fmt: skip


def test_structure_status_and_numbers_are_kept_and_the_input_is_unchanged():
    probe = {
        "status": 201,
        "request": {"json": {"title": "Q3 trip", "amount": 12.5}},
        "body": {"id": 7, "createdAt": "2026-10-23T17:31:26.986077Z", "traceId": TRACE,
                 "lines": [{"updatedAt": "2026-10-23 17:31:53.657712", "n": 1}]},
        "db_diff": {"t": {"added": [{"id": 7, "created_at": "2026-10-23 17:31:26.986077"}]}},
    }  # fmt: skip
    before = json.dumps(probe, sort_keys=True)
    out = stabilise(probe)
    assert json.dumps(probe, sort_keys=True) == before
    assert out == {
        "status": 201,
        "request": {"json": {"title": "Q3 trip", "amount": 12.5}},
        "body": {"id": 7, "createdAt": "<timestamp>", "traceId": "<trace-id>",
                 "lines": [{"updatedAt": "<timestamp>", "n": 1}]},
        "db_diff": {"t": {"added": [{"id": 7, "created_at": "<timestamp>"}]}},
    }  # fmt: skip


def test_a_guid_of_a_row_the_app_created_is_masked_and_a_seeded_one_is_kept():
    seeded = GUID
    new = "9b2c1d5e-0000-4000-8000-123456789abc"
    probe = {
        "request": {"json": {"owner": seeded}},
        "body": {"id": new, "owner": seeded, "msg": f"saved {new}"},
        "db_diff": {"notes": {"added": [{"id": new, "owner_id": seeded}], "removed": []}},
    }
    assert stabilise(probe) == {
        "request": {"json": {"owner": seeded}},
        "body": {"id": "<guid>", "owner": seeded, "msg": "saved <guid>"},
        "db_diff": {"notes": {"added": [{"id": "<guid>", "owner_id": seeded}], "removed": []}},
    }
    evidence = {"rows": [{"id": new, "owner_id": seeded}], "requests": [{"json": {"id": new}}]}
    assert stabilise(evidence) == {
        "rows": [{"id": "<guid>", "owner_id": seeded}],
        "requests": [{"json": {"id": "<guid>"}}],
    }


def test_two_runs_of_the_same_answer_mask_to_the_same_bytes():
    def answer(token, trace, stamp):
        return {"probes": {"p": {"status": 200, "body": {
            "access_token": token, "traceId": trace, "createdAt": stamp}}}}  # fmt: skip

    a = answer(H1_TOKEN, TRACE, "2026-10-23T17:31:26.986077Z")
    b = answer(H1_TOKEN[::-1], TRACE.replace("b37d", "aaaa"), "2026-10-23T17:35:15.633821Z")
    assert a != b
    assert json.dumps(stabilise(a), sort_keys=True) == json.dumps(stabilise(b), sort_keys=True)


def _import(framework):
    return SimpleNamespace(
        framework=framework,
        rules_dict=lambda: {"probes": {"p": {"body": {"traceId": TRACE}}}},
        mining=SimpleNamespace(proposals=[]),
    )


def test_a_dotnet_import_is_written_with_stable_evidence_and_the_others_as_before():
    assert rules.stable_rules(_import("aspnetcore")) == {
        "probes": {"p": {"body": {"traceId": "<trace-id>"}}}
    }
    for framework in ("fastapi", "gin"):
        assert rules.stable_rules(_import(framework))["probes"]["p"]["body"]["traceId"] == TRACE


def test_proposal_evidence_is_stable_for_a_dotnet_import():
    rec = [
        {"id": "p1", "evidence": {"rows": [{"id": 1, "updated_at": "2026-10-23 17:31:35.858469"}]}}
    ]
    assert (
        rules.stable_proposals(rec, "aspnetcore")[0]["evidence"]["rows"][0]["updated_at"]
        == "<timestamp>"
    )
    assert rules.stable_proposals(rec, "gin") == rec
