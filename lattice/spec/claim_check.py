"""`lattice.spec.claim_check`: the deterministic claim checker of the chat (`lattice.chatcheck/1`).

Spike F, chat round 2 (docs/results/2026-10-18-goto-chat-round2-prereg.md, section 2.1 and
Amendments 1-2).

**The binding rule.** A workflow failure is observed or logically determined, never predicted. Free
chat must not state a failure without a tier 1 (observed) or tier 2 (determined) item id of a
``lattice.impact/1`` report behind it. Round 1 measured that a prompt alone cannot meet a hard 0, so
this module checks the model's text after the fact and **replaces** (never relabels) what it cannot
support with Lattice's own text, or withholds it.

Pure: no model, no subprocess, no write. It reuses :mod:`lattice.spec.ask` (digest and
``check_citation``), :mod:`lattice.spec.appmap` and :mod:`lattice.spec.impact_ask` read-only;
none of them is edited.

**Units and the claim rule are the frozen round-1 scorer's, copied verbatim** (``FAIL_RE``,
``HEDGE_RE``, the splitter: lines, then ``(?<=[.!?])\\s+``); probe PC1 pins the equality.

The flow of a text: the pre-pass E2 (a Lattice-minted id that does not exist withholds its
unit), the dispositions D0-D7 for every unit that matches ``FAIL_RE``, then the enforcement of
:func:`tail` (E1 verbatim tier ids, E3 hypothetical without a change, the footer). The session path
checks each text block with :func:`check_block` and the turn once with :func:`tail`;
:func:`check_text` does both for one text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lattice.spec import appmap as _appmap
from lattice.spec import ask as _ask
from lattice.spec import impact_ask
from lattice.spec import implementation as impl

SCHEMA = "lattice.chatcheck/1"
IMPACT_SCHEMA = "lattice.impact/1"

# ---- the frozen round-1 scorer's rule, copied verbatim ------------------------------------------

FAIL_RE = re.compile(r"\b(fail|fails|failing|failed|break|breaks|broke|broken)\b", re.IGNORECASE)
# the scorer's hedge list "may|might|could|not decided|undecid|static|not run|no change"
HEDGE_RE = re.compile(
    r"\b(?:may|might|could|not decided|undecid\w*|static\w*|not run|no change\w*)\b", re.IGNORECASE
)
TID_RE = re.compile(r"\bt([1-4])-[0-9a-f]{10}\b")
TIER3_WORD_RE = re.compile(r"\btier\s*3\b", re.IGNORECASE)
_SPLIT = re.compile(r"(?<=[.!?])\s+")

# the owner's labels: the hedge list minus may / might / could (speculation is not a label)
OWNER_LABEL_RE = re.compile(
    r"\b(?:not decided|undecid\w*|static\w*|not run|no change\w*)\b", re.IGNORECASE
)
# the scorer's "not decided" phrases of the H check (copied): the owner's vocabulary for "no answer"
NOT_DECIDED_PHRASES = (
    "not decided",
    "undecided",
    "undecidable",
    "not decidable",
    "does not predict",
    "doesn't predict",
    "do not predict",
    "don't predict",
    "cannot predict",
    "can't predict",
    "does not decide",
    "doesn't decide",
    "cannot decide",
    "can't decide",
    "only for a drafted change",
    "needs a drafted change",
    "need a drafted change",
    "requires a drafted change",
    "no drafted change",
    "without a drafted change",
    "not determined",
    "cannot be determined",
    "can't be determined",
    "cannot say",
    "can't say",
    "cannot tell",
    "can't tell",
)
_DRAFT_RE = re.compile(r"\bdraft\w*\b", re.IGNORECASE)

ANY_TID = re.compile(r"\bt[1-4]-[0-9a-f]{10}\b")
T12_ID = re.compile(r"\bt[12]-[0-9a-f]{10}\b")
WF_RE = re.compile(r"\bwf-[0-9a-f]{10}\b")
RULE_RE = re.compile(r"(?<![\w:])(?:rule:)?(R\d+)\b")
_CHANGE_RE = re.compile(r"\bc\d+\b", re.IGNORECASE)
_MD_PREFIX = re.compile(r"^(?:(?:[-*+]|\d+[.)]|>)\s+)+")
_HEADING = re.compile(r"^#{1,6}\s+")
_BACKTICK_BOLD = re.compile(r"[`*]")

FOOTER_FIX = (
    "Lattice withheld {k} sentence(s) that stated a failure without a tier 1 or tier 2 item id. "
    "Lattice does not predict failures; it decides them only for a drafted change."
)
FOOTER_ID = "Lattice also withheld {k} sentence(s) that named an id Lattice did not mint."
NO_REPORT_TAIL = " Whether anything fails is not decided."
TIER3_FIXED = "Tier 3 items are static: they may fail and decide nothing."
TOUCH_CAP = 8
TIER3_RENDER_CAP = 5
ELEMENT_CAP = 3
WF_CAP = 6

COUNT_KEYS = (
    "kept",
    "supported",
    "labelled",
    "replaced_item",
    "replaced_tier3",
    "replaced_quote",
    "replaced_replay",
    "replaced_element",
    "withheld",
    "appended",
    "speculative_hedge",
    "unknown_id",
)
_ACTED = frozenset(
    {
        "replaced_item",
        "replaced_tier3",
        "replaced_quote",
        "replaced_replay",
        "replaced_element",
        "withheld",
    }
)


def new_counts() -> dict[str, int]:
    return dict.fromkeys(COUNT_KEYS, 0)


def merge_counts(*parts: Mapping[str, int]) -> dict[str, int]:
    out = new_counts()
    for p in parts:
        for k, v in p.items():
            out[k] = out.get(k, 0) + int(v)
    return out


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sentences(text: str) -> list[str]:
    """The frozen scorer's units: lines, then sentence ends (., ! or ? followed by white space)."""
    out: list[str] = []
    for line in (text or "").splitlines():
        for s in _SPLIT.split(line.strip()):
            if s.strip():
                out.append(s.strip())
    return out


