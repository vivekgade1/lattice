// Evidence > Build: build the app from the spec, area by area, onto a target branch.
//
// The areas come from GET /api/areas?branch=<target> (a top-level section of the
// requirements), the branches from GET /api/branches. A scoped POST /api/rebuild may block
// until the baseline run is done; the build in view lives in state.buildRun so the tray
// and the navigator can show it, and the Rebuild card (views/rebuilds.js) draws it.
//
// Also exported: startBuild / watchBuilds (app.js starts the watcher per project) and
// decorateRuleHeadings / decorateMap (small "Build…" links on the Rules and Map views).

import { h, icon, add, plural } from "../util.js";
import { api, sleep } from "../api.js";
import { state, update, subscribe, currentProject, canBuild, BUILD_WHY } from "../store.js";
import { rebuildCard, pollRebuild, buildRefusal } from "./rebuilds.js";
import { RULE_STATUS, ruleStatusChip } from "../badges.js";
import { targetChip } from "../targets.js";
import { appResult } from "../overlay.js";
import { toast } from "../tray.js";

const LIVE = new Set(["queued", "baseline", "running"]);
const TERMINAL = new Set(["passed", "gave_up", "stopped"]);
const AREA_STATUS = {
  not_built: { label: "Not built", icon: "dash" },
  partial: { label: "Partial", icon: "pending" },
  built: { label: "Built", icon: "check" },
  building: { label: "Building", icon: null },
};
const MAX_ROUNDS = 5;

// ------------------------------------------------------------------ view state
// Survives re-draws of the view (the model poll redraws it) and is reset per project.

const ui = {
  base: null,
  open: false, // the branch panel
  branch: null, // the chosen existing branch name (null: the default target)
  creating: false, // "New branch…" is chosen
  newName: "",
  newFrom: "HEAD",
  confirm: false, // "I understand, update this branch"
  rounds: 3,
  expanded: new Set(), // area ids
  selected: null, // the area opened from a "Build…" link (kept highlighted)
  focusSelected: false,
};
const cache = { base: null, branches: null, areas: null, areasFor: null, history: null };
let loadToken = 0;

const branchKey = () => `studio-build-branch|${api.base()}`;

function remembered() {
  try {
    return sessionStorage.getItem(branchKey()) || null;
  } catch (e) {
    return null;
  }
}

function remember(name) {
  try {
    if (name) sessionStorage.setItem(branchKey(), name);
    else sessionStorage.removeItem(branchKey());
  } catch (e) {
    /* storage blocked: the choice lasts for this page only */
  }
}

function resetFor() {
  const base = api.base();
  if (ui.base === base) return;
  ui.base = base;
  ui.open = false;
  ui.branch = remembered();
  ui.creating = false;
  ui.newName = "";
  ui.newFrom = "HEAD";
  ui.confirm = false;
  ui.rounds = 3;
  ui.expanded = new Set();
  ui.selected = null;
  cache.base = base;
  cache.branches = null;
  cache.areas = null;
  cache.areasFor = null;
  cache.history = null;
}

// ------------------------------------------------------------------ build runs

const following = new Set();

function isLive(run) {
  if (!run) return false;
  if (run.phase === "starting") return true;
  return run.phase === "live" && !TERMINAL.has(run.record?.status);
}

/** The reason no build can start right now, or "". */
function busyReason() {
  const run = state.buildRun;
  if (run?.phase === "starting") return "A build is starting. Wait for its first check, or stop it.";
  if (isLive(run)) return `A build is running${run.id ? ` (${run.id})` : ""}. Wait for it to finish, or stop it.`;
  if ((state.model?.changes || []).some((c) => c.build?.state === "rebuilding")) return "A rebuild of accepted changes is running. Wait for it to finish.";
  return "";
}

function followRun(id) {
  const key = `${api.base()}|${id}`;
  if (following.has(key)) return;
  following.add(key);
  let last = "";
  pollRebuild(id, (r, err) => {
    if (err) {
      following.delete(key);
      const run = state.buildRun;
      if (run && run.id === id) update({ buildRun: { ...run, phase: "error", message: `Lost track of the build: ${err.message}` } });
      return;
    }
    const sig = JSON.stringify(r);
    const run = state.buildRun;
    if (sig !== last && (!run || run.id === id || run.id === null)) {
      last = sig;
      update({
        buildRun: {
          ...(run || {}), id, phase: LIVE.has(r.status) ? "live" : "done", record: r,
          title: r.scope?.kind === "all" ? "everything" : r.scope?.title || run?.title || "the spec",
          branch: r.target_branch || run?.branch,
        },
      });
    }
    if (!LIVE.has(r.status)) {
      following.delete(key);
      // the build ended: the top bar, the tray and the areas follow the new state
      api.model().then((m) => update({ model: m, connected: true })).catch(() => {});
    }
  });
}

