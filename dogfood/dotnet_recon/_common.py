"""Shared helpers of the Stage 0 recon scripts RC1-RC6 (docs/results/2026-10-18-dotnet-prereg.md, 5.1).

Nothing here writes into a target directory, a fixture, Streak or the main checkout: every build,
copy and temporary file lives under the scratch directory (``--scratch``, else
``$LATTICE_DNRECON_SCRATCH``, else a fresh temporary directory). No model is called, no Docker.
"""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_DOTNET_ROOT = "/opt/homebrew/opt/dotnet/libexec"
HERE = Path(__file__).resolve().parent


def dotnet_root() -> Path:
    return Path(os.environ.get("DOTNET_ROOT") or DEFAULT_DOTNET_ROOT)


def dotnet_env() -> dict[str, str]:
    env = dict(os.environ)
    env["DOTNET_ROOT"] = str(dotnet_root())
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["DOTNET_NOLOGO"] = "1"
    return env


def dotnet_exe() -> str:
    found = shutil.which("dotnet")
    if found:
        return found
    candidate = dotnet_root() / "bin" / "dotnet"
    if candidate.exists():
        return str(candidate)
    sys.exit("recon: no dotnet on PATH (DotnetToolchainMissing)")


def run(
    cmd: list[str], cwd: Path | None = None, stdin: str | None = None, timeout: float = 900
) -> tuple[int, str, str, float]:
    """(returncode, stdout, stderr, wall seconds); the timeout is a Python one (no `timeout` on macOS)."""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dotnet_env(),
            check=False,
        )
        out = (p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        out = (124, "", f"timeout after {timeout}s")
    return (*out, time.perf_counter() - t0)


def sdk_version() -> str:
    rc, out, _err, _t = run([dotnet_exe(), "--version"])
    return out.strip() if rc == 0 else ""


def scratch_dir(arg: str | None, name: str) -> Path:
    base = arg or os.environ.get("LATTICE_DNRECON_SCRATCH")
    path = Path(base) if base else Path(tempfile.mkdtemp(prefix="dnrecon-"))
    path = path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def source_files(root: Path, suffix: str) -> list[Path]:
    """Files under ``root`` ending in ``suffix``, ordinal path order, without bin/ and obj/."""
    found = []
    for p in root.rglob("*" + suffix):
        rel = p.relative_to(root).parts
        if p.is_file() and "bin" not in rel[:-1] and "obj" not in rel[:-1]:
            found.append(p)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def summarize(times: list[float]) -> dict[str, Any]:
    return {
        "runs": len(times),
        "min": round(min(times), 3),
        "median": round(statistics.median(times), 3),
        "max": round(max(times), 3),
        "all": [round(t, 3) for t in times],
    }


def emit(result: dict[str, Any], out: str | None) -> None:
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text)
    sys.stdout.write(text)
