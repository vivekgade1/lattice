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

**Install** (:func:`install`, :func:`install_many`, ``lattice module install SOURCE
[SOURCE ...] [--index DIR|URL] [--allow-remote] [--yes]``) prints each manifest and the
tree's sha256, then installs only after ``--yes`` or an interactive ``y`` (no terminal and
no ``--yes``: refused, exit 2, nothing written). All sources are fetched, validated and
clash-checked before the one confirmation, each is copied to
``<project>/.lattice/modules/.staging-<pid>/<name>``, re-hashed and renamed into
``<project>/.lattice/modules/<name>/`` (without ``__pycache__``, ``.git`` and
``.DS_Store``), then ``name``, ``version``, ``kind``, ``sha256`` (of the installed tree) and
``source`` are written to ``<project>/lattice.modules.lock`` (JSON) in one atomic replace; a
failure removes what it made and leaves the lock as it was.

SOURCE is a local directory, a ``file://`` URL, or a git source pinned to a commit:
``git+file://<absolute path>@<sha>`` and, with ``--allow-remote``,
``git+https://<host>/<path>@<sha>`` (:mod:`lattice.modules.git`: ``<sha>`` is a full
lowercase commit id; a branch, a tag, ssh, credentials in the URL are refused; the fetch runs
in an isolated environment with no checkout, and **nothing from the module executes at
install**). A git entry's lock record adds ``commit`` (and, through an index, ``index``).
Any other git URL (``https://....git``, ``git@host:repo``, ``ssh://``) is refused with a
message naming the accepted forms; :func:`resolve_source` itself still reads local
directories only. With ``--index`` a bare NAME resolves through ``<index>/<NAME>.toml``
(``name``, ``version``, ``source`` -- a path relative to the index, a ``file://`` URL or a
pinned git source -- and an optional ``sha256`` pin the source tree must match); the index is
a directory or a pinned git source (then every ``source`` in it must be a pinned git source).

**Every load re-hashes** (:func:`verify`, :func:`register_modules`): a module whose
installed tree no longer hashes to its lock entry is ``changed`` (``missing`` when its
directory is gone, ``error`` when its manifest no longer reads) and is refused: the checker
(``lattice spec check --modules DIR``) exits 2, Studio lists it with that status and does
not register its engine. The hash covers every file of the installed tree (path and
content; ``.DS_Store`` excepted); an engine adapter's code is imported with bytecode
writing off, so loading a module never changes its tree.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
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
    "entry",
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
    # a `custom` module's Python entry point: "pkg.module" whose main(argv) the host runs for
    # the subcommand(s) in `provides` (spike 1b: the security module's entry, lattice.security).
    # Engine modules do not use it. None when absent.
    entry: str | None = None


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
    entry = data.get("entry")
    if entry is not None and not isinstance(entry, str):
        raise ModuleError(f"{where}: entry must be a string (a 'pkg.module' with a main(argv))")
    if entry is not None and kind == "engine":
        raise ModuleError(f"{where}: an engine module has no entry (it provides an engine)")
    return Manifest(name, version, kind, description, provides, requires, engine, d, text, entry)


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
    commit: str | None = None  # a git install: the pinned commit
    index: str | None = None  # a git install through an index: the index path or URL

    def to_dict(self) -> dict[str, str]:
        out = {
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "sha256": self.sha256,
            "source": self.source,
        }
        if self.commit is not None:  # directory installs gain no key
            out["commit"] = self.commit
        if self.index is not None:
            out["index"] = self.index
        return out


def read_lock(project: Path | str) -> list[LockEntry]:
    path = Path(project) / LOCK
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [
            LockEntry(
                **{k: str(e[k]) for k in ("name", "version", "kind", "sha256", "source")},
                commit=None if e.get("commit") is None else str(e["commit"]),
                index=None if e.get("index") is None else str(e["index"]),
            )
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
    tmp = path.with_name(path.name + ".tmp")  # one atomic replace: never a half-written lock
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


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
    label: str | None = None  # a git index: its source, for messages (path is a temp file)

    @property
    def where(self) -> str:
        """The entry as messages and the lock name it."""
        return str(self.path) if self.label is None else f"{self.label}:{self.path.name}"


def read_index_entry(index: Path | str, name: str, label: str | None = None) -> IndexEntry:
    path = Path(index) / f"{name}.toml"
    shown = str(index) if label is None else label
    if not _NAME.fullmatch(name):
        raise ModuleError(f"{name!r} is not a module name to look up in an index")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ModuleError(f"index {shown}: no module {name!r} ({path.name} not found)") from err
    except tomllib.TOMLDecodeError as err:
        raise ModuleError(f"index {shown}: {path.name} is not valid TOML ({err})") from err
    where = f"index entry {path}" if label is None else f"index entry {label}:{path.name}"
    for key in data:
        if key not in _INDEX_FIELDS:
            raise ModuleError(f"{where}: unknown field {key!r}")
    entry = IndexEntry(
        _str(data, "name", where),
        _str(data, "version", where),
        _str(data, "source", where),
        data.get("sha256"),
        path,
        label,
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


def describe(
    m: Manifest,
    sha256: str,
    source: str,
    commit: str | None = None,
    dropped: Sequence[str] = (),
) -> str:
    """The manifest as the install shows it before asking (``commit``: a git install's
    ``FetchedTree.note``; ``dropped``: the names left out of the fetched tree)."""
    lines = [
        f"module {m.name} {m.version} ({m.kind})",
        f"  source:   {source}",
    ]
    if commit is not None:
        lines.append(f"  commit:   {commit}")
    if dropped:
        lines.append(f"  dropped:  {', '.join(dropped)}")
    lines += [
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


@dataclass
class _Prepared:
    """One source, fetched and validated: its tree, manifest and what the lock records."""

    manifest: Manifest
    digest: str
    source: Path  # the tree to copy (a temp directory for a git source)
    where: str  # the lock's ``source``
    commit: str | None = None
    note: str | None = None
    dropped: list[str] = field(default_factory=list)
    index: str | None = None  # a git install through an index


def _prepare(
    source: str,
    index_dir: Path | None,
    index_label: str | None,
    index_shown: str | None,
    allow_remote: bool,
    stack: contextlib.ExitStack,
) -> _Prepared:
    from lattice.modules import git as _git

    entry: IndexEntry | None = None
    commit: str | None = None
    note: str | None = None
    dropped: list[str] = []
    via_index: str | None = None
    git_source: str | None = None
    src: Path | None = None
    if is_git_url(source):
        git_source = source
    elif index_dir is not None and _NAME.fullmatch(source) and not Path(source).exists():
        entry = read_index_entry(index_dir, source, index_label)
        if is_git_url(entry.source):
            git_source = entry.source
        elif index_label is not None:
            raise ModuleError(
                f"index entry {entry.where}: a git index names pinned git sources only "
                f"(got {entry.source})"
            )
        else:
            src, entry = resolve_source(source, index_dir)
    else:
        src, entry = resolve_source(source, index_dir)
    if git_source is not None:
        try:
            _git.parse_git_source(git_source, allow_remote)
        except GitSourceNotSupported as err:
            if entry is not None:
                raise GitSourceNotSupported(f"index entry {entry.where}: {err}") from err
            raise
        fetched = stack.enter_context(_git.fetch_tree(git_source, allow_remote=allow_remote))
        src, commit, note, dropped = (
            fetched.directory,
            fetched.commit,
            fetched.note,
            fetched.dropped,
        )
        if entry is not None:
            via_index = index_shown
    assert src is not None
    manifest = read_manifest(src)
    if entry is not None and (manifest.name, manifest.version) != (entry.name, entry.version):
        raise ModuleError(
            f"index entry {entry.where} says {entry.name} {entry.version}, the "
            f"source is {manifest.name} {manifest.version}"
        )
    digest = tree_hash(src, COPY_IGNORE)
    if entry is not None and entry.sha256 and entry.sha256 != digest:
        raise ModuleError(
            f"index entry {entry.where} pins sha256 {entry.sha256}, the source "
            f"tree hashes to {digest}: refused"
        )
    if git_source is not None:
        where = git_source
    else:
        where = entry.where if entry is not None else str(src.resolve())
    return _Prepared(manifest, digest, src, where, commit, note, dropped, via_index)


def install(
    project: Path | str,
    source: str,
    *,
    index: Path | str | None = None,
    yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    out: Callable[[str], None] = print,
    allow_remote: bool = False,
) -> LockEntry:
    """Install the module ``source`` into ``project`` (see the module docstring). Raises
    ModuleError when it cannot, :class:`Refused` when it was not confirmed."""
    return install_many(
        project, [source], index=index, yes=yes, confirm=confirm, out=out, allow_remote=allow_remote
    )[0]


def install_many(
    project: Path | str,
    sources: Sequence[str],
    *,
    index: Path | str | None = None,
    yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    out: Callable[[str], None] = print,
    allow_remote: bool = False,
) -> list[LockEntry]:
    """Install several modules in one step: every source is fetched, validated, hashed and
    clash-checked before the single confirmation; the lock is written once, atomically."""
    from lattice.modules import git as _git

    if not sources:
        raise ModuleError("nothing to install: give a SOURCE")
    root = Path(project).resolve()
    with contextlib.ExitStack() as stack:
        index_dir: Path | None = None
        index_label: str | None = None
        index_shown: str | None = None
        if index is not None:
            index_text = str(index)
            if is_git_url(index_text):
                tree = stack.enter_context(_git.fetch_tree(index_text, allow_remote=allow_remote))
                index_dir, index_label, index_shown = tree.directory, index_text, index_text
            else:
                index_dir, index_shown = Path(index), str(Path(index).resolve())
        locked = {e.name: e for e in read_lock(root)}
        reg = stack.enter_context(_engines.REGISTRY.scoped())  # clashes: built-ins and each other
        prepared: list[_Prepared] = []
        for source in sources:
            p = _prepare(source, index_dir, index_label, index_shown, allow_remote, stack)
            m = p.manifest
            if any(q.manifest.name == m.name for q in prepared):
                raise ModuleError(f"module {m.name} appears twice in this install")
            dest = module_dir(root, m.name)
            if m.name in locked or dest.exists():
                have = locked.get(m.name)
                what = f"version {have.version}" if have else f"a directory at {dest}"
                raise ModuleError(
                    f"module {m.name!r} is already installed ({what}): uninstall it first"
                )
            if m.engine is not None:  # would it register? (a name clash is an error now)
                try:
                    reg.add(m.engine, "module", module=m.name)
                except _engines.EngineError as err:
                    raise ModuleError(str(err)) from err
            prepared.append(p)
        for p in prepared:
            out(describe(p.manifest, p.digest, p.where, p.note, p.dropped))
        ask = confirm or _tty_confirm
        if len(prepared) == 1:
            m = prepared[0].manifest
            question = f"Install {m.name} {m.version} into {root}? [y/N] "
            named = f"{m.name} {m.version} was"
        else:
            names = ", ".join(f"{p.manifest.name} {p.manifest.version}" for p in prepared)
            question = f"Install {len(prepared)} modules ({names}) into {root}? [y/N] "
            named = f"{names} were"
        if not yes and not ask(question):
            raise Refused(
                f"not installed: {named} not confirmed (give --yes, or answer y at a terminal)"
            )
        entries = _write_modules(root, prepared)
    for e in entries:
        dest = module_dir(root, e.name)
        out(
            f"installed {e.name} {e.version} into {dest.relative_to(root)} "
            f"(sha256 {e.sha256[:12]}...; {LOCK})"
        )
    return entries


def _write_modules(root: Path, prepared: Sequence[_Prepared]) -> list[LockEntry]:
    """Copy every tree to a staging directory, re-hash, rename into place, then replace the
    lock. A failure removes the staging directory and every renamed directory."""
    locked = [e for e in read_lock(root)]
    modules_dir = root / MODULES_DIR
    staging = modules_dir / f".staging-{os.getpid()}"
    created = [d for d in (root / ".lattice", modules_dir) if not d.exists()]
    renamed: list[Path] = []
    entries: list[LockEntry] = []
    try:
        staging.mkdir(parents=True)
        for p in prepared:
            name = p.manifest.name
            tmp = staging / name
            shutil.copytree(p.source, tmp, ignore=shutil.ignore_patterns(*COPY_IGNORE))
            installed = tree_hash(tmp)
            if installed != p.digest:  # the source changed while we asked
                raise ModuleError(
                    f"module {name!r}: the source changed during the install "
                    "(hash mismatch); nothing installed"
                )
            entries.append(
                LockEntry(
                    name,
                    p.manifest.version,
                    p.manifest.kind,
                    installed,
                    p.where,
                    p.commit,
                    p.index if p.commit is not None else None,
                )
            )
        for p in prepared:
            dest = module_dir(root, p.manifest.name)
            os.rename(staging / p.manifest.name, dest)
            renamed.append(dest)
        write_lock(root, [*locked, *entries])
    except BaseException:
        for d in reversed(renamed):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        for d in reversed(created):
            with contextlib.suppress(OSError):
                d.rmdir()
        raise
    with contextlib.suppress(OSError):
        staging.rmdir()
    return entries


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
    # the project's own registry (built-ins, its files, its verified modules); also the one
    # the calling context sees after load_project_engines (see lattice.spec.engines.using)
    registry: _engines.EngineRegistry | None = field(default=None, compare=False, repr=False)


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


def _load_into(
    root: Path | str, engines_table: Any, reg: _engines.EngineRegistry
) -> ProjectEngines:
    r = Path(root).resolve()
    out = ProjectEngines(registry=reg)
    paths, out.errors = engine_paths(r, engines_table)
    for p in paths:
        try:
            out.files.append((p, _engines.load_engine_file(p, "project", reg)))
        except _engines.EngineError as err:
            out.errors.append(str(err))
    try:
        out.modules = register_modules(r, strict=False, registry=reg)
    except ModuleError as err:
        out.errors.append(str(err))
    for s in out.modules:
        if s.status != "ok":
            out.errors.append(refusal(s))
    return out


def load_project_engines(root: Path | str, engines_table: Any = None) -> ProjectEngines:
    """Register ``root``'s engines (its ``[engines] paths``, then its verified modules) in
    a NEW registry of its own (the built-ins, then those, in that order) and report them; a
    problem is listed in ``errors`` (never raised).

    The registry is activated for the calling context (``ProjectEngines.registry``; no
    reset: a thread's context ends with the thread), so ``--database``, ``find`` and the
    adapters resolve this project's engines, never another project's, and the process
    registry (the CLI's) is not touched. Where a thread is reused across projects, use
    :func:`project_engines`, which restores the previous registry on exit."""
    out = _load_into(root, engines_table, _engines.project_registry())
    assert out.registry is not None
    _engines.activate(out.registry)
    return out


@contextlib.contextmanager
def project_engines(root: Path | str, engines_table: Any = None) -> Iterator[ProjectEngines]:
    """:func:`load_project_engines` as a scope: the project's registry is the one the
    calling context sees inside the block, and the previous one is restored after it,
    also when the block raises."""
    out = _load_into(root, engines_table, _engines.project_registry())
    assert out.registry is not None
    with _engines.using(out.registry):
        yield out


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
    "install_many",
    "load_project_engines",
    "project_engines",
    "read_lock",
    "read_manifest",
    "register_modules",
    "resolve_source",
    "tree_hash",
    "uninstall",
    "verify",
]