def scorer_claims(text: str, relevant_ids: set[str]) -> list[str]:
    """The frozen scorer's ``failure_claims`` (probe PC1 compares the checker with it)."""
    return [
        s
        for s in sentences(text)
        if FAIL_RE.search(s) and not any(i in s for i in relevant_ids) and not HEDGE_RE.search(s)
    ]


def has_not_decided(text: str) -> bool:
    t = (text or "").replace("`", "").replace("**", "")
    low = t.lower()
    return any(p in low for p in NOT_DECIDED_PHRASES)


# ---- the relevant reports -----------------------------------------------------------------------


def item_message(item: Mapping[str, Any]) -> str:
    """The verbatim words of a tier 1 / tier 2 item: its ``message``, else the CLI's ``summary``
    (tier 1), ``claim`` (tier 2) or ``label``."""
    for key in ("message", "summary", "claim", "label"):
        v = item.get(key)
        if isinstance(v, str) and v:
            return v
    return str(item.get("target") or "")


def tier3_label(item: Mapping[str, Any]) -> str:
    if item.get("kind") == "may_fail" or "routes" in item:
        routes = ", ".join(str(r) for r in item.get("routes") or [])
        return (
            f"{item.get('target')} (a workflow on {routes})" if routes else str(item.get("target"))
        )
    what = str(item.get("what") or item.get("kind") or "")
    where = f" ({item['file']}:{item['line']})" if item.get("file") and item.get("line") else ""
    sym = f" [{item['symbol']}]" if item.get("symbol") else ""
    return f"{item.get('target')}: {what}{where}{sym}"


@dataclass
class ReportView:
    """One relevant change: its state and its items by id (tiers 1-3)."""

    change: str
    state: str = "no_change"
    why: str = ""
    t1: dict[str, dict[str, Any]] = field(default_factory=dict)
    t2: dict[str, dict[str, Any]] = field(default_factory=dict)
    t3: dict[str, dict[str, Any]] = field(default_factory=dict)
    impact: dict[str, Any] | None = None
    rules: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    @property
    def t12_ids(self) -> set[str]:
        return set(self.t1) | set(self.t2)

    def all_ids(self) -> set[str]:
        return set(self.t1) | set(self.t2) | set(self.t3)


def _index(items: list[Any]) -> dict[str, dict[str, Any]]:
    return {str(i["id"]): i for i in items if isinstance(i, dict) and i.get("id")}


def _add_report(view: ReportView, report: Mapping[str, Any], tier1: Mapping[str, Any]) -> None:
    view.t1.update(_index([*tier1.get("failing", []), *tier1.get("flows", [])]))
    t2 = report.get("tier2") or {}
    view.t2.update(_index([*t2.get("determined", []), *t2.get("flows", [])]))
    t3 = report.get("tier3") or {}
    view.t3.update(_index([*t3.get("locations", []), *t3.get("flows", [])]))
    diff = (report.get("diff") or {}).get("rules") or {}
    for key in ("added", "changed", "removed"):
        for r in diff.get(key, []) or []:
            rid = r.get("id") if isinstance(r, dict) else r
            if isinstance(rid, str):
                view.rules.add(rid)
    for it in [*view.t1.values(), *view.t2.values(), *view.t3.values()]:
        for r in it.get("rules") or []:
            if isinstance(r, str):
                view.rules.add(r)


def view_of_body(change: str, body: Mapping[str, Any]) -> ReportView:
    """The relevant report of a ``lattice.studio-impact/1`` body (what ``impact_view`` returns)."""
    view = ReportView(change, str(body.get("state") or "error"), str(body.get("why") or ""))
    if view.state != "ok" or not isinstance(body.get("report"), dict):
        if view.state == "ok":
            view.state = "error"
        return view
    saved = body.get("tier1_saved") or {}
    tier1: Mapping[str, Any] = (
        saved["tier1"] if saved.get("state") == "shown" and saved.get("tier1") else {}
    )
    _add_report(view, body["report"], tier1)
    view.impact = impact_ask._items(body["report"], saved)
    return view


# ---- the context --------------------------------------------------------------------------------


