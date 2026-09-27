# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends
from platform_kit import app, get_db, require_role
from pydantic import BaseModel


class ProductIn(BaseModel):
    name: str
    category: str
    price: float
    stock: int


@app.post("/products", status_code=201)
def create_product(payload: ProductIn, user=Depends(require_role("Admin")), db=Depends(get_db)):
    cur = db.execute(
        "INSERT INTO products (name, category, price, stock, active) VALUES (?, ?, ?, ?, 1)",
        (payload.name, payload.category, payload.price, payload.stock),
    )
    db.commit()
    return {
        "id": cur.lastrowid,
        "name": payload.name,
        "category": payload.category,
        "price": payload.price,
        "stock": payload.stock,
    }
