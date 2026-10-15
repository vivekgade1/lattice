// Evidence > Contracts: this project's place in the workspace and what its contracts say.
//   links       from GET /api/workspace (already loaded by the shell): as consumer, as provider,
//   Check       GET /api/contracts?consumer=NAME, only when the owner asks (it spawns export
//               subprocesses): status, findings, gaps, pinned against current,
//   Preview     GET /api/contracts/impact (a provider's candidate version, advisory only),
//   questions   GET /api/linked-questions (+ Resolve and Dismiss with a note),
//   deliveries  GET /api/linked-notifications.
// Listed in the navigator only when the workspace manifest names a workspace. Every call goes
// through api.js; nothing here writes outside the owner's own project's notes.

import { h, icon, add, clear, plural } from "../util.js";
import { api } from "../api.js";
import { state, currentProject } from "../store.js";

// ------------------------------------------------------------------ the project in its manifest

/** The current hub project's manifest entry (workspace.projects[] with hub_name), or null. */
export function manifestEntry(ws = state.workspace, project = currentProject()) {
  if (!ws || !project) return null;
  return (ws.projects || []).find((p) => p.hub_name === project.name) || null;
}

// "N open" for the navigator: one `?status=open` read per project load (loadAttention).
let attention = { base: null, open: null };
const listeners = [];

/** `fn` is called when the number of open questions was (re)read: the navigator redraws. */
export function onAttention(fn) {
  listeners.push(fn);
}

/** The number of open linked questions of this project (null: not read, or no manifest entry). */
export function openCount() {
  return attention.base === api.base() ? attention.open : null;
}

/** One read of /api/linked-questions?status=open for this project; `onChange` redraws the nav. */
export async function loadAttention(ep, onChange = () => {}) {
  const base = api.base();
  attention = { base, open: null };
  if (!manifestEntry()) return;
  try {
    const r = await api.linkedQuestions("open");
    if (ep !== api.epoch()) return;
    attention = { base, open: (r.counts && r.counts.open) || 0 };
    onChange();
    listeners.forEach((fn) => fn());
  } catch (e) {
    /* the navigator simply shows no number */
  }
}

// ------------------------------------------------------------------ state

function fresh(base) {
  return {
    base,
    check: null, // {loading, doc, error}
    impact: null,
    candidate: "HEAD",
    questions: null, // {loading, doc, error}
    status: "open",
    notes: {}, // question id -> note text being typed
    busy: {}, // question id -> true while a clear is in flight
    clearError: {},
    notifications: null,
  };
}

let C = fresh(null);
let mounted = null;

function redraw() {
  if (mounted && mounted.root.isConnected) draw(mounted.root);
}

async function run(slot, fn) {
  const base = api.base();
  C[slot] = { loading: true, doc: null, error: null };
  redraw();
  try {
    const doc = await fn();
    if (C.base === base) C[slot] = { loading: false, doc, error: null };
  } catch (e) {
    if (C.base === base) C[slot] = { loading: false, doc: null, error: e.message };
  }
  redraw();
}

const loadQuestions = () => run("questions", () => api.linkedQuestions(C.status));
const loadNotifications = () => run("notifications", () => api.linkedNotifications());

async function clearQuestion(id, verb) {
  const base = api.base();
  C.busy[id] = true;
  delete C.clearError[id];
  redraw();
  try {
    await api.linkedQuestionClear(id, verb, (C.notes[id] || "").trim());
    if (C.base !== base) return;
    delete C.notes[id];
  } catch (e) {
    if (C.base === base) C.clearError[id] = e.message;
  }
  if (C.base !== base) return;
  delete C.busy[id];
  await loadQuestions();
  // the navigator's count follows: one more read of the open ones
  loadAttention(api.epoch());
}

// ------------------------------------------------------------------ drawing

const short = (sha) => String(sha || "").slice(0, 12);

function findingLine(f) {
  const what = [f.endpoint, f.kind, f.item].filter(Boolean).join(" · ");
  return `${f.class || ""}${f.relied ? "*" : ""} ${what}${f.detail ? `: ${f.detail}` : ""}`.trim();
}

function versionLine(v) {
  if (!v) return "n/a";
  return `${v.spec_ref || "spec"} @ ${short(v.spec_hash)}${v.dirty ? " (dirty)" : ""}`;
}

function statusPill(status) {
  const tone = status === "ok" || status === "unaffected" ? "pill-pass"
    : status === "broken" || status === "contradiction" || status === "regression" || status === "error" ? "pill-fail" : "pill-none";
  return h("span", { class: ["pill", tone] }, status);
}

function problemList(problems) {
  if (!problems || !problems.length) return null;
  return h("ul", { class: "ctr-problems" }, problems.map((p) => h("li", {},
    h("span", { class: "chip" }, p.severity || "warning"), " ", h("code", {}, p.code), ` ${p.message || ""}`.trimEnd())));
}

