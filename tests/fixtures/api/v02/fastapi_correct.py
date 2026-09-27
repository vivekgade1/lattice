# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends
from platform_kit import app, current_user, get_db
from pydantic import BaseModel, Field


class AddressIn(BaseModel):
    line1: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=60)
    zip: str = Field(min_length=5, max_length=5)


@app.post("/addresses", status_code=201)
def create_address(payload: AddressIn, user=Depends(current_user), db=Depends(get_db)):
    cur = db.execute(
        "INSERT INTO addresses (customer_id, line1, city, zip) VALUES (?, ?, ?, ?)",
        (user["id"], payload.line1, payload.city, payload.zip),
    )
    db.commit()
    return {
        "id": cur.lastrowid,
        "customer_id": user["id"],
        "line1": payload.line1,
        "city": payload.city,
        "zip": payload.zip,
    }
