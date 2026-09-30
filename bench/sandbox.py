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
   Reads are confined only where it matters for the benchmark's integrity: the
   harness's sources and tests (``<repo>/bench``, ``<repo>/tests`` -- the hidden
   cases live there), every earlier run's outputs (``<repo>/results``, the CLI's
   default out root: other runs' programs, outcomes and reports), the same
   directories of the main checkout when this is a worktree, and every directory
   registered with ``deny_reads_under`` (the CLI registers the run's out dir, which
   may lie outside ``results/``) are unreadable,
   EXCEPT the run's own workdir (SBPL: the last matching rule wins, so the
   workdir's allow follows the denies). Everything else stays readable: the
   interpreter needs its stdlib and site-packages (outside those directories).

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

_STDERR_LIMIT = 4000  # keep the LAST this-many chars: the error is at the end
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


_REPO = Path(__file__).resolve().parent.parent
# bench/ and tests/: the harness and the hidden cases; results/: earlier runs' outputs
# (bench.cli's default out root; the edit-slice runs write under bench/results/).
_READ_DENIED_DIRS = ("bench", "tests", "results")
_registered_read_denies: set[Path] = set()


def deny_reads_under(path: Path) -> None:
    """Make ``path`` (e.g. a benchmark run's out dir) unreadable to sandboxed code,
    except each run's own workdir inside it. Process-wide."""
    _registered_read_denies.add(Path(path).resolve())


def _main_checkout(repo: Path) -> Path | None:
    """The main checkout's root when ``repo`` is a worktree (its ``.git`` is a file
    naming ``<main>/.git/worktrees/<name>``), else None."""
    marker = repo / ".git"
    if not marker.is_file():
        return None
    try:
        text = marker.read_text().strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    pointed = Path(text.split(":", 1)[1].strip())
    if not pointed.is_absolute():
        pointed = (repo / pointed).resolve()
    if pointed.parent.name == "worktrees" and pointed.parent.parent.name == ".git":
        return pointed.parent.parent.parent
    return None


def read_denied_paths() -> tuple[Path, ...]:
    """Directories sandboxed code may not read (see the module docstring)."""
    roots = [_REPO]
    main = _main_checkout(_REPO)
    if main is not None:
        roots.append(main)
    paths = [root / name for root in roots for name in _READ_DENIED_DIRS]
    paths += sorted(_registered_read_denies)
    return tuple(dict.fromkeys(p.resolve() for p in paths))


def sandbox_profile(
    workdir: Path, *, confine_reads: bool = True, extra_reads: tuple[Path, ...] = ()
) -> str:
    """The Seatbelt profile: deny network, deny writes outside ``workdir``, and (with
    ``confine_reads``) deny reading the contents (files and directory listings; not
    metadata) of ``read_denied_paths()`` except ``workdir`` and
    ``extra_reads`` (files or directories the process itself needs, e.g. its binary)."""
    root = _sbpl_string(str(workdir.resolve()))
    profile = (
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
    if not confine_reads:
        return profile
    denied = " ".join(f"(subpath {_sbpl_string(str(p))})" for p in read_denied_paths())
    allowed = [f"(subpath {root})"] + [
        f"(subpath {_sbpl_string(str(Path(p).resolve()))})" for p in extra_reads
    ]
    # file-read-data (contents, directory listings), not file-read-metadata: a process
    # in a workdir nested inside a denied directory must still stat its ancestors
    # (realpath, getcwd, SQLite opening its database) to work at all. The allow names
    # the same operation as the deny: Seatbelt lets a later rule override an earlier
    # one only for the operation it names (a later `file-read*` does not undo it).
    return profile + f"(deny file-read-data {denied})\n(allow file-read-data {' '.join(allowed)})\n"


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
    script_name: str = "solution.py",
    env_extra: Mapping[str, str] | None = None,
) -> ScriptResult:
    """Write ``code`` to ``workdir/<script_name>`` (default ``solution.py``) and
    run it in an isolated subprocess.

    ``env_extra`` adds variables to the child's (otherwise minimal) environment,
    e.g. a secret the script reads and removes before running model code; it may not
    replace ``PATH``, ``HOME`` or ``TMPDIR``."""
    reserved = sorted(set(env_extra or {}) & {"PATH", "HOME", "TMPDIR"})
    if reserved:
        raise ValueError(f"env_extra may not set {', '.join(reserved)}")
    workdir.mkdir(parents=True, exist_ok=True)
    workdir = workdir.resolve()
    script_path = workdir / script_name
    script_path.write_text(code)
    tmpdir = workdir / ".tmp"
    tmpdir.mkdir(exist_ok=True)

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workdir),
        "TMPDIR": str(tmpdir),
        **(env_extra or {}),
    }

    argv = [sys.executable, "-I", script_name]
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
            stderr=(stderr or "")[-_STDERR_LIMIT:],
            timed_out=True,
        )
    finally:
        # Reap any stragglers the script left running in its group.
        if proc.returncode is not None:
            _kill_group(proc)

    return ScriptResult(
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=(stderr or "")[-_STDERR_LIMIT:],
        timed_out=False,
    )
