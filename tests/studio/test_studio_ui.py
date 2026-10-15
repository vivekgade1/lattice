"""UI facts of the improvement loop and the contracts view (API v1.21) that need no browser: the
navigator entries, the route strings in api.js, the words the owner reads, ``node --check`` on
every module, and node runs of the pure functions (``canApply``, ``applyRefusal``,
``sha256Hex``) and of ``api.applyProposal`` with a stubbed ``fetch``. Rendering itself (layout,
focus, polling feel) is browser-only and is NOT checked here."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "lattice" / "studio" / "web"
JS = WEB / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


def test_navigator_entries() -> None:
    app = read("app.js")
    assert 'id: "improve"' in app and 'id: "contracts"' in app
    # Contracts is listed only when the workspace manifest names a workspace
    m = re.search(r'id: "contracts"[^\n]*\n[^\n]*', app)
    assert m and "when: () => !!state.workspace" in m.group(0)
    assert "views/improve.js" in app and "views/contracts.js" in app


def test_api_js_has_every_route_of_the_loop() -> None:
    api = read("api.js")
    for needle in (
        '"/api/improve/applies"',
        "/api/improve/${encodeURIComponent(id)}/draft",
        "/api/improve/${encodeURIComponent(id)}/verify",
        "/api/improve/${encodeURIComponent(id)}/apply",
        "/api/improve/${encodeURIComponent(id)}/discard",
        "?wait=",
        "?at=",
        "`/api/contracts${consumer",
        "/api/contracts/impact?provider=",
        "/api/linked-questions",
        '"/api/linked-notifications"',
    ):
        assert needle in api, needle
    assert re.search(r'call\("POST", `/api/improve/\$\{encodeURIComponent\(id\)\}/apply`', api)
    assert re.search(r'call\("POST", `/api/improve/\$\{encodeURIComponent\(id\)\}/discard`', api)


def test_the_words_the_owner_reads() -> None:
    improve, contracts = read("views/improve.js"), read("views/contracts.js")
    for needle in (
        "Apply on a branch",
        "NOT MERGED",
        "Discard",
        "Run the gates",
        "Lattice never merges",
        "--allow-apply",
        "Verify",
        "studio.toml snippet",
    ):
        assert needle in improve, needle
    for needle in ("Check contracts", "Preview impact", "advisory", "Resolve", "Dismiss"):
        assert needle in contracts, needle


def test_the_new_views_do_not_talk_to_the_server_themselves() -> None:
    for rel in ("views/improve.js", "views/contracts.js"):
        src = read(rel)
        assert "fetch(" not in src and "XMLHttpRequest" not in src, rel
        assert "api." in src, rel
    # the load-time reads are cheap; the two that spawn export processes run on a click only
    src = read("views/contracts.js")
    for call in ("api.contracts(", "api.contractsImpact("):
        for m in re.finditer(re.escape(call), src):
            assert "onclick" in src[max(0, m.start() - 160) : m.start()], call


def test_the_new_files_use_no_colour_literals() -> None:
    lit = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    for rel in ("views/improve.js", "views/contracts.js"):
        assert not lit.search(read(rel)), rel


@needs_node
def test_node_checks_every_module() -> None:
    for js in sorted(JS.rglob("*.js")):
        r = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True)
        assert r.returncode == 0, f"{js}: {r.stderr}"


SCRIPT = r"""
import { canApply, applyRefusal, sha256Hex, gatesWord } from "./views/improve.js";
import { api } from "./api.js";

const out = {};
const cfg = { template: "config", proposal: { id: "imp-1" } };
const en = { enabled: true, applies: [] };
out.table = [
  canApply(cfg, en),
  canApply(cfg, { enabled: true, applies: [{ id: "imp-1" }] }),
  canApply(cfg, { enabled: false, applies: [] }),
  canApply({ template: "prereg", proposal: { id: "imp-1" } }, en),
  canApply({ template: "change_request", proposal: { id: "imp-1" } }, en),
  canApply({ refused: true, reason: "closed" }, en),
  canApply(null, en),
  canApply(cfg, null),
];
const codes = ["apply_disabled", "bad_origin", "json_required", "bad_id", "bad_body",
               "no_state", "apply_running", "draft_changed"];
out.refusals = codes.map((c) => applyRefusal(c));
out.unknown = applyRefusal("nonsense");
out.sha = await sha256Hex("draft é 中\n");
out.gates = ["passed", "failed", "skipped", "running", "interrupted"].map(gatesWord);

