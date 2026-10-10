// Rules: the owner's REQUIREMENTS.md, rendered as a plain document list. Each rule has a
// status chip (icon + word) that opens what checks it. Hover or focus reveals Ask,
// Change, Edit and Code. Also exports the small helpers the other Intent views share.

import { h, s, icon, add } from "../util.js";
import { parse, inline, slugify } from "../markdown.js";
import { RULE_STATUS, ruleStatusChip, resultBadge, pendingBadge, addedBadge, removedBadge } from "../badges.js";
import { targetAttrs, targetChip, kindOf } from "../targets.js";
import { appResult, ruleDiff, changeBanner, openChange } from "../overlay.js";
import { unifiedDiff } from "../diff.js";
import { state, update } from "../store.js";

// ---- shared helpers (used by access, data, machines, invariants, scenarios, services, gaps)

function pencil() {
  const svg = s("svg", { class: "icon", viewBox: "0 0 16 16", width: "16", height: "16", "aria-hidden": "true", focusable: "false" });
  svg.append(s("path", { d: "M11.4 1.6a1.5 1.5 0 0 1 2.1 0l.9.9a1.5 1.5 0 0 1 0 2.1L5.5 13.5 1.5 14.5l1-4L11.4 1.6Z", fill: "currentColor" }));
  return svg;
}

/**
 * The hover/focus action row for a target: Ask, Change, Edit (pencil, E) and Code, as
 * icon buttons (the accessible name and the tooltip carry the word).
 * Put it as a direct child of an element that also has class "acts-host".
 * o: {edit: false to omit the pencil, code: false to omit Code, label: the name used in
 * accessible labels}.
 */
export function actions(ctx, target, o = {}) {
  const name = o.label || target;
  const btn = (cls, label, key, fn, ico) =>
    h("button", {
      type: "button",
      class: ["act", cls],
      "aria-label": `${label} ${name}`,
      "aria-keyshortcuts": key || null,
      title: key ? `${label} (${key})` : label,
      onclick: (e) => {
        e.stopPropagation();
        fn(target);
      },
    }, ico);
  return h("span", { class: "acts", role: "group", "aria-label": `Actions for ${name}` },
    btn("act-ask", "Ask about", "A", (t) => ctx.askAbout(t), icon("chat")),
    btn("act-change", "Change", "C", (t) => ctx.changeAbout(t), icon("sparkle")),
    o.edit === false ? null : btn("act-edit", "Edit", "E", (t) => ctx.startEdit(t), pencil()),
    o.code === false ? null : btn("act-code", "Show code for", "", (t) => ctx.openCode(t), icon("text-lines")),
  );
}

/** "is-impact" when the open change does not edit the target but affects its checks. */
export function impactClass(target) {
  const failing = openChange()?.checks?.impact?.failing || [];
  return failing.some((f) => f.target === target) ? "is-impact" : null;
}

/** The rules that link to a target, as chips, or a quiet "not linked" note. */
export function ruleLinks(idx, target) {
  const rules = idx.get(target) || [];
  if (!rules.length) return h("span", { class: "muted small unlinked" }, "Not linked to a rule");
  return h("span", { class: "rule-links" }, rules.map((r) => targetChip(`rule:${r}`)));
}

/** A calm empty state: one sentence and the next action. */
export function emptyState(text, action) {
  return h("div", { class: "empty-note" },
    h("p", {}, text),
    action ? h("button", { type: "button", class: "btn btn-quiet", onclick: action.onclick }, action.label) : null,
  );
}

const navInstalled = new Set();
/** J and K move through the items of a view (focus and selection). Installed once. */
export function installListNav(viewClass, itemSel) {
  if (navInstalled.has(viewClass)) return;
  navInstalled.add(viewClass);
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
    const k = e.key.toLowerCase();
    if (k !== "j" && k !== "k") return;
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable], dialog")) return;
    const root = document.querySelector("." + viewClass);
    if (!root) return;
    const items = [...root.querySelectorAll(itemSel)].filter((el) => !el.classList.contains("is-dim") && el.offsetParent !== null);
    if (!items.length) return;
    const cur = items.indexOf(e.target.closest ? e.target.closest(itemSel) : null);
    const next = k === "j" ? Math.min(items.length - 1, cur + 1) : Math.max(0, cur < 0 ? 0 : cur - 1);
    e.preventDefault();
    items[next].focus({ preventScroll: true });
    items[next].scrollIntoView({ block: "nearest" });
    if (items[next].dataset.target) update({ selection: [items[next].dataset.target] });
  });
}

