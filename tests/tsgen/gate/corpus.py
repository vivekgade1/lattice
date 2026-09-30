"""Request corpora for the gate (spec 2026-09-29 ui-a §5.3): (a) existing suites' requests,
(b) boundary values from each parameter spec, (c) 200 seeded random requests per endpoint.
Wire cases (L2) are answered by the server prelude; typed cases (L3) are sent by the client."""

from __future__ import annotations

import json
import math
import random
import re
import urllib.parse
import zlib
from typing import Any

from starlette.routing import compile_path

from lattice.api.codegen import _specificity
from lattice.api.ir import EndpointPlan
from lattice.tsgen.pyref import answer, case, pattern_map, prelude, to_param
from lattice.tsgen.specs import plan_specs
from tests.tsgen.gate.apps import App

RANDOM_PER_ENDPOINT = 200


def R_A(value: object) -> bool:
    """A path value the client cannot route faithfully (decision log: path `Str`)."""
    return isinstance(value, str) and (value in ("", ".", "..") or "/" in value)


# Candidate raw values per scalar: text form for path/query/form, JSON form for bodies.
TEXT = {
    "Int": ["0", "1", "-1", "5", " 5", "\x1c5", "5.0", "1e3", "abc", "", "9007199254740993",
            "9223372036854775807", "9223372036854775808", "-9223372036854775809", "+7", "007"],
    "Float": ["0", "1.5", "-2.5", ".5", "5.", "nan", "inf", "1e16", "1e400", "1_0", "\x1c1.5"],
    "Str": ["", "a", "ab", "abc", "a\n", "é", "👋", "👋👋👋", " x ", "K", "A1_",
            "x@y.z", "it's5"],
    "Bool": ["true", "TRUE", "yes", "on", "1", "0", "off", "no", "maybe", "", "\x1ctrue"],
    "Date": ["2026-09-28", "20260928", "2026-W01-1", "2026-02-30", "", "2026-09-28T00:00"],
    "DateTime": ["2026-09-28T10:11:12", "2026-09-28T10:11:12Z", "2026-09-28T10:11:12+01:00",
                 "2026-09-28T24:00", "2026-09-28 10:00", "20260928T101112", "bad",
                 "0001-01-01T00:00:00+01:00", "2026-09-28T10:11:12.1234567"],
    "Uuid": ["0123456789abcdef0123456789abcdef", "01234567-89AB-CDEF-0123-456789ABCDEF",
             "01234567-89abcdef-0123-456789abcdef", "nope", ""],
}  # fmt: skip
JSON_EXTRA: dict[str, list[Any]] = {
    "Int": [0, 1, -1, 3.0, 1.5, 9007199254740993, 2**63, -(2**63) - 1, 1e300, True, "5", None],
    "Float": [0, 1.5, 9007199254740993, 10**400, True, "1.5", None],
    "Str": ["\ud800", "a\ud83d", 5, None, ["a"]],
    "Bool": [True, False, 1, "true", None],
    "Date": [20260928, None],
    "DateTime": [0, None],
    "Uuid": [0, None],
}  # fmt: skip


