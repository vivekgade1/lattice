# Spike 3 results: a self-improving Lattice from logged usage signals

Date: 2026-10-12. Branch `np-spike3` (worktree `lattice-np3`, from `next-phases` 396bb91).
Pre-registration: `docs/results/2026-10-12-spike3-prereg.md` (written before any code or probe).
Raw data: `docs/results/data/2026-10-12-spike3/`. CLI backend only, no API key, no model call
anywhere in this spike (the ranker has no LLM). Nothing in `~/Documents/streak` or
`~/Documents/lattice` was modified; Streak was read and cloned into scratch.

## Summary

| Probe | Pre-registered criterion | Result |
|---|---|---|
| I-R replay | top 3 contain layer X at level gates AND first-baseline cost (>= 2 of I-R's top 3); none of top 5 closed | **Partly met, exactly as predicted.** I-02 (#2) and I-01 (#3, as whole level-gate time) are in the top 3; "layer X" cannot be named from old records; 2 of the top 5 (#1, #4) are events I-R attributes to the closed I-05 |
| I-2 accounting | layer_seconds, cause, human approve/run, node's own wall: all four, 3 of 3 runs | **Pass, 3 of 3.** Cause `spec_unsatisfiable` (predicted) |
| I-3 one improvement, n=1 | direction right every run, saving within 25% of the logged prediction | **Pass.** X 119.51 s -> 58.50 s (-51.0%); predicted saving 71.71 s, measured 61.01 s (error 14.9%) |
| I-4 refusal | lint proposal `open`, checker proposal `needs_owner`, in tests and one CLI run | **Pass** |

Verification: pytest 1313 passed, 5 skipped (the task's suite list); dogfood `check.sh` PASS, 403
cases (393 before, +10 for the new scenario); restart drill A, B, C, F pass; mutants 6 of 6
caught (control passes); ruff check clean on every changed file, ruff format clean on every
created file.

## What was built

1. **Studio closes the signal gaps** (I-R gaps 1, 2, 3, 5, 7; items (a)-(e) of the task):
   - (a) `layer_seconds {A,B,C,D,X}` on a gate run, copied as written from the
     `timings.layers` of the JSON report the gate (re)wrote (`[rebuild] findings_json`), attributed
     to the gate during which the report changed; on round and integration `gates[]`,
     `level_runs[].gate` (and its `gates[]`), the plan's `baseline`, per-tip result docs. Absent
     when no report was written. Layer X gates also get `x_repeats` (the report's "each up to N
     time(s)" note; else the most tries of a race; else `--x-repeats` in the command).
   - (b) `cause` on the build record (and its last timeline entry), the plan node (and its
     timeline entry) and the `node_ended` event: the ten codes of the task, computed by
     `lattice/improve/signals.py:build_cause`. Plan-level failures (start failure, merge conflict,
     level-gate regression, land failure) get theirs from `plan.py`.
   - (c) Human interventions: plan approve, revise, run/resume/retry/land, stop, forget as `human`
     log events (`actor: "owner"`), and the plan's timeline entries for those actions carry
     `actor: "owner"`; change accept/reject and spec sync (API v1.12 route) as log events.
   - (d) The metrics hook's ledger line uses the build's own `usage.wall_s` once the build is done
     (a plan node's hook runs at level end; it used to report the time until then).
   - (e) `signal_key` `<layer>:<class>` on every round finding (JSON findings in
     `areas.findings_from_json`, text findings in `_gate_round`, Studio's own findings).
2. **`lattice/improve/events.py`**: append-only `<state dir>/events.jsonl`, `v: 1`, the seven
   kinds, fsync per append, writes never raise (a warning instead), a tolerant reader.
3. **`lattice/improve/analyze.py`**: the I-R ranker (no LLM), the `improvements.jsonl` ledger,
   at most N proposals, protected targets `needs_owner`.
4. **`lattice/improve/importer.py` + CLI** `python -m lattice.improve report|import-plan|ledger`
   and `lattice improve ...`; exit 0 done, 2 cannot read.
5. **`GET /api/improve`** (API v1.15 section in `lattice/studio/API.md`), mock parity in
   `mock_server.py`, dogfood rules R85-R89 (R85-R87 checked by the new scenario
   `improvement_proposals_are_listed`; R88, R89 stated unchecked with pointers to tests), trace
   updated. **No web UI in this spike** (API, CLI and mock only).

### Files changed (lines)

Modified (`git diff --numstat`, +/-): `lattice/studio/rebuild.py` +186/-16, `plan.py` +91/-17,
`server.py` +37/-1, `mock_server.py` +43, `areas.py` +7, `API.md` +40/-1, `lattice/cli.py` +8/-1,
`dogfood/studio/REQUIREMENTS.md` +8, `dogfood/studio/spec.py` +36, `dogfood/studio/spec.trace.json`
+42/-1, `tests/studio/test_build_scope.py` +2, `tests/studio/test_rebuild.py` +1 (12 files, 501
insertions, 37 deletions). The two test edits add the new `signal_key` field to two exact-dict
expectations; nothing else in an existing test changed.

New: `lattice/improve/__init__.py` 20, `__main__.py` 173, `analyze.py` 718, `events.py` 221,
`importer.py` 279, `signals.py` 168; `tests/improve/test_analyze.py` 328, `test_cli.py` 103,
`test_events.py` 87, `test_import.py` 118, `test_signals.py` 149; `tests/studio/test_events.py`
366; `dogfood/improve_mutants.py` 225; the prereg 146; this document; data files.

## Blind results (against the pre-registration)

### I-R replay

`import-plan` of p2-final, p1, p3 (33 events; p2.json not imported: an earlier snapshot of p2),
the ledger closing I-05 `cause:spec_unsatisfiable`, I-06 `gate:node*:D`, I-07 `gate:level_full:*`,
I-08 `human:spec_sync`, then `report --top 5` (`data/.../ir-replay-report.txt`, `.json`,
`ir-replay-events.jsonl`, `ir-replay-improvements.jsonl`):

| # | Ranker | f x c = score | I-R item | Predicted |
|---|---|---|---|---|
| 1 | `cause:stopped_by_owner` (p3 account) | 1 x 2709.2 s = 2709.2 s | I-05 (closed) | yes, same |
| 2 | `gate:baseline_rerun:total` | 2 x 421.2 s = 842.4 s | I-02 | yes, same |
| 3 | `gate:level_regression:total` | 5 x 143.3 s = 716.3 s | I-01 without the X attribution | yes, same |
| 4 | `cause:other` (p2 account b4, sync b11) | 2 x 317.0 s = 634.0 s | I-05 (closed) | yes, same |
| 5 | `gate:baseline:total` | 2 x 148.4 s = 296.8 s | none | yes, same |

Closed and left out: `gate:level_full:total` (I-07), 2 x 423.2 s = 846.3 s. The hand prediction
in the prereg matched the ranker's output exactly (order, f, scores to 0.1 s).

Against the criteria: **"at least 2 of I-R's top 3": met on identity** (I-02 #2, I-01 #3).
**"Layer X at level gates": not met**: the plan records carry only whole-gate seconds (I-R gap
1), so the proposal is "attribute the time of level gates to layers" (`experiment`), not the X
change. **"None of the top 5 closed": not met**: #1 and #4 are the three events I-R attributes to
the closed I-05 (L012); an old record has no cause, and p3's stop was the owner's. I-03 (container
per gate) does not appear: container start is in `timings.setup`, which no record holds.

Why they differ from I-R's hand list: (1) **I-02 doubles**: the ranker's rule "a baseline on a
commit that already had a gate run" also catches p3's baseline (426.35 s at 941735104c, gated by
p2's level 1 at 02:02:35, 13 minutes earlier); I-R counted only p2's (f = 1, 416 s). (2)
**#5 is not an I-R item**: I-R judged the resume-3/4 baselines unavoidable (new tip after a spec
sync); the ranker has no such judgement. (3) **I-05's events stay open** because the cause the
fix addresses (`spec_unsatisfiable`) is not what the records can say (`other`,
`stopped_by_owner`).

### I-2 accounting

Three runs of the stub plan (`data/.../i2-runs.json`, `i2-run{1,2,3}-events.jsonl`); 14 events
each (6 gate runs: baseline, 3 + 1 node gates, 1 level gate; plan partial: t-dep and integration
blocked by the give-up):

| Run | layer_seconds = report timings | w-broken cause (node / build / event) | human events | r-ok wall: build = event = ledger; level wall | Pass |
|---|---|---|---|---|---|
| 1 | yes (node gates `{C, D}`, others all 5 layers) | spec_unsatisfiable x3 | approve, run (owner); timeline entries `actor: owner` | 0.35 s = 0.35 = 0.35; level 4.0 s | yes |
| 2 | yes | spec_unsatisfiable x3 | same | 0.28 = 0.28 = 0.28; 4.0 s | yes |
| 3 | yes | spec_unsatisfiable x3 | same | 0.28 = 0.28 = 0.28; 4.0 s | yes |

**Pass, 3 of 3.** The cause is `spec_unsatisfiable`, as predicted; `no_progress` also held (same
failing set three rounds running) and is lower in the precedence. Caveat: the gate is a
check-shaped fake (deterministic layer seconds); the "spec problem" line in it is produced by the
real `lattice.spec.preconditions.unestablished` on a real unsatisfiable `Scenario`, but the
real checker did not run in I-2 (it did in I-3).

### I-3 one improvement end to end (n = 1, labelled)

Three level gates on a scratch clone of Streak at a0679e9, through `Rebuilder.gate_round` on an
integration worktree with the level-gate command built as `_merge_level` builds it
(`scoped_command(first gate, cfg.level_gate_args, selection)`, 152 selected scenarios, real
`./check.sh` = real `lattice spec check`, MySQL in one `lattice-imp-mysql` container passed as
`LATTICE_MYSQL_URL`). The ranker's report on those 9 events (`data/.../i3-report-before.txt`):
**#1 "Fewer layer X repeats at level gates (regression): level_gate_args = ["--x-repeats", "2"]",
3 x 119.5 s = 358.5 s, change_set, studio.toml, open; expected: saving 71.7 s a gate (60%; x_repeats
5 -> 2)**, evidence the three logged `gate_run` events. The change was made in the clone's
`studio.toml` only (`data/.../i3-clone-studio-toml.diff`); the owner's merge into Streak was not
done. Three level gates after:

| | run 1 | run 2 | run 3 | mean (sd) |
|---|---|---|---|---|
| X before (s) | 119.75 | 119.31 | 119.47 | 119.51 (0.22) |
| X after (s) | 58.56 | 58.51 | 58.44 | 58.50 (0.06) |
| D before / after (s) | 22.38 / 22.36 | 22.81 / 22.22 | 22.36 / 22.34 | 22.52 / 22.31 |
| gate total before / after (s) | 143.76 / 82.54 | 143.81 / 82.30 | 143.59 / 82.34 | 143.72 / 82.39 |

Predicted saving (from the logged signal alone): 119.51 x (1 - 2/5) = 71.71 s. Measured: 61.01 s
(-51.0%). |61.01 - 71.71| / 71.71 = **14.9% <= 25%: pass**; direction right in every run (largest
after 58.56 < smallest before 119.31); within the hypothesised 40-60% drop. The hand measurement
it replaces (120.0 -> 58.9 s) is reproduced within 1 s. X is not purely proportional to repeats:
fitting a + b·r to the two points gives about 17.8 s fixed and 20.3 s per repeat, which is why
the linear prediction overshoots. The gate's exit code was 0 and it found 0 findings in all 6
runs (coverage trade at level gates; the final full gate keeps 5 repeats).

### I-4 refusal

`tests/improve/test_analyze.py::test_i4_a_lint_proposal_is_normal`: six `finding_class lint:L012`
events (180 s each) -> top proposal `lint:L012`, 6 x 180 = 1080 s, targets
`lattice/spec/lint.py`, `lattice/spec/preconditions.py`, `status: open`, `draftable: true`.
`test_i4_a_checker_proposal_needs_the_owner`: four regression level gates with layer C 150 s ->
top proposal `gate:level_regression:C`, target `lattice/spec/check.py`, `status: needs_owner`,
`draftable: false`, why says "never drafted automatically". The same two sets through `lattice
improve report` (`tests/improve/test_cli.py::test_i4_refusal_in_the_cli`, both params): `status
open` and `status needs_owner`. **Pass.** The live I-3 report shows the same rule on real data
(layers A and C at level gates: `needs_owner`).

## Mutants

`dogfood/improve_mutants.py` (patch texts match the current source; control 61 passed):

| Mutant | Predicted catcher | Caught by | Verdict |
|---|---|---|---|
| IM1 dollars converted into seconds | class-order and no-conversion tests | those two + 3 more analyze tests | caught |
| IM2 closed items still ranked | closed test, I-R replay, ledger CLI, mock shape | exactly those 4 | caught |
| IM3 protected target not needs_owner | I-4 checker test, I-4 CLI run, mock shape | exactly those 3 | caught |
| IM4 torn last line crashes the reader | torn-line and bad-lines tests | exactly those 2 | caught |
| IM5 node cause always `other` | give-up cause, stop cause, live-log ranker | exactly those 3 | caught |
| IM6 layer_seconds from the wrong layer | layer-seconds test | exactly that 1 | caught |

6 of 6 caught, every prediction held (IM1 was caught by more tests than predicted). No survivor,
so no test had to be added. All existing mutant scripts (`dogfood/*_mutants.py`,
`dogfood/studio/*_mutants.py`) still apply to the changed source (each patch's old text occurs
exactly once; checked by applying each to a scratch copy); they were not re-run.

## Post hoc (not pre-registered)

- **I-R records plus the live I-3 "before" events** (`data/.../posthoc-ir-plus-i3-report.txt`):
  the X cluster appears, at **#5** (3 x 119.5 = 358.5 s), below the old records' clusters; the
  same kind of gate is represented twice (5 old "total" runs, 716.3 s at #3, and 3 live per-layer
  runs) because the old ones cannot be split into layers.
- **After the change, before closing it**, the X cluster mixes 3 runs at 5 repeats and 3 at 2
  (6 x 89.0 s = 534.0 s) and its `expected` disappears (a tie in the logged repeats; the proposal
  falls back to `experiment`): the ranker does not know a change happened. Closing it in the
  ledger (`gate:level_regression:X`, `data/.../i3-report-after-closed.txt`) removes it; the next
  proposal is "Cap Await waits at level gates" (3 x 22.5 s). A ledger close of a config change
  should be scoped to events after its timestamp (or keyed by repeats); not built.
- The unchecked reason classifier, on the 13 unchecked entries of Streak's check report:
  `stub_observability` 3 (I-R's I-15 also counts 3), `request_header` 2, `response_header` 1,
  `pattern_match` 1, `production_only` 1, `free_text` 5. I-R's I-14 counts 6 DSL gaps; the table
  finds 4 of them by keyword. It is a crude stand-in for a reason field the spec language does
  not have. The snapshot was logged twice (once per Rebuilder instance: the before and after
  drivers); snapshot semantics make that harmless (f is the latest count).

## What the ranker still cannot see

1. Human time (approvals, diagnosis, hand merges): counted (`human:<what>`), never timed.
2. Risk and correctness: a wrong answer costs no seconds (I-13, I-14 rank by frequency only).
3. Recurrence and one-offs: I-02 scores on past occurrences, though it can recur only for
   imported pre-v1.10 records.
4. Cost to fix (S/M/L) is not an input, by the method.
5. Old records: no cause, no layer seconds; only new runs carry them.
6. Causes behind an owner's stop, and why a change set was rejected (only the decision is logged).
7. Container start per gate (I-03): `timings.setup` is in the report but not copied (only
   `timings.layers` was in scope).
8. Unchecked reasons: no reason field in the DSL; the keyword table is weak (above).
9. Whether a closed config change "recurred": the ledger closes by key, not by condition.
10. One project's log (n = 1 project).

## Decisions made that the task did not state

- `cause` precedence: `stopped_by_owner` (a stop the server makes while closing is `other`),
  `usage_limit`, `spec_unsatisfiable`, `token_budget`, `timeout`, `no_progress`, `regression`,
  `repair_exhausted`, `other`. A land failure and a broken level gate are `other` (with
  `cause_detail`).
- Cluster keys: gate runs `gate:<role>[_<mode>]:<layer>` (`total` without layer seconds; one
  occurrence per layer when present); `gate:baseline_rerun` = a baseline whose sha had an earlier,
  non-reused gate run in the log (computed by the ranker, so import order does not matter);
  only interventions cluster as `human:*` (resume, retry, land, stop, forget, revise, spec_sync;
  approve and run are routine); `change:rejected` (accepts do not cluster); `finding_class` with
  `snapshot: true` (unchecked counts) uses the latest count as f.
- c is the mean over occurrences that recorded the unit (weighted by count); score = f x c.
  Ties: f, then key.
- Protected set: the task's three files plus `lattice/spec/engines/`, `lattice/spec/model.py`
  (the spec DSL's types) and `docs/lattice-language-rules.md` (my reading of "the spec language").
- Proposal templates are a fixed table (no model). The X proposal's `expected` saving assumes X
  scales with repeats; it is shown only when the logged repeats exceed the target.
- Change accept/reject and spec sync have no timeline in Studio; they are log events only
  (documented in API.md). Plan actions get `actor: "owner"` on their existing timeline entries
  (no new entries, so no timeline length changes).
- `GET /api/improve` answers exactly the four fields of the task (the CLI's `--json` adds
  `closed`, `clusters`, `warnings`).
- The I-R replay ledger is written from I-R's "closed" column under the keys the fixes address
  (prereg); I-09 and I-23 have no signal key.
- `stop()` of the Rebuilder and PlanManager gained a `by` parameter (`"owner"` default,
  `"studio"` from `close()`).

## Not verified

- No owner was asked whether any ranking matches their priorities (I-R's open exit).
- No real Studio plan run (with a real agent) has produced events yet; I-2 used the stub agent
  and a fake check-shaped gate; I-3 drove Studio's gate code directly, not a whole plan.
- The improvement was not merged into Streak (the owner's step); `level_gate_args` is set only
  in the scratch clone, which was deleted.
- The web UI shows nothing of this (no UI in this spike).
- Behaviour under concurrent appends from several Studio processes on one project (one process
  per project is the supported setup; appends are serialised per process only).
- The existing mutant suites of other spikes were checked to still apply, not re-run.
