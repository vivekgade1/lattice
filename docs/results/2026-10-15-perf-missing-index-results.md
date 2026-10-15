# Results: perf missing-index check and seeded-id capture (G14)

Pre-registration: `2026-10-15-perf-missing-index-prereg.md` (with Amendment 1). Branch `np-missidx`.
Data: `docs/results/data/2026-10-15-perf-missing-index/`.

## What was built
`lattice perf measure|check --explain [--explain-min-rows K]`: after the measurement windows, on the measured
clone of the largest size, the statements a request issued are EXPLAINed (read-only session/connection, no
`ANALYZE`, one statement per text) and a full scan of a table with at least the budget's largest seeded
size rows is a finding, judged by plan shape only. SQLite, MySQL and Postgres; ClickHouse reports
`unchecked` (exit 2). Seed steps may carry `"capture": {"name": "key"}` so an endpoint keyed by an id
measures the real statement instead of a 404 path. Without `--explain` every output is byte-identical to
30c9120 (P-byte).

## Result: 13/13 probes pass, 0 not run; mutants 36/36 killed
Every arm ran, including Postgres and MySQL in Docker and the Streak arm: a dropped index is caught on all
three engines (SQLite `SCAN items`; MySQL `type=index key=PRIMARY Using where`; Postgres `Index Scan ...
Filter: (user_id = $1)`), the clean app has 0 findings, and Streak at `a0679e9` with the one-line S3 edit
(no index on `habits.user_id`) gives `habits (25 rows)` 10/10 while clean Streak gives 0. Counts with
`--explain` equal counts without. Tests: `tests/perf` + `test_engines` + `test_cli` 273 passed, 0 skipped
(with the driver venv); 80 new tests; `perf_rule_probes` 24/24; `perf_mutants` MI1-MI12 and the earlier 24
killed; `check.sh` PASS 435; ruff clean. No container left.

## Findings / observations
1. On a missing index the planners did not report `Seq Scan`/`type=ALL` as the pre-registration's headline
   case: Postgres and MySQL satisfy `ORDER BY id` by scanning the primary key with a filter. The
   pre-registration predicted this (decision 5) and the classifier flags it.
2. A PyMySQL app that opens one connection per request carries +1 statement (`SET NAMES utf8mb4`); the
   fixture opens one connection per app on MySQL. Real observation about the counter, not a defect.
3. `view` names pass the base-table check (it is a successful `SELECT COUNT(*)`).
4. Not flagged by design: joins whose driving table has no WHERE, `INSERT ... SELECT`, tables below the
   threshold. Streak is the only real app (n=1, MySQL). EXPLAIN is not the executed plan.

## Post hoc
Script corrections only (MySQL fixture connection, a cwd that made "old" import the new tree, a wrong
target for the capture byte-compat file, the S3 edit as a regex); none changed an expectation.

## Orchestrator verification
Independent re-run: 273 passed 0 skipped (driver venv); ruff check and format clean; P-sqlite, P-seedid,
P-safe(+postgres,+mysql) 5/5 and the Streak arm (clean 0, S3 caught, counts 4/4) at 2-3 runs; diff stat
shows only the owned files. (My first Streak-arm run used the owner's checkout, which holds only the
scaffold: login 404; the probe needs the `a0679e9` clone, which the runner's prereg names.)
