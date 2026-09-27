"""The API benchmark task suite (spec 2026-09-27 §5.2): 40 endpoint tasks.

Families (8 tasks each):

- ``ownership`` (``o01``-``o08``): customers act only on their own orders, addresses and
  reviews; another customer's row must behave exactly like a missing one (404), and
  must stay unchanged. The natural slip is fetching/updating by id without the owner
  filter (IDOR).
- ``validation`` (``v01``-``v08``): bounded numbers, enumerations and string lengths
  must be rejected with 422 *before* anything is written. The natural slip is typing
  a field as a plain ``int``/``str``.
- ``errors`` (``e01``-``e08``): not found is 404 (not a 500 from a missing row),
  business-rule violations are 409, a missing token is 401 and a wrong role is 403.
- ``efficiency`` (``f01``-``f08``): list endpoints that need related data per row must
  run a constant number of SQL statements (``max_queries`` counts the auth lookup), and
  every list is paginated with a bounded ``limit`` and a default page size.
- ``control`` (``c01``-``c08``): plain reads and writes with no special trap.

Every spec states the intended semantics in plain terms (so exactly one behaviour is
correct) and never names the trap. Each case runs against a fresh copy of the DB built
by ``bench.api_datasets.build_db`` (seed 21); the expected values below were read from
that DB. Hand-written solutions live in ``tests/fixtures/api/<id>/``: ``lattice.py``,
``fastapi_correct.py`` and, for trap tasks, ``fastapi_naive.py`` (passes every happy
case, fails at least one trap case).
"""

from __future__ import annotations

from bench.api_datasets import TOKENS, USER_IDS
from bench.api_model import ApiTask, HttpCase

TRAP_FAMILIES = ("ownership", "validation", "errors", "efficiency")
FAMILIES = (*TRAP_FAMILIES, "control")

ALICE = TOKENS["alice"]
BOB = TOKENS["bob"]
CAROL = TOKENS["carol"]
DAVE = TOKENS["dave"]
ADMIN = TOKENS["admin"]
ALICE_ID = USER_IDS["alice"]
DAVE_ID = USER_IDS["dave"]

# Budget for list endpoints that need related data per row: the auth lookup plus a
# couple of set-based queries, independent of the number of rows returned.
QUERY_BUDGET = 3

_AUTH_CUSTOMER = (
    "Callers must be signed in (role Customer; Admin may call it too). "
    "Missing or unknown bearer token → 401."
)
_AUTH_ADMIN = "Admin only: missing or unknown bearer token → 401; a signed-in non-Admin → 403."
_AUTH_PUBLIC = "Public: no authentication required."


def _owned(noun: str) -> str:
    return (
        f"Customers can only see and act on their own {noun}s; another customer's {noun} "
        f"behaves exactly like a missing one (404) and is left unchanged. An Admin caller "
        f"may access any customer's {noun}s."
    )


def _page(order: str) -> str:
    return (
        "Paginated with query parameters `limit` (integer 1–100, default 20) and `offset` "
        "(integer ≥ 0, default 0); a value out of range → 422. "
        f"Items are ordered by {order}. "
        'Response 200: `{"items": [...], "limit": <limit>, "offset": <offset>}`.'
    )


_NO_WRITE = "Invalid input is rejected with 422 and nothing is written."


def _case(
    name: str,
    kind: str,
    family: str,
    method: str,
    path: str,
    token: str | None,
    status: int,
    **kwargs: object,
) -> HttpCase:
    return HttpCase(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        family=family,
        method=method,
        path=path,
        token=token,
        expect_status=status,
        **kwargs,  # type: ignore[arg-type]
    )


def _task(task_id: str, family: str, spec: str, cases: list[tuple]) -> ApiTask:
    built = []
    for name, kind, method, path, token, status, extra in cases:
        built.append(_case(name, kind, family, method, path, token, status, **extra))
    return ApiTask(task_id, family, " ".join(spec.split()), tuple(built))


def _ids(*ids: int) -> list[dict[str, int]]:
    return [{"id": i} for i in ids]


# ---------------------------------------------------------------------------
# ownership
# ---------------------------------------------------------------------------

_O = "ownership"

O01 = _task(
    "o01",
    _O,
    f"""
    GET /orders/{{order_id}} — fetch one order. {_AUTH_CUSTOMER} `order_id` is a positive
    integer path parameter. {_owned("order")} Response 200: a JSON object with exactly the
    fields `id`, `status`, `created_at` (ISO date string) and `total`. Unknown order → 404.
    """,
    [
        (
            "alice_own",
            "happy",
            "GET",
            "/orders/1",
            ALICE,
            200,
            {
                "expect_json": {
                    "id": 1,
                    "status": "pending",
                    "created_at": "2024-01-21",
                    "total": 1557.18,
                }
            },
        ),
        (
            "bob_own",
            "happy",
            "GET",
            "/orders/8",
            BOB,
            200,
            {
                "expect_json": {
                    "id": 8,
                    "status": "cancelled",
                    "created_at": "2024-04-11",
                    "total": 1228.9,
                }
            },
        ),
        ("admin_any", "happy", "GET", "/orders/8", ADMIN, 200, {"expect_json": {"id": 8}}),
        ("missing", "happy", "GET", "/orders/9999", ALICE, 404, {}),
        ("alice_reads_bobs", "trap", "GET", "/orders/8", ALICE, 404, {}),
        ("bob_reads_alices", "trap", "GET", "/orders/1", BOB, 404, {}),
    ],
)

O02 = _task(
    "o02",
    _O,
    f"""
    GET /addresses/{{address_id}} — fetch one saved address. {_AUTH_CUSTOMER} `address_id`
    is a positive integer path parameter. {_owned("address")} Response 200: a JSON object
    with exactly the fields `id`, `line1`, `city` and `zip` (text). Unknown address → 404.
    """,
    [
        (
            "alice_own",
            "happy",
            "GET",
            "/addresses/12",
            ALICE,
            200,
            {
                "expect_json": {
                    "id": 12,
                    "line1": "7924 Pine St",
                    "city": "Georgetown",
                    "zip": "04738",
                }
            },
        ),
        (
            "bob_own",
            "happy",
            "GET",
            "/addresses/9",
            BOB,
            200,
            {
                "expect_json": {
                    "id": 9,
                    "line1": "5741 Sunset St",
                    "city": "Riverton",
                    "zip": "07151",
                }
            },
        ),
        ("missing", "happy", "GET", "/addresses/999", ALICE, 404, {}),
        ("alice_reads_bobs", "trap", "GET", "/addresses/9", ALICE, 404, {}),
        ("dave_reads_alices", "trap", "GET", "/addresses/42", DAVE, 404, {}),
    ],
)

_ADDR_COUNT = "SELECT COUNT(*) FROM addresses WHERE id = {}"

O03 = _task(
    "o03",
    _O,
    f"""
    DELETE /addresses/{{address_id}} — delete a saved address. {_AUTH_CUSTOMER}
    `address_id` is a positive integer path parameter. {_owned("address")} Success → 204
    with an empty body. Unknown address → 404.
    """,
    [
        (
            "alice_deletes_own",
            "happy",
            "DELETE",
            "/addresses/12",
            ALICE,
            204,
            {"after_sql": _ADDR_COUNT.format(12), "after_expect": [0]},
        ),
        (
            "bob_deletes_own",
            "happy",
            "DELETE",
            "/addresses/23",
            BOB,
            204,
            {"after_sql": _ADDR_COUNT.format(23), "after_expect": [0]},
        ),
        ("missing", "happy", "DELETE", "/addresses/999", ALICE, 404, {}),
        (
            "alice_deletes_bobs",
            "trap",
            "DELETE",
            "/addresses/9",
            ALICE,
            404,
            {"after_sql": _ADDR_COUNT.format(9), "after_expect": [1]},
        ),
    ],
)

_REVIEW_STATE = "SELECT rating, body FROM reviews WHERE id = {}"

