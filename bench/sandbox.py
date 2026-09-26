"""Subprocess sandbox for running untrusted model-written (pandas) scripts.

The baseline arm is the only place model text is ever executed (see the
plan's global constraint: no eval/exec/compile of model text in ``lattice/``).

Isolation layers, in order of strength:

1. A private working directory, no inherited environment beyond PATH/HOME
   (and a ``TMPDIR`` inside the workdir), and an isolated interpreter (``-I``).
2. The script runs as the leader of a new session/process group
   (``start_new_session=True``); on timeout the *whole group* is SIGKILLed, so
   grandchildren it spawned cannot outlive the run (nor hold the output pipes
   open and stall the harness).
3. Resource limits set in the child before exec: ``RLIMIT_CPU`` (timeout + 5 s),
   ``RLIMIT_FSIZE`` (512 MB by default) and ``RLIMIT_AS`` (4 GB) where the
   platform supports it — macOS rejects ``RLIMIT_AS``, so it is best-effort.
4. On macOS, when ``/usr/bin/sandbox-exec`` exists and works, the interpreter
   runs under a generated Seatbelt (SBPL) profile that denies all network
   access and denies file writes anywhere except the workdir and a handful of
   device nodes (``/dev/null`` etc.). The script's temp dir (``TMPDIR``) is a
   private ``.tmp`` directory inside the workdir, so it is covered too.
   **Reads outside the workdir remain allowed**: the interpreter needs the
   stdlib and site-packages, so the profile does not try to confine reads.

Fallback: without ``sandbox-exec`` (Linux, or a macOS where it is missing or
cannot be applied — e.g. when already inside a sandbox) only layers 1–3
apply, which do not stop network access or writes elsewhere on disk. For a
live benchmark run on Linux, run the whole harness inside a disposable
container (e.g. ``docker run --network=none`` with only the results directory
mounted read-write).
"""

from __future__ import annotations

import functools
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_STDERR_LIMIT = 4000
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"

DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_ADDRESS_SPACE_BYTES = 4 * 1024 * 1024 * 1024
_CPU_GRACE_S = 5


@dataclass(frozen=True)
class ScriptResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def _sbpl_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandbox_profile(workdir: Path) -> str:
    """The Seatbelt profile: deny network, deny writes outside ``workdir``."""
    root = _sbpl_string(str(workdir.resolve()))
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny network*)\n"
        f"(deny file-write* (require-not (subpath {root})))\n"
        "(allow file-write*\n"
        f"  (subpath {root})\n"
        '  (literal "/dev/null")\n'
        '  (literal "/dev/zero")\n'
        '  (literal "/dev/dtracehelper")\n'
        '  (regex #"^/dev/tty"))\n'
    )


@functools.cache
def sandbox_exec_available() -> bool:
    """True when ``sandbox-exec`` exists *and* can apply a profile here.

    Applying a profile fails when the harness itself already runs inside a
    sandbox; in that case we fall back to the rlimit-only isolation.
    """
    if sys.platform != "darwin" or not os.path.exists(_SANDBOX_EXEC):
        return False
    try:
        probe = subprocess.run(
            [_SANDBOX_EXEC, "-p", "(version 1)(allow default)(deny network*)", "/usr/bin/true"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _limits_preexec(cpu_s: int, max_file_bytes: int) -> Callable[[], None]:
    def apply() -> None:
        import resource

        limits = [
            (resource.RLIMIT_CPU, cpu_s),
            (resource.RLIMIT_FSIZE, max_file_bytes),
            (resource.RLIMIT_AS, DEFAULT_MAX_ADDRESS_SPACE_BYTES),
        ]
        for which, value in limits:
            try:
                _soft, hard = resource.getrlimit(which)
                if hard != resource.RLIM_INFINITY:
                    value = min(value, hard)
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                # e.g. macOS refuses RLIMIT_AS; best effort.
                pass

    return apply


def _kill_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_python_script(
    code: str,
    workdir: Path,
    timeout_s: float = 60.0,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ScriptResult:
    """Write ``code`` to ``workdir/solution.py`` and run it in an isolated subprocess."""
    workdir.mkdir(parents=True, exist_ok=True)
    workdir = workdir.resolve()
    script_path = workdir / "solution.py"
    script_path.write_text(code)
    tmpdir = workdir / ".tmp"
    tmpdir.mkdir(exist_ok=True)

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workdir),
        "TMPDIR": str(tmpdir),
    }

    argv = [sys.executable, "-I", "solution.py"]
    if sandbox_exec_available():
        argv = [_SANDBOX_EXEC, "-p", sandbox_profile(workdir), *argv]

    cpu_s = int(timeout_s) + _CPU_GRACE_S
    proc = subprocess.Popen(
        argv,
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=_limits_preexec(cpu_s, max_file_bytes),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Something outside the group still holds the pipes; give up on output.
            proc.kill()
            stdout, stderr = "", ""
        return ScriptResult(
            returncode=-1,
            stdout=stdout or "",
            stderr=(stderr or "")[:_STDERR_LIMIT],
            timed_out=True,
        )
    finally:
        # Reap any stragglers the script left running in its group.
        if proc.returncode is not None:
            _kill_group(proc)

    return ScriptResult(
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=(stderr or "")[:_STDERR_LIMIT],
        timed_out=False,
    )
