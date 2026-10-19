# The go-to place, chat round 2: scored stages (RUN node)

2026-10-18, worktree `lattice-fc2`, branch `np-chat-round2`, build 107074b (frozen). Prereg:
`2026-10-18-goto-chat-round2-prereg.md` with Amendments 1-3. Data: `data/2026-10-18-goto-chat-round2/scored/`.

**Headline.** G1 30/30 and G2 16/16 PASS. **G3 FAILS** on two of its six sub-gates, (i) all 64 ok (58 of 64) and
(iv) I verbatim-id compliance (6/12), and both come from one defect in the frozen build: all six `I_free` sessions
crashed with `KeyError: 'tier2'` the moment the model called `change_impact`. The other four sub-gates pass: (ii) 0
literal claims, (iii) correct 52/64 (exactly the threshold, with the 6 crashed questions counted as not correct), (v)
floors, (vi) routed 25/25. The observe arm is NOT RUN (cap). G4 (browser) is not part of this node. The smoke overshoot
(USD 0.392 vs 0.35) was waived by Amendment 3; the first version of this file, written before that ruling, said G3
was NOT RUN: it is superseded by this one.

## 1. Preconditions (all verified before any scoring)

| check | result |
|---|---|
| `freeze-build.sha256` (17 files) via `shasum -a 256 -c` | 17/17 OK |
| `freeze.sha256` (question set, wrapper, selftest, FORMATS2.md, MANIFEST, 6 candidates x 3) | qset.json 012c1aae..., chat2_score.py c51b12ee..., selftest 9d2357d6..., FORMATS2.md 07161d7a..., MANIFEST 25e0addc... all equal; `shasum -c MANIFEST.sha256` no difference |
| step 0, `frozen-r1/` against round 1's freeze | every line OK; `goto_score.py` is d7e5e5aa... |
| wrapper self-test | 97/97 as specified (`scored/step0-selftest.txt`) |
| API key | none in the environment; the runner removes every `ANTHROPIC_*` variable |
| tracked files | none edited; `git status` shows only `scored/` as new here |

## 2. Question-set stage (`dogfood/chat2_probes.py --stage qset`, once)

- All six candidates c1..c6 were recorded in order on a fresh state directory: status `ready`, **byte-equal** to the
  frozen `files_sha256`, and the scorer verified every D report: `c1..c6 verified`, no `candidate_mismatch`, no
  `report_drift`.
- One Studio server per corpus (S = a0679e9 + workflows.json, `[chat] route = true, check = true`; S4 = 1d00ede static),
  one counting stub generator per corpus, the base build c75cb48 for the C controls. No `claude` call (shim log: 0).

**Deviation D1 (environmental, disclosed).** The stage's own call of `chat2_score.py` exited 2 ("round-1 scorer
goto_score.py not found"): the wrapper looks for `../frozen-r1/goto_score.py` next to the question directory, and the
question directory lives in scratch. The stage had already collected `maps.json` and `router.json` and written
`world.json`; only the scoring call failed, so the stage's `qset-scored.json` has `G1: null, G2: null`. I did **not** rerun the stage. I
scored the collected, unchanged answer files once with the same wrapper and `--r1-scorer` pointing at the
hash-checked `frozen-r1/goto_score.py` (the wrapper asserts d7e5e5aa... itself): `scored/qset/score-qset.json`. No
file was edited. The scorer's choice of location is an environment issue, not a metric.

**Deviation D2 (environmental).** `verify_freeze(out.parent)` reads `freeze-build.sha256` next to the parent of `--out`.
To write under `scored/` as asked, I copied the file unchanged to `scored/freeze-build.sha256` (it is the verified
file; the copy is byte-identical).

### Gate table (blind, scored once)

| gate | registered | predicted | observed | result |
|---|---|---|---|---|
| **G1** map API (M + N, 30) | 30/30 exact | 30/30 | **30/30** (M_rules 6/6, M_workflows 8/8, M_machines 4/4, M_access 6/6, N_not_built 6/6) | **PASS** |
| **G2** router `POST /api/ask` (16) | 16/16 exact; 0 literal claims; 0 model calls on routed | 16/16 | **16/16** (I_routed 6/6, H routed 4/4, C_control 6/6); literal claims 0, lenient 0; model calls on routed 0, unknown 0 | **PASS** |
| G3 session, all 64 | six sub-gates | (iii) 52-60 | **58/64 ok, 52/64 correct, 0 claims, I compliance 6/12**: sub-gates (i) and (iv) fail, (ii) (iii) (v) (vi) pass (section 3) | **FAIL** |
| G4 browser | 17/17 states | n/a | not run by this node | NOT RUN here |