class CheckContext:
    """What the checker needs to know about one turn: the question, the open change, the reports
    (``lookup(change) -> lattice.studio-impact/1`` body, called at most once per change), the
    app map and the spec digest (lazily built, once), and this turn's ``change_impact`` results."""

    def __init__(
        self,
        question: str = "",
        *,
        open_change: str | None = None,
        lookup: Callable[[str], Mapping[str, Any]] | None = None,
        appmap: _appmap.AppMap | Callable[[], _appmap.AppMap | None] | None = None,
        digest: _ask.Digest | Callable[[], _ask.Digest | None] | None = None,
    ) -> None:
        self.question = question or ""
        self.open_change = open_change
        self._lookup = lookup
        self._appmap = appmap
        self._digest = digest
        self._am_done = self._dg_done = False
        self._am: _appmap.AppMap | None = None
        self._dg: _ask.Digest | None = None
        self._views: dict[str, ReportView] = {}
        self.turn_reports: dict[str, Mapping[str, Any]] = {}
        self.turn_ids: set[str] = set()
        self.turn_t1: set[str] = set()

    # -- this turn's tool results (change_impact)
    def add_tool_result(self, text: str) -> None:
        """Keep a ``change_impact`` result of this turn (memory only): its ids are real (Lattice
        minted them); a tier 1 id there is observed."""
        self.turn_ids |= set(ANY_TID.findall(text or ""))
        try:
            doc = json.loads(text)
        except (TypeError, ValueError):
            doc = None
        if isinstance(doc, dict) and doc.get("schema") == IMPACT_SCHEMA:
            inp = doc.get("input") or {}
            key = str(inp.get("change") or "candidate")
            self.turn_reports[key] = doc
            self._views.pop(key, None)
            t1 = doc.get("tier1") or {}
            self.turn_t1 |= {
                str(i.get("id"))
                for i in [*t1.get("failing", []), *t1.get("flows", [])]
                if i.get("id")
            }
        else:
            self.turn_t1 |= {m.group(0) for m in TID_RE.finditer(text or "") if m.group(1) == "1"}

    # -- lazily built inputs
    def am(self) -> _appmap.AppMap | None:
        if not self._am_done:
            self._am_done = True
            src = self._appmap
            try:
                am = src() if callable(src) else src
            except Exception:  # noqa: BLE001 -- no map is better than no answer
                am = None
            self._am = am if am is not None and am.state == "ok" else None
        return self._am

    def dg(self) -> _ask.Digest | None:
        if not self._dg_done:
            self._dg_done = True
            src = self._digest
            try:
                self._dg = src() if callable(src) else src
            except Exception:  # noqa: BLE001
                self._dg = None
        return self._dg

    # -- changes
    def changes_named(self, text: str) -> list[str]:
        out: list[str] = []
        for src in (self.question, self.open_change or "", text or ""):
            for m in _CHANGE_RE.finditer(src):
                cid = m.group(0).lower()
                if impact_ask.CHANGE_RE.match(cid) and cid not in out:
                    out.append(cid)
        for key in self.turn_reports:
            if impact_ask.CHANGE_RE.match(key) and key not in out:
                out.append(key)
        return out

    def enforced_changes(self) -> list[str]:
        """The changes whose record E1 may append: the ones the owner named and the open one."""
        out: list[str] = []
        for src in (self.question, self.open_change or ""):
            for m in _CHANGE_RE.finditer(src):
                cid = m.group(0).lower()
                if impact_ask.CHANGE_RE.match(cid) and cid not in out:
                    out.append(cid)
        return out

    def report(self, cid: str) -> ReportView:
        v = self._views.get(cid)
        if v is not None:
            return v
        if self._lookup is None:
            v = ReportView(cid, "impact_not_configured", "studio.toml has no [impact] table")
        else:
            try:
                v = view_of_body(cid, self._lookup(cid))
            except Exception as exc:  # noqa: BLE001 -- a lookup that breaks is "no report"
                v = ReportView(cid, "error", f"the report could not be built: {type(exc).__name__}")
        turn = self.turn_reports.get(cid)
        if turn is not None:
            if v.state != "ok":
                v = ReportView(cid, "ok")
            t1 = turn.get("tier1") or {}
            _add_report(v, turn, t1)
            v.impact = impact_ask._items(turn, {"state": "shown", "tier1": t1})
        self._views[cid] = v
        return v

    def views(self, text: str) -> list[ReportView]:
        return [self.report(c) for c in self.changes_named(text)]

    def other_views(self) -> list[ReportView]:
        """Turn reports that are not a Studio change (a candidate directory)."""
        return [self.report(k) for k in self.turn_reports if not impact_ask.CHANGE_RE.match(k)]

    def support_ids(self, text: str) -> set[str]:
        ids: set[str] = set(self.turn_t1)
        for v in [*self.views(text), *self.other_views()]:
            ids |= v.t12_ids
        return ids

    def known_ids(self, text: str) -> set[str]:
        ids: set[str] = set(self.turn_ids)
        for v in [*self.views(text), *self.other_views()]:
            ids |= v.all_ids()
        return ids

    # -- rules, workflows, machines
    def rule_ids(self) -> set[str] | None:
        am = self.am()
        if am is not None:
            return {r["id"] for r in am.doc["rules"]}
        dg = self.dg()
        if dg is not None and dg.with_rules:
            return {t.removeprefix("rule:") for t in dg.elements if t.startswith("rule:")}
        return None

    def wf_ids(self) -> set[str] | None:
        am = self.am()
        return {w["id"] for w in am.doc["workflows"]} if am is not None else None

    def rule_text(self, rid: str) -> str | None:
        dg = self.dg()
        if dg is not None:
            el = dg.elements.get(f"rule:{rid}")
            if el is not None:
                return el.content
        am = self.am()
        if am is not None:
            for r in am.doc["rules"]:
                if r["id"] == rid:
                    return str(r["text"])
        return None


