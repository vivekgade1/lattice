"""Fidelity scenarios for the vendored microblog-api (``bench/import_repos/microblog``).

Written from the upstream README / API docs (``api/__init__.py``) and tests
(``tests/test_{auth,posts,users}.py``; not vendored), then recorded against the running
app on ``seed.py``'s data: every expected status and body fragment below is what the app
answers. Users are ``seed.SEED_USERS`` handles (logged in through ``POST /api/tokens``);
ids follow ``seed.py``'s insertion order. ``expect_body`` is a subset match
(``lattice.spec.check.subset``).

Not used by the importer; ``tests/bench/test_phase2_apps.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

UNAUTHORIZED = {"code": 401, "message": "Unauthorized"}
FORBIDDEN = {"code": 403, "message": "Forbidden"}
NOT_FOUND = {"code": 404, "message": "Not Found"}
INVALID = {"code": 400, "message": "Validation Error"}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "every API route but registration and token routes needs a token",
        steps=(
            Step("GET", "/api/users", expect_status=401, expect_body=UNAUTHORIZED),
            Step("GET", "/api/posts", expect_status=401, expect_body=UNAUTHORIZED),
            Step("POST", "/api/posts", json={"text": "hi"}, expect_status=401),
            Step("POST", "/api/tokens", expect_status=401, expect_body=UNAUTHORIZED),
        ),
    ),
    Scenario(
        "register a new user",
        steps=(
            Step(
                "POST",
                "/api/users",
                json={"username": "dave", "email": "dave@example.com", "password": "dog"},
                expect_status=201,
                expect_body={
                    "id": 4,
                    "username": "dave",
                    "email": "dave@example.com",
                    "has_password": True,
                    "url": "/api/users/4",
                },
            ),
        ),
    ),
    Scenario(
        "registration validates the username and email",
        steps=(
            Step(
                "POST",
                "/api/users",
                json={"username": "alice", "email": "new@example.com", "password": "dog"},
                expect_status=400,
                expect_body={
                    **INVALID,
                    "errors": {"json": {"username": ["Use a different username."]}},
                },
            ),
            Step(
                "POST",
                "/api/users",
                json={"username": "1dave", "email": "d1@example.com", "password": "dog"},
                expect_status=400,
                expect_body={
                    "errors": {"json": {"username": ["Username must start with a letter"]}}
                },
            ),
            Step(
                "POST",
                "/api/users",
                json={"username": "dave", "email": "bob@example.com", "password": "dog"},
                expect_status=400,
                expect_body={"errors": {"json": {"email": ["Use a different email."]}}},
            ),
            Step(
                "POST",
                "/api/users",
                json={"username": "dave", "email": "not-an-email", "password": "dog"},
                expect_status=400,
                expect_body={"errors": {"json": {"email": ["Not a valid email address."]}}},
            ),
            Step(
                "POST",
                "/api/users",
                json={"username": "dave", "email": "d2@example.com", "password": "do"},
                expect_status=400,
                expect_body={"errors": {"json": {"password": ["Shorter than minimum length 3."]}}},
            ),
        ),
    ),
    Scenario(
        "me returns the caller; others are readable by id or username",
        steps=(
            Step(
                "GET",
                "/api/me",
                user="alice",
                expect_body={"id": 1, "username": "alice", "about_me": "I write about tea."},
            ),
            Step("GET", "/api/users/2", user="alice", expect_body={"username": "bob"}),
            Step("GET", "/api/users/carol", user="alice", expect_body={"id": 3}),
            Step("GET", "/api/users/99", user="alice", expect_status=404, expect_body=NOT_FOUND),
            Step(
                "GET",
                "/api/users",
                user="carol",
                expect_body={"pagination": {"offset": 0, "limit": 25, "count": 3, "total": 3}},
            ),
        ),
    ),
    Scenario(
        "editing my password needs the old one",
        steps=(
            Step("PUT", "/api/me", user="bob", json={"password": "newpass"}, expect_status=400),
            Step(
                "PUT",
                "/api/me",
                user="bob",
                json={"password": "newpass", "old_password": "wrong"},
                expect_status=400,
                expect_body={"errors": {"json": {"old_password": ["Password is incorrect"]}}},
            ),
            Step(
                "PUT",
                "/api/me",
                user="bob",
                json={"password": "newpass", "old_password": "bobpass1", "about_me": "Hi!"},
                expect_body={"username": "bob", "about_me": "Hi!"},
            ),
        ),
    ),
    Scenario(
        "a new post belongs to the caller",
        steps=(
            Step(
                "POST",
                "/api/posts",
                user="carol",
                json={"text": "This is a test post"},
                expect_status=201,
                expect_body={
                    "id": 5,
                    "text": "This is a test post",
                    "url": "/api/posts/5",
                    "author": {"username": "carol"},
                },
            ),
            Step(
                "GET",
                "/api/users/3/posts",
                user="alice",
                expect_body={"data": [{"id": 5}], "pagination": {"total": 1}},
            ),
        ),
    ),
    Scenario(
        "post text is 1 to 280 characters",
        steps=(
            Step(
                "POST",
                "/api/posts",
                user="alice",
                json={"text": ""},
                expect_status=400,
                expect_body={"errors": {"json": {"text": ["Length must be between 1 and 280."]}}},
            ),
            Step("POST", "/api/posts", user="alice", json={"text": "x" * 281}, expect_status=400),
            Step("POST", "/api/posts", user="alice", json={"text": "x" * 280}, expect_status=201),
            Step(
                "POST",
                "/api/posts",
                user="alice",
                json={},
                expect_status=400,
                expect_body={"errors": {"json": {"text": ["Missing data for required field."]}}},
            ),
        ),
    ),
    Scenario(
        "any logged-in user can read any post; listings are newest first",
        steps=(
            Step(
                "GET",
                "/api/posts/1",
                user="bob",
                expect_body={"id": 1, "text": "First cup of the day.", "author": {"id": 1}},
            ),
            Step("GET", "/api/posts/42", user="bob", expect_status=404, expect_body=NOT_FOUND),
            Step(
                "GET",
                "/api/posts",
                user="carol",
                expect_body={
                    "data": [{"id": 4}, {"id": 3}, {"id": 2}, {"id": 1}],
                    "pagination": {"total": 4, "count": 4},
                },
            ),
            Step(
                "GET",
                "/api/posts",
                user="carol",
                params={"limit": 2, "offset": 1},
                expect_body={
                    "data": [{"id": 3}, {"id": 2}],
                    "pagination": {"offset": 1, "limit": 2, "count": 2, "total": 4},
                },
            ),
            Step("GET", "/api/posts", user="carol", params={"offset": 9}, expect_status=400),
        ),
    ),
    Scenario(
        "only the author may edit a post",
        steps=(
            Step(
                "PUT",
                "/api/posts/1",
                user="bob",
                json={"text": "hijacked"},
                expect_status=403,
                expect_body=FORBIDDEN,
            ),
            Step(
                "PUT",
                "/api/posts/1",
                user="alice",
                json={"text": "edited"},
                expect_body={"id": 1, "text": "edited"},
            ),
            Step("GET", "/api/posts/1", user="bob", expect_body={"text": "edited"}),
            Step("PUT", "/api/posts/77", user="alice", json={"text": "x"}, expect_status=404),
        ),
    ),
    Scenario(
        "only the author may delete a post",
        steps=(
            Step("DELETE", "/api/posts/3", user="alice", expect_status=403, expect_body=FORBIDDEN),
            Step("GET", "/api/posts/3", user="alice", expect_body={"id": 3}),
            Step("DELETE", "/api/posts/3", user="bob", expect_status=204),
            Step("GET", "/api/posts/3", user="alice", expect_status=404),
        ),
    ),
    Scenario(
        "the feed holds my posts and those of users I follow",
        steps=(
            Step(
                "GET",
                "/api/feed",
                user="alice",
                expect_body={"data": [{"id": 4}, {"id": 3}, {"id": 2}, {"id": 1}]},
            ),
            Step("GET", "/api/feed", user="bob", expect_body={"data": [{"id": 4}, {"id": 3}]}),
            Step("GET", "/api/feed", user="carol", expect_body={"data": [{"id": 2}, {"id": 1}]}),
        ),
    ),
    Scenario(
        "follow and unfollow",
        steps=(
            Step("GET", "/api/me/following/2", user="alice", expect_status=204),
            Step("POST", "/api/me/following/2", user="alice", expect_status=409),
            Step("GET", "/api/me/following/3", user="alice", expect_status=404),
            Step("POST", "/api/me/following/3", user="alice", expect_status=204),
            Step(
                "GET",
                "/api/me/following",
                user="alice",
                expect_body={"data": [{"username": "bob"}, {"username": "carol"}]},
            ),
            Step("DELETE", "/api/me/following/2", user="alice", expect_status=204),
            Step("DELETE", "/api/me/following/2", user="alice", expect_status=409),
            Step("POST", "/api/me/following/99", user="alice", expect_status=404),
        ),
    ),
    Scenario(
        "followers and following of any user",
        steps=(
            Step(
                "GET",
                "/api/users/1/followers",
                user="bob",
                expect_body={"data": [{"username": "carol"}]},
            ),
            Step(
                "GET",
                "/api/users/1/following",
                user="bob",
                expect_body={"data": [{"username": "bob"}]},
            ),
            Step(
                "GET",
                "/api/me/followers",
                user="bob",
                expect_body={"data": [{"username": "alice"}]},
            ),
            Step("GET", "/api/users/99/followers", user="bob", expect_status=404),
        ),
    ),
    Scenario(
        "a password reset request answers 204 for known and unknown emails",
        steps=(
            Step(
                "POST", "/api/tokens/reset", json={"email": "alice@example.com"}, expect_status=204
            ),
            Step(
                "POST", "/api/tokens/reset", json={"email": "nobody@example.com"}, expect_status=204
            ),
            Step(
                "PUT",
                "/api/tokens/reset",
                json={"token": "bad", "new_password": "foo"},
                expect_status=400,
            ),
        ),
    ),
    Scenario(
        "a refresh needs a valid refresh token",
        steps=(
            Step(
                "PUT",
                "/api/tokens",
                json={"access_token": "x", "refresh_token": "y"},
                expect_status=401,
            ),
        ),
    ),
)
