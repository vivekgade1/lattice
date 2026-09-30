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

export function specOf(d: any, patterns: Record<string, string>): ParamSpec {
  const s: any = { ...d };
  if (d.default !== undefined) s.default = pyValueOf(d.default);
  if (d.pattern !== undefined) {
    const js = patterns[d.pattern];
    if (js === undefined) throw new Error("no JS source for pattern " + d.pattern);
    s.pattern = { py: d.pattern, js };
  }
  return s;
}

const show = (v: unknown) => JSON.stringify(v, (_k, x) => (typeof x === "bigint" ? `${x}n` : x));

export function replayParams(cases: any[], patterns: Record<string, string>): string[] {
  const bad: string[] = [];
  for (const c of cases) {
    const specs = c.specs.map((d: any) => specOf(d, patterns));
    const body = new Uint8Array(Buffer.from(c.body, "base64"));
    const r = parseParams(specs, new Map(Object.entries(c.path)), parseQS(c.query), body, c.form, c.ctype);
    const got = r.ok
      ? { values: Object.fromEntries([...r.values].map(([k, v]) => [k, tagOf(v)])), order: [...r.values.keys()] }
      : { status: 422, detail: r.detail };
    const want = "status" in c
      ? { status: c.status, detail: JSON.parse(c.lattice).detail }
      : { values: c.values, order: c.order };
    if (!isDeepStrictEqual(got, want)) bad.push(`${c.name}: got ${show(got)} want ${show(want)}`);
  }
  return bad;
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop()!)) {
  const file = process.argv[2];
  if (file) {
    const corpus = JSON.parse(readFileSync(file, "utf8"));
    const mismatches = replayParams(corpus.params, corpus.patterns);
    console.log(JSON.stringify({ cases: corpus.params.length, mismatches }));
  }
}
