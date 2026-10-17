// App > App map: what the code implements of what the spec requires, with the evidence.
//   GET /api/appmap          lattice.appmap/1: a join of the implementation report and the review
//                            data, read once per visit (the server re-reads the inventory),
//   GET /api/appmap/element  one element's row and its static neighbourhood, when a row is opened.
// Nothing here decides or guesses anything: every value is copied from the map, a status is a
// word plus an icon (never colour alone), `observed` / `static` / `inferred` are the map's own
// basis words, and "touches" is labelled static. Every call goes through api.js; nothing is written.

import { h, icon, add, clear, plural } from "../util.js";
import { api } from "../api.js";

// ------------------------------------------------------------------ the fixed words

export const WORDS = {
  title: "App map",
  lede: "What the code implements of what the spec requires, and where the evidence is. A status is read from the code or from a recorded replay; Lattice does not guess.",
  loading: "Reading the app map…",
  notConfigured: "No implementation is hooked up to this project.",
  notConfiguredHow: "Add these lines to studio.toml, then reload. The inventory is the directory that “lattice import inventory” writes.",
  noSpec: "This project has no loadable spec, so there is nothing to map the code against.",
  noWorkflows: "Not captured. Machines and flows come from a workflows capture; this inventory has none. That is different from “none found”.",
  noneFoundMachines: "None found. The capture saw no state machine.",
  noneFoundFlows: "None found. The capture recorded no workflow.",
  unreadHead: "Not read",
  unreadLede: "Lattice could not read these; nothing is guessed.",
  unreadEmpty: "Nothing was left unread.",
  notBuilt: "Not built yet means the spec requires it and the extract of the code has no such element. In the spec, not in the code means an endpoint the spec names that no route registers.",
  staleHead: "The code changed after the inventory was captured.",
  staleTail: "Nothing is shown as observed until the inventory is captured again.",
  staticNote: "Static: read from the code, not run.",
  touchesHead: "Touches",
  touchesMeaning: "these elements reference it; whether they fail is not decided",
  askAbout: "Ask about this",
  whatBreaks: "What breaks if I change this?",
  whatBreaksNote: "A failure is observed or determined only for a drafted change. Draft this change, then open Impact.",
  accessLegend: [
    ["Spec", "what the spec expects for that caller on that endpoint (“Not specified” when it says nothing)."],
    ["Code", "the auth the route registers with, read from the code."],
    ["Seen", "the answers recorded workflows got for that caller, at capture time (observed, verified workflows only)."],
  ],
  accessNote: "The map shows these side by side and does not compare them. Whether the code honours a cell is decided by the Checks view.",
};

export const IMPL = {
  implemented_verified: { word: "Verified", icon: "check", tone: "trust-ok" },
  implemented_unverified: { word: "Implemented, not verified", icon: "dash", tone: "trust-warn" },
  required_not_implemented: { word: "Not built yet", icon: "diamond", tone: "trust-bad" },
  no_element: { word: "No code element", icon: "question", tone: "trust-none" },
};
export const BASIS = {
  observed: "observed",
  static: "static",
  inferred: "inferred",
  none: "none",
};
export const BASIS_MEANING = {
  observed: "a replay of these requests passed on a fresh database at capture time",
  static: "read from the code",
  inferred: "an absence in a static extract",
  none: "no code element is required",
};
export const SPEC_STATUS = {
  checked: "Checked",
  unchecked: "Not checked",
  context: "Context",
  untraced: "Untraced",
  probe_gap: "Probe gap",
};
export const FLOW = {
  verified: { word: "Verified at capture", icon: "check", tone: "trust-ok" },
  failed: { word: "Failed at capture", icon: "diamond", tone: "trust-bad" },
  unverified: { word: "Not verified", icon: "dash", tone: "trust-warn" },
};
export const MACHINE = {
  confirmed: "Confirmed by a replay",
  static: "Read from the code (static)",
};
export const ENDPOINT = {
  implemented: "In the code",
  spec_only: "In the spec, not in the code",
  test_only: "Registered only under a test flag",
  plumbing: "Plumbing, not a business endpoint",
  implemented_not_required: "In the code, no rule requires it",
};
const TABS = [
  { id: "rules", label: "Rules" },
  { id: "workflows", label: "Workflows" },
  { id: "lifecycles", label: "Lifecycles" },
  { id: "access", label: "Access" },
];
const FILTERS = ["all", "implemented_verified", "implemented_unverified", "required_not_implemented", "no_element"];
const FILTER_LABEL = { all: "All", ...Object.fromEntries(Object.entries(IMPL).map(([k, v]) => [k, v.word])) };

