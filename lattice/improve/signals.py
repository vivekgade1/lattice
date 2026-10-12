"""Machine codes for the improvement signals (spike 3): a finding's ``signal_key`` and a
build's ``cause``, so records cluster without anyone reading prose.

``signal_key`` is ``<layer>:<class>``:

* a check finding (``lattice spec check --json``): the layer letter and a stable class --
  layer A the checker's code (``A:G012``); D/B the first problem's kind (``status``, ``body``,
  ``db``, ``await``), ``spec_problem`` when the check's "spec problem:" line
  (:func:`lattice.spec.preconditions.unestablished`) is among the problems; C ``invariant``;
  X ``race``; R ``rule``; a finding with no problem text ``loud``/``silent``;
* a lint finding of a change set: ``lint:<L-code>`` (``lint:L012``);
* an ``Unchecked(...)`` entry of the spec: ``unchecked:<reason_code>`` (:func:`unchecked_reason`,
  a keyword table: the spec language has no reason field, so the code is derived here);
* a finding Studio makes itself: ``studio:<what>`` (``studio:spec_edited``,
  ``studio:agent_timeout``);
* a gate whose output had no parsable finding: ``gate:exit_<rc>``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "SPEC_PROBLEM",
    "build_cause",
    "check_finding_key",
    "has_spec_problem",
    "lint_key",
    "text_finding_key",
    "unchecked_key",
    "unchecked_reason",
]

SPEC_PROBLEM = "spec problem:"  # the line lattice.spec.preconditions.unestablished writes
_KINDS = ("status", "body", "db", "await", "headers", "header")
_LAYERS = frozenset("ABCDRX")

# unchecked reason codes: (code, regex on the lowercased text), first match wins
_UNCHECKED = (
    (
        "request_header",
        r"request header|send (?:a )?(?:request )?header|authorization header|"
        r"x-[a-z-]+ header|cannot send (?:request )?headers?",
    ),
    ("response_header", r"response header|read (?:a )?header"),
    ("carried_value", r"carr(?:y|ied|ies) (?:a )?value|carried|value from an earlier|capture"),
    ("pattern_match", r"pattern|regex|matches a format"),
    ("stub_observability", r"stub records nothing|records nothing|stub .*(?:not|no) observ"),
    ("web_ui", r"web ui|browser|not observable over http"),
    ("needs_binary", r"needs the claude binary|claude binary|needs an app|needs docker"),
    ("concurrency", r"concurren|race"),
    ("production_only", r"production|deploy"),
    ("context", r"\bis context\b|context;"),
)


def has_spec_problem(problems: Sequence[Any] | None) -> bool:
    return any(str(p).strip().lower().startswith(SPEC_PROBLEM) for p in problems or ())


def check_finding_key(f: Mapping[str, Any]) -> str:
    """The ``signal_key`` of one finding of a ``lattice spec check --json`` report."""
    layer = str(f.get("layer") or "").upper()[:1] or "gate"
    if layer == "A":
        return f"A:{f.get('code') or 'other'}"
    details = f.get("details") if isinstance(f.get("details"), Mapping) else {}
    problems = [str(p) for p in details.get("problems") or [] if p]
    if has_spec_problem(problems):
        return f"{layer}:spec_problem"
    if layer == "X":
        return "X:race"
    if layer == "C":
        return "C:invariant"
    if layer == "R":
        return "R:rule"
    if problems:
        word = re.split(r"[\s:]", problems[0].strip(), maxsplit=1)[0].lower()
        return f"{layer}:{word if word in _KINDS else 'other'}"
    if details.get("error"):
        return f"{layer}:error"
    return f"{layer}:{f.get('verdict') or 'other'}"


def text_finding_key(title: str, *, exit_code: int | None = None, fallback: bool = False) -> str:
    """The ``signal_key`` of a finding parsed from a gate's text output (no JSON report):
    ``gate:exit_<rc>`` for a gate that failed without a parsable finding; else the layer when
    the line names one (``[D silent] ...``, ``D silent ...``), with ``spec_problem`` or the
    problem kind when the line has one."""
    if fallback:
        return f"gate:exit_{exit_code if exit_code is not None else 'unknown'}"
    t = str(title).strip()
    m = re.match(r"^\[?([A-Z])\s+(?:silent|loud)\]?\s", t)
    layer = m.group(1) if m and m.group(1) in _LAYERS else "gate"
    low = t.lower()
    if SPEC_PROBLEM in low:
        return f"{layer}:spec_problem"
    for k in _KINDS:
        if re.search(rf"(?:^|[/:;]\s*|\s){k}[\s:]", low):
            return f"{layer}:{k}"
    return f"{layer}:finding"


def lint_key(code: str) -> str:
    return f"lint:{code}"


def unchecked_reason(text: str) -> str:
    """A stable reason code for an ``Unchecked(...)`` text (keyword table; ``free_text`` when
    nothing matches)."""
    low = str(text).lower()
    for code, rx in _UNCHECKED:
        if re.search(rx, low):
            return code
    return "free_text"


def unchecked_key(text: str) -> str:
    return f"unchecked:{unchecked_reason(text)}"


def _failing(rnd: Mapping[str, Any]) -> frozenset[str]:
    check = rnd.get("check") or {}
    return frozenset(
        str(f.get("target") or f.get("title") or "") for f in check.get("findings") or []
    )


def build_cause(rec: Mapping[str, Any], *, stopped_by: str | None = None) -> str | None:
    """The machine cause of a build record that did not pass (None when it passed).

    Precedence: ``stopped_by_owner`` (a stop the owner asked for; another stop is ``other``),
    ``usage_limit``, ``spec_unsatisfiable`` (the last round's findings hold the check's "spec
    problem" line: nothing could have passed), ``token_budget``, ``timeout``, ``no_progress``
    (the last two rounds failed on the same set), ``regression`` (every in-scope target passes,
    a check that passed before fails), ``repair_exhausted`` (every round used), ``other``."""
    status = rec.get("status")
    if status == "passed":
        return None
    if status == "stopped":
        return "stopped_by_owner" if stopped_by in (None, "owner") else "other"
    if status == "interrupted":
        return "other"
    rounds = [r for r in rec.get("rounds") or [] if isinstance(r, Mapping)]
    last = rounds[-1] if rounds else {}
    err = rec.get("error") or last.get("error") or {}
    if err.get("kind") == "usage_limit":
        return "usage_limit"
    findings = (last.get("check") or {}).get("findings") or []
    if any(
        str(f.get("signal_key") or "").endswith(":spec_problem")
        or SPEC_PROBLEM in str(f.get("title") or "").lower()
        for f in findings
    ):
        return "spec_unsatisfiable"
    lerr = last.get("error") or {}
    if lerr.get("kind") == "timeout" and lerr.get("reason") == "node_max_tokens":
        return "token_budget"
    if last.get("status") == "timeout" or lerr.get("kind") == "timeout":
        return "timeout"
    if len(rounds) >= 2 and _failing(rounds[-1]) and _failing(rounds[-1]) == _failing(rounds[-2]):
        return "no_progress"
    if last.get("regressions") and last.get("scope_passing") == last.get("scope_total"):
        return "regression"
    if rounds and len(rounds) >= int(rec.get("max_rounds") or 0) > 0:
        return "repair_exhausted"
    return "other"
