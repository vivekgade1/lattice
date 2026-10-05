"""yoonge/conduit-drf, vendored as Phase 2 held-out app H3 (Django 4.2 + DRF, simplejwt).

Upstream code lives in :mod:`bench.import_repos.conduit` (``conduit_drf`` project, ``api``
app); see ``PROVENANCE.md``. This module adds only :func:`make_app` and :func:`dispose`.
Django is process-global: one Django project per process (see
``bench/import_repos/_django_runtime.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos import _django_runtime

SETTINGS_MODULE = "bench.import_repos.conduit.lattice_settings"


def make_app(db_path: str | Path) -> Any:
    """The project's WSGI application on the SQLite file at ``db_path`` (built with
    ``migrate`` when the file has no schema yet)."""
    return _django_runtime.make_app(Path(db_path).resolve(), SETTINGS_MODULE)


def dispose(app: Any = None) -> None:
    """Close Django's connections (before snapshotting the database file)."""
    _django_runtime.dispose()
