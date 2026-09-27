"""Loop corpus 04 -- 3-row moving average of daily revenue (pandas + deque).

Loop shape: a bounded buffer of the last N values carried across rows per key.
Construct:  window(by=["store_id"], order=["day"]) + win.mean(x, rows=3).
Targets:    tabular (polars and SQL).
"""

from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict, deque

import pandas as pd

from lattice.typesys import Scalar, Schema

SHAPE = "moving frame of N rows"
CONSTRUCT = "window + win.mean(rows=3)"
HINT = "window("  # what the L127 hint on the original loop names

SALES = Schema.from_pairs(
    [("store_id", Scalar.INT), ("day", Scalar.DATE), ("revenue", Scalar.FLOAT)],
    keys=[("store_id", "day")],
)
SCHEMAS = {"Sales": SALES}
INPUTS = {"sales": "Sales"}
KEY = ("store_id", "day")
PANDAS = True


# --- original -----------------------------------------------------------------------------
def smooth_revenue(sales: pd.DataFrame) -> pd.DataFrame:
    """Dashboard series: each day's revenue and its 3-day moving average per store."""
    recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=3))
    rows = []
    for r in sales.sort_values(["store_id", "day"]).itertuples(index=False):
        buf = recent[r.store_id]
        buf.append(r.revenue)
        rows.append(
            {"store_id": r.store_id, "day": r.day, "revenue": r.revenue,
             "avg_3": sum(buf) / len(buf)}
        )  # fmt: skip
    return pd.DataFrame(rows, columns=["store_id", "day", "revenue", "avg_3"])


original = smooth_revenue

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
sales = load("sales.csv", schema=Sales)
smoothed = window(sales, by=["store_id"], order=["day"], avg_3=win.mean(col("revenue"), rows=3))
write(smoothed, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    start = dt.date(2024, 5, 1)
    rows = [
        {"store_id": store, "day": start + dt.timedelta(offset),
         "revenue": round(rng.uniform(0, 5000), 2)}
        for store in range(1, rng.randint(1, 4) + 1)
        for offset in rng.sample(range(20), rng.randint(0, 9))
    ]  # fmt: skip
    rng.shuffle(rows)
    return {"sales": rows}
