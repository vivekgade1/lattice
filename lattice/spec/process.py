"""Checking an app that is not Python: a process speaking HTTP over a SQLite file.

A process app's ``app_factory(db_path)`` answers a :class:`ProcessApp` -- a handle on the
command that serves the database, not started. :func:`process_runtime` is the
:class:`~lattice.importer.transport.AppRuntime` that serves it:

* ``open(handle)`` starts the process (once per handle: one process serves one
  database file) on a free port, with the port and the database path in its
  environment under the names the handle states (``port_env``, ``db_env``), waits until
  ``GET health_path`` answers 200 -- or raises :class:`ProcessStartError` (a
  ``CheckError``) with the process's stderr -- and answers an
  :class:`~lattice.importer.transport.HttpxTransport` on ``http://127.0.0.1:<port>``.
  Every further ``open`` of a running handle answers one more client of the same
  server: layer X races ``k`` clients on one live process (``one_server``).
* ``close(handle)`` (the runtime's dispose hook, run after every app, whatever
  happened) terminates the process group -- SIGTERM, then SIGKILL after
  ``stop_timeout`` -- and reaps it. An ``atexit`` guard stops whatever is still running
  when the interpreter exits.

**gRPC** (Spike I): ``ProcessApp(ready="grpc")`` starts the app when ``grpc.health.v1.
Health/Check`` answers SERVING (``ready="tcp"``: when the port accepts a connection) and
serves the spec's ``Rpc`` steps through :meth:`ProcessTransport.rpc` (one bridge process per
transport: :mod:`lattice.spec.grpc`). The one port ``port_env`` names then serves gRPC; with
``grpc_port_env`` set the app serves HTTP on ``port_env``'s port (a grpc-gateway) and gRPC on
a second one. ``ready="http"`` (the default) is the behaviour above, unchanged.

On a server database (``check(..., database="postgres" | "mysql" | "clickhouse")``)
``db_path`` is the database's canonical URL (:mod:`lattice.spec.db`), and ``db_env``
carries it unchanged (a SQLite path is made absolute); the app opens it (a Go app
translates it with :func:`lattice.spec.db.url_for`, e.g. ``go-mysql``).

The check reads the SQLite file directly (layer C's invariants, a scenario's
``expect_db``, layer B's tables) once a request's answer has been read in full: what the
app committed before answering is what a new SQLite connection sees.

A harness::

    from lattice.importer.transport import AuthDriver
    from lattice.spec.process import ProcessApp, process_runtime

    def make_app(db_path) -> ProcessApp:
        return ProcessApp(["./bin/server"], db_path=db_path, env={"GIN_MODE": "release"},
                          port_env="PORT", db_env="DATABASE_PATH", health_path="/health")

    RUNTIME = process_runtime(framework="go", auth=AuthDriver.json("/login", USERS))
    check(SPEC, make_app, build_db, layers="CDX", runtime=RUNTIME)
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from lattice.importer.transport import AppRuntime, AuthDriver, HttpxTransport
from lattice.spec.check import CheckError

STDERR_TAIL = 4000  # characters of the process's stderr a startup error carries
PORT_RETRIES = 3  # a free port taken between choosing it and the process binding it


class ProcessStartError(CheckError):
    """A process app did not start: it exited, or never answered its health route."""


_LIVE: set[ProcessApp] = set()
_LIVE_LOCK = threading.Lock()


def live_processes() -> list[ProcessApp]:
    """The process apps started and not yet stopped (empty after every check)."""
    with _LIVE_LOCK:
        return [p for p in _LIVE if p.running]


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


@dataclass(eq=False)
class ProcessApp:
    """A handle on a process serving one SQLite database over HTTP (not started).

    ``argv``/``cwd``: the command; ``env``: added to the environment (the caller's, unless
    ``inherit_env`` is False), plus ``port_env`` = the chosen port and ``db_env`` = the
    database path; ``health_path``: answers 200 once the app serves, within
    ``startup_timeout`` seconds; ``stop_timeout``: SIGTERM's grace before SIGKILL;
    ``request_timeout``: per request, seconds. ``timings`` (runtime state): the seconds
    of the last start -- ``process_spawn_s`` (choosing a port, ``Popen``),
    ``health_wait_s`` (until ``health_path`` answered 200: the app's own start-up),
    ``start_attempts`` -- and of the last stop, ``process_stop_s``."""

    argv: Sequence[str]
    db_path: Path | str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | str | None = None
    health_path: str = "/health"
    startup_timeout: float = 15.0
    port_env: str = "PORT"
    db_env: str = "DB_PATH"
    host: str = "127.0.0.1"
    inherit_env: bool = True
    stop_timeout: float = 5.0
    request_timeout: float = 30.0
    # gRPC (Spike I). ``ready``: how start-up is detected -- ``"http"`` (``GET health_path``
    # answers 200: today's behaviour), ``"grpc"`` (``grpc.health.v1.Health/Check`` answers
    # SERVING) or ``"tcp"`` (the port accepts a connection). ``grpc_port_env``: the
    # environment variable carrying a second free port, for an app serving HTTP (``port_env``)
    # and gRPC on two ports; None: with ``ready="grpc"`` the one port serves gRPC. ``protos``
    # / ``proto_paths`` / ``proto_root``: the ``.proto`` files the rpc transport describes
    # when the spec names none (paths relative to ``proto_root``, default the working
    # directory) and their include roots.
    ready: str = "http"
    grpc_port_env: str | None = None
    protos: Sequence[str] = ()
    proto_paths: Sequence[str] = ()
    proto_root: Path | str | None = None
    # runtime state
    grpc_port: int | None = field(default=None, init=False)
    port: int | None = field(default=None, init=False)
    pid: int | None = field(default=None, init=False)
    returncode: int | None = field(default=None, init=False)
    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)
    _stderr: IO[bytes] | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    timings: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("the process app is not started")
        return f"http://{self.host}:{self.port}"

    def describe(self) -> str:
        return " ".join(str(a) for a in self.argv)

    def stderr_text(self) -> str:
        """What the process wrote to stderr (its tail)."""
        if self._stderr is None:
            return ""
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            text = self._stderr.read().decode(errors="replace")
        except (OSError, ValueError):
            return ""
        return text if len(text) <= STDERR_TAIL else "..." + text[-STDERR_TAIL:]

    # -- lifecycle

    def start(self) -> str:
        """Start the process (once) and wait for its health route; its base URL."""
        with self._lock:
            if self._proc is not None:
                return self.base_url
            spawn = health = 0.0
            for attempt in range(PORT_RETRIES):
                try:
                    t0 = time.perf_counter()
                    self._spawn()
                    t1 = time.perf_counter()
                    spawn += t1 - t0
                    try:
                        self._wait_healthy()
                    finally:
                        health += time.perf_counter() - t1
                        self.timings.update(process_spawn_s=round(spawn, 4),
                                            health_wait_s=round(health, 4),
                                            start_attempts=attempt + 1)  # fmt: skip
                    return self.base_url
                except ProcessStartError as err:
                    in_use = "address already in use" in str(err).lower()
                    self._stop_locked()
                    if not in_use or attempt == PORT_RETRIES - 1:
                        raise
                    self._proc = None
            raise AssertionError("unreachable")  # pragma: no cover

    def _spawn(self) -> None:
        self.port = _free_port(self.host)
        self.grpc_port = None
        if self.grpc_port_env is not None:
            self.grpc_port = _free_port(self.host)
            while self.grpc_port == self.port:
                self.grpc_port = _free_port(self.host)
        elif self.ready == "grpc":
            self.grpc_port = self.port
        env = dict(os.environ) if self.inherit_env else {}
        env.update({k: str(v) for k, v in self.env.items()})
        env[self.port_env] = str(self.port)
        if self.grpc_port_env is not None:
            env[self.grpc_port_env] = str(self.grpc_port)
        if self.db_path is not None:
            env[self.db_env] = _db_env_value(self.db_path)
        if self._stderr is not None:
            self._stderr.close()
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 -- closed in stop()
        self.returncode = None
        try:
            self._proc = subprocess.Popen(  # noqa: S603 -- the harness's own command
                [str(a) for a in self.argv],
                cwd=None if self.cwd is None else str(self.cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr,
                start_new_session=True,  # its own process group: stopped as a whole
            )
        except OSError as err:
            raise ProcessStartError(f"cannot start {self.describe()!r}: {err}") from err
        self.pid = self._proc.pid
        with _LIVE_LOCK:
            _LIVE.add(self)

    def _wait_healthy(self) -> None:
        if self.ready != "http":
            self._wait_ready()
            return
        import httpx

        assert self._proc is not None
        url = self.base_url + self.health_path
        deadline = time.monotonic() + self.startup_timeout
        last = "no answer"
        with httpx.Client(timeout=1.0) as client:
            while True:
                code = self._proc.poll()
                if code is not None:
                    self.returncode = code
                    raise ProcessStartError(
                        f"process app {self.describe()!r} exited with code {code} before "
                        f"answering {self.health_path}; stderr:\n{self.stderr_text() or '(empty)'}"
                    )
                try:
                    r = client.get(url)
                    if r.status_code == 200:
                        return
                    last = f"status {r.status_code}"
                except httpx.HTTPError as err:
                    last = f"{type(err).__name__}"
                if time.monotonic() > deadline:
                    raise ProcessStartError(
                        f"process app {self.describe()!r} did not answer 200 on "
                        f"{self.health_path} within {self.startup_timeout:g}s (last: {last}); "
                        f"stderr:\n{self.stderr_text() or '(empty)'}"
                    )
                time.sleep(0.02)

    def _wait_ready(self) -> None:
        """Wait for a gRPC (``Health/Check`` SERVING) or a TCP (a connection) start-up."""
        assert self._proc is not None
        target = f"{self.host}:{self.grpc_port if self.grpc_port is not None else self.port}"
        what = "grpc.health.v1.Health/Check SERVING" if self.ready == "grpc" else "a TCP connection"
        deadline = time.monotonic() + self.startup_timeout
        last = "no answer"
        bridge = None
        if self.ready == "grpc":
            from lattice.spec.grpc import Bridge

            bridge = Bridge()
        try:
            while True:
                code = self._proc.poll()
                if code is not None:
                    self.returncode = code
                    raise ProcessStartError(
                        f"process app {self.describe()!r} exited with code {code} before "
                        f"answering {what}; stderr:\n{self.stderr_text() or '(empty)'}"
                    )
                if bridge is not None:
                    from lattice.spec.grpc import BridgeError

                    try:
                        answer = bridge.request(
                            {"op": "health", "target": target, "timeout_ms": 1000}
                        )
                        if answer.get("serving"):
                            return
                        last = str(answer.get("code") or answer.get("status") or "not serving")
                    except BridgeError as err:
                        last = err.kind
                else:
                    try:
                        with socket.create_connection(
                            (self.host, int(target.rsplit(":", 1)[1])), 1.0
                        ):
                            return
                    except OSError as err:
                        last = type(err).__name__
                if time.monotonic() > deadline:
                    raise ProcessStartError(
                        f"process app {self.describe()!r} did not answer {what} within "
                        f"{self.startup_timeout:g}s (last: {last}); "
                        f"stderr:\n{self.stderr_text() or '(empty)'}"
                    )
                time.sleep(0.02)
        finally:
            if bridge is not None:
                bridge.stop()

    def stop(self) -> None:
        """Terminate the process group (SIGTERM, then SIGKILL) and reap it."""
        with self._lock:
            started = self.running
            t0 = time.perf_counter()
            self._stop_locked()
            if started:
                self.timings["process_stop_s"] = round(time.perf_counter() - t0, 4)

    def _stop_locked(self) -> None:
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                _signal_group(proc, signal.SIGTERM)
                try:
                    proc.wait(self.stop_timeout)
                except subprocess.TimeoutExpired:
                    _signal_group(proc, signal.SIGKILL)
                    proc.wait()
            # the group's other members (children the app spawned), if any survive
            _signal_group(proc, signal.SIGKILL)
            self.returncode = proc.returncode
        with _LIVE_LOCK:
            _LIVE.discard(self)
        if self._stderr is not None:
            with contextlib.suppress(OSError):
                self._stderr.close()
            self._stderr = None


def _db_env_value(db: Path | str) -> str:
    """What ``db_env`` carries: a server database's URL as it is, a SQLite path absolute."""
    if isinstance(db, str) and "://" in db:
        return db
    return str(Path(db).resolve())


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError, OSError):
            if proc.poll() is None:
                proc.send_signal(sig)


