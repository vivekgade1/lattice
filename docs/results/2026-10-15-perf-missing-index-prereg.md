# Perf: the missing-index check (EXPLAIN on the measured clone) and seeded-id endpoints - pre-registration

## 1. Status and gap

Date 2026-10-15, branch `np-missidx` (from `np-integ` 30c9120). Written BEFORE any code. Author's
commands: reading code; `pytest tests/perf --collect-only` (**112 tests**); `docker version` (**running**,
29.4.0); Python 3.14.0, SQLite 3.50.4, `go` 1.26.2; no `psycopg`/`pymysql` in the Lattice venv; one check
that `sqlite3.set_trace_callback` receives **expanded** SQL (`... where a = 5`). Results:
`docs/results/2026-10-15-perf-missing-index-results.md`, data `docs/results/data/2026-10-15-perf-missidx/`.
No LLM, no API key, 0 tokens.

Gap: (a) HANDOFF-2026-10-12 "Spike 1a": "Count cannot see a missing index ... needs ... an EXPLAIN-based
check"; spike-1a S3 (no index on Streak `habits.user_id`) passed 10/10 by count and latency (-1.3%).
(b) `grep -i explain lattice/perf tests/perf dogfood/perf_*.py` = 0 hits; statements become `len(stmts)`
and, with `--statements`, `shapes_of`. (c) Seeded ids: measure.py:522 sends `budget.path` unexpanded;
`_steps` allows only `method, path, user, json, count`; `expand` knows `{i}`, `{uuid}`: an id endpoint can
name only a literal id, else 404 (exit 2). **Premise notes:** `tests/perf/perfapp.py` has **no** index on
`items.user_id` (it would itself be flagged; a new fixture, its DDL untouched); the S3 commit `cf197b6` is
**not** in `~/Documents/streak`, so the Streak arm re-makes S3 by the one-line edit of the spike-1a
prereg (`index` dropped from `Habit.UserID`, `internal/db/models.go`) in a scratch clone.

## 2. Decisions

1. **Opt-in flag, not a new budget verdict.** `measure` and `check` gain `--explain` and
   `--explain-min-rows K`. Without `--explain` every output (text, JSON, stderr, exit) is byte-identical
   to 30c9120. `Report.verdict` keeps meaning "the count budget" (pass|fail|unmeasured); the index check
   is a separate per-report object and summary line. Rejected: a fourth verdict (changes
   `overall_verdict` and every consumer); a separate command (re-seeds; the statements must be the
   measured ones); notes only (not machine-readable).
2. **Which statements.** Per budget, only at its **largest** measured size: the raw statements of that
   size's **first** run (kept in memory, not in `SizeResult.to_dict`), deduplicated by `shape()` (first
   occurrence explained). Explained kinds by first keyword: `SELECT`, `WITH`, `UPDATE`, `DELETE`.
   Skipped silently: transaction control, `SET`, `SHOW`, `PRAGMA`, `INSERT`/`REPLACE`, DDL (counted in
   `ignored`). Skipped **with a reason** (listed): text with a `;` outside literals/quoted identifiers
   other than a trailing one (`more than one statement`), an EXPLAIN error (first error line), a plan
   naming a table not resolvable to a base table (`alias not resolved`).