The six C controls are byte-identical to the base path (the wrapper checks `base`), and no C control was routed.

**Report-only on `POST /api/ask` (the 12 router-free questions, never gated):** 2/12 correct under the router checks.
- `I_free` 0/6 as a router surface: all six went to the generator (stub) and returned `not_specified`, because F's
  frozen patterns do not route them (by design; the checker and the session path are their real surface). The model is
  the only path for them in one-shot.
- `H_free`: 2 of 6 hit the broad cue (MR6): R2-H5 and R2-H7, both correct. R2-H6, R2-H8, R2-H9 and R2-H10 were not routed
  and reached the stub (1 generator call each). **Predicted 3-6 of 6 hit the broad cue (p): observed 2 of 6, below the
  predicted range.** Misses of the cue wording, stated: R2-H6 "Would existing users run into trouble if ...", R2-H8
  "Is it safe to lower ...", R2-H9 "Picture the batch endpoint accepting ...", R2-H10 "How risky would it be to ...". These
  phrasings are outside the cue list the router froze (suppose, what if, if we, if I, were to, imagine, hypothetically).
  Claims on these 12: 0 literal, 0 lenient (they were `not_specified`).

## 3. Agent stage (`dogfood/chat2_agent.py`), real Studio session HTTP routes, real `claude` CLI (sonnet, 12 turns)

Run 1 stopped at the smoke cap (USD 0.392 > 0.35, registered runner stop). Amendment 3 waived it. Run 2, once, with
`--arms gated,ablation,oneshot,observe --usd-cap 8` (no override of any other stop; the freeze-build check passed again).
The runner's own scoring call failed the same way as in D1 (`score-answers-*.json` empty); I scored the written answer files
with the same wrapper and `--r1-scorer` (D1), once each. Everything in this section is **blind** except where marked post hoc.

### 3.1 Gate G3 (all 64, `scored/agent/score-answers-checked.json`)

| sub-gate | registered | predicted | observed | result |
|---|---|---|---|---|
| (i) all 64 status ok | 64/64 | n/a | **58/64**: R2-I1f..I6f ended `error` (`KeyError: 'tier2'`) | **FAIL** |
| (ii) literal unsupported failure claims | 0 | 0 | **0** (lenient 0) | PASS |
| (iii) correct | >= 52/64 | 52-60, point 56 | **52/64** (the 6 errors count as not correct) | PASS (at the threshold, low edge of the range) |
| (iv) I verbatim-id compliance | >= 11/12 | 12/12 | **6/12**: I_routed 6/6, I_free 0/6 (not run) | **FAIL** |
| (v) floors | H >= 8/10, N >= 4/6, M_workflows >= 6/8 | H 8-10, N 4-6, M_workflows 6-8 | H **10/10**, N **6/6**, M_workflows **6/8** | PASS |
| (vi) routed answers | >= 95% correct, 0 claims | 100% | **25/25** correct, 0 claims, not vacuous | PASS |

**G3 overall: FAIL** (i and iv). Reported as scored; the literal rule and the thresholds are unchanged.

Correct by class (predicted range): M_rules 6/6 (5-6), M_workflows 6/8 (6-8), M_machines 3/4 (3-4), M_access 6/6 (4-6), N 6/6
(4-6), I 6/12 (11-12: **below**; all six misses are the crash), H 10/10 (8-10), C 5/6 (5-6), W_prose 2/4 (2-4), X_absent 2/2 (2).

