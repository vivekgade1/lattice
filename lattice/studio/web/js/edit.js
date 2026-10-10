// Inline editors: press E on a selected element (or click its pencil) and a small popover
// opens in place. A matrix cell gets a status picker, an invariant bound a number field, a
// lifecycle move a toggle, a rule a text field. "Save as change" writes the request text
// for the owner (editable), adds the edit to state.edits and shows it amber in place; the
// conversation panel (chat.js) sends all pending edits as ONE change set.
//
// Contract: hooks.edit(target, selection, detail?) opens the editor. `detail` is optional:
// for a lifecycle move {from, to}; the same move can be written in the target as
// "machine:JobRun.status#queued>running". Unknown kinds get a plain request field.
//
// Exports: mountEdit(), phraseFor(endpoint), combinedRequest(edits), discardEdits(),
// removeEdit(target), markSent(edits, changeId), pruneSent(statusOf), refreshOverlay().

import { h, add } from "./util.js";
import { state, update, subscribe, hooks } from "./store.js";
import { kindOf, nameOf, tid, labelOf } from "./targets.js";

let popover = null; // {el, opener, target}
let sent = []; // edits already posted as a change set, shown amber until it is accepted or rejected

const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const STATUS_CHOICES = [
  { code: "200", label: "Allow", help: "200 · the call goes through" },
  { code: "403", label: "Refuse", help: "403 · the caller is told no" },
  { code: "404", label: "Hide", help: "404 · the endpoint looks like it does not exist" },
];

// ---------------------------------------------------------------- small helpers

/** "POST /applications/{}/runs/{}/cancel" -> {verb: "cancel", noun: "runs"}; falls back to the route. */
export function phraseFor(endpoint) {
  const [method, ...rest] = String(endpoint).split(" ");
  const parts = rest.join(" ").split("/").filter(Boolean);
  const words = parts.filter((p) => /^[A-Za-z_-]+$/.test(p));
  const last = parts[parts.length - 1];
  if (last && /^[A-Za-z_-]+$/.test(last) && words.length >= 2 && method === "POST") {
    return { verb: last.replace(/[-_]/g, " "), noun: words[words.length - 2].replace(/[-_]/g, " "), text: `${last} ${words[words.length - 2]}` };
  }
  const plainVerb = { GET: "view", POST: "create", PUT: "change", PATCH: "change", DELETE: "delete" }[method] || method.toLowerCase();
  const noun = words.length ? words[words.length - 1].replace(/[-_]/g, " ") : "this";
  return { verb: plainVerb, noun, text: `${plainVerb} ${noun}`, route: String(endpoint) };
}

function modelOf() {
  return state.model || {};
}

function cellValue(row, col) {
  for (const g of modelOf().views?.access_groups || []) {
    const v = g.cells?.[`${row}|${col}`];
    if (v !== undefined) return String(v);
  }
  return "·";
}

function invariantSentence(target) {
  const tuple = (modelOf().views?.section6?.invariants || []).find((t) => t[0] === target);
  return tuple ? { name: tuple[1], sentence: tuple[2] } : null;
}

/** First bound in a sentence: "at most one", "at least 3", "up to two". */
function findBound(sentence) {
  const re = new RegExp(`\\b(at most|at least|no more than|up to|exactly|fewer than|more than)\\s+(\\d+|${NUMBER_WORDS.join("|")})\\b`, "i");
  const m = re.exec(sentence);
  if (!m) return null;
  const word = m[2].toLowerCase();
  const isWord = NUMBER_WORDS.includes(word);
  return { index: m.index + m[0].length - m[2].length, raw: m[2], value: isWord ? NUMBER_WORDS.indexOf(word) : Number(word), isWord, lead: m[1] };
}

function numberText(n, asWord) {
  return asWord && n >= 0 && n < NUMBER_WORDS.length ? NUMBER_WORDS[n] : String(n);
}

function machineOf(target) {
  const base = String(target).split("#")[0];
  const tuple = (modelOf().views?.section6?.machines || []).find((t) => t[0] === base);
  if (!tuple) return null;
  const sentence = tuple[2] || "";
  const edges = [];
  const states = [];
  const note = (s) => {
    if (s && !states.includes(s)) states.push(s);
  };
  for (const part of sentence.replace(/\(initial:[^)]*\)/, "").split(";")) {
    const m = /^\s*([^-\s][^-]*?)\s*->\s*(.+)$/.exec(part.trim());
    if (!m) continue;
    const from = m[1].trim();
    note(from);
    for (const to of m[2].split(",").map((x) => x.trim()).filter(Boolean)) {
      note(to);
      edges.push([from, to]);
    }
  }
  return { base, name: tuple[1], sentence, states, edges };
}

