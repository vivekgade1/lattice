"""Owner transfer (spec 2026-09-27 lattice-t2 §6.4): `update(t, owner=<new owner>)` --
or the owner column set by name -- hands rows to another user. It is allowed in an
Admin endpoint, or when the rows passed an ownership check (`accessible`/`owned`,
`mine`, or `require(row, owner_is(user), ...)`); otherwise it stays L802.

The new owner is a value of the users table's key type, the caller (`user`), or a
one-row users table (`owner=<one(...) of Users>`). It must exist: the UPDATE only
writes rows whose new owner is a users row, and answers the declared
`else_=NotFound(...)` (or any declarable error) when it is not. `else_=` is required
unless the new owner is the caller or a users row read by `one()` (L404)."""

from __future__ import annotations

from textwrap import dedent

import pytest

from lattice.check import check_source
from lattice.diagnostics import Severity
from tests.api import realapps


def _check(src: str):
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), realapps.PROFILE)


def errors(src: str) -> list[str]:
    return [d.code for d in _check(src).diagnostics if d.severity is Severity.ERROR]


def diag(src: str, code: str):
    found = [d for d in _check(src).diagnostics if d.code == code]
    assert found, _check(src).diagnostics
    return found[0]


def transfer(
    check: str = "visible = accessible(items, by=user)",
    write: str = 'update(item, owner=new_owner, else_=NotFound("User not found"))',
    auth: str = "Customer",
) -> str:
    return f"""
    @endpoint("POST", "/items/{{id}}/transfer", auth={auth})
    def give(id: Uuid, new_owner: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
        items = fetch(Item)
        {check}
        found = where(visible, col("id") == id)
        item = one(found, else_=NotFound("Item not found"))
        moved = {write}
        return moved
    """


BY_EMAIL = """
@endpoint("POST", "/items/{id}/transfer", auth=Customer)
def give(id: Uuid, email: Str(max_len=255), user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    visible = accessible(items, by=user)
    found = where(visible, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    users = fetch(User)
    matched = where(users, col("email") == email)
    target = one(matched, else_=NotFound("User not found"))
    moved = update(item, owner=target)
    return moved
"""


# ---- allowed ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "check",
    [
        "visible = accessible(items, by=user)",
        "visible = owned(items, by=user)",
        "visible = mine(items, by=user)",
    ],
)
def test_a_transfer_of_checked_rows_is_allowed(check: str) -> None:
    assert errors(transfer(check=check)) == []


def test_a_transfer_after_owner_is_is_allowed() -> None:
    src = """
    @endpoint("POST", "/items/{id}/transfer", auth=Customer)
    def give(id: Uuid, new_owner: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
        items = fetch(Item)
        found = where(items, col("id") == id)
        item = one(found, else_=NotFound("Item not found"))
        mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
        moved = update(mine, owner=new_owner, else_=NotFound("User not found"))
        return moved
    """
    assert errors(src) == []


def test_the_owner_column_by_name_is_a_transfer() -> None:
    write = 'update(item, owner_id=new_owner, else_=NotFound("User not found"))'
    assert errors(transfer(write=write)) == []


def test_an_admin_endpoint_may_transfer_any_row() -> None:
    src = transfer(check='visible = where(items, col("title") != "")', auth="Admin")
    assert errors(src) == []


def test_a_one_row_users_table_is_a_new_owner_without_else() -> None:
    assert errors(BY_EMAIL) == []


def test_the_caller_is_a_new_owner_without_else() -> None:
    assert errors(transfer(write="update(item, owner=user)")) == []
    assert errors(transfer(write="update(item, owner_id=user.id)")) == []


# ---- refused ---------------------------------------------------------------------------------


def test_a_transfer_of_unchecked_rows_is_l802() -> None:
    src = """
    @endpoint("POST", "/items/{id}/transfer", auth=Customer)
    def give(id: Uuid, new_owner: Uuid, user: CurrentUser) -> One | NotFound:
        items = fetch(Item)
        found = where(items, col("id") == id)
        item = one(found, else_=NotFound("Item not found"))
        moved = update(item, owner=new_owner, else_=NotFound("User not found"))
        return moved
    """
    assert errors(src) == ["L802"]


def test_a_scalar_new_owner_needs_else() -> None:
    d = diag(transfer(write="update(item, owner=new_owner)"), "L404")
    assert "must exist" in d.message and d.hint is not None and "else_=" in d.hint


def test_a_users_table_that_may_be_empty_needs_else() -> None:
    src = BY_EMAIL.replace(
        "moved = update(item, owner=target)",
        'again = where(target, col("is_active"))\n    moved = update(item, owner=again)',
    )
    assert "L404" in errors(src)


def test_the_new_owner_has_the_key_type() -> None:
    src = transfer(write='update(item, owner="bob", else_=NotFound("User not found"))')
    assert "L402" in errors(src)


def test_owner_takes_a_users_table_only() -> None:
    src = BY_EMAIL.replace("moved = update(item, owner=target)", "moved = update(item, owner=item)")
    d = diag(src, "L404")
    assert "owner=" in d.message


def test_else_without_a_transfer_is_l404() -> None:
    src = transfer(write='update(item, title="x", else_=NotFound("no"))')
    d = diag(src, "L404")
    assert "else_=" in d.message


def test_a_table_owned_through_a_parent_has_no_owner_to_transfer() -> None:
    src = """
    @endpoint("POST", "/notes/{id}/transfer", auth=Admin)
    def give(id: Uuid, new_owner: Uuid, user: CurrentUser) -> One | NotFound:
        notes = fetch(Note)
        found = where(notes, col("id") == id)
        note = one(found, else_=NotFound("Note not found"))
        moved = update(note, owner=new_owner, else_=NotFound("User not found"))
        return moved
    """
    d = diag(src, "L404")
    assert "owned through" in d.message