# ---- renderings (Lattice's own text, from the report, the map and ReviewData) -------------------


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def render_item(item_id: str, tier: int, message: str, change: str) -> str:
    """``<id>`` (tier N, observed/determined, cN): the message verbatim. The change id keeps the id
    known when the line is checked again. Every unit of a message that holds a failure word keeps
    the item id (a message with sentence ends is cut per unit)."""
    basis = "observed" if tier == 1 else "determined"
    head = f"`{item_id}` (tier {tier}, {basis}, {change}): "
    parts = _SPLIT.split(_one_line(message))
    out = [head + parts[0]]
    out += [f"`{item_id}` (cont.): {p}" if FAIL_RE.search(p) else p for p in parts[1:]]
    return " ".join(out) if len(out) == 1 else "\n".join(out)


def render_tier3(item_id: str, item: Mapping[str, Any], change: str) -> str:
    label = _one_line(tier3_label(item))
    return f"`{item_id}` (tier 3, static: may fail; not a claim, {change}): {label}"


def record_from_impact(change: str, impact: Mapping[str, Any]) -> str:
    """The Lattice record of a change from the router's ``impact`` value (``impact_ask._items``):
    its fixed template line and one line per tier 1 and tier 2 item, verbatim."""
    lines = [f"Lattice record ({change}): {impact_ask._tier_template(impact)}"]
    t1 = [*impact["tier1"]["failing"], *impact["tier1"]["flows"]]
    t2 = [*impact["tier2"]["determined"], *impact["tier2"]["flows"]]
    for it in sorted(t1, key=lambda i: i["id"]):
        lines.append("- " + render_item(it["id"], 1, item_message(it), change))
    for it in sorted(t2, key=lambda i: i["id"]):
        lines.append("- " + render_item(it["id"], 2, item_message(it), change))
    return "\n".join(lines)


def record_block(view: ReportView) -> str:
    """The Lattice record of a change with an ok report (E1)."""
    assert view.impact is not None
    return record_from_impact(view.change, view.impact)


def no_report_sentence(view: ReportView) -> str:
    """The router's fixed ``not_specified`` sentence for a change without an ok report."""
    why = view.why or impact_ask._WHY.get(view.state, "")
    return f"Lattice has no impact report for {view.change}: {why}. Nothing is decided."


def render_machine(m: Mapping[str, Any]) -> str:
    """The map's row of a lifecycle: its basis, states and edges (with the side each edge is on)."""
    detail = {f"{e['from']} -> {e['to']}": e for e in m.get("edges_detail") or []}
    edges = list(m.get("edges") or [])
    for k in detail:
        if k not in edges:
            edges.append(k)
    shown = []
    for e in edges:
        d = detail.get(e)
        side = ""
        if d is not None:
            side = (
                " (spec, code)"
                if d["in_spec"] and d["in_code"]
                else (" (spec only)" if d["in_spec"] else " (code only)")
            )
        shown.append(e + side)
    return (
        f"Lifecycle `{m['key']}` (basis: {m.get('status')}; its states are code facts; whether "
        f"anything fails is not decided): states {', '.join(m.get('states') or [])}, "
        f"edges ({len(edges)}) {', '.join(shown) if shown else 'none'}"
    )


_STEP_ANSWERED = re.compile(r"\bstep (\d+)\b.*?\banswered (\d{3})\b")
_STEP_GOT = re.compile(r"\bstep (\d+)\b.*?\bexpected (\d{3}), got (\d{3})\b")


def render_workflow(w: Mapping[str, Any], *, note: bool = True) -> str:
    """The map's row of a workflow: its recorded replay status at capture time (an observed fact,
    ruling Q3), the step the replay stopped at when the reason names one."""
    status = w.get("effective") or w.get("status")
    out = (
        f"Workflow `{w['id']}` ({_one_line(w.get('name') or '')}): recorded replay status {status}"
    )
    out += " at capture time"
    reason = str(w.get("reason") or "")
    steps = {s.get("i"): s for s in w.get("steps") or []}
    m = _STEP_GOT.search(reason)
    if m:
        i, exp, got = int(m.group(1)), m.group(2), m.group(3)
        route = (steps.get(i) or {}).get("route")
        out += f", step {i}{f' ({route})' if route else ''} answered {got}, expected {exp}"
    else:
        m = _STEP_ANSWERED.search(reason)
        if m:
            i, got = int(m.group(1)), m.group(2)
            st = steps.get(i) or {}
            route = st.get("route")
            exp = st.get("expect_status")
            out += f", step {i}{f' ({route})' if route else ''} answered {got}"
            if exp is not None:
                out += f", expected {exp}"
    if note:
        out += "; what any change does to it is not decided."
    return out


def render_rule_quote(rid: str, text: str) -> str:
    """Spec text, quoted: every unit of the text that holds a failure word is preceded by a tag
    that holds an owner label, so the quote stays whole and no unit of it stands alone."""
    flat = _one_line(text)
    parts = _SPLIT.split(flat)
    first = f'{rid} (spec text, quoted; whether anything fails at run time is not decided here): "'
    body = parts[0]
    for p in parts[1:]:
        body += f" [{rid}, quoted; not decided here:] {p}" if FAIL_RE.search(p) else " " + p
    return first + body + '"'