function ruleOf(target) {
  const id = nameOf(target);
  return (modelOf().rules || []).find((r) => r.id === id) || null;
}

function targetElement(target) {
  const base = String(target).split("#")[0];
  return document.getElementById(tid(base)) || document.getElementById(tid(target));
}

// ---------------------------------------------------------------- the editor description
//
// describe(target, detail) -> {title, fields(), sentence, ...}: what to show and how to
// turn the control's value into the amber mark and the request text.

function describe(target, detail) {
  const kind = kindOf(target);
  if (kind === "cell") return describeCell(target);
  if (kind === "invariant") return describeInvariant(target);
  if (kind === "machine") return describeMachine(target, detail);
  if (kind === "rule") return describeRule(target);
  return describeGeneric(target);
}

function describeCell(target) {
  const [row, col] = nameOf(target).split("|");
  const before = cellValue(row, col);
  const phrase = phraseFor(col);
  const initial = STATUS_CHOICES.find((c) => c.code === before)?.code || null;
  return {
    kind: "cell",
    title: `${row} · ${col}`,
    before,
    initial,
    control: "status",
    request(after) {
      if (after === "200") return `${row} may ${phrase.text}: ${before} → 200`;
      if (after === "403") return `${row} may not ${phrase.text}: ${before} → 403`;
      return `${row} gets 404 on ${phrase.route || col}, as if it did not exist: ${before} → 404`;
    },
    changed: (after) => !!after && after !== before,
    mark: (after) => `${before} → ${after}`,
  };
}

function describeInvariant(target) {
  const inv = invariantSentence(target);
  const sentence = inv?.sentence || "";
  const bound = findBound(sentence);
  if (!bound) return describeGeneric(target, sentence ? `Reword “${sentence}”: ` : "");
  return {
    kind: "invariant",
    title: inv.name.replace(/_/g, " "),
    sentence,
    control: "number",
    before: bound.value,
    request(after) {
      const n = Number(after);
      const verb = n > bound.value ? "Raise" : "Lower";
      return `${verb} the limit in “${sentence}” from ${bound.raw} to ${numberText(n, bound.isWord)}`;
    },
    changed: (after) => after !== "" && Number.isFinite(Number(after)) && Number(after) !== bound.value,
    mark: (after) => `${bound.value} → ${after}`,
  };
}

function describeMachine(target, detail) {
  const mach = machineOf(target);
  if (!mach) return describeGeneric(target);
  let move = detail && detail.from && detail.to ? [detail.from, detail.to] : null;
  const hash = String(target).split("#")[1];
  if (!move && hash && hash.includes(">")) move = hash.split(">");
  const moves = [];
  for (const a of mach.states) for (const b of mach.states) if (a !== b) moves.push([a, b]);
  const allowed = (m) => mach.edges.some(([f, t]) => f === m[0] && t === m[1]);
  return {
    kind: "machine",
    title: `${mach.name} moves`,
    control: "move",
    moves: moves.map((m) => ({ from: m[0], to: m[1], allowed: allowed(m) })),
    initialMove: move ? `${move[0]}>${move[1]}` : null,
    before: null,
    request(after, picked) {
      const [from, to] = picked.split(">");
      return after === "allow"
        ? `Allow ${mach.name} to move from ${from} to ${to}`
        : `Do not allow ${mach.name} to move from ${from} to ${to}`;
    },
    changed: (after, picked) => !!picked && !!after && (after === "allow") !== allowed(picked.split(">")),
    mark: (after, picked) => `${picked.replace(">", " → ")}: ${after === "allow" ? "allowed" : "refused"}`,
    allowed,
  };
}

function describeRule(target) {
  const r = ruleOf(target);
  const id = nameOf(target);
  return {
    kind: "rule",
    title: `Rule ${id}`,
    control: "text",
    before: r?.text || "",
    request: (after) => `Reword ${id} so it reads: ${after.trim()}`,
    changed: (after) => after.trim() !== "" && after.trim() !== (r?.text || "").trim(),
    mark: () => "reworded",
  };
}

function describeGeneric(target, starter = "") {
  return {
    kind: "request",
    title: labelOf(target),
    control: "request",
    before: starter,
    request: (after) => after.trim(),
    changed: (after) => after.trim() !== "" && after.trim() !== starter.trim(),
    mark: () => "edited",
  };
}

// ---------------------------------------------------------------- the popover

