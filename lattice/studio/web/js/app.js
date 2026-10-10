// Lattice Studio: app shell. Loads /api/model, draws the top bar, the left nav and the
// current view, wires selection and highlight, and mounts the chat panel.

import { h, icon, clear, fmtTime, plural, add } from "./util.js";
import { api, sleep } from "./api.js";
import { state, update, subscribe } from "./store.js";
import { applyMarks, toggleSelection, highlight, clearMarks } from "./targets.js";
import { hasAppTargets } from "./overlay.js";
import { mountChat, prefillChange } from "./chat.js";
import * as rules from "./views/rules.js";
import * as access from "./views/access.js";
import * as data from "./views/data.js";
import * as machines from "./views/machines.js";
import * as invariants from "./views/invariants.js";
import * as scenarios from "./views/scenarios.js";
import * as services from "./views/services.js";
import * as gaps from "./views/gaps.js";

const VIEWS = [
  { id: "rules", label: "Rules", mod: rules, count: (m) => m.rules?.length },
  { id: "access", label: "Access", mod: access, count: (m) => (m.views?.access_groups || []).reduce((n, g) => n + (g.columns || []).length, 0) },
  { id: "data", label: "Data model", mod: data, count: (m) => m.data_model?.tables?.length },
  { id: "machines", label: "State machines", mod: machines, count: (m) => m.views?.section6?.machines?.length },
  { id: "invariants", label: "Invariants", mod: invariants, count: (m) => m.views?.section6?.invariants?.length },
  { id: "scenarios", label: "Scenarios", mod: scenarios, count: (m) => m.views?.section6?.scenarios?.length },
  { id: "services", label: "Services", mod: services, count: (m) => m.views?.section6?.services?.length },
  { id: "gaps", label: "Gaps", mod: gaps, count: gapCount, attention: true },
];

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
  add($actions, 
    h("button", {
      type: "button",
      class: "btn btn-primary",
      disabled: !state.connected || !configured || state.checkRunning,
      title: !configured ? "No app is hooked up (studio.toml), so there is nothing to check against." : "Run the full check of the spec against the app",
      onclick: runCheck,
    }, icon("play"), state.checkRunning ? "Checking…" : "Run check"),
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
  $nav.append(h("ul", {}, VIEWS.map((v) => {
    const n = m ? v.count(m) : null;
    return h("li", {},
      h("a", {
        href: `#${v.id}`,
        class: ["nav-item", state.view === v.id && "is-current"],
        "aria-current": state.view === v.id ? "page" : null,
        onclick: (e) => {
          e.preventDefault();
          go(v.id);
        },
      },
        h("span", {}, v.label),
        n !== null && n !== undefined ? h("span", { class: ["count", v.attention && n ? "count-attn" : ""], "aria-label": `${n} items` }, String(n)) : null,
      ),
    );
  })));
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

const ctx = { prefillChange };

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
  const v = VIEWS.find((x) => x.id === state.view) || VIEWS[0];
  const root = h("div", { class: `view view-${v.id}` });
  add($main, root);
  v.mod.render(state.model, root, ctx);
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
      const changesKey = (model) => JSON.stringify((model?.changes || []).map((c) => [c.id, c.status, c.superseded_by]));
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
  if ("view" in patch && location.hash.slice(1) !== st.view) history.replaceState(null, "", `#${st.view}`);
  if (keys.some((k) => ["model", "view", "change", "overlay", "marked", "loadError"].includes(k))) {
    const keep = !("view" in patch) && !st.focusTarget;
    renderMain(keep);
  } else if ("selection" in patch) {
    applyMarks($main);
  }
});

setTheme(storedTheme());
const initial = location.hash.slice(1);
if (VIEWS.some((v) => v.id === initial)) state.view = initial;
renderTopbar();
renderNav();
renderMain(false);
mountChat($chat, { onAccepted: load });
load();
pollModel();

// For debugging and for other scripts: window.studio.highlight(["cell:eve|..."]).
window.studio = { highlight, state };
