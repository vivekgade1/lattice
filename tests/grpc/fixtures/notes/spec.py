"""The dev spec of the notes app: 17 scenarios of ``Rpc`` steps, two invariants, the note
state machine. Each of the eight ``BUG`` variants of the app breaks exactly one scenario
(``BUG_SCENARIO``)."""

from __future__ import annotations

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Invariant, Machine, Rpc, Scenario, Spec
from lattice.typesys import Scalar, Schema
from tests.grpc.fixtures.notes.harness import PROTOS, USERS

S = Scalar
ACC, NOTES = "notes.v1.Accounts", "notes.v1.Notes"

USER = Schema.from_pairs(
    [("id", S.INT), ("name", S.STR), ("password", S.STR), ("token", S.STR)],
    keys=[("id",), ("name",), ("token",)],
    private=["password", "token"],
)
NOTE = Schema.from_pairs(
    [
        ("id", S.INT),
        ("user_id", S.INT),
        ("title", S.STR),
        ("body", S.STR),
        ("state", S.STR),
        ("priority", S.INT),
        ("client_rev", S.INT),
    ],
    keys=[("id",)],
    nullable=["body", "priority", "client_rev"],
    owner="user_id",
    one_of={"state": ("draft", "published", "archived")},
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Note": NOTE},
        table_names={"User": "users", "Note": "notes"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("name"),
        error_body="fastapi",
    ),
)
AT_MOST_THREE = Invariant(
    "three_published_at_most",
    "A user has at most three published notes.",
    'count(Note, by="user_id", where=Note.state == "published") <= 3',
)
PRIORITY_RANGE = Invariant(
    "priority_in_range", "A note's priority is 0 to 5.", "Note.priority >= 0 and Note.priority <= 5"
)


def create(user: str, title: str = "t", **extra) -> Rpc:
    return Rpc(NOTES, "CreateNote", user, {"title": title, **extra})


def note(**fields) -> dict:
    return {"note": fields}


