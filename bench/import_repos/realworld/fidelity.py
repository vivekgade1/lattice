"""Fidelity scenarios for the vendored flask-realworld-example-app (``realworld``).

Written from the upstream tests (``tests/test_{authentication,profile,articles}.py``; not
vendored), the README and the RealWorld ("Conduit") API spec the app implements
(https://realworld-docs.netlify.app/specifications/backend/endpoints/), then recorded
against the running app on ``seed.py``'s data: every expected status and body fragment
below is what the app answers, including the upstream defects noted inline. Users are
``seed.SEED_USERS`` handles (logged in through ``POST /api/users/login``). Ids follow
``seed.py``'s insertion order. ``expect_body`` is a subset match
(``lattice.spec.check.subset``).

Not used by the importer; ``bench/heldout/reserve/test_reserve_app.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

NO_HEADER = {"msg": "Missing Authorization Header"}
USER_NOT_FOUND = {"errors": {"body": ["User not found"]}}
ALREADY = {"errors": {"body": ["User already registered"]}}
ARTICLE_NOT_FOUND = {"errors": {"body": ["Article not found"]}}
DRAGON = "how-to-train-your-dragon"
WONDERLAND = "alice-in-wonderland"
BOB_ARTICLE = "bob-builds-things"


def _user(email: str, password: str) -> dict:
    return {"user": {"email": email, "password": password}}


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "register answers the new user with a token, who can then log in",  # test_register_user
        steps=(
            Step(
                "POST",
                "/api/users",
                json={"user": {"username": "mo", "email": "mo@mo.mo", "password": "momo"}},
                expect_body={"user": {"username": "mo", "email": "mo@mo.mo", "bio": None}},
            ),
            Step(
                "POST",
                "/api/users/login",
                json=_user("mo@mo.mo", "momo"),
                expect_body={"user": {"username": "mo", "email": "mo@mo.mo"}},
            ),
            Step(
                "GET",
                "/api/profiles/mo",
                expect_body={"profile": {"username": "mo", "following": False}},
            ),
        ),
    ),
    Scenario(
        # test_register_already_registered_user
        "registering a taken username or email answers 422",
        steps=(
            Step(
                "POST",
                "/api/users",
                json={"user": {"username": "alice", "email": "new@example.com", "password": "x1"}},
                expect_status=422,
                expect_body=ALREADY,
            ),
            Step(
                "POST",
                "/api/users",
                json={
                    "user": {"username": "newbie", "email": "alice@example.com", "password": "x1"}
                },
                expect_status=422,
                expect_body=ALREADY,
            ),
            Step(
                "POST",
                "/api/users",
                json={"user": {"username": "newbie", "email": "not-an-email", "password": "x1"}},
                expect_status=422,
            ),
        ),
    ),
    Scenario(
        "login answers the user for valid credentials and 404 otherwise",  # test_user_login
        steps=(
            Step(
                "POST",
                "/api/users/login",
                json=_user("alice@example.com", "alicepass1"),
                expect_body={
                    "user": {
                        "username": "alice",
                        "email": "alice@example.com",
                        "bio": "I write about dragons.",
                    }
                },
            ),
            Step(
                "POST",
                "/api/users/login",
                json=_user("alice@example.com", "wrong"),
                expect_status=404,
                expect_body=USER_NOT_FOUND,
            ),
            Step(
                "POST",
                "/api/users/login",
                json=_user("nobody@example.com", "alicepass1"),
                expect_status=404,
                expect_body=USER_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        "the current user needs a token",  # test_get_user
        steps=(
            Step(
                "GET",
                "/api/user",
                user="alice",
                expect_body={"user": {"username": "alice", "email": "alice@example.com"}},
            ),
            Step("GET", "/api/user", user="bob", expect_body={"user": {"username": "bob"}}),
            Step("GET", "/api/user", expect_status=401, expect_body=NO_HEADER),
        ),
    ),
    Scenario(
        "a user updates their own email, bio and password",  # test_update_user
        steps=(
            Step(
                "PUT",
                "/api/user",
                user="carol",
                json={"user": {"email": "meh@mo.mo", "bio": "I'm a simple man", "password": "hmm"}},
                expect_body={
                    "user": {"username": "carol", "email": "meh@mo.mo", "bio": "I'm a simple man"}
                },
            ),
            Step(
                "GET", "/api/profiles/carol", expect_body={"profile": {"bio": "I'm a simple man"}}
            ),
            Step(
                "POST",
                "/api/users/login",
                json=_user("meh@mo.mo", "hmm"),
                expect_body={"user": {"username": "carol"}},
            ),
            Step(
                "POST",
                "/api/users/login",
                json=_user("carol@example.com", "carolpass1"),
                expect_status=404,
                expect_body=USER_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        "updating the user needs a token and a valid image URL",
        steps=(
            Step(
                "PUT",
                "/api/user",
                json={"user": {"bio": "anon"}},
                expect_status=401,
                expect_body=NO_HEADER,
            ),
            Step(
                "PUT",
                "/api/user",
                user="alice",
                json={"user": {"image": "not a url"}},
                expect_status=422,
            ),
            Step(
                "PUT",
                "/api/user",
                user="alice",
                json={"user": {"image": "https://img.example.com/a.png"}},
                expect_body={
                    "user": {
                        "image": "https://img.example.com/a.png",
                        "bio": "I write about dragons.",
                    }
                },
            ),
        ),
    ),
    Scenario(
        "profiles are public; an unknown username is 404",  # test_get_profile_*
        steps=(
            Step(
                "GET",
                "/api/profiles/alice",
                expect_body={
                    "profile": {
                        "username": "alice",
                        "email": "alice@example.com",
                        "bio": "I write about dragons.",
                        "following": False,
                    }
                },
            ),
            Step(
                "GET",
                "/api/profiles/bob",
                user="alice",
                expect_body={"profile": {"username": "bob", "following": True}},
            ),
            Step(
                "GET",
                "/api/profiles/alice",
                user="bob",
                expect_body={"profile": {"username": "alice", "following": False}},
            ),
            Step("GET", "/api/profiles/nobody", expect_status=404, expect_body=USER_NOT_FOUND),
        ),
    ),
    Scenario(
        "follow and unfollow a user",  # test_follow_user, test_unfollow_user
        steps=(
            Step(
                "POST",
                "/api/profiles/carol/follow",
                user="bob",
                expect_body={"profile": {"username": "carol", "following": True}},
            ),
            Step(
                "GET",
                "/api/profiles/carol",
                user="bob",
                expect_body={"profile": {"following": True}},
            ),
            Step(
                "DELETE",
                "/api/profiles/carol/follow",
                user="bob",
                expect_body={"profile": {"username": "carol", "following": False}},
            ),
            Step(
                "GET",
                "/api/profiles/carol",
                user="bob",
                expect_body={"profile": {"following": False}},
            ),
        ),
    ),
    Scenario(
        "following needs a token and an existing user",
        steps=(
            Step("POST", "/api/profiles/bob/follow", expect_status=401, expect_body=NO_HEADER),
            Step("DELETE", "/api/profiles/bob/follow", expect_status=401, expect_body=NO_HEADER),
            Step(
                "POST",
                "/api/profiles/nobody/follow",
                user="alice",
                expect_status=404,
                expect_body=USER_NOT_FOUND,
            ),
            Step(
                "DELETE",
                "/api/profiles/nobody/follow",
                user="alice",
                expect_status=404,
                expect_body=USER_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        "list articles, filtered by author, tag and favoriter",  # test_get_articles_by_*
        steps=(
            Step("GET", "/api/articles", expect_body={"articlesCount": 3}),
            Step(
                "GET",
                "/api/articles",
                params={"author": "bob"},
                expect_body={
                    "articlesCount": 1,
                    "articles": [
                        {"slug": BOB_ARTICLE, "author": {"username": "bob"}, "tagList": ["dragons"]}
                    ],
                },
            ),
            Step(
                "GET", "/api/articles", params={"author": "alice"}, expect_body={"articlesCount": 2}
            ),
            Step(
                "GET", "/api/articles", params={"tag": "dragons"}, expect_body={"articlesCount": 2}
            ),
            Step(
                "GET",
                "/api/articles",
                params={"tag": "stories"},
                expect_body={"articlesCount": 1, "articles": [{"slug": WONDERLAND}]},
            ),
            Step(
                "GET",
                "/api/articles",
                params={"favorited": "bob"},
                expect_body={
                    "articlesCount": 1,
                    "articles": [{"slug": DRAGON, "favoritesCount": 1}],
                },
            ),
            Step(
                "GET",
                "/api/articles",
                params={"limit": 1, "offset": 1},
                expect_body={"articlesCount": 1, "articles": [{"slug": WONDERLAND}]},
            ),
        ),
    ),
    Scenario(
        "a single article by slug; an unknown slug is 404",
        steps=(
            Step(
                "GET",
                f"/api/articles/{DRAGON}",
                expect_body={
                    "article": {
                        "slug": DRAGON,
                        "title": "How to train your dragon",
                        "description": "Ever wonder how?",
                        "body": "It takes a Jacobian.",
                        "tagList": ["dragons", "training"],
                        "favoritesCount": 1,
                        "author": {"username": "alice"},
                    }
                },
            ),
            Step(
                "GET",
                "/api/articles/no-such-article",
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        # test_make_article
        "create an article with tags; it is listed under its author and its tags",
        steps=(
            Step(
                "POST",
                "/api/articles",
                user="bob",
                json={
                    "article": {
                        "title": "How to train your cat",
                        "description": "Ever wonder how?",
                        "body": "You have to believe",
                        "tagList": ["reactjs", "angularjs", "dragons"],
                    }
                },
                expect_body={
                    "article": {
                        "slug": "how-to-train-your-cat",
                        "body": "You have to believe",
                        "favoritesCount": 0,
                        "tagList": ["reactjs", "angularjs", "dragons"],
                        "author": {"username": "bob", "email": "bob@example.com"},
                    }
                },
            ),
            Step(
                "GET",
                "/api/articles/how-to-train-your-cat",
                expect_body={"article": {"author": {"username": "bob"}}},
            ),
            Step(
                "GET", "/api/articles", params={"author": "bob"}, expect_body={"articlesCount": 2}
            ),
            Step(
                "GET",
                "/api/tags",
                expect_body={"tags": ["dragons", "training", "stories", "reactjs", "angularjs"]},
            ),
        ),
    ),
    Scenario(
        "creating an article needs a token",
        steps=(
            Step(
                "POST",
                "/api/articles",
                json={"article": {"title": "Anon", "description": "d", "body": "b"}},
                expect_status=401,
                expect_body=NO_HEADER,
            ),
            Step("GET", "/api/articles", expect_body={"articlesCount": 3}),
        ),
    ),
    Scenario(
        "only the author updates an article; anyone else gets 404",
        steps=(
            Step(
                "PUT",
                f"/api/articles/{DRAGON}",
                user="alice",
                json={"article": {"body": "Updated body"}},
                expect_body={
                    "article": {
                        "slug": DRAGON,
                        "body": "Updated body",
                        "author": {"username": "alice"},
                    }
                },
            ),
            Step(
                "PUT",
                f"/api/articles/{DRAGON}",
                user="bob",
                json={"article": {"title": "Hijack"}},
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
            Step(
                "PUT",
                f"/api/articles/{DRAGON}",
                json={"article": {"title": "Anon"}},
                expect_status=401,
                expect_body=NO_HEADER,
            ),
            Step(
                "GET",
                f"/api/articles/{DRAGON}",
                expect_body={
                    "article": {"title": "How to train your dragon", "body": "Updated body"}
                },
            ),
        ),
    ),
    Scenario(
        "the author deletes an article; someone else's delete is an upstream 500",
        steps=(
            Step("DELETE", f"/api/articles/{WONDERLAND}", user="alice", expect_body=""),
            Step(
                "GET",
                f"/api/articles/{WONDERLAND}",
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
            # upstream defect: the view calls .delete() on None when the caller is not the
            # author (or the slug is unknown); the article survives
            Step("DELETE", f"/api/articles/{BOB_ARTICLE}", user="alice", expect_status=500),
            Step(
                "GET",
                f"/api/articles/{BOB_ARTICLE}",
                expect_body={"article": {"slug": BOB_ARTICLE}},
            ),
            Step(
                "DELETE", f"/api/articles/{BOB_ARTICLE}", expect_status=401, expect_body=NO_HEADER
            ),
        ),
    ),
    Scenario(
        "favorite and unfavorite an article",  # test_favorite_an_article
        steps=(
            Step(
                "POST",
                f"/api/articles/{BOB_ARTICLE}/favorite",
                user="carol",
                expect_body={
                    "article": {"slug": BOB_ARTICLE, "favorited": True, "favoritesCount": 1}
                },
            ),
            # upstream defect: `?favorited=<username>` joins the favoriters but filters on an
            # unjoined `users` table, so it lists every favorited article whenever that
            # username exists (bob's favorite of article 1 shows up for carol too)
            Step(
                "GET",
                "/api/articles",
                params={"favorited": "carol"},
                expect_body={
                    "articlesCount": 2,
                    "articles": [{"slug": DRAGON}, {"slug": BOB_ARTICLE}],
                },
            ),
            Step(
                "DELETE",
                f"/api/articles/{BOB_ARTICLE}/favorite",
                user="carol",
                expect_body={
                    "article": {"slug": BOB_ARTICLE, "favorited": False, "favoritesCount": 0}
                },
            ),
            Step(
                "GET",
                "/api/articles",
                params={"favorited": "carol"},
                expect_body={"articlesCount": 1, "articles": [{"slug": DRAGON}]},
            ),
            Step(
                "GET",
                "/api/articles",
                params={"favorited": "nobody"},
                expect_body={"articlesCount": 0, "articles": []},
            ),
        ),
    ),
    Scenario(
        "favoriting needs a token and an existing article",
        steps=(
            Step(
                "POST", f"/api/articles/{DRAGON}/favorite", expect_status=401, expect_body=NO_HEADER
            ),
            Step(
                "POST",
                "/api/articles/no-such-article/favorite",
                user="carol",
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
            Step(
                "DELETE",
                "/api/articles/no-such-article/favorite",
                user="carol",
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        # test_make_comment_correct_schema
        "comments are listed publicly and added by any logged-in user",
        steps=(
            Step(
                "GET",
                f"/api/articles/{DRAGON}/comments",
                expect_body={
                    "comments": [
                        {"id": 1, "body": "Great dragon advice.", "author": {"username": "bob"}}
                    ]
                },
            ),
            Step(
                "POST",
                f"/api/articles/{DRAGON}/comments",
                user="carol",
                json={"comment": {"body": "You have to believe"}},
                expect_body={
                    "comment": {
                        "id": 3,
                        "body": "You have to believe",
                        "author": {"username": "carol", "email": "carol@example.com"},
                    }
                },
            ),
            Step(
                "GET",
                f"/api/articles/{DRAGON}/comments",
                expect_body={"comments": [{"id": 1}, {"id": 3}]},
            ),
        ),
    ),
    Scenario(
        "commenting needs a token and an existing article",
        steps=(
            Step(
                "POST",
                f"/api/articles/{DRAGON}/comments",
                json={"comment": {"body": "anon"}},
                expect_status=401,
                expect_body=NO_HEADER,
            ),
            Step(
                "POST",
                "/api/articles/no-such-article/comments",
                user="carol",
                json={"comment": {"body": "x"}},
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
            Step(
                "GET",
                "/api/articles/no-such-article/comments",
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        "the comment's author deletes it; anyone else's delete is an upstream 500",
        steps=(
            # upstream defect: the view calls .delete() on None when the caller is not the
            # comment's author; the comment survives
            Step("DELETE", f"/api/articles/{DRAGON}/comments/1", user="alice", expect_status=500),
            Step("GET", f"/api/articles/{DRAGON}/comments", expect_body={"comments": [{"id": 1}]}),
            Step(
                "DELETE",
                f"/api/articles/{DRAGON}/comments/1",
                expect_status=401,
                expect_body=NO_HEADER,
            ),
            Step("DELETE", f"/api/articles/{DRAGON}/comments/1", user="bob", expect_body=""),
            Step("GET", f"/api/articles/{DRAGON}/comments", expect_body={"comments": []}),
            Step(
                "DELETE",
                "/api/articles/no-such-article/comments/1",
                user="bob",
                expect_status=404,
                expect_body=ARTICLE_NOT_FOUND,
            ),
        ),
    ),
    Scenario(
        "the feed holds articles by followed users, newest first",
        steps=(
            Step(
                "GET",
                "/api/articles/feed",
                user="alice",
                expect_body={
                    "articlesCount": 1,
                    "articles": [
                        {"slug": BOB_ARTICLE, "author": {"username": "bob", "following": True}}
                    ],
                },
            ),
            Step("GET", "/api/articles/feed", user="bob", expect_body={"articlesCount": 0}),
            Step(
                "POST",
                "/api/profiles/alice/follow",
                user="bob",
                expect_body={"profile": {"following": True}},
            ),
            Step(
                "GET",
                "/api/articles/feed",
                user="bob",
                expect_body={
                    "articlesCount": 2,
                    "articles": [{"slug": WONDERLAND}, {"slug": DRAGON}],
                },
            ),
            Step("GET", "/api/articles/feed", expect_status=401, expect_body=NO_HEADER),
        ),
    ),
    Scenario(
        "tags are public",
        steps=(
            Step("GET", "/api/tags", expect_body={"tags": ["dragons", "training", "stories"]}),
            Step(
                "GET",
                "/api/tags",
                user="carol",
                expect_body={"tags": ["dragons", "training", "stories"]},
            ),
        ),
    ),
)
