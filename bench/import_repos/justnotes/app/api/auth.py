# ruff: noqa
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session
from pydantic import BaseModel
from ..database.connection import get_db_session
from ..database import UserCRUD
from ..utils.auth import verify_password, hash_password
from ..utils.jwt import create_access_token
from ..config import settings
from ..models.user import UserResponse, UserBase

# Router for authentication-related endpoints
router = APIRouter()
# OAuth2 scheme for token-based authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


class Token(BaseModel):
    """
    Token response model for authentication.

    Attributes:
        access_token: JWT access token for authenticated requests
        token_type: Type of token (always "bearer" for JWT)
    """

    access_token: str
    token_type: str


class UserLogin(BaseModel):
    """
    User login request model.

    Attributes:
        username: User's username for authentication
        password: User's plain text password
    """

    username: str
    password: str


def get_user_crud(session: Session = Depends(get_db_session)):
    """
    Dependency function to get UserCRUD instance with database session.

    Args:
        session: Database session from dependency injection

    Returns:
        UserCRUD: Instance with database operations for users
    """
    return UserCRUD(session)


def get_current_user(
    token: str = Depends(oauth2_scheme), crud: UserCRUD = Depends(get_user_crud)
):
    """
    Dependency function to get current authenticated user from JWT token.

    Args:
        token: JWT token from Authorization header
        crud: UserCRUD instance for database operations

    Returns:
        User: Authenticated user object

    Raises:
        HTTPException: 401 if token is invalid or user not found
    """
    from ..utils.jwt import verify_token

    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str | None = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Find user in database
    user = crud.get_user_by_username(username)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


@router.post(
    "/auth/register",
    tags=["Auth"],
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(user: UserBase, crud: UserCRUD = Depends(get_user_crud)):
    """
    Register a new user account.

    Args:
        user: User registration data (username and password)
        crud: UserCRUD instance for database operations

    Returns:
        UserResponse: Created user information (without password)

    Raises:
        HTTPException: 400 if username already exists
    """
    # Hash password before storing
    hashed_password = hash_password(user.password)
    user_data = UserBase(username=user.username, password=hashed_password)
    return crud.create_user(user_data)


@router.post("/auth/login", tags=["Auth"], response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    crud: UserCRUD = Depends(get_user_crud),
):
    """
    Authenticate user and return JWT access token.

    Args:
        form_data: OAuth2 form with username and password
        crud: UserCRUD instance for database operations

    Returns:
        Token: JWT access token and token type

    Raises:
        HTTPException: 401 if username or password is incorrect
    """
    user = crud.get_user_by_username(form_data.username)

    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/auth/me", tags=["Auth"], response_model=UserResponse)
async def read_users_me(current_user=Depends(get_current_user)):
    """
    Get current authenticated user information.

    Args:
        current_user: Authenticated user from JWT token

    Returns:
        dict: User information (username and ID)
    """
    return current_user