O04 = _task(
    "o04",
    _O,
    f"""
    PUT /reviews/{{review_id}} — rewrite one of the caller's product reviews.
    {_AUTH_CUSTOMER} `review_id` is a positive integer path parameter. JSON body:
    `rating` (integer 1–5) and `body` (string, 1–500 characters), both required; out of
    range → 422. {_owned("review")} Both fields are replaced. Response 200: a JSON object
    with exactly the fields `id`, `product_id`, `rating` and `body` (the new values).
    Unknown review → 404.
    """,
    [
        (
            "alice_updates_own",
            "happy",
            "PUT",
            "/reviews/111",
            ALICE,
            200,
            {
                "json": {"rating": 4, "body": "Better than I first thought."},
                "expect_json": {
                    "id": 111,
                    "product_id": 23,
                    "rating": 4,
                    "body": "Better than I first thought.",
                },
                "after_sql": _REVIEW_STATE.format(111),
                "after_expect": [[4, "Better than I first thought."]],
            },
        ),
        (
            "bob_updates_own",
            "happy",
            "PUT",
            "/reviews/8",
            BOB,
            200,
            {
                "json": {"rating": 2, "body": "Broke after a month."},
                "expect_json": {"id": 8, "rating": 2, "body": "Broke after a month."},
            },
        ),
        (
            "missing",
            "happy",
            "PUT",
            "/reviews/9999",
            ALICE,
            404,
            {"json": {"rating": 3, "body": "ok"}},
        ),
        (
            "alice_updates_bobs",
            "trap",
            "PUT",
            "/reviews/8",
            ALICE,
            404,
            {
                "json": {"rating": 1, "body": "Terrible."},
                "after_sql": _REVIEW_STATE.format(8),
                "after_expect": [[4, "Average product, nothing special."]],
            },
        ),
    ],
)

