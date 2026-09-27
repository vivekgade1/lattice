# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Path, Query
from platform_kit import app, get_db, require_role


@app.get("/admin/customers/{user_id}/orders")
def customer_orders(
    user_id: int = Path(ge=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT id, status, created_at, total FROM orders WHERE customer_id = ? "
        "ORDER BY id LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
