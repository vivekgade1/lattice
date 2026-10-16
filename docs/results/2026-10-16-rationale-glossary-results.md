# Results: rationale notes and a business glossary (Spike G)

Pre-registration: `2026-10-16-rationale-glossary-prereg.md` (with Amendment 1; Amendment 2 appends the freeze hashes of the
real-model runner). Branch `np-rationale`, from `next-phases` 8931d46. Data: `docs/results/data/2026-10-16-rationale-glossary/`
(`summary.json`, `probes/*.json`, `mutants/`, `agent/`, `regression/`). Streak a0679e9 (and 1d00ede for PB) only through
`git archive` into scratch; model calls only through the `claude` CLI (2.1.299, sonnet), no API key. Nothing was committed
by the build node.

## What was built
- `lattice/spec/rationale.py` (new): D1 files and discovery, D2 schema and validation (`bad_rationale`), D3 states
  (current, moved, edited, ambiguous, stale), D8 RC/RN/RP, D9 drafter (proposals only, forged owner/status dropped and noted,
  citations checked by `ask.check_citation`, written after every rule), D4 owner CLI (`approve`, `add`, `reject`; TTY and
  `CLAUDECODE` guard; atomic write; exactly one file), CLI `show|coverage|draft|approve|add|reject`.
- `lattice/spec/glossary.py` (new): D5 schema and validation (`bad_glossary`), D6 candidates C1-C3, C5, C6 with the
  normal form, longest-first non-overlapping matching, whole-token code tokens, D7 G001-G004 (never in `lint()`), D8 GU/GT/GR/GC/
  GA/GP/GX, deterministic draft, model draft, `show|lint|draft|approve|add` (`add --ignore`).
- Edits, each behind "a file is present": `ask.py` (digest sections at the end, `rationale:`/`term:` citation check, grounding
  rule, `rationale` answer key, core/retrieval with the two budgets, one prompt paragraph, `--rationale/--glossary`),
  `implementation.py` (D11 keys, flags), `__main__.py` (two `AUTHORING` entries), `mcp/tools.py` (`rationale_get`,
  `glossary_get`, `rule_get` keys), `mcp/guard.py` and `studio/rebuild.py` (protect the approved files while they exist),
  `studio/server.py` (`files_hash`, model keys, ask 422), `studio/session.py` (tool list and sentence), `studio/API.md` v1.23.
- Tests: `tests/spec/test_rationale.py` (53), `test_glossary.py` (70), `test_ask_rationale.py` (36),
  `tests/mcp/test_rationale_tools.py` (22): 181 pass. Dogfood: `rationale_probes.py`, `rationale_mutants.py`,
  `rationale_agent.py`, `rationale_agent_scorer.py`, `rationale_agent_cases.json` (46 synthetic scorer cases, all as expected),
  `glossary_seeded.json` and `rationale_agent_world.json` (both hash-checked against the registered sha256 before running).

