// Code lens: the source files behind the spec, read only. A file tree with rule chips, the
// numbered source with gutter chips (rule id + short name), static findings inline and
// coverage bars (covered / failing / untested), "rebuilt in cN" markers. Selecting a rule or
// element scrolls to and highlights its lines (api.codeLinks). **why** on a line asks the
// conversation. Exports render() (the Code view) and renderPane() (the lens "Both" split).

import { h, icon, add, plural } from "../util.js";
import { api } from "../api.js";
import { state, subscribe, update, hooks } from "../store.js";
import { toggleSelection } from "../targets.js";
import { RULE_STATUS } from "../badges.js";

const WHY_QUESTION = "What rule does this line serve, and what breaks if it changes?";
const COVERAGE = { covered: "Covered", failing: "Failing", untested: "Untested" };

// Silent bookkeeping: a new selection supersedes a stale openCode() target, so the pane
// follows what the owner selects last. Registered at import, before the shell subscribes.
subscribe((st, patch) => {
  if ("selection" in patch && !("codeTarget" in patch) && st.codeTarget) {
    const last = st.selection[st.selection.length - 1] || null;
    if (last !== st.codeTarget) st.codeTarget = null;
  }
});

export function currentTarget() {
  return state.codeTarget || state.selection[state.selection.length - 1] || null;
}

// ------------------------------------------------------------------ data (cached per spec version)

let cacheKey = "";
const cache = new Map();

function cached(model, key, load) {
  const k = `${api.base()}|${model.project?.git_head}|${model.app?.last_check?.at}`;
  if (k !== cacheKey) {
    cache.clear();
    cacheKey = k;
  }
  if (!cache.has(key)) {
    const p = load();
    cache.set(key, p);
    p.catch(() => cache.delete(key));
  }
  return cache.get(key);
}

const loadFiles = (model) => cached(model, "files", () => api.codeFiles());
const loadFile = (model, path) => cached(model, `file:${path}`, () => api.codeFile(path));
const loadLinks = (model, target) => cached(model, `links:${target}`, () => api.codeLinks(target));

export function parseTarget(target) {
  const m = /^code:(.+?):(\d+)$/.exec(target || "");
  if (m) return { kind: "code", path: m[1], line: Number(m[2]) };
  const n = /^code:(.+)$/.exec(target || "");
  if (n) return { kind: "code", path: n[1], line: null };
  return { kind: target ? "element" : "none" };
}

function shortName(text) {
  const t = String(text || "").replace(/[*`_]/g, "").replace(/\([^)]*\)/g, "").replace(/^\W+/, "");
  return t.split(/\s+/).filter(Boolean).slice(0, 3).join(" ").toLowerCase().replace(/[^\w]+$/, "");
}

const ruleById = (model) => new Map((model.rules || []).map((r) => [r.id, r]));

function unsupportedState(err) {
  return h("div", { class: "empty-state s2-empty", role: "status" },
    h("h2", {}, "No code index here"),
    h("p", { class: "muted" }, err.unsupported
      ? "This server has no code index, so the Code lens has nothing to show. The mock server provides one."
      : err.message),
  );
}

// ------------------------------------------------------------------ the viewer

function askWhy(path, line) {
  const target = `code:${path}:${line}`;
  if (typeof hooks.ask === "function") hooks.ask(target, [target], { question: WHY_QUESTION, mode: "explain" });
}

function selectRule(id) {
  const target = `rule:${id}`;
  const selection = state.selection.includes(target) ? state.selection : [...state.selection, target];
  update({ selection, codeTarget: target });
}

function ruleChip(model, id, on) {
  const rule = ruleById(model).get(id);
  const st = RULE_STATUS[rule?.status] || RULE_STATUS.untraced;
  const sentence = rule ? rule.text.replace(/[*`]/g, "") : "No sentence for this rule in the spec.";
  return h("button", {
    type: "button",
    class: ["code-chip", `st-${rule?.status || "untraced"}`, on && "is-on"],
    title: `${id} (${st.label}): ${sentence}`,
    "aria-label": `Rule ${id}, ${st.label}: ${sentence}. Select it`,
    onclick: (e) => { e.stopPropagation(); selectRule(id); },
  },
    icon(st.icon),
    h("span", { class: "code-chip-id" }, id),
    h("span", { class: "code-chip-name" }, shortName(rule?.text)),
  );
}

