# Results: build performance and resume, Streak p2 (2026-10-11)

Plan: `docs/design/lattice-build-performance-phase.md`. Detail per piece:
`2026-10-11-gate-profile.md` (P0), `2026-10-11-gate-timings.md` (G1),
`2026-10-11-perf-studio-prereg.md` and `-results.md` (S2a, mutants), this document (the
whole phase and the real run). Dollar figures are the CLI's **list-price estimates, not
charges**. Raw plan records: `docs/results/data/streak-plans-2026-10-11/`.

## What was asked, what happened

| Ask | Result |
|---|---|
| Improve build performance | Node gate on Streak 412.7 s -> 21-29 s; level gate 424 s -> 138-151 s. Layer X at level ends did not shrink. |
| Durable state, resume from where a plan stopped | Merged (API v1.10, 6/6 mutants). p2's pre-store record imported and resumed from level 1. |
| Run p2 as the test, from where it stopped | **p2 passed**: 12/12 nodes, `build/streak` a0679e9, full ABCDX gate 0 findings (re-run by hand, 156 scenarios, 2 min 33 s). |
| (added) a failing level gate must be corrected, not stop the plan | Level repair loop (v1.11, default 2 attempts). Not triggered in the real run (no level gate failed); exercised by tests, dogfood and the restart drill. |

## Build order and what each node produced

| Node | Commit on `roadmap` | Evidence |
|---|---|---|
| V1 verify + merge state | 1f9d495 | suite green, 6/6 state mutants |
| P0 profile | da271ea | 412 s = A 0.02 / C 0.33 / D 290 / X 117; 95% of D is 9 scenarios waiting 30 s `Await`s on the unbuilt nudger |
| G1 `--only`, timings | 3843a37, 31eff5a | `--only` of 3 scenarios 6.3 s |
| S2a scoped gates, level repair, stop hygiene (v1.11) | f4d8b01, fd028c8 | 934 passed; dogfood 313 cases; **18/18 mutants** (re-run independently) |
| S2b plan UI (resume, land, repair) | d733746, 7157aed | 411 tests; browser check on the mock, per state |
| Importer for pre-v1.10 records | 0085aa2 | tested on Streak's real p2 record |
| Restart drill + a defect it found | 1073e67 | 943 passed; dogfood 313; 2/2 mutants |

## Gate time, same tip (9417351, measured by S2a)

| Gate | Before | After |
|---|---|---|
| A node's gate (devices node, 4 in scope) | 412.7 s full ABCDX | **21.5 s** (predicted 21.1 s; 120 scenarios = scope + regression set) |
| A level's end gate | 424 s full | **138.2 s** regression selection, full layers |
| Gates per level | 3 full (baseline, round, level end) | 1 scoped per round + 1 regression; the next start baseline reuses a stored per-tip result |

Layer X is capped at `x_budget = 60` races and 120 scenarios still fill it, so ~117 s of
every level gate is X. Layer D fell from 290 s to 15 s.

## The real run (p2, resumed at level 1)

| Level | Node (model) | Rounds | Wall | Out tokens | List $ | Gate |
|---|---|---|---|---|---|---|
| 0 (before this work) | scaffold (opus) | 1 | 1902 s | 77,047 | 3.90 | full 422 s |
| 1 (before) | auth (opus) | 1 | 1482 s | 16,872 | 1.32 | full 424 s |
| 2 | habits-core (sonnet) | 1 | 99 s | 8,262 | 0.30 | node 24 s, level 137 s |
| 2 | account (sonnet) | 3, then 1 on retry | 309 s + 106 s | 12,367 + 7,510 | 1.47 | node 21 s, level 151 s |
| 3 | habits-edit, progress | 0 | 0 | 0 | 0 | nothing in scope failing |
| 4 | checkins (opus) | 1 | 98 s | 2,071 | 0.28 | level 139 s |
| 5 | nudges (sonnet) | 1 | 191 s | 20,203 | 0.58 | level 144 s |
| 6 | nudger (opus), home (sonnet) in parallel | 1 each | 114 s, 141 s | 2,590, 6,118 | 0.27, 0.17 | level 145 s |
| 7 | sync (opus) | 3 (gave up) | 325 s | 7,843 | 1.83 | node 28-29 s |
| 8 | integration | 0 | 0 | 0 | 0 | start baseline 151 s, 0 failing |

- Resumed part (levels 2-8, three runs): **2,604 s agent+gate wall time, +67k output tokens, +$4.90**.
  Original levels 0-1 took 38.8 and 31.8 minutes each.
