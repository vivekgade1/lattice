# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path, Query
from platform_kit import app, current_user, get_db


@app.get("/orders/{order_id}/lines")
def order_lines(
    order_id: int = Path(ge=1),
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
    rows = db.execute(
        "SELECT i.id, i.product_id, p.name AS product_name, i.qty, i.unit_price "
        "FROM order_items i LEFT JOIN products p ON p.id = i.product_id "
        "WHERE i.order_id = ? ORDER BY i.id LIMIT ? OFFSET ?",
        (order_id, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