function closeEditor(returnFocus = true) {
  if (!popover) return;
  const { el, opener } = popover;
  popover = null;
  document.removeEventListener("mousedown", onOutside, true);
  el.remove();
  if (returnFocus && opener && document.contains(opener)) opener.focus({ preventScroll: true });
}

function onOutside(e) {
  if (popover && !popover.el.contains(e.target)) closeEditor(false);
}

function position(el, anchor) {
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const w = el.offsetWidth;
  const hgt = el.offsetHeight;
  let left = 24;
  let top = 72;
  if (anchor) {
    const r = anchor.getBoundingClientRect();
    left = r.left;
    top = r.bottom + 8;
    if (top + hgt > vh - 8) top = Math.max(8, r.top - hgt - 8);
  }
  left = Math.min(Math.max(8, left), Math.max(8, vw - w - 8));
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
}

function openEditor(target, selection, detail) {
  if (!target) return;
  closeEditor(false);
  const d = describe(target, detail);
  const existing = state.edits.find((e) => e.target === target);
  const opener = targetElement(target) || document.activeElement;
  const titleId = `edit-title-${Math.random().toString(36).slice(2, 8)}`;
  let picked = existing?.picked || d.initialMove || (d.moves?.[0] ? `${d.moves[0].from}>${d.moves[0].to}` : null);
  const movedNow = d.moves?.find((m) => `${m.from}>${m.to}` === picked);
  let value = existing
    ? existing.after
    : d.control === "status"
      ? d.initial
      : d.control === "move"
        ? (movedNow?.allowed ? "allow" : "refuse")
        : d.control === "number"
          ? String(d.before)
          : d.before;
  let dirtyRequest = !!existing;

  const requestBox = h("textarea", {
    id: `${titleId}-request`, class: "edit-request", rows: "2",
    oninput: () => {
      dirtyRequest = true;
    },
  });
  const note = h("p", { class: "edit-note muted small", role: "status" });
  const saveBtn = h("button", { type: "submit", class: "btn btn-primary" }, "Save as change");
  const controlHost = h("div", { class: "edit-control" });

  const sync = () => {
    const ok = d.changed(value, picked);
    saveBtn.disabled = !ok || !requestBox.value.trim();
    if (!dirtyRequest) requestBox.value = ok ? d.request(value, picked) : "";
    saveBtn.disabled = !ok || !requestBox.value.trim();
    note.textContent = ok ? "" : "Change the value to save it as a change.";
  };

  const draw = () => {
    controlHost.replaceChildren();
    if (d.control === "status") {
      add(controlHost, h("div", { class: "edit-choices", role: "radiogroup", "aria-label": "Status for this cell" },
        STATUS_CHOICES.map((c) => {
          const id = `${titleId}-${c.code}`;
          return h("label", { class: "edit-choice", for: id },
            h("input", {
              type: "radio", name: `${titleId}-status`, id, value: c.code, checked: value === c.code,
              onchange: () => {
                value = c.code;
                sync();
              },
            }),
            h("span", { class: "edit-choice-name" }, `${c.label} · ${c.code}`),
            h("span", { class: "edit-choice-help muted small" }, c.help.split("· ")[1]),
          );
        }),
      ), h("p", { class: "muted small" }, `Now: ${d.before}`));
    } else if (d.control === "number") {
      add(controlHost,
        h("label", { for: `${titleId}-n`, class: "edit-label" }, "Limit"),
        h("input", {
          type: "number", id: `${titleId}-n`, class: "edit-number", min: "0", step: "1", value: String(value),
          oninput: (e) => {
            value = e.target.value;
            sync();
          },
        }),
        h("p", { class: "muted small" }, d.sentence),
      );
    } else if (d.control === "move") {
      const sel = h("select", {
        id: `${titleId}-move`, class: "edit-select",
        onchange: (e) => {
          picked = e.target.value;
          const m = d.moves.find((x) => `${x.from}>${x.to}` === picked);
          value = m?.allowed ? "allow" : "refuse";
          draw();
          sync();
        },
      }, d.moves.map((m) => {
        const v = `${m.from}>${m.to}`;
        return h("option", { value: v, selected: v === picked }, `${m.from} → ${m.to} (${m.allowed ? "allowed" : "not allowed"})`);
      }));
      add(controlHost,
        h("label", { for: `${titleId}-move`, class: "edit-label" }, "Move"),
        sel,
        h("label", { class: "edit-toggle", for: `${titleId}-allow` },
          h("input", {
            type: "checkbox", role: "switch", id: `${titleId}-allow`, checked: value === "allow",
            onchange: (e) => {
              value = e.target.checked ? "allow" : "refuse";
              sync();
            },
          }),
          h("span", {}, "Allow this move"),
        ),
      );
    } else if (d.control === "text") {
      add(controlHost,
        h("label", { for: `${titleId}-text`, class: "edit-label" }, "Rule text"),
        h("textarea", {
          id: `${titleId}-text`, class: "edit-text", rows: "5",
          oninput: (e) => {
            value = e.target.value;
            sync();
          },
        }, String(value || d.before)),
      );
    } else {
      add(controlHost,
        h("label", { for: `${titleId}-req`, class: "edit-label" }, "What should change?"),
        h("textarea", {
          id: `${titleId}-req`, class: "edit-text", rows: "3",
          oninput: (e) => {
            value = e.target.value;
            sync();
          },
        }, String(value || d.before)),
      );
    }
  };

  const form = h("form", {
    class: "edit-pop", role: "dialog", "aria-modal": "false", "aria-labelledby": titleId,
    onsubmit: (e) => {
      e.preventDefault();
      if (saveBtn.disabled) return;
      const edit = {
        target, kind: d.kind, before: d.before, after: value, picked,
        request: requestBox.value.trim(), mark: d.mark(value, picked),
      };
      update({ edits: [...state.edits.filter((x) => x.target !== target), edit] });
      closeEditor(true);
      document.body.classList.add("chat-open");
    },
    onkeydown: (e) => {
      e.stopPropagation(); // keep the global A, C and E keys out of the form
      if (e.key === "Escape") {
        e.preventDefault();
        closeEditor(true);
      }
    },
  },
    h("p", { class: "edit-title", id: titleId }, h("span", { class: "muted" }, "Edit "), d.title),
    controlHost,
    h("label", { for: `${titleId}-request`, class: "edit-label" }, "Request"),
    requestBox,
    note,
    h("div", { class: "edit-actions" },
      h("button", { type: "button", class: "btn btn-quiet", onclick: () => closeEditor(true) }, "Cancel"),
      saveBtn,
    ),
  );
  if (existing) requestBox.value = existing.request;
  draw();
  sync();
  document.body.append(form);
  popover = { el: form, opener, target };
  position(form, targetElement(target));
  document.addEventListener("mousedown", onOutside, true);
  const first = form.querySelector("input:checked, input, select, textarea");
  (first || form).focus?.({ preventScroll: true });
}

