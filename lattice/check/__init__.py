"""check_source(): parse, then run checker passes P1-P5, collecting every diagnostic."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field

from lattice.check import p1_nodes, p2_names, p3_canon, p4_types, p5_effects, p6_api, p7_backend
from lattice.check.api_info import EndpointInfo
from lattice.check.astutil import MAX_DEPTH
from lattice.check.context import CheckContext
from lattice.diagnostics import Diagnostic, has_errors, sort_key
from lattice.parse import parse, too_deep
from lattice.profile import Profile
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


@dataclass(frozen=True)
class CheckResult:
    program: CheckedProgram | None
    diagnostics: tuple[Diagnostic, ...]

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


def check_source(source: str, profile: Profile) -> CheckResult:
    """Never raises: pathological nesting yields a single L003 diagnostic."""
    try:
        return _check_source(source, profile)
    except RecursionError:
        return CheckResult(None, (too_deep(),))


def _check_source(source: str, profile: Profile) -> CheckResult:
    parsed = parse(source)
    if parsed.module is None:
        return CheckResult(None, _sorted(list(parsed.diagnostics)))
    module = parsed.module
    if _depth(module) > MAX_DEPTH:
        return CheckResult(None, (too_deep(),))
    ctx = CheckContext(profile, diagnostics=list(parsed.diagnostics))

    before = ctx.error_count()
    p1_nodes.run(module, ctx)
    if ctx.error_count() > before:
        # Later passes assume P1's shape guarantees; stop to avoid cascades.
        return CheckResult(None, _sorted(ctx.diagnostics))

    p2_names.run(module, ctx)
    p3_canon.run(module, ctx)
    steps = p4_types.run(module, ctx)
    p5_effects.run(module, ctx)
    endpoints = p6_api.run(module, ctx)
    p7_backend.run(module, ctx)

    diags = _sorted(ctx.diagnostics)
    if has_errors(diags):
        return CheckResult(None, diags)
    program = CheckedProgram(
        module, parsed.source, dict(ctx.types), dict(steps), profile, endpoints
    )
    return CheckResult(program, diags)
