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

// ---- dates (CPython's date/datetime.fromisoformat, over the UTF-8 bytes) ------------------

const MIN_YEAR = 1;
const MAX_YEAR = 9999;
const EPOCH_ORD = 719163; // date(1970, 1, 1).toordinal()
const US_PER_DAY = 86_400_000_000n;
const DAYS_BEFORE_MONTH = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

const isLeap = (y: number) => y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
const daysInMonth = (y: number, m: number) => (m === 2 && isLeap(y) ? 29 : DAYS_IN_MONTH[m]);
const daysBeforeYear = (y: number) => {
  const p = y - 1;
  return p * 365 + Math.floor(p / 4) - Math.floor(p / 100) + Math.floor(p / 400);
};
const ymdToOrd = (y: number, m: number, d: number) =>
  daysBeforeYear(y) + DAYS_BEFORE_MONTH[m] + (m > 2 && isLeap(y) ? 1 : 0) + d;
const validDate = (y: number, m: number, d: number) =>
  y >= MIN_YEAR && y <= MAX_YEAR && m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth(y, m);

function ordToYMD(ord: number): [number, number, number] {
  let n = ord - 1;
  const n400 = Math.floor(n / 146097);
  n %= 146097;
  let year = n400 * 400 + 1;
  const n100 = Math.floor(n / 36524);
  n %= 36524;
  const n4 = Math.floor(n / 1461);
  n %= 1461;
  const n1 = Math.floor(n / 365);
  n %= 365;
  year += n100 * 100 + n4 * 4 + n1;
  if (n1 === 4 || n100 === 4) return [year - 1, 12, 31];
  const leap = n1 === 3 && (n4 !== 24 || n100 === 3);
  let month = (n + 50) >> 5;
  let preceding = DAYS_BEFORE_MONTH[month] + (month > 2 && leap ? 1 : 0);
  if (preceding > n) {
    month -= 1;
    preceding -= DAYS_IN_MONTH[month] + (month === 2 && leap ? 1 : 0);
  }
  return [year, month, n - preceding + 1];
}

const pad = (n: number, w: number) => String(n).padStart(w, "0");
const isDigit = (c: number) => c >= 0x30 && c <= 0x39;
const at = (b: Uint8Array, i: number) => (i >= 0 && i < b.length ? b[i] : 0);

function digitsAt(b: Uint8Array, i: number, n: number): [number, number] | null {
  let v = 0;
  for (let k = 0; k < n; k++) {
    const c = at(b, i + k);
    if (!isDigit(c)) return null;
    v = v * 10 + (c - 0x30);
  }
  return [v, i + n];
}

function isoToYMD(year: number, week: number, day: number): [number, number, number] | null {
  if (year < MIN_YEAR || year > MAX_YEAR) return null;
  if (week <= 0 || week >= 53) {
    const first = ymdToOrd(year, 1, 1) % 7;
    if (!(week === 53 && (first === 4 || (first === 3 && isLeap(year))))) return null;
  }
  if (day <= 0 || day >= 8) return null;
  const first = ymdToOrd(year, 1, 1);
  const wd = (first + 6) % 7;
  const monday = first - wd + (wd > 3 ? 7 : 0);
  return ordToYMD(monday + (week - 1) * 7 + day - 1);
}

function parseDatePart(b: Uint8Array, n: number): [number, number, number] | null {
  const y = digitsAt(b, 0, 4);
  if (y === null) return null;
  let p = y[1];
  const sep = at(b, p) === 0x2d;
  if (sep) p++;
  if (at(b, p) === 0x57) {
    const w = digitsAt(b, p + 1, 2);
    if (w === null) return null;
    p = w[1];
    let day = 1;
    if (p < n) {
      if (sep) {
        if (at(b, p) !== 0x2d) return null;
        p++;
      }
      const d = digitsAt(b, p, 1);
      if (d === null) return null;
      day = d[0];
    }
    return isoToYMD(y[0], w[0], day);
  }
  const m = digitsAt(b, p, 2);
  if (m === null) return null;
  p = m[1];
  if (sep) {
    if (at(b, p) !== 0x2d) return null;
    p++;
  }
  const d = digitsAt(b, p, 2);
  return d === null ? null : [y[0], m[0], d[0]];
}

