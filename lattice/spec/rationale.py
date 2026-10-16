"""Rationale notes: why a rule exists, what it trades away, who decided it (Spike G).

A spec says *what* (REQUIREMENTS.md, spec.py, spec.trace.json). Nothing in those three files
records *why*. This module adds owner-held JSON files beside the requirements, a state for each
note against the current rules, deterministic coverage metrics, a drafter whose output is only
ever a *proposal*, and the owner's CLI approval that makes a note authoritative.

**Files** (decision D1; in the directory of the trace file, named from the trace's ``source``
stem, ``REQUIREMENTS.md`` giving ``REQUIREMENTS``):

==================================  ============================================================
``<stem>.rationale.json``           approved notes (authoritative), written only by ``rationale
                                    approve|add`` in a terminal, or by the owner's own editor
``<stem>.rationale.proposed.json``  proposals (drafted by a model, or by hand)
``<stem>.glossary.json``            approved terms and the ``ignore`` list
``<stem>.glossary.proposed.json``   term proposals
==================================  ============================================================

The trace is not used for them: ``Trace.from_json`` keeps only its own fields, so a key placed in
the trace is lost on the next draft or Studio accept.

**Schema** ``lattice.rationale/1`` (both rationale files)::

    {"schema": "lattice.rationale/1", "source": "REQUIREMENTS.md",
     "notes": [{"rule": "R63", "anchor": "<sha256 of the rule's normalised text>",
                "why": "1..600 chars", "tradeoff": "<= 600 chars or null", "owner": "...",
                "status": "approved | proposed | rejected", "author": "owner | llm:<m> | hand",
                "approved_by": "...", "approved_sha": "...",
                "basis": [{"target": "rule:R63", "quote": "..."}], "refs": []}]}

**States** (D3). An approved note resolves against the current rules, first match wins:
``edited`` (its ``approved_sha`` no longer matches its why/tradeoff/owner), ``current`` (the rule
with its id has its anchor), ``moved`` (exactly one rule has its anchor), ``ambiguous`` (two or
more do), ``stale`` (none). Only ``current`` and ``moved`` count as approved anywhere.

**Never** (D4, D15): a model's text is never authoritative; no read path writes anything; the four
approval commands refuse without a terminal or with ``CLAUDECODE`` set (a speed bump for agents,
not a security boundary); each writes exactly one file, atomically.

CLI (``python -m lattice.spec rationale``)::

    show --trace T [--rationale P] [--rule R] [--json]
    coverage --trace T [--json]
    draft --trace T --spec MOD (--rule R ... | --unexplained [--limit N]) --out FILE [--model M]
    approve --trace T --from PROPOSALS --rule R --approver NAME [--owner NAME] [--why T]
            [--tradeoff T] [--replace] [--retire-proposal]
    add --trace T --rule R --why T [--tradeoff T] --owner NAME --approver NAME [--replace]
    reject --trace T --from PROPOSALS --rule R [--reason T]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.spec.rules import Trace

RATIONALE_SCHEMA = "lattice.rationale/1"
GLOSSARY_SCHEMA = "lattice.glossary/1"
TEXT_MAX = 600  # why and tradeoff
STATES = ("current", "moved", "edited", "ambiguous", "stale")
APPROVED_STATES = ("current", "moved")
NOTE_STATUSES = ("approved", "proposed", "rejected")
# the order in which a rule's notes decide the rule's one state
_STATE_ORDER = ("current", "moved", "edited", "ambiguous", "stale")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RULE_ID = re.compile(r"^R(\d+)$")


class NotesError(Exception):
    """A refusal or an invalid file: ``code`` (``bad_rationale``, ``bad_glossary``,
    ``approve_needs_terminal``, ``unknown_rule`` ...) and ``detail``; ``str()`` is
    ``code: detail``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ---- text, anchors, hashes ----------------------------------------------------------------


def norm_text(text: str) -> str:
    """Whitespace runs collapsed to one space, stripped."""
    return " ".join(str(text).split())


def anchor_of(text: str) -> str:
    """The anchor of a rule: the sha256 (hex) of its normalised text."""
    return hashlib.sha256(norm_text(text).encode("utf-8")).hexdigest()


def _sha_of(obj: Mapping[str, Any]) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def approved_sha(why: str, tradeoff: str | None, owner: str | None) -> str:
    """The sha256 of ``{why, tradeoff, owner}`` at approval (D2)."""
    return _sha_of({"why": why, "tradeoff": tradeoff, "owner": owner})