function linkRows(rows) {
  if (!rows.length) return h("p", { class: "muted" }, "None.");
  return h("ul", { class: "ctr-links" }, rows.map((lk) => h("li", {},
    h("code", {}, `${lk.from} → ${lk.to}`), " ", h("span", { class: "chip" }, lk.kind),
    lk.contract ? h("span", { class: "chip" }, `contract: ${lk.contract}`) : null,
    lk.note ? h("span", { class: "muted" }, ` ${lk.note}`) : null,
  )));
}

function checkPanel(name) {
  const c = C.check;
  const box = h("section", { class: "ctr-section", "aria-label": "Check contracts" },
    h("h2", {}, "Check contracts"),
    h("p", { class: "muted" }, "Re-reads each opted-in link's pin and the provider's spec. It starts export processes, so it runs only when you ask."),
    h("button", {
      type: "button", class: "btn btn-primary", disabled: !!(c && c.loading),
      onclick: () => run("check", () => api.contracts(name)),
    }, icon("play"), c && c.loading ? "Checking…" : "Check contracts"),
  );
  if (!c || c.loading) return box;
  if (c.error) return add(box, h("p", { class: "imp-err", role: "alert" }, c.error));
  const doc = c.doc;
  add(box, h("p", { role: "status" }, `${plural(doc.checked || 0, "link")} checked for ${doc.consumer || "every consumer"}.`),
    problemList(doc.problems));
  for (const lk of doc.links || []) {
    add(box, h("article", { class: "ctr-link" },
      h("h3", {}, `${lk.consumer} → ${lk.provider} `, statusPill(lk.status), h("span", { class: "chip" }, lk.contract)),
      lk.error ? h("p", { class: "imp-err" }, lk.error) : null,
      h("p", { class: "muted" }, `${plural(lk.imports || 0, "import")} · pinned ${versionLine(lk.pinned)} · current ${versionLine(lk.current)}`),
      (lk.findings || []).length
        ? h("ul", { class: "ctr-findings" }, lk.findings.map((f) => h("li", {}, findingLine(f)))) : null,
      (lk.gaps || []).length
        ? h("details", {}, h("summary", {}, plural(lk.gaps.length, "gap")),
            h("ul", { class: "ctr-findings" }, lk.gaps.map((f) => h("li", {}, findingLine(f))))) : null,
    ));
  }
  return box;
}

function impactPanel(name) {
  const c = C.impact;
  const box = h("section", { class: "ctr-section", "aria-label": "Preview impact" },
    h("h2", {}, "Preview impact"),
    h("p", { class: "muted" }, "What a candidate version of this provider would do to its consumers' pins. Advisory: nothing is changed anywhere."),
    h("div", { class: "imp-row" },
      h("label", { for: "ctr-candidate" }, "Candidate (a ref or a directory)"),
      h("input", {
        id: "ctr-candidate", type: "text", class: "imp-input", value: C.candidate,
        oninput: (e) => { C.candidate = e.target.value; },
      }),
      h("button", {
        type: "button", class: "btn", disabled: !!(c && c.loading),
        onclick: () => run("impact", () => api.contractsImpact(name, C.candidate.trim() || "HEAD")),
      }, c && c.loading ? "Previewing…" : "Preview impact"),
    ),
  );
  if (!c || c.loading) return box;
  if (c.error) return add(box, h("p", { class: "imp-err", role: "alert" }, c.error));
  const doc = c.doc;
  const k = doc.counts || {};
  add(box,
    h("p", { class: "imp-note", role: "status" }, "advisory: this is a preview of a candidate, not a verdict on anything that was accepted."),
    h("p", {}, `${plural(doc.checked || 0, "consumer")}: ${k.contradiction || 0} contradiction, ${k.regression || 0} regression, ${k.unaffected || 0} unaffected, ${k.error || 0} error.`),
    problemList(doc.problems),
  );
  for (const r of doc.consumers || []) {
    add(box, h("article", { class: "ctr-link" },
      h("h3", {}, `${r.consumer} `, statusPill(r.label), h("span", { class: "chip" }, r.contract)),
      r.error ? h("p", { class: "imp-err" }, r.error) : null,
      (r.findings || []).length
        ? h("ul", { class: "ctr-findings" }, r.findings.map((f) => h("li", {}, findingLine(f)))) : null,
    ));
  }
  return box;
}

function questionCard(q) {
  const open = q.status === "open";
  const note = C.notes[q.id] || "";
  const busy = !!C.busy[q.id];
  const noteId = `ctr-note-${q.id}`;
  return h("li", { class: "imp-card" },
    h("div", { class: "imp-card-head" },
      h("code", { class: "imp-id" }, q.id),
      h("span", { class: ["pill", open ? "pill-fail" : "pill-none"] }, q.status),
      statusPill(q.label),
    ),
    h("p", {}, q.text),
    (q.findings || []).length
      ? h("ul", { class: "ctr-findings" }, q.findings.map((f) => h("li", {}, findingLine(f)))) : null,
    q.suggestion ? h("p", { class: "muted small" }, q.suggestion) : null,
    q.resolution && q.resolution.note ? h("p", { class: "muted small" }, `Note: ${q.resolution.note}`) : null,
    open
      ? h("div", { class: "imp-row" },
          h("label", { for: noteId }, "Note (optional)"),
          h("input", { id: noteId, type: "text", class: "imp-input", value: note, oninput: (e) => { C.notes[q.id] = e.target.value; } }),
          h("button", { type: "button", class: "btn", disabled: busy, onclick: () => clearQuestion(q.id, "resolve") }, "Resolve"),
          h("button", { type: "button", class: "btn btn-quiet", disabled: busy, onclick: () => clearQuestion(q.id, "dismiss") }, "Dismiss"),
        )
      : null,
    C.clearError[q.id] ? h("p", { class: "imp-err", role: "alert" }, C.clearError[q.id]) : null,
  );
}

