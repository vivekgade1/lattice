"""The shared importer modules read the framework-neutral parts of the app model (plan
W2-A): ``Route.view`` (a class-based view's handler), ``RepoModel.auth`` (the front end's
``AuthModel``) -- so a Django or Flask app needs no FastAPI ``Dependency`` -- while a
FastAPI model (no view, no AuthModel) is read exactly as before."""

from __future__ import annotations

import ast
import dataclasses
import textwrap
from pathlib import Path

import pytest

from lattice.importer import adapt, policy
from lattice.importer.appmodel import (
    AuthModel,
    FieldSpec,
    RepoModel,
    Route,
    RouteParam,
    TableModel,
)
from lattice.importer.extract import _Index, route_function
from lattice.importer.lift import _functions
from lattice.importer.transport import AuthDriver, basic_login, scheme_header
from lattice.profile import ApiProfile

VIEWS = """
class Base:
    def get_queryset(self):
        return []


class ItemView(Base):
    def get(self, request):
        return 1

    def destroy(self, request, pk=None):
        return 2


def plain():
    return 3
"""

HASHING = """
def make_hash(password):
    return "h$" + password
"""


@pytest.fixture
def pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "w2a_pkg"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "views.py").write_text(textwrap.dedent(VIEWS))
    (root / "hashing.py").write_text(textwrap.dedent(HASHING))
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _route(name: str = "item_get", *, method: str = "GET", path: str = "/items/",
           view: str | None = None, params: tuple[RouteParam, ...] = (),
           module: str = "w2a_pkg.views", line: int = 1) -> Route:  # fmt: skip
    return Route(name, module, method, path, params, None, None, None, (), "Public", "", (),
                 (), (), (), (), line, view=view)  # fmt: skip


def _model(*routes: Route, auth: AuthModel | None = None,
           tables: tuple[TableModel, ...] = ()) -> RepoModel:  # fmt: skip
    return RepoModel("w2a_pkg", tables, (), (), routes, (), None, None, (), (), auth=auth)


# ---------------------------------------------------------------- the route's handler --


def test_route_function_finds_a_class_based_views_method(pkg: Path) -> None:
    index = _Index(pkg, "w2a_pkg")
    fn = route_function(index, _route(view="w2a_pkg.views:ItemView.get"))
    assert isinstance(fn, ast.FunctionDef) and fn.name == "get"


def test_route_function_follows_the_package_base_classes(pkg: Path) -> None:
    index = _Index(pkg, "w2a_pkg")
    fn = route_function(index, _route(view="w2a_pkg.views:ItemView.get_queryset"))
    assert isinstance(fn, ast.FunctionDef) and fn.name == "get_queryset"


def test_route_function_is_none_for_a_method_the_package_does_not_define(pkg: Path) -> None:
    index = _Index(pkg, "w2a_pkg")
    assert route_function(index, _route(view="w2a_pkg.views:ItemView.list")) is None
    assert route_function(index, _route(view="w2a_pkg.views:Missing.get")) is None


def test_route_function_without_a_view_is_the_named_function(pkg: Path) -> None:
    index = _Index(pkg, "w2a_pkg")
    fn = route_function(index, _route("plain"))
    assert isinstance(fn, ast.FunctionDef) and fn.name == "plain"


def test_functions_of_a_class_based_route_are_its_handler(pkg: Path) -> None:
    import w2a_pkg.views as views

    route = _route("item_destroy", view="w2a_pkg.views:ItemView.destroy")
    assert _functions(_model(route), route) == [views.ItemView.destroy]
    assert _functions(_model(_route("plain")), _route("plain")) == [views.plain]


# -------------------------------------------------------------------- the route's kind --

FORM = RouteParam("image", "UploadFile", None, "form")


def test_a_form_parameter_is_a_login_only_without_an_auth_model() -> None:
    upload = _route("upload", method="POST", path="/items/{pk}/image/", params=(FORM,))
    assert adapt._kind(_model(upload), upload, None) == "login"  # FastAPI: OAuth2 form
    model = _model(upload, auth=AuthModel(login_path="/token/"))
    assert adapt._kind(model, upload, None) == "other"
    login = _route("token", method="POST", path="/token/")
    assert adapt._kind(_model(login, auth=AuthModel(login_path="/token/")), login, None) == "login"


