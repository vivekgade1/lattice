# ruff: noqa
from fastapi import APIRouter, Depends
from sqlmodel import Session
from ..database.connection import get_db_session
from ..database import UserCRUD
from ..models.user import UserResponse
from ..api.auth import get_current_user

router = APIRouter()


def get_user_crud(session: Session = Depends(get_db_session)):
    return UserCRUD(session)


@router.get("/user/list", tags=["User"], response_model=list[UserResponse])
def list_users(
    current_user=Depends(get_current_user), crud: UserCRUD = Depends(get_user_crud)
):
    """
    List all users (requires authentication).

    Args:
        current_user: Authenticated user from JWT token
        crud: UserCRUD instance for database operations

    Returns:
        list[UserResponse]: List of all users (without passwords)
    """
    return crud.get_all_users()
