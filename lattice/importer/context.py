"""Prompt context for the LLM rungs (importer I5, spec §3.4 and §3.6): the evidence
layers S1-S6 rendered for one route, and their assembly per **method arm** M1-M6.

Arms: M1 = S1, M2 = S1 S2, M3 = S3 S4, M4 = S1 S2 S3, M5 = S4 S5 S6, M6 = S1-S6.

| Layer | Section |
|-------|---------|
| S1 | the route function's own source, its IR facts, the models it touches |
| S2 | the code-graph slice (callees, dependencies, models) and its effects and flags |
| S3 | the feature's intent notes, under the rule "code wins" |
| S4 | the route's OpenAPI endpoint and the schemas it names |
| S5 | the database's tables (columns, keys, unique, storage spellings) |
| S6 | the SQL the original issues per case of the route's suite, lifted where it can be |

Every arm also gets the **base** context: the route's identity (name, method, path),
the lifter output for the route if any (the Lattice under repair, or the fragments that
matched), and the diff history of earlier attempts. The generated profile and the
Lattice API guide are in the system prompt (:mod:`lattice.importer.llm_lift`), the same
for every route. An arm without S1/S2 (M3, M5) sees no source code at all.

:class:`Evidence` computes each layer lazily, once per import: the graph, the OpenAPI
document and the database facts are built on first use; S6 runs the route's cases
against the original under the SQL tracer.
"""

from __future__ import annotations

import ast
import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lattice.importer.graph import CodeGraph, build_graph, route_slice, slice_source

if TYPE_CHECKING:
    from lattice.importer.intent import IntentNotes
    from lattice.importer.lift import ImportResult, LiftResult
    from lattice.importer.outside import DbFacts, OpenApiFacts

__all__ = [
    "LAYERS",
    "METHODS",
    "Evidence",
    "Layer",
    "RouteContext",
    "Section",
    "assemble",
]

Layer = Literal["S1", "S2", "S3", "S4", "S5", "S6"]
LAYERS: tuple[Layer, ...] = ("S1", "S2", "S3", "S4", "S5", "S6")
METHODS: dict[str, frozenset[Layer]] = {
    "M1": frozenset({"S1"}),
    "M2": frozenset({"S1", "S2"}),
    "M3": frozenset({"S3", "S4"}),
    "M4": frozenset({"S1", "S2", "S3"}),
    "M5": frozenset({"S4", "S5", "S6"}),
    "M6": frozenset(LAYERS),
}
TITLES: dict[str, str] = {
    "S1": "Route source (S1)",
    "S2": "Code-graph slice (S2)",
    "S3": "Intent notes (S3)",
    "S4": "OpenAPI endpoint (S4)",
    "S5": "Database (S5)",
    "S6": "SQL trace of the original (S6)",
}
MAX_TRACE_CASES = 12


@dataclass(frozen=True)
class Section:
    title: str
    body: str
    layer: str | None = None  # None: base context

    def render(self) -> str:
        return f"## {self.title}\n\n{self.body.rstrip()}"


@dataclass(frozen=True)
class RouteContext:
    key: str
    method: str
    sections: tuple[Section, ...]

    @property
    def layers(self) -> frozenset[str]:
        return frozenset(s.layer for s in self.sections if s.layer is not None)

    def section(self, title: str) -> Section | None:
        return next((s for s in self.sections if s.title == title), None)

    def render(self) -> str:
        return "\n\n".join(s.render() for s in self.sections) + "\n"


def _fence(text: str, lang: str = "python") -> str:
    return f"```{lang}\n{text.rstrip()}\n```"


