"""The oracles of the TaskFlow tickets (t19-t21): tables, auth, routes, cases.

TaskFlow (``bench/import_repos/taskflow``) as the generated suite sees it: ``users`` (the
login is the e-mail, the hash ``password_hash``, an ``is_active`` flag, no superusers) and
``tasks`` owned through ``owner_id``; every task route is owner-scoped and answers another
user's task with the same 404 "Task not found" as a missing one. After the seed rewrite
(:mod:`bench.edit_slice.repos`) the users are ``admin`` 1, ``alice`` 2, ``bob`` 3 and
``carol`` 4 (inactive), and tasks 1-8 belong round-robin to them (alice: 2 and 6, bob: 3
and 7), with statuses todo / in_progress / done in turn (task 2 in progress, 6 done; 3
done, 7 todo) and a past due date on two tasks in three.

A request stamps ``updated_at`` (and, for t21, ``completed_at``) with a fresh
``utc_now()``: whether one request writes one stamp or several is not something the
tickets settle, so on a mismatch the records are compared once more with every new
timestamp as "a new timestamp" (:func:`fresh_timestamps`; still in its spelling, still
distinct from every seed value).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from bench.edit_slice.repos import IdMap, RepoOracle, Writer, add_rows
from bench.edit_slice.variant import TASKFLOW_SECRET, Variant
from lattice.importer.suite import (
    ACTOR,
    ADMIN,
    OTHER,
    AuthSpec,
    ColumnSpec,
    ErrorSpec,
    ParamSpec,
    RouteSpec,
    SeedPlan,
    TableSpec,
)
from lattice.importer.verify import Case, CaseRecord

API = "/api/v1"
TASKS = f"{API}/tasks"
MISSING_TASK = 999  # no such task
MISSING_USER = 999_999  # a token naming no user

C, P = ColumnSpec, ParamSpec
USERS = TableSpec(
    "users",
    (
        C("email", "email", unique=True, max_len=320),
        C("full_name", "str", min_len=2, max_len=120),
        C("password_hash", "str"),
        C("is_active", "bool"),
        C("id", "int", primary_key=True),
        C("created_at", "datetime"),
    ),
)
# status and priority are enums; the seed's text values are replaced by fixup()
TASKS_TABLE = TableSpec(
    "tasks",
    (
        C("title", "str", min_len=3, max_len=120),
        C("description", "str", nullable=True, max_len=1000),
        C("status", "str"),
        C("priority", "str"),
        C("due_date", "datetime", nullable=True),
        C("id", "int", primary_key=True),
        C("owner_id", "int", foreign_key="users.id"),
        C("created_at", "datetime"),
        C("updated_at", "datetime"),
    ),
    owner="owner_id",
)
TABLES = (USERS, TASKS_TABLE)
ORDER = ("users", "tasks")
STATUSES = ("todo", "in_progress", "done")
PRIORITIES = ("low", "medium", "high")


def auth(ids: IdMap) -> AuthSpec:
    from bench.import_repos.taskflow.app.auth import create_access_token, hash_password

    def mint_token(user_id: uuid.UUID | int) -> str:
        uid = user_id if isinstance(user_id, int) else ids.get(user_id, MISSING_USER)
        return create_access_token(str(uid), TASKFLOW_SECRET, 30)

    return AuthSpec(
        users_table="users",
        email_column="email",
        password_column="password_hash",
        active_column="is_active",
        admin_column=None,
        login_path=f"{API}/auth/token",
        hash_password=hash_password,
        mint_token=mint_token,
    )


def fixup(plan: SeedPlan) -> None:
    """Valid statuses and priorities; due dates a week after creation."""
    for i, row in enumerate(plan.rows["tasks"]):
        row["status"] = STATUSES[i % 3]
        row["priority"] = PRIORITIES[(i + 1) % 3]
        if row["due_date"] is not None:
            row["due_date"] = row["created_at"] + timedelta(days=7)


# ---- routes -----------------------------------------------------------------------------------

TASK_ID = P("task_id", "int", "path")
NOT_FOUND = ErrorSpec(404, "Task not found", "not_found")
NOT_OWNER = ErrorSpec(404, "Task not found", "not_owner")
TITLE = P("title", "str", "body", min_len=3, max_len=120)
DESCRIPTION = P("description", "str", "body", required=False, nullable=True, max_len=1000)
UPDATE_TITLE = P("title", "str", "body", required=False, nullable=True, min_len=3, max_len=120)

SUMMARY = RouteSpec("read_task_summary", "GET", f"{TASKS}/summary", "other", "tasks")
BULK = RouteSpec("bulk_update_status", "POST", f"{TASKS}/bulk-status", "other", "tasks")
CREATE = RouteSpec("create_task", "POST", TASKS, "create", "tasks", params=(TITLE, DESCRIPTION))
LIST = RouteSpec(
    "read_tasks", "GET", TASKS, "list", "tasks",
    params=(
        P("limit", "int", "query", required=False, default=20, ge=1, le=100),
        P("offset", "int", "query", required=False, default=0, ge=0),
        P("search", "str", "query", required=False, min_len=1, max_len=100),
    ),
    pagination=("offset", "limit"),
)  # fmt: skip
READ = RouteSpec("read_task", "GET", f"{TASKS}/{{task_id}}", "read", "tasks",
                 params=(TASK_ID,), errors=(NOT_FOUND, NOT_OWNER))  # fmt: skip
UPDATE = RouteSpec("update_task", "PATCH", f"{TASKS}/{{task_id}}", "update", "tasks",
                   params=(TASK_ID, UPDATE_TITLE, DESCRIPTION),
                   errors=(NOT_FOUND, NOT_OWNER))  # fmt: skip

ROUTES: dict[str, tuple[RouteSpec, ...]] = {
    "t19_task_summary": (SUMMARY,),
    "t20_bulk_status": (BULK,),
    "t21_completed_at": (CREATE, LIST, READ, UPDATE),
}
# route name -> the reference functions whose raise sites the route's cases must reach
FUNCTIONS: dict[str, tuple[str, ...]] = {
    "read_task_summary": ("read_task_summary",),
    "bulk_update_status": ("bulk_update_status", "require_task"),
    "create_task": ("create_task",),
    "read_tasks": ("read_tasks",),
    "read_task": ("read_task", "require_task"),
    "update_task": ("update_task", "require_task"),
}


def branches(ticket_id: str) -> Callable[[Variant], dict[str, list[Callable[..., Any]]]]:
    def of(ref: Variant) -> dict[str, list[Callable[..., Any]]]:
        routes = ref.module("routes")
        return {r.name: [getattr(routes, f) for f in FUNCTIONS[r.name]]
                for r in ROUTES[ticket_id]}  # fmt: skip

    return of


# ---- database variants ------------------------------------------------------------------------


def _task(tid: int, owner: int, title: str, status: str, due: datetime | None,
          created: datetime, **extra: Any) -> dict[str, Any]:  # fmt: skip
    return {"id": tid, "title": title, "description": None, "status": status,
            "priority": "medium", "due_date": due, "owner_id": owner,
            "created_at": created, "updated_at": created, **extra}  # fmt: skip


def writers(ticket_id: str, plan: SeedPlan) -> dict[str, Writer]:
    uid = {u.handle: u.id for u in plan.users}
    epoch = datetime(2026, 3, 1, 9, 0, 0)

    def due(engine: Engine) -> None:
        """Alice's tasks due on either side of now (built when the database is), and one
        overdue task of Bob's."""
        now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        a, b = uid[ACTOR], uid[OTHER]
        add_rows(engine, ticket_id, "tasks", [
            _task(101, a, "Due in two days", "todo", now + timedelta(days=2), epoch),
            _task(102, a, "Due two hours ago", "todo", now - timedelta(hours=2), epoch),
            _task(103, a, "Finished late", "done", now - timedelta(days=3), epoch),
            _task(104, a, "Due yesterday", "in_progress", now - timedelta(days=1), epoch),
            _task(105, a, "Someday", "todo", None, epoch),
            _task(106, b, "Bob's overdue task", "todo", now - timedelta(days=5), epoch),
        ])  # fmt: skip

    def delete_where(sql: str, **params: Any) -> Writer:
        def write(engine: Engine) -> None:
            with engine.begin() as conn:
                conn.execute(text(sql), params)

        return write

    def completed(engine: Engine) -> None:
        """The seed's done tasks were completed a day after creation."""
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE tasks SET completed_at = "
                "strftime('%Y-%m-%d %H:%M:%S.000000', created_at, '+1 day') "
                "WHERE status = 'DONE'"
            ))  # fmt: skip

    return {
        "due": due,
        "no_bob_tasks": delete_where("DELETE FROM tasks WHERE owner_id = :o", o=uid[OTHER]),
        "no_tasks": delete_where("DELETE FROM tasks"),
        "completed": completed,
    }


