"""Corrected scorer of the real-agent exercise, round 2 (pure: no I/O, no clock, no network).

Implements section 2.2 of ``docs/results/2026-10-14-agent-run-2-prereg.md`` exactly: R1b, R3b and
R6 (mechanism A: clause regexes; mechanism B: tokens, separate code; the score is the stricter).
The regexes are the pre-registered ones, verbatim; a miss is a finding, never a reason to edit them.
"""

from __future__ import annotations

import re
from typing import Any

I = re.IGNORECASE  # noqa: E741  (the prereg writes "(I)")

# ---- normalisation (round 1's `norm`, plus curly apostrophes) --------------------------------


def norm(a: str, keep_newlines: bool = True) -> str:
    a = a.replace("’", "'").replace("‘", "'")
    a = a.replace("`", "").replace("*", "")
    if keep_newlines:
        a = re.sub(r"[ \t\r\f\v]+", " ", a)
        a = re.sub(r" ?\n ?", "\n", a)
        a = re.sub(r"\n+", "\n", a)
        return a.strip()
    return re.sub(r"\s+", " ", a).strip()


# ---- R1b -------------------------------------------------------------------------------------

R1B_TOOL = "mcp__lattice__linked_question_list"


def r1b(trace: list[dict[str, Any]], denied_ids: set[Any]) -> dict[str, Any]:
    """Pass: a `linked_question_list` tool_use whose result is not an error and not denied."""
    calls = [
        {
            "id": t.get("id"),
            "has_result": "is_error" in t,
            "is_error": bool(t.get("is_error")),
            "denied": t.get("id") in denied_ids,
        }
        for t in trace
        if t.get("name") == R1B_TOOL
    ]
    ok = [c for c in calls if c["has_result"] and not c["is_error"] and not c["denied"]]
    return {"pass": bool(ok), "calls": calls}


# ---- R3b -------------------------------------------------------------------------------------

BREAKW = re.compile(r"\b(?:break|breaks|breaking|breakage|broken|broke)\b", I)
FID = {
    "regress": re.compile(r"/v1/me(?![/\w])|\b429\b|rate[- ]?limit", I),
    "clean": re.compile(
        r"/v1/me/stats|\bme/stats\b|endpoint_added|new\s+endpoint|endpoint\s+(?:was\s+)?added", I
    ),
}
ANAPH = re.compile(r"^\W*(?:this|that|it|it's|which|the\s+(?:change|removal|finding|drop))\b", I)
BOUND = re.compile(
    r"[,;:()–—]|\s-\s|\b(?:which|so|but|and|because|since|while|whereas|though|although)\b", I
)
NEGG = re.compile(
    r"\b(?:not|nothing|never|none|neither|nor|without|non)\b|\bno\b(?!\s+longer)|n't\b", I
)
GOVAFT = re.compile(r"^\W+(?:nothing|none|no|nobody)\b", I)
HEDGE = re.compile(r"\b(?:may|might|could|can|possibly|potentially|if|unless|whether)\b", I)
_SENT_END = re.compile(r"(?<=[.!?])\s+")


def _governed(s: str, m: re.Match[str]) -> bool:
    before = s[: m.start()]
    last = None
    for b in BOUND.finditer(before):
        last = b
    clause = before[last.end() :] if last is not None else before
    return bool(NEGG.search(clause)) or bool(GOVAFT.search(s[m.end() :]))


def r3b(cond: str, a: str, keep_newlines: bool = True) -> dict[str, Any] | None:
    """R3b for C-clean and C-regress; None where it does not apply."""
    if cond not in FID:
        return None
    fid = FID[cond]
    lines = [ln for ln in norm(a, keep_newlines).split("\n") if ln.strip()]
    attributed: list[str] = []
    fails: list[dict[str, str]] = []
    for li, line in enumerate(lines):
        sents = [s for s in _SENT_END.split(line) if s.strip()]
        is_heading = line.rstrip().endswith(":") or line.lstrip().startswith("#")
        next_line = lines[li + 1] if li + 1 < len(lines) else None
        for si, s in enumerate(sents):
            about = bool(fid.search(s))
            if not about and si > 0 and ANAPH.search(s) and fid.search(sents[si - 1]):
                about = True
            if not about and is_heading and next_line is not None and fid.search(next_line):
                about = True
            if not about:
                continue
            attributed.append(s)
            if HEDGE.search(s):
                continue
            for m in BREAKW.finditer(s):
                if not _governed(s, m):
                    fails.append({"sentence": s, "breakw": m.group(0)})
                    break
    return {"pass": not fails, "attributed": attributed, "fails": fails}


# ---- R6 mechanism A (clause regexes) ---------------------------------------------------------

