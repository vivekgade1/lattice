"""amirpsd/drf_blog_api, vendored as Phase 2 held-out app H1 (Django + DRF, simplejwt).

Upstream code lives in :mod:`bench.import_repos.drf_blog.src` (``config`` project,
``account``, ``blog``, ``comment`` and ``extensions`` apps, ``permissions``); see
``PROVENANCE.md``. This module adds only :func:`make_app` and :func:`dispose`. Django is
process-global: one Django project per process (see
``bench/import_repos/_django_runtime.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos import _django_runtime

SETTINGS_MODULE = "bench.import_repos.drf_blog.lattice_settings"


def make_app(db_path: str | Path) -> Any:
    """The project's WSGI application on the SQLite file at ``db_path``.

    Builds the schema with ``migrate`` when the file has none yet. Blog image uploads go
    to ``<db_path's directory>/media``. The cache (OTP codes, throttle counters; LocMem,
    as upstream) is cleared: each app starts like a fresh server process.
    """
    db_path = Path(db_path).resolve()
    _django_runtime.boot(SETTINGS_MODULE)
    _django_runtime.use_media_root(db_path.parent / "media")
    app = _django_runtime.make_app(db_path, SETTINGS_MODULE)
    from django.core.cache import cache

    cache.clear()
    return app


def dispose(app: Any = None) -> None:
    """Close Django's connections (before snapshotting the database file)."""
    _django_runtime.dispose()
