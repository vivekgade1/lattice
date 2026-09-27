# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db


@app.get("/products")
def browse(
    category: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT id, name, price FROM products WHERE active = 1 AND category = ? "
        "ORDER BY id LIMIT ? OFFSET ?",
        (category, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