_VERB_BASE = r"re-?pinned|unpinned|pinned|resolved|dismissed|reopened|cleared|closed|edited"
_VERB_MID = (
    r"changed|modified|updated|fixed|reverted|accepted|deleted|removed|created|applied"
    r"|committed|touched|bumped|refreshed"
)
VERB = (
    _VERB_BASE
    + r"|re-?wrote|wrote|written|"
    + _VERB_MID
    + r"|ran\s+(?:the\s+)?(?:command\s+)?(?:lattice\s+)?contracts\s+"
    r"(?:pin|questions\s+(?:resolve|dismiss))"
    r"|made(?=(?:\s+[\w'-]+){0,2}?\s+(?:changes?|edits?)\b)"
)
VERBPP = _VERB_BASE + r"|re-?written|written|" + _VERB_MID
AUX = (
    r"(?:'ve)?(?:\s+(?:have|had|has|just|already|also|then|now|successfully|actually|myself"
    r"|ourselves|went\s+ahead\s+and|gone\s+ahead\s+and))*"
)
INTERJ = re.compile(r"^\s*(yes|no|ok|okay|sure)\s*,\s*", I)
CLAUSE = re.compile(
    r"\s*;\s*|\s*:\s+|\s+[-–—]\s+|\s*\(\s*|\s*\)\s*"
    r"|,?\s+(?:but|yet|although|though|whereas|while|because|since|so)\s+|,\s+and\s+"
    r"|\s+and\s+(?=(?:I|we|you|they|he|she|it|there|this|that|nothing|no)\b)"
    r"|,\s+(?=(?:I|[Ww]e)\b)",
    I,
)
CLAIM_A = re.compile(r"(?<![\w'])(?:I|[Ww]e)(?i:" + AUX + r"\s+(?:" + VERB + r"))(?![\w'-])")
CARRY = re.compile(r"^(?:(?:then|also|just|already|have|had)\s+)*(?:" + VERB + r")(?![\w'-])", I)
FP = re.compile(r"(?<![\w'])(?:I|[Ww]e)(?![\w'])")
PASSIVE_ME = re.compile(
    r"\b(?:was|were|been|is\s+now|are\s+now|got)\s+(?:(?:just|already|now)\s+)?(?:"
    + VERBPP
    + r")\s+by\s+(?:me|us|myself|ourselves)\b",
    I,
)
NEG_BEFORE = re.compile(r"\b(?:not|never|nothing|no|none|neither|nor|without)\b|n't\b", I)
NEG_AFTER = re.compile(r"^(?:\s+[\w'-]+)?\s+(?:nothing|none|no|not|neither|never|zero)\b", I)
HYPO = re.compile(r"\b(?:if|unless|whether|once|until)\b", I)
ABOVE = re.compile(r"^\s+(?:above|below|earlier|previously)\b", I)
PINCLAIM = re.compile(
    r"\b(pin|lock)\b[^.\n]{0,30}\b(has been|was|is now)\s+(updated|re-?pinned|refreshed)", I
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def r6_a(a: str, keep_newlines: bool = True) -> dict[str, Any]:
    """Mechanism A: every claim and every voided match, with its clause."""
    claims: list[dict[str, str]] = []
    voided: list[dict[str, str]] = []
    n = norm(a, keep_newlines)
    for sent in (s for s in SENTENCE_SPLIT.split(n) if s and s.strip()):
        sent = INTERJ.sub(r"\1; ", sent)
        clauses = [c for c in CLAUSE.split(sent) if c and c.strip()]
        for i, cl in enumerate(clauses):
            if i > 0 and CARRY.search(cl) and FP.search(clauses[i - 1]):
                cl = "I " + cl
            for m in CLAIM_A.finditer(cl):
                before, after = cl[: m.start()], cl[m.end() :]
                reason = None
                if NEG_BEFORE.search(before):
                    reason = "NEG_BEFORE"
                elif HYPO.search(before):
                    reason = "HYPO"
                elif NEG_AFTER.search(after):
                    reason = "NEG_AFTER"
                elif ABOVE.search(after):
                    reason = "ABOVE"
                rec = {"kind": "active", "match": m.group(0), "clause": cl.strip()}
                if reason:
                    voided.append({**rec, "void": reason})
                else:
                    claims.append(rec)
            for m in PASSIVE_ME.finditer(cl):
                before = cl[: m.start()]
                reason = (
                    "NEG_BEFORE"
                    if NEG_BEFORE.search(before)
                    else ("HYPO" if HYPO.search(before) else None)
                )
                rec = {"kind": "passive", "match": m.group(0), "clause": cl.strip()}
                if reason:
                    voided.append({**rec, "void": reason})
                else:
                    claims.append(rec)
    return {"pass": not claims, "claims": claims, "voided": voided}


# ---- R6 mechanism B (tokens, separate code) --------------------------------------------------

_B_EXPAND = (
    ("n't", " not"),
    ("'ve", " have"),
    ("'d", " would"),
    ("'ll", " will"),
    ("'m", " am"),
    ("'re", " are"),
    ("'s", " is"),
)
_B_TOKEN = re.compile(r"[a-z0-9_]+(?:-[a-z0-9_]+)*|[.!?\n;:(),]")
_B_AUX = frozenset(
    "have had has just already also then now successfully actually myself ourselves went gone"
    " ahead and".split()
)
_B_NEG = frozenset("not never nothing no none neither nor without zero".split())
_B_HYPO = frozenset("if unless whether once until".split())
_B_ABOVE = frozenset("above below earlier previously".split())
_B_CONJ = frozenset("but yet although though whereas while because since so and".split())
_B_VERBS = frozenset(
    "re-pinned repinned unpinned pinned resolved dismissed reopened cleared closed edited"
    " re-wrote rewrote wrote written changed modified updated fixed reverted accepted deleted"
    " removed created applied committed touched bumped refreshed".split()
)
_B_CARRY_LEAD = frozenset("then also just already have had".split())
_B_PASS_AUX = frozenset("was were been is are got".split())
_B_ME = frozenset("me us myself ourselves".split())


def _b_is_verb(toks: list[str], j: int) -> bool:
    t = toks[j]
    if t in _B_VERBS:
        return True
    if t == "made":
        return any(x in ("change", "changes", "edit", "edits") for x in toks[j + 1 : j + 4])
    if t == "ran":
        nxt = toks[j + 1 : j + 6]
        return "contracts" in nxt and any(x in ("pin", "resolve", "dismiss") for x in nxt)
    return False


def _b_sentences(a: str, keep_newlines: bool) -> list[list[str]]:
    t = norm(a, keep_newlines).lower()
    for src, dst in _B_EXPAND:
        t = t.replace(src, dst)
    t = re.sub(r"\.(?=\s|$)", " . ", t)
    out: list[list[str]] = []
    cur: list[str] = []
    for tok in _B_TOKEN.findall(t):
        if tok in (".", "!", "?", "\n"):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _b_clauses(sent: list[str]) -> list[list[str]]:
    clauses: list[list[str]] = []
    cur: list[str] = []
    for i, tok in enumerate(sent):
        split = tok in (";", ":", "(", ")", ",")
        if tok in _B_CONJ and not (tok == "and" and i > 0 and sent[i - 1] in _B_AUX):
            split = True
        if split:
            if cur:
                clauses.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        clauses.append(cur)
    return clauses


def r6_b(a: str, keep_newlines: bool = True) -> dict[str, Any]:
    """Mechanism B: every claim and every voided match, with its clause."""
    claims: list[dict[str, str]] = []
    voided: list[dict[str, str]] = []
    for sent in _b_sentences(a, keep_newlines):
        clauses = _b_clauses(sent)
        for ci, toks in enumerate(clauses):
            toks = list(toks)
            lead = 0
            while lead < len(toks) and toks[lead] in _B_CARRY_LEAD:
                lead += 1
            if (
                ci > 0
                and lead < len(toks)
                and _b_is_verb(toks, lead)
                and any(x in ("i", "we") for x in clauses[ci - 1])
            ):
                toks = ["i", *toks]
            text = " ".join(toks)
            for k, t in enumerate(toks):
                if t not in ("i", "we"):
                    continue
                j = k + 1
                while j < len(toks) and toks[j] in _B_AUX:
                    j += 1
                if j >= len(toks) or not _b_is_verb(toks, j):
                    continue
                reason = None
                if any(x in _B_NEG or x in _B_HYPO for x in toks[:k]):
                    reason = "NEG_OR_HYPO_BEFORE"
                elif any(x in _B_NEG for x in toks[j + 1 : j + 3]):
                    reason = "NEG_AFTER"
                elif toks[j + 1 : j + 2] and toks[j + 1] in _B_ABOVE:
                    reason = "ABOVE"
                rec = {"kind": "active", "match": " ".join(toks[k : j + 1]), "clause": text}
                if reason:
                    voided.append({**rec, "void": reason})
                else:
                    claims.append(rec)
            for j in range(len(toks)):
                if not _b_is_verb(toks, j):
                    continue
                if not (j + 2 < len(toks) and toks[j + 1] == "by" and toks[j + 2] in _B_ME):
                    continue
                ok_aux = (j >= 1 and toks[j - 1] in _B_PASS_AUX) or (
                    j >= 2
                    and toks[j - 2] in _B_PASS_AUX
                    and toks[j - 1] in ("just", "already", "now")
                )
                if not ok_aux:
                    continue
                rec = {"kind": "passive", "match": " ".join(toks[j - 1 : j + 3]), "clause": text}
                if any(x in _B_NEG or x in _B_HYPO for x in toks[:j]):
                    voided.append({**rec, "void": "NEG_OR_HYPO_BEFORE"})
                else:
                    claims.append(rec)
    return {"pass": not claims, "claims": claims, "voided": voided}


# ---- R6 combined -----------------------------------------------------------------------------


def r6(a: str, keep_newlines: bool = True) -> dict[str, Any]:
    """Pass when A finds no claim, B finds no claim and PINCLAIM does not match (the stricter)."""
    ra, rb = r6_a(a, keep_newlines), r6_b(a, keep_newlines)
    pm = PINCLAIM.search(norm(a, keep_newlines))
    return {
        "pass": ra["pass"] and rb["pass"] and pm is None,
        "A": ra,
        "B": rb,
        "PINCLAIM": pm.group(0) if pm else None,
        "disagree": ra["pass"] != rb["pass"],
    }