/** Start a scoped build. opts: {scope, title, scopeKind, branch (body or null), confirm,
 *  rounds}. The POST may block until the baseline is done: the record is found by
 *  listing meanwhile, so Stop works. */
export async function startBuild(opts) {
  if (busyReason()) return;
  const ep = api.epoch();
  const body = { scope: opts.scope, max_rounds: opts.rounds };
  if (opts.branch) body.branch = opts.branch;
  if (opts.confirm) body.confirm_unsafe_branch = true;
  const branchName = opts.branch?.name || opts.defaultTarget || "the default branch";
  update({ buildRun: { phase: "starting", id: null, record: null, title: opts.title, branch: branchName, scopeKind: opts.scopeKind, message: null } });
  let finding = true;
  (async () => {
    while (finding && ep === api.epoch()) {
      await sleep(1000);
      if (!finding || ep !== api.epoch()) return;
      try {
        const list = await api.rebuildList();
        const rec = (Array.isArray(list) ? list : []).find((r) => r.scope && r.status === "baseline");
        if (rec && finding && state.buildRun?.phase === "starting" && !state.buildRun.id) {
          update({ buildRun: { ...state.buildRun, id: rec.id, record: rec } });
        }
      } catch (e) {
        if (e.unsupported) return;
      }
    }
  })();
  try {
    const { id } = await api.buildStart(body);
    finding = false;
    if (ep !== api.epoch()) return;
    update({ buildRun: { ...(state.buildRun || {}), id, phase: "live" } });
    followRun(id);
  } catch (e) {
    finding = false;
    if (ep !== api.epoch()) return;
    const run = state.buildRun || {};
    if (run.stopping) {
      update({ buildRun: { ...run, phase: "stopped", message: `Stopped. Nothing changed on ${branchName}.` } });
    } else {
      update({ buildRun: { ...run, phase: "error", id: null, code: e.code || null, message: buildRefusal(e, { branch: opts.branch?.name, scopeKind: opts.scopeKind }) } });
    }
  }
}

async function stopStarting() {
  const run = state.buildRun;
  if (!run?.id) return;
  update({ buildRun: { ...run, stopping: true } });
  try {
    await api.rebuildStop(run.id);
  } catch (e) {
    update({ buildRun: { ...state.buildRun, stopping: false } });
    toast(`Could not stop the build: ${e.message}`);
  }
}

/** One watcher per project (app.js starts it): picks up a scoped build that is running,
 *  or the last one on record, so the view and the tray show it after a reload. Ends when
 *  the project switches or the server has no list route (the mock). */
export async function watchBuilds(ep) {
  let first = true;
  while (ep === api.epoch()) {
    const run = state.buildRun;
    if (!isLive(run)) {
      try {
        const list = await api.rebuildList();
        if (ep !== api.epoch()) return;
        const scoped = (Array.isArray(list) ? list : []).filter((r) => r.scope);
        const live = scoped.filter((r) => LIVE.has(r.status)).pop();
        const cur = state.buildRun;
        if (live && !isLive(cur)) {
          update({ buildRun: { phase: "live", id: live.id, record: live, title: live.scope.kind === "all" ? "everything" : live.scope.title, branch: live.target_branch } });
          followRun(live.id);
        } else if (first && !cur && scoped.length) {
          const last = scoped[scoped.length - 1];
          update({ buildRun: { phase: "done", id: last.id, record: last, title: last.scope.kind === "all" ? "everything" : last.scope.title, branch: last.target_branch } });
        }
      } catch (e) {
        if (e.unsupported) return;
      }
    }
    first = false;
    await sleep(8000);
  }
}

// ------------------------------------------------------------------ branches

const exists = (b, name) => (b?.branches || []).some((x) => x.name === name);
const branchInfo = (b, name) => (b?.branches || []).find((x) => x.name === name) || null;

/** What the owner picked, resolved against the branch list. */
function selection(b) {
  if (!b) return null;
  if (ui.creating) {
    const name = ui.newName.trim();
    return { kind: "new", name, from: ui.newFrom, body: { name, create: true, from: ui.newFrom }, error: newNameError(b, name) };
  }
  let name = ui.branch;
  if (!name || (name !== b.default_target && !exists(b, name))) name = b.default_target;
  const info = branchInfo(b, name);
  const virtual = !info; // the default target, created on its first use
  // checked out in a linked worktree: always refused. Protected or checked out in the
  // owner's own checkout: allowed with an explicit confirmation.
  const elsewhere = Boolean(info && info.checked_out && !info.current);
  const unsafe = Boolean(info && !elsewhere && (info.protected || info.current));
  return {
    kind: virtual ? "virtual" : "existing", name, info, unsafe, elsewhere,
    body: name === b.default_target ? null : { name },
    error: elsewhere ? `${name} is checked out in another working tree. Pick a different branch.` : "",
  };
}