- Plan totals: 179,592 output tokens, 10.4M input (9.7M cache reads), 3,851 lines, 14 rounds, $10.97.
- The first start baseline after import ran a full 416 s gate: no stored result existed
  for the tip.

**Caution on the comparison.** Levels 2-8 are different, mostly smaller nodes than scaffold,
so the node-for-node speed-up is the gate row above, not 1,900 s vs 100 s. The
pre-registered B1 before/after was folded into S2a's same-tip measurement; the 21.1 s
prediction was written before that run but not committed to a prereg file.

## What went wrong, and what it taught us

1. **Two spec defects stopped the plan twice.** `device_registration` expected 0 rows for a
   token step 1 had registered; `access__logout__as_anon` expected one refresh token no
   step creates. Both were unsatisfiable `expect_db` preconditions. Both agents said so and
   refused to fake state or credentials (the p3 `account` timeout of 1800 s was very likely
   the first). Each fix was verified with a control on the real tip (original spec fails
   at the named step, fixed spec passes) before committing.
2. **A Studio change set cannot make a scenario-only fix.** The drafter rewrote requirement
   text, added a rule that locked the bug in, and touched an unrelated scenario (c1, rejected,
   about $2.28). The fix was two lines in `spec.py`.
3. **A spec fix on the working copy is invisible to a build.** Nodes are cut from the
   `build/streak` commit and gated on the spec committed there; the fix had to be committed
   on `main` and merged into `build/streak`.
4. **The restart drill found a real defect** (below).

## The restart drill (`scripts/restart_drill.py`)

A real Studio server, `SIGKILL`, restart, Run; stub planner and agent, throwaway project.

| Kill during | Before the fix | After |
|---|---|---|
| an agent round | pass | pass: orphan reaped, passed work kept, 1 node continued (attempts 2) |
| a level gate | pass | pass: no agent re-run at all, level re-merged and re-gated |
| a **level-repair round** | **fail**: nodes stayed `repairing`, level `failed`; completed rounds (`w-clash#1`, `r-clash#1`, a finished repair) ran again; the repair pass then refused the interrupted builds | pass: nodes `interrupted`, level `repair_pending`, `resume.kind` repair, nothing completed re-runs |

Fix: reconcile marks the level `repair_pending` and `repair_build` accepts an interrupted
build that has its commit. Removing either half fails the drill (2/2 mutants).

## Not verified

- A real `claude --resume` session continuing after a `SIGKILL` (the drill uses the stub
  agent; `--resume` was exercised for real only in the p2 retry of `account`).
- Token usage accounting across a restart (the stub reports none).
- The level repair loop on a real build: no level gate failed in p2.
- Any change of layer X cost at level ends (left as configured, on purpose).
- Planner usage for p2 (an imported record predates v1.11; it shows 0).

## Improvements, ordered by value

1. **Lint unestablished `expect_db` preconditions** in the checker: a step or invariant that
   expects rows nothing in the seed or earlier steps can create. Both defects were this class.
2. **Spec fixes should reach the build branch.** Either a Studio "apply spec fix" that commits
   to the spec branch and merges it into the integration branch, or a spec overlay for builds.
3. **A scenario-level change set** (edit a named scenario, no requirement rewrite).
4. **Cheaper level gates:** a separate `x_repeats` / `x_budget` for level gates (about
   2 min of every level is layer X).
5. **A real-agent restart drill** (a few cents list estimate) and usage-across-restart check.
6. **Level numbering:** server prose and `level_runs[].level` are 0-based, the UI says
   level 1.., and shifts prose with a regex; expose 1-based fields or prose.
7. **Stale target labels** in a gave-up node's build record ("failing before; not re-run by the
   regression gate") for scenarios that now pass.
8. Reuse a per-tip result for an imported plan's first baseline (save 416 s, once).
9. Streak `check.sh` leaves an empty `metrics/last-check.json` on exit 2; the `gate_env`
   JSON report is 190 KB because it lists every step.
10. Carried over from the handoff: MySQL ISO-Z timestamp adapter bug, DSL gaps, answered
    questions not marked answered; multi-project / contracts phase.

## State at the end

`roadmap` 1073e67 (nothing pushed). Streak: `main` a0db033 (two spec-only commits, plus the
owner's uncommitted `studio.toml`), `build/streak` a0679e9, plan p2 `passed` in
`.lattice/state/streak/`. One kept node worktree (`rebuild/p2-account`). Studio on :8765
restarted on the merged code.
