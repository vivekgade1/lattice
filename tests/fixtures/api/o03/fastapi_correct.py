# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path, Response
from platform_kit import app, current_user, get_db


@app.delete("/addresses/{address_id}", status_code=204)
def delete_address(address_id: int = Path(ge=1), user=Depends(current_user), db=Depends(get_db)):
    sql = "DELETE FROM addresses WHERE id = ?"
    args = [address_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    cur = db.execute(sql, args)
    if cur.rowcount == 0:
        raise HTTPException(404, "address not found")
    db.commit()
    return Response(status_code=204)
