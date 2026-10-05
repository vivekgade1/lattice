"""Invariant and state-machine mining (Phase 2 plan 2026-10-05 §4, W2-C).

Each template on a hand-built SQLite file: a tiny shop profile (orders with a status and
a total, order lines, items with stock and price, users, accounts one per user), a seed
snapshot and the snapshots after "requests" (rows written here as the app would). Then:
seed rows are never mined, refuted candidates are dropped, proposals are pending and
stable, the ASGI recorder, and on the multi-vendor app (probe and scenario traffic
through the original) the recall of the reviewed spec's machine and invariants and the
precision of everything else proposed.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.appmodel import DataModel, FieldSpec, RepoModel, TableModel
from lattice.importer.mine import (
    CheckConstraint,
    MiningHints,
    Proposal,
    Recorder,
    Run,
    TransitionMap,
    Unique,
    Validator,
    mine,
    transition_maps,
)
from lattice.profile import ApiProfile, Profile
from lattice.spec import Invariant, Machine, Unchecked
from lattice.typesys import Scalar, Schema

S = Scalar

# ---- the hand-built shop --------------------------------------------------------------------

STATES = ("pending", "paid", "shipped", "cancelled")

ORDER = Schema.from_pairs(
    [
        ("id", S.INT),
        ("user_id", S.INT),
        ("status", S.STR),
        ("total", S.FLOAT),
        ("note", S.STR),
    ],
    keys=[("id",)],
    nullable=["note"],
    owner="user_id",
    one_of={"status": STATES},
    one_of_stored={"status": tuple(s.upper() for s in STATES)},
)
LINE = Schema.from_pairs(
    [("id", S.INT), ("order_id", S.INT), ("qty", S.INT), ("price", S.FLOAT)],
    keys=[("id",)],
    owner_via=("order_id", "Order"),
)
ITEM = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("stock", S.INT), ("price", S.FLOAT)], keys=[("id",)]
)
USER = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("token", S.STR), ("role", S.STR)], keys=[("id",)]
)
ACCOUNT = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("nickname", S.STR)],
    keys=[("id",)],
    nullable=["nickname"],
)
TABLES = {"Order": ORDER, "Line": LINE, "Item": ITEM, "User": USER, "Account": ACCOUNT}
SQL = {"Order": "orders", "Line": "lines", "Item": "items", "User": "users", "Account": "accounts"}


def shop_profile() -> Profile:
    api = ApiProfile(
        tables=TABLES,
        table_names=SQL,
        roles=frozenset({"Public", "Customer", "Admin"}),
        users_table="User",
    )
    return Profile(schemas={}, api=api)


PROFILE = shop_profile()

SEED: dict[str, list[dict[str, Any]]] = {
    "users": [
        {"id": 1, "email": "a@x", "token": "t1", "role": "Customer"},
        {"id": 2, "email": "b@x", "token": "t2", "role": "Customer"},
        {"id": 3, "email": "c@x", "token": "t3", "role": "Admin"},
    ],
    "items": [{"id": 1, "name": "pen", "stock": 5, "price": 2.0}],
    "orders": [{"id": 1, "user_id": 1, "status": "PAID", "total": 4.0, "note": None}],
    "lines": [{"id": 1, "order_id": 1, "qty": 2, "price": 2.0}],
    "accounts": [],
}


def write_db(path: Path, data: Mapping[str, Sequence[Mapping[str, Any]]]) -> Path:
    conn = sqlite3.connect(path)
    try:
        for name, schema in TABLES.items():
            table = SQL[name]
            cols = ", ".join(f'"{c}"' for c in schema.names())
            conn.execute(f'CREATE TABLE "{table}" ({cols})')
            for row in data.get(table, ()):
                marks = ", ".join("?" for _ in schema.names())
                values = [row.get(c) for c in schema.names()]
                conn.execute(f'INSERT INTO "{table}" VALUES ({marks})', values)
        conn.commit()
    finally:
        conn.close()
    return path


def with_rows(base: Mapping[str, list[dict]], **changes: list[dict]) -> dict[str, list[dict]]:
    """``base`` with each named table's rows replaced (by key) or added."""
    out = {t: [dict(r) for r in rows] for t, rows in base.items()}
    for table, rows in changes.items():
        current = {r["id"]: r for r in out.get(table, [])}
        for r in rows:
            current[r["id"]] = {**current.get(r["id"], {}), **r}
        out[table] = list(current.values())
    return out


