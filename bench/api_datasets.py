"""Deterministic SQLite commerce DB for the API benchmark.

See docs/superpowers/specs/2026-09-27-lattice-api-design.md sections 3 and
5.1. This module owns the data layer only: `build_db` writes a byte-identical
SQLite file for a given seed, the module-level `Schema` constants describe
the tables in Lattice's type system (with owner/private metadata for the
checker's L802/L803 rules), `api_profile()` assembles the `ApiProfile` the
checker and runtime consume, and `schema_description()` renders the same
information as prose so both benchmark arms (Lattice and FastAPI) receive
identical schema information in their prompts.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema, validate_owner_via

S = Scalar

# ---------------------------------------------------------------------------
# Lattice scalar -> SQLite column type mapping.
#
# Int -> INTEGER, Float -> REAL, Str -> TEXT.
# Bool -> INTEGER, stored as 0/1 (SQLite has no native boolean).
# Date -> TEXT, stored as an ISO-8601 date string ("YYYY-MM-DD").
# ---------------------------------------------------------------------------
SQL_TYPES: Mapping[Scalar, str] = {
    S.INT: "INTEGER",
    S.FLOAT: "REAL",
    S.STR: "TEXT",
    S.BOOL: "INTEGER",  # 0/1
    S.DATE: "TEXT",  # ISO-8601 date string
}

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

USERS: Schema = Schema.from_pairs(
    [
        ("id", S.INT),
        ("email", S.STR),
        ("name", S.STR),
        ("role", S.STR),
        ("token", S.STR),
        ("password_hash", S.STR),
    ],
    keys=[("id",)],
    private=["password_hash", "token"],
)

PRODUCTS: Schema = Schema.from_pairs(
    [
        ("id", S.INT),
        ("name", S.STR),
        ("category", S.STR),
        ("price", S.FLOAT),
        ("stock", S.INT),
        ("active", S.BOOL),
    ],
    keys=[("id",)],
)

ORDERS: Schema = Schema.from_pairs(
    [
        ("id", S.INT),
        ("customer_id", S.INT),
        ("status", S.STR),
        ("created_at", S.DATE),
        ("total", S.FLOAT),
    ],
    keys=[("id",)],
    owner="customer_id",
)

# No direct owner column: an item belongs to the owner of its order (owner_via).
ORDER_ITEMS: Schema = Schema.from_pairs(
    [
        ("id", S.INT),
        ("order_id", S.INT),
        ("product_id", S.INT),
        ("qty", S.INT),
        ("unit_price", S.FLOAT),
    ],
    keys=[("id",)],
    owner_via=("order_id", "Orders"),
)

REVIEWS: Schema = Schema.from_pairs(
    [
        ("id", S.INT),
        ("product_id", S.INT),
        ("customer_id", S.INT),
        ("rating", S.INT),
        ("body", S.STR),
    ],
    keys=[("id",)],
    owner="customer_id",
)

ADDRESSES: Schema = Schema.from_pairs(
    [
        ("id", S.INT),
        ("customer_id", S.INT),
        ("line1", S.STR),
        ("city", S.STR),
        ("zip", S.STR),
    ],
    keys=[("id",)],
    owner="customer_id",
)

# schema name -> (SQL table name, Schema) — single source of truth for
# api_profile(), build_db()'s DDL, and schema_description().
_TABLES: tuple[tuple[str, str, Schema], ...] = (
    ("Users", "users", USERS),
    ("Products", "products", PRODUCTS),
    ("Orders", "orders", ORDERS),
    ("OrderItems", "order_items", ORDER_ITEMS),
    ("Reviews", "reviews", REVIEWS),
    ("Addresses", "addresses", ADDRESSES),
)

ROLES: frozenset[str] = frozenset({"Public", "Customer", "Admin"})


def api_profile() -> Profile:
    validate_owner_via({name: schema for name, _, schema in _TABLES})
    return Profile(
        schemas={},
        api=ApiProfile(
            tables={name: schema for name, _, schema in _TABLES},
            table_names={name: sql_name for name, sql_name, _ in _TABLES},
            roles=ROLES,
        ),
        outputs=None,
    )


# ---------------------------------------------------------------------------
# Fixture users: fixed ids/handles/tokens, independent of `seed`, so tasks and
# tests can reference them directly. alice/bob/carol have orders; dave has
# none; admin is an administrator.
# ---------------------------------------------------------------------------

USER_IDS: Mapping[str, int] = {
    "alice": 1,
    "bob": 2,
    "carol": 3,
    "dave": 4,
    "admin": 5,
}

TOKENS: Mapping[str, str] = {handle: f"tok-{handle}" for handle in USER_IDS}
_HANDLE_BY_ID: Mapping[int, str] = {uid: handle for handle, uid in USER_IDS.items()}

_FIXTURE_USERS: tuple[dict[str, object], ...] = (
    {"id": 1, "email": "alice@example.com", "name": "Alice Nguyen", "role": "Customer"},
    {"id": 2, "email": "bob@example.com", "name": "Bob Martinez", "role": "Customer"},
    {"id": 3, "email": "carol@example.com", "name": "Carol Kim", "role": "Customer"},
    {"id": 4, "email": "dave@example.com", "name": "Dave Okafor", "role": "Customer"},
    {"id": 5, "email": "admin@example.com", "name": "Admin User", "role": "Admin"},
)

# Ids 6-40 (35 users) fill out the roster: 6 and 7 are the two remaining
# admins (role total = 3), 8-40 (33 users) are customers.
_EXTRA_ADMIN_IDS: frozenset[int] = frozenset({6, 7})
_NUM_USERS = 40

# Customers with zero orders ("a few"): dave plus two of the generated
# customers, fixed by id so the guarantee holds for any seed.
_ZERO_ORDER_CUSTOMER_IDS: frozenset[int] = frozenset({4, 10, 20})

_NUM_PRODUCTS = 30
# Fixed ids that always land on the "some inactive" / "some stock 0" cases,
# regardless of what the RNG draws for everyone else.
_FORCED_ZERO_STOCK_PRODUCT_ID = 2
_FORCED_INACTIVE_PRODUCT_ID = 5

_NUM_ORDERS = 300
_ITEMS_PER_ORDER = 3  # 300 * 3 == 900, matching the spec's target exactly
_NUM_REVIEWS = 120
_NUM_ADDRESSES = 60

_STATUSES = ("pending", "paid", "shipped", "cancelled")
_STATUS_WEIGHTS = (0.30, 0.30, 0.25, 0.15)

_FIRST_NAMES = (
    "Ivy",
    "Leo",
    "Nora",
    "Owen",
    "Ruby",
    "Finn",
    "Sara",
    "Kai",
    "Luna",
    "Theo",
    "Elle",
    "Max",
    "Ada",
    "Jude",
    "Zoe",
    "Ethan",
    "Mia",
    "Liam",
    "Ava",
    "Noah",
)
_LAST_NAMES = (
    "Chen",
    "Patel",
    "Garcia",
    "Kim",
    "Novak",
    "Silva",
    "Haddad",
    "Nguyen",
    "Fischer",
    "Rossi",
    "Okafor",
    "Larsen",
    "Costa",
    "Ivanov",
    "Diaz",
    "Weber",
    "Suzuki",
    "Ali",
    "Moreau",
    "Reyes",
)
_CATEGORIES = ("electronics", "home", "outdoor", "office", "kitchen", "sports")
_PRODUCT_WORDS = (
    "Aero",
    "Nimbus",
    "Solid",
    "Crest",
    "Vivid",
    "Quartz",
    "Ember",
    "Drift",
    "Pulse",
    "Ridge",
    "Arc",
    "Halo",
    "Terra",
    "Nova",
    "Onyx",
    "Cobalt",
    "Amber",
    "Slate",
    "Reef",
    "Fern",
)
_PRODUCT_NOUNS = (
    "Chair",
    "Lamp",
    "Desk",
    "Speaker",
    "Backpack",
    "Bottle",
    "Jacket",
    "Monitor",
    "Tent",
    "Grill",
    "Blender",
    "Headset",
    "Mat",
    "Shelf",
    "Cooler",
    "Kettle",
    "Rack",
    "Case",
    "Pan",
    "Fan",
)
_STREET_NAMES = (
    "Maple",
    "Oak",
    "Cedar",
    "Birch",
    "Elm",
    "Pine",
    "River",
    "Lake",
    "Hill",
    "Sunset",
)
_CITIES = (
    "Springfield",
    "Riverton",
    "Fairview",
    "Georgetown",
    "Clinton",
    "Madison",
    "Franklin",
    "Greenville",
)
_REVIEW_PHRASES = (
    "Works great, exactly as described.",
    "Good value for the price.",
    "Arrived damaged, disappointed.",
    "Solid build quality, would buy again.",
    "Not what I expected, but usable.",
    "Excellent, exceeded expectations.",
    "Average product, nothing special.",
    "Stopped working after a week.",
    "Perfect fit for my needs.",
    "Would not recommend.",
)


def _hash_password(user_id: int) -> str:
    return hashlib.sha256(f"pwd-{user_id}".encode()).hexdigest()


def _date(year: int, day_of_year: int) -> str:
    """A deterministic ISO date: `day_of_year` (0-based) days after Jan 1."""
    from datetime import date, timedelta

    return (date(year, 1, 1) + timedelta(days=day_of_year)).isoformat()


def _generate_users(rng: random.Random) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fixture in _FIXTURE_USERS:
        uid = int(fixture["id"])
        rows.append(
            {
                "id": uid,
                "email": fixture["email"],
                "name": fixture["name"],
                "role": fixture["role"],
                "token": TOKENS[_HANDLE_BY_ID[uid]],
                "password_hash": _hash_password(uid),
            }
        )
    for uid in range(len(_FIXTURE_USERS) + 1, _NUM_USERS + 1):
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        role = "Admin" if uid in _EXTRA_ADMIN_IDS else "Customer"
        rows.append(
            {
                "id": uid,
                "email": f"{first.lower()}.{last.lower()}{uid}@example.com",
                "name": f"{first} {last}",
                "role": role,
                "token": f"tok-user{uid}",
                "password_hash": _hash_password(uid),
            }
        )
    return rows


def _generate_products(rng: random.Random) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for pid in range(1, _NUM_PRODUCTS + 1):
        name = f"{rng.choice(_PRODUCT_WORDS)} {rng.choice(_PRODUCT_NOUNS)}"
        category = rng.choice(_CATEGORIES)
        price = round(rng.uniform(5.0, 500.0), 2)
        if pid == _FORCED_ZERO_STOCK_PRODUCT_ID:
            stock = 0
        else:
            stock = rng.randint(0, 300)
        if pid == _FORCED_INACTIVE_PRODUCT_ID:
            active = 0
        else:
            active = 1 if rng.random() < 0.85 else 0
        rows.append(
            {
                "id": pid,
                "name": name,
                "category": category,
                "price": price,
                "stock": stock,
                "active": active,
            }
        )
    return rows


def _customer_ids(users: list[dict[str, object]]) -> list[int]:
    return [int(u["id"]) for u in users if u["role"] == "Customer"]


def _order_counts_by_customer(rng: random.Random, customer_ids: list[int]) -> dict[int, int]:
    active = [c for c in customer_ids if c not in _ZERO_ORDER_CUSTOMER_IDS]
    counts = {c: 2 for c in active}
    remaining = _NUM_ORDERS - 2 * len(active)
    for _ in range(remaining):
        c = rng.choice(active)
        counts[c] += 1
    return counts


def _generate_orders_and_items(
    rng: random.Random, customer_ids: list[int], products: list[dict[str, object]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    counts = _order_counts_by_customer(rng, customer_ids)
    orders: list[dict[str, object]] = []
    items: list[dict[str, object]] = []
    order_id = 0
    item_id = 0
    for customer_id in sorted(counts):
        for _ in range(counts[customer_id]):
            order_id += 1
            if order_id <= len(_STATUSES):
                status = _STATUSES[order_id - 1]  # guarantee every status appears
            else:
                status = rng.choices(_STATUSES, weights=_STATUS_WEIGHTS, k=1)[0]
            created_at = _date(2024, rng.randint(0, 364))
            total = 0.0
            for _ in range(_ITEMS_PER_ORDER):
                item_id += 1
                product = rng.choice(products)
                qty = rng.randint(1, 5)
                unit_price = float(product["price"])
                total += qty * unit_price
                items.append(
                    {
                        "id": item_id,
                        "order_id": order_id,
                        "product_id": product["id"],
                        "qty": qty,
                        "unit_price": unit_price,
                    }
                )
            orders.append(
                {
                    "id": order_id,
                    "customer_id": customer_id,
                    "status": status,
                    "created_at": created_at,
                    "total": round(total, 2),
                }
            )
    return orders, items


def _generate_reviews(
    rng: random.Random, customer_ids: list[int], products: list[dict[str, object]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rid in range(1, _NUM_REVIEWS + 1):
        product = rng.choice(products)
        customer_id = rng.choice(customer_ids)
        rating = rng.randint(1, 5)
        body = rng.choice(_REVIEW_PHRASES)
        rows.append(
            {
                "id": rid,
                "product_id": product["id"],
                "customer_id": customer_id,
                "rating": rating,
                "body": body,
            }
        )
    return rows


def _generate_addresses(rng: random.Random, customer_ids: list[int]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for aid in range(1, _NUM_ADDRESSES + 1):
        customer_id = rng.choice(customer_ids)
        number = rng.randint(1, 9999)
        street = rng.choice(_STREET_NAMES)
        city = rng.choice(_CITIES)
        if aid <= 15:
            zip_code = f"0{rng.randint(0, 9999):04d}"  # leading zero, e.g. real New England zips
        else:
            zip_code = f"{rng.randint(10000, 99999)}"
        rows.append(
            {
                "id": aid,
                "customer_id": customer_id,
                "line1": f"{number} {street} St",
                "city": city,
                "zip": zip_code,
            }
        )
    return rows


_DDL: Mapping[str, str] = {
    "users": """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
    """,
    "products": """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL,
            active INTEGER NOT NULL
        )
    """,
    "orders": """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            total REAL NOT NULL
        )
    """,
    "order_items": """
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            qty INTEGER NOT NULL,
            unit_price REAL NOT NULL
        )
    """,
    "reviews": """
        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id),
            customer_id INTEGER NOT NULL REFERENCES users(id),
            rating INTEGER NOT NULL,
            body TEXT NOT NULL
        )
    """,
    "addresses": """
        CREATE TABLE addresses (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES users(id),
            line1 TEXT NOT NULL,
            city TEXT NOT NULL,
            zip TEXT NOT NULL
        )
    """,
}

_INSERT_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "users": ("id", "email", "name", "role", "token", "password_hash"),
    "products": ("id", "name", "category", "price", "stock", "active"),
    "orders": ("id", "customer_id", "status", "created_at", "total"),
    "order_items": ("id", "order_id", "product_id", "qty", "unit_price"),
    "reviews": ("id", "product_id", "customer_id", "rating", "body"),
    "addresses": ("id", "customer_id", "line1", "city", "zip"),
}


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, object]]) -> None:
    columns = _INSERT_COLUMNS[table]
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(row[c] for c in columns) for row in rows])


def build_db(path: Path, seed: int = 21) -> None:
    """Write a deterministic SQLite commerce DB to `path`.

    Byte-identical across runs for the same seed: a fixed insertion order, no
    autoincrement/rowid drift (every id is assigned explicitly), and no
    timestamps drawn from the wall clock.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    rng = random.Random(seed)
    users = _generate_users(rng)
    products = _generate_products(rng)
    customer_ids = _customer_ids(users)
    orders, items = _generate_orders_and_items(rng, customer_ids, products)
    reviews = _generate_reviews(rng, customer_ids, products)
    addresses = _generate_addresses(rng, customer_ids)

    conn = sqlite3.connect(str(path))
    try:
        for table in ("users", "products", "orders", "order_items", "reviews", "addresses"):
            conn.execute(_DDL[table])
        _insert_rows(conn, "users", users)
        _insert_rows(conn, "products", products)
        _insert_rows(conn, "orders", orders)
        _insert_rows(conn, "order_items", items)
        _insert_rows(conn, "reviews", reviews)
        _insert_rows(conn, "addresses", addresses)
        conn.commit()
    finally:
        conn.close()