let filter = "all";
let CTX = null;
const openDetails = new Set();

const ORDER = ["checked", "probe_gap", "untraced", "unchecked", "context"];

export function render(model, root, ctx) {
  CTX = ctx;
  const rules = model.rules || [];
  if (String(state.focusTarget || "").startsWith("rule:")) filter = "all";
  const byLine = new Map(rules.map((r) => [r.line, r]));
  const counts = Object.fromEntries(ORDER.map((st) => [st, 0]));
  for (const r of rules) counts[r.status] = (counts[r.status] || 0) + 1;

  add(root,
    h("header", { class: "view-head" },
      h("h1", {}, "Rules"),
      h("p", { class: "lede" }, `${rules.length} rules from REQUIREMENTS.md. The chip on each rule says whether Lattice checks it; click it to see what does.`),
    ),
    changeBanner(),
  );
  if (!rules.length) {
    add(root, emptyState("There are no rules yet. Describe what the app should do and Lattice drafts them.", {
      label: "Draft the first rules",
      onclick: () => ctx.prefillChange("Draft rules for ", []),
    }));
    return;
  }
  add(root, coverageBar(counts, rules.length), filterBar(counts, root), scopeNote(root));

  const doc = h("article", { class: ["md", "rules-doc", state.rulesScope && "is-scoped"], "aria-label": "REQUIREMENTS.md" });
  for (const b of parse(model.markdown || "")) doc.append(renderBlock(b, byLine, model));
  // Enter opens the rule's detail instead of only selecting it.
  doc.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.target.nodeType !== 1 || !e.target.classList.contains("rule")) return;
    e.preventDefault();
    e.stopPropagation();
    e.target.querySelector(".badge-rule")?.click();
  });
  add(root, doc);
  applyFilter(root);
  installListNav("view-rules", ".rule");
}

function coverageBar(counts, total) {
  const segs = ORDER.filter((st) => counts[st]).map((st) =>
    h("span", {
      class: ["cov-seg", RULE_STATUS[st].cls],
      style: { flexGrow: String(counts[st]) },
      title: `${RULE_STATUS[st].label}: ${counts[st]} of ${total}`,
    }),
  );
  return h("div", { class: "cov-bar", role: "img", "aria-label": ORDER.filter((st) => counts[st]).map((st) => `${counts[st]} ${RULE_STATUS[st].label}`).join(", ") }, segs);
}

function filterBar(counts, root) {
  const opts = [["all", `All ${Object.values(counts).reduce((a, b) => a + b, 0)}`], ...ORDER.filter((st) => counts[st]).map((st) => [st, `${RULE_STATUS[st].label} ${counts[st]}`])];
  return h("div", { class: "seg rules-filter", role: "group", "aria-label": "Show rules" },
    opts.map(([k, label]) =>
      h("button", {
        type: "button",
        class: "seg-btn",
        "aria-pressed": filter === k ? "true" : "false",
        onclick: (e) => {
          filter = k;
          for (const b of e.currentTarget.parentElement.children) b.setAttribute("aria-pressed", b === e.currentTarget ? "true" : "false");
          applyFilter(root);
        },
      }, label),
    ),
  );
}

/** "Rules about JobRun: 3 · Show all rules" when the Map opened Rules for one element. */
function scopeNote(root) {
  const scope = state.rulesScope;
  if (!scope) return null;
  const note = h("p", { class: "rules-scope", role: "status" },
    `Rules about ${scope.name}: ${scope.ids.length}. `,
    h("button", {
      type: "button", class: "btn btn-link",
      onclick: () => {
        state.rulesScope = null;
        note.remove();
        root.querySelector(".rules-doc")?.classList.remove("is-scoped");
        applyFilter(root);
      },
    }, "Show all rules"));
  return note;
}

function applyFilter(root) {
  const scope = state.rulesScope;
  for (const el of root.querySelectorAll(".rule")) {
    el.classList.toggle("is-dim", filter !== "all" && el.dataset.status !== filter);
    el.hidden = !!scope && !scope.ids.includes(el.dataset.target?.slice(5));
  }
}

