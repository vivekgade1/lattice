"""The loop corpus (spec 2026-09-27 lattice-loops §6 item 5): every item in
tests/fixtures/loops/ is a real-style Python loop and its Lattice equivalent; both run on
generated inputs (seeded random) and must give identical results.

* tabular items: the original on Python rows (or a pandas DataFrame), the Lattice program
  through the polars executor over CSVs and, when it has no `iterate`, through the
  compiled SQL (one statement) over the same rows in SQLite;
* API items: the original FastAPI + SQLModel route through its own app, the Lattice
  program through the Lattice runtime, on copies of one generated database -- same
  responses, same database afterwards, same side effects in the same order;
* the native island: no Lattice program; the loop is rejected with a hint naming a
  native function.
"""

from __future__ import annotations

import ast
import datetime as dt
import importlib
import inspect
import math
import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pandas as pd
import polars as pl
import pytest
from sqlmodel import Session, create_engine
from starlette.testclient import TestClient

from bench.compare import frames_match
from bench.metrics import ProgramMetrics, program_metrics
from lattice.api.ir import lower_api
from lattice.api.runtime import build_app
from lattice.api.sql import register_functions, rels_from_plan
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.ir import lower
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema
from tests.api.test_sql import _csv_cell, sql_frame
from tests.fixtures.loops import API, ITEMS, NATIVE_ISLANDS, TABULAR

SEEDS = range(25)
API_SEEDS = range(8)


def item(name: str) -> ModuleType:
    return importlib.import_module(f"tests.fixtures.loops.{name}")


# ---- comparing rows --------------------------------------------------------------------------


def _plain(value: object) -> object:
    """One cell as a plain Python value: numpy scalars unwrapped, NaN/NaT as None,
    timestamps as dates."""
    if hasattr(value, "item") and not isinstance(value, str | bytes):
        value = value.item()  # type: ignore[union-attr]
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, dt.datetime):
        return value.date()
    return value


def _rows(result: object) -> list[dict]:
    if isinstance(result, pd.DataFrame):
        result = result.to_dict("records")
    elif isinstance(result, pl.DataFrame):
        result = result.to_dicts()
    return [{k: _plain(v) for k, v in row.items()} for row in result]  # type: ignore[union-attr]


def _same(a: object, b: object) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)  # type: ignore[arg-type]
    return a == b


def assert_same_rows(
    expected: list[dict], actual: list[dict], key: tuple[str, ...], ordered: bool, where: str
) -> None:
    assert len(expected) == len(actual), f"{where}: {len(expected)} vs {len(actual)} rows"
    if expected:
        assert set(expected[0]) == set(actual[0]), f"{where}: columns differ"
    if not ordered:
        expected = sorted(expected, key=lambda r: tuple(r[k] for k in key))
        actual = sorted(actual, key=lambda r: tuple(r[k] for k in key))
    for want, got in zip(expected, actual, strict=True):
        for column, value in want.items():
            assert _same(value, got[column]), f"{where}: {column}: {want} != {got}"


# ---- running a tabular item ------------------------------------------------------------------


def _write_inputs(root: Path, module: ModuleType, inputs: dict[str, list[dict]]) -> None:
    for stem, schema_name in module.INPUTS.items():
        schema: Schema = module.SCHEMAS[schema_name]
        lines = [",".join(schema.names())]
        lines += [",".join(_csv_cell(r[c]) for c in schema.names()) for r in inputs[stem]]
        (root / f"{stem}.csv").write_text("\n".join(lines) + "\n")


_SQL_TYPES = {Scalar.INT: "INTEGER", Scalar.FLOAT: "REAL", Scalar.BOOL: "INTEGER"}


