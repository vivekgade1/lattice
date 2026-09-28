"""lattice.importer.policy: the deviations a lift adopts, per case, and the token
policies read from the original's current-user dependency."""

from __future__ import annotations

from pathlib import Path

from lattice.importer.extract import _Index, extract
from lattice.importer.lift import load_profile
from lattice.importer.policy import NotNull, PageBound, TokenPolicy, auth_policies, deviations_for
from lattice.importer.profile_gen import generate_profile
from lattice.importer.verify import Case

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "bench.template_app.app"


def _api():
    model = extract(REPO_ROOT, PACKAGE)
    return model, load_profile(generate_profile(model), "policy_test_profile").profile().api


def _case(what: str, **kw) -> Case:
    return Case(f"r.{what}", "GET", "/x", route="r", **kw)


def test_page_bounds_name_the_out_of_bound_cases():
    _, api = _api()
    policies = [PageBound("skip", 0, None), PageBound("limit", 0, 100)]
    cases = [
        _case("huge", params={"limit": 1_000_000}),
        _case("neg", params={"skip": -1}),
        _case("ok", params={"limit": 100, "skip": 0}),
        _case("text", params={"limit": "many"}),  # a 422 on both sides: not a deviation
    ]
    out = deviations_for(cases, policies, api)
    assert set(out) == {"r.huge", "r.neg"}
    assert out["r.huge"].id == "bounded-page-size" and out["r.huge"].status == 422
    assert out["r.huge"].body == {"detail": [{"loc": ["query", "limit"], "msg": "must be <= 100"}]}
    assert out["r.neg"].id == "non-negative-page-window"
    assert out["r.neg"].body == {"detail": [{"loc": ["query", "skip"], "msg": "must be >= 0"}]}


def test_not_null_names_explicit_nulls_only():
    _, api = _api()
    cases = [
        _case("null", json={"title": None}),
        _case("absent", json={}),
        _case("set", json={"title": "x"}),
    ]
    out = deviations_for(cases, [NotNull("title")], api)
    assert set(out) == {"r.null"}
    assert out["r.null"].body == {"detail": [{"loc": ["body", "title"], "msg": "must not be null"}]}


def test_the_templates_token_policies_come_from_get_current_user():
    model, api = _api()
    policies = auth_policies(model, _Index(REPO_ROOT, PACKAGE), api)
    assert [(p.case, p.id, p.original) for p in policies] == [
        ("bad_token", "invalid-token-401", (403, "Could not validate credentials")),
        # no guard on the claim: a token without it reaches the caller lookup (only
        # requested when the suite mints its own claims; the template's has a mint)
        ("claimless_token", "claimless-token", (404, "User not found")),
        ("unknown_user_token", "unknown-user-token", (404, "User not found")),
    ]
    out = deviations_for(
        [_case("bad_token"), _case("unknown_user_token"), _case("no_token")], policies, api
    )
    assert set(out) == {"r.bad_token", "r.unknown_user_token"}
    assert all((d.status, d.body) == (401, {"detail": "Not authenticated"}) for d in out.values())


def test_a_policy_matching_lattice_is_not_declared():
    model, api = _api()
    same = TokenPolicy("bad_token", "x", "y", (401, "Not authenticated"))
    assert same.original == (401, api.unauthorized_detail)
    # auth_policies leaves such a policy out; deviations_for would declare what it is given
    assert all(
        p.original != (401, api.unauthorized_detail)
        for p in auth_policies(model, _Index(REPO_ROOT, PACKAGE), api)
    )
