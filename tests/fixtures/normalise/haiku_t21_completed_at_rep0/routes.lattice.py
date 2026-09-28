# lattice: v1

@endpoint("PATCH", "/tasks/{task_id}", auth=Customer)
def update_task(
    task_id: Int(ge=1),
    user: CurrentUser,
    title: Str(min_len=1, max_len=200) | None = None,
    description: Str(max_len=1000) | None = None,
    status: OneOf("todo", "in_progress", "done") | None = None,
    priority: OneOf("low", "medium", "high") | None = None,
    due_date: DateTime | None = None,
) -> One | NotFound | Forbidden:
    tasks = fetch(Task)
    accessible_tasks = accessible(tasks, by=user)
    found = where(accessible_tasks, col("id") == task_id)
    task = one(found, else_=NotFound("task"))
    
    old_status = col("status")
    new_status = when(sent(status), then=status, otherwise=old_status)
    is_becoming_done = (old_status != "done") & (new_status == "done")
    is_leaving_done = (old_status == "done") & (new_status != "done")
    
    completed_at_value = when(
        is_becoming_done,
        then=fn.now(),
        otherwise=when(is_leaving_done, then=None, otherwise=col("completed_at"))
    )
    
    updated = update(
        task,
        title=when(sent(title), then=title, otherwise=col("title")),
        description=when(sent(description), then=description, otherwise=col("description")),
        status=new_status,
        priority=when(sent(priority), then=priority, otherwise=col("priority")),
        due_date=when(sent(due_date), then=due_date, otherwise=col("due_date")),
        completed_at=completed_at_value,
    )
    
    return updated


@endpoint("GET", "/tasks/{task_id}", auth=Customer)
def get_task(task_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Forbidden:
    tasks = fetch(Task)
    accessible_tasks = accessible(tasks, by=user)
    found = where(accessible_tasks, col("id") == task_id)
    task = one(found, else_=NotFound("task"))
    return task


@endpoint("GET", "/tasks", auth=Customer)
def list_tasks(
    user: CurrentUser,
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page:
    tasks = fetch(Task)
    accessible_tasks = accessible(tasks, by=user)
    ordered = sort(accessible_tasks, by=["created_at"], desc=[True])
    result = page(ordered, limit=limit, offset=offset)
    return result


@endpoint("POST", "/tasks", auth=Customer)
def create_task(
    user: CurrentUser,
    title: Str(min_len=1, max_len=200),
    description: Str(max_len=1000) | None = None,
    priority: OneOf("low", "medium", "high") = "medium",
    due_date: DateTime | None = None,
) -> Created:
    task = insert(
        Task,
        owner=user,
        title=title,
        description=description,
        status="todo",
        priority=priority,
        due_date=due_date,
        completed_at=None,
    )
    return task


@endpoint("DELETE", "/tasks/{task_id}", auth=Customer)
def delete_task(task_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound | Forbidden:
    tasks = fetch(Task)
    accessible_tasks = accessible(tasks, by=user)
    found = where(accessible_tasks, col("id") == task_id)
    task = one(found, else_=NotFound("task"))
    deleted = delete(task)
    return deleted
