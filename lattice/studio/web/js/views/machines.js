// Lifecycle: one SVG diagram per state machine, laid out left to right in layers from
// the initial state, with the transitions table as the accessible alternative.

import { h, s, add } from "../util.js";
import { targetAttrs } from "../targets.js";
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
        h("div", { class: "scroll-x diagram-wrap", tabindex: "0", role: "region", "aria-label": `${name} diagram` }, diagram(m, name)),
        transitionsTable(m, name),
      ),
    );
  }
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