def _bounds(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    s = d["scalar"]
    for key, delta in (("ge", -1), ("le", 1)):
        if key in d and s == "Int":
            b = math.ceil(d[key]) if key == "ge" else math.floor(d[key])
            out += [str(b), str(b + delta)]
        elif key in d and s == "Float":
            out += [repr(float(d[key])), repr(float(d[key]) + delta * 0.5)]
    for key in ("min_len", "max_len"):
        if key in d:
            n = d[key]
            out += ["a" * max(n - 1, 0), "a" * n, "a" * (n + 1), "👋" * n, "👋" * (n + 1)]
    out += list(d.get("one_of", []))
    out += [v.upper() for v in d.get("one_of", [])]
    return out


def text_values(d: dict[str, Any]) -> list[str]:
    return TEXT[d["scalar"]] + _bounds(d)


def json_values(d: dict[str, Any]) -> list[Any]:
    base = [
        int(v) if d["scalar"] == "Int" and re.fullmatch(r"-?\d+", v) else v for v in text_values(d)
    ]
    return base + JSON_EXTRA[d["scalar"]]


def _accepts(d: dict[str, Any], raw: object, text: bool) -> bool:
    p = prelude()
    try:
        value = p["_convert"](to_param(d), raw, text=text)
        p["_constrain"](to_param(d), value)
    except ValueError:
        return False
    return True


def valid_value(d: dict[str, Any], seen: list[object], text: bool) -> object | None:
    """A value the server accepts for `d` (None: none found, the parameter is left out)."""
    pool = seen + (text_values(d) if text else json_values(d))
    return next(
        (v for v in pool if not (d["source"] == "path" and R_A(v)) and _accepts(d, v, text)), None
    )


def _route(plan: EndpointPlan, path: str) -> dict[str, str] | None:
    """Path parameters as Starlette extracts them: the route's compiled regex matched against
    the percent-decoded path (the ASGI scope's `path`), then its convertors applied."""
    rx, _, convertors = compile_path(plan.path)
    m = rx.match(path)
    return None if m is None else {k: convertors[k].convert(v) for k, v in m.groupdict().items()}


def _resolve(
    plans: tuple[EndpointPlan, ...], method: str, path: str
) -> tuple[EndpointPlan, dict[str, str]] | None:
    """The endpoint serving `method path` and its path parameters, as Starlette's router picks
    them: in the server's registration order (literal segments before parameters, so
    /items/since is not /items/{id}), the first route whose path and method match (a path
    match with another method only is a 405); when no route's path matches, the same with
    the trailing slash toggled (redirect_slashes: a 307 the suites' client follows)."""
    decoded = urllib.parse.unquote(path.split("?")[0])
    toggled = decoded[:-1] if decoded.endswith("/") else decoded + "/"
    for candidate in [decoded] if decoded == "/" else [decoded, toggled]:
        ordered = sorted(plans, key=_specificity)
        hits = [(p, m) for p in ordered if (m := _route(p, candidate)) is not None]
        if hits:
            return next(((p, m) for p, m in hits if p.method == method), None)
    return None


def _qtext(v: object) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def _wire(plan: EndpointPlan, specs: list[dict[str, Any]], name: str, path: dict[str, str],
          query: dict[str, object], body: dict[str, object] | None, rules: tuple[str, ...] = ()
          ) -> dict[str, Any]:  # fmt: skip
    q = "&".join(
        f"{urllib.parse.quote(k)}={urllib.parse.quote(_qtext(v))}" for k, v in query.items()
    )
    if plan.body == "form":
        raw = urllib.parse.urlencode({k: _qtext(v) for k, v in (body or {}).items()}).encode()
        return case(name, specs, path=path, query=q, body=raw, form=True,
                    ctype="application/x-www-form-urlencoded", rules=rules)  # fmt: skip
    raw = b"" if body is None else json.dumps(body).encode()
    return case(name, specs, path=path, query=q, body=raw, rules=rules)


def _endpoint_cases(app: App, plan: EndpointPlan) -> list[dict[str, Any]]:
    specs = plan_specs(plan)
    if not specs:
        return []
    has_body = any(d["source"] == "body" for d in specs)
    seen: dict[str, list[object]] = {d["name"]: [] for d in specs}
    out: list[dict[str, Any]] = []
    # (a) the suites' requests
    for i, r in enumerate(app.requests):
        if (hit := _resolve(app.plans, r.method, r.path)) is None or hit[0] is not plan:
            continue
        path = hit[1]
        body = r.form if plan.body == "form" else r.json
        out.append(_wire(plan, specs, f"{plan.name} suite {i}", path, r.query,
                         body if has_body else None))  # fmt: skip
        for d in specs:
            src = path if d["source"] == "path" else r.query if d["source"] == "query" else body
            if isinstance(src, dict) and d["name"] in src:
                v = src[d["name"]]
                # the server sees a query or form value as its wire text
                seen[d["name"]].append(
                    v if d["source"] == "body" and plan.body != "form" else _qtext(v)
                )
    text = {d["name"]: d["source"] != "body" or plan.body == "form" for d in specs}
    valid = {d["name"]: valid_value(d, seen[d["name"]], text[d["name"]]) for d in specs}

    def request(over: dict[str, object], drop: str | None = None) -> tuple[dict, dict, dict | None]:
        path, query, body = {}, {}, ({} if has_body else None)
        for d in specs:
            n = d["name"]
            v = over.get(n, valid[n])
            if n == drop or v is None and n not in over:
                continue
            target = path if d["source"] == "path" else query if d["source"] == "query" else body
            target[n] = v  # type: ignore[index]
        return path, query, body

    # (b) boundaries: one parameter varied, the others valid
    for d in specs:
        n = d["name"]
        values = text_values(d) if text[n] else json_values(d)
        for k, v in enumerate(values):
            if d["source"] == "path" and (v is None or R_A(v)):
                continue
            if d["source"] == "query" and v is None:
                continue
            out.append(_wire(plan, specs, f"{plan.name} {n} b{k}", *request({n: v})))
        if d["source"] != "path":
            out.append(_wire(plan, specs, f"{plan.name} {n} missing", *request({}, drop=n)))
    # (c) seeded random
    rng = random.Random(zlib.crc32(f"{app.name}/{plan.name}".encode()))
    for k in range(RANDOM_PER_ENDPOINT):
        over: dict[str, object] = {}
        for d in specs:
            roll = rng.random()
            pool = text_values(d) if text[d["name"]] else json_values(d)
            if roll < 0.4:
                v = rng.choice(pool)
                if d["source"] == "path" and (v is None or R_A(v)):
                    continue
                if d["source"] == "query" and v is None:
                    continue
                over[d["name"]] = v
        out.append(_wire(plan, specs, f"{plan.name} r{k}", *request(over)))
    return out


def wire_corpus(app: App) -> dict[str, Any]:
    cases = [answer(c) for plan in app.plans for c in _endpoint_cases(app, plan)]
    return {"app": app.name, "params": cases, "patterns": pattern_map(cases)}
