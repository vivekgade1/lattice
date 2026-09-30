# ruff: noqa: E501
"""Generate ts_answers.json: Python answers for the JS hazards of spec 2026-09-29 ui-a §4.

Run from the repository root: python lattice/tsgen/rt/testdata/gen_ts_answers.py
Every params case carries the API_PARAM_RULES ids it pins (tests/tsgen/test_governance.py
fails when a rule has no case)."""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bench" / "api_kit"))  # the prelude imports platform_kit
sys.path.insert(0, str(ROOT))

from starlette.datastructures import QueryParams  # noqa: E402

from lattice.tsgen.pyref import answer, case, pattern_map  # noqa: E402

HERE = Path(__file__).resolve().parent
GO = json.loads((ROOT / "lattice/gogen/rt/testdata/prelude_answers.json").read_text())


def sp(name: str, source: str, scalar: str, **kw: object) -> dict[str, object]:
    return {"name": name, "source": source, "scalar": scalar, **kw}


cases: list[dict[str, object]] = []


def add(name: str, specs: list[dict[str, object]], rules: tuple[str, ...], **kw: object) -> None:
    cases.append(case(name, specs, rules=rules, **kw))  # type: ignore[arg-type]


LEN = sp("s", "query", "Str", min_len=2, max_len=3)
for q in ["s=%F0%9F%91%8B%F0%9F%91%8B", "s=%F0%9F%91%8B", "s=ab%F0%9F%91%8B",
          "s=abcd", "s=%F0%9F%91%8Ba%F0%9F%91%8Bb", "s=%E2%80%8B%E2%80%8B"]:  # fmt: skip
    add(f"len {q}", [LEN], ("P1",), query=q)
BODY_S = sp("s", "body", "Str", max_len=5)
for b in [b'{"s": "\\ud800"}', b'{"s": "a\\ud83d\\ude00"}', b'{"s": "\\udc00\\ud800"}',
          b'{"s": "\\ud83d"}', '{"s": "\U0001f600\U0001f600"}'.encode(),
          b'{"s": "\xed\xa0\x80"}']:  # fmt: skip
    add(f"surrogate {b!r}", [BODY_S], ("P1", "P2", "P12"), body=b)
INT_Q, FLOAT_Q, BOOL_Q = (
    sp("n", "query", "Int"),
    sp("x", "query", "Float"),
    sp("b", "query", "Bool"),
)
for pad in ["%1C", "%1F", "%C2%85", "%EF%BB%BF", "%E3%80%80", "%C2%A0", "%0B", "%E2%80%8B"]:
    add(f"strip int {pad}", [INT_Q], ("P3",), query=f"n={pad}5{pad}")
    add(f"strip float {pad}", [FLOAT_Q], ("P3",), query=f"x={pad}1.5")
    add(f"strip bool {pad}", [BOOL_Q], ("P3", "P11"), query=f"b={pad}true")
INT_B = sp("n", "body", "Int")
for b in [b'{"n": 9007199254740993}', b'{"n": 9007199254740993.0}', b'{"n": 3.0}',
          b'{"n": -0.0}', b'{"n": 1e300}', b'{"n": 9223372036854775808}',
          b'{"n": -9223372036854775809}', b'{"n": 1.5}', b'{"n": true}', b'{"n": "5"}',
          b'{"n": NaN}', b'{"n": ' + b"9" * 4301 + b"}"]:  # fmt: skip
    add(f"int body {b[:40]!r}", [INT_B], ("P4", "P12"), body=b)
for q in ["n=9007199254740993", "n=-9223372036854775808", "n=00009", "n=+0", "n=-0"]:
    add(f"int text {q}", [INT_Q], ("P4",), query=q)
FLOAT_B = sp("x", "body", "Float")
for b in [b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}', b'{"x": 1e400}',
          b'{"x": 1' + b"0" * 400 + b"}", b'{"x": 9007199254740993}', b'{"x": -0.0}',
          b'{"x": 1e-400}']:  # fmt: skip
    add(f"float body {b[:40]!r}", [FLOAT_B], ("P5", "P12"), body=b)
for q in ["x=nan", "x=inf", "x=1e400", "x=5.", "x=.5", "x=+.5", "x=1E5", "x=1e-400"]:
    add(f"float text {q}", [FLOAT_Q], ("P5",), query=q)