3. **Where and how (safety).** On **the measured clone of that size** (ServerDatabases clone or the
   SQLite copy in the run's temp dir), after `rt.close(app)`, before `release_db`: after the size's last
   counting window. Never the seed template, a given server's own databases, or a URL `make_db` did not
   return (asserted). Never `EXPLAIN ANALYZE`; nothing rewritten. Per engine:
   - **SQLite** (in-process apps, as the counter): `connect("file:<copy>?mode=ro", uri=True)` (not the
     watched path, so not traced), `EXPLAIN QUERY PLAN <stmt>`.
   - **MySQL**: new `open_db(clone_url)`; `SET SESSION TRANSACTION READ ONLY`, `SET SESSION
     max_seeks_for_key = 1` (documented to make the optimizer prefer an index over a table scan: a tiny
     seeded table with a usable index is not reported); `EXPLAIN FORMAT=TRADITIONAL <stmt>`, no params.
   - **Postgres** (16): `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`, `SET statement_timeout =
     '5s'`, `SET enable_seqscan = off` (a Seq Scan survives only when no index can serve), `EXPLAIN
     (GENERIC_PLAN, FORMAT JSON) <stmt>` sent with **no** params (psycopg's simple protocol: pgss texts
     carry `$1`, which GENERIC_PLAN accepts; an extended-protocol Bind would need values).
   - **Other engines** (ClickHouse, mariadb, module engines): `unchecked`, `explain is not supported on
     <title>`. EXPLAIN stays in code keyed by engine name (plan parsing is code; descriptors never name
     code); a descriptor `[explain]` table is a follow-up.
4. **Parameter binding of captured statements.** None is done: MySQL general-log `Query` rows are the
   text sent and `Execute` rows are logged with values substituted; SQLite's trace gives the expanded
   SQL (checked above; if SQLite cannot expand, `?` placeholders still EXPLAIN); Postgres texts are
   normalized (`$n`) and explained as generic plans. Consequence: MySQL and SQLite plans are for the
   seeded values, Postgres plans for no value.
5. **What a finding is** (one per (table, shape); only base tables of the clone):
   - SQLite: a plan row `SCAN <t>` (with or without `USING [COVERING] INDEX`) when the shape contains
     `WHERE`; or any `AUTOMATIC [PARTIAL] [COVERING] INDEX` on `<t>`. `SEARCH ...` is never a finding.
   - MySQL: a row with `type` in {`ALL`, `index`} whose `Extra` contains `Using where` or `Using join
     buffer`; `table` must resolve to a base table (`<derivedN>`, `<subqueryN>`, `<unionM,N>` skipped).
   - Postgres: a `Seq Scan` node with a `Filter`, or an `Index Scan`/`Index Only Scan` with a `Filter` and
     no `Index Cond` (with `enable_seqscan = off` the planner substitutes a full scan of the primary key
     to satisfy `ORDER BY id`; flagging only Seq Scan would miss it). `Relation Name`, not `Alias`.
   - **Size threshold:** a finding only when the table's exact `SELECT COUNT(*)` on the clone (after the
     runs) is >= `min_rows`; default `min_rows` = the budget's largest measured size N (a table holding
     at least one row per seeded item); `--explain-min-rows 0` checks every table. Below the threshold:
     no finding, counted in `below_threshold`. Aliases (MySQL, SQLite) are resolved by
     `(FROM|JOIN) <table> [AS] <alias>` in the shape; unresolved -> skipped with reason, never dropped.
6. **Status per budget:** `checked` (every candidate explained), `partial` (>= 1 skipped with a reason),
   `unchecked` (engine unsupported, the budget unmeasured, or no candidate explained). Zero candidates
   (e.g. an endpoint with no SELECT) is `checked` with 0 explained.
