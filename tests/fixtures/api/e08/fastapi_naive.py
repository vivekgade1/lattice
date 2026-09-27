# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, current_user, get_db


@app.get("/admin/customers/{user_id}/orders")
def customer_orders(
    user_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT id, status, created_at, total FROM orders WHERE customer_id = ? "
        "ORDER BY id LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
