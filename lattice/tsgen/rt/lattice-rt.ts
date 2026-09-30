// lattice-rt.ts — the Lattice client runtime (spec
// docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md §4). Vendored into every
// generated client; no dependencies. A port of the server prelude's parameter parsing
// (lattice/api/codegen.py _PRELUDE), as lattice/gogen/rt/params.go is for Go.

// ---- strings ------------------------------------------------------------------------------

/** len(s): code points (a lone surrogate counts one). */
export function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

// str.isspace(): bidi WS/B/S or category Zs.
const PY_SPACE = new Set<number>([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001,
  0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
  0x205f, 0x3000,
]);

/** s.strip() */
export function pyStrip(s: string): string {
  const cps = Array.from(s);
  let i = 0;
  let j = cps.length;
  while (i < j && PY_SPACE.has(cps[i].codePointAt(0)!)) i++;
  while (j > i && PY_SPACE.has(cps[j - 1].codePointAt(0)!)) j--;
  return cps.slice(i, j).join("");
}

const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;
const hex = (prefix: string, cp: number, width: number) =>
  prefix + cp.toString(16).padStart(width, "0");

/** repr(s) */
export function pyRepr(s: string): string {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = quote;
  for (let i = 0; i < s.length; ) {
    const cp = s.codePointAt(i)!;
    i += cp > 0xffff ? 2 : 1;
    const ch = String.fromCodePoint(cp);
    if (ch === quote || ch === "\\") out += "\\" + ch;
    else if (cp === 9) out += "\\t";
    else if (cp === 10) out += "\\n";
    else if (cp === 13) out += "\\r";
    else if (cp < 0x20 || cp === 0x7f) out += hex("\\x", cp, 2);
    else if (cp < 0x7f) out += ch;
    else if (NOT_PRINTABLE.test(ch))
      out += cp <= 0xff ? hex("\\x", cp, 2) : cp <= 0xffff ? hex("\\u", cp, 4) : hex("\\U", cp, 8);
    else out += ch;
  }
  return out + quote;
}

// ---- numbers ------------------------------------------------------------------------------

export const INT_MIN = -(1n << 63n);
export const INT_MAX = (1n << 63n) - 1n;

/** repr(x) for a float: the shortest round-trip digits, Python's layout. */
export function reprFloat(x: number): string {
  if (Number.isNaN(x)) return "nan";
  if (x === Infinity) return "inf";
  if (x === -Infinity) return "-inf";
  if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
  const [mant, expText] = Math.abs(x).toExponential().split("e");
  const digits = mant.replace(".", "");
  const exp = Number(expText);
  const sign = x < 0 ? "-" : "";
  if (exp < -4 || exp >= 16) {
    const m = digits.length > 1 ? digits[0] + "." + digits.slice(1) : digits;
    const e = String(Math.abs(exp)).padStart(2, "0");
    return `${sign}${m}e${exp < 0 ? "-" : "+"}${e}`;
  }
  if (exp < 0) return sign + "0." + "0".repeat(-exp - 1) + digits;
  if (digits.length <= exp + 1) return sign + digits + "0".repeat(exp + 1 - digits.length) + ".0";
  return sign + digits.slice(0, exp + 1) + "." + digits.slice(exp + 1);
}

/** The prelude's _num: a bound as Python prints it. */
export function num(v: number): string {
  return Number.isInteger(v) ? BigInt(v).toString() : reprFloat(v);
}

// ---- JSON (json.loads of request bytes) ---------------------------------------------------

export type JsonStr = { kind: "str"; s: string; bad: boolean };
export type JsonObj = { kind: "obj"; keys: string[]; vals: Map<string, JsonVal> };
export type JsonVal = null | boolean | bigint | number | JsonStr | JsonVal[] | JsonObj;
export const isJsonStr = (v: unknown): v is JsonStr =>
  typeof v === "object" && v !== null && (v as JsonStr).kind === "str";
export const isJsonObj = (v: unknown): v is JsonObj =>
  typeof v === "object" && v !== null && (v as JsonObj).kind === "obj";

export class DecodeError extends Error {}

function fromCodePoints(cps: readonly number[]): string {
  let out = "";
  for (const cp of cps) out += String.fromCodePoint(cp);
  return out;
}

