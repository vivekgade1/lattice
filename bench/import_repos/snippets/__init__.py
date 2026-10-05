"""The Django REST framework tutorial ("snippets"), vendored as a Phase 2 dev app.

Upstream code lives in :mod:`bench.import_repos.snippets.tutorial` (project) and
:mod:`bench.import_repos.snippets.snippets` (app); see ``PROVENANCE.md``. This module adds
only :func:`make_app` and :func:`dispose`. Django is process-global: one Django project
per process (see ``bench/import_repos/_django_runtime.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos import _django_runtime

SETTINGS_MODULE = "bench.import_repos.snippets.lattice_settings"


def make_app(db_path: str | Path) -> Any:
    """The tutorial's WSGI application on the SQLite file at ``db_path``.

    Builds the schema with ``migrate`` when the file has none yet.
    """
    return _django_runtime.make_app(db_path, SETTINGS_MODULE)


def dispose(app: Any = None) -> None:
    """Close Django's connections (before snapshotting the database file)."""
    _django_runtime.dispose()
