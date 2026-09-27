"""lattice.importer.graph (spec 2026-09-27 importer §3.6, S2): the repo-wide symbol and
call graph and per-route dependency slices, on the template and on synthetic snippets."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lattice.importer.extract import extract
from lattice.importer.graph import (
    CodeGraph,
    GraphBackend,
    PythonAstBackend,
    build_graph,
    route_slice,
    slice_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG = "bench.template_app.app"
ITEMS = f"{PKG}.api.routes.items"
USERS = f"{PKG}.api.routes.users"
LOGIN = f"{PKG}.api.routes.login"
DEPS = f"{PKG}.api.deps"
CRUD = f"{PKG}.crud"
SECURITY = f"{PKG}.core.security"
MODELS = f"{PKG}.models"


@pytest.fixture(scope="module")
def graph() -> CodeGraph:
    return build_graph(REPO_ROOT, PKG)


# ---- the template: slices equal hand-listed closures ----------------------------------------

AUTH = {f"{DEPS}:get_current_user", f"{DEPS}:get_db"}
CLOSURES = {
    "read_items": {
        "functions": {f"{ITEMS}:read_items", *AUTH},
        "models": {f"{MODELS}:Item", f"{MODELS}:User"},
        "effects": set(),
    },
    "update_item": {
        "functions": {f"{ITEMS}:update_item", *AUTH},
        "models": {f"{MODELS}:Item", f"{MODELS}:User"},
        "effects": {f"{ITEMS}:update_item"},
    },
    "update_user_me": {
        "functions": {f"{USERS}:update_user_me", f"{CRUD}:get_user_by_email", *AUTH},
        "models": {f"{MODELS}:User"},
        "effects": {f"{USERS}:update_user_me"},
    },
    "login_access_token": {
        "functions": {
            f"{LOGIN}:login_access_token",
            f"{DEPS}:get_db",
            f"{CRUD}:authenticate",
            f"{CRUD}:get_user_by_email",
            f"{SECURITY}:verify_password",
            f"{SECURITY}:create_access_token",
        },
        "models": {f"{MODELS}:User"},
        "effects": {f"{CRUD}:authenticate"},
    },
}
ROUTE_MODULE = {"read_items": ITEMS, "update_item": ITEMS, "update_user_me": USERS,
                "login_access_token": LOGIN}  # fmt: skip


@pytest.mark.parametrize("name", sorted(CLOSURES))
def test_route_slice_equals_the_hand_listed_closure(graph, name):
    s = route_slice(graph, f"{ROUTE_MODULE[name]}:{name}")
    want = CLOSURES[name]
    assert set(s.functions) == want["functions"]
    assert set(s.models) == want["models"]
    assert {q for q, _ in s.effects} == want["effects"]
    assert s.dynamic == ()


def test_dependencies_are_reached_through_depends_edges(graph):
    s = route_slice(graph, f"{ITEMS}:read_items")
    assert set(s.dependencies) >= {f"{DEPS}:get_current_user", f"{DEPS}:get_db"}
    assert f"{DEPS}:reusable_oauth2" in s.variables  # TokenDep = Depends(reusable_oauth2)
    # the admin dependency is reached through a decorator's dependencies=[Depends(...)]
    admin = route_slice(graph, f"{USERS}:read_users")
    assert f"{DEPS}:get_current_active_superuser" in admin.dependencies
    assert f"{DEPS}:get_current_user" in admin.functions


def test_a_slice_accepts_an_extracted_route(graph):
    model = extract(REPO_ROOT, PKG)
    route = next(r for r in model.routes if r.name == "update_item")
    assert route_slice(graph, route) == route_slice(graph, f"{ITEMS}:update_item")


def test_effect_reasons_name_the_session_writes_and_the_outbox(graph):
    s = route_slice(graph, f"{USERS}:create_user")
    effects = dict(s.effects)
    assert "session.commit" in effects[f"{CRUD}:create_user"]
    assert "OUTBOX" in effects[f"{PKG}.utils:send_email"]
    assert f"{PKG}.core.config:Settings.emails_enabled" in s.functions  # a property read
    delete_user = dict(route_slice(graph, f"{USERS}:delete_user").effects)
    assert "delete(" in delete_user[f"{USERS}:delete_user"]  # session.exec(delete(Item)...)


def test_external_calls_are_listed(graph):
    s = route_slice(graph, f"{ITEMS}:read_items")
    assert {"jwt:decode", "sqlmodel:select", "sqlmodel:Session.exec"} <= set(s.external)


def test_slice_source_is_callees_first(graph):
    s = route_slice(graph, f"{LOGIN}:login_access_token")
    src = slice_source(s)
    order = [src.index(f"def {n}(") for n in
             ("get_user_by_email", "authenticate", "login_access_token")]  # fmt: skip
    assert order == sorted(order)
    assert "class User(UserBase, table=True):" in src  # the models touched
    assert "SessionDep = Annotated[Session, Depends(get_db)]" in src  # the variables used
    assert src.index("def verify_password(") < src.index("def authenticate(")


def test_the_backend_is_pluggable():
    class Fake:
        def build(self, repo_root: Path, package: str) -> CodeGraph:
            return CodeGraph(package=package)

    assert isinstance(PythonAstBackend(), GraphBackend)
    assert build_graph(REPO_ROOT, "x", backend=Fake()).package == "x"


# ---- synthetic snippets ---------------------------------------------------------------------


def write_pkg(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("")
    for name, src in files.items():
        (root / f"{name}.py").write_text(textwrap.dedent(src))
    return tmp_path


DYNAMIC = """
import importlib
from fastapi import APIRouter, Depends, FastAPI

