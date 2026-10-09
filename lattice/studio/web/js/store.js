// The one app state object, plus a tiny subscribe/notify loop.

export const state = {
  model: null, // GET /api/model
  connected: false, // last /api/model fetch succeeded
  loadError: null,
  view: "rules", // current left-nav view
  overlay: false, // show app pass/fail on items (only when last_check.targets exists)
  selection: [], // targets the owner clicked; sent with ask/change
  marked: [], // targets highlighted by highlight()
  focusTarget: null, // the first highlighted target: scrolled to and pulsed once
  change: null, // the open change set (GET /api/changes/{id}) when status is ready
  checkRunning: false,
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
