"""Caller references in T0 expressions (spec 2026-09-27 lattice-t2 §6.2):
`user.<column>` (a non-private column of the users table, e.g. `user.id`),
`user.is_admin` and `is_admin(user)` are the caller's values -- the same on every row --
usable in where/require/derive/when and as write values, where `user` is the endpoint's
`CurrentUser` parameter. A private column is L803; any other attribute access stays
L106."""

from __future__ import annotations

from textwrap import dedent

import pytest

from lattice.check import check_source
from lattice.diagnostics import Severity
from tests.api import realapps


def _check(src: str, profile=None):
    if profile is None:
        from bench.api_datasets import api_profile

        profile = api_profile()
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)


def errors(src: str, profile=None) -> list[str]:
    return [d.code for d in _check(src, profile).diagnostics if d.severity is Severity.ERROR]


def diag(src: str, code: str, profile=None):
    found = [d for d in _check(src, profile).diagnostics if d.code == code]
    assert found, _check(src, profile).diagnostics
    return found[0]


def endpoint(body: str, auth: str = "Customer", ret: str = "Page") -> str:
    lines = "\n".join("    " + line for line in dedent(body).strip().splitlines())
    return (
        f'@endpoint("GET", "/x", auth={auth})\n'
        f"def x(user: CurrentUser, limit: Int(ge=1, le=100) = 20) -> {ret}:\n{lines}\n"
    )


# ---- positive: every place an expression goes ---------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'kept = where(visible, col("customer_id") == user.id)',
        'kept = where(visible, user.is_admin | (col("customer_id") == user.id))',
        'kept = where(visible, is_admin(user) | (col("status") == "paid"))',
        'kept = derive(visible, mine=col("customer_id") == user.id, admin=is_admin(user))',
        "kept = derive(visible, who=user.email, role=user.role)",
        'kept = derive(visible, t=when(user.is_admin, then="all", otherwise="own"))',
    ],
)
def test_caller_references_type_in_expressions(line: str) -> None:
    src = endpoint(
        f"""
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        {line}
        ordered = sort(kept, by=["id"])
        result = page(ordered, limit=limit)
        return result
        """
    )
    assert errors(src) == []


def test_a_caller_only_require_condition_is_allowed() -> None:
    src = endpoint(
        """
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        checked = require(visible, ~is_admin(user), else_=Forbidden("admins use /admin"))
        ordered = sort(checked, by=["id"])
        result = page(ordered, limit=limit)
        return result
        """,
        ret="Page | Forbidden",
    )
    assert errors(src) == []


def test_a_constant_condition_without_the_caller_is_still_l304() -> None:
    src = endpoint(
        """
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        checked = require(visible, limit > 5, else_=Forbidden("no"))
        ordered = sort(checked, by=["id"])
        result = page(ordered, limit=limit)
        return result
        """,
        ret="Page | Forbidden",
    )
    assert "L304" in errors(src)


def test_caller_values_are_write_values() -> None:
    src = """
    @endpoint("POST", "/addresses", auth=Customer)
    def add(line1: Str(min_len=1, max_len=50), user: CurrentUser) -> Created:
        row = insert(Addresses, owner=user, line1=line1, city=user.name, zip="00000")
        return row
    """
    assert errors(src) == []


def test_caller_references_type_like_their_columns() -> None:
    bad = endpoint(
        """
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        kept = where(visible, col("customer_id") == user.email)
        result = page(kept, limit=limit)
        return result
        """
    )
    assert "L403" in errors(bad) or "L402" in errors(bad)


# ---- negative ------------------------------------------------------------------------------


def test_a_private_user_column_is_l803() -> None:
    src = endpoint(
        """
        items = fetch(Item)
        visible = accessible(items, by=user)
        kept = derive(visible, pw=user.hashed_password)
        result = page(kept, limit=limit)
        return result
        """
    )
    d = diag(src, "L803", realapps.PROFILE)
    assert "hashed_password" in d.message and "private" in d.message


def test_an_unknown_user_column_is_l106() -> None:
    src = endpoint(
        """
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        kept = where(visible, col("customer_id") == user.idd)
        result = page(kept, limit=limit)
        return result
        """
    )
    d = diag(src, "L106")
    assert "idd" in d.message and d.hint is not None and "user.id" in d.hint


@pytest.mark.parametrize(
    "expr",
    ["orders.id", "user.id.real", 'col("x").id', "limit.real"],
)
def test_other_attribute_access_stays_l106(expr: str) -> None:
    src = endpoint(
        f"""
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        kept = where(visible, col("customer_id") == {expr})
        result = page(kept, limit=limit)
        return result
        """
    )
    assert "L106" in errors(src)


def test_is_admin_takes_the_caller() -> None:
    src = endpoint(
        """
        orders = fetch(Orders)
        visible = accessible(orders, by=user)
        kept = where(visible, is_admin(limit))
        result = page(kept, limit=limit)
        return result
        """
    )
    d = diag(src, "L404")
    assert "is_admin(user)" in d.hint


def test_user_is_not_available_in_a_public_endpoint() -> None:
    src = """
    @endpoint("GET", "/products", auth=Public)
    def products() -> Page:
        rows = fetch(Products)
        kept = where(rows, col("id") == user.id)
        result = page(kept, limit=10)
        return result
    """
    assert errors(src)


def test_user_is_not_available_in_a_step() -> None:
    src = """
    @step
    def cheap(t: Table) -> Table:
        kept = where(t, col("id") == user.id)
        return kept
    """
    assert errors(src)


def test_a_nullable_user_column_is_nullable() -> None:
    src = endpoint(
        """
        items = fetch(Item)
        visible = accessible(items, by=user)
        named = derive(visible, who=user.full_name, mail=user.email)
        stats = group(named, by=[], longest=agg.max(fn.len(col("who"))))
        result = page(stats, limit=1)
        return result
        """
    )
    assert "L421" in errors(src, realapps.PROFILE)
    assert "L421" not in errors(src.replace('col("who")', 'col("mail")'), realapps.PROFILE)
