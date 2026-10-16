# Multi-project slice 4d, consumer-side drafting: results

Pre-registration: `2026-10-15-consumer-draft-4c-prereg.md` (with Amendment 1; the file name keeps "4c", the slice
is **4d**). Branch `np-draft4c` from `np-studioui` 223adff. Data: `docs/results/data/2026-10-15-consumer-draft-4d/`
(`summary.json` lists RAN / NOT RUN per probe). No model call outside P3; P3 went through the `claude` CLI only
(2.1.299, `cli_env`, no API key). Nothing was committed by the build node.

## 1. What was built

| File | Lines | Change |
|---|---|---|
| `lattice/contracts/draft.py` | 561 | new: `draft`, `draft_from_row`, `draft_for`, `load_files`; pure template, no I/O in `draft` |
| `lattice/contracts/__main__.py` | +26 | `questions draft CONSUMER ID [--json]`, docstring |
| `lattice/mcp/projects.py` | +58 | `linked_question_draft {consumer?, id}` in `WORKSPACE_TOOLS` (read only), docstring; `WORKSPACE_NOTE`/`READONLY_NOTE` untouched |
| `tests/contracts/test_draft.py` | 766 | 41 tests |
| `tests/mcp/test_linked_question_draft.py` | 169 | 9 tests |
| `dogfood/contracts/draft/ci-bot-spec/{REQUIREMENTS.md,spec.trace.json}` | 8 + 62 | the planted consumer spec (R1-R6) |
| `dogfood/contracts/draft_probes.py` | 1209 | P1, P2, T, P4, S |
| `dogfood/contracts/draft_agent.py`, `draft_agent_scorer.py`, `draft_agent_scorer_cases.json`, `draft_agent_check.py` | 376, 232, 86, 71 | P3 runner, frozen scorer (37 cases), check |
| `dogfood/contracts_mutants.py` | +155 | DR1-DR14, `DR` stages, `dogfood/contracts/draft` in `COPY` |
| `docs/design/lattice-multi-project-phase.md` | row 4 | stale row corrected (4c transitive and the Studio UI are built; 4d added) |
| four pinned tool-list tests (disclosed) | +25 | `tests/mcp/test_contract_check.py` (3 asserts `names[-5:]`), `test_plumbing_plugin.py` (`WS_TOOLS`), `test_linked_questions.py` (`ours`), `tests/contracts/test_linked.py` (the static allow-list names `contracts/draft.py`) |

Premise corrections hold as pre-registered: no impact row or question carries a consumer rule or step; the draft names
the affected `[[import]]` tables exactly (file position) and rules only by the stated text match, which is not
verified reliance. Not built (as decided): API route, Studio card, model-written refinement, any write.

## 2. Deterministic probes (run 2026-10-15, 10 runs, `probes/`)

| Probe | Predicted | Observed |
|---|---|---|
| P1 | 13/13 rows as the table; 11 + 11 no-draft; dismissed, superseded 2/2; 4/4 variants; two-adds 1/1; identical 10/10 and across two scratch roots | **as predicted**: 13/13 (job D1-D5, Streak D1-D7, I3x-b: imports, hunks, rules, scenarios, wording exact); 11/11 refused by `draft_from_row` (`not_contradiction`, exit 1) and 11/11 `unknown_question` (exit 2) by CLI; dismissed `not_open` exit 1; superseded lq1 produced with the line; stale trace, pin moved, imports deleted, two-adds as described; 10/10 identical, two roots identical; library draft of the impact row equals the CLI draft of the stored question (job D2, Streak D2) |
| P2 | 6/6 checks on every row; missing state dir exit 2 and nothing created | **as predicted**: 13/13 rows 6/6 (CLI json/text, MCP stdio, `draft_for` x10 each under `chmod -R a-w`); missing state dir: CLI exit 2 `no_state`, MCP tool error `no_state:`, `find` unchanged |
| T | median <= 1.0 s (0.2-0.5 s) | median 0.17 s (`--json` 0.07 s) |
| P4 | all identical to `git archive 223adff` | 100/100 CLI argv (impact [--transitive], export, check, notify [--dry-run], questions list/resolve/dismiss/init, pin; text and `--json`, times masked) identical; MCP `initialize` identical in 6 of 6 configurations; `tools/list` byte-identical without a workspace and base + exactly `linked_question_draft` with one; `lattice/` differs from 223adff in `contracts/__main__.py`, `mcp/projects.py` and the new `contracts/draft.py` only; `lattice/studio/**` (API.md included) identical |
| S | real repos untouched | job-scheduler ef17cf8 ` M uv.lock`, Streak a0db033 ` M studio.toml`, 0 newer files |