// ---------------------------------------------------------------- pending edits and the amber overlay

/** One request for a set of edits: the single request, or each one joined with "; ". */
export function combinedRequest(edits) {
  return edits.map((e) => e.request.replace(/[.\s]+$/, "")).join("; ");
}

export function discardEdits() {
  update({ edits: [] });
}

export function removeEdit(target) {
  update({ edits: state.edits.filter((e) => e.target !== target) });
}

/** The posted change set now owns these edits; keep them amber until it settles. */
export function markSent(edits, changeId) {
  sent = [...sent.filter((s) => !edits.some((e) => e.target === s.target)), ...edits.map((e) => ({ ...e, change: changeId }))];
  update({ edits: state.edits.filter((e) => !edits.some((x) => x.target === e.target)) });
  refreshOverlay();
}

/** Drop sent edits whose change was accepted, rejected, cancelled, failed or superseded. */
export function pruneSent(statusOf) {
  const keep = sent.filter((s) => ["drafting", "checking", "ready"].includes(statusOf(s.change)));
  if (keep.length !== sent.length) {
    sent = keep;
    refreshOverlay();
  }
}

export function refreshOverlay() {
  for (const el of document.querySelectorAll(".edit-pending")) {
    el.classList.remove("edit-pending");
    if (el.dataset.editAdded) {
      el.classList.remove("diff-changed");
      delete el.dataset.editAdded;
    }
    el.removeAttribute("data-edit");
  }
  for (const e of [...state.edits, ...sent]) {
    const el = targetElement(e.target);
    if (!el) continue;
    if (!el.classList.contains("diff-changed")) {
      el.classList.add("diff-changed");
      el.dataset.editAdded = "1";
    }
    el.classList.add("edit-pending");
    el.setAttribute("data-edit", e.mark || "edited");
  }
}

export function mountEdit() {
  hooks.edit = (target, selection, detail) => openEditor(target, selection, detail);
  subscribe((st, patch) => {
    const keys = Object.keys(patch);
    if (keys.some((k) => ["edits", "model", "view", "change", "overlay", "marked", "lens"].includes(k))) {
      // The shell re-renders the canvas in its own subscriber first; draw after it.
      queueMicrotask(refreshOverlay);
    }
    if (("view" in patch || "lens" in patch) && popover) closeEditor(false);
  });
  refreshOverlay();
}
