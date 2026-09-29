# ruff: noqa
# app/services/user_service.py
from sqlmodel import Session
from bench.import_repos.multivendor.app.repositories import user_repository
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.schemas.user import UserUpdate


def get_user_by_id(session: Session, user_id: int) -> User | None:
    return user_repository.get_by_id(session, user_id)


def update_own_profile(session: Session, current_user: User, data: UserUpdate) -> User:
    update_data = data.model_dump(exclude_unset=True)
    return user_repository.update(session, current_user, update_data)