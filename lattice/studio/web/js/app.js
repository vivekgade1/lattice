// Lattice Studio: app shell. Loads /api/model, draws the top bar, the grouped navigator,
// the change tray and the current view, wires selection and highlight, mounts the chat.
//
// VIEW MODULE CONTRACT
//   export function render(model, root, ctx)
//     model  GET /api/model; root  the element to draw into (class "view view-<id>");
//     ctx    {
//       prefillChange(text, selected)  put a change request in the composer,
//       lens                           "intent" | "code" | "both" at render time,
//       hooks                          the shared registry from store.js (ask/change/edit),
//       openCode(target)               show the code for a target: lens "both" on an Intent
//                                      view, else the Code lens view; sets state.codeTarget,
//       askAbout(target)               hooks.ask(target) (default: select + open the chat),
//       changeAbout(target)            hooks.change(target) (default: prefill a change),
//       startEdit(target)              hooks.edit(target) (default: prefill a change),
//     }
//   Intent views render inside the lens frame (lens.js). Views of the Code and Evidence
//   groups render alone. Addressable elements use targetAttrs() so selection and marks work.
//   Optional second export for views that appear in the code pane: renderPane(model, root, target).

import { h, icon, clear, fmtTime, plural, add } from "./util.js";
import { api, sleep } from "./api.js";
import { state, update, subscribe, hooks } from "./store.js";
import { applyMarks, toggleSelection, highlight, clearMarks } from "./targets.js";
import { hasAppTargets } from "./overlay.js";
import { mountChat, prefillChange } from "./chat.js";
import { renderLensed, refreshPane } from "./lens.js";
import { mountTray, toast } from "./tray.js";
import { mountPalette, openPalette } from "./palette.js";
import * as map from "./views/map.js";
import * as rules from "./views/rules.js";
import * as access from "./views/access.js";
import * as data from "./views/data.js";
import * as machines from "./views/machines.js";
import * as invariants from "./views/invariants.js";
import * as scenarios from "./views/scenarios.js";
import * as services from "./views/services.js";
import * as code from "./views/code.js";
import * as flow from "./views/flow.js";
import * as gaps from "./views/gaps.js";
import * as checks from "./views/checks.js";
import * as rebuilds from "./views/rebuilds.js";

// Grouped navigator. `attn(m)` returns a short reason when the item needs attention (it
// draws the dot); `count(m)` shows a number only where it helps.
const GROUPS = [
  { id: "intent", title: "Intent", lens: true, views: [
    { id: "map", label: "Map", mod: map },
    { id: "rules", label: "Rules", mod: rules, count: (m) => m.rules?.length },
    { id: "access", label: "Access", mod: access },
    { id: "data", label: "Data", mod: data, count: (m) => m.data_model?.tables?.length },
    { id: "machines", label: "Lifecycle", mod: machines },
    { id: "invariants", label: "Invariants", mod: invariants },
    { id: "scenarios", label: "Scenarios", mod: scenarios },
    { id: "services", label: "Services", mod: services },
  ] },
  { id: "code", title: "Code", views: [
    { id: "code", label: "Code lens", mod: code },
    { id: "flow", label: "Request flow", mod: flow },
  ] },
  { id: "evidence", title: "Evidence", views: [
    { id: "gaps", label: "Gaps", mod: gaps, count: gapCount, attn: (m) => (gapCount(m) ? `${gapCount(m)} open` : null) },
    { id: "checks", label: "Checks", mod: checks, attn: (m) => (m.app?.last_check?.verdict === "fail" ? "the last check failed" : null) },
    { id: "rebuilds", label: "Rebuilds", mod: rebuilds, attn: (m) => (notBuilt(m).length ? `${notBuilt(m).length} accepted, not built` : null) },
  ] },
];
const VIEWS = GROUPS.flatMap((g) => g.views.map((v) => ({ ...v, group: g.id, lens: !!g.lens, groupTitle: g.title })));
const HOME = "map";

function notBuilt(m) {
  return (m?.changes || []).filter((c) => c.build?.state === "not_built");
}

const MODEL_POLL_MS = 15000;
const CHECK_POLL_MS = 1000;
const CHECK_POLL_LIMIT = 600;

const $main = document.getElementById("main");
const $nav = document.getElementById("leftnav");
const $project = document.getElementById("project");
const $actions = document.getElementById("top-actions");
const $chat = document.getElementById("chat");

