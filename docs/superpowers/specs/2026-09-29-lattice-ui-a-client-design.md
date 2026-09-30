# Lattice UI, option A — a proven TypeScript client for API-profile apps (design, for review)

> Status: DRAFT for review · 2026-09-29 · **Parked after approval:** implementation starts only after the option 1 experiment reports (memory `project-state`, `docs/option1-handoff.md`) and only if the project continues. On resume, rebase `ui-target` onto `go-target` first. Context: `docs/handoffs/2026-09-28-lattice-ui-react-handoff.md` (options A/B/C), `docs/lattice-language-rules.md` (binding), `docs/superpowers/specs/2026-09-28-lattice-go-target-design.md` (the multi-target pattern this follows).

## 1. Goal

From a checked Lattice API-profile program, generate a TypeScript client and React hooks whose request validation is **proven identical** to the server's by a differential gate.

The one thing A offers that OpenAPI generators (openapi-typescript, orval, zod) do not: a tested guarantee that the client accepts exactly the requests the server accepts, with the same converted values and the same 422 detail, and that every error the endpoint declares is a typed case the UI must handle.

Decisions taken in brainstorming (2026-09-29):

| Question | Decision |
|---|---|
| Purpose | A proven client/server match; a typed client alone is not the goal |
| Proof apps | The imported FastAPI template API plus the API programs in the codegen differential registry; the template's React UI is not vendored |
| Output | Typed client, request validators, typed error unions, TanStack Query hooks (hooks outside the proof) |
| Strictness | Identical 422 detail: same accept/reject, converted values, `loc`, `msg`, order |
| `Int` | `number` when the declared bounds fit in ±2^53, else `bigint`; responses parsed losslessly |
| Dependencies | Zero-dependency core (vendored `lattice-rt.ts`); `react` and `@tanstack/react-query` are peer dependencies of `hooks.ts` only |
| Approach | Emit TypeScript directly from the lowered API plan (not via OpenAPI, not via a JS T2 target) |

Non-goals: running T2 procedures in the browser (option B); a UI language (option C); any claim that models build better UIs (§8).

**Why park.** The pre-registered confirmation run refuted Lattice's central editing claim (memory `project-state`). A's guarantee does not rest on that claim, but A is only useful if people build apps in Lattice, and R8 says evidence decides what is added. The design is recorded now; the build waits.

## 2. Architecture

### 2.1 Validate the wire, not the object

The client never checks the typed TS object with separately written rules. It first serializes the request to what it will send (path segments, query text, JSON or form body), then runs `parseParams`, a port of the server's `parse_params` (`lattice/api/codegen.py`, prelude), on exactly that wire request. Client and server run the same algorithm on the same input. TS types sit on top and catch wrong shapes at compile time; the runtime check carries the guarantee.

### 2.2 Scope of the client-side check

Only request validation (the 422 path) runs on the client. Authentication (401/403), `NotFound`/`Forbidden`/`Conflict`/`BadRequest` checks, `require` over rows, `T2CheckStep` and `CallStep` all need the database: they are **typed, not predicted**. The client must handle them; it cannot know them in advance.

### 2.3 Module layout

```
lattice/tsgen/
  __init__.py      generate_ts(plans) -> dict[str, str]  (filename -> source)
  emit.py          EndpointPlan + check/api_info.ParamSpec -> TS source
  regex.py         RE2-subset pattern -> JS regex source, from the check/re2.py parse tree
  rt/
    lattice-rt.ts  vendored, zero-dependency runtime (§4)
    rt.test.ts     node:test over testdata/param_answers.json
    driver.ts      JSONL wire requests in -> verdicts out (the L2 gate)
    testdata/
      gen_param_answers.py   Python-generated answers (extends Go's gen_prelude_answers.py)
      param_answers.json
```

