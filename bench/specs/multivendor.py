"""The multi-vendor app's spec (Phase 0, W2-B): `SPEC`, a `lattice.spec.Spec`.

App: `bench/import_repos/multivendor` (upstream CS-Nithin/multi-vendor-inventory-booking-
system). The profile starts from the importer's output (`bench/imports/multivendor/
profile.py`), reviewed by the app's owner; every correction is marked `REVIEW:`.

Seed data (`bench.import_repos.multivendor.seed`), as ids, used by the scenarios here and
in `multivendor_tickets`:

    users     1 admin (ADMIN)   2 vicky (VENDOR)  3 victor (VENDOR)  4 vera (VENDOR)
              5 carol (BUYER)   6 dave (BUYER)    7 erin (BUYER, inactive)  8 frank (BUYER)
    vendors   1 Vicky's Crafts (user 2, verified)  2 Victor Tools (user 3, verified)
              3 Vera Vintage (user 4, unverified, no products)
    products  id name               vendor category    price stock active
              1  Ceramic mug        1      home        12.00 40    yes
              2  Soy candle         1      home         8.50 30    yes
              3  Dot-grid notebook  1      stationery   5.00 100   yes
              4  Vintage poster     1      decor       20.00 0     no
              5  Claw hammer        2      tools       25.00 15    yes
              6  Screwdriver set    2      tools       18.00 20    yes
              7  Tape measure       2      tools        9.99 0     yes
              8  Work gloves        2      tools        6.00 50    yes
    orders    id buyer created_at           status     total  lines (item id: product x qty @ price)
              1  carol 2026-01-05 10:00:00  delivered  45.0   1: P1x2@10.0, 2: P5x1@25.0
              2  carol 2026-01-31 23:30:00  delivered  20.0   3: P3x4@5.0
              3  dave  2026-02-01 00:00:00  shipped    61.0   4: P5x1@25.0, 5: P6x2@18.0
              4  dave  2026-02-14 12:00:00  cancelled  25.5   6: P2x3@8.5
              5  carol 2026-02-28 23:59:59  confirmed  47.0   7: P1x1@12, 8: P2x2@8.5, 9: P6x1@18
              6  erin  2026-03-01 00:00:00  delivered  50.0   10: P3x10@5.0
              7  dave  2026-03-10 09:00:00  pending    12.0   11: P1x1@12.0
              8  carol 2026-03-15 16:45:00  pending    58.5   12: P2x1@8.5, 13: P5x2@25.0
              9  carol 2026-03-20 08:15:00  cancelled  18.0   14: P6x1@18.0

(The seed does not decrement stock for its orders: stock is as listed.)

Scenario conventions (for the W2-A runner): each scenario starts from a fresh seed
database; `user` signs in through `POST /auth/login` with `users[handle]`; `expect_body`,
when given, is the whole response body (exact JSON equality), so it is given only where
every field is known (never for a new row's `created_at`).
"""

from __future__ import annotations

from bench.imports.multivendor.profile import NATIVES
from lattice.profile import (
    ApiProfile,
    NativeSig,
    NativeToken,
    Nested,
    Profile,
    ResponseField,
    RoleColumn,
)
from lattice.spec import Accepted, Invariant, Machine, Scenario, Spec, Step, Unchecked
from lattice.typesys import Scalar, Schema

__all__ = ["NATIVES", "ORDER_STATES", "PROFILE", "SPEC", "USERS", "order_read", "product_read"]

ORDER_STATES = ("pending", "confirmed", "shipped", "delivered", "cancelled")

# ---- profile (reviewed) ------------------------------------------------------------------

USER = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("full_name", Scalar.STR),
        ("email", Scalar.STR),
        ("hashed_password", Scalar.STR),
        ("role", Scalar.STR),
        ("is_active", Scalar.BOOL),
        ("created_at", Scalar.DATETIME),
    ],
    # `email` is unique (models/user.py: unique=True), but it is not a key here: Lattice
    # keys never change and PATCH /users/me changes the email. Uniqueness is the
    # invariant one_account_per_email instead.
    keys=[("id",)],
    private=["hashed_password"],
    one_of={"role": ("buyer", "vendor", "admin")},
    one_of_stored={"role": ("BUYER", "VENDOR", "ADMIN")},
)

VENDOR = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("user_id", Scalar.INT),
        ("business_name", Scalar.STR),
        ("is_verified", Scalar.BOOL),
    ],
    # REVIEW: `user_id` is unique (models/vendor.py: unique=True -- one vendor profile per
    # user); the import listed only `id`. Also stated as the invariant one_vendor_per_user.
    keys=[("id",), ("user_id",)],
    owner="user_id",
)

