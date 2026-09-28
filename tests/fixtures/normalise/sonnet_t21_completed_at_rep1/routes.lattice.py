# lattice: v1

TASK_PUBLIC = [
    "title",
    "description",
    "status",
    "priority",
    "due_date",
    "id",
    "owner_id",
    "created_at",
    "updated_at",
    "completed_at",
]


@endpoint("GET", "/tasks", auth=Customer)
def list_tasks(
    user: CurrentUser,
    status: OneOf("todo", "in_progress", "done") | None = None,
    priority: OneOf("low", "medium", "high") | None = None,
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page:
    tasks = fetch(Task)
    mine_tasks = mine(tasks, by=user)
    status_filtered = where(mine_tasks, is_null(status) | (col("status") == status))
    filtered = where(status_filtered, is_null(priority) | (col("priority") == priority))
    ordered = sort(filtered, by=["created_at"], desc=True)
    slim = select(ordered, cols=TASK_PUBLIC)
    result = page(slim, limit=limit, offset=offset)
    return result


@endpoint("GET", "/tasks/{task_id}", auth=Customer)
def get_task(task_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    tasks = fetch(Task)
    mine_tasks = mine(tasks, by=user)
    wanted = where(mine_tasks, col("id") == task_id)
    slim = select(wanted, cols=TASK_PUBLIC)
    task = one(slim, else_=NotFound("task"))
    return task


@endpoint("POST", "/tasks", auth=Customer)
def create_task(
    title: Str(min_len=1, max_len=200),
    user: CurrentUser,
    description: Str(max_len=2000) | None = None,
    status: OneOf("todo", "in_progress", "done") = "todo",
    priority: OneOf("low", "medium", "high") = "medium",
    due_date: DateTime | None = None,
) -> Created:
    created = insert(
        Task,
        owner=user,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
        completed_at=when(status == "done", fn.now(), otherwise=None),
    )
    slim = select(created, cols=TASK_PUBLIC)
    return slim


@endpoint("PATCH", "/tasks/{task_id}", auth=Customer)
def update_task(
    task_id: Int(ge=1),
    user: CurrentUser,
    title: Str(min_len=1, max_len=200) = UNSET,
    description: Str(max_len=2000) = UNSET,
    status: OneOf("todo", "in_progress", "done") = UNSET,
    priority: OneOf("low", "medium", "high") = UNSET,
    due_date: DateTime = UNSET,
) -> One | NotFound:
    tasks = fetch(Task)
    mine_tasks = mine(tasks, by=user)
    wanted = where(mine_tasks, col("id") == task_id)
    task = one(wanted, else_=NotFound("task"))
    updated = update(
        task,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
        completed_at=when(
            sent(status) & (status == "done"),
            fn.now(),
            otherwise=when(
                sent(status) & (col("status") == "done") & (status != "done"),
                None,
                otherwise=col("completed_at"),
            ),
        ),
    )
    slim = select(updated, cols=TASK_PUBLIC)
    return slim


@endpoint("DELETE", "/tasks/{task_id}", auth=Customer)
def delete_task(task_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    tasks = fetch(Task)
    mine_tasks = mine(tasks, by=user)
    wanted = where(mine_tasks, col("id") == task_id)
    task = one(wanted, else_=NotFound("task"))
    deleted = delete(task)
    return deleted
