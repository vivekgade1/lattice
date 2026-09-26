"""Subprocess sandbox for running untrusted model-written (pandas) scripts.

The baseline arm is the only place model text is ever executed (see the
plan's global constraint: no eval/exec/compile of model text in ``lattice/``).
Isolation here means: a private working directory, no inherited environment
beyond PATH/HOME, an isolated interpreter (``-I``), and a wall-clock timeout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_STDERR_LIMIT = 4000


@dataclass(frozen=True)
class ScriptResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def run_python_script(code: str, workdir: Path, timeout_s: float = 60.0) -> ScriptResult:
    """Write ``code`` to ``workdir/solution.py`` and run it in an isolated subprocess."""
    workdir.mkdir(parents=True, exist_ok=True)
    script_path = workdir / "solution.py"
    script_path.write_text(code)

    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(workdir)}

    try:
        proc = subprocess.run(
            [sys.executable, "-I", "solution.py"],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return ScriptResult(
            returncode=-1,
            stdout=stdout,
            stderr=stderr[:_STDERR_LIMIT],
            timed_out=True,
        )

    stderr = proc.stderr[:_STDERR_LIMIT] if proc.stderr else ""
    return ScriptResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=stderr,
        timed_out=False,
    )
