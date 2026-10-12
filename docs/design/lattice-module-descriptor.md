# Lattice module descriptor and SDK surface (spike 2, descriptor-only slice)

Status: built for the `engine` kind (`lattice/spec/engines.py`, `lattice/modules/`), 2026-10-12.
Evidence: `docs/results/2026-10-12-m-r-engine-seams.md` (38 seams, 31 manifest-able, 7 SDK-needed);
results: `docs/results/2026-10-12-spike2-results.md`. The plan this slices:
`docs/design/spikes/02-module-marketplace.md`.

## 1. The engine descriptor (data; built)

One TOML file per server engine of the spec checker. The built-ins are
`lattice/spec/engines/{postgres,mysql,clickhouse}.toml`; SQLite is built in and has no descriptor. A
project adds one with `studio.toml` `[engines] paths = [...]`, `lattice spec check --engine-file FILE`,
or an installed module of kind `engine`. Example without code: `docs/examples/engines/mariadb.toml`.

Every field. `?` = optional (default shown). Templates use `{name}` placeholders, substituted in one
pass; an unknown placeholder is a validation error.

| Field | Type | Meaning (and the M-R seam row it replaces) |
|---|---|---|
| `name` | string `[a-z][a-z0-9_-]*`, not `sqlite` | the engine, and its `--database` value (rows 1-3, 11) |
| `title`? | string (= name) | display name in notes, e.g. layer X's "not applicable on ClickHouse" (row 9) |
| `aliases`? | list (= []) | more `--database` spellings (`postgres`: `postgresql`) |
| `url_schemes` | non-empty list | URL schemes that resolve to this engine (row 12); an added engine may take over a built-in engine's *alias* scheme (not its first) |
| `url_template` | string, `{user} {password} {host} {port} {db}` | the canonical URL (row 13); user and password are percent-encoded |
| `default_port` | 1-65535 | the port an adapter uses when a URL has none (row 13) |
| `adapter` | `postgres` \| `mysql` \| `clickhouse` \| `module:Class` | how connections are opened: a built-in adapter (no code) or a `lattice.spec.db.Database` subclass (rows 15-18) |
| `requires`? | list (= []) | pip requirements; shown, never installed (row 10) |
| `clone` | `"template"` \| `"code"` | how a scenario database is cloned from the seed (rows 23-25) |
| `[drivers]`? | table | `url_for(ref, driver)` forms (row 14), each a template string or `{template, query_auth?, query_fixed?, query_url?}`: the query string is user/password (`query_auth`), then fixed parameters, then the URL's own query (`query_url`). Template placeholders: `{url} {rest} {auth} {raw_auth} {host} {port} {hostport} {db} {user} {password}` (`auth` percent-encoded `user:pw@`, `raw_auth` as typed). `canonical` is always the URL itself |
| `[sql] identifier_quote` | `"` or `` ` `` | quoting of identifiers in invariant SQL and database names (rows 5, 21) |
| `[sql] placeholder`? | string (= `%s`) | the driver's parameter marker |
| `[sql] true_literal`, `false_literal` | string | boolean literals in invariant SQL (rows 5-6) |
| `[sql] backslash_escapes` | bool | a backslash in a text literal is doubled (row 7) |
| `[sql] float_cast`? | `{x}` template (= `{x}`) | a number as a float where Decimal and Float do not mix (ClickHouse `toFloat64({x})`) (row 8) |
| `[sql] divide`? | `{a} {b}` template (= `(({a}) * 1.0 / ({b}))`) | division; `a`, `b` already `float_cast` (row 8) |
| `[sql] list_tables`, `list_columns` | SQL | the adapter's `tables()` / `columns(table)` (one parameter: the table) (rows 16-18, data part) |
| `[sql] drop_database` | `{name}` template | drop a scenario database (row 22) |
| `[sql] terminate_sessions`? | SQL, one parameter (= "") | end other sessions on a database before it is cloned or after seeding (Postgres) (row 23) |
| `[sql] clone_template`? | `{name} {template}` template | the clone statement when `clone = "template"` (row 23) |
| `[capabilities]` | exactly `unique_constraints`, `transactions` (bools) | an engine with neither cannot enforce an upper count bound: layer X reports those bounds not applicable (row 9). Any other flag is an error |
| `[server] image` | string | the throwaway container's image (row 19) |
| `[server] port`? | int (= default_port) | the container port published on 127.0.0.1 (row 19) |
| `[server] data_dir` | absolute path | mounted as a tmpfs (row 19) |
| `[server] admin_user` | string, `{user}` allowed | the account the checker administers with (`{user}`: its own, `lattice`) (row 19) |
| `[server] admin_db` | string | the database the admin connection opens (row 19) |
| `[server] env`? | table of `{user} {password}` templates | the container's environment (row 20) |
| `[server] args`? | list | the server's own arguments (row 20) |
| `[server] docker_args`? | list | more `docker run` options (ClickHouse `--ulimit`) (row 20) |
| `[server] ready_sql`? | SQL (= `SELECT 1`) | the readiness probe |

Validation is exact (`lattice.spec.engines.EngineError`, messages pinned in
`tests/spec/test_engines.py`): unknown fields, missing fields, wrong types, unknown placeholders,
unknown capability flags, a `clone = "code"` on a built-in adapter that has no clone procedure, a
`clone = "template"` without `clone_template`, and registry clashes (a name, alias or scheme another
engine has; a built-in engine's name).

Seams still outside the descriptor (by design, see M-R "What I would NOT include"): the Lattice-runtime
`Dialect` protocol (`lattice/api/backend.py`), value-normalisation hooks, per-engine layer lists.

## 2. The module manifest (data; built)

A module is a directory with `lattice-module.toml`:

| Field | Meaning |
|---|---|
| `name` | `[a-z][a-z0-9_-]*` |
| `version` | non-empty string |
| `kind` | `engine` or `custom` (the plan's `resource`, `compute`, `framework` kinds are not built: no instance yet) |
| `description`? | text |
| `provides`? | list; an engine module defaults to `engine:<engine name>` |
| `requires`? | pip requirements; an engine module defaults to its descriptor's |
| `descriptor` | `kind = "engine"`: a descriptor file inside the module, **or** |
| `[engine]` | the descriptor's fields inline |

`lattice module install SOURCE [--index DIR] [--yes]`, `list`, `info NAME`, `uninstall NAME`
(`lattice/modules/`): install prints the manifest and the tree's sha256 and installs only after
`--yes` or an interactive `y`; it copies into `<project>/.lattice/modules/<name>/` and writes
`<project>/lattice.modules.lock` (`name`, `version`, `kind`, `sha256`, `source`). Every load re-hashes.
An index is a directory of `<name>.toml` entries (`name`, `version`, `source`, optional `sha256` pin).
Git sources are parsed and refused (not implemented).

## 3. The SDK surface (code; documented, only the adapter hook built)

M-R found 7 SDK-needed seams; they reduce to three code items. Only (1) is implemented: an engine
descriptor's `adapter = "module:Class"` is imported (the descriptor's directory first on `sys.path`,
bytecode writing off so an installed module's tree stays byte-identical) and must be a
`lattice.spec.db.Database` subclass.

### 3.1 Adapter (built as a hook): a `lattice.spec.db.Database` subclass

```python
class MyDatabase(lattice.spec.db.Database):
    dialect: str = "mydb"            # overwritten with the engine's name by open_db
    ph: str = "%s"                   # the parameter placeholder
    url: str                         # the canonical URL it was opened with

    def __init__(self, url: str) -> None: ...          # connect; raise DatabaseError on failure
    def _run(self, sql: str, params: Sequence[Any] | None) -> tuple[list[str], list[tuple]]: ...
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...  # autocommit
    def close(self) -> None: ...
    # optional overrides (defaults are built on _run / execute and the descriptor):
    def quote_ident(self, name: str) -> str: ...       # default: double quotes
    def insert(self, table: str, rows: Iterable[Mapping[str, Any]]) -> None: ...  # bulk insert
    def tables(self) -> list[str]: ...                 # default: [sql] list_tables
    def columns(self, table: str) -> list[str]: ...    # default: [sql] list_columns
