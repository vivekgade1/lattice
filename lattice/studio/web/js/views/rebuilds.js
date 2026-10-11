// Evidence > Rebuilds: the live Rebuild card for the rebuild in state.rebuild (polled every
// second while it runs) and the history of rebuilds. rebuildCard() and pollRebuild() are
// exported so the conversation panel embeds the same card.

import { h, icon, add, plural } from "../util.js";
import { api } from "../api.js";
import { state, update, subscribe, hooks, canRebuild, REBUILD_WHY } from "../store.js";
import { highlight } from "../targets.js";
import { toast } from "../tray.js";

const LIVE = new Set(["queued", "baseline", "running"]);
const STATUS = {
  baseline: { label: "Checking the current state", icon: "pending" },
  queued: { label: "Queued", icon: "pending" },
  running: { label: "Rebuilding", icon: "play" },
  passed: { label: "Passed", icon: "check" },
  gave_up: { label: "Gave up", icon: "diamond" },
  stopped: { label: "Stopped", icon: "dash" },
  interrupted: { label: "Interrupted", icon: "dash" },
};
const LAYERS = ["A", "B", "C", "D", "X"];

// Round rows already shown, so only a newly finished round resolves with motion. Keys carry
// the project base: rebuild ids (b1, b2, ...) repeat across projects.
const seen = new Set();
const refreshed = new Set(); // rebuild ends already followed by a model reload
const sk = (...parts) => `${api.base()}|${parts.join(":")}`;

const short = (sha) => String(sha || "").slice(0, 7);
const num = (n) => Number(n || 0).toLocaleString("en-US");

/** A calm sentence and the next action for a refused or failed build start. `err` is an
 *  Error from api.js (status, code, detail) or a record's {kind, message}. Names are
 *  filled from `ctx` {branch, scope}. */
export function buildRefusal(err, ctx = {}) {
  const code = err.code || err.kind || null;
  const detail = String(err.detail || err.message || "");
  const b = ctx.branch ? ` ${ctx.branch}` : "";
  const known = {
    branch_exists: `A branch named${b} already exists. Pick a different name, or choose it from the list.`,
    branch_protected: "That branch is protected. Pick another branch or confirm the update.",
    branch_checked_out: "That branch is checked out in a working tree. Pick another branch, or confirm the update if it is your own checkout.",
    tree_dirty: "Commit or stash your working changes, then try again.",
    branch_moved: "The branch moved while the build ran. Nothing is lost: the build commit is kept on its working branch. Build again to continue.",
    merge_conflict: "Your latest commits conflict with the target branch, so the build could not start. Merge them yourself, or build onto a new branch.",
    no_branch: "That branch no longer exists. Pick another branch.",
    branch_name_invalid: "That is not a valid branch name. Use letters, digits, dashes and slashes, with no spaces.",
    no_ref: "That starting point does not exist. Pick another one.",
    baseline: "The first check of the current state could not run. Look at the gate output below, fix the gate, then try again.",
  };
  if (known[code]) return known[code];
  if (err.status === 409 && /nothing to build/i.test(detail)) {
    return ctx.scopeKind === "all" ? "The checks already pass, so there is nothing to build." : "The checks in this area already pass.";
  }
  if (err.status === 409 && /already running/i.test(detail)) return "Another build is running in this project. Wait for it to finish, or stop it first.";
  if (err.status === 404 && /no area|no rule/i.test(detail)) return "That area is not in the spec any more. Reload the list and pick again.";
  if (err.status === 503) return `Build is not available here: ${detail || "the project is not set up for it"}.`;
  return `The build could not start: ${detail || err.message}`;
}

/** A small button that copies `text` to the clipboard and says so. */
export function copyButton(text, label = "Copy") {
  const btn = h("button", { type: "button", class: "btn btn-quiet small rb-copy", "aria-label": `${label}: ${text}` }, label);
  btn.addEventListener("click", async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch (e) {
      try {
        const ta = h("textarea", { class: "sr-only", "aria-hidden": "true" });
        ta.value = text;
        document.body.append(ta);
        ta.select();
        ok = document.execCommand("copy");
        ta.remove();
      } catch (e2) {
        ok = false;
      }
    }
    btn.textContent = ok ? "Copied" : "Select and copy";
    setTimeout(() => { btn.textContent = label; }, 1800);
  });
  return btn;
}

