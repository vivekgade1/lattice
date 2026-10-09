// Gaps: everything that needs the owner's attention. Open questions, probe gaps (with
// the patch that slipped through), rules Lattice does not check, untraced rules and lint.

import { h, icon, add } from "../util.js";
import { tid, targetChip, targetAttrs } from "../targets.js";
import { unifiedDiff } from "../diff.js";
import { ruleStatusChip } from "../badges.js";
import { changeBanner } from "../overlay.js";

export function render(model, root, ctx) {
  const v = model.views || {};
  const questions = v.questions || [];
  const open = questions.filter((q) => !q.answer);
  const answered = questions.filter((q) => q.answer);
  const probe = v.probe_gaps || [];
  const unTrace = v.unchecked_trace || [];
  const unNotes = v.unchecked_notes || [];
  const untraced = v.untraced || [];
  const lint = v.lint_gaps || [];

  add(root, 
    h("header", { class: "view-head" },
      h("h1", {}, "Gaps"),
      h("p", { class: "lede" }, attention(open.length, probe.length, unTrace.length, untraced.length, lint.length)),
    ),
    changeBanner(),
    h("nav", { class: "toc", "aria-label": "Gap sections" }, h("ul", {},
      tocItem("gap-questions", "Open questions", open.length),
      tocItem("gap-probe", "Probe gaps", probe.length),
      tocItem("gap-unchecked", "Not checked by Lattice", unTrace.length),
      tocItem("gap-untraced", "Untraced rules", untraced.length),
      tocItem("gap-lint", "Lint findings", lint.length),
    )),
  );

  // Open questions
  add(root, section("gap-questions", "Open questions", `${open.length} of ${questions.length} unanswered. Until you answer, the spec uses the default shown.`,
    open.length ? h("ol", { class: "rows" }, open.map((q) => questionRow(q, ctx))) : h("p", { class: "empty" }, "No open questions."),
    answered.length ? h("details", {}, h("summary", {}, `Answered (${answered.length})`), h("ol", { class: "rows" }, answered.map((q) => questionRow(q, ctx)))) : null,
  ));

  // Probe gaps
  add(root, section("gap-probe", "Probe gaps", "A planted bug in the app passed every linked check. Each patch shows the bug that got through; the rule needs a sharper scenario.",
    probe.length ? h("ol", { class: "rows" }, probe.map((g) =>
      h("li", { class: "row", id: tid(`gap:probe:${g.rule_id}`) },
        h("div", { class: "row-main" }, ruleStatusChip("probe_gap"), targetChip(`rule:${g.rule_id}`), h("p", { class: "sentence" }, g.rule_text)),
        h("details", {}, h("summary", {}, "The patch that slipped through"), unifiedDiff(g.patch_summary, { label: `Probe patch for ${g.rule_id}` })),
      ))) : h("p", { class: "empty" }, "No probe gaps: every planted bug was caught."),
  ));

  // Unchecked
  add(root, section("gap-unchecked", "Not checked by Lattice", "These requirements are true to the text but nothing tests them. Promote one to a rule if a check is possible.",
    unTrace.length ? h("ol", { class: "rows" }, unTrace.map((u) =>
      h("li", { class: "row unchecked-row", id: tid(`gap:unchecked:${u.rule_id}`) },
        h("div", { class: "row-main" }, ruleStatusChip("unchecked"), targetChip(`rule:${u.rule_id}`), h("p", { class: "sentence" }, u.rule_text)),
        h("p", { class: "muted" }, h("strong", {}, "Why: "), u.reason),
        h("div", { class: "row-actions" }, h("button", { type: "button", class: "btn btn-quiet", onclick: () => ctx.prefillChange(`Make ${u.rule_id} checkable: ${u.rule_text}`, [`rule:${u.rule_id}`]) }, icon("sparkle"), "Promote to rule")),
      ))) : null,
    unNotes.length ? h("div", {}, h("h3", {}, "Parts of rules the spec cannot express"), h("ul", { class: "rows" }, unNotes.map((n) =>
      h("li", { class: "row unchecked-row" },
        h("div", { class: "row-main" }, ruleStatusChip("unchecked"), h("p", { class: "sentence" }, n.text)),
        (n.concerns || []).length ? h("div", { class: "chip-row" }, h("span", { class: "muted small" }, "Concerns:"), n.concerns.map((c) => targetChip(`profile:${c}`))) : null,
      )))) : null,
    !unTrace.length && !unNotes.length ? h("p", { class: "empty" }, "Every requirement is checked.") : null,
  ));

  // Untraced
  add(root, section("gap-untraced", "Untraced rules", "Rules with no linked check and no reason given.",
    untraced.length ? h("ol", { class: "rows" }, untraced.map((r) =>
      h("li", { class: "row" }, h("div", { class: "row-main" }, ruleStatusChip("untraced"), targetChip(`rule:${r.id}`), h("p", { class: "sentence" }, r.text))),
    )) : h("p", { class: "empty" }, "None: every rule links to a check or says why it cannot."),
  ));

  // Lint
  add(root, section("gap-lint", "Lint findings", "Places where the scenarios would miss a plausible bug.",
    lint.length ? h("ol", { class: "rows" }, lint.map((l) =>
      h("li", { class: "row" },
        h("div", { class: "row-main" },
          h("code", { class: "lint-code" }, l.code),
          l.rule_id ? targetChip(`rule:${l.rule_id}`) : null,
          l.element ? targetChip(l.element) : null,
        ),
        h("p", { class: "sentence" }, l.message),
        l.fix ? h("details", {}, h("summary", {}, "Suggested fix"), h("pre", { class: "code-block" }, l.fix)) : null,
      ))) : h("p", { class: "empty" }, "No lint findings."),
  ));
}

