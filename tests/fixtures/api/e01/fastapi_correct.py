# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, get_db


@app.get("/products/{product_id}")
def get_product(product_id: int = Path(ge=1), db=Depends(get_db)):
    row = db.execute(
        "SELECT id, name, category, price, stock FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "product not found")
    return dict(row)
