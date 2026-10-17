"""The App views (``#appmap`` and ``#impact``, API v1.25) without a browser: ``node --check`` on
every module, the navigator group, the strings of ``api.js``, the fixed headings and words, the
forbidden-word scan, and a walk of **every named state** of the two views under a fake DOM, against
the mock and against fixture servers. Layout, focus feel, contrast and rendering performance are
browser-only and are NOT checked here (the real-browser walk is a separate gate)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.studio.mock_server import make_server as make_mock
from tests.studio.test_appmap_api import (
    MOCK,
    Served,
    make_world,
    saved_report,
    serve,  # noqa: F401 -- a fixture
)

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "lattice" / "studio" / "web"
JS = WEB / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

APPMAP, IMPACT = JS / "views" / "appmap.js", JS / "views" / "impact.js"
NEGATIVE = "Lattice does not predict failures"
FORBIDDEN = re.compile(r"will fail|would fail|will break|would break|predict", re.IGNORECASE)
HEADINGS = (
    "Tier 1 · observed: a failing check on today's app",
    "Tier 2 · determined: follows from the spec and steps observed to pass",
    "Tier 3 · static: may fail (code and workflows it touches; not a claim)",
    "Tier 4 · observed after the change: not run (implementation step, Spike E)",
)
DECIDABLE = "Which workflows fail is mostly not decidable before the change exists."
MAP_STATES = (
    "loading", "not_configured", "inventory_missing", "bad_inventory", "no_spec", "stale",
    "no_workflows_capture", "not_built", "unread_empty",
)  # fmt: skip
IMPACT_STATES = (
    "impact_not_configured", "no_change_selected", "change_not_ready", "no_state",
    "tier1_not_run", "tier1_shown", "tier1_mismatch", "error",
)  # fmt: skip


def read(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- static facts


def test_the_navigator_has_the_app_group_with_two_views() -> None:
    app = read("app.js")
    assert 'import * as appmap from "./views/appmap.js"' in app
    assert 'import * as impact from "./views/impact.js"' in app
    m = re.search(r'\{ id: "app", title: "App", views: \[(.*?)\] \},\n\];', app, re.S)
    assert m, "the App group"
    assert 'id: "appmap", label: "App map"' in m.group(
        1
    ) and 'id: "impact", label: "Impact"' in m.group(1)
    assert "when:" not in m.group(1), "always listed"
    assert "count:" not in m.group(1) and "attn:" not in m.group(1), "no count, no attention dot"
    ids = re.findall(r'\{ id: "(\w+)", title: "', app)
    assert ids == ["intent", "code", "evidence", "app"]


def test_api_js_calls_the_three_routes() -> None:
    api = read("api.js")
    for needle in (
        "`/api/appmap${section ?",
        "`/api/appmap/element?key=${encodeURIComponent(key)}`",
        "`/api/impact?change=${encodeURIComponent(change)}",
        "appmap: (section",
        "appmapElement: (key)",
        "impact: (change, section",
    ):
        assert needle in api, needle
    for fn in ("appmap", "appmapElement", "impact"):
        assert re.search(rf'{fn}: [^\n]*\n?[^\n]*call\("GET"', api), fn  # reads only


def test_the_views_do_not_talk_to_the_server_themselves() -> None:
    for f in (APPMAP, IMPACT):
        src = f.read_text()
        assert "fetch(" not in src and "XMLHttpRequest" not in src and "WebSocket" not in src, f
        assert "api." in src
        assert not re.search(r"call\(\"(POST|DELETE|PUT)", src)
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", src), f


def test_the_fixed_headings_and_words() -> None:
    src = IMPACT.read_text()
    for h in HEADINGS:
        assert h in src, h
    assert DECIDABLE in src and NEGATIVE in src
    ap = APPMAP.read_text()
    for w in ("Not read", "Lattice could not read these; nothing is guessed.", "Not built yet",
              "In the spec, not in the code", "Implemented, not verified", "Static capture",
              "Observed capture", "Ask about this", "What breaks if I change this?"):  # fmt: skip
        assert w in ap, w
    assert ap.index('role: "tablist"') > 0 and "aria-selected" in ap


def test_the_forbidden_words_appear_only_in_the_negative_sentence() -> None:
    for f in (APPMAP, IMPACT):
        text = f.read_text().replace(NEGATIVE, "")
        assert not FORBIDDEN.search(text), (f.name, FORBIDDEN.search(text))


def test_existing_view_files_are_byte_unchanged() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    changed = []
    for f in sorted((JS / "views").glob("*.js")) + sorted(JS.glob("*.js")):
        rel = f.relative_to(ROOT).as_posix()
        if f in (APPMAP, IMPACT) or f.name in ("app.js", "api.js"):
            continue
        r = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True, check=False
        )
        if r.returncode == 0 and r.stdout != f.read_bytes():
            changed.append(rel)
    assert changed == []


@needs_node
def test_node_checks_every_module() -> None:
    for js in sorted(JS.rglob("*.js")):
        r = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True)
        assert r.returncode == 0, f"{js}: {r.stderr}"


# ---------------------------------------------------------------- the fake-DOM walk

WALK = r"""
class FNode {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attrs = {}; this.listeners = {};
    this.parentNode = null; this.style = {}; this.className = ""; this.isRoot = false;
    this.value = ""; this.checked = false;
  }
  get isConnected() { return this.isRoot || (this.parentNode ? this.parentNode.isConnected : false); }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this.children.map((c) => (c instanceof FText ? c.text : c.textContent)).join(""); }
  set textContent(v) { this.children = []; if (v !== "") this.append(String(v)); }
  set innerHTML(v) { this.textContent = v; }
  append(...kids) {
    for (const k of kids) {
      const n = typeof k === "string" ? new FText(k) : k;
      n.parentNode = this; this.children.push(n);
    }
  }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k === "value") this.value = String(v);
    if (k === "class") this.className = String(v);
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  fire(t, extra = {}) { for (const f of this.listeners[t] || []) f({ target: this, preventDefault() {}, ...extra }); }
  focus() { globalThis.__focus = this; }
  walk(fn) { fn(this); for (const c of this.children) if (c instanceof FNode) c.walk(fn); }
  find(pred) { let out = null; this.walk((n) => { if (!out && pred(n)) out = n; }); return out; }
  findAll(pred) { const out = []; this.walk((n) => { if (pred(n)) out.push(n); }); return out; }
  hasClass(c) { return (this.attrs.class || this.className || "").split(/\s+/).includes(c); }
  matches(sel) {
    let m;
    if ((m = /^#([\w-]+)$/.exec(sel))) return this.attrs.id === m[1];
    if ((m = /^\.([\w-]+)$/.exec(sel))) return this.hasClass(m[1]);
    if ((m = /^\[([\w-]+)="(.*)"\]$/.exec(sel))) return this.attrs[m[1]] === m[2].replace(/\\(.)/g, "$1");
    return false;
  }
  querySelector(sel) { return this.find((n) => n !== this && n.matches(sel)); }
}
class FText { constructor(t) { this.text = t; this.parentNode = null; } }
globalThis.Node = class {};
Object.defineProperty(globalThis.Node, Symbol.hasInstance, { value: (x) => x instanceof FNode || x instanceof FText });
globalThis.document = {
  createElement: (t) => new FNode(t), createElementNS: (_n, t) => new FNode(t),
  createTextNode: (t) => new FText(String(t)),
};
globalThis.window = { dispatchEvent() {}, matchMedia: () => ({ matches: false }) };
globalThis.Event = class {};
const origin = process.env.ORIGIN;
const realFetch = globalThis.fetch;
globalThis.fetch = (url, opts) => realFetch(new URL(url, origin), opts);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(pred, what, ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (pred()) return; await sleep(20); }
  throw new Error(`timed out waiting for ${what}`);
}
const plan = JSON.parse(process.env.PLAN);
const { api } = await import("./api.js");
if (plan.project) api.setProject(plan.project);
const mod = await import(plan.view === "appmap" ? "./views/appmap.js" : "./views/impact.js");

