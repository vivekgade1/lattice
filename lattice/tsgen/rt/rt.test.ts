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

test("dates", () => {
  for (const c of GO.dates) assert.equal(rt.parseISODate(c.in), c.out, JSON.stringify(c.in));
});

test("datetimes", () => {
  for (const c of GO.datetimes) {
    const t = rt.parseDateTime(c.in);
    assert.equal(t === null ? null : rt.dateTimeText(t), c.out, JSON.stringify(c.in));
  }
});

test("uuids", () => {
  for (const c of GO.uuids) assert.equal(rt.uuidText(c.in), c.out, JSON.stringify(c.in));
});

test("lone surrogates: only the first at code point 7, 8 or 10 is the date-time separator (Review Focus 3)", () => {
  const ok = (s: string) => { const t = rt.parseDateTime(s); return t === null ? null : rt.dateTimeText(t); };
  assert.equal(ok("2026-09-28\ud80010:00"), "2026-09-28T10:00:00.000000Z");
  assert.equal(ok("2026-09-28\udc0010:00"), "2026-09-28T10:00:00.000000Z");
  assert.equal(ok("2026-09-2\ud800T10:00"), null);
  assert.equal(ok("2026-09-28T1\ud8000:00"), null);
  assert.equal(ok("2026-09-28\ud800"), null);
  assert.equal(ok("20260928\ud80010:00"), "2026-09-28T10:00:00.000000Z");
  assert.equal(ok("2026W01\ud80010:00"), "2025-12-29T10:00:00.000000Z");
  assert.equal(ok("2026-W01\ud80010"), "2025-12-29T00:00:00.000000Z".replace("T00:00", "T10:00"));
  assert.equal(ok("2026-09\ud80010"), null);
  assert.equal(ok("2026W01\ud800\ud80010:00"), null);
  assert.equal(ok("2026-09-28\u{1F600}10:00"), "2026-09-28T10:00:00.000000Z"); // a pair is a character, not a lone surrogate
  assert.equal(rt.parseISODate("2026-09-2\ud800"), null);
  assert.equal(rt.uuidText("0123456789abcdef0123456789abcde\ud800"), null);
});

import { replayParams } from "./replay.ts";

test("params: Go corpus", () => {
  const bad = replayParams(GO.params, TS.patterns);
  assert.deepEqual(bad, [], bad.slice(0, 10).join("\n"));
});

test("params: JS-hazard corpus", () => {
  const bad = replayParams(TS.params, TS.patterns);
  assert.deepEqual(bad, [], bad.slice(0, 10).join("\n"));
});

test("error bodies", () => {
  for (const c of GO.error_bodies) {
    const body = new TextEncoder().encode(c.body);
    const wantDetail = c.detail ?? (c.style === "fastapi" ? GO.phrases[String(c.status)] : null);
    if (c.status === 422 && !Array.isArray(wantDetail)) {
      assert.throws(() => rt.decodeErrorBody(c.status, body), rt.DecodeError);
      continue;
    }
    const got = rt.decodeErrorBody(c.status, body);
    assert.deepEqual(got, { code: c.style === "lattice" ? c.code : null, detail: wantDetail }, c.body);
  }
});
