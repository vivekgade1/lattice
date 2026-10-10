// Lens: the Intent | Code | Both switch at the top of every Intent view.
//   intent  the view fills the canvas
//   code    only the code pane (views/code.js renderPane) for the selected element
//   both    the view on the left, the code pane on the right
// The store key `lens` holds the choice; app.js re-renders the canvas when it changes.

import { h, clear } from "./util.js";
import { state, update } from "./store.js";
import { renderPane } from "./views/code.js";

const LENSES = [
  ["intent", "Intent"],
  ["code", "Code"],
  ["both", "Both"],
];

/** The target the code pane shows: the one openCode() set, else the last selected. */
export function paneTarget() {
  return state.codeTarget || state.selection[state.selection.length - 1] || null;
}

export function lensControl() {
  return h("div", { class: "lens", role: "group", "aria-label": "Lens" },
    LENSES.map(([id, label]) =>
      h("button", {
        type: "button",
        class: ["lens-btn", state.lens === id && "is-current"],
        "aria-pressed": state.lens === id ? "true" : "false",
        onclick: () => update({ lens: id }),
      }, label),
    ),
  );
}

function fillPane(model, pane) {
  clear(pane);
  try {
    const r = renderPane(model, pane, paneTarget());
    if (r && typeof r.catch === "function") r.catch(() => {});
  } catch (e) {
    pane.append(h("p", { class: "muted" }, "The code pane could not be drawn."));
  }
}

/** Draw an Intent view inside the lens frame into `host`. */
export function renderLensed(model, host, viewMod, viewId, ctx) {
  const lens = state.lens;
  host.append(h("div", { class: "lens-bar" }, lensControl()));
  const body = h("div", { class: `lens-body lens-${lens}` });
  if (lens !== "code") {
    const root = h("div", { class: `view view-${viewId}` });
    body.append(h("div", { class: "lens-view" }, root));
    viewMod.render(model, root, ctx);
  }
  if (lens !== "intent") {
    const pane = h("aside", { class: "code-pane", "aria-label": "Code for the selection" });
    body.append(pane);
    fillPane(model, pane);
  }
  host.append(body);
}

/** Re-draw only the code pane (the selection or code target changed). */
export function refreshPane() {
  const pane = document.querySelector(".code-pane");
  if (pane && state.model) fillPane(state.model, pane);
}
