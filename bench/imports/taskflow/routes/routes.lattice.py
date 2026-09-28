# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.taskflow.app.routes by lattice.importer (deterministic lifters and the LLM loop: create_task, read_tasks, read_task, delete_task; every route verified).
@endpoint("POST", "/api/v1/tasks", auth=Customer)
def create_task(
    user: CurrentUser,
    title: Str(min_len=3, max_len=120),
    description: Str(min_len=0, max_len=1000) | None = None,
    status: OneOf("todo", "in_progress", "done") = "todo",
    priority: OneOf("low", "medium", "high") = "medium",
    due_date: DateTime | None = None,
) -> Created:
    created = insert(
        Task,
        owner_id=user.id,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
        created_at=fn.now(),
        updated_at=fn.datetime_add(fn.now(), seconds=1),
    )
    result = select(
        created,
        cols=["title", "description", "status", "priority", "due_date", "id", "owner_id", "created_at", "updated_at"],
    )
    return result


@endpoint("GET", "/api/v1/tasks", auth=Customer)
def read_tasks(
    user: CurrentUser,
    status: OneOf("todo", "in_progress", "done") | None = None,
    priority: OneOf("low", "medium", "high") | None = None,
    search: Str(min_len=1, max_len=100) | None = None,
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page:
    tasks = fetch(Task)
    own = where(tasks, col("owner_id") == user.id)
    by_status = where(own, is_null(status) | (col("status") == status))
    by_priority = where(by_status, is_null(priority) | (col("priority") == priority))
    searched = where(
        by_priority,
        is_null(search)
        | fn.contains(col("title"), sub=search, ignore_case=True)
        | fn.contains(col("description"), sub=search, ignore_case=True),
    )
    ordered = sort(searched, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=offset, total=True)
    return result


@endpoint("GET", "/api/v1/tasks/{task_id}", auth=Customer)
def read_task(task_id: Int, user: CurrentUser) -> One | NotFound:
    tasks = fetch(Task)
    mine = where(tasks, col("owner_id") == user.id)
    found = where(mine, col("id") == task_id)
    slim = select(found, cols=["title", "description", "status", "priority", "due_date", "id", "owner_id", "created_at", "updated_at"])
    task = one(slim, else_=NotFound("Task not found"))
    return task


@endpoint("DELETE", "/api/v1/tasks/{task_id}", auth=Customer)
def delete_task(task_id: Int, user: CurrentUser) -> NoContent | NotFound:
    tasks = fetch(Task)
    mine_tasks = where(tasks, col("owner_id") == user.id)
    found = where(mine_tasks, col("id") == task_id)
    task = one(found, else_=NotFound("Task not found"))
    deleted = delete(task)
    return deleted
