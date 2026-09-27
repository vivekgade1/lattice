# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends
from platform_kit import app, get_db


@app.get("/catalog")
def catalog(limit: int | None = None, offset: int = 0, db=Depends(get_db)):
    sql = "SELECT id, name, category, price, stock FROM products ORDER BY id"
    args: list[int] = []
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args = [limit, offset]
    rows = db.execute(sql, args).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit or 20, "offset": offset}