## 3. Mutants (`mutants/`, final test files)

Control passes. **14/14 killed** by `tests/contracts/test_draft.py`: DR1, DR2 (`test_the_endpoint_gone`, `numbers == [6]`),
DR3, DR4, DR5 (same test, `rules == ["R4"]`), DR6 (`test_the_text_is_exactly_the_template`), DR7
(`test_the_draft_row_label_is_checked`), DR8 (`test_a_cleared_question_is_refused`), DR9
(`test_two_added_items_do_not_pair`), DR10 (`test_the_endpoint_gone`), DR11
(`test_nothing_is_written_not_even_a_timestamp`), DR12 (`test_a_missing_state_directory_is_no_state_and_nothing_is_created`),
DR13 (`test_the_trace_guard`), DR14 (`test_a_moved_pin_omits_the_before_facts`). The prereg also named a probe per
mutant; the unit tests kill each first, so the probe was not needed for the kill and was not run per mutant.

## 4. Regression

| Gate | Before first edit | After |
|---|---|---|
| `pytest tests/contracts tests/mcp tests/workspace` | 641 passed | 691 passed (+41 +9) |
| `pytest tests/studio tests/improve` | n/a | 740 passed, 1 skipped |
| `dogfood/contracts/probes.py --runs 1` | 35/35 | 35/35 |
| `dogfood/studio/check.sh` | PASS 481 cases, 57 scenarios (old tree) | PASS 481 cases, 57 scenarios |
| `linkedq_probes.py --runs 10 --skip-regression` | - | 8/10 (see findings 1, 2) |
| `impact_probes.py --runs 10` (11 probes, as 4b) | - | 11/11 in isolation (first invocation 10/11, finding 3) |
| `transitive_probes.py --runs 10 --base a4ab4a1` | - | 10/11, T-flagoff 30/30 byte-identical (finding 4) |
| `improve2_probes.py --runs 3`, new vs old tree | - | 15/15 both; D1-D4 identical; D5 `report --json` digests differ by scratch path |
| ruff check, ruff format --check | - | clean on every created or changed .py |

## 5. P3, real-agent exercise (full run 2; run 1 was partial)

Scorer frozen before any session (`agent/scorer-freeze.json`): `draft_agent_scorer.py` 97395b9e..., cases
32201ba4... (37 cases, 37/37), `draft_agent_check.py` 46b66768...; unchanged for both runs; `claude` 2.1.299, resolved
model `claude-sonnet-5-5` in all 10 sessions. Amendment 2 (prereg, orchestrator ruling): the 400k-token abort counts
input + output + cache creation only; cache reads are reported beside, never counted. The scorer is not edited.

### 5.1 Full run (Amendment 2; `agent/`, all 8 sessions RAN, wall 127 s)

Tokens: counted per the amendment, (with cache reads) in brackets.

| Session | World | Cost | Tokens | Turns | Tools (! = error) | Items | A6 |
|---|---|---|---|---|---|---|---|
| sonnet-WD2-1 | W-D2 | USD 0.096 | 12,377 (364,693) | 6 | project_list, contract_check, linked_question_list, linked_question_draft, code_links! | A1 A2 A4 A5 A7 ok, **A3 FAIL** | ok |
| sonnet-WS6-1 | W-S6 | USD 0.101 | 12,100 (438,922) | 7 | project_list, contract_check, linked_question_list, linked_question_draft, project_status, Bash! (denied) | all ok (A7 n/a) | ok |
| sonnet-WD2-2 | W-D2 | USD 0.079 | 11,102 (288,846) | 5 | project_list, contract_check, linked_question_list, linked_question_draft | all ok | ok |
| sonnet-WS6-2 | W-S6 | USD 0.092 | 12,094 (364,178) | 7 | project_list, contract_check, linked_question_list, linked_question_draft, code_links!, project_status | all ok (A7 n/a) | ok |
| sonnet-WD2-3 | W-D2 | USD 0.090 | 11,763 (364,056) | 6 | project_list, contract_check, linked_question_list, linked_question_draft, code_links! | all ok | ok |
| sonnet-WS6-3 | W-S6 | USD 0.092 | 11,956 (364,031) | 6 | project_list, contract_check, linked_question_list, linked_question_draft, code_links! | A1 A2 A4 A5 ok, **A3 FAIL** (A7 n/a) | ok |
| ablation-WD2-1 (no tool) | W-D2 | USD 0.327 | 74,443 (286,468) | 6 | project_list, contract_check, linked_question_list, code_links!, project_status | A2 FAIL, A3 FAIL, A4 A5 ok, A7 FAIL | ok |
| ablation-WD2-2 (no tool) | W-D2 | USD 0.072 | 10,529 (216,555) | 6 | project_list, contract_check, linked_question_list, model_get!, code_links! | A2 FAIL, A3 FAIL, A4 A5 ok, A7 FAIL | ok |