`emit.py` reads only the lowered plan, the same inputs as `generate_fastapi`. It does not read the source text, the T2 IR, or anything under `lattice/gogen/` (fixtures are copied, not imported). `api.ts` depends only on `lattice-rt.ts`; the runtime depends on nothing.

### 2.4 Generated output per app

`lattice codegen --target ts <app>` writes, next to the FastAPI output:

| File | Contents | In the proof |
|---|---|---|
| `lattice-rt.ts` | the vendored runtime | yes |
| `api.ts` | per endpoint: params type, `PARAMS` spec table, response type, error union, `validateX`, and the call function | yes |
| `hooks.ts` | `useX` hooks over `api.ts` | type-check only |

## 3. The per-endpoint API surface

Example: `update_item` (PUT, auth `User`, errors `NotFound`, `Forbidden`).

```ts
export type UpdateItemParams = {
  item_id: string;                 // Uuid, path
  title?: string | null;           // Str = UNSET, nullable: absent = not sent, null = set NULL
  priority?: number;               // Int ge=0 le=5 -> number
};
export type UpdateItemResponse = { id: string; title: string | null; owner_id: bigint };
export type UpdateItemError =
  | { kind: "validation"; status: 422; detail: FieldError[] }   // local or from the server
  | { kind: "unauthorized"; status: 401; detail: string | null }
  | { kind: "forbidden"; status: 403; detail: string | null }
  | { kind: "not_found"; status: 404; detail: string }
  | Unexpected;
export function validateUpdateItem(p: UpdateItemParams): FieldError[];
export function updateItem(p: UpdateItemParams, o: CallOpts): Promise<Result<UpdateItemResponse, UpdateItemError>>;
```

- **Result, not exceptions.** `Result<T, E> = {ok: true; value: T} | {ok: false; error: E}`. The error union is closed: the plan's `errors`, plus 401 (and 403 when the role is restricted) for authenticated endpoints, plus 422. An exhaustive `switch` with a `never` default makes a UI that ignores a declared error fail to compile. `Unexpected` is one variant: network failure, 5xx (the server's `Failure`), or a status the plan does not declare.
- **Error bodies per profile.** The decoder handles both server styles (`ERROR_BODY` `lattice`: `{error, detail}`; `fastapi`: `{detail}` with the HTTP phrase), and the profile's `UNAUTHORIZED_DETAIL`, `FORBIDDEN_DETAIL`, inactive-user status and detail, and native token answers. All are read from the plan, never hard-coded.
- **Success shapes.** `One`: the row type. `Page`: the envelope in the profile's `page_shape` order and field names, with `total` only when declared. `Created`: 201 and the row. `NoContent`: `void`. `response="M"`: a named model type shared by every endpoint that returns `M`.
- **Scalars.** `Str` → `string`; `Bool` → `boolean`; `Float` → `number`; `Int` → `number` if the declared `ge`/`le` both lie within ±(2^53 − 1), else `bigint` (response columns have no bounds, so they are `bigint`); `Uuid` → `string` (canonical lowercase in responses); `Date`, `DateTime` → `string` (wire ISO text; a JS `Date` would drop microseconds). Nullable columns are `T | null`.
- **UNSET vs null.** A missing key is "not sent"; `null` is "set NULL", allowed only for parameters in the plan's `null_ok`. The spec table enforces this at run time, not only in the types.
- **Form bodies** (`body="form"`) are sent as `application/x-www-form-urlencoded`; body fields are then validated as text, as on the server.
- **`CallOpts`** = `{ baseUrl: string; token?: string | (() => string | null); fetch?: typeof fetch; signal?: AbortSignal }`. No global state.
- **Hooks.** For GET: `useX(params, opts?)` over `useQuery`, key `["x", canonicalParams]` (bigint-safe), exposing `{data, error: XError | null, isPending}` without throwing. Other methods: `useX()` over `useMutation`, with the same typed `error`. Query invalidation after a mutation is left to user code: the plan does not record which reads a write affects.

