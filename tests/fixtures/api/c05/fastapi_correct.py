# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path, Response
from platform_kit import app, current_user, get_db


@app.delete("/reviews/{review_id}", status_code=204)
def delete_review(review_id: int = Path(ge=1), user=Depends(current_user), db=Depends(get_db)):
    sql = "DELETE FROM reviews WHERE id = ?"
    args = [review_id]
    if user["role"] != "Admin":
        sql += " AND customer_id = ?"
        args.append(user["id"])
    if db.execute(sql, args).rowcount == 0:
        raise HTTPException(404, "review not found")
    db.commit()
    return Response(status_code=204)
