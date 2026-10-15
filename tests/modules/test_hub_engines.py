"""Engines of several projects in one process (marketplace prereg 2.8 and Amendment 1 B;
M-hub H1-H5 and M-hub6): a project's engines live in a registry of its own, two projects may
declare the same engine name differently, and a thread that never activated a project's
registry sees the built-in engines only (fails closed)."""

from __future__ import annotations

import ast
import concurrent.futures
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from lattice import modules as mods
from lattice.modules import install, load_project_engines, project_engines
from lattice.spec import engines as eng
from lattice.spec.engines import REGISTRY, EngineError
from lattice.studio.server import StubGenerator, make_hub_server, make_server
from tests.studio.test_server import make_project, request

ROOT = Path(__file__).resolve().parents[2]
MARIADB = ROOT / "docs/examples/engines/mariadb.toml"
BUILTINS = ["postgres", "mysql", "clickhouse"]
ADAPTER = """
from lattice.spec.db import Database


class FakeDb(Database):
    dialect = "fakedb"
    origin = "{who}"

    def __init__(self, url):
        self.url = url

    def close(self):
        pass
"""


@pytest.fixture(autouse=True)
def _scoped_registry():
    with REGISTRY.scoped():
        yield


def pg_module(parent: Path, name: str = "pg-a", engine: str = "pga") -> Path:
    d = parent / name
    d.mkdir(parents=True)
    text = (eng.HERE / "postgres.toml").read_text()
    text = (
        text.replace('name = "postgres"', f'name = "{engine}"')
        .replace('aliases = ["postgresql"]', "aliases = []")
        .replace('url_schemes = ["postgres", "postgresql"]', f'url_schemes = ["{engine}"]')
    )
    (d / "engine.toml").write_text(text)
    (d / "lattice-module.toml").write_text(
        f'name = "{name}"\nversion = "1.0.0"\nkind = "engine"\ndescriptor = "engine.toml"\n'
    )
    return d


def two_projects(tmp_path: Path) -> tuple[Path, Path]:
    a = make_project(tmp_path / "a", app_limit=5, repo=False)
    b = make_project(tmp_path / "b", app_limit=5, repo=False)
    (a / "engines").mkdir()
    (a / "engines" / "mariadb.toml").write_text(MARIADB.read_text())
    (b / "engines").mkdir()
    (b / "engines" / "mariadb.toml").write_text(
        MARIADB.read_text().replace("mariadb:11", "mariadb:10.11")
    )
    for p in (a, b):
        with (p / "studio.toml").open("a") as f:
            f.write('\n[engines]\npaths = ["engines/mariadb.toml"]\n')
    install(a, str(pg_module(tmp_path / "mods")), yes=True, out=lambda s: None)
    return a, b


def serve(srv: Any) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


def hub(a: Path, b: Path) -> Any:
    return make_hub_server([("a", a), ("b", b)], 0, generator_factory=lambda _m: StubGenerator())


def get_engines(base: str, name: str) -> dict[str, Any]:
    status, out = request("GET", f"{base}/p/{name}/api/engines")
    assert status == 200
    return out  # type: ignore[no-any-return]


def names(body: dict[str, Any]) -> list[str]:
    return [r["name"] for r in body["engines"]]


def maria_image(body: dict[str, Any]) -> str:
    return next(r for r in body["engines"] if r["name"] == "mariadb")["server"]["image"]


# ---- H1, H3: the API through one hub ----------------------------------------------------------


