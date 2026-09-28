"""A policy as a short statement for a model's prompt (arm Q's system prompt).

It states the guarantees the edit will be checked for, in the app's own names, with the
same facts a Lattice profile gives about them (owner columns, private columns, the admin
flag, the page-size bound, the app's error answers), and no fix recipes beyond the
one-line idiom each rule needs -- the recipes come back as the findings' hints.
"""

from __future__ import annotations

from lattice.pycheck.policy import Policy


def _answer(status: int, detail: str | None) -> str:
    return f'{status} "{detail}"' if detail else str(status)


def describe_policy(policy: Policy) -> str:
    c = policy.caller
    admin = policy.admin
    out = [
        "## Guarantees your edit is checked for",
        "",
        "Before the app is run, the edited code is checked for these guarantees; each "
        "violation comes back with its file, line and a suggested fix.",
        "",
    ]

    owned = [f"`{t}` rows belong to the user in `{t}.{col}`" for t, col in policy.owners.items()]
    owned += [
        f"`{t}` rows belong to the owner of their `{parent}` (`{t}.{fk}`)"
        for t, (fk, parent) in policy.owner_via.items()
    ]
    if owned:
        example = next(iter(policy.owners.items()))
        bypass = f"; superusers (`{c}.{admin.column}`) may access every row" if admin else ""
        line = (
            f"- Ownership: {'; '.join(owned)}. A route reads, changes or deletes only the "
            f"signed-in caller's own rows -- filter or check them by the caller (e.g. "
            f"`{example[0]}.{example[1]} == {c}.id`){bypass}. A new row belongs to the caller."
        )
        grouped: dict[tuple[int, str | None], list[str]] = {}
        for t, a in policy.not_owned.items():
            grouped.setdefault((a.status, a.detail), []).append(f"`{t}`")
        for (status, detail), tables in grouped.items():
            names = tables[0] if len(tables) == 1 else ", ".join(tables[:-1]) + " or " + tables[-1]
            line += f" Another user's {names} row answers {_answer(status, detail)}" + (
                " (lookups are scoped to the caller)." if status == 404 else "."
            )
        if policy.users_table:
            line += (
                f" Only admins change another user's `{policy.users_table}` row."
                if admin
                else f" A route changes only the caller's own `{policy.users_table}` row."
            )
        out.append(line)

    private = [f"`{t}.{col}`" for t, cols in policy.private.items() for col in cols]
    if private:
        out.append(
            f"- Private columns: {', '.join(private)} never "
            + ("reaches" if len(private) == 1 else "reach")
            + " a response"
            + (
                f" (only admin-only routes may return {'it' if len(private) == 1 else 'them'})."
                if admin
                else "."
            )
        )
    out.append(
        "- Bounds: a request string or number written to the database has a bound "
        "(`max_length` for strings, `ge`/`le` for numbers, on the request model or "
        f"parameter), and a page size used as a row limit has `le=` at most "
        f"{policy.page_limit_max}."
    )
    out.append(
        "- Route order: a literal path (e.g. `/stats`) is declared before a parameter path "
        "of the same method that would also match it (e.g. `/{id}`)."
    )
    errors = "- Errors: HTTP errors follow the app's own answers"
    if admin is not None and admin.status is not None:
        errors += (
            f"; a non-admin is refused as `{admin.dependency}` refuses "
            f"({_answer(admin.status, admin.detail)})"
        )
    out.append(errors + ".")
    if policy.naive_datetimes:
        out.append(
            "- Datetimes: the database returns stored datetimes naive (in UTC); a naive and "
            "an aware datetime are never compared, and a request datetime (which may carry "
            "any UTC offset) is converted to naive UTC before it is compared with a stored "
            "one."
        )
    else:
        out.append("- Datetimes: a naive and an aware datetime are never compared.")
    return "\n".join(out) + "\n"


__all__ = ["describe_policy"]
