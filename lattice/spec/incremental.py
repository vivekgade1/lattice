"""Incremental spec drafting: the owner edits their requirements, Lattice drafts only the
change (``lattice.spec.incremental``).

A real project grows ticket by ticket. :func:`diff_rules` aligns the rules of the old and
the new requirements (:func:`lattice.spec.rules.extract_rules`); :func:`draft_since`
prompts the model with that diff, the base spec module and its trace, and asks for the
updated module (the rest kept as it is) plus the trace of the new rule ids, recording
which old rules the change replaces (``Trace.replaces``) or retires (``Trace.retired``)
and which base elements it deleted (``Trace.retired_targets``). The answer goes through
:func:`lattice.spec.draft`'s validate / trace-check / repair loop, plus the change's own
checks (:func:`change_problems`): every link of an unchanged rule survives. The result
carries the diff and :func:`changed_targets` -- the elements added, removed and modified,
from comparing the base and the new ``SPEC``, never from the model's claims.

**The rule diff.** Rules whose whitespace-normalised text is identical are unchanged
(paired in document order, the same section first). The rest are paired greedily by a
word-level ``difflib`` ratio (lower-cased word and punctuation tokens), best pair first,
with :data:`SECTION_BONUS` added when both rules are under the same heading; a pair
needs a raw ratio of at least :data:`SIMILARITY` (0.5) and becomes ``changed``. Two
rules that both name endpoints (``METHOD /path``), none in common, are never paired:
they are about different endpoints, whatever their shared wording. What is
left is ``added`` (new rules) or ``removed`` (old rules). A rule split in two pairs with
the closer half (changed) and the other half is added. Ties break on document order, so
the diff is deterministic.

**Impact** (:func:`impact`, :func:`label_impact`): the combined spec runs against the
current app (layers D and C, X optional), and every failing target (``kind:name``) is
labelled from the rules that link it:

* ``contradiction`` -- the target is in an L011 conflict pair, or it is linked both to
  added or changed rules and to unchanged ones;
* ``not_built_yet`` -- it is linked only to added or changed rules (an added rule the
  trace says ``replaces`` an old one counts as changed);
* ``regression`` -- it is linked only to unchanged rules;
* ``unlinked`` -- no rule links it.

CLI (through ``lattice.spec.draft``)::

    draft NEW.md --since OLD.md --base BASE.py [--context CTX.md] --out NEW.py
        [--impact --app mod:factory --db mod:build_db [--login mod:fn] [--runtime ...]
         [--services mod:SERVICES] [--database ...] [--database-url URL] [--race]]

writes the new module, its trace, ``<out>.draft.json`` (usage, rounds, problems, the
diff, ``changed_targets`` and the L011 findings) and, with ``--impact``,
``<out>.impact.json``.
"""

from __future__ import annotations

import dataclasses
import difflib
import enum
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lattice.llm.client import Generator
from lattice.spec.draft import (
    DraftResult,
    _loop,
    _rules_block,
    _trace_json,
    load_spec,
)
from lattice.spec.model import Spec, resolve_table, tables_of
from lattice.spec.rules import Question, Rule, Trace, extract_rules

__all__ = [
    "IMPACT_LABELS",
    "INCREMENTAL_RULES",
    "SECTION_BONUS",
    "SIMILARITY",
    "ImpactItem",
    "RuleDiff",
    "carry_trace",
    "change_problems",
    "changed_targets",
    "diff_rules",
    "draft_since",
    "impact",
    "label_impact",
    "spec_elements",
]

SIMILARITY = 0.5
SECTION_BONUS = 0.15
IMPACT_LABELS = ("contradiction", "regression", "not_built_yet", "unlinked")

# ---- the rule diff -------------------------------------------------------------------------

_TOKEN = re.compile(r"\w+|[^\w\s]")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


_ENDPOINT = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s;,)]*)")


