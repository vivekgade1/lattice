# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, current_user, get_db


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, user=Depends(current_user), db=Depends(get_db)):
    order = db.execute("SELECT id, status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        raise HTTPException(404, "order not found")
    if order["status"] != "pending":
        raise HTTPException(409, "order is not pending")
    db.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
    db.commit()
    return {"id": order_id, "status": "cancelled"}