const seen = [];
globalThis.fetch = async (url, opts) => {
  seen.push({ url, method: opts.method, headers: opts.headers, body: opts.body });
  return { ok: true, status: 202, json: async () => ({ id: "imp-1", status: "running" }) };
};
await api.applyProposal("imp-1", "a".repeat(64), false, null);
api.setProject("b");
await api.applyProposal("imp-1", "b".repeat(64));
await api.discardApply("imp-1");
await api.improveApply("imp-1", 30);
await api.improveVerify("imp-1", "2026-10-01T12:00:00Z");
out.seen = seen;
console.log(JSON.stringify(out));
"""


@needs_node
def test_pure_functions_and_the_apply_call_under_node(tmp_path: Path) -> None:
    web = tmp_path / "js"
    shutil.copytree(JS, web)
    (web / "package.json").write_text('{"type": "module"}')
    (web / "t.mjs").write_text(SCRIPT)
    r = subprocess.run([NODE, str(web / "t.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    # canApply: true only for a config draft, apply enabled, no live record
    assert out["table"] == [True, False, False, False, False, False, False, False]
    # one distinct sentence ending in "." per D3 code, none with a status number
    sentences = out["refusals"]
    assert len(set(sentences)) == 8
    for s in [*sentences, out["unknown"]]:
        assert s.endswith(".") and not re.search(r"\d", s), s
    assert out["unknown"] not in sentences
    assert out["sha"] == hashlib.sha256("draft é 中\n".encode()).hexdigest()
    assert out["gates"][0] == "Gates passed"
    first, second, discard, get, verify = out["seen"]
    assert first["url"] == "/api/improve/imp-1/apply" and first["method"] == "POST"
    assert first["headers"] == {"Content-Type": "application/json"}
    assert json.loads(first["body"]) == {"draft_sha256": "a" * 64, "gates": False, "branch": None}
    assert second["url"] == "/p/b/api/improve/imp-1/apply"
    assert json.loads(second["body"]) == {"draft_sha256": "b" * 64, "gates": True, "branch": None}
    assert discard["url"] == "/p/b/api/improve/imp-1/discard" and discard["method"] == "POST"
    assert get["url"] == "/p/b/api/improve/imp-1/apply?wait=30" and get["method"] == "GET"
    assert verify["url"] == "/p/b/api/improve/imp-1/verify?at=2026-10-01T12%3A00%3A00Z"


WALK = r"""
// A minimal fake DOM: enough for util.js's h(), s(), clear() and the two views' drawing code.
class FNode {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attrs = {}; this.listeners = {};
    this.parentNode = null; this.style = {}; this.className = ""; this.isRoot = false;
    this.value = ""; this.checked = false;
  }
  get isConnected() {
    return this.isRoot || (this.parentNode ? this.parentNode.isConnected : false);
  }
  get firstChild() { return this.children[0] || null; }
  get textContent() {
    return this.children.map((c) => (c instanceof FText ? c.text : c.textContent)).join("");
  }
  set textContent(v) { this.children = []; if (v !== "") this.append(String(v)); }
  set innerHTML(v) { this.textContent = v; }
  append(...kids) {
    for (const k of kids) {
      const n = typeof k === "string" ? new FText(k) : k;
      n.parentNode = this;
      this.children.push(n);
    }
  }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k === "value") this.value = String(v);
    if (k === "checked") this.checked = true;
  }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  fire(t, extra = {}) { for (const f of this.listeners[t] || []) f({ target: this, ...extra }); }
  focus() {}
  walk(fn) { fn(this); for (const c of this.children) if (c instanceof FNode) c.walk(fn); }
  find(pred) { let out = null; this.walk((n) => { if (!out && pred(n)) out = n; }); return out; }
  findAll(pred) { const out = []; this.walk((n) => { if (pred(n)) out.push(n); }); return out; }
  querySelector(sel) {
    const tags = sel.split(",").map((x) => x.trim());
    return this.find((n) => n !== this && tags.includes(n.tagName));
  }
}
class FText { constructor(t) { this.text = t; this.parentNode = null; } }
globalThis.Node = class {}; // util.js: `c instanceof Node`
Object.defineProperty(globalThis.Node, Symbol.hasInstance, {
  value: (x) => x instanceof FNode || x instanceof FText,
});
globalThis.document = {
  createElement: (t) => new FNode(t),
  createElementNS: (_ns, t) => new FNode(t),
  createTextNode: (t) => new FText(String(t)),
};
globalThis.window = { dispatchEvent() {}, matchMedia: () => ({ matches: false }) };
globalThis.Event = class {};

const origin = process.env.ORIGIN;
const realFetch = globalThis.fetch;
globalThis.fetch = (url, opts) => realFetch(new URL(url, origin), opts);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(pred, what, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (pred()) return; await sleep(25); }
  throw new Error(`timed out waiting for ${what}`);
}
const text = (n) => n.textContent;
const button = (root, label) => root.find((n) => n.tagName === "button" && text(n).includes(label));

const { api } = await import("./api.js");
const { state } = await import("./store.js");
const improve = await import("./views/improve.js");
const contracts = await import("./views/contracts.js");

