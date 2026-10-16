# Implementation diff and grounded chat (Spike C): results (Streak stage; held-out stage not run by the builder)

Data: `docs/results/data/2026-10-15-implementation-diff/` (`probes.json`, `summary.md`, `setup.json`, `blind-run-1/`,
`mutants/`, `streak-report-summaries.json`, `heldout-selftest-streak/`, `agent/`, `freeze.sha256`,
`pytest-baseline-c0ff1e4.txt`, `pytest-after.txt`, `posthoc-pt-bm25-exploration.txt`). Pre-registration:
`2026-10-15-implementation-diff-prereg.md` and its Addendum 1. Streak a0679e9 (S) and 1d00ede (S4) from `git archive`,
Spike B's committed dynamic run as S-dyn (sha256 `af1383db...`), MySQL 8.4 in Docker for PF. Predictions are the
pre-registration's. The builder saw the predictions and the truth; only PH is blind.

## Streak stage

| probe | predicted | observed | |
|---|---|---|---|
| PE | S: 25 implemented + `PUT /test/clock` test_only; S4: 19 implemented + 6 spec-only + test_only | exactly that, both | pass |
| PR | S: 142 implemented, 0 required, no_element {R1,R2,R6,R15,R136,R142}; S4: 29 required (the listed set), 113 implemented | exact sets on both; S4 required set equals the prediction (0 extra, 0 missing); 19 of the 29 are `partial`; table-only rules R35-R38, R40-R42 are unverified | pass |
| PV | S-dyn: routes verified 17-24/25, rules implemented_verified 50-120/142; static runs 0; one `.go` edit: stale with exactly that path, 0 verified | 20/25 routes, 108/142 rules; recomputation from the frozen `workflows.json` agrees 100% (routes and rules); S-stat and S4-stat 0 rules, 0 routes, 0 flows verified; after editing `internal/habits/routes.go` in a copy: `stale.changed == ["internal/habits/routes.go"]`, nothing verified; R123 and R127 (machine `static`) unverified | pass |
| PO | S: 0 orphans, excluded test_only + 2 framework; S+: orphans exactly `{GET /v1/admin/stats}`, `/metrics` plumbing | exactly that | pass |
| PC | 23 cases (>= 20): valid 6, invalid 7, forged 10; 100%, 10/10 identical | 23/23, 10/10 identical | pass |
| PA | (a) no inventory: identical to 454a8e6; (b) core impl section 5-7k tokens and <= 8000; 10/10 scripted replies | (a) 18 hashes (digest, system prompt, context, answer, prompts on 3 specs) identical; (b) 10/10 labels and bases as expected; core section **7,716 tokens** on S-dyn (7,435 S-stat, 7,311 S4-stat): within 8000, **above the predicted 5-7k** | pass; token prediction missed |
| PT | 7/8 hints (6-8) | **2/8** (PT7 `no_match`, PT8 `several`); the other six answer `several` | **FAIL (finding)** |
| PW | 0 changed, 0 new; capture with `--out` inside the clone exit 2 | 0, 0 over: report CLI x10, the three tools x10 over stdio `--readonly`, ask x10, PF (clone writable only during PF); capture exit 2 | pass |
| PF | 3/3 agree, 1/1 disagree, 0 generator calls | check-in (`wf-fc7d0a0772`), device delete (`wf-801d529a0f`), owner guard (`wf-087fea1dbb`): all `agrees`; the in-memory copy with `expect_status` 418: `disagrees` (expected 418, got 201); 4 generator calls in all (one per ask), `rounds` 1 each, no scenario written; containers before == after | pass |
| PP | record `source {server: extractor, item}`, not accepted; code and spec byte-identical; only `changes/` grows; no accept tool | exactly that (change `c1`, item `implemented_not_required:GET /v1/admin/stats`, the drafting failed on the scripted stub, never accepted) | pass |
| PD | 10/10 + second root identical; no `/Users`, `/private`, time | 4 inventories x 10 separate processes x {json, text} (random hash seeds) identical; a second archive root gives identical `inventory.json` and report; no leak | pass |
| PX | report 0.1-0.6 s; tool < 0.3 s; digest overhead < 0.2 s | report CLI 0.15 s (in-process 9 ms); each tool 11-14 ms after load; digest overhead 8 ms | pass |
| PB | all identical; base + 3 tools | 20 hashes identical to 454a8e6 (instructions and `tools/list` for 8 project/workspace/readonly configs, single toolbox, `ask --json`, route table AST); `/api/ask` and `/api/model` of the dogfood project identical; with `[implementation]`: base + 3 after `change_list`; session allowlists base + 3 (ask, explain, change only), prompts and rebuild unchanged | pass |
| LEAK | 0 containers | 0 | pass |

