"""`lattice.spec.chat_route`: the model-free map router of the chat (`lattice.chatroute/1`).

Spike F, chat round 2 (docs/results/2026-10-18-goto-chat-round2-prereg.md, section 2.3).

It answers more question shapes without a model, from the app map (``lattice.appmap/1``) with the
same functions as ``GET /api/appmap``: six shapes (MR1 built?, MR2 workflows by route or status,
MR3 rules by status, MR4 lifecycle basis, MR5 access cell, MR6 broad hypothetical). It runs after
the impact router (:func:`lattice.spec.impact_ask.route`) in ``POST /api/ask`` and as a pre-route
in the session path. A question that matches no shape, or two shapes that disagree, is **not
routed**: the model answers, then the claim checker (lattice/spec/claim_check.py).

**Frozen.** The regular expressions below are frozen with this module's first commit; their hash is
recorded in ``docs/results/data/2026-10-18-goto-chat-round2/freeze-build.sha256`` before the
question set is scored. The question-set author never saw them.

Pure: no model, no process, no write. Every answer is a fixed template over map values verbatim;
``usage.tokens`` is 0.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lattice.spec import appmap as _appmap
from lattice.spec import claim_check as cc
from lattice.spec import impact_ask
from lattice.spec import implementation as impl

SCHEMA = "lattice.chatroute/1"
SHAPES = ("MR1", "MR2", "MR3", "MR4", "MR5", "MR6")
LIST_CAP = 200
CITE_CAP = 20
OBSERVED_CAP = 6
TOUCH_CAP = 12

# ---- the frozen patterns (R5; case-insensitive) -------------------------------------------------

_I = re.IGNORECASE
# a request to list: which / what / list / show / name / give / how many
LIST_CUE = re.compile(
    r"\b(?:which|what|list|show|name|enumerate|give\s+me|tell\s+me|how\s+many)\b", _I
)
BUILD_CUE = re.compile(
    r"\b(?:built|implemented|coded|available|"
    r"(?:does|do|did|is|are)\s+(?:it|this|that|the\s+\w+|\S+)\s+exist|"
    r"exists?\s+(?:in|yet|at\s+all|today|now|anywhere)|"
    r"(?:in|from)\s+the\s+(?:code|app|backend|server|api|codebase|implementation)|"
    r"(?:implementation|build|code)\s+status|"
    r"(?:does|do|did|is|are|has|have)\s+(?:the\s+)?(?:code|app|backend|server|api|codebase)\b"
    r".{0,30}\b(?:have|has|offer|serve|expose|provide|handle|implement|support|contain))\b",
    _I,
)
WORKFLOW_CUE = re.compile(
    r"\b(?:workflows?|flows?|scenario\s+replays?|replays?|replayed|captured)\b", _I
)
STATUS_UNVERIFIED = re.compile(r"\bunverified\b|\bnot\s+(?:yet\s+)?verified\b", _I)
STATUS_FAILED = re.compile(
    r"\b(?:failed|failing|did\s+not\s+pass|didn't\s+pass|do\s+not\s+pass|don't\s+pass|"
    r"not\s+pass(?:ed|ing)?|never\s+passed|no\s+longer\s+pass(?:es)?|broke|broken)\b",
    _I,
)
STATUS_VERIFIED = re.compile(r"\b(?:verified|passed|passing|passes|succeeded|successful)\b", _I)
RULE_CUE = re.compile(r"\b(?:rules?|requirements?)\b", _I)
RULE_ID = re.compile(r"(?<![\w:])(?:rule:)?R\d+\b")
WHY_CUE = re.compile(r"\b(?:why|where|how\s+does|how\s+is|explain)\b", _I)
# rule status phrases: (impl_status, pattern); several distinct statuses in one question: not routed
RULE_STATUS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "implemented_unverified",
        re.compile(
            r"\bimplemented\b.{0,30}\b(?:not\s+(?:yet\s+)?|un)verified\b|"
            r"\bimplemented_unverified\b|\bunverified\s+rules?\b|"
            r"\brules?\s+(?:\w+\s+){0,3}unverified\b",
            _I,
        ),
    ),
    (
        "required_not_implemented",
        re.compile(
            r"\bnot\s+(?:yet\s+)?(?:been\s+)?(?:built|implemented|coded)\b|"
            r"\brequired_not_implemented\b|"
            r"\b(?:missing|absent)\s+from\s+the\s+(?:code|implementation|codebase)\b|\bunbuilt\b|"
            r"\b(?:no|without)\s+code\s+yet\b|\bspec(?:ified)?\s+but\s+(?:not|no)\b",
            _I,
        ),
    ),
    (
        "no_element",
        re.compile(
            r"\bno[_\s]+element\b|\b(?:without|with\s+no)\s+(?:any\s+)?element\b|"
            r"\bnothing\s+in\s+the\s+code\b|\b(?:no|without)\s+(?:any\s+)?code\s+at\s+all\b",
            _I,
        ),
    ),
    (
        "implemented_verified",
        re.compile(
            r"\brules?\s+(?:that\s+|which\s+)?(?:are\s+|is\s+)?(?:implemented\s+and\s+)?verified\b|"
            r"\bverified\s+rules?\b|\bimplemented_verified\b",
            _I,
        ),
    ),
)
LIFECYCLE_WORD = re.compile(r"\b(?:life[-\s]?cycles?|state[-\s]machines?|machines?)\b", _I)
MACHINE_CUE = re.compile(
    r"\b(?:life[-\s]?cycles?|state[-\s]machines?|machines?|states?|transitions?|edges?)\b", _I
)
ACCESS_CUE = re.compile(
    r"\b(?:spec(?:ification)?\s+(?:expect|say|state|give)|expects?|expected|allowed|permitted|"
    r"authori[sz]ed|access|auth(?:entication|orization)?|who\s+can|refus\w*|forbidden|denied|"
    r"observ\w*|status(?:es)?|app\s+map|cell)\b",
    _I,
)
CALLER_NO_TOKEN = re.compile(
    r"\b(?:no\s+token|without\s+(?:a\s+)?token|anonymous(?:ly)?|unauthenticated|not\s+logged\s+in|"
    r"logged[-\s]out|anon)\b",
    _I,
)
# endpoints in the spec but not in the code (no exact route in the question)
ENDPOINT_WORD = re.compile(r"\b(?:endpoints?|routes?|apis?|handlers?)\b", _I)
NOT_IN_CODE = re.compile(
    r"\bnot\s+(?:yet\s+)?(?:been\s+)?(?:built|implemented|coded)\b|"
    r"\bnot\s+in\s+the\s+(?:code|app|backend|codebase|implementation)\b|"
    r"\b(?:spec[_\s]only|only\s+in\s+the\s+spec|unbuilt)\b|"
    r"\b(?:missing|absent)\s+from\s+the\s+(?:code|implementation|codebase|app|backend)\b|"
    r"\bin\s+the\s+spec\s+(?:but|and)\s+(?:not|no)\b|\b(?:no|without)\s+code\b|"
    r"\b(?:still\s+to\s+(?:build|implement)|left\s+to\s+(?:build|implement))\b",
    _I,
)
# MR6, the broad hypothetical: a counterfactual cue, a verb that changes the spec or the app, and
# no behaviour question ("what if a user ...") and no change id
COUNTERFACTUAL = re.compile(
    r"\b(?:suppose|supposing|imagine|hypothetical(?:ly)?|what\s+if|if\s+we|if\s+the\s+team|"
    r"were\s+to|if\s+i\s+(?:were|was)\s+to|let's\s+say\s+we|say\s+we)\b",
    _I,
)
# the impact phrasing of a hypothetical that has no counterfactual word ("what is touched if R28 is
# changed to ..."): an impact phrase, and a verb that changes the spec or the app
IMPACT_ASK = re.compile(
    r"\b(?:affect\w*|impact\w*|touch(?:ed|es)?|depend\w*|need(?:s|ed)?\s+to\s+(?:change|be\s+"
    r"(?:changed|updated|rewritten))|stop\s+working|still\s+(?:work|pass)|knock-?on|ripple)\b",
    _I,
)
CHANGE_VERB = re.compile(
    r"\b(?:rais|lower|add|remov|drop|renam|chang|widen|narrow|replac|shorten|extend|relax|tighten|"
    r"increas|decreas|reduc|switch|delet|disabl|enabl|allow|forbid|requir|introduc|split|merg|"
    r"retir|deprecat|doubl|halv|bump|alter|modif|updat|expand|rewrit|swap|flip|loosen|restrict|"
    r"stop|turn|cut|mak|set|ban|block|reorder|tweak|move|rework|redesign)\w*\b|"
    r"\b(?:gets?|got)\s+(?:a|an)\s+new\b",
    _I,
)
BEHAVIOUR = re.compile(
    r"\b(?:what\s+if|if|suppose|supposing|imagine)\s+(?:(?:a|an|the|my|some|that|any)\s+)?"
    r"(?:i|you|they|he|she|user|customer|caller|client|stranger|someone|somebody|person|request|"
    r"check-?in|token|password|email|account|session|attacker|payload|body|input)s?\b",
    _I,
)


def hypothetical(question: str) -> bool:
    """Whether the question is the broad hypothetical (MR6): cue and change verb, no behaviour
    question, no change id."""
    q = question or ""
    return bool(
        (COUNTERFACTUAL.search(q) or (IMPACT_ASK.search(q) and re.search(r"\bif\b", q, _I)))
        and CHANGE_VERB.search(q)
        and not BEHAVIOUR.search(q)
        and not impact_ask.CHANGE_ID.search(q)
    )


# ---- the answer ---------------------------------------------------------------------------------


@dataclass
class Routed:
    """A routed answer: the shape, the ``/api/ask`` fields and the Markdown the session shows."""

    shape: str
    kind: str
    answer: str
    explanation: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    rows: dict[str, Any] = field(default_factory=dict)
    impact: dict[str, Any] | None = None
    route: str = "map"  # the session's ``result.routed`` value: map | hypothetical

    @property
    def markdown(self) -> str:
        return (self.answer + "\n\n" + self.explanation).strip()

    def to_ask(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "answer": self.answer,
            "explanation": self.explanation,
            "citations": self.citations,
            "citations_checked": True,
            "rejected_citations": [],
            "verified": None,
            "usage": {"seconds": 0, "tokens": 0},
            "map": {"schema": SCHEMA, "shape": self.shape, **self.rows},
        }
        if self.impact is not None:
            out["impact"] = self.impact
        return out


_SOURCE = (
    "Source: the app map (static facts from the code and recorded statuses from the capture); "
    "whether anything fails after a change is not decided."
)


def _cite(target: str, quote: str) -> dict[str, Any]:
    return {"target": target, "step": None, "quote": quote}


def _ids(values: list[str], cap: int = LIST_CAP) -> str:
    shown = values[:cap]
    more = f" (and {len(values) - cap} more)" if len(values) > cap else ""
    return ", ".join(shown) + more


# ---- MR1 built? ---------------------------------------------------------------------------------


def _route_row(am: _appmap.AppMap, key: str) -> tuple[Mapping[str, Any] | None, Mapping[str, Any]]:
    ep = next((e for e in am.doc["endpoints"] if e["key"] == key), None)
    rt = next((r for r in am.report.get("routes", []) if r["key"] == key), None)
    return ep, rt or {}


def _status_sentence(key: str, ep: Mapping[str, Any] | None) -> str:
    if ep is None:
        return f"`{key}` is not in the app map: it is neither in the spec nor in the code."
    st = ep["status"]
    if st == "spec_only":
        return f"`{key}` is not built: it is in the spec, not in the code (spec_only)."
    if st == "test_only":
        return f"`{key}` is in the code only as a test route, not in the spec (test_only)."
    if st == "implemented":
        return f"`{key}` is implemented: it is in the spec and in the code (implemented)."
    return f"`{key}` has the endpoint status {st}."


def _mr1_route(am: _appmap.AppMap, key: str) -> Routed:
    ep, rt = _route_row(am, key)
    first = _status_sentence(key, ep)
    lines = []
    if rt.get("evidence"):
        lines.append("Code: " + ", ".join(sorted(set(rt["evidence"]))[:6]) + ".")
    needs = [
        r["id"]
        for r in am.doc["rules"]
        if r["impl_status"] == "required_not_implemented" and f"impl:route:{key}" in r["missing"]
    ]
    if needs:
        lines.append(
            f"Rules that need it and are required_not_implemented ({len(needs)}): "
            f"{_ids(needs, 10)}."
        )
    kind = "yes" if ep is not None and ep["status"] in ("implemented", "test_only") else "no"
    cites = [_cite(f"impl:route:{key}", first)] if ep is not None else []
    return Routed(
        "MR1",
        kind,
        first,
        "\n".join([*lines, _SOURCE]),
        cites,
        {"keys": [f"impl:route:{key}"], "status": ep["status"] if ep else None},
    )


def _mr1_list(am: _appmap.AppMap) -> Routed:
    rows = [e for e in am.doc["endpoints"] if e["status"] == "spec_only"]
    keys = [e["key"] for e in rows]
    if keys:
        body = (
            f"{len(keys)} endpoints are in the spec and not in the code (spec_only):\n"
            + "\n".join(f"- `{k}`" for k in keys)
        )
    else:
        body = "No endpoint is in the spec and not in the code (no spec_only endpoint)."
    return Routed(
        "MR1",
        "value",
        body,
        _SOURCE,
        [_cite(f"impl:route:{k}", f"{k}: spec_only") for k in keys[:CITE_CAP]],
        {"keys": [f"impl:route:{k}" for k in keys], "status": "spec_only"},
    )


# ---- MR2 workflows ------------------------------------------------------------------------------


def _wf_status(question: str) -> str | None:
    """The one recorded replay status the question asks for; a status word is read once (the
    failed and unverified phrases are cut out before the plain verified words are looked for)."""
    found = set()
    rest = question
    if STATUS_FAILED.search(rest):
        found.add("failed")
        rest = STATUS_FAILED.sub(" ", rest)
    if STATUS_UNVERIFIED.search(rest):
        found.add("unverified")
        rest = STATUS_UNVERIFIED.sub(" ", rest)
    if STATUS_VERIFIED.search(rest):
        found.add("verified")
    return next(iter(found)) if len(found) == 1 else None


def _mr2(am: _appmap.AppMap, question: str, route: str | None, status: str | None) -> Routed:
    rows = [
        w
        for w in am.doc["workflows"]
        if (route is None or route in w["routes"]) and (status is None or w["effective"] == status)
    ]
    what = f" that call `{route}`" if route else ""
    st = f" with recorded replay status {status}" if status else ""
    if rows:
        head = (
            f"{len(rows)} captured workflow(s){what}{st} (capture-time record; what any change "
            "does to them is not decided):"
        )
        body = "\n".join(
            "- " + cc.render_workflow(w, note=w["effective"] != "verified").replace("Workflow ", "")
            for w in rows
        )
        text = head + "\n" + body
    else:
        text = f"No captured workflow{what}{st} (capture-time record; nothing is decided)."
    return Routed(
        "MR2",
        "value",
        text,
        _SOURCE,
        [_cite(w["id"], w["name"]) for w in rows[:CITE_CAP]],
        {"keys": [w["id"] for w in rows], "route": route, "status": status},
    )


# ---- MR3 rules by status ------------------------------------------------------------------------


def _rule_status(question: str) -> str | None:
    hits = []
    for status, rx in RULE_STATUS:
        if rx.search(question):
            hits.append(status)
    # "implemented but not verified" also holds the plain word "verified" and "implemented": the
    # more specific phrase wins; two distinct specific phrases are ambiguous
    if "implemented_verified" in hits and len(hits) > 1:
        hits.remove("implemented_verified")
    return hits[0] if len(hits) == 1 else None


_RULE_GLOSS = {
    "implemented_unverified": "implemented in the code, not verified by a replay",
    "required_not_implemented": "required by the spec and not built",
    "no_element": "no element in the code at all",
    "implemented_verified": "implemented and verified by a replay",
}


def _mr3(am: _appmap.AppMap, status: str) -> Routed:
    rows = [r for r in am.doc["rules"] if r["impl_status"] == status]
    ids = [r["id"] for r in rows]
    gloss = _RULE_GLOSS[status]
    if ids:
        text = f"{len(ids)} rule(s) have impl_status `{status}` ({gloss}): {_ids(ids)}"
    else:
        text = f"No rule has impl_status `{status}` ({gloss})."
    return Routed(
        "MR3",
        "value",
        text,
        _SOURCE,
        [_cite(f"rule:{r['id']}", r["text"]) for r in rows[:CITE_CAP]],
        {"keys": [f"rule:{i}" for i in ids], "impl_status": status},
    )


# ---- MR4 lifecycle ------------------------------------------------------------------------------


def _machine_named(am: _appmap.AppMap, question: str) -> list[Mapping[str, Any]]:
    low = question.lower()
    wide = bool(LIFECYCLE_WORD.search(question))
    out = []
    for m in am.doc["machines"]:
        key = str(m["key"])
        table, _, col = key.removeprefix("impl:machine:").partition(".")
        target = str(m.get("spec_target") or "").removeprefix("machine:")
        ok = key.lower() in low or (target and f"machine:{target}".lower() in low)
        for t in {table, target.partition(".")[0]}:
            c = col or target.partition(".")[2]
            if t and c and f"{t}.{c}".lower() in low:
                ok = True
            stem = t.lower().rstrip("s")
            has_table = bool(t) and bool(re.search(rf"\b{re.escape(stem)}s?\b", low))
            has_col = bool(c) and bool(re.search(rf"\b{re.escape(c.lower())}\b", low))
            if (has_table and has_col) or (has_table and wide):
                ok = True
        if ok:
            out.append(m)
    return out


def _mr4(am: _appmap.AppMap, machines: list[Mapping[str, Any]]) -> Routed:
    blocks = []
    keys = []
    cites = []
    for m in machines:
        keys.append(m["key"])
        lines = [cc.render_machine(m)]
        if m.get("spec_target"):
            lines.append(f"Spec target: `{m['spec_target']}`.")
        basis = "read from the code, not observed" if m["status"] == "static" else m["status"]
        lines.append(f"Basis: {m['status']} ({basis}).")
        if m.get("rules"):
            lines.append(f"Rules: {_ids(list(m['rules']))}.")
        if m.get("evidence"):
            lines.append("Code: " + ", ".join(m["evidence"]) + ".")
        wfs = [w["id"] for w in am.doc["workflows"] if m["id"] in w["machine_refs"]]
        if wfs:
            lines.append(f"Workflows that reference it: {_ids(wfs, 12)}.")
        blocks.append("\n".join(lines))
        cites.append(_cite(m["key"], cc.render_machine(m)))
    return Routed(
        "MR4",
        "value",
        "\n\n".join(blocks),
        _SOURCE,
        cites,
        {"keys": keys},
    )


# ---- MR5 access cell ----------------------------------------------------------------------------


def _callers(am: _appmap.AppMap, question: str) -> list[str]:
    rows: list[str] = []
    for g in am.doc["access"]["groups"]:
        for r in g["rows"]:
            if r not in rows:
                rows.append(r)
    out = []
    for r in rows:
        if r == "no token":
            if CALLER_NO_TOKEN.search(question):
                out.append(r)
        elif re.search(rf"(?<![\w-]){re.escape(r)}(?![\w-])", question, _I):
            out.append(r)
    return out


def _cell_lines(c: Mapping[str, Any]) -> list[str]:
    key = f"cell:{c['row']}|{c['col']}"
    spec = c["spec"]
    head = f"Access cell `{c['row']} | {c['col']}` ({key}):"
    spec_line = (
        f"- The spec expects {spec}."
        if spec != "·"
        else "- The spec does not specify this cell (not specified)."
    )
    status = c.get("route_status")
    if c["route"] is None:
        code = (
            "- The code has no such route: it is not built (spec_only)."
            if status == "spec_only"
            else "- The app map has no route for this cell."
        )
    else:
        refusals = ", ".join(c["refusals"] or []) or "none listed"
        code = (
            f"- Code: the route is implemented ({status}); auth {c['auth']}; refusals {refusals}."
        )
    obs = c["observed"]
    if obs:
        shown = "; ".join(
            f"status {o['status']} in {o['flow']} step {o['step']}" for o in obs[:OBSERVED_CAP]
        )
        more = f" (and {len(obs) - OBSERVED_CAP} more)" if len(obs) > OBSERVED_CAP else ""
        observed = f"- Observed at capture by verified workflows: {shown}{more}."
    else:
        observed = "- No verified workflow observed it (nothing observed at capture)."
    return [head, spec_line, code, observed]


def _mr5(am: _appmap.AppMap, rows: list[str], route: str) -> Routed | None:
    cells = {(c["row"], c["col"]): c for c in am.doc["access"]["cells"]}
    picked = [cells[(r, route)] for r in rows if (r, route) in cells]
    if not picked:
        return None
    lines: list[str] = []
    for c in picked:
        lines += _cell_lines(c)
    return Routed(
        "MR5",
        "value",
        "\n".join(lines),
        _SOURCE,
        [_cite(f"cell:{c['row']}|{c['col']}", str(c["spec"])) for c in picked],
        {"keys": [f"cell:{c['row']}|{c['col']}" for c in picked], "route": route},
    )


# ---- MR6 broad hypothetical ---------------------------------------------------------------------


def _mr6(
    question: str,
    appmap: Callable[[], _appmap.AppMap | None] | _appmap.AppMap | None,
    selected: list[str],
) -> Routed:
    am = appmap() if callable(appmap) else appmap
    names = impact_ask.named_elements(am, " ".join([question, *selected]))
    touched = {k: _appmap.touches(am, k) for k in names} if am is not None else {}
    explain = (
        f"{impact_ask._STATIC_NOTE} They are listed under impact.touches."
        if touched
        else "No element was named exactly (METHOD /path, Rn, wf-... or machine:T.c), so "
        "there is no static neighbourhood to list."
    )
    ans = impact_ask._answer(
        "not_specified",
        impact_ask.NO_PREDICTION,
        explain,
        [],
        {
            "change": None,
            "state": "no_change",
            "tiers": None,
            "touches": touched,
            "basis": "static",
            "meaning": _appmap.MEANING,
        },
    )
    return Routed(
        "MR6",
        "not_specified",
        ans["answer"],
        _touch_markdown(explain, touched),
        [],
        {"keys": list(touched)},
        ans["impact"],
        "hypothetical",
    )


def _touch_markdown(explain: str, touched: Mapping[str, Mapping[str, list[str]]]) -> str:
    lines = [explain.replace(" They are listed under impact.touches.", "")]
    for key, groups in touched.items():
        flat = [
            f"{name}: "
            + ", ".join(vals[:TOUCH_CAP])
            + (f" (+{len(vals) - TOUCH_CAP} more)" if len(vals) > TOUCH_CAP else "")
            for name, vals in groups.items()
            if vals
        ]
        lines.append(
            f"- `{key}` touches " + ("; ".join(flat) if flat else "nothing in the map") + "."
        )
    return "\n".join(lines)


# ---- the router ---------------------------------------------------------------------------------


def _is_c(question: str) -> bool:
    return bool(impact_ask.CHANGE_ID.search(question))


def route(
    question: str,
    *,
    appmap: Callable[[], _appmap.AppMap | None] | _appmap.AppMap | None = None,
    selected: list[str] | None = None,
) -> Routed | None:
    """The routed answer to `question`, or ``None`` (not routed: the model answers). Call it after
    :func:`lattice.spec.impact_ask.route`; a question with a change id is never routed here."""
    q = (question or "").strip()
    if not q or _is_c(q):
        return None
    if hypothetical(q):
        return _mr6(q, appmap, list(selected or []))
    am = appmap() if callable(appmap) else appmap
    if am is None or am.state != "ok":
        return None
    routes = list(dict.fromkeys(impl.routes_in_text(q)))
    exact = [r for r in routes if any(e["key"] == r for e in am.doc["endpoints"])]
    one_route = routes[0] if len(routes) == 1 else None
    callers = _callers(am, q)
    machines = _machine_named(am, q) if MACHINE_CUE.search(q) else []
    hits: dict[str, Routed | None] = {}

    # MR5: a caller and an exact route
    if callers and one_route is not None and len(callers) <= 3 and ACCESS_CUE.search(q):
        r5 = _mr5(am, callers, one_route)
        if r5 is not None:
            hits["MR5"] = r5
    # MR1: an exact route and a build verb, or the list of endpoints not in the code
    if one_route is not None and BUILD_CUE.search(q):
        hits["MR1"] = _mr1_route(am, one_route)
    elif not routes and ENDPOINT_WORD.search(q) and NOT_IN_CODE.search(q) and LIST_CUE.search(q):
        hits["MR1"] = _mr1_list(am)
    # MR2: workflows by route and/or status
    if WORKFLOW_CUE.search(q) and not RULE_ID.search(q) and not _names_workflow(q):
        status = _wf_status(q)
        if one_route is not None and (len(routes) == 1) and exact:
            hits["MR2"] = _mr2(am, q, one_route, status)
        elif not routes and status is not None and LIST_CUE.search(q) and not RULE_CUE.search(q):
            hits["MR2"] = _mr2(am, q, None, status)
    # MR3: rules by status
    if (
        RULE_CUE.search(q)
        and not RULE_ID.search(q)
        and LIST_CUE.search(q)
        and not WHY_CUE.search(q)
    ):
        st = _rule_status(q)
        if st is not None and not (WORKFLOW_CUE.search(q) and "MR2" in hits):
            hits["MR3"] = _mr3(am, st)
    # MR4: a lifecycle
    if machines and not routes:
        hits["MR4"] = _mr4(am, machines)
    names = [s for s, r in hits.items() if r is not None]
    if not names:
        return None
    if len(names) == 1:
        return hits[names[0]]
    if "MR5" in names and set(names) <= {"MR1", "MR2", "MR5"}:
        return hits["MR5"]
    return None


def _names_workflow(q: str) -> bool:
    """A question that names one workflow is not a list."""
    return bool(cc.WF_RE.search(q))


# ---- rendering the F router's answers as Markdown (the session path) ----------------------------


def answer_markdown(answer: Mapping[str, Any]) -> str:
    """The Markdown of a router answer (``impact_ask.route`` or :meth:`Routed.to_ask`)."""
    imp = answer.get("impact")
    if isinstance(imp, Mapping) and imp.get("change") and imp.get("state") == "ok":
        return (
            cc.record_from_impact(str(imp["change"]), imp)
            + "\n\n"
            + "Tier 3 items are static: they may fail and decide nothing. Tier 4 is not run."
        )
    if isinstance(imp, Mapping) and imp.get("touches") is not None:
        touched = imp["touches"]
        explain = (
            impact_ask._STATIC_NOTE
            if touched
            else "No element was named exactly (METHOD /path, Rn, wf-... or machine:T.c), so "
            "there is no static neighbourhood to list."
        )
        return (str(answer["answer"]) + "\n\n" + _touch_markdown(explain, touched)).strip()
    return (str(answer.get("answer") or "") + "\n\n" + str(answer.get("explanation") or "")).strip()


def routed_kind(answer: Mapping[str, Any]) -> str:
    """``impact`` for the impact router's change answer, ``hypothetical`` for its hypothetical,
    ``map`` for this module's answers (the session's ``result.routed`` value)."""
    imp = answer.get("impact")
    if isinstance(imp, Mapping):
        if imp.get("change"):
            return "impact"
        return "hypothetical"
    return "map"


__all__ = [
    "SCHEMA",
    "SHAPES",
    "Routed",
    "answer_markdown",
    "hypothetical",
    "route",
    "routed_kind",
]
