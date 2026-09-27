# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from typing import Literal

from fastapi import Depends, HTTPException, Path
from platform_kit import app, get_db, require_role
from pydantic import BaseModel


class StatusIn(BaseModel):
    status: Literal["pending", "paid", "shipped", "cancelled"]


@app.post("/orders/{order_id}/status")
def set_status(
    payload: StatusIn,
    order_id: int = Path(ge=1),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    cur = db.execute("UPDATE orders SET status = ? WHERE id = ?", (payload.status, order_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "order not found")
    db.commit()
    return {"id": order_id, "status": payload.status}
