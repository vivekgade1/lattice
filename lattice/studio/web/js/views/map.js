// Map (home): the whole app on one canvas. Tables as nodes, endpoints grouped by resource,
// background services and the run lifecycle. Each node is tinted by its worst trust state
// (failing > probe gap > gaps > checked) and carries small badges: rules, gaps, failing
// checks. Click selects (global); double-click or Enter opens the element's own view.
// A list alternative (same nodes, a table) serves keyboard and screen-reader users.

import { h, icon, add, plural } from "../util.js";
import { state, update } from "../store.js";
import { targetAttrs, toggleSelection, highlight } from "../targets.js";

let mode = "canvas"; // "canvas" | "list"

const TRUST = {
  bad: { label: "Failing", icon: "diamond" },
  warn: { label: "Probe gap", icon: "triangle" },
  gap: { label: "Has gaps", icon: "question" },
  ok: { label: "Checked", icon: "shield-check" },
  none: { label: "No rules", icon: "dash" },
};
const RANK = { none: 0, ok: 1, gap: 2, warn: 3, bad: 4 };
const worst = (a, b) => (RANK[a] >= RANK[b] ? a : b);
const GAP_STATUS = ["untraced", "probe_gap", "unchecked"];

const snake = (name) => name.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const VERBS = new Set(["activate", "deactivate", "cancel", "run-once", "schedule-tick", "login"]);

/** The resource an endpoint belongs to: its last noun segment, after dropping action verbs. */
function resourceOf(endpoint) {
  const path = endpoint.split(" ").slice(1).join(" ");
  const segs = path.split("/").filter((s) => s && s !== "{}");
  while (segs.length > 1 && VERBS.has(segs[segs.length - 1])) segs.pop();
  return segs[segs.length - 1] || "root";
}

function endpointRegex(endpoint) {
  const [method, ...rest] = endpoint.split(" ");
  const parts = rest.join(" ").split("{}").map(escRe);
  return new RegExp(`${escRe(method)} ${parts.join("[^/\\s]+")}(?![\\w/])`);
}

function buildMap(model) {
  const rules = model.rules || [];
  const findings = model.app?.last_check?.findings || [];
  const openQ = (model.views?.questions || []).filter((q) => !q.answer);
  const sec6 = model.views?.section6 || {};

  // Assess one element from the rules that link to it and the failing findings that touch it.
  function assess(target, ruleList, hints = [], regex = null) {
    const links = new Set(ruleList.flatMap((r) => r.links || []));
    const ids = new Set(ruleList.map((r) => r.id));
    const failing = findings.filter(
      (f) =>
        f.target === target ||
        links.has(f.target) ||
        (f.message && (hints.some((x) => x && new RegExp(`(?<![/\\w])${escRe(x)}(?![/\\w])`).test(f.message)) || (regex && regex.test(f.message)))),
    ).length;
    const gaps =
      ruleList.filter((r) => GAP_STATUS.includes(r.status)).length +
      openQ.filter((q) => (q.rules || []).some((id) => ids.has(id))).length;
    let trust = "none";
    if (failing) trust = "bad";
    else if (ruleList.some((r) => r.status === "probe_gap")) trust = "warn";
    else if (gaps) trust = "gap";
    else if (ruleList.some((r) => r.status === "checked")) trust = "ok";
    return { rules: ruleList.length, ruleIds: ruleList.map((r) => r.id), gaps, failing, trust };
  }
  const rulesWhere = (pred) => rules.filter((r) => (r.links || []).some(pred));

  const tables = (model.data_model?.tables || []).map((t) => {
    const target = `profile:${t.name}`;
    const own = rulesWhere((l) => l === target || l.startsWith(`${target}.`) || l.startsWith(`machine:${t.name}.`));
    const known = new Map((model.data_model.tables || []).map((x) => [snake(x.name), x.name]));
    const refs = new Set();
    for (const c of t.columns || []) {
      if (!/_id$/.test(c.name) && !["created_by", "triggered_by"].includes(c.name)) continue;
      const base = c.name.replace(/_id$/, "");
      const hit = known.get(base) || (/_by$/.test(base) ? known.get("user") : null);
      if (hit && hit !== t.name) refs.add(hit);
    }
    return {
      kind: "Table", target, name: t.name, view: "data",
      sub: `${t.sql} · ${plural((t.columns || []).length, "column")}${refs.size ? ` · refers to ${[...refs].join(", ")}` : ""}`,
      ...assess(target, own, [t.sql]),
    };
  });

  const groups = [];
  for (const g of model.views?.access_groups || []) {
    const byRes = new Map();
    for (const ep of g.columns || []) {
      const res = resourceOf(ep);
      if (!byRes.has(res)) byRes.set(res, []);
      const cells = (g.rows || []).map((row) => g.cells?.[`${row}|${ep}`]).filter((v) => v && v !== "·");
      const target = `endpoint:${ep}`;
      const failing = findings.filter((f) => f.message && endpointRegex(ep).test(f.message)).length;
      const trust = failing ? "bad" : cells.length ? "ok" : "gap";
      byRes.get(res).push({
        kind: "Endpoint", target, name: ep, view: "access", sub: `${plural(cells.length, "caller")} stated`,
        rules: 0, gaps: cells.length ? 0 : 1, failing, trust,
      });
    }
    for (const [res, eps] of byRes) {
      groups.push({ name: res, endpoints: eps, trust: eps.reduce((t, e) => worst(t, e.trust), "none") });
    }
  }
  // merge groups of the same resource that came from different path prefixes
  const merged = new Map();
  for (const g of groups) {
    const m = merged.get(g.name);
    if (!m) merged.set(g.name, g);
    else {
      m.endpoints.push(...g.endpoints);
      m.trust = worst(m.trust, g.trust);
    }
  }

  const services = (sec6.services || []).map(([target, name, text]) => ({
    kind: "Service", target, name, view: "services", sub: text, ...assess(target, rulesWhere((l) => l === target)),
  }));
  const machines = (sec6.machines || []).map(([target, name, sentence]) => ({
    kind: "Lifecycle", target, name, view: "machines", sub: sentence, sentence,
    ...assess(target, rulesWhere((l) => l === target), [snake(name.split(".")[0]) + "s"]),
  }));

  const eps = [...merged.values()].flatMap((g) => g.endpoints);
  return { tables, groups: [...merged.values()], services, machines, all: [...tables, ...eps, ...services, ...machines] };
}

