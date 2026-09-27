"""Loop corpus 05 -- a category's descendants with their depth (BFS with a seen-set).

Loop shape: walk child links level by level (frontier / seen set / depth counter),
            cycle-safe, cut off at a maximum depth.
Construct:  traverse(categories, root, parent="parent_id", child="id", max_depth=4).
Targets:    tabular (polars and SQL: a recursive CTE).
"""

from __future__ import annotations

import random
from collections import defaultdict

from lattice.typesys import Scalar, Schema

SHAPE = "tree walk down, with depth"
CONSTRUCT = "traverse (down)"
HINT = "traverse("  # what the L127 hint on the original loop names

CATEGORIES = Schema.from_pairs(
    [("id", Scalar.INT), ("parent_id", Scalar.INT), ("name", Scalar.STR)],
    keys=[("id",)],
    nullable=["parent_id"],
)
SCHEMAS = {"Categories": CATEGORIES}
INPUTS = {"categories": "Categories"}
KEY = ("id",)
ORDERED = True  # both sides give (depth, id) order


# --- original -----------------------------------------------------------------------------
def category_subtree(categories: list[dict], root_id: int = 1, max_depth: int = 4) -> list[dict]:
    """Catalog service: a category and everything below it, nearest first."""
    children: dict[int, list[dict]] = defaultdict(list)
    for cat in categories:
        if cat["parent_id"] is not None:
            children[cat["parent_id"]].append(cat)
    root = next((c for c in categories if c["id"] == root_id), None)
    if root is None:
        return []
    seen = {root["id"]}
    found = [{**root, "depth": 0}]
    frontier = [root]
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        next_level = []
        for node in frontier:
            for child in children[node["id"]]:
                if child["id"] not in seen:
                    seen.add(child["id"])
                    found.append({**child, "depth": depth})
                    next_level.append(child)
        frontier = next_level
    return sorted(found, key=lambda c: (c["depth"], c["id"]))


original = category_subtree

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
categories = load("categories.csv", schema=Categories)
root = where(categories, col("id") == 1)
subtree = traverse(categories, root, parent="parent_id", child="id", max_depth=4)
write(subtree, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    """A random forest over ids 1..n (parents mostly earlier ids, so depth > 4 happens),
    some roots, and some links to later ids, which can close cycles."""
    n = rng.randint(1, 25)
    rows = []
    for i in range(1, n + 1):
        roll = rng.random()
        if i == 1 or roll < 0.1:
            parent = None
        elif roll < 0.2:
            parent = rng.randint(1, n)  # anywhere: a possible cycle (even a self-loop)
        else:
            parent = rng.randint(max(1, i - 3), i - 1)
        rows.append({"id": i, "parent_id": parent, "name": f"cat{i}"})
    rng.shuffle(rows)
    return {"categories": rows}