function money(n) {
  const v = Number(n) || 0;
  return v > 0 ? `$${v < 0.01 ? v.toFixed(4) : v.toFixed(2)}` : "";
}

function seconds(ms) {
  const s = (Number(ms) || 0) / 1000;
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s` : `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
}

// ------------------------------------------------------------------ polling

/** Poll GET /api/rebuild/{id} every second while it is queued or running.
 *  onUpdate(rebuild, error). Returns stop(). Stops by itself when `anchor` leaves the DOM. */
export function pollRebuild(id, onUpdate, anchor = null) {
  let stopped = false;
  let timer = null;
  let first = true;
  const ep = api.epoch(); // a project switch ends this poll
  const tick = async () => {
    if (stopped || ep !== api.epoch() || (!first && anchor && !anchor.isConnected)) return;
    first = false;
    try {
      const r = await api.rebuildGet(id);
      if (stopped) return;
      onUpdate(r, null);
      if (LIVE.has(r.status)) timer = setTimeout(tick, 1000);
    } catch (e) {
      if (!stopped) onUpdate(null, e);
    }
  };
  tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}

// ------------------------------------------------------------------ the card

/** A round the Studio itself crashed in: it has an error and no gate ever ran. */
export function roundCrashed(r) {
  return Boolean(r && r.error && !(r.gates || []).length && !r.check);
}

/** Seconds of one round from its own started/finished, never from "now" or the mount time.
 *  Empty while the round is open or when the record has no usable stamps. */
export function roundSeconds(r) {
  const a = Date.parse(r?.started);
  const b = Date.parse(r?.finished);
  if (Number.isNaN(a) || Number.isNaN(b)) return "";
  return seconds(Math.max(0, b - a));
}

