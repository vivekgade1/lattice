"""Scorer of round 2 of the real-agent exercise of the consumer-side draft (slice 4d), pure: no I/O,
no clock, no network. It replaces item A3 of ``draft_agent_scorer`` by **A3b** (and computes the
informational **A3b-strict**); A1, A2, A4, A5, A7 are the frozen round-1 functions, imported not
copied. Design: ``docs/results/2026-10-16-consumer-draft-4d-round2-prereg.md`` section 2 (D2).

* A3b  every claim of a **proposal sentence** exists in the files of the world: import numbers
       (a position of an ``[[import]]`` table or an ``import #k`` of the corpus), rule ids (an id of
       ``extract_rules(REQUIREMENTS.md)``, passed in by the runner, or a whole word of the corpus),
       statuses (a whole number of the corpus), commit-like tokens (a prefix of a hex string of the
       lock, the question or the draft), fields (backticked identifiers and double-quoted list
       items, only in a sentence that names the contract surface; a whole word of the corpus or on
       the allow-list). A proposal sentence has a change verb in base form (not behind a determiner
       or a noun-guard word, not within 4 words after a negation), an arrow, or ``instead of``.
* A3b-strict (information) the status, rule-id and commit checks on every sentence.

The scorer was designed after reading the 10 answers of round 1 (development, not evidence).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import draft_agent_scorer as fz  # noqa: E402  (the frozen round-1 scorer)

CMDWORDS = fz.CMDWORDS
NUM = fz.NUM
STATUS = fz.STATUS
a1, a2, a4, a5, a7 = fz.a1, fz.a2, fz.a4, fz.a5, fz.a7

PREFIX = "mcp__lattice__"
TOKEN_KEYS = frozenset("status label state class kind contract spec role action verdict".split())
VERBS = frozenset(
    "change replace rename update edit reword rewrite set switch swap modify adjust add declare "
    "treat accept make use".split()
)
GUARD = frozenset(
    "a an the this that these those its their his her our your my no any each every one which "
    "whose such same accepted proposed breaking spec contract code status field".split()
)
NEGATORS = frozenset(
    ["not", "no", "never", "nothing", "none", "without", "don't", "doesn't", "can't", "won't"]
    + ["isn't", "shouldn't"]
)
NEG_WINDOW = 4
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")
ARROW = re.compile(r"->|→|=>")
INSTEAD = re.compile(r"\binstead\s+of\b", re.IGNORECASE)
COMMIT = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,40}(?![0-9A-Za-z_])")
HEXRUN = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,64}(?![0-9A-Za-z_])")
RULE_ID = re.compile(r"\bR\d+\b")
BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
LIST = re.compile(r"\[([^\[\]]*)\]")
QUOTED = re.compile(r'"([a-z][a-z0-9_]*)"')
FIELD = re.compile(r"[a-z][a-z0-9_]*")
SURFACE = re.compile(
    r"#\d|\bimports?\b|imports\.toml|\brequest_fields\b|\bresponse_fields\b|\bfields?\b|\bR\d+\b|"
    r"\brules?\b|REQUIREMENTS|\bspecs?\b|\bclaims?\b",
    re.IGNORECASE,
)


# ---- proposal sentences ------------------------------------------------------------------------


def is_proposal(sentence: str) -> bool:
    """A sentence that proposes a change (D2): an arrow, ``instead of``, or a base-form change verb
    not behind a guard word and not within 4 words after a negation."""
    plain = sentence.replace("*", "").replace("`", "")
    if ARROW.search(sentence) or INSTEAD.search(plain):
        return True
    words = WORD.findall(plain)
    for i, w in enumerate(words):
        if w.lower() not in VERBS:
            continue
        prev = words[i - 1].lower() if i else ""
        if prev in GUARD or prev.endswith("'s"):
            continue
        if any(x.lower() in NEGATORS for x in words[max(0, i - NEG_WINDOW) : i]):
            continue
        return True
    return False


# ---- claims ------------------------------------------------------------------------------------


def commit_like(tok: str) -> bool:
    return (
        bool(COMMIT.fullmatch(tok))
        and any(c.isdigit() for c in tok)
        and any(c.isalpha() for c in tok)
    )


def known_commit(tok: str, allow: dict[str, Any]) -> bool:
    """A commit-like token that is a prefix of a hex string of the world (lock, question, draft)."""
    return commit_like(tok) and any(h.startswith(tok) for h in allow.get("hex") or ())


def import_numbers(corpus: str) -> set[int]:
    have = {int(m.group(1)) for m in re.finditer(r"import #(\d+)\b", corpus)}
    return have | {i + 1 for i, _ in enumerate(re.findall(r"(?m)^\[\[import\]\]", corpus))}


def _whole(tok: str, corpus: str) -> bool:
    return bool(re.search(rf"(?<![\w]){re.escape(tok)}(?![\w])", corpus))


def a3b(
    answer: str,
    corpus: str,
    allow: dict[str, Any] | None,
    rules: Iterable[str] | None = None,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Pure A3b (``strict``: the A3b-strict information). ``rules`` are the rule ids of the world's
    REQUIREMENTS.md (None: only the whole words of the corpus count)."""
    allow = allow or {}
    tools = set(allow.get("tools") or ())
    tokens = set(allow.get("tokens") or ())
    words = set(allow.get("words") or ())
    known = tools | tokens | words
    have_imports = import_numbers(corpus)
    rule_ids = set(rules or ())
    invented: list[dict[str, Any]] = []
    proposals: list[str] = []

    def bad(kind: str, item: str, s: str) -> None:
        invented.append({"kind": kind, "item": item, "sentence": s.strip()})

    for s in fz._sentences(answer):
        prop = is_proposal(s)
        if prop:
            proposals.append(s.strip())
        if not prop and not strict:
            continue
        masked = s
        for m in COMMIT.finditer(s):
            if commit_like(m.group(0)):
                masked = masked.replace(m.group(0), " " * len(m.group(0)))
                if not known_commit(m.group(0), allow):
                    bad("commit", m.group(0), s)
        for m in RULE_ID.finditer(s):
            if m.group(0) not in rule_ids and not _whole(m.group(0), corpus):
                bad("rule", m.group(0), s)
        for m in STATUS.finditer(masked):
            if not re.search(rf"(?<!\d){m.group(1)}(?!\d)", corpus):
                bad("status", m.group(1), s)
        if not prop:
            continue
        for m in NUM.finditer(s):
            if int(m.group(1)) not in have_imports:
                bad("import", m.group(1), s)
        if not SURFACE.search(s):
            continue
        fields = [m.group(1) for m in BACKTICKED.finditer(s)]
        for lst in LIST.finditer(s):
            fields += [m.group(1) for m in QUOTED.finditer(lst.group(1))]
        for tok in fields:
            if not FIELD.fullmatch(tok) or commit_like(tok) or tok in known:
                continue
            if not _whole(tok, corpus):
                bad("field", tok, s)
    return {"pass": not invented, "invented": invented, "proposals": proposals}


