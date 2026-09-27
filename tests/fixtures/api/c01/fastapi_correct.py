# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends
from platform_kit import app, current_user


@app.get("/me")
def me(user=Depends(current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}
