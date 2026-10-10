// Lifecycle: one React Flow diagram per state machine (states as nodes, moves as lines,
// initial and final marked in words, pending moves marked added or removed), with the
// transitions table below as the accessible alternative. The graph comes from
// GET /api/graph (`machines`); without it (the real server) or when the bundle cannot
// load, the original hand-drawn SVG diagram is shown instead.

import { h, s, add } from "../util.js";
import { targetAttrs, toggleSelection } from "../targets.js";
import { mountGraph, fetchGraph } from "../graph.js";
import { rulesByTarget, appResult, machineChange, changeBanner } from "../overlay.js";
import { resultBadge, pendingBadge } from "../badges.js";
import { actions, impactClass, ruleLinks, emptyState } from "./rules.js";

export function render(model, root, ctx) {
  const machines = model.views?.section6?.machines || [];
  add(root,
    h("header", { class: "view-head" },
      h("h1", {}, "Lifecycle"),
      h("p", { class: "lede" }, "The status moves the spec allows. Any move not drawn is refused."),
    ),
    changeBanner(),
  );
  if (!machines.length) {
    add(root, emptyState("The spec declares no lifecycles yet. Describe how something moves from state to state.", {
      label: "Describe a lifecycle",
      onclick: () => ctx.prefillChange("A lifecycle: ", []),
    }));
    return;
  }
  const idx = rulesByTarget(model);
  // One shared request for every machine's graph data; null when the server has none.
  const graphData = fetchGraph({ kinds: "machine", limit: 1 }).then((g) => g.machines || []).catch(() => null);
  for (const [target, name, sentence] of machines) {
    const change = machineChange(target);
    const res = appResult(target);
    const m = parseMachine(sentence);
    add(root,
      h("section", { ...targetAttrs(target), class: ["machine", "acts-host", change && "diff-modified", !change && impactClass(target)], "aria-label": `Lifecycle ${name}` },
        h("header", { class: "entity-head" },
          h("h2", {}, name),
          res !== undefined && res !== null ? resultBadge(res) : null,
          change ? pendingBadge("Changed by pending edit") : null,
          ruleLinks(idx, target),
        ),
        actions(ctx, target, { label: `lifecycle ${name}` }),
        change && change.new
          ? h("p", { class: "machine-sentence pending-text" },
              h("del", {}, sentence), h("span", { class: "cell-arrow", "aria-hidden": "true" }, " → "), h("strong", {}, change.new))
          : h("p", { class: "machine-sentence" }, sentence),
        diagramHost(m, name, target, sentence, change, graphData, ctx),
        transitionsTable(m, name),
      ),
    );
  }
}

/** The graph of one machine; the SVG diagram stands in until (or instead of) the graph. */
function diagramHost(m, name, target, sentence, change, graphData, ctx) {
  const wrap = h("div", { class: "machine-diagram" });
  const svgFallback = () =>
    h("div", { class: "scroll-x diagram-wrap", tabindex: "0", role: "region", "aria-label": `${name} diagram` }, diagram(m, name));
  wrap.append(h("p", { class: "muted small", role: "status" }, "Drawing the lifecycle…"));
  graphData.then((list) => {
    if (!wrap.isConnected) return;
    const mine = (list || []).find((x) => x.target === target);
    if (!mine) {
      wrap.replaceChildren(svgFallback());
      return;
    }
    const g = machineGraph(mine, m, change);
    const canvas = h("div", { class: "gr-canvas is-compact", "data-view": "machine-graph", "data-machine": target });
    wrap.replaceChildren(canvas, h("p", { class: "muted small gr-machine-note" },
      `Start: ${g.initial.join(", ") || "none"}. Final: ${g.final.join(", ") || "none"}. Any move not drawn is refused.${g.pending ? " Dashed green lines are moves the pending change adds; dotted red lines are moves it removes." : ""}`));
    mountGraph(canvas, {
      nodes: g.nodes,
      edges: g.edges,
      direction: "LR",
      diff: g.diff,
      selected: [],
      label: `${name} lifecycle graph`,
      onSelect: () => toggleSelection(target),
      onOpen: () => ctx.openCode(target),
      onError: () => wrap.replaceChildren(svgFallback()),
    });
  });
  return wrap;
}