function decodeUtf8Pass(b: Uint8Array, start: number): number[] | null {
  const out: number[] = [];
  for (let i = start; i < b.length; ) {
    const c = b[i];
    if (c < 0x80) {
      out.push(c);
      i++;
      continue;
    }
    let need = 0;
    let cp = 0;
    let lo = 0x80;
    let hi = 0xbf;
    if (c >= 0xc2 && c <= 0xdf) [need, cp] = [1, c & 0x1f];
    else if (c >= 0xe0 && c <= 0xef) {
      [need, cp] = [2, c & 0x0f];
      if (c === 0xe0) lo = 0xa0; // "surrogatepass": ED A0..BF is a lone surrogate
    } else if (c >= 0xf0 && c <= 0xf4) {
      [need, cp] = [3, c & 0x07];
      if (c === 0xf0) lo = 0x90;
      if (c === 0xf4) hi = 0x8f;
    } else return null;
    if (i + need >= b.length) return null;
    for (let k = 1; k <= need; k++) {
      const d = b[i + k];
      if (d < (k === 1 ? lo : 0x80) || d > (k === 1 ? hi : 0xbf)) return null;
      cp = (cp << 6) | (d & 0x3f);
    }
    out.push(cp);
    i += need + 1;
  }
  return out;
}

function decodeUtf16(b: Uint8Array, start: number, be: boolean): number[] | null {
  if ((b.length - start) % 2 !== 0) return null;
  const units: number[] = [];
  for (let i = start; i < b.length; i += 2) units.push(be ? (b[i] << 8) | b[i + 1] : (b[i + 1] << 8) | b[i]);
  const out: number[] = [];
  for (let i = 0; i < units.length; i++) {
    const u = units[i];
    const lo = units[i + 1];
    if (u >= 0xd800 && u < 0xdc00 && lo !== undefined && lo >= 0xdc00 && lo < 0xe000) {
      out.push(0x10000 + ((u - 0xd800) << 10) + (lo - 0xdc00));
      i++;
    } else out.push(u);
  }
  return out;
}

function decodeUtf32(b: Uint8Array, start: number, be: boolean): number[] | null {
  if ((b.length - start) % 4 !== 0) return null;
  const out: number[] = [];
  for (let i = start; i < b.length; i += 4) {
    const u = be
      ? ((b[i] << 24) >>> 0) + (b[i + 1] << 16) + (b[i + 2] << 8) + b[i + 3]
      : ((b[i + 3] << 24) >>> 0) + (b[i + 2] << 16) + (b[i + 1] << 8) + b[i];
    if (u > 0x10ffff) return null;
    out.push(u);
  }
  return out;
}

/** json.detect_encoding, then decode with "surrogatepass". */
function decodeJsonBytes(b: Uint8Array): number[] | null {
  const has = (...p: number[]) => b.length >= p.length && p.every((x, i) => b[i] === x);
  if (has(0, 0, 0xfe, 0xff)) return decodeUtf32(b, 4, true);
  if (has(0xff, 0xfe, 0, 0)) return decodeUtf32(b, 4, false);
  if (has(0xfe, 0xff)) return decodeUtf16(b, 2, true);
  if (has(0xff, 0xfe)) return decodeUtf16(b, 2, false);
  if (has(0xef, 0xbb, 0xbf)) return decodeUtf8Pass(b, 3);
  if (b.length >= 4) {
    if (b[0] === 0) return b[1] !== 0 ? decodeUtf16(b, 0, true) : decodeUtf32(b, 0, true);
    if (b[1] === 0) return b[2] !== 0 || b[3] !== 0 ? decodeUtf16(b, 0, false) : decodeUtf32(b, 0, false);
  } else if (b.length === 2) {
    if (b[0] === 0) return decodeUtf16(b, 0, true);
    if (b[1] === 0) return decodeUtf16(b, 0, false);
  }
  return decodeUtf8Pass(b, 0);
}

