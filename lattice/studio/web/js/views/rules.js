// Rules: the owner's REQUIREMENTS.md, rendered, with a status chip on each rule.
// Clicking a chip opens what checks the rule (its links), why it is not checked, its
// open questions and, for a probe gap, the patch that slipped past the checks.

import { h, icon, add } from "../util.js";
import { parse, inline, slugify } from "../markdown.js";
import { RULE_STATUS, ruleStatusChip, resultBadge, pendingBadge, removedBadge } from "../badges.js";
import { targetAttrs, targetChip, kindOf } from "../targets.js";
import { appResult, ruleDiff, changeBanner } from "../overlay.js";
import { unifiedDiff } from "../diff.js";
import { state } from "../store.js";

let filter = "all";
const openDetails = new Set();

const ORDER = ["checked", "probe_gap", "untraced", "unchecked", "context"];

export function render(model, root) {
  const rules = model.rules || [];
  if (String(state.focusTarget || "").startsWith("rule:")) filter = "all";
  const byLine = new Map(rules.map((r) => [r.line, r]));
  const counts = Object.fromEntries(ORDER.map((s) => [s, 0]));
  for (const r of rules) counts[r.status] = (counts[r.status] || 0) + 1;

  add(root, 
    h("header", { class: "view-head" },
      h("h1", {}, "Rules"),
      h("p", { class: "lede" }, `${rules.length} rules from REQUIREMENTS.md. Each chip says whether Lattice checks the rule. Click a chip to see what checks it.`),
    ),
    changeBanner(),
    coverageBar(counts, rules.length),
    filterBar(counts, root, model),
  );

  const doc = h("article", { class: "md", "aria-label": "REQUIREMENTS.md" });
  for (const b of parse(model.markdown || "")) doc.append(renderBlock(b, byLine, model));
  add(root, doc);
  applyFilter(root);
}

function coverageBar(counts, total) {
  const segs = ORDER.filter((s) => counts[s]).map((s) =>
    h("span", {
      class: ["cov-seg", RULE_STATUS[s].cls],
      style: { flexGrow: String(counts[s]) },
      title: `${RULE_STATUS[s].label}: ${counts[s]} of ${total}`,
    }),
  );
  const legend = ORDER.filter((s) => counts[s]).map((s) =>
    h("li", {}, ruleStatusChip(s), h("span", { class: "num" }, `${counts[s]} of ${total}`)),
  );
  return h("section", { class: "coverage", "aria-label": "Rule coverage" },
    h("div", { class: "cov-bar", role: "img", "aria-label": ORDER.filter((s) => counts[s]).map((s) => `${counts[s]} ${RULE_STATUS[s].label}`).join(", ") }, segs),
    h("ul", { class: "cov-legend" }, legend),
  );
}

function filterBar(counts, root) {
  const opts = [["all", "All rules"], ...ORDER.filter((s) => counts[s]).map((s) => [s, RULE_STATUS[s].label])];
  return h("div", { class: "seg", role: "group", "aria-label": "Show rules" },
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

function applyFilter(root) {
  for (const el of root.querySelectorAll(".rule")) {
    el.classList.toggle("is-dim", filter !== "all" && el.dataset.status !== filter);
  }
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
  return h(
    tag,
    {
      ...targetAttrs(target),
      class: ["rule", st.cls, diff && `diff-${diff}`],
      "data-status": rule.status,
      "aria-label": `${rule.id}, ${st.label}`,
    },
    h("div", { class: "rule-gutter" },
      h("span", { class: "rule-id" }, rule.id),
      chip,
      res !== undefined ? resultBadge(res) : null,
      diff === "changed" ? pendingBadge("Pending edit") : null,
      diff === "removed" ? removedBadge() : null,
    ),
    h("div", { class: "rule-body" }, content),
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