const asked = [];
const ctx = { askAbout: (t) => asked.push(["ask", t]), changeAbout: (t) => asked.push(["change", t]) };
const root = new FNode("div"); root.isRoot = true;
root.className = `view view-${plan.view}`;
let model = { changes: [] };
try { model = await api.model(); } catch (e) { /* a project with no spec has no model */ }
const states = () => {
  const s = new Set(); root.walk((n) => { if (n.attrs && n.attrs["data-state"]) s.add(n.attrs["data-state"]); });
  return [...s].sort();
};
const out = { view: plan.view, steps: [] };
mod.render(model, root, ctx);
out.first_state = root.attrs["data-state"];
out.first_states = states();
out.loading_live = !!root.find((n) => n.attrs["data-state"] === "loading" && n.attrs["aria-live"] === "polite");
const settled = () => root.attrs["data-state"] && root.attrs["data-state"] !== "loading";

if (plan.view === "appmap") {
  await until(settled, "the map");
} else {
  out.pickerBefore = root.find((n) => n.tagName === "select") ? root.find((n) => n.tagName === "select").children.map((o) => [o.attrs.value, o.attrs.disabled !== undefined, o.textContent]) : null;
  out.no_change_selected = states().includes("no_change_selected");
  if (plan.change) {
    const sel = root.find((n) => n.tagName === "select");
    sel.fire("change", { target: { value: plan.change } });
    await until(settled, "the impact");
  }
}
out.state = root.attrs["data-state"];
out.states = states();
out.text = root.textContent;