# ------------------------------------------------------------------------- the auth spec --

USERS = TableModel(
    "User",
    "auth_user",
    "django.contrib.auth.models",
    (
        FieldSpec("id", "AutoField", "Int", primary_key=True, required=False),
        FieldSpec("email", "EmailField", "Email", unique=True),
        FieldSpec("username", "CharField", "Str", unique=True, max_length=150),
        FieldSpec("password", "CharField", "Str", max_length=128),
        FieldSpec("is_staff", "BooleanField", "Bool"),
        FieldSpec("is_active", "BooleanField", "Bool"),
    ),
    (),
)
DJANGO_AUTH = AuthModel(
    scheme="basic",
    users_table="User",
    email_column="username",
    password_column="password",
    active_column="is_active",
    admin_column="is_staff",
    hasher="w2a_pkg.hashing:make_hash",
)


def _api(users_table: str = "User") -> ApiProfile:
    return ApiProfile(tables={}, table_names={}, roles=frozenset({"Customer", "Admin"}),
                      users_table=users_table)  # fmt: skip


def test_the_users_table_comes_from_the_auth_model() -> None:
    model = _model(auth=DJANGO_AUTH, tables=(USERS,))
    assert adapt.users_model(model, _api("Users")) is USERS
    assert adapt.users_model(_model(tables=(USERS,)), _api("Users")) is None


def test_auth_spec_reads_the_auth_model(pkg: Path) -> None:
    model = _model(auth=DJANGO_AUTH, tables=(USERS,))
    spec = adapt.auth_spec(model, _api(), _Index(pkg, "w2a_pkg"), {}, {})
    assert spec.users_table == "auth_user"
    assert spec.email_column == "username"  # not the unique Email column
    assert spec.password_column == "password"
    assert spec.admin_column == "is_staff"  # no admin Dependency
    assert spec.active_column == "is_active"
    assert spec.key_type == "int"
    assert spec.hash_password is not None and spec.hash_password("pw") == "h$pw"


def test_auth_spec_without_an_auth_model_is_unchanged(pkg: Path) -> None:
    spec = adapt.auth_spec(_model(tables=(USERS,)), _api(), _Index(pkg, "w2a_pkg"), {}, {})
    assert spec.email_column == "email"
    assert spec.admin_column is None
    assert spec.active_column is None
    assert spec.hash_password is None


# ---------------------------------------------------------------------- token answers --


def test_token_answers_come_from_the_auth_model_without_a_current_user_dependency(
    pkg: Path,
) -> None:
    index = _Index(pkg, "w2a_pkg")
    auth = dataclasses.replace(DJANGO_AUTH, invalid_answer=(401, "Invalid token."))
    answers = policy.token_answers(_model(auth=auth), index)
    assert answers == policy.TokenAnswers(
        (401, "Invalid token."), (401, "Invalid token."), (401, "Invalid token.")
    )
    jwt = dataclasses.replace(auth, unknown_answer=(401, "User not found"))
    assert policy.token_answers(_model(auth=jwt), index).unknown == (401, "User not found")
    assert policy.token_answers(_model(), index) == policy.TokenAnswers()


# ------------------------------------------------------------------------- basic auth --


def test_the_basic_scheme() -> None:
    assert scheme_header("basic") == "Basic"
    login = basic_login({"alice": ("alice", "pw:1")})
    token = login(None, "alice")
    import base64

    assert base64.b64decode(token).decode() == "alice:pw:1"
    driver = AuthDriver.basic({"alice": ("alice", "pw:1")})
    assert driver.scheme == "basic"
    assert driver.headers(token) == {"Authorization": f"Basic {token}"}


def test_an_auth_driver_from_an_auth_model() -> None:
    creds = {"alice": ("alice@example.com", "pw")}
    basic = AuthDriver.from_model(DJANGO_AUTH, creds)
    assert basic.scheme == "basic" and basic.login is not None
    token = AuthModel(login_path="/api/token/", login_encoding="json",
                      username_field="email", token_path=("token",), scheme="token")  # fmt: skip
    driver = AuthDriver.from_model(token, creds)
    assert driver.scheme == "token" and driver.login is not None
    assert AuthDriver.from_model(None, creds) == AuthDriver()
