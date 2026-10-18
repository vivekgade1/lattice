"""The rule and behaviour layers (R and B) on the compiled Go app of ``tests/spec/goapp``
(Spike J, node J1, arm S1). ``go_harness`` is frozen and gives the app, its process runtime
and a two-scenario spec; this module adds what layers R and B need and ``go_harness`` lacks:

* a role-aware seed: ``build_db(variant, path)`` writes users ``ann`` and ``ben`` with the
  role ``Member`` (the app never reads the ``role`` column, so the binary is untouched),
  configs and runs; ``variant2`` has other ids and rows (the corrections' re-probe);
* contracts for the four non-health routes and Lattice programs for ``POST
  /configs/{cid}/runs``, ``POST /runs/{rid}/cancel`` and ``GET /runs/{rid}``;
* ``LOGIN`` = the runtime's login, which R and B need explicitly (``check`` ignores
  ``runtime.auth`` there: J1 finding F-LOGIN).

::

    python -m lattice.spec check --spec tests.spec.go_harness_rb:SPEC \\
        --app tests.spec.go_harness_rb:make_app --db tests.spec.go_harness_rb:build_db \\
        --runtime tests.spec.go_harness_rb:RUNTIME --login tests.spec.go_harness_rb:LOGIN \\
        --layers RB

The binary is the one ``$LATTICE_GOAPP_BIN`` names (``go_harness``'s convention).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Contract, ContractError, ContractParam, Invariant, Machine, Spec
from lattice.typesys import Scalar, Schema
from tests.spec.go_harness import BINARY_ENV, RUNTIME, USERS, make_app

S = Scalar
__all__ = [
    "BINARY_ENV",
    "LOGIN",
    "PROFILE",
    "RULES",
    "RUNTIME",
    "SPEC",
    "USERS",
    "build_db",
    "make_app",
]

LOGIN = RUNTIME.auth.login


@dataclass(frozen=True)
class Any422:
    """A representation rule for layer B: a 422 is a 422 whatever its body. The Go app
    answers ``{"detail": "bad id"}`` to a malformed path id; the spec's program answers the
    FastAPI-shaped ``{"detail": [{"loc", "msg"}]}`` the check's default rule expects. The
    CLI takes no ``--rules``, so a Python caller passes ``rules=RULES`` (finding F-B422)."""

    name: str = "ANY_422_BODY"
    description: str = "Both answer 422; the bodies' shapes differ (a string vs a list of locs)."

    def canon(self, body: Any, status: int, side: Any) -> Any:
        return {"<422>": True} if status == 422 else body


RULES = (Any422(),)

DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL,
                    token TEXT NOT NULL, role TEXT NOT NULL);
CREATE TABLE configs (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE runs (id INTEGER PRIMARY KEY, config_id INTEGER NOT NULL REFERENCES configs(id),
                   status TEXT NOT NULL);
"""

# variant -> (user ids, configs, runs): config 1 has no run (a create succeeds), config 2 a
# queued one (a create is refused), config 3 a cancelled one (a create succeeds)
VARIANTS = {
    "seed": {
        "users": (1, 2),
        "configs": ((1, "nightly"), (2, "hourly"), (3, "weekly")),
        "runs": ((1, 2, "queued"), (2, 3, "cancelled")),
    },
    "variant2": {
        "users": (11, 12),
        "configs": ((11, "daily"), (12, "monthly"), (13, "yearly"), (14, "adhoc")),
        "runs": ((21, 12, "queued"), (22, 13, "cancelled"), (23, 14, "cancelled")),
    },
}


def build_db(variant: str | Path, path: Path | None = None) -> Path:
    """The seeded database: ``build_db(path)`` is the seed, ``build_db(variant, path)`` a
    variant (``seed`` or ``variant2``)."""
    if path is None:
        variant, path = "seed", Path(variant)
    plan = VARIANTS[str(variant)]
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for uid, (handle, (name, pw)) in zip(plan["users"], USERS.items(), strict=True):
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, 'Member')", (uid, name, pw, f"tok-{handle}")
            )
        conn.executemany("INSERT INTO configs VALUES (?, ?)", plan["configs"])
        conn.executemany("INSERT INTO runs VALUES (?, ?, ?)", plan["runs"])
        conn.commit()
    finally:
        conn.close()
    return path


