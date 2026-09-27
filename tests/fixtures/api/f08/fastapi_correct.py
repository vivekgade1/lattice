# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from typing import Literal

from fastapi import Depends, Query
from platform_kit import app, get_db, require_role


@app.get("/admin/users")
def list_users(
    role: Literal["Customer", "Admin"] = "Customer",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT id, email, name, role FROM users WHERE role = ? ORDER BY id LIMIT ? OFFSET ?",
        (role, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
