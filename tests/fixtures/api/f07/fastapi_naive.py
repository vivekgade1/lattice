# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Query
from platform_kit import app, current_user, get_db


@app.get("/orders/{order_id}/lines")
def order_lines(
    order_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    sql = "SELECT id FROM orders WHERE id = ?"
    args = [order_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    if db.execute(sql, args).fetchone() is None:
        raise HTTPException(404, "order not found")
    lines = db.execute(
        "SELECT id, product_id, qty, unit_price FROM order_items WHERE order_id = ? "
        "ORDER BY id LIMIT ? OFFSET ?",
        (order_id, limit, offset),
    ).fetchall()
    items = []
    for line in lines:
        product = db.execute(
            "SELECT name FROM products WHERE id = ?", (line["product_id"],)
        ).fetchone()
        items.append({**dict(line), "product_name": product["name"]})
    return {"items": items, "limit": limit, "offset": offset}
