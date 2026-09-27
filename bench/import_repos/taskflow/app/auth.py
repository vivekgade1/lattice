# ruff: noqa
"""Authentication helpers and dependencies."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlmodel import Session, select

from bench.import_repos.taskflow.app.database import get_session
from bench.import_repos.taskflow.app.models import User

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
SessionDependency = Annotated[Session, Depends(get_session)]


def hash_password(password: str) -> str:
    """Hash a password using the recommended password-hashing algorithm."""

    return password_hash.hash(password)


def verify_password(password: str, password_digest: str) -> bool:
    """Return whether a password matches its stored digest."""

    return password_hash.verify(password, password_digest)


def create_access_token(subject: str, secret_key: str, minutes: int) -> str:
    """Create a signed, expiring access token."""

    expires_at = datetime.now(UTC) + timedelta(minutes=minutes)
    return jwt.encode({"sub": subject, "exp": expires_at}, secret_key, algorithm="HS256")


def authenticate_user(session: Session, email: str, password: str) -> User | None:
    """Authenticate a user by normalized email and password."""

    user = session.exec(select(User).where(User.email == email.strip().lower())).first()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user(
    request: Request,
    session: SessionDependency,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    """Resolve the authenticated user represented by an access token."""

    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            request.app.state.settings.secret_key,
            algorithms=["HS256"],
        )
        subject = payload.get("sub")
        if not subject:
            raise credentials_error
        user_id = int(subject)
    except (InvalidTokenError, TypeError, ValueError) as exc:
        raise credentials_error from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