def _endpoints(text: str) -> set[tuple[str, str]]:
    """The ``METHOD /path`` pairs a rule names (path parameters as ``{}``)."""
    return {(m, re.sub(r"\{[^}/]*\}|:\w+", "{}", path).rstrip("/.") or "/")
            for m, path in _ENDPOINT.findall(text)}  # fmt: skip


def similarity(a: str, b: str) -> float:
    """The word-level difflib ratio of two rule texts."""
    return difflib.SequenceMatcher(None, _tokens(a), _tokens(b), autojunk=False).ratio()


@dataclass
class RuleDiff:
    """How the new requirements' rules relate to the old ones. Pairs are (old id, new
    id); ids are each document's own :func:`extract_rules` ids."""

    old_rules: list[Rule]
    new_rules: list[Rule]
    unchanged: list[tuple[str, str]] = field(default_factory=list)
    changed: list[tuple[str, str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def old_to_new(self) -> dict[str, str]:
        """Old id -> new id of every unchanged or changed rule."""
        return {o: n for o, n in [*self.unchanged, *self.changed]}

    @property
    def new_to_old(self) -> dict[str, str]:
        return {n: o for o, n in [*self.unchanged, *self.changed]}

    def old(self, rule_id: str) -> Rule:
        return next(r for r in self.old_rules if r.id == rule_id)

    def new(self, rule_id: str) -> Rule:
        return next(r for r in self.new_rules if r.id == rule_id)

    @property
    def empty(self) -> bool:
        """Whether nothing changed (every rule unchanged)."""
        return not (self.changed or self.added or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unchanged": [list(p) for p in self.unchanged],
            "changed": [
                {"old": o, "new": n, "old_text": self.old(o).text, "new_text": self.new(n).text}
                for o, n in self.changed
            ],
            "added": [{"id": n, "text": self.new(n).text} for n in self.added],
            "removed": [{"id": o, "text": self.old(o).text} for o in self.removed],
        }


def _order(rule_id: str) -> int:
    return int(rule_id[1:]) if rule_id[1:].isdigit() else 0


def diff_rules(old_md: str, new_md: str) -> RuleDiff:
    """Align the rules of `old_md` and `new_md` (see the module docstring)."""
    old = extract_rules(old_md)
    new = extract_rules(new_md)
    diff = RuleDiff(old, new)
    free_old = list(range(len(old)))
    free_new = list(range(len(new)))
    pairs: list[tuple[int, int, bool]] = []  # (old index, new index, identical)
    # identical text: the same section first, then any
    for same_section in (True, False):
        for j in list(free_new):
            for i in free_old:
                if _norm(old[i].text) == _norm(new[j].text) and (
                    not same_section or old[i].section == new[j].section
                ):
                    pairs.append((i, j, True))
                    free_old.remove(i)
                    free_new.remove(j)
                    break
    # similar text: best scored pair first
    scored = []
    for i in free_old:
        for j in free_new:
            ratio = similarity(old[i].text, new[j].text)
            ends_old, ends_new = _endpoints(old[i].text), _endpoints(new[j].text)
            if ends_old and ends_new and not ends_old & ends_new:
                continue  # rules about different endpoints are different rules
            if ratio >= SIMILARITY:
                bonus = SECTION_BONUS if old[i].section == new[j].section else 0.0
                scored.append((-(ratio + bonus), i, j))
    scored.sort()
    for _, i, j in scored:
        if i in free_old and j in free_new:
            pairs.append((i, j, False))
            free_old.remove(i)
            free_new.remove(j)
    pairs.sort(key=lambda p: p[1])
    for i, j, identical in pairs:
        (diff.unchanged if identical else diff.changed).append((old[i].id, new[j].id))
    diff.added = [new[j].id for j in sorted(free_new)]
    diff.removed = [old[i].id for i in sorted(free_old)]
    return diff


# ---- the spec's elements, compared ---------------------------------------------------------


def _canon(value: Any) -> Any:
    """A JSON-able, order-stable form of a spec element (dataclasses, enums, sets,
    mappings, tuples), for structural comparison."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": type(value).__name__,
            **{f.name: _canon(getattr(value, f.name)) for f in dataclasses.fields(value)},
        }
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, Mapping):
        return {str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, frozenset | set):
        return sorted((_canon(v) for v in value), key=lambda v: json.dumps(v, default=str))
    if isinstance(value, list | tuple):
        return [_canon(v) for v in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return repr(value)


def spec_elements(spec: Spec) -> dict[str, Any]:
    """Every element of `spec` by its target name (``kind:name``) -> its canonical form:
    scenarios, invariants, machines (``machine:<Table>.<column>``, the schema name),
    services, the profile's tables (``profile:<Table>``) and contracts (``contract:METHOD
    /path``)."""
    out: dict[str, Any] = {}
    for sc in spec.scenarios:
        out[f"scenario:{sc.name}"] = _canon(sc)
    for inv in spec.invariants:
        out[f"invariant:{inv.name}"] = _canon(inv)
    for m in spec.machines:
        found = resolve_table(spec.profile, m.table)
        table = found[0] if found else m.table
        out[f"machine:{table}.{m.column}"] = _canon(dataclasses.replace(m, table=table))
    for s in spec.services:
        out[f"service:{s.name}"] = _canon(s)
    tables, sql_names = tables_of(spec.profile)
    for name, schema in tables.items():
        out[f"profile:{name}"] = {"sql": sql_names.get(name, name), "schema": _canon(schema)}
    for c in spec.contracts:
        out[f"contract:{c.endpoint}"] = _canon(c)
    return out


def changed_targets(base: Spec, new: Spec) -> dict[str, list[str]]:
    """The elements the new spec added, removed and modified against the base one,
    compared structurally (:func:`spec_elements`)."""
    a, b = spec_elements(base), spec_elements(new)
    return {
        "added": sorted(t for t in b if t not in a),
        "removed": sorted(t for t in a if t not in b),
        "modified": sorted(t for t in a if t in b and a[t] != b[t]),
    }


def _resolve_target(spec: Spec | None, target: str) -> str:
    """`target` with a machine's / profile's table as its schema name, when it resolves."""
    kind, _, name = target.partition(":")
    if spec is None or kind not in ("machine", "profile"):
        return target
    table, dot, column = name.partition(".")
    found = resolve_table(spec.profile, table)
    return f"{kind}:{found[0]}{dot}{column}" if found else target


# ---- carrying the base trace over ----------------------------------------------------------


def carry_trace(diff: RuleDiff, base: Trace, source: str) -> Trace:
    """The base trace on the new rule ids: the links, unchecked reasons and context of
    the unchanged rules, and the questions whose rules survive (their rule ids mapped;
    removed rules dropped)."""
    keep = dict(diff.unchanged)
    out = Trace(source=source, rules=list(diff.new_rules))
    for old_id, new_id in keep.items():
        if base.links.get(old_id):
            out.links[new_id] = list(base.links[old_id])
        if old_id in base.unchecked:
            out.unchecked[new_id] = base.unchecked[old_id]
        if old_id in base.context:
            out.context.append(new_id)
    mapping = diff.old_to_new
    for q in base.questions:
        rules = [mapping[r] for r in q.rules if r in mapping]
        if rules:
            out.questions.append(Question(q.id, q.text, q.default, rules, q.answer))
    return out


# ---- the prompt ----------------------------------------------------------------------------

INCREMENTAL_RULES = """\
This is an **incremental** draft: the requirements were edited since the base spec was
drafted. Draft only the change:

A. **Keep the rest of the module unchanged** -- every element not touched by a changed,
   added or removed rule keeps its name and its content, byte for byte where you can.
B. **Unchanged rules keep their links.** The base trace below is already carried over to
   the new rule ids; keep every one of those links (the elements must stay in SPEC).
C. **A changed rule's checks are rewritten or replaced, not kept alongside.** Edit the
   elements its old links name so they state the new text (or replace them with new
   ones and delete the old ones), and link the new rule to them. The tool records
   `replaces` for every changed rule itself.
D. **A removed rule's checks are deleted** -- unless an unchanged or new rule still links
   them -- and the removed rule goes in the trace's `retired` (old rule id -> reason).
E. If an added rule in fact rewrites a removed one, say so in `replaces` (new rule id ->
   old rule id) and treat it as C.
F. If the new text contradicts what an unchanged rule says, do not keep two scenarios
   expecting different answers for the same request: follow the newer rule, and ask a
   `Question` naming both rules.
"""


def _links_text(trace: Trace, rule_id: str) -> str:
    if trace.links.get(rule_id):
        return ", ".join(trace.links[rule_id])
    if rule_id in trace.unchecked:
        return f"(unchecked: {trace.unchecked[rule_id]})"
    if rule_id in trace.context:
        return "(context)"
    return "(not traced)"


def since_message(
    diff: RuleDiff, base: Trace, carried: Trace, context_md: str, base_module: str
) -> str:
    """The first user message of an incremental draft."""
    lines = []
    if diff.changed:
        lines.append("## Changed rules (old text -> new text; the old rule's links)\n")
        for o, n in diff.changed:
            lines.append(f"- {n} (was {o})\n  - old: {diff.old(o).text}\n"
                         f"  - new: {diff.new(n).text}\n"
                         f"  - old links: {_links_text(base, o)}")  # fmt: skip
        lines.append("")
    if diff.added:
        lines.append("## Added rules\n")
        lines += [f"- {n}: {diff.new(n).text}" for n in diff.added]
        lines.append("")
    if diff.removed:
        lines.append("## Removed rules (old id, old text, the old rule's links)\n")
        lines += [f"- {o}: {diff.old(o).text}\n  - old links: {_links_text(base, o)}"
                  for o in diff.removed]  # fmt: skip
        lines.append("")
    if diff.unchanged:
        moved = [f"{o}->{n}" for o, n in diff.unchanged if o != n]
        lines.append(f"## Unchanged rules\n\n{len(diff.unchanged)} rule(s), carried over"
                     + (f" (renumbered: {', '.join(moved)})" if moved else "")
                     + "; their links are in the carried-over trace below.\n")  # fmt: skip
    if diff.empty:
        lines.append("Nothing changed: answer the base module and the carried-over trace.\n")
    return (
        "# Requirements, as numbered rules (the new version)\n\n"
        f"{_rules_block(diff.new_rules)}\n\n"
        "# What changed since the base spec\n\n" + "\n".join(lines).rstrip() + "\n\n# Context\n\n"
        f"{context_md.strip() or '(none given)'}\n\n"
        "# The base spec module\n\n"
        f"```python\n{base_module.rstrip()}\n```\n\n"
        "# The base trace, carried over to the new rule ids (unchanged rules only)\n\n"
        f"```json\n{_trace_json(carried)}\n```\n\n"
        "# How to draft the change\n\n"
        f"{INCREMENTAL_RULES}\n"
        "# Task\n\n"
        "Answer with the complete updated module and the complete trace for the new rule "
        "ids (every new rule id in exactly one of links, unchecked or context), plus "
        '`"retired"` and, when E applies, `"replaces"`. Keep every question id of the '
        "carried-over trace.\n"
    )


# ---- the change's own checks ---------------------------------------------------------------


def change_problems(
    diff: RuleDiff,
    base: Trace,
    base_spec: Spec,
    spec: Spec | None,
    trace: Trace,
    raw: Mapping[str, Any] | None,
) -> list[str]:
    """Complete `trace`'s record of the change in place (``replaces``, ``retired``,
    ``retired_targets``) and answer the change's problems: an unchanged rule whose link
    did not survive, a removed rule's element left in SPEC that no rule links, a
    ``replaces`` claim that is not an added rule over a removed one."""
    problems: list[str] = []
    raw = raw or {}
    claimed = raw.get("replaces") if isinstance(raw.get("replaces"), Mapping) else {}
    reasons = raw.get("retired") if isinstance(raw.get("retired"), Mapping) else {}
    replaces = {n: o for o, n in diff.changed}
    added, removed = set(diff.added), set(diff.removed)
    for n, o in (claimed or {}).items():
        n, o = str(n), str(o)
        if replaces.get(n) == o:
            continue
        if n in added and o in removed:
            replaces[n] = o
        else:
            problems.append(
                f"trace: replaces {n!r} -> {o!r}: only an added rule "
                f"({', '.join(diff.added) or 'none'}) may replace a removed one "
                f"({', '.join(diff.removed) or 'none'}); changed rules are recorded by the tool"
            )
    trace.replaces = dict(sorted(replaces.items(), key=lambda kv: _order(kv[0])))
    replaced = set(replaces.values())
    trace.retired = {
        o: str((reasons or {}).get(o) or "removed from the requirements")
        for o in diff.removed
        if o not in replaced
    }
    if spec is None:
        return problems
    elements = {_resolve_target(spec, t) for t in spec_elements(spec)}
    base_elements = {_resolve_target(base_spec, t) for t in spec_elements(base_spec)}
    linked_now = {_resolve_target(spec, t) for ts in trace.links.values() for t in ts}
    for o, n in diff.unchanged:
        now = {_resolve_target(spec, t) for t in trace.links.get(n, [])}
        for t in base.links.get(o, []):
            if _resolve_target(base_spec, t) not in now:
                problems.append(
                    f"trace: {n} is unchanged (was {o}) but lost its link {t!r}; keep every "
                    "link of an unchanged rule, and the element it names"
                )
    for o in diff.removed:
        for t in base.links.get(o, []):
            r = _resolve_target(base_spec, t)
            if r in elements and r not in linked_now:
                problems.append(
                    f"spec: {t} checked only the removed rule {o} ({diff.old(o).text!r}); "
                    "delete it from SPEC (or link it to the rule that still needs it)"
                )
    old_targets = {_resolve_target(base_spec, t) for ts in base.links.values() for t in ts}
    trace.retired_targets = sorted(
        t for t in old_targets if t in base_elements and t not in elements
    )
    return problems


# ---- draft_since ---------------------------------------------------------------------------


def draft_since(
    old_md: str,
    new_md: str,
    *,
    base_module: str,
    base_trace: Trace,
    context_md: str = "",
    generator: Generator,
    model: str,
    max_repairs: int = 3,
    source: str | None = None,
) -> DraftResult:
    """Draft only the change from `old_md` to `new_md` on top of the base spec
    (`base_module`, `base_trace`; see the module docstring). The result is a
    :class:`lattice.spec.draft.DraftResult` with ``diff`` and ``changed_targets`` set;
    ``notes`` name changed rules none of whose checks changed. Raises ValueError when the
    base module does not import."""
    base_spec, errors = load_spec(base_module)
    if base_spec is None:
        raise ValueError("the base module does not import: " + "; ".join(errors))
    diff = diff_rules(old_md, new_md)
    src = source or base_trace.source
    carried = carry_trace(diff, base_trace, src)

    def post(spec: Spec | None, trace: Trace, raw: Mapping[str, Any] | None) -> list[str]:
        return change_problems(diff, base_trace, base_spec, spec, trace, raw)

    result = _loop(
        since_message(diff, base_trace, carried, context_md, base_module),
        rules=diff.new_rules,
        source=src,
        generator=generator,
        model=model,
        max_repairs=max_repairs,
        module_source=base_module,
        trace=carried,
        keep_questions=[q.id for q in carried.questions],
        post=post,
    )
    result.diff = diff
    if result.spec is not None:
        result.changed_targets = changed_targets(base_spec, result.spec)
        touched = {t for ts in result.changed_targets.values() for t in ts}
        for o, n in diff.changed:
            olds = {_resolve_target(base_spec, t) for t in base_trace.links.get(o, [])}
            if olds and not olds & touched:
                result.notes.append(
                    f"{n} changed (was {o}) but none of its old checks changed: "
                    f"{', '.join(sorted(olds))}"
                )
    return result


# ---- impact --------------------------------------------------------------------------------


@dataclass
class ImpactItem:
    """A failing target of the combined spec on the current app, labelled
    (IMPACT_LABELS), with the rules linking it (new ids) and its findings' summaries."""

    target: str
    label: str
    rules: list[str]
    findings: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _finding_target(spec: Spec, finding: Any) -> str | None:
    details = getattr(finding, "details", None) or {}
    if finding.layer == "D":
        return "scenario:" + str(finding.rule).removeprefix("scenario: ")
    if finding.layer == "C" and details.get("invariant"):
        return f"invariant:{details['invariant']}"
    if finding.layer == "X":
        rule = str(details.get("rule_id") or "")
        if rule in {i.name for i in spec.invariants}:
            return f"invariant:{rule}"
        if "." in rule:
            return f"machine:{rule}"
        if details.get("scenario"):
            return f"scenario:{details['scenario']}"
    return None


