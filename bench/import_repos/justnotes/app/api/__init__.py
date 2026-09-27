# ruff: noqa
from .notes import router as notes_router
from .users import router as users_router
from .health import router as health_router
from .auth import router as auth_router
from .tags import router as tags_router

__all__ = [
    "notes_router",
    "users_router",
    "health_router",
    "auth_router",
    "tags_router",
]