// ------------------------------------------------------------------ state

function fresh(base) {
  return { base, phase: "idle", doc: null, error: null, tab: "rules", filter: "all", drawer: null, asked: null };
}

let S = fresh(null);
let mounted = null;

function redraw() {
  if (mounted && mounted.root.isConnected) draw(mounted.root, mounted.ctx);
}

async function load() {
  const base = S.base;
  S.phase = "loading";
  redraw();
  try {
    const doc = await api.appmap();
    if (S.base !== base) return;
    S.doc = doc;
    S.phase = "done";
  } catch (e) {
    if (S.base !== base) return;
    S.error = e.message;
    S.phase = "error";
  }
  redraw();
}

async function openDrawer(key, opener) {
  const base = S.base;
  S.drawer = { key, loading: true, doc: null, error: null, opener };
  S.asked = null;
  redraw();
  focusKey(".am-drawer");
  try {
    const doc = await api.appmapElement(key);
    if (S.base !== base || !S.drawer || S.drawer.key !== key) return;
    S.drawer = { ...S.drawer, loading: false, doc };
  } catch (e) {
    if (S.base !== base || !S.drawer || S.drawer.key !== key) return;
    S.drawer = { ...S.drawer, loading: false, error: e.message };
  }
  redraw();
}

/** Focus the first element matching `sel` in the view, if the DOM can look (the view redraws, so
 *  the element the owner used is gone; the one that replaces it gets the focus). */
function focusKey(sel) {
  const root = mounted && mounted.root;
  if (!root || typeof root.querySelector !== "function") return;
  const el = root.querySelector(sel);
  if (el && typeof el.focus === "function") el.focus();
}

function closeDrawer() {
  const key = S.drawer && S.drawer.key;
  S.drawer = null;
  S.asked = null;
  redraw();
  if (key) focusKey(`[data-key="${String(key).replace(/["\\]/g, "\\$&")}"]`);
}

// ------------------------------------------------------------------ small parts

const statusMark = (m, extra = "") =>
  h("span", { class: ["s2-trust", m.tone, extra] }, icon(m.icon), h("span", {}, m.word));

function keyOf(kind, x) {
  if (kind === "rule") return `rule:${x.id}`;
  if (kind === "workflow") return x.id;
  if (kind === "machine") return x.key;
  if (kind === "cell") return `cell:${x.row}|${x.col}`;
  return `impl:route:${x.key}`;
}

function opener(label, key, text, extra = {}) {
  let btn = null;
  btn = h("button", {
    type: "button", class: "am-open", "aria-label": label, "data-key": key,
    "aria-haspopup": "true", "aria-expanded": S.drawer && S.drawer.key === key ? "true" : "false",
    onclick: () => openDrawer(key, btn), ...extra,
  }, text);
  return btn;
}

function th(scope, text, cls) {
  return h("th", { scope, class: cls }, text);
}

function codeLine(text) {
  return h("li", {}, h("code", { class: "am-code" }, text), " ",
    h("button", {
      type: "button", class: "btn btn-quiet am-copy", "aria-label": `Copy ${text}`,
      onclick: () => {
        try { navigator.clipboard.writeText(text); } catch (e) { /* no clipboard: the text is on screen */ }
      },
    }, "Copy"));
}

function evidenceList(lines) {
  if (!lines || !lines.length) return h("p", { class: "muted small" }, "No code location.");
  return h("ul", { class: "am-evidence", "aria-label": "Evidence" }, lines.map(codeLine));
}

function chips(list, empty = "none") {
  if (!list || !list.length) return h("span", { class: "muted" }, empty);
  return h("span", { class: "rule-links" }, list.map((x) => h("code", { class: "chip" }, x)));
}

// ------------------------------------------------------------------ the states other than ok

function stateBox(state, title, ...body) {
  return h("section", { class: "am-state", "data-state": state, "aria-label": title },
    h("h2", {}, title), ...body);
}

