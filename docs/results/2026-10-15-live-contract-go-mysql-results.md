# Live contract check on a Go + MySQL provider (Streak): results

Date: 2026-10-15. Branch `np-livego`. Pre-registration with Amendment 1:
`2026-10-15-live-contract-go-mysql-prereg.md` (not edited). Data:
`data/2026-10-15-live-go-mysql/` (`probes/` the final run, `mutants/`, `regression/`, `baseline/`).
No LLM, no API key, 0 tokens. Provider: Streak `build/streak` a0679e9 cloned into scratch (the
real checkout was only read); interpreter of the probes: the driver venv (PyMySQL 1.2.3).

## 1. What was built (files owned, section 7 of the prereg)

| file | change |
|---|---|
| `lattice/contracts/live.py` | `LIVE_DATABASES`, the MySQL path (lazy one-server-per-run, a clone per replay, prechecks, env stripping, `database` block), SIGTERM grace, markers and the parent's sweep |
| `lattice/security/target.py` | only the keyword-only `databases=None` of `start_target_harness` (via `contextlib.nullcontext`; `_open_db` untouched) |
| `tests/contracts/test_live_server.py` | new, 24 tests (23 with fakes/real child, 1 on a real throwaway MySQL) |
| `dogfood/contracts/livego_probes.py` | new, the probe runner (P0, G0, 21 drift cells, A1-A5, A1', K1-K3, Z1, B1, T, `--mutants`) |
| `dogfood/contracts_mutants.py` | LG1-LG13 appended |
| `dogfood/contracts/streak_probes.py` | W3 expected code `unsupported_database` -> `driver_missing` (post hoc, disclosed in its notes) |

## 2. The 22 configurations (G0 + 7 drifts x modes a, b, c): predicted vs observed

All 22 match the pre-registered table in every judged field (S, L, N, class, exit, containers,
access_token verified, static CHANGED sets equal the committed 2026-10-13 `Dn.json` sets, mode b
static = G0's with `version_changed` true / `spec_hash_changed` false) and are identical across
runs (G0 and mode a 10/10, modes b and c 3/3). **22/22.** Raw data: `probes/G0.json`,
`probes/G-D<n><m>.json`. S = static BROKEN, L = live BROKEN, N = endpoints not replayed.

| cell | S | L | N | class | exit | containers | answered (login / other) | median wall s |
|---|---|---|---|---|---|---|---|---|
| G0 | - | - | - | EXACT | 0 | 1 | 200 / 6x200, completions 201 | 5.85 |
| D1 a | endpoint_missing GET /v1/home | - | home | UNREPLAYED | 1 | 1 | 200 | 6.61 |
| D1 b | - | status_not_observed 200 @ home (404) | - | LIVE-ONLY | 1 | 1 | 200 | 5.70 |
| D1 c | endpoint_missing home | - | home | UNREPLAYED | 1 | 1 | 200 | 6.30 |
| D2 a | status_not_observed 200 @ dismiss | same + response_field_missing id (204) | - | SUPERSET | 1 | 1 | 200 | 6.42 |
| D2 b | - | same as a | - | LIVE-ONLY | 1 | 1 | 200 | 6.59 |
| D2 c | status_not_observed 200 @ dismiss | - (app answers 200) | - | DISAGREE | 1 | 1 | 200 | 5.63 |
| D3 a | response_field_missing expires_in @ login | same | - | EXACT | 1 | 1 | 200 | 6.40 |
| D3 b | - | same | - | LIVE-ONLY | 1 | 1 | 200 | 6.41 |
| D3 c | same as a | - | - | DISAGREE | 1 | 1 | 200 | 6.41 |
| D4 a | request_field_missing password @ login | - (request fields not checked) | - | DISAGREE | 1 | 1 | 200 | 6.43 |
| D4 b | - | status_not_observed 200 @ login (422) | 6 token endpoints (harness login 422) | LIVE-ONLY | 1 | 1 | 422 (1 replay) | 6.34 |
| D4 c | request_field_missing password | status_not_observed 200 @ login (422) | - | DISAGREE | 1 | 1 | 422 | 6.44 |
| D6 a | response_field_missing next_cursor @ habits | same | - | EXACT | 1 | 1 | 200 | 6.38 |
| D6 b | - | same | - | LIVE-ONLY | 1 | 1 | 200 | 6.60 |
| D6 c | same as a | - | - | DISAGREE | 1 | 1 | 200 | 6.40 |
| D7 a | endpoint_missing x7 | - | 7 ("not in the provider's current spec") | UNREPLAYED | 1 | **0** | - | 0.37 |
| D7 b | - | status_not_observed 200 @ login (404) | 6 | LIVE-ONLY | 1 | 1 | 404 (1 replay) | 6.44 |
| D7 c | endpoint_missing x7 | - | 7 | UNREPLAYED | 1 | **0** | - | 0.41 |
| D9 a / b / c | - | - | - | EXACT | 0 | 1 | 200 | 6.42 / 6.40 / 6.39 |

**The registered question (Amendment 1).** "Static and live agree on the D-series" is **false in
the strict sense, as predicted**: mode a EXACT 3/7 (D3, D6, D9; 4/8 with G0), lenient (EXACT,
SUPERSET or UNREPLAYED) 6/7 (D4 is the one DISAGREE: request fields are not checked live);
mode c strict 1/7 (D9), lenient 3/7; mode b: live alone sees 6/7 (LIVE-ONLY on D1, D2, D3, D4, D6,
D7; D9 is the shared miss, both blind to a type). Removals and lost access are silence
(UNREPLAYED), a real 204 drops the body the spec still shows (D2), and an unchanged app disagrees
with a spec-only edit (mode c: DISAGREE on D2, D3, D4, D6).

## 3. Probes (RAN / NOT RUN)

37 of 37 RAN, 37 pass (`probes/summary.json`, `probes/run-table.txt`). Nothing NOT RUN.

| probe | what | observed | pass |
|---|---|---|---|
| P0 | clone = a0679e9; 5 files equal main a0db033; pin = golden export, spec_hash d44b738522ea; go build; image local | all true; build 6.2 s cold | PASS |
| G0 + 21 cells | section 2 | 22/22 | PASS |
| A1 | docker shim exit 1: `docker_unavailable`, log = `info` only | exit 2, 0.4 s, log `info --format ...` x1 | PASS |
| A1' | real CLI, `DOCKER_HOST=unix://none.sock` | `docker_unavailable`, 0.44 s | PASS |
| A2 | Lattice venv via `uv run`: `driver_missing`, 0 Docker calls | exit 2, names `pymysql>=1.1` and `lattice[db]`, shim log empty, 0.4 s | PASS |
| A3 | `info` 0, `image inspect` 1 | `docker_image_missing`, message has `docker pull mysql:8.4`, log = info, image; no run/pull | PASS |
| A4 | no `bin/` | `provider_start_failed`, message names `.../bin/streak-api`, 1 container created and removed, 5.9 s (<= 9) | PASS |
| A5 | `--static-only` under A1's shim | exit 0, `not_run`/`static-only`, shim log empty, 0.21 s | PASS |
| K1 | after every live call (166 calls): 0 `lattice-live-` containers, 0 streak-api, 0 TMPDIR entries | 0 leaks (K3b's known TMPDIR excepted) | PASS |
| K2 | TIMEOUT_S 2, 5, 8 (x3) in-process | 2 s and 5 s `live_timeout` (returned at 2.5 s, 5.4 s); 8 s: `ok` in 5.4-6.8 s (recorded either way); 0 leaks 5 s after | PASS |
| K3a | SIGKILL child at 3 s and 6 s (x3) | 6/6 `live_failed`, 0 containers, 0 streak-api (see 5.3: the 6 s kill landed this run, not in the dry run) | PASS |
| K3b | SIGKILL parent at 4 s (x3) | within 3.1-3.2 s 0 containers and 0 processes; 1 TMPDIR left per call (predicted known leak, reported) | PASS |
| Z1 | decoy mysql named by `LATTICE_MYSQL_URL`, `HABIT_DB_DSN`, `DATABASE_URL`, `[app] database_url` (x3) | result = G0's, `ignored` = both; decoy Connections 10 -> 11 (the runner's own read); SHOW DATABASES, sentinel checksum and GLOBAL sql_mode unchanged; no `lattice_*` database on the decoy | PASS |
| B1 | job-scheduler L1-L4, W3, L5 x4: CLI `--json` and text, new vs `git archive 30c9120 lattice` | 18/18 case-format cells byte-identical 10/10; docker shim: 0 calls; `git diff 30c9120 -- tests/contracts/test_live.py` empty | PASS |
| T | cost | section 4 | PASS |
| K3c | **post hoc supplement** (not registered): SIGKILL the child when its first app pid is recorded (x3) | `live_failed`, 0 containers, 0 streak-api | PASS |

## 4. Cost (reported; thresholds 30 s median, 60 s max)

| | seconds |
|---|---|
| G0 live CLI wall, median of 10 | **5.85** (max 6.89; predicted 8) |
| mode a, 10 runs, medians | 6.4 - 6.6 (D7: 0.37, no container) |
| Streak `--static-only` (5 runs) | 0.23 (max 0.24) |
| refusals A1-A3, A5 | 0.2 - 0.47 |
| A4 (container started, then removed) | 5.9 |
| SQLite live check (job-scheduler, from the 2026-10-13 runs) | 0.72 |

Per live link about 5.1 s is the container (start and `docker rm -f -v`), the rest migrate+seed,
7 clones+replays and two spec loads. Whole final runner: 1243 s wall (predicted 25-40 min).
Docker events: 136 containers created by 169 live calls (every one `lattice-live-mysql-*`, label
`lattice.owner=live-*`; the rest are refusals and the two 0-container cells).

## 5. Mutants LG1-LG13: 13/13 killed

Unit stage (`dogfood/contracts_mutants.py LG1..LG13`, control PASS): every mutant killed by
`tests/contracts/test_live_server.py` (first failing test below). Probe stage
(`livego_probes.py --mutants`, `--runs 1`): the named probe, check that failed.

| id | mutant | unit test that kills it | probe that kills it (failing check) |
|---|---|---|---|
| LG1 | sqlite only | `test_postgres_stays_unsupported_...` | G0 (replayed_7, database_block, exit_0, ...) |
| LG2 | no shared server | `test_one_server_for_the_run_and_a_clone_per_replay` | G0 `one_container_per_call` |
| LG3 | one clone reused | same test | G0 `replayed_7`: 2 replayed, 5 N "given rows refused (Duplicate entry ... habits.PRIMARY)" (**after a mutant-definition fix, section 6**) |
| LG4 | `LATTICE_MYSQL_URL` kept | `test_the_users_servers_are_dropped_...` | Z1 (connections_plus_1, ...) |
| LG5 | `database_url` honoured | same | Z1 |
| LG6 | driver precheck removed | `test_the_prechecks_run_in_order_...` | A2 (error_code, zero_docker_calls) |
| LG7 | Docker precheck removed | same | A1 (error_code, shim_log_is_info_only) |
| LG8 | image precheck removed | same | A3 (error_code, no_run_no_pull) |
| LG9 | label sweep removed | `test_the_sweep_kills_recorded_apps_...` | K3a `no_container_left` |
| LG10 | pid sweep removed | same | K3c `no_streak_api_left` (K3a's kills landed before an app existed) |
| LG11 | server opened eagerly | `test_a_run_that_replays_nothing_starts_no_server` | G-D7a and G-D7c `containers_as_predicted` |
| LG12 | `database` block on SQLite | `test_a_sqlite_result_has_no_database_key_...` | B1 `all_byte_identical` |
| LG13 | SIGTERM grace removed | `test_a_timeout_gives_the_child_sigterm_...` | (unit test only, as predicted) |

## 6. Findings, post hoc corrections and decisions the prereg did not fix

Predictions that did not hold, or are inconsistent in the document:
1. **verified_live sentence.** The prereg says "wherever login answered 200: every a and c row but
   D7, ...", but its own table has c-D4 login answering 422. By the rule (login 2xx) c-D4 is not
   verified; the cell was evaluated by the rule and observed False. A document inconsistency.
2. **K3a timing.** Registered offsets 3 s and 6 s assumed an 8 s check; it takes about 6.1-6.9 s.
   In the dry run two of three 6 s kills found no child (check already finished). In the final
   run all six landed. Because an app only exists late, K3a at 3 s does not exercise LG10; the
   post hoc probe K3c (event-triggered) does.
3. **pid sweep timing (prereg inconsistency).** L5 says "right after its /health answered" but
   also gives a ~15 ms window (spawn to write). Implemented at spawn (`ProcessApp._spawn` wrapped
   in the child only), which gives the ~15 ms window and covers a kill during the health wait.
4. **Runner exit code** of the final run was 1 although 37/37 pass: `leftover_processes` listed a
   `lattice.studio` process of another node (`studioui` scratch), not mine.

Post hoc script corrections (all found in a `--runs 1` dry run, before the final run; also in
`probes/summary.json` `post_hoc`):
- `classify()`: LIVE-ONLY (S empty, L non-empty) was reported SUPERSET because the SUPERSET test
  ran first and an empty set is a proper subset. Order swapped; SUPERSET needs non-empty S.
  Prediction table untouched; six cells were affected (all b rows).
- A2/K1 TMPDIR check: `uv run` leaves `uv-<hash>.lock` in the call's TMPDIR; now listed under
  `uv_lock_entries`, excluded from `tmp_entries`.
- LG3 mutant definition (old: a fresh `first` list per `databases()` call, so a replay still got
  its own clone and the probe SURVIVED; new: the list is kept on the `_ServerRun` instance). Not
  a code defect; LG3 is then killed by G0 and the unit test (it was also killed by the unit test
  before). Both runs are in `mutants/`.
- `streak_probes.py` W3: expected code `unsupported_database` -> `driver_missing`; W3 passes
  (32 probes: 31 pass, S1 fails for the reason in section 7).

Decisions the prereg did not fix:
- Environment prechecks (driver, Docker, image) run lazily with the server, at the first replay
  that is sent: a run that replays nothing needs no Docker (a-D7, c-D7: 0 calls).
- `server_for(..., None)` is called after stripping every `LATTICE_<DIALECT>_URL`; additionally a
  server that is not "owned" (not started by the check) is refused (`database_start_failed`).
- The parent uses its own owner token for the label sweep and trusts a marker only when it names
  that token (`live-<16 hex>`); the pid sweep kills a recorded pid's group only when the pid is its
  own process-group leader (pid-reuse guard).
- `database` block: `ignored` is computed for any server dialect even when no replay is sent.
- MySQL live needs a `studio.toml` with `database = "mysql"`: the manifest's `[[project]]` keys
  have no database key (the fallback harness stays `sqlite`).
- `unsupported_database` message changed (lists the supported set), as L1 asks.
- The SIGTERM handler and the pid hook are installed for every provider (also SQLite process
  apps), invisible in outputs (B1 byte-identical, 0 Docker calls).
- Driver detection: `importlib.metadata` version vs the engine's `requires` (`>=` and `==`).

## 7. Regression

| run | baseline (before the first edit) | after |
|---|---|---|
| `uv run pytest tests/contracts tests/security tests/workspace -q -p no:cacheprovider` | 420 passed (2m15s) | **443 passed, 1 skipped** (the Docker test, no PyMySQL in that venv) (2m20s) |
| same + `tests/perf`, driver venv (Docker test runs) | - | **556 passed** (2m23s) |
| `probes.py --runs 1` | 35/35 | 35/35 |
| `streak_probes.py --allow-code-diff --runs 1` | 32/32 | 31/32: only S1 (see below); W3 passes |
| `impact`, `exclude`, `linkedq`, `transitive` probes `--runs 1` | not run at baseline | see below |
| ruff check (all changed .py), ruff format --check (created .py) | - | clean |

The failures of the later runs all trace to one environmental fact: **an untracked
`perf.budget.json` appeared in the real Streak checkout at 16:39:48** (during my final run;
`git status`: ` M studio.toml`, `?? perf.budget.json`; my baseline at 10:01 saw only
` M studio.toml`). It was not written by this change (no code path of mine writes into a project;
the file is a `lattice perf` budget for GET /v1/habits, another node's). Probes that assert the
exact Streak status fail on it: `streak_probes` S1, `exclude_probes` S and X0 (X0 cascades from
streak 31/32), `impact_probes` I6 and I8 (cascade), `linkedq_probes` S and Q7 (cascade),
`transitive_probes` T-safety. `transitive_probes` T-flagoff (0/19 byte-identical to a4ab4a1,
"regression suites not run yet") fails identically on a `git archive 30c9120` tree: pre-existing.
I did not touch the Streak checkout.

## 8. Cannot show / residual risks

As section 5 of the prereg (production data, consumer's real requests, request fields, nested
keys, types, headers, Await/nudger, Postgres/ClickHouse, Linux Docker). Known windows: a kill
between app spawn and its pid write; a parent killed with SIGKILL leaves its TMPDIR (K3b observed
1 per call; container and app are still cleaned by the child). Builder bias: the builder read
these predictions; mode a code edits are the author's. The LG mutants are single-line patches by
the same author.

## Orchestrator verification (independent re-run)
`tests/contracts tests/security tests/workspace` 443 passed, 1 skipped (the real-MySQL test, no PyMySQL in
the Lattice venv); `tests/contracts/test_live_server.py` 24 passed with the driver venv (the Docker test
runs); ruff check clean, format clean on created files; probes P0, G0, A1, A2, A3, A5 at 1 run: 6/6 pass
(G0 live 0 findings, 7 replayed, verified `access_token`, 6.8 s, no container left).

**Note on the B2 regression runners reported failing at the end of the build.** Their failures were traced to
an untracked `perf.budget.json` that appeared in the real Streak checkout at 16:39. That file was written by
the orchestrator's own re-run of the G14 (missing-index) Streak arm, pointed at the owner's checkout by
mistake (the probe operates on the path it is given). It was identical to that probe's budget, has been
removed, and the ignored `bin/streak-api` rebuilt from clean source; the Streak checkout's `git status` is
back to the owner's own `studio.toml` edit. It was not caused by this node.
