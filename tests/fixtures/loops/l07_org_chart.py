"""Loop corpus 07 -- org chart: everyone below a manager, cycle-safe (FastAPI + SQLModel).

Loop shape: `while frontier:` one query per level for the next reports, a seen-set
            against cycles in bad data (A manages B manages A), a depth limit.
Construct:  traverse(employees, start, parent="manager_id", child="id",
            max_depth=depth) -- one SQL statement (a recursive CTE) instead of one
            query per level.
Targets:    API (Lattice runtime vs the original FastAPI app, same database).
"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from sqlmodel import Field, Session, SQLModel, col, create_engine, select

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

SHAPE = "tree walk down, cycles in data"
CONSTRUCT = "traverse (API, one statement)"
HINT = "traverse("  # what the L127 hint on the original loop names


# --- original -----------------------------------------------------------------------------
class OrgEmployee(SQLModel, table=True):
    __tablename__ = "org_employees"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    title: str
    manager_id: int | None = None


engine = create_engine("sqlite://")  # the deployment's database (tests override get_session)
app = FastAPI()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/employees/{employee_id}/reports")
def list_reports(
    employee_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
    depth: Annotated[int, Query(ge=1, le=10)] = 10,
) -> dict:
    manager = session.get(OrgEmployee, employee_id)
    if manager is None:
        raise HTTPException(status_code=404, detail="employee")
    seen = {manager.id}
    frontier = [manager.id]
    reports = []
    level = 0
    while frontier and level < depth:
        level += 1
        below = session.exec(
            select(OrgEmployee).where(col(OrgEmployee.manager_id).in_(frontier))
        ).all()
        frontier = []
        for emp in sorted(below, key=lambda e: e.id):
            if emp.id in seen:
                continue  # a reporting cycle: already listed
            seen.add(emp.id)
            frontier.append(emp.id)
            reports.append({**emp.model_dump(), "depth": level})
    return {"items": reports[:100], "limit": 100, "offset": 0}


original = list_reports

# --- Lattice ------------------------------------------------------------------------------
USERS = Schema.from_pairs(
    [("id", Scalar.INT), ("name", Scalar.STR), ("role", Scalar.STR), ("token", Scalar.STR)],
    keys=[("id",)],
    private=["token"],
)
EMPLOYEES = Schema.from_pairs(
    [("id", Scalar.INT), ("name", Scalar.STR), ("title", Scalar.STR), ("manager_id", Scalar.INT)],
    keys=[("id",)],
    nullable=["manager_id"],
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"Users": USERS, "Employees": EMPLOYEES},
        table_names={"Users": "users", "Employees": "org_employees"},
        roles=frozenset({"Public", "Customer", "Admin"}),
        error_body="fastapi",  # {"detail": ...} like the original's HTTPException
    ),
)
NATIVES: dict = {}

LATTICE = """# lattice: v1
@endpoint("GET", "/employees/{employee_id}/reports", auth=Public)
def list_reports(employee_id: Int(ge=1), depth: Int(ge=1, le=10) = 10) -> Page | NotFound:
    employees = fetch(Employees)
    start = where(employees, col("id") == employee_id)
    manager = one(start, else_=NotFound("employee"))
    chart = traverse(employees, start, parent="manager_id", child="id", max_depth=depth)
    reports = where(chart, col("depth") > 0)
    ordered = sort(reports, by=["depth", "id"])
    result = page(ordered, limit=100)
    return result
"""


# --- generated inputs ---------------------------------------------------------------------
def generate(rng: random.Random) -> dict[str, list[tuple]]:
    """An org of n people: mostly a tree (managers are earlier ids), some without a
    manager, and some reporting lines to later ids that close cycles."""
    n = rng.randint(1, 40)
    employees = []
    for i in range(1, n + 1):
        roll = rng.random()
        if i == 1 or roll < 0.08:
            manager = None
        elif roll < 0.18:
            manager = rng.randint(1, n)
        else:
            manager = rng.randint(max(1, i - 4), i - 1)
        employees.append((i, f"emp{i}", rng.choice(["eng", "lead", "vp"]), manager))
    return {"org_employees": employees, "users": [(1, "Admin", "Admin", "tok-admin")]}


def requests(rng: random.Random, inputs: dict[str, list[tuple]]) -> list[dict]:
    n = len(inputs["org_employees"])
    out = []
    for _ in range(6):
        who = rng.randint(1, n + 2)  # sometimes missing: 404
        params = {} if rng.random() < 0.3 else {"depth": rng.randint(1, 10)}
        out.append({"method": "GET", "url": f"/employees/{who}/reports", "params": params})
    out.append({"method": "GET", "url": "/employees/1/reports", "params": {"depth": 11}})
    return out


def build_db(path: FilePath, inputs: dict[str, list[tuple]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE
            );
            CREATE TABLE org_employees (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, title TEXT NOT NULL,
                manager_id INTEGER
            );
            """
        )
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", inputs["users"])
        conn.executemany("INSERT INTO org_employees VALUES (?, ?, ?, ?)", inputs["org_employees"])
        conn.commit()
    finally:
        conn.close()


COMPARED_TABLES = ("org_employees",)