**The defect (finding, not environmental).** In each `I_free` session the model called `mcp__lattice__change_impact`
(for I1f after `change_get`; 1 to 3 calls) and the session turn ended in `KeyError: 'tier2'` with no text event, so the question
is `error`, not a pass. The tool result in `streams/R2-I3f.jsonl` is a `lattice.impact/1` document with `section: "summary"`,
which carries `tiers`, `summary`, `workflows` and no top-level `tier2` key; `claim_check.TurnContext.add_tool_result`
stores any such document in `turn_reports`, and a later step indexes `report["tier2"]` (candidate frames:
`impact_ask.py:278` or `claim_check.py:460`; the runner records only the exception text, so the frame is **probable, not
captured**). The unit tests and the dev replay used full reports. The same crash ended the ablation of all six `I_routed`
questions (route off, so the model called the tool). A model that calls `change_impact` on a section other than the full
report breaks the checked session path. A fix is a separate post-hoc commit and a rerun reported with deltas; I did not edit or rerun.
The crashed sessions still spent model calls that the ledger does not show (the CLI `result` record was never read), so the
USD figures below are a **lower bound** for the I_free and ablation-I questions.

### 3.2 Every miss, stated (checked arm)

| id | class | what | cause (as scored) |
|---|---|---|---|
| R2-I1f..I6f | I_free | error, no answer | the crash above (6) |
| R2-MW3 | M_workflows | named the 2 correct `after_delete` flows and also mentioned `wf-2818e45412` (an `owner_guard` flow, in prose) | `equal` id set; the model's own sentence, identical in the raw text |
| R2-MW7 | M_workflows | listed 7 ids for "create habits > read progress" (expected 2) | model over-listing; identical in the raw text |
| R2-MM2 | M_machines | needed "4" or "four" states; the model wrote "4 states" and the checker replaced that sentence and the `pending -> failed` line with Lattice's lifecycle text | **damage**: correct in the raw text, incorrect after the check (see 3.3) |
| R2-C2 | C_control | answered "The spec doesn't say" (R141 says the dismiss still answers 200) | the model's miss; the check expects `200`; the answer also holds 3 replaced recorded-replay rows and a withheld footer |
| R2-W3 | W_prose | no `204` and no `DELETE .../completions/{}` for the habit lifecycle walk | content check `any:1` |
| R2-W4 | W_prose | no `DELETE /v1/devices/{}` literal in the walk | content check `all:DELETE /v1/devices/{}` |

### 3.3 Report-only rows

