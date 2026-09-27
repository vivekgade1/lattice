"""Loop corpus 10 -- notify a project's watchers after a new comment (FastAPI + SQLModel).

Loop shape: `for watcher in watchers: notify(...)` -- one side effect per row, after the
            insert is committed.
Construct:  for_each(first, native.notify_watcher, col("email"), project_id, body) on a
            bounded input (limit n=100): an outbox row per watcher in the endpoint's
            transaction, dispatched in order after commit.
Targets:    API (Lattice runtime vs the original FastAPI app, same database).
Bound:      the original notifies every watcher; Lattice needs a bounded input (L439),
            so it notifies the first 100 by id (generated projects have fewer).
"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from pydantic import BaseModel
from pydantic import Field as BodyField
from sqlmodel import Field, Session, SQLModel, create_engine, select

from lattice.profile import ApiProfile, NativeSig, Profile
from lattice.typesys import Scalar, Schema

SHAPE = "side effect per row"
CONSTRUCT = "for_each + outbox (API)"
HINT = "for_each("  # what the L127 hint on the original loop names

SENT: list[tuple] = []  # notifications sent, in order (both apps call the same function)


def notify_watcher(email: str, project_id: int, body: str) -> None:
    """The mail/push integration (idempotent: at-least-once delivery)."""
    SENT.append((email, project_id, body))


# --- original -----------------------------------------------------------------------------
class FanUser(SQLModel, table=True):
    __tablename__ = "fan_users"
    id: int | None = Field(default=None, primary_key=True)
    email: str
    role: str
    token: str


class FanProject(SQLModel, table=True):
    __tablename__ = "fan_projects"
    id: int | None = Field(default=None, primary_key=True)
    name: str


class FanWatcher(SQLModel, table=True):
    __tablename__ = "fan_watchers"
    id: int | None = Field(default=None, primary_key=True)
    project_id: int
    email: str


class FanComment(SQLModel, table=True):
    __tablename__ = "fan_comments"
    id: int | None = Field(default=None, primary_key=True)
    project_id: int
    author_id: int
    body: str


class CommentIn(BaseModel):
    body: str = BodyField(min_length=1, max_length=500)


engine = create_engine("sqlite://")  # the deployment's database (tests override get_session)
app = FastAPI()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def current_user(session: SessionDep, authorization: Annotated[str, Header()]) -> FanUser:
    token = authorization.removeprefix("Bearer ")
    user = session.exec(select(FanUser).where(FanUser.token == token)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


@app.post("/projects/{project_id}/comments", status_code=201)
def add_comment(
    project_id: Annotated[int, Path(ge=1)],
    payload: CommentIn,
    user: Annotated[FanUser, Depends(current_user)],
    session: SessionDep,
) -> dict:
    if session.get(FanProject, project_id) is None:
        raise HTTPException(status_code=404, detail="project")
    comment = FanComment(project_id=project_id, author_id=user.id, body=payload.body)
    session.add(comment)
    session.commit()
    session.refresh(comment)
    watchers = session.exec(
        select(FanWatcher).where(FanWatcher.project_id == project_id).order_by(FanWatcher.id)
    ).all()
    for watcher in watchers:
        try:
            notify_watcher(watcher.email, project_id, comment.body)
        except Exception:  # a failed notification never fails the request
            pass
    return comment.model_dump()


original = add_comment

# --- Lattice ------------------------------------------------------------------------------
USERS = Schema.from_pairs(
    [("id", Scalar.INT), ("email", Scalar.STR), ("role", Scalar.STR), ("token", Scalar.STR)],
    keys=[("id",)],
    private=["token"],
)
PROJECTS = Schema.from_pairs([("id", Scalar.INT), ("name", Scalar.STR)], keys=[("id",)])
WATCHERS = Schema.from_pairs(
    [("id", Scalar.INT), ("project_id", Scalar.INT), ("email", Scalar.STR)], keys=[("id",)]
)
COMMENTS = Schema.from_pairs(
    [("id", Scalar.INT), ("project_id", Scalar.INT), ("author_id", Scalar.INT),
     ("body", Scalar.STR)],
    keys=[("id",)],
    owner="author_id",
)  # fmt: skip
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"Users": USERS, "Projects": PROJECTS, "Watchers": WATCHERS, "Comments": COMMENTS},
        table_names={
            "Users": "fan_users",
            "Projects": "fan_projects",
            "Watchers": "fan_watchers",
            "Comments": "fan_comments",
        },
        roles=frozenset({"Public", "Customer", "Admin"}),
        natives={"notify_watcher": NativeSig((Scalar.STR, Scalar.INT, Scalar.STR), effect=True)},
        error_body="fastapi",
    ),
)
NATIVES = {"notify_watcher": notify_watcher}

LATTICE = """# lattice: v1
@endpoint("POST", "/projects/{project_id}/comments", auth=Customer)
def add_comment(
    project_id: Int(ge=1), body: Str(min_len=1, max_len=500), user: CurrentUser
) -> Created | NotFound:
    projects = fetch(Projects)
    wanted = where(projects, col("id") == project_id)
    project = one(wanted, else_=NotFound("project"))
    comment = insert(Comments, owner=user, project_id=project_id, body=body)
    watchers = fetch(Watchers)
    watching = where(watchers, col("project_id") == project_id)
    ordered = sort(watching, by=["id"])
    first = limit(ordered, n=100)
    for_each(first, native.notify_watcher, col("email"), project_id, body)
    return comment
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[tuple]]:
    users = [(i, f"user{i}@example.com", "Customer", f"tok-{i}") for i in range(1, 4)]
    projects = [(i, f"project {i}") for i in range(1, rng.randint(1, 4) + 1)]
    ids = rng.sample(range(1, 200), rng.randint(0, 30))  # watcher ids not in project order
    watchers = [(i, rng.randint(1, len(projects) + 1), f"w{i}@example.com") for i in sorted(ids)]
    rng.shuffle(watchers)
    return {"fan_users": users, "fan_projects": projects, "fan_watchers": watchers}


def requests(rng: random.Random, inputs: dict[str, list[tuple]]) -> list[dict]:
    n = len(inputs["fan_projects"])
    out = []
    for i in range(5):
        project = rng.randint(1, n + 1)  # sometimes missing: 404
        out.append(
            {
                "method": "POST",
                "url": f"/projects/{project}/comments",
                "json": {"body": f"note {i}"},
                "headers": {"Authorization": f"Bearer tok-{rng.randint(1, 3)}"},
            }
        )
    out.append(
        {
            "method": "POST",
            "url": "/projects/1/comments",
            "json": {"body": ""},  # too short: 422, nothing written or sent
            "headers": {"Authorization": "Bearer tok-1"},
        }
    )
    return out


def build_db(path: FilePath, inputs: dict[str, list[tuple]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE fan_users (
                id INTEGER PRIMARY KEY, email TEXT NOT NULL, role TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE
            );
            CREATE TABLE fan_projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE fan_watchers (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, email TEXT NOT NULL
            );
            CREATE TABLE fan_comments (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL REFERENCES fan_users(id), body TEXT NOT NULL
            );
            """
        )
        conn.executemany("INSERT INTO fan_users VALUES (?, ?, ?, ?)", inputs["fan_users"])
        conn.executemany("INSERT INTO fan_projects VALUES (?, ?)", inputs["fan_projects"])
        conn.executemany("INSERT INTO fan_watchers VALUES (?, ?, ?)", inputs["fan_watchers"])
        conn.commit()
    finally:
        conn.close()


COMPARED_TABLES = ("fan_comments",)
