// Services: the background loops the spec declares.

import { h, add } from "../util.js";
import { targetAttrs, targetChip } from "../targets.js";
import { rulesByTarget, appResult, itemDiff, changeBanner } from "../overlay.js";
import { resultBadge, pendingBadge } from "../badges.js";

export function render(model, root) {
  const services = model.views?.section6?.services || [];
  add(root, 
    h("header", { class: "view-head" },
      h("h1", {}, "Services"),
      h("p", { class: "lede" }, "Background processes the app runs besides answering requests."),
    ),
    changeBanner(),
  );
  if (!services.length) {
    add(root, h("p", { class: "empty" }, "The spec declares no background services."));
    return;
  }
  const idx = rulesByTarget(model);
  add(root, h("ul", { class: "rows" }, services.map(([target, name, text]) => {
    const res = appResult(target);
    const diff = itemDiff(target);
    const rules = idx.get(target) || [];
    return h("li", { ...targetAttrs(target), class: ["row", diff && `diff-${diff}`] },
      h("div", { class: "row-main" }, h("h2", { class: "row-title" }, name), h("p", { class: "sentence" }, text)),
      h("div", { class: "row-meta" },
        res !== undefined ? resultBadge(res) : null,
        diff ? pendingBadge("Changed") : null,
        rules.map((r) => targetChip(`rule:${r}`)),
      ),
    );
  })));
}
