# Live contract check on a Go + MySQL provider (Streak): pre-registration

## 1. Status, and the exact gap

Date: 2026-10-15. Branch `np-livego` (worktree `lattice-livego`, from `np-integ` 30c9120: marketplace, per-project
engine registries, perf engines). **Written BEFORE any code of this change**; no live check was run on Streak. The
author ran recon only: code reading; `select_step` on Streak's spec for the 7 widget imports (below); `go build` of a
`git archive` of Streak `build/streak` a0679e9 in the scratchpad (cold **6.2 s**, warm **0.09 s**, `GOPROXY=off`);
an offline `uv pip install pymysql==1.2.3` into a scratch venv (works). Docker 29.4.0 up, `mysql:8.4` present.
Results: `2026-10-15-live-contract-go-mysql-results.md`, data `data/2026-10-15-live-contract-go-mysql/`. No LLM, 0 tokens.

**The gap, from the code at 30c9120.** Almost everything a MySQL live check needs already exists:
`security/target.start_target_harness` opens MySQL/Postgres/ClickHouse throwaway servers (`_open_db`, prefix
`lattice-sec-`), and Streak's `harness.py` starts `bin/streak-api` on a `mysql://` URL (process runtime, `/health`,
`streak-api migrate` + seed rows, bcrypt login). What stops it:
1. **The guard.** `live._child`: `if getattr(harness, "database", "sqlite") != "sqlite": raise _Fatal(
   "unsupported_database", ...)`. Recorded on Streak 2026-10-13 (W3: exit 2, nothing started).
2. **No driver.** The child is `sys.executable`; the Lattice venv has `bcrypt` 5.0.0, `httpx`, no `pymysql`: it would
   die in `wait_ready` (`ModuleNotFoundError`, uncaught) after a container started (`provider_start_failed`).
3. **A container per replay.** `_replay_one` calls `start_target_harness` per step: 7 x `docker run` mysql:8.4
   (5.1 s each, `2026-10-11-gate-timings.md`).
4. **Leaks on timeout.** `_spawn` SIGKILLs the child's group at once; the container goes only through the child's
   `atexit`, and the Go app has its own session (`process.py` `start_new_session=True`): both outlive a timeout.
5. **A server the user named.** `server_for` honours `$LATTICE_MYSQL_URL` and `[app] database_url`: a replay would
   create `lattice_*` databases there, and Streak's seed sets GLOBAL `sql_mode` on a root/lattice@127.0.0.1 server.
6. **No build.** `bin/` is gitignored: a fresh clone has no binary; `lattice check` never builds (`check.sh` does).

**Premise check.** Possible **without changing Streak**: yes (its harness already does the work). **Premise
correction:** the D-series are spec-only edits, and live replays the CURRENT spec's step against the APP. On the
literal D-series the app is unchanged, so live verifies the unchanged app and is predicted to disagree with static in
4 of 7. Agreement is meaningful only when the app implements the drifted spec, so each drift is run in three modes:
**a** spec + code (a coherent provider change), **b** code only (the app drifts from its spec: the L-series analogue),
**c** spec only (the committed D edits, verbatim).

## 2. Decisions (fixed before coding)

