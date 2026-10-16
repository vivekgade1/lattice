# Slice 4d consumer-side draft, real-agent round 2 (A3 scorer fix): results

**Read this first.** The round-2 scorer (A3b) was designed by an author who had read all 10 round-1 answers, their
`score.json`, two transcripts and two drafts. The case table, the mutants and the development re-score of round 1
(section 4) are therefore development figures, not evidence; the round-1 answers cannot test a scorer built from them.
**Only the fresh exercise (section 5), n = 6 tool sessions (+2 ablation, information), counts.** The fresh table reports
A3b (the registered item), the informational A3b-strict and the frozen round-1 A3 side by side. **Verdict (section 6):
the registered pass rule is NOT MET (4 of 6 tool sessions satisfy every item, needs 5)**; it would be met (5 of 6) if a
denied attempt to call a non-lattice tool were not counted as a tool call, a reading the registered Amendment 1 text
does not take. A3b itself passed 6/6.

Pre-registration: `2026-10-16-consumer-draft-4d-round2-prereg.md` (D1-D6, V1-V5, Amendment 1, Addendum 1 = the freeze
hashes). Data: `data/2026-10-16-consumer-draft-4d-round2/`. Date 2026-10-16, `claude` 2.1.299, model `sonnet`
(resolved `claude-sonnet-5-5` in all 8 sessions), no API key (the child env had no `ANTHROPIC*` key; `claude` CLI only).

## 1. What was built (4 `.py` + 1 cases file, all new, nothing else touched)

| file | lines |
|---|---|
| `dogfood/contracts/draft_agent2_scorer.py` (A3b, A3b-strict, `allow_from`, `score2`; A1, A2, A4, A5, A7 imported from the frozen scorer) | 256 |
| `dogfood/contracts/draft_agent2_scorer_cases.json` (37 carried + 49 new cases, fixtures C-D2, C-S6, L-D2, L-S6) | 972 |
| `dogfood/contracts/draft_agent2_check.py` (case runner, `--scorer PATH`) | 120 |
| `dogfood/contracts/draft_agent2_mutants.py` (SM1-SM19, scratch copies only) | 215 |
| `dogfood/contracts/draft_agent2.py` (runner: `--write-freeze`, `--rescore-round1`, `--dry-run`, sessions) | 533 |

`ruff check` and `ruff format --check` clean on the 4 `.py`. The only edit outside these files is the append of
Addendum 1 (freeze hashes) to the pre-registration, as D5/section 7 prescribe.

## 2. Order followed

case table (86/86) -> scorer mutants (19/19) -> freeze (sha256 of every frozen file, 17:44:19Z) -> Addendum 1 ->
development re-score of round 1 -> dry-run (argv check) -> fresh exercise (17:55:51Z to 17:59:10Z) last. Before the
freeze the runner pipeline was smoke-tested only in a scratch copy with a fake `claude` and **synthetic answers**
(round-1 transcripts with the answer replaced), so no real round-1 answer was scored before the freeze. The only
pre-freeze uses of round-1 data were the ones the pre-registration prescribes: the flagged sentences of the frozen A3
(G1), the two ablation answers (G2), and `allow_from` over the transcripts of sonnet-WD2-1 / sonnet-WS6-3 (L-D2, L-S6).

## 3. Case table (V1) and scorer mutants (V2), development

`cases.txt`: **86/86** (round-1 `draft_agent_check.py` 37/37). Groups: G1 7/7, G2 6/6, G3 10/10, G4 10/10, G5 7/7,
G6 3/3, G7 2/2, G8 4/4, carried a1 4/4, a2 9/9, a3 6/6 (now scored by A3b with an empty allow-list), a4 7/7, a5 6/6,
a7 5/5. Each G3-G7 fail case fails for the intended claim (checked by printing `invented`: `#9`, `R7`, `202`,
`1a2b3c4d`, `418`, `next_token`, `nxt`, `R2`, `0db033af`, `a0db03`, `299`, `green`, `linked_question_fix`, `state_x`).
Both fixtures satisfy the check script (L-D2 / L-S6 contain `contract_check` and `ok`, none of the G3-G7 fail tokens).

