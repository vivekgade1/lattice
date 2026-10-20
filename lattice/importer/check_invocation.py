"""Finding an app's harness from the project's own check invocation (workflow layer, round 2,
R2-D1 and R2-D2).

A project that gates its builds with ``python -m lattice.spec check --app mod:fn --db mod:fn
...`` has already said how its app is started, seeded, logged into and served. This module
reads that text, never runs it, and imports the named ``module:attr`` references, so the
workflow layer runs the same harness the checker does.

Sources, in order; the first one found wins, and once one is found but cannot be resolved
the run is static with a stated reason (no later source is tried, nothing is guessed):

1. ``studio.toml`` ``[app]`` with ``app`` and ``db`` (an ``[app]`` with some of the harness
   keys but not both is unresolvable);
2. a check invocation: ``python -m lattice.spec check`` in the files of the ``studio.toml``
   ``[rebuild] gates`` that are files under the root, else the root ``check.sh``. The text is
   split into commands (newline, ``;``, ``&&``, ``||``, ``|``, outside quotes), tokenised with
   ``shlex``, and ``"$@"`` is replaced by the arguments of the last preceding ``set --``;
3. a root ``harness.py`` with ``make_app`` and ``build_db`` (round 1, unchanged);
4. nothing: static, the reason lists the sources looked for.

Nothing here executes a shell, a script or a command. Importing ``module:attr`` runs project
code in this process, exactly as the checker does.
"""

from __future__ import annotations

import shlex
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCES_LOOKED_FOR = (
    "studio.toml [app] (app and db), a [rebuild] gate file with a `python -m lattice.spec "
    "check` invocation, check.sh with one, harness.py (make_app and build_db)"
)
SUPPORTED_DATABASES = ("mysql", "sqlite")
# the flags the invocation is read for (each takes a value) ...
VALUE_FLAGS = ("app", "db", "login", "runtime", "services", "database")
# ... and the two whose presence alone matters
PRESENCE_FLAGS = ("login-path", "mint-token")
_SHELL_WORDS = {"then", "do", "else", "elif", "if", "while", "until", "!", "{", "exec"}


# ---- reading the text -----------------------------------------------------------------------


def split_commands(text: str) -> list[str]:
    """The commands of a shell text: split on newlines, ``;``, ``&&``, ``||`` and ``|``
    outside quotes; a backslash-newline joins lines; comments are dropped."""
    out: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    quote = ""
    while i < n:
        c = text[i]
        if quote == "'":
            cur.append(c)
            if c == "'":
                quote = ""
        elif quote == '"':
            if c == "\\" and i + 1 < n:
                if text[i + 1] == "\n":
                    i += 2
                    continue
                cur.append(c + text[i + 1])
                i += 2
                continue
            cur.append(c)
            if c == '"':
                quote = ""
        elif c == "\\" and i + 1 < n:
            if text[i + 1] == "\n":
                i += 2
                continue
            cur.append(c + text[i + 1])
            i += 2
            continue
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "#" and (not cur or cur[-1].isspace()):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c in ";\n" or (c in "&|" and text[i + 1 : i + 2] == c) or (c == "|"):
            out.append("".join(cur))
            cur = []
            i += 2 if (c in "&|" and text[i + 1 : i + 2] == c) else 1
            continue
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def _tokens(command: str) -> list[str] | None:
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return None


@dataclass
class Invocation:
    """The flags read from one ``python -m lattice.spec check`` command."""

    flags: dict[str, str | None] = field(default_factory=dict)
    presence: set[str] = field(default_factory=set)
    problem: str | None = None  # why the command cannot be read

    def key(self) -> tuple[Any, ...]:
        return (
            tuple((f, self.flags.get(f)) for f in VALUE_FLAGS),
            tuple(sorted(self.presence)),
        )


def _is_check(tokens: list[str]) -> int | None:
    for i in range(len(tokens) - 2):
        if tokens[i : i + 3] == ["-m", "lattice.spec", "check"]:
            return i + 3
    return None


