# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, current_user, get_db
from pydantic import BaseModel, Field


class ReviewIn(BaseModel):
    product_id: int = Field(ge=1)
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=1, max_length=500)


@app.post("/reviews", status_code=201)
def create_review(payload: ReviewIn, user=Depends(current_user), db=Depends(get_db)):
    if db.execute("SELECT id FROM products WHERE id = ?", (payload.product_id,)).fetchone() is None:
        raise HTTPException(404, "product not found")
    cur = db.execute(
        "INSERT INTO reviews (product_id, customer_id, rating, body) VALUES (?, ?, ?, ?)",
        (payload.product_id, user["id"], payload.rating, payload.body),
    )
    db.commit()
    return {
        "id": cur.lastrowid,
        "product_id": payload.product_id,
        "customer_id": user["id"],
        "rating": payload.rating,
        "body": payload.body,
    }
