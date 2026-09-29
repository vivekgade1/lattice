"""Seed data for the vendored multi-vendor marketplace (``bench/import_repos/multivendor``).

``build_seed_db(path)`` writes a SQLite database with the upstream schema (created via
this app's isolated registry, see ``app/_lattice_registry.py``). It is written to be
ticket-neutral but not trivially uniform, the way a real shop's data is:

* every role: one admin, two verified vendors with products, one unverified vendor
  with none, buyers with several orders, a buyer with none, an inactive buyer;
* products in several categories, one inactive, some never sold, one out of stock;
* orders in every status, several with lines from more than one vendor, spread over
  January to March 2026 including instants on month boundaries;
* ``price_at_purchase`` that differs from the product's current price (the mug was
  10.00 before it went up to 12.00).

Timestamps are naive UTC, as upstream stores them (``datetime.utcnow``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session

from bench.import_repos.multivendor import sqlite_engine
from bench.import_repos.multivendor.app._lattice_registry import metadata_registry
from bench.import_repos.multivendor.app.core.security import hash_password
from bench.import_repos.multivendor.app.models import (
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Role,
    User,
    Vendor,
)

USER_EPOCH = datetime(2025, 12, 1, 9, 0, 0)

# (email, full_name, password, role, is_active)
SEED_USERS: tuple[tuple[str, str, str, Role, bool], ...] = (
    ("admin@example.com", "Ada Admin", "adminpass1", Role.ADMIN, True),
    ("vicky@example.com", "Vicky Vale", "vickypass1", Role.VENDOR, True),
    ("victor@example.com", "Victor Voss", "victorpass1", Role.VENDOR, True),
    ("vera@example.com", "Vera Vance", "verapass1", Role.VENDOR, True),
    ("carol@example.com", "Carol Chen", "carolpass1", Role.BUYER, True),
    ("dave@example.com", "Dave Diaz", "davepass1", Role.BUYER, True),
    ("erin@example.com", "Erin Evans", "erinpass1", Role.BUYER, False),
    ("frank@example.com", "Frank Fox", "frankpass1", Role.BUYER, True),
)

SEED_USER_CREDENTIALS: dict[str, str] = {email: pw for email, _, pw, _, _ in SEED_USERS}

# (owner email, business_name, is_verified)
SEED_VENDORS: tuple[tuple[str, str, bool], ...] = (
    ("vicky@example.com", "Vicky's Crafts", True),
    ("victor@example.com", "Victor Tools", True),
    ("vera@example.com", "Vera Vintage", False),
)

# (vendor index, name, description, category, price, stock, is_active)
SEED_PRODUCTS: tuple[tuple[int, str, str | None, str, float, int, bool], ...] = (
    (0, "Ceramic mug", "Hand-thrown, 350 ml.", "home", 12.0, 40, True),
    (0, "Soy candle", None, "home", 8.5, 30, True),
    (0, "Dot-grid notebook", "A5, 120 pages.", "stationery", 5.0, 100, True),
    (0, "Vintage poster", None, "decor", 20.0, 0, False),
    (1, "Claw hammer", "16 oz.", "tools", 25.0, 15, True),
    (1, "Screwdriver set", "6 pieces.", "tools", 18.0, 20, True),
    (1, "Tape measure", None, "tools", 9.99, 0, True),
    (1, "Work gloves", "One size.", "tools", 6.0, 50, True),
)

# (buyer email, created_at, status, [(product index, quantity, price_at_purchase)])
SEED_ORDERS: tuple[tuple[str, datetime, OrderStatus, tuple[tuple[int, int, float], ...]], ...] = (
    (
        "carol@example.com",
        datetime(2026, 1, 5, 10, 0),
        OrderStatus.DELIVERED,
        ((0, 2, 10.0), (4, 1, 25.0)),
    ),
    ("carol@example.com", datetime(2026, 1, 31, 23, 30), OrderStatus.DELIVERED, ((2, 4, 5.0),)),
    (
        "dave@example.com",
        datetime(2026, 2, 1, 0, 0),
        OrderStatus.SHIPPED,
        ((4, 1, 25.0), (5, 2, 18.0)),
    ),
    ("dave@example.com", datetime(2026, 2, 14, 12, 0), OrderStatus.CANCELLED, ((1, 3, 8.5),)),
    (
        "carol@example.com",
        datetime(2026, 2, 28, 23, 59, 59),
        OrderStatus.CONFIRMED,
        ((0, 1, 12.0), (1, 2, 8.5), (5, 1, 18.0)),
    ),
    ("erin@example.com", datetime(2026, 3, 1, 0, 0), OrderStatus.DELIVERED, ((2, 10, 5.0),)),
    ("dave@example.com", datetime(2026, 3, 10, 9, 0), OrderStatus.PENDING, ((0, 1, 12.0),)),
    (
        "carol@example.com",
        datetime(2026, 3, 15, 16, 45),
        OrderStatus.PENDING,
        ((1, 1, 8.5), (4, 2, 25.0)),
    ),
    ("carol@example.com", datetime(2026, 3, 20, 8, 15), OrderStatus.CANCELLED, ((5, 1, 18.0),)),
)


def build_seed_db(path: str | Path) -> Path:
    """Create (or overwrite) the SQLite file at ``path`` with the seed data above."""
    path = Path(path)
    if path.exists():
        path.unlink()
    engine = sqlite_engine(path)
    try:
        metadata_registry.metadata.create_all(engine)
        with Session(engine) as session:
            users: dict[str, User] = {}
            for k, (email, full_name, password, role, is_active) in enumerate(SEED_USERS):
                user = User(
                    email=email,
                    full_name=full_name,
                    hashed_password=hash_password(password),
                    role=role,
                    is_active=is_active,
                    created_at=USER_EPOCH + timedelta(days=k),
                )
                session.add(user)
                users[email] = user
            session.flush()

            vendors = []
            for email, business_name, is_verified in SEED_VENDORS:
                vendor = Vendor(
                    user_id=users[email].id, business_name=business_name, is_verified=is_verified
                )
                session.add(vendor)
                vendors.append(vendor)
            session.flush()

            products = []
            for vendor_idx, name, description, category, price, stock, is_active in SEED_PRODUCTS:
                product = Product(
                    vendor_id=vendors[vendor_idx].id,
                    name=name,
                    description=description,
                    category=category,
                    price=price,
                    stock_quantity=stock,
                    is_active=is_active,
                )
                session.add(product)
                products.append(product)
            session.flush()

            for email, created_at, status, lines in SEED_ORDERS:
                order = Order(
                    user_id=users[email].id,
                    status=status,
                    total_amount=sum(qty * price for _, qty, price in lines),
                    created_at=created_at,
                )
                session.add(order)
                session.flush()
                for product_idx, qty, price in lines:
                    session.add(
                        OrderItem(
                            order_id=order.id,
                            product_id=products[product_idx].id,
                            quantity=qty,
                            price_at_purchase=price,
                        )
                    )
            session.commit()
    finally:
        engine.dispose()
    return path
