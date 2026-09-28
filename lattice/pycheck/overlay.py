"""An edited copy of an app package on disk, for :func:`lattice.pycheck.run_pycheck`.

pycheck reads a package directory. An edit is an overlay (``{path relative to the
package: source}``, as :mod:`bench.edit_slice.variant` loads it); :func:`materialize`
writes the original package's ``.py`` files with the overlay applied under
``<dest>/<package path>`` and returns that directory, so absolute imports of the package
(``from bench.template_app.app.models import Item``) resolve as in the original.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def materialize(original: Path, package: str, overlay: Mapping[str, str], dest: Path) -> Path:
    """Write ``original`` (the package directory) with ``overlay`` applied under
    ``dest``; the copy's package directory."""
    original = Path(original)
    target = Path(dest).joinpath(*package.split("."))
    for path in sorted(original.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(original).as_posix()
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(overlay.get(rel, path.read_text()))
    for rel, src in overlay.items():
        out = target / rel
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(src)
    return target


__all__ = ["materialize"]
