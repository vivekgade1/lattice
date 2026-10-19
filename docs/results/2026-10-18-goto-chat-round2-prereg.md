# The go-to place, chat round 2 (Spike F, round 2): pre-registration

## 1. Status, goal and the measured gap

2026-10-18, branch `np-chat-round2` (from `next-phases` c75cb48). **Written BEFORE any code.** Results:
`2026-10-18-goto-chat-round2-results.md`, data in `docs/results/data/2026-10-18-goto-chat-round2/`.
Round 1: `2026-10-16-goto-place-prereg.md` (with Amendments 1-2) and `2026-10-16-goto-place-results.md`.

**Binding owner rule (unchanged):** a workflow failure is observed or logically determined, never predicted. Free chat must
not state a failure without a tier 1 (observed) or tier 2 (determined) item id of a `lattice.impact/1` report behind it.
Anything else is "static: may fail" or "not decided".

**What round 1 measured (m).** The deterministic surfaces passed: app map 22/22 and the model-free router 22/22 with 0
unsupported failure claims and 0 model calls on routed questions. The free chat prose (sonnet through the `claude` CLI, ask
mode, `--readonly`) missed all three of its sub-gates:

| sub-gate | registered | observed |
|---|---|---|
| unsupported failure claims (literal, hard) | 0 | **72** (lenient, report-only: 49) |
| correct | >= 36/44 (p: 37-42) | **25/44** (27/44 with the N questions asked on S4) |
| I verbatim-id compliance | >= 10/12 | **8/12** |

By set (correct/n, literal claims): I 8/12, 22; H 0/6, 19; C 4/4, 1; M_rules 5/5, 0; M_workflows 1/5, 9; M_machines 3/3, 11;
M_access 4/5, 5; N 0/4, 5. The N questions ran against the wrong corpus (S instead of S4: round-1 deviation D5).

**Recon for this prereg (r: my heuristic script over the committed `agent/answers-agent.json`, using the frozen round-1
scorer's own functions; no model was called).** The 72 literal claims split roughly as follows:

| kind | n (r) | example (shortened) |
|---|---|---|
| negations and meta-sentences | ~26 | "So I can't say what would break."; "0 checks or workflows are determined to fail." |
| D's own sentence "... is mostly not decidable ..." | 5 | the frozen hedge list has `not decided` and `undecid*`, but not `not decidable` |
| recorded replay statuses | 17 | "Eight are `failed` and one is `unverified`." (Spike B capture-time statuses) |
| machine state names and quoted spec text | ~15 | "States: pending, sent, failed, suppressed."; "A check-in that fails validation is refused" |
| tier 2 item text copied without its id | 4 | "`scenario:deleting_a_device_is_no_longer_offered` fails at step <= 2." |
| real predictions or speculation | ~5 | H-6 "would fail with or without this invariant"; H-4 "which breaks the ... wording" |

So most of the 72 are literal matches, not predictions. But at least ~9 are real violations of the owner rule: the ones in
the last two rows. The model followed the frozen paragraph (it offered to draft a change in all 6 H answers), and still
wrote failure prose around it. **A prompt alone cannot meet a hard 0.** The fix has to be deterministic.

**Other measured facts (m).**
1. The round-1 scorer, question set, selftest, `FORMATS.md` and candidates exist **only in scratch**
   (`.../scratchpad/questions/`). Their sha256 values equal the orchestrator's `freeze.sha256` (`goto_score.py` d7e5e5aa...,
   `qset.json` 4eaca957..., selftest 37e8622c..., `FORMATS.md` faad2546...). Scratch can be cleaned, so the build node
   first copies them into the repo with hashes verified (section 9).
2. The round-1 scorer hard-codes the round-1 shape: `n == 44`, `correct >= 36`, `comp >= 10` in `score()`'s gate, and
   `SET_COUNTS` in `qset_problems`. Its per-question functions (`check_map`, `check_router`, `check_agent`,
   `failure_claims`, `lenient_claims`, `tier3_called_failure`) take any set size. They dispatch H logic on the set name
   `H_hypothetical` and I logic on `I_impact`, and every other set name gets content checks on the agent surface.
3. In the real Studio session path, the session streams text deltas to the UI as `partial` events
   (`Transcript._stream_event`). **Any post-check of the final text alone would still show unchecked text while it
   streams.**
4. `session.py` has no hook between the model's text and the UI. `ask.py` has the deterministic citation checker
   (`check_citation`: target existence, quote match per element kind, `code:` locations). `impact_ask.route` is model-free
   and is called only from `POST /api/ask`, never from the session path.
5. Round-1 agent cost: USD 2.15 list estimate for 44 questions (USD 0.049 per question, 146 turns, 500 s), plus 0.37 + 0.32
   + 0.31 for the variants and a smoke run.
6. `claude` 2.1.303 is installed. Streak refs a0679e9 (**S**) and 1d00ede (**S4**) exist in `~/Documents/streak`, which is
   read only through `git archive`. `workflows.json` sha256 af1383db...c573a0. API.md reads **v1.25**. Gate rules end at R108.
7. **A browser is connected now:** `list_connected_browsers` returned one browser ("Browser 1", macOS, `inUse: true`).

Labels: **(m)** measured, **(r)** recon script output (a blind expectation), everything else a **prediction (p)**.

## 2. Design: deterministic first, the model last

There are four parts. Each is switchable, and with all of them off every existing output is byte-identical (PB2).

