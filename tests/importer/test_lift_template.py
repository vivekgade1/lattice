"""The I3 gate: the FastAPI template imported with the deterministic lifters only (no
LLM), ``lift_repo(repo_root, app_package, app_factory)``.

* all 9 proof routes are lifted and verified, each on its own against the live template;
* the verdict equals the hand-written proof's (``tests/template/
  test_template_generated_suite.py``): the same generated suite, the same declared
  deviations case by case, the same tally, every raise site reached;
* the lifted programs lower to the same checked plans as the hand-written
  ``tests/fixtures/template/*.lattice.py`` (compared as plans, not text);
* the 53-case template differential (``tests/template/test_template_differential.py``)
  passes on the lifted programs;
* the soundness gate: a lifter made subtly wrong (the owner check dropped, a wrong
  status, a program the checker refuses) never gets its route emitted -- the route is
  reported as needing the LLM.
"""

from __future__ import annotations

import ast
import dataclasses
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from bench.template_app import make_app
from bench.template_app.app import utils as template_utils
from bench.template_profile import template_plans
from lattice.api.runtime import build_app
from lattice.diagnostics import Span
from lattice.importer.lift import HEADER, ImportResult, check_program, lift_repo
from lattice.importer.lifters import create, get_or_404, owner_check
from lattice.importer.lifters.base import Step
from tests.template import test_template_differential as proof
from tests.template import test_template_generated_suite as hand

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "bench.template_app.app"
ROUTES = [r.name for r in hand.ROUTES]  # the 9 routes of the template proof
LIFTERS = {
    "read_items": ("page_list",),
    "read_item": ("get_or_404", "owner_check", "returns"),
    "create_item": ("create", "returns"),
    "update_item": ("get_or_404", "owner_check", "partial_update", "returns"),
    "delete_item": ("get_or_404", "owner_check", "delete"),
    "read_users": ("admin_list",),
    "read_user_me": ("me",),
    "update_user_me": ("unique_check", "partial_update", "me"),
    "login_access_token": ("login",),
}


def _lift(**kw: Any) -> ImportResult:
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    template_utils.OUTBOX.clear()
    return lift_repo(REPO_ROOT, PACKAGE, make_app, **kw)


@pytest.fixture(scope="module")
def imported() -> ImportResult:
    return _lift()


# ---- lifted and verified -------------------------------------------------------------------


@pytest.mark.parametrize("name", ROUTES)
def test_every_template_route_is_lifted_and_verified(imported, name):
    r = imported.result(name)
    assert r.status == "lifted-verified", (r.reason, r.diagnostics)
    assert r.lift.lifters == LIFTERS[name]
    assert r.verify is not None and r.verify.unverified == () and not r.needs_llm


def test_only_verified_routes_are_emitted(imported):
    emitted = {
        node.name
        for src in imported.programs.values()
        for node in ast.parse(src).body
        if isinstance(node, ast.FunctionDef)
    }
    verified = {r.key for r in imported.verified}
    assert emitted == verified
    # beyond the proof's 9, test_token (``return current_user``) is lifted and verified
    assert verified == {*ROUTES, "test_token"}
    assert set(imported.programs) == {"items", "login", "users"}
    for r in imported.needs_llm:
        assert r.status == "no-lifter" and r.reason.startswith("no lifter matches")
        assert r.source is None


def test_every_emitted_program_checks(imported):
    for router, src in imported.programs.items():
        assert src.startswith(HEADER), router
        plans, errors = check_program(src, imported.lattice_profile)
        assert plans is not None and errors == (), router


# ---- the verdict equals the hand-written proof's -------------------------------------------


def test_the_suite_is_the_hand_written_proofs(imported):
    assert imported.suite is not None

    def same(cases):  # minted tokens carry their expiry time: compare what they name
        return {c.name: dataclasses.replace(c, token=_sub(c.token)) for c in cases}

    ours = [c for c in imported.suite.cases if c.route in ROUTES]
    assert same(ours) == same(hand.generated().cases)


def _sub(token: str | None) -> str | None:
    if token is None or token.count(".") != 2:
        return token
    import jwt

    return "sub:" + jwt.decode(token, options={"verify_signature": False})["sub"]


# The hand-written template profile answers every token naming no caller with Lattice's
# 401 and declares the template's answers as deviations; the import carries them in the
# profile instead (NativeToken(invalid=, unknown=), spec 2026-09-27 surface §4.1), so
# those cases are equal, not declared.
TOKEN_DEVIATIONS = frozenset({"invalid-token-401", "unknown-user-token", "claimless-token"})
TEMPLATE_TOKEN_ANSWERS = ((403, "Could not validate credentials"), (404, "User not found"))


