"""`lattice.spec.ask`: business questions answered from a Lattice spec, with mandatory,
machine-checked citations, and optionally verified against the live app.

    ask(spec, "Can ben cancel ann's run?", generator=gen, model="sonnet") -> Answer

**Grounding.** The model sees only a *spec digest* (`build_digest`): every element of the
spec under its exact target string -- the access matrix's cells (``cell:<caller>|<METHOD
/template>`` -> the statuses the scenarios expect), the scenarios with numbered readable
steps, the invariants' sentences, the machines' moves, the services, the tables
(``profile:<Table>``), the spec's `Unchecked` notes (``unchecked:<n>``) and, with a
trace, the owner's rules (``rule:<Rn>``) with their status. It must answer from the
digest alone, as strict JSON, citing targets (and step numbers), and say
``not_specified`` when the digest does not settle the question.

**Retrieval and caching** (`retrieve`). The model is not sent the whole digest. The
*core* -- seed users, tables, the access matrix (when it fits `CELLS_IN_CORE`), the
invariants, machines, services, unchecked notes, the rules with their links, and an
index naming every scenario -- goes in the system prompt after the instructions; it is
the same for every question on a spec, so the CLI's prompt cache serves it after the
first question. The user message carries the question's part: the scenarios related to
the selected targets, then the scenarios the question's words score best (IDF-weighted
overlap of crude stems, plus half the score of a rule that links them), within
`SCENARIO_BUDGET` tokens, each noting the rules that check it; and, when the matrix is
too large for the core, the cells of the callers and endpoints the question names. The
model is told it sees a subset and may ask, once, for more (``{"need": [targets],
"search": [keywords]}``); those elements are sent and it must then answer.
`usage.digest_elements` counts the elements sent in full.

**Citation check** (deterministic, `check_citation`): a cited target must exist in the
digest; a step number must exist in that scenario; the quote must match the element --
a cell's statuses equal the quote's, a step's method, path and status appear in the
quote (and its caller, when the quote names one), a machine's quoted ``a -> b`` moves
are edges, anything else a normalised substring of the element's text. A failing
citation moves to `Answer.rejected_citations` with its reason. Citations are checked
against the FULL digest, whether or not the element was sent. After one repair round
(the model is told which citations failed), a yes / no / value answer left with no
valid citation is downgraded to ``not_specified``, whatever the model insists.

**Verify** (`VerifyConfig`): the model writes a minimal scenario (Lattice steps as JSON)
that would demonstrate the answer on the seeded app, and marks its *claim step*
(``claim_step``, 1-based, default the last step): the step that tests the answer; the
others are setup. Its prompt shows real setup from the spec (`setup_patterns`: the
opening steps, as JSON with request bodies, of scenarios calling the same endpoints).
It runs alone through `lattice.spec.check.check(..., layers="D")` on a spec that reuses
the base spec's profile, users and services. ``agrees``: it passed; ``disagrees``: the
claim step failed (expected vs actual); a setup step failed: the model gets the failure
and fixes the setup once (keeping the claim step), and if a setup step fails again,
``inconclusive`` (which step, expected vs actual). A claim step whose status (and body)
held but whose database assertion (``expect_db``) failed, with setup steps before it, is
also ``inconclusive``: the assertion reads state those setup steps were meant to produce
(a setup job that never ran, say), so the app neither confirmed nor contradicted the
answer; the detail names the setup steps. ``error``: the scenario was invalid
or could not run. ``verified.claim_step`` is set whenever a scenario was written. For a
``not_specified`` answer, the scenario is an *observation*: it is rerun with each
failing step's expectation replaced by what the app answered, until it passes, and
``verified.status`` is ``observed`` with the app's answers.

CLI (wired as ``python -m lattice.spec ask``)::

    ask --spec MOD:SPEC [--trace T.json] "question" [--selected TARGET ...]
        [--verify --app MOD:FN --db MOD:FN [--login MOD:FN] [--login-path /login]
         [--runtime REF] [--services MOD:SERVICES] [--database D] [--database-url URL]]
        [--model sonnet] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from lattice.llm.client import Generator
from lattice.spec.authoring import _table_block
from lattice.spec.model import Await, Scenario, Spec, Step, tables_of
from lattice.spec.review import _build, _normalize_path, _step_line
from lattice.spec.rules import Trace

KINDS = ("yes", "no", "value", "not_specified")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
NOT_GROUNDED = "Not specified: the answer could not be grounded in the spec."


# ---- the digest ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    """One citable element of the digest: its target, kind and readable content.
    `steps` (a scenario): its readable step lines; `raw` the model object behind it
    (the scenario's steps, a cell's status set, a machine's edges, a rule's links);
    `line`: the element's head line when it is not ``[target] content`` (a rule)."""

    target: str
    kind: str
    content: str
    steps: tuple[str, ...] = ()
    raw: Any = field(default=None, compare=False, hash=False)
    line: str = field(default="", compare=False, hash=False)

    def lines(self, note: str = "", *, plain: bool = False) -> list[str]:
        """The element as digest lines (`note`: one extra line before its steps;
        `plain`: the head is ``[target] content`` even for a rule)."""
        out = [
            f"[{self.target}] {self.content}"
            if plain
            else (self.line or f"[{self.target}] {self.content}")
        ]
        if note:
            out.append(f"  ({note})")
        return out + [f"  {i}. {s}" for i, s in enumerate(self.steps, 1)]


# the digest's sections, in order: (kind, header)
SECTIONS = (
    ("profile", "## Tables"),
    (
        "cell",
        "## Access matrix (caller | METHOD /template -> the status(es) the scenarios "
        "expect; a cell not listed is not exercised by any scenario)",
    ),
    ("scenario", "## Scenarios (each runs on a fresh copy of the seeded database)"),
    ("invariant", "## Invariants (hold after every request)"),
    ("machine", "## State machines (the only allowed moves)"),
    ("service", "## Background services"),
    ("unchecked", "## Not checked (stated in the spec, not checked by it)"),
    ("rule", "## The owner's requirements (rules), with their status"),
)
_HEADERS = dict(SECTIONS)


@dataclass(frozen=True)
class Digest:
    """The spec digest: `text` (all of it, as one document) and `elements` (target ->
    Element, what citations are checked against). `users`: the seed-user lines;
    `with_rules`: a trace was given (the rule section exists)."""

    text: str
    elements: Mapping[str, Element]
    users: tuple[str, ...] = ()
    with_rules: bool = False

    def lines_for(self, targets: Iterable[str]) -> list[str]:
        """The digest lines of the given targets (unknown ones skipped)."""
        out = []
        for t in targets:
            e = self.elements.get(t)
            if e is not None:
                out += e.lines(plain=True)
        return out

    def of_kind(self, kind: str) -> list[Element]:
        return [e for e in self.elements.values() if e.kind == kind]

    def section(self, kind: str, elements: Iterable[Element] | None = None) -> list[str]:
        """One section's lines: its header, its elements (default: all of that kind)
        and a blank line; ``(none)`` when it has none."""
        els = list(self.of_kind(kind) if elements is None else elements)
        body = [ln for e in els for ln in e.lines()] or ["(none)"]
        return [_HEADERS[kind], "", *body, ""]

    def users_section(self) -> list[str]:
        return [
            "## Seed users (handle: login) -- scenarios sign in by handle",
            "",
            *(self.users or ("(none)",)),
            "",
        ]


