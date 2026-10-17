// App > Impact: what a drafted change touches, in tiers, from Spike D's change-impact report.
//   GET /api/impact?change=cN  lattice.studio-impact/1: D's report built in the server (tiers 2 and 3,
//                              no app run), the change's own check, and a saved tier 1 report only
//                              when its input hashes match.
// The binding rule: a workflow failure is observed or logically determined, never guessed. Tier 1
// (observed) and tier 2 (determined) are the only failures this view states; tier 3 is "may fail",
// never a claim; tier 4 is not run. Every item is shown with its id, kind, target, rules and its text
// fields verbatim from the JSON, in the report's own order. A status is a word, never colour alone.

import { h, icon, add, clear, plural } from "../util.js";
import { api } from "../api.js";

// ------------------------------------------------------------------ the fixed words

export const WORDS = {
  title: "Impact",
  lede: "What a drafted change touches, and what is observed or determined to fail. Lattice does not predict failures: it reports what was run (tier 1), what follows logically (tier 2), and what is only touched (tier 3).",
  decidable: "Which workflows fail is mostly not decidable before the change exists.",
  pick: "Change",
  pickPlaceholder: "Choose a drafted change",
  noChanges: "There is no change yet. Draft one in the chat (change mode); impact is read only for a change that is ready or accepted.",
  tier1: "Tier 1 · observed: a failing check on today's app",
  tier2: "Tier 2 · determined: follows from the spec and steps observed to pass",
  tier3: "Tier 3 · static: may fail (code and workflows it touches; not a claim)",
  tier4: "Tier 4 · observed after the change: not run (implementation step, Spike E)",
  tier4Body: "Not run. The implementation of the change does not exist yet; Spike E observes it after the change is built.",
  tier1NotRun: "Not run here.",
  tier1NotRunHow: "Tier 1 runs the candidate on the app, which starts Docker, so this view never starts it. Run it from a terminal and save the report where [impact] reports points:",
  tier1Shown: "Observed in a saved report whose inputs match this change.",
  tier1Mismatch: "A saved report for this change exists, but its inputs differ from this change, so it is not shown. Differs in:",
  studioCheckHead: "Studio's change check: observed, layers C and D, when the change was drafted",
  studioCheckNone: "No app check ran when this change was drafted.",
  noItems: "No item.",
  notConfigured: "Impact is not switched on for this project.",
  notConfiguredHow: "Add an [impact] table to studio.toml (it also needs [implementation]). The reports key is optional.",
  noChangeSelected: "Choose a drafted change to see what it touches.",
  noState: "Durable state is off, so Studio keeps no change record on disk and impact cannot read one. Remove “enabled = false” from [state] in studio.toml.",
  loading: "Reading the impact…",
  staticNote: "Static: these are places the change touches. Whether anything fails there is not decided.",
};
const READY = ["ready", "accepted"];
const CLI = (id) => `lattice spec impact --project PROJECT_DIR --change ${id} --tiers 123 --json --out REPORTS_DIR/${id}.json`;

// ------------------------------------------------------------------ state

function fresh(base) {
  return { base, change: null, phase: "idle", body: null, error: null };
}

let S = fresh(null);
let mounted = null;

function redraw() {
  if (mounted && mounted.root.isConnected) draw(mounted.root, mounted.model);
}

async function load(id) {
  const base = S.base;
  S.change = id;
  S.phase = "loading";
  S.body = null;
  S.error = null;
  redraw();
  try {
    const body = await api.impact(id);
    if (S.base !== base || S.change !== id) return;
    S.body = body;
    S.phase = "done";
  } catch (e) {
    if (S.base !== base || S.change !== id) return;
    S.error = e.message;
    S.phase = "error";
  }
  redraw();
}

// ------------------------------------------------------------------ the items, verbatim

const SKIP = new Set(["id", "kind", "target", "rules", "tier"]);

function leaf(v) {
  return h("span", { class: "ip-val" }, String(v));
}

/** Every value of `v` as DOM, strings verbatim; nulls are not text and are left out. */
function valueView(v) {
  if (v === null || v === undefined) return null;
  if (Array.isArray(v)) {
    if (!v.length) return null;
    return h("ul", { class: "ip-list" }, v.map((x) => h("li", {}, valueView(x))));
  }
  if (typeof v === "object") return fieldsView(v);
  return leaf(v);
}

function fieldsView(obj, skip = new Set()) {
  const rows = Object.entries(obj).filter(([k, v]) => !skip.has(k) && valueView(v) !== null);
  if (!rows.length) return null;
  return h("dl", { class: "ip-fields" }, rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, valueView(v))]));
}

