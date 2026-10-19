# HARNESS_API_DOTNET: how your service is built, started, seeded, logged into and checked

This file states every fact about the environment that will build, start and check your service. That environment is
not on your machine. Three Python names it provides (`AuthDriver` from `lattice.importer.transport`, `ProcessApp` and
`process_runtime` from `lattice.spec.process`, and `ServiceCmd` from `lattice.spec`) are imported by the harness file
you write; you cannot import them, so `smoke.py` (section 12) stands in for them with the same fields and the same
behaviour. Nothing here describes your service's design: that is yours.

Replace `<x>` everywhere with the name you give the service: lowercase letters and digits, starting with a letter. It
is also the name of the startup project and of its assembly.

## 1. Layout of what you hand back

Everything you hand back is under `OUT`, the directory your brief names:

```
OUT/tests/spec/<x>/              the .NET project directory (the "module"): <x>.csproj (the startup project),
                                 packages.lock.json next to every project file, your sources, studio.toml, truth.json
OUT/tests/spec/<x>_harness.py    the harness (section 6)
```

Create no other file under `OUT/tests/`; in particular create no `__init__.py` (section 11 says why none is needed).
The module directory holds no `bin/` and no `obj/` directory when you hand it back (a restore or a build run inside it
creates them: delete them, or build in a copy). Everything the build needs is inside the module directory: the
environment builds a copy of it, so a path that leaves it does not resolve. A project file compiles every `*.cs` file
below its own directory, including those of a project below it; keep each project's sources in a tree of their own.
If your startup project file is not `<x>.csproj` at the module root, change the `PROJECT` and `ASSEMBLY` constants of
the harness (section 6) and nothing else.

## 2. Toolchain, packages and the build

