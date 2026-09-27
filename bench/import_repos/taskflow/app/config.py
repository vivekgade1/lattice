# ruff: noqa
"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for the API."""

    app_name: str = "TaskFlow API"
    app_version: str = "2.0.0"
    database_url: str = "sqlite:///./taskflow.db"
    secret_key: str = "development-only-change-me"
    access_token_minutes: int = 30


def get_settings() -> Settings:
    """Build settings from environment variables."""

    return Settings(
        app_name=getenv("APP_NAME", "TaskFlow API"),
        app_version=getenv("APP_VERSION", "2.0.0"),
        database_url=getenv("DATABASE_URL", "sqlite:///./taskflow.db"),
        secret_key=getenv("SECRET_KEY", "development-only-change-me"),
        access_token_minutes=int(getenv("ACCESS_TOKEN_MINUTES", "30")),
    )
