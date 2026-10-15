// Evidence > Improve: what the project's builds taught Lattice, as proposals the owner decides.
//   proposals  GET /api/improve (score, class, kind; a proposal that needs the owner has no Draft),
//   applies    GET /api/improve/applies (the live apply records, never the discarded ones),
//   Draft      GET /api/improve/{id}/draft (sections and the studio.toml snippet),
//   Verify     GET /api/improve/{id}/verify[?at=] (did a closed improvement work),
//   Apply      POST /api/improve/{id}/apply, only for a config draft, only when the server was
//              started with --allow-apply, then the record: branch, commit, diff, gates, the
//              six commands to copy, and Discard (POST /api/improve/{id}/discard).
// Lattice never merges, pushes or closes the ledger: merge and close are text to copy.
//
// This module imports util.js, api.js and store.js only, so its pure functions load in node.

import { h, icon, add, clear, plural } from "../util.js";
import { api } from "../api.js";
import { currentProject } from "../store.js";

const POLL_WAIT_S = 30;

// ------------------------------------------------------------------ pure functions

/** Whether "Apply on a branch..." is offered: a config draft, apply enabled on this server, and
 *  no live record for the proposal. `draft` is GET .../draft, `applies` GET /api/improve/applies. */
export function canApply(draft, applies) {
  if (!draft || draft.refused || draft.error) return false;
  if (draft.template !== "config") return false;
  if (!applies || applies.enabled !== true) return false;
  const id = draft.proposal && draft.proposal.id;
  return !(applies.applies || []).some((a) => a.id === id);
}

const REFUSALS = {
  apply_disabled: "Apply is off for this Studio. Start it with --allow-apply to turn it on.",
  bad_origin: "This request did not come from this Studio's own page, so it was refused.",
  json_required: "The request was not sent as JSON, so it was refused.",
  bad_id: "That is not a valid proposal id.",
  bad_body: "The apply request was malformed. Reload the page and try again.",
  no_state: "This project keeps no durable state, so there is nowhere to record an apply.",
  apply_running: "Another apply or discard is running on this Studio. Wait for it to finish, then try again.",
  draft_changed: "The draft changed since you read it. Read it again, then apply.",
};

/** A calm sentence for a refused apply or discard (an Error's `code` from api.js). */
export function applyRefusal(code) {
  return REFUSALS[code] || "The server refused this request.";
}

