"""dncheck: Lattice's Roslyn helper for ASP.NET Core + EF Core apps (the .NET counterpart of
:mod:`lattice.gocheck`).

The helper is a small C# program (``src/``: one ``PackageReference``,
``Microsoft.CodeAnalysis.CSharp`` 5.9.0, restored in locked mode) that reads a target project's
``*.cs`` files with Roslyn and answers ``lattice.dnextract/1`` -- the ``lattice.goextract/1``
key set plus one ``dotnet`` key -- on stdout. **The target is never restored, built or
written**: the compilation is the project's own source, the implicit global usings its csproj
implies and the *installed reference packs* only, so the output is the same whether or not the
target was ever restored, on any path, under any locale. What a package would have told (EF
Core, FluentValidation) is read by name; what cannot be read is listed ``unread``.

Usage::

    doc = extract_json(project_dir)                 # lattice.dnextract/1 (dict)
    doc = extract_json(project_dir, workflow=True)  # plus the workflow keys (the workflow pass)
    doc = extract_json(project_dir, policy=True)    # plus columns[].json, raises[].bind

The helper is built on demand, once per hash of its sources, its lock file and
``dotnet --version``, **in a copy of ``src/`` under the cache directory**
(``$LATTICE_DNCHECK_CACHE``, else ``~/.cache/lattice/dncheck/<key>/``): this repository's
``src/`` never gets a ``bin/`` or ``obj/``. The newest four builds are kept.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent / "src"
CACHE_KEEP = 4
BUILD_TIMEOUT_S = 900
RUN_TIMEOUT_S = 300
DOTNET_ENV = {
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    "MSBUILDDISABLENODEREUSE": "1",
}
BUILD_CMD = [
    "build",
    "-c",
    "Release",
    "-p:RestoreLockedMode=true",
    "--disable-build-servers",
    "-nodeReuse:false",
    "-v",
    "q",
    "-nologo",
]


class DnCheckError(Exception):
    """The helper could not be built or run, or answered something unreadable."""


class DotnetToolchainMissing(DnCheckError):
    """No .NET SDK on ``PATH`` (layer A then skips with a note, as Go does without ``go``)."""


def _env() -> dict[str, str]:
    return {**os.environ, **DOTNET_ENV}


def _dotnet() -> str:
    exe = shutil.which("dotnet")
    if exe is None:
        raise DotnetToolchainMissing("dncheck needs the .NET SDK: `dotnet` is not on PATH")
    return exe


@functools.lru_cache(maxsize=1)
def sdk_version() -> str:
    """``dotnet --version`` (recorded in the extract as ``dotnet.sdk``)."""
    proc = subprocess.run(
        [_dotnet(), "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=_env(),
        timeout=60,
    )  # noqa: S603
    if proc.returncode != 0 or not proc.stdout.strip():
        raise DotnetToolchainMissing(
            f"`dotnet --version` failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip().splitlines()[-1].strip()


def source_hash() -> str:
    """The build cache key: the helper's sources, its lock file and the SDK version."""
    h = hashlib.sha256()
    for p in sorted(SRC.rglob("*")):
        rel = p.relative_to(SRC)
        if p.is_file() and rel.parts[0] not in ("bin", "obj"):
            h.update(rel.as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    h.update(sdk_version().encode())
    return h.hexdigest()


def cache_dir() -> Path:
    """Where built helpers are kept: ``$LATTICE_DNCHECK_CACHE``, else
    ``$XDG_CACHE_HOME/lattice/dncheck`` (``~/.cache/lattice/dncheck``)."""
    if env := os.environ.get("LATTICE_DNCHECK_CACHE"):
        return Path(env)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "lattice" / "dncheck"


def build_dncheck() -> Path:
    """``dncheck.dll``, built with ``dotnet build -c Release -p:RestoreLockedMode=true`` once per
    source hash, in a copy of ``src/`` under the cache directory (a failed restore or build
    raises :class:`DnCheckError` with the tail of the output: it is never skipped silently)."""
    key = source_hash()[:24]
    out = cache_dir() / key / "dncheck.dll"
    if out.is_file():
        return out
    exe = _dotnet()
    cache_dir().mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".build-{key}-", dir=cache_dir()))
    try:
        shutil.copytree(SRC, work / "src", ignore=shutil.ignore_patterns("bin", "obj"))
        try:
            proc = subprocess.run(  # noqa: S603
                [exe, *BUILD_CMD, "-o", str(work / "out")],
                cwd=work / "src",
                capture_output=True,
                text=True,
                check=False,
                env=_env(),
                timeout=BUILD_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as err:
            raise DnCheckError(
                f"dotnet build of dncheck timed out after {BUILD_TIMEOUT_S}s"
            ) from err
        if proc.returncode != 0 or not (work / "out" / "dncheck.dll").is_file():
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
            raise DnCheckError(f"dotnet build of dncheck failed (exit {proc.returncode}):\n{tail}")
        final = cache_dir() / key
        shutil.rmtree(final, ignore_errors=True)
        os.replace(work / "out", final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    prune_cache(cache_dir())
    return out


def prune_cache(cache: Path, keep: int = CACHE_KEEP) -> None:
    """Keep the ``keep`` most recently built helpers under ``cache``."""
    builds = sorted(
        (p for p in cache.iterdir() if p.is_dir() and (p / "dncheck.dll").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in builds[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def run(command: str, payload: dict[str, Any], timeout: float = RUN_TIMEOUT_S) -> str:
    """Run ``dotnet dncheck.dll <command>`` with ``payload`` as JSON on stdin; stdout on success."""
    dll = build_dncheck()
    try:
        proc = subprocess.run(  # noqa: S603
            [_dotnet(), str(dll), command],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as err:
        raise DnCheckError(f"dncheck {command} timed out after {timeout}s") from err
    if proc.returncode != 0:
        raise DnCheckError(
            f"dncheck {command} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def extract_json(
    root: Path | str,
    *,
    workflow: bool = False,
    policy: bool = False,
    workspace: Path | str | None = None,
    timeout: float = RUN_TIMEOUT_S,
) -> dict[str, Any]:
    """``dncheck extract`` on the project directory ``root`` (read-only on the target).
    ``workspace`` is the import root a ``<ProjectReference>`` must stay inside (default: ``root``;
    a referenced project outside it is ``unread``)."""
    path = Path(root).resolve()
    if not path.is_dir():
        raise DnCheckError(f"no project directory at {path}")
    payload: dict[str, Any] = {
        "root": str(path),
        "sdk": sdk_version(),
        "workflow": workflow,
        "policy": policy,
    }
    if workspace is not None:
        payload["workspace"] = str(Path(workspace).resolve())
    text = run("extract", payload, timeout)
    try:
        data = json.loads(text)
    except ValueError as err:
        raise DnCheckError(f"dncheck extract answered invalid JSON: {err}") from err
    if not isinstance(data, dict) or data.get("schema") != "lattice.dnextract/1":
        raise DnCheckError("dncheck extract answered something that is not lattice.dnextract/1")
    return data


__all__ = [
    "DnCheckError",
    "DotnetToolchainMissing",
    "build_dncheck",
    "cache_dir",
    "extract_json",
    "run",
    "sdk_version",
    "source_hash",
]
