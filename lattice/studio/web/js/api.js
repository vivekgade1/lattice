// The JSON API of lattice/studio/API.md. Every call returns parsed JSON or throws an
// Error whose message is safe to show.

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
};

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
