# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, get_db, require_role
from pydantic import BaseModel, Field


class PriceIn(BaseModel):
    price: float = Field(ge=0.01, le=10000)


@app.patch("/products/{product_id}/price")
def set_price(
    payload: PriceIn,
    product_id: int = Path(ge=1),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    cur = db.execute("UPDATE products SET price = ? WHERE id = ?", (payload.price, product_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "product not found")
    db.commit()
    return {"id": product_id, "price": payload.price}
