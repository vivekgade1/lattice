# Pre-registration: spike 1a, performance budget steered by the spec (C0, P1, P2 and the mutation run)

Written 2026-10-12 before C0, P1, P2 or any mutant was run. Design: `docs/design/spikes/01a-feature-performance.md`;
baseline: `docs/results/2026-10-12-p0-query-count-baseline.md` (P0: `GET /v1/home` on Streak `build/streak`
a0679e9 = N + 6 statements, MySQL general_log, `Query` + `Execute` rows of the app's account). Defaults in
force: `docs/design/next-phases-plan.md` (query count first, budgets in a side file, not a build gate, CLI
backend only, agents never commit). Results go to `docs/results/2026-10-12-spike1a-results.md`, blind (the
criteria below) and post hoc separately; a failed prediction is reported as a finding, never silently fixed.
Raw data: `docs/results/data/2026-10-12-spike1a-*.json`.

## What is under test

`lattice/perf/` (new; not part of `lattice check`, not a build gate):

- `budget.py`: the side file `perf.budget.json` (`{"version": 1, "prepare"?: "<shell>", "budgets": [{id,
  endpoint, user, seed: {items: "N", steps: [{method, path, user, json, count}]}, sizes, queries: {constant,
  per_item}, latency_ms: null | {median, size, margin}}]}`), validation, the fit and the verdict. A budget is
  `constant + per_item * N` statements per request. The fit takes the line through the smallest and the
  largest size (each at its largest count over the runs), in exact rational arithmetic; `exact` when every
  run of every size lies on it. **Verdict `fail`** when the fitted constant > budget constant, or the fitted
  per_item > budget per_item, or a size's largest count > `constant + per_item * n` of the budget (the owner
  default: "fails when either exceeds"); a fitted value below the budget's is reported as "better than the
  budget" with a `delta` (fit minus budget). Latency (min/median/max of the runs per size) is compared only
  when `latency_ms` is set: median at `size` must be <= `median * (1 + margin)`.
