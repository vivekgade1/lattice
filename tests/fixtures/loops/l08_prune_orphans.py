"""Loop corpus 08 -- retry-until-stable clean-up: drop orphaned replies.

Loop shape: `while True:` apply one clean-up pass over the whole table; stop when a
            pass changes nothing (deleting a reply orphans its own replies).
Construct:  iterate(prune, comments, until="fixpoint", max=100) with a @step round.
Targets:    tabular (polars; iterate is tabular only).
Bound:      the original stops after at most len(comments) + 1 passes; the Lattice
            version states its cap (100) and fails loudly with R610 past it.
"""

from __future__ import annotations

import random

from lattice.typesys import Scalar, Schema

SHAPE = "repeat until stable"
CONSTRUCT = 'iterate(until="fixpoint")'
HINT = "iterate("  # what the L127 hint on the original loop names

COMMENTS = Schema.from_pairs(
    [("id", Scalar.INT), ("parent_id", Scalar.INT), ("author", Scalar.STR), ("body", Scalar.STR)],
    keys=[("id",)],
    nullable=["parent_id"],
)
SCHEMAS = {"Comments": COMMENTS}
INPUTS = {"comments": "Comments"}
KEY = ("id",)


# --- original -----------------------------------------------------------------------------
def prune_orphans(comments: list[dict]) -> list[dict]:
    """Moderation job: after deletions, remove replies whose parent is gone, repeatedly,
    until the thread is consistent."""
    rows = list(comments)
    while True:
        ids = {c["id"] for c in rows}
        kept = [c for c in rows if c["parent_id"] is None or c["parent_id"] in ids]
        if len(kept) == len(rows):
            return kept
        rows = kept


original = prune_orphans

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
@step
def prune(t: Table[Comments]) -> Table[Comments]:
    present = group(t, by=["id"], n=agg.count())
    parents = rename(present, id="parent_id")
    linked = join(t, parents, on=["parent_id"], how="left")
    kept = where(linked, is_null(col("parent_id")) | ~is_null(col("n")))
    out = select(kept, cols=["id", "parent_id", "author", "body"])
    return out

comments = load("comments.csv", schema=Comments)
clean = iterate(prune, comments, until="fixpoint", max=100)
write(clean, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    """Reply threads over ids 1..n, with some ids deleted: a deleted parent orphans a
    chain of replies below it (several passes)."""
    n = rng.randint(0, 30)
    deleted = set(rng.sample(range(1, n + 1), min(n, rng.randint(0, 4))))
    rows = []
    for i in range(1, n + 1):
        if i in deleted:
            continue
        parent = None if i == 1 or rng.random() < 0.2 else rng.randint(max(1, i - 3), i - 1)
        rows.append({"id": i, "parent_id": parent, "author": f"u{i % 5}", "body": f"reply {i}"})
    rng.shuffle(rows)
    return {"comments": rows}
