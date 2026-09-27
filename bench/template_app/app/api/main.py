# ruff: noqa
from fastapi import APIRouter

from bench.template_app.app.api.routes import items, login, private, users, utils
from bench.template_app.app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