## 4. `lattice-rt.ts` and the JavaScript rules it must match

A line-by-line port of the prelude's `_convert`, `_constrain` and `parse_params`, as `lattice/gogen/rt/params.go` is for Go. Each row is a place where the obvious JavaScript diverges silently; each gets a helper and fixture cases.

| # | Area | Server (Python) | Obvious JS (wrong) | Rule |
|---|---|---|---|---|
| 1 | String length | `len()` counts code points | `.length` counts UTF-16 units (`"👋"` is 2) | `cpLen(s)` iterates code points |
| 2 | Lone surrogates | `json.loads` keeps `"\ud800"`; the check fails "must be valid UTF-8 text" | JS strings hold them | explicit well-formedness check, same message |
| 3 | `strip()` in Int/Float/Bool text | Python whitespace (includes `\x1c`–`\x1f`, `\x85`; excludes `﻿`) | `trim()` differs on those characters | `pyStrip` over Python's exact set |
| 4 | Int | text `[+-]?[0-9]+`; ±2^63 range; JSON `3.0` accepted as 3 | `Number()` rounds above 2^53 | BigInt parsing; the lossless JSON reader keeps int and float literals distinct |
| 5 | Float | `NaN`/`Infinity` tokens parse, then "must be a number"; a huge int overflows to "must be a number" | `JSON.stringify(NaN)` is `null`, which becomes "field required" or the default | the serializer writes Python's tokens so the wire, and the error, match |
| 6 | Numbers in messages | `_num(v)`: `str(int(v))` or `repr(float)` | `String(1e16)` is `"10000000000000000"`, Python gives `1e+16` | port `reprFloat` (Go's `rt.ReprFloat` is the model) |
| 7 | `pattern` | `re.fullmatch(p, v, re.ASCII)`, RE2 subset | JS `\s` is Unicode; `$` and `.` differ at newlines | `tsgen/regex.py` translates from the parsed tree (`check/re2.py`), never by string substitution: ASCII classes, `^(?:…)$`, no `m` flag, `u` flag. The runtime receives translated source only |
| 8 | `one_of` message | `repr()` of each value (`'a'`, Python escaping) | `JSON.stringify` uses double quotes | port `pyRepr` for Str and Int |
| 9 | Date, DateTime | `date.fromisoformat`, `datetime.fromisoformat` (3.11+ forms: `20240101`, week dates, offsets, fractions); naive is UTC; out-of-range fails | `new Date()` is lenient, milliseconds, local time | port `fromisoformat`, following Go's `dates.go`/`strptime.go` grammar; output canonical ISO text, never a JS `Date` |
| 10 | Uuid | hyphenated or 32 hex digits, any case, consistent hyphens | — | the same regex; canonical lowercase output |
| 11 | Bool text | `lower()` in `{true,1,yes,on}` / `{false,0,no,off}` | — | the same sets |
| 12 | Wire JSON | the server reads bytes | `JSON.stringify` cannot write `bigint` | the client's own serializer: bigint as a number literal, the tokens of row 5 |

**422 detail shape.** Parameters in plan order; one entry per parameter (the first failure wins); exact `loc` (e.g. `["body", "title"]`); exact `msg`; a non-object JSON body is `["body"]` "the body must be a JSON object".

**Settled by rule, not runtime:**
- **R-a. Path `Str` containing `/`.** Starlette decodes `%2F` before routing, so the request never reaches the endpoint. The client sends it unchanged and does not claim to predict routing; the guarantee covers parameter validation of requests that reach the endpoint. Decision-log row (known limitation).
- **R-b. Unicode version.** Code-point counting does not depend on it, and the Bool sets are ASCII. No rule; the gate confirms.

**Fixtures.** `gen_param_answers.py` extends the `params` corpus of Go's `gen_prelude_answers.py` with the JS hazards above: emoji and astral text at length bounds, lone surrogates, `\x1c`/`\x85`/`﻿` padding, 2^53 ± 1, ±2^63 and beyond, `NaN`, `Infinity`, `1e16`, `1e21`, regex newline edges, and the `fromisoformat` grammar (valid and invalid forms). Python's real `parse_params` writes the answers; `lattice-rt.ts` reproduces each byte for byte.

## 5. The equivalence gate

### 5.1 Claims

For every proof app and every request in its corpus:

1. TS `parseParams` and Python `parse_params`, given the same wire request, return the same result: equal converted values, or the same 422 detail (compared as canonical JSON).
2. Over real HTTP, for a caller the endpoint admits, the client reports a local 422 exactly when the running server answers 422, with identical detail.
3. Every server response decodes in the client losslessly (bigint values equal Python's ints) and matches the declared type; every error response maps to its declared `kind`; only 5xx, network failure and undeclared statuses become `Unexpected`.

**Bar: 0 mismatches.** No tolerance.

### 5.2 Proof apps

The FastAPI template API (`tests/template`), with its generated suite, and every program in the API codegen differential registry (`tests/api/test_codegen_differential.py` `PROGRAMS`). Unlike the Go gate, the template is included: its native token decoding runs only on the server, and the client just sends a bearer token.

### 5.3 Request corpus (per endpoint, fixed seed)

- (a) the requests of the importer's generated suites (`lattice/importer/suite.py`) and the registry's probes;
- (b) boundary requests generated from each `ParamSpec`: each bound ± 1, empty and at-length strings, missing, `null`, UNSET, wrong JSON type, form text, and every §4 hazard applied to every parameter of its type;
- (c) 200 seeded random requests mixing valid and invalid parameters.

The corpus is written to a checked-in file so any failure replays exactly.

### 5.4 Layers

| Layer | Checks | How |
|---|---|---|
| L1 runtime fixtures | `lattice-rt.ts` against `param_answers.json` | `node --test --experimental-strip-types` |
| L2 direct differential | claim 1 | `driver.ts` reads JSONL wire requests and writes verdicts; pytest runs Python `parse_params` on the same lines and compares |
| L3 HTTP differential | claims 2 and 3 | the generated client sends to the generated FastAPI app (uvicorn, ephemeral port, seeded database); records its local verdict, the HTTP answer and the decoded result |
| L4 types | `api.ts` and `hooks.ts` type-check under `strict`; a UI file whose `switch` misses a declared error kind must **fail** `tsc` | `tsc --noEmit` in `tests/ts/`, dev dependencies (`typescript`, `react`, `@tanstack/react-query`, their types) pinned in a lockfile |

L1–L4 need Node 22. Locally they skip with a reason when `node` is absent; in CI they are required, following the Go gate's pattern for `go`.

## 6. Language rules and governance

Changes to `docs/lattice-language-rules.md` and `lattice/targets.py`, in the same change as the runtime:

- **Client target.** Defined as a target that carries the *API request semantics* (parameter conversion and validation, wire formats, error bodies) and not T2. The parameter rules move into the semantics table in `targets.py` so they are defined once (R1) rather than only in the Python prelude.
- **R7.5** becomes "differential tests across every target that carries the construct" (today it says Python ≡ Go).
- **R14** gains a CI check: fail when a `ParamSpec` feature (`ge`, `le`, `min_len`, `max_len`, `one_of`, `pattern`, `email`, `optional`, `nullable`, `default`, form bodies) has no fixture case on the client.
- **Decision-log rows:** `Int` typed by declared range (representation, not a gate); R-a path `Str` with `/` (known limitation); a generation-time refusal (L89x style; code assigned in the plan) for any plan feature `emit.py` cannot produce. None is expected today: every RE2-subset pattern translates.

## 7. Build order

| # | Task | Depends on |
|---|---|---|
| 0 | Language rules and `targets.py`: client target, API parameter semantics in the table, decision-log rows | — |
| 1 | `gen_param_answers.py` and `param_answers.json` | — |
| 2 | `lattice-rt.ts` core: `cpLen`, `pyStrip`, UTF-8 check, Int/Float text, lossless JSON read and write, `reprFloat`, `pyRepr` | 1 |
| 3 | `fromisoformat` port (Date, DateTime) | 1 |
| 4 | `tsgen/regex.py` translator and its fixture cases | 1 |
| 5 | `parseParams` and error-body decoding; L1 green | 2, 3, 4 |
| 6 | `emit.py` → `api.ts`; `lattice codegen --target ts` | 5 |
| 7 | `hooks.ts` | 6 |
| 8 | L2: corpus generator, `driver.ts`, direct differential | 6 |
| 9 | L3: HTTP differential over the proof apps | 8 |
| 10 | L4: strict `tsc`, the negative exhaustiveness test, R14 client checks, CI wiring | 7, 9 |

Tasks 2, 3 and 4 are independent and can run in parallel.

**Stop point:** if L2 cannot reach 0 mismatches, the API parameter semantics are not defined well enough; fix the semantics table before emitting any client code beyond the runtime.

## 8. Out of scope (follow-ups)

- Option B (a TS target for T2 procedures) and option C (a UI profile): separate specs; C needs evidence first.
- Proving the client against the Go server: transitive once plan 5a's gate shows Go ≡ Python; a direct check is a follow-up.
- An OpenAPI export; vendoring the template's React UI; styling; publishing an npm package; Vue, Svelte or React Native adapters (`api.ts` has no framework dependency, so an adapter is a thin layer like `hooks.ts`).
- Inferring query invalidation after mutations.
- Any claim that A helps models build UIs. That needs its own pre-registered benchmark, reporting accuracy, silent failures, tokens, cost per correct task, program size, rounds and timing.

## 9. Success criteria

- L1–L4 green in CI with 0 mismatches over the template and every registry program.
- The negative exhaustiveness test fails `tsc` as intended.
- Every client decision-log row is present, and the R14 client checks run in CI.

## 10. Amendments (2026-09-29, from planning)

The following amendments to §4 and §5 are documented in the decision log and language rules (docs/lattice-language-rules.md):

1. **Auth runs before validation** (`lattice/api/codegen.py:1080-1086`): a server answers 401/403/inactive before any 422. Claim 2 therefore holds **for a caller the endpoint admits**; a local 422 predicts the server's answer once authentication passes. The L3 gate uses an admin token (the admin role is admitted everywhere, `codegen.py:867`).
2. **R-a widens:** a path `Str` that is empty, `.`, `..` or contains `/` cannot be routed faithfully (URL normalisation by `fetch`, Starlette routing). The client sends it; the corpus excludes it; the decision-log row names all four.
3. **Page envelope counts:** `total` is a `number`, decoded exactly (a value beyond 2^53 − 1 is a decode error, never rounded); `limit` and `offset` follow amendment 9.
4. **Hooks take `opts` as a required argument** (no provider/context).
5. **CLI:** there is no `lattice codegen` command; the plan adds `python -m lattice.tsgen`.
6. **"Required in CI"** becomes `LATTICE_REQUIRE_NODE=1` (no CI config exists).
7. **Generation refusal code:** `L893`.
8. **Every response column is `T | null`** (from the L3 gate, 2026-09-30): the lowered response schema does not carry nullability after `select`/`derive` (its `nullable` is empty after a projection), and the server answers null for such columns (realapps `read_item`: `"description": null`). A precise type needs the API IR to carry nullability (follow-up).
9. **Page `limit` and `offset` are `bigint` unless their declared range fits ±(2^53 − 1)** (from the L3 gate, 2026-09-30): the server echoes the requested value (`offset: Int(ge=0)` can be 2^63 − 1). A `page()` literal or a parameter whose `ge`/`le` both fit is a `number`, decoded exactly; otherwise a `bigint`, lossless.