# ---- ticket-specific cases --------------------------------------------------------------------


def _own(plan: SeedPlan, handle: str, k: int = 0) -> int:
    return plan.owned("tasks", handle)[k]["id"]


def _status(plan: SeedPlan, task_id: int) -> str:
    return next(r["status"] for r in plan.rows["tasks"] if r["id"] == task_id)


def t19_cases(plan: SeedPlan) -> list[Case]:
    def c(what: str, user: str, db: str = "seed") -> Case:
        return Case(f"t19.{what}", "GET", f"{TASKS}/summary", user=user, db=db,
                    route="read_task_summary")  # fmt: skip

    return [
        c("alice", ACTOR),
        c("bob", OTHER),
        c("third_user", ADMIN),
        c("alice_due_on_both_sides_of_now", ACTOR, "due"),
        c("bob_beside_alices_due_tasks", OTHER, "due"),
        c("bob_without_tasks", OTHER, "no_bob_tasks"),
        c("no_tasks_at_all", ACTOR, "no_tasks"),
        Case("t19.task_route_still_works", "GET", f"{TASKS}/{_own(plan, ACTOR)}", user=ACTOR,
             route="read_task_summary"),
        Case("t19.task_route_other_users_task", "GET", f"{TASKS}/{_own(plan, OTHER)}",
             user=ACTOR, route="read_task_summary"),
    ]  # fmt: skip


