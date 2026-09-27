# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db, require_role


@app.get("/admin/orders")
def all_orders(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT o.id, o.customer_id, u.name AS customer_name, o.status, o.total "
        "FROM orders o LEFT JOIN users u ON u.id = o.customer_id "
        "ORDER BY o.id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
