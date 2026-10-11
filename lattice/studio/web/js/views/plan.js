// Evidence > Build > Plan: build the whole spec as an execution graph (API v1.9).
//
// Two parts, both mounted by views/areas.js (the Build view):
//   plannerCard()    step 1, "Plan the build": scope, capacity, Create plan, and the plans list.
//   renderPlanView() one plan as a left-to-right graph (nodes in level columns), what Studio
//                    corrected, edits before the run, Approve and Run, then the live run.
//
// A plan is a record on the server (GET /api/plan/{id}); this module keeps the one in view in
// state.planRun = {id, record} and polls it every second while it is planning or running.
// Polls end by themselves when the project switches (api.epoch) or the view goes away.
//
// v1.10 (resume) and v1.11 (level repair): a plan that stopped (interrupted, partial, failed,
// stopped) shows what happened, what a run will skip and run, and ONE button by resume.kind
// (continue, land, retry, repair). A level that failed its check after the merge shows who is
// held responsible, the targets still failing and the repair attempts.

import { h, icon, add, plural, fmtTime } from "../util.js";
import { api, sleep } from "../api.js";
import { state, update, subscribe, canPlan, currentProject, PLAN_WHY } from "../store.js";
import { mountGraph } from "../graph.js";
import { rebuildCard, pollRebuild, buildRefusal } from "./rebuilds.js";
import { toast } from "../tray.js";

const LIVE = new Set(["planning", "running"]);
const EDITABLE = new Set(["ready", "approved"]);
const RESUMABLE = new Set(["interrupted", "partial", "failed", "stopped"]);
const UNSAFE_CODES = new Set(["branch_checked_out", "branch_protected"]); // a refusal the owner may confirm
const MAX_LISTED = 5; // targets shown before "+N more"
const MAX_CAP = 8;
const DEFAULT_CAP = 2;

const PLAN_STATUS = {
  planning: { word: "Planning", icon: null },
  ready: { word: "Ready to approve", icon: "pending" },
  approved: { word: "Approved, not run yet", icon: "check" },
  running: { word: "Running", icon: null },
  passed: { word: "Passed", icon: "check" },
  partial: { word: "Partly built", icon: "triangle" },
  failed: { word: "Failed", icon: "diamond" },
  stopped: { word: "Stopped", icon: "dash" },
  interrupted: { word: "Interrupted", icon: "dash" },
};
const NODE_STATUS = {
  pending: { word: "Waiting", icon: "pending" },
  running: { word: "Running", icon: null },
  passed: { word: "Passed", icon: "check" },
  gave_up: { word: "Gave up", icon: "diamond" },
  blocked: { word: "Blocked", icon: "lock" },
  merge_conflict: { word: "Merge conflict", icon: "triangle" },
  regressed: { word: "Regressed", icon: "triangle" },
  stopped: { word: "Stopped", icon: "dash" },
  repairing: { word: "Repairing", icon: null },
  interrupted: { word: "Interrupted", icon: "dash" },
};
const TIER = {
  fast: { word: "Fast", icon: "sparkle", hint: "a small, quick model for mechanical work" },
  mid: { word: "Mid", icon: "dot", hint: "the everyday model" },
  frontier: { word: "Frontier", icon: "diamond", hint: "the strongest model, for the parts that need judgment" },
};

const num = (n) => Number(n || 0).toLocaleString("en-US");
const kfmt = (n) => {
  const v = Number(n) || 0;
  return v >= 10000 ? `${Math.round(v / 1000)}k` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v);
};
const secs = (s) => {
  const v = Number(s) || 0;
  return v >= 60 ? `${Math.floor(v / 60)}m ${String(Math.round(v % 60)).padStart(2, "0")}s` : `${v < 10 ? v.toFixed(1) : Math.round(v)}s`;
};
const money = (n) => {
  const v = Number(n) || 0;
  return v > 0 ? `$${v < 0.01 ? v.toFixed(4) : v.toFixed(2)}` : "$0";
};
const sentence = (t) => {
  const x = String(t || "").trim();
  return x ? x.charAt(0).toUpperCase() + x.slice(1) + (/[.!?]$/.test(x) ? "" : ".") : "";
};

// ------------------------------------------------------------------ the record in view

const following = new Set();
const cache = { base: null, plans: null };

function resetCache() {
  if (cache.base !== api.base()) {
    cache.base = api.base();
    cache.plans = null;
  }
}

/** The plan in view when it is planning or running (the tray and the navigator ask). */
export function planLive() {
  const rec = state.planActive;
  return rec && LIVE.has(rec.status) ? rec : null;
}

/** "Building: level 2 of 5 · 2 nodes running" */
export function planWords(rec) {
  if (rec.status === "planning") return "Planning the build";
  const total = (rec.levels || []).length || 1;
  const runs = rec.level_runs || [];
  const open = runs.filter((r) => r.status === "running").pop();
  const done = new Set(runs.filter((r) => r.status === "passed").map((r) => r.level));
  const level = open ? open.level + 1 : Math.min(total, done.size + 1);
  const running = (rec.nodes || []).filter((n) => n.status === "running").length;
  const tail = running ? plural(running, "node") + " running" : "checking the merged level";
  return `Building: level ${level} of ${total} · ${tail}`;
}

function setRecord(r) {
  if (!r || !r.id) return;
  const patch = {};
  const cur = state.planRun;
  // planRun is the plan in view; planActive the one planning or running (the tray follows it)
  const inView = !state.planView || state.planView === r.id || !cur || cur.id !== state.planView;
  if (inView && !(cur && cur.id === r.id && JSON.stringify(cur.record) === JSON.stringify(r))) {
    patch.planRun = { id: r.id, record: r };
  }
  const act = state.planActive;
  if (LIVE.has(r.status)) {
    if (!act || act.id !== r.id || JSON.stringify(act) !== JSON.stringify(r)) patch.planActive = r;
  } else if (act && act.id === r.id) {
    patch.planActive = null;
  }
  if (Object.keys(patch).length) update(patch);
}

/** Poll GET /api/plan/{id} every second while it is planning or running. */
function followPlan(id) {
  const key = `${api.base()}|${id}`;
  if (following.has(key)) return;
  following.add(key);
  const ep = api.epoch();
  (async () => {
    let fails = 0;
    try {
      while (ep === api.epoch()) {
        try {
          const r = await api.planGet(id);
          if (ep !== api.epoch()) return;
          fails = 0;
          setRecord(r);
          cache.plans = null;
          if (!LIVE.has(r.status)) {
            // the build ended: the top bar, the tray and the areas follow the new state
            api.model().then((m) => update({ model: m, connected: true })).catch(() => {});
            return;
          }
        } catch (e) {
          if (ep !== api.epoch()) return;
          if (e.status === 404 || ++fails > 5) return;
        }
        await sleep(1000);
      }
    } finally {
      following.delete(key);
    }
  })();
}

/** One watcher per project (app.js starts it): finds a plan that is planning or running
 *  (after a reload, or one started elsewhere) so the tray and the navigator show it. */
export async function watchPlans(ep) {
  while (ep === api.epoch()) {
    if (canPlan() && !planLive()) {
      try {
        const list = await api.planList();
        if (ep !== api.epoch()) return;
        if (Array.isArray(list)) {
          cache.plans = list;
          const live = list.filter((p) => LIVE.has(p.status)).pop();
          if (live && !planLive()) {
            const rec = await api.planGet(live.id);
            if (ep !== api.epoch()) return;
            setRecord(rec);
            followPlan(live.id);
          }
        }
      } catch (e) {
        if (ep !== api.epoch()) return;
        if (e.status === 404 || e.unsupported) return;
      }
    }
    await sleep(6000);
  }
}

/** Create a plan and open it. Throws the server's refusal. */
export async function createPlan(body) {
  const { id } = await api.planCreate(body);
  const stub = { id, status: "planning", nodes: [], edges: [], levels: [], validation: null, scope: {}, target_branch: "", totals: {} };
  update({ planView: id, planRun: { id, record: stub }, planActive: stub });
  cache.plans = null;
  followPlan(id);
  return id;
}

// ------------------------------------------------------------------ words

/** A node's title; a repair node's title ("Repair of level 1") counts levels from 0 too. */
function nodeTitle(n) {
  const t = n.title || n.id;
  return n.kind === "repair" ? serverLevels(t) : t;
}

function titleOf(rec, id) {
  const n = (rec.nodes || []).find((x) => x.id === id);
  return n ? nodeTitle(n) : id;
}

function overlapWords(o) {
  const parts = [];
  const t = o?.tables || [];
  const p = o?.packages || [];
  const f = o?.files || [];
  if (t.length) parts.push(`the ${t.join(", ")} table${t.length > 1 ? "s" : ""}`);
  if (p.length) parts.push(`the ${p.join(", ")} package${p.length > 1 ? "s" : ""}`);
  if (f.length) parts.push(`${f.slice(0, 3).join(", ")}${f.length > 3 ? " and more" : ""}`);
  return parts.join(" and ") || "the same files";
}

