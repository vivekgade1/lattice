"""Shared API-profile contracts: Schema.owner/private, ApiProfile, Profile.api."""

import pytest

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

S = Scalar


def _orders(**kw) -> Schema:
    return Schema.from_pairs(
        [("id", S.INT), ("customer_id", S.INT), ("status", S.STR)], keys=[("id",)], **kw
    )


def test_owner_and_private_are_metadata_not_equality() -> None:
    a = _orders(owner="customer_id", private=["status"])
    b = _orders()
    assert a.owner == "customer_id" and a.private == frozenset({"status"})
    assert a == b


def test_owner_and_private_must_name_columns() -> None:
    with pytest.raises(ValueError):
        _orders(owner="nope")
    with pytest.raises(ValueError):
        _orders(private=["nope"])


def test_api_profile_attaches_to_profile() -> None:
    api = ApiProfile(
        tables={"Orders": _orders(owner="customer_id")},
        table_names={"Orders": "orders"},
        roles=frozenset({"Public", "Customer", "Admin"}),
    )
    p = Profile(schemas={"Orders": _orders(owner="customer_id")}, api=api)
    assert p.api is api and api.admin_role == "Admin" and api.users_table == "Users"
    assert Profile(schemas={}).api is None