def _flags_of(args: list[str]) -> Invocation:
    inv = Invocation()
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("--") and len(tok) > 2:
            name, eq, value = tok[2:].partition("=")
            if name in VALUE_FLAGS or name in PRESENCE_FLAGS:
                if not eq:
                    nxt = args[i + 1] if i + 1 < len(args) else None
                    if nxt is not None and not nxt.startswith("--"):
                        value = nxt
                        i += 1
                    else:
                        value = ""
                if name in PRESENCE_FLAGS:
                    inv.presence.add(name)
                else:
                    inv.flags[name] = value
        i += 1
    return inv


def parse_invocations(text: str) -> list[Invocation]:
    """The ``python -m lattice.spec check`` commands of a script, in order, with their flags
    (``"$@"`` replaced by the last preceding ``set --``)."""
    found: list[Invocation] = []
    set_args: list[str] = []
    for command in split_commands(text):
        toks = _tokens(command)
        if toks is None:
            if "lattice.spec" in command and "check" in command:
                found.append(Invocation(problem="a command with unbalanced quotes"))
            continue
        while toks and toks[0] in _SHELL_WORDS:
            toks = toks[1:]
        if toks[:2] == ["set", "--"]:
            set_args = [t for t in toks[2:] if t not in ("$@", "$*")]
            continue
        start = _is_check(toks)
        if start is None:
            continue
        args: list[str] = []
        for tok in toks[start:]:
            if tok in ("$@", "$*"):
                args += set_args
            else:
                args.append(tok)
        found.append(_flags_of(args))
    return found


# ---- the result -----------------------------------------------------------------------------


@dataclass
class Discovery:
    """What was found: the objects when resolved, else why not."""

    source: str | None = None  # studio.toml | check.sh | harness.py | None
    file: str | None = None  # relative to the root
    reason: str | None = None  # why the run is static (None when a harness was found)
    refs: dict[str, str | None] = field(default_factory=dict)
    database: str | None = None  # named by the project (None: the extract's dialect)
    make_app: Any = None
    build_db: Any = None
    login: Any = None
    runtime: Any = None
    services: Mapping[str, Any] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.reason is None and self.make_app is not None

    def describe(self) -> dict[str, Any]:
        """Plain data (no objects): what the probes compare."""
        return {
            "source": self.source,
            "file": self.file,
            "reason": self.reason,
            "refs": dict(sorted(self.refs.items())),
            "resolved": [
                n
                for n, obj in (
                    ("app", self.make_app),
                    ("db", self.build_db),
                    ("login", self.login),
                    ("runtime", self.runtime),
                )
                if obj is not None
            ],
            "services": sorted(self.services),
            "database": self.database,
        }


def _import(root: Path, ref: str) -> Any:
    from lattice.importer.frontends.go_gin import _harness_attr

    return _harness_attr(root, ref)


class _Unresolvable(Exception):
    """A found source that cannot be used; the message is the reason."""


def _load(root: Path, ref: str) -> Any:
    if ":" not in ref or not ref.split(":", 1)[0] or not ref.split(":", 1)[1]:
        raise _Unresolvable(f"`{ref}` is not module:attr")
    try:
        return _import(root, ref)
    except (Exception, SystemExit) as err:  # noqa: BLE001 -- named, never a traceback
        raise _Unresolvable(f"`{ref}` cannot be imported: {type(err).__name__}") from err


def _runtime(root: Path, ref: str) -> Any:
    if ref in ("process", "go", "dotnet"):
        from lattice.spec.process import process_runtime

        return process_runtime() if ref == "process" else process_runtime(framework=ref)
    from lattice.importer.transport import AppRuntime

    got = _load(root, ref)
    if callable(got) and not isinstance(got, AppRuntime):
        try:
            got = got()
        except Exception as err:  # noqa: BLE001
            raise _Unresolvable(f"`{ref}` cannot be called: {type(err).__name__}") from err
    return got


