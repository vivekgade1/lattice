# Implement with verification (Spike E), Streak arm and PAg-MS: run results

**Arms run: SO, SO3, SL, SG, SO-rep (oracle), SA (real agent, sonnet), PAg-MS (real agent, sonnet). NOT RUN: none.**
Each ran once, in the order oracle, SA, PAg-MS, with the frozen runner `dogfood/implement_probes_streak.py` (sha256
`a7364973...02ed4`, equal to `docs/results/data/2026-10-18-implement-verify-streak-arm/freeze.sha256` before and after
the run; the runner was not edited). Pre-registration `2026-10-17-implement-verify-prereg.md` with Amendments 1-3 and
`2026-10-18-implement-verify-streak-arm-results.md` (the stop report this run supersedes) were read, unedited.
Worktree `/Users/vivekgade/Documents/lattice-ea` (branch `np-e-arm`), 2026-10-18. No commit, no `git add`, no write to
`~/Documents/streak` or the main checkout. Data: `docs/results/data/2026-10-18-implement-verify-streak-arm/{streak-oracle,streak-agent,pagms}/`.

All scoring is **blind**: one run, predictions fixed before it, nothing re-scored. Post hoc items are listed in section 4
and are separate from every blind value.

## 1. Table: predicted vs observed (blind)

| row | prediction (prereg section 4) | observed | status |
|---|---|---|---|
| SO | rebuild passes round 1 on 19/19; c15, c21 `nothing_to_build`; `resolved` exact 19/19; flow changes c03: 2, c08: 6, all `t3_may_fail`, 0 `unreported`, 0 `workflow_fails`; `spec_backed` 2-8 of 8; verdicts pass 17-19, needs_owner 0-2, fail 0 | 19 builds passed, 2/2 controls `nothing_to_build`; `resolved` exact 19/19; `unreported` 0 and `regression` 0 on 21/21; `before.matches_report` 21/21; flow changes c03: 2, c08: 6, all `t3_may_fail`, 0 `workflow_fails`; `spec_backed` 7/8; verdicts pass 18, needs_owner 1, nothing_to_build 2, fail 0. **Round 1 built 17/19, not 19/19** (c19 and c20 needed 3 rounds). | PASS on the registered pass rule; one sub-prediction missed (round 1) |
| SO3 | `t3_vs_edit` file sets equal to D's PT3 truth per change; micro file recall 0.864 | file sets equal on 21/21 (c15 and c21 empty); micro file recall 0.864 (19/22 edited files), equal to D's. Declarations (report-only): equal 3/19 strict, 16/19 on the U0 form. | PASS |
| SA | 3-6 of 6 pass; `regression` 0; `unreported` 0-1; `needs_owner` 0-2; cost USD 3-10 (cap 12) | 6 of 6 ended OK: 5 builds `pass` in round 1 (c01, c02, c05, c09, c10) plus the c15 control `nothing_to_build`; `regression` 0, `unreported` 0, `needs_owner` 0, `still_failing` 0, `workflow_fails` 0, 59 flows unchanged on each build. **Cost USD 0.50 (list estimate), not USD 3-10.** | report-only; 4 of 5 hits, cost prediction missed |
| SL | 0 leaked `lattice-` containers incl. a fault run | 0 leaked per run and after the stage, on the oracle stage (24 runs), the SA stage (6 runs) and the fault run (c14, the after run raised; rc 3, `not_run:error`, 1 container live at the fault, 0 left) | PASS |
| SG | PG and PNM clean on every Streak run; Streak checkout unchanged | 25/25 oracle runs and 6/6 SA runs clean; Streak `git status --short` before and after = ` M studio.toml` only, same branch `main`, same head | PASS |
| SO-rep | (risk 4, report-only) the same change twice gives equal canonical Tier 4 bytes | c03 equal, c08 equal, **c01 not equal**: the only differing key is `rebuild` (first run 1 round, repeat 2 rounds) | REPORT |
| PAg-MS | i1 `pass` (p 0.8); `bad_invariant` `not_built` or `pass` with 0 regressions; 0 `unreported` (cap USD 3) | i1 `pass` round 1 (USD 0.053); i3 candidate, no planted edit, `pass` round 1 (USD 0.026); `bad_invariant` `pass` round 1 (USD 0.072), 0 regressions; 0 `unreported`; total USD 0.151 | report-only; all 3 predictions hit |

SO per change (oracle, round 1 unless noted): c01 pass; c02 pass; c03 pass (2 flow changes, both `spec_backed`); c04 pass;
c05 pass; c06 pass; c07 pass; c08 `needs_owner` (6 flow changes, 5 `spec_backed`); c09-c14 pass; c15 `nothing_to_build`;
c16-c18 pass; c19 pass (3 rounds); c20 pass (3 rounds); c21 `nothing_to_build`.

## 2. Misses and findings (every one stated)

1. **SO, round 1: 17/19, predicted 19/19.** c19 (K14) and c20 (K4) passed only in round 3, with `./check.sh` as the gate
   (runner default, Amendment 3 ruling 3). The oracle replays the same reference diff each round, so a failure of round 1
   and 2 followed by a pass is not an agent difference. The SO-rep run of c01 also took 2 rounds where the first run took 1.
   The artifacts do not keep the per-round gate output (the Studio rebuild records are in the scratch, not in the output
   directory), so the cause is **not established**. The consistent reading is a flaky gate (Streak's nudger timing, prereg
   risk 4), but that is a hypothesis, not a measurement. The verdict, `resolved` and the counts on c19 and c20 are
   unaffected (pass, `resolved` exact).
