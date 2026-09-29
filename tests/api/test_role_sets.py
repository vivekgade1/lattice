"""Exact role sets (``auth=Roles(...)``, ``auth=Authenticated``) and stored role values
(``RoleColumn(values=...)``) -- spec docs/superpowers/specs/2026-09-29-lattice-role-sets-
design.md. The fixture is tests/api/rolesets.py; the generated FastAPI app is compared
with the runtime in test_codegen_differential."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.ir import lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import ApiProfile, NoRoles, Profile, RoleColumn
from tests.api import rolesets as F


def _errors(source: str, api: ApiProfile = F.API) -> list[str]:
    result = check_source(source, Profile(schemas={}, api=api))
    return [d.code for d in result.diagnostics if d.severity is Severity.ERROR]


def _endpoint(auth: str, user: bool = True) -> str:
    param = "user: CurrentUser" if user else ""
    body = (
        'shown = select(user, cols=["id"])' if user else 'shown = select(fetch(Item), cols=["id"])'
    )
    kind = "One" if user else "Page"
    ret = "    return shown" if user else "    result = page(shown, limit=10)\n    return result"
    return (
        f'# lattice: v1\n@endpoint("GET", "/x", auth={auth})\n'
        f"def x({param}) -> {kind}:\n    {body}\n{ret}\n"
    )


def test_the_fixture_checks() -> None:
    assert _errors(F.PROGRAM) == []


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    check = check_source(F.PROGRAM, F.PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    plans = lower_api(check.program)
    db = tmp_path / "app.db"
    F.build_db(db)
    with TestClient(build_app(plans, db)) as c:
        yield c


@pytest.mark.parametrize("path", sorted(F.ALLOWED))
def test_exactly_the_allowed_roles_may_call(client: TestClient, path: str) -> None:
    for name in F.USERS:
        r = client.get(path, headers={"Authorization": f"Bearer {F.TOKENS[name]}"})
        want = 200 if name in F.ALLOWED[path] else 403
        assert r.status_code == want, (path, name, r.text)
    assert client.get(path).status_code == 401


def test_stored_values_decode_before_is_admin(client: TestClient) -> None:
    def me(name: str) -> dict:
        return client.get("/me", headers={"Authorization": f"Bearer {F.TOKENS[name]}"}).json()

    assert me("ada")["admin"] is True
    assert me("ann")["admin"] is False
    assert me("odd")["admin"] is False


def test_owned_uses_the_decoded_admin_role(client: TestClient) -> None:
    def items(name: str) -> list[int]:
        r = client.get("/items", headers={"Authorization": f"Bearer {F.TOKENS[name]}"})
        return [row["id"] for row in r.json()["items"]]

    assert items("ada") == [1, 2, 3]
    assert items("ann") == [1]
    assert items("odd") == []


def test_the_plan_carries_the_exact_sets() -> None:
    check = check_source(F.PROGRAM, F.PROFILE)
    assert check.program is not None
    plans = {p.name: p for p in lower_api(check.program)}
    assert plans["buyers_only"].roles == ("Buyer",)
    assert plans["buyers_or_vendors"].roles == ("Buyer", "Vendor")
    assert plans["read_me"].roles == ("Admin", "Buyer", "Vendor")  # every non-Public role
    assert plans["vendors_and_admin"].roles is None  # the unchanged rule
    assert dict(plans["read_me"].role_values) == F.STORED


# ---- the checker (L801) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "auth",
    ["Roles()", "Roles(Public)", "Roles(Nobody)", "Roles(Buyer, Buyer)", 'Roles("Buyer")',
     "Roles(Buyer, extra=Vendor)", "Roles(*Buyer)", "Other(Buyer)"],
)  # fmt: skip
def test_a_malformed_role_set_is_l801(auth: str) -> None:
    assert "L801" in _errors(_endpoint(auth))


@pytest.mark.parametrize("auth", ["Roles(Buyer)", "Roles(Admin, Vendor)", "Authenticated"])
def test_a_role_set_needs_the_caller(auth: str) -> None:
    assert _errors(_endpoint(auth)) == []
    assert "L801" in _errors(_endpoint(auth, user=False))


def test_single_role_profiles_accept_their_role_and_authenticated() -> None:
    api = replace(F.API, roles=frozenset({"Public", "Customer", "Admin"}), roles_from=NoRoles())
    assert _errors(_endpoint("Roles(Customer)"), api) == []
    assert _errors(_endpoint("Authenticated"), api) == []
    assert "L801" in _errors(_endpoint("Roles(Admin)"), api)


@pytest.mark.parametrize("reserved", ["Authenticated", "Roles"])
def test_the_new_names_are_reserved(reserved: str) -> None:
    with pytest.raises(ValueError, match=reserved):
        replace(F.API, roles=frozenset({"Public", reserved, "Admin"}))


def test_stored_values_must_name_roles() -> None:
    with pytest.raises(ValueError, match="values"):
        replace(F.API, roles_from=RoleColumn("role", values={"X": "Nobody"}))
    with pytest.raises(ValueError, match="values"):
        replace(F.API, roles_from=RoleColumn("role", values={"X": "Public"}))


def test_the_unchanged_rule_still_admits_the_admin(client: TestClient) -> None:
    r = client.get("/vendors", headers={"Authorization": f"Bearer {F.TOKENS['ada']}"})
    assert r.status_code == 200
