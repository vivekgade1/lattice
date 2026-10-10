// Access: one matrix per path prefix. Rows are endpoints (method + path template),
// columns are callers. A cell's target is the contract's `cell:<caller>|<METHOD /template>`.
// Quiet by design: no gridlines, status as a word with a small tint. A cell nobody states
// shows a muted dot and offers "Make this a rule". Hovering or focusing any cell shows
// Ask, Change, Edit and Code in one floating bar.

import { h, icon, add } from "../util.js";
import { statusClass, STATUS_CLASS_LABEL, resultBadge } from "../badges.js";
import { targetAttrs, tid, cellTarget } from "../targets.js";
import { cellDiff, appResult, changeBanner } from "../overlay.js";
import { actions, impactClass } from "./rules.js";

const STATUS_WORDS = {
  200: "OK", 201: "Created", 204: "No content", 401: "Not signed in", 403: "Forbidden",
  404: "Not found", 405: "Method not allowed", 409: "Conflict", 422: "Invalid", 500: "Server error",
};

export function render(model, root, ctx) {
  const groups = model.views?.access_groups || [];
  add(root,
    h("header", { class: "view-head" },
      h("h1", {}, "Access"),
      h("p", { class: "lede" }, "Who may call what. Each cell lists the statuses the spec expects for that caller on that endpoint."),
    ),
    changeBanner(),
    legend(),
  );
  if (!groups.length) {
    add(root, h("div", { class: "empty-note" },
      h("p", {}, "The spec states no access rules yet."),
      h("button", { type: "button", class: "btn btn-quiet", onclick: () => ctx.prefillChange("Describe who may call each endpoint: ", []) }, "Describe who may call what"),
    ));
    return;
  }
  const bar = floatingBar(root, ctx);
  for (const g of groups) add(root, groupTable(g, ctx, bar));
  add(root, bar.el);
}

function legend() {
  return h("ul", { class: "legend", "aria-label": "Cell legend" },
    Object.entries(STATUS_CLASS_LABEL).map(([cls, v]) =>
      h("li", {}, h("span", { class: ["cell-swatch", cls] }, icon(v.glyph)), v.label),
    ),
    h("li", {}, h("span", { class: "cell-swatch pending" }, icon("pending")), "Pending change (old → new)"),
  );
}

/** One floating action bar shared by every cell, so the scrolling matrix never clips it. */
function floatingBar(root, ctx) {
  const el = h("div", { class: "cell-bar", hidden: true });
  let cur = null;
  let timer = null;
  const hide = () => {
    el.hidden = true;
    cur = null;
  };
  const later = () => {
    clearTimeout(timer);
    timer = setTimeout(hide, 160);
  };
  const show = (td) => {
    clearTimeout(timer);
    if (cur === td) return;
    cur = td;
    el.replaceChildren(actions(ctx, td.dataset.target, { label: td.getAttribute("aria-label") || td.dataset.target }));
    el.hidden = false;
    const r = td.getBoundingClientRect();
    const host = root.getBoundingClientRect();
    el.style.top = `${r.bottom - host.top - 2}px`;
    el.style.left = `${Math.max(0, r.left - host.left + r.width / 2)}px`;
  };
  root.addEventListener("pointerover", (e) => {
    const td = e.target.closest?.("td.mcell");
    if (td) show(td);
    else if (e.target.closest?.(".cell-bar")) clearTimeout(timer);
  });
  root.addEventListener("pointerout", (e) => {
    if (e.target.closest?.("td.mcell, .cell-bar")) later();
  });
  root.addEventListener("focusin", (e) => {
    const td = e.target.closest?.("td.mcell");
    if (td) show(td);
    else if (!e.target.closest?.(".cell-bar")) hide();
  });
  root.addEventListener("focusout", (e) => {
    if (e.target.closest?.("td.mcell, .cell-bar")) later();
  });
  root.addEventListener("scroll", hide, true);
  return { el, hide };
}

function methodChip(endpoint) {
  const [method, ...rest] = endpoint.split(" ");
  return h("span", { class: "endpoint" },
    h("span", { class: ["method", `m-${method.toLowerCase()}`] }, method),
    h("code", { class: "path" }, rest.join(" ")),
  );
}

function groupTable(g, ctx) {
  const callers = g.rows || [];
  const endpoints = g.columns || [];
  const table = h("table", { class: "matrix" },
    h("caption", { class: "sr-only" }, `/${g.prefix}: ${endpoints.length} ${endpoints.length === 1 ? "endpoint" : "endpoints"} by ${callers.length} callers`),
    h("thead", {}, h("tr", {},
      h("th", { scope: "col", class: "matrix-corner" }, "Endpoint"),
      callers.map((c) => h("th", { scope: "col", class: "matrix-caller" }, c)),
    )),
    h("tbody", {}, endpoints.map((ep) =>
      h("tr", {},
        h("th", { scope: "row", id: tid(`endpoint:${ep}`), class: "matrix-endpoint" }, methodChip(ep)),
        callers.map((caller) => cell(g, caller, ep, ctx)),
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

function cell(g, caller, ep, ctx) {
  const key = `${caller}|${ep}`;
  const value = g.cells?.[key] ?? "·";
  const target = cellTarget(caller, ep);
  const diff = cellDiff(caller, ep);
  const cls = statusClass(diff ? diff.new : value);
  const glyph = STATUS_CLASS_LABEL[cls].glyph;
  const res = appResult(target);
  const unstated = !diff && cls === "unstated";
  const impact = !diff ? impactClass(target) : null;
  const label = diff
    ? `${caller} calling ${ep}: pending change from ${describe(diff.old)} to ${describe(diff.new)}`
    : `${caller} calling ${ep}: ${describe(value)}`;
  let content;
  if (diff) {
    content = [
      h("span", { class: "cell-old" }, h("del", {}, diff.old ?? value)),
      h("span", { class: "cell-arrow", "aria-hidden": "true" }, "→"),
      h("span", { class: "cell-new" }, diff.new),
    ];
  } else if (unstated) {
    content = h("span", { class: "cell-empty" },
      h("span", { class: "cell-dot", "aria-hidden": "true", title: "Not stated" }),
      h("button", {
        type: "button",
        class: "cell-make",
        onclick: (e) => {
          e.stopPropagation();
          ctx.prefillChange(`State what ${caller} gets from ${ep}`, [target]);
        },
      }, "Make this a rule"),
    );
  } else {
    content = h("span", { class: "cell-val" }, value);
  }
  return h("td", {
    ...targetAttrs(target),
    class: ["mcell", cls, diff && "pending", impact],
    title: unstated ? null : label,
    "aria-label": label,
  }, content, res ? resultBadge(res) : null);
}
