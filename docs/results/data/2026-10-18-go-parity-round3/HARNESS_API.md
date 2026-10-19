# HARNESS_API: how your service is started, seeded, logged into and checked

This file states every fact about the environment that will build, start and check your service. That environment
is not on your machine. Two Python modules it provides (`lattice.importer.transport` and `lattice.spec.process`)
are imported by the harness file you write; you cannot import them, so `smoke.py` (section 10) stands in for them
with the same fields and the same behaviour. Nothing here describes your service's design: that is yours.

Replace `<x>` everywhere with the name you give the service: lowercase letters and digits, starting with a letter.

## 1. Layout of what you hand back

Everything you hand back is under `OUT = /Users/vivekgade/Documents/g3-s4-author/out`:

```
OUT/tests/spec/<x>/              the Go module: go.mod, go.sum, main.go (package main, at the module root),
                                 your other packages and directories, studio.toml, truth.json
OUT/tests/spec/<x>_harness.py    the harness (section 5)
```

Create no other file under `OUT/tests/`. The module directory is never written by the checking environment: the
binary is built into the system temporary directory.

## 2. Toolchain and modules

| fact | value |
|---|---|
| Go | `go version` prints `go1.26.2` (darwin/arm64) |
| module path | `example.com/<x>` (the first line of `go.mod`) |
| `go` directive | `go 1.22` |
| direct requirements | `github.com/gin-gonic/gin v1.10.0`, `github.com/glebarez/sqlite v1.11.0`, `gorm.io/gorm v1.25.12`; any other module you need is your choice |
| where modules come from | the Go module proxy (`GOPROXY` is its default, `https://proxy.golang.org,direct`); the machine has network access to it |
| files committed | `go.mod` AND `go.sum`, both complete, in the module directory |
| how it is built | `go build -mod=readonly` (`GOFLAGS=-mod=readonly`, `GOTOOLCHAIN=auto`): it must succeed without changing `go.mod` or `go.sum`; so run `go mod tidy` after your last code change |
| not allowed | a `vendor` directory, `replace` directives, modules from a local path |
| cgo | not needed: the SQLite driver is pure Go. The build does not rely on a C compiler |

`go mod tidy -diff` must print nothing and `gofmt -l .` must print nothing.

To keep every file the toolchain writes inside your scratch directory, export before any command:

```sh
export SCRATCH=/Users/vivekgade/Documents/g3-s4-author
export PATH=/opt/homebrew/bin:$PATH
export GOPATH=$SCRATCH/.gopath GOCACHE=$SCRATCH/.gocache TMPDIR=$SCRATCH/.tmp GOFLAGS=-mod=readonly
mkdir -p "$TMPDIR"
```

## 3. The process contract

The environment builds the module with `go build -trimpath -buildvcs=false -o <temporary dir>/<x> .` run in the
module directory, and starts that one binary.

| fact | value |
|---|---|
| command line | the binary alone, no arguments |
| environment | the caller's environment plus `PORT` (a free TCP port, decimal) and `DB_PATH` (the absolute path of a SQLite file). The service must start with only these two set (any other setting it reads needs a default) |
| listening | on `PORT` of `127.0.0.1` (a listener on all interfaces is also reached) |
| database | the SQLite file at `DB_PATH`, opened through GORM over `github.com/glebarez/sqlite`. The file may not exist yet, may be empty, or may already hold your tables |
| schema | the service creates its own tables and indexes when it starts, on a missing or empty file, and changes nothing on a file that already holds the same schema. Anything the service inserts at start-up is deleted by the environment before it seeds, so nothing may depend on a start-up row |
| health | `GET /health` answers `200` (any JSON body, no credentials) within 15 seconds of the start; the environment starts calling it at once |
| working directory | inherited from the caller, NOT the module directory: read no relative path |
| standard output | discarded. Standard error is captured and shown if the start fails |
| files | the service writes only the SQLite file at `DB_PATH` (and SQLite's own side files) |
| concurrency | one process serves one database file; several clients call it at the same time |
| durability | every write is committed to the file before the answer is sent: the environment opens the file with its own connection right after an answer |
| answers | JSON |
| stop | SIGTERM to the process group, SIGKILL after 5 seconds |
| request timeout | 30 seconds per request |

## 4. `ProcessApp`: every field

`make_app(db_path)` answers a `ProcessApp`, a handle on the command, not started. It is a dataclass of
`lattice.spec.process`; these are all its fields, in order.

| field | type | default | meaning |
|---|---|---|---|
| `argv` | `Sequence[str]` | required | the command: `[str(binary_path)]` |
| `db_path` | `Path \| str \| None` | `None` | the database file; the process gets its absolute path in the variable named by `db_env` |
| `env` | `Mapping[str, str]` | `{}` | added to the process environment |
| `cwd` | `Path \| str \| None` | `None` | the process's working directory (`None`: the caller's) |
| `health_path` | `str` | `"/health"` | the route that answers 200 once the service serves |
| `startup_timeout` | `float` | `15.0` | seconds to wait for `health_path` |
| `port_env` | `str` | `"PORT"` | the variable carrying the port |
| `db_env` | `str` | `"DB_PATH"` | the variable carrying the database path |
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