for ge, le, x, n in [(2.5e-5, None, "0", "0"), (None, 1.0000000000000002, "2", "2"),
                     (1e16, None, "0", "0"), (1e22, None, "0", "0"), (0.1, None, "0", "0"),
                     (-1.5e-7, None, "-1", "-1"),
                     (None, 123456789012345680.0, "1e18", "123456789012345681")]:  # fmt: skip
    kw = {k: v for k, v in (("ge", ge), ("le", le)) if v is not None}
    add(f"message float {kw}", [sp("x", "query", "Float", **kw)], ("P6",), query=f"x={x}")
    add(f"message int {kw}", [sp("n", "query", "Int", **kw)], ("P6",), query=f"n={n}")
ONE = sp("s", "query", "Str", one_of=["it's", 'say "hi"', "both'\"", "tab\t", "é", "\x7f",
                                      "​", "\U0001f600", "back\\slash", "퟿"])  # fmt: skip
for q in ["s=zz", "s=%C3%A9", "s=tab%09"]:
    add(f"one_of {q}", [ONE], ("P8",), query=q)
for pat, qs in [
    (r"a$", ["a", "a%0A"]), (r"a.b", ["a%0Ab", "axb"]), (r"\s+", ["%C2%85", "%C2%A0", "%0B", "%20"]),
    (r"\w+", ["%C3%A9", "a_1"]), (r"\bfoo\b", ["foo"]), (r"(?i)k", ["%E2%84%AA", "K"]),
    (r"(?i)s", ["%C5%BF", "S"]), (r"[^a]", ["%0A", "b"]), (r"\d", ["%D9%A3", "3"]),
    (r"(?s).+", ["a%0Ab"]), (r"(?m)a$\nb", ["a%0Ab"]), (r"it's\d", ["it's5"]),
]:  # fmt: skip
    for q in qs:
        add(f"pattern {pat!r} {q}", [sp("s", "query", "Str", pattern=pat)], ("P7",), query=f"s={q}")
for q in ["t=2026-09-28T10:11:12.1234567", "t=2026-09-28T24:00", "t=2026-02-28T24:00",
          "t=0001-01-01T00:00:00%2B01:00", "t=9999-12-31T23:59:59-01:00", "t=2026-W53-1",
          "t=2020-W53-7T01", "t=20260928T101112", "t=2026-09-28%E2%80%8310:00",
          "t=2026-01-29_24%2B05:60", "t=2026-01-29T10%2B05:30:60", "t=2026-01-29T10%2B24:00",
          "t=2026-01-29T10%2B23:59:59.999999", "t=2026-01-29T10-23:59:59.999999",
          "t=1900-12-28T23-00:00:00.123456", "t=1900-12-28T23%2B00:00:00.5",
          "t=1900-12-28T23-00:00:01.5", "t=1900-12-28T23:59:59.123456-00:00:00.123456",
          "t=1900-00-00x24", "t=1900-00-00x10", "t=1900-01-00x24", "t=1900-00-01x24",
          "t=1900-00-00x24:00:01", "t=1900-13-01x24", "t=1900-01-00x10"]:  # fmt: skip
    add(f"datetime {q}", [sp("t", "query", "DateTime")], ("P9",), query=q)
for q in ["d=2026-09-28", "d=20260928", "d=2026-W01-1", "d=2026-02-30", "d=2026-09-28T00"]:
    add(f"date {q}", [sp("d", "query", "Date")], ("P9",), query=q)
add("datetime surrogate body", [sp("t", "body", "DateTime")], ("P2", "P9"),
    body=b'{"t": "2026-09-28\\ud80010:00"}')  # fmt: skip
for q in [
    "u=0123456789ABCDEF0123456789ABCDEF",
    "u=01234567-89ab-cdef-0123-456789abcdef",
    "u=01234567-89abcdef-0123-456789abcdef",
    "u=0123456789abcdef0123456789abcde",
]:
    add(f"uuid {q}", [sp("u", "query", "Uuid")], ("P10",), query=q)
for q in ["b=TRUE", "b=Yes", "b=oN", "b=0", "b=OFF", "b=maybe", "b=", "b=%C4%B0"]:
    add(f"bool {q}", [BOOL_Q], ("P11",), query=q)
TWO = [sp("a", "query", "Int", ge=1.0), sp("b", "body", "Str", max_len=1), sp("c", "path", "Uuid")]
add("detail order", TWO, ("P13",), path={"c": "nope"}, query="a=0", body=b'{"b": "xy"}')
add("detail first failure", [sp("s", "query", "Str", min_len=5, pattern="[a-z]+")], ("P13",),
    query="s=A")  # fmt: skip