/** One plain sentence for each correction Studio made; the scaffold edges are summed up. */
function autoEditSentences(rec) {
  const edits = (rec.validation?.auto_edits || []).slice();
  const out = [];
  const t = (id) => titleOf(rec, id);
  const scaffold = edits.filter((e) => e.kind === "edge" && /scaffold/i.test(e.detail || e.from || ""));
  if (scaffold.length) {
    out.push(`The shared infrastructure runs first, so ${plural(scaffold.length, "node")} wait${scaffold.length === 1 ? "s" : ""} for it.`);
  }
  for (const e of edits) {
    if (scaffold.includes(e)) continue;
    if (e.kind === "serialise") {
      out.push(`${t(e.from)} and ${t(e.to)} both write ${overlapWords(e.overlap)}, so ${t(e.to)} waits for ${t(e.from)}.`);
    } else if (e.kind === "level") {
      out.push(`${t(e.node)} moved to level ${Number(e.to) + 1}, because at most ${rec.max_parallel} nodes run at once.`);
    } else if (e.kind === "edge" && e.from && e.to) {
      out.push(`${t(e.to)} waits for ${t(e.from)}${e.detail ? `: ${e.detail}` : ""}.`.replace(/\.\.$/, "."));
    } else if (e.kind === "gate") {
      out.push(`Studio set the check that ${e.node ? t(e.node) : "a node"} must pass; the plan cannot choose it.`);
    } else if (e.detail) {
      out.push(sentence(e.detail));
    }
  }
  return out;
}

function fallbackWords(rec) {
  const why = String(rec.fallback_reason || "");
  if (/^requested/i.test(why)) return "You asked for the safe sequential plan: each area runs after the one before it.";
  let m = /^the planner failed:\s*(.*)$/is.exec(why);
  if (m) return `The planner could not make a plan (${m[1].trim() || "it failed"}), so Studio made a safe one: each area runs after the one before it.`;
  m = /^the planner's plan was invalid twice:\s*(.*)$/is.exec(why);
  if (m) return `The planner's plan was invalid twice (${m[1].trim()}), so Studio made a safe one: each area runs after the one before it.`;
  return why ? sentence(why) : "Each area runs after the one before it.";
}

function rulesWord(n) {
  const r = n.scope?.rules;
  if (Array.isArray(r)) return r.length || n.kind !== "repair" ? plural(r.length, "rule") : "";
  if (n.scope === "all" || n.scope?.kind === "all") return "all rules";
  return n.gate?.rules && Array.isArray(n.gate.rules) ? plural(n.gate.rules.length, "rule") : "";
}

function usageIn(u) {
  return (u?.tokens_in || 0) + (u?.cache_read_input_tokens || 0) + (u?.cache_creation_input_tokens || 0);
}

function nodeFacts(n) {
  if (n.nothing_to_build) return "";
  const u = n.status === "running" && n.live ? n.live : n.usage;
  const bits = [];
  const tokens = usageIn(u) + (u?.tokens_out || 0);
  if (tokens) bits.push(`${kfmt(tokens)} tokens`);
  if (n.wall_s) bits.push(secs(n.wall_s));
  if (n.lines_changed) bits.push(`${num(n.lines_changed)} lines`);
  if (n.repairs) bits.push(plural(n.repairs, "repair"));
  return bits.join(" · ");
}

function nodeStatusWord(n) {
  if (n.nothing_to_build && n.status === "passed") return "Nothing to build";
  if (n.status === "running") {
    const r = n.live?.rounds;
    return r ? `Running · round ${r}` : "Running";
  }
  if (n.status === "repairing" && n.repairs) return `Repairing · repair ${n.repairs}`;
  return (NODE_STATUS[n.status] || NODE_STATUS.pending).word;
}

/** Why a node stopped and what to do next, in plain words ("" while it is fine). */
function nodeNext(rec, n) {
  const live = LIVE.has(rec.status);
  const t = (id) => titleOf(rec, id);
  if (n.status === "passed") return n.nothing_to_build ? "Its checks already pass on the target branch, so no agent ran for it." : "";
  if (n.status === "running" || (n.status === "pending" && live)) return "";
  if (n.status === "repairing") return "The merged level failed its check, so this node is getting a repair round. The plan goes on when the level passes.";
  if (n.status === "interrupted") return "Studio stopped while this node was running. Next: continue the plan; it picks the node up where it stopped when its working copy is still there, else builds it again from the level's start.";
  if (n.status === "gave_up") {
    const why = n.error?.message || n.error?.kind;
    const rounds = n.rounds ? ` after ${plural(n.rounds, "round")}` : "";
    return `It gave up${rounds}${why ? `: ${why}` : ": some of its checks still fail"}. Next: open its build below to see what still fails, then run again to try it from the current tip.`;
  }
  if (n.status === "blocked") {
    const by = (n.blocked_by || []).map(t).join(", ");
    return `It did not start because ${by || "a node it waits for"} did not pass. Next: run again; it retries ${by || "that node"} first, then this one.`;
  }
  if (n.status === "merge_conflict") return "Its changes conflicted with another node's at the end of its level, so they were left out. Next: run again; it builds this node again on top of what was merged.";
  if (n.status === "regressed") {
    if (rec.error?.kind === "repair_exhausted" && (rec.error.nodes || []).includes(n.id)) {
      return "The merged level still failed its check after the repair attempts, so this node was not landed. Next: fix with the agent again; the node is not built over.";
    }
    return "The full check after its level found checks that used to pass now failing, so nothing from the level was merged. Next: run again to retry it.";
  }
  if (n.status === "stopped") return "The plan was stopped before this node finished. Next: run again to continue from here.";
  if (n.status === "pending") return "It never started. Next: run again.";
  return "";
}

// ------------------------------------------------------------------ resume and repair words

/** The server counts levels from 0 in its prose (resume.note, error.message, timeline events),
 *  like level_runs[].level and resume.from_level; the views say Level 1, 2, ... */
export function serverLevels(text) {
  return String(text || "").replace(/\b(level)\s+(\d+)\b/gi, (_, w, n) => `${w} ${Number(n) + 1}`);
}

const levelNo = (i) => Number(i) + 1;

/** The one button of a stopped plan, by resume.kind (from_level is 0-based). */
export function resumeLabel(resume) {
  const L = resume && Number.isInteger(resume.from_level) ? ` level ${resume.from_level + 1}` : "";
  switch (resume && resume.kind) {
    case "land": return `Land${L || " the level"}`;
    case "retry": return "Retry failed nodes";
    case "repair": return "Fix with the agent again";
    default: return L ? `Continue from${L}` : "Continue";
  }
}

/** "regression check · 12 scenarios": how a level's check ran (gate.mode, gate.selected). */
export function gateWords(gate) {
  if (!gate) return "";
  const n = gate.selected;
  const sel = n !== null && n !== undefined && Number.isFinite(Number(n)) ? ` · ${plural(Number(n), "scenario")}` : "";
  return `${gate.mode === "regression" ? "regression" : "full"} check${sel}`;
}

/** How the level checks and repairs are set up (gate_config of a plan, plan_defaults of a project). */
export function gateConfigWords(cfg) {
  if (!cfg) return "";
  const out = [cfg.level_gate === "regression"
    ? "Each level is checked against what passed before and the scenarios built so far; the last level runs the full check."
    : "Each level ends with the full check."];
  const n = Number(cfg.level_repair_attempts);
  if (cfg.level_repair_attempts !== undefined && Number.isFinite(n)) {
    out.push(n > 0 ? `A level that fails it gets up to ${plural(n, "repair attempt")}.` : "A level that fails it stops the plan.");
  }
  const scoped = cfg.scoped_gate_args || cfg.scoped_gate;
  if (Array.isArray(scoped) && scoped.length) out.push("Nodes are checked on their own scope plus what passed before.");
  return out.join(" ");
}

/** The first `n` of a list and how many were left out. */
export function capList(items, n = MAX_LISTED) {
  const all = Array.isArray(items) ? items : [];
  return { shown: all.slice(0, n), more: Math.max(0, all.length - n) };
}

function planError(r) {
  const e = r.error || {};
  const k = e.kind || "";
  if (k === "baseline") return "The first check of the target branch could not run, so nothing was built. Fix the gate commands, then run again.";
  if (k === "repair_exhausted") return `Level ${levelNo(e.level)} still fails its check after the repair attempts.`;
  return sentence(serverLevels(e.message || e.detail || "The plan stopped with an error."));
}

/** A refusal in the server's words, without the API hint (the page offers that choice itself). */
export function refusalWords(text) {
  const t = serverLevels(text).replace(/\s*\(or send confirm_unsafe_branch[^)]*\)\s*$/i, "");
  return /[.!?]$/.test(t) ? t : `${t}.`;
}

function interruptedWhy(r) {
  const k = String(r.interrupted_reason || "");
  if (!k || k === "studio_restarted") return "Studio was restarted while the plan was running";
  return sentence(k.replace(/_/g, " ")).replace(/\.$/, "");
}

/** What happened to a plan that stopped, in one sentence. */
function happenedWords(r) {
  if (r.status === "interrupted" || r.interrupted) return `${interruptedWhy(r)}.`;
  if (r.error) return planError(r);
  const v = r.resume;
  if (v && v.kind === "land" && Number.isInteger(v.from_level)) {
    return `Level ${v.from_level + 1} passed its check but is not on ${r.target_branch || "the target branch"} yet.`;
  }
  return {
    stopped: "The plan was stopped.",
    partial: "The plan ended with some nodes not built.",
    failed: "The plan did not build anything.",
  }[r.status] || "";
}

function nodeTitles(rec, ids) {
  const { shown, more } = capList((ids || []).map((id) => titleOf(rec, id)), 8);
  return shown.length ? shown.join(", ") + (more ? ` and ${more} more` : "") : "";
}

const REPAIR_OUTCOME = {
  passed: "Fixed it",
  failed: "Still failing",
  verify_failed: "The repair did not pass its own check",
};
const REPAIR_BY = {
  scope: "its rules link the failing check",
  files: "its commit changed the failing file",
  repair_node: "no node owns it, so a repair node takes it",
};

