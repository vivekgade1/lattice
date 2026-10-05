"""Spec emission (Phase 2 plan 2026-10-05, W2-D): a candidate ``lattice.spec.Spec`` from
an app model plus the generated profile -- a ``Contract`` per route, the accepted
proposals -- written as a Python spec module that validates and round-trips.

Test bed: the multi-vendor import (``--no-llm``; in process, no route lifted). Its
contracts are compared field by field with the hand-written ones of
``bench/specs/multivendor_contracts.py`` (the four routes those cover); layer R then runs
the emitted spec on the original app, its verdict counts reported, not gated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from bench.specs.multivendor_contracts import CONTRACTS as HAND_CONTRACTS
from lattice.importer.appmodel import Guard, OwnerUse, RaiseSite, Route, RouteParam
from lattice.importer.emit_spec import (
    candidate_spec,
    contract_diff,
    import_rules,
    load_spec,
    route_contract,
    spec_source,
    write_spec,
)
from lattice.importer.lift import lift_repo
from lattice.spec import Contract, ContractParam, Invariant, Machine, Unchecked, validate
from lattice.typesys import Scalar

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "bench.import_repos.multivendor.app"


@dataclass(frozen=True)
class Proposal:
    """W2-C's proposal shape (lattice.importer.mine), as emit_spec accepts it."""

    kind: str
    spec_object: Any
    evidence: Any = ()
    support: int = 0
    source: str = ""


@pytest.fixture(scope="module")
def imported():
    from bench.import_repos.multivendor import make_app

    return lift_repo(ROOT, PACKAGE, make_app, only=())


@pytest.fixture(scope="module")
def spec(imported):
    return candidate_spec(imported)


def by_endpoint(contracts) -> dict[str, Contract]:
    return {c.endpoint: c for c in contracts}


# ---- the candidate spec ------------------------------------------------------------------


def test_a_contract_for_every_route_and_the_spec_validates(imported, spec):
    assert [p.render() for p in validate(spec)] == []
    routes = [f"{r.method} {r.path}" for r in imported.model.routes]
    assert [c.endpoint for c in spec.contracts] == routes and len(routes) == 17
    # users: the import's seed credentials (layer R signs in as them)
    assert {"admin", "alice", "bob", "carol"} <= set(spec.users)


def test_contract_fields_from_the_app_model(spec):
    c = by_endpoint(spec.contracts)
    get_order = c["GET /orders/{order_id}"]
    assert (get_order.auth, get_order.table, get_order.kind, get_order.owner_scope) == (
        "Authenticated", "Order", "read", "owner",
    )  # fmt: skip
    assert [(e.status, e.when) for e in get_order.errors] == [
        (404, "not_found"),
        (403, "not_owned"),
    ]
    assert get_order.params == (ContractParam("order_id", "path", Scalar.INT, ref="Order"),)
    assert c["GET /products"].kind == "list" and c["GET /products"].auth == "Public"
    assert c["GET /users/me"].table == "User" and c["GET /users/me"].owner_scope is None
    # W3: an admin-only route (Roles(Admin)) has no ownership scope -- no non-admin
    # reaches it, so R2 is n/a (W2-D stated "any": no owner evidence)
    verify = c["PATCH /vendors/{vendor_id}/verify"]
    assert (verify.auth, verify.owner_scope) == ("Roles(Admin)", None)
    register = c["POST /vendors"]
    assert (register.kind, register.owner_scope) == ("create", "owner")


def test_the_hand_contracts_field_by_field(spec):
    """The emitted contracts of the four hand-contracted routes, against the hand ones.
    Rule-bearing fields (auth, table, kind, owner scope, parameters with their bounds,
    one_of, columns and refs, error statuses and triggers) agree; the differences left
    are what static extraction cannot see (error details the code builds, examples)."""
    emitted = by_endpoint(spec.contracts)
    rows = contract_diff([emitted[h.endpoint] for h in HAND_CONTRACTS], HAND_CONTRACTS)
    print("\nemitted vs hand contracts (multi-vendor):")
    for row in rows:
        print(
            f"  {row['endpoint']}: {row['field']}: emitted {row['emitted']!r}, hand {row['hand']!r}"
        )
    fields = {(r["endpoint"], r["field"]) for r in rows}
    core = {"auth", "table", "kind", "owner_scope", "params", "response"}
    assert not {f for f in fields if f[1] in core}, rows
    # errors agree on (status, trigger); only details and examples differ
    for h in HAND_CONTRACTS:
        e = emitted[h.endpoint]
        assert [(x.status, x.when) for x in e.errors] == [(x.status, x.when) for x in h.errors]
    assert all(r["field"].startswith("errors[") for r in rows)
    assert all(r["field"].endswith((".detail", ".example")) for r in rows)


def test_accepted_proposals_go_in(imported):
    inv = Invariant("stock_nonneg", "stock is never negative", "Product.stock_quantity >= 0")
    machine = Machine("Order", "status", {"pending": ("confirmed",), "confirmed": "shipped"})
    note = Unchecked("totals add up", concerns=("Order",))
    accepted = [
        Proposal("invariant", inv),
        Proposal("machine", machine),
        Proposal("unchecked", note),
    ]
    spec = candidate_spec(imported, proposals=accepted)
    assert spec.invariants == (inv,) and spec.machines == (machine,) and spec.unchecked == (note,)
    assert validate(spec) == []
    with pytest.raises(ValueError, match="kind"):
        candidate_spec(imported, proposals=[Proposal("frobnicate", inv)])


