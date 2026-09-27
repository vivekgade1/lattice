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
    if user["role"] == "Admin":
        orders = db.execute(
            "SELECT id, status, created_at, total FROM orders ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    else:
        orders = db.execute(
            "SELECT id, status, created_at, total FROM orders WHERE customer_id = ? "
            "ORDER BY id LIMIT ? OFFSET ?",
            (user["id"], limit, offset),
        ).fetchall()
    items = []
    for order in orders:
        units = db.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = ?", (order["id"],)
        ).fetchone()[0]
        items.append({**dict(order), "units": units})
    return {"items": items, "limit": limit, "offset": offset}
