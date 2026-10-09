// Invariants: one sentence each (5.5), with the layer C badge, the app's last result
// and the rules that ask for it. A pending change marks added and removed ones.

import { h, add } from "../util.js";
import { targetAttrs, targetChip } from "../targets.js";
import { rulesByTarget, appResult, itemDiff, addedItems, changeBanner } from "../overlay.js";
import { resultBadge, addedBadge, removedBadge, pendingBadge } from "../badges.js";

export function render(model, root) {
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
  const list = h("ol", { class: "rows" });
  for (const a of added) {
    list.append(row(a.target, a.name, a.text || "Added by the pending change (see its diff).", idx, "added"));
  }
  for (const [target, name, sentence] of invs) list.append(row(target, name, sentence, idx, itemDiff(target)));
  if (!invs.length && !added.length) add(root, h("p", { class: "empty" }, "The spec declares no invariants."));
  else add(root, list);
}

function row(target, name, sentence, idx, diff) {
  const res = appResult(target);
  const rules = idx.get(target) || [];
  return h("li", { ...targetAttrs(target), class: ["row", "inv", diff && `diff-${diff}`] },
    h("div", { class: "row-main" },
      h("span", { class: "layer layer-c", title: "Layer C: invariants" }, h("span", { "aria-hidden": "true" }, "∀"), h("span", { class: "sr-only" }, "Layer C")),
      h("p", { class: "sentence" }, diff === "removed" ? h("del", {}, sentence) : sentence),
    ),
    h("div", { class: "row-meta" },
      h("code", { class: "muted" }, name),
      res !== undefined ? resultBadge(res) : null,
      diff === "added" ? addedBadge() : diff === "removed" ? removedBadge() : diff === "modified" ? pendingBadge("Changed") : null,
      rules.map((r) => targetChip(`rule:${r}`)),
    ),
  );
}
