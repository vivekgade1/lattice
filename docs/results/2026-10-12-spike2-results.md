# Spike 2 results: module marketplace, descriptor-only slice (M-P, M-L, M-S)

Date: 2026-10-12. Worktree `/Users/vivekgade/Documents/lattice-np2`, branch `np-spike2` from `next-phases`
cc77f73. Nothing committed. Pre-registration: `docs/results/2026-10-12-spike2-prereg.md` (written before
any probe, amended once before M-P2 ran: the MariaDB server arguments). Evidence the design follows:
`docs/results/2026-10-12-m-r-engine-seams.md`. Field reference and SDK surface:
`docs/design/lattice-module-descriptor.md`. Trust probe: `docs/results/2026-10-12-spike2-trust-probe.md`.
Raw data: `docs/results/data/2026-10-12-spike2/` (`streak-runs.json`, `mutants.json`,
`m-l1-transcript.txt`) and `docs/results/data/2026-10-12-spike2-trust/`.

## What was built

- `lattice/spec/engines.py` (new): `EngineDescriptor` read from TOML, validated with exact messages
  (`EngineError`), a process-wide registry (`REGISTRY`) with clash rules, the `module:Class` adapter hook.
  `lattice/spec/engines/{postgres,mysql,clickhouse}.toml` (new) hold the three built-in engines.
- `db.py`, `dbserver.py`, `invariants.py`, `race.py` and the three `--database` parsers (`__main__`,
  `ask`, `draft`) read the registry: the scheme ladder, canonical URL ladder, driver URL table, adapter
  dict, `check_dialect`, `IMAGES`/`PORTS`/`DATA_DIRS`/`ADMIN_DB`/`_run_args`/`_admin_user`, the drop,
  quote, terminate and Postgres clone statements, the invariant compiler's quote/boolean/backslash/
  float-cast/divide ladders, and layer X's ClickHouse test are all lookups now. `IMAGES` etc. remain as
  values derived from the registry (same contents). SQLite stays built in.
- A project adds an engine without core edits: `studio.toml` `[engines] paths`, `lattice spec check
  --engine-file FILE` (repeatable; registered before the parser is built, so the name is a `--database`
  choice), `--modules DIR`; `lattice check --project DIR` adds both from the project.
  `docs/examples/engines/mariadb.toml` is the pure-descriptor proof (image `mariadb:11`, adapter `mysql`).
- `lattice/modules/` (new): `lattice module install|list|info|uninstall`, the lock file, re-hash on every
  load, `--index DIR`, `file://`; git URLs parsed and refused ("not implemented yet").
- Studio: `GET /api/engines` (API v1.14 section in `lattice/studio/API.md`), mock parity, the project's
  engines registered at load, on every `GET /api/engines` and before every check/verify. **No web UI**:
  the UI has no place that shows the project's check settings, so the list is API and mock only.
- Dogfood: rules R82-R84 in `dogfood/studio/REQUIREMENTS.md`, scenario `database_engines_are_listed`
  (R82, R83), R84 unchecked with the reason (the seed cannot gain modules without changing the seed commit
  every recorded id depends on).
- Docs: `docs/design/lattice-module-descriptor.md`, a row in `docs/lattice-reference.md`.

### Files changed (lines added/removed from `git diff --numstat`; new files: line count)

| File | Change |
|---|---|
| `lattice/spec/db.py` | +57 / -124 |
| `lattice/spec/dbserver.py` | +63 / -64 |
| `lattice/spec/invariants.py` | +23 / -28 |
| `lattice/spec/race.py` | +17 / -4 |
| `lattice/spec/__main__.py` | +66 / -5 |
| `lattice/spec/ask.py`, `draft.py` | +3/-3, +3/-2 |
| `lattice/cli.py` | +17 / -2 |
| `lattice/studio/server.py` | +58 / -1 |
| `lattice/studio/mock_server.py` | +41 |
| `lattice/studio/API.md` | +41 / -1 |
| `dogfood/studio/REQUIREMENTS.md`, `spec.py`, `spec.trace.json` | +6, +32, +26/-1 |
| `docs/lattice-reference.md` | +1 |
| new `lattice/spec/engines.py` | 880 (ruff-formatted) |
| new `lattice/spec/engines/*.toml` | 50 + 42 + 45 |
| new `lattice/modules/__init__.py`, `__main__.py` | 624, 131 |
| new `tests/spec/test_engines.py` | 881 |
| new `tests/modules/test_modules.py` (+ empty `__init__.py`) | 423 |
| new `tests/studio/test_engines_api.py` | 195 |
| new `dogfood/module_mutants.py` | 236 |
| new `docs/examples/engines/mariadb.toml` | 46 |
| new `docs/design/lattice-module-descriptor.md` | 142 |
| new results docs and data | prereg 96, trust probe 119, this file, data files |

