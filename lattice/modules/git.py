"""Fetch a module (or an index) from a git repository, pinned to a commit, without running
anything from it (``lattice module install git+file://<path>@<sha>``; the decisions are in
``docs/results/2026-10-15-marketplace-prereg.md`` sections 2.1-2.4).

:func:`parse_git_source` refuses, before any subprocess, everything but
``git+file://<absolute path>@<sha>`` and ``git+https://<host>/<path>@<sha>`` (the latter only
with ``allow_remote``), where ``<sha>`` is a full lowercase commit id (40 or 64 hex).
:func:`fetch_tree` then, in a temporary directory that is removed on every exit:

1. runs ``git`` from a fresh environment (no system or user config, no ``GIT_*`` of the
   caller, a null askpass, one allowed transport) with the hardening flags of
   :data:`HARDENING` on every call, and refuses a git older than :data:`GIT_FLOOR`;
2. ``fetch --depth=1 <url> <sha>`` into an empty **bare** repository created with
   ``--template=`` (no hooks), so nothing is ever checked out: no ``.gitattributes``
   filter, smudge, ``post-checkout`` hook, LFS or submodule can act;
3. reads the commit's file list with ``ls-tree`` and validates it (modes, path names,
   case collisions, size caps) BEFORE reading any blob;
4. writes the blobs, taken from ``cat-file --batch``, as plain ``0644`` files.

Nothing in the fetched tree is imported or executed. ``.gitattributes``, ``.gitmodules``
and ``.lfsconfig`` are dropped from the tree and listed.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lattice.modules import GitSourceNotSupported, ModuleError

GIT_FLOOR = (2, 45, 1)  # CVE-2024-32002, -32004, -32465
FETCH_TIMEOUT = 120.0
CALL_TIMEOUT = 20.0
MAX_FILES = 200
MAX_BLOB = 1_048_576
MAX_TOTAL = 8 * 1024 * 1024
MAX_PACK = 32 * 1024 * 1024
MAX_COMPONENTS = 8
MAX_PATH_BYTES = 200
DROPPED_NAMES = (".gitattributes", ".gitmodules", ".lfsconfig")

_PIN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_VERSION = re.compile(r"git version (\d+)\.(\d+)\.(\d+)")

# on every git call, before the subcommand (the transports are added per source)
HARDENING: tuple[tuple[str, str], ...] = (
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("core.symlinks", "false"),
    ("core.protectHFS", "true"),
    ("core.protectNTFS", "true"),
    ("protocol.allow", "never"),
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
    ("transfer.fsckObjects", "true"),
    ("fetch.fsckObjects", "true"),
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
    ("credential.helper", ""),
    ("http.followRedirects", "false"),
)

_PIN_MESSAGE = (
    "pin a full lowercase commit sha (git+<url>@<40 or 64 hex>); branch and tag names are refused"
)
_FORM_MESSAGE = "only git+file:// and git+https:// sources are installed"
_CREDENTIAL_MESSAGE = (
    "a git source must not carry credentials (user[:password]@host), a ?query or a #fragment"
)
_REMOTE_MESSAGE = "remote sources need --allow-remote"


# ---- the source -----------------------------------------------------------------------------


@dataclass(frozen=True)
class GitSource:
    """A validated ``git+<url>@<sha>``: ``url`` is what git is given, ``sha`` the pin."""

    source: str  # as written, with the pin
    url: str  # file:///abs/path or https://host/path
    sha: str
    scheme: str  # "file" | "https"

    @property
    def object_format(self) -> str:
        return "sha256" if len(self.sha) == 64 else "sha1"


def is_git_source(source: str) -> bool:
    """Whether ``source`` is a ``git+...`` form (accepted or refused by
    :func:`parse_git_source`), as opposed to a path, a name or a ``file://`` URL."""
    return source.startswith("git+")


