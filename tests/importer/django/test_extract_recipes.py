"""The Django + DRF front end on recipe-app-api (``bench/import_repos/recipes``; plan
W2-A): a custom users model (``AUTH_USER_MODEL``), many-to-many link tables, routes
from ``include``d URLconfs (APIViews and a ``DefaultRouter``), ``TokenAuthentication``
with the app's own ``ObtainAuthToken`` login, ``get_queryset`` owner scoping and
``perform_create`` owner stamping.

Extraction boots Django, so it runs in a subprocess (one project per process)."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("django")
pytest.importorskip("rest_framework")

from _django_fe import columns, extract_json, guards, owner_uses, routes, tables  # noqa: E402

PACKAGE = "bench.import_repos.recipes"
RECIPE_VIEWS = "bench.import_repos.recipes.app.recipe.views"
USER_VIEWS = "bench.import_repos.recipes.app.user.views"


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return extract_json(PACKAGE, tmp_path_factory.mktemp("recipes"))


def test_tables(model: dict[str, Any]) -> None:
    assert {n: t["table_name"] for n, t in tables(model).items()} == {
        "User": "core_user",
        "Tag": "core_tag",
        "Ingredient": "core_ingredient",
        "Recipe": "core_recipe",
        "Recipe_ingredients": "core_recipe_ingredients",
        "Recipe_tags": "core_recipe_tags",
    }
    excluded = {e["name"]: e["reason"] for e in model["excluded"] if e["what"] == "table"}
    # the custom user's PermissionsMixin links to framework tables: framework too
    assert "core_user_groups" in excluded and "core_user_user_permissions" in excluded
    assert "authtoken_token" in excluded


def test_custom_users_model(model: dict[str, Any]) -> None:
    cols = columns(tables(model)["User"])
    assert cols["email"]["type"] == "Email"
    assert cols["email"]["unique"] and cols["email"]["max_length"] == 255
    assert cols["name"]["max_length"] == 255 and cols["name"]["required"]
    assert cols["is_active"]["default"] == "True"
    assert {"password", "last_login", "is_superuser", "is_staff"} <= set(cols)


def test_recipe_columns(model: dict[str, Any]) -> None:
    cols = columns(tables(model)["Recipe"])
    assert list(cols) == ["id", "user_id", "title", "time_minutes", "price", "link", "image"]
    assert cols["user_id"]["foreign_key"] == "core_user.id"
    assert cols["time_minutes"]["type"] == "Int"
    assert cols["price"]["type"] == "Float" and cols["price"]["annotation"] == "DecimalField"
    assert cols["link"]["max_length"] == 255
    assert cols["image"]["nullable"] and cols["image"]["type"] == "Str"
    # many-to-many fields are link tables, not columns
    assert "tags" not in cols and "ingredients" not in cols


def test_link_tables(model: dict[str, Any]) -> None:
    link = tables(model)["Recipe_tags"]
    assert link["through"] == "Recipe.tags"
    cols = columns(link)
    assert cols["recipe_id"]["foreign_key"] == "core_recipe.id"
    assert cols["tag_id"]["foreign_key"] == "core_tag.id"
    assert link["unique_together"] == [["recipe_id", "tag_id"]]
    assert tables(model)["Recipe_ingredients"]["through"] == "Recipe.ingredients"


def test_routes(model: dict[str, Any]) -> None:
    assert set(routes(model)) == {
        ("POST", "/api/user/create/"),
        ("POST", "/api/user/token/"),
        ("GET", "/api/user/me/"),
        ("PUT", "/api/user/me/"),
        ("PATCH", "/api/user/me/"),
        ("GET", "/api/recipe/tags/"),
        ("POST", "/api/recipe/tags/"),
        ("GET", "/api/recipe/ingredients/"),
        ("POST", "/api/recipe/ingredients/"),
        ("GET", "/api/recipe/recipes/"),
        ("POST", "/api/recipe/recipes/"),
        ("GET", "/api/recipe/recipes/{pk}/"),
        ("PUT", "/api/recipe/recipes/{pk}/"),
        ("PATCH", "/api/recipe/recipes/{pk}/"),
        ("DELETE", "/api/recipe/recipes/{pk}/"),
        ("POST", "/api/recipe/recipes/{pk}/upload-image/"),
    }
    excluded = {e["name"]: e["reason"] for e in model["excluded"] if e["what"] == "route"}
    assert "API root" in excluded["GET /api/recipe/"]
    assert any(n.startswith("* /admin/") and "admin" in r for n, r in excluded.items())


def test_api_views(model: dict[str, Any]) -> None:
    r = routes(model)
    assert r[("POST", "/api/user/create/")]["view"] == f"{USER_VIEWS}:CreateUserView.post"
    assert r[("GET", "/api/user/me/")]["view"] == f"{USER_VIEWS}:ManageUserView.get"
    assert r[("POST", "/api/user/create/")]["status_code"] == 201
    assert r[("GET", "/api/user/me/")]["response_model"] == "User"
    assert (
        r[("POST", "/api/recipe/recipes/{pk}/upload-image/")]["view"]
        == f"{RECIPE_VIEWS}:RecipeViewSet.upload_image"
    )


def test_guards(model: dict[str, Any]) -> None:
    r = routes(model)
    assert guards(r[("POST", "/api/user/create/")]) == []
    assert r[("POST", "/api/user/create/")]["auth"] == "Public"
    assert guards(r[("POST", "/api/user/token/")]) == []
    for key in (("GET", "/api/user/me/"), ("GET", "/api/recipe/tags/"),
                ("DELETE", "/api/recipe/recipes/{pk}/")):  # fmt: skip
        assert guards(r[key]) == [("authenticated", "IsAuthenticated", 401)], key
        assert r[key]["auth"] == "Customer"


def test_owner_evidence(model: dict[str, Any]) -> None:
    r = routes(model)
    assert owner_uses(r[("GET", "/api/recipe/tags/")]) == {("Tag", "user_id", "compare")}
    assert owner_uses(r[("POST", "/api/recipe/tags/")]) == {("Tag", "user_id", "assign")}
    assert owner_uses(r[("GET", "/api/recipe/ingredients/")]) == {
        ("Ingredient", "user_id", "compare")
    }
    assert owner_uses(r[("POST", "/api/recipe/recipes/")]) == {("Recipe", "user_id", "assign")}
    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert owner_uses(r[(method, "/api/recipe/recipes/{pk}/")]) == {
            ("Recipe", "user_id", "compare")
        }
    assert owner_uses(r[("POST", "/api/recipe/recipes/{pk}/upload-image/")]) == {
        ("Recipe", "user_id", "compare")
    }
    assert owner_uses(r[("GET", "/api/user/me/")]) == set()


def test_query_params(model: dict[str, Any]) -> None:
    r = routes(model)
    q = {
        p["name"]: p for p in r[("GET", "/api/recipe/recipes/")]["params"] if p["source"] == "query"
    }
    assert set(q) == {"tags", "ingredients"} and q["tags"]["type"] == "Str"
    assert not q["tags"]["required"]
    q = {p["name"]: p for p in r[("GET", "/api/recipe/tags/")]["params"] if p["source"] == "query"}
    assert q["assigned_only"]["type"] == "Int" and q["assigned_only"]["default"] == "0"


def test_error_sites(model: dict[str, Any]) -> None:
    r = routes(model)
    login = r[("POST", "/api/user/token/")]["raises"]
    assert {(s["status"], s["detail"], s["kind"]) for s in login} >= {
        (400, "Unable to authenticate with provided credentials", "drf")
    }
    upload = {
        (s["status"], s["kind"])
        for s in r[("POST", "/api/recipe/recipes/{pk}/upload-image/")]["raises"]
    }
    assert {(404, "drf"), (400, "return")} <= upload
    detail = {(s["status"], s["kind"]) for s in r[("GET", "/api/recipe/recipes/{pk}/")]["raises"]}
    assert (404, "drf") in detail


def test_body_models(model: dict[str, Any]) -> None:
    by_name = {m["name"]: m for m in model["models"]}
    r = routes(model)

    def body(key: tuple[str, str]) -> dict[str, dict[str, Any]]:
        p = next(p for p in r[key]["params"] if p["source"] == "body")
        return {f["name"]: f for f in by_name[p["type"]]["fields"]}

    user = body(("POST", "/api/user/create/"))
    assert set(user) == {"email", "password", "name"}
    assert user["password"]["min_length"] == 5 and user["password"]["write_only"]
    assert user["email"]["type"] == "Email"
    recipe = body(("POST", "/api/recipe/recipes/"))
    assert recipe["tags"]["type"] == "List" and recipe["tags"]["item"] == "Int"
    assert recipe["price"]["type"] == "Float"
    assert not recipe["link"]["required"]
    login = body(("POST", "/api/user/token/"))
    assert set(login) == {"email", "password"}


def test_auth_model(model: dict[str, Any]) -> None:
    auth = model["auth"]
    assert auth["scheme"] == "token"
    assert auth["login_path"] == "/api/user/token/"
    assert auth["login_encoding"] == "json"
    assert auth["username_field"] == "email" and auth["password_field"] == "password"
    assert auth["token_path"] == ["token"]
    assert auth["users_table"] == "User"
    assert auth["email_column"] == "email"
    assert auth["active_column"] == "is_active"
    assert auth["minter"] == "rest_framework.authtoken.models:Token"
    assert auth["invalid_answer"] == [401, "Invalid token."]
    assert auth["inactive_answer"] == [401, "User inactive or deleted."]
    assert auth["unauthenticated_answer"] == [401, "Authentication credentials were not provided."]
