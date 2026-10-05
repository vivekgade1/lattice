"""Lattice-owned stand-in for ``decouple.config`` (not upstream code).

Upstream's settings, logger and URL modules read their configuration with python-decouple,
which looks for a ``.env`` / ``settings.ini`` file next to the caller and in every parent
directory, and in ``os.environ``. A test run must never read the host's ``.env`` file or
environment, so the three ``from decouple import config`` lines import this module instead
(``LATTICE MOD``). It answers from the fixed values below, with decouple's call shape
(``config(name, default=..., cast=...)``) and its boolean cast.

``DEBUG`` is ``"False"``: upstream's non-debug branch (database from ``DB_*``, which
``lattice_settings.py`` replaces with SQLite anyway; ``STATIC_ROOT``; no static URL
patterns).
"""

from __future__ import annotations

from typing import Any

VALUES: dict[str, str] = {
    "SECRET_KEY": "lattice-test-secret-drf-blog-000000000000000000000000000000",
    "DEBUG": "False",
    "ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
    "DB_ENGINE": "django.db.backends.sqlite3",
    "DB_NAME": ":memory:",
    "DB_USER": "",
    "DB_HOST": "",
    "DB_PORT": "",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
}

_UNDEFINED = object()
_TRUE = {"y", "yes", "t", "true", "on", "1"}
_FALSE = {"n", "no", "f", "false", "off", "0", ""}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"invalid truth value {value!r}")


def config(name: str, default: Any = _UNDEFINED, cast: Any = _UNDEFINED) -> Any:
    """``VALUES[name]`` (else ``default``), cast as decouple would."""
    if name in VALUES:
        value: Any = VALUES[name]
    elif default is not _UNDEFINED:
        value = default
    else:
        raise KeyError(f"{name} not found (Lattice: add it to _lattice_env.VALUES)")
    if cast is _UNDEFINED:
        return value
    if cast is bool:
        return _bool(value)
    return cast(value)