def _cell_statuses(text: str) -> frozenset[int]:
    return frozenset(int(s) for s in re.findall(r"\b[1-5]\d\d\b", text))


def build_digest(spec: Spec, trace: Trace | None = None) -> Digest:
    """The spec digest (see the module docstring). Deterministic: the same spec and
    trace always give the same text, in the spec's own order."""
    data = _build(spec, trace or Trace(source="", rules=[]), None, None, "spec")
    elements: dict[str, Element] = {}

    def add(e: Element) -> None:
        elements[e.target] = e

    users = []
    for handle, cred in (spec.users or {}).items():
        login = cred[0] if isinstance(cred, tuple | list) and cred else cred
        users.append(f"- {handle}: {login}")

    tables, sql_names = tables_of(spec.profile)
    for name, schema in tables.items():
        sql = sql_names.get(name, name)
        cols = "; ".join(
            ln[2:] for ln in _table_block(name, sql, schema)[2:] if ln.startswith("- ")
        )
        head = f"{name} ({sql})" if sql != name else name
        add(Element(f"profile:{name}", "profile", f"{head}: {cols}"))

    for group in data.access_groups:
        for row in group.rows:
            for col in group.columns:
                status = group.cells.get((row, col), "·")
                if status != "·":
                    add(Element(f"cell:{row}|{col}", "cell", status, raw=_cell_statuses(status)))

    by_name = {sc.name: sc for sc in spec.scenarios}
    for sv in data.section6.scenarios:
        sc = by_name.get(sv.name)
        given = ""
        if sc is not None and sc.given:
            given = " given rows: " + json.dumps(sc.given, sort_keys=True, default=str)
        add(
            Element(
                sv.target,
                "scenario",
                sv.name + given,
                steps=tuple(sv.steps),
                raw=sc.steps if sc is not None else (),
            )
        )

    for target, _name, sentence in data.section6.invariants:
        add(Element(target, "invariant", sentence))

    machines = {f"machine:{m.table}.{m.column}": m for m in spec.machines}
    for target, _label, moves in data.section6.machines:
        m = machines.get(target)
        add(Element(target, "machine", moves, raw=frozenset(m.edges) if m else frozenset()))

    for target, name, doc in data.section6.services:
        add(Element(target, "service", f"{name}: {doc}" if doc else name))

    for i, u in enumerate(data.unchecked_notes, 1):
        concerns = f" (concerns: {', '.join(u.concerns)})" if u.concerns else ""
        add(Element(f"unchecked:{i}", "unchecked", u.text + concerns))

    if trace is not None:
        for row in data.rule_rows:
            how = f"; checked by {', '.join(row.links)}" if row.links else ""
            note = f"; unchecked because: {row.note}" if row.note else ""
            add(
                Element(
                    f"rule:{row.id}",
                    "rule",
                    row.text,
                    raw=tuple(row.links),
                    line=f"[rule:{row.id}] ({row.status}{how}{note}) {row.text}",
                )
            )

    d = Digest("", elements, tuple(users), trace is not None)
    lines = ["SPEC DIGEST -- every citable element, under its exact target in [brackets].", ""]
    lines += d.users_section()
    for kind, _ in SECTIONS:
        if kind != "rule" or d.with_rules:
            lines += d.section(kind)
    return replace(d, text="\n".join(lines).rstrip() + "\n")


# ---- the citation check -------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9{}]+", " ", str(text).lower()).split())


_ARROW = re.compile(r"([A-Za-z_][\w]*)\s*(?:->|→|to)\s*([A-Za-z_][\w]*)")


def _check_step(step: Step | Await, line: str, quote: str) -> str | None:
    """Why `quote` does not match the scenario step (None: it does)."""
    if isinstance(step, Await):
        return (
            None
            if _norm(quote) and _norm(quote) in _norm(line)
            else (f"the quote is not in the step's text: {line!r}")
        )
    tokens = quote.replace("->", " -> ").split()
    methods = [t for t in tokens if t.upper() in METHODS]
    paths = [t.rstrip(",.;:") for t in tokens if t.startswith("/")]
    rest = " ".join(t for t in tokens if not t.startswith("/"))
    statuses = _cell_statuses(rest.split("--")[0])
    problems = []
    if not methods or methods[0].upper() != step.method:
        problems.append(f"method (the step is {step.method})")
    if not paths or paths[0] not in (step.path, _normalize_path(step.path)):
        problems.append(f"path (the step is {step.path})")
    if step.expect_status not in statuses or len(statuses) != 1:
        problems.append(f"status (the step expects {step.expect_status})")
    who = re.search(r"\bas\s+([\w.@-]+)", quote)
    if (
        who
        and who.group(1) not in ((step.user or "no"), "anonymous", "anon")
        and not (step.user is None and who.group(1) in ("no", "nobody"))
    ):
        problems.append(f"caller (the step is as {step.user or 'no token'})")
    if problems:
        return f"the quote does not match step's {', '.join(problems)}: {line!r}"
    return None


def check_citation(digest: Digest, citation: Mapping[str, Any]) -> str | None:
    """Why `citation` ({target, step, quote}) fails against the digest; None: it holds.
    Deterministic, no model."""
    target = str(citation.get("target") or "")
    quote = str(citation.get("quote") or "").strip()
    step = citation.get("step")
    el = digest.elements.get(target)
    if el is None:
        return f"no element {target!r} in the spec"
    if not quote:
        return "the quote is empty"
    if step is not None:
        if el.kind != "scenario":
            return "a step number is only for a scenario target"
        if isinstance(step, bool) or not isinstance(step, int):
            return f"step {step!r} is not a step number"
        if not 1 <= step <= len(el.steps):
            return f"the scenario has no step {step} (it has {len(el.steps)})"
        return _check_step(el.raw[step - 1], el.steps[step - 1], quote)
    if el.kind == "cell":
        quoted = _cell_statuses(" ".join(t for t in quote.split() if not t.startswith("/")))
        if not quoted:
            return f"the quote gives no status (the cell is {el.content})"
        if quoted != el.raw:
            return f"the quote says {'/'.join(map(str, sorted(quoted)))}; the cell is {el.content}"
        return None
    if el.kind == "machine":
        moves = _ARROW.findall(quote)
        if moves:
            bad = [f"{a} -> {b}" for a, b in moves if (a, b) not in el.raw]
            return f"not an allowed move: {', '.join(bad)} (moves: {el.content})" if bad else None
    hay = _norm(el.content + " " + " ".join(el.steps))
    if _norm(quote) and _norm(quote) in hay:
        return None
    return f"the quote is not in the element's text: {el.content!r}"


# ---- the answer ---------------------------------------------------------------------------


