"""The ``lattice`` command: a thin dispatcher over the existing ``python -m`` entry points.

    lattice studio [DIR ...] [--port N] [--model M] [--mock [FIXTURES]]   # lattice.studio
    lattice check [--project DIR] [lattice.spec check flags]              # lattice.spec check
    lattice spec  check|fidelity|draft|lint|probe|review|ask ...          # lattice.spec
    lattice import ...                                                    # lattice.importer
    lattice mcp   serve|guard ...                                         # lattice.mcp
    lattice gogen build ... | lattice tsgen SOURCE ...                    # code generators
    lattice projects [DIR ...]                                            # discover projects

Every ``lattice X ...`` is the same as ``python -m lattice.X ...`` (argv forwarded
verbatim); the one convenience is ``lattice check``, which fills the harness flags from
the project's ``studio.toml``. Targets are imported lazily so ``lattice --help`` stays
fast. Install: ``uv tool install -e .`` (or ``uv run lattice ...`` inside the repo).
"""

from __future__ import annotations

import dataclasses
import sys
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path

PROG = "lattice"

USAGE = """\
usage: lattice [--version] [--help] <command> [args ...]

commands:
  studio    [DIR ...] [--port N] [--model M] [--mock [FIXTURES]]   run Lattice Studio
  check     [--project DIR] [--layers ABCDRX ...]   check an app against its spec
                                                   (harness from DIR/studio.toml)
  spec      check|fidelity|draft|lint|probe|review|ask ...   the spec tools
  import    ...                                    import an existing app into a spec
  mcp       serve|guard ...                        the MCP server / PreToolUse guard
  gogen     build SOURCE --profile M:A --out DIR   build a program into a Go binary
  tsgen     SOURCE --profile M:A --out DIR         emit TypeScript
  projects  [DIR ...]                              list Studio projects found

`lattice <command> --help` shows a command's options. `lattice X ...` is the same as
`python -m lattice.X ...`."""

# lattice.spec.__main__ dispatches these itself; listed for `lattice spec --help`
SPEC_COMMANDS = ("check", "fidelity", "draft", "lint", "probe", "review", "ask")

COMMANDS = ("studio", "check", "spec", "import", "mcp", "gogen", "tsgen", "projects")

STUDIO_TOML = "studio.toml"
SPEC_FILE = "spec.py"
TRACE_FILE = "spec.trace.json"

# [app] keys -> `lattice.spec check` flags, and the [check] keys that are also flags
_APP_FLAGS = {
    "app": "--app",
    "db": "--db",
    "login": "--login",
    "login_path": "--login-path",
    "runtime": "--runtime",
    "services": "--services",
    "database": "--database",
    "database_url": "--database-url",
}
_CHECK_FLAGS = {
    "layers": "--layers",
    "app_dir": "--app-dir",
    "package": "--package",
    "go_src": "--go-src",
    "x_k": "--x-k",
    "x_repeats": "--x-repeats",
    "x_budget": "--x-budget",
    "mint_token": "--mint-token",
    "natives": "--natives",
    "baseline": "--baseline",
}


