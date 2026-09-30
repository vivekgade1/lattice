"""Arm G of the 5b Go pilot: the model's ``handlers.go`` built, screened, vetted and
run in the sandbox (``bench.runners.run_api_go`` drives the repair loop).

Per round, under ``<workdir>/go_round<N>/``:

- ``module/``: the model's ``handlers.go``, the harness's ``main.go``
  (``platformkit.Serve(Register)``), a ``go.mod`` reaching the kit
  (``bench/api_kit_go``) through a ``replace``, and the kit's ``go.sum``;
- ``run/``: the case run's scratch (pristine DB, per-case DB copies) -- the ONLY
  directory the model's binary may write to;
- ``state/``: the launcher's per-case stderr logs, pid files and time-out markers
  (outside ``run/``, so the binary cannot forge or erase them);
- ``launch.py``: the launcher the proxy starts in place of the binary (outside
  ``run/``, so the binary cannot replace it).

**Screening** (``analyze``): ``bench/tools/goast`` parses ``handlers.go`` with go/ast,
measures it (lines, statements, AST nodes, as ``bench.metrics.program_metrics``
measures Python) and lists forbidden constructs -- opening another database
connection (``sql.Open``/``OpenDB``/``Register``, ``.Driver()``, a SQL driver import,
``database/sql/driver``) or a multi-statement SQL string -- which the runner feeds
back for repair before building.

**Building** (``build``/``vet``): ``go build -trimpath -buildvcs=false`` and ``go vet``
offline (GOPROXY=off, GOFLAGS=-mod=mod, GOWORK=off, GOTOOLCHAIN=local, the module
cache read-only), with cgo off (no C compiler runs on model code), a minimal
environment, and -- where ``sandbox-exec`` works -- under ``bench.sandbox``'s profile
for the module directory (no network; writes only in the module directory and the
Go build cache).

**Running** (``run_cases``): the cases run through ``bench.api_runner.run_cases_go``
(the proxy, ``X-Lattice-Sql-Count``, the same scorer as arm B), but the proxy starts
``launch.py`` instead of the binary. The launcher (harness code, unsandboxed) forks
the binary as a new session with a minimal environment, cwd ``run/``, stderr to its
``state/`` log, and the Python arm's resource limits (``RLIMIT_CPU`` = time limit +
5 s, ``RLIMIT_FSIZE`` 512 MB, ``RLIMIT_AS`` 4 GB where the platform allows), under
``bench.sandbox.sandbox_profile(run/)`` extended for a server (see
``run_profile``); it kills the binary's whole process group at the wall-clock limit
or when the proxy stops it. The whole case run shares one wall-clock budget
(``timeout_s``, 60 s by default: arm P's ``run_cases_fastapi`` budget); a request
still running when it ends makes the run a time-out, as arm P's does.

**Harness errors** (``GoHarnessError``, and ``lattice.gogen.build.GoBuildError`` from
``check_toolchain``) are raised, never scored: the Go toolchain missing or too old,
the kit (with an empty ``Register``) failing to build, vet or start on its own --
checked once per kit before any model output (``ensure_ready``) -- or goast failing
to build. Everything the model's file causes is the runner's to score, including a
build over its (generous) time limit and goast failing on the file (both reported as
a failed build). ``go vet`` is advisory: the runner never gates on it.

Arm B's Go backend (``bench.runners._run_go_cases``) runs its binary through the same
``run_cases`` -- sandbox, limits, budget and stderr capture -- so both arms' programs
are run, timed and diagnosed alike.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bench.sandbox import (
    _SANDBOX_EXEC,
    DEFAULT_MAX_ADDRESS_SPACE_BYTES,
    DEFAULT_MAX_FILE_BYTES,
    _sbpl_string,
    sandbox_exec_available,
    sandbox_profile,
)
from lattice.gogen.build import GO_ENV

ROOT = Path(__file__).resolve().parent
KIT_DIR = ROOT / "api_kit_go"
GOAST_DIR = ROOT / "tools" / "goast"

BUILD_TIMEOUT_S = 600.0  # a warm-cache build takes seconds; beyond this it's the machine
DEFAULT_CASE_TIMEOUT_S = 60.0  # arm P's run_cases_fastapi budget
_CPU_GRACE_S = 5  # as bench.sandbox
_ERROR_LIMIT = 3000  # as bench.api_runner
# lattice.gogen.build's offline settings (arm B's builds), plus: no user go env file,
# no checksum database, and no cgo (no C compiler ever runs on model code).
_OFFLINE_ENV = {**GO_ENV, "GOENV": "off", "GOSUMDB": "off", "CGO_ENABLED": "0"}
MAIN_GO = 'package main\n\nimport "platformkit"\n\nfunc main() { platformkit.Serve(Register) }\n'
_EMPTY_HANDLERS = (
    'package main\n\nimport (\n\t"net/http"\n\n\t"platformkit"\n)\n\n'
    "func Register(mux *http.ServeMux, k *platformkit.Kit) {}\n"
)


class GoHarnessError(RuntimeError):
    """A failure of the harness's own Go setup, not of the model's program."""


@dataclass(frozen=True)
class GoastReport:
    parse_error: str
    lines: int
    statements: int
    ast_nodes: int
    chars: int
    violations: tuple[tuple[int, str], ...]
    # goast itself failed on the file (crashed, timed out, bad output): scored as a
    # build error of the model's file, not a harness error; "" normally
    error: str = ""

    def render_violations(self) -> str:
        return "\n".join(f"handlers.go:{line}: {message}" for line, message in self.violations)


# --------------------------------------------------------------------------- toolchain


@functools.cache
def _go_env_value(name: str) -> str:
    proc = subprocess.run(  # noqa: S603
        ["go", "env", name],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    value = proc.stdout.strip()
    if proc.returncode != 0 or not value:
        raise GoHarnessError(f"`go env {name}` failed: {proc.stderr.strip()}")
    return value


def _go_env(module_dir: Path) -> dict[str, str]:
    home = module_dir / ".home"
    tmp = module_dir / ".tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "GOTMPDIR": str(tmp),
        "GOCACHE": _go_env_value("GOCACHE"),
        "GOMODCACHE": _go_env_value("GOMODCACHE"),
        **_OFFLINE_ENV,
    }


def build_profile(module_dir: Path) -> str:
    """``bench.sandbox.sandbox_profile(module_dir)`` (no network, writes only there)
    plus writes to the Go build cache (shared and content-addressed; the module
    cache stays read-only). Reads are not confined here: the compiler must read the
    kit (under bench/), and no model code runs during a build (cgo is off)."""
    cache = Path(_go_env_value("GOCACHE")).resolve()
    return (
        sandbox_profile(module_dir, confine_reads=False)
        + f"(allow file-write* (subpath {_sbpl_string(str(cache))}))\n"
    )


def run_profile(run_dir: Path, binary: Path) -> str:
    """The profile the model's binary runs under: ``bench.sandbox.sandbox_profile(
    run_dir)`` -- the Python arm's (all network denied, writes only in ``run_dir``, no
    reads of the harness's sources or the run's out dir but ``run_dir``) -- plus
    reading the binary itself,
    with the one opening a server needs, binding and accepting on the loopback
    interface (outbound connections, even to localhost, stay denied), and with
    fork and exec denied (a server needs neither; no process can outlive the run)."""
    exe = _sbpl_string(str(binary.resolve()))
    return sandbox_profile(run_dir, extra_reads=(binary,)) + (
        '(allow network-bind (local ip "localhost:*"))\n'
        '(allow network-inbound (local ip "localhost:*"))\n'
        "(deny process-fork)\n"
        f"(deny process-exec (require-not (literal {exe})))\n"
    )


def _go(
    args: list[str], module_dir: Path, timeout_s: float
) -> subprocess.CompletedProcess[str] | None:
    """``go <args>`` in the module, offline and sandboxed; None when it exceeded
    ``timeout_s`` (a pathological file: the caller scores it, never a harness error)."""
    module_dir = module_dir.resolve()
    argv = ["go", *args]
    if sandbox_exec_available():
        argv = [_SANDBOX_EXEC, "-p", build_profile(module_dir), *argv]
    try:
        return subprocess.run(  # noqa: S603
            argv,
            cwd=module_dir,
            env=_go_env(module_dir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise GoHarnessError(f"cannot run go: {exc}") from exc
    except subprocess.TimeoutExpired:
        return None


def _clean_output(text: str, module_dir: Path) -> str:
    """Compiler/vet output with the module's absolute path shown as ``.``."""
    for path in {str(module_dir), str(module_dir.resolve())}:
        text = text.replace(path, ".")
    return text.strip()


