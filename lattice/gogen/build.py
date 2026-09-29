"""Build a Lattice API program into one Go binary (plan 5a Task 12).

`build_go_app(source, profile, out_dir)` checks the program for the Go target (a
rejected program raises GoBuildError with the diagnostics: natives L890, tabular L891,
T2 constructs Go lacks L892), lowers it, emits the Go module -- `handlers.go` and
`main.go` (lattice.gogen.endpoints), `t2.go` (the procedures, lattice.gogen.t2),
`go.mod` (`module latticeapp`, the runtime `lattice.dev/rt` through a `replace` to
lattice/gogen/rt) and the runtime's `go.sum` -- and runs `go build -trimpath -o app`
offline (GOFLAGS=-mod=mod, GOPROXY=off).

Builds are cached under `out_dir/.cache/<hash>/` (the module and its `app`), keyed by
the sha256 of the source, repr(profile), the runtime tree and the generated files (so a
generator change is a new key too); a hit returns without running go. The cache keeps
the CACHE_KEEP most recently used builds.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
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

__all__ = ["CACHE_KEEP", "GoBuild", "GoBuildError", "build_go_app", "cache_key", "prune_cache"]

RT = Path(__file__).resolve().parent / "rt"
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOWORK": "off", "GOTOOLCHAIN": "local"}
CACHE_KEEP = 20  # builds kept in out_dir/.cache (least recently used pruned first)
GO_VERSION = "1.26.0"


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


def cache_key(source: str, profile: Profile, files: dict[str, str]) -> str:
    """sha256 of (the source, repr(profile), the runtime tree, the generated files)."""
    h = hashlib.sha256()
    for part in (source, repr(profile), rt_digest()):
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


def _go_build(module_dir: Path) -> None:
    """gofmt the generated sources (for readers; a syntax error is left to go build),
    then go build -trimpath -o app in the module, offline; GoBuildError with its
    stderr."""
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
            ["go", "build", "-trimpath", "-o", "app", "."],  # noqa: S607
            cwd=module_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    except FileNotFoundError as err:
        raise GoBuildError("the go toolchain is not installed (go not on PATH)") from err
    if proc.returncode != 0:
        raise GoBuildError(f"go build failed:\n{proc.stderr}")


def prune_cache(cache: Path, keep: int = CACHE_KEEP) -> None:
    """Keep the `keep` most recently used builds under `cache` (by mtime; a hit touches
    its build); remove the others, and stale temporary build directories."""
    if not cache.is_dir():
        return
    entries = [p for p in cache.iterdir() if p.is_dir()]
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