class JsonParser {
  i = 0;
  s: number[];
  constructor(s: number[]) {
    this.s = s;
  }
  ws(): void {
    while (this.i < this.s.length && [0x20, 0x09, 0x0a, 0x0d].includes(this.s[this.i])) this.i++;
  }
  lit(word: string): boolean {
    for (let k = 0; k < word.length; k++) if (this.s[this.i + k] !== word.charCodeAt(k)) return false;
    this.i += word.length;
    return true;
  }
  value(): JsonVal | undefined {
    const c = this.s[this.i];
    if (c === undefined) return undefined;
    if (c === 0x7b) return this.object();
    if (c === 0x5b) return this.array();
    if (c === 0x22) {
      this.i++;
      return this.str();
    }
    if (c === 0x6e && this.lit("null")) return null;
    if (c === 0x74 && this.lit("true")) return true;
    if (c === 0x66 && this.lit("false")) return false;
    if (c === 0x4e && this.lit("NaN")) return NaN;
    if (c === 0x49 && this.lit("Infinity")) return Infinity;
    if (c === 0x2d && this.s[this.i + 1] === 0x49) {
      this.i++;
      return this.lit("Infinity") ? -Infinity : undefined;
    }
    if (c === 0x2d || (c >= 0x30 && c <= 0x39)) return this.number();
    return undefined;
  }
  digits(): number {
    const start = this.i;
    while (this.i < this.s.length && this.s[this.i] >= 0x30 && this.s[this.i] <= 0x39) this.i++;
    return this.i - start;
  }
  number(): JsonVal | undefined {
    const start = this.i;
    if (this.s[this.i] === 0x2d) this.i++;
    if (this.i >= this.s.length) return undefined;
    if (this.s[this.i] === 0x30) this.i++;
    else if (this.digits() === 0) return undefined;
    let isFloat = false;
    const d = this.s[this.i + 1];
    if (this.s[this.i] === 0x2e && d !== undefined && d >= 0x30 && d <= 0x39) {
      this.i++;
      this.digits();
      isFloat = true;
    }
    if (this.s[this.i] === 0x65 || this.s[this.i] === 0x45) {
      const save = this.i;
      this.i++;
      if (this.s[this.i] === 0x2b || this.s[this.i] === 0x2d) this.i++;
      if (this.digits() === 0) this.i = save;
      else isFloat = true;
    }
    const text = fromCodePoints(this.s.slice(start, this.i));
    if (isFloat) return Number(text);
    if (text.replace("-", "").length > 4300) return undefined; // CPython's int digit limit
    return BigInt(text);
  }
  hex4(): number | undefined {
    if (this.i + 4 > this.s.length) return undefined;
    let u = 0;
    for (let k = 0; k < 4; k++) {
      const c = this.s[this.i + k];
      const d = c >= 0x30 && c <= 0x39 ? c - 0x30 : c >= 0x61 && c <= 0x66 ? c - 87 : c >= 0x41 && c <= 0x46 ? c - 55 : -1;
      if (d < 0) return undefined;
      u = (u << 4) | d;
    }
    this.i += 4;
    return u;
  }
  str(): JsonStr | undefined {
    const out: number[] = [];
    let bad = false;
    const ESC: Record<number, number> = { 0x22: 0x22, 0x5c: 0x5c, 0x2f: 0x2f, 0x62: 8, 0x66: 12, 0x6e: 10, 0x72: 13, 0x74: 9 };
    for (;;) {
      if (this.i >= this.s.length) return undefined;
      const c = this.s[this.i++];
      if (c === 0x22) return { kind: "str", s: fromCodePoints(out), bad };
      if (c < 0x20) return undefined; // strict: an unescaped control character
      if (c !== 0x5c) {
        if (c >= 0xd800 && c < 0xe000) bad = true;
        out.push(c);
        continue;
      }
      const e = this.s[this.i++];
      if (e === undefined) return undefined;
      if (e in ESC) {
        out.push(ESC[e]);
        continue;
      }
      if (e !== 0x75) return undefined;
      const u = this.hex4();
      if (u === undefined) return undefined;
      if (u >= 0xd800 && u < 0xdc00 && this.s[this.i] === 0x5c && this.s[this.i + 1] === 0x75) {
        const save = this.i;
        this.i += 2;
        const lo = this.hex4();
        if (lo !== undefined && lo >= 0xdc00 && lo < 0xe000) {
          out.push(0x10000 + ((u - 0xd800) << 10) + (lo - 0xdc00));
          continue;
        }
        this.i = save;
      }
      if (u >= 0xd800 && u < 0xe000) bad = true;
      out.push(u);
    }
  }
  array(): JsonVal[] | undefined {
    this.i++;
    const out: JsonVal[] = [];
    this.ws();
    if (this.s[this.i] === 0x5d) {
      this.i++;
      return out;
    }
    for (;;) {
      this.ws();
      const v = this.value();
      if (v === undefined) return undefined;
      out.push(v);
      this.ws();
      const c = this.s[this.i++];
      if (c === 0x5d) return out;
      if (c !== 0x2c) return undefined;
    }
  }
  object(): JsonObj | undefined {
    this.i++;
    const obj: JsonObj = { kind: "obj", keys: [], vals: new Map() };
    this.ws();
    if (this.s[this.i] === 0x7d) {
      this.i++;
      return obj;
    }
    for (;;) {
      this.ws();
      if (this.s[this.i] !== 0x22) return undefined;
      this.i++;
      const k = this.str();
      if (k === undefined) return undefined;
      this.ws();
      if (this.s[this.i] !== 0x3a) return undefined;
      this.i++;
      this.ws();
      const v = this.value();
      if (v === undefined) return undefined;
      if (!obj.vals.has(k.s)) obj.keys.push(k.s);
      obj.vals.set(k.s, v);
      this.ws();
      const c = this.s[this.i++];
      if (c === 0x7d) return obj;
      if (c !== 0x2c) return undefined;
    }
  }
}

