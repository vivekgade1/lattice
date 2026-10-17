"""A business glossary for a Lattice spec: what the words of a rule mean (Spike G).

Rules use business words ("active window", "past period", "nudge") whose meaning the rules
themselves rarely pin down. This module finds the words a rule *uses* (deterministically, in
English, no model), compares them with the owner's approved glossary, and reports the ones that
are undefined. It sits beside :mod:`lattice.spec.rationale` and shares its file layout, approval
guard and atomic writes.

**Files** (D1): ``<stem>.glossary.json`` (approved terms and the ``ignore`` list; authoritative)
and ``<stem>.glossary.proposed.json`` (proposals), beside the trace. Schema
``lattice.glossary/1``::

    {"schema": "lattice.glossary/1", "source": "REQUIREMENTS.md",
     "terms": [{"term": "active window", "aliases": ["window"], "definition": "...",
                "owner": "...", "status": "approved | proposed | rejected",
                "author": "owner | llm:<m> | extracted | hand", "approved_by": "...",
                "approved_sha": "...", "basis": [{"target": "rule:R97", "quote": "..."}]}],
     "ignore": ["password"]}

**Term candidates** (D6; a rule "uses" a term). The *prose* of a rule is its text with each
backtick span replaced by a break; its *code* is the backtick spans, split on every character but
letters, digits and ``_``. C1 entity (a table's schema and SQL name), C2 enum value (a ``one_of``
value), C3 marked phrase (1-4 words in double quotes or emphasis, outside ``{...}``/``[...]`` and
not followed by ``:``), C5 service, C6 inline definition (``A|An|The <P> is|are|means ...``).
Normal form: lowercase, ``_`` and ``-`` become spaces, the possessive is stripped and a plural
stemmed on the *last word only*; matching is whole-token phrase equality, longest first, and
non-overlapping within a rule. Code tokens count for C1, C2 and C5 only. By design it does not see
an unmarked new phrase or an unknown backticked word (the registered misses).

**Lint** (D7; never part of :func:`lattice.spec.lint.lint`): G001 undefined term (one per rule and
term), G002 approved term used by no rule, G003 a rationale note or approved term that is not
current, G004 a proposal pending. ``glossary lint`` exits 0 with no G001 and no G003, 1 with one,
2 when it cannot run.

CLI (``python -m lattice.spec glossary``)::

    show --trace T [--glossary P] [--json]
    lint --trace T --spec MOD [--glossary P] [--rationale P] [--json]
    draft --trace T --spec MOD --out FILE [--model M]
    approve --trace T --from PROPOSALS --term TERM --approver NAME [--owner NAME]
            [--definition T] [--retire-proposal]
    add --trace T (--term TERM [--alias A ...] --definition T --owner NAME | --ignore WORD)
        --approver NAME
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.spec import rationale as rt
from lattice.spec.model import Spec, tables_of
from lattice.spec.rationale import (
    GLOSSARY_SCHEMA,
    NotesError,
    NotesPaths,
    RationaleSet,
    atomic_write_json,
    human_guard,
    notes_paths,
    pct,
    ratio,
    rule_number,
    same_file,
)
from lattice.spec.rules import Trace

DEFINITION_MAX = 2000
_BREAK = "\x00"
_CODE_SPAN = re.compile(r"`([^`]*)`")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*'?")
_CODE_SPLIT = re.compile(r"[^A-Za-z0-9_]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_MARK = re.compile(r"^[A-Za-z'’_-]+(?: [A-Za-z'’_-]+){0,3}$")
_STRAIGHT = re.compile(r'"([^"\n\x00]*)"')
_CURLY = re.compile("“([^”\n\x00]*)”")
_EMPH2 = re.compile(r"(?<![\w*])\*\*(?=\S)([^*\n\x00]+?)(?<=\S)\*\*(?![\w*])")
_EMPH1 = re.compile(r"(?<![\w*])\*(?=\S)([^*\n\x00]+?)(?<=\S)\*(?![\w*])")
_EMPH_U = re.compile(r"(?<!\w)_(?=\S)([^_\n\x00]+?)(?<=\S)_(?!\w)")
_W = r"[a-z][a-z'-]*"
_C6 = re.compile(
    rf"^(?:A|An|The)\s+(?:user's\s+)?(?P<p>{_W}(?:\s+{_W}){{0,3}}?)\s+"
    r"(?:\([^)]*\)\s+)?(?:is|are|means)\b"
)
_C6_STOP = frozenset(
    "that which who whose with of for in on at to by from and or without whole".split()
)
SOURCE_ORDER = ("C6", "C3", "C2", "C1", "C5")
CODE_SOURCES = frozenset({"C1", "C2", "C5"})
MAX_PHRASE = 4


def _bad(detail: str) -> NotesError:
    return NotesError("bad_glossary", detail)


# ---- normal forms (D6) --------------------------------------------------------------------


def slug(term: str) -> str:
    """``active window`` -> ``active-window``, ``on_track`` -> ``on-track``."""
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def words_of(text: str) -> list[str]:
    """The lowercase word tokens of `text` (``_`` and ``-`` split words; a trailing quote of a
    plural possessive is dropped)."""
    t = text.lower().replace("’", "'").replace("_", " ").replace("-", " ")
    return [w.rstrip("'") for w in _WORD.findall(t)]


def stem_last(word: str) -> str:
    """The possessive stripped and a plural stemmed (the last word of a phrase)."""
    if word.endswith("'s"):
        word = word[:-2]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def key_of(words: Sequence[str]) -> tuple[str, ...]:
    """The normal form of a phrase given as words."""
    ws = list(words)
    return (*ws[:-1], stem_last(ws[-1])) if ws else ()


def norm_phrase(text: str) -> tuple[str, ...]:
    """The normal form of a phrase (D6): ``Grace days`` -> ``('grace', 'day')``."""
    return key_of(words_of(text))


def normal_text(text: str) -> str:
    return " ".join(norm_phrase(text))


# ---- candidates ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A word or phrase some rule may use as a business term."""

    key: tuple[str, ...]
    display: str
    sources: set[str] = field(default_factory=set)
    defined_in: list[str] = field(default_factory=list)  # C6: the rules defining it inline

    @property
    def source(self) -> str:
        return next(s for s in SOURCE_ORDER if s in self.sources)

    @property
    def normal(self) -> str:
        return " ".join(self.key)


