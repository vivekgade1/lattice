"""Lattice-owned settings for the vendored drf_blog_api project (not upstream code).

Imports the upstream ``src/config/settings.py`` (whose ``decouple`` values come from
``_lattice_env.py``, never from a ``.env`` file or the environment) and overrides only what
a test run needs: the database (SQLite instead of Postgres from ``DB_*``; ``make_app``
points it at the caller's file), an in-memory cache (upstream: Django's default, also
LocMem) and mail backend, a fixed secret key, ``DEBUG=False``, the test host, uploads and
static files moved off the vendored tree, logging without upstream's file handler
(``src/log/app.log`` inside the vendored tree), and a fast password hasher.
"""

from bench.import_repos.drf_blog.src.config.settings import *  # noqa: F403

SECRET_KEY = "lattice-test-secret-drf-blog-000000000000000000000000000000"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Placeholder: make_app() points the default connection at its db_path before any query.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
# Upstream sets no CACHES, i.e. Django's default LocMemCache: the OTP codes and the
# throttle counters live there. make_app() clears it (a fresh server process).
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Upstream: <src>/media and <src>/static. make_app() moves MEDIA_ROOT next to the
# database file; nothing collects static files.
MEDIA_ROOT = "/nonexistent-lattice-media-root"
STATIC_ROOT = "/nonexistent-lattice-static-root"

# Upstream's LOGGING adds a FileHandler on <src>/log/app.log (inside the vendored tree);
# keep the console handler only.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"": {"level": "WARNING", "handlers": ["console"]}},
}

# Users log in with an OTP and have unusable passwords; the two-step password is hashed
# by the app's set_password. MD5 is what Django's test-suite docs recommend for tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