add("body not object", TWO, ("P13", "P12"), path={"c": "x"}, body=b"[1]")
OPT = sp("s", "body", "Str", default={"t": "unset"}, optional=True)
OPT_NULL = sp("s", "body", "Str", default={"t": "unset"}, optional=True, nullable=True)
DEF_NONE = sp("s", "body", "Str", default={"t": "none"})
DEF_INT = sp("n", "query", "Int", default={"t": "int", "v": "7"})
for specs, b in [([OPT], b'{"s": null}'), ([OPT], b"{}"), ([OPT_NULL], b'{"s": null}'),
                 ([DEF_NONE], b'{"s": null}'), ([DEF_NONE], b"")]:  # fmt: skip
    add(f"null {specs[0].get('nullable')} {b!r}", specs, ("P14",), body=b)
add("default int", [DEF_INT], ("P14",), query="")
FORM = [sp("s", "body", "Str", max_len=1), sp("n", "body", "Int")]
add("form hazards", FORM, ("P1", "P3", "P12"), body=b"s=%F0%9F%91%8B&n=%1C5", form=True,
    ctype="application/x-www-form-urlencoded; charset=utf-8")  # fmt: skip

params = [answer(c) for c in cases]

floats = []
for x in [0.1, 1e16, 1e-5, 1.5e-7, 123456789012345680.0, 1e22, 5e-324, 1.7976931348623157e308,
          -0.0, 0.0, 2.0**53, 2.0**53 + 2, 1 / 3, 100.0, 1e15, 9.999999999999999e15]:  # fmt: skip
    floats.append({"bits": struct.pack(">d", x).hex(), "repr": repr(x)})
strs = []
for s in ["it's", 'say "hi"', "both'\"", "\x00\x1f\x7f\x80\x9f\xa0\xad", "​ 　",
          "\ud800", "\U0001f600", "\U000e0001", "\x1c5\x1f", "\x85x\xa0", "﻿x", " \t\n"]:  # fmt: skip
    strs.append({"s": s, "repr": repr(s), "strip": s.strip(), "len": len(s)})


def jtag(v: object) -> object:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return {"int": str(v)}
    if isinstance(v, float):
        return {"float": repr(v)}
    if isinstance(v, str):
        return {"str": v}
    if isinstance(v, list):
        return [jtag(x) for x in v]
    return {"obj": [[k, jtag(x)] for k, x in v.items()]}  # type: ignore[union-attr]


json_cases = []
for raw in [b'{"a": 1}', b"\xef\xbb\xbf{}", '{"a": "é"}'.encode("utf-16"),
            '{"a": "é"}'.encode("utf-16-le"), '{"a": "é"}'.encode("utf-16-be"),
            '{"a": 1}'.encode("utf-32"), '{"a": 1}'.encode("utf-32-le"), b'"\xed\xa0\x80"',
            b"NaN", b"-Infinity", b"[1, 1.0, -0, -0.0, 1e2, 1E+2]", b"01", b"1.", b"-",
            b'"a\x01"', b'{"a": 1, "a": 2, "b": 3}', b'"\\ud800"', b'"\\ud83d\\ude00"',
            b" \t\n\r1\r\n", b"1 2", b"\x0c1", b"9" * 4301, b"[" * 50 + b"]" * 50,
            b'"\\/"', b'"\\x"', b"\xff"]:  # fmt: skip
    try:
        value: object = {"ok": jtag(json.loads(raw))}
    except ValueError:
        value = {"error": True}
    json_cases.append({"b64": base64.b64encode(raw).decode(), "value": value})

qs = []
for raw in ["a=1&b=2", "a=1&a=2", "a", "a=", "=1", "a=%zz", "a=%C3", "a=%C3%A9", "a+b=c+d",
            "a=\xe9", "&&a=1&&", "a=%E2%82", "a=%F0%9F%91%8B", "a=1;b=2"]:  # fmt: skip
    qs.append({"raw": raw, "pairs": dict(QueryParams(raw))})

out = {
    "python": sys.version.split()[0],
    "params": params,
    "patterns": pattern_map(params, GO["params"]),
    "floats": floats,
    "strs": strs,
    "json": json_cases,
    "qs": qs,
}
(HERE / "ts_answers.json").write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n")
print(f"wrote ts_answers.json ({len(params)} parameter cases)")