@dataclass(frozen=True)
class Use:
    """The first use of a candidate in a rule."""

    rule: str
    key: tuple[str, ...]
    text: str  # the matched words as written
    in_code: bool


@dataclass
class Analysis:
    """The candidates of a spec and trace, and which rules use them."""

    candidates: dict[tuple[str, ...], Candidate]
    uses: dict[str, dict[tuple[str, ...], Use]]  # rule -> candidate key -> first use
    rules: dict[str, str]  # rule id -> text


def _add(
    cands: dict[tuple[str, ...], Candidate], text: str, source: str, rule: str | None = None
) -> None:
    key = norm_phrase(text)
    if not key:
        return
    c = cands.get(key)
    if c is None:
        c = cands[key] = Candidate(key, text.strip())
    c.sources.add(source)
    if source == "C6" and rule is not None and rule not in c.defined_in:
        c.defined_in.append(rule)


def prose_and_code(text: str) -> tuple[str, list[str]]:
    """(the prose, the backtick spans) of a rule's text."""
    return _CODE_SPAN.sub(_BREAK, text), [m.group(1) for m in _CODE_SPAN.finditer(text)]


def _pairs(prose: str) -> list[tuple[int, int]]:
    """The ``{...}`` and ``[...]`` pairs of the prose, as (start, end) offsets."""
    out = []
    for open_c, close_c in (("{", "}"), ("[", "]")):
        stack: list[int] = []
        for i, ch in enumerate(prose):
            if ch == open_c:
                stack.append(i)
            elif ch == close_c and stack:
                out.append((stack.pop(), i))
    return out


def marked_phrases(prose: str) -> list[str]:
    """The C3 phrases of a rule's prose: quoted or emphasised 1-4 word spans, minus those
    inside ``{...}``/``[...]`` or immediately followed by ``:``."""
    pairs = _pairs(prose)
    out = []
    for rx in (_STRAIGHT, _CURLY, _EMPH2, _EMPH1, _EMPH_U):
        for m in rx.finditer(prose):
            inner = m.group(1)
            if not _MARK.match(inner):
                continue
            if any(a < m.start() and m.end() <= b + 1 for a, b in pairs):
                continue
            if prose[m.end() : m.end() + 1] == ":":
                continue
            out.append(inner)
    return out


def _strip_marks(prose: str) -> str:
    t = prose.replace('"', "").replace("“", "").replace("”", "")
    for rx in (_EMPH2, _EMPH1, _EMPH_U):
        t = rx.sub(lambda m: m.group(1), t)
    return t


def inline_definition(prose: str) -> str | None:
    """P of ``A|An|The [user's] P [(...)] is|are|means`` at the start of the prose (C6)."""
    m = _C6.match(_strip_marks(prose).strip())
    if m is None:
        return None
    p = m.group("p")
    if any(w in _C6_STOP for w in p.split()):
        return None
    return p


def spec_candidates(spec: Spec) -> dict[tuple[str, ...], Candidate]:
    """C1, C2 and C5 candidates, from the spec."""
    cands: dict[tuple[str, ...], Candidate] = {}
    tables, sql_names = tables_of(spec.profile)
    for name, schema in tables.items():
        _add(cands, " ".join(_CAMEL.findall(name)), "C1")
        _add(cands, sql_names.get(name, name), "C1")
        for _col, values in dict(schema.one_of).items():
            for v in values:
                _add(cands, str(v), "C2")
    for svc in spec.services:
        _add(cands, svc.name, "C5")
    return cands


def _segments(prose: str) -> list[list[str]]:
    return [ws for seg in prose.split(_BREAK) if (ws := words_of(seg))]


def _match(
    segments: Sequence[list[str]], keys: Mapping[tuple[str, ...], Any], longest: int
) -> list[tuple[tuple[str, ...], str]]:
    """Longest-first, non-overlapping matches of `keys` in the segments: (key, text as written)."""
    out = []
    for ws in segments:
        taken = [False] * len(ws)
        for n in range(min(longest, len(ws)), 0, -1):
            for i in range(len(ws) - n + 1):
                if any(taken[i : i + n]):
                    continue
                k = key_of(ws[i : i + n])
                if k in keys:
                    out.append((k, " ".join(ws[i : i + n])))
                    for j in range(i, i + n):
                        taken[j] = True
    return out


def _code_tokens(code: Sequence[str]) -> list[list[str]]:
    """The tokens of a rule's code: each backtick span split on every character but letters,
    digits and ``_``; a token is one phrase (its ``_`` are spaces), never matched piecemeal."""
    return [ws for chunk in code for c in _CODE_SPLIT.split(chunk) if (ws := words_of(c))]


def _match_code(
    code: Sequence[str], keys: Mapping[tuple[str, ...], Any]
) -> list[tuple[tuple[str, ...], str]]:
    """The code tokens whose whole normal form is a key:
    ``validation_failed`` is not ``failed``."""
    out = []
    for ws in _code_tokens(code):
        k = key_of(ws)
        if k in keys:
            out.append((k, " ".join(ws)))
    return out