/** date.fromisoformat(s).isoformat(), or null where Python raises. */
export function parseISODate(s: string): string | null {
  if (!s.isWellFormed()) return null;
  const b = ENCODER.encode(s);
  if (b.length !== 7 && b.length !== 8 && b.length !== 10) return null;
  const ymd = parseDatePart(b, b.length);
  if (ymd === null || !validDate(...ymd)) return null;
  return `${pad(ymd[0], 4)}-${pad(ymd[1], 2)}-${pad(ymd[2], 2)}`;
}

function isoSeparator(b: Uint8Array): number {
  const n = b.length;
  if (n === 7) return 7;
  if (at(b, 4) === 0x2d) {
    if (at(b, 5) === 0x57) {
      if (n < 8) return -1;
      if (n > 8 && at(b, 8) === 0x2d) {
        if (n === 9) return -1;
        return n > 10 && isDigit(at(b, 10)) ? 8 : 10;
      }
      return 8;
    }
    return 10;
  }
  if (at(b, 4) === 0x57) {
    let idx = 7;
    while (idx < n && isDigit(b[idx])) idx++;
    if (idx < 9) return idx;
    return idx % 2 === 0 ? 7 : 8;
  }
  return 8;
}

/** CPython's parse_hh_mm_ss_ff over b[:end]: rv 0 at the end, 1 when more follows, <0 error. */
function parseHMSF(b: Uint8Array, end: number): [number, number, number, number, number] {
  const vals = [0, 0, 0];
  let p = 0;
  let hasSep = true;
  let i = 0;
  for (; i < 3; i++) {
    const v = digitsAt(b, p, 2);
    if (v === null) return [0, 0, 0, 0, -3];
    [vals[i], p] = v;
    const c = at(b, p);
    p++;
    if (i === 0) hasSep = c === 0x3a;
    if (p >= end) return [vals[0], vals[1], vals[2], 0, c !== 0 ? 1 : 0];
    if (hasSep && c === 0x3a) {
      if (i === 2) return [0, 0, 0, 0, -4];
      continue;
    }
    if (c === 0x2e || c === 0x2c) {
      if (i < 2) return [0, 0, 0, 0, -3];
      break;
    }
    if (!hasSep) p--;
    else return [0, 0, 0, 0, -4];
  }
  const remains = end - p;
  if (remains <= 0) return [0, 0, 0, 0, -3];
  const toParse = Math.min(remains, 6);
  const f = digitsAt(b, p, toParse);
  if (f === null) return [0, 0, 0, 0, -3];
  let frac = f[0];
  for (let k = toParse; k < 6; k++) frac *= 10;
  p = f[1];
  while (isDigit(at(b, p))) p++;
  return [vals[0], vals[1], vals[2], frac, at(b, p) !== 0 ? 1 : 0];
}

/** CPython's parse_isoformat_time: [h, m, s, us, offset µs] or null. */
function parseISOTime(b: Uint8Array): [number, number, number, number, number] | null {
  let tz = b.findIndex((c) => c === 0x5a || c === 0x2b || c === 0x2d);
  if (tz < 0) tz = b.length;
  const [h, m, s, us, rv] = parseHMSF(b, tz);
  if (rv < 0) return null;
  if (tz >= b.length) return rv === 1 ? null : [h, m, s, us, 0];
  if (b[tz] === 0x5a) return tz + 1 === b.length ? [h, m, s, us, 0] : null;
  const sign = b[tz] === 0x2d ? -1 : 1;
  const rest = b.subarray(tz + 1);
  const [th, tm, ts, tus, trv] = parseHMSF(rest, rest.length);
  if (trv !== 0) return null;
  // CPython bounds only the total offset (< 24h), not each field; a sub-second part is
  // dropped when h, m and s are all zero.
  const secs = th * 3600 + tm * 60 + ts;
  if (secs >= 86400) return null;
  return [h, m, s, us, sign * (secs * 1_000_000 + (secs === 0 ? 0 : tus))];
}

