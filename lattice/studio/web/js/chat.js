// The conversation panel. One panel, three modes, always grounded in the selection:
//   Ask      answers from the spec (optionally verified on the app),
//   Explain  answers from the code (citations are code lines plus the rules they serve),
//   Change   proposes an edit to the contract; it comes back as a checked change set.
//
// Top to bottom: the open-work strip (changes in flight), the mode switch, the thread,
// pending inline edits (edit.js), context chips with suggested prompts, the composer.
// Accepting a change offers Rebuild; the rebuild card runs in the thread and, when its
// check passes, the model reloads so the amber marks clear.
//
// Hooks assigned on mount (store.js): hooks.ask, hooks.change, hooks.edit (edit.js).
// Listens on window for "studio:open-change" {detail: {id}} (the tray's Open action).

import { h, icon, clear, plural, add } from "./util.js";
import { api, sleep } from "./api.js";
import { state, update, subscribe, hooks } from "./store.js";
import { highlight, targetChip, labelOf, toggleSelection, kindOf, nameOf } from "./targets.js";
import { answerBadge, changeStatusBadge, IMPACT_LABEL } from "./badges.js";
import { unifiedDiff } from "./diff.js";
import { changeStatus } from "./tray.js";
import { mountEdit, phraseFor, combinedRequest, discardEdits, removeEdit, markSent, pruneSent } from "./edit.js";

const POLL_MS = 700;
const POLL_LIMIT = 300; // about 3.5 minutes
const REBUILD_POLL_MS = 1200;
const REBUILD_POLL_LIMIT = 600;

const OPEN_STATUSES = ["drafting", "checking", "ready"];
const HISTORY_STATUSES = ["accepted", "rejected", "cancelled", "failed"];
const INTENT_VIEWS = new Set(["map", "rules", "access", "data", "machines", "invariants", "scenarios", "services"]);
const MODES = [
  { id: "ask", label: "Ask", send: "Ask", label2: "Question", placeholder: "Ask about the spec, e.g. Can an executor cancel a run?" },
  { id: "explain", label: "Explain", send: "Explain", label2: "Question about the code", placeholder: "Ask about the code, e.g. Why does cancel return 409 here?" },
  { id: "change", label: "Change", send: "Request change", label2: "Change request", placeholder: "Describe the change, e.g. only app admins may cancel runs" },
];

let mode = "ask";
let els = {};
let onAccepted = () => {};
let autoText = ""; // the last text this panel wrote into the composer for the owner

// Every change the panel knows about (from GET /api/model's `changes`, or fetched in
// full when opened or drafted here), keyed by id. The id of the change-set card
// currently open in the thread, and its <li>, so a follow-up can replace it in place.
let changeRecords = new Map();
let pollingIds = new Set();
let followingRebuilds = new Set();
let openId = null;
let openLi = null;
let rebuildsMod = null; // views/rebuilds.js, when N5's exports are there

async function loadRebuildsMod() {
  if (rebuildsMod !== null) return rebuildsMod || null;
  try {
    const m = await import("./views/rebuilds.js");
    rebuildsMod = m && (typeof m.rebuildCard === "function" || typeof m.pollRebuild === "function") ? m : false;
  } catch (e) {
    rebuildsMod = false;
  }
  return rebuildsMod || null;
}

// ---------------------------------------------------------------- mount

export function mountChat(root, opts = {}) {
  onAccepted = opts.onAccepted || onAccepted;
  clear(root);
  changeRecords = new Map();
  pollingIds = new Set();
  followingRebuilds = new Set();
  openId = null;
  openLi = null;
  const thread = h("ol", { class: "thread", "aria-live": "polite", "aria-label": "Conversation" });
  const workStrip = h("div", { class: "work-strip", "aria-label": "Open work", hidden: true });
  const editsBox = h("form", { class: "edits-box", "aria-label": "Edits to send", hidden: true, onsubmit: (e) => {
    e.preventDefault();
    sendEdits();
  } });
  const ctxRow = h("div", { class: "ctx-row", "aria-label": "Context" });
  const prompts = h("div", { class: "prompt-row", "aria-label": "Suggested prompts" });
  const input = h("textarea", {
    id: "chat-input",
    rows: "2",
    placeholder: "",
    onkeydown: (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        send();
      } else if (e.key === "Escape") {
        e.preventDefault();
        if (input.value) {
          input.value = "";
          autoText = "";
        } else if (state.selection.length) update({ selection: [] });
        else {
          document.body.classList.remove("chat-open");
          input.blur();
        }
      }
    },
  });
  const verify = h("input", { type: "checkbox", id: "chat-verify" });
  const verifyLabel = h("label", { class: "toggle", for: "chat-verify" }, verify, h("span", {}, "Verify on the app"));
  const verifyNote = h("span", { class: "muted small", id: "verify-note" });
  const sendBtn = h("button", { type: "submit", class: "btn btn-primary", title: "Send (⌘↵)", "aria-keyshortcuts": "Meta+Enter" }, "Ask");
  const modeBtns = Object.fromEntries(MODES.map((m) => [m.id,
    h("button", { type: "button", class: "seg-btn", "aria-pressed": "false", onclick: () => setMode(m.id) }, m.label)]));
  const inputLabel = h("label", { for: "chat-input", class: "sr-only" }, "Question");

  const form = h("form", { class: "composer", onsubmit: (e) => {
    e.preventDefault();
    send();
  } },
    ctxRow,
    prompts,
    inputLabel,
    input,
    h("div", { class: "composer-row" },
      h("div", { class: "verify-wrap" }, verifyLabel, verifyNote),
      sendBtn,
    ),
  );

  add(root,
    h("div", { class: "chat-head" },
      h("h2", { id: "chat-title" }, "Conversation"),
      h("button", { type: "button", class: "btn btn-icon chat-close", "aria-label": "Close the conversation", onclick: () => document.body.classList.remove("chat-open") }, icon("close")),
    ),
    workStrip,
    h("div", { class: "seg seg-wide", role: "group", "aria-label": "Mode" }, MODES.map((m) => modeBtns[m.id])),
    h("div", { class: "thread-wrap" }, thread, emptyState()),
    editsBox,
    form,
  );
  els = { root, thread, ctxRow, prompts, input, verify, verifyLabel, verifyNote, sendBtn, modeBtns, inputLabel, workStrip, editsBox };
  applyMode(state.mode || "ask");
  renderContext();
  renderEdits();

  hooks.ask = (target, selection) => {
    const sel = [...new Set([...(selection || state.selection), ...(target ? [target] : [])])];
    if (sel.length !== state.selection.length || sel.some((t, i) => t !== state.selection[i])) update({ selection: sel });
    setMode(target && String(target).startsWith("code:") ? "explain" : "ask");
    prefill(questionFor(target));
  };
  hooks.change = (target, selection) => {
    const sel = [...new Set([...(selection || state.selection), ...(target ? [target] : [])])];
    prefillChange(target ? `${labelOf(target)}: ` : "", sel);
  };
  mountEdit();

  subscribe((st, patch) => {
    if ("selection" in patch || "codeTarget" in patch) {
      renderContext();
    }
    if ("mode" in patch && patch.mode !== mode) applyMode(patch.mode);
    if ("edits" in patch) renderEdits();
    if ("model" in patch) {
      syncVerify();
      syncChangesFromModel(st.model?.changes || []);
    }
  });
  window.addEventListener("studio:open-change", (e) => {
    const id = e.detail?.id;
    if (!id) return;
    document.body.classList.add("chat-open");
    openChangeCard(id);
  });
  syncVerify();
  syncChangesFromModel(state.model?.changes || []);
}

