// The chat panel: Ask questions about the spec, or request a Change that comes back as
// a checked change set. Mirrors the Figma rule editor (5.9): the conversation on the
// left of the decision, the drafted change and the checker's verdict with it.

import { h, icon, clear, plural, add } from "./util.js";
import { api, sleep } from "./api.js";
import { state, update, subscribe } from "./store.js";
import { highlight, targetChip, labelOf, toggleSelection } from "./targets.js";
import { answerBadge, IMPACT_LABEL } from "./badges.js";
import { unifiedDiff } from "./diff.js";

const POLL_MS = 700;
const POLL_LIMIT = 300; // about 3.5 minutes

let mode = "ask";
let els = {};
let onAccepted = () => {};

export function mountChat(root, opts = {}) {
  onAccepted = opts.onAccepted || onAccepted;
  clear(root);
  const thread = h("ol", { class: "thread", "aria-live": "polite", "aria-label": "Conversation" });
  const selRow = h("div", { class: "sel-row", "aria-label": "Selected items" });
  const input = h("textarea", {
    id: "chat-input",
    rows: "3",
    placeholder: "",
    onkeydown: (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        send();
      }
    },
  });
  const verify = h("input", { type: "checkbox", id: "chat-verify" });
  const verifyLabel = h("label", { class: "toggle", for: "chat-verify" }, verify, h("span", {}, "Verify on the app"));
  const verifyNote = h("span", { class: "muted small", id: "verify-note" });
  const sendBtn = h("button", { type: "submit", class: "btn btn-primary" }, "Ask");
  const modeAsk = h("button", { type: "button", class: "seg-btn", "aria-pressed": "true", onclick: () => setMode("ask") }, "Ask");
  const modeChange = h("button", { type: "button", class: "seg-btn", "aria-pressed": "false", onclick: () => setMode("change") }, "Change");
  const inputLabel = h("label", { for: "chat-input", class: "sr-only" }, "Question");

  const form = h("form", { class: "composer", onsubmit: (e) => {
    e.preventDefault();
    send();
  } },
    selRow,
    inputLabel,
    input,
    h("div", { class: "composer-row" },
      h("div", { class: "verify-wrap" }, verifyLabel, verifyNote),
      sendBtn,
    ),
    h("p", { class: "muted small hint" }, "Click anything on the views to add it to the question. ⌘/Ctrl+Enter sends."),
  );

  add(root, 
    h("div", { class: "chat-head" },
      h("h2", { id: "chat-title" }, "Ask or change the spec"),
      h("button", { type: "button", class: "btn btn-icon chat-close", "aria-label": "Close chat", onclick: () => document.body.classList.remove("chat-open") }, icon("close")),
    ),
    h("div", { class: "seg seg-wide", role: "group", "aria-label": "Mode" }, modeAsk, modeChange),
    h("div", { class: "thread-wrap" }, thread, emptyState()),
    form,
  );
  els = { root, thread, selRow, input, verify, verifyLabel, verifyNote, sendBtn, modeAsk, modeChange, inputLabel };
  setMode("ask");
  renderSelection();
  subscribe((st, patch) => {
    if ("selection" in patch) renderSelection();
    if ("model" in patch) syncVerify();
  });
  syncVerify();
}

function emptyState() {
  return h("div", { class: "thread-empty" },
    h("p", {}, h("strong", {}, "Ask"), " a question in plain words, e.g. “Can eve cancel a run?” The answer cites the rules and cells it rests on."),
    h("p", {}, h("strong", {}, "Change"), " asks for an edit, e.g. “only app admins may cancel runs”. It comes back as a checked change set you accept or reject."),
  );
}

function syncVerify() {
  const configured = !!state.model?.app?.configured;
  els.verify.disabled = !configured || mode !== "ask";
  if (!configured) els.verify.checked = false;
  els.verifyNote.textContent = !configured ? "No app is hooked up (studio.toml)." : "";
  els.verifyLabel.hidden = mode !== "ask";
  els.verifyNote.hidden = mode !== "ask";
}

