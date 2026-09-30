"""RE2-subset pattern (Python `re` syntax, run with re.ASCII) -> JavaScript regex source
with the same fullmatch meaning (spec 2026-09-29 ui-a §4 row 7).

Translated from the parsed tree, never by text substitution: every literal becomes
`\\u{X}`, classes become alternations, ASCII categories become explicit classes, and
IGNORECASE folds ASCII letters only (JS's `i` flag folds Unicode, e.g. K and U+212A). The
result is compiled with the `u` flag and no other flag."""

from __future__ import annotations

import re
from typing import Any

try:  # Python 3.11+
    from re import _constants as _c  # type: ignore[attr-defined]
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_constants as _c  # type: ignore[no-redef]
    import sre_parse as _p  # type: ignore[no-redef]

__all__ = ["Untranslatable", "fullmatch_source"]

_ALLOWED = re.IGNORECASE | re.DOTALL | re.MULTILINE | re.VERBOSE | re.ASCII
_WORD = "[A-Za-z0-9_]"
_CATS = {
    "CATEGORY_DIGIT": "[0-9]",
    "CATEGORY_NOT_DIGIT": "[^0-9]",
    "CATEGORY_WORD": _WORD,
    "CATEGORY_NOT_WORD": "[^A-Za-z0-9_]",
    "CATEGORY_SPACE": "[\\t\\n\\v\\f\\r ]",
    "CATEGORY_NOT_SPACE": "[^\\t\\n\\v\\f\\r ]",
}
_BOUNDARY = f"(?:(?<={_WORD})(?!{_WORD})|(?<!{_WORD})(?={_WORD}))"
_NOT_BOUNDARY = f"(?:(?<={_WORD})(?={_WORD})|(?<!{_WORD})(?!{_WORD}))"


class Untranslatable(Exception):
    """The pattern has no exact JS translation (outside the RE2 subset or an unknown flag)."""


def fullmatch_source(pattern: str) -> str:
    """JS source (flag `u`) that matches a whole string exactly when
    `re.fullmatch(pattern, s, re.ASCII)` does."""
    try:
        parsed = _p.parse(pattern, re.ASCII)
    except re.error as err:
        raise Untranslatable(str(err)) from None
    flags = parsed.state.flags
    if flags & ~_ALLOWED:
        raise Untranslatable(f"flags {flags:#x}")
    return "^(?:" + _seq(parsed, flags) + ")$"


def _seq(items: Any, flags: int) -> str:  # noqa: ANN401 - re._parser internals
    return "".join(_item(str(op), av, flags) for op, av in items)


def _esc(cp: int) -> str:
    return f"\\u{{{cp:X}}}"


def _lit(cp: int, flags: int) -> str:
    ch = chr(cp)
    if flags & re.IGNORECASE and ch.isascii() and ch.isalpha():
        return f"[{_esc(ord(ch.lower()))}{_esc(ord(ch.upper()))}]"
    return _esc(cp)


def _range(lo: int, hi: int, flags: int) -> list[str]:
    out = [f"[{_esc(lo)}-{_esc(hi)}]"]
    if flags & re.IGNORECASE:
        for a, b, shift in ((97, 122, -32), (65, 90, 32)):
            s, e = max(lo, a), min(hi, b)
            if s <= e:
                out.append(f"[{_esc(s + shift)}-{_esc(e + shift)}]")
    return out


def _class(items: Any, flags: int) -> str:  # noqa: ANN401
    negate = bool(items) and str(items[0][0]) == "NEGATE"
    alts: list[str] = []
    for op, av in items[1:] if negate else items:
        name = str(op)
        if name == "LITERAL":
            alts.append(_lit(av, flags))
        elif name == "RANGE":
            alts.extend(_range(av[0], av[1], flags))
        elif name == "CATEGORY":
            alts.append(_CATS[str(av)])
        else:
            raise Untranslatable(f"class item {name}")
    body = "(?:" + "|".join(alts) + ")"
    return f"(?!{body})[\\s\\S]" if negate else body


def _item(name: str, av: Any, flags: int) -> str:  # noqa: ANN401, C901, PLR0911, PLR0912
    if name == "LITERAL":
        return _lit(av, flags)
    if name == "NOT_LITERAL":
        return f"(?!{_lit(av, flags)})[\\s\\S]"
    if name == "ANY":
        return "[\\s\\S]" if flags & re.DOTALL else "[^\\n]"
    if name == "IN":
        return _class(av, flags)
    if name == "BRANCH":
        return "(?:" + "|".join(_seq(b, flags) for b in av[1]) + ")"
    if name == "SUBPATTERN":
        _group, add, delete, sub = av
        return "(?:" + _seq(sub, (flags | add) & ~delete) + ")"
    if name in ("MAX_REPEAT", "MIN_REPEAT"):
        lo, hi, sub = av
        top = "" if hi == _c.MAXREPEAT else str(hi)
        lazy = "?" if name == "MIN_REPEAT" else ""
        return "(?:" + _seq(sub, flags) + f"){{{lo},{top}}}" + lazy
    if name == "AT":
        at = str(av)
        multiline = bool(flags & re.MULTILINE)
        return {
            "AT_BEGINNING": "(?:^|(?<=\\n))" if multiline else "^",
            "AT_BEGINNING_STRING": "^",
            "AT_END": "(?=\\n|$)" if multiline else "(?=\\n?$)",
            "AT_END_STRING": "$",
            "AT_BOUNDARY": _BOUNDARY,
            "AT_NON_BOUNDARY": _NOT_BOUNDARY,
        }[at]
    raise Untranslatable(f"{name} is outside the RE2 subset")
