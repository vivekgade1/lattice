"""The Django + DRF front end on the DRF tutorial ("snippets", ``bench/import_repos/snippets``;
plan W2-A): the AppModel facts it reads -- tables from the live ``_meta``, routes from
the URLconf (the router's format-suffix duplicates and API root excluded and listed),
guards from the permission classes and the settings' default authentication, owner
evidence, error sites and the AuthModel.

Extraction boots Django, so it runs in a subprocess (one project per process)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("django")
pytest.importorskip("rest_framework")

from _django_fe import columns, extract_json, guards, owner_uses, routes, tables  # noqa: E402

PACKAGE = "bench.import_repos.snippets"
VIEWS = "bench.import_repos.snippets.snippets.views"


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return extract_json(PACKAGE, tmp_path_factory.mktemp("snippets"))


def test_framework_and_orm(model: dict[str, Any]) -> None:
    assert model["framework"] == "django"
    assert model["orm"] == "django"
    assert model["app_package"] == PACKAGE
    assert model["dialect"] == "sqlite"


def test_tables_are_the_app_models_plus_the_users_model(model: dict[str, Any]) -> None:
    t = tables(model)
    assert {n: x["table_name"] for n, x in t.items()} == {
        "User": "auth_user",
        "Snippet": "snippets_snippet",
    }
    assert t["User"]["module"] == "django.contrib.auth.models"
    excluded = {e["name"] for e in model["excluded"] if e["what"] == "table"}
    assert {
        "auth_permission",
        "auth_group",
        "auth_group_permissions",
        "auth_user_groups",
        "auth_user_user_permissions",
        "django_content_type",
        "django_session",
        "django_admin_log",
        "django_migrations",
    } <= excluded


def test_snippet_columns(model: dict[str, Any]) -> None:
    cols = columns(tables(model)["Snippet"])
    assert list(cols) == [
        "id", "created", "title", "code", "linenos", "language", "style", "owner_id",
        "highlighted",
    ]  # fmt: skip
    assert cols["id"]["type"] == "Int" and cols["id"]["primary_key"]
    assert cols["created"]["type"] == "DateTime" and cols["created"]["tz_aware"]
    assert not cols["created"]["required"]  # auto_now_add
    assert cols["title"]["type"] == "Str"
    assert cols["title"]["max_length"] == 100
    assert cols["title"]["default"] == "''" and not cols["title"]["required"]
    assert cols["code"]["type"] == "Str" and cols["code"]["required"]
    assert cols["code"]["max_length"] is None
    assert cols["linenos"]["type"] == "Bool" and cols["linenos"]["default"] == "False"
    assert cols["language"]["max_length"] == 100
    assert "python" in cols["language"]["choices"] and len(cols["language"]["choices"]) > 100
    assert "friendly" in cols["style"]["choices"]
    owner = cols["owner_id"]
    assert owner["type"] == "Int" and owner["foreign_key"] == "auth_user.id"
    assert not owner["nullable"] and owner["index"]


def test_users_columns(model: dict[str, Any]) -> None:
    cols = columns(tables(model)["User"])
    assert cols["username"]["unique"] and cols["username"]["max_length"] == 150
    assert cols["email"]["type"] == "Email"
    assert cols["password"]["max_length"] == 128
    for flag in ("is_superuser", "is_staff", "is_active"):
        assert cols[flag]["type"] == "Bool"
    assert cols["last_login"]["nullable"]


def test_routes_method_by_path(model: dict[str, Any]) -> None:
    assert set(routes(model)) == {
        ("GET", "/snippets/"),
        ("POST", "/snippets/"),
        ("GET", "/snippets/{pk}/"),
        ("PUT", "/snippets/{pk}/"),
        ("PATCH", "/snippets/{pk}/"),
        ("DELETE", "/snippets/{pk}/"),
        ("GET", "/snippets/{pk}/highlight/"),
        ("GET", "/users/"),
        ("GET", "/users/{pk}/"),
    }


def test_router_duplicates_and_api_root_excluded_and_listed(model: dict[str, Any]) -> None:
    excluded = [e for e in model["excluded"] if e["what"] == "route"]
    reasons = {e["name"]: e["reason"] for e in excluded}
    assert "format-suffix" in reasons["GET,POST /snippets.{format}"]
    assert "format-suffix" in reasons["GET,PUT,PATCH,DELETE /snippets/{pk}.{format}"]
    assert "format-suffix" in reasons["GET /users/{pk}.{format}"]
    assert "API root" in reasons["GET /"]
    assert any(n.startswith("* /api-auth/login/") for n in reasons)


def test_views_and_names(model: dict[str, Any]) -> None:
    r = routes(model)
    assert r[("GET", "/snippets/")]["view"] == f"{VIEWS}:SnippetViewSet.list"
    assert r[("DELETE", "/snippets/{pk}/")]["view"] == f"{VIEWS}:SnippetViewSet.destroy"
    assert r[("GET", "/snippets/{pk}/highlight/")]["view"] == f"{VIEWS}:SnippetViewSet.highlight"
    assert r[("GET", "/snippets/{pk}/highlight/")]["line"] > 0
    assert len({x["name"] for x in model["routes"]}) == len(model["routes"])
    assert r[("POST", "/snippets/")]["status_code"] == 201
    assert r[("DELETE", "/snippets/{pk}/")]["status_code"] == 204


def test_path_params_take_the_key_type(model: dict[str, Any]) -> None:
    params = routes(model)[("GET", "/snippets/{pk}/")]["params"]
    pk = next(p for p in params if p["name"] == "pk")
    assert pk["source"] == "path" and pk["type"] == "Int"


def test_list_routes_have_the_page_param(model: dict[str, Any]) -> None:
    params = routes(model)[("GET", "/snippets/")]["params"]
    page = next(p for p in params if p["name"] == "page")
    assert page["source"] == "query" and page["type"] == "Int" and not page["required"]


def test_guards_from_permission_classes(model: dict[str, Any]) -> None:
    r = routes(model)
    # IsAuthenticatedOrReadOnly: reads public; SessionAuthentication first, so DRF
    # answers an anonymous write 403 (no WWW-Authenticate header)
    assert guards(r[("GET", "/snippets/")]) == []
    assert r[("GET", "/snippets/")]["auth"] == "Public"
    assert guards(r[("POST", "/snippets/")]) == [
        ("authenticated", "IsAuthenticatedOrReadOnly", 403)
    ]
    assert r[("POST", "/snippets/")]["auth"] == "Customer"
    for method in ("PUT", "PATCH", "DELETE"):
        assert guards(r[(method, "/snippets/{pk}/")]) == [
            ("authenticated", "IsAuthenticatedOrReadOnly", 403),
            ("object_owner", "IsOwnerOrReadOnly", 403),
        ]
    # IsOwnerOrReadOnly lets safe methods through
    assert guards(r[("GET", "/snippets/{pk}/highlight/")]) == []
    assert guards(r[("GET", "/users/")]) == []


def test_owner_evidence(model: dict[str, Any]) -> None:
    r = routes(model)
    assert owner_uses(r[("POST", "/snippets/")]) == {("Snippet", "owner_id", "assign")}
    for method in ("PUT", "PATCH", "DELETE"):
        assert owner_uses(r[(method, "/snippets/{pk}/")]) == {("Snippet", "owner_id", "compare")}
    assert owner_uses(r[("GET", "/snippets/")]) == set()


def test_error_sites(model: dict[str, Any]) -> None:
    r = routes(model)
    sites = {(s["status"], s["kind"]) for s in r[("GET", "/snippets/{pk}/highlight/")]["raises"]}
    assert (404, "drf") in sites
    assert (404, "drf") in {
        (s["status"], s["kind"]) for s in r[("GET", "/snippets/{pk}/")]["raises"]
    }
    assert (400, "drf") in {(s["status"], s["kind"]) for s in r[("POST", "/snippets/")]["raises"]}


def test_body_model_from_the_serializer(model: dict[str, Any]) -> None:
    r = routes(model)[("POST", "/snippets/")]
    body = next(p for p in r["params"] if p["source"] == "body")
    data = {m["name"]: m for m in model["models"]}[body["type"]]
    fields = {f["name"]: f for f in data["fields"]}
    # owner is a ReadOnlyField, url / highlight / id read-only: not request data
    assert set(fields) == {"title", "code", "linenos", "language", "style"}
    assert fields["title"]["max_length"] == 100 and not fields["title"]["required"]
    assert fields["code"]["required"]
    assert "python" in fields["language"]["choices"]
    assert r["tables"] == ["Snippet"]
    assert r["returns"] == "Snippet"


def test_auth_model(model: dict[str, Any]) -> None:
    auth = model["auth"]
    assert auth["scheme"] == "basic"
    assert auth["login_path"] is None
    assert auth["users_table"] == "User"
    assert auth["email_column"] == "username"
    assert auth["password_column"] == "password"
    assert auth["active_column"] == "is_active"
    assert auth["admin_column"] == "is_staff"
    assert auth["hasher"] == "django.contrib.auth.hashers:make_password"
    # the answers to bad credentials: DRF's BasicAuthentication, 403 behind Session
    assert auth["invalid_answer"] == [403, "Invalid username/password."]
    assert auth["unauthenticated_answer"] == [403, "Authentication credentials were not provided."]


def test_no_files_written(model: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> None:
    root = Path(tmp_path_factory.getbasetemp()) / "snippets0"
    assert not any(p.suffix in (".db", ".sqlite3") for p in root.rglob("*"))
