// The JSON API of lattice/studio/API.md. Every call returns parsed JSON or throws an
// Error whose message is safe to show.

// Studio 2 endpoints that only the mock server has. A 404 whose body is the router's
// generic "no such route" (or has no body) on one of these means "unsupported here"; a 404
// that names a missing file, change or rebuild is an ordinary error.
const MOCK_ONLY = /^\/api\/(code\/|flow|graph|rebuild|explain)/;
const GENERIC_404 = /^(no such route|not found)$/i;

// The project base path (v1.6): "" for the default project (bare /api), "/p/<name>" once the
// owner picked one. `epoch` counts switches: a response that arrives for an earlier epoch
// is dropped (its promise never settles), so a slow answer of project A can never paint
// into project B. Loops that sleep between calls compare `api.epoch()` to stop themselves.
let base = "";
let epoch = 0;

async function call(method, path, body) {
  const myEpoch = epoch;
  const url = base + path;
  const never = () => new Promise(() => {});
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch (e) {
    if (myEpoch !== epoch) return never();
    throw new Error("The Studio server is not reachable. Is it running?");
  }
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (myEpoch !== epoch) return never();
  if (!res.ok) {
    const detail = data && (data.detail || data.error);
    if (res.status === 404 && MOCK_ONLY.test(path) && (!detail || GENERIC_404.test(String(detail).trim()))) {
      const err = new Error("This needs the mock server (the real server has no code index yet).");
      err.unsupported = true;
      throw err;
    }
    const err = new Error(detail ? `${res.status}: ${detail}` : `The server answered ${res.status}.`);
    err.status = res.status;
    err.detail = detail ? String(detail) : "";
    err.code = (data && data.code) || null; // v1.8: branch_protected, tree_dirty, ...
    throw err;
  }
  return data;
}

