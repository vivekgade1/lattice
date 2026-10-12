# M-R: how large is the seam between Lattice's core and a database engine or language target

Date: 2026-10-12. Node M-R of the L0 tranche (spike 2, `docs/design/spikes/02-module-marketplace.md`).
Read-only git analysis on branch `next-phases` (HEAD c321aca). Nothing was run: no tests, no Docker, no network.
Exit criterion (from `docs/design/next-phases-plan.md`): a table of seams with line counts, each classed
manifest-able or SDK-needed, and a recommendation on SDK size.

## Question

When MySQL (and Postgres, ClickHouse) and Go were added, which places in Lattice's core had to name or register the
new engine or target, and how many of those places could have been data in a descriptor versus needing a code
interface? From that: what is the smallest descriptor plus SDK surface that lets a third engine be added with no
core edits?

## Method

Commands (all read-only), run in `/Users/vivekgade/Documents/lattice`:

```
git show --stat --format='%h %ad %s' --date=short e008353 a5268fe bbb6185 926f496 11a7032
git show --numstat --format= e008353            # per-file added/removed
git show e008353 -- lattice/spec/{__main__,check,race,invariants,process,services,fidelity,probes}.py
git show e008353:lattice/spec/db.py ; git show e008353:lattice/spec/dbserver.py   # then ast.parse for symbol spans
git show --format= -U0 e008353 -- <file>   # per-hunk +/- (script splitting on '@@')
git show e008353 --format= -U0 -- lattice | awk ... | grep -i 'postgres|mysql|clickhouse|sqlite'   # engine names added outside db.py/dbserver.py
git log --format='%h %ad %s' --date=short --follow -- lattice/spec/db.py lattice/spec/dbserver.py
git log -S'clickhouse' -- pyproject.toml lattice ; git show 5559e6a
git show 11a7032 / bbb6185 -- lattice/spec/{__main__,check,race,process}.py lattice/importer/transport.py docs/lattice-language-rules.md
git log --reverse -- lattice/gogen ; git merge-base 071b544 c751850 ; git log 273d87f..c751850
git diff --numstat 273d87f c751850 -- lattice tests ':!lattice/gogen' ':!tests/gogen' ':!lattice/tsgen' ':!tests/tsgen'
git show 3f536d2 ; git show aee31a6 817d314 743c7ee 5ada232 251f685 -- lattice/targets.py lattice/check/p8_target.py
git diff cfee694^2 cfee694 -- lattice/targets.py lattice/check/p8_target.py     # the role-sets gate was added in the merge
git show 609d350 -- lattice/cli.py ; git show --stat 6101474                    # CLI registration; removal of gogen
git grep -n -i -E 'clickhouse|postgres|mysql' HEAD -- lattice pyproject.toml    # what names engines at HEAD
git grep -n -E 'gogen|"go"' 6101474^ -- lattice ':!lattice/gogen'               # what names Go (before gogen was removed)
```

Unit of count: a **seam** is one (file, symbol) where a new engine or target must be named, registered, or branched on,
outside the engine's own adapter bodies, plus the engine-named registries that live inside the shared files
`db.py` / `dbserver.py`, plus the adapter bodies themselves (counted as SDK-needed). Line counts are lines added/removed
in the commit named, taken from `--numstat` or from per-hunk `-U0` counts; symbol spans are from `ast.parse` of the file
at that commit. "Class" is judged by what a new instance has to supply: a literal value (string, number, list, flag, SQL
text, template) is MANIFEST-ABLE (M); behaviour (a connection, a loop, a predicate, a checker) is SDK-NEEDED (S).

Corrections to the hints I was given:

- `a5268fe` is **not** an engine commit. It is a 3-line `AuthDriver(scheme='')` fix in `lattice/importer/transport.py`
  (+10 lines with its test). The HANDOFF table lists it beside e008353 but it does not touch the database layer.
- The `db` optional-dependency extra (`psycopg[binary]`, `pymysql`, `clickhouse-connect`) is commit `5559e6a`
  (pyproject.toml +7, uv.lock +227), made the same day as e008353 (17:01 vs 19:33).
