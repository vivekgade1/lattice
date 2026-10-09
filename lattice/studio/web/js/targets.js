// Targets are the contract's link strings: scenario:<name>, invariant:<name>,
// machine:<Table.column>, service:<name>, profile:<Table[.column]>, rule:<Rn>,
// cell:<row>|<METHOD /template>. Every element drawn for a target carries a stable DOM
// id derived from the string, so highlight() can find it on any view.

import { h } from "./util.js";
import { state, update } from "./store.js";

/** The stable DOM id for a target: "t-" plus the string with every character outside
 *  [A-Za-z0-9_-] written as _<hex>_. Reversible, so two targets never share an id. */
export function tid(target) {
  return "t-" + String(target).replace(/[^A-Za-z0-9-]/g, (c) => "_" + c.codePointAt(0).toString(16) + "_");
}

export function kindOf(target) {
  const i = String(target).indexOf(":");
  return i < 0 ? "" : target.slice(0, i);
}

export function nameOf(target) {
  const i = String(target).indexOf(":");
  return i < 0 ? target : target.slice(i + 1);
}

export function cellTarget(row, col) {
  return `cell:${row}|${col}`;
}

const VIEW_OF_KIND = {
  rule: "rules",
  cell: "access",
  endpoint: "access",
  profile: "data",
  machine: "machines",
  invariant: "invariants",
  scenario: "scenarios",
  service: "services",
  question: "gaps",
  gap: "gaps",
};

export function viewFor(target) {
  return VIEW_OF_KIND[kindOf(target)] || null;
}

const KIND_LABEL = {
  rule: "Rule",
  cell: "Access",
  endpoint: "Endpoint",
  profile: "Table",
  machine: "State machine",
  invariant: "Invariant",
  scenario: "Scenario",
  service: "Service",
  question: "Question",
};

/** A short readable label: "Access · eve · POST /applications/{}/runs/{}/cancel". */
export function labelOf(target) {
  const kind = kindOf(target);
  const name = nameOf(target);
  if (kind === "cell") {
    const [row, col] = name.split("|");
    return `${row} · ${col}`;
  }
  if (kind === "rule") return name;
  return `${KIND_LABEL[kind] || kind} · ${name}`;
}

/** A clickable chip for a target; click highlights it on the views. */
export function targetChip(target, opts = {}) {
  const kind = kindOf(target);
  const known = !!viewFor(target);
  return h(
    "button",
    {
      type: "button",
      class: ["chip", "chip-target", `chip-kind-${kind || "other"}`, opts.className],
      title: known ? `Show ${target} on the views` : target,
      "data-chip-target": target,
      onclick: (e) => {
        e.stopPropagation();
        if (opts.onclick) opts.onclick(target);
        else highlight([target]);
      },
    },
    h("span", { class: "chip-kind" }, KIND_LABEL[kind] || kind || "link"),
    h("span", { class: "chip-name" }, kind === "cell" ? nameOf(target).replace("|", " · ") : nameOf(target)),
  );
}

/** Switch to the view of the first target, scroll to it and pulse it, and mark all of
 *  them. Targets on other views are marked when the owner visits those views. */
export function highlight(targets) {
  const list = (targets || []).filter(Boolean);
  if (!list.length) return;
  const first = list.find((t) => viewFor(t)) || list[0];
  const view = viewFor(first) || state.view;
  update({ marked: list, focusTarget: first, view });
}

export function clearMarks() {
  update({ marked: [], focusTarget: null });
}

/** Called after a view renders: apply marks and selection, then scroll to and pulse
 *  the focus target once. */
export function applyMarks(root) {
  for (const el of root.querySelectorAll(".is-marked, .is-selected")) {
    el.classList.remove("is-marked", "is-selected");
  }
  for (const t of state.marked) {
    const el = document.getElementById(tid(t));
    if (el) el.classList.add("is-marked");
  }
  for (const t of state.selection) {
    const el = document.getElementById(tid(t));
    if (el) {
      el.classList.add("is-selected");
    }
  }
  if (state.focusTarget) {
    const el = document.getElementById(tid(state.focusTarget));
    state.focusTarget = null;
    if (el) {
      // Open a collapsed <details> holding the element.
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (p.tagName === "DETAILS") p.open = true;
      }
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center", inline: "center" });
      el.classList.remove("pulse");
      void el.offsetWidth; // restart the animation
      el.classList.add("pulse");
      if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
      el.focus({ preventScroll: true });
    }
  }
}

/** Add or remove a target from the selection (shown as chips in the chat panel). */
export function toggleSelection(target) {
  const sel = state.selection.includes(target)
    ? state.selection.filter((t) => t !== target)
    : [...state.selection, target];
  update({ selection: sel });
}

/** Mark an element as addressable and selectable. */
export function targetAttrs(target, extra = {}) {
  return {
    id: tid(target),
    "data-target": target,
    tabindex: "0",
    ...extra,
  };
}