def pct(n: int, d: int) -> float:
    """``n/d`` as a percentage rounded half up to one decimal, in integers (D8)."""
    return 0.0 if d == 0 else (1000 * n + d // 2) // d / 10


def ratio(n: int, d: int) -> dict[str, Any]:
    return {"n": n, "d": d, "pct": pct(n, d)}


def rule_number(rule_id: str) -> int:
    m = _RULE_ID.match(rule_id)
    return int(m.group(1)) if m else 10**9


# ---- where the files live (D1) ------------------------------------------------------------


def proposals_sibling(path: Path) -> Path:
    """``X.rationale.json`` -> ``X.rationale.proposed.json``."""
    name = path.name
    stem = name[: -len(".json")] if name.endswith(".json") else name
    return path.with_name(stem + ".proposed.json")


@dataclass(frozen=True)
class NotesPaths:
    """The four files' paths (any of them may not exist)."""

    rationale: Path
    rationale_proposed: Path
    glossary: Path
    glossary_proposed: Path

    def existing(self) -> list[Path]:
        return [
            p
            for p in (
                self.rationale,
                self.rationale_proposed,
                self.glossary,
                self.glossary_proposed,
            )
            if p.is_file()
        ]

    @property
    def has_rationale(self) -> bool:
        return self.rationale.is_file() or self.rationale_proposed.is_file()

    @property
    def has_glossary(self) -> bool:
        return self.glossary.is_file() or self.glossary_proposed.is_file()

    @property
    def any(self) -> bool:
        return self.has_rationale or self.has_glossary


def notes_names(source: str) -> tuple[str, str, str, str]:
    """The four file names for a trace ``source`` (``REQUIREMENTS.md`` -> ``REQUIREMENTS``)."""
    stem = Path(source).stem
    return (
        f"{stem}.rationale.json",
        f"{stem}.rationale.proposed.json",
        f"{stem}.glossary.json",
        f"{stem}.glossary.proposed.json",
    )


_SOURCE_CACHE: dict[str, tuple[tuple[int, int], str | None]] = {}


def trace_source(trace_path: Path) -> str | None:
    """The ``source`` of the trace file at `trace_path` (None: no file, not JSON, no source).
    Cached by the file's mtime and size, since Studio asks on every request."""
    try:
        st = trace_path.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _SOURCE_CACHE.get(str(trace_path))
    if hit is not None and hit[0] == key:
        return hit[1]
    source: str | None
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
        source = data.get("source") if isinstance(data, dict) else None
        source = source if isinstance(source, str) and source else None
    except (OSError, ValueError):
        source = None
    _SOURCE_CACHE[str(trace_path)] = (key, source)
    return source


def notes_paths(
    trace_path: Path | str,
    *,
    rationale: Path | str | None = None,
    glossary: Path | str | None = None,
    source: str | None = None,
) -> NotesPaths | None:
    """The four files for the trace at `trace_path` (D1). ``rationale`` / ``glossary`` override
    the authoritative path, and imply the ``.proposed.json`` sibling. None: no override and the
    trace names no source."""
    tp = Path(trace_path)
    src = source if source is not None else trace_source(tp)
    names = notes_names(src) if src else None
    if names is None and rationale is None and glossary is None:
        return None
    d = tp.parent
    none = d / "(no source).json"  # an override given while the trace names no source
    r = Path(rationale) if rationale is not None else (d / names[0] if names else none)
    g = Path(glossary) if glossary is not None else (d / names[2] if names else none)
    rp = proposals_sibling(r) if rationale is not None else (d / names[1] if names else none)
    gp = proposals_sibling(g) if glossary is not None else (d / names[3] if names else none)
    return NotesPaths(r, rp, g, gp)


# ---- notes --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    rule: str
    anchor: str
    why: str
    tradeoff: str | None
    owner: str | None
    status: str
    author: str
    approved_by: str | None = None
    approved_sha: str | None = None
    basis: tuple[dict[str, Any], ...] = ()
    refs: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "rule": self.rule,
            "anchor": self.anchor,
            "why": self.why,
            "tradeoff": self.tradeoff,
            "owner": self.owner,
            "status": self.status,
            "author": self.author,
            "approved_by": self.approved_by,
            "approved_sha": self.approved_sha,
            "basis": [dict(b) for b in self.basis],
            "refs": list(self.refs),
        }
        d.update(self.extra)
        return d

    @property
    def sha_ok(self) -> bool:
        return self.approved_sha == approved_sha(self.why, self.tradeoff, self.owner)


_EXTRA_KEYS = ("reason", "rejected_basis", "dropped")


def _bad(detail: str) -> NotesError:
    return NotesError("bad_rationale", detail)


def _opt_str(d: Mapping[str, Any], key: str, where: str) -> str | None:
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise _bad(f"{where}: {key} must be a string or null")
    return v


