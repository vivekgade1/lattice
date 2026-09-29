# ruff: noqa
# app/main.py
from fastapi import FastAPI
from bench.import_repos.multivendor.app.routers import auth_router, user_router, vendor_router, product_router,order_router

def create_app() -> FastAPI:  # LATTICE MOD (module-level app wrapped in a factory)
    app = FastAPI(title="Multi-Vendor Inventory & Order Booking System")

    app.include_router(auth_router.router)
    app.include_router(user_router.router)
    app.include_router(vendor_router.router)
    app.include_router(product_router.router)
    app.include_router(order_router.router)
    return app
