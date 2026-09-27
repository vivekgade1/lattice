# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException
from platform_kit import app, current_user, get_db


@app.get("/addresses/{address_id}")
def get_address(address_id: int, user=Depends(current_user), db=Depends(get_db)):
    row = db.execute(
        "SELECT id, line1, city, zip FROM addresses WHERE id = ?", (address_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "address not found")
    return dict(row)
