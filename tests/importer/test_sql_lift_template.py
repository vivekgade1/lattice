"""The template's traced SQL lifted to Lattice steps (importer plan I2b, gate for S6):
the generated request suite (tests/template/test_template_generated_suite.py) runs
against the vendored template app under :class:`~lattice.importer.trace.SqlTracer`; each
statement a successful request issues is lifted (:func:`~lattice.importer.sql_lift.
lift_sql`, bindings from the case) and, per route, consolidated; the result must agree
with the hand-written programs (tests/fixtures/template/{items,users}.lattice.py): the
same filters, ordering, limit/offset, count and writes."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from bench.template_app import make_app
from bench.template_profile import template_program
from lattice.importer.outside import db_facts
from lattice.importer.sql_lift import (
    LiftContext,
    Lifted,
    Step,
    consolidate,
    lift_trace,
    program_steps,
    source_chain,
    step,
)
from lattice.importer.trace import RequestTrace, case_bindings, n_plus_one, trace_suite
from tests.template.test_template_generated_suite import ROUTES, generated

LIFTED_ROUTES = ("read_items", "read_item", "create_item", "update_item", "delete_item",
                 "read_users", "update_user_me")  # fmt: skip
SPECS = {r.name: r for r in ROUTES}
PROGRAM = program_steps(template_program())
AUTH_LOOKUP = (Step("user"),)


@pytest.fixture(scope="module")
def traced(tmp_path_factory) -> tuple[dict[str, RequestTrace], LiftContext, object]:
    suite = generated()
    root = tmp_path_factory.mktemp("lift-template")
    seed = suite.db_factory("seed", root / "seed.db")
    cases = [c for c in suite.cases if c.route in LIFTED_ROUTES]
    traces = trace_suite(make_app, {"seed": Path(seed)}, cases, login=suite.login)
    db = db_facts(seed)
    ctx = LiftContext(
        tables={"item": "Item", "user": "User"},
        columns={t.name: tuple(c.name for c in t.columns) for t in db.tables},
        owners={"item": "owner_id"},
        users_table="user",
        keys={"item": "id", "user": "id"},
    )
    return traces, ctx, suite


@pytest.fixture(scope="module")
def lifted(traced) -> dict[str, list[Lifted]]:
    """Per route: every statement of every 2xx request, lifted (the auth lookup of the
    caller's row dropped)."""
    traces, ctx, suite = traced
    by_case = {c.name: c for c in suite.cases}  # type: ignore[attr-defined]
    out: dict[str, list[Lifted]] = defaultdict(list)
    for name, trace in traces.items():
        if trace.status is None or not 200 <= trace.status < 300:
            continue
        case = by_case[name]
        spec = SPECS[case.route]
        caller = suite.plan.user(case.user).id if case.user else None  # type: ignore[attr-defined]
        defaults = {p.name: p.default for p in spec.params if p.default is not None}
        bindings = case_bindings(case, spec.path, caller=caller, defaults=defaults)
        statements = [(q.template or q.sql, q.params) for q in trace.queries()]
        for got in lift_trace(statements, _with(ctx, bindings)):
            assert isinstance(got, Lifted), (name, got)
            if got.steps != AUTH_LOOKUP:
                out[case.route].append(got)  # type: ignore[index]
    return out


def _with(ctx: LiftContext, bindings: dict) -> LiftContext:
    from dataclasses import replace

    return replace(ctx, bindings=bindings)


def chain(endpoint: str, var: str) -> tuple[Step, ...]:
    return source_chain(PROGRAM[endpoint], var)[0]


def reads(lifts: list[Lifted]) -> list[tuple[Step, ...]]:
    return [x.steps for x in consolidate([x for x in lifts if x.kind in ("read", "count")])]


def writes(lifts: list[Lifted], kind: str) -> list[Lifted]:
    return [x for x in lifts if x.kind == kind]


def test_every_traced_statement_lifts(lifted):
    assert set(lifted) == set(LIFTED_ROUTES)


def test_read_items_is_the_owned_sorted_counted_page(lifted):
    # count(*) folded into the page; the admin's unfiltered variant into accessible(by=user)
    assert reads(lifted["read_items"]) == [chain("read_items", "result")]
    assert chain("read_items", "result") == (
        step("fetch", "Item"),
        step("accessible", by="user"),
        step("sort", by=["created_at"], desc=True),
        step("page", limit="limit", offset="skip", total=True),
    )


def test_read_users_is_the_sorted_counted_page(lifted):
    # SQLAlchemy reads every column (hashed_password too); UserPublic projects them in
    # the response, where the Lattice program selects USER_PUBLIC in the query.
    hand = chain("read_users", "result")
    assert hand[1].op == "select"
    assert reads(lifted["read_users"]) == [tuple(s for s in hand if s.op != "select")]


@pytest.mark.parametrize("route", ["read_item", "update_item", "delete_item"])
def test_by_id_routes_read_the_row_by_its_key(lifted, route):
    # session.get(Item, id) (and the refresh after a commit): the SQL-visible part of
    # found = where(fetch(Item), col("id") == id); the ownership check is Python code
    # in the template (require(..., owner_is(user)) in the program).
    assert set(reads(lifted[route])) == {chain(route, "found")}


def test_update_item_writes_the_sent_fields(lifted):
    updates = writes(lifted["update_item"], "update")
    hand = chain("update_item", "updated")
    assert {x.steps[:-1] for x in updates} == {hand[:-1]}  # the row by its key
    sent = {k for x in updates for k, _ in x.steps[-1].args}
    assert sent == {k for k, _ in hand[-1].args} == {"title", "description"}
    for x in updates:  # each value is the request's own field
        assert all(k == v for k, v in x.steps[-1].args), x.steps[-1]


def test_delete_item_deletes_the_row_by_its_key(lifted):
    assert {x.steps for x in writes(lifted["delete_item"], "delete")} == {
        chain("delete_item", "gone")
    }


def test_create_item_inserts_the_callers_row(lifted):
    inserts = writes(lifted["create_item"], "insert")
    hand = chain("create_item", "item")[0]
    assert {x.steps[0].arg("") for x in inserts} == {"Item"}
    assert {k for x in inserts for k, _ in x.steps[0].args} == {k for k, _ in hand.args}
    # a request that sends a description writes exactly what the program writes; one
    # that does not writes NULL (SQLAlchemy lists every column)
    assert {x.steps[0] for x in inserts} == {
        hand, step("insert", "Item", owner="user", title="title", description="None",
                   created_at="fn.now()"),
    }  # fmt: skip
    assert all(any("generated" in n for n in x.notes) for x in inserts)
    # the only other statement: session.refresh(item) re-reading the new row
    others = [x for x in lifted["create_item"] if x.kind != "insert"]
    assert others and {x.kind for x in others} == {"refresh"}


def test_update_user_me_updates_the_callers_row(lifted):
    updates = writes(lifted["update_user_me"], "update")
    hand = chain("update_user_me", "updated")
    assert {x.steps[0] for x in updates} == {hand[0]} == {Step("user")}
    assert {k for x in updates for k, _ in x.steps[-1].args} == {"email", "full_name"}
    # the email lookup is the template's uniqueness check; the program declares it as
    # update(..., unique_or=Conflict(...)) instead of a read
    assert reads(lifted["update_user_me"]) == [
        (step("fetch", "User"), step("where", "col('email') == email"))
    ]


def test_the_template_routes_have_no_n_plus_one(traced):
    traces, _, _ = traced
    assert [n for t in traces.values() for n in n_plus_one(t)] == []


def test_the_suite_exercised_every_route(traced):
    traces, _, _ = traced
    ok = {t.route for t in traces.values() if t.status and 200 <= t.status < 300}
    assert ok == set(LIFTED_ROUTES)