PT is the one failed gate. Raw output (`probes.json` PT rows): for "check in a habit" the top three are
`DELETE /v1/devices/{}`, `DELETE /v1/habits/{}` and `DELETE /v1/habits/{}/completions/{}` at the same score 2.4995. The
pre-registered document (key, handler, tables, flow name, and the texts of every rule that links the route) makes
a route that 25 rules link say every query term, so the IDF-weighted set overlap ties at its maximum and the 10% rule
answers `several`. The ranking carries no signal there. A BM25 variant (one post hoc round, not kept) ranks the intended
route first for 2 of 6 positive queries and its hints match 2/8 as well (`posthoc-pt-bm25-exploration.txt`).

## The report on Streak

| | rules implemented (verified / unverified) | required_not_implemented | no_element | spec-only endpoints | orphan routes | excluded |
|---|---|---|---|---|---|---|
| S a0679e9, S-dyn (observed) | 142 (108 / 34) | 0 | 6 | 0 | 0 | 3 (test_only + 2 framework) |
| S a0679e9, S-stat | 142 (0 / 142) | 0 | 6 | 0 | 0 | 3 |
| S4 1d00ede, S4-stat | 113 (0 / 113) | 29 | 6 | 6 (`GET /v1/home`, `GET /v1/sync`, `GET /v1/nudges`, `POST /v1/nudges/{}/dismiss`, `POST /v1/devices`, `DELETE /v1/devices/{}`) | 0 | 3 |
| S+ (planted), no workflows | 142 (0 / 142) | 0 | 6 | 0 | 1 (`GET /v1/admin/stats`) | 4 (+ `GET /metrics` plumbing) |

