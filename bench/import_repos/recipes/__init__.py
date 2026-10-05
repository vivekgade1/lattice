"""LondonAppDeveloper/recipe-app-api, vendored as a Phase 2 dev app (Django + DRF).

Upstream code lives in :mod:`bench.import_repos.recipes.app` (``app`` project, ``core``,
``user`` and ``recipe`` apps); see ``PROVENANCE.md``. This module adds only
:func:`make_app` and :func:`dispose`. Django is process-global: one Django project per
process (see ``bench/import_repos/_django_runtime.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos import _django_runtime

SETTINGS_MODULE = "bench.import_repos.recipes.lattice_settings"


def make_app(db_path: str | Path) -> Any:
    """The project's WSGI application on the SQLite file at ``db_path``.

    Builds the schema with ``migrate`` when the file has none yet. Recipe image uploads
    go to ``<db_path's directory>/media``.
    """
    db_path = Path(db_path).resolve()
    _django_runtime.boot(SETTINGS_MODULE)
    _django_runtime.use_media_root(db_path.parent / "media")
    return _django_runtime.make_app(db_path, SETTINGS_MODULE)


def dispose(app: Any = None) -> None:
    """Close Django's connections (before snapshotting the database file)."""
    _django_runtime.dispose()