/** Hex sha256 of a string's UTF-8 bytes (what the server hashes the draft text with). */
export async function sha256Hex(text) {
  const subtle = typeof crypto !== "undefined" && crypto.subtle;
  if (!subtle) throw new Error("This page cannot hash the draft: it needs a secure context such as http://127.0.0.1.");
  const buf = await subtle.digest("SHA-256", new TextEncoder().encode(String(text)));
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The words for a job or record's gates status. */
export function gatesWord(status) {
  return {
    passed: "Gates passed",
    failed: "A gate failed",
    skipped: "Gates skipped",
    none_configured: "No gate is configured",
    running: "Gates running",
    interrupted: "Gates interrupted",
  }[status] || `Gates: ${status || "unknown"}`;
}

// ------------------------------------------------------------------ state

function fresh(base) {
  return {
    base, loaded: false, loading: false, error: null,
    report: null, applies: null,
    sel: null,
    draft: {}, // id -> {loading, doc, error}
    verify: {}, // id -> {at, loading, doc, error}
    job: {}, // id -> {view, error}
    dialog: null, // {kind: "apply" | "discard", id, gates, busy, error}
    polling: {},
    note: null,
  };
}

let S = fresh(null);
let mounted = null; // {root, ctx} of the last render, redrawn when a call returns

function redraw() {
  if (mounted && mounted.root.isConnected) draw(mounted.root);
}

// ------------------------------------------------------------------ loading

async function loadAll() {
  const base = api.base();
  S.loading = true;
  S.error = null;
  try {
    const [report, applies] = await Promise.all([api.improve(), api.improveApplies()]);
    if (S.base !== base) return;
    S.report = report;
    S.applies = applies;
    S.loaded = true;
  } catch (e) {
    if (S.base !== base) return;
    S.error = e.message;
  }
  S.loading = false;
  redraw();
}

async function refreshApplies() {
  const base = api.base();
  try {
    const applies = await api.improveApplies();
    if (S.base === base) S.applies = applies;
  } catch (e) {
    /* the list stays as it was */
  }
  redraw();
}

async function loadDraft(id) {
  const base = api.base();
  S.draft[id] = { loading: true, doc: null, error: null };
  redraw();
  try {
    const doc = await api.improveDraft(id);
    if (S.base === base) S.draft[id] = { loading: false, doc, error: doc.error || null };
  } catch (e) {
    if (S.base === base) S.draft[id] = { loading: false, doc: null, error: e.message };
  }
  redraw();
}

async function loadVerify(id) {
  const base = api.base();
  const at = (S.verify[id] && S.verify[id].at) || "";
  S.verify[id] = { at, loading: true, doc: null, error: null };
  redraw();
  try {
    const doc = await api.improveVerify(id, at.trim());
    if (S.base === base) S.verify[id] = { at, loading: false, doc, error: doc.error || null };
  } catch (e) {
    if (S.base === base) S.verify[id] = { at, loading: false, doc: null, error: e.message };
  }
  redraw();
}

/** Follow the apply job of `id` with the server's own hold (wait=30) until it is done. */
async function pollJob(id) {
  if (S.polling[id]) return;
  const base = api.base();
  const ep = api.epoch();
  S.polling[id] = true;
  try {
    for (;;) {
      const view = await api.improveApply(id, POLL_WAIT_S);
      if (ep !== api.epoch() || S.base !== base) return;
      S.job[id] = { view, error: null };
      redraw();
      if (view.status === "done") break;
    }
  } catch (e) {
    if (ep === api.epoch() && S.base === base) {
      S.job[id] = { view: (S.job[id] && S.job[id].view) || null, error: e.message };
      redraw();
    }
  } finally {
    if (S.base === base) delete S.polling[id];
  }
  await refreshApplies();
}

async function openRecord(id) {
  const base = api.base();
  S.sel = id;
  redraw();
  try {
    const view = await api.improveApply(id, 0);
    if (S.base !== base) return;
    S.job[id] = { view, error: null };
    redraw();
    if (view.status === "running") pollJob(id);
  } catch (e) {
    if (S.base === base) S.job[id] = { view: null, error: e.message };
    redraw();
  }
}

// ------------------------------------------------------------------ writes

async function doApply(id) {
  const dlg = S.dialog;
  const base = api.base();
  const draft = S.draft[id] && S.draft[id].doc;
  if (!dlg || !draft || draft.refused || typeof draft.text !== "string") return;
  dlg.busy = true;
  dlg.error = null;
  redraw();
  try {
    const sha = await sha256Hex(draft.text);
    const view = await api.applyProposal(id, sha, dlg.gates, null);
    if (S.base !== base) return;
    S.dialog = null;
    S.job[id] = { view, error: null };
    S.sel = id;
    redraw();
    pollJob(id);
  } catch (e) {
    if (S.base !== base) return;
    dlg.busy = false;
    dlg.error = e.code ? applyRefusal(e.code) : e.message;
    if (e.code === "draft_changed") loadDraft(id);
    redraw();
  }
}

async function doDiscard(id) {
  const dlg = S.dialog;
  const base = api.base();
  if (!dlg) return;
  dlg.busy = true;
  dlg.error = null;
  redraw();
  try {
    const out = await api.discardApply(id);
    if (S.base !== base) return;
    if (out.exit_code === 0) {
      S.dialog = null;
      delete S.job[id];
      S.note = `Discarded ${id}: its branch and worktree are removed. Nothing was merged.`;
      await refreshApplies();
      return;
    }
    dlg.busy = false;
    const r = out.result || {};
    dlg.error = r.detail || r.error || "The discard could not run.";
    redraw();
  } catch (e) {
    if (S.base !== base) return;
    dlg.busy = false;
    dlg.error = e.code ? applyRefusal(e.code) : e.message;
    redraw();
  }
}

// ------------------------------------------------------------------ drawing

const short = (sha) => String(sha || "").slice(0, 7);

function copyButton(text, label = "Copy") {
  const btn = h("button", { type: "button", class: "btn btn-quiet small", "aria-label": `${label}: ${text}` }, label);
  btn.addEventListener("click", async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch (e) {
      ok = false;
    }
    btn.textContent = ok ? "Copied" : "Select and copy";
    setTimeout(() => { btn.textContent = label; }, 1800);
  });
  return btn;
}