def analyse(spec: Spec, trace: Trace) -> Analysis:
    """The candidates (D6) and each rule's uses of them."""
    cands = spec_candidates(spec)
    parts = {}
    for r in trace.rules:
        prose, code = prose_and_code(r.text)
        parts[r.id] = (prose, code)
        for phrase in marked_phrases(prose):
            _add(cands, phrase, "C3")
        p = inline_definition(prose)
        if p is not None:
            _add(cands, p, "C6", r.id)
    longest = max([MAX_PHRASE, *(len(k) for k in cands)])
    code_keys = {k: c for k, c in cands.items() if c.sources & CODE_SOURCES}
    uses: dict[str, dict[tuple[str, ...], Use]] = {}
    for r in trace.rules:
        prose, code = parts[r.id]
        found: dict[tuple[str, ...], Use] = {}
        for k, text in _match(_segments(prose), cands, longest):
            found.setdefault(k, Use(r.id, k, text, False))
        for k, text in _match_code(code, code_keys):
            found.setdefault(k, Use(r.id, k, text, True))
        uses[r.id] = found
    return Analysis(cands, uses, {r.id: r.text for r in trace.rules})


# ---- glossary files -----------------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    term: str
    aliases: tuple[str, ...]
    definition: str
    owner: str | None
    status: str
    author: str
    approved_by: str | None = None
    approved_sha: str | None = None
    basis: tuple[dict[str, Any], ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def slug(self) -> str:
        return slug(self.term)

    @property
    def keys(self) -> list[tuple[str, ...]]:
        return [norm_phrase(self.term), *(norm_phrase(a) for a in self.aliases)]

    @property
    def sha_ok(self) -> bool:
        return self.approved_sha == term_sha(self.definition, self.owner)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "term": self.term,
            "aliases": list(self.aliases),
            "definition": self.definition,
            "owner": self.owner,
            "status": self.status,
            "author": self.author,
            "approved_by": self.approved_by,
            "approved_sha": self.approved_sha,
            "basis": [dict(b) for b in self.basis],
        }
        d.update(self.extra)
        return d


def term_sha(definition: str, owner: str | None) -> str:
    return rt._sha_of({"definition": definition, "owner": owner})


def _parse_term(d: Any, i: int, kind: str) -> Term:
    if not isinstance(d, dict):
        raise _bad(f"terms[{i}] is not an object")
    term = d.get("term")
    if not isinstance(term, str) or not slug(term):
        raise _bad(f"terms[{i}]: term must be a non-empty string")
    where = f"terms[{i}] ({term})"
    aliases = d.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) and slug(a) for a in aliases):
        raise _bad(f"{where}: aliases must be a list of non-empty strings")
    definition = d.get("definition")
    if not isinstance(definition, str) or not definition.strip():
        raise _bad(f"{where}: definition is empty")
    if len(definition) > DEFINITION_MAX:
        raise _bad(f"{where}: definition is longer than {DEFINITION_MAX} characters")
    owner = d.get("owner")
    if owner is not None and not isinstance(owner, str):
        raise _bad(f"{where}: owner must be a string or null")
    status = d.get("status")
    if status not in rt.NOTE_STATUSES:
        raise _bad(f"{where}: status must be one of {', '.join(rt.NOTE_STATUSES)}")
    author = d.get("author")
    if not isinstance(author, str) or not (
        author in ("owner", "hand", "extracted") or (author.startswith("llm:") and len(author) > 4)
    ):
        raise _bad(f"{where}: author must be owner, hand, extracted or llm:<model>")
    by, sha = d.get("approved_by"), d.get("approved_sha")
    for key, v in (("approved_by", by), ("approved_sha", sha)):
        if v is not None and not isinstance(v, str):
            raise _bad(f"{where}: {key} must be a string or null")
    basis_raw = d.get("basis", [])
    if not isinstance(basis_raw, list):
        raise _bad(f"{where}: basis must be a list")
    basis = []
    for b in basis_raw:
        if (
            not isinstance(b, dict)
            or not isinstance(b.get("target"), str)
            or not isinstance(b.get("quote"), str)
        ):
            raise _bad(f"{where}: a basis entry needs a string target and quote")
        basis.append(dict(b))
    if kind == "authoritative":
        if status != "approved":
            raise _bad(f"{where}: the authoritative file holds only approved terms, not {status}")
        for key, v in (("owner", owner), ("approved_by", by), ("approved_sha", sha)):
            if not v or not v.strip():
                raise _bad(f"{where}: an approved term needs a non-empty {key}")
    else:
        if status == "approved":
            raise _bad(f"{where}: a proposals file cannot hold an approved term")
        if by is not None or sha is not None:
            raise _bad(f"{where}: a proposal has no approved_by or approved_sha")
        if author.startswith("llm:") and owner is not None:
            raise _bad(f"{where}: a model never names the owner")
    extra = {k: d[k] for k in ("reason", "rejected_basis", "dropped") if k in d}
    return Term(
        term, tuple(aliases), definition, owner, status, author, by, sha, tuple(basis), extra
    )