function setMode(m) {
  mode = m;
  els.modeAsk.setAttribute("aria-pressed", m === "ask" ? "true" : "false");
  els.modeChange.setAttribute("aria-pressed", m === "change" ? "true" : "false");
  els.sendBtn.textContent = m === "ask" ? "Ask" : "Request change";
  els.input.placeholder = m === "ask" ? "Ask about the spec…" : "Describe the change, e.g. only app admins may cancel runs";
  els.inputLabel.textContent = m === "ask" ? "Question" : "Change request";
  syncVerify();
}

function renderSelection() {
  const sel = state.selection;
  els.selRow.replaceChildren();
  if (!sel.length) return;
  add(els.selRow, 
    h("span", { class: "muted small" }, `About ${plural(sel.length, "item")}:`),
    sel.map((t) =>
      h("span", { class: "chip chip-sel" },
        h("button", { type: "button", class: "chip-sel-name", title: `Show ${t}`, onclick: () => highlight([t]) }, labelOf(t)),
        h("button", { type: "button", class: "chip-x", "aria-label": `Remove ${labelOf(t)} from the selection`, onclick: () => toggleSelection(t) }, icon("close")),
      ),
    ),
    h("button", { type: "button", class: "btn btn-link small", onclick: () => update({ selection: [] }) }, "Clear"),
  );
}

/** Pre-fill a change request (from "Make this a rule", "Promote to rule", etc.). */
export function prefillChange(text, selected = []) {
  setMode("change");
  if (selected.length) update({ selection: [...new Set([...state.selection, ...selected])] });
  els.input.value = text;
  document.body.classList.add("chat-open");
  els.input.focus();
  els.input.setSelectionRange(text.length, text.length);
}

function push(el) {
  els.root.querySelector(".thread-empty")?.remove();
  const li = h("li", {}, el);
  els.thread.append(li);
  li.scrollIntoView({ block: "end", behavior: "auto" });
  return li;
}

function userMsg(text, kind, selected) {
  return h("div", { class: ["msg", "msg-user"] },
    h("span", { class: "msg-kind" }, kind === "ask" ? "You asked" : "You requested a change"),
    h("p", {}, text),
    selected.length ? h("div", { class: "chip-row" }, selected.map((t) => targetChip(t))) : null,
  );
}

async function send() {
  const text = els.input.value.trim();
  if (!text) {
    els.input.focus();
    return;
  }
  const selected = [...state.selection];
  els.input.value = "";
  push(userMsg(text, mode, selected));
  if (mode === "ask") await doAsk(text, selected, els.verify.checked);
  else await doChange(text, selected);
}

// ---------------------------------------------------------------- Ask

