"""Layers R and B (and the process runtime) on the .NET app of ``tests/spec/dnapp``: the port of the Go S1 app
(``tests/spec/goapp``) for the .NET spike (docs/results/2026-10-18-dotnet-prereg.md, 4.4, Q8). The spec, its rules, the
seed and the programs are ``go_harness_rb``'s objects, imported (the same spec object on a different
``make_app``); only how the service is built and started is new, per ``HARNESS_API_DOTNET.md``::

    python -m lattice.spec check --spec tests.spec.dn_harness_rb:SPEC \\
        --app tests.spec.dn_harness_rb:make_app --db tests.spec.dn_harness_rb:build_db \\
        --runtime tests.spec.dn_harness_rb:RUNTIME --login tests.spec.dn_harness_rb:LOGIN \\
        --layers RB

``$DNAPP_OUT`` names a directory holding an already built ``dnapp.dll``; otherwise the module is built (a copy of
``dnapp/`` without ``bin``/``obj``, once per change of its sources) into the system temporary directory.
``DNAPP_MODE`` (safe | racy) and ``DNAPP_BUG`` (nocheck | nocancel | noauth | forbidden) pick a variant.
"""

# ruff: noqa: E501

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

from lattice.importer.transport import AuthDriver
from lattice.spec.process import ProcessApp, process_runtime
from tests.spec.go_harness_rb import PROFILE, RULES, SPEC, USERS, VARIANTS, build_db

__all__ = [
    "LOGIN",
    "PROFILE",
    "RULES",
    "RUNTIME",
    "SPEC",
    "USERS",
    "VARIANTS",
    "build_db",
    "build_dir",
    "make_app",
]

MODULE = Path(__file__).resolve().parent / "dnapp"
ASSEMBLY = "dnapp"
BUILD_ENV = "DNAPP_OUT"
BUILD_TIMEOUT_S = 900

RUNTIME = process_runtime(framework="dotnet", auth=AuthDriver.json("/login", USERS))
LOGIN = RUNTIME.auth.login


def _source_key() -> str:
    crc = 0
    for p in sorted(MODULE.rglob("*")):
        rel = p.relative_to(MODULE)
        if p.is_file() and not {"bin", "obj"} & set(rel.parts):
            crc = zlib.crc32(rel.as_posix().encode() + b"\0" + p.read_bytes() + b"\0", crc)
    return f"{crc:08x}"


def build_dir() -> Path:
    """The directory holding the built service: ``$DNAPP_OUT``, else built once per source key from a copy."""
    if env_out := os.environ.get(BUILD_ENV):
        return Path(env_out)
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise RuntimeError("dnapp: `dotnet` is not on PATH")
    root = Path(tempfile.gettempdir()) / f"build-dnapp-{_source_key()}"
    out = root / "out"
    if (out / f"{ASSEMBLY}.dll").is_file():
        return out
    shutil.rmtree(root, ignore_errors=True)
    src = root / "src"
    shutil.copytree(MODULE, src, ignore=shutil.ignore_patterns("bin", "obj"))
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    tmp = root / f"out-{os.getpid()}"
    proc = subprocess.run(  # noqa: S603 -- the module's own build
        [dotnet, "build", str(src / f"{ASSEMBLY}.csproj"), "-c", "Release", "-p:RestoreLockedMode=true", "-o", str(tmp), "--nologo"],
        cwd=src, capture_output=True, text=True, check=False, env=env, timeout=BUILD_TIMEOUT_S,
    )  # fmt: skip
    if proc.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError(
            f"dnapp: dotnet build failed:\n{(proc.stdout + proc.stderr).strip()[-3000:]}"
        )
    os.replace(tmp, out)
    return out


def make_app(db_path: Path) -> ProcessApp:
    """The .NET service on ``db_path``; ``DNAPP_MODE`` / ``DNAPP_BUG`` pick a variant."""
    out = build_dir()
    env = {
        "MODE": os.environ.get("DNAPP_MODE", "safe"),
        "DNAPP_BUG": os.environ.get("DNAPP_BUG", ""),
    }
    dotnet = shutil.which("dotnet") or "dotnet"
    return ProcessApp(
        [dotnet, str(out / f"{ASSEMBLY}.dll")],
        db_path=db_path,
        env=env,
        cwd=str(out),
        health_path="/health",
    )