@dataclass
class Answer:
    """The `/api/ask` answer (lattice/studio/API.md)."""

    kind: str
    answer: str
    explanation: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    citations_checked: bool = True
    rejected_citations: list[dict[str, Any]] = field(default_factory=list)
    verified: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=lambda: {"seconds": 0, "tokens": 0})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        out = [f"{self.kind.upper()}: {self.answer}", self.explanation]
        for c in self.citations:
            step = f" step {c['step']}" if c.get("step") is not None else ""
            out.append(f"  cites {c['target']}{step}: {c.get('quote', '')}")
        for c in self.rejected_citations:
            out.append(f"  rejected {c['target']}: {c['reason']}")
        if self.verified:
            out.append(f"verified: {self.verified['status']} -- {self.verified['detail']}")
            out += [f"  {s}" for s in self.verified.get("scenario", "").splitlines()]
        u = self.usage
        out.append(
            f"({u.get('seconds', 0)}s, {u.get('tokens', 0)} tokens, "
            f"{u.get('rounds', 0)} model calls)"
        )
        return "\n".join(out)


# ---- retrieval: the core digest and the scenarios relevant to a question -------------------

# Budgets, in estimated tokens (`approx_tokens`: 3 characters a token, a conservative
# estimate for the digest's dense text). The core (system prompt) is the same for every
# question on a spec, so the CLI's prompt cache serves it after the first question.
SCENARIO_BUDGET = 5000  # the relevant scenarios sent with one question
CELLS_IN_CORE = 4000  # the whole access matrix goes in the core when it fits this
CELLS_BUDGET = 1500  # else: the cells relevant to the question, in the user message
NEED_BUDGET = 3000  # the elements the model asked for in its one "need more" round
NEED_MAX = 10  # at most this many targets per "need more" round


def approx_tokens(text: str) -> int:
    """A conservative token estimate (3 characters a token)."""
    return (len(text) + 2) // 3


_STOP = frozenset(
    """a an and any are as at be been being but by can could did do does doing done for
    from get gets getting got has have having he her his how i if in into is it its me my
    no not of on or our she should so some such than that the their them then there these
    they this those to too up us was we were what when where which while who whom whose
    why will with would you your yes ok happen happens happened allowed able may might must
    s t""".split()
)


def _stem(word: str) -> str:
    """A crude stem, so cancel / cancelled / cancellation and run / runs / running meet."""
    w = word
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith(("ses", "xes", "zes", "ches", "shes")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]
    for suf in ("ation", "ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    if len(w) > 3 and w.endswith("y"):
        w = w[:-1] + "i"
    return w


def terms(text: str) -> set[str]:
    """The search terms of `text`: stemmed words (snake_case and paths split), stop words
    dropped, and HTTP statuses kept as ``#409``; other numbers (ids) dropped."""
    out = set()
    for tok in re.findall(r"[A-Za-z]+|\d+", str(text)):
        if tok.isdigit():
            if len(tok) == 3 and tok[0] in "12345":
                out.add("#" + tok)
            continue
        low = tok.lower()
        if len(low) < 2 or low in _STOP:
            continue
        out.add(_stem(low))
    return out


def _endpoint(step: Any) -> tuple[str, str] | None:
    if isinstance(step, Step):
        return step.method, _normalize_path(step.path)
    return None


def _cell_endpoint(target: str) -> tuple[str, str, str] | None:
    """(caller, METHOD, template) of a ``cell:`` target."""
    if not target.startswith("cell:") or "|" not in target:
        return None
    row, _, col = target[5:].partition("|")
    method, _, template = col.partition(" ")
    return row, method, template


@dataclass
class Retrieval:
    """What one question sends: `system` (instructions + the core, the same for every
    question on the spec), `context` (the question's elements, for the user message),
    `scenarios` (the scenario targets in `context`, best first) and `sent` (every target
    sent in full, the core's included)."""

    system: str
    context: str
    scenarios: list[str]
    sent: set[str]


class Index:
    """Lexical scores over a digest's scenarios and rules (IDF-weighted term overlap)."""

    def __init__(self, digest: Digest) -> None:
        self.digest = digest
        self.docs: dict[str, set[str]] = {}
        for e in digest.elements.values():
            if e.kind in ("scenario", "rule", "cell"):
                self.docs[e.target] = terms(" ".join([e.target, e.content, *e.steps]))
        n = max(len(self.docs), 1)
        df: dict[str, int] = {}
        for ts in self.docs.values():
            for t in ts:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + n / c) for t, c in df.items()}
        # rule -> the targets it links; target -> the rules that link it
        self.links = {e.target: tuple(e.raw or ()) for e in digest.of_kind("rule")}
        self.linked_by: dict[str, list[str]] = {}
        for rule, targets in self.links.items():
            for t in targets:
                self.linked_by.setdefault(t, []).append(rule)
        self.order = {t: i for i, t in enumerate(digest.elements)}

    def score(self, target: str, query: set[str]) -> float:
        return sum(self.idf.get(t, 0.0) for t in query & self.docs.get(target, set()))

    def rank_scenarios(
        self, query: set[str], boost: Mapping[str, float] | None = None
    ) -> list[tuple[float, str]]:
        """The scenarios with a positive score, best first: their own score, plus half
        the best score of a rule that links them (a relevant requirement pulls in the
        scenarios that check it), plus `boost`."""
        rule_scores = {r: self.score(r, query) for r in self.links}
        out = []
        for e in self.digest.of_kind("scenario"):
            s = self.score(e.target, query)
            s += 0.5 * max((rule_scores.get(r, 0.0) for r in self.linked_by.get(e.target, ())),
                           default=0.0)  # fmt: skip
            s += (boost or {}).get(e.target, 0.0)
            if s > 0:
                out.append((s, e.target))
        out.sort(key=lambda p: (-p[0], self.order[p[1]]))
        return out

    def scenario_lines(self, target: str) -> list[str]:
        """A scenario's digest lines, noting the rules that link it."""
        e = self.digest.elements[target]
        rules = self.linked_by.get(target, ())
        return e.lines(f"checks {', '.join(rules)}" if rules else "")

    def related_scenarios(self, targets: Iterable[str]) -> list[str]:
        """The scenarios the given targets point at: themselves (a scenario), the targets
        a rule links, and the scenarios that exercise a cell (its caller and endpoint)."""
        out: list[str] = []
        cells = []
        for t in targets:
            el = self.digest.elements.get(t)
            if el is None:
                continue
            if el.kind == "scenario":
                out.append(t)
            elif el.kind == "rule":
                out += [x for x in self.links.get(t, ()) if x.startswith("scenario:")]
            elif el.kind == "cell":
                cells.append(_cell_endpoint(t))
        for e in self.digest.of_kind("scenario"):
            for row, method, template in filter(None, cells):
                if any(
                    isinstance(s, Step)
                    and (s.user or "no token") == row
                    and _endpoint(s) == (method, template)
                    for s in e.raw or ()
                ):
                    out.append(e.target)
                    break
        return list(dict.fromkeys(out))


def _fill(candidates: Iterable[str], cost: Callable[[str], int], budget: int) -> list[str]:
    """The candidates, in order, that fit `budget` (a candidate that does not fit is
    skipped and smaller later ones may still fit)."""
    out, used = [], 0
    for t in candidates:
        c = cost(t)
        if used + c <= budget:
            out.append(t)
            used += c
    return out


