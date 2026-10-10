// The chat's session client: talks to the v1.5 session routes (API.md) and folds the
// normalized event log into a thread the panel renders.
//
//   reduce(thread, events)   pure: merge a batch of events into `thread` (below)
//   SessionClient            one session of one mode: lazy create, send, poll, stop
//
// thread = {items: [...], cost, turns}. Item kinds (each has a unique `key`):
//   user    {text, selected, pending}      text    {text, partial, messageId}
//   tool    {id, name, label, input, result: null | {ok, summary}, stopped}
//   result  {ok, seconds, cost, turns}     error   {message, kind}
//   stopped {}                             (the owner stopped the turn)
// Partial text (API.md "Partial text"): each growth of a text block arrives as a new
// event under a higher seq; the latest one for a message replaces the earlier one.

import { api, sleep } from "./api.js";

export const POLL_ACTIVE_MS = 600;
export const POLL_IDLE_MS = 1500;
const FAIL_LIMIT = 6; // consecutive network failures before the poll gives up

export function newThread() {
  return { items: [], cost: 0, turns: 0, lastUser: null, seq: 0 };
}

function key(thread, kind) {
  thread.seq += 1;
  return `${kind}-${thread.seq}`;
}

function lastText(thread, messageId) {
  for (let i = thread.items.length - 1; i >= 0; i--) {
    const it = thread.items[i];
    if (it.kind === "text" && it.messageId === messageId) return it;
    if (it.kind === "user") return null;
  }
  return null;
}

/** Add a client-side error item (a failed send) to the thread. */
export function addError(thread, message, kind = "other") {
  thread.items.push({ kind: "error", key: key(thread, "error"), message, errorKind: kind });
}

/** Merge `events` into `thread`. Returns true when anything changed. */
export function reduce(thread, events) {
  let changed = false;
  for (const ev of events || []) {
    switch (ev.type) {
      case "user": {
        const pending = thread.items.find((i) => i.kind === "user" && i.pending);
        if (pending) {
          pending.pending = false;
          pending.selected = ev.selected || pending.selected;
        } else {
          thread.items.push({ kind: "user", key: key(thread, "user"), text: ev.text || "", selected: ev.selected || [], pending: false });
        }
        thread.lastUser = { text: ev.text || "", selected: ev.selected || [] };
        changed = true;
        break;
      }
      case "text": {
        const mid = ev.message_id || "";
        const cur = mid ? lastText(thread, mid) : null;
        const text = ev.text || "";
        if (cur && (cur.partial || text.startsWith(cur.text) || cur.text.startsWith(text))) {
          cur.text = text;
          cur.partial = !!ev.partial;
        } else if (text) {
          thread.items.push({ kind: "text", key: key(thread, "text"), text, partial: !!ev.partial, messageId: mid });
        }
        changed = true;
        break;
      }
      case "tool_use":
        if (!thread.items.some((i) => i.kind === "tool" && i.id === ev.id)) {
          thread.items.push({
            kind: "tool", key: key(thread, "tool"), id: ev.id, name: ev.name || "",
            label: ev.label || ev.name || "Tool", input: ev.input_summary || "", result: null, stopped: false,
          });
          changed = true;
        }
        break;
      case "tool_result": {
        const t = thread.items.find((i) => i.kind === "tool" && i.id === ev.tool_use_id);
        if (t) {
          t.result = { ok: ev.ok !== false, summary: ev.summary || "" };
          changed = true;
        }
        break;
      }
      case "result":
        thread.turns += 1;
        thread.items.push({
          kind: "result", key: key(thread, "result"), ok: ev.ok !== false,
          seconds: (Number(ev.duration_ms) || 0) / 1000, cost: Number(ev.cost_usd) || 0, turns: Number(ev.num_turns) || 0,
        });
        changed = true;
        break;
      case "error":
        thread.items.push({ kind: "error", key: key(thread, "error"), message: ev.message || "", errorKind: ev.kind || "other" });
        changed = true;
        break;
      case "status":
        if (ev.state === "interrupted") {
          for (const i of thread.items) if (i.kind === "tool" && !i.result) i.stopped = true;
          for (const i of thread.items) if (i.kind === "text") i.partial = false;
          thread.items.push({ kind: "stopped", key: key(thread, "stopped") });
          changed = true;
        }
        break;
      default:
        break;
    }
  }
  return changed;
}

