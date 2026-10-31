"""The workflow layer on ``tests/gocheck/fixtures/bodybind`` (node GD): a step sends the body
fields the code refuses when absent, and a route whose handler authenticates itself is called
as a user. Skipped when ``go`` is not on PATH."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from lattice.importer import workflows as W

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

FIXTURE = Path(__file__).parents[1] / "gocheck" / "fixtures" / "bodybind"


@pytest.fixture(scope="module")
def plan() -> W.Plan:
    from lattice.importer.frontends.go_gin import GoGinFrontEnd

    x: dict[str, Any] = GoGinFrontEnd().extract_json(FIXTURE, workflow=True)
    return W.plan(x, ["ann", "ben"])


def sent(plan: W.Plan, key: str) -> list[str]:
    return [p["name"] for p in W.required_fields(plan.view.by_key[key], None)]


def test_a_step_sends_the_fields_the_code_refuses_when_absent(plan):
    assert sent(plan, "POST /login") == ["email", "password"]
    assert sent(plan, "POST /apps") == ["name"]
    assert sent(plan, "POST /users") == ["email"]
    assert sent(plan, "POST /notes") == ["text"]
    assert sent(plan, "POST /members") == ["role"]
    assert sent(plan, "POST /tags") == ["label"]


def test_a_tag_required_field_is_still_sent(plan):
    assert sent(plan, "PUT /apps/{id}") == ["name"]


def test_a_handler_that_authenticates_itself_is_not_public(plan):
    assert not plan.view.by_key["GET /self"].public
    assert plan.view.by_key["GET /ping"].public
