"""The Python reference for client fixtures: the server prelude (lattice/api/codegen.py
_PRELUDE) run on wire cases, in the fixture format of lattice/gogen/rt/testdata. Imports
FastAPI; never imported by lattice.tsgen itself."""

from __future__ import annotations

import base64
import datetime as dt
import functools
import sys
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from starlette.datastructures import QueryParams

from lattice.api.codegen import _PRELUDE
from lattice.tsgen.regex import fullmatch_source

KIT_DIR = Path(__file__).resolve().parents[2] / "bench" / "api_kit"  # the prelude's platform_kit


@functools.cache
def prelude() -> dict[str, Any]:
    if str(KIT_DIR) not in sys.path:
        sys.path.append(str(KIT_DIR))
    ns: dict[str, Any] = {}
    exec(compile(_PRELUDE, "prelude", "exec"), ns)  # noqa: S102 - the reference itself
    return ns


def tag(v: object) -> dict[str, object]:
    p = prelude()
    if v is p["UNSET"]:
        return {"t": "unset"}
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
        return {"t": "datetime", "v": p["datetime_text"](v)}
    if isinstance(v, dt.date):
        return {"t": "date", "v": v.isoformat()}
    if isinstance(v, uuid.UUID):
        return {"t": "uuid", "v": str(v)}
    raise TypeError(type(v))


def _untag(d: dict[str, Any]) -> object:
    t = d["t"]
    if t == "unset":
        return prelude()["UNSET"]
    if t == "none":
        return None
    if t == "int":
        return int(d["v"])
    if t == "float":
        return float(d["v"])
    if t == "date":
        return dt.date.fromisoformat(d["v"])
    if t == "datetime":
        return dt.datetime.fromisoformat(d["v"])
    return d["v"]


def to_param(d: dict[str, Any]) -> object:
    kw = {k: v for k, v in d.items() if k not in ("name", "source", "scalar", "default")}
    if "one_of" in kw:
        kw["one_of"] = tuple(kw["one_of"])
    if "default" in d:
        kw["default"] = _untag(d["default"])
    return prelude()["Param"](d["name"], d["source"], d["scalar"], **kw)


def case(
    name: str,
    specs: Sequence[dict[str, object]],
    *,
    path: dict[str, str] | None = None,
    query: str = "",
    body: bytes = b"",
    form: bool = False,
    ctype: str | None = None,
    rules: Iterable[str] = (),
) -> dict[str, object]:
    """A wire case: `query` is the raw query string as latin-1 text."""
    return {
        "name": name,
        "specs": list(specs),
        "path": path or {},
        "query": query,
        "body": base64.b64encode(body).decode(),
        "form": form,
        "ctype": ctype,
        "rules": list(rules),
    }


def _body_text(status: int, code: str, detail: object, style: str) -> str:
    p = prelude()
    p["ERROR_BODY"] = style
    try:
        return p["json_error"](status, code, detail).body.decode("utf-8")
    finally:
        p["ERROR_BODY"] = "lattice"


def answer(c: dict[str, Any]) -> dict[str, Any]:
    """`c` with the server's answer: `values` and `order`, or `status`, `lattice`, `fastapi`."""
    p = prelude()
    params = [to_param(s) for s in c["specs"]]
    query = dict(QueryParams(c["query"].encode("latin-1").decode("latin-1")))
    body = base64.b64decode(c["body"])
    out = dict(c)
    try:
        values = p["parse_params"](params, c["path"], query, body, c["form"], c["ctype"])
        out["values"] = {k: tag(v) for k, v in values.items()}
        out["order"] = list(values)
    except p["ErrorResponse"] as err:
        out["status"] = err.status
        out["lattice"] = _body_text(err.status, err.code, err.detail, "lattice")
        out["fastapi"] = _body_text(err.status, err.code, err.detail, "fastapi")
    return out


def pattern_map(*case_lists: Iterable[dict[str, Any]]) -> dict[str, str]:
    """{python pattern: JS source} for every pattern in the cases' specs."""
    out: dict[str, str] = {}
    for cases in case_lists:
        for c in cases:
            for s in c["specs"]:
                if "pattern" in s:
                    out[s["pattern"]] = fullmatch_source(s["pattern"])
    return dict(sorted(out.items()))
