"""Lattice-owned settings for the vendored DRF tutorial project (not upstream code).

Imports the upstream ``tutorial/settings.py`` unchanged and overrides only what a test
run needs: the database (SQLite; ``make_app`` points it at the caller's file), an
in-memory cache and mail backend, a fixed secret key, ``DEBUG=False``, the test host,
and a fast password hasher. Nothing here reads the environment or any ``.env`` file.
"""

from bench.import_repos.snippets.tutorial.settings import *  # noqa: F403

SECRET_KEY = "lattice-test-secret-snippets-0000000000000000000000000000"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Placeholder: make_app() points the default connection at its db_path before any query.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# Upstream uses Django's default (PBKDF2, ~1M iterations). DRF basic auth hashes the
# password on every request, so probes would spend most of their time hashing; MD5 is
# what Django's own test-suite docs recommend for tests. Hashes are made by the app's
# own make_password either way.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