def _sqlite(module: ModuleType, inputs: dict[str, list[dict]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    register_functions(conn)
    for stem, schema_name in module.INPUTS.items():
        schema: Schema = module.SCHEMAS[schema_name]
        cols = ", ".join(f"{n} {_SQL_TYPES.get(s, 'TEXT')}" for n, s in schema.columns)
        conn.execute(f"CREATE TABLE {stem} ({cols})")
        marks = ", ".join("?" for _ in schema.columns)
        conn.executemany(
            f"INSERT INTO {stem} VALUES ({marks})",
            [
                tuple(v.isoformat() if isinstance(v, dt.date) else v for v in r.values())
                for r in ({c: row[c] for c in schema.names()} for row in inputs[stem])
            ],
        )
    return conn


def _run_original(module: ModuleType, inputs: dict[str, list[dict]]) -> list[dict]:
    args = [[dict(r) for r in inputs[stem]] for stem in module.INPUTS]
    if getattr(module, "PANDAS", False):
        frames = []
        for stem, rows in zip(module.INPUTS, args, strict=True):
            names = module.SCHEMAS[module.INPUTS[stem]].names()
            frames.append(pd.DataFrame(rows, columns=names))
        return _rows(module.original(*frames))
    return _rows(module.original(*args))


def run_tabular(module: ModuleType, inputs: dict[str, list[dict]], root: Path) -> pl.DataFrame:
    """The Lattice program's output through polars and, without iterate, through SQL (the
    two frames equal); returns the polars frame."""
    profile = Profile(schemas=module.SCHEMAS)
    root.mkdir(parents=True, exist_ok=True)
    _write_inputs(root, module, inputs)
    check, run = run_source(module.LATTICE, profile, root)
    assert run is not None, [d.render() for d in check.diagnostics]
    frame = run.outputs["out.csv"]
    assert check.program is not None
    plan = lower(check.program)
    if all(n.op != "iterate" for n in plan.nodes):
        tables = {f"{s}.csv": (s, module.SCHEMAS[n]) for s, n in module.INPUTS.items()}
        written = next(n for n in plan.nodes if n.op == "write")
        rels = rels_from_plan(plan.nodes, tables)
        conn = _sqlite(module, inputs)
        try:
            actual = sql_frame(conn, rels, written.inputs[0])
        finally:
            conn.close()
        ordered = getattr(module, "ORDERED", False)
        ok, why = frames_match(actual, frame, ordered=ordered, rtol=1e-9, atol=1e-9)
        assert ok, f"SQL differs from polars: {why}\n{actual}\n{frame}"
    return frame


@pytest.mark.parametrize("name", TABULAR)
def test_tabular_item_is_equivalent_on_generated_inputs(name: str, tmp_path: Path) -> None:
    module = item(name)
    nonempty = 0
    for seed in SEEDS:
        inputs = module.generate(random.Random(seed))
        expected = _run_original(module, inputs)
        frame = run_tabular(module, inputs, tmp_path / f"seed{seed}")
        assert_same_rows(
            expected,
            _rows(frame),
            module.KEY,
            getattr(module, "ORDERED", False),
            f"{name} seed {seed}",
        )
        nonempty += bool(expected)
    assert nonempty >= len(SEEDS) // 2, "the generator rarely produces a non-empty result"


# ---- running an API item ---------------------------------------------------------------------


def _send(client: TestClient, req: dict) -> tuple[int, object]:
    resp = client.request(
        req["method"],
        req["url"],
        params=req.get("params"),
        json=req.get("json"),
        headers=req.get("headers"),
    )
    return resp.status_code, resp.json() if resp.content else None


def _table_rows(db: Path, tables: tuple[str, ...]) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    finally:
        conn.close()


def _serve_original(module: ModuleType, db: Path, reqs: list[dict]) -> list[tuple[int, object]]:
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})

    def session() -> Iterator[Session]:
        with Session(engine) as s:
            yield s

    module.app.dependency_overrides[module.get_session] = session
    try:
        with TestClient(module.app) as client:
            return [_send(client, r) for r in reqs]
    finally:
        module.app.dependency_overrides.clear()
        engine.dispose()


