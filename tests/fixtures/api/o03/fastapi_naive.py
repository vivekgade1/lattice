# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Response
from platform_kit import app, current_user, get_db


@app.delete("/addresses/{address_id}", status_code=204)
def delete_address(address_id: int, user=Depends(current_user), db=Depends(get_db)):
    cur = db.execute("DELETE FROM addresses WHERE id = ?", (address_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "address not found")
    db.commit()
    return Response(status_code=204)