/** json.loads(bytes); undefined where Python raises ValueError. Ints are bigint. */
export function loadsJson(b: Uint8Array): JsonVal | undefined {
  const text = decodeJsonBytes(b);
  if (text === null) return undefined;
  const p = new JsonParser(text);
  p.ws();
  const v = p.value();
  if (v === undefined) return undefined;
  p.ws();
  return p.i === text.length ? v : undefined;
}

// ---- query strings (dict(parse_qsl(raw, keep_blank_values=True)), as Starlette) -----------

const UTF8_REPLACE = new TextDecoder("utf-8", { fatal: false, ignoreBOM: true });

function unquoteToBytes(s: string): Uint8Array {
  const parts = s.split("%");
  const out: number[] = [];
  const push = (t: string) => {
    for (let k = 0; k < t.length; k++) out.push(t.charCodeAt(k));
  };
  push(parts[0]);
  for (const item of parts.slice(1)) {
    if (/^[0-9A-Fa-f]{2}/.test(item)) {
      out.push(parseInt(item.slice(0, 2), 16));
      push(item.slice(2));
    } else {
      out.push(0x25);
      push(item);
    }
  }
  return new Uint8Array(out);
}

/** urllib.parse.unquote(s): ASCII runs percent-decoded as UTF-8 with replacement. */
export function unquote(s: string): string {
  if (!s.includes("%")) return s;
  let out = "";
  for (let i = 0; i < s.length; ) {
    let j = i;
    if (s.charCodeAt(i) < 0x80) {
      while (j < s.length && s.charCodeAt(j) < 0x80) j++;
      out += UTF8_REPLACE.decode(unquoteToBytes(s.slice(i, j)));
    } else {
      while (j < s.length && s.charCodeAt(j) >= 0x80) j++;
      out += s.slice(i, j);
    }
    i = j;
  }
  return out;
}

/** `raw` is the query as latin-1 text (one char per byte). A repeated name keeps its last value. */
export function parseQS(raw: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const field of raw.split("&")) {
    if (field === "") continue;
    const eq = field.indexOf("=");
    const name = eq < 0 ? field : field.slice(0, eq);
    const value = eq < 0 ? "" : field.slice(eq + 1);
    out.set(unquote(name.replaceAll("+", " ")), unquote(value.replaceAll("+", " ")));
  }
  return out;
}

/** bytes.decode("latin-1") */
export function latin1(b: Uint8Array): string {
  let out = "";
  for (const x of b) out += String.fromCharCode(x);
  return out;
}

// ---- the wire (what the client sends) -----------------------------------------------------

function wireNumber(x: number): string {
  if (Number.isNaN(x)) return "NaN";
  if (x === Infinity) return "Infinity";
  if (x === -Infinity) return "-Infinity";
  if (Object.is(x, -0)) return "-0.0";
  return Number.isSafeInteger(x) ? String(x) : reprFloat(x);
}

function wireString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += "\\\\";
    else if (c >= 0x20 && c < 0x7f) out += s[i];
    else out += "\\u" + c.toString(16).padStart(4, "0"); // keeps lone surrogates
  }
  return out + '"';
}

/** ASCII-only JSON the server reads back exactly: bigint as an integer, Python's NaN and
 * Infinity tokens, undefined members left out. */
export function dumpsWire(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "number") return wireNumber(v);
  if (typeof v === "string") return wireString(v);
  if (Array.isArray(v)) return "[" + v.map(dumpsWire).join(", ") + "]";
  const o = v as Record<string, unknown>;
  const members = Object.keys(o).filter((k) => o[k] !== undefined);
  return "{" + members.map((k) => wireString(k) + ": " + dumpsWire(o[k])).join(", ") + "}";
}

/** A value as path, query or form text. */
export function wireText(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "number")
    return Number.isInteger(v) && !Object.is(v, -0) ? BigInt(v).toString() : reprFloat(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

const ENCODER = new TextEncoder();

/** Percent-encode the UTF-8 bytes (a lone surrogate becomes U+FFFD, as TextEncoder does). */
export function pctEncode(s: string): string {
  let out = "";
  for (const b of ENCODER.encode(s)) {
    const c = String.fromCharCode(b);
    out += /[A-Za-z0-9\-_.~]/.test(c) ? c : "%" + b.toString(16).toUpperCase().padStart(2, "0");
  }
  return out;
}