def _services(root: Path, ref: str) -> dict[str, Any]:
    got = _load(root, ref)
    if not isinstance(got, Mapping) and callable(got):
        try:
            got = got()
        except Exception as err:  # noqa: BLE001
            raise _Unresolvable(f"`{ref}` cannot be called: {type(err).__name__}") from err
    if not isinstance(got, Mapping):
        raise _Unresolvable(f"`{ref}` is not a mapping of service name -> command")
    return dict(got)


def _dynamic_value(flag: str, value: str) -> str | None:
    if "$" in value or "`" in value:
        return f"--{flag} {value!r} holds a shell variable or substitution: it is not resolved"
    return None


def resolve_invocation(root: Path, inv: Invocation, file: str) -> Discovery:
    """Import the references an invocation names (``source`` = ``check.sh``)."""
    refs: dict[str, str | None] = {
        f: inv.flags.get(f) or None for f in VALUE_FLAGS if f != "database"
    }
    base = Discovery(source="check.sh", file=file, refs=refs)
    try:
        if inv.problem:
            raise _Unresolvable(inv.problem)
        for flag in VALUE_FLAGS:
            value = inv.flags.get(flag)
            if value and (why := _dynamic_value(flag, value)):
                raise _Unresolvable(why)
        for flag in ("app", "db"):
            if not inv.flags.get(flag):
                raise _Unresolvable(f"the invocation has no --{flag}")
        if inv.presence and not inv.flags.get("login"):
            names = ", ".join(f"--{n}" for n in sorted(inv.presence))
            raise _Unresolvable(f"the invocation logs in with {names} and has no --login")
        base.make_app = _load(root, inv.flags["app"] or "")
        base.build_db = _load(root, inv.flags["db"] or "")
        login = inv.flags.get("login")
        base.login = _load(root, login) if login else None
        runtime = inv.flags.get("runtime")
        base.runtime = _runtime(root, runtime) if runtime else None
        services = inv.flags.get("services")
        base.services = _services(root, services) if services else {}
        base.database = inv.flags.get("database") or "sqlite"
    except _Unresolvable as err:
        return Discovery(reason=f"{file}: {err}")
    return base


# ---- the sources ----------------------------------------------------------------------------


def _gate_files(root: Path, gates: Any) -> list[Path]:
    files: list[Path] = []
    for entry in gates if isinstance(gates, list) else []:
        if not isinstance(entry, str):
            continue
        tokens = _tokens(entry) or []
        for cand in (entry, tokens[0] if tokens else ""):
            if not cand:
                continue
            path = (root / cand).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                if path not in files:
                    files.append(path)
                break
    return files


def _read(path: Path) -> str:
    return path.read_text(errors="replace")


def _studio(root: Path) -> tuple[dict[str, Any], str | None]:
    p = root / "studio.toml"
    if not p.is_file():
        return {}, None
    try:
        return tomllib.loads(p.read_text()), None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as err:
        return {}, f"studio.toml: cannot be read ({type(err).__name__})"


def _from_studio(root: Path, cfg: dict[str, Any]) -> Discovery | None:
    app = cfg.get("app", {})
    keys = {k: v for k, v in app.items() if isinstance(v, str)} if isinstance(app, dict) else {}
    harness_keys = [k for k in ("app", "db", "login", "runtime", "services") if k in keys]
    if not harness_keys:
        return None
    missing = [k for k in ("app", "db") if k not in keys]
    if missing:
        return Discovery(
            reason=(
                f"studio.toml [app] names {', '.join(harness_keys)} but lacks "
                f"{' and '.join(missing)}: app and db are both required"
            )
        )
    refs = {k: keys.get(k) for k in ("app", "db", "login", "runtime", "services")}
    out = Discovery(source="studio.toml", file="studio.toml", refs=refs)
    try:
        out.make_app = _load(root, keys["app"])
        out.build_db = _load(root, keys["db"])
        out.login = _load(root, keys["login"]) if "login" in keys else None
        out.runtime = _load(root, keys["runtime"]) if "runtime" in keys else None
        out.services = dict(_load(root, keys["services"])) if "services" in keys else {}
    except _Unresolvable as err:
        return Discovery(reason=f"studio.toml: {err}")
    except (TypeError, ValueError) as err:
        return Discovery(reason=f"studio.toml: [app] services is not a mapping ({err})")
    out.database = keys.get("database") or None
    return out


