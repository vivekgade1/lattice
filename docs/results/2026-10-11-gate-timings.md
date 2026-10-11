# 2026-10-11: Streak gate timings and narrowed gates (G1)

Follows `2026-10-11-gate-profile.md` (P0). The check now reports `timings` (per layer, and per
layer-D scenario where its seconds went), and `--only SELECTOR ...` runs layers D and X on the
selected scenarios only (`docs/lattice-reference.md`, "Narrowing and timing a check").

Setup: a detached worktree of Streak `build/streak` at `9417351` (scaffold and auth built; 156
scenarios in this spec), Studio's gate environment (`UV_NO_SYNC=1`, Streak's `.venv`,
`LATTICE_REBUILD=1`), `PYTHONPATH` set to the `perf-gates` worktree so the modified Lattice was
the one imported (checked: `lattice.__file__` was under `lattice-perf`). MySQL 8.4 in a throwaway
container per run. Runs were sequential on an otherwise idle machine. Command:
`./check.sh --layers <L> --json [--only ...]`. Wall = the whole `check.sh` (go build, uv,
container, check, JSON).

## The three measurements

| Run | Scenarios | Wall | Check `total_s` | Layer seconds | Share of full D wall |
|---|---|---|---|---|---|
| (a) full layer D | 156 | **290.3 s** (rerun 289.2) | 288.9 | D 283.5 | 100% |
| (b) `--only` 3 auth scenarios | 3 | **6.3 s** | 5.9 | D 0.59 | **2.2%** |
| (c) `--only` 15 account/auth scenarios | 15 | **7.4 s** | 7.1 | D 1.73 | **2.5%** |

(b): `login_answers_a_bearer_token refresh_rotates_the_token logout_ends_only_this_installation`.
(c): the 15 scenarios of registration, login, refresh, logout, `me` and device registration.
The gate's target (3 scenarios in under 10% of the full layer-D time) is met with room to spare:
what is left in (b) is fixed cost, mostly the MySQL container (below).

Extra runs:

| Run | Wall | Check `total_s` | Layers |
|---|---|---|---|
| `--only rule:R16 rule:R19 'access__*__as_anon'` (26 scenarios) | 7.6 s | 7.3 | D 1.95 |
| `--layers CD --only` (b)'s 3 | 6.5 s | 6.2 | C 0.33 (includes the 0.27 s seed build), D 0.38; invariants 0.09 s |
| full layer X (`x_repeats 5`, 60 races, 456 clones) | 82.7 s | 82.4 | X 77.0 |
| `--layers X --only` (b)'s 3 (3 races, 18 clones) | 9.3 s | 9.0 | X 3.5 |
| `--only login_answers_a_bearer_tokn` (a typo) | 0.3 s, exit 2 | | `--only 'login_answers_a_bearer_tokn' matches no scenario of the spec (156 scenarios); nearest: login_answers_a_bearer_token`. Nothing ran and no container started |

## Where a scenario's ~1.9 s goes: it does not

The mean of 1.82 s per scenario is an average of two very different groups:

| Group | Scenarios | Sum | Per scenario |
|---|---|---|---|
| 9 scenarios with an `Await` on the `nudger` service | 9 | **271.2 s** | 30.1 s |
| everything else | 147 | **12.4 s** | 0.084 s (median 0.072) |

The 9 `Await` scenarios (`nudge_after_daily_pass_is_suppressed`, `nudger_creates_missing_nudges`,
`nudger_waits_for_nudge_time`, `pass_*`) fail at this tip, because the nudges area is not built
yet. Each one waits out its `Await`'s `within_s` of **30 s** for an effect the stub nudger never
produces (`await_s` 30.03 s each, 270.2 s in all). **95% of layer D is waiting on unbuilt
background-service behaviour.** Once the nudger works, each wait should end at its first
successful poll.

Breakdown over all 156 scenarios (run a2, which has `await_s`; run a gives the same numbers to
within 0.1 s):

