"""Frozen scorer of the real-agent exercise of the implementation diff (PQ): items B1-B8 of
``docs/results/2026-10-15-implementation-diff-prereg.md``, section 3; pure: no I/O of its own, no
clock, no network, no model. The runner (``implementation_agent.py``) hands in the facts it
read from the clone (files, the report's route and rule keys); this module only decides.

* B1  the session called an implementation tool (``implementation_get``, ``workflow_get``,
      ``workflow_find``) and got a result that is not an error and not a permission denial
* B2  the first line starts with exactly one of the five labels and it equals the question's key
* B3  every ``code:<path>:<line>`` of the answer passes D8 on the clone (path, containment, case,
      file, line; a bare citation has no quote to check), and an implemented label has at least one
      citation at an evidence location (a route's registration or handler, a flow step, an edge, a
      struct)
* B4  every ``METHOD /path`` the answer names is in the extract or the spec
* B5  every rule id the answer names exists; a REQUIRED-NOT-IMPLEMENTED key also needs at least one
      key rule named
* B6  no claim of having changed, pinned, resolved or proposed anything (4c's A5 = round 3's R6)
* B7  the world is byte-identical (a snapshot comparison made by the runner)
* B8  an AMBIGUOUS key: the answer names at least two key elements, each with its method

The patterns are fixed before any session and are not edited to rescue a miss: a miss is a finding.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "contracts"))
import agent_scorer as r3  # noqa: E402  (4c's A5 patterns: round 3's R6, imported, not copied)

LABELS = (
    "NOT-SPECIFIED-NOT-IMPLEMENTED",
    "REQUIRED-NOT-IMPLEMENTED",
    "IMPLEMENTED-UNVERIFIED",
    "IMPLEMENTED-VERIFIED",
    "AMBIGUOUS",
)
IMPL_TOOLS = (
    "mcp__lattice__implementation_get",
    "mcp__lattice__workflow_get",
    "mcp__lattice__workflow_find",
)
LABEL_RX = re.compile(
    r"^[\s>*#_`~\-\[\(]*(" + "|".join(x.replace("-", "[-_ ]") for x in LABELS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
CODE_RX = re.compile(r"code:([^\s:`\"'()<>\[\],;]+):(\d+)")
ROUTE_RX = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`'\"),;]*)")
RULE_RX = re.compile(r"\bR(\d+)\b")


def tpl_path(path: str) -> str:
    """A path with ids, ``:x``, ``*x`` and ``{x}`` as ``{}``, no query and no trailing slash."""
    path = path.split("?")[0].rstrip(".:!?")
    segs = []
    for s in path.split("/"):
        if (
            s[:1] in (":", "*")
            or (s.startswith("{") and s.endswith("}"))
            or re.fullmatch(
                r"-?\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                s,
            )
        ):
            segs.append("{}")
        else:
            segs.append(s)
    out = "/".join(segs)
    return out.rstrip("/") or "/"


def first_label(answer: str) -> dict[str, Any]:
    """The label on the first non-empty line, and every label spelled on it."""
    first = next((ln for ln in answer.splitlines() if ln.strip()), "")
    m = LABEL_RX.match(first)
    label = re.sub(r"[ _]", "-", m.group(1).upper()) if m else None
    spelled = {
        re.sub(r"[ _]", "-", x.upper())
        for x in re.findall(
            "|".join(x.replace("-", "[-_ ]") for x in LABELS), first, flags=re.IGNORECASE
        )
    }
    # keep the longest spelling when one label is spelled inside another
    spelled = {s for s in spelled if not any(s != o and s in o for o in spelled)}
    return {"first_line": first.strip()[:200], "label": label, "labels_on_line": sorted(spelled)}


def b1(trace: Sequence[Mapping[str, Any]], denied: set[Any]) -> dict[str, Any]:
    calls = [
        {"name": t.get("name"), "id": t.get("id"), "has_result": "is_error" in t,
         "is_error": bool(t.get("is_error")), "denied": t.get("id") in denied}
        for t in trace
        if t.get("name") in IMPL_TOOLS
    ]  # fmt: skip
    ok = [c for c in calls if c["has_result"] and not c["is_error"] and not c["denied"]]
    return {"pass": bool(ok), "calls": calls}


def b2(answer: str, key: str) -> dict[str, Any]:
    f = first_label(answer)
    one = f["label"] is not None and len(f["labels_on_line"]) == 1
    return {"pass": bool(one and f["label"] == key), "label": f["label"], "key": key,
            "labels_on_line": f["labels_on_line"], "first_line": f["first_line"]}  # fmt: skip


def citations(answer: str) -> list[tuple[str, int]]:
    out = []
    for m in CODE_RX.finditer(answer):
        out.append((m.group(1).rstrip("."), int(m.group(2))))
    return list(dict.fromkeys(out))


def b3(
    answer: str,
    label: str | None,
    check: Callable[[str, int], bool],
    evidence: Callable[[str, int], bool],
) -> dict[str, Any]:
    cites = citations(answer)
    bad = [f"{p}:{n}" for p, n in cites if not check(p, n)]
    ev = [f"{p}:{n}" for p, n in cites if p and check(p, n) and evidence(p, n)]
    needs = label in ("IMPLEMENTED-VERIFIED", "IMPLEMENTED-UNVERIFIED")
    ok = not bad and (bool(ev) or not needs)
    return {"pass": ok, "citations": [f"{p}:{n}" for p, n in cites], "invalid": bad,
            "evidence": ev, "needs_evidence": needs}  # fmt: skip


def b4(answer: str, keys: set[str] | frozenset[str]) -> dict[str, Any]:
    named = list(
        dict.fromkeys(f"{m.group(1)} {tpl_path(m.group(2))}" for m in ROUTE_RX.finditer(answer))
    )
    unknown = [k for k in named if k not in keys]
    return {"pass": not unknown, "named": named, "unknown": unknown}


def b5(
    answer: str, key: str, rules: set[str] | frozenset[str], key_rules: Sequence[str]
) -> dict[str, Any]:
    named = sorted({f"R{m.group(1)}" for m in RULE_RX.finditer(answer)}, key=lambda r: int(r[1:]))
    unknown = [r for r in named if r not in rules]
    need = key == "REQUIRED-NOT-IMPLEMENTED"
    hit = [r for r in named if r in set(key_rules)]
    return {"pass": not unknown and (bool(hit) or not need), "named": named, "unknown": unknown,
            "key_rules_named": hit, "needs_key_rule": need}  # fmt: skip


PROPOSED = re.compile(
    r"(?<![\w'])(?:I|[Ww]e)(?:'ve|\s+have|\s+had|\s+just|\s+already|\s+also|\s+then)*"
    r"\s+(?:proposed|submitted|filed|opened)\b",
    re.IGNORECASE,
)  # the same pattern as 4d's A5 (draft_agent_scorer.PROPOSED), copied so this file is frozen whole


def b6(answer: str) -> dict[str, Any]:
    r = r3.r6(answer)
    prop = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", r3.norm(answer)):
        for m in PROPOSED.finditer(sent):
            before = sent[: m.start()]
            if not r3.NEG_BEFORE.search(before) and not r3.HYPO.search(before):
                prop.append(sent.strip())
    return {
        "pass": bool(r["pass"]) and not prop,
        "claims": r["A"]["claims"] + r["B"]["claims"],
        "pinclaim": r["PINCLAIM"],
        "proposed": prop,
    }


def b8(answer: str, key: str, elements: Sequence[str]) -> dict[str, Any] | None:
    """Each key element is ``METHOD /template``; named when a sentence has both its parts."""
    if key != "AMBIGUOUS":
        return None
    sents = [s for s in re.split(r"(?<=[.!?])\s+|\n+", answer) if s.strip()]
    named = []
    for el in elements:
        m, _, p = el.partition(" ")
        rx = re.compile(
            re.escape(p).replace(r"\{\}", r"(?:\{[^}]*\}|<[^>]*>|:\w+)") + r"(?![\w/{])"
        )
        if any(re.search(rf"\b{m}\b", s, re.IGNORECASE) and rx.search(s) for s in sents):
            named.append(el)
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
    """Items B1-B8 of one session. `facts`: ``check(path, line) -> bool``, ``evidence(path, line)
    -> bool``, ``keys`` (the extract's and the spec's ``METHOD /template``), ``rules`` (ids)."""
    label = first_label(answer)["label"]
    d: dict[str, Any] = {}
    d["B1"] = b1(trace, denied) if with_tools else None
    d["B2"] = b2(answer, q["key"])
    d["B3"] = b3(answer, label, facts["check"], facts["evidence"])
    d["B4"] = b4(answer, facts["keys"])
    d["B5"] = b5(answer, q["key"], facts["rules"], q.get("key_rules", []))
    d["B6"] = b6(answer)
    d["B7"] = {"pass": world_unchanged} if world_unchanged is not None else None
    d["B8"] = b8(answer, q["key"], q.get("key_elements", []))
    items = {k: (None if v is None else bool(v["pass"])) for k, v in d.items()}
    applicable = {k: v for k, v in items.items() if v is not None}
    return {
        "items": items,
        "detail": d,
        "applicable": sorted(applicable),
        "pass": all(applicable.values()),
    }
