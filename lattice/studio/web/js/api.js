// The JSON API of lattice/studio/API.md. Every call returns parsed JSON or throws an
// Error whose message is safe to show.

// Studio 2 endpoints that only the mock server has. A 404 whose body is the router's
// generic "no such route" (or has no body) on one of these means "unsupported here"; a 404
// that names a missing file, change or rebuild is an ordinary error.
const MOCK_ONLY = /^\/api\/(code\/|flow|graph|rebuild|explain)/;
const GENERIC_404 = /^(no such route|not found)$/i;

async function call(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch (e) {
    throw new Error("The Studio server is not reachable. Is it running?");
  }
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.error);
    if (res.status === 404 && MOCK_ONLY.test(path) && (!detail || GENERIC_404.test(String(detail).trim()))) {
      const err = new Error("This needs the mock server (the real server has no code index yet).");
      err.unsupported = true;
      throw err;
    }
    throw new Error(detail ? `${res.status}: ${detail}` : `The server answered ${res.status}.`);
  }
  return data;
}

export const api = {
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
};

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
