# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db


@app.get("/orders/{order_id}")
def get_order(order_id: int = Path(ge=1), user=Depends(current_user), db=Depends(get_db)):
    sql = "SELECT id, status, created_at, total FROM orders WHERE id = ?"
    args = [order_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    row = db.execute(sql, args).fetchone()
    if row is None:
        raise HTTPException(404, "order not found")
    return dict(row)