| fact | value |
|---|---|
| SDK | `dotnet --version` prints `10.0.401` (Homebrew, arm64). No global tool is installed (`dotnet tool list -g` is empty: no `dotnet-ef`), no workload is needed |
| runtimes | `Microsoft.NETCore.App 10.0.12` and `Microsoft.AspNetCore.App 10.0.12` only: no 8.0 or 9.0 runtime. Every project targets exactly `net10.0` |
| where packages come from | nuget.org (the machine's default and only source); the machine has network access to it; packages are cached in `~/.nuget/packages`. A `NuGet.config` in the module is not part of the contract |
| lock files | every project file sets `<RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>`, and its `packages.lock.json` is committed next to it. Create it with `dotnet restore`; after changing a package reference refresh it with `dotnet restore --force-evaluate` |
| how it is built | `dotnet build <startup project> -c Release -p:RestoreLockedMode=true -o <output dir>` run on a copy of the module without `bin/` and `obj/`: it must succeed with 0 errors, restoring exactly the locked versions, and leave `packages.lock.json` byte-identical |
| a switch that does not exist | `dotnet build --locked-mode` is rejected by SDK 10.0.401 (`MSB1001: Unknown switch`); the property above is the form that works (`dotnet restore --locked-mode` is also valid). A missing `packages.lock.json` is NOT an error in that mode (restore writes one), so commit it |
| not allowed | a `NuGet.config` with other sources, a local package feed, binaries committed to the module, a restore that needs a global tool |

Provider packages, each restored and run on this machine (net10.0, SDK 10.0.401):

| use | package references | what they restore |
|---|---|---|
| EF Core over SQLite | `Microsoft.EntityFrameworkCore.Sqlite` 10.0.13 | EF Core 10.0.13, Microsoft.Data.Sqlite, SQLitePCLRaw 2.1.13; 0 warnings |
| MySQL, Pomelo | `Pomelo.EntityFrameworkCore.MySql` 9.0.0 (the newest; there is no EF Core 10 build) | `Microsoft.EntityFrameworkCore` 9.0.0 and MySqlConnector 2.4.0. It requires `Microsoft.EntityFrameworkCore.Relational` `>= 9.0.0 && <= 9.0.999`: any EF Core 10 package in the same project fails the restore with `NU1107` |
| MySQL, Oracle | `MySql.EntityFrameworkCore` 10.0.9 (the EF Core 10 option) | `Microsoft.EntityFrameworkCore` 10.0.9 and `MySql.Data` 26.7.0 |
| SQLite next to Pomelo | `Microsoft.EntityFrameworkCore.Sqlite` 9.0.0 or 9.0.21 (EF Core 9) | 9.0.0 prints warning `NU1903` (a known vulnerability in the native SQLitePCLRaw 2.1.10 library); 9.0.21 prints none. Both ran |
| SQLite next to Oracle | `Microsoft.EntityFrameworkCore.Sqlite` 10.0.9 or 10.0.13 | 10.0.9 prints `NU1903` (SQLitePCLRaw 2.1.11); 10.0.13 prints none. Both ran |

A warning is not an error: nothing in the environment promotes warnings to errors. The lowest package version a restore
accepts is the one in the lock file; a higher version of a package that another package only lower-bounds is allowed
(Oracle's 10.0.9 ran on EF Core 10.0.13, Pomelo's 9.0.0 on EF Core 9.0.21).

To keep every file the toolchain writes inside your scratch directory, export before any command (`SCRATCH` is the
scratch directory your brief names; a cold package cache downloads about 100 MB and works):

```sh
export SCRATCH=<your scratch directory>
export PATH=/opt/homebrew/bin:$PATH
export DOTNET_ROOT=/opt/homebrew/opt/dotnet/libexec DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
export HOME=$SCRATCH/home DOTNET_CLI_HOME=$SCRATCH/home NUGET_PACKAGES=$SCRATCH/home/.nuget/packages TMPDIR=$SCRATCH/.tmp
mkdir -p "$HOME" "$TMPDIR"
```

## 3. The process contract

The harness builds the module (a copy, into the system temporary directory) and starts the built service as
`dotnet <output dir>/<ASSEMBLY>.dll` (a plain `dotnet <dll>`, not `dotnet run`: `launchSettings.json` is not read).

| fact | value |
|---|---|
| command line | `dotnet <ASSEMBLY>.dll`, no argument. A background service of the same project is started with the arguments you choose (section 9) |
| working directory | the output directory of the build (a temporary directory): the content root is that directory, and the content files the build copies to its output are found there. It is NOT the module directory: read no relative path into the sources |
| environment | the caller's environment, plus `PORT` (a free TCP port, decimal), `DB_PATH` (the database as `db_env` carries it, section 4), plus what the harness adds with `ProcessApp.env` (the skeleton adds `DB_PROVIDER` and `DB_CONNECTION`, section 8). The service must start with only these set; any other setting it reads needs a default. No `ASPNETCORE_ENVIRONMENT`/`DOTNET_ENVIRONMENT` is set: the hosting environment is `Production` unless the caller's environment says otherwise. The caller's environment may also hold `ASPNETCORE_URLS` |
| listening | on `127.0.0.1` at the port in `PORT`, and nowhere else: `smoke.py` connects to the machine's other IPv4 address at that port and fails if the connection is accepted. ASP.NET Core does not read `PORT` by itself (with nothing configured a web service listens on `http://localhost:5000`), and a value of `ASPNETCORE_URLS` in the caller's environment is not the port to use: the service reads `PORT` |
| database | the one named by the environment (a SQLite file, or a MySQL URL, section 8). The file may not exist yet, may be empty, or may already hold your tables |
| schema | the service creates its own tables and indexes when it starts, on a missing or empty SQLite file or an empty MySQL database, and changes nothing on a database that already holds the same schema. Anything the service inserts at start-up is deleted by the environment before it seeds, so nothing may depend on a start-up row |
| health | `GET /health` answers `200` (any JSON body, no credentials) within 10 seconds of the start (the environment waits up to 15 s, `startup_timeout`; `smoke.py` fails above 10 s). It starts calling at once. A start measured on a small EF Core service: 0.3 to 0.4 s |
| one process per database, a new one per scenario | a check starts a fresh process on each scenario's own database and stops it afterwards, so in-memory state starts empty every time. The environment also sends several requests at one live process at the same time, on one database |
| files | the service writes only its database (and SQLite's own side files `-journal`, `-wal`, `-shm`) and a temporary directory. It writes nothing in the module directory, nothing in the home directory (a framework feature that persists a key ring under the home directory by default writes outside the allowed places; `smoke.py` runs the service with an empty home directory and fails if anything is written there), no log file |
| network | none besides the database: no outgoing call, no container. MySQL 8.4 throwaway containers come from Lattice (`lattice.spec.dbserver`); the service never starts one |
| standard output | discarded. Standard error is captured and shown if the start fails |
| durability | every write is committed to the database before the answer is sent: the environment opens SQLite files with its own connection right after an answer and reads what was committed |
| answers | JSON |
| stop | SIGTERM to the process group, SIGKILL after 5 seconds |
| request timeout | 30 seconds per request |

## 4. `ProcessApp`: every field

`make_app(db)` answers a `ProcessApp`, a handle on the command, not started. It is a dataclass of `lattice.spec.process`;
these are all its fields, in order.

| field | type | default | meaning |
|---|---|---|---|
| `argv` | `Sequence[str]` | required | the command: `[dotnet, "<output dir>/<ASSEMBLY>.dll"]` |
| `db_path` | `Path \| str \| None` | `None` | the database: a SQLite path (made absolute) or a MySQL URL (unchanged); the process gets it in the variable named by `db_env` |
| `env` | `Mapping[str, str]` | `{}` | added to the process environment (the caller's, unless `inherit_env` is False) |
| `cwd` | `Path \| str \| None` | `None` | the working directory (`None`: the caller's); the skeleton sets the build output directory |
| `health_path` | `str` | `"/health"` | the route that answers 200 once the service serves |
| `startup_timeout` | `float` | `15.0` | seconds to wait for `health_path` |
| `port_env` | `str` | `"PORT"` | the variable carrying the port |
| `db_env` | `str` | `"DB_PATH"` | the variable carrying `db_path` |
| `host` | `str` | `"127.0.0.1"` | the address the environment connects to |
| `inherit_env` | `bool` | `True` | start from the caller's environment (`False`: only `env` plus the two variables) |
| `stop_timeout` | `float` | `5.0` | seconds between SIGTERM and SIGKILL |
| `request_timeout` | `float` | `30.0` | seconds per request |
| `ready` | `str` | `"http"` | how start-up is detected; leave the default (`GET health_path` answers 200) |
| `grpc_port_env` | `str \| None` | `None` | not used by an HTTP service; leave it |
| `protos` | `Sequence[str]` | `()` | not used; leave it |
| `proto_paths` | `Sequence[str]` | `()` | not used; leave it |
| `proto_root` | `Path \| str \| None` | `None` | not used; leave it |

Runtime state, not constructor arguments (set by the environment, read-only for you): `grpc_port`, `port`, `pid`,
`returncode`, `timings` (and the private `_proc`, `_stderr`, `_lock`).

The skeleton sets `argv`, `db_path`, `env`, `cwd` and `health_path`; set another field only if your brief says so.

## 5. The names the harness defines

`OUT/tests/spec/<x>_harness.py` defines these names (names are exact):

| name | what |
|---|---|
| `MODULE` | `Path(__file__).resolve().parent / "<x>"`, the module directory |
| `USERS` | `dict[str, tuple[str, str]]`: handle to (login name, password); at least two entries; the password is the text typed at login |
| `make_app(db) -> ProcessApp` | builds the service (once per change of the sources, into the temporary directory) and answers a `ProcessApp`. `db` is a `Path` (SQLite) or a `str` (a MySQL URL or, from the importer, a path). The return annotation `ProcessApp` is required |
| `build_db(db)` | creates and seeds the database at `db` and RETURNS `db`, the very argument; callers use the returned value, so a function that returns `None` fails |
| `RUNTIME` | `process_runtime(framework="dotnet", auth=AuthDriver.json(<login path>, USERS, ...))`, or, for a login that needs more than a name and a password, `process_runtime(framework="dotnet", auth=AuthDriver(login=login))` (section 7) |
| `LOGIN` | `RUNTIME.auth.login`, or your `login` function |
| `SERVICES` | `{service name: (db) -> ServiceCmd}` when the product has a background worker (section 9); `{}` otherwise |

The imports, exactly as written (they come from the checking environment):

```python
from lattice.importer.transport import AuthDriver
from lattice.spec import ServiceCmd
from lattice.spec.process import ProcessApp, process_runtime
```

Signatures:

```python
AuthDriver.json(path: str, credentials: Mapping[str, tuple[str, str]], *, username_field: str = "username",
                password_field: str = "password", token_path: Sequence[str] = ("access_token",),
                scheme: str = "bearer") -> AuthDriver
AuthDriver(scheme: str = "bearer", header: str = "Authorization", login: Login | None = None, mint=None, cookie=None)
process_runtime(*, framework: str = "process", auth: AuthDriver | None = None,
                dispose: Sequence[Callable[[Any], None]] = (),
                services: Mapping[str, Callable[[Path], Any]] | None = None) -> AppRuntime
ServiceCmd(argv: Sequence[str], env: Mapping[str, str] | None = None, cwd: Path | str | None = None,
           stop_timeout: float = 5.0)
```

Use `framework="dotnet"`: it is the value that names a .NET service (another value, such as `"go"`, makes the environment
read your sources as another language). Leave `dispose` and `services` of `process_runtime` at their
defaults (`SERVICES` is passed separately, section 11).

## 6. The harness skeleton

No server code. Fill the TODO places (`seed_rows`, `DDL`, `SERVICES`, `_worker`), keep everything else, replace
`<NAME>` by your `<x>`. Delete the parts that do not apply: with no MySQL, `adonet_mysql`, `_build_server_db` and
`CHECKER_SQL_MODE`; with no background service, `_worker`.

```python
"""Harness of tests/spec/<NAME>: how the service is built and started, how its database is seeded, how callers log in.

``make_app(db)`` answers a ProcessApp (the command, not started). The service is built once per change of its sources,
from a copy of the module directory, into the system temp directory; the module directory is never written.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lattice.importer.transport import AuthDriver
from lattice.spec import ServiceCmd
from lattice.spec.process import ProcessApp, process_runtime

MODULE = Path(__file__).resolve().parent / "<NAME>"
PROJECT = "<NAME>.csproj"  # the startup project, relative to MODULE
ASSEMBLY = "<NAME>"  # its assembly name: the build writes <ASSEMBLY>.dll
BUILD_ENV = "<NAME>_OUT"  # optional: a directory holding an already built <ASSEMBLY>.dll, instead of building
# handle -> (login name, password); at least two entries; the password is the one typed at login
USERS = {"ann": ("ann", "pw-ann"), "ben": ("ben", "pw-ben")}
BUILD_TIMEOUT_S = 900


# ---------------------------------------------------------------- build and start


def _source_key() -> str:
    """A short key of the module's sources: the build is redone only when they change."""
    crc = 0
    for p in sorted(MODULE.rglob("*")):
        rel = p.relative_to(MODULE)
        if p.is_file() and not {"bin", "obj"} & set(rel.parts):
            crc = zlib.crc32(rel.as_posix().encode() + b"\0" + p.read_bytes() + b"\0", crc)
    return f"{crc:08x}"


def _dotnet() -> str:
    found = shutil.which("dotnet")
    if found is None:
        raise RuntimeError("<NAME>: `dotnet` is not on PATH")
    return found


def build_dir() -> Path:
    """The directory holding the built service: ``$<NAME>_OUT``, else built (once per source key) into the system
    temp directory from a copy of MODULE (bin and obj left out)."""
    if env := os.environ.get(BUILD_ENV):
        return Path(env)
    root = Path(tempfile.gettempdir()) / f"build-<NAME>-{_source_key()}"
    out = root / "out"
    if (out / f"{ASSEMBLY}.dll").is_file():
        return out
    shutil.rmtree(root, ignore_errors=True)
    src = root / "src"
    shutil.copytree(MODULE, src, ignore=shutil.ignore_patterns("bin", "obj"))
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    tmp = root / f"out-{os.getpid()}"
    proc = subprocess.run(  # noqa: S603 -- the module's own build
        [_dotnet(), "build", str(src / PROJECT), "-c", "Release", "-p:RestoreLockedMode=true", "-o", str(tmp), "--nologo"],
        cwd=src, capture_output=True, text=True, check=False, env=env, timeout=BUILD_TIMEOUT_S,
    )  # fmt: skip
    if proc.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError(f"<NAME>: dotnet build failed:\n{(proc.stdout + proc.stderr).strip()[-3000:]}")
    os.replace(tmp, out)
    return out


# ---------------------------------------------------------------- database references


def is_url(db: object) -> bool:
    """A server database is a URL (``mysql://user:password@host:port/name``), a SQLite database a file path."""
    return "://" in str(db)


def _value(v: str) -> str:
    """One ADO.NET connection-string value, quoted when it holds a character the syntax reserves."""
    if v == "" or any(c in v for c in ";\"'=") or v != v.strip():
        return '"' + v.replace('"', '""') + '"'
    return v


def adonet_mysql(url: str) -> str:
    """The ADO.NET connection string of a ``mysql://`` URL, for Pomelo.EntityFrameworkCore.MySql 9.0.0 (MySqlConnector)
    and for MySql.EntityFrameworkCore 10.0.9 (Oracle's MySql.Data): both parse these keys and values."""
    p = urlsplit(url)
    parts = [("Server", p.hostname or "127.0.0.1"), ("Port", str(p.port or 3306)), ("Database", unquote(p.path.lstrip("/"))),
             ("User ID", unquote(p.username or "")), ("Password", unquote(p.password or "")),
             ("SslMode", "Disabled"), ("AllowPublicKeyRetrieval", "True")]  # fmt: skip
    return ";".join(f"{k}={_value(v)}" for k, v in parts)


def adonet_sqlite(path: str | Path) -> str:
    """The Microsoft.Data.Sqlite connection string of a database file."""
    return f"Data Source={Path(path).resolve()}"


# ---------------------------------------------------------------- the app and its services


def make_app(db: Path | str) -> ProcessApp:
    """The service on ``db`` (a SQLite path, or a MySQL URL). The service creates its own schema when it starts on an
    empty database. It receives PORT, DB_PATH (``db`` as given, a path made absolute), DB_PROVIDER (``sqlite`` or
    ``mysql``) and DB_CONNECTION (the ADO.NET string)."""
    out = build_dir()
    server = is_url(db)
    env = {"DB_PROVIDER": "mysql" if server else "sqlite",
           "DB_CONNECTION": adonet_mysql(str(db)) if server else adonet_sqlite(db)}  # fmt: skip
    return ProcessApp([_dotnet(), str(out / f"{ASSEMBLY}.dll")], db_path=db, env=env, cwd=str(out), health_path="/health")


def _worker(db: Path | str) -> ServiceCmd:
    """TODO (only when the spec names a background service): how the service starts on ``db``."""
    app = make_app(db)
    return ServiceCmd([*app.argv, "worker"], env={**app.env, "DB_PATH": str(db)}, cwd=app.cwd)


# the spec's background services {name: (db) -> ServiceCmd}; empty when the product has none
SERVICES: dict = {}  # TODO e.g. {"worker": _worker}

RUNTIME = process_runtime(framework="dotnet", auth=AuthDriver.json("/login", USERS))
LOGIN = RUNTIME.auth.login


# ---------------------------------------------------------------- the seed


def seed_rows() -> dict[str, list[dict]]:
    """The seed: table -> rows (column -> value; str, int, float, bool, None or datetime), parents before children.
    The users of USERS carry id 1, 2, ... and their plain password in the password column."""
    users = []
    for i, (login_name, password) in enumerate(USERS.values(), 1):
        users.append({})  # TODO: the user's columns: id i, the login name, the plain password, and every NOT NULL column
    return {"users": users}  # TODO: the other seed rows, one list per table


# The schema exactly as the service creates it on an empty SQLite file (``python3 smoke.py OUT <NAME> schema`` prints it).
DDL = """
-- TODO: the CREATE TABLE / CREATE INDEX statements, one per table and index, each ending with a semicolon
"""  # noqa: E501

# MySQL only: the session mode of the checker's own connections, without STRICT_TRANS_TABLES (see HARNESS_API_DOTNET.md)
CHECKER_SQL_MODE = ("ONLY_FULL_GROUP_BY,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
                    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION")  # fmt: skip


def _sqlite_value(v: object) -> object:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S.%f")
    return v


def build_db(db: Path | str) -> Path | str:
    """A seeded database. A SQLite path: replaced, schema by hand (DDL), then the rows. A MySQL URL (an empty
    database): the service starts once and creates its schema, then the rows go in through Lattice's adapter.
    Answers ``db``, the very argument."""
    if is_url(db):
        return _build_server_db(str(db))
    path = Path(db)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for table, rows in seed_rows().items():
            for row in rows:
                cols = list(row)
                conn.execute(f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                             f'VALUES ({", ".join("?" for _ in cols)})', [_sqlite_value(row[c]) for c in cols])  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    return db


def _build_server_db(url: str) -> str:
    from lattice.spec.db import UrlParts, open_db

    app = make_app(url)
    try:
        app.start()  # the service creates its schema on the empty database
    finally:
        app.stop()
    conn = open_db(url)
    try:
        u = UrlParts(url)
        if (u.host, u.user, u.password) == ("127.0.0.1", "root", "lattice"):  # Lattice's own throwaway server
            conn.execute(f"SET GLOBAL sql_mode = '{CHECKER_SQL_MODE}'")
        for table, rows in seed_rows().items():
            conn.insert(table, rows)
    finally:
        conn.close()
    return url
```

## 7. Logging in

`LOGIN(client, handle)` is called for each key of `USERS`; `client` is an `httpx.Client` on the service's base URL
(`client.post(path, json=...)`, `client.get(...)`; an answer has `.status_code`, `.text`, `.headers`, `.json()`). The
token it returns is an opaque string. Every later call of that user carries the header `Authorization: Bearer <token>`.

With `AuthDriver.json(path, USERS)`, `LOGIN` does `client.post(path, json={username_field: login_name, password_field:
password})`, requires status exactly `200`, reads the JSON answer, follows `token_path` (default: the key
`access_token` of the top-level object) and returns that string. So the login route is:

- `POST <path>` (any path, passed as the first argument of `AuthDriver.json`);
- a JSON body with the two fields named by `username_field` and `password_field` (defaults `username` and `password`;
  pass others as keyword arguments);
- on success `200` with a JSON object holding the string at `token_path` (a tuple of keys, `("data", "token")` reads
  `answer["data"]["token"]`); a wrong login is refused with a 4xx.

When the login needs another field (an installation id, say) or a different shape, write the function yourself and
use it for both names:

```python
def login(client, handle: str) -> str:
    name, password = USERS[handle]
    r = client.post("/some/login", json={"email": name, "password": password, "installation_id": "..."})
    if r.status_code != 200:
        raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text[:200]}")
    return r.json()["access_token"]

RUNTIME = process_runtime(framework="dotnet", auth=AuthDriver(login=login))
LOGIN = login
```

Both forms were run through `python -m lattice.spec check` on a .NET service.

## 8. Databases, seed users and the stored password

### 8.1 SQLite

`make_app(db)` gets a `Path`. The skeleton puts it in the environment three ways: `DB_PATH` (the absolute path),
`DB_PROVIDER=sqlite` and `DB_CONNECTION=Data Source=<absolute path>` (the Microsoft.Data.Sqlite form). A service that
reads other names reads whatever its brief says; the environment sets only what `ProcessApp` says.

### 8.2 MySQL 8.4 (when the brief makes it a database)

The environment gives the harness a URL `mysql://root:lattice@127.0.0.1:<port>/<database>` of an empty database on a
throwaway `mysql:8.4` container of Lattice's (server 8.4.11, `utf8mb4_0900_ai_ci`, `lower_case_table_names = 0`: table
names are case-sensitive; the global `sql_mode` holds `STRICT_TRANS_TABLES`). What happens, in order:

1. `build_db(url)` runs ONCE per seed on an empty database. It has the service create its schema (the skeleton
   starts the service once on the empty database and stops it), then inserts the seed rows through Lattice's adapter
   (`lattice.spec.db.open_db(url).insert(table, rows)`).
2. Every scenario and every concurrency check runs on a CLONE of that database: `CREATE DATABASE`, then each table's
   `SHOW CREATE TABLE` replayed (indexes, generated columns, CHECK and foreign keys kept) and its rows copied with
   foreign key checks off, then views and triggers; the clone is dropped afterwards. So the schema must be
   reproducible by `SHOW CREATE TABLE` (tables, views, triggers), and tables the service keeps for itself are cloned too.
3. `make_app(clone_url)` and the services get the clone's URL. The service is started on it.

The checker reads and writes the database directly, with the table and column names of the spec (given rows,
`expect_db`, invariants): the names in the database are the spec's names, spelled as the spec spells them.

The URL as an ADO.NET connection string: `adonet_mysql(url)` of the skeleton. The one form below was run against
`Pomelo.EntityFrameworkCore.MySql` 9.0.0 (MySqlConnector) and `MySql.EntityFrameworkCore` 10.0.9 (Oracle, MySql.Data),
each creating its schema and reading and writing rows on the 8.4 container:

```
Server=127.0.0.1;Port=<port>;Database=<database>;User ID=root;Password=lattice;SslMode=Disabled;AllowPublicKeyRetrieval=True
```

`Uid` and `Pwd` for `User ID` and `Password` are accepted by both. `SslMode=None` is accepted by Pomelo but rejected by
the Oracle provider (`ArgumentException: Requested value 'None' was not found`): use `Disabled`. A password that holds
`;`, `"`, `'`, `=` or spaces is double-quoted with `""` for a quote (the skeleton does it; `smoke.py` checks the round trip
through a reader of that syntax, and both providers' string builders read the same values back).

Instants in the spec's `given` rows are written as `"2026-03-01T00:00:00Z"`. A strict MySQL session refuses the trailing `Z`
(`1292 Incorrect datetime value`, verified); the harness therefore drops `STRICT_TRANS_TABLES` from the **global**
`sql_mode` of Lattice's own throwaway server (user `root`, password `lattice`, host `127.0.0.1`) when it seeds
(`CHECKER_SQL_MODE` in the skeleton). That affects only the checker's connections: a service that wants a strict mode sets
it on its own session.

### 8.3 Seed users and the stored password

Two kinds of seeded database exist.

**Yours (`build_db`).** The users of `USERS` (at least two), each with its login name and its password stored EXACTLY as
typed at login in the users table: the login must succeed for a row whose password column holds that plain text, and
the answers never carry it. `seed_rows()` is where the rows are written; they must satisfy the route check of section 10.
On SQLite `build_db` writes the schema by hand in plain SQL (the `DDL` string), identical to what the service itself
creates on an empty file (`python3 smoke.py OUT <x> schema` prints it), then the rows.

**The checking environment's own** (used when the service has a SQLite provider). It starts your
service on an empty SQLite file once, lets it create its schema, deletes every row the service wrote at start-up, then
inserts rows itself by plain SQL, without calling the service, and then logs in through your login route. The users it
writes are `admin`, `alice`, `bob` and `carol`; the login name is `<handle>@example.com` (for example
`alice@example.com`, so the login-name column must accept at least 40 characters), the password is `<handle>-pass-0031`
(for example `alice-pass-0031`), and the password column holds that text exactly as typed, as for yours. A second
database for a re-probe uses `-pass-0097`, other row counts and other integer keys: depend on no id. Every other column
gets a placeholder (text such as `Name 01`, integers, 0/1 for booleans, timestamps as text `YYYY-MM-DD HH:MM:SS.ffffff`
in UTC, foreign keys pointing at rows it inserted), respecting NOT NULL and uniqueness. The users table has a text column
that holds the login name, named like the login body's username field or the first unique text column of the table.

## 9. Background services

When the spec names a background service, `SERVICES[name](db)` answers a `ServiceCmd(argv, env, cwd, stop_timeout)`:
how that service starts on the scenario's database (`db` is a SQLite path, or the clone's URL on MySQL). An `Await` step
of a scenario starts it on that scenario's database, polls the database about every 0.2 s until the step's `expect_db`
holds (within the step's seconds it passes, else a finding), and stops it (its process group: SIGTERM, then SIGKILL
after `stop_timeout`) whatever happened. The service runs with the caller's environment plus `ServiceCmd.env`; it gets
no `PORT`. It is a separate process: the skeleton starts the same built assembly with the argument `worker`; how your
program reacts to an argument is yours. The service's database access must commit each change (the checker reads the
database from outside). `smoke.py` starts every `SERVICES` entry on the seeded SQLite file for 2 seconds and fails if it
exits with an error.

## 10. The route check, and `truth.json`

For every route of `truth.json` the environment makes one call:

- every `{...}` segment of the route's `template` is replaced by `1`: path parameters are integer row ids;
- a `POST`, `PUT` or `PATCH` carries the JSON body `{}`;
- the caller is the first user of `USERS` whose role in `truth.json` `seed_users` matches the route's `auth` (`Member`
  or `Admin`); a `Public` route is called without credentials;
- order: all `GET`s, then the writes, then the `DELETE`s.

The status must be neither `404` nor `405`, which is what an unregistered route answers. A `404` that your own code
answers for a missing row is not told apart from it. So `build_db` must seed, for each table a path parameter names, a row
with id `1` that the calling user may reach (it belongs to that user, directly or through its parents), and the parent
rows it needs. The environment also calls each non-public route without credentials and expects `401` or `403` (a
warning, not a failure). If your brief fixes the rows of the seed, `build_db` writes exactly those and no more; a
path-parameter route then may answer `404` for the missing row, and `python3 smoke.py OUT <x> --warn-404` prints it as a
warning instead of a failure.

`smoke.py` reads only these keys of `truth.json` (its full schema is in your brief): `routes[{method, template, auth,
file, line}]`, `seed_users[{name, role}]` (one per login name of `USERS`, roles `Member` or `Admin`) and `login.path`
(a public `POST` route in `routes`).

## 11. `studio.toml`, and how the harness module is imported

```toml
[app]
app = "tests.spec.<x>_harness:make_app"
db = "tests.spec.<x>_harness:build_db"
runtime = "tests.spec.<x>_harness:RUNTIME"
```

Keys of the table `[app]` that Lattice reads: `app`, `db`, `runtime` (always), and `login`
(`"tests.spec.<x>_harness:LOGIN"`, when a function other than the runtime's is the login), `services`
(`"tests.spec.<x>_harness:SERVICES"`, when there are background services), `database` (`"mysql"` or `"sqlite"`; absent:
`sqlite`), `login_path`, `database_url`. Use only the keys your brief asks for.

The DOTTED module form `tests.spec.<x>_harness:<attribute>` is required. The bare form `<x>_harness:make_app` cannot be
imported by the environment (it answers `ModuleNotFoundError: No module named '<x>_harness'`; checked). The dotted form
resolves because the environment runs from the repository root, which is on `sys.path`: `tests/` has NO `__init__.py`
(a namespace package) and `tests/spec/` has one (an empty file, already in the repository). So create neither.

The harness runs inside Lattice's Python process: `lattice` is importable there, and so are `httpx` and, for MySQL,
PyMySQL. Import only the standard library and those names; the harness prints nothing on import.

## 12. Check it yourself without the checking environment

Save the script below as `$SCRATCH/smoke.py` (outside `OUT`) and run it, after the exports of section 2, with Python 3.11+
and nothing installed:

```sh
python3 $SCRATCH/smoke.py $OUT <x> schema        # starts the service on an empty SQLite file, prints the schema it creates
python3 $SCRATCH/smoke.py $OUT <x>               # every check
python3 $SCRATCH/smoke.py $OUT <x> --warn-404    # as above, a 404 on a path-parameter route is a warning
```

Use the output of the first command as the `DDL` of the harness. The second command runs, in this order: the layout (the
files exist, no `bin/` or `obj/`); `studio.toml` (dotted keys); `dotnet --version` is 10.0.401; every project file targets
`net10.0`, sets `RestorePackagesWithLockFile` and has a `packages.lock.json`; the clean-copy build with
`-p:RestoreLockedMode=true` (it stops here if the build fails) and the lock file unchanged by it; the harness (names,
`USERS`, `RUNTIME.framework`, `adonet_mysql` round trips, `build_db` returns its argument and is repeatable, the stored
plain passwords); `make_app` and the start on the seeded file (`GET /health` is 200 within 10 s, the tables, columns and
indexes unchanged by the start, the listener refuses the machine's other address); the login of every seed user; the
`truth.json` keys and the route check of section 10; the stop; every `SERVICES` entry for 2 seconds; the start on a missing
file and the equality of the schema it creates with the schema `build_db` writes; that the service wrote nothing to its
home directory, nothing but SQLite's side files next to its database, and nothing in the module. All lines must read
`PASS`; the last line reads `ALL CHECKS PASSED`. The checks run on SQLite; a MySQL database is not started here.

```python
#!/usr/bin/env python3
"""Stand-alone check of a .NET service module and its harness. Python 3.11+, standard library only.

    python3 smoke.py OUT_DIR NAME             # every check below, then a summary
    python3 smoke.py OUT_DIR NAME schema      # start the service on an empty SQLite file, print the schema it creates
    python3 smoke.py OUT_DIR NAME --warn-404  # as the first, a 404 on a route with a path parameter is a warning

OUT_DIR holds tests/spec/NAME/ (the project, studio.toml, truth.json) and tests/spec/NAME_harness.py.
Exit status 0 only when every check passes. The imports of the checking environment that the harness needs (a process
handle, a login description, a service command) are replaced here by stand-ins with the same fields and behaviour.
The checks run on SQLite; a MySQL database is not started here.
"""

from __future__ import annotations

import filecmp
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import types
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SDK = "10.0.401"
FAILS: list[str] = []
WARN_404 = "--warn-404" in sys.argv
if WARN_404:
    sys.argv.remove("--warn-404")


def ok(name: str, cond: bool, detail: str = "") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + (f"   -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ---------------------------------------------------------------- stand-ins for the checking environment's imports


@dataclass(eq=False)
class ProcessApp:
    argv: Any
    db_path: Any = None
    env: Any = field(default_factory=dict)
    cwd: Any = None
    health_path: str = "/health"
    startup_timeout: float = 15.0
    port_env: str = "PORT"
    db_env: str = "DB_PATH"
    host: str = "127.0.0.1"
    inherit_env: bool = True
    stop_timeout: float = 5.0
    request_timeout: float = 30.0
    ready: str = "http"
    grpc_port_env: Any = None
    protos: Any = ()
    proto_paths: Any = ()
    proto_root: Any = None


@dataclass(frozen=True)
class ServiceCmd:
    argv: Any
    env: Any = None
    cwd: Any = None
    stop_timeout: float = 5.0


class Resp:
    def __init__(self, status_code: int, text: str, headers: dict[str, str]) -> None:
        self.status_code, self.text, self.headers = status_code, text, headers

    def json(self) -> Any:
        return json.loads(self.text)


def http(method: str, url: str, token: str | None = None, body: Any = None, timeout: float = 30.0,
         headers: dict[str, str] | None = None) -> Resp:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Resp(r.status, r.read().decode(errors="replace"), dict(r.headers))
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read().decode(errors="replace"), dict(e.headers))


class Client:
    """What a login function is handed (httpx.Client in the checking environment): request/get/post/put/patch/delete
    with ``json=``, ``headers=``; the answer has status_code, text, headers, json()."""

    def __init__(self, base: str) -> None:
        self.base = base

    def request(self, method: str, path: str, json: Any = None, headers: dict[str, str] | None = None,
                **_: Any) -> Resp:  # noqa: A002 -- same signature as the real client
        return http(method, self.base + path, body=json, headers=headers)

    def get(self, path: str, **kw: Any) -> Resp:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Resp:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> Resp:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw: Any) -> Resp:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Resp:
        return self.request("DELETE", path, **kw)


class AuthDriver:
    def __init__(self, scheme: str = "bearer", header: str = "Authorization", login: Any = None) -> None:
        self.scheme, self.header, self.login = scheme.lower(), header, login

    @classmethod
    def json(cls, path, credentials, *, username_field="username", password_field="password",
             token_path=("access_token",), scheme="bearer"):  # noqa: ANN001
        def login(client: Any, handle: str) -> str:
            username, password = credentials[handle]
            r = client.post(path, json={username_field: username, password_field: password})
            if r.status_code != 200:
                raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text}")
            value = r.json()
            for key in token_path:
                value = value[key]
            return value

        return cls(scheme=scheme, login=login)


def process_runtime(*, framework="process", auth=None, dispose=(), services=None):  # noqa: ANN001
    return types.SimpleNamespace(framework=framework, auth=auth if auth is not None else AuthDriver(),
                                 dispose=tuple(dispose), services=dict(services or {}))


def install_stand_ins() -> None:
    mods = {n: types.ModuleType(n) for n in ("lattice", "lattice.importer", "lattice.importer.transport", "lattice.spec",
                                             "lattice.spec.process", "lattice.spec.services")}
    mods["lattice.importer.transport"].AuthDriver = AuthDriver
    mods["lattice.spec.process"].ProcessApp = ProcessApp
    mods["lattice.spec.process"].process_runtime = process_runtime
    mods["lattice.spec.services"].ServiceCmd = ServiceCmd
    mods["lattice.spec"].ServiceCmd = ServiceCmd
    sys.modules.update(mods)


# ---------------------------------------------------------------- running the process


class Server:
    """What the checking environment does with a ProcessApp: port, environment, health wait, stop."""

    def __init__(self, app: ProcessApp, confine: dict[str, str] | None = None) -> None:
        self.app, self.proc, self.port, self.err, self.health_s = app, None, 0, None, 0.0
        self.confine = confine or {}

    def start(self) -> str:
        a = self.app
        with socket.socket() as s:
            s.bind((a.host, 0))
            self.port = s.getsockname()[1]
        env = dict(os.environ) if a.inherit_env else {}
        env.update({k: str(v) for k, v in a.env.items()})
        env.update(self.confine)
        env[a.port_env] = str(self.port)
        if a.db_path is not None:
            env[a.db_env] = str(Path(a.db_path).resolve())
        self.err = tempfile.TemporaryFile()
        t0 = time.monotonic()
        self.proc = subprocess.Popen([str(x) for x in a.argv], cwd=None if a.cwd is None else str(a.cwd), env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.err,
                                     start_new_session=True)
        base = f"http://{a.host}:{self.port}"
        deadline = t0 + a.startup_timeout
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError(f"exited with code {self.proc.returncode} before answering {a.health_path}; "
                                   f"stderr:\n{self.stderr()}")
            try:
                if http("GET", base + a.health_path, timeout=1.0).status_code == 200:
                    self.health_s = time.monotonic() - t0
                    return base
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"no 200 on {a.health_path} within {a.startup_timeout:g}s; stderr:\n{self.stderr()}")
            time.sleep(0.02)

    def stderr(self) -> str:
        if self.err is None:
            return ""
        self.err.flush()
        self.err.seek(0)
        return self.err.read().decode(errors="replace")[-4000:]

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(self.app.stop_timeout)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait()


# ---------------------------------------------------------------- utilities


def snapshot(path: Path) -> dict[str, Any]:
    """Tables (column name, type, not null, primary key) and indexes (unique, columns) of a SQLite file."""
    con = sqlite3.connect(path)
    try:
        out: dict[str, Any] = {}
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tables:
            cols = sorted((c[1], c[2].lower(), c[3], c[5]) for c in con.execute(f'PRAGMA table_info("{t}")'))
            idx = sorted((i[2], tuple(x[2] for x in con.execute(f'PRAGMA index_info("{i[1]}")')))
                         for i in con.execute(f'PRAGMA index_list("{t}")') if not i[1].startswith("sqlite_"))
            out[t] = {"columns": cols, "indexes": idx}
        return out
    finally:
        con.close()


def run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int = 900) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    return p.returncode, (p.stdout + p.stderr).strip()


def file_line_ok(module: Path, ref: Any) -> bool:
    if not isinstance(ref, str) or ":" not in ref:
        return False
    name, _, line = ref.rpartition(":")
    p = (module / name).resolve()
    if not line.isdigit() or not p.is_file():
        return False
    return 1 <= int(line) <= len(p.read_text(errors="replace").splitlines())


def parse_adonet(s: str) -> dict[str, str]:
    """A small ADO.NET connection-string reader (keys case-folded, double-quoted values with "" for a quote)."""
    out: dict[str, str] = {}
    i = 0
    while i < len(s):
        eq = s.index("=", i)
        key = s[i:eq].strip().lower()
        i = eq + 1
        if i < len(s) and s[i] == '"':
            i += 1
            val = ""
            while True:
                if s[i] == '"' and i + 1 < len(s) and s[i + 1] == '"':
                    val += '"'
                    i += 2
                elif s[i] == '"':
                    i += 1
                    break
                else:
                    val += s[i]
                    i += 1
            while i < len(s) and s[i] != ";":
                i += 1
        else:
            j = s.find(";", i)
            j = len(s) if j < 0 else j
            val = s[i:j]
            i = j
        out[key] = val
        i += 1
    return out


def copy_module(module: Path, dest: Path) -> None:
    shutil.copytree(module, dest, ignore=shutil.ignore_patterns("bin", "obj"))


def locked_build(module: Path, project: str, work: Path, env: dict[str, str]) -> tuple[int, str, Path]:
    src = work / "cleancopy"
    shutil.rmtree(src, ignore_errors=True)
    copy_module(module, src)
    out = work / "cleanout"
    shutil.rmtree(out, ignore_errors=True)
    code, text = run(["dotnet", "build", str(src / project), "-c", "Release", "-p:RestoreLockedMode=true", "-o", str(out),
                      "--nologo"], src, env)
    return code, text, src


# ---------------------------------------------------------------- truth.json (only the keys the route check reads)


def check_truth_keys(truth: dict[str, Any], module: Path, harness_users: dict[str, Any]) -> None:
    routes = truth.get("routes", [])
    ok("truth: routes is a non-empty list of {method, template, auth, file, line}", bool(routes) and all(
        isinstance(r, dict) and r.get("method") in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        and str(r.get("template", "")).startswith("/") and r.get("auth") in ("Public", "Member", "Admin")
        and file_line_ok(module, f'{r.get("file")}:{r.get("line")}') for r in routes))
    keys = [f'{r.get("method")} {r.get("template")}' for r in routes]
    ok("truth: routes are distinct", len(set(keys)) == len(keys))
    seeds = truth.get("seed_users", [])
    names = {u.get("name") for u in seeds}
    ok("truth: seed_users are {name, role} with roles Member or Admin, one per login name of the harness's USERS",
       isinstance(seeds, list) and names == {v[0] for v in harness_users.values()}
       and all(u.get("role") in ("Member", "Admin") for u in seeds))
    ok("truth: login has a path that is a public POST route",
       any(r.get("method") == "POST" and r.get("template") == (truth.get("login") or {}).get("path")
           and r.get("auth") == "Public" for r in routes))


def sweep(truth: dict[str, Any], base: str, users: dict[str, Any], tokens: dict[str, str]) -> None:
    """Every truth route, called as a user who may call it, path parameters replaced by 1, {} on writes."""
    roles = {u["name"]: u["role"] for u in truth.get("seed_users", [])}
    by_role: dict[str, str] = {}
    for handle, (login_name, _pw) in users.items():
        role = roles.get(login_name)
        if role and handle in tokens:
            by_role.setdefault(role, tokens[handle])
    order = {"GET": 0, "POST": 1, "PUT": 1, "PATCH": 1, "DELETE": 2}
    seen: list[str] = []
    for r in sorted(truth.get("routes", []), key=lambda r: order.get(r["method"], 1)):
        has_param = "{" in r["template"]
        url = base + re.sub(r"\{[^}]*\}", "1", r["template"])
        token = None if r["auth"] == "Public" else by_role.get(r["auth"])
        body = {} if r["method"] in ("POST", "PUT", "PATCH") else None
        if r["auth"] != "Public" and token is None:
            ok(f'route {r["method"]} {r["template"]}: a seed user with role {r["auth"]} exists', False)
            continue
        resp = http(r["method"], url, token=token, body=body)
        seen.append(resp.text)
        label = f'route {r["method"]} {r["template"]} as {r["auth"]}: answers neither 404 nor 405 (got {resp.status_code})'
        hint = ("a 404 here means the route is not registered, or row 1 / its parent is missing from the seed or is not "
                "reachable by the seed user")
        if resp.status_code == 404 and WARN_404 and has_param:
            print(f"WARN  {label} -- {hint}")
        else:
            ok(label, resp.status_code not in (404, 405), hint)
        if r["auth"] != "Public":
            anon = http(r["method"], url, body=body)
            if anon.status_code not in (401, 403):
                print(f'WARN  {r["method"]} {r["template"]} without credentials answered {anon.status_code} '
                      f'(truth says {r["auth"]})')
    leaked = [p for _u, p in users.values() if any(p in t for t in seen)]
    ok("no answer of the sweep contains a seed user's stored password", not leaked, str(leaked))


def lan_address() -> str | None:
    """This machine's outbound IPv4 address when it is not a loopback one (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") or ip == "0.0.0.0" else ip
    except OSError:
        return None


# ---------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    out, name = Path(argv[1]).resolve(), argv[2]
    mode = argv[3] if len(argv) > 3 else "all"
    module = out / "tests" / "spec" / name
    harness_py = out / "tests" / "spec" / f"{name}_harness.py"
    work = Path(tempfile.mkdtemp(prefix="smoke-", dir=os.environ.get("TMPDIR") or None))
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    project = f"{name}.csproj"
    home, tmpdir, dbdir = work / "home", work / "tmp", work / "db"
    for d in (home, tmpdir, dbdir):
        d.mkdir()
    # the service runs with an empty home directory and its own temp directory: whatever it writes elsewhere shows
    confine = {"HOME": str(home), "DOTNET_CLI_HOME": str(home), "TMPDIR": str(tmpdir)}

    if mode == "all":
        for rel in (project, "packages.lock.json", "studio.toml", "truth.json"):
            ok(f"layout: {module.name}/{rel} exists", (module / rel).is_file())
        ok(f"layout: tests/spec/{name}_harness.py exists", harness_py.is_file())
        ok("layout: no bin or obj directory under the module (delete them: rm -rf bin obj)",
           not [p for p in module.rglob("*") if p.is_dir() and p.name in ("bin", "obj")])
        try:
            app_cfg = tomllib.loads((module / "studio.toml").read_text()).get("app", {})
        except Exception as err:  # noqa: BLE001
            app_cfg = {}
            ok("studio.toml parses", False, repr(err))
        for key, attr in (("app", "make_app"), ("db", "build_db"), ("runtime", "RUNTIME")):
            ok(f"studio.toml [app] {key} = tests.spec.{name}_harness:{attr}",
               app_cfg.get(key) == f"tests.spec.{name}_harness:{attr}", str(app_cfg.get(key)))
        extra = {k: v for k, v in app_cfg.items() if k not in ("app", "db", "runtime")}
        ok("studio.toml [app] other keys are login, services (dotted form) or database (mysql or sqlite)", all(
            (k in ("login", "services") and re.fullmatch(rf"tests\.spec\.{name}_harness:[A-Za-z_]\w*", str(v)))
            or (k == "database" and v in ("mysql", "sqlite")) for k, v in extra.items()), str(extra))
        code, text = run(["dotnet", "--version"], module, env)
        ok(f"dotnet --version is {SDK}", code == 0 and text.splitlines()[-1] == SDK, text)
        csprojs = sorted(p for p in module.rglob("*.csproj") if not {"bin", "obj"} & set(p.relative_to(module).parts))
        ok("every *.csproj targets net10.0 and sets RestorePackagesWithLockFile to true, next to a packages.lock.json",
           bool(csprojs) and all(
               re.search(r"<TargetFrameworks?>\s*net10\.0\s*</TargetFrameworks?>", p.read_text())
               and re.search(r"<RestorePackagesWithLockFile>\s*true\s*</RestorePackagesWithLockFile>", p.read_text(), re.I)
               and (p.parent / "packages.lock.json").is_file() for p in csprojs))
        code, text, src = locked_build(module, project, work, env)
        tail = " ".join(x for x in text.splitlines() if "Warning(s)" in x or "Error(s)" in x)
        ok(f"clean copy: dotnet build -c Release -p:RestoreLockedMode=true succeeds ({tail.strip()})", code == 0, text[-1800:])
        if code != 0:
            print("\nSTOPPED: the clean-copy build fails; fix it first (after changing a package reference run "
                  "`dotnet restore --force-evaluate` in the project directory and keep the new packages.lock.json).")
            print(f"{len(FAILS)} CHECK(S) FAILED")
            return 1
        if (module / "packages.lock.json").is_file():
            ok("the build leaves packages.lock.json byte-identical",
               filecmp.cmp(module / "packages.lock.json", src / "packages.lock.json", shallow=False))

    install_stand_ins()
    spec = importlib.util.spec_from_file_location(f"tests.spec.{name}_harness", harness_py)
    assert spec and spec.loader
    h = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = h
    spec.loader.exec_module(h)

    if mode == "schema":
        db = work / "empty.db"
        srv = Server(h.make_app(db))
        try:
            srv.start()
        finally:
            srv.stop()
        con = sqlite3.connect(db)
        for (sql,) in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                                  "ORDER BY type DESC, name"):
            print(sql.strip() + ";")
        return 0

    ok("harness defines make_app, build_db, RUNTIME, LOGIN, USERS",
       all(hasattr(h, a) for a in ("make_app", "build_db", "RUNTIME", "LOGIN", "USERS")))
    users = dict(h.USERS)
    ok("USERS maps at least two handles to (login name, password) pairs of non-empty strings", len(users) >= 2 and all(
        isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, str) and x for x in v) for v in users.values()))
    ok("LOGIN is callable (RUNTIME.auth.login for AuthDriver.json)", callable(h.LOGIN))
    ok("RUNTIME is process_runtime(framework='dotnet', ...)", h.RUNTIME.framework == "dotnet")
    if hasattr(h, "adonet_mysql"):
        for pw in ("lattice", 'p;w"x\'=y', " sp ", "a%b@c:d/e"):
            from urllib.parse import quote
            parsed = parse_adonet(h.adonet_mysql(f"mysql://us%40r:{quote(pw, safe='')}@127.0.0.1:3307/db%201"))
            ok(f"adonet_mysql round-trips the password {pw!r}", (parsed.get("server"), parsed.get("port"),
               parsed.get("database"), parsed.get("user id", parsed.get("uid")), parsed.get("password", parsed.get("pwd")))
               == ("127.0.0.1", "3307", "db 1", "us@r", pw), str(parsed))
    if hasattr(h, "adonet_sqlite"):
        ok("adonet_sqlite is Data Source=<absolute path>", h.adonet_sqlite(work / "x.db") == f"Data Source={(work / 'x.db').resolve()}")

    db = dbdir / "seeded.db"
    returned = h.build_db(db)
    ok("build_db(path) returns the path it was given", returned == db and Path(returned) == db, repr(returned))
    again = h.build_db(db)
    ok("build_db twice on the same path replaces the file and returns the path", again == db)
    con = sqlite3.connect(db)
    texts = {str(v) for t in snapshot(db) for row in con.execute(f'SELECT * FROM "{t}"') for v in row}
    con.close()
    ok("every seed user's login name and plain password is stored in the file",
       all(u in texts and p in texts for u, p in users.values()))
    before = snapshot(db)

    app = h.make_app(db)
    ok("make_app(path) returns a ProcessApp", isinstance(app, ProcessApp))
    ok("ProcessApp.db_path is the path; argv[0] is an existing file; health_path starts with /",
       Path(app.db_path) == db and Path(str(app.argv[0])).is_file() and app.health_path.startswith("/"))
    module_before = sorted(str(p.relative_to(module)) for p in module.rglob("*")) if module.is_dir() else []
    srv = Server(app, confine)
    try:
        base = srv.start()
        ok(f"GET {app.health_path} answers 200 on the seeded file", True)
        ok(f"the service answered within 10 s of its start ({srv.health_s:.2f} s)", srv.health_s <= 10.0)
        ok("starting on the harness's file leaves its tables, columns and indexes unchanged",
           snapshot(db) == before, "before/after differ: " + json.dumps(
               {t: [before.get(t), snapshot(db).get(t)] for t in set(before) | set(snapshot(db))
                if before.get(t) != snapshot(db).get(t)})[:900])
        lan = lan_address()
        if lan is None:
            print("SKIP  no non-loopback address to try the loopback-only binding on")
        else:
            try:
                with socket.create_connection((lan, srv.port), timeout=2):
                    reached = True
            except OSError:
                reached = False
            ok(f"the service listens on 127.0.0.1 only (a connection to {lan}:{srv.port} is refused)", not reached)
        client = Client(base)
        tokens: dict[str, str] = {}
        for handle in users:
            try:
                tokens[handle] = h.LOGIN(client, handle)
                ok(f"login as {handle!r} (200, a token in the answer)", bool(tokens[handle]))
            except Exception as err:  # noqa: BLE001
                ok(f"login as {handle!r}", False, repr(err))
        if mode == "all":
            truth = json.loads((module / "truth.json").read_text())
            bad = client.post(truth.get("login", {}).get("path", "/login"), json={})
            ok("a login with an empty body is refused with a 4xx", 400 <= bad.status_code < 500, str(bad.status_code))
            check_truth_keys(truth, module, users)
            sweep(truth, base, users, tokens)
    finally:
        srv.stop()
        if mode == "all":
            ok("the process is stopped", srv.proc is None or srv.proc.poll() is not None)

    services = getattr(h, "SERVICES", {})
    for sname, factory in dict(services).items():
        cmd = factory(db)
        ok(f"service {sname!r}: a ServiceCmd whose argv[0] exists", isinstance(cmd, ServiceCmd) and (
            Path(str(cmd.argv[0])).is_file() or shutil.which(str(cmd.argv[0])) is not None))
        senv = dict(os.environ)
        senv.update({k: str(v) for k, v in (cmd.env or {}).items()})
        senv.update(confine)
        errf = tempfile.TemporaryFile()
        proc = subprocess.Popen([str(a) for a in cmd.argv], cwd=None if cmd.cwd is None else str(cmd.cwd), env=senv,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
        time.sleep(2.0)
        alive = proc.poll() is None
        if alive:
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        errf.seek(0)
        ok(f"service {sname!r} runs for 2 s on the seeded file without exiting in error",
           alive or proc.returncode == 0, errf.read().decode(errors="replace")[-800:])

    if mode == "all":
        empty = work / "never-created.db"
        srv2 = Server(h.make_app(empty), confine)
        try:
            srv2.start()
            ok("starts on a file that does not exist and creates its own schema", True)
        except Exception as err:  # noqa: BLE001
            ok("starts on a file that does not exist and creates its own schema", False, str(err)[:800])
        finally:
            srv2.stop()
        ok("the schema created on an empty file equals the schema build_db writes",
           empty.exists() and snapshot(empty) == before,
           json.dumps({t: [before.get(t), snapshot(empty).get(t)] for t in set(before) | set(snapshot(empty))
                       if before.get(t) != snapshot(empty).get(t)})[:900] if empty.exists() else "no file")

    if mode == "all":
        ok("the service wrote nothing to its home directory", not list(home.rglob("*")),
           str(sorted(str(p) for p in home.rglob("*"))[:5]))
        side = {db.name, db.name + "-journal", db.name + "-wal", db.name + "-shm"}
        ok("next to the database file the service wrote only SQLite's own side files",
           {p.name for p in dbdir.iterdir()} <= side, str(sorted(p.name for p in dbdir.iterdir())))
        ok("the module directory is unchanged by running the service",
           module_before == sorted(str(p.relative_to(module)) for p in module.rglob("*")))
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' CHECK(S) FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```
