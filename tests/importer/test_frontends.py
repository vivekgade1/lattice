"""lattice.importer.frontends (Phase 2 plan, W1-A): the framework-neutral app model, the
FastAPI front end behind the ``extract`` shim, the front-end registry and the graph hints
a front end supplies."""

from __future__ import annotations

import dataclasses
import textwrap
from pathlib import Path

import pytest

import lattice.importer.extract as shim
from lattice.importer import appmodel, frontends
from lattice.importer.frontends import fastapi as fastapi_frontend
from lattice.importer.frontends import pyindex
from lattice.importer.graph import FASTAPI_HINTS, GraphHints, build_graph, route_slice
from lattice.importer.transport import AppRuntime
from tests.importer.test_frontend_goldens import ADDED_DEFAULTS

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = "bench.template_app.app"

# the model's fields at W0 (the goldens' shape); every later field is an added default
W0_FIELDS = {
    "RepoModel": ("app_package", "tables", "models", "dependencies", "routes", "natives",
                  "database_url", "dialect", "exception_handlers", "custom_serialization",
                  "role_scheme"),
    "Route": ("name", "module", "method", "path", "params", "response_model", "response_class",
              "status_code", "dependencies", "auth", "auth_reason", "raises", "calls", "tables",
              "owner_uses", "body_models", "line", "enclosing", "returns"),
    "RaiseSite": ("status", "detail", "line"),
}  # fmt: skip


def test_the_extract_module_is_a_shim_over_the_fastapi_front_end():
    assert shim.extract is fastapi_frontend.extract
    assert shim.route_function is fastapi_frontend.route_function
    assert shim._Index is pyindex._Index is fastapi_frontend._Index
    assert shim._single_assignment is pyindex._single_assignment
    assert shim.LANGUAGE_GAP == "language gap"
    for name in ("RepoModel", "Route", "RaiseSite", "FieldSpec", "TableModel", "DataModel",
                 "Dependency", "NativeCandidate", "RoleScheme"):  # fmt: skip
        assert getattr(shim, name) is getattr(appmodel, name)
    assert appmodel.AppModel is appmodel.RepoModel


def test_the_new_fields_default_to_todays_fastapi_output():
    route = appmodel.Route(
        "r", "m", "GET", "/", (), None, None, None, (), "Public", "", (), (), (), (), (), 1
    )
    assert (route.guards, route.view) == ((), None)
    assert appmodel.RaiseSite(404, "x", 3).kind == "HTTPException"
    model = appmodel.RepoModel("p", (), (), (), (), (), None, None, (), ())
    assert (model.framework, model.orm, model.auth) == ("fastapi", "sqlmodel", None)


def test_every_added_field_is_in_the_goldens_added_defaults():
    classes = {"RepoModel": appmodel.RepoModel, "Route": appmodel.Route,
               "RaiseSite": appmodel.RaiseSite}  # fmt: skip
    for name, cls in classes.items():
        for f in dataclasses.fields(cls):
            if f.name in W0_FIELDS[name]:
                continue
            assert f.name in ADDED_DEFAULTS, f"{name}.{f.name}"
            default = f.default if f.default is not dataclasses.MISSING else f.default_factory()  # type: ignore[misc]
            as_json = list(default) if isinstance(default, tuple) else default
            assert ADDED_DEFAULTS[f.name] == as_json, f"{name}.{f.name}"


def test_a_guard_and_an_auth_model():
    g = appmodel.Guard("role", "permission_required", 403, "Forbidden", ("Moderator",), 12)
    assert g.kind == "role" and g.roles == ("Moderator",)
    auth = appmodel.AuthModel(login_path="/api/token", login_encoding="json",
                              token_path=("token",), scheme="token")  # fmt: skip
    assert auth.scheme == "token" and auth.password_column is None
    assert appmodel.AuthModel().scheme == "bearer"
    assert appmodel.AuthModel().login_encoding == "form"


def test_the_registry_selects_the_fastapi_front_end():
    fe = frontends.select("fastapi")
    assert fe.name == "fastapi"
    assert frontends.select(None, REPO_ROOT, TEMPLATE) is fe  # None: detect
    assert fe.detect(REPO_ROOT, TEMPLATE)
    assert fe.graph_hints is FASTAPI_HINTS
    assert fe.runtime(REPO_ROOT, TEMPLATE) == AppRuntime()
    model = fe.extract(REPO_ROOT, TEMPLATE)
    assert model == shim.extract(REPO_ROOT, TEMPLATE)
    with pytest.raises(KeyError, match="no front end"):
        frontends.select("django")


def test_auto_selection_without_a_match_says_so(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("import json\n")
    with pytest.raises(LookupError, match="no front end recognises"):
        frontends.select(None, tmp_path, "pkg")


FLASKISH = """
from flask import Blueprint, abort
from .db import db

bp = Blueprint("notes", __name__)


class Note(db.Model):
    __tablename__ = "notes"


def helper():
    return Note()


@bp.route("/notes")
def list_notes():
    return helper()
"""


def _pkg(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "db.py").write_text("db = object()\n")
    (root / "views.py").write_text(textwrap.dedent(FLASKISH))
    return tmp_path


def test_graph_hints_come_from_the_front_end(tmp_path):
    root = _pkg(tmp_path)
    default = build_graph(root, "pkg")
    assert default.routes() == []  # FastAPI's hints: @bp.route is not a route
    assert build_graph(root, "pkg", hints=FASTAPI_HINTS).routes() == []

    def is_route(fn):
        return any(getattr(getattr(d, "func", None), "attr", None) == "route"
                   for d in fn.decorator_list)  # fmt: skip

    hints = GraphHints(is_route=is_route)
    g = build_graph(root, "pkg", hints=hints)
    assert g.routes() == ["pkg.views:list_notes"]
    s = route_slice(g, "pkg.views:list_notes")
    assert "pkg.views:helper" in s.functions
    assert s.models == ("pkg.views:Note",)  # __tablename__: a table under both hints


def test_the_default_graph_hints_equal_fastapis():
    assert GraphHints() == FASTAPI_HINTS
    assert FASTAPI_HINTS.sql_modules == ("sqlmodel", "sqlalchemy")
    assert FASTAPI_HINTS.overrides_attr == "dependency_overrides"
