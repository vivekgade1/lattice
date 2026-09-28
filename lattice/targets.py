"""Semantics v1.1 and target capabilities (spec 2026-09-28 go-target §3).

One meaning for every target: the rules below are implemented by each emitter
(`lattice.t2.emit_py`, `lattice.gogen`). A target that cannot run a construct says so
here, and `lattice.check.p8_target` rejects the program for it before code is generated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lattice.runtime import LatticeInternalError

SEMANTICS_VERSION = "1.1"
INT_MIN = -(1 << 63)
INT_MAX = (1 << 63) - 1

_ALWAYS = ("proc:", "record:", "helper:")

# T2 call targets (CheckedProcedure.targets values) the Go emitter implements. Grows as
# the Go runtime gains them (plan 5a Tasks 8-10); anything else is L892 on Go.
GO_T2_CALLS: set[str] = set()


@dataclass(frozen=True)
class Target:
    name: str
    natives: bool
    tabular: bool
    t2_calls: frozenset[str] | None  # None: every call target the checker accepts


def _go() -> Target:
    return Target("go", natives=False, tabular=False, t2_calls=frozenset(GO_T2_CALLS))


def target_named(name: str) -> Target:
    if name == "python":
        return TARGETS["python"]
    if name == "go":
        return _go()  # rebuilt: GO_T2_CALLS may have grown since import
    raise LatticeInternalError(f"unknown target {name!r} (known: python, go)")


def supports_call(target: Target, call: str) -> bool:
    if call.startswith(_ALWAYS):
        return True
    return target.t2_calls is None or call in target.t2_calls


TARGETS: Mapping[str, Target] = MappingProxyType(
    {"python": Target("python", natives=True, tabular=True, t2_calls=None), "go": _go()}
)
