"""The check engine (lattice.spec.check) and its CLI on the mini-shop (tests/spec/minishop.py):
the correct app passes all four layers, and each broken variant is caught by the layer
whose guarantee it breaks."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.verify import Case
from lattice.spec import (
    CheckError,
    Invariant,
    Report,
    Scenario,
    Step,
    cases_for,
    check,
)
from lattice.spec.__main__ import main
from tests.spec import minishop

SPEC = minishop.SPEC


@pytest.fixture(scope="module")
def apps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, str]]:
    root = tmp_path_factory.mktemp("apps")
    variants = ["ok", *minishop.BROKEN]
    return {v: minishop.write_app(root, v) for v in variants}


def run(apps: dict[str, tuple[Path, str]], variant: str, **kw: Any) -> Report:
    app_dir, package = apps[variant]
    kw.setdefault("login", minishop.login)
    return check(
        kw.pop("spec", SPEC),
        minishop.app_factory(package),
        kw.pop("db_factory", minishop.build_db),
        app_dir=app_dir,
        package=package,
        **kw,
    )


def layers(report: Report) -> set[str]:
    return {f.layer for f in report.findings}


# ---- the correct app ----------------------------------------------------------------------


def test_the_correct_app_passes_all_four_layers(apps):
    report = run(apps, "ok")
    assert report.findings == [], report.render()
    assert report.verdict == "pass"
    assert report.counts == {"A": 0, "B": 0, "C": 0, "D": 0}
    # B ran cases for every endpoint with a program, D every scenario step
    endpoints = {c["endpoint"] for c in report.cases.values() if c["layer"] == "B"}
    assert endpoints == {"GET /orders", "GET /orders/{order_id}", "POST /orders/{order_id}/cancel"}
    assert all(c["verdict"] == "correct" for c in report.cases.values())
    assert sum(c["layer"] == "D" for c in report.cases.values()) == 3
    assert report.unchecked == ["a shipped order cannot be changed"]


# ---- layer A ------------------------------------------------------------------------------


def test_layer_a_catches_a_partial_write(apps):
    report = run(apps, "partial_write")
    assert report.verdict == "fail"
    assert layers(report) == {"A"}, report.render()
    (f,) = report.findings
    assert f.code == "PY807"
    assert f.file == "routes.py" and f.line > 0
    assert f.endpoint == "POST /orders/{order_id}/items"
    assert "partial writes" in f.rule
    assert f.details["hint"]


def test_layer_a_overlays_the_specs_owners(apps):
    from lattice.spec.check import spec_policy

    app_dir, package = apps["ok"]
    policy = spec_policy(SPEC, app_dir, package)
    assert dict(policy.owners) == {"Order": "user_id"}
    assert dict(policy.owner_via) == {"OrderItem": ("order_id", "Order")}
    assert set(policy.private["User"]) == {"password"}
    assert policy.baseline == frozenset()  # the app under check is never its own baseline
    if hasattr(policy, "machines"):
        column, edges = policy.machines["Order"]
        assert column == "status" and ("pending", "cancelled") in edges


def test_layer_a_is_skipped_without_the_app_source(apps):
    _, package = apps["partial_write"]
    report = check(SPEC, minishop.app_factory(package), minishop.build_db, layers="A")
    assert report.findings == []
    assert any("layer A skipped" in n for n in report.notes)


# ---- layer B ------------------------------------------------------------------------------


def test_layer_b_catches_a_wrong_answer(apps):
    report = run(apps, "wrong_answer", layers="B")
    assert report.verdict == "fail"
    assert layers(report) == {"B"}
    found = {f.case: f for f in report.findings}
    # ann reads ben's order 3: the spec answers 404, the app shows it
    (f,) = [
        f for f in found.values() if f.request["path"] == "/orders/3" and f.request["user"] == "ann"
    ]
    assert f.endpoint == "GET /orders/{order_id}"
    assert f.verdict == "silent"
    assert f.details["expected"]["status"] == 404
    assert f.details["actual"]["status"] == 200
    assert f.details["actual"]["body"]["user_id"] == 2
    assert "spec program" in f.rule
    # the caller's own orders still agree
    own = [
        c
        for c in report.cases.values()
        if c.get("request", {}).get("path") == "/orders/1" and c["request"]["user"] == "ann"
    ]
    assert own and all(c["verdict"] == "correct" for c in own)


def test_layer_b_reports_database_differences(apps, tmp_path):
    # cancel answers as the spec but never commits
    edit = (
        "routes.py",
        "    session.add(order)\n    session.commit()\n    session.refresh(order)\n",
        "",
    )
    app_dir, package = minishop.write_app(tmp_path, "nocommit", edit)
    report = check(
        SPEC, minishop.app_factory(package), minishop.build_db, layers="B", login=minishop.login
    )
    cancels = [
        f
        for f in report.findings
        if f.request["path"] == "/orders/1/cancel" and f.request["user"] == "ann"
    ]
    (f,) = cancels
    assert f.details["expected"]["status"] == f.details["actual"]["status"] == 200
    changed = f.db_diff["orders"]["changed"]
    assert changed == [{"key": {"id": 1}, "columns": {"status": ["cancelled", "pending"]}}]


def test_layer_b_cases_are_pluggable_and_extra_cases_run(apps):
    calls: list[str] = []

    def only_reads(spec, endpoint, ctx):
        calls.append(f"{endpoint.method} {endpoint.path}")
        return [c for c in cases_for(spec, endpoint, ctx) if endpoint.method == "GET"][:2]

    extra = Case("extra.ann_reads_3", "GET", "/orders/3", user="ann", tables=("orders",))
    report = run(apps, "wrong_answer", layers="B", cases_for=only_reads, extra_cases=[extra])
    assert sorted(calls) == [
        "GET /orders",
        "GET /orders/{order_id}",
        "POST /orders/{order_id}/cancel",
    ]
    b_cases = [n for n, c in report.cases.items() if c["layer"] == "B"]
    assert "extra.ann_reads_3" in b_cases
    assert len(b_cases) == 5
    assert any(f.case == "extra.ann_reads_3" for f in report.findings)


def test_default_cases_cover_callers_rows_and_auth():
    from lattice.spec.check import CaseContext, spec_plans

    plans = {f"{p.method} {p.path}": p for p in spec_plans(SPEC)}
    seed = {t: [dict(zip(cols, r, strict=True)) for r in rows] for t, cols, rows in _seed_tables()}
    ctx = CaseContext(seed=seed, handles=("ann", "ben"), tables=("orders",))
    cases = cases_for(SPEC, plans["GET /orders/{order_id}"], ctx)
    paths = {(c.user, c.path) for c in cases}
    for user in ("ann", "ben"):
        for oid in ("1", "3", "4", "999999", "not-an-int"):
            assert (user, f"/orders/{oid}") in paths
    tokens = {c.token for c in cases if c.token is not None}
    assert tokens  # a garbage token
    assert any(c.user is None and c.token is None for c in cases)  # no token
    assert len({c.name for c in cases}) == len(cases)


def _seed_tables():
    cols = {
        "users": ("id", "name", "password", "token", "role", "is_active"),
        "products": ("id", "name", "price", "stock"),
        "orders": ("id", "user_id", "status", "total"),
        "order_items": ("id", "order_id", "product_id", "quantity", "price"),
    }
    return [(t, cols[t], rows) for t, rows in minishop.SEED.items()]


# ---- layer C ------------------------------------------------------------------------------


def test_layer_c_catches_an_invariant_broken_by_a_write(apps):
    report = run(apps, "total_drift")
    assert report.verdict == "fail"
    assert layers(report) == {"C"}, report.render()
    (f,) = report.findings
    assert f.rule == "An order's total is the sum of its lines"
    assert f.details["invariant"] == "order_total"
    assert f.case == "ann adds a line to a new order / step 1"
    assert f.endpoint == "POST /orders/10/items"
    (row,) = f.details["rows"]
    assert row["id"] == 10 and row["total"] == 0.0 and row["expected"] == 12.0
    assert f.verdict == "silent"


def test_layer_c_runs_after_layer_b_requests_too(apps, tmp_path):
    # cancelling also zeroes the order's total: B (if it had no program) would not see it
    edit = (
        '    order.status = "cancelled"\n',
        '    order.status = "cancelled"\n    order.total = 0.0\n',
    )
    app_dir, package = minishop.write_app(tmp_path, "zeroed", ("routes.py", *edit))
    report = check(
        SPEC, minishop.app_factory(package), minishop.build_db, layers="BC", login=minishop.login
    )
    c = [f for f in report.findings if f.layer == "C"]
    assert c and all(f.details["invariant"] == "order_total" for f in c)
    assert any(f.case.startswith("POST /orders/{order_id}/cancel") for f in c)


def test_layer_c_reports_a_seed_violation_once(apps, tmp_path):
    def bad_seed(path: Path) -> Path:
        import sqlite3

        minishop.build_db(path)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE products SET stock = -1 WHERE id = 2")
        conn.commit()
        conn.close()
        return path

    report = run(apps, "ok", layers="BC", db_factory=bad_seed)
    c = [f for f in report.findings if f.layer == "C"]
    assert [(f.details["invariant"], f.case) for f in c] == [("stock_non_negative", "(seed)")]


# ---- layer D ------------------------------------------------------------------------------


def test_layer_d_catches_a_wrong_database_effect(apps):
    report = run(apps, "wrong_quantity")
    assert report.verdict == "fail"
    assert layers(report) == {"D"}, report.render()
    (f,) = report.findings
    assert f.rule == "scenario: ann adds a line to a new order"
    assert f.case == "ann adds a line to a new order / step 1"
    problems = " ".join(f.details["problems"])
    assert "quantity" in problems
    assert f.details["actual"]["body"]["quantity"] == 1


def test_layer_d_checks_status_and_body_subsets(apps):
    scenario = Scenario(
        "wrong expectations",
        steps=(
            Step("GET", "/orders/1", user="ann", expect_status=200, expect_body={"status": "paid"}),
        ),
    )
    spec = replace(SPEC, scenarios=(scenario,))
    report = run(apps, "ok", layers="D", spec=spec)
    (f,) = report.findings
    assert any("body" in p for p in f.details["problems"])
    scenario = Scenario("status", steps=(Step("GET", "/orders/1", expect_status=200),))
    report = run(apps, "ok", layers="D", spec=replace(SPEC, scenarios=(scenario,)))
    (f,) = report.findings
    assert f.details["actual"]["status"] == 401


# ---- the report ---------------------------------------------------------------------------


def test_report_json_and_render(apps):
    report = run(apps, "total_drift", layers="CD")
    data = json.loads(report.to_json())
    assert data["verdict"] == "fail"
    assert data["counts"] == {"C": 1, "D": 0}
    (f,) = data["findings"]
    assert f["layer"] == "C" and f["invariant_violations"]
    assert set(f) >= {
        "layer",
        "verdict",
        "rule",
        "endpoint",
        "case",
        "request",
        "details",
        "db_diff",
        "invariant_violations",
        "code",
        "file",
        "line",
    }
    text = report.render()
    assert text.startswith("FAIL")
    assert text.index("An order's total is the sum of its lines") < text.index("POST /orders/10")


def test_an_invalid_spec_is_an_error(apps):
    bad = replace(SPEC, invariants=(Invariant("x", "x", "Nope.col > 0"),))
    with pytest.raises(CheckError):
        run(apps, "ok", spec=bad)


# ---- the CLI ------------------------------------------------------------------------------


def cli(apps, variant: str, *extra: str) -> list[str]:
    app_dir, package = apps[variant]
    return [
        "check",
        "--spec",
        "tests.spec.minishop:SPEC",
        "--app",
        f"{package}.main:make_app",
        "--db",
        "tests.spec.minishop:build_db",
        "--login",
        "tests.spec.minishop:login",
        "--app-dir",
        str(app_dir),
        "--package",
        package,
        *extra,
    ]


def test_cli_passes_the_correct_app(apps, capsys):
    assert main(cli(apps, "ok")) == 0
    assert capsys.readouterr().out.startswith("PASS")


def test_cli_fails_a_broken_app_with_json(apps, capsys):
    assert main(cli(apps, "wrong_quantity", "--json", "--layers", "D")) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "fail" and data["counts"] == {"D": 1}


def test_cli_errors_exit_2(apps, capsys):
    assert main(["check", "--spec", "tests.spec.minishop:NOPE", "--app", "x:y", "--db", "x:y"]) == 2
    assert "error" in capsys.readouterr().err
    assert main(cli(apps, "ok", "--layers", "AQ")) == 2
