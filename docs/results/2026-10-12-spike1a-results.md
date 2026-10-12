# Spike 1a results: performance budget steered by the spec (C0, P1, P2, mutation run)

Run 2026-10-12 on branch `np-spike1a` (worktree `/Users/vivekgade/Documents/lattice-np1a`, from `next-phases`
5ef8b7e). Pre-registration: `docs/results/2026-10-12-spike1a-prereg.md` (written before any probe, with the
development smoke run disclosed). Raw data: `docs/results/data/2026-10-12-spike1a-{c0,p1,p2,mutants}.json`.
Blind results (against the pre-registered criteria) first, post hoc observations after.

## What was built

| File | Lines | What |
|---|---|---|
| `lattice/perf/__init__.py` | 8 | package (not a build gate, not part of `lattice check`) |
| `lattice/perf/budget.py` | 526 | `perf.budget.json` model, load, validation, exact rational fit, verdict, report JSON, text table |
| `lattice/perf/measure.py` | 438 | harness resolution (the code `lattice check` uses), per-size fresh clone + fresh app, API seeding, statement counting (MySQL general_log; in-process SQLite trace callback), timing |
| `lattice/perf/__main__.py` | 98 | `python -m lattice.perf measure|check --project DIR [--budget F] [--json] [--sizes 5,25] [--runs 10] [--id ID] [--statements]` |
| `lattice/cli.py` | +7 -1 | `lattice perf ...` |
| `lattice/spec/dbserver.py` | +14 -6 | `start_container` / `server_for` take a `name_prefix` (default `lattice-`; perf uses `lattice-perf-`) |
| `docs/lattice-reference.md` | +7 | the command, the file format, exit codes, the extra-gate usage `[rebuild] gates = ["./check.sh", "lattice perf check"]` |
| `tests/perf/perfapp.py`, `test_budget.py`, `test_measure.py` | 107, 274, 249 | 61 tests |
| `tests/test_cli.py` | +2 -1 | `perf` in the subcommand list, a dispatch case |
| `dogfood/perf_mutants.py` | 221 | the mutation run (pytest + optional real Streak runs) |

Studio is not changed.

## C0: calibration against P0 (blind)

10 invocations of `measure --id home --runs 10` on the clean clone (a0679e9). **PASS.**

| | result |
|---|---|
| N=5 | 11 in 100 of 100 runs |
| N=25 | 31 in 100 of 100 runs |
| fit | constant 6, per_item 1, exact, in 10 of 10 invocations |
| invocation wall time | 6.6-7.0 s each (container start, seed build, 2 sizes x (app start + seed + 10 requests), teardown) |
| latency median (ms) | N=5: 5.2-6.4; N=25: 12.8-14.0 (P0 by hand: 7.4 and 14.9) |

The statement shapes (from the development run with `--statements`) match P0's table: 3 `test_clock`, 1
`users`, 1 `habits`, N `completions`, 1 nudges join.

## P1: seeded-regression probe (blind)

Budgets checked in every invocation: `habits` = `GET /v1/habits` 4 + 0*N (measured on the clean tip; the
brief expected 3, the tool measured 4: 2 `test_clock` reads, the user lookup, the habits select), `home` =
`GET /v1/home` 6 + 1*N. 10 invocations of `check --runs 10` per variant (each: 2 budgets x 2 sizes x 10
runs).

| Variant | Exit codes | `habits` counts (N=5 / N=25), fit, verdict | `home` counts, fit, verdict |
|---|---|---|---|
| clean a0679e9 | 0 x10 | 4 / 4, 4 + 0N exact, pass 10/10 | 11 / 31, 6 + 1N exact, pass 10/10 |
| S1 c8ab675 (N+1 in `GET /v1/habits`) | 1 x10 | **9 / 29, 4 + 1N, fail 10/10** ("per_item 1 > budget 0") | 11 / 31, pass 10/10 |
| S2 9c80a3d (`GET /v1/home` N+1 removed) | 1 x10 | 4 / 4, pass 10/10 | **7 / 7, 7 + 0N, fail 10/10** on "constant 7 > budget 6"; delta {constant +1, per_item -1}; notes "better than the budget: per_item 0 < 1" and "every measured size is within its allowed count: the constant grew while the per-item cost fell" in 10/10 |
| S3 cf197b6 (no index on `habits.user_id`) | 0 x10 | 4 / 4, pass 10/10 | 11 / 31, pass 10/10 |