| part | what | where |
|---|---|---|
| (a) **map router** | more question shapes answered model-free from the app map (not-built, workflows-by-route, rules-by-status, lifecycle basis, access cell, broader hypothetical cue) | new `lattice/spec/chat_route.py`, called after `impact_ask.route` in `POST /api/ask` and as a **pre-route** in the session path |
| (b) **claim checker** | a deterministic post-check of every chat text block, in the session path and on the one-shot model answer: unsupported failure sentences are replaced with Lattice's own deterministic text or withheld; tier ids are enforced verbatim | new `lattice/spec/claim_check.py` (reuses `ask.py`'s digest and `check_citation`, `appmap`, and `impact_ask.impact_view` read-only) |
| (c) **frozen prompt** | one more paragraph, `_CHECK_RULES`, frozen below | `session.py`, only with the checker on |
| (d) **new question set** | >= 60 questions from a new author with new wording, a new candidate set, H (routed and free) and N on S4; hash-frozen with a reused hash-frozen scorer; scored once | section 6 |

**Switch (R1, ruling Q1).** A new optional `studio.toml` table `[chat]`, with keys `route = true|false` (part a) and
`check = true|false` (parts b and c), both default false. `check` needs `[implementation]`. `[impact]` is needed only for the
tier-id parts; without it, every failure sentence is unsupported by definition (there are no relevant ids). A bad value
makes Studio refuse to start, naming the key, like the other tables.

### 2.1 The claim checker, `lattice.chatcheck/1` (D1)

`check_text(text, ctx) -> CheckResult`. This is a pure function: no model, no subprocess, no write. `ctx` holds:
- the question;
- the **relevant changes** (change ids named in the user message, the session's open change, and change ids named in the
  answer), each with D's report built in process exactly as `impact_ask.impact_view` builds it (tiers 2+3,
  `observe` off, no tier 1 run);
- tier 1 ids found in the raw `tool_result` content of a `change_impact` call made **in this turn** (change mode,
  `observe: true`);
- the app map (`appmap.build_appmap`), and the spec digest (`ask.build_digest`, used for rule existence and quote checks
  through `check_citation`).

Output: `{schema: "lattice.chatcheck/1", text, units[{i, before, disposition, reason, after}], counts{kept, supported,
labelled, replaced_item, replaced_tier3, replaced_quote, replaced_replay, replaced_element, withheld, appended,
speculative_hedge, unknown_id}}`, canonical JSON.

**Units and the claim rule are the frozen round-1 scorer's, copied verbatim** (the literal `FAIL_RE`, `HEDGE_RE` and the
sentence splitter: lines, then `(?<=[.!?])\s+`). Probe PC1 pins equality with the scorer on every corpus. A **flagged unit**
is a unit that matches `FAIL_RE` (literal).

