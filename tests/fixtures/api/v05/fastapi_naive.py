# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, get_db, require_role
from pydantic import BaseModel


class StatusIn(BaseModel):
    status: str


@app.post("/orders/{order_id}/status")
def set_status(
    order_id: int, payload: StatusIn, user=Depends(require_role("Admin")), db=Depends(get_db)
):
    cur = db.execute("UPDATE orders SET status = ? WHERE id = ?", (payload.status, order_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "order not found")
    db.commit()
    return {"id": order_id, "status": payload.status}