def label_impact(
    report: Any, spec: Spec, diff: RuleDiff, trace: Trace, *, conflict_pairs: Iterable[Any] = ()
) -> list[ImpactItem]:
    """Label every failing target of `report` (see the module docstring). Findings whose
    target cannot be named (a layer A or B finding) are left out. `conflict_pairs`:
    :class:`lattice.spec.lint.Conflict`s (default: those of `spec`)."""
    from lattice.spec.lint import conflicts

    pairs = list(conflict_pairs) or conflicts(spec)
    in_conflict = {t for c in pairs for t in c.targets}
    fresh = set(diff.added) | {n for _, n in diff.changed} | set(trace.replaces)
    unchanged = {n for _, n in diff.unchanged}
    by_target: dict[str, list[str]] = {}
    for rid, targets in trace.links.items():
        for t in targets:
            by_target.setdefault(_resolve_target(spec, t), []).append(rid)
    failing: dict[str, list[dict[str, Any]]] = {}
    for f in report.findings:
        target = _finding_target(spec, f)
        if target is not None:
            failing.setdefault(_resolve_target(spec, target), []).append(
                {"layer": f.layer, "case": f.case, "summary": f.summary(), "rule": f.rule}
            )
    out = []
    for target, found in failing.items():
        rules = sorted(set(by_target.get(target, [])), key=_order)
        has_fresh = any(r in fresh for r in rules)
        has_old = any(r in unchanged for r in rules)
        if target in in_conflict or (has_fresh and has_old):
            label = "contradiction"
        elif has_fresh:
            label = "not_built_yet"
        elif has_old:
            label = "regression"
        else:
            label = "unlinked"
        out.append(ImpactItem(target, label, rules, found))
    out.sort(key=lambda i: (IMPACT_LABELS.index(i.label), i.target))
    return out


