"""Build a Lattice API program into one Go binary (plan 5a Task 12).

`build_go_app(source, profile, out_dir)` checks the program for the Go target (a
rejected program raises GoBuildError with the diagnostics: natives L890, tabular L891,
T2 constructs Go lacks L892), lowers it, emits the Go module -- `handlers.go` and
`main.go` (lattice.gogen.endpoints), `t2.go` (the procedures, lattice.gogen.t2),
`go.mod` (`module latticeapp`, the runtime `lattice.dev/rt` through a `replace` to
lattice/gogen/rt) and the runtime's `go.sum` -- and runs `go build -trimpath -o app`
offline (GOFLAGS=-mod=mod, GOPROXY=off; -buildvcs=false: no git, and nothing of the
checkout's state in the binary).

Builds are cached under `out_dir/.cache/<hash>/` (the module and its `app`), keyed by
the sha256 of the source, repr(profile), the runtime tree, the generated files (so a
generator change is a new key too) and `go version` (a toolchain upgrade rebuilds); a
hit returns without running go. The cache keeps the CACHE_KEEP most recently used
builds; a `.tmp-*` build directory younger than TMP_GRACE_S is another process's build
in progress and is left alone.

Prerequisites: Go >= 1.26 on PATH, and the runtime's dependencies in the Go module cache
(builds never touch the network): run `cd lattice/gogen/rt && go mod download` once,
with network access. Without them the build raises GoBuildError saying so.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from lattice.api.ir import lower_api
from lattice.check import CheckResult, check_source
from lattice.diagnostics import Severity
from lattice.gogen.endpoints import emit_go_endpoints
from lattice.gogen.t2 import emit_go_t2
from lattice.gogen.types import GoTypes, api_types
from lattice.profile import Profile
from lattice.runtime import LatticeInternalError
from lattice.t2ir.lower import lower_t2

__all__ = [
    "CACHE_KEEP",
    "GoBuild",
    "GoBuildError",
    "build_go_app",
    "build_failure",
    "cache_key",
    "check_toolchain",
    "go_version",
    "prune_cache",
]

RT = Path(__file__).resolve().parent / "rt"
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOWORK": "off", "GOTOOLCHAIN": "local"}
CACHE_KEEP = 20  # builds kept in out_dir/.cache (least recently used pruned first)
TMP_GRACE_S = 600  # a younger .tmp-* build directory is a build in progress
GO_VERSION = "1.26.0"
GO_MIN = (1, 26)  # the oldest toolchain that builds the runtime (go.mod: go 1.26.0)
SETUP_HINT = (
    f"run `cd {RT} && go mod download` once, with network access (builds are offline: "
    f"GOPROXY=off); requires Go >= {GO_MIN[0]}.{GO_MIN[1]}"
)
# go build's messages when a module the runtime needs is not in the module cache
_COLD_CACHE = ("module lookup disabled by GOPROXY=off", "GOPROXY=off")


@dataclass(frozen=True)
class GoBuild:
    binary: Path
    check: CheckResult
    module_dir: Path


class GoBuildError(Exception):
    """The program does not build for Go: the checker's diagnostics (check is set), or
    `go build`'s stderr."""

    def __init__(self, message: str, check: CheckResult | None = None) -> None:
        super().__init__(message)
        self.check = check


