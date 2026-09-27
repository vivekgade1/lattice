# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, get_db, require_role
from pydantic import BaseModel, Field


class RestockIn(BaseModel):
    qty: int = Field(ge=1, le=1000)


@app.post("/products/{product_id}/restock")
def restock(
    payload: RestockIn,
    product_id: int = Path(ge=1),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    cur = db.execute(
        "UPDATE products SET stock = stock + ? WHERE id = ?", (payload.qty, product_id)
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "product not found")
    db.commit()
    stock = db.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()[0]
    return {"id": product_id, "stock": stock}
