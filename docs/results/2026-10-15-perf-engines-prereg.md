# Perf budgets beyond MySQL (Postgres, ClickHouse) through the engine descriptors - pre-registration

## 1. Status

Date: 2026-10-15. Branch `np-perfengines` (worktree `lattice-perfengines`, from `next-phases` a4ab4a1),
written BEFORE any code of this change; nothing about the new counting was run. Author's commands:
reading code; `pytest tests/perf --collect-only` (baseline **68 tests**); `docker version` (daemon **not
running** when written: start Docker Desktop before the probes); `go` 1.26.2, `uvicorn`, `fastapi`
present; the Lattice venv has **no** `psycopg`, `clickhouse-connect`, `pymysql`. Gap: HANDOFF-2026-10-12
section 4, "Spike 1a". Results: `docs/results/2026-10-15-perf-engines-results.md`, raw data
`docs/results/data/2026-10-15-perf-engines/`. No LLM in the change or the probes, no API key, 0 tokens.

## 2. Decisions

1. **Files.** Owned and changed: `lattice/perf/measure.py`; `lattice/spec/engines/{mysql,postgres,
   clickhouse}.toml` (a `[perf]` table only); `tests/perf/` (new `serverapp.py`, `test_counting.py`,
   `test_counting_docker.py`; the 68 existing tests source-identical); `dogfood/perf_mutants.py`; new
   `dogfood/perf_engine_probes.py`. **Three minimal edits outside the owned set are unavoidable:**
   (a) `lattice/spec/engines.py` rejects unknown top-level keys, so it must accept and validate `[perf]`;
   (b) `lattice/spec/dbserver.py`: `start_container` and `server_for` gain `extra_args: Sequence[str] = ()`,
   appended after `[server] args` (empty by default: every existing `docker run` argv unchanged);
   (c) `tests/spec/test_engines.py`: one expected string (unknown field 'colour') gains `, perf`. Docs, by
   the orchestrator: `docs/design/lattice-module-descriptor.md`, `docs/lattice-reference.md`. Untouched:
   `budget.py`, `__main__.py`, `lattice/cli.py`, Studio, modules, contracts, mcp, improve, security,
   `docs/examples/engines/mariadb.toml` (`counter = "general_log"` there is a follow-up, not probed).
2. **Descriptor table `[perf]`**, optional. `EngineDescriptor.perf: Perf | None = None` (frozen dataclass of
   strings and tuples, declared after `base_dir`, part of `==`); `to_dict()` adds `perf` only when present,
   defaults filled, so `parse_descriptor(e.to_dict()) == e` holds. Fields in order: `counter` (required),
   `source`, `server_args`, `setup`, `check`, `begin`, `mark`, `flush`, `read`. Validated with `_Reader`,
   exact messages after the descriptor's `where:` prefix:
   - `perf.counter: must be "general_log" or "sql", not 'x'`. No `module:Class`: a descriptor never names
     counting code. `general_log` allows no other key: `perf.<key>: only for counter = "sql"`.
   - `sql` requires `read` and `source` (`[perf] missing required field 'read'`). Lists of non-empty
     strings: `server_args`, `setup`, `check`, `begin`, `flush`; strings: `source`, `mark`, `read`.
     Unknown key: `[perf] unknown field 'docker_args' (known: counter, source, server_args, setup, check,
     begin, mark, flush, read)`: a descriptor cannot add `docker run` options.
   - Placeholders: none in `setup`, `check`, `mark`; `{db}` in `begin`, `flush`; `{db}`, `{mark}` in
     `read` (unknown: the existing `unknown placeholder {x} (known: ...)`). `perf.read: must use {db} (the
     measured database; the tool's own statements are excluded by it)`; `perf.read: must use {mark} when
     perf.mark is set`; `perf.read: {mark} needs perf.mark`.
   - At run time each `{db}`/`{mark}` becomes the engine's `sql.placeholder` with its value passed as a
     parameter, in order of occurrence (never spliced into SQL). A literal `%` must be written `%%`.
