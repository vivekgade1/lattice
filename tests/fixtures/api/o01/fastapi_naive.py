# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, current_user, get_db


@app.get("/orders/{order_id}")
def get_order(order_id: int, user=Depends(current_user), db=Depends(get_db)):
    row = db.execute(
        "SELECT id, status, created_at, total FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "order not found")
    return dict(row)
