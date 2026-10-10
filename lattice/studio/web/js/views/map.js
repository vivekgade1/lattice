// Map (home): how the business logic is tied together. Three columns read left to right:
// the rules that need attention, the endpoints they govern (grouped by resource) and the
// tables, services and lifecycle those endpoints touch (GET /api/graph, drawn with React
// Flow by ../graph.js). Every other rule is summarised as a rule count on the endpoint or
// table it governs; clicking the count focuses there and shows them. Click selects (global,
// shift adds), double-click or Enter on a selected element opens its own view. The first
// view is drawn at a readable size, top-left, and pans; "Fit all" is one click away.
// A List switch shows the same elements as a table. Without /api/graph (the real server)
// or when the bundle cannot load, the original tile Map is drawn instead.

import { h, icon, add, plural } from "../util.js";
import { state, update, subscribe } from "../store.js";
import { api } from "../api.js";
import { targetAttrs, toggleSelection, highlight, labelOf } from "../targets.js";
import { openChange, changeBanner } from "../overlay.js";
import { mountGraph, fetchGraph, toNode, toEdge, resolveIds, edgeLegend, TRUST_WORD, TRUST_LOOK, KIND_WORD } from "../graph.js";
import { render as renderTiles, resourceOf } from "./map-tiles.js";

const KEY = "studio-map-graph-2";
const KINDS = ["rule", "endpoint", "table", "service", "machine", "invariant", "scenario", "code"];
const OVERVIEW = ["rule", "endpoint", "table", "service", "machine"];
const ATTENTION = new Set(["probe_gap", "untraced", "not_built"]);
const CHANGED = new Set(["modified", "added", "removed"]);
const LIMIT = 400;
// column per kind: rules | checks and code | endpoints | data, services, lifecycle
const COLUMN = { rule: 0, scenario: 1, invariant: 1, code: 1, endpoint: 2, table: 3, service: 3, machine: 3 };
const ORDER = { rule: 0, invariant: 1, scenario: 2, code: 3, endpoint: 4, table: 5, machine: 6, service: 7 };

// View state: remembered for the session (sessionStorage, in try/catch) and across re-renders.
const S = {
  mode: "graph", // "graph" | "list"
  attention: false, // only what needs attention, with what it touches
  allRules: false, // every rule as a node on the overview (default: those needing attention)
  detail: true, // checks and code around a focus
  drawerOpen: false, // the selected element's details, expanded
  depth: 1,
  focus: null, // target the map is narrowed to, or null
  search: "",
};

try {
  const saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
  if (saved && typeof saved === "object") {
    if (saved.mode === "list" || saved.mode === "graph") S.mode = saved.mode;
    for (const k of ["attention", "allRules", "detail"]) if (typeof saved[k] === "boolean") S[k] = saved[k];
    if ([1, 2, 3].includes(saved.depth)) S.depth = saved.depth;
  }
} catch (e) {
  /* storage blocked: the choices last for this page only */
}

function persist() {
  try {
    const { mode, attention, allRules, detail, depth } = S;
    sessionStorage.setItem(KEY, JSON.stringify({ mode, attention, allRules, detail, depth }));
  } catch (e) {
    /* storage blocked */
  }
}

const needsAttention = (n) => !!n.failing || ATTENTION.has(n.trust);
const trustOf = (n) => (n.failing ? "failing" : n.trust);
const trustWord = (n) => TRUST_WORD[trustOf(n)] || n.trust;
const nodeText = (n) => `${n.label} ${n.sub || ""} ${n.id}`.toLowerCase();
const governing = (n) => n.governed_by || n.rules || [];

const linkCache = new Map();
function codeLinksOf(target) {
  if (!linkCache.has(target)) {
    linkCache.set(target, api.codeLinks(target).then((l) => (Array.isArray(l) ? l.length : 0)).catch(() => 0));
  }
  return linkCache.get(target);
}

/** Open an element's own view: its view for most kinds, the code lens for code. */
function openElement(ctx, node) {
  const t = node.target || node.id;
  if (node.kind === "code") ctx.openCode(t);
  else highlight([t]);
}

