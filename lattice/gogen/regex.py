"""Regular expressions on Go (plan 5a Task 9): Python's own compiled form.

A T2 pattern is a string literal (the catalog marks it `literal=(0,)`), so it is compiled
here, when the Go code is generated, exactly as the Python target compiles it at run time
(`re.compile(pattern, re.ASCII)`, `lattice.t2.support._t2_re_ascii`): `re._parser.parse`
then `re._compiler._code`. The resulting SRE code -- the program CPython's regular
expression engine runs -- is embedded in the Go program, and `lattice/gogen/rt/regex.go`
runs it with a port of CPython's engine (`Modules/_sre/sre_lib.h`). Matching, captures,
the empty-match rules of findall/split/sub and every offset are therefore Python's, not
RE2's: Go's `regexp` differs from Python on `$` before a trailing newline, empty matches
next to a previous match, `\\s` (no `\\v`), and case folding.

A `re.sub` replacement is compiled here too (`re._parser.parse_template`), into literal
chunks and group numbers.

What Go cannot run is refused for Go before code is generated (`regex_problem`, L892 in
`lattice.check.p8_target`)."""

from __future__ import annotations

import re
from dataclasses import dataclass

try:  # Python 3.11+
    from re import _compiler as _c  # type: ignore[attr-defined]
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_compile as _c  # type: ignore[no-redef]
    import sre_parse as _p  # type: ignore[no-redef]

__all__ = [
    "GoRegex",
    "GoTemplate",
    "compile_regex",
    "compile_template",
    "regex_problem",
]

# The parse-tree operations the Go engine implements (lookaround, backreferences,
# conditionals, atomic groups and possessive repeats are outside the RE2 subset, L926).
_SUPPORTED = frozenset(
    {
        "LITERAL",
        "NOT_LITERAL",
        "ANY",
        "IN",
        "AT",
        "BRANCH",
        "SUBPATTERN",
        "MAX_REPEAT",
        "MIN_REPEAT",
        "CATEGORY",
        "RANGE",
        "NEGATE",
    }
)


@dataclass(frozen=True)
class GoRegex:
    """A pattern compiled as Python compiles it with re.ASCII: its SRE `code`, the
    number of capturing groups, the group names, or the exception (kind, message)
    compiling it raises in Python (every use raises it)."""

    pattern: str
    code: tuple[int, ...] = ()
    groups: int = 0
    names: tuple[tuple[str, int], ...] = ()
    error: tuple[str, str] | None = None


@dataclass(frozen=True)
class GoTemplate:
    """A `re.sub` replacement: literal chunks around group numbers (`lits` has one more
    item than `groups`), or the exception (kind, message) compiling it raises."""

    repl: str
    lits: tuple[str, ...] = ("",)
    groups: tuple[int, ...] = ()
    error: tuple[str, str] | None = None


def _kind(e: Exception) -> str:
    return "PatternError" if isinstance(e, re.error) else type(e).__name__


def _walk(items: object) -> str | None:
    for op, av in items:  # type: ignore[attr-defined]
        name = str(op)
        if name not in _SUPPORTED:
            return name
        match name:
            case "SUBPATTERN":
                subs = [av[-1]]
            case "MAX_REPEAT" | "MIN_REPEAT":
                subs = [av[2]]
            case "BRANCH":
                subs = list(av[1])
            case "IN":
                subs = [av]
            case _:
                subs = []
        for sub in subs:
            bad = _walk(sub)
            if bad is not None:
                return bad
    return None


def compile_regex(pattern: str) -> GoRegex:
    """`pattern` compiled as `re.compile(pattern, re.ASCII)` compiles it."""
    try:
        parsed = _p.parse(pattern, re.ASCII)
        code = _c._code(parsed, re.ASCII)
    except Exception as e:  # noqa: BLE001 - the program raises it on every use
        return GoRegex(pattern, error=(_kind(e), str(e)))
    names = tuple(sorted(parsed.state.groupdict.items(), key=lambda kv: kv[1]))
    return GoRegex(pattern, tuple(int(x) for x in code), parsed.state.groups - 1, names)


def regex_problem(pattern: str) -> str | None:
    """Why the Go engine cannot run `pattern`, or None."""
    try:
        parsed = _p.parse(pattern, re.ASCII)
    except Exception:  # noqa: BLE001 - raised on every use, on both targets
        return None
    bad = _walk(parsed.data)
    if bad is not None:
        return f"the regular expression uses {bad}"
    return None


def compile_template(pattern: str, repl: str) -> GoTemplate:
    """The replacement `repl` of `re.sub(pattern, repl, s)` as CPython compiles it
    (`re._compile_template`); a replacement without a backslash is taken literally."""
    if "\\" not in repl:
        return GoTemplate(repl, (repl,), ())
    try:
        compiled = re.compile(pattern, re.ASCII)
        parts = _p.parse_template(repl, compiled)
    except Exception as e:  # noqa: BLE001 - raised on every use
        return GoTemplate(repl, error=(_kind(e), str(e)))
    lits = tuple(parts[0::2])
    groups = tuple(int(g) for g in parts[1::2])
    return GoTemplate(repl, lits, groups)
