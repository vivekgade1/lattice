"""The rule oracle (lattice.spec.probes) and layer R (plan 2026-10-05 phase 2, W1-B).

Test bed: the multi-vendor app with its hand spec plus contracts for its program-less
routes (bench/specs/multivendor_contracts.py). The original app checks clean in layer R;
mutants of it -- each a tmp_path copy of the app package with one edit -- are refuted by
the rule item whose guarantee they break. Also: `Contract` and its validation codes,
every item verdict kind, the report's item verdicts, the pluggable transport, and the
language-rules decision-log row.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bench.edit_slice.repos import multivendor as mv
from bench.import_repos.multivendor import make_app
from bench.specs.multivendor_contracts import CONTRACTS
from bench.specs.multivendor_contracts import SPEC as MV_SPEC
from lattice.spec import (
    CODES,
    Contract,
    ContractError,
    ContractParam,
    Report,
    check,
    validate,
)
from lattice.spec.probes import (
    Answer,
    RuleResult,
    allowed_roles,
    asgi_sessions,
    owner_scope,
    route_contracts,
    rule_items,
    run_probes,
)
from lattice.typesys import Scalar

ROOT = Path(__file__).resolve().parents[2]
APP_PKG = "bench.import_repos.multivendor.app"
APP_DIR = ROOT / "bench/import_repos/multivendor/app"


def run_r(spec=MV_SPEC, factory=make_app, **kw: Any) -> Report:
    kw.setdefault("login", mv.login)
    kw.setdefault("mint_token", mv.mint)
    return check(spec, factory, mv.db_factory, layers=kw.pop("layers", "R"), **kw)


@pytest.fixture(scope="module")
def clean() -> Report:
    return run_r()


def verdicts(report: Report) -> dict[str, str]:
    assert report.rules is not None
    return {i: v.verdict for i, v in report.rules.items.items()}


# ---- Contract and its validation -----------------------------------------------------------


def test_contract_normalises_its_collections_and_auth():
    c = Contract(
        "post",
        "/x/{x_id}",
        auth=("Buyer", "Vendor"),
        params=[ContractParam("x_id", "path", Scalar.INT)],
        errors=[ContractError(404, "gone", when="not_found")],
    )
    assert c.method == "POST"
    assert c.auth == "Roles(Buyer, Vendor)"
    assert isinstance(c.params, tuple) and isinstance(c.errors, tuple)
    assert c.endpoint == "POST /x/{x_id}"
    hash(c)


def test_the_test_bed_validates():
    assert [p.render() for p in validate(MV_SPEC)] == []
    assert len(MV_SPEC.contracts) == 4


def _codes(*contracts: Contract) -> list[str]:
    return [p.code for p in validate(replace(MV_SPEC, contracts=contracts))]


def test_contract_validation_codes():
    ok = Contract("GET", "/z", auth="Public")
    assert _codes(ok) == []
    # S017 malformed: method, path, kind, owner_scope
    assert _codes(Contract("FETCH", "/z", auth="Public")) == ["S017"]
    assert _codes(Contract("GET", "z", auth="Public")) == ["S017"]
    assert _codes(Contract("GET", "/z", auth="Public", kind="frobnicate")) == ["S017"]
    assert _codes(Contract("GET", "/z", auth="Public", table="Order", owner_scope="x")) == ["S017"]
    # S018 one endpoint, two contracts
    assert _codes(ok, Contract("GET", "/z", auth="Admin")) == ["S018"]
    # S019 unknown role
    assert _codes(Contract("GET", "/z", auth="Roles(Pirate)")) == ["S019"]
    assert _codes(Contract("GET", "/z", auth="Pirate")) == ["S019"]
    # S002 / S003: unknown table, unknown column
    assert _codes(Contract("GET", "/z", auth="Public", table="Nope")) == ["S002"]
    bad_col = ContractParam("q", "body", Scalar.INT, column="Order.nope")
    assert _codes(Contract("POST", "/z", auth="Public", params=(bad_col,))) == ["S003"]
    bad_ref = ContractParam("q", "body", Scalar.INT, ref="Nope")
    assert _codes(Contract("POST", "/z", auth="Public", params=(bad_ref,))) == ["S002"]
    # S020 malformed parameter: source, a bound of the wrong kind, a path param not in
    # the path, a parameter given twice
    assert _codes(Contract("GET", "/z", auth="Public", params=(ContractParam("q", "cookie"),))) == [
        "S020"
    ]
    strlen = ContractParam("q", "query", Scalar.INT, max_len=3)
    assert _codes(Contract("GET", "/z", auth="Public", params=(strlen,))) == ["S020"]
    ghost = ContractParam("ghost_id", "path", Scalar.INT)
    assert _codes(Contract("GET", "/z", auth="Public", params=(ghost,))) == ["S020"]
    twice = (ContractParam("q", "query"), ContractParam("q", "query"))
    assert _codes(Contract("GET", "/z", auth="Public", params=twice)) == ["S020"]
    # S021 malformed error: status, trigger, example keys
    assert _codes(Contract("GET", "/z", auth="Public", errors=(ContractError(99),))) == ["S021"]
    assert _codes(Contract("GET", "/z", auth="Public", errors=(ContractError(404, when="x"),))) == [
        "S021"
    ]
    weird = ContractError(400, example={"body": {}})
    assert _codes(Contract("GET", "/z", auth="Public", errors=(weird,))) == ["S021"]
    # S022 unknown response model
    assert _codes(Contract("GET", "/z", auth="Public", response="Nope")) == ["S022"]
    # S023 owner scope on a table nobody owns, or without a table
    assert _codes(Contract("GET", "/z", auth="Public", table="Product", owner_scope="owner")) == [
        "S023"
    ]
    assert _codes(Contract("GET", "/z", auth="Public", owner_scope="any")) == ["S023"]
    assert {"S017", "S018", "S019", "S020", "S021", "S022", "S023"} <= set(CODES)


def test_unknowns_are_allowed_they_make_items_unclear():
    unknown = Contract("GET", "/z", auth=None, table="Order", owner_scope="unknown")
    assert _codes(unknown) == []
    assert _codes(Contract("GET", "/z", auth="Public", errors=(ContractError(None),))) == []


def test_an_unchecked_requirement_may_concern_a_contract_endpoint():
    from lattice.spec import Unchecked

    spec = replace(MV_SPEC, unchecked=(Unchecked("x", ("POST /orders",)),))
    assert validate(spec) == []


# ---- the oracle: routes, items, predictions ------------------------------------------------


def test_route_contracts_are_the_explicit_ones_plus_one_per_program_endpoint():
    routes = {c.endpoint: c for c in route_contracts(MV_SPEC)}
    assert len(routes) == 17
    for c in CONTRACTS:
        assert routes[c.endpoint] is c
    assert routes["GET /orders/me"].auth == "Roles(Buyer, Vendor)"
    assert routes["GET /orders/me"].table == "Order"
    assert routes["GET /orders/me"].kind == "list"
    # a derived contract leaves the scope to the table's ownership (owner_scope None)
    assert routes["GET /orders/me"].owner_scope is None
    assert owner_scope(MV_SPEC, routes["GET /orders/me"]) == "owner"
    assert routes["POST /vendors"].kind == "create"
    assert routes["POST /vendors"].table == "Vendor"
    assert routes["GET /users/me"].table == "User"
    assert routes["GET /orders/{order_id}"].params[0].ref == "Order"
    verify = routes["PATCH /vendors/{vendor_id}/verify"]
    assert owner_scope(MV_SPEC, verify) is None  # admin only: n/a
    assert routes["GET /products/{product_id}"].kind == "read"
    assert owner_scope(MV_SPEC, routes["GET /products/{product_id}"]) is None  # shared
    assert owner_scope(MV_SPEC, CONTRACTS[2]) == "any"  # stated


def test_allowed_roles():
    api = MV_SPEC.profile.api
    assert allowed_roles("Public", api) is None
    assert allowed_roles("Authenticated", api) == {"Admin", "Buyer", "Vendor"}
    assert allowed_roles("Admin", api) == {"Admin"}
    assert allowed_roles("Vendor", api) == {"Vendor", "Admin"}  # auth=<role> admits the admin
    assert allowed_roles("Roles(Vendor)", api) == {"Vendor"}
    assert allowed_roles(None, api) is None


def test_rule_items_cover_tables_and_routes():
    items = {i.id: i for i in rule_items(MV_SPEC)}
    for t in ("Order", "OrderItem", "Product", "User", "Vendor"):
        assert f"T1 {t}" in items
    assert items["T1 Order"].predicted == {"ownership": "owner", "column": "user_id"}
    assert items["T1 OrderItem"].predicted == {
        "ownership": "owner_via",
        "via": ["order_id", "Order"],
    }
    assert items["T1 Product"].predicted == {"ownership": "shared"}
    assert items["T1 User"].predicted == {"ownership": "users"}
    assert "T2 User.hashed_password" in items
    assert "T3 OrderItem.quantity gt 0" in items
    assert "T4 Order.status" in items
    assert "T4 User.role" in items
    assert "T5 Vendor (user_id)" in items and "T5 Order (id)" in items
    for route in route_contracts(MV_SPEC):
        assert f"R1 {route.endpoint}" in items
        assert f"R3 {route.endpoint} 5xx" in items
    assert "R2 GET /orders/me" in items
    assert "R2 GET /products" not in items  # touches no owned table: n/a
    assert "R3 POST /orders 409" in items
    assert "R3 PATCH /orders/{order_id}/status 404 Order not found" in items
    assert items["R1 PATCH /orders/{order_id}/status"].predicted == {"allowed": ["Admin", "Vendor"]}


# ---- layer R on the original: no findings, every verdict kind ---------------------------------


def test_layer_r_on_the_original_reports_no_findings(clean):
    assert clean.findings == [], clean.render()
    assert clean.verdict == "pass"
    assert clean.counts == {"R": 0}
    v = verdicts(clean)
    assert v["T1 Order"] == "verified"
    assert v["T1 OrderItem"] == "verified"  # owner stamping through POST /orders
    assert v["T1 Product"] == "verified"
    assert v["T1 User"] == "verified"
    assert v["T1 Vendor"] == "verified"
    assert v["T2 User.hashed_password"] == "verified"
    assert v["T3 OrderItem.quantity gt 0"] == "verified"
    assert v["T4 Order.status"] == "verified"
    assert v["T5 Vendor (user_id)"] == "verified"
    assert v["R2 GET /orders/me"] == "verified"
    assert v["R2 GET /orders/{order_id}"] == "verified"
    assert v["R2 POST /orders"] == "verified"
    assert v["R2 PATCH /vendors/me"] == "verified"  # an update without an id: own rows only
    assert v["R2 GET /vendors/me"] == "verified"  # a read without an id: own row only
    assert v["R2 PATCH /orders/{order_id}/status"] == "verified"  # owner_scope any
    assert v["R3 POST /orders 409"] == "verified"
    assert v["R3 PATCH /orders/{order_id}/status 404 Order not found"] == "verified"
    for route in route_contracts(MV_SPEC):
        assert v[f"R1 {route.endpoint}"] == "verified", clean.rules.items[f"R1 {route.endpoint}"]
        assert v[f"R3 {route.endpoint} 5xx"] == "verified"
    assert "refuted" not in v.values()
    assert "unclear" not in v.values()


def test_verified_items_name_their_probes(clean):
    item = clean.rules.items["R2 GET /orders/me"]
    assert item.probes and all(p.startswith("R2 GET /orders/me") or "|" in p for p in item.probes)
    assert all(p in clean.rules.probes for p in item.probes)
    record = clean.rules.probes[item.probes[0]]
    assert record["request"]["path"] == "/orders/me"
    assert 200 <= record["status"] < 300


def test_unexercised_items(clean):
    v = verdicts(clean)
    # an autoincrement key no route takes: no second create can repeat it
    assert v["T5 Order (id)"] == "unexercised"
    # no route writes User.role through a parameter (and every stored value is listed)
    assert v["T4 User.role"] == "unexercised"
    reason = clean.rules.items["T5 Order (id)"].reason
    assert "no route" in reason


def test_an_error_no_probe_can_trigger_is_unexercised_as_unreachable_by_probe():
    odd = Contract(
        "POST",
        "/orders",
        auth="Roles(Buyer)",
        table="Order",
        kind="create",
        owner_scope="owner",
        params=CONTRACTS[1].params,
        errors=(ContractError(409, when="other"),),  # no example: no probe triggers it
    )
    spec = replace(MV_SPEC, contracts=(odd,))
    result = _run(spec, only=("R3 POST /orders",))
    item = result.items["R3 POST /orders 409"]
    assert item.verdict == "unexercised"
    assert item.reason.startswith("unreachable-by-probe")


def test_unclear_items_have_no_candidate():
    vague = Contract(
        "PATCH",
        "/orders/{order_id}/status",
        auth=None,
        table="Order",
        kind="update",
        owner_scope="unknown",
        errors=(ContractError(None, when="other"),),
    )
    spec = replace(MV_SPEC, contracts=(vague,))
    items = {i.id: i for i in rule_items(spec)}
    result = _run(spec, only=("R1 PATCH /orders/{order_id}/status", "R2 PATCH"))
    assert result.items["R1 PATCH /orders/{order_id}/status"].verdict == "unclear"
    assert result.items["R2 PATCH /orders/{order_id}/status"].verdict == "unclear"
    assert "R3 PATCH /orders/{order_id}/status ?" in items
    assert items["R3 PATCH /orders/{order_id}/status ?"].unclear


def _run(spec, factory=make_app, only=None) -> RuleResult:
    return run_probes(
        spec,
        asgi_sessions(factory),
        lambda variant, path: mv.db_factory(variant, path),
        login=mv.login,
        mint_token=mv.mint,
        only=only,
    )


# ---- summaries ------------------------------------------------------------------------------


def test_table_and_route_summaries(clean):
    tables = clean.rules.tables
    assert tables["Order"]["verdict"] == "verified"
    assert tables["User"]["verdict"] == "verified"
    routes = clean.rules.routes
    assert routes["GET /orders/me"]["verdict"] == "verified"
    assert routes["POST /orders"]["verdict"] == "verified"
    assert set(routes) == {c.endpoint for c in route_contracts(MV_SPEC)}


def test_the_report_carries_item_verdicts(clean):
    data = clean.to_dict()
    assert data["layers"] == "R"
    rules = data["rules"]
    item = rules["items"]["T1 Order"]
    assert item["verdict"] == "verified" and item["probes"] and "reason" in item
    assert set(rules) >= {"items", "tables", "routes", "summary"}
    text = clean.render()
    assert "rule items" in text and "verified" in text


def test_layers_a_to_d_report_no_rules_section():
    assert "rules" not in Report(layers="ABCD").to_dict()


# ---- mutants: each refuted by the item whose guarantee it breaks ----------------------------


def write_mutant(root: Path, name: str, rel: str, old: str, new: str) -> Callable[[Path], Any]:
    """A copy of the app package under ``root`` with one edit; its app factory."""
    package = f"mv_mutant_{name}"
    dest = root / package
    shutil.copytree(APP_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    for path in dest.rglob("*.py"):
        text = path.read_text().replace(APP_PKG, package)
        path.write_text(text)
    target = dest / rel
    text = target.read_text()
    assert old in text, f"mutation anchor not found in {rel}"
    target.write_text(text.replace(old, new, 1))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    return _factory(package)


def _factory(package: str) -> Callable[[Path], Any]:
    from sqlmodel import Session

    import bench.import_repos.multivendor  # noqa: F401  (DATABASE_URL / SECRET_KEY)
    from bench.import_repos.multivendor import sqlite_engine

    registry = importlib.import_module(f"{package}._lattice_registry").metadata_registry
    importlib.import_module(f"{package}.models")
    database = importlib.import_module(f"{package}.core.database")
    create_app = importlib.import_module(f"{package}.main").create_app

    def factory(db_path: Path) -> Any:
        engine = sqlite_engine(db_path)
        registry.metadata.create_all(engine)

        def get_session() -> Generator[Session]:
            with Session(engine) as session:
                yield session

        app = create_app()
        app.dependency_overrides[database.get_session] = get_session
        app.state.engine = engine
        return app

    return factory


MUTANTS = {
    # list_own_orders answers every order, not the caller's
    "drop_owner_filter": (
        "repositories/order_repository.py",
        "statement = select(Order).where(Order.user_id == user_id)",
        "statement = select(Order)",
        ("R2 GET /orders/me", "T1 Order"),
        ("R2 GET /orders/me", "T1 Order"),
    ),
    # buyers may move orders' statuses
    "widen_role": (
        "core/permissions.py",
        "        Permission.PLACE_ORDER,\n        Permission.VIEW_OWN_ORDERS,\n    },",
        "        Permission.PLACE_ORDER,\n        Permission.VIEW_OWN_ORDERS,\n"
        "        Permission.UPDATE_ORDER_STATUS,\n    },",
        ("R1 PATCH /orders/{order_id}/status",),
        ("R1 PATCH /orders/{order_id}/status",),
    ),
    # an order line of quantity 0 is accepted
    "remove_bound": (
        "schemas/order.py",
        "quantity: int = Field(gt=0)",
        "quantity: int",
        ("T3 OrderItem.quantity gt 0",),
        ("T3 OrderItem.quantity",),
    ),
    # a second vendor profile for the same user answers 201 (the existing one)
    "swallow_unique_check": (
        "services/vendor_service.py",
        '        raise VendorError("This user is already registered as a vendor")',
        "        return existing",
        ("T5 Vendor (user_id)",),
        ("T5 Vendor",),
    ),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_a_mutant_is_refuted_by_the_expected_item(name, tmp_path):
    rel, old, new, expected, only = MUTANTS[name]
    factory = write_mutant(tmp_path, name, rel, old, new)
    report = run_r(factory=factory, rule_items=only)
    v = verdicts(report)
    for item in expected:
        assert v[item] == "refuted", (item, report.render())
    keys = {f.case.split(" | ")[0] for f in report.findings}
    assert set(expected) <= keys, report.render()
    assert all(f.layer == "R" for f in report.findings)
    finding = next(f for f in report.findings if f.case.startswith(expected[0]))
    assert finding.request and finding.details["actual"]["status"]
    assert finding.details.get("observed") is not None
    # the original passes the same items
    original = run_r(rule_items=only)
    assert all(verdicts(original)[i] == "verified" for i in expected), original.render()


def test_a_refuted_item_proposes_the_observed_fact(tmp_path):
    rel, old, new, _, _ = MUTANTS["widen_role"]
    factory = write_mutant(tmp_path, "widen_role_obs", rel, old, new)
    report = run_r(factory=factory, rule_items=("R1 PATCH /orders/{order_id}/status",))
    item = report.rules.items["R1 PATCH /orders/{order_id}/status"]
    assert item.verdict == "refuted"
    assert item.observed == {"allowed": ["Admin", "Buyer", "Vendor"]}


# ---- transport: probes take a session factory -------------------------------------------------


def test_probes_run_through_any_session_factory():
    seen: list[tuple[str, str]] = []
    base = asgi_sessions(make_app)

    @contextmanager
    def recording(db: Path) -> Iterator[Any]:
        with base(db) as session:

            class Recorder:
                client = getattr(session, "client", None)

                def request(self, method: str, path: str, **kw: Any) -> Answer:
                    seen.append((method, path))
                    return session.request(method, path, **kw)

            yield Recorder()

    result = run_probes(
        MV_SPEC,
        recording,
        mv.db_factory,
        login=mv.login,
        mint_token=mv.mint,
        only=("R2 GET /orders/me",),
    )
    assert result.items["R2 GET /orders/me"].verdict == "verified"
    assert ("GET", "/orders/me") in seen


def test_a_private_column_in_an_answer_is_refuted():
    base = asgi_sessions(make_app)

    @contextmanager
    def leaky(db: Path) -> Iterator[Any]:
        with base(db) as session:

            class Leak:
                client = getattr(session, "client", None)

                def request(self, method: str, path: str, **kw: Any) -> Answer:
                    answer = session.request(method, path, **kw)
                    if path == "/users/me" and isinstance(answer.body, dict):
                        body = {**answer.body, "hashed_password": "$2b$12$leaked"}
                        return Answer(answer.status, answer.headers, body)
                    return answer

            yield Leak()

    only = ("T2 User", "T1 User")
    result = run_probes(MV_SPEC, leaky, mv.db_factory, login=mv.login, only=only)
    item = result.items["T2 User.hashed_password"]
    assert item.verdict == "refuted"
    assert item.observed["shown_by"][0].startswith("GET /users/me")
    clean = run_probes(MV_SPEC, base, mv.db_factory, login=mv.login, only=only)
    assert clean.items["T2 User.hashed_password"].verdict == "verified"


def test_layer_r_runs_with_the_other_layers_and_c_watches_its_requests(tmp_path):
    rel, old, new, _, _ = MUTANTS["remove_bound"]
    factory = write_mutant(tmp_path, "remove_bound_c", rel, old, new)
    report = run_r(factory=factory, layers="CR", rule_items=("T3 OrderItem.quantity",))
    layers = {f.layer for f in report.findings}
    assert layers == {"C", "R"}, report.render()
    c = [f for f in report.findings if f.layer == "C"]
    assert {f.details["invariant"] for f in c} == {"line_quantity_positive"}


def test_unknown_layers_still_fail():
    from lattice.spec import CheckError

    with pytest.raises(CheckError, match="ABCDR"):
        check(MV_SPEC, make_app, mv.db_factory, layers="X")


# ---- language rules ---------------------------------------------------------------------------


def test_the_decision_log_states_that_no_target_compiles_a_contract():
    log = (ROOT / "docs/lattice-language-rules.md").read_text()
    section = log.split("## 6. Decision log", 1)[1].split("## 7.", 1)[0]
    rows = [r for r in section.splitlines() if "`Contract`" in r]
    assert rows, "no decision-log row for Contract"
    assert "no target" in rows[0].lower()
