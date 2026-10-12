"""The optional LLM attacker's claim parser: model output is untrusted data. A malformed claim
is skipped, never fatal; a claim can never name another host. (Found by the first real run of
the attacker, whose claim had ``expect_status`` as an integer.)"""

from __future__ import annotations

import pytest

from lattice.security.agent import _claim_to_attack, _parse_claims, _statuses


@pytest.mark.parametrize(
    ("value", "want"),
    [
        (404, (404,)),
        ("404", (404,)),
        ([401, "403"], (401, 403)),
        (None, (401, 403, 404)),
        ("nope", (401, 403, 404)),
        ([], (401, 403, 404)),
    ],
)
def test_expect_status_accepts_int_str_or_list(value, want):
    assert _statuses(value) == want


def test_an_integer_expect_status_is_a_valid_claim():
    a = _claim_to_attack(
        0,
        {
            "method": "get",
            "path": "/v1/habits/1",
            "as_user": "ben",
            "expect_status": 404,
            "why": "idor",
        },
    )
    assert a is not None and a.confirm.expect_status == (404,) and a.endpoint == "GET /v1/habits/1"


@pytest.mark.parametrize(
    "claim",
    [
        {"method": "GET"},  # no path
        {"path": "/x"},  # no method
        {"method": "GET", "path": "http://evil.example/x"},  # another host
        {"method": "GET", "path": "//evil.example/x"},
        {"method": "GET", "path": "v1/habits"},  # not rooted
        {"method": "GET", "path": "/ok\nHost: evil"},  # header injection shape
    ],
)
def test_unusable_or_hostile_claims_are_skipped(claim):
    assert _claim_to_attack(0, claim) is None


def test_odd_field_types_do_not_crash():
    a = _claim_to_attack(
        0,
        {
            "method": "POST",
            "path": "/v1/x",
            "as_user": 7,
            "headers": "x",
            "expect_status": {"a": 1},
        },
    )
    assert a is not None and a.requests[0].as_user is None and a.requests[0].headers is None
    assert a.confirm.expect_status == (401, 403, 404)


def test_parse_claims_ignores_non_objects_and_prose():
    assert _parse_claims('prose [1, {"method": "GET", "path": "/a"}, "x"] tail') == [
        {"method": "GET", "path": "/a"}
    ]
    assert _parse_claims("no json here") == []