Core, net: `db.py` lost 67 lines; the engine-specific code left in core is the three adapter classes and
the MySQL/ClickHouse clone procedures (the SDK-needed rows of M-R, as recommended).

## Blind (pre-registered) results

### M-P1 equivalence: PASS (prediction "identical" held)

(a) Tests, no existing test file edited (`git diff --stat tests/` lists no modified file; only new files):

| Run | Result |
|---|---|
| brief's command on the refactored core, before the new tests existed | **1106 passed, 5 skipped** (383.6 s), = baseline |
| brief's command plus `tests/modules`, with the new tests (final) | **1197 passed, 5 skipped** (375.3 s) = 1106 + 91 new |
| Docker-free tests of `tests/spec/test_db.py` (`-k 'canonical or url_for or sqlite or same_compares'`) | 6 passed |
| post hoc: `tests/spec/test_db.py` + `test_multidb.py` in full, with Docker (all three engines) | 37 passed (32.8 s), no container left |

(b) Dogfood gate `(cd dogfood/studio && ./check.sh)`: **PASS, 0 findings, 393 cases** (baseline 387 + the
6 steps of the new scenario), 47 scenarios, 48.8 s.

(c) Streak a0679e9, `./check.sh` (MySQL, ABCDX, `--x-repeats 5`), wall time by `/usr/bin/time -p`:

| Run | Lattice tree | Verdict | Findings | Scenarios | Cases | Wall |
|---|---|---|---|---|---|---|
| B1 | `/Users/vivekgade/Documents/lattice` (unrefactored) | PASS | 0 | 156 | 480 | 146.47 s |
| B2 | same, right before R1 | PASS | 0 | 156 | 480 | 146.02 s |
| R1 | `lattice-np2` (refactored) | PASS | 0 | 156 | 480 | 147.86 s |
| R2 | same | PASS | 0 | 156 | 480 | 147.16 s |

mean(B) 146.25 s, mean(R) 147.51 s: **+0.86%**, inside the 15% criterion. Layer X 117.0/118.5 s vs
118.8/118.2 s; 613 clones in every run. No container left after any run (label
`lattice.owner=lattice-mod-spike2`). R1/R2 imported `lattice-np2` (checked:
`lattice.spec.engines.__file__` under `lattice-np2` with the same environment).

### M-P2 third engine with no core edit: PASS (prediction "starts and runs, verdict unknown")

`./check.sh --database mariadb --engine-file <scratch>/mariadb.toml` (a copy of
`docs/examples/engines/mariadb.toml`): **PASS, 0 findings, 156 scenarios, 480 cases, 149.17 s**; the
report says `database: mariadb (container lattice-mariadb-7f6f6fcd); ... 613 clone(s), 14 ms each on
average` (MySQL: 26 ms). Post hoc confirmation that it really was MariaDB: the same descriptor started a
container `lattice-mod-mariadb-37e7f4b7`, image `mariadb:11`, `SELECT VERSION()` = `11.8.9-MariaDB-ubu2404`,
removed after. So Streak's GORM / go-sql-driver app and the checker's PyMySQL adapter and MySQL clone
procedure worked unchanged against MariaDB 11.8 for all 156 scenarios and 60 races. Caveat, decided before
the run and pre-registered: the descriptor's canonical URL is `mysql://` (Streak's harness refuses any
other scheme); with `mariadb://` the harness would have refused.

### M-L1 local install with pinning: 5 of 5

Transcript: `docs/results/data/2026-10-12-spike2/m-l1-transcript.txt` (real CLI, `python -m lattice.cli
module ...`, scratch project; the module is the MariaDB descriptor with a manifest).

