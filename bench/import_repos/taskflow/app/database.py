# ruff: noqa
"""Database engine and request-scoped session helpers."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine


def build_engine(database_url: str) -> Engine:
    """Create a SQLModel engine for the configured database."""

    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def get_session(request: Request) -> Generator[Session, None, None]:
    """Provide one database session per HTTP request."""

    with Session(request.app.state.engine) as session:
        yield session