router = APIRouter()
HANDLERS = {}


def plain(x):
    return x + 1


def real_get_db():
    return 1


@router.get("/a")
def by_getattr(kind: str):
    return getattr(HANDLERS, kind)()


@router.get("/b")
def by_import(name: str):
    mod = importlib.import_module(name)
    return mod.run()


@router.get("/c")
def by_variable(kind: str):
    handler = HANDLERS[kind]
    return handler(kind)


@router.get("/d")
def by_parameter(callback=plain):
    return callback(1)


@router.get("/e")
def static(kind: str):
    return plain(len(kind))


@router.get("/f")
def uses_db(db=Depends(real_get_db)):
    return db


@router.get("/g")
def literal_getattr(obj: str):
    return getattr(obj, "upper")()


def make_app():
    app = FastAPI()
    app.dependency_overrides[real_get_db] = plain
    return app
"""


@pytest.fixture
def dyn(tmp_path) -> CodeGraph:
    return build_graph(write_pkg(tmp_path, {"routes": DYNAMIC}), "pkg")


@pytest.mark.parametrize(
    ("route", "kind"),
    [
        ("by_getattr", "getattr"),
        ("by_import", "dynamic_import"),
        ("by_variable", "unresolved_call"),
        ("by_parameter", "unresolved_call"),
        ("uses_db", "dependency_overrides"),
    ],
)
def test_dynamic_dispatch_is_flagged(dyn, route, kind):
    s = route_slice(dyn, f"pkg.routes:{route}")
    assert kind in {f.kind for f in s.dynamic}, s.dynamic


def test_static_calls_are_not_flagged(dyn):
    s = route_slice(dyn, "pkg.routes:static")
    assert s.dynamic == ()
    assert "pkg.routes:plain" in s.functions
    assert route_slice(dyn, "pkg.routes:literal_getattr").dynamic == ()


def test_the_override_is_an_edge_of_the_graph(dyn):
    flags = dyn.dynamic["pkg.routes:make_app"]
    assert [f.kind for f in flags] == ["dependency_overrides"]
    assert dyn.overrides == {"pkg.routes:real_get_db": ("pkg.routes:plain",)}


def test_relative_imports_methods_and_self_calls_resolve(tmp_path):
    root = write_pkg(
        tmp_path,
        {
            "models": """
                from sqlmodel import Field, SQLModel

                class Note(SQLModel, table=True):
                    id: int = Field(primary_key=True)
            """,
            "service": """
                from .models import Note

                class Repo:
                    def __init__(self, session):
                        self.session = session

                    def get(self, id):
                        return self.session.get(Note, id)

                    def save(self, note):
                        self.session.add(note)
                        self.session.commit()
                        return self.get(note.id)
            """,
            "routes": """
                from fastapi import APIRouter
                from . import service

                router = APIRouter()

                @router.post("/notes")
                def create(session):
                    repo = service.Repo(session)
                    return repo.save(None)
            """,
        },
    )
    g = build_graph(root, "pkg")
    s = route_slice(g, "pkg.routes:create")
    assert set(s.functions) == {
        "pkg.routes:create",
        "pkg.service:Repo.__init__",
        "pkg.service:Repo.save",
        "pkg.service:Repo.get",
    }
    assert s.models == ("pkg.models:Note",)
    assert [q for q, _ in s.effects] == ["pkg.service:Repo.save"]