async function doAsk(question, selected, verify) {
  const li = push(h("div", { class: "msg msg-bot is-loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), verify ? "Reading the spec, then checking on the app…" : "Reading the spec…"));
  try {
    const ans = await api.ask(question, selected, verify);
    li.replaceChildren(answerCard(ans, question, selected));
    const cited = (ans.citations || []).map((c) => c.target).filter(Boolean);
    if (cited.length) highlight(cited);
  } catch (e) {
    li.replaceChildren(errorMsg(e));
  }
}

function answerCard(ans, question, selected) {
  const cites = ans.citations || [];
  const v = ans.verified;
  return h("div", { class: ["msg", "msg-bot", "answer", `answer-${ans.kind}`] },
    h("div", { class: "answer-head" }, answerBadge(ans.kind), h("p", { class: "answer-short" }, ans.answer)),
    ans.explanation ? h("p", {}, ans.explanation) : null,
    cites.length
      ? h("div", { class: "cites" },
          h("div", { class: "cites-head" },
            h("span", { class: "small strong" }, `Rests on ${plural(cites.length, "citation")}`),
            h("button", { type: "button", class: "btn btn-link small", onclick: () => highlight(cites.map((c) => c.target)) }, "Show all on views"),
          ),
          h("ul", { class: "cite-list" }, cites.map((c) =>
            h("li", {}, targetChip(c.target), c.step ? h("span", { class: "muted small" }, ` step ${c.step}`) : null, c.quote ? h("q", { class: "cite-quote" }, c.quote) : null),
          )),
        )
      : null,
    h("p", { class: ["small", ans.citations_checked ? "muted" : "warn-text"] },
      ans.citations_checked ? [icon("check"), " Citations checked against the spec."] : [icon("triangle"), " Citations were not checked against the spec."]),
    (ans.rejected_citations || []).length
      ? h("details", { class: "small" }, h("summary", {}, `${plural(ans.rejected_citations.length, "citation")} dropped as not in the spec`),
          h("ul", {}, ans.rejected_citations.map((r) => h("li", {}, typeof r === "string" ? r : `${r.target || ""}${r.reason ? `: ${r.reason}` : ""}`))))
      : null,
    v ? verifiedBlock(v) : null,
    ans.kind === "not_specified"
      ? h("div", { class: "row-actions" },
          h("button", { type: "button", class: "btn btn-primary", onclick: () => prefillChange(`Make this a rule: ${question.replace(/\?\s*$/, "")}. `, selected) }, icon("sparkle"), "Make this a rule"),
        )
      : null,
    ans.usage ? h("p", { class: "muted tiny" }, `${Number(ans.usage.seconds || 0).toFixed(1)} s · ${Number(ans.usage.tokens || 0).toLocaleString()} tokens`) : null,
  );
}

function verifiedBlock(v) {
  const map = {
    agrees: { cls: "ver-agrees", icon: "check", label: "The app agrees" },
    disagrees: { cls: "ver-disagrees", icon: "diamond", label: "The app disagrees" },
    error: { cls: "ver-error", icon: "triangle", label: "Could not verify on the app" },
  };
  const m = map[v.status] || map.error;
  return h("div", { class: ["verified", m.cls], role: v.status === "disagrees" ? "alert" : null },
    h("p", { class: "verified-head" }, icon(m.icon), h("strong", {}, m.label)),
    v.detail ? h("p", {}, v.detail) : null,
    v.scenario ? h("details", {}, h("summary", {}, "The scenario that was run"), h("pre", { class: "code-block" }, v.scenario)) : null,
  );
}

function errorMsg(e) {
  return h("div", { class: "msg msg-error", role: "alert" }, icon("triangle"), " ", e.message || String(e));
}

// ---------------------------------------------------------------- Change

const STEPS = [
  ["drafting", "Drafting the edit"],
  ["checking", "Checking the spec"],
  ["ready", "Ready for review"],
];

function progress(status) {
  const idx = STEPS.findIndex(([k]) => k === status);
  return h("ol", { class: "stepper", "aria-label": "Progress" }, STEPS.map(([k, label], i) =>
    h("li", { class: [i < idx && "done", i === idx && "current"], "aria-current": i === idx ? "step" : null },
      h("span", { class: "step-dot", "aria-hidden": "true" }, i < idx ? icon("check") : String(i + 1)),
      h("span", {}, label),
    ),
  ));
}

async function doChange(request, selected) {
  const li = push(h("div", { class: "msg msg-bot" }, progress("drafting")));
  let id;
  try {
    id = (await api.newChange(request, selected)).id;
  } catch (e) {
    li.replaceChildren(errorMsg(e));
    return;
  }
  for (let n = 0; n < POLL_LIMIT; n++) {
    let c;
    try {
      c = await api.change(id);
    } catch (e) {
      li.replaceChildren(errorMsg(e));
      return;
    }
    if (c.status === "failed") {
      li.replaceChildren(h("div", { class: "msg msg-error", role: "alert" }, icon("triangle"), ` The change could not be drafted: ${c.error || "no reason given"}.`));
      return;
    }
    if (c.status === "ready") {
      if (state.change && state.change.id !== id) closeOpenCard("Replaced by a newer change.");
      update({ change: c });
      li.replaceChildren(changeCard(c));
      li.scrollIntoView({ block: "start" });
      return;
    }
    li.replaceChildren(h("div", { class: "msg msg-bot" }, progress(c.status)));
    await sleep(POLL_MS);
  }
  li.replaceChildren(errorMsg(new Error("The change is taking too long. Try again later.")));
}

function closeOpenCard(note) {
  const card = els.thread.querySelector(".change-card.is-open");
  if (!card) return;
  card.classList.remove("is-open");
  for (const b of card.querySelectorAll(".change-actions button")) b.disabled = true;
  card.querySelector(".change-actions")?.append(h("span", { class: "muted small" }, note));
}

function affectedTargets(c) {
  const out = [];
  for (const d of c.view_diff?.cells || []) out.push(`cell:${d.row}|${d.col}`);
  for (const k of ["modified", "added", "removed"]) {
    for (const t of c.targets?.[k] || []) out.push(typeof t === "string" ? t : t.target);
  }
  for (const r of c.rules?.changed || []) out.push(`rule:${typeof r === "string" ? r : r.old}`);
  return [...new Set(out.filter(Boolean))];
}

function changeCard(c) {
  const checks = c.checks || {};
  const conflicts = checks.conflicts || [];
  const lint = checks.lint || [];
  const problems = checks.problems || [];
  const impact = checks.impact;
  const rules = c.rules || {};
  const status = h("p", { class: "change-status", role: "status" });

  const acceptBtn = h("button", { type: "button", class: "btn btn-primary", disabled: checks.valid === false }, icon("check"),
    conflicts.length ? `Accept with ${plural(conflicts.length, "open conflict")}` : "Accept and commit");
  const rejectBtn = h("button", { type: "button", class: "btn" }, icon("cross"), "Reject");
  const card = h("div", { class: "msg change-card is-open", "aria-label": `Change set ${c.id}` },
    h("header", { class: "change-head" },
      h("span", { class: "badge badge-pending" }, icon("pending"), h("span", {}, "Pending change")),
      h("span", { class: "muted small" }, c.id),
    ),
    h("p", { class: "change-request" }, h("q", {}, c.request)),

    // Conflicts first: they need the owner's decision.
    conflicts.length
      ? h("section", { class: "conflicts", "aria-label": "Conflicts" },
          h("h3", {}, icon("diamond"), ` ${plural(conflicts.length, "conflict")} to decide`),
          conflicts.map((k) => h("div", { class: "conflict", role: "group" },
            h("p", { class: "conflict-q" }, k.question || "Which one holds?"),
            h("p", { class: "small" }, k.message),
            h("div", { class: "chip-row" }, k.a ? targetChip(k.a) : null, h("span", { class: "muted small", "aria-hidden": "true" }, "vs"), k.b ? targetChip(k.b) : null),
            h("button", { type: "button", class: "btn btn-quiet small", onclick: () => prefillChange(`For ${c.id} (${c.request}): `, [k.a, k.b].filter(Boolean)) }, "Answer with a follow-up change"),
          )),
        )
      : null,

    h("section", { "aria-label": "Rules" },
      h("h3", {}, "Rules"),
      ruleLine("Added", rules.added || [], "added"),
      ruleLine("Changed", (rules.changed || []).map((r) => (typeof r === "string" ? r : r.old === r.new ? r.new : `${r.old} → ${r.new}`)), "changed"),
      ruleLine("Removed", rules.removed || [], "removed"),
    ),

    h("section", { "aria-label": "Checks" },
      h("h3", {}, "Checks"),
      h("ul", { class: "checks" },
        h("li", { class: checks.valid === false ? "chk-bad" : "chk-ok" },
          icon(checks.valid === false ? "cross" : "check"),
          checks.valid === false ? ` The spec does not load: ${plural(problems.length, "problem")}` : " The spec loads and is consistent"),
        problems.length ? h("li", {}, h("ul", { class: "small" }, problems.map((p) => h("li", {}, typeof p === "string" ? p : p.message || JSON.stringify(p))))) : null,
        h("li", { class: lint.length ? "chk-warn" : "chk-ok" }, icon(lint.length ? "triangle" : "check"), ` ${plural(lint.length, "lint finding")}`,
          lint.length ? h("details", { class: "small" }, h("summary", {}, "Show"), h("ul", {}, lint.map((l) => h("li", {}, h("code", {}, l.code), " ", l.message, l.fix ? h("p", { class: "muted" }, "Fix: ", l.fix) : null)))) : null),
        impactBlock(impact),
      ),
    ),

    (c.questions || []).length
      ? h("section", { "aria-label": "Questions" },
          h("h3", {}, "Questions it raises"),
          h("ul", { class: "small" }, c.questions.map((q) => h("li", {}, h("strong", {}, q.id + " "), q.text, q.default ? h("p", { class: "muted" }, "Default: ", q.default) : null))),
        )
      : null,

    c.markdown_diff
      ? h("section", { "aria-label": "REQUIREMENTS.md diff" },
          h("h3", {}, "REQUIREMENTS.md"),
          unifiedDiff(c.markdown_diff, { label: "REQUIREMENTS.md diff" }),
        )
      : null,

    h("div", { class: "change-actions" },
      h("button", { type: "button", class: "btn btn-quiet", onclick: () => highlight(affectedTargets(c)) }, "Show on views"),
      rejectBtn,
      acceptBtn,
    ),
    status,
  );

  acceptBtn.addEventListener("click", async () => {
    acceptBtn.disabled = rejectBtn.disabled = true;
    status.textContent = "Writing the spec and committing…";
    try {
      const out = await api.accept(c.id);
      status.replaceChildren(icon("check"), " Committed ", h("code", {}, String(out.commit || "").slice(0, 10)), out.message ? `: ${out.message}` : "");
      status.className = "change-status ok";
      card.classList.remove("is-open");
      update({ change: null });
      await onAccepted();
    } catch (e) {
      status.textContent = e.message;
      status.className = "change-status bad";
      acceptBtn.disabled = rejectBtn.disabled = false;
    }
  });
  rejectBtn.addEventListener("click", async () => {
    acceptBtn.disabled = rejectBtn.disabled = true;
    try {
      await api.reject(c.id);
      status.textContent = "Rejected. The spec is unchanged.";
      card.classList.remove("is-open");
      update({ change: null });
    } catch (e) {
      status.textContent = e.message;
      acceptBtn.disabled = rejectBtn.disabled = false;
    }
  });
  return card;
}

function ruleLine(label, ids, kind) {
  if (!ids.length) return null;
  return h("p", { class: ["rule-line", `rl-${kind}`] },
    h("span", { class: "rl-label" }, label),
    ids.map((id) => {
      const plain = String(id).split(" → ").pop();
      const known = (state.model?.rules || []).some((r) => r.id === plain);
      return known && kind !== "added"
        ? targetChip(`rule:${plain}`)
        : h("span", { class: "chip" }, h("span", { class: "chip-kind" }, "Rule"), h("span", { class: "chip-name" }, id));
    }),
  );
}

function impactBlock(impact) {
  if (!impact) return h("li", { class: "chk-none" }, icon("dash"), " Not run on the app (no app hooked up)");
  const failing = impact.failing || [];
  if (impact.verdict === "pass" || !failing.length) return h("li", { class: "chk-ok" }, icon("check"), " Runs clean on the app");
  const groups = {};
  for (const f of failing) (groups[f.label] ||= []).push(f);
  return h("li", { class: "chk-impact" },
    h("p", {}, icon("triangle"), ` ${plural(failing.length, "check")} fail on the app with this change`),
    ["contradiction", "regression", "not_built_yet"].filter((k) => groups[k]).map((k) =>
      h("div", { class: ["impact-group", IMPACT_LABEL[k].cls] },
        h("p", { class: "impact-label" }, h("span", { class: "badge badge-impact" }, IMPACT_LABEL[k].label), h("span", { class: "muted small" }, ` ${IMPACT_LABEL[k].help}`)),
        h("ul", {}, groups[k].map((f) => h("li", {}, targetChip(f.target), h("span", { class: "small" }, ` ${f.message}`)))),
      ),
    ),
    Object.keys(groups).filter((k) => !IMPACT_LABEL[k]).map((k) =>
      h("div", { class: "impact-group" }, h("p", {}, k), h("ul", {}, groups[k].map((f) => h("li", {}, targetChip(f.target), ` ${f.message}`))))),
  );
}