export const api = {
  // v1.6: many projects behind one server. `name` null/"" is the default project (bare /api).
  setProject: (name) => {
    base = name ? `/p/${encodeURIComponent(name)}` : "";
    epoch += 1;
  },
  base: () => base,
  epoch: () => epoch,
  // Not project-scoped: the list of projects this server holds.
  projects: async () => {
    let res;
    try {
      res = await fetch("/api/projects", { cache: "no-store" });
    } catch (e) {
      throw new Error("The Studio server is not reachable. Is it running?");
    }
    if (!res.ok) throw new Error(`The server answered ${res.status}.`);
    const data = await res.json();
    if (!Array.isArray(data)) throw new Error("The server did not return a project list.");
    return data;
  },
  // v1.16, not project-scoped: the workspace manifest ({workspace: null, ...} when none).
  workspace: async () => {
    const res = await fetch("/api/workspace", { cache: "no-store" });
    if (!res.ok) throw new Error(`The server answered ${res.status}.`);
    return res.json();
  },
  model: () => call("GET", "/api/model"),
  ask: (question, selected, verify) => call("POST", "/api/ask", { question, selected, verify }),
  // `parent` (v1.1 #3) answers a change set's conflict or question as a follow-up: the
  // server drafts it on top of the parent's proposal and supersedes the parent.
  newChange: (request, selected, parent = null) =>
    call("POST", "/api/changes", parent ? { request, selected, parent } : { request, selected }),
  change: (id) => call("GET", `/api/changes/${encodeURIComponent(id)}`),
  accept: (id) => call("POST", `/api/changes/${encodeURIComponent(id)}/accept`, {}),
  reject: (id) => call("POST", `/api/changes/${encodeURIComponent(id)}/reject`, {}),
  // Cancels a change still drafting or checking (v1.1 #5).
  cancelChange: (id) => call("DELETE", `/api/changes/${encodeURIComponent(id)}`),
  check: () => call("POST", "/api/check", {}),

  // v1.3 (Studio 2). Rejects with err.unsupported === true when the server lacks them.
  explain: (question, selected) => call("POST", "/api/explain", { question, selected }),
  codeFiles: () => call("GET", "/api/code/files"),
  codeFile: (path) => call("GET", `/api/code/file?path=${encodeURIComponent(path)}`),
  codeLinks: (target) => call("GET", `/api/code/links?target=${encodeURIComponent(target)}`),
  flow: (endpoint, caller = "ann") =>
    call("GET", `/api/flow?endpoint=${encodeURIComponent(endpoint)}&caller=${encodeURIComponent(caller)}`),
  // v1.4: the business-logic graph. params: {focus, kinds (comma list or array), depth, limit}.
  graph: (params = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === null || v === undefined || v === "") continue;
      q.set(k, Array.isArray(v) ? v.join(",") : String(v));
    }
    const qs = q.toString();
    return call("GET", `/api/graph${qs ? `?${qs}` : ""}`);
  },
  // opts: {model, max_rounds}. Resolves to {id}.
  rebuild: (changes, opts = {}) => call("POST", "/api/rebuild", { changes, ...opts }),
  rebuildGet: (id) => call("GET", `/api/rebuild/${encodeURIComponent(id)}`),
  rebuildStop: (id) => call("DELETE", `/api/rebuild/${encodeURIComponent(id)}`),
  // v1.8: build from the spec. `areas(branch)` and `branches()` feed the Build view;
  // `buildStart` is a scoped POST that may block until the baseline has run; `rebuildList`
  // lists every record of the project (the mock has no such route: err.unsupported).
  areas: (branch = null) => call("GET", `/api/areas${branch ? `?branch=${encodeURIComponent(branch)}` : ""}`),
  branches: () => call("GET", "/api/branches"),
  // v1.12: one commit of the spec committed on the current branch onto a target branch
  syncSpec: (name) => call("POST", `/api/branches/${encodeURIComponent(name)}/sync-spec`, {}),
  buildStart: (body) => call("POST", "/api/rebuild", body),
  rebuildList: () => call("GET", "/api/rebuild"),

  // v1.9: build plans. planRevise takes {edits} or {feedback}; its answer is the new record
  // (edits) or {id, status: "planning"} (feedback).
  planCreate: (body) => call("POST", "/api/plan", body),
  planGet: (id, wait = 0) => call("GET", `/api/plan/${encodeURIComponent(id)}${wait ? `?wait=${wait}` : ""}`),
  planList: () => call("GET", "/api/plans"),
  planRevise: (id, body) => call("POST", `/api/plan/${encodeURIComponent(id)}/revise`, body),
  planApprove: (id) => call("POST", `/api/plan/${encodeURIComponent(id)}/approve`, {}),
  // v1.10: `run` also resumes an interrupted, partial, failed or stopped plan; `land` only lands
  // a level that passed its check (never an agent). body: {confirm_unsafe_branch?: bool}.
  planRun: (id, body = {}) => call("POST", `/api/plan/${encodeURIComponent(id)}/run`, body),
  planLand: (id, body = {}) => call("POST", `/api/plan/${encodeURIComponent(id)}/land`, body),
  planStop: (id) => call("DELETE", `/api/plan/${encodeURIComponent(id)}`),

  // v1.13: the connected MCP servers a chat session may read (refresh re-runs the discovery)
  mcp: (refresh = false) => call("GET", `/api/mcp${refresh ? "?refresh=1" : ""}`),

  // v1.5: the chat on a Claude Code session (session.js drives these).
  sessionCreate: (mode, change = null) => call("POST", "/api/session", change ? { mode, change } : { mode }),
  sessionMessage: (id, text, selected = []) =>
    call("POST", `/api/session/${encodeURIComponent(id)}/message`, { text, selected }),
  sessionEvents: (id, after = 0) =>
    call("GET", `/api/session/${encodeURIComponent(id)}/events?after=${Number(after) || 0}`),
  sessionStop: (id) => call("DELETE", `/api/session/${encodeURIComponent(id)}`),

  // v1.21: the improvement loop. Draft and verify read; applyProposal and discardApply are the
  // only writes (403 apply_disabled unless the server runs with --allow-apply). `wait` holds
  // the apply job's GET up to that many seconds (0 to 60).
  improve: () => call("GET", "/api/improve"),
  improveDraft: (id) => call("GET", `/api/improve/${encodeURIComponent(id)}/draft`),
  improveVerify: (id, at = "") =>
    call("GET", `/api/improve/${encodeURIComponent(id)}/verify${at ? `?at=${encodeURIComponent(at)}` : ""}`),
  improveApplies: () => call("GET", "/api/improve/applies"),
  improveApply: (id, wait = 0) =>
    call("GET", `/api/improve/${encodeURIComponent(id)}/apply${wait ? `?wait=${Number(wait)}` : ""}`),
  applyProposal: (id, draftSha256, gates = true, branch = null) =>
    call("POST", `/api/improve/${encodeURIComponent(id)}/apply`, { draft_sha256: draftSha256, gates, branch }),
  discardApply: (id) => call("POST", `/api/improve/${encodeURIComponent(id)}/discard`, {}),

  // v1.17-v1.19, read by the Contracts view: the static check, the impact preview, the notes
  // other projects left here (clear with a note) and what this project's accepts delivered.
  contracts: (consumer = "") => call("GET", `/api/contracts${consumer ? `?consumer=${encodeURIComponent(consumer)}` : ""}`),
  contractsImpact: (provider, candidate = "HEAD") =>
    call("GET", `/api/contracts/impact?provider=${encodeURIComponent(provider)}&candidate=${encodeURIComponent(candidate)}`),
  linkedQuestions: (status = "") =>
    call("GET", `/api/linked-questions${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  linkedQuestionClear: (id, verb, note = "") =>
    call("POST", `/api/linked-questions/${encodeURIComponent(id)}/${verb === "dismiss" ? "dismiss" : "resolve"}`, note ? { note } : {}),
  linkedNotifications: () => call("GET", "/api/linked-notifications"),
};

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
