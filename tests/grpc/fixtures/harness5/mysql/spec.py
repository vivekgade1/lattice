"""A gRPC spec of the ledger5 MySQL stand-in app (A.6): two scenarios of ``Rpc`` steps."""

from harness import USERS

from lattice.profile import ApiProfile, Profile
from lattice.spec import Rpc, Scenario, Spec

E = "ledger.v1.Entries"
PROFILE = Profile(
    schemas={},
    api=ApiProfile(tables={}, table_names={}, roles=frozenset({"Public"}), error_body="fastapi"),
)
SPEC = Spec(
    profile=PROFILE,
    users=USERS,
    scenarios=(
        Scenario(
            "create_then_get",
            steps=(
                Rpc(
                    E,
                    "Create",
                    "ann",
                    {"title": "x"},
                    expect_message={"title": "x", "state": "OPEN"},
                ),
                Rpc(E, "Get", "ann", {"id": 2}, expect_message={"title": "x"}),
            ),
        ),
        Scenario(
            "empty_title",
            steps=(Rpc(E, "Create", "ann", {"title": ""}, expect_code="INVALID_ARGUMENT"),),
        ),
    ),
)
