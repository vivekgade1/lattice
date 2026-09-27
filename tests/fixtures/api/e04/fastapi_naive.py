# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, get_db


@app.get("/products/{product_id}/stock")
def product_stock(product_id: int, db=Depends(get_db)):
    row = db.execute("SELECT id, name, stock FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "product not found")
    return dict(row)