PRODUCT = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("vendor_id", Scalar.INT),
        ("name", Scalar.STR),
        ("description", Scalar.STR),
        ("category", Scalar.STR),
        ("price", Scalar.FLOAT),
        ("stock_quantity", Scalar.INT),
        ("version", Scalar.INT),
        ("is_active", Scalar.BOOL),
    ],
    keys=[("id",)],
    nullable=["description"],
    # REVIEW: the import made Product `owner_via=("vendor_id", "Vendor")`. In Lattice that
    # makes a product private to its vendor, but the catalogue is public
    # (GET /products, GET /products/{id} need no login). Ownership through the vendor
    # governs *writes* only, so it is not declared here; the write programs
    # (update_product) check `vendor_id == <caller's vendor>.id` themselves, and the
    # rule is listed in `unchecked` (Lattice has no "public read, owner write" table).
)

ORDER = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("user_id", Scalar.INT),
        ("status", Scalar.STR),
        ("total_amount", Scalar.FLOAT),
        ("created_at", Scalar.DATETIME),
    ],
    keys=[("id",)],
    owner="user_id",
    one_of={"status": ORDER_STATES},
    one_of_stored={"status": tuple(s.upper() for s in ORDER_STATES)},
)

ORDER_ITEM = Schema.from_pairs(
    [
        ("id", Scalar.INT),
        ("order_id", Scalar.INT),
        ("product_id", Scalar.INT),
        ("quantity", Scalar.INT),
        ("price_at_purchase", Scalar.FLOAT),
    ],
    keys=[("id",)],
    owner_via=("order_id", "Order"),
)

# Column bounds (price > 0, quantity > 0, stock >= 0) have no place in a Lattice Schema:
# they are the invariants below and the parameter bounds of the programs.

RESPONSES: dict[str, tuple[ResponseField, ...]] = {
    "UserRead": ("id", "full_name", "email", "role", "is_active", "created_at"),
    # REVIEW: `items` is a nested list of OrderItemRead (schemas/order.py: List[
    # OrderItemRead]); the import wrote it as a plain column. Upstream loads the
    # relationship Order.items (order_items.order_id, no order_by: SQLite reads it
    # through ix_order_items_order_id, by id), Lattice orders it by OrderItem.id.
    "OrderRead": (
        "id",
        "user_id",
        "status",
        "total_amount",
        "created_at",
        Nested("items", "OrderItemRead", table="OrderItem", via="order_id"),
    ),
    # REVIEW: OrderItemRead was missing (schemas/order.py); added for nested `items`.
    "OrderItemRead": ("id", "product_id", "quantity", "price_at_purchase"),
    "ProductRead": (
        "id",
        "vendor_id",
        "name",
        "description",
        "category",
        "price",
        "stock_quantity",
        "version",
        "is_active",
    ),
    "VendorRead": ("id", "user_id", "business_name", "is_verified"),
}


def profile() -> Profile:
    """The reviewed Lattice profile of the multi-vendor app."""
    api = ApiProfile(
        tables={
            "Order": ORDER,
            "OrderItem": ORDER_ITEM,
            "Product": PRODUCT,
            "User": USER,
            "Vendor": VENDOR,
        },
        table_names={
            "Order": "orders",
            "OrderItem": "order_items",
            "Product": "products",
            "User": "users",
            "Vendor": "vendors",
        },
        roles=frozenset({"Public", "Buyer", "Vendor", "Admin"}),
        users_table="User",
        responses=RESPONSES,
        natives={
            "hash_password": NativeSig((Scalar.STR,), Scalar.STR),
            "verify_password": NativeSig((Scalar.STR, Scalar.STR), Scalar.BOOL, reads_private=True),
            "decode_token": NativeSig((Scalar.STR,), Scalar.INT, nullable=True),
        },
        auth=NativeToken(
            "decode_token",
            invalid=(401, "Could not validate credentials"),
            unknown=(401, "Could not validate credentials"),
        ),
        # The role set: User.role stores the enum's member names; each maps to a role.
        roles_from=RoleColumn(
            "role", values={"buyer": "Buyer", "vendor": "Vendor", "admin": "Admin"}
        ),
        backend="sqlite",
        uuid_storage="hex32",
        datetime_storage="sql_space",
        null_order="smallest",
        active_column="is_active",
        inactive_status=403,
        inactive_detail="Inactive user",
        error_body="fastapi",
        unauthorized_detail="Not authenticated",
        forbidden_detail="You do not have permission to perform this action.",
        datetime_wire="naive",
    )
    return Profile(schemas={}, api=api)


PROFILE = profile()


