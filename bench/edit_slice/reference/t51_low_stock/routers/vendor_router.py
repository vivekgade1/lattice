# ruff: noqa
# app/routers/vendor_router.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from bench.import_repos.multivendor.app.core.database import get_session
from bench.import_repos.multivendor.app.core.dependencies import get_current_user, require_permission
from bench.import_repos.multivendor.app.core.permissions import Permission
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.schemas.vendor import VendorCreate, VendorUpdate, VendorRead, VendorVerify
from bench.import_repos.multivendor.app.services import vendor_service
from bench.import_repos.multivendor.app.services.vendor_service import VendorError

router = APIRouter(prefix="/vendors", tags=["vendors"])


@router.post("", response_model=VendorRead, status_code=status.HTTP_201_CREATED)
def register_as_vendor(
    data: VendorCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        return vendor_service.register_vendor(session, current_user, data)
    except VendorError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/me", response_model=VendorRead)
def read_own_vendor_profile(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        return vendor_service.get_own_vendor_profile(session, current_user)
    except VendorError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch("/me", response_model=VendorRead)
def update_own_vendor_profile(
    data: VendorUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        return vendor_service.update_own_vendor_profile(session, current_user, data)
    except VendorError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch("/{vendor_id}/verify", response_model=VendorRead)
def verify_vendor(
    vendor_id: int,
    data: VendorVerify,
    current_user: User = Depends(require_permission(Permission.VERIFY_VENDOR)),
    session: Session = Depends(get_session),
):
    try:
        return vendor_service.verify_vendor(session, vendor_id, data.is_verified)
    except VendorError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))



from fastapi import Query


@router.get("/me/low-stock")
def read_own_low_stock(
    threshold: int = Query(default=10, gt=0, le=1000),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        return vendor_service.low_stock(session, current_user, threshold)
    except VendorError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
