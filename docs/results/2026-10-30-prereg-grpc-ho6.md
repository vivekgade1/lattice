# gRPC held-out round HO6: do the codes, tables and auth readers generalise to an unseen grpc-go app? Pre-registration

**Status: REGISTERED, NOT RUN; needs owner approval of the budget.** Written 2026-10-31 by node T2P of the 2026-10-30 agent graph
(branch `nc/t2p-preregs`, from the integration tip 3cc77f7). No model was called and no runner exists yet. The author caps in section 8
are the owner's decision; no figure here is approved.

## 1. Why a new round

GA2 (commit 8f97724, merged into the integration tip from branch `nc/go-ga2`) changed how the Go extractor reads gRPC status codes:
string-keyed constructor maps, error parameters bound per call, pass-through translators, interceptors that are not literal auth gates, and
services registered from a descriptor table (`lattice/gocheck/src/goextract_grpc_icodes.go`, `lattice/gocheck/src/goextract_grpc_flow.go`,
tests `tests/grpc/test_codes6.py` over `tests/grpc/fixtures/codes6`). Its diagnosis and dev numbers are in
`docs/results/2026-10-30-grpc-codes-diagnosis.md`: on SG, CODES recall 0.000 -> 1.000 and precision 0.000 -> 0.868; on HO5, 0.675 -> 1.000
and 0.800 -> 0.954. **Every one of those gaps was found by reading SG and HO5, and fixed on them.** They show the diagnosed gaps are closed
on those two apps, not how the extractor does on an app nobody has read. Only a fresh held-out app can answer that.

What did not change since round 5: the auth readers (GA2 left them as they were: SG and HO5 stay all-unclear, round 5's
`docs/results/2026-10-18-grpc-round5-scored-results.md`), the table reader (SG TABLES 0 of 9: DDL in an embedded `.sql` file applied to
MySQL; there is no reader for embedded or MySQL DDL), observed reconciliation and flows.

## 2. Question and hypotheses

**Primary question (CODES).** On an unseen grpc-go app written blind, does the extractor reach round 5's CODES gate (micro recall >= 0.70,
precision >= 0.90), which HO5 missed blind (0.675 / 0.80)?

| id | hypothesis | basis |
|---|---|---|
| HC | GA2's mechanisms are generic: CODES passes on HO6 | GA2's fixtures were hand-written per mechanism; risk: the author picks idioms GA2 never saw |
| HR | RPC and SAFE improve over HO5 (0.733, 1 confident-wrong) through the descriptor-table registration reader | HO5-1 in the diagnosis |
| HA | AUTH stays unclear: 0 wrong, but the unclear share stays above 0.50 | no auth change since round 5 |
| HT | TABLES is bimodal: pass when the schema is a Go literal or in the harness, 0 when it is embedded DDL or a migration tool | SG 0/9 cause |

## 3. Data status (binding)

Development data, never the basis of a blind claim: **HO5** (`~/Documents/g5-ho5-author`), **SG** (`~/Documents/g5-sg-author`), **S4**, the
**J** apps, the **chat round-2** questions, **D1**, **H1**, **H2**, **Streak** (every revision), **Cyclops**, and for gRPC also toolshed,
harborline, kilnworks, deskd and the notes app N. HO5 and SG may serve as runner self-test stand-ins only (section 6). Only HO6, written after
the freeze, backs a blind row. Once HO6 is scored it is dev data too.

## 4. The HO6 app contract

**Brief** (`BRIEF.md`, new, in the freeze): round 5's Appendix C (`docs/results/2026-10-18-grpc-round5-prereg.md`) as the base, with the
same minimums (>= 3 registered services, >= 15 unary rpcs, >= 1 server-streaming rpc, >= 1 rpc answered `Unimplemented`, >= 1 declared but
never registered service, >= 2 users of different roles, >= 2 request fields naming a seeded row by a natural key, >= 6 truth flows of >= 3
steps with links, a login rpc), pure-Go SQLite, `cmd/replay`, `FILES.txt`, `freeze.sha256` at the app root, and the same neutral sentences
(wire each service by a different idiom, report errors by a different idiom per service, authenticate in an idiom of the author's choice, put
the schema wherever a mid-sized team would, copy no example). **Changes for HO6:**
1. **Domain exclusion:** not marinas, harbours or shipping; habits or streaks; notes; tool rental; help desks; kilns or pottery; beekeeping;
   job scheduling; image viewers. (Every domain an earlier author used.)
