// The change tray: a bar at the bottom of the shell. Collapsed (44px) it counts the
// changes in flight ("2 changes ready · 1 rebuilding"); open (280px) it lists every change
// with its status, request and actions. Data comes from state.model.changes.
//
// Opening a change row dispatches window event "studio:open-change" {detail: {id}}; the
// conversation panel (chat.js) listens and shows the change card.

import { h, clear, add, icon, plural } from "./util.js";
import { api } from "./api.js";
import { state, update, subscribe, canRebuild, REBUILD_WHY } from "./store.js";

/** The one human status of a change: drafting, checking, ready, accepted · not built,
 *  rebuilding, built, gave up, failed, rejected, cancelled. */
export function changeStatus(c) {
  if (c.status === "accepted") {
    const b = c.build?.state || "not_built";
    return { not_built: "accepted · not built", rebuilding: "rebuilding", built: "built", gave_up: "gave up" }[b] || "accepted";
  }
  return c.superseded_by && c.status === "rejected" ? `superseded by ${c.superseded_by}` : c.status;
}

/** The words the tray shows for a change (sentence case; changeStatus keeps the short
 *  keys other modules compare against). */
export function trayStatus(c) {
  const s = changeStatus(c);
  if (c.status === "accepted") {
    const b = c.build?.state || "not_built";
    if (b === "not_built") return "Accepted, not built";
    if (b === "rebuilding") return "Rebuilding";
    if (b === "built") return c.build?.branch ? `Built on ${c.build.branch}` : "Built";
    if (b === "gave_up") return "Gave up";
  }
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function tone(c) {
  const s = changeStatus(c);
  if (s === "built") return "ok";
  if (s === "gave up") return "warn";
  if (s === "failed") return "bad";
  if (["drafting", "checking", "rebuilding", "ready", "accepted · not built"].includes(s)) return "live";
  return "quiet";
}

const ACTIVE = new Set(["drafting", "checking", "ready", "accepted · not built", "rebuilding", "failed", "gave up"]);

export function summarize(changes) {
  const n = { ready: 0, working: 0, notBuilt: 0, rebuilding: 0, failed: 0 };
  for (const c of changes) {
    const s = changeStatus(c);
    if (s === "ready") n.ready++;
    else if (s === "drafting" || s === "checking") n.working++;
    else if (s === "accepted · not built") n.notBuilt++;
    else if (s === "rebuilding") n.rebuilding++;
    else if (s === "failed" || s === "gave up") n.failed++;
  }
  return n;
}

/** The scoped build in flight (Build view), or null. */
function liveBuild() {
  const r = state.buildRun;
  if (!r) return null;
  const status = r.record?.status;
  if (r.phase === "starting" || (r.phase === "live" && !["passed", "gave_up", "stopped"].includes(status))) return r;
  return null;
}

/** "Building Habits · round 2 of 3", or the baseline check before round 1. */
export function buildWords(r) {
  const rec = r.record || {};
  const title = r.title || "the spec";
  if (r.phase === "starting" || rec.status === "baseline") return `Checking the current state of ${title}`;
  const max = rec.max_rounds || "";
  const n = Math.max((rec.rounds || []).length, 1);
  return `Building ${title} · round ${n}${max ? ` of ${max}` : ""}`;
}

function barText(changes) {
  const n = summarize(changes);
  const parts = [];
  const lb = liveBuild();
  if (lb) parts.push(buildWords(lb));
  if (n.ready) parts.push(`${plural(n.ready, "change")} ready`);
  if (n.working) parts.push(`${n.working} drafting`);
  if (n.notBuilt) parts.push(`${n.notBuilt} accepted, not built`);
  if (n.rebuilding) parts.push(`${n.rebuilding} rebuilding`);
  if (n.failed) parts.push(`${n.failed} failed`);
  return parts.length ? parts.join(" · ") : "No pending changes";
}

function checkSummary(c) {
  const k = c.checks;
  if (!k) return null;
  const bits = [k.valid === false ? "invalid" : "valid"];
  if ((k.conflicts || []).length) bits.push(plural(k.conflicts.length, "conflict"));
  if (k.impact) bits.push(`impact ${k.impact.verdict}`);
  return bits.join(", ");
}

let toastTimer = null;
/** A short status message in a live region; used for failed actions. */
export function toast(message) {
  let el = document.getElementById("toast");
  if (!el) {
    el = h("div", { id: "toast", class: "toast", role: "status", "aria-live": "polite" });
    document.body.append(el);
  }
  el.textContent = message;
  el.classList.add("is-shown");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("is-shown"), 6000);
}

// Gave-up rebuilds, fetched once to show their open findings in the tray.
const gaveUp = new Map(); // `${base}${rebuild id}` -> record | "pending"

