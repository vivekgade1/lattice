"""Spec of a toy notes app, R1..R4: the project the dogfood check of Lattice Studio serves
(dogfood/studio/harness.py copies it into a fresh git repository per scenario)."""

from lattice.profile import ApiProfile, NoRoles, Profile
from lattice.spec import Scenario, Spec, Step
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

# fmt: off
SPEC = Spec(
    profile=PROFILE,
    users=USERS,
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
        )),
        Scenario("read_own_and_other", given=ANN_NOTE, steps=(
            Step("GET", "/notes/10", "ann", expect_status=200, expect_body={"text": "milk"}),
            Step("GET", "/notes/10", "ben", expect_status=404),
        )),
        Scenario("no_token", given=ANN_NOTE, steps=(
            Step("POST", "/notes", None, json={"text": "x"}, expect_status=401,
                 expect_db={"notes": {"count": 1}}),
            Step("GET", "/notes/10", None, expect_status=401),
        )),
    ),
)
# fmt: on