function makeDateTime(y: number, mo: number, d: number, h: number, mi: number, s: number, us: number): bigint {
  return BigInt(ymdToOrd(y, mo, d) - EPOCH_ORD) * US_PER_DAY + BigInt(((h * 60 + mi) * 60 + s) * 1_000_000 + us);
}
const MIN_DT = makeDateTime(1, 1, 1, 0, 0, 0, 0);
const MAX_DT = makeDateTime(9999, 12, 31, 23, 59, 59, 999_999);

/** The prelude's parse_datetime: µs since the epoch (UTC; naive is UTC), or null. */
export function parseDateTime(s: string): bigint | null {
  // CPython's _sanitize_isoformat_str: the first lone surrogate (a single code unit, not half
  // of a pair) at code point 7, 8 or 10 is the separator and is replaced by "T"
  const cps = Array.from(s);
  for (const pos of [7, 8, 10]) {
    const c = cps[pos];
    if (c !== undefined && c.length === 1 && c >= "\ud800" && c <= "\udfff") {
      cps[pos] = "T";
      s = cps.join("");
      break;
    }
  }
  if (!s.isWellFormed() || cps.length < 7) return null;
  const b = ENCODER.encode(s);
  const sepAt = isoSeparator(b);
  if (sepAt < 0) return null;
  const ymd = parseDatePart(b, sepAt);
  if (ymd === null) return null;
  let [y, mo, d] = ymd;
  let [h, mi, sec, us, off] = [0, 0, 0, 0, 0];
  if (b.length > sepAt) {
    const lead = b[sepAt];
    const size = lead < 0x80 ? 1 : lead < 0xe0 ? 2 : lead < 0xf0 ? 3 : 4;
    const t = parseISOTime(b.subarray(sepAt + size));
    if (t === null) return null;
    [h, mi, sec, us, off] = t;
  }
  if (h === 24 && mo >= 0 && mo <= 12) {  // days_in_month(y, 0) is 0, as in CPython
    const dim = daysInMonth(y, mo);
    if (d <= dim) {
      if (mi !== 0 || sec !== 0 || us !== 0) return null;
      h = 0;
      d++;
      if (d > dim) {
        d = 1;
        mo++;
        if (mo > 12) [mo, y] = [1, y + 1];
      }
    }
  }
  if (!validDate(y, mo, d) || h > 23 || mi > 59 || sec > 59) return null;
  const t = makeDateTime(y, mo, d, h, mi, sec, us) - BigInt(off);
  return t < MIN_DT || t > MAX_DT ? null : t;
}

/** The prelude's datetime_text. */
export function dateTimeText(t: bigint): string {
  let days = t / US_PER_DAY;
  let rem = t % US_PER_DAY;
  if (rem < 0n) {
    rem += US_PER_DAY;
    days -= 1n;
  }
  const [y, mo, d] = ordToYMD(Number(days) + EPOCH_ORD);
  const us = Number(rem);
  const secs = Math.floor(us / 1_000_000);
  return (
    `${pad(y, 4)}-${pad(mo, 2)}-${pad(d, 2)}T${pad(Math.floor(secs / 3600), 2)}:` +
    `${pad(Math.floor(secs / 60) % 60, 2)}:${pad(secs % 60, 2)}.${pad(us % 1_000_000, 6)}Z`
  );
}

