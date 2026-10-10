// Graph adapter: the one place the React Flow bundle (vendor/graph.js) is touched.
//
//   mountGraph(host, {nodes, edges, layout, direction, columns, groups, readableZoom, diff, dim,
//                     minimap, selected, marked, onSelect(id, {additive}), onOpen(id),
//                     onExpand(id), onClear(), onError(err), label})
//     -> {update(patch), fit(), readable(), viewport(), destroy(), ready}
//
// * The bundle and its stylesheet are loaded lazily, once, on the first mount.
// * Selection and marks come from the store (state.selection / state.marked) unless the
//   caller passes `selected` / `marked` id lists. Targets resolve to node ids (a cell to
//   its endpoint, a column to its table).
// * The graph cleans itself up: it unmounts when its host leaves the document (the shell
//   re-renders the main area on most changes), so views never have to.
// * A load failure shows a calm message in the host, or calls onError so the view can draw
//   its own alternative.
// * `dim` (a Set of ids) fades the other nodes; the shell's applyMarks strips classes it
//   does not own, so selection/mark/dim are re-applied to the DOM after every mutation.

import { h, s } from "./util.js";
import { state, subscribe, update } from "./store.js";
import { api } from "./api.js";

const BUNDLE = new URL("../vendor/graph.js", import.meta.url).href;
const STYLE = new URL("../vendor/graph.css", import.meta.url).href;

let bundle = null;
let styleReady = null;

function loadStyle() {
  if (!styleReady) {
    styleReady = new Promise((resolve) => {
      const link = h("link", { rel: "stylesheet", href: STYLE, "data-graph-css": "1" });
      link.addEventListener("load", () => resolve(true));
      link.addEventListener("error", () => resolve(false));
      document.head.append(link);
    });
  }
  return styleReady;
}

/** Resolves to the bundle's mount(); rejects (and forgets) when the bundle cannot load. */
export function loadBundle() {
  if (!bundle) {
    bundle = (async () => {
      const [mod] = await Promise.all([import(BUNDLE), loadStyle()]);
      const mount = mod.mount || mod.default?.mount;
      if (typeof mount !== "function") throw new Error("The graph bundle has no mount().");
      return mount;
    })().catch((e) => {
      bundle = null;
      throw e;
    });
  }
  return bundle;
}

// ------------------------------------------------------------------ data

const cache = new Map();

function modelKey() {
  const m = state.model;
  const c = state.change;
  return [
    api.base(),
    m?.project?.git_head || "",
    m?.app?.last_check?.at || "",
    JSON.stringify((m?.changes || []).map((x) => [x.id, x.status, x.build?.state])),
    c ? `${c.id}:${c.status}` : "",
  ].join("|");
}

/** GET /api/graph, shared across views while the model has not moved. */
export function fetchGraph(params = {}) {
  const key = `${modelKey()}#${JSON.stringify(params)}`;
  if (cache.has(key)) return cache.get(key);
  if (cache.size > 16) cache.delete(cache.keys().next().value);
  const p = api.graph(params).catch((e) => {
    cache.delete(key);
    throw e;
  });
  cache.set(key, p);
  return p;
}

// ------------------------------------------------------------------ vocabulary

export const TRUST_WORD = {
  checked: "Checked",
  unchecked: "Not checked",
  context: "Context",
  untraced: "Untraced",
  probe_gap: "Probe gap",
  not_built: "Not built",
  failing: "Failing",
};

/** The app's treatment of each trust state: one tone, icon and word (see badges.js). */
export const TRUST_LOOK = {
  checked: { tone: "ok", icon: "shield-check" },
  probe_gap: { tone: "warn", icon: "triangle" },
  untraced: { tone: "gap", icon: "question" },
  unchecked: { tone: "none", icon: "text-lines" },
  context: { tone: "none", icon: "info" },
  not_built: { tone: "none", icon: "dash" },
  failing: { tone: "bad", icon: "diamond" },
};

export const KIND_WORD = {
  rule: "Rule", endpoint: "Endpoint", table: "Table", service: "Service", machine: "Lifecycle",
  invariant: "Invariant", scenario: "Scenario", code: "Code", state: "State", stage: "Stage", caller: "Caller",
};

/** What each line kind means, in words. */
export const EDGE_WORD = {
  enforces: "enforces",
  reads: "reads",
  writes: "writes",
  traces: "checked by",
  implements: "implements",
  applies_to: "applies to",
  transitions: "governs",
};

/** The one legend the graph needs: direct and inferred lines (a line sample plus the word). */
export function edgeLegend() {
  const row = (cls, word, why) =>
    h("li", { title: why },
      s("svg", { class: "gr-legend-line", width: "28", height: "8", viewBox: "0 0 28 8", "aria-hidden": "true", focusable: "false" },
        s("line", { x1: "1", y1: "4", x2: "27", y2: "4", class: `gr-line ${cls}` })),
      h("span", {}, word));
  return h("ul", { class: "gr-legend", "aria-label": "Line styles" },
    row("", "linked", "A direct link in the spec, the code index or a traced flow"),
    row("gr-line-inferred", "inferred", "Derived: a rule reaches this endpoint or table through a scenario or code"));
}

/** API node -> bundle node. Checked is the quiet default, so it carries no trust word. */
export function toNode(n, extra = {}) {
  // checked is the quiet default, and context (no rule touches it) says nothing either
  const trust = n.failing ? "failing" : n.trust === "checked" || n.trust === "context" ? undefined : n.trust;
  const trustLabel = n.failing ? `${n.failing} failing` : undefined;
  const out = { id: n.id, kind: n.kind, label: n.label, sub: n.sub || "", trust, trustLabel, badges: [], target: n.target || n.id, ...extra };
  return out;
}

