"""Stable evidence: the values an app mints at run time, replaced by placeholders.

A rules import runs the app under test, and the app answers with values it makes up on the
spot: a bearer token, a bcrypt salt, a trace id, a ``createdAt`` stamp, a database row's
``updated_at``. They are not evidence of a rule (the rule is that the answer carries a token, a
stamp, an id of that shape), and they differ on every run, so a report that records them is
never the same twice. :func:`stabilise` replaces them, at the point the evidence is written,
with a placeholder naming the shape: ``<token>``, ``<hash>``, ``<hex>``, ``<trace-id>``,
``<guid>``, ``<timestamp>``. Everything else is kept as it is: status codes, field names, the
structure, every value that is not minted (a seeded id, a name, an enum word, a number).

What is masked, by the value (any string, whole or embedded) and by the key:

* a bcrypt hash; a JWT (three base64url parts, the first starting ``eyJ``); a W3C trace parent
  (``00-<32 hex>-<16 hex>-<2 hex>``, ASP.NET's ``traceId``); an ISO 8601 date and time (``T`` or
  a space; the fraction and the zone optional);
* a whole string that looks like an opaque token: 32 or more base64url characters with upper
  case, lower case and a digit, ``-`` or ``_`` (``secrets.token_urlsafe``; a snake_case name has
  no upper case), or dotted base64url parts of 16 or more characters each (``payload.signature``);
  32 or more hex digits holding a digit, a letter and at least six different characters (a run of
  ``b`` a boundary probe sends is not a digest);
* a Guid the app made for a row it created: the ``id`` of a row in a probe's ``db_diff`` ``added``
  list, or of a sample row in a proposal's ``evidence`` (the seeded Guids, which are the same on
  every run, and the foreign keys that point at them, are kept), wherever that probe's record or
  that proposal repeats it;
* the string value of a key naming a credential (``access_token``, ``refresh_token``,
  ``accessToken``, ``token`` and so on), whatever its form.

:func:`stabilise` does not change its argument. :func:`lattice.importer.rules.write_rules` applies
it to a .NET import (``aspnetcore``); the Python and Go imports are written as before.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

BCRYPT = re.compile(r"\$2[abxy]\$\d\d\$[./A-Za-z0-9]{53}")
JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
TRACE_PARENT = re.compile(
    r"(?<![0-9A-Za-z])00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}(?![0-9A-Za-z])"
)
TIMESTAMP = re.compile(
    r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d(?::?\d\d)?)?(?![0-9A-Za-z])"
)
GUID = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
    r"-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
HEX = re.compile(r"[0-9a-fA-F]{32,}")
OPAQUE = re.compile(r"[A-Za-z0-9_-]{32,}")
DOTTED = re.compile(r"[A-Za-z0-9_-]{16,}(?:\.[A-Za-z0-9_-]{16,}){1,2}")

#: The keys whose string value is a credential the app made up, whatever it looks like.
CREDENTIAL_KEYS = frozenset({
    "access_token", "accesstoken", "refresh_token", "refreshtoken", "id_token", "idtoken",
    "token", "jwt", "api_key", "apikey", "session_token", "sessiontoken", "csrf_token",
    "csrftoken", "xsrf_token", "xsrftoken", "bearer",
})  # fmt: skip


def _has_mark(part: str) -> bool:
    return any(c.isdigit() or c in "-_" for c in part)


def _is_hex_digest(s: str) -> bool:
    """32 or more hex digits with a digit, a letter and a spread of characters: not a run of
    ``b`` that a boundary probe sends."""
    return bool(
        HEX.fullmatch(s) and any(c.isdigit() for c in s) and any(c.isalpha() for c in s)
        and len(set(s.lower())) >= 6
    )  # fmt: skip


def _looks_opaque(s: str) -> bool:
    if _is_hex_digest(s):
        return True
    if DOTTED.fullmatch(s) and all(_has_mark(part) for part in s.split(".")):
        return True  # ``payload.signature``; a dotted name (``Service.Method``) has no digit
    return bool(
        OPAQUE.fullmatch(s)
        and any(c.isupper() for c in s)
        and any(c.islower() for c in s)
        and any(c.isdigit() or c in "-_" for c in s)
    )


def stabilise_text(text: str, *, minted: Collection[str] = ()) -> str:
    """``text`` with its minted values replaced by placeholders (see the module docstring);
    ``minted``: the lower-case Guids the app made."""
    if _looks_opaque(text):
        return "<hex>" if _is_hex_digest(text) else "<token>"
    text = BCRYPT.sub("<hash>", text)
    text = JWT.sub("<token>", text)
    text = TRACE_PARENT.sub("<trace-id>", text)
    text = TIMESTAMP.sub("<timestamp>", text)
    if minted:
        text = GUID.sub(lambda m: "<guid>" if m.group(0).lower() in minted else m.group(0), text)
    return text


def _row_guids(rows: Any) -> set[str]:
    """The Guid primary keys (``id``) of the row dicts in ``rows``."""
    found: set[str] = set()
    for row in rows if isinstance(rows, list) else ():
        if isinstance(row, Mapping):
            for k, v in row.items():
                if isinstance(k, str) and k.lower() == "id" and isinstance(v, str) \
                        and GUID.fullmatch(v):  # fmt: skip
                    found.add(v.lower())
    return found


def _minted_here(obj: Mapping[str, Any]) -> set[str]:
    """The Guids of the rows the app created that this mapping records: a probe record's
    ``db_diff`` (``{table: {"added": [rows]}}``), a proposal's evidence (``rows``)."""
    found = _row_guids(obj.get("rows"))
    diff = obj.get("db_diff")
    if isinstance(diff, Mapping):
        for table in diff.values():
            if isinstance(table, Mapping):
                found |= _row_guids(table.get("added"))
    return found


def stabilise(obj: Any, *, minted: Collection[str] = (), key: str | None = None) -> Any:
    """A copy of the JSON-like ``obj`` with every minted value replaced by a placeholder."""
    if isinstance(obj, str):
        if key is not None and key.lower() in CREDENTIAL_KEYS and obj:
            return "<token>"
        return stabilise_text(obj, minted=minted)
    if isinstance(obj, Mapping):
        here = _minted_here(obj)
        if here:
            minted = frozenset(minted) | here
        return {k: stabilise(v, minted=minted, key=k if isinstance(k, str) else None)
                for k, v in obj.items()}  # fmt: skip
    if isinstance(obj, (list, tuple)):
        return [stabilise(v, minted=minted) for v in obj]
    return obj
