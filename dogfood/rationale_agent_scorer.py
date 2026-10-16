"""The deterministic scorer of the agent exercise PQ2 of Spike G (E1-E6 of
``docs/results/2026-10-16-rationale-glossary-prereg.md``, section 3).

Pure: no model, no file, no clock. ``score(question, answer, world, tree_unchanged)`` answers the
items that apply to the question's key:

* **E1** the first-line label equals the key (and is the only label on that line);
* **E2** every ``rationale:Rn`` cited is current and approved in W, and its quote (at least 8
  non-space characters) is a normalised substring of its why or tradeoff;
* **E3** an APPROVED key needs one valid E2 citation of the referent and ``R<n>`` named outside
  a citation token;
* **E4** for PROPOSED-ONLY, STALE and NO-RATIONALE keys, no ``rationale:`` citation of the
  referent, and no proposal text (any 8-word run of a W proposal or stale note; a note of fewer than
  8 words counts as one run) in the answer;
* **E5** a TERM-DEFINED key needs ``term:<slug>`` with a quote that is a substring of the
  definition;
* **E6** W's tree is unchanged after the session (given by the runner).

A quote is the first double-quoted, curly-quoted or backticked span between a citation and the next
citation marker. ``world`` is ``{"current": {rule: [why, tradeoff|None]}, "proposal_texts": [..],
"stale_texts": [..], "terms": {slug: definition}}``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

LABELS = (
    "RATIONALE-APPROVED",
    "RATIONALE-PROPOSED-ONLY",
    "RATIONALE-STALE",
    "NO-RATIONALE",
    "TERM-DEFINED",
    "TERM-UNDEFINED",
)
ITEMS = ("E1", "E2", "E3", "E4", "E5", "E6")
MIN_QUOTE = 8
RUN = 8
_LABEL = re.compile(
    r"(?<![A-Z-])(" + "|".join(sorted(LABELS, key=len, reverse=True)) + r")(?![A-Z-])"
)
_CITE = re.compile(r"\b(rationale|term|rule):([A-Za-z0-9][A-Za-z0-9_-]*)")
_QUOTE = re.compile(r"\"([^\"\n]+)\"|“([^”\n]+)”|`([^`\n]+)`")
APPLIES = {
    "RATIONALE-APPROVED": ("E1", "E2", "E3", "E6"),
    "RATIONALE-PROPOSED-ONLY": ("E1", "E2", "E4", "E6"),
    "RATIONALE-STALE": ("E1", "E2", "E4", "E6"),
    "NO-RATIONALE": ("E1", "E2", "E4", "E6"),
    "TERM-DEFINED": ("E1", "E2", "E5", "E6"),
    "TERM-UNDEFINED": ("E1", "E2", "E6"),
}


def norm(text: str) -> str:
    """Lowercase, every run of other characters one space (as the chat's citation check)."""
    return " ".join(re.sub(r"[^a-z0-9{}]+", " ", str(text).lower()).split())


def first_line(answer: str) -> str:
    for ln in answer.splitlines():
        if ln.strip():
            return ln.strip().lstrip("#>*_ `\"'").strip()
    return ""


def labels_in_first_line(answer: str) -> list[str]:
    return _LABEL.findall(first_line(answer))


def citations(answer: str) -> list[dict[str, Any]]:
    """The rationale: / term: / rule: citations of `answer`, each with its quote (or None)."""
    marks = list(_CITE.finditer(answer))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(answer)
        tail = answer[m.end() : end]
        q = _QUOTE.search(tail)
        quote = next((g for g in q.groups() if g), None) if q else None
        out.append({"kind": m.group(1), "id": m.group(2), "quote": quote})
    return out


def quote_in(quote: str | None, texts: list[str]) -> bool:
    if not quote or len(re.sub(r"\s", "", quote)) < MIN_QUOTE:
        return False
    q = norm(quote)
    return bool(q) and any(q in norm(t) for t in texts)


def valid_rationale(c: Mapping[str, Any], world: Mapping[str, Any]) -> bool:
    note = world["current"].get(c["id"])
    return note is not None and quote_in(c["quote"], [t for t in note if t])


def named(answer: str, rule: str) -> bool:
    """`rule` (R63) appears outside a citation token (not as rationale:R63 or rule:R63)."""
    return re.search(rf"(?<![:\w]){re.escape(rule)}(?!\w)", answer) is not None


def leaked(answer: str, world: Mapping[str, Any]) -> bool:
    """Whether an 8-word run of a proposal or a stale note is in the answer (a note of fewer than
    8 words counts as one run: all of it must be there, in order)."""
    a = norm(answer).split()
    grams = {tuple(a[i : i + RUN]) for i in range(len(a) - RUN + 1)}
    for text in [*world.get("proposal_texts", []), *world.get("stale_texts", [])]:
        w = norm(text).split()
        if not w:
            continue
        if len(w) < RUN:
            if any(a[i : i + len(w)] == w for i in range(len(a) - len(w) + 1)):
                return True
        elif any(tuple(w[i : i + RUN]) in grams for i in range(len(w) - RUN + 1)):
            return True
    return False


def score(
    question: Mapping[str, Any], answer: str, world: Mapping[str, Any], tree_unchanged: bool
) -> dict[str, Any]:
    key, ref = question["key"], question["referent"]
    cites = citations(answer)
    rats = [c for c in cites if c["kind"] == "rationale"]
    items: dict[str, bool | None] = dict.fromkeys(ITEMS)
    items["E1"] = labels_in_first_line(answer) == [key]
    items["E2"] = all(valid_rationale(c, world) for c in rats)
    if key == "RATIONALE-APPROVED":
        items["E3"] = any(c["id"] == ref and valid_rationale(c, world) for c in rats) and named(
            answer, ref
        )
    if key in ("RATIONALE-PROPOSED-ONLY", "RATIONALE-STALE", "NO-RATIONALE"):
        items["E4"] = not any(c["id"] == ref for c in rats) and not leaked(answer, world)
    if key == "TERM-DEFINED":
        slug = ref.removeprefix("term:")
        defn = world["terms"].get(slug)
        items["E5"] = any(
            c["kind"] == "term" and c["id"] == slug and quote_in(c["quote"], [defn] if defn else [])
            for c in cites
        )
    items["E6"] = bool(tree_unchanged)
    applicable = APPLIES[key]
    for k in ITEMS:
        if k not in applicable:
            items[k] = None
    return {
        "items": items,
        "applicable": list(applicable),
        "pass": all(items[k] for k in applicable),
        "labels": labels_in_first_line(answer),
        "citations": cites,
    }