3. **Dispatch** in `measure._databases` (the hard-coded `"mysql"` goes). `sqlite`: both branches
   unchanged. Else `e = engines.find(h.database)`: `None` -> the a4ab4a1 unmeasured note; `e.perf is None`
   -> unmeasured, **no server started**, note `unmeasured: statement counting on {e.title} is not
   supported (its engine descriptor has no [perf] section)`; `general_log` -> the MySQL branch, body
   unchanged except `server_for(e.name, ...)`; `sql` -> the new `SqlCounter` branch.
4. **Postgres: `pg_stat_statements` (pgss), filtered by the measured database.** Descriptor:
   `server_args = ["-c", "shared_preload_libraries=pg_stat_statements", "-c",
   "pg_stat_statements.track=top", "-c", "pg_stat_statements.track_utility=on"]`,
   `setup = ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements"]`,
   `check = ["SELECT count(*) FROM pg_stat_statements"]`,
   `begin = ["SELECT pg_stat_statements_reset(0::oid, (SELECT oid FROM pg_database WHERE datname =
   {db}), 0::bigint)"]`, `read = "SELECT query, calls FROM pg_stat_statements WHERE dbid = (SELECT oid
   FROM pg_database WHERE datname = {db}) AND toplevel ORDER BY query, queryid"`, no `mark`, no `flush`,
   `source = "pg_stat_statements (top-level statement executions in the measured database)"`.
   **Why deterministic:** pgss updates shared memory synchronously in the backend (ExecutorEnd /
   ProcessUtility) before ReadyForQuery (simple protocol: portal dropped before CommandComplete;
   extended + autocommit: unnamed portal cleaned at the implicit commit on Sync; in a transaction: at the
   next Bind or COMMIT). Drivers return to the app only after ReadyForQuery, so once the HTTP answer is
   read every statement is counted: **no flush, polling, sleep or timeout**. The reset is per `dbid`: a
   given server's other statistics, and a parallel perf run on another clone, are untouched.
   **Rejected:** `log_statement=all` + server log (the collector is fed through an asynchronous pipe:
   sentinels, polling and timeouts per window; needs `logging_collector` restart, superuser
   `pg_read_file`, rotation off); `pg_stat_database` (transactions, flushed asynchronously);
   `pg_stat_activity` sampling (misses statements); `docker logs` (CLI-bound, container-only).
   **Accepted differences** (documented): failed statements are not counted (MySQL counts them); a
   multi-statement string counts per statement; texts are normalised (`$1`, shapes read `$?`); shapes come
   in `read` order (by text), not first occurrence. So budgets are per engine (the reference doc says so).
