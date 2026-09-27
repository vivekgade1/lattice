# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path
from platform_kit import app, get_db, require_role


@app.get("/admin/users/{user_id}")
def get_user(user_id: int = Path(ge=1), user=Depends(require_role("Admin")), db=Depends(get_db)):
    row = db.execute("SELECT id, email, name, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "user not found")
    return dict(row)
