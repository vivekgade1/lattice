# ruff: noqa
# app/core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    class Config:
        env_file = None  # LATTICE MOD (was ".env": never read the host repo's .env; see PROVENANCE.md)

settings = Settings()