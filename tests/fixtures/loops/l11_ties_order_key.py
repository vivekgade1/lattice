"""Loop corpus 11 -- number each customer's orders; same-day orders tie.

Loop shape: a per-key counter over rows sorted by a column with ties; the original
            breaks ties by sorting on the id too.
Construct:  window(by=["customer_id"], order=["ordered_on", "id"]) + win.row_number.
            Ordering by "ordered_on" alone is L431 (a warning here, an error in API
            programs): the fix the hint names is adding the key column to `order`.
Targets:    tabular (polars and SQL).
"""

from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict

from lattice.typesys import Scalar, Schema

SHAPE = "sequence number per key, ties"
CONSTRUCT = "window + win.row_number, key in order"
HINT = "window("  # what the L127 hint on the original loop names

ORDERS = Schema.from_pairs(
    [("id", Scalar.INT), ("customer_id", Scalar.INT), ("ordered_on", Scalar.DATE),
     ("total", Scalar.FLOAT)],
    keys=[("id",)],
)  # fmt: skip
SCHEMAS = {"Orders": ORDERS}
INPUTS = {"orders": "Orders"}
KEY = ("id",)


# --- original -----------------------------------------------------------------------------
def number_orders(orders: list[dict]) -> list[dict]:
    """CRM export: each order's sequence number for its customer (1 = first order)."""
    seq: dict[int, int] = defaultdict(int)
    out = []
    for order in sorted(orders, key=lambda o: (o["customer_id"], o["ordered_on"], o["id"])):
        seq[order["customer_id"]] += 1
        n = seq[order["customer_id"]]
        out.append({**order, "order_no": n, "is_first": n == 1})
    return out


original = number_orders

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
orders = load("orders.csv", schema=Orders)
numbered = window(
    orders, by=["customer_id"], order=["ordered_on", "id"], order_no=win.row_number()
)
flagged = derive(numbered, is_first=col("order_no") == 1)
write(flagged, "out.csv")
"""
# The tie-prone spelling the checker flags (L431), kept for the test.
LATTICE_WITH_TIES = LATTICE.replace('order=["ordered_on", "id"]', 'order=["ordered_on"]')


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    start = dt.date(2024, 7, 1)
    ids = rng.sample(range(1, 300), rng.randint(0, 25))
    rows = [
        {
            "id": i,
            "customer_id": rng.randint(1, 3),
            "ordered_on": start + dt.timedelta(rng.randint(0, 3)),  # many same-day orders
            "total": round(rng.uniform(5, 200), 2),
        }
        for i in ids
    ]
    return {"orders": rows}