function emptyState() {
  return h("div", { class: "thread-empty" },
    h("p", {}, h("strong", {}, "Ask"), " answers from the spec: “Can eve cancel a run?” It cites the rules and cells it rests on."),
    h("p", {}, h("strong", {}, "Explain"), " answers from the code: “Why does cancel return 409?” It cites the lines."),
    h("p", {}, h("strong", {}, "Change"), " asks for an edit: “only app admins may cancel runs”. It comes back as a checked change you accept or reject."),
    h("p", { class: "muted" }, "Click anything on the canvas to add it. ⌘↵ sends, Esc clears."),
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

/** Switch mode from the owner (or a hook): records it in the store, then redraws. */
function setMode(m) {
  if (state.mode !== m) update({ mode: m });
  applyMode(m);
}

function applyMode(m) {
  mode = MODES.some((x) => x.id === m) ? m : "ask";
  const def = MODES.find((x) => x.id === mode);
  for (const x of MODES) els.modeBtns[x.id].setAttribute("aria-pressed", x.id === mode ? "true" : "false");
  els.sendBtn.textContent = def.send;
  els.input.placeholder = def.placeholder;
  els.inputLabel.textContent = def.label2;
  syncVerify();
  renderPrompts();
}

/** Put text in the composer unless the owner has typed something of their own. */
function prefill(text, force = false) {
  document.body.classList.add("chat-open");
  const cur = els.input.value;
  if (text && (force || !cur.trim() || cur === autoText)) {
    els.input.value = text;
    autoText = text;
  }
  els.input.focus();
  const n = els.input.value.length;
  els.input.setSelectionRange(n, n);
}

/** Pre-fill a change request (from "Make this a rule", "Promote to rule", etc.). */
export function prefillChange(text, selected = []) {
  if (selected.length) update({ selection: [...new Set([...state.selection, ...selected])] });
  setMode("change");
  prefill(text, true);
}

function questionFor(target) {
  if (!target) return "";
  const kind = kindOf(target);
  const name = nameOf(target);
  if (kind === "cell") {
    const [row, col] = name.split("|");
    return `Can ${row} ${phraseFor(col).text}?`;
  }
  if (kind === "rule") return `What does ${name} require, and what enforces it?`;
  if (kind === "code") return "Why does this line do that?";
  if (kind === "invariant") return `What happens if ${name.replace(/_/g, " ")} does not hold?`;
  if (kind === "scenario") return `What does ${name.replace(/_/g, " ")} check?`;
  return `Tell me about ${labelOf(target)}.`;
}

// ---------------------------------------------------------------- context chips and prompts

/** "code:internal/app/runs.go:126" -> {path, line, file}. */
function parseCode(target) {
  const m = /^code:(.+):(\d+)$/.exec(String(target));
  return m ? { path: m[1], line: Number(m[2]), file: m[1].split("/").pop() } : null;
}

function chipLabel(t) {
  const c = parseCode(t);
  if (c) return `${c.file}:${c.line}`;
  const kind = kindOf(t);
  if (kind === "cell") return nameOf(t).replace("|", " × ");
  return labelOf(t);
}

function renderContext() {
  const sel = state.selection;
  const code = state.codeTarget && !sel.includes(state.codeTarget) ? state.codeTarget : null;
  els.ctxRow.replaceChildren();
  add(els.ctxRow,
    sel.map((t) =>
      h("span", { class: "chip chip-sel" },
        h("button", { type: "button", class: "chip-sel-name", title: `Show ${t}`, onclick: () => highlight([t]) }, chipLabel(t)),
        h("button", { type: "button", class: "chip-x", "aria-label": `Remove ${chipLabel(t)} from the context`, onclick: () => toggleSelection(t) }, icon("close")),
      ),
    ),
    h("button", {
      type: "button", class: "chip chip-add", title: code ? `Add ${chipLabel(code)}` : "Click an item on the canvas to add it",
      "aria-label": code ? `Add ${chipLabel(code)} to the context` : "Add from the canvas",
      onclick: () => {
        if (code) toggleSelection(code);
        else els.ctxRow.dataset.hint = "Click an item on the canvas to add it.";
        renderContext();
      },
    }, icon("plus"), h("span", {}, code ? chipLabel(code) : "Add")),
    els.ctxRow.dataset.hint && !code ? h("span", { class: "muted small", role: "status" }, els.ctxRow.dataset.hint) : null,
  );
  if (code) delete els.ctxRow.dataset.hint;
  renderPrompts();
}

function renderPrompts() {
  if (!els.prompts) return;
  els.prompts.replaceChildren();
  const target = state.selection[state.selection.length - 1];
  if (!target) return;
  const kind = kindOf(target);
  const items = [];
  if (kind === "cell") {
    const [, col] = nameOf(target).split("|");
    items.push(["Who can do this?", "ask", `Who can ${phraseFor(col).text}?`]);
  } else if (kind === "code") {
    items.push(["Why is this here?", "explain", "Why is this line here, and which rule does it serve?"]);
  } else {
    items.push(["What does this mean?", "ask", questionFor(target)]);
  }
  items.push(["What breaks if this changes?", kind === "code" ? "explain" : "ask", `What breaks if ${chipLabel(target)} changes?`]);
  items.push(["Make this a rule", "change", `Make this a rule: ${chipLabel(target)}. `]);
  add(els.prompts, items.map(([label, m, text]) =>
    h("button", {
      type: "button", class: "prompt",
      onclick: () => {
        setMode(m);
        prefill(text, true);
      },
    }, label),
  ));
}

// ---------------------------------------------------------------- open work

function isTerminal(status) {
  return HISTORY_STATUSES.includes(status);
}

/** Merge fresh data for a change into the local record, propagate a follow-up's
 *  supersession onto its parent, and redraw the strip. */
function registerChange(c) {
  changeRecords.set(c.id, { ...changeRecords.get(c.id), ...c });
  if (c.status === "ready" && c.parent) {
    const p = changeRecords.get(c.parent);
    if (p && !isTerminal(p.status)) {
      changeRecords.set(c.parent, { ...p, status: "rejected", superseded_by: c.id });
    }
  }
  pruneSent((id) => changeRecords.get(id)?.status);
  renderWork();
  syncAcceptedCard(changeRecords.get(c.id));
  const moved = [c.id, c.parent].filter(Boolean).map((id) => mirrorToModel(changeRecords.get(id))).some(Boolean);
  if (moved) update({ changesSeen: Date.now() });
}

/** A change card still offering "Rebuild now" follows a rebuild started elsewhere (the top
 *  bar, the tray): its head and prompt show the build state instead. */
function syncAcceptedCard(c) {
  const b = c?.build?.state;
  if (!b || b === "not_built" || !els.thread) return;
  const card = els.thread.querySelector(`.change-card[data-accepted="${CSS.escape(c.id)}"]`);
  if (!card) return;
  card.querySelector(".change-head .badge")?.replaceWith(statusBadge(c));
  const prompt = card.querySelector(".rebuild-prompt");
  if (!prompt || !prompt.querySelector("button:not(:disabled)")) return; // answered here: the rebuild card follows below
  const text = { rebuilding: "Rebuilding. Rebuilds shows the rounds.", built: "Built and verified.", gave_up: "The rebuild gave up. Rebuilds shows the findings." }[b];
  if (text) prompt.replaceChildren(h("p", { class: "muted small" }, text));
}

const MIRRORED = ["status", "request", "parent", "superseded_by", "build"];

/** Copy what this panel knows about a change into state.model.changes, so the tray follows
 *  drafting → checking → ready without waiting for the next model poll. True if it moved. */
function mirrorToModel(c) {
  const list = state.model?.changes;
  if (!c || !list) return false;
  const i = list.findIndex((x) => x.id === c.id);
  const cur = i >= 0 ? list[i] : {};
  const keys = MIRRORED.filter((k) => c[k] !== undefined && JSON.stringify(c[k]) !== JSON.stringify(cur[k]));
  if (!keys.length) return false;
  const next = { ...cur, id: c.id, ...Object.fromEntries(keys.map((k) => [k, c[k]])) };
  if (i >= 0) list[i] = next;
  else list.push(next);
  return true;
}

/** Called whenever the model refreshes: pick up changes made elsewhere (through the
 *  API, or before this page loaded) and keep polling the ones still in progress. */
function syncChangesFromModel(list) {
  for (const item of list) registerChange(item);
  for (const item of list) {
    if ((item.status === "drafting" || item.status === "checking") && !pollingIds.has(item.id)) {
      pollingIds.add(item.id);
      backgroundPoll(item.id).finally(() => pollingIds.delete(item.id));
    }
  }
}

async function backgroundPoll(id) {
  for (let n = 0; n < POLL_LIMIT; n++) {
    await sleep(POLL_MS);
    let c;
    try {
      c = await api.change(id);
    } catch (e) {
      return;
    }
    registerChange(c);
    if (c.status !== "drafting" && c.status !== "checking") return;
  }
}

/** The one-word state of a change for the strip: drafting, checking, ready, accepted ·
 *  not built, rebuilding, built, gave up, ... (the same words as the tray). */
function stateWord(c) {
  return changeStatus(c);
}

function inFlight(c) {
  return ["drafting", "checking", "ready", "accepted · not built", "rebuilding"].includes(stateWord(c));
}

function sentence(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function statusBadge(c) {
  const w = stateWord(c);
  if (["drafting", "checking", "ready"].includes(w)) return changeStatusBadge(w);
  const map = {
    "accepted · not built": ["cs-accepted", "pending"],
    rebuilding: ["cs-drafting", "play"],
    built: ["cs-accepted", "check"],
    "gave up": ["cs-failed", "triangle"],
  };
  if (map[w]) return h("span", { class: ["badge", map[w][0]] }, icon(map[w][1]), h("span", {}, sentence(w)));
  if (c.status === "accepted") return changeStatusBadge("accepted");
  return changeStatusBadge(c.status);
}

function renderWork() {
  const panel = els.workStrip;
  if (!panel) return;
  const all = [...changeRecords.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const open = all.filter(inFlight);
  const history = all.filter((c) => !inFlight(c));
  clear(panel);
  panel.hidden = !open.length && !history.length;
  if (panel.hidden) return;
  add(panel,
    open.length
      ? h("ul", { class: "change-list", "aria-label": "Open work" }, open.map(workRow))
      : h("p", { class: "muted small work-none" }, "Nothing in flight."),
    history.length
      ? h("details", { class: "change-history" },
          h("summary", {}, `History (${plural(history.length, "change")})`),
          h("ul", { class: "change-list change-list-history" }, history.map(workRow)),
        )
      : null,
  );
}

function workRow(c) {
  const w = stateWord(c);
  const live = w === "drafting" || w === "checking";
  return h("li", { class: ["change-row", openId === c.id && "is-open"] },
    h("button", {
      type: "button", class: "change-row-main",
      title: "Open this change in the conversation",
      onclick: () => openChangeCard(c.id),
    },
      h("span", { class: "muted tiny" }, c.id),
      h("span", { class: "change-row-req" }, c.request || "(a follow-up)"),
      statusBadge(c),
    ),
    live
      ? h("button", {
          type: "button", class: "btn btn-quiet small",
          onclick: (e) => { e.stopPropagation(); cancelChange(c.id); },
        }, "Cancel")
      : null,
    c.superseded_by ? h("span", { class: "muted tiny" }, `superseded by ${c.superseded_by}`) : null,
  );
}

function closedMsg(c, note) {
  return h("div", { class: "msg msg-bot" },
    c.request ? h("p", {}, h("q", {}, c.request)) : null,
    h("p", { class: "muted small" }, note),
  );
}

function cancelInlineButton(id) {
  return h("button", { type: "button", class: "btn btn-quiet small", onclick: () => cancelChange(id) }, "Cancel");
}

async function cancelChange(id) {
  try {
    await api.cancelChange(id);
  } catch (e) {
    toastError(`Could not cancel ${id}: ${e.message}`);
    return;
  }
  registerChange({ ...(changeRecords.get(id) || { id }), status: "cancelled" });
  if (openId === id && openLi) openLi.replaceChildren(closedMsg(changeRecords.get(id), "Cancelled."));
}

function toastError(text) {
  push(errorMsg(new Error(text)));
}

/** Open a change in the thread: a strip row, or the tray's Open. Drafting/checking
 *  changes keep polling and rendering into the same <li>; a ready one gets the full
 *  change-set card (and draws its diff on the canvas); an accepted one gets Rebuild. */
async function openChangeCard(id) {
  if (openId === id && openLi && openLi.isConnected) {
    openLi.scrollIntoView({ block: "start", behavior: "smooth" });
    const known = changeRecords.get(id);
    if (known?.status === "ready" && state.change?.id !== id) api.change(id).then((c) => update({ change: c })).catch(() => {});
    return;
  }
  const li = push(h("div", { class: "msg msg-bot is-loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Opening the change…"));
  let c;
  try {
    c = await api.change(id);
  } catch (e) {
    li.replaceChildren(errorMsg(e));
    return;
  }
  registerChange(c);
  openId = id;
  openLi = li;
  const rec = changeRecords.get(id);
  if (c.status === "ready") {
    update({ change: c });
    li.replaceChildren(changeCard(c));
  } else if (c.status === "drafting" || c.status === "checking") {
    li.replaceChildren(h("div", { class: "msg msg-bot" }, progress(c.status), cancelInlineButton(id)));
    runChange(id, li, c.parent || null);
  } else if (c.status === "failed") {
    li.replaceChildren(errorMsg(new Error(`The change could not be drafted: ${c.error || "no reason given"}. Edit the request and send it again.`)));
  } else if (c.status === "cancelled") {
    li.replaceChildren(closedMsg(c, "Cancelled."));
  } else if (c.status === "accepted") {
    li.replaceChildren(acceptedCard(rec, li));
  } else {
    li.replaceChildren(closedMsg(c, c.superseded_by ? `Superseded by ${c.superseded_by}.` : `This change is ${c.status}.`));
  }
  li.scrollIntoView({ block: "start" });
}

function push(el) {
  els.root.querySelector(".thread-empty")?.remove();
  const li = h("li", {}, el);
  els.thread.append(li);
  li.scrollIntoView({ block: "end", behavior: "auto" });
  return li;
}

function userMsg(text, kind, selected) {
  const label = { ask: "You asked", explain: "You asked about the code", change: "You requested a change" }[kind] || "You wrote";
  return h("div", { class: ["msg", "msg-user"] },
    h("span", { class: "msg-kind" }, label),
    h("p", {}, text),
    selected.length ? h("div", { class: "chip-row" }, selected.map((t) => contextChip(t))) : null,
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
  autoText = "";
  push(userMsg(text, mode, selected));
  if (mode === "ask") await doAsk(text, selected, els.verify.checked);
  else if (mode === "explain") await doExplain(text, selected);
  else await doChange(text, selected);
}

// ---------------------------------------------------------------- Ask and Explain

/** The canvas shows the code for a target: lens "both" on an Intent view, else the Code lens. */
function openCode(target) {
  if (INTENT_VIEWS.has(state.view)) update({ codeTarget: target, lens: state.lens === "intent" ? "both" : state.lens });
  else update({ codeTarget: target, view: "code" });
}

function contextChip(t) {
  if (parseCode(t)) return codeChip(t);
  return targetChip(t);
}

function codeChip(target) {
  const c = parseCode(target);
  return h("button", {
    type: "button", class: "chip chip-target chip-kind-code", "data-chip-target": target,
    title: `Show ${c ? `${c.path} line ${c.line}` : target} in the code`,
    onclick: (e) => {
      e.stopPropagation();
      openCode(target);
    },
  }, h("span", { class: "chip-kind" }, "Code"), h("span", { class: "chip-name" }, c ? `${c.file}:${c.line}` : nameOf(target)));
}

async function doAsk(question, selected, verify) {
  const li = push(h("div", { class: "msg msg-bot is-loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), verify ? "Reading the spec, then checking on the app…" : "Reading the spec…"));
  try {
    const ans = await api.ask(question, selected, verify);
    li.replaceChildren(answerCard(ans, question, selected));
    const cited = (ans.citations || []).map((c) => c.target).filter((t) => t && !parseCode(t));
    if (cited.length) highlight(cited);
  } catch (e) {
    li.replaceChildren(errorMsg(e));
  }
}

async function doExplain(question, selected) {
  const li = push(h("div", { class: "msg msg-bot is-loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Reading the code…"));
  try {
    const ans = await api.explain(question, selected);
    li.replaceChildren(answerCard(ans, question, selected));
    const first = (ans.citations || []).map((c) => c.target).find((t) => parseCode(t));
    if (first) openCode(first);
  } catch (e) {
    li.replaceChildren(errorMsg(e));
  }
}

function answerCard(ans, question, selected) {
  const cites = ans.citations || [];
  const v = ans.verified;
  const specCites = cites.filter((c) => c.target && !parseCode(c.target));
  return h("div", { class: ["msg", "msg-bot", "answer", `answer-${ans.kind}`] },
    h("div", { class: "answer-head" }, answerBadge(ans.kind), h("p", { class: "answer-short" }, ans.answer)),
    ans.explanation ? h("p", {}, ans.explanation) : null,
    cites.length
      ? h("div", { class: "cites" },
          h("div", { class: "cites-head" },
            h("span", { class: "small strong" }, `Rests on ${plural(cites.length, "citation")}`),
            specCites.length
              ? h("button", { type: "button", class: "btn btn-link small", onclick: () => highlight(specCites.map((c) => c.target)) }, "Show on the canvas")
              : null,
          ),
          h("ul", { class: "cite-list" }, cites.map((c) =>
            h("li", {}, contextChip(c.target), c.step ? h("span", { class: "muted small" }, ` step ${c.step}`) : null, c.quote ? h("q", { class: "cite-quote" }, c.quote) : null),
          )),
        )
      : null,
    h("p", { class: ["small", ans.citations_checked ? "muted" : "warn-text"] },
      ans.citations_checked ? [icon("check"), " Citations checked against the spec."] : [icon("triangle"), " Citations were not checked against the spec."]),
    (ans.rejected_citations || []).length
      ? h("details", { class: "small" }, h("summary", {}, `${plural(ans.rejected_citations.length, "citation")} dropped as not in the spec`),
          h("ul", { class: "rejected-cites" }, ans.rejected_citations.map((r) =>
            h("li", {}, h("s", {}, typeof r === "string" ? r : r.target || ""), typeof r === "string" || !r.reason ? null : ` · ${r.reason}`))))
      : null,
    v ? verifiedBlock(v) : null,
    h("div", { class: "row-actions" },
      ans.kind === "not_specified"
        ? h("button", { type: "button", class: "btn btn-primary", onclick: () => prefillChange(`Make this a rule: ${question.replace(/\?\s*$/, "")}. `, selected) }, icon("sparkle"), "Make this a rule")
        : null,
      h("button", { type: "button", class: "btn btn-quiet", onclick: () => { prefill("", false); } }, "Ask a follow-up"),
      ans.kind !== "not_specified" && selected.length
        ? h("button", { type: "button", class: "btn btn-quiet", onclick: () => prefillChange("", selected) }, "Change this")
        : null,
    ),
    ans.usage ? h("p", { class: "muted tiny" }, `${Number(ans.usage.seconds || 0).toFixed(1)} s · ${Number(ans.usage.tokens || 0).toLocaleString()} tokens`) : null,
  );
}

function verifiedBlock(v) {
  const map = {
    agrees: { cls: "ver-agrees", icon: "check", label: "The app agrees" },
    disagrees: { cls: "ver-disagrees", icon: "diamond", label: "The app disagrees" },
    error: { cls: "ver-error", icon: "triangle", label: "Could not verify on the app" },
    // v1.1 #7: what the app does today, with no spec expectation to agree or disagree with.
    observed: { cls: "ver-observed", icon: "info", label: "What the app does today" },
    // v1.2 #8: a setup step failed before the claim step ran, so neither side is proven.
    inconclusive: { cls: "ver-inconclusive", icon: "question", label: "Could not be confirmed" },
  };
  const m = map[v.status] || map.error;
  return h("div", { class: ["verified", m.cls], role: v.status === "disagrees" ? "alert" : null },
    h("p", { class: "verified-head" }, icon(m.icon), h("strong", {}, m.label)),
    v.detail ? h("p", {}, v.detail) : null,
    v.claim_step ? h("p", { class: "muted small" }, `Claim step: ${v.claim_step}`) : null,
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

async function doChange(request, selected, parent = null) {
  const li = push(h("div", { class: "msg msg-bot" }, progress("drafting")));
  let id;
  try {
    id = (await api.newChange(request, selected, parent)).id;
  } catch (e) {
    li.replaceChildren(errorMsg(e));
    return null;
  }
  registerChange({ id, request, status: "drafting", parent, superseded_by: null });
  runChange(id, li, parent);
  return id;
}

/** Poll a change until it settles, rendering its progress (then its card, or its
 *  outcome) into `li`. Shared by a fresh request (doChange) and by re-opening a change
 *  still drafting or checking (openChangeCard). */
async function runChange(id, li, parent) {
  for (let n = 0; n < POLL_LIMIT; n++) {
    let c;
    try {
      c = await api.change(id);
    } catch (e) {
      li.replaceChildren(errorMsg(e));
      return;
    }
    registerChange(c);
    if (c.status === "cancelled") {
      li.replaceChildren(closedMsg(c, "Cancelled."));
      return;
    }
    if (c.status === "failed") {
      li.replaceChildren(h("div", { class: "msg msg-error", role: "alert" }, icon("triangle"), ` The change could not be drafted: ${c.error || "no reason given"}. Edit the request and send it again.`));
      return;
    }
    if (c.status === "ready") {
      if (state.change && state.change.id !== id) {
        const note = parent && state.change.id === parent ? `Superseded by ${id}.` : "Replaced by a newer change.";
        closeOpenCard(note);
      }
      openId = id;
      openLi = li;
      update({ change: c });
      li.replaceChildren(changeCard(c));
      li.scrollIntoView({ block: "start" });
      return;
    }
    li.replaceChildren(h("div", { class: "msg msg-bot" }, progress(c.status), cancelInlineButton(id)));
    await sleep(POLL_MS);
  }
  li.replaceChildren(errorMsg(new Error("The change is taking too long. Check the tray for its status, or send the request again.")));
}

function closeOpenCard(note) {
  const card = els.thread.querySelector(".change-card.is-open");
  if (!card) return;
  card.classList.remove("is-open");
  // Fold it like a decided card: head, request and the note; the tray keeps the rest.
  card.querySelector(".change-head .badge")?.replaceWith(h("span", { class: "badge cs-rejected" }, icon("dash"), h("span", {}, note.replace(/\.$/, ""))));
  for (const sec of card.querySelectorAll(":scope > section")) sec.hidden = true;
  card.querySelector(".change-actions")?.remove();
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

/** Send the pending inline edits (edit.js) as ONE change set. */
async function sendEdits() {
  const edits = [...state.edits];
  if (!edits.length) return;
  const text = (els.editsBox.querySelector("textarea")?.value || combinedRequest(edits)).trim();
  if (!text) return;
  const targets = edits.map((e) => e.target);
  push(userMsg(text, "change", targets));
  setMode("change");
  const id = await doChange(text, targets);
  if (id) markSent(edits, id);
}

let editsDirty = false;

function renderEdits() {
  const box = els.editsBox;
  if (!box) return;
  const edits = state.edits;
  box.hidden = !edits.length;
  if (!edits.length) {
    box.replaceChildren();
    editsDirty = false;
    return;
  }
  const old = box.querySelector("textarea");
  const text = old && editsDirty ? old.value : combinedRequest(edits);
  const area = h("textarea", {
    id: "edits-request", rows: "2", class: "edit-request",
    oninput: () => {
      editsDirty = true;
    },
  }, text);
  box.replaceChildren(
    h("p", { class: "edits-head" }, h("strong", {}, plural(edits.length, "edit")), " ready to send as one change"),
    h("ul", { class: "edits-list" }, edits.map((e) =>
      h("li", {},
        h("span", { class: "edits-what" }, chipLabel(e.target), h("span", { class: "muted small" }, ` · ${e.mark}`)),
        h("button", { type: "button", class: "chip-x", "aria-label": `Remove the edit to ${chipLabel(e.target)}`, onclick: () => removeEdit(e.target) }, icon("close")),
      ))),
    h("label", { for: "edits-request", class: "sr-only" }, "Request for the change set"),
    area,
    h("div", { class: "edits-actions" },
      h("button", { type: "button", class: "btn btn-quiet", onclick: () => discardEdits() }, "Discard"),
      h("button", { type: "submit", class: "btn btn-primary" }, edits.length > 1 ? `Send ${edits.length} edits` : "Send"),
    ),
  );
}

/** An in-place "Answer" control for a conflict or question (v1.1 #3): choose the
 *  default (when there is one) or type an answer, and send it as a follow-up change
 *  (`POST /api/changes` with `parent: parentId`). Replaces the parent's card once ready. */
function answerControl(parentId, questionText, defaultText) {
  const input = h("input", {
    type: "text", class: "answer-input", "aria-label": "Your answer",
    placeholder: defaultText ? `Default: ${defaultText}` : "Type an answer…",
  });
  const note = h("span", { class: "muted small answer-note" });
  const buttons = [];
  const disableAll = () => {
    input.disabled = true;
    for (const b of buttons) b.disabled = true;
  };
  const go = (answerText) => {
    const text = (answerText ?? input.value).trim();
    if (!text) {
      input.focus();
      return;
    }
    disableAll();
    note.textContent = "Sent as a follow-up. It appears below.";
    doChange(`${questionText.trim()} ${text}`.trim(), [], parentId);
  };
  const answerBtn = h("button", { type: "button", class: "btn btn-quiet small" }, "Answer");
  answerBtn.addEventListener("click", () => go());
  buttons.push(answerBtn);
  const children = [input];
  if (defaultText) {
    const defaultBtn = h("button", { type: "button", class: "btn btn-quiet small" }, "Use default");
    defaultBtn.addEventListener("click", () => go(defaultText));
    buttons.push(defaultBtn);
    children.push(defaultBtn);
  }
  children.push(answerBtn, note);
  return h("div", { class: "answer-row", role: "group", "aria-label": "Answer this" }, children);
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
    conflicts.length ? `Accept with ${plural(conflicts.length, "open conflict")}` : "Accept");
  const rejectBtn = h("button", { type: "button", class: "btn" }, icon("cross"), "Reject");
  const actions = h("div", { class: "change-actions" },
    h("button", { type: "button", class: "btn btn-quiet", onclick: () => highlight(affectedTargets(c)) }, "Show on the canvas"),
    rejectBtn,
    acceptBtn,
  );
  const badge = h("span", { class: "badge badge-pending" }, icon("pending"), h("span", {}, "Pending change"));
  // Once decided, the card folds to its head, request and outcome; the tray keeps the rest.
  const decided = (cls, ico, word) => {
    badge.replaceWith(h("span", { class: ["badge", cls] }, icon(ico), h("span", {}, word)));
    card.classList.remove("is-open");
    for (const sec of card.querySelectorAll(":scope > section")) sec.hidden = true;
  };
  const card = h("div", { class: "msg change-card is-open", "aria-label": `Change set ${c.id}` },
    h("header", { class: "change-head" },
      badge,
      h("span", { class: "muted small" }, c.id, c.parent ? ` · follows ${c.parent}` : ""),
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
            answerControl(c.id, k.question || "Which one holds?", null),
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
          h("ul", { class: "small" }, c.questions.map((q) => h("li", {},
            h("strong", {}, q.id + " "), q.text,
            q.default ? h("p", { class: "muted" }, "Default: ", q.default) : null,
            answerControl(c.id, q.text, q.default || null),
          ))),
        )
      : null,

    c.markdown_diff
      ? h("section", { "aria-label": "REQUIREMENTS.md diff" },
          h("h3", {}, "REQUIREMENTS.md"),
          unifiedDiff(c.markdown_diff, { label: "REQUIREMENTS.md diff" }),
        )
      : null,

    actions,
    checks.valid === false
      ? h("p", { class: "muted small" }, "Accept is off because the spec does not load. Fix the problems above, then send the request again.")
      : null,
    status,
  );

  acceptBtn.addEventListener("click", async () => {
    acceptBtn.disabled = rejectBtn.disabled = true;
    status.textContent = "Writing the spec and committing…";
    try {
      const out = await api.accept(c.id);
      status.replaceChildren("Committed ", h("code", {}, String(out.commit || "").slice(0, 7)), out.message ? `: ${out.message}` : "");
      status.className = "change-status muted small";
      decided("cs-accepted", "pending", "Accepted · not built");
      update({ change: null });
      registerChange({ ...(changeRecords.get(c.id) || c), status: "accepted", build: { state: "not_built" } });
      actions.replaceWith(status, rebuildPrompt(c.id, card));
      await onAccepted();
    } catch (e) {
      status.textContent = `Could not accept: ${e.message} Try again, or reject the change.`;
      status.className = "change-status bad";
      acceptBtn.disabled = checks.valid === false;
      rejectBtn.disabled = false;
    }
  });
  rejectBtn.addEventListener("click", async () => {
    acceptBtn.disabled = rejectBtn.disabled = true;
    try {
      await api.reject(c.id);
      status.textContent = "The spec is unchanged.";
      decided("cs-rejected", "cross", "Rejected");
      actions.remove();
      update({ change: null });
      registerChange({ ...(changeRecords.get(c.id) || c), status: "rejected" });
    } catch (e) {
      status.textContent = `Could not reject: ${e.message} Try again.`;
      acceptBtn.disabled = checks.valid === false;
      rejectBtn.disabled = false;
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

// ---------------------------------------------------------------- Rebuild

/** "Accepted · not built" with Rebuild now / Rebuild later, shown in the change card. */
function rebuildPrompt(id, card) {
  const box = h("div", { class: "rebuild-prompt", role: "group", "aria-label": "Rebuild" });
  const later = h("button", { type: "button", class: "btn btn-quiet" }, "Rebuild later");
  const now = h("button", { type: "button", class: "btn btn-primary" }, icon("play"), "Rebuild now");
  now.addEventListener("click", () => {
    now.disabled = later.disabled = true;
    box.replaceChildren(h("p", { class: "muted small" }, "Rebuilding. The progress is below."));
    startRebuild([id]);
  });
  later.addEventListener("click", () => {
    now.disabled = later.disabled = true;
    box.replaceChildren(h("p", { class: "muted small" }, "Accepted · not built. Rebuild it from the strip above, the tray or the top bar."));
  });
  add(box,
    h("p", { class: "muted small" }, "Rebuild hands the accepted spec to the coding agent, which edits the code and runs the checks until they pass."),
    h("div", { class: "change-actions" }, later, now),
  );
  card.dataset.accepted = id;
  return box;
}

/** The card for an accepted change opened from the strip or the tray. */
function acceptedCard(c, li) {
  const b = c.build?.state || "not_built";
  const head = h("header", { class: "change-head" }, statusBadge(c), h("span", { class: "muted small" }, c.id));
  const req = h("p", { class: "change-request" }, h("q", {}, c.request || "(no request text)"));
  if (b === "rebuilding" && c.build?.rebuild_id) {
    const host = h("div", { class: "rebuild-host" }, h("p", { class: "muted small" }, "Opening the rebuild…"));
    followRebuild(c.build.rebuild_id, host, [c.id]);
    return h("div", { class: "msg change-card" }, head, req, host);
  }
  if (b === "built") {
    return h("div", { class: "msg change-card" }, head, req,
      h("p", { class: "change-status ok" }, icon("check"), " Built and verified. The checks pass on the rebuilt code."),
      c.build?.rebuild_id ? h("button", { type: "button", class: "btn btn-quiet", onclick: () => update({ view: "rebuilds", rebuild: c.build.rebuild_id }) }, "Open the rebuild") : null);
  }
  const card = h("div", { class: "msg change-card" }, head, req);
  if (b === "gave_up") {
    add(card,
      h("p", { class: "change-status bad" }, icon("triangle"), " The last rebuild gave up with checks still failing."),
      h("div", { class: "change-actions" },
        h("button", { type: "button", class: "btn btn-quiet", onclick: () => prefillChange(`Revise: ${c.request || ""}`, []) }, "Edit the change"),
        h("button", { type: "button", class: "btn btn-primary", onclick: () => startRebuild([c.id]) }, icon("play"), "Rebuild again"),
      ),
    );
    return card;
  }
  add(card, rebuildPrompt(c.id, card));
  return card;
}

async function startRebuild(ids) {
  const li = push(h("div", { class: "msg msg-bot is-loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Starting the rebuild…"));
  let id;
  try {
    id = (await api.rebuild(ids)).id;
  } catch (e) {
    li.replaceChildren(errorMsg(new Error(e.unsupported ? e.message : `The rebuild could not start: ${e.message}`)));
    return;
  }
  for (const cid of ids) registerChange({ ...(changeRecords.get(cid) || { id: cid }), build: { state: "rebuilding", rebuild_id: id } });
  update({ rebuild: id });
  const host = h("div", { class: "rebuild-host" });
  li.replaceChildren(host);
  await followRebuild(id, host, ids);
}

/** Draw a rebuild into `host` and keep it fresh until it ends. Uses views/rebuilds.js's
 *  rebuildCard and pollRebuild when that module has them, else a minimal card and loop. */
async function followRebuild(rid, host, ids) {
  if (followingRebuilds.has(rid)) return;
  followingRebuilds.add(rid);
  const mod = await loadRebuildsMod();
  let finished = false;
  const stop = async () => {
    try {
      await api.rebuildStop(rid);
    } catch (e) {
      host.append(errorMsg(new Error(`Could not stop the rebuild: ${e.message}`)));
    }
  };
  const draw = (rb) => {
    let card = null;
    if (mod?.rebuildCard) {
      try {
        const out = mod.rebuildCard(rb, { compact: true, onStop: stop, changes: ids, openCode });
        if (out instanceof Node) card = out;
      } catch (e) {
        card = null;
      }
    }
    // rebuildCard draws its own result; the outcome line is only for the fallback card.
    host.replaceChildren(...(card ? [card] : [fallbackRebuildCard(rb, ids, stop), outcomeBlock(rb, ids)].filter(Boolean)));
  };
  const finish = async (rb) => {
    if (finished) return;
    finished = true;
    followingRebuilds.delete(rid);
    const built = rb.status === "passed" ? "built" : rb.status === "gave_up" ? "gave_up" : "not_built";
    for (const cid of ids) registerChange({ ...(changeRecords.get(cid) || { id: cid }), build: { state: built, rebuild_id: rid } });
    if (rb.status === "passed") await onAccepted(); // reload the model: amber marks clear
  };
  const onUpdate = (rb) => {
    if (!rb) return;
    draw(rb);
    if (["passed", "gave_up", "stopped"].includes(rb.status)) finish(rb);
  };
  try {
    if (mod?.pollRebuild) {
      const ret = mod.pollRebuild(rid, onUpdate);
      if (ret && typeof ret.then === "function") await ret;
    } else {
      for (let n = 0; n < REBUILD_POLL_LIMIT && !finished; n++) {
        const rb = await api.rebuildGet(rid);
        onUpdate(rb);
        if (finished) break;
        await sleep(REBUILD_POLL_MS);
      }
    }
    if (!finished) {
      const rb = await api.rebuildGet(rid);
      onUpdate(rb);
    }
  } catch (e) {
    host.replaceChildren(errorMsg(new Error(`The rebuild could not be followed: ${e.message} Open Rebuilds to see where it got to.`)));
    followingRebuilds.delete(rid);
  }
}

function outcomeBlock(rb, ids) {
  if (rb.status === "passed") {
    return h("p", { class: "change-status ok", role: "status" }, icon("check"),
      ` Built and verified in round ${rb.rounds?.length || 1}. The amber marks are plain now.`);
  }
  if (rb.status === "gave_up") {
    return h("div", { class: "rebuild-outcome" },
      h("p", { class: "change-status bad", role: "status" }, icon("triangle"),
        ` Gave up after ${plural(rb.rounds?.length || 0, "round")} with checks still failing. Review the findings, edit the change, or rebuild again.`),
      h("div", { class: "change-actions" },
        h("button", { type: "button", class: "btn btn-quiet", onclick: () => prefillChange(`Revise: ${changeRecords.get(ids[0])?.request || ""}`, []) }, "Edit the change"),
        h("button", { type: "button", class: "btn btn-primary", onclick: () => startRebuild(ids) }, icon("play"), "Rebuild again"),
      ),
    );
  }
  if (rb.status === "stopped") {
    return h("p", { class: "change-status", role: "status" }, "Stopped between rounds. The change is accepted · not built.");
  }
  return null;
}

/** A minimal rebuild card for when views/rebuilds.js has no rebuildCard. */
function fallbackRebuildCard(rb, ids, stop) {
  const live = rb.status === "queued" || rb.status === "running";
  const rounds = rb.rounds || [];
  const names = ids.map((id) => `${id} · ${changeRecords.get(id)?.request || "change"}`).join("; ");
  return h("div", { class: "msg rebuild-card", "aria-label": `Rebuild ${rb.id}` },
    h("header", { class: "rebuild-head" },
      h("p", { class: "rebuild-title" }, names),
      h("span", { class: "muted small" }, `${rb.model || "agent"} · round ${Math.min(Math.max(rounds.length, 1), rb.max_rounds || 1)} of ${rb.max_rounds || "?"}`),
      live ? h("button", { type: "button", class: "btn btn-quiet small", onclick: stop }, "Stop") : null,
    ),
    h("ol", { class: "rebuild-rounds" }, rounds.map((r) => {
      const pending = !r.check;
      const pass = r.check?.verdict === "pass";
      return h("li", { class: ["rebuild-round", pending && "is-running", pass && "is-pass", r.check && !pass && "is-fail"] },
        h("span", { class: "round-mark", "aria-hidden": "true" }, pending ? h("span", { class: "spinner" }) : icon(pass ? "check" : "diamond")),
        h("div", {},
          h("p", { class: "round-line" }, h("strong", {}, `Round ${r.n}`), " · ", pending ? "the agent is editing" : pass ? "checks pass" : `checks fail (${Object.entries(r.check.counts || {}).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(", ") || "findings"})`),
          r.agent_summary ? h("p", { class: "muted small" }, r.agent_summary) : null,
          r.files_changed?.length ? h("p", { class: "muted small" }, r.files_changed.join(", ")) : null,
          (r.check?.findings || []).length
            ? h("ul", { class: "round-findings" }, r.check.findings.map((f) => h("li", {}, f.target ? targetChip(f.target) : null, h("span", { class: "small" }, ` ${f.title}`))))
            : null,
        ),
      );
    })),
    (rb.targets || []).length
      ? h("ul", { class: "rebuild-targets", "aria-label": "Checks to turn green" }, rb.targets.map((t) =>
          h("li", { class: t.done ? "is-done" : "" }, icon(t.done ? "check" : "dash"), " ", t.label || t.id)))
      : null,
    rb.commit ? h("p", { class: "muted small" }, "Commit ", h("code", {}, String(rb.commit.sha || "").slice(0, 10)), rb.commit.message ? `: ${rb.commit.message}` : "") : null,
  );
}
