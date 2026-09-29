# ruff: noqa
# app/routers/user_router.py
from fastapi import APIRouter, Depends
from sqlmodel import Session

from bench.import_repos.multivendor.app.core.database import get_session
from bench.import_repos.multivendor.app.core.dependencies import get_current_user
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.schemas.user import UserRead, UserUpdate
from bench.import_repos.multivendor.app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_own_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserRead)
def update_own_profile(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return user_service.update_own_profile(session, current_user, data)