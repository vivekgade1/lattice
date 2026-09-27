# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends
from platform_kit import app, current_user, get_db
from pydantic import BaseModel


class NameIn(BaseModel):
    name: str


@app.patch("/me/name")
def rename_me(payload: NameIn, user=Depends(current_user), db=Depends(get_db)):
    db.execute("UPDATE users SET name = ? WHERE id = ?", (payload.name, user["id"]))
    db.commit()
    return {"id": user["id"], "email": user["email"], "name": payload.name}
