"""Frozen scorer of agent exercise 2 (PQ2): items C1-C8 of
``docs/results/2026-10-16-implementation-diff-round2-prereg.md``, D8; pure: no I/O of its own, no
clock, no network, no model. The runner hands in the facts it read from the clone.

Round 1's scorer (``implementation_agent_scorer.py``, frozen) is imported, not edited:

* C1 = B1  the session called an implementation tool and got a result that is not an error
* C2 = B2  the first line has exactly one of the five labels and it equals the question's key
* C3 = B3  every ``code:<path>:<line>`` passes D8 on the clone; an implemented label cites evidence
* C4       every named ``METHOD /path`` is an extract or spec key, or is group-relative and
           derivable: exactly one key with that method ends with the named path at a segment
           boundary among the keys whose registration ``code:<file>:<line>`` the answer cites
* C5       every ``R<n>`` exists; a REQUIRED-NOT-IMPLEMENTED key needs a key rule named as ``R<n>``
           or cited as ``<trace source>:<line>`` equal to that rule's trace ``line``
* C6 = B6  no claim of having changed, pinned, resolved or proposed anything
* C7 = B7  the world is byte-identical (the runner's snapshot comparison)
* C8       an AMBIGUOUS key: at least two key elements named, each with its method in one
           sentence, in full or C4-derivable

The patterns are fixed before any session and are not edited to rescue a miss.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import implementation_agent_scorer as S1  # noqa: E402  (round 1's scorer: frozen, imported)

LABELS = S1.LABELS
IMPL_TOOLS = S1.IMPL_TOOLS
ITEMS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
first_label = S1.first_label
citations = S1.citations


def c2(answer: str, key: str) -> dict[str, Any]:
    """The first line has exactly one label and it is the key (round 1's B2, unchanged): an
    AMBIGUOUS key is met only by AMBIGUOUS, even when every candidate route is implemented."""
    return S1.b2(answer, key)


def named_routes(answer: str) -> list[tuple[str, str]]:
    """Every ``METHOD /path`` the answer names, the path as a template."""
    out = [(m.group(1), S1.tpl_path(m.group(2))) for m in S1.ROUTE_RX.finditer(answer)]
    return list(dict.fromkeys(out))


def cited_keys(answer: str, reg: Mapping[str, Sequence[Any]]) -> list[str]:
    """The keys whose registration (extract ``file``, ``line``) the answer cites."""
    cites = set(citations(answer))
    return sorted(k for k, (f, n) in reg.items() if (f, int(n)) in cites)


def derive(method: str, path: str, keys: set[str], cited: Sequence[str]) -> str | None:
    """The key a named ``METHOD /path`` stands for: itself when it is a key, else the one cited
    key with that method whose path ends with it at a segment boundary (``path`` starts with
    ``/``, so a suffix match is on a boundary); None when there is not exactly one."""
    full = f"{method} {path}"
    if full in keys:
        return full
    hits = []
    for k in cited:
        m, _, p = k.partition(" ")
        if m == method and p.endswith(path) and path.startswith("/"):
            hits.append(k)
    return hits[0] if len(hits) == 1 else None


def c4(
    answer: str, keys: set[str] | frozenset[str], reg: Mapping[str, Sequence[Any]]
) -> dict[str, Any]:
    cited = cited_keys(answer, reg)
    named = named_routes(answer)
    resolved: dict[str, str | None] = {}
    for m, p in named:
        resolved[f"{m} {p}"] = derive(m, p, set(keys), cited)
    unknown = [k for k, v in resolved.items() if v is None]
    return {
        "pass": not unknown,
        "named": [f"{m} {p}" for m, p in named],
        "derived": {k: v for k, v in resolved.items() if v is not None and v != k},
        "unknown": unknown,
        "registrations_cited": cited,
    }


def c5(
    answer: str,
    key: str,
    rules: set[str] | frozenset[str],
    key_rules: Sequence[str],
    rule_lines: Mapping[str, int],
    source: str,
) -> dict[str, Any]:
    named = sorted(
        {f"R{m.group(1)}" for m in S1.RULE_RX.finditer(answer)}, key=lambda r: int(r[1:])
    )
    unknown = [r for r in named if r not in rules]
    need = key == "REQUIRED-NOT-IMPLEMENTED"
    by_id = [r for r in named if r in set(key_rules)]
    base = re.escape(Path(source).name)
    lines = {int(m.group(1)) for m in re.finditer(rf"(?<![\w.-]){base}:(\d+)", answer)}
    by_line = sorted(r for r in key_rules if rule_lines.get(r) in lines)
    return {
        "pass": not unknown and (bool(by_id) or bool(by_line) or not need),
        "named": named,
        "unknown": unknown,
        "key_rules_named": by_id,
        "key_rules_cited_by_line": by_line,
        "needs_key_rule": need,
    }


def c8(
    answer: str,
    key: str,
    elements: Sequence[str],
    reg: Mapping[str, Sequence[Any]],
) -> dict[str, Any] | None:
    """Each key element is ``METHOD /template``; named when one sentence has its method and its
    path, in full or as a group-relative path C4 can derive to exactly that element."""
    if key != "AMBIGUOUS":
        return None
    cited = cited_keys(answer, reg)
    sents = [s for s in re.split(r"(?<=[.!?])\s+|\n+", answer) if s.strip()]
    named = []
    for el in elements:
        m, _, p = el.partition(" ")
        full = re.compile(
            re.escape(p).replace(r"\{\}", r"(?:\{[^}]*\}|<[^>]*>|:\w+)") + r"(?![\w/{])"
        )
        for s in sents:
            if not re.search(rf"\b{m}\b", s, re.IGNORECASE):
                continue
            if full.search(s):
                named.append(el)
                break
            rel = [(mm.group(1), S1.tpl_path(mm.group(2))) for mm in S1.ROUTE_RX.finditer(s)]
            if any(rm == m and derive(rm, rp, {el}, cited) == el for rm, rp in rel):
                named.append(el)
                break
    return {"pass": len(named) >= 2, "named": named, "of": len(elements)}


def score(
    q: Mapping[str, Any],
    answer: str,
    trace: Sequence[Mapping[str, Any]],
    facts: Mapping[str, Any],
    with_tools: bool,
    denied: set[Any] = frozenset(),  # type: ignore[assignment]
    world_unchanged: bool | None = None,
) -> dict[str, Any]:
    """Items C1-C8 of one session. `facts`: ``check(path, line)``, ``evidence(path, line)``,
    ``keys`` (extract and spec ``METHOD /template``), ``rules`` (ids), ``reg`` (key -> (file,
    line) of its registration), ``rule_lines`` (id -> trace line), ``trace_source``."""
    label = first_label(answer)["label"]
    d: dict[str, Any] = {}
    d["C1"] = S1.b1(trace, denied) if with_tools else None
    d["C2"] = c2(answer, q["key"])
    d["C3"] = S1.b3(answer, label, facts["check"], facts["evidence"])
    d["C4"] = c4(answer, facts["keys"], facts["reg"])
    d["C5"] = c5(
        answer, q["key"], facts["rules"], q.get("key_rules", []), facts["rule_lines"],
        facts["trace_source"],
    )  # fmt: skip
    d["C6"] = S1.b6(answer)
    d["C7"] = {"pass": world_unchanged} if world_unchanged is not None else None
    d["C8"] = c8(answer, q["key"], q.get("key_elements", []), facts["reg"])
    items = {k: (None if v is None else bool(v["pass"])) for k, v in d.items()}
    applicable = {k: v for k, v in items.items() if v is not None}
    return {
        "items": items,
        "detail": d,
        "applicable": sorted(applicable),
        "pass": all(applicable.values()),
    }
