// Request flow: one request traced end to end, left to right, one column per stage
// (route, middleware, handler, database writes, response). Each stage lists the rules it
// applies and the tables it reads and writes. A failing divergence overlays the path in red
// and says "expected X, got Y at <stage>". Select a stage to ask about it or open its code.
// A table below the diagram carries the same facts for screen readers.

import { h, icon, add, plural } from "../util.js";
import { api } from "../api.js";
import { targetAttrs } from "../targets.js";

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

  function showDetail() {
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
    h("div", { class: "flow-lanes", role: "group", "aria-label": `Request flow for ${flow.endpoint} as ${flow.caller}`, tabindex: "0" }, cols),
    detail,
    h("p", { class: "muted small" }, `${plural(stages.length, "stage")}. ${div ? "The last check fails on this path." : "The last check does not contradict this path."}`),
    table,
  ].filter(Boolean));
  showDetail();
}