def load_terms_file(path: Path | None, kind: str) -> tuple[list[Term], list[str]]:
    """(terms, ignore) of `path`; ``([], [])`` when absent. Raises ``bad_glossary`` (D14)."""
    if path is None or not path.is_file():
        return [], []
    data = rt._read_json(path, "bad_glossary")
    if not isinstance(data, dict):
        raise _bad(f"{path.name}: the top level is not an object")
    if data.get("schema") != GLOSSARY_SCHEMA:
        raise _bad(f"{path.name}: schema must be {GLOSSARY_SCHEMA!r}, not {data.get('schema')!r}")
    raw = data.get("terms")
    if not isinstance(raw, list):
        raise _bad(f"{path.name}: terms must be a list")
    terms = [_parse_term(d, i, kind) for i, d in enumerate(raw)]
    ignore = data.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(x, str) and slug(x) for x in ignore):
        raise _bad(f"{path.name}: ignore must be a list of non-empty strings")
    if ignore and kind != "authoritative":
        raise _bad(f"{path.name}: ignore lives only in the authoritative file")
    if kind == "authoritative":
        seen: dict[tuple[str, ...], str] = {}
        for t in terms:
            for k in t.keys:
                if k in seen:
                    raise _bad(
                        f"{path.name}: duplicate: {t.term!r} and {seen[k]!r} have the same "
                        f"normal form {' '.join(k)!r}"
                    )
                seen[k] = t.term
    return terms, [str(x) for x in ignore]


@dataclass
class GlossarySet:
    """The glossary of one project: approved terms, proposals, the ignore list."""

    file: Path | None
    proposals_file: Path | None
    terms: list[Term]  # approved, in the file (including edited ones)
    proposals: list[Term]
    ignore: list[str]

    @property
    def defining(self) -> list[Term]:
        """The approved terms whose approval still holds (an edited one defines nothing)."""
        return [t for t in self.terms if t.sha_ok]

    @property
    def edited(self) -> list[Term]:
        return [t for t in self.terms if not t.sha_ok]

    def defined_keys(self) -> dict[tuple[str, ...], Term | None]:
        """Normal form -> the approved term (None for an ignore entry)."""
        out: dict[tuple[str, ...], Term | None] = {}
        for t in self.defining:
            for k in t.keys:
                out[k] = t
        for i in self.ignore:
            out.setdefault(norm_phrase(i), None)
        return out

    def proposal_for(self, key: tuple[str, ...]) -> Term | None:
        for p in self.proposals:
            if p.status == "proposed" and key in p.keys:
                return p
        return None

    def term_by(self, name: str) -> Term | None:
        """The approved term whose term, alias or slug is `name`."""
        k, s = norm_phrase(name), slug(name)
        for t in self.defining:
            if k in t.keys or s == t.slug:
                return t
        return None


def load_glossary(paths: NotesPaths | None) -> GlossarySet | None:
    """The :class:`GlossarySet`; None when neither the authoritative nor the proposals file
    exists. Raises ``bad_glossary`` for an invalid file."""
    if paths is None or not paths.has_glossary:
        return None
    terms, ignore = load_terms_file(paths.glossary, "authoritative")
    proposals, _ = load_terms_file(paths.glossary_proposed, "proposals")
    return GlossarySet(
        paths.glossary if paths.glossary.is_file() else None,
        paths.glossary_proposed if paths.glossary_proposed.is_file() else None,
        terms,
        proposals,
        ignore,
    )


# ---- the notes bundle ---------------------------------------------------------------------


@dataclass
class Notes:
    """What is on disk for one project: the rationale and the glossary (either may be None)."""

    paths: NotesPaths
    rationale: RationaleSet | None
    glossary: GlossarySet | None

    @property
    def sections(self) -> bool:
        """Whether the chat digest gets sections: an authoritative file exists."""
        return self.paths.rationale.is_file() or self.paths.glossary.is_file()


def load_notes(trace: Trace, paths: NotesPaths | None) -> Notes | None:
    """The notes of `paths`; None when no file exists. Raises :class:`NotesError`."""
    if paths is None or not paths.any:
        return None
    return Notes(paths, rt.load_set(trace, paths), load_glossary(paths))


def discover_notes(
    trace: Trace,
    trace_path: Path | str,
    *,
    rationale: Path | str | None = None,
    glossary: Path | str | None = None,
) -> Notes | None:
    """The notes beside the trace at `trace_path` (D1), with optional path overrides."""
    paths = notes_paths(trace_path, rationale=rationale, glossary=glossary, source=trace.source)
    return load_notes(trace, paths)


# ---- lint (D7) and metrics (D8) -----------------------------------------------------------


@dataclass
class LintResult:
    findings: list[dict[str, Any]]
    metrics: dict[str, Any]
    exit_code: int


def _fix_for(c: Candidate, gset: GlossarySet | None, analysis: Analysis, gname: str) -> str:
    """What to do about an undefined term: name the glossary file; say "approve the proposed
    definition" when a proposal exists; quote the rule that defines it inline (verbatim) for C6."""
    prop = gset.proposal_for(c.key) if gset is not None else None
    inline = (
        f"; rule {c.defined_in[0]} defines it inline: {analysis.rules[c.defined_in[0]]!r}"
        if c.defined_in
        else ""
    )
    if prop is not None:
        return (
            f"approve the proposed definition of {prop.term!r} in {gname} "
            f"(python -m lattice.spec glossary approve --from <proposals> --term {prop.term!r} "
            f"--approver NAME){inline}"
        )
    if c.defined_in:
        return f"define {c.display!r} in {gname}{inline}"
    return (
        f"define {c.display!r} in {gname} (glossary add --term {c.display!r} ...), or list it "
        f"under ignore if it is not a business term"
    )