5. **ClickHouse: `system.query_log`, `SYSTEM FLUSH LOGS`, a server-clock mark.** Descriptor: no
   `server_args`, `setup` or `begin`; `check = ["SYSTEM FLUSH LOGS", "SELECT count() FROM system.query_log
   WHERE 0"]` (creates the table on a fresh server, proves the privilege); `mark = "SELECT
   toUnixTimestamp64Micro(now64(6))"`; `flush = ["SYSTEM FLUSH LOGS"]`; `read = "SELECT query, 1 FROM
   system.query_log WHERE current_database = {db} AND is_initial_query = 1 AND type != 'QueryStart' AND
   toUnixTimestamp64Micro(query_start_time_microseconds) >= {mark} ORDER BY query_start_time_microseconds,
   event_time_microseconds, query SETTINGS final = 0"` (overrides the adapter's `final = 1`);
   `source = "system.query_log (initial queries finished or failed in the measured database)"`.
   **Why:** a query's finish/exception row is queued when it finishes, before the HTTP response is
   finalised; `SYSTEM FLUSH LOGS` writes every queued row synchronously, so one flush after the answer
   makes the request's rows visible. Earlier requests (seeding, earlier runs) started before the mark,
   so the window is exact on one server clock and needs no flush at its start. One row per query, failed
   ones included (as MySQL). **Rejected:** `TRUNCATE system.query_log` per window (clock-free but destroys
   a given server's log); `system.events` (global, includes the tool); `system.processes` sampling.
   `log_queries` is not set by the tool; predicted default 1 on 24.8 (P-ch).
6. **Exclusion of the tool's statements: by database** for `sql` counters. The measuring connection and
   the dbserver admin connection open `admin_db` (`postgres`, `default`); clones and drops happen outside
   every window; nothing of Lattice opens the measured database while a window is open; `read` must filter
   on `{db}` (validated). So reset, mark, flush and read never match. MySQL keeps its thread-id exclusion.
7. **The window.** `SqlCounter` (in `measure.py`): `start(owned)` opens `open_db(server.url)`, runs `setup`
   **only on a container the tool started**, then `check`; `watch(url)` keeps `database_name(url)`;
   `begin()` runs `begin` then `mark` (kept as `{mark}`); `end()` runs `flush` then `read` and returns each
   row's statement `int(calls)` times, in row order; `stop()` closes. `measure_size` calls `watch` for
   `SqliteCounter` and `SqlCounter` only. Per measured run: begin, request, body read, end.
8. **Liveness guard (`sql` only).** Per size, one window spans the seed phase (login included). If at least
   one seed request was sent and it holds 0 statements, the budget is `unmeasured` (its sizes dropped):
   `unmeasured: no statement of the app was seen in database {db} while seeding (N={n}, {k} seed
   requests); the app may not use the database it was given, or its statements are not logged` (an
   exception caught per budget in `measure()`). Sizes with 0 seed requests are not guarded; if no size of
   a budget was, the note `the counter's liveness was not checked (no seed request)` is added. Reason: a
   silent counter would pass every budget, the worst failure.
9. **Container lifecycle.** `server_for(e.name, h.database_url, name_prefix="lattice-perf-",
   extra_args=e.perf.server_args)`: one container per invocation, `lattice-perf-<engine>-<8 hex>`, labels
   `lattice.spec=throwaway` (+ `lattice.owner` from `$LATTICE_CONTAINER_OWNER`), tmpfs, 127.0.0.1 only,
   shared by every budget and size, a fresh clone per size. `extra_args` apply only to a started
   container; a given server (`--database-url`, `$LATTICE_<ENGINE>_URL`) gets no `setup` and is never
   reconfigured. Removal in `finally` (`counter.stop(); dbs.close(); server.stop()`) on every path
   (measured, check failure, liveness, MeasureError, Ctrl-C), plus dbserver's `atexit` guard.
10. **Failure modes; exit codes unchanged.** *Cannot run* (MeasureError or other exception, exit 2,
    stderr, as today): no Docker, `docker run` fails, a given URL does not answer (`wait_ready(30)`), the
    server dies mid-run, a non-2xx seed or measured request. *Unmeasured* (verdict with a reason note,
    exit 2, report printed): no `[perf]`; process app on SQLite (text unchanged); a `setup`/`check`
    statement fails (`unmeasured: statement counting on {title} is not available on this server:
    {stmt!r} failed: {first error line}`, server still removed); the liveness guard.
11. **Notes** (`sql`): `database: {server.describe()}; statements from {perf.source}`; on a given server
    also `counting on a given server: setup statements were not run`; then the existing runs note.
12. **Byte-identical to a4ab4a1:** `budget.py` and `__main__.py` (files); in `measure.py` the ASTs of
    `MysqlCounter`, `SqliteCounter`, `app_statements`, `shape`, `shapes_of`, `prepare`, `_in_process`,
    `harness_from_project`, `COUNTED_MYSQL`, `CONTAINER_PREFIX`; MySQL/SQLite notes and reports
    (P-regress); `lattice check`'s `docker run` argv.
13. **Process apps.** Postgres and ClickHouse count on the server, so **process apps are counted** (P-pg/P-ch
    run each app in-process and as a process). **Process app on SQLite stays out:** its statements run
    inside the app's own SQLite library. A preload shim (`DYLD_INSERT_LIBRARIES`/`LD_PRELOAD` on
    `sqlite3_prepare`/`step`) injects code into the app, is stripped by SIP and cannot see a static or
    pure-Go SQLite (`mattn` static cgo, `modernc.org/sqlite`); `sqlite3_trace_v2` or a VFS needs in-process
    code; `fs_usage`/dtrace see pages, not statements, and need root; an app-side log is self-report.

## 3. Probes (`dogfood/perf_engine_probes.py`; each output canonicalised and hashed per run)

**Setup.** Docker running. A scratch venv in the run's scratchpad (never `/tmp`, never the Lattice venv):
`uv venv --python /Users/vivekgade/Documents/lattice/.venv/bin/python $SCRATCH/perfvenv`; `uv pip install
--python $SCRATCH/perfvenv "psycopg[binary]>=3.2" "clickhouse-connect>=0.8" "pymysql>=1.1"`; a `.pth` adding
the Lattice venv's `site-packages`. **Old** = `git archive a4ab4a1 lattice` in `$SCRATCH/old`; **new** = the
worktree. **Canonical JSON** = the CLI's `--json` without `latency_ms`, `project`, `budget_file`, with
`lattice-perf-[a-z]+-[0-9a-f]{8}` -> `<container>`, `lattice_[0-9a-f]{8}_` -> `<run>_`. "10 identical" =
10 invocations, 1 distinct sha256. **Test app** `tests/perf/serverapp.py` (the `perfapp.py` pattern,
FastAPI): `build_db(url)` makes `users(id, name, token)` with ann/ben and `items(id, user_id, name)`;
`POST /login`, `POST /items`, `GET /items` (caller + items = 2), `GET /items_n1` (+ one owner lookup per
item = 2 + N), `GET /nodb` (none), `GET /health`. Postgres: psycopg, one autocommit connection per
request. ClickHouse: clickhouse-connect, **one client made at app start** (the driver's own queries on
client creation then precede every window), uuid ids. `make_process_app(url) -> ProcessApp` serves it
under uvicorn. Each scratch project's `studio.toml` names app, `build_db`, `login` and `database =
"postgres"`/`"clickhouse"`. Sizes `[3, 7]`, `--runs 10`, `--statements`, seed `POST /items` x N as ann.

**P-regress: MySQL and SQLite unchanged.** (a) decision 12's file/AST identity: **0 differences**.
(b) the 68 existing `tests/perf` tests source-identical (per-function AST) and passing. (c) SQLite
in-process, a `perfapp` project (as `test_measure._project`), `GET /items` 2+0N and `GET /items_n1` 2+1N,
`measure --json --runs 10 --sizes 2,6 --statements`, 10x old + 10x new: **1 distinct hash over 20**,
counts 2/2 and 4/8 in every run. (d) process app on SQLite: the same `unmeasured` note old and new, exit 2.
(e) MySQL on Streak: `git clone --no-hardlinks /Users/vivekgade/Documents/streak $SCRATCH/streak-perf`,
checkout a0679e9, `perf.budget.json` as in the spike-1a prereg (`prepare` `go build -o bin/streak-api
./cmd/server`; `home` `GET /v1/home` 6 + 1N, `habits` `GET /v1/habits` 4 + 0N; sizes 5, 25; seed
`POST /v1/habits`), `check --json --runs 10 --statements` 10x old, 10x new: **1 distinct hash over 20**,
exit 0 x20, counts 11 / 31 and 4 / 4 in 400/400 runs per side, fits 6 + 1N and 4 + 0N exact. (f) the
`start_container` argv (fake `_docker`) for postgres, mysql, clickhouse without `extra_args` equals the
literal a4ab4a1 argv: 3/3. **Pass:** (a)-(f).

**P-pg: Postgres counted exactly.** Predicted counts (N=3 / N=7), fit and verdict:

| id | endpoint | budget | counts | fit | verdict |
|---|---|---|---|---|---|
| items | `GET /items` | 2 + 0N | 2 / 2 | 2 + 0N exact | pass |
| n1-tight | `GET /items_n1` | 2 + 0N | 5 / 9 | 2 + 1N exact | **fail**, `per_item 1 > budget 0` |
| n1 | `GET /items_n1` | 2 + 1N | 5 / 9 | 2 + 1N exact | pass |
| nodb | `GET /nodb` | 0 + 0N | 0 / 0 | 0 + 0N exact | pass |

`check` exit **1**, `measure` exit **0**. Shapes of `n1`, N=3, first run, in order: `(1, "SELECT * FROM
items WHERE user_id = $? ORDER BY id")`, `(1, "SELECT id FROM users WHERE token = $?")`, `(3, "SELECT
name FROM users WHERE id = $?")` (psycopg's server-side prepare after 5 executions changes nothing).
In-process and process app, 10 invocations each. **Pass:** every count as tabled in 1600/1600 runs
(4 budgets x 2 sizes x 10 runs x 20), verdicts and exits 20/20, **1 distinct canonical hash over all
20** (the runtime leaves no trace), 0 `lattice-perf-*` containers after each invocation.

**P-ch: ClickHouse counted exactly.** The same budgets and predictions on
`clickhouse/clickhouse-server:24.8-alpine`, in-process and process, 10 invocations each. Shapes judged
by count per shape (`1, 1, 3`: caller, items, owner lookups) and by each starting with the app's text,
literals as `?`; recorded, not judged: no driver suffix (e.g. `FORMAT Native`), wall time, flush time per
window. **Pass:** as P-pg.

**P-excl: the tool's statements are never counted.** (a) `nodb` = 0 in 400/400 runs per engine (reset,
mark, flush, read run inside every window). (b) "Noisy tool": a probe subclass of `SqlCounter` sends
`SELECT 1` x5 on the measuring and x5 on the dbserver admin connection after `begin()`; `GET /items`
2 + 0N, 10 invocations per engine: **2 in 200/200 runs per engine**. (c) Not luck: in one window per
engine, `read` with `{db}` = the admin database returns >= 1 row (the tool's statements are logged and
filtered). **Pass:** (a)-(c).

**P-down: no server, or no counting.** (a) `PATH` without the docker CLI, postgres and clickhouse: exit
**2**, stderr `needs Docker`, 0 containers, 10 runs identical. (b) `LATTICE_POSTGRES_URL` at a closed
127.0.0.1 port: exit 2 within 40 s, stderr `did not answer within 30s`. (c) A given Postgres without
pgss (a plain `postgres:16-alpine` the probe starts as `lattice-probe-pg-*` and removes): `measure` and
`check` exit **2**, every report `unmeasured`, note with `not available on this server` and
`pg_stat_statements`; afterwards **0** `pg_extension` rows for it (no setup on a given server). (d) The
container `docker kill`ed by a hook after the first size: exit 2, `lattice perf: error:`, 0
`lattice-perf-*` left. (e) Liveness: a factory that ignores its URL and keeps data in a temporary SQLite
file (2xx answers), on both engines: every budget `unmeasured` with the decision-8 note, exit 2, 10 runs
identical. **Pass:** (a)-(e).

**P-trust: nothing runs but SQL in the named container.** (a) Exact decision-2 refusals for `counter =
"evil:Code"`, `[perf] docker_args`, `read` without `{db}`, `setup` with `{db}`, `general_log` with
`read`, `{mark}` without `mark`: **6/6**. (b) One P-pg and one P-ch invocation (in-process app) under
`sys.addaudithook` recording `subprocess.Popen`: only `docker`; verbs ⊆ {`run`, `port`, `inspect`,
`logs`, `rm`}, **no `exec`**; exactly 1 `run`, image = `[server] image`, arguments after the image =
`[server] args` + `[perf] server_args`, name `lattice-perf-<engine>-[0-9a-f]{8}`. (c) Every statement
`SqlCounter` sends (a wrapping connection records them) is a descriptor statement, Postgres **178** =
setup 1 + check 1 + 4 budgets x 2 sizes x 11 windows x (begin + read); ClickHouse **266** = check 2 +
88 windows x (mark + flush + read). **Pass:** (a)-(c). **Skip rule:** without Docker or the scratch venv,
Docker tests and probes skip **with the reason printed**; a skipped probe is NOT RUN, never a pass.

## 4. Mutation plan (PX1-PX11 in `dogfood/perf_mutants.py`)

`--python PY` now also runs pytest, so Docker tests run; a skipped killer does not count. Tests named
now, `tests/perf/test_counting.py` (fakes, no Docker): `test_perf_section_validation_messages`,
`test_builtin_descriptors_carry_perf`, `test_descriptor_without_perf_is_unmeasured_with_reason`,
`test_dispatch_by_descriptor_counter`, `test_sql_counter_sends_descriptor_statements_in_order`,
`test_sql_counter_expands_calls`, `test_sql_counter_on_given_server_skips_setup`,
`test_check_failure_is_unmeasured_and_server_stopped`, `test_liveness_guard_marks_budget_unmeasured`,
`test_liveness_not_checked_without_seed_requests`, `test_start_container_argv_unchanged_without_extra_args`,
`test_extra_args_follow_server_args`; `tests/perf/test_counting_docker.py`: `test_pg_counts_exactly`,
`test_pg_process_app_counts_the_same`, `test_ch_counts_exactly`, `test_no_perf_container_left`.

| id | mutant | predicted killers |
|---|---|---|
| PX1 | `begin` statements skipped (no pgss reset) | `..._statements_in_order`, `test_pg_counts_exactly` (seed counted) |
| PX2 | `flush` skipped in `end()` | `..._in_order`, `test_ch_counts_exactly` (0 rows: liveness unmeasured) |
| PX3 | `calls` ignored (one per row) | `test_sql_counter_expands_calls`, `test_pg_counts_exactly` (n1 3 + 0N) |
| PX4 | `{db}` bound to the admin database | `..._in_order`, `test_pg_counts_exactly`, `test_ch_counts_exactly` |
| PX5 | liveness guard removed | `test_liveness_guard_marks_budget_unmeasured` |
| PX6 | `setup` also run on a given server | `test_sql_counter_on_given_server_skips_setup` |
| PX7 | `extra_args` not passed to `server_for` | `test_extra_args_follow_server_args`, `test_pg_counts_exactly` |
| PX8 | server not stopped when `check` fails | `test_check_failure_is_unmeasured_and_server_stopped` |
| PX9 | validation accepts `read` without `{db}` | `test_perf_section_validation_messages` |
| PX10 | validation accepts any `counter` | `test_perf_section_validation_messages` |
| PX11 | dispatch keeps the a4ab4a1 rule (`mysql` only) | `test_dispatch_by_descriptor_counter`, both Docker count tests |

Prediction: control PASS; PM1-PM4 and PR1-PR9 still killed (their texts still occur once); PX1-PX11
killed: **24/24**. No equivalent mutant predicted (`read`'s shape order is asserted in `test_pg_counts_exactly`).

## 5. Named risks, and what this cannot show

1. **Engine semantics differ** (decision 4): pgss skips failed statements and splits multi-statement
   strings; ClickHouse counts failed queries; MySQL counts protocol commands. Budgets do not transfer.
2. **Statements sent after the HTTP answer** (deferred work, a pool's ROLLBACK after the handler returns)
   race the read on every engine, MySQL included; only the 10-identical rule detects it.
3. **The ClickHouse window rests on the server clock**: a backward step (Docker Desktop's VM resyncing
   after host sleep) between seeding and the mark could admit seed rows or drop request rows.
4. **The app controls its own logging**: as container superuser it could `SET pg_stat_statements.track =
   none` or send `log_queries = 0`. The liveness guard catches total silence only, not a partial one.
5. **Version drift**: pgss columns (`toplevel` from PG 14, `reset` grows in PG 17), the `query_log`
   schema, the `log_queries` default, `final` handling. Images are pinned; a given server of another
   version fails `check` (unmeasured, good) or counts differently without failing (not detectable here).
6. **Edits outside the owned files** may collide with parallel nodes (`lattice/modules` inherits `[perf]`).
7. **The builder reads these predictions**: counts follow from the test app, old columns from a4ab4a1.
8. **Synthetic apps only**: no real Go app on Postgres or ClickHouse (Streak is MySQL). **Not shown:**
   production load, concurrency, managed servers (restricted `reset`), rows scanned, cost, missing
   indexes, cross-version stability, Linux hosts.

## 6. Blind vs post hoc, and verification

Everything above is fixed before code. A missed prediction is a finding reported with its output, never
fixed silently; first-measured numbers stay; what is learned while building goes under "Post hoc".

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-perfengines PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-perfengines
uv run pytest tests/perf tests/spec/test_engines.py -q -rs      # fakes pass; Docker tests skip with reason
$SCRATCH/perfvenv/bin/python -m pytest tests/perf -q -rs        # with Docker: 0 skipped
$SCRATCH/perfvenv/bin/python dogfood/perf_engine_probes.py --runs 10 --streak $SCRATCH/streak-perf \
  --out docs/results/data/2026-10-15-perf-engines               # P-regress P-pg P-ch P-excl P-down P-trust
uv run python dogfood/perf_mutants.py --python $SCRATCH/perfvenv/bin/python   # control + 24/24 killed
uv run pytest tests/spec tests/modules tests/studio tests/mcp tests/test_cli.py tests/perf -q \
  --ignore=tests/spec/test_db.py --ignore=tests/spec/test_multidb.py -p no:cacheprovider
(cd dogfood/studio && ./check.sh)                               # PASS, 0 findings, cases = baseline
uv run ruff check lattice/perf lattice/spec/engines.py lattice/spec/dbserver.py tests/perf dogfood/perf_*.py
uv run ruff format --check lattice/perf tests/perf dogfood/perf_engine_probes.py
docker ps -a --filter name=lattice-perf- --format '{{.Names}}'  # empty
```

**Pass for the change:** all six probes as stated (none NOT RUN); 24/24 mutants killed; the broad suite at
its a4ab4a1 baseline (collected before code, stated in the results) plus the new tests, 0 failed; dogfood
check unchanged; ruff clean; no file changed outside decision 1's list.

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**A. File ownership.** Three files the decisions need but no node owned are APPROVED for minimal,
disclosed edits: `lattice/spec/engines.py` (accept the optional `[perf]` table and validate it; no other
behaviour), `lattice/spec/dbserver.py` (an `extra_args=()` keyword on `start_container`/`server_for`; with
the default every container command stays byte-identical, proven by P-regress (f)), and the one expected
error-message string in `tests/spec/test_engines.py` (gains `, perf`). `lattice/spec/engines.py` is also
edited by the marketplace node (descriptor-schema and registry changes); the two edits are disjoint and
the integration step resolves any textual conflict. `docs/lattice-reference.md` and
`docs/design/lattice-module-descriptor.md` are updated at the integration step, not here.

**B. Environment: Docker is not running and the venv has no database drivers (checked 2026-10-15).**
This build phase starts NO Docker Desktop and installs NO package. Consequences, fixed now: (1) every
probe that needs a container (P-regress's MySQL arm on a Streak clone, P-pg, P-ch, P-excl against a
server, P-down's container arm, the Docker-backed mutant killers) is implemented in the runner but is
reported **NOT RUN** (never PASS) in this phase, with its prediction recorded; the runner executes them
with `--only` once Docker and the scratch venv exist (steps in section 3); (2) the unit-level probes run
now with FAKE driver modules injected (descriptor parsing and validation, the `[perf]` table's rejection
of code/`docker run` options, query construction and database-filter enforcement, the liveness guard,
the two failure kinds, the SQLite in-process path and `tests/perf` unchanged); (3) PX mutants are
killed by unit tests where possible and the ones that need a server are listed as "pending a server".
The results document must say, per probe, RAN or NOT RUN.
