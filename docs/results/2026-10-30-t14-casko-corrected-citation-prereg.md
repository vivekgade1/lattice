# T1-4: real-model C-ASK on the Streak Go spec with the corrected citation definition: pre-registration

**Status.** Written and committed before the driver and before any model call. Node T14-prep of the 2026-10-30 agent graph, branch `nc/t14-prep`
(cut from the integration tip 066bbc8). This node makes **no** model call; a later node runs the single paid run after the rebuild chain has merged.
The driver (`dogfood/t14_casko_run.py`, committed after this file) refuses to call the model unless this file, the question set and every runner file are
committed and unchanged, and `--confirm-spend` is passed (section 7).

**Data status: development data, exploratory.** The corrected citation definition (section 4) was written *after* seeing the .NET C-ASK result
(docs/results/2026-10-23-dotnet-real-agent-results.md section 4: A-CITED failed by the registered definition because route citations of the form
`impl:route:...` were not counted; post hoc count 0 uncited), so it is a post hoc definition. The Streak spec is dev data (owner-written, used by earlier rounds,
including that .NET C-ASK). The 12 questions are new, but they were written by the same agent that wrote the definition, with the spec digest, the trace and
the Go source open. n = 12 (or fewer, section 6) answers from one run: nothing here backs a blind or general claim; misses are reported, not tuned.

## 1. What this measures

Row 13 of the parity matrix (chat grounded in spec and code) on a **Go** target: a real model (`sonnet` through the `claude` CLI) answers questions about Streak
through the library's ask path, with the implemented app's inventory in the digest, and every claim the answer makes about a route or a rule must carry a citation
that resolves. It repeats the .NET C-ASK with the definition corrected, more questions (12 instead of 3), the target's own Go code (not D1's C#), and the raw-vs-checked
comparison of chat round 2 (docs/results/2026-10-18-goto-chat-round2-scored-results.md: raw model text 53 literal unsupported failure claims, 0 after the claim checker).

## 2. Target (read only; copied by `git archive`)

- `~/Documents/streak` at **a0679e9** (branch `build/streak`: the p2 build of the Go app, 42 `.go` files, merged with main's spec fixes; the same rev as the .NET C-ASK's spec).
  The driver `git archive`s the whole tree into a scratch directory; nothing is written into the repository.
- Pinned hashes (sha256) at a0679e9: `spec.py` 28dfc2e6c6a2aa27..., `spec.trace.json` 627a2b3f4c9cfafc..., `REQUIREMENTS.md` dcac6deb3ad3ec4a..., `CONTEXT.md` 61b8040f8a88f2a5...
  The driver records them in the freeze file and refuses the run if they differ.
- Inventory: `python -m lattice.importer inventory <app> --out <scratch>/inv --package .` (static extract, no observed workflows; captured during authoring:
  44 source files, extract sha256 d51ef75340d54682..., 26 `impl:route` elements, 148 `impl:rule` elements, 0 `spec_only` endpoints).
- Digest: `lattice.spec.ask.build_digest(spec, trace, inventory)`: 156 scenarios, 148 rules, 66 cells, 15 invariants, 13 unchecked notes, 9 tables, 1 machine,
  1 service, plus the impl section. The core sent with every question is about 25.6k estimated tokens (`approx_tokens`).

## 3. The path (as Studio's `POST /api/ask` runs it, in process)