def version() -> str:
    """The installed version, else the one in pyproject.toml, else ``unknown``."""
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _version

        try:
            return _version("lattice")
        except PackageNotFoundError:
            pass
    except ImportError:  # pragma: no cover
        pass
    try:
        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        return str(tomllib.loads(text)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


# ---- project discovery ------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Project:
    """A Studio project directory."""

    name: str
    path: Path
    configured: bool  # studio.toml hooks up an app (``[app] app`` and ``[app] db``)
    has_toml: bool = False


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _project_at(d: Path) -> Project | None:
    toml = d / STUDIO_TOML
    has_toml = toml.is_file()
    if not has_toml and not ((d / SPEC_FILE).is_file() and (d / TRACE_FILE).is_file()):
        return None
    app = (_read_toml(toml).get("app") if has_toml else None) or {}
    if not isinstance(app, dict):
        app = {}
    return Project(d.name, d, bool(app.get("app") and app.get("db")), has_toml)


def _subdirs(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def discover_projects(roots: Iterable[Path | str] = ()) -> list[Project]:
    """The Studio projects under ``roots`` (default: the cwd): each root itself and its
    children one level down; with no roots also ``cwd/dogfood/*``. A directory is a
    project when it has ``studio.toml``, or ``spec.py`` and ``spec.trace.json``. A
    broken ``studio.toml`` still counts, as not configured. Deduplicated, in order."""
    given = [Path(r) for r in roots]
    candidates: list[Path] = []
    for root in given or [Path.cwd()]:
        candidates.append(root)
        candidates.extend(_subdirs(root))
    if not given:
        candidates.extend(_subdirs(Path.cwd() / "dogfood"))
    seen: set[Path] = set()
    found: list[Project] = []
    for d in candidates:
        try:
            real = d.resolve()
        except OSError:
            continue
        if real in seen or not real.is_dir():
            continue
        seen.add(real)
        p = _project_at(real)
        if p is not None:
            found.append(p)
    return found


def _projects(args: list[str]) -> int:
    if args and args[0] in ("-h", "--help"):
        print(
            "usage: lattice projects [DIR ...]\n\nList Studio projects: directories with a\n"
            "studio.toml, or spec.py and spec.trace.json, in each DIR (default: the cwd,\n"
            "cwd/dogfood/*), and one level of their children."
        )
        return 0
    found = discover_projects(args)
    if not found:
        print(
            "no Lattice projects found. A project is a directory with spec.py and "
            'spec.trace.json,\nor a studio.toml: add one ([app] app = "mod:make_app", '
            'db = "mod:build_db")\nto hook an app up, then run `lattice studio DIR`.'
        )
        return 0
    w = max(len(p.name) for p in found)
    for p in found:
        state = "app hooked up" if p.configured else "no app"
        print(f"{p.name:<{w}}  {state:<13}  {p.path}")
    return 0


# ---- check: harness flags from studio.toml ---------------------------------------------------


def _flag_given(args: Sequence[str], flag: str) -> bool:
    return any(a == flag or a.startswith(flag + "=") for a in args)


def _fill_check_args(project: Path, args: list[str]) -> list[str] | str:
    """``args`` with the harness flags ``project``'s studio.toml supplies appended, or an
    error message. Also puts the project (and ``[app] sys_path``) on sys.path, as Studio
    does, so ``spec:SPEC`` and the harness modules import."""
    import os

    toml = project / STUDIO_TOML
    data = _read_toml(toml) if toml.is_file() else {}
    app = data.get("app") if isinstance(data.get("app"), dict) else {}
    chk = data.get("check") if isinstance(data.get("check"), dict) else {}
    out = list(args)
    missing: list[str] = []
    if not _flag_given(out, "--spec"):
        if (project / SPEC_FILE).is_file():
            out += ["--spec", "spec:SPEC"]
        else:
            missing.append(f"--spec (no {SPEC_FILE} in {project})")
    for key, flag in _APP_FLAGS.items():
        if _flag_given(out, flag):
            continue
        if app.get(key):
            out += [flag, str(app[key])]
        elif key in ("app", "db"):
            where = f"[app] {key} in {toml}" if toml.is_file() else f"{toml} (not found)"
            missing.append(f"{flag} (set {where})")
    for key, flag in _CHECK_FLAGS.items():
        if not _flag_given(out, flag) and chk.get(key) not in (None, ""):
            out += [flag, str(chk[key])]
    if missing:
        return "lattice check: missing " + "; ".join(missing)
    paths = [str(project)] + [
        str((project / p).resolve()) for p in (app.get("sys_path") or []) if isinstance(p, str)
    ]
    for p in reversed(paths):
        if p not in sys.path:
            sys.path.insert(0, p)
    if app.get("cwd"):
        try:
            os.chdir((project / str(app["cwd"])).resolve())
        except OSError as err:
            return f"lattice check: [app] cwd: {err}"
    return out


def _check(args: list[str]) -> int:
    project = Path.cwd()
    rest: list[str] = []
    it = iter(args)
    for a in it:
        if a == "--project":
            try:
                project = Path(next(it))
            except StopIteration:
                print("lattice check: --project needs a directory", file=sys.stderr)
                return 2
        elif a.startswith("--project="):
            project = Path(a.split("=", 1)[1])
        else:
            rest.append(a)
    if "-h" in rest or "--help" in rest:
        print(
            "usage: lattice check [--project DIR] [lattice.spec check flags]\n\n"
            "--project DIR  the project (default: the cwd); --spec, --app, --db, --login,\n"
            "               --runtime, --services, --database, --layers ... default to\n"
            "               DIR/spec.py and the [app] / [check] tables of DIR/studio.toml.\n"
        )
        from lattice.spec.__main__ import main as spec_main

        return _run(spec_main, ["check", "--help"])
    filled = _fill_check_args(project.resolve(), rest)
    if isinstance(filled, str):
        print(filled, file=sys.stderr)
        return 2
    from lattice.spec.__main__ import main as spec_main

    return _run(spec_main, ["check", *filled])


# ---- dispatch ---------------------------------------------------------------------------------


def _run(fn, argv: list[str]) -> int:
    try:
        rc = fn(argv)
    except SystemExit as exc:  # argparse --help / errors
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 2)
    return int(rc or 0)


def _spec(args: list[str]) -> int:
    if not args:
        print("usage: lattice spec {" + "|".join(SPEC_COMMANDS) + "} [args ...]", file=sys.stderr)
        return 2
    from lattice.spec.__main__ import main as spec_main

    return _run(spec_main, args)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    if cmd in ("-V", "--version", "version"):
        print(f"lattice {version()}")
        return 0
    if cmd not in COMMANDS:
        print(f"lattice: unknown command {cmd!r}\n\n{USAGE}", file=sys.stderr)
        return 2
    if cmd == "projects":
        return _projects(rest)
    if cmd == "check":
        return _check(rest)
    if cmd == "spec":
        return _spec(rest)
    if cmd == "studio":
        from lattice.studio.__main__ import main as target
    elif cmd == "import":
        from lattice.importer.cli import main as target
    elif cmd == "mcp":
        from lattice.mcp.__main__ import main as target
    elif cmd == "gogen":
        from lattice.gogen.__main__ import main as target
    else:
        from lattice.tsgen.__main__ import main as target
    return _run(target, rest)


if __name__ == "__main__":
    sys.exit(main())