function gapCount(m) {
  const v = m.views || {};
  return (
    (v.questions || []).filter((q) => !q.answer).length +
    (v.probe_gaps || []).length +
    (v.unchecked_trace || []).length +
    (v.untraced || []).length +
    (v.lint_gaps || []).length
  );
}

// ------------------------------------------------------------------ theme

function storedTheme() {
  try {
    return localStorage.getItem("studio-theme") || "auto";
  } catch (e) {
    return "auto";
  }
}

function setTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem("studio-theme", t);
  } catch (e) {
    /* storage blocked: the choice lasts for this page only */
  }
}

// ------------------------------------------------------------------ chrome

function renderTopbar() {
  const m = state.model;
  clear($project);
  if (m) {
    add($project, 
      h("span", { class: "project-name" }, m.project?.name || "Untitled project"),
      m.project?.git_head ? h("code", { class: "git-head", title: "Spec version (git HEAD)" }, m.project.git_head) : null,
    );
  } else {
    add($project, h("span", { class: "project-name" }, state.loadError ? "No project loaded" : "Loading…"));
  }
  clear($actions);
  add($actions, statusPill());
  if (m && hasAppTargets(m)) {
    const id = "overlay-toggle";
    add($actions, 
      h("label", { class: "toggle top-toggle", for: id, title: "Show each item's result from the last app check" },
        h("input", { type: "checkbox", id, checked: state.overlay, onchange: (e) => update({ overlay: e.target.checked }) }),
        h("span", {}, "App results"),
      ),
    );
  }
  const configured = !!m?.app?.configured;
  const pending = notBuilt(m);
  add($actions,
    h("button", {
      type: "button", id: "search-btn", class: "btn btn-quiet",
      title: "Search rules, views and code (⌘K)", "aria-label": "Search (⌘K)", "aria-keyshortcuts": "Meta+K",
      onclick: () => openPalette(), // the top bar re-draws, so the handler lives on the button
    }, icon("search"), h("span", { class: "kbd" }, "⌘K")),
    h("button", {
      type: "button",
      class: "btn btn-primary",
      disabled: !state.connected || !configured || state.checkRunning,
      title: !configured ? "No app is hooked up (studio.toml), so there is nothing to check against." : "Run the full check of the spec against the app",
      onclick: runCheck,
    }, icon("play"), state.checkRunning ? "Checking…" : "Run check"),
    h("button", {
      type: "button", id: "rebuild-btn", class: "btn",
      disabled: !state.connected || !pending.length,
      title: pending.length ? `${plural(pending.length, "accepted change")} not built` : "Nothing to rebuild: no accepted change is waiting",
      onclick: startRebuild,
    }, icon("play"), "Rebuild"),
    h("button", {
      type: "button",
      class: "btn btn-icon",
      title: `Theme: ${storedTheme()}`,
      "aria-label": `Colour theme: ${storedTheme()}. Change it`,
      onclick: () => {
        const order = ["auto", "light", "dark"];
        setTheme(order[(order.indexOf(storedTheme()) + 1) % order.length]);
        renderTopbar();
      },
    }, icon("sun")),
    h("button", {
      type: "button",
      class: "btn chat-toggle",
      "aria-controls": "chat",
      "aria-expanded": document.body.classList.contains("chat-open") ? "true" : "false",
      onclick: () => {
        document.body.classList.toggle("chat-open");
        renderTopbar();
        if (document.body.classList.contains("chat-open")) document.getElementById("chat-input")?.focus();
      },
    }, icon("chat"), "Chat"),
  );
}

