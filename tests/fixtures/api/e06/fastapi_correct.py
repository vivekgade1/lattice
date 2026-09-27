# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db
from pydantic import BaseModel, Field


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=1, max_length=500)


@app.post("/products/{product_id}/my-review", status_code=201)
def review_product(
    payload: ReviewIn,
    product_id: int = Path(ge=1),
    user=Depends(current_user),
    db=Depends(get_db),
):
    if db.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone() is None:
        raise HTTPException(404, "product not found")
    existing = db.execute(
        "SELECT id FROM reviews WHERE product_id = ? AND customer_id = ?",
        (product_id, user["id"]),
    ).fetchone()
    if existing is not None:
        raise HTTPException(409, "already reviewed")
    cur = db.execute(
        "INSERT INTO reviews (product_id, customer_id, rating, body) VALUES (?, ?, ?, ?)",
        (product_id, user["id"], payload.rating, payload.body),
    )
    db.commit()
    return {
        "id": cur.lastrowid,
        "product_id": product_id,
        "customer_id": user["id"],
        "rating": payload.rating,
        "body": payload.body,
    }