@atexit.register
def stop_all() -> None:
    """Stop every process app still running (the interpreter's exit guard)."""
    with _LIVE_LOCK:
        live = list(_LIVE)
    for app in live:
        with contextlib.suppress(Exception):
            app.stop()


# ------------------------------------------------------------------------------ runtime --


class NoHttpClient:
    """The ``client`` of a gRPC-only process app (no HTTP port): it carries the rpc
    transport (``_lattice_rpc``) a gRPC login reads, and refuses every HTTP request."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def request(self, method: str, url: str, **kw: Any) -> Any:
        raise CheckError(f"{method} {url}: the app serves gRPC only (no HTTP port)")

    def __enter__(self) -> NoHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class ProcessTransport(HttpxTransport):
    """An httpx client of a running process app; a request the server cannot answer
    (it died) is a CheckError carrying its stderr. For a gRPC app (``ready="grpc"``)
    :meth:`rpc` is its unary-call transport (one bridge process, stopped with this
    transport); ``client`` stays the HTTP client when the app has an HTTP port too."""

    def __init__(self, app: ProcessApp) -> None:
        import httpx

        self.app = app
        self._rpc: Any = None
        client: Any
        if app.ready == "grpc" and app.grpc_port_env is None:
            client = NoHttpClient()
        else:
            client = httpx.Client(base_url=app.base_url, timeout=app.request_timeout)
        super().__init__(client)
        client._lattice_rpc = self.rpc  # what a gRPC login reaches the server through

    def rpc(self, protos: Sequence[str] | None = None) -> Any:
        """The app's gRPC transport (created on first use; ``protos`` are described by the
        first call)."""
        from lattice.spec.grpc import GrpcTransport

        if self.app.grpc_port is None:
            raise CheckError(
                f"process app {self.app.describe()!r} has no gRPC port: start it with "
                "ProcessApp(ready='grpc')"
            )
        if self._rpc is None:
            self._rpc = GrpcTransport(
                f"{self.app.host}:{self.app.grpc_port}",
                protos=tuple(protos or self.app.protos),
                root=self.app.proto_root,
                proto_paths=tuple(self.app.proto_paths),
            )
        elif protos and not self._rpc._described:
            self._rpc.protos = tuple(protos)
        return self._rpc

    def request(self, method: str, path: str, **kw: Any) -> Any:
        import httpx

        try:
            return super().request(method, path, **kw)
        except httpx.TransportError as err:
            if self.app.running:
                raise
            raise CheckError(
                f"process app {self.app.describe()!r} died during {method} {path} "
                f"({type(err).__name__}); stderr:\n{self.app.stderr_text() or '(empty)'}"
            ) from err

    def __exit__(self, *exc: object) -> None:
        try:
            if self._rpc is not None:
                self._rpc.close()
        finally:
            super().__exit__(*exc)


def as_process_app(app: Any) -> ProcessApp:
    if not isinstance(app, ProcessApp):
        raise CheckError(
            f"the process runtime serves a ProcessApp; the app factory answered "
            f"{type(app).__name__}"
        )
    return app


def open_process(app: Any, *, raise_errors: bool = False) -> ProcessTransport:
    """The runtime's transport: start the handle's process (once) and a new client of it.
    ``raise_errors`` has no meaning here (the server's errors are its answers)."""
    handle = as_process_app(app)
    handle.start()
    return ProcessTransport(handle)


def stop_process(app: Any) -> None:
    """The runtime's dispose hook: stop the handle's process (a no-op when not started)."""
    if isinstance(app, ProcessApp):
        app.stop()


def process_runtime(
    *,
    framework: str = "process",
    auth: AuthDriver | None = None,
    dispose: Sequence[Callable[[Any], None]] = (),
    services: Mapping[str, Callable[[Path], Any]] | None = None,
) -> AppRuntime:
    """The runtime of process apps (see the module docstring). ``framework`` names the
    app's stack for the report (``"go"``: layer A reads the app's ``go_src`` with
    :mod:`lattice.gocheck`; anything else but a Python framework: layer A is skipped);
    ``auth`` how callers log in (default: a bearer token from the check's ``login``);
    ``dispose``: more hooks, run after the process is stopped; ``services``: the app's
    background services, ``{name: (db_path) -> ServiceCmd}``
    (:mod:`lattice.spec.services`), which the spec's ``Await`` steps start."""
    return AppRuntime(
        framework=framework,
        transport=open_process,
        auth=auth if auth is not None else AuthDriver(),
        dispose=(stop_process, *dispose),
        seed_hook=None,
        openapi=None,
        one_server=True,
        services=dict(services or {}),
    )


def returns_process_app(app_factory: Any) -> bool:
    """Whether ``app_factory`` is annotated to answer a :class:`ProcessApp`."""
    import inspect
    import typing

    try:
        hints = typing.get_type_hints(app_factory)
        ret = hints.get("return")
    except Exception:  # noqa: BLE001 -- an unresolvable annotation: read it as text
        try:
            ret = inspect.signature(app_factory).return_annotation
        except (TypeError, ValueError):
            return False
    if isinstance(ret, str):
        return ret.rsplit(".", 1)[-1] == "ProcessApp"
    return isinstance(ret, type) and issubclass(ret, ProcessApp)


__all__ = [
    "NoHttpClient",
    "ProcessApp",
    "ProcessStartError",
    "ProcessTransport",
    "live_processes",
    "process_runtime",
    "returns_process_app",
    "stop_all",
]