def _from_invocation(root: Path, cfg: dict[str, Any]) -> Discovery | None:
    rebuild = cfg.get("rebuild", {})
    gates = rebuild.get("gates") if isinstance(rebuild, dict) else None
    files = _gate_files(root, gates)
    top = (root / "check.sh").resolve()
    # gate files first; the root check.sh only when no gate file has an invocation
    found: list[tuple[str, Invocation]] = []
    for pool in (files, [top] if top.is_file() and top not in files else []):
        for path in pool:
            rel = path.relative_to(root).as_posix()
            found += [(rel, inv) for inv in parse_invocations(_read(path))]
        if found:
            break
    if not found:
        return None
    first_file, first = found[0]
    for rel, inv in found[1:]:
        if inv.key() != first.key():
            diff = sorted(
                {f"--{f}" for f in VALUE_FLAGS if inv.flags.get(f) != first.flags.get(f)}
                | {f"--{f}" for f in PRESENCE_FLAGS if (f in inv.presence) != (f in first.presence)}
            )
            return Discovery(
                reason=(
                    f"ambiguous: the check invocations of {first_file} and {rel} disagree on "
                    f"{', '.join(diff)}"
                )
            )
    return resolve_invocation(root, first, first_file)


def _from_harness_py(root: Path) -> Discovery | None:
    if not (root / "harness.py").is_file():
        return None
    got: dict[str, Any] = {}
    for attr in ("make_app", "build_db", "login", "SERVICES", "RUNTIME"):
        try:
            got[attr] = _import(root, f"harness:{attr}")
        except AttributeError:
            got[attr] = None
        except (Exception, SystemExit) as err:  # noqa: BLE001 -- named, never a traceback
            return Discovery(
                reason=f"harness.py: `harness:{attr}` cannot be imported: {type(err).__name__}"
            )
    missing = [a for a in ("make_app", "build_db") if got[a] is None]
    if missing:
        return Discovery(
            reason=(
                f"harness.py has no {' and no '.join(missing)} "
                "(make_app and build_db are both required; no other names are tried)"
            )
        )
    try:
        services = dict(got["SERVICES"] or {})
    except (TypeError, ValueError):
        return Discovery(reason="harness.py: `harness:SERVICES` is not a mapping")
    return Discovery(
        source="harness.py",
        file="harness.py",
        refs={
            "app": "harness:make_app",
            "db": "harness:build_db",
            "login": "harness:login" if got["login"] is not None else None,
            "runtime": "harness:RUNTIME" if got["RUNTIME"] is not None else None,
            "services": "harness:SERVICES" if got["SERVICES"] is not None else None,
        },
        make_app=got["make_app"],
        build_db=got["build_db"],
        login=got["login"],
        runtime=got["RUNTIME"],
        services=services,
    )


def discover(root: Path) -> Discovery:
    """The harness of the app at ``root``, or why there is none (see the module docstring)."""
    root = Path(root).resolve()
    cfg, err = _studio(root)
    if err:
        return Discovery(reason=err)
    for source in (
        lambda: _from_studio(root, cfg),
        lambda: _from_invocation(root, cfg),
        lambda: _from_harness_py(root),
    ):
        got = source()
        if got is not None:
            return got
    return Discovery(reason=f"no harness found; looked for {SOURCES_LOOKED_FOR}")


def choose_database(
    named: str | None, explicit: str, extract_dialect: str | None
) -> tuple[str | None, str | None]:
    """(database, reason). An explicit ``--database`` wins; else the one the project names
    (the invocation's ``--database`` or ``sqlite``, ``[app] database``); else the extract's
    dialect. Anything but mysql and sqlite is a reason, not a database."""
    chosen = explicit if explicit != "auto" else (named or extract_dialect or "mysql")
    if chosen not in SUPPORTED_DATABASES:
        return None, (
            f"unsupported database {chosen!r}: the layer runs {' and '.join(SUPPORTED_DATABASES)}"
        )
    return chosen, None
