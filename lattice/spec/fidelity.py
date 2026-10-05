"""The fidelity suite of an imported app (Phase 2 plan 2026-10-05, §1.4; W3).

An app's ``fidelity.py`` records ``SCENARIOS`` (``lattice.spec.Scenario``), written from
its upstream README and tests and recorded against the running app on its own seed
(``seed.py``). :func:`run_fidelity` replays them through the app's runtime
(``lattice.importer.transport.AppRuntime``: the front end's transport, so a Django or
Flask app runs as it does under import) and checks, per step:

* **the answer**: the recorded status, and the recorded body as a subset
  (``lattice.spec.check.subset``);
* **the rule oracle**: the spec's contracts predict the step's *status class* --
  ``ok``, ``unauthenticated``, ``forbidden``, ``not_owned``, ``not_found``,
  ``invalid``, ``conflict``, ``method`` (no such route), ``error`` (a 5xx) -- from the
  caller alone (:func:`predict`): an anonymous caller of a non-Public route is
  unauthenticated, an inactive one unauthenticated or forbidden, a role the auth does
  not admit forbidden; an admitted caller gets ``ok`` or one of the route's stated
  errors' classes; a route the spec has no contract for answers ``method`` or
  ``not_found``. The answer's status must fall in one of the predicted classes
  (:func:`status_classes`).

The suite **passes** when the spec validates and every step passes both checks. The
seed module's conventions: ``build_seed_db(path)``, ``login_headers(client, handle)``
(request headers authenticating as a handle; a ``RuntimeError`` when the app refuses
the login), optional ``raw_credentials_headers(handle)`` (what a refused user sends
instead) and ``USER_IDS`` (handle -> users key: the caller's role and activity come
from its users row).
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.spec.model import Contract, Scenario, Spec, Step, resolve_table
from lattice.spec.validate import validate

__all__ = ["FidelityReport", "StepResult", "predict", "run_fidelity", "status_classes"]

CLASSES = ("ok", "unauthenticated", "forbidden", "not_owned", "not_found", "invalid",
           "conflict", "method", "error")  # fmt: skip
WHEN_CLASS = {
    "not_found": "not_found",
    "not_owned": "not_owned",
    "invalid": "invalid",
    "duplicate": "conflict",
    "unauthenticated": "unauthenticated",
    "inactive": "unauthenticated",
    "forbidden": "forbidden",
}


def status_classes(status: int) -> set[str]:
    """The classes a status may stand for (a 403 is a refusal, a role check or an owner
    check; a 404 a missing row or another user's)."""
    if 200 <= status < 300:
        return {"ok"}
    return {
        400: {"invalid", "conflict"},
        401: {"unauthenticated"},
        403: {"unauthenticated", "forbidden", "not_owned"},
        404: {"not_found", "not_owned", "method"},
        405: {"method"},
        409: {"conflict"},
        422: {"invalid"},
    }.get(status, {"error"} if status >= 500 else {"invalid"})


def _class_of_error(status: int | None, when: str) -> set[str]:
    """A stated error's classes: what its trigger means, and what its status shows."""
    if status is None:
        return set(CLASSES)
    return status_classes(status) | ({WHEN_CLASS[when]} if when in WHEN_CLASS else set())


def _match(contracts: Sequence[Contract], method: str, path: str) -> Contract | None:
    """The contract whose template matches ``path`` (literal segments first)."""
    found = []
    clean = path.split("?", 1)[0]
    for c in contracts:
        if c.method != method:
            continue
        pattern = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(c.path)) + "$"
        if re.match(pattern, clean):
            found.append(c)
    found.sort(key=lambda c: c.path.count("{"))
    return found[0] if found else None


@dataclass(frozen=True)
class Caller:
    """A scenario user as the oracle sees it: its role and whether it is active (None:
    anonymous)."""

    role: str | None
    active: bool = True


def framework_route(step: Step, routes: Sequence[tuple[str, str]]) -> bool:
    """Whether ``step`` calls one of the framework's routes the import set apart (each
    (methods -- comma-separated, ``*`` any --, path template; a template ending in ``/``
    after ``*`` is a prefix: Django admin))."""
    clean = step.path.split("?", 1)[0]
    for methods, path in routes:
        if methods != "*" and step.method not in methods.split(","):
            continue
        if methods == "*" and clean.startswith(path):
            return True
        pattern = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$"
        if re.match(pattern, clean):
            return True
    return False


def predict(
    spec: Spec, step: Step, caller: Caller | None, framework: Sequence[tuple[str, str]] = ()
) -> set[str]:
    """The status classes the spec's rule layer allows for ``step`` (see the module
    docstring); a framework route the import set apart (``framework``): any."""
    from lattice.spec.probes import allowed_roles, owner_scope, route_contracts

    api = spec.profile.api
    if framework_route(step, framework):
        return set(CLASSES)
    c = _match(route_contracts(spec), step.method, step.path)
    if c is None:
        return {"method", "not_found"}
    if c.auth is None:
        return set(CLASSES)
    public = c.auth == "Public"
    if caller is None and not public:
        return {"unauthenticated"}
    if caller is not None and not caller.active:
        return {"unauthenticated", "forbidden"} | ({"ok"} if public else set())
    roles = allowed_roles(c.auth, api) if api is not None else None
    if caller is not None and roles is not None and caller.role not in roles:
        return {"forbidden"}
    out = {"ok"}
    if owner_scope(spec, c) == "owner":
        out.add("not_owned")  # another user's row: the not-owned answer (403 or 404)
    for e in c.errors:
        out |= _class_of_error(e.status, e.when)
    return out


