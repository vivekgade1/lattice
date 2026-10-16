"""The importer's front ends (Phase 2 plan §2.2): one per web framework + ORM, each turning
an app package into the framework-neutral app model and saying how to run the app.

A :class:`FrontEnd` has a ``name``, ``detect(root, package)`` (does the package use its
framework?), ``extract(root, package) -> AppModel`` (``lattice.importer.appmodel``),
``runtime(root, package) -> AppRuntime`` (``lattice.importer.transport``: transport,
auth, dispose, seed and error-site hooks) and ``graph_hints``
(``lattice.importer.graph.GraphHints``). :func:`select` picks one by name, or (None)
the first whose ``detect`` holds. The built-in front end is FastAPI + SQLModel
(:mod:`.fastapi`); Django + DRF and Flask + SQLAlchemy register themselves the same way
(:func:`register`). The Go front end (:mod:`.go_gin`: Gin + GORM, over ``gocheck extract``)
is loaded by name, or last when every Python front end declined (:func:`_load_go`).

(This package's ``__init__`` is loaded whenever any front end is: it holds no string
literal, as the request suite's Hypothesis bodies draw from the importer's own -- see
``lattice.importer.suite``.)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from lattice.importer.appmodel import RepoModel
    from lattice.importer.graph import GraphHints
    from lattice.importer.transport import AppRuntime


class FrontEnd(Protocol):
    name: str

    @property
    def graph_hints(self) -> GraphHints: ...

    def detect(self, root: Path, package: str) -> bool: ...

    def extract(self, root: Path, package: str) -> RepoModel: ...

    def runtime(self, root: Path, package: str) -> AppRuntime: ...


_REGISTRY: dict[str, FrontEnd] = {}


def register(front_end: FrontEnd) -> FrontEnd:
    """Add (or replace) a front end under its name."""
    _REGISTRY[front_end.name] = front_end
    return front_end


def front_ends() -> tuple[FrontEnd, ...]:
    """Every registered front end, the built-in ones first."""
    from lattice.importer.frontends.fastapi import FRONT_END

    _REGISTRY.setdefault(FRONT_END.name, FRONT_END)
    return tuple(_REGISTRY.values())


def _load_others() -> None:
    """Register the Django + DRF and Flask + SQLAlchemy front ends. They are imported only
    when FastAPI's is not the one wanted, so a FastAPI import loads neither (their
    literals would join the request suite's Hypothesis pool: see ``suite``)."""
    from lattice.importer.frontends import django_drf, flask_app  # noqa: F401 -- register


def _load_go() -> None:
    """Register the Go (Gin + GORM) front end: imported only on a request for it, or after
    every Python front end declined, so a Python import never loads it."""
    from lattice.importer.frontends import go_gin  # noqa: F401 -- register


def select(
    framework: str | None = None, root: Path | str | None = None, package: str | None = None
) -> FrontEnd:
    """The front end named ``framework``; None: the first that detects its framework in
    ``package`` under ``root`` (FastAPI's first, then Django's and Flask's). A KeyError
    for an unknown name, a LookupError when none detects the package."""
    known = {fe.name: fe for fe in front_ends()}
    if framework is not None:
        if framework not in known:
            _load_others()
            known = {fe.name: fe for fe in front_ends()}
        if framework not in known:
            _load_go()
            known = {fe.name: fe for fe in front_ends()}
        if framework not in known:
            raise KeyError(f"no front end {framework!r} (known: {', '.join(sorted(known))})")
        return known[framework]
    if root is None or package is None:
        raise ValueError(f"detecting the framework needs a root and a package: {root}, {package}")
    tried: set[str] = set()
    for load in (None, _load_others, _load_go):
        if load is not None:
            load()
        for fe in front_ends():
            if fe.name not in tried:
                tried.add(fe.name)
                if fe.detect(Path(root), package):
                    return fe
    raise LookupError(f"no front end recognises {package!r} under {root}")
