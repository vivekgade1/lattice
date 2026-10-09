# Spec v2: background services and the same-minute rule (2026-10-09)

This follows up the Go bare-brief 2×2 (`2026-10-09-go-bare-brief-results.md`), which found three rules the spec did not check:
1. the worker loop;
2. the scheduler loop;
3. "a minute is scheduled once, even after its run finished".

The bare spec-first builds skipped them.

## What changed

**Lattice** (`3bc3539`): background-service checks.
- `Spec(services=(Service(...), ...))` declares the services.
- A new step, `Await(service, expect_db, within_s)`, waits on one.
- The harness maps each service name to a `ServiceCmd`, given as `--services MOD:NAME` or with `process_runtime(services=)`.
- The checker starts the service against the scenario's database, polls until `expect_db` holds or the time runs out, then always stops it.
- New validation codes: S024 and S025.
- Layer C runs after each Await. Layer X cuts a scenario at its first Await.

**The spec** (job-scheduler `753a8d2`, copied to job-scheduler-go `bf413fa`): the owner's rules, now as scenarios.
- `the_worker_service_executes_queued_runs`: the worker runs a queued `python ok.py` run to succeeded, within 30 s, with no call to `/worker/run-once`.
- `the_scheduler_service_queues_due_runs`: a `* * * * *` job gets a cron run from the scheduler on the real clock, within 75 s.
- `a_minute_is_scheduled_once_even_after_its_run_finished`: tick, cancel the run, tick the same minute again, and there is still 1 run; the next minute makes it 2.

**Harnesses** (not the spec):
- Go uses `server worker` and `server scheduler`.
- Python uses `python -m job_scheduler.{worker,scheduler}`. Each runs in a fresh directory holding the database link and a copy of the fixtures, because the builds' loops find their database in different ways.

**gocheck precision** (`926f496`, post hoc from the held-out false positives):
- GO803 accepts enum checks and anchored finite-length regexes as bounds.
- GO807 ignores a "nothing written" flag set inside the transaction.
- Results: the 6 mutants are still 6/6, all 5 clean Go builds give 0 findings, and Sonnet prose keeps its 3 true GO803 findings.

## Re-judge: every build against spec v2 (ABCDX + services, lattice frozen at 3bc3539)

| build | before (v1: BCDX, gocheck apart) | **spec v2** | what v2 adds |
|---|---|---|---|
| Go spec-first, matched briefs (Opus) | pass | ✅ pass, 154 cases | — |
| Go baseline, matched briefs (Opus) | pass | ✅ pass | — |
| Go bare, prose (Opus) | 4 X | ❌ 3 X | (X is probabilistic: 3 of the same 4) |
| Go bare, prose (Sonnet) | 1 D + 7 X, plus 3 true A | ❌ 3 A + 1 D + 6 X | A in the gate now |
| Go bare, spec (Opus) | pass | ❌ **2 D**: no worker service, no scheduler service (its loop is behind an env var) | the gap, now caught |
| Go bare, spec (Sonnet) | pass | ❌ **3 D**: no worker, no scheduler, a second cron run in the same minute | the gap, now caught |
| Python spec-first (Sonnet master, Haiku, Opus, Fable) | pass | ✅ pass, 154 cases each | services hold in all four |
| Python baseline (Sonnet, prose) | 11 | ❌ 11 (3 A, 1 D, 7 X) | unchanged; its loops pass |

**Reading:**
- Every rule the bare spec arms skipped is now a finding that a spec-first agent would see in its own gate.
- No build that implemented the rules was newly failed: the matched-brief Go builds and all four Python spec-first builds still pass.
- The bare prose arms keep their concurrency findings.
- The bare spec arms, built against the old spec, now fail only on the rules v1 left out. A spec-first agent gated on v2 would have been sent back to implement them.

**Caveats:**
- These are re-judges of existing builds, not new builds against v2.
- The scheduler scenario waits on the real clock (up to 75 s), which adds about a minute to every full check.