def parse_git_source(source: str, allow_remote: bool = False) -> GitSource:
    """The :class:`GitSource` ``source`` names; raises :class:`GitSourceNotSupported` for
    everything section 2.1 refuses (first hit wins, nothing is run)."""
    body, at, pin = source.rpartition("@")
    if not at or not _PIN.fullmatch(pin):
        raise GitSourceNotSupported(_PIN_MESSAGE)
    if not (body.startswith("git+file://") or body.startswith("git+https://")):
        raise GitSourceNotSupported(_FORM_MESSAGE)
    url = body[len("git+") :]
    parts = urlsplit(url)
    if "?" in url or "#" in url or "@" in parts.netloc:
        raise GitSourceNotSupported(_CREDENTIAL_MESSAGE)
    scheme = parts.scheme
    if scheme == "https":
        if not allow_remote:
            raise GitSourceNotSupported(_REMOTE_MESSAGE)
        if not parts.hostname:
            raise GitSourceNotSupported("git+https:// needs a host")
    else:
        path = unquote(parts.path)
        if parts.netloc or not path.startswith("/"):
            raise GitSourceNotSupported(
                "git+file:// needs an absolute path (git+file:///path/to/repo@<sha>)"
            )
        if not Path(path).is_dir():
            raise GitSourceNotSupported(f"git+file path {path} is not a directory")
    return GitSource(source, url, pin, scheme)


# ---- running git ----------------------------------------------------------------------------


