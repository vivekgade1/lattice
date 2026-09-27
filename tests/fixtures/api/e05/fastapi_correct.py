# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, get_db, require_role


@app.post("/orders/{order_id}/ship")
def ship_order(order_id: int = Path(ge=1), user=Depends(require_role("Admin")), db=Depends(get_db)):
    order = db.execute("SELECT id, status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        raise HTTPException(404, "order not found")
    if order["status"] != "paid":
        raise HTTPException(409, "order is not paid")
    db.execute("UPDATE orders SET status = 'shipped' WHERE id = ?", (order_id,))
    db.commit()
    return {"id": order_id, "status": "shipped"}