| Part | Mean | Median | Sum | Share of D |
|---|---|---|---|---|
| `db_clone_s` (MySQL clone of the seed) | 0.027 | 0.024 | 4.3 s | 1.5% |
| `given_s` | 0.004 | 0.004 | 0.6 s | 0.2% |
| `app_start_s` (spawn + wait for `/health`) | 0.025 | 0.018 | 3.9 s | 1.4% |
|   of which `process_spawn_s` | 0.003 | | 0.5 s | |
|   of which `health_wait_s` (Go start-up incl. `db.Migrate`) | 0.015 | 0.008 | 2.3 s | 0.8% |
| `steps_s` | 1.754 | 0.011 | 273.5 s | 96.5% |
|   of which `await_s` | 1.732 | 0 | 270.2 s | 95.3% |
|   of which `login_s` | 0.004 | | 0.6 s | |
| `app_stop_s` (SIGTERM + reap) | 0.001 | | 0.2 s | 0.1% |
| `db_release_s` (DROP the clone) | 0.005 | | 0.8 s | 0.3% |

Fixed cost per run (`timings.setup`): **MySQL container start 5.1 s**, seed build 0.27 s
(`streak-api migrate` plus the seed rows, once), teardown 0.27 s; `check.sh`'s go build, uv and
Python start add about 0.4 to 1.4 s. In (b), 5.1 s of the 5.9 s check is the container.

P0's hypotheses do not hold at this tip: start-up per scenario (AutoMigrate on every start) is
15 ms of health wait, the nudger is not started with the app (only by `Await` steps), and
registration's bcrypt cost is small: `register_answers_the_user_with_a_lowercased_email` takes
0.18 s and `password_length_bounds` 0.26 s. The slowest non-`Await` scenario is the first one,
`access__health__as_anon` at 0.46 s, because its clone pays the one-time seed build; next is
`rate_limit_120_per_minute` at 0.36 s for 123 requests.

## The `--app-per-batch` experiment: not built

Starting a fresh app per scenario costs `app_start_s` + `app_stop_s` = 26 ms, 1.5% of layer D
(4.1 s of 283.5 s). A batch mode would also need the app to change databases between scenarios
(the process is bound to its clone's URL at start) or scenarios to share a database, which changes
what a scenario means. That is unsafe and saves almost nothing, so it was not added. No existing
flag changed meaning.

## What this changes for the plan

1. **Node-scoped gates work.** With `--only` set to a node's scenarios, the in-loop gate costs
   the container plus about 0.1 s per scenario (more for a scenario that awaits a service).
   The level-end full gate stays as it is.
2. **The remaining lever in a full D run is `Await` timeouts, not app start.** Ideas for later,
   none built here: a cap on `within_s` in the in-loop gate (an opt-in flag); ending the wait
   early when the service process exits (this already happens) or logs that its pass is done;
   ordering `Await` scenarios last, so a node's failing fast scenarios report sooner.
3. **The container is the fixed cost** (5.1 s of a narrowed run). Reusing one MySQL server
   across the gates of a build (`--database-url`, which already exists) would make a narrowed
   gate about 1 to 2 s. That belongs to Studio (S2), not the check.
4. **Layer X narrows too:** 3.5 s for 3 scenarios instead of 77 s.

## Notes

- On a selection error (exit 2) `check.sh` writes an empty `metrics/last-check.json`, and its
  summary one-liner prints a JSON traceback (it is `|| true`). The error itself is on stderr.
  Studio should read the exit code and stderr, not the file, when the exit code is 2.
- `check.sh` forwards extra arguments last (`"$@"`), so `./check.sh --only a b` works unchanged.
  `--only`'s values stop at the next flag, including the `--json` that check.sh appends.
- `layers.C` covers the seed check only, and it includes the one-time seed build whenever C runs
  first. Layer C's per-request SQL sits inside D/X's seconds (`invariants_s`, 31 ms per
  scenario on MySQL in the CD run, from a fresh connection per request).
- Cleanup: the Streak worktree was removed (`git worktree remove --force`, `prune`). Each run
  removed its own `lattice-mysql-*` container, and no `streak-api` process was left.
