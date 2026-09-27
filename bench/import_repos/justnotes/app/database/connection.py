# ruff: noqa
from sqlmodel import create_engine, SQLModel
from ..config import settings
import os

# Database engine using settings from configuration
engine = create_engine(settings.database_url)


def create_db_and_tables():
    """
    Create database and all tables based on SQLModel metadata.

    This function creates the database file (if using SQLite) and
    all tables defined in SQLModel classes. Should be called once
    during application startup.
    """
    # Ensure database directory exists for SQLite
    if settings.database_url.startswith("sqlite:///"):
        # Extract directory path from database URL
        db_path = settings.database_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    SQLModel.metadata.create_all(engine)


def get_db_session():
    """
    Dependency function to get database session.

    Yields a SQLModel Session for database operations.
    Used with FastAPI's dependency injection system.

    Yields:
        Session: Database session for performing operations

    Note:
        Session is automatically closed after use
    """
    from sqlmodel import Session

    with Session(engine) as session:
        yield session
