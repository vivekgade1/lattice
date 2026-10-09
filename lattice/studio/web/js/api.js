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
  newChange: (request, selected) => call("POST", "/api/changes", { request, selected }),
  change: (id) => call("GET", `/api/changes/${encodeURIComponent(id)}`),
  accept: (id) => call("POST", `/api/changes/${encodeURIComponent(id)}/accept`, {}),
  reject: (id) => call("POST", `/api/changes/${encodeURIComponent(id)}/reject`, {}),
  check: () => call("POST", "/api/check", {}),
};

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