function questionsPanel() {
  const c = C.questions;
  const box = h("section", { class: "ctr-section", "aria-label": "Linked questions" },
    h("h2", {}, "Linked questions"),
    h("p", { class: "muted" }, "Notes other projects left here when their accepted contract broke this project's pins. Advisory: clearing one changes nothing else."),
    h("div", { class: "seg", role: "group", "aria-label": "Status" },
      ["open", "resolved", "dismissed", "all"].map((s) => h("button", {
        type: "button", class: "seg-btn", "aria-pressed": C.status === s ? "true" : "false",
        onclick: () => { C.status = s; loadQuestions(); },
      }, s))),
  );
  if (!c || c.loading) return add(box, h("p", { class: "muted", role: "status" }, "Reading the notes…"));
  if (c.error) return add(box, h("p", { class: "imp-err", role: "alert" }, c.error));
  const doc = c.doc;
  const k = doc.counts || {};
  add(box, h("p", { class: "muted small" }, `${k.open || 0} open, ${k.resolved || 0} resolved, ${k.dismissed || 0} dismissed.`));
  if (doc.legend) {
    add(box, h("dl", { class: "ctr-legend" }, Object.entries(doc.legend).flatMap(([mark, text]) => [h("dt", {}, h("code", {}, mark)), h("dd", {}, text)])));
  }
  if (!(doc.questions || []).length) add(box, h("p", { class: "s2-empty" }, "No note here."));
  else add(box, h("ul", { class: "imp-list" }, doc.questions.map(questionCard)));
  return box;
}

function notificationsPanel() {
  const c = C.notifications;
  const box = h("section", { class: "ctr-section", "aria-label": "Notifications this project sent" },
    h("h2", {}, "What this project's accepts delivered"));
  if (!c || c.loading) return add(box, h("p", { class: "muted", role: "status" }, "Reading…"));
  if (c.error) return add(box, h("p", { class: "imp-err", role: "alert" }, c.error));
  const rows = c.doc.notifications || [];
  if (!rows.length) return add(box, h("p", { class: "s2-empty" }, "No accept has notified anyone since the server started."));
  add(box, h("ul", { class: "imp-list" }, rows.map((n) => h("li", { class: "imp-card" },
    h("div", { class: "imp-card-head" }, h("code", { class: "imp-id" }, n.change), h("span", { class: "pill pill-none" }, n.status)),
    n.reason ? h("p", { class: "muted" }, n.reason) : null,
    n.error ? h("p", { class: "imp-err" }, n.error) : null,
    ((n.result && n.result.consumers) || []).map((cn) => h("p", { class: "muted small" },
      `${cn.consumer}: ${cn.action}${cn.question ? ` (${cn.question})` : ""}${cn.warning ? ` — ${cn.warning}` : ""}`)),
  ))));
  return box;
}

function draw(root) {
  clear(root);
  add(root, h("header", { class: "view-head" },
    h("h1", {}, "Contracts"),
    h("p", { class: "lede" }, "Where this project stands against the workspace's other projects: what it relies on, what relies on it, and what changed under it. Advisory: Lattice never changes another project."),
  ));
  const ws = state.workspace;
  if (!ws) {
    add(root, h("p", { class: "s2-empty" }, "No workspace manifest is in force for this Studio."));
    return;
  }
  const me = manifestEntry(ws);
  if (!me) {
    add(root, h("p", { class: "s2-empty" }, `This project is not in the manifest of workspace ${ws.workspace && ws.workspace.name}.`));
    return;
  }
  const links = ws.links || [];
  const asConsumer = links.filter((l) => l.from === me.name);
  const asProvider = links.filter((l) => l.to === me.name);
  add(root,
    h("p", {}, h("strong", {}, me.name), me.role ? ` · ${me.role}` : "", ` in workspace ${ws.workspace.name}`),
    h("section", { class: "ctr-section", "aria-label": "Links" },
      h("h2", {}, "Links"),
      h("h3", {}, "This project uses"), linkRows(asConsumer),
      h("h3", {}, "Used by"), linkRows(asProvider),
    ),
  );
  add(root, checkPanel(me.name));
  if (asProvider.length) add(root, impactPanel(me.name));
  add(root, questionsPanel(), notificationsPanel());
}

export function render(model, root, ctx) {
  if (C.base !== api.base()) C = fresh(api.base());
  mounted = { root, ctx };
  draw(root);
  if (state.workspace && manifestEntry()) {
    if (!C.questions) loadQuestions();
    if (!C.notifications) loadNotifications();
  }
}