Every count in every run equalled its size's value (zero spread; 400 measured requests per variant), every
fit exact.

Against the criteria:

1. Clean, no false alarm 10/10: **met.**
2. S1 detected 10/10: **met** (fit per_item 1, exactly the seeded one query per habit).
3. S2 correctly reported 10/10 (per-item better, every size under its allowed count, no per-item
   regression): **met.** The pre-registered prediction of the verdict also held: `fail`, because the batched
   query makes the constant 7 > 6 and the owner's rule fails a budget when *either* term exceeds it. The
   report states the trade. Secondary: against the P2 budget (8 + 0N) S2 passed in 10/10 with "better than
   the budget: constant 7 < 8".
4. S3 not detected by count (prediction): **held.** Latency did not show it either (prediction held):
   median of the 10 invocation medians of `GET /v1/habits` at N=25: clean 1.865 ms (range 1.77-2.77), S3
   1.841 ms (1.79-2.22), -1.3%. The table holds at most 25 rows of one user; a missing index needs a scale
   the API seed cannot reach (50 active habits per user, two seed users).

**P1: PASS** (criteria 1, 2, 3).

## P2: optimizer probe (n = 1, blind)

Budget `GET /v1/home` constant 8, per_item 0 on the clean clone (before: 11 / 31, fit 6 + 1N, FAIL).
One `claude -p` agent (model `sonnet`, reported as `claude-sonnet-5-5`), flags and prompt as pre-registered
(prompt and command in the raw data).

| | |
|---|---|
| Rounds | **1** (of at most 3) |
| Perf after round 1 | 7 / 7 statements (10 runs each), fit 7 + 0N exact, PASS, exit 0 |
| Gate `./check.sh` after round 1 | **PASS**, exit 0: layers ABCDX, 480 cases, 0 findings, 147.4 s |
| Diff | `internal/progress/routes.go` +41 (new `OfMany`: windows first, one `SELECT habit_id, date FROM completions WHERE habit_id IN (...) AND deleted_at IS NULL AND date BETWEEN min AND max`, then `Compute` per habit; not-started habits left out of the query), `internal/home/routes.go` +6 -6 (calls `OfMany` once). No protected file touched. |
| Agent cost | **$0.108** (`total_cost_usd`; 11 turns) |
| Agent wall time | 37.6 s (API 29.2 s); perf check after the turn 6.4 s; gate 147.4 s |

**P2: PASS** (budget met, full gate green, 1 round, no protected file changed). Prediction: pass in 1-2
rounds (held), $0.3-1.0 (missed: about a third of the low end).

## Mutation run (blind)

`uv run python dogfood/perf_mutants.py --jobs 2 --streak <clone at a0679e9> --python <venv with PyMySQL>`:
control PASS (61 passed; Streak: clean budgets exit 0 with fits 6 + 1N and 4 + 0N, tight budget exit 1).

| Mutant | Caught | pytest failures | Streak run |
|---|---|---|---|
| PM1 fit constant = larger size's count | yes | 9: `test_exactly_at_budget_passes`, `test_fit_constant_is_the_intercept_not_a_count`, `test_fit_is_rational_and_exact`, `test_fit_of_the_p0_numbers_is_n_plus_6`, `test_latency_compared_only_when_set`, `test_render_table`, `test_report_schema`, `test_cli_json_report`, `test_n_plus_one_is_fitted_and_fails_a_constant_budget` | clean budgets exit 1, home fit 31 + 1N |
| PM2 per_item `>=` | yes | 7: `test_exactly_at_budget_passes`, `test_latency_compared_only_when_set`, `test_zero_per_item_budget_met_exactly_passes`, `test_cli_check_pass_is_exit_0`, `test_cli_json_report`, `test_constant_endpoint_counts_exactly`, `test_in_repo_racy_notes_app` | clean budgets exit 1 (false alarm) |
| PM3 tool's own statements counted | yes | 2: `test_app_statements_excludes_...`, `test_mysql_counter_truncates_...` | counts 12 / 32 and 5 / 5 (+1: the tool's own read of `mysql.general_log` is logged and was counted) |
| PM4 failing budget exits 0 | yes | 2: `test_cli_check_over_budget_is_exit_1_and_measure_is_0`, `test_lattice_perf_dispatch` | tight budget exit 0 |

