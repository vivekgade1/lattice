"""Loop corpus 03 -- salary rank within a department (ties share a rank).

Loop shape: a counter carried across the sorted rows of a group, reset per group,
            held on ties ("1224" competition ranking).
Construct:  window(by=["dept"], order=["salary"], desc=["salary"]) + win.rank.
Targets:    tabular (polars and SQL).
"""

from __future__ import annotations

import random
from collections import defaultdict

from lattice.typesys import Scalar, Schema

SHAPE = "rank within group, ties share"
CONSTRUCT = "window + win.rank"
HINT = "window("  # what the L127 hint on the original loop names

EMPLOYEES = Schema.from_pairs(
    [("id", Scalar.INT), ("dept", Scalar.STR), ("name", Scalar.STR), ("salary", Scalar.INT)],
    keys=[("id",)],
)
SCHEMAS = {"Employees": EMPLOYEES}
INPUTS = {"employees": "Employees"}
KEY = ("id",)


# --- original -----------------------------------------------------------------------------
def rank_salaries(employees: list[dict]) -> list[dict]:
    """HR report: each employee's salary rank in their department (1 = highest)."""
    by_dept: dict[str, list[dict]] = defaultdict(list)
    for emp in employees:
        by_dept[emp["dept"]].append(emp)
    ranked = []
    for staff in by_dept.values():
        staff.sort(key=lambda e: e["salary"], reverse=True)
        rank, prev = 0, None
        for position, emp in enumerate(staff, start=1):
            if emp["salary"] != prev:
                rank, prev = position, emp["salary"]
            ranked.append({**emp, "salary_rank": rank})
    return ranked


original = rank_salaries

# --- Lattice ------------------------------------------------------------------------------
LATTICE = """# lattice: v1
employees = load("employees.csv", schema=Employees)
ranked = window(
    employees, by=["dept"], order=["salary"], desc=["salary"], salary_rank=win.rank()
)
write(ranked, "out.csv")
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[dict]]:
    depts = ["eng", "ops", "sales"]
    rows = [
        {
            "id": i,
            "dept": rng.choice(depts),
            "name": f"emp{i}",
            "salary": rng.choice([50, 60, 60, 70, 80, 80, 90]) * 1000,  # many ties
        }
        for i in range(1, rng.randint(0, 25) + 1)
    ]
    rng.shuffle(rows)
    return {"employees": rows}
