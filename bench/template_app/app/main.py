# ruff: noqa
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from bench.template_app.app.api.main import api_router
from bench.template_app.app.core.config import settings

FRONTEND_DIR = Path(__file__).parent / "frontend"  # not vendored; see create_app()


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


# LATTICE MOD: sentry initialisation removed; app construction wrapped in
# create_app() so each test gets a fresh FastAPI instance; the frontend mount
# (app.frontend("/", directory=FRONTEND_DIR)) is dropped (no frontend vendored).
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        generate_unique_id_function=custom_generate_unique_id,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_HOST],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_V1_STR)
    return app


app = create_app()
