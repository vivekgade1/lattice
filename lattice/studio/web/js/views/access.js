// Access: one matrix per path prefix. Rows are endpoints (method + path template),
// columns are callers, as in the Figma policy matrix (5.3). A cell's target is still
// the contract's `cell:<caller>|<METHOD /template>`.

import { h, icon, add } from "../util.js";
import { statusClass, STATUS_CLASS_LABEL, resultBadge } from "../badges.js";
import { targetAttrs, tid, cellTarget } from "../targets.js";
import { cellDiff, appResult, changeBanner } from "../overlay.js";

const STATUS_WORDS = {
  200: "OK", 201: "Created", 204: "No content", 401: "Not signed in", 403: "Forbidden",
  404: "Not found", 405: "Method not allowed", 409: "Conflict", 422: "Invalid", 500: "Server error",
};

export function render(model, root) {
  const groups = model.views?.access_groups || [];
  add(root, 
    h("header", { class: "view-head" },
      h("h1", {}, "Access"),
      h("p", { class: "lede" }, "Who may call what. Each cell lists the statuses the spec's scenarios expect for that caller on that endpoint."),
    ),
    changeBanner(),
    legend(),
  );
  if (!groups.length) {
    add(root, h("p", { class: "empty" }, "The spec has no access scenarios yet."));
    return;
  }
  for (const g of groups) add(root, groupTable(g));
}

function legend() {
  return h("ul", { class: "legend", "aria-label": "Cell legend" },
    Object.entries(STATUS_CLASS_LABEL).map(([cls, v]) =>
      h("li", {}, h("span", { class: ["cell-swatch", cls] }, icon(v.glyph)), v.label),
    ),
    h("li", {}, h("span", { class: "cell-swatch pending" }, icon("pending")), "Pending change (old → new)"),
  );
}

function methodChip(endpoint) {
  const [method, ...rest] = endpoint.split(" ");
  return h("span", { class: "endpoint" },
    h("span", { class: ["method", `m-${method.toLowerCase()}`] }, method),
    h("code", { class: "path" }, rest.join(" ")),
  );
}

function groupTable(g) {
  const callers = g.rows || [];
  const endpoints = g.columns || [];
  const table = h("table", { class: "matrix" },
    h("caption", {}, `/${g.prefix}: ${endpoints.length} ${endpoints.length === 1 ? "endpoint" : "endpoints"} × ${callers.length} callers`),
    h("thead", {}, h("tr", {},
      h("th", { scope: "col", class: "matrix-corner" }, "Endpoint"),
      callers.map((c) => h("th", { scope: "col", class: "matrix-caller" }, c)),
    )),
    h("tbody", {}, endpoints.map((ep) =>
      h("tr", {},
        h("th", { scope: "row", id: tid(`endpoint:${ep}`), class: "matrix-endpoint" }, methodChip(ep)),
        callers.map((caller) => cell(g, caller, ep)),
      ),
    )),
  );
  return h("section", { class: "matrix-group", "aria-label": `/${g.prefix}` },
    h("h2", {}, `/${g.prefix}`),
    h("div", { class: "scroll-x", tabindex: "0", role: "region", "aria-label": `/${g.prefix} access matrix` }, table),
  );
}

function describe(value) {
  const v = String(value ?? "·");
  if (v === "·") return "not stated by the spec";
  return v.split("/").map((c) => `${c}${STATUS_WORDS[c] ? " " + STATUS_WORDS[c] : ""}`).join(" or ");
}

function cell(g, caller, ep) {
  const key = `${caller}|${ep}`;
  const value = g.cells?.[key] ?? "·";
  const target = cellTarget(caller, ep);
  const diff = cellDiff(caller, ep);
  const cls = statusClass(diff ? diff.new : value);
  const glyph = STATUS_CLASS_LABEL[cls].glyph;
  const res = appResult(target);
  const label = diff
    ? `${caller} calling ${ep}: pending change from ${describe(diff.old)} to ${describe(diff.new)}`
    : `${caller} calling ${ep}: ${describe(value)}`;
  const content = diff
    ? [
        h("span", { class: "cell-old" }, h("del", {}, diff.old ?? value)),
        h("span", { class: "cell-arrow", "aria-hidden": "true" }, "→"),
        h("span", { class: "cell-new" }, icon(glyph), diff.new),
      ]
    : [icon(glyph), h("span", { class: "cell-val" }, value)];
  return h("td", {
    ...targetAttrs(target),
    class: ["mcell", cls, diff && "pending"],
    title: label,
    "aria-label": label,
  }, content, res ? resultBadge(res) : null);
}
