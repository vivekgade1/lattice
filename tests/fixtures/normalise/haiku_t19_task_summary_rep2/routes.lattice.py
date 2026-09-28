# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.taskflow.app.routes by lattice.importer (deterministic lifters and the LLM loop: create_task, read_tasks, read_task; every route verified).
@endpoint("POST", "/api/v1/tasks", auth=Customer, response="TaskPublic")
def create_task(
    title: Str(min_len=3, max_len=120),
    user: CurrentUser,
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
        created_at=native.utc_now(),
        updated_at=native.utc_now(),
    )
    return created


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
    matched = where(
        by_priority,
        is_null(search)
        | fn.contains(col("title"), sub=search, ignore_case=True)
        | fn.contains(col("description"), sub=search, ignore_case=True),
    )
    slim = select(
        matched,
        cols=["title", "description", "status", "priority", "due_date", "id", "owner_id", "created_at", "updated_at"],
    )
    ordered = sort(slim, by=["created_at", "id"], desc=[True, True])
    result = page(ordered, limit=limit, offset=offset, total=True)
    return result


@endpoint("GET", "/api/v1/tasks/{task_id}", auth=Customer, response="TaskPublic")
def read_task(task_id: Int, user: CurrentUser) -> One | NotFound:
    tasks = fetch(Task)
    mine_only = where(tasks, col("owner_id") == user.id)
    found = where(mine_only, col("id") == task_id)
    task = one(found, else_=NotFound("Task not found"))
    result = select(task, cols=["title", "description", "status", "priority", "due_date", "id", "owner_id", "created_at", "updated_at"])
    return result


@endpoint("GET", "/api/v1/tasks/summary", auth=Customer, response="TaskSummary")
def get_tasks_summary(user: CurrentUser) -> One:
    tasks = fetch(Task)
    own = where(tasks, col("owner_id") == user.id)
    
    summary_stats = group(
        own,
        by=[],
        total=agg.count(),
        todo=agg.count(when(col("status") == "todo", then=col("id"), otherwise=None)),
        in_progress=agg.count(when(col("status") == "in_progress", then=col("id"), otherwise=None)),
        done=agg.count(when(col("status") == "done", then=col("id"), otherwise=None)),
    )
    
    overdue_tasks = where(
        own,
        (col("due_date") < fn.now()) & (col("status") != "done"),
    )
    overdue_stats = group(overdue_tasks, by=[], overdue=agg.count())
    
    summary = join(summary_stats, overdue_stats, on=[], how="inner")
    result = select(summary, cols=["total", "todo", "in_progress", "done", "overdue"])
    
    return result
