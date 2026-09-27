# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db


@app.get("/catalog")
def catalog(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    rows = db.execute(
        "SELECT id, name, category, price, stock FROM products ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