/** The level run a repair banner describes: the exhausted one, else the one being repaired. */
function repairRun(r) {
  const runs = (r.level_runs || []).filter((x) => !x.superseded && (x.repairs || []).length);
  if (r.error?.kind === "repair_exhausted") {
    return runs.filter((x) => x.level === r.error.level).pop() || runs.filter((x) => x.repair_pending).pop() || null;
  }
  const repairing = (r.nodes || []).filter((n) => n.status === "repairing").map((n) => n.id);
  return runs.filter((x) => x.status === "running" || (x.nodes || []).some((id) => repairing.includes(id))).pop() || null;
}

function sumUsage(list) {
  const out = { tokens: 0, cost: 0, any: false };
  for (const u of list) {
    if (!u) continue;
    out.any = true;
    out.tokens += usageIn(u) + (u.tokens_out || 0);
    out.cost += Number(u.cost_usd_list_estimate) || 0;
  }
  return out;
}

/** What the repair rounds cost: the nodes' repair_usage, else the usage of each repair entry. */
function repairUsage(r) {
  const direct = sumUsage((r.nodes || []).map((n) => n.repair_usage));
  if (direct.any) return direct;
  return sumUsage((r.level_runs || []).flatMap((lr) => (lr.repairs || []).flatMap((x) => (x.nodes || []).map((n) => n.usage))));
}

function repairBanner(r) {
  const lr = repairRun(r);
  if (!lr) return null;
  const reps = lr.repairs || [];
  const last = reps[reps.length - 1] || {};
  const exhausted = r.error?.kind === "repair_exhausted";
  const attributed = Object.assign({}, ...reps.map((x) => x.attributed || {}));
  const stillFailing = exhausted && (r.error.targets || []).length ? r.error.targets
    : (last.still_failing || []).length ? last.still_failing
    : (last.targets || lr.gate?.failing || []);
  const { shown, more } = capList(stillFailing);
  const held = [...new Set(stillFailing.flatMap((t) => attributed[t]?.nodes || []))];
  const heldIds = held.length ? held : (exhausted && r.error.nodes) || lr.merged || [];
  const attempt = last.attempt && last.of && last.attempt <= last.of ? `${last.attempt} of ${last.of}` : last.attempt ? String(last.attempt) : "";
  const head = exhausted
    ? `Level ${levelNo(lr.level)} still fails after ${plural(reps.length, "repair attempt")}`
    : `Level ${levelNo(lr.level)} failed its check after merging, and is being repaired`;
  const box = h("section", { class: ["pv-repair", exhausted && "is-exhausted"], "aria-label": "Level repair", role: exhausted ? null : "status" });
  box.append(h("p", { class: "pv-repair-head" },
    exhausted ? icon("triangle") : h("span", { class: "spinner", "aria-hidden": "true" }), h("strong", {}, head)));
  const facts = [];
  if (heldIds.length) facts.push(h("span", {}, "Held responsible: ", h("strong", {}, nodeTitles(r, heldIds))));
  if (attempt) facts.push(h("span", {}, exhausted ? `Last attempt ${attempt}` : `Attempt ${attempt}`));
  if (facts.length) box.append(h("p", { class: "pv-repair-facts small" }, facts.map((f, i) => [i ? " · " : "", f])));
  if (shown.length) {
    box.append(h("p", { class: "pv-repair-sub muted small" }, exhausted ? "Still failing" : "Failing on the merged result"),
      h("ul", { class: "pv-repair-targets" }, [
        ...shown.map((t) => h("li", {}, h("span", { class: "s2-mono" }, t),
          attributed[t]?.nodes?.length
            ? h("span", { class: "muted small" }, ` held on ${nodeTitles(r, attributed[t].nodes)}${REPAIR_BY[attributed[t].by] ? `: ${REPAIR_BY[attributed[t].by]}` : ""}`)
            : null)),
        more ? h("li", { class: "muted small" }, `+${more} more`) : null,
      ]));
  }
  box.append(h("p", { class: "pv-repair-sub muted small" }, "Repair history"),
    h("ol", { class: "pv-repair-hist" }, reps.map((x) => {
      const toks = sumUsage((x.nodes || []).map((n) => n.usage));
      const wall = Number(x.wall_s) || (x.nodes || []).reduce((a, n) => a + (Number(n.wall_s) || 0), 0);
      const bits = [REPAIR_OUTCOME[x.outcome] || "Running"];
      if ((x.nodes || []).length) bits.push(nodeTitles(r, x.nodes.map((n) => n.id)));
      if (toks.tokens) bits.push(`${kfmt(toks.tokens)} tokens`);
      if (wall) bits.push(secs(wall));
      if (x.outcome === "failed" && (x.still_failing || []).length) bits.push(`${plural(x.still_failing.length, "check")} still failing`);
      const of = x.of && x.attempt <= x.of ? ` of ${x.of}` : "";
      return h("li", {}, h("span", { class: "pv-level-n" }, `Attempt ${x.attempt}${of}`), h("span", {}, bits.join(" · ")));
    })));
  return box;
}

// ------------------------------------------------------------------ small pieces

function planChip(status) {
  const st = PLAN_STATUS[status] || PLAN_STATUS.ready;
  return h("span", { class: ["rb-status", `is-${status}`, "pv-chip"] },
    st.icon ? icon(st.icon) : h("span", { class: "spinner", "aria-hidden": "true" }), st.word);
}

function nodeChip(n) {
  const st = NODE_STATUS[n.status] || NODE_STATUS.pending;
  return h("span", { class: ["pv-node-status", `is-${n.status}`] },
    st.icon ? icon(st.icon) : h("span", { class: "spinner", "aria-hidden": "true" }), nodeStatusWord(n));
}

function tierMark(tier) {
  const t = TIER[tier] || TIER.mid;
  return h("span", { class: "pv-tier", title: t.hint }, icon(t.icon), t.word);
}

const kindWord = { scaffold: "Shared infrastructure", area: "Area", merge: "Merge", integration: "Final integration", repair: "Repair of a level" };

/** Replace `el`'s children and keep keyboard focus on the same control (data-fk). */
function redraw(el, fn) {
  const fk = document.activeElement && el.contains(document.activeElement) ? document.activeElement.dataset?.fk : null;
  el.replaceChildren();
  fn(el);
  if (fk) el.querySelector(`[data-fk="${CSS.escape(fk)}"]`)?.focus({ preventScroll: true });
}

// ------------------------------------------------------------------ step 1: plan the build

const pui = { base: null, scope: "all", areas: new Set(), cap: DEFAULT_CAP, capSet: false, fallback: false, error: null, creating: false };

/** The project's own default capacity (GET /api/projects[].plan_defaults, API v1.11). */
function planDefaults() {
  return currentProject()?.plan_defaults || null;
}

function defaultCap() {
  const n = Number(planDefaults()?.max_parallel);
  return Number.isInteger(n) && n >= 1 && n <= MAX_CAP ? n : DEFAULT_CAP;
}

function resetPlanner() {
  if (pui.base === api.base()) return;
  pui.base = api.base();
  pui.scope = "all";
  pui.areas = new Set();
  pui.cap = defaultCap();
  pui.capSet = false;
  pui.fallback = false;
  pui.error = null;
  pui.creating = false;
}

const listWord = (s) => (PLAN_STATUS[s] || { word: s }).word;

/** The "Plan the build" card and the plans list. `getCtx()` -> {areas, branch: {label, body,
 *  unsafe, confirmed, error}|null, busy: string, loading: bool}. Returns {el, sync()}. */