Totals: USD 0.949 (the six tool sessions USD 0.55; the first three USD 0.28), 156,364 counted tokens
(2,687,749 with cache reads), no stop, no usage or rate limit.

**Frozen scorer's verdict:** 4 of 6 scored sessions satisfy every applicable item (WD2-2, WD2-3, WS6-1, WS6-2);
the pass rule (>= 5 of 6, A6 8/8) is **not met** (A6 is 8/8). Per item with the tool: A1 6/6, A2 6/6, A3 4/6,
A4 6/6, A5 6/6, A7 3/3. Ablation (information only): A2 0/2 (no import number named without the draft),
A3 0/2, A4 2/2, A5 2/2, A7 0/2.

**By-hand reading beside the verdict (not a rescore).** Both A3 misses are the scorer's pattern flagging backticked
tokens in sentences that propose no change: WD2-1 `contract_check`, `ok`, `ef17cf88` ("`contract_check` still says
`ok`... at the old commit `ef17cf88`"); WS6-3 `a0db033a`, `contract_check`, `ok` (the pinned commit and "reports
`ok`"). The two short commit ids are the real pinned commits (lock `git_ref` ef17cf8809ef..., a0db033...). Read by
hand, neither answer names an import, rule, status or field absent from the files, so by hand 6 of 6 would satisfy
every item. The frozen verdict stands; a scorer fix is a separate pre-registered round (Amendment 2). The ablation's
A3 misses are the same pattern (commit ids, `contract_check`, `ok`) plus its A2/A7 misses, which are real: without the
draft the two sessions named the endpoint but no import number and no rule.

### 5.2 Partial run 1 (kept: `agent-partial-run1/`)

Same scorer, runner counted cache reads. Two sessions ran (WD2-1 USD 0.342, 364,059 tokens, 6 turns, A3 FAIL;
WS6-1 USD 0.073, 218,420 tokens, 6 turns, A3 FAIL; both found the tool unaided and passed A1, A2, A4, A5, A6, A7), then
the abort fired at 582,479 tokens (USD 0.41). 6 sessions NOT RUN (token cap), pass rule not evaluated. Its A3 misses
are the same false-positive pattern (`contract_check`, `ok`).

### 5.3 Predictions against the full run

| Prediction | Observed |
|---|---|
| 6/6 scored sessions satisfy every item | 4/6 by the frozen scorer (the 2 misses are A3 false positives); 6/6 by hand |
| A1 5-6/6 | 6/6 (the tool is named in no instruction) |
| ablation A7 0-1/2 | 0/2 |
| USD 0.40-1.00 total | USD 0.949 over 8 sessions (0.55 for the six) |
| 4-6 turns | 5-7 |
| 20-60k tokens per session | 10.5-12.4k counted (cache creation + input + output), 74k for ablation-1; 216-439k with cache reads |
| resolved id claude-sonnet-5-5 | yes |

## 6. Findings (differences from a prediction or a pass rule)

1. **P3 token cap** (run 1): the 400k abort counted cache reads and fired after 2 sessions at USD 0.41; the
   ruling (Amendment 2) excludes cache reads and the full exercise was re-run (section 5.1), run 1 kept beside it.
2. **P3 A3 false positives** of the frozen scorer on backticked tool names, `ok` and commit ids (all 4 A3 misses of the
   tool sessions of both runs are of this kind: 2 in run 1, 2 in run 2); the pass rule is not met by the verdict (4/6) and
   would be by hand (6/6). Scorer not edited; a fix is a separate pre-registered round.
3. **`linkedq_probes.py` Q4** `static_linked_questions_only_where_allowed`: the runner's allow-list does not name the
   new module `contracts/draft.py` (it imports `linked`); the same pinned list in `tests/contracts/test_linked.py` was
   updated (disclosed). **Q7** `regression_run` is false because `--skip-regression` was passed (12/12 byte comparison
   with 33d8de1 passes).
4. **`impact_probes.py` I6 and `transitive_probes.py` T-safety failed in the first invocation** because I ran several
   runners and wrote results into the same worktree at the same time (the probes' own "worktree newer since marker"
   check); both pass when re-run alone (11/11, 10/11). **T-flagoff** needs `--base` (git archive a4ab4a1), which the
   first invocation omitted (0/19); with it 30/30 byte-identical; its `regression_run` check stays false because
   `--regression` was not passed (the three regression suites were run directly).
5. **Streak commit**: a0db033 was used (the runners' REF and the committed data), not a0679e9 as the node brief said:
   a0679e9 is the merge of main into a build branch; `git diff --name-only a0db033 a0679e9` lists Go sources and
   `metrics/phases.jsonl` only, no spec file.
6. `lattice/studio/session.py` `_MULTI_TOOLS` is unchanged by design, so Studio chat sessions cannot call the new tool
   (the prereg's decision). `dogfood/multi_plumbing_probes.py` (`WS_TOOLS`) and `dogfood/contracts/fixes_probes.py`
   (not owned, not in the prereg's gate list) name `linked_question_list` as the last tool and were not run; they may
   need the same one-name update.

## 7. Post hoc

Script corrections (before the reported runs): none after the official P1/P2/T/P4 run. Development runs at
`--runs 1/2` preceded it and fixed, with no prediction or pass rule changed: the `.lock` file name in P2 (the runner
first used the contracts lock's name), the `lock_changed` field of the pin-moved variant (computed after the restore),
and the label "identical N runs". Test and template choices made while coding are in section 8.

Runner change for P3 (Amendment 2, after run 1): `draft_agent.py` `tokens_of` counted input + cache creation +
cache read + output; now it counts input + cache creation + output for the abort rule and the per-session `tokens`,
and reports `tokens_incl_cache_reads` beside. Old: 4 fields, new: 3 fields (cache read dropped from the cap). The
frozen files (scorer, cases, check) are unchanged (hashes in `agent/scorer-freeze.json` re-verified before run 2).
`agent-partial-run1/` holds the run-1 sessions unchanged. Run 2's output directory still carried run 1's files
until each session directory was rewritten; every session directory, `sessions.json` and `summary.json` of `agent/`
are run 2's.

## 8. Decisions the prereg did not fix

- "before" facts are the pinned facts of the finding's kind (status finding: statuses; request field: request and query
  fields; response field; auth; body; the whole endpoint for endpoint kinds), one `before:` line per finding.
- A superseded question with no successor (`no_contradiction`): "; superseded: the provider's current contract no
  longer contradicts the pin". Missing or bad lock, pin moved, missing or bad imports are sentences with the code, never
  a path.
- Rules: a method word is case-insensitive (`get /x` counts); a bare `/` needs a method; a path must start at a word
  boundary (`and/or`, `TCP/IP`, URLs are not paths); "no Lattice spec" means no `REQUIREMENTS.md` (the draft never reads
  `spec.py`); wording replaces whole-word occurrences of the old item only.
- A replacement whose new item the import already holds is not a hunk (it would only remove a claim).
- The MCP tool answers a refusal as a tool error `code: message` (the CLI's exit codes 1/2 have no MCP equivalent).
- Imports of other providers keep their file position in the numbering (`import #k` is the position among all tables).
- P3: tokens = input + cache creation + output of the `result` record (Amendment 2; run 1 also counted cache reads); no retry on any failure.

## 9. What this cannot show

As the prereg's section 5: rule matching is text, the planted spec is written by the matcher's author (circular), the
rename pairing is a guess, "after" facts are the finding details, and
nothing here shows that owners act on drafts. P3 is n = 6 (+2) sessions of one model with a prompt, scorer and draft by one author.

## Orchestrator verification (independent re-run)
`tests/contracts tests/mcp tests/workspace` 691 passed; scorer, cases and check hashes equal the freeze file;
`draft_agent_check.py` 37/37; `draft_probes.py --runs 2`: P1, P2, T, S pass, P4 100/100 CLI argv identical
against `git archive 223adff` (my first P4 run used `a4ab4a1` as the old tree and showed 86/100, an
orchestrator error: the intermediate slices differ between the two commits); ruff check clean.
**P3 verdict stands as the frozen scorer's: 4 of 6 tool sessions satisfy every item, the pass rule (>= 5 of
6) is NOT met.** Both misses are A3 false positives of the scorer (a backticked tool name, the token `ok`, a
short pinned commit id in sentences that propose no change); by hand 6/6. A scorer fix is a separate
pre-registered round. Without the tool (ablation, n=2) no session named an import number or a rule.
