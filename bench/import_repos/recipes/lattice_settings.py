"""Lattice-owned settings for the vendored recipe-app-api project (not upstream code).

Imports the upstream ``app/app/settings.py`` unchanged and overrides only what a test
run needs: the database (SQLite instead of Postgres from ``DB_*`` env vars; ``make_app``
points it at the caller's file), uploads (``MEDIA_ROOT``, moved next to the database by
``make_app``), an in-memory cache and mail backend, a fixed secret key, ``DEBUG=False``,
the test host, a fast password hasher, and the auto field the upstream migrations use.
Nothing here reads the environment or any ``.env`` file.
"""

from bench.import_repos.recipes.app.app.settings import *  # noqa: F403

SECRET_KEY = "lattice-test-secret-recipes-00000000000000000000000000000"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Placeholder: make_app() points the default connection at its db_path before any query.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# Upstream: '/vol/web/media' and '/vol/web/static' (Docker volumes). make_app() moves
# MEDIA_ROOT next to the database file; nothing collects static files.
MEDIA_ROOT = "/nonexistent-lattice-media-root"
STATIC_ROOT = "/nonexistent-lattice-static-root"

# Django 2.1 (upstream's pin) created implicit primary keys as AutoField, and the
# upstream migrations say so; Django >= 3.2 would otherwise warn (models.W042).
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# Upstream uses Django's default (PBKDF2, ~1M iterations); MD5 is what Django's own
# test-suite docs recommend for tests. Hashes are made by the app's make_password.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
