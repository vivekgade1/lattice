"""Tier reporting (spec 2026-09-27 lattice-t2-design §1, §5, node T2c): every endpoint
and procedure's strongest-guarantee tier.

| Tier | Form                                | Guarantees (weakest first below) |
|------|--------------------------------------|-----------------------------------|
| T0   | declarative ops only                 | strongest                        |
| T1   | `window`/`traverse`/`iterate`/`for_each` | termination by explicit bounds |
| T2   | calls a `@procedure`                 | static types, effects, fuel      |
| T3   | calls a native                       | signature and declared effects only |

A code unit's tier is the weakest reached anywhere in it: an endpoint that uses a T1
construct is T1 unless it also calls a procedure (T2), and a call graph that reaches a
native (directly, or through a chain of procedure calls) is T3 the whole way up.
Procedures are always at least T2 (that is what `@procedure` means); they are T3 only
when they (or something they call) call a native directly -- `for_each` already
carries its own native call at T1, so it does not count here.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

from lattice.catalog import FOR_EACH, ITERATE
from lattice.check.api_info import EndpointInfo
from lattice.check.t2 import T2Program
from lattice.check.t2_calls import is_proc_call

T0, T1, T2, T3 = "T0", "T1", "T2", "T3"
TIERS = (T0, T1, T2, T3)
_T1_OPS = frozenset({"window", "traverse", ITERATE, FOR_EACH})
_CALL_BUILTIN = "call"


def _procedure_tiers(t2: T2Program) -> dict[str, str]:
    """Every procedure's tier, propagated through its call graph: T3 when it (or a
    procedure it calls, transitively) calls a native directly, else T2."""
    tiers: dict[str, str] = {}

    def compute(name: str, path: frozenset[str]) -> str:
        if name in tiers:
            return tiers[name]
        proc = t2.procedures.get(name)
        if proc is None:
            return T2  # a call to an unknown name: P4 already reports it
        if proc.natives:
            tiers[name] = T3
            return T3
        result = T2
        for callee in proc.calls:
            if callee in path:  # a recursive cycle: does not itself imply T3
                continue
            if compute(callee, path | {name}) == T3:
                result = T3
        tiers[name] = result
        return result

    for name in t2.procedures:
        compute(name, frozenset())
    return tiers


def _called_procedures(fn: ast.FunctionDef, t2: T2Program) -> set[str]:
    """Procedure names `fn`'s body calls: `call(proc, ...)`, `for_each(t, proc, ...)`
    (an effectful procedure), and `proc(...)` as the whole value of a derive/where/
    require expression."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == _CALL_BUILTIN:
            if node.args and isinstance(node.args[0], ast.Name):
                names.add(node.args[0].id)
        elif node.func.id == FOR_EACH:
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
                if node.args[1].id in t2.procedures:
                    names.add(node.args[1].id)
        elif is_proc_call(node, t2):
            names.add(node.func.id)
    return names


def _uses_t1_ops(fn: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _T1_OPS
        for node in ast.walk(fn)
    )


def endpoint_tier(fn: ast.FunctionDef, t2: T2Program | None, proc_tiers: Mapping[str, str]) -> str:
    """One endpoint's tier: spec §5's tier table, read off its body."""
    procs = _called_procedures(fn, t2) if t2 is not None else set()
    if any(proc_tiers.get(p) == T3 for p in procs):
        return T3
    if procs:
        return T2
    if _uses_t1_ops(fn):
        return T1
    return T0


def compute_tiers(endpoints: Mapping[str, EndpointInfo], t2: T2Program | None) -> dict[str, str]:
    """Every endpoint's and procedure's tier (`CheckedProgram.tiers`)."""
    proc_tiers = _procedure_tiers(t2) if t2 is not None else {}
    tiers: dict[str, str] = dict(proc_tiers)
    for name, info in endpoints.items():
        tiers[name] = endpoint_tier(info.node, t2, proc_tiers)
    return tiers


def tier_summary(tiers: Mapping[str, str]) -> str:
    """A one-line tier mix for reports, e.g. `"T0: 3, T1: 1, T2: 1"` (omits a tier with
    no members)."""
    counts = dict.fromkeys(TIERS, 0)
    for tier in tiers.values():
        counts[tier] = counts.get(tier, 0) + 1
    return ", ".join(f"{t}: {counts[t]}" for t in TIERS if counts[t])