function trustTag(n) {
  const t = trustOf(n);
  const look = TRUST_LOOK[t] || TRUST_LOOK.context;
  return h("span", { class: `s2-trust trust-${look.tone}` }, icon(look.icon), h("span", {}, trustWord(n)));
}

export function render(model, root, ctx) {
  const hasSpec = (model.rules || []).length || (model.data_model?.tables || []).length;
  if (!hasSpec) {
    renderTiles(model, root, ctx);
    return;
  }

  S.search = ""; // a search does not outlive the view
  let data = null; // the last /api/graph response
  let shown = { nodes: [], edges: [], listNodes: [], dim: new Set() };
  let ctl = null;
  let token = 0;

  const switchEl = h("div", { class: "seg gr-switch", role: "group", "aria-label": "Map view" },
    [["graph", "Graph"], ["list", "List"]].map(([id, label]) =>
      h("button", {
        type: "button", class: "seg-btn", "data-mode": id, "aria-pressed": S.mode === id ? "true" : "false",
        onclick: () => { S.mode = id; persist(); paint(); },
      }, label)));
  const head = h("header", { class: "view-head map-head" },
    h("div", {},
      h("h1", {}, "Map"),
      h("p", { class: "lede" }, "Which rules govern which endpoints, and what data they touch."),
    ),
    switchEl,
  );
  const body = h("div", { class: "gr-body" });
  add(root, head, changeBanner(), body);

  // Toolbar controls are re-drawn on every change: put the keyboard focus back where it was.
  let refocus = null;
  const focusKey = () => document.activeElement?.closest?.(".gr-toolbar [data-ctl], .gr-options [data-ctl]")?.dataset.ctl || null;
  function restoreFocus(key) {
    if (!key) return;
    const el = body.querySelector(`[data-ctl="${key}"]`);
    if (!el || el.disabled) return;
    if (el.closest("details") && !el.closest("details").open) el.closest("details").open = true;
    el.focus({ preventScroll: true });
    if (key === "find") el.setSelectionRange?.(el.value.length, el.value.length);
  }

  // ---- data

  async function load() {
    const mine = ++token;
    refocus = focusKey() || refocus;
    ctl?.destroy();
    ctl = null;
    body.replaceChildren(h("p", { class: "muted", "aria-busy": "true", role: "status" }, "Reading the map…"));
    try {
      const res = await fetchGraph({
        focus: S.focus || undefined,
        depth: S.focus ? S.depth : undefined,
        kinds: S.focus && S.detail ? undefined : OVERVIEW.join(","),
        limit: LIMIT,
      });
      if (mine !== token) return;
      data = res;
      paint();
    } catch (e) {
      if (mine !== token) return;
      if (S.focus && /^(404|422)/.test(e.message)) {
        // The focus is not in the graph (a gap or a removed element): go back to the whole map.
        S.focus = null;
        load();
        return;
      }
      fallback(e);
    }
  }

  function fallback(err) {
    ctl?.destroy();
    ctl = null;
    root.replaceChildren();
    const note = err?.unsupported
      ? "The graph needs the mock server (the real server has no graph yet). Here is the tile map."
      : `The graph could not be drawn (${err?.message || "unknown error"}). Here is the tile map.`;
    renderTiles(state.model || model, root, ctx, { note });
  }

  // ---- what is drawn

  function selectedIds(idset) {
    return new Set(state.selection.flatMap((t) => resolveIds([t], idset)));
  }

  /** Nodes and edges to draw, from the last response and the view state. */
  function compute() {
    const diff = openChange() ? data.diff || {} : {};
    const all = data.nodes || [];
    const allIds = new Set(all.map((n) => n.id));
    const sel = selectedIds(allIds);
    const q = S.search.trim().toLowerCase();
    let nodes = all;
    let edges = data.edges || [];
    let hiddenRules = 0;
    if (!S.focus) {
      // Overview: a rule is a node only when it needs attention, the pending change touches
      // it, it is selected or it matches the search; the rest are counted on what they govern.
      const keepRule = (n) => S.allRules || needsAttention(n) || CHANGED.has(diff[n.id]) || sel.has(n.id) || (q && nodeText(n).includes(q));
      nodes = all.filter((n) => n.kind !== "rule" || keepRule(n));
      hiddenRules = all.length - nodes.length;
      // a rule's derived link to a table repeats the path through its endpoint
      edges = edges.filter((e) => !(e.inferred && e.kind === "applies_to"));
    }
    let attentionNote = null;
    if (S.attention) {
      const core = new Set(nodes.filter((n) => needsAttention(n) || CHANGED.has(diff[n.id])).map((n) => n.id));
      const keep = new Set(core);
      for (const e of edges) {
        if (core.has(e.source)) keep.add(e.target);
        if (core.has(e.target)) keep.add(e.source);
      }
      if (S.focus) keep.add(data.focus);
      nodes = nodes.filter((n) => keep.has(n.id));
      attentionNote = `${plural(core.size, "element")} need${core.size === 1 ? "s" : ""} attention.`;
    }
    const idset = new Set(nodes.map((n) => n.id));
    edges = edges.filter((e) => idset.has(e.source) && idset.has(e.target));
    const linked = new Set();
    for (const e of edges) { linked.add(e.source); linked.add(e.target); }
    const listNodes = all;
    return { all, nodes, edges, listNodes, diff, hiddenRules, attentionNote, sel };
  }

  /** API node -> bundle node, placed in its column (endpoints grouped by resource). */
  function place(n, showingRules) {
    const extra = { column: COLUMN[n.kind] ?? 1 };
    if (n.kind === "endpoint") extra.group = `res:${resourceOf(n.label)}`;
    if (n.kind === "machine") extra.sub = "lifecycle";
    if (n.kind === "rule") extra.sub = n.sub;
    if (n.kind === "endpoint" || n.kind === "table") {
      const k = governing(n).length;
      const drawn = governing(n).filter((r) => showingRules.has(`rule:${r}`)).length;
      if (k && (S.focus ? false : drawn < k)) extra.more = { label: plural(k, "rule"), title: `Show the ${plural(k, "rule")} that govern ${n.label}` };
    }
    return toNode(n, extra);
  }

  // ---- paint

  function paint() {
    const keep = focusKey() || refocus;
    refocus = null;
    ctl?.destroy();
    ctl = null;
    for (const b of switchEl.children) b.setAttribute("aria-pressed", String(b.dataset.mode === S.mode));
    const c = compute();
    shown = { ...c, dim: new Set() };
    applySearch();
    const { nodes, edges, listNodes, diff } = c;
    const ruleIds = new Set(nodes.filter((n) => n.kind === "rule").map((n) => n.id));

    const focusNode = S.focus ? c.all.find((n) => n.id === data.focus) : null;
    const status = h("p", { class: "gr-status", role: "status", "aria-live": "polite" });
    const counts = {};
    for (const n of nodes) counts[n.kind] = (counts[n.kind] || 0) + 1;
    const lines = [];
    if (S.focus) {
      lines.push(`What ${focusNode ? focusNode.label : labelOf(S.focus)} is tied to, ${S.depth === 1 ? "one step" : `${S.depth} steps`} out: ${plural(nodes.length, "element")}.`);
    } else {
      const ruleCount = nodes.filter((n) => n.kind === "rule" && (needsAttention(n) || CHANGED.has(diff[n.id]))).length;
      lines.push(`${plural(counts.endpoint || 0, "endpoint")}, ${plural(counts.table || 0, "table")}.`);
      lines.push(S.allRules
        ? `All ${plural(counts.rule || 0, "rule")}.`
        : `${plural(ruleCount, "rule")} ${ruleCount === 1 ? "needs" : "need"} attention; the other ${c.hiddenRules} are counted on what they govern.`);
    }
    if (data.truncated) lines.push(`Showing the nearest ${data.nodes.length}.`);
    if (c.attentionNote) lines.push(c.attentionNote);
    status.textContent = lines.join(" ");
    const searchNote = h("span", { class: "gr-search-note" });
    searchNote.textContent = searchSummary();
    status.append(" ", searchNote);

    // added targets have no node until the change is accepted: say so instead of drawing them
    const ghost = Object.keys(diff).filter((t) => diff[t] === "added" && !c.all.some((n) => n.id === t));
    const empty = !listNodes.length;

    const stage = h("div", { class: "gr-stage" });
    const canvas = h("div", { class: "gr-canvas is-map", "data-view": "map-graph" });
    const drawer = h("aside", { class: "gr-drawer", "aria-label": "Selected element", hidden: true });
    stage.append(canvas, drawer);

    body.replaceChildren(
      buildToolbar(),
      status,
      ...(ghost.length
        ? [h("p", { class: "gr-ghost", role: "status" }, icon("sparkle"),
            `The pending change also adds ${plural(ghost.length, "element")} that ${ghost.length === 1 ? "is" : "are"} not drawn until accepted.`)]
        : []),
      empty
        ? h("p", { class: "s2-empty" }, "Nothing matches these filters.")
        : S.mode === "list"
          ? listView(listNodes, data.edges || [])
          : stage,
      empty || S.mode === "list" ? null : legend(),
    );
    if (!empty && S.mode === "graph") {
      sizeCanvas(canvas);
      const columns = S.focus
        ? [{ label: "Rules" }, { label: "Checks and code" }, { label: "Endpoints" }, { label: "Data, services, lifecycle" }]
        : [{ label: S.allRules ? "Rules" : "Rules needing attention" }, null, { label: "Endpoints, by resource" }, { label: "Data, services, lifecycle" }];
      const groups = {};
      for (const n of nodes) if (n.kind === "endpoint") groups[`res:${resourceOf(n.label)}`] = { label: resourceOf(n.label) };
      // resources with something needing attention come first
      const hot = new Set(nodes.filter((n) => n.kind === "endpoint" && (needsAttention(n) || diff[n.id])).map((n) => resourceOf(n.label)));
      const resKey = (n) => `${hot.has(resourceOf(n.label)) ? 0 : 1}${resourceOf(n.label)}`;
      const sorted = [...nodes].sort((a, b) =>
        (ORDER[a.kind] - ORDER[b.kind]) ||
        (a.kind === "endpoint" ? resKey(a).localeCompare(resKey(b)) || pathKey(a.label).localeCompare(pathKey(b.label)) : 0) ||
        a.label.localeCompare(b.label, undefined, { numeric: true }));
      ctl = mountGraph(canvas, {
        nodes: sorted.map((n) => place(n, ruleIds)),
        edges: edges.map(toEdge),
        layout: S.focus ? "layers" : "columns",
        columns,
        groups,
        diff,
        dim: shown.dim,
        label: S.focus ? `What ${focusNode?.label || labelOf(S.focus)} is tied to` : "Business-logic map of the app",
        onSelect: selectNode,
        onOpen: (id) => {
          const n = c.all.find((x) => x.id === id);
          if (n) openElement(ctx, n);
        },
        onExpand: (id) => focusOn(id),
        onError: () => {
          S.mode = "list";
          for (const b of switchEl.children) b.setAttribute("aria-pressed", String(b.dataset.mode === "list"));
          body.insertBefore(h("p", { class: "s2-empty gr-note", role: "status" }, "The graph could not load, so the list is shown."), body.firstChild);
          stage.replaceWith(listView(listNodes, data.edges || []));
          drawerCleanup();
        },
      });
      drawFor(drawer);
      unsubDrawer?.();
      unsubDrawer = subscribe((st, patch) => {
        if (!drawer.isConnected) return drawerCleanup();
        if ("selection" in patch) {
          // a rule that is not on the overview joins it when selected
          if (!S.focus && !S.allRules) {
            const want = selectedIds(new Set(c.all.map((n) => n.id)));
            const missing = [...want].some((id) => !nodes.some((n) => n.id === id));
            const stale = [...ruleIds].some((id) => !needsAttention(c.all.find((n) => n.id === id)) && !CHANGED.has(diff[id]) && !want.has(id) && !(S.search && nodeText(c.all.find((n) => n.id === id)).includes(S.search.trim().toLowerCase())));
            if (missing || stale) {
              paint();
              return;
            }
          }
          drawFor(drawer);
          const fb = body.querySelector('[data-ctl="focus"]');
          const last = state.selection[state.selection.length - 1] || null;
          if (fb) {
            fb.disabled = !last;
            fb.title = last ? `Show only what ${labelOf(last)} is tied to` : "Select an element first";
          }
        }
      });
    }
    restoreFocus(keep);
  }

  function pathKey(label) {
    const [m, p] = label.split(" ");
    return `${p} ${["GET", "POST", "PUT", "PATCH", "DELETE"].indexOf(m)}`;
  }

  /** The canvas fills the rest of the main area, so the map reads without page scrolling. */
  function sizeCanvas(canvas) {
    const fit = () => {
      if (!canvas.isConnected) {
        window.removeEventListener("resize", fit);
        return;
      }
      const main = canvas.closest("main") || document.querySelector("main");
      const top = canvas.getBoundingClientRect().top + (main ? main.scrollTop : 0) - (main ? main.getBoundingClientRect().top : 0);
      const avail = (main ? main.clientHeight : window.innerHeight) - top - 44;
      canvas.style.height = `${Math.max(380, Math.min(960, Math.round(avail)))}px`;
    };
    fit();
    window.addEventListener("resize", fit);
  }

  let unsubDrawer = null;
  let searchTimer = 0;
  function drawerCleanup() {
    unsubDrawer?.();
    unsubDrawer = null;
  }

  function selectNode(id, { additive }) {
    const n = (data?.nodes || []).find((x) => x.id === id);
    if (!n) return;
    const t = n.target || n.id;
    if (additive) toggleSelection(t);
    else if (state.selection.length === 1 && state.selection[0] === t) update({ selection: [] });
    else update({ selection: [t] });
  }

  // ---- toolbar: find, needs attention, focus, and one View options menu

  function buildToolbar() {
    const sel = state.selection[state.selection.length - 1] || null;
    const search = h("label", { class: "gr-search" },
      icon("search"),
      h("input", {
        type: "search", class: "gr-input", "data-ctl": "find", placeholder: "Find a rule, route or table", value: S.search, "aria-label": "Find an element on the map",
        oninput: (e) => {
          S.search = e.target.value;
          clearTimeout(searchTimer);
          searchTimer = setTimeout(() => { refocus = "find"; paint(); }, 220);
        },
      }));
    const attention = h("button", {
      type: "button", class: "btn gr-toggle", "aria-pressed": S.attention ? "true" : "false", "data-ctl": "attention",
      title: "Only what fails, has a probe gap, is untraced or not built, or is touched by the pending change, with what it is tied to",
      onclick: () => { S.attention = !S.attention; persist(); paint(); },
    }, icon("triangle"), "Needs attention");
    const focus = S.focus
      ? h("span", { class: "gr-focus-chip" },
          h("span", {}, "Around ", h("strong", {}, (data.nodes || []).find((n) => n.id === data.focus)?.label || labelOf(S.focus))),
          h("button", { type: "button", class: "btn btn-quiet", "data-act": "show-all", "data-ctl": "show-all", onclick: () => { S.focus = null; load(); } }, icon("close"), "Show all"))
      : h("button", {
          type: "button", class: "btn", disabled: !sel, "data-act": "focus", "data-ctl": "focus",
          title: sel ? `Show only what ${labelOf(sel)} is tied to` : "Select an element first",
          onclick: () => focusOn(state.selection[state.selection.length - 1]),
        }, icon("diamond"), "Focus on selection");
    return h("div", { class: "gr-toolbar" }, search, attention, focus, viewOptions());
  }

  function viewOptions() {
    const check = (key, label, help, after = () => paint()) =>
      h("label", { class: "gr-check", title: help },
        h("input", { type: "checkbox", "data-ctl": `opt-${key}`, checked: S[key], onchange: (e) => { S[key] = e.target.checked; persist(); after(); } }),
        h("span", {}, label));
    const det = h("details", { class: "gr-options" },
      h("summary", { class: "btn", "data-ctl": "options" }, "View options"),
      h("div", { class: "gr-options-panel", role: "group", "aria-label": "View options" },
        check("allRules", "Show every rule on the map", "Off: only rules that need attention are drawn; the rest are counted on the endpoints and tables they govern", () => { if (!S.focus) paint(); }),
        check("detail", "Include checks and code around a focus", "Scenarios, invariants and code that tie a focused element to the rest", () => { if (S.focus) load(); }),
        h("label", { class: "gr-field" }, h("span", {}, "Focus reaches"),
          h("select", {
            class: "gr-select", "aria-label": "Focus depth", "data-ctl": "depth",
            onchange: (e) => { S.depth = Number(e.target.value); persist(); if (S.focus) load(); },
          }, [1, 2, 3].map((d) => h("option", { value: String(d), selected: S.depth === d }, d === 1 ? "1 step" : `${d} steps`)))),
        h("button", { type: "button", class: "btn btn-quiet", "data-ctl": "fit", onclick: () => ctl?.fit() }, "Fit all on screen"),
      ));
    det.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && det.open) {
        e.stopPropagation();
        det.open = false;
        det.querySelector("summary").focus();
      }
    });
    return det;
  }

  function focusOn(target) {
    if (!target) return;
    S.focus = target;
    S.search = "";
    load();
  }

  function applySearch() {
    const q = S.search.trim().toLowerCase();
    shown.dim = new Set();
    shown.hits = 0;
    if (!q) return;
    for (const n of shown.nodes) {
      if (nodeText(n).includes(q)) shown.hits += 1;
      else shown.dim.add(n.id);
    }
  }

  function searchSummary() {
    return S.search.trim() ? `${plural(shown.hits || 0, "match", "matches")} for “${S.search.trim()}”.` : "";
  }

  // ---- drawer (over the canvas, only while something is selected)

  const FWD = { enforces: "enforces", reads: "reads", writes: "writes", traces: "checked by", implements: "implements", applies_to: "applies to", transitions: "governs" };
  const BACK = { enforces: "governed by", reads: "read by", writes: "written by", traces: "checks", implements: "implemented by", applies_to: "governed by", transitions: "governed by" };

  /** The selected element's lines in words, grouped: "enforces: POST /… (inferred)". */
  function lineWords(node) {
    const byId = new Map((data.nodes || []).map((n) => [n.id, n]));
    const groupsOf = new Map();
    for (const e of data.edges || []) {
      if (e.source !== node.id && e.target !== node.id) continue;
      const out = e.source === node.id;
      const other = byId.get(out ? e.target : e.source);
      if (!other) continue;
      let word = out ? FWD[e.kind] : BACK[e.kind];
      if (e.kind === "traces" && node.kind === "scenario" && out && other.kind === "endpoint") word = "calls";
      if (e.kind === "traces" && node.kind === "endpoint") word = "checked by";
      word = word || e.kind;
      if (!groupsOf.has(word)) groupsOf.set(word, []);
      groupsOf.get(word).push(other.label + (e.inferred ? " (inferred)" : ""));
    }
    return h("dl", { class: "gr-drawer-lines" }, [...groupsOf].map(([word, items]) => [
      h("dt", {}, word),
      h("dd", {}, items.slice(0, 4).join(", ") + (items.length > 4 ? `, and ${items.length - 4} more` : "")),
    ]));
  }

  function drawFor(drawer) {
    const all = data?.nodes || [];
    const ids = new Set(all.map((n) => n.id));
    const sel = state.selection;
    let node = null;
    let target = sel[sel.length - 1] || null;
    for (let i = sel.length - 1; i >= 0 && !node; i--) {
      const [id] = resolveIds([sel[i]], ids);
      if (id) {
        node = all.find((n) => n.id === id);
        target = sel[i];
      }
    }
    drawer.replaceChildren();
    drawer.hidden = !target;
    if (!target) return;
    const t = node ? node.target || node.id : target;
    const rules = node && node.kind !== "rule" ? governing(node) : [];
    const codeBtn = h("button", {
      type: "button", class: "btn btn-quiet", disabled: true, "data-act": "code",
      title: "Looking for linked code…", onclick: () => ctx.openCode(t),
    }, icon("text-lines"), "Code");
    const lineCount = node ? (data.edges || []).filter((e) => e.source === node.id || e.target === node.id).length : 0;
    const more = h("details", { class: "gr-drawer-more", open: S.drawerOpen },
      h("summary", {}, node ? `What it is tied to (${plural(lineCount, "line")})` : "Details"),
      node?.sub ? h("p", { class: "gr-drawer-sub" }, node.sub) : null,
      rules.length ? h("p", { class: "gr-drawer-sub" }, `Governed by ${plural(rules.length, "rule")}: ${rules.slice(0, 8).join(", ")}${rules.length > 8 ? "…" : ""}`) : null,
      node ? lineWords(node) : h("p", { class: "muted" }, "Not on the map as drawn now."));
    more.addEventListener("toggle", () => { S.drawerOpen = more.open; });
    add(drawer,
      h("div", { class: "gr-drawer-id" },
        h("h2", { class: "gr-drawer-title" }, node ? node.label : labelOf(target)),
        h("span", { class: "gr-drawer-kind" }, node ? KIND_WORD[node.kind] || node.kind : "Selected"),
        node ? trustTag(node) : null),
      h("div", { class: "gr-drawer-actions" },
        node && S.focus !== t
          ? h("button", { type: "button", class: "btn", "data-act": "focus-here", onclick: () => focusOn(t) }, icon("diamond"), rules.length ? "Show its rules" : "Focus here")
          : null,
        node ? h("button", { type: "button", class: "btn", "data-act": "open", onclick: () => openElement(ctx, node) }, "Open") : null,
        h("button", { type: "button", class: "btn btn-quiet", onclick: () => ctx.askAbout(t) }, icon("chat"), "Ask"),
        h("button", { type: "button", class: "btn btn-quiet", onclick: () => ctx.changeAbout(t) }, icon("sparkle"), "Change"),
        codeBtn,
        h("button", { type: "button", class: "btn btn-quiet gr-drawer-close", "aria-label": "Clear the selection", title: "Clear the selection (Esc)", onclick: () => update({ selection: [] }) }, icon("close")),
      ),
      more,
    );
    if (node?.kind === "code") {
      codeBtn.disabled = false;
      codeBtn.title = "Show this code";
    } else {
      codeLinksOf(t).then((n) => {
        if (!codeBtn.isConnected) return;
        codeBtn.disabled = !n;
        codeBtn.title = n ? `Show the ${plural(n, "code span")} linked to this element` : "No code is linked to this element yet";
      });
    }
  }

  // ---- list alternative

  function listView(nodes, edges) {
    const deg = new Map();
    for (const e of edges) {
      deg.set(e.source, (deg.get(e.source) || 0) + 1);
      deg.set(e.target, (deg.get(e.target) || 0) + 1);
    }
    const q = S.search.trim().toLowerCase();
    const rows = q ? nodes.filter((n) => nodeText(n).includes(q)) : nodes;
    const sorted = [...rows].sort((a, b) =>
      (ORDER[a.kind] - ORDER[b.kind]) || (Number(!!b.failing) - Number(!!a.failing)) || a.label.localeCompare(b.label, undefined, { numeric: true }));
    return h("div", { class: "map-list scroll-x", "data-view": "map-list" }, h("table", {},
      h("caption", { class: "s2-sr" }, "Every element on the map with its kind and trust state"),
      h("thead", {}, h("tr", {}, ["Element", "Kind", "Trust", "Rules", "Failing", "Lines", ""].map((c) => h("th", { scope: "col" }, c)))),
      h("tbody", {}, sorted.map((n) => h("tr", { ...targetAttrs(n.target || n.id), "data-kind": n.kind },
        h("th", { scope: "row" }, h("span", { class: "s2-mono" }, n.label), n.sub ? h("span", { class: "gr-list-sub" }, n.sub) : null),
        h("td", {}, KIND_WORD[n.kind] || n.kind),
        h("td", {}, trustTag(n)),
        h("td", { class: "num" }, String(n.kind === "rule" ? 1 : governing(n).length)),
        h("td", { class: "num" }, String(n.failing || 0)),
        h("td", { class: "num" }, String(deg.get(n.id) || 0)),
        h("td", {}, h("span", { class: "gr-list-acts" },
          h("button", { type: "button", class: "btn btn-quiet", onclick: () => { S.mode = "graph"; persist(); focusOn(n.target || n.id); } }, "Focus"),
          h("button", { type: "button", class: "btn btn-quiet", onclick: () => openElement(ctx, n) }, "Open"))),
      ))),
    ));
  }

  function legend() {
    return h("div", { class: "gr-legend-wrap" },
      edgeLegend(),
      h("p", { class: "muted small" }, "A trust word appears only when something is not checked. Select an element to read its lines in words."),
    );
  }

  load();
}