The harness uses only `argv`, `db_path` and `health_path`, as in the skeleton (section 5).

## 5. The harness file and its skeleton

`OUT/tests/spec/<x>_harness.py` defines these names (all of them; names are exact):

| name | what |
|---|---|
| `MODULE` | `Path(__file__).resolve().parent / "<x>"`, the module directory |
| `USERS` | `dict[str, tuple[str, str]]`: handle to (login name, password); at least two entries; the password is the text typed at login |
| `make_app(db_path: Path) -> ProcessApp` | builds the binary (once per change of the sources, into the temporary directory) and answers `ProcessApp([str(binary)], db_path=db_path, health_path="/health")`. The return annotation `ProcessApp` is required |
| `build_db(path: Path) -> Path` | creates and seeds a SQLite file at `path` (replacing an existing file) and RETURNS `path`; callers use the returned value, so a function that returns `None` fails |
| `RUNTIME` | `process_runtime(framework="go", auth=AuthDriver.json("/login", USERS))` |
| `LOGIN` | `RUNTIME.auth.login` |

The two imports, exactly as written (they come from the checking environment):

```python
from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime
```

Signatures:

```python
AuthDriver.json(path: str, credentials: Mapping[str, tuple[str, str]], *, username_field: str = "username",
                password_field: str = "password", token_path: Sequence[str] = ("access_token",),
                scheme: str = "bearer") -> AuthDriver
process_runtime(*, framework: str = "process", auth: AuthDriver | None = None,
                dispose: Sequence[Callable[[Any], None]] = (),
                services: Mapping[str, Callable[[Path], Any]] | None = None) -> AppRuntime
```

Use `framework="go"`; leave `dispose` and `services` at their defaults. Skeleton (no server code; fill the two
TODO places, keep everything else; replace `<NAME>` by your `<x>`):

