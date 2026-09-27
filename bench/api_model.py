"""API benchmark task model (spec 2026-09-27 §5.2).

``API_TASKS`` itself lives in ``bench/api_tasks.py``; ``bench.tasks.suite("api")``
loads it lazily.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

CaseKind = Literal["happy", "trap"]

# Every ApiTask outcome carries this tier, so reports can tell api runs apart.
API_TIER = "api"


@dataclass(frozen=True, kw_only=True)
class HttpCase:
    """One HTTP request against a fresh copy of the DB, and what must hold after it.

    ``expect_json`` is a subset match: every key it names must be present with a
    matching value (recursively); extra keys in the response are ignored. Lists
    (e.g. a Page's ``items``) must have the same length and match element-wise --
    in order only when ``ordered`` is set, otherwise as a multiset. Numbers match
    within a small tolerance; a bool never equals a number. ``max_queries`` bounds
    the SQL statements (SELECT/INSERT/UPDATE/DELETE/WITH/REPLACE) executed while
    serving the request, authentication included. ``after_sql`` is run against the
    DB after the request and its rows must equal ``after_expect`` (a list of rows;
    a bare scalar is a one-column row; row order significant only if ``ordered``).
    ``expect_status_any`` (implicit-requirements condition, spec §5.6), when set,
    accepts any of its statuses in place of ``expect_status``; every other
    expectation still applies. Keyword-only, so the field order can follow the spec.
    """

    name: str
    kind: CaseKind
    family: str
    method: str
    path: str
    token: str | None
    query: Mapping[str, object] = field(default_factory=dict)
    json: Mapping[str, object] | None = None
    expect_status: int
    expect_json: Mapping[str, object] | None = None
    ordered: bool = False
    max_queries: int | None = None
    after_sql: str | None = None
    after_expect: list | None = None
    expect_status_any: tuple[int, ...] | None = None


@dataclass(frozen=True)
class ApiTask:
    id: str
    family: str  # ownership | validation | errors | efficiency | control
    spec: str  # natural-language endpoint spec(s)
    cases: tuple[HttpCase, ...]
    # Implicit-requirements condition (spec §5.6): the same ticket without the
    # org-policy sentences, and the same cases with a status tolerance where that
    # spec no longer fixes one. None = use ``spec`` / ``cases``.
    implicit_spec: str | None = None
    implicit_cases: tuple[HttpCase, ...] | None = None

    def implicit_view(self) -> ApiTask:
        """The task as the implicit condition runs it: ``spec`` and ``cases``
        replaced by their implicit versions (when set). Everything downstream --
        both arms' task message, the case runners -- just uses the view."""
        return replace(
            self,
            spec=self.implicit_spec if self.implicit_spec is not None else self.spec,
            cases=self.implicit_cases if self.implicit_cases is not None else self.cases,
            implicit_spec=None,
            implicit_cases=None,
        )

    @property
    def tier(self) -> str:
        return API_TIER

    @property
    def happy_cases(self) -> tuple[HttpCase, ...]:
        return tuple(c for c in self.cases if c.kind == "happy")

    @property
    def trap_cases(self) -> tuple[HttpCase, ...]:
        return tuple(c for c in self.cases if c.kind == "trap")
