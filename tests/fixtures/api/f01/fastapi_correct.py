# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, current_user, get_db


@app.get("/orders")
def list_orders(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    where = "" if user["role"] == "Admin" else "WHERE o.customer_id = ?"
    args = [] if user["role"] == "Admin" else [user["id"]]
    rows = db.execute(
        "SELECT o.id, o.status, o.created_at, o.total, "
        "COALESCE((SELECT SUM(i.qty) FROM order_items i WHERE i.order_id = o.id), 0) AS units "
        f"FROM orders o {where} ORDER BY o.id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
