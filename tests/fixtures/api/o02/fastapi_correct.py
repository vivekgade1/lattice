# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db


@app.get("/addresses/{address_id}")
def get_address(address_id: int = Path(ge=1), user=Depends(current_user), db=Depends(get_db)):
    sql = "SELECT id, line1, city, zip FROM addresses WHERE id = ?"
    args = [address_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    row = db.execute(sql, args).fetchone()
    if row is None:
        raise HTTPException(404, "address not found")
    return dict(row)