4 of 4 caught. Predictions vs actual: every predicted test failed where it could; PM1 also failed
`test_latency_compared_only_when_set`, `test_render_table`, `test_n_plus_one_...` (not predicted), and did
not fail `test_constant_endpoint_counts_exactly` / `test_cli_check_pass_is_exit_0` (I had listed "the
measure tests asserting the fit, the CLI pass tests": with per_item 0 the intercept equals the count, so
the mutant is invisible there). PM2 also failed `test_latency_compared_only_when_set`, `test_cli_json_report`.

## Verification

| Check | Result |
|---|---|
| `uv run pytest tests/studio tests/mcp tests/test_cli.py tests/spec tests/perf -q --ignore=tests/spec/test_db.py --ignore=tests/spec/test_multidb.py -p no:cacheprovider` | **1106 passed, 5 skipped** (baseline 1043 + 2 new `test_cli` cases + 61 `tests/perf`), 377 s |
| `(cd dogfood/studio && ./check.sh)` | **PASS: 0 findings (D 0, X 0); 387 cases run** (unchanged) |
| `uv run ruff check` on every changed and new Python file | all checks passed |
| `uv run ruff format --check lattice/perf tests/perf dogfood/perf_mutants.py` | 9 files already formatted |
| Streak gate on the clean clone (before P2) | PASS, 480 cases, 0 findings, 148 s |

## Post hoc observations (not pre-registered)

1. **The "either term" rule fails a real improvement.** S2 and the P2 agent's fix both have the shape 7 +
   0N against a clean-tip guard of 6 + 1N: fewer statements at every N >= 2, yet `fail` on the constant. The
   report says so explicitly, but a regression-guard budget written from the current numbers will block
   every N+1 fix that adds one batched query. Options for the owner: judge at the measured sizes only
   (`count(n) <= allowed(n)` for each size), or keep the rule and expect budgets to be rewritten after an
   improvement.
2. **The optimizer task was easy.** One agent turn, 11 tool calls, $0.11: the N+1 is a textbook loop and
   the code already had a pure `Compute` that ignores out-of-window dates. This says nothing about harder
   perf work (a missing index, an unbounded read, a cache).
3. **Bash restriction was not strict for read-only commands.** The agent's `cd ... && ls && cat ...` and
   `cat ...; grep ...` ran although only `go build`, `go test` and the perf wrapper were on
   `--allowedTools`; `dontAsk` mode let them through as read-only. A heredoc that would have written a file
   was denied (1 permission denial), and the agent switched to Edit. Its transcript shows it read only
   files in the clone (`internal/home/routes.go`, `internal/progress/*`), and its one `grep -r` over the
   clone failed on a zsh glob. So the restriction held for writes, not for reads.
4. **The behaviour gate is the guard that made the change safe** (480 cases incl. layer X races). I did not
   run the gate on my hand-made S2; only the agent's change has a green gate.
5. Counting is cheap: a 2-budget check (4 sizes x 10 runs) takes about 7 s wall, almost all container and
   app start-up.
6. The tool's own log read appears in `general_log` (PM3 showed +1), so the connection-id filter is load
   bearing on MySQL.

## Decisions made that the brief did not fix

- **Tool connections are filtered by MySQL connection id, not by account.** In Lattice's container the app
  and the checker both use `root` (the checker hands the app a root URL); giving the app another account
  would change how it is started. The tool excludes its measuring connection and the dbserver's admin
  connection; every other connection during the window is the app's (the container is the tool's own). On
  a given shared server (`LATTICE_MYSQL_URL`) other clients would be counted; the global `general_log`
  setting is switched on there and restored, with a note.
- **Container names:** `dbserver.start_container`/`server_for` gained a `name_prefix` argument (default
  unchanged) so perf containers are `lattice-perf-mysql-*`.