def test_the_profile_carries_the_templates_token_answers(imported, lifted_plans):
    auth = imported.lattice_profile.api.auth
    assert (auth.invalid, auth.unknown) == TEMPLATE_TOKEN_ANSWERS
    for plan in lifted_plans.values():
        if plan.auth != "Public":
            assert (plan.token_invalid, plan.token_unknown) == TEMPLATE_TOKEN_ANSWERS


def test_the_deviations_are_the_hand_written_proofs_case_by_case(imported):
    ours = {n: d for name in ROUTES for n, d in imported.result(name).deviations.items()}
    theirs = {
        n: d
        for n, d in hand.deviations_for(hand.generated().cases).items()
        if d.id not in TOKEN_DEVIATIONS
    }
    assert set(ours) == set(theirs)
    for name, d in theirs.items():
        assert (ours[name].id, ours[name].kind, ours[name].status, ours[name].body) == (
            d.id,
            d.kind,
            d.status,
            d.body,
        ), name


def test_the_tally_is_the_hand_written_proofs(imported):
    tallies = [imported.result(n).tally() for n in ROUTES]
    total = tuple(sum(t[i] for t in tallies) for i in range(4))
    # hand: (91, 18, 29, 0), with the Email finding now closed; its 16 token deviations
    # are exact matches here
    assert total == (107, 18, 13, 0)
    kinds = Counter(
        o.deviation
        for n in ROUTES
        for o in imported.result(n).verify.outcomes.values()
        if o.kind == "deviation"
    )
    assert kinds == {
        "bounded-page-size": 3,
        "non-negative-page-window": 6,
        "null-into-required-column": 4,
    }


def test_every_raise_site_is_reached(imported):
    reached = {
        (s.function, s.status, s.detail)
        for n in ROUTES
        for s, hits in imported.result(n).verify.hits.items()
        if hits
    }
    assert reached == {
        ("read_item", 404, "Item not found"),
        ("read_item", 403, "Not enough permissions"),
        ("update_item", 404, "Item not found"),
        ("update_item", 403, "Not enough permissions"),
        ("delete_item", 404, "Item not found"),
        ("delete_item", 403, "Not enough permissions"),
        ("update_user_me", 409, "User with this email already exists"),
        ("login_access_token", 400, "Incorrect email or password"),
        ("login_access_token", 400, "Inactive user"),
        ("get_current_user", 403, "Could not validate credentials"),
        ("get_current_user", 404, "User not found"),
        ("get_current_user", 400, "Inactive user"),
        ("get_current_active_superuser", 403, "The user doesn't have enough privileges"),
    }


# ---- the lifted programs are the hand-written ones, as checked plans ------------------------


def _shape(o: Any) -> Any:
    """A plan with its source positions dropped (everything else compared)."""
    if isinstance(o, Span):
        return None
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return type(o).__name__, tuple(
            (f.name, _shape(getattr(o, f.name))) for f in dataclasses.fields(o)
        )
    if isinstance(o, tuple | list):
        return tuple(_shape(x) for x in o)
    if isinstance(o, dict):
        return tuple(sorted((k, _shape(v)) for k, v in o.items()))
    return o


@pytest.fixture(scope="module")
def lifted_plans(imported):
    plans, errors = check_program(imported.program(), imported.lattice_profile)
    assert plans is not None, errors
    return {p.name: p for p in plans}


@pytest.mark.parametrize("name", ROUTES)
def test_the_lifted_plan_equals_the_hand_written_plan(lifted_plans, name):
    """Equal but for the token answers (the template's, carried by the profile)."""
    reference = {p.name: p for p in template_plans()}[name]
    lifted = dataclasses.replace(lifted_plans[name], token_invalid=None, token_unknown=None)
    assert _shape(lifted) == _shape(reference)


def test_the_plan_comparison_sees_a_difference(imported):
    """The comparison is not vacuous: read_item with another 403 text lowers to a
    different plan."""
    reference = {p.name: p for p in template_plans()}
    source = imported.result("read_item").source
    assert source is not None
    changed = source.replace('Forbidden("Not enough permissions")', 'Forbidden("No")')
    assert changed != source
    plans, errors = check_program(HEADER + changed, imported.lattice_profile)
    assert plans is not None, errors
    assert _shape(plans[0]) != _shape(reference["read_item"])


# ---- the 53-case template differential on the lifted programs -------------------------------