SYSTEM = """\
You answer business questions about one application, using ONLY its spec digest.

The digest lists elements of the spec under an exact target in [brackets]:
access-matrix cells (cell:<caller>|<METHOD /template> -> expected status), scenarios
with numbered steps ("METHOD /path as <caller> -> <status>"), invariants, state machines,
background services, tables (profile:<Table>), notes the spec does not check
(unchecked:<n>) and, when present, the owner's rules (rule:<Rn>).

What you see: the CORE of the digest is below (users, tables, cells, invariants,
machines, services, unchecked notes, rules, and an index naming every scenario). The
user message adds the scenarios most relevant to the question, chosen by its words:
a SUBSET of all scenarios. A scenario not shown still exists if the index names it.

Rules:
- Answer only from the digest. Never answer from general knowledge of how apps usually
  work, and never guess. If the digest does not settle the question, answer with kind
  "not_specified" and say what is missing.
- If the answer may be in elements you were not shown (a scenario named in the index, or
  one a keyword would find), you may ask for them ONCE, before answering, by replying
  {"need": ["scenario:<name>", ...], "search": ["keyword", ...]} instead of an answer.
  Ask only when it matters; then answer from what you have. If the evidence is still
  not there, answer "not_specified".
- An access cell is only what the scenarios expect for that caller and endpoint; a
  caller or endpoint with no cell is not specified.
- Every yes / no / value answer must cite the elements that settle it. A citation is
  {"target": "<exact target from the digest>", "step": <step number, scenarios only,
  else null>, "quote": "<text copied from that element>"}. For a cell quote its status
  (e.g. "403"); for a scenario step copy the step line (e.g. "POST /runs/1/cancel as
  ben -> 403"); for anything else copy words from it. Citations are checked
  mechanically; a citation that does not match is rejected.
- Reply with ONE JSON object and nothing else:
  {"kind": "yes|no|value|not_specified", "answer": "short answer",
   "explanation": "1-3 sentences, grounded in the cited elements",
   "citations": [{"target": "...", "step": null, "quote": "..."}]}
"""


def _cells_in_core(digest: Digest) -> bool:
    return approx_tokens("\n".join(digest.section("cell"))) <= CELLS_IN_CORE