`mutants.json` / `mutants.txt`: control 86/86; **19/19 killed with a named catcher among the mismatches**.

| id | mutant | catcher cases hit (of named) | mismatches |
|---|---|---|---|
| SM1 | allow-list removed (tools, tokens, hex) | G4-1,2,3,4 (of G4-1..6) | 6 |
| SM2 | tool names not allowed | G4-1 | 3 |
| SM3 | verdict tokens not allowed | G4-2 | 2 |
| SM4 | commit-id rule removed | G4-3 (of G4-3,4) | 3 |
| SM5 | any 7+ hex accepted | G3-4 | 2 |
| SM6 | hex prefix -> substring | G3-9 | 1 |
| SM7 | import numbers unchecked | G3-1, G4-7 | 4 |
| SM8 | rule ids unchecked | G3-2, G3-8 | 3 |
| SM9 | statuses unchecked | G3-3, G3-5, G7-2 | 5 |
| SM10 | fields unchecked | G3-6 | 10 |
| SM11 | quoted-list extraction removed | G3-7 | 1 |
| SM12 | proposal filter removed (round-1 negation) | G5-2, G5-3 | 4 |
| SM13 | negation anywhere voids | G6-2 | 1 |
| SM14 | negation ignored | G6-1 | 2 |
| SM15 | contract-surface gate removed | G7-1 | 1 |
| SM16 | tokens from every string value | G8-2 | 1 |
| SM17 | noun guard removed | G5-1 | 1 |
| SM18 | tools/tokens from non-lattice tools | G8-1, G8-4 | 2 |
| SM19 | rule ids from corpus text only | G4-6 | 1 |

G1 alone kills no mutant, as predicted. The coding produced no edge case that changed a case verdict or the scorer's
rules after the case table passed (the first run was 86/86); the only pre-freeze edits after the first full run were
`ruff format`/line-length fixes (mutant anchors re-verified, 19/19 re-run).

## 4. Development re-score of round 1 (V3), no model, report only

`rescore-round1/rescore.json|txt`. W-D2 and W-S6 rebuilt once each; the rebuilt draft text equals every stored
`draft-text.txt` (10/10, none NOT RESCORED); stored answers equal the transcripts' (10/10); A1, A2, A4, A5, A7 equal the
stored `score.json` (10/10); the recomputed frozen A3 equals the stored A3 (10/10).

| set | session | A1 | A2 | A3b | A4 | A5 | A6 (stored) | A7 | frozen A3 | A3b-strict | every item |
|---|---|---|---|---|---|---|---|---|---|---|---|
| run 2 | sonnet-WD2-1 | ok | ok | ok | ok | ok | ok | ok | FAIL | ok | ok |
| run 2 | sonnet-WS6-1 | ok | ok | ok | ok | ok | ok | - | ok | ok | ok |
| run 2 | sonnet-WD2-2 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| run 2 | sonnet-WS6-2 | ok | ok | ok | ok | ok | ok | - | ok | ok | ok |
| run 2 | sonnet-WD2-3 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok |
| run 2 | sonnet-WS6-3 | ok | ok | ok | ok | ok | ok | - | FAIL | ok | ok |
| run 2 | ablation-WD2-1 | - | FAIL | ok | ok | ok | ok | FAIL | FAIL | ok | FAIL |
| run 2 | ablation-WD2-2 | - | FAIL | ok | ok | ok | ok | FAIL | FAIL | ok | FAIL |
| partial run 1 | sonnet-WD2-1 | ok | ok | ok | ok | ok | ok | ok | FAIL | ok | ok |
| partial run 1 | sonnet-WS6-1 | ok | ok | ok | ok | ok | ok | - | FAIL | ok | ok |

As predicted: run-2 tool sessions 6/6 every item with A3b (frozen verdict 4/6), ablation A3b 2/2 (A2 0/2, A7 0/2
unchanged), partial run 1 2/2. Not evidence (section 0). One finding: round-1 sonnet-WS6-1 (run 2) made one denied
`Bash` call (`<tool_use_error> No such tool available: Bash`), so the Amendment 1 sentence "none occurred in round 1" is
true only of executed calls; under the literal rule that round-1 session would also have failed.