def touches_line(am: _appmap.AppMap | None, key: str) -> str:
    if am is None:
        return "Static: no app map is available for it."
    try:
        t = _appmap.touches(am, key)
    except KeyError:
        return "Static: the app map has no such element."
    flat = [k for vals in t.values() for k in vals]
    if not flat:
        return "Static: the app map shows nothing that references it."
    shown = ", ".join(f"`{k}`" for k in flat[:TOUCH_CAP])
    more = f" (first {TOUCH_CAP} of {len(flat)})" if len(flat) > TOUCH_CAP else f" ({len(flat)})"
    return f"Static: it touches {shown}{more}."


def render_element(label: str, key: str | None, am: _appmap.AppMap | None) -> str:
    head = (
        f"Whether `{label}` fails is not decided: Lattice decides failures only for a drafted "
        "change (tier 1 observed, tier 2 determined)."
    )
    if key is None:
        return head + " Static: the app map has no such element."
    return head + " " + touches_line(am, key)


# ---- units --------------------------------------------------------------------------------------


@dataclass
class _Unit:
    index: int
    line: int
    pos: int  # index of the segment in its line
    text: str


@dataclass
class _Line:
    lead: str
    segs: list[str]
    seps: list[str]
    table: bool = False


def _split_lines(text: str) -> tuple[list[_Line], list[_Unit]]:
    lines: list[_Line] = []
    units: list[_Unit] = []
    for li, raw in enumerate((text or "").splitlines()):
        stripped = raw.strip()
        lead = raw[: len(raw) - len(raw.lstrip())]
        segs: list[str] = []
        seps: list[str] = []
        pos = 0
        for m in _SPLIT.finditer(stripped):
            segs.append(stripped[pos : m.start()])
            seps.append(m.group())
            pos = m.end()
        if stripped:
            segs.append(stripped[pos:])
        lines.append(_Line(lead, segs, seps, stripped.startswith("|")))
        for si, s in enumerate(segs):
            if s.strip():
                units.append(_Unit(len(units), li, si, s.strip()))
    return lines, units


# ---- the result ---------------------------------------------------------------------------------


@dataclass
class CheckResult:
    """``lattice.chatcheck/1``: the checked text, the acted-on units and the counts."""

    text: str
    units: list[dict[str, Any]]
    counts: dict[str, int]
    changed: bool = False
    withheld_claims: int = 0
    withheld_ids: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "text": self.text,
            "units": self.units,
            "counts": self.counts,
        }

    def canonical(self) -> str:
        return canonical(self.to_dict())


# ---- the unit pass ------------------------------------------------------------------------------


def _safe(repl: str, support: set[str]) -> bool:
    """A replacement is safe when the frozen rule finds no claim in it and every unit that holds a
    failure word carries an id of the support set or an owner label (so a second pass keeps it)."""
    for s in sentences(repl):
        if FAIL_RE.search(s) and not any(i in s for i in support) and not OWNER_LABEL_RE.search(s):
            return False
    return True


def _word_windows(unit: str, m: re.Match[str]) -> list[str]:
    before = re.findall(r"[A-Za-z0-9_']+", unit[: m.start()])
    after = re.findall(r"[A-Za-z0-9_']+", unit[m.end() :])
    word = m.group(0)
    out = []
    for b, a in ((2, 2), (1, 1), (1, 0), (0, 1)):
        left = before[len(before) - b :] if b else []
        out.append(" ".join([*left, word, *after[:a]]))
    return out


_LIFECYCLE_WORD = re.compile(
    r"\b(?:life[-\s]?cycles?|state[-\s]machines?|machines?|states?|transitions?|edges?)\b", re.I
)


def _names_machine(unit: str, m: Mapping[str, Any]) -> bool:
    low = unit.lower()
    key = str(m["key"])
    table, _, col = key.removeprefix("impl:machine:").partition(".")
    spec_target = str(m.get("spec_target") or "")
    if key.lower() in low or (spec_target and spec_target.lower() in low):
        return True
    if table and col and f"{table}.{col}".lower() in low:
        return True
    if spec_target:
        t2, _, c2 = spec_target.removeprefix("machine:").partition(".")
        if t2 and c2 and f"{t2}.{c2}".lower() in low:
            return True
    stem = re.escape(table.lower().rstrip("s")) if table else ""
    has_table = bool(stem) and bool(re.search(rf"\b{stem}s?\b", low))
    has_col = bool(col) and bool(re.search(rf"\b{re.escape(col.lower())}\b", low))
    if (has_table or has_col) and _LIFECYCLE_WORD.search(unit):
        return True
    states = [s for s in m.get("states") or []]
    hit = [s for s in states if re.search(rf"\b{re.escape(s)}\b", unit, re.IGNORECASE)]
    return len(set(hit)) >= 2


def _machine_for_failure(unit: str, am: _appmap.AppMap | None) -> Mapping[str, Any] | None:
    if am is None:
        return None
    for m in am.doc["machines"]:
        if not _names_machine(unit, m):
            continue
        names = {s.lower() for s in m.get("states") or []}
        ok = True
        for fm in FAIL_RE.finditer(unit):
            w = fm.group(0).lower()
            if w not in names and not any(w in e.lower().split() for e in m.get("edges") or []):
                ok = False
        if ok:
            return m
    return None


