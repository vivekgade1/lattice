# ruff: noqa
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration settings.

    Loads configuration from environment variables and .env file.
    Uses JUSTNOTES_ prefix for environment variables.
    Provides default values for all settings.

    Attributes:
        database_url: Database connection URL
        app_name: Name of the application
        app_version: Version of the application
        app_description: Description of the application
        secret_key: Secret key for JWT token signing
        algorithm: Algorithm used for JWT token signing
        access_token_expire_minutes: Minutes until JWT token expires
        environment: Current environment (development/production)
        debug: Enable debug mode
        host: Server host for Docker
        port: Server port for Docker
        reload: Enable auto-reload for development
    """

    # Database Configuration
    database_url: str = "sqlite:///db.sqlite"

    # Application Configuration
    app_name: str = "JustNotes"
    app_version: str = "0.0.1"
    app_description: str = "JustNotes is a simple note-taking app"

    # JWT Configuration
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Environment Configuration
    environment: str = "development"
    debug: bool = True

    # Server Configuration (for Docker)
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False

    # CORS Configuration
    cors_origins: str = "*"

    # LLM Service Configuration
    llm_service_url: str = "http://localhost:1234/api/v1/chat"
    llm_model: str = "google/gemma-3n-e4b"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="JUSTNOTES_",
        extra="ignore",
    )


# Global settings instance
settings = Settings()
