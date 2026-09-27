"""Implicit-requirements condition (spec 2026-09-27 §5.6).

The same ownership and efficiency tasks, with the org-policy sentences (ownership /
visibility rules, the page-size cap) removed from the ticket and the same hidden
cases -- except that where the implicit spec no longer fixes a detail (403 vs 404 for
another customer's row) the case accepts either. Explicit mode is unchanged.

The per-solution fairness proofs (fastapi_correct / lattice correct, fastapi_naive
still caught) live in tests/test_api_solutions.py next to the explicit ones.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from bench.api_datasets import TOKENS
from bench.api_model import ApiTask, HttpCase
from bench.api_runner import _case_dict, evaluate_case, evaluate_http_case
from bench.api_tasks import API_TASKS, IMPLICIT_FAMILIES, OWNERSHIP_PROBE_STATUSES
from bench.report import read_outcomes, render_markdown_api, write_outcomes
from bench.runners import TaskOutcome

TASKS = {t.id: t for t in API_TASKS}
IMPLICIT = [t for t in API_TASKS if t.implicit_spec is not None]
IMPLICIT_IDS = [t.id for t in IMPLICIT]
SOLUTIONS = Path(__file__).resolve().parents[1] / "fixtures" / "api"

# Policy phrasing that must not survive in an implicit spec.
POLICY_PHRASES = (
    "their own",
    "another customer",
    "behaves exactly like",
    "left unchanged",
    "admin caller",
    "any customer's",
    "every customer's",
    "queries",
    "query budget",
    "per-row",
    "per row",
    "n+1",
    "at most",
    "integer 1–100",  # the page-size cap (string lengths like `line1` 1–100 stay)
    "le=100",
)


def _backticked(text: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", text))


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


# ------------------------------------------------------------------ model --


def test_defaults_keep_explicit_tasks_unchanged() -> None:
    case = HttpCase(
        name="n",
        kind="happy",
        family="control",
        method="GET",
        path="/",
        token=None,
        expect_status=200,
    )
    assert case.expect_status_any is None
    task = ApiTask("x01", "control", "GET / -- spec.", (case,))
    assert task.implicit_spec is None and task.implicit_cases is None
    view = task.implicit_view()
    assert view.spec == task.spec and view.cases == task.cases and view.id == task.id


def test_implicit_view_swaps_spec_and_cases_only() -> None:
    task = TASKS["o01"]
    view = task.implicit_view()
    assert view.spec == task.implicit_spec
    assert view.cases == task.implicit_cases
    assert (view.id, view.family, view.tier) == (task.id, task.family, task.tier)
    # the view is final: viewing it again changes nothing
    assert view.implicit_view() == view


# ------------------------------------------------------------- evaluation --


def _dict(**kw: object) -> dict:
    base = dict(
        name="t",
        kind="trap",
        family="ownership",
        method="GET",
        path="/orders/8",
        token="x",
        expect_status=404,
    )
    base.update(kw)
    return _case_dict(HttpCase(**base))  # type: ignore[arg-type]


def test_expect_status_any_accepts_each_listed_status() -> None:
    case = _dict(expect_status_any=(403, 404))
    for status in (403, 404):
        assert evaluate_http_case(case, status, {"error": "x"}, 1, None, "") == (True, "")
    passed, reason = evaluate_http_case(case, 200, {"id": 8}, 1, None, "")
    assert not passed and "status 200" in reason


def test_expect_status_any_keeps_the_other_expectations() -> None:
    case = _dict(
        expect_status_any=(403, 404), after_sql="SELECT 1", after_expect=[1], max_queries=2
    )
    assert evaluate_http_case(case, 403, None, 3, [[1]], "")[0] is False  # too many queries
    assert evaluate_http_case(case, 403, None, 1, [[2]], "")[0] is False  # row changed
    assert evaluate_http_case(case, 403, None, 1, [[1]], "") == (True, "")
    assert evaluate_http_case(case, 403, None, 1, [[1]], "boom")[0] is False  # crash


@pytest.mark.parametrize(
    "status,body,queries,after,crash",
    [
        (404, None, 1, None, ""),
        (403, {"error": "forbidden"}, 1, None, ""),
        (200, {"id": 8}, 1, None, ""),
        (500, None, 0, None, "Traceback"),
    ],
)
def test_explicit_cases_evaluate_exactly_as_before(status, body, queries, after, crash) -> None:
    for task in API_TASKS:
        for case in task.cases:
            data = _case_dict(case)
            assert data["expect_status_any"] is None
            assert evaluate_http_case(data, status, body, queries, after, crash) == evaluate_case(
                data, status, body, queries, after, crash
            )


def test_explicit_cases_serialize_as_before_plus_a_null_field() -> None:
    data = _case_dict(TASKS["o01"].cases[0])
    assert data.pop("expect_status_any") is None
    assert set(data) == {f.name for f in dataclasses.fields(HttpCase)} - {"expect_status_any"}
    json.dumps(_case_dict(TASKS["o01"].implicit_cases[-1]))  # tuples serialize


# ------------------------------------------------------------ task suite --


def test_exactly_the_ownership_and_efficiency_tasks_have_an_implicit_spec() -> None:
    assert IMPLICIT_FAMILIES == ("ownership", "efficiency")
    assert sorted(IMPLICIT_IDS) == sorted(t.id for t in API_TASKS if t.family in IMPLICIT_FAMILIES)
    assert len(IMPLICIT_IDS) == 16
    for task in API_TASKS:
        if task.family not in IMPLICIT_FAMILIES:
            assert task.implicit_spec is None and task.implicit_cases is None, task.id
            assert task.implicit_view() == task


@pytest.mark.parametrize("task", IMPLICIT, ids=IMPLICIT_IDS)
def test_implicit_spec_drops_the_policy_sentences(task: ApiTask) -> None:
    spec = task.implicit_spec
    assert spec is not None and spec != task.spec
    lowered = spec.lower()
    for phrase in POLICY_PHRASES:
        assert phrase not in lowered, (task.id, phrase)
    assert "  " not in spec  # whitespace-normalized like the explicit specs


@pytest.mark.parametrize("task", IMPLICIT, ids=IMPLICIT_IDS)
def test_implicit_spec_only_removes_never_adds(task: ApiTask) -> None:
    spec = task.implicit_spec or ""
    assert _words(spec) <= _words(task.spec), _words(spec) - _words(task.spec)
    assert len(spec) < len(task.spec)
    # every name the ticket gives (endpoint, parameters, response fields, statuses'
    # values like `pending`) is still given
    assert _backticked(spec) == _backticked(task.spec)
    for method_path in re.findall(r"\b(?:GET|POST|PUT|PATCH|DELETE) /[\w/{}-]*", task.spec):
        assert method_path in spec


@pytest.mark.parametrize("task", IMPLICIT, ids=IMPLICIT_IDS)
def test_implicit_spec_keeps_every_status_the_cases_expect(task: ApiTask) -> None:
    spec = task.implicit_spec or ""
    for case in task.implicit_cases or ():
        for status in case.expect_status_any or (case.expect_status,):
            if status in (201, 204, 409, 422, 401):
                assert str(status) in spec, (case.name, status)
        if case.expect_status_any is None and case.expect_status == 404:
            assert "404" in spec, case.name
    for case in task.implicit_cases or ():
        for key in _keys(case.expect_json) - {"items", "limit", "offset"}:
            assert f"`{key}`" in spec, (case.name, key)


def _keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            keys.add(key)
            keys |= _keys(inner)
    elif isinstance(value, list | tuple):
        for inner in value:
            keys |= _keys(inner)
    return keys


@pytest.mark.parametrize("task", IMPLICIT, ids=IMPLICIT_IDS)
def test_implicit_cases_are_the_same_cases_with_only_the_tolerance_changed(task: ApiTask) -> None:
    implicit = task.implicit_cases
    assert implicit is not None and len(implicit) == len(task.cases)
    for explicit, case in zip(task.cases, implicit, strict=True):
        if case.expect_status_any is None:
            assert case == explicit, case.name
            continue
        # a customer probing another customer's row: 403 or 404, same DB check
        assert case.expect_status_any == OWNERSHIP_PROBE_STATUSES == (403, 404)
        assert explicit.expect_status == 404 and explicit.expect_json is None
        assert case.token != TOKENS["admin"]  # never the admin
        assert dataclasses.replace(case, expect_status_any=None) == explicit


def test_every_ownership_trap_tolerates_403_or_404() -> None:
    for task in IMPLICIT:
        for case in task.implicit_cases or ():
            if task.family == "ownership" and case.kind == "trap":
                assert case.expect_status_any == (403, 404), (task.id, case.name)
    tolerant = {
        (t.id, c.name) for t in IMPLICIT for c in t.implicit_cases or () if c.expect_status_any
    }
    assert ("f07", "not_mine") in tolerant  # a happy case that probes another's order
    assert all(t.startswith("o") or t == "f07" for t, _ in tolerant)


def test_efficiency_budgets_and_page_bounds_are_still_expected() -> None:
    for task in IMPLICIT:
        if task.family != "efficiency":
            continue
        for explicit, case in zip(task.cases, task.implicit_cases or (), strict=True):
            assert case.max_queries == explicit.max_queries
            assert case.expect_status == explicit.expect_status


# ---------------------------------------------------------- 403 variant --

O01_FORBIDDEN = """\
# ruff: noqa: B008
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db