def _git_env(tmp: Path, scheme: str) -> dict[str, str]:
    """The environment of every git call: built from scratch, nothing of the caller's
    ``GIT_*`` or ``HOME``."""
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    false = shutil.which("false") or "/usr/bin/false"
    env = {"PATH": os.environ.get("PATH", os.defpath)}
    if "TMPDIR" in os.environ:
        env["TMPDIR"] = os.environ["TMPDIR"]
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": false,
            "SSH_ASKPASS": false,
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_ALLOW_PROTOCOL": scheme,
            "LC_ALL": "C",
        }
    )
    if scheme == "https":
        for key in ("https_proxy", "HTTPS_PROXY", "no_proxy"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def hardening_args(scheme: str) -> list[str]:
    """``-c key=value`` for every pair of :data:`HARDENING` and the one transport."""
    pairs = [*HARDENING, (f"protocol.{scheme}.allow", "always")]
    out: list[str] = []
    for key, value in pairs:
        out += ["-c", f"{key}={value}"]
    return out


class _Git:
    def __init__(self, tmp: Path, scheme: str, source: str) -> None:
        self.tmp = tmp
        self.env = _git_env(tmp, scheme)
        self.prefix = ["git", *hardening_args(scheme)]
        self.source = source

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = CALL_TIMEOUT,
        stdin: bytes | None = None,
        what: str | None = None,
    ) -> bytes:
        argv = [*self.prefix, *args]
        try:
            proc = subprocess.Popen(
                argv,
                env=self.env,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as err:
            raise ModuleError(f"cannot run git: {err.strerror or err}") from err
        try:
            stdout, stderr = proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired as err:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise ModuleError(
                f"git {what or args[0]} of {self.source} timed out after {timeout:.0f} s"
            ) from err
        except BaseException:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        if proc.returncode != 0:
            lines = [ln for ln in stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
            last = lines[-1].strip() if lines else f"exit {proc.returncode}"
            raise ModuleError(f"git {what or args[0]} of {self.source} failed: {last}")
        return stdout


def git_version(raw: str) -> tuple[int, int, int] | None:
    m = _VERSION.search(raw)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


# ---- the listing ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    mode: str
    oid: str
    size: int
    path: str


def parse_listing(raw: bytes, source: str) -> list[Entry]:
    out: list[Entry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        head, tab, path = record.partition(b"\t")
        fields = head.decode("ascii", "replace").split()
        if not tab or len(fields) != 4:
            raise ModuleError(f"git ls-tree of {source}: cannot read the listing")
        mode, _kind, oid, size = fields
        try:
            name = path.decode("utf-8")
        except UnicodeDecodeError as err:
            raise ModuleError(f"{source}: a path is not UTF-8: refused") from err
        out.append(Entry(mode, oid, 0 if size == "-" else int(size), name))
    return out


def validate_listing(entries: Sequence[Entry]) -> tuple[list[Entry], list[str]]:
    """The entries to materialise and the dropped names, or a refusal of the WHOLE tree
    (section 2.4). Raises :class:`ModuleError`."""
    keep: list[Entry] = []
    dropped: list[str] = []
    for e in entries:
        if e.mode == "120000":
            raise ModuleError(f"{e.path} is a symbolic link (mode 120000): refused")
        if e.mode == "160000":
            raise ModuleError(f"{e.path} is a submodule (mode 160000): refused")
        if e.mode not in ("100644", "100755"):
            raise ModuleError(f"{e.path} has mode {e.mode}: refused")
        parts = e.path.split("/")
        if len(parts) > MAX_COMPONENTS:
            raise ModuleError(f"{e.path} is nested more than {MAX_COMPONENTS} levels: refused")
        if len(e.path.encode()) > MAX_PATH_BYTES:
            raise ModuleError(f"{e.path[:40]}... is longer than {MAX_PATH_BYTES} bytes: refused")
        if parts[-1] in DROPPED_NAMES:
            if not all(_COMPONENT.fullmatch(p) for p in parts[:-1]):
                raise ModuleError(f"{e.path!r} is not an allowed path name: refused")
            dropped.append(e.path)
            continue
        for p in parts:
            if not _COMPONENT.fullmatch(p):
                raise ModuleError(
                    f"{e.path!r} is not an allowed path name "
                    f"(component {p!r} must match [A-Za-z0-9][A-Za-z0-9._+-]*): refused"
                )
        keep.append(e)
    seen: dict[str, str] = {}
    for e in keep:
        parts = e.path.split("/")
        nodes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        for node in nodes:
            folded = node.casefold()
            other = seen.setdefault(folded, node)
            if other != node:
                raise ModuleError(f"{other} and {node} differ only in case: refused")
    if len(keep) > MAX_FILES:
        raise ModuleError(f"the tree has {len(keep)} files (cap {MAX_FILES}): refused")
    for e in keep:
        if e.size > MAX_BLOB:
            raise ModuleError(f"{e.path} is {e.size} bytes (cap {MAX_BLOB}): refused")
    total = sum(e.size for e in keep)
    if total > MAX_TOTAL:
        raise ModuleError(f"the tree is {total} bytes (cap {MAX_TOTAL}): refused")
    return keep, sorted(dropped)


def _batch_blobs(raw: bytes, entries: Sequence[Entry], source: str) -> list[bytes]:
    """The contents ``cat-file --batch`` printed for ``entries``, in order, each checked
    against its listed oid and size."""
    out: list[bytes] = []
    pos = 0
    for e in entries:
        nl = raw.find(b"\n", pos)
        if nl < 0:
            raise ModuleError(f"git cat-file of {source}: output ended early")
        fields = raw[pos:nl].decode("ascii", "replace").split()
        if len(fields) != 3 or fields[0] != e.oid or fields[1] != "blob":
            raise ModuleError(f"git cat-file of {source}: {e.path} is not the blob listed")
        size = int(fields[2])
        if size != e.size:
            raise ModuleError(f"git cat-file of {source}: {e.path} changed size")
        start = nl + 1
        out.append(raw[start : start + size])
        pos = start + size + 1  # the newline after the content
    return out


def _write_file(root: Path, rel: str, data: bytes) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(target, flags, 0o644)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as f:
            fd = -1
            f.write(data)
    finally:
        if fd >= 0:
            os.close(fd)


# ---- the fetch ------------------------------------------------------------------------------


@dataclass
class FetchedTree:
    """A commit's files, written to ``directory`` (removed when the ``fetch_tree`` block
    exits)."""

    directory: Path
    commit: str
    source: str
    files: int
    bytes: int
    dropped: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        return (
            f"{self.commit} (fetched without hooks, filters, submodules, LFS; "
            f"{self.files} files, {self.bytes} bytes)"
        )


def _pack_bytes(count_objects: str) -> int:
    """Loose plus packed size ``git count-objects -v`` reports (KiB), in bytes."""
    kib = 0
    for line in count_objects.splitlines():
        key, _, value = line.partition(":")
        if key.strip() in ("size", "size-pack", "size-garbage") and value.strip().isdigit():
            kib += int(value)
    return kib * 1024


@contextlib.contextmanager
def fetch_tree(source: str, *, allow_remote: bool = False) -> Iterator[FetchedTree]:
    """Fetch the pinned commit ``source`` names and write its files to a temporary
    directory, yielded as a :class:`FetchedTree`; the directory is removed when the block
    ends, however it ends. Raises :class:`ModuleError`
    (:class:`GitSourceNotSupported` for a refused source form)."""
    src = parse_git_source(source, allow_remote)
    tmp = Path(tempfile.mkdtemp(prefix="lattice-module-"))
    try:
        git = _Git(tmp, src.scheme, source)
        raw_version = git.run(["--version"], what="--version").decode("utf-8", "replace")
        version = git_version(raw_version)
        if version is None or version < GIT_FLOOR:
            have = ".".join(map(str, version)) if version else raw_version.strip() or "unknown"
            floor = ".".join(map(str, GIT_FLOOR))
            raise ModuleError(
                f"git {have} is older than {floor} (CVE-2024-32002, CVE-2024-32004, "
                "CVE-2024-32465): update git; nothing was fetched"
            )
        repo = tmp / "repo"
        init = ["init", "-q", "--bare", "--template="]
        if src.object_format == "sha256":
            init.append("--object-format=sha256")
        git.run([*init, str(repo)], what="init")
        git.run(
            [
                "-C",
                str(repo),
                "fetch",
                "-q",
                "--depth=1",
                "--no-tags",
                "--no-recurse-submodules",
                "--no-write-fetch-head",
                src.url,
                src.sha,
            ],
            timeout=FETCH_TIMEOUT,
            what="fetch",
        )
        counts = git.run(["-C", str(repo), "count-objects", "-v"], what="count-objects")
        pack = _pack_bytes(counts.decode("utf-8", "replace"))
        if pack > MAX_PACK:
            raise ModuleError(
                f"{source}: size-pack {pack // 1024} KiB (cap {MAX_PACK // (1024 * 1024)} MiB): "
                "refused"
            )
        kind = git.run(["-C", str(repo), "cat-file", "-t", src.sha], what="cat-file")
        kind_text = kind.decode("ascii", "replace").strip()
        if kind_text != "commit":
            raise ModuleError(f"{src.sha} is a {kind_text}, not a commit: refused")
        listing = git.run(
            ["-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", "--long", src.sha],
            what="ls-tree",
        )
        keep, dropped = validate_listing(parse_listing(listing, source))
        tree = tmp / "tree"
        tree.mkdir()
        if keep:
            wanted = "".join(f"{e.oid}\n" for e in keep).encode("ascii")
            batch = git.run(
                ["-C", str(repo), "cat-file", "--batch"], stdin=wanted, what="cat-file --batch"
            )
            for e, data in zip(keep, _batch_blobs(batch, keep, source), strict=True):
                _write_file(tree, e.path, data)
        yield FetchedTree(tree, src.sha, source, len(keep), sum(e.size for e in keep), dropped)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


__all__ = [
    "DROPPED_NAMES",
    "GIT_FLOOR",
    "HARDENING",
    "FetchedTree",
    "GitSource",
    "fetch_tree",
    "hardening_args",
    "is_git_source",
    "parse_git_source",
    "validate_listing",
]