- The plan `docs/superpowers/plans/2026-10-07-go-and-db-import.md` (7ab0942) put the engine abstraction in
  `lattice/importer/dbengine.py`; it landed as `lattice/spec/db.py` (+ `dbserver.py`). Same shape (seeding, clones,
  invariant SQL per dialect).

Three different things get called "Go" or "engine" in the history, and they are measured separately:

- Track DB: databases for the *spec checker* (the app under check runs on Postgres/MySQL/ClickHouse). Commit e008353.
- Track GR: Go as the language of the *app under check* (process runtime, layer A for Go). Commits 11a7032, bbb6185, 926f496.
- Track GG: Go as a *generation target* of Lattice programs (`lattice/gogen`, 3f536d2 and the go-target branch
  273d87f..c751850, merged by cfee694; removed 2026-10-12 by 6101474).

## Data

### What each commit is

| Commit | Date | What | Core files touched (added/removed) | Engine/target's own files |
|---|---|---|---|---|
| 5559e6a | 2026-10-09 | `db` extra | pyproject.toml +7; uv.lock +227 (generated) | none |
| e008353 | 2026-10-09 | Postgres, MySQL, ClickHouse **in one commit, together with the interface that abstracts SQLite** | `__main__.py` +21/-2, `check.py` +235/-120, `fidelity.py` +3/-7, `invariants.py` +75/-17, `probes.py` +4/-7, `process.py` +13/-1, `race.py` +187/-144, `services.py` +7/-1 = **+545/-299** | `db.py` +592, `dbserver.py` +542 (= 1,134); tests +1,075 |
| 11a7032 | 2026-10-08 | process runtime (Go toy app tests) | `transport.py` +3, `__main__.py` +31/-2, `check.py` +143/-10, `race.py` +43/-5 | `process.py` +355; tests +640 (Go toy app `goapp/`, `go_harness.py`, `test_process_runtime.py`) |
| bbb6185 | 2026-10-09 | gocheck, layer A for Go | `__main__.py` +15/-7, `check.py` +60/-7, `process.py` +2/-1, docs rules +2 | `lattice/gocheck/__init__.py` +487, `src/*.go` +6,975 (13 files, from the commit's stat); plus Go fixtures and tests |
| 926f496 | 2026-10-09 | gocheck precision | none outside `lattice/gocheck` and its tests | +1,522/-11 |
| 3f536d2 | 2026-09-28 | targets + gate pass P8 (first Go-target commit in core) | `targets.py` +55, `p8_target.py` +79, `catalog.py` +5, `check/__init__.py` +6/-3 | none yet |
| go-target branch (273d87f..c751850, 87 commits) | 2026-09-28..30 | Go generator, TS client, T2 IR, bench arm | see "one-time work" below | `lattice/gogen` (Python emitters and a Go runtime; 6101474 removed 88 files, 61,659 lines, tests included) |

Per-engine numbers inside e008353: the check.py/race.py/etc. changes are mostly **not** engine naming. They replace direct
`sqlite3` use with the `Database` interface (`open_db(...)`, `quote_ident`, `ph`, `query_dicts`, `insert`, `read_table`,
`where_sql`). `check.py` itself never names Postgres or MySQL in code (only in docstrings); it names `"sqlite"` as a
default and branches `dialect == "sqlite"` vs `_on_server`. The engine-name literals added outside `db.py`/`dbserver.py` in
e008353 are about 11 code lines (all in `invariants.py`, `race.py`, `__main__.py`; counted by hand from the awk listing).

### The seam table

IDs are used in the Counts and Recommendation sections. "Doc" marks a row that is documentation or a config comment only.

Track DB (engines for the spec checker). Commit e008353 unless noted.

| # | File | Symbol / site | Lines | Class | Why |
|---|---|---|---|---|---|
| 1 | `lattice/spec/__main__.py` | `_parser`: `--database` `choices=("sqlite","postgres","mysql","clickhouse")` (+ `--database-url`) | +12 | M | list of names |
| 2 | `lattice/spec/ask.py` | `ask` parser `--database choices=(...)` (commit bff46e8, later) | +3 | M | same literal tuple repeated |
| 3 | `lattice/spec/draft.py` | incremental parser `--database choices=(...)` (commit fc2f66b, later) | +2 | M | same literal tuple repeated |
| 4 | `lattice/studio/server.py` | studio.toml template comment `database = "sqlite" # sqlite \| postgres \| mysql \| clickhouse` (e2d74ec, later); the value is passed through unvalidated (lines 292-315) | +1 | M (Doc) | a comment |
| 5 | `lattice/spec/invariants.py` | `_Compiler.__init__` (+ `text`, `num`): identifier-quote ladder (`mysql` backtick, `postgres`/`clickhouse` double quote, else raise), true/false/unknown literals | +21/-2 | M | quote char, bool literals, float-cast template |
| 6 | `invariants.py` | `_BOOLS` dict, `_double_quote`, `_backtick` | +22/-1 | M | literals |
| 7 | `invariants.py` | `_sql_text`: backslash doubling for mysql, clickhouse | +5/-4 | M | boolean flag |
| 8 | `invariants.py` | `_Compiler` Div branch for clickhouse (`(toFloat64(a) / toFloat64(b))`) | +2 | M | an expression template |
| 9 | `lattice/spec/race.py` | `_Harness.__init__`: `clickhouse = self.dialect == "clickhouse"`, `not_applicable` list; plus the layer-X note text | +5, +7 | M | two capability flags (no unique constraints, no transactions) |
| 10 | `pyproject.toml` | `[project.optional-dependencies] db` (5559e6a) | +7 | M | list of pip requirements |
| 11 | `lattice/spec/db.py` | `DIALECTS`, `SERVER_DIALECTS` | 2 | M | names |
| 12 | `db.py` | `dialect_of`: URL scheme to engine (`postgres`/`postgresql`, `mysql`/`mariadb`, `clickhouse`/`ch`/`http`/`https`) | 14 | M | scheme aliases |
| 13 | `db.py` | `canonical_url`: per-engine URL pattern, default port | 17 | M | template |
| 14 | `db.py` | `url_for`: engine x driver table (go-pgx/go-pq/psycopg/libpq, go-mysql, go-clickhouse, sqlalchemy, jdbc: 9 distinct templates) | 46 | M | templates over {user,password,host,port,db} |
| 15 | `db.py` | `_ADAPTERS` dict, `open_db` | 5 + 10 | M | name to class entry |
| 16 | `db.py` | `PostgresDatabase` (psycopg 3) | 50 | S | connect, `_run`, error class; `tables()`/`columns()` SQL (17 lines) is data |
| 17 | `db.py` | `MysqlDatabase` (PyMySQL) | 66 | S | same; adds `quote_ident` override (data) |
| 18 | `db.py` | `ClickhouseDatabase` (clickhouse-connect, HTTP) | 75 | S | not DB-API; own `insert`, `READ_SETTINGS={"final":1}` |
| 19 | `lattice/spec/dbserver.py` | `IMAGES`, `PORTS`, `DATA_DIRS`, `ADMIN_DB`, `_admin_user` | 14 | M | image, port, tmpfs dir, admin db/user |
| 20 | `dbserver.py` | `_run_args`: per-engine env vars, server args, extra docker opts (`--ulimit`) | 20 | M | lists |
| 21 | `dbserver.py` | `DbServer.q`: identifier quoting for database names | 4 | M | reuses row 5's quote char |
| 22 | `dbserver.py` | `DbServer.drop`: `WITH (FORCE)` (postgres), `SYNC` (clickhouse) | 7 | M | statement template |
| 23 | `dbserver.py` | `DbServer.clone` postgres branch + `terminate` | about 16 | M | `CREATE DATABASE {dst} TEMPLATE {src}` plus a pre-statement |
| 24 | `dbserver.py` | `_clone_clickhouse` (per table `CREATE TABLE AS`, `INSERT ... SELECT`; refuses views/dictionaries) | 20 | S (borderline) | statement templates in a per-table loop; needs a loop construct |
| 25 | `dbserver.py` | `_clone_mysql` (`SHOW CREATE TABLE` result executed as DDL, generated-column filter, FK checks off, views and triggers replayed) | 49 | S | a procedure; a result becomes a statement |
| 26 | `tests/spec/conftest.py` | `pg`, `my`, `ch` fixtures, `any_server` param list | +80 | M (test) | per-engine fixtures |

Track GR (Go as the app under check: process runtime and gocheck).

| # | Commit | File | Symbol / site | Lines | Class | Why |
|---|---|---|---|---|---|---|
| 27 | 11a7032 | `lattice/importer/transport.py` | `AppRuntime.one_server: bool = False` (a field added to the existing runtime interface) | +3 | M | boolean value; the field is interface growth (one-time) |
| 28 | 11a7032, bbb6185 | `lattice/spec/__main__.py` | `_runtime()`: `ref == "process"`, then `ref in ("process","go")`; `--runtime` help | +31/-2, +15/-7 (also holds `--go-src`) | M | name to factory |
| 29 | 11a7032 | `lattice/spec/check.py` | `check()`: if no runtime and the factory is annotated `-> ProcessApp`, use `process_runtime()` | about +5 | M | already expressible as project config `runtime = "process"` |
| 30 | 11a7032 | `check.py` | `PYTHON_FRAMEWORKS` frozenset and `_layer_a` skip with a note | about +13 | M | framework name to "has a layer-A checker" |
| 31 | bbb6185 | `check.py` | `_layer_a_go` (calls `lattice.gocheck.run_gocheck`, maps `GO_CODES`), dispatch `runtime.framework == "go"`, `check(go_src=, policy=GoPolicy)` | +60/-7 | S | a static-checker adapter: source dir + policy in, findings out |
| 32 | bbb6185 | `docs/lattice-language-rules.md` | 2 rows in the R4 target-gate decision log (GO805 unavailable, GO804/GO806 not applicable) | +2 | M (Doc) | declared per-target gaps |

Track GG (Go as a generation target).

| # | Commit | File | Symbol / site | Lines | Class | Why |
|---|---|---|---|---|---|---|
| 33 | 3f536d2 | `lattice/targets.py` | `Target` dataclass, `_go()`, `target_named` (`if name == "go"`), `TARGETS` | +55 | M | name + capability flags (`natives`, `tabular`; later `role_sets`, `nested_responses`, `error_styles`) |
| 34 | aee31a6, 817d314, 743c7ee, f1b8f66 | `targets.py` | `GO_T2_CALLS`, `GO_T2_TYPES` growth | +62/-6 | M | lists of call and type names the emitter implements |
| 35 | 3f536d2, cfee694, 5ada232, 251f685 | `lattice/check/p8_target.py` | flag-driven gates L890/L891/L892 and one gate pass per language feature: role sets (+36/-1, in the merge), nested responses (+29/-1), error styles (+26/-1), generic part +79 | +170/-3 | M | code written once, driven by a boolean on `Target` |
| 36 | 817d314, 743c7ee, aee31a6 | `p8_target.py` | Go-specific predicates: `from lattice.gogen.regex import regex_problem`, `from lattice.gogen.types import is_container, kinds`, the strftime-directive set Go implements | within +115/-5, +40/-1, +54/-3 (not all of it is Go-specific; not separated) | S | core checker imports the target package; the target supplies predicates |
| 37 | 609d350 | `lattice/cli.py` | `COMMANDS` tuple, usage text, `elif cmd == "gogen"` dispatch to `lattice.gogen.__main__.main` | 3 sites, about 6 lines | M | command name to module:main |
| 38 | go-target, bench | `lattice/llm/api_prompt.py`, `lattice/llm/client.py` | arm-G prompt (about 480 lines), `extract_go_code` (fence tags `go`, `golang`) | +480, +18 | M | prompt text and fence tags (benchmark only) |
| 39 | go-target | `docs/lattice-language-rules.md` | target-gate rows for Go | +121 for the whole branch (not split by target) | M (Doc) | declared per-target gaps |
| 40 | go-target | `tests/semantics_v11/gorun.py`, `test_go.py`, `go` pytest marker in `tests/conftest.py` | build and run a program on Go for the differential suite | +515, +68, +24 (the marker; shown as removed in 6101474) | S (test) | a "run this program on the target" adapter |

### One-time work that the second target or engine forced into the core (not per-instance seams)

These are interface generalisations: they were paid once and a third instance would not pay them again, but a descriptor
cannot remove them (the interface has to exist before a manifest can point at it).

| Commit | What | Lines |
|---|---|---|
| e008353 | `Database` interface and `open_db` replace direct `sqlite3` in `check.py` (`release_db`, `db_dialect`, `_on_server`, `_run_layers`, `_read_tables`, `_insert_given`, `_where`, `_plain`, `_same_time`), `race.py` (`_race_on`, `read`), `fidelity.py`, `probes.py`, `services.py` typing | check.py +235/-120, race.py +187/-144, fidelity/probes/services/process about +30/-20 |
| 11a7032 | layer C through a runtime's transport (`_TransportProbe`, `InvariantMonitor.watch`), one-server race mode, process runtime | check.py +143/-10, race.py +43/-5, process.py +355 |
| go-target branch | language-neutral T2 IR (`lattice/t2ir`: nodes 399, lower 859, dump 174), Python emitter from the IR (`t2/emit_py.py` +626), RE2-subset check (`check/re2.py` +69, L926), `t2/support.py` +312, `parse.py` +14 | about +2,500 in `lattice/` outside `gogen`/`tsgen` |

### What still names or hard-wires things at HEAD (for the descriptor's edges)

- `lattice/importer/*` (`mine.py`, `trace.py`, `verify.py`, `suite.py`, `outside.py`, `rules.py`) still import `sqlite3`
  directly (counts from `git grep -c sqlite3 HEAD`: 7, 6, 3, 2, 3, 2). Layers B and R are skipped on server engines by
  design (`check.py` `_on_server` note). Only `lattice/spec` is behind the `Database` interface.
- `lattice/api/backend.py` has a separate, larger `Dialect` Protocol (about 30 methods plus `Capabilities`, `DIALECTS`,
  `dialect_for`) for the SQL the Lattice runtime and generators emit. Only `SqliteDialect` exists; no commit adds a
  second implementation. Postgres/MySQL/ClickHouse in e008353 are **not** instances of it.
- `lattice/mcp/tools.py:250` `_CODE_EXT` is an already multi-language extension list (11 extensions, added in c5ae3fb);
  a new source language needs an entry there if its extension is missing.
- At HEAD `lattice/check/p8_target.py` still imports `lattice.gogen.regex` and `lattice.gogen.types`; commit 6101474 kept
  those two modules for that reason ("lattice.check.p8_target uses for the Go-target rules"), while removing the rest of
  `lattice/gogen` (88 files, +55/-61,659). The core therefore depends on a file in the removed target's package.

## Counts

Rows 1-25 (Track DB code and config), 27-39 (Track GR and GG code, config and docs). Test rows 26 and 40 are counted
separately.

| Track | Rows | MANIFEST-ABLE | SDK-NEEDED |
|---|---|---|---|
| DB (1-25) | 25 | 20 | 5 (rows 16, 17, 18, 24, 25; row 24 is borderline) |
| GR (27-32) | 6 | 5 | 1 (row 31) |
| GG (33-39) | 7 | 6 | 1 (row 36) |
| **Total, product code and docs** | **38** | **31** | **7** |
| Tests (26, 40) | 2 | 1 | 1 |

- 3 of the 31 manifest-able rows are documentation or a comment only (rows 4, 32, 39). Excluding them: 28 M, 7 S.
- If the borderline row 24 (ClickHouse clone) is counted manifest-able: 32 M / 6 S.
- Row 5 to 8 (the invariant compiler) are four rows but one idea: `lattice.spec.invariants` is a data record (quote
  char, two bool literals, backslash flag, float-cast template, division template) written as an if-ladder.
- Track DB by lines: manifest-able rows 1-3, 5-15, 19-23 total about 234 lines; SDK-needed rows 16-18, 24, 25 total 260
  lines, of which 51 are the `tables()`/`columns()` SQL strings in rows 16-18 (data). So code-bearing SDK lines are about 209.
- Same literal in more than one place: the engine-name tuple appears in 3 CLI parsers (rows 1-3) and 1 config comment (row 4).
  A fourth engine means editing 3 code files for the names alone in today's tree.
- Per-engine cost visible from the structure: a hypothetical 4th server engine today would touch rows 1-3 (3 files),
  5-8 (1 file, 4 spots), 9 (if it lacks unique constraints/transactions), 10, 11-15 (db.py: 5 spots), 16-18 analogue
  (1 new class), 19-23 (dbserver.py: 5 spots), 24/25 analogue (1 clone method), and a test fixture: about 9 files and
  about 20 spots, none of them in `check.py`, `fidelity.py`, `probes.py`, `process.py`, `services.py`.

## Recommendation

Smallest descriptor plus SDK that lets a third engine in with no core edits. Each item cites its evidence row.

### Descriptor (data only; the 31 manifest-able rows)

One file per engine, kind `engine`, role `check-database`:

```toml
name = "mysql"
schemes = ["mysql", "mariadb"]                      # row 12
default_port = 3306                                 # row 13 (+ row 19 PORTS)
url = "mysql://{user}:{password}@{host}:{port}/{db}"  # row 13
[drivers]                                           # row 14: 3 observed driver names only
go-mysql   = "{auth}tcp({hostport})/{db}?parseTime=true"
sqlalchemy = "mysql+pymysql://{rest}"
jdbc       = "jdbc:mysql://{hostport}/{db}?user={user}&password={password}"
[sql]                                               # rows 5-8, 16-18, 21-22
ident_quote = "`"          # placeholder = "%s"; true = "TRUE"; false = "FALSE"
backslash_escapes = true
float_cast = "{x}"         # clickhouse: "toFloat64({x})"
divide = "(({a}) * 1.0 / ({b}))"     # clickhouse: "({a} / {b})"
list_tables  = "SELECT table_name FROM information_schema.tables WHERE ..."
list_columns = "SELECT column_name FROM information_schema.columns WHERE ... = %s"
drop_database = "DROP DATABASE IF EXISTS {name}"
[capabilities]                                      # row 9
unique_constraints = true
transactions = true
[server]                                            # rows 19-20
image = "mysql:8.4"
container_port = 3306
data_dir = "/var/lib/mysql"
admin_user = "root"
admin_db = "mysql"
env  = { MYSQL_ROOT_PASSWORD = "{password}" }
args = ["--innodb-flush-log-at-trx-commit=0", "--skip-log-bin"]
ready_sql = "SELECT 1"                              # wait_ready already issues this for every engine
[clone]                                             # row 23 only
strategy = "template"      # "template" = one statement (+ optional pre-statement); anything else = "code"
[python]
requires = ["pymysql>=1.1"]                         # row 10
adapter = "lattice_mysql:MysqlDatabase"             # rows 15-18
```

Evidence for the shape: every field is today a dict entry, tuple member or string literal in `db.py`, `dbserver.py`,
`invariants.py`, `race.py` (rows 5-23); the three engines differ only in the values.

### SDK surface (code; the 7 SDK-needed rows)

Three things, in this order of evidence:

1. **Adapter**: `connect(url_parts) -> connection`, `run(sql, params) -> (columns, rows)`, `execute(sql, params)`,
   `close()`, `error_types`. This is exactly the abstract surface of the existing `Database` base class
   (`_run`, `execute`, `close` plus the data `tables`/`columns`/`quote_ident`). Evidence: rows 16 and 17 differ only in
   the connect call, the exception class and how the column name is read (`d.name` vs `d[0]`); row 18 differs by
   not being DB-API (its own `insert`, `READ_SETTINGS`). Optional override: `bulk_insert`.
2. **Clone** (optional): `clone(server, template, name)`. Rows 24 and 25. Postgres (row 23) needs only the descriptor.
3. **Layer-A checker** (language targets only): `run(src_dir, spec) -> findings` plus `GATED` note. Row 31
   (`_layer_a_go` is the only code the check core gained for Go). Process runtime needs no SDK: `framework` is already a
   free string on `process_runtime(...)` (row 27 is the only core edit it needed, a boolean).

For generation targets the descriptor is the existing `Target` dataclass (rows 33-35: flags and name lists, no code) and
the SDK is the target's predicates that p8 calls (row 36: `regex_problem`, supported strftime directives, container-`str`
rule). That is two small callables, today reached by a direct import.

### What I would NOT include

- **The Lattice-runtime SQL `Dialect` Protocol** (`lattice/api/backend.py`, about 30 methods). One implementation, no
  history of a second; e008353 did not touch it. A descriptor for "engine as a compile backend" cannot be sized from
  this history. Keep that out until a second `Dialect` exists.
- **A step language for clone** (loops over tables, templated statements). Rows 24 and 25 show a loop plus "execute a
  query result as a statement"; a manifest that can express them is a small programming language. Keep clone as an SDK method.
- **Value-normalisation hooks** (`_plain`, `_same_time`: Decimal, UUID, datetime). They are engine-neutral helpers in
  `check.py`, not engine-named. A fourth engine returning new driver types would need a hook; there is no evidence yet.
- **Per-engine layer lists.** Layers B and R are skipped on every server engine for one shared reason (`_on_server`);
  that is a property of the harness being SQLite-only, not of an engine.
- **More driver URL forms than the three observed** (go, sqlalchemy, jdbc) and any per-driver code.
- **Anything about trust, hashing, index or sandbox.** Out of scope for M-R: nothing in this history bears on it.
- **Auto-selection by annotation** (row 29) and **bench prompts** (row 38): project config and a separate guide file
  already cover them; they are not needed for engine or target registration.
- **A descriptor for the language-neutral IR work** (T2 IR, RE2 subset, 64-bit Int). That was the price of making a
  second target possible and is a language-core design, not a module seam.

### What the descriptor alone buys, and what it does not

- It removes rows 1-15, 19-23 from the "edit core" list: 3 CLI parsers, the invariant compiler's if-ladders, 5 spots in
  `db.py`, 5 in `dbserver.py`, `pyproject.toml`. That is 20 of the 25 Track DB rows.
- The core has to be refactored once so those places *read* the descriptor (rows 1-3, 5-15, 19-23 become lookups). That
  refactor is new work; this history does not contain it, so its size is not measured here.
- It does not remove the adapter (rows 16-18) or the clone procedures (rows 24, 25). A third engine that is DB-API and
  whose clone is a single statement needs descriptor only plus a `connect`; one that is not (ClickHouse) needs code.

## What could not be determined

- **The marginal cost of one engine.** All three engines landed in one commit that also introduced the interface; no
  commit adds a single engine to an existing interface. The per-engine cost above is inferred from structure, not measured.
- **Which lines of `p8_target.py` (+115, +40, +54) are Go-specific.** I did not separate them; row 36 gives the commit totals
  and the two import lines that make the dependency explicit.
- **Whether a `Dialect` (backend) descriptor is feasible**: only SQLite exists.
- **Line counts of documentation rows** in `docs/lattice-language-rules.md`: the merge shows +121 for the whole branch,
  not per target (row 39); `lattice-reference.md` and HANDOFF table rows for engines were not counted.
- **Hunk-level line counts for `check.py` rows 29 and 30** are approximate ("about"), taken by reading the diff, not from
  per-hunk script output.
- **The gocheck and gogen internals** (`lattice/gocheck/src/*.go`, `lattice/gogen/*`) were not read beyond file names,
  sizes and the module docstring of `lattice/gocheck/__init__.py`; they are the target's own files, outside the seam count.
- **Whether tests pass** or any engine works: nothing was run.
- **Streak** (Go + MySQL) was not examined: its repo is outside this node's scope, so whether a real project exercises
  anything this table misses is unknown.