1. `install` without `--yes`, stdin `/dev/null`: manifest, sha256 and the manifest file printed, `refused:
   not installed: ... was not confirmed`, **exit 2**, nothing in the project. Pass.
2. `install --yes`: exit 0; `.lattice/modules/mariadb-engine/{engine.toml,lattice-module.toml}` and
   `lattice.modules.lock` (name, version, kind, sha256 `a3d5f5c0...`, source). Pass.
3. `list`: `mariadb-engine 0.1.0 engine ok a3d5f5c084bb ...`, exit 0; `info` prints the lock entry and
   manifest. Pass.
4. One bit of the first byte of the installed `engine.toml` flipped: `list` shows `changed` with both
   hashes, exit 1; `python -m lattice.spec check ... --database mariadb --modules proj` exits **2** with
   `ModuleError: module 'mariadb-engine' (0.1.0) is changed: its tree hashes to 3ed06ae6..., the lock says
   a3d5f5c0...; it is refused`. Pass.
5. `uninstall`: exit 0, the module directory and the lock file gone (the lock had one entry), `list` says
   no modules. An empty `.lattice/modules/` directory remains. Pass.

### Mutants: 5 of 5 killed, each by the predicted file; dogfood caught none (as predicted)

`uv run python dogfood/module_mutants.py --dogfood --jobs 2` (suite: `tests/spec/test_engines.py`,
`tests/modules`, `tests/studio/test_engines_api.py`; plus the dogfood gate per mutant). Control: 91 passed,
dogfood PASS 393.

| Mutant | Predicted | Killed by | Dogfood |
|---|---|---|---|
| MM1 mysql `default_port = 3307` | test_engines | test_engines: 3 tests (`builtin_adapter_default_ports`, `builtin_container_tables`, `defaults_fill_in_optional_fields`) | PASS (not caught) |
| MM2 tamper check skipped | test_modules | test_modules: 4 tests, test_engines_api: 1 | PASS |
| MM3 install without confirmation | test_modules | test_modules: 2 tests (refuse, CLI without a TTY) | PASS |
| MM4 unknown capability flag accepted | test_engines | test_engines: the exact-message case | PASS |
| MM5 lock hash ignores contents | test_modules | test_modules: 4 tests, test_engines_api: 1 | PASS |

## Trust probe (M-S), summary