def test_the_template_differential_passes_on_the_lifted_programs(imported, lifted_plans):
    plans = tuple(lifted_plans.values())

    def factory(path: Path) -> Any:
        return build_app(plans, path, natives=imported.natives)

    template_utils.OUTBOX.clear()
    declared = {k: d for k, d in proof.DEVIATIONS.items() if d.id not in TOKEN_DEVIATIONS}
    result = proof._verify(make_app, factory, deviations=declared)
    tally = {
        "suite": result.tally([c.name for c in proof.TEMPLATE_SUITE])[:3],
        "extra": result.tally([c.name for c in proof.EXTRA_CASES])[:3],
    }
    # the proof's tally with its token deviations (2 suite, 1 extra) now exact matches
    assert tally == {"suite": (42, 9, 2), "extra": (9, 0, 0)}
    assert result.mismatches == {} and result.ok, result.render()


# ---- the soundness gate --------------------------------------------------------------------


def _patched(module: Any, change) -> Any:
    real = module.match

    def match(route, model):
        f = real(route, model)
        return None if f is None else change(f)

    return match


def _assert_needs_llm(result: ImportResult, name: str) -> None:
    r = result.result(name)
    assert r.status == "lifted-unverified" and r.needs_llm
    assert r.source is not None  # the (wrong) Lattice is kept as evidence for the LLM ...
    assert result.programs == {}  # ... and never emitted
    assert r not in result.verified


def test_a_lifter_dropping_the_owner_check_is_not_emitted(monkeypatch):
    """The owner check's statement is claimed but produces nothing: the checker refuses
    the program (L802: Item rows reach the caller unchecked) before any request."""
    monkeypatch.setattr(
        owner_check,
        "match",
        _patched(owner_check, lambda f: dataclasses.replace(f, steps=(), errors=())),
    )
    result = _lift(only={"read_item"})
    _assert_needs_llm(result, "read_item")
    r = result.result("read_item")
    assert "require" not in (r.source or "")
    assert r.verify is None and any("L802" in d for d in r.diagnostics)


def test_a_lifter_turning_the_403_into_a_filter_is_not_emitted(monkeypatch):
    """Subtler: the owner check becomes an ``owned`` filter. The program is valid Lattice
    (the checker accepts it) and safe, but another user's item now answers 404 where the
    original answers 403 -- verification catches it."""

    def owned_filter(f):
        fetch, where, one = f.steps
        mine = Step("mine", "owned", (f"{{#{fetch.name}}}",), (("by", "user"),))
        where = dataclasses.replace(where, args=("{#mine}", *where.args[1:]))
        return dataclasses.replace(f, steps=(fetch, mine, where, one))

    monkeypatch.setattr(get_or_404, "match", _patched(get_or_404, owned_filter))
    monkeypatch.setattr(
        owner_check,
        "match",
        _patched(owner_check, lambda f: dataclasses.replace(f, steps=(), errors=())),
    )
    result = _lift(only={"read_item"})
    _assert_needs_llm(result, "read_item")
    r = result.result("read_item")
    assert "owned(items, by=user)" in (r.source or "") and r.diagnostics == ()
    mismatches = r.verify.mismatches
    assert "read_item.other_user_row" in mismatches  # (the error.403 case is the same request)
    assert mismatches["read_item.other_user_row"].diff.status == (403, 404)
    assert r.reason.startswith("verification failed")


def test_a_lifter_with_a_wrong_status_is_not_emitted(monkeypatch):
    def bad_request(f):
        steps = tuple(
            dataclasses.replace(s, kwargs=(("else_", 'BadRequest("Item not found")'),))
            if s.op == "one"
            else s
            for s in f.steps
        )
        return dataclasses.replace(f, steps=steps, errors=("BadRequest",))

    monkeypatch.setattr(get_or_404, "match", _patched(get_or_404, bad_request))
    result = _lift(only={"read_item"})
    _assert_needs_llm(result, "read_item")
    mismatch = result.result("read_item").verify.mismatches["read_item.missing_id"]
    assert mismatch.diff is not None and mismatch.diff.status == (404, 400)


def test_a_lifted_program_the_checker_refuses_is_not_emitted(monkeypatch):
    def no_owner(f):
        (s,) = f.steps
        return dataclasses.replace(
            f, steps=(Step(s.name, s.op, s.args, s.kwargs[1:], s.binds, s.writes),)
        )

    monkeypatch.setattr(create, "match", _patched(create, no_owner))
    result = _lift(only={"create_item"})
    _assert_needs_llm(result, "create_item")
    r = result.result("create_item")
    assert r.verify is None and r.diagnostics  # refused before any request is sent
    assert r.reason == "the checker refuses the lifted program"
