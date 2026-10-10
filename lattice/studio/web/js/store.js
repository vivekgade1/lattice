// The one app state object, plus a tiny subscribe/notify loop.

export const state = {
  model: null, // GET /api/model
  connected: false, // last /api/model fetch succeeded
  loadError: null,
  view: "map", // current navigator view (home is the Map)
  overlay: false, // show app pass/fail on items (only when last_check.targets exists)
  selection: [], // targets the owner clicked; sent with ask/change
  marked: [], // targets highlighted by highlight()
  focusTarget: null, // the first highlighted target: scrolled to and pulsed once
  change: null, // the open change set (GET /api/changes/{id}) when status is ready
  checkRunning: false,
  // Studio 2
  lens: "intent", // "intent" | "code" | "both": what the canvas of an Intent view shows
  mode: "ask", // conversation mode: "ask" | "explain" | "change"
  codeTarget: null, // the target whose code the Code lens / code pane shows
  rebuild: null, // id of the rebuild shown in the Rebuilds view (string) or null
  trayOpen: false, // the change tray is open (280px) instead of collapsed (44px)
  edits: [], // pending inline edits [{target, kind, before, after}] owned by edit.js
  palette: false, // the ⌘K search dialog is open
  changesSeen: 0, // bumped when the conversation learns a change moved (the tray re-draws)
  rulesScope: null, // {name, ids}: the Map opened Rules for one element; Rules shows only these
};

// Hook registry. The shell gives every view `ctx.hooks`; the conversation and inline-edit
// modules replace these defaults (hooks.ask = ..., etc.) when they mount. Each takes
// (target, selection) and returns nothing.
export const hooks = {
  ask: null,
  change: null,
  edit: null,
};

const subs = new Set();

export function subscribe(fn) {
  subs.add(fn);
  return () => subs.delete(fn);
}

export function update(patch) {
  Object.assign(state, patch);
  for (const fn of subs) fn(state, patch);
}
