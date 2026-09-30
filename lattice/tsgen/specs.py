"""An endpoint parameter as the fixture-format dict every client tool shares (spec
2026-09-29 ui-a §4): the keys of the prelude's Param, exactly as codegen._param emits it."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from lattice.api.ir import EndpointPlan
from lattice.check.api_info import MISSING, UNSET, ParamSpec
from lattice.runtime import LatticeInternalError


def tag_default(v: object) -> dict[str, object]:
    """A default value, tagged as the fixtures tag parameter values."""
    if v is None:
        return {"t": "none"}
    if isinstance(v, bool):
        return {"t": "bool", "v": v}
    if isinstance(v, int):
        return {"t": "int", "v": str(v)}
    if isinstance(v, float):
        return {"t": "float", "v": repr(v)}
    if isinstance(v, str):
        return {"t": "str", "v": v}
    if isinstance(v, dt.datetime):
        utc = v.astimezone(dt.UTC).replace(tzinfo=None)
        return {"t": "datetime", "v": utc.isoformat(timespec="microseconds") + "Z"}
    if isinstance(v, dt.date):
        return {"t": "date", "v": v.isoformat()}
    raise LatticeInternalError(f"default {v!r} has no tag")


def spec_dict(spec: ParamSpec, null_ok: Sequence[str]) -> dict[str, object]:
    if spec.scalar is None:
        raise LatticeInternalError(f"parameter '{spec.name}' has no scalar type")
    d: dict[str, object] = {"name": spec.name, "source": spec.source, "scalar": spec.scalar.value}
    for key in ("ge", "le", "min_len", "max_len"):
        value = getattr(spec, key)
        if value is not None:
            d[key] = value
    if spec.one_of is not None:
        d["one_of"] = list(spec.one_of)
    if spec.pattern is not None:
        d["pattern"] = spec.pattern
    if spec.default is UNSET:
        d["default"] = {"t": "unset"}
    elif spec.default is not MISSING:
        d["default"] = tag_default(spec.default)
    if spec.optional:
        d["optional"] = True
        if spec.name in null_ok:
            d["nullable"] = True
    if spec.email:
        d["email"] = True
    return d


def plan_specs(plan: EndpointPlan) -> list[dict[str, object]]:
    """The request parameters (not `user: CurrentUser`), in plan order."""
    return [spec_dict(p, plan.null_ok) for p in plan.params if p.source != "user"]