export function plannerCard(getCtx, { pickBranch } = {}) {
  resetPlanner();
  resetCache();
  const ep = api.epoch();
  const el = h("section", { class: "pv-start", "aria-labelledby": "pv-start-title" });
  const body = h("div", { class: "pv-start-body" });
  const listEl = h("div", { class: "pv-plans" });
  const why = h("p", { class: "muted small pv-why", role: "status", "aria-live": "polite" });
  let areaBox = null;
  let areasFor = null;
  let createBtn = null;
  let branchLabel = null;
  let capOut = null;
  let errEl = null;
  let defaultsEl = null;

  if (!canPlan()) {
    add(el, h("h2", { id: "pv-start-title", class: "s2-section-title" }, "Plan the build"),
      h("p", { class: "muted" }, "Build plans are not available for this project."), h("p", { class: "muted small" }, PLAN_WHY));
    return { el, sync() {} };
  }

  function areaList() {
    const c = getCtx();
    const list = (c.areas || []).filter((a) => a.id !== "all");
    areaBox.replaceChildren();
    if (!list.length) {
      areaBox.append(h("p", { class: "muted small" }, "The spec has no sections to choose from. Plan everything."));
      return;
    }
    areaBox.append(h("ul", { class: "pv-areas" }, list.map((a) => {
      const box = h("input", {
        type: "checkbox", "data-fk": `area:${a.id}`, id: `pv-area-${a.id}`,
        onchange: (e) => { if (e.target.checked) pui.areas.add(a.id); else pui.areas.delete(a.id); sync(); },
      });
      box.checked = pui.areas.has(a.id);
      return h("li", {}, h("label", { for: `pv-area-${a.id}` }, box, h("span", {}, a.title),
        h("span", { class: "muted small" }, ` ${plural(a.rules.length, "rule")}`)));
    })));
  }

  function scopeBody() {
    if (pui.scope === "all") return "all";
    return { areas: [...pui.areas] };
  }

  async function create() {
    const c = getCtx();
    if (pui.creating || c.busy || !c.branch || c.branch.error) return;
    if (pui.scope === "areas" && !pui.areas.size) return;
    pui.creating = true;
    pui.error = null;
    sync();
    const req = { scope: scopeBody(), max_parallel: pui.cap };
    if (c.branch.body) req.branch = c.branch.body;
    if (c.branch.unsafe && c.branch.confirmed) req.confirm_unsafe_branch = true;
    if (pui.fallback) req.planner = "fallback";
    try {
      await createPlan(req);
      pui.creating = false;
    } catch (e) {
      pui.creating = false;
      if (ep !== api.epoch()) return;
      pui.error = buildRefusal(e, { branch: c.branch.body?.name, scopeKind: pui.scope === "all" ? "all" : "area" })
        .replace(/^The build could not start/, "The plan could not be made");
      sync();
    }
  }

  function sync() {
    if (!el.isConnected && !areaBox) return;
    const c = getCtx();
    if (!pui.capSet) pui.cap = defaultCap(); // until the owner picks one, the project's default
    if (defaultsEl) {
      const words = gateConfigWords(planDefaults());
      defaultsEl.textContent = words;
      defaultsEl.hidden = !words;
    }
    if (areaBox && areasFor !== c.areas) {
      areasFor = c.areas;
      areaList();
    }
    if (areaBox) areaBox.hidden = pui.scope !== "areas";
    if (branchLabel) branchLabel.textContent = c.branch ? c.branch.label || "" : "…";
    if (capOut) capOut.textContent = String(pui.cap);
    let reason = "";
    if (c.loading) reason = "";
    else if (c.busy) reason = c.busy;
    else if (!c.branch) reason = "";
    else if (c.branch.error) reason = c.branch.error;
    else if (c.branch.unsafe && !c.branch.confirmed) reason = `${c.branch.label} is protected or checked out. Confirm the update in the target branch panel first.`;
    else if (pui.scope === "areas" && !pui.areas.size) reason = "Choose at least one area, or plan everything.";
    if (createBtn) {
      createBtn.disabled = Boolean(reason) || c.loading || pui.creating;
      createBtn.lastChild.textContent = pui.creating ? "Planning…" : "Create plan";
      if (reason) createBtn.title = reason;
      else createBtn.removeAttribute("title");
    }
    why.textContent = reason;
    why.hidden = !reason;
    if (errEl) {
      errEl.textContent = pui.error || "";
      errEl.hidden = !pui.error;
    }
  }

  const scopeRadio = (value, label) => {
    const input = h("input", {
      type: "radio", name: "pv-scope", value, "data-fk": `scope:${value}`,
      onchange: () => { pui.scope = value; sync(); },
    });
    input.checked = pui.scope === value;
    return h("label", { class: "bv-opt" }, input, h("span", {}, label));
  };

  areaBox = h("div", { class: "pv-area-box", hidden: true });
  branchLabel = h("span", { class: "s2-mono" }, "…");
  capOut = h("output", { class: "bv-rounds", "aria-live": "polite" }, String(pui.cap));
  errEl = h("p", { class: "bv-field-err small", role: "alert", hidden: true });
  defaultsEl = h("p", { class: "muted small pv-gates", hidden: true });
  const fb = h("input", { type: "checkbox", id: "pv-fallback", "data-fk": "fallback", onchange: (e) => { pui.fallback = e.target.checked; } });
  fb.checked = pui.fallback;
  createBtn = h("button", { type: "button", class: "btn btn-primary", disabled: true, onclick: create }, icon("play"), h("span", {}, "Create plan"));

  add(body,
    h("fieldset", { class: "pv-fieldset" },
      h("legend", {}, "What to plan"),
      scopeRadio("all", "Everything in the spec"),
      scopeRadio("areas", "Chosen areas"),
      areaBox),
    h("div", { class: "pv-row" },
      h("span", { class: "muted" }, "Target branch"), branchLabel,
      pickBranch ? h("button", { type: "button", class: "btn btn-quiet", onclick: pickBranch, "data-fk": "pick-branch" }, "Change…") : null),
    h("div", { class: "pv-row", role: "group", "aria-label": "Run at most this many nodes at once" },
      h("span", { class: "muted" }, "Run at most"),
      h("button", { type: "button", class: "btn btn-icon", "aria-label": "Fewer nodes at once", "data-fk": "cap-minus",
        onclick: () => { pui.cap = Math.max(1, pui.cap - 1); pui.capSet = true; sync(); } }, icon("minus")),
      capOut,
      h("button", { type: "button", class: "btn btn-icon", "aria-label": "More nodes at once", "data-fk": "cap-plus",
        onclick: () => { pui.cap = Math.min(MAX_CAP, pui.cap + 1); pui.capSet = true; sync(); } }, icon("plus")),
      h("span", {}, "nodes at once"),
      h("span", { class: "muted small" }, "A machine limit. What can run together is decided by the plan.")),
    h("label", { class: "toggle pv-fallback", for: "pv-fallback" }, fb,
      h("span", {}, "Use the safe sequential plan (no planner agent)")),
    defaultsEl,
    h("div", { class: "pv-row" }, createBtn),
    why, errEl);

  add(el,
    h("h2", { id: "pv-start-title", class: "s2-section-title" }, "Plan the build"),
    h("p", { class: "lede" }, "A plan splits the build into steps, shows which can run together and which must wait, and lets you review it before anything is built."),
    body, listEl);

  // the plans list
  function drawList() {
    redraw(listEl, (box) => {
      const list = (cache.plans || []).slice().reverse().slice(0, 8);
      if (!list.length) return;
      box.append(h("h3", { class: "s2-section-title" }, "Plans"),
        h("ul", { class: "rb-history pv-plan-list" }, list.map((p) => h("li", { class: ["rb-hist-row", p.id === state.planRun?.id && "is-current"] },
          h("span", { class: "s2-mono" }, p.id),
          h("div", {},
            h("button", { type: "button", class: "s2-quiet-btn", "data-fk": `open:${p.id}`, onclick: () => openPlan(p.id) },
              `${p.scope?.kind === "all" || !p.scope?.title ? "Everything" : p.scope.title} onto ${p.target_branch || "?"}`),
            h("p", { class: "rb-meta" }, `${plural((p.nodes || []).length, "node")} · ${plural(p.levels || 0, "level")} · ${p.source === "fallback" ? "safe sequential plan" : "planned by an agent"}`)),
          planChip(p.status)))));
    });
  }
  async function loadList() {
    try {
      const l = await api.planList();
      if (ep !== api.epoch() || !el.isConnected) return;
      cache.plans = Array.isArray(l) ? l : [];
      drawList();
    } catch (e) {
      /* the list is a convenience: the card still works without it */
    }
  }
  if (cache.plans) drawList();
  loadList();
  const unsub = subscribe((st, patch) => {
    if (!el.isConnected) { unsub(); return; }
    if ("planRun" in patch) {
      const st2 = st.planRun?.record?.status;
      if (st2 && !LIVE.has(st2)) loadList();
    }
  });
  sync();
  return { el, sync };
}

export function openPlan(id) {
  const cur = state.planRun;
  update({ planView: id, planRun: cur && cur.id === id ? cur : null });
}

// ------------------------------------------------------------------ the plan view

const vui = { base: null, selected: null, feedback: "", listOpen: true, issues: null, note: null, busy: false, confirm: false, branches: null, newBranch: "", refusal: null, historyOpen: false };

function resetView() {
  if (vui.base === api.base()) return;
  vui.base = api.base();
  vui.selected = null;
  vui.feedback = "";
  vui.issues = null;
  vui.note = null;
  vui.busy = false;
  vui.confirm = false;
  vui.branches = null;
  vui.refusal = null;
}