/** One chip per line keeps rows one line tall: the selected rule (or the first), then a
 *  count of the others, named in its tooltip and accessible name. */
function gutter(model, ids, ruleOn) {
  if (!ids.length) return null;
  const first = ids.includes(ruleOn) ? ruleOn : ids[0];
  const rest = ids.filter((id) => id !== first);
  return [
    ruleChip(model, first, ruleOn === first),
    rest.length
      ? h("span", { class: "code-chip-more", title: rest.join(", "), "aria-label": `and ${rest.length} more: ${rest.join(", ")}` }, `+${rest.length}`)
      : null,
  ];
}

function legend() {
  return h("ul", { class: "code-legend", "aria-label": "Coverage legend" },
    Object.entries(COVERAGE).map(([k, label]) =>
      h("li", {}, h("span", { class: ["code-swatch", `is-${k}`], "aria-hidden": "true" }), label)));
}

function sourceView(model, file, hl, ruleOn, scrollTo) {
  const ann = new Map((file.annotations || []).map((a) => [a.line, a]));
  const rows = [];
  (file.lines || []).forEach((text, i) => {
    const n = i + 1;
    const a = ann.get(n);
    const row = h("div", {
      class: ["code-line", hl.has(n) && "is-hl"], "data-line": String(n),
      "data-target": `code:${file.path}:${n}`,
    },
      h("div", { class: "code-gutter" }, gutter(model, a?.rules || [], ruleOn)),
      h("span", { class: ["code-cov", `is-${a?.coverage || "none"}`], title: a?.coverage ? COVERAGE[a.coverage] : null },
        a?.coverage ? h("span", { class: "s2-sr" }, COVERAGE[a.coverage]) : null),
      h("span", { class: "code-no" }, String(n)),
      h("pre", { class: "code-text" }, text || " "),
      h("span", { class: "code-tail" },
        a?.rebuilt_in ? h("span", { class: "code-rebuilt" }, icon("sparkle"), `rebuilt in ${a.rebuilt_in}`) : null,
        h("button", {
          type: "button", class: "code-why", "aria-label": `Why line ${n}? Ask the conversation`,
          title: WHY_QUESTION, onclick: (e) => { e.stopPropagation(); askWhy(file.path, n); },
        }, "why"),
      ),
    );
    rows.push(row);
    if (a?.finding) {
      rows.push(h("div", { class: "code-finding", role: "note" },
        icon("diamond"),
        h("span", {}, h("strong", {}, `Finding ${a.finding.code}: `), a.finding.message)));
    }
  });
  const box = h("div", { class: "code-src" }, h("div", { class: "code-scroll", tabindex: "0", role: "region", "aria-label": `Source of ${file.path}` }, rows));
  // scroll after attach
  queueMicrotask(() => {
    const el = scrollTo ? box.querySelector(`[data-line="${scrollTo.line}"]`) : null;
    if (el) el.scrollIntoView({ block: scrollTo.block || "center", behavior: "auto" });
  });
  return box;
}