| row | predicted | observed |
|---|---|---|
| lenient count (checked) | 0 | **0** |
| raw arm (same sessions' CLI text, frozen scorer), literal claims | 10-40 | **53** (lenient 29) over the 52 questions that ran; above the range |
| raw arm correct | 38-50/64 | **35/64** (12 not run: 6 I_free + 6 I_routed crashes); below the range. By class: M_rules 6, M_workflows 4/8, M_machines 3/4, M_access 6, N 6, H 1/10, C 5/6, W 2/4, X 2 |
| raw arm claims by class | n/a | H 19 (lenient 8), M_workflows 12 (7), M_machines 8 (8), C 6 (3), M_access 6 (3), W 2 (0), others 0 |
| damage (raw correct, checked incorrect) | 0-2 | **1** (R2-MM2) |
| checker actions over the gated run (sum of `result.check`) | withheld 5-30; replaced 15-60; E1 on 1-6 of 12 I answers; E3 0-4; speculative 0-10; unknown_id 0-3 | kept 710, supported 2, labelled 39, replaced_item 0, replaced_tier3 0, replaced_quote 4, replaced_replay 14, replaced_element 1 (replaced total 19), **withheld 9**, appended 1 (E1 and E3 together), speculative_hedge 0, unknown_id 0. Only the 6 I_routed answers ran as I answers, and they were router answers. |
| session routing rate (25 of 64 routed) | M+N 10-21 of 30; H_free 3-6 of 6; C 0 | M+N **12** of 30; H_free **2** of 6 (below; same two as on `/api/ask`: H5, H7); C **0** of 6; also I_routed 6, H routed 4, X_absent 1 (R2-X2) |
| cost, gated arm (CLI list estimate) | USD 1.8-3.5 (point 2.5) | **USD 3.36** (within range, high end; lower bound, see 3.1); with the smoke USD 3.75 |
| cost per correct answer | n/a | USD 0.0646 (3.359/52); USD 0.0721 with the smoke |
| tokens, turns, wall | n/a | all arms in `agent-usage.json`: input 228, output 32,604, cache read 8,552,820, cache creation 544,399; 212 turns; 464 s of model time |
| partial text events in check mode | 0 | 0 |

**Semantic audit (post hoc, manual, labelled).** I read the checked text of R2-C2, R2-MW3 and R2-MM2 only: the replay rows show the map's
capture-time status and say "what any change does to it is not decided". The full audit of every kept or replaced unit that
contains a failure word is **not done** (710 kept, 19 replaced, 9 withheld units). The quirk the damage row
exposes is the one the prereg named (risk 4): a failure word inside a model sentence replaces more than the word.

### 3.4 Report-only arms

| arm | asked | ok | correct | literal claims | note |
|---|---|---|---|---|---|
| **ablation** (the 25 routed questions, `route = false`, model plus checker) | 25 | 19 | **16/19** (the 6 I_routed crashed) | 0 | misses: R2-MW1, R2-MW4 (id sets), R2-MM1. Routed correct 25/25 vs model plus checker 16/19: the router is what lifts those classes. |
| **one-shot** (`POST /api/ask`, real CLI generator, `check = true`) | 29 of the 39 non-routed questions | 29 | **26/29** | 0 | stopped by the cap before R2-C2 (10 not asked: C2-C6, W1-W4, X1). Misses: R2-N3, R2-H6, R2-H9 (no "not decided" or offer to draft). I_free 6/6 correct here (the one-shot path has no crash). |
| **observe** (2 change-mode, tier 1, Docker) | 0 | 0 | n/a | n/a | **NOT RUN**: cap rule, USD 7.53 + 1.5 x 0.367 > 8. No container was started. |

Spend by arm: smoke 0.392, gated 3.359, ablation 1.528, one-shot 2.249; total **USD 7.528 of 8.00** (CLI list estimate).

## 4. Other disclosures

- **Builder's accidental `claude` CLI use.** The orchestrator's brief says the builder disclosed one; its details and cost were not
  given to this node and are not in the repo data I read. It is not in this node's ledger.
- **Containers.** Neither stage started a container labelled `fc2-*` (observe, the only Docker arm, did not run). The containers
  present (`lattice-mysql-5f332b59` owner `w-gr`, `g5-sg-author-mysql`) belong to other runs and were left alone. No Studio or runner process is left.
- **No key.** `ANTHROPIC_*` unset; the CLI only.
- **Streak:** read only through `git archive`; its pre-existing ` M studio.toml` was not touched.
- **Blind vs post hoc.** All numbers above are blind and scored once. Not blind: the cause paragraph of 3.1 (read from the saved
  tool result and the source after scoring) and the semantic audit. The fix and a rerun are not done.

## 5. Files created

- `docs/results/2026-10-18-goto-chat-round2-scored-results.md` (this file)
- `docs/results/data/2026-10-18-goto-chat-round2/scored/step0-selftest.txt`, `scored/freeze-build.sha256` (byte copy, D2)
- `scored/qset/{maps.json, router.json, world.json, qset-scored.json, score-qset.json, score-qset.stderr}`
- `scored/agent/{agent-usage.json, ledger.json, answers-checked.json, answers-raw.json, answers-ablation.json, answers-oneshot.json, score-answers-checked.json, score-answers-checked.stderr, score-answers-ablation.json, score-answers-oneshot.json, streams/*.jsonl}`

## Orchestrator ruling
The blind result stands as scored: **G1 30/30 pass, G2 16/16 pass, G3 FAILS** on sub-gates (i) (58 of 64 sessions ok) and (iv) (verbatim-id compliance 6 of 12): all six
`I_free` sessions ended in a crash (`KeyError: 'tier2'`) after the model called `change_impact` and the tool result carried `section: "summary"`; the same crash ended
all six `I_routed` questions in the ablation arm. Sub-gates (ii) 0 literal claims, (iii) 52 of 64 (exactly the threshold), (v) floors and (vi) routed precision pass.
The raw arm shows what the checker bought: 53 literal claims and 35 of 64 correct without it, against 0 claims and 52 of 64 with it (one damage case, R2-MM2). The crash is a
defect in the frozen build: a fix is a separate **post hoc** commit and is never scored as blind. The registered USD 8.00 is spent (7.528): a rerun of the failing
questions needs a new approved budget and is left for the owner. G4 (the real-browser walk) is run by the orchestrator separately.
