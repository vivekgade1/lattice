"""A small FastAPI app that is not the template, for the generic emitter
(``lattice.emit.thin.emit_into_app``; tests/emit/test_thin_generic.py).

Its routes live on an ``APIRouter(prefix="/things")`` included under ``/api`` plus one
route on the app itself (``/health``), its tables are created by the factory, and it keeps
a configuration value on ``app.state`` (read by a context native through the profile's
``settings_from_app``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request

router = APIRouter(prefix="/things", tags=["things"])


def _db(request: Request) -> sqlite3.Connection:
    return sqlite3.connect(request.app.state.db_path)


@router.get("/")
def list_things(request: Request) -> list[dict[str, object]]:
    rows = _db(request).execute("SELECT id, name FROM things ORDER BY id").fetchall()
    return [{"id": i, "name": n, "source": "original"} for i, n in rows]


@router.get("/{thing_id}")
def read_thing(thing_id: int, request: Request) -> dict[str, object]:
    row = _db(request).execute("SELECT id, name FROM things WHERE id = ?", (thing_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Thing not found")
    return {"id": row[0], "name": row[1], "source": "original"}


@router.delete("/{thing_id}", status_code=204)
def delete_thing(thing_id: int, request: Request) -> None:
    conn = _db(request)
    conn.execute("DELETE FROM things WHERE id = ?", (thing_id,))
    conn.commit()


def make_app(db_path: str | Path) -> FastAPI:
    app = FastAPI()
    app.state.db_path = str(db_path)
    app.state.greeting = "Hello"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS things (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL)"
    )
    conn.commit()
    conn.close()
    app.include_router(router, prefix="/api")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


PROFILE_SOURCE = """
from __future__ import annotations

from collections.abc import Mapping

from lattice.profile import ApiProfile, NativeSig, Profile
from lattice.typesys import Scalar, Schema


def greet(ctx: Mapping[str, object], name: str) -> str:
    return f"{ctx['greeting']}, {name}"


NATIVES = {"greet": greet}
SETTINGS_FROM_APP = {"greeting": "state.greeting"}


def settings_from_app(app: object) -> dict[str, object]:
    out = {}
    for name, path in SETTINGS_FROM_APP.items():
        value = app
        for attr in path.split("."):
            value = getattr(value, attr)
        out[name] = value
    return out


THING = Schema.from_pairs(
    [("id", Scalar.INT), ("name", Scalar.STR), ("note", Scalar.STR)],
    keys=[("id",)],
    nullable=["note"],
)


def profile() -> Profile:
    return Profile(
        schemas={},
        api=ApiProfile(
            tables={"Thing": THING},
            table_names={"Thing": "things"},
            roles=frozenset({"Public"}),
            natives={"greet": NativeSig((Scalar.STR,), Scalar.STR, settings=("greeting",))},
            settings={"greeting": Scalar.STR},
            error_body="fastapi",
        ),
    )
"""

# replaces GET /api/things/{thing_id} (lifted) and adds GET /api/things/count, a literal
# route the kept parameter routes must not shadow
PROGRAM = """# lattice: v1
@endpoint("GET", "/api/things/{id}", auth=Public)
def read_thing(id: Int) -> One | NotFound:
    things = fetch(Thing)
    found = where(things, col("id") == id)
    thing = one(found, else_=NotFound("Thing not found"))
    greeted = derive(thing, greeting=native.greet(col("name")))
    shown = select(greeted, cols=["id", "name", "greeting"])
    return shown


@endpoint("GET", "/api/things/count", auth=Public)
def count_things() -> One:
    things = fetch(Thing)
    counted = group(things, by=[], n=agg.count())
    return counted
"""
