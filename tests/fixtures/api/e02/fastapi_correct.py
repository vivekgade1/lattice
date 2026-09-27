# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db


@app.post("/orders/{order_id}/pay")
def pay_order(order_id: int = Path(ge=1), user=Depends(current_user), db=Depends(get_db)):
    sql = "SELECT id, status FROM orders WHERE id = ?"
    args = [order_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    order = db.execute(sql, args).fetchone()
    if order is None:
        raise HTTPException(404, "order not found")
    if order["status"] != "pending":
        raise HTTPException(409, "order is not pending")
    db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
    db.commit()
    return {"id": order_id, "status": "paid"}
