"""L3 (spec 2026-09-29 ui-a §5.1 claims 2 and 3) over real HTTP, for an admitted caller."""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any

import pytest

from lattice.api.ir import EndpointPlan
from lattice.catalog import LIST_KIND
from lattice.check.api_info import ParamSpec
from lattice.tsgen import generate_ts
from lattice.tsgen.emit import page_num, page_shape
from lattice.tsgen.pyref import answer, case
from lattice.tsgen.specs import plan_specs
from tests.tsgen.gate.apps import ADMIN_TOKENS, APP_NAMES, RESET_PATH, app, serve
from tests.tsgen.gate.corpus import typed_corpus
from tests.tsgen.node import node_json, run_node

DRIVER = Path(__file__).resolve().parent / "gate" / "http_driver.ts"


def _run(name: str, tmp: Path, cases: list[dict], token: str | None) -> list[dict]:
    client = tmp / "client"
    client.mkdir()
    for fname, text in generate_ts(app(name).plans).items():
        (client / fname).write_text(text)
    server = tmp / "server"
    server.mkdir()
    with serve(name, server) as base:
        done = run_node([str(DRIVER), client.as_uri(), base, token or "", RESET_PATH], cwd=tmp,
                        stdin=json.dumps(cases, allow_nan=False))  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout)


# ---- claim 3, lossless: the decoded value against Python's json.loads of the raw body --------

_MISMATCH = object()


def _int(raw: object) -> bool:
    return isinstance(raw, int) and not isinstance(raw, bool)


def _same_float(got: object, raw: object) -> bool:
    """`got` is the client's number ({"$f": String(x)}); `raw` the server's JSON number."""
    if not (isinstance(got, dict) and set(got) == {"$f"}) or not (_int(raw) or type(raw) is float):
        return False
    try:
        want = float(raw)  # a Float column's value as a double (what the server holds)
    except OverflowError:
        return False
    x = float(got["$f"])
    return x == want and math.copysign(1, x) == math.copysign(1, want)


def _same_scalar(got: object, raw: object, scalar: str) -> bool:
    if raw is None:
        return got is None
    if scalar == "Int":
        return _int(raw) and got == {"$int": str(raw)}
    if scalar == "Float":
        return _same_float(got, raw)
    if scalar == "Bool":
        return type(raw) is bool and got is raw
    return type(raw) is str and type(got) is str and got == raw


def _same_row(got: object, raw: object, cols: tuple[tuple[str, Any], ...]) -> bool:
    names = [n for n, _s in cols]
    return (
        isinstance(got, dict) and isinstance(raw, dict)
        and sorted(got) == sorted(raw) == sorted(names)
        and all(_same_scalar(got[n], raw[n], s.value) for n, s in cols)
    )  # fmt: skip


def lossless(plan: EndpointPlan, got: object, body: bytes) -> bool:
    """The decoded success value equals the server's body, read exactly (Python's json:
    ints unbounded, floats as doubles): every Int a bigint of the same value, every Float the
    same double (sign of zero included), every string, bool and null the same; rows,
    lists and page envelopes with exactly the server's keys."""
    if plan.success == "NoContent":
        return got is None and body == b""
    raw = json.loads(body)
    cols = plan.schema.columns
    if plan.success == "Page":
        shape = page_shape(plan)
        if not (isinstance(got, dict) and isinstance(raw, dict)):
            return False
        if sorted(got) != sorted(raw) or sorted(raw) != sorted(f for _k, f in shape):
            return False
        for key, field in shape:
            g, r = got[field], raw[field]
            if key == "items":
                ok = isinstance(g, list) and isinstance(r, list) and len(g) == len(r)
                ok = ok and all(_same_row(a, b, cols) for a, b in zip(g, r, strict=False))
            elif page_num(plan, key) == "bigint":
                ok = _int(r) and g == {"$int": str(r)}
            else:
                ok = _int(r) and g == {"$f": str(r)}
            if not ok:
                return False
        return True
    if plan.success == LIST_KIND:
        return (
            isinstance(got, list) and isinstance(raw, list) and len(got) == len(raw)
            and all(_same_row(a, b, cols) for a, b in zip(got, raw, strict=False))
        )  # fmt: skip
    return _same_row(got, raw, cols)


