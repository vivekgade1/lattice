// Badges with fixed meaning. Colour is never the only signal: each has an icon and a
// text label. An unchecked rule never shares a colour, icon, outline or word with a
// checked one.

import { h, icon } from "./util.js";

// Rule status (contract: checked|unchecked|context|untraced|probe_gap) -> trust treatment.
export const RULE_STATUS = {
  checked: { label: "Checked", icon: "shield-check", cls: "st-checked", help: "A scenario, invariant, state machine, service or table profile checks this rule." },
  probe_gap: { label: "Probe gap", icon: "triangle", cls: "st-probe-gap", help: "Linked checks exist, but a planted bug in the app got past them. The patch shows what slipped through." },
  untraced: { label: "Untraced", icon: "question", cls: "st-untraced", help: "No check is linked to this rule yet. It needs a scenario or a reason it cannot be checked." },
  unchecked: { label: "Not checked", icon: "text-lines", cls: "st-unchecked", help: "Not checked by Lattice: the spec cannot express this rule, so nothing tests it." },
  context: { label: "Context", icon: "info", cls: "st-context", help: "Background text, not a rule to check." },
};

export function ruleStatusChip(status, opts = {}) {
  const st = RULE_STATUS[status] || RULE_STATUS.untraced;
  return h(
    opts.onclick ? "button" : "span",
    {
      type: opts.onclick ? "button" : null,
      class: ["badge", "badge-rule", st.cls, opts.className],
      title: st.help,
      "aria-haspopup": opts.onclick ? "dialog" : null,
      "aria-label": opts.onclick ? `${st.label}${opts.rule ? ` (${opts.rule})` : ""}: show what checks it` : null,
      onclick: opts.onclick || null,
    },
    icon(st.icon),
    h("span", {}, st.label),
  );
}

// App check results on an item (app.last_check.targets).
export function resultBadge(result) {
  if (result === "pass") {
    return h("span", { class: "badge badge-result res-pass", title: "Passed on the app in the last check" }, icon("dot"), h("span", {}, "Correct"));
  }
  if (result === "fail") {
    return h("span", { class: "badge badge-result res-fail", title: "Failed on the app in the last check" }, icon("diamond"), h("span", {}, "Fails on app"));
  }
  return h("span", { class: "badge badge-result res-none", title: "Not run in the last check" }, icon("dash"), h("span", {}, "Not run"));
}

export function pendingBadge(text = "Pending") {
  return h("span", { class: "badge badge-pending" }, icon("pending"), h("span", {}, text));
}

export function addedBadge() {
  return h("span", { class: "badge badge-added" }, icon("plus"), h("span", {}, "Added"));
}

export function removedBadge() {
  return h("span", { class: "badge badge-removed" }, icon("minus"), h("span", {}, "Removed"));
}

export function modifiedBadge() {
  return h("span", { class: "badge badge-pending" }, icon("pending"), h("span", {}, "Changed"));
}

// HTTP status classes for access-matrix cells. "·" means the spec states nothing.
export function statusClass(value) {
  const v = String(value ?? "").trim();
  if (!v || v === "·") return "unstated";
  const codes = v.split("/").map((c) => c.trim());
  const first = codes[0];
  if (/^2/.test(first)) return "s2xx";
  if (/^5/.test(first)) return "s5xx";
  if (/^4/.test(first)) return "s4xx";
  return "unstated";
}

export const STATUS_CLASS_LABEL = {
  s2xx: { label: "Allowed (2xx)", glyph: "check" },
  s4xx: { label: "Refused (4xx)", glyph: "cross" },
  s5xx: { label: "Server error (5xx)", glyph: "triangle" },
  unstated: { label: "Not stated", glyph: "dash" },
};

// Answer kinds from POST /api/ask.
export const ANSWER_KIND = {
  yes: { label: "Yes", cls: "ans-yes", icon: "check" },
  no: { label: "No", cls: "ans-no", icon: "cross" },
  value: { label: "Value", cls: "ans-value", icon: "info" },
  not_specified: { label: "Not specified", cls: "ans-ns", icon: "text-lines" },
};

export function answerBadge(kind) {
  const k = ANSWER_KIND[kind] || ANSWER_KIND.value;
  return h("span", { class: ["badge", "badge-answer", k.cls] }, icon(k.icon), h("span", {}, k.label));
}

// Change set status (contract: drafting|checking|ready|failed|accepted|rejected|cancelled),
// for the compact cards in the chat panel's "Open changes" / "History" lists.
export const CHANGE_STATUS = {
  drafting: { label: "Drafting", cls: "cs-drafting", icon: "pending" },
  checking: { label: "Checking", cls: "cs-checking", icon: "pending" },
  ready: { label: "Ready for review", cls: "cs-ready", icon: "pending" },
  accepted: { label: "Accepted", cls: "cs-accepted", icon: "check" },
  rejected: { label: "Rejected", cls: "cs-rejected", icon: "cross" },
  cancelled: { label: "Cancelled", cls: "cs-cancelled", icon: "dash" },
  failed: { label: "Failed", cls: "cs-failed", icon: "triangle" },
};

export function changeStatusBadge(status) {
  const s = CHANGE_STATUS[status] || CHANGE_STATUS.drafting;
  return h("span", { class: ["badge", s.cls] }, icon(s.icon), h("span", {}, s.label));
}

// Impact failure labels on a change set.
export const IMPACT_LABEL = {
  not_built_yet: { label: "Not built yet", cls: "imp-nby", help: "The app does not do this yet. Expected until the code is changed." },
  regression: { label: "Regression", cls: "imp-reg", help: "This passed before the change and fails now." },
  contradiction: { label: "Contradiction", cls: "imp-con", help: "The spec now asks for two different things here." },
};
