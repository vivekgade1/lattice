# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, current_user, get_db


@app.get("/orders/{order_id}/units")
def order_units(order_id: int, user=Depends(current_user), db=Depends(get_db)):
    if db.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone() is None:
        raise HTTPException(404, "order not found")
    units = db.execute(
        "SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchone()[0]
    return {"order_id": order_id, "units": units}
