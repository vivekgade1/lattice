"""Seed data for the vendored project-management-api (``bench/import_repos/projmgmt``).

``build_seed_db(path)`` creates the tables (``create_app``) and fills them through the
app's own models (``User.set_password`` hashes with bcrypt). Roles are the ``user_type``
enum: ``manager`` (``role_manager``) and ``employee`` (``alice``, ``bob``). Projects are
owned through ``user_id`` (a manager assigns a project to any user): ``alice`` owns two,
``bob`` one; tasks belong to projects. The ``users`` table has no active flag, so no seed
user can be refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos.projmgmt import dispose, make_app

# handle -> (name, email, password, user_type)
SEED_USERS: dict[str, tuple[str, str, str, str]] = {
    "role_manager": ("Maya Manager", "maya@example.com", "managerpass1", "manager"),
    "alice": ("Alice Archer", "alice@example.com", "alicepass1", "employee"),
    "bob": ("Bob Baker", "bob@example.com", "bobpass1", "employee"),
}
# (owner, name, description)
SEED_PROJECTS: tuple[tuple[str, str, str | None], ...] = (
    ("alice", "Website redesign", "New marketing site"),  # 1
    ("alice", "Mobile app", None),  # 2
    ("bob", "Data pipeline", "Nightly ETL"),  # 3
)
# (project id, title, description, status)
SEED_TASKS: tuple[tuple[int, str, str | None, str], ...] = (
    (1, "Design homepage", "Mockups first", "pending"),  # 1
    (1, "Write copy", None, "in_progress"),  # 2
    (2, "Set up CI", "GitHub Actions", "completed"),  # 3
    (3, "Schema draft", None, "pending"),  # 4
)

USER_IDS = {"role_manager": 1, "alice": 2, "bob": 3}

# role (the user_type enum value) -> the seed user who holds it
ROLES: dict[str, str] = {"manager": "role_manager", "employee": "alice"}
# seed users who exist but must be refused, with the reason: none can be (no active flag)
REFUSED: dict[str, str] = {}
# a route only an authenticated caller passes: (method, path, json)
AUTH_PROBE: tuple[str, str, dict | None] = ("GET", "/users", None)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    from bench.import_repos.projmgmt.app import db
    from bench.import_repos.projmgmt.app.projects.models import Project
    from bench.import_repos.projmgmt.app.tasks.models import Task
    from bench.import_repos.projmgmt.app.users.models import User, UserType

    path = Path(path)
    if path.exists():
        path.unlink()
    app = make_app(path)
    try:
        with app.app_context():
            users = {}
            for handle, (name, email, password, user_type) in SEED_USERS.items():
                user = User()
                user.name = name
                user.email = email
                user.set_password(password)
                user.user_type = UserType(user_type)
                db.session.add(user)
                users[handle] = user
            db.session.flush()
            projects = []
            for owner, name, description in SEED_PROJECTS:
                project = Project()
                project.name = name
                project.description = description
                project.user_id = users[owner].id
                db.session.add(project)
                projects.append(project)
            db.session.flush()
            for project_id, title, description, status in SEED_TASKS:
                task = Task()
                task.title = title
                task.description = description
                task.status = status
                task.project_id = projects[project_id - 1].id
                db.session.add(task)
            db.session.commit()
    finally:
        dispose(app)
    return path


def login_headers(client: Any, handle: str) -> dict[str, str]:
    """Log in through the app's own route (``POST /auth/login`` with JSON ``email`` and
    ``password``; answer ``{"access_token": ...}``) and return the
    ``Authorization: Bearer <token>`` header the ``token_required`` decorator reads."""
    _, email, password, _ = SEED_USERS[handle]
    answer = client.post("/auth/login", json={"email": email, "password": password})
    if answer.status_code != 200:
        raise RuntimeError(f"login as {handle!r} answered {answer.status_code}: {answer.text}")
    return {"Authorization": f"Bearer {answer.json()['access_token']}"}