def check(name: str, cases: list[dict], results: list[dict]) -> tuple[list, dict[str, int]]:
    """(mismatches, counts) of claims 2 and 3 over one app's results."""
    plans = {p.name: p for p in app(name).plans}
    bad: list = []
    counts = {"cases": len(cases), "compared": 0}
    for c, r in zip(cases, results, strict=True):
        err = r.get("error") or {}
        status = None if r["ok"] else err.get("status")
        server_422 = not r["ok"] and status == 422
        if bool(r["local"]) != server_422:
            bad.append((c["name"], "claim 2", r["local"], err))
        elif server_422 and r["local"] != err.get("detail"):
            bad.append((c["name"], "claim 2 detail", r["local"], err.get("detail")))
        unexpected = not r["ok"] and err.get("kind") == "unexpected"
        if unexpected and (err.get("reason") == "decode" or err.get("status", 0) < 500):
            bad.append((c["name"], "claim 3", err))
        if r["ok"]:
            counts["compared"] += 1
            body = base64.b64decode(r["raw"])
            if not lossless(plans[c["endpoint"]], r.get("value"), body):
                bad.append((c["name"], "claim 3 lossless", r.get("value"), body[:300]))
    return bad, counts


def unadmitted(name: str, cases: list[dict], results: list[dict]) -> list[str]:
    """Guarded endpoints with cases where every case was answered 401 or 403."""
    guarded = {p.name for p in app(name).plans if p.auth != "Public"}
    seen: dict[str, bool] = {}
    for c, r in zip(cases, results, strict=True):
        if c["endpoint"] in guarded:
            status = None if r["ok"] else (r.get("error") or {}).get("status")
            seen[c["endpoint"]] = seen.get(c["endpoint"], False) or status not in (401, 403)
    return sorted(e for e, ok in seen.items() if not ok)


def test_every_app_has_an_admin_token() -> None:
    assert set(ADMIN_TOKENS) == set(APP_NAMES)


@pytest.mark.parametrize("name", APP_NAMES)
def test_http_differential(name: str, tmp_path: Path) -> None:
    cases = typed_corpus(app(name))
    results = _run(name, tmp_path, cases, ADMIN_TOKENS[name])
    assert unadmitted(name, cases, results) == [], "the gate's caller is never admitted there"
    bad, _counts = check(name, cases, results)
    assert not bad, bad[:20]


def test_unadmitted_caller_gets_auth_error_first(tmp_path: Path) -> None:
    """Review Focus 1 / spec delta 1: the server authenticates before it validates."""
    name = "realapps"

    def int_query(s: ParamSpec) -> bool:
        return s.source == "query" and s.scalar is not None and s.scalar.value == "Int"

    plan = next(p for p in app(name).plans if p.auth != "Public" and any(map(int_query, p.params)))
    param = next(s.name for s in plan.params if int_query(s))
    cases = [{"name": "bad token, bad param", "endpoint": plan.name,
              "params": {param: {"t": "json", "v": "not-a-number"}}}]  # fmt: skip
    [r] = _run(name, tmp_path, cases, "no-such-token")
    assert r["local"] != []  # the client alone says 422
    assert r["error"]["status"] == 401  # the server answers the auth error first


UNROUTABLE = """
import { readFileSync } from "node:fs";
const { dir, endpoint, param, values } = JSON.parse(readFileSync(0, "utf8"));
const api = await import(dir + "/api.ts");
const rt = await import(dir + "/lattice-rt.ts");
const sent = [];
const spy = async (url) => { sent.push(String(url)); return new Response("{}", { status: 200 }); };
const ep = api.ENDPOINTS[endpoint];
const out = [];
for (const v of values) {
  const p = v === null ? {} : { [param]: v };
  const r = await rt.call(ep, p, { baseUrl: "http://x", token: "t", fetch: spy });
  out.push({ result: r, local: rt.validate(ep, p) });
}
console.log(JSON.stringify({ sent, out }));
"""


def test_unroutable_path_values_are_never_sent(tmp_path: Path) -> None:
    """Final review #3 / R-a: a path value that is "", ".", "..", contains "/" or is missing
    is refused locally: call() answers Unexpected "unroutable" (status 0, empty body) and
    sends nothing; validate() reports what the server's parse_params would (nothing for a
    string path value, which the server cannot tell from any other)."""
    name = "realapps"
    plan = next(p for p in app(name).plans if "{" in p.path)
    param = next(s for s in plan.params if s.source == "path")
    client = tmp_path / "client"
    client.mkdir()
    for fname, text in generate_ts(app(name).plans).items():
        (client / fname).write_text(text)
    values = ["", ".", "..", "a/b", "/", None]
    got = node_json(UNROUTABLE, {"dir": client.as_uri(), "endpoint": plan.name,
                                 "param": param.name, "values": values}, cwd=tmp_path)  # fmt: skip
    assert got["sent"] == []
    want = {"kind": "unexpected", "status": 0, "reason": "unroutable", "body": ""}
    assert [o["result"] for o in got["out"]] == [{"ok": False, "error": want}] * len(values)
    for v, o in zip(values, got["out"], strict=True):
        c = case(v or "", plan_specs(plan), path={param.name: v or ""})
        done = answer(c)
        assert o["local"] == (json.loads(done["lattice"])["detail"] if "status" in done else [])
