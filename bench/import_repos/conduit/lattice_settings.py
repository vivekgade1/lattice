"""Lattice-owned settings for the vendored conduit-drf project (not upstream code).

Imports the upstream ``conduit_drf/settings.py`` and overrides only what a test run
needs: the database (SQLite instead of MySQL on localhost:3306; ``make_app`` points it at
the caller's file), an in-memory cache and mail backend, a fixed secret key,
``DEBUG=False``, the test host, and a fast password hasher. Upstream reads no environment
variable and no ``.env`` file.
"""

from bench.import_repos.conduit.conduit_drf.settings import *  # noqa: F403

SECRET_KEY = "lattice-test-secret-conduit-0000000000000000000000000000000"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Placeholder: make_app() points the default connection at its db_path before any query.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Upstream uses Django's default (PBKDF2); MD5 is what Django's test-suite docs recommend
# for tests. Hashes are still made by the app's set_password / make_password.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
