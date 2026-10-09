// Scenarios: searchable step stories (5.7). Each step reads as When (the request and
// the caller) and Then (status, body, database effect).

import { h, icon, debounce, add } from "../util.js";
import { targetAttrs, targetChip } from "../targets.js";
import { rulesByTarget, appResult, itemDiff, addedItems, openChange, changeBanner } from "../overlay.js";
import { resultBadge, addedBadge, removedBadge, pendingBadge } from "../badges.js";
import { applyMarks } from "../targets.js";
import { state } from "../store.js";

let query = "";
let show = "all";

export function render(model, root) {
  const scenarios = model.views?.section6?.scenarios || [];
  if (String(state.focusTarget || "").startsWith("scenario:")) {
    query = "";
    show = "all";
  }
  const idx = rulesByTarget(model);
  const listEl = h("div", { class: "scen-list" });
  const countEl = h("p", { class: "muted small", role: "status", "aria-live": "polite" });
  const hasResults = !!model.app?.last_check?.targets;

  const redraw = () => {
    listEl.replaceChildren();
    const q = query.trim().toLowerCase();
    const added = addedItems("scenario").map((a) => ({ target: a.target, name: a.name, steps: a.steps || [], added: true }));
    const all = [...added, ...scenarios];
    const shown = all.filter((sc) => {
      if (q && !(sc.name.toLowerCase().includes(q) || (sc.steps || []).some((st) => st.toLowerCase().includes(q)))) return false;
      if (show === "fail") return appResult(sc.target) === "fail";
      if (show === "changed") return sc.added || !!itemDiff(sc.target);
      return true;
    });
    countEl.textContent = `Showing ${shown.length} of ${all.length} scenarios`;
    const LIMIT = 200;
    for (const sc of shown.slice(0, LIMIT)) listEl.append(card(sc, idx));
    if (shown.length > LIMIT) listEl.append(h("p", { class: "muted" }, `${shown.length - LIMIT} more. Narrow the search to see them.`));
    if (!shown.length) listEl.append(h("p", { class: "empty" }, q ? `No scenario mentions “${query}”.` : "No scenarios match this filter."));
    applyMarks(root);
  };

  const filterOpts = [["all", "All"]];
  if (hasResults) filterOpts.push(["fail", "Failing on the app"]);
  if (openChange()) filterOpts.push(["changed", "Touched by the pending change"]);
  if (!filterOpts.some(([k]) => k === show)) show = "all";

  add(root, 
    h("header", { class: "view-head" },
      h("h1", {}, "Scenarios"),
      h("p", { class: "lede" }, `${scenarios.length} step-by-step stories the app must reproduce exactly.`),
    ),
    changeBanner(),
    h("div", { class: "toolbar" },
      h("label", { class: "search" },
        icon("search"),
        h("span", { class: "sr-only" }, "Search scenarios"),
        h("input", {
          type: "search",
          placeholder: "Search by name, endpoint, caller or status",
          value: query,
          oninput: debounce((e) => {
            query = e.target.value;
            redraw();
          }, 120),
        }),
      ),
      filterOpts.length > 1
        ? h("div", { class: "seg", role: "group", "aria-label": "Show scenarios" }, filterOpts.map(([k, label]) =>
            h("button", {
              type: "button", class: "seg-btn", "aria-pressed": show === k ? "true" : "false",
              onclick: (e) => {
                show = k;
                for (const b of e.currentTarget.parentElement.children) b.setAttribute("aria-pressed", b === e.currentTarget ? "true" : "false");
                redraw();
              },
            }, label)))
        : null,
    ),
    countEl,
    listEl,
  );
  redraw();
}

const STEP_RE = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (\S+)(.*?) as (.+?) -> (\d{3})(?: -- (.*))?$/;

export function parseStep(step) {
  const m = STEP_RE.exec(step);
  if (!m) return { raw: step };
  const rest = (m[6] || "").split(/;\s+/).filter(Boolean);
  let body = null;
  const db = [];
  for (const part of rest) {
    if (!body && part.startsWith("body ")) body = part.slice(5);
    else db.push(part);
  }
  return { method: m[1], path: m[2], extra: m[3].trim(), caller: m[4], status: m[5], body, db };
}

function card(sc, idx) {
  const diff = sc.added ? "added" : itemDiff(sc.target);
  const res = appResult(sc.target);
  const rules = idx.get(sc.target) || [];
  return h("article", { ...targetAttrs(sc.target), class: ["card", "scen", diff && `diff-${diff}`], "aria-label": `Scenario ${sc.name}` },
    h("header", { class: "card-head" },
      h("h2", { class: "scen-name" }, sc.name),
      res !== undefined ? resultBadge(res) : null,
      diff === "added" ? addedBadge() : diff === "removed" ? removedBadge() : diff === "modified" ? pendingBadge("Changed") : null,
    ),
    (sc.steps || []).length
      ? h("ol", { class: "steps" }, sc.steps.map((st) => stepRow(st)))
      : h("p", { class: "muted" }, "Its steps are in the pending change's diff."),
    rules.length ? h("div", { class: "chip-row" }, h("span", { class: "muted small" }, "Rules:"), rules.map((r) => targetChip(`rule:${r}`))) : null,
  );
}

function stepRow(step) {
  const p = parseStep(step);
  if (!p.method) return h("li", { class: "step" }, h("span", { class: "step-raw" }, step));
  const cls = /^2/.test(p.status) ? "s2xx" : /^5/.test(p.status) ? "s5xx" : "s4xx";
  return h("li", { class: "step" },
    h("div", { class: "gwt" },
      h("span", { class: "gwt-k" }, "When"),
      h("span", {},
        h("span", { class: "caller" }, p.caller), " calls ",
        h("span", { class: ["method", `m-${p.method.toLowerCase()}`] }, p.method), " ",
        h("code", {}, p.path), p.extra ? h("code", { class: "muted" }, " " + p.extra) : null,
      ),
    ),
    h("div", { class: "gwt" },
      h("span", { class: "gwt-k" }, "Then"),
      h("span", {},
        h("span", { class: ["status-pill", cls] }, p.status),
        p.body ? [" ", h("code", { class: "body" }, p.body)] : null,
      ),
    ),
    p.db.length ? h("div", { class: "gwt" }, h("span", { class: "gwt-k" }, "DB"), h("span", {}, p.db.map((d, i) => [i ? h("br") : null, h("code", {}, d)]))) : null,
  );
}
