"""Fidelity scenarios for the vendored DRF tutorial (``bench/import_repos/snippets``).

Written from the upstream tutorial text (parts 1, 4, 5, 6: the sample requests and the
behaviour each part describes), then recorded against the running app on ``seed.py``'s
data: every expected status and body fragment below is what the app answers. Users are
``seed.SEED_USERS`` handles; ids are ``seed.SNIPPET_IDS`` / ``seed.USER_IDS``.
``expect_body`` is a subset match (``lattice.spec.check.subset``).

Not used by the importer; ``tests/bench/test_phase2_apps.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

AUTH_MISSING = {"detail": "Authentication credentials were not provided."}
NOT_ALLOWED = {"detail": "You do not have permission to perform this action."}
NOT_FOUND = {"detail": "No Snippet matches the given query."}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "api root links the two collections",
        steps=(
            Step(
                "GET",
                "/",
                expect_body={
                    "snippets": "http://testserver/snippets/",
                    "users": "http://testserver/users/",
                },
            ),
        ),
    ),
    Scenario(
        "anyone can list snippets (paginated, 10 a page)",
        steps=(
            Step("GET", "/snippets/", expect_body={"count": 5, "next": None, "previous": None}),
            Step(
                "GET",
                "/snippets/",
                params={"page": 2},
                expect_status=404,
                expect_body={"detail": "Invalid page."},
            ),
        ),
    ),
    Scenario(
        "anyone can read one snippet, with its owner's username",
        steps=(
            Step(
                "GET",
                "/snippets/1/",
                expect_body={
                    "id": 1,
                    "owner": "alice",
                    "title": "hello",
                    "language": "python",
                    "highlight": "http://testserver/snippets/1/highlight/",
                },
            ),
            Step("GET", "/snippets/99/", expect_status=404, expect_body=NOT_FOUND),
        ),
    ),
    Scenario(
        "the highlight view renders HTML for anyone",
        steps=(Step("GET", "/snippets/3/highlight/"),),
    ),
    Scenario(
        "creating a snippet needs a login",
        steps=(
            Step(
                "POST",
                "/snippets/",
                json={"code": "print(123)"},
                expect_status=403,
                expect_body=AUTH_MISSING,
            ),
            Step("GET", "/snippets/", expect_body={"count": 5}),
        ),
    ),
    Scenario(
        "a created snippet is owned by the caller, whatever the body says",
        steps=(
            Step(
                "POST",
                "/snippets/",
                user="alice",
                json={"code": "print(789)", "title": "foo", "owner": "bob"},
                expect_status=201,
                expect_body={
                    "id": 6,
                    "owner": "alice",
                    "title": "foo",
                    "code": "print(789)",
                    "linenos": False,
                    "language": "python",
                    "style": "friendly",
                },
            ),
            Step(
                "GET",
                "/users/2/",
                expect_body={
                    "username": "alice",
                    "snippets": [
                        "http://testserver/snippets/1/",
                        "http://testserver/snippets/2/",
                        "http://testserver/snippets/6/",
                    ],
                },
            ),
        ),
    ),
    Scenario(
        "the owner can update and partially update a snippet",
        steps=(
            Step(
                "PUT",
                "/snippets/1/",
                user="alice",
                json={"code": "print(1)\n", "title": "one"},
                # the serializer's CharField trims surrounding whitespace
                expect_body={"title": "one", "code": "print(1)", "owner": "alice"},
            ),
            Step(
                "PATCH",
                "/snippets/2/",
                user="alice",
                json={"linenos": False},
                expect_body={"id": 2, "linenos": False, "language": "sql"},
            ),
        ),
    ),
    Scenario(
        "another user cannot edit or delete a snippet",
        steps=(
            Step(
                "PUT",
                "/snippets/1/",
                user="bob",
                json={"code": "x"},
                expect_status=403,
                expect_body=NOT_ALLOWED,
            ),
            Step(
                "PATCH",
                "/snippets/1/",
                user="bob",
                json={"title": "x"},
                expect_status=403,
                expect_body=NOT_ALLOWED,
            ),
            Step("DELETE", "/snippets/1/", user="bob", expect_status=403, expect_body=NOT_ALLOWED),
            Step("GET", "/snippets/1/", expect_body={"title": "hello", "owner": "alice"}),
        ),
    ),
    Scenario(
        "the superuser gets no exemption from IsOwnerOrReadOnly",
        steps=(
            Step(
                "DELETE", "/snippets/3/", user="admin", expect_status=403, expect_body=NOT_ALLOWED
            ),
            Step(
                "PATCH",
                "/snippets/5/",
                user="admin",
                json={"title": "mine"},
                expect_body={"title": "mine", "owner": "admin"},
            ),
        ),
    ),
    Scenario(
        "the owner can delete a snippet",
        steps=(
            Step("DELETE", "/snippets/3/", user="bob", expect_status=204),
            Step("GET", "/snippets/3/", expect_status=404, expect_body=NOT_FOUND),
            Step("GET", "/snippets/", expect_body={"count": 4}),
        ),
    ),
    Scenario(
        "language and style must be known to pygments",
        steps=(
            Step(
                "POST",
                "/snippets/",
                user="bob",
                json={"code": "x", "language": "klingon"},
                expect_status=400,
                expect_body={"language": ['"klingon" is not a valid choice.']},
            ),
            Step(
                "POST",
                "/snippets/",
                user="bob",
                json={"code": "x", "style": "loud"},
                expect_status=400,
                expect_body={"style": ['"loud" is not a valid choice.']},
            ),
            Step(
                "POST",
                "/snippets/",
                user="bob",
                json={"code": "x = 1", "language": "rust", "style": "monokai"},
                expect_status=201,
                expect_body={"language": "rust", "style": "monokai", "owner": "bob"},
            ),
        ),
    ),
    Scenario(
        "code is required and the title is at most 100 characters",
        steps=(
            Step(
                "POST",
                "/snippets/",
                user="bob",
                json={"title": "no code"},
                expect_status=400,
                expect_body={"code": ["This field is required."]},
            ),
            Step(
                "POST",
                "/snippets/",
                user="bob",
                json={"code": "x", "title": "t" * 101},
                expect_status=400,
                expect_body={"title": ["Ensure this field has no more than 100 characters."]},
            ),
            Step(
                "POST",
                "/snippets/",
                user="bob",
                json={"code": "x", "title": "t" * 100},
                expect_status=201,
            ),
        ),
    ),
    Scenario(
        "an inactive user cannot authenticate",
        steps=(
            Step(
                "POST",
                "/snippets/",
                user="carol",
                json={"code": "x"},
                expect_status=403,
                expect_body={"detail": "Invalid username/password."},
            ),
        ),
    ),
    Scenario(
        "users are read-only and public",
        steps=(
            Step("GET", "/users/", expect_body={"count": 4}),
            Step(
                "GET",
                "/users/3/",
                expect_body={
                    "id": 3,
                    "username": "bob",
                    "snippets": ["http://testserver/snippets/3/", "http://testserver/snippets/4/"],
                },
            ),
            Step("GET", "/users/4/", expect_body={"username": "carol", "snippets": []}),
            Step("POST", "/users/", user="admin", json={"username": "x"}, expect_status=405),
        ),
    ),
    Scenario(
        "the list pages at 10 and the highlight of a missing snippet is 404",
        steps=(
            *(
                Step(
                    "POST", "/snippets/", user="alice", json={"code": f"n = {i}"}, expect_status=201
                )
                for i in range(6)
            ),
            Step(
                "GET",
                "/snippets/",
                expect_body={"count": 11, "next": "http://testserver/snippets/?page=2"},
            ),
            Step(
                "GET",
                "/snippets/",
                params={"page": 2},
                expect_body={"count": 11, "results": [{"id": 11, "owner": "alice"}]},
            ),
            Step("GET", "/snippets/42/highlight/", expect_status=404),
        ),
    ),
)