# ---- programs: the executable behaviour of the endpoints Lattice can express today ----
#
# Shared deviations from upstream, forced by the language (also in `unchecked`):
# - upstream strings are unbounded; Lattice needs bounds on written parameters, so they
#   take the importer's `Str(min_len=0, max_len=255)`;
# - `Float` has no exclusive bound: upstream's `price > 0` is `Float(ge=0.000001, ...)`,
#   with an upper bound of 1e9 that upstream does not have.
#
# NESTED: the order endpoints answer OrderRead, an order with its nested `items` (W1-B
# nested responses). GET /orders/me (list_own_orders) and GET /orders/{order_id}
# (get_order) have programs. The others have none (see unchecked for the missing
# construct): POST /orders (place_order), PATCH /orders/{order_id}/status
# (update_order_status), POST /orders/flash-sale-purchase (flash_sale_purchase).
# POST /auth/login has no program: it reads a form body and issues a JWT (see unchecked).

AUTH_PROGRAM = """# lattice: v1
USER_READ = ["id", "full_name", "email", "role", "is_active", "created_at"]


@endpoint("POST", "/auth/register", auth=Public, response="UserRead")
def register(
    full_name: Str(min_len=0, max_len=255),
    email: Email(max_len=255),
    password: Str(min_len=0, max_len=72),
) -> Created | BadRequest:
    users = fetch(User)
    matching = where(users, col("email") == email)
    counted = group(matching, by=[], n=agg.count())
    checked = require(counted, col("n") == 0, else_=BadRequest("Email already registered"))
    hashed = native.hash_password(password)
    created = insert(
        User,
        full_name=full_name,
        email=email,
        hashed_password=hashed,
        role="buyer",
        is_active=True,
        created_at=fn.now(),
    )
    result = select(created, cols=USER_READ)
    return result
"""

USERS_PROGRAM = """# lattice: v1
USER_READ = ["id", "full_name", "email", "role", "is_active", "created_at"]


@endpoint("GET", "/users/me", auth=Authenticated, response="UserRead")
def read_own_profile(user: CurrentUser) -> One:
    shown = select(user, cols=USER_READ)
    return shown


@endpoint("PATCH", "/users/me", auth=Authenticated, response="UserRead")
def update_own_profile(
    user: CurrentUser,
    full_name: Str(min_len=0, max_len=255) = UNSET,
    email: Email(max_len=255) = UNSET,
) -> One:
    changed = update(user, full_name=full_name, email=email)
    shown = select(changed, cols=USER_READ)
    return shown
"""

VENDORS_PROGRAM = """# lattice: v1
VENDOR_READ = ["id", "user_id", "business_name", "is_verified"]


@endpoint("POST", "/vendors", auth=Authenticated, response="VendorRead")
def register_as_vendor(
    business_name: Str(min_len=0, max_len=255), user: CurrentUser
) -> Created | BadRequest:
    vendors = fetch(Vendor)
    own = mine(vendors, by=user)
    counted = group(own, by=[], n=agg.count())
    fresh = require(
        counted, col("n") == 0, else_=BadRequest("This user is already registered as a vendor")
    )
    created = insert(Vendor, owner=user, business_name=business_name, is_verified=False)
    promoted = update(user, role="vendor")
    shown = select(created, cols=VENDOR_READ)
    return shown


@endpoint("GET", "/vendors/me", auth=Authenticated, response="VendorRead")
def read_own_vendor_profile(user: CurrentUser) -> One | NotFound:
    vendors = fetch(Vendor)
    own = mine(vendors, by=user)
    shown = select(own, cols=VENDOR_READ)
    vendor = one(shown, else_=NotFound("No vendor profile found for this user"))
    return vendor


@endpoint("PATCH", "/vendors/me", auth=Authenticated, response="VendorRead")
def update_own_vendor_profile(
    user: CurrentUser, business_name: Str(min_len=0, max_len=255) = UNSET
) -> One | NotFound:
    vendors = fetch(Vendor)
    own = mine(vendors, by=user)
    shown = select(own, cols=VENDOR_READ)
    vendor = one(shown, else_=NotFound("No vendor profile found for this user"))
    updated = update(vendor, business_name=business_name)
    return updated


@endpoint("PATCH", "/vendors/{vendor_id}/verify", auth=Admin, response="VendorRead")
def verify_vendor(vendor_id: Int, is_verified: Bool, user: CurrentUser) -> One | NotFound:
    vendors = fetch(Vendor)
    found = where(vendors, col("id") == vendor_id)
    selected = select(found, cols=VENDOR_READ)
    vendor = one(selected, else_=NotFound("Vendor not found"))
    updated = update(vendor, is_verified=is_verified)
    return updated
"""