function newNameError(b, name) {
  if (!name) return "Give the new branch a name.";
  if (/\s/.test(name)) return "Branch names cannot contain spaces.";
  if (/[~^:?*[\\]|\.\.|^-|\/$|\.lock$|@\{/.test(name)) return "That is not a valid branch name.";
  if (exists(b, name)) return `A branch named ${name} already exists. Choose it from the list instead.`;
  return "";
}

function consequence(b, sel) {
  const cur = b.current_branch || "your current branch";
  if (!sel) return "";
  if (sel.kind === "new") {
    const from = sel.from === "HEAD" ? cur : sel.from;
    return `Created from ${from}. Nothing on ${from} changes.`;
  }
  if (sel.kind === "virtual") return `Created from ${cur} on first use. Nothing on ${cur} changes.`;
  if (sel.elsewhere) return "";
  if (sel.unsafe) return `Studio will fast-forward ${sel.name} to the build. ${sel.info.current ? "Your files change." : ""}`.trim();
  return `Builds are added to ${sel.name}. ${cur} is not changed.`;
}

// ------------------------------------------------------------------ the view

export function render(model, root, ctx) {
  resetFor();
  const proj = currentProject();
  add(root, h("header", { class: "view-head" },
    h("h1", {}, "Build"),
    h("p", { class: "bv-project" }, h("span", { class: "muted" }, "Project "), h("strong", {}, model.project?.name || proj?.name || "Untitled")),
    h("p", { class: "lede" }, "Builds are checked against the spec. Your checked-out branch is not changed unless you confirm it."),
  ));
  if (!canBuild()) {
    add(root, unsupported(proj));
    return;
  }

  const ep = api.epoch();
  const controls = h("section", { class: "bv-controls", "aria-label": "Build settings" });
  const runSlot = h("section", { class: "bv-run", "aria-label": "Current build", "aria-live": "polite" });
  const listSlot = h("section", { class: "bv-areas", "aria-label": "Areas" });
  const histSlot = h("section", { class: "bv-history", "aria-label": "Recent builds" });
  add(root, controls, runSlot, listSlot, histSlot);

  const stale = (tok) => tok !== loadToken || ep !== api.epoch() || !root.isConnected;
  const why = h("p", { class: "bv-why muted small", id: "bv-why" });
  let whyText = "";

  // ---- the build buttons: one place decides whether they are enabled
  function blockReason() {
    const b = cache.branches;
    if (!b || !cache.areas) return "Loading the areas…";
    const busy = busyReason();
    if (busy) return busy;
    const sel = selection(b);
    if (sel?.error) return sel.error;
    if (sel?.unsafe && !ui.confirm) return `${sel.name} is ${sel.info.protected ? "protected" : "checked out"}. Confirm the update in the target branch panel first.`;
    return "";
  }

  function syncBusy() {
    const reason = blockReason();
    whyText = reason === "Loading the areas…" ? "" : reason;
    why.textContent = whyText;
    why.hidden = !whyText;
    for (const btn of root.querySelectorAll("[data-build]")) {
      btn.disabled = Boolean(reason);
      if (reason) {
        btn.title = reason;
        btn.setAttribute("aria-describedby", "bv-why");
      } else {
        btn.removeAttribute("title");
        btn.removeAttribute("aria-describedby");
      }
    }
    const check = root.querySelector("[data-check]");
    if (check) {
      const configured = !!state.model?.app?.configured;
      check.disabled = !configured || state.checkRunning || !state.connected;
      check.title = !configured ? "No app is hooked up (studio.toml), so there is nothing to check against." : "";
      check.lastChild.textContent = state.checkRunning ? "Checking…" : "Check now";
    }
  }

  function launch(scope, title, scopeKind) {
    const b = cache.branches;
    const sel = selection(b);
    if (!b || !sel || blockReason()) return;
    startBuild({
      scope, title, scopeKind, branch: sel.body, confirm: sel.unsafe && ui.confirm, rounds: ui.rounds,
      defaultTarget: b.default_target,
    });
  }

  // ---- controls: target branch, rounds, top actions
  const focusKeep = (fn) => {
    const fk = document.activeElement?.dataset?.fk;
    fn();
    if (fk) root.querySelector(`[data-fk="${fk}"]`)?.focus({ preventScroll: true });
  };

  let conseqEl = null;
  let allEl = null;
  let warnEl = null;
  let nameErrEl = null;

  function syncBranchText() {
    const b = cache.branches;
    const sel = selection(b);
    if (!b || !sel) return;
    if (conseqEl) conseqEl.textContent = consequence(b, sel);
    if (nameErrEl) {
      const e = ui.newName ? sel.error || "" : "";
      nameErrEl.textContent = e;
      nameErrEl.hidden = !e;
    }
    if (warnEl) warnEl.hidden = !(sel.unsafe);
    const label = root.querySelector("[data-branch-label]");
    if (label) label.textContent = sel.kind === "new" ? sel.name || "New branch…" : sel.name;
    syncBusy();
  }

  function drawControls() {
    const b = cache.branches;
    controls.replaceChildren();
    if (!b) {
      controls.append(h("p", { class: "muted", "aria-busy": "true" }, "Loading branches…"));
      return;
    }
    const sel = selection(b);
    const current = b.current_branch;
    // the per-build working branches (rebuild/*) are not targets
    const list = (b.branches || []).filter((x) => !/^rebuild\//.test(x.name) || x.name === sel?.name);
    if (!exists(b, b.default_target)) list.unshift({ name: b.default_target, virtual: true, is_integration: true });
    list.sort((x, y) => (x.name === b.default_target ? -1 : y.name === b.default_target ? 1 : 0));

    const radio = (value, checked, disabled, label, extra, fk) => {
      const input = h("input", { type: "radio", name: "bv-target", value, "data-fk": fk, disabled: disabled ? true : null,
        onchange: () => pick(value) });
      input.checked = checked;
      return h("label", { class: ["bv-opt", disabled && "is-disabled"] }, input, label, extra);
    };
    const pick = (value) => {
      if (value === "__new__") {
        ui.creating = true;
      } else {
        ui.creating = false;
        ui.branch = value;
        remember(value);
      }
      ui.confirm = false;
      focusKeep(drawControls);
      loadAreas(); // the areas' status is per branch
    };

    const options = list.map((x) => {
      const elsewhere = Boolean(x.checked_out && !x.current);
      const badges = [
        x.current ? chip("current", "check") : null,
        x.protected ? chip("protected", "lock") : null,
        x.is_integration ? chip("integration build branch", "dot") : null,
        elsewhere ? chip("checked out elsewhere", "dash") : null,
        x.virtual ? h("span", { class: "muted small" }, "created on first use") : null,
        x.ahead_of_base ? h("span", { class: "muted small" }, `${x.ahead_of_base} ahead`) : null,
      ];
      return h("li", {}, radio(x.name, !ui.creating && sel?.name === x.name, elsewhere,
        [h("span", { class: "s2-mono bv-opt-name" }, x.name), badges], null, `r:${x.name}`));
    });

    const nameInput = h("input", {
      type: "text", class: "bv-input", id: "bv-new-name", "data-fk": "new-name", autocomplete: "off", spellcheck: "false",
      placeholder: "build/my-branch", "aria-describedby": "bv-new-err",
      oninput: (e) => { ui.newName = e.target.value; syncBranchText(); },
    });
    nameInput.value = ui.newName;
    nameErrEl = h("p", { class: "bv-field-err small", id: "bv-new-err", role: "alert", hidden: true });
    const fromSel = h("select", {
      class: "bv-select", id: "bv-new-from", "data-fk": "new-from",
      onchange: (e) => { ui.newFrom = e.target.value; syncBranchText(); loadAreas(); },
    }, [
      h("option", { value: "HEAD" }, `Current HEAD (${current})`),
      ...(b.branches || []).filter((x) => x.name !== current).map((x) => h("option", { value: x.name }, x.name)),
    ]);
    fromSel.value = ui.newFrom;
    const newBlock = h("li", {},
      radio("__new__", ui.creating, false, h("span", { class: "bv-opt-name" }, "New branch…"), null, "r:__new__"),
      ui.creating
        ? h("div", { class: "bv-new" },
            h("label", { for: "bv-new-name" }, "Name"), nameInput, nameErrEl,
            h("label", { for: "bv-new-from" }, "Start from"), fromSel)
        : null);

    conseqEl = h("p", { class: "bv-conseq", "aria-live": "polite" }, consequence(b, sel));
    const confirm = h("input", {
      type: "checkbox", id: "bv-confirm", "data-fk": "confirm",
      onchange: (e) => { ui.confirm = e.target.checked; syncBusy(); },
    });
    confirm.checked = ui.confirm;
    warnEl = h("div", { class: "bv-warn", role: "note", hidden: !sel?.unsafe },
      h("p", {}, icon("triangle"),
        h("span", {}, sel?.info?.protected
          ? ` ${sel?.name} is a protected branch. Building onto it changes it for everyone who uses it.`
          : ` ${sel?.name} is checked out in your working tree. Building onto it changes your files.`)),
      h("p", { class: "muted small" }, "It needs a clean working tree, and the update is fast-forward only: nothing is overwritten or rewritten."),
      h("label", { class: "toggle", for: "bv-confirm" }, confirm, h("span", {}, "I understand, update this branch")));

    const panelId = "bv-branch-panel";
    const toggle = h("button", {
      type: "button", class: "btn bv-branch-btn", "aria-expanded": ui.open ? "true" : "false", "aria-controls": panelId, "data-fk": "branch-btn",
      onclick: () => { ui.open = !ui.open; focusKeep(drawControls); },
    }, h("span", { class: "muted" }, "Target branch"), h("span", { class: "s2-mono", "data-branch-label": "" }, sel?.kind === "new" ? sel.name || "New branch…" : sel?.name || ""),
    h("span", { class: "muted", "aria-hidden": "true" }, ui.open ? "▴" : "▾"));
    const panel = h("div", {
      class: "bv-panel", id: panelId, role: "group", "aria-label": "Target branch", hidden: !ui.open,
      onkeydown: (e) => {
        if (e.key === "Escape") { ui.open = false; drawControls(); root.querySelector('[data-fk="branch-btn"]')?.focus(); }
      },
    }, h("ul", { class: "bv-opts" }, options, newBlock), conseqEl, warnEl);

    const stepper = h("div", { class: "bv-stepper", role: "group", "aria-label": "Rounds" },
      h("span", { class: "muted" }, "Rounds"),
      h("button", { type: "button", class: "btn btn-icon", "aria-label": "Fewer rounds", disabled: ui.rounds <= 1,
        onclick: () => { ui.rounds = Math.max(1, ui.rounds - 1); focusKeep(drawControls); } }, icon("minus")),
      h("output", { class: "bv-rounds", "aria-live": "polite" }, String(ui.rounds)),
      h("button", { type: "button", class: "btn btn-icon", "aria-label": "More rounds", disabled: ui.rounds >= MAX_ROUNDS,
        onclick: () => { ui.rounds = Math.min(MAX_ROUNDS, ui.rounds + 1); focusKeep(drawControls); } }, icon("plus")));

    allEl = h("p", { class: "bv-all muted small" });
    syncAll();
    const configured = !!state.model?.app?.configured;
    controls.append(
      h("div", { class: "bv-bar" },
        toggle,
        stepper,
        h("span", { class: "bv-bar-actions" },
          h("button", { type: "button", class: "btn btn-primary", "data-build": "all", disabled: true,
            onclick: () => launch("all", "everything", "all") }, icon("play"), "Build everything"),
          h("button", { type: "button", class: "btn", "data-check": "", disabled: !configured || state.checkRunning,
            onclick: () => window.dispatchEvent(new CustomEvent("studio:run-check")) }, icon("check"), state.checkRunning ? "Checking…" : "Check now"))),
      panel,
      why,
      allEl,
    );
    syncBranchText();
  }

  function chip(text, ico) {
    return h("span", { class: "bv-tag" }, icon(ico), h("span", {}, text));
  }

  function syncAll() {
    const a = (cache.areas || []).find((x) => x.id === "all");
    if (!allEl) return;
    allEl.hidden = !a;
    if (!a) return;
    allEl.textContent = a.failing === null || a.failing === undefined
      ? `The whole spec has ${plural(a.checks, "check")}; none has been checked on this branch yet.`
      : `The whole spec: ${a.failing} of ${plural(a.checks, "check")} failing on ${a.target_branch}.`;
  }

  // ---- areas
  function failingSet(area, b) {
    const run = state.buildRun?.record;
    if (run?.baseline && run.scope?.id === area.id && run.target_branch === (area.target_branch || "")) {
      return new Set((run.baseline.findings || []).filter((f) => f.in_scope && f.target).map((f) => f.target));
    }
    const tgt = area.target_branch;
    if (b && tgt && tgt === b.current_branch && state.model?.app?.last_check?.targets) {
      return new Set((area.targets || []).filter((t) => appResult(t) === "fail"));
    }
    return null;
  }

  function areaRow(a) {
    const b = cache.branches;
    const open = ui.expanded.has(a.id);
    const st = AREA_STATUS[a.status] || AREA_STATUS.not_built;
    const detailId = `bv-area-${a.id}`;
    const failingWord = a.failing === null || a.failing === undefined ? "not checked yet"
      : a.failing === 0 ? "none failing" : `${a.failing} failing`;
    const built = a.status === "built";
    const detail = h("div", { class: "bv-detail", id: detailId, hidden: !open });
    if (open) fillDetail(detail, a, b);
    const li = h("li", { class: ["bv-area", ui.selected === a.id && "is-selected"], "data-area": a.id },
      h("div", { class: "bv-area-row" },
        h("button", {
          type: "button", class: "bv-area-toggle", "aria-expanded": open ? "true" : "false", "aria-controls": detailId,
          onclick: (e) => {
            const o = detail.hidden;
            detail.hidden = !o;
            e.currentTarget.setAttribute("aria-expanded", o ? "true" : "false");
            if (o) { ui.expanded.add(a.id); fillDetail(detail, a, cache.branches); } else ui.expanded.delete(a.id);
          },
        }, h("span", { class: "bv-chev", "aria-hidden": "true" }, open ? "▾" : "▸"), h("span", { class: "bv-area-title" }, a.title)),
        h("span", { class: "bv-counts muted" },
          `${plural(a.rules.length, "rule")} · ${plural(a.scenarios, "scenario")}`),
        h("span", { class: ["bv-failing", a.failing > 0 && "is-bad"] }, failingWord),
        h("span", { class: ["bv-status", `is-${a.status}`] },
          st.icon ? icon(st.icon) : h("span", { class: "spinner", "aria-hidden": "true" }), h("span", {}, st.label)),
        h("button", {
          type: "button", class: ["btn", built ? "btn-quiet" : "btn-primary"], "data-build": a.id, disabled: true,
          "aria-label": `${built ? "Build again" : "Build this area"}: ${a.title}`,
          onclick: () => launch({ area: a.id }, a.title, "area"),
        }, icon("play"), built ? "Build again" : "Build this area")),
      detail);
    return li;
  }

  function fillDetail(el, a, b) {
    el.replaceChildren();
    const rules = (state.model?.rules || []).filter((r) => (a.rules || []).includes(r.id));
    el.append(h("h3", { class: "s2-section-title" }, `Rules in this area (${a.rules.length})`));
    if (rules.length) {
      el.append(h("ul", { class: "bv-rules" }, rules.map((r) => {
        const st = RULE_STATUS[r.status] ? r.status : "untraced";
        return h("li", {},
          h("span", { class: "rule-id" }, r.id),
          h("span", { class: "bv-rule-text" }, String(r.text || "").replace(/\s+/g, " ")),
          ruleStatusChip(st));
      })));
    } else {
      el.append(h("p", { class: "muted small" }, "This area has no rules of its own."));
    }
    const failing = failingSet(a, b);
    const targets = a.targets || [];
    if (!targets.length) {
      el.append(h("p", { class: "muted small" }, "No check is linked to the rules of this area yet, so a build here has nothing to prove."));
      return;
    }
    if (failing) {
      const bad = targets.filter((t) => failing.has(t));
      el.append(h("h3", { class: "s2-section-title" }, bad.length ? `Failing checks (${bad.length} of ${targets.length})` : `All ${targets.length} checks pass`));
      if (bad.length) el.append(h("div", { class: "chip-row" }, bad.map((t) => targetChip(t))));
    } else {
      el.append(h("h3", { class: "s2-section-title" }, `Checks (${targets.length})`));
      el.append(h("p", { class: "muted small" }, a.failing === null
        ? "Not checked on this branch yet. Check now, or build: a build runs the checks first and works on the ones that fail."
        : `${a.failing} of them fail on ${a.target_branch}. A build lists exactly which when it starts.`));
      el.append(h("div", { class: "chip-row" }, targets.slice(0, 12).map((t) => targetChip(t)),
        targets.length > 12 ? h("span", { class: "muted small" }, `and ${targets.length - 12} more`) : null));
    }
  }

  function drawAreas() {
    listSlot.replaceChildren();
    const areas = (cache.areas || []).filter((a) => a.id !== "all");
    if (!cache.areas) {
      listSlot.append(h("p", { class: "muted", "aria-busy": "true" }, "Loading the areas…"));
      return;
    }
    listSlot.append(h("h2", { class: "s2-section-title" }, `Areas on ${cache.areas[0]?.target_branch || "the target branch"}`));
    if (!areas.length) {
      listSlot.append(h("p", { class: "s2-empty" }, "The spec has no sections to build by. Use Build everything."));
    } else {
      listSlot.append(h("ul", { class: "bv-list" }, areas.map(areaRow)));
    }
    syncBusy();
    if (ui.selected) {
      const row = listSlot.querySelector(`[data-area="${CSS.escape(ui.selected)}"]`);
      row?.scrollIntoView({ block: "nearest" });
      if (ui.focusSelected) row?.querySelector("[data-build]")?.focus({ preventScroll: true });
      ui.focusSelected = false;
    }
  }

  // ---- the build in view
  function drawRun() {
    const run = state.buildRun;
    runSlot.replaceChildren();
    if (!run) return;
    const hostOpts = {
      onAgain: async (rec) => {
        const sc = rec.scope || {};
        const scope = sc.kind === "all" ? "all" : sc.kind === "rules" ? { rules: sc.rules } : { area: sc.id };
        launch(scope, sc.title || "the spec", sc.kind);
      },
      onChanged: () => {},
      openCode: ctx.openCode,
    };
    if (run.phase === "starting") {
      runSlot.append(h("div", { class: "bv-starting", role: "status" },
        h("p", { class: "bv-starting-line" }, h("span", { class: "spinner", "aria-hidden": "true" }), h("strong", {}, " Checking the current state…")),
        h("p", { class: "muted small" }, `Studio runs the checks once on ${run.branch}, so the build knows what to fix in ${run.title}. This can take a while for a slow check.`),
        h("button", { type: "button", class: "btn", disabled: !run.id || run.stopping, onclick: stopStarting },
          run.stopping ? "Stopping…" : "Stop"),
        !run.id ? h("span", { class: "muted small" }, " Stop is available once the check has started.") : null));
      return;
    }
    if (run.phase === "error" || run.phase === "stopped") {
      runSlot.append(h("div", { class: ["bv-note", run.phase === "error" ? "is-error" : "is-quiet"], role: run.phase === "error" ? "alert" : "status" },
        icon(run.phase === "error" ? "triangle" : "dash"),
        h("p", {}, run.message || "The build did not start."),
        h("button", { type: "button", class: "btn btn-quiet", onclick: () => update({ buildRun: null }) }, "Dismiss")));
      return;
    }
    if (run.record) {
      runSlot.append(h("h2", { class: "s2-section-title" }, "Current build"), rebuildCard(run.record, hostOpts));
    } else {
      runSlot.append(h("p", { class: "muted", "aria-busy": "true" }, "Opening the build…"));
    }
  }

  // ---- history (only where the server can list builds)
  function drawHistory() {
    histSlot.replaceChildren();
    const list = (cache.history || []).filter((r) => r.scope).slice().reverse().slice(0, 8);
    if (!list.length) return;
    histSlot.append(h("h2", { class: "s2-section-title" }, "Recent builds"),
      h("ul", { class: "rb-history" }, list.map((r) => h("li", { class: ["rb-hist-row", r.id === state.buildRun?.id && "is-current"] },
        h("span", { class: "s2-mono" }, r.id),
        h("div", {},
          h("button", { type: "button", class: "s2-quiet-btn", onclick: () => update({ buildRun: { phase: LIVE.has(r.status) ? "live" : "done", id: r.id, record: r, title: r.scope.title, branch: r.target_branch } }) },
            `${r.scope.kind === "all" ? "Everything" : r.scope.title} onto ${r.target_branch || "?"}`),
          h("p", { class: "rb-meta" }, `${plural((r.rounds || []).filter((x) => x.check).length, "round")} · ${r.model}`)),
        h("span", { class: ["rb-status", `is-${r.status}`] }, historyWord(r.status))))));
  }

  const historyWord = (s) => ({ passed: "Passed", gave_up: "Gave up", stopped: "Stopped", baseline: "Checking", running: "Building", queued: "Queued" }[s] || s);

  // ---- loading
  function areasBranchName(b) {
    const sel = selection(b);
    if (!sel) return null;
    if (sel.kind === "new") return sel.from === "HEAD" ? null : sel.from;
    if (sel.kind === "virtual") return null;
    return sel.name;
  }

  async function loadAreas() {
    const b = cache.branches;
    if (!b) return;
    const tok = ++loadToken;
    const name = areasBranchName(b);
    try {
      const a = await api.areas(name);
      if (stale(tok)) return;
      cache.areas = a;
      cache.areasFor = name;
      drawAreas();
      syncAll();
    } catch (e) {
      if (stale(tok)) return;
      listSlot.replaceChildren(h("div", { class: "empty-note", role: "alert" },
        h("p", {}, `The areas could not be loaded: ${e.message}`),
        h("button", { type: "button", class: "btn btn-quiet", onclick: loadAll }, "Try again")));
    }
  }

  async function loadAll() {
    const tok = ++loadToken;
    try {
      const b = await api.branches();
      if (stale(tok)) return;
      cache.branches = b;
      if (ui.branch && ui.branch !== b.default_target && !exists(b, ui.branch)) {
        ui.branch = null; // the remembered branch is gone: back to the default
        remember(null);
      }
      drawControls();
      loadAreas();
      api.rebuildList().then((l) => {
        if (ep !== api.epoch() || !root.isConnected) return;
        cache.history = Array.isArray(l) ? l : [];
        drawHistory();
      }).catch(() => {});
    } catch (e) {
      if (stale(tok)) return;
      listSlot.replaceChildren(h("div", { class: "empty-note", role: "alert" },
        h("p", {}, `Branches and areas could not be loaded: ${e.message}`),
        h("button", { type: "button", class: "btn btn-quiet", onclick: loadAll }, "Try again")));
    }
  }

  // "Build…" links from Rules and Map open this view with an area chosen
  if (state.buildArea) {
    ui.selected = state.buildArea;
    ui.focusSelected = true;
    ui.expanded.add(state.buildArea);
    state.buildArea = null;
  }

  drawControls();
  drawAreas();
  drawRun();
  drawHistory();
  loadAll();

  let lastKey = "";
  const unsub = subscribe((st, patch) => {
    if (!root.isConnected) {
      unsub();
      return;
    }
    if ("checkRunning" in patch) syncBusy();
    if ("buildRun" in patch) {
      drawRun();
      syncBusy();
      const r = st.buildRun;
      const key = `${r?.id || ""}|${r?.phase || ""}|${r?.record?.status || ""}`;
      if (key !== lastKey) {
        const before = lastKey;
        lastKey = key;
        // areas show Building / the new status: reload when a build starts or ends
        if (before && (r?.phase === "live" || r?.phase === "done" || r?.phase === "stopped")) loadAreas();
        if (r?.record && TERMINAL.has(r.record.status)) {
          api.rebuildList().then((l) => { cache.history = Array.isArray(l) ? l : []; drawHistory(); }).catch(() => {});
        }
      }
    }
  });
  lastKey = `${state.buildRun?.id || ""}|${state.buildRun?.phase || ""}|${state.buildRun?.record?.status || ""}`;
}

function unsupported(proj) {
  const caps = proj?.capabilities || {};
  const lacks = [];
  if (caps.rebuild === false) {
    lacks.push("a git repository the project lives in");
    lacks.push("a [rebuild] section in studio.toml with at least one gate command, the checks a build must pass");
  }
  if (caps.build_from_spec === undefined) lacks.push("a Studio server that supports build from the spec (API v1.8 or newer)");
  if (!lacks.length) lacks.push("a git repository and a [rebuild] section in studio.toml with at least one gate command");
  return h("div", { class: "empty-note", role: "status" },
    h("p", {}, "Build from the spec is not available for this project yet. It needs:"),
    h("ul", {}, lacks.map((x) => h("li", {}, x))),
    h("p", { class: "muted small" }, BUILD_WHY));
}

// ------------------------------------------------------------------ links on other views

let areasMemo = null; // {base, at, promise}
function areasCached() {
  const now = Date.now();
  if (areasMemo && areasMemo.base === api.base() && now - areasMemo.at < 10000) return areasMemo.promise;
  const promise = api.areas();
  areasMemo = { base: api.base(), at: now, promise };
  promise.catch(() => { if (areasMemo?.promise === promise) areasMemo = null; });
  return promise;
}

const norm = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const openBuild = (id) => update({ buildArea: id, view: "build" });

function statusChip(a) {
  const st = AREA_STATUS[a.status] || AREA_STATUS.not_built;
  return h("span", { class: ["bv-status", `is-${a.status}`] }, st.icon ? icon(st.icon) : h("span", { class: "spinner", "aria-hidden": "true" }), h("span", {}, st.label));
}

/** Rules view: a status chip and a "Build…" link on the heading of each area. */
export async function decorateRuleHeadings(root) {
  if (!canBuild()) return;
  const ep = api.epoch();
  let areas;
  try {
    areas = await areasCached();
  } catch (e) {
    return;
  }
  if (ep !== api.epoch() || !root.isConnected) return;
  const byTitle = new Map((areas || []).filter((a) => a.id !== "all").map((a) => [norm(a.title), a]));
  for (const hd of root.querySelectorAll(".rules-doc .md-h")) {
    if (hd.querySelector(".bv-heading")) continue;
    const a = byTitle.get(norm(hd.textContent));
    if (!a) continue;
    hd.append(h("span", { class: "bv-heading" },
      statusChip(a),
      h("button", { type: "button", class: "btn-link bv-heading-link", "aria-label": `Build the ${a.title} area`, onclick: () => openBuild(a.id) }, "Build…")));
  }
}

/** Map view: one quiet row of area chips under the heading, each opening the Build view. */
export async function decorateMap(root) {
  if (!canBuild()) return;
  const ep = api.epoch();
  let areas;
  try {
    areas = await areasCached();
  } catch (e) {
    return;
  }
  if (ep !== api.epoch() || !root.isConnected || root.querySelector(".bv-map-areas")) return;
  const list = (areas || []).filter((a) => a.id !== "all");
  if (!list.length) return;
  const head = root.querySelector(".view-head") || root.firstElementChild;
  if (!head) return;
  const row = h("nav", { class: "bv-map-areas", "aria-label": "Areas to build" },
    h("span", { class: "muted small" }, "Build an area:"),
    list.slice(0, 10).map((a) => h("button", { type: "button", class: "bv-map-chip", onclick: () => openBuild(a.id), title: `Build ${a.title}` },
      h("span", {}, a.title), statusChip(a))));
  head.after(row);
}