`lattice.spec.ask.ask(spec, question, trace=..., generator=ClaudeCliClient(...), model="sonnet", inventory=inv)` (its citation check, its optional "need more" round and
its single repair round are the library's), then `lattice.spec.claim_check.check_answer(answer, CheckContext(question, appmap=appmap.build(spec, trace, inv), digest=digest))`
(`lattice/studio/server.py` `Server.ask`, API v1.26). One difference from Studio, kept from the .NET C-ASK for comparability: the claim checker's digest is the one with
the impl section (Studio's `chat_context` builds it without). The model-free router `chat_route.route` is not applied; whether it would have routed each question is recorded.

Model: `sonnet` via `claude -p` only (`ClaudeCliClient`: no tools, no MCP, empty cwd, effort `high`, one transient retry, no refusal retry), every call through the shim
(section 7). `ANTHROPIC_*` variables are stripped (`env -u ANTHROPIC_API_KEY` at launch, and again by the driver and the shim). No API key, ever.

## 4. The corrected citation definition (registered)

### 4.1 The citation target kinds the library accepts (derived from the code at 066bbc8)

`claim_check.py` does not accept or reject citations itself: the answer's citations are checked by `lattice.spec.ask.check_citation` (ask.py:348), which the claim checker
also calls for its rule quotes (claim_check.py:973). A citation holds iff its target is a key of `Digest.elements` (or a `code:` target with an inventory) and its quote matches.
The keys are built by `build_digest` (ask.py:212), `_impl_elements` (ask.py:498) and `_with_notes` (ask.py:665):

| kind | target form | built from | quote rule |
|---|---|---|---|
| spec | `profile:<Table>` | the spec's tables | normalised substring |
| spec | `cell:<caller>\|<METHOD /template>` | the access matrix | the quoted status set equals the cell's |
| spec | `scenario:<name>` (optional `step`) | scenarios | step: method, path, status (caller); else substring |
| spec | `invariant:<name>`, `machine:<table>.<column>`, `service:<name>`, `unchecked:<n>` | the spec | substring (`machine`: quoted `a -> b` moves must be edges) |
| rule | `rule:<Rn>` | the trace (only with a trace) | substring |
| impl (inventory) | `impl:route:<METHOD /template>`, `impl:rule:<Rn>`, `impl:machine:<key>`, `impl:flow:<wf-id>` | the inventory (only with one) | substring (`impl:flow`: the quoted status equals the flow's) |
| code (inventory) | `code:<relative path>:<line>` | files under the app root (`implementation.check_code`, implementation.py:1386) | >= 8 non-space characters on that one line |
| notes | `rationale:<Rn>`, `term:<slug>` | approved notes (only with notes files) | substring; **never grounds** a yes / no / value answer (ask.py:831) |

There is **no** `spec:` target kind: a literal `spec:...` target is rejected ("no element ... in the spec"). Below, "spec target" means the spec kinds of this table.
The claim checker additionally recognises, inside the answer's *text*, rule ids (`RULE_RE`, claim_check.py:92: `R<n>` or `rule:R<n>`, not preceded by a word character or `:`),
impact item ids `t1-`..`t4-` (claim_check.py:89) and workflow ids `wf-` (claim_check.py:91); a unit naming an id Lattice did not mint is withheld (E2), and a unit stating a
failure without support is replaced or withheld (D0-D7).

### 4.2 Claims

The **claims** of an answer are counted in its text (`answer` + `explanation`):

- a **route claim** is every `METHOD /path` that `lattice.spec.implementation.routes_in_text` finds, keyed by `route_key` (path parameters become `{}`).
  It is a *known* route claim when `impl:route:<key>` is an element of the digest (a route of the code, or a `spec_only` endpoint) or a `cell:<caller>|<key>` exists;
  otherwise it is an **unknown route mention** (counted and listed apart, not gated: e.g. the trap route of Q08).
- a **rule claim** is every rule id that `claim_check.RULE_RE` finds. It is *known* when `rule:<Rn>` is a digest element; otherwise an **unknown rule mention** (listed apart).
- Bare paths without a method (`/v1/sync`) and prose descriptions of a rule without its id are **not** counted (a stated limit of this definition).
- In the checked answer, a known claim that does not occur in the raw text of the same answer (section 5) is **Lattice-inserted** (the claim checker's own replacement text)
  and is reported apart, not gated.

### 4.3 Resolving and binding

A citation **resolves** when (existence only; the library's quote check is separate):
`impl:route:`/`impl:rule:`/`impl:machine:`/`impl:flow:` targets are digest elements of the matching `impl_*` kind (i.e. built from the inventory); spec and `rule:` targets are
digest elements of their kind; a `code:<path>:<line>` target parses (`parse_code_target`), names a file under the app root without `..`, and the line exists in it.
`rationale:`/`term:` targets and any other prefix (including `spec:`) never resolve for this definition.

A resolving citation **binds** a claim when:

| claim | binding citations |
|---|---|
| route `K` | `impl:route:K`; `cell:<any caller>\|K`; `scenario:<s>` with a step whose `route_key(method, path)` is `K` (with `step` given: that step); `rule:Rn` or `impl:rule:Rn` where the inventory's requirement for `Rn` needs `K` (`ImplContext.target_endpoints`) or the rule's text names `K`; `impl:flow:` whose steps name `K`; `code:<path>:<line>` where `<path>` is the file of `K`'s registration or handler in the inventory; any other spec element whose text names `K` |
| rule `Rn` | `rule:Rn`; `impl:rule:Rn`; `unchecked:<n>` whose text starts with `Rn` (e.g. "R6: ...") |

A claim is **cited** iff at least one citation **accepted by the library** (the answer's `citations`, not `rejected_citations`) resolves and binds it.

## 5. Arms: raw model text vs checked answer (as in chat round 2)

- **raw**: the model's *first* answer reply of each question (after the optional "need more" round, before any repair round), parsed with `ask._parse_answer`; its text and
  all its citations as written. Here "cited" means a citation as written resolves and binds (the library's acceptance is reported apart). If the reply does not parse,
  the raw text is the reply verbatim with no citations.
- **checked**: the answer after `ask()` and `claim_check.check_answer` (the `POST /api/ask` output): accepted citations only.

Both arms are audited with section 4, counted for chat round 2's literal unsupported failure claims (`claim_check.scorer_claims(text, set())`), and scored against the key
(section 6). Damage = raw correct and checked incorrect.

## 6. Questions, key and scoring

The 12 questions, the key and the scripted dry-run replies are in `dogfood/t14_casko_questions.json` (committed with this file), asked in this order:

| id | category | question | key (abridged; the evidence is the gold reply's citations) |
|---|---|---|---|
| Q01 | routes | Which API routes does the app have for check-ins? | `POST /v1/habits/{}/completions`, `GET /v1/habits/{}/completions`, `DELETE /v1/habits/{}/completions/{}`, `POST /v1/completions/batch` (R72, R81-R83; internal/completions/routes.go:25-28) |
| Q02 | trap (false premise) | Which admin role is allowed to delete another user's habits? | none: no roles or admins (R27); another user's habit answers 404 (R28) |
| Q03 | rule | What does the spec require when a check-in date is more than 60 days before the user's today? | 422 `date_too_old`, `t - 60` accepted (R75); precedence after future / out of range (R76) |
| Q04 | not implemented | Is there an endpoint to reset a forgotten password, and does the code implement one? | no: neither the spec nor the code (26 inventory routes) has one; label `not_specified_not_implemented` |
| Q05 | where in the code | Where in the Go code is the limit of 120 requests per minute per signed-in user enforced? | internal/httpapi/ratelimit.go:13 (`RateLimit = 120`), :55 (`RateLimited`), applied at internal/httpapi/router.go:54; the digest does not show these lines, so `not_specified` is also scored correct (`abstain_ok`) |
| Q06 | who can call | Who can delete a check-in, and what does another signed-in user get if they try? | only the owner; another user 404 `not_found`; no token 401 (R27, R28, R21, R82; cells) |
| Q07 | not specified | What is the maximum number of devices one user may register? | not specified; only one row per `push_token` (R32) |
| Q08 | trap (false premise) | What does DELETE /v1/nudges/{id} answer when the nudge was already dismissed? | there is no such route; dismissal is `POST /v1/nudges/{id}/dismiss`, already dismissed 409 `nudge_dismissed` (R138) |
| Q09 | rule | What happens when a client presents a refresh token that was already rotated? | 401 `refresh_token_reused`, revokes every refresh token of that user and installation (R20, R7) |
| Q10 | where in the code | In which file and line is the route that dismisses a nudge registered, and where is its handler? | internal/nudges/routes.go:17 (registration), :57 (handler `dismiss`) |
| Q11 | who can call | Can a client without an access token read GET /v1/sync? | no: 401 `not_authenticated` (cell, R21) |
| Q12 | routes | Which routes can a client call without an access token? | `GET /health`, register, login, refresh (R3, R21); `PUT /test/clock` is test-mode only |

The order interleaves the categories so that a run the cap truncates (section 8) still covers most of them.

**A-CORRECT scoring (mechanical, reported, not gated).** An answer is correct iff its `kind` is in `key.kinds` and (when `key.abstain_ok` and the kind is `not_specified`:
nothing more) every group of `key.must` has a regex matching the haystack (answer + explanation + the arm's citation targets and quotes; case-insensitive), the route keys of
`key.routes_required` are all among the route keys of the text, and no `key.must_not` regex matches the text. The written key's `claims` list is the human-readable truth;
a manual reading of every answer is reported next to the mechanical score, labelled as such.

## 7. Spend controls

- **Cap: USD 0.60 total, hard.** One run only. Every `claude` invocation goes through `dogfood/t14_casko_shim.py` (it runs `dn_real_agent_shim.main` unchanged with this run's
  margin): ANTHROPIC_* stripped; `--max-budget-usd min(remaining, 0.30)` per call; one append-only ledger line per invocation (`ledger.jsonl`, the CLI's `total_cost_usd`);
  a call is refused without a model call when `0.60 - 0.07 - spent < 0.01` (margin 0.07 = 1.5 x the largest later-call cost observed in the .NET C-ASK, because the CLI
  checks its budget only between model calls, docs/results/2026-10-30-preflights.md P1, and an ask call is one model call). The driver starts a question only when
  `0.60 - spent >= 0.12` (two later calls and the margin). Worst case total: 0.52 + one call <= about 0.59.
- **Guard** (`guard()`): refuses unless `--confirm-spend` is given, this file, the question set, the driver, the audit module, both shim files and the fake model are committed and
  unchanged (`git status --porcelain` empty for each), this file is in a commit that is an ancestor of HEAD and older than the driver's first commit, the Streak hashes match
  section 2, no `ask-result.json` exists in the output directory, and the ledger holds no model call (a relaunch is allowed only after a crash before the first model call).
  The freeze (`ask-freeze.json`) records HEAD, every file hash and `claude --version`.
- `--dry-run` (no model, spend 0): the same driver, shim and ledger with a fake `claude` binary that answers from the gold replies (`gold`) or with wrong citations (`wrongcite`).

## 8. Gates

| id | what | pass | status |
|---|---|---|---|
| **A-ANSWERED** | every question answered by the model (a parsed reply; a library downgrade to `not_specified` still counts as answered) | 12 of 12 | **gated** |
| **A-CITED** | checked arm, section 4: known route and rule claims without a resolving, binding, accepted citation | **0** | **gated** |
| A-REJECTED | citations the library rejected (`rejected_citations`, after the repair round), and the raw arm's citations that would be rejected | count | reported |
| A-CORRECT | share correct against the key, checked arm and raw arm; damage | share | reported, not gated |
| A-RAW | the raw arm's uncited known claims; literal failure claims raw vs checked | counts | reported |
| A-UNKNOWN | unknown route and rule mentions, Lattice-inserted claims, answers downgraded to `not_specified` by the library | lists | reported |
| A-COST | ledger total | <= USD 0.60 | gated |

An answer the library downgraded to `not_specified` has no claims left, so A-CITED is vacuous for it; the downgrades are counted (A-UNKNOWN) so that a pass made of
downgrades is visible.

## 9. Budget feasibility and predictions (stated before the run)

**The cap is expected to bind.** The .NET C-ASK (same Streak spec, sonnet, effort high) cost USD 0.160 for its first call (cache write of the ~31.7k-token core) and
0.040-0.046 for each later call; its questions took 1-2 calls (mean 1.67). This digest's core is about the same size (25.6k estimated tokens here vs about 30k there).
Predicted cost of 12 questions: 0.16 + (12 x 1.5 - 1) x 0.045 = about **0.93** (range 0.65-1.10), above the USD 0.60 cap. With the stop rules of section 7 the run is
predicted to answer **6 of 12** questions (range 5-8: starts at spent about 0.18, 0.25, 0.32, 0.38, 0.45; no start above 0.48); the rest are NOT RUN (reported as such, never re-run). A-ANSWERED is registered as asked (12 of 12) and is therefore
predicted to **fail**: p(pass) = 0.05 at USD 0.60. A cap of about USD 1.10 would be needed for p(pass) about 0.9. Raising the cap or cutting questions is the owner's call;
it must be a committed amendment to this file *before* the first model call (the guard hashes this file), never after.

| gate | prediction | p |
|---|---|---|
| A-ANSWERED | fail at 0.60 (6 of 12 answered, range 5-8) | pass 0.05 |
| A-CITED | pass: 0 uncited known claims among the answered questions | 0.7 (risk: a route named in the explanation with only a nearby citation, e.g. Q12's `PUT /test/clock`, Q04's auth list) |
| A-REJECTED | 0-2 rejected citations in the checked arm; raw arm 0-4 | 0.7 |
| A-CORRECT | checked arm 75% of answered (range 55-90%); likeliest misses Q02 / Q08 (traps answered `not_specified`) and Q05 (guessing a file the digest does not show) | n/a |
| A-RAW | raw arm uncited known claims 0-3; literal failure claims raw 0-2, checked 0 | 0.6 |
| A-COST | <= 0.60 | 0.97 |

## 10. Outputs

`docs/results/data/2026-10-30-t14/`: `ask-freeze.json`, `ledger.jsonl`, `ask-raw/call-NNN.{out,err}` (the CLI's raw output of every call), `ask-result.json` (per question:
both arms' text, citations, rejected citations, audit, score, usage; the verdicts and counts). Dry runs write under `docs/results/data/2026-10-30-t14/dry-run/<variant>/`.
The results document is written by the node that runs it, and states the post hoc definition and the data status of this file's header.