On macOS 27.0, `sandbox-exec` with `(deny network*) (deny file-write*)` plus an allowance for the
module's directory blocked all 9 hostile attempts of the provision step except reading home files
(blocked too with a home-read deny), children included, for +4.5 ms per process; `docker run --network
none` confines equally at +122 ms per process. Not enforceable: a provision step that needs Docker (every
engine's), because the Docker socket is a unix socket the profile blocks, and allowing it let the
sandboxed step write outside its directory through a bind mount. Engine provisioning therefore has to stay
data Lattice executes (the descriptor's `[server]`), which this slice does. An adapter `module:Class` runs
unsandboxed inside the checker. Full table: `docs/results/2026-10-12-spike2-trust-probe.md`.

## Post hoc (not pre-registered)

- **A bug the new value-for-value test caught during development**: the template helper
  `fill(template, **values)` clashed with the `{template}` placeholder of `clone_template`, so every
  Postgres clone would have raised `TypeError`. The brief's verification command does not run any
  Postgres clone (`test_db.py`/`test_multidb.py` are excluded and need Docker), and Streak is MySQL; it was
  caught by `test_the_postgres_clone_is_the_old_two_statements` and fixed (positional-only parameter)
  before any probe. This is the argument for running the Docker tests too: they passed (37) afterwards.
- MariaDB clones were faster than MySQL's (14 vs 26 ms each, n=1 run each; not investigated).
- The Lattice venv has no database drivers (psycopg, PyMySQL, clickhouse-connect): the Docker tests ran
  with the Streak venv's driver packages linked into a scratch directory on `PYTHONPATH` (same Python
  3.14.0). Those tests started their own `lattice-postgres/mysql/clickhouse-*` containers (the fixtures'
  fixed prefix), labelled with the owner label and all removed.

## Decisions made that the brief did not fix

1. MariaDB's canonical URL is `mysql://` with `url_schemes = ["mariadb"]` (pre-registered), and an added
   engine may take over a built-in engine's *alias* scheme (`mariadb` from mysql) but not a primary scheme
   or a name. `server_for` accepts a given URL whose scheme is the engine's template scheme.
2. Descriptor fields beyond the brief's list: `title` (layer X's note names "ClickHouse"), `placeholder`,
   `terminate_sessions` and `clone_template` (Postgres' clone and post-seed terminate as data),
   `docker_args` (ClickHouse's `--ulimit`), driver tables `{template, query_auth, query_fixed, query_url}`
   (needed to reproduce the old query-string order exactly).
3. Capabilities: an upper count bound is "not applicable" when an engine has **neither** unique
   constraints nor transactions (ClickHouse; identical behaviour for the built-ins). Exactly the two flags
   are allowed.
4. `GET /api/engines` adds `errors` (descriptors that did not load, refused modules) to the brief's shape.
5. `--modules DIR` is explicit (no implicit loading from the cwd); `lattice check --project` passes it when
   the project has a lock file. `lattice module list` exits 1 when a module is not `ok`.
6. The tree hash is sha256 over (relative path, sha256 of content) per file in path order; `.DS_Store` is
   ignored; install drops `__pycache__`, `.git`, `.DS_Store`; symlinks are refused; an adapter is imported
   with bytecode writing off so loading never changes an installed tree. The lock is JSON.
7. The registry is process-wide. Studio registers each project's engines into it; two projects in one
   hub that describe the same engine name differently get an error for the second (listed, not fatal).
   Untested in a hub.
8. Re-registration happens at Studio load, on every `GET /api/engines` and before each check/verify, so a
   module tampered while Studio runs is dropped before the next check.
9. Index entries resolve a bare name only when `--index` is given and no such path exists.
10. `UV_NO_SYNC=1` was added to the Streak environment (pre-registered) so `uv run` never touches Streak's
    venv.

## Final verification (last runs, on the final tree)

```
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv PYTHONPATH=/Users/vivekgade/Documents/lattice-np2
uv run pytest tests/perf tests/studio tests/mcp tests/test_cli.py tests/spec tests/modules -q \
  --ignore=tests/spec/test_db.py --ignore=tests/spec/test_multidb.py -p no:cacheprovider
    -> 1197 passed, 5 skipped in 375.29s   (baseline 1106 passed, 5 skipped; +60 test_engines,
       +26 tests/modules, +5 test_engines_api)
(cd dogfood/studio && ./check.sh)  -> PASS: 0 findings (D 0, X 0); 393 cases run (also the control of
                                       the mutant run: 393)
uv run ruff check <the 19 changed/created .py files>   -> All checks passed!
uv run ruff format --check <the 8 created .py files>   -> 8 files already formatted
git status: no existing test file modified (only new files under tests/)
docker ps -a --filter name=lattice-: empty after every run
```

## What is not verified

- Install from a git URL or a git-repository index (refused by design; only directories and `file://`).
- A real third-party `module:Class` adapter against a real database: the hook is tested with a fake
  adapter (import, subclass check, `open_db`, the code-clone dispatch, no bytecode written), not with a
  driver and a server.
- The layer-A checker SDK item (documented only, as decided).
- `ask` and `draft` CLIs take no `--engine-file` (their `--database` choices are the registry's, so
  built-ins only unless the process registered more).
- Studio hub with several projects declaring engines; the web UI (none built).
- `lattice/perf/measure.py` still hard-codes `mysql` (outside this slice).
- Sandbox: Mach/XPC escapes, Linux, a deny-default profile (see the trust probe).
- M-P2 is n=1 on one app and one MariaDB version (11.8.9).

## Orchestrator verification (independent re-run, 2026-10-12)

Suite (tests/modules + perf + studio + mcp + test_cli + spec): 1197 passed, 5 skipped. Dogfood
gate: PASS, 393 cases. Restart drills A, B, C, F: pass. `module_mutants.py --dogfood`: control
PASS (91 tests), MM1-MM5 caught (by pytest). Streak (scratch clone at a0679e9, the orchestrator's
own runs): MySQL through the TOML descriptors PASS, 0 findings, layers ABCDX, 147.7 s;
`--database mariadb --engine-file mariadb.toml` PASS, 0 findings, 149.2 s (layer B skipped, as on
MySQL); no containers left.
