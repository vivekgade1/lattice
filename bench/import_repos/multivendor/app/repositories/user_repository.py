# ruff: noqa
# app/repositories/user_repository.py
from sqlmodel import Session, select
from bench.import_repos.multivendor.app.models.user import User

def get_by_id(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)

def get_by_email(session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    return session.exec(statement).first()

def create(session: Session, user: User) -> User:
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def update(session: Session, user: User, data: dict) -> User:
    for key, value in data.items():
        setattr(user, key, value)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user