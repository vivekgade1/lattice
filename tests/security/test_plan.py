"""The deterministic generator: per-class output, determinism, and that it reads the spec,
not the app."""

from __future__ import annotations

from lattice.security.plan import generate
from lattice.security.schema import CLASSES


def test_generates_every_class(attack_spec):
    attacks = generate(attack_spec)
    seen = {a.cls for a in attacks}
    assert seen == set(CLASSES), seen


def test_deterministic(attack_spec):
    a = [x.id for x in generate(attack_spec)]
    b = [x.id for x in generate(attack_spec)]
    assert a == b, "two plans of the same spec are identical"
    assert len(a) == len(set(a)), "ids are unique"


def test_authz_covers_every_non_owner_refusal_cell(attack_spec):
    authz = generate(attack_spec, ["authz"])
    # ann's order 1 read: ben and anon are refused in the spec, so both are attacked
    ids = {a.id for a in authz}
    assert "authz-GET-orders-1-ben" in ids
    assert "authz-GET-orders-1-anon" in ids
    # ann (the owner) is never attacked on her own resource
    assert not any(a.id.endswith("-ann") for a in authz)


def test_auth_sends_no_token_and_malformed(attack_spec):
    auth = generate(attack_spec, ["auth"])
    assert any("no-token" in a.id for a in auth)
    assert any("malformed-bearer" in a.id for a in auth)
    # every auth request carries no spec user (it is unauthenticated by construction)
    assert all(r.as_user is None for a in auth for r in a.requests)


def test_bounds_hits_each_stated_limit(attack_spec):
    bounds = generate(attack_spec, ["bounds"])
    # quantity has ge=1 and le=100 -> an under-ge and an over-le attack exist
    assert any("quantity-just-under-ge" in a.id for a in bounds)
    assert any("quantity-just-over-le" in a.id for a in bounds)


def test_bounds_is_not_off_by_one(attack_spec):
    """The just-over-le value is le+1, not le (the mutant); the under-ge value is ge-1."""
    bounds = {a.id: a for a in generate(attack_spec, ["bounds"])}
    over = bounds["bounds-POST-orders-1-items-quantity-just-over-le"]
    assert over.requests[0].json["quantity"] == 101  # le=100 -> 101, never 100
    under = bounds["bounds-POST-orders-1-items-quantity-just-under-ge"]
    assert under.requests[0].json["quantity"] == 0  # ge=1 -> 0, never 1


def test_mass_injects_a_forbidden_column(attack_spec):
    mass = generate(attack_spec, ["mass"])
    # price is a table column the request model (product_id, quantity) does not allow
    assert any("price" in a.id for a in mass)
    price = next(a for a in mass if "price" in a.id)
    assert price.confirm.kind == "row_present"
    assert price.confirm.table == "order_items"  # the SQL table, so the judge can read it


def test_state_repeats_a_terminal_action(attack_spec):
    state = generate(attack_spec, ["state"])
    assert any("cancel" in a.id for a in state)
    cancel = next(a for a in state if "cancel" in a.id)
    assert len(cancel.requests) == 2  # do it, then do it again
