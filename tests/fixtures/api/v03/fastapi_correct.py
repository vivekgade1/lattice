# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from typing import Literal

from fastapi import Depends
from platform_kit import app, get_db, require_role
from pydantic import BaseModel, Field


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    category: Literal["electronics", "home", "outdoor", "office", "kitchen", "sports"]
    price: float = Field(ge=0.01, le=10000)
    stock: int = Field(ge=0, le=100000)


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
