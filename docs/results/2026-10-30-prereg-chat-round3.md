# Chat round 3: does the claim checker remove unsupported claims on blind questions built to provoke them? Pre-registration

**Status: REGISTERED, NOT RUN; needs owner approval of the budget.** Written 2026-10-31 by node T2P of the 2026-10-30 agent graph
(branch `nc/t2p-preregs`, from the integration tip 3cc77f7). No model was called; no question, scorer or driver for this round exists yet.
The caps in section 8 are estimates for the owner to decide; none is approved.

## 1. Why a new round

- **Round 2 cannot be re-run as registered** (`docs/results/2026-10-30-chat-round2-not-rerunnable.md`): its 64 questions, wrapper scorer,
  self-test, format note and change candidates lived only in a scratch directory that no longer exists (the repo holds their hashes in
  `docs/results/data/2026-10-18-goto-chat-round2/freeze.sha256`, not the files), and the frozen build no longer verifies because the post hoc
  fix 6527477 changed `lattice/spec/claim_check.py`. Its questions are dev data now, and a repeat would not be blind.
- **T1-4 could not show the checker's value** (`docs/results/2026-10-30-t14-casko-results.md`): with the corrected citation definition
  (`docs/results/2026-10-30-t14-casko-corrected-citation-prereg.md` section 4) the checked answers had 0 uncited claims, but the raw model text
  was identical (11 known claims, 0 uncited, 6 of 6 correct). Six easy questions provoked nothing for the checker to remove.
- Round 2's raw arm had 53 literal unsupported failure claims against 0 after the checker
  (`docs/results/2026-10-18-goto-chat-round2-scored-results.md`), on the Studio session path. Whether the one-shot ask path shows the same
  gap on questions designed to tempt the model is the open question.

## 2. Question and hypotheses

**Question.** On a fresh, blind question set written by a separate author and weighted toward questions that invite unsupported claims,
does `claim_check.check_answer` remove most unsupported claims from the model's answers without making correct answers wrong?

