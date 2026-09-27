# ruff: noqa: B008 -- idiomatic FastAPI dependency injection
from fastapi import Depends, HTTPException, Path, Response
from platform_kit import app, get_db, require_role


@app.delete("/products/{product_id}", status_code=204)
def delete_product(
    product_id: int = Path(ge=1), user=Depends(require_role("Admin")), db=Depends(get_db)
):
    cur = db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "product not found")
    db.commit()
    return Response(status_code=204)
