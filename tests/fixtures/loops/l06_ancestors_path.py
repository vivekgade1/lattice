"""Loop corpus 06 -- breadcrumb: the path from a category up to its root.

Loop shape: `while node is not None: node = by_id.get(node["parent_id"])` -- follow the
            parent link upwards, guarded against cycles, cut off at a maximum depth.
Construct:  traverse(..., direction="up", max_depth=5).
Targets:    tabular (polars and SQL: a recursive CTE).
"""

from __future__ import annotations

import random

from lattice.typesys import Scalar, Schema

SHAPE = "parent walk up (ancestors)"
CONSTRUCT = 'traverse (direction="up")'
HINT = "traverse("  # what the L127 hint on the original loop names

CATEGORIES = Schema.from_pairs(
    [("id", Scalar.INT), ("parent_id", Scalar.INT), ("name", Scalar.STR)],
    keys=[("id",)],
    nullable=["parent_id"],
)
SCHEMAS = {"Categories": CATEGORIES}
INPUTS = {"categories": "Categories"}
KEY = ("id",)
ORDERED = True  # the leaf first, then each ancestor


# --- original -----------------------------------------------------------------------------
def breadcrumbs(categories: list[dict], leaf_id: int = 8, max_depth: int = 5) -> list[dict]:
    """Storefront breadcrumb: the category, its parent, grandparent, ... (leaf first)."""
    by_id = {c["id"]: c for c in categories}
    trail: list[dict] = []
    seen: set[int] = set()
    node = by_id.get(leaf_id)
    depth = 0
    while node is not None and node["id"] not in seen and depth <= max_depth:
        seen.add(node["id"])
        trail.append({**node, "depth": depth})
        node = by_id.get(node["parent_id"])
        depth += 1
    return trail


original = breadcrumbs

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
categories = load("categories.csv", schema=Categories)
leaf = where(categories, col("id") == 8)
trail = traverse(
    categories, leaf, parent="parent_id", child="id", max_depth=5, direction="up"
)
write(trail, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    """Ids 1..n (the leaf 8 missing when n < 8); parents are earlier ids, a root, or any
    id (a possible cycle, a self-loop, or a dangling id past n)."""
    n = rng.randint(5, 14)
    rows = []
    for i in range(1, n + 1):
        roll = rng.random()
        if i == 1 or roll < 0.1:
            parent = None
        elif roll < 0.25:
            parent = rng.randint(1, n + 2)
        else:
            parent = rng.randint(max(1, i - 2), i - 1)
        rows.append({"id": i, "parent_id": parent, "name": f"cat{i}"})
    rng.shuffle(rows)
    return {"categories": rows}
