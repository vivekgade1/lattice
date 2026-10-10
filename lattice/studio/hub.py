"""Many projects behind one Studio server (API.md v1.6).

A :class:`StudioHub` holds named projects (``name``, ``root``). Each project gets its own
:class:`~lattice.studio.server.Studio`, built on first use, so change sets, chat sessions,
checks and the model cache never cross projects. The server routes ``/p/<name>/api/...``
to the named project and bare ``/api/...`` to the default one; ``GET /api/projects``
lists them from cheap fields only (studio.toml, ``git rev-parse``), never building a
Studio.

**Discovery** (:func:`discover`):

1. explicit directories (``lattice studio DIR ...``): a directory that is a project is
   taken as is, any other one contributes its project children (one level down);
2. with no directories: :func:`lattice.cli.discover_projects` over the cwd (the cwd
   itself, its children one level down, then ``cwd/dogfood/*``), then the projects of
   the registry ``$XDG_CONFIG_HOME/lattice/projects.toml`` (default
   ``~/.config/lattice/projects.toml``; ``[[project]] path = "..."``, relative paths
   resolve against the file; a missing or broken file is ignored).

A project is a directory with ``studio.toml``, or ``spec.py`` and ``spec.trace.json``.
Duplicates (by resolved path) are dropped; a project's name is ``[project] name`` of its
studio.toml, else the directory's name, made unique with ``-2``, ``-3``, ...
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import threading
import tomllib
from collections.abc import Callable, Iterable, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lattice.cli import _project_at, _read_toml, discover_projects, project_name

REGISTRY_ENV = "LATTICE_PROJECTS_REGISTRY"  # tests / power users: another registry file
GIT_TIMEOUT = 5.0


def registry_path() -> Path:
    if os.environ.get(REGISTRY_ENV):
        return Path(os.environ[REGISTRY_ENV])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "lattice" / "projects.toml"


def registry_dirs(path: Path | None = None) -> list[Path]:
    """The ``[[project]] path`` entries of the registry file (missing or broken: none)."""
    path = registry_path() if path is None else path
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    items = data.get("project")
    if not isinstance(items, list):
        return []
    out: list[Path] = []
    for item in items:
        p = item.get("path") if isinstance(item, dict) else None
        if isinstance(p, str) and p.strip():
            q = Path(p.strip()).expanduser()
            out.append(q if q.is_absolute() else path.parent / q)
    return out


def _expand(d: Path) -> list[Path]:
    """`d` when it is a project, else its project children."""
    try:
        real = d.resolve()
    except OSError:
        return []
    if not real.is_dir():
        return []
    if _project_at(real) is not None:
        return [real]
    return [p.path for p in discover_projects([real]) if p.path != real]


def _unique(base: str, taken: set[str]) -> str:
    base = base.replace("/", "-").strip() or "project"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def discover(
    dirs: Sequence[Path | str] = (),
    *,
    cwd: Path | None = None,
    registry: Path | None = None,
) -> list[tuple[str, Path]]:
    """``(name, root)`` of every project, in discovery order (see the module docstring)."""
    roots: list[Path] = []
    if dirs:
        for d in dirs:
            roots.extend(_expand(Path(d)))
    else:
        roots.extend(p.path for p in discover_projects(cwd=cwd))
        for d in registry_dirs(registry):
            roots.extend(_expand(d))
    seen: set[Path] = set()
    taken: set[str] = set()
    out: list[tuple[str, Path]] = []
    for r in roots:
        r = r.resolve()
        if r in seen:
            continue
        seen.add(r)
        name = _unique(project_name(r), taken)
        taken.add(name)
        out.append((name, r))
    return out


def _git_head(root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


@dataclasses.dataclass
class HubProject:
    name: str
    root: Path
    studio: Any = None  # the Studio, once built
    error: str | None = None  # why the Studio could not be built


class StudioHub:
    """Named projects, each with a lazily built Studio (see the module docstring).

    `factory(root, name)` builds a project's Studio. `prefixed`: whether a project's own
    base URL (its chat sessions' ``--studio-url``) carries ``/p/<name>``; a single-project
    server keeps the bare URL, as before v1.6."""

    def __init__(
        self,
        projects: Iterable[tuple[str, Path]],
        factory: Callable[[Path, str], Any],
        *,
        default: str | None = None,
        prefixed: bool = True,
        claude_bin: str | None = None,
    ) -> None:
        self.projects: dict[str, HubProject] = {}
        for name, root in projects:
            self.projects[name] = HubProject(name, Path(root).resolve())
        if not self.projects:
            raise ValueError("no projects")
        if default is not None and default not in self.projects:
            raise ValueError(f"no project named {default!r} (have {', '.join(self.projects)})")
        self.default = default or next(iter(self.projects))
        self.factory = factory
        self.prefixed = prefixed
        self.claude_bin = claude_bin
        self.base_url: str | None = None
        self.lock = threading.Lock()

    def __contains__(self, name: str) -> bool:
        return name in self.projects

    def url_of(self, name: str) -> str | None:
        if self.base_url is None:
            return None
        return self.base_url + (f"/p/{quote(name, safe='')}" if self.prefixed else "")

    def set_base_url(self, url: str) -> None:
        with self.lock:
            self.base_url = url
            for p in self.projects.values():
                if p.studio is not None:
                    p.studio.base_url = self.url_of(p.name)

    def loaded(self, name: str) -> Any:
        """The project's Studio if it is built, else None (never builds one)."""
        p = self.projects.get(name)
        return p.studio if p is not None else None

    def get(self, name: str | None = None) -> Any:
        """The Studio of project `name` (default: the default project), built on first
        use. Unknown name: ``ApiError`` 404; a project that cannot load: 500."""
        from lattice.studio.server import ApiError

        name = self.default if name is None else name
        p = self.projects.get(name)
        if p is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "no such project")
        with self.lock:
            if p.studio is None:
                try:
                    st = self.factory(p.root, p.name)
                except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
                    raise ApiError(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"project {name} does not load: {type(exc).__name__}: {exc}",
                    ) from None
                st.base_url = self.url_of(p.name)
                p.studio = st
            return p.studio

    def summary(self, name: str) -> dict[str, Any]:
        """One ``GET /api/projects`` item, from cheap fields only."""
        from lattice.studio.server import SPEC_FILE, TRACE_FILE
        from lattice.studio.session import claude_available

        p = self.projects[name]
        toml = _read_toml(p.root / "studio.toml")
        app = toml.get("app") if isinstance(toml.get("app"), dict) else {}
        llm = toml.get("llm") if isinstance(toml.get("llm"), dict) else {}
        st = p.studio
        last = st.last_check if st is not None else None
        claude_bin = str(
            (st.claude_bin if st is not None else None)
            or self.claude_bin
            or llm.get("claude_bin")
            or "claude"
        )
        return {
            "name": name,
            "root": str(p.root),
            "git_head": _git_head(p.root),
            "spec": (p.root / SPEC_FILE).is_file() and (p.root / TRACE_FILE).is_file(),
            "app_configured": bool(app.get("app") and app.get("db")),
            "last_check": (
                {"verdict": last.get("verdict"), "at": last.get("at")} if last else None
            ),
            "capabilities": {"session": claude_available(claude_bin), "rebuild": False},
            "default": name == self.default,
        }

    def list(self) -> list[dict[str, Any]]:
        return [self.summary(n) for n in self.projects]

    def close(self) -> None:
        for p in self.projects.values():
            if p.studio is not None:
                p.studio.close_sessions()
