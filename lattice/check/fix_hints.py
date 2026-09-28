"""Shared wording for hints that name the program's own tables and columns (node C3).

A hint says what to write, with the program's names; these helpers answer the questions
several passes ask: which column of a table would be its owner column, and how to say
"declare it in the profile"."""

from __future__ import annotations

from lattice.profile import ApiProfile
from lattice.typesys import Schema

PROFILE_FILE = "profile.py"
# column names that hold the owning user's id, besides <users table>_id
_OWNER_NAMES = ("owner_id", "user_id", "author_id", "created_by", "creator_id", "owner")


def users_key(api: ApiProfile) -> str | None:
    """The single-column key of the users table (e.g. 'id'), if it has one."""
    users = api.tables.get(api.users_table)
    if users is None:
        return None
    single = [k[0] for k in users.keys if len(k) == 1]
    return single[0] if single else ("id" if "id" in users else None)


def owner_candidate(schema: Schema, api: ApiProfile) -> str | None:
    """The column of an unowned table that would be its owner column: named like an
    owner (owner_id, user_id, <users table>_id, ...) and typed like the users table's
    key. None when no column fits (then the table really is shared)."""
    if schema.owner is not None or schema.owner_via is not None:
        return None
    users = api.tables.get(api.users_table)
    key = users_key(api)
    want = users.get(key) if users is not None and key is not None else None
    names = (*_OWNER_NAMES, f"{api.users_table.lower()}_id")
    for name in names:
        if name in schema and (want is None or schema.get(name) is want):
            return name
    return None


def declare_owner(table: str, column: str) -> str:
    """How to declare `column` as `table`'s owner column, and what that implies."""
    return (
        f"declare it in {PROFILE_FILE}: add owner='{column}' to {table}'s "
        f"Schema.from_pairs(...) (then every endpoint that returns or writes {table} rows "
        "filters them with accessible(..., by=user))"
    )
