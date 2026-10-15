# Results: perf statement counting beyond MySQL (G1)

Pre-registration: `2026-10-15-perf-engines-prereg.md` (with Amendment 1). Branch `np-perfengines`.
Data: `docs/results/data/2026-10-15-perf-engines/`.

## What was built
Engine-agnostic counting for `lattice perf`: Postgres through `pg_stat_statements`, ClickHouse through
`system.query_log`, chosen by an optional `[perf]` table in the engine descriptor (it can pick a built-in
mechanism or supply SQL, never code or `docker run` options). Tool statements are excluded by database. A
liveness guard marks a budget `unmeasured` when the counter saw nothing. MySQL and in-process SQLite are
byte-identical: `lattice/perf/budget.py` and `__main__.py` unchanged, the 68 existing perf tests unchanged.

## Result, stated plainly
**The counting has NOT been shown to work against a real Postgres or ClickHouse server.** Docker was not
running and no database driver was installed, so under Amendment 1 every container probe is **NOT RUN**
(14 of 34 probes), never a pass. What did run (20/34, all PASS, 10 identical repetitions each):
- the regression probes: SQLite in-process and the process app are identical to `a4ab4a1`; the container
  command lines for `lattice check` are byte-identical with the default `extra_args`;
- descriptor parsing and the trust boundary (`[perf]` cannot name code or docker options);
- the tool's own logic against a fake Postgres/ClickHouse twin (this shows the tool's logic, not that
  `pg_stat_statements` or `query_log` behave as assumed);
- tests: `tests/perf` 112 collected = 108 passed + 4 skipped (the Docker tests); broad suite 1424 passed,
  9 skipped, 0 failed; `perf_rule_probes` 24/24; `dogfood/studio/check.sh` PASS, 435 cases.
Mutants: control + PM1-PM4 + PR1-PR9 + PX1-PX11 = 24/24 killed by tests that need no server; the two
Docker-backed killers are annotated "pending a server".

## Findings
1. Pre-existing, not fixed (outside owned files): `lattice perf` does not honour studio.toml
   `[engines] paths`; a project-added engine fails argparse in `harness_from_project`.
2. With a given URL and no driver, P-down(b) exits 2 at once with `ModuleNotFoundError`, not the predicted
   30 s timeout message (the prediction assumed the driver is installed).
3. Untested assumption: clickhouse-connect appends ` FORMAT Native` after the descriptor's trailing
   `SETTINGS final = 0`; only a real server decides.

## Orchestrator verification
Independent re-run: `tests/perf tests/spec/test_engines.py tests/test_cli.py` 189 passed, 4 skipped;
probe runner 20/34 pass, 14 not run; `perf_rule_probes` 24/24; mutants 24/24; ruff check and format clean.

## To finish (needs Docker Desktop running and a scratch venv with psycopg, clickhouse-connect, pymysql)
`dogfood/perf_engines_probes.py --only 'P-pg' 'P-ch' 'P-excl(a)' ...` and `dogfood/perf_mutants.py
--python <scratch venv python>`; the exact command is in the data directory's summary.
