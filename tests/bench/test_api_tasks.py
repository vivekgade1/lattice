"""Structure of the API task suite (bench/api_tasks.py): families, cases, specs, tokens."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from bench.api_datasets import TOKENS
from bench.api_model import ApiTask, HttpCase
from bench.api_tasks import API_TASKS, FAMILIES, QUERY_BUDGET, TRAP_FAMILIES
from bench.tasks import suite

TASK_IDS = [t.id for t in API_TASKS]
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
PAGE_KEYS = {"items", "limit", "offset"}
# A deliberately unknown token, used only to prove that bad tokens are rejected.
UNKNOWN_TOKENS = {"tok-nobody"}


def _endpoints(spec: str) -> list[tuple[str, re.Pattern[str]]]:
    """(method, path regex) for every `METHOD /path` the spec declares."""
    found = []
    for method, template in re.findall(r"\b(GET|POST|PUT|PATCH|DELETE) (/[\w/{}-]*)", spec):
        pattern = re.sub(r"\\\{\w+\\\}", r"[^/]+", re.escape(template))
        found.append((method, re.compile(pattern + "$")))
    return found


def _keys(value: object) -> set[str]:
    """Every object key named anywhere inside an expected JSON value."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            keys.add(key)
            keys |= _keys(inner)
    elif isinstance(value, list | tuple):
        for inner in value:
            keys |= _keys(inner)
    return keys


def test_forty_tasks_with_unique_ids():
    assert len(API_TASKS) == 40
    assert len(set(TASK_IDS)) == len(TASK_IDS)
    assert all(isinstance(t, ApiTask) for t in API_TASKS)


def test_eight_tasks_per_family():
    counts = Counter(t.family for t in API_TASKS)
    assert counts == dict.fromkeys(FAMILIES, 8)
    assert set(TRAP_FAMILIES) | {"control"} == set(FAMILIES)


def test_id_prefix_matches_family():
    prefix = {"ownership": "o", "validation": "v", "errors": "e", "efficiency": "f", "control": "c"}
    for task in API_TASKS:
        assert re.fullmatch(r"[a-z]\d\d", task.id), task.id
        assert task.id[0] == prefix[task.family], task.id


def test_suite_loader_returns_the_tasks():
    assert suite("api") == API_TASKS


@pytest.mark.parametrize("task", API_TASKS, ids=TASK_IDS)
def test_case_counts(task: ApiTask):
    assert len(task.happy_cases) >= 2
    if task.family in TRAP_FAMILIES:
        assert len(task.trap_cases) >= 1
    else:
        assert task.trap_cases == ()
    names = [c.name for c in task.cases]
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("task", API_TASKS, ids=TASK_IDS)
def test_cases_are_well_formed(task: ApiTask):
    for case in task.cases:
        assert isinstance(case, HttpCase)
        assert case.family == task.family
        assert case.kind in ("happy", "trap")
        assert case.method in METHODS
        assert 200 <= case.expect_status < 600
        if case.method in ("GET", "DELETE"):
            assert case.json is None, case.name
        if case.after_sql is not None:
            assert case.after_sql.lstrip().upper().startswith("SELECT"), case.name
            assert case.after_expect is not None, case.name
        if case.max_queries is not None:
            assert task.family == "efficiency" and case.kind == "trap", case.name
            assert case.max_queries == QUERY_BUDGET


@pytest.mark.parametrize("task", API_TASKS, ids=TASK_IDS)
def test_tokens_are_fixture_tokens(task: ApiTask):
    valid = set(TOKENS.values())
    for case in task.cases:
        if case.token in UNKNOWN_TOKENS:
            assert case.expect_status == 401, case.name
        else:
            assert case.token is None or case.token in valid, case.name


@pytest.mark.parametrize("task", API_TASKS, ids=TASK_IDS)
def test_every_case_hits_an_endpoint_the_spec_declares(task: ApiTask):
    endpoints = _endpoints(task.spec)
    assert 1 <= len(endpoints) <= 2, task.spec
    for case in task.cases:
        assert any(
            method == case.method and pattern.match(case.path) for method, pattern in endpoints
        ), f"{case.name}: {case.method} {case.path} is not declared in the spec"


@pytest.mark.parametrize("task", API_TASKS, ids=TASK_IDS)
def test_spec_names_every_response_field(task: ApiTask):
    for case in task.cases:
        for key in _keys(case.expect_json) - PAGE_KEYS:
            assert f"`{key}`" in task.spec, f"{case.name}: field {key!r} not named in the spec"
        if case.expect_json is not None and "items" in case.expect_json:
            assert '"items"' in task.spec and "`limit`" in task.spec


@pytest.mark.parametrize("task", API_TASKS, ids=TASK_IDS)
def test_spec_mentions_no_files_and_no_trap(task: ApiTask):
    lowered = task.spec.lower()
    for word in (
        ".py",
        "fixture",
        "platform_kit",
        "lattice",
        "fastapi",
        "trap",
        "naive",
        "n+1",
        "idor",
        "sqlite",
    ):
        assert word not in lowered, word
    assert "  " not in task.spec  # whitespace-normalized


@pytest.mark.parametrize("task", API_TASKS, ids=TASK_IDS)
def test_spec_states_auth_and_statuses(task: ApiTask):
    assert any(word in task.spec for word in ("Public:", "Admin only", "signed in"))
    for case in task.cases:
        if case.expect_status in (201, 204, 404, 409, 422):
            assert str(case.expect_status) in task.spec, (case.name, case.expect_status)
        if case.expect_status == 401:
            assert "401" in task.spec
        if case.expect_status == 403:
            assert "403" in task.spec


def test_efficiency_family_has_query_budgets_and_page_bounds():
    budgets = [t.id for t in API_TASKS if any(c.max_queries for c in t.cases)]
    assert len(budgets) >= 5
    bounds = [
        t.id
        for t in API_TASKS
        if t.family == "efficiency"
        and any(c.kind == "trap" and c.expect_status == 422 for c in t.cases)
    ]
    assert bounds  # at least one task traps an out-of-range limit


def test_validation_traps_check_that_nothing_was_written():
    for task in API_TASKS:
        if task.family != "validation":
            continue
        writes = [c for c in task.trap_cases if c.method != "GET"]
        assert all(c.expect_status == 422 and c.after_sql for c in writes), task.id
