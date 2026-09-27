# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, current_user, get_db
from pydantic import BaseModel, Field


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=1, max_length=500)


@app.put("/reviews/{review_id}")
def update_review(
    review_id: int, payload: ReviewIn, user=Depends(current_user), db=Depends(get_db)
):
    review = db.execute("SELECT id FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if review is None:
        raise HTTPException(404, "review not found")
    db.execute(
        "UPDATE reviews SET rating = ?, body = ? WHERE id = ?",
        (payload.rating, payload.body, review_id),
    )
    db.commit()
    row = db.execute(
        "SELECT id, product_id, rating, body FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()
    return dict(row)
