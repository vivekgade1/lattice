"""Fidelity scenarios for the vendored flasky API (``bench/import_repos/flasky``).

Written from the upstream tests (``tests/test_api.py``; not vendored) and the API code's
documented behaviour, then recorded against the running app on ``seed.py``'s data:
every expected status and body fragment below is what the app answers. Users are
``seed.SEED_USERS`` handles (logged in with a token from ``POST /api/v1/tokens/``;
``carol``, who cannot get one, sends her email and password). Ids follow ``seed.py``'s
insertion order. ``expect_body`` is a subset match (``lattice.spec.check.subset``).

Not used by the importer; ``tests/bench/test_phase2_apps.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

UNAUTHORIZED = {"error": "unauthorized", "message": "Invalid credentials"}
FORBIDDEN = {"error": "forbidden", "message": "Insufficient permissions"}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "every API route needs credentials",
        steps=(
            Step("GET", "/api/v1/posts/", expect_status=401, expect_body=UNAUTHORIZED),
            Step("GET", "/api/v1/users/3", expect_status=401, expect_body=UNAUTHORIZED),
            Step("POST", "/api/v1/tokens/", expect_status=401, expect_body=UNAUTHORIZED),
        ),
    ),
    Scenario(
        "an unconfirmed account is refused",
        steps=(
            Step(
                "GET",
                "/api/v1/posts/",
                user="carol",
                expect_status=403,
                expect_body={"error": "forbidden", "message": "Unconfirmed account"},
            ),
            Step("POST", "/api/v1/tokens/", user="carol", expect_status=403),
        ),
    ),
    Scenario(
        "a token cannot be used to get another token",
        steps=(
            Step(
                "POST", "/api/v1/tokens/", user="alice", expect_status=401, expect_body=UNAUTHORIZED
            ),
        ),
    ),
    Scenario(
        "any confirmed user reads posts, newest last, 20 a page",
        steps=(
            Step(
                "GET",
                "/api/v1/posts/",
                user="bob",
                expect_body={
                    "count": 4,
                    "prev": None,
                    "next": None,
                    "posts": [
                        {"url": "/api/v1/posts/1"},
                        {"url": "/api/v1/posts/2"},
                        {"url": "/api/v1/posts/3"},
                        {"url": "/api/v1/posts/4"},
                    ],
                },
            ),
            Step(
                "GET",
                "/api/v1/posts/",
                user="bob",
                params={"page": 2},
                expect_body={"count": 4, "posts": []},
            ),
            Step(
                "GET",
                "/api/v1/posts/1",
                user="role_moderator",
                expect_body={
                    "body": "Hello from *Alice*.",
                    "body_html": "<p>Hello from <em>Alice</em>.</p>",
                    "author_url": "/api/v1/users/3",
                    "comment_count": 1,
                },
            ),
            Step("GET", "/api/v1/posts/9", user="bob", expect_status=404),
        ),
    ),
    Scenario(
        "a new post belongs to the caller and renders markdown",
        steps=(
            Step(
                "POST",
                "/api/v1/posts/",
                user="bob",
                json={"body": "body of the *blog* post"},
                expect_status=201,
                expect_body={
                    "url": "/api/v1/posts/5",
                    "author_url": "/api/v1/users/4",
                    "body_html": "<p>body of the <em>blog</em> post</p>",
                },
            ),
            Step(
                "GET",
                "/api/v1/users/4/posts/",
                user="alice",
                expect_body={"count": 3, "posts": [{"url": "/api/v1/posts/5"}, {}, {}]},
            ),
        ),
    ),
    Scenario(
        "an empty post is a bad request",
        steps=(
            Step(
                "POST",
                "/api/v1/posts/",
                user="bob",
                json={"body": ""},
                expect_status=400,
                expect_body={"error": "bad request", "message": "post does not have a body"},
            ),
            Step("POST", "/api/v1/posts/", user="bob", json={}, expect_status=400),
            Step("GET", "/api/v1/posts/", user="bob", expect_body={"count": 4}),
        ),
    ),
    Scenario(
        "the author edits their post",
        steps=(
            Step(
                "PUT",
                "/api/v1/posts/1",
                user="alice",
                json={"body": "updated body"},
                expect_body={
                    "url": "/api/v1/posts/1",
                    "body": "updated body",
                    "body_html": "<p>updated body</p>",
                },
            ),
        ),
    ),
    Scenario(
        "another user or a moderator cannot edit a post",
        steps=(
            Step(
                "PUT",
                "/api/v1/posts/1",
                user="bob",
                json={"body": "x"},
                expect_status=403,
                expect_body=FORBIDDEN,
            ),
            Step(
                "PUT",
                "/api/v1/posts/1",
                user="role_moderator",
                json={"body": "x"},
                expect_status=403,
                expect_body=FORBIDDEN,
            ),
            Step("GET", "/api/v1/posts/1", user="bob", expect_body={"body": "Hello from *Alice*."}),
        ),
    ),
    Scenario(
        "an administrator edits anyone's post",
        steps=(
            Step(
                "PUT",
                "/api/v1/posts/3",
                user="admin",
                json={"body": "moderated"},
                expect_body={"body": "moderated", "author_url": "/api/v1/users/4"},
            ),
            Step("PUT", "/api/v1/posts/30", user="admin", json={"body": "x"}, expect_status=404),
        ),
    ),
    Scenario(
        "users are readable with their post counts",
        steps=(
            Step(
                "GET",
                "/api/v1/users/3",
                user="bob",
                expect_body={
                    "username": "alice",
                    "url": "/api/v1/users/3",
                    "post_count": 2,
                    "posts_url": "/api/v1/users/3/posts/",
                    "followed_posts_url": "/api/v1/users/3/timeline/",
                },
            ),
            Step(
                "GET",
                "/api/v1/users/5",
                user="bob",
                expect_body={"username": "carol", "post_count": 0},
            ),
            Step("GET", "/api/v1/users/42", user="bob", expect_status=404),
        ),
    ),
    Scenario(
        "a user's posts, newest first, and their timeline of followed users",
        steps=(
            Step(
                "GET",
                "/api/v1/users/3/posts/",
                user="bob",
                expect_body={
                    "count": 2,
                    "posts": [{"url": "/api/v1/posts/2"}, {"url": "/api/v1/posts/1"}],
                },
            ),
            Step("GET", "/api/v1/users/3/timeline/", user="bob", expect_body={"count": 4}),
            Step(
                "GET",
                "/api/v1/users/4/timeline/",
                user="bob",
                expect_body={
                    "count": 2,
                    "posts": [{"url": "/api/v1/posts/4"}, {"url": "/api/v1/posts/3"}],
                },
            ),
        ),
    ),
    Scenario(
        "comments: list, read, and per post",
        steps=(
            Step("GET", "/api/v1/comments/", user="alice", expect_body={"count": 3}),
            Step(
                "GET",
                "/api/v1/comments/2",
                user="alice",
                expect_body={
                    "body": "Thanks Bob!",
                    "post_url": "/api/v1/posts/3",
                    "author_url": "/api/v1/users/3",
                },
            ),
            Step(
                "GET",
                "/api/v1/posts/3/comments/",
                user="alice",
                expect_body={
                    "count": 2,
                    "comments": [{"url": "/api/v1/comments/2"}, {"url": "/api/v1/comments/3"}],
                },
            ),
            Step("GET", "/api/v1/comments/9", user="alice", expect_status=404),
        ),
    ),
    Scenario(
        "a new comment belongs to the caller and its post",
        steps=(
            Step(
                "POST",
                "/api/v1/posts/2/comments/",
                user="bob",
                json={"body": "Good [post](http://example.com)!"},
                expect_status=201,
                expect_body={
                    "url": "/api/v1/comments/4",
                    "post_url": "/api/v1/posts/2",
                    "author_url": "/api/v1/users/4",
                    "body_html": 'Good <a href="http://example.com" rel="nofollow">post</a>!',
                },
            ),
            Step("GET", "/api/v1/posts/2/comments/", user="bob", expect_body={"count": 1}),
            Step(
                "POST",
                "/api/v1/posts/2/comments/",
                user="bob",
                json={"body": ""},
                expect_status=400,
                expect_body={"error": "bad request", "message": "comment does not have a body"},
            ),
            Step(
                "POST",
                "/api/v1/posts/99/comments/",
                user="bob",
                json={"body": "x"},
                expect_status=404,
            ),
        ),
    ),
    Scenario(
        "moderators and administrators write posts and comments like users",
        steps=(
            Step(
                "POST",
                "/api/v1/posts/",
                user="role_moderator",
                json={"body": "mod post"},
                expect_status=201,
                expect_body={"url": "/api/v1/posts/5", "author_url": "/api/v1/users/2"},
            ),
            Step(
                "POST",
                "/api/v1/posts/5/comments/",
                user="admin",
                json={"body": "admin comment"},
                expect_status=201,
                expect_body={"post_url": "/api/v1/posts/5", "author_url": "/api/v1/users/1"},
            ),
            Step("GET", "/api/v1/users/2", user="alice", expect_body={"post_count": 1}),
        ),
    ),
    Scenario(
        "a token, once issued, authenticates every API route",
        steps=(
            Step("GET", "/api/v1/users/1", user="admin", expect_body={"username": "admin"}),
            Step(
                "GET",
                "/api/v1/comments/3",
                user="alice",
                expect_body={"body": "Please keep it civil."},
            ),
            Step("GET", "/api/v1/posts/4/comments/", user="alice", expect_body={"count": 0}),
        ),
    ),
)
