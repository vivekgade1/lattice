# ruff: noqa
# app/routers/order_router.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from bench.import_repos.multivendor.app.core.database import get_session
from bench.import_repos.multivendor.app.core.dependencies import get_current_user, require_permission
from bench.import_repos.multivendor.app.core.permissions import Permission
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.repositories.product_repository import InsufficientStockError, ConcurrentModificationError
from bench.import_repos.multivendor.app.schemas.order import OrderCreate, OrderRead, OrderStatusUpdate
from bench.import_repos.multivendor.app.services import order_service
from bench.import_repos.multivendor.app.services.order_service import OrderError, OrderNotFoundError, OrderPermissionError

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def place_order(
    data: OrderCreate,
    current_user: User = Depends(require_permission(Permission.PLACE_ORDER)),
    session: Session = Depends(get_session),
):
    try:
        return order_service.place_order(session, current_user, data)
    except InsufficientStockError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ConcurrentModificationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{str(e)} — please retry your order",
        )


@router.get("/me", response_model=list[OrderRead])
def list_own_orders(
    current_user: User = Depends(require_permission(Permission.VIEW_OWN_ORDERS)),
    session: Session = Depends(get_session),
):
    return order_service.list_own_orders(session, current_user)


@router.get("/report")
def read_monthly_report(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    current_user: User = Depends(require_permission(Permission.VIEW_ALL_ORDERS)),
    session: Session = Depends(get_session),
):
    return order_service.monthly_report(session, month)


@router.get("/{order_id}", response_model=OrderRead)
def get_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from bench.import_repos.multivendor.app.core.permissions import ROLE_PERMISSIONS

    try:
        if Permission.VIEW_ALL_ORDERS in ROLE_PERMISSIONS.get(current_user.role, set()):
            return order_service.get_order(session, order_id)
        return order_service.get_own_order(session, current_user, order_id)
    except OrderNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except OrderPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.patch("/{order_id}/status", response_model=OrderRead)
def update_order_status(
    order_id: int,
    data: OrderStatusUpdate,
    current_user: User = Depends(require_permission(Permission.UPDATE_ORDER_STATUS)),
    session: Session = Depends(get_session),
):
    try:
        return order_service.transition_order_status(session, order_id, data.status)
    except OrderNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except OrderError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    

from bench.import_repos.multivendor.app.schemas.order import OrderItemCreate

@router.post("/flash-sale-purchase", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def flash_sale_purchase(
    data: OrderItemCreate,
    current_user: User = Depends(require_permission(Permission.PLACE_ORDER)),
    session: Session = Depends(get_session),
):
    try:
        return order_service.purchase_single_item_pessimistic(
            session, current_user, data.product_id, data.quantity
        )
    except InsufficientStockError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))