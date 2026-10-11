# Phase (planned): build performance in Studio

Status: **planned, not started** (recorded 2026-10-10 during the first real Streak build, plan p2/p3).
Update 2026-10-11: P0 profile and G1 timings done; S2a built (API v1.11: scoped node gates,
the regression level gate, the level repair loop, stop/budget hygiene, planner/areas from
per-tip results): `docs/results/2026-10-11-perf-studio-results.md`.
Do it after plan p3 finishes and the durable-state work (branch `studio-state`) is merged.

## Why

The first real build from Studio (Streak: 148 rules, 153 scenarios, Go + MySQL) is slow, and most of
the time is not the model. Measured on p2 and p3:

| Cost | Measured |
|---|---|
| Agent writing code | scaffold: 77k output tokens in ~25 min (~50 tokens/s) |
| One full gate run (`check.sh`, ABCDX) | **~7 min** (baseline 422 s, round 434 s, account baseline 426 s). With every endpoint answering 404 the same run took 31 s |
| Gate runs per level | 3 (baseline, round, full check after the merge) + any the agent runs itself |
| A level in total | ~35 to 45 min, even for a node with 2 failing checks (auth) |
| Whole plan p3 (8 levels) | estimate ~5 h |

Comparison point: a direct single-session build with the same gate is estimated at 1.5 to 2.5 h, and the
earlier Go job-scheduler build (full check ~60 s) took 24 min for 5 tickets. The graph's extra hours buy
per-level regression checks, isolation, resume and per-node metrics; they should not cost 3 h.

## Not yet measured (do first)

**Which layer makes the gate take 7 minutes.** Suspects, unverified: layer X (races, `x_repeats = 5`,
now against real handlers), scenarios that log in and hash passwords (bcrypt cost in the app), MySQL
clone per case. Profile one run per layer (`--layers A|B|C|D|X`) on the build/streak tip before changing
anything.

## Improvements, in expected order of payoff

1. **Reuse the previous level's final gate as the next level's baseline** when the tip is identical.
   Saves one full gate (~7 min) per level. Studio change (plan.py runner).
2. **Two gates: cheap in the loop, full at the level end.** Config `[rebuild] node_gates` (node-scoped
   scenarios, `--x-repeats 1`, layer D + the node's invariants) used for the baseline of a node, the
   agent's own runs and the round gate; the full ABCDX only after the level merge. Needs a scenario
   filter in `lattice spec check` (e.g. `--only <scenario names|rule ids>`) or a project-side wrapper.
3. **Make the full gate itself faster** once profiled: lower `x_repeats` for non-final levels, a cheaper
   password hash in the test harness (`HABIT_BCRYPT_COST`), fewer DB clones, run layers in parallel
   where they are independent.
4. **Scope fencing for agents.** Show the agent only its in-scope findings (filter the gate output it
   sees, or give it the scoped command), and state in the brief which areas belong to other nodes. The
   scaffold node wrote auth, habits, check-ins and progress code (3.3k lines) because it saw 155 red checks.
5. **Per-node budget.** A turn budget (minutes and tokens) per node and a tier suggestion by size, so a
   scaffold cannot run 25+ minutes.
6. **Planner awareness of what is built.** Compute built areas on the target branch (per-branch areas
   status) and tell the planner automatically; never include already-passing nodes. The scaffold node
   always runs an agent even when its checks pass: skip it when nothing fails in scope.
7. **Record the planner's own usage** (plan totals show 0 tokens/cost for the planner calls) and keep
   cache tokens in every total.
8. **Expose `plan_defaults`** (`max_parallel`, models) in `GET /api/projects` so the UI shows the
   configured cap instead of its hard-coded 2.
9. **Areas status on a built branch** shows `not_built` / `failing: null` after levels have landed:
   record the full-gate result per tip so areas reflect it.

## Success criteria and how to evidence them

Pre-register before building (as for the earlier experiments): re-run the same Streak plan shape from a
frozen checkout before and after, same model tiers, and report blind and post-hoc separately.

- A level with 1 to 2 failing checks takes **≤ 15 min** (from ~40).
- Whole Streak plan **≤ 2 h** wall (from ~5 h), with **no change in the final result** (same checks
  pass, no regressions at any level) and the full gate still run at every level end.
- Report per phase: gate time by layer, agent time, tokens (with cache), rounds, lines changed.

## Risks and notes

- A cheaper in-loop gate can pass a node that the full gate then fails: the level-end full gate must stay,
  and a level failure must name the node responsible.
- Parallel gates on one project were checked safe at 2 concurrent runs (16 s each, no port or container
  collisions); more than 2 is untested.
- Scope fencing must not hide regressions from the agent: regressions it causes in other areas must be
  shown to it.
