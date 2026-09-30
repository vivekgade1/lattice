// L3 driver: for each case, the client's local verdict and the server's real answer.
// argv: <file URL of the dir with api.ts> <baseUrl> <token>; stdin: JSON list of typed cases.
import { readFileSync } from "node:fs";

const [dir, baseUrl, token] = process.argv.slice(2);
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

const show = (v: unknown): unknown =>
  typeof v === "bigint" ? { $int: v.toString() } : Array.isArray(v) ? v.map(show)
  : v !== null && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, show(x)])) : v;

const out = [];
for (const c of JSON.parse(readFileSync(0, "utf8"))) {
  const ep = api.ENDPOINTS[c.endpoint];
  const p = Object.fromEntries(Object.entries(c.params).map(([k, t]) => [k, value(t, ep.numberInts.includes(k))]));
  const local = rt.validate(ep, p);
  const r = await rt.call(ep, p, { baseUrl, token, unchecked: true });
  out.push({ name: c.name, local, ok: r.ok, value: r.ok ? show(r.value) : undefined, error: r.ok ? undefined : r.error });
}
console.log(JSON.stringify(out));