def _serve_lattice(module: ModuleType, db: Path, reqs: list[dict]) -> list[tuple[int, object]]:
    check = check_source(module.LATTICE, module.PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    app = build_app(lower_api(check.program), db, natives=module.NATIVES)
    with TestClient(app) as client:
        return [_send(client, r) for r in reqs]


@pytest.mark.parametrize("name", API)
def test_api_item_is_equivalent_on_generated_databases(name: str, tmp_path: Path) -> None:
    module = item(name)
    sent = getattr(module, "SENT", None)
    effects = 0
    for seed in API_SEEDS:
        rng = random.Random(seed)
        inputs = module.generate(rng)
        reqs = module.requests(rng, inputs)
        runs = {}
        for arm, serve in (("original", _serve_original), ("lattice", _serve_lattice)):
            db = tmp_path / f"{arm}{seed}.db"
            module.build_db(db, inputs)
            if sent is not None:
                sent.clear()
            responses = serve(module, db, reqs)
            runs[arm] = (responses, _table_rows(db, module.COMPARED_TABLES), list(sent or []))
        (orig_resp, orig_db, orig_sent), (lat_resp, lat_db, lat_sent) = runs.values()
        where = f"{name} seed {seed}"
        for req, (s1, b1), (s2, b2) in zip(reqs, orig_resp, lat_resp, strict=True):
            assert s1 == s2, f"{where} {req}: status {s1} vs {s2}: {b1} / {b2}"
            if s1 != 422:  # validation bodies differ by design (FastAPI vs Lattice detail)
                assert b1 == b2, f"{where} {req}: {b1} != {b2}"
        assert orig_db == lat_db, where
        assert orig_sent == lat_sent, where
        effects += len(orig_sent)
    if sent is not None:
        assert effects > 0, "no side effect was ever exercised"


# ---- every item ------------------------------------------------------------------------------


def test_the_corpus_lists_every_item_file() -> None:
    folder = Path(__file__).resolve().parents[1] / "fixtures" / "loops"
    files = sorted(p.stem for p in folder.glob("l[0-9][0-9]_*.py"))
    assert files == list(ITEMS)
    assert len(ITEMS) >= 12


@pytest.mark.parametrize("name", ITEMS)
def test_every_item_describes_itself(name: str) -> None:
    module = item(name)
    doc = module.__doc__ or ""
    assert "Loop shape:" in doc and "Construct:" in doc, name
    assert module.SHAPE and module.CONSTRUCT
    assert callable(module.original)
    body = ast.parse(inspect.getsource(module.original)).body[0]
    assert any(isinstance(n, ast.For | ast.While) for n in ast.walk(body)), "no loop"


@pytest.mark.parametrize("name", TABULAR + API)
def test_every_lattice_program_checks_clean(name: str) -> None:
    module = item(name)
    profile = module.PROFILE if name in API else Profile(schemas=module.SCHEMAS)
    result = check_source(module.LATTICE, profile)
    assert result.ok and not result.diagnostics, [d.render() for d in result.diagnostics]


def test_ties_are_flagged_until_a_key_joins_the_order() -> None:
    module = item("l11_ties_order_key")
    result = check_source(module.LATTICE_WITH_TIES, Profile(schemas=module.SCHEMAS))
    (diag,) = [d for d in result.diagnostics if d.code == "L431"]
    assert diag.severity is Severity.WARNING
    assert "order" in (diag.hint or "")


# ---- line counts (docs/results/2026-09-26-bench-smoke.md) ------------------------------------


def line_counts(name: str) -> tuple[ProgramMetrics, ProgramMetrics | None]:
    """`bench.metrics.program_metrics` (non-blank, non-comment lines; AST nodes) of an
    item's original function (with its docstring) and of its Lattice program (None for
    a native island)."""
    module = item(name)
    original = program_metrics(inspect.getsource(module.original))
    lattice = getattr(module, "LATTICE", None)
    return original, None if lattice is None else program_metrics(lattice)


def test_line_counts_are_measurable() -> None:
    for name in ITEMS:
        original, lattice = line_counts(name)
        assert original.lines > 0 and original.ast_nodes > 0
        assert (lattice is None) == (name in NATIVE_ISLANDS)


# ---- the native island -----------------------------------------------------------------------


@pytest.mark.parametrize("name", NATIVE_ISLANDS)
def test_native_island_loop_is_rejected_with_a_native_hint(name: str) -> None:
    module = item(name)
    assert not hasattr(module, "LATTICE")
    function = ast.parse(inspect.getsource(module.original)).body[0]
    assert isinstance(function, ast.FunctionDef)
    loop = next(s for s in function.body if isinstance(s, ast.For))
    source = "# lattice: v1\n" + ast.unparse(loop) + "\n"
    result = check_source(source, Profile(schemas={}))
    (diag,) = [d for d in result.diagnostics if d.code == "L127"]
    assert "native function" in (diag.hint or "")
    # the original itself runs, deterministically, on generated inputs
    for seed in SEEDS:
        inputs = module.generate(random.Random(seed))
        assert module.original(**inputs) == module.original(**inputs)


@pytest.mark.parametrize("name", ITEMS)
def test_the_original_loop_gets_a_hint_naming_the_construct(name: str) -> None:
    """The importer's first cue: checking an item's original loop as Lattice gives L127
    (or L121 for a comprehension) with a hint naming the item's construct."""
    module = item(name)
    function = ast.parse(inspect.getsource(module.original)).body[0]
    assert isinstance(function, ast.FunctionDef)
    loops = [s for s in ast.walk(function) if isinstance(s, ast.For | ast.While)]
    profile = module.PROFILE if name in API else Profile(schemas={})
    hints = []
    for loop in loops:
        result = check_source("# lattice: v1\n" + ast.unparse(loop) + "\n", profile)
        hints += [d.hint or "" for d in result.diagnostics if d.code == "L127"]
    assert any(module.HINT in h for h in hints), hints