function select(target) {
  if (!state.selection.includes(target)) toggleSelection(target);
}

function openNode(n) {
  highlight([n.target]);
}

function openRules(n) {
  select(n.target);
  update({ view: "rules", rulesScope: { name: n.name, ids: n.ruleIds || [] }, marked: [], focusTarget: null });
}

function trustMark(trust) {
  const t = TRUST[trust];
  return h("span", { class: ["s2-trust", `trust-${trust}`] }, icon(t.icon), h("span", {}, t.label));
}

function badges(n) {
  const out = [];
  if (n.rules) out.push(h("button", {
    type: "button", class: "map-badge", title: `Show the rules about ${n.name}`,
    onclick: (e) => { e.stopPropagation(); openRules(n); },
  }, plural(n.rules, "rule")));
  if (n.gaps) out.push(h("span", { class: "map-badge" }, icon("question"), plural(n.gaps, "gap")));
  if (n.failing) out.push(h("span", { class: "map-badge is-fail" }, icon("diamond"), plural(n.failing, "failing check")));
  return out;
}

const summary = (n) =>
  `${n.kind} ${n.name}: ${TRUST[n.trust].label}. ${plural(n.rules, "rule")}, ${plural(n.gaps, "gap")}, ${plural(n.failing, "failing check")}. Enter opens its view.`;

function wire(el, n) {
  el.addEventListener("dblclick", () => openNode(n));
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target === el) {
      e.preventDefault();
      e.stopPropagation(); // Enter opens; Space still selects (the shell handles it)
      openNode(n);
    }
  });
  return el;
}

function nodeCard(n) {
  return wire(h("div", {
    ...targetAttrs(n.target), role: "button", "aria-label": summary(n),
    class: ["map-node", `trust-${n.trust}`],
  },
    h("div", { class: "map-node-top" }, h("span", { class: "map-node-name" }, n.name), trustMark(n.trust)),
    h("p", { class: "map-node-sub" }, n.sub),
    h("div", { class: "map-badges" }, badges(n)),
    n.sentence ? lifecycle(n.sentence) : null,
  ), n);
}

/** "queued -> running, cancelled; running -> succeeded" as state pills plus its moves. */
function lifecycle(sentence) {
  const body = sentence.replace(/\(initial:[^)]*\)/, "");
  const initial = (sentence.match(/initial:\s*([\w-]+)/) || [])[1];
  const moves = body.split(";").map((s) => s.trim()).filter(Boolean).map((m) => {
    const [from, to] = m.split("->").map((x) => x.trim());
    return [from, (to || "").split(",").map((x) => x.trim()).filter(Boolean)];
  });
  const states = [];
  for (const [from, to] of moves) for (const s of [from, ...to]) if (s && !states.includes(s)) states.push(s);
  return h("div", {},
    h("div", { class: "map-states", "aria-label": "States" },
      states.map((s) => h("span", { class: "map-state" }, s === initial ? `${s} (start)` : s))),
    h("ul", { class: "map-moves" }, moves.map(([from, to]) => h("li", {}, `${from} → ${to.join(", ") || "end"}`))),
  );
}