7. **Output, only with `--explain`.** JSON: each report's last key `"explain": {"size", "engine",
   "status", "reason", "min_rows", "explained", "ignored", "below_threshold", "skipped": [{"sql",
   "reason"}], "tables": {name: rows}, "findings": [{"table", "rows", "access", "sql"}]}`, lists sorted by
   (table, sql); `access` = the plan's words without estimates (SQLite detail; MySQL `type=ALL key=NULL
   Extra=<text>`; Postgres `<Node Type> on <rel>, Filter: <text>`); top level's last key `"explain":
   {"verdict": "clean"|"findings"|"unchecked", "findings", "budgets"}`. Text, after a report's notes:
   `  explain at N=<n> (<engine>): <k> statement(s) explained, <s> skipped, <f> finding(s)` (or
   `  explain: <status>: <reason>`), per finding `  - missing index: <table> (<rows> rows) is read by a
   full scan [<access>] in: <sql[:110]>`, per skip `  - not explained: <reason>: <sql[:110]>`; after the
   unchanged `perf:` line, `explain: <verdict> (<f> finding(s) in <b> budget(s), <u> unchecked or partial)`.
8. **Exit codes.** `measure` (with or without `--explain`) and `check` without it: unchanged. `check
   --explain`: **1** if a budget fails or any finding; else **2** if a budget is unmeasured or any status
   is `partial`/`unchecked` (nothing passes unchecked, as the liveness guard); else **0**.
9. **Determinism.** Findings use plan *shape* (access type, node, filter text), never `rows`, `filtered`
   or costs (InnoDB stats are sampled and recalculated asynchronously; autovacuum may ANALYZE a Postgres
   clone mid-run). The knobs of 3 pin index-vs-scan; row counts are exact; uuids vanish in `shape()`; no
   `ANALYZE` is run (it writes statistics).
10. **Seeded-id endpoints.** A seed step may carry `"capture": {"name": "dotted.key"}`: the JSON
    response's value there (string or non-bool number) after the step's **first** repetition becomes
    `{name}` for the size, usable in later steps' `path`/`json` and the endpoint path (percent-quoted in
    paths, `quote(v, safe="")`). Names `[a-z_][a-z0-9_]{0,31}`, not `i`/`uuid`, captured once, used only
    after their step (else BudgetError). **Compatibility:** only captured names are substituted; any other
    `{x}` stays literal (a 30c9120 file means the same). Missing key or non-scalar: MeasureError, exit 2,
    `budget <id> N=<n>: seed step <METHOD path> #1: capture <name>: response has no <key>`. The report
    keeps the endpoint template and adds one note `seeded id: {item} from seed step <k> (key id)` (no
    value: uuids would break 10-identical). Why here: a request on a missing id runs another path (404),
    and MySQL EXPLAINs a const lookup of a missing key as `no matching row in const table`.

## 3. Probes (`dogfood/perf_index_probes.py`; canonical JSON as the engines prereg: `--json` without
`latency_ms`, `project`, `budget_file`, container and run names replaced; "10 identical" = 1 sha256)

**Fixture `tests/perf/indexapp.py`** (FastAPI, `perfapp` pattern; SQLite path, or a Postgres/MySQL URL
with the driver imported when used): `users(id, name, token)` ann/ben, `items(id, user_id, name)`,
`notes(id, item_id, body)`, indexes `items_user_id ON items(user_id)`, `notes_item_id ON notes(item_id)`.
`POST /login`; `POST /items` -> `{"id"}`; `POST /items/{id}/notes`; `GET /items` (caller + `WHERE
user_id = ? ORDER BY id`: 2 + 0N); `GET /items/{id}/notes` (caller + `items WHERE id = ? AND user_id = ?`
+ `notes WHERE item_id = ? ORDER BY id`: 3 + 0N). Variants by db factory: `build_db` (clean), `drop_items`
/ `drop_notes` (the clean schema, then `DROP INDEX` on that copy). Budgets: `items` = `GET /items` 2 + 0N,
seed `POST /items` xN as ann; `notes` = `GET /items/{item}/notes` 3 + 0N, seed `POST /items` x1 with
`capture {"item": "id"}`, then `POST /items/{item}/notes` xN. Sizes `2,6`, `--runs 10`.

**P-sqlite (first; runnable now) and P-det (10 identical per engine and variant).** `check --explain
--json`, 10 invocations per variant:

| variant | items finding | notes finding | explain verdict | exit |
|---|---|---|---|---|
| clean | none | none | clean | 0 |
| drop_items | `items (6 rows)`, access `SCAN items` | none (items has 1 row) | findings | 1 |
| drop_notes | none | `notes (6 rows)`, access `SCAN notes` | findings | 1 |

