// L1 (spec 2026-09-29 ui-a §5.4): lattice-rt.ts against Python-generated answers.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as rt from "./lattice-rt.ts";

const load = (rel: string) => JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8"));
export const GO = load("../../gogen/rt/testdata/prelude_answers.json");
export const TS = load("./testdata/ts_answers.json");
const b64 = (s: string) => new Uint8Array(Buffer.from(s, "base64"));

function jtag(v: rt.JsonVal): unknown {
  if (v === null || typeof v === "boolean") return v;
  if (typeof v === "bigint") return { int: v.toString() };
  if (typeof v === "number") return { float: rt.reprFloat(v) };
  if (Array.isArray(v)) return v.map(jtag);
  if (rt.isJsonStr(v)) return { str: v.s };
  return { obj: v.keys.map((k) => [k, jtag(v.vals.get(k)!)]) };
}

test("reprFloat", () => {
  for (const c of TS.floats) {
    const raw = Buffer.from(c.bits, "hex"); // a pooled Buffer: honour its byteOffset
    const x = new DataView(raw.buffer, raw.byteOffset, raw.byteLength).getFloat64(0);
    assert.equal(rt.reprFloat(x), c.repr, c.bits);
  }
});

test("strings: repr, strip, len", () => {
  for (const c of TS.strs) {
    assert.equal(rt.pyRepr(c.s), c.repr, c.repr);
    assert.equal(rt.pyStrip(c.s), c.strip, c.repr);
    assert.equal(rt.cpLen(c.s), c.len, c.repr);
  }
});

test("loadsJson", () => {
  for (const c of TS.json) {
    const v = rt.loadsJson(b64(c.b64));
    assert.deepEqual(v === undefined ? { error: true } : { ok: jtag(v) }, c.value, c.b64);
  }
});

test("parseQS", () => {
  for (const c of TS.qs) assert.deepEqual(Object.fromEntries(rt.parseQS(c.raw)), c.pairs, c.raw);
});