class Runs:
    """Builds runs: each a seed snapshot followed by the states given."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.n = 0

    def run(self, *states: Mapping[str, list[dict]], seed: Mapping[str, list[dict]] = SEED) -> Run:
        self.n += 1
        paths = []
        for k, state in enumerate((seed, *states)):
            paths.append(write_db(self.tmp / f"run{self.n}-{k}.db", state))
        requests = tuple({"method": "POST", "path": f"/r{self.n}/{k}"} for k in states)
        return Run(f"run{self.n}", tuple(paths), requests[: len(states)])


@pytest.fixture
def runs(tmp_path: Path) -> Runs:
    return Runs(tmp_path)


def exprs(result: Any, kind: str = "invariant") -> set[str]:
    return {p.spec_object.expr for p in result.proposals if p.kind == kind}


def by_expr(result: Any, expr: str) -> Proposal:
    return next(p for p in result.proposals if p.kind == "invariant" and p.spec_object.expr == expr)


def table_of(text: str) -> str:
    m = re.search(r"count\((\w+)|\b([A-Z]\w*)\.", text)
    assert m is not None, text
    return m.group(1) or m.group(2)


def machines(result: Any) -> list[Machine]:
    return [p.spec_object for p in result.proposals if p.kind == "machine"]


# ---- templates ------------------------------------------------------------------------------


def test_non_negative_and_positive(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 0, "price": 3.0}]))
    b = runs.run(with_rows(SEED, items=[{"id": 2, "name": "cup", "stock": 3, "price": 1.5}]))
    result = mine(PROFILE, [a, b])
    found = exprs(result)
    # stock 0 was written: >= 0, not > 0; price: > 0 (and not the weaker >= 0 as well)
    assert "Item.stock >= 0" in found
    assert "Item.stock > 0" not in found
    assert "Item.price > 0" in found
    assert "Item.price >= 0" not in found
    p = by_expr(result, "Item.price > 0")
    assert p.support == 2
    assert p.source.startswith("observed")
    assert {r["id"] for r in p.evidence["rows"]} == {2}
    assert p.evidence["requests"]  # the requests that wrote the rows
    # ids, foreign keys and the seed-only tables are not mined
    assert not any("Item.id" in e or "Order.user_id" in e for e in found)


def test_non_empty_text(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 1, "price": 1.0}]))
    b = runs.run(with_rows(SEED, items=[{"id": 3, "name": "pad", "stock": 1, "price": 1.0}]))
    assert 'Item.name != ""' in exprs(mine(PROFILE, [a, b]))
    c = runs.run(with_rows(SEED, items=[{"id": 4, "name": "", "stock": 1, "price": 1.0}]))
    assert 'Item.name != ""' not in exprs(mine(PROFILE, [a, b, c]))


def test_bounds_from_check_constraints(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 0, "price": 3.0}]))
    hints = MiningHints(
        checks=(
            CheckConstraint("items", "stock >= 0 AND price < 1000", name="ck_items", source="m:3"),
            CheckConstraint("Item", '"price" <> 0', source="m:4"),
            CheckConstraint("Item", "length(name) > 2", source="m:5"),
        )
    )
    result = mine(PROFILE, [a], hints=hints)
    found = exprs(result)
    assert "Item.stock >= 0 and Item.price < 1000" in found
    assert "Item.price != 0" in found
    p = by_expr(result, "Item.stock >= 0 and Item.price < 1000")
    assert "CheckConstraint" in p.source and "m:3" in p.source
    # what does not translate is an Unchecked proposal quoting the source
    (u,) = [p for p in result.proposals if p.kind == "unchecked"]
    assert isinstance(u.spec_object, Unchecked)
    assert "length(name) > 2" in u.spec_object.text and "Item" in u.spec_object.concerns


def test_bounds_from_model_fields(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 1, "price": 3.0}]))
    result = mine(PROFILE, [a], model=shop_model())
    found = exprs(result)
    # ItemCreate.price gt=0 (a request model named after the table), Item.stock ge=0
    # (the table's own field); supported or not, a static candidate is proposed
    assert "Item.price > 0" in found
    assert "Item.stock >= 0" in found
    assert "static" in by_expr(result, "Item.price > 0").source
    assert "ItemCreate.price" in by_expr(result, "Item.price > 0").source
    # Line.qty le=99 on LineCreate: no line was written, still proposed (support 0)
    assert by_expr(result, "Line.qty <= 99").support == 0


def test_choices_on_a_plain_column(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 1, "price": 3.0}]))
    model = shop_model(item_name_choices=("pen", "ink"))
    assert 'Item.name in ("pen", "ink")' in exprs(mine(PROFILE, [a], model=model))


def test_not_null_beyond_the_schema(runs: Runs) -> None:
    order = {"user_id": 2, "status": "PENDING", "total": 0.0}
    a = runs.run(with_rows(SEED, orders=[{"id": 2, **order, "note": "gift"}]))
    b = runs.run(with_rows(SEED, orders=[{"id": 2, **order, "note": "rush"}]))
    found = exprs(mine(PROFILE, [a, b]))
    # the seed's order 1 has no note, so it refutes "note is never null"... unless the
    # app's rows are all it is mined on and the seed agrees: here the seed disagrees
    assert "not is_null(Order.note)" not in found
    seed = with_rows(SEED, orders=[{"id": 1, "note": "first"}])
    c = runs.run(with_rows(seed, orders=[{"id": 2, **order, "note": "gift"}]), seed=seed)
    d = runs.run(with_rows(seed, orders=[{"id": 2, **order, "note": "rush"}]), seed=seed)
    assert "not is_null(Order.note)" in exprs(mine(PROFILE, [c, d]))
    e = runs.run(with_rows(seed, orders=[{"id": 2, **order, "note": None}]), seed=seed)
    assert "not is_null(Order.note)" not in exprs(mine(PROFILE, [c, d, e]))


def test_uniqueness_one_row_per_foreign_key(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, accounts=[{"id": 1, "user_id": 1, "nickname": "al"}]))
    b = runs.run(
        with_rows(SEED, accounts=[{"id": 1, "user_id": 2, "nickname": "bo"}]),
        with_rows(
            SEED,
            accounts=[
                {"id": 1, "user_id": 2, "nickname": "bo"},
                {"id": 2, "user_id": 3, "nickname": "cy"},
            ],
        ),
    )
    result = mine(PROFILE, [a, b], model=shop_model())
    found = exprs(result)
    assert 'count(Account, by="user_id") <= 1' in found
    # a unique column of the model (User.email unique=True): proposed from the source
    assert 'count(User, by="email") <= 1' in found
    assert "unique" in by_expr(result, 'count(User, by="email") <= 1').source
    # a second account for user 3 refutes the one-to-one candidate
    c = runs.run(
        with_rows(
            SEED,
            accounts=[
                {"id": 1, "user_id": 3, "nickname": "x"},
                {"id": 2, "user_id": 3, "nickname": "y"},
            ],
        )
    )
    assert 'count(Account, by="user_id") <= 1' not in exprs(mine(PROFILE, [a, b, c]))


def test_unique_hints(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 1, "price": 3.0}]))
    hints = MiningHints(
        uniques=(
            Unique("Item", ("name",), source="UniqueConstraint(name)"),
            Unique("Item", ("name", "price"), source="unique_together"),
            Unique("Item", ("name",), condition="stock > 0", source="UniqueConstraint(cond)"),
        )
    )
    result = mine(PROFILE, [a], hints=hints)
    assert 'count(Item, by="name") <= 1' in exprs(result)
    texts = [p.spec_object.text for p in result.proposals if p.kind == "unchecked"]
    assert any("name, price" in t for t in texts)
    assert any("stock > 0" in t for t in texts)


def _order_with_lines(oid: int, total: float, lines: list[tuple[int, int, float]]) -> dict:
    return {
        "orders": [{"id": oid, "user_id": 2, "status": "PENDING", "total": total, "note": None}],
        "lines": [{"id": i, "order_id": oid, "qty": q, "price": p} for i, q, p in lines],
    }


def test_aggregate_equality_order_total(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, **_order_with_lines(2, 10.0, [(2, 2, 3.0), (3, 1, 4.0)])))
    b = runs.run(with_rows(SEED, **_order_with_lines(2, 7.5, [(2, 3, 2.5)])))
    result = mine(PROFILE, [a, b])
    found = exprs(result)
    expr = 'Order.total == sum(Line.qty * Line.price, by="order_id")'
    assert expr in found
    assert by_expr(result, expr).support == 2
    assert not any(e.startswith("Order.total == sum(Line.price") for e in found)
    assert not any(e.startswith("Order.total == sum(Line.qty,") for e in found)


def test_observed_state_machine(runs: Runs) -> None:
    def order(status: str) -> dict:
        return with_rows(
            SEED, orders=[{"id": 2, "user_id": 2, "status": status, "total": 0.0, "note": None}]
        )

    def seed_order(status: str) -> dict:
        return with_rows(SEED, orders=[{"id": 1, "status": status}])

    a = runs.run(order("PENDING"), order("PAID"), order("SHIPPED"))
    b = runs.run(order("PENDING"), order("CANCELLED"))
    c = runs.run(seed_order("SHIPPED"))  # the seed's paid order 1 is shipped
    result = mine(PROFILE, [a, b, c])
    (m,) = machines(result)
    assert (m.table, m.column) == ("Order", "status")
    assert set(m.edges) == {
        ("pending", "paid"),
        ("paid", "shipped"),
        ("pending", "cancelled"),
    }
    assert m.initial == ("pending",)
    p = next(p for p in result.proposals if p.kind == "machine")
    assert p.support == 4  # transitions seen
    assert p.source.startswith("observed")
    assert any(t["from"] == "paid" and t["to"] == "shipped" for t in p.evidence["transitions"])


def test_static_transition_map(runs: Runs) -> None:
    def order(status: str) -> dict:
        return with_rows(
            SEED, orders=[{"id": 2, "user_id": 2, "status": status, "total": 0.0, "note": None}]
        )

    a = runs.run(order("PENDING"), order("PAID"))
    static = TransitionMap(
        "Order",
        "status",
        {"PENDING": ("PAID", "CANCELLED"), "PAID": ("SHIPPED", "CANCELLED"), "SHIPPED": ()},
        source="m.py:10 VALID",
    )
    result = mine(PROFILE, [a], hints=MiningHints(transitions=(static,)))
    (m,) = machines(result)
    # the static map's edges (in value spelling), the observed initial states
    assert set(m.edges) == {
        ("pending", "paid"),
        ("pending", "cancelled"),
        ("paid", "shipped"),
        ("paid", "cancelled"),
    }
    assert m.initial == ("pending",)
    p = next(p for p in result.proposals if p.kind == "machine")
    assert "m.py:10" in p.source
    # an observed move the map does not allow refutes it; the observed machine remains
    b = runs.run(order("PENDING"), order("SHIPPED"))
    result = mine(PROFILE, [a, b], hints=MiningHints(transitions=(static,)))
    (m,) = machines(result)
    assert set(m.edges) == {("pending", "paid"), ("pending", "shipped")}
    assert any(r.kind == "machine" for r in result.refuted)


def test_transition_maps_read_from_source(tmp_path: Path) -> None:
    pkg = tmp_path / "shoppkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "flow.py").write_text(
        "import enum\n"
        "class St(enum.Enum):\n"
        "    PENDING = 'pending'\n    PAID = 'paid'\n    SHIPPED = 'shipped'\n"
        "    CANCELLED = 'cancelled'\n"
        "ALLOWED: dict = {\n"
        "    St.PENDING: {St.PAID, St.CANCELLED},\n"
        "    St.PAID: [St.SHIPPED],\n"
        "    St.SHIPPED: set(),\n"
        "}\n"
        "NOT_A_MAP = {'a': 1}\n"
    )
    (found,) = transition_maps(tmp_path, "shoppkg", shop_model())
    assert (found.table, found.column) == ("Order", "status")
    assert dict(found.edges) == {
        "PENDING": ("CANCELLED", "PAID"),
        "PAID": ("SHIPPED",),
        "SHIPPED": (),
    }
    assert "flow.py:7" in found.source and "ALLOWED" in found.source


def test_validators(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 1, "price": 3.0}]))
    hints = MiningHints(
        validators=(
            Validator("Item", "self.price > 1000", source="clean() m:20"),
            Validator("Item", "value < 0", column="stock", source="validate_stock m:30"),
            Validator("Item", "attrs['price'] > attrs.get('stock') * 100", source="m:40"),
            Validator("Item", "not self.name.isalpha()", source="clean() m:50"),
        )
    )
    result = mine(PROFILE, [a], hints=hints)
    found = exprs(result)
    assert "not (Item.price > 1000)" in found
    assert "not (Item.stock < 0)" in found
    assert "not (Item.price > Item.stock * 100)" in found
    (u,) = [p for p in result.proposals if p.kind == "unchecked"]
    assert "isalpha" in u.spec_object.text and "m:50" in u.source


# ---- seed rows, refutation, proposals ------------------------------------------------------


def test_seed_rows_are_never_mined(runs: Runs) -> None:
    # runs that write nothing: the seed's rows fit many templates, none is proposed
    quiet = runs.run(SEED, SEED)
    result = mine(PROFILE, [quiet])
    assert [p for p in result.proposals if p.source.startswith("observed")] == []
    # a run writing an order only: nothing about items, users or the seed's lines
    a = runs.run(
        with_rows(
            SEED, orders=[{"id": 2, "user_id": 2, "status": "PENDING", "total": 3.0, "note": "n"}]
        )
    )
    b = runs.run(
        with_rows(
            SEED, orders=[{"id": 3, "user_id": 3, "status": "PENDING", "total": 5.0, "note": "m"}]
        )
    )
    tables = {table_of(p.text) for p in mine(PROFILE, [a, b]).proposals}
    assert tables == {"Order"}
    # a seed row the app left as it was is not support either
    p = by_expr(mine(PROFILE, [a, b]), "Order.total > 0")
    assert p.support == 2
    assert {r["id"] for r in p.evidence["rows"]} == {2, 3}


def test_refuted_candidates_are_dropped(runs: Runs) -> None:
    # positive in the first write, negative after a later request of the same run
    a = runs.run(
        with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 4, "price": 3.0}]),
        with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": -1, "price": 3.0}]),
    )
    b = runs.run(with_rows(SEED, items=[{"id": 3, "name": "pad", "stock": 2, "price": 1.0}]))
    result = mine(PROFILE, [a, b])
    assert not any(e.startswith("Item.stock") for e in exprs(result))
    refuted = {r.expr for r in result.refuted}
    assert {"Item.stock >= 0", "Item.stock > 0"} <= refuted
    r = next(r for r in result.refuted if r.expr == "Item.stock >= 0")
    assert r.witness["run"] == "run1" and r.witness["rows"]
    # a static candidate the data refutes is dropped as well
    hints = MiningHints(checks=(CheckConstraint("Item", "stock >= 0"),))
    assert "Item.stock >= 0" not in exprs(mine(PROFILE, [a, b], hints=hints))
    # and so is one the seed contradicts (the seed's pen costs 2.0)
    hints = MiningHints(checks=(CheckConstraint("Item", "price > 2"),))
    assert "Item.price > 2" not in exprs(mine(PROFILE, [b], hints=hints))


def test_min_support(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 4, "price": 3.0}]))
    assert "Item.stock > 0" not in exprs(mine(PROFILE, [a], min_support=2))
    assert "Item.stock > 0" in exprs(mine(PROFILE, [a], min_support=1))


def test_proposals_are_pending_stable_and_serialisable(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, **_order_with_lines(2, 10.0, [(2, 2, 3.0), (3, 1, 4.0)])))
    b = runs.run(with_rows(SEED, **_order_with_lines(2, 7.5, [(2, 3, 2.5)])))
    first = mine(PROFILE, [a, b], model=shop_model())
    second = mine(PROFILE, [b, a], model=shop_model())
    assert first.proposals
    assert {p.status for p in first.proposals} == {"pending"}
    assert [p.id for p in first.proposals] == [p.id for p in second.proposals]
    assert len({p.id for p in first.proposals}) == len(first.proposals)
    data = json.loads(json.dumps(first.to_dict()))
    one = data["proposals"][0]
    assert {"id", "kind", "sentence", "source", "evidence", "support", "status"} <= set(one)
    assert one["status"] == "pending"
    assert data["counts"]["proposed"] == len(first.proposals)
    for p in first.proposals:
        if p.kind == "invariant":
            assert isinstance(p.spec_object, Invariant)
            assert p.sentence == p.spec_object.sentence


# ---- the ASGI recorder ----------------------------------------------------------------------


def test_recorder_snapshots_each_request(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    def factory(db_path: Path) -> Any:
        async def app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                return
            conn = sqlite3.connect(db_path)
            n = conn.execute("SELECT count(*) FROM items").fetchone()[0]
            conn.execute("INSERT INTO items VALUES (?, 'x', ?, 1.0)", (n + 1, n - 1))
            conn.commit()
            conn.close()
            await send({"type": "http.response.start", "status": 201, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        return app

    seed = write_db(tmp_path / "seed.db", SEED)
    recorder = Recorder(tmp_path / "snaps")
    wrapped = recorder.wrap(factory)
    for _ in range(2):
        db = tmp_path / "work.db"
        shutil.copyfile(seed, db)
        client = TestClient(wrapped(db))
        client.post("/items")
        client.post("/items", params={"q": "1"})
    (r1, r2) = recorder.runs
    assert len(r1.snapshots) == 3
    assert r1.requests[0] == {"method": "POST", "path": "/items", "status": 201}
    assert all(p.exists() for p in r1.snapshots + r2.snapshots)
    found = exprs(mine(PROFILE, recorder.runs))
    assert "Item.stock >= 0" in found and "Item.stock > 0" not in found


def test_templates_can_be_chosen(runs: Runs) -> None:
    a = runs.run(with_rows(SEED, items=[{"id": 2, "name": "ink", "stock": 4, "price": 3.0}]))
    b = runs.run(with_rows(SEED, items=[{"id": 3, "name": "pad", "stock": 2, "price": 1.0}]))
    found = exprs(mine(PROFILE, [a, b], templates=("sign",)))
    assert "Item.stock > 0" in found
    assert 'Item.name != ""' not in found
    with pytest.raises(ValueError, match="unknown templates"):
        mine(PROFILE, [a], templates=("daikon",))


# ---- multi-vendor: recall and precision against the reviewed spec ----------------------------

# Every proposal not in bench/specs/multivendor.py, reviewed by hand against the app's
# source (bench/import_repos/multivendor/app): True when the app guarantees it.
MV_EXTRAS: dict[str, bool] = {
    # place_order needs >= 1 line (OrderCreate.items min_length=1), quantity > 0 and
    # price_at_purchase = the product's price > 0: a placed order's total is positive
    "Order.total_amount > 0": True,
    # Product.version starts at 0 (models/product.py default) and only increments
    "Product.version >= 0": True,
    # EmailStr refuses ""; hashed_password is a bcrypt hash
    'User.email != ""': True,
    'User.hashed_password != ""': True,
    # plain `str` request fields: "" is accepted, the probes simply never sent it
    'Product.name != ""': False,
    'Product.category != ""': False,
    'User.full_name != ""': False,
    'Vendor.business_name != ""': False,
    'is_null(Product.description) or Product.description != ""': False,
    # vendor_service.create_vendor sets the caller's role to VENDOR (whatever it was);
    # nothing else writes User.role and registration creates buyers
    "User.role: admin->vendor, buyer->vendor | initial buyer": True,
}


@pytest.mark.slow
def test_multivendor_recall_and_precision(tmp_path: Path) -> None:
    from bench.edit_slice.repos import multivendor as mv
    from bench.import_repos.multivendor import make_app
    from bench.imports.multivendor.profile import profile
    from bench.specs.multivendor import SPEC
    from bench.specs.multivendor_contracts import SPEC as PROBED
    from lattice.importer.extract import extract
    from lattice.spec import check

    root = Path(__file__).resolve().parents[2]
    package = "bench.import_repos.multivendor.app"
    # realistic traffic through the original app: layer R's probes and the scenarios
    recorder = Recorder(tmp_path / "runs")
    check(
        PROBED,
        recorder.wrap(make_app),
        mv.db_factory,
        layers="DR",
        login=mv.login,
        mint_token=mv.mint,
    )
    model = extract(root, package)
    hints = MiningHints(transitions=tuple(transition_maps(root, package, model)))
    result = mine(profile(), recorder.runs, model=model, hints=hints)
    assert {p.status for p in result.proposals} == {"pending"}

    # recall: the Order.status machine and every invariant of the reviewed spec
    wanted = {inv.expr: inv.name for inv in SPEC.invariants}
    found = exprs(result)
    recalled = sorted(name for expr, name in wanted.items() if expr in found)
    (order_machine,) = SPEC.machines
    mined = {(m.table, m.column): m for m in machines(result)}
    machine = mined.get(("Order", "status"))
    machine_ok = (
        machine is not None
        and set(machine.edges) == set(order_machine.edges)
        and set(machine.initial) == set(order_machine.initial)
    )
    # precision: what is proposed beyond the spec, and whether it is true
    in_spec = set(wanted) | {_machine_text(order_machine)}
    extras = sorted(p.text for p in result.proposals if p.text not in in_spec)
    true_extras = [e for e in extras if MV_EXTRAS.get(e)]
    print(
        f"\nmulti-vendor mining: {len(recorder.runs)} runs, {result.snapshots} distinct "
        f"snapshots, {result.app_rows} app-written rows; {len(result.proposals)} proposed, "
        f"{len(result.refuted)} refuted\n"
        f"recall: machine {'yes' if machine_ok else 'no'}, invariants "
        f"{len(recalled)}/{len(wanted)} {recalled}\n"
        f"precision: {len(result.proposals) - len(extras)}/{len(result.proposals)} in the spec, "
        f"{len(result.proposals) - len(extras) + len(true_extras)}/{len(result.proposals)} true"
    )
    assert machine_ok, machine
    for name in ("order_total", "stock_never_negative", "price_positive"):
        assert name in recalled
    assert len(recalled) == len(wanted)  # 7/7
    # every extra proposal has been reviewed (a new one must be judged here)
    assert set(extras) == set(MV_EXTRAS), set(extras) ^ set(MV_EXTRAS)
    assert len(true_extras) == 5


def _machine_text(m: Machine) -> str:
    canonical = Machine(m.table, m.column, tuple(sorted(m.edges)), tuple(sorted(m.initial)))
    return Proposal("machine", canonical, {}, 0, "").text


# ---- helpers ---------------------------------------------------------------------------------


def _f(name: str, type_: str, **kw: Any) -> FieldSpec:
    return FieldSpec(name=name, annotation=type_.lower(), type=type_, **kw)


def shop_model(item_name_choices: tuple[str, ...] = ()) -> RepoModel:
    def table(name: str, cols: Sequence[FieldSpec]) -> TableModel:
        return TableModel(name, SQL[name], "shoppkg.models", tuple(cols), ())

    tables = (
        table(
            "Order",
            [
                _f("id", "Int", primary_key=True),
                _f("user_id", "Int", foreign_key="users.id"),
                _f(
                    "status",
                    "Str",
                    choices=STATES,
                    choice_names=tuple(s.upper() for s in STATES),
                ),
                _f("total", "Float"),
                _f("note", "Str", nullable=True),
            ],
        ),
        table(
            "Line",
            [
                _f("id", "Int", primary_key=True),
                _f("order_id", "Int", foreign_key="orders.id"),
                _f("qty", "Int"),
                _f("price", "Float"),
            ],
        ),
        table(
            "Item",
            [
                _f("id", "Int", primary_key=True),
                _f("name", "Str", choices=item_name_choices),
                _f("stock", "Int", ge=0),
                _f("price", "Float"),
            ],
        ),
        table("User", [_f("id", "Int", primary_key=True), _f("email", "Str", unique=True)]),
        table(
            "Account",
            [
                _f("id", "Int", primary_key=True),
                _f("user_id", "Int", foreign_key="users.id"),
                _f("nickname", "Str", nullable=True),
            ],
        ),
    )
    models = (
        DataModel("ItemCreate", "shoppkg.schemas", (_f("price", "Float", gt=0),), ()),
        DataModel("LineCreate", "shoppkg.schemas", (_f("qty", "Int", le=99),), ()),
        DataModel("ItemRead", "shoppkg.schemas", (_f("price", "Float"),), ()),
    )
    return RepoModel(
        app_package="shoppkg",
        tables=tables,
        models=models,
        dependencies=(),
        routes=(),
        natives=(),
        database_url=None,
        dialect="sqlite",
        exception_handlers=(),
        custom_serialization=(),
    )
