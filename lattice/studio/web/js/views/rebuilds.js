// Evidence > Rebuilds: the live Rebuild card for the rebuild in state.rebuild (polled every
// second while it runs) and the history of rebuilds. rebuildCard() and pollRebuild() are
// exported so the conversation panel embeds the same card.

import { h, icon, add, plural } from "../util.js";
import { api } from "../api.js";
import { state, update, subscribe, hooks } from "../store.js";
import { highlight } from "../targets.js";
import { toast } from "../tray.js";

const LIVE = new Set(["queued", "running"]);
const STATUS = {
  queued: { label: "Queued", icon: "pending" },
  running: { label: "Rebuilding", icon: "play" },
  passed: { label: "Passed", icon: "check" },
  gave_up: { label: "Gave up", icon: "diamond" },
  stopped: { label: "Stopped", icon: "dash" },
};
const LAYERS = ["A", "B", "C", "D", "X"];

// Round rows already shown, so only a newly finished round resolves with motion.
const seen = new Set();

// ------------------------------------------------------------------ polling

/** Poll GET /api/rebuild/{id} every second while it is queued or running.
 *  onUpdate(rebuild, error). Returns stop(). Stops by itself when `anchor` leaves the DOM. */
export function pollRebuild(id, onUpdate, anchor = null) {
  let stopped = false;
  let timer = null;
  let first = true;
  const tick = async () => {
    if (stopped || (!first && anchor && !anchor.isConnected)) return;
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

function elapsed(rebuild) {
  const start = Date.parse(rebuild.started);
  if (Number.isNaN(start)) return "";
  let end = Date.now();
  if (!LIVE.has(rebuild.status)) {
    const last = (rebuild.rounds || []).map((r) => Date.parse(r.started)).filter((x) => !Number.isNaN(x));
    end = last.length ? Math.max(...last) : start;
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
  return h("details", { class: "rb-finding" },
    h("summary", { title: "Open the counterexample" }, icon("diamond"), h("span", {}, name)),
    h("div", { class: "rb-finding-body" },
      h("p", {}, f.title || ""),
      f.target
        ? h("button", { type: "button", class: "s2-quiet-btn", onclick: () => (opts.onShow || highlight)([f.target]) }, `Show ${f.target} on the views`)
        : null,
    ),
  );
}

function roundRow(r, rebuild, opts, animate) {
  const key = `${rebuild.id}:${r.n}:${r.check ? "done" : "live"}`;
  const isNew = animate && !seen.has(key);
  seen.add(key);
  const c = r.check;
  const verdict = c
    ? h("div", { class: ["rb-verdict", c.verdict === "pass" ? "is-pass" : "is-fail"] },
        h("span", { class: "rb-verdict-word" }, icon(c.verdict === "pass" ? "check" : "diamond"), c.verdict === "pass" ? "Passes" : "Fails"),
        layerCounts(c.counts))
    : h("div", { class: "rb-verdict is-working" },
        h("span", { class: "rb-verdict-word" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Agent working"));
  return h("li", { class: ["rb-round", isNew && "is-new"], "data-round": String(r.n) },
    h("span", { class: "rb-round-n" }, `Round ${r.n}`),
    h("div", {},
      h("p", { class: "rb-summary" }, r.agent_summary || (c ? "No summary." : "The agent is editing the code and running the checker.")),
      (r.files_changed || []).length ? h("p", { class: "rb-files" }, r.files_changed.join(", ")) : null,
    ),
    verdict,
    c && (c.findings || []).length
      ? h("div", { class: "rb-findings" }, c.findings.map((f) => findingChip(f, opts)))
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

  const head = h("header", { class: "rb-head" },
    h("div", {},
      h("h2", {}, ids.map((id) => `${id}${byId.get(id)?.request ? ` · ${byId.get(id).request}` : ""}`).join(" and ") || `Rebuild ${rebuild.id}`),
      h("p", { class: "rb-meta" },
        `${rebuild.model} · `,
        live ? `round ${Math.max(current, 1)} of ${maxRounds}` : `${plural(current, "round")} of up to ${maxRounds}`,
        ` · ${elapsed(rebuild)}`),
    ),
    h("div", { class: "row-actions" },
      h("span", { class: ["rb-status", `is-${rebuild.status}`], role: "status" }, icon(st.icon), st.label),
      live ? stopBtn : null,
    ),
  );

  const timeline = rounds.length
    ? h("ol", { class: "rb-rounds", "aria-label": "Rounds" }, rounds.map((r) => roundRow(r, rebuild, opts, animate)))
    : h("p", { class: "muted" }, live ? "Queued. The first round starts shortly." : "No round ran.");

  const targets = rebuild.targets || [];
  const done = targets.filter((t) => t.done).length;
  const targetList = targets.length
    ? h("section", { "aria-label": "Checks the rebuild must turn green" },
        h("h3", { class: "s2-section-title" }, `Checks not built yet: ${done} of ${targets.length} passing`),
        h("ul", { class: "rb-targets" }, targets.map((t) => {
          const key = `${rebuild.id}:t:${t.id}:${t.done}`;
          const ticked = t.done && live && !seen.has(key);
          seen.add(key);
          return h("li", { class: ["rb-target", t.done ? "is-done" : "is-waiting", ticked && "is-ticked"] },
            icon(t.done ? "check" : "pending"),
            h("span", {}, t.label, h("span", { class: "s2-sr" }, t.done ? " (passing)" : " (not built yet)")));
        })))
    : null;

  let result = null;
  if (rebuild.status === "passed" && rebuild.commit) {
    const files = rebuild.commit.files_changed || [];
    result = h("section", { class: "rb-result is-passed", "aria-label": "Result" },
      h("p", {}, h("code", { class: "s2-mono" }, String(rebuild.commit.sha).slice(0, 7)), ` ${rebuild.commit.message || ""}`),
      files.length ? h("ul", {}, files.map((f) => h("li", {}, f))) : null,
      h("p", { class: "muted" }, "Amber marks cleared: the changed elements are plain again."),
      h("button", {
        type: "button", class: "btn btn-primary",
        onclick: () => openCode(`code:${files[0] || ""}`),
      }, icon("text-lines"), "Open in Code lens"),
    );
  } else if (rebuild.status === "gave_up") {
    const last = rounds[rounds.length - 1];
    const left = last?.check?.findings || [];
    result = h("section", { class: "rb-result is-gave_up", "aria-label": "Result" },
      h("h3", {}, `Gave up after ${plural(current, "round")}`),
      h("p", {}, left.length ? `${plural(left.length, "finding")} left:` : "The checker still fails."),
      left.length ? h("div", { class: "rb-findings" }, left.map((f) => findingChip(f, opts))) : null,
      h("div", { class: "row-actions" },
        h("button", {
          type: "button", class: "btn",
          onclick: () => (opts.onEdit ? opts.onEdit(ids[0]) : hooks.change?.(`change:${ids[0]}`, [])),
        }, "Edit the change"),
        h("button", {
          type: "button", class: "btn btn-primary",
          onclick: async () => {
            try {
              if (opts.onAgain) await opts.onAgain(ids);
              else {
                const { id } = await api.rebuild(ids);
                update({ rebuild: id });
              }
              opts.onChanged?.();
            } catch (err) {
              toast(`Could not rebuild: ${err.message}`);
            }
          },
        }, "Rebuild again"),
      ),
    );
  } else if (rebuild.status === "stopped") {
    result = h("section", { class: "rb-result", "aria-label": "Result" },
      h("h3", {}, "Stopped"),
      h("p", { class: "muted" }, "The completed rounds stay on record. The change is back to accepted, not built."));
  }

  return h("article", { class: "rb-card", "aria-label": `Rebuild ${rebuild.id}` }, head, timeline, targetList, result);
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
    h("p", { class: "lede" }, "Accepting a change reinforces the contract; a rebuild hands it to the coding agent, which edits the code and runs the checker every round until it passes or reaches its limit."),
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
      h("button", { type: "button", class: "btn btn-primary", onclick: () => startFor(waiting.map((c) => c.id)) }, icon("play"), "Rebuild"),
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
    h("p", { class: "muted" }, "Accept a change in the conversation, then press Rebuild in the top bar.")));

  // keep the pending list and history fresh as the model changes; stop polling on exit
  const unsub = subscribe((st, patch) => {
    if (!root.isConnected) {
      stop?.();
      unsub();
    }
  });
}