**Pre-pass (all units):**
- **E2 unknown ids.** A unit holding a Lattice-minted id that does not exist is **withheld**: a `t[1-4]-<10 hex>` id that
  is in no relevant report (tiers 1-3, plus this turn's tier 1 ids), a `wf-<10 hex>` id not in the map, or an `R<n>` that is
  not a rule of the spec. Lattice mints these ids, so a non-existent one is fabricated. Routes are not checked: a user may
  ask about a route that does not exist.

**Dispositions for a flagged unit (first match wins):**

| # | condition | action |
|---|---|---|
| D0 supported | contains a tier 1 or tier 2 id of a relevant report (or a tier 1 id of this turn) | keep |
| D1 owner-labelled | contains an owner label: `static`, `not decided`, `undecid*`, `not run`, `no change` (the scorer's hedge list **minus** `may`, `might`, `could`) | keep |
| D2 item match | contains the exact `target` of exactly one relevant tier 1 or tier 2 item | **replace** with that item's verbatim rendering: "`<id>` (tier N, observed/determined): `<message verbatim>`" |
| D3 tier 3 | contains a `t3-` id, the `target` of a relevant tier 3 item, or the words "tier 3", with no tier 1 or tier 2 id | **replace**: "`<id>` (tier 3, static: may fail; not a claim): `<label verbatim>`" (for each named item; "Tier 3 items are static: they may fail and decide nothing." if none is named) |
| D4 quote | the failure word lies inside the text of a spec rule the unit names (`check_citation(digest, {target: rule:Rn, quote: <the unit's words around it>})` holds), or is a state or edge name of a map machine the unit names | **replace** with the source verbatim: rule: "Rn (spec text, quoted; whether anything fails at run time is not decided here): "`<ReviewData text>`""; machine: "Lifecycle `<key>` (basis: `<basis>`; its states are code facts; whether anything fails is not decided): states `<...>`, edges `<...>`" |
| D5 recorded replay | names a `wf-` id of the map | **replace** with the map's row: "Workflow `<wf-id>` (`<name>`): recorded replay status `<status>` at capture time`<, step i answered N, expected M>`; what any change does to it is not decided." |
| D6 named element | names a route, `Rn`, `machine:`, or a change id without a report | **replace**: "Whether `<element>` fails is not decided: Lattice decides failures only for a drafted change (tier 1 observed, tier 2 determined). Static: it touches `<touches from appmap.touches, capped at 8 keys + total>`." |
| D7 otherwise | anything else (including negations, speculation hedged only by may/might/could, and echoes of the user's words) | **withhold** |

**Replacement only, never relabelling.** The model's own sentence is never kept with a label added: it is replaced by
Lattice's text taken from the report, the map or ReviewData (no label laundering, mutant CM6). Identical consecutive
replacements are merged. A flagged unit in a Markdown table row removes the row, and its replacement goes into the footer.

**Enforcement after the unit pass:**
- **E1 verbatim tier ids.** For each relevant change with an `ok` report: if the checked text's set of stated `t1-`/`t2-`
  ids differs from the report's set (plus this turn's tier 1 ids), append the **Lattice record** block. It holds the
  router's fixed template line (`impact_ask._tier_template`) and one line per tier 1 and tier 2 item:
  "`<id>` (tier N): `<message verbatim>`". For a change without an `ok` report, append the router's fixed `not_specified`
  sentence ("Lattice has no impact report for cN: `<why>`. Nothing is decided.").
- **E3 hypothetical without a change.** If the user message matches the frozen hypothetical cue (`impact_ask.HYPOTHETICAL`,
  or the broader counterfactual cue frozen in `chat_route.py`), names no change id, and the checked text has no
  not-decided phrase, append `impact_ask.NO_PREDICTION` verbatim.
- **Footer** when k > 0 units were withheld: "Lattice withheld k sentence(s) that stated a failure without a tier 1 or
  tier 2 item id. Lattice does not predict failures; it decides them only for a drafted change." (no `FAIL_RE` word,
  pinned by PC2).

**Properties pinned by probes:**
- the frozen scorer counts **0 literal claims on any checker output** (PC2);
- the checker is idempotent (`check(check(x)) == check(x)`) and every fixed sentence it emits is a fixed point;
- the output is deterministic across processes and hash seeds;
- routed answers (part a) pass through it unchanged.

**What it never does:** call a model, run tier 1, start a process, write a file, or edit D's report. It also never rewrites
a failure word into a synonym (no "does not pass" substitution, mutant CM19): that would evade the regex without changing the
claim.

### 2.2 The session path with the checker on (D2)

- **No unchecked text reaches the UI.** With `[chat] check = true`, `Transcript` buffers each assistant text block (no
  `partial` text events) and emits it once, after `check_text`, as `{type: "text", text: <checked>, partial: false,
  message_id, checked: {counts}}`. This applies to every text block of a turn, including text written between tool calls.
  The raw text is never put in an event or a log Studio serves.
- `tool_result` content of `mcp__lattice__change_impact` is kept in memory for this turn only, for D0 and E1's tier 1 ids.
  The event's `summary` is unchanged.
- The `result` event gains `check: {counts summed over the turn}`, plus `routed: null | "impact" | "map" | "hypothetical"`.
- **Pre-route (with `[chat] route = true`):** `SessionManager.message` first runs `impact_ask.route`, then
  `chat_route.route`, on the user text (and the selected targets). A routed turn spawns **no** process. It adds the
  `user`, `text` (the routed answer rendered as Markdown, then passed through `check_text`), `result` (`ok`, `cost_usd` 0,
  `num_turns` 0, `routed`) and `status idle` events. A routed turn does not need the `claude` binary, and it is not part of
  the Claude session's memory (ruling Q5).
- `system_prompt` appends `_CHECK_RULES` (below) when the checker is on. `_IMPACT_RULES` stays byte-identical.
- `SessionManager` receives the checker context from `server.py` (`Studio.get_appmap`, `impact_view`, the digest), cached
  per input hashes for the life of a turn. It never writes.
- **One-shot `POST /api/ask`:** with `route = true`, `chat_route.route` runs after `impact_ask.route` and before the model.
  With `check = true`, the model's `answer` and `explanation` are passed through `check_text`, and the answer gains an
  optional key `check` (`kind` keeps its four values; a `yes`/`no`/`value` answer whose `answer` unit was withheld is
  downgraded to `not_specified` with the fixed text, mirroring `ask.py`'s downgrade rule). **`ask.py` is not edited.**
- The UI (`chat.js`) shows a small text badge under a checked message: "Checked by Lattice: k withheld, m replaced". The
  badge is a text word, not colour alone, and is absent when the event has no `checked` key.

**`_CHECK_RULES`, frozen here (one paragraph):**
> "Lattice checks every sentence you write before the owner sees it. A sentence that says something fails, failed or breaks
> is shown only if it carries the id of a tier 1 or tier 2 item (t1-..., t2-...) from change_impact's report on the change
> it is about; Lattice withholds or replaces any other. So never describe failures in your own words. For a change, list its
> tier 1 and tier 2 item ids exactly as the tool returns them, and call tier 3 items 'static: may fail'. For a change that
> has not been drafted, say that it is not decided and offer to draft it. A workflow's recorded status is a capture-time
> fact: give it with its wf- id as 'recorded replay status: <status>'. For rules, workflows, lifecycles, endpoints and
> access, use the status words and ids exactly as the tools return them (implemented_verified, implemented_unverified,
> required_not_implemented, no_element, spec_only, verified, failed, unverified, static, observed), and list no id the tools
> did not return. Quote spec text only with its rule id."

### 2.3 The map router, `chat_route.py` (D3)

These are model-free answers from `lattice.appmap/1`, built with the same functions as `GET /api/appmap` and
`/api/appmap/element`. The regexes are frozen in the module's **first commit** and hashed in `freeze-build.sha256` before
the question set is scored. The question-set author never sees them (section 6). Shapes:

| shape | trigger (lexical; frozen) | answer (fixed template; map values verbatim) |
|---|---|---|
| MR1 built? | an exact route (`impl.routes_in_text`) + a build verb (built, implemented, exist, coded, in the code, available) | the endpoint status (`spec_only` -> "not built: in the spec, not in the code (spec_only)"; else the status word + evidence `code:` lines); rules `required_not_implemented` that cite it |
| MR2 workflows by route | (workflow, flow, scenario replay) + an exact route [+ a status word] | the id set of map workflows whose `routes` contain it (filtered by status when named), each with name and recorded status |
| MR3 rules by status | rules + a status phrase from a frozen list (implemented but not verified, not built, verified, without code, no element) | the id set of rules with that `impl_status` |
| MR4 lifecycle basis | (lifecycle, machine, state machine, states) + a map machine (key, spec target or table name) | basis word + states + edges with `in_spec`/`in_code` |
| MR5 access cell | a ReviewData row name (or "no token"/anonymous) + an exact route | spec value, code `auth`, refusals, `observed[]` statuses (labelled observed at capture) |
| MR6 broad hypothetical | a counterfactual cue (suppose, what if, if we, if I, were to, imagine, hypothetically) and **no** change id | `impact_ask.NO_PREDICTION` + static `touches` of named elements (identical to the F router's (B) answer) |

A question that matches no shape, or matches two shapes that disagree, is **not routed**: it goes to the model, then the
checker. Routed answers carry `citations` of the map keys (`rule:Rn`, `wf-...`, `impl:route:...`) and `usage.tokens: 0`.
On `POST /api/ask` the answer gains an optional key `map` (the rows used). `kind` keeps its four values.

**API v1.26** (additive; ruling Q9): `[chat]` keys; session `text.checked` and `result.check` / `result.routed`; ask
answer keys `check` and `map`. Every existing route answers byte-identically with `[chat]` absent.

## 3. Gates and blind predictions

### 3.1 Dev stage (builder, before the freeze; round-1 data is **dev data** now, not blind)

| id | probe | prediction | pass |
|---|---|---|---|
| PC1 | literal equivalence: checker's flagged units == `goto_score.failure_claims(text, relevant)` on round-1 agent answers (44 + 4 N-on-S4 + 2 observe), the scorer selftest's `claims` cases, and >= 40 builder fixtures | equal | 100% |
| PC2 | fixed point: frozen scorer literal claims on every checker output = 0; idempotence; the footer and all fixed sentences have no unhedged `FAIL_RE` word | 0; idempotent | all |
| PC3 | **offline replay of round 1:** round-1 agent answers through the checker (ctx rebuilt from the round-1 reports and maps), scored by the frozen scorer | literal 0, lenient 0; correct **33-37/44** (p, point 35: H +6 via rewrite, I +4 via E1); I compliance 12/12; damage (correct before, incorrect after) 0 | literal 0; damage 0; compliance 12/12 (dev gate) |
| PC4 | disposition fixtures: >= 3 per D0-D7 and E1-E3, each asserting the exact output bytes | all | all |
| PS2 | session with a fake `claude_bin` replaying stream-json fixtures (made from round-1 answers plus planted violations): check mode emits 0 `partial` text events; every text block, including pre-tool blocks, is checked; `result.check` sums the counts; routed turns spawn 0 processes (the fake logs invocations) | as stated | exact |
| PR2 | router on the round-1 question set (dev): every routed answer correct under the frozen scorer's agent checks; 0 C controls routed; I and H routing unchanged from F | routed share of M+N 40-80% (p) | routed precision 100%; 0 C routed |
| PB2 | **byte-compat with `[chat]` absent** vs `git archive` of c75cb48: session argv, settings, prompt (4 configs, as F's PS); `Transcript` events on 6 replayed stream fixtures (ts and seq normalised); every existing route on the fixture projects, real and mock (`/api/model`, `/api/projects`, `/api/appmap*`, `/api/impact`, `/api/changes/cN`, `POST /api/ask` routed and non-routed with stub); `tools/list` and MCP instructions; `lattice --help`; fake-DOM render of a chat transcript without `checked` keys identical; empty diff for `ask.py`, `impact_ask.py`, `appmap.py`, `impact_report*.py`, `implementation.py`, `mcp/tools.py`; pytest: same failure ids as the base | identical | all |
| PW2 | no write, no process: snapshots of the project, inventory, state and reports dirs around 50 checked answers, 50 routed answers and the session replay; recording `claude` shim log empty for routed turns; `docker ps -a` equal | 0/0/0 | all 0 |
| PD2 | determinism: `check_text` and `chat_route.route` outputs over all dev corpora, 10 processes, random `PYTHONHASHSEED`, two scratch roots | 1 distinct output | 10/10 |
| PX2 | time on S: `check_text` per answer, ctx cached per turn (p50, p95); pre-route p50 | 0.05-0.4 s; <= 0.6 s; <= 0.25 s (p) | p95 <= 1.0 s; pre-route p50 <= 0.5 s |
| PAPI2 | `## v1.26` fenced shapes == real and mock answers recursively; header v1.26 | pass | pass |
| PU2 | `chat.js` badge: `node --check`, fake-DOM walk of a checked and a routed transcript, the forbidden-word scan (`will fail`, `would fail`, `will break`, `would break`, `predict` outside the negative sentence) on the added strings | all | all |
| PP2 | the mock uses the same `chat_route` and `claim_check` over its fixture; mock == real key for key on routed and checked fixture answers | equal | equal |
| GATE | `dogfood/studio/check.sh` with new rules R109-R112 (`[chat]` bad value refused; `POST /api/ask` routed map answer has `usage.tokens` 0 and `map`; `check` key absent without `[chat]`; session `result.routed` documented values) | PASS; 509 + 6-16 cases; 62 + 2-4 scenarios (p) | PASS |
| mutants | section 4, control first | 19/19 killed (p) | all killed |

### 3.2 Question-set stage (orchestrator, once, after both freezes)

The corpora and setup follow round-1 `FORMATS.md`, with **one Studio server per corpus**: S (a0679e9 + `workflows.json`,
`[implementation]`, `[impact]`, `[chat] route = true, check = true`, the new change records) and S4 (1d00ede, static
inventory, `[implementation]`, `[chat]` on). **Each question runs on its own corpus's server** (fixing round-1 D5).

| surface | gate (registered) | blind prediction |
|---|---|---|
| **G1 map API** (M + N: 30) | 30/30 exact | 30/30 |
| **G2 router, `POST /api/ask`** (I_routed 6 + H_routed 4 + C 6: 16) | 16/16 exact under the frozen scorer's router checks; 0 literal claims; 0 model calls on routed questions | 16/16 |
| **G3 session (real Studio session path, real `claude` CLI), all 64** | (i) all run with status ok; (ii) **0 literal unsupported failure claims** (hard); (iii) correct **>= 52/64**; (iv) I verbatim-id compliance **>= 11/12**; (v) class floors: H **>= 8/10**, N **>= 4/6**, M_workflows **>= 6/8**; (vi) routed answers: >= 95% correct and 0 claims | (ii) 0 (by construction); (iii) **52-60, point 56**; (iv) 12/12; (v) H 8-10 (routed 4/4, free 4-6), N 4-6, M_workflows 6-8; (vi) 100% |
| G4 browser (PBR, section 5) | 17/17 states pass (a)-(g) on the **real** server; NOT RUN if no browser is connected at walk time | pass, if run; a browser is connected at prereg time (m) |

**Note on G2 (fixed now, before the set exists):** G2 covers only the questions F's frozen patterns route, so that it
stays the round-1 router gate on new wording. The 6 `I_free` and 6 `H_free` questions on `POST /api/ask` go to the map
router (MR6), or to the model and then the checker. They are scored on that surface as report-only.

**Per-class blind predictions for G3 (correct/n):** M_rules 5-6/6, M_workflows 6-8/8, M_machines 3-4/4, M_access 4-6/6,
N 4-6/6, I 11-12/12 (routed 6/6, free 5-6), H 8-10/10, C 5-6/6, W_prose 2-4/4, X_absent 2/2. (The class lows do not add up
to the total's low: the total range is my 80% interval on the sum.)

**Report-only (never gating), predicted where marked:**
- the lenient count (Amendment 2): 0 (p);
- **the raw arm:** the same session runs' raw text (from the `claude` stream tee, section 7) scored by the frozen scorer,
  which measures prompt (c) alone: literal claims **10-40** (p; round 1: 72 on 44), correct **38-50/64** (p);
- checker actions on G3: withheld 5-30 units, replaced 15-60, E1 appended on 1-6 of the 12 I answers, E3 on 0-4 H
  answers, `speculative_hedge` 0-10, `unknown_id` 0-3 (p);
- **damage**: questions correct in the raw arm and incorrect after the check: 0-2 (p);
- session routing rate on the new wording: M + N **10-21 of 30** routed (p), H_free 3-6 of 6 hit the broad cue (p), C 0
  routed (p);
- cost: main run USD **1.8-3.5** (p, point 2.5), cost per correct answer, tokens, turns, wall time;
- semantic audit: the orchestrator lists every kept or replaced unit containing a failure word, with its disposition, and
  marks any it judges a disguised claim (post hoc, manual, labelled as such);
- the ablation and one-shot arms (section 7), if the budget allows.

## 4. Mutants (`dogfood/chat2_mutants.py`, scratch copy; the control passes first; predicted 19/19)

| id | mutant | killed by |
|---|---|---|
| CM0 | control (unmutated) | must pass every killer below |
| CM1 | any well-formed `t1-`/`t2-` id counts as support, even from another change's report | PC4 (cross-change fixture), PC3 |
| CM2 | a `t3-` id counts as support | PC4, PC3 (I tier3 check) |
| CM3 | `may`/`might`/`could` accepted as an owner label | PC4 speculative fixtures |
| CM4 | negations exempted (the lenient rule used as the checker rule) | PC1, PC2 |
| CM5 | sentence splitter splits on lines only | PC1 |
| CM6 | label laundering: the model's sentence is kept and a label appended | PC4 (exact bytes), PC3 |
| CM7 | E1 off (no Lattice record when ids are missing) | PC3 compliance, PC4 |
| CM8 | E2 off (a fabricated `t2-` or `wf-` id kept) | PC4 |
| CM9 | a flagged table row kept | PC4, PC2 |
| CM10 | only the final text block is checked, not pre-tool blocks | PS2 |
| CM11 | partial text events emitted in check mode | PS2 |
| CM12 | the checker or `_CHECK_RULES` active without `[chat]` | PB2 |
| CM13 | a routed session turn spawns `claude` | PS2 (fake binary log), PW2 |
| CM14 | MR1 answers "built" from a route-prefix heuristic instead of the endpoint status | PR2, PC4 (S4 fixture) |
| CM15 | the relevant report is built with tier 1 (`observe`) | PW2 (containers, shim), PB2 |
| CM16 | not idempotent (the checker re-labels its own output) | PC2 |
| CM17 | D6 drops the static neighbourhood | PC4 (compares with `appmap.touches`) |
| CM18 | D4 renders a paraphrase instead of the ReviewData text | PC4, `check_citation` assertion |
| CM19 | synonym evasion: "fails" rewritten to "does not pass" and the unit kept | PC4 (exact bytes; the kept unit still names an element and must be D6) |

Each mutant is one asserted text replacement. The killer is named in `mutants.json`, and kills by named unit tests are
disclosed as such (round-1 practice).

## 5. Real-browser walk (PBR, gated row G4)

**Who:** the orchestrator, in its own session, after the build freeze. The build node claims no browser result.
**B0:** `list_connected_browsers` at walk time. With no browser connected, G4 is **NOT RUN** and never described as passed.
**B1:** the **real** server (`lattice studio`, not the mock) on a scratch copy of S with `[implementation]`, `[impact]`,
`[impact] reports`, durable state, the 6 dev change records and `[chat]` on, at `127.0.0.1:<port>`. A second instance runs
on S4 (static, not-built states). Fixture variants reach the remaining states: no `[implementation]`, a missing inventory, a
tampered extract, no spec, S-stale, `[state] enabled = false`, a saved tier-1 report matching and mismatching.
**B2:** for each of the **17 states of F's D4** (9 map, 8 impact):
- (a) page text sha256 and `data-state`;
- (b) accessibility tree: 0 unnamed interactive nodes;
- (c) console: 0 errors and 0 warnings from Studio scripts (the console capture starts before navigation);
- (d) network: only GET to `/api/model`, `/api/projects`, `/api/appmap*`, `/api/impact*` and static files;
- (e) `scrollWidth <= innerWidth` at 1280 px and at 375 px. If `resize_window` does not change `innerWidth` (round-1
  finding), the 375 px check uses a same-origin `<iframe style="width:375px">` of the same URL, read through
  `contentDocument` and labelled "iframe width";
- (f) 40 Tab presses log the focused names: tabs, the first 5 table rows and the drawer buttons are reached, the focus style
  is visible, and Escape closes the drawer;
- (g) on `#impact`, every report item id is in the page text, and the four tier headings are verbatim.

**Two extra chat states (report-only, not part of the 17):** one checked session answer (badge text present, no raw text in
the DOM or in network responses) and one routed answer (`result.routed`, no `claude` process: the shim log is empty).
**B3:** `data/browser/<state>.json` plus screenshots per view and width. **B4:** a PW2 snapshot around the walk.
**Pass:** (a)-(g) on 17/17 states. One failure is a finding, never relabelled.

## 6. The new question set (authored and frozen before the build is scored)

**Author (R2):** a new, separate agent: not round 1's author, not the builder, not this prereg's author. It receives a
**brief** written by the orchestrator from this section only. It never reads this prereg's sections 2.1-2.3, `chat_route.py`,
`claim_check.py`, `session.py`, or round 1's `qset.json`.

**What the author reads:**
- the Spike C reports and ReviewData dumps for S and S4;
- `workflows.json`;
- round 1's `FORMATS.md` and `lattice.goto-qset/1` schema, and the frozen round-1 scorer (to write checks it can score);
- D's CLI output for **its own new candidates**.

**What the author writes:**
- `qset.json` (`schema: lattice.goto-qset/1`, `spike: F round 2`);
- 6 **new** candidate changes (c1..c6; one is a pure-rewording control), with `files_sha256` and the D report expectations
  (`--tiers 23 --baseline none`), recorded through the same scripted-stub method as round 1;
- the round-2 wrapper `chat2_score.py` and its selftest (below).

**Content (64 questions, each with a known answer and a cited source):**

| set (scorer dispatch name) | n | corpus | requirement |
|---|---|---|---|
| M_rules | 6 | S | id sets or statuses from the report |
| M_workflows | 8 | S | id sets from report flows (by route, status, name); the `equal` mode where a list is asked |
| M_machines | 4 | S | basis, states, edges |
| M_access | 6 | S | ReviewData cell + code auth + observed statuses |
| N_not_built | 6 | **S4** | `spec_only` endpoints and `required_not_implemented` rules, including at least 2 phrased without the word "built" |
| I_impact | 12 | S | 6 changes × 2; per change one **I_routed** (a change id + one of F's impact words) and one **I_free** (a change id, none of `break, breaks, broken, fail, fails, failing, impact, affect`) |
| H_hypothetical | 10 | S | no change id; **subset `routed`** (4): match one of F's (B) patterns; **subset `free`** (6): match none of F's patterns |
| C_control | 6 | S | spec questions that must not be routed by F's patterns; at least 3 use a failure word in the user's own text (the echo test) |
| W_prose | 4 | S | open "walk me through" workflow questions; content checks; agent only |
| X_absent | 2 | S | a change id with no record (state `no_change`); content checks: "no report / not decided"; agent only |

Each question carries `subset` where named above. N questions carry `corpus: "S4"`.

**Wording difference:** no new question may have a word-set Jaccard similarity of >= 0.6 with any round-1 question (frozen
round-1 `qset.json`, sha 4eaca957). The wrapper's selftest checks this mechanically.

**`chat2_score.py` (wrapper; standard library only).**
- It **imports the frozen round-1 scorer** from the committed copy, asserts its sha256 (d7e5e5aa...) at start, and exits 2
  on a mismatch.
- It calls only that scorer's functions for every per-question verdict (`check_map`, `check_router`, `check_agent`,
  `failure_claims`, `lenient_claims`), so the claim rule, the hedge list, the content checks and the I checks are round 1's,
  byte for byte. `score()`'s round-1 `summary.gate` (shaped for 44 questions) is ignored.
- It aggregates the gates of section 3.2 (G1 n=30, G2 n=16, G3 n=64 with floors and routed precision) and the report-only
  rows. It scores the raw arm as a second agent answers file (`--raw`).
- It reports `partial: k of n` whenever any answer is not `ok`.
- Selftest: qset shape (counts, subsets, corpus, routing phrasing against F's frozen patterns, Jaccard), plus >= 20 cases
  covering every gate's boundary.

**Freeze (R3):** before the builder runs any gated probe, the orchestrator records the sha256 of `qset.json`,
`chat2_score.py`, its selftest, the candidates, the brief, and the copied round-1 scorer in
`docs/results/data/2026-10-18-goto-chat-round2/freeze.sha256`. The builder never opens the question directory; the runner
reads it. The builder records `freeze-build.sha256` (modules, server, session, mock, UI, runner, agent driver) before the
set is scored. **Scored once.**

## 7. The real-agent exercise (claude CLI only; USD cap 8 total)

- **Driver** `dogfood/chat2_agent.py` drives the **real Studio session HTTP routes** (`POST /p/<proj>/api/sessions`
  `{mode: ask}`, `POST .../messages`, poll `.../events`). It does not rebuild argv itself (round 1 did). There is one fresh
  session per question, on the question's corpus server.
- `[llm] claude_bin` points at a scratch wrapper that execs `claude --max-turns 12 "$@"` and tees stdout (stream-json) to
  `$SCRATCH/streams/<qid>.jsonl`, the source of the raw arm. The wrapper sits outside Studio's served paths.
- `[llm] session_timeout = 600`. Model `sonnet`. The environment has no API key (`cli_env` strips credentials; the driver
  also unsets every `ANTHROPIC_*` variable and asserts none is set).
- **Scored text** = every checked text event of the turn, joined by a blank line (what the owner sees). **Raw text** = the
  same blocks from the tee.
- **Order and caps (list estimate from `result.cost_usd`):**
  1. smoke, 1 question from the dev set, <= USD 0.35;
  2. **the gated run, all 64** (no question starts once the spent total plus 1.5 × the largest per-question cost so far
     would exceed USD 8);
  3. report-only, only if budget remains: (a) **ablation**: the questions the session routed, re-run with `route = false`,
     to show what the model plus checker does on them; (b) **one-shot arm**: the non-routed questions through
     `POST /api/ask` with the real CLI generator and `check = true`; (c) 2 change-mode `observe: true` questions (Docker,
     tier 1): their tier 1 ids must survive the checker (D0 via this turn's tool result).
- **A usage limit** (`error.kind: usage_limit`) stops the exercise: G3 is **NOT RUN** (or `partial: k of 64`) and never
  scored as a pass. A timeout or error is `not_run` for that question.
- Usage reported: tokens, cache tokens, list estimate, wall time, turns, cost per correct answer, per arm.

## 8. Blind vs post hoc

Fixed now: the design, the frozen paragraph, the gates, every prediction and pass rule, the mutant list, the question-set
shape, and the wrapper's aggregation rules. Router regexes are frozen at the module's first commit, before the question set
is scored, and the author never sees them. Round-1 answers and questions are **dev data**: tuning on them is allowed and
is not blind evidence. The blind evidence is G1-G3 on the new set, **scored once** by the orchestrator. A missed prediction
is a finding. Any fix after the score is a separate post-hoc commit, rerun and reported as a rerun with deltas, never as
blind. The literal claim rule is not loosened. "Not decidable" (D's wording) stays outside the frozen hedge list; the
checker replaces such sentences (D6/D7 or E1) instead of changing the scorer.

**Pass:**
- every gated row of 3.1 and 3.2 (G1, G2, G3 all sub-gates);
- mutants 19/19;
- PB2;
- G4 passed, or honestly NOT RUN (the results' first line then says the round passes without its browser gate).

## 9. Commands

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-fc2 PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/chat2
R1Q=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/questions
OUT=docs/results/data/2026-10-18-goto-chat-round2
WF=docs/results/data/2026-10-15-workflow-layer/workflows.json                 # sha256 af1383db...c573a0
# 0. preserve round 1's frozen artifacts (build node, first action); hashes must equal round 1's freeze.sha256
mkdir -p $OUT/frozen-r1 && cp -R $R1Q/{goto_score.py,goto_score_selftest.json,FORMATS.md,qset.json,candidates,reports} $OUT/frozen-r1/
(cd $OUT/frozen-r1 && shasum -a 256 -c ../../2026-10-16-goto-place/freeze.sha256)  # every line OK, else stop
git -C /Users/vivekgade/Documents/streak status --short                       # unchanged; read only via git archive
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/llm tests/modules -q -rs > $OUT/pytest-before.txt
# dev stage (builder)
uv run python dogfood/chat2_probes.py --stage dev --streak-ref a0679e9 --streak-ref2 1d00ede --workflows $WF \
    --round1 $OUT/frozen-r1 --round1-answers docs/results/data/2026-10-16-goto-place --scratch $SCRATCH/p --out $OUT
uv run python dogfood/chat2_mutants.py --scratch $SCRATCH/m $(seq -f CM%g 1 19)
uv run python -m pytest tests/spec/test_claim_check.py tests/spec/test_chat_route.py tests/studio/test_chat_check_session.py -q
dogfood/studio/check.sh --json > $OUT/studio-check-sh.json
shasum -a 256 lattice/spec/claim_check.py lattice/spec/chat_route.py lattice/studio/session.py lattice/studio/server.py \
    lattice/studio/mock_server.py lattice/studio/web/js/chat.js dogfood/chat2_*.py > $OUT/freeze-build.sha256   # BEFORE scoring
# question-set stage (orchestrator, once)
python3 $SCRATCH/questions/chat2_score.py --qset $SCRATCH/questions/qset.json --self-test $SCRATCH/questions/chat2_score_selftest.json
uv run python dogfood/chat2_probes.py --stage qset --questions $SCRATCH/questions --out $OUT/qset          # G1, G2
uv run python dogfood/chat2_agent.py --questions $SCRATCH/questions --usd-cap 8 --out $OUT/agent          # G3, last
python3 $SCRATCH/questions/chat2_score.py --qset $SCRATCH/questions/qset.json --surface agent \
    --answers $OUT/agent/answers-checked.json --raw $OUT/agent/answers-raw.json --reports $OUT/agent/reports --json
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; node --check lattice/studio/web/js/chat.js
```

## 10. File ownership (build node)

**Owns (new):**
- `lattice/spec/claim_check.py`, `lattice/spec/chat_route.py`;
- `tests/spec/test_claim_check.py`, `tests/spec/test_chat_route.py`, `tests/studio/test_chat_check_session.py`,
  `tests/studio/test_chat_check_api.py`;
- `dogfood/chat2_probes.py`, `dogfood/chat2_mutants.py`, `dogfood/chat2_agent.py`;
- stream fixtures under `tests/studio/fixtures/chat2/`;
- the results data directory (except `freeze.sha256`, which is the orchestrator's).

**Additive edits only, all gated on `[chat]`:**
- `lattice/studio/session.py`: `_CHECK_RULES`, buffered checked text, the pre-route, checker ctx injection, the in-turn
  `change_impact` result capture;
- `lattice/studio/server.py`: config parsing, ctx provider, the `POST /api/ask` hooks;
- `lattice/studio/mock_server.py` (parity);
- `lattice/studio/API.md` (v1.26 header and section);
- `lattice/studio/web/js/chat.js` (badge), `css/views.css` (appended rules);
- `dogfood/studio/{REQUIREMENTS.md, spec.py, spec.trace.json}` (R109-R112).

**Reuses read-only:** `ask.build_digest`, `ask.check_citation`, `appmap.build_appmap/touches/element_keys`,
`impact_ask.impact_view/route/_tier_template/NO_PREDICTION/HYPOTHETICAL`, `implementation.routes_in_text/load_inventory`,
`review._build`.

**Not touched (any edit is a finding):**
- `ask.py`, `impact_ask.py`, `appmap.py`, `impact_report*.py`, `implementation.py`, `lattice/mcp/tools.py` (no new MCP tool),
  `check.py`, `importer/`, `gocheck/`;
- existing views, tests, goldens and fixtures;
- the frozen round-1 artifacts;
- `~/Documents/streak`, `job-scheduler*` and the main checkout;
- the question directory.

The builder makes no commits. The orchestrator stages explicit paths only (never `git add -A`). No API key; model use only
through the `claude` CLI, and only in section 7.

## 11. Risks and honest limits

1. **G3(ii) is met by construction.** 0 literal claims shows the checker works, not that the model learned the rule. The
   raw arm measures the model and the prompt; the damage count measures what the checker cost.
2. **The checker's labels use words from the frozen hedge list on purpose** ("static", "not decided"): they are the
   owner's vocabulary for non-claims. The literal scorer cannot tell a correct label from a wrong one. Replacement-only
   (CM6), the item, quote and row renderings taken from the report, map and ReviewData verbatim, and the post-hoc semantic
   audit are the guards. They are not a proof.
3. **D0 trusts a cited id.** A sentence that carries a relevant tier 1 or tier 2 id but overreaches ("t2-... so the whole
   app breaks") is kept. The literal rule has the same blind spot.
4. **Withholding costs content.** A correct sentence that uses a failure word with no named element and no label is
   dropped (D7). The damage count and per-class results show the price.
5. **Routing is lexical.** The new author's wording measures recall; misrouting a C control is a measured precision
   failure. MR6's cue list can misfire on a spec question phrased as "if I ...".
6. **The UX changes in check mode:** text appears per block after its check, not streamed, and routed turns are not in the
   Claude session's memory.
7. **Inherited scorer quirks, kept on purpose:** negations, D's "not decidable" sentence, machine state names and the
   recorded replay word `failed` all count literally unless replaced. The checker handles them; the scorer is not changed.
8. **Recorded replay statuses** are capture-time Spike B observations. Showing them labelled (D5) is ruling Q3; if they
   must be withheld instead, M_workflows status questions lose their content.
9. **Small n, one app, one model, one run:** 64 questions on Streak with sonnet. The 52/64 threshold has a wide interval.
   No second captured Go app exists.
10. **Round-1 answers are dev data.** A checker tuned on them may fit round-1 phrasing; only the new set is blind.
11. **Scratch volatility:** the round-1 frozen artifacts live in `/private/tmp`. If they vanish before step 0, the reuse
    is impossible and the round stops and reports.
12. **Cannot show:** tier 4 (Spike E); chat in explain mode (not measured); other models; chats in languages other than
    English (the regexes are English).

## 12. Questions for the orchestrator

- **Q1 (R1).** Approve the opt-in `[chat]` table (`route`, `check`, default off, byte-compat when absent)? Or should the
  checker be on whenever `[impact]` is present (then PB2 is measured with `[impact]` absent only)?
- **Q2.** Approve the checker as a new module that reuses `ask.py` read-only (`ask.py` byte-unchanged), rather than editing
  `ask.py`'s checker in place?
- **Q3.** May the chat show a workflow's **recorded replay status `failed`** (capture time, labelled by D5, as the App map
  already shows it), or must such sentences be withheld?
- **Q4.** Accept non-streamed text in check mode (no unchecked text ever reaches the UI)?
- **Q5.** Accept that routed session turns are not part of the Claude session's memory? The alternative is a one-line
  context note added to the next model turn's prompt, which is not byte-compat inside a checked session.
- **Q6 (R2).** Dispatch the new question-set author with 6 **new** candidates (recommended: round-1 c1..c6 are public now),
  using the counts in section 6?
- **Q7.** Budget: USD 8 total, gated run first, then ablation, one-shot and 2 observe questions, in that order, while budget
  remains?
- **Q8.** PBR: a browser is connected now. Run the 17-state walk on the real server after the build freeze, with the iframe
  fallback for 375 px?
- **Q9.** API v1.26, or the next free version at build time if another spike takes v1.26 first?
- **Q10.** Confirm **no new MCP tool** this round (an `appmap_get` tool would help M_workflows but would mix effects)?
- **Q11.** Confirm the frozen round-1 scorer stays byte-unchanged, including its hedge list without `not decidable`, and
  that the new thresholds live only in the wrapper?
- **Q12.** Approve the thresholds: G3 >= 52/64, compliance >= 11/12, floors H >= 8/10, N >= 4/6, M_workflows >= 6/8, and
  routed precision >= 95%.

## Amendment 1 (orchestrator rulings on section 12, 2026-10-18, before any code)
Q1: the opt-in `[chat]` table (`route`, `check`, default off, byte-compat when absent) is approved. Q2: the checker is a new module that
reuses `ask.py` read-only; `ask.py` stays byte-unchanged. Q3: the chat MAY show a workflow's recorded capture-time replay status
`failed`, labelled as such, because it is an observed fact (as the App map already shows); an unlabelled or inferred failure is
still withheld. Q4: non-streamed text in check mode is accepted. Q5: routed session turns are not part of the Claude session's
memory (accepted). Q6: the question-set author is dispatched with 6 NEW candidates and the counts of section 6. Q7: the USD 8
budget in the order given (gated run, ablation, one-shot, 2 observe questions) while budget remains; the claude CLI only; a usage
limit is NOT RUN. Q8: the 17-state browser walk runs on the real server after the freeze with the iframe fallback for 375 px, if a
browser is connected; else NOT RUN. Q9: the next free API version at build time. Q10: no new MCP tool. Q11: the frozen round-1 scorer
stays byte-unchanged and the new thresholds live only in the wrapper. Q12: the thresholds are approved (G3 >= 52/64, compliance
>= 11/12, floors H >= 8/10, N >= 4/6, M_workflows >= 6/8, routed precision >= 95%). Step 0 is done: the round-1 frozen files are
copied into `docs/results/data/2026-10-18-goto-chat-round2/frozen-r1/` and their hashes verified against round 1's `freeze.sha256`.