export const toEdge = (e) => ({
  id: e.id,
  source: e.source,
  target: e.target,
  kind: e.kind,
  label: e.label,
  state: e.inferred ? "dashed" : undefined,
});

/** Store targets -> node ids present in `ids` (a cell becomes its endpoint, a column its table). */
export function resolveIds(targets, ids) {
  const out = [];
  for (const t of targets || []) {
    let id = null;
    if (ids.has(t)) id = t;
    else if (t.startsWith("cell:")) {
      const route = t.slice(5).split("|")[1];
      if (route && ids.has(`endpoint:${route}`)) id = `endpoint:${route}`;
    } else if (t.startsWith("profile:") && t.includes(".")) {
      const base = t.slice(0, t.indexOf("."));
      if (ids.has(base)) id = base;
    }
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

// ------------------------------------------------------------------ mount

export function mountGraph(host, opts = {}) {
  const o = { direction: "LR", layout: "dagre", nodes: [], edges: [], diff: {}, ...opts };
  let ids = new Set(o.nodes.map((n) => n.id));
  let handle = null;
  let dead = false;
  let raf = 0;
  host.classList.add("gr-host");
  const mountEl = h("div", { class: "gr-mount" });
  const status = h("p", { class: "gr-loading", role: "status", "aria-busy": "true" }, "Drawing the graph…");
  host.replaceChildren(mountEl, status);

  const selectedIds = () => (Array.isArray(o.selected) ? o.selected : resolveIds(state.selection, ids));
  const markedIds = () => (Array.isArray(o.marked) ? o.marked : resolveIds(state.marked, ids));
  const props = () => ({
    nodes: o.nodes,
    edges: o.edges,
    direction: o.direction,
    layout: o.layout,
    columns: o.columns,
    groups: o.groups,
    readableZoom: o.readableZoom,
    selected: selectedIds(),
    marked: markedIds(),
    diff: o.diff || {},
    minimap: !!o.minimap,
    onSelect: (id, ex) => o.onSelect && o.onSelect(id, ex),
    onOpen: (id) => o.onOpen && o.onOpen(id),
    onExpand: (id) => o.onExpand && o.onExpand(id),
    onClear: () => (o.onClear ? o.onClear() : update({ selection: [] })),
  });

  // The shell's applyMarks removes is-selected / is-marked from everything under main; put
  // back what the graph owns (and the dim flag) whenever the DOM moves.
  function syncDom() {
    raf = 0;
    if (dead) return;
    const sel = new Set(selectedIds());
    const mk = new Set(markedIds());
    for (const b of mountEl.querySelectorAll(".lg-node[data-lg-id]")) {
      const id = b.dataset.lgId;
      b.classList.toggle("is-selected", sel.has(id));
      b.classList.toggle("is-marked", mk.has(id));
      const dim = !!(o.dim && o.dim.has(id));
      if (dim !== b.hasAttribute("data-dim")) {
        if (dim) b.setAttribute("data-dim", "true");
        else b.removeAttribute("data-dim");
      }
    }
  }
  const schedule = () => {
    if (!raf && !dead) raf = setTimeout(syncDom, 16);
  };
  const observer = new MutationObserver(schedule);
  observer.observe(mountEl, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });

  // Enter and Space on a node are the graph's; keep the shell's own handler (which also
  // toggles the selection on data-target elements) from doubling them up.
  host.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.closest?.(".lg-node")) e.stopPropagation();
  });

  const unsub = subscribe((st, patch) => {
    if (dead) return;
    if (!host.isConnected) {
      destroy();
      return;
    }
    if (("selection" in patch || "marked" in patch) && handle) {
      handle.update({ selected: selectedIds(), marked: markedIds() });
      schedule();
    }
  });

  function fail(err) {
    if (dead) return;
    if (o.onError) {
      o.onError(err);
      return;
    }
    host.replaceChildren(
      h("div", { class: "gr-error", role: "status" },
        h("p", {}, "The graph could not be drawn."),
        h("p", { class: "muted" }, "Everything it shows is also in the list view. " + (err?.message || "")),
        h("button", { type: "button", class: "btn", onclick: () => location.reload() }, "Reload the page")));
  }

  const ready = (async () => {
    let mount;
    try {
      mount = await loadBundle();
    } catch (e) {
      fail(e);
      return false;
    }
    if (dead) return false;
    if (!host.isConnected) {
      // The view was replaced while the bundle loaded.
      destroy();
      return false;
    }
    try {
      handle = mount(mountEl, props());
    } catch (e) {
      handle = null;
      fail(e);
      return false;
    }
    status.remove();
    const label = o.label || "Business-logic graph";
    mountEl.setAttribute("role", "group");
    mountEl.setAttribute("aria-label", label);
    schedule();
    return true;
  })();

  function destroy() {
    if (dead) return;
    dead = true;
    unsub();
    observer.disconnect();
    if (raf) clearTimeout(raf);
    try {
      handle?.destroy();
    } catch (e) {
      /* already unmounted */
    }
    handle = null;
  }

  return {
    ready,
    update(patch = {}) {
      Object.assign(o, patch);
      if (patch.nodes) ids = new Set(o.nodes.map((n) => n.id));
      if (handle) handle.update(props());
      schedule();
    },
    fit() {
      handle?.fit();
    },
    /** The first view again: everything at a readable size, or top-left. */
    readable() {
      handle?.readable();
    },
    /** {x, y, zoom} of the canvas, or null before the bundle has drawn. */
    viewport() {
      return handle?.viewport ? handle.viewport() : null;
    },
    destroy,
    get alive() {
      return !dead;
    },
  };
}
