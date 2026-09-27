# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, Query
from platform_kit import app, get_db


@app.get("/products/ratings")
def product_ratings(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    products = db.execute(
        "SELECT id, name FROM products ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    items = []
    for product in products:
        stats = db.execute(
            "SELECT COUNT(*), AVG(rating) FROM reviews WHERE product_id = ?", (product["id"],)
        ).fetchone()
        items.append(
            {
                "id": product["id"],
                "name": product["name"],
                "review_count": stats[0],
                "avg_rating": stats[1],
            }
        )
    return {"items": items, "limit": limit, "offset": offset}
