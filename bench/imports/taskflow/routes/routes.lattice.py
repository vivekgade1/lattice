# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.taskflow.app.routes by lattice.importer (deterministic lifters and the LLM loop: create_task, read_tasks, read_task, delete_task; every route verified).
@endpoint("POST", "/api/v1/tasks", auth=Customer, response="TaskPublic")
def create_task(
    title: Str(min_len=3, max_len=120),
    user: CurrentUser,
    description: Str(min_len=0, max_len=1000) | None = None,
    status: OneOf("todo", "in_progress", "done") = "todo",
    priority: OneOf("low", "medium", "high") = "medium",
    due_date: DateTime | None = None,
) -> Created:
    task = insert(
        Task,
        owner=user,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
        created_at=native.utc_now(),
        updated_at=native.utc_now(),
    )
    return task


@step
def search_tasks(t: Table[Task], q: Str) -> Table[Task]:
    matched = where(
        t,
        is_null(q)
        | fn.contains(col("title"), sub=q, ignore_case=True)
        | fn.contains(col("description"), sub=q, ignore_case=True),
    )
    return matched


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
    own = mine(tasks, by=user)
    by_status = where(own, is_null(status) | (col("status") == status))
    by_priority = where(by_status, is_null(priority) | (col("priority") == priority))
    found = search_tasks(by_priority, q=search)
    ordered = sort(found, by=["created_at", "id"], desc=[True, True])
    result = page(ordered, limit=limit, offset=offset, total=True)
    return result


@endpoint("GET", "/api/v1/tasks/{task_id}", auth=Customer, response="TaskPublic")
def read_task(task_id: Int, user: CurrentUser) -> One | NotFound:
    tasks = fetch(Task)
    visible = mine(tasks, by=user)
    found = where(visible, col("id") == task_id)
    task = one(found, else_=NotFound("Task not found"))
    return task


@endpoint("DELETE", "/api/v1/tasks/{task_id}", auth=Customer)
def delete_task(task_id: Int, user: CurrentUser) -> NoContent | NotFound:
    tasks = fetch(Task)
    visible = mine(tasks, by=user)
    found = where(visible, col("id") == task_id)
    task = one(found, else_=NotFound("Task not found"))
    removed = delete(task)
    return removed