```python
"""Harness of tests/spec/<NAME>: how the service is started, how its database is seeded, how callers log in.

``make_app(db_path)`` answers a ProcessApp (the binary, not started). The binary is built once from the
module with ``go build -mod=readonly`` into the system temp directory; the module directory is never written.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import zlib
from pathlib import Path

from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

MODULE = Path(__file__).resolve().parent / "<NAME>"
BINARY_ENV = "<NAME>_BIN"  # optional: a prebuilt binary, instead of building
# handle -> (login name, password); at least two entries; the password is the one typed at login
USERS = {"ann": ("ann", "pw-ann"), "ben": ("ben", "pw-ben")}
BUILD_TIMEOUT_S = 600


def _source_key() -> str:
    """A short key of the module's sources: the binary is rebuilt only when it changes."""
    crc = 0
    for p in sorted(MODULE.rglob("*")):
        if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")):
            crc = zlib.crc32(p.relative_to(MODULE).as_posix().encode() + b"\0" + p.read_bytes() + b"\0", crc)
    return f"{crc:08x}"


def binary() -> Path:
    """The built service: ``$<NAME>_BIN``, else built (once per source key) into the system temp directory."""
    if env := os.environ.get(BINARY_ENV):
        return Path(env)
    out = Path(tempfile.gettempdir()) / f"build-<NAME>-{_source_key()}" / "<NAME>"
    if out.is_file():
        return out
    go = shutil.which("go")
    if go is None:
        raise RuntimeError("<NAME>: `go` is not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".<NAME>-{os.getpid()}")
    env = {**os.environ, "GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": "auto"}
    proc = subprocess.run(  # noqa: S603 -- the module's own build
        [go, "build", "-trimpath", "-buildvcs=false", "-o", str(tmp), "."],
        cwd=MODULE, capture_output=True, text=True, check=False, env=env,
        timeout=BUILD_TIMEOUT_S,
    )  # fmt: skip
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"<NAME>: go build failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    return out


def make_app(db_path: Path) -> ProcessApp:
    """The service on ``db_path`` (the service creates its own schema when it starts)."""
    return ProcessApp([str(binary())], db_path=db_path, health_path="/health")


RUNTIME = process_runtime(framework="go", auth=AuthDriver.json("/login", USERS))
LOGIN = RUNTIME.auth.login

# The schema exactly as the service creates it on an empty file (``python3 smoke.py OUT <NAME> schema`` prints it).
DDL = """
-- TODO: the CREATE TABLE / CREATE INDEX statements, one per table and index, each ending with a semicolon
"""  # noqa: E501


def build_db(path: Path) -> Path:
    """A seeded database written by hand: the users of ``USERS`` (the stored password is the plain text typed at
    login) and the rows the routes need (see HARNESS_API.md). Answers ``path``."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for i, (login_name, password) in enumerate(USERS.values(), 1):
            # TODO: INSERT the user with id i, the login name and the plain password in their columns
            pass
        # TODO: INSERT the other seed rows
        conn.commit()
    finally:
        conn.close()
    return path
```

## 6. Logging in

`LOGIN(client, handle)` is called for each key of `USERS`. It does `client.post(path, json={username_field:
login_name, password_field: password})`, requires status exactly `200`, reads the JSON answer, follows
`token_path` (default: the key `access_token` of the top-level object) and returns that string. Every later call
of that user carries the header `Authorization: Bearer <that string>`. So the login route you write is:

- `POST /login` (any path, passed as the first argument of `AuthDriver.json`);
- a JSON body with the two fields named by `username_field` and `password_field` (defaults `username` and
  `password`; pass others as keyword arguments if you prefer);
- on success `200` with a JSON object holding the string at `token_path`; a wrong login is refused with a 4xx.

## 7. Seed users, and the stored password

Two kinds of seeded file exist.

**Yours (`build_db`).** The users of `USERS` (at least two), each with its login name and its password stored
EXACTLY as typed at login in the users table: the login must succeed for a row whose password column holds that
plain text. `build_db` writes the schema by hand in plain SQL through Python's `sqlite3` module (the `DDL`
string), identical to what the service itself creates on an empty file, then the rows. The rows must satisfy
the route check of section 8.

