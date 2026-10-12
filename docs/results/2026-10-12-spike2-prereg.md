# Pre-registration: spike 2, module marketplace (descriptor-only slice): M-P1, M-P2, M-L1

Written 2026-10-12, before any probe below was run and before the Streak baseline was measured. Design:
`docs/design/spikes/02-module-marketplace.md`; evidence: `docs/results/2026-10-12-m-r-engine-seams.md`
(38 seams, 31 manifest-able, 7 SDK-needed; its Recommendation is the descriptor followed here). Defaults
in force (`docs/design/next-phases-plan.md`): descriptor-only slice; the index is a directory in this repo
for now; CLI backend only; agents never commit. Results go to `docs/results/2026-10-12-spike2-results.md`,
blind (the criteria below) and post hoc separately; a failed prediction is reported as a finding.

## What is under test

- `lattice/spec/engines.py` (new): `EngineDescriptor` read from TOML, validated with exact error messages,
  and a registry. The three server engines ship as `lattice/spec/engines/{postgres,mysql,clickhouse}.toml`;
  `db.py`, `dbserver.py`, `invariants.py`, `race.py` and the three `--database` parsers read the registry
  instead of hard-coded dicts, tuples and if-ladders. SQLite stays built in (not a descriptor).
- A project adds an engine with no core edit: `studio.toml` `[engines] paths = [...]` and
  `lattice spec check --engine-file FILE` (repeatable). An `adapter` naming a built-in adapter (`mysql`,
  `postgres`, `clickhouse`) needs no code; `module:Class` loads a `Database` subclass.
- `lattice module install|list|info|uninstall` (local path, `file://` URL, `--index DIR`), the lock file
  `lattice.modules.lock` with a sha256 per module tree, re-hashed on every load.
- `GET /api/engines` (Studio API v1.14) and mock parity.

## Setup shared by M-P1(c) and M-P2

- Streak: `git clone --no-hardlinks /Users/vivekgade/Documents/streak <scratch>/sp2/streak-a`,
  `git checkout a0679e9`. Nothing runs in `/Users/vivekgade/Documents/streak`.
- Environment: `UV_NO_SYNC=1` (so `uv run` never touches Streak's venv; added to the brief's layout, since
  the clone's `../lattice` path source does not exist from scratch),
  `UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/streak/.venv`,
  `PYTHONPATH=<clone>:<lattice tree>`; `<lattice tree>` is `/Users/vivekgade/Documents/lattice` (baseline,
  unrefactored, HEAD cc77f73 like the worktree's base) or `/Users/vivekgade/Documents/lattice-np2`.
  `LATTICE_CONTAINER_OWNER=lattice-mod-spike2` labels the containers the checker starts (their names are
  the checker's own `lattice-mysql-*` / `lattice-mariadb-*`: `check.sh` gives no way to set a prefix); after
  every run `docker ps -a --filter label=lattice.owner=lattice-mod-spike2` must be empty.
- Command: `./check.sh` unchanged (MySQL, layers ABCDX, `--x-repeats 5`), timed with `/usr/bin/time -p`
  around the whole script (it includes `go build` and the container start).
- Wall time: two baseline runs (B1 before the refactor is written, B2 right before the refactored runs) and
  two refactored runs (R1, R2) back to back with B2 on an otherwise idle test load (no pytest running in
  parallel). Criterion: mean(R) within 15% of mean(B). Every run is reported, not only the means.

## M-P1: equivalence (pass criterion of the spike)

(a) `uv run pytest tests/perf tests/studio tests/mcp tests/test_cli.py tests/spec -q
--ignore=tests/spec/test_db.py --ignore=tests/spec/test_multidb.py -p no:cacheprovider` passes with NO edit
to an existing test file: at least the baseline's 1106 passed, 5 skipped, plus the new tests, 0 failed.
Also the Docker-free tests of `tests/spec/test_db.py` (URLs, the SQLite adapter, the SQLite invariant SQL,
`_same`) run unchanged and pass.
(b) `(cd dogfood/studio && ./check.sh)`: PASS (baseline 387 cases, plus the new `/api/engines` cases).
(c) Streak at a0679e9: verdict PASS, 0 findings, 156 scenarios, on both R runs; wall time as above.
**Prediction: identical** (same verdict, findings and scenario count; time within noise).

## M-P2: a third engine with no core edit (n=1; a finding either way, not a pass criterion)

`./check.sh --database mariadb --engine-file <path>/mariadb.toml` on the same clone and environment (the
later `--database` wins over `check.sh`'s own `--database mysql` in argparse). The descriptor is
`docs/examples/engines/mariadb.toml`: image `mariadb:11`, adapter `mysql` (no code), clone `code` (the
mysql adapter's `SHOW CREATE TABLE` clone), server args as for MySQL except
`--innodb-doublewrite=OFF` (its meaning differs across MariaDB versions; left out so a start failure is not
self-inflicted), `MARIADB_ROOT_PASSWORD` instead of `MYSQL_ROOT_PASSWORD`.

Decision fixed before the run: the descriptor's `url_template` is `mysql://...` (MariaDB speaks the MySQL
wire protocol) while its `url_schemes` is `["mariadb"]`. Reason: Streak's harness refuses any reference not
starting `mysql://` (`harness._require_mysql`) and hands the app `url_for(url, "go-mysql")`; a `mariadb://`
canonical URL would make the outcome a known harness refusal rather than a test of GORM / go-sql-driver
against MariaDB.

**Prediction:** the container starts and the check runs to a verdict; the verdict is UNKNOWN. Reported: PASS,
or the concrete list of findings / errors that differ from the MySQL run.

## M-L1: local install with pinning (pass: 5 of 5 steps)

On a scratch project with a module directory holding `lattice-module.toml` (kind `engine`,
`descriptor = "engine.toml"`):

1. `lattice module install <dir>` with no `--yes` and stdin not a terminal: the manifest is printed, nothing
   is written (no `.lattice/modules/<name>`, no lock), exit code **2**.
2. `lattice module install <dir> --yes`: exit 0; `.lattice/modules/<name>/` holds a copy; `lattice.modules.lock`
   has name, version, sha256 of the tree and the source path.
3. `lattice module list`: shows the module with status `ok`; `info NAME` prints its manifest and lock entry.
4. One byte of a file in the installed tree changed: loading the project's engines (the checker with
   `--database <engine>`, and `lattice module list`) refuses it: the check exits 2 naming the module and the
   hash mismatch, `list` shows status `changed`.
5. `lattice module uninstall NAME`: the directory and the lock entry are gone; `list` no longer shows it.

## Mutants (`dogfood/module_mutants.py`), predictions

| Mutant | Predicted to be caught by |
|---|---|
| registry answers port 3307 for mysql | `tests/spec/test_engines.py` (built-ins equal the old literals) |
| the tamper check on load is skipped | `tests/modules/test_modules.py` (tamper test) |
| install writes without confirmation | `tests/modules/test_modules.py` (refuse without --yes) |
| validation accepts an unknown capability flag | `tests/spec/test_engines.py` (exact error message) |
| the lock hash ignores file contents | `tests/modules/test_modules.py` (tamper / hash tests) |

Predicted: 5 of 5 killed, each by the file named; the dogfood gate is not predicted to catch any (it does not
install modules).