SCENARIOS = (
    Scenario(
        "register_and_login",
        steps=(
            Rpc(
                ACC,
                "Register",
                None,
                {"name": "cat", "password": "secret1"},
                expect_message={"id": 3},
                expect_db={"User": {"count": 3}},
            ),
            Rpc(
                ACC,
                "Login",
                None,
                {"name": "cat", "password": "secret1"},
                expect_message={"token": "tok-cat"},
            ),
            Rpc(ACC, "Me", "ann", expect_message={"id": 1, "name": "ann"}),
        ),
    ),
    Scenario(
        "register_validation",
        steps=(
            Rpc(
                ACC,
                "Register",
                None,
                {"name": "ab", "password": "secret1"},
                expect_code="INVALID_ARGUMENT",
                expect_db={"User": {"count": 2}},
            ),
            Rpc(
                ACC,
                "Register",
                None,
                {"name": "dave", "password": "123"},
                expect_code="INVALID_ARGUMENT",
                expect_db={"User": {"count": 2}},
            ),
        ),
    ),
    Scenario(
        "register_duplicate",
        steps=(
            Rpc(
                ACC,
                "Register",
                None,
                {"name": "ann", "password": "secret1"},
                expect_code="ALREADY_EXISTS",
                expect_db={"User": {"count": 2}},
            ),
        ),
    ),
    Scenario(
        "login_refused",
        steps=(
            Rpc(
                ACC,
                "Login",
                None,
                {"name": "ann", "password": "wrong"},
                expect_code="UNAUTHENTICATED",
            ),
            Rpc(ACC, "Login", None, {"name": "", "password": "x"}, expect_code="INVALID_ARGUMENT"),
        ),
    ),
    Scenario(
        "callers_without_a_token",
        steps=(
            Rpc(ACC, "Me", None, expect_code="UNAUTHENTICATED"),
            Rpc(NOTES, "ListNotes", None, expect_code="UNAUTHENTICATED"),
            Rpc(NOTES, "GetNote", None, {"id": 1}, expect_code="UNAUTHENTICATED"),
            Rpc(
                NOTES,
                "CreateNote",
                None,
                {"title": "x"},
                expect_code="UNAUTHENTICATED",
                expect_db={"Note": {"count": 0}},
            ),
        ),
    ),
    Scenario(
        "create_and_get",
        steps=(
            create("ann", "first", body="b", priority=2),
            Rpc(
                NOTES,
                "GetNote",
                "ann",
                {"id": 1},
                expect_message=note(id=1, title="first", state="draft", visibility="PRIVATE"),
            ),
            Rpc(NOTES, "GetNote", "ann", {"id": 99}, expect_code="NOT_FOUND"),
        ),
    ),
    Scenario(
        "create_validation",
        steps=(
            Rpc(
                NOTES,
                "CreateNote",
                "ann",
                {"title": ""},
                expect_code="INVALID_ARGUMENT",
                expect_db={"Note": {"count": 0}},
            ),
            Rpc(
                NOTES,
                "CreateNote",
                "ann",
                {"title": "x", "priority": 9},
                expect_code="INVALID_ARGUMENT",
                expect_db={"Note": {"count": 0}},
            ),
            Rpc(
                NOTES,
                "CreateNote",
                "ann",
                {"title": "y" * 81},
                expect_code="INVALID_ARGUMENT",
                expect_db={"Note": {"count": 0}},
            ),
        ),
    ),
    Scenario(
        "other_user_cannot_read",
        steps=(
            create("ann", "private"),
            Rpc(NOTES, "GetNote", "ben", {"id": 1}, expect_code="NOT_FOUND"),
            Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_message=note(title="private")),
        ),
    ),
    Scenario(
        "list_is_scoped",
        steps=(
            create("ann", "a1"),
            create("ann", "a2"),
            create("ben", "b1"),
            Rpc(NOTES, "ListNotes", "ben", {}, expect_message={"notes": [{"title": "b1"}]}),
            Rpc(NOTES, "ListNotes", "ann", {"state": "published"}, expect_message={"notes": []}),
        ),
    ),
    Scenario(
        "update_note",
        steps=(
            create("ann", "old"),
            Rpc(
                NOTES,
                "UpdateNote",
                "ann",
                {
                    "id": 1,
                    "title": "new",
                    "body": "b",
                    "priority": 3,
                    "client_rev": 9007199254740993,
                },
                expect_message=note(title="new", priority=3, client_rev="9007199254740993"),
                expect_db={"Note": {"where": {"id": 1}, "equals": {"title": "new"}}},
            ),
            Rpc(NOTES, "UpdateNote", "ann", {"id": 1, "title": ""}, expect_code="INVALID_ARGUMENT"),
        ),
    ),
    Scenario(
        "publish_flow",
        steps=(
            create("ann", "p"),
            Rpc(
                NOTES,
                "PublishNote",
                "ann",
                {"id": 1},
                expect_message=note(state="published", visibility="SHARED"),
                expect_db={"Note": {"where": {"id": 1}, "equals": {"state": "published"}}},
            ),
            Rpc(NOTES, "PublishNote", "ann", {"id": 1}, expect_code="FAILED_PRECONDITION"),
        ),
    ),
    Scenario(
        "publish_limit",
        steps=(
            create("ann", "n1"),
            create("ann", "n2"),
            create("ann", "n3"),
            create("ann", "n4"),
            Rpc(NOTES, "PublishNote", "ann", {"id": 1}),
            Rpc(NOTES, "PublishNote", "ann", {"id": 2}),
            Rpc(NOTES, "PublishNote", "ann", {"id": 3}),
            Rpc(
                NOTES,
                "PublishNote",
                "ann",
                {"id": 4},
                expect_code="RESOURCE_EXHAUSTED",
                expect_db={"Note": {"where": {"id": 4}, "equals": {"state": "draft"}}},
            ),
        ),
    ),
    Scenario(
        "archive_flow",
        steps=(
            create("ann", "x"),
            Rpc(
                NOTES,
                "ArchiveNote",
                "ann",
                {"id": 1},
                expect_message=note(state="archived"),
                expect_db={"Note": {"where": {"id": 1}, "equals": {"state": "archived"}}},
            ),
            Rpc(NOTES, "ArchiveNote", "ann", {"id": 1}, expect_code="FAILED_PRECONDITION"),
        ),
    ),
    Scenario(
        "update_archived_note",
        steps=(
            create("ann", "x"),
            Rpc(NOTES, "ArchiveNote", "ann", {"id": 1}),
            Rpc(
                NOTES,
                "UpdateNote",
                "ann",
                {"id": 1, "title": "again"},
                expect_code="FAILED_PRECONDITION",
            ),
        ),
    ),
    Scenario(
        "delete_rules",
        steps=(
            create("ann", "keep"),
            create("ann", "drop"),
            Rpc(NOTES, "PublishNote", "ann", {"id": 1}),
            Rpc(
                NOTES,
                "DeleteNote",
                "ann",
                {"id": 1},
                expect_code="UNKNOWN",
                expect_db={"Note": {"count": 2}},
            ),
            Rpc(NOTES, "DeleteNote", "ann", {"id": 2}, expect_db={"Note": {"count": 1}}),
            Rpc(NOTES, "GetNote", "ann", {"id": 2}, expect_code="NOT_FOUND"),
        ),
    ),
    Scenario(
        "share_is_not_served",
        steps=(
            create("ann", "s"),
            Rpc(
                NOTES,
                "ShareNote",
                "ann",
                {"id": 1, "with_user": "ben"},
                expect_code="UNIMPLEMENTED",
            ),
        ),
    ),
    Scenario(
        "int64_is_exact",
        steps=(
            create("ann", "big", client_rev=9007199254740991),
            create("ann", "bigger", client_rev=9007199254740993),
            Rpc(
                NOTES,
                "GetNote",
                "ann",
                {"id": 2},
                expect_message=note(client_rev="9007199254740993"),
            ),
        ),
    ),
)

SPEC = Spec(
    profile=PROFILE,
    invariants=(AT_MOST_THREE, PRIORITY_RANGE),
    machines=(
        Machine(
            "Note",
            "state",
            {"draft": ("published", "archived"), "published": ("archived",)},
            initial=("draft",),
        ),
    ),
    scenarios=SCENARIOS,
    users=USERS,
    protos=PROTOS,
)

# the scenario each deliberate fault of the app breaks (and no other)
BUG_SCENARIO = {
    "no_owner": "other_user_cannot_read",
    "publish_any_state": "publish_flow",
    "delete_published": "delete_rules",
    "list_all": "list_is_scoped",
    "dup_code": "register_duplicate",
    "no_priority_check": "create_validation",
    "update_archived": "update_archived_note",
    "publish_limit_off": "publish_limit",
}
