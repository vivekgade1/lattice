# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, current_user, get_db


@app.get("/me/addresses")
def my_addresses(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    where = "" if user["role"] == "Admin" else "WHERE customer_id = ?"
    args = [] if user["role"] == "Admin" else [user["id"]]
    rows = db.execute(
        f"SELECT id, line1, city, zip FROM addresses {where} ORDER BY id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
