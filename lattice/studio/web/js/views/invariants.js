// Invariants: one always-true sentence each, with its last result and the rules that ask
// for it. A pending change marks added and removed ones. Hover or focus shows Ask,
// Change, Edit (the bound) and Code.

import { h, add } from "../util.js";
import { targetAttrs } from "../targets.js";
import { rulesByTarget, appResult, itemDiff, addedItems, changeBanner } from "../overlay.js";
import { resultBadge, addedBadge, removedBadge, pendingBadge } from "../badges.js";
import { actions, impactClass, ruleLinks, emptyState } from "./rules.js";

export function render(model, root, ctx) {
  const invs = model.views?.section6?.invariants || [];
  add(root,
    h("header", { class: "view-head" },
      h("h1", {}, "Invariants"),
      h("p", { class: "lede" }, `${invs.length} facts that must hold in the database after every request.`),
    ),
    changeBanner(),
  );
  const idx = rulesByTarget(model);
  const added = addedItems("invariant");
  if (!invs.length && !added.length) {
    add(root, emptyState("The spec declares no invariants yet. State something that must always be true.", {
      label: "Add an invariant",
      onclick: () => ctx.prefillChange("Add an invariant: ", []),
    }));
    return;
  }
  const list = h("ol", { class: "rows" });
  for (const a of added) list.append(row(ctx, a.target, a.name, a.text || "Added by the pending change (see its diff).", idx, "added"));
  for (const [target, name, sentence] of invs) list.append(row(ctx, target, name, sentence, idx, itemDiff(target)));
  add(root, list);
}

function row(ctx, target, name, sentence, idx, diff) {
  const res = appResult(target);
  const impact = !diff ? impactClass(target) : null;
  return h("li", { ...targetAttrs(target), class: ["row", "inv", "acts-host", diff && `diff-${diff}`, impact] },
    h("div", { class: "row-main" },
      h("p", { class: "sentence" },
        diff === "added" ? h("span", { class: "diff-sign", "aria-label": "Added" }, "+") : null,
        diff === "removed" ? h("span", { class: "diff-sign", "aria-label": "Removed" }, "−") : null,
        diff === "removed" ? h("del", {}, sentence) : sentence),
    ),
    h("div", { class: "row-meta" },
      h("code", { class: "muted" }, name),
      res !== undefined && res !== null ? resultBadge(res) : null,
      diff === "added" ? addedBadge() : diff === "removed" ? removedBadge() : diff === "modified" ? pendingBadge("Changed") : null,
      impact ? h("span", { class: "badge badge-impact" }, "Checks affected") : null,
      ruleLinks(idx, target),
    ),
    actions(ctx, target, { label: name }),
  );
}