2. **No idiom named**, as round 5: none of round 5's forbidden list, and none of GA2's mechanisms by name (constructor maps, comma-ok
   lookups, `errors.As`, translators, descriptor tables, interceptors that test the method).
3. `IDIOMS.md` is written to `../sealed/IDIOMS.md`, outside `app/` and outside the app freeze, and read by nobody until HO6 is scored
   (round 5's author left it in the root, a disclosed deviation).
4. The author records in `sealed/IDIOMS.md` where the schema lives, using truth/5's `tables[*].ddl` values.

**Truth schema:** `lattice.heldout.truth/5` unchanged (round-5 prereg Appendix B), so `dogfood/grpc_truth5.py` and the frozen scorers read it.
**Harness API:** round-5 Appendix A unchanged. **Contract check:** `dogfood/grpc_contract5.py` through a frozen wheel (the round-5 `contract5`
console command); every line must print `ok`; at most one fix round (section 7), each printout disclosed.

## 5. Rows and gates (copied from round 5, section 10.5; fixed now)

| id | metric | gate | gated? |
|---|---|---|---|
| HO6-CODES | micro recall / precision (implemented rpcs, `OK` and `unimplemented_stub` codes dropped, `grpc_scorer4.score_codes`) | **>= 0.70 / >= 0.90** | gated, primary |
| HO6-RPC | recall / precision, state included | **>= 0.90 / >= 0.95** | gated |
| HO6-SAFE | confident-wrong (S8) count | **0** | gated |
| HO6-AUTH | S7 | **0 wrong and unclear share <= 0.50** | gated |
| HO6-TABLES | table recall; `tables_source` reported | **>= 0.90** | gated |
| HO6-DET | extract, model, reconcile: 10 processes, random `PYTHONHASHSEED`, two roots | **10/10 identical** | gated |
| HO6-NW | writes under the app root; `claude` calls during the scored run | **all 0** | gated |
| HO6-REC | as round 5 (coverage >= 0.95, agreement 1.00, ...) | reported against round 5's rule | reported |
| HO6-FLOWS | as round 5 (verified >= 1, 0 vbf, edge recall >= 0.30, coverage >= 2) | reported against round 5's rule | reported |
| HO6-SQLT | S14 | none | reported |

REC and FLOWS are reported, not gated, by decision now: no work since round 5 targets them, and a near-certain miss there would say nothing
about GA2. **Vacuous rows (handoff `docs/HANDOFF-2026-10-24.md` section 5, item 2c):** a precision over an empty extracted set, or a recall
over an empty truth set, is **not measured** and counts as a gate failure, never a pass; a row fails, not passes, when the extract crashes.

**Held-out mutants (new; H2 discipline, section 9.3 of `docs/results/2026-10-23-dotnet-h2-prereg.md`).** A separate writer W writes 12
patches of HO6 with `MUTANTS.md` declaring each one's truth delta: 6 change the status code one rpc answers at one site, 3 add or remove one
table write, 3 flip one rpc between Public and Member. The control (unpatched) extract must equal the scored extract byte for byte first.

| id | rule | gated? |
|---|---|---|
| HM6-CODES | the extract's code delta on the named rpc equals the declared one: **>= 5 of 6** | gated |
| HM6-TABLES | write delta equals the declared one: **>= 2 of 3** | gated |
| HM6-AUTH | **0** auth answers contradicting the declared post-patch truth (an `unclear` is not tracked, and not wrong) | gated |

## 6. Runners (new; written after this prereg, before the freeze)

`dogfood/grpc_probes6.py` (new) and `dogfood/grpc_mutants6.py` (new) import the round-5 and round-4 row and scoring functions unchanged
(`dogfood/grpc_probes5.py` `load_app5`, `dogfood/grpc_probes4.py` `rpc_rows4`, `dogfood/grpc_scorer4.py`, `dogfood/grpc_scorer5.py`,
`dogfood/grpc_truth5.py`) and add only their row table, the vacuous-row rule and the mutant judging. They follow the H2 runner rules
(H2 prereg sections 6-8): kinds `scored`, `rerun`, `verify`; the freeze and the inputs manifest are verified **before** the ledger is
touched (a refused launch writes to `refused.jsonl` only); self-hash of every freeze entry, the running script included, and a must-list
of every file under `lattice/`; `--out` and `--scratch` are required on every scored and verify line (template
`docs/design/templates/blind-round-runner-section.md`); every finding is stored whole (the SG-CD lesson: round 5 kept only 20 rule names,
`docs/results/2026-10-30-sg-cd-diagnosis.md`). Self-tests run on copies of HO5 and SG only, never write the ledger, and must catch an
injected NOT RUN and an edited freeze entry.

## 7. Author and isolation protocol

- **A (app author):** one new agent, not any earlier author, builder, verifier or this file's writer; `claude` CLI only, `env -u
  ANTHROPIC_API_KEY`, **sonnet** (HO5's author was opus: a disclosed confound), dispatched only after the freeze commit, in the fixed empty
  directory `~/Documents/g6-ho6-author` (new), with `BRIEF.md`, `HARNESS_API.md`, `TRUTH_SCHEMA.md` and the frozen wheel, the H2 flag set
  (no MCP server, `--setting-sources ""`, fixed session id, Bash/Read/Write/Edit/Glob/Grep only). It may use the Go module proxy and public
  grpc-go documentation.
- **W (mutant writer):** a separate session after the contract check passes, in `~/Documents/g6-ho6-w` (new), with `BRIEF-W.md` and the
  app archive only. Patches named exactly as the mutants runner reads them, each applying with `patch -p1` and building.
- **Audit:** `scripts/audit_transcript_generic.py <transcript> --root <dir> --forbid ~/Documents/g5- --forbid ~/Documents/cyclops --forbid ~/Documents/job-scheduler`
  (built in: the Lattice checkouts, `~/.claude`, Streak, the J apps, `~/Documents/dn-*`) on both
  transcripts. One forbidden or outside item voids the deliverable **after the dispatching node confirms it by reading the call** (two honest
  H2 runs were voided on false positives by the older auditor, `docs/HANDOFF-2026-10-24.md` section 2.6); review items are listed, not voiding.
  A voided app is never scored; the caps do not cover a second author, so the owner decides.
- **Diversity floor**, checked by the orchestrator on `sealed/IDIOMS.md` alone: >= 3 distinct wiring lines for >= 3 services, >= 3 distinct
  error lines. A miss is recorded before scoring, not fixed.

## 8. Predictions (before any runner exists)

| row | prediction | p(pass) |
|---|---|---|
| HO6-CODES | recall 0.45-0.95 (point 0.75), precision 0.75-1.00 (point 0.90) | 0.35 |
| HO6-RPC | recall 0.60-1.00 (point 0.87), precision 0.70-1.00 (point 0.90) | 0.3 |
| HO6-SAFE | 0-2 (point 0) | 0.6 |
| HO6-AUTH | 0 wrong (p 0.8); unclear share 0.50-1.00 (point 0.9). Expected to stay unclear | 0.1 |
| HO6-TABLES | bimodal: 0.90-1.00 if the schema is a Go literal or in the harness, 0.0 if embedded DDL or a migration tool | 0.55 |
| HO6-DET / HO6-NW | 10/10; all 0 | 0.95 / 0.95 |
| HM6-CODES / HM6-TABLES / HM6-AUTH | 3-6 of 6 (point 5); bimodal as TABLES; 0 wrong | 0.5 / 0.5 / 0.85 |
| all 7 gated HO6 rows | | 0.03-0.08 |
| the app is scored at all (no void, no cap stop, no crash on both permitted runs) | | 0.7 |

## 9. Budget (the owner's decision; **nothing approved**)

| item | model | cap | basis |
|---|---|---|---|
| A (dispatch + one fix round) | sonnet | **USD 3.00** | H2 A3 cap (H2 prereg 9.1); a whole gRPC service with truth is near it (P(cap hit) about 0.35) |
| W | sonnet | **USD 1.50**, and <= 4.50 minus A's spend | H2 W3 cap |
| scored runs, verify | none (static extraction and the app's own Go replay) | USD 0 | |
| **round ceiling** | | **USD 4.50** | the remainder of earlier approvals is not assumed |

**Stop rule (H2 prereg 9.4):** a call whose result is not `success`, or whose cost reaches its cap, stops the round; the deliverable is not
contract-checked or used; nothing is re-dispatched; the owner decides. A CLI usage limit is the same kind of stop. One `spend.jsonl` line per call.

## 10. Freeze list and ordering

**Order (binding):** this prereg committed alone -> runners and their self-tests -> `BRIEF.md`, `BRIEF-W.md`, `HARNESS_API.md`,
`TRUTH_SCHEMA.md`, dispatch scripts -> self-tests and the refusal test pass -> freeze file + empty ledger committed -> A -> audit ->
contract check (one fix round) -> W -> audit -> inputs manifest committed -> one scored probes run -> one scored mutants run -> independent
verify -> results. No amendment to this file after the freeze file exists.

**Freeze file** `docs/results/data/<run date>-grpc-ho6/freeze.sha256` (new): this prereg; the two new runners and every runner they import
(listed in section 6); `dogfood/grpc_contract5.py` and the wheel; the brief directory and dispatch scripts; `scripts/audit_transcript_generic.py`;
the self-test stand-in archives; every tracked file under `lattice/` (including `lattice/gocheck/src/*.go`). **Inputs manifest** (after W):
`ho6.tgz`, `ho6-truth.json`, `mutants/MUTANTS.md` and the 12 patches, byte copies checked with `cmp`. Every entry is a committed, tracked file.

## 11. Risks, and what this cannot show

1. **n = 1 app.** A pass says GA2 reached one more app; a miss localises a new idiom. Neither is a rate.
2. **Author model and cost.** Sonnet at USD 3.00 may stop before the truth is complete (then nothing is measured).
3. **The brief cannot force unfamiliar idioms**; the diversity floor is the only check, and the author may pick idioms GA2 happens to cover.
4. **Truth errors.** The contract check and `cmd/replay` guard flows and codes; a wrong code in `truth.json` that the replay never exercises
   would score as an extractor miss. The verifier lists every CODES disagreement with its `code_sites` entry so a truth error is visible.
5. **TABLES depends on one author choice**; the result is reported with `tables_source` so it is not read as a general rate.
6. **Auth** is predicted to fail; it is gated anyway because round 5 gated it, and an unchanged miss is information.

## 12. Launch checklist

1. Owner approves A USD 3.00 and W USD 1.50 (ceiling USD 4.50) in a dated note committed before the freeze.
2. Runners, briefs and dispatch scripts committed after this file; self-tests and the refusal test pass (logs kept).
3. Freeze file and empty ledger committed; from a **fresh clean worktree** of that commit, `git status --ignored --short` prints nothing and
   `dogfood/grpc_probes6.py --verify-freeze-only` passes.
4. `~/Documents/g6-ho6-author` absent or empty; dispatch A; audit; contract check; at most one fix round.
5. Diversity floor checked on `sealed/IDIOMS.md`; W dispatched; audit; inputs manifest committed (commit id and sha256 into the results).
6. One scored run of each runner with explicit `--out` and `--scratch`; a rerun only after `crash_before_metric` with unchanged bytes.
7. Independent verifier (opus) re-runs both with `--kind verify` from a second clean worktree; any differing verdict is reported.
8. Results: every row and prediction scored once; misses localised post hoc in a separate section, never tuned.