def lint_glossary(
    analysis: Analysis,
    trace: Trace,
    gset: GlossarySet | None,
    rset: RationaleSet | None = None,
    names: tuple[str, str] = ("glossary.json", "glossary.proposed.json"),
) -> LintResult:
    """G001-G004 over `analysis` (the spec's candidates and uses) against `gset`."""
    defined = gset.defined_keys() if gset is not None else {}
    findings: list[dict[str, Any]] = []
    undefined_terms: set[tuple[str, ...]] = set()
    rules_with_use = rules_clean = rules_undef = 0
    for r in trace.rules:
        found = analysis.uses.get(r.id, {})
        if not found:
            continue
        rules_with_use += 1
        bad = [u for k, u in found.items() if k not in defined]
        if not bad:
            rules_clean += 1
            continue
        rules_undef += 1
        for u in bad:
            c = analysis.candidates[u.key]
            undefined_terms.add(u.key)
            findings.append(
                {
                    "code": "G001",
                    "rule": r.id,
                    "term": c.display,
                    "normal": c.normal,
                    "source": c.source,
                    "sources": [s for s in SOURCE_ORDER if s in c.sources],
                    "match": u.text,
                    "in_code": u.in_code,
                    "message": f"{r.id} uses the undefined term {c.display!r} ({c.source}): "
                    f"{u.text!r}",
                    "fix": _fix_for(c, gset, analysis, names[0]),
                }
            )
    # G002: approved terms no rule uses (any word window of the prose or code equals one)
    gx = 0
    if gset is not None:
        for t in gset.defining:
            if not _term_used(t, trace):
                gx += 1
                findings.append(
                    {
                        "code": "G002",
                        "term": t.term,
                        "normal": normal_text(t.term),
                        "message": f"the approved term {t.term!r} is used by no rule",
                        "fix": "remove it, or add the rule that uses it",
                    }
                )
        for t in gset.edited:
            findings.append(
                {
                    "code": "G003",
                    "term": t.term,
                    "normal": normal_text(t.term),
                    "state": "edited",
                    "message": f"the approved term {t.term!r} was edited after approval, so it "
                    "defines nothing",
                    "fix": "re-approve it (glossary add / approve) or restore the approved text",
                }
            )
        for p in gset.proposals:
            if p.status == "proposed":
                findings.append(
                    {
                        "code": "G004",
                        "term": p.term,
                        "normal": normal_text(p.term),
                        "message": f"the proposed term {p.term!r} ({p.author}) is not approved",
                        "fix": "approve it or reject it",
                    }
                )
    if rset is not None:
        for r in rset.resolved:
            if r.state in ("edited", "ambiguous", "stale"):
                findings.append(
                    {
                        "code": "G003",
                        "rule": r.note.rule,
                        "state": r.state,
                        "matches": list(r.matches),
                        "message": f"the rationale note of {r.note.rule} is {r.state}"
                        + (f" (its wording is now in {', '.join(r.matches)})" if r.matches else ""),
                        "fix": "re-approve the note for the current wording of the rule",
                    }
                )
        for p in rset.proposals:
            if p.status == "proposed":
                findings.append(
                    {
                        "code": "G004",
                        "rule": p.rule,
                        "message": f"the rationale proposed for {p.rule} ({p.author}) is not "
                        "approved",
                        "fix": "approve it or reject it",
                    }
                )
    findings.sort(
        key=lambda f: (f["code"], rule_number(f["rule"]) if "rule" in f else -1, f.get("term", ""))
    )
    gu = sum(1 for f in findings if f["code"] == "G001")
    g003 = sum(1 for f in findings if f["code"] == "G003")
    metrics = {
        "file": gset.file.name if gset is not None and gset.file else None,
        "GU": gu,
        "GT": len(undefined_terms),
        "GR": rules_undef,
        "GC": ratio(rules_clean, rules_with_use),
        "GA": len(gset.defining) if gset is not None else 0,
        "GP": sum(1 for p in gset.proposals if p.status == "proposed") if gset is not None else 0,
        "GX": gx,
        "G003": g003,
    }
    return LintResult(findings, metrics, 1 if (gu or g003) else 0)


def _term_used(t: Term, trace: Trace) -> bool:
    keys = set(t.keys)
    longest = max(len(k) for k in keys)
    for r in trace.rules:
        prose, code = prose_and_code(r.text)
        if _match(_segments(prose), dict.fromkeys(keys), longest):
            return True
        if _match_code(code, dict.fromkeys(keys)):
            return True
    return False


def find_terms(text: str, gset: GlossarySet | None) -> list[Term]:
    """The approved terms whose term or alias occurs in `text` (a question), by slug."""
    if gset is None:
        return []
    segs = _segments(text)
    found = {}
    for t in gset.defining:
        keys = dict.fromkeys(t.keys)
        if _match(segs, keys, max(len(k) for k in keys)):
            found[t.slug] = t
    return [found[s] for s in sorted(found)]


def terms_by_rule(analysis: Analysis, gset: GlossarySet | None) -> dict[str, list[Term]]:
    """For each rule, the approved terms it uses (by their term or an alias), sorted by slug."""
    out: dict[str, list[Term]] = {}
    if gset is None:
        return out
    defined = gset.defined_keys()
    for rid, found in analysis.uses.items():
        ts = {t.slug: t for k in found if (t := defined.get(k)) is not None}
        if ts:
            out[rid] = [ts[s] for s in sorted(ts)]
    return out


def term_view(t: Term) -> dict[str, Any]:
    return {
        "term": t.term,
        "slug": t.slug,
        "aliases": list(t.aliases),
        "definition": t.definition,
        "owner": t.owner,
        "approved_by": t.approved_by,
    }


def run_lint(spec: Spec, trace: Trace, notes: Notes | None, paths: NotesPaths | None) -> LintResult:
    names = (
        paths.glossary.name if paths else "glossary.json",
        paths.glossary_proposed.name if paths else "glossary.proposed.json",
    )
    gset = notes.glossary if notes is not None else None
    rset = notes.rationale if notes is not None else None
    return lint_glossary(analyse(spec, trace), trace, gset, rset, names)


