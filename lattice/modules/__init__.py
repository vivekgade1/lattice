"""Lattice modules, installed locally and pinned by hash (spike 2, M-L; the descriptor-only
slice of ``docs/design/spikes/02-module-marketplace.md``).

A **module** is a directory holding ``lattice-module.toml``::

    name = "mariadb"                  # [a-z][a-z0-9_-]*
    version = "0.1.0"
    kind = "engine"                   # engine | custom
    description? = ""
    provides? = ["engine:mariadb"]    # default for an engine: engine:<its name>
    requires? = []                    # pip requirements (shown, never installed)
    descriptor = "engine.toml"        # kind engine: the descriptor file in the module,
    # [engine] ...                    #   or the descriptor's fields inline as [engine]

**Install** (:func:`install`, ``lattice module install SOURCE [--index DIR] [--yes]``)
prints the manifest and the tree's sha256, then installs only after ``--yes`` or an
interactive ``y`` (no terminal and no ``--yes``: refused, exit 2, nothing written). It
copies the module to ``<project>/.lattice/modules/<name>/`` (without ``__pycache__``,
``.git`` and ``.DS_Store``) and records ``name``, ``version``, ``kind``, ``sha256`` (of the
installed tree) and ``source`` in ``<project>/lattice.modules.lock`` (JSON).

SOURCE is a local directory or a ``file://`` URL. A git URL (``git+https://...``,
``https://....git``, ``git@host:repo``, ``ssh://``) is recognised and refused: **installing
from git is not implemented yet**. With ``--index DIR`` a bare NAME resolves through
``DIR/<NAME>.toml`` (``name``, ``version``, ``source`` -- a path relative to the index, a
``file://`` URL or a git URL -- and an optional ``sha256`` pin the source tree must match);
the index is a directory (in this repository for now; a plain git checkout works the same).

**Every load re-hashes** (:func:`verify`, :func:`register_modules`): a module whose
installed tree no longer hashes to its lock entry is ``changed`` (``missing`` when its
directory is gone, ``error`` when its manifest no longer reads) and is refused: the checker
(``lattice spec check --modules DIR``) exits 2, Studio lists it with that status and does
not register its engine. The hash covers every file of the installed tree (path and
content; ``.DS_Store`` excepted); an engine adapter's code is imported with bytecode
writing off, so loading a module never changes its tree.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from lattice.spec import engines as _engines

MANIFEST = "lattice-module.toml"
LOCK = "lattice.modules.lock"
MODULES_DIR = Path(".lattice") / "modules"
KINDS = ("engine", "custom")
STATUSES = ("ok", "changed", "missing", "error")
LOCK_VERSION = 1
# not copied at install; .DS_Store is also left out of the hash (Finder writes it)
COPY_IGNORE = ("__pycache__", ".git", ".DS_Store")
HASH_IGNORE = (".DS_Store",)

_NAME = re.compile(r"[a-z][a-z0-9_-]*")
_MANIFEST_FIELDS = (
    "name",
    "version",
    "kind",
    "description",
    "provides",
    "requires",
    "descriptor",
    "engine",
)
_INDEX_FIELDS = ("name", "version", "source", "sha256", "description")
_GIT_SCHEMES = ("git", "ssh", "http", "https")


class ModuleError(Exception):
    """A module that cannot be read, installed or loaded."""


class GitSourceNotSupported(ModuleError):
    """A git URL: recognised, not implemented yet."""


# ---- the manifest ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    kind: str
    description: str
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    engine: _engines.EngineDescriptor | None
    directory: Path
    text: str  # the manifest file as written


def _strs(data: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ModuleError(f"{where}: {key} must be a list of strings")
    return tuple(value)


def _str(data: Mapping[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if value is None:
        raise ModuleError(f"{where}: missing required field {key!r}")
    if not isinstance(value, str):
        raise ModuleError(f"{where}: {key} must be a string")
    return value


def read_manifest(directory: Path | str) -> Manifest:
    """The validated manifest of the module in ``directory`` (an engine's descriptor
    included)."""
    d = Path(directory)
    path = d / MANIFEST
    where = f"module {d}"
    if not d.is_dir():
        raise ModuleError(f"{where}: not a directory")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ModuleError(f"{where}: no readable {MANIFEST} ({err.strerror or err})") from err
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ModuleError(f"{where}: {MANIFEST} is not valid TOML ({err})") from err
    where = f"{where}: {MANIFEST}"
    for key in data:
        if key not in _MANIFEST_FIELDS:
            raise ModuleError(
                f"{where}: unknown field {key!r} (known: {', '.join(_MANIFEST_FIELDS)})"
            )
    name = _str(data, "name", where)
    if not _NAME.fullmatch(name):
        raise ModuleError(
            f"{where}: {name!r} is not a valid module name (lowercase letters, "
            "digits, '-' and '_', starting with a letter)"
        )
    version = _str(data, "version", where)
    if not version.strip():
        raise ModuleError(f"{where}: version must not be empty")
    kind = _str(data, "kind", where)
    if kind not in KINDS:
        raise ModuleError(f"{where}: kind must be one of {', '.join(KINDS)}, not {kind!r}")
    description = _str(data, "description", where, "")
    provides = _strs(data, "provides", where)
    requires = _strs(data, "requires", where)
    engine = None
    has_file, has_inline = "descriptor" in data, "engine" in data
    if kind == "engine":
        if has_file == has_inline:
            raise ModuleError(
                f"{where}: an engine module gives exactly one of descriptor = "
                '"<file>" or an [engine] table'
            )
        if has_file:
            rel = _str(data, "descriptor", where)
            target = (d / rel).resolve()
            if not target.is_relative_to(d.resolve()):
                raise ModuleError(f"{where}: descriptor {rel!r} is outside the module")
            try:
                engine = _engines.read_descriptor(target)
            except _engines.EngineError as err:
                raise ModuleError(str(err)) from err
        else:
            if not isinstance(data["engine"], dict):
                raise ModuleError(f"{where}: [engine] must be a table")
            try:
                engine = _engines.parse_descriptor(data["engine"], f"{where} [engine]", d.resolve())
            except _engines.EngineError as err:
                raise ModuleError(str(err)) from err
        if not provides:
            provides = (f"engine:{engine.name}",)
        if not requires:
            requires = engine.requires
    elif has_file or has_inline:
        raise ModuleError(f"{where}: only an engine module has a descriptor")
    return Manifest(name, version, kind, description, provides, requires, engine, d, text)


# ---- the tree hash --------------------------------------------------------------------------


def tree_files(directory: Path, ignore: tuple[str, ...] = HASH_IGNORE) -> list[Path]:
    """The files of a module tree, sorted by their relative POSIX path (``ignore``: names
    left out, at any depth). A symbolic link is refused."""
    out: list[Path] = []
    for p in sorted(directory.rglob("*"), key=lambda q: q.relative_to(directory).as_posix()):
        rel = p.relative_to(directory)
        if any(part in ignore for part in rel.parts):
            continue
        if p.is_symlink():
            raise ModuleError(
                f"module {directory}: {rel.as_posix()} is a symbolic link (not allowed in a module)"
            )
        if p.is_file():
            out.append(p)
    return out


def tree_hash(directory: Path | str, ignore: tuple[str, ...] = HASH_IGNORE) -> str:
    """sha256 over the tree: for each file in path order, its relative path and the sha256
    of its content."""
    d = Path(directory)
    h = hashlib.sha256()
    for p in tree_files(d, ignore):
        h.update(p.relative_to(d).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


# ---- the lock file --------------------------------------------------------------------------


@dataclass(frozen=True)
class LockEntry:
    name: str
    version: str
    kind: str
    sha256: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "sha256": self.sha256,
            "source": self.source,
        }


def read_lock(project: Path | str) -> list[LockEntry]:
    path = Path(project) / LOCK
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [
            LockEntry(**{k: str(e[k]) for k in ("name", "version", "kind", "sha256", "source")})
            for e in data["modules"]
        ]
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise ModuleError(f"{path}: not a valid lock file ({err})") from err
    return entries


def write_lock(project: Path | str, entries: list[LockEntry]) -> None:
    path = Path(project) / LOCK
    if not entries:
        path.unlink(missing_ok=True)
        return
    data = {
        "version": LOCK_VERSION,
        "modules": [e.to_dict() for e in sorted(entries, key=lambda e: e.name)],
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def module_dir(project: Path | str, name: str) -> Path:
    return Path(project) / MODULES_DIR / name


# ---- sources --------------------------------------------------------------------------------


def is_git_url(source: str) -> bool:
    if source.startswith("git+") or source.startswith("git@"):
        return True
    scheme = urlsplit(source).scheme.lower()
    if scheme in _GIT_SCHEMES:
        return True
    return "://" in source and source.rstrip("/").endswith(".git")


@dataclass(frozen=True)
class IndexEntry:
    name: str
    version: str
    source: str
    sha256: str | None
    path: Path


def read_index_entry(index: Path | str, name: str) -> IndexEntry:
    path = Path(index) / f"{name}.toml"
    if not _NAME.fullmatch(name):
        raise ModuleError(f"{name!r} is not a module name to look up in an index")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ModuleError(f"index {index}: no module {name!r} ({path.name} not found)") from err
    except tomllib.TOMLDecodeError as err:
        raise ModuleError(f"index {index}: {path.name} is not valid TOML ({err})") from err
    where = f"index entry {path}"
    for key in data:
        if key not in _INDEX_FIELDS:
            raise ModuleError(f"{where}: unknown field {key!r}")
    entry = IndexEntry(
        _str(data, "name", where),
        _str(data, "version", where),
        _str(data, "source", where),
        data.get("sha256"),
        path,
    )
    if entry.name != name:
        raise ModuleError(f"{where}: names module {entry.name!r}, not {name!r}")
    return entry


def resolve_source(source: str, index: Path | str | None = None) -> tuple[Path, IndexEntry | None]:
    """The local directory ``source`` names (a path, a ``file://`` URL, or with ``index`` a
    module name) and the index entry it came from."""
    if is_git_url(source):
        raise GitSourceNotSupported(
            f"installing from a git URL is not implemented yet: only local paths and file:// "
            f"URLs are (got {source})"
        )
    if source.startswith("file://"):
        return Path(unquote(urlsplit(source).path)), None
    if index is not None and _NAME.fullmatch(source) and not Path(source).exists():
        entry = read_index_entry(index, source)
        src = entry.source
        if is_git_url(src):
            raise GitSourceNotSupported(
                f"index entry {entry.path}: installing from a git URL is not implemented yet: "
                f"only local paths and file:// URLs are (got {src})"
            )
        path = Path(unquote(urlsplit(src).path)) if src.startswith("file://") else Path(src)
        if not path.is_absolute():
            path = (Path(index) / path).resolve()
        return path, entry
    return Path(source), None


# ---- install / uninstall --------------------------------------------------------------------


def describe(m: Manifest, sha256: str, source: str) -> str:
    """The manifest as the install shows it before asking."""
    lines = [
        f"module {m.name} {m.version} ({m.kind})",
        f"  source:   {source}",
        f"  sha256:   {sha256}",
        f"  files:    {len(tree_files(m.directory, COPY_IGNORE))}",
        f"  provides: {', '.join(m.provides) or '(nothing)'}",
        f"  requires: {', '.join(m.requires) or '(nothing)'} (pip; not installed by Lattice)",
    ]
    if m.description:
        lines.insert(1, f"  {m.description}")
    if m.engine is not None:
        e = m.engine
        code = (
            "no code: a built-in adapter"
            if e.adapter in _engines.BUILTIN_ADAPTERS
            else "CODE: imported into the checker when the engine is used"
        )
        lines += [
            f"  engine:   {e.name} (--database {e.name}), adapter {e.adapter} ({code})",
            f"  server:   docker image {e.server.image}, port {e.server.port}",
        ]
    lines += ["", f"--- {MANIFEST} ---", m.text.rstrip(), "---"]
    return "\n".join(lines)


def _tty_confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def install(
    project: Path | str,
    source: str,
    *,
    index: Path | str | None = None,
    yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    out: Callable[[str], None] = print,
) -> LockEntry:
    """Install the module ``source`` into ``project`` (see the module docstring). Raises
    ModuleError when it cannot, :class:`Refused` when it was not confirmed."""
    root = Path(project).resolve()
    src, entry = resolve_source(source, index)
    manifest = read_manifest(src)
    if entry is not None and (manifest.name, manifest.version) != (entry.name, entry.version):
        raise ModuleError(
            f"index entry {entry.path} says {entry.name} {entry.version}, the "
            f"source is {manifest.name} {manifest.version}"
        )
    digest = tree_hash(src, COPY_IGNORE)
    if entry is not None and entry.sha256 and entry.sha256 != digest:
        raise ModuleError(
            f"index entry {entry.path} pins sha256 {entry.sha256}, the source "
            f"tree hashes to {digest}: refused"
        )
    locked = {e.name: e for e in read_lock(root)}
    dest = module_dir(root, manifest.name)
    if manifest.name in locked or dest.exists():
        have = locked.get(manifest.name)
        what = f"version {have.version}" if have else f"a directory at {dest}"
        raise ModuleError(
            f"module {manifest.name!r} is already installed ({what}): uninstall it first"
        )
    if manifest.engine is not None:  # would it register? (a name clash is an error now)
        with _engines.REGISTRY.scoped() as reg:
            try:
                reg.add(manifest.engine, "module", module=manifest.name)
            except _engines.EngineError as err:
                raise ModuleError(str(err)) from err
    where = str(entry.path) if entry is not None else str(src.resolve())
    out(describe(manifest, digest, where))
    ask = confirm or _tty_confirm
    if not yes and not ask(f"Install {manifest.name} {manifest.version} into {root}? [y/N] "):
        raise Refused(
            f"not installed: {manifest.name} {manifest.version} was not confirmed "
            "(give --yes, or answer y at a terminal)"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*COPY_IGNORE))
    installed = tree_hash(dest)
    if installed != digest:  # the source changed while we asked
        shutil.rmtree(dest)
        raise ModuleError(
            f"module {manifest.name!r}: the source changed during the install "
            "(hash mismatch); nothing installed"
        )
    lock_entry = LockEntry(manifest.name, manifest.version, manifest.kind, installed, where)
    write_lock(root, [*locked.values(), lock_entry])
    out(
        f"installed {manifest.name} {manifest.version} into {dest.relative_to(root)} "
        f"(sha256 {installed[:12]}...; {LOCK})"
    )
    return lock_entry


class Refused(ModuleError):
    """An install that was not confirmed."""


def uninstall(project: Path | str, name: str) -> LockEntry:
    root = Path(project).resolve()
    locked = {e.name: e for e in read_lock(root)}
    if name not in locked:
        raise ModuleError(f"module {name!r} is not installed in {root}")
    dest = module_dir(root, name)
    if dest.is_symlink() or (
        dest.exists() and not dest.resolve().is_relative_to((root / MODULES_DIR).resolve())
    ):
        raise ModuleError(f"refusing to remove {dest}: not inside {MODULES_DIR}")
    if dest.exists():
        shutil.rmtree(dest)
    entry = locked.pop(name)
    write_lock(root, list(locked.values()))
    return entry


# ---- verify and load ------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleState:
    entry: LockEntry
    status: str  # ok | changed | missing | error
    actual: str | None = None  # the tree's sha256 now
    detail: str = ""
    manifest: Manifest | None = field(default=None, compare=False)

    @property
    def name(self) -> str:
        return self.entry.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.entry.name,
            "version": self.entry.version,
            "sha256": self.entry.sha256,
            "status": self.status,
        }


def verify(project: Path | str) -> list[ModuleState]:
    """Every installed module of ``project``, re-hashed against its lock entry."""
    root = Path(project).resolve()
    out: list[ModuleState] = []
    for e in read_lock(root):
        d = module_dir(root, e.name)
        if not d.is_dir():
            out.append(ModuleState(e, "missing", None, f"{d} is gone"))
            continue
        try:
            actual = tree_hash(d)
        except ModuleError as err:
            out.append(ModuleState(e, "error", None, str(err)))
            continue
        if actual != e.sha256:
            out.append(
                ModuleState(
                    e, "changed", actual, f"its tree hashes to {actual}, the lock says {e.sha256}"
                )
            )
            continue
        try:
            manifest = read_manifest(d)
        except ModuleError as err:
            out.append(ModuleState(e, "error", actual, str(err)))
            continue
        out.append(ModuleState(e, "ok", actual, "", manifest))
    return out


def refusal(state: ModuleState) -> str:
    return (
        f"module {state.name!r} ({state.entry.version}) is {state.status}: {state.detail}; "
        "it is refused (reinstall or uninstall it)"
    )


def register_modules(
    project: Path | str, strict: bool = True, registry: _engines.EngineRegistry | None = None
) -> list[ModuleState]:
    """Verify ``project``'s modules and register the engines of those that are ``ok``. A
    module that is not ok is refused: ``strict`` raises ModuleError naming it; otherwise its
    engines are dropped from the registry and its state says why."""
    reg = registry or _engines.REGISTRY
    states = verify(project)
    bad = [s for s in states if s.status != "ok"]
    if strict and bad:
        raise ModuleError(refusal(bad[0]))
    for s in states:
        if s.status != "ok":
            for r in reg.entries():
                if r.source == "module" and r.module == s.name:
                    reg.remove(r.engine.name)
            continue
        if s.manifest is not None and s.manifest.engine is not None:
            try:
                reg.add(s.manifest.engine, "module", s.manifest.directory / MANIFEST, s.name)
            except _engines.EngineError as err:
                if strict:
                    raise ModuleError(str(err)) from err
    return states


# ---- a project's engines (Studio, lattice check --project) -----------------------------------


@dataclass
class ProjectEngines:
    """The engines a project adds (``[engines] paths`` and its modules), registered."""

    files: list[tuple[Path, _engines.EngineDescriptor]] = field(default_factory=list)
    modules: list[ModuleState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def engine_paths(root: Path, engines_table: Any) -> tuple[list[Path], list[str]]:
    """The descriptor files ``studio.toml`` ``[engines] paths`` names (relative to the
    project), and the problems with the table."""
    if engines_table in (None, {}):
        return [], []
    if not isinstance(engines_table, dict):
        return [], ["studio.toml: [engines] must be a table"]
    unknown = [k for k in engines_table if k != "paths"]
    problems = [f"studio.toml: [engines] unknown field {k!r} (known: paths)" for k in unknown]
    paths = engines_table.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return [], [*problems, "studio.toml: [engines] paths must be a list of file paths"]
    return [(root / p).resolve() for p in paths], problems


def load_project_engines(root: Path | str, engines_table: Any = None) -> ProjectEngines:
    """Register ``root``'s engines (its ``[engines] paths``, then its verified modules) and
    report them; a problem is listed in ``errors`` (never raised)."""
    r = Path(root).resolve()
    out = ProjectEngines()
    paths, out.errors = engine_paths(r, engines_table)
    for p in paths:
        try:
            out.files.append((p, _engines.load_engine_file(p, "project")))
        except _engines.EngineError as err:
            out.errors.append(str(err))
    try:
        out.modules = register_modules(r, strict=False)
    except ModuleError as err:
        out.errors.append(str(err))
    for s in out.modules:
        if s.status != "ok":
            out.errors.append(refusal(s))
    return out


__all__ = [
    "LOCK",
    "MANIFEST",
    "MODULES_DIR",
    "GitSourceNotSupported",
    "LockEntry",
    "Manifest",
    "ModuleError",
    "ModuleState",
    "ProjectEngines",
    "Refused",
    "install",
    "load_project_engines",
    "read_lock",
    "read_manifest",
    "register_modules",
    "resolve_source",
    "tree_hash",
    "uninstall",
    "verify",
]