def core_text(digest: Digest) -> str:
    """The question-independent part of the digest: everything but the scenarios (named
    in an index) -- and the access matrix, when it is too large (`CELLS_IN_CORE`)."""
    lines = [
        "SPEC DIGEST, CORE -- citable elements, under their exact target in [brackets].",
        "",
        *digest.users_section(),
    ]
    for kind, _ in SECTIONS:
        if kind == "scenario" or (kind == "rule" and not digest.with_rules):
            continue
        if kind == "cell" and not _cells_in_core(digest):
            lines += [
                _HEADERS["cell"],
                "",
                "(large: the cells relevant to the question are in the user message)",
                "",
            ]
            continue
        lines += digest.section(kind)
    names = [e.target.removeprefix("scenario:") for e in digest.of_kind("scenario")]
    lines += [
        f"## Scenario index ({len(names)} scenarios; target = scenario:<name>; the "
        "relevant ones are shown in full in the user message)",
        "",
        "; ".join(names) or "(none)",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _relevant_cells(digest: Digest, index: Index, query: set[str], selected: Sequence[str]):
    """The cells for the question when the matrix is not in the core: rows the question
    names (all rows when it names none) x columns that share its words, best first."""
    cells = digest.of_kind("cell")
    rows = {(_cell_endpoint(e.target) or ("",))[0] for e in cells}
    named = {r for r in rows if terms(r) and terms(r) <= query}
    scored = []
    for e in cells:
        row = (_cell_endpoint(e.target) or ("",))[0]
        if named and row not in named and e.target not in selected:
            continue
        s = index.score(e.target, query) + (100.0 if e.target in selected else 0.0)
        if s > 0:
            scored.append((s, e.target))
    scored.sort(key=lambda p: (-p[0], index.order[p[1]]))
    return _fill(
        (t for _, t in scored),
        lambda t: approx_tokens("\n".join(digest.elements[t].lines())),
        CELLS_BUDGET,
    )


def retrieve(
    digest: Digest,
    question: str,
    selected: Sequence[str] = (),
    *,
    index: Index | None = None,
    budget: int = SCENARIO_BUDGET,
) -> Retrieval:
    """The prompt parts for `question` (see `Retrieval`): the core in the system prompt;
    in the context, the relevant cells (when the matrix is not in the core) and the
    scenarios related to the selected targets, then the best-scoring ones, within
    `budget` tokens."""
    index = index or Index(digest)
    query = terms(question)
    for t in selected:
        el = digest.elements.get(t)
        if el is not None and el.kind != "scenario":
            query |= terms(el.content)
    sent = {
        e.target
        for e in digest.elements.values()
        if e.kind not in ("scenario", "cell") or (e.kind == "cell" and _cells_in_core(digest))
    }
    parts: list[str] = []
    if not _cells_in_core(digest):
        cells = _relevant_cells(digest, index, query, selected)
        if cells:
            parts += [
                "## Access-matrix cells relevant to the question (a subset)",
                "",
                *digest.lines_for(cells),
                "",
            ]
            sent |= set(cells)
    forced = [t for t in index.related_scenarios(selected) if t not in selected]
    boost = dict.fromkeys(forced, 1000.0)
    ranked = [t for _, t in index.rank_scenarios(query, boost) if t not in selected]
    picked = _fill(
        dict.fromkeys(forced + ranked),
        lambda t: approx_tokens("\n".join(index.scenario_lines(t))),
        budget,
    )
    n_all = len(digest.of_kind("scenario"))
    parts += [
        f"## Relevant scenarios ({len(picked)} of {n_all}, chosen by the question's words; "
        "the rest are only named in the index)",
        "",
    ]
    for t in picked:
        parts += index.scenario_lines(t)
    if not picked:
        parts.append("(none matched the question)")
    parts.append("")
    sel = digest.lines_for(selected)
    if sel:
        parts += [
            "The user selected these elements (the question is probably about them):",
            *sel,
            "",
        ]
        sent |= {t for t in selected if t in digest.elements}
    sent |= set(picked)
    return Retrieval(SYSTEM + "\n" + core_text(digest), "\n".join(parts), picked, sent)


def _need_request(data: Any) -> tuple[list[str], list[str]] | None:
    """A "need more" reply's (targets, keywords), or None when it is not one."""
    if not isinstance(data, Mapping) or "kind" in data:
        return None
    if "need" not in data and "search" not in data:
        return None
    need, search = data.get("need") or [], data.get("search") or []
    need = [need] if isinstance(need, str) else need
    search = [search] if isinstance(search, str) else search
    return [str(t) for t in need if t][:NEED_MAX], [str(k) for k in search if k]


def _more(
    digest: Digest, index: Index, sent: set[str], need: list[str], search: list[str]
) -> tuple[list[str], list[str]]:
    """The extra elements for a "need more" round: the named targets that exist and were
    not sent, then the scenarios the keywords score best, within `NEED_BUDGET`. Returns
    (targets, unknown names)."""
    unknown = [t for t in need if t not in digest.elements]
    named = [t for t in need if t in digest.elements and t not in sent]
    query = set().union(*(terms(k) for k in search)) if search else set()
    found = [t for _, t in index.rank_scenarios(query) if t not in sent] if query else []

    def cost(t: str) -> int:
        lines = index.scenario_lines(t) if t.startswith("scenario:") else digest.lines_for([t])
        return approx_tokens("\n".join(lines))

    return _fill(dict.fromkeys(named + found[:NEED_MAX]), cost, NEED_BUDGET), unknown


def _more_message(digest: Digest, index: Index, picked: list[str], unknown: list[str]) -> str:
    lines = []
    if picked:
        lines += ["Here are the elements you asked for:", ""]
        for t in picked:
            lines += index.scenario_lines(t) if t.startswith("scenario:") else digest.lines_for([t])
        lines.append("")
    else:
        lines.append("Nothing more matched your request beyond what you were already shown.")
    if unknown:
        lines.append(f"No such elements in the spec: {', '.join(unknown)}.")
    lines.append(
        "No more elements can be sent. Answer now with the JSON answer object; if the "
        'evidence is not there, answer with kind "not_specified".'
    )
    return "\n".join(lines)


def _json_object(text: str) -> Any:
    """The first JSON object in `text` (a ```json fence, or the outermost braces)."""
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    body = fence.group(1) if fence else text
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(body[start : end + 1])


def _parse_answer(text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = _json_object(text)
    except (ValueError, json.JSONDecodeError) as err:
        return None, f"the reply is not one JSON object ({err})"
    if not isinstance(data, dict):
        return None, "the reply is not a JSON object"
    kind = str(data.get("kind", "")).strip().lower()
    if kind not in KINDS:
        return None, f"kind must be one of {', '.join(KINDS)}, not {data.get('kind')!r}"
    cites = data.get("citations") or []
    if not isinstance(cites, list):
        return None, "citations must be a list"
    clean = []
    for c in cites:
        if not isinstance(c, Mapping):
            continue
        step = c.get("step")
        if isinstance(step, str) and step.strip().isdigit():
            step = int(step)
        clean.append(
            {"target": str(c.get("target") or ""), "step": step, "quote": str(c.get("quote") or "")}
        )
    return {
        "kind": kind,
        "answer": str(data.get("answer", "")),
        "explanation": str(data.get("explanation", "")),
        "citations": clean,
    }, None


def _split(digest: Digest, cites: list[dict[str, Any]]) -> tuple[list, list]:
    good, bad = [], []
    for c in cites:
        reason = check_citation(digest, c)
        if reason is None:
            good.append(c)
        else:
            bad.append(
                {
                    "target": c["target"],
                    "step": c.get("step"),
                    "quote": c["quote"],
                    "reason": reason,
                }
            )
    return good, bad


class _Meter:
    """Model calls' usage, summed; `sent`: every digest target sent to the model."""

    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.rounds = 0
        self.input = self.output = self.cache_read = self.cache_write = 0
        self.cost: float | None = None
        self.sent: set[str] = set()
        self.calls: list[dict[str, int]] = []

    def call(self, generator: Generator, system: str, messages: list[dict[str, str]]) -> str:
        gen = generator.generate(system, messages)
        u = gen.usage
        self.rounds += 1
        self.input += u.total_input_tokens
        self.output += u.output_tokens
        self.cache_read += u.cache_read_input_tokens
        self.cache_write += u.cache_creation_input_tokens
        self.calls.append(
            {
                "input_tokens": u.total_input_tokens,
                "cache_read_tokens": u.cache_read_input_tokens,
                "prompt_tokens_est": approx_tokens(system)
                + sum(approx_tokens(m["content"]) for m in messages),
            }
        )
        if gen.cost_usd is not None:
            self.cost = (self.cost or 0.0) + gen.cost_usd
        return gen.text

    def as_dict(self, model: str) -> dict[str, Any]:
        out = {
            "seconds": round(time.perf_counter() - self.start, 2),
            "tokens": self.input + self.output,
            "input_tokens": self.input,
            "output_tokens": self.output,
            "cache_read_tokens": self.cache_read,
            "cache_creation_tokens": self.cache_write,
            "rounds": self.rounds,
            "model": model,
            "digest_elements": len(self.sent),
            "calls": self.calls,
        }
        if self.cost is not None:
            out["cost_usd"] = round(self.cost, 6)
        return out


def _repair_message(parse_error: str | None, bad: list, kind: str | None, n_good: int) -> str:
    lines = ["Your answer failed the mechanical citation check."]
    if parse_error:
        lines.append(f"- {parse_error}")
    for b in bad:
        step = f" step {b['step']}" if b.get("step") is not None else ""
        lines.append(f"- {b['target']}{step} (quote {b['quote']!r}): {b['reason']}")
    if kind in ("yes", "no", "value") and n_good == 0 and not bad and not parse_error:
        lines.append("- a yes / no / value answer needs at least one citation")
    lines.append(
        "Fix the citations: use exact targets and step numbers from the digest and copy "
        "the quote from the element. If the digest does not settle the question, answer "
        'with kind "not_specified". Reply with the JSON object only.'
    )
    return "\n".join(lines)


def ask(
    spec: Spec,
    question: str,
    *,
    trace: Trace | None = None,
    selected: Sequence[str] = (),
    generator: Generator,
    model: str = "",
    verify: VerifyConfig | None = None,
    digest: Digest | None = None,
    index: Index | None = None,
) -> Answer:
    """Answer `question` from `spec` (see the module docstring). `digest` / `index`: a
    prebuilt `build_digest(spec, trace)` and its `Index` (`ask_many` reuses them).

    Rounds: the answer; at most one "need more" round (the model asked for elements it
    was not shown); at most one citation-repair round. Citations are always checked
    against the FULL digest, whatever was sent."""
    digest = digest or build_digest(spec, trace)
    index = index or Index(digest)
    meter = _Meter()
    r = retrieve(digest, question, selected, index=index)
    meter.sent |= r.sent
    system = r.system
    messages = [{"role": "user", "content": f"{r.context}\nQuestion: {question}"}]
    text = meter.call(generator, system, messages)
    try:
        need = _need_request(_json_object(text))
    except (ValueError, json.JSONDecodeError):
        need = None
    if need is not None:
        picked, unknown = _more(digest, index, meter.sent, *need)
        meter.sent |= set(picked)
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": _more_message(digest, index, picked, unknown)},
        ]
        text = meter.call(generator, system, messages)
    parsed, err = _parse_answer(text)
    good, bad = _split(digest, parsed["citations"]) if parsed else ([], [])
    needs = parsed is None or bool(bad) or (parsed["kind"] != "not_specified" and not good)
    if needs:
        messages += [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": _repair_message(err, bad, parsed and parsed["kind"], len(good)),
            },
        ]
        text = meter.call(generator, system, messages)
        parsed2, err2 = _parse_answer(text)
        if parsed2 is not None or parsed is None:
            parsed, err = parsed2, err2
            good, bad = _split(digest, parsed["citations"]) if parsed else ([], [])

    if parsed is None:
        answer = Answer(
            "not_specified", NOT_GROUNDED, f"The model's reply could not be read: {err}."
        )
    else:
        answer = Answer(
            parsed["kind"],
            parsed["answer"],
            parsed["explanation"],
            good,
            True,
            [{"target": b["target"], "reason": b["reason"]} for b in bad],
        )
        if parsed["kind"] != "not_specified" and not good:
            why = (
                "none of its citations matched the spec" if bad else "it cited nothing in the spec"
            )
            answer.kind = "not_specified"
            answer.answer = NOT_GROUNDED
            answer.explanation = (
                f"The model answered {parsed['kind']} ({parsed['answer']!r}), but {why}, "
                "so the answer is not grounded in the spec."
            )
    if verify is not None:
        answer.verified = _verify(
            spec, digest, index, question, answer, r.scenarios, verify, generator, meter
        )
    answer.usage = meter.as_dict(model)
    return answer