- **`prepare`** (optional top-level key, a shell command run in the project before measuring; Streak's is
  `go build -o bin/streak-api ./cmd/server`), so a check never measures a stale binary.
- **Fit** through the smallest and largest size, each at its largest count over the runs; a middle size off
  the line fails per size; runs that differ make `exact` false (a cache that answers later runs from memory
  shows as differing runs, and the largest count is judged).
- **No warm-up request** (as P0); login and seeding happen before the first measured request, so the
  connection is warm.
- **`--statements`** (off by default) adds statement shapes; the P2 prompt used the default output (numbers
  only), so no shapes reached the agent.
- **Overall verdict:** any `fail` makes `check` exit 1 even if another budget is unmeasured; otherwise any
  `unmeasured` exits 2.
- **SQLite counting** counts every traced statement (BEGIN/COMMIT included) of connections opened on the
  measured file while the app runs; the tests' apps use autocommit connections.
- **Python environment:** the Lattice venv has no PyMySQL, so MySQL runs used a scratch venv layered over
  the Lattice venv plus PyMySQL, with the worktree on `PYTHONPATH`; Streak's `check.sh` ran with
  `UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=<that venv>` (its `pyproject.toml` points at `../lattice`, which
  does not exist next to the clone).
- After the probes I ran `ruff format` on the new files (layout only, no logic change); the mutation run
  and the test suite ran on the formatted code, the probes on the code before formatting.

## Not verified

- **Not supported:** statement counting for a process app on SQLite, Postgres and ClickHouse (reported
  `unmeasured`); endpoints whose path needs a seeded id (no capture of response values); rows scanned or
  statement cost; concurrent load.
- MySQL counting is verified only on Streak (one Go/GORM app, prepared statements); there is no MySQL test
  in `tests/perf` (the Lattice venv has no PyMySQL): the counter's filtering is unit-tested with a fake
  connection, and the real path by C0, P1 and the mutation run's Streak runs.
- The production-mode constant (without `HABIT_TEST_CLOCK`, 3 fewer `test_clock` reads) was not measured.
- Latency budgets (`latency_ms` set) are unit-tested only; no probe used one.
- P2 is n = 1 on an easy target; its cost and round count are not a rate.
- The gate on S2 (my hand-written improvement) was not run.
- `lattice perf check` as a Studio `[rebuild] gates` entry was documented, not run inside Studio.

## Cleanup

The clone, the scratch venv and all probe files were in my scratch directory and removed at the end;
every perf container was removed by the tool (`docker ps -a` lists no `lattice-` container); no
process left. Nothing ran in `/Users/vivekgade/Documents/streak` and nothing was committed in the
Lattice repo. The P2 agent's session transcript stays in Claude Code's own session store
(`~/.claude/projects/...streak-perf/a83c6aab-....jsonl`), as for any `claude -p` run.

## Orchestrator verification and a disclosure (2026-10-12)

Independent checks on the final tree: suite (tests/perf + studio + mcp + test_cli + spec) 1106
passed, 5 skipped; dogfood gate PASS, 387 cases; `dogfood/perf_mutants.py` control PASS and PM1-PM4
caught (9, 7, 2 and 2 failing tests); C0 reproduced by the orchestrator on real MySQL in a scratch
clone of Streak at a0679e9 (11 statements in 10 of 10 runs at N=5, 31 in 10 of 10 at N=25, fit
6 + 1*N exact; `check` exit 0).

**Disclosure: the worktree of this spike was deleted by an orchestrator error and rebuilt.** The
original files were removed (a failed `git add` stopped the commit chain; the following `git
worktree remove --force` still ran). The code, tests, mutants runner and this document were rebuilt
by replaying the subagent's logged Write/edit operations from its session transcript; every new
file has exactly the line count the agent reported and the whole verification above was re-run on
the rebuilt tree. **The raw probe data files (`docs/results/data/2026-10-12-spike1a-{c0,p1,p2,
mutants}.json`) referenced above could not be rebuilt**: they were generated from scratch files the
agent had already deleted. The tables in this document are the record of the probes; the numbers in
them were checked against the agent's report and C0 against the orchestrator's own run.