class _Pass:
    """One unit pass over a text (state: the context, the support and known ids)."""

    def __init__(self, text: str, ctx: CheckContext) -> None:
        self.ctx = ctx
        self.text = text
        self.support = ctx.support_ids(text)
        self.known = ctx.known_ids(text)
        self.rules = ctx.rule_ids()
        self.wfs = ctx.wf_ids()
        self.am = ctx.am()
        self.q_ids = set(ANY_TID.findall(ctx.question)) | set(WF_RE.findall(ctx.question))
        self.q_rules = {m.group(1) for m in RULE_RE.finditer(ctx.question)}
        self.views = {v.change: v for v in ctx.views(text)}
        self.known_rules: set[str] = set(self.rules or ())
        for v in [*self.views.values(), *ctx.other_views()]:
            self.known_rules |= v.rules

    # E2 -----------------------------------------------------------------------------------
    def unknown_ids(self, u: str) -> list[str]:
        bad = [i for i in ANY_TID.findall(u) if i not in self.known and i not in self.q_ids]
        if self.wfs is not None:
            bad += [i for i in WF_RE.findall(u) if i not in self.wfs and i not in self.q_ids]
        if self.rules is not None:
            bad += [
                r
                for r in (m.group(1) for m in RULE_RE.finditer(u))
                if r not in self.known_rules and r not in self.q_rules
            ]
        return bad

    # D2 ------------------------------------------------------------------------------------
    def _targets(self, u: str, tier_items: list[tuple[int, str, dict[str, Any], str]]) -> list[Any]:
        hits = []
        for tier, iid, item, change in tier_items:
            tgt = str(item.get("target") or "")
            if tgt and re.search(rf"(?<![\w:.-]){re.escape(tgt)}(?![\w])", u):
                hits.append((tier, iid, item, change))
        return hits

    def _items12(self) -> list[tuple[int, str, dict[str, Any], str]]:
        out: list[tuple[int, str, dict[str, Any], str]] = []
        for v in [*self.views.values(), *self.ctx.other_views()]:
            out += [(1, i, it, v.change) for i, it in v.t1.items()]
            out += [(2, i, it, v.change) for i, it in v.t2.items()]
        return out

    def _items3(self) -> list[tuple[int, str, dict[str, Any], str]]:
        out: list[tuple[int, str, dict[str, Any], str]] = []
        for v in [*self.views.values(), *self.ctx.other_views()]:
            out += [(3, i, it, v.change) for i, it in v.t3.items()]
        return out

    def d2(self, u: str) -> str | None:
        hits = self._targets(u, self._items12())
        if len({(t, i) for t, i, _, _ in hits}) != 1:
            return None
        tier, iid, item, change = hits[0]
        return render_item(iid, tier, item_message(item), change)

    def d3(self, u: str) -> str | None:
        named: list[tuple[int, str, dict[str, Any], str]] = []
        t3 = {i: (it, ch) for _, i, it, ch in self._items3()}
        for m in TID_RE.finditer(u):
            if m.group(1) == "3" and m.group(0) in t3:
                named.append((3, m.group(0), t3[m.group(0)][0], t3[m.group(0)][1]))
        named += self._targets(u, self._items3())
        seen: set[str] = set()
        uniq = [n for n in named if not (n[1] in seen or seen.add(n[1]))]
        has_word = bool(TIER3_WORD_RE.search(u)) or any(
            m.group(1) == "3" for m in TID_RE.finditer(u)
        )
        if not uniq and not has_word:
            return None
        if not uniq:
            return TIER3_FIXED
        lines = [render_tier3(i, it, ch) for _, i, it, ch in uniq[:TIER3_RENDER_CAP]]
        if len(uniq) > TIER3_RENDER_CAP:
            lines.append(
                f"({len(uniq) - TIER3_RENDER_CAP} more tier 3 items; static: they may fail and "
                "decide nothing)"
            )
        return "\n".join(lines)

    # D4 ------------------------------------------------------------------------------------
    def d4(self, u: str) -> str | None:
        dg = self.ctx.dg()
        out: list[str] = []
        if dg is not None:
            fms = list(FAIL_RE.finditer(u))
            for m in RULE_RE.finditer(u):
                rid = m.group(1)
                text = self.ctx.rule_text(rid)
                if text is None or f"rule:{rid}" not in dg.elements:
                    continue
                if all(
                    any(
                        _ask.check_citation(dg, {"target": f"rule:{rid}", "quote": q}) is None
                        for q in _word_windows(u, fm)
                    )
                    for fm in fms
                ):
                    line = render_rule_quote(rid, text)
                    if line not in out:
                        out.append(line)
        if out:
            return "\n".join(out)
        mach = _machine_for_failure(u, self.am)
        return render_machine(mach) if mach is not None else None

    # D5 ------------------------------------------------------------------------------------
    def d5(self, u: str) -> str | None:
        if self.am is None:
            return None
        by_id = {w["id"]: w for w in self.am.doc["workflows"]}
        ids = [i for i in dict.fromkeys(WF_RE.findall(u)) if i in by_id]
        if not ids:
            return None
        lines = [render_workflow(by_id[i]) for i in ids[:WF_CAP]]
        if len(ids) > WF_CAP:
            lines.append(
                f"({len(ids) - WF_CAP} more workflows; their recorded status is a capture-time "
                "fact; what any change does to them is not decided.)"
            )
        return "\n".join(lines)

    # D6 ------------------------------------------------------------------------------------
    def d6(self, u: str) -> str | None:
        labels: list[tuple[str, str | None]] = []
        keys = impact_ask.named_elements(self.am, u) if self.am is not None else []
        have = set(keys)
        for k in keys:
            labels.append((_pretty(k), k))
        for rk in impl.routes_in_text(u):
            if f"impl:route:{rk}" not in have:
                labels.append((rk, None))
        for cid in dict.fromkeys(m.group(0).lower() for m in _CHANGE_RE.finditer(u)):
            v = self.ctx.report(cid) if impact_ask.CHANGE_RE.match(cid) else None
            if v is not None and not v.ok:
                labels.append((cid, None))
        if not labels:
            return None
        return "\n".join(render_element(lab, key, self.am) for lab, key in labels[:ELEMENT_CAP])