# ---- drafting (D9) ------------------------------------------------------------------------


def draft_terms(spec: Spec, trace: Trace) -> list[Term]:
    """The deterministic proposals: C6 verbatim from the defining rule, enum, table and service
    stubs. Nothing is approved, nothing is invented."""
    an = analyse(spec, trace)
    tables, sql_names = tables_of(spec.profile)
    stubs: dict[tuple[str, ...], tuple[str, str, str, list[dict[str, str]]]] = {}
    for name in tables:
        sql = sql_names.get(name, name)
        text = f"From the spec: the table {sql} ({name})."
        for label in (" ".join(_CAMEL.findall(name)), sql):
            stubs.setdefault(norm_phrase(label), ("C1", " ".join(_CAMEL.findall(name)), text, []))
    for name, schema in tables.items():
        for col, values in dict(schema.one_of).items():
            vals = ", ".join(str(v) for v in values)
            text = f"From the spec: one of the values of {name}.{col} ({vals})."
            for v in values:
                stubs.setdefault(norm_phrase(str(v)), ("C2", str(v), text, []))
    for svc in spec.services:
        text = svc.doc or f"From the spec: the background service {svc.name}."
        stubs[norm_phrase(svc.name)] = ("C5", svc.name, text, [])
    out: list[Term] = []
    done: set[tuple[str, ...]] = set()
    for key, c in an.candidates.items():
        if "C6" in c.sources and c.defined_in:
            rid = c.defined_in[0]
            text = an.rules[rid]
            out.append(
                Term(
                    c.display,
                    (),
                    text,
                    None,
                    "proposed",
                    "extracted",
                    None,
                    None,
                    ({"target": f"rule:{rid}", "quote": text},),
                )
            )
            done.add(key)
    for key, (_src, display, text, basis) in stubs.items():
        if key in done:
            continue
        out.append(Term(display, (), text, None, "proposed", "extracted", None, None, tuple(basis)))
    return out


GLOSSARY_DRAFT_SYSTEM = """\
You propose a definition for ONE business term of an application's requirements, using only the
spec text you are given. You do not know the owner's definition. Cite the spec text you lean on;
if the spec does not define the term, give basis [] and say so in the definition; never name an
owner.

Reply with ONE JSON object and nothing else:
{"definition": "1-600 characters",
 "basis": [{"target": "<exact target from the spec text>", "quote": "<text copied from it>"}]}
"""


def draft_with_model(
    spec: Spec, trace: Trace, an: Analysis, have: set[tuple[str, ...]], generator: Any, model: str
) -> tuple[list[Term], list[dict[str, Any]]]:
    """Model proposals for the C3 terms not in `have`. Citations are checked by
    ``ask.check_citation``; a forged owner or status is dropped."""
    from lattice.spec.ask import _json_object, build_digest, check_citation

    digest = build_digest(spec, trace)
    out: list[Term] = []
    reports = []
    for key, c in an.candidates.items():
        if key in have or "C3" not in c.sources or "C6" in c.sources:
            continue
        rules = [rid for rid, uses in an.uses.items() if key in uses][:8]
        ctx = [f"Term: {c.display}", "", "Rules that use it:"]
        ctx += [f"- {rid}: {an.rules[rid]}" for rid in rules]
        ctx += ["", "Reply with the JSON object only."]
        gen = generator.generate(
            GLOSSARY_DRAFT_SYSTEM, [{"role": "user", "content": "\n".join(ctx)}]
        )
        rep: dict[str, Any] = {"term": c.display, "cost_usd": getattr(gen, "cost_usd", None)}
        try:
            data = _json_object(gen.text)
            definition = data.get("definition")
            if not isinstance(definition, str) or not definition.strip():
                raise ValueError("no definition")
        except (ValueError, json.JSONDecodeError, AttributeError) as err:
            rep["error"] = f"unreadable reply ({err})"
            reports.append(rep)
            continue
        dropped = sorted(k for k in rt._FORGED if k in data and data.get(k) is not None)
        dropped += sorted(k for k in ("owner", "status") if k in data and k not in dropped)
        basis, rejected = [], []
        for b in data.get("basis") if isinstance(data.get("basis"), list) else []:
            if not isinstance(b, Mapping):
                continue
            cite = {
                "target": str(b.get("target") or ""),
                "step": b.get("step"),
                "quote": str(b.get("quote") or ""),
            }
            reason = check_citation(digest, cite)
            if reason is None:
                basis.append({k: v for k, v in cite.items() if v is not None})
            else:
                rejected.append(
                    {"target": cite["target"], "quote": cite["quote"], "reason": reason}
                )
        extra = {"rejected_basis": rejected, "dropped": sorted(set(dropped))}
        out.append(
            Term(
                c.display,
                (),
                definition.strip()[: rt.TEXT_MAX],
                None,
                "proposed",
                f"llm:{model}",
                None,
                None,
                tuple(basis),
                extra,
            )
        )
        rep.update(basis=len(basis), rejected_basis=len(rejected), dropped=sorted(set(dropped)))
        reports.append(rep)
    return out, reports


def _terms_obj(trace: Trace, terms: Sequence[Term], ignore: Sequence[str] | None) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "schema": GLOSSARY_SCHEMA,
        "source": trace.source,
        "terms": [t.to_dict() for t in terms],
    }
    if ignore is not None:
        obj["ignore"] = list(ignore)
    return obj


