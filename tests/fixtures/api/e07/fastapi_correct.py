# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db
from pydantic import BaseModel, Field


class ReserveIn(BaseModel):
    qty: int = Field(ge=1, le=100)


@app.post("/products/{product_id}/reserve")
def reserve(
    payload: ReserveIn,
    product_id: int = Path(ge=1),
    user=Depends(current_user),
    db=Depends(get_db),
):
    row = db.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "product not found")
    if row["stock"] < payload.qty:
        raise HTTPException(409, "not enough stock")
    new_stock = row["stock"] - payload.qty
    db.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))
    db.commit()
    return {"id": product_id, "stock": new_stock}
