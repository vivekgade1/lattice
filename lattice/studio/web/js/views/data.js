// Data model: tables, columns and constraints (Figma 5.2's node content, as cards).

import { h, icon, add } from "../util.js";
import { targetAttrs, targetChip } from "../targets.js";
import { rulesByTarget, appResult, changeBanner } from "../overlay.js";
import { resultBadge } from "../badges.js";

export function render(model, root) {
  const tables = model.data_model?.tables || [];
  add(root, 
    h("header", { class: "view-head" },
      h("h1", {}, "Data model"),
      h("p", { class: "lede" }, `${tables.length} tables. Keys, allowed values, bounds and private columns come from the spec's table profiles.`),
    ),
    changeBanner(),
  );
  if (!tables.length) {
    add(root, h("p", { class: "empty" }, "The model has no tables."));
    return;
  }
  const idx = rulesByTarget(model);
  add(root, h("div", { class: "cards" }, tables.map((t) => tableCard(t, idx))));
}

function tableCard(t, idx) {
  const target = `profile:${t.name}`;
  const rules = idx.get(target) || [];
  const res = appResult(target);
  return h("section", { ...targetAttrs(target), class: "card table-card", "aria-label": `Table ${t.name}` },
    h("header", { class: "card-head" },
      h("h2", {}, t.name),
      h("code", { class: "muted" }, t.sql),
      res ? resultBadge(res) : null,
    ),
    h("ul", { class: "cols" }, (t.columns || []).map((c) => columnRow(t, c, idx))),
    rules.length ? h("div", { class: "chip-row" }, h("span", { class: "muted small" }, "Rules:"), rules.map((r) => targetChip(`rule:${r}`))) : null,
  );
}

function columnRow(t, c, idx) {
  const target = `profile:${t.name}.${c.name}`;
  const chips = [];
  if (c.unique) chips.push(h("span", { class: "cchip cchip-key" }, "unique"));
  if (c.nullable) chips.push(h("span", { class: "cchip" }, "nullable"));
  if (c.private) chips.push(h("span", { class: "cchip cchip-private" }, icon("lock"), "private"));
  if (c.bounds) chips.push(h("span", { class: "cchip cchip-bound" }, c.bounds));
  for (const v of c.one_of || []) chips.push(h("span", { class: "cchip cchip-enum" }, v));
  const rules = idx.get(target) || [];
  return h("li", { ...targetAttrs(target), class: ["col", c.private && "is-private"] },
    h("span", { class: "col-name" }, c.name),
    h("span", { class: "col-type" }, c.type),
    h("span", { class: "col-chips" }, chips, rules.map((r) => targetChip(`rule:${r}`))),
  );
}
