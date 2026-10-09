"""Background services of an app under check: how they start, and how they stop.

A spec names its services (``Spec.services``) and waits on them in scenarios
(:class:`~lattice.spec.model.Await`); the app's harness says how each one starts, as a
mapping ``{name: (db_path) -> ServiceCmd}``. Give it to ``check(..., services=...)``,
to ``process_runtime(..., services=...)``, or on the command line as ``--services
MOD:NAME``::

    from lattice.spec.services import ServiceCmd

    SERVICES = {
        "worker": lambda db: ServiceCmd(["./bin/worker"], env={"DATABASE_PATH": str(db)}),
        "scheduler": lambda db: ServiceCmd([sys.executable, "-m", "app.scheduler", str(db)]),
    }

A service runs in its own process group with the caller's environment plus
``ServiceCmd.env``; it is stopped as a whole -- SIGTERM, then SIGKILL after
``stop_timeout`` seconds -- and reaped, whatever happened (an ``atexit`` guard stops
whatever is still running when the interpreter exits).
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

STDERR_TAIL = 2000  # characters of a service's stderr a finding carries
POLL_S = 0.2  # how often an Await reads the database


@dataclass(frozen=True)
class ServiceCmd:
    """How one service starts on one database: ``argv`` run in ``cwd`` with the
    caller's environment plus ``env``; ``stop_timeout``: SIGTERM's grace before
    SIGKILL, seconds."""

    argv: Sequence[str]
    env: Mapping[str, str] | None = field(default=None, hash=False)
    cwd: Path | str | None = None
    stop_timeout: float = 5.0

    def describe(self) -> str:
        return " ".join(str(a) for a in self.argv)


Services = Mapping[str, Callable[[Path], ServiceCmd]]

_LIVE: set[RunningService] = set()
_LIVE_LOCK = threading.Lock()


def live_services() -> list[RunningService]:
    """The services started and not yet stopped (empty after every check)."""
    with _LIVE_LOCK:
        return [s for s in _LIVE if s.running]


class RunningService:
    """One started service (:func:`start_service`); :meth:`stop` ends it."""

    def __init__(self, name: str, cmd: ServiceCmd) -> None:
        self.name = name
        self.cmd = cmd
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (cmd.env or {}).items()})
        self._stderr: IO[bytes] | None = tempfile.TemporaryFile()  # noqa: SIM115 -- stop()
        self.returncode: int | None = None
        try:
            self._proc: subprocess.Popen[bytes] | None = subprocess.Popen(  # noqa: S603
                [str(a) for a in cmd.argv],
                cwd=None if cmd.cwd is None else str(cmd.cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr,
                start_new_session=True,  # its own process group: stopped as a whole
            )
        except BaseException:
            self._stderr.close()
            self._stderr = None
            raise
        self.pid = self._proc.pid
        with _LIVE_LOCK:
            _LIVE.add(self)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def poll(self) -> int | None:
        """The exit code once the service has exited, else None."""
        return None if self._proc is None else self._proc.poll()

    def stderr_text(self) -> str:
        """What the service wrote to stderr (its tail)."""
        if self._stderr is None:
            return ""
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            text = self._stderr.read().decode(errors="replace")
        except (OSError, ValueError):
            return ""
        return text if len(text) <= STDERR_TAIL else "..." + text[-STDERR_TAIL:]

    def stop(self) -> None:
        """Terminate the process group (SIGTERM, then SIGKILL) and reap it."""
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                _signal_group(proc, signal.SIGTERM)
                try:
                    proc.wait(self.cmd.stop_timeout)
                except subprocess.TimeoutExpired:
                    _signal_group(proc, signal.SIGKILL)
                    proc.wait()
            # the group's other members (children the service spawned), if any survive
            _signal_group(proc, signal.SIGKILL)
            self.returncode = proc.returncode
            self._proc = None
        with _LIVE_LOCK:
            _LIVE.discard(self)
        if self._stderr is not None:
            with contextlib.suppress(OSError):
                self._stderr.close()
            self._stderr = None


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError, OSError):
            if proc.poll() is None:
                proc.send_signal(sig)


def start_service(name: str, cmd: ServiceCmd) -> RunningService:
    """Start ``cmd`` as service ``name`` (raises OSError when it cannot run)."""
    if not isinstance(cmd, ServiceCmd):
        raise TypeError(f"service {name!r}: the harness answered {type(cmd).__name__}, "
                        "not a ServiceCmd")  # fmt: skip
    return RunningService(name, cmd)


@atexit.register
def stop_all() -> None:
    """Stop every service still running (the interpreter's exit guard)."""
    with _LIVE_LOCK:
        live = list(_LIVE)
    for service in live:
        with contextlib.suppress(Exception):
            service.stop()


__all__ = [
    "POLL_S",
    "RunningService",
    "ServiceCmd",
    "Services",
    "live_services",
    "start_service",
    "stop_all",
]