function itemView(item, tier, open) {
  const rules = Array.isArray(item.rules) ? item.rules : [];
  const body = fieldsView(item, SKIP);
  return h("li", { class: "ip-item", "data-tier": String(tier), "data-id": String(item.id) },
    h("div", { class: "ip-item-head" },
      h("code", { class: "imp-id" }, String(item.id)),
      item.kind ? h("span", { class: "chip" }, String(item.kind)) : null,
      item.target ? h("code", { class: "ip-target" }, String(item.target)) : null,
      rules.length ? h("span", { class: "rule-links", "aria-label": "Rules" }, rules.map((r) => h("code", { class: "chip" }, String(r)))) : null),
    body ? h("details", { open: open ? true : null }, h("summary", {}, "Details"), body) : null);
}

function itemList(items, tier, open, label) {
  if (!items.length) return h("p", { class: "s2-empty" }, WORDS.noItems);
  return h("ol", { class: "ip-items", "aria-label": label }, items.map((i) => itemView(i, tier, open)));
}

// ------------------------------------------------------------------ the tiers

function studioCheck(check) {
  const box = h("aside", { class: "ip-studio-check", "aria-label": WORDS.studioCheckHead, "data-source": "studio_check" },
    h("h3", {}, WORDS.studioCheckHead));
  if (!check) return add(box, h("p", { class: "muted" }, WORDS.studioCheckNone));
  const pass = check.verdict === "pass";
  add(box, h("p", {}, h("span", { class: ["s2-trust", pass ? "trust-ok" : "trust-bad"] },
    icon(pass ? "check" : "diamond"), h("span", {}, pass ? "Pass" : "Fail")), ` ${plural((check.failing || []).length, "failing check")}`));
  if (check.error) add(box, h("p", { class: "imp-err" }, check.error));
  const failing = check.failing || [];
  if (failing.length) {
    add(box, h("ol", { class: "ip-items", "aria-label": "Failing checks" }, failing.map((f, i) => h("li", { class: "ip-item", "data-id": `studio-check-${i}` },
      h("div", { class: "ip-item-head" }, h("code", { class: "ip-target" }, String(f.target)), f.label ? h("span", { class: "chip" }, String(f.label)) : null),
      h("p", { class: "ip-val" }, String(f.message || ""))))));
  }
  return box;
}

function tier1Section(body, id) {
  const saved = body.tier1_saved || { state: "not_configured" };
  const left = [];
  if (saved.state === "shown" && saved.tier1) {
    const t1 = saved.tier1;
    left.push(h("p", { class: "muted", "data-state": "tier1_shown" }, WORDS.tier1Shown));
    left.push(h("h3", {}, "Checks"), itemList(t1.failing || [], 1, true, "Tier 1 checks"));
    left.push(h("h3", {}, "Workflows"), itemList(t1.flows || [], 1, true, "Tier 1 workflows"));
  } else {
    if (saved.state === "mismatch") {
      left.push(h("p", { class: "imp-note", role: "status", "data-state": "tier1_mismatch" },
        `${WORDS.tier1Mismatch} ${(saved.differs || []).join(", ")}.`));
    }
    left.push(h("p", { "data-state": "tier1_not_run" }, h("strong", {}, WORDS.tier1NotRun)));
    left.push(h("p", { class: "muted" }, WORDS.tier1NotRunHow));
    left.push(h("pre", { class: "imp-pre ip-cmd" }, CLI(id)));
  }
  return h("section", { class: "ip-tier ip-tier1", "aria-labelledby": "ip-h1", "data-tier": "1" },
    h("h2", { id: "ip-h1" }, WORDS.tier1),
    h("div", { class: "ip-tier1-grid" }, h("div", { class: "ip-tier1-main" }, left), studioCheck(body.studio_check)));
}

function tier2Section(report) {
  const t2 = report.tier2;
  return h("section", { class: "ip-tier ip-tier2", "aria-labelledby": "ip-h2", "data-tier": "2" },
    h("h2", { id: "ip-h2" }, WORDS.tier2),
    h("h3", {}, "Checks"), itemList(t2.determined || [], 2, true, "Tier 2 checks"),
    h("h3", {}, "Workflows"), itemList(t2.flows || [], 2, true, "Tier 2 workflows"));
}

function tier3Section(report) {
  const t3 = report.tier3;
  const un = t3.unread || [];
  return h("section", { class: "ip-tier ip-tier3", "aria-labelledby": "ip-h3", "data-tier": "3" },
    h("h2", { id: "ip-h3" }, WORDS.tier3),
    h("p", { class: "muted small" }, WORDS.staticNote),
    h("h3", {}, `Code locations (${(t3.locations || []).length})`), itemList(t3.locations || [], 3, false, "Tier 3 code locations"),
    h("h3", {}, `Workflows that may fail (${(t3.flows || []).length})`), itemList(t3.flows || [], 3, false, "Tier 3 workflows"),
    un.length
      ? h("details", { class: "ip-unread" }, h("summary", {}, `Not read (${un.length})`),
          h("ul", { class: "ip-list" }, un.map((u) => h("li", {}, valueView(u))))) : null);
}

