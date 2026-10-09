"""`lattice.spec.review`: the page the requirements owner reads to approve an
LLM-drafted spec, so they never have to read the Python.

`review_markdown` / `review_html` render a `Spec` together with its `Trace`
(`lattice.spec.rules`) -- the link from the owner's numbered requirements to the spec
elements that check them -- plus a lint run's findings and a probe run's results, as
one page with seven sections, in order:

1. Summary -- counts and a one-line verdict.
2. Decisions for you -- the trace's open questions, their defaults, and the rules they
   touch.
3. Gaps -- the things the spec does *not* check: untraced rules, lint findings, probe
   gaps and the unchecked notes (both the trace's per-rule reasons and the spec's own
   `Unchecked` entries).
4. Rule by rule -- one row per requirement: its text, what checks it (linked into
   section 6) and its status.
5. Access at a glance -- a caller x endpoint matrix, derived from the scenarios.
6. What the spec checks -- the invariants, machines, services and scenarios, in plain
   English.
7. The back-translation -- the full `lattice.spec.authoring.requirements_markdown`
   output, for the owner to read side by side with their own document.

A rule's status in section 4 is one of: "checked" (something links it and no probe
result says the link is a gap), "unchecked" (`Trace.unchecked` gives a reason),
"context" (`Trace.context`), "untraced" (none of the above -- `Trace.untraced()`), or
"probe gap" (it is linked, but a probe result for it is `"gap"`: the check that was
supposed to catch a violation of this rule does not).
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from lattice.spec.authoring import requirements_markdown
from lattice.spec.model import Await, Machine, Spec, Step, Unchecked
from lattice.spec.rules import Rule, Trace

__all__ = ["review_markdown", "review_html", "main"]

_STATUSES = ("checked", "unchecked", "context", "untraced", "probe_gap")
_STATUS_ICON = {
    "checked": "✅",  # checkmark
    "unchecked": "⚠️",  # warning
    "context": "—",  # em dash
    "untraced": "❌",  # cross
    "probe_gap": "\U0001f573",  # hole
}
_STATUS_LABEL = {
    "checked": "checked",
    "unchecked": "unchecked",
    "context": "context",
    "untraced": "untraced",
    "probe_gap": "probe gap",
}

_SEG_NUM = re.compile(r"^-?\d+$")
_SEG_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# ---- the data the two renderers share -------------------------------------------------


@dataclass
class RuleRow:
    id: str
    text: str
    links: tuple[str, ...]
    status: str
    note: str


@dataclass
class QuestionView:
    id: str
    text: str
    default: str
    rules: tuple[str, ...]
    answer: str | None


@dataclass
class LintGap:
    code: str
    rule_id: str
    rule_text: str
    element: str
    message: str
    fix: str


@dataclass
class ProbeGap:
    rule_id: str
    rule_text: str
    patch_summary: str


@dataclass
class UncheckedTraceGap:
    rule_id: str
    rule_text: str
    reason: str


@dataclass
class AccessGroup:
    prefix: str
    columns: tuple[str, ...]
    rows: tuple[str, ...]
    cells: Mapping[tuple[str, str], str]


@dataclass
class ScenarioView:
    target: str
    name: str
    steps: tuple[str, ...]


@dataclass
class Section6:
    invariants: tuple[tuple[str, str, str], ...]  # (target, name, sentence)
    machines: tuple[tuple[str, str, str], ...]  # (target, label, moves)
    services: tuple[tuple[str, str, str], ...]  # (target, name, doc)
    scenarios: tuple[ScenarioView, ...]


@dataclass
class ReviewData:
    title: str
    counts: Mapping[str, Any]
    verdict: str
    questions: tuple[QuestionView, ...]
    untraced: tuple[Rule, ...]
    lint_gaps: tuple[LintGap, ...]
    probe_gaps: tuple[ProbeGap, ...]
    unchecked_trace: tuple[UncheckedTraceGap, ...]
    unchecked_notes: tuple[Unchecked, ...]
    rule_rows: tuple[RuleRow, ...]
    access_groups: tuple[AccessGroup, ...]
    section6: Section6
    back_translation: str


def _short(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _short_value(value: Any, limit: int = 140) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _slug(target: str) -> str:
    """An HTML-safe anchor id for a link target (``<kind>:<name>``), shared by both
    renderers so a rule's "how checked" link always lands on the matching element of
    section 6."""
    s = re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-")
    return f"el-{s}" if s else "el"


def _normalize_path(path: str) -> str:
    """`path` with its id-shaped segments (plain integers, UUIDs) turned into ``{}``."""
    parts = path.split("/")
    out = [
        "{}" if seg and (_SEG_NUM.match(seg) or _SEG_UUID.match(seg)) else seg for seg in parts
    ]
    return "/".join(out)


def _path_prefix(template: str) -> str:
    _, _, path = template.partition(" ")
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else "/"


def _db_fact(table: str, cond: Mapping[str, Any]) -> str:
    where = cond.get("where")
    where_text = f" where {where}" if where else ""
    if "count" in cond:
        return f"{table}{where_text} count={cond['count']}"
    return f"{table}{where_text} equals {cond.get('equals')}"


def _step_line(step: Step | Await) -> str:
    """One step of a scenario, in plain English (as `requirements_markdown` renders
    one, but as a numbered-list line)."""
    if isinstance(step, Await):
        facts = "; ".join(_db_fact(t, c) for t, c in step.expect_db.items())
        note = f" ({step.note})" if step.note else ""
        return f"wait for the {step.service} service: {facts} within {step.within_s:g}s{note}"
    who = step.user or "no token"
    bits = []
    if step.expect_body is not None:
        bits.append(f"body {_short_value(step.expect_body)}")
    if step.expect_db:
        bits.append("; ".join(_db_fact(t, c) for t, c in step.expect_db.items()))
    fact = "; ".join(bits)
    tail = f" -- {fact}" if fact else ""
    return f"{step.method} {step.path} as {who} -> {step.expect_status}{tail}"


def _machine_moves(m: Machine) -> str:
    moves: dict[str, list[str]] = {}
    for src, dst in m.edges:
        moves.setdefault(src, []).append(dst)
    parts = [f"{src} -> {', '.join(dsts)}" for src, dsts in moves.items()]
    initial = f" (initial: {', '.join(m.initial)})" if m.initial else ""
    return "; ".join(parts) + initial


def _build_access(spec: Spec) -> tuple[AccessGroup, ...]:
    """The caller x endpoint matrix of `_access_at_a_glance` (section 5), derived from
    `spec.scenarios`'s `Step`s (an `Await` names no caller or endpoint, so it takes no
    part). Rows: every declared user, plus "no token"; columns: endpoint templates
    (path parameters normalised to ``{}``), grouped by their first path segment."""
    cells: dict[tuple[str, str], set[int]] = {}
    templates: list[str] = []
    for scenario in spec.scenarios:
        for step in scenario.steps:
            if not isinstance(step, Step):
                continue
            caller = step.user if step.user is not None else "no token"
            template = f"{step.method} {_normalize_path(step.path)}"
            if template not in templates:
                templates.append(template)
            cells.setdefault((caller, template), set()).add(step.expect_status)
    rows = (*spec.users, "no token")
    groups: dict[str, list[str]] = {}
    for t in templates:
        groups.setdefault(_path_prefix(t), []).append(t)
    result = []
    for prefix, columns in groups.items():
        cell_text = {
            (row, col): "/".join(str(s) for s in sorted(cells[row, col]))
            if (row, col) in cells
            else "·"
            for row in rows
            for col in columns
        }
        result.append(AccessGroup(prefix, tuple(columns), rows, cell_text))
    return tuple(result)


def _build_section6(spec: Spec) -> Section6:
    invariants = tuple((f"invariant:{i.name}", i.name, i.sentence) for i in spec.invariants)
    machines = tuple(
        (f"machine:{m.table}.{m.column}", f"{m.table}.{m.column}", _machine_moves(m))
        for m in spec.machines
    )
    services = tuple((f"service:{s.name}", s.name, s.doc) for s in spec.services)
    scenarios = tuple(
        ScenarioView(f"scenario:{sc.name}", sc.name, tuple(_step_line(s) for s in sc.steps))
        for sc in spec.scenarios
    )
    return Section6(invariants, machines, services, scenarios)


def _build(
    spec: Spec,
    trace: Trace,
    lint: list[dict] | None,
    probe: list[dict] | None,
    title: str,
) -> ReviewData:
    lint = list(lint or [])
    probe = list(probe or [])
    rules_by_id = {r.id: r for r in trace.rules}
    probe_gap_ids = {p["rule_id"] for p in probe if p.get("status") == "gap"}

    rule_rows: list[RuleRow] = []
    n_checked = n_unchecked = n_context = n_probe_gap = 0
    for r in trace.rules:
        links = tuple(trace.links.get(r.id, ()))
        if r.id in probe_gap_ids:
            status = "probe_gap"
            n_probe_gap += 1
        elif links:
            status = "checked"
            n_checked += 1
        elif r.id in trace.unchecked:
            status = "unchecked"
            n_unchecked += 1
        elif r.id in trace.context:
            status = "context"
            n_context += 1
        else:
            status = "untraced"
        note = trace.unchecked.get(r.id, "") if status == "unchecked" else ""
        rule_rows.append(RuleRow(r.id, r.text, links, status, note))
    untraced = tuple(trace.untraced())

    questions = tuple(
        QuestionView(q.id, q.text, q.default, tuple(q.rules), q.answer) for q in trace.questions
    )
    n_open_q = sum(1 for q in questions if q.answer is None)

    lint_gaps = tuple(
        LintGap(
            item["code"],
            item["rule_id"],
            rules_by_id[item["rule_id"]].text if item["rule_id"] in rules_by_id else "",
            item.get("element", ""),
            item.get("message", ""),
            item.get("fix", ""),
        )
        for item in lint
    )
    lint_by_code = Counter(item["code"] for item in lint)

    probe_gaps = tuple(
        ProbeGap(
            p["rule_id"],
            rules_by_id[p["rule_id"]].text if p["rule_id"] in rules_by_id else "",
            p.get("patch_summary", ""),
        )
        for p in probe
        if p.get("status") == "gap"
    )

    unchecked_trace = tuple(
        UncheckedTraceGap(rid, rules_by_id[rid].text if rid in rules_by_id else "", reason)
        for rid, reason in trace.unchecked.items()
    )
    unchecked_notes = tuple(spec.unchecked)

    counts = {
        "checked": n_checked,
        "unchecked": n_unchecked,
        "context": n_context,
        "untraced": len(untraced),
        "probe_gap": n_probe_gap,
        "open_questions": n_open_q,
        "lint_total": len(lint),
        "lint_by_code": lint_by_code,
        "probe_gaps_total": len(probe_gaps),
    }
    attention = len(untraced) + len(lint) + len(probe_gaps) + n_open_q
    verdict = "ready to approve" if attention == 0 else f"{attention} items need your attention"

    return ReviewData(
        title=title,
        counts=counts,
        verdict=verdict,
        questions=questions,
        untraced=untraced,
        lint_gaps=lint_gaps,
        probe_gaps=probe_gaps,
        unchecked_trace=unchecked_trace,
        unchecked_notes=unchecked_notes,
        rule_rows=tuple(rule_rows),
        access_groups=_build_access(spec),
        section6=_build_section6(spec),
        back_translation=requirements_markdown(spec, title=title),
    )


# ---- markdown ---------------------------------------------------------------------------


def _md_summary(data: ReviewData) -> list[str]:
    c = data.counts
    by_code = ", ".join(f"{code}: {n}" for code, n in sorted(c["lint_by_code"].items()))
    lines = [
        "## Summary",
        "",
        f"- Rules: {c['checked']} checked, {c['unchecked']} unchecked, "
        f"{c['context']} context, {c['untraced']} untraced, {c['probe_gap']} probe gap",
        f"- Open questions: {c['open_questions']}",
        f"- Lint findings: {c['lint_total']} ({by_code or 'none'})",
        f"- Probe gaps: {c['probe_gaps_total']}",
        "",
        f"**Verdict: {data.verdict}**",
        "",
    ]
    return lines


def _md_decisions(data: ReviewData) -> list[str]:
    lines = ["## Decisions for you", ""]
    if not data.questions:
        return [*lines, "None.", ""]
    for q in data.questions:
        lines.append(f"### {q.id}: {q.text}")
        lines.append(f"- Default: {q.default}")
        lines.append(f"- Rules: {', '.join(q.rules) if q.rules else '-'}")
        answer = q.answer if q.answer is not None else f"(using default: {q.default})"
        lines.append(f"- Answer: {answer}")
        lines.append("")
    return lines


def _md_gaps(data: ReviewData) -> list[str]:
    lines = ["## Gaps", "", "These are the things the spec does not check.", ""]

    lines.append("### Untraced rules")
    lines.append("")
    if data.untraced:
        lines += [f"- **{r.id}**: {r.text}" for r in data.untraced]
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Lint findings")
    lines.append("")
    if data.lint_gaps:
        for g in data.lint_gaps:
            fix = f" (fix: {g.fix})" if g.fix else ""
            lines.append(f"- **[{g.code}]** {g.rule_id} ({g.rule_text}) -- {g.element}: "
                          f"{g.message}{fix}")  # fmt: skip
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Probe gaps")
    lines.append("")
    if data.probe_gaps:
        lines += [f"- **{g.rule_id}** ({g.rule_text}): {g.patch_summary}" for g in data.probe_gaps]
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Not checked")
    lines.append("")
    if data.unchecked_trace or data.unchecked_notes:
        lines += [f"- **{g.rule_id}** ({g.rule_text}): {g.reason}" for g in data.unchecked_trace]
        for u in data.unchecked_notes:
            concerns = f" ({', '.join(u.concerns)})" if u.concerns else ""
            lines.append(f"- {u.text}{concerns}")
    else:
        lines.append("None.")
    lines.append("")
    return lines


def _md_rule_by_rule(data: ReviewData) -> list[str]:
    lines = ["## Rule by rule", "", "| id | rule | checked by | status |", "|---|---|---|---|"]
    for row in data.rule_rows:
        text = _short(row.text).replace("|", "\\|")
        if row.status in ("checked", "probe_gap"):
            how = ", ".join(f"[{t}](#{_slug(t)})" for t in row.links) or "·"
        elif row.status == "unchecked":
            how = row.note.replace("|", "\\|") if row.note else "·"
        elif row.status == "context":
            how = "(context)"
        else:
            how = "·"
        lines.append(f"| {row.id} | {text} | {how} | {_STATUS_ICON[row.status]} |")
    lines.append("")
    return lines


def _md_access(data: ReviewData) -> list[str]:
    lines = ["## Access at a glance", ""]
    if not data.access_groups:
        return [*lines, "No scenarios to derive access from.", ""]
    for group in data.access_groups:
        heading = "/" if group.prefix == "/" else f"/{group.prefix}"
        lines.append(f"### {heading}")
        lines.append("")
        lines.append("| caller | " + " | ".join(group.columns) + " |")
        lines.append("|---" * (len(group.columns) + 1) + "|")
        for row in group.rows:
            cells = [group.cells.get((row, col), "·") for col in group.columns]
            lines.append(f"| {row} | " + " | ".join(cells) + " |")
        lines.append("")
    return lines


def _md_section6(data: ReviewData) -> list[str]:
    s = data.section6
    lines = ["## What the spec checks", ""]

    lines.append("### Invariants")
    lines.append("")
    if s.invariants:
        for target, name, sentence in s.invariants:
            lines.append(f'<a id="{_slug(target)}"></a>')
            lines.append(f"- **{name}**: {sentence}")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### State machines")
    lines.append("")
    if s.machines:
        for target, label, moves in s.machines:
            lines.append(f'<a id="{_slug(target)}"></a>')
            lines.append(f"- **{label}**: {moves}")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Background services")
    lines.append("")
    if s.services:
        for target, name, doc in s.services:
            lines.append(f'<a id="{_slug(target)}"></a>')
            lines.append(f"- **{name}**" + (f": {doc}" if doc else ""))
    else:
        lines.append("None.")
    lines.append("")

    lines.append("### Scenarios")
    lines.append("")
    if s.scenarios:
        for sv in s.scenarios:
            lines.append(f'<a id="{_slug(sv.target)}"></a>')
            lines.append(f"- **{sv.name}**")
            lines += [f"  {i}. {step}" for i, step in enumerate(sv.steps, start=1)]
    else:
        lines.append("None.")
    lines.append("")
    return lines


def _md_back_translation(data: ReviewData) -> list[str]:
    return ["## The back-translation", "", data.back_translation.rstrip(), ""]


def review_markdown(
    spec: Spec,
    trace: Trace,
    *,
    lint: list[dict] | None = None,
    probe: list[dict] | None = None,
    title: str,
) -> str:
    """The review page, as markdown: the seven sections of the module docstring, in
    order. Deterministic -- the same `spec`, `trace`, `lint` and `probe` always render
    the same text (the tuple/list order of their inputs decides the output order)."""
    data = _build(spec, trace, lint, probe, title)
    lines = [f"# {title}", ""]
    lines += _md_summary(data)
    lines += _md_decisions(data)
    lines += _md_gaps(data)
    lines += _md_rule_by_rule(data)
    lines += _md_access(data)
    lines += _md_section6(data)
    lines += _md_back_translation(data)
    return "\n".join(lines).rstrip() + "\n"


# ---- html -------------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #ffffff;
  --fg: #1a1a1a;
  --muted: #666666;
  --border: #dddddd;
  --accent: #2b6cb0;
  --chip: rgba(43, 108, 176, 0.1);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1a1a;
    --fg: #e5e5e5;
    --muted: #aaaaaa;
    --border: #444444;
    --accent: #7db6ff;
    --chip: rgba(125, 182, 255, 0.15);
  }
}
* { box-sizing: border-box; }
body {
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0 auto;
  max-width: 920px;
  padding: 1.5rem;
  line-height: 1.55;
}
h1 { font-size: 1.6rem; margin-bottom: 1rem; }
h3 { font-size: 1.05rem; margin: 1rem 0 0.4rem; }
summary { font-size: 1.15rem; font-weight: 600; cursor: pointer; padding: 0.2rem 0; }
details {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem 1rem;
  margin-bottom: 1rem;
}
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem; font-size: 0.9rem; }
th, td { border: 1px solid var(--border); padding: 0.35rem 0.55rem; text-align: left; }
th { background: var(--chip); }
code, pre {
  background: var(--chip);
  border-radius: 6px;
}
pre { padding: 0.9rem; overflow-x: auto; white-space: pre-wrap; }
.verdict { font-size: 1.05rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
ul, ol { padding-left: 1.4rem; }
.muted { color: var(--muted); }
""".strip()