PRODUCTS_PROGRAM = """# lattice: v1
PRODUCT_READ = [
    "id", "vendor_id", "name", "description", "category", "price", "stock_quantity",
    "version", "is_active",
]


@endpoint("POST", "/products", auth=Roles(Vendor), response="ProductRead")
def create_product(
    name: Str(min_len=0, max_len=255),
    category: Str(min_len=0, max_len=255),
    price: Float(ge=0.000001, le=1000000000),
    stock_quantity: Int(ge=0, le=1000000000),
    user: CurrentUser,
    description: Str(min_len=0, max_len=255) | None = None,
) -> Created | Forbidden:
    vendors = fetch(Vendor)
    own = mine(vendors, by=user)
    vendor = one(own, else_=Forbidden("You must have a vendor profile to create products"))
    created = insert(
        Product,
        vendor_id=vendor.id,
        name=name,
        description=description,
        category=category,
        price=price,
        stock_quantity=stock_quantity,
        version=0,
        is_active=True,
    )
    shown = select(created, cols=PRODUCT_READ)
    return shown


@endpoint("GET", "/products", auth=Public, response="ProductRead")
def list_products(
    category: Str | None = None,
    min_price: Float(ge=0) | None = None,
    max_price: Float(ge=0) | None = None,
    skip: Int(ge=0) = 0,
    limit: Int(ge=0, le=100) = 20,
) -> List:
    products = fetch(Product)
    active = where(products, col("is_active"))
    in_category = where(active, is_null(category) | (col("category") == category))
    above = where(in_category, is_null(min_price) | (col("price") >= min_price))
    below = where(above, is_null(max_price) | (col("price") <= max_price))
    ordered = sort(below, by=["id"])
    shown = select(ordered, cols=PRODUCT_READ)
    result = page(shown, limit=limit, offset=skip)
    return result


@endpoint("GET", "/products/{product_id}", auth=Public, response="ProductRead")
def get_product(product_id: Int) -> One | NotFound:
    products = fetch(Product)
    found = where(products, col("id") == product_id)
    shown = select(found, cols=PRODUCT_READ)
    product = one(shown, else_=NotFound("Product not found"))
    return product


@endpoint("PATCH", "/products/{product_id}", auth=Roles(Vendor), response="ProductRead")
def update_product(
    product_id: Int,
    user: CurrentUser,
    name: Str(min_len=0, max_len=255) = UNSET,
    description: Str(min_len=0, max_len=255) = UNSET,
    category: Str(min_len=0, max_len=255) = UNSET,
    price: Float(ge=0.000001, le=1000000000) = UNSET,
    is_active: Bool = UNSET,
) -> One | Forbidden | NotFound:
    vendors = fetch(Vendor)
    own = mine(vendors, by=user)
    vendor = one(own, else_=Forbidden("You do not have a vendor profile"))
    products = fetch(Product)
    found = where(products, col("id") == product_id)
    shown = select(found, cols=PRODUCT_READ)
    product = one(shown, else_=NotFound("Product not found"))
    marked = derive(product, is_own=col("vendor_id") == vendor.id)
    owned_product = require(marked, col("is_own"), else_=Forbidden("You do not own this product"))
    changed = update(
        owned_product,
        name=name,
        description=description,
        category=category,
        price=price,
        is_active=is_active,
    )
    updated = select(changed, cols=PRODUCT_READ)
    return updated
"""

# get_order: anyone signed in; the admin (VIEW_ALL_ORDERS) reads any order, everyone else
# only their own: 404 "Order not found" first, then 403 for another user's order
# (owner_is lets Admin through; its L823 warning is the intended contract here).
# list_own_orders: buyers and vendors (VIEW_OWN_ORDERS; the admin gets the permission
# 403), by id (upstream has no ORDER BY; SQLite reads through ix_orders_user_id, by id),
# capped at 100 (L831; upstream has no cap, see unchecked).
ORDERS_PROGRAM = """# lattice: v1
ORDER_READ = ["id", "user_id", "status", "total_amount", "created_at"]


@endpoint("GET", "/orders/me", auth=Roles(Buyer, Vendor), response="OrderRead")
def list_own_orders(user: CurrentUser) -> List:
    orders = fetch(Order)
    own = mine(orders, by=user)
    ordered = sort(own, by=["id"])
    shown = select(ordered, cols=ORDER_READ)
    result = limit(shown, n=100)
    return result


@endpoint("GET", "/orders/{order_id}", auth=Authenticated, response="OrderRead")
def get_order(order_id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    orders = fetch(Order)
    found = where(orders, col("id") == order_id)
    order = one(found, else_=NotFound("Order not found"))
    allowed = require(
        order, owner_is(user), else_=Forbidden("You do not have access to this order")
    )
    shown = select(allowed, cols=ORDER_READ)
    return shown
"""