def write_module(code: str, module_dir: Path, kit_dir: Path = KIT_DIR) -> Path:
    """The module for one round: handlers.go (the model's), main.go, go.mod, go.sum."""
    module_dir.mkdir(parents=True, exist_ok=True)
    kit = kit_dir.resolve()
    (module_dir / "handlers.go").write_text(code)
    (module_dir / "main.go").write_text(MAIN_GO)
    (module_dir / "go.mod").write_text(
        f"module app\n\ngo 1.26.0\n\nrequire platformkit v0.0.0\n\nreplace platformkit => {kit}\n"
    )
    shutil.copyfile(kit / "go.sum", module_dir / "go.sum")
    return module_dir / "handlers.go"


def build(module_dir: Path) -> tuple[bool, str]:
    """(ok, output) of ``go build`` in the module: the binary is ``module_dir/app``."""
    proc = _go(
        ["build", "-trimpath", "-buildvcs=false", "-o", "app", "."], module_dir, BUILD_TIMEOUT_S
    )
    if proc is None:
        return False, f"go build exceeded its {BUILD_TIMEOUT_S:g}s time limit"
    return proc.returncode == 0, _clean_output(proc.stderr + proc.stdout, module_dir)


def vet(module_dir: Path) -> tuple[bool, str]:
    """(ok, findings) of ``go vet`` on the module (advisory for the runner)."""
    proc = _go(["vet", "."], module_dir, BUILD_TIMEOUT_S)
    if proc is None:
        return False, f"go vet exceeded its {BUILD_TIMEOUT_S:g}s time limit"
    return proc.returncode == 0, _clean_output(proc.stderr + proc.stdout, module_dir)


