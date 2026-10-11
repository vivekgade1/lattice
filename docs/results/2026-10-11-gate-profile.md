# 2026-10-11: Streak gate profile (P0)

One gate run per layer on the `build/streak` tip `9417351` (scaffold + auth built), sequentially, on an
otherwise idle machine, Studio's gate environment (`LATTICE_REBUILD=1`, `UV_NO_SYNC=1`, main `.venv`),
MySQL 8.4 in a throwaway Docker container, 153 scenarios. Command: `./check.sh --layers <L>`.

| Layer | What | Seconds | Share of the full run |
|---|---|---|---|
| A | gocheck (static) | 1 | 0.2% |
| C | invariants | 6 | 1.5% |
| **D** | **153 scenarios** | **290** | **70%** |
| X | races (`x_repeats 5`) | 83 | 20% |
| ABCDX | the gate as configured | **412** | |
| (B | endpoint programs | skipped on MySQL | |)

Sum of layers 380 s; the other ~32 s is `go build`, container start and report writing.

## What this changes

- The 7 minutes are **layer D**, not X. 153 scenarios in 290 s is ~1.9 s per scenario.
- Hypotheses for the per-scenario cost, **not yet measured** (the report has no timings; G1 adds them):
  1. the runtime starts one app process per scenario (`lattice/spec/process.py`: free port, wait for
     `GET /health`), and Streak's binary runs `db.Migrate` (GORM AutoMigrate of 8 tables on MySQL) at
     every start although the seed already created the schema;
  2. the `nudger` service is started with the app;
  3. app-side password hashing uses `bcrypt.DefaultCost` (10) for registration (`internal/auth/routes.go:86`);
     the seed uses cost 4, so logins are cheap: this affects registration scenarios only.
- Layer X is 83 s for 5 repeats: not the main cost, but worth `--x-repeats 1` in loops.
- 40 scenarios fail at this tip (layer D, 40 findings), 0 in A, C, X: those are the areas not yet built.

## Consequences for the plan

Node-scoped gates will help most: the in-loop gate need only start the app for the node's own scenarios
(a node with 10 scenarios is ~20 s, not 290 s). The level-end full gate stays. A cheaper per-scenario
start (skip AutoMigrate when the schema exists, `HABIT_SKIP_MIGRATE`; reuse one app per scenario batch
where scenarios do not alter shared state) is a second lever and a Lattice runtime change.

## Cleanup observation

Stopping p3 left two orphaned processes (`streak-api`, `streak-api nudger`, 17 h old) and two exited
`lattice-mysql-*` containers from the node's gate. Removed by hand (nothing else touched). The orphan
reconciliation in the durable-state work covers this on restart; the stop path should also kill the app
processes a gate started (add to S2).