def draft(
    spec: Spec,
    trace: Trace,
    paths: NotesPaths,
    *,
    out: Path,
    generator: Any = None,
    model: str = "",
) -> dict[str, Any]:
    """Write the proposals to `out` (merged by term: an existing proposal is kept). Refuses an
    `out` that is the authoritative file. Deterministic without a generator."""
    if same_file(out, paths.glossary):
        raise NotesError("draft_out_is_authoritative", "--out is the authoritative file")
    existing, _ = load_terms_file(out, "proposals")
    have = {k for t in existing for k in t.keys}
    det = [t for t in draft_terms(spec, trace) if not set(t.keys) & have]
    new = list(det)
    reports: list[dict[str, Any]] = []
    if generator is not None:
        an = analyse(spec, trace)
        have |= {k for t in det for k in t.keys}
        more, reports = draft_with_model(spec, trace, an, have, generator, model)
        new += more
    if new:
        atomic_write_json(out, _terms_obj(trace, [*existing, *new], None))
    return {
        "out": str(out),
        "proposals": len(new),
        "extracted": sum(1 for t in new if t.author == "extracted"),
        "llm": sum(1 for t in new if t.author.startswith("llm:")),
        "approved": 0,
        "models": reports,
    }


# ---- approval (D4 a, mirrored) ------------------------------------------------------------


def _write_auth(paths: NotesPaths, trace: Trace, terms: list[Term], ignore: list[str]) -> None:
    atomic_write_json(paths.glossary, _terms_obj(trace, terms, ignore))


