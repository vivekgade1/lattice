"""Expectations no implementation can satisfy, found without running one.

Two defects of one kind stopped Streak's first build (2026-10-11): a scenario's
``expect_db`` asked for rows that nothing in the scenario puts there. An agent can only
give up on such a check. Two pure helpers, used by ``lint`` (L012) and by ``check`` (the
reason a failing ``expect_db`` gets):

* :func:`contradictions` -- a step expects *no* row of a table where W, after an earlier
  step expected one, and nothing in between can remove it (every later step is a read or
  a request the spec says is refused, ``expect_status >= 400``).
* :func:`unestablished` -- a failing step expects a row where W, and no step up to it can
  have put one there (no ``Await``, no signed-in step, no write expecting a 2xx), nor
  ``given`` row of that table.

Both rest on the spec's own premise that a refused request changes nothing, and on the
checker signing a user in only when a step runs as that user."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lattice.spec.model import Await, Rpc, Scenario, Step

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def asserts_row(cond: Mapping[str, Any]) -> bool:
    """Whether an ``expect_db`` entry says a row exists."""
    count = cond.get("count")
    return bool(cond.get("equals")) or (isinstance(count, int) and count >= 1)


def asserts_none(cond: Mapping[str, Any]) -> bool:
    return cond.get("count") == 0 and not cond.get("equals")


def removes_rows(step: Step | Await | Rpc) -> bool:
    """Whether, by its own expectation, a step may delete or change a row: an ``Await``
    (a service works meanwhile), a write the spec expects to succeed, or an ``Rpc`` that
    expects OK (the spec cannot tell a reading rpc from a writing one)."""
    if isinstance(step, Await):
        return True
    if isinstance(step, Rpc):
        return step.expect_code == "OK"
    return step.method.upper() not in READ_METHODS and step.expect_status < 400


def creates_rows(step: Step | Await | Rpc) -> bool:
    """Whether a step may put a row there: as :func:`removes_rows`, or a step run as a
    user (the checker signs her in, which writes the session rows)."""
    return removes_rows(step) or (isinstance(step, Step | Rpc) and step.user is not None)


@dataclass(frozen=True)
class Contradiction:
    table: str
    where: dict[str, Any]
    step_a: int  # 1-based: expects a row
    step_b: int  # 1-based: expects none


def _where_of(cond: Mapping[str, Any]) -> dict[str, Any]:
    return dict(cond.get("where") or {})


def contradictions(scenario: Scenario) -> list[Contradiction]:
    out: list[Contradiction] = []
    last: dict[tuple[str, str], int] = {}  # (table, where) -> step that expected a row
    for i, step in enumerate(scenario.steps, 1):
        if isinstance(step, Await):
            last.clear()
            for table, cond in step.expect_db.items():
                if asserts_row(cond):
                    last[(table, repr(sorted(_where_of(cond).items())))] = i
            continue
        if removes_rows(step):
            last.clear()
        for table, cond in (step.expect_db or {}).items():
            key = (table, repr(sorted(_where_of(cond).items())))
            if asserts_none(cond) and key in last:
                out.append(Contradiction(table, _where_of(cond), last[key], i))
            elif asserts_row(cond):
                last[key] = i
    return out


def unestablished(
    scenario: Scenario,
    index: int,
    expect_db: Mapping[str, Any],
    problems: Sequence[str],
) -> list[str]:
    """Hints for the failing ``expect_db`` of step `index` (1-based) of `scenario`:
    one line per table whose expected row is missing (`problems` says "found 0" or "no
    row"), nothing can have created, and the scenario's ``given`` does not hold."""
    steps = scenario.steps[:index]
    if any(creates_rows(s) for s in steps):
        return []
    given = scenario.given or {}
    lines: list[str] = []
    for table, cond in expect_db.items():
        if not asserts_row(cond) or given.get(table):
            continue
        prefix = f"db {table}"
        missing = any(
            p.startswith(prefix) and ("found 0" in p or ": no row" in p) for p in problems
        )
        if not missing:
            continue
        where = _where_of(cond)
        what = f"{table} where {where}" if where else table
        lines.append(
            f"spec problem: nothing before this step puts a row in {what} (no earlier "
            "request that succeeds and writes, no Await, no step run as a signed-in user, "
            "no given row), so no implementation can satisfy this check: correct the "
            "scenario, not the code"
        )
    return lines