export function mountTray(root, opts = {}) {
  const onChanged = opts.onChanged || (() => {});

  function gaveUpFindings(c) {
    const rid = c.build?.rebuild_id;
    if (!rid) return null;
    const key = `${api.base()}|${rid}`;
    const got = gaveUp.get(key);
    if (got && got !== "pending") {
      const last = (got.rounds || [])[(got.rounds || []).length - 1];
      return last?.check?.findings || [];
    }
    if (!got) {
      gaveUp.set(key, "pending");
      api.rebuildGet(rid).then((r) => {
        gaveUp.set(key, r);
        render();
      }).catch(() => gaveUp.delete(key));
    }
    return null;
  }

  async function rebuildNow(c) {
    try {
      const { id } = await api.rebuild([c.id]);
      update({ view: "rebuilds", rebuild: id });
      onChanged();
    } catch (e) {
      toast(`Rebuild could not start: ${e.message}`);
    }
  }

  async function act(fn, label) {
    try {
      await fn();
      onChanged();
    } catch (e) {
      toast(`${label} failed: ${e.message}`);
    }
  }

  function row(c) {
    const s = changeStatus(c);
    const sum = checkSummary(c);
    const actions = [
      h("button", {
        type: "button", class: "btn btn-quiet",
        onclick: () => window.dispatchEvent(new CustomEvent("studio:open-change", { detail: { id: c.id } })),
      }, "Open"),
    ];
    if (c.status === "ready") {
      actions.push(
        h("button", {
          type: "button", class: "btn btn-primary", disabled: c.checks?.valid === false,
          onclick: () => act(() => api.accept(c.id), "Accept"),
        }, "Accept"),
        h("button", { type: "button", class: "btn btn-quiet", onclick: () => act(() => api.reject(c.id), "Reject") }, "Reject"),
      );
    }
    if ((c.status === "drafting" || c.status === "checking")) {
      actions.push(h("button", { type: "button", class: "btn btn-quiet", onclick: () => act(() => api.cancelChange(c.id), "Cancel") }, "Cancel"));
    }
    const b = c.status === "accepted" ? c.build?.state || "not_built" : null;
    let note = null;
    const can = canRebuild();
    if (b === "not_built") {
      if (can) {
        actions.push(h("button", { type: "button", class: "btn btn-primary", onclick: () => rebuildNow(c) }, icon("play"), "Rebuild"));
      } else {
        note = h("span", { class: "tray-note muted small" }, REBUILD_WHY);
      }
    }
    if (b === "gave_up") {
      const left = gaveUpFindings(c);
      note = h("span", { class: "tray-note small" },
        left === null ? "Checks still failing." : left.length ? `${plural(left.length, "finding")} left: ${left.slice(0, 2).map((f) => f.title || f.id).join("; ")}${left.length > 2 ? "; …" : ""}` : "Checks still failing.");
      actions.push(
        h("button", { type: "button", class: "btn btn-quiet", onclick: () => window.dispatchEvent(new CustomEvent("studio:open-change", { detail: { id: c.id } })) }, "Edit the change"),
      );
      if (can) actions.push(h("button", { type: "button", class: "btn btn-primary", onclick: () => rebuildNow(c) }, icon("play"), "Rebuild again"));
    }
    if (c.build?.rebuild_id && ["rebuilding", "gave_up", "built"].includes(c.build.state)) {
      actions.push(h("button", {
        type: "button", class: "btn btn-quiet",
        onclick: () => update({ view: "rebuilds", rebuild: c.build.rebuild_id }),
      }, "Open rebuild"));
    }
    return h("li", { class: ["tray-row", `tray-${tone(c)}`], "data-change": c.id },
      h("span", { class: "tray-id" }, c.id),
      h("span", { class: "tray-request" }, c.request || "(no request text)"),
      h("span", { class: "tray-status" }, b === "rebuilding" ? h("span", { class: "spinner", "aria-hidden": "true" }) : null, b === "rebuilding" ? " " : null, trayStatus(c)),
      sum ? h("span", { class: "tray-checks muted" }, sum) : null,
      h("span", { class: "tray-actions" }, actions),
      note,
    );
  }

  function render() {
    const changes = state.model?.changes || [];
    const open = state.trayOpen;
    root.classList.toggle("is-open", open);
    clear(root);
    const active = changes.filter((c) => ACTIVE.has(changeStatus(c)));
    const history = changes.filter((c) => !ACTIVE.has(changeStatus(c)));
    const lb = liveBuild();
    add(root,
      h("button", {
        type: "button", class: "tray-bar", id: "tray-toggle",
        "aria-expanded": open ? "true" : "false", "aria-controls": "tray-body",
        onclick: () => update({ trayOpen: !state.trayOpen }),
      },
        h("span", { class: "tray-count" }, lb ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon(active.length ? "pending" : "dash"), h("span", {}, barText(changes))),
        h("span", { class: "tray-chev muted" }, open ? "Hide" : "Show"),
      ),
      open ? h("div", { class: "tray-body", id: "tray-body" },
        lb ? h("ul", { class: "tray-list" }, h("li", { class: "tray-row tray-live", "data-build": lb.id || "" },
          h("span", { class: "tray-id" }, lb.id || "build"),
          h("span", { class: "tray-request" }, `${buildWords(lb)}${lb.branch ? ` · onto ${lb.branch}` : ""}`),
          h("span", { class: "tray-status" }, h("span", { class: "spinner", "aria-hidden": "true" }), " Building"),
          h("span", { class: "tray-actions" }, h("button", { type: "button", class: "btn btn-quiet", onclick: () => update({ view: "build" }) }, "Open build")))) : null,
        changes.length
          ? h("ul", { class: "tray-list" }, [...active, ...history].map(row))
          : lb ? null : h("p", { class: "muted" }, "No changes yet. Ask for one in the conversation and it appears here."),
      ) : null,
    );
  }

  subscribe((st, patch) => {
    if ("model" in patch || "trayOpen" in patch || "changesSeen" in patch || "buildRun" in patch) render();
  });
  render();
}
