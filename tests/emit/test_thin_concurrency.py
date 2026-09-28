"""App construction is thread-safe (the edit-slice harness's ``--workers`` builds
emitted and variant apps from several threads at once).

Regression: ``make_emitted_app`` used to swap the module-global
``bench.template_app.app.main.api_router`` for an empty router while it built the
template app, then restore it. Two threads interleaving that read-swap-restore could
leave the *empty* router installed for the rest of the process: every later template
app then served no API routes (``login as 'alice' failed: 404``).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench.template_app.seed import build_template_db
from bench.template_profile import template_profile, template_sources
from lattice.emit import EmitResult, emit_into_template
from lattice.emit.thin import _template_routes, make_emitted_app

THREADS = 8
ROUNDS = 3


@pytest.fixture(scope="module")
def emitted(tmp_path_factory: pytest.TempPathFactory) -> EmitResult:
    out = tmp_path_factory.mktemp("emit-conc") / "template_emit"
    return emit_into_template(template_sources(), template_profile(), out)


def _paths(app) -> set[tuple[str, str]]:
    try:
        with TestClient(app) as client:
            doc = client.get("/api/v1/openapi.json").json()
    finally:
        app.state.engine.dispose()
    return {(m.upper(), p) for p, ops in doc["paths"].items() for m in ops}


def _template_intact(tmp: Path) -> None:
    from bench.template_app import make_app
    from bench.template_app.app import main as template_main

    assert template_main.api_router.routes, "the template's api_router was emptied"
    assert _template_routes(), "the template's routes are gone"
    paths = _paths(make_app(tmp / "plain.db"))
    assert ("GET", "/api/v1/utils/health-check/") in paths
    assert ("POST", "/api/v1/login/access-token") in paths


def test_emitted_and_template_apps_build_in_parallel(emitted: EmitResult, tmp_path: Path):
    from bench.template_app import make_app

    seed = build_template_db(tmp_path / "seed.db")
    barrier = threading.Barrier(THREADS)
    expected_emitted = {tuple(r.split(" ")) for r in emitted.routes_emitted}
    expected_kept = {tuple(r.split(" ")) for r in emitted.routes_kept}

    def work(i: int) -> list[str]:
        problems: list[str] = []
        barrier.wait()
        for k in range(ROUNDS):
            db = tmp_path / f"t{i}-{k}.db"
            db.write_bytes(seed.read_bytes())
            if (i + k) % 2:
                paths = _paths(make_emitted_app(emitted.out_dir, db))
                if not expected_emitted <= paths:
                    problems.append(f"emitted app {i}/{k} lacks emitted routes")
                if not expected_kept <= paths:
                    problems.append(f"emitted app {i}/{k} lacks kept template routes")
                if len(paths) != len(expected_emitted) + len(expected_kept):
                    problems.append(f"emitted app {i}/{k} has {len(paths)} routes")
            else:
                paths = _paths(make_app(db))
                if ("GET", "/api/v1/utils/health-check/") not in paths:
                    problems.append(f"template app {i}/{k} lacks its API routes")
            if not _template_routes():
                problems.append(f"the template routes vanished during {i}/{k}")
        return problems

    with ThreadPoolExecutor(THREADS) as ex:
        problems = [p for ps in ex.map(work, range(THREADS)) for p in ps]
    assert problems == []
    _template_intact(tmp_path)


def test_variant_apps_build_in_parallel(tmp_path: Path):
    from bench.edit_slice.reference import reference_files
    from bench.edit_slice.variant import load_variant

    # t1_search adds a search filter to GET /items/; the plain template has none
    overlays = [{}, dict(reference_files("t1_search"))]
    barrier = threading.Barrier(THREADS)

    def work(i: int):
        barrier.wait()
        v = load_variant(overlays[i % 2])
        app = v.make_app(tmp_path / f"v{i}.db")
        try:
            with TestClient(app) as client:
                spec = client.get("/api/v1/openapi.json").json()
        finally:
            app.state.engine.dispose()
        params = {p["name"] for p in spec["paths"]["/api/v1/items/"]["get"].get("parameters", [])}
        return i % 2, v, params

    with ThreadPoolExecutor(THREADS) as ex:
        results = list(ex.map(work, range(THREADS)))
    for kind in (0, 1):
        same = {id(v) for k, v, _ in results if k == kind}
        assert len(same) == 1, "one content must load as one Variant"
    plain = {frozenset(p) for k, _, p in results if k == 0}
    search = {frozenset(p) for k, _, p in results if k == 1}
    assert len(plain) == len(search) == 1
    assert plain != search
    _template_intact(tmp_path)


def test_load_variant_is_atomic_per_content():
    """Concurrent first loads of one new content share one Variant (its SQLModel
    registry is the one the finder executes the modules against)."""
    from bench.edit_slice.variant import load_variant

    barrier = threading.Barrier(THREADS)
    files = {"_conc_probe.py": f"X = {id(barrier)}\n"}

    def work(_: int):
        barrier.wait()
        return load_variant(files)

    with ThreadPoolExecutor(THREADS) as ex:
        loaded = list(ex.map(work, range(THREADS)))
    assert len({id(v) for v in loaded}) == 1