def _parse_note(d: Any, i: int, kind: str) -> Note:
    if not isinstance(d, dict):
        raise _bad(f"notes[{i}] is not an object")
    rule = d.get("rule")
    if not isinstance(rule, str) or not rule:
        raise _bad(f"notes[{i}]: rule must be a non-empty string")
    where = f"notes[{i}] ({rule})"
    anchor = d.get("anchor")
    if not isinstance(anchor, str) or not _HEX64.match(anchor):
        raise _bad(f"{where}: anchor must be 64 lowercase hex characters")
    why = d.get("why")
    if not isinstance(why, str) or not why.strip():
        raise _bad(f"{where}: why is empty")
    if len(why) > TEXT_MAX:
        raise _bad(f"{where}: why is longer than {TEXT_MAX} characters")
    tradeoff = _opt_str(d, "tradeoff", where)
    if tradeoff is not None and len(tradeoff) > TEXT_MAX:
        raise _bad(f"{where}: tradeoff is longer than {TEXT_MAX} characters")
    owner = _opt_str(d, "owner", where)
    status = d.get("status")
    if status not in NOTE_STATUSES:
        raise _bad(f"{where}: status must be one of {', '.join(NOTE_STATUSES)}")
    author = d.get("author")
    if not isinstance(author, str) or not (
        author in ("owner", "hand") or (author.startswith("llm:") and len(author) > 4)
    ):
        raise _bad(f"{where}: author must be owner, hand or llm:<model>")
    approved_by = _opt_str(d, "approved_by", where)
    sha = _opt_str(d, "approved_sha", where)
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
    refs_raw = d.get("refs", [])
    if not isinstance(refs_raw, list) or not all(isinstance(r, str) for r in refs_raw):
        raise _bad(f"{where}: refs must be a list of strings")
    if kind == "authoritative":
        if status != "approved":
            raise _bad(f"{where}: the authoritative file holds only approved notes, not {status}")
        for key, v in (("owner", owner), ("approved_by", approved_by), ("approved_sha", sha)):
            if not v or not v.strip():
                raise _bad(f"{where}: an approved note needs a non-empty {key}")
    else:
        if status == "approved":
            raise _bad(f"{where}: a proposals file cannot hold an approved note")
        if approved_by is not None or sha is not None:
            raise _bad(f"{where}: a proposal has no approved_by or approved_sha")
        if author.startswith("llm:") and owner is not None:
            raise _bad(f"{where}: a model never names the owner")
    extra = {k: d[k] for k in _EXTRA_KEYS if k in d}
    return Note(
        rule,
        anchor,
        why,
        tradeoff,
        owner,
        status,
        author,
        approved_by,
        sha,
        tuple(basis),
        tuple(refs_raw),
        extra,
    )


def _read_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise NotesError(code, f"cannot read {path.name}: {e.strerror or e}") from None
    except ValueError as e:
        raise NotesError(code, f"{path.name} is not valid JSON ({e})") from None


def load_notes_file(path: Path | None, kind: str) -> list[Note]:
    """The notes of `path` (kind ``authoritative`` or ``proposals``); ``[]`` when `path` is None
    or not a file. Raises :class:`NotesError` ``bad_rationale`` for an invalid file (D14)."""
    if path is None or not path.is_file():
        return []
    data = _read_json(path, "bad_rationale")
    if not isinstance(data, dict):
        raise _bad(f"{path.name}: the top level is not an object")
    if data.get("schema") != RATIONALE_SCHEMA:
        raise _bad(f"{path.name}: schema must be {RATIONALE_SCHEMA!r}, not {data.get('schema')!r}")
    raw = data.get("notes")
    if not isinstance(raw, list):
        raise _bad(f"{path.name}: notes must be a list")
    notes = []
    seen: set[str] = set()
    for i, d in enumerate(raw):
        n = _parse_note(d, i, kind)
        if kind == "authoritative":
            if n.rule in seen:
                raise _bad(f"{path.name}: duplicate approved note for {n.rule}")
            seen.add(n.rule)
        notes.append(n)
    return notes


