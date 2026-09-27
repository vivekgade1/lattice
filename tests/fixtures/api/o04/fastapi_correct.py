# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db
from pydantic import BaseModel, Field


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=1, max_length=500)


@app.put("/reviews/{review_id}")
def update_review(
    payload: ReviewIn,
    review_id: int = Path(ge=1),
    user=Depends(current_user),
    db=Depends(get_db),
):
    sql = "UPDATE reviews SET rating = ?, body = ? WHERE id = ?"
    args = [payload.rating, payload.body, review_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    cur = db.execute(sql, args)
    if cur.rowcount == 0:
        raise HTTPException(404, "review not found")
    db.commit()
    row = db.execute(
        "SELECT id, product_id, rating, body FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()
    return dict(row)
