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
    customers = db.execute(
        "SELECT id, name, email FROM users WHERE role = 'Customer' ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    items = []
    for customer in customers:
        count, spent = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(total), 0) FROM orders "
            "WHERE customer_id = ? AND status != 'cancelled'",
            (customer["id"],),
        ).fetchone()
        items.append({**dict(customer), "order_count": count, "total_spent": spent})
    return {"items": items, "limit": limit, "offset": offset}