O05 = _task(
    "o05",
    _O,
    f"""
    GET /orders/{{order_id}}/items — list the line items of one order. {_AUTH_CUSTOMER}
    `order_id` is a positive integer path parameter. {_owned("order")} Unknown order → 404.
    {_page("item `id` ascending")} Each item is an object with exactly the fields `id`,
    `product_id`, `qty` and `unit_price`.
    """,
    [
        (
            "alice_own",
            "happy",
            "GET",
            "/orders/1/items",
            ALICE,
            200,
            {
                "expect_json": {
                    "items": [
                        {"id": 1, "product_id": 18, "qty": 4, "unit_price": 343.16},
                        {"id": 2, "product_id": 7, "qty": 1, "unit_price": 23.17},
                        {"id": 3, "product_id": 26, "qty": 3, "unit_price": 53.79},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "bob_own_paged",
            "happy",
            "GET",
            "/orders/8/items",
            BOB,
            200,
            {
                "query": {"limit": 2, "offset": 1},
                "expect_json": {"items": _ids(23, 24), "limit": 2, "offset": 1},
                "ordered": True,
            },
        ),
        ("missing", "happy", "GET", "/orders/9999/items", ALICE, 404, {}),
        ("alice_reads_bobs", "trap", "GET", "/orders/8/items", ALICE, 404, {}),
    ],
)

_ORDER_STATUS = "SELECT status FROM orders WHERE id = {}"

O06 = _task(
    "o06",
    _O,
    f"""
    POST /orders/{{order_id}}/cancel — cancel an order. {_AUTH_CUSTOMER} `order_id` is a
    positive integer path parameter; no body. {_owned("order")} Only a `pending` order
    can be cancelled: any other status → 409 and the order is unchanged. On success the
    order's status becomes `cancelled`. Response 200: a JSON object with exactly the
    fields `id` and `status`. Unknown order → 404.
    """,
    [
        (
            "alice_cancels_pending",
            "happy",
            "POST",
            "/orders/1/cancel",
            ALICE,
            200,
            {
                "expect_json": {"id": 1, "status": "cancelled"},
                "after_sql": _ORDER_STATUS.format(1),
                "after_expect": ["cancelled"],
            },
        ),
        (
            "bob_cancels_pending",
            "happy",
            "POST",
            "/orders/9/cancel",
            BOB,
            200,
            {"expect_json": {"id": 9, "status": "cancelled"}},
        ),
        (
            "not_pending",
            "happy",
            "POST",
            "/orders/2/cancel",
            ALICE,
            409,
            {"after_sql": _ORDER_STATUS.format(2), "after_expect": ["paid"]},
        ),
        ("missing", "happy", "POST", "/orders/9999/cancel", ALICE, 404, {}),
        (
            "alice_cancels_bobs",
            "trap",
            "POST",
            "/orders/9/cancel",
            ALICE,
            404,
            {"after_sql": _ORDER_STATUS.format(9), "after_expect": ["pending"]},
        ),
    ],
)

_ADDR_STATE = "SELECT line1, city, zip FROM addresses WHERE id = {}"

O07 = _task(
    "o07",
    _O,
    f"""
    PUT /addresses/{{address_id}} — replace a saved address. {_AUTH_CUSTOMER}
    `address_id` is a positive integer path parameter. JSON body, all required: `line1`
    (string, 1–100 characters), `city` (string, 1–60 characters), `zip` (string of
    exactly 5 characters); out of range → 422. {_owned("address")} Response 200: a JSON
    object with exactly the fields `id`, `line1`, `city` and `zip` (the new values).
    Unknown address → 404.
    """,
    [
        (
            "alice_updates_own",
            "happy",
            "PUT",
            "/addresses/12",
            ALICE,
            200,
            {
                "json": {"line1": "1 Harbor Rd", "city": "Portland", "zip": "04101"},
                "expect_json": {
                    "id": 12,
                    "line1": "1 Harbor Rd",
                    "city": "Portland",
                    "zip": "04101",
                },
                "after_sql": _ADDR_STATE.format(12),
                "after_expect": [["1 Harbor Rd", "Portland", "04101"]],
            },
        ),
        (
            "bob_updates_own",
            "happy",
            "PUT",
            "/addresses/27",
            BOB,
            200,
            {
                "json": {"line1": "9 Elm Ct", "city": "Madison", "zip": "53703"},
                "expect_json": {"id": 27, "city": "Madison", "zip": "53703"},
            },
        ),
        (
            "missing",
            "happy",
            "PUT",
            "/addresses/999",
            ALICE,
            404,
            {"json": {"line1": "1 Main St", "city": "Salem", "zip": "97301"}},
        ),
        (
            "alice_updates_bobs",
            "trap",
            "PUT",
            "/addresses/9",
            ALICE,
            404,
            {
                "json": {"line1": "1 Main St", "city": "Salem", "zip": "97301"},
                "after_sql": _ADDR_STATE.format(9),
                "after_expect": [["5741 Sunset St", "Riverton", "07151"]],
            },
        ),
    ],
)

O08 = _task(
    "o08",
    _O,
    f"""
    GET /orders/{{order_id}}/units — how many units an order contains. {_AUTH_CUSTOMER}
    `order_id` is a positive integer path parameter. {_owned("order")} Response 200: a
    JSON object with exactly the fields `order_id` and `units` (the sum of `qty` over the
    order's items). Unknown order → 404.
    """,
    [
        (
            "alice_own",
            "happy",
            "GET",
            "/orders/1/units",
            ALICE,
            200,
            {"expect_json": {"order_id": 1, "units": 8}},
        ),
        (
            "bob_own",
            "happy",
            "GET",
            "/orders/8/units",
            BOB,
            200,
            {"expect_json": {"order_id": 8, "units": 7}},
        ),
        ("missing", "happy", "GET", "/orders/9999/units", ALICE, 404, {}),
        ("alice_reads_bobs", "trap", "GET", "/orders/8/units", ALICE, 404, {}),
    ],
)

# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

_V = "validation"
_REVIEW_COUNT = "SELECT COUNT(*) FROM reviews"

V01 = _task(
    "v01",
    _V,
    f"""
    POST /reviews — write a product review. {_AUTH_CUSTOMER} JSON body, all required:
    `product_id` (integer ≥ 1), `rating` (integer 1–5), `body` (string, 1–500 characters).
    {_NO_WRITE} Unknown product → 404. The review is recorded as written by the caller.
    Response 201: a JSON object with exactly the fields `id` (the new review's id),
    `product_id`, `customer_id` (the caller's id), `rating` and `body`.
    """,
    [
        (
            "alice_reviews",
            "happy",
            "POST",
            "/reviews",
            ALICE,
            201,
            {
                "json": {"product_id": 1, "rating": 5, "body": "Kept us dry all week."},
                "expect_json": {
                    "id": 121,
                    "product_id": 1,
                    "customer_id": ALICE_ID,
                    "rating": 5,
                    "body": "Kept us dry all week.",
                },
                "after_sql": _REVIEW_COUNT,
                "after_expect": [121],
            },
        ),
        (
            "dave_reviews_low",
            "happy",
            "POST",
            "/reviews",
            DAVE,
            201,
            {
                "json": {"product_id": 2, "rating": 1, "body": "Wobbly."},
                "expect_json": {"product_id": 2, "customer_id": DAVE_ID, "rating": 1},
            },
        ),
        (
            "unknown_product",
            "happy",
            "POST",
            "/reviews",
            ALICE,
            404,
            {"json": {"product_id": 999, "rating": 3, "body": "ok"}},
        ),
        (
            "rating_too_high",
            "trap",
            "POST",
            "/reviews",
            ALICE,
            422,
            {
                "json": {"product_id": 1, "rating": 6, "body": "Amazing"},
                "after_sql": _REVIEW_COUNT,
                "after_expect": [120],
            },
        ),
        (
            "rating_zero",
            "trap",
            "POST",
            "/reviews",
            ALICE,
            422,
            {
                "json": {"product_id": 1, "rating": 0, "body": "Awful"},
                "after_sql": _REVIEW_COUNT,
                "after_expect": [120],
            },
        ),
        (
            "empty_body",
            "trap",
            "POST",
            "/reviews",
            ALICE,
            422,
            {
                "json": {"product_id": 1, "rating": 3, "body": ""},
                "after_sql": _REVIEW_COUNT,
                "after_expect": [120],
            },
        ),
    ],
)

_ADDR_TOTAL = "SELECT COUNT(*) FROM addresses"

V02 = _task(
    "v02",
    _V,
    f"""
    POST /addresses — save a new address for the caller. {_AUTH_CUSTOMER} JSON body, all
    required: `line1` (string, 1–100 characters), `city` (string, 1–60 characters), `zip`
    (string of exactly 5 characters). {_NO_WRITE} The address belongs to the caller.
    Response 201: a JSON object with exactly the fields `id` (the new address's id),
    `customer_id` (the caller's id), `line1`, `city` and `zip`.
    """,
    [
        (
            "alice_adds",
            "happy",
            "POST",
            "/addresses",
            ALICE,
            201,
            {
                "json": {"line1": "12 Wharf St", "city": "Boston", "zip": "02110"},
                "expect_json": {
                    "id": 61,
                    "customer_id": ALICE_ID,
                    "line1": "12 Wharf St",
                    "city": "Boston",
                    "zip": "02110",
                },
                "after_sql": _ADDR_TOTAL,
                "after_expect": [61],
            },
        ),
        (
            "dave_adds",
            "happy",
            "POST",
            "/addresses",
            DAVE,
            201,
            {
                "json": {"line1": "3 Oak Ave", "city": "Austin", "zip": "73301"},
                "expect_json": {"customer_id": DAVE_ID, "zip": "73301"},
            },
        ),
        (
            "zip_too_short",
            "trap",
            "POST",
            "/addresses",
            ALICE,
            422,
            {
                "json": {"line1": "12 Wharf St", "city": "Boston", "zip": "211"},
                "after_sql": _ADDR_TOTAL,
                "after_expect": [60],
            },
        ),
        (
            "empty_city",
            "trap",
            "POST",
            "/addresses",
            ALICE,
            422,
            {
                "json": {"line1": "12 Wharf St", "city": "", "zip": "02110"},
                "after_sql": _ADDR_TOTAL,
                "after_expect": [60],
            },
        ),
        (
            "line1_too_long",
            "trap",
            "POST",
            "/addresses",
            ALICE,
            422,
            {
                "json": {"line1": "x" * 101, "city": "Boston", "zip": "02110"},
                "after_sql": _ADDR_TOTAL,
                "after_expect": [60],
            },
        ),
    ],
)

_PRODUCT_COUNT = "SELECT COUNT(*) FROM products"

V03 = _task(
    "v03",
    _V,
    f"""
    POST /products — add a product to the catalog. {_AUTH_ADMIN} JSON body, all required:
    `name` (string, 1–80 characters), `category` (one of `electronics`, `home`, `outdoor`,
    `office`, `kitchen`, `sports`), `price` (number 0.01–10000), `stock` (integer
    0–100000). {_NO_WRITE} New products are active. Response 201: a JSON object with
    exactly the fields `id` (the new product's id), `name`, `category`, `price` and
    `stock`.
    """,
    [
        (
            "admin_adds",
            "happy",
            "POST",
            "/products",
            ADMIN,
            201,
            {
                "json": {"name": "Nova Lamp", "category": "home", "price": 49.5, "stock": 12},
                "expect_json": {
                    "id": 31,
                    "name": "Nova Lamp",
                    "category": "home",
                    "price": 49.5,
                    "stock": 12,
                },
                "after_sql": "SELECT name, category, price, stock, active FROM products "
                "WHERE id = 31",
                "after_expect": [["Nova Lamp", "home", 49.5, 12, 1]],
            },
        ),
        (
            "customer_forbidden",
            "happy",
            "POST",
            "/products",
            ALICE,
            403,
            {
                "json": {"name": "Nova Lamp", "category": "home", "price": 49.5, "stock": 12},
                "after_sql": _PRODUCT_COUNT,
                "after_expect": [30],
            },
        ),
        (
            "unknown_category",
            "trap",
            "POST",
            "/products",
            ADMIN,
            422,
            {
                "json": {"name": "Yo-yo", "category": "toys", "price": 3.5, "stock": 10},
                "after_sql": _PRODUCT_COUNT,
                "after_expect": [30],
            },
        ),
        (
            "negative_price",
            "trap",
            "POST",
            "/products",
            ADMIN,
            422,
            {
                "json": {"name": "Nova Lamp", "category": "home", "price": -5.0, "stock": 12},
                "after_sql": _PRODUCT_COUNT,
                "after_expect": [30],
            },
        ),
        (
            "negative_stock",
            "trap",
            "POST",
            "/products",
            ADMIN,
            422,
            {
                "json": {"name": "Nova Lamp", "category": "home", "price": 49.5, "stock": -1},
                "after_sql": _PRODUCT_COUNT,
                "after_expect": [30],
            },
        ),
    ],
)

_PRICE = "SELECT price FROM products WHERE id = {}"

V04 = _task(
    "v04",
    _V,
    f"""
    PATCH /products/{{product_id}}/price — change a product's price. {_AUTH_ADMIN}
    `product_id` is a positive integer path parameter. JSON body: `price` (number
    0.01–10000, required). {_NO_WRITE} Unknown product → 404. Response 200: a JSON
    object with exactly the fields `id` and `price` (the new price).
    """,
    [
        (
            "reprice",
            "happy",
            "PATCH",
            "/products/1/price",
            ADMIN,
            200,
            {
                "json": {"price": 399.99},
                "expect_json": {"id": 1, "price": 399.99},
                "after_sql": _PRICE.format(1),
                "after_expect": [399.99],
            },
        ),
        (
            "reprice_cheap",
            "happy",
            "PATCH",
            "/products/7/price",
            ADMIN,
            200,
            {"json": {"price": 0.5}, "expect_json": {"id": 7, "price": 0.5}},
        ),
        ("missing", "happy", "PATCH", "/products/999/price", ADMIN, 404, {"json": {"price": 5.0}}),
        (
            "zero_price",
            "trap",
            "PATCH",
            "/products/1/price",
            ADMIN,
            422,
            {"json": {"price": 0.0}, "after_sql": _PRICE.format(1), "after_expect": [417.89]},
        ),
        (
            "negative_price",
            "trap",
            "PATCH",
            "/products/1/price",
            ADMIN,
            422,
            {"json": {"price": -3.0}, "after_sql": _PRICE.format(1), "after_expect": [417.89]},
        ),
        (
            "absurd_price",
            "trap",
            "PATCH",
            "/products/1/price",
            ADMIN,
            422,
            {
                "json": {"price": 25000.0},
                "after_sql": _PRICE.format(1),
                "after_expect": [417.89],
            },
        ),
    ],
)

V05 = _task(
    "v05",
    _V,
    f"""
    POST /orders/{{order_id}}/status — set an order's status. {_AUTH_ADMIN} `order_id` is
    a positive integer path parameter. JSON body: `status` (required; one of `pending`,
    `paid`, `shipped`, `cancelled`). {_NO_WRITE} Any order may be moved to any of these
    statuses. Unknown order → 404. Response 200: a JSON object with exactly the fields
    `id` and `status` (the new status).
    """,
    [
        (
            "ship",
            "happy",
            "POST",
            "/orders/1/status",
            ADMIN,
            200,
            {
                "json": {"status": "shipped"},
                "expect_json": {"id": 1, "status": "shipped"},
                "after_sql": _ORDER_STATUS.format(1),
                "after_expect": ["shipped"],
            },
        ),
        (
            "reopen",
            "happy",
            "POST",
            "/orders/4/status",
            ADMIN,
            200,
            {"json": {"status": "pending"}, "expect_json": {"id": 4, "status": "pending"}},
        ),
        (
            "missing",
            "happy",
            "POST",
            "/orders/9999/status",
            ADMIN,
            404,
            {"json": {"status": "paid"}},
        ),
        (
            "unknown_status",
            "trap",
            "POST",
            "/orders/1/status",
            ADMIN,
            422,
            {
                "json": {"status": "refunded"},
                "after_sql": _ORDER_STATUS.format(1),
                "after_expect": ["pending"],
            },
        ),
        (
            "wrong_case",
            "trap",
            "POST",
            "/orders/1/status",
            ADMIN,
            422,
            {
                "json": {"status": "Shipped"},
                "after_sql": _ORDER_STATUS.format(1),
                "after_expect": ["pending"],
            },
        ),
    ],
)

V06 = _task(
    "v06",
    _V,
    f"""
    GET /products — browse active products of one category. {_AUTH_PUBLIC} Query
    parameter `category` (required; one of `electronics`, `home`, `outdoor`, `office`,
    `kitchen`, `sports`; anything else → 422). Only products whose `active` flag is set
    are listed. {_page("product `id` ascending")} Each item is an object with exactly the
    fields `id`, `name` and `price`.
    """,
    [
        (
            "kitchen",
            "happy",
            "GET",
            "/products",
            None,
            200,
            {
                "query": {"category": "kitchen"},
                "expect_json": {
                    "items": [
                        {"id": 26, "name": "Slate Kettle", "price": 53.79},
                        {"id": 28, "name": "Ember Desk", "price": 408.27},
                        {"id": 30, "name": "Onyx Rack", "price": 473.79},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "outdoor_paged",
            "happy",
            "GET",
            "/products",
            None,
            200,
            {
                "query": {"category": "outdoor", "limit": 2, "offset": 1},
                "expect_json": {"items": _ids(16, 18), "limit": 2, "offset": 1},
                "ordered": True,
            },
        ),
        (
            "unknown_category",
            "trap",
            "GET",
            "/products",
            None,
            422,
            {"query": {"category": "toys"}},
        ),
        (
            "wrong_case",
            "trap",
            "GET",
            "/products",
            None,
            422,
            {"query": {"category": "Kitchen"}},
        ),
    ],
)

_USER_NAME = "SELECT name FROM users WHERE id = {}"

V07 = _task(
    "v07",
    _V,
    f"""
    PATCH /me/name — change the caller's display name. {_AUTH_CUSTOMER} JSON body: `name`
    (string, 1–60 characters, required). {_NO_WRITE} Response 200: a JSON object with
    exactly the fields `id`, `email` and `name` (the new name) of the caller.
    """,
    [
        (
            "alice_renames",
            "happy",
            "PATCH",
            "/me/name",
            ALICE,
            200,
            {
                "json": {"name": "Alice N."},
                "expect_json": {"id": ALICE_ID, "email": "alice@example.com", "name": "Alice N."},
                "after_sql": _USER_NAME.format(ALICE_ID),
                "after_expect": ["Alice N."],
            },
        ),
        (
            "dave_renames",
            "happy",
            "PATCH",
            "/me/name",
            DAVE,
            200,
            {
                "json": {"name": "D"},
                "expect_json": {"id": DAVE_ID, "name": "D"},
                "after_sql": _USER_NAME.format(DAVE_ID),
                "after_expect": ["D"],
            },
        ),
        (
            "empty_name",
            "trap",
            "PATCH",
            "/me/name",
            ALICE,
            422,
            {
                "json": {"name": ""},
                "after_sql": _USER_NAME.format(ALICE_ID),
                "after_expect": ["Alice Nguyen"],
            },
        ),
        (
            "name_too_long",
            "trap",
            "PATCH",
            "/me/name",
            ALICE,
            422,
            {
                "json": {"name": "A" * 61},
                "after_sql": _USER_NAME.format(ALICE_ID),
                "after_expect": ["Alice Nguyen"],
            },
        ),
    ],
)

_STOCK = "SELECT stock FROM products WHERE id = {}"

V08 = _task(
    "v08",
    _V,
    f"""
    POST /products/{{product_id}}/restock — add received units to a product's stock.
    {_AUTH_ADMIN} `product_id` is a positive integer path parameter. JSON body: `qty`
    (integer 1–1000, required). {_NO_WRITE} The product's `stock` increases by `qty`.
    Unknown product → 404. Response 200: a JSON object with exactly the fields `id` and
    `stock` (the new stock).
    """,
    [
        (
            "restock",
            "happy",
            "POST",
            "/products/1/restock",
            ADMIN,
            200,
            {
                "json": {"qty": 10},
                "expect_json": {"id": 1, "stock": 91},
                "after_sql": _STOCK.format(1),
                "after_expect": [91],
            },
        ),
        (
            "restock_empty",
            "happy",
            "POST",
            "/products/2/restock",
            ADMIN,
            200,
            {"json": {"qty": 1000}, "expect_json": {"id": 2, "stock": 1000}},
        ),
        ("missing", "happy", "POST", "/products/999/restock", ADMIN, 404, {"json": {"qty": 5}}),
        (
            "zero_qty",
            "trap",
            "POST",
            "/products/1/restock",
            ADMIN,
            422,
            {"json": {"qty": 0}, "after_sql": _STOCK.format(1), "after_expect": [81]},
        ),
        (
            "negative_qty",
            "trap",
            "POST",
            "/products/1/restock",
            ADMIN,
            422,
            {"json": {"qty": -10}, "after_sql": _STOCK.format(1), "after_expect": [81]},
        ),
    ],
)

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

_E = "errors"

E01 = _task(
    "e01",
    _E,
    f"""
    GET /products/{{product_id}} — product detail. {_AUTH_PUBLIC} `product_id` is a
    positive integer path parameter. Response 200: a JSON object with exactly the fields
    `id`, `name`, `category`, `price` and `stock`. Unknown product → 404.
    """,
    [
        (
            "tent",
            "happy",
            "GET",
            "/products/1",
            None,
            200,
            {
                "expect_json": {
                    "id": 1,
                    "name": "Terra Tent",
                    "category": "electronics",
                    "price": 417.89,
                    "stock": 81,
                }
            },
        ),
        (
            "chair",
            "happy",
            "GET",
            "/products/2",
            None,
            200,
            {"expect_json": {"id": 2, "name": "Cobalt Chair", "stock": 0}},
        ),
        ("missing", "trap", "GET", "/products/999", None, 404, {}),
        ("missing_just_past_end", "trap", "GET", "/products/31", None, 404, {}),
    ],
)

E02 = _task(
    "e02",
    _E,
    f"""
    POST /orders/{{order_id}}/pay — mark an order as paid. {_AUTH_CUSTOMER} `order_id` is a
    positive integer path parameter; no body. {_owned("order")} Only a `pending` order can
    be paid: any other status → 409 and the order is unchanged. On success the status
    becomes `paid`. Response 200: a JSON object with exactly the fields `id` and `status`.
    Unknown order → 404.
    """,
    [
        (
            "alice_pays",
            "happy",
            "POST",
            "/orders/1/pay",
            ALICE,
            200,
            {
                "expect_json": {"id": 1, "status": "paid"},
                "after_sql": _ORDER_STATUS.format(1),
                "after_expect": ["paid"],
            },
        ),
        (
            "bob_pays",
            "happy",
            "POST",
            "/orders/9/pay",
            BOB,
            200,
            {"expect_json": {"id": 9, "status": "paid"}},
        ),
        ("not_mine", "happy", "POST", "/orders/9/pay", ALICE, 404, {}),
        ("missing", "happy", "POST", "/orders/9999/pay", ALICE, 404, {}),
        (
            "already_paid",
            "trap",
            "POST",
            "/orders/2/pay",
            ALICE,
            409,
            {"after_sql": _ORDER_STATUS.format(2), "after_expect": ["paid"]},
        ),
        (
            "cancelled",
            "trap",
            "POST",
            "/orders/4/pay",
            ALICE,
            409,
            {"after_sql": _ORDER_STATUS.format(4), "after_expect": ["cancelled"]},
        ),
    ],
)

_PRODUCT_EXISTS = "SELECT COUNT(*) FROM products WHERE id = {}"

E03 = _task(
    "e03",
    _E,
    f"""
    DELETE /products/{{product_id}} — remove a product from the catalog. {_AUTH_ADMIN}
    `product_id` is a positive integer path parameter. Success → 204 with an empty body.
    Unknown product → 404.
    """,
    [
        (
            "admin_deletes",
            "happy",
            "DELETE",
            "/products/3",
            ADMIN,
            204,
            {"after_sql": _PRODUCT_EXISTS.format(3), "after_expect": [0]},
        ),
        ("missing", "happy", "DELETE", "/products/999", ADMIN, 404, {}),
        (
            "no_token",
            "happy",
            "DELETE",
            "/products/3",
            None,
            401,
            {"after_sql": _PRODUCT_EXISTS.format(3), "after_expect": [1]},
        ),
        (
            "customer_deletes",
            "trap",
            "DELETE",
            "/products/3",
            ALICE,
            403,
            {"after_sql": _PRODUCT_EXISTS.format(3), "after_expect": [1]},
        ),
    ],
)

E04 = _task(
    "e04",
    _E,
    f"""
    GET /products/{{product_id}}/stock — current stock level of a product, for signed-in
    users only. {_AUTH_CUSTOMER} `product_id` is a positive integer path parameter.
    Response 200: a JSON object with exactly the fields `id`, `name` and `stock`. Unknown
    product → 404.
    """,
    [
        (
            "alice_checks",
            "happy",
            "GET",
            "/products/1/stock",
            ALICE,
            200,
            {"expect_json": {"id": 1, "name": "Terra Tent", "stock": 81}},
        ),
        (
            "admin_checks",
            "happy",
            "GET",
            "/products/2/stock",
            ADMIN,
            200,
            {"expect_json": {"id": 2, "stock": 0}},
        ),
        ("missing", "happy", "GET", "/products/999/stock", ALICE, 404, {}),
        ("no_token", "trap", "GET", "/products/1/stock", None, 401, {}),
        ("unknown_token", "trap", "GET", "/products/1/stock", "tok-nobody", 401, {}),
    ],
)

E05 = _task(
    "e05",
    _E,
    f"""
    POST /orders/{{order_id}}/ship — mark an order as shipped. {_AUTH_ADMIN} `order_id` is
    a positive integer path parameter; no body. Only a `paid` order can be shipped: any
    other status → 409 and the order is unchanged. On success the status becomes
    `shipped`. Response 200: a JSON object with exactly the fields `id` and `status`.
    Unknown order → 404.
    """,
    [
        (
            "ship_paid",
            "happy",
            "POST",
            "/orders/2/ship",
            ADMIN,
            200,
            {
                "expect_json": {"id": 2, "status": "shipped"},
                "after_sql": _ORDER_STATUS.format(2),
                "after_expect": ["shipped"],
            },
        ),
        (
            "not_paid",
            "happy",
            "POST",
            "/orders/1/ship",
            ADMIN,
            409,
            {"after_sql": _ORDER_STATUS.format(1), "after_expect": ["pending"]},
        ),
        ("customer_forbidden", "happy", "POST", "/orders/2/ship", ALICE, 403, {}),
        ("missing", "trap", "POST", "/orders/9999/ship", ADMIN, 404, {}),
        ("missing_past_end", "trap", "POST", "/orders/301/ship", ADMIN, 404, {}),
    ],
)

E06 = _task(
    "e06",
    _E,
    f"""
    POST /products/{{product_id}}/my-review — review a product. {_AUTH_CUSTOMER}
    `product_id` is a positive integer path parameter. JSON body, all required: `rating`
    (integer 1–5) and `body` (string, 1–500 characters); out of range → 422. Unknown
    product → 404. A customer may review each product at most once: if the caller has
    already reviewed this product → 409 and nothing is written. The review is recorded as
    written by the caller. Response 201: a JSON object with exactly the fields `id` (the
    new review's id), `product_id`, `customer_id` (the caller's id), `rating` and `body`.
    """,
    [
        (
            "alice_first_review",
            "happy",
            "POST",
            "/products/1/my-review",
            ALICE,
            201,
            {
                "json": {"rating": 4, "body": "Roomy."},
                "expect_json": {
                    "id": 121,
                    "product_id": 1,
                    "customer_id": ALICE_ID,
                    "rating": 4,
                    "body": "Roomy.",
                },
                "after_sql": _REVIEW_COUNT,
                "after_expect": [121],
            },
        ),
        (
            "dave_reviews_product_alice_reviewed",
            "happy",
            "POST",
            "/products/23/my-review",
            DAVE,
            201,
            {
                "json": {"rating": 3, "body": "Fine."},
                "expect_json": {"product_id": 23, "customer_id": DAVE_ID, "rating": 3},
            },
        ),
        (
            "unknown_product",
            "happy",
            "POST",
            "/products/999/my-review",
            ALICE,
            404,
            {"json": {"rating": 4, "body": "?"}},
        ),
        (
            "alice_reviews_again",
            "trap",
            "POST",
            "/products/23/my-review",
            ALICE,
            409,
            {
                "json": {"rating": 5, "body": "Changed my mind."},
                "after_sql": _REVIEW_COUNT,
                "after_expect": [120],
            },
        ),
    ],
)

E07 = _task(
    "e07",
    _E,
    f"""
    POST /products/{{product_id}}/reserve — reserve units of a product for the caller.
    {_AUTH_CUSTOMER} `product_id` is a positive integer path parameter. JSON body: `qty`
    (integer 1–100, required); out of range → 422. Unknown product → 404. If the
    product's `stock` is less than `qty` → 409 and the stock is unchanged; otherwise
    `stock` decreases by `qty`. Response 200: a JSON object with exactly the fields `id`
    and `stock` (the new stock).
    """,
    [
        (
            "reserve",
            "happy",
            "POST",
            "/products/1/reserve",
            ALICE,
            200,
            {
                "json": {"qty": 5},
                "expect_json": {"id": 1, "stock": 76},
                "after_sql": _STOCK.format(1),
                "after_expect": [76],
            },
        ),
        (
            "reserve_all",
            "happy",
            "POST",
            "/products/16/reserve",
            BOB,
            200,
            {
                "json": {"qty": 5},
                "expect_json": {"id": 16, "stock": 0},
                "after_sql": _STOCK.format(16),
                "after_expect": [0],
            },
        ),
        ("missing", "happy", "POST", "/products/999/reserve", ALICE, 404, {"json": {"qty": 1}}),
        (
            "out_of_stock",
            "trap",
            "POST",
            "/products/2/reserve",
            ALICE,
            409,
            {"json": {"qty": 1}, "after_sql": _STOCK.format(2), "after_expect": [0]},
        ),
        (
            "not_enough",
            "trap",
            "POST",
            "/products/8/reserve",
            ALICE,
            409,
            {"json": {"qty": 10}, "after_sql": _STOCK.format(8), "after_expect": [7]},
        ),
    ],
)

E08 = _task(
    "e08",
    _E,
    f"""
    GET /admin/customers/{{user_id}}/orders — list any customer's orders for support
    staff. {_AUTH_ADMIN} `user_id` is a positive integer path parameter; a user with no
    orders (or no such user) gives an empty list. {_page("order `id` ascending")} Each
    item is an object with exactly the fields `id`, `status`, `created_at` (ISO date
    string) and `total`.
    """,
    [
        (
            "alice_orders",
            "happy",
            "GET",
            "/admin/customers/1/orders",
            ADMIN,
            200,
            {
                "expect_json": {
                    "items": [
                        {"id": 1, "status": "pending", "created_at": "2024-01-21"},
                        {"id": 2, "status": "paid", "created_at": "2024-10-27"},
                        {"id": 3, "status": "shipped", "created_at": "2024-03-22"},
                        {"id": 4, "status": "cancelled", "created_at": "2024-05-31"},
                        {"id": 5, "status": "shipped", "created_at": "2024-09-04"},
                        {"id": 6, "status": "paid", "created_at": "2024-08-24"},
                        {"id": 7, "status": "cancelled", "created_at": "2024-02-23"},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "dave_has_none",
            "happy",
            "GET",
            "/admin/customers/4/orders",
            ADMIN,
            200,
            {"expect_json": {"items": [], "limit": 20, "offset": 0}},
        ),
        ("no_token", "happy", "GET", "/admin/customers/1/orders", None, 401, {}),
        ("customer_forbidden", "trap", "GET", "/admin/customers/2/orders", ALICE, 403, {}),
        ("customer_own_forbidden", "trap", "GET", "/admin/customers/1/orders", ALICE, 403, {}),
    ],
)

# ---------------------------------------------------------------------------
# efficiency
# ---------------------------------------------------------------------------

_F = "efficiency"

_BOB_UNITS = {8: 7, 9: 10, 10: 8, 11: 4, 12: 11, 13: 14, 14: 10, 15: 9, 16: 11, 17: 9, 18: 8}

F01 = _task(
    "f01",
    _F,
    f"""
    GET /orders — the caller's orders, each with its number of units. {_AUTH_CUSTOMER}
    Customers see only their own orders; an Admin caller sees every customer's orders.
    {_page("order `id` ascending")} Each item is an object with exactly the fields `id`,
    `status`, `created_at` (ISO date string), `total` and `units` (the sum of `qty` over
    the order's items).
    """,
    [
        (
            "bob_all",
            "happy",
            "GET",
            "/orders",
            BOB,
            200,
            {
                "expect_json": {
                    "items": [{"id": k, "units": v} for k, v in _BOB_UNITS.items()],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "bob_paged",
            "happy",
            "GET",
            "/orders",
            BOB,
            200,
            {
                "query": {"limit": 3, "offset": 2},
                "expect_json": {
                    "items": [
                        {"id": 10, "status": "paid", "total": 1439.15, "units": 8},
                        {"id": 11, "status": "cancelled", "total": 870.27, "units": 4},
                        {"id": 12, "status": "pending", "total": 3053.21, "units": 11},
                    ],
                    "limit": 3,
                    "offset": 2,
                },
                "ordered": True,
            },
        ),
        (
            "dave_none",
            "happy",
            "GET",
            "/orders",
            DAVE,
            200,
            {"expect_json": {"items": [], "limit": 20, "offset": 0}},
        ),
        (
            "bob_all_budget",
            "trap",
            "GET",
            "/orders",
            BOB,
            200,
            {
                "expect_json": {"items": _ids(*_BOB_UNITS)},
                "max_queries": QUERY_BUDGET,
            },
        ),
    ],
)


def _order_names(first: int, last: int) -> list[dict[str, object]]:
    names = {range(1, 8): "Alice Nguyen", range(8, 19): "Bob Martinez"}
    out: list[dict[str, object]] = []
    for oid in range(first, last + 1):
        name = next((n for r, n in names.items() if oid in r), "Carol Kim")
        out.append({"id": oid, "customer_name": name})
    return out


F02 = _task(
    "f02",
    _F,
    f"""
    GET /admin/orders — all orders with the name of the customer who placed each.
    {_AUTH_ADMIN} {_page("order `id` ascending")} Each item is an object with exactly the
    fields `id`, `customer_id`, `customer_name` (that user's `name`), `status` and `total`.
    """,
    [
        (
            "first_page",
            "happy",
            "GET",
            "/admin/orders",
            ADMIN,
            200,
            {
                "expect_json": {"items": _order_names(1, 20), "limit": 20, "offset": 0},
                "ordered": True,
            },
        ),
        (
            "last_page",
            "happy",
            "GET",
            "/admin/orders",
            ADMIN,
            200,
            {
                "query": {"limit": 10, "offset": 295},
                "expect_json": {
                    "items": [
                        {"id": i, "customer_id": 40, "customer_name": "Elle Weber"}
                        for i in range(296, 301)
                    ],
                    "limit": 10,
                    "offset": 295,
                },
                "ordered": True,
            },
        ),
        ("customer_forbidden", "happy", "GET", "/admin/orders", ALICE, 403, {}),
        (
            "page_budget",
            "trap",
            "GET",
            "/admin/orders",
            ADMIN,
            200,
            {
                "query": {"limit": 50},
                "expect_json": {"limit": 50, "offset": 0},
                "max_queries": QUERY_BUDGET,
            },
        ),
    ],
)

F03 = _task(
    "f03",
    _F,
    f"""
    GET /catalog — the whole product catalog, active or not. {_AUTH_PUBLIC}
    {_page("product `id` ascending")} Each item is an object with exactly the fields
    `id`, `name`, `category`, `price` and `stock`.
    """,
    [
        (
            "first_page",
            "happy",
            "GET",
            "/catalog",
            None,
            200,
            {
                "query": {"limit": 20},
                "expect_json": {"items": _ids(*range(1, 21)), "limit": 20, "offset": 0},
                "ordered": True,
            },
        ),
        (
            "tail",
            "happy",
            "GET",
            "/catalog",
            None,
            200,
            {
                "query": {"limit": 5, "offset": 28},
                "expect_json": {
                    "items": [
                        {
                            "id": 29,
                            "name": "Arc Shelf",
                            "category": "electronics",
                            "price": 434.65,
                            "stock": 37,
                        },
                        {
                            "id": 30,
                            "name": "Onyx Rack",
                            "category": "kitchen",
                            "price": 473.79,
                            "stock": 226,
                        },
                    ],
                    "limit": 5,
                    "offset": 28,
                },
                "ordered": True,
            },
        ),
        (
            "default_page_size",
            "trap",
            "GET",
            "/catalog",
            None,
            200,
            {"expect_json": {"items": _ids(*range(1, 21)), "limit": 20, "offset": 0}},
        ),
        ("limit_too_large", "trap", "GET", "/catalog", None, 422, {"query": {"limit": 1000}}),
        ("limit_zero", "trap", "GET", "/catalog", None, 422, {"query": {"limit": 0}}),
    ],
)

_REVIEWS_HEAD = [
    (1, "Dave Okafor", "Slate Pan"),
    (2, "Elle Weber", "Arc Shelf"),
    (3, "Nora Haddad", "Halo Fan"),
    (4, "Ivy Novak", "Slate Kettle"),
    (5, "Ethan Haddad", "Slate Pan"),
    (6, "Mia Nguyen", "Arc Mat"),
    (7, "Jude Ivanov", "Halo Fan"),
    (8, "Bob Martinez", "Amber Lamp"),
    (9, "Jude Garcia", "Onyx Rack"),
    (10, "Max Diaz", "Arc Mat"),
    (11, "Nora Haddad", "Onyx Rack"),
    (12, "Elle Costa", "Crest Mat"),
    (13, "Ruby Ali", "Arc Rack"),
    (14, "Max Moreau", "Vivid Chair"),
    (15, "Liam Weber", "Arc Mat"),
    (16, "Sara Okafor", "Amber Monitor"),
    (17, "Ivy Chen", "Cobalt Lamp"),
    (18, "Finn Patel", "Terra Jacket"),
    (19, "Nora Haddad", "Arc Rack"),
    (20, "Ivy Novak", "Amber Lamp"),
]

F04 = _task(
    "f04",
    _F,
    f"""
    GET /admin/reviews — moderation queue: every review with the reviewer's and the
    product's name. {_AUTH_ADMIN} {_page("review `id` ascending")} Each item is an object
    with exactly the fields `id`, `rating`, `body`, `reviewer_name` (the `name` of the
    user who wrote it) and `product_name` (the reviewed product's `name`).
    """,
    [
        (
            "first_page",
            "happy",
            "GET",
            "/admin/reviews",
            ADMIN,
            200,
            {
                "expect_json": {
                    "items": [
                        {"id": i, "reviewer_name": r, "product_name": p}
                        for i, r, p in _REVIEWS_HEAD
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "tail",
            "happy",
            "GET",
            "/admin/reviews",
            ADMIN,
            200,
            {
                "query": {"limit": 5, "offset": 118},
                "expect_json": {
                    "items": [
                        {
                            "id": 119,
                            "rating": 1,
                            "body": "Works great, exactly as described.",
                            "reviewer_name": "Alice Nguyen",
                            "product_name": "Arc Shelf",
                        },
                        {
                            "id": 120,
                            "rating": 2,
                            "body": "Stopped working after a week.",
                            "reviewer_name": "Elle Costa",
                            "product_name": "Solid Cooler",
                        },
                    ],
                    "limit": 5,
                    "offset": 118,
                },
                "ordered": True,
            },
        ),
        ("customer_forbidden", "happy", "GET", "/admin/reviews", BOB, 403, {}),
        (
            "page_budget",
            "trap",
            "GET",
            "/admin/reviews",
            ADMIN,
            200,
            {
                "expect_json": {"items": _ids(*range(1, 21))},
                "max_queries": QUERY_BUDGET,
            },
        ),
    ],
)

F05 = _task(
    "f05",
    _F,
    f"""
    GET /products/ratings — every product with its review statistics. {_AUTH_PUBLIC}
    {_page("product `id` ascending")} Each item is an object with exactly the fields
    `id`, `name`, `review_count` (number of reviews of the product; 0 if none) and
    `avg_rating` (mean `rating` of those reviews; null if none).
    """,
    [
        (
            "first_page",
            "happy",
            "GET",
            "/products/ratings",
            None,
            200,
            {
                "query": {"limit": 3},
                "expect_json": {
                    "items": [
                        {"id": 1, "name": "Terra Tent", "review_count": 2, "avg_rating": 4.0},
                        {"id": 2, "name": "Cobalt Chair", "review_count": 5, "avg_rating": 3.0},
                        {
                            "id": 3,
                            "name": "Amber Monitor",
                            "review_count": 6,
                            "avg_rating": 3.1666666666666665,
                        },
                    ],
                    "limit": 3,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "tail",
            "happy",
            "GET",
            "/products/ratings",
            None,
            200,
            {
                "query": {"offset": 27},
                "expect_json": {
                    "items": [
                        {"id": 28, "review_count": 3, "avg_rating": 2.3333333333333335},
                        {"id": 29, "review_count": 4, "avg_rating": 2.75},
                        {"id": 30, "review_count": 5, "avg_rating": 2.6},
                    ],
                    "limit": 20,
                    "offset": 27,
                },
                "ordered": True,
            },
        ),
        (
            "page_budget",
            "trap",
            "GET",
            "/products/ratings",
            None,
            200,
            {
                "expect_json": {"items": _ids(*range(1, 21))},
                "max_queries": QUERY_BUDGET,
            },
        ),
    ],
)

_CUSTOMER_STATS = [
    (1, "Alice Nguyen", 5, 9937.21),
    (2, "Bob Martinez", 8, 18692.36),
    (3, "Carol Kim", 7, 17831.26),
    (4, "Dave Okafor", 0, 0.0),
    (8, "Ethan Haddad", 6, 15620.19),
    (9, "Ethan Suzuki", 6, 15085.27),
    (10, "Finn Suzuki", 0, 0.0),
    (11, "Mia Nguyen", 8, 16427.55),
    (12, "Ivy Chen", 6, 19860.33),
    (13, "Max Moreau", 3, 8888.07),
    (14, "Jude Garcia", 3, 7532.69),
    (15, "Ruby Nguyen", 5, 10395.53),
    (16, "Kai Patel", 9, 20308.26),
    (17, "Jude Ivanov", 8, 18818.91),
    (18, "Noah Diaz", 11, 26383.23),
    (19, "Leo Okafor", 12, 34815.57),
    (20, "Liam Weber", 0, 0.0),
    (21, "Owen Larsen", 9, 32559.56),
    (22, "Ivy Novak", 8, 21923.18),
    (23, "Nora Kim", 9, 27861.3),
]

F06 = _task(
    "f06",
    _F,
    f"""
    GET /admin/customers — every user with role `Customer`, with their order statistics.
    {_AUTH_ADMIN} {_page("user `id` ascending")} Each item is an object with exactly the
    fields `id`, `name`, `email`, `order_count` (number of the customer's orders whose
    status is not `cancelled`; 0 if none) and `total_spent` (sum of `total` over those
    same orders; 0 if none).
    """,
    [
        (
            "first_page",
            "happy",
            "GET",
            "/admin/customers",
            ADMIN,
            200,
            {
                "expect_json": {
                    "items": [
                        {"id": i, "name": n, "order_count": c, "total_spent": t}
                        for i, n, c, t in _CUSTOMER_STATS
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "tail",
            "happy",
            "GET",
            "/admin/customers",
            ADMIN,
            200,
            {
                "query": {"limit": 3, "offset": 34},
                "expect_json": {
                    "items": [
                        {
                            "id": 38,
                            "name": "Max Diaz",
                            "email": "max.diaz38@example.com",
                            "order_count": 11,
                            "total_spent": 30896.51,
                        },
                        {"id": 39, "order_count": 9, "total_spent": 30598.54},
                        {"id": 40, "order_count": 5, "total_spent": 11450.04},
                    ],
                    "limit": 3,
                    "offset": 34,
                },
                "ordered": True,
            },
        ),
        ("customer_forbidden", "happy", "GET", "/admin/customers", ALICE, 403, {}),
        (
            "page_budget",
            "trap",
            "GET",
            "/admin/customers",
            ADMIN,
            200,
            {
                "expect_json": {"items": [{"id": row[0]} for row in _CUSTOMER_STATS]},
                "max_queries": QUERY_BUDGET,
            },
        ),
    ],
)

F07 = _task(
    "f07",
    _F,
    f"""
    GET /orders/{{order_id}}/lines — the line items of one order with product names.
    {_AUTH_CUSTOMER} `order_id` is a positive integer path parameter. {_owned("order")}
    Unknown order → 404. {_page("item `id` ascending")} Each item is an object with
    exactly the fields `id`, `product_id`, `product_name` (the product's `name`), `qty`
    and `unit_price`.
    """,
    [
        (
            "alice_order",
            "happy",
            "GET",
            "/orders/1/lines",
            ALICE,
            200,
            {
                "expect_json": {
                    "items": [
                        {
                            "id": 1,
                            "product_id": 18,
                            "product_name": "Reef Shelf",
                            "qty": 4,
                            "unit_price": 343.16,
                        },
                        {
                            "id": 2,
                            "product_id": 7,
                            "product_name": "Halo Bottle",
                            "qty": 1,
                            "unit_price": 23.17,
                        },
                        {
                            "id": 3,
                            "product_id": 26,
                            "product_name": "Slate Kettle",
                            "qty": 3,
                            "unit_price": 53.79,
                        },
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "bob_order",
            "happy",
            "GET",
            "/orders/8/lines",
            BOB,
            200,
            {
                "expect_json": {
                    "items": [
                        {"id": 22, "product_name": "Solid Speaker"},
                        {"id": 23, "product_name": "Arc Shelf"},
                        {"id": 24, "product_name": "Reef Bottle"},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        ("not_mine", "happy", "GET", "/orders/8/lines", ALICE, 404, {}),
        (
            "lines_budget",
            "trap",
            "GET",
            "/orders/1/lines",
            ALICE,
            200,
            {"expect_json": {"items": _ids(1, 2, 3)}, "max_queries": QUERY_BUDGET},
        ),
    ],
)

F08 = _task(
    "f08",
    _F,
    f"""
    GET /admin/users — list user accounts by role. {_AUTH_ADMIN} Query parameter `role`
    (one of `Customer`, `Admin`; default `Customer`; anything else → 422).
    {_page("user `id` ascending")} Each item is an object with exactly the fields `id`,
    `email`, `name` and `role`.
    """,
    [
        (
            "admins",
            "happy",
            "GET",
            "/admin/users",
            ADMIN,
            200,
            {
                "query": {"role": "Admin", "limit": 20},
                "expect_json": {
                    "items": [
                        {
                            "id": 5,
                            "email": "admin@example.com",
                            "name": "Admin User",
                            "role": "Admin",
                        },
                        {"id": 6, "role": "Admin"},
                        {"id": 7, "role": "Admin"},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "customers_paged",
            "happy",
            "GET",
            "/admin/users",
            ADMIN,
            200,
            {
                "query": {"limit": 5, "offset": 2},
                "expect_json": {"items": _ids(3, 4, 8, 9, 10), "limit": 5, "offset": 2},
                "ordered": True,
            },
        ),
        ("customer_forbidden", "happy", "GET", "/admin/users", ALICE, 403, {}),
        (
            "default_page_size",
            "trap",
            "GET",
            "/admin/users",
            ADMIN,
            200,
            {
                "expect_json": {
                    "items": _ids(1, 2, 3, 4, *range(8, 24)),
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        ("limit_too_large", "trap", "GET", "/admin/users", ADMIN, 422, {"query": {"limit": 500}}),
    ],
)

# ---------------------------------------------------------------------------
# control
# ---------------------------------------------------------------------------

_C = "control"

C01 = _task(
    "c01",
    _C,
    f"""
    GET /me — the caller's own profile. {_AUTH_CUSTOMER} Response 200: a JSON object with
    exactly the fields `id`, `email`, `name` and `role`.
    """,
    [
        (
            "alice",
            "happy",
            "GET",
            "/me",
            ALICE,
            200,
            {
                "expect_json": {
                    "id": 1,
                    "email": "alice@example.com",
                    "name": "Alice Nguyen",
                    "role": "Customer",
                }
            },
        ),
        (
            "admin",
            "happy",
            "GET",
            "/me",
            ADMIN,
            200,
            {"expect_json": {"id": 5, "email": "admin@example.com", "role": "Admin"}},
        ),
        ("no_token", "happy", "GET", "/me", None, 401, {}),
    ],
)

C02 = _task(
    "c02",
    _C,
    f"""
    GET /me/addresses — the caller's saved addresses. {_AUTH_CUSTOMER} Customers see only
    their own addresses; an Admin caller sees every customer's addresses.
    {_page("address `id` ascending")} Each item is an object with exactly the fields `id`,
    `line1`, `city` and `zip` (text).
    """,
    [
        (
            "alice",
            "happy",
            "GET",
            "/me/addresses",
            ALICE,
            200,
            {
                "expect_json": {
                    "items": [
                        {
                            "id": 12,
                            "line1": "7924 Pine St",
                            "city": "Georgetown",
                            "zip": "04738",
                        },
                        {"id": 42, "line1": "2391 Lake St", "city": "Fairview", "zip": "36950"},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "bob",
            "happy",
            "GET",
            "/me/addresses",
            BOB,
            200,
            {
                "expect_json": {"items": _ids(9, 23, 27), "limit": 20, "offset": 0},
                "ordered": True,
            },
        ),
        (
            "dave_paged",
            "happy",
            "GET",
            "/me/addresses",
            DAVE,
            200,
            {
                "query": {"limit": 1, "offset": 1},
                "expect_json": {"items": _ids(38), "limit": 1, "offset": 1},
                "ordered": True,
            },
        ),
    ],
)

C03 = _task(
    "c03",
    _C,
    f"""
    GET /me/stats — a summary of the caller's orders. {_AUTH_CUSTOMER} Customers are
    summarized over their own orders; for an Admin caller the summary covers every
    order. Response 200: a JSON object with exactly the fields `order_count` (number of
    orders, any status) and `total_spent` (sum of `total` over the orders whose status is
    `paid` or `shipped`; 0 if none).
    """,
    [
        (
            "alice",
            "happy",
            "GET",
            "/me/stats",
            ALICE,
            200,
            {"expect_json": {"order_count": 7, "total_spent": 8380.03}},
        ),
        (
            "dave_no_orders",
            "happy",
            "GET",
            "/me/stats",
            DAVE,
            200,
            {"expect_json": {"order_count": 0, "total_spent": 0}},
        ),
    ],
)

C04 = _task(
    "c04",
    _C,
    f"""
    GET /categories/{{category}}/products — all products of a category, cheapest first.
    {_AUTH_PUBLIC} `category` is a text path parameter (at most 40 characters); an
    unknown category simply gives an empty list. {_page("`price` ascending, ties by `id`")}
    Each item is an object with exactly the fields `id`, `name` and `price`.
    """,
    [
        (
            "kitchen",
            "happy",
            "GET",
            "/categories/kitchen/products",
            None,
            200,
            {
                "expect_json": {
                    "items": [
                        {"id": 26, "name": "Slate Kettle", "price": 53.79},
                        {"id": 3, "name": "Amber Monitor", "price": 306.41},
                        {"id": 28, "name": "Ember Desk", "price": 408.27},
                        {"id": 5, "name": "Arc Headset", "price": 465.2},
                        {"id": 30, "name": "Onyx Rack", "price": 473.79},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "kitchen_paged",
            "happy",
            "GET",
            "/categories/kitchen/products",
            None,
            200,
            {
                "query": {"limit": 2, "offset": 3},
                "expect_json": {"items": _ids(5, 30), "limit": 2, "offset": 3},
                "ordered": True,
            },
        ),
        (
            "unknown",
            "happy",
            "GET",
            "/categories/toys/products",
            None,
            200,
            {"expect_json": {"items": [], "limit": 20, "offset": 0}},
        ),
    ],
)

C05 = _task(
    "c05",
    _C,
    f"""
    DELETE /reviews/{{review_id}} — delete one of the caller's reviews. {_AUTH_CUSTOMER}
    `review_id` is a positive integer path parameter. {_owned("review")} Success → 204 with
    an empty body. Unknown review → 404.
    """,
    [
        (
            "alice_deletes_own",
            "happy",
            "DELETE",
            "/reviews/111",
            ALICE,
            204,
            {"after_sql": "SELECT COUNT(*) FROM reviews WHERE id = 111", "after_expect": [0]},
        ),
        (
            "carol_deletes_own",
            "happy",
            "DELETE",
            "/reviews/60",
            CAROL,
            204,
            {"after_sql": _REVIEW_COUNT, "after_expect": [119]},
        ),
        ("missing", "happy", "DELETE", "/reviews/9999", ALICE, 404, {}),
    ],
)

C06 = _task(
    "c06",
    _C,
    f"""
    GET /admin/users/{{user_id}} — look up any user account. {_AUTH_ADMIN} `user_id` is a
    positive integer path parameter. Response 200: a JSON object with exactly the fields
    `id`, `email`, `name` and `role`. Unknown user → 404.
    """,
    [
        (
            "bob",
            "happy",
            "GET",
            "/admin/users/2",
            ADMIN,
            200,
            {
                "expect_json": {
                    "id": 2,
                    "email": "bob@example.com",
                    "name": "Bob Martinez",
                    "role": "Customer",
                }
            },
        ),
        ("missing", "happy", "GET", "/admin/users/999", ADMIN, 404, {}),
        ("customer_forbidden", "happy", "GET", "/admin/users/2", ALICE, 403, {}),
    ],
)

C07 = _task(
    "c07",
    _C,
    f"""
    GET /categories — product categories with how many products each has (active or
    not). {_AUTH_PUBLIC} {_page("`category` ascending")} Each item is an object with
    exactly the fields `category` and `product_count`.
    """,
    [
        (
            "all",
            "happy",
            "GET",
            "/categories",
            None,
            200,
            {
                "expect_json": {
                    "items": [
                        {"category": "electronics", "product_count": 6},
                        {"category": "home", "product_count": 3},
                        {"category": "kitchen", "product_count": 5},
                        {"category": "office", "product_count": 5},
                        {"category": "outdoor", "product_count": 6},
                        {"category": "sports", "product_count": 5},
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "paged",
            "happy",
            "GET",
            "/categories",
            None,
            200,
            {
                "query": {"limit": 2, "offset": 4},
                "expect_json": {
                    "items": [
                        {"category": "outdoor", "product_count": 6},
                        {"category": "sports", "product_count": 5},
                    ],
                    "limit": 2,
                    "offset": 4,
                },
                "ordered": True,
            },
        ),
    ],
)

C08 = _task(
    "c08",
    _C,
    f"""
    GET /me/reviews — the reviews the caller has written. {_AUTH_CUSTOMER} Customers see
    only their own reviews; an Admin caller sees every review.
    {_page("review `id` ascending")} Each item is an object with exactly the fields `id`,
    `product_id`, `rating` and `body`.
    """,
    [
        (
            "alice",
            "happy",
            "GET",
            "/me/reviews",
            ALICE,
            200,
            {
                "expect_json": {
                    "items": [
                        {
                            "id": 111,
                            "product_id": 23,
                            "rating": 1,
                            "body": "Would not recommend.",
                        },
                        {
                            "id": 119,
                            "product_id": 29,
                            "rating": 1,
                            "body": "Works great, exactly as described.",
                        },
                    ],
                    "limit": 20,
                    "offset": 0,
                },
                "ordered": True,
            },
        ),
        (
            "carol",
            "happy",
            "GET",
            "/me/reviews",
            CAROL,
            200,
            {
                "expect_json": {"items": _ids(30, 32, 57, 60, 94), "limit": 20, "offset": 0},
                "ordered": True,
            },
        ),
    ],
)

API_TASKS: tuple[ApiTask, ...] = (
    O01,
    O02,
    O03,
    O04,
    O05,
    O06,
    O07,
    O08,
    V01,
    V02,
    V03,
    V04,
    V05,
    V06,
    V07,
    V08,
    E01,
    E02,
    E03,
    E04,
    E05,
    E06,
    E07,
    E08,
    F01,
    F02,
    F03,
    F04,
    F05,
    F06,
    F07,
    F08,
    C01,
    C02,
    C03,
    C04,
    C05,
    C06,
    C07,
    C08,
)