def ask_many(
    spec: Spec,
    questions: Sequence[str],
    *,
    trace: Trace | None = None,
    generator: Generator,
    model: str = "",
    verify: VerifyConfig | None = None,
) -> list[Answer]:
    """`ask` each question in turn, on one digest (and so one cached system prompt)."""
    digest = build_digest(spec, trace)
    index = Index(digest)
    return [
        ask(
            spec,
            q,
            trace=trace,
            generator=generator,
            model=model,
            verify=verify,
            digest=digest,
            index=index,
        )
        for q in questions
    ]


# ---- verify -------------------------------------------------------------------------------


@dataclass
class VerifyConfig:
    """How to run a scenario against the app: `lattice.spec.check.check`'s harness.
    `app`: the app factory, `db`: the database factory; `options`: more check kwargs.
    `observe`: run an observation scenario for a not_specified answer."""

    app: Callable[..., Any]
    db: Callable[..., Any]
    login: Callable[..., Any] | None = None
    login_path: str | None = None
    runtime: Any = None
    services: Mapping[str, Any] | None = None
    database: str = "sqlite"
    database_url: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    observe: bool = True

    def kwargs(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "login_path": self.login_path,
            "runtime": self.runtime,
            "services": self.services,
            "database": self.database,
            "database_url": self.database_url,
            **dict(self.options),
        }


VERIFY_SYSTEM = """\
You write ONE minimal Lattice scenario that runs against the real application, on a fresh
copy of its seeded database. Use only the spec digest: its seed users (by handle), its
endpoints and paths, and the setup steps of its existing scenarios. The seeded database
holds only what the seed creates; anything else the scenario needs must be created by
earlier steps (or by "given" rows) -- copy the setup of an existing scenario that calls
the same endpoint: the same request bodies, with every field they send. Use concrete
paths (/runs/1, never /runs/{}).

Exactly one step tests the answer: the CLAIM STEP. Every step before it is setup and
must succeed for the run to say anything. Make the claim step the last step when you
can, and give its number (1-based) in "claim_step".

Reply with ONE JSON object and nothing else:
{"given": <null or {"<sql table>": [{row}, ...]}>,
 "steps": [{"method": "POST", "path": "/...", "user": "<handle or null>",
            "json": <request body or null>, "expect_status": 200,
            "expect_db": <null or {"<table>": {"where": {...}, "count": n}}>}],
 "claim_step": <number of the step that tests the answer>}
"""

SETUP_BUDGET = 2500  # tokens of setup patterns in the verify prompt
SETUP_SCENARIOS = 6  # at most this many patterns, 2 per endpoint


def _step_json(step: Step | Await) -> str:
    if isinstance(step, Await):
        d: dict[str, Any] = {"service": step.service, "expect_db": step.expect_db}
        d["within_s"] = step.within_s
    else:
        d = {"method": step.method, "path": step.path, "user": step.user}
        if step.json is not None:
            d["json"] = step.json
        if step.params:
            d["params"] = dict(step.params)
        d["expect_status"] = step.expect_status
    return json.dumps(d, default=str)


def _answer_endpoints(digest: Digest, question: str, answer: Answer) -> list[tuple[str, str]]:
    """The endpoints the answer is about: its cited cells' and cited steps' endpoints,
    and any ``METHOD /path`` the question names."""
    out: list[tuple[str, str]] = []
    for c in answer.citations:
        el = digest.elements.get(c["target"])
        if el is None:
            continue
        if el.kind == "cell":
            ce = _cell_endpoint(el.target)
            if ce:
                out.append(ce[1:])
        elif el.kind == "scenario":
            steps = el.raw or ()
            n = c.get("step")
            picks = [steps[n - 1]] if isinstance(n, int) and 1 <= n <= len(steps) else steps
            out += [ep for ep in map(_endpoint, picks) if ep]
    for m, path in re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s?,;]*)", question):
        out.append((m, _normalize_path(path.replace("{id}", "1"))))
    return list(dict.fromkeys(out))


def setup_patterns(
    digest: Digest, endpoints: Sequence[tuple[str, str]], fallback: Sequence[str] = ()
) -> list[str]:
    """Real setup from the spec's own scenarios, as the JSON steps the model writes: for
    each endpoint, up to two scenarios that call it, from their first step to the first
    call (6 steps at most: the first 3, then that call); `fallback` scenarios (whole,
    first 4 steps) when no endpoint is known. Within `SETUP_BUDGET`."""
    blocks: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for ep in endpoints:
        hits = 0
        for e in digest.of_kind("scenario"):
            if hits == 2 or e.target in seen:
                continue
            steps = list(e.raw or ())
            k = next((i for i, s in enumerate(steps) if _endpoint(s) == ep), None)
            if k is None:
                continue
            shown = list(enumerate(steps[: k + 1], 1))
            if len(shown) > 6:
                shown = shown[:3] + [(k + 1, steps[k])]
            blocks.append((e.target, [f"  {i}. {_step_json(s)}" for i, s in shown]))
            seen.add(e.target)
            hits += 1
    if not blocks:
        for t in fallback:
            e = digest.elements.get(t)
            if e is not None and t not in seen:
                steps = list(e.raw or ())[:4]
                blocks.append((t, [f"  {i}. {_step_json(s)}" for i, s in enumerate(steps, 1)]))
    out: list[str] = []
    used = 0
    for target, lines in blocks[:SETUP_SCENARIOS]:
        sc = digest.elements[target]
        given = sc.content.partition(" given rows: ")[2]
        head = f"[{target}]" + (f" given rows: {given}" if given else "")
        cost = approx_tokens("\n".join([head, *lines]))
        if used + cost > SETUP_BUDGET:
            continue
        out += [head, *lines]
        used += cost
    return out