PROGRAMS: dict[str, str] = {
    "auth": AUTH_PROGRAM,
    "users": USERS_PROGRAM,
    "vendors": VENDORS_PROGRAM,
    "products": PRODUCTS_PROGRAM,
    "orders": ORDERS_PROGRAM,
}

# ---- state machine: Order.status (services/order_service.py VALID_TRANSITIONS) -------

ORDER_MACHINE = Machine(
    "Order",
    "status",
    {
        "pending": ("confirmed", "cancelled"),
        "confirmed": ("shipped", "cancelled"),
        "shipped": ("delivered",),
    },
    initial=("pending",),
)

# ---- invariants (each holds on the seed: tests/spec/test_multivendor_spec.py) ----------

INVARIANTS = (
    Invariant(
        "order_total",
        "An order's total is the sum of quantity x price at purchase over its lines.",
        "Order.total_amount == "
        'sum(OrderItem.quantity * OrderItem.price_at_purchase, by="order_id")',
    ),
    Invariant(
        "stock_never_negative",
        "A product's stock is never negative.",
        "Product.stock_quantity >= 0",
    ),
    Invariant("price_positive", "A product's price is positive.", "Product.price > 0"),
    Invariant(
        "line_quantity_positive",
        "Every order line orders at least one unit.",
        "OrderItem.quantity > 0",
    ),
    Invariant(
        "line_price_positive",
        "Every order line was bought at a positive price.",
        "OrderItem.price_at_purchase > 0",
    ),
    Invariant(
        "one_vendor_per_user",
        "A user has at most one vendor profile.",
        'count(Vendor, by="user_id") <= 1',
    ),
    Invariant(
        "one_account_per_email",
        "No two accounts share an email address.",
        'count(User, by="email") <= 1',
    ),
)

# ---- users: the seed's credentials (seed.py SEED_USERS) --------------------------------

USERS: dict[str, tuple[str, str]] = {
    "admin": ("admin@example.com", "adminpass1"),
    "vicky": ("vicky@example.com", "vickypass1"),
    "victor": ("victor@example.com", "victorpass1"),
    "vera": ("vera@example.com", "verapass1"),
    "carol": ("carol@example.com", "carolpass1"),
    "dave": ("dave@example.com", "davepass1"),
    # inactive: login answers 401 "Account is inactive", so no scenario can sign in as her
    "erin": ("erin@example.com", "erinpass1"),
    "frank": ("frank@example.com", "frankpass1"),
}

# ---- scenarios for the existing routes -------------------------------------------------

DENIED = {"detail": "You do not have permission to perform this action."}
NO_ACCESS = {"detail": "You do not have access to this order"}
ORDER_NOT_FOUND = {"detail": "Order not found"}
NO_VENDOR = {"detail": "No vendor profile found for this user"}

_PRODUCTS = {
    # id: (vendor_id, name, description, category, price, stock, is_active)
    1: (1, "Ceramic mug", "Hand-thrown, 350 ml.", "home", 12.0, 40, True),
    2: (1, "Soy candle", None, "home", 8.5, 30, True),
    3: (1, "Dot-grid notebook", "A5, 120 pages.", "stationery", 5.0, 100, True),
    4: (1, "Vintage poster", None, "decor", 20.0, 0, False),
    5: (2, "Claw hammer", "16 oz.", "tools", 25.0, 15, True),
    6: (2, "Screwdriver set", "6 pieces.", "tools", 18.0, 20, True),
    7: (2, "Tape measure", None, "tools", 9.99, 0, True),
    8: (2, "Work gloves", "One size.", "tools", 6.0, 50, True),
}


def product_read(pid: int, **changes: object) -> dict:
    """Seed product `pid` as ProductRead shows it, with `changes` applied."""
    vendor_id, name, description, category, price, stock, active = _PRODUCTS[pid]
    row = {
        "id": pid,
        "vendor_id": vendor_id,
        "name": name,
        "description": description,
        "category": category,
        "price": price,
        "stock_quantity": stock,
        "version": 0,
        "is_active": active,
    }
    return {**row, **changes}


def line(item_id: int, product_id: int, quantity: int, price: float) -> dict:
    """An OrderItemRead."""
    return {
        "id": item_id,
        "product_id": product_id,
        "quantity": quantity,
        "price_at_purchase": price,
    }


def order_read(
    oid: int, user_id: int, status: str, total: float, created_at: str, items: list[dict]
) -> dict:
    """An OrderRead."""
    return {
        "id": oid,
        "user_id": user_id,
        "status": status,
        "total_amount": total,
        "created_at": created_at,
        "items": items,
    }


