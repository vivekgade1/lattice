"""A static heuristic for catastrophic backtracking (ReDoS) in program-author regular
expressions: procedure patterns (`re.fullmatch(r"...", s)`, L925) and `Str(pattern=...)`
parameter constraints -- both applied to request input.

Two shapes are refused, inside a repeat without an upper bound (`*`, `+`, `{n,}`; a
possessive repeat or an atomic group never backtracks and is left alone):

* a nested unbounded repeat that nothing mandatory beside it separates -- `(a+)+`,
  `(a*)*`, `(\\w+\\s?)*`, `(x+x+)+`: every other required piece of the outer body can
  match the inner repeat's characters, so a failing input splits exponentially many
  ways (`(\\w+\\.)*` is fine: the `.` cannot be a `\\w`);
* an alternation whose branches can start with the same character -- `(a|a)*`,
  `(a|ab)*`, `(\\d|\\w)+`.

It is a heuristic: it errs toward refusing (a refused pattern can be rewritten with
disjoint pieces, a bounded repeat `{0,50}`, or a possessive `++`)."""

from __future__ import annotations

import re
from typing import Any

try:  # Python 3.11+
    from re import _constants as _c  # type: ignore[attr-defined]
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_constants as _c  # type: ignore[no-redef]
    import sre_parse as _p  # type: ignore[no-redef]

__all__ = ["redos_risk"]

_MAX = 0x10FFFF
Ranges = tuple[tuple[int, int], ...]
_ALL: Ranges = ((0, _MAX),)
_UNICODE = (128, _MAX)
_CATEGORIES: dict[Any, Ranges] = {
    _c.CATEGORY_DIGIT: ((48, 57), _UNICODE),
    _c.CATEGORY_WORD: ((48, 57), (65, 90), (95, 95), (97, 122), _UNICODE),
    _c.CATEGORY_SPACE: ((9, 13), (32, 32), _UNICODE),
}
_REPEATS = (_c.MAX_REPEAT, _c.MIN_REPEAT)


def redos_risk(pattern: str) -> str | None:
    """Why `pattern` may backtrack catastrophically, or None (also for a pattern that
    does not compile: that is reported elsewhere)."""
    try:
        parsed = _p.parse(pattern)
    except (re.error, RecursionError):
        return None
    return _scan(list(parsed))


def _items(av: Any) -> list[Any]:
    return list(av)


def _scan(seq: list[Any]) -> str | None:
    """The first risky unbounded repeat in `seq` (at any depth)."""
    for op, av in seq:
        if op in _REPEATS:
            lo, hi, body = av
            body = _items(body)
            if hi == _c.MAXREPEAT:
                why = _risky_body(body)
                if why is not None:
                    return why
            found = _scan(body)
            if found is not None:
                return found
        elif op is _c.SUBPATTERN:
            found = _scan(_items(av[-1]))
            if found is not None:
                return found
        elif op is _c.BRANCH:
            for alt in av[1]:
                found = _scan(_items(alt))
                if found is not None:
                    return found
        elif op in (_c.ASSERT, _c.ASSERT_NOT):
            found = _scan(_items(av[1]))
            if found is not None:
                return found
    return None


def _risky_body(body: list[Any]) -> str | None:
    """The body of an unbounded repeat: a nested unbounded repeat nothing mandatory
    separates, or an alternation with overlapping branches."""
    flat = _flatten(body)
    for i, (op, av) in enumerate(flat):
        if op in _REPEATS and av[1] == _c.MAXREPEAT:
            inner = _chars(_items(av[2]))
            others = [flat[j] for j in range(len(flat)) if j != i and _mandatory(flat[j])]
            if all(_overlap(inner, _chars([o])) for o in others):
                return "a repeated group holds an unbounded repeat (like (a+)+)"
        if op is _c.BRANCH:
            firsts = [_first(_items(alt)) for alt in av[1]]
            for a in range(len(firsts)):
                for b in range(a + 1, len(firsts)):
                    if _overlap(firsts[a], firsts[b]):
                        return "a repeated alternation has branches that start alike (like (a|a)*)"
    return None


def _flatten(seq: list[Any]) -> list[Any]:
    """`seq` with capturing / non-capturing groups opened (their items inline)."""
    out: list[Any] = []
    for op, av in seq:
        if op is _c.SUBPATTERN:
            out.extend(_flatten(_items(av[-1])))
        else:
            out.append((op, av))
    return out


def _mandatory(item: Any) -> bool:
    op, av = item
    if op in _REPEATS or op is _c.POSSESSIVE_REPEAT:
        return av[0] > 0
    return op not in (_c.AT, _c.ASSERT, _c.ASSERT_NOT)


def _first(seq: list[Any]) -> Ranges:
    """The characters `seq` can start with (approximate: all, when unsure)."""
    out: list[tuple[int, int]] = []
    for item in _flatten(seq):
        op, av = item
        if op in (_c.AT, _c.ASSERT, _c.ASSERT_NOT):
            continue
        out.extend(_chars([item]))
        if _mandatory(item):
            return tuple(out)
    return _ALL


def _chars(seq: list[Any]) -> Ranges:
    """Every character `seq` can consume (approximate: all, when unsure)."""
    out: list[tuple[int, int]] = []
    for op, av in seq:
        if op is _c.LITERAL:
            out.append((av, av))
        elif op is _c.IN:
            for iop, iav in av:
                if iop is _c.LITERAL:
                    out.append((iav, iav))
                elif iop is _c.RANGE:
                    out.append(iav)
                elif iop is _c.CATEGORY and iav in _CATEGORIES:
                    out.extend(_CATEGORIES[iav])
                else:
                    return _ALL  # a negation, a negated category, ...
        elif op in _REPEATS or op is _c.POSSESSIVE_REPEAT:
            out.extend(_chars(_items(av[2])))
        elif op is _c.SUBPATTERN:
            out.extend(_chars(_items(av[-1])))
        elif op is _c.BRANCH:
            for alt in av[1]:
                out.extend(_chars(_items(alt)))
        elif op in (_c.AT, _c.ASSERT, _c.ASSERT_NOT):
            continue
        else:
            return _ALL  # any char, a negated literal, a back reference, ...
    return tuple(out)


def _overlap(a: Ranges, b: Ranges) -> bool:
    return any(lo1 <= hi2 and lo2 <= hi1 for lo1, hi1 in a for lo2, hi2 in b)