Clean access rows predicted `SEARCH items USING INDEX items_user_id (user_id=?)`, `SEARCH items USING
INTEGER PRIMARY KEY (rowid=?)`, `SEARCH notes USING INDEX notes_item_id (item_id=?)`; `users` (2 rows <
6) never a finding. Counts 2/2 and 3/3 in every run of every variant, verdicts `pass`, statuses
`checked`. Arm (b): `--explain-min-rows 0` on clean: exactly one finding per budget, `users (2 rows)`
`SCAN users` (the token lookup). **Pass:** every cell in 10/10, (b) 10/10, 1 hash per variant.

**P-byte: old outputs unchanged.** Old = `git archive 30c9120 lattice` in `$SCRATCH/old`. (a) The 112
existing tests source-identical (per-function AST) and passing. (b) `perfapp` project (`test_measure.
_project`), `measure` and `check`, text and `--json`, `--statements` on/off, 10x old + 10x new: **1
distinct hash per command over 20**, same exits. (c) Six invalid budget files (as `test_budget`'s
messages): stderr identical old/new; one file with a step key `capture` differs (old: unknown key; new:
valid) and is the only allowed difference. (d) The indexapp project **without** `--explain`: old = new.
(e) Counts with `--explain` = counts without, 10/10 (the EXPLAIN is outside every window).
**Pass:** (a)-(e).

**P-seedid.** (a) Old tool, `notes` budget: exit 2 (BudgetError `unknown key(s) capture`). (b) New: counts
3/3 at N=2 and 6, exact, the note once, 10 identical. (c) Capture key `nope`: exit 2, stderr as decision 10.
(d) `{x}` not captured stays literal in the request path (fake transport records it). (e) A captured
value `a/b` reaches the app as `a%2Fb`. **Pass:** (a)-(e).

**P-safe.** (a) `SELECT 1; DELETE FROM items` injected into the explain list (probe hook): skipped
`more than one statement`, rows unchanged, `partial`, exit 2. (b) The explain connection cannot write
(an `INSERT` on it fails: SQLite `mode=ro`, MySQL/Postgres read-only session). (c) Every SQL the explain
step sends is `EXPLAIN ...`, a session `SET` or `SELECT COUNT(*)`. (d) Process app on SQLite: count
`unmeasured` (note unchanged), explain `unchecked`, exit 2. **Pass:** (a)-(d); (b)/(c) server arms run with P-pg/P-mysql.

**P-pg (Docker + scratch venv with `psycopg[binary]`).** indexapp on Postgres, in-process, same budgets.
Predicted: clean 0 findings; drop_items `items (6 rows)` with access `Seq Scan on items, Filter: (user_id
= $1)` **or** `Index Scan on items, Filter: (user_id = $1)` (the primary key, ORDER BY id) - the pass rule
judges table + finding, not which access; drop_notes likewise on `notes`. Statuses `checked` (GENERIC_PLAN
explains every SELECT). **Pass:** as P-sqlite, 10/10 per variant, 0 `lattice-perf-*` containers after.

**P-mysql (Docker + scratch venv with `pymysql`).** (i) indexapp on MySQL 8.4: clean 0 findings;
drop_items `items (6 rows)` `type=ALL key=NULL Extra=Using where; Using filesort` (or `type=index` with
`Using where`); drop_notes on `notes`. (ii) Streak arm (needs `go`): `git clone --no-hardlinks
~/Documents/streak $SCRATCH/missidx/streak` at a0679e9; budget `habits` = `GET /v1/habits` 4 + 0N, sizes
5, 25, seed `POST /v1/habits` xN (spike-1a prereg); clean and S3 (the one-line edit), 10 invocations
each. Predicted: clean **0 findings on tables >= 25 rows**; S3 a finding on `habits (25 rows)`.
**Pass:** (i) as P-sqlite; (ii) S3 caught 10/10, clean 0 findings 10/10, counts as spike 1a (4 / 4).