export class SessionClient {
  /** `onUpdate(client)` is called after every change of thread or state. */
  constructor(mode, onUpdate) {
    this.mode = mode;
    this.onUpdate = onUpdate || (() => {});
    this.thread = newThread();
    this.id = null;
    this.state = "idle"; // idle | starting | running | interrupted
    this.after = 0;
    this.claudeSessionId = null;
    this.resumeCommand = null;
    this.cwd = null;
    this.cost = 0;
    this.disposed = false;
    this.loop = null; // the running poll loop's promise
    this.fails = 0;
  }

  get busy() {
    return this.state === "starting" || this.state === "running";
  }

  _emit() {
    if (!this.disposed) this.onUpdate(this);
  }

  async ensure(change = null) {
    if (this.id) return;
    const rec = await api.sessionCreate(this.mode, this.mode === "change" ? change : null);
    this.id = rec.id;
    this.cwd = rec.cwd || null;
    this.after = 0;
  }

  /** Send a message (creating the session on the first one). Throws the API's Error
   *  (err.status 409 a turn is running, 503 no claude binary). */
  async send(text, selected = [], change = null) {
    await this.ensure(change);
    this.thread.items.push({ kind: "user", key: key(this.thread, "user"), text, selected, pending: true });
    this.thread.lastUser = { text, selected };
    this.state = "starting";
    this._emit();
    try {
      await api.sessionMessage(this.id, text, selected);
    } catch (e) {
      const it = this.thread.items.find((i) => i.kind === "user" && i.pending);
      if (it) this.thread.items.splice(this.thread.items.indexOf(it), 1);
      this.state = e.status === 409 ? "running" : "idle";
      this._emit();
      if (e.status === 409) this.poll();
      throw e;
    }
    this.poll();
  }

  /** Start the poll loop unless it is already running. */
  poll() {
    if (this.loop || this.disposed || !this.id) return;
    this.loop = this._run().finally(() => {
      this.loop = null;
    });
  }

  async _run() {
    while (!this.disposed && this.id) {
      let out;
      try {
        out = await api.sessionEvents(this.id, this.after);
        this.fails = 0;
      } catch (e) {
        if (e.status === 404) {
          // The server forgot the session (it restarted): the next message starts a new one.
          this.id = null;
          this.state = "idle";
          this.resumeCommand = null;
          this.thread.items.push({ kind: "error", key: key(this.thread, "error"), message: "This conversation is no longer on the server. Send a message to start a new one.", errorKind: "other" });
          this._emit();
          return;
        }
        this.fails += 1;
        if (this.fails >= FAIL_LIMIT) {
          this.state = "idle";
          this.thread.items.push({ kind: "error", key: key(this.thread, "error"), message: "Lost contact with the Studio server. Check that it is running, then retry.", errorKind: "other" });
          this._emit();
          return;
        }
        await sleep(POLL_IDLE_MS);
        continue;
      }
      if (this.disposed) return;
      reduce(this.thread, out.events);
      this.after = Math.max(this.after, Number(out.next) || 0);
      this.state = out.state || "idle";
      if (out.claude_session_id) this.claudeSessionId = out.claude_session_id;
      if (out.resume_command) this.resumeCommand = out.resume_command;
      if (typeof out.cost_usd_total === "number") this.cost = out.cost_usd_total;
      this._emit();
      if (out.done || !this.busy) return;
      // Quiet for a while (Claude is thinking or a tool is slow): ease off to 1.5 s.
      this.quiet = out.events && out.events.length ? 0 : (this.quiet || 0) + 1;
      await sleep(this.quiet >= 8 ? POLL_IDLE_MS : POLL_ACTIVE_MS);
    }
  }

  /** Interrupt the running turn. Resolves quietly when none is running. */
  async stop() {
    if (!this.id || !this.busy) return;
    try {
      await api.sessionStop(this.id);
    } catch (e) {
      if (e.status !== 409) throw e;
    }
    this.poll();
  }

  dispose() {
    this.disposed = true;
  }
}