def t20_cases(plan: SeedPlan) -> list[Case]:
    a1, a2 = _own(plan, ACTOR), _own(plan, ACTOR, 1)
    b1, b2 = _own(plan, OTHER), _own(plan, OTHER, 1)
    assert (_status(plan, a1), _status(plan, a2)) == ("in_progress", "done")

    def c(what: str, body: Any, user: str = ACTOR) -> Case:
        return Case(f"t20.{what}", "POST", f"{TASKS}/bulk-status", user=user, json=body,
                    tables=("tasks",), route="bulk_update_status")  # fmt: skip

    def ids(task_ids: list[Any], status: str = "todo") -> dict[str, Any]:
        return {"task_ids": task_ids, "status": status}

    return [
        c("alice_both_own", ids([a1, a2])),
        c("alice_repeats_an_id", ids([a2, a1, a2])),
        c("alice_already_in_that_status", ids([a1], "in_progress")),
        c("alice_one_done", ids([a1], "done")),
        c("alice_with_bobs_task", ids([a1, b1], "done")),
        c("alice_bobs_task_first", ids([b1, a1], "done")),
        c("alice_with_a_missing_task", ids([a1, a2, MISSING_TASK], "done")),
        c("alice_only_bobs_task", ids([b1])),
        c("bob_both_own", ids([b1, b2], "in_progress"), user=OTHER),
        c("invalid_status", ids([a1], "archived")),
        c("missing_status", {"task_ids": [a1]}),
        c("ids_not_integers", ids(["first"])),
    ]


def t21_cases(plan: SeedPlan) -> list[Case]:
    todo_like, done = _own(plan, ACTOR), _own(plan, ACTOR, 1)
    bobs_done = _own(plan, OTHER)
    assert (_status(plan, todo_like), _status(plan, done), _status(plan, bobs_done)) == (
        "in_progress", "done", "done")  # fmt: skip
    tables = ("tasks",)
    backdated = "2020-01-01T00:00:00"

    def create(what: str, body: dict[str, Any]) -> Case:
        return Case(f"t21.{what}", "POST", TASKS, user=ACTOR, json=body, db="completed",
                    tables=tables, route="create_task")  # fmt: skip

    def patch(what: str, task_id: int, body: dict[str, Any]) -> Case:
        return Case(f"t21.{what}", "PATCH", f"{TASKS}/{task_id}", user=ACTOR, json=body,
                    db="completed", tables=tables, route="update_task")  # fmt: skip

    return [
        create("create_done", {"title": "Ship it", "status": "done"}),
        create("create_todo", {"title": "Later"}),
        create("create_sends_completed_at", {"title": "Backdated", "completed_at": backdated}),
        create("create_done_sends_completed_at",
               {"title": "Backdated", "status": "done", "completed_at": backdated}),
        patch("patch_to_done", todo_like, {"status": "done"}),
        patch("patch_done_again", done, {"status": "done"}),
        patch("patch_title_of_done_task", done, {"title": "Renamed"}),
        patch("patch_reopen", done, {"status": "todo"}),
        patch("patch_sends_completed_at", todo_like, {"completed_at": backdated}),
        patch("patch_done_sends_completed_at", done, {"completed_at": None}),
        patch("patch_status_null", done, {"status": None}),
        patch("patch_other_users_done_task", bobs_done, {"status": "todo"}),
        Case("t21.read_done_task", "GET", f"{TASKS}/{done}", user=ACTOR, db="completed",
             route="read_task"),
        Case("t21.list", "GET", TASKS, user=ACTOR, db="completed", route="read_tasks"),
        Case("t21.list_by_status", "GET", TASKS, user=OTHER, db="completed",
             params={"status": "done"}, route="read_tasks"),
    ]  # fmt: skip


SPECIFIC = {"t19_task_summary": t19_cases, "t20_bulk_status": t20_cases,
            "t21_completed_at": t21_cases}  # fmt: skip

# ---- a request's fresh timestamps -------------------------------------------------------------

_FRESH = re.compile(r"^<datetime:\d+:([^>]*)>$")


def _fresh(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _fresh(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_fresh(x) for x in v]
    if isinstance(v, str) and (m := _FRESH.match(v)):
        return f"<datetime:new:{m.group(1)}>"
    return v


def fresh_timestamps(record: CaseRecord) -> CaseRecord:
    """Every timestamp the request wrote is "a new timestamp" (see the module docstring)."""
    return replace(record, body=_fresh(record.body), db=_fresh(record.db))


def oracle_spec(ticket_id: str) -> RepoOracle:
    return RepoOracle(
        routes=ROUTES[ticket_id],
        tables=TABLES,
        auth=auth,
        order=ORDER,
        fixup=fixup,
        specific=SPECIFIC[ticket_id],
        writers=writers,
        branches=branches(ticket_id),
        canon=fresh_timestamps,
        canon_name="FRESH_TIMESTAMPS",
    )


__all__ = ["ROUTES", "SPECIFIC", "TABLES", "auth", "fresh_timestamps", "oracle_spec"]