function tomlBlock() {
  return h("pre", { class: "imp-pre am-toml" },
    "[implementation]\ninventory = \"../inventory\"   # the directory lattice import inventory wrote\napp_root = \".\"\n");
}

function nonOk(doc) {
  const why = doc.why ? h("p", { class: "imp-err" }, doc.why) : null;
  if (doc.state === "not_configured") {
    return stateBox("not_configured", WORDS.notConfigured,
      h("p", { class: "muted" }, WORDS.notConfiguredHow), tomlBlock(),
      h("p", { class: "muted small" }, "lattice import inventory APP_DIR --out INVENTORY_DIR"));
  }
  if (doc.state === "inventory_missing") {
    return stateBox("inventory_missing", "The inventory is missing.", why,
      h("p", { class: "muted" }, "Capture it with “lattice import inventory APP_DIR --out INVENTORY_DIR”, and point [implementation] inventory at that directory."));
  }
  if (doc.state === "bad_inventory") {
    return stateBox("bad_inventory", "The inventory cannot be read.", why,
      h("p", { class: "muted" }, "It does not match its own hashes or is not an inventory. Nothing from it is shown."));
  }
  return stateBox("no_spec", WORDS.noSpec, why);
}

// ------------------------------------------------------------------ the strip, banners, tabs

function strip(doc) {
  const inv = doc.inputs.inventory;
  const s = doc.summary;
  const observed = inv.run_mode === "observed";
  return h("div", { class: "am-strip", "aria-label": "Inventory" },
    h("span", { class: ["s2-trust", observed ? "trust-ok" : "trust-warn"], "data-capture": inv.run_mode || "static" },
      icon(observed ? "check" : "dash"), h("span", {}, observed ? "Observed capture" : "Static capture")),
    h("span", { class: "muted small" }, `Inventory ${String(inv.extract_sha256).slice(0, 12)}`),
    h("span", { class: "muted small" },
      `${plural(s.rules.total, "rule")} · ${plural(s.workflows.total, "workflow")} · ${plural(s.machines.total, "lifecycle")} · ${plural(s.access.cells, "access cell")}`),
  );
}

function staleBanner(doc) {
  const st = doc.inputs.inventory.stale;
  if (!st) return null;
  const c = st.counts || {};
  return h("div", { class: "imp-banner am-stale", role: "status", "data-state": "stale" },
    h("strong", {}, WORDS.staleHead),
    h("span", {}, `${c.changed || 0} changed, ${c.added || 0} added, ${c.removed || 0} removed. ${WORDS.staleTail}`));
}

function notBuiltNote(doc) {
  const rules = doc.rules.filter((r) => r.impl_status === "required_not_implemented").length;
  const eps = doc.endpoints.filter((e) => e.status === "spec_only").length;
  if (!rules && !eps) return null;
  return h("p", { class: "imp-note am-notbuilt", role: "status", "data-state": "not_built" },
    `${IMPL.required_not_implemented.word}: ${plural(rules, "rule")}; ${ENDPOINT.spec_only}: ${plural(eps, "endpoint")}. ${WORDS.notBuilt}`);
}

function tabs() {
  const list = h("div", { role: "tablist", class: "am-tabs", "aria-label": "App map sections" },
    TABS.map((t, i) => h("button", {
      type: "button", role: "tab", class: ["seg-btn", "am-tab"], id: `am-tab-${t.id}`,
      "aria-selected": S.tab === t.id ? "true" : "false", "aria-controls": `am-panel-${t.id}`,
      tabindex: S.tab === t.id ? "0" : "-1",
      onclick: () => { S.tab = t.id; redraw(); },
      onkeydown: (e) => {
        const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        if (e.preventDefault) e.preventDefault();
        S.tab = TABS[(i + step + TABS.length) % TABS.length].id;
        redraw();
        focusKey(`#am-tab-${S.tab}`);
      },
    }, t.label)));
  return list;
}

// ------------------------------------------------------------------ the four panels

