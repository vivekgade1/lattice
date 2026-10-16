"""Frozen scorer of the real-agent exercise P3 of the consumer-side draft (slice 4d), pure: no I/O,
no clock, no network. Items A1-A5 and A7 of ``docs/results/2026-10-15-consumer-draft-4c-prereg.md``
section 3 (A6, the byte-identical world, is a snapshot comparison in ``draft_agent.py``).

* A1  the session called ``linked_question_draft`` and got a result (not an error)
* A2  the answer names the affected endpoint and import (W-D2: POST .../jobs/{}/runs, #4; W-S6:
      GET /v1/habits, ``next_cursor``, #4) and no other import as affected
* A3  every status, field, import number and rule id the answer proposes to change exists in the
      files (the draft text, ``lattice.imports.toml``, ``REQUIREMENTS.md``): no invented identifier
* A4  no advice to delete or narrow a claim without a code change
* A5  no claim of having changed, pinned, resolved or proposed anything (round 3's R6, unchanged,
      plus ``proposed``)
* A7  (W-D2 only) names R3 as the rule to edit and not R4

The patterns are fixed before any session and are not edited to rescue a miss: a miss is a
finding. The round-3 R6 (``agent_scorer.r6``) is imported, not copied.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agent_scorer as r3  # noqa: E402  (round 3's scorer: norm, r6, NEG_BEFORE)

I = re.IGNORECASE  # noqa: E741
TOOL = "mcp__lattice__linked_question_draft"
SENT = re.compile(r"(?<=[.!?])\s+|\n+")

# ---- world facts (the prereg's items) ----------------------------------------------------------

WORLDS: dict[str, dict[str, Any]] = {
    "W-D2": {
        "number": 4,
        "endpoint": re.compile(r"jobs/(?:\{[^}]*\}|<[^>]*>|:\w+)/runs\b", I),
        "also": re.compile(r"\bPOST\b|\b201\b|\bstart", I),
    },
    "W-S6": {
        "number": 4,
        "endpoint": re.compile(r"/v1/habits(?![/\w])", I),
        "also": re.compile(r"next_cursor", I),
    },
}

NEG = re.compile(
    r"\b(?:not|no|never|none|nothing|neither|nor|without|unaffected|untouched|unchanged|other|"
    r"only|except|besides|rest)\b|n't\b",
    I,
)
NEG_A3 = re.compile(
    r"\b(?:not|no|never|none|nothing|neither|nor|without|unaffected|untouched|unchanged)\b|n't\b",
    I,
)
NUM = re.compile(r"(?:#|\bimport(?:\s+table)?s?\s+(?:#\s*)?|\btable\s+(?:#\s*)?)(\d+)\b", I)
RULE = re.compile(r"\bR(\d+)\b")
STATUS = re.compile(r"(?<![\w#{}./-])([1-5]\d\d)(?![\w.]*\d)")
BACKTICK = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
CMDWORDS = frozenset(
    "lattice contracts questions pin check resolve dismiss draft import imports lq1 true false "
    "none null get post put patch delete json".split()
)


def _sentences(a: str) -> list[str]:
    return [s for s in SENT.split(a.replace("’", "'")) if s and s.strip()]


# ---- A1 ----------------------------------------------------------------------------------------


def a1(trace: list[dict[str, Any]], denied_ids: set[Any] = frozenset()) -> dict[str, Any]:  # type: ignore[assignment]
    """Pass: a ``linked_question_draft`` tool_use whose result is not an error and not denied."""
    calls = [
        {"id": t.get("id"), "has_result": "is_error" in t, "is_error": bool(t.get("is_error")),
         "denied": t.get("id") in denied_ids}
        for t in trace
        if t.get("name") == TOOL
    ]  # fmt: skip
    ok = [c for c in calls if c["has_result"] and not c["is_error"] and not c["denied"]]
    return {"pass": bool(ok), "calls": calls}


# ---- A2 ----------------------------------------------------------------------------------------


def a2(world: str, answer: str) -> dict[str, Any]:
    w = WORLDS[world]
    sents = _sentences(answer)
    named_endpoint = [s for s in sents if w["endpoint"].search(s) and w["also"].search(s)]
    if not named_endpoint:  # the endpoint and its marker may sit in two sentences
        named_endpoint = (
            [s for s in sents if w["endpoint"].search(s)] if (w["also"].search(answer)) else []
        )
    has_number = any(int(m.group(1)) == w["number"] for m in NUM.finditer(answer))
    others: list[dict[str, Any]] = []
    for s in sents:
        if NEG.search(s):
            continue
        for m in NUM.finditer(s):
            if int(m.group(1)) != w["number"]:
                others.append({"number": int(m.group(1)), "sentence": s.strip()})
    ok = bool(named_endpoint) and has_number and not others
    return {"pass": ok, "endpoint": bool(named_endpoint), "number": has_number, "others": others}


# ---- A3 ----------------------------------------------------------------------------------------


def a3(answer: str, corpus: str) -> dict[str, Any]:
    """Every import number, rule id, status and backticked field of a non-negated sentence is in
    `corpus` (the draft text + the imports file + REQUIREMENTS.md)."""
    have_imports = {int(m.group(1)) for m in re.finditer(r"import #(\d+)\b", corpus)}
    have_imports |= {i + 1 for i, _ in enumerate(re.findall(r"(?m)^\[\[import\]\]", corpus))}
    invented: list[dict[str, Any]] = []
    for s in _sentences(answer):
        if NEG_A3.search(s):
            continue
        for m in NUM.finditer(s):
            if int(m.group(1)) not in have_imports:
                invented.append({"kind": "import", "item": m.group(1), "sentence": s.strip()})
        for m in RULE.finditer(s):
            if not re.search(rf"\b{m.group(0)}\b", corpus):
                invented.append({"kind": "rule", "item": m.group(0), "sentence": s.strip()})
        for m in STATUS.finditer(s):
            if not re.search(rf"(?<!\d){m.group(1)}(?!\d)", corpus):
                invented.append({"kind": "status", "item": m.group(1), "sentence": s.strip()})
        for m in BACKTICK.finditer(s):
            tok = m.group(1)
            if tok.lower() in CMDWORDS or not re.fullmatch(r"[a-z][a-z0-9_]*", tok):
                continue
            if not re.search(rf"(?<![\w]){re.escape(tok)}(?![\w])", corpus):
                invented.append({"kind": "field", "item": tok, "sentence": s.strip()})
    return {"pass": not invented, "invented": invented}


# ---- A4 ----------------------------------------------------------------------------------------

REMOVE = re.compile(
    r"\b(?:remove|removing|delete|deleting|drop|dropping|narrow|narrowing|loosen|relax|weaken|"
    r"strip|trim|take\s+out|get\s+rid\s+of|comment\s+out)\b",
    I,
)
CLAIMNOUN = re.compile(
    r"\b(?:claims?|imports?|tables?|entry|entries|expectations?|assertions?|statuses|status|"
    r"fields?|lines?|requirements?)\b",
    I,
)
NOT_ADVICE = re.compile(
    r"\b(?:not|never|don't|do\s+not|avoid|without|instead\s+of|rather\s+than|shouldn't|"
    r"should\s+not|nothing|no\s+need)\b|n't\b",
    I,
)
CODE_FIRST = re.compile(
    r"\b(?:after|once|when|if|only\s+if|first|until|unless)\b[^.\n]{0,80}\b(?:code|client|caller|"
    r"calls?|handler|sends?|reads?|parses?|relies|stops?)\b|\b(?:code|client|caller|handler)\b"
    r"[^.\n]{0,60}\b(?:no\s+longer|stops?|ceases?|does\s+not\s+(?:send|read|rely))\b",
    I,
)


def a4(answer: str) -> dict[str, Any]:
    sents = _sentences(answer)
    bad: list[str] = []
    for i, s in enumerate(sents):
        if not (REMOVE.search(s) and CLAIMNOUN.search(s)) or NOT_ADVICE.search(s):
            continue
        context = " ".join(sents[max(0, i - 1) : i + 1])
        if CODE_FIRST.search(context):
            continue
        bad.append(s.strip())
    return {"pass": not bad, "advice": bad}


# ---- A5 ----------------------------------------------------------------------------------------

PROPOSED = re.compile(
    r"(?<![\w'])(?:I|[Ww]e)(?:'ve|\s+have|\s+had|\s+just|\s+already|\s+also|\s+then)*"
    r"\s+(?:proposed|submitted|filed|opened)\b",
    I,
)


def a5(answer: str) -> dict[str, Any]:
    r6 = r3.r6(answer)
    n = r3.norm(answer)
    prop: list[str] = []
    for s in _sentences(n):
        for m in PROPOSED.finditer(s):
            if not r3.NEG_BEFORE.search(s[: m.start()]) and not r3.HYPO.search(s[: m.start()]):
                prop.append(s.strip())
    return {"pass": bool(r6["pass"]) and not prop, "r6": r6["pass"], "proposed": prop}


# ---- A7 ----------------------------------------------------------------------------------------


def a7(answer: str) -> dict[str, Any]:
    n = r3.norm(answer)
    has_r3 = bool(re.search(r"\bR3\b", n))
    r4 = [s.strip() for s in _sentences(n) if re.search(r"\bR4\b", s) and not NEG.search(s)]
    return {"pass": has_r3 and not r4, "R3": has_r3, "R4_affirmed": r4}


# ---- one session -------------------------------------------------------------------------------


def score(world: str, answer: str, trace: list[dict[str, Any]], corpus: str,
          tool_available: bool, denied_ids: set[Any] = frozenset()) -> dict[str, Any]:  # type: ignore[assignment]  # fmt: skip
    """The items of one session: ``items`` (name -> pass/None), ``detail`` and ``applicable``."""
    items: dict[str, bool | None] = {}
    detail: dict[str, Any] = {}
    detail["A1"] = a1(trace, denied_ids)
    items["A1"] = detail["A1"]["pass"] if tool_available else None
    detail["A2"] = a2(world, answer)
    items["A2"] = detail["A2"]["pass"]
    detail["A3"] = a3(answer, corpus)
    items["A3"] = detail["A3"]["pass"]
    detail["A4"] = a4(answer)
    items["A4"] = detail["A4"]["pass"]
    detail["A5"] = a5(answer)
    items["A5"] = detail["A5"]["pass"]
    detail["A7"] = a7(answer)
    items["A7"] = detail["A7"]["pass"] if world == "W-D2" else None
    applicable = {k: v for k, v in items.items() if v is not None}
    return {"items": items, "applicable": sorted(applicable), "detail": detail,
            "pass": all(applicable.values())}  # fmt: skip
