"""api.ts: generated for every proof program; its validation equals Python's on the wire it
builds; responses that drift from the schema are decode errors (Review Focus 5)."""

from __future__ import annotations

import base64
import dataclasses
import json
from pathlib import Path

import pytest

from lattice.api.ir import EndpointPlan, lower_api
from lattice.check import check_source
from lattice.tsgen import generate_ts
from lattice.tsgen.emit import TsGenRefusal, camel, error_statuses, pascal, ts_spec
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


# ---- review round 1 --------------------------------------------------------------------------


def test_error_statuses_include_the_generic_unique_409() -> None:
    by_name = {p.name: p for p in plans_of("realapps")}
    assert error_statuses(by_name["update_user_me"]) == [401, 403, 404, 409, 422]
    assert error_statuses(by_name["register_user"]) == [404, 409, 422]
    assert error_statuses(by_name["delete_item"]) == [401, 403, 404, 422]  # a delete: no 409
    assert error_statuses(by_name["read_items"]) == [401, 403, 422]


def renamed(plan: EndpointPlan, name: str, response: str | None = None) -> EndpointPlan:
    return dataclasses.replace(plan, name=name, response=response or plan.response)


@pytest.mark.parametrize(
    ("names", "response"),
    [
        (["delete"], None),  # a reserved word as the function name
        (["new"], None),
        (["default"], None),
        (["switch"], None),
        (["get_item", "getItem"], None),  # camel names collide
        (["ENDPOINTS"], None),  # the registry
        (["rt"], None),  # the runtime import
        (["GET"], None),  # its const and its function are both GET
        (["a_b", "A_B"], None),  # the consts collide
        (["x"], "XParams"),  # a model named like a generated type
        (["x"], "Result"),  # a model named like a re-exported runtime type
        (["x"], "string"),  # a predefined type name
        (["x", "x"], None),  # two endpoints, one name
        (["query"], None),  # its hook is the imported useQuery
        (["mutation"], None),  # its hook is the imported useMutation
    ],
)
def test_invalid_or_colliding_names_are_l893(names: list[str], response: str | None) -> None:
    base = plans_of("realapps")[1]  # read_item: a response model (ItemPublic)
    plans = tuple(renamed(base, n, response) for n in names)
    with pytest.raises(TsGenRefusal, match="L893"):
        generate_ts(plans)


def test_valid_names_are_emitted() -> None:
    base = plans_of("realapps")[1]
    api = generate_ts((renamed(base, "delete_it"), renamed(base, "type")))["api.ts"]
    assert "export function deleteIt(" in api and "export function type(" in api


IMPORT_ALL = """
import { readFileSync } from "node:fs";
const dirs = JSON.parse(readFileSync(0, "utf8"));
const out = {};
for (const [pid, dir] of Object.entries(dirs)) {
  try { out[pid] = Object.keys((await import(dir + "/api.ts")).ENDPOINTS); }
  catch (e) { out[pid] = String(e); }
}
console.log(JSON.stringify(out));
"""


def test_every_program_api_ts_imports(tmp_path: Path) -> None:
    dirs, names = {}, {}
    for pid in sorted(PROGRAMS):
        plans = plans_of(pid)
        (tmp_path / pid).mkdir()
        write(tmp_path / pid, plans)
        dirs[pid] = (tmp_path / pid).as_uri()
        names[pid] = [p.name for p in plans]
    assert node_json(IMPORT_ALL, dirs, cwd=tmp_path) == names


LIST_DRIFT = """
import { readFileSync } from "node:fs";
const dir = JSON.parse(readFileSync(0, "utf8"));
const rt = await import(dir + "/lattice-rt.ts");
const cols = [["id", "Int", false], ["name", "Str", true]];
const enc = (s) => new TextEncoder().encode(s);
const tryList = (s) => { try { rt.decodeList(rt.decodeJson(enc(s)), cols); return "ok"; }
                         catch (e) { return e instanceof rt.DecodeError ? "decode" : "crash"; } };
console.log(JSON.stringify([
  tryList('[]'), tryList('[{"id": 1, "name": null}, {"id": 2, "name": "b"}]'),
  tryList('{"id": 1, "name": null}'), tryList('null'), tryList('{"items": []}'),
  tryList('[{"id": 1, "name": null}, {"id": 2}]'), tryList('[1]'), tryList('[[1, null]]'),
]));
"""


def test_list_drift_is_a_decode_error(tmp_path: Path) -> None:
    write(tmp_path, plans_of("realapps"))
    got = node_json(LIST_DRIFT, tmp_path.as_uri(), cwd=tmp_path)
    assert got == ["ok", "ok", "decode", "decode", "decode", "decode", "decode", "decode"]


CALL_EDGES = """
import { readFileSync } from "node:fs";
const dir = JSON.parse(readFileSync(0, "utf8"));
const api = await import(dir + "/api.ts");
const rt = await import(dir + "/lattice-rt.ts");
const broken = new ReadableStream({ start(c) { c.error(new Error("drop")); } });
const dropped = async () => new Response(broken, { status: 200 });
const refused = async () => { throw new TypeError("fetch failed"); };
const opts = (f) => ({ baseUrl: "http://x", token: "t", fetch: f });
const mid = await api.readItems({}, opts(dropped));
const net = await api.readItems({}, opts(refused));
console.log(JSON.stringify({
  mid: [mid.ok, mid.error.kind, mid.error.reason, mid.error.status],
  net: [net.ok, net.error.kind, net.error.reason, net.error.status],
  keys: [0, -0, { a: -0 }, { a: 0 }].map(rt.canonicalKey),
}));
"""


def test_call_never_rejects_and_zero_keys_differ(tmp_path: Path) -> None:
    write(tmp_path, plans_of("realapps"))
    got = node_json(CALL_EDGES, tmp_path.as_uri(), cwd=tmp_path)
    assert got["mid"] == [False, "unexpected", "network", 200]
    assert got["net"] == [False, "unexpected", "network", 0]
    zero, neg, obj_neg, obj_zero = got["keys"]
    assert zero != neg and obj_neg != obj_zero