function statusPill() {
  const m = state.model;
  if (!state.connected) return h("span", { class: "pill pill-none", role: "status" }, icon("dash"), "Not connected");
  if (!m?.app?.configured) return h("span", { class: "pill pill-none", role: "status", title: "Add studio.toml to the project to check the spec against the app." }, icon("dash"), "No app hooked up");
  const lc = m.app.last_check;
  if (state.checkRunning) return h("span", { class: "pill pill-running", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Checking the app…");
  if (!lc) return h("span", { class: "pill pill-none", role: "status" }, icon("dash"), "Not run yet");
  const nFind = (lc.findings || []).length;
  const counts = Object.entries(lc.counts || {}).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(" · ");
  const pass = lc.verdict === "pass";
  return h("span", {
    class: ["pill", pass ? "pill-pass" : "pill-fail"],
    role: "status",
    title: `Last check ${fmtTime(lc.at)}${counts ? `; findings by layer: ${counts}` : ""}`,
  },
    icon(pass ? "dot" : "diamond"),
    pass ? "Last check: pass" : `Last check: fail · ${plural(nFind, "finding")}`,
    h("span", { class: "pill-time" }, fmtTime(lc.at)),
  );
}

function renderNav() {
  clear($nav);
  const m = state.model;
  add($nav, GROUPS.map((g) => h("section", { class: "nav-group", "aria-labelledby": `ng-${g.id}` },
    h("h2", { class: "nav-group-title", id: `ng-${g.id}` }, g.title),
    h("ul", {}, g.views.map((v) => {
      const n = m && v.count ? v.count(m) : null;
      const why = m && v.attn ? v.attn(m) : null;
      const current = state.view === v.id;
      return h("li", {},
        h("a", {
          href: `#${v.id}`,
          class: ["nav-item", current && "is-current"],
          "aria-current": current ? "page" : null,
          onclick: (e) => {
            e.preventDefault();
            document.body.classList.remove("nav-open");
            go(v.id);
          },
        },
          h("span", {}, v.label),
          h("span", { class: "nav-meta" },
            n !== null && n !== undefined ? h("span", { class: ["count", v.attn && n ? "count-attn" : ""], "aria-label": `${n} items` }, String(n)) : null,
            why && !(v.attn && n) ? h("span", { class: "nav-dot", role: "img", "aria-label": `Needs attention: ${why}`, title: why }) : null,
          ),
        ),
      );
    })),
  )));
  const menu = document.getElementById("nav-toggle");
  if (menu) menu.setAttribute("aria-expanded", document.body.classList.contains("nav-open") ? "true" : "false");
}

function go(view) {
  if (state.view === view) return;
  clearMarks();
  update({ view });
  $main.scrollTop = 0;
  window.scrollTo({ top: 0 });
  $main.focus({ preventScroll: true });
}

// ------------------------------------------------------------------ views

// Defaults for the hook registry; chat.js / edit.js replace them on mount.
hooks.ask = hooks.ask || ((target) => {
  if (!state.selection.includes(target)) toggleSelection(target);
  document.body.classList.add("chat-open");
  document.getElementById("chat-input")?.focus();
});
hooks.change = hooks.change || ((target) => prefillChange("", [target]));
hooks.edit = hooks.edit || ((target) => prefillChange("", [target]));

function openCode(target) {
  const v = VIEWS.find((x) => x.id === state.view);
  if (v?.lens) update({ codeTarget: target, lens: state.lens === "intent" ? "both" : state.lens });
  else update({ codeTarget: target, view: "code" });
}

function makeCtx() {
  return {
    prefillChange,
    lens: state.lens,
    hooks,
    openCode,
    askAbout: (t) => hooks.ask(t, [...state.selection]),
    changeAbout: (t) => hooks.change(t, [...state.selection]),
    startEdit: (t) => hooks.edit(t, [...state.selection]),
  };
}

function renderMain(keepScroll) {
  const top = $main.scrollTop;
  clear($main);
  if (!state.model) {
    add($main, state.loadError
      ? h("div", { class: "empty-state", role: "alert" },
          h("h1", {}, "Not connected"),
          h("p", {}, state.loadError),
          h("p", { class: "muted" }, "Start the server with “lattice studio PROJECT_DIR”, or the mock with “uv run python -m lattice.studio.mock_server”."),
          h("button", { type: "button", class: "btn btn-primary", onclick: load }, "Try again"))
      : h("div", { class: "skeleton", "aria-busy": "true" }, h("div", {}), h("div", {}), h("div", {})));
    return;
  }
  const v = VIEWS.find((x) => x.id === state.view) || VIEWS.find((x) => x.id === HOME);
  try {
    if (v.lens) renderLensed(state.model, $main, v.mod, v.id, makeCtx());
    else {
      const root = h("div", { class: `view view-${v.id}` });
      add($main, root);
      v.mod.render(state.model, root, makeCtx());
    }
  } catch (e) {
    clear($main);
    add($main, h("div", { class: "empty-state", role: "alert" },
      h("h1", {}, v.label),
      h("p", {}, "This view could not be drawn."),
      h("p", { class: "muted" }, String(e.message || e))));
    console.error(e);
  }
  if (keepScroll) $main.scrollTop = top;
  applyMarks($main);
  document.title = `${v.label} · ${state.model.project?.name || "Lattice Studio"}`;
}

// Click (or Enter/Space) on any addressable element adds it to the selection.
function isInteractive(el) {
  return !!el.closest("button, a, input, textarea, select, summary, label, .diff");
}

$main.addEventListener("click", (e) => {
  if (isInteractive(e.target)) return;
  if (window.getSelection && String(window.getSelection()).length) return; // the owner is selecting text
  const el = e.target.closest("[data-target]");
  if (el) toggleSelection(el.dataset.target);
});
$main.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const el = e.target;
  if (el?.dataset?.target && e.target === el) {
    e.preventDefault();
    toggleSelection(el.dataset.target);
  }
});

