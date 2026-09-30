"""An endpoint parameter as the fixture-format dict every client tool shares (spec
2026-09-29 ui-a §4): the keys of the prelude's Param, exactly as codegen._param emits it."""

from __future__ import annotations

import ast
import datetime as dt
import functools
import math
from collections.abc import Sequence
from typing import Any

from lattice.api.codegen import _PRELUDE
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


# ---- what the client runtime needs beyond the Param keys ------------------------------------


@functools.cache
def _prelude_defs() -> dict[str, Any]:
    """The prelude's own `_num` and `EMAIL_MAX_LEN`, taken from its source (no FastAPI)."""
    tree = ast.parse(_PRELUDE)
    keep = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "_num")
        or (
            isinstance(node, ast.Assign | ast.AnnAssign)
            and "EMAIL_MAX_LEN" in ast.unparse(node).split("=")[0]
        )
    ]
    ns: dict[str, Any] = {}
    exec(compile(ast.Module(keep, []), "prelude", "exec"), ns)  # noqa: S102 - prelude source
    return ns


class BoundError(ValueError):
    """A bound the prelude cannot print (`_num`) or that is not a finite number."""


def tag_bound(v: object) -> dict[str, object]:
    """A bound exactly: an int bound is tagged int (the runtime holds it as a bigint and
    compares exactly), a float bound float (repr: the double itself)."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise LatticeInternalError(f"bound {v!r} is not a number")
    if isinstance(v, float):
        return {"t": "float", "v": repr(v)}
    return {"t": "int", "v": str(v)}


def ts_checks(d: dict[str, object]) -> dict[str, dict[str, object]]:
    """Each constraint of spec dict `d` with the 422 message the prelude's _constrain gives
    when it fails, formatted here at generation time exactly as the prelude formats it
    (`_num` for ge/le, repr() for one_of values and the pattern), so the client never
    formats a number or a repr (spec 2026-09-29 ui-a §10). `ge`/`le` also carry the bound
    exactly (tag_bound). Raises BoundError for a bound that is not finite or that `_num`
    cannot print (an int beyond a double's range: the server itself would answer 500)."""
    p = _prelude_defs()
    out: dict[str, dict[str, object]] = {}
    for key, op in (("ge", ">="), ("le", "<=")):
        if key not in d:
            continue
        v = d[key]
        if isinstance(v, float) and not math.isfinite(v):
            raise BoundError(f"the bound {v!r} is not finite")
        try:
            text = p["_num"](v)
        except OverflowError:
            bits = v.bit_length() if isinstance(v, int) else 0
            raise BoundError(f"an int bound of {bits} bits is beyond a double's range") from None
        out[key] = {"v": tag_bound(v), "msg": f"must be {op} {text}"}
    if "min_len" in d:
        out["min_len"] = {"msg": f"must have at least {d['min_len']} characters"}
    if "max_len" in d:
        out["max_len"] = {"msg": f"must have at most {d['max_len']} characters"}
    if "one_of" in d:
        values = d["one_of"]
        assert isinstance(values, list | tuple)
        out["one_of"] = {"msg": "must be one of " + ", ".join(repr(v) for v in values)}
    if d.get("email"):
        n = p["EMAIL_MAX_LEN"]
        out["email"] = {"v": n, "msg": f"must have at most {n} characters"}
    if "pattern" in d:
        msg = f"must match the pattern {d['pattern']!r}"
        out["pattern"] = {"msg": "must be an email address" if d.get("email") else msg}
    return out