# --------------------------------------------------------------------------- goast

_SETUP_LOCK = threading.Lock()
_goast_binary: Path | None = None
_ready_kits: set[Path] = set()


def _goast() -> Path:
    global _goast_binary
    with _SETUP_LOCK:
        if _goast_binary is not None and _goast_binary.exists():
            return _goast_binary
        out = Path(tempfile.mkdtemp(prefix="lattice-goast-")) / "goast"
        env = {**os.environ, **_OFFLINE_ENV}
        env.pop("GOENV", None)
        try:
            proc = subprocess.run(  # noqa: S603
                ["go", "build", "-trimpath", "-buildvcs=false", "-o", str(out), "."],  # noqa: S607
                cwd=GOAST_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=BUILD_TIMEOUT_S,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise GoHarnessError(f"cannot build bench/tools/goast: {exc}") from exc
        if proc.returncode != 0:
            raise GoHarnessError(f"bench/tools/goast does not build:\n{proc.stderr}")
        _goast_binary = out
        return out


def analyze(path: Path) -> GoastReport:
    """goast's measurement and screen of one Go file. goast failing on the file
    (crash, time-out, unreadable output) is a report with ``error`` set and lines/
    chars only -- the runner scores it as a build error of the file. goast failing to
    BUILD is the harness's (``GoHarnessError`` from ``_goast``)."""
    goast = _goast()
    code = path.read_text()
    try:
        proc = subprocess.run(  # noqa: S603
            [str(goast), str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
        problem = "" if data is not None else f"exit {proc.returncode}: {proc.stderr[-1000:]}"
    except subprocess.TimeoutExpired:
        data, problem = None, "timed out"
    except ValueError as exc:
        data, problem = None, f"unreadable output: {exc}"
    if data is None:
        lines = sum(1 for ln in code.splitlines() if ln.strip() and not ln.strip().startswith("//"))
        return GoastReport(
            parse_error="",
            lines=lines,
            statements=0,
            ast_nodes=0,
            chars=len(code),
            violations=(),
            error=f"the Go source could not be analyzed (goast {problem})",
        )
    return GoastReport(
        parse_error=data["parse_error"],
        lines=data["lines"],
        statements=data["statements"],
        ast_nodes=data["ast_nodes"],
        chars=len(code),
        violations=tuple((v["line"], v["message"]) for v in data["violations"] or ()),
    )


# --------------------------------------------------------------------------- readiness


def ensure_ready(kit_dir: Path = KIT_DIR) -> None:
    """Check, once per kit per process and before any model output, that the harness
    itself works: the Go toolchain (``check_toolchain``: missing or too old raises
    ``GoBuildError``), goast, and the kit with an empty ``Register`` -- built, vetted
    and started through the launcher exactly as a model's program would be. Any
    failure here is the harness's (``GoHarnessError``), never the model's."""
    from lattice.gogen.build import check_toolchain

    check_toolchain()
    kit = kit_dir.resolve()
    with _SETUP_LOCK:
        if kit in _ready_kits:
            return
    _goast()
    root = Path(tempfile.mkdtemp(prefix="lattice-go-kit-probe-")).resolve()
    try:
        module = root / "module"
        write_module(_EMPTY_HANDLERS, module, kit)
        report = analyze(module / "handlers.go")
        if report.error or report.parse_error or report.violations:
            raise GoHarnessError(f"goast rejects the empty handlers: {report}")
        ok, output = build(module)
        if not ok:
            raise GoHarnessError(f"the Go kit does not build on its own:\n{output}")
        ok, output = vet(module)
        if not ok:
            raise GoHarnessError(f"the Go kit does not vet on its own:\n{output}")
        _probe_start(module / "app", root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    with _SETUP_LOCK:
        _ready_kits.add(kit)


def _probe_start(binary: Path, root: Path) -> None:
    import sqlite3

    from lattice.gogen.proxy import GoServer

    launcher = write_launcher(root, binary, DEFAULT_CASE_TIMEOUT_S)
    db = root / "run" / "probe.db"
    sqlite3.connect(db).close()
    server = GoServer(launcher, db)
    try:
        server.start()
    except Exception as exc:
        log = root / "state" / "probe.db.stderr"
        text = log.read_text() if log.exists() else ""
        raise GoHarnessError(f"the Go kit does not start in the sandbox: {exc}\n{text}") from exc
    finally:
        server.stop()
        _sweep(root / "state")


# --------------------------------------------------------------------------- launcher

_LAUNCHER = '''\
#!__PYTHON__ -IS
"""Arm G launcher (bench/go_arm.py): runs the model's binary sandboxed, limited and
supervised; started by lattice.gogen.proxy.GoServer in place of the binary."""
import os
import resource
import signal
import sys
import time

ARGV = __ARGV__
RUN = __RUN__
STATE = __STATE__
WALL_S = __WALL__
LIMITS = __LIMITS__

args = sys.argv[1:]
db = args[args.index("--db") + 1] if "--db" in args[:-1] else "app.db"
tag = os.path.basename(db)
env = {"PATH": "/usr/bin:/bin", "HOME": RUN, "TMPDIR": os.path.join(RUN, ".tmp")}
if os.environ.get("LATTICE_TRACE_SQL"):
    env["LATTICE_TRACE_SQL"] = os.environ["LATTICE_TRACE_SQL"]
err = os.open(os.path.join(STATE, tag + ".stderr"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
pid = os.fork()
if pid == 0:
    try:
        os.setsid()
        os.dup2(err, 2)
        os.close(err)
        os.chdir(RUN)
        for name, value in LIMITS:
            which = getattr(resource, name)
            try:
                _soft, hard = resource.getrlimit(which)
                if hard != resource.RLIM_INFINITY:
                    value = min(value, hard)
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                pass  # e.g. macOS refuses RLIMIT_AS; best effort, as bench.sandbox
        os.execve(ARGV[0], ARGV + args, env)
    finally:
        os._exit(127)
os.close(err)
# Only the binary holds the proxy's stdout pipe: when it dies, the proxy sees EOF.
null = os.open(os.devnull, os.O_WRONLY)
os.dup2(null, 1)
os.close(null)
with open(os.path.join(STATE, tag + ".pid"), "w") as f:
    f.write(str(pid))


def kill():
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def stop(signum, frame):
    kill()
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass
    os._exit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
deadline = time.monotonic() + WALL_S
while True:
    done, status = os.waitpid(pid, os.WNOHANG)
    if done:
        kill()  # anything it left in its group
        os._exit(os.waitstatus_to_exitcode(status) & 0xFF)
    if time.monotonic() >= deadline:
        open(os.path.join(STATE, tag + ".timeout"), "w").close()
        kill()
        os.waitpid(pid, 0)
        os._exit(124)
    time.sleep(0.02)
'''


def write_launcher(round_dir: Path, binary: Path, wall_s: float) -> Path:
    """``round_dir/launch.py``, running ``binary`` with ``round_dir/run`` as its only
    writable directory and ``round_dir/state`` for its logs."""
    round_dir = round_dir.resolve()
    run_dir, state_dir = round_dir / "run", round_dir / "state"
    (run_dir / ".tmp").mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    binary = binary.resolve()
    argv = [str(binary)]
    if sandbox_exec_available():
        argv = [_SANDBOX_EXEC, "-p", run_profile(run_dir, binary), str(binary)]
    limits = [
        ("RLIMIT_CPU", int(wall_s) + _CPU_GRACE_S),
        ("RLIMIT_FSIZE", DEFAULT_MAX_FILE_BYTES),
        ("RLIMIT_AS", DEFAULT_MAX_ADDRESS_SPACE_BYTES),
    ]
    source = (
        _LAUNCHER.replace("__PYTHON__", sys.executable)
        .replace("__ARGV__", repr(argv))
        .replace("__RUN__", repr(str(run_dir)))
        .replace("__STATE__", repr(str(state_dir)))
        .replace("__WALL__", repr(float(wall_s)))
        .replace("__LIMITS__", repr(limits))
    )
    launcher = round_dir / "launch.py"
    launcher.write_text(source)
    launcher.chmod(0o755)
    return launcher


def _sweep(state_dir: Path) -> None:
    """Kill every process group a launcher started (normally already gone)."""
    for pid_file in state_dir.glob("*.pid"):
        try:
            os.killpg(int(pid_file.read_text().strip()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


# --------------------------------------------------------------------------- cases


class _BudgetSpent(Exception):
    pass


def _tail(text: str) -> str:
    return text.strip()[-_ERROR_LIMIT:]


def run_cases(
    binary: Path,
    task: Any,
    *,
    db_builder: Callable[[Path], None],
    round_dir: Path,
    timeout_s: float = DEFAULT_CASE_TIMEOUT_S,
) -> Any:
    """The task's cases on ``binary`` (``bench.api_runner.run_cases_go``: the proxy,
    X-Lattice-Sql-Count, the shared scorer), with the binary sandboxed and supervised
    by ``launch.py`` and the whole run on one wall-clock budget of ``timeout_s``.

    Like arm P's sandboxed run: a run over budget is a time-out (not started,
    ``timed_out``); an app that does not start reports its stderr (a panic in
    ``Register``, say); and a case the app answered with a 5xx also carries the
    binary's stderr for that case (the kit logs a handler's panic and its stack
    there), as arm P's crash carries the server-side traceback."""
    import httpx

    from bench.api_runner import SERVER_ERROR_PREFIX, CaseResults, run_cases_go

    round_dir = round_dir.resolve()
    launcher = write_launcher(round_dir, binary, timeout_s)
    run_dir, state_dir = round_dir / "run", round_dir / "state"
    deadline = time.monotonic() + timeout_s
    spent = [False]

    def wrap(app: Any, db_path: Path, case: Any) -> Any:
        remaining = deadline - time.monotonic()
        if spent[0] or remaining <= 0:
            raise _BudgetSpent
        app.timeout_s = remaining

        async def guarded(scope: Any, receive: Any, send: Any) -> None:
            try:
                await app(scope, receive, send)
            except httpx.TimeoutException:
                spent[0] = True
                raise

        return guarded

    try:
        try:
            results = run_cases_go(
                launcher, task, db_builder=db_builder, workdir=run_dir, wrap=wrap
            )
        except _BudgetSpent:
            results = None
    finally:
        _sweep(state_dir)

    if results is None or spent[0] or any(state_dir.glob("*.timeout")):
        return CaseResults(
            cases=(),
            started=False,
            start_error=f"the app exceeded the {timeout_s:g}s time limit",
            timed_out=True,
        )

    def log(index: int) -> str:
        path = state_dir / f"case_{index:02d}.db.stderr"
        return path.read_text(errors="replace") if path.exists() else ""

    if not results.started:
        logs = sorted(state_dir.glob("case_*.db.stderr"))
        text = logs[-1].read_text(errors="replace") if logs else ""
        error = "the app did not start"
        if text.strip():
            error += ":\n" + _tail(text)
        return replace(results, start_error=error)

    cases = []
    for index, case in enumerate(results.cases):
        text = log(index)
        if case.reason.startswith(SERVER_ERROR_PREFIX) and text.strip():
            case = replace(case, reason=f"{case.reason}\nserver log:\n{_tail(text)}")
        cases.append(case)
    return replace(results, cases=tuple(cases))