def _html_summary(data: ReviewData) -> str:
    c = data.counts
    by_code = ", ".join(f"{code}: {n}" for code, n in sorted(c["lint_by_code"].items()))
    items = [
        f"Rules: {c['checked']} checked, {c['unchecked']} unchecked, {c['context']} context, "
        f"{c['untraced']} untraced, {c['probe_gap']} probe gap",
        f"Open questions: {c['open_questions']}",
        f"Lint findings: {c['lint_total']} ({by_code or 'none'})",
        f"Probe gaps: {c['probe_gaps_total']}",
    ]
    lis = "".join(f"<li>{escape(i)}</li>" for i in items)
    return (
        "<details open><summary>Summary</summary>"
        f"<ul>{lis}</ul>"
        f'<p class="verdict"><strong>Verdict:</strong> {escape(data.verdict)}</p>'
        "</details>"
    )


def _html_decisions(data: ReviewData) -> str:
    if not data.questions:
        return "<details open><summary>Decisions for you</summary><p>None.</p></details>"
    items = []
    for q in data.questions:
        answer = q.answer if q.answer is not None else f"(using default: {q.default})"
        items.append(
            "<li>"
            f"<strong>{escape(q.id)}</strong>: {escape(q.text)}"
            f'<ul><li>Default: {escape(q.default)}</li>'
            f"<li>Rules: {escape(', '.join(q.rules) if q.rules else '-')}</li>"
            f"<li>Answer: {escape(answer)}</li></ul>"
            "</li>"
        )
    return (
        "<details open><summary>Decisions for you</summary>"
        f"<ul>{''.join(items)}</ul></details>"
    )


