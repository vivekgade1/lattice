"""Fidelity scenarios for the vendored drf_blog_api (``bench/import_repos/drf_blog``).

Written from the upstream tests (``src/{account,blog,comment}/tests/test_views.py``; not
vendored) and the README, then recorded against the running app on ``seed.py``'s data:
every expected status and body fragment below is what the app answers. Users are
``seed.SEED_USERS`` handles (logged in through the OTP routes, ``seed.login_headers``).
Ids and slugs follow ``seed.py``. ``expect_body`` is a subset match
(``lattice.spec.check.subset``; a list must match element-wise with the same length).

The login and register routes are throttled (5 an hour per client IP; verify 8), and every
logged-in user of a scenario costs one login and one verify, so a scenario logs in at most
three users and sends few login steps of its own.

Not used by the importer; ``bench/heldout/test_heldout_apps.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

NO_CREDENTIALS = {"detail": "Authentication credentials were not provided."}
NOT_FOUND = {"detail": "No Blog matches the given query."}
BLOG = {"title": "Updated title", "body": "Updated body.", "summary": "Updated.", "status": "p"}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "anyone lists the published blogs only, newest first",
        steps=(
            Step(
                "GET",
                "/blog/api/",
                expect_body=[
                    {"title": "Archived news", "slug": "archived-news"},
                    {
                        "title": "Bob on Python",
                        "author": {"first_name": "Bob", "last_name": "Baker"},
                        "category": ["Python", "Technology"],
                    },
                    {"title": "Alice on Django", "category": ["Technology"]},
                ],
            ),
            Step("GET", "/blog/api/", user="role_user", expect_body=[{}, {}, {}]),
        ),
    ),
    Scenario(
        "blogs are searchable and filterable",
        steps=(
            Step("GET", "/blog/api/", params={"search": "Python"},
                 expect_body=[{"title": "Bob on Python"}]),  # fmt: skip
            Step("GET", "/blog/api/", params={"search": "nothing-like-this"}, expect_body=[]),
            Step("GET", "/blog/api/", params={"special": "true"},
                 expect_body=[{"title": "Bob on Python"}]),  # fmt: skip
        ),
    ),
    Scenario(
        "a blog's detail is found by slug; drafts and unknown slugs are 404",
        steps=(
            Step(
                "GET",
                "/blog/api/alice-on-django/",
                expect_body={"title": "Alice on Django", "likes": 1, "status": "p"},
            ),
            Step("GET", "/blog/api/alice-draft-notes/", expect_status=404, expect_body=NOT_FOUND),
            Step("GET", "/blog/api/test/", expect_status=404, expect_body=NOT_FOUND),
        ),
    ),
    Scenario(
        "the users list is for the superuser only",
        steps=(
            Step("GET", "/account/api/", expect_status=403, expect_body=NO_CREDENTIALS),
            Step("GET", "/account/api/", user="role_user", expect_status=403,
                 expect_body={"detail": "You Must Be SuperUser"}),  # fmt: skip
            Step("GET", "/account/api/", user="alice", expect_status=403),
            Step(
                "GET",
                "/account/api/",
                user="admin",
                params={"ordering": "id"},
                expect_body=[
                    {"id": 1, "phone": "989100000001", "author": False},
                    {"id": 2, "phone": "989100000002", "author": True},
                    {"id": 3, "phone": "989100000003", "author": True},
                    {"id": 4, "phone": "989100000004", "author": False},
                ],
            ),
        ),
    ),
    Scenario(
        "the superuser searches and filters users",
        steps=(
            Step("GET", "/account/api/", user="admin", params={"author": "true", "ordering": "id"},
                 expect_body=[{"first_name": "Alice"}, {"first_name": "Bob"}]),  # fmt: skip
            Step("GET", "/account/api/", user="admin", params={"search": "Uma"},
                 expect_body=[{"phone": "989100000004"}]),  # fmt: skip
            Step("GET", "/account/api/", user="admin", params={"search": "wrong search"},
                 expect_body=[]),  # fmt: skip
        ),
    ),
    Scenario(
        "a user's detail is for the superuser; an unknown id is 404",
        steps=(
            Step("GET", "/account/api/users/2/", user="admin",
                 expect_body={"id": 2, "phone": "989100000002", "author": True}),  # fmt: skip
            Step("GET", "/account/api/users/99/", user="admin", expect_status=404),
            Step("GET", "/account/api/users/2/", user="bob", expect_status=403),
        ),
    ),
    Scenario(
        "the superuser updates and deletes a user",
        steps=(
            Step("PATCH", "/account/api/users/4/", user="admin", json={"author": True},
                 expect_body={"id": 4, "author": True}),  # fmt: skip
            Step("DELETE", "/account/api/users/3/", user="admin", expect_status=204),
            Step("GET", "/account/api/users/3/", user="admin", expect_status=404),
        ),
    ),
    Scenario(
        "the profile needs a login and returns the caller",
        steps=(
            Step("GET", "/account/api/profile/", expect_status=403, expect_body=NO_CREDENTIALS),
            Step(
                "GET",
                "/account/api/profile/",
                user="role_user",
                expect_body={"id": 4, "phone": "989100000004", "first_name": "Uma",
                             "two_step_password": False},  # fmt: skip
            ),
        ),
    ),
    Scenario(
        "the caller updates their profile; the phone is read-only",
        steps=(
            Step(
                "PUT",
                "/account/api/profile/",
                user="alice",
                json={"first_name": "Alicia", "last_name": "Arrow", "phone": "989100000099"},
                expect_body={"phone": "989100000002", "first_name": "Alicia",
                             "last_name": "Arrow"},  # fmt: skip
            ),
        ),
    ),
    Scenario(
        "login needs a valid, registered phone",
        steps=(
            Step("POST", "/account/api/login/", json={"phone": "450404146044"}, expect_status=400),
            Step(
                "POST",
                "/account/api/login/",
                json={"phone": "989101115878"},
                expect_status=401,
                expect_body={"No User exists.": "Please enter another phone number."},
            ),
            Step("POST", "/account/api/login/", json={}, expect_status=400),
        ),
    ),
    Scenario(
        "register needs a valid, new phone",
        steps=(
            Step("POST", "/account/api/register/", json={"phone": "98910111"}, expect_status=400),
            Step(
                "POST",
                "/account/api/register/",
                json={"phone": "989100000002"},
                expect_status=401,
                expect_body={"User exists.": "Please enter a different phone number."},
            ),
            Step("POST", "/account/api/register/", json={"phone": "989101115878"}),
        ),
    ),
    Scenario(
        "an OTP must be six digits, current and correct",
        steps=(
            Step("POST", "/account/api/verify/", json={"code": "12a456"}, expect_status=400,
                 expect_body={"code": ["Invalid Code."]}),  # fmt: skip
            Step(
                "POST",
                "/account/api/verify/",
                json={"code": "123456"},
                expect_status=408,
                expect_body={"Code expired.": "The entered code has expired."},
            ),
            Step("POST", "/account/api/login/", json={"phone": "989100000003"}),
            Step(
                "POST",
                "/account/api/verify/",
                json={"code": "1234567"},
                expect_status=400,
            ),
        ),
    ),
    Scenario(
        "authors create draft blogs; the superuser chooses; plain users are refused",
        steps=(
            Step("POST", "/blog/api/create/", json={**BLOG, "category": [1]}, expect_status=403),
            Step(
                "POST",
                "/blog/api/create/",
                user="role_user",
                json={**BLOG, "category": [1]},
                expect_status=403,
                expect_body={"detail": "You Must Be SuperUser or Author"},
            ),
            Step(
                "POST",
                "/blog/api/create/",
                user="alice",
                json={**BLOG, "category": [1], "special": True},
                expect_status=201,
                expect_body={"title": "Updated title", "status": "d", "special": False,
                             "category": [1]},  # fmt: skip
            ),
            Step(
                "POST",
                "/blog/api/create/",
                user="admin",
                json={**BLOG, "category": [2], "special": True},
                expect_status=201,
                expect_body={"status": "p", "special": True},
            ),
            Step("POST", "/blog/api/create/", user="alice", json={"title": "No body"},
                 expect_status=400),  # fmt: skip
        ),
    ),
    Scenario(
        "only a blog's author or the superuser updates it; an author's update makes it a draft",
        steps=(
            Step("PUT", "/blog/api/alice-on-django/", json=BLOG, expect_status=403),
            Step("PUT", "/blog/api/alice-on-django/", user="bob", json=BLOG, expect_status=403,
                 expect_body={"detail": "You do not have permission to perform this action."}),  # fmt: skip
            Step("PUT", "/blog/api/alice-on-django/", user="alice", json=BLOG,
                 expect_body={"title": "Updated title", "status": "d"}),  # fmt: skip
            Step("GET", "/blog/api/alice-on-django/", expect_status=404),
            Step("PUT", "/blog/api/bob-on-python/", user="admin", json=BLOG,
                 expect_body={"title": "Updated title", "status": "p"}),  # fmt: skip
        ),
    ),
    Scenario(
        "only a blog's author or the superuser deletes it",
        steps=(
            Step("DELETE", "/blog/api/bob-on-python/", user="alice", expect_status=403),
            Step("DELETE", "/blog/api/bob-on-python/", user="role_user", expect_status=403),
            Step("DELETE", "/blog/api/bob-on-python/", user="bob", expect_status=204),
            Step("GET", "/blog/api/bob-on-python/", expect_status=404),
        ),
    ),
    Scenario(
        "a logged-in user likes and unlikes a published blog",
        steps=(
            Step("GET", "/blog/api/like/1/", expect_status=403),
            Step("GET", "/blog/api/like/1/", user="bob",
                 expect_body={"ok": "Your request was successful."}),  # fmt: skip
            Step("GET", "/blog/api/alice-on-django/", expect_body={"likes": 2}),
            Step("GET", "/blog/api/like/1/", user="bob"),
            Step("GET", "/blog/api/alice-on-django/", expect_body={"likes": 1}),
            Step("GET", "/blog/api/like/2/", user="bob", expect_status=404),
        ),
    ),
    Scenario(
        "active categories and their published blogs",
        steps=(
            Step(
                "GET",
                "/blog/api/category/list/",
                expect_body=[
                    {"title": "Python", "parent": {"title": "Technology"}},
                    {"title": "Lifestyle", "parent": {"title": "None"}},
                    {"title": "Technology", "parent": {"title": "None"}},
                ],
            ),
            Step("GET", "/blog/api/category/technology/",
                 expect_body=[{"title": "Bob on Python"}, {"title": "Alice on Django"}]),  # fmt: skip
            Step("GET", "/blog/api/category/lifestyle/", expect_body=[]),
            Step("GET", "/blog/api/category/archived/", expect_status=404),
        ),
    ),
    Scenario(
        "anyone lists a published blog's comments",
        steps=(
            Step(
                "GET",
                "/comment/api/1/",
                expect_body=[
                    {"user": {"name": "Uma"}, "body": "Agreed with Bob.", "parent": 1},
                    {"user": {"name": "Bob"}, "body": "Nice post, Alice.", "parent": None},
                ],
            ),
            Step("GET", "/comment/api/2/", expect_status=404),
            Step("GET", "/comment/api/99/", expect_status=404),
        ),
    ),
    Scenario(
        "a logged-in user comments on a published blog",
        steps=(
            Step("POST", "/comment/api/create/", json={"object_id": 3, "body": "Hi"},
                 expect_status=403),  # fmt: skip
            Step(
                "POST",
                "/comment/api/create/",
                user="role_user",
                json={"object_id": 3, "name": "uma", "body": "Great read."},
                expect_status=201,
                expect_body={"object_id": 3, "name": "uma", "parent": None, "body": "Great read."},
            ),
            Step("POST", "/comment/api/create/", user="role_user", json={"object_id": 3},
                 expect_status=400),  # fmt: skip
            Step("POST", "/comment/api/create/", user="role_user",
                 json={"object_id": 2, "body": "On a draft."}, expect_status=404),  # fmt: skip
            Step("GET", "/comment/api/3/", expect_body=[{"body": "Great read."},
                                                       {"body": "Thanks for the Python tips."}]),  # fmt: skip
        ),
    ),
    Scenario(
        "only a comment's author updates or deletes it",
        steps=(
            Step("PUT", "/comment/api/update-delete/1/", json={"object_id": 1, "body": "x"},
                 expect_status=403),  # fmt: skip
            Step("PUT", "/comment/api/update-delete/1/", user="alice",
                 json={"object_id": 1, "body": "Edited."}, expect_status=404),  # fmt: skip
            Step("PUT", "/comment/api/update-delete/1/", user="bob",
                 json={"object_id": 1, "name": "bob", "body": "Edited."},
                 expect_body={"object_id": 1, "body": "Edited."}),  # fmt: skip
            Step("PUT", "/comment/api/update-delete/1/", user="bob", json={"object_id": 1},
                 expect_status=400),  # fmt: skip
            Step("DELETE", "/comment/api/update-delete/1/", user="alice", expect_status=404),
            Step("DELETE", "/comment/api/update-delete/1/", user="bob", expect_status=204),
        ),
    ),
    Scenario(
        "a two-step password is created once, then changed with the old one",
        steps=(
            Step("POST", "/account/api/create-two-step-password/", user="role_user",
                 json={"password": "1234", "confirm_password": "1234"}, expect_status=401),  # fmt: skip
            Step("POST", "/account/api/create-two-step-password/", user="role_user",
                 json={"password": "Zebra-Quill-77", "confirm_password": "Zebra-Quill-78"},
                 expect_status=400),  # fmt: skip
            Step("POST", "/account/api/change-two-step-password/", user="role_user",
                 json={"old_password": "x", "password": "Zebra-Quill-77",
                       "confirm_password": "Zebra-Quill-77"}, expect_status=401),  # fmt: skip
            Step("POST", "/account/api/create-two-step-password/", user="role_user",
                 json={"password": "Zebra-Quill-77", "confirm_password": "Zebra-Quill-77"},
                 expect_body={"Successful.": "Your password was changed successfully."}),  # fmt: skip
            Step("POST", "/account/api/create-two-step-password/", user="role_user",
                 json={"password": "Zebra-Quill-77", "confirm_password": "Zebra-Quill-77"},
                 expect_status=401,
                 expect_body={"Error!": "Your request could not be approved."}),  # fmt: skip
            Step("POST", "/account/api/change-two-step-password/", user="role_user",
                 json={"old_password": "wrong-one", "password": "Otter-Maple-91",
                       "confirm_password": "Otter-Maple-91"}, expect_status=406),  # fmt: skip
            Step("POST", "/account/api/change-two-step-password/", user="role_user",
                 json={"old_password": "Zebra-Quill-77", "password": "Otter-Maple-91",
                       "confirm_password": "Otter-Maple-91"}),  # fmt: skip
            Step("GET", "/account/api/profile/", user="role_user",
                 expect_body={"two_step_password": True}),  # fmt: skip
        ),
    ),
    Scenario(
        "a user deletes their own account",
        steps=(
            Step("DELETE", "/account/api/delete-account/", expect_status=403),
            Step("DELETE", "/account/api/delete-account/", user="role_user", expect_status=204),
            Step("GET", "/account/api/", user="admin", expect_body=[{}, {}, {}]),
        ),
    ),
    Scenario(
        "simplejwt's verify and refresh routes reject bad tokens",
        steps=(
            Step("POST", "/api/token/verify/", json={"token": "not-a-token"}, expect_status=401),
            Step("POST", "/api/token/refresh/", json={"refresh": "not-a-token"}, expect_status=401),
            Step("POST", "/api/token/refresh/", json={}, expect_status=400),
        ),
    ),
)