- `measure.py`: the project's harness resolved with the same code as `lattice check`
  (`lattice.cli._fill_check_args` + the checker's own argument parser and loaders); the harness's `db`
  factory builds the seed once (`ServerDatabases`), each size gets a fresh clone and a fresh app (the
  harness's runtime: for Streak a `ProcessApp` on a free port, `/health` awaited), login through the
  harness's `login`, the seed's steps sent through the API, then `runs` measured requests on one
  connection. Counting on MySQL: `SET GLOBAL log_output='TABLE', general_log=1`; before each measured
  request `TRUNCATE mysql.general_log`; after the answer, count rows with `command_type` in (`Query`,
  `Execute`) whose `thread_id` is not one of the tool's own connections (its measuring connection and the
  dbserver's admin connection). The MySQL server is a throwaway `mysql:8.4` container named
  `lattice-perf-mysql-*` (the checker's dbserver with a name prefix). SQLite in-process: a `sqlite3` trace
  callback on connections to the measured file. Process app on SQLite, Postgres, ClickHouse: `unmeasured`.
- CLI: `python -m lattice.perf measure|check --project DIR [--budget FILE] [--json] [--sizes 5,25] [--runs
  10] [--id ID] [--statements]` and `lattice perf ...`. `check` exits 0 pass, 1 over budget, 2 cannot run
  or unmeasured; `measure` 0 measured, 2 otherwise.

## Setup (all probes)

- Lattice: worktree `/Users/vivekgade/Documents/lattice-np1a` (branch `np-spike1a`), on `PYTHONPATH`;
  Python: a scratch venv layered over the Lattice venv plus PyMySQL (the Lattice venv has no PyMySQL).
- Streak: `git clone --no-hardlinks /Users/vivekgade/Documents/streak <scratch>/streak-perf`,
  `git checkout a0679e9` there. Nothing runs in `/Users/vivekgade/Documents/streak`.
- Budget file `<clone>/perf.budget.json` (untracked in the clone), `prepare` = `go build -o
  bin/streak-api ./cmd/server`, seed for every budget: `POST /v1/habits` as ann, `count` "N", body `{"id":
  "{uuid}", "name": "habit-{i}", "period": "week", "target_count": 3, "start_date": "2026-03-02"}`; sizes
  5 and 25. Every invocation starts its own container and removes it.

**Disclosure: a development smoke run happened before this document.** While building the tool I ran
`measure --runs 3 --statements` once on the clean clone with two budgets: `GET /v1/home` gave 11, 11, 11
(N=5) and 31, 31, 31 (N=25), fit 6 + 1*N exact; `GET /v1/habits` gave 4, 4, 4 and 4, 4, 4, fit **4 + 0*N**
(2 `test_clock` reads, 1 user lookup, 1 habits select). The brief expected constant 3 for `/v1/habits`; the
measured clean-tip value is 4, so the `/v1/habits` budget below is **constant 4, per_item 0**. No seeded
defect and no agent had been run.

## C0: calibration against P0

10 invocations of `python -m lattice.perf measure --project <clone> --id home --runs 10` on the clean clone
(budget `home`: `GET /v1/home`, constant 6, per_item 1).

**Pass iff** in all 10 invocations: every one of the 10 runs at N=5 counts 11 and every one at N=25 counts
31 (200 of 200 counts), and the fit is constant 6, per_item 1, exact. Prediction: pass (10/10).

## P1: seeded-regression probe

Three seeded defects, each one throwaway commit on its own branch in the clone, off a0679e9:

- **S1** (`internal/habits/routes.go`, `list`): after the page is read, a loop over the rows runs
  `SELECT count(*) FROM habit_targets WHERE habit_id = ?` per habit (result unused). Predicted count for
  `GET /v1/habits`: 4 + 1*N (9 at N=5, 29 at N=25).
- **S2** (`internal/home/routes.go`, `home`): the per-habit `progress.Of` (one completions select per habit)
  replaced by one batched `SELECT habit_id, date FROM completions WHERE habit_id IN (...) AND deleted_at IS
  NULL AND date BETWEEN <min window start> AND <max window end>`, then `progress.Compute` per habit on its
  dates (Compute ignores dates outside the habit's window). An improvement. Predicted count for
  `GET /v1/home`: 7 + 0*N (the 6 constant statements plus the one batched select).
- **S3** (`internal/db/models.go`): the `index` dropped from `Habit.UserID`, the column `GET /v1/habits` (and
  `/v1/home`) filter by. Count-invisible by construction.

Budget file for P1 (both budgets checked in every invocation): `habits` = `GET /v1/habits`, constant 4,
per_item 0 (measured on the clean tip, above); `home` = `GET /v1/home`, constant 6, per_item 1 (the clean
tip's own numbers, i.e. a regression guard). Per variant (clean, S1, S2, S3): `go build`, then 10
invocations of `python -m lattice.perf check --project <clone> --runs 10 --json` (one container per
invocation).

Hypotheses and criteria:

1. **Clean: no false alarm in 10/10** invocations (exit 0, both budgets `pass`).
2. **S1 detected in 10/10**: exit 1, `habits` verdict `fail` with fitted per_item 1 > 0 (counts 9 and 29).
   `home` unaffected (pass).
3. **S2 correctly reported in 10/10**: the `home` report shows the fitted per_item 0 below the budget's 1
   ("better than the budget", `delta.per_item` = -1) and every measured size under its allowed count (7 <=
   11, 7 <= 31); no per-item regression is reported. **Predicted verdict: `fail`**, because the batched
   select makes the constant 7 > 6 and the fixed rule fails a budget when *either* term exceeds it; the
   report must then also carry the note that every measured size is within its allowed count (a trade of
   constant for per-item). This is a prediction about the rule, not a criterion the tool can meet by
   design; if it happens it is reported as a finding about the rule. Secondary (reported, not a
   criterion): against the P2 budget (constant 8, per_item 0) S2 passes with "better than the budget:
   constant 7 < 8".
4. **S3 not detected by count** (predicted): both budgets pass in 10/10, counts identical to the clean tip.
   Latency: predicted no detectable difference either (median of `GET /v1/habits` at N=25 within +/-20% of
   the clean tip's median over the same 10 invocations), because the table holds at most 25 rows of one
   user; a missing index needs data at a scale the API seed cannot produce (50 active habits per user, two
   seed users).

**P1 passes iff** 1, 2 and 3 hold (3 as defined: the improvement is reported as better on per-item with
every size under its allowed count). 4 is a recorded prediction, not a pass criterion.

## P2: optimizer probe (n = 1, labelled as such)

- Clean clone (S1-S3 branches not checked out; tracked files at a0679e9). Budget file with one budget:
  `home` = `GET /v1/home`, constant 8, per_item 0 (sizes 5, 25). The clean tip fails it (6 + 1*N).
- One `claude -p` agent, model `sonnet`, cwd = the clone, environment without `ANTHROPIC_*`, flags as
  Lattice's rebuild agent minus the MCP server: `--setting-sources "" --disable-slash-commands
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' --tools Read,Grep,Glob,Edit,Bash --permission-mode
  dontAsk --allowedTools Read Grep Glob Edit "Bash(go build:*)" "Bash(go test:*)" "Bash(<perf wrapper>:*)"
  --disallowedTools Write,NotebookEdit,WebFetch,WebSearch --output-format json`. The perf wrapper is a
  script outside the repo that runs `python -m lattice.perf "$@"` with the setup above.
- Its prompt gives only: the budget (the JSON), the text output of `lattice perf check` on the clean clone
  (the table of numbers: no statement shapes, `--statements` not used), the instruction to make the
  endpoint meet the budget without changing its behaviour, the rule not to edit `perf.budget.json`,
  `spec.py`, `spec.trace.json`, `harness.py`, `check.sh`, `REQUIREMENTS.md`, `CONTEXT.md`, and the commands
  it may run. No hint where the N+1 is.
- **A round** = one agent turn, then (by me, not the agent) `lattice perf check` (10 runs per size) and
  Streak's full gate `./check.sh` (layers ABCDX, all behaviour scenarios, MySQL container). Round n > 1
  resumes the same session (`--resume`) with that round's perf output and the gate's verdict and failing
  scenario names. At most 3 rounds.
- **Pass iff** after some round <= 3: `lattice perf check` exits 0 (budget met) AND `./check.sh` exits 0,
  and the diff touches none of the protected files above (a change to one of them disqualifies the run).
- Reported: rounds, the diff (files, lines), cost (`total_cost_usd` of each turn), agent wall time and the
  per-round perf and gate wall time.
- **Prediction:** pass in 1-2 rounds, about $0.3-1.0 of agent cost.

Baseline for the gate: `./check.sh` on the clean clone is run once before P2 (expected PASS, as
`build/streak` is the owner's passing build); if it does not pass, P2 is reported as not runnable.

## Mutation run

`dogfood/perf_mutants.py` (modelled on `dogfood/studio/sync_mutants.py`): `lattice/` and `tests/` copied to
a scratch directory, the mutant applied there, `pytest tests/perf` run with the copy first on `PYTHONPATH`;
optionally (`--streak DIR`) also a real `lattice perf check` of the Streak clone with known budgets. A
control (unpatched copy) first. Predicted catches:

| Mutant | Behaviour | Predicted to be caught by |
|---|---|---|
| PM1 | the fit takes the larger size's count as the constant | `test_fit_of_the_p0_numbers_is_n_plus_6`, `test_fit_constant_is_the_intercept_not_a_count`, `test_fit_is_rational_and_exact`, `test_exactly_at_budget_passes`, `test_report_schema`, the measure tests asserting the fit, the CLI JSON and pass tests; Streak run: the clean budget fails |
| PM2 | per_item compared with `>=` instead of `>` | `test_exactly_at_budget_passes`, `test_zero_per_item_budget_met_exactly_passes`, `test_constant_endpoint_counts_exactly`, `test_in_repo_racy_notes_app`, `test_cli_check_pass_is_exit_0`; Streak run: false alarm on the clean tip |
| PM3 | counting includes the tool's own statements (the thread filter dropped) | `test_app_statements_excludes_the_tools_connections_and_other_commands`, `test_mysql_counter_truncates_counts_app_rows_and_restores`; not by the SQLite tests (the tool issues no SQLite statements); Streak run: counts +1 or more if the tool's log read is logged |
| PM4 | a failing budget exits 0 | `test_cli_check_over_budget_is_exit_1_and_measure_is_0`, `test_lattice_perf_dispatch` |

Pass: the control passes and all four are caught.