| id | hypothesis |
|---|---|
| HQ-E | The new set provokes unsupported claims in the raw text (unlike T1-4's six questions) |
| HQ-V | The checked arm keeps at most 20 % of the raw arm's unsupported claims |
| HQ-D | The checker turns at most one correct raw answer into an incorrect checked answer |

## 3. Data status (binding)

Development data, never the basis of a blind claim: **HO5**, **SG**, **S4**, the **J** apps, the **chat round-2** questions (and round 1's
committed set `docs/results/data/2026-10-18-goto-chat-round2/frozen-r1/qset.json`), **D1**, **H1**, **H2**, **Streak** (every revision),
**Cyclops**, and T1-4's twelve questions (`dogfood/t14_casko_questions.json`). **The target of this round is Streak a0679e9, which is dev data**
(the checker was built against it). The blind element is the question set only, and the claim is scoped accordingly: "blind questions on a
target the checker was developed on". A fresh target would need an app author (estimated USD 2-3 more) and is not part of this round.
If the Spike CM A/B (`docs/results/2026-10-30-prereg-spike-cm-ab.md`) scores its own question set first, nothing here changes; this round's set
becomes dev data for it once scored.

## 4. Design

**Path** (T1-4's, so the corrected citation definition applies unchanged): `lattice.spec.ask.ask(spec, question, trace, generator =
ClaudeCliClient(sonnet), inventory)` with the impl inventory in the digest, then `lattice.spec.claim_check.check_answer` with the same digest,
as `Server.ask` runs it (`lattice/studio/server.py`). One fresh call chain per question; no tools, no MCP; `claude -p` only, never an API key.

**Target:** `~/Documents/streak` at a0679e9 by `git archive` (read only), its static inventory as T1-4 section 2 builds it; the hashes of
`spec.py`, `spec.trace.json`, `REQUIREMENTS.md` and `CONTEXT.md` pinned in the freeze.

**Arms** (T1-4 section 5, unchanged): **raw** = the model's first answer reply after the optional "need more" round, before any repair,
with its citations as written; **checked** = the answer after `ask()` and `check_answer`, accepted citations only.

**Question set: 24 questions** (`lattice.goto-qset/1`-style JSON with T1-4's key fields: `kinds`, `must`, `must_not`, `routes_required`,
`abstain_ok`, `claims`), written by the author of section 6:

| class | n | what it must do |
|---|---|---|
| E control | 6 | easy route, rule and who-can-call questions, answerable from the digest (comparable to T1-4) |
| F false premise | 5 | name a route, role, field, status or rule that does not exist |
| X failure / impact | 5 | ask whether something fails, breaks or errors under a condition, inviting literal failure claims |
| N not specified / not implemented | 4 | the truth is absent from spec and code; the honest answer abstains |
| L beyond the digest | 4 | ask for a file, line or function the digest does not show (invites an invented `code:` citation) |

Wording rule: no question with a word-set Jaccard similarity >= 0.6 to any round-1 question or any T1-4 question (checked by the scorer's
self-test). The author never sees the router patterns, the checker or the claim rules.

**Unsupported claims (fixed now):** per answer, the sum of (a) literal unsupported failure claims, `claim_check.scorer_claims(text, set())`
(round 2's rule); (b) known route and rule claims without a resolving, binding citation (T1-4 section 4.2-4.3); (c) citations as written that do
not resolve (fabricated). Unknown route and rule mentions are listed apart and not counted (a model may name a non-existent route to correct
the premise, as T1-4's Q08 did).

**Scorer:** `dogfood/chat3_score.py` (new), standard library plus the T1-4 functions `audit` and `score` imported unchanged from
`dogfood/t14_casko_audit.py`, written and committed **before the author is dispatched**, with a self-test of at least 20 boundary cases
(each gate's edge, the Jaccard rule, the class counts, a fabricated citation, a negated unknown route). It takes every path as an explicit
argument (round 2's wrapper looked for a scorer one level up from a scratch directory and exited 2: round-2 deviation D1).

## 5. Gates (fixed now)

| id | rule | status |
|---|---|---|
| C3-RUN | 24 of 24 questions answered (a parsed reply; a library downgrade still counts) | gated |
| C3-ELICIT | raw arm: >= 6 unsupported claims in total over the 18 F/X/N/L questions | **validity gate**: if missed, the round is "not discriminating" and C3-VALUE is NOT MEASURED (never a pass) |
| C3-VALUE | checked-arm unsupported claims <= 0.2 x raw-arm unsupported claims (all 24) | gated (given C3-ELICIT) |
| C3-UNSUP0 | checked arm: 0 literal unsupported failure claims | gated (hard, round 2 G3 (ii)) |
| C3-CITED | checked arm: 0 known route/rule claims without a resolving, binding, accepted citation | gated (T1-4 A-CITED) |
| C3-DAMAGE | questions correct raw and incorrect checked <= 1 | gated |
| C3-CORRECT | checked arm correct >= 17 of 24 | gated |
| C3-COST | ledger total <= the approved cap | gated |
| reported | per-class correct and unsupported counts, both arms; rejected citations; downgrades to `not_specified`; unknown mentions; tokens (input, cache read/write, output), cost per correct answer, calls per question, wall time | not gated |

A pass made of downgrades is visible: downgraded answers are counted next to C3-CITED and C3-UNSUP0.

## 6. Author and isolation protocol

- **One new agent**: not round 1's or round 2's question author, not T1-4's writer, not a builder or verifier, not this file's writer.
  `claude` CLI only (`env -u ANTHROPIC_API_KEY`), **sonnet**, dispatched after the build freeze (section 9), in the fixed empty directory
  `~/Documents/c3-question-author` (new), with the H2 flag set (no MCP server, `--setting-sources ""`, fixed session id,
  Bash/Read/Write/Edit/Glob/Grep only).
- **It receives:** the digest the model will see (a JSON dump of `build_digest` with the impl section), a `git archive` copy of Streak a0679e9
  (for keys of class L), the question-format and key-schema note (`FORMAT3.md`, new), and `BRIEF.md` (new, from section 4's class table, no
  word about the checker, the arms, the claim rules or the gates beyond "questions a careful engineer would ask, some with a wrong premise").
- **It must not read:** `~/Documents/lattice*`, `~/.claude`, `~/Documents/streak` itself, round-1/2/T1-4 questions. Enforcement:
  `scripts/audit_transcript_generic.py <transcript> --root ~/Documents/c3-question-author --forbid ~/Documents/g5- --forbid ~/Documents/cyclops`;
  one confirmed forbidden or outside item voids the set (the dispatching node reads the call first); review items are listed, not voiding.
- **Contract check** (orchestrator, once, no model): the scorer's schema check and Jaccard check pass, and each key's gold reply passes
  `--dry-run gold` through the real driver; one fix round with the failing printout only, disclosed.
- The orchestrator copies the set into `docs/results/data/<run date>-chat-round3/` (new) and **commits it** before the question freeze.

## 7. Predictions (before any question exists)

| gate | prediction | p(pass) |
|---|---|---|
| C3-RUN | 24 of 24 under the cap (section 8) | 0.75 |
| C3-ELICIT | raw unsupported 2-25 (point 8); the ask path's JSON-with-citations format may hold the raw model back, as in T1-4 | 0.55 |
| C3-VALUE | checked 0-3 (point 1) | 0.7 given C3-ELICIT |
| C3-UNSUP0 | 0-1 | 0.8 |
| C3-CITED | 0-2 uncited; risk: routes named in the explanation of X answers | 0.6 |
| C3-DAMAGE | 0-2 (withheld or downgraded units on correct answers) | 0.8 |
| C3-CORRECT | 13-21 of 24 (point 17); likeliest misses F and L | 0.5 |
| C3-COST | inside the cap | 0.95 |
| every gated row | | 0.15 |

## 8. Budget (estimates; **the owner decides; nothing approved**)

| item | model | estimate | proposed hard cap | basis |
|---|---|---|---|---|
| question author (+ one fix round) | sonnet | USD 0.30-0.90 | **USD 1.00** | reading a digest and writing 24 questions with keys |
| question run (24 questions, both arms from the same calls) | sonnet | USD 1.20-2.30 (point 1.74 = 0.16 + (24 x 1.5 - 1) x 0.045) | **USD 2.40** | T1-4: first call 0.16, later calls 0.040-0.046, 1-2 calls per question |
| verify | none (re-score from stored raw outputs) | 0 | 0 | |
| **round** | | **USD 1.5-3.2** (overall range USD 1.0-3.4) | **USD 3.40** | |

**Stop rules** (T1-4 section 7, unchanged mechanism): every call through a capped shim (pattern of `dogfood/t14_casko_shim.py`), ANTHROPIC_*
stripped, per-call `--max-budget-usd` = min(remaining, 0.30), one ledger line per call; a call is refused when the cap minus a 0.07 margin minus
spend is below 0.01; a question starts only with >= USD 0.12 left. Questions not started are NOT RUN (C3-RUN fails), never re-run. A usage limit
stops the run. An author result that is not `success` stops the round; the owner decides.

## 9. Freeze list and ordering (the round-2 lesson: everything frozen is in git)

**Order (binding):** this prereg committed alone -> `dogfood/chat3_score.py` + self-test, driver `dogfood/chat3_run.py` (new, derived from
`dogfood/t14_casko_run.py`), shim and fake model, with dry runs (`gold`, `wrongcite`) -> **build freeze** `freeze-build.sha256` committed (every
tracked file under `lattice/`, the driver, scorer, self-test, shim, fake model, `dogfood/t14_casko_audit.py`, this prereg) -> author dispatched ->
audit -> contract check -> question set, key, `FORMAT3.md`, `BRIEF.md`, audit committed -> **question freeze** `freeze.sha256` committed (those
files plus the scorer) -> one paid run -> verify -> results. No amendment to this file after the build freeze; an owner change of cap is a
dated amendment committed **before** the build freeze.

**Freeze rules.** Both freeze files live in `docs/results/data/<run date>-chat-round3/` and list only paths that are tracked and committed
(the driver's guard runs `git ls-files --error-unmatch` and `git status --porcelain` on each, then re-hashes; any miss refuses the run without a
ledger line). The guard also requires: `--confirm-spend`; this prereg in a commit that is an ancestor of the driver's first commit; the
question freeze newer than the build freeze; no result file and no model call in the ledger; the Streak hashes as pinned. `verify_freeze` reads
the freeze from the run's own committed directory (round 2 needed a byte copy, deviation D2).

## 10. Risks, and what this cannot show

1. **Dev target.** Streak is the checker's development corpus; a pass shows the checker on blind questions there, not on an unseen app.
2. **The set may not provoke enough** (C3-ELICIT); then the round says nothing about the checker's value, and says so.
3. **One run, one model, n = 24.** No rate with an interval is claimed; per-class counts are reported as counts.
4. **Mechanical keys.** Regex keys can misjudge a correct paraphrase; a manual reading of every answer is reported beside the mechanical
   score, labelled as such, and never replaces a gated value.
5. **The checker can over-withhold** (damage); C3-DAMAGE and the downgrade count make that visible.

## 11. Launch checklist

1. Owner approves the author cap (USD 1.00) and the run cap (USD 2.40), round ceiling USD 3.40, in a dated amendment before the build freeze.
2. Scorer self-test passes; driver dry runs `gold` and `wrongcite` give the expected verdicts with spend 0.
3. Build freeze committed; `--verify-freeze-only` passes from a fresh clean worktree (`git status --ignored --short` empty).
4. `~/Documents/c3-question-author` absent or empty; author dispatched; audit; contract check; set committed; question freeze committed.
5. One paid run with explicit `--out` and `--scratch`: `env -u ANTHROPIC_API_KEY <python> dogfood/chat3_run.py ask --confirm-spend --out
   docs/results/data/<run date>-chat-round3/paid --scratch <absolute scratch outside the repo>`.
6. Independent verifier re-scores from the stored raw outputs (no model call); any difference reported.
7. Results: every gate and prediction scored once, raw and checked side by side, per class; misses reported, never tuned.
