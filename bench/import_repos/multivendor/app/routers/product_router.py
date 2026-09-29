# ruff: noqa
# app/routers/product_router.py
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel import Session

from bench.import_repos.multivendor.app.core.database import get_session
from bench.import_repos.multivendor.app.core.dependencies import get_current_user, require_permission
from bench.import_repos.multivendor.app.core.permissions import Permission
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.schemas.product import ProductCreate, ProductUpdate, ProductRead
from bench.import_repos.multivendor.app.services import product_service
from bench.import_repos.multivendor.app.services.product_service import ProductNotFoundError, ProductPermissionError

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    data: ProductCreate,
    current_user: User = Depends(require_permission(Permission.MANAGE_OWN_PRODUCTS)),
    session: Session = Depends(get_session),
):
    try:
        return product_service.create_product(session, current_user, data)
    except ProductPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("", response_model=list[ProductRead])
def list_products(
    category: str | None = Query(default=None),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, le=100),
    session: Session = Depends(get_session),
):
    return product_service.list_products(session, category, min_price, max_price, skip, limit)


@router.get("/{product_id}", response_model=ProductRead)
def get_product(product_id: int, session: Session = Depends(get_session)):
    try:
        return product_service.get_product(session, product_id)
    except ProductNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: int,
    data: ProductUpdate,
    current_user: User = Depends(require_permission(Permission.MANAGE_OWN_PRODUCTS)),
    session: Session = Depends(get_session),
):
    try:
        return product_service.update_own_product(session, current_user, product_id, data)
    except ProductNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ProductPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))