def test_each_project_lists_its_own_engines_in_any_order(tmp_path):
    a, b = two_projects(tmp_path)
    bodies: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}
    for order in ("ABAB", "BABA", "AABB"):
        srv = hub(a, b)
        base = serve(srv)
        try:
            for ch in order:
                name = ch.lower()
                bodies[name].append(get_engines(base, name))
        finally:
            stop(srv)
    for name in ("a", "b"):
        first = bodies[name][0]
        assert all(body == first for body in bodies[name])
    body_a, body_b = bodies["a"][0], bodies["b"][0]
    assert names(body_b) == ["sqlite", *BUILTINS, "mariadb"]
    assert maria_image(body_b) == "mariadb:10.11" and body_b["errors"] == []
    assert names(body_a) == ["sqlite", *BUILTINS, "mariadb", "pga"]
    assert maria_image(body_a) == "mariadb:11" and body_a["errors"] == []
    # the same bodies as each project served alone
    for name, root in (("a", a), ("b", b)):
        srv = make_server(root, 0, generator=StubGenerator())
        base = serve(srv)
        try:
            status, alone = request("GET", base + "/api/engines")
        finally:
            stop(srv)
        assert status == 200 and alone == bodies[name][0]


def test_threads_and_interleaved_requests_see_their_own_project(tmp_path):
    a, b = two_projects(tmp_path)
    srv = hub(a, b)
    base = serve(srv)
    try:
        want = {"a": get_engines(base, "a"), "b": get_engines(base, "b")}
        failures: list[str] = []

        def worker(k: int) -> None:
            for i in range(25):
                name = "ab"[(i + k) % 2]
                body = get_engines(base, name)
                if body != want[name]:
                    failures.append(f"thread {k} request {i}: {name} got {names(body)}")

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert failures == []
    finally:
        stop(srv)


# ---- H2: one thread, A then B -----------------------------------------------------------------


def test_one_thread_switches_projects_without_leaking(tmp_path):
    a, b = two_projects(tmp_path)
    srv = hub(a, b)
    try:
        h = srv.hub
        h.get("a").harness.check_kwargs()
        assert eng.find("mariadb").server.image == "mariadb:11"
        assert eng.find("pga") is not None and "pga" in eng.database_names()
        h.get("b").harness.check_kwargs()
        assert eng.find("mariadb").server.image == "mariadb:10.11"
        assert eng.find("pga") is None and "pga" not in eng.database_names()
        assert eng._BASE.names() == tuple(BUILTINS)
    finally:
        srv.server_close()


# ---- H4: adapters ----------------------------------------------------------------------------


def adapter_project(root: Path, who: str) -> Path:
    (root / "engines").mkdir(parents=True)
    (root / "engines" / "adapter.py").write_text(ADAPTER.replace("{who}", who))
    text = (eng.HERE / "mysql.toml").read_text()
    text = (
        text.replace('name = "mysql"', 'name = "fakedb"')
        .replace('url_schemes = ["mysql", "mariadb"]', 'url_schemes = ["fakedb"]')
        .replace('url_template = "mysql://', 'url_template = "fakedb://')
        .replace('adapter = "mysql"', 'adapter = "adapter:FakeDb"')
    )
    (root / "engines" / "fakedb.toml").write_text(text)
    return root


def test_two_projects_with_an_adapter_of_the_same_name(tmp_path, monkeypatch):
    a = adapter_project(tmp_path / "pa", "A")
    b = adapter_project(tmp_path / "pb", "B")
    table = {"paths": ["engines/fakedb.toml"]}
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "adapter", raising=False)
    try:
        with project_engines(a, table) as pa:
            assert pa.errors == []
            cls = eng.find("fakedb").adapter_class()
            assert cls.origin == "A"
        with project_engines(b, table) as pb:
            assert pb.errors == []
            with pytest.raises(
                EngineError,
                match="^engine 'fakedb': adapter module 'adapter' is already imported from "
                ".*pa/engines/adapter.py; give this engine's adapter module a distinct name$",
            ):
                eng.find("fakedb").adapter_class()
    finally:
        sys.modules.pop("adapter", None)


# ---- H5: Studio loads a project's engines right before it looks them up -----------------------