2. **SO, `spec_backed` 7/8, one `needs_owner`.** c08 flow `wf-597f2899e4` ("create habits > read completions", step 2,
   `GET /v1/habits/<id>/completions` as ben) changed, was `t3_may_fail` in D's report, and no candidate step backs the new
   answer, so the verdict is `needs_owner` (R4). This is inside the predicted bands (needs_owner 0-2, `spec_backed` 2-8) and
   is the registered behaviour, not a failure. The other 7 flow changes are `spec_backed`.
3. **SO-rep c01 not byte-equal** (differing key `rebuild`, rounds 1 vs 2), tied to item 1. c03 and c08 were equal, so
   Tier 4 content (everything but `rebuild`) was equal on 3/3.
4. **SO3 declarations 3/19 strict, 16/19 on U0** (report-only, Amendment 3 ruling 6). Strict inequalities are of the kind
   `only_in_truth: internal/habits/routes.go:Register` (c01): D's truth tool lists an enclosing declaration that the
   Tier 4 U0 diff does not. Not gating.
5. **SO3 file recall misses (same as D, so the 0.864 matches):** c14 and c19 (edited file `nudger.go` not in T3's list,
   recall 0.0) and c18 (one of two edited files, recall 0.5). File sets still equal D's truth on every change.
6. **SA cost: USD 0.50 against a predicted USD 3-10.** Per build USD 0.06-0.13 (c01 0.135, c02 0.095, c05 0.082, c09 0.124,
   c10 0.061). Every build passed in round 1. The prediction was too high; there is no cap pressure (cap USD 12).
7. **SA sample is small and easy:** 6 changes, 5 builds, all round 1, 0 flow changes. It says the machinery and the agent
   worked on these 5, not that the agent is reliable in general (prereg risk 3).
8. No usage limit, no login failure, no crash in any step. No re-run was needed or done.

## 3. Engine drift (disclosed, Amendment 3 ruling 10)

The engine that ran is the `next-phases` tip merged into `np-e-arm` (HEAD `3bcc355` at the end of the run), not the
6a318e5 tree of the prereg hash list. `meta.json` of all three runs reports five files that differ from the prereg hashes:
`lattice/spec/appmap.py`, `check.py`, `impact_report.py`, `impact_tier2.py`, `implementation.py`. D's frozen engine and the
core results were not edited. The SO and SA numbers are therefore for the drifted engine; the drift did not change the
registered outcomes (SO rules pass) but it is a known difference between the registered and the measured code.
The two synthetic smoke runs (c90, c91) from the runner build are excluded from every value above.

## 4. Blind versus post hoc

- **Blind (scored once):** every row in section 1, the SO pass rule (`unreported` 0 and `regression` 0 on 21/21,
  `resolved` exact on 19/19, `before.matches_report` 21/21), SO3, SL, SG, SA, PAg-MS, SO-rep.
- **Post hoc (not scored, observations after the run):** the reading in section 2 item 1 (flaky gate) and the per-flow look
  at c08's `wf-597f2899e4`. Neither changes a registered value.
- Pass summary of the registered rules: SO PASS, SO3 PASS, SL PASS, SG PASS. SA, SO-rep and PAg-MS are report-only.
  Per Amendment 2, the Streak rows are scored once, so E now passes on both the core and the Streak arm, with the round-1
  sub-prediction miss (item 1) and the SA cost miss (item 6) stated.

## 5. Cost, time, cleanup

| step | wall time | model cost (CLI list estimate) |
|---|---|---|
| (1) oracle (SO, SO3, SL, SG, SO-rep, 25 pipeline runs incl. fault) | 3038 s (about 51 min) | USD 0 (0 `claude` calls) |
| (2) SA (6 changes) | 1469 s (about 24.5 min) | USD 0.4968 (cap 12) |
| (3) PAg-MS (3 runs) | 51 s | USD 0.1509 (cap 3) |
| total | about 80 min | USD 0.6477 |

- No API key was used; model calls only through the `claude` CLI inside rebuild's agent.
- Containers: every container this runner started carried a `lattice.owner` label with the prefix `implp-`. `meta.json` of
  all three runs lists `containers_with_our_tokens_at_end: []`, and `docker ps -a` after step 3 shows none. Containers
  I did not start were left alone: `cyclops-*`, `aetheris-*`, and `lattice-mysql-*` containers with other owner labels
  (`h-streak-author`, `h-streak-arm`) that other work started and replaced during the run.
- Scratch under `/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/w-e-run`;
  0 worktrees left by the runner (`worktrees_left` 0 on every run); `git status` of `lattice-ea` shows only the three new
  data directories (and this file).

## 6. Files created

- `docs/results/2026-10-18-implement-verify-streak-arm-run-results.md` (this file)
- `docs/results/data/2026-10-18-implement-verify-streak-arm/streak-oracle/` (`meta.json`, `probes.json`, `summary.md`, `runs/` for so-c01..c21, sorep-c01/c03/c08, fault-c14)
- `docs/results/data/2026-10-18-implement-verify-streak-arm/streak-agent/` (`meta.json`, `probes.json`, `summary.md`, `runs/` for sa-c01/c02/c05/c09/c10/c15)
- `docs/results/data/2026-10-18-implement-verify-streak-arm/pagms/` (`meta.json`, `probes.json`, `summary.md`)

## Orchestrator verification
The runner's sha256 matched `freeze.sha256` (checked again after the run). I cross-checked the oracle arm independently of the
runner: for all 21 changes the `resolved` set in each run's Tier 4 equals the Tier 1 failing set from Spike D's own stored raw
engine output (a separate earlier run, same change set): 21 of 21 equal. No container with an `implp-` owner label remains.
The oracle arm replays the reference diff; the SA arm is five easy builds at USD 0.50, so it says little about agent
reliability in general. The round-1 miss (17 of 19 instead of 19 of 19) is real; its cause is not established (a flaky full
`./check.sh` gate is a hypothesis only).