```
Errors from the driver are raised as `lattice.spec.db.DatabaseError` (text only). Evidence: rows 16-18
differ only in the connect call, the exception class and how a column name is read; ClickHouse adds its
own `insert` and read settings.

### 3.2 Clone (optional; documented, the built-in procedures stay in core)

```python
    @staticmethod
    def clone(server: lattice.spec.dbserver.DbServer, template: str, name: str) -> None:
        """Create database `name` holding `template`'s tables and rows, through
        server.admin() (a Database on the admin URL); drop `name` again if it fails."""
```
Called when the descriptor says `clone = "code"` and its adapter is `module:Class` (the dispatch is
built; no third-party clone exists yet). The built-in `mysql` (SHOW CREATE TABLE replayed, views and
triggers) and `clickhouse` (CREATE TABLE AS, INSERT SELECT) procedures are reached by adapter name.
Evidence: rows 24, 25. A statement language for clone was rejected (M-R: it would be a small
programming language).

### 3.3 Layer-A checker (language targets; documented, NOT built)

```python
def run(src_dir: Path, spec: lattice.spec.model.Spec) -> list[lattice.spec.check.Finding]:
    """Static findings for an app's source tree (layer A), each with a rule code and a target."""

GATED: str = "..."   # the note layer A prints when this checker cannot run (tool missing)
```
A descriptor field (`layer_a = "module:run"`) would name it. Evidence: row 31 (`_layer_a_go` is the only
code the check core gained for Go). Not built: the slice is engines only, and there is one instance
(gocheck).

## 4. Trust (measured, not built)

See `docs/results/2026-10-12-spike2-trust-probe.md`: whether a module's provision step can run with no
network and only declared paths writable on macOS (`sandbox-exec`, `docker run --network none`). Nothing
in core sandboxes module code today: an adapter is imported into the checker process. The install
shows the manifest (and says CODE when the adapter is not built in) and pins the tree by hash.