def _verify_prompt(
    digest: Digest,
    question: str,
    answer: Answer,
    observe: bool,
    patterns: Sequence[str],
) -> str:
    if observe:
        goal = (
            "The spec does not settle this question. Write the steps that would show "
            "what the app actually does; put your best guess in expect_status (the "
            "app's real answers will be recorded)."
        )
    else:
        cites = "; ".join(
            f"{c['target']}" + (f" step {c['step']}" if c.get("step") else "")
            for c in answer.citations
        )
        goal = (
            f"The answer is: {answer.kind} -- {answer.answer}. {answer.explanation} "
            f"(cited: {cites}). Write the steps whose expectations demonstrate exactly "
            "this answer: the scenario passes only if the app behaves as answered."
        )
    parts = []
    cited = digest.lines_for(dict.fromkeys(c["target"] for c in answer.citations))
    if cited:
        parts += ["The cited elements:", *cited, ""]
    if patterns:
        parts += [
            "Setup that works in this spec (steps of its own scenarios that call the same "
            "endpoints, as JSON; copy their bodies and the steps that come before):",
            *patterns,
            "",
        ]
    parts += [f"Question: {question}", "", goal]
    return "\n".join(parts)


def _scenario_from(data: Any, name: str) -> Scenario:
    """A Scenario from the model's JSON (raises ValueError when malformed)."""
    if not isinstance(data, Mapping) or not isinstance(data.get("steps"), list):
        raise ValueError('expected {"steps": [...]}')
    steps: list[Step | Await] = []
    for i, s in enumerate(data["steps"], 1):
        if not isinstance(s, Mapping):
            raise ValueError(f"step {i} is not an object")
        if "service" in s or "await" in s:
            steps.append(
                Await(
                    str(s.get("service") or s.get("await")),
                    dict(s["expect_db"]),
                    float(s.get("within_s", 10.0)),
                )
            )
            continue
        if not s.get("method") or not s.get("path"):
            raise ValueError(f"step {i} needs a method and a path")
        status = s.get("expect_status", 200)
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError(f"step {i}: expect_status must be an integer")
        steps.append(
            Step(
                str(s["method"]).upper(),
                str(s["path"]),
                s.get("user") or None,
                json=s.get("json"),
                params=s.get("params"),
                expect_status=status,
                expect_body=s.get("expect_body"),
                expect_db=s.get("expect_db") or None,
            )
        )
    if not steps:
        raise ValueError("the scenario has no steps")
    return Scenario(name, given=data.get("given") or None, steps=tuple(steps))


def _readable(scenario: Scenario) -> str:
    return "\n".join(f"{i}. {_step_line(s)}" for i, s in enumerate(scenario.steps, 1))


def _run(spec: Spec, scenario: Scenario, cfg: VerifyConfig) -> tuple[str, Any]:
    """Run `scenario` alone through layer D: ("pass", None), ("fail", finding) or
    ("error", message)."""
    from lattice.spec.check import CheckError, check
    from lattice.spec.validate import validate

    one = Spec(
        profile=spec.profile, users=spec.users, services=spec.services, scenarios=(scenario,)
    )
    problems = validate(one)
    if problems:
        return "error", "the scenario is not valid: " + "; ".join(p.render() for p in problems)
    try:
        report = check(one, cfg.app, cfg.db, layers="D", **cfg.kwargs())
    except CheckError as err:
        return "error", f"the check could not run: {err}"
    except Exception as err:  # noqa: BLE001 -- any failure to run is an error result
        return "error", f"the check could not run: {type(err).__name__}: {err}"
    found = [f for f in report.findings if f.layer == "D"]
    if not found:
        return "pass", None
    f = found[0]
    if "actual" not in f.details and "problems" not in f.details:
        return "error", f"{f.case}: {f.details.get('error', f.details)}"
    return "fail", f


def _step_no(finding: Any) -> int | None:
    m = re.search(r"/ step (\d+)$", finding.case or "")
    return int(m.group(1)) if m else None


def _db_only_failure(scenario: Scenario, finding: Any) -> bool:
    """Whether the failing step answered with the expected status and only its database
    assertion failed (no status or body problem)."""
    n = _step_no(finding)
    d = finding.details
    if n is None or not isinstance(scenario.steps[n - 1], Step) or "actual" not in d:
        return False
    step = scenario.steps[n - 1]
    problems = d.get("problems") or []
    return (
        bool(step.expect_db)
        and d["actual"].get("status") == step.expect_status
        and bool(problems)
        and all(str(p).startswith("db ") for p in problems)
    )


def _finding_text(scenario: Scenario, finding: Any) -> str:
    n = _step_no(finding)
    where = f"step {n} ({_step_line(scenario.steps[n - 1])})" if n else finding.case
    d = finding.details
    if "actual" in d:
        body = json.dumps(d["actual"].get("body"), default=str)
        body = body if len(body) <= 200 else body[:197] + "..."
        return (
            f"{where}: expected {d['expected']['status']}, got {d['actual']['status']} "
            f"(body {body}); {'; '.join(d.get('problems', []))}"
        )
    return f"{where}: {'; '.join(d.get('problems', []))}"


def _observe(spec: Spec, scenario: Scenario, cfg: VerifyConfig) -> dict[str, Any]:
    """Run `scenario` with no expectation baked in: each failing step's expected status
    becomes what the app answered, and it reruns until it passes."""
    steps = [
        replace(s, expect_body=None, expect_db=None) if isinstance(s, Step) else s
        for s in scenario.steps
    ]
    bodies: dict[int, Any] = {}
    for _ in range(len(steps) + 1):
        current = replace(scenario, steps=tuple(steps))
        status, out = _run(spec, current, cfg)
        if status == "pass":
            lines = []
            for i, s in enumerate(steps, 1):
                body = f" (body {json.dumps(bodies[i], default=str)[:160]})" if i in bodies else ""
                lines.append(f"{i}. {_step_line(s)}{body}")
            return {
                "status": "observed",
                "detail": "the spec does not settle this; the app answered as shown",
                "scenario": "\n".join(lines),
            }
        if status == "error":
            return {"status": "error", "detail": out, "scenario": _readable(current)}
        n = _step_no(out)
        actual = out.details.get("actual") if n else None
        if not actual or not isinstance(steps[n - 1], Step):
            return {
                "status": "error",
                "detail": "observation: " + _finding_text(current, out),
                "scenario": _readable(current),
            }
        steps[n - 1] = replace(steps[n - 1], expect_status=actual["status"])
        bodies[n] = actual.get("body")
    return {
        "status": "error",
        "detail": "observation did not settle (the app's answers vary)",
        "scenario": _readable(replace(scenario, steps=tuple(steps))),
    }


def _claim_step(data: Any, n_steps: int) -> int:
    """The model's ``claim_step`` (1-based); the last step when absent or out of range."""
    c = data.get("claim_step") if isinstance(data, Mapping) else None
    if isinstance(c, str) and c.strip().isdigit():
        c = int(c)
    if isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= n_steps:
        return n_steps
    return c


def _claim_key(step: Step | Await) -> tuple:
    if isinstance(step, Await):
        return ("await", step.service)
    return (step.method, _normalize_path(step.path), step.user, step.expect_status)