function elapsed(rebuild) {
  const start = Date.parse(rebuild.started);
  if (Number.isNaN(start)) return "";
  let end = Date.now();
  if (!LIVE.has(rebuild.status)) {
    const stamps = [rebuild.finished, ...(rebuild.rounds || []).map((r) => r.finished || r.started)]
      .map((x) => Date.parse(x)).filter((x) => !Number.isNaN(x));
    end = stamps.length ? Math.max(...stamps) : start;
  }
  const s = Math.max(0, Math.round((end - start) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

function layerCounts(counts) {
  const entries = LAYERS.filter((k) => k in (counts || {})).map((k) => [k, counts[k]]);
  const nonzero = entries.filter(([, n]) => n);
  if (!nonzero.length) return h("span", { class: "muted" }, "all layers clear");
  return h("span", { class: "rb-layers", "aria-label": nonzero.map(([k, n]) => `layer ${k}: ${n}`).join(", ") },
    nonzero.map(([k, n]) => h("span", { class: `rb-layer-${k.toLowerCase()}` }, `${k} ${n}`)));
}

function findingChip(f, opts) {
  const name = String(f.target || f.id || "finding").replace(/^[a-z]+:/, "");
  const regression = f.regression === true;
  const outside = f.in_scope === false && !regression;
  return h("details", { class: ["rb-finding", regression && "is-regression", outside && "is-outside"] },
    h("summary", { title: "Open the counterexample" }, icon(regression ? "triangle" : "diamond"), h("span", {}, name),
      regression ? h("span", { class: "rb-tag" }, "regression") : null,
      outside ? h("span", { class: "rb-tag" }, "outside this scope") : null),
    h("div", { class: "rb-finding-body" },
      h("p", {}, f.title || ""),
      f.target
        ? h("button", { type: "button", class: "s2-quiet-btn", onclick: () => (opts.onShow || highlight)([f.target]) }, `Show ${f.target} on the views`)
        : null,
    ),
  );
}

function roundRow(r, rebuild, opts, animate) {
  const key = sk(rebuild.id, r.n, r.check ? "done" : "live");
  const isNew = animate && !seen.has(key);
  seen.add(key);
  const c = r.check;
  const crashed = roundCrashed(r);
  const verdict = crashed
    ? h("div", { class: "rb-verdict is-fail" },
        h("span", { class: "rb-verdict-word" }, icon("triangle"), "Stopped by an error"),
        h("span", { class: "muted" }, "no check ran"))
    : c
    ? h("div", { class: ["rb-verdict", c.verdict === "pass" ? "is-pass" : "is-fail"] },
        h("span", { class: "rb-verdict-word" }, icon(c.verdict === "pass" ? "check" : "diamond"), c.verdict === "pass" ? "Passes" : "Fails"),
        layerCounts(c.counts))
    : h("div", { class: "rb-verdict is-working" },
        h("span", { class: "rb-verdict-word" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Agent working"));
  return h("li", { class: ["rb-round", isNew && "is-new"], "data-round": String(r.n) },
    h("span", { class: "rb-round-n" }, `Round ${r.n}`),
    h("div", {},
      h("p", { class: "rb-summary" }, r.agent_summary || (c || crashed ? "No summary." : "The agent is editing the code and running the checker.")),
      scopeLine(r),
      (r.files_changed || []).length ? h("p", { class: "rb-files" }, `${crashed ? "Files the agent changed: " : ""}${r.files_changed.join(", ")}`) : null,
      roundCost(r),
    ),
    verdict,
    (r.spec_edited || []).length
      ? h("p", { class: "rb-spec-edited", role: "note" }, icon("triangle"),
          h("span", {}, "The agent edited spec files; they were restored: ", h("span", { class: "s2-mono" }, r.spec_edited.join(", "))))
      : null,
    r.error
      ? h("p", { class: "rb-round-error", role: "note" }, icon("triangle"), h("span", {}, errorWords(r.error)))
      : null,
    (r.gates || []).length ? gateList(r.gates) : null,
    c && (c.findings || []).length
      ? h("div", { class: "rb-findings" }, c.findings.slice(0, 12).map((f) => findingChip(f, opts)),
          c.findings.length > 12
            ? h("details", { class: "rb-more" },
                h("summary", {}, `and ${c.findings.length - 12} more`),
                h("div", { class: "rb-findings" }, c.findings.slice(12).map((f) => findingChip(f, opts))))
            : null)
      : null,
  );
}

/** A scoped build's score for one round: "In scope 3 of 5 pass", regression chips and the
 *  newly fixed count. Null for a round of an ordinary rebuild or one still open. */
function scopeLine(r) {
  if (r.scope_total === undefined || r.scope_total === null || !r.check) return null;
  const regs = r.regressions || [];
  const fixed = (r.newly_fixed || []).length;
  return h("p", { class: "rb-scope-line" },
    h("span", { class: "rb-scope-score" }, `In scope ${r.scope_passing} of ${r.scope_total} pass`),
    fixed ? h("span", { class: "muted" }, ` · ${plural(fixed, "check")} newly fixed`) : null,
    regs.length
      ? h("span", { class: "rb-regressions", "aria-label": `${plural(regs.length, "regression")}` },
          regs.map((id) => h("span", { class: "rb-regress" }, icon("triangle"), h("span", {}, "regression"), " ", h("span", { class: "s2-mono" }, String(id).replace(/^[a-z]+:/, "")))))
      : null);
}

/** The agent's cost and time for one round, from the round's own usage and stamps. */
function roundCost(r) {
  const a = r.agent || {};
  const bits = [money(a.cost_usd_list_estimate ?? a.cost_usd), a.duration_ms ? seconds(a.duration_ms) : roundSeconds(r)].filter(Boolean);
  return bits.length ? h("p", { class: "rb-round-cost muted small" }, bits.join(" · ")) : null;
}

function errorWords(err) {
  const kind = {
    usage_limit: "The agent hit its usage limit",
    timeout: "The agent step timed out",
    cli: "The agent did not finish",
    internal: "Studio stopped the round with an error",
    git: "Studio could not commit the result",
    baseline: "The first check of the current state could not run",
    branch_moved: "The target branch moved while the build ran, so it was not updated",
    branch_checked_out: "The target branch got checked out during the build, so it was not updated",
    tree_dirty: "Your working tree has uncommitted changes, so the target branch was not updated",
    merge_conflict: "Your latest commits conflict with the target branch",
  }[err.kind] || "The round had an error";
  const tag = err.kind ? ` [${err.kind}]` : "";
  return `${kind}${tag}${err.message ? `: ${err.message}` : "."}`;
}

/** One line per gate command (exit code, seconds) with its output tail behind a disclosure. */
function gateList(gates) {
  return h("ul", { class: "rb-gates", "aria-label": "Gates" }, gates.map((g) => {
    const ok = g.exit_code === 0;
    const tail = String(g.output_tail || "").trimEnd();
    const label = h("span", { class: "rb-gate-line" },
      icon(ok ? "check" : "diamond"),
      h("code", { class: "s2-mono" }, g.command),
      h("span", { class: ok ? "rb-gate-ok" : "rb-gate-bad" }, ok ? "exit 0" : `exit ${g.exit_code}`),
      h("span", { class: "muted" }, seconds((Number(g.seconds) || 0) * 1000)));
    return h("li", { class: ["rb-gate", ok ? "is-pass" : "is-fail"] },
      tail
        ? h("details", {}, h("summary", {}, label), h("pre", { class: "rb-gate-out" }, tail))
        : label);
  }));
}

/** Tokens, time, rounds and the estimated API value of a scoped build. */
function metricsBlock(rebuild) {
  const u = rebuild.usage || {};
  const uncached = u.input_tokens || 0;
  const read = u.cache_read_input_tokens || 0;
  const write = u.cache_creation_input_tokens || 0;
  const tokensIn = uncached + read + write;
  const out = u.output_tokens || 0;
  const wall = u.wall_s ?? (u.duration_ms ? u.duration_ms / 1000 : null);
  const cost = u.cost_usd_list_estimate ?? u.cost_usd;
  const rows = [
    ["Tokens in", `${num(tokensIn)}${read || write ? ` (${num(uncached)} new, ${num(read)} cache read, ${num(write)} cache write)` : ""}`],
    ["Tokens out", num(out)],
    ["Time", wall !== null && wall !== undefined ? seconds(wall * 1000) : elapsed(rebuild)],
    ["Rounds", `${(rebuild.rounds || []).filter((r) => r.check || roundCrashed(r)).length} of up to ${rebuild.max_rounds}`],
    ["Estimated API value", cost ? `${money(cost)} (list-price estimate, not charged)` : "none recorded"],
  ];
  const m = rebuild.metrics;
  if (m) rows.push(["Metrics hook", `${m.phase || "ran"}${m.exit_code === 0 ? ", ok" : `, exit ${m.exit_code}`}`]);
  return h("dl", { class: "rb-metrics", "aria-label": "Metrics" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

/** The result block of a scoped build that passed and landed on its target branch. */
function scopedPassed(rebuild, { files, shown, usage, warnings, cmd, openCode }) {
  const range = rebuild.commit_range || {};
  const target = rebuild.target_branch || "the target branch";
  const u = rebuild.commit || {};
  const lines = (u.insertions || u.deletions) ? ` · +${num(u.insertions)} −${num(u.deletions)} lines` : "";
  return h("section", { class: "rb-result is-passed", "aria-label": "Result" },
    h("h3", {}, `${rebuild.scope.kind === "all" ? "Everything" : rebuild.scope.title} is built on `, h("span", { class: "s2-mono" }, target)),
    h("dl", { class: "rb-facts" },
      h("dt", {}, "Target branch"),
      h("dd", {}, h("span", { class: "s2-mono" }, target), " was updated",
        rebuild.created_branch ? ` (created from ${rebuild.base_ref || "HEAD"})` : ""),
      h("dt", {}, "Working branch"),
      h("dd", {}, h("span", { class: "s2-mono" }, rebuild.branch || "none"), rebuild.branch ? copyButton(rebuild.branch, "Copy branch") : null),
      h("dt", {}, "Commits"),
      h("dd", {}, h("span", { class: "s2-mono" }, `${short(range.from)}..${short(range.to || u.sha)}`),
        range.from && (range.to || u.sha) ? copyButton(`${short(range.from)}..${short(range.to || u.sha)}`, "Copy range") : null),
      h("dt", {}, "Files"),
      h("dd", {}, `${plural(files.length, "file")} changed${lines}`)),
    shown.length ? h("ul", { class: "rb-files-list", "aria-label": "Files changed" }, shown.map((f) => h("li", {}, f))) : null,
    warnings,
    usage,
    h("div", { class: "rb-merge" },
      rebuild.merge_hint ? h("p", { class: "muted small" }, rebuild.merge_hint) : null,
      cmd && /^git merge/.test(cmd) ? h("div", { class: "rb-cmd" }, h("code", { class: "s2-mono" }, cmd), copyButton(cmd, "Copy command")) : null),
    files.length
      ? h("button", { type: "button", class: "btn", onclick: () => openCode(`code:${files[0]}`) }, icon("text-lines"), "Open in Code lens")
      : null,
  );
}

/** The Rebuild card as a DOM element. opts: {changes, onStop, onEdit, onAgain, openCode,
 *  onShow, onChanged}. Everything has a default, so `rebuildCard(r)` works anywhere. */
export function rebuildCard(rebuild, opts = {}) {
  // opts.changes may be change objects or bare ids (the conversation passes ids).
  const known = state.model?.changes || [];
  const changes = (opts.changes || known).map((c) => (typeof c === "string" ? known.find((x) => x.id === c) || { id: c } : c));
  const byId = new Map(changes.map((c) => [c.id, c]));
  const live = LIVE.has(rebuild.status);
  const st = STATUS[rebuild.status] || STATUS.running;
  const rounds = rebuild.rounds || [];
  const maxRounds = rebuild.max_rounds;
  const current = rounds.length;
  const animate = live || opts.animate;
  const ids = rebuild.changes || [];
  const openCode = opts.openCode || ((t) => update({ codeTarget: t, view: "code" }));
  const stopBtn = h("button", {
    type: "button", class: "btn",
    onclick: async (e) => {
      e.target.disabled = true;
      try {
        if (opts.onStop) await opts.onStop(rebuild);
        else await api.rebuildStop(rebuild.id);
        opts.onChanged?.();
      } catch (err) {
        e.target.disabled = false;
        toast(`Could not stop the rebuild: ${err.message}`);
      }
    },
  }, "Stop");

  const scope = rebuild.scope || null;
  const baselineNow = rebuild.status === "baseline";
  const scopeTitle = scope ? (scope.kind === "all" ? "Everything" : scope.title || scope.id) : null;
  const est = money(rebuild.usage?.cost_usd_list_estimate ?? rebuild.usage?.cost_usd);
  const head = h("header", { class: "rb-head" },
    h("div", {},
      h("h2", {}, scope
        ? `Build ${scopeTitle}`
        : ids.map((id) => `${id}${byId.get(id)?.request ? ` · ${byId.get(id).request}` : ""}`).join(" and ") || `Rebuild ${rebuild.id}`),
      h("p", { class: "rb-meta" },
        scope && rebuild.target_branch ? [`onto `, h("span", { class: "s2-mono" }, rebuild.target_branch), " · "] : null,
        `${rebuild.model} · `,
        baselineNow ? "checking the current state"
          : live ? `round ${Math.max(current, 1)} of ${maxRounds}` : `${plural(current, "round")} of up to ${maxRounds}`,
        ` · ${elapsed(rebuild)}`,
        est ? ` · ${est} estimated` : ""),
    ),
    h("div", { class: "row-actions" },
      h("span", { class: ["rb-status", `is-${rebuild.status}`], role: "status" }, icon(st.icon), st.label),
      live ? stopBtn : null,
    ),
  );

  const timeline = rounds.length
    ? h("ol", { class: "rb-rounds", "aria-label": "Rounds" }, rounds.map((r) => roundRow(r, rebuild, opts, animate)))
    : h("p", { class: "muted" }, baselineNow
      ? "Running the checks once on the current code, to find what this build has to fix. The first round starts after that."
      : live ? "Queued. The first round starts shortly." : "No round ran.");

  const targets = rebuild.targets || [];
  const targetRow = (t) => {
    const key = sk(rebuild.id, "t", t.id, t.done);
    const ticked = t.done && live && !seen.has(key);
    seen.add(key);
    return h("li", { class: ["rb-target", t.done ? "is-done" : "is-waiting", ticked && "is-ticked"] },
      icon(t.done ? "check" : "pending"),
      h("span", {}, (scope && t.id ? String(t.id).replace(/^[a-z]+:/, "") : t.label) || String(t.id || "").replace(/^[a-z]+:/, ""), h("span", { class: "s2-sr" }, t.done ? " (passing)" : " (not built yet)")));
  };
  const done = targets.filter((t) => t.done).length;
  const targetList = targets.length
    ? h("section", { "aria-label": "Checks the rebuild must turn green" },
        h("h3", { class: "s2-section-title" }, scope
          ? `Checks in this scope: ${done} of ${targets.length} passing`
          : `Checks not built yet: ${done} of ${targets.length} passing`),
        h("ul", { class: "rb-targets" }, targets.slice(0, 10).map(targetRow)),
        targets.length > 10
          ? h("details", { class: "rb-more" }, h("summary", {}, `Show the other ${targets.length - 10}`),
              h("ul", { class: "rb-targets" }, targets.slice(10).map(targetRow)))
          : null)
    : null;

  const can = canRebuild();
  const branchLine = rebuild.branch
    ? h("p", { class: "rb-branch" }, scope ? "Working branch " : "Branch ", h("code", { class: "s2-mono" }, rebuild.branch), copyButton(rebuild.branch, "Copy branch"),
        rebuild.base ? h("span", { class: "muted small" }, ` from ${String(rebuild.base).slice(0, 7)}`) : null)
    : null;
  const u = rebuild.usage || {};
  const usageBits = [money(u.cost_usd_list_estimate ?? u.cost_usd), u.duration_ms ? seconds(u.duration_ms) : "",
    u.input_tokens || u.output_tokens ? `${(u.input_tokens || 0) + (u.output_tokens || 0)} tokens` : ""].filter(Boolean);
  const usage = scope ? metricsBlock(rebuild)
    : usageBits.length ? h("p", { class: "muted small" }, `Estimated API value, not charged: ${usageBits.join(" · ")}`) : null;
  const warnings = (rebuild.warnings || []).length
    ? h("ul", { class: "rb-warnings", "aria-label": "Warnings" }, rebuild.warnings.map((w) => h("li", {}, icon("triangle"), h("span", {}, w))))
    : null;
  const errorLine = rebuild.error ? h("p", { class: "rb-round-error", role: "note" }, icon("triangle"), h("span", {}, errorWords(rebuild.error))) : null;

  let result = null;
  if (rebuild.status === "passed" && rebuild.commit) {
    const nameStatus = rebuild.commit.name_status || [];
    const files = rebuild.commit.files_changed || [];
    const shown = nameStatus.length ? nameStatus : files;
    const hintCmd = (String(rebuild.merge_hint || "").match(/`(git merge [^`]+)`/) || [])[1];
    const cmd = hintCmd || (rebuild.branch ? `git merge ${rebuild.branch}` : null);
    result = scope ? scopedPassed(rebuild, { files, shown, usage, warnings, cmd: hintCmd || null, openCode }) : h("section", { class: "rb-result is-passed", "aria-label": "Result" },
      h("h3", {}, rebuild.branch ? `Built on ${rebuild.branch}` : "Built"),
      h("p", {}, h("code", { class: "s2-mono" }, String(rebuild.commit.sha).slice(0, 7)), ` ${String(rebuild.commit.message || "").split("\n")[0]}`),
      shown.length ? h("ul", { class: "rb-files-list", "aria-label": "Files changed" }, shown.map((f) => h("li", {}, f))) : null,
      usage,
      h("div", { class: "rb-merge" },
        h("p", {}, "The running app is unchanged until you merge this branch yourself."),
        rebuild.merge_hint ? h("p", { class: "muted small" }, rebuild.merge_hint) : null,
        cmd ? h("div", { class: "rb-cmd" }, h("code", { class: "s2-mono" }, cmd), copyButton(cmd, "Copy command")) : null,
      ),
      files.length
        ? h("button", { type: "button", class: "btn", onclick: () => openCode(`code:${files[0]}`) }, icon("text-lines"), "Open in Code lens")
        : null,
    );
  } else if (rebuild.status === "gave_up") {
    const last = rounds[rounds.length - 1];
    const left = last?.check?.findings || [];
    const before = roundCrashed(last); // an error, and no gate ran
    const commitFailed = !before && rebuild.error?.kind === "git";
    const headline = before || commitFailed ? `Stopped by an error after ${plural(current, "round")}` : `Gave up after ${plural(current, "round")}`;
    const banner = before
      ? "The rebuild stopped before the checks ran."
      : commitFailed
        ? "The checks passed, but Studio could not commit the result."
        : left.length ? `${plural(left.length, "finding")} left:` : "The checks still fail.";
    result = h("section", { class: "rb-result is-gave_up", "aria-label": "Result" },
      h("h3", {}, headline),
      h("p", {}, banner),
      left.length ? h("div", { class: "rb-findings rb-findings-flat" }, left.map((f) => findingChip(f, opts))) : null,
      errorLine,
      scope && rebuild.error ? h("p", { class: "rb-next" }, buildRefusal(rebuild.error, { branch: rebuild.target_branch })) : null,
      warnings,
      usage,
      rebuild.commit?.sha && scope
        ? h("p", { class: "muted small" }, "The build commit ", h("code", { class: "s2-mono" }, short(rebuild.commit.sha)), " is kept on ",
            h("code", { class: "s2-mono" }, rebuild.branch), `; ${rebuild.target_branch || "the target branch"} was not updated.`)
        : rebuild.branch
        ? h("p", { class: "muted small" }, "The agent's uncommitted edits stay on ", h("code", { class: "s2-mono" }, rebuild.branch),
            rebuild.worktree ? [" in the worktree ", h("code", { class: "s2-mono" }, rebuild.worktree)] : null,
            scope ? `; ${rebuild.target_branch || "the target branch"} is unchanged.` : "; nothing is merged.")
        : null,
      h("div", { class: "row-actions" },
        ids.length ? h("button", {
          type: "button", class: "btn",
          onclick: () => (opts.onEdit ? opts.onEdit(ids[0]) : hooks.change?.(`change:${ids[0]}`, [])),
        }, "Edit the change") : null,
        scope ? (opts.onAgain ? h("button", {
          type: "button", class: "btn btn-primary",
          onclick: async (e) => {
            e.currentTarget.disabled = true;
            try {
              await opts.onAgain(rebuild);
              opts.onChanged?.();
            } catch (err) {
              e.target.disabled = false;
              toast(`Could not build: ${err.message}`);
            }
          },
        }, icon("play"), "Build again") : null) : can ? h("button", {
          type: "button", class: "btn btn-primary",
          onclick: async (e) => {
            e.currentTarget.disabled = true;
            try {
              if (opts.onAgain) await opts.onAgain(ids);
              else {
                const { id } = await api.rebuild(ids);
                update({ rebuild: id });
              }
              opts.onChanged?.();
            } catch (err) {
              e.target.disabled = false;
              toast(`Could not rebuild: ${err.message}`);
            }
          },
        }, icon("play"), "Rebuild again") : h("span", { class: "muted small" }, REBUILD_WHY),
      ),
    );
  } else if (rebuild.status === "stopped") {
    result = h("section", { class: "rb-result", "aria-label": "Result" },
      h("h3", {}, "Stopped"),
      h("p", { class: "muted" }, scope
        ? `The completed rounds stay on record and the worktree was removed. ${rebuild.target_branch || "The target branch"} is unchanged.`
        : "The completed rounds stay on record and the worktree was removed. The change is back to accepted, not built."),
      usage);
  }

  const notice = live ? h("p", { class: "muted small rb-notice" }, scope
    ? "The agent works in an isolated worktree. Your checkout is not touched, and Studio decides when it is done by running the checks itself."
    : "The agent works in an isolated worktree. Your checkout is not touched, and Studio decides when it is done by running the gates itself.") : null;
  return h("article", { class: "rb-card", "aria-label": `Rebuild ${rebuild.id}` }, head, branchLine, timeline, targetList, notice, result);
}

// ------------------------------------------------------------------ the view

function groupHistory(model) {
  const byId = new Map();
  for (const c of model.changes || []) {
    const rid = c.build?.rebuild_id;
    if (!rid) continue;
    if (!byId.has(rid)) byId.set(rid, []);
    byId.get(rid).push(c);
  }
  return [...byId].map(([id, changes]) => ({ id, changes })).sort((a, b) => b.id.localeCompare(a.id, undefined, { numeric: true }));
}

export function render(model, root, ctx) {
  const history = groupHistory(model);
  const waiting = (model.changes || []).filter((c) => c.build?.state === "not_built");
  const shownId = state.rebuild || history[0]?.id || null;
  add(root, h("header", { class: "view-head" },
    h("h1", {}, "Rebuilds"),
    h("p", { class: "lede" }, "Accepting a change reinforces the contract; a rebuild hands it to the coding agent, which edits the code in its own worktree and branch while Studio runs the gates every round until they pass or the limit is reached. Studio never merges: you do."),
  ));

  const layout = h("div", { class: "rb-layout" });
  add(root, layout);
  const slot = h("div", { "aria-live": "polite" });
  let stop = null;


  async function startFor(ids) {
    try {
      const { id } = await api.rebuild(ids);
      update({ rebuild: id });
      show(id);
    } catch (e) {
      toast(e.unsupported ? e.message : `Rebuild could not start: ${e.message}`);
    }
  }

  if (waiting.length) {
    add(layout, h("section", { class: "rb-waiting", "aria-label": "Accepted, not built" },
      h("p", {}, h("strong", {}, `${plural(waiting.length, "accepted change")} not built yet: `),
        waiting.map((c) => `${c.id} ${c.request}`).join("; ")),
      canRebuild()
        ? h("button", { type: "button", class: "btn btn-primary", onclick: () => startFor(waiting.map((c) => c.id)) }, icon("play"), "Rebuild")
        : h("p", { class: "muted small", id: "rebuild-why-view" }, REBUILD_WHY),
    ));
  }
  add(layout, slot);

  const histEl = h("section", { "aria-label": "Rebuild history" });
  add(layout, histEl);
  function drawHistory(infoById) {
    histEl.replaceChildren(
      h("h2", { class: "s2-section-title" }, "History"),
      history.length
        ? h("ul", { class: "rb-history" }, history.map((r) => {
            const info = infoById?.get(r.id);
            const st = STATUS[info?.status] || null;
            return h("li", { class: ["rb-hist-row", r.id === state.rebuild && "is-current"] },
              h("span", { class: "s2-mono" }, r.id),
              h("div", {},
                h("button", { type: "button", class: "s2-quiet-btn", onclick: () => { update({ rebuild: r.id }); show(r.id); } },
                  r.changes.map((c) => `${c.id} ${c.request}`).join("; ")),
                info ? h("p", { class: "rb-meta" }, `${plural((info.rounds || []).filter((x) => x.check).length, "round")} · ${info.model}`) : null),
              h("span", { class: ["rb-status", `is-${info?.status || "queued"}`] }, icon(st?.icon || "dash"), st?.label || "…"),
            );
          }))
        : h("p", { class: "s2-empty" }, "No rebuild has run yet. Accept a change, then Rebuild."),
    );
  }

  function show(id) {
    stop?.();
    stop = null;
    slot.replaceChildren(h("p", { class: "muted", "aria-busy": "true" }, "Loading the rebuild…"));
    const opts = {
      openCode: ctx.openCode,
      onEdit: (cid) => ctx.changeAbout(`change:${cid}`),
      onChanged: () => show(id),
    };
    stop = pollRebuild(id, (r, err) => {
      if (err) {
        slot.replaceChildren(h("p", { class: "s2-empty", role: "status" }, err.unsupported ? err.message : err.message));
        return;
      }
      slot.replaceChildren(rebuildCard(r, opts));
      if (!LIVE.has(r.status) && !refreshed.has(sk(r.id, r.status))) {
        // the rebuild just ended: the tray and the top bar follow the new build state
        refreshed.add(sk(r.id, r.status));
        api.model().then((m) => update({ model: m, connected: true })).catch(() => {});
      }
    }, root);
    drawHistory(infoCache);
  }

  const infoCache = new Map();
  Promise.all(history.map((r) => api.rebuildGet(r.id).then((x) => infoCache.set(r.id, x)).catch(() => {}))).then(() => {
    if (root.isConnected) drawHistory(infoCache);
  });
  drawHistory(infoCache);

  if (shownId) show(shownId);
  else slot.replaceChildren(h("div", { class: "s2-empty" },
    h("p", {}, "No rebuild is running."),
    h("p", { class: "muted" }, canRebuild() ? "Accept a change in the conversation, then press Rebuild in the top bar." : REBUILD_WHY)));

  // keep the pending list and history fresh as the model changes; stop polling on exit
  const unsub = subscribe((st, patch) => {
    if (!root.isConnected) {
      stop?.();
      unsub();
    }
  });
}
