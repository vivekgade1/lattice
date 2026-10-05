"""Shared Lattice-owned runtime for the vendored Django apps (``snippets``, ``recipes``).

Not upstream code. Django's settings, app registry and connections are process-global,
so a process hosts **one** Django project: :func:`boot` refuses a second settings module
(run each Django app in its own subprocess, as ``tests/bench/test_phase2_apps.py`` does).

:func:`make_app` points the default connection at a SQLite file, builds the schema with
``migrate`` when the file has none yet, and returns the project's WSGI application.
Dispose is :func:`dispose` (``connections.close_all()``), to run before snapshotting the
database file.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any


def boot(settings_module: str) -> None:
    """Configure Django for ``settings_module`` (idempotent for the same module)."""
    import django
    from django.apps import apps
    from django.conf import settings

    current = os.environ.get("DJANGO_SETTINGS_MODULE")
    if apps.ready:
        if settings.SETTINGS_MODULE != settings_module:
            raise RuntimeError(
                f"this process already runs Django project {settings.SETTINGS_MODULE!r}; "
                f"{settings_module!r} needs its own process"
            )
        return
    if current not in (None, settings_module) and settings.configured:
        raise RuntimeError(f"DJANGO_SETTINGS_MODULE is already {current!r}")
    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    django.setup()


def _has_schema(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='django_migrations'"
        ).fetchone()
    finally:
        con.close()
    return row is not None


def use_database(db_path: str | Path) -> Path:
    """Point the ``default`` connection at ``db_path``; migrate it if it has no schema."""
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections

    db_path = Path(db_path).resolve()
    connections.close_all()
    settings.DATABASES["default"]["NAME"] = str(db_path)
    connections["default"].settings_dict["NAME"] = str(db_path)
    if not _has_schema(db_path):
        call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
        connections.close_all()
    return db_path


def use_media_root(path: str | Path) -> None:
    """Point ``MEDIA_ROOT`` (uploads) at ``path`` and tell the storages it moved."""
    from django.conf import settings
    from django.test.signals import setting_changed

    settings.MEDIA_ROOT = str(path)
    setting_changed.send(sender=type(settings), setting="MEDIA_ROOT", value=str(path), enter=True)


def make_app(db_path: str | Path, settings_module: str) -> Any:
    """The project's WSGI application on the SQLite file at ``db_path``."""
    from django.core.wsgi import get_wsgi_application

    boot(settings_module)
    use_database(db_path)
    return get_wsgi_application()


def dispose() -> None:
    """Close every connection (run before reading or copying the database file)."""
    from django.db import connections

    connections.close_all()