**The checking environment's own.** It starts your service on an empty file once, lets it create its schema,
deletes every row the service wrote at start-up, then inserts rows itself by plain SQL, without calling the
service, and then logs in through your login route. The users it writes are `admin`, `alice`, `bob` and
`carol`; the login name is `<handle>@example.com` (for example `alice@example.com`, so the login-name column
must accept at least 40 characters), the password is `<handle>-pass-0031` (for example `alice-pass-0031`), and
the password column holds that text exactly as typed, as for yours. Every other column gets a placeholder
(text such as `Name 01`, integers, 0/1 for booleans, timestamps as text `YYYY-MM-DD HH:MM:SS.ffffff` in UTC,
foreign keys pointing at rows it inserted), respecting NOT NULL and uniqueness. The users table has a unique text column that holds the login name, declared before any other unique text
column of that table (or a column named like the login body's username field).

## 8. The route check

For every route of `truth.json` the environment makes one call:

- the path parameters (`:name` in Gin's pattern syntax) are replaced by `1`: path parameters are integer row ids;
- a `POST`, `PUT` or `PATCH` carries the JSON body `{}`;
- the caller is the first seed user, in `USERS` order, whose role in `truth.json` matches the route's `auth`
  (`Member` or `Admin`); a `Public` route is called without credentials;
- order: all `GET`s, then the writes, then the `DELETE`s.

The status must be neither `404` nor `405`, which is what an unregistered route answers. A `404` that your own
code answers for a missing row is not told apart from it. So `build_db` must seed, for each table a path
parameter names, a row with id `1` that the calling user may reach (it belongs to that user, directly or through
its parents), and the parent rows it needs. The environment also calls each non-public route without
credentials and expects `401` or `403` (a warning, not a failure).

## 9. `studio.toml`

```toml
[app]
app = "tests.spec.<x>_harness:make_app"
db = "tests.spec.<x>_harness:build_db"
runtime = "tests.spec.<x>_harness:RUNTIME"
```

Exactly these three keys in the table `[app]`, with the DOTTED module form `tests.spec.<x>_harness:<attribute>`.
This form is required. The bare form `<x>_harness:make_app` cannot be imported by the environment (it answers
`ModuleNotFoundError: No module named '<x>_harness'`).

## 10. Check it yourself without the checking environment

Save the script below as `/Users/vivekgade/Documents/g3-s4-author/smoke.py` (outside `OUT`) and run, after the exports of section 2:

```sh
python3 /Users/vivekgade/Documents/g3-s4-author/smoke.py /Users/vivekgade/Documents/g3-s4-author/out <x> schema   # starts the service on an empty file, prints the schema it creates
python3 /Users/vivekgade/Documents/g3-s4-author/smoke.py /Users/vivekgade/Documents/g3-s4-author/out <x>          # every check
```

Use the output of the first command as the `DDL` of the harness. The second command runs, in this order:
the layout; `studio.toml`; `go version`; `gofmt -l .`; `go build -mod=readonly ./...`; `go vet ./...`;
`go mod tidy -diff`; the harness (names, `USERS`, `build_db` returns its argument and is repeatable, the stored
plain passwords); `make_app`; the start on the seeded file (`GET /health` is 200 and the tables, columns and indexes
are unchanged by it); the login of every seed user; `truth.json` (its shape, the counts, every `file:line`);
the route check of section 8; the stop; the start on a missing file and the equality of the schema it creates
with the schema `build_db` writes. All lines must read `PASS`; the last line reads `ALL CHECKS PASSED`.

The commands behind it, runnable by hand from the module directory:

```sh
gofmt -l .                       # prints nothing
go build -mod=readonly ./...     # exit 0
go vet ./...                     # exit 0
go mod tidy -diff                # prints nothing
```

```python
#!/usr/bin/env python3
"""Stand-alone check of a service module and its harness. Python 3.11+, standard library only.

    python3 smoke.py OUT_DIR NAME            # every check below, then a summary
    python3 smoke.py OUT_DIR NAME schema     # start the service on an empty file, print the schema it creates

OUT_DIR holds tests/spec/NAME/ (the Go module, studio.toml, truth.json) and
tests/spec/NAME_harness.py. Exit status 0 only when every check passes.
The two imports of the checking environment that the harness needs (a process handle and a
login description) are replaced here by stand-ins with the same fields and behaviour.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FAILS: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> bool:
    print(("PASS  " if cond else "FAIL  ") + name + (f"   -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ---------------------------------------------------------------- stand-ins for the two imports


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


class Resp:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code, self.text = status_code, text

    def json(self) -> Any:
        return json.loads(self.text)


def http(method: str, url: str, token: str | None = None, body: Any = None, timeout: float = 30.0) -> Resp:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Resp(r.status, r.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read().decode(errors="replace"))


class Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def post(self, path: str, json: Any = None) -> Resp:  # noqa: A002 -- same signature as the real client
        return http("POST", self.base + path, body=json)


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
    mods = {
        "lattice": types.ModuleType("lattice"),
        "lattice.importer": types.ModuleType("lattice.importer"),
        "lattice.importer.transport": types.ModuleType("lattice.importer.transport"),
        "lattice.spec": types.ModuleType("lattice.spec"),
        "lattice.spec.process": types.ModuleType("lattice.spec.process"),
    }
    mods["lattice.importer.transport"].AuthDriver = AuthDriver
    mods["lattice.spec.process"].ProcessApp = ProcessApp
    mods["lattice.spec.process"].process_runtime = process_runtime
    sys.modules.update(mods)


# ---------------------------------------------------------------- running the process


class Server:
    """What the checking environment does with a ProcessApp: port, environment, health wait, stop."""

    def __init__(self, app: ProcessApp) -> None:
        self.app, self.proc, self.port, self.err = app, None, 0, None

    def start(self) -> str:
        a = self.app
        with socket.socket() as s:
            s.bind((a.host, 0))
            self.port = s.getsockname()[1]
        env = dict(os.environ) if a.inherit_env else {}
        env.update({k: str(v) for k, v in a.env.items()})
        env[a.port_env] = str(self.port)
        if a.db_path is not None:
            env[a.db_env] = str(Path(a.db_path).resolve())
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([str(x) for x in a.argv], cwd=None if a.cwd is None else str(a.cwd), env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.err,
                                     start_new_session=True)
        base = f"http://{a.host}:{self.port}"
        deadline = time.monotonic() + a.startup_timeout
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError(f"exited with code {self.proc.returncode} before answering {a.health_path}; "
                                   f"stderr:\n{self.stderr()}")
            try:
                if http("GET", base + a.health_path, timeout=1.0).status_code == 200:
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


def run(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900, check=False)
    return p.returncode, (p.stdout + p.stderr).strip()


def file_line_ok(module: Path, ref: Any) -> bool:
    if not isinstance(ref, str) or ":" not in ref:
        return False
    name, _, line = ref.rpartition(":")
    p = (module / name).resolve()
    if not line.isdigit() or not p.is_file():
        return False
    return 1 <= int(line) <= len(p.read_text(errors="replace").splitlines())


def pos(file: Any, line: Any) -> str:
    return f"{file}:{line}"


# ---------------------------------------------------------------- truth.json


METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
CAUSES = {"auth", "not_found", "not_owned", "invalid", "conflict", "other"}


def check_truth(truth: dict[str, Any], module: Path, harness_users: dict[str, Any]) -> None:
    ok("truth: top-level keys", {"routes", "tables", "owners", "owner_via", "machines", "refusals", "bind_status",
                                 "login", "seed_users", "refs", "package", "harness"} <= set(truth),
       str(sorted({"routes", "tables", "owners", "owner_via", "machines", "refusals", "bind_status", "login",
                   "seed_users", "refs", "package", "harness"} - set(truth))))
    routes = truth.get("routes", [])
    ok("truth: 10 to 16 routes in total", 10 <= len(routes) <= 16, f"{len(routes)} routes")
    good = all(isinstance(r, dict) and r.get("method") in METHODS and str(r.get("path", "")).startswith("/")
               and r.get("auth") in ("Public", "Member", "Admin") and file_line_ok(module, pos(r.get("file"), r.get("line")))
               for r in routes)
    ok("truth: every route has method, path, auth, and an existing file:line", good)
    keys = [f'{r.get("method")} {r.get("path")}' for r in routes]
    ok("truth: routes are distinct", len(set(keys)) == len(keys))
    ok("truth: GET /health is a public route", any(r.get("method") == "GET" and r.get("path") == "/health"
                                                   and r.get("auth") == "Public" for r in routes))
    login = truth.get("login", {})
    ok("truth: login is a public POST route listed in routes with a field list",
       any(r.get("method") == "POST" and r.get("path") == login.get("path") and r.get("auth") == "Public"
           for r in routes) and isinstance(login.get("fields"), list) and len(login.get("fields", [])) >= 2)
    tables = truth.get("tables", [])
    structs = {t.get("struct"): t for t in tables}
    ok("truth: tables have struct, table and columns (name, json, private)", bool(tables) and all(
        t.get("struct") and t.get("table") and t.get("columns") and all(
            isinstance(c.get("name"), str) and (c.get("json") is None or isinstance(c.get("json"), str))
            and isinstance(c.get("private"), bool) and c["private"] == (c.get("json") is None)
            for c in t["columns"]) for t in tables))
    ok("truth: at least one never-returned column", any(c.get("private") for t in tables for c in t.get("columns", [])))
    cols = {s: {c.get("name") for c in t.get("columns", [])} for s, t in structs.items()}
    owners, via = truth.get("owners", {}), truth.get("owner_via", {})
    ok("truth: owners name a struct and one of its columns",
       bool(owners) and all(s in structs and c in cols[s] for s, c in owners.items()))
    ok("truth: owner_via names a struct, a parent struct and a column of the struct",
       bool(via) and all(s in structs and isinstance(v, list) and len(v) == 2 and v[0] in structs
                         and v[1] in cols[s] for s, v in via.items()))
    ok("truth: per-user data on at least two tables", len(set(owners) | set(via)) >= 2)
    machines = truth.get("machines", {})
    ok("truth: a status column with at least two transitions", any(
        isinstance(m, dict) and len({tuple(e) for e in m.get("edges", [])}) >= 2 for m in machines.values()))
    ok("truth: machine keys are Struct.column of tables", all(
        k.split(".")[0] in structs and k.split(".")[-1] in cols.get(k.split(".")[0], set()) for k in machines))
    ref = truth.get("refusals", [])
    ok("truth: refusals name a listed route, a 4xx or 5xx status, a cause, an existing file:line", bool(ref) and all(
        r.get("route") in keys and isinstance(r.get("status"), int) and 400 <= r["status"] < 600
        and r.get("cause") in CAUSES and file_line_ok(module, pos(r.get("file"), r.get("line"))) for r in ref))
    ok("truth: bind_status is a 4xx integer", isinstance(truth.get("bind_status"), int) and 400 <= truth["bind_status"] < 500)
    seeds = truth.get("seed_users", [])
    names = {u.get("name") for u in seeds}
    ok("truth: seed_users match the harness's login names, roles Member or Admin, at least two Members",
       names == {v[0] for v in harness_users.values()} and all(u.get("role") in ("Member", "Admin") for u in seeds)
       and sum(u.get("role") == "Member" for u in seeds) >= 2)
    refs = truth.get("refs", {})
    need = [f"tables.{s}" for s in structs] + [f"owners.{s}" for s in owners] + [f"owner_via.{s}" for s in via]
    for k, m in machines.items():
        need += [f"machines.{k}.initial"] + [f"machines.{k}.edge.{a}->{b}" for a, b in m.get("edges", [])]
    need += ["bind_status", "login", "seed_users"]
    missing = [k for k in need if k not in refs]
    ok("truth: refs has a file:line for every table, owner, owner_via, machine initial and edge, bind_status, login, "
       "seed_users", not missing, f"missing {missing[:6]}")
    bad = [k for k, v in refs.items() if not file_line_ok(module, v)]
    ok("truth: every refs entry is an existing file:line", not bad, f"bad {bad[:6]}")


# ---------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    out, name = Path(argv[1]).resolve(), argv[2]
    mode = argv[3] if len(argv) > 3 else "all"
    module = out / "tests" / "spec" / name
    harness_py = out / "tests" / "spec" / f"{name}_harness.py"
    work = Path(tempfile.mkdtemp(prefix="smoke-", dir=os.environ.get("TMPDIR") or None))
    env = {**os.environ, "GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": "auto"}

    if mode == "all":
        for rel in ("go.mod", "go.sum", "main.go", "studio.toml", "truth.json"):
            ok(f"layout: {module.name}/{rel} exists", (module / rel).is_file())
        ok(f"layout: tests/spec/{name}_harness.py exists", harness_py.is_file())
        try:
            import tomllib
            app = tomllib.loads((module / "studio.toml").read_text()).get("app", {})
        except Exception as err:  # noqa: BLE001
            app = {}
            ok("studio.toml parses", False, repr(err))
        for key, attr in (("app", "make_app"), ("db", "build_db"), ("runtime", "RUNTIME")):
            ok(f"studio.toml [app] {key} = tests.spec.{name}_harness:{attr}",
               app.get(key) == f"tests.spec.{name}_harness:{attr}", str(app.get(key)))
        code, text = run(["go", "version"], module, env)
        ok("go version is go1.26.2", "go1.26.2" in text, text)
        code, text = run(["gofmt", "-l", "."], module, env)
        ok("gofmt -l prints nothing", code == 0 and not text, text)
        code, text = run(["go", "build", "-mod=readonly", "-o", os.devnull, "./..."], module, env)
        ok("go build -mod=readonly ./...", code == 0, text[-1500:])
        code, text = run(["go", "vet", "./..."], module, env)
        ok("go vet ./...", code == 0, text[-1500:])
        code, text = run(["go", "mod", "tidy", "-diff"], module, env)
        ok("go.mod and go.sum are tidy (go mod tidy -diff prints nothing)", code == 0 and not text, text[-800:])
        ok("no vendor directory", not (module / "vendor").exists())

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
    ok("LOGIN is RUNTIME.auth.login", h.LOGIN is h.RUNTIME.auth.login and callable(h.LOGIN))
    ok("RUNTIME is process_runtime(framework='go', ...)", h.RUNTIME.framework == "go")

    db = work / "seeded.db"
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
    srv = Server(app)
    try:
        base = srv.start()
        ok(f"GET {app.health_path} answers 200 on the seeded file", True)
        ok("starting on the harness's file leaves its tables, columns and indexes unchanged",
           snapshot(db) == before, "before/after differ: " + json.dumps(
               {t: [before.get(t), snapshot(db).get(t)] for t in set(before) | set(snapshot(db))
                if before.get(t) != snapshot(db).get(t)})[:900])
        client = Client(base)
        tokens: dict[str, str] = {}
        for handle in users:
            try:
                tokens[handle] = h.LOGIN(client, handle)
                ok(f"login as {handle!r} (POST with the credentials, 200, token in the answer)", bool(tokens[handle]))
            except Exception as err:  # noqa: BLE001
                ok(f"login as {handle!r}", False, repr(err))
        if mode == "all":
            truth = json.loads((module / "truth.json").read_text())
            bad = client.post(truth.get("login", {}).get("path", "/login"), json={})
            ok("a login with an empty body is refused with a 4xx", 400 <= bad.status_code < 500, str(bad.status_code))
            check_truth(truth, module, users)
            sweep(truth, base, users, tokens)
    finally:
        srv.stop()
        if mode == "all":
            ok("the process is stopped", srv.proc is None or srv.proc.poll() is not None)

    if mode == "all":
        empty = work / "never-created.db"
        srv2 = Server(h.make_app(empty))
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

    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' CHECK(S) FAILED'}")
    return 1 if FAILS else 0


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
        url = base + re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "1", r["path"])
        token = None if r["auth"] == "Public" else by_role.get(r["auth"])
        body = {} if r["method"] in ("POST", "PUT", "PATCH") else None
        if r["auth"] != "Public" and token is None:
            ok(f'route {r["method"]} {r["path"]}: a seed user with role {r["auth"]} exists', False)
            continue
        resp = http(r["method"], url, token=token, body=body)
        seen.append(resp.text)
        ok(f'route {r["method"]} {r["path"]} as {r["auth"]}: answers neither 404 nor 405 (got {resp.status_code})',
           resp.status_code not in (404, 405),
           "a 404 here means the route is not registered, or row 1 / its parent is missing from the seed or is not "
           "reachable by the seed user")
        if r["auth"] != "Public":
            anon = http(r["method"], url, body=body)
            if anon.status_code not in (401, 403):
                print(f'WARN  {r["method"]} {r["path"]} without credentials answered {anon.status_code} '
                      f'(truth says {r["auth"]})')
    leaked = [p for _u, p in users.values() if any(p in t for t in seen)]
    ok("no answer of the sweep contains a seed user's stored password", not leaked, str(leaked))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```