ORDER_1 = order_read(
    1, 5, "delivered", 45.0, "2026-01-05T10:00:00", [line(1, 1, 2, 10.0), line(2, 5, 1, 25.0)]
)


def _order(oid: int, **equals: object) -> dict:
    return {"orders": {"where": {"id": oid}, "equals": equals}}


def _product(pid: int, **equals: object) -> dict:
    return {"products": {"where": {"id": pid}, "equals": equals}}


SCENARIOS = (
    Scenario(
        "place_order_decrements_stock",
        steps=(
            # carol buys 3 mugs (P1, 12.00 now) and 2 hammers (P5, 25.00):
            # total 3*12 + 2*25 = 36 + 50 = 86.0; stock P1 40-3 = 37, P5 15-2 = 13;
            # the new order is id 10 (9 seeded); each decrement bumps the version.
            Step(
                "POST",
                "/orders",
                "carol",
                json={
                    "items": [{"product_id": 1, "quantity": 3}, {"product_id": 5, "quantity": 2}]
                },
                expect_status=201,
                expect_db=_order(10, user_id=5, status="pending", total_amount=86.0),
            ),
            Step("GET", "/products/1", expect_body=product_read(1, stock_quantity=37, version=1)),
            Step(
                "GET",
                "/products/5",
                expect_body=product_read(5, stock_quantity=13, version=1),
                expect_db={
                    "order_items": {
                        "where": {"order_id": 10, "product_id": 1},
                        "equals": {"quantity": 3, "price_at_purchase": 12.0},
                    }
                },
            ),
        ),
    ),
    Scenario(
        "order_out_of_stock_is_409_and_writes_nothing",
        steps=(
            # the tape measure (P7) has 0 in stock
            Step(
                "POST",
                "/orders",
                "carol",
                json={"items": [{"product_id": 7, "quantity": 1}]},
                expect_status=409,
                expect_body={"detail": "Insufficient stock for product 7"},
                expect_db={"orders": {"count": 9}, "order_items": {"count": 14}},
            ),
        ),
    ),
    Scenario(
        "only_buyers_place_orders",
        steps=(
            Step(
                "POST",
                "/orders",
                "vicky",
                json={"items": [{"product_id": 1, "quantity": 1}]},
                expect_status=403,
                expect_body=DENIED,
                expect_db={"orders": {"count": 9}},
            ),
            Step(
                "POST",
                "/orders",
                "admin",
                json={"items": [{"product_id": 1, "quantity": 1}]},
                expect_status=403,
                expect_body=DENIED,
            ),
            Step(
                "POST",
                "/orders",
                "carol",
                json={"items": [{"product_id": 1, "quantity": 0}]},
                expect_status=422,
                expect_db={"orders": {"count": 9}},
            ),
            Step("POST", "/orders", "carol", json={"items": []}, expect_status=422),
        ),
    ),
    Scenario(
        "another_buyers_order_is_403",
        steps=(
            # order 1 is carol's: dave gets 403 (not 404), an unknown id is 404,
            # carol and the admin get the order
            Step("GET", "/orders/1", "dave", expect_status=403, expect_body=NO_ACCESS),
            Step("GET", "/orders/999", "dave", expect_status=404, expect_body=ORDER_NOT_FOUND),
            Step("GET", "/orders/1", "carol", expect_body=ORDER_1),
            Step("GET", "/orders/1", "admin", expect_body=ORDER_1),
            Step("GET", "/orders/1", None, expect_status=401),
        ),
    ),
    Scenario(
        "illegal_status_transition_is_400",
        steps=(
            # order 1 is delivered: no edge leaves it
            Step(
                "PATCH",
                "/orders/1/status",
                "vicky",
                json={"status": "pending"},
                expect_status=400,
                expect_body={"detail": "Cannot transition order from delivered to pending"},
                expect_db=_order(1, status="delivered"),
            ),
            # order 3 is shipped: shipped -> cancelled is not an edge
            Step(
                "PATCH",
                "/orders/3/status",
                "admin",
                json={"status": "cancelled"},
                expect_status=400,
                expect_body={"detail": "Cannot transition order from shipped to cancelled"},
                expect_db=_order(3, status="shipped"),
            ),
        ),
    ),
    Scenario(
        "order_moves_along_the_machine",
        steps=(
            # order 7 (dave, pending): pending -> confirmed -> shipped -> delivered
            Step(
                "PATCH",
                "/orders/7/status",
                "admin",
                json={"status": "confirmed"},
                expect_body=order_read(
                    7, 6, "confirmed", 12.0, "2026-03-10T09:00:00", [line(11, 1, 1, 12.0)]
                ),
            ),
            Step(
                "PATCH",
                "/orders/7/status",
                "victor",
                json={"status": "shipped"},
                expect_db=_order(7, status="shipped"),
            ),
            Step(
                "PATCH",
                "/orders/7/status",
                "admin",
                json={"status": "delivered"},
                expect_db=_order(7, status="delivered"),
            ),
            # buyers may not move orders
            Step(
                "PATCH",
                "/orders/8/status",
                "carol",
                json={"status": "cancelled"},
                expect_status=403,
                expect_body=DENIED,
                expect_db=_order(8, status="pending"),
            ),
            Step(
                "PATCH",
                "/orders/999/status",
                "admin",
                json={"status": "confirmed"},
                expect_status=404,
                expect_body=ORDER_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        "flash_sale_purchase",
        steps=(
            # dave buys 5 work gloves (P8, 6.00, stock 50): total 5*6 = 30.0, stock 45
            Step(
                "POST",
                "/orders/flash-sale-purchase",
                "dave",
                json={"product_id": 8, "quantity": 5},
                expect_status=201,
                expect_db={
                    **_order(10, user_id=6, status="pending", total_amount=30.0),
                    **_product(8, stock_quantity=45),
                },
            ),
            Step(
                "POST",
                "/orders/flash-sale-purchase",
                "dave",
                json={"product_id": 7, "quantity": 1},
                expect_status=409,
                expect_body={"detail": "Insufficient stock for product 7"},
                expect_db={"orders": {"count": 10}},
            ),
        ),
    ),
    Scenario(
        "vendors_change_only_their_own_products",
        steps=(
            Step(
                "PATCH",
                "/products/1",
                "victor",
                json={"price": 1.0},
                expect_status=403,
                expect_body={"detail": "You do not own this product"},
                expect_db=_product(1, price=12.0),
            ),
            Step(
                "PATCH",
                "/products/999",
                "victor",
                json={"price": 1.0},
                expect_status=404,
                expect_body={"detail": "Product not found"},
            ),
            Step(
                "PATCH",
                "/products/1",
                "carol",
                json={"price": 1.0},
                expect_status=403,
                expect_body=DENIED,
            ),
            Step(
                "PATCH",
                "/products/1",
                "vicky",
                json={"price": 0},
                expect_status=422,
                expect_db=_product(1, price=12.0),
            ),
            Step(
                "PATCH",
                "/products/1",
                "vicky",
                json={"price": 13.5},
                expect_body=product_read(1, price=13.5),
            ),
        ),
    ),
    Scenario(
        "catalogue_is_public",
        steps=(
            # active tools, by id: P5, P6, P7, P8
            Step(
                "GET",
                "/products",
                params={"category": "tools"},
                expect_body=[product_read(p) for p in (5, 6, 7, 8)],
            ),
            # the inactive poster is not listed, but is readable by id
            Step("GET", "/products", params={"category": "decor"}, expect_body=[]),
            Step("GET", "/products/4", expect_body=product_read(4)),
            # active, 8.50 <= price <= 18.00: P1 12.0, P2 8.5, P6 18.0, P7 9.99 (by id)
            Step(
                "GET",
                "/products",
                params={"min_price": 8.5, "max_price": 18},
                expect_body=[product_read(p) for p in (1, 2, 6, 7)],
            ),
            Step(
                "GET",
                "/products/999",
                expect_status=404,
                expect_body={"detail": "Product not found"},
            ),
        ),
    ),
    Scenario(
        "register_as_vendor",
        steps=(
            # frank (user 8, a buyer) opens shop 4 and becomes a vendor
            Step(
                "POST",
                "/vendors",
                "frank",
                json={"business_name": "Frank's Fences"},
                expect_status=201,
                expect_body={
                    "id": 4,
                    "user_id": 8,
                    "business_name": "Frank's Fences",
                    "is_verified": False,
                },
                expect_db={"users": {"where": {"id": 8}, "equals": {"role": "vendor"}}},
            ),
            Step(
                "POST",
                "/vendors",
                "frank",
                json={"business_name": "Again"},
                expect_status=400,
                expect_body={"detail": "This user is already registered as a vendor"},
                expect_db={"vendors": {"count": 4}},
            ),
            Step("GET", "/vendors/me", "carol", expect_status=404, expect_body=NO_VENDOR),
        ),
    ),
)

# ---- unchecked requirements ------------------------------------------------------------

UNCHECKED = (
    Unchecked(
        "POST /auth/login takes an OAuth2 form, answers 401 'Incorrect email or password' "
        "or 'Account is inactive', and issues a JWT whose 'sub' is the user id: Lattice "
        "has no token-issuing endpoint, so login has no program. A token of a user made "
        "inactive later answers 403 'Inactive user' (profile: active_column); scenarios "
        "sign in through login, so they cannot reach that case.",
        ("User",),
    ),
    Unchecked(
        "NESTED: the order endpoints answer OrderRead with nested items (W1-B). "
        "list_own_orders and get_order have programs; place_order, update_order_status "
        "and flash_sale_purchase have scenarios only. place_order: the body is a list of "
        "lines (parameters are scalars) and each line is its own insert and stock "
        "decrement (no per-row loop of writes; update() of products joined to the lines "
        "is L841); its 409 detail names the product (error details are literals, L404).",
        ("Order", "OrderItem", "Product.stock_quantity"),
    ),
    Unchecked(
        "update_order_status: any vendor moves any buyer's order (UPDATE_ORDER_STATUS "
        "has no ownership), but Order is owned by its buyer and only an Admin-only "
        "endpoint may write rows the caller does not own (L802); its 400 detail 'Cannot "
        "transition order from <old> to <new>' is formatted from both states (error "
        "details are literals, L404). ORDER_MACHINE and the scenarios state it.",
        ("Order.status",),
    ),
    Unchecked(
        "flash_sale_purchase: its 409 detail 'Insufficient stock for product <id>' names "
        "the product (error details are literals, L404), and an unknown product_id is an "
        "unhandled ValueError upstream (500), which no program answers.",
        ("Order", "OrderItem", "Product.stock_quantity"),
    ),
    Unchecked(
        "GET /orders/me answers every order of the caller upstream; the program caps the "
        "list at 100 (a List endpoint returns bounded rows, L831).",
        ("list_own_orders",),
    ),
    Unchecked(
        "Products are publicly readable but only their vendor (Product.vendor_id -> "
        "Vendor.user_id) may change them; Lattice's owner_via also makes reads private, so "
        "the write rule is only in update_product's program, not in the profile.",
        ("Product", "Product.vendor_id", "update_product"),
    ),
    Unchecked(
        "An order line belongs to the buyer through its order and to a vendor through its "
        "product; Lattice's owner_via has one parent, so a vendor's view of other buyers' "
        "lines of their products is not expressible.",
        ("OrderItem",),
    ),
    Unchecked(
        "A user with a vendor profile has role vendor (POST /vendors sets it): a "
        "cross-table rule, not an allowed invariant form.",
        ("User.role", "Vendor"),
    ),
    Unchecked(
        "Bounds: Lattice's Float has no exclusive bound, so price > 0 is ge=0.000001 in the "
        "programs (the invariant price_positive states it exactly); strings and prices get "
        "upper bounds (255 characters, 1e9) that upstream does not have.",
        ("Product.price", "create_product", "update_product"),
    ),
    Unchecked(
        "Upstream behaviour kept: PATCH /orders/{id}/status lets any vendor move any "
        "order, including orders without their products.",
        ("Order.status",),
    ),
    Unchecked(
        "Upstream bug kept: POST /orders commits each line's stock decrement, so when a "
        "later line fails for stock the order row and earlier decrements stay committed "
        "(with total 0, breaking order_total). Scenarios avoid that path.",
        ("Order", "Product.stock_quantity"),
    ),
    Unchecked(
        "Optimistic concurrency: a stock decrement bumps Product.version and a concurrent "
        "change answers 409 '... please retry your order'; one client cannot exercise it.",
        ("Product.version",),
    ),
    Unchecked(
        "PATCH /users/me with another user's email: upstream answers 500 (unique index); "
        "PATCH /products/{id} with description null clears it upstream, the program "
        "refuses null; GET /products accepts a negative limit upstream (no lower bound).",
        ("update_own_profile", "update_product", "list_products"),
    ),
)

# The original app's known state against this spec (lattice.spec.Accepted): reported by
# the check, not failing it. Each entry is a real gap upstream has, stated by the spec.
BASELINE = (
    Accepted(
        "A:PY803:*",
        "upstream request models have no length or upper bounds; the spec's programs add "
        "them (255 characters, 72 for passwords: bcrypt's limit, 1e9 for prices)",
    ),
    Accepted("B:*:*above_max_len", "the same missing upper bounds, seen in the answers"),
    Accepted("B:GET /products:*limit below_min", "upstream accepts a negative limit"),
    Accepted(
        "A:PY807:POST /orders:*",
        "upstream POST /orders commits each stock decrement before a later line can fail "
        "(the UNCHECKED partial-write bug)",
    ),
)

SPEC = Spec(
    profile=PROFILE,
    programs=PROGRAMS,
    machines=(ORDER_MACHINE,),
    invariants=INVARIANTS,
    scenarios=SCENARIOS,
    unchecked=UNCHECKED,
    users=USERS,
)
