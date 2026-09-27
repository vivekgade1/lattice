# ruff: noqa
"""Persistence operations for users and tasks."""

from sqlalchemy import func, or_
from sqlmodel import Session, select

from bench.import_repos.taskflow.app.auth import hash_password
from bench.import_repos.taskflow.app.models import (
    Task,
    TaskCreate,
    TaskPriority,
    TaskStatus,
    TaskUpdate,
    User,
    UserCreate,
    utc_now,
)


def get_user_by_email(session: Session, email: str) -> User | None:
    """Return a user by normalized email."""

    return session.exec(select(User).where(User.email == email.strip().lower())).first()


def create_user(session: Session, user_input: UserCreate) -> User:
    """Persist a user with a securely hashed password."""

    user = User(
        email=str(user_input.email).strip().lower(),
        full_name=user_input.full_name.strip(),
        password_hash=hash_password(user_input.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def create_task(session: Session, task_input: TaskCreate, owner_id: int) -> Task:
    """Persist and return a task owned by the authenticated user."""

    task = Task.model_validate(task_input, update={"owner_id": owner_id})
    if task.status == TaskStatus.DONE:
        task.completed_at = utc_now()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def list_tasks(
    session: Session,
    *,
    owner_id: int,
    status: TaskStatus | None,
    priority: TaskPriority | None,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Task], int]:
    """Return filtered, paginated tasks belonging to one user."""

    conditions = [Task.owner_id == owner_id]
    if status is not None:
        conditions.append(Task.status == status)
    if priority is not None:
        conditions.append(Task.priority == priority)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(or_(Task.title.ilike(pattern), Task.description.ilike(pattern)))

    task_statement = select(Task)
    count_statement = select(func.count()).select_from(Task)
    for condition in conditions:
        task_statement = task_statement.where(condition)
        count_statement = count_statement.where(condition)

    task_statement = task_statement.order_by(Task.created_at.desc()).offset(offset).limit(limit)
    tasks = list(session.exec(task_statement).all())
    total = session.exec(count_statement).one()
    return tasks, total


def get_task(session: Session, task_id: int, owner_id: int) -> Task | None:
    """Return an owned task by ID when it exists."""

    return session.exec(select(Task).where(Task.id == task_id, Task.owner_id == owner_id)).first()


def update_task(session: Session, task: Task, task_input: TaskUpdate) -> Task:
    """Apply a partial update and return the refreshed task."""

    update_data = task_input.model_dump(exclude_unset=True)
    was_done = task.status == TaskStatus.DONE
    task.sqlmodel_update(update_data)
    now = utc_now()
    if task.status != TaskStatus.DONE:
        task.completed_at = None
    elif not was_done:
        task.completed_at = now
    task.updated_at = now
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def delete_task(session: Session, task: Task) -> None:
    """Delete an existing task."""

    session.delete(task)
    session.commit()