## 5. Fresh exercise (V4), the scored result

Argv identical to round 1 for the same world in all 8 sessions (`argv_equals_round1` true after masking the
`mcp.json` path); every world unchanged (A6 8/8); caps respected (per session <= USD 0.50 and <= 8 turns; first three
USD 0.54; total USD 1.21; counted tokens 221,063 of 400,000). No usage or rate limit, no infra failure, nothing NOT RUN.

| session | world | USD | counted tokens (incl. cache reads) | turns | wall s | lattice tools (`!` = error) | non-lattice calls |
|---|---|---|---|---|---|---|---|
| r2-sonnet-WD2-1 | W-D2 | 0.3423 | 76,265 (363,124) | 6 | 22.8 | project_list, contract_check, linked_question_list, linked_question_draft, code_links! | none |
| r2-sonnet-WS6-1 | W-S6 | 0.1028 | 12,629 (438,673) | 8 | 23.9 | project_list, contract_check, linked_question_list, linked_question_draft, code_links!, project_status | **Bash (denied)** |
| r2-sonnet-WD2-2 | W-D2 | 0.0921 | 11,954 (363,181) | 6 | 24.4 | project_list, contract_check, linked_question_list, linked_question_draft, code_links! | none |
| r2-sonnet-WS6-2 | W-S6 | 0.0795 | 11,027 (287,893) | 5 | 17.1 | project_list, contract_check, linked_question_list, linked_question_draft | none |
| r2-sonnet-WD2-3 | W-D2 | 0.0905 | 11,804 (363,031) | 6 | 23.5 | project_list, contract_check, linked_question_list, linked_question_draft, code_links! | none |
| r2-sonnet-WS6-3 | W-S6 | 0.0936 | 12,316 (363,305) | 7 | 25.2 | project_list, contract_check, linked_question_list, linked_question_draft, code_links!, project_status | none |
| r2-ablation-WD2-1 | W-D2 | 0.3362 | 74,729 (358,938) | 7 | 23.8 | project_list, contract_check, linked_question_list, code_links!, project_status | **mcp__claude_ai_Claude_Docs__read (denied)** |
| r2-ablation-WD2-2 | W-D2 | 0.0770 | 10,339 (285,737) | 6 | 24.4 | project_list, contract_check, linked_question_list, model_get!, code_links! | none |

The two high-cost sessions (WD2-1 and ablation-1) each created about 74k cache tokens in the first request (a cold
cache; the other six created 9-11k). Both denied non-lattice attempts were refused by `dontAsk` + the allow-list; none
executed. The Docs read is the first attempt of a claude.ai connector tool in either round (the connectors are loaded
despite `--setting-sources ""`, 94-95 tool names per init record).

Items (tool sessions; `-` = not applicable). **A3b | A3b-strict | frozen A3** side by side:

