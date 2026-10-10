// Services: the background loops the spec declares. Hover or focus shows Ask, Change,
// Edit (wait limits) and Code.

import { h, add } from "../util.js";
import { targetAttrs } from "../targets.js";
import { rulesByTarget, appResult, itemDiff, changeBanner } from "../overlay.js";
import { resultBadge, pendingBadge } from "../badges.js";
import { actions, impactClass, ruleLinks, emptyState } from "./rules.js";

export function render(model, root, ctx) {
  const services = model.views?.section6?.services || [];
  add(root,
    h("header", { class: "view-head" },
      h("h1", {}, "Services"),
      h("p", { class: "lede" }, "Background processes the app runs besides answering requests."),
    ),
    changeBanner(),
  );
  if (!services.length) {
    add(root, emptyState("The spec declares no background services. Describe a worker or scheduler if the app has one.", {
      label: "Describe a service",
      onclick: () => ctx.prefillChange("A background service: ", []),
    }));
    return;
  }
  const idx = rulesByTarget(model);
  add(root, h("ul", { class: "rows" }, services.map(([target, name, text]) => {
    const res = appResult(target);
    const diff = itemDiff(target);
    const impact = !diff ? impactClass(target) : null;
    return h("li", { ...targetAttrs(target), class: ["row", "acts-host", diff && `diff-${diff}`, impact] },
      h("div", { class: "row-main" }, h("h2", { class: "row-title" }, name), h("p", { class: "sentence" }, text)),
      h("div", { class: "row-meta" },
        res !== undefined && res !== null ? resultBadge(res) : null,
        diff ? pendingBadge("Changed") : null,
        ruleLinks(idx, target),
      ),
      actions(ctx, target, { label: name }),
    );
  })));
}
