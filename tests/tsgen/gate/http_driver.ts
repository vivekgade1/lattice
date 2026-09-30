// L3 driver: for each case, the client's local verdict and the server's real answer.
// argv: <file URL of the dir with api.ts> <baseUrl> <token> <reset path>; stdin: JSON list of
// typed cases. Before each endpoint's first case the server's database is reset to the seed.
// A success also records the raw body (base64), for the lossless check (claim 3).
import { readFileSync } from "node:fs";

const [dir, baseUrl, token, resetPath] = process.argv.slice(2);
const api = await import(dir + "/api.ts");
const rt = await import(dir + "/lattice-rt.ts");

function value(t: any, asNumber: boolean): unknown {
  switch (t.t) {
    case "none": return null;
    case "int": return asNumber ? Number(t.v) : BigInt(t.v);
    case "float": return t.v === "nan" ? NaN : t.v === "inf" ? Infinity : t.v === "-inf" ? -Infinity : Number(t.v);
    default: return t.v; // bool, str, json
  }
}

// bigint and number stay distinguishable (and exact) in the JSON handed back to Python
const show = (v: unknown): unknown =>
  typeof v === "bigint" ? { $int: v.toString() } : typeof v === "number" ? { $f: String(v) }
  : Array.isArray(v) ? v.map(show)
  : v !== null && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, show(x)])) : v;

let raw: Uint8Array = new Uint8Array();
const recording: typeof fetch = async (input, init) => {
  const res = await fetch(input, init);
  raw = new Uint8Array(await res.clone().arrayBuffer());
  return res;
};

const out = [];
let current: string | null = null;
for (const c of JSON.parse(readFileSync(0, "utf8"))) {
  if (c.endpoint !== current) {
    const res = await fetch(baseUrl + resetPath, { method: "POST" });
    if (res.status !== 204) throw new Error(`reset failed: ${res.status}`);
    current = c.endpoint;
  }
  const ep = api.ENDPOINTS[c.endpoint];
  const p = Object.fromEntries(Object.entries(c.params).map(([k, t]) => [k, value(t, ep.numberInts.includes(k))]));
  const local = rt.validate(ep, p);
  raw = new Uint8Array();
  const r = await rt.call(ep, p, { baseUrl, token, unchecked: true, fetch: recording });
  out.push({
    name: c.name, local, ok: r.ok,
    value: r.ok ? show(r.value) : undefined,
    raw: r.ok ? Buffer.from(raw).toString("base64") : undefined,
    error: r.ok ? undefined : r.error,
  });
}
console.log(JSON.stringify(out));
