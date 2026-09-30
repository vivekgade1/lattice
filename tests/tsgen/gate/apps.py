"""The proof apps (spec 2026-09-29 ui-a §5.2): every API program in the codegen differential
registry, plus the FastAPI template."""

from __future__ import annotations

import dataclasses
import functools
from typing import Any

from bench.template_profile import template_plans
from bench.template_suite import API, TEMPLATE_SUITE
from lattice.api.ir import EndpointPlan, lower_api
from lattice.tsgen import compile_source_to_ts
from tests.api.test_codegen_differential import PROGRAMS, setup_of


@dataclasses.dataclass(frozen=True)
class Request:
    """A request from an existing suite: method, concrete path, query dict, JSON body."""

    method: str
    path: str
    query: dict[str, Any]
    json: Any
    form: dict[str, str] | None = None


@dataclasses.dataclass(frozen=True)
class App:
    name: str
    plans: tuple[EndpointPlan, ...]
    requests: tuple[Request, ...]


@functools.cache
def app(name: str) -> App:
    if name == "template":
        # the suite's paths are relative to the API prefix; the template's plans carry it
        reqs = tuple(
            Request(c.method, API + c.path, dict(c.params or {}), c.json, c.form)
            for c in TEMPLATE_SUITE
        )
        return App(name, tuple(template_plans()), reqs)
    source, task = PROGRAMS[name]
    result, files = compile_source_to_ts(source, setup_of(name).profile)
    assert files is not None, result.diagnostics
    plans = tuple(lower_api(result.program))
    reqs = tuple(Request(c.method, c.path, dict(c.query), c.json) for c in task.cases)
    return App(name, plans, reqs)


APP_NAMES = sorted(PROGRAMS) + ["template"]
