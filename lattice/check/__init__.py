"""check_source(): parse, then run checker passes P1-P5, collecting every diagnostic."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from lattice.check import (
    list_consts,
    normalise,
    p1_nodes,
    p2_names,
    p3_canon,
    p4_types,
    p5_effects,
    p6_api,
    p7_backend,
    p8_target,
    t2,
)
from lattice.check.api_info import EndpointInfo
from lattice.check.astutil import MAX_DEPTH
from lattice.check.context import CheckContext
from lattice.check.t2 import T2Program
from lattice.check.tiers import compute_tiers
from lattice.check.tiers import tier_summary as _tier_summary
from lattice.diagnostics import Diagnostic, Span, has_errors, sort_key
from lattice.parse import parse, too_deep
from lattice.profile import Profile
from lattice.targets import target_named
from lattice.typesys import StepT, Type


@dataclass(frozen=True)
class CheckedProgram:
    module: ast.Module
    source: str
    types: Mapping[int, Type]
    steps: Mapping[str, StepT]
    profile: Profile
    # API profile: endpoint name -> metadata for lowering (empty for other programs)
    endpoints: Mapping[str, EndpointInfo] = field(default_factory=dict)
    # API profile: the @procedure / @record part (None when the program has none).
    # `module` then holds only the T0 statements; T2Program.nodes holds the rest.
    t2: T2Program | None = None
    # Every endpoint's and procedure's tier (spec 2026-09-27 lattice-t2-design §5):
    # name -> "T0" | "T1" | "T2" | "T3" (lattice.check.tiers).
    tiers: Mapping[str, str] = field(default_factory=dict)

    def tier_summary(self) -> str:
        """A one-line tier mix for reports, e.g. `"T0: 3, T1: 1, T2: 1"`."""
        return _tier_summary(self.tiers)


@dataclass(frozen=True)
class CheckResult:
    program: CheckedProgram | None
    diagnostics: tuple[Diagnostic, ...]
    # N1: every ordinary-Python spelling rewritten to canonical Lattice, as (kind, span of
    # the user's text); kinds are lattice.check.normalise's constants. Recorded even when
    # the program is refused (the benchmark counts how often the surface is used).
    normalisations: tuple[tuple[str, Span], ...] = ()

    @property
    def ok(self) -> bool:
        return self.program is not None


def _sorted(diags: list[Diagnostic]) -> tuple[Diagnostic, ...]:
    return tuple(sorted(diags, key=sort_key))


def _depth(module: ast.Module) -> int:
    deepest = 0
    stack: list[tuple[ast.AST, int]] = [(module, 0)]
    while stack:
        node, d = stack.pop()
        deepest = max(deepest, d)
        if deepest > MAX_DEPTH:
            break
        stack.extend((child, d + 1) for child in ast.iter_child_nodes(node))
    return deepest


def check_source(source: str, profile: Profile, *, target: str = "python") -> CheckResult:
    """Never raises: pathological nesting yields a single L003 diagnostic."""
    try:
        return _check_source(source, profile, target=target)
    except RecursionError:
        return CheckResult(None, (too_deep(),))


def _check_source(source: str, profile: Profile, *, target: str = "python") -> CheckResult:
    parsed = parse(source)
    if parsed.module is None:
        return CheckResult(None, _sorted(list(parsed.diagnostics)))
    module = parsed.module
    if _depth(module) > MAX_DEPTH:
        return CheckResult(None, (too_deep(),))
    ctx = CheckContext(profile, diagnostics=list(parsed.diagnostics))

    # T2: procedures, records (and their imports) are checked by their own checker;
    # the T0 passes see only the T0 statements.
    t0_body, t2_stmts = t2.split(module, api=profile.api is not None)
    # N1: ordinary Python spellings -> canonical Lattice, before P1 (never in T2 code)
    norm = normalise.normalise(t0_body, api=profile.api is not None)
    ctx.diagnostics.extend(norm.warnings)
    records = list(norm.records)
    t0_body = norm.body
    ctx.row_reads = norm.row_reads
    ctx.fresh = norm.fresh
    t2_program = t2.run(t2_stmts, t0_body, ctx) if t2_stmts else None
    ctx.t2 = t2_program
    if t2_stmts or norm.records or norm.row_reads:
        module = ast.Module(body=t0_body, type_ignores=module.type_ignores)

    def result(program: CheckedProgram | None) -> CheckResult:
        diags = [
            normalise.humanise(normalise.annotate(d, norm.macro_uses), norm.fresh)
            for d in ctx.diagnostics
        ]
        # a fix edits the source at the diagnostic's span; a column expression reported at
        # its use was written elsewhere (at its definition), so no edit is offered there
        diags = [
            replace(d, fix=None) if d.fix is not None and d.span in norm.macro_uses else d
            for d in diags
        ]
        if norm.macro_uses:  # a column expression used twice inside another: report once
            seen: set[Diagnostic] = set()
            diags = [
                d
                for d in diags
                if d.span not in norm.macro_uses or not (d in seen or seen.add(d))
            ]
        return CheckResult(program, _sorted(diags), tuple(records))

    before = ctx.error_count()
    p1_nodes.run(module, ctx)
    if ctx.error_count() > before:
        # Later passes assume P1's shape guarantees; stop to avoid cascades.
        return result(None)

    p2_names.run(module, ctx)
    before = ctx.error_count()
    list_consts.run(module, ctx)  # B6: select(cols=NAME) -> the list; NAME dropped
    if ctx.error_count() > before:
        return result(None)
    p3_canon.run(module, ctx)
    steps = p4_types.run(module, ctx)
    # N1: the `+` P4 typed as Str concatenation becomes fn.concat(a, b)
    records += normalise.resolve_concats(module, ctx.concats, ctx.types)
    p5_effects.run(module, ctx)
    endpoints = p6_api.run(module, ctx)
    p7_backend.run(module, ctx)
    p8_target.run(module, ctx, t2_program, target_named(target))

    if has_errors(ctx.diagnostics):
        return result(None)
    program = CheckedProgram(
        module,
        parsed.source,
        dict(ctx.types),
        dict(steps),
        profile,
        endpoints,
        t2_program,
        compute_tiers(endpoints, t2_program),
    )
    return result(program)
