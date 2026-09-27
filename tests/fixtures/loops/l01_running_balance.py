"""Loop corpus 01 -- running balance per account.

Loop shape: carry a value across rows in order (one accumulator per key, `+=`).
Construct:  window(by=["account_id"], order=["posted_on", "id"]) + win.cum_sum.
Targets:    tabular (polars and SQL).
"""

from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict

from lattice.typesys import Scalar, Schema

SHAPE = "running value per key"
CONSTRUCT = "window + win.cum_sum"
HINT = "window("  # what the L127 hint on the original loop names

TXNS = Schema.from_pairs(
    [("id", Scalar.INT), ("account_id", Scalar.INT), ("posted_on", Scalar.DATE),
     ("amount", Scalar.INT)],
    keys=[("id",)],
)  # fmt: skip
SCHEMAS = {"Txns": TXNS}
INPUTS = {"txns": "Txns"}  # file stem (txns.csv) -> schema
KEY = ("id",)  # rows are compared as a set keyed by this


# --- original -----------------------------------------------------------------------------
def statement_lines(txns: list[dict]) -> list[dict]:
    """Ledger service: every transaction with the account balance after it."""
    balances: dict[int, int] = defaultdict(int)
    lines = []
    for txn in sorted(txns, key=lambda t: (t["account_id"], t["posted_on"], t["id"])):
        balances[txn["account_id"]] += txn["amount"]
        lines.append({**txn, "balance": balances[txn["account_id"]]})
    return lines


original = statement_lines

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
txns = load("txns.csv", schema=Txns)
lines = window(
    txns, by=["account_id"], order=["posted_on", "id"], balance=win.cum_sum(col("amount"))
)
write(lines, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    start = dt.date(2024, 1, 1)
    ids = rng.sample(range(1, 500), rng.randint(0, 30))  # ids not in date order
    txns = [
        {
            "id": i,
            "account_id": rng.randint(1, 4),
            "posted_on": start + dt.timedelta(days=rng.randint(0, 6)),  # same-day postings
            "amount": rng.randint(-500, 500),
        }
        for i in ids
    ]
    return {"txns": txns}
