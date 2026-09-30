// Replay fixture-format wire cases through parseParams (L1 and the L2 gate).
import { isDeepStrictEqual } from "node:util";
import { readFileSync } from "node:fs";
import { dateTimeText, parseDateTime, parseParams, parseQS, reprFloat, type ParamSpec, type PyValue } from "./lattice-rt.ts";

const pyFloat = (s: string) => (s === "inf" ? Infinity : s === "-inf" ? -Infinity : s === "nan" ? NaN : Number(s));

export function pyValueOf(d: any): PyValue {
  if (d.t === "int") return { t: "int", v: BigInt(d.v) };
  if (d.t === "float") return { t: "float", v: pyFloat(d.v) };
  if (d.t === "datetime") {
    // the fixture spells a default as Python's isoformat; the runtime holds datetime_text
    const t = parseDateTime(d.v);
    return t === null ? d : { t: "datetime", v: dateTimeText(t) };
  }
  return d;
}

export function tagOf(v: PyValue): Record<string, unknown> {
  if (v.t === "int") return { t: "int", v: v.v.toString() };
  if (v.t === "float") return { t: "float", v: reprFloat(v.v) };
  return v;
}

/** A fixture-format spec dict as a runtime ParamSpec. The dict's `ts` (specs.ts_checks,
 * written by Python; for the Go corpus given separately) holds each constraint's message and
 * each bound exactly (an int bound as a bigint): never recomputed here. */
export function specOf(d: any, patterns: Record<string, string>, checks?: any): ParamSpec {
  const ts = checks ?? d.ts ?? {};
  const s: any = { name: d.name, source: d.source, scalar: d.scalar };
  const check = (key: string, v: unknown) => {
    if (ts[key] === undefined) throw new Error(`no message for ${key} of ${d.name}`);
    s[key] = { v, msg: ts[key].msg };
  };
  for (const key of ["ge", "le"]) if (d[key] !== undefined) check(key, ts[key] && (pyValueOf(ts[key].v) as any).v);
  for (const key of ["min_len", "max_len", "one_of"]) if (d[key] !== undefined) check(key, d[key]);
  if (d.email) check("email", ts.email?.v);
  if (d.pattern !== undefined) {
    const js = patterns[d.pattern];
    if (js === undefined) throw new Error("no JS source for pattern " + d.pattern);
    check("pattern", js);
  }
  if (d.default !== undefined) s.default = pyValueOf(d.default);
  for (const key of ["optional", "nullable"]) if (d[key]) s[key] = true;
  return s;
}

const show = (v: unknown) => JSON.stringify(v, (_k, x) => (typeof x === "bigint" ? `${x}n` : x));

// A case carries its `specs`, or names an `endpoint` of `bySpec` (the gate's corpus shape);
// `form` and `ctype` may be left out (false, null); a rejected case gives its `detail`, or
// the `lattice` error body it is read from.
// `checks[i]` (optional) gives case i's specs their `ts` (the Go corpus has none of its own).
export function replayParams(
  cases: any[],
  patterns: Record<string, string>,
  bySpec: Record<string, any[]> = {},
  checks?: any[][],
): string[] {
  const bad: string[] = [];
  const cache = new Map<string, ParamSpec[]>();
  let index = -1;
  const specsOf = (c: any): ParamSpec[] => {
    if (c.specs !== undefined) return c.specs.map((d: any, k: number) => specOf(d, patterns, checks?.[index][k]));
    let s = cache.get(c.endpoint);
    if (s === undefined) {
      const raw = bySpec[c.endpoint];
      if (raw === undefined) throw new Error("no specs for endpoint " + c.endpoint);
      s = raw.map((d: any) => specOf(d, patterns));
      cache.set(c.endpoint, s);
    }
    return s;
  };
  if (checks !== undefined && checks.length !== cases.length) throw new Error("checks do not match the cases");
  for (const c of cases) {
    index++;
    const specs = specsOf(c);
    const body = new Uint8Array(Buffer.from(c.body, "base64"));
    const r = parseParams(specs, new Map(Object.entries(c.path)), parseQS(c.query), body, c.form ?? false, c.ctype ?? null);
    const got = r.ok
      ? { values: Object.fromEntries([...r.values].map(([k, v]) => [k, tagOf(v)])), order: [...r.values.keys()] }
      : { status: 422, detail: r.detail };
    const want = "status" in c
      ? { status: c.status, detail: c.detail !== undefined ? c.detail : JSON.parse(c.lattice).detail }
      : { values: c.values, order: c.order };
    if (!isDeepStrictEqual(got, want)) bad.push(`${c.name}: got ${show(got)} want ${show(want)}`);
  }
  return bad;
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop()!)) {
  const file = process.argv[2];
  if (file) {
    const corpus = JSON.parse(readFileSync(file, "utf8"));
    const mismatches = replayParams(corpus.params, corpus.patterns, corpus.specs ?? {});
    console.log(JSON.stringify({ cases: corpus.params.length, mismatches }));
  }
}
