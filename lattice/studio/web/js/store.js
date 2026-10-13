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
  // v1.8 build from the spec
  buildArea: null, // an area id the Build view should open preselected (set by "Build…" links)
  buildRun: null, // the scoped build in view: {phase: starting|live|done|stopped|error, id, title, branch, record, message}
  // v1.9 build plans
  planView: null, // id of the plan open in the Build view, or null (the plans list and the start form)
  planRun: null, // the plan in view: {id, record}; kept fresh by views/plan.js
  planActive: null, // the plan record that is planning or running (the tray follows it), or null
  // v1.6 projects
  projects: null, // GET /api/projects (array) or null before it loaded / when it failed
  project: null, // the project the owner picked (name), or null for the server's default
  workspace: null, // v1.16: GET /api/workspace when it names a workspace, else null
  switching: false, // a project switch is loading its model
};

/** The project record in force: the picked one, else the default, else the first. */
export function currentProject() {
  const list = state.projects || [];
  return (state.project && list.find((p) => p.name === state.project))
    || list.find((p) => p.default) || list[0] || null;
}

/** Whether this project can rebuild: false (with a reason shown), or true. Unknown (no
 *  project list, an older server) counts as true: the POST answers 503 with the reason. */
export function canRebuild() {
  const p = currentProject();
  if (!p || !p.capabilities || p.capabilities.rebuild === undefined) return true;
  return p.capabilities.rebuild === true;
}

/** Whether this project can build from the spec (GET /api/projects capabilities). Unknown
 *  (no project list) counts as true: the areas call then says what is wrong. */
export function canBuild() {
  const p = currentProject();
  if (!p) return true;
  return Boolean(p.capabilities && p.capabilities.build_from_spec === true);
}

/** Whether this project can plan builds (capabilities.build_plan, API v1.9). Unknown counts
 *  as true when there is no project list; an older server (the flag is absent) as false. */
export function canPlan() {
  const p = currentProject();
  if (!p) return true;
  return Boolean(p.capabilities && p.capabilities.build_plan === true);
}

export const PLAN_WHY = "Build plans need a git repository and a [rebuild] section in studio.toml with at least one gate command, on a Studio server that supports API v1.9.";

export const BUILD_WHY = "Build from the spec needs a git repository and a [rebuild] section in studio.toml with at least one gate command.";

export const REBUILD_WHY = "Rebuild needs a git repository and a gate command: add [rebuild] to studio.toml.";

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