# ---- resolution (D3) ----------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """An approved note against the current rules: its `state`, the `rule` it applies to now
    (the note's own id unless ``moved``) and the rules whose text has its anchor."""

    note: Note
    state: str
    rule: str
    matches: tuple[str, ...] = ()


def rule_anchors(trace: Trace) -> dict[str, str]:
    return {r.id: anchor_of(r.text) for r in trace.rules}


def resolve(trace: Trace, notes: Sequence[Note]) -> list[Resolved]:
    """Each approved note's state against `trace` (D3), in the notes' order."""
    anchors = rule_anchors(trace)
    by_anchor: dict[str, list[str]] = {}
    for rid, a in anchors.items():
        by_anchor.setdefault(a, []).append(rid)
    out = []
    for n in notes:
        hits = tuple(by_anchor.get(n.anchor, ()))
        if not n.sha_ok:
            out.append(Resolved(n, "edited", n.rule, hits))
        elif anchors.get(n.rule) == n.anchor:
            out.append(Resolved(n, "current", n.rule, hits))
        elif len(hits) == 1:
            out.append(Resolved(n, "moved", hits[0], hits))
        elif len(hits) >= 2:
            out.append(Resolved(n, "ambiguous", n.rule, hits))
        else:
            out.append(Resolved(n, "stale", n.rule, ()))
    return out


@dataclass
class RationaleSet:
    """The rationale of one project: the approved notes with their states, the proposals."""

    trace: Trace
    file: Path | None  # the authoritative file when it exists
    proposals_file: Path | None  # the proposals file when it exists
    notes: list[Note]
    proposals: list[Note]
    resolved: list[Resolved]
    _by_rule: dict[str, list[Resolved]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for r in self.resolved:
            self._by_rule.setdefault(r.rule, []).append(r)

    def approved_for(self, rule_id: str) -> Resolved | None:
        """The current-or-moved note of `rule_id`, or None."""
        for r in self._by_rule.get(rule_id, ()):
            if r.state in APPROVED_STATES:
                return r
        return None

    def rule_state(self, rule_id: str) -> str:
        """``current|moved|edited|ambiguous|stale`` (the best note of the rule),
        ``proposed_only`` or ``none``."""
        have = {r.state for r in self._by_rule.get(rule_id, ())}
        for s in _STATE_ORDER:
            if s in have:
                return s
        if any(p.rule == rule_id and p.status == "proposed" for p in self.proposals):
            return "proposed_only"
        return "none"

    def status(self, rule_id: str) -> str:
        """The chat's word for the rule: ``approved`` (current or moved), ``proposed_only``,
        ``stale``, ``ambiguous``, ``edited`` or ``none``."""
        s = self.rule_state(rule_id)
        return "approved" if s in APPROVED_STATES else s

    def metrics(self) -> dict[str, Any]:
        """The D8 rationale metrics (``file`` is the file's name, None when absent)."""
        context = set(self.trace.context)
        rules = [r.id for r in self.trace.rules]
        scope = [r for r in rules if r not in context]
        approved = {r for r in rules if self.approved_for(r) is not None}
        counts = dict.fromkeys(STATES, 0)
        for r in self.resolved:
            counts[r.state] += 1
        rp = sum(1 for r in rules if self.rule_state(r) == "proposed_only")
        return {
            "file": self.file.name if self.file else None,
            "proposals_file": self.proposals_file.name if self.proposals_file else None,
            "RC": ratio(sum(1 for r in scope if r in approved), len(scope)),
            "RC_all": ratio(len(approved), len(rules)),
            "RN": counts,
            "RP": rp,
            "proposals": sum(1 for p in self.proposals if p.status == "proposed"),
        }


def load_set(trace: Trace, paths: NotesPaths | None) -> RationaleSet | None:
    """The :class:`RationaleSet` of `paths`; None when neither the authoritative nor the
    proposals file exists. Raises :class:`NotesError` for an invalid file."""
    if paths is None or not paths.has_rationale:
        return None
    notes = load_notes_file(paths.rationale, "authoritative")
    proposals = load_notes_file(paths.rationale_proposed, "proposals")
    return RationaleSet(
        trace,
        paths.rationale if paths.rationale.is_file() else None,
        paths.rationale_proposed if paths.rationale_proposed.is_file() else None,
        notes,
        proposals,
        resolve(trace, notes),
    )


def empty_set(trace: Trace) -> RationaleSet:
    """A set with no file (metrics with ``file: null``)."""
    return RationaleSet(trace, None, None, [], [], [])


def note_view(r: Resolved, *, text: bool = True) -> dict[str, Any]:
    """A resolved note for display. Text (why, tradeoff, owner, approved_by) only for a current
    or moved note: any other state is never shown as the owner's reason."""
    d: dict[str, Any] = {
        "rule": r.note.rule,
        "state": r.state,
        "applies_to": r.rule,
        "author": r.note.author,
    }
    if r.state == "moved":
        d["moved_to"] = r.rule
    if r.state in ("ambiguous", "stale", "edited"):
        d["matches"] = list(r.matches)
    if text and r.state in APPROVED_STATES:
        d.update(
            why=r.note.why,
            tradeoff=r.note.tradeoff,
            owner=r.note.owner,
            approved_by=r.note.approved_by,
        )
    return d


# ---- writing: the human guard and atomic writes (D4) --------------------------------------


def _terminal() -> bool:
    """Whether stdin and stdout are terminals (replaced in tests)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def human_guard(command: str) -> None:
    """Refuse (``approve_needs_terminal``) unless a person is at a terminal: stdin and stdout
    must be TTYs and ``CLAUDECODE`` must not be set. A speed bump against an agent's Bash tool
    approving by accident, not a security boundary (D4 b)."""
    if os.environ.get("CLAUDECODE"):
        raise NotesError(
            "approve_needs_terminal",
            f"{command} is for the owner: it refuses inside a Claude Code session "
            "(CLAUDECODE is set). Run it yourself in a terminal.",
        )
    if not _terminal():
        raise NotesError(
            "approve_needs_terminal",
            f"{command} needs a terminal on stdin and stdout; it never runs from a script or "
            "an agent.",
        )


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write `obj` as JSON to `path`: a temporary file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _notes_file_obj(trace: Trace, notes: Sequence[Note]) -> dict[str, Any]:
    ordered = sorted(notes, key=lambda n: (rule_number(n.rule), n.rule))
    return {
        "schema": RATIONALE_SCHEMA,
        "source": trace.source,
        "notes": [n.to_dict() for n in ordered],
    }


def _rule(trace: Trace, rule_id: str) -> Any:
    for r in trace.rules:
        if r.id == rule_id:
            return r
    raise NotesError("unknown_rule", f"the trace has no rule {rule_id}")


def _need(value: str | None, what: str, prompt: Callable[[str], str] | None) -> str:
    v = (value or "").strip()
    if not v and prompt is not None:
        v = prompt(f"{what}: ").strip()
    if not v:
        raise NotesError("missing_value", f"{what} is required")
    return v


def _check_text(why: str, tradeoff: str | None) -> None:
    if not why.strip():
        raise NotesError("bad_rationale", "why is empty")
    if len(why) > TEXT_MAX:
        raise NotesError("bad_rationale", f"why is longer than {TEXT_MAX} characters")
    if tradeoff is not None and len(tradeoff) > TEXT_MAX:
        raise NotesError("bad_rationale", f"tradeoff is longer than {TEXT_MAX} characters")


def _install(paths: NotesPaths, trace: Trace, note: Note, replace: bool) -> tuple[Path, bool]:
    """Put `note` in the authoritative file (atomically); the path and whether it replaced one."""
    existing = load_notes_file(paths.rationale, "authoritative")
    old = next((n for n in existing if n.rule == note.rule), None)
    replaced = old is not None
    if old is not None:
        state = next(r.state for r in resolve(trace, [old]))
        if state in APPROVED_STATES and not replace:
            raise NotesError(
                "rationale_exists",
                f"{note.rule} already has a current approved note; pass --replace to replace it",
            )
    notes = [n for n in existing if n.rule != note.rule] + [note]
    atomic_write_json(paths.rationale, _notes_file_obj(trace, notes))
    return paths.rationale, replaced


def approve(
    paths: NotesPaths,
    trace: Trace,
    *,
    from_path: Path,
    rule: str,
    approver: str | None,
    owner: str | None = None,
    why: str | None = None,
    tradeoff: str | None = None,
    replace: bool = False,
    retire: bool = False,
    prompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Copy one proposal of `rule` from `from_path` into the authoritative file (D4 a)."""
    human_guard("rationale approve")
    if same_file(from_path, paths.rationale):
        raise NotesError("approve_from_authoritative", "--from is the authoritative file")
    target = _rule(trace, rule)
    props = load_notes_file(from_path, "proposals")
    cands = [p for p in props if p.rule == rule and p.status == "proposed"]
    if not cands:
        raise NotesError("no_proposal", f"{from_path.name} has no proposed note for {rule}")
    prop = cands[-1]
    if prop.anchor != anchor_of(target.text):
        raise NotesError(
            "proposal_stale",
            f"the wording of {rule} changed since this proposal was drafted; draft it again",
        )
    new_why = prop.why if why is None else why
    new_trade = prop.tradeoff if tradeoff is None else tradeoff
    _check_text(new_why, new_trade)
    edited = new_why != prop.why or new_trade != prop.tradeoff
    owner_name = _need(owner, "owner (who owns this decision)", prompt)
    by = _need(approver, "approver", prompt)
    note = Note(
        rule,
        prop.anchor,
        new_why,
        new_trade,
        owner_name,
        "approved",
        "owner" if edited else prop.author,
        by,
        approved_sha(new_why, new_trade, owner_name),
        tuple(prop.basis),
        tuple(prop.refs),
    )
    path, replaced = _install(paths, trace, note, replace)
    retired = False
    if retire:
        marked = [
            Note(
                p.rule,
                p.anchor,
                p.why,
                p.tradeoff,
                p.owner,
                "rejected" if p is prop else p.status,
                p.author,
                None,
                None,
                p.basis,
                p.refs,
                {**p.extra, **({"reason": "superseded"} if p is prop else {})},
            )
            for p in props
        ]
        atomic_write_json(from_path, _notes_file_obj(trace, marked))
        retired = True
    return {
        "written": str(path),
        "rule": rule,
        "replaced": replaced,
        "retired": retired,
        "approved_sha": note.approved_sha,
        "author": note.author,
    }


def add(
    paths: NotesPaths,
    trace: Trace,
    *,
    rule: str,
    why: str,
    tradeoff: str | None,
    owner: str | None,
    approver: str | None,
    replace: bool = False,
    prompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Write an owner-authored note directly (D4 a)."""
    human_guard("rationale add")
    target = _rule(trace, rule)
    _check_text(why, tradeoff)
    owner_name = _need(owner, "owner (who owns this decision)", prompt)
    by = _need(approver, "approver", prompt)
    note = Note(
        rule,
        anchor_of(target.text),
        why,
        tradeoff,
        owner_name,
        "approved",
        "owner",
        by,
        approved_sha(why, tradeoff, owner_name),
    )
    path, replaced = _install(paths, trace, note, replace)
    return {
        "written": str(path),
        "rule": rule,
        "replaced": replaced,
        "retired": False,
        "approved_sha": note.approved_sha,
        "author": "owner",
    }


def reject(
    paths: NotesPaths, trace: Trace, *, from_path: Path, rule: str, reason: str | None
) -> dict[str, Any]:
    """Mark the last proposal of `rule` rejected (writes the proposals file only)."""
    if same_file(from_path, paths.rationale):
        raise NotesError("approve_from_authoritative", "--from is the authoritative file")
    _rule(trace, rule)
    props = load_notes_file(from_path, "proposals")
    idx = [i for i, p in enumerate(props) if p.rule == rule and p.status == "proposed"]
    if not idx:
        raise NotesError("no_proposal", f"{from_path.name} has no proposed note for {rule}")
    out = []
    for i, p in enumerate(props):
        if i == idx[-1]:
            p = Note(
                p.rule,
                p.anchor,
                p.why,
                p.tradeoff,
                p.owner,
                "rejected",
                p.author,
                None,
                None,
                p.basis,
                p.refs,
                {**p.extra, "reason": reason or "rejected"},
            )
        out.append(p)
    atomic_write_json(from_path, {**_notes_file_obj(trace, out)})
    return {"written": str(from_path), "rule": rule}


# ---- drafting (D9): proposals only --------------------------------------------------------

_DRAFT_RULE = (
    "You do not know the owner's reason. Propose a short why and trade-off; cite the spec text "
    "you lean on; if nothing in the spec states a reason, give basis []; never name an owner."
)
DRAFT_SYSTEM = (
    "You propose a possible reason for ONE rule of an application's requirements, using only "
    "the spec text you are given.\n"
    + _DRAFT_RULE
    + """

Reply with ONE JSON object and nothing else:
{"why": "1-600 characters", "tradeoff": "up to 600 characters, or null",
 "basis": [{"target": "<exact target from the spec text>", "quote": "<text copied from it>"}]}
A target looks like rule:R12, scenario:name, invariant:name, machine:Table.column, service:name
or unchecked:1. A quote is copied from that element and checked mechanically; a quote that does
not match is dropped.
"""
)

_FORGED = ("owner", "status", "approved_by", "approved_sha", "author", "anchor", "rule")


def draft_context(spec: Any, trace: Trace, digest: Any, rule_id: str) -> str:
    """The user message for one rule: its text and section, its links as digest lines, the
    questions that name it and the other rules of its section."""
    r = _rule(trace, rule_id)
    lines = [f"Rule {r.id} (section: {r.section or '-'}):", r.text, ""]
    links = list(trace.links.get(rule_id, ()))
    if links:
        lines.append("Spec elements that check it:")
        lines += digest.lines_for(links)
        lines.append("")
    if rule_id in trace.unchecked:
        lines += [f"Not checked because: {trace.unchecked[rule_id]}", ""]
    qs = [q for q in trace.questions if rule_id in q.rules]
    if qs:
        lines.append("Open questions that name it:")
        for q in qs:
            ans = q.answer if q.answer is not None else "(unanswered)"
            lines.append(f"- {q.id}: {q.text} default: {q.default} answer: {ans}")
        lines.append("")
    others = [x for x in trace.rules if x.section == r.section and x.id != rule_id]
    if others:
        lines.append("Other rules of the same section:")
        lines += [f"- {x.id}: {x.text}" for x in others[:30]]
        lines.append("")
    lines.append("Reply with the JSON object only.")
    return "\n".join(lines)


def draft_one(
    spec: Any, trace: Trace, digest: Any, rule_id: str, generator: Any, model: str
) -> tuple[Note | None, dict[str, Any]]:
    """One model call for `rule_id`. Returns (the proposal or None, a report dict). The
    entry is always ``proposed`` with ``owner`` null and author ``llm:<model>``: a forged
    owner or status in the reply is dropped and noted."""
    from lattice.spec.ask import _json_object, check_citation

    r = _rule(trace, rule_id)
    gen = generator.generate(
        DRAFT_SYSTEM, [{"role": "user", "content": draft_context(spec, trace, digest, rule_id)}]
    )
    report: dict[str, Any] = {
        "rule": rule_id,
        "cost_usd": getattr(gen, "cost_usd", None),
        "tokens": (gen.usage.total_input_tokens + gen.usage.output_tokens) if gen.usage else 0,
    }
    try:
        data = _json_object(gen.text)
    except (ValueError, json.JSONDecodeError) as err:
        report["error"] = f"the reply is not one JSON object ({err})"
        return None, report
    if not isinstance(data, dict):
        report["error"] = "the reply is not a JSON object"
        return None, report
    why = data.get("why")
    if not isinstance(why, str) or not why.strip():
        report["error"] = "the reply has no why"
        return None, report
    dropped = sorted(k for k in _FORGED if k in data and data.get(k) is not None)
    if len(why) > TEXT_MAX:
        why = why[:TEXT_MAX]
        dropped.append("why_truncated")
    trade = data.get("tradeoff")
    trade = trade if isinstance(trade, str) and trade.strip() else None
    if trade is not None and len(trade) > TEXT_MAX:
        trade = trade[:TEXT_MAX]
        dropped.append("tradeoff_truncated")
    basis, rejected = [], []
    raw_basis = data.get("basis")
    for b in raw_basis if isinstance(raw_basis, list) else []:
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
            rejected.append({"target": cite["target"], "quote": cite["quote"], "reason": reason})
    note = Note(
        rule_id,
        anchor_of(r.text),
        why.strip(),
        trade,
        None,
        "proposed",
        f"llm:{model}",
        None,
        None,
        tuple(basis),
        (),
        {"rejected_basis": rejected, "dropped": dropped},
    )
    report["basis"] = len(basis)
    report["rejected_basis"] = len(rejected)
    report["dropped"] = dropped
    return note, report


def draft(
    spec: Any,
    trace: Trace,
    paths: NotesPaths,
    *,
    rules: Sequence[str],
    out: Path,
    generator: Any,
    model: str,
) -> dict[str, Any]:
    """Draft proposals for `rules` into `out` (merged by rule: existing ones are kept, new ones
    appended). Refuses an `out` that is the authoritative file. Nothing else is written."""
    from lattice.spec.ask import build_digest

    if same_file(out, paths.rationale):
        raise NotesError("draft_out_is_authoritative", "--out is the authoritative file")
    for rid in rules:
        _rule(trace, rid)
    existing = load_notes_file(out, "proposals")
    digest = build_digest(spec, trace)
    new, reports = [], []
    stopped = None
    for rid in rules:
        try:
            note, rep = draft_one(spec, trace, digest, rid, generator, model)
        except Exception as err:  # noqa: BLE001 -- a usage limit or a CLI failure ends the run
            stopped = f"{rid}: {type(err).__name__}: {err}"
            break
        reports.append(rep)
        if note is not None:
            new.append(note)
            # written after every rule, so a limit hit midway keeps what was drafted
            atomic_write_json(out, _notes_file_obj(trace, [*existing, *new]))
    return {
        "out": str(out),
        "drafted": len(new),
        "rules": reports,
        "cost_usd": round(sum(r["cost_usd"] or 0.0 for r in reports), 6),
        "stopped": stopped,
    }


# ---- the CLI ------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec rationale",
        description="Why a rule exists: owner-approved notes, coverage, drafts as proposals.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(s: argparse.ArgumentParser, spec: bool = False) -> None:
        s.add_argument("--trace", type=Path, required=True, help="the spec's trace JSON")
        s.add_argument("--rationale", type=Path, help="the authoritative file (default: D1)")
        s.add_argument("--json", action="store_true")
        if spec:
            s.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")

    s = sub.add_parser("show", help="the notes with their states")
    common(s)
    s.add_argument("--rule")
    s = sub.add_parser("coverage", help="the deterministic coverage metrics")
    common(s)
    s = sub.add_parser("draft", help="draft proposals with a model (never authoritative)")
    common(s, spec=True)
    s.add_argument("--rule", action="append", default=[])
    s.add_argument("--unexplained", action="store_true")
    s.add_argument("--limit", type=int)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--model", default="sonnet")
    s = sub.add_parser("approve", help="approve one proposal (owner, in a terminal)")
    common(s)
    s.add_argument("--from", dest="from_path", type=Path, required=True)
    s.add_argument("--rule", required=True)
    s.add_argument("--approver")
    s.add_argument("--owner")
    s.add_argument("--why")
    s.add_argument("--tradeoff")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--retire-proposal", action="store_true")
    s = sub.add_parser("add", help="write an owner-authored note (owner, in a terminal)")
    common(s)
    s.add_argument("--rule", required=True)
    s.add_argument("--why", required=True)
    s.add_argument("--tradeoff")
    s.add_argument("--owner")
    s.add_argument("--approver")
    s.add_argument("--replace", action="store_true")
    s = sub.add_parser("reject", help="mark a proposal rejected")
    common(s)
    s.add_argument("--from", dest="from_path", type=Path, required=True)
    s.add_argument("--rule", required=True)
    s.add_argument("--reason")
    return p


def render_show(rs: RationaleSet | None, rule: str | None = None) -> str:
    if rs is None:
        return "rationale: no file (nothing approved, nothing proposed)\n"
    m = rs.metrics()
    out = [
        f"rationale: {m['file'] or '(no authoritative file)'}: "
        f"{len(rs.notes)} approved note(s); proposals {m['proposals']}",
        f"coverage: RC {m['RC']['n']}/{m['RC']['d']} = {m['RC']['pct']}% "
        f"(all rules {m['RC_all']['n']}/{m['RC_all']['d']}); "
        + ", ".join(f"{k} {v}" for k, v in m["RN"].items()),
    ]
    for r in rs.resolved:
        if rule and rule not in (r.note.rule, r.rule):
            continue
        v = note_view(r)
        head = f"{r.note.rule} {r.state}"
        if r.state == "moved":
            head += f" -> {r.rule}"
        out.append(head)
        if "why" in v:
            out.append(f"    why: {v['why']}")
            if v["tradeoff"]:
                out.append(f"    trade-off: {v['tradeoff']}")
            out.append(f"    owner {v['owner']}, approved by {v['approved_by']}")
        elif r.matches:
            out.append(f"    anchor matches: {', '.join(r.matches)}")
    for p in rs.proposals:
        if rule and p.rule != rule:
            continue
        out.append(f"{p.rule} {p.status} ({p.author}; not the owner's reason)")
        out.append(f"    why: {p.why}")
        if p.tradeoff:
            out.append(f"    trade-off: {p.tradeoff}")
    return "\n".join(out) + "\n"


def load_spec_ref(ref: str) -> Any:
    """A Spec from ``module:ATTR`` or a spec module file (its ``SPEC``), as ``lint`` takes it."""
    from lattice.spec.lint import LintError, _load_spec

    try:
        return _load_spec(ref)[0]
    except (LintError, ImportError, AttributeError, OSError) as e:
        raise NotesError("no_spec", f"{ref}: {type(e).__name__}: {e}") from None


def _load_trace(path: Path) -> Trace:
    try:
        return Trace.load(path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise NotesError("no_trace", f"cannot read {path}: {e}") from None


def main(
    argv: Sequence[str] | None = None,
    *,
    generator: Any = None,
    prompt: Callable[[str], str] | None = input,
) -> int:
    """Exit 0 done, 2 could not run or refused. ``generator`` replaces the CLI client (tests)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "rationale":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        trace = _load_trace(args.trace)
        paths = notes_paths(args.trace, rationale=args.rationale, source=trace.source)
        if paths is None:
            raise NotesError("no_trace", "the trace names no source")
        cmd = args.command
        if cmd == "show":
            rs = load_set(trace, paths)
            if args.json:
                body = {
                    "file": rs.file.name if rs and rs.file else None,
                    "metrics": (rs or empty_set(trace)).metrics(),
                    "notes": [note_view(r) for r in rs.resolved] if rs else [],
                    "proposals": [p.to_dict() for p in rs.proposals] if rs else [],
                }
                print(json.dumps(body, indent=2, ensure_ascii=False))
            else:
                sys.stdout.write(render_show(rs, args.rule))
            return 0
        if cmd == "coverage":
            m = (load_set(trace, paths) or empty_set(trace)).metrics()
            if args.json:
                print(json.dumps({"metrics": m}, indent=2))
            else:
                print(
                    f"RC {m['RC']['n']}/{m['RC']['d']} = {m['RC']['pct']}%; RC_all "
                    f"{m['RC_all']['n']}/{m['RC_all']['d']} = {m['RC_all']['pct']}%; "
                    + ", ".join(f"{k} {v}" for k, v in m["RN"].items())
                    + f"; proposed_only {m['RP']}; file {m['file']}"
                )
            return 0
        if cmd == "draft":
            spec = load_spec_ref(args.spec)
            rs = load_set(trace, paths) or empty_set(trace)
            rules = list(args.rule)
            if args.unexplained:
                ctx = set(trace.context)
                rules += [
                    r.id for r in trace.rules if r.id not in ctx and rs.rule_state(r.id) == "none"
                ][: args.limit if args.limit else None]
            if not rules:
                raise NotesError("no_rules", "give --rule R or --unexplained")
            if generator is None:
                from lattice.llm.cli_client import ClaudeCliClient

                generator = ClaudeCliClient(model=args.model, effort="", full_text=True)
            rep = draft(
                spec,
                trace,
                paths,
                rules=rules,
                out=args.out,
                generator=generator,
                model=args.model,
            )
            print(json.dumps(rep, indent=2) if args.json else _render_draft(rep))
            if rep["stopped"]:
                print(f"error: stopped at {rep['stopped']}", file=sys.stderr)
                return 2
            return 0
        if cmd == "approve":
            res = approve(
                paths,
                trace,
                from_path=args.from_path,
                rule=args.rule,
                approver=args.approver,
                owner=args.owner,
                why=args.why,
                tradeoff=args.tradeoff,
                replace=args.replace,
                retire=args.retire_proposal,
                prompt=prompt,
            )
        elif cmd == "add":
            res = add(
                paths,
                trace,
                rule=args.rule,
                why=args.why,
                tradeoff=args.tradeoff,
                owner=args.owner,
                approver=args.approver,
                replace=args.replace,
                prompt=prompt,
            )
        else:
            res = reject(paths, trace, from_path=args.from_path, rule=args.rule, reason=args.reason)
        print(json.dumps(res, indent=2) if args.json else f"wrote {res['written']} ({args.rule})")
        return 0
    except NotesError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- any failure is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


def _render_draft(rep: Mapping[str, Any]) -> str:
    out = [f"drafted {rep['drafted']} proposal(s) into {rep['out']} (cost ${rep['cost_usd']})"]
    for r in rep["rules"]:
        if "error" in r:
            out.append(f"  {r['rule']}: no proposal ({r['error']})")
        else:
            out.append(
                f"  {r['rule']}: basis {r['basis']}, rejected basis {r['rejected_basis']}"
                + (f", dropped {r['dropped']}" if r["dropped"] else "")
            )
    return "\n".join(out)


__all__ = [
    "APPROVED_STATES",
    "GLOSSARY_SCHEMA",
    "RATIONALE_SCHEMA",
    "STATES",
    "Note",
    "NotesError",
    "NotesPaths",
    "RationaleSet",
    "Resolved",
    "anchor_of",
    "approved_sha",
    "atomic_write_json",
    "empty_set",
    "human_guard",
    "load_notes_file",
    "load_set",
    "load_spec_ref",
    "main",
    "norm_text",
    "notes_names",
    "notes_paths",
    "note_view",
    "pct",
    "ratio",
    "resolve",
    "trace_source",
]


if __name__ == "__main__":
    sys.exit(main())