const UUID_TEXT = /^[0-9a-fA-F]{8}(-?)[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{12}$/;

/** The prelude's uuid_text: canonical lowercase hyphenated, or null. */
export function uuidText(s: string): string | null {
  if (!UUID_TEXT.test(s)) return null;
  const h = s.replaceAll("-", "").toLowerCase();
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// ---- parameters (the prelude's _convert, _constrain, parse_params) ------------------------

export type Scalar = "Int" | "Float" | "Str" | "Bool" | "Date" | "DateTime" | "Uuid";
export type PyValue =
  | { t: "unset" }
  | { t: "none" }
  | { t: "bool"; v: boolean }
  | { t: "int"; v: bigint }
  | { t: "float"; v: number }
  | { t: "str"; v: string }
  | { t: "date"; v: string }
  | { t: "datetime"; v: string }
  | { t: "uuid"; v: string };
/** A constraint and the 422 message the server gives when it fails. The message is
 * formatted at generation time, in Python, exactly as the server's prelude formats it (`_num`
 * for a bound, repr() for one_of values and the pattern): the client never formats a number
 * or a repr (spec 2026-09-29 ui-a §10). */
export type Check<T> = { readonly v: T; readonly msg: string };
export type ParamSpec = {
  name: string;
  source: "path" | "query" | "body";
  scalar: Scalar;
  /** A bigint for an int bound (compared exactly), a number for a float bound. */
  ge?: Check<bigint | number>;
  le?: Check<bigint | number>;
  min_len?: Check<number>;
  max_len?: Check<number>;
  one_of?: Check<readonly string[]>;
  default?: PyValue;
  optional?: boolean;
  nullable?: boolean;
  /** The pattern as JS source (tsgen/regex.py; for Email, its address pattern). */
  pattern?: Check<string>;
  /** Email: the address length limit (the prelude's EMAIL_MAX_LEN). */
  email?: Check<number>;
};
export type FieldError = { loc: string[]; msg: string };
export type Parsed = { ok: true; values: Map<string, PyValue> } | { ok: false; detail: FieldError[] };

const TYPE_NAMES: Record<Scalar, string> = {
  Int: "an integer",
  Float: "a number",
  Str: "a string",
  Bool: "a boolean",
  Date: "an ISO date (YYYY-MM-DD)",
  DateTime: "an ISO date-time",
  Uuid: "a UUID",
};
const OUT_OF_RANGE = "must be an integer between -9223372036854775808 and 9223372036854775807";
const RE_INT = /^[+-]?[0-9]+$/;
const RE_FLOAT = /^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/;
const TRUE = new Set(["true", "1", "yes", "on"]);
const FALSE = new Set(["false", "0", "no", "off"]);

type Raw = string | JsonVal;
type Problem = { problem: string };
const inRange = (n: bigint): PyValue | Problem =>
  n < INT_MIN || n > INT_MAX ? { problem: OUT_OF_RANGE } : { t: "int", v: n };

function convert(spec: ParamSpec, raw: Raw, text: boolean): PyValue | Problem {
  const bad: Problem = { problem: "must be " + TYPE_NAMES[spec.scalar] };
  let str: string | null = typeof raw === "string" ? raw : null;
  if (isJsonStr(raw)) {
    if (raw.bad && spec.scalar === "Str") return { problem: "must be valid UTF-8 text" };
    str = raw.s; // a lone surrogate stays: parseDateTime, parseISODate and uuidText decide
  }
  switch (spec.scalar) {
    case "Int": {
      if (text && str !== null) {
        const t = pyStrip(str);
        if (!RE_INT.test(t)) return bad;
        if (t.replace(/^[+-]/, "").length > 4300) return { problem: OUT_OF_RANGE };
        return inRange(BigInt(t.startsWith("+") ? t.slice(1) : t));
      }
      if (text) return bad;
      if (typeof raw === "bigint") return inRange(raw);
      if (typeof raw === "number") {
        if (!Number.isFinite(raw) || !Number.isInteger(raw)) return bad;
        return inRange(BigInt(raw));
      }
      return bad;
    }
    case "Float": {
      let v: number;
      if (text && str !== null) {
        const t = pyStrip(str);
        if (!RE_FLOAT.test(t)) return bad;
        v = Number(t);
      } else if (!text && typeof raw === "bigint") v = Number(raw);
      else if (!text && typeof raw === "number") v = raw;
      else return bad;
      return Number.isFinite(v) ? { t: "float", v } : bad;
    }
    case "Str":
      return str !== null ? { t: "str", v: str } : bad;
    case "Bool":
      if (text && str !== null) {
        const l = str.toLowerCase();
        return TRUE.has(l) ? { t: "bool", v: true } : FALSE.has(l) ? { t: "bool", v: false } : bad;
      }
      return !text && typeof raw === "boolean" ? { t: "bool", v: raw } : bad;
    case "DateTime": {
      const t = str === null ? null : parseDateTime(str);
      return t === null ? bad : { t: "datetime", v: dateTimeText(t) };
    }
    case "Uuid": {
      const u = str === null ? null : uuidText(str);
      return u === null ? bad : { t: "uuid", v: u };
    }
    case "Date": {
      const d = str === null ? null : parseISODate(str);
      return d === null ? bad : { t: "date", v: d };
    }
  }
}

const cmpBig = (a: bigint, b: bigint) => (a < b ? -1 : a > b ? 1 : 0);

/** A parameter value (int or float) compared exactly with a bound (a bigint for an int
 * bound, a number for a float one), as Python compares int and float. */
export function cmpNum(v: PyValue, bound: bigint | number): number {
  if (v.t !== "int" && v.t !== "float") return 0;
  if (v.t === "int" && typeof bound === "bigint") return cmpBig(v.v, bound);
  if (v.t === "float" && typeof bound === "number") return v.v < bound ? -1 : v.v > bound ? 1 : 0;
  // one side an integer (bigint), the other a double: exact
  const [i, x, sign] = v.t === "int" ? [v.v, bound as number, 1] : [bound as bigint, v.v, -1];
  if (Number.isNaN(x)) return 0;
  if (!Number.isFinite(x)) return (x > 0 ? -1 : 1) * sign;
  // x not integral: i < x exactly when i <= floor(x)
  const c = Number.isInteger(x) ? cmpBig(i, BigInt(x)) : i <= BigInt(Math.floor(x)) ? -1 : 1;
  return c * sign;
}

const PATTERNS = new Map<string, RegExp>();
function fullMatch(js: string, s: string): boolean {
  let re = PATTERNS.get(js);
  if (re === undefined) PATTERNS.set(js, (re = new RegExp(js, "u")));
  return re.test(s);
}

function constrain(spec: ParamSpec, value: PyValue): string | null {
  if (spec.one_of !== undefined && !(value.t === "str" && spec.one_of.v.includes(value.v)))
    return spec.one_of.msg;
  if (value.t === "int" || value.t === "float") {
    if (spec.ge !== undefined && cmpNum(value, spec.ge.v) < 0) return spec.ge.msg;
    if (spec.le !== undefined && cmpNum(value, spec.le.v) > 0) return spec.le.msg;
  }
  if (value.t === "str") {
    const n = cpLen(value.v);
    if (spec.min_len !== undefined && n < spec.min_len.v) return spec.min_len.msg;
    if (spec.max_len !== undefined && n > spec.max_len.v) return spec.max_len.msg;
    if (spec.email !== undefined && n > spec.email.v) return spec.email.msg;
    if (spec.pattern !== undefined && !fullMatch(spec.pattern.v, value.v)) return spec.pattern.msg;
  }
  return null;
}

function defaultValue(spec: ParamSpec): PyValue {
  const d = spec.default!;
  if (spec.scalar === "Uuid" && d.t === "str") {
    const u = uuidText(d.v);
    if (u !== null) return { t: "uuid", v: u };
  }
  return d;
}

/** The prelude's form_fields. */
export function formFields(body: Uint8Array, ctype: string | null): Map<string, string> {
  const media = pyStrip((ctype ?? "").split(";")[0]).toLowerCase();
  return media === "application/x-www-form-urlencoded" ? parseQS(latin1(body)) : new Map();
}

const isBlank = (b: Uint8Array) => b.every((c) => c === 0x20 || (c >= 0x09 && c <= 0x0d));

/** The prelude's parse_params: every parameter converted and validated, in order; else the
 * 422 detail listing each problem in order. */
export function parseParams(
  specs: readonly ParamSpec[],
  path: ReadonlyMap<string, string>,
  query: ReadonlyMap<string, string>,
  body: Uint8Array,
  form: boolean,
  ctype: string | null,
): Parsed {
  const detail: FieldError[] = [];
  const values = new Map<string, PyValue>();
  let data: ReadonlyMap<string, Raw> = new Map();
  if (form) data = formFields(body, ctype);
  else if (specs.some((s) => s.source === "body")) {
    let parsed: JsonVal | undefined = { kind: "obj", keys: [], vals: new Map() };
    if (!isBlank(body)) parsed = loadsJson(body);
    if (!isJsonObj(parsed)) return { ok: false, detail: [{ loc: ["body"], msg: "the body must be a JSON object" }] };
    data = parsed.vals;
  }
  for (const spec of specs) {
    const source: ReadonlyMap<string, Raw> = spec.source === "path" ? path : spec.source === "query" ? query : data;
    const loc = [spec.source, spec.name];
    const present = source.has(spec.name);
    const raw = source.get(spec.name);
    if (spec.optional && present && raw === null) {
      if (spec.nullable) values.set(spec.name, { t: "none" });
      else detail.push({ loc, msg: "must not be null" });
      continue;
    }
    if (!present || raw === null || raw === undefined) {
      if (spec.default !== undefined) values.set(spec.name, defaultValue(spec));
      else detail.push({ loc, msg: "field required" });
      continue;
    }
    const value = convert(spec, raw, spec.source !== "body" || form);
    if ("problem" in value) {
      detail.push({ loc, msg: value.problem });
      continue;
    }
    const problem = constrain(spec, value);
    if (problem !== null) {
      detail.push({ loc, msg: problem });
      continue;
    }
    values.set(spec.name, value);
  }
  return detail.length > 0 ? { ok: false, detail } : { ok: true, values };
}

// ---- responses ----------------------------------------------------------------------------

/** A response body (UTF-8 JSON, ints kept exact). */
export function decodeJson(bytes: Uint8Array): JsonVal {
  const v = loadsJson(bytes);
  if (v === undefined) throw new DecodeError("the body is not JSON");
  return v;
}

const isFieldErrors = (d: unknown): d is FieldError[] =>
  Array.isArray(d) &&
  d.every(
    (e) =>
      typeof e === "object" && e !== null && Array.isArray(e.loc) &&
      e.loc.every((x: unknown) => typeof x === "string") && typeof e.msg === "string",
  );

/** An error body in either style: {"error": code, "detail"?} or {"detail": ...}. A 422's
 * detail must be a list of {loc, msg}; any other status's a string or absent. */
export function decodeErrorBody(status: number, body: Uint8Array): { code: string | null; detail: unknown } {
  let o: unknown;
  try {
    o = JSON.parse(UTF8_REPLACE.decode(body));
  } catch {
    throw new DecodeError("the error body is not JSON");
  }
  if (typeof o !== "object" || o === null || Array.isArray(o)) throw new DecodeError("not an object");
  const rec = o as Record<string, unknown>;
  const code = typeof rec.error === "string" ? rec.error : null;
  const detail = rec.detail ?? null;
  if (status === 422 ? !isFieldErrors(detail) : !(detail === null || typeof detail === "string"))
    throw new DecodeError(`unexpected ${status} detail`);
  return { code, detail };
}

export type Col = readonly [name: string, scalar: Scalar, nullable: boolean];

function decodeScalar(v: JsonVal | undefined, scalar: Scalar, nullable: boolean, where: string): unknown {
  if (v === null) {
    if (nullable) return null;
    throw new DecodeError(`${where}: null`);
  }
  if (scalar === "Int" && typeof v === "bigint") return v;
  if (scalar === "Float" && typeof v === "number") return v;
  if (scalar === "Float" && typeof v === "bigint") return Number(v);
  if (scalar === "Bool" && typeof v === "boolean") return v;
  if ((scalar === "Str" || scalar === "Uuid" || scalar === "Date" || scalar === "DateTime") && isJsonStr(v) && !v.bad)
    return v.s;
  throw new DecodeError(`${where}: not ${scalar}`);
}

/** A row: exactly the declared columns, each of its declared type. */
export function decodeRow(v: JsonVal | undefined, cols: readonly Col[]): Record<string, unknown> {
  if (!isJsonObj(v)) throw new DecodeError("the row is not an object");
  if (v.keys.length !== cols.length || cols.some(([n]) => !v.vals.has(n)))
    throw new DecodeError(`columns ${v.keys.join(",")} are not ${cols.map((c) => c[0]).join(",")}`);
  const out: Record<string, unknown> = {};
  for (const [n, s, nullable] of cols) out[n] = decodeScalar(v.vals.get(n), s, nullable, n);
  return out;
}

/** A List: a JSON array of rows (no envelope). */
export function decodeList(v: JsonVal | undefined, cols: readonly Col[]): Record<string, unknown>[] {
  if (!Array.isArray(v)) throw new DecodeError("the body is not a list");
  return v.map((r) => decodeRow(r, cols));
}

/** How a Page envelope number decodes: "count" (`total`: 0 .. 2^53 - 1, a number), "number"
 * (a limit/offset whose declared range fits: |x| <= 2^53 - 1, a number) or "bigint" (a
 * limit/offset parameter whose range does not fit: any integer, kept exact). */
export type PageNum = "count" | "number" | "bigint";

/** A Page envelope: `shape` is [page_shape key, JSON field, how its number decodes]. */
export function decodePage(
  v: JsonVal | undefined,
  cols: readonly Col[],
  shape: readonly (readonly [string, string, PageNum?])[],
): Record<string, unknown> {
  if (!isJsonObj(v) || v.keys.length !== shape.length) throw new DecodeError("not the page envelope");
  const out: Record<string, unknown> = {};
  const safe = BigInt(Number.MAX_SAFE_INTEGER);
  for (const [key, field, mode = "count"] of shape) {
    const x = v.vals.get(field);
    if (key === "items") {
      if (!Array.isArray(x)) throw new DecodeError(`${field}: not a list`);
      out[field] = x.map((r) => decodeRow(r, cols));
    } else if (typeof x !== "bigint") {
      throw new DecodeError(`${field}: not an integer`);
    } else if (mode === "bigint") {
      out[field] = x;
    } else {
      if (x > safe || x < (mode === "count" ? 0n : -safe)) throw new DecodeError(`${field}: out of range`);
      out[field] = Number(x);
    }
  }
  return out;
}

// ---- the call path (api.ts calls these) ---------------------------------------------------

export type Result<T, E> = { ok: true; value: T } | { ok: false; error: E };
export type ValidationError = { kind: "validation"; status: 422; detail: FieldError[] };
export type Unexpected = { kind: "unexpected"; status: number; reason: "network" | "status" | "decode"; body: string };
export type CallOpts = {
  baseUrl: string;
  token?: string | (() => string | null);
  fetch?: typeof fetch;
  signal?: AbortSignal;
  /** Test-only: send without validating locally (the L3 gate compares both answers). */
  unchecked?: boolean;
};
export type Endpoint = {
  method: string;
  path: string;
  params: readonly ParamSpec[];
  form: boolean;
  success: number;
  errors: Readonly<Record<number, string>>;
  numberInts: readonly string[];
  decode: (v: JsonVal | undefined) => unknown;
};
export type Wire = {
  url: string;
  pathValues: Map<string, string>;
  query: string;
  body: Uint8Array | null;
  ctype: string | null;
};

const present = (v: unknown) => v !== undefined && v !== null;

/** The request exactly as it will be sent, and the path values the server will decode. */
export function toWire(ep: Endpoint, p: Record<string, unknown>): Wire {
  const pathValues = new Map<string, string>();
  const url = ep.path.replace(/\{([^}]+)\}/g, (_m, name: string) => {
    const text = present(p[name]) ? wireText(p[name]) : "";
    pathValues.set(name, text.toWellFormed());
    return pctEncode(text);
  });
  const query = ep.params
    .filter((s) => s.source === "query" && present(p[s.name]))
    .map((s) => pctEncode(s.name) + "=" + pctEncode(wireText(p[s.name])))
    .join("&");
  const bodySpecs = ep.params.filter((s) => s.source === "body");
  let body: Uint8Array | null = null;
  let ctype: string | null = null;
  if (bodySpecs.length > 0 && ep.form) {
    const fields = bodySpecs.filter((s) => present(p[s.name]));
    body = ENCODER.encode(fields.map((s) => pctEncode(s.name) + "=" + pctEncode(wireText(p[s.name]))).join("&"));
    ctype = "application/x-www-form-urlencoded";
  } else if (bodySpecs.length > 0) {
    const obj: Record<string, unknown> = {};
    for (const s of bodySpecs) if (p[s.name] !== undefined) obj[s.name] = p[s.name];
    body = ENCODER.encode(dumpsWire(obj));
    ctype = "application/json";
  }
  return { url: query ? `${url}?${query}` : url, pathValues, query, body, ctype };
}

function validateWire(ep: Endpoint, w: Wire): FieldError[] {
  const r = parseParams(ep.params, w.pathValues, parseQS(w.query), w.body ?? new Uint8Array(), ep.form, w.ctype);
  return r.ok ? [] : r.detail;
}

/** The server's 422 detail for these params (empty when it would accept them). */
export function validate(ep: Endpoint, p: Record<string, unknown>): FieldError[] {
  return validateWire(ep, toWire(ep, p));
}

const unexpected = (status: number, reason: Unexpected["reason"], body: Uint8Array | string): { ok: false; error: Unexpected } => ({
  ok: false,
  error: { kind: "unexpected", status, reason, body: typeof body === "string" ? body : UTF8_REPLACE.decode(body) },
});

export async function call(ep: Endpoint, p: Record<string, unknown>, o: CallOpts): Promise<Result<unknown, unknown>> {
  const w = toWire(ep, p);
  if (!o.unchecked) {
    const detail = validateWire(ep, w);
    if (detail.length > 0) return { ok: false, error: { kind: "validation", status: 422, detail } };
  }
  const headers: Record<string, string> = {};
  const token = typeof o.token === "function" ? o.token() : o.token;
  if (token !== undefined && token !== null) headers["authorization"] = "Bearer " + token;
  if (w.ctype !== null) headers["content-type"] = w.ctype;
  let res: Response;
  try {
    res = await (o.fetch ?? fetch)(o.baseUrl.replace(/\/$/, "") + w.url, {
      method: ep.method,
      headers,
      body: (w.body ?? undefined) as BodyInit | undefined, // Uint8Array<ArrayBufferLike> in TS 5.7+ libs
      signal: o.signal,
    });
  } catch (e) {
    return unexpected(0, "network", String(e));
  }
  let bytes: Uint8Array;
  try {
    bytes = new Uint8Array(await res.arrayBuffer());
  } catch (e) {
    return unexpected(res.status, "network", String(e)); // aborted or dropped mid-body
  }
  // Decoding is total: any error while decoding a body (a DecodeError, or e.g. a RangeError
  // from nesting deeper than the stack) is Unexpected "decode"; call() never rejects.
  if (res.status === ep.success) {
    try {
      return { ok: true, value: ep.decode(res.status === 204 ? undefined : decodeJson(bytes)) };
    } catch {
      return unexpected(res.status, "decode", bytes);
    }
  }
  const kind = ep.errors[res.status];
  if (kind === undefined) return unexpected(res.status, "status", bytes);
  try {
    return { ok: false, error: { kind, status: res.status, detail: decodeErrorBody(res.status, bytes).detail } };
  } catch {
    return unexpected(res.status, "decode", bytes);
  }
}

/** A stable query key for params (bigint-safe, key order independent). */
export function canonicalKey(v: unknown): string {
  if (typeof v === "bigint") return v.toString() + "n";
  if (Object.is(v, -0)) return "-0"; // sent as -0.0, not 0
  if (v === undefined) return "u";
  if (typeof v === "number" && !Number.isFinite(v)) return String(v);
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canonicalKey).join(",") + "]";
  const o = v as Record<string, unknown>;
  return (
    "{" +
    Object.keys(o)
      .filter((k) => o[k] !== undefined)
      .sort()
      .map((k) => JSON.stringify(k) + ":" + canonicalKey(o[k]))
      .join(",") +
    "}"
  );
}