/** Load everything for `target` and draw it into `host`. `pane`: compact (file select, no tree). */
async function mount(model, host, target, pane) {
  const tok = (host._tok = (host._tok || 0) + 1);
  const stale = () => host._tok !== tok;
  const scroller = host.closest(".code-pane, .main");
  const prevTop = scroller ? scroller.scrollTop : 0;
  host.replaceChildren(h("p", { class: "muted", "aria-busy": "true" }, "Loading code…"));
  let files;
  try {
    files = await loadFiles(model);
  } catch (e) {
    if (!stale()) host.replaceChildren(unsupportedState(e));
    return;
  }
  if (stale()) return;
  const t = parseTarget(target);
  let links = []; // [{path,line_from,line_to,symbol,why}] for element targets
  let lineRules = []; // [{rule,text,status}] for a code line target
  if (t.kind === "element" || (t.kind === "code" && t.line)) {
    try {
      const r = await loadLinks(model, target);
      if (t.kind === "code") lineRules = r;
      else links = r;
    } catch (e) {
      /* a target the index does not know: no links */
    }
    if (stale()) return;
  }
  const byPath = (p) => files.find((f) => f.path === p);
  let path = null;
  if (t.kind === "code" && byPath(t.path)) path = t.path;
  else if (links[0] && byPath(links[0].path)) path = links[0].path;
  let userPath = pane ? lastPanePath : lastViewPath;
  if (!path) path = userPath && byPath(userPath) ? userPath : files[0]?.path;
  const ruleId = t.kind === "element" && target.startsWith("rule:") ? target.slice(5) : null;

  let autoScrollForced = false;
  async function draw(openPath, autoScroll) {
    autoScrollForced = false;
    if (pane) lastPanePath = openPath; else lastViewPath = openPath;
    let file;
    try {
      file = await loadFile(model, openPath);
    } catch (e) {
      if (!stale()) host.replaceChildren(unsupportedState(e));
      return;
    }
    if (stale()) return;
    const hl = new Set();
    for (const l of links) if (l.path === openPath) for (let n = l.line_from; n <= l.line_to; n++) hl.add(n);
    if (t.kind === "code" && t.path === openPath && t.line) hl.add(t.line);
    if (!hl.size && ruleId) for (const a of file.annotations || []) if ((a.rules || []).includes(ruleId)) hl.add(a.line);
    let scroll = null;
    if (autoScroll) {
      if (hl.size) scroll = { line: Math.min(...hl), block: t.kind === "code" ? "nearest" : "center" };
      else if (t.kind === "code" && t.path === openPath && !t.line) {
        const a = (file.annotations || []).find((x) => x.rebuilt_in || x.finding);
        if (a) scroll = { line: a.line, block: "center" };
      }
    }
    const keepTop = t.kind === "code" && t.line && t.path === openPath && !autoScrollForced;
    const src = sourceView(model, file, hl, ruleId, scroll);
    const center = h("div", { class: "code-main" },
      focusNote(model, target, t, links, lineRules, ruleId),
      h("div", { class: "code-bar" },
        pane ? fileSelect(files, openPath, (p) => draw(p, true)) : h("code", { class: "s2-mono" }, file.path),
        legend(),
      ),
      src,
    );
    const parts = [];
    if (!pane) parts.push(tree(model, files, openPath, links, (p) => draw(p, true), ruleId));
    parts.push(center);
    host.replaceChildren(...parts);
    host.classList.toggle("is-pane", !!pane);
    if (keepTop && scroller) scroller.scrollTop = prevTop;
    for (const sel of state.selection) {
      for (const el of host.querySelectorAll("[data-target]")) if (el.dataset.target === sel) el.classList.add("is-selected");
    }
  }
  await draw(path, true);
}

let lastViewPath = null;
let lastPanePath = null;

function fileSelect(files, current, onPick) {
  return h("label", { class: "code-bar-file" },
    h("span", { class: "s2-sr" }, "File"),
    h("select", { onchange: (e) => onPick(e.target.value) },
      files.map((f) => h("option", { value: f.path, selected: f.path === current }, f.path))));
}

function tree(model, files, current, links, onPick, ruleId) {
  const serves = new Set(links.map((l) => l.path));
  const dirs = new Map();
  for (const f of files) {
    const i = f.path.lastIndexOf("/");
    const d = i < 0 ? "" : f.path.slice(0, i);
    if (!dirs.has(d)) dirs.set(d, []);
    dirs.get(d).push(f);
  }
  return h("nav", { class: "code-tree", "aria-label": `Source files (${files.length})` },
    h("h2", { class: "s2-section-title" }, plural(files.length, "file")),
    [...dirs].map(([d, fs]) => h("div", {},
      d ? h("p", { class: "code-tree-dir" }, d + "/") : null,
      fs.map((f) => {
        const shown = (f.rules || []).slice(0, 5);
        return h("div", { class: ["code-tree-file", f.path === current && "is-open", serves.has(f.path) && "serves"] },
          h("button", {
            type: "button", class: "code-tree-name", "aria-current": f.path === current ? "true" : null,
            title: serves.has(f.path) ? "Holds code for the selection" : f.path,
            onclick: () => onPick(f.path),
          }, f.path.split("/").pop()),
          h("div", { class: "code-rules" },
            shown.map((id) => h("button", {
              type: "button", class: ["code-rule-chip", ruleId === id && "is-on"], onclick: () => selectRule(id),
              "aria-label": `Rule ${id}: select it`,
            }, id)),
            (f.rules || []).length > shown.length
              ? h("span", { class: "code-rule-more" }, `+${f.rules.length - shown.length}`) : null),
        );
      }),
    )),
  );
}