window.addEventListener("hashchange", () => {
  const id = location.hash.slice(1);
  if (VIEWS.some((v) => v.id === id) && id !== state.view) go(id);
});

// ------------------------------------------------------------------ data

async function load() {
  try {
    const model = await api.model();
    update({ model, connected: true, loadError: null, overlay: state.overlay && hasAppTargets(model) });
  } catch (e) {
    update({ connected: false, loadError: e.message });
  }
}

async function pollModel() {
  for (;;) {
    await sleep(MODEL_POLL_MS);
    if (state.checkRunning) continue;
    try {
      const m = await api.model();
      const old = state.model;
      const changesKey = (model) => JSON.stringify((model?.changes || []).map((c) => [c.id, c.status, c.superseded_by, c.build?.state]));
      const changed = !old
        || old.project?.git_head !== m.project?.git_head
        || old.app?.last_check?.at !== m.app?.last_check?.at
        || changesKey(old) !== changesKey(m);
      if (changed) update({ model: m, connected: true });
      else if (!state.connected) update({ connected: true, loadError: null });
    } catch (e) {
      if (state.connected) update({ connected: false });
    }
  }
}

async function startRebuild() {
  const ids = notBuilt(state.model).map((c) => c.id);
  if (!ids.length) return;
  try {
    const { id } = await api.rebuild(ids);
    update({ view: "rebuilds", rebuild: id });
    await load();
  } catch (e) {
    toast(e.unsupported ? e.message : `Rebuild could not start: ${e.message}`);
  }
}

// While a rebuild runs, refresh the model every two seconds so the tray and the Rebuild
// button follow it; the slow poll above covers everything else.
async function pollBuilding() {
  for (;;) {
    await sleep(2000);
    const live = (state.model?.changes || []).some((c) => c.build?.state === "rebuilding");
    if (!live || state.checkRunning) continue;
    try {
      update({ model: await api.model(), connected: true });
    } catch (e) {
      /* the slow poll reports a lost connection */
    }
  }
}

async function runCheck() {
  const before = state.model?.app?.last_check?.at || null;
  update({ checkRunning: true });
  try {
    await api.check();
    for (let i = 0; i < CHECK_POLL_LIMIT; i++) {
      await sleep(CHECK_POLL_MS);
      const m = await api.model();
      if ((m.app?.last_check?.at || null) !== before) {
        update({ model: m, checkRunning: false, connected: true });
        return;
      }
    }
    update({ checkRunning: false });
  } catch (e) {
    update({ checkRunning: false, connected: false, loadError: e.message });
  }
}

// ------------------------------------------------------------------ boot

subscribe((st, patch) => {
  const keys = Object.keys(patch);
  if (keys.some((k) => ["model", "connected", "checkRunning", "overlay", "loadError"].includes(k))) renderTopbar();
  if (keys.some((k) => ["model", "view"].includes(k))) renderNav();
  if ("view" in patch) document.body.classList.remove("nav-open");
  if ("view" in patch && st.view !== "rules" && !("rulesScope" in patch)) st.rulesScope = null;
  if ("view" in patch && location.hash.slice(1) !== st.view) history.replaceState(null, "", `#${st.view}`);
  if (keys.some((k) => ["model", "view", "change", "overlay", "marked", "loadError", "lens"].includes(k))) {
    const keep = !("view" in patch) && !st.focusTarget;
    renderMain(keep);
  } else if ("selection" in patch || "codeTarget" in patch) {
    applyMarks($main);
    if (state.lens !== "intent") refreshPane();
  }
});

setTheme(storedTheme());
const initial = location.hash.slice(1);
if (VIEWS.some((v) => v.id === initial)) state.view = initial;
renderTopbar();
renderNav();
renderMain(false);
mountChat($chat, { onAccepted: load });
mountTray(document.getElementById("tray"), { onChanged: load });
mountPalette({
  views: VIEWS.map((v) => ({ id: v.id, label: v.label, group: v.groupTitle })),
  go,
  openCode,
});
document.getElementById("nav-toggle")?.addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
  renderNav();
});
load();
pollModel();
pollBuilding();

// For debugging and for other scripts: window.studio.highlight(["cell:eve|..."]).
window.studio = { highlight, state, hooks };
