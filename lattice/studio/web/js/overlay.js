// What the views draw on top of the spec: the open change set's diff (amber, pending)
// and the app's last check results (pass/fail per target).

import { state } from "./store.js";
import { nameOf } from "./targets.js";
import { h } from "./util.js";

export function openChange() {
  const c = state.change;
  return c && c.status === "ready" ? c : null;
}

/** {old, new} for an access cell under the open change, or null. */
export function cellDiff(row, col) {
  const c = openChange();
  if (!c) return null;
  return (c.view_diff?.cells || []).find((d) => d.row === row && d.col === col) || null;
}

function targetsIn(list) {
  return (list || []).map((x) => normItem(x).target).filter(Boolean);
}

/** "added" | "removed" | "modified" | null for a scenario/invariant/other target. */
export function itemDiff(target) {
  const c = openChange();
  if (!c) return null;
  const vd = c.view_diff || {};
  const kind = target.split(":")[0];
  const groups = [];
  if (kind === "scenario") groups.push(vd.scenarios || {});
  if (kind === "invariant") groups.push(vd.invariants || {});
  groups.push(c.targets || {});
  for (const g of groups) {
    if (targetsIn(g.added).includes(target)) return "added";
    if (targetsIn(g.removed).includes(target)) return "removed";
    if (targetsIn(g.modified).includes(target)) return "modified";
  }
  if (kind === "machine") {
    const m = (vd.machines?.changed || []).map(normItem).find((x) => x.target === target);
    if (m) return "modified";
  }
  return null;
}

/** Items the open change adds for a kind ("scenario" | "invariant"), normalised. */
export function addedItems(kind) {
  const c = openChange();
  if (!c) return [];
  const vd = c.view_diff || {};
  const group = kind === "scenario" ? vd.scenarios : kind === "invariant" ? vd.invariants : null;
  const fromView = (group?.added || []).map(normItem);
  const fromTargets = (c.targets?.added || []).map(normItem).filter((x) => x.target.startsWith(kind + ":"));
  const seen = new Set();
  return [...fromView, ...fromTargets].filter((x) => (seen.has(x.target) ? false : seen.add(x.target)));
}

export function machineChange(target) {
  const c = openChange();
  if (!c) return null;
  return (c.view_diff?.machines?.changed || []).map(normItem).find((x) => x.target === target) || null;
}

/** "added" | "changed" | "removed" | null for a rule id under the open change. */
export function ruleDiff(id) {
  const c = openChange();
  if (!c) return null;
  const r = c.rules || {};
  if ((r.added || []).includes(id)) return "added";
  if ((r.removed || []).includes(id)) return "removed";
  if ((r.changed || []).some((x) => (typeof x === "string" ? x === id : x.old === id || x.new === id))) return "changed";
  return null;
}

/**
 * The contract leaves the element shape of view_diff lists open. Accept a target string,
 * a [target, name, text] tuple (section6 style) or an object, and return
 * {target, name, text, steps, old, new}.
 */
export function normItem(x) {
  if (typeof x === "string") return { target: x, name: nameOf(x) };
  if (Array.isArray(x)) return { target: x[0], name: x[1] ?? nameOf(x[0]), text: x[2] };
  if (x && typeof x === "object") {
    const target = x.target || (x.name ? `${x.kind || "scenario"}:${x.name}` : "");
    return {
      target,
      name: x.name || nameOf(target),
      text: x.sentence || x.text || x.description || null,
      steps: x.steps || null,
      old: x.old ?? null,
      new: x.new ?? null,
    };
  }
  return { target: "", name: "" };
}

/** The app's last result for a target when the overlay is on, else undefined. */
export function appResult(target) {
  if (!state.overlay) return undefined;
  const t = state.model?.app?.last_check?.targets;
  if (!t) return undefined;
  return t[target] ?? null;
}

export function hasAppTargets(model) {
  const t = model?.app?.last_check?.targets;
  return !!(t && Object.keys(t).length);
}

/** Reverse index: target -> rule ids that link to it. */
export function rulesByTarget(model) {
  if (model.__rulesByTarget) return model.__rulesByTarget;
  const idx = new Map();
  for (const r of model.rules || []) {
    for (const t of r.links || []) {
      if (!idx.has(t)) idx.set(t, []);
      idx.get(t).push(r.id);
    }
  }
  Object.defineProperty(model, "__rulesByTarget", { value: idx, enumerable: false });
  return idx;
}

/** A banner shown at the top of each view while a change set is open. */
export function changeBanner() {
  const c = openChange();
  if (!c) return null;
  return h(
    "div",
    { class: "change-banner", role: "status" },
    h("strong", {}, "Showing a pending change: "),
    h("span", {}, `“${c.request}”. `),
    h("span", { class: "muted" }, "Amber marks what it would change. Accept or reject it in the chat panel."),
  );
}
