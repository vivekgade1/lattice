"""RE2 subset (spec 2026-09-28 go-target §3.1, L926): patterns every target can run.

Go's regexp is RE2: no lookaround, backreferences, conditionals, possessive repeats or
atomic groups. v1.1 refuses them on every target so a program means the same
everywhere. Character classes (`\\d \\w \\s \\b`) are ASCII on every target: the Python
runtime compiles patterns with `re.ASCII`."""

from __future__ import annotations

from typing import Any

try:  # Python 3.11+
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_parse as _p  # type: ignore[no-redef]

__all__ = ["re2_problem"]

_REASONS = {
    "ASSERT": "lookahead",
    "ASSERT_NOT": "negative lookahead or lookbehind",
    "GROUPREF": "backreference",
    "GROUPREF_EXISTS": "conditional group",
    "POSSESSIVE_REPEAT": "possessive repeat",
    "ATOMIC_GROUP": "atomic group",
}


def _walk(items: Any) -> Any:  # noqa: ANN401 - re._parser internals have no public type
    for op, av in items:
        name = str(op)
        if name in _REASONS:
            reason = _REASONS[name]
            if name == "ASSERT":
                reason = "lookbehind" if av[0] < 0 else "lookahead"
            yield reason
        for sub in _children(name, av):
            yield from _walk(sub)


def _children(name: str, av: Any) -> Any:  # noqa: ANN401
    if name == "SUBPATTERN":
        yield av[-1]
    elif name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
        yield av[2]
    elif name == "BRANCH":
        yield from av[1]
    elif name in ("ASSERT", "ASSERT_NOT"):
        yield av[1]
    elif name == "ATOMIC_GROUP":
        yield av
    elif name == "GROUPREF_EXISTS":
        yield av[1]
        if av[2] is not None:
            yield av[2]


def re2_problem(pattern: str) -> str | None:
    """Why `pattern` is outside Go's RE2 subset, or None when RE2 accepts it (also for a
    pattern that does not compile: that is reported elsewhere, L916/L601)."""
    try:
        parsed = _p.parse(pattern)
    except Exception:  # noqa: BLE001 - the existing checks report invalid patterns
        return None
    for reason in _walk(parsed):
        return f"{reason} is not supported (Lattice regular expressions are the RE2 subset)"
    return None
