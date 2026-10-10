// Request flow: one request traced end to end, one node per stage (route, middleware,
// handler, database writes, response) drawn with React Flow from the flow's stage ids and
// `next`, in rows that wrap to the canvas width so every label reads at full size. Each
// stage carries a line of facts: the rules it applies and the tables
// it reads and writes. A failing divergence overlays the path in red and says "expected X,
// got Y at <stage>". Select a stage to ask about it or open its code. A table below the
// diagram carries the same facts for screen readers; the original columns are the fallback
// when the graph cannot load.

import { h, icon, add, plural } from "../util.js";
import { api } from "../api.js";
import { state, update } from "../store.js";
import { targetAttrs } from "../targets.js";
import { mountGraph } from "../graph.js";

let endpoint = null;
let caller = null;

const sentence = (s) => String(s || "").replace(/^./, (c) => c.toUpperCase());
const stageTarget = (s) => `code:${s.path}:${s.line}`;
const clean = (t) => String(t || "").replace(/[*`]/g, "");

function endpointsOf(model) {
  const seen = new Set();
  for (const g of model.views?.access_groups || []) for (const c of g.columns || []) seen.add(c);
  return [...seen];
}

function callersOf(model) {
  const seen = new Set();
  for (const g of model.views?.access_groups || []) for (const r of g.rows || []) seen.add(r);
  return [...seen];
}

export function render(model, root, ctx) {
  const endpoints = endpointsOf(model);
  const callers = callersOf(model);
  if (!endpoint || !endpoints.includes(endpoint)) {
    endpoint = endpoints.find((e) => e === "POST /applications/{}/runs/{}/cancel") || endpoints[0] || null;
  }
  if (!caller || !callers.includes(caller)) caller = callers.includes("ann") ? "ann" : callers[0] || "ann";

  add(root, h("header", { class: "view-head" },
    h("h1", {}, "Request flow"),
    h("p", { class: "lede" }, "Follow one request from the route to the response: what each stage applies and what it touches."),
  ));
  if (!endpoints.length) {
    add(root, h("p", { class: "s2-empty" }, "The spec states no endpoints yet, so there is no request to trace."));
    return;
  }
  const out = h("div", { "aria-live": "polite" });
  const controls = h("div", { class: "flow-controls" },
    h("label", {}, "Endpoint",
      h("select", { onchange: (e) => { endpoint = e.target.value; load(); } },
        endpoints.map((ep) => h("option", { value: ep, selected: ep === endpoint }, ep)))),
    h("label", {}, "Caller",
      h("select", { onchange: (e) => { caller = e.target.value; load(); } },
        callers.map((c) => h("option", { value: c, selected: c === caller }, c)))),
  );
  add(root, controls, out);

  let token = 0;
  async function load() {
    const mine = ++token;
    out.replaceChildren(h("p", { class: "muted", "aria-busy": "true" }, "Tracing the request…"));
    try {
      const flow = await api.flow(endpoint, caller);
      if (mine === token) drawFlow(out, model, flow, ctx);
    } catch (e) {
      if (mine !== token) return;
      out.replaceChildren(h("p", { class: "s2-empty", role: "status" },
        e.unsupported
          ? e.message
          : /^404/.test(e.message)
            ? `No flow is traced for ${endpoint} yet. Pick another endpoint, such as POST /applications/{}/runs/{}/cancel.`
            : e.message));
    }
  }
  load();
}

function drawFlow(out, model, flow, ctx) {
  const rules = new Map((model.rules || []).map((r) => [r.id, r]));
  const div = flow.divergence || null;
  const stages = flow.stages || [];
  const divIdx = div ? stages.findIndex((s) => s.stage === div.stage) : -1;
  let selected = divIdx >= 0 ? divIdx : 0;
  const detail = h("div", { "aria-live": "polite" });

  const cols = [];
  stages.forEach((s, i) => {
    if (i) cols.push(h("span", { class: ["flow-link", divIdx >= 0 && i <= divIdx && "is-on-path"], "aria-hidden": "true" }));
    const el = h("div", {
      ...targetAttrs(stageTarget(s)), role: "button",
      "aria-label": `${sentence(s.stage)}: ${s.symbol}, ${s.path} line ${s.line}${i === divIdx ? ". Diverges here" : ""}`,
      class: ["flow-col", divIdx >= 0 && i <= divIdx && "is-on-path", i === divIdx && "is-diverged", i === selected && "is-chosen"],
      onclick: () => { selected = i; showDetail(); },
    },
      h("h2", { class: "flow-stage" }, sentence(s.stage)),
      h("p", { class: "flow-sym" }, s.symbol),
      h("p", { class: "flow-loc" }, `${s.path.split("/").pop()}:${s.line}`),
      (s.rules || []).length ? h("p", { class: "flow-meta" }, h("strong", {}, "Rules "), s.rules.join(", ")) : null,
      (s.reads || []).length ? h("p", { class: "flow-meta" }, h("strong", {}, "Reads "), s.reads.join(", ")) : null,
      (s.writes || []).length ? h("p", { class: "flow-meta" }, h("strong", {}, "Writes "), s.writes.join(", ")) : null,
      i === divIdx ? h("p", { class: "flow-here" }, icon("diamond"), "Diverges here") : null,
    );
    cols.push(el);
  });

  // The graph: stages as nodes, `next` as lines, a caller node in front.
  const ids = stages.map((st, i) => st.id || `stage-${i}`);
  const base = (p) => String(p).split("/").pop();
  const facts = (st) => {
    const out = [];
    if ((st.rules || []).length) out.push(plural(st.rules.length, "rule"));
    if ((st.reads || []).length) out.push(`reads ${st.reads.length}`);
    if ((st.writes || []).length) out.push(`writes ${st.writes.length}`);
    return out.join(" · ");
  };
  const W = 208;
  const gNodes = [
    { id: "caller", kind: "caller", label: flow.caller || caller, sub: "calls " + (flow.endpoint || endpoint).split(" ")[0], w: W },
    ...stages.map((st, i) => ({
      id: ids[i],
      kind: "stage",
      label: `${sentence(st.stage)}: ${st.symbol}`,
      sub: i === divIdx ? `expected ${div.expected}, got ${div.actual}` : `${base(st.path)}:${st.line}`,
      trust: i === divIdx ? "failing" : undefined,
      badges: facts(st) && i !== divIdx ? [{ label: facts(st), tone: "neutral" }] : [],
      target: stageTarget(st),
      w: W,
    })),
  ];
  const gEdges = [];
  if (stages.length) gEdges.push({ id: `caller>${ids[0]}`, source: "caller", target: ids[0], kind: "calls", state: divIdx >= 0 ? "failing" : undefined });
  stages.forEach((st, i) => {
    const next = Array.isArray(st.next) ? st.next : i + 1 < stages.length ? [ids[i + 1]] : [];
    for (const n of next) {
      const j = ids.indexOf(n);
      gEdges.push({
        id: `${ids[i]}>${n}`, source: ids[i], target: n, kind: "next",
        state: divIdx >= 0 && j >= 0 && j <= divIdx ? "failing" : undefined,
        label: j === divIdx ? "diverges here" : undefined,
      });
    }
  });
  const canvas = h("div", { class: "gr-canvas is-flow", "data-view": "flow-graph" });
  const lanes = h("div", { class: "flow-lanes", role: "group", "aria-label": `Request flow for ${flow.endpoint} as ${flow.caller}`, tabindex: "0" }, cols);
  const graph = mountGraph(canvas, {
    nodes: gNodes,
    edges: gEdges,
    layout: "wrap",
    selected: [ids[selected]],
    label: `Request flow for ${flow.endpoint} as ${flow.caller}`,
    onSelect: (id) => {
      const i = ids.indexOf(id);
      if (i < 0) return;
      selected = i;
      const t = stageTarget(stages[i]);
      if (!state.selection.includes(t)) update({ selection: [...state.selection, t] });
      showDetail();
    },
    onOpen: (id) => {
      const i = ids.indexOf(id);
      if (i >= 0) ctx.openCode(stageTarget(stages[i]));
    },
    onError: () => canvas.replaceWith(lanes),
  });

  function showDetail() {
    graph.update({ selected: [ids[selected]] });
    for (const el of out.querySelectorAll(".flow-col")) el.classList.toggle("is-chosen", false);
    const s = stages[selected];
    if (!s) { detail.replaceChildren(); return; }
    const target = stageTarget(s);
    const colEls = out.querySelectorAll(".flow-col");
    colEls[selected]?.classList.add("is-chosen");
    detail.replaceChildren(h("section", { class: "flow-detail", "aria-label": `Stage ${s.stage}` },
      h("h2", {}, `${sentence(s.stage)}: ${s.symbol}`),
      h("p", { class: "muted" }, `${s.path}:${s.line}`),
      (s.rules || []).length
        ? h("ul", { class: "legend" }, s.rules.map((id) => h("li", {}, h("strong", {}, id), " ", clean(rules.get(id)?.text).slice(0, 140))))
        : h("p", { class: "muted" }, "No rule is linked to this stage."),
      h("div", { class: "row-actions" },
        h("button", { type: "button", class: "btn", onclick: () => ctx.askAbout(target) }, icon("chat"), "Ask about this stage"),
        h("button", { type: "button", class: "btn", onclick: () => ctx.openCode(target) }, icon("text-lines"), "Open its code"),
      ),
    ));
  }

  const table = h("div", { class: "scroll-x" }, h("table", { class: "flow-table" },
    h("caption", {}, `${flow.endpoint} as ${flow.caller}, stage by stage`),
    h("thead", {}, h("tr", {}, ["Stage", "Symbol", "Location", "Rules", "Reads", "Writes", "Result"].map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", {}, stages.map((s, i) => h("tr", { class: i === divIdx && "is-diverged" },
      h("th", { scope: "row" }, sentence(s.stage)),
      h("td", { class: "s2-mono" }, s.symbol),
      h("td", { class: "s2-mono" }, `${s.path}:${s.line}`),
      h("td", {}, (s.rules || []).join(", ") || "none"),
      h("td", {}, (s.reads || []).join(", ") || "none"),
      h("td", {}, (s.writes || []).join(", ") || "none"),
      h("td", {}, i === divIdx ? "Diverges: see above" : divIdx >= 0 && i < divIdx ? "Reached" : divIdx >= 0 ? "After the divergence" : "As expected"),
    ))),
  ));

  out.replaceChildren(...[
    div ? h("div", { class: "flow-diverge", role: "alert" },
      icon("diamond"),
      h("span", {},
        h("strong", {}, "Diverges: "), "expected ", h("code", {}, div.expected),
        ", got ", h("code", {}, div.actual), ` at ${div.stage}`)) : null,
    canvas,
    detail,
    h("p", { class: "muted small" }, `${plural(stages.length, "stage")}. ${div ? "The last check fails on this path." : "The last check does not contradict this path."}`),
    table,
  ].filter(Boolean));
  showDetail();
}
