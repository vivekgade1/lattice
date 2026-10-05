"""Fidelity scenarios for the vendored recipe-app-api (``bench/import_repos/recipes``).

Written from the upstream tests (``app/user/tests/test_user_api.py``,
``app/recipe/tests/test_{tags,ingredients,recipe}_api.py``; not vendored), then recorded
against the running app on ``seed.py``'s data: every expected status and body fragment
below is what the app answers. Users are ``seed.SEED_USERS`` handles; ids follow
``seed.py``'s insertion order. ``expect_body`` is a subset match
(``lattice.spec.check.subset``).

Not used by the importer; ``tests/bench/test_phase2_apps.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

AUTH_MISSING = {"detail": "Authentication credentials were not provided."}
BAD_LOGIN = {"non_field_errors": ["Unable to authenticate with provided credentials"]}
NO_RECIPE = {"detail": "No Recipe matches the given query."}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "register a user; the password is write-only",
        steps=(
            Step(
                "POST",
                "/api/user/create/",
                json={
                    "email": "test@londonappdev.com",
                    "password": "testpass",
                    "name": "Test name",
                },
                expect_status=201,
                expect_body={"email": "test@londonappdev.com", "name": "Test name"},
            ),
            Step(
                "POST",
                "/api/user/token/",
                json={"email": "test@londonappdev.com", "password": "testpass"},
            ),
        ),
    ),
    Scenario(
        "registering an existing email or a short password fails",
        steps=(
            Step(
                "POST",
                "/api/user/create/",
                json={"email": "alice@example.com", "password": "testpass", "name": "Again"},
                expect_status=400,
                expect_body={"email": ["user with this email already exists."]},
            ),
            Step(
                "POST",
                "/api/user/create/",
                json={"email": "short@example.com", "password": "pw", "name": "Short"},
                expect_status=400,
                expect_body={"password": ["Ensure this field has at least 5 characters."]},
            ),
        ),
    ),
    Scenario(
        "a token needs valid credentials of an active user",
        steps=(
            Step(
                "POST",
                "/api/user/token/",
                json={"email": "alice@example.com", "password": "wrong"},
                expect_status=400,
                expect_body=BAD_LOGIN,
            ),
            Step(
                "POST",
                "/api/user/token/",
                json={"email": "nobody@example.com", "password": "alicepass1"},
                expect_status=400,
                expect_body=BAD_LOGIN,
            ),
            Step(
                "POST",
                "/api/user/token/",
                json={"email": "alice@example.com", "password": ""},
                expect_status=400,
                expect_body={"password": ["This field may not be blank."]},
            ),
            Step(
                "POST",
                "/api/user/token/",
                json={"email": "carol@example.com", "password": "carolpass1"},
                expect_status=400,
                expect_body=BAD_LOGIN,
            ),
        ),
    ),
    Scenario(
        "the profile route needs a login and returns the caller",
        steps=(
            Step("GET", "/api/user/me/", expect_status=401, expect_body=AUTH_MISSING),
            Step(
                "GET",
                "/api/user/me/",
                user="alice",
                expect_body={"email": "alice@example.com", "name": "Alice Archer"},
            ),
            Step("POST", "/api/user/me/", user="alice", json={}, expect_status=405),
        ),
    ),
    Scenario(
        "the caller can update their own profile and password",
        steps=(
            Step(
                "PATCH",
                "/api/user/me/",
                user="bob",
                json={"name": "new name", "password": "newpassword123"},
                expect_body={"email": "bob@example.com", "name": "new name"},
            ),
            Step(
                "POST",
                "/api/user/token/",
                json={"email": "bob@example.com", "password": "newpassword123"},
            ),
            Step(
                "POST",
                "/api/user/token/",
                json={"email": "bob@example.com", "password": "bobpass1"},
                expect_status=400,
                expect_body=BAD_LOGIN,
            ),
        ),
    ),
    Scenario(
        "tags need a login and list only the caller's, by name descending",
        steps=(
            Step("GET", "/api/recipe/tags/", expect_status=401, expect_body=AUTH_MISSING),
            Step(
                "GET",
                "/api/recipe/tags/",
                user="alice",
                expect_body=[{"id": 1, "name": "Vegan"}, {"id": 2, "name": "Dessert"}],
            ),
            Step(
                "GET",
                "/api/recipe/tags/",
                user="bob",
                expect_body=[{"id": 4, "name": "Quick"}, {"id": 3, "name": "Breakfast"}],
            ),
        ),
    ),
    Scenario(
        "a created tag belongs to the caller; a blank name fails",
        steps=(
            Step(
                "POST",
                "/api/recipe/tags/",
                user="alice",
                json={"name": "Simple"},
                expect_status=201,
                expect_body={"id": 6, "name": "Simple"},
            ),
            Step(
                "POST",
                "/api/recipe/tags/",
                user="alice",
                json={"name": ""},
                expect_status=400,
                expect_body={"name": ["This field may not be blank."]},
            ),
            Step(
                "GET",
                "/api/recipe/tags/",
                user="alice",
                expect_body=[{"name": "Vegan"}, {"name": "Simple"}, {"name": "Dessert"}],
            ),
            Step("GET", "/api/recipe/tags/", user="bob", expect_body=[{"id": 4}, {"id": 3}]),
        ),
    ),
    Scenario(
        "ingredients are the caller's; assigned_only keeps those on a recipe",
        steps=(
            Step(
                "GET",
                "/api/recipe/ingredients/",
                user="alice",
                expect_body=[{"name": "Salt"}, {"name": "Pepper"}, {"name": "Kale"}],
            ),
            Step(
                "GET",
                "/api/recipe/ingredients/",
                user="alice",
                params={"assigned_only": 1},
                expect_body=[{"name": "Salt"}, {"name": "Kale"}],
            ),
            Step(
                "POST",
                "/api/recipe/ingredients/",
                user="bob",
                json={"name": "Cabbage"},
                expect_status=201,
                expect_body={"id": 7, "name": "Cabbage"},
            ),
        ),
    ),
    Scenario(
        "tags and ingredients have no detail routes",
        steps=(
            Step("GET", "/api/recipe/tags/1/", user="alice", expect_status=404),
            Step("DELETE", "/api/recipe/ingredients/1/", user="alice", expect_status=404),
        ),
    ),
    Scenario(
        "recipes need a login and list only the caller's",
        steps=(
            Step("GET", "/api/recipe/recipes/", expect_status=401, expect_body=AUTH_MISSING),
            Step(
                "GET",
                "/api/recipe/recipes/",
                user="alice",
                expect_body=[
                    {
                        "id": 1,
                        "title": "Kale chips",
                        "tags": [1],
                        "ingredients": [1, 2],
                        "time_minutes": 20,
                        "price": "4.50",
                    },
                    {"id": 2, "title": "Vegan brownies"},
                ],
            ),
            Step("GET", "/api/recipe/recipes/", user="admin", expect_body=[]),
        ),
    ),
    Scenario(
        "a recipe's detail nests its tags and ingredients; others' recipes are not found",
        steps=(
            Step(
                "GET",
                "/api/recipe/recipes/3/",
                user="bob",
                expect_body={
                    "id": 3,
                    "title": "Pancakes",
                    "tags": [{"id": 3, "name": "Breakfast"}, {"id": 4, "name": "Quick"}],
                    "ingredients": [{"name": "Eggs"}, {"name": "Flour"}, {"name": "Butter"}],
                },
            ),
            Step(
                "GET",
                "/api/recipe/recipes/3/",
                user="alice",
                expect_status=404,
                expect_body=NO_RECIPE,
            ),
        ),
    ),
    Scenario(
        "a created recipe belongs to the caller",
        steps=(
            Step(
                "POST",
                "/api/recipe/recipes/",
                user="alice",
                json={
                    "title": "Chocolate cheesecake",
                    "time_minutes": 30,
                    "price": "5.00",
                    "tags": [1, 2],
                    "ingredients": [1],
                },
                expect_status=201,
                expect_body={"id": 5, "title": "Chocolate cheesecake", "tags": [1, 2]},
            ),
            Step(
                "GET",
                "/api/recipe/recipes/5/",
                user="alice",
                expect_body={"title": "Chocolate cheesecake", "price": "5.00"},
            ),
            Step("GET", "/api/recipe/recipes/5/", user="bob", expect_status=404),
        ),
    ),
    Scenario(
        "recipe fields are validated",
        steps=(
            Step(
                "POST",
                "/api/recipe/recipes/",
                user="alice",
                json={"title": "No time", "price": "5.00", "tags": [], "ingredients": []},
                expect_status=400,
                expect_body={"time_minutes": ["This field is required."]},
            ),
            Step(
                "POST",
                "/api/recipe/recipes/",
                user="alice",
                json={
                    "title": "Pricey",
                    "time_minutes": 5,
                    "price": "1000.00",
                    "tags": [],
                    "ingredients": [],
                },
                expect_status=400,
                expect_body={"price": ["Ensure that there are no more than 5 digits in total."]},
            ),
            Step(
                "POST",
                "/api/recipe/recipes/",
                user="alice",
                json={
                    "title": "Ghost tag",
                    "time_minutes": 5,
                    "price": "1.00",
                    "tags": [99],
                    "ingredients": [],
                },
                expect_status=400,
                expect_body={"tags": ['Invalid pk "99" - object does not exist.']},
            ),
        ),
    ),
    Scenario(
        "the owner can partially and fully update a recipe",
        steps=(
            Step(
                "PATCH",
                "/api/recipe/recipes/1/",
                user="alice",
                json={"title": "Chicken tikka", "tags": [2]},
                expect_body={"id": 1, "title": "Chicken tikka", "tags": [2], "ingredients": [1, 2]},
            ),
            Step(
                "PUT",
                "/api/recipe/recipes/1/",
                user="alice",
                json={
                    "title": "Spaghetti carbonara",
                    "time_minutes": 25,
                    "price": "5.00",
                    "tags": [],
                    "ingredients": [],
                },
                expect_body={"title": "Spaghetti carbonara", "tags": [], "ingredients": []},
            ),
        ),
    ),
    Scenario(
        "another user can neither change nor delete a recipe",
        steps=(
            Step(
                "PATCH",
                "/api/recipe/recipes/1/",
                user="bob",
                json={"title": "Mine"},
                expect_status=404,
                expect_body=NO_RECIPE,
            ),
            Step("DELETE", "/api/recipe/recipes/1/", user="bob", expect_status=404),
            Step("DELETE", "/api/recipe/recipes/4/", user="bob", expect_status=204),
            Step("GET", "/api/recipe/recipes/", user="bob", expect_body=[{"id": 3}]),
            Step("GET", "/api/recipe/recipes/", user="alice", expect_body=[{"id": 1}, {"id": 2}]),
        ),
    ),
    Scenario(
        "recipes filter by tag and ingredient ids",
        steps=(
            Step(
                "GET",
                "/api/recipe/recipes/",
                user="alice",
                params={"tags": "2"},
                expect_body=[{"id": 2}],
            ),
            Step(
                "GET",
                "/api/recipe/recipes/",
                user="alice",
                params={"ingredients": "1,2"},
                expect_body=[{"id": 1}, {"id": 1}],
            ),
            Step("GET", "/api/recipe/recipes/", user="bob", params={"tags": "1"}, expect_body=[]),
        ),
    ),
    Scenario(
        "uploading an image that is not an image fails",
        steps=(
            Step(
                "POST",
                "/api/recipe/recipes/1/upload-image/",
                user="alice",
                json={"image": "notimage"},
                expect_status=400,
            ),
            Step(
                "POST",
                "/api/recipe/recipes/3/upload-image/",
                user="alice",
                json={"image": "notimage"},
                expect_status=404,
            ),
        ),
    ),
)
