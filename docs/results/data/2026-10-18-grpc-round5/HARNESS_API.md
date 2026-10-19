**A.1 Files.** Your app directory holds `harness.py` and `studio.toml` at its root:

```toml
[app]
app = "harness:make_app"
db = "harness:build_db"
runtime = "harness:RUNTIME"
```

**A.2 What `harness.py` defines.**
- `USERS: dict[str, tuple[str, str]]`: handle -> (login name, password). Every handle must be able to log in on a freshly built database.
- `build_db(path) -> path`: create and seed a SQLite database file at `path` (a `pathlib.Path`; its directory exists), then **return
  `path`**. Do not return `None`; do not create files elsewhere.
- `make_app(db_path) -> ProcessApp`: called with exactly what `build_db` returned. It may build your server (into a temporary directory,
  never into the app tree) and returns a `ProcessApp` describing how to start it. Find Go with `shutil.which("go")`; do not hard-code a path.
- `RUNTIME`: **the value returned by `process_runtime(...)`**, never the `AuthDriver` itself:

```python
from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

AUTH = AuthDriver.rpc("pkg.v1.SomeService", "SomeLogin", USERS,
                      username_field="<field>", password_field="<field>", token_field="token")
RUNTIME = process_runtime(framework="go", auth=AUTH)
```

`AuthDriver.rpc(service: str, method: str, credentials: dict[str, tuple[str, str]], *, username_field: str | None = None,
password_field: str | None = None, token_field: str = "token", scheme: str = "bearer") -> AuthDriver`. Everything after `credentials` is
**keyword-only**; positional use is an error. With the two field names left `None` they are discovered from the login request message (the
one string field whose name contains `pass`, and the one other string field); name them if your message has more than two string fields.
`token_field` is a dotted path into the login response. The token is sent as metadata `authorization: Bearer <token>`.

`process_runtime(*, framework: str = "process", auth: AuthDriver | None = None, dispose=(), services=None) -> AppRuntime`; pass
`framework="go"`.

**A.3 `ProcessApp` fields a gRPC app uses** (from `lattice/spec/process.py`; all keyword arguments except `argv`):
`argv: Sequence[str]` (the command); `db_path: Path | str | None` (pass `db_path`, it is exported to your process); `env: Mapping[str,
str]` (added to the inherited environment); `cwd: Path | str | None`; `port_env: str = "PORT"` (the variable carrying the free port your
server must listen on, on 127.0.0.1); `db_env: str = "DB_PATH"` (the variable carrying `db_path`); `ready: str = "http"`, set **`ready=
"grpc"`**: start-up is detected by `grpc.health.v1.Health/Check` answering `SERVING` on that port, so register the standard health service;
`startup_timeout: float = 15.0` (seconds; raise it if your server is slow to start); `protos: Sequence[str]` (your `.proto` files, paths
relative to `proto_root`), `proto_paths: Sequence[str]` (include roots), `proto_root: Path | str | None` (base directory of the two lists);
`grpc_port_env: str | None = None` (only if the app serves HTTP on `port_env` and gRPC on a second port); `stop_timeout: float = 5.0`;
`request_timeout: float = 30.0`; `host: str = "127.0.0.1"`; `inherit_env: bool = True`. Do not set any other attribute.

**A.4 Skeleton (fill in; no server code here).**

```python
from pathlib import Path
import shutil, sqlite3, subprocess, tempfile
from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime

ROOT = Path(__file__).resolve().parent
USERS = {"<handle>": ("<login name>", "<password>")}
RUNTIME = process_runtime(framework="go", auth=AuthDriver.rpc("<svc>", "<method>", USERS, token_field="token"))

def build_db(path):
    con = sqlite3.connect(path)
    ...  # schema (or apply your own migrations) and seed rows
    con.commit(); con.close()
    return path

def make_app(db_path):
    out = Path(tempfile.mkdtemp()) / "server"
    subprocess.run([shutil.which("go"), "build", "-o", str(out), "./cmd/<server>"], cwd=ROOT, check=True)
    return ProcessApp([str(out)], db_path=str(db_path), ready="grpc",
                      protos=["<a.proto>", ...], proto_paths=["."], proto_root=str(ROOT / "<proto dir>"))
```

**A.5 MySQL variant (PM-SG only).** Add `database = "mysql"` under `[app]`. Then `build_db(db)` receives a database **URL**
`mysql://USER:PASS@HOST:PORT/DB` for an empty database (not a file path), creates the schema and seed rows in it, and returns `db`;
`make_app(db)` receives that URL; translate it for the Go MySQL driver with `from lattice.spec.db import url_for; url_for(db,
"go-mysql")` and pass it to your server in an environment variable of your choice (keep `db_path=db` as well). MySQL is 8.4, started by
Lattice; do not start containers yourself.

**A.6 Spec steps (PM-SG only).** `from lattice.spec import Rpc`: `Rpc(service: str, method: str, user: str | None = None, message: Any =
None, metadata: Mapping[str, str] | None = None, expect_code: str = "OK", expect_message: Any | None = None, expect_db: Mapping[str, Any]
| None = None)`; `service` is the full name (`pkg.v1.Service`), `user` a `USERS` handle (None: no token), `message` the request in proto
field names (64-bit integers within +/-2^53 as JSON numbers), `expect_code` an UPPER_SNAKE `google.rpc.Code` name, `expect_message` a
subset of the response (OK) or of `{"code", "message"}` (an error). Only unary rpcs.

**A.7 Your own check.** `contract5 <your app dir>` (if present in your directory) prints one line per fact above (truth schema, codes,
import, RUNTIME, build_db, make_app, ProcessApp, server start, one login per handle, one replay per truth flow). Hand over only when every
line says `ok`.
