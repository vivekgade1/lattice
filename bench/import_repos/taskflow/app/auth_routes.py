# ruff: noqa
"""Registration, login, and current-user routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session

from bench.import_repos.taskflow.app import crud
from bench.import_repos.taskflow.app.auth import CurrentUser, authenticate_user, create_access_token
from bench.import_repos.taskflow.app.database import get_session
from bench.import_repos.taskflow.app.models import Token, UserCreate, UserPublic

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(user_input: UserCreate, session: SessionDependency):
    """Register a new account with a unique email address."""

    if crud.get_user_by_email(session, str(user_input.email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    return crud.create_user(session, user_input)


@router.post("/token", response_model=Token)
def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDependency,
) -> Token:
    """Exchange valid credentials for a bearer token."""

    user = authenticate_user(session, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        str(user.id),
        request.app.state.settings.secret_key,
        request.app.state.settings.access_token_minutes,
    )
    return Token(access_token=token)


@router.get("/me", response_model=UserPublic)
def read_current_user(current_user: CurrentUser):
    """Return the authenticated user's public account data."""

    return current_user