function scoreText(p) {
  if (p.score === null || p.score === undefined) return "no cost recorded";
  return `${p.score}${p.unit ? ` ${p.unit}` : ""}`;
}

function proposalCard(p) {
  const live = ((S.applies && S.applies.applies) || []).some((a) => a.id === p.id);
  const needsOwner = p.status === "needs_owner";
  return h("li", { class: ["imp-card", S.sel === p.id && "is-open"] },
    h("div", { class: "imp-card-head" },
      h("span", { class: "imp-title" }, p.title),
      h("code", { class: "imp-id" }, p.id),
    ),
    h("div", { class: "imp-meta" },
      h("span", { class: "chip" }, `Class ${p.score_class}`),
      h("span", { class: "chip" }, p.kind),
      h("span", { class: "muted" }, `score ${scoreText(p)} · seen ${plural(p.frequency || 0, "time")}`),
      needsOwner ? h("span", { class: "pill pill-none" }, "Needs the owner") : null,
      live ? h("span", { class: "pill pill-running" }, "Applied on a branch") : null,
    ),
    needsOwner
      ? h("p", { class: "muted small" }, "It touches the checker or the spec language, so Lattice never drafts it: you decide.")
      : h("div", { class: "imp-actions" },
          h("button", {
            type: "button", class: "btn", onclick: () => { S.sel = p.id; if (!S.draft[p.id]) loadDraft(p.id); else redraw(); },
          }, icon("text-lines"), "Draft"),
          h("button", {
            type: "button", class: "btn btn-quiet", onclick: () => { S.sel = p.id; redraw(); },
          }, "Verify"),
        ),
  );
}

function sectionBlock(sec) {
  return h("section", { class: "imp-sec" },
    h("h4", {}, sec.heading),
    h("pre", { class: "imp-pre" }, sec.body),
  );
}

function draftPanel(id) {
  const d = S.draft[id];
  const box = h("section", { class: "imp-panel", "aria-label": `Draft of ${id}` }, h("h3", {}, `Draft of ${id}`));
  if (!d) {
    add(box, h("p", { class: "muted" }, "Nothing has been drafted yet."),
      h("button", { type: "button", class: "btn", onclick: () => loadDraft(id) }, "Draft it"));
    return box;
  }
  if (d.loading) return add(box, h("p", { class: "muted", role: "status" }, "Reading the draft…"));
  if (d.error) return add(box, h("p", { class: "imp-err", role: "alert" }, d.error));
  const doc = d.doc;
  if (doc.refused) {
    return add(box, h("p", { class: "imp-err", role: "status" },
      `Not drafted (${doc.reason}): ${doc.detail}`));
  }
  add(box,
    h("div", { class: "imp-meta" },
      h("span", { class: "chip" }, `Template: ${doc.template}`),
      h("span", { class: "muted" }, `as of ${doc.as_of}`),
      copyButton(doc.text, "Copy the draft"),
    ),
    doc.sections.map(sectionBlock),
  );
  if (doc.toml) {
    add(box, h("section", { class: "imp-sec" },
      h("h4", {}, "studio.toml snippet"),
      h("pre", { class: "imp-pre" }, doc.toml),
      copyButton(doc.toml, "Copy the snippet"),
    ));
  }
  return box;
}

function verifyPanel(id) {
  const v = S.verify[id] || { at: "", loading: false, doc: null, error: null };
  const box = h("section", { class: "imp-panel", "aria-label": `Verify ${id}` },
    h("h3", {}, `Verify ${id}`),
    h("p", { class: "muted" }, "Did a closed improvement work? Compares the log before and after the close; read only."),
  );
  const inputId = `imp-at-${id}`;
  add(box, h("div", { class: "imp-row" },
    h("label", { for: inputId }, "Took effect at (optional)"),
    h("input", {
      id: inputId, type: "text", class: "imp-input", value: v.at, placeholder: "YYYY-MM-DDTHH:MM:SSZ",
      oninput: (e) => { S.verify[id] = { ...v, at: e.target.value }; },
    }),
    h("button", { type: "button", class: "btn", disabled: v.loading, onclick: () => loadVerify(id) }, v.loading ? "Verifying…" : "Verify"),
  ));
  if (v.error) return add(box, h("p", { class: "imp-err", role: "alert" }, v.error));
  const doc = v.doc;
  if (!doc) return box;
  if (doc.refused) return add(box, h("p", { class: "imp-err", role: "status" }, `Not verified (${doc.reason}): ${doc.detail}`));
  const stat = (w) => (w && w.n !== undefined
    ? `${w.n} seen, frequency ${w.frequency ?? "n/a"}${w.cost_per !== null && w.cost_per !== undefined ? `, mean cost ${w.cost_per}` : ""}`
    : "n/a");
  add(box,
    h("p", {}, h("span", { class: ["pill", doc.verdict === "improved" ? "pill-pass" : "pill-none"] }, doc.verdict), " ", doc.reason),
    h("ul", { class: "imp-facts" },
      h("li", {}, `Before the close: ${stat(doc.before)}`),
      h("li", {}, `After the close: ${stat(doc.after)}`),
      doc.change && doc.change.delta !== null && doc.change.delta !== undefined
        ? h("li", {}, `Change: ${doc.change.delta} (${doc.direction || "n/a"})`) : null,
    ),
  );
  return box;
}