/** API machine (+ the pending change's new moves) -> bundle nodes and edges. */
function machineGraph(api, parsed, change) {
  const states = new Map(api.states.map((x) => [x.id, { ...x }]));
  const moves = new Map(api.moves.map((mv) => [`${mv.from}>${mv.to}`, { ...mv }]));
  const diff = {};
  let pending = false;
  const newText = change && typeof change.new === "string" ? change.new : null;
  if (newText) {
    const next = parseMachine(newText);
    if (next.edges.length || next.states.length) {
      pending = true;
      const nextKeys = new Set(next.edges.map(([f, t]) => `${f}>${t}`));
      for (const st of next.states) {
        if (!states.has(st)) {
          states.set(st, { id: st, final: next.terminal.includes(st) });
          diff[st] = "added";
        }
      }
      for (const [f, t] of next.edges) {
        const k = `${f}>${t}`;
        if (!moves.has(k)) moves.set(k, { from: f, to: t, change: "added" });
      }
      for (const [k, mv] of moves) {
        if (!nextKeys.has(k) && !mv.change) mv.change = "removed";
      }
      for (const st of [...states.keys()]) {
        if (!next.states.includes(st) && !diff[st]) diff[st] = "removed";
      }
    }
  }
  const nodes = [...states.values()].map((x) => ({
    id: x.id,
    kind: "state",
    label: x.id,
    sub: x.initial ? "start" : x.final ? "final" : "",
  }));
  const edges = [...moves.values()].map((mv) => {
    const rules = (mv.rules || []).slice(0, 2).join(", ") + ((mv.rules || []).length > 2 ? "…" : "");
    return {
      id: `${mv.from}>${mv.to}`,
      source: mv.from,
      target: mv.to,
      kind: mv.change || "moves",
      label: mv.change === "added" ? "added" : mv.change === "removed" ? "removed" : rules || undefined,
      state: mv.change === "removed" ? "dashed" : undefined,
    };
  });
  return {
    nodes, edges, diff, pending,
    initial: [...states.values()].filter((x) => x.initial).map((x) => x.id),
    final: [...states.values()].filter((x) => x.final).map((x) => x.id),
  };
}

/** "queued -> running, cancelled; running -> succeeded (initial: queued)" -> {states, edges, initial} */
export function parseMachine(sentence) {
  const text = String(sentence || "");
  const init = /\(initial:\s*([^)]+)\)/.exec(text);
  const body = text.replace(/\(initial:[^)]*\)/, "");
  const edges = [];
  const states = [];
  const add = (x) => {
    if (x && !states.includes(x)) states.push(x);
  };
  for (const part of body.split(";")) {
    const m = /^\s*([^-\s][^-]*?)\s*->\s*(.+)$/.exec(part.trim());
    if (!m) continue;
    const from = m[1].trim();
    add(from);
    for (const to of m[2].split(",").map((x) => x.trim()).filter(Boolean)) {
      add(to);
      edges.push([from, to]);
    }
  }
  const initial = init ? init[1].trim() : states[0];
  if (initial) add(initial);
  const terminal = states.filter((st) => !edges.some(([f]) => f === st));
  return { states, edges, initial, terminal };
}

