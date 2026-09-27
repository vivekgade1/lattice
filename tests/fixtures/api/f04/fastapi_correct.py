# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db, require_role


@app.get("/admin/reviews")
def moderation_queue(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT r.id, r.rating, r.body, u.name AS reviewer_name, p.name AS product_name "
        "FROM reviews r LEFT JOIN users u ON u.id = r.customer_id "
        "LEFT JOIN products p ON p.id = r.product_id "
        "ORDER BY r.id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