function diffBlock(text) {
  const pre = h("pre", { class: "imp-pre imp-diff", tabindex: "0", "aria-label": "The diff" });
  for (const line of String(text || "").split("\n")) {
    const kind = line.startsWith("+") && !line.startsWith("+++") ? "imp-add"
      : line.startsWith("-") && !line.startsWith("---") ? "imp-del" : line.startsWith("@@") ? "imp-hunk" : "";
    add(pre, h("span", { class: ["imp-line", kind] }, `${line}\n`));
  }
  return pre;
}

function recordPanel(id, rec, job) {
  const box = h("section", { class: "imp-panel imp-record", "aria-label": `Apply of ${id}` });
  add(box,
    h("div", { class: "imp-banner", role: "status" },
      h("strong", {}, "NOT MERGED"),
      h("span", {}, ` Branch ${rec.branch} holds commit ${short(rec.commit && rec.commit.sha)}, based on ${rec.base && rec.base.branch} ${short(rec.base && rec.base.sha)}. Lattice never merges, pushes or closes the ledger for you.`),
    ),
    h("p", {}, h("span", { class: ["pill", rec.gates.status === "passed" || rec.gates.status === "skipped" ? "pill-pass"
      : rec.gates.status === "running" ? "pill-running" : "pill-fail"] }, gatesWord(rec.gates.status))),
  );
  if (job && job.stale) {
    add(box, h("p", { class: "imp-err", role: "alert" },
      "The record says the gates were still running when Studio last saw it (a restart or a crash). Discard it, or run the gates yourself."));
  }
  add(box,
    h("section", { class: "imp-sec" },
      h("h4", {}, "The edit"),
      h("p", {}, `${rec.edit.file}, line ${rec.edit.line_no} (${rec.edit.action}): `, h("code", {}, rec.edit.line)),
      diffBlock(rec.edit.diff),
    ),
    h("section", { class: "imp-sec" },
      h("h4", {}, "Gates"),
      rec.gates.results.length
        ? h("ul", { class: "imp-gates" }, rec.gates.results.map((r) => h("li", {},
            h("details", {},
              h("summary", {}, h("code", {}, r.command), ` — ${r.timed_out ? "timed out" : r.passed ? "passed" : `failed (exit ${r.exit_code})`} in ${r.seconds}s`),
              h("pre", { class: "imp-pre" }, r.output_tail || "(no output)"),
            ))))
        : h("p", { class: "muted" }, rec.gates.status === "running" ? "The gates are running…" : "No gate ran."),
      rec.gates.untracked_after && rec.gates.untracked_after.length
        ? h("p", { class: "muted small" }, `Untracked after the gates: ${rec.gates.untracked_after.join(", ")}`) : null,
    ),
  );
  if ((rec.warnings || []).length) {
    add(box, h("ul", { class: "imp-warn" }, rec.warnings.map((w) => h("li", {}, w))));
  }
  add(box, h("section", { class: "imp-sec" },
    h("h4", {}, "What to do next"),
    h("ol", { class: "imp-next" }, (rec.next || []).map((s) => h("li", {},
      h("span", { class: "imp-step" }, s.step), " ", h("code", { class: "imp-cmd" }, s.command), copyButton(s.command)))),
  ));
  add(box, h("div", { class: "imp-actions" },
    h("button", {
      type: "button", class: "btn",
      onclick: () => { S.dialog = { kind: "discard", id, busy: false, error: null }; redraw(); },
    }, "Discard…"),
  ));
  return box;
}

