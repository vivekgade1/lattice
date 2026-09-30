"""Request corpora for the gate (spec 2026-09-29 ui-a §5.3): (a) existing suites' requests,
(b) boundary values from each parameter spec (every §4 hazard applied to every parameter of
its type), (c) seeded random requests per endpoint (15 per parameter, at most 60), drawing
freshly generated values.
Wire cases (L2) are answered by the server prelude; typed cases (L3) are sent by the client.

A corpus stores each endpoint's specs once (`specs`, by endpoint name); a case names its
`endpoint`, and a rejected case keeps only its status and `detail` (the lattice body's).
Duplicate wire requests are dropped (the first name is kept)."""

from __future__ import annotations

import json
import math
import random
import re
import struct
import urllib.parse
import uuid
import zlib
from collections.abc import Callable
from typing import Any

from starlette.routing import compile_path

from lattice.api.codegen import _specificity
from lattice.api.ir import EndpointPlan
from lattice.tsgen.pyref import answer, case, pattern_map, prelude, to_param
from lattice.tsgen.specs import plan_specs
from tests.tsgen.gate.apps import App

# (c) per endpoint: RANDOM_PER_PARAM per request parameter, at most RANDOM_PER_ENDPOINT (the
# corpora stay near 5 MB in all; an endpoint's random requests scale with its parameters)
RANDOM_PER_PARAM = 15
RANDOM_PER_ENDPOINT = 60


def R_A(value: object) -> bool:
    """A path value the client cannot route faithfully (decision log: path `Str`)."""
    return isinstance(value, str) and (value in ("", ".", "..") or "/" in value)


# Characters str.strip() removes (Python whitespace, incl. the \x1c-\x1f separators and
# \x85), and ﻿, which it does not.
PAD = [" ", "\t", "\n", "\x0b", "\x0c", "\r", "\x1c", "\x1d", "\x1e", "\x1f", "\x85", "\xa0",
       " ", " ", "　", "﻿"]  # fmt: skip


def _padded(core: str) -> list[str]:
    return [p + core for p in PAD if p not in " \t\n"] + [core + "\x85", core + "﻿"]


# Candidate raw values per scalar: text form for path/query/form, JSON form for bodies.
TEXT = {
    "Int": ["0", "1", "-1", "5", " 5", *_padded("5"), "5.0", "1e3", "1e21", "abc", "",
            "9007199254740991", "9007199254740992", "9007199254740993",
            "9223372036854775807", "9223372036854775808", "-9223372036854775808",
            "-9223372036854775809", "+7", "007", "1_000", "٥", "５", "-0"],
    "Float": ["0", "1.5", "-2.5", ".5", "5.", "-0", "nan", "NaN", "inf", "-inf", "Infinity",
              "1e16", "1e21", "1e400", "1e-400", "1_0", "0x1p3", *_padded("1.5"),
              "9007199254740991", "9007199254740992", "9007199254740993", "١.5",
              "1.7976931348623157e308", "5e-324"],
    "Str": ["", "a", "ab", "abc", "a\n", "é", "👋", "👋👋👋", " x ", "K", "A1_",
            "x@y.z", "it's5", "\x00", "\x85", "﻿", " ", "é"],
    "Bool": ["true", "TRUE", "yes", "on", "1", "0", "off", "no", "maybe", "", " true",
             *_padded("true"), "t", "True", "oN"],
    "Date": ["2026-09-28", "20260928", "2026-W01-1", "2026-02-30", "", "2026-09-28T00:00",
             " 2026-09-28", "\x852026-09-28"],
    "DateTime": ["2026-09-28T10:11:12", "2026-09-28T10:11:12Z", "2026-09-28T10:11:12+01:00",
                 "2026-09-28T24:00", "2026-09-28 10:00", "20260928T101112", "bad",
                 "0001-01-01T00:00:00+01:00", "2026-09-28T10:11:12.1234567",
                 " 2026-09-28T10:11:12", "\x852026-09-28T10:11:12", "﻿2026-09-28T10:11"],
    "Uuid": ["0123456789abcdef0123456789abcdef", "01234567-89AB-CDEF-0123-456789ABCDEF",
             "01234567-89abcdef-0123-456789abcdef", "nope", "",
             "{01234567-89ab-cdef-0123-456789abcdef}",
             "urn:uuid:01234567-89ab-cdef-0123-456789abcdef",
             " 01234567-89ab-cdef-0123-456789abcdef"],
}  # fmt: skip
NAN, INF = float("nan"), float("inf")
JSON_EXTRA: dict[str, list[Any]] = {
    "Int": [0, 1, -1, 3.0, 1.5, 2**53 - 1, 2**53, 2**53 + 1, 2**63 - 1, 2**63, -(2**63),
            -(2**63) - 1, 1e21, 1e300, -0.0, NAN, INF, -INF, True, "5", None, [5]],
    "Float": [0, 1.5, -0.0, 2**53 - 1, 2**53, 2**53 + 1, 2**63, 10**400, 1e21, 5e-324,
              1.7976931348623157e308, NAN, INF, -INF, True, "1.5", None],
    "Str": ["\ud800", "a\ud83d", "\udc00a", 5, None, ["a"], True],
    "Bool": [True, False, 1, 0, "true", None],
    "Date": [20260928, None],
    "DateTime": [0, None],
    "Uuid": [0, None, 123456789],
}  # fmt: skip


