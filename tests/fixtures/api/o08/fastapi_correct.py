# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db


@app.get("/orders/{order_id}/units")
def order_units(order_id: int = Path(ge=1), user=Depends(current_user), db=Depends(get_db)):
    sql = "SELECT id FROM orders WHERE id = ?"
    args = [order_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    if db.execute(sql, args).fetchone() is None:
        raise HTTPException(404, "order not found")
    units = db.execute(
        "SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchone()[0]
    return {"order_id": order_id, "units": units}