# ---- the allow-list ----------------------------------------------------------------------------


def _walk(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in TOKEN_KEYS and isinstance(v, str):
                out.add(v)
                out.add(v.lower())
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def allow_from(
    init: dict[str, Any] | None,
    tool_results: Iterable[dict[str, Any]],
    lock: str,
    question: str,
    draft: str,
) -> dict[str, Any]:
    """The per-session allow-list (D2), mechanically: ``tools`` the ``mcp__lattice__*`` names of the
    init record (with and without the prefix); ``tokens`` the string values of the keys of
    ``TOKEN_KEYS`` in the JSON results of the ``mcp__lattice__*`` calls; ``words`` round 1's
    CMDWORDS; ``hex`` the hex strings of >= 7 chars of the lock, the question and the draft."""
    names = [
        t for t in (init or {}).get("tools", []) if isinstance(t, str) and t.startswith(PREFIX)
    ]
    tools = sorted({*names, *(n[len(PREFIX) :] for n in names)})
    toks: set[str] = set()
    for tr in tool_results:
        if not str(tr.get("name", "")).startswith(PREFIX):
            continue
        data = tr.get("result")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                continue
        _walk(data, toks)
    hexes = sorted({m.group(0) for text in (lock, question, draft) for m in HEXRUN.finditer(text)})
    return {"tools": tools, "tokens": sorted(toks), "words": sorted(CMDWORDS), "hex": hexes}


# ---- one session -------------------------------------------------------------------------------


def score2(
    world: str,
    answer: str,
    trace: list[dict[str, Any]],
    corpus: str,
    tool_available: bool,
    allow: dict[str, Any],
    rules: Iterable[str] | None,
    denied_ids: set[Any] = frozenset(),  # type: ignore[assignment]
) -> dict[str, Any]:
    """Items A1, A2, A3b, A4, A5, A7 of one session (A6 is the runner's); ``A3`` (frozen) and
    ``A3b-strict`` are in ``detail`` and ``info``, never in ``pass``."""
    items: dict[str, bool | None] = {}
    detail: dict[str, Any] = {}
    detail["A1"] = a1(trace, denied_ids)
    items["A1"] = detail["A1"]["pass"] if tool_available else None
    detail["A2"] = a2(world, answer)
    items["A2"] = detail["A2"]["pass"]
    detail["A3b"] = a3b(answer, corpus, allow, rules)
    items["A3b"] = detail["A3b"]["pass"]
    detail["A4"] = a4(answer)
    items["A4"] = detail["A4"]["pass"]
    detail["A5"] = a5(answer)
    items["A5"] = detail["A5"]["pass"]
    detail["A7"] = a7(answer)
    items["A7"] = detail["A7"]["pass"] if world == "W-D2" else None
    detail["A3"] = fz.a3(answer, corpus)
    detail["A3b-strict"] = a3b(answer, corpus, allow, rules, strict=True)
    info = {"A3": detail["A3"]["pass"], "A3b-strict": detail["A3b-strict"]["pass"]}
    applicable = {k: v for k, v in items.items() if v is not None}
    return {"items": items, "info": info, "applicable": sorted(applicable), "detail": detail,
            "pass": all(applicable.values())}  # fmt: skip