def _pretty(key: str) -> str:
    if key.startswith("impl:route:"):
        return key.removeprefix("impl:route:")
    if key.startswith("impl:machine:"):
        return key
    return key


@dataclass
class _Disp:
    name: str
    reason: str
    after: str | None  # replacement text; None: withheld (or kept)
    flagged: bool = False
    speculative: bool = False


def _classify(u: str, p: _Pass) -> _Disp:
    bad = p.unknown_ids(u)
    if bad:
        return _Disp("withheld", "unknown_id: " + ", ".join(dict.fromkeys(bad)), None)
    if not FAIL_RE.search(u):
        return _Disp("kept", "", None)
    spec = bool(HEDGE_RE.search(u)) and not OWNER_LABEL_RE.search(u)
    if any(i in u for i in p.support):
        return _Disp("supported", "D0", None, True, spec)
    if OWNER_LABEL_RE.search(u):
        return _Disp("labelled", "D1", None, True, False)
    for name, reason, fn in (
        ("replaced_item", "D2", p.d2),
        ("replaced_tier3", "D3", p.d3),
        ("replaced_quote", "D4", p.d4),
        ("replaced_replay", "D5", p.d5),
        ("replaced_element", "D6", p.d6),
    ):
        repl = fn(u)
        if repl is not None and _safe(repl, p.support):
            return _Disp(name, reason, repl, True, spec)
    return _Disp("withheld", "D7", None, True, spec)


def _strip_prefix(seg: str) -> tuple[str, str]:
    m = _MD_PREFIX.match(seg)
    if m:
        return m.group(0), seg[m.end() :]
    return "", seg


def check_block(text: str, ctx: CheckContext) -> CheckResult:
    """The unit pass over one text (E2, D0-D7). No enforcement and no footer: the turn does that
    once (:func:`tail`). Pure."""
    if not text:
        return CheckResult(text or "", [], new_counts())
    p = _Pass(text, ctx)
    lines, units = _split_lines(text)
    counts = new_counts()
    recs: list[dict[str, Any]] = []
    replaced: dict[tuple[int, int], str | None] = {}
    notes: list[str] = []
    withheld_claims = withheld_ids = 0
    table_rows: dict[int, list[_Disp]] = {}
    for u in units:
        d = _classify(u.text, p)
        counts[d.name] += 1
        if d.name == "withheld":
            if d.reason.startswith("unknown_id"):
                counts["unknown_id"] += 1
                withheld_ids += 1
            else:
                withheld_claims += 1
        if d.speculative and d.name != "kept":
            counts["speculative_hedge"] += 1
        if d.name in ("kept", "supported", "labelled"):
            continue
        recs.append(
            {
                "i": u.index,
                "before": u.text,
                "disposition": d.name,
                "reason": d.reason,
                "after": d.after,
                "flagged": d.flagged,
                "speculative": d.speculative,
            }
        )
        if lines[u.line].table:
            table_rows.setdefault(u.line, []).append(d)
            continue
        replaced[(u.line, u.pos)] = d.after
    out_lines: list[str] = []
    for li, ln in enumerate(lines):
        if ln.table and li in table_rows:
            for d in table_rows[li]:
                if d.after:
                    notes.append(d.after)
            continue
        if not any((li, si) in replaced for si in range(len(ln.segs))):
            out_lines.append(ln.lead + _join(ln.segs, ln.seps) if ln.segs else "")
            continue
        parts: list[str] = []
        prefix = _strip_prefix(ln.segs[0])[0] if ln.segs else ""
        pending = ""
        for si, seg in enumerate(ln.segs):
            if (li, si) in replaced:
                repl = replaced[(li, si)]
                if repl is None:
                    if si == 0:
                        pending = prefix
                    continue
                if si == 0 and prefix:
                    repl = prefix + repl
                if parts and parts[-1] == repl:
                    continue
                parts.append(pending + repl)
            else:
                parts.append(pending + seg)
            pending = ""
        line = ln.lead + " ".join(parts) if parts else ""
        if line.strip() and _MD_PREFIX.sub("", line.strip()) == "":
            line = ""
        if line.strip():
            out_lines.append(line)
    new_text = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines))
    if notes:
        uniq_notes = list(dict.fromkeys(notes))
        new_text = (new_text.rstrip("\n") + "\n\n" if new_text.strip() else "") + "\n".join(
            uniq_notes
        )
    changed = bool(recs) and (new_text != text)
    return CheckResult(
        new_text if recs else text,
        recs,
        counts,
        changed,
        withheld_claims,
        withheld_ids,
        notes,
    )