USER = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("password", S.STR), ("token", S.STR), ("role", S.STR)],
    keys=[("id",), ("token",)],
    private=["password"],
)
CONFIG = Schema.from_pairs([("id", S.INT), ("name", S.STR)], keys=[("id",)])
RUN = Schema.from_pairs(
    [("id", S.INT), ("config_id", S.INT), ("status", S.STR)],
    keys=[("id",)],
    one_of={"status": ("queued", "cancelled")},
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Config": CONFIG, "Run": RUN},
        table_names={"User": "users", "Config": "configs", "Run": "runs"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("role"),
        error_body="fastapi",
        unauthorized_detail="Not authenticated",
    ),
)

CONTRACTS = (
    Contract(
        "POST",
        "/login",
        auth="Public",
        table="User",
        kind="action",
        params=(
            ContractParam("username", "body", S.STR, min_len=1),
            ContractParam("password", "body", S.STR, min_len=1),
        ),
    ),
    Contract(
        "POST",
        "/configs/{cid}/runs",
        auth="Member",
        table="Run",
        kind="create",
        params=(ContractParam("cid", "path", S.INT, ref="Config"),),
        errors=(
            ContractError(401, "Not authenticated", when="unauthenticated"),
            ContractError(404, "Not found", when="not_found"),
        ),
    ),
    Contract(
        "POST",
        "/runs/{rid}/cancel",
        auth="Member",
        table="Run",
        kind="action",
        params=(ContractParam("rid", "path", S.INT, ref="Run"),),
        errors=(
            ContractError(401, "Not authenticated", when="unauthenticated"),
            ContractError(404, "Not found", when="not_found"),
        ),
    ),
    Contract(
        "GET",
        "/runs/{rid}",
        auth="Member",
        table="Run",
        kind="read",
        params=(ContractParam("rid", "path", S.INT, ref="Run"),),
        errors=(
            ContractError(401, "Not authenticated", when="unauthenticated"),
            ContractError(404, "Not found", when="not_found"),
        ),
    ),
)

RUNS_PROGRAM = """# lattice: v1
RUN_COLS = ["id", "config_id", "status"]


@endpoint("POST", "/configs/{cid}/runs", auth=Authenticated)
def create_run(cid: Int(ge=1, le=1000000), user: CurrentUser) -> Created | NotFound | Conflict:
    configs = fetch(Config)
    found = where(configs, col("id") == cid)
    config = one(found, else_=NotFound("Not found"))
    runs = fetch(Run)
    live = where(runs, (col("config_id") == cid) & (col("status") == "queued"))
    counted = group(live, by=[], n=agg.count())
    row = one(counted, else_=NotFound("Not found"))
    free = require(row, col("n") == 0, else_=Conflict("already live"))
    made = insert(Run, config_id=cid, status="queued")
    shown = select(made, cols=RUN_COLS)
    return shown


@endpoint("POST", "/runs/{rid}/cancel", auth=Authenticated)
def cancel_run(rid: Int, user: CurrentUser) -> One | NotFound | Conflict:
    runs = fetch(Run)
    found = where(runs, col("id") == rid)
    run = one(found, else_=NotFound("Not found"))
    pending = require(run, col("status") == "queued", else_=Conflict("already final"))
    done = update(pending, status="cancelled")
    shown = select(done, cols=RUN_COLS)
    return shown


@endpoint("GET", "/runs/{rid}", auth=Authenticated)
def get_run(rid: Int, user: CurrentUser) -> One | NotFound:
    runs = fetch(Run)
    found = where(runs, col("id") == rid)
    run = one(found, else_=NotFound("Not found"))
    shown = select(run, cols=RUN_COLS)
    return shown
"""

ONE_LIVE = Invariant(
    "one_live_run_per_config",
    "A config has at most one queued run.",
    'count(Run, by="config_id", where=Run.status == "queued") <= 1',
)
SPEC = Spec(
    profile=PROFILE,
    contracts=CONTRACTS,
    programs={"runs": RUNS_PROGRAM},
    invariants=(ONE_LIVE,),
    machines=(Machine("Run", "status", {"queued": ("cancelled",)}, initial=("queued",)),),
    users=USERS,
)
