"""Which front end read an app, and what that means for the consumers of its extract (N16 of
docs/results/2026-10-18-dotnet-prereg.md): the workflow layer, the inventory, the
implementation report and change impact ask this module instead of naming the Go front end.

A front end here is a module of :mod:`lattice.importer.frontends` that holds ``FRONT_END`` (an
object with ``name``, ``detect(root, package)``, ``extract_json(root, package, workflow=False)``
and ``runtime(root, package, *, model=None)``) and ``model_from_extract(doc, package)``. The
inventory's ``frontend`` is the module's name: ``go_gin`` (``lattice.goextract/1``, ``*.go``) or
``dotnet_aspnet`` (``lattice.dnextract/1``, ``*.cs``). The Go bytes of every consumer are
unchanged: ``go_gin`` is the answer for anything the .NET front end does not claim.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

# frontend -> (extract schema, source suffixes, exact file names, directories the source set skips)
FRONT_ENDS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], frozenset[str]]] = {
    "go_gin": ("lattice.goextract/1", (".go",), ("go.mod", "go.sum"), frozenset()),
    "dotnet_aspnet": (
        "lattice.dnextract/1",
        (".cs", ".csproj", ".sln", ".slnx"),
        ("Directory.Build.props", "Directory.Packages.props", "packages.lock.json", "global.json"),
        frozenset({"bin", "obj"}),
    ),
}


def _module(frontend: str) -> Any:
    return importlib.import_module(f"lattice.importer.frontends.{frontend}")


def front_end_for(root: Path | str, package: str = ".") -> Any:
    """The front end that detects the app: Go's, else .NET's (when it is installed), else Go's
    (so a directory neither detects fails inside the Go extractor, as it always did)."""
    go = _module("go_gin").FRONT_END
    if go.detect(Path(root), package):
        return go
    try:
        dn = _module("dotnet_aspnet").FRONT_END
    except (ImportError, AttributeError):
        return go
    return dn if dn.detect(Path(root), package) else go


def frontend_name(front_end: Any) -> str:
    """The inventory's ``frontend`` of a front-end object: the name of its module."""
    return str(type(front_end).__module__.rsplit(".", 1)[-1])


def frontend_of(doc: Any) -> str:
    """The ``frontend`` an extract document belongs to (by its schema; Go's when unknown)."""
    schema = doc.get("schema") if isinstance(doc, dict) else None
    return next((k for k, v in FRONT_ENDS.items() if v[0] == schema), "go_gin")


def extract_json(root: Path | str, package: str = ".", workflow: bool = False) -> dict[str, Any]:
    """The extract of the app under `root` by the front end that detects it."""
    fe = front_end_for(root, package)
    return fe.extract_json(root, package, workflow=workflow)  # type: ignore[no-any-return]


def model_from_extract(doc: dict[str, Any], package: str = ".") -> Any:
    """The importer's app model of an extract document, by its ``schema`` (an unknown one is
    refused by the Go mapper, as before)."""
    return _module(frontend_of(doc)).model_from_extract(doc, package)


def source_suffixes(
    frontend: str | None,
) -> tuple[tuple[str, ...], tuple[str, ...], frozenset[str]]:
    """(suffixes, exact names, skipped dirs) of a front end's source set; Go's when unknown."""
    return FRONT_ENDS.get(frontend or "go_gin", FRONT_ENDS["go_gin"])[1:]