@app.get("/orders/{order_id}")
def get_order(order_id: int = Path(ge=1), user=Depends(current_user), db=Depends(get_db)):
    row = db.execute(
        "SELECT id, status, created_at, total, customer_id FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "order not found")
    if user["role"] != "Admin" and row["customer_id"] != user["id"]:
        raise HTTPException(403, "not your order")
    return {k: row[k] for k in ("id", "status", "created_at", "total")}
"""


def test_a_403_for_another_customers_order_is_correct_only_when_implicit(tmp_path: Path) -> None:
    import shutil

    from bench.api_datasets import build_db
    from bench.api_runner import classify, run_cases_fastapi

    pristine = tmp_path / "pristine.db"
    build_db(pristine)
    kit = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"

    def run(task: ApiTask, name: str):
        return run_cases_fastapi(
            O01_FORBIDDEN,
            tmp_path / name,
            task,
            db_builder=lambda p: shutil.copyfile(pristine, p),
            kit_path=kit,
        )

    assert classify(run(TASKS["o01"], "explicit")) == "silent"
    assert classify(run(TASKS["o01"].implicit_view(), "implicit")) == "correct"


# -------------------------------------------------------------- outcomes --


def _outcome(**kw: object) -> TaskOutcome:
    base = dict(
        task_id="o01",
        arm="A",
        tier="api",
        correct=True,
        check_passed=None,
        rounds=1,
        output_tokens=0,
        input_tokens=0,
        latency_s=0.0,
        failure=None,
        detail="",
        family="ownership",
    )
    base.update(kw)
    return TaskOutcome(**base)  # type: ignore[arg-type]


def test_condition_round_trips_and_old_jsonl_loads(tmp_path: Path) -> None:
    path = tmp_path / "o.jsonl"
    write_outcomes(path, [_outcome(condition="implicit")])
    (back,) = read_outcomes(path)
    assert back.condition == "implicit"
    old = json.loads(path.read_text())
    del old["condition"]
    path.write_text(json.dumps(old) + "\n")
    (back,) = read_outcomes(path)
    assert back.condition == ""


def test_report_header_shows_the_condition() -> None:
    explicit = render_markdown_api([_outcome()])
    implicit = render_markdown_api([_outcome(condition="implicit")])
    assert "Condition" not in explicit
    header = implicit.splitlines()[:4]
    assert header[0] == "# Lattice Benchmark Report — API suite"
    assert any(line.startswith("Condition: implicit requirements") for line in header)
    mixed = render_markdown_api([_outcome(), _outcome(arm="B", condition="implicit")])
    assert "Condition: mixed (explicit, implicit)" in mixed


# ------------------------------------------------------------------- CLI --


def _fenced(path: Path) -> str:
    return f"```python\n{path.read_text()}\n```\n"


def test_cli_implicit_offline_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import bench.cli

    offline = tmp_path / "offline"
    for arm, solution in (("A", "fastapi_correct.py"), ("B", "lattice.py")):
        (offline / arm).mkdir(parents=True)
        for task_id in ("o01", "f01"):
            (offline / arm / f"{task_id}.txt").write_text(_fenced(SOLUTIONS / task_id / solution))

    generators = []
    real = bench.cli._offline_generator

    def recording(offline_dir: Path, arm: str, task_id: str):
        gen = real(offline_dir, arm, task_id)
        generators.append((arm, task_id, gen))
        return gen

    monkeypatch.setattr(bench.cli, "_offline_generator", recording)
    out = tmp_path / "out"
    argv = ["run", "--suite", "api", "--implicit", "--arms", "A,B", "--tasks", "o01,f01"]
    assert bench.cli.main([*argv, "--offline", str(offline), "--out", str(out)]) == 0

    outcomes = read_outcomes(out / "outcomes.jsonl")
    assert sorted((o.arm, o.task_id) for o in outcomes) == [
        ("A", "f01"),
        ("A", "o01"),
        ("B", "f01"),
        ("B", "o01"),
    ]
    for o in outcomes:
        assert o.correct, (o.arm, o.task_id, o.failure, o.detail)
        assert o.condition == "implicit"
    # both arms were shown the implicit spec, never the explicit one
    assert len(generators) == 4
    for _, task_id, gen in generators:
        message = gen.calls[0][1][0]["content"]
        assert message.startswith(TASKS[task_id].implicit_spec)
        assert TASKS[task_id].spec not in message
    report = (out / "report.md").read_text()
    assert "Condition: implicit requirements" in report


def test_cli_implicit_is_api_only(tmp_path: Path) -> None:
    import bench.cli

    with pytest.raises(SystemExit):
        bench.cli.main(["run", "--suite", "seed", "--implicit", "--out", str(tmp_path)])