def test_each_studio_lookup_follows_a_load_in_the_same_function():
    src = (ROOT / "lattice/studio/server.py").read_text()
    tree = ast.parse(src)
    checked = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        loads = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == "load_project_engines"
        ]
        if not loads:
            continue
        checked += 1
        lookups = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "")
            in ("find", "resolve", "entries", "database_names", "engine", "by_scheme")
        ]
        assert all(min(loads) < ln for ln in lookups), fn.name
    assert checked == 3, "the three load_project_engines sites of Studio"


# ---- M-hub6: fail closed -----------------------------------------------------------------------


def run_in_thread(fn):
    out: dict[str, Any] = {}

    def target() -> None:
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            out["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    t.join()
    assert "error" not in out, out.get("error")
    return out.get("value")


def test_a_thread_that_never_loaded_sees_the_builtins_only(tmp_path):
    a, _b = two_projects(tmp_path)
    loaded = run_in_thread(lambda: load_project_engines(a, {"paths": ["engines/mariadb.toml"]}))
    assert [r.engine.name for r in loaded.registry.entries()] == [*BUILTINS, "mariadb", "pga"]

    def other() -> tuple[tuple[str, ...], bool, bool, tuple[str, ...]]:
        return (
            REGISTRY.names(),
            eng.find("mariadb") is None,
            eng.find("pga") is None,
            eng.database_names(),
        )

    names_, no_maria, no_pga, dbs = run_in_thread(other)
    assert names_ == tuple(BUILTINS)
    assert no_maria and no_pga and dbs == ("sqlite", *BUILTINS)
    assert REGISTRY.names() == tuple(BUILTINS)  # this thread never loaded either


def test_a_pooled_thread_serves_a_then_b_then_nothing(tmp_path):
    a, b = two_projects(tmp_path)
    table = {"paths": ["engines/mariadb.toml"]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

        def job_a() -> tuple[str, bool]:
            with project_engines(a, table):
                return eng.find("mariadb").server.image, eng.find("pga") is not None

        def job_b() -> tuple[str, bool]:
            with project_engines(b, table):
                return eng.find("mariadb").server.image, eng.find("pga") is None

        def job_idle() -> tuple[str, ...]:
            return REGISTRY.names()

        def job_raises() -> str:
            try:
                with project_engines(a, table):
                    raise RuntimeError("boom")
            except RuntimeError:
                return "raised"

        assert pool.submit(job_a).result() == ("mariadb:11", True)
        assert pool.submit(job_idle).result() == tuple(BUILTINS)
        assert pool.submit(job_b).result() == ("mariadb:10.11", True)
        assert pool.submit(job_idle).result() == tuple(BUILTINS)
        assert pool.submit(job_raises).result() == "raised"
        assert pool.submit(job_idle).result() == tuple(BUILTINS)  # restored after the exception


def test_scoped_forgets_what_was_activated_and_using_restores_the_previous(tmp_path):
    a, b = two_projects(tmp_path)
    table = {"paths": ["engines/mariadb.toml"]}
    with REGISTRY.scoped():
        load_project_engines(a, table)
        assert eng.find("mariadb").server.image == "mariadb:11"
    assert eng.find("mariadb") is None and REGISTRY.names() == tuple(BUILTINS)
    outer = eng.project_registry()
    with eng.using(outer):
        with project_engines(b, table):
            assert eng.find("mariadb").server.image == "mariadb:10.11"
        assert REGISTRY.current() is outer  # restored to the enclosing scope, not to the base
    assert REGISTRY.current() is eng._BASE


def test_cli_flags_still_register_in_the_process_registry(tmp_path):
    (tmp_path / "m.toml").write_text(MARIADB.read_text())
    eng.load_engine_file(tmp_path / "m.toml", "project")
    assert eng._BASE.entry("mariadb").source == "project"
    # a project loaded afterwards, in this context, adds to ITS registry and not to the base
    shutil.copy(MARIADB, tmp_path / "other.toml")
    out = mods.load_project_engines(tmp_path, {"paths": ["other.toml"]})
    assert out.errors == []  # the same descriptor registered twice is a no-op, no clash
    assert eng._BASE.names() == (*BUILTINS, "mariadb")