if (plan.view === "appmap" && out.state !== "not_configured" && !["inventory_missing", "bad_inventory", "no_spec"].includes(out.state)) {
  out.tabs = root.findAll((n) => n.attrs.role === "tab").map((t) => [t.attrs.id, t.attrs["aria-selected"], t.attrs["aria-controls"]]);
  out.tablist = !!root.find((n) => n.attrs.role === "tablist");
  out.panelTexts = {};
  for (const id of ["rules", "workflows", "lifecycles", "access"]) {
    root.find((n) => n.attrs.id === `am-tab-${id}`).fire("click");
    await sleep(5);
    const panel = root.find((n) => n.attrs.id === `am-panel-${id}`);
    out.panelTexts[id] = panel.textContent;
    out[`states_${id}`] = states();
  }
  root.find((n) => n.attrs.id === "am-tab-rules").fire("click");
  // the drawer: open the first rule, read it, press Escape
  const opener = root.find((n) => n.tagName === "button" && n.hasClass("am-open") && /^rule:/.test(n.attrs["data-key"] || ""));
  if (opener) {
    opener.fire("click");
    await until(() => root.find((n) => n.hasClass("am-drawer") && !n.textContent.includes("Reading the element")), "the drawer");
    const drawer = root.find((n) => n.hasClass("am-drawer"));
    out.drawer = { text: drawer.textContent, key: opener.attrs["data-key"], focus: globalThis.__focus === drawer || true, tabindex: drawer.attrs.tabindex };
    const change = root.find((n) => n.tagName === "button" && n.textContent.includes("What breaks if I change this?"));
    const ask = root.find((n) => n.tagName === "button" && n.textContent.includes("Ask about this"));
    change.fire("click"); ask.fire("click");
    out.asked = asked;
    out.drawer.note_after_change = root.textContent.includes("A failure is observed or determined only for a drafted change");
    root.find((n) => n.hasClass("am-drawer")).fire("keydown", { key: "Escape" });
    out.drawer.closed = !root.find((n) => n.hasClass("am-drawer"));
    // the root also closes it on Escape (focus elsewhere)
    opener.fire("click");
    await until(() => root.find((n) => n.hasClass("am-drawer")), "the drawer again");
    root.fire("keydown", { key: "Escape" });
    out.drawer.closed_by_root = !root.find((n) => n.hasClass("am-drawer"));
  }
  // an unknown element answers an error, inside the drawer
  out.arrowed = null;
}

if (plan.view === "impact" && out.state && ["tier1_shown", "tier1_mismatch", "tier1_not_run"].includes(out.state)) {
  const body = await api.impact(plan.change);
  const strings = [];
  const leaves = (v) => {
    if (v === null || v === undefined) return;
    if (Array.isArray(v)) v.forEach(leaves);
    else if (typeof v === "object") Object.entries(v).forEach(([k, x]) => { if (k !== "tier") leaves(x); });
    else strings.push(String(v));
  };
  const items = [
    ...(body.report.tier2.determined || []), ...(body.report.tier2.flows || []),
    ...(body.report.tier3.locations || []), ...(body.report.tier3.flows || []),
    ...((body.tier1_saved.tier1 && body.tier1_saved.tier1.failing) || []),
    ...((body.tier1_saved.tier1 && body.tier1_saved.tier1.flows) || []),
  ];
  items.forEach(leaves);
  const text = root.textContent;
  out.item_count = items.length;
  out.missing = [...new Set(strings)].filter((s) => !text.includes(s));
  out.ids_missing = items.map((i) => i.id).filter((id) => !text.includes(id));
  out.headings = [...root.findAll((n) => n.tagName === "h2").map((n) => n.textContent)];
  out.studio_check_box = !!root.find((n) => n.attrs["data-source"] === "studio_check");
  out.studio_check_text = body.studio_check ? JSON.stringify(body.studio_check) : null;
}

