# P0 (spike 1a): query count per request on a real Streak endpoint (2026-10-12)

Node P0 of the L0 tranche (`docs/design/next-phases-plan.md`). Hand-run measurement on the built
Streak app, no Lattice or Streak code changed. Exit criterion (fixed before running): a written
baseline with variance that says whether query count is stable enough to be a budget metric,
yes or no, with the numbers.

## Question

Is the number of SQL statements one request makes stable enough (same input, repeated runs) to be
a performance budget metric, and does the number tell something real about the endpoint (is the
N+1 shape visible in it)? Latency is measured alongside to compare its noise with the count's.

## Method

### Target

- Streak `build/streak` at `a0679e9` (`merge main: spec fix for the anonymous logout scenario`),
  as a detached worktree `/Users/vivekgade/Documents/streak/.lattice/p0-perf`
  (`git -C /Users/vivekgade/Documents/streak worktree add --detach .lattice/p0-perf build/streak`).
  Streak's main working copy was not modified, checked out or committed in.
- Built with `go build -o bin/streak-api ./cmd/server` (go1.26.2 darwin/arm64).
- MySQL: throwaway container `lattice-p0-mysql`, image `mysql:8.4` (server 8.4.11), default
  strict `sql_mode`, published on `127.0.0.1:55000`. Two accounts: `root` (my measurement and
  seeding connection) and `app` (the app's only account), so the app's statements are told apart
  from mine by `user_host`.
- Schema: `streak-api migrate` (with `HABIT_TEST_CLOCK=1`, which also creates `test_clock`), then
  one row `test_clock(1, 2026-03-04 12:00:00)` as the harness seed has it.
- App env (the harness's test-mode env, from `harness.py`): `HABIT_TEST_CLOCK=1`, `HABIT_PUSH=stub`,
  `HABIT_MIN_CLIENT_VERSION=2.0.0`, `HABIT_SECRET_KEY=streak-test-secret-key`, `PORT=18765`,
  `HABIT_DB_DSN='app:apppw@tcp(127.0.0.1:55000)/streak?parseTime=true'`. One process, started as
  `./bin/streak-api`.

### Endpoint and why

`GET /v1/habits`, `GET /v1/home` and `GET /v1/habits/{id}/history` are the candidates. I chose
**`GET /v1/home`** because:

- It is the only list-style endpoint whose per-item output is *derived* (each habit carries its
  `progress`, which REQUIREMENTS M17 defines as equal to `GET /v1/habits/{id}/progress`), so it is
  the one where a per-item query is plausible. `GET /v1/habits` returns stored columns only, and
  `history` is one habit, so its cost scales with that habit's periods, not with N habits.
- It is unpaginated and the app's main read (M17), so a budget on it is realistic.
- Honesty note: I read `internal/home/routes.go` before measuring, so I expected `progress.Of`
  to run once per habit. The measurement below is what establishes it, not the reading.

### Seed

One user `p0@example.com` (timezone `America/New_York`, so the seed instant 2026-03-04T12:00Z is
Wednesday 07:00 local; week Mon 2026-03-02 to Sun 2026-03-08), registered and logged in through
the API (`POST /v1/auth/register`, `POST /v1/auth/login`). Habits are weekly, `target_count` 3,
`start_date` 2026-03-02, names `habit-001`..`habit-100`.

| Stage | Habits N | How seeded | Check-ins | Runs |
|---|---|---|---|---|
| A | 5 | `POST /v1/habits` | 0 | 10 |
| B | 25 | +20 by `POST /v1/habits` | 0 | 10 |
| C | 50 | +25 by `POST /v1/habits` | 0 | 10 |
| D | 100 | +50 by SQL (`INSERT ... SELECT` copying the 50 API habits, name `<name> copy`, plus their `habit_targets` rows) | 0 | 10 |
| E | 100 | same | 300 = 3 per habit (2026-03-02, 03, 04): 150 by `POST /v1/habits/{id}/completions` for the 50 API habits, 150 by SQL for the 50 copies | 10 |

N=50 is the legal maximum of active habits per user (REQUIREMENTS: "at most 50 active ... 409
habit_limit"). **N=100 is outside the contract**: the 50 extra habits were inserted by SQL, which
the read path does not check. It is kept because the task named N=100 and because it shows the
scaling; it is flagged wherever it is used. The SQL-inserted rows leave `users.change_seq`
unchanged, which does not matter for `/v1/home`.

Two things the seeding had to respect: the per-user rate limit is 120 requests per clock minute
(`RateLimit`, counted on the *test clock*, which never advances by itself), so the clock was moved
one minute (`PUT /test/clock`) before each stage and every 100 member requests; and the access
token (issued at the process's start instant, 15 minutes TTL) bounds how far the clock may move.
The clock ended at 12:07, always on 2026-03-04, so every request saw the same local date.

### Counting statements

- `SET GLOBAL log_output='TABLE'; SET GLOBAL general_log=1;` on the container.
- Before **each** measured request: `TRUNCATE TABLE mysql.general_log` (root), then confirmed
  zero rows from the app account (`pre_rows` = 0 in all 50 runs).
- One request: `GET /v1/home` with `Authorization: Bearer <token>` over one persistent HTTP
  connection (Python `http.client`), wall time around `request` + `getresponse` + `read`
  (`time.perf_counter`).
- After the response: read `mysql.general_log` rows with `user_host LIKE 'app[%'`
  (the app's account only; my root statements are excluded by this, and the log was truncated
  anyway). **Counted: `command_type` in (`Query`, `Execute`).** The app's driver uses prepared
  statements, so every statement is one `Prepare`, one `Execute`, one `Close stmt` (shown as
  `by_type` in the raw data: for every run the three counts are equal); counting `Execute` counts
  each statement once and `Prepare`/`Close stmt` are not counted. `Query` was 0 in all runs.
- Each run's statements were also hashed after replacing string literals with `?`, to check that
  not only the count but the *sequence of statement shapes* is identical across runs.
- 10 runs per stage, consecutive, no discarded warm-up (the connection was already warm from the
  seeding calls). Runs were sequential, nothing else hit the app.

Commands, in order (the driver was a throwaway Python script in the scratchpad, not committed;
`$ENV` is the env above):

```
git -C /Users/vivekgade/Documents/streak worktree add --detach .lattice/p0-perf build/streak
(cd .lattice/p0-perf && go build -o bin/streak-api ./cmd/server)
docker run -d --name lattice-p0-mysql -e MYSQL_ROOT_PASSWORD=p0root -e MYSQL_DATABASE=streak \
    -p 127.0.0.1:0:3306 mysql:8.4
mysql> CREATE USER 'app'@'%' IDENTIFIED BY 'apppw'; GRANT ALL ON streak.* TO 'app'@'%';
$ENV ./bin/streak-api migrate
mysql> INSERT INTO test_clock(id,now) VALUES (1,'2026-03-04 12:00:00');
mysql> SET GLOBAL log_output='TABLE'; SET GLOBAL general_log=1;
$ENV nohup ./bin/streak-api &
python p0.py   # seed + stages A..E; per run: TRUNCATE general_log, GET /v1/home, SELECT general_log
```

## Data

Every request answered 200 with `habits` of length N (response 2,880 bytes at N=5, 14,220 at
N=25, 28,395 at N=50, 56,995 at N=100, 69,644 at N=100 with check-ins).

Statements are `Query`+`Execute` of the app account, one request each. Latency is client-side
wall time in ms.

### Stage A: N=5, no check-ins

| Run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| statements | 11 | 11 | 11 | 11 | 11 | 11 | 11 | 11 | 11 | 11 |
| latency ms | 12.77 | 8.20 | 7.63 | 6.88 | 7.43 | 8.29 | 7.40 | 6.35 | 6.78 | 7.09 |

min 6.35 / median 7.41 / max 12.77 ms; mean 7.9, sd 1.8 (CV 23%), max/min 2.0.

### Stage B: N=25, no check-ins

| Run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| statements | 31 | 31 | 31 | 31 | 31 | 31 | 31 | 31 | 31 | 31 |
| latency ms | 15.97 | 16.04 | 16.38 | 17.60 | 14.29 | 13.89 | 15.26 | 13.98 | 14.22 | 14.63 |

min 13.89 / median 14.94 / max 17.60 ms; mean 15.2, sd 1.2 (CV 8%), max/min 1.27.

### Stage C: N=50 (legal maximum), no check-ins

| Run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| statements | 56 | 56 | 56 | 56 | 56 | 56 | 56 | 56 | 56 | 56 |
| latency ms | 27.05 | 25.41 | 26.24 | 28.03 | 28.05 | 32.06 | 35.52 | 30.46 | 28.54 | 29.90 |

min 25.41 / median 28.29 / max 35.52 ms; mean 29.1, sd 3.0 (CV 10%), max/min 1.40.

### Stage D: N=100 (outside the 50-habit contract, SQL-seeded), no check-ins

| Run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| statements | 106 | 106 | 106 | 106 | 106 | 106 | 106 | 106 | 106 | 106 |
| latency ms | 50.65 | 54.27 | 53.75 | 50.34 | 50.34 | 56.28 | 57.61 | 51.46 | 50.75 | 56.14 |

min 50.34 / median 52.61 / max 57.61 ms; mean 53.2, sd 2.8 (CV 5%), max/min 1.14.

### Stage E: N=100, 300 check-ins (3 per habit)

| Run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| statements | 106 | 106 | 106 | 106 | 106 | 106 | 106 | 106 | 106 | 106 |
| latency ms | 52.24 | 58.18 | 44.76 | 52.32 | 55.05 | 52.74 | 55.12 | 57.41 | 55.59 | 57.28 |

min 44.76 / median 55.08 / max 58.18 ms; mean 54.1, sd 3.9 (CV 7%), max/min 1.30.

### Summary

| N | check-ins | statements (10 runs) | latency min / median / max (ms) | latency CV | max/min |
|---|---|---|---|---|---|
| 5 | 0 | 11 x10 | 6.35 / 7.41 / 12.77 | 23% | 2.01 |
| 25 | 0 | 31 x10 | 13.89 / 14.94 / 17.60 | 8% | 1.27 |
| 50 | 0 | 56 x10 | 25.41 / 28.29 / 35.52 | 10% | 1.40 |
| 100 (outside contract) | 0 | 106 x10 | 50.34 / 52.61 / 57.61 | 5% | 1.14 |
| 100 (outside contract) | 300 | 106 x10 | 44.76 / 55.08 / 58.18 | 7% | 1.30 |

### Where the statements come from

Identical in every run of every stage (one distinct statement-shape sequence per stage, and one
distinct exact sequence, since the habit ids are fixed):

| Count | Statement shape |
|---|---|
| 3 | `SELECT * FROM test_clock WHERE id = 1 LIMIT 1` |
| 1 | `SELECT * FROM users WHERE id = 1 LIMIT 1` |
| 1 | `SELECT * FROM habits WHERE user_id = 1 AND deleted_at IS NULL AND archived = 0 ORDER BY id` |
| **N** | `SELECT date FROM completions WHERE habit_id = ? AND deleted_at IS NULL AND date BETWEEN ? AND ?` (one per habit, in habit order) |
| 1 | `SELECT nudges.* ... FROM nudges JOIN habits ON habits.id = nudges.habit_id WHERE ...` |

Order in a run: `test_clock`, `users`, `test_clock`, `test_clock`, `habits`, then the N
`completions` selects, then the nudges join. So statements = **N + 6**: 5, 25, 50, 100 habits give
11, 31, 56, 106. All 10 runs of all 5 stages match this exactly (50 of 50).

Raw per-run data (status, latency, bytes, statement count, command-type breakdown, sequence hash)
and the full statement text of one run: `docs/results/data/next-phases/p0-query-count-raw.json`.

An extra check after the main stages: the app was restarted (kill, start), logged in again, and
`GET /v1/home` called 4 times at N=100 with the log truncated each time: 106 `Query`+`Execute`
statements every time, one connection thread, 318 log rows (106 Prepare + 106 Execute + 106 Close
stmt), latency 62.6, 54.5, 50.9, 55.1 ms. So a fresh process gave the same count.

## Findings

1. **The count is exactly stable.** 50 runs over 5 stages: zero variation within a stage, and the
   statement shapes and their order are identical in every run. There is no noise to filter in
   this setup (sequential requests, one warm connection, fixed clock).
2. **It is a clear N+1.** The count is exactly N + 6, linear in the number of habits, because
   `progress.Of` issues one `SELECT date FROM completions` per habit inside the loop in
   `internal/home/routes.go`. The check-ins do not change the count (stage D vs E: 106 both), so
   it is the habit count, not the data volume, that drives it. The 6 fixed statements are 3
   `test_clock` reads (test mode only; see below), the user lookup, the habit list, and the nudges
   join.
3. **Latency is noisier and tells the same story more slowly.** Median latency grows
   approximately linearly with N (7.4, 14.9, 28.3, 52.6 ms: about 0.5 to 0.6 ms per habit past
   N=25, so about 0.5 ms per extra statement here, on localhost against a container). The per-run
   spread is large at small N: at N=5 one run was 12.8 ms against a 7.4 ms median (max/min 2.0,
   CV 23%); at N>=25 max/min is 1.1 to 1.4 (CV 5 to 10%). A single-run latency budget with a
   +/-30% margin would false-alarm at N=5 and sit at the edge at N=50. A median of 10 runs is
   usable, but the numbers are machine- and container-bound.
4. **Latency does not separate the cases by itself where count does.** From the latency data
   alone, "28 ms at N=50" cannot be told from "a slow single query"; the count (56 = 50 + 6)
   identifies the per-item pattern. Conversely the count says nothing about a single slow
   statement (a missing index on a big table): it was not tested here (tables were tiny).

## Verdict: query count as a budget metric

**Yes**, with the following numbers behind it:

- Stable: 10/10 identical in each of 5 stages (50/50 overall), and identical across a process
  restart (4/4 more). Variance is 0 in this setup, against latency CV of 5 to 23% and max/min up
  to 2.0.
- Informative: it equals N + 6 exactly, so it exposes the N+1 shape directly. A budget must
  therefore be stated **against a seed size**, or as "constant + per item". A single small seed
  would let this exact N+1 through a loose budget: a budget of 20 statements passes at N=5 (11)
  and fails at N=25 (31). A budget needs at least two seed sizes (here N=5 and N=50: 11 and 56)
  or the form "constant in N".
- Cheap to read: the log read is a deterministic count of one account's `Execute` rows; no
  statistics needed.

Latency as a *second* metric: usable only as a median of repeated runs with a wide margin on a
scaled seed; this baseline does not support single-run latency thresholds.

This verdict is about *stability and informativeness of the count in this setup*. It does not
say that a count budget is sufficient (see below).

## What could not be determined

- **Production count.** In test mode (`HABIT_TEST_CLOCK=1`, the harness's mode) every `Now`
  reads `test_clock`: 3 of the 6 constant statements. With the real clock the constant is likely
  3, giving N + 3. Not measured. The budget metric's constant therefore depends on the mode and
  must be calibrated per mode.
- **Which code paths issue the 3 clock reads and the user lookup.** From the statement order
  (clock, user, clock, clock) and the code read (rate limiter calls `Now`, the handler calls
  `Today`) I attribute them to the auth middleware, the rate limiter and the handler, but I did
  not trace the middleware source, so the attribution of the first read is an inference.
- **Other endpoints.** `GET /v1/habits`, `GET /v1/habits/{id}/history` and the sync, nudges and
  progress routes were not measured; nothing here says whether they are N+1 free. Not asserted.
- **Concurrency.** All requests were sequential on one connection; behaviour under concurrent
  requests (retries on deadlock, more connections, connection setup statements) was not
  measured. Connection-init statements (`time_zone`, `sql_mode` set by the driver at handshake)
  did not appear in `general_log` as `Query` rows in any run, so the count excludes them; a
  restarted process that opens a connection by the first request was not isolated (the login
  call opened the connection first).
- **Large tables.** Rows scanned and statement *cost* were not measured; tables held at most
  100 habits and 300 check-ins. A count budget would not catch a missing index or an unbounded
  read of one statement.
- **N=100 is outside the contract** (50 active habits per user) and was SQL-seeded. The
  conclusion at N <= 50 does not depend on it, but the "N=100" numbers are about an input the
  API cannot produce.
- **Variance across machines and across days.** One machine, one afternoon, localhost Docker
  MySQL, 10 runs per stage. The count's exactness is deterministic by construction of the code;
  the latency numbers are not portable.
- **Whether the clock minute or the date matters.** All requests were at 12:00 to 12:07 on one
  date; a request near a period or date boundary may issue different statements (not tried).

## Cleanup evidence

Run at the end (all after the last measurement):

- App stopped: `pkill -f bin/streak-api`; `pgrep -fl streak-api` printed nothing (rc 1).
- General log switched off (`general_log=0`, `log_output='FILE'`), then `docker rm -f
  lattice-p0-mysql`; `docker ps -a --format '{{.Names}}' | grep -c '^lattice-p0-'` printed `0`.
- `git -C /Users/vivekgade/Documents/streak worktree remove --force
  /Users/vivekgade/Documents/streak/.lattice/p0-perf`, then `worktree prune`; `worktree list`
  shows only `/Users/vivekgade/Documents/streak a0db033 [main]` and the owner's pre-existing
  `.lattice/rebuilds/streak-b4-a93dcb 9417351 [rebuild/p2-account]`; the directory no longer
  exists.
- `git -C /Users/vivekgade/Documents/streak status --short` shows only ` M studio.toml` (the
  owner's pre-existing change). Streak `build/streak` is still `a0679e9`; Streak's HEAD is on
  `main`.
- Lattice repo: no tracked file changed; the only new paths are this document and
  `docs/results/data/next-phases/p0-query-count-raw.json`.
- Environment note: Docker Desktop was **not running** at the start (no daemon socket), contrary
  to the brief. I started it with `open -a Docker`. Its own restart policies then brought the
  owner's `cyclops-scheduler`, `cyclops-ui` and `cyclops-stream` containers up (visible in
  `docker ps`); I did not touch them and left Docker Desktop running.
