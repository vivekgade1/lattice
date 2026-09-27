# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db, require_role


@app.get("/admin/reviews")
def moderation_queue(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_role("Admin")),
    db=Depends(get_db),
):
    reviews = db.execute(
        "SELECT id, product_id, customer_id, rating, body FROM reviews "
        "ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    items = []
    for review in reviews:
        reviewer = db.execute(
            "SELECT name FROM users WHERE id = ?", (review["customer_id"],)
        ).fetchone()
        product = db.execute(
            "SELECT name FROM products WHERE id = ?", (review["product_id"],)
        ).fetchone()
        items.append(
            {
                "id": review["id"],
                "rating": review["rating"],
                "body": review["body"],
                "reviewer_name": reviewer["name"],
                "product_name": product["name"],
            }
        )
    return {"items": items, "limit": limit, "offset": offset}
