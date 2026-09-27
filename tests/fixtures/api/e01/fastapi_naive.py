# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends
from platform_kit import app, get_db


@app.get("/products/{product_id}")
def get_product(product_id: int, db=Depends(get_db)):
    row = db.execute(
        "SELECT id, name, category, price, stock FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    return dict(row)
