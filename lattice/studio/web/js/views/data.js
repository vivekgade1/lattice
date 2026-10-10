// Data: each table is a flat section with hairline-separated columns. Private columns
// carry a lock icon and the word "private". Hover or focus shows Ask, Change, Edit, Code.

import { h, icon, add } from "../util.js";
import { targetAttrs } from "../targets.js";
import { rulesByTarget, appResult, changeBanner } from "../overlay.js";
import { resultBadge } from "../badges.js";
import { actions, impactClass, ruleLinks, emptyState } from "./rules.js";

export function render(model, root, ctx) {
  const tables = model.data_model?.tables || [];
  add(root,
    h("header", { class: "view-head" },
      h("h1", {}, "Data"),
      h("p", { class: "lede" }, `${tables.length} tables. Keys, allowed values, bounds and private columns come from the spec's table profiles.`),
    ),
    changeBanner(),
  );
  if (!tables.length) {
    add(root, emptyState("The model has no tables yet. Describe the data the app keeps.", {
      label: "Describe the data",
      onclick: () => ctx.prefillChange("The app keeps these tables: ", []),
    }));
    return;
  }
  const idx = rulesByTarget(model);
  add(root, h("div", { class: "entities" }, tables.map((t) => entity(t, idx, ctx))));
}

function entity(t, idx, ctx) {
  const target = `profile:${t.name}`;
  const res = appResult(target);
  return h("section", { ...targetAttrs(target), class: ["entity", "acts-host", impactClass(target)], "aria-label": `Table ${t.name}` },
    h("header", { class: "entity-head" },
      h("h2", {}, t.name),
      h("code", { class: "muted" }, t.sql),
      res !== undefined && res !== null ? resultBadge(res) : null,
      ruleLinks(idx, target),
    ),
    actions(ctx, target, { label: `table ${t.name}` }),
    h("ul", { class: "cols" }, (t.columns || []).map((c) => columnRow(t, c, idx, ctx))),
  );
}

function columnRow(t, c, idx, ctx) {
  const target = `profile:${t.name}.${c.name}`;
  const chips = [];
  if (c.unique) chips.push(h("span", { class: "cchip cchip-key" }, "unique"));
  if (c.nullable) chips.push(h("span", { class: "cchip" }, "nullable"));
  if (c.private) chips.push(h("span", { class: "cchip cchip-private" }, icon("lock"), "private"));
  if (c.bounds) chips.push(h("span", { class: "cchip cchip-bound" }, c.bounds));
  for (const v of c.one_of || []) chips.push(h("span", { class: "cchip cchip-enum" }, v));
  const rules = idx.get(target) || [];
  const res = appResult(target);
  return h("li", { ...targetAttrs(target), class: ["col", "acts-host", c.private && "is-private", impactClass(target)] },
    h("span", { class: "col-name" }, c.name),
    h("span", { class: "col-type" }, c.type),
    h("span", { class: "col-chips" }, chips, rules.length ? ruleLinks(idx, target) : null, res !== undefined && res !== null ? resultBadge(res) : null),
    actions(ctx, target, { label: `column ${t.name}.${c.name}` }),
  );
}