function groupCard(g) {
  const failing = g.endpoints.reduce((a, e) => a + e.failing, 0);
  return h("section", { class: ["map-group", `trust-${g.trust}`], "aria-label": `${g.name} endpoints` },
    h("div", { class: "map-group-head" },
      h("h3", {}, g.name),
      h("span", { class: "map-badges" },
        h("span", { class: "map-badge" }, plural(g.endpoints.length, "endpoint")),
        failing ? h("span", { class: "map-badge is-fail" }, icon("diamond"), plural(failing, "failing check")) : null,
      ),
    ),
    h("ul", { class: "map-eps" }, g.endpoints.map((e) => {
      const [method, ...rest] = e.name.split(" ");
      return h("li", {}, wire(h("div", {
        ...targetAttrs(e.target), role: "button", "aria-label": summary(e), class: ["map-ep", `trust-${e.trust}`],
      },
        h("span", { class: ["method", `m-${method.toLowerCase()}`] }, method),
        h("code", { class: "path", title: e.name }, rest.join(" ")),
        trustMark(e.trust),
      ), e));
    })),
  );
}

function section(title, ...kids) {
  return h("section", { class: "map-section" }, h("h2", { class: "s2-section-title" }, title), ...kids);
}

function listView(map) {
  return h("div", { class: "map-list scroll-x" }, h("table", {},
    h("caption", { class: "s2-sr" }, "Every element of the app with its trust state"),
    h("thead", {}, h("tr", {}, ["Element", "Kind", "Trust", "Rules", "Gaps", "Failing", ""].map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", {}, map.all.map((n) => h("tr", { ...targetAttrs(n.target), "data-kind": n.kind },
      h("th", { scope: "row" }, h("span", { class: "s2-mono" }, n.name)),
      h("td", {}, n.kind),
      h("td", {}, trustMark(n.trust)),
      h("td", { class: "num" }, String(n.rules)),
      h("td", { class: "num" }, String(n.gaps)),
      h("td", { class: "num" }, String(n.failing)),
      h("td", {}, h("button", { type: "button", class: "btn btn-quiet", onclick: () => openNode(n) }, "Open")),
    ))),
  ));
}

export function render(model, root, ctx) {
  const map = buildMap(model);
  if (!map.all.length) {
    const file = h("input", {
      type: "file", accept: ".md,.txt,text/markdown,text/plain", class: "s2-sr", id: "map-req-file",
      onchange: async (e) => {
        const f = e.target.files?.[0];
        if (!f) return;
        const text = await f.text();
        (ctx?.prefillChange || (() => {}))(`Draft a spec from these requirements:\n\n${text}`, []);
      },
    });
    add(root, h("div", { class: "empty-state" },
      h("h1", {}, "Map"),
      h("p", {}, "There is no spec to draw yet."),
      h("p", { class: "row-actions" },
        h("button", {
          type: "button", class: "btn btn-primary",
          onclick: () => (ctx?.prefillChange || (() => {}))("Draft a spec from my requirements. ", []),
        }, icon("sparkle"), "Draft a spec from your requirements"),
        h("label", { class: "btn", for: "map-req-file" }, "Choose a requirements file"),
        file,
      ),
    ));
    return;
  }
  const failing = (model.app?.last_check?.findings || []).length;
  const toggle = h("div", { class: "seg", role: "group", "aria-label": "Map layout" },
    [["canvas", "Canvas"], ["list", "List"]].map(([id, label]) =>
      h("button", {
        type: "button", class: "seg-btn", "aria-pressed": mode === id ? "true" : "false",
        onclick: () => { mode = id; draw(); },
      }, label)));
  const body = h("div");
  add(root,
    h("header", { class: "view-head map-head" },
      h("div", {},
        h("h1", {}, "Map"),
        h("p", { class: "lede" }, `The whole app: ${plural(map.tables.length, "table")}, ${plural(map.all.length - map.tables.length - map.services.length - map.machines.length, "endpoint")}, ${plural(map.services.length, "background service")}. Click selects, double-click or Enter opens.${failing ? ` ${plural(failing, "check")} failing in the last run.` : ""}`),
      ),
      toggle,
    ),
    body,
  );
  function draw() {
    for (const b of toggle.children) b.setAttribute("aria-pressed", String((b.textContent === "Canvas") === (mode === "canvas")));
    body.replaceChildren();
    if (mode === "list") {
      add(body, listView(map));
    } else {
      add(body,
        map.tables.length ? section("Data", h("div", { class: "map-tables" }, map.tables.map(nodeCard))) : null,
        map.groups.length ? section("Endpoints by resource", h("div", { class: "map-groups" }, map.groups.map(groupCard))) : null,
        map.services.length || map.machines.length
          ? section("Background and lifecycle", h("div", { class: "map-bg" }, [...map.services, ...map.machines].map(nodeCard)))
          : null,
      );
    }
    for (const t of state.selection) {
      for (const el of body.querySelectorAll("[data-target]")) if (el.dataset.target === t) el.classList.add("is-selected");
    }
  }
  draw();
}