function applyPanel(id) {
  const box = h("section", { class: "imp-panel", "aria-label": `Apply ${id}` }, h("h3", {}, "Apply on a branch"));
  const applies = S.applies;
  const j = S.job[id];
  const view = j && j.view;
  const live = ((applies && applies.applies) || []).some((a) => a.id === id);
  if (j && j.error) add(box, h("p", { class: "imp-err", role: "alert" }, j.error));
  if (view) {
    if (view.status === "running") {
      add(box, h("p", { class: "muted", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }), " Applying… the gates can take minutes."));
    } else if (view.result && view.result.refused) {
      add(box, h("p", { class: "imp-err", role: "status" }, `Not applied (${view.result.reason}): ${view.result.detail}`));
    } else if (view.result && view.result.error) {
      add(box, h("p", { class: "imp-err", role: "alert" }, `Could not apply: ${view.result.error}`));
    }
    if (view.record) add(box, recordPanel(id, view.record, view));
    return box;
  }
  if (live) {
    return add(box, h("p", { class: "muted" }, "This proposal has an apply record."),
      h("button", { type: "button", class: "btn", onclick: () => openRecord(id) }, "Open the record"));
  }
  const draft = S.draft[id] && S.draft[id].doc;
  if (!applies) return add(box, h("p", { class: "muted" }, "Loading…"));
  if (applies.enabled !== true) {
    const root = (currentProject() && currentProject().root) || ".";
    return add(box,
      h("p", { id: "imp-apply-off" }, "Apply is off for this Studio (start it with --allow-apply)."),
      h("p", { class: "muted small" }, applies.why_disabled || ""),
      h("p", { class: "muted small" }, "Or on the command line: ",
        h("code", {}, `lattice improve apply ${id} --project ${root}`)),
    );
  }
  if (!draft) return add(box, h("p", { class: "muted" }, "Read the draft first: the apply is of exactly that draft."));
  if (draft.refused) return add(box, h("p", { class: "muted" }, "This proposal is not drafted, so there is nothing to apply."));
  if (draft.template !== "config") {
    return add(box, h("p", { class: "muted" },
      `This draft is a ${draft.template}, not a literal [rebuild] setting. Only the owner applies it.`));
  }
  add(box,
    h("p", { class: "muted" }, "Commits the one line of the draft to studio.toml on a branch of its own, in a worktree beside your checkout."),
    h("button", {
      type: "button", class: "btn btn-primary", disabled: !canApply(draft, applies),
      onclick: () => { S.dialog = { kind: "apply", id, gates: true, busy: false, error: null }; redraw(); },
    }, "Apply on a branch…"),
  );
  return box;
}

