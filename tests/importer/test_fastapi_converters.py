"""Starlette path converters in FastAPI routes (``{id:int}``, ``{x:uuid}``, ``{name:str}``,
``{path:path}``): found by T1-3's import of a real app, whose SPA catch-all ``GET
/{path:path}`` made ``review ... emit`` refuse the spec (S020: the contract path's hole
was ``path:path``, not ``path``).

The front end writes each hole as ``{name}`` (the spec's form; the spec checker matches a
hole against one path segment) and keeps the converter's type (int -> Int, float ->
Float, uuid -> Uuid; str leaves the annotation's type, Str when there is none). A
``{name:path}`` hole matches across ``/``, which a spec hole cannot say, so that route is
set apart (``excluded_routes``, kind ``unsupported``) instead of emitted as a narrower
contract; the rest of the import goes on.

The app is written to a temporary directory; the end-to-end test runs the CLI as a
subprocess (rules-first import, then ``review DIR emit``), as the CLI runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lattice.importer.appmodel import ExcludedRoute
from lattice.importer.frontends.fastapi import FRONT_END

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "conv_app"

ROUTES = """
    from uuid import UUID

    from fastapi import APIRouter

    router = APIRouter(prefix="/api")


    @router.get("/health")
    def health() -> dict:
        return {"ok": True}


    @router.get("/items/{item_id:int}")
    def read_item(item_id: int) -> dict:
        return {"id": item_id}


    @router.get("/ratios/{r:float}")
    def read_ratio(r: float) -> dict:
        return {"r": r}


    @router.get("/things/{thing:uuid}")
    def read_thing(thing: UUID) -> dict:
        return {"thing": str(thing)}


    @router.get("/names/{name:str}")
    def read_name(name) -> dict:
        return {"name": name}
"""

INIT = """
    from fastapi import FastAPI

    from conv_app.routes import router


    def make_app(db_path):
        app = FastAPI()
        app.include_router(router)

        # an SPA catch-all, registered last (as the T1-3 app does)
        @app.get("/{path:path}")
        def spa(path: str) -> dict:
            return {"path": path}

        return app
"""


@pytest.fixture
def app_root(tmp_path: Path) -> Path:
    pkg = tmp_path / PACKAGE
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent(INIT))
    (pkg / "routes.py").write_text(textwrap.dedent(ROUTES))
    return tmp_path


def test_converter_holes_are_plain_and_typed(app_root: Path) -> None:
    model = FRONT_END.extract(app_root, PACKAGE)
    paths = {r.path: r for r in model.routes}
    assert set(paths) == {
        "/api/health",
        "/api/items/{item_id}",
        "/api/ratios/{r}",
        "/api/things/{thing}",
        "/api/names/{name}",
    }
    holes = {
        p.name: (p.type, p.source) for r in model.routes for p in r.params if p.source == "path"
    }
    assert holes == {
        "item_id": ("Int", "path"),
        "r": ("Float", "path"),
        "thing": ("Uuid", "path"),
        "name": ("Str", "path"),  # unannotated: the str converter's type
    }


def test_catch_all_path_converter_is_set_apart(app_root: Path) -> None:
    model = FRONT_END.extract(app_root, PACKAGE)
    assert not [r for r in model.routes if "path" in r.path]
    assert model.excluded_routes == (
        ExcludedRoute(
            "GET",
            "/{path:path}",
            "unsupported",
            "unsupported: catch-all path converter ({path:path} matches across '/'; a spec "
            "path hole is one segment)",
        ),
    )


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", *args], cwd=cwd, env=env, capture_output=True, text=True,
        timeout=600, check=False,
    )  # fmt: skip


def test_rules_import_emits_a_valid_spec(app_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "import"
    done = _run(
        ["lattice.importer", str(app_root), "--package", PACKAGE,
         "--app-factory", f"{PACKAGE}:make_app", "--rules-only", "--out", str(out)],
        app_root,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    report = json.loads((out / "report.json").read_text())
    rules = report["rules"]
    assert rules["excluded_routes"] == [
        {"method": "GET", "path": "/{path:path}", "kind": "unsupported",
         "reason": "unsupported: catch-all path converter ({path:path} matches across '/'; "
                   "a spec path hole is one segment)"},
    ]  # fmt: skip
    assert rules["summary"]["routes"]["total"] == 5
    assert rules["summary"]["items"]["refuted"] == 0
    # each converter route is probed with a value its converter matches, so the probe
    # reaches that route's handler (before the fix the uuid hole got "1", missed the
    # converter and fell through to the catch-all, which answered 200 too)
    answers = {
        p["route"]: (p["request"]["path"], p["status"], p["body"]) for p in rules["probes"].values()
    }
    uuid_path = answers["GET /api/things/{thing}"][0]
    assert answers["GET /api/items/{item_id}"] == ("/api/items/1", 200, {"id": 1})
    assert answers["GET /api/ratios/{r}"] == ("/api/ratios/1.0", 200, {"r": 1.0})
    assert answers["GET /api/things/{thing}"] == (
        uuid_path, 200, {"thing": uuid_path.rsplit("/", 1)[1]},
    )  # fmt: skip
    status, body = answers["GET /api/names/{name}"][1:]
    assert status == 200 and set(body) == {"name"}  # the handler, not the catch-all

    emitted = _run(["lattice.importer", "review", str(out), "emit"], app_root)
    assert emitted.returncode == 0, emitted.stderr[-4000:]
    spec = (out / "spec.py").read_text()
    for path in ("/api/items/{item_id}", "/api/ratios/{r}", "/api/things/{thing}",
                 "/api/names/{name}"):  # fmt: skip
        assert f"'{path}'" in spec or f'"{path}"' in spec
    assert "path:path" not in spec and ":int}" not in spec and ":uuid}" not in spec