function tier4Section() {
  return h("section", { class: "ip-tier ip-tier4", "aria-labelledby": "ip-h4", "data-tier": "4" },
    h("h2", { id: "ip-h4" }, WORDS.tier4), h("p", { class: "muted" }, WORDS.tier4Body));
}

function okView(body) {
  const r = body.report;
  const s = r.summary.tiers;
  const rules = r.diff.rules;
  const saved = body.tier1_saved || {};
  const st = saved.state === "shown" ? "tier1_shown" : saved.state === "mismatch" ? "tier1_mismatch" : "tier1_not_run";
  return {
    state: st,
    nodes: [
      h("p", { class: "ip-summary", role: "status" },
        `Change ${body.change}: ${plural((rules.added || []).length, "rule")} added, ${(rules.changed || []).length} changed, ${(rules.removed || []).length} removed. ` +
        `Tier 2: ${plural((s["2"].determined || 0) + (s["2"].flows || 0), "item")}. Tier 3: ${plural(s["3"].locations, "code location")}, ${plural(s["3"].flows, "workflow")}.`),
      h("p", { class: "imp-note ip-decidable" }, WORDS.decidable),
      tier1Section(body, body.change), tier2Section(r), tier3Section(r), tier4Section(),
    ],
  };
}

// ------------------------------------------------------------------ the view

function stateBox(state, title, ...body) {
  return h("section", { class: "am-state ip-state", "data-state": state, "aria-label": title }, h("h2", {}, title), ...body);
}

function picker(model) {
  const changes = model.changes || [];
  const id = "ip-change";
  return h("div", { class: "imp-row ip-picker" },
    h("label", { for: id }, WORDS.pick),
    h("select", {
      id, class: "imp-input", disabled: changes.length ? null : true,
      onchange: (e) => { if (e.target.value) load(e.target.value); },
    },
      h("option", { value: "", selected: !S.change }, WORDS.pickPlaceholder),
      changes.map((c) => h("option", { value: c.id, selected: S.change === c.id, disabled: !READY.includes(c.status) ? true : null },
        `${c.id} · ${c.status}${c.request ? ` · ${String(c.request).slice(0, 60)}` : ""}`))));
}

function draw(root, model) {
  clear(root);
  let state = "no_change_selected";
  const nodes = [];
  const changes = model.changes || [];
  if (!S.change) {
    nodes.push(stateBox("no_change_selected", WORDS.noChangeSelected, changes.length ? null : h("p", { class: "muted" }, WORDS.noChanges)));
  } else if (S.phase === "loading") {
    state = "loading";
    nodes.push(h("div", { class: "am-loading", role: "status", "aria-live": "polite", "data-state": "loading" }, WORDS.loading));
  } else if (S.phase === "error") {
    state = "error";
    nodes.push(stateBox("error", "The impact could not be read.", h("p", { class: "imp-err", role: "alert" }, S.error)));
  } else if (S.phase === "done") {
    const b = S.body;
    if (b.state === "ok") {
      const v = okView(b);
      state = v.state;
      nodes.push(...v.nodes);
    } else if (b.state === "impact_not_configured") {
      state = "impact_not_configured";
      nodes.push(stateBox("impact_not_configured", WORDS.notConfigured, h("p", { class: "muted" }, WORDS.notConfiguredHow),
        h("pre", { class: "imp-pre am-toml" }, "[impact]\nreports = \"../impact\"   # optional: a directory of lattice spec impact --out files\n")));
    } else if (b.state === "change_not_ready") {
      state = "change_not_ready";
      nodes.push(stateBox("change_not_ready", `Change ${b.change} is not ready.`, h("p", { class: "muted" }, b.why || ""),
        h("p", { class: "muted" }, "Impact is read only for a change that is ready or accepted.")));
    } else if (b.state === "no_state") {
      state = "no_state";
      nodes.push(stateBox("no_state", "Studio keeps no change records.", h("p", { class: "muted" }, WORDS.noState)));
    } else {
      state = "error";
      const why = b.state === "no_change" ? `There is no change ${b.change}.` : b.state === "inventory_missing" ? "Impact needs the implementation inventory ([implementation] in studio.toml)." : "The report could not be built.";
      nodes.push(stateBox("error", why, b.why ? h("p", { class: "imp-err" }, b.why) : null));
    }
  } else {
    nodes.push(stateBox("no_change_selected", WORDS.noChangeSelected));
  }
  root.setAttribute("data-state", state);
  add(root, h("header", { class: "view-head" }, h("h1", {}, WORDS.title), h("p", { class: "lede" }, WORDS.lede)),
    picker(model), ...nodes);
}

export function render(model, root, ctx) {
  if (S.base !== api.base()) S = fresh(api.base());
  mounted = { root, model, ctx };
  draw(root, model);
  if (S.change && S.phase === "idle") load(S.change);
}

/** The state of the view, for tests that drive it without a DOM. */
export function currentChange() {
  return S.change;
}
