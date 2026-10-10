// ⌘K / Ctrl-K search dialog and the global keyboard map.
//   ⌘K  search rules, views and code files        A  ask about the selection
//   C   change the selection                      E  edit the selection inline
//   Esc close the dialog, else clear the selection
// J/K list movement belongs to the views.

import { h, clear, add } from "./util.js";
import { api } from "./api.js";
import { state, update, hooks } from "./store.js";
import { highlight, toggleSelection, clearMarks } from "./targets.js";

const typing = (el) =>
  !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

let dlg = null;
let files = null; // code files, fetched on first open; [] when unsupported

function closePalette() {
  if (!dlg) return;
  const back = dlg.opener;
  dlg.el.remove();
  dlg = null;
  update({ palette: false });
  if (back && back.focus) back.focus();
}

async function loadFiles() {
  if (files) return files;
  try {
    files = await api.codeFiles();
  } catch (e) {
    files = []; // 404 or offline: the palette just has no code entries
  }
  return files;
}

function entries(model, views) {
  const out = [];
  for (const v of views) out.push({ kind: "View", label: v.label, hint: v.group, run: () => opts.go(v.id) });
  for (const r of model?.rules || []) {
    out.push({
      kind: "Rule", label: `${r.id} ${String(r.text || "").replace(/[*`]/g, "")}`, hint: r.status,
      run: () => {
        const t = `rule:${r.id}`;
        if (!state.selection.includes(t)) toggleSelection(t);
        highlight([t]);
      },
    });
  }
  for (const f of files || []) {
    out.push({
      kind: "Code", label: f.path, hint: (f.rules || []).join(" "),
      run: () => opts.openCode(`code:${f.path}:1`),
    });
  }
  return out;
}

let opts = { views: [], go: () => {}, openCode: () => {} };

async function openPalette() {
  if (dlg) return;
  const opener = document.activeElement;
  const input = h("input", {
    type: "text", class: "palette-input", id: "palette-input", role: "combobox",
    "aria-expanded": "true", "aria-controls": "palette-list", "aria-autocomplete": "list",
    placeholder: "Search rules, views and code files", "aria-label": "Search",
  });
  const list = h("ul", { class: "palette-list", id: "palette-list", role: "listbox", "aria-label": "Results" });
  const el = h("div", { class: "palette-scrim" },
    h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Search" }, input, list),
  );
  let shown = [];
  let active = 0;

  const draw = () => {
    clear(list);
    shown.forEach((e, i) => {
      list.append(h("li", {
        id: `pal-${i}`, role: "option", class: ["palette-item", i === active && "is-active"],
        "aria-selected": i === active ? "true" : "false",
        onclick: () => choose(i),
      }, h("span", { class: "palette-kind" }, e.kind), h("span", { class: "palette-label" }, e.label),
        e.hint ? h("span", { class: "palette-hint muted" }, e.hint) : null));
    });
    if (!shown.length) list.append(h("li", { class: "palette-empty muted" }, "Nothing matches."));
    input.setAttribute("aria-activedescendant", shown.length ? `pal-${active}` : "");
    list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
  };
  const filter = () => {
    const q = input.value.trim().toLowerCase();
    const all = entries(state.model, opts.views);
    shown = (q ? all.filter((e) => e.label.toLowerCase().includes(q) || (e.hint || "").toLowerCase().includes(q)) : all).slice(0, 40);
    active = 0;
    draw();
  };
  const choose = (i) => {
    const e = shown[i];
    if (!e) return;
    closePalette();
    e.run();
  };

  input.addEventListener("input", filter);
  el.addEventListener("mousedown", (e) => {
    if (e.target === el) closePalette();
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closePalette();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      active = Math.min(shown.length - 1, active + 1);
      draw();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      active = Math.max(0, active - 1);
      draw();
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(active);
    } else if (e.key === "Tab") {
      e.preventDefault(); // focus stays on the field: the dialog has one tab stop
      input.focus();
    }
  });
  dlg = { el, opener };
  document.body.append(el);
  update({ palette: true });
  filter();
  input.focus();
  await loadFiles();
  if (dlg && dlg.el === el) filter();
}

function selectedTarget() {
  return state.selection[state.selection.length - 1] || null;
}

/** Wire ⌘K and the global letter keys. opts: {views: [{id,label,group}], go(id), openCode(target)}. */
export function mountPalette(o) {
  opts = o;
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      dlg ? closePalette() : openPalette();
      return;
    }
    if (dlg || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "Escape") {
      if (typing(e.target)) return;
      if (state.selection.length || state.marked.length) {
        update({ selection: [] });
        clearMarks();
      }
      return;
    }
    if (typing(e.target)) return;
    const k = e.key.toLowerCase();
    const hook = { a: hooks.ask, c: hooks.change, e: hooks.edit }[k];
    if (!hook || e.shiftKey) return;
    const target = selectedTarget();
    if (!target) return;
    e.preventDefault();
    hook(target, [...state.selection]);
  });
}

export { openPalette, closePalette };