/** The text a pending change gives a changed rule, read from the change's REQUIREMENTS.md
 *  diff: the rule's old lines minus the deleted ones, plus the lines inserted among them
 *  (an inserted line that starts a new list item belongs to a new rule). Null when the
 *  diff does not touch the rule's lines. */
function newRuleText(rule, model) {
  const diff = openChange()?.markdown_diff;
  if (!diff || !rule.line) return null;
  const old = String(model.markdown || "").split("\n");
  const item = /^\s*([-*+]|\d+\.)\s/;
  let end = rule.line;
  while (end < old.length && old[end].trim() && !item.test(old[end]) && !old[end].startsWith("#")) end++;
  const deleted = new Set();
  const inserted = new Map();
  let o = 0;
  for (const line of diff.split("\n")) {
    const hunk = /^@@ -(\d+)/.exec(line);
    if (hunk) o = Number(hunk[1]) - 1;
    else if (line.startsWith("---") || line.startsWith("+++")) continue;
    else if (line.startsWith("-")) deleted.add(++o);
    else if (line.startsWith("+")) {
      if (!inserted.has(o)) inserted.set(o, []);
      inserted.get(o).push(line.slice(1));
    } else if (o || line.startsWith(" ")) o++;
  }
  let touched = false;
  const out = [];
  for (let n = rule.line; n <= end; n++) {
    if (deleted.has(n)) touched = true;
    else out.push(old[n - 1]);
    for (const add of inserted.get(n) || []) {
      if ((n === end || deleted.has(n)) && item.test(add) && out.length) break;
      touched = true;
      out.push(add);
    }
  }
  if (!touched) return null;
  return out.map((l) => l.replace(item, "").trim()).join(" ");
}

function renderBlock(b, byLine, model) {
  if (b.type === "heading") {
    const tag = `h${Math.min(6, b.level + 1)}`;
    return h(tag, { class: "md-h", id: "md-" + slugify(b.text), html: inline(b.text) });
  }
  if (b.type === "hr") return h("hr");
  if (b.type === "code") return h("pre", { class: "md-code" }, h("code", {}, b.text));
  if (b.type === "para") return wrapRule(byLine.get(b.line), h("p", { html: inline(b.text) }), model, "div");
  if (b.type === "list") return renderList(b, byLine, model);
  return h("div");
}

function renderList(list, byLine, model) {
  const el = h(list.ordered ? "ol" : "ul", { class: "md-list" });
  for (const item of list.items) {
    const content = [h("div", { class: "md-li-text", html: inline(item.text) })];
    const rule = byLine.get(item.line);
    const li = h("li", {});
    li.append(wrapRule(rule, content, model, "div"));
    if (item.children) li.append(renderList(item.children, byLine, model));
    el.append(li);
  }
  return el;
}

/** An accepted change that touches this rule and is not built into the app yet. */
function notBuiltYet(rule, model) {
  return (model.changes || []).some((c) => {
    if (c.status !== "accepted" || c.build?.state !== "not_built") return false;
    const r = c.rules || {};
    const ids = [...(r.added || []), ...(r.removed || []), ...(r.changed || []).map((x) => (typeof x === "string" ? x : x.new || x.old))];
    return ids.includes(rule.id);
  });
}