export function renderPlanView(root, ctx = {}) {
  resetView();
  resetCache();
  const ep = api.epoch();
  const id = state.planView;
  const back = ctx.back || (() => update({ planView: null }));
  let graph = null;
  let stopBuildPoll = null;
  let buildShown = null;

  const head = h("header", { class: "view-head pv-head" });
  const bar = h("div", { class: "pv-bar" });
  const body = h("div", { class: "pv-body" });
  add(root, head, bar, body);

  const sections = {
    meta: h("section", { class: "pv-meta", "aria-label": "About this plan" }),
    actions: h("section", { class: "pv-actions", "aria-label": "Approve and run", "aria-live": "polite" }),
    graph: h("div", { class: "pv-graph-wrap" }),
    levels: h("section", { class: "pv-levels", "aria-label": "Levels" }),
    side: h("aside", { class: "pv-side", "aria-label": "Selected node" }),
    list: h("section", { class: "pv-list", "aria-label": "The plan as a list" }),
    history: h("section", { class: "pv-history", "aria-label": "What happened" }),
    plan: h("section", { class: "pv-planedit", "aria-label": "Plan settings" }),
  };
  const graphHost = h("div", { class: "pv-graph", "aria-label": "Plan graph" });
  const legend = h("p", { class: "pv-legend muted small" });
  sections.graph.append(graphHost, legend);
  const sideInfo = h("div", { class: "pv-side-info" });
  const sideEdit = h("div", { class: "pv-side-edit" });
  const sideBuild = h("div", { class: "pv-side-build" });
  sections.side.append(sideInfo, sideEdit, sideBuild);

  add(body,
    sections.meta,
    sections.actions,
    h("div", { class: "pv-main" }, h("div", { class: "pv-graph-col" }, sections.graph, sections.levels), sections.side),
    sections.plan,
    sections.list,
    sections.history);

  const rec = () => state.planRun?.id === id ? state.planRun.record : null;
  const live = () => !!rec() && LIVE.has(rec().status);
  const editable = () => !!rec() && EDITABLE.has(rec().status) && !vui.busy;

  // ---- header
  function drawHead() {
    const r = rec();
    redraw(head, (el) => {
      el.append(
        h("p", { class: "pv-crumb" }, h("button", { type: "button", class: "btn-link", onclick: back }, "Build"), h("span", { class: "muted" }, " / Plan ", id)),
        h("h1", {}, r?.scope?.title && r.scope.kind !== "all" ? `Plan: ${r.scope.title}` : "Plan: everything"),
        r ? h("p", { class: "pv-sub" }, planChip(r.status),
          h("span", { class: "muted" }, " onto "), h("span", { class: "s2-mono" }, r.target_branch || "?"),
          r.version ? h("span", { class: "muted" }, ` · version ${r.version}`) : null) : null);
    });
  }

  // ---- source, validation, totals
  function drawMeta() {
    const r = rec();
    redraw(sections.meta, (el) => {
      if (!r) return;
      if (r.status === "planning") {
        el.append(h("p", { class: "pv-planning", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }),
          h("strong", {}, " Planning the build…"),
          h("span", { class: "muted" }, " The planner reads the spec and decides what depends on what. This can take a minute.")));
        return;
      }
      const fallback = r.source === "fallback";
      el.append(h("div", { class: ["pv-source", fallback && "is-fallback"] },
        h("span", { class: "pv-badge" }, icon(fallback ? "lock" : "sparkle"), fallback ? "Safe sequential plan" : "Planned by an agent"),
        fallback ? h("span", { class: "pv-source-why" }, fallbackWords(r)) : null));
      const v = r.validation;
      if (v) {
        const issues = v.issues || [];
        const sentences = autoEditSentences(r);
        if (issues.length) {
          el.append(h("div", { class: "bv-warn", role: "alert" },
            h("p", {}, icon("triangle"), h("strong", {}, ` ${plural(issues.length, "problem")} to fix before this plan can run`)),
            h("ul", {}, issues.map((i) => h("li", {}, sentence(i))))));
        }
        if (sentences.length) {
          el.append(h("details", { class: "pv-fixes", open: sentences.length <= 6 ? "" : null },
            h("summary", {}, `Studio adjusted the plan in ${plural(sentences.length, "way")}`),
            h("ul", {}, sentences.map((t) => h("li", {}, t)))));
        } else if (!issues.length) {
          el.append(h("p", { class: "muted small" }, icon("check"), " The plan is valid as it was made."));
        }
      }
      const shape = r.totals?.shape || {};
      const widths = (r.levels || []).map((l) => l.length);
      const widest = shape.max_level_width ?? (widths.length ? Math.max(...widths) : 0);
      el.append(h("dl", { class: "pv-shape" },
        h("div", {}, h("dt", {}, "Nodes"), h("dd", {}, String(shape.nodes ?? (r.nodes || []).length))),
        h("div", {}, h("dt", {}, "Levels"), h("dd", {}, String(shape.levels ?? (r.levels || []).length))),
        h("div", {}, h("dt", {}, "Widest level"), h("dd", {}, `${widest} at once`)),
        h("div", {}, h("dt", {}, "At most at once"), h("dd", {}, String(r.max_parallel || "?")))));
      if (r.status !== "ready" && r.status !== "approved" && r.status !== "planning") {
        el.append(totalsBlock(r));
        const split = costSplit(r);
        if (split) el.append(split);
        if (r.gate_config) el.append(h("p", { class: "muted small pv-gates" }, gateConfigWords(r.gate_config)));
        if (r.baseline?.reused) {
          el.append(h("p", { class: "muted small pv-gates" }, icon("info"), " The first check reused the recorded result for this commit, so it did not run again."));
        }
      }
    });
  }

  function totalsBlock(r) {
    const t = r.totals || {};
    const tin = usageIn(t);
    const cache = (t.cache_read_input_tokens || 0) + (t.cache_creation_input_tokens || 0);
    return h("dl", { class: "pv-totals", "aria-label": "Totals so far" },
      h("div", {}, h("dt", {}, "Tokens in"), h("dd", {}, `${num(tin)}${cache ? ` (${num(cache)} from cache)` : ""}`)),
      h("div", {}, h("dt", {}, "Tokens out"), h("dd", {}, num(t.tokens_out))),
      h("div", {}, h("dt", {}, "Time"), h("dd", {}, secs(t.wall_s))),
      h("div", {}, h("dt", {}, "Lines changed"), h("dd", {}, num(t.lines_changed))),
      h("div", {}, h("dt", {}, "Estimated API value"), h("dd", {}, `${money(t.cost_usd_list_estimate)} `, h("span", { class: "muted small" }, "not charged"))));
  }

  /** What the late findings cost, apart from the build itself: the planner and the repair rounds. */
  function costSplit(r) {
    const planner = sumUsage([r.planner_usage || r.planner?.usage]);
    const repairs = repairUsage(r);
    const rows = [];
    const row = (label, u, extra) => h("div", {}, h("dt", {}, `${label}, estimated API value`),
      h("dd", {}, `${money(u.cost)} `, h("span", { class: "muted small" }, `not charged · ${kfmt(u.tokens)} tokens${extra || ""}`)));
    if (planner.tokens || planner.cost) rows.push(row("Planner", planner));
    if (repairs.tokens || repairs.cost || r.repair_attempts) {
      rows.push(row("Repairs", repairs, r.repair_attempts ? ` · ${plural(r.repair_attempts, "attempt")}` : ""));
    }
    return rows.length ? h("dl", { class: "pv-totals pv-split", "aria-label": "What the planner and the repairs cost, included above" }, rows) : null;
  }

  // ---- approve, run, stop
  async function act(fn, failed) {
    if (vui.busy) return;
    vui.busy = true;
    drawAll();
    try {
      const out = await fn();
      return out;
    } catch (e) {
      if (ep === api.epoch()) toast(`${failed}: ${e.message}`);
    } finally {
      vui.busy = false;
      if (ep === api.epoch()) drawAll();
    }
  }

  async function reloadPlan() {
    const r = await api.planGet(id);
    if (ep !== api.epoch()) return;
    setRecord(r);
    return r;
  }

  function drawActions() {
    const r = rec();
    redraw(sections.actions, (el) => {
      if (!r || r.status === "planning") {
        if (r) el.append(h("button", { type: "button", class: "btn", "data-fk": "stop", onclick: stop }, "Stop planning"));
        return;
      }
      const invalid = r.validation && r.validation.ok === false;
      const row = h("div", { class: "pv-act-row" });
      // v1.10: a stopped plan says what happened and offers the one action resume.kind names
      const banner = RESUMABLE.has(r.status) && r.resume ? resumeBanner(r) : null;
      if (r.status === "ready" || r.status === "approved") {
        const approved = r.status === "approved";
        row.append(
          h("button", {
            type: "button", class: ["btn", approved ? "btn-quiet" : "btn-primary"], "data-fk": "approve",
            disabled: approved || vui.busy || invalid, "aria-describedby": "pv-run-why",
            onclick: () => act(async () => { await api.planApprove(id); vui.note = null; await reloadPlan(); }, "Could not approve the plan"),
          }, icon("check"), approved ? "Approved" : "Approve this plan"),
          h("button", {
            type: "button", class: "btn btn-primary", "data-fk": "run", disabled: !approved || vui.busy,
            title: approved ? null : "Approve the plan first", "aria-describedby": "pv-run-why",
            onclick: () => act(async () => { await api.planRun(id); await reloadPlan(); followPlan(id); }, "Could not run the plan"),
          }, icon("play"), "Run plan"));
      } else if (r.status === "running") {
        row.append(h("button", { type: "button", class: "btn", "data-fk": "stop", disabled: vui.busy, onclick: stop }, "Stop the plan"));
      } else if (RESUMABLE.has(r.status) && !banner) {
        row.append(h("button", {
          type: "button", class: "btn btn-primary", "data-fk": "again", disabled: vui.busy,
          onclick: () => act(async () => { await api.planRun(id); await reloadPlan(); followPlan(id); }, "Could not run the plan again"),
        }, icon("play"), "Run again"));
      }
      if (RESUMABLE.has(r.status) && !banner) {
        const stuck = (r.nodes || []).filter((n) => n.status !== "passed");
        const done = (r.nodes || []).length - stuck.length;
        el.append(h("p", { class: "pv-stuck", role: "status" }, icon("triangle"), " ",
          `${plural(done, "node")} passed. Did not finish: `,
          stuck.map((n, i) => [i ? "; " : "", h("strong", {}, nodeTitle(n)), ` (${nodeStatusWord(n).toLowerCase()})`])));
      }
      sections.actions.classList.toggle("is-stopped", Boolean(banner)); // CSS: above the totals
      if (banner) el.append(banner);
      if (row.childElementCount) el.append(row);
      const words = h("p", { class: "muted small", id: "pv-run-why" });
      if (r.status === "ready") words.textContent = invalid ? "Fix the problems above, then approve. " : "Review the plan, change what you like, then approve it. ";
      if (r.status === "approved") words.textContent = "Approved. ";
      if (r.status === "ready" || r.status === "approved") words.textContent += "Nothing is built until you run it. Your checked-out branch is not changed.";
      if (r.status === "running") words.textContent = "The plan is running. Nodes build in their own working copies; the target branch only moves after a level's full check passes. Stopping ends the running builds; branches of nodes that passed stay.";
      if (RESUMABLE.has(r.status)) {
        words.textContent = r.status === "stopped"
          ? "The plan was stopped. Run again builds only the nodes that did not pass, from the target branch as it is now."
          : "Run again builds only the nodes that failed, were blocked or never started, from the target branch as it is now. Nodes that passed are kept.";
      }
      if (r.status === "passed") {
        const cur = vui.branches?.current_branch;
        words.textContent = `Every node passed and was merged onto ${r.target_branch}. ` +
          (cur && cur === r.target_branch ? `${cur} is your checked-out branch, so it was fast-forwarded.` : "Your checked-out branch was not changed.");
      }
      if (!banner) el.append(words);
      if (vui.note && (r.status === "ready")) el.append(h("p", { class: "pv-note", role: "status" }, icon("info"), " ", vui.note));
      if (r.error && !banner) {
        el.append(h("div", { class: "bv-note is-error", role: "alert" }, icon("triangle"),
          h("p", {}, planError(r))));
      }
      const repair = repairBanner(r);
      if (repair) el.append(repair);
      for (const w of r.warnings || []) el.append(h("p", { class: "muted small" }, icon("info"), " ", String(w)));
    });
  }

  // ---- resume (v1.10): the banner of a plan that stopped, and its one button
  /** Run, continue, retry, repair or land, by resume.kind. A 409 is shown in the banner; the
   *  owner may then confirm an unsafe branch with a second, explicit click. */
  async function resumeNow(r, confirm = false) {
    if (vui.busy) return;
    const land = r.resume?.kind === "land";
    vui.busy = true;
    vui.refusal = null;
    drawAll();
    try {
      const body = confirm ? { confirm_unsafe_branch: true } : {};
      if (land) await api.planLand(id, body);
      else await api.planRun(id, body);
      const now = await reloadPlan();
      if (ep === api.epoch() && now && LIVE.has(now.status)) followPlan(id);
    } catch (e) {
      if (ep !== api.epoch()) return;
      if (e.status === 409) {
        vui.refusal = {
          plan: id, code: e.code, message: refusalWords(e.detail || e.message),
          unsafe: UNSAFE_CODES.has(e.code) && !confirm, confirming: false,
        };
        reloadPlan().catch(() => {});
      } else {
        toast(`Could not ${land ? "land the level" : "run the plan"}: ${e.message}`);
      }
    } finally {
      vui.busy = false;
      if (ep === api.epoch()) drawAll();
    }
  }

  function refusalBox(r) {
    const ref = vui.refusal && vui.refusal.plan === id ? vui.refusal : null;
    if (!ref) return null;
    const box = h("div", { class: "pv-refusal", role: "alert" },
      h("p", {}, icon("triangle"), h("span", {}, ref.message)));
    if (ref.unsafe && !ref.confirming) {
      box.append(h("div", { class: "pv-act-row" }, h("button", {
        type: "button", class: "btn btn-quiet", "data-fk": "land-anyway", disabled: vui.busy,
        onclick: () => { ref.confirming = true; drawActions(); },
      }, "Land anyway")));
    } else if (ref.unsafe) {
      box.append(h("div", { class: "pv-confirm" },
        h("p", {}, `This moves ${r.target_branch} forward inside your own working copy. Nothing is rewritten, and the working copy must have no uncommitted changes. Land it there?`),
        h("div", { class: "pv-act-row" },
          h("button", { type: "button", class: "btn", "data-fk": "land-confirm", disabled: vui.busy, onclick: () => resumeNow(r, true) },
            `Yes, update ${r.target_branch}`),
          h("button", { type: "button", class: "btn btn-quiet", "data-fk": "land-cancel", onclick: () => { ref.confirming = false; drawActions(); } }, "Not now"))));
    }
    return box;
  }

  function resumeBanner(r) {
    const v = r.resume;
    const stopped = r.status === "stopped" || r.status === "interrupted";
    const box = h("div", { class: ["pv-resume", `is-${r.status}`, stopped && "is-quiet"], role: "status", "data-kind": v.kind || "none" });
    const when = r.interrupted_at ? h("span", { class: "muted small" }, ` ${fmtTime(r.interrupted_at)}`) : null;
    box.append(h("p", { class: "pv-resume-what" }, icon(stopped ? "dash" : "triangle"),
      h("span", {}, h("strong", {}, happenedWords(r)), when)));
    const exhausted = v.kind === "repair" && v.available && r.error?.kind === "repair_exhausted";
    const cap = r.gate_config?.level_repair_attempts;
    const note = exhausted
      ? `Fixing it merges the level's nodes onto the current ${r.target_branch} and gives the agent another bounded pass${cap ? ` (up to ${plural(cap, "attempt")})` : ""}. No node starts over.`
      : serverLevels(v.note);
    if (note) box.append(h("p", { class: "pv-resume-note" }, note));
    if (v.available) {
      const skip = nodeTitles(r, v.will_skip);
      const run = nodeTitles(r, v.will_run);
      box.append(h("p", { class: "pv-resume-list" }, h("span", { class: "muted" }, "Skipped: "), skip || "nothing"),
        h("p", { class: "pv-resume-list" }, h("span", { class: "muted" }, "Will run: "), run || "nothing"));
      box.append(h("div", { class: "pv-act-row" }, h("button", {
        type: "button", class: "btn btn-primary", "data-fk": "resume", "data-resume": v.kind, disabled: vui.busy,
        onclick: () => resumeNow(r),
      }, icon(v.kind === "land" ? "check" : "play"), resumeLabel(v))));
    }
    const refusal = refusalBox(r);
    if (refusal) box.append(refusal);
    return box;
  }

  async function stop() {
    await act(async () => { await api.planStop(id); await reloadPlan(); }, "Could not stop the plan");
  }

  // ---- the graph
  function graphNodes(r) {
    const sizes = (r.levels || []).map((l) => l.length);
    const nodes = (r.nodes || []).map((n) => ({
      id: n.id, kind: "plan", label: nodeTitle(n), sub: n.why || "", column: n.level || 0,
      plan: {
        tier: n.tier, status: n.status, statusLabel: nodeStatusWord(n), facts: nodeFacts(n),
        rules: n.kind === "repair" ? ["Repair", rulesWord(n)].filter(Boolean).join(" · ") : rulesWord(n),
      },
    }));
    const columns = sizes.map((c, i) => ({
      label: `Level ${i + 1}`,
      sub: c > 1 ? "These run at the same time" : "These run one after another",
    }));
    const edges = (r.edges || []).map((e) => {
      const hot = vui.selected && e.to === vui.selected;
      return {
        id: `${e.from}>${e.to}`, source: e.from, target: e.to,
        kind: e.kind === "serialised" ? "serialised" : e.kind,
        state: e.kind === "serialised" ? "dashed" : undefined,
        label: hot && e.kind !== "scaffold" && e.kind !== "integration"
          ? String(e.why || "").replace(/^overlapping write sets:\s*/i, "shares ")
          : undefined,
      };
    });
    return { nodes, columns, edges };
  }

  function drawGraph() {
    const r = rec();
    if (!r || !(r.nodes || []).length) {
      sections.graph.hidden = true;
      return;
    }
    sections.graph.hidden = false;
    const g = graphNodes(r);
    const selected = vui.selected && r.nodes.some((n) => n.id === vui.selected) ? [vui.selected] : [];
    if (!graph || !graph.alive) {
      graph = mountGraph(graphHost, {
        nodes: g.nodes, edges: g.edges, layout: "columns", columns: g.columns, selected, marked: [],
        label: "Build plan: nodes in level columns, left to right",
        onSelect: (nid) => select(vui.selected === nid ? null : nid),
        onOpen: (nid) => select(nid),
        onClear: () => select(null),
      });
    } else {
      graph.update({ nodes: g.nodes, edges: g.edges, columns: g.columns, selected });
    }
    legend.textContent = "Dashed lines mean a node waits only because it writes the same data as the node before it. Select a node to see why each line is there.";
  }

  function select(nid) {
    vui.selected = nid;
    vui.issues = null;
    const r = rec();
    if (graph && r) graph.update({ edges: graphNodes(r).edges, selected: nid ? [nid] : [] });
    drawSide();
    drawList();
  }

  // ---- the levels strip
  function drawLevels() {
    const r = rec();
    redraw(sections.levels, (el) => {
      if (!r || !(r.level_runs || []).length) return;
      const t = (nid) => titleOf(r, nid);
      el.append(h("h2", { class: "s2-section-title" }, "Levels"));
      const rows = (r.levels || []).map((_, i) => {
        const run = (r.level_runs || []).filter((x) => x.level === i && !x.superseded).pop()
          || (r.level_runs || []).filter((x) => x.level === i).pop();
        let words;
        if (!run) words = h("span", { class: "muted" }, live() ? "waiting" : "did not run");
        else if (run.status === "running") {
          const fixing = (run.repairs || []).length;
          words = h("span", {}, h("span", { class: "spinner", "aria-hidden": "true" }),
            fixing ? ` repairing after the check failed (attempt ${run.repairs[fixing - 1].attempt})` : " running");
        } else if (run.status === "interrupted") {
          words = h("span", { class: "muted" }, "interrupted: Studio stopped while this level ran");
        } else {
          const merged = (run.merged || []).map(t);
          const bits = [`merged: ${merged.length ? merged.join(", ") : "nothing"}`];
          if ((run.merge_conflicts || []).length) bits.push(`merge conflicts: ${run.merge_conflicts.map(t).join(", ")}`);
          const regs = run.regressions || [];
          const check = run.gate ? gateWords(run.gate) : "full check";
          bits.push(run.status === "passed" || !regs.length
            ? `${check}: ${run.gate ? "passed" : run.status === "failed" ? "not reached" : "passed"}`
            : `${check}: ${plural(regs.length, "regression")} (${regs.slice(0, 3).join(", ")}${regs.length > 3 ? ", …" : ""})`);
          if (run.status === "passed" && (run.repairs || []).length) bits.push(`repaired in ${plural(run.repairs.length, "attempt")}`);
          if (run.status === "failed" && run.repair_pending) bits.push("repair attempts used up");
          words = h("span", { class: run.status === "failed" ? "is-bad" : "" }, bits.join(" · "));
        }
        return h("li", {}, h("span", { class: "pv-level-n" }, `Level ${i + 1}`), words);
      });
      if (r.final_gate) {
        const failing = (r.final_gate.failing || []).length;
        rows.push(h("li", {}, h("span", { class: "pv-level-n" }, "Before the last level"),
          h("span", { class: "muted" }, `full check of the whole spec: ${failing ? `${plural(failing, "check")} still failing` : "everything passes"}`)));
      }
      el.append(h("ol", { class: "pv-level-list" }, rows));
    });
  }

  // ---- the list alternative
  function drawList() {
    const r = rec();
    redraw(sections.list, (el) => {
      if (!r || !(r.nodes || []).length) return;
      const det = h("details", { class: "pv-list-det", open: vui.listOpen ? "" : null,
        ontoggle: (e) => { vui.listOpen = e.target.open; } },
        h("summary", {}, "The plan as a list"));
      const byLevel = (r.levels || []).map((ids, i) => ({ i, nodes: ids.map((nid) => r.nodes.find((n) => n.id === nid)).filter(Boolean) }));
      for (const { i, nodes } of byLevel) {
        det.append(h("h3", { class: "pv-list-level" }, `Level ${i + 1}`,
          h("span", { class: "muted small" }, nodes.length > 1 ? ": these run at the same time" : ": runs on its own")),
        h("ul", { class: "pv-list-nodes" }, nodes.map((n) => {
          const deps = (n.depends_on || []).map((d) => titleOf(r, d));
          const f = nodeFacts(n);
          return h("li", { class: [vui.selected === n.id && "is-selected"] },
            h("button", { type: "button", class: "pv-list-btn", "data-fk": `node:${n.id}`, "aria-pressed": vui.selected === n.id ? "true" : "false", onclick: () => select(vui.selected === n.id ? null : n.id) },
              h("span", { class: "pv-list-title" }, nodeTitle(n)),
              h("span", { class: "muted small" }, [rulesWord(n), deps.length ? `waits for ${deps.join(", ")}` : "waits for nothing"].filter(Boolean).join(" · "))),
            tierMark(n.tier), nodeChip(n), f ? h("span", { class: "muted small" }, f) : null);
        })));
      }
      el.append(det);
    });
  }

  // ---- what happened: the timeline of the plan (resume and repair entries are tagged)
  function drawHistory() {
    const r = rec();
    redraw(sections.history, (el) => {
      const events = (r && r.timeline) || [];
      if (!events.length) return;
      el.append(h("details", { class: "pv-timeline", open: vui.historyOpen ? "" : null, ontoggle: (e) => { vui.historyOpen = e.target.open; } },
        h("summary", {}, `What happened (${plural(events.length, "event")})`),
        h("ol", {}, events.slice(-60).map((ev) => h("li", {},
          h("span", { class: "pv-ev-at muted small" }, fmtTime(ev.at)),
          h("span", {}, serverLevels(ev.event)),
          ev.repair ? h("span", { class: "pv-tag" }, "repair") : null)))));
    });
  }

  // ---- the selected node
  function drawSide() {
    const r = rec();
    const n = r && vui.selected ? r.nodes.find((x) => x.id === vui.selected) : null;
    redraw(sideInfo, (el) => {
      if (!r || !(r.nodes || []).length) return;
      if (!n) {
        el.append(h("p", { class: "muted" }, editable()
          ? "Select a node to see what it builds, what it waits for, and to change its model or what it depends on."
          : "Select a node to see what it builds and what it waits for."));
        return;
      }
      el.append(
        h("h2", { class: "pv-side-title" }, nodeTitle(n)),
        h("p", { class: "pv-side-meta" }, h("span", { class: "muted" }, kindWord[n.kind] || n.kind), " · ", rulesWord(n), " · ", tierMark(n.tier), " ", nodeChip(n)),
        n.why ? h("p", {}, n.why) : null);
      const next = nodeNext(r, n);
      if (next) el.append(h("p", { class: ["pv-next", n.status === "passed" ? "is-quiet" : "is-warn"], role: "status" }, icon(n.status === "passed" ? "info" : "triangle"), " ", next));
      const into = (r.edges || []).filter((e) => e.to === n.id);
      const out = (r.edges || []).filter((e) => e.from === n.id);
      el.append(h("h3", { class: "s2-section-title" }, "Waits for"));
      el.append(into.length
        ? h("ul", { class: "pv-edges" }, into.map((e) => h("li", {},
          h("strong", {}, titleOf(r, e.from)), e.kind === "serialised" ? h("span", { class: "pv-dashed" }, " (one after the other)") : null,
          h("span", { class: "muted small" }, ` ${sentence(e.why)}`))))
        : h("p", { class: "muted small" }, "Nothing: it can start as soon as its level starts."));
      if (out.length) {
        el.append(h("h3", { class: "s2-section-title" }, "Then runs"),
          h("p", { class: "small" }, out.map((e) => titleOf(r, e.to)).join(", ")));
      }
      if (n.serialised_because) {
        el.append(h("p", { class: "small pv-serial" }, icon("lock"), ` It waits for ${titleOf(r, n.serialised_because.node)} because both write ${overlapWords(n.serialised_because.overlap)}. Two nodes that write the same data never run together.`));
      }
      const touches = n.touches || {};
      const tl = [
        (touches.tables || []).length ? `tables ${touches.tables.join(", ")}` : null,
        (touches.packages || []).length ? `packages ${touches.packages.join(", ")}` : null,
        (touches.files || []).length ? `files ${touches.files.slice(0, 4).join(", ")}${touches.files.length > 4 ? " …" : ""}` : null,
      ].filter(Boolean);
      el.append(h("dl", { class: "rb-facts" },
        h("dt", {}, "Model"), h("dd", {}, n.model ? `${n.model} (${(TIER[n.tier] || TIER.mid).word.toLowerCase()})` : (TIER[n.tier] || TIER.mid).word),
        tl.length ? [h("dt", {}, "Writes"), h("dd", {}, tl.join("; "))] : null,
        Array.isArray(n.scope?.rules) ? [h("dt", {}, "Rules"), h("dd", { class: "s2-mono" }, n.scope.rules.slice(0, 12).join(", ") + (n.scope.rules.length > 12 ? " …" : ""))] : null,
        n.gate ? [h("dt", {}, "Passes when"), h("dd", {}, `its linked checks pass and nothing that passed before fails${n.gate.kind === "full" ? "; the whole spec is checked" : ""}. Studio decides, not the agent.`)] : null,
        usageIn(n.usage) || n.usage?.tokens_out ? [h("dt", {}, "Tokens"), h("dd", {}, `${num(usageIn(n.usage))} in, ${num(n.usage.tokens_out)} out`)] : null,
        n.wall_s ? [h("dt", {}, "Time"), h("dd", {}, secs(n.wall_s))] : null,
        n.rounds ? [h("dt", {}, "Rounds"), h("dd", {}, String(n.rounds))] : null,
        n.attempts > 1 ? [h("dt", {}, "Attempts"), h("dd", {}, String(n.attempts))] : null,
        n.repairs ? [h("dt", {}, "Repairs"), h("dd", {}, `${plural(n.repairs, "round")}${n.repair_usage ? `, ${kfmt(sumUsage([n.repair_usage]).tokens)} tokens, ${money(n.repair_usage.cost_usd_list_estimate)} estimated API value, not charged` : ""}`)] : null,
        n.lines_changed ? [h("dt", {}, "Lines changed"), h("dd", {}, `${num(n.lines_changed)} in ${plural((n.files_changed || []).length, "file")}`)] : null,
        n.branch ? [h("dt", {}, "Branch"), h("dd", { class: "s2-mono" }, n.branch)] : null,
        n.merged ? [h("dt", {}, "Merged"), h("dd", {}, `into ${r.target_branch}`)] : null));
    });
    drawEdit(r, n);
    drawBuild(r, n);
  }

  // ---- edits before the run
  function issueList() {
    if (!vui.issues || !vui.issues.length) return null;
    return h("div", { class: "bv-warn", role: "alert" },
      h("p", {}, icon("triangle"), h("strong", {}, " That change was not applied")),
      h("ul", {}, vui.issues.map((i) => h("li", {}, sentence(i)))));
  }

  async function applyEdits(edits, extra = {}) {
    if (!editable()) return;
    const was = rec().status;
    vui.busy = true;
    vui.issues = null;
    drawSide();
    drawPlanEdit();
    try {
      const out = await api.planRevise(id, { edits });
      if (ep !== api.epoch()) return;
      if (out.status === "planning") followPlan(id);
      setRecord(out.nodes ? out : await api.planGet(id));
      vui.note = was === "approved" ? "Your change cleared the approval. Approve the plan again before running it." : null;
      if (extra.afterOk) extra.afterOk();
    } catch (e) {
      if (ep !== api.epoch()) return;
      const text = String(e.detail || e.message || "");
      vui.issues = e.code === "plan_invalid" || e.status === 422
        ? text.replace(/^the edited plan is invalid:\s*/i, "").split(/;\s*/).filter(Boolean)
        : [text];
    } finally {
      vui.busy = false;
      if (ep === api.epoch()) drawAll();
    }
  }

  function drawEdit(r, n) {
    redraw(sideEdit, (el) => {
      if (!r || !n) return;
      if (!EDITABLE.has(r.status)) {
        if (LIVE.has(r.status)) el.append(h("p", { class: "muted small" }, "The plan cannot be edited while it is running."));
        return;
      }
      const dis = vui.busy;
      const tier = h("select", {
        class: "bv-select", id: "pv-tier", "data-fk": "tier", disabled: dis ? true : null,
        onchange: (e) => applyEdits({ nodes: { [n.id]: { tier: e.target.value } } }),
      }, Object.entries(TIER).map(([k, t]) => h("option", { value: k }, `${t.word}: ${t.hint}`)));
      tier.value = n.tier;
      const model = h("input", {
        type: "text", class: "bv-input", id: "pv-model", "data-fk": "model", spellcheck: "false", autocomplete: "off",
        placeholder: "Use the tier's model", disabled: dis ? true : null,
      });
      model.value = (r.models?.[n.tier] || "") === n.model ? "" : n.model || "";
      model.addEventListener("change", () => applyEdits({ nodes: { [n.id]: { model: model.value.trim() || null } } }));
      const others = (r.nodes || []).filter((x) => x.id !== n.id && x.kind !== "integration");
      const deps = new Set(n.depends_on || []);
      const depList = n.kind === "scaffold" || n.kind === "integration" || !others.length ? null : h("ul", { class: "pv-deps" }, others.map((o) => {
        const edge = (r.edges || []).find((e) => e.from === o.id && e.to === n.id);
        const box = h("input", {
          type: "checkbox", id: `pv-dep-${o.id}`, "data-fk": `dep:${o.id}`, disabled: dis ? true : null,
          "aria-describedby": edge ? `pv-dep-why-${o.id}` : null,
          onchange: (e) => {
            const on = e.target.checked;
            applyEdits(on ? { add_edges: [[o.id, n.id]] } : { remove_edges: [[o.id, n.id]] }, {
              afterOk: () => {
                const again = (rec().edges || []).some((x) => x.from === o.id && x.to === n.id);
                if (!on && again) vui.issues = [`${o.title || o.id} stays: it is needed here. ${sentence(rec().edges.find((x) => x.from === o.id && x.to === n.id)?.why)}`];
              },
            });
          },
        });
        box.checked = deps.has(o.id);
        return h("li", {}, h("label", { for: `pv-dep-${o.id}` }, box, h("span", {}, o.title || o.id)),
          edge ? h("span", { class: "muted small", id: `pv-dep-why-${o.id}` }, ` ${edge.why}`) : null);
      }));
      el.append(h("h3", { class: "s2-section-title" }, "Change this node"),
        h("div", { class: "pv-form" },
          h("label", { for: "pv-tier" }, "Model tier"), tier,
          h("label", { for: "pv-model" }, "Model override"), model),
        depList ? h("fieldset", { class: "pv-fieldset" }, h("legend", {}, "Waits for"), depList,
          h("p", { class: "muted small" }, "Studio checks every change: a cycle, or two nodes writing the same data at once, is corrected or refused.")) : null,
        issueList());
    });
  }

  // ---- plan-level edits: capacity, target branch, feedback
  function drawPlanEdit() {
    const r = rec();
    redraw(sections.plan, (el) => {
      if (!r || !EDITABLE.has(r.status)) return;
      const dis = vui.busy;
      const cap = r.max_parallel || DEFAULT_CAP;
      const branchSel = h("select", {
        class: "bv-select", id: "pv-branch", "data-fk": "branch", disabled: dis ? true : null,
        onchange: (e) => {
          const name = e.target.value;
          const known = (vui.branches?.branches || []).some((b) => b.name === name);
          const info = (vui.branches?.branches || []).find((b) => b.name === name);
          const unsafe = Boolean(info && (info.protected || info.current));
          if (unsafe && !vui.confirm) {
            vui.issues = [`${name} is ${info.protected ? "protected" : "checked out in your working tree"}. Tick "I understand, update this branch" below, then pick it again.`];
            drawPlanEdit();
            return;
          }
          const body = known ? { name } : { name, create: true, from: "HEAD" };
          applyEdits({ branch: body, ...(unsafe ? { confirm_unsafe_branch: true } : {}) });
        },
      });
      const names = (vui.branches?.branches || []).filter((b) => !/^rebuild\//.test(b.name) && !(b.checked_out && !b.current)).map((b) => b.name);
      for (const nm of [vui.branches?.default_target, r.target_branch]) if (nm && !names.includes(nm)) names.unshift(nm);
      for (const nm of names) branchSel.append(h("option", { value: nm }, nm));
      branchSel.value = r.target_branch;
      const confirm = h("input", { type: "checkbox", id: "pv-confirm", "data-fk": "confirm", onchange: (e) => { vui.confirm = e.target.checked; } });
      confirm.checked = vui.confirm;
      const fbText = h("textarea", {
        class: "pv-feedback", id: "pv-feedback", rows: "3", "data-fk": "feedback", placeholder: "For example: keep sign-in and accounts together, and build the worker last.",
        disabled: dis ? true : null,
        oninput: (e) => { vui.feedback = e.target.value; fbBtn.disabled = dis || !vui.feedback.trim(); },
      });
      fbText.value = vui.feedback;
      const fbBtn = h("button", {
        type: "button", class: "btn", "data-fk": "replan", disabled: dis || !vui.feedback.trim(),
        onclick: async () => {
          const text = vui.feedback.trim();
          if (!text || vui.busy) return;
          vui.busy = true;
          vui.issues = null;
          drawAll();
          try {
            await api.planRevise(id, { feedback: text });
            vui.feedback = "";
            vui.note = null;
            followPlan(id);
            setRecord({ ...rec(), status: "planning" });
          } catch (e) {
            vui.issues = [String(e.detail || e.message)];
          } finally {
            vui.busy = false;
            if (ep === api.epoch()) drawAll();
          }
        },
      }, "Plan again with feedback");
      el.append(h("h2", { class: "s2-section-title" }, "Change the plan"),
        h("p", { class: "muted small" }, "Edits are checked by Studio and clear any approval. Nothing runs until you approve and run."),
        h("div", { class: "pv-form" },
          h("label", { for: "pv-cap" }, "Run at most"),
          h("span", { class: "pv-cap" },
            h("button", { type: "button", class: "btn btn-icon", "aria-label": "Fewer nodes at once", "data-fk": "cap-minus", disabled: dis || cap <= 1 ? true : null,
              onclick: () => applyEdits({ max_parallel: cap - 1 }) }, icon("minus")),
            h("output", { id: "pv-cap", class: "bv-rounds", "aria-live": "polite" }, String(cap)),
            h("button", { type: "button", class: "btn btn-icon", "aria-label": "More nodes at once", "data-fk": "cap-plus", disabled: dis || cap >= MAX_CAP ? true : null,
              onclick: () => applyEdits({ max_parallel: cap + 1 }) }, icon("plus")),
            h("span", {}, " nodes at once")),
          h("label", { for: "pv-branch" }, "Target branch"), branchSel),
        h("label", { class: "toggle", for: "pv-confirm" }, confirm, h("span", {}, "I understand, update this branch (needed for a protected or checked-out branch)")),
        h("label", { for: "pv-feedback", class: "pv-fb-label" }, "Tell the planner what to change"),
        fbText, h("div", {}, fbBtn),
        vui.note ? h("p", { class: "pv-note", role: "status" }, icon("info"), " ", vui.note) : null,
        sideEdit.childElementCount ? null : issueList());
    });
  }

  // ---- the build card of the selected node
  function drawBuild(r, n) {
    const want = n && n.build_id && r.status !== "ready" && r.status !== "approved" ? n.build_id : null;
    if (want === buildShown) return;
    if (stopBuildPoll) { stopBuildPoll(); stopBuildPoll = null; }
    buildShown = want;
    sideBuild.replaceChildren();
    if (!want) return;
    sideBuild.append(h("h3", { class: "s2-section-title" }, "Its build"), h("p", { class: "muted", "aria-busy": "true" }, "Opening the build…"));
    stopBuildPoll = pollRebuild(want, (b, err) => {
      if (buildShown !== want || ep !== api.epoch()) return;
      sideBuild.replaceChildren();
      if (err || !b) {
        sideBuild.append(h("p", { class: "muted small" }, "The record of this node's build is not available."));
        return;
      }
      sideBuild.append(h("h3", { class: "s2-section-title" }, "Its build"),
        rebuildCard(b, { openCode: ctx.openCode, onChanged: () => {} }));
    }, sideBuild);
  }

  function drawAll() {
    if (!root.isConnected) return;
    drawHead();
    drawMeta();
    drawActions();
    drawGraph();
    drawLevels();
    drawSide();
    drawPlanEdit();
    drawList();
    drawHistory();
  }

  // ---- loading
  function missing(text) {
    redraw(body, (el) => {
      el.append(h("div", { class: "empty-note", role: "status" }, h("p", {}, text),
        h("button", { type: "button", class: "btn", onclick: back }, "Back to Build")));
    });
  }

  async function load() {
    drawHead();
    if (!rec()) body.append(h("p", { class: "muted", "aria-busy": "true", id: "pv-loading" }, "Opening the plan…"));
    try {
      const r = await api.planGet(id);
      if (ep !== api.epoch() || !root.isConnected) return;
      document.getElementById("pv-loading")?.remove();
      if (state.planRun?.id !== id || JSON.stringify(state.planRun.record) !== JSON.stringify(r)) update({ planRun: { id, record: r } });
      else drawAll();
      if (LIVE.has(r.status)) followPlan(id);
    } catch (e) {
      if (ep !== api.epoch() || !root.isConnected) return;
      if (e.status === 404) missing(`Plan ${id} is not on this server any more.`);
      else missing(`The plan could not be opened: ${e.message}`);
    }
  }

  api.branches().then((b) => {
    if (ep !== api.epoch()) return;
    vui.branches = b;
    drawPlanEdit();
  }).catch(() => {});

  const unsub = subscribe((st, patch) => {
    if (!root.isConnected || state.planView !== id) {
      unsub();
      if (stopBuildPoll) stopBuildPoll();
      return;
    }
    if ("planRun" in patch && st.planRun?.id === id) drawAll();
  });
  if (rec()) drawAll();
  load();
}