class Evidence:
    """The evidence layers of one import (see the module docstring). ``intent``: the S3
    notes (None: S3 renders as unavailable)."""

    def __init__(self, result: ImportResult, *, intent: IntentNotes | None = None) -> None:
        if result.context is None:
            raise ValueError("the import has no verification context")
        self.result = result
        self.ctx = result.context
        self.intent = intent
        self._graph: CodeGraph | None = None
        self._openapi: OpenApiFacts | None = None
        self._db: DbFacts | None = None
        self._seed: Path | None = None
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._cache: dict[tuple[str, str], str] = {}

    # -- shared inputs --

    @property
    def graph(self) -> CodeGraph:
        if self._graph is None:
            self._graph = build_graph(self.ctx.root, self.result.model.app_package,
                                      hints=self.ctx.frontend.graph_hints)  # fmt: skip
        return self._graph

    def _dir(self) -> Path:
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="lattice-evidence-")
        return Path(self._tmp.name)

    def seed_db(self) -> Path | None:
        """A seeded database (the full suite's seed plan)."""
        if self._seed is None:
            suite = self.ctx.full_suite()
            if suite is None:
                return None
            self._seed = Path(suite.db_factory("seed", self._dir() / "seed.db"))
        return self._seed

    def openapi(self) -> OpenApiFacts | None:
        """The original's OpenAPI facts (the runtime's reader); None when its framework
        has no OpenAPI document."""
        read = self.ctx.runtime.openapi
        if self._openapi is None and read is not None:
            app = self.ctx.app_factory(self._dir() / "openapi.db")
            self._openapi = read(app)
        return self._openapi

    def db(self) -> DbFacts | None:
        if self._db is None:
            from lattice.importer.outside import db_facts

            seed = self.seed_db()
            if seed is None:
                return None
            self._db = db_facts(seed)
        return self._db

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    # -- layers --

    def layer(self, layer: str, r: LiftResult) -> str:
        cached = self._cache.get((layer, r.key))
        if cached is None:
            render = {
                "S1": self.s1,
                "S2": self.s2,
                "S3": self.s3,
                "S4": self.s4,
                "S5": self.s5,
                "S6": self.s6,
            }[layer]
            cached = render(r)
            self._cache[(layer, r.key)] = cached
        return cached

    def _symbol_source(self, qual: str) -> str | None:
        sym = self.graph.symbols.get(qual)
        if sym is None:
            return None
        lines = self.graph.sources[sym.module].splitlines()
        return "\n".join(lines[sym.line - 1 : sym.end_line])

    def s1(self, r: LiftResult) -> str:
        """The route function (with its decorator), what the extractor read from it, and
        the table models it touches."""
        route = r.route
        src = self._symbol_source(f"{route.module}:{route.name}") or "(source not found)"
        params = ", ".join(f"{p.name} ({p.source}: {p.annotation})" for p in route.params)
        facts = [
            f"- module: `{route.module}`",
            f"- auth (from its dependencies): {route.auth} -- {route.auth_reason}",
            f"- parameters: {params or '(none)'}",
            f"- response model: {route.response_model}; status code: {route.status_code or 200}",
            f"- tables: {', '.join(route.tables) or '(none)'}",
        ]
        if route.owner_uses:
            uses = ", ".join(f"{u.table}.{u.column} ({u.how})" for u in route.owner_uses)
            facts.append(f"- owner columns compared with the caller: {uses}")
        parts = [_fence(src), "\n".join(facts)]
        models = [
            q
            for q, sym in self.graph.symbols.items()
            if sym.kind == "class" and sym.is_table and q.rsplit(":", 1)[-1] in route.tables
        ]
        for q in models:
            text = self._symbol_source(q)
            if text:
                parts.append(f"# {q}\n" + _fence(text))
        return "\n\n".join(parts)

    def s2(self, r: LiftResult) -> str:
        """The route's dependency slice (callees first) and its effects and flags."""
        s = route_slice(self.graph, f"{r.route.module}:{r.route.name}")
        facts = [
            f"- dependencies (Depends): {', '.join(s.dependencies) or '(none)'}",
            f"- models: {', '.join(s.models) or '(none)'}",
            f"- external calls: {', '.join(s.external) or '(none)'}",
        ]
        for fn, why in s.effects:
            facts.append(f"- effect: `{fn}`: {why}")
        for flag in s.dynamic:
            facts.append(f"- dynamic dispatch: `{flag.function}` {flag.kind}: {flag.detail}")
        return _fence(slice_source(s)) + "\n\n" + "\n".join(facts)

    def s3(self, r: LiftResult) -> str:
        from lattice.importer.intent import CODE_WINS

        if self.intent is None:
            return f"(no intent notes for this run)\n\n{CODE_WINS}"
        try:
            note = self.intent.for_route(r.route.module, r.route.name)
        except Exception as exc:  # a note that cannot be generated is not fatal
            return f"(intent notes unavailable: {exc})\n\n{CODE_WINS}"
        head = (
            f"LLM-written notes on the feature `{note.feature}`. They are claims, not "
            f"facts: {CODE_WINS} Behaviour is decided by the request suite, never by the "
            "notes."
        )
        return f"{head}\n\n{note.text.strip()}"

    def s4(self, r: LiftResult) -> str:
        api = self.openapi()
        if api is None:
            return f"(no OpenAPI document: {self.ctx.runtime.framework} apps have none)"
        try:
            e = api.endpoint(r.route.method, r.route.path)
        except KeyError:
            return f"(no OpenAPI endpoint {r.route.method} {r.route.path})"
        lines = [
            f"- {e.method} {e.path}"
            + (f" (operation `{e.operation}`)" if e.operation else "")
            + f", secured: {e.secured}",
            f"- responses: {', '.join(f'{s}: {m or "-"}' for s, m in sorted(e.responses.items()))}",
        ]
        for p in e.params:
            bounds = f" {dict(p.bounds)}" if p.bounds else ""
            default = f" default={p.default!r}" if p.default is not None else ""
            fmt = f"/{p.format}" if p.format else ""
            lines.append(
                f"- {p.location} parameter `{p.name}`: {p.type}{fmt}"
                f"{' required' if p.required else ''}{' nullable' if p.nullable else ''}"
                f"{default}{bounds}"
            )
        if e.body:
            lines.append(f"- request body: {e.body} ({e.body_media})")
        names = [n for n in (e.body, *e.responses.values()) if n]
        for name in dict.fromkeys(names):
            schema = api.schemas.get(name)
            if schema is None:
                continue
            lines.append(f"\nSchema `{name}`:")
            for prop in schema.properties:
                kind = prop.ref or f"{prop.type}{'/' + prop.format if prop.format else ''}"
                extra = [
                    "required" if prop.required else "",
                    "nullable" if prop.nullable else "",
                    f"default={prop.default!r}" if prop.default is not None else "",
                    str(dict(prop.bounds)) if prop.bounds else "",
                ]
                lines.append(f"  - {prop.name}: {kind} {' '.join(x for x in extra if x)}".rstrip())
        return "\n".join(lines)

    def s5(self, r: LiftResult) -> str:
        db = self.db()
        if db is None:
            return "(no seeded database)"
        lines = []
        for t in db.tables:
            lines.append(f"Table `{t.name}`:")
            for c in t.columns:
                bits = [c.type or "?", "NULL" if c.nullable else "NOT NULL"]
                if c.pk:
                    bits.append("PRIMARY KEY")
                if c.storage:
                    bits.append(f"stored as {c.storage}")
                lines.append(f"  - {c.name}: {' '.join(bits)}")
            for fk in t.foreign_keys:
                cols, refs = ", ".join(fk.columns), ", ".join(fk.ref_columns)
                lines.append(f"  - foreign key ({cols}) -> {fk.table}({refs})")
            for u in t.unique:
                lines.append(f"  - unique ({', '.join(u)})")
        return "\n".join(lines)

    def s6(self, r: LiftResult) -> str:
        """The statements the original runs per case (up to :data:`MAX_TRACE_CASES`),
        with the response status, and the Lattice steps they lift to where they do."""
        from lattice.importer.sql_lift import LiftContext, Lifted, lift_trace
        from lattice.importer.trace import case_bindings, trace_suite

        suite = self.ctx.suite_for(r.key)
        seed = self.seed_db()
        if suite is None or seed is None:
            return "(no request suite for this route: nothing to trace)"
        cases = [c for c in suite.cases if c.route == r.key][:MAX_TRACE_CASES]
        traces = trace_suite(self.ctx.app_factory, {"seed": seed}, cases, login=suite.login,
                             runtime=self.ctx.runtime)  # fmt: skip
        db = self.db()
        api = self.ctx.profile.api
        assert api is not None
        names = {v: k for k, v in api.table_names.items()} if api.table_names else {}
        lift_ctx = LiftContext(
            tables=names,
            columns={t.name: tuple(c.name for c in t.columns) for t in (db.tables if db else ())},
            owners={
                api.table_names.get(n, n): s.owner
                for n, s in api.tables.items()
                if getattr(s, "owner", None)
            },
            users_table=api.table_names.get(api.users_table or "", None)
            if api.users_table
            else None,
            null_order=api.null_order,
        )
        blocks = []
        for c in cases:
            trace = traces.get(c.name)
            if trace is None:
                continue
            head = f"### {c.name}: {c.method} {c.path}"
            if c.user:
                head += f" as {c.user}"
            if c.params:
                head += f" params={json.dumps(c.params, default=str)}"
            if c.json is not None:
                head += f" json={json.dumps(c.json, default=str)}"
            head += f" -> {trace.status}"
            statements = [(q.template or q.sql, q.params) for q in trace.queries()]
            caller = suite.plan.user(c.user).id if c.user else None
            bindings = case_bindings(c, r.route.path, caller=caller)
            lifted = lift_trace(statements, replace(lift_ctx, bindings=bindings))
            body = []
            for (sql, _), got in zip(statements, lifted, strict=True):
                body.append(sql.strip())
                if isinstance(got, Lifted):
                    body.append("-- lifts to: " + " | ".join(s.render() for s in got.steps))
                else:
                    body.append(f"-- not lifted: {got.reason}")
            blocks.append(head + "\n" + _fence("\n".join(body) or "-- no statements", "sql"))
        return "\n\n".join(blocks) or "(no traced cases)"


