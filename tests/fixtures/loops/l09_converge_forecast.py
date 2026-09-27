"""Loop corpus 09 -- converge estimates towards targets, stop on a predicate.

Loop shape: `while not all(<row is close enough>): <update every row>` -- a numeric
            convergence loop whose stop condition is a per-row predicate.
Construct:  iterate(halve, forecasts, until=fn.abs(...) <= 0.5, max=64).
Targets:    tabular (polars; iterate is tabular only).
Bound:      each pass halves the gap, so the original needs about log2(gap / 0.5)
            passes; the Lattice cap is 64 (R610 past it).
"""

from __future__ import annotations

import random

from lattice.typesys import Scalar, Schema

SHAPE = "converge until a predicate holds"
CONSTRUCT = "iterate(until=<Bool expr>)"
HINT = "iterate("  # what the L127 hint on the original loop names

FORECASTS = Schema.from_pairs(
    [("id", Scalar.INT), ("region", Scalar.STR), ("estimate", Scalar.FLOAT),
     ("target", Scalar.FLOAT)],
    keys=[("id",)],
)  # fmt: skip
SCHEMAS = {"Forecasts": FORECASTS}
INPUTS = {"forecasts": "Forecasts"}
KEY = ("id",)


# --- original -----------------------------------------------------------------------------
def settle_forecasts(forecasts: list[dict], tolerance: float = 0.5) -> list[dict]:
    """Planning job: move every estimate halfway to its target, round after round, until
    all of them are within the tolerance."""
    rows = [dict(r) for r in forecasts]
    while not all(abs(r["estimate"] - r["target"]) <= tolerance for r in rows):
        for r in rows:
            r["estimate"] = (r["estimate"] + r["target"]) / 2
    return rows


original = settle_forecasts

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
@step
def halve(t: Table[Forecasts]) -> Table[Forecasts]:
    out = derive(t, estimate=(col("estimate") + col("target")) / 2)
    return out

forecasts = load("forecasts.csv", schema=Forecasts)
settled = iterate(
    halve, forecasts, until=fn.abs(col("estimate") - col("target")) <= 0.5, max=64
)
write(settled, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    rows = [
        {
            "id": i,
            "region": rng.choice(["north", "south"]),
            "estimate": round(rng.uniform(-1000, 1000), 3),
            "target": round(rng.uniform(-1000, 1000), 3),
        }
        for i in range(1, rng.randint(0, 20) + 1)
    ]
    return {"forecasts": rows}
