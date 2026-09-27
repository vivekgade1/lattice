"""lattice.importer.adapt: the extractor's RepoModel as the suite generator's inputs.

The gate: for the template, the adapter reproduces exactly the specs the I2 test writes
by hand (``tests/template/test_template_generated_suite.py``: ROUTES, USER, ITEM, AUTH),
so the generated suite is the same suite.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bench.template_app.app.core import security
from lattice.importer.adapt import Unsupported, adapt, route_key, route_spec
from lattice.importer.extract import _Index, extract
from lattice.importer.lift import load_profile
from lattice.importer.profile_gen import generate_profile
from tests.template import test_template_generated_suite as hand

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "bench.template_app.app"


@pytest.fixture(scope="module")
def adapted():
    model = extract(REPO_ROOT, PACKAGE)
    module = load_profile(generate_profile(model), "adapt_test_profile")
    api = module.profile().api
    return model, api, adapt(model, api, _Index(REPO_ROOT, PACKAGE), module.NATIVES)


@pytest.mark.parametrize("spec", hand.ROUTES, ids=lambda s: s.name)
def test_every_template_route_spec_equals_the_hand_written_one(adapted, spec):
    assert adapted[2].routes[spec.name] == spec


def test_the_tables_equal_the_hand_written_ones(adapted):
    assert adapted[2].tables == (hand.USER, hand.ITEM)


def test_the_auth_uses_the_originals_hash_and_token_functions(adapted):
    auth = adapted[2].auth
    ref = hand.AUTH
    for name in (
        "users_table",
        "email_column",
        "password_column",
        "active_column",
        "admin_column",
        "login_path",
        "username_field",
        "password_field",
        "token_field",
        "bad_token",
    ):
        assert getattr(auth, name) == getattr(ref, name), name
    hashed = auth.hash_password("a-password")
    assert security.verify_password("a-password", hashed)[0]
    token = auth.mint_token(hand.generated().plan.user("alice").id)
    assert token.count(".") == 2  # a JWT from the template's own create_access_token


def test_route_keys_qualify_repeated_names(adapted):
    model = adapted[0]
    keys = [route_key(model, r) for r in model.routes]
    assert len(set(keys)) == len(keys)
    assert {"private_create_user", "users_create_user"} <= set(keys)


def test_kinds_of_the_other_routes(adapted):
    kinds = {k: s.kind for k, s in adapted[2].routes.items()}
    assert kinds["test_token"] == "me"
    assert kinds["update_password_me"] == "update_me"
    assert kinds["read_user_by_id"] == "read"
    assert kinds["health_check"] == "other"


def test_an_unclear_route_has_no_spec(adapted):
    model, api, _ = adapted
    r = next(r for r in model.routes if r.name == "read_item")
    with pytest.raises(Unsupported):
        route_spec(model, replace(r, auth="unclear"), api)
