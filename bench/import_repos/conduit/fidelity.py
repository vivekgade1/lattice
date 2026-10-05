"""Fidelity scenarios for the vendored conduit-drf (``bench/import_repos/conduit``).

Upstream has no tests (``api/tests.py`` is Django's empty stub), so these are written
from the README, the ``api_root`` listing and the view docstrings (the request examples
of ``TopicViewSet``, ``CommentViewSet`` and ``UserViewSet``), then recorded against the
running app on ``seed.py``'s data: every expected status and body fragment below is what
the app answers. Users are ``seed.SEED_USERS`` handles (logged in through
``POST /api/token/``). Ids follow ``seed.py``. ``expect_body`` is a subset match
(``lattice.spec.check.subset``; a list must match element-wise with the same length).

Upstream answers most "not found" and "invalid" outcomes with HTTP 200 and the real code
in the body (``{"code": 404, "msg": ...}``); the scenarios record that as it is.

Not used by the importer; ``bench/heldout/test_heldout_apps.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

NO_CREDENTIALS = {"detail": "Authentication credentials were not provided."}
FORBIDDEN = {"detail": "You do not have permission to perform this action."}
TOPIC_NOT_FOUND = {"code": 404, "msg": "Topic not found."}
TOPIC = {"title": "Edited title", "content": "Edited content.", "tags": ["python"]}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "the API root lists the routes to anyone",
        steps=(
            Step(
                "GET",
                "/api/",
                expect_body=[{"topics": "http://testserver/api/topics/"}] + [{}] * 12,
            ),
        ),
    ),
    Scenario(
        "anyone lists topics, newest first, in pages",
        steps=(
            Step(
                "GET",
                "/api/topics/",
                expect_body={
                    "code": 200,
                    "count": 3,
                    "total": 3,
                    "msg": "Topics query succeed.",
                    "data": [
                        {"_id": 3, "title": "Bob on Django", "tags": ["django", "web"],
                         "user": {"username": "bob"}},  # fmt: skip
                        {"_id": 2, "tags": []},
                        {"_id": 1, "favorite": 1},
                    ],
                },
            ),
            Step("GET", "/api/topics/", params={"size": 2, "page": 2},
                 expect_body={"count": 3, "data": [{"_id": 1}],
                              "next": None}),  # fmt: skip
        ),
    ),
    Scenario(
        "a topic's detail, with its comments; an unknown topic says 404 in the body",
        steps=(
            Step(
                "GET",
                "/api/topic/1/",
                expect_body={
                    "code": 200,
                    "data": {
                        "_id": 1,
                        "title": "Alice on typing",
                        "favorite": 1,
                        "user": {"username": "alice"},
                        "comments": [{"content": "Nice notes, Alice.",
                                      "user": {"username": "bob"}}],  # fmt: skip
                    },
                },
            ),
            Step("GET", "/api/topic/99/", expect_body=TOPIC_NOT_FOUND),
        ),
    ),
    Scenario(
        "creating a topic needs a login; new tags are created on the fly",
        steps=(
            Step("POST", "/api/topics/", json={**TOPIC, "user": 2}, expect_status=401,
                 expect_body=NO_CREDENTIALS),  # fmt: skip
            Step(
                "POST",
                "/api/topics/",
                user="alice",
                json={"title": "Welcome", "content": "Hello.", "tags": ["Python", "Rust"],
                      "user": 2},  # fmt: skip
                expect_body={"code": 201, "msg": "Topic creation succeed.",
                             "data": {"title": "Welcome", "tags": [1, 4], "user": 2}},  # fmt: skip
            ),
            Step("GET", "/api/tags/", expect_body={"total": 4}),
            Step("GET", "/api/topic/4/", expect_body={"data": {"tags": ["python", "rust"]}}),
        ),
    ),
    Scenario(
        "a topic is stored for the user the body names",
        steps=(
            Step(
                "POST",
                "/api/topics/",
                user="alice",
                json={"title": "As Bob", "content": "Written by Alice.", "tags": [], "user": 3},
                expect_body={"code": 201, "data": {"user": 3}},
            ),
            Step("GET", "/api/topic/4/", expect_body={"data": {"user": {"username": "bob"}}}),
        ),
    ),
    Scenario(
        "an invalid topic answers 200 with code 400 in the body",
        steps=(
            Step(
                "POST",
                "/api/topics/",
                user="bob",
                json={"title": "", "content": "x", "tags": [], "user": 3},
                expect_body={"code": 400, "msg": "Topic creation failed.",
                             "error": {"title": ["This field may not be blank."]}},  # fmt: skip
            ),
            Step("POST", "/api/topics/", user="bob",
                 json={"title": "No user", "content": "x", "tags": []},
                 expect_body={"code": 400}),  # fmt: skip
        ),
    ),
    Scenario(
        "only a topic's owner or staff updates it",
        steps=(
            Step("PUT", "/api/topic/1/", json={**TOPIC, "user": 2}, expect_status=401),
            Step("PUT", "/api/topic/1/", user="bob", json={**TOPIC, "user": 2},
                 expect_status=403, expect_body=FORBIDDEN),  # fmt: skip
            Step("PUT", "/api/topic/1/", user="alice", json={**TOPIC, "user": 2},
                 expect_body={"code": 200, "msg": "Topic update succeed.",
                              "data": {"title": "Edited title", "tags": [1]}}),  # fmt: skip
            Step("PUT", "/api/topic/3/", user="admin", json={**TOPIC, "user": 3},
                 expect_body={"code": 200}),  # fmt: skip
            Step("PUT", "/api/topic/99/", user="alice", json={**TOPIC, "user": 2},
                 expect_body=TOPIC_NOT_FOUND),  # fmt: skip
        ),
    ),
    Scenario(
        "only a topic's owner or staff deletes it",
        steps=(
            Step("DELETE", "/api/topic/3/", expect_status=401),
            Step("DELETE", "/api/topic/3/", user="alice", expect_status=403, expect_body=FORBIDDEN),
            Step("DELETE", "/api/topic/3/", user="bob",
                 expect_body={"code": 204, "msg": "Topic delete succeed."}),  # fmt: skip
            Step("GET", "/api/topic/3/", expect_body=TOPIC_NOT_FOUND),
            Step("DELETE", "/api/topic/1/", user="admin", expect_body={"code": 204}),
            Step("GET", "/api/topics/", expect_body={"count": 1}),
        ),
    ),
    Scenario(
        "my topics and my favorites need a login",
        steps=(
            Step("GET", "/api/my-topics/", expect_status=401, expect_body=NO_CREDENTIALS),
            Step(
                "GET",
                "/api/my-topics/",
                user="alice",
                expect_body={"count": 2, "total": 2, "msg": "My own topics query succeed.",
                             "user": {"username": "alice"},
                             "data": [{"_id": 2}, {"_id": 1}]},  # fmt: skip
            ),
            Step("GET", "/api/my-favorites/", expect_status=401),
            Step("GET", "/api/my-favorites/", user="alice",
                 expect_body={"total": 1, "data": [{"_id": 3}]}),  # fmt: skip
        ),
    ),
    Scenario(
        "a user's topics and favorites, by username, need a login",
        steps=(
            Step("GET", "/api/profile/bob/", expect_status=401),
            Step("GET", "/api/profile/bob/", user="alice",
                 expect_body={"msg": "User bob's own topics query succeed.",
                              "user": {"username": "bob"}, "data": [{"_id": 3}]}),  # fmt: skip
            Step("GET", "/api/profile/bob/favorites/", expect_status=401),
            Step("GET", "/api/profile/bob/favorites/", user="alice",
                 expect_body={"total": 1, "data": [{"_id": 1}]}),  # fmt: skip
        ),
    ),
    Scenario(
        "favoring a topic toggles, for a logged-in user",
        steps=(
            Step("POST", "/api/topic/2/favor/", expect_status=401),
            Step("POST", "/api/topic/2/favor/", user="bob",
                 expect_body={"code": 200, "msg": "Topic favor succeed.",
                              "data": {"favorite": 1}}),  # fmt: skip
            Step("GET", "/api/my-favorites/", user="bob", expect_body={"total": 2}),
            Step("POST", "/api/topic/2/favor/", user="bob",
                 expect_body={"msg": "Topic unfavor succeed.", "data": {"favorite": 0}}),  # fmt: skip
            Step("POST", "/api/topic/99/favor/", user="bob", expect_body=TOPIC_NOT_FOUND),
        ),
    ),
    Scenario(
        "a topic's comments are read by logged-in users",
        steps=(
            Step("GET", "/api/topic/1/comment/", expect_status=401),
            Step("GET", "/api/topic/1/comment/", user="alice",
                 expect_body={"code": 200, "msg": "Topic comments query succeed.",
                              "data": [{"content": "Nice notes, Alice.",
                                        "user": {"username": "bob"}}]}),  # fmt: skip
            Step("GET", "/api/topic/1/comment/1/", user="alice",
                 expect_body={"code": 200, "data": {"_id": 1, "content": "Nice notes, Alice."}}),  # fmt: skip
            Step("GET", "/api/topic/1/comment/2/", user="alice",
                 expect_body={"code": 404, "msg": "Comment not found."}),  # fmt: skip
            Step("GET", "/api/topic/99/comment/", user="alice", expect_body=TOPIC_NOT_FOUND),
        ),
    ),
    Scenario(
        "a logged-in user comments as themself",
        steps=(
            Step("POST", "/api/topic/2/comment/", json={"content": "Hi"}, expect_status=401),
            Step(
                "POST",
                "/api/topic/3/comment/",
                user="bob",
                json={"content": "Replying to myself.", "user": 2},
                expect_body={"code": 201, "msg": "Topic comment creation succeed.",
                             "data": {"_id": 3, "comments": [
                                 {"content": "Thanks Bob.", "user": {"username": "alice"}},
                                 {"content": "Replying to myself.", "user": {"username": "bob"}},
                             ]}},  # fmt: skip
            ),
            Step("POST", "/api/topic/3/comment/", user="bob", json={"content": ""},
                 expect_body={"code": 400, "msg": "Topic comment creation failed."}),  # fmt: skip
            Step("POST", "/api/topic/99/comment/", user="bob", json={"content": "x"},
                 expect_body=TOPIC_NOT_FOUND),  # fmt: skip
        ),
    ),
    Scenario(
        # upstream's permitted delete answers 500 (``topic.comments.remove`` does not exist
        # for a non-null foreign key; see PROVENANCE.md), so only the refusals are recorded
        "a comment's deletion is refused to others than its author and staff",
        steps=(
            Step("DELETE", "/api/topic/1/comment/1/", expect_status=401),
            Step("DELETE", "/api/topic/1/comment/1/", user="alice", expect_status=403,
                 expect_body=FORBIDDEN),  # fmt: skip
            Step("DELETE", "/api/topic/1/comment/99/", user="bob",
                 expect_body={"code": 404, "msg": "Comment not found."}),  # fmt: skip
            Step("DELETE", "/api/topic/99/comment/1/", user="bob", expect_body=TOPIC_NOT_FOUND),
        ),
    ),
    Scenario(
        "anyone reads the tags",
        steps=(
            Step("GET", "/api/tags/",
                 expect_body={"code": 200, "total": 3,
                              "data": [{"tag": "web"}, {"tag": "django"}, {"tag": "python"}]}),  # fmt: skip
            Step("GET", "/api/tag/django/", expect_body={"code": 200, "data": {"_id": 2,
                                                                                "tag": "django"}}),  # fmt: skip
            Step("GET", "/api/tag/nope/", expect_body={"code": 404, "msg": "Tag not found."}),
        ),
    ),
    Scenario(
        "the users list and a user's detail need a login",
        steps=(
            Step("GET", "/api/users/", expect_status=401, expect_body=NO_CREDENTIALS),
            Step("GET", "/api/users/", user="alice",
                 expect_body={"count": 4, "msg": "Users query succeed."}),  # fmt: skip
            Step("GET", "/api/user/bob/", user="alice",
                 expect_body={"code": 200, "data": {"_id": 3, "username": "bob",
                                                    "favorites": [1]}}),  # fmt: skip
            Step("GET", "/api/user/zed/", user="alice",
                 expect_body={"code": 404, "msg": "User not found."}),  # fmt: skip
        ),
    ),
    Scenario(
        "creating a user needs a login, and matching, unused credentials",
        steps=(
            Step("POST", "/api/users/", json={"email": "dan@example.com", "username": "dan",
                                              "password": "danpass12",
                                              "confirm_password": "danpass12"},
                 expect_status=401),  # fmt: skip
            Step("POST", "/api/users/", user="alice",
                 json={"email": "dan@example.com", "username": "dan", "password": "danpass12",
                       "confirm_password": "danpass12"},
                 expect_body={"code": 201, "msg": "User create succeed.",
                              "data": {"_id": 5, "username": "dan"}}),  # fmt: skip
            Step("POST", "/api/token/", json={"email": "dan@example.com", "password": "danpass12"},
                 expect_body={"msg": "Login succeed.", "user": {"username": "dan"}}),  # fmt: skip
            Step("POST", "/api/users/", user="alice",
                 json={"email": "eve@example.com", "username": "eve", "password": "evepass12",
                       "confirm_password": "other"},
                 expect_body={"code": 400,
                              "error": {"confirm_password": ["Passwords don't match."]}}),  # fmt: skip
            Step("POST", "/api/users/", user="alice",
                 json={"email": "bob@example.com", "username": "bobby", "password": "x1234567",
                       "confirm_password": "x1234567"},
                 expect_body={"code": 400, "error": {"email": ["user with this e-mail already exists."]}}),  # fmt: skip
        ),
    ),
    Scenario(
        "a user updates themself; staff update anyone",
        steps=(
            Step("PUT", "/api/user/alice/", user="bob", json={"nickname": "x"}, expect_status=403,
                 expect_body=FORBIDDEN),  # fmt: skip
            Step("PUT", "/api/user/alice/", user="alice", json={"nickname": "Ali", "gender": 0},
                 expect_body={"code": 200, "msg": "User update succeed.",
                              "data": {"nickname": "Ali", "gender": "Female"}}),  # fmt: skip
            Step("PUT", "/api/user/bob/", user="admin", json={"bio": "Edited by staff."},
                 expect_body={"code": 200, "data": {"bio": "Edited by staff."}}),  # fmt: skip
            Step("PUT", "/api/user/alice/", user="alice", json={"username": "alicia"},
                 expect_body={"code": 400,
                              "error": {"username": ["Username can't be changed."]}}),  # fmt: skip
            Step("PUT", "/api/user/zed/", user="alice", json={"bio": "x"},
                 expect_body={"code": 404, "msg": "User not found."}),  # fmt: skip
        ),
    ),
    Scenario(
        "only staff delete users",
        steps=(
            Step("DELETE", "/api/user/bob/", expect_status=401),
            Step("DELETE", "/api/user/bob/", user="alice", expect_status=403),
            Step("DELETE", "/api/user/bob/", user="admin",
                 expect_body={"code": 204, "msg": "User delete succeed."}),  # fmt: skip
            Step("GET", "/api/user/bob/", user="admin", expect_body={"code": 404}),
        ),
    ),
    Scenario(
        "the caller's settings: read and update, but not the e-mail",
        steps=(
            Step("GET", "/api/settings/", expect_status=401, expect_body=NO_CREDENTIALS),
            Step("GET", "/api/settings/", user="alice",
                 expect_body={"code": 200, "msg": "My settings query succeed.",
                              "data": {"_id": 2, "email": "alice@example.com"}}),  # fmt: skip
            Step("PUT", "/api/settings/", user="alice", json={"bio": "Hello there."},
                 expect_body={"code": 200, "data": {"bio": "Hello there."}}),  # fmt: skip
            Step("PUT", "/api/settings/", user="alice", json={"email": "new@example.com"},
                 expect_body={"code": 400, "error": {"email": ["E-mail can't be changed."]}}),  # fmt: skip
        ),
    ),
    Scenario(
        "login needs the e-mail and password of an active user",
        steps=(
            Step("POST", "/api/token/", json={"email": "alice@example.com", "password": "alicepass1"},
                 expect_body={"msg": "Login succeed.",
                              "user": {"_id": 2, "username": "alice"}}),  # fmt: skip
            Step("POST", "/api/token/", json={"email": "alice@example.com", "password": "wrong"},
                 expect_status=401,
                 expect_body={"detail": "No active account found with the given credentials"}),  # fmt: skip
            Step("POST", "/api/token/", json={"email": "carol@example.com", "password": "carolpass1"},
                 expect_status=401),  # fmt: skip
            Step("POST", "/api/token/", json={"email": "alice@example.com"}, expect_status=400),
            Step("POST", "/api/token/refresh/", json={"refresh": "not-a-token"}, expect_status=401),
        ),
    ),
)
