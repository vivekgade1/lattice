# ruff: noqa
from sqlmodel import Session
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.core.security import hash_password, verify_password, create_access_token
from bench.import_repos.multivendor.app.repositories import user_repository
from bench.import_repos.multivendor.app.schemas import UserCreate


class AuthError(Exception):
    """Custom exception for authentication errors."""
    pass

def register_user(session: Session, data: UserCreate) -> User:
    existing = user_repository.get_by_email(session, data.email)
    if existing is not None:
        raise AuthError("Email already registered")

    new_user = User(
        full_name=data.full_name,
        email=data.email,
        hashed_password=hash_password(data.password),
    )
    return user_repository.create(session, new_user)


def authenticate_user(session: Session, email: str, password: str) -> str:
    user = user_repository.get_by_email(session, email)
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthError("Incorrect email or password")

    if not user.is_active:
        raise AuthError("Account is inactive")

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return token