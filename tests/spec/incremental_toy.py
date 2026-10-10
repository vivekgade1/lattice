"""A toy notes app's requirements, v1 and v2, and a hand-written v1 spec + trace: the base
of the incremental-drafting tests (tests/spec/test_incremental.py). v1 -> v2: R4 changed
(at most 3 notes -> at most 5), an edit rule added, the delete rule removed."""

NOTES_V1 = """\
# Notes app

- A signed-in user creates a note with POST /notes {"text": ...}; it answers 201 with the note.
- A note's text is 1 to 200 characters; anything else answers 422.
- GET /notes/{id} answers the caller's own note; another user's note answers 404.
- A user has at most 3 notes; a fourth answers 409.
- Requests without a token answer 401.
- DELETE /notes/{id} deletes the caller's own note; it answers 204.
"""

NOTES_V2 = """\
# Notes app

- A signed-in user creates a note with POST /notes {"text": ...}; it answers 201 with the note.
- A note's text is 1 to 200 characters; anything else answers 422.
- GET /notes/{id} answers the caller's own note; another user's note answers 404.
- A user has at most 5 notes; a sixth answers 409.
- Requests without a token answer 401.
- PATCH /notes/{id} {"text": ...} edits the caller's own note; it answers 200 with the note.
"""

NOTES_CONTEXT = """\
Stack: FastAPI + SQLite, errors as {"detail": ...}, bearer tokens stored in users.token.
Tables (SQL names): users(id INTEGER, email TEXT, password TEXT, token TEXT) and
notes(id INTEGER, user_id INTEGER -> users.id, text TEXT NOT NULL). No roles: every user
is a "Member" (NoRoles("Member"); roles Public and Member).
Seed: users ann (id 1, login "ann@example.com", password "pw-ann") and ben (id 2,
"ben@example.com", "pw-ben"); no notes. No background services.
"""

NOTES_BASE_MODULE = '''\
"""Spec of the notes app (v1), R1..R6."""

from lattice.profile import ApiProfile, NoRoles, Profile
from lattice.spec import Invariant, Scenario, Spec, Step
from lattice.typesys import Scalar, Schema

S = Scalar
USER = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("password", S.STR), ("token", S.STR)],
    keys=[("id",), ("email",), ("token",)],
    private=["password", "token"],
)
NOTE = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("text", S.STR)], keys=[("id",)], owner="user_id"
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Note": NOTE},
        table_names={"User": "users", "Note": "notes"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=NoRoles("Member"),
        error_body="fastapi",
    ),
)
USERS = {"ann": ("ann@example.com", "pw-ann"), "ben": ("ben@example.com", "pw-ben")}
ANN_NOTE = {"notes": [{"id": 10, "user_id": 1, "text": "milk"}]}
THREE = {"notes": [{"id": 10 + i, "user_id": 1, "text": f"n{i}"} for i in range(3)]}

SPEC = Spec(
    profile=PROFILE,
    users=USERS,
    invariants=(
        Invariant("notes_at_most_3", "A user has at most 3 notes.",
                  'count(Note, by="user_id") <= 3'),
    ),
    scenarios=(
        Scenario("create_a_note", steps=(
            Step("POST", "/notes", "ann", json={"text": "tea"}, expect_status=201,
                 expect_body={"text": "tea"},
                 expect_db={"notes": {"where": {"text": "tea"}, "equals": {"user_id": 1}}}),
        )),
        Scenario("text_bounds", steps=(
            Step("POST", "/notes", "ann", json={"text": ""}, expect_status=422,
                 expect_db={"notes": {"count": 0}}),
            Step("POST", "/notes", "ann", json={"text": "x" * 201}, expect_status=422,
                 expect_db={"notes": {"count": 0}}),
            Step("POST", "/notes", "ann", json={"text": "y" * 200}, expect_status=201,
                 expect_db={"notes": {"count": 1}}),
        )),
        Scenario("read_own_and_other", given=ANN_NOTE, steps=(
            Step("GET", "/notes/10", "ann", expect_status=200, expect_body={"text": "milk"}),
            Step("GET", "/notes/10", "ben", expect_status=404),
        )),
        Scenario("the_fourth_note_is_refused", given=THREE, steps=(
            Step("POST", "/notes", "ann", json={"text": "one more"}, expect_status=409,
                 expect_db={"notes": {"where": {"user_id": 1}, "count": 3}}),
        )),
        Scenario("no_token", given=ANN_NOTE, steps=(
            Step("POST", "/notes", None, json={"text": "x"}, expect_status=401,
                 expect_db={"notes": {"count": 1}}),
            Step("GET", "/notes/10", None, expect_status=401),
        )),
        Scenario("delete_own_note", given=ANN_NOTE, steps=(
            Step("DELETE", "/notes/10", "ann", expect_status=204,
                 expect_db={"notes": {"count": 0}}),
        )),
    ),
)
'''

NOTES_BASE_TRACE = {
    "links": {
        "R1": ["scenario:create_a_note"],
        "R2": ["scenario:text_bounds"],
        "R3": ["scenario:read_own_and_other"],
        "R4": ["scenario:the_fourth_note_is_refused", "invariant:notes_at_most_3"],
        "R5": ["scenario:no_token"],
        "R6": ["scenario:delete_own_note"],
    },
    "unchecked": {},
    "context": [],
    "questions": [],
}
