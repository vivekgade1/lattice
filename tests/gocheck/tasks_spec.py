"""A tiny spec of the ``fixtures/app`` tasks service (Gin + GORM), for layer A through
``lattice.spec.check`` and the CLI::

    python -m lattice.spec check --spec tests.gocheck.tasks_spec:SPEC \\
        --app tests.gocheck.tasks_spec:make_app --db tests.gocheck.tasks_spec:build_db \\
        --runtime go --go-src tests/gocheck/fixtures/app --layers A
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Machine, Spec
from lattice.spec.process import ProcessApp
from lattice.typesys import Scalar, Schema

S = Scalar
APP_DIR = Path(__file__).parent / "fixtures" / "app"

USER = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("password_hash", S.STR), ("role", S.STR), ("token", S.STR)],
    keys=[("id",), ("token",)],
    private=["password_hash"],
)
TASK = Schema.from_pairs(
    [("id", S.INT), ("owner_id", S.INT), ("title", S.STR), ("state", S.STR)],
    keys=[("id",)],
    owner="owner_id",
    one_of={"state": ("todo", "doing", "done", "dropped")},
)
COMMENT = Schema.from_pairs(
    [("id", S.INT), ("task_id", S.INT), ("body", S.STR)],
    keys=[("id",)],
    owner_via=("task_id", "Task"),
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Task": TASK, "Comment": COMMENT},
        table_names={"User": "users", "Task": "tasks", "Comment": "comments"},
        roles=frozenset({"Public", "Member", "Admin"}),
        users_table="User",
        roles_from=RoleColumn("role"),
        error_body="fastapi",
    ),
)
SPEC = Spec(
    profile=PROFILE,
    machines=(
        Machine(
            "Task",
            "state",
            {"todo": ("doing", "dropped"), "doing": ("done", "dropped")},
            initial=("todo",),
        ),  # fmt: skip
    ),
)


def make_app(db_path: Path) -> ProcessApp:
    """The service's binary (layer A never starts it)."""
    return ProcessApp(["./tasks"], db_path=db_path, cwd=APP_DIR)


def build_db(path: Path) -> Path:
    sqlite3.connect(path).close()
    return path