function diagram(m, name) {
  // Layers by BFS from the initial state.
  const layer = new Map([[m.initial, 0]]);
  const queue = [m.initial];
  while (queue.length) {
    const cur = queue.shift();
    for (const [f, t] of m.edges) {
      if (f === cur && !layer.has(t)) {
        layer.set(t, layer.get(cur) + 1);
        queue.push(t);
      }
    }
  }
  for (const st of m.states) if (!layer.has(st)) layer.set(st, 0);
  const cols = [];
  for (const st of m.states) {
    const l = layer.get(st);
    (cols[l] ||= []).push(st);
  }
  const W = 120, H = 40, GX = 110, GY = 36, PAD = 24;
  const pos = new Map();
  const maxRows = Math.max(...cols.map((c) => c?.length || 0));
  const totalH = maxRows * H + (maxRows - 1) * GY;
  cols.forEach((col, ci) => {
    const colH = col.length * H + (col.length - 1) * GY;
    col.forEach((st, ri) => {
      pos.set(st, { x: PAD + ci * (W + GX), y: PAD + (totalH - colH) / 2 + ri * (H + GY) });
    });
  });
  const width = PAD * 2 + cols.length * W + (cols.length - 1) * GX;
  const height = PAD * 2 + totalH;
  const svg = s("svg", {
    class: "sm-svg",
    viewBox: `0 0 ${width} ${height}`,
    width: String(width),
    height: String(height),
    role: "img",
    "aria-label": `${name}: ${m.edges.map(([f, t]) => `${f} to ${t}`).join(", ")}. Initial state ${m.initial}.`,
  });
  const markerId = `arrow-${name.replace(/[^A-Za-z0-9]/g, "-")}`;
  svg.append(s("defs", {}, s("marker", { id: markerId, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
    s("path", { d: "M0 0 L10 5 L0 10 z", class: "sm-arrowhead" }))));
  for (const [f, t] of m.edges) {
    const a = pos.get(f), b = pos.get(t);
    let d;
    if (layer.get(f) === layer.get(t)) {
      // Same column: an arc on the right side.
      const x = a.x + W, y1 = a.y + H / 2, y2 = b.y + H / 2;
      d = `M ${x} ${y1} C ${x + 50} ${y1}, ${x + 50} ${y2}, ${x + 2} ${y2}`;
    } else if (layer.get(t) > layer.get(f)) {
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 2, y2 = b.y + H / 2;
      const mx = (x1 + x2) / 2;
      d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
    } else {
      const x1 = a.x, y1 = a.y + H / 2, x2 = b.x + W + 2, y2 = b.y + H / 2;
      d = `M ${x1} ${y1} C ${x1 - 40} ${y1 + 60}, ${x2 + 40} ${y2 + 60}, ${x2} ${y2}`;
    }
    svg.append(s("path", { d, class: "sm-edge", "marker-end": `url(#${markerId})` }, s("title", { text: `${f} → ${t}` })));
  }
  for (const st of m.states) {
    const p = pos.get(st);
    const isInit = st === m.initial;
    const isTerm = m.terminal.includes(st);
    const g = s("g", { class: ["sm-node", isInit && "sm-initial", isTerm && "sm-terminal"].filter(Boolean).join(" ") });
    if (isInit) g.append(s("rect", { x: p.x - 4, y: p.y - 4, width: W + 8, height: H + 8, rx: "12", class: "sm-outer" }));
    g.append(s("rect", { x: p.x, y: p.y, width: W, height: H, rx: "9" }));
    g.append(s("text", { x: p.x + W / 2, y: p.y + H / 2 + 5, "text-anchor": "middle", text: st }));
    svg.append(g);
  }
  return svg;
}

function transitionsTable(m, name) {
  return h("details", { class: "transitions" },
    h("summary", {}, `Transitions table (${m.edges.length})`),
    h("table", { class: "plain" },
      h("caption", { class: "sr-only" }, `${name} transitions`),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "From"), h("th", { scope: "col" }, "To"))),
      h("tbody", {}, m.edges.map(([f, t]) => h("tr", {}, h("td", {}, f + (f === m.initial ? " (initial)" : "")), h("td", {}, t + (m.terminal.includes(t) ? " (final)" : ""))))),
    ),
    h("p", { class: "muted small" }, `Final states: ${m.terminal.join(", ") || "none"}. Nothing leaves a final state.`),
  );
}