def impact(
    spec: Spec,
    diff: RuleDiff,
    trace: Trace,
    *,
    app_factory: Callable[..., Any],
    db_factory: Callable[..., Any],
    race: bool = False,
    check: Callable[..., Any] | None = None,
    **check_kwargs: Any,
) -> list[ImpactItem]:
    """Run the combined `spec` against the current app (layers D and C; X too with
    `race`) and label each failing target (:func:`label_impact`). `check_kwargs` go to
    :func:`lattice.spec.check.check` (``login``, ``runtime``, ``services``,
    ``database``, ``database_url``, ...); `check` replaces it (tests)."""
    if check is None:
        from lattice.spec.check import check as run_check
    else:
        run_check = check
    layers = "CDX" if race else "CD"
    report = run_check(spec, app_factory, db_factory, layers=layers, **check_kwargs)
    return label_impact(report, spec, diff, trace)


# ---- the CLI part (called from lattice.spec.draft.main) ------------------------------------


def run_cli(args: Any, generator: Generator) -> int:
    """``draft NEW.md --since OLD.md --base BASE.py ...`` (see the module docstring)."""
    import sys
    from pathlib import Path

    from lattice.spec.draft import draft_json_path
    from lattice.spec.lint import _l011
    from lattice.spec.rules import trace_path

    try:
        new_md = args.requirements.read_text()
        old_md = args.since.read_text()
        base_module = args.base.read_text()
        base_trace = Trace.load(trace_path(args.base))
        context = args.context.read_text() if args.context else ""
    except OSError as exc:
        print(f"draft: {exc}", file=sys.stderr)
        return 2
    try:
        result = draft_since(
            old_md,
            new_md,
            base_module=base_module,
            base_trace=base_trace,
            context_md=context,
            generator=generator,
            model=args.model,
            max_repairs=args.max_repairs,
            source=str(args.requirements),
        )
    except ValueError as exc:
        print(f"draft: {exc}", file=sys.stderr)
        return 2
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.module_source.rstrip() + "\n")
    result.trace.save(trace_path(out))
    l011 = [f.to_dict() for f in _l011(result.spec, result.trace)] if result.spec else []
    record = {
        "mode": "since",
        "model": args.model,
        "since": str(args.since),
        "base": str(args.base),
        "valid": result.valid,
        "rounds": result.rounds,
        "problems": result.problems,
        "notes": result.notes,
        "usage": result.usage.as_dict(),
        "rules": len(result.trace.rules),
        "diff": result.diff.to_dict(),
        "replaces": result.trace.replaces,
        "retired": result.trace.retired,
        "retired_targets": result.trace.retired_targets,
        "changed_targets": result.changed_targets,
        "conflicts": l011,
        "questions": [dataclasses.asdict(q) for q in result.trace.questions],
        "transcript": result.transcript,
    }
    draft_json_path(out).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    d = result.diff
    state = "valid" if result.valid else f"{len(result.problems)} problem(s)"
    cost = result.usage.cost_usd
    print(
        f"draft: {out} -- {state}, {result.rounds} round(s); rules {len(d.unchanged)} "
        f"unchanged, {len(d.changed)} changed, {len(d.added)} added, {len(d.removed)} "
        f"removed; {len(l011)} conflict(s); {result.usage.total_input_tokens} in / "
        f"{result.usage.output_tokens} out tokens" + (f", ${cost:.4f}" if cost is not None else "")
    )
    for p in result.problems:
        print(f"  {p}")
    code = 0 if result.valid else 1
    if args.impact:
        if result.spec is None or not result.valid:
            print("draft: --impact skipped: the draft is not valid", file=sys.stderr)
            return 1
        if not args.app or not args.db:
            print("draft: --impact needs --app and --db", file=sys.stderr)
            return 2
        try:
            from lattice.spec.__main__ import _runtime, _services, load

            items = impact(
                result.spec,
                d,
                result.trace,
                app_factory=load(args.app),
                db_factory=load(args.db),
                race=args.race,
                login=load(args.login) if args.login else None,
                runtime=_runtime(args.runtime),
                services=_services(args.services),
                database=args.database,
                database_url=args.database_url,
            )
        except Exception as err:  # noqa: BLE001 -- any failure to run the check
            print(f"draft: --impact could not run: {type(err).__name__}: {err}", file=sys.stderr)
            return 2
        impact_path = out.with_name(out.stem + ".impact.json")
        impact_path.write_text(
            json.dumps({"items": [i.to_dict() for i in items]}, indent=2, default=str) + "\n"
        )
        for label in IMPACT_LABELS:
            n = sum(i.label == label for i in items)
            if n:
                print(f"  impact: {n} {label}")
    return code