def _verify(
    spec: Spec,
    digest: Digest,
    index: Index,
    question: str,
    answer: Answer,
    relevant: Sequence[str],
    cfg: VerifyConfig,
    generator: Generator,
    meter: _Meter,
) -> dict[str, Any]:
    """Write a scenario for the answer and run it (see the module docstring). A failing
    step other than the claim step is setup: the model gets one try to fix it, then the
    result is ``inconclusive``."""
    observe = answer.kind == "not_specified"
    if observe and not cfg.observe:
        return {
            "status": "error",
            "detail": "not run: the answer is not_specified and observation is off",
            "scenario": "",
        }
    patterns = setup_patterns(digest, _answer_endpoints(digest, question, answer), relevant)
    meter.sent |= set(re.findall(r"^\[(scenario:[^\]]+)\]", "\n".join(patterns), re.M))
    meter.sent |= {c["target"] for c in answer.citations if c["target"] in digest.elements}
    system = VERIFY_SYSTEM + "\n" + core_text(digest)
    messages = [
        {"role": "user", "content": _verify_prompt(digest, question, answer, observe, patterns)}
    ]
    try:
        text = meter.call(generator, system, messages)
        data = _json_object(text)
        scenario = _scenario_from(data, "ask_verify")
    except Exception as err:  # noqa: BLE001 -- a bad scenario is an error result
        return {
            "status": "error",
            "detail": f"no valid scenario: {type(err).__name__}: {err}",
            "scenario": "",
        }
    claim = _claim_step(data, len(scenario.steps))
    if observe:
        return {**_observe(spec, scenario, cfg), "claim_step": claim}
    for attempt in (1, 2):
        status, out = _run(spec, scenario, cfg)
        result = {"scenario": _readable(scenario), "claim_step": claim}
        if status == "pass":
            return {"status": "agrees", "detail": "the scenario passed against the app", **result}
        if status == "error":
            return {"status": "error", "detail": out, **result}
        n = _step_no(out)
        if n is not None and n == claim and claim > 1:
            setup = _db_only_failure(scenario, out)
            if setup:
                names = ", ".join(
                    f"{i} ({_step_line(scenario.steps[i - 1])})" for i in range(1, claim)
                )
                plural = "s" if claim > 2 else ""
                return {
                    "status": "inconclusive",
                    "detail": (
                        f"the claim step {claim} got the expected status but its database "
                        f"assertion failed, so setup step{plural} {names} did not leave the "
                        "state the assertion reads (a setup step failed to do its work); "
                        f"the app neither confirmed nor contradicted the answer: "
                        f"{_finding_text(scenario, out)}"
                    ),
                    **result,
                }
        if n is None or n == claim:
            return {"status": "disagrees", "detail": _finding_text(scenario, out), **result}
        where = "before" if n < claim else "after"
        detail = (
            f"setup step {n} failed {where} the claim step {claim}, so the app neither "
            f"confirmed nor contradicted the answer: {_finding_text(scenario, out)}"
        )
        if attempt == 2:
            return {"status": "inconclusive", "detail": detail, **result}
        messages += [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    f"The scenario did not test the claim: step {n} is setup ({where} the "
                    f"claim step {claim}) and it failed: {_finding_text(scenario, out)}.\n"
                    "Fix the setup so every step but the claim step succeeds: valid request "
                    "bodies with every required field, ids that exist, the steps the "
                    "endpoint needs first (copy the setup patterns). Keep the claim step's "
                    "request, caller and expected status as they are, and give its number "
                    "in claim_step. Reply with the JSON object only."
                ),
            },
        ]
        try:
            text = meter.call(generator, system, messages)
            data = _json_object(text)
            fixed = _scenario_from(data, "ask_verify")
        except Exception as err:  # noqa: BLE001 -- a bad retry leaves the first result
            return {
                "status": "inconclusive",
                "detail": f"{detail}; the setup retry was not a valid scenario ({err})",
                **result,
            }
        new_claim = _claim_step(data, len(fixed.steps))
        if _claim_key(fixed.steps[new_claim - 1]) != _claim_key(scenario.steps[claim - 1]):
            return {
                "status": "inconclusive",
                "detail": f"{detail}; the setup retry changed the claim step, so it was not run",
                **result,
            }
        scenario, claim = fixed, new_claim
    raise AssertionError("unreachable")


# ---- CLI ----------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec ask",
        description="Answer a question from a spec, with citations.",
    )
    p.add_argument("question")
    p.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")
    p.add_argument("--trace", type=Path, help="the spec's trace JSON")
    p.add_argument("--selected", action="append", default=[], help="a selected target")
    p.add_argument("--model", default="sonnet", help="Claude model (alias or id)")
    p.add_argument("--json", action="store_true", help="print the answer as JSON")
    p.add_argument("--verify", action="store_true", help="check the answer against the app")
    p.add_argument("--app", help="module:factory, (db_path) -> app")
    p.add_argument("--db", help="module:build_db")
    p.add_argument("--login", help="module:fn, (client, handle) -> bearer token")
    p.add_argument("--login-path", help="a form login route taking Spec.users' credentials")
    p.add_argument("--runtime", help="module:RUNTIME, 'process' or 'go'")
    p.add_argument("--services", help="module:SERVICES")
    from lattice.spec.engines import database_names

    p.add_argument("--database", default="sqlite", choices=database_names())
    p.add_argument("--database-url")
    return p


def main(argv: Sequence[str] | None = None, *, generator: Generator | None = None) -> int:
    """``ask --spec MOD:SPEC "question" ...`` (see the module docstring). A leading
    ``ask`` word is skipped. `generator` replaces the CLI client (tests). Exit code: 0
    answered (any kind), 2 the question could not be asked."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "ask":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        from lattice.spec.__main__ import _runtime, _services, load

        spec = load(args.spec)
        if not isinstance(spec, Spec) and callable(spec):
            spec = spec()
        if not isinstance(spec, Spec):
            raise ValueError(f"{args.spec} is not a Spec")
        trace = Trace.load(args.trace) if args.trace else None
        cfg = None
        if args.verify:
            if not args.app or not args.db:
                raise ValueError("--verify needs --app and --db")
            cfg = VerifyConfig(
                app=load(args.app),
                db=load(args.db),
                login=load(args.login) if args.login else None,
                login_path=args.login_path,
                runtime=_runtime(args.runtime),
                services=_services(args.services),
                database=args.database,
                database_url=args.database_url,
            )
        if generator is None:
            from lattice.llm.cli_client import ClaudeCliClient

            generator = ClaudeCliClient(model=args.model, effort="", full_text=True)
        answer = ask(
            spec,
            args.question,
            trace=trace,
            selected=args.selected,
            generator=generator,
            model=args.model,
            verify=cfg,
        )
    except Exception as err:  # noqa: BLE001 -- any failure to ask is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    print(json.dumps(answer.to_dict(), indent=2, default=str) if args.json else answer.render())
    return 0


__all__ = [
    "Answer",
    "Digest",
    "Element",
    "Index",
    "Retrieval",
    "VerifyConfig",
    "approx_tokens",
    "ask",
    "ask_many",
    "build_digest",
    "check_citation",
    "core_text",
    "main",
    "retrieve",
    "setup_patterns",
    "terms",
]


if __name__ == "__main__":
    sys.exit(main())
