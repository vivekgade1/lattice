# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends
from platform_kit import app, current_user, get_db


@app.get("/me/stats")
def my_stats(user=Depends(current_user), db=Depends(get_db)):
    where = "" if user["role"] == "Admin" else "WHERE customer_id = ?"
    args = [] if user["role"] == "Admin" else [user["id"]]
    count, spent = db.execute(
        "SELECT COUNT(*), "
        "COALESCE(SUM(CASE WHEN status IN ('paid', 'shipped') THEN total ELSE 0 END), 0) "
        f"FROM orders {where}",
        args,
    ).fetchone()
    return {"order_count": count, "total_spent": spent}
