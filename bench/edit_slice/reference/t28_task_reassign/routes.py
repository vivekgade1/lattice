# ruff: noqa
"""Authenticated task routes for the TaskFlow API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, SQLModel

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


class ReassignTask(SQLModel):
    """Payload accepted when reassigning a task to another user."""

    new_owner_email: str


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


@router.post("/{task_id}/reassign", response_model=TaskPublic)
def reassign_task(
    task_id: int,
    body: ReassignTask,
    session: SessionDependency,
    current_user: CurrentUser,
):
    """Reassign an owned task to another user, by email.

    The task lookup stays owner-scoped (require_task), so a stranger cannot reassign
    someone else's task -- they see the app's usual "Task not found" 404, exactly as
    for any other task id that is not theirs. The new owner is looked up only after
    that scoped lookup succeeds.
    """

    task = require_task(session, task_id, current_user.id)
    new_owner = crud.get_user_by_email(session, body.new_owner_email)
    if new_owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not new_owner.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reassign a task to an inactive user",
        )
    if task.owner_id == new_owner.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task already belongs to this user",
        )
    task.owner_id = new_owner.id
    task.updated_at = utc_now()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


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
