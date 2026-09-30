"""api.ts: generated for every proof program; its validation equals Python's on the wire it
builds; responses that drift from the schema are decode errors (Review Focus 5)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from lattice.api.ir import EndpointPlan, lower_api
from lattice.check import check_source
from lattice.tsgen import generate_ts
from lattice.tsgen.emit import TsGenRefusal, camel, pascal, ts_spec
from lattice.tsgen.pyref import answer, case
from lattice.tsgen.specs import plan_specs
from tests.api.test_codegen_differential import PROGRAMS, setup_of
from tests.tsgen.node import node_json


def plans_of(pid: str) -> tuple[EndpointPlan, ...]:
    source, _task = PROGRAMS[pid]
    result = check_source(source, setup_of(pid).profile)
    assert result.program is not None
    return tuple(lower_api(result.program))


def write(tmp: Path, plans: tuple[EndpointPlan, ...]) -> Path:
    for name, text in generate_ts(plans).items():
        (tmp / name).write_text(text)
    return tmp


@pytest.mark.parametrize("pid", sorted(PROGRAMS))
def test_emits_every_endpoint(pid: str) -> None:
    plans = plans_of(pid)
    api = generate_ts(plans)["api.ts"]
    for plan in plans:
        assert f"export function validate{pascal(plan.name)}(" in api
        assert f"export function {camel(plan.name)}(" in api


SCRIPT = """
import { readFileSync } from "node:fs";
const { dir, calls } = JSON.parse(readFileSync(0, "utf8"));
const api = await import(dir + "/api.ts");
const rt = await import(dir + "/lattice-rt.ts");
console.log(JSON.stringify(calls.map(([name, params]) => {
  const ep = api.ENDPOINTS[name];
  const w = rt.toWire(ep, params);
  return { detail: rt.validate(ep, params), path: Object.fromEntries(w.pathValues), query: w.query,
           body: w.body === null ? "" : Buffer.from(w.body).toString("base64"), ctype: w.ctype };
})));
"""


@pytest.mark.parametrize("pid", ["realapps", "gaps", "t2"])
def test_empty_request_matches_python(pid: str, tmp_path: Path) -> None:
    plans = plans_of(pid)
    write(tmp_path, plans)
    calls = [[p.name, {}] for p in plans]
    out = node_json(SCRIPT, {"dir": tmp_path.as_uri(), "calls": calls}, cwd=tmp_path)
    for plan, got in zip(plans, out, strict=True):
        c = case(plan.name, plan_specs(plan), path=got["path"], query=got["query"],
                 body=base64.b64decode(got["body"]), form=plan.body == "form",
                 ctype=got["ctype"])  # fmt: skip
        want = answer(c)
        want_detail = json.loads(want["lattice"])["detail"] if "status" in want else []
        assert got["detail"] == want_detail, plan.name


DRIFT = """
import { readFileSync } from "node:fs";
const dir = JSON.parse(readFileSync(0, "utf8"));
const rt = await import(dir + "/lattice-rt.ts");
const cols = [["id", "Int", false], ["name", "Str", true]];
const enc = (s) => new TextEncoder().encode(s);
const tryRow = (s) => { try { rt.decodeRow(rt.decodeJson(enc(s)), cols); return "ok"; }
                        catch (e) { return e instanceof rt.DecodeError ? "decode" : "crash"; } };
console.log(JSON.stringify([
  tryRow('{"id": 1, "name": null}'), tryRow('{"id": 1}'), tryRow('{"id": 1, "name": "a", "x": 2}'),
  tryRow('{"id": 1.0, "name": "a"}'), tryRow('{"id": null, "name": "a"}'),
  tryRow('{"id": 9223372036854775807, "name": "a"}'),
]));
"""


def test_response_drift_is_a_decode_error(tmp_path: Path) -> None:
    write(tmp_path, plans_of("realapps"))
    got = node_json(DRIFT, tmp_path.as_uri(), cwd=tmp_path)
    assert got == ["ok", "decode", "decode", "decode", "decode", "ok"]


SURROGATE = """
import { readFileSync } from "node:fs";
const dir = JSON.parse(readFileSync(0, "utf8"));
const rt = await import(dir + "/lattice-rt.ts");
const ep = { method: "GET", path: "/x/{u}", form: false, success: 200, errors: {},
  errorStyle: "lattice", numberInts: [], decode: () => undefined,
  params: [{ name: "u", source: "path", scalar: "Uuid" },
           { name: "t", source: "query", scalar: "DateTime" }] };
const p = { u: "0123456789abcdef0123456789abcde\\ud800", t: "2026-09-28T10:00\\ud800" };
const w = rt.toWire(ep, p);
console.log(JSON.stringify({ path: w.url, detail: rt.validate(ep, p) }));
"""


def test_lone_surrogates_in_path_and_query(tmp_path: Path) -> None:
    write(tmp_path, plans_of("realapps"))
    got = node_json(SURROGATE, tmp_path.as_uri(), cwd=tmp_path)
    assert "%EF%BF%BD" in got["path"]
    assert [d["loc"] for d in got["detail"]] == [["path", "u"], ["query", "t"]]


def test_untranslatable_pattern_is_l893() -> None:
    plan = plans_of("realapps")[0]
    with pytest.raises(TsGenRefusal, match="L893"):
        ts_spec({"name": "s", "source": "query", "scalar": "Str", "pattern": "(a)\\1"}, plan)