function focusNote(model, target, t, links, lineRules, ruleId) {
  if (t.kind === "none") return null;
  const rules = ruleById(model);
  if (t.kind === "code") {
    if (!t.line) return h("div", { class: "code-focus" }, h("p", {}, `Showing ${t.path}.`));
    return h("div", { class: "code-focus", "aria-live": "polite" },
      h("p", {}, h("strong", {}, `Line ${t.line}`), lineRules.length ? " serves:" : " serves no rule in the spec."),
      lineRules.map((r) => h("p", {}, h("strong", {}, r.rule), " ", String(r.text || "").replace(/[*`]/g, ""))),
    );
  }
  const name = ruleId ? `${ruleId}: ${String(rules.get(ruleId)?.text || "").replace(/[*`]/g, "")}` : target;
  if (!links.length) {
    return h("div", { class: "code-focus", role: "status" },
      h("p", {}, icon("question"), " ", h("strong", {}, "No code found "), `for ${name}`),
      h("p", { class: "muted" }, "The index links no code to it. That is a gap, not a bug in the view."));
  }
  return h("div", { class: "code-focus", "aria-live": "polite" },
    h("p", {}, h("strong", {}, name)),
    links.map((l) => h("p", { class: "muted" }, h("code", { class: "s2-mono" }, `${l.symbol} ${l.path}:${l.line_from}-${l.line_to}`), ` ${l.why || ""}`)),
  );
}

// ------------------------------------------------------------------ exports

/** The code pane of the lens: the code that serves `target` (a target string or null). */
export async function renderPane(model, root, target) {
  if (!target) {
    add(root, h("div", { class: "empty-state code-pane-empty s2-empty" },
      h("h2", {}, "Code"),
      h("p", { class: "muted" }, "Select a rule or a cell to see its code."),
    ));
    return;
  }
  const host = h("div", { class: "code-view is-pane" });
  root.append(host);
  await mount(model, host, target, true);
}

async function noCodeList(model) {
  let files;
  try {
    files = await loadFiles(model);
  } catch (e) {
    return null;
  }
  const covered = new Set(files.flatMap((f) => f.rules || []));
  const missing = (model.rules || []).filter((r) => r.status !== "context" && !covered.has(r.id));
  if (!missing.length) return null;
  return h("details", { class: "code-nocode", open: missing.length <= 12 },
    h("summary", {}, icon("question"), `No code found for ${plural(missing.length, "rule")}`),
    h("div", { class: "chip-row" }, missing.map((r) => h("button", {
      type: "button", class: "code-rule-chip code-nocode-chip",
      title: String(r.text).replace(/[*`]/g, ""), onclick: () => selectRule(r.id),
    }, r.id))),
  );
}

export async function render(model, root) {
  add(root, h("header", { class: "view-head" },
    h("h1", {}, "Code lens"),
    h("p", { class: "lede" }, "The source files behind the spec, read only. Chips in the gutter name the rules a line serves; changes to code come only through a rebuild."),
  ));
  const top = h("div");
  const host = h("div", { class: "code-view" });
  add(root, top, host);
  noCodeList(model).then((el) => { if (el) top.append(el); });
  // Later selection changes re-mount in place (the shell does not refresh a non-lensed view).
  const unsub = subscribe((st, patch) => {
    if (!root.isConnected) return unsub();
    if ("selection" in patch || "codeTarget" in patch) mount(model, host, currentTarget(), false);
  });
  await mount(model, host, currentTarget(), false);
}