def partial_output(r: LiftResult) -> str | None:
    """The lifters' output for the route: the whole (unverified) endpoint, or the
    fragments that matched when they did not compose."""
    if r.lift.source is not None:
        return _fence(r.lift.source)
    if not r.lift.fragments:
        return None
    lines = [f"The deterministic lifters matched part of the route ({r.lift.reason}):"]
    for f in r.lift.fragments:
        steps = "; ".join(
            f"{s.name} = {s.op}({', '.join([*s.args, *(f'{k}={v}' for k, v in s.kwargs)])})"
            for s in f.steps
        )
        lines.append(f"- {f.lifter}: {steps or '(no steps)'}")
    return "\n".join(lines)


def assemble(
    evidence: Evidence,
    r: LiftResult,
    method: str,
    *,
    history: Sequence[str] = (),
    include_partial: bool = True,
) -> RouteContext:
    """The route's context for arm ``method`` (see the module docstring)."""
    if method not in METHODS:
        raise ValueError(f"unknown method arm {method!r}; one of {', '.join(METHODS)}")
    layers = METHODS[method]
    route = r.route
    identity = (
        f"Lift the route `{route.name}`: {route.method} {route.path}. Write one Lattice "
        f"`@endpoint` named `{route.name}` with exactly that method and path."
    )
    sections = [Section("Route", identity)]
    for layer in LAYERS:
        if layer in layers:
            sections.append(Section(TITLES[layer], evidence.layer(layer, r), layer))
    partial = partial_output(r) if include_partial else None
    if partial is not None:
        sections.append(Section("Lifter output", partial))
    if history:
        sections.append(Section("Earlier attempts", "\n\n".join(history)))
    return RouteContext(r.key, method, tuple(sections))


def ast_size(source: str) -> tuple[int, int]:
    """(lines, AST nodes) of a function's source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return len(source.splitlines()), 0
    return len(source.splitlines()), sum(1 for _ in ast.walk(tree))