const out = { steps: [] };
const ok = (name) => out.steps.push(name);

state.projects = await api.projects();
state.workspace = await api.workspace();
state.project = null;

// ---- Improve: proposals, a draft, apply, the record, discard
const root = new FNode("div"); root.isRoot = true;
improve.render({}, root, {});
await until(() => text(root).includes("imp-3e1cd7f9"), "the proposals");
ok("proposals");
out.noDraftOnNeedsOwner = root.findAll((n) => n.tagName === "button" && text(n) === "Draft").length;
out.needsOwner = text(root).includes("Needs the owner");
button(root, "Draft").fire("click");
await until(() => text(root).includes("studio.toml snippet"), "the draft");
ok("draft");
out.applyButtonDisabled = button(root, "Apply on a branch").attrs.disabled !== undefined;
button(root, "Apply on a branch").fire("click");
await until(() => text(root).includes("Run the gates (can take minutes)"), "the confirm dialog");
out.dialogText = text(root.find((n) => n.attrs.role === "dialog"));
ok("dialog");
button(root, "Apply on a branch").fire("click"); // the first match is the page's own button
const dlg = root.find((n) => n.attrs.role === "dialog");
const dlgButtons = dlg.findAll((n) => n.tagName === "button");
dlgButtons.find((b) => text(b) === "Apply on a branch").fire("click");
await until(() => text(root).includes("NOT MERGED"), "the record", 30000);
ok("record");
out.record = text(root);
button(root, "Discard").fire("click");
await until(() => root.find((n) => n.attrs.role === "dialog"), "the discard dialog");
root.find((n) => n.attrs.role === "dialog").findAll((n) => n.tagName === "button")
  .find((b) => text(b) === "Discard").fire("click");
await until(() => text(root).includes("Nothing is applied on a branch"), "the discard", 30000);
ok("discarded");

// ---- the disabled project: the button is replaced by the sentence
api.setProject("lattice-studio");
state.project = "lattice-studio";
const root2 = new FNode("div"); root2.isRoot = true;
improve.render({}, root2, {});
await until(() => text(root2).includes("imp-3e1cd7f9"), "the second project's proposals");
button(root2, "Draft").fire("click");
const OFF = "Apply is off for this Studio (start it with --allow-apply)";
await until(() => text(root2).includes(OFF), "the off text");
out.offHasButton = !!button(root2, "Apply on a branch");
out.offCli = text(root2).includes("lattice improve apply imp-3e1cd7f9");
ok("disabled");

// ---- Contracts, on the default (provider) project
api.setProject("");
state.project = null;
const root3 = new FNode("div"); root3.isRoot = true;
contracts.render({}, root3, {});
await until(() => text(root3).includes("Linked questions"), "the contracts view");
out.contractsText = text(root3);
out.checkedBeforeClick = text(root3).includes("links checked");
button(root3, "Preview impact").fire("click");
await until(() => text(root3).includes("advisory: this is a preview"), "the preview");
button(root3, "Check contracts").fire("click");
await until(() => text(root3).includes("checked for"), "the check");
ok("contracts");
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_views_walk_against_the_mock_under_a_fake_dom(tmp_path: Path) -> None:
    """Both views draw and drive their calls without a runtime error: proposals, Draft, the
    confirm dialog, apply, the record with NOT MERGED, discard, the off text, and the contracts
    view's on-demand check and preview, against the mock server. Layout is not checked."""
    import threading

    from lattice.studio.mock_server import make_server

    srv = make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    web = tmp_path / "js"
    shutil.copytree(JS, web)
    (web / "package.json").write_text('{"type": "module"}')
    (web / "walk.mjs").write_text(WALK)
    try:
        env = {"ORIGIN": f"http://127.0.0.1:{srv.server_address[1]}", "PATH": "/usr/bin:/bin"}
        r = subprocess.run(
            [NODE, str(web / "walk.mjs")], capture_output=True, text=True, timeout=120, env=env
        )
    finally:
        srv.shutdown()
        srv.server_close()
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["steps"] == [
        "proposals",
        "draft",
        "dialog",
        "record",
        "discarded",
        "disabled",
        "contracts",
    ]
    assert out["needsOwner"] is True
    assert out["applyButtonDisabled"] is False
    for word in ("Run the gates", "Lattice never merges", "improve/imp-3e1cd7f9"):
        assert word in out["dialogText"], word
    for word in (
        "NOT MERGED",
        "level_gate_args",
        "review",
        "merge",
        "close",
        "verify",
        "tidy",
        "drop",
    ):
        assert word in out["record"], word
    assert out["offHasButton"] is False and out["offCli"] is True
    assert out["checkedBeforeClick"] is False  # nothing spawns on load
    assert "Check contracts" in out["contractsText"] and "Preview impact" in out["contractsText"]
