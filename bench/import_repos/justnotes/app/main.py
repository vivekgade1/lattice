# ruff: noqa
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .api import auth_router, health_router, notes_router, tags_router, users_router
from .config import settings


# LATTICE MOD: app construction wrapped in create_app() so each held-out run gets a
# fresh FastAPI instance bound to its own database (see
# bench/import_repos/justnotes/__init__.py); upstream instead built one app object at
# import time and called create_db_and_tables() (against the relative
# "sqlite:///db.sqlite") as a side effect of importing this module. The
# ``if __name__ == "__main__":  uvicorn.run(...)`` block is dropped -- it is a runnable
# CLI entry point, not a route.
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=settings.app_description,
        version=settings.app_version,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        contact={
            "name": "JustNotes",
            "url": "https://github.com/DatMayo/JustNotes/",
            "email": "mario.franze@gmail.com",
        },
        license_info={
            "name": "MIT License",
            "url": "https://github.com/DatMayo/JustNotes/blob/main/LICENSE",
        },
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins.split(",")
        if settings.cors_origins != "*"
        else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Order matters: auth router should be included first for authentication endpoints
    app.include_router(auth_router)
    app.include_router(notes_router)
    app.include_router(users_router)
    app.include_router(tags_router)
    app.include_router(health_router)
    return app


app = create_app()
