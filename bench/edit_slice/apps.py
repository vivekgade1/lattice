"""The apps an edit-slice ticket can target (``Ticket.repo``), described once.

Everything the harness needs to know about one app lives here, as data:

* ``template`` -- the official FastAPI template (``bench/template_app``), the app the
  importer's lifters are developed against (tickets t1-t12);
* ``taskflow`` and ``justnotes`` -- the importer's held-out repos
  (``bench/import_repos/``, see their ``PROVENANCE.md``), round 3's tickets t19-t24.

Per app: the package a variant replaces and its directory (:mod:`bench.edit_slice.variant`),
whether its tables already live in their own SQLAlchemy registry (the held-out repos'
``app/_lattice_registry.py``) or a variant must redirect ``sqlmodel.SQLModel``, the login
route and the users the smoke run signs in as (from the app's own seed), the seed tables
in insertion order (:mod:`bench.edit_slice.smoke`), the password-hash columns (for the
verifier's normaliser), and what arm P is shown (:mod:`bench.edit_slice.prompts`).

How a variant app is *built* (the app factory) is in :mod:`bench.edit_slice.variant`; how
the hidden oracle describes the app's tables and auth is in :mod:`bench.edit_slice.oracle`
(template) and :mod:`bench.edit_slice.repos` (held-out repos).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Repo:
    """One app a ticket can target (see the module docstring)."""

    name: str
    package: str  # the app package a variant replaces
    root: Path  # its directory
    isolated_registry: bool  # tables in the app's own `_lattice_registry` registry
    login_path: str
    seed_users: Callable[[], Mapping[str, tuple[str, str]]]
    seed_tables: tuple[str, ...]  # the seed's tables, parents first
    hash_columns: tuple[str, ...]
    about: str  # arm P's description of the app
    context: tuple[str, ...]  # files arm P is shown besides the ticket's own

    def users(self) -> Mapping[str, tuple[str, str]]:
        """Smoke handle -> (login name, password): the users of the app's own seed."""
        return self.seed_users()

    def seed(self) -> Callable[[Path], Path]:
        """The app's seed builder (``path -> path``), imported lazily."""
        if self.name == "template":
            from bench.template_app.seed import build_template_db

            return build_template_db
        if self.name == "taskflow":
            from bench.import_repos.taskflow.seed import build_seed_db

            return build_seed_db
        from bench.import_repos.justnotes.seed import build_seed_db as build_notes_db

        return build_notes_db


def _template_users() -> dict[str, tuple[str, str]]:
    from bench.template_app.seed import TEMPLATE_USERS

    return {h: (email, password) for h, (email, password, _) in TEMPLATE_USERS.items()}


def _taskflow_users() -> dict[str, tuple[str, str]]:
    from bench.import_repos.taskflow.seed import SEED_USERS

    return {email.split("@")[0]: (email, password) for email, _, password in SEED_USERS}


def _justnotes_users() -> dict[str, tuple[str, str]]:
    from bench.import_repos.justnotes.seed import SEED_USERS

    return {name: (name, password) for name, password in SEED_USERS}


TEMPLATE = Repo(
    name="template",
    package="bench.template_app.app",
    root=BENCH / "template_app" / "app",
    isolated_registry=False,
    login_path="/api/v1/login/access-token",
    seed_users=_template_users,
    seed_tables=("user", "item"),
    hash_columns=("hashed_password",),
    about=(
        "the backend of the official full-stack FastAPI template (FastAPI, SQLModel and "
        "SQLAlchemy on SQLite, JWT bearer authentication)"
    ),
    context=("models.py", "api/deps.py"),  # + crud.py when a shown file imports it
)
TASKFLOW = Repo(
    name="taskflow",
    package="bench.import_repos.taskflow.app",
    root=BENCH / "import_repos" / "taskflow" / "app",
    isolated_registry=True,
    login_path="/api/v1/auth/token",
    seed_users=_taskflow_users,
    seed_tables=("users", "tasks"),
    hash_columns=("password_hash",),
    about=(
        "TaskFlow, a multi-user task management API (FastAPI, SQLModel and SQLAlchemy on "
        "SQLite, JWT bearer authentication)"
    ),
    context=("models.py", "crud.py", "auth.py"),
)
JUSTNOTES = Repo(
    name="justnotes",
    package="bench.import_repos.justnotes.app",
    root=BENCH / "import_repos" / "justnotes" / "app",
    isolated_registry=True,
    login_path="/auth/login",
    seed_users=_justnotes_users,
    seed_tables=("user", "note", "tag", "keyword", "notetaglink", "notekeywordlink"),
    hash_columns=("password",),
    about=(
        "JustNotes, a note-taking API with tags and keywords (FastAPI, SQLModel and "
        "SQLAlchemy on SQLite, JWT bearer authentication)"
    ),
    context=(
        "models/note.py",
        "models/tag.py",
        "models/user.py",
        "database/note_crud.py",
        "database/tag_crud.py",
        "api/auth.py",
    ),
)

REPOS: dict[str, Repo] = {r.name: r for r in (TEMPLATE, TASKFLOW, JUSTNOTES)}


def repo(name: str) -> Repo:
    try:
        return REPOS[name]
    except KeyError:
        raise KeyError(f"unknown repo {name!r} (one of {sorted(REPOS)})") from None


__all__ = ["JUSTNOTES", "REPOS", "TASKFLOW", "TEMPLATE", "Repo", "repo"]