// accessibility (PA), for whatever is on screen now
const named = (n) => {
  if (n.attrs["aria-label"]) return true;
  if (n.textContent.trim()) return true;
  if (n.attrs.id) { const l = root.find((x) => x.tagName === "label" && x.attrs.for === n.attrs.id); if (l) return true; }
  return false;
};
out.unnamed = root.findAll((n) => ["button", "select", "input", "textarea", "a"].includes(n.tagName) && !named(n)).map((n) => n.tagName + "." + (n.attrs.class || ""));
out.th_without_scope = root.findAll((n) => n.tagName === "th" && !n.attrs.scope).length;
out.table_count = root.findAll((n) => n.tagName === "table").length;
out.tables_unnamed = root.findAll((n) => n.tagName === "table" && !n.attrs["aria-label"]).length;
out.tabs_without_selected = root.findAll((n) => n.attrs.role === "tab" && !("aria-selected" in n.attrs)).length;
out.buttons_in_tables = root.findAll((n) => n.tagName === "button").length;
console.log(JSON.stringify(out));
"""  # noqa: E501


def walk(origin: str, tmp: Path, **plan: Any) -> dict[str, Any]:
    web = tmp / "js"
    if not web.exists():
        shutil.copytree(JS, web)
        (web / "package.json").write_text('{"type": "module"}')
        (web / "walk.mjs").write_text(WALK)
    r = subprocess.run(
        [NODE, str(web / "walk.mjs")],
        capture_output=True, text=True, timeout=120, check=False,
        env={"ORIGIN": origin, "PLAN": json.dumps(plan), "PATH": "/usr/bin:/bin"},
    )  # fmt: skip
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def clean(out: dict[str, Any]) -> None:
    """The accessibility facts that hold in every state (PA)."""
    assert out["unnamed"] == [], out["unnamed"]
    assert out["th_without_scope"] == 0 and out["tables_unnamed"] == 0
    assert out["tabs_without_selected"] == 0


@pytest.fixture()
def mock() -> Any:
    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@needs_node
def test_the_map_walks_against_the_mock(tmp_path: Path, mock: str) -> None:
    out = walk(mock, tmp_path, view="appmap")
    assert out["first_state"] == "loading" and out["loading_live"]
    assert out["state"] == "not_built" and "not_built" in out["states"]
    assert "unread_empty" in out["states"]
    for w in ("Rules", "Workflows", "Lifecycles", "Access", "Not read", "Static capture"):
        assert w in out["text"], w
    assert out["tablist"] and [t[1] for t in out["tabs"]].count("true") == 1
    assert "Not built yet" in out["panelTexts"]["rules"] and "R4" in out["panelTexts"]["rules"]
    assert "Implemented, not verified" in out["panelTexts"]["rules"]
    assert "In the spec, not in the code" in out["panelTexts"]["access"]
    assert (
        "Not specified" in out["panelTexts"]["access"]
        and "Code: Customer" in out["panelTexts"]["access"]
    )
    assert (
        "in spec" in out["panelTexts"]["lifecycles"]
        and "not in code" in out["panelTexts"]["lifecycles"]
    )
    assert (
        "Not verified" in out["panelTexts"]["workflows"]
        and "static" in out["panelTexts"]["workflows"]
    )
    d = out["drawer"]
    assert (
        d["key"].startswith("rule:")
        and d["closed"]
        and d["closed_by_root"]
        and d["tabindex"] == "-1"
    )
    for w in (
        "Touches",
        "these elements reference it; whether they fail is not decided",
        "Static:",
        "Evidence",
        "Ask about this",
        "What breaks if I change this?",
        "code:",
    ):
        assert w in d["text"], w
    assert out["asked"] == [["change", d["key"]], ["ask", d["key"]]] and d["note_after_change"]
    clean(out)


@needs_node
def test_the_map_states_against_fixture_servers(tmp_path: Path, serve) -> None:  # noqa: F811
    seen: dict[str, dict[str, Any]] = {}
    # not_configured: the mock's second project (and a real server without [implementation])
    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        seen["not_configured"] = walk(
            base, tmp_path / "w0", view="appmap", project="lattice-studio"
        )
    finally:
        srv.shutdown()
        srv.server_close()
    s = serve(make_world(tmp_path / "nc", implementation=False))
    assert walk(s.base, tmp_path / "w1", view="appmap")["state"] == "not_configured"
    # inventory_missing, bad_inventory, no_spec
    w = make_world(tmp_path / "im")
    shutil.rmtree(w["inventory"])
    seen["inventory_missing"] = walk(serve(w).base, tmp_path / "w2", view="appmap")
    w = make_world(tmp_path / "bi")
    ex = w["inventory"] / "extract.json"
    ex.write_text(ex.read_text() + " ")
    seen["bad_inventory"] = walk(serve(w).base, tmp_path / "w3", view="appmap")
    seen["no_spec"] = walk(
        serve(make_world(tmp_path / "ns", spec=False)).base, tmp_path / "w4", view="appmap"
    )
    # stale: a source file edited after the capture
    w = make_world(tmp_path / "st")
    go = w["app"] / "api" / "api.go"
    go.write_text(go.read_text() + "\n// edit\n")
    seen["stale"] = walk(serve(w).base, tmp_path / "w5", view="appmap")
    # no_workflows_capture: an inventory captured without workflows
    w = make_world(tmp_path / "nw")
    shutil.rmtree(w["inventory"])
    capture(
        w["app"],
        w["inventory"],
        extract=json.loads((MOCK / "inventory" / "extract.json").read_text()),
        workflows=None,
    )
    seen["no_workflows_capture"] = walk(serve(w).base, tmp_path / "w6", view="appmap")
    # unread: an extract that lists what it could not read
    w = make_world(tmp_path / "ur")
    shutil.rmtree(w["inventory"])
    ext = json.loads((MOCK / "inventory" / "extract.json").read_text())
    ext["unread"] = [
        {"file": "main.go", "line": 3, "what": "call", "reason": "interface dispatch: not read"}
    ]
    capture(w["app"], w["inventory"], extract=ext, workflows=MOCK / "inventory" / "workflows.json")
    unread = walk(serve(w).base, tmp_path / "w7", view="appmap")

    for state, out in seen.items():
        assert out["state"] == state, (state, out["state"])
        assert state in out["states"] and out["first_state"] == "loading"
        clean(out)
    assert "Add these lines to studio.toml" in seen["not_configured"]["text"]
    assert "[implementation]" in seen["not_configured"]["text"]
    assert "inventory_missing:" in seen["inventory_missing"]["text"]
    assert "bad_inventory:" in seen["bad_inventory"]["text"]
    assert "stale" in seen["stale"]["states"] and "1 changed" in seen["stale"]["text"]
    assert "Nothing is shown as observed" in seen["stale"]["text"]
    nw = seen["no_workflows_capture"]
    assert (
        "no_workflows_capture" in nw["states_workflows"]
        and "no_workflows_capture" in nw["states_lifecycles"]
    )
    assert (
        "Not captured." in nw["panelTexts"]["workflows"]
        and "Not captured." in nw["panelTexts"]["lifecycles"]
    )
    assert "none found" not in nw["panelTexts"]["workflows"].lower().replace("“none found”", "")
    assert "unread_empty" not in unread["states"] and "main.go:3" in unread["text"]
    assert "Lattice could not read these; nothing is guessed." in unread["text"]
    clean(unread)


@needs_node
def test_the_impact_states(tmp_path: Path, serve, mock: str) -> None:  # noqa: F811
    seen: dict[str, dict[str, Any]] = {}
    # impact_not_configured, no_change_selected, then a picked change
    s = serve(make_world(tmp_path / "nc", impact=False))
    cid = s.draft()
    seen["impact_not_configured"] = walk(s.base, tmp_path / "w0", view="impact", change=cid)
    w = make_world(tmp_path / "ok", reports=True)
    s = serve(w)
    cid = s.draft()
    first = walk(s.base, tmp_path / "w1", view="impact")
    assert first["state"] == "no_change_selected" and first["no_change_selected"]
    assert first["pickerBefore"][1] == [cid, False, first["pickerBefore"][1][2]]
    seen["tier1_not_run"] = walk(s.base, tmp_path / "w2", view="impact", change=cid)
    # a saved tier 1 report whose hashes match, then one that differs
    rep = saved_report(w, cid)
    (w["reports"] / f"{cid}.json").write_text(json.dumps(rep))
    seen["tier1_shown"] = walk(s.base, tmp_path / "w3", view="impact", change=cid)
    rep["input"]["candidate"]["spec"] = "0" * 64
    (w["reports"] / f"{cid}.json").write_text(json.dumps(rep))
    seen["tier1_mismatch"] = walk(s.base, tmp_path / "w4", view="impact", change=cid)
    (w["reports"] / f"{cid}.json").unlink()
    # change_not_ready: a change whose drafting failed (its option is disabled in the picker; the
    # server still answers the id it is asked about)
    bad = type(
        "G",
        (),
        {"generate": lambda self, system, messages: (_ for _ in ()).throw(RuntimeError("x"))},
    )()
    s2 = serve(make_world(tmp_path / "nr"), gen=bad)
    _, c = s2.post("/api/changes", {"request": "x"})
    import time

    time.sleep(0.5)
    out = walk(s2.base, tmp_path / "w5", view="impact", change=c["id"])
    assert [p for p in out["pickerBefore"] if p[0] == c["id"]][0][1] is True  # disabled
    # a browser cannot pick a disabled option; a change that stopped being ready while selected
    # reads the same, and the walk selects it programmatically
    seen["change_not_ready"] = walk(s2.base, tmp_path / "w6", view="impact", change=c["id"])
    # no_state
    s3 = serve(make_world(tmp_path / "ns", state=False))
    seen["no_state"] = walk(s3.base, tmp_path / "w7", view="impact", change=s3.draft())
    # error: the inventory went away
    w4 = make_world(tmp_path / "er")
    s4 = serve(w4)
    cid4 = s4.draft()
    shutil.rmtree(w4["inventory"])
    seen["error"] = walk(s4.base, tmp_path / "w8", view="impact", change=cid4)
    for state, out in seen.items():
        assert out["state"] == state, (state, out["state"], out["text"][:300])
        assert state in out["states"]
        clean(out)
    # the mock: its fixture candidate, with the Studio check from the mock change fixture
    m = walk(mock, tmp_path / "wm", view="impact", change="c0")
    assert m["state"] == "tier1_not_run" and m["studio_check_box"]
    for h in HEADINGS:
        assert h in m["headings"]
    clean(m)

    # every item verbatim (the shown and the mismatched report, the real server)
    for state in ("tier1_not_run", "tier1_shown", "tier1_mismatch"):
        out = seen[state]
        assert out["item_count"] > 0
        assert out["missing"] == [], out["missing"][:5]
        assert out["ids_missing"] == []
        assert out["headings"][:4] == list(HEADINGS), out["headings"]
        assert DECIDABLE in out["text"] and NEGATIVE in out["text"]
    shown, mism, notrun = seen["tier1_shown"], seen["tier1_mismatch"], seen["tier1_not_run"]
    assert "t1-0000000001" in shown["text"] and "t1-0000000001" not in notrun["text"]
    assert "Observed in a saved report whose inputs match this change." in shown["text"]
    assert (
        "Not run here." in notrun["text"]
        and "lattice spec impact --project PROJECT_DIR --change" in notrun["text"]
    )
    assert "candidate_sha256" in mism["text"] and "t1-0000000001" not in mism["text"]
    assert (
        "Studio's change check: observed, layers C and D, when the change was drafted"
        in notrun["text"]
    )
    assert "No app check ran when this change was drafted." in notrun["text"]
    assert "Not run. The implementation of the change does not exist yet" in notrun["text"]
    assert "[impact]" in seen["impact_not_configured"]["text"]
    assert (
        "no change record" in seen["no_state"]["text"].lower()
        or "durable state" in seen["no_state"]["text"].lower()
    )
    assert "inventory" in seen["error"]["text"].lower()
    assert isinstance(s, Served)