# ---- the spec module -----------------------------------------------------------------------


def test_the_module_round_trips(imported, spec, tmp_path):
    inv = Invariant("stock_nonneg", "stock is never negative", "Product.stock_quantity >= 0")
    machine = Machine("Order", "status", {"pending": ("confirmed",)}, initial=("pending",))
    note = Unchecked('a "quoted" note\nover two lines', ("Order",))
    spec = replace(spec, invariants=(inv,), machines=(machine,), unchecked=(note,))
    (tmp_path / "profile.py").write_text(imported.profile.source)
    status = {"R1 GET /orders/{order_id}": "verified", "R3 GET /orders/{order_id} 404": "inferred"}
    path = write_spec(spec, tmp_path / "spec.py", status=status, package=PACKAGE)
    text = path.read_text()
    assert "# R1 GET /orders/{order_id}: verified" in text
    loaded = load_spec(path)
    assert loaded.spec.contracts == spec.contracts
    assert dict(loaded.spec.users) == dict(spec.users)
    assert (loaded.spec.invariants, loaded.spec.machines) == ((inv,), (machine,))
    assert loaded.spec.unchecked == spec.unchecked
    assert loaded.status == status and loaded.package == PACKAGE
    assert validate(loaded.spec) == []
    again = spec_source(loaded.spec, status=loaded.status, package=loaded.package,
                        profile_file=loaded.profile_file)  # fmt: skip
    assert again == text
    meta = json.loads((tmp_path / "spec_meta.json").read_text())
    assert meta["items"]["R1 GET /orders/{order_id}"]["status"] == "verified"


def test_the_module_copies_the_profile_next_to_it(imported, spec, tmp_path):
    src = tmp_path / "import"
    src.mkdir()
    (src / "profile.py").write_text(imported.profile.source)
    out = write_spec(spec, tmp_path / "elsewhere" / "spec.py", status={}, package=PACKAGE,
                     profile=src / "profile.py")  # fmt: skip
    assert (out.parent / "profile.py").read_text() == imported.profile.source
    assert load_spec(out).spec.contracts == spec.contracts


# ---- one route, front-end evidence ------------------------------------------------------


def _route(**kw: Any) -> Route:
    base: dict[str, Any] = dict(
        name="snippet_detail", module="snippets.views", method="DELETE",
        path="/snippets/{pk}", params=(RouteParam("pk", "int", "Int", "path"),),
        response_model=None, response_class=None, status_code=None, dependencies=(),
        auth="unclear", auth_reason="", raises=(), calls=(), tables=("Order",),
        owner_uses=(), body_models=(), line=1,
    )  # fmt: skip
    base.update(kw)
    return Route(**base)


def test_auth_and_owner_scope_from_guards(imported):
    api = imported.lattice_profile.api
    model = imported.model
    owner = Guard("object_owner", "IsOwnerOrReadOnly", 403)
    route = _route(guards=(Guard("authenticated", "IsAuthenticated", 401), owner),
                   raises=(RaiseSite(404, "Not found.", 3, kind="drf"),))  # fmt: skip
    c = route_contract(model, route, api)
    assert (c.auth, c.table, c.kind, c.owner_scope) == ("Authenticated", "Order", "delete", "owner")
    assert [(e.status, e.detail, e.when) for e in c.errors] == [(404, "Not found.", "not_found")]
    roles = _route(
        guards=(Guard("role", "IsVendor", 403, roles=("vendor", "Admin")),),
        method="GET",
        owner_uses=(OwnerUse("Order", "user_id", "compare", 2),),
    )
    c = route_contract(model, roles, api)
    assert (c.auth, c.kind, c.owner_scope) == ("Roles(Vendor, Admin)", "read", "owner")
    admin = _route(
        guards=(Guard("authenticated", "IsAuthenticated"), Guard("admin", "IsAdminUser"))
    )
    assert route_contract(model, admin, api).auth == "Admin"
    custom = _route(guards=(Guard("custom", "IsWeird"),))
    assert route_contract(model, custom, api).auth is None  # unknown: R1 is unclear
    assert route_contract(model, _route(auth="Public"), api).auth == "Public"


# ---- layer R on the original --------------------------------------------------------------


def test_layer_r_on_the_original_with_the_emitted_spec(imported, spec):
    """Reported, not gated (the dev-app gate is W3's)."""
    emitted, result = import_rules(imported, spec=spec)
    assert emitted is spec
    counts = result.summary["items"]
    print(f"\nlayer R, multi-vendor, emitted spec: items {counts}; "
          f"tables {result.summary['tables']}; routes {result.summary['routes']}")  # fmt: skip
    refuted = sorted(i for i, v in result.items.items() if v.verdict == "refuted")
    print("  refuted:", *refuted, sep="\n    ")
    assert sum(counts.values()) == len(result.items) > 0
    assert set(result.routes) == {c.endpoint for c in spec.contracts}