def approve_term(
    paths: NotesPaths,
    trace: Trace,
    *,
    from_path: Path,
    term: str,
    approver: str | None,
    owner: str | None = None,
    definition: str | None = None,
    retire: bool = False,
    prompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    human_guard("glossary approve")
    if same_file(from_path, paths.glossary):
        raise NotesError("approve_from_authoritative", "--from is the authoritative file")
    props, _ = load_terms_file(from_path, "proposals")
    key = norm_phrase(term)
    cands = [p for p in props if p.status == "proposed" and key in p.keys]
    if not cands:
        raise NotesError("no_proposal", f"{from_path.name} has no proposed term {term!r}")
    prop = cands[-1]
    new_def = prop.definition if definition is None else definition
    if not new_def.strip() or len(new_def) > DEFINITION_MAX:
        raise NotesError("bad_glossary", "the definition is empty or too long")
    owner_name = rt._need(owner, "owner (who owns this definition)", prompt)
    by = rt._need(approver, "approver", prompt)
    edited = new_def != prop.definition
    new = Term(
        prop.term,
        prop.aliases,
        new_def,
        owner_name,
        "approved",
        "owner" if edited else prop.author,
        by,
        term_sha(new_def, owner_name),
        prop.basis,
    )
    terms, ignore = load_terms_file(paths.glossary, "authoritative")
    terms = [t for t in terms if not set(t.keys) & set(new.keys)] + [new]
    _write_auth(paths, trace, terms, ignore)
    if retire:
        marked = [
            Term(
                p.term,
                p.aliases,
                p.definition,
                p.owner,
                "rejected" if p is prop else p.status,
                p.author,
                None,
                None,
                p.basis,
                {**p.extra, **({"reason": "superseded"} if p is prop else {})},
            )
            for p in props
        ]
        atomic_write_json(from_path, _terms_obj(trace, marked, None))
    return {"written": str(paths.glossary), "term": new.term, "retired": retire}


def add_term(
    paths: NotesPaths,
    trace: Trace,
    *,
    term: str | None,
    aliases: Sequence[str],
    definition: str | None,
    owner: str | None,
    approver: str | None,
    ignore: str | None = None,
    prompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    human_guard("glossary add")
    by = rt._need(approver, "approver", prompt)
    terms, ign = load_terms_file(paths.glossary, "authoritative")
    if ignore is not None:
        if not slug(ignore):
            raise NotesError("bad_glossary", "ignore is empty")
        if ignore not in ign:
            ign = [*ign, ignore]
        _write_auth(paths, trace, terms, ign)
        return {"written": str(paths.glossary), "ignore": ignore}
    if not term or not definition:
        raise NotesError("missing_value", "--term and --definition are required")
    if not definition.strip() or len(definition) > DEFINITION_MAX:
        raise NotesError("bad_glossary", "the definition is empty or too long")
    owner_name = rt._need(owner, "owner (who owns this definition)", prompt)
    new = Term(
        term,
        tuple(aliases),
        definition,
        owner_name,
        "approved",
        "owner",
        by,
        term_sha(definition, owner_name),
    )
    terms = [t for t in terms if not set(t.keys) & set(new.keys)] + [new]
    _write_auth(paths, trace, terms, ign)
    return {"written": str(paths.glossary), "term": term}


# ---- the CLI ------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec glossary",
        description="What the words of a rule mean: owner-approved terms and a deterministic lint.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(s: argparse.ArgumentParser, spec: bool = False) -> None:
        s.add_argument("--trace", type=Path, required=True, help="the spec's trace JSON")
        s.add_argument("--glossary", type=Path, help="the authoritative file (default: D1)")
        s.add_argument("--json", action="store_true")
        if spec:
            s.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")

    s = sub.add_parser("show", help="the approved terms and pending proposals")
    common(s)
    s = sub.add_parser("lint", help="G001-G004 (exit 0 clean, 1 findings, 2 cannot run)")
    common(s, spec=True)
    s.add_argument("--rationale", type=Path, help="the rationale file G003 reads (default: D1)")
    s = sub.add_parser("draft", help="proposals: deterministic, or a model for the marked terms")
    common(s, spec=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--model", help="also propose the remaining marked terms with this model")
    s = sub.add_parser("approve", help="approve one proposed term (owner, in a terminal)")
    common(s)
    s.add_argument("--from", dest="from_path", type=Path, required=True)
    s.add_argument("--term", required=True)
    s.add_argument("--approver")
    s.add_argument("--owner")
    s.add_argument("--definition")
    s.add_argument("--retire-proposal", action="store_true")
    s = sub.add_parser("add", help="add an owner-authored term or an ignore word")
    common(s)
    s.add_argument("--term")
    s.add_argument("--alias", action="append", default=[])
    s.add_argument("--definition")
    s.add_argument("--owner")
    s.add_argument("--approver")
    s.add_argument("--ignore")
    return p


def _load_spec(ref: str) -> Spec:
    return rt.load_spec_ref(ref)


def render_lint(res: LintResult) -> str:
    m = res.metrics
    out = []
    for f in res.findings:
        where = f["rule"] if "rule" in f else "-"
        out.append(f"{f['code']} {where} {f['message']}")
        if f["code"] in ("G001", "G003"):
            out.append(f"    fix: {f['fix']}")
    out.append(
        f"glossary: GU {m['GU']}, GT {m['GT']}, GR {m['GR']}, GC {m['GC']['n']}/{m['GC']['d']} "
        f"= {m['GC']['pct']}%, GA {m['GA']}, GP {m['GP']}, GX {m['GX']}"
    )
    return "\n".join(out) + "\n"


def render_show(g: GlossarySet | None) -> str:
    if g is None:
        return "glossary: no file (no approved term, no proposal)\n"
    out = [
        f"glossary: {g.file.name if g.file else '(no authoritative file)'}: "
        f"{len(g.terms)} approved term(s), {len(g.ignore)} ignored, "
        f"{sum(1 for p in g.proposals if p.status == 'proposed')} proposed"
    ]
    for t in g.terms:
        mark = "" if t.sha_ok else " [edited since approval: defines nothing]"
        also = f" (also: {', '.join(t.aliases)})" if t.aliases else ""
        out.append(f"{t.slug}: {t.term}{also}{mark}")
        out.append(f"    {t.definition}")
        out.append(f"    owner {t.owner}, approved by {t.approved_by}")
    for p in g.proposals:
        if p.status == "proposed":
            out.append(f"{p.slug} proposed ({p.author}; not approved): {p.definition}")
    if g.ignore:
        out.append("ignore: " + ", ".join(g.ignore))
    return "\n".join(out) + "\n"


def main(
    argv: Sequence[str] | None = None,
    *,
    generator: Any = None,
    prompt: Callable[[str], str] | None = input,
) -> int:
    """Exit 0 done / clean, 1 lint findings, 2 could not run or refused."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "glossary":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        trace = rt._load_trace(args.trace)
        paths = notes_paths(
            args.trace,
            glossary=args.glossary,
            rationale=getattr(args, "rationale", None),
            source=trace.source,
        )
        if paths is None:
            raise NotesError("no_trace", "the trace names no source")
        cmd = args.command
        if cmd == "show":
            g = load_glossary(paths)
            if args.json:
                body = {
                    "file": g.file.name if g and g.file else None,
                    "terms": [term_view(t) for t in g.defining] if g else [],
                    "edited": [t.term for t in g.edited] if g else [],
                    "ignore": g.ignore if g else [],
                    "proposals": [p.to_dict() for p in g.proposals] if g else [],
                }
                print(json.dumps(body, indent=2, ensure_ascii=False))
            else:
                sys.stdout.write(render_show(g))
            return 0
        if cmd == "lint":
            spec = _load_spec(args.spec)
            notes = load_notes(trace, paths)
            res = run_lint(spec, trace, notes, paths)
            if args.json:
                counts: dict[str, int] = {}
                for f in res.findings:
                    counts[f["code"]] = counts.get(f["code"], 0) + 1
                print(
                    json.dumps(
                        {"findings": res.findings, "metrics": res.metrics, "counts": counts},
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                sys.stdout.write(render_lint(res))
            return res.exit_code
        if cmd == "draft":
            spec = _load_spec(args.spec)
            gen = None
            if args.model:
                gen = generator
                if gen is None:
                    from lattice.llm.cli_client import ClaudeCliClient

                    gen = ClaudeCliClient(model=args.model, effort="", full_text=True)
            rep = draft(spec, trace, paths, out=args.out, generator=gen, model=args.model or "")
            print(
                json.dumps(rep, indent=2)
                if args.json
                else f"proposed {rep['proposals']} term(s) into {rep['out']} "
                f"({rep['extracted']} extracted, {rep['llm']} model); approved 0"
            )
            return 0
        if cmd == "approve":
            res_d = approve_term(
                paths,
                trace,
                from_path=args.from_path,
                term=args.term,
                approver=args.approver,
                owner=args.owner,
                definition=args.definition,
                retire=args.retire_proposal,
                prompt=prompt,
            )
        else:
            res_d = add_term(
                paths,
                trace,
                term=args.term,
                aliases=args.alias,
                definition=args.definition,
                owner=args.owner,
                approver=args.approver,
                ignore=args.ignore,
                prompt=prompt,
            )
        print(json.dumps(res_d, indent=2) if args.json else f"wrote {res_d['written']}")
        return 0
    except NotesError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- any failure is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


__all__ = [
    "Analysis",
    "Candidate",
    "GlossarySet",
    "LintResult",
    "Notes",
    "Term",
    "analyse",
    "discover_notes",
    "draft_terms",
    "key_of",
    "lint_glossary",
    "load_glossary",
    "load_notes",
    "main",
    "norm_phrase",
    "normal_text",
    "pct",
    "run_lint",
    "slug",
    "term_view",
    "terms_by_rule",
    "term_sha",
]


if __name__ == "__main__":
    sys.exit(main())
