# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db, require_role


@app.get("/admin/customers")
def customer_stats(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT u.id, u.name, u.email, "
        "COALESCE(s.order_count, 0) AS order_count, "
        "COALESCE(s.total_spent, 0) AS total_spent "
        "FROM users u LEFT JOIN ("
        "  SELECT customer_id, COUNT(*) AS order_count, SUM(total) AS total_spent "
        "  FROM orders WHERE status != 'cancelled' GROUP BY customer_id"
        ") s ON s.customer_id = u.id "
        "WHERE u.role = 'Customer' ORDER BY u.id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