def _html_gaps(data: ReviewData) -> str:
    parts = ["<details><summary>Gaps</summary>",
              "<p>These are the things the spec does not check.</p>"]  # fmt: skip

    parts.append("<h3>Untraced rules</h3>")
    if data.untraced:
        lis = "".join(f"<li><strong>{escape(r.id)}</strong>: {escape(r.text)}</li>"
                       for r in data.untraced)  # fmt: skip
        parts.append(f"<ul>{lis}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("<h3>Lint findings</h3>")
    if data.lint_gaps:
        lis = "".join(
            f"<li><strong>[{escape(g.code)}]</strong> {escape(g.rule_id)} "
            f"({escape(g.rule_text)}) -- {escape(g.element)}: {escape(g.message)}"
            + (f" (fix: {escape(g.fix)})" if g.fix else "")
            + "</li>"
            for g in data.lint_gaps
        )
        parts.append(f"<ul>{lis}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("<h3>Probe gaps</h3>")
    if data.probe_gaps:
        lis = "".join(
            f"<li><strong>{escape(g.rule_id)}</strong> ({escape(g.rule_text)}): "
            f"{escape(g.patch_summary)}</li>"
            for g in data.probe_gaps
        )
        parts.append(f"<ul>{lis}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("<h3>Not checked</h3>")
    if data.unchecked_trace or data.unchecked_notes:
        lis = "".join(
            f"<li><strong>{escape(g.rule_id)}</strong> ({escape(g.rule_text)}): "
            f"{escape(g.reason)}</li>"
            for g in data.unchecked_trace
        )
        for u in data.unchecked_notes:
            concerns = f" ({escape(', '.join(u.concerns))})" if u.concerns else ""
            lis += f"<li>{escape(u.text)}{concerns}</li>"
        parts.append(f"<ul>{lis}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("</details>")
    return "".join(parts)


def _html_rule_by_rule(data: ReviewData) -> str:
    rows = []
    for row in data.rule_rows:
        if row.status in ("checked", "probe_gap"):
            how = ", ".join(
                f'<a href="#{_slug(t)}">{escape(t)}</a>' for t in row.links
            ) or "·"
        elif row.status == "unchecked":
            how = escape(row.note) if row.note else "·"
        elif row.status == "context":
            how = "(context)"
        else:
            how = "·"
        rows.append(
            f"<tr><td>{escape(row.id)}</td><td>{escape(_short(row.text))}</td>"
            f"<td>{how}</td><td>{_STATUS_ICON[row.status]} {_STATUS_LABEL[row.status]}</td></tr>"
        )
    return (
        "<details><summary>Rule by rule</summary>"
        "<table><thead><tr><th>id</th><th>rule</th><th>checked by</th><th>status</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></details>"
    )


def _html_access(data: ReviewData) -> str:
    if not data.access_groups:
        return (
            "<details><summary>Access at a glance</summary>"
            "<p>No scenarios to derive access from.</p></details>"
        )
    parts = ["<details><summary>Access at a glance</summary>"]
    for group in data.access_groups:
        heading = "/" if group.prefix == "/" else f"/{group.prefix}"
        parts.append(f"<h3>{escape(heading)}</h3>")
        head = "<th>caller</th>" + "".join(f"<th>{escape(c)}</th>" for c in group.columns)
        body_rows = []
        for row in group.rows:
            cells = "".join(
                f"<td>{escape(group.cells.get((row, col), chr(0xB7)))}</td>"
                for col in group.columns
            )
            body_rows.append(f"<tr><td>{escape(row)}</td>{cells}</tr>")
        parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>"
                      f"{''.join(body_rows)}</tbody></table>")  # fmt: skip
    parts.append("</details>")
    return "".join(parts)


def _html_section6(data: ReviewData) -> str:
    s = data.section6
    parts = ["<details><summary>What the spec checks</summary>"]

    parts.append("<h3>Invariants</h3>")
    if s.invariants:
        lis = "".join(
            f'<li id="{_slug(t)}"><strong>{escape(n)}</strong>: {escape(sent)}</li>'
            for t, n, sent in s.invariants
        )
        parts.append(f"<ul>{lis}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("<h3>State machines</h3>")
    if s.machines:
        lis = "".join(
            f'<li id="{_slug(t)}"><strong>{escape(label)}</strong>: {escape(moves)}</li>'
            for t, label, moves in s.machines
        )
        parts.append(f"<ul>{lis}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("<h3>Background services</h3>")
    if s.services:
        lis = "".join(
            f'<li id="{_slug(t)}"><strong>{escape(n)}</strong>'
            + (f": {escape(doc)}" if doc else "") + "</li>"
            for t, n, doc in s.services
        )
        parts.append(f"<ul>{lis}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("<h3>Scenarios</h3>")
    if s.scenarios:
        items = []
        for sv in s.scenarios:
            steps = "".join(f"<li>{escape(step)}</li>" for step in sv.steps)
            items.append(
                f'<li id="{_slug(sv.target)}"><strong>{escape(sv.name)}</strong>'
                f"<ol>{steps}</ol></li>"
            )
        parts.append(f"<ul>{''.join(items)}</ul>")
    else:
        parts.append("<p>None.</p>")

    parts.append("</details>")
    return "".join(parts)


def _html_back_translation(data: ReviewData) -> str:
    return (
        "<details><summary>The back-translation</summary>"
        f"<pre>{escape(data.back_translation)}</pre></details>"
    )


def review_html(
    spec: Spec,
    trace: Trace,
    *,
    lint: list[dict] | None = None,
    probe: list[dict] | None = None,
    title: str,
) -> str:
    """The review page as a single self-contained HTML page: inline CSS, collapsible
    (``<details>``) sections, light/dark via ``prefers-color-scheme``, no external
    assets. Same content and order as `review_markdown`; deterministic the same way."""
    data = _build(spec, trace, lint, probe, title)
    body = "\n".join(
        [
            _html_summary(data),
            _html_decisions(data),
            _html_gaps(data),
            _html_rule_by_rule(data),
            _html_access(data),
            _html_section6(data),
            _html_back_translation(data),
        ]
    )
    title_html = escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_html}</title>
<style>
{_CSS}
</style>
</head>
<body>
<h1>{title_html}</h1>
{body}
</body>
</html>
"""


# ---- CLI --------------------------------------------------------------------------------


def _load(ref: str) -> Any:
    """The object ``module:attr`` (``attr`` may be dotted)."""
    module, sep, attr = ref.partition(":")
    if not sep or not module or not attr:
        raise ValueError(f"{ref!r}: expected module:attribute")
    obj: Any = importlib.import_module(module)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _load_spec(ref: str) -> Spec:
    obj = _load(ref)
    if not isinstance(obj, Spec) and callable(obj):
        obj = obj()
    if not isinstance(obj, Spec):
        raise ValueError(f"{ref} is not a Spec")
    return obj


def _load_json_list(path: str | None) -> list[dict] | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text())


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="review")
    p.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")
    p.add_argument("--trace", required=True, help="the spec's trace (lattice.spec.rules.Trace)")
    p.add_argument("--lint", help="a lint findings JSON file (a list of items)")
    p.add_argument("--probe", help="a probe results JSON file (a list of items)")
    p.add_argument("--out", required=True, help="where to write the markdown review")
    p.add_argument("--html", help="also write a self-contained HTML review here")
    p.add_argument("--title", help="the page title (default: the trace's source document)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        spec = _load_spec(args.spec)
        trace = Trace.load(args.trace)
        lint = _load_json_list(args.lint)
        probe = _load_json_list(args.probe)
        title = args.title or f"Spec review: {trace.source}"
        md = review_markdown(spec, trace, lint=lint, probe=probe, title=title)
    except Exception as err:  # noqa: BLE001 -- any failure to run the CLI is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    Path(args.out).write_text(md)
    if args.html:
        html_text = review_html(spec, trace, lint=lint, probe=probe, title=title)
        Path(args.html).write_text(html_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