function rulesPanel(doc) {
  const counts = doc.summary.rules.impl_status;
  const rows = doc.rules.filter((r) => S.filter === "all" || r.impl_status === S.filter);
  const filter = h("div", { class: "seg am-filter", role: "group", "aria-label": "Filter rules by implementation status" },
    FILTERS.map((f) => h("button", {
      type: "button", class: "seg-btn", "aria-pressed": S.filter === f ? "true" : "false",
      onclick: () => { S.filter = f; redraw(); },
    }, `${FILTER_LABEL[f]} (${f === "all" ? doc.summary.rules.total : counts[f] || 0})`)));
  if (!doc.rules.length) {
    return [filter, h("p", { class: "s2-empty" }, "The spec has no rules to map.")];
  }
  const table = h("table", { class: "am-table", "aria-label": "Rules" },
    h("thead", {}, h("tr", {}, ["Rule", "What it says", "Spec", "Implementation", "Basis", "Evidence"].map((c) => th("col", c)))),
    h("tbody", {}, rows.map((r) => {
      const m = IMPL[r.impl_status] || IMPL.no_element;
      return h("tr", { "data-impl": r.impl_status },
        th("row", opener(`${r.id}: ${m.word}. Open the details`, `rule:${r.id}`, r.id)),
        h("td", { class: "am-text" }, r.text),
        h("td", {}, SPEC_STATUS[r.spec_status] || r.spec_status),
        h("td", {}, statusMark(m)),
        h("td", { title: BASIS_MEANING[r.basis] }, BASIS[r.basis] || r.basis),
        h("td", {}, plural(r.evidence.length, "location")),
      );
    })));
  return [filter, h("p", { class: "muted small", role: "status", "aria-live": "polite" }, `${plural(rows.length, "rule")} shown.`), table];
}

function workflowsPanel(doc) {
  if (hasNote(doc, "no_workflows_capture")) {
    return [h("p", { class: "s2-empty", "data-state": "no_workflows_capture" }, WORDS.noWorkflows)];
  }
  if (!doc.workflows.length) return [h("p", { class: "s2-empty", "data-state": "none_found" }, WORDS.noneFoundFlows)];
  return [h("table", { class: "am-table", "aria-label": "Workflows" },
    h("thead", {}, h("tr", {}, ["Workflow", "Status", "Basis", "Routes", "Rules"].map((c) => th("col", c)))),
    h("tbody", {}, doc.workflows.map((f) => {
      const m = FLOW[f.effective] || FLOW.unverified;
      return h("tr", { "data-flow": f.effective },
        th("row", opener(`${f.name}: ${m.word}. Open the steps`, f.id, f.name),
          f.inferred_name ? h("span", { class: "chip am-inferred", title: "the name is inferred from the routes" }, "name inferred") : null),
        h("td", {}, statusMark(m)),
        h("td", { title: BASIS_MEANING[f.basis] }, f.basis),
        h("td", {}, String(f.routes.length)),
        h("td", {}, String(f.rules.length)),
      );
    })))];
}

function lifecyclesPanel(doc) {
  if (hasNote(doc, "no_workflows_capture")) {
    return [h("p", { class: "s2-empty", "data-state": "no_workflows_capture" }, WORDS.noWorkflows)];
  }
  if (!doc.machines.length) return [h("p", { class: "s2-empty", "data-state": "none_found" }, WORDS.noneFoundMachines)];
  return doc.machines.map((m) => h("article", { class: "am-machine", "aria-label": m.key },
    h("h3", {}, opener(`${m.key}: open the details`, m.key, m.key), " ",
      h("span", { class: "chip" }, MACHINE[m.status] || m.status)),
    h("p", { class: "muted small" }, `States: ${m.states.join(", ")} · rules ${m.rules.join(", ") || "none"}${m.spec_target ? ` · spec ${m.spec_target}` : " · no matching spec machine"}`),
    h("table", { class: "am-table", "aria-label": `Edges of ${m.key}` },
      h("thead", {}, h("tr", {}, ["From", "To", "In the spec", "In the code"].map((c) => th("col", c)))),
      h("tbody", {}, m.edges_detail.map((e) => h("tr", {},
        h("td", {}, e.from), h("td", {}, e.to),
        h("td", {}, markYesNo(e.in_spec, "in spec", "not in spec")),
        h("td", {}, markYesNo(e.in_code, "in code", "not in code")))))),
    evidenceList(m.evidence),
  ));
}

const markYesNo = (on, yes, no) =>
  h("span", { class: ["s2-trust", on ? "trust-ok" : "trust-none"] }, icon(on ? "check" : "dash"), h("span", {}, on ? yes : no));

