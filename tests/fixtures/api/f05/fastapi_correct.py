# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db


@app.get("/products/ratings")
def product_ratings(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT p.id, p.name, COUNT(r.id) AS review_count, AVG(r.rating) AS avg_rating "
        "FROM products p LEFT JOIN reviews r ON r.product_id = p.id "
        "GROUP BY p.id, p.name ORDER BY p.id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
