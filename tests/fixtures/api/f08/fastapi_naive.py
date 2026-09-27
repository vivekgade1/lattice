# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from typing import Literal

from fastapi import Depends
from platform_kit import app, get_db, require_role


@app.get("/admin/users")
def list_users(
    role: Literal["Customer", "Admin"] = "Customer",
    limit: int | None = None,
    offset: int = 0,
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    sql = "SELECT id, email, name, role FROM users WHERE role = ? ORDER BY id"
    args: list[object] = [role]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args += [limit, offset]
    rows = db.execute(sql, args).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit or 20, "offset": offset}