def _bounds(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    s = d["scalar"]
    for key, delta in (("ge", -1), ("le", 1)):
        if key in d and s == "Int":
            b = math.ceil(d[key]) if key == "ge" else math.floor(d[key])
            out += [str(b), str(b + delta)]
        elif key in d and s == "Float":
            b = float(d[key])
            out += [repr(b), repr(b + delta * 0.5), repr(math.nextafter(b, delta * INF))]
    for key in ("min_len", "max_len"):
        if key in d:
            n = d[key]
            out += ["a" * max(n - 1, 0), "a" * n, "a" * (n + 1), "👋" * n, "👋" * (n + 1)]
    out += list(d.get("one_of", []))
    out += [v.upper() for v in d.get("one_of", [])]
    return out


def _unique(values: list[Any]) -> list[Any]:
    """`values` without repeats (by JSON spelling, so 1, 1.0 and True stay apart)."""
    seen: set[str] = set()
    out = []
    for v in values:
        key = json.dumps(v)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def text_values(d: dict[str, Any]) -> list[str]:
    return _unique(TEXT[d["scalar"]] + _bounds(d))


_JSON_INT = re.compile(r"-?(?:0|[1-9][0-9]*)")
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _number(v: str, scalar: str) -> list[Any]:
    """The JSON number forms of a numeric text value (none when it is not a JSON number)."""
    if scalar not in ("Int", "Float") or not _JSON_NUMBER.fullmatch(v):
        return []
    out: list[Any] = []
    if _JSON_INT.fullmatch(v):
        out.append(int(v))
    if scalar == "Float" or not _JSON_INT.fullmatch(v):
        out.append(float(v))  # 1e400 becomes an Infinity token
    return out


def json_values(d: dict[str, Any]) -> list[Any]:
    """Every text value as a JSON string and, if numeric, as JSON numbers; plus JSON extras."""
    base: list[Any] = []
    for v in text_values(d):
        base += _number(v, d["scalar"]) + [v]
    return _unique(base + JSON_EXTRA[d["scalar"]])


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


# ---- random values (layer c) ----------------------------------------------------------------


def _maybe_pad(rng: random.Random, s: str) -> str:
    if rng.random() < 0.25:
        s = rng.choice(PAD) + s
    if rng.random() < 0.15:
        s = s + rng.choice(PAD)
    return s


def _rand_int(rng: random.Random, d: dict[str, Any]) -> int:
    roll = rng.random()
    anchors = [d[k] for k in ("ge", "le") if k in d]
    if roll < 0.4 and anchors:
        lo = math.ceil(d.get("ge", anchors[0])) - 5
        hi = math.floor(d.get("le", anchors[-1])) + 5
        return rng.randint(min(lo, hi), max(lo, hi))
    if roll < 0.6:
        edge = rng.choice([2**53, -(2**53), 2**63, -(2**63), 2**64, 0])
        return edge + rng.randint(-3, 3)
    sign = rng.choice([1, -1])
    return sign * rng.getrandbits(rng.randint(1, 70))


def _int_text(rng: random.Random, n: int) -> str:
    roll = rng.random()
    s = str(n)
    if roll < 0.1:
        s = ("+" if n >= 0 else "") + s
    elif roll < 0.2:
        s = s.replace("-", "-00") if n < 0 else "00" + s
    elif roll < 0.27:
        s = s + rng.choice([".0", "e0", ".5", "_0"])
    return _maybe_pad(rng, s)


def _int_json(rng: random.Random, n: int) -> object:
    roll = rng.random()
    if roll < 0.6:
        return n
    if roll < 0.8:
        return float(n)
    if roll < 0.9:
        return n + 0.5
    return str(n)


def _rand_float(rng: random.Random, d: dict[str, Any]) -> float:
    roll = rng.random()
    anchors = [float(d[k]) for k in ("ge", "le") if k in d]
    if roll < 0.35 and anchors:
        b = rng.choice(anchors)
        pick = rng.random()
        if pick < 0.3:
            return math.nextafter(b, rng.choice([INF, -INF]))
        return b + rng.uniform(-2, 2) * rng.choice([1, 1e-3, 1e-9])
    if roll < 0.5:
        return struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
    if roll < 0.6:
        return rng.choice([NAN, INF, -INF, 0.0, -0.0, 5e-324, 1.7976931348623157e308])
    if roll < 0.75:
        return float(rng.choice([2**53, 2**63, 10**21]) + rng.randint(-3, 3))
    return rng.uniform(-1, 1) * 10.0 ** rng.randint(-30, 30)


def _float_text(rng: random.Random, x: float) -> str:
    roll = rng.random()
    if roll < 0.5:
        s = repr(x)
    elif roll < 0.7:
        s = f"{x:.{rng.randint(1, 20)}g}"
    elif roll < 0.85:
        s = f"{x:e}"
    else:
        s = f"{x:.{rng.randint(0, 3)}f}"
    return _maybe_pad(rng, s)


def _float_json(rng: random.Random, x: float) -> object:
    roll = rng.random()
    if roll < 0.7:
        return x  # NaN / Infinity / -Infinity tokens for the non-finite ones
    if roll < 0.85 and math.isfinite(x):
        return int(x)
    return repr(x)


_ALPHABETS: list[tuple[int, int]] = [
    (0x20, 0x7E), (0x20, 0x7E), (0x00, 0x1F), (0x7F, 0xFF), (0x100, 0xD7FF),
    (0xE000, 0xFFFF), (0x10000, 0x10FFFF),
]  # fmt: skip


def _rand_chars(rng: random.Random, n: int, surrogates: bool) -> str:
    out = []
    for _ in range(n):
        if surrogates and rng.random() < 0.08:
            out.append(chr(rng.randint(0xD800, 0xDFFF)))
        else:
            lo, hi = rng.choice(_ALPHABETS)
            out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def _rand_str(rng: random.Random, d: dict[str, Any], surrogates: bool) -> str:
    roll = rng.random()
    if roll < 0.25 and d.get("one_of"):
        v = rng.choice(d["one_of"])
        pick = rng.random()
        return v if pick < 0.4 else v.upper() if pick < 0.6 else _maybe_pad(rng, v)
    if roll < 0.35 and d.get("email"):
        return _rand_chars(rng, rng.randint(0, 6), surrogates) + "@x.io"
    lens = [d[k] + rng.randint(-2, 2) for k in ("min_len", "max_len") if k in d]
    n = max(0, rng.choice(lens)) if lens and rng.random() < 0.7 else rng.randint(0, 10)
    return _rand_chars(rng, n, surrogates)


def _rand_bool(rng: random.Random) -> str:
    word = rng.choice(["true", "false", "yes", "no", "on", "off", "1", "0", "t", "y", "2"])
    word = "".join(c.upper() if rng.random() < 0.3 else c for c in word)
    return _maybe_pad(rng, word)


def _rand_date(rng: random.Random) -> str:
    y, m, dd = rng.randint(0, 10000), rng.randint(0, 13), rng.randint(0, 32)
    roll = rng.random()
    if roll < 0.7:
        return f"{y:04d}-{m:02d}-{dd:02d}"
    if roll < 0.85:
        return f"{y:04d}{m:02d}{dd:02d}"
    return f"{y:04d}-W{rng.randint(0, 54):02d}-{rng.randint(0, 8)}"


def _rand_datetime(rng: random.Random) -> str:
    s = _rand_date(rng) + rng.choice(["T", "T", " ", "t", "\x85", "_"])
    s += f"{rng.randint(0, 25):02d}"
    if rng.random() < 0.85:
        s += f":{rng.randint(0, 61):02d}"
        if rng.random() < 0.7:
            s += f":{rng.randint(0, 61):02d}"
            if rng.random() < 0.4:
                s += rng.choice([".", ","]) + str(rng.getrandbits(40))[: rng.randint(1, 9)]
    roll = rng.random()
    if roll < 0.3:
        s += rng.choice(["Z", "z"])
    elif roll < 0.6:
        sign = rng.choice("+-")
        s += f"{sign}{rng.randint(0, 25):02d}:{rng.randint(0, 61):02d}"
        if rng.random() < 0.2:
            s += f":{rng.randint(0, 61):02d}"
    return _maybe_pad(rng, s) if rng.random() < 0.3 else s


def _rand_uuid(rng: random.Random) -> str:
    u = uuid.UUID(int=rng.getrandbits(128))
    roll = rng.random()
    s = str(u) if roll < 0.4 else u.hex if roll < 0.6 else str(u).upper() if roll < 0.7 else ""
    if not s:
        s = rng.choice(["{%s}", "urn:uuid:%s", "%s-", "-%s"]) % u
    if rng.random() < 0.15:
        cut = rng.randint(0, len(s))
        s = s[:cut] + s[cut + 1 :]
    return _maybe_pad(rng, s) if rng.random() < 0.2 else s


def random_value(rng: random.Random, d: dict[str, Any], text: bool) -> object:
    """A freshly generated raw value for `d`: text for path/query/form, JSON otherwise.
    Text never holds a lone surrogate: a URL or form cannot carry one (UTF-8 has no
    encoding of it), so no real request delivers it; a JSON body can (as an escape)."""
    s = d["scalar"]
    if s == "Int":
        n = _rand_int(rng, d)
        return _int_text(rng, n) if text else _int_json(rng, n)
    if s == "Float":
        x = _rand_float(rng, d)
        return _float_text(rng, x) if text else _float_json(rng, x)
    gen: dict[str, Callable[[], str]] = {
        "Str": lambda: _rand_str(rng, d, surrogates=not text),
        "Bool": lambda: _rand_bool(rng),
        "Date": lambda: _rand_date(rng),
        "DateTime": lambda: _rand_datetime(rng),
        "Uuid": lambda: _rand_uuid(rng),
    }
    v = gen[s]()
    if not text and s == "Bool" and rng.random() < 0.7:
        return rng.choice([True, False])
    return v


# ---- routing --------------------------------------------------------------------------------


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


# ---- cases ----------------------------------------------------------------------------------


def _qtext(v: object) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def _wire(plan: EndpointPlan, specs: list[dict[str, Any]], name: str, path: dict[str, str],
          query: dict[str, object], body: dict[str, object] | None) -> dict[str, Any]:  # fmt: skip
    q = "&".join(
        f"{urllib.parse.quote(k)}={urllib.parse.quote(_qtext(v))}" for k, v in query.items()
    )
    if plan.body == "form":
        raw = urllib.parse.urlencode({k: _qtext(v) for k, v in (body or {}).items()}).encode()
        return case(name, specs, path=path, query=q, body=raw, form=True,
                    ctype="application/x-www-form-urlencoded")  # fmt: skip
    raw = b"" if body is None else json.dumps(body).encode()
    return case(name, specs, path=path, query=q, body=raw)


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

    Wire = tuple[dict, dict, dict | None]

    def request(over: dict[str, object], drop: tuple[str, ...] = ()) -> Wire:
        path, query, body = {}, {}, ({} if has_body else None)
        for d in specs:
            n = d["name"]
            v = over.get(n, valid[n])
            if n in drop or v is None and n not in over:
                continue
            target = path if d["source"] == "path" else query if d["source"] == "query" else body
            target[n] = v  # type: ignore[index]
        return path, query, body

    def deliverable(d: dict[str, Any], v: object) -> bool:
        """A path value is a non-R_A string; a query value is never None (no JSON null)."""
        if d["source"] == "path":
            return v is not None and not R_A(v)
        return not (d["source"] == "query" and v is None)

    # (b) boundaries: one parameter varied, the others valid
    for d in specs:
        n = d["name"]
        values = text_values(d) if text[n] else json_values(d)
        for k, v in enumerate(values):
            if deliverable(d, v):
                out.append(_wire(plan, specs, f"{plan.name} {n} b{k}", *request({n: v})))
        if d["source"] != "path":
            out.append(_wire(plan, specs, f"{plan.name} {n} missing", *request({}, drop=(n,))))
    # (c) seeded random: per parameter, left out, the valid value, a hazard or a fresh value
    rng = random.Random(zlib.crc32(f"{app.name}/{plan.name}".encode()))
    for k in range(min(RANDOM_PER_ENDPOINT, RANDOM_PER_PARAM * len(specs))):
        over: dict[str, object] = {}
        drop: list[str] = []
        for d in specs:
            n = d["name"]
            roll = rng.random()
            if roll < 0.12:
                if d["source"] != "path":
                    drop.append(n)
                continue
            if roll < 0.3:
                continue  # the valid value
            if roll < 0.5:
                v = rng.choice(text_values(d) if text[n] else json_values(d))
            else:
                v = random_value(rng, d, text[n])
            if deliverable(d, v):
                over[n] = v
        out.append(_wire(plan, specs, f"{plan.name} r{k}", *request(over, tuple(drop))))
    return out


def _key(endpoint: str, c: dict[str, Any]) -> str:
    wire = [endpoint, sorted(c["path"].items()), c["query"], c["body"], c["form"], c["ctype"]]
    return json.dumps(wire)


def wire_corpus(app: App) -> dict[str, Any]:
    """{"app", "specs": {endpoint: specs}, "patterns", "params": [answered cases]}: each case
    names its `endpoint`; duplicate wire requests are dropped (the first name is kept)."""
    specs: dict[str, list[dict[str, Any]]] = {}
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for plan in app.plans:
        for c in _endpoint_cases(app, plan):
            if (key := _key(plan.name, c)) in seen:
                continue
            seen.add(key)
            assert specs.setdefault(plan.name, c["specs"]) == c["specs"], plan.name
            done = answer(c)
            slim = {k: v for k, v in done.items() if k not in ("specs", "fastapi", "rules")}
            if "lattice" in slim:
                slim["detail"] = json.loads(slim.pop("lattice"))["detail"]
            if not slim["form"]:
                del slim["form"]
            if slim["ctype"] is None:
                del slim["ctype"]
            cases.append({"endpoint": plan.name, **slim})
    return {
        "app": app.name,
        "specs": specs,
        "patterns": pattern_map([{"specs": s} for s in specs.values()]),
        "params": cases,
    }


def corpus_text(corpus: dict[str, Any]) -> str:
    """Compact JSON, one case per line (diffs stay readable), keys sorted, ASCII only."""

    def dump(v: object) -> str:
        return json.dumps(v, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    head = [f"{dump(k)}:{dump(corpus[k])}" for k in ("app", "patterns", "specs")]
    rows = ",\n".join(dump(c) for c in corpus["params"])
    return "{" + ",\n".join(head) + ',\n"params":[\n' + rows + "\n]}\n"
