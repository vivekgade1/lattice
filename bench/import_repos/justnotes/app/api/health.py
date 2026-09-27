# ruff: noqa
from fastapi import APIRouter
from ..config import settings
router = APIRouter()


@router.get("/health", status_code=200, tags=["Health"])
def health():
    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "version": settings.app_version
    }
