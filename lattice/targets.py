"""Semantics v1.1 and target capabilities (spec 2026-09-28 go-target §3).

One meaning for every target: the rules below are implemented by each emitter
(`lattice.t2.emit_py`, `lattice.gogen`). A target that cannot run a construct says so
here, and `lattice.check.p8_target` rejects the program for it before code is generated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lattice.catalog import T2_METHODS, T2_STDLIB
from lattice.runtime import LatticeInternalError

SEMANTICS_VERSION = "1.1"
INT_MIN = -(1 << 63)
INT_MAX = (1 << 63) - 1

_ALWAYS = ("proc:", "record:", "helper:")

# T2 call targets (CheckedProcedure.targets values) the Go emitter implements. Grows as
# the Go runtime gains them (plan 5a Tasks 8-10); anything else is L892 on Go.
# Task 8: the core builtins. `str()` of a container stays L892 (`p8_target`); the error
# constructors are not listed because the checker refuses to build errors (L912).
GO_T2_CALLS: set[str] = {
    f"builtin:{name}"
    for name in (
        "len", "abs", "round", "min", "max", "sum", "sorted", "range", "enumerate", "zip",
        "reversed", "any", "all", "int", "float", "str", "list", "set", "dict",
    )
}  # fmt: skip
# Task 9: the Str methods, `re` and Match. The `string` constants are values, not call
# targets (listed for the record; the emitter copies every stdlib constant exactly).
GO_T2_CALLS |= {f"method:Str.{name}" for name in T2_METHODS["Str"]}
GO_T2_CALLS |= {f"std:re.{name}" for name in T2_STDLIB["re"]}
GO_T2_CALLS |= {f"method:Match.{name}" for name in T2_METHODS["Match"]}
GO_T2_CALLS |= {f"std:string.{name}" for name in T2_STDLIB["string"]}
# Task 10: the list, dict, set and Counter methods and collections.Counter/defaultdict.
GO_T2_CALLS |= {
    f"method:{kind}.{name}"
    for kind in ("list", "dict", "set", "Counter")
    for name in T2_METHODS[kind]
}
GO_T2_CALLS |= {f"std:collections.{name}" for name in T2_STDLIB["collections"]}
# Task 10: math, datetime (and the Date / DateTime / Duration methods) and json. The math
# constants are values, listed for the record (copied exactly, as the string ones).
GO_T2_CALLS |= {
    f"std:{mod}.{name}" for mod in ("math", "datetime", "json") for name in T2_STDLIB[mod]
}
GO_T2_CALLS |= {
    f"method:{kind}.{name}"
    for kind in ("Date", "DateTime", "Duration")
    for name in T2_METHODS[kind]
}

# The kinds of T2 value the Go emitter implements (`lattice.gogen.types.kind_of`): a
# procedure that holds a value of any other kind (Date, DateTime, Duration, Json, Row,
# Caller, Counter) is L892 on Go. Grows with GO_T2_CALLS (Task 10: the rest).
GO_T2_TYPES: set[str] = {
    "Int", "Float", "Str", "Bool", "Uuid", "None", "Error",
    "list", "set", "dict", "tuple", "Iter", "record",
    "Match", "module",  # Task 9 (a module: `re.search`, `string.digits`, `math.pi`)
    "Counter", "factory",  # Task 10 (a factory: `defaultdict(list)`'s argument)
    "Date", "DateTime", "Duration", "Json",  # Task 10
}  # fmt: skip


@dataclass(frozen=True)
class Target:
    name: str
    natives: bool
    tabular: bool
    t2_calls: frozenset[str] | None  # None: every call target the checker accepts
    t2_types: frozenset[str] | None = None  # None: every kind of T2 value


def _go() -> Target:
    return Target(
        "go",
        natives=False,
        tabular=False,
        t2_calls=frozenset(GO_T2_CALLS),
        t2_types=frozenset(GO_T2_TYPES),
    )


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


def supports_type(target: Target, kind: str) -> bool:
    """`kind` is a T2 value kind (`lattice.gogen.types.kind_of`: "Int", "list", ...)."""
    return target.t2_types is None or kind in target.t2_types


TARGETS: Mapping[str, Target] = MappingProxyType(
    {"python": Target("python", natives=True, tabular=True, t2_calls=None), "go": _go()}
)
