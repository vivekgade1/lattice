# Implement with verification (Spike E), Streak arm and PAg-MS: results

**Arms run: NONE. SO, SO3, SA, SL, SG and PAg-MS are all NOT RUN.** The frozen runner has no code for them. Nothing here
is a pass. No model call, no Docker container, no write to Streak or to the main checkout.

Pre-registration: `2026-10-17-implement-verify-prereg.md` with Amendment 1 (read, unedited). Core results:
`2026-10-17-implement-verify-results.md` (section 6 lists these rows as NOT RUN). This node ran in worktree
`/Users/vivekgade/Documents/lattice-ea` (branch `np-e-arm`, `978b468`), 2026-10-18. Data:
`docs/results/data/2026-10-18-implement-verify-streak-arm/stop-report.json`.

## 1. Why every arm stopped

The brief says: use the frozen runner and probe files unchanged; if a runner has a stub, bug or missing input, stop that
arm and report precisely; do not edit frozen files.

1. **Freeze verified.** The 7 hashes in `docs/results/data/2026-10-17-implement-verify/freeze.sha256` match the files in
   this worktree (`implement.py`, `impact_tier4.py`, `implement_probes.py`, `implement_mutants.py`, the two test files,
   `fixtures/implement/ms.py`). So the frozen files are the ones that were scored on the core arm.
2. **`dogfood/implement_probes.py` is core-only.**
   - `--stage` is `choices=("core",)` (line 1081). `--stage streak` is rejected by argparse.
   - `not_run_rows()` (lines 1069-1077) hard-codes `PAg-MS` and `SO SO3 SA SL SG` as NOT RUN, with the container-era
     reasons ("no Streak checkout and no Docker daemon"; "budget not approved").
   - There is no probe function for any of the six rows, and none of the flags the prereg's command lines use
     (`--streak-ref`, `--changeset`, `--agent`, `--subset`, `--usd-cap`, `--parallel`).
   - The core results doc (section 9, last paragraph) already says the same: "`--stage streak` is not implemented in
     `implement_probes.py`: it needs the owner's machine, the frozen D engine and D's change set; it is the
     orchestrator's step after the core freeze."
   - The prereg's section 8 and 9 assume the runner has the stage (`dogfood/implement_probes.py --stage streak ...`) and
     list that file as owned by the build node. That did not happen. This is a defect of the frozen runner (a stub),
     not of the machine or the inputs.
3. **PAg-MS has the same problem.** It is a row of the core runner, but the runner only emits a hard-coded NOT RUN for
   it. The product CLI (`lattice spec implement --agent claude`, `implement.py`) exists and is frozen, but the probe
   (which change, which config, what is scored, the `max_rounds` 3 / USD 3.00 cap bookkeeping, the report) is not coded.
4. Running the CLI by hand per change would not be "the registered arm": the SO pass rules (`resolved` exact on 19/19,
   `unreported` 0 on 21/21, `before.matches_report` 21/21, SO3's file-level equality with D's truth, SL's leak counts
   including a fault run, SG's owner snapshots on every run) are scored by probe code that does not exist. Writing it
   now would be a new, unfrozen scorer written by the node that then runs it, so I did not.

## 2. Inputs (all present; checked, none consumed)

| input | state |
|---|---|
| Streak `build/streak` | `a0679e9`, present; `git status --short` shows only the pre-existing ` M studio.toml`. Not touched. |
| D change set | `scratchpad/impact/changeset`: 21 dirs `c01..c21` plus `READY`; `shasum -a 256 MANIFEST.sha256` = `52e0e541...caa3`, equal to the brief. `c15/ref.diff` is 0 bytes (as the prereg says). |
| Planted set | exists at `scratchpad/e-planted` (not under `impact/`); Spike E's prereg does not use it; not hashed. |
| Docker | daemon 29.4.0 up. Three pre-existing containers (`cyclops-scheduler`, `cyclops-ui`, `cyclops-stream`) carry no `lattice.owner` label and are not mine; untouched. |
| Model | `claude` CLI only; no API key was used; 0 calls. |

## 3. Table: predicted vs observed

| row | prediction (blind, prereg section 4) | observed | status |
|---|---|---|---|
| SO | rebuild passes round 1 on 19/19; c15, c21 `nothing_to_build`; `resolved` exact 19/19; 8 flow changes `t3_may_fail`, 0 `unreported`; verdicts pass 17-19, needs_owner 0-2, fail 0 | none | NOT RUN (no runner code) |
| SO3 | `t3_vs_edit` file sets equal to D's PT3 truth per change; micro file recall 0.864 | none | NOT RUN |
| SA | 3-6 of 6 pass; `regression` 0; cost USD 3-10 (cap 12, approved by the owner) | none; USD 0 spent | NOT RUN |
| SL | 0 leaked `lattice-` containers incl. a fault run | none; 0 containers started | NOT RUN |
| SG | PG and PNM clean on every Streak run; Streak checkout unchanged | Streak checkout unchanged (only the pre-existing ` M studio.toml`); no run | NOT RUN |
| PAg-MS | i1 `pass` (p 0.8); `bad_invariant` `not_built` or `pass` with 0 regressions; 0 `unreported` (cap USD 3, approved) | none; USD 0 spent | NOT RUN |

Blind rows scored: 0. Post hoc rows: 0. Misses: none to state, because nothing was scored. The one defect found is the
runner stub above.

## 4. Cost, time, cleanup

Wall time about 10 minutes (reading, hash checks, input checks). Model cost USD 0. Containers started: 0 (so none to
remove). Writes: only the two new files below in `lattice-ea`; nothing in `~/Documents/streak`, nothing in the main
checkout, no commit, no `git add`.

## 5. What would unblock this (owner/orchestrator decision, not taken here)

A new runner file, separate from the frozen ones (for example `dogfood/implement_probes_streak.py`), written and frozen
BEFORE any Streak run, with: `--stage streak`; a per-change D report from D's frozen engine on a scratch archive of
`a0679e9` with its own MySQL 8.4 container (label `lattice.owner`); `lattice spec implement --agent oracle --ref-diff
<c>/ref.diff` per change (4 in parallel); the SO, SO3, SL, SG scorers per the prereg; the SA subset `K1,K2,K5,K9,K10,K15`
(first by sorted id) with the USD 12 cap and 1800 s per round; and a PAg-MS probe on the fixture corpus (i1, i3's
candidate with no planted edit, `bad_invariant`; `max_rounds` 3; USD 3 cap). The CLI it would drive
(`implement.py`, `impact_tier4.py`) is frozen and unchanged. This is a deviation from "frozen runner unchanged" and so
needs the owner's yes; the arm would then be scored once and reported as a post-freeze build of the runner.

## 6. Files

- `docs/results/2026-10-18-implement-verify-streak-arm-results.md` (this file)
- `docs/results/data/2026-10-18-implement-verify-streak-arm/stop-report.json`
