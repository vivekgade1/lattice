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


def _names(tables: list[str], conj: str = "or") -> str:
    return tables[0] if len(tables) == 1 else ", ".join(tables[:-1]) + f" {conj} " + tables[-1]


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
    parts = []
    if owned:
        bypass = f"; superusers (`{c}.{admin.column}`) may access every row" if admin else ""
        example = next(iter(policy.owners.items()), None)
        how = f" (e.g. `{example[0]}.{example[1]} == {c}.id`)" if example else ""
        line = (
            f"{'; '.join(owned)}. A route reads, changes or deletes only the signed-in "
            f"caller's own rows -- filter or check them by the caller{how}{bypass}. A new "
            "row belongs to the caller."
        )
        grouped: dict[tuple[int, str | None], list[str]] = {}
        for t, a in policy.not_owned.items():
            grouped.setdefault((a.status, a.detail), []).append(f"`{t}`")
        for (status, detail), tables in grouped.items():
            line += f" Another user's {_names(tables)} row answers {_answer(status, detail)}" + (
                " (lookups are scoped to the caller)." if status == 404 else "."
            )
        parts.append(line)
    if policy.shared:
        tables = [f"`{t}`" for t in policy.shared]
        parts.append(
            f"{_names(tables, 'and')} rows declare no "
            "owner: they are shared by every signed-in user, and no route has to scope "
            "them to the caller."
        )
    if parts or policy.users_table:
        line = "- Ownership: " + " ".join(parts)
        if policy.users_table:
            line += (
                f" Only admins change another user's `{policy.users_table}` row."
                if admin
                else f" A route changes only the caller's own `{policy.users_table}` row."
            )
        out.append(line.replace(":  ", ": "))

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
    out.append(
        "- Writes: a request's writes succeed or fail together. Every check that can "
        "refuse the request comes before the first commit, so a refused request leaves "
        "nothing it wrote in the database (commit once, after the last check; calling a "
        "helper that commits counts as committing)."
    )
    return "\n".join(out) + "\n"


__all__ = ["describe_policy"]