**Runnable now vs later.** P-sqlite, P-byte, P-seedid, P-safe, P-det (SQLite) run in the Lattice venv.
P-pg and P-mysql(i) need a scratch venv over it: `uv venv --python /Users/vivekgade/Documents/lattice/
.venv/bin/python $SCRATCH/missidx/venv`, `uv pip install "psycopg[binary]>=3.2" "pymysql>=1.1"` into it
(network), a `.pth` to the Lattice `site-packages`; Docker is running. P-mysql(ii) also needs `go`. A probe
that cannot run is **NOT RUN** with its reason, never PASS. ClickHouse: no arm; a `fakeengines` unit test
shows `unchecked`, exit 2.

## 4. Mutation plan (MI1-MI12 appended to `dogfood/perf_mutants.py`)

Tests named now: `tests/perf/test_explain.py` (classifiers on plan fixtures, the SQLite path, CLI exits),
`tests/perf/test_seeded_id.py`; Docker arms in `tests/perf/test_explain_docker.py` (skip with reason).

| id | mutant | predicted killers |
|---|---|---|
| MI1 | SQLite: bare `SCAN t` not a finding (only AUTOMATIC INDEX) | `test_sqlite_drop_index_is_found` |
| MI2 | SQLite: `SEARCH ... USING INDEX` counted as a scan | `test_sqlite_clean_has_no_finding` |
| MI3 | threshold `>` instead of `>=` (6 rows at N=6 dropped) | `test_threshold_boundary_counts` |
| MI4 | Postgres: only `Seq Scan` flagged (PK scan + Filter missed) | `test_pg_full_index_scan_filter` |
| MI5 | Postgres: `enable_seqscan = off` not sent | `test_pg_explain_session_statements` |
| MI6 | MySQL: `type=ALL` flagged without `Using where` | `test_mysql_plan_rows_classified` |
| MI7 | the `;` guard removed | `test_multi_statement_is_skipped_not_run` |
| MI8 | SQLite explain connection opened read-write | `test_explain_connection_is_read_only` |
| MI9 | explain at the smallest size | `test_explain_runs_at_largest_size` |
| MI10 | explain before the last window (statements counted) | `test_counts_identical_with_explain` |
| MI11 | `check --explain` exits 0 on a finding / on `partial` | `test_cli_explain_exit_codes` |
| MI12 | capture substitutes every `{x}` (uncaptured too) | `test_uncaptured_braces_stay_literal` |

Prediction: control PASS, the 24 existing mutants still killed, MI1-MI12 killed without a server
(MI4-MI6 by plan fixtures in the engines' documented formats): **36/36**.

## 5. Risks and limits

1. **Planner choice on tiny tables.** Every seeded item belongs to one user, so the index is not
   selective at seed scale: unpinned, Postgres picks Seq Scan on a 1-page table and MySQL may pick ALL.
   The knobs of decision 3 are the mitigation; if MySQL still picks ALL with the index, the clean arm
   fails and is reported so, not tuned away.
2. **EXPLAIN is not the executed plan.** Postgres generic vs custom plans; MySQL plans for the seeded
   values; both knobs change the plan the app would get. The check answers "can an index serve this
   predicate", not "is the production plan fast".
3. **Coverage gaps:** `INSERT ... SELECT`; a join's driving table without WHERE (a Seq Scan without
   Filter); wrong-order composite indexes unless the plan scans; `LIKE '%x'`, functions on columns;
   tables below `min_rows` (a 2-row sessions table) are invisible by default.
4. **Unmeasured engine behaviour:** MySQL may refuse EXPLAIN UPDATE/DELETE in a READ ONLY session, and
   pgss text may not EXPLAIN (types not inferable from `$n`, an app-set `search_path`): either makes
   the budget `partial` (exit 2 under `check --explain`). Alias regex misses quoted/qualified names
   (skipped with reason). The simple protocol is why the `;` guard and read-only session are both kept.
5. **The builder reads these predictions**; P-mysql(ii) is the only real app (n = 1, MySQL only).