def schema_description() -> str:
    """Human-readable description of the commerce DB: tables, columns and
    Lattice types, keys, owner columns, private columns, roles, and the
    bearer-token auth scheme. Both benchmark arms (Lattice and FastAPI)
    receive this exact text so they have identical schema information."""
    lines: list[str] = []
    lines.append("Commerce database schema (SQLite):")
    lines.append("")
    for name, sql_name, schema in _TABLES:
        col_text = ", ".join(f"{col}: {scalar.value}" for col, scalar in schema.columns)
        lines.append(f"- {name} (table `{sql_name}`): {{{col_text}}}")
        if schema.keys:
            key_cols = ", ".join(schema.keys[0])
            lines.append(f"    key: {key_cols}")
        if schema.owner:
            lines.append(f"    owner column: {schema.owner} (rows are owned by this user id)")
        if schema.owner_via:
            fk, parent = schema.owner_via
            lines.append(
                f"    owned through {fk} → {parent} (a row belongs to the owner of its "
                f"{parent} row)"
            )
        if schema.private:
            private_cols = ", ".join(sorted(schema.private))
            lines.append(f"    private columns (never returned to clients): {private_cols}")
    lines.append("")
    lines.append(f"Roles: {', '.join(sorted(ROLES))}. Admin may access every role's endpoints.")
    lines.append(
        "Auth: bearer token in the `Authorization: Bearer <token>` header, resolved against "
        "users.token. Missing or unknown token -> 401. Role not permitted for the endpoint -> 403."
    )
    return "\n".join(lines)