function seen(observed) {
  if (!observed.length) return "";
  const n = {};
  for (const o of observed) n[o.status] = (n[o.status] || 0) + 1;
  return Object.entries(n).map(([s, k]) => (k > 1 ? `${s}×${k}` : s)).join(", ");
}

function cellButton(c) {
  const spec = c.spec === "·" ? "Not specified" : c.spec;
  const code = c.route === null ? "Not in the code" : `Code: ${c.auth || "no auth read"}`;
  const sn = seen(c.observed);
  const label = `${c.row} on ${c.col}. Spec: ${spec}. ${code}.${sn ? ` Seen: ${sn}.` : ""} Open the details`;
  return opener(label, `cell:${c.row}|${c.col}`, [
    h("span", { class: "am-cell-spec" }, spec),
    h("span", { class: "am-cell-line small muted" }, code),
    sn ? h("span", { class: "am-cell-line small" }, `Seen: ${sn}`) : null,
  ], { class: "am-open am-cell-btn" });
}

function accessPanel(doc) {
  const out = [];
  const cellAt = new Map(doc.access.cells.map((c) => [`${c.row}|${c.col}`, c]));
  if (!doc.access.groups.length) out.push(h("p", { class: "s2-empty" }, "The spec states no access rules yet."));
  for (const g of doc.access.groups) {
    out.push(h("section", { class: "am-group", "aria-label": `Access: ${g.prefix}` },
      h("h3", {}, g.prefix),
      h("div", { class: "am-scroll", role: "region", tabindex: "0", "aria-label": `Access matrix: ${g.prefix}` },
        h("table", { class: "am-table am-grid", "aria-label": `Access: ${g.prefix}` },
          h("thead", {}, h("tr", {}, th("col", "Caller"), g.columns.map((col) => th("col", col, "am-col")))),
          h("tbody", {}, g.rows.map((row) => h("tr", {},
            th("row", row),
            g.columns.map((col) => {
              const c = cellAt.get(`${row}|${col}`);
              return h("td", { class: c && c.spec === "·" ? "am-cell am-unspec" : "am-cell" }, c ? cellButton(c) : "");
            }))))))));
  }
  out.push(h("dl", { class: "ctr-legend am-legend", "aria-label": "Legend" },
    WORDS.accessLegend.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
  out.push(h("p", { class: "muted small" }, WORDS.accessNote));
  const odd = doc.endpoints.filter((e) => e.status !== "implemented");
  if (odd.length) {
    out.push(h("section", { class: "am-group", "aria-label": "Endpoints that are not plainly in the code" },
      h("h3", {}, "Endpoints"),
      h("table", { class: "am-table", "aria-label": "Endpoints" },
        h("thead", {}, h("tr", {}, th("col", "Endpoint"), th("col", "Where it is"))),
        h("tbody", {}, odd.map((e) => h("tr", { "data-endpoint": e.status },
          th("row", opener(`${e.key}: ${ENDPOINT[e.status] || e.status}. Open the details`, `impl:route:${e.key}`, e.key)),
          h("td", {}, ENDPOINT[e.status] || e.status)))))));
  }
  return out;
}

function hasNote(doc, code) {
  return (doc.summary.notes || []).some((n) => n.code === code);
}

// ------------------------------------------------------------------ not read

function unreadSection(doc) {
  const head = [h("h2", {}, WORDS.unreadHead), h("p", { class: "muted" }, WORDS.unreadLede)];
  if (!doc.unread.length) {
    return h("section", { class: "am-unread", "aria-label": WORDS.unreadHead },
      ...head, h("p", { class: "s2-empty", "data-state": "unread_empty" }, WORDS.unreadEmpty));
  }
  return h("section", { class: "am-unread", "aria-label": WORDS.unreadHead }, ...head,
    h("ul", { class: "am-unread-list" }, doc.unread.map((u) => h("li", {},
      u.at ? h("code", { class: "am-code" }, u.at) : h("span", { class: "muted" }, "no location"),
      h("span", { class: "chip" }, u.what), ` ${u.reason}`))));
}

// ------------------------------------------------------------------ the drawer

function fact(label, value) {
  return [h("dt", {}, label), h("dd", {}, value === null || value === undefined || value === "" ? h("span", { class: "muted" }, "none") : value)];
}

function rowFacts(kind, row) {
  if (kind === "rule") {
    const m = IMPL[row.impl_status] || IMPL.no_element;
    return [
      ...fact("Says", row.text),
      ...fact("Spec", SPEC_STATUS[row.spec_status] || row.spec_status),
      ...fact("Implementation", statusMark(m)),
      ...fact("Basis", `${row.basis}: ${BASIS_MEANING[row.basis] || ""}`),
      ...fact("Reasons", row.reasons.length ? row.reasons.join("; ") : null),
      ...fact("Missing", row.missing.length ? chips(row.missing) : null),
      ...fact("Elements", row.elements.length
        ? h("ul", { class: "am-elements" }, row.elements.map((e) => h("li", {},
          h("code", {}, e.key), ` ${e.kind}, ${e.present ? "present" : "absent"}, ${e.verified ? "verified" : "not verified"}, ${e.basis}`)))
        : null),
      ...fact("Last check", row.last_check),
    ];
  }
  if (kind === "workflow") {
    const m = FLOW[row.effective] || FLOW.unverified;
    return [
      ...fact("Name", row.name + (row.inferred_name ? " (the name is inferred from the routes)" : "")),
      ...fact("Status", statusMark(m)),
      ...fact("Basis", `${row.basis}: ${BASIS_MEANING[row.basis] || ""}`),
      ...fact("Reason", row.reason),
      ...fact("Routes", chips(row.routes)),
      ...fact("Rules", chips(row.rules)),
      ...fact("Steps", h("table", { class: "am-table", "aria-label": "Steps" },
        h("thead", {}, h("tr", {}, ["#", "Request", "Caller", "Answer at capture", "Evidence"].map((c) => th("col", c)))),
        h("tbody", {}, row.steps.map((s) => h("tr", {},
          h("td", {}, String(s.i)), h("td", {}, `${s.method} ${s.route}`), h("td", {}, s.caller || "no token"),
          h("td", {}, String(s.expect_status)), h("td", {}, s.evidence.join(", ") || "none")))))),
    ];
  }
  if (kind === "machine") {
    return [
      ...fact("Status", MACHINE[row.status] || row.status),
      ...fact("States", row.states.join(", ")),
      ...fact("Spec machine", row.spec_target),
      ...fact("Edges", h("ul", {}, row.edges_detail.map((e) => h("li", {},
        `${e.from} → ${e.to}: ${e.in_spec ? "in spec" : "not in spec"}, ${e.in_code ? "in code" : "not in code"}`)))),
      ...fact("Rules", chips(row.rules)),
    ];
  }
  if (kind === "cell") {
    return [
      ...fact("Caller", row.row), ...fact("Endpoint", row.col),
      ...fact("Spec expects", row.spec === "·" ? "Not specified" : row.spec),
      ...fact("Code route", row.route), ...fact("Code auth", row.auth),
      ...fact("Code refusals", row.refusals && row.refusals.length ? row.refusals.join(", ") : null),
      ...fact("Endpoint", ENDPOINT[row.route_status] || row.route_status),
      ...fact("Seen", row.observed.length
        ? h("ul", {}, row.observed.map((o) => h("li", {}, `${o.flow} step ${o.step} answered ${o.status}`))) : null),
    ];
  }
  const ep = row.endpoint, rt = row.route;
  return [
    ...fact("Endpoint", ep.key), ...fact("Where it is", ENDPOINT[ep.status] || ep.status),
    ...fact("Code auth", rt ? rt.auth : null), ...fact("Handler", rt ? rt.handler : null),
    ...fact("Answers", rt ? rt.statuses.join(", ") : null),
    ...fact("Refusals", rt && rt.refusals.length ? rt.refusals.join(", ") : null),
  ];
}

function evidenceOf(kind, row) {
  if (kind === "rule" || kind === "workflow" || kind === "machine" || kind === "cell") return row.evidence;
  return row.route ? row.route.evidence : [];
}

function touchesBlock(t, meaning) {
  const names = [["rules", "Rules"], ["workflows", "Workflows"], ["machines", "Lifecycles"], ["cells", "Access cells"], ["routes", "Routes"]];
  return h("section", { class: "am-touches", "aria-label": WORDS.touchesHead, "data-basis": "static" },
    h("h3", {}, WORDS.touchesHead), h("p", { class: "muted small" }, `Static: ${meaning || WORDS.touchesMeaning}.`),
    h("dl", {}, names.flatMap(([k, label]) => fact(label, t[k].length ? chips(t[k]) : null))));
}

function drawer(ctx) {
  const d = S.drawer;
  if (!d) return null;
  const close = h("button", { type: "button", class: "btn btn-quiet", "aria-label": "Close the details", onclick: closeDrawer }, icon("close"), "Close");
  const body = [];
  if (d.loading) {
    body.push(h("p", { class: "muted", role: "status", "aria-live": "polite" }, "Reading the element…"));
  } else if (d.error) {
    body.push(h("p", { class: "imp-err", role: "alert" }, d.error));
  } else {
    const doc = d.doc;
    body.push(h("dl", { class: "am-facts" }, rowFacts(doc.kind, doc.row)));
    body.push(h("section", { class: "am-evidence-box", "aria-label": "Evidence" }, h("h3", {}, "Evidence"), evidenceList(evidenceOf(doc.kind, doc.row))));
    body.push(touchesBlock(doc.touches, doc.meaning));
    body.push(h("div", { class: "imp-actions" },
      h("button", { type: "button", class: "btn", onclick: () => ctx.askAbout(askTarget(doc.key)) }, icon("chat"), WORDS.askAbout),
      h("button", {
        type: "button", class: "btn",
        onclick: () => { ctx.changeAbout(askTarget(doc.key)); S.asked = doc.key; redraw(); },
      }, WORDS.whatBreaks)));
    if (S.asked === doc.key) body.push(h("p", { class: "imp-note", role: "status" }, WORDS.whatBreaksNote));
  }
  return h("aside", {
    class: "am-drawer", role: "complementary", tabindex: "-1", "aria-label": `Details of ${d.key}`,
    onkeydown: (e) => { if (e.key === "Escape") closeDrawer(); },
  }, h("header", { class: "am-drawer-head" }, h("h2", {}, h("code", {}, d.key)), close), ...body);
}

const askTarget = (key) => key;

// ------------------------------------------------------------------ the view

function primaryState(doc) {
  if (!doc) return "loading";
  if (doc.state !== "ok") return doc.state;
  if (doc.inputs.inventory.stale) return "stale";
  if (hasNote(doc, "no_workflows_capture")) return "no_workflows_capture";
  if (doc.rules.some((r) => r.impl_status === "required_not_implemented") || doc.endpoints.some((e) => e.status === "spec_only")) return "not_built";
  return "ok";
}

function draw(root, ctx) {
  clear(root);
  const doc = S.phase === "done" ? S.doc : null;
  root.setAttribute("data-state", S.phase === "error" ? "error" : primaryState(doc));
  add(root, h("header", { class: "view-head" }, h("h1", {}, WORDS.title), h("p", { class: "lede" }, WORDS.lede)));
  if (S.phase === "idle" || S.phase === "loading") {
    add(root, h("div", { class: "am-loading", role: "status", "aria-live": "polite", "data-state": "loading" }, WORDS.loading));
    return;
  }
  if (S.phase === "error") {
    add(root, stateBox("error", "The app map could not be read.", h("p", { class: "imp-err", role: "alert" }, S.error),
      h("button", { type: "button", class: "btn", onclick: () => { S = fresh(api.base()); mounted.ctx = ctx; load(); } }, "Try again")));
    return;
  }
  if (doc.state !== "ok") {
    add(root, nonOk(doc));
    return;
  }
  add(root, strip(doc), staleBanner(doc), notBuiltNote(doc), tabs());
  const panels = { rules: rulesPanel, workflows: workflowsPanel, lifecycles: lifecyclesPanel, access: accessPanel };
  for (const t of TABS) {
    add(root, h("section", {
      role: "tabpanel", id: `am-panel-${t.id}`, class: "am-panel", "aria-labelledby": `am-tab-${t.id}`,
      tabindex: "0", hidden: S.tab !== t.id,
    }, S.tab === t.id ? panels[t.id](doc) : null));
  }
  add(root, unreadSection(doc), drawer(ctx));
}

export function render(model, root, ctx) {
  if (S.base !== api.base()) S = fresh(api.base());
  mounted = { root, ctx };
  root.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.drawer) closeDrawer(); });
  draw(root, ctx);
  if (S.phase === "idle") load();
}
