# ruff: noqa
"""Authenticated task routes for the TaskFlow API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, select

from bench.import_repos.taskflow.app import crud
from bench.import_repos.taskflow.app.auth import CurrentUser
from bench.import_repos.taskflow.app.database import get_session
from bench.import_repos.taskflow.app.models import (
    Task,
    TaskCreate,
    TaskList,
    TaskPriority,
    TaskPublic,
    TaskStatus,
    TaskUpdate,
    utc_now,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
SessionDependency = Annotated[Session, Depends(get_session)]


def require_task(session: Session, task_id: int, owner_id: int) -> Task:
    """Return an owned task or a consistent 404 response."""

    task = crud.get_task(session, task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.post("", response_model=TaskPublic, status_code=status.HTTP_201_CREATED)
def create_task(
    task_input: TaskCreate,
    session: SessionDependency,
    current_user: CurrentUser,
):
    """Create a task owned by the authenticated user."""

    return crud.create_task(session, task_input, current_user.id)


@router.get("", response_model=TaskList)
def read_tasks(
    session: SessionDependency,
    current_user: CurrentUser,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    priority: TaskPriority | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """List the current user's tasks with filters, search, and pagination."""

    tasks, total = crud.list_tasks(
        session,
        owner_id=current_user.id,
        status=task_status,
        priority=priority,
        search=search,
        limit=limit,
        offset=offset,
    )
    return TaskList(items=tasks, total=total, limit=limit, offset=offset)


@router.get("/summary")
def read_task_summary(session: SessionDependency, current_user: CurrentUser) -> dict:
    """Count the current user's tasks by status, and the overdue ones."""

    tasks = session.exec(select(Task).where(Task.owner_id == current_user.id)).all()
    now = utc_now()  # due dates are stored naive UTC, like every timestamp here
    by_status = {task_status.value: 0 for task_status in TaskStatus}
    overdue = 0
    for task in tasks:
        by_status[TaskStatus(task.status).value] += 1
        if (
            task.due_date is not None
            and task.due_date < now
            and task.status != TaskStatus.DONE
        ):
            overdue += 1
    return {"total": len(tasks), "by_status": by_status, "overdue": overdue}


@router.get("/{task_id}", response_model=TaskPublic)
def read_task(task_id: int, session: SessionDependency, current_user: CurrentUser):
    """Return an owned task by ID."""

    return require_task(session, task_id, current_user.id)


@router.patch("/{task_id}", response_model=TaskPublic)
def update_task(
    task_id: int,
    task_input: TaskUpdate,
    session: SessionDependency,
    current_user: CurrentUser,
):
    """Partially update an owned task."""

    task = require_task(session, task_id, current_user.id)
    update_data = task_input.model_dump(exclude_unset=True)
    for required_field in ("title", "status", "priority"):
        if required_field in update_data and update_data[required_field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{required_field} cannot be null",
            )
    return crud.update_task(session, task, task_input)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Response:
    """Delete an owned task."""

    task = require_task(session, task_id, current_user.id)
    crud.delete_task(session, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