## 6. Blind vs post hoc, and commands

All above is fixed before code; a missed prediction is a finding with its output, never fixed silently;
what is learned while building goes under "Post hoc".

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-missidx PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad
cd /Users/vivekgade/Documents/lattice-missidx
uv run pytest tests/perf -q -rs                                   # 112 old + new; Docker tests skip w/ reason
uv run python dogfood/perf_index_probes.py --runs 10 --only P-sqlite P-byte P-seedid P-safe P-det \
  --out docs/results/data/2026-10-15-perf-missidx
$SCRATCH/missidx/venv/bin/python -m pytest tests/perf -q -rs      # with Docker + drivers: 0 skipped
$SCRATCH/missidx/venv/bin/python dogfood/perf_index_probes.py --runs 10 --only P-pg P-mysql P-det \
  --streak $SCRATCH/missidx/streak --out docs/results/data/2026-10-15-perf-missidx
uv run python dogfood/perf_mutants.py                             # control + 36/36
uv run pytest tests/spec/test_engines.py tests/test_cli.py tests/perf -q -p no:cacheprovider
uv run ruff check lattice/perf tests/perf dogfood/perf_*.py; uv run ruff format --check lattice/perf tests/perf
docker ps -a --filter name=lattice-perf- --format '{{.Names}}'    # empty
```

**Pass for the change:** P-sqlite, P-byte, P-seedid, P-safe(SQLite), P-det(SQLite) PASS; server arms
PASS or NOT RUN with reason (never silently skipped); 36/36 mutants; 112 old tests source-identical and
green; ruff clean; no file outside section 7 changed.

## 7. File ownership (build node)

Owned: new `lattice/perf/explain.py` (EXPLAIN runners, plan classifiers, alias resolution, `;` guard);
`lattice/perf/budget.py` (`SeedStep.capture` + validation + expansion, `Report.explain` emitted only
when set, render lines, the exit helper); `lattice/perf/measure.py` (`measure(..., explain=None)`,
capture and endpoint expansion, run-1 raw statements at the largest size, EXPLAIN between `rt.close` and
`release_db`); `lattice/perf/__main__.py` (flags, exits); new `tests/perf/{indexapp,test_explain,
test_seeded_id,test_explain_docker}.py`, `dogfood/perf_index_probes.py`; `dogfood/perf_mutants.py`
(append MI1-MI12 only); the results doc and data dir. **Not touched:** `tests/perf/perfapp.py` and the 112 existing tests, `lattice/spec/*`
(engines, descriptors, dbserver, db), `lattice/cli.py` (`lattice perf` already forwards argv; its usage
line may gain `[--explain]` only at integration), Studio, MCP, docs outside `docs/results`.

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**A. Environment is better than section 3 assumed.** Docker Desktop is running, images `mysql:8.4` and
`postgres:16-alpine` are local, `go` is at /opt/homebrew/bin/go, and a scratch venv with `psycopg`,
`pymysql`, `clickhouse-connect` layered over the Lattice venv already exists at
`/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/perfengines/perfvenv`
(use its python read-only; do not install anything into the Lattice venv). P-pg and P-mysql arms are therefore
**RUN**, not deferred. A probe that still cannot run is NOT RUN with its reason.
**B. Streak.** If commit `a0679e9` is missing in `~/Documents/streak` (`git cat-file -t` fails), use the
`main` tip at clone time, record its sha, and say so; the arm's prediction (clean 0 findings on tables >= 25
rows; S3 a finding on `habits`) is unchanged. Streak is read only (clone into scratch).
**C. Base.** The branch is cut from the integration branch `np-integ` (which holds perf engines); byte-compat
(P-byte) is against `30c9120` as written in the prereg. Section 7 file ownership stands; `lattice/spec/*`
stays untouched.
**D. A failed prediction is a finding** (for example MySQL choosing a full scan on a tiny clean table): report
the raw EXPLAIN, do not tune the knobs or thresholds to make it pass.