@dataclass
class StepResult:
    scenario: str
    index: int
    request: dict[str, Any]
    status: int
    expected: int
    answer_ok: bool
    predicted: list[str]
    oracle_ok: bool
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.answer_ok and self.oracle_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "step": self.index,
            "request": self.request,
            "status": self.status,
            "expected": self.expected,
            "answer_ok": self.answer_ok,
            "predicted": self.predicted,
            "oracle_ok": self.oracle_ok,
            "problems": self.problems,
        }


@dataclass
class FidelityReport:
    spec_problems: list[str]
    steps: list[StepResult]
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.spec_problems and all(s.ok for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        failed = [s.to_dict() for s in self.steps if not s.ok]
        return {
            "passed": self.passed,
            "spec_valid": not self.spec_problems,
            "spec_problems": self.spec_problems,
            "scenarios": len({s.scenario for s in self.steps}),
            "steps": len(self.steps),
            "answers_ok": sum(s.answer_ok for s in self.steps),
            "oracle_ok": sum(s.oracle_ok for s in self.steps),
            "failed": failed,
            "notes": self.notes,
        }


def _callers(spec: Spec, db: Path, user_ids: Mapping[str, Any]) -> dict[str, Caller]:
    """Each handle's role and activity, read from its users row in the seed."""
    from lattice.spec.probes import _role_of

    api = spec.profile.api
    if api is None or not user_ids:
        return {}
    found = resolve_table(spec.profile, api.users_table)
    if found is None:
        return {}
    _, sql, schema = found
    key = next(iter(schema.keys[0]), "id") if schema.keys else "id"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = {r[key]: dict(r) for r in con.execute(f'SELECT * FROM "{sql}"')}
    finally:
        con.close()
    out = {}
    for handle, uid in user_ids.items():
        row = rows.get(uid)
        if row is None:
            continue
        active = bool(row.get(api.active_column, True)) if api.active_column else True
        out[handle] = Caller(_role_of(api, schema, row), active)
    return out


def run_fidelity(
    spec: Spec,
    scenarios: Sequence[Scenario],
    app_factory: Callable[[Path], Any],
    build_seed: Callable[[Path], Any],
    headers_for: Callable[[Any, str], Mapping[str, str]],
    *,
    runtime: Any = None,
    raw_headers: Callable[[str], Mapping[str, str]] | None = None,
    user_ids: Mapping[str, Any] | None = None,
    framework: Sequence[tuple[str, str]] = (),
) -> FidelityReport:
    """Replay ``scenarios`` (see the module docstring): each on a fresh copy of the seed
    ``build_seed(path)`` writes, through ``runtime`` (default FastAPI's), as the users
    ``headers_for(client, handle)`` authenticates (``raw_headers(handle)`` for a user
    the app refuses to log in)."""
    from lattice.importer.transport import AppRuntime
    from lattice.spec.check import subset

    rt = runtime if runtime is not None else AppRuntime()
    problems = [p.render() for p in validate(spec)]
    report = FidelityReport(problems, [])
    if not scenarios:
        report.notes.append("no recorded scenarios: the suite is the spec's validity only")
        return report
    with tempfile.TemporaryDirectory(prefix="lattice-fidelity-") as name:
        tmp = Path(name)
        template = tmp / "template.db"
        built = build_seed(template)
        if isinstance(built, str | Path) and Path(built) != template:
            shutil.copyfile(built, template)
        callers = _callers(spec, template, user_ids or {})
        for n, scenario in enumerate(scenarios):
            db = tmp / f"scenario-{n}.db"
            shutil.copyfile(template, db)
            with rt.serve(app_factory, db) as (_, transport):
                headers: dict[str, dict[str, str]] = {}
                for i, step in enumerate(scenario.steps, 1):
                    report.steps.append(_step(spec, scenario, i, step, transport, headers,
                                              headers_for, raw_headers, callers, subset,
                                              framework))  # fmt: skip
    return report


def _step(spec: Spec, scenario: Scenario, i: int, step: Step, transport: Any,
          headers: dict[str, dict[str, str]], headers_for: Callable[..., Any],
          raw_headers: Callable[[str], Mapping[str, str]] | None,
          callers: Mapping[str, Caller], subset: Callable[[Any, Any], bool],
          framework: Sequence[tuple[str, str]] = ()) -> StepResult:  # fmt: skip
    sent: dict[str, str] = {}
    if step.user is not None:
        if step.user not in headers:
            try:
                headers[step.user] = dict(headers_for(transport.client, step.user))
            except RuntimeError:
                headers[step.user] = dict(raw_headers(step.user)) if raw_headers else {}
        sent = headers[step.user]
    answer = transport.request(step.method, step.path, params=step.params, json=step.json,
                               headers=sent)  # fmt: skip
    problems = []
    if answer.status != step.expect_status:
        problems.append(f"status {answer.status}, recorded {step.expect_status}")
    elif step.expect_body is not None and not subset(step.expect_body, answer.body):
        problems.append(f"body {str(answer.body)[:200]} lacks {step.expect_body!r}")
    answer_ok = not problems
    caller = None
    if step.user is not None:
        caller = callers.get(step.user, Caller(None))
    predicted = predict(spec, step, caller, framework)
    if caller is not None and caller.role is None and step.user not in callers:
        predicted = set(CLASSES)  # a user the seed module does not key: any class
    oracle_ok = bool(status_classes(answer.status) & predicted)
    if not oracle_ok:
        problems.append(f"the rule oracle predicts {sorted(predicted)}, not {answer.status}")
    request = {"method": step.method, "path": step.path, "user": step.user,
               "params": dict(step.params) if step.params else None, "json": step.json}  # fmt: skip
    return StepResult(scenario.name, i, request, answer.status, step.expect_status,
                      answer_ok, sorted(predicted), oracle_ok, problems)  # fmt: skip
