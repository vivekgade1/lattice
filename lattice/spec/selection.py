"""Choosing which scenarios a check runs (``check(..., only=...)``, ``--only``).

A **selector** names scenarios of the spec:

* ``scenario:<name>`` -- the scenario called ``<name>``;
* ``<name>`` -- the same (a bare name);
* a glob on scenario names (:func:`fnmatch.fnmatchcase`: ``*``, ``?``, ``[...]``), bare or
  after ``scenario:``, e.g. ``auth_*`` or ``scenario:*login*``;
* ``rule:<id>`` -- the scenarios the project's trace (``spec.trace.json`` next to the spec
  module, :mod:`lattice.spec.rules`) links to rule ``<id>`` (a glob on rule ids works
  too, e.g. ``rule:R1?``). A rule the trace links to no scenario is an error.

A selector with a colon whose prefix is neither ``scenario`` nor ``rule`` is a bare name
(scenario names may contain colons). Every selector must match at least one scenario: an
unknown name is a :class:`SelectionError` naming the nearest scenario names (a typo must
never run zero scenarios silently), and so is an empty selector list.

:func:`select_scenarios` answers the selected names in the spec's order (not the
selectors'), each once. The selection restricts layers D and X only (and the invariants
and machine checks run after their requests); layers A, B, C and R are unaffected.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from lattice.spec.check import CheckError
from lattice.spec.model import Spec

GLOB_CHARS = frozenset("*?[")
KINDS = ("scenario", "rule")


class SelectionError(CheckError):
    """A selector matches nothing, the selection is empty, or a ``rule:`` selector cannot
    be resolved (no trace, an unknown rule, a rule linked to no scenario)."""


def parse_selector(text: str) -> tuple[str, str]:
    """``(kind, pattern)`` of one selector: kind ``scenario`` or ``rule``."""
    raw = text.strip()
    if not raw:
        raise SelectionError("an empty selector")
    kind, sep, rest = raw.partition(":")
    if sep and kind in KINDS:
        if not rest:
            raise SelectionError(f"selector {raw!r}: nothing after {kind}:")
        return kind, rest
    return "scenario", raw


def read_only_file(path: Path | str) -> list[str]:
    """The selectors in ``path``: a JSON array of strings, or one selector per line
    (blank lines and lines starting with ``#`` skipped)."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as err:
        raise SelectionError(f"--only-file {p}: {err}") from err
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as err:
            raise SelectionError(f"--only-file {p}: not a JSON array: {err}") from err
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise SelectionError(f"--only-file {p}: a JSON array of strings expected")
        return [x for x in data if x.strip()]
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def trace_links(trace: Any) -> dict[str, list[str]]:
    """rule id -> its link targets, from a :class:`lattice.spec.rules.Trace`, its JSON
    mapping, or a path to its JSON file."""
    if trace is None:
        return {}
    if isinstance(trace, str | Path):
        try:
            trace = json.loads(Path(trace).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise SelectionError(f"the trace {trace}: {err}") from err
    links = trace.get("links") if isinstance(trace, Mapping) else getattr(trace, "links", None)
    if not isinstance(links, Mapping):
        return {}
    return {str(k): [str(t) for t in v or []] for k, v in links.items()}


def _is_glob(pattern: str) -> bool:
    return any(c in GLOB_CHARS for c in pattern)


def _nearest(word: str, names: Iterable[str], n: int = 5) -> str:
    names = list(names)
    close = difflib.get_close_matches(word, names, n=n, cutoff=0.5)
    if not close:
        # a substring hit is a better hint than nothing (e.g. 'login' in a long name)
        close = [x for x in names if word.lower().strip("*?") in x.lower()][:n]
    return ", ".join(close) if close else "(none close)"


def select_scenarios(spec: Spec, selectors: Sequence[str], trace: Any = None) -> list[str]:
    """The names of ``spec``'s scenarios ``selectors`` select, in the spec's order (see
    the module docstring). Raises :class:`SelectionError`."""
    names = [s.name for s in spec.scenarios]
    known = set(names)
    if not selectors:
        raise SelectionError("--only selects no scenario: the selector list is empty")
    links: dict[str, list[str]] | None = None
    chosen: set[str] = set()
    for text in selectors:
        kind, pattern = parse_selector(text)
        if kind == "scenario":
            hits = (
                [n for n in names if fnmatchcase(n, pattern)]
                if _is_glob(pattern)
                else [pattern] if pattern in known else []
            )  # fmt: skip
            if not hits:
                raise SelectionError(
                    f"--only {text!r} matches no scenario of the spec "
                    f"({len(names)} scenarios); nearest: {_nearest(pattern, names)}"
                )
            chosen.update(hits)
            continue
        if links is None:
            links = trace_links(trace)
            if trace is None:
                raise SelectionError(
                    f"--only {text!r}: rule selectors need the project trace "
                    "(spec.trace.json next to the spec module, or --trace PATH); none found"
                )
        rule_ids = (
            [r for r in links if fnmatchcase(r, pattern)] if _is_glob(pattern)
            else [pattern] if pattern in links else []
        )  # fmt: skip
        if not rule_ids:
            raise SelectionError(
                f"--only {text!r}: the trace links no rule {pattern!r}; nearest: "
                f"{_nearest(pattern, links)}"
            )
        hits = []
        for rid in rule_ids:
            for target in links[rid]:
                tkind, _, tname = target.partition(":")
                if tkind == "scenario" and tname in known:
                    hits.append(tname)
        if not hits:
            targets = sorted({t for rid in rule_ids for t in links[rid]})
            raise SelectionError(
                f"--only {text!r}: the trace links no scenario of the spec to "
                f"{', '.join(rule_ids)} (its targets: {', '.join(targets) or 'none'})"
            )
        chosen.update(hits)
    selected = [n for n in names if n in chosen]
    if not selected:  # pragma: no cover -- every selector matched at least one name
        raise SelectionError("--only selects no scenario")
    return selected


def find_trace(spec_module_file: Path | str | None) -> Path | None:
    """The trace next to a spec module (``<stem>.trace.json``), when it exists."""
    if spec_module_file is None:
        return None
    from lattice.spec.rules import trace_path

    p = trace_path(spec_module_file)
    return p if p.is_file() else None


__all__ = [
    "SelectionError",
    "find_trace",
    "parse_selector",
    "read_only_file",
    "select_scenarios",
    "trace_links",
]