On S-dyn the flows are 59 verified, 1 unverified, 8 failed (Spike B's counts); routes 20 of 25 verified. The report is 251 kB
of JSON (S-dyn), 7.3-7.7k tokens of digest (`streak-report-summaries.json`).

## Mutants (section 4)

Control first: PE, PR, PV, PO, PC, PA, PW, PD, PB pass; PT fails (the blind finding), so a mutant is killed when a probe
that passes on the control does not pass on it, or, for PT, when its rows differ from the control's. 3 runs for PD in the
mutant runs (10 in the Streak stage). IM16 runs PW with Docker.

| id | mutant | predicted catcher | caught by |
|---|---|---|---|
| IM1 | `norm_route` (no UUID templating) for scenario steps | PE | PA, PE, PR, PV |
| IM2 | conditional routes not excluded from scenario links | PO | **PV** (not PO: D6 excludes a conditional route whether or not a rule links it, so the exclusion list does not change; the clock route becomes an element of every rule whose scenario calls it, and no flow verifies it, so those rules lose their verified status, which PV's independent recomputation sees) |
| IM3 | rule implemented when ANY element is present | PR | PR |
| IM4 | rule-text routes ignored | PR | PR, PV |
| IM5 | any flow status counts as verified | PV | PV |
| IM6 | stale check off | PV | PV |
| IM7 | plumbing list applied to rule-linked routes | PE, PO | PE, PO |
| IM8 | quote matched anywhere in the file | PC | PC |
| IM9 | no root-containment or symlink check | PC | PC |
| IM10 | model's implementation label trusted | PA (b) | PA |
| IM11 | SYSTEM_IMPL without an inventory | PA (a), PB | PA (PB does not read the prompt) |
| IM12 | the 3 tools listed without `[implementation]` | PB | PB |
| IM13 | read path caches the report under the inventory | PW | PW |
| IM14 | absolute path in the report | PD | PD |
| IM15 | `workflow_find` ranks spec-only as exists | PT (S4 rows) | **SURVIVED** the probes: the PT rows are `several` on the control, so the `spec_only`/`exists` branch is never reached; killed by the unit test `test_workflow_find_a_spec_only_route_is_spec_only` (run against the mutant) |
| IM16 | verify writes a model scenario when a flow is cited | PF | PF, PW |

**15/16 killed by the probes; 16/16 counting the unit test for IM15.** The pre-registered 16/16 is not met by the probes
alone.

## Findings (differences from the predictions, with the raw evidence in `probes.json`)

1. **PT (`workflow_find`) 2/8 against a prediction of 7/8 and a gate of 6/8.** See above. A route that many rules link
   matches every query term, so the pre-registered lexical score ties and the hint is `several`; "check in a habit" never
   answers `exists`, "register a device" on S4 never `spec_only`. The exact `METHOD /path` mention path works (unit-tested); a free question does not
   get a usable hint.
2. **PA token prediction missed**: the implemented-app section of the core is 7,716 tokens on S-dyn (predicted 5-7k); it is
   inside the 8,000 budget, and refusal codes are still in the route lines (they are dropped only above the budget).
3. **IM15 survives the probes** (see the mutant table): a consequence of finding 1.
4. **Q3 key differs from its prediction** (agent exercise, below): the report says `POST /v1/completions/batch` is
   verified (flows `wf-2da750f4e8` and `wf-4e49f96210` verify it; Spike B's K2 flow, the one with `habits` then `batch`,
   failed), so the frozen key is IMPLEMENTED-VERIFIED, not UNVERIFIED.
5. **A rule text that ends a sentence with a route** (`... POST /api/login.`) keyed a phantom spec-only endpoint under the
   literal `routes_in`; found on the unit fixture before any probe and fixed (Addendum 1 B2). Streak's texts do not trigger it.
6. **Held-out paths**: the Spike A scorer joins spec paths to code paths by suffix (the held-out apps mount under a
   prefix). The report keys are exact templated paths (D3), so an app whose spec omits the prefix will show every
   endpoint as spec-only and every route as implemented-not-required in the primary score. The held-out runner reports the
   exact-key score (the pre-registered one) and, labelled, the suffix join beside it.

## Post hoc corrections (script bugs; listed old/new)

The first Streak-stage run is kept as `blind-run-1/`; every other row of it was reproduced by the second run.

| id | old | new | effect |
|---|---|---|---|
| PA (a) | the sanity bound `len(base hashes) >= 20` (the script yields 3 specs x 6 = 18) | `== 18` | blind PA read FAIL on that bound only; the 18 hashes were identical |
| PW / PF | the Studio built on the clone used Studio's default state directory, inside the repository: `.lattice/state/S/...` appeared in the clone (6 new paths) | `[state] dir` outside the clone in the clone's `studio.toml` | blind PW read FAIL (6 new, 0 changed) for a Studio side effect that is not the read path (premise 5 of the pre-registration) |
| PF | the "habit check-in" flow was the first verified flow with that skeleton, an owner-guard flow (ben, 404) | the first verified `pair` flow (ann, 201) | the same 3/3 agree; the wrong-status copy now fails at 418 vs 201 |

No expectation, ground-truth set or threshold was changed. One exploration of PT (BM25) was run once with its parameters
fixed beforehand, did not reach the gate, and was not kept (`posthoc-pt-bm25-exploration.txt`).

## Decisions the pre-registration does not fix (Addendum 1 B lists them in full)

`package` in `inventory.json`; punctuation after a route in a rule text; element reasons `static schema` and
`machine <status>`; tables, `endpoints[]`, `spec_skeletons[]` in the report; `workflow_find`'s `no_match` denominator and
tie order; the `type` key of `workflow_get`; an empty `[implementation]` table registers the tools and leaves ask as before;
the S+ `/metrics` route planted on the engine; the mutant kill criterion (differential against the control); the held-out
runner's primary and secondary joins; the agent exercise's keys (from the frozen report), world (`[state] enabled = false`),
tool set (`--tools Read,Grep,Glob`), B3 on bare citations and B8's method requirement.

## Held-out stage

Not run by the builder. `dogfood/implementation_probes.py --held-out --app NAME=DIR@REF ... --spec DIR:MODULE:CALL
--truth NAME=FILE [--workflows NAME=FILE]` freezes the hashes into `freeze.sha256`, archives each app, captures an inventory
(the given workflows, else Spike B's static run), builds the report with no trace (`requirement_basis: spec`), scores the
endpoint classification against the truth file (exact keys, and the suffix join beside it) and runs the report twice for
determinism. Self-test on Streak with a hand-built truth file (26 routes, the S spec as "t5"): accuracy 25/25, 0 crashes,
deterministic (`heldout-selftest-streak/`).

## Real-agent exercise (PQ, run last and separately)

`claude` CLI only (no API key; the child environment has no `ANTHROPIC*`), sonnet resolved as `claude-sonnet-5-5`
(`claude 2.1.299`), caps USD 0.50 and 8 turns per session, USD 5 in all. Scorer, questions, keys, runner and Spike B's
workflows are frozen by hash before the first session (Addendum 1 C); the runner started only after checking them and
the two report hashes. No usage or rate limit was hit. A one-off flag smoke before the freeze (`--max-turns` accepted; haiku,
USD 0.0006) is outside the exercise.

**Pass rule: >= 11 of 13 scored sessions satisfy every applicable item and B7 holds in 15 of 15. Observed: 8 of 13,
B7 15 of 15. The rule is evaluated (n = 13) and NOT met.**

| session | key | first-line label | result | USD | tokens | turns |
|---|---|---|---|---|---|---|
| Q1 | IMPLEMENTED-VERIFIED | IMPLEMENTED-VERIFIED | pass | 0.359 | 79,894 | 7 |
| Q2 | IMPLEMENTED-VERIFIED | IMPLEMENTED-VERIFIED | FAIL (B4) | 0.061 | 8,491 | 4 |
| Q3 | IMPLEMENTED-VERIFIED | IMPLEMENTED-VERIFIED | pass | 0.079 | 12,656 | 4 |
| Q4 | IMPLEMENTED-UNVERIFIED | IMPLEMENTED-UNVERIFIED | pass | 0.100 | 14,900 | 8 |
| Q5 | NOT-SPECIFIED-NOT-IMPLEMENTED | NOT-SPECIFIED-NOT-IMPLEMENTED | pass | 0.078 | 13,047 | 5 |
| Q6 | NOT-SPECIFIED-NOT-IMPLEMENTED | NOT-SPECIFIED-NOT-IMPLEMENTED | pass | 0.102 | 16,286 | 7 |
| Q7 | AMBIGUOUS | IMPLEMENTED-VERIFIED | FAIL (B2) | 0.078 | 14,790 | 4 |
| Q8 | AMBIGUOUS | IMPLEMENTED-VERIFIED | FAIL (B2) | 0.068 | 12,563 | 3 |
| Q9 | REQUIRED-NOT-IMPLEMENTED | REQUIRED-NOT-IMPLEMENTED | FAIL (B5) | 0.074 | 11,908 | 5 |
| Q10 | REQUIRED-NOT-IMPLEMENTED | REQUIRED-NOT-IMPLEMENTED | pass | 0.067 | 9,991 | 6 |
| Q11 | REQUIRED-NOT-IMPLEMENTED | REQUIRED-NOT-IMPLEMENTED | pass | 0.132 | 23,872 | 6 |
| Q12 | IMPLEMENTED-UNVERIFIED | IMPLEMENTED-UNVERIFIED | FAIL (B4) | 0.097 | 14,614 | 6 |
| Q13 | NOT-SPECIFIED-NOT-IMPLEMENTED | NOT-SPECIFIED-NOT-IMPLEMENTED | pass | 0.055 | 7,097 | 4 |
| ablation-Q3 | IMPLEMENTED-VERIFIED | IMPLEMENTED-UNVERIFIED | FAIL (B2,B4) | 0.392 | 89,494 | 6 |
| ablation-Q9 | REQUIRED-NOT-IMPLEMENTED | REQUIRED-NOT-IMPLEMENTED | FAIL (B3) | 0.093 | 14,145 | 6 |

Totals: USD 1.83 for the 15 sessions (cap 5.00; the first four cost USD 0.60, cap 1.50);
tokens counted as input + output + cache creation 343,748 (cap 1,000,000), 3,886,586 with cache reads.
Per session: USD 0.055-0.392 (median 0.079), 7k-89k counted tokens (median 14k), 3-8 turns (median 6). Wall 369 s.

Items over the 13 scored sessions: B1 13/13, B2 11/13, B3 13/13, B4 11/13,
B5 12/13, B6 13/13, B7 13/13, B8 2/2. Ablation (without the three tools, Q3 and Q9; information
only): B2 1/2, B3 1/2, B4 1/2, B5 2/2.

Predictions: 12/13 (11-13), B1 13/13, ablation B2 1/2, USD 0.10-0.30 per session and 1.8-3.5 in total, 4-7 turns and 30-70k counted
tokens per session, E2 B2 9-12/13 for USD 0.6-1.5. Observed: 8/13 (miss), B1 13/13, ablation B2 1/2, USD 0.055-0.39 per session
and **1.83** in total (in range), 3-8 turns (median 6), **7k-89k tokens (median 14k; below the predicted 30-70k)**, E2 B2 8/13 (just below)
for USD 1.44 (in range).

What the misses are (raw answers in `agent/<sid>/answer.txt`, item detail in `score.json`; none was re-scored or excused):

- **Q7, Q8** (key AMBIGUOUS): both answered IMPLEMENTED-VERIFIED and listed every delete (4) and list route: the label is the
  agent's reading of "is there ...?" as a yes; the frozen key is AMBIGUOUS. B2 misses.
- **Q2, Q12** (B4): the agent named `DELETE /devices/{}`, `POST /habits/{}/completions` and `POST /completions/batch`, the group-relative
  paths as `internal/*/routes.go` registers them under `/v1`. The frozen B4 requires the templated full path; these are not
  in the extract or the spec as written, so B4 fails although the answer also gives the full path.
- **Q9** (B5): REQUIRED-NOT-IMPLEMENTED with the right reasoning (no `GET /v1/home` route; the spec requires it) but it cites
  `REQUIREMENTS.md` lines and M17 and names none of the key rules R21, R27, R114, R139.
- **Q3**: the key follows the report (VERIFIED); the agent said IMPLEMENTED-VERIFIED and passed. The ablation without the tools said
  IMPLEMENTED-UNVERIFIED (and named `POST /completions/batch`).
- ablation **Q9** (B3): it cited `REQUIREMENTS.md:219` and `spec.py:294` as `code:` locations (B3 counts them as invalid
  code citations); with the tools, Q9 had no such citation.
- Q1 spent USD 0.36 and 7 turns (the most of the 13); Q5, Q6, Q13 (NOT-SPECIFIED) pass cheaply.

Every scored session called an implementation tool (B1 13/13): `workflow_find` was the first call in 12 of the 15 sessions (Grep
in 2, `implementation_get` in 1) and answered `several` in 11 of the 13 scored sessions it ran in (`exists` in Q3 and Q11; also
`several` for Q5 and Q6, where `no_match` was right: finding 1); the agents then went on to Grep, Read and `implementation_get`
and answered from the code.

### Arm E2 (report only): `python -m lattice.spec ask --inventory`, sonnet, the 13 question texts

B2 8/13, USD 1.44 (cap 2.00). The label is the answer's `implementation` key after the deterministic downgrades.

| q | label | missed items | USD | tokens | model calls |
|---|---|---|---|---|---|
| Q1 | IMPLEMENTED-UNVERIFIED | B2,B3 | 0.255 | 94,321 | 2 |
| Q2 | IMPLEMENTED-UNVERIFIED | B2,B3 | 0.151 | 102,219 | 2 |
| Q3 | IMPLEMENTED-UNVERIFIED | B2,B3 | 0.121 | 94,374 | 2 |
| Q4 | IMPLEMENTED-UNVERIFIED | B3 | 0.119 | 93,937 | 2 |
| Q5 | NOT-SPECIFIED-NOT-IMPLEMENTED | - | 0.053 | 45,963 | 1 |
| Q6 | NOT-SPECIFIED-NOT-IMPLEMENTED | - | 0.054 | 46,205 | 1 |
| Q7 | IMPLEMENTED-UNVERIFIED | B2,B3 | 0.118 | 93,796 | 2 |
| Q8 | IMPLEMENTED-UNVERIFIED | B2,B3 | 0.115 | 92,742 | 2 |
| Q9 | REQUIRED-NOT-IMPLEMENTED | - | 0.165 | 40,806 | 1 |
| Q10 | REQUIRED-NOT-IMPLEMENTED | - | 0.058 | 46,039 | 1 |
| Q11 | REQUIRED-NOT-IMPLEMENTED | - | 0.056 | 45,856 | 1 |
| Q12 | IMPLEMENTED-UNVERIFIED | B3 | 0.120 | 92,626 | 2 |
| Q13 | NOT-SPECIFIED-NOT-IMPLEMENTED | - | 0.055 | 45,778 | 1 |

E2 is conservative where the agent is not: it never reached IMPLEMENTED-VERIFIED (the answer must cite a verified `impl:flow` or
`impl:rule` AND a code location at an evidence line, and the model did not), so Q1-Q3 read IMPLEMENTED-UNVERIFIED (the downgrade
is by design). Its REQUIRED-NOT-IMPLEMENTED answers (Q9-Q11) and NOT-SPECIFIED answers (Q5, Q6, Q13) match the keys. B3 fails when an
implemented label carries no valid evidence `code:` citation.

## Regression and hygiene

- `uv run pytest tests/mcp tests/studio tests/spec tests/importer tests/gocheck -q -p no:cacheprovider` (by directory, the main
  checkout's venv, no database drivers): baseline on a pristine archive of `c0ff1e4` **2 failed, 2283 passed, 5 skipped, 29
  errors**; after: **2 failed, 2428 passed, 5 skipped, 29 errors**. The 145 new tests (11 + 59 + 30 + 24 + 21) are the difference;
  the failed and errored test ids are identical sets (31 ids; `ModuleNotFoundError` for the server-database drivers, and
  `test_multidb::test_a_check_starts_and_removes_its_own_container`, `test_outside::test_the_template_reconciles_with_one_explained_disagreement`).
  `pytest-baseline-c0ff1e4.txt`, `pytest-after.txt`.
- `dogfood/studio/check.sh`: PASS, layers D and X, 481 cases (all correct), 0 findings: the same count as the pre-registration expects.
- `ruff check` clean on every changed `.py`; `ruff format --check` clean on every created `.py` and on every changed file except
  `lattice/studio/server.py`, which was already unformatted at `c0ff1e4` in two places this change does not touch. `gofmt -l` clean
  on the Go fixture (no Go source of the product changed; `go vet` cannot compile the fixture offline, it needs Gin and GORM).
- Pinned tests edited (each named in the pre-registration): `tests/studio/test_mcp_intake_http.py` (`L_ASK` gains the three tools after
  the multi-project ones, and the 17 `n_allowed` counts rise by 3). `tests/studio/test_legend.py` and the other session-argv pins needed no edit.
- No container left (`LEAK` 0, and `docker ps -a` shows none of ours); the Streak checkout's `git status` is the owner's `studio.toml`
  only, before and after; the Streak checkout was only read (`git archive`).
- The built code is the pre-registered one except what Addendum 1 B records. No existing golden, `dogfood/studio/**`, the mock or the web UI
  was touched; `GET /api/model` has no new key; API.md moved to v1.22 (documentation only).

## Orchestrator verification (independent re-run)
191 new and affected tests pass; `ruff check` clean and `ruff format --check` clean on the created files
(`server.py` was already unformatted at base); `dogfood/studio/check.sh` PASS, 481 cases; the Streak-stage
probe runner at 3 runs reproduces every row, including the failing PT (2/8 against a gate of 6/8): 12 of 13
rows pass; the broad regression `tests/mcp tests/studio tests/spec tests/importer tests/gocheck` gives
2 failed, 2428 passed, 5 skipped, 29 errors, the same counts the builder measured on a pristine base (the
errors are `ModuleNotFoundError` for the database drivers in the Lattice venv, plus the known order-dependent
`test_outside` flake and one `test_multidb` case).
**Headline, plainly.** The deterministic part works on the ground truth: the requirement-vs-implementation
report is exact on Streak a0679e9 (25 implemented endpoints, 0 required-not-implemented, 6 rules with no
element) and on 1d00ede (exactly the 29 predicted required-not-implemented rules and 6 spec-only
endpoints), the planted orphan is found and plumbing excluded, the code-citation checker is correct
(23/23), reads write nothing, and replay-verify works. The two parts that involve search and an agent
fall short of their pre-registered bars: `workflow_find` (PT 2/8 vs 6/8; it answers "several" for most
queries because routes linked by ~25 rules contain every query term) and the capped real-agent exercise
(8/13 vs 11/13; "is there ...?" questions read as yes, relative paths rejected by the frozen scorer). Neither
was tuned after the fact. The held-out stage follows below once run.