function dialogView() {
  const d = S.dialog;
  if (!d) return null;
  const draft = S.draft[d.id] && S.draft[d.id].doc;
  const titleId = "imp-dialog-title";
  const close = () => { S.dialog = null; redraw(); };
  const body = [];
  const buttons = [];
  if (d.kind === "apply") {
    body.push(
      h("p", {}, "This commits one line to studio.toml:"),
      h("pre", { class: "imp-pre" }, draft ? draft.toml : ""),
      h("p", {}, "on a new branch ", h("code", {}, `improve/${d.id}`), " in a worktree of its own. Your checkout is not touched."),
      h("label", { class: "imp-check" },
        h("input", { type: "checkbox", checked: d.gates, onchange: (e) => { d.gates = e.target.checked; } }),
        " Run the gates (can take minutes)"),
      h("p", { class: "muted" }, "Lattice never merges, pushes or closes the ledger."),
    );
    buttons.push(h("button", { type: "button", class: "btn btn-primary", disabled: d.busy, onclick: () => doApply(d.id) },
      d.busy ? "Applying…" : "Apply on a branch"));
  } else {
    body.push(
      h("p", {}, "This removes the worktree and the branch ", h("code", {}, `improve/${d.id}`), " and moves its record aside. Nothing was merged."),
    );
    buttons.push(h("button", { type: "button", class: "btn btn-primary", disabled: d.busy, onclick: () => doDiscard(d.id) },
      d.busy ? "Discarding…" : "Discard"));
  }
  buttons.push(h("button", { type: "button", class: "btn", disabled: d.busy, onclick: close }, "Cancel"));
  const dialog = h("div", { class: "imp-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h("h2", { id: titleId }, d.kind === "apply" ? "Apply on a branch?" : "Discard this apply?"),
    body,
    d.error ? h("p", { class: "imp-err", role: "alert" }, d.error) : null,
    h("div", { class: "imp-actions" }, buttons),
  );
  dialog.addEventListener("keydown", (e) => { if (e.key === "Escape" && !d.busy) close(); });
  if (!d.focused) {
    d.focused = true; // focus the first control once, not on every redraw
    setTimeout(() => { const f = dialog.querySelector("input, button"); if (f && dialog.isConnected) f.focus(); }, 0);
  }
  return h("div", { class: "imp-scrim" }, dialog);
}

function appliesList() {
  const a = S.applies;
  const rows = (a && a.applies) || [];
  const box = h("section", { class: "imp-section", "aria-label": "Applied on branches" }, h("h2", {}, "Applied on branches"));
  if (a && a.enabled === false) {
    add(box, h("p", { class: "muted" }, "Apply is off for this Studio (start it with --allow-apply)."));
  }
  if (a && a.running) add(box, h("p", { class: "muted", role: "status" }, `Applying ${a.running.id}…`));
  if (!rows.length) {
    add(box, h("p", { class: "s2-empty" }, "Nothing is applied on a branch."));
  } else {
    add(box, h("ul", { class: "imp-list" }, rows.map((r) => h("li", { class: "imp-card" },
      h("div", { class: "imp-card-head" }, h("span", { class: "imp-title" }, r.title), h("code", { class: "imp-id" }, r.id)),
      h("div", { class: "imp-meta" },
        h("code", {}, r.branch),
        h("span", { class: "muted" }, `commit ${short(r.commit)} · ${gatesWord(r.gates)}${r.stale ? " (stale)" : ""}`),
        h("span", { class: "pill pill-none" }, "NOT MERGED"),
      ),
      h("div", { class: "imp-actions" },
        h("button", { type: "button", class: "btn", onclick: () => openRecord(r.id) }, "Open the record")),
    ))));
  }
  if (a && a.problems && a.problems.length) {
    add(box, h("ul", { class: "imp-warn" }, a.problems.map((p) => h("li", {}, `${p.file}: ${p.detail}`))));
  }
  return box;
}

function draw(root) {
  clear(root);
  add(root, h("header", { class: "view-head" },
    h("h1", {}, "Improve"),
    h("p", { class: "lede" }, "What this project's builds taught Lattice, as proposals. You decide each one: nothing is applied or merged unless you say so, and merging is always yours."),
  ));
  if (S.error) {
    add(root, h("p", { class: "imp-err", role: "alert" }, S.error),
      h("button", { type: "button", class: "btn", onclick: loadAll }, "Try again"));
    return;
  }
  if (!S.loaded) {
    add(root, h("p", { class: "muted", role: "status" }, "Reading the proposals…"));
    return;
  }
  const props = S.report.proposals || [];
  if (S.note) add(root, h("p", { class: "imp-note", role: "status" }, S.note));
  add(root, h("section", { class: "imp-section", "aria-label": "Proposals" },
    h("h2", {}, "Proposals"),
    h("p", { class: "muted small" }, `${plural(S.report.events || 0, "event")} in the log. ${S.report.method || ""}`),
    props.length
      ? h("ul", { class: "imp-list" }, props.map(proposalCard))
      : h("p", { class: "s2-empty" }, "No proposal yet: it needs builds that logged their gates and give-ups."),
  ));
  add(root, appliesList());
  if (S.sel) {
    add(root, h("section", { class: "imp-section imp-detail", "aria-label": `Detail of ${S.sel}` },
      h("h2", {}, S.sel),
      draftPanel(S.sel),
      verifyPanel(S.sel),
      applyPanel(S.sel),
    ));
  }
  const dlg = dialogView();
  if (dlg) add(root, dlg);
}

export function render(model, root, ctx) {
  if (S.base !== api.base()) S = fresh(api.base());
  mounted = { root, ctx };
  draw(root);
  if (!S.loaded && !S.loading) loadAll();
  // a running job whose page was left: follow it again
  for (const [id, j] of Object.entries(S.job)) {
    if (j.view && j.view.status === "running") pollJob(id);
  }
}