function attention(q, p, u, t, l) {
  const parts = [
    [q, "open question", "open questions"],
    [p, "probe gap", "probe gaps"],
    [u, "rule not checked by Lattice", "rules not checked by Lattice"],
    [t, "untraced rule", "untraced rules"],
    [l, "lint finding", "lint findings"],
  ].filter(([n]) => n).map(([n, one, many]) => `${n} ${n === 1 ? one : many}`);
  const total = q + p + u + t + l;
  if (!total) return "Nothing needs your attention: every rule is checked and every question answered.";
  return `${total} ${total === 1 ? "item needs" : "items need"} your attention: ${parts.join(", ")}.`;
}

function tocItem(id, label, n) {
  return h("li", {}, h("a", { href: `#${id}`, onclick: (e) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ block: "start" });
  } }, label, h("span", { class: ["count", n ? "count-on" : ""] }, String(n))));
}

function section(id, title, lede, ...body) {
  return h("section", { class: "gap-section", id, "aria-labelledby": `${id}-h` },
    h("h2", { id: `${id}-h` }, title),
    h("p", { class: "muted" }, lede),
    body,
  );
}

function questionRow(q, ctx) {
  return h("li", { ...targetAttrs(`question:${q.id}`), class: "row q-row" },
    h("div", { class: "row-main" }, h("span", { class: "q-id" }, q.id), h("p", { class: "sentence" }, q.text)),
    q.answer
      ? h("p", {}, h("strong", {}, "Answer: "), q.answer)
      : h("p", { class: "muted" }, h("strong", {}, "Default if unanswered: "), q.default),
    h("div", { class: "row-meta" },
      (q.rules || []).map((r) => targetChip(`rule:${r}`)),
      !q.answer ? h("button", { type: "button", class: "btn btn-quiet", onclick: (e) => {
        e.stopPropagation();
        ctx.prefillChange(`Answer ${q.id}: ${q.text} — `, (q.rules || []).map((r) => `rule:${r}`));
      } }, "Answer as a change") : null,
    ),
  );
}
