# ruff: noqa: E501
"""Regenerates prelude_answers.json: what the generated FastAPI app's prelude
(lattice/api/codegen.py _PRELUDE) answers for parameter parsing, error bodies, tokens,
date-time / UUID parsing, the SQLite functions, wire values and JSON rendering. The Go
request runtime's tests (http_test.go, prelude_test.go) assert against it. Run from the
repository root:

    uv run python lattice/gogen/rt/testdata/gen_prelude_answers.py

Values are written tagged, so the Go side can tell an int from a float and a None from
an absent value: {"t": "int", "v": "12"}, {"t": "float", "v": "<repr>"}, {"t": "str",
"v": ...}, {"t": "bool", "v": true}, {"t": "none"}, {"t": "unset"}, {"t": "date", "v":
iso}, {"t": "datetime", "v": datetime_text}, {"t": "uuid", "v": canonical}, {"t":
"bytes", "v": hex}.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bench" / "api_kit"))
sys.path.insert(0, str(ROOT))

from starlette.datastructures import QueryParams  # noqa: E402

from lattice.api.codegen import _PRELUDE  # noqa: E402
from lattice.gogen.regex import compile_regex  # noqa: E402

NS: dict[str, object] = {}
exec(compile(_PRELUDE, "prelude", "exec"), NS)  # noqa: S102 - the reference itself
P = NS  # prelude namespace


def tag(v: object) -> dict[str, object]:
    if v is P["UNSET"]:
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
        return {"t": "datetime", "v": P["datetime_text"](v)}
    if isinstance(v, dt.date):
        return {"t": "date", "v": v.isoformat()}
    if isinstance(v, uuid.UUID):
        return {"t": "uuid", "v": str(v)}
    if isinstance(v, bytes):
        return {"t": "bytes", "v": v.hex()}
    raise TypeError(type(v))


def untag(d: dict[str, object]) -> object:
    t = d["t"]
    if t == "unset":
        return P["UNSET"]
    if t == "none":
        return None
    if t == "int":
        return int(d["v"])  # type: ignore[arg-type]
    if t == "float":
        return float(d["v"])  # type: ignore[arg-type]
    if t == "date":
        return dt.date.fromisoformat(d["v"])  # type: ignore[arg-type]
    if t == "datetime":
        return dt.datetime.fromisoformat(d["v"])  # type: ignore[arg-type]
    return d["v"]


def response_text(style: str, status: int, code: str, detail: object) -> str:
    P["ERROR_BODY"] = style
    try:
        return P["json_error"](status, code, detail).body.decode("utf-8")
    finally:
        P["ERROR_BODY"] = "lattice"


out: dict[str, object] = {"python": sys.version.split()[0]}

# ---- error bodies ---------------------------------------------------------------------------
bodies = []
for style in ("lattice", "fastapi"):
    for status, code, detail in [
        (401, "unauthorized", None),
        (403, "forbidden", None),
        (404, "not_found", None),
        (405, "method_not_allowed", None),
        (409, "conflict", "a row with the same unique value already exists"),
        (400, "bad_request", "Inactive user"),
        (422, "validation", [{"loc": ["query", "x"], "msg": "field required"}]),
        (500, "internal", "1:2: error R620: out of fuel"),
        (404, "not_found", "no such row"),
        (418, "teapot", None),
        (413, "too_large", None),
        (422, "unprocessable", None),
        (429, "slow_down", None),
        (400, "bad_request", 'é   \x00 " \\ 🎉'),
    ]:
        bodies.append(
            {
                "style": style,
                "status": status,
                "code": code,
                "detail": detail,
                "body": response_text(style, status, code, detail),
            }
        )
out["error_bodies"] = bodies


# ---- parameters -----------------------------------------------------------------------------
def spec(name: str, source: str, scalar: str, **kw: object) -> dict[str, object]:
    d: dict[str, object] = {"name": name, "source": source, "scalar": scalar, **kw}
    if isinstance(kw.get("pattern"), str):  # the SRE code the Go program embeds
        c = compile_regex(kw["pattern"])  # type: ignore[arg-type]
        assert c.error is None, kw["pattern"]
        d["pattern_code"] = {"code": list(c.code), "groups": c.groups, "names": dict(c.names)}
    return d


def to_param(d: dict[str, object]) -> object:
    skip = ("name", "source", "scalar", "default", "pattern_code")
    kw = {k: v for k, v in d.items() if k not in skip}
    if "one_of" in kw:
        kw["one_of"] = tuple(kw["one_of"])  # type: ignore[arg-type]
    if "default" in d:
        kw["default"] = untag(d["default"])  # type: ignore[arg-type]
    return P["Param"](d["name"], d["source"], d["scalar"], **kw)


INT_Q = spec("n", "query", "Int")
cases: list[dict[str, object]] = []


def case(
    name: str,
    specs: list[dict[str, object]],
    *,
    path: dict[str, str] | None = None,
    query: str = "",
    body: bytes = b"",
    form: bool = False,
    ctype: str | None = None,
) -> None:
    cases.append(
        {
            "name": name,
            "specs": specs,
            "path": path or {},
            "query": query,
            "body": base64.b64encode(body).decode(),
            "form": form,
            "ctype": ctype,
        }
    )


# Int from the query string
for q in [
    "n=5",
    "n=-5",
    "n=+5",
    "n=%205%20",
    "n=007",
    "n=5.0",
    "n=abc",
    "n=",
    "",
    "m=1",
    "n=9223372036854775807",
    "n=9223372036854775808",
    "n=-9223372036854775808",
    "n=-9223372036854775809",
    "n=" + "9" * 5000,
    "n=1&n=2",
    "n=%EF%BC%95",
    "n=1e3",
    "n=%0A5",
    "n=5;x",
    "n= 5".encode().decode("latin-1"),
    "n=0x10",
    "n=1_000",
]:
    case(f"int query {q[:30]!r}", [INT_Q], query=q)
# bounds
for q in ["n=0", "n=1", "n=10", "n=11", "n=-1"]:
    case(f"int bounds {q}", [spec("n", "query", "Int", ge=1.0, le=10.0)], query=q)
case("int ge frac", [spec("n", "query", "Int", ge=0.5, le=1e20)], query="n=0")
case("int le big", [spec("n", "query", "Int", ge=-1e20, le=1e20)], query="n=9223372036854775807")
case("int le frac", [spec("n", "query", "Int", le=2.5)], query="n=3")
case("float le frac", [spec("x", "query", "Float", le=2.5)], query="x=2.6")
case("float ge", [spec("x", "query", "Float", ge=-1.5)], query="x=-2")
# Float
for q in [
    "x=1",
    "x=1.5",
    "x=.5",
    "x=5.",
    "x=1e3",
    "x=1E-3",
    "x=-0",
    "x=inf",
    "x=nan",
    "x=1e400",
    "x=1e-400",
    "x=1_0",
    "x=%201.5",
    "x=0x1p3",
    "x=+.5e+2",
    "x=.",
]:
    case(f"float query {q}", [spec("x", "query", "Float")], query=q)
# Bool
for q in [
    "b=true",
    "b=TRUE",
    "b=1",
    "b=yes",
    "b=On",
    "b=false",
    "b=0",
    "b=no",
    "b=off",
    "b=t",
    "b=2",
    "b=",
    "b=%20true",
]:
    case(f"bool query {q}", [spec("b", "query", "Bool")], query=q)
# Str with constraints
S = [spec("s", "query", "Str", min_len=2, max_len=4)]
for q in [
    "s=a",
    "s=ab",
    "s=abcd",
    "s=abcde",
    "s=%C3%A9%C3%A9",
    "s=%F0%9F%8E%89",
    "s=%FF%FE",
    "s=%E2%82",
    "s=%E2%82A",
    "s=+a+",
    "s=%zz",
    "s=a%",
    "s=éé".encode().decode("latin-1"),
]:
    case(f"str query {q}", S, query=q)
case("one_of ok", [spec("s", "query", "Str", one_of=["a", "b'c", 'd"e'])], query="s=a")
case("one_of bad", [spec("s", "query", "Str", one_of=["a", "b'c", 'd"e', "é\n"])], query="s=z")
case("pattern ok", [spec("s", "query", "Str", pattern=r"[a-z]+\d?")], query="s=abc1")
case("pattern bad", [spec("s", "query", "Str", pattern=r"[a-z]+\d?")], query="s=abc12")
case("pattern quote", [spec("s", "query", "Str", pattern=r"it's\\d")], query="s=x")
# the parameter pattern runs on Python's SRE engine (re.ASCII), not Go's regexp
for pat, q in [
    ("(?i)[a-z]+", "s=%C5%BF"),  # U+017F: IGNORECASE is ASCII under re.ASCII
    ("(?i)[a-z]+", "s=%E2%84%AA"),  # the Kelvin sign
    ("(?i)[a-z]+", "s=AbC"),
    (r"x\Z", "s=x"),
    (r"x\Z", "s=x%0A"),
    ("a{1001}", "s=" + "a" * 1001),
    ("a{1001}", "s=" + "a" * 1000),
    ("x{,2}", "s=xx"),
    ("x{,2}", "s=x{,2}"),
    ("[[:alpha:]]", "s=a"),
    ("[[:alpha:]]", "s=:"),
    (r"\s", "s=%0B"),
    (r"a$", "s=a%0A"),
    (r"\d+", "s=%D9%A3"),
]:
    case(f"pattern sre {pat} {q[:20]}", [spec("s", "query", "Str", pattern=pat)], query=q)
EMAIL = r"[^@\s]+@[^@\s]+\.[^@\s]+"
case("email bad", [spec("e", "query", "Str", pattern=EMAIL, email=True)], query="e=nope")
case("email ok", [spec("e", "query", "Str", pattern=EMAIL, email=True)], query="e=a%40b.co")
case(
    "email long",
    [spec("e", "query", "Str", pattern=EMAIL, email=True, max_len=1000)],
    query="e=" + "a" * 250 + "%40b.co",
)
# Date / DateTime / Uuid from text
for v in [
    "2026-09-28",
    "20260928",
    "2026-W39-1",
    "2026W391",
    "2026-02-30",
    "2026-9-28",
    "2026-09-28T10:00:00",
    "",
    "abcd-ef-gh",
    "2026-W53",
    "2020-W53",
    "2026-09-2",
]:
    case(f"date {v}", [spec("d", "query", "Date")], query="d=" + v)
for v in [
    "2026-09-28T10:11:12",
    "2026-09-28T10:11:12Z",
    "2026-09-28 10:11:12.5",
    "2026-09-28T10:11:12.1234567",
    "2026-09-28T10:11:12%2B02:00",
    "2026-09-28T10:11:12-0130",
    "2026-09-28",
    "2026-09-28T24:00:00",
    "2026-09-28T24:00:01",
    "2026-09-28T10",
    "2026-09-28T1011",
    "2026-09-28T10:11:12,5",
    "20260928T101112",
    "2026-W39-1T10:00",
    "0001-01-01T00:00:00%2B01:00",
    "9999-12-31T23:59:59-01:00",
    "2026-09-28X10:11",
    "2026-09-28T10:11:12Z%2B01",
    "2026-09-28T10:11:60",
    "2026-09-28T10:11:12.",
    "2026-09-28T10:11:12%2B00:00",
    "2026-09-28T10:11:12%2B01:30:15.5",
    "bad",
    "2026-09-28T10:11:12 ",
    "2026-09-28T10:11:1a",
    "2026-09-28T10:1112",
    "2026-09-28T1011:12",
    "2026-09-28%C3%A910:11",
    "2026-09-28T10:11:12z",
    "2026-09-28T10:11:12.123Z",
    "2026-09-28T10:11:12-00:00",
    "2026-09-28T25:00",
]:
    case(f"datetime {v}", [spec("t", "query", "DateTime")], query="t=" + v)
for v in [
    "A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11",
    "a0eebc999c0b4ef8bb6d6bb9bd380a11",
    "a0eebc99-9c0b4ef8-bb6d-6bb9bd380a11",
    "{a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11}",
    "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a1",
    "urn:uuid:a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
    "g0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
]:
    case(f"uuid {v}", [spec("u", "query", "Uuid")], query="u=" + v)
# path parameters and ordering of several problems
case(
    "path and query",
    [spec("id", "path", "Int", ge=1.0), spec("q", "query", "Str", max_len=3), INT_Q],
    path={"id": "0"},
    query="q=abcd",
)
case("path ok", [spec("id", "path", "Uuid")], path={"id": "A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11"})
# defaults
DEF = [
    spec("a", "query", "Int", default={"t": "int", "v": "7"}),
    spec("b", "query", "Str", default={"t": "none"}, nullable=True),
    spec("c", "query", "Uuid", default={"t": "str", "v": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"}),
    spec("d", "query", "Date", default={"t": "date", "v": "2026-01-02"}),
    spec("e", "query", "DateTime", default={"t": "datetime", "v": "2026-01-02T03:04:05+00:00"}),
    spec("f", "query", "Float", default={"t": "float", "v": "2.5"}),
    spec("g", "query", "Bool", default={"t": "bool", "v": False}),
]
case("defaults", DEF)
case("defaults sent", DEF, query="a=1&b=x&c=A0EEBC999C0B4EF8BB6D6BB9BD380A12&g=yes")
# JSON bodies
BODY = [
    spec("n", "body", "Int", ge=0.0),
    spec("s", "body", "Str", max_len=5),
    spec("b", "body", "Bool", default={"t": "bool", "v": False}),
    spec("x", "body", "Float", default={"t": "float", "v": "0.0"}),
    spec("u", "body", "Str", default={"t": "unset"}, optional=True, nullable=True),
    spec("v", "body", "Int", default={"t": "unset"}, optional=True),
]
for b in [
    b'{"n": 1, "s": "hi"}',
    b'{"n": 1.0, "s": "hi"}',
    b'{"n": 1.5, "s": "hi"}',
    b'{"n": true, "s": "hi"}',
    b'{"n": "1", "s": "hi"}',
    b'{"n": 1e2, "s": 5}',
    b'{"n": 99999999999999999999, "s": "hi"}',
    b'{"n": -1, "s": "toolong"}',
    b'{"n": 1, "s": "hi", "b": 1, "x": true}',
    b'{"n": 1, "s": "hi", "b": true, "x": 1}',
    b'{"n": 1, "s": "hi", "x": 1e400}',
    b'{"n": 1, "s": "hi", "x": NaN}',
    b'{"n": 1, "s": "hi", "x": -Infinity}',
    b'{"n": 1, "s": "hi", "x": ' + b"9" * 400 + b"}",
    b'{"n": 1, "s": "hi", "u": null, "v": null}',
    b'{"n": 1, "s": "hi", "u": "x", "v": 3}',
    b'{"n": null, "s": null}',
    b'{"n": 1, "s": "\\ud800"}',
    b'{"n": 1, "s": "\\ud83c\\udf89"}',
    b'{"n": 1, "s": "\xed\xa0\x80"}',
    b'{"n": 1, "s": "\xff"}',
    b'{"n": 1, "s": "a\tb"}',
    b'{"n": 1, "s": "a\\u0000"}',
    b'{"n": 1, "n": 2, "s": "x"}',
    b"[1, 2]",
    b"null",
    b"",
    b"   ",
    b"{",
    b'{"n": 01}',
    b'{"n": 1,}',
    b'\xef\xbb\xbf{"n": 1, "s": "bom"}',
    '{"n": 1, "s": "u16"}'.encode("utf-16"),
    '{"n": 1, "s": "u16"}'.encode("utf-16-le"),
    '{"n": 1, "s": "u32"}'.encode("utf-32"),
    b'{"n": 1, "s": "hi"} x',
    b' \n{"n": 1, "s": "hi"}\n ',
    b'{"n": -0, "s": "hi", "x": -0.0}',
    b'{"n": 1, "s": "\\/\\b\\f\\n\\r\\t\\"\\\\"}',
    b'{"n": 1, "s": "\\x"}',
    b'{"n": 1, "s": "\\u12"}',
    b'{"n": 1E+2, "s": "e"}',
    b'{"n": -, "s": "e"}',
    b'{"n": 1, "s": {"a": [1, {"b": null}]}}',
    b'{"n": 9223372036854775808.0, "s": "hi"}',
    b'{"n": 1, "s": "hi", "x": 1.5e308, "b": false}',
    b'{"n": 1, "s": "hi", "x": 123456789012345678901234567890}',
    b"\x00{}",
]:
    case(f"body {b[:40]!r}", BODY, body=b)
case("body with query", [INT_Q, spec("s", "body", "Str")], query="n=x", body=b"[]")
# forms
FORM = [spec("n", "body", "Int"), spec("s", "body", "Str"), spec("b", "body", "Bool")]
for b, ct in [
    (b"n=1&s=a+b&b=on", "application/x-www-form-urlencoded"),
    (b"n=1&s=a+b&b=on", "application/x-www-form-urlencoded; charset=utf-8"),
    (b"n=1&s=a+b&b=on", "APPLICATION/X-WWW-FORM-URLENCODED"),
    (b"n=1&s=a+b&b=on", "application/json"),
    (b"n=1&s=a+b&b=on", None),
    (b"n=x&s=%C3%A9&b=maybe", "application/x-www-form-urlencoded"),
    (b"n=1&s=\xe9&b=1", "application/x-www-form-urlencoded"),
    (b"n=1&s&b=1&&n=2", "application/x-www-form-urlencoded"),
]:
    case(f"form {b!r} {ct}", FORM, body=b, form=True, ctype=ct)

results = []
for c in cases:
    params = [to_param(s) for s in c["specs"]]  # type: ignore[attr-defined]
    raw_query = c["query"].encode("latin-1")  # type: ignore[attr-defined]
    query = dict(QueryParams(raw_query.decode("latin-1")))
    body = base64.b64decode(c["body"])  # type: ignore[arg-type]
    try:
        values = P["parse_params"](params, c["path"], query, body, c["form"], c["ctype"])
        c["values"] = {k: tag(v) for k, v in values.items()}
        c["order"] = list(values)
    except P["ErrorResponse"] as err:  # type: ignore[misc]
        c["status"] = err.status
        c["lattice"] = response_text("lattice", err.status, err.code, err.detail)
        c["fastapi"] = response_text("fastapi", err.status, err.code, err.detail)
    results.append(c)
out["params"] = results

# ---- tokens ---------------------------------------------------------------------------------
tokens = []
for h in [
    None,
    "",
    "Bearer abc",
    "bearer abc",
    "  BEARER   abc  ",
    "Bearer",
    "Bearer ",
    "Basic abc",
    "Bearer\tabc",
    "Bearer a b",
    "　Bearer abc　",
    "Bearerabc",
    "Bearer  abc",
]:
    tokens.append({"header": h, "token": P["_token"](h)})
out["tokens"] = tokens

# ---- date-times, dates, UUIDs --------------------------------------------------------------
dts = []
for v in [
    "2026-09-28T10:11:12",
    "2026-09-28T10:11:12Z",
    "2026-09-28 10:11:12.5",
    "2026-09-28",
    "2026-09-28T10:11:12.123456789",
    "2026-09-28T10:11:12+05:30",
    "2026-09-28T10:11:12-0130",
    "2026-09-28T24:00:00",
    "2026-12-31T24:00:00",
    "2026-02-30T24:00:00",
    "2026-09-28T10",
    "20260928T1011",
    "2026W391T10",
    "2026-W39",
    "2026W39",
    "2026-W39-7",
    "2026W397",
    "0001-01-01T00:00:00+01:00",
    "0001-01-01T00:59:59+01:00",
    "9999-12-31T23:59:59-01:00",
    "2026-09-28T10:11:12+00",
    "2026-09-28T10:11:12+0",
    "2026-09-28T10:11:12+000",
    "2026-09-28T10:11:12+01:00:30",
    "2026-09-28T10:11:12+010030.5",
    "2026-09-28T10:11:12+23:59",
    "2026-09-28T10:11:12+24:00",
    "2026-09-28T10:11:12Z+01:00",
    "2026-09-28T10:11:12.1,2",
    "2026-09-28T10:11:12,123",
    "2026-09-28T10:11",
    "2026-09-28T10:1",
    "2026-09-28T1:11",
    "2026-09-28 10:11:12",
    "2026-09-28é10:11:12",
    "2026-09-28T10:11:12.0000001",
    "2026-09-28T10:11:12.",
    "2026-09-28T10:11:12.a",
    "2026-09-28T10:11:12.1a",
    "2026-09-28T",
    "2026-09-28T10:11:12-",
    "2026-09-28T10:11:12+1",
    "2026-09-28T10:11:12+01:3",
    "2026-09-28T10:11:12+01:30:1",
    "2026-09-28T10:11:12Z ",
    " 2026-09-28",
    "2026-09-28 ",
    "2026-9-28T10:00",
    "20260928",
    "2026-09-2810:00",
    "2026-0928T10:00",
    "202609-28T10:00",
    "2026-09-28T10:00:00.000000Z",
    "1999-12-31T23:59:59.999999-00:00",
    "2026-13-01T00:00",
    "2026-09-28T101112.5",
    "2026-09-28T10:11:12.5+01:00",
    "2026-09-28T10.5",
    "2026-09-28T10:11.5",
    "2026-W01-1T00:00",
    "2020-W53-7",
    "2026-W53-1",
    "2026-W00-1",
    "2026-W10-8",
    "2026-09-28T10:11:12+01:00Z",
    "2026-09-28T10:11:12-01:00:00.123456",
    "1970-01-01T00:00:00",
    "2026-09-28T10:11:12+0100",
    "2026-09-28T10:11:12+01:00.5",
    "２026-09-28",
    "2026-09-28T１10:00",
]:
    try:
        parsed = P["parse_datetime"](v)
    except Exception as e:  # noqa: BLE001
        parsed = f"!{type(e).__name__}"
    dts.append(
        {
            "in": v,
            "out": parsed
            if parsed is None or isinstance(parsed, str)
            else P["datetime_text"](parsed),
        }
    )
out["datetimes"] = dts

dates = []
for v in [
    "2026-09-28",
    "20260928",
    "2026-W39-1",
    "2026W391",
    "2026-W39",
    "2026W39",
    "2026-02-29",
    "2024-02-29",
    "2026-00-01",
    "0000-01-01",
    "9999-12-31",
    "2026-09-2",
    "2026-0928",
    "202609-28",
    "2026-W39-0",
    "2026-W391",
    "2026W39-1",
    "2026-09-28 ",
    "+026-09-28",
    "2026-09-2٣",
    "2026-W53-1",
    "2020-W53-5",
    "2021-W53-1",
    "abcdefgh",
]:
    try:
        d = dt.date.fromisoformat(v).isoformat()
    except ValueError:
        d = None
    dates.append({"in": v, "out": d})
out["dates"] = dates

uuids = []
for v in [
    "A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11",
    "a0eebc999c0b4ef8bb6d6bb9bd380a11",
    "a0eebc99-9c0b4ef8-bb6d-6bb9bd380a11",
    "a0eebc999c0b-4ef8-bb6d-6bb9bd380a11",
    "{a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11}",
    "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a1",
    "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a111",
    "０a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a1",
    "",
]:
    uuids.append({"in": v, "out": P["uuid_text"](v)})
out["uuids"] = uuids

# ---- the SQLite functions -------------------------------------------------------------------
FN_SQL = [
    "SELECT lattice_lower('ÀB İ ẞ'), lattice_lower(3), lattice_lower(NULL), lattice_lower(1.5)",
    "SELECT lattice_upper('ßx ŉ ﬁ'), lattice_upper(X'41')",
    "SELECT lattice_strip('  x y 　 \n'), lattice_strip(''), lattice_strip(7)",
    "SELECT lattice_mod(7, -2), lattice_mod(-7, 2), lattice_mod(7, 0), lattice_mod(7, 0.0)",
    "SELECT lattice_mod(7.5, 2), lattice_mod(-7.5, 2), lattice_mod(5, -0.5), lattice_mod(NULL, 1)",
    "SELECT lattice_mod(1, NULL), lattice_mod(-9223372036854775808, -1), lattice_mod(-0.0, 5)",
    "SELECT lattice_mod(9223372036854775807, 10), lattice_mod(1e308, 3), lattice_mod(-1, 1e300)",
    "SELECT lattice_to_uuid('A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11'), "
    "lattice_to_uuid('a0eebc999c0b4ef8bb6d6bb9bd380a11'), lattice_to_uuid('nope'), "
    "lattice_to_uuid(NULL), lattice_to_uuid(12)",
    "SELECT lattice_to_datetime('2026-09-28 10:11:12', '%Y-%m-%d %H:%M:%S')",
    "SELECT lattice_to_datetime('28/09/2026', '%d/%m/%Y'), lattice_to_datetime('2026-9-8', '%Y-%m-%d')",
    "SELECT lattice_to_datetime('2026-09-28T10:11:12.5', '%Y-%m-%dT%H:%M:%S.%f')",
    "SELECT lattice_to_datetime('2026-09-28 10:11:12 +0130', '%Y-%m-%d %H:%M:%S %z')",
    "SELECT lattice_to_datetime('2026-09-28 10:11:12 Z', '%Y-%m-%d %H:%M:%S %z')",
    "SELECT lattice_to_datetime('2026-09-28 10:11:12 -01:30', '%Y-%m-%d %H:%M:%S %z')",
    "SELECT lattice_to_datetime('Sep 28 2026', '%b %d %Y'), lattice_to_datetime('september 28 26', '%B %d %y')",
    "SELECT lattice_to_datetime('Mon, 28 Sep 2026', '%a, %d %b %Y'), lattice_to_datetime('Monday 28', '%A %d')",
    "SELECT lattice_to_datetime('10:11 PM', '%I:%M %p'), lattice_to_datetime('12:11 am', '%I:%M %p')",
    "SELECT lattice_to_datetime('2026 272', '%Y %j'), lattice_to_datetime('69 01', '%y %m')",
    "SELECT lattice_to_datetime('2026-09-28 extra', '%Y-%m-%d'), lattice_to_datetime('2026-02-30', '%Y-%m-%d')",
    "SELECT lattice_to_datetime('2026-09-28', '%Y-%m-%d %Q'), lattice_to_datetime(20260928, '%Y%m%d')",
    "SELECT lattice_to_datetime('2026-09-28', 5), lattice_to_datetime(NULL, '%Y')",
    "SELECT lattice_to_datetime('20260928', '%Y%m%d'), lattice_to_datetime('2026   09', '%Y %m')",
    "SELECT lattice_to_datetime('100%', '%H%%'), lattice_to_datetime('2026.09', '%Y.%m')",
    "SELECT lattice_to_datetime(' 9', '%d'), lattice_to_datetime('2026-09-28T10', '%Y-%m-%dT%H')",
    "SELECT lattice_to_datetime('2026-09-28 10:11:61', '%Y-%m-%d %H:%M:%S')",
    "SELECT lattice_to_datetime('2026-09-28 10:11:60', '%Y-%m-%d %H:%M:%S')",
    "SELECT lattice_to_datetime('2026-09-28 10:11:12.1234567', '%Y-%m-%d %H:%M:%S.%f')",
    "SELECT lattice_to_datetime('2026-09-28 10:11:12 +01:30:15', '%Y-%m-%d %H:%M:%S %z')",
    "SELECT lattice_to_datetime('13 PM', '%I %p'), lattice_to_datetime('2026-09-28 (x)', '%Y-%m-%d (x)')",
    "SELECT lattice_to_datetime('SEP 2026', '%b %Y'), lattice_to_datetime('Sept 2026', '%b %Y')",
    "SELECT lattice_datetime_add('2026-09-28T10:11:12.000000Z', 1, 2, 3, 4)",
    "SELECT lattice_datetime_add('2026-09-28T10:11:12+02:00', -1, 0, 0, 0)",
    "SELECT lattice_datetime_add('2026-09-28', 0, 0, 0, -1), lattice_datetime_add('bad', 1, 1, 1, 1)",
    "SELECT lattice_datetime_add('2026-09-28', 1.5, 0, 0, 0), lattice_datetime_add('2026-09-28', '1', 0, 0, 0)",
    "SELECT lattice_datetime_add('9999-12-31T23:59:59', 0, 0, 0, 1), lattice_datetime_add('2026-01-01', 1000000000, 0, 0, 0)",
    "SELECT lattice_datetime_add('2026-01-01', 0, 0, 0, 9223372036854775807), lattice_datetime_add(NULL, 1, 1, 1, 1)",
    "SELECT lattice_datetime_add('2026-01-01', 0, 0, 0, -63745920000)",
    "SELECT lattice_datetime_diff('2026-09-28T10:00:00Z', '2026-09-27T09:00:00Z', 'days')",
    "SELECT lattice_datetime_diff('2026-09-27T09:00:00Z', '2026-09-28T10:00:00Z', 'hours')",
    "SELECT lattice_datetime_diff('2026-09-28T10:00:00Z', '2026-09-28T09:59:59.5Z', 'seconds'), "
    "lattice_datetime_diff('2026-09-28T09:59:59.5Z', '2026-09-28T10:00:00Z', 'seconds')",
    "SELECT lattice_datetime_diff('9999-12-31', '0001-01-01', 'minutes'), "
    "lattice_datetime_diff('2026-01-01', '2026-01-01', 'weeks'), lattice_datetime_diff('x', '2026-01-01', 'days')",
    "SELECT lattice_datetime_diff('2026-01-01', '2026-01-01', 5), lattice_datetime_diff(NULL, '2026-01-01', 'days')",
]
fns = []
for storage in (("canonical", "iso_z"), ("hex32", "sql_space")):
    P["UUID_STORAGE"], P["DATETIME_STORAGE"] = storage
    conn = sqlite3.connect(":memory:")
    for name, n, fn in [
        ("lattice_lower", 1, P["_lower"]),
        ("lattice_upper", 1, P["_upper"]),
        ("lattice_strip", 1, P["_strip"]),
        ("lattice_mod", 2, P["_mod"]),
        ("lattice_to_uuid", 1, P["_to_uuid"]),
        ("lattice_to_datetime", 2, P["_to_datetime"]),
        ("lattice_datetime_add", 5, P["_datetime_add"]),
        ("lattice_datetime_diff", 3, P["_datetime_diff"]),
    ]:
        conn.create_function(name, n, fn, deterministic=True)
    for sql in FN_SQL:
        try:
            row = conn.execute(sql).fetchone()
            res: object = [tag(v) for v in row]
        except sqlite3.Error as err:
            res = f"!{type(err).__name__}: {err}"
        fns.append({"storage": list(storage), "sql": sql, "row": res})
    conn.close()
P["UUID_STORAGE"], P["DATETIME_STORAGE"] = "canonical", "iso_z"
out["sqlfns"] = fns

# the text of a failing statement, as serve's 500 names it (f"{type(err).__name__}: {err}")
errs = []
conn = sqlite3.connect(":memory:")
conn.create_function("lattice_mod", 2, P["_mod"], deterministic=True)
conn.execute("CREATE TABLE t (x NOT NULL, u UNIQUE)")
for sql in [
    "SELECT * FROM nope",
    "SELECT lattice_mod('a', 1)",
    "SELEC 1",
    "INSERT INTO t VALUES (NULL, 1)",
    "INSERT INTO t VALUES (1, 1), (2, 1)",
    "SELECT lattice_mod(1)",
]:
    try:
        conn.execute(sql)
        errs.append({"sql": sql, "error": None})
    except sqlite3.Error as err:
        errs.append(
            {
                "sql": sql,
                "error": f"{type(err).__name__}: {err}",
                "unique": P["_is_unique_violation"](err),
            }
        )
conn.close()
out["sql_errors"] = errs

# stored spellings read back from columns declared DATE / DATETIME / TIMESTAMP / TEXT:
# Python's sqlite3 returns the stored value as it is; the wire value is _decode's
spellings = []
conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE s (k INTEGER PRIMARY KEY, d DATE, dt DATETIME, ts TIMESTAMP, x TEXT)")
for v in [
    "2026-09-28 10:11:12+02:00",
    "2026-09-28 10:11:12.000000",
    "2026-09-28 10:11:12",
    "2026-09-28T10:11:12.123456789Z",
    "2026-09-28T10:11:12.500Z",
    "2026-09-28T10:11:12.000000Z",
    "2026-09-28 10:11",
    "2026-09-28T10:11",
    "2026-09-28",
    "2026-09-28 1:02:03",
    "2026-9-28 10:11:12",
    "2026-09-28 10:11:12.5 +0000 UTC",
    "2026-09-28 10:11:12 +0200 +0200",
    "not a date",
    "123",
    "",
]:
    conn.execute("INSERT INTO s (d, dt, ts, x) VALUES (?, ?, ?, ?)", (v, v, v, v))
    row = conn.execute("SELECT d, dt, ts, x FROM s ORDER BY k DESC LIMIT 1").fetchone()
    spellings.append(
        {
            "in": v,
            "row": [tag(c) for c in row],
            "wire": [
                tag(P["_finite"](P["_decode"](row[0], "Date"))),
                *(tag(P["_finite"](P["_decode"](c, "DateTime"))) for c in row[1:]),
            ],
            "rows_json": P["rows_json"]((("d", "dt", "ts", "x"), [row]), ("d", "dt", "ts", "x")),
        }
    )
conn.close()
out["spellings"] = spellings


# the outbox: the constants the generator sets, and dispatch_outbox over a traced
# connection (statement count, outcome, the rows after it)
from bench.api_runner import _is_counted_statement  # noqa: E402
from lattice.api import outbox as outbox_module  # noqa: E402

CONSTS = {
    "OUTBOX_DDL": outbox_module.CREATE_SQL,
    "OUTBOX_COLUMNS": outbox_module.COLUMNS_SQL,
    "OUTBOX_ADD_CLAIMED": outbox_module.ADD_CLAIMED_SQL,
    "OUTBOX_CLAIM": outbox_module.CLAIM_SQL,
    "OUTBOX_DONE": outbox_module.MARK_DONE_SQL,
    "OUTBOX_FAILED": outbox_module.MARK_FAILED_SQL,
    "OUTBOX_EXISTS": outbox_module.EXISTS_SQL,
    "OUTBOX_RETRY": outbox_module.RETRY_SQL,
    "OUTBOX_RETRY_PENDING": outbox_module.RETRY_PENDING_SQL,
}
P.update(CONSTS)
counted = [0]
conn = sqlite3.connect(":memory:", isolation_level=None)
conn.set_trace_callback(lambda sql: counted.__setitem__(0, counted[0] + _is_counted_statement(sql)))
conn.execute(outbox_module.CREATE_SQL.replace(', "claimed_at" TEXT', ""))  # an old table
T0 = dt.datetime(2026, 9, 28, 10, 0, 0, 123456, tzinfo=dt.UTC)
ARGS = (
    '{"types": ["Str", "Int", "Uuid", "DateTime", "Bool", "Date", "Float"], "values": '
    '["ann", 1, "a0eebc999c0b4ef8bb6d6bb9bd380a11", "2026-09-28 10:00:00", 1, "2026-09-28", 2]}'
)
for native in ("send", "flaky", "missing"):
    conn.execute(
        'INSERT INTO "lattice_outbox" ("endpoint", "native", "args", "status", "error", '
        '"created_at", "attempts") VALUES (?, ?, ?, ?, NULL, ?, 0)',
        ("notify", native, ARGS, "pending", P["datetime_text"](T0)),
    )
calls: list[str] = []


def _send(*args: object) -> None:
    calls.append(repr(args))


def _flaky(*args: object) -> None:
    raise ValueError("flaky failed")


P["EFFECTS"].clear()
P["EFFECTS"].update({"send": _send, "flaky": _flaky})
OUTBOX_ROWS = (
    'SELECT "id", "native", "status", "error", "attempts", "claimed_at" '
    'FROM "lattice_outbox" ORDER BY "id"'
)
steps = []
pending = conn.execute('SELECT "id", "native", "args" FROM "lattice_outbox"').fetchall()
for label, now, rows in [
    ("dispatch", T0, pending),
    ("again within grace", T0 + dt.timedelta(seconds=10), pending),
    ("retry after grace", T0 + dt.timedelta(seconds=301), None),
]:
    if rows is None:
        cutoff = P["_outbox_cutoff"](now, P["OUTBOX_GRACE"])
        rows = conn.execute(P["OUTBOX_RETRY"], (cutoff,)).fetchall()
        P["EFFECTS"]["flaky"] = _send
    counted[0] = 0
    done, failed = P["dispatch_outbox"](conn, rows, now)
    steps.append(
        {
            "label": label,
            "now": P["datetime_text"](now),
            "rows": [[tag(v) for v in r] for r in rows],
            "queries": counted[0],
            "done": done,
            "failed": failed,
            "after": [[tag(v) for v in r] for r in conn.execute(OUTBOX_ROWS).fetchall()],
        }
    )
conn.close()
out["outbox"] = {
    "constants": CONSTS,
    "steps": steps,
    "calls": calls,
    "cutoff": P["_outbox_cutoff"](T0, 300),
}

# ---- wire values and JSON rendering ----------------------------------------------------------
wire = []
for style in ("iso_z", "naive"):
    P["DATETIME_WIRE"] = style
    for scalar, v in [
        ("DateTime", "2026-09-28T10:11:12.000000Z"),
        ("DateTime", "2026-09-28 10:11:12.500000"),
        ("DateTime", "2026-09-28T10:11:12+02:00"),
        ("DateTime", "2026-09-28T10:11:12.000001"),
        ("DateTime", "not a date"),
        ("DateTime", None),
        ("Uuid", "A0EEBC999C0B4EF8BB6D6BB9BD380A11"),
        ("Uuid", "junk"),
        ("Int", 3),
        ("Float", 3),
        ("Float", 1e16),
        ("Float", float("inf")),
        ("Float", float("nan")),
        ("Bool", 0),
        ("Bool", 2),
        ("Str", "é"),
        ("Date", "2026-09-28"),
    ]:
        decoded = P["_finite"](P["_decode"](v, scalar))
        wire.append({"style": style, "scalar": scalar, "in": tag(v), "out": tag(decoded)})
P["DATETIME_WIRE"] = "iso_z"
out["wire"] = wire

renders = []
for label, value in [
    ("text", {"s": 'é \x00\x1f\x7f"\\/\n\r\t\b\f 🎉 <>&'}),
    ("floats", [0.0, -0.0, 1.0, 1e16, 1e-5, 0.1 + 0.2, 123456789.0, 1.5e300, 5e-324, -2.5]),
    ("ints", [0, -1, 9223372036854775807, -9223372036854775808]),
    ("nested", {"items": [{"a": None, "b": True, "c": False}], "limit": 10, "offset": 0}),
    ("empty", {"items": [], "o": {}}),
]:
    renders.append(
        {
            "label": label,
            "value": json.loads(json.dumps(value)),
            "body": P["JSONResponse"](value).body.decode("utf-8"),
        }
    )
out["renders"] = renders

rj = []
for cols, rows, pick in [
    (("a", "b"), [(1, "x"), (2.5, None)], ("b", "a")),
    (("a",), [("é 🎉\x01",), (float("nan"),), (float("inf"),), (-0.0,), (1e16,)], ("a",)),
    (("a",), [], ("a",)),
]:
    rj.append(
        {
            "cols": list(cols),
            "rows": [[tag(v) for v in r] for r in rows],
            "pick": list(pick),
            "out": P["rows_json"]((cols, rows), pick),
        }
    )
out["rows_json"] = rj

# the HTTP status phrases the "fastapi" style uses
from http import HTTPStatus  # noqa: E402

out["phrases"] = {str(s.value): s.phrase for s in HTTPStatus}

# the located 500 message of a T2 runtime error
out["internal"] = [
    {"detail": d, "body": response_text("lattice", 500, "internal", d[:500])}
    for d in ["3:4: error R620: procedure ran out of fuel", "é" * 600]
]

dest = Path(__file__).with_name("prelude_answers.json")
dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
print(f"wrote {dest} ({len(results)} parameter cases)")
