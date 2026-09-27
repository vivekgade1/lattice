"""Shared profile and helpers for the T2 (@procedure) checker tests."""

from __future__ import annotations

from textwrap import dedent

from lattice.check import CheckResult, check_source
from lattice.diagnostics import Diagnostic, Severity
from lattice.profile import ApiProfile, NativeSig, Profile
from lattice.typesys import Scalar, Schema

S = Scalar

USERS = Schema.from_pairs(
    [
        ("id", S.INT),
        ("email", S.STR),
        ("role", S.STR),
        ("display_name", S.STR),
        ("token", S.STR),
        ("password_hash", S.STR),
    ],
    keys=[("id",)],
    nullable=["display_name"],
    private=["password_hash", "token"],
)
ITEMS = Schema.from_pairs(
    [
        ("id", S.INT),
        ("owner_id", S.INT),
        ("title", S.STR),
        ("created_at", S.DATETIME),
        ("due", S.DATE),
        ("score", S.FLOAT),
        ("secret", S.STR),
    ],
    keys=[("id",)],
    owner="owner_id",
    nullable=["due"],
    private=["secret"],
)
LOGIN_EVENTS = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("minute", S.INT), ("kind", S.STR)],
    keys=[("id",)],
)
API = ApiProfile(
    tables={"Users": USERS, "Items": ITEMS, "LoginEvents": LOGIN_EVENTS},
    table_names={"Users": "users", "Items": "items", "LoginEvents": "login_events"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    natives={
        "send_mail": NativeSig((S.STR, S.STR), effect=True, capability="mail"),
        "ping": NativeSig((S.STR,), effect=True),  # capability defaults to its name
        "slugify": NativeSig((S.STR,), S.STR),
    },
    capabilities=frozenset({"http"}),
)
PROFILE = Profile(schemas={}, api=API)
TABULAR = Profile(schemas={"Items": Schema.from_pairs([("id", S.INT)])})


def check(src: str, profile: Profile = PROFILE) -> CheckResult:
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)


def diags(src: str, profile: Profile = PROFILE) -> tuple[Diagnostic, ...]:
    return check(src, profile).diagnostics


def codes(src: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in diags(src, profile)]


def errors(src: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in diags(src, profile) if d.severity is Severity.ERROR]


def ok(src: str, profile: Profile = PROFILE) -> CheckResult:
    result = check(src, profile)
    assert result.ok, "\n".join(d.render() for d in result.diagnostics)
    return result


def first(src: str, code: str, profile: Profile = PROFILE) -> Diagnostic:
    found = [d for d in diags(src, profile) if d.code == code]
    assert found, "\n".join(d.render() for d in diags(src, profile))
    return found[0]


def proc(body: str, sig: str = "(x: Int) -> Int", deco: str = "@procedure") -> str:
    """A one-procedure program with the given body (indented 4 spaces)."""
    lines = dedent(body).strip("\n").splitlines()
    inner = "\n".join("    " + line for line in lines)
    return f"{deco}\ndef f{sig}:\n{inner}\n"