## Probes: predicted vs observed
| probe | predicted | observed |
|---|---|---|
| PS (gated) | TP 51, FP 6, FN 2; P 0.895, R 0.962; B0 0-2 | TP 50, FP 6, FN 3; **P 0.893, R 0.943**; B0 0 findings. Pass |
| PB | all identical | 90 captured outputs identical new vs 8931d46 (lint text/JSON, digest, core, retrieve for 6 questions, 6 canned answers with and without S-dyn/S4-stat, reports, MCP `instructions`+`tools/list` for the 8 configs, single toolbox, `rule_get`/`model_get`/`spec_lint`, Studio model and ask, `files_hash`, session prompts/argv, rebuild brief and protected set, 40 guard decisions, `check --help`). S-dyn report 84624894, S4-stat 80511cbb. Pass |
| PBp | lint identical, old digest a prefix, report minus keys identical | all 8 sub-checks hold. Pass |
| PR | K0-K4 exact | exact: K0 6 current RC 6/146 = 4.1%; K1 5 moved + R88 ambiguous (R89, R95) RC 5/147 = 3.4%; K2 R75 stale 5/146; K3 R63 edited 5/146; K4 R133 stale 5/145. Pass |
| PC | W RC 5/146 = 3.4%, RP 2, stale 2, GA 2; S RC 0/146, file null | exact. Pass |
| PL (report) | GU 300 (250-350), GT 37 (34-40), GR 115 (95-135), GC 0/GR | GU 293, GT 37, GR 116, GC 0/116: all in range |
| PG | 38 (34-42): 10 C6 verbatim, 20 enum, 7 table, 1 service; 0 approved | 38: 10/10 verbatim, 20, 7, 1; 0 approved. Pass |
| PV | 14 fixtures x 5 surfaces | 14/14 (CLI, ask, implementation, MCP, Studio ask 422 and `model.*.error`). Pass |
| PAP | refusals exit 2; pty run writes 1 file with a valid `approved_sha` | 6 refusals (approve and add and glossary add without a TTY, CLAUDECODE under a pty, `--from` = approved file, unknown rule) exit 2 with their codes and no change; the pty approve wrote only `REQUIREMENTS.rationale.json`, sha and anchor verified; pty add changed only that file. Pass |
| PDR | proposals, owner null, bad basis listed, approved files unchanged | exact (dropped: owner, status, approved_by, author; 2 rejected basis entries; `--out` = approved file refused for both drafters, no model call). Pass |
| PA | 13/13 | 13/13. Pass |
| PI | keys present, 2 glossary terms, minus keys = base | exact (R63 current, R56 current, R26 proposed_only, R8 stale, R132 none, R70 stale). Pass |
| PM | as D12 | all checks, incl. no proposal or stale text in any output. Pass |
| PD10 | 10/10 + root | 10/10 identical (random `PYTHONHASHSEED`) and the second archive root identical, sha256 `ffd7276f...083b`. Pass |
| PW | 0 / 0 / exact | 15 scratch worlds unchanged; owner's Streak (`git status` and 147-file listing) and the main checkout unchanged. Pass |
| PX | glossary lint 20-150 ms, coverage 5-50 ms, digest overhead <= 15%; pass each <= 1 s | 6.4 ms, 0.4 ms, -0.9% (3.2 vs 3.2 ms): pass; **two predicted ranges missed** (both faster than predicted); a first run under load read +23.7% overhead (3.4 vs 4.2 ms), noise at this size |
| REG | same failing ids, PASS | `tests/spec mcp studio importer llm modules`: 2935 passed, 5 skipped, **2 failed + 29 errors, all `ModuleNotFoundError` psycopg/pymysql/clickhouse_connect** (the known baseline, none mine; `tests/gocheck` was not in the brief's list for this run). `dogfood/studio/check.sh`: PASS, 481 cases. ruff check clean on all changed `.py`; ruff format clean on the created ones |

### Findings against the predictions (nothing was tuned after a score)
1. **PS FN 3, not 2.** The two registered misses (SP5 "vacation pause", SP6 backticked `snoozed`) and the six registered false
   positives (SN1 "try again later", SN3 R149 `failed`, SN4 `own` in R93, R122 and "own habits" in R149, R27) are exactly as
   predicted. The extra miss is **SR7 (R114, `today`)**: the rule only has `today` inside backticks (`{today, habits, nudges}`)
   and D6 counts code tokens for C1, C2 and C5 only, so the registered truth pair cannot be found by the registered rule.
2. **A build bug was fixed before the PS run that is reported, after one look at its score.** My first reading of D6 matched
   windows of words inside a code token, so `validation_failed` matched the value `failed` (15 extra pairs in SR8/SN3; first run
   TP 50, FP 36, FN 3). D6 says matching is whole-token for code tokens; I changed `_match_code` to compare a whole token
   (`validation_failed` is not `failed`). This was a bug against the registered text, not tuning to the truth, but I had seen the
   score; tell me if you want it labelled `post hoc:`.
3. **GM6 survives** (below).
4. **PQ2 misses its bar** (below).
5. PB needed round 1's frozen S4-stat inventory (`--inv-s4stat`, copied from the Spike C2 scratch): regenerating the static
   workflows at 8931d46 adds four fields to the `run` block (`database`, `harness_file`, `harness_reason`, `list_rule`, from the
   merged Spike B round 2), so the regenerated report hash is e2ec4c77, not 80511cbb. With the frozen inventory both expected
   hashes reproduce. S-dyn uses the frozen `S-dyn.json` (sha256 af1383db...).

## Mutants GM1-GM19 (control GM0 passes every gated probe): 18/19 killed
| id | caught by (observed) | predicted |
|---|---|---|
| GM1 | PA, PC, PI, PM (PA output differs) | PC, PA, PR |
| GM2 | PA, PC, PI, PM, PR | PR K2, K4 |
| GM3, GM4, GM5 | PR (output differs) | PR K1, K1, K3 |
| **GM6** | **survived**: no gated probe fails, no PS/PA/PR/PB output differs (PC, PL, PM outputs differ) | PS SR1 (R132) |
| GM7, GM8 | PS | PS |
| GM9 | PS, PG, PM, PV | PS |
| GM10 | PV, PS output | PS SN6 |
| GM11, GM12 | PS output | PS SN5, SN2 |
| GM13 | PS | PS B0/SN4, PL |
| GM14, GM15 | PA | PA |
| GM16 | PV | PV |
| GM17 | PAP | PAP |
| GM18 | PB (`files_hash`) | PB |
| GM19 | PDR | PDR |

GM6 survives because R132 also holds the identifier form (`quiet_hours` in backticks) next to the prose "quiet hours", so SR1
still finds both truth pairs; the predicted catcher cannot see the patch. Not edited away.

## Real-model probes (claude CLI, sonnet, total USD 2.04 of 7.50; freeze in Amendment 2; run once)
| probe | predicted | observed |
|---|---|---|
| PQ1 drafter, gated | 6/6 absent, `proposed`, owner null | **6/6**. Reported: valid basis 5/6 (p 5/6), `basis []` 1 (R75; p 1), USD 0.19 (0.014-0.099 per rule) |
| PQ2 agent exercise | 11/13 (9-13), E6 13/13, USD 1.0-3.0, 3-7 turns | **10/13: bar (>= 11/13) not met**; E6 13/13; E1 13/13, E2 10/13, E3 5/5, E4 3/6, E5 1/1; USD 1.56; 3-9 turns |
| PQ3 live ask, gated 4/4 | `rationale` key = D10 function, no proposal or stale text | **4/4**; cited R63, R56, R8 but not `term:active-window` (3/4, as predicted); USD 0.29 |

PQ2 misses: A6 (R26), A7 (R20), A10 (R132). Every label was right (E1 13/13). The three failures are E2/E4: the answers
wrote status text such as "`rationale:R132` is `none`" or "no `rationale:R26` note I can quote", and the frozen scorer reads
any `rationale:<referent>` token as a citation (no quote, or an unapproved rule). No answer leaked proposal text (A6, A7 passed the
8-word check; the E4 failure is the token). The predicted A6/A7 pattern (a proposal labelled APPROVED or quoted) did not occur;
A8/A9 were labelled STALE correctly. The scorer and its 46 cases are frozen, so the 10/13 stands; a rerun with a prompt or scorer
that separates "mentions the id" from "cites it" needs a new registered round.

## Deviations and decisions to confirm
- Extra error codes: `approve_needs_terminal`, `approve_from_authoritative`, `unknown_rule` as registered; added `no_proposal`,
  `proposal_stale` (a proposal for an older wording), `rationale_exists` (needs `--replace`), `missing_value`, `no_trace`, `no_spec`,
  `no_rules`, `bad_state` (MCP). `rationale reject` is not behind the human guard (it writes only the proposals file).
- A glossary term edited after approval (sha mismatch) defines nothing and is a G003; D5 left this open.
- `glossary lint` G003 also lists rationale notes that are not current (D7); `glossary_get` therefore reads both files, while
  `rationale_get` reads only the rationale.
- `rule_state` (current, moved, ... proposed_only, none) is used by the report; `status` (approved, ...) by the chat and the model.
- `rebuild.py` protects the approved files in `_protected_paths` (also restoring a file the base commit had) and `spec_names()`.
- PQ1/PQ3 use a `ClaudeCliClient` subclass that adds `--max-budget-usd` and `--max-turns` (no flag exists on the client); PQ3 runs
  `ask.main` in process.
- `rationale draft` writes after every rule and exits 2 when the model stops midway (a limit keeps earlier proposals).
- The brief listed `tests/gocheck` in the registered REG but not in this task's dirs; it was not run.

## Files changed
Created: `lattice/spec/rationale.py`, `lattice/spec/glossary.py`, `tests/spec/test_rationale.py`, `tests/spec/test_glossary.py`,
`tests/spec/test_ask_rationale.py`, `tests/mcp/test_rationale_tools.py`, `dogfood/glossary_seeded.json`,
`dogfood/rationale_agent_world.json`, `dogfood/rationale_probes.py`, `dogfood/rationale_mutants.py`, `dogfood/rationale_agent.py`,
`dogfood/rationale_agent_scorer.py`, `dogfood/rationale_agent_cases.json`, this file, `docs/results/data/2026-10-16-rationale-glossary/`.
Edited: `lattice/spec/ask.py`, `implementation.py`, `__main__.py`, `lattice/mcp/tools.py`, `guard.py`, `lattice/studio/server.py`,
`session.py`, `rebuild.py`, `API.md`, and the prereg (Amendment 2 only).

## Orchestrator verification and rulings
Independent re-run from this worktree in my own scratch: all gated probes PASS (PS PB PBp PR PC PG PV PAP PDR PA PI PM
PD10 PX PW; PL report only); `pytest tests/spec tests/mcp tests/studio tests/importer tests/llm tests/modules`: 2935
passed, 2 failed, 29 errors (the known baseline ids, driver-less); `check.sh` pass; ruff clean on changed files (one
unused import in `tests/spec/test_rationale.py` auto-fixed by the orchestrator).
Not met, stated plainly: **PQ2 (agent exercise) 10/13 against the registered >= 11/13: the pass rule is NOT met**; the
three misses have correct labels and are a scorer-reading artefact, but the frozen scorer is not changed and a rerun
needs a new registered round. **Mutant GM6 survives (18/19).** Lint miss R114 is beyond the two predicted misses.
**post hoc:** the matching fix from piecemeal to whole-token code matching was made after the first PS run read
FP 36; the registered number is the post-fix run (TP 50 FP 6 FN 3).
Everything Spike G adds is inert until the rationale/glossary files exist (byte-identical probe PB).