| session | A1 | A2 | A3b | A3b-strict | frozen A3 | A4 | A5 | A6 | A7 | non-lattice call | every item (registered, literal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r2-sonnet-WD2-1 | ok | ok | ok | ok | FAIL (`ef17cf88`, `contract_check`, `ok`) | ok | ok | ok | ok | no | **pass** |
| r2-sonnet-WS6-1 | ok | ok | ok | ok | FAIL (`contract_check`, `ok`) | ok | ok | ok | - | **Bash denied** | **FAIL (auto)** |
| r2-sonnet-WD2-2 | ok | ok | ok | ok | FAIL (`raise_for_status`, `contract_check`) | ok | ok | ok | ok | no | **pass** |
| r2-sonnet-WS6-2 | ok | ok | ok | ok | FAIL (`a0db033a`) | ok | ok | ok | - | no | **pass** |
| r2-sonnet-WD2-3 | ok | ok | ok | ok | ok | ok | ok | ok | ok | no | **pass** |
| r2-sonnet-WS6-3 | ok | ok | ok | ok | FAIL (`contract_check`) | **FAIL** | ok | ok | - | no | **FAIL** |
| **tool sessions** | 6/6 | 6/6 | **6/6** | 6/6 | 1/6 | 5/6 | 6/6 | 6/6 | 3/3 | 1 of 6 | **4/6** |
| ablation (info) | - | 0/2 | 2/2 | 2/2 | 1/2 | 2/2 | 2/2 | 2/2 | 0/2 | 1 of 2 | - |

Frozen round-1 verdict (frozen A3 in place of A3b, no non-lattice rule): 1/6.

## 6. Verdict against the pass rule (>= 5 of 6 tool sessions satisfy every applicable item, and A6 8/8)

- **Registered reading (Amendment 1: any non-lattice tool call is an automatic fail): 4 of 6 -> pass rule NOT MET.**
  A6 is 8/8. The two failing sessions: WS6-1 (every item ok; one denied `Bash` attempt) and WS6-3 (A4).
- Information, same data, denied attempts not counted as calls: 5 of 6 -> would be met (`summary.json`
  `pass_rule_met_ignoring_denied_attempts`). This is not the registered verdict.
- A3b: 6/6 (the item this round was built to fix); A3b-strict 6/6; frozen A3 1/6. The A3 fix works on fresh answers,
  but the pass rule fails on two other things (a denied tool attempt, an A4 miss).
- WS6-3's A4 miss is a **scorer false positive** (post hoc, section 8): the sentence "The draft explicitly rules out
  removing a claim to make the check pass." is flagged as advice to remove a claim. A4 is the frozen round-1
  function, unchanged by design (D1), and the session set is not re-scored.

Predictions (V4) against outcome: pass rule met -> **not met (4/6)**; every item 5-6/6 -> 4/6 (5/6 ignoring the denied
call); A1 5-6/6 -> 6/6; A3b 5-6/6 -> 6/6; frozen A3 3-5/6 -> **1/6** (wrong: five of six answers cite `contract_check`,
`ok`, a pin commit or, new, `raise_for_status`); ablation A2 0-1/2 -> 0/2, A7 0-1/2 -> 0/2, A3b 1-2/2 -> 2/2; per tool
session USD 0.06-0.15 -> 5 of 6 inside (WD2-1 0.34); 5-7 turns -> 5 of 6 inside (WS6-1 8); 9-15k counted tokens ->
5 of 6 inside (WD2-1 76k); total USD 0.6-1.3 -> 1.21; 80-200k counted -> 221k (above); resolved model as predicted;
non-lattice attempts 0-2 -> 2 (both denied).

The orchestrator's read of the 8 answers (per-item agreement) is still due; my own reading found the two scorer
findings below and no answer that proposes an invented identifier. The answers are in `agent/*/answer.txt`.

## 7. Hygiene (V5)

`git diff --exit-code bb3ff0c -- lattice/ tests/ docs/results/data/2026-10-15-consumer-draft-4d/` and the round-1 files
(`draft_agent.py`, `draft_agent_scorer.py`, its cases, `draft_agent_check.py`, `agent_scorer.py`, `agent_probes.py`,
`draft_probes.py`, `contracts_mutants.py`): empty. `draft_agent_check.py` 37/37. `pytest tests/contracts tests/mcp -q`:
697 passed, 1 skipped in the worktree and at `bb3ff0c` (a `git archive` in scratch; two runs of the baseline, 337.66 s
and 330.17 s). Frozen hashes at run time equal the freeze (the runner refuses otherwise; `summary.json` `hashes_now`).
Job-scheduler*, Streak and the main checkout: no file written (scratch clones only; `git status` of the main checkout
unchanged). Commands and wall times: case table 0.04 s; mutants 0.88 s; freeze a few seconds; re-score 4.3 s; fresh
exercise 3 min 18.7 s (198.5 s in `summary.json`); pytest about 5.6 min each.

```sh
uv run python dogfood/contracts/draft_agent_check.py                                  # 37/37
uv run python dogfood/contracts/draft_agent2_check.py | tee $D/cases.txt              # 86/86
uv run python dogfood/contracts/draft_agent2_mutants.py --scratch $S/m --out $D/mutants   # 19/19
uv run python dogfood/contracts/draft_agent2.py --write-freeze --out $D/agent
uv run python dogfood/contracts/draft_agent2.py --rescore-round1 --scratch $S/r --out $D/rescore-round1
uv run python dogfood/contracts/draft_agent2.py --dry-run --scratch $S/d --out $D/agent
uv run python dogfood/contracts/draft_agent2.py --scratch $S/a --out $D/agent         # TMPDIR fresh and empty
```

(`uv run` was replaced by the venv's `python` with `UV_NO_SYNC=1`; the dry run's artifacts in `$D/agent` were
overwritten by the real run.)

## 8. Post hoc (nothing here changes a frozen file or re-scores the session set)

1. **A4 false positive, WS6-3** (frozen round-1 A4): `REMOVE` matches "removing", `CLAIMNOUN` matches "claim", and
   `NOT_ADVICE` has no pattern for "rules out". The sentence argues against removal. The registered verdict stands; a
   later round could extend A4 as A3 was extended.
2. **Non-lattice rule, two readings.** Amendment 1 says any non-lattice tool call is an automatic fail and that none
   occurred in round 1; round 1 had one denied `Bash`. I applied the literal text (a denied attempt fails the session)
   and reported the other reading beside it. The deciding fact is one session (WS6-1: `Bash` denied, all items ok); the
   orchestrator decides which reading the program keeps. The same session slot (WS6-1) tried `Bash` in both rounds,
   so a denied shell attempt is a recurring behaviour of this prompt, not noise.
3. **Frozen A3 widened its failure set**: besides `contract_check`/`ok`/commit prefixes, a code identifier in code
   advice (`raise_for_status`, WD2-2) now trips it; A3b exempts it by the contract-surface gate (G7 behaviour).
4. **A3b looseness unobserved here**: A3b-strict (status, rule id and commit on every sentence) agrees with A3b on all
   10 fresh answers, so no invented status, rule id or commit hid in a descriptive sentence this round.
5. **Cost outliers** are cold-cache first requests (about 74k cache-creation tokens), not longer sessions; the counted
   token total (221k) therefore sits above the predicted 80-200k although every session's turns and output were ordinary.
6. The two pytest runs in the first batch printed the identical duration (337.66 s) in both files, an unexplained
   coincidence; the baseline was re-run alone (330.17 s, same counts), which is the figure of record.

## 9. What this does and does not show

Shows: A3b removes the round-1 A3 false positives on fresh answers (6/6, strict 6/6) with 86 cases and 19 killed mutants
behind it, and the frozen A3 would still have failed 5 of 6 (n = 6, one model, one prompt, scorer author = prompt
author). Does not show: that the pass rule is robust (it fell on a denied tool attempt and a frozen-A4 false positive,
both outside A3), that owners act on drafts, other models, or a spec not written by the matcher's author.

## Orchestrator verification and ruling on the pass-rule reading
Independent re-run: the 86-case table 86/86; the scorer, cases, check and runner sha256 equal the freeze file;
the 19 scorer mutants are killed; ruff clean; `git diff bb3ff0c -- lattice tests` and the round-1 data directory
are empty. I read the two sessions that decide the verdict: r2-sonnet-WS6-3's answer states that the draft
'explicitly rules out removing a claim to make the check pass', which the round-1 A4 pattern matches as a
removal proposal (a false positive of the inherited A4, as the builder says); r2-sonnet-WS6-1's answer is
correct in every item but the session made one denied Bash attempt.
**Ruling.** The registered reading stands: Amendment 1 made any non-lattice tool call (including a denied
attempt) an automatic session fail, so the pass rule is **NOT MET (4 of 6)**. The alternative reading (denied
attempts not counted, 5 of 6) is reported beside it as `pass_rule_met_ignoring_denied_attempts` and is not the
program's verdict. What the round shows, honestly: the corrected claim-based scorer (A3b, and the stricter
A3b-strict) passes all 6 tool sessions on the thing it was built to fix (invented claims); the remaining failures
are an inherited A4 false positive and a recurring Bash probe that the guard denied. The scorer was designed
after reading round 1, so only these 6 fresh sessions count, and n = 6 is small. Without the tool (n = 2) no
session named an import number or a rule (A2 0/2, A7 0/2), unchanged from round 1.
