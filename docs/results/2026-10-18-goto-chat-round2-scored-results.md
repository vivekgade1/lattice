# The go-to place, chat round 2: scored stages (RUN node)

2026-10-18, worktree `lattice-fc2`, branch `np-chat-round2`, build 107074b (frozen). Prereg:
`2026-10-18-goto-chat-round2-prereg.md` with Amendments 1-2. Data: `data/2026-10-18-goto-chat-round2/scored/`.

**Headline.** G1 and G2 PASS on the blind question set. **G3 is NOT RUN**: the runner's registered smoke cap
(USD 0.35) was exceeded by the one smoke question (USD 0.392), and the runner stops by design at that point, before
any of the 64 gated questions. No G3 number, no raw arm, no ablation, one-shot or observe result exists. Nothing
below describes an agent result. G4 (browser) is not part of this node.

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
| G3 session, all 64 | six sub-gates | see below | **NOT RUN** | NOT RUN |
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

## 3. Agent stage (`dogfood/chat2_agent.py`): stopped at the smoke cap

Run once with the default arms. The only call made was the **smoke** question (round 1's C-1, "What happens when a
check-in fails validation?", dev data), through the real Studio session HTTP routes and the real `claude` CLI (sonnet).

| item | registered | observed |
|---|---|---|
| smoke status | ok | ok, 6 turns, 19.2 s, 0 `partial` text events (check mode buffers), routed: false |
| smoke cost (CLI list estimate) | <= USD 0.35 | **USD 0.392** (input 10, output 1,545, cache read 315,199, cache creation 86,228 tokens) |
| checker on the smoke answer | n/a | kept 28, replaced_element 1, withheld 1, others 0 |
| runner outcome | stop if over the cap | `stopped: smoke question: ok $0.392 (cap 0.35)`; gated, ablation, one-shot, observe not started |

The smoke tools used: `spec_ask`, `workflow_find`, `model_get`, `Grep`, `Read`. The overshoot is 12% above the cap; the
first call carries the cache creation (86k tokens). This is a registered stop and I did not override it: the cap is
the orchestrator's, the runner has no waiver switch, and no ruling gave me one. The ledger keeps the USD 0.392.

**Consequence.** `agent/answers-checked.json` and `answers-raw.json` are empty by design (`{"answers": {}}`), so the
G3 sub-gates (i) all ok, (ii) 0 literal claims, (iii) correct >= 52/64, (iv) I compliance >= 11/12, (v) floors (H >= 8/10,
N >= 4/6, M_workflows >= 6/8), (vi) routed >= 95% correct, and every report-only row (lenient count, raw arm, damage, checker
action counts, session routing rates, cost per correct answer, ablation, one-shot, observe) are **NOT RUN**. No G3 prediction
is confirmed or missed. Blind vs post hoc: everything in section 2 is blind (scored once); the scoring-call workaround in D1
changes no input. There is no post hoc material.

**To continue (the orchestrator's decision).** The ledger and the scratch world are intact (USD 0.392 of 8.00 spent), so
the remaining arms can be run without repeating the smoke:

```sh
$DRV dogfood/chat2_agent.py --questions $Q --usd-cap 8 --out docs/results/data/2026-10-18-goto-chat-round2/scored/agent \
    --arms gated,ablation,oneshot,observe
```

This needs a ruling that the smoke overshoot (USD 0.392 vs 0.35) is waived or that the smoke gate is read as a cost
note. Without it, G3 stays NOT RUN. Estimated list cost for the gated run per the prereg: USD 1.8-3.5 (p).

## 4. Other disclosures

- **Builder's accidental `claude` CLI use.** The orchestrator's brief says the builder disclosed one; its details and
  cost were not given to this node and are not in the repo data I read. It is not in this node's ledger.
- **Containers.** This node's stages start no container (tier 1 is not run; `--tiers 23`; shim log 0 calls). After both
  stages, `docker ps -a` shows no container with an `fc2-` owner label and no `lattice.owner` container; no Studio or
  runner process is left.
- **Cost, CLI list estimate:** USD 0.392 (one smoke question). Nothing else called a model.
- **Streak:** read only through `git archive`; its pre-existing ` M studio.toml` was not touched by this node.

## 5. Files created

- `docs/results/2026-10-18-goto-chat-round2-scored-results.md` (this file)
- `docs/results/data/2026-10-18-goto-chat-round2/scored/step0-selftest.txt`
- `docs/results/data/2026-10-18-goto-chat-round2/scored/freeze-build.sha256` (byte copy, D2)
- `docs/results/data/2026-10-18-goto-chat-round2/scored/qset/{maps.json, router.json, world.json, qset-scored.json, score-qset.json, score-qset.stderr}`
  (`score-qset.json` is the scoring in D1)
- `docs/results/data/2026-10-18-goto-chat-round2/scored/agent/{agent-usage.json, ledger.json, answers-checked.json, answers-raw.json, streams/smoke.jsonl}`