def rt_digest() -> str:
    """A hash of the runtime module's tree (its Go sources, go.mod and go.sum)."""
    h = hashlib.sha256()
    for p in sorted(RT.rglob("*")):
        if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")):
            if "testdata" in p.relative_to(RT).parts:
                continue
            h.update(str(p.relative_to(RT)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


@functools.cache
def _go_version() -> str:
    try:
        proc = subprocess.run(  # noqa: S603
            ["go", "version"],  # noqa: S607
            env={**os.environ, **GO_ENV},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError:
        return "no go"
    return proc.stdout.strip()


def go_version() -> str:
    """`go version`'s output (the toolchain, its version and GOOS/GOARCH)."""
    return _go_version()


def cache_key(source: str, profile: Profile, files: dict[str, str]) -> str:
    """sha256 of (the source, repr(profile), the runtime tree, `go version`, the
    generated files)."""
    h = hashlib.sha256()
    for part in (source, repr(profile), rt_digest(), go_version()):
        h.update(part.encode() + b"\0")
    for name in sorted(files):
        h.update(name.encode() + b"\0" + files[name].encode() + b"\0")
    return h.hexdigest()


def generate(source: str, profile: Profile) -> tuple[CheckResult, dict[str, str]]:
    """Check for Go, lower, emit: (the check result, the module's files)."""
    check = check_source(source, profile, target="go")
    if not check.ok or check.program is None:
        errors = [d for d in check.diagnostics if d.severity is Severity.ERROR]
        text = "\n".join(d.render() for d in (errors or check.diagnostics))
        raise GoBuildError(f"the program does not check for Go:\n{text}", check)
    program = check.program
    api = program.profile.api
    if api is None:
        raise GoBuildError("the Go target builds API programs: the profile has no api", check)
    plans = lower_api(program)
    keys = {p.user_key for p in plans}
    tables, caller = api_types(api, keys.pop() if len(keys) == 1 else "id")
    module = None
    if program.t2 is not None:
        module = lower_t2(program.t2, ast.parse(program.source))
    records = () if module is None else tuple((r.name, r.fields) for r in module.records)
    types = GoTypes(records, tables, caller)
    handlers, main = emit_go_endpoints(plans, types, module, source)
    files = {"handlers.go": handlers, "main.go": main}
    if module is not None:
        files["t2.go"] = emit_go_t2(module, package="main", types=types)
    files["go.mod"] = (
        f"module latticeapp\n\ngo {GO_VERSION}\n\nrequire lattice.dev/rt v0.0.0\n\n"
        f"replace lattice.dev/rt => {RT}\n"
    )
    files["go.sum"] = (RT / "go.sum").read_text()
    return check, files


def _toolchain_version(version: str) -> tuple[int, int] | None:
    """(major, minor) of `go version`'s output ("go version go1.26.2 darwin/arm64"), or
    None when it cannot be read."""
    m = re.search(r"\bgo(\d+)\.(\d+)", version)
    return (int(m.group(1)), int(m.group(2))) if m else None


def check_toolchain(version: str | None = None) -> None:
    """GoBuildError with what to do when `go` is missing or older than GO_MIN."""
    version = go_version() if version is None else version
    if version == "no go":
        raise GoBuildError(
            f"the go toolchain is not installed (go not on PATH): install Go >= "
            f"{GO_MIN[0]}.{GO_MIN[1]}, then {SETUP_HINT}"
        )
    found = _toolchain_version(version)
    if found is not None and found < GO_MIN:
        raise GoBuildError(
            f"the go toolchain is too old ({version}): Lattice's Go runtime requires Go >= "
            f"{GO_MIN[0]}.{GO_MIN[1]} (builds use GOTOOLCHAIN=local, so go does not fetch "
            "a newer one)"
        )


def build_failure(stderr: str) -> GoBuildError:
    """The GoBuildError for go build's `stderr`: an actionable one when the module cache
    lacks the runtime's dependencies (a fresh machine: builds are offline)."""
    if any(m in stderr for m in _COLD_CACHE):
        return GoBuildError(
            "go build failed: the runtime's Go dependencies are not in the module cache "
            f"(a fresh machine?): {SETUP_HINT}.\n{stderr}"
        )
    return GoBuildError(f"go build failed:\n{stderr}")


def _go_build(module_dir: Path) -> None:
    """gofmt the generated sources (for readers; a syntax error is left to go build),
    then go build -trimpath -o app in the module, offline; GoBuildError with its
    stderr (with what to do when go is missing or too old, or its module cache cold)."""
    check_toolchain()
    env = {**os.environ, **GO_ENV}
    try:
        subprocess.run(  # noqa: S603
            ["gofmt", "-w", "."],  # noqa: S607
            cwd=module_dir,
            env=env,
            capture_output=True,
            check=False,
            timeout=300,
        )
    except FileNotFoundError:
        pass  # formatting is cosmetic
    try:
        proc = subprocess.run(  # noqa: S603
            ["go", "build", "-trimpath", "-buildvcs=false", "-o", "app", "."],  # noqa: S607
            cwd=module_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    except FileNotFoundError as err:
        raise GoBuildError(
            f"the go toolchain is not installed (go not on PATH): {SETUP_HINT}"
        ) from err
    if proc.returncode != 0:
        raise build_failure(proc.stderr)


def prune_cache(cache: Path, keep: int = CACHE_KEEP) -> None:
    """Keep the `keep` most recently used builds under `cache` (by mtime; a hit touches
    its build); remove the others, and stale temporary build directories (`.tmp-*`
    older than TMP_GRACE_S; a younger one is a build in progress)."""
    if not cache.is_dir():
        return
    now = time.time()
    entries = []
    for p in cache.iterdir():
        if not p.is_dir():
            continue
        if p.name.startswith(".tmp-"):
            try:
                stale = now - p.stat().st_mtime >= TMP_GRACE_S
            except FileNotFoundError:
                continue  # its build finished meanwhile
            if stale:
                shutil.rmtree(p, ignore_errors=True)
            continue
        entries.append(p)
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in entries[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def build_go_app(source: str, profile: Profile, out_dir: Path) -> GoBuild:
    """Check, generate and build the program: the binary (cached by content)."""
    check, files = generate(source, profile)
    cache = Path(out_dir).resolve() / ".cache"
    key = cache_key(source, profile, files)
    module_dir = cache / key[:24]
    binary = module_dir / "app"
    if binary.is_file():
        os.utime(module_dir)  # most recently used: kept by prune_cache
        return GoBuild(binary, check, module_dir)
    tmp = cache / f".tmp-{key[:24]}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        for name, text in files.items():
            (tmp / name).write_text(text)
        _go_build(tmp)
        try:
            tmp.rename(module_dir)
        except OSError:  # another process built the same program first: keep theirs
            if not binary.is_file():
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    prune_cache(cache)
    if not binary.is_file():
        raise LatticeInternalError(f"go build produced no binary in {module_dir}")
    return GoBuild(binary, check, module_dir)
