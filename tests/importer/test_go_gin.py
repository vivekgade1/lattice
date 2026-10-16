"""The Go (Gin + GORM) importer front end (``lattice.importer.frontends.go_gin``): detection,
selection, the extractor's JSON mapped to the app model, the lazy loading (a Python import
never reaches it) and the reconcile guards. The extractor itself is tested in
``tests/gocheck/test_goextract.py``. Skipped when ``go`` is not on PATH."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lattice.importer import frontends
from lattice.importer.appmodel import RepoModel
from lattice.importer.frontends import go_gin
from lattice.importer.frontends.go_gin import GoExtractError, GoGinFrontEnd, model_from_extract

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

FIXTURES = Path(__file__).resolve().parents[1] / "gocheck" / "fixtures"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fe() -> GoGinFrontEnd:
    return GoGinFrontEnd()


@pytest.fixture(scope="module")
def model(fe: GoGinFrontEnd) -> RepoModel:
    return fe.extract(FIXTURES, "goextract")


def test_detect_requires_gin_and_gorm_in_go_mod(tmp_path, fe):
    assert fe.detect(FIXTURES, "goextract")
    assert not fe.detect(tmp_path, ".")  # no go.mod: False, no raise
    (tmp_path / "go.mod").write_text(
        "module x\n\ngo 1.22\n\nrequire github.com/gin-gonic/gin v1.9.1\n"
    )
    assert not fe.detect(tmp_path, ".")
    (tmp_path / "go.mod").write_text(
        "module x\n\ngo 1.22\n\nrequire (\n"
        "\tgithub.com/gin-gonic/gin v1.9.1\n\tgorm.io/gorm v1.25.5\n)\n"
    )
    assert fe.detect(tmp_path, ".")


def test_select_by_name_and_by_detection_after_python_declined():
    assert frontends.select("gin").name == "gin"
    fe = frontends.select(None, FIXTURES, "goextract")
    assert fe.name == "gin" and fe is go_gin.FRONT_END
    assert [f.name for f in frontends.front_ends()][0] == "fastapi"


def test_a_python_import_never_loads_the_go_front_end():
    code = textwrap.dedent("""
        import sys
        sys.dont_write_bytecode = True
        from lattice.importer import frontends
        import lattice.importer.extract
        frontends.select("fastapi")
        frontends.select("django")
        assert "lattice.importer.frontends.go_gin" not in sys.modules
        frontends.select("gin")
        assert "lattice.importer.frontends.go_gin" in sys.modules
    """)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={"PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr


def test_extract_maps_the_json_to_the_app_model(model, fe):
    assert (model.framework, model.orm, model.app_package) == ("gin", "gorm", "example.com/gx")
    assert model.database_url is None and model.dialect == "sqlite"
    assert sorted(t.name for t in model.tables) == ["Audit", "Item", "Tag", "User"]
    tag = next(t for t in model.tables if t.name == "Tag")
    assert tag.table_name == "item_tags" and tag.module == "example.com/gx/models"
    assert tag.unique_together == (("item_id", "label"),)
    cols = {c.name: c for c in tag.columns}
    assert cols["item_id"].foreign_key == "items.id" and cols["seen"].type == "Date"
    assert any(c.kind == "unique" and c.table == "Tag" for c in model.constraints)
    assert len(model.routes) == 9
    r = next(r for r in model.routes if (r.method, r.path) == ("POST", "/api/items"))
    assert r.auth == "Customer" and r.status_code == 201
    assert r.view == "example.com/gx/api:api.CreateItem.func1"
    assert r.body_models == ("createItem",) and r.dependencies == ()
    assert {(p.name, p.source) for p in r.params} >= {("name", "body"), ("price", "body")}
    assert {(o.table, o.column, o.how) for o in r.owner_uses} >= {("Item", "owner_id", "assign")}
    assert r.tables and "Item" in r.tables
    assert {g.kind for g in r.guards} == {"authenticated"}
    assert [e.reason for e in model.excluded_routes if e.kind == "unreachable"] == [
        "not reachable from main"
    ]
    assert fe.last_extract is not None and fe.last_extract["module"] == "example.com/gx"
    assert model.auth is not None and model.auth.users_table == "User"


def test_the_model_and_the_sidecar_are_deterministic(fe):
    a = fe.extract(FIXTURES, "goextract").to_json()
    first = fe.sidecar()
    b = fe.extract(FIXTURES, "goextract").to_json()
    assert a == b and first == fe.sidecar()
    assert "/Users" not in first and str(FIXTURES) not in first


def test_graph_hints_recognise_nothing_in_python(fe):
    import ast

    hints = fe.graph_hints
    fn = ast.parse("def f():\n    pass\n").body[0]
    cls = ast.parse("class T:\n    pass\n").body[0]
    assert not hints.is_route(fn) and not hints.is_table(cls)  # type: ignore[arg-type]
    assert hints.overrides_attr is None and hints.sql_modules == ()


def test_the_schema_is_checked(model):
    with pytest.raises(GoExtractError, match="schema"):
        model_from_extract({"schema": "lattice.goextract/0"})


def test_runtime_is_a_go_process_runtime(fe, model):
    rt = fe.runtime(FIXTURES, "goextract", model=model)
    assert rt.framework == "go" and rt.one_server
    rt2 = fe.runtime(FIXTURES, "goextract", model=model, credentials={"ann": ("a@x", "pw")})
    assert rt2.framework == "go"


def test_reconcile_without_a_harness_names_what_is_missing(fe, model):
    with pytest.raises(GoExtractError, match="studio.toml"):
        fe.reconcile(FIXTURES / "goextract", model)


def test_extract_json_is_valid_json_with_the_schema(fe):
    data = fe.extract_json(FIXTURES, "goextract")
    assert data["schema"] == "lattice.goextract/1"
    json.dumps(data)  # plain values