**L1 Scope.** `LIVE_DATABASES = ("sqlite", "mysql")`; Postgres, ClickHouse stay `unsupported_database` (message lists
the supported set): one tested target; adding one is a tuple entry plus its own probe.
**L2 One server per live run, a clone per replay.** For MySQL the child opens one server lazily, at the first replay
that is actually sent (an import set with nothing replayable starts no container), and one `ServerDatabases`:
`build_db` (migrate + seed rows) runs once into the template, every replay gets a fresh clone (seed, then the
scenario's `given`), dropped after the replay; at the end `dbs.close()` then `server.stop()` (`docker rm -f -v`).
`start_target_harness` gains a keyword-only `databases=None` (a ready `make_db`); `None` is today's path, byte for
byte. The SQLite path keeps a fresh SQLite seed per replay through the unchanged code.
**L3 Throwaway only, never a given server.** The container is `lattice-live-mysql-<8 hex>` (`server_for(engine, None,
name_prefix="lattice-live-")`). Before starting it the child removes every `LATTICE_<DIALECT>_URL` from its
`os.environ` (so the Go app, which inherits it, never sees one) and ignores the harness's `database_url`. The live
check never runs `docker pull` and never reconfigures Docker.
**L4 Prechecks, in order, each a `LiveError` code (exit 2, link `error`, findings [] as today):**

| step | code | when |
|---|---|---|
| spec, harness | `spec_failed`, `no_harness` | unchanged |
| database kind | `unsupported_database` | not in L1 |
| driver | `driver_missing` | the engine's `requires` (`pymysql>=1.1`) does not import; message: `lattice[db]` |
| Docker | `docker_unavailable` | `dbserver.docker_available()` is false (no CLI, or `docker info` fails, <= 20 s) |
| image | `docker_image_missing` | `docker image inspect mysql:8.4` fails; message gives the `docker pull` command |
| server | `database_start_failed` | `DbServerError` from `docker run` / `wait_ready` (120 s, dbserver's) |
| app, seed | `provider_start_failed` | `build_db`, `migrate`, app spawn or `/health` (harness: 20 s) fails |
| whole child | `live_timeout` / `live_failed` | `TIMEOUT_S` 300 s unchanged / no result |

**L5 Teardown and leaks.** Normal path as today (transport exit stops the app group SIGTERM then SIGKILL; clone
dropped) plus L2's end. The child installs a SIGTERM handler (`sys.exit(143)`) so `with`/`finally` unwind. The parent
sets `LATTICE_CONTAINER_OWNER=live-<16 hex>` in the child env (overriding a Studio gate's token; containers get
`lattice.owner=live-...`). The child writes `$TMPDIR/live-server.json` (`{owner}`) before `docker run` and appends
each started app's pid to `$TMPDIR/live-pids` right after its `/health` answered. On timeout the parent sends SIGTERM
to the child group, waits <= 15 s, then SIGKILL. After ANY child exit and before removing its TMPDIR, the parent
sweeps only what markers name: `docker ps -aq --filter label=lattice.owner=<owner>` -> `docker rm -f -v`; each
recorded pid whose group is alive -> `killpg(SIGKILL)`. No marker, no Docker call (the SQLite path never calls
`docker`). Known windows (disclosed): a kill between app spawn and its pid write (~15 ms); a parent killed with
SIGKILL leaves its TMPDIR (pre-existing; the child still finishes and cleans its container and app).
**L6 What is compared: unchanged.** `select_step`, `judge`, `_verified`, `fields_unobservable`, `not_checked:
["request_fields"]`, the current spec's first 2xx step, imports judged against themselves, never the pin.
**L7 Result shape.** SQLite: byte-identical. MySQL adds one key: `"database": {"engine": "mysql", "image":
"mysql:8.4", "throwaway": true, "ignored": [...]}` (`ignored`: sorted subset of `["LATTICE_MYSQL_URL",
"database_url"]` that were set). No container name, port or timing in the result (determinism).
**L8 Opt-in unchanged** (`contract = "live"`, not `--static-only`; API and MCP stay static). **L9 No build:** live
runs whatever the harness starts; no `bin/streak-api` is `provider_start_failed` naming the path (risk 4).
**L10 Drivers.** Never installed into the Lattice venv. The builder and probes use a scratch venv layered over it
(section 6); `uv run` selects the Lattice venv, so it gets `driver_missing` (that is probe A2).
**L11 Streak untouched.** Provider = `git clone --no-hardlinks` of Streak at `build/streak` a0679e9 (the built app,
full gate 0 findings, 156 scenarios); `spec.py`, `spec.trace.json`, `studio.toml`, `harness.py`, `check.sh` are
identical to `main` a0db033 (empty `git diff`). Consumer `dogfood/contracts/streak-widget` (7 imports). Pin made on
the clean clone; its `export` and `spec_hash` (`d44b738522ea`) must equal `golden-streak`'s.

## 3. Probes (runner `dogfood/contracts/livego_probes.py`)

**Global.** Canonical JSON (`elapsed_s` dropped, scratch root -> `$WORK`). Every variant is a fresh clone, edits by
`str.replace` with the count asserted first, committed (`-c user.name=probe`), then `go build -o bin/streak-api
./cmd/server` (`GOCACHE=$SCRATCH/gocache GOPROXY=off`). TMPDIR of each CLI call = a fresh `$SCRATCH/tmp-<run>`.
Recon: all 7 imports replay a ONE-step scenario with 4 `given` rows, no `Await`: login `login_answers_a_bearer_token`,
the 6 token imports `access__<me|home|list_habits|progress|add_completion|dismiss>__as_ann`.
**Needs:** G, K, Z, T, A4: Docker + `mysql:8.4` + `go` + the scratch venv; A1-A3, A5: shims or prechecks only, no
container; B1, B2: neither (SQLite and static).

**G0 clean** (x10). Static: exit 0, 0 findings, gaps 2 (`request_field_unobserved cursor @ GET /v1/habits`,
`response_field_unobserved access_token @ POST /v1/auth/login`). Live: `replayed` 7, answered 201 on completions and 200
on the other 6; findings [], not_replayed [], `verified_live` = access_token, `database` block with `ignored` [];
exactly **1** `lattice-live-` container created (`docker events`). Exit 0.

**Drift cases.** Spec edits = the committed `streak_probes.DRIFTS` edit lists for D1, D2, D3, D4, D6, D7, D9 (mode a
and c). Code edits (mode a and b), each count 1:

| id | Go (and harness) edit |
|---|---|
| D1 | `internal/home/routes.go`: `r.Member.GET("/home", httpapi.Handle(h.home))` -> `_ = h.home` |
| D2 | `internal/nudges/routes.go`: `c.JSON(http.StatusOK, db.NudgeBody(out))` -> `c.JSON(http.StatusNoContent, ...)` |
| D3 | `internal/auth/routes.go`: `` `json:"expires_in"` `` -> `` `json:"expires_in_s"` `` (TokenPair) |
| D4 | auth: `b.Str("password")\n\tif err != nil \|\| !ok {\n\t\treturn httpapi.Invalid("password", "password is required")` -> `passphrase` x3; mode a also `harness.py` `"password": f"password-{handle}"` -> `"passphrase": ...` |
| D6 | `internal/httpapi/pagination.go`: `` `json:"next_cursor"` `` -> `` `json:"next"` `` |
| D7 | `internal/httpapi/router.go`: `v1 := r.Group("/v1",` -> `"/v2",`; mode a also `harness.py` `"/v1/auth/login"` -> `"/v2/..."` |
| D9 | auth: `` `json:"expires_in"` `` -> `` `json:"expires_in,string"` `` |

**Classes.** S = static BROKEN {(endpoint, kind, item)}, L = live BROKEN, N = endpoints live did not replay.
EXACT S = L; SUPERSET S strictly in L; UNREPLAYED S non-empty, L empty, every endpoint of S in N; LIVE-ONLY S empty,
L non-empty; DISAGREE otherwise. Strict agreement = EXACT; lenient = EXACT, SUPERSET or UNREPLAYED.
Static in modes a, c is predicted equal to the committed `data/2026-10-13-contracts-streak/Dn.json` sets (BROKEN
and CHANGED); in mode b static is G0's (0 findings, `version_changed` true, `spec_hash_changed` false).

| id | static BROKEN (a, c) | live a (spec+code) | live b (code) | live c (spec) | class a / b / c |
|---|---|---|---|---|---|
| D1 | endpoint_missing GET /v1/home | N: home ("no 2xx step for as_user ann") | status_not_observed 200 (answered 404) | as a | UNREPL / LIVE-ONLY / UNREPL |
| D2 | status_not_observed 200 @ dismiss | status_not_observed 200 (204) + response_field_missing id (204 has no body) | as a | 0 (app answers 200) | SUPERSET / LIVE-ONLY / DISAGREE |
| D3 | response_field_missing expires_in @ login | same | same | 0 | EXACT / LIVE-ONLY / DISAGREE |
| D4 | request_field_missing password @ login | 0 (request fields not checked) | login status_not_observed 200 (422); 6 N (harness login 422) | login status_not_observed 200 (422) | DISAGREE / LIVE-ONLY / DISAGREE |
| D6 | response_field_missing next_cursor @ GET /v1/habits | same | same | 0 | EXACT / LIVE-ONLY / DISAGREE |
| D7 | endpoint_missing x7 | N x7 ("not in the provider's current spec"), 0 containers | login status_not_observed 200 (404); 6 N | as a | UNREPL / LIVE-ONLY / UNREPL |
| D9 | none (MISS) | 0 | 0 | 0 | EXACT / EXACT / EXACT |

`verified_live` (access_token) wherever login answered 200: every a and c row but D7, b rows D1, D2, D3, D6, D9.
**Predicted verdict:** mode a strict 3/7 (4/8 with G0), lenient 6/7; mode c strict 1/7, lenient 3/7; mode b live
alone sees 6/7. The proposition "static and live agree on the D-series" is predicted **false in the strict sense**:
removals and lost access are silence (not replayed), request fields are unchecked, a real 204 drops the body the
spec still shows. **Pass G:** 22/22 configurations exactly as tabled (S, L, N, class; exit 1 in every row but D9
and G0, which exit 0; containers created 1, or 0 for a-D7 and c-D7); G0 and mode a identical 10/10, modes b and c 3/3. Answer codes in brackets are predictions
recorded, not judged (404/422/204 bodies are Streak's), the kinds and items are judged.

**A absence and failures** (G0's workspace, x3 each, identical). A1 a `docker` shim first on PATH that logs and
exits 1: `docker_unavailable`, log = `info` only. A1' real CLI, `DOCKER_HOST=unix://$SCRATCH/none.sock`: same code.
A2 the Lattice venv (`uv run python -m lattice.contracts ...`): `driver_missing`, 0 Docker calls (logging shim that
forwards to the real CLI). A3 shim answering `info` 0, `image inspect` 1: `docker_image_missing`, no `run`/`pull`
logged. A4 clone without `bin/`: `provider_start_failed`, message names `bin/streak-api`, its container removed.
A5 `--static-only` under A1's shim: exit 0, live `not_run`/`static-only`, shim log empty. Each: exit 2 (A5: 0), no
`streak-api` process, wall <= 25 s (predicted <= 3 s, A4 <= 9 s). Pass 6/6.

**K leaks.** K1: after every G, A, Z call: 0 `lattice-live-` containers (`docker ps -a --filter
name=lattice-live-`; other `lattice-*` containers belong to other runs and are ignored), 0 processes whose command
starts with a clone's `bin/streak-api`, 0 entries in the call's TMPDIR. Pass: 0 over all ~190 calls. K2: an
in-process driver sets `live.TIMEOUT_S` to 2, 5, 8 s and calls `run_check` (x3 each): 2 and 5 predicted
`live_timeout`, 8 recorded either way; return <= TIMEOUT_S + 20 s; 0 leaks 5 s after return. K3a: SIGKILL the child
(`lattice.contracts.live`) at 3 s and 6 s (x3): `live_failed`, 0 leaks after return. K3b: SIGKILL the parent at 4 s
(x3): within 60 s 0 containers and 0 processes; 1 TMPDIR left per call (predicted known leak, reported). Pass: K1 0,
K2 9/9 clean, K3a 6/6, K3b 3/3 on containers and processes.

**Z1 never the real database** (x3). The runner starts a decoy `lattice-decoy-mysql` (database `streak_real`, a
sentinel row) and points at it: `LATTICE_MYSQL_URL` in the caller env, `HABIT_DB_DSN` and `DATABASE_URL` in the
manifest's `[[project]] env`, `[app] database_url` committed in the clone's `studio.toml`. Pass: live result = G0's
but `ignored: ["LATTICE_MYSQL_URL", "database_url"]`; decoy `Connections` between the runner's two reads +1 (its own);
`SHOW DATABASES`, the sentinel checksum and GLOBAL `sql_mode` unchanged. Prediction: pass (Streak's harness overrides
the two app variables; that is the harness's doing, not Lattice's: risk 5).

**B byte-compat and regressions.** B1: job-scheduler pair (`probes.py`'s clones of ef17cf8 + `ci-bot`): L1-L5 and W3
via the CLI, `--json` and text, new code vs old (`git archive 30c9120 lattice` in `$SCRATCH/old`), canonical outputs
byte-identical 10/10, a `docker` logging shim sees 0 calls; `git diff 30c9120 -- tests/contracts/test_live.py`
empty and it passes. B2: `probes.py` 35/35, `impact_probes.py`, `exclude_probes.py`, `linkedq_probes.py`,
`transitive_probes.py` all pass (`--runs 10`); `streak_probes.py --runs 10 --allow-code-diff` 31/32: W3 predicted to
fail on its error-code check only (`driver_missing` under `uv run`), "nothing started" still true; `pytest
tests/contracts tests/security tests/workspace tests/perf` 0 failures (Docker tests skip in the Lattice venv, run in
the scratch venv).
**T cost** (reported, thresholds). G0 live CLI wall, median of 10: predicted **8 s** (container 5.1 + migrate/seed
0.3 + 7 replays ~1 + interpreter and two spec loads ~1.5), threshold <= 30 s, max <= 60 s; a-D7 <= 3 s; Streak
static-only <= 1.5 s (0.33 s measured 2026-10-13). Whole runner: predicted 25-40 min.

## 4. Mutants (`dogfood/contracts_mutants.py`, LG1-LG13; killed by `tests/contracts` and the named probe)

| id | one-line mutant | killed by |
|---|---|---|
| LG1 | `LIVE_DATABASES` back to sqlite only | G0 (`unsupported_database`) |
| LG2 | no shared server: `databases=None` on MySQL (a container per replay) | G0 creates 7; unit `server_for` called once |
| LG3 | one clone reused for every replay | G0 (given rows refused on the 2nd replay: N non-empty) |
| LG4 | `LATTICE_MYSQL_URL` kept in the child env | Z1 (decoy connections, `lattice_*` databases) |
| LG5 | the harness `database_url` honoured | Z1 |
| LG6 | driver precheck removed | A2 (`provider_start_failed`, a container started) |
| LG7 | Docker precheck removed | A1 (`database_start_failed`) |
| LG8 | image precheck removed | A3 (`run` logged) |
| LG9 | label sweep removed | K3a (container left) |
| LG10 | pid sweep removed | K3a (`streak-api` left) |
| LG11 | server opened eagerly (before the first replay) | a-D7, c-D7 (1 container, not 0) |
| LG12 | `database` block also on SQLite | B1 (byte diff) |
| LG13 | SIGTERM grace removed (SIGKILL at once, as before) | unit test (fake child traps TERM); probes cannot see it |

Prediction: 13/13 killed (LG13 by its unit test only).

## 5. Risks

1. **Cost.** A fixed ~5-6 s container per live link per check (SQLite: 0.72 s); two consumers of one provider pay
   it twice (no cross-link reuse, by design). Docker Desktop memory for mysql:8.4 while it runs.
2. **Docker flakiness.** VM hiccups, port-publish races, other nodes' containers (a `lattice-perf-mysql-*` was
   running while this was written). No retry in the live check: a failed run in the determinism series is a
   finding, never re-run silently. The runner never starts, stops or restarts Docker (Docker Desktop restarts the
   owner's `cyclops-*` containers when it starts).
3. **Drivers.** PyMySQL is not in the Lattice venv; users running through `uv run` get `driver_missing` until they
   install `lattice[db]`. Probes use 1.2.3 (Streak's venv's version).
4. **Stale binary.** Live tests whatever `bin/streak-api` is; a binary older than the clone's code silently tests old
   code. The runner builds every variant; a harness build hook is out of scope.
5. **The harness owns the DSN.** Lattice guarantees the URL it hands the factories and strips its own env variables;
   a harness or app reading another variable could still reach a real database. Z1 shows Streak only.
6. **Agreement is the wrong yardstick for removals.** UNREPLAYED is silence; emitting a finding for "no 2xx step"
   would change the SQLite output (L7), so it is deliberately not done here.
7. **Cannot show:** production data (seed only), the consumer's real requests (spec steps are replayed), request
   fields, nested item keys (D12), types (D9: both blind), headers (`X-Client-Version` -> 426), `Await`s and the
   nudger, Postgres/ClickHouse, another Go provider, Linux Docker. **Builder bias:** the builder reads these
   predictions; the mode a code edits are the author's, chosen to mirror D.

## 6. Blind vs post hoc, and commands

Fixed now. A failed prediction is a **finding** (prediction error or defect), never fixed silently; fixes are separate
post-hoc commits; W3's expected code in `streak_probes.py` is updated post hoc and disclosed.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-livego PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/livego
cd /Users/vivekgade/Documents/lattice-livego
uv venv --python /Users/vivekgade/Documents/lattice/.venv/bin/python "$SCRATCH/venv"
uv pip install --offline --python "$SCRATCH/venv/bin/python" pymysql==1.2.3
echo /Users/vivekgade/Documents/lattice/.venv/lib/python3.14/site-packages \
  > "$SCRATCH/venv/lib/python3.14/site-packages/lattice-venv.pth"          # bcrypt, httpx from the Lattice venv
docker info --format '{{.ServerVersion}}' && docker image inspect mysql:8.4 >/dev/null   # never pulled by probes
git -C /Users/vivekgade/Documents/streak rev-parse build/streak; git -C /Users/vivekgade/Documents/streak status --short
"$SCRATCH/venv/bin/python" dogfood/contracts/livego_probes.py --runs 10 --streak-ref a0679e9 \
    --scratch "$SCRATCH/g" --out "$SCRATCH/g-data"                          # G, A, K, Z, B1, T
for p in probes impact_probes exclude_probes linkedq_probes transitive_probes; do       # B2, Lattice venv
  uv run python dogfood/contracts/$p.py --runs 10 --scratch "$SCRATCH/$p" --out "$SCRATCH/$p-data"; done
uv run python dogfood/contracts/streak_probes.py --runs 10 --allow-code-diff --scratch "$SCRATCH/s" --out "$SCRATCH/s-data"
"$SCRATCH/venv/bin/python" dogfood/contracts_mutants.py $(seq -f LG%g 1 13)  # control passes; 13 killed
"$SCRATCH/venv/bin/python" -m pytest tests/contracts tests/security tests/workspace tests/perf -q -rs
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

**Pass:** G 22/22 with determinism, A 6/6, K as stated, Z1 3/3, B1 10/10 and 0 Docker calls, B2 as stated, T within
thresholds, 13/13 mutants. Streak and job-scheduler are never written (clones and archives only).

## 7. File ownership (build node)

Owns: `lattice/contracts/live.py` (L1-L10), `lattice/security/target.py` (only the keyword-only `databases=None` of
`start_target_harness`; default path unchanged), new `tests/contracts/test_live_server.py` (fakes for docker,
server and child; one Docker test skipped without Docker or PyMySQL), new `dogfood/contracts/livego_probes.py`,
`dogfood/contracts_mutants.py` (append LG1-LG13), `dogfood/contracts/streak_probes.py` (W3
expected code, post hoc), the results file and data directory. **Not touched:** `lattice/spec/{dbserver,db,engines,
process}.py`, `engines/mysql.toml`, `lattice/contracts/{check,export,__main__}.py`, `lattice/workspace.py`,
`lattice/perf/`, existing tests (an edit would be a finding), Streak, job-scheduler, the main checkout.

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**A. The premise is reframed, as the document found.** "Static and live agree on the D-series" is predicted
false in the strict sense (the D-series are spec-only edits and live replays the current spec's step against
the unchanged app). The registered question is the three-mode table of section 3 (a: spec and code together,
b: code only, c: spec only), with every cell a numeric prediction. A disagreement is reported as the finding it
is; no cell is tuned.
**B. Environment.** Docker is running and `mysql:8.4` is local; go is at /opt/homebrew/bin/go. The driver venv
already exists and may be reused read-only (it holds `pymysql`, `psycopg`, `clickhouse-connect` over the Lattice
venv): `/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/perfengines/perfvenv/bin/python`.
Nothing is installed into the Lattice venv. Another node may have `lattice-perf-*` containers running: the
leak probes filter on the `lattice-live-` prefix only, as written.
**C. Security module.** The keyword-only `databases=None` on `lattice/security/target.start_target_harness` is
approved because the SQLite path must stay byte-identical (the byte-compat probe covers it, and the existing
security tests run unchanged). No other change under `lattice/security/`.
**D. Provider.** The Streak provider is `build/streak` a0679e9 as the document says, cloned into scratch;
the owner's checkout is never written.
**E. Reporting.** Probe wall time (25-40 min) is accepted. Cost (seconds per live check) is reported, not
gated beyond the stated 30 s threshold.
