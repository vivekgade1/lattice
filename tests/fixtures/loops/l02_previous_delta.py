"""Loop corpus 02 -- change since the previous reading (pandas `itertuples`).

Loop shape: read the previous row of the same key (a `last` dict carried across rows).
Construct:  window(by=["product_id"], order=["day"]) + win.lag, then derive.
Targets:    tabular (polars and SQL).
"""

from __future__ import annotations

import datetime as dt
import random

import pandas as pd

from lattice.typesys import Scalar, Schema

SHAPE = "previous row of the same key"
CONSTRUCT = "window + win.lag"
HINT = "window("  # what the L127 hint on the original loop names

PRICES = Schema.from_pairs(
    [("product_id", Scalar.INT), ("day", Scalar.DATE), ("price", Scalar.FLOAT)],
    keys=[("product_id", "day")],
)
SCHEMAS = {"Prices": PRICES}
INPUTS = {"prices": "Prices"}
KEY = ("product_id", "day")
PANDAS = True  # the original takes and returns DataFrames


# --- original -----------------------------------------------------------------------------
def price_changes(prices: pd.DataFrame) -> pd.DataFrame:
    """Price history report: each price with the previous price of that product."""
    last: dict[int, float] = {}
    out = []
    for row in prices.sort_values(["product_id", "day"]).itertuples(index=False):
        prev = last.get(row.product_id)
        out.append(
            {
                "product_id": row.product_id,
                "day": row.day,
                "price": row.price,
                "prev_price": prev,
                "change": None if prev is None else row.price - prev,
            }
        )
        last[row.product_id] = row.price
    return pd.DataFrame(out, columns=["product_id", "day", "price", "prev_price", "change"])


original = price_changes

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
prices = load("prices.csv", schema=Prices)
with_prev = window(prices, by=["product_id"], order=["day"], prev_price=win.lag(col("price")))
changes = derive(with_prev, change=col("price") - col("prev_price"))
write(changes, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    start = dt.date(2024, 3, 1)
    rows = []
    for product in range(1, rng.randint(1, 5) + 1):
        for offset in rng.sample(range(30), rng.randint(0, 8)):
            price = round(rng.uniform(1, 100), 2)
            day = start + dt.timedelta(offset)
            rows.append({"product_id": product, "day": day, "price": price})
    rng.shuffle(rows)
    return {"prices": rows}