def _join(segs: list[str], seps: list[str]) -> str:
    out = ""
    for i, s in enumerate(segs):
        out += s + (seps[i] if i < len(seps) else "")
    return out


# ---- enforcement and the footer -----------------------------------------------------------------


def tail(
    ctx: CheckContext,
    text: str,
    *,
    withheld_claims: int = 0,
    withheld_ids: int = 0,
) -> tuple[str, int]:
    """What the turn appends once: E1 (the Lattice record of a named change whose tier 1 / tier 2
    ids the text does not all state, or the router's fixed sentence for a change without an ok
    report), E3 (a hypothetical without a change) and the footer. Returns (text, appended)."""
    parts: list[str] = []
    appended = 0
    stated = set(T12_ID.findall(text))
    for cid in ctx.enforced_changes():
        v = ctx.report(cid)
        if v.ok:
            if v.t12_ids - stated:
                parts.append(record_block(v))
                appended += 1
        else:
            sentence = no_report_sentence(v)
            if sentence not in text:
                parts.append(sentence + NO_REPORT_TAIL)
                appended += 1
    q = ctx.question
    if q and not _CHANGE_RE.search(q) and _is_hypothetical(q):
        if not has_not_decided(text) or not _DRAFT_RE.search(text):
            if impact_ask.NO_PREDICTION not in text:
                parts.append(impact_ask.NO_PREDICTION)
                appended += 1
    if withheld_claims:
        parts.append(FOOTER_FIX.format(k=withheld_claims))
    if withheld_ids:
        parts.append(FOOTER_ID.format(k=withheld_ids))
    return "\n\n".join(parts), appended


def _is_hypothetical(question: str) -> bool:
    """The frozen hypothetical cue of the impact router, or the broader cue frozen in chat_route."""
    if any(p.search(question) for p in impact_ask.HYPOTHETICAL):
        return True
    from lattice.spec import chat_route  # noqa: PLC0415 -- chat_route imports this module

    return chat_route.hypothetical(question)


def result_changed(res: CheckResult, appended: int, tail_text: str) -> bool:
    return bool(res.changed or appended or tail_text)


def check_text(text: str, ctx: CheckContext) -> CheckResult:
    """The whole check of one text: the unit pass, then the tail appended after a blank line."""
    res = check_block(text, ctx)
    tail_text, appended = tail(
        ctx,
        res.text,
        withheld_claims=res.withheld_claims,
        withheld_ids=res.withheld_ids,
    )
    if tail_text:
        res.text = (res.text.rstrip("\n") + "\n\n" if res.text.strip() else "") + tail_text
        res.changed = True
    res.counts["appended"] += appended
    return res


def check_answer(answer: Mapping[str, Any], ctx: CheckContext) -> dict[str, Any]:
    """The one-shot ``POST /api/ask`` answer after the check: ``answer`` and ``explanation`` are
    checked; a yes / no / value answer whose ``answer`` text lost a unit is downgraded to
    ``not_specified`` with the fixed text (ask.py's downgrade rule); the key ``check`` (the counts)
    is added only when something changed, so an unchanged answer is byte-identical."""
    out = dict(answer)
    a = check_block(str(answer.get("answer") or ""), ctx)
    e = check_block(str(answer.get("explanation") or ""), ctx)
    joined = a.text + "\n\n" + e.text
    tail_text, appended = tail(
        ctx,
        joined,
        withheld_claims=a.withheld_claims + e.withheld_claims,
        withheld_ids=a.withheld_ids + e.withheld_ids,
    )
    counts = merge_counts(a.counts, e.counts)
    counts["appended"] += appended
    if not (a.changed or e.changed or tail_text):
        return out
    out["answer"] = a.text
    out["explanation"] = (e.text.rstrip("\n") + "\n\n" if e.text.strip() else "") + tail_text
    if out["explanation"].endswith("\n\n"):
        out["explanation"] = out["explanation"].rstrip("\n")
    if answer.get("kind") in ("yes", "no", "value") and (a.withheld_claims or a.withheld_ids):
        out["kind"] = "not_specified"
        out["answer"] = _ask.NOT_GROUNDED
        out["explanation"] = (
            "Lattice withheld the model's answer: it stated a failure without a tier 1 or tier 2 "
            "item id, or named an id Lattice did not mint. Nothing is decided. "
            + str(out["explanation"])
        ).strip()
    out["check"] = counts
    return out


__all__ = [
    "COUNT_KEYS",
    "FAIL_RE",
    "FOOTER_FIX",
    "HEDGE_RE",
    "SCHEMA",
    "CheckContext",
    "CheckResult",
    "ReportView",
    "check_answer",
    "check_block",
    "check_text",
    "has_not_decided",
    "merge_counts",
    "new_counts",
    "record_block",
    "record_from_impact",
    "scorer_claims",
    "sentences",
    "tail",
    "view_of_body",
]