function wrapRule(rule, content, model, tag) {
  if (!rule) return h(tag, { class: "md-plain" }, content);
  const target = `rule:${rule.id}`;
  const diff = ruleDiff(rule.id);
  const detailId = `rule-detail-${rule.id}`;
  const st = RULE_STATUS[rule.status] || RULE_STATUS.untraced;
  const detail = h("div", { class: "rule-detail", id: detailId, hidden: !openDetails.has(rule.id) });
  if (openDetails.has(rule.id)) fillDetail(detail, rule, model);
  const chip = ruleStatusChip(rule.status, {
    rule: rule.id,
    onclick: (e) => {
      e.stopPropagation();
      const open = detail.hidden;
      detail.hidden = !open;
      chip.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        openDetails.add(rule.id);
        fillDetail(detail, rule, model);
      } else openDetails.delete(rule.id);
    },
  });
  chip.setAttribute("aria-expanded", openDetails.has(rule.id) ? "true" : "false");
  chip.setAttribute("aria-controls", detailId);

  const res = appResultForRule(rule);
  const isContext = rule.status === "context";
  const untraced = rule.status === "untraced";
  const impact = !diff ? impactClass(target) : null;
  const newText = diff === "changed" ? newRuleText(rule, model) : null;
  return h(
    tag,
    {
      ...targetAttrs(target),
      class: ["rule", "acts-host", st.cls, diff && `diff-${diff}`, impact],
      "data-status": rule.status,
      "aria-label": `${rule.id}, ${st.label}`,
    },
    h("span", { class: "rule-id" }, rule.id),
    h("div", { class: "rule-main" },
      h("div", { class: ["rule-body", newText && "is-old"] }, content),
      newText ? h("div", { class: "rule-new" }, h("span", { class: "diff-sign", "aria-label": "becomes" }, "→"), h("span", { html: inline(newText) })) : null,
      h("div", { class: "rule-meta" },
        chip,
        notBuiltYet(rule, model) ? h("span", { class: "badge badge-rule st-notbuilt", title: "Accepted, but the code has not been rebuilt yet." }, icon("pending"), h("span", {}, "Not built yet")) : null,
        res !== undefined ? resultBadge(res) : null,
        diff === "changed" ? pendingBadge("Pending edit") : null,
        diff === "added" ? addedBadge() : null,
        diff === "removed" ? removedBadge() : null,
        impact ? h("span", { class: "badge badge-impact" }, icon("triangle"), h("span", {}, "Checks affected")) : null,
        untraced
          ? h("button", {
              type: "button",
              class: "btn btn-quiet trace-it",
              onclick: (e) => {
                e.stopPropagation();
                CTX.prefillChange(`Trace ${rule.id} to a check`, [target]);
              },
            }, icon("sparkle"), "Trace it")
          : null,
      ),
    ),
    actions(CTX, target, { label: rule.id, code: !isContext }),
    detail,
  );
}

function appResultForRule(rule) {
  // A rule passes on the app when every linked target the last check ran passed.
  const results = (rule.links || []).map((t) => appResult(t)).filter((r) => r !== undefined && r !== null);
  if (appResult("rule:" + rule.id) !== undefined && appResult("rule:" + rule.id) !== null) return appResult("rule:" + rule.id);
  if (!results.length) return undefined;
  return results.includes("fail") ? "fail" : "pass";
}

function fillDetail(el, rule, model) {
  el.replaceChildren();
  const st = RULE_STATUS[rule.status] || RULE_STATUS.untraced;
  el.append(h("p", { class: "rule-detail-lede" }, h("strong", {}, st.label + ". "), st.help));
  if (rule.status === "unchecked") {
    el.append(h("p", { class: "unchecked-label" }, icon("text-lines"), " Not checked by Lattice", rule.unchecked_reason ? `: ${rule.unchecked_reason}` : "."));
  }
  const links = rule.links || [];
  if (links.length) {
    const groups = new Map();
    for (const t of links) {
      const k = kindOf(t);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(t);
    }
    el.append(h("h4", {}, `Checked by ${links.length} ${links.length === 1 ? "item" : "items"}`));
    for (const [k, ts] of groups) {
      el.append(h("div", { class: "chip-row", "aria-label": k }, ts.map((t) => {
        const chip = targetChip(t);
        const r = appResult(t);
        if (r !== undefined) chip.append(h("span", { class: `chip-res res-${r || "none"}` }, r === "pass" ? "pass" : r === "fail" ? "fail" : "not run"));
        return chip;
      })));
    }
  } else if (rule.status !== "unchecked" && rule.status !== "context") {
    el.append(h("p", { class: "muted" }, "Nothing links to this rule."));
  }
  const qmap = new Map((model.views?.questions || []).map((q) => [q.id, q]));
  if ((rule.questions || []).length) {
    el.append(h("h4", {}, "Questions about this rule"));
    el.append(h("ul", { class: "q-list" }, rule.questions.map((qid) => {
      const q = qmap.get(qid);
      return h("li", {}, targetChip(`question:${qid}`), " ", q ? q.text : "", q && q.answer ? h("span", { class: "muted" }, ` Answered: ${q.answer}`) : q ? h("span", { class: "muted" }, ` Default if unanswered: ${q.default}`) : null);
    })));
  }
  if (rule.status === "probe_gap") {
    for (const g of (model.views?.probe_gaps || []).filter((g) => g.rule_id === rule.id)) {
      el.append(h("h4", {}, "The planted bug that got past the checks"), unifiedDiff(g.patch_summary, { label: `Probe patch for ${rule.id}` }));
    }
  }
}
