# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, current_user, get_db
from pydantic import BaseModel, Field


class AddressIn(BaseModel):
    line1: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=60)
    zip: str = Field(min_length=5, max_length=5)


@app.put("/addresses/{address_id}")
def replace_address(
    payload: AddressIn,
    address_id: int = Path(ge=1),
    user=Depends(current_user),
    db=Depends(get_db),
):
    sql = "UPDATE addresses SET line1 = ?, city = ?, zip = ? WHERE id = ?"
    args = [payload.line1, payload.city, payload.zip, address_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    if db.execute(sql, args).rowcount == 0:
        raise HTTPException(404, "address not found")
    db.commit()
    return {"id": address_id, "line1": payload.line1, "city": payload.city, "zip": payload.zip}
