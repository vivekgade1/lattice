"""Phase 1's spec arms (docs/superpowers/specs/2026-10-04-phase1-loop-experiment.md):
the spec step of arm S, the simulated approval, and the spec check of arms S and S*.

* :func:`draft_prompt` -- (system, user) asking the model for the ticket's requirement as
  a ``SpecDelta`` module: a compact reference for writing one (scenarios, steps,
  ``expect_db``, the invariant forms, machines, unchecked requirements, the seed ids) and
  the base spec's relevant source (the profile, the state machine and invariants, a few
  existing scenarios). Never an oracle case, a reference, a trap or an owner's delta.
* :func:`parse_delta` -- the answer's one python block run in a restricted namespace
  (the :data:`PREAMBLE` names and a few builtins; no import, no dunder or private
  attribute) to a ``SpecDelta``, validated with ``apply(SPEC, delta)``; any problem is a
  :class:`DraftError` whose ``feedback`` is the repair message.
* :func:`draft_spec` -- the spec step: the draft prompt, then up to
  :data:`MAX_SPEC_REPAIRS` validation repairs.
* Arm S+ (plan 2026-10-05-pivot-spec-authoring.md, item C): ``draft_prompt(...,
  checklist=True)`` adds the generic edge-case checklist
  (:mod:`bench.edit_slice.spec_checklist`) to the draft's system prompt, and
  :func:`review_spec` is one self-review call after the first valid draft (the draft,
  the ticket and the checklist; parsed and validated like a draft, not repaired; an
  invalid revision keeps the first draft).
* :func:`approve` -- the simulated approval: each drafted scenario and invariant runs on
  the ticket's reference (layers C/D, plus B when there are invariants, so they are
  checked after the program cases' requests as the candidate's check will); a rule the
  reference breaks is rejected. Programs and machines are approved as a whole when they
  add no finding on the reference. Unchecked entries are kept and not counted.
* :func:`owner_delta` -- arm S*'s delta: the owner's blind delta (``BLIND_DELTAS`` for
  t31-t44, the pivot's ``BLIND_PIVOT_DELTAS`` for t45-t56).
* :func:`code_prompt` -- arm Q's code prompt plus the approved spec (:data:`SPEC_SECTION`).
* :func:`check_candidate` / :func:`spec_feedback` -- the check round of S, S+ and S*:
  layers A-D against ``apply(SPEC, delta)`` with the app's baseline (plus the ticket's
  own approvals, :func:`_owner_approved`), rendered rule first.
* :func:`coverage` -- offline: how many of the ticket's traps (Phase 0's or the
  pivot's) a delta catches.

``python -m bench.edit_slice.sdraft --estimate`` prints the prompt sizes per ticket (no
model call).
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any

from bench.edit_slice import prompts
from bench.edit_slice.tickets import ticket
from bench.specs import multivendor
from bench.specs.multivendor import SPEC, line, order_read, product_read
from bench.specs.multivendor_tickets import BLIND_DELTAS, DELTAS, SpecDelta, apply
from lattice.llm.client import Generation, GenerationRefused, Generator
from lattice.spec import Invariant, Machine, Report, Scenario, Step, Unchecked, validate
from lattice.spec.report import LAYER_NAMES, CheckFinding

__all__ = [
    "MAX_SPEC_REPAIRS",
    "PREAMBLE",
    "SPEC_SECTION",
    "Approval",
    "DraftError",
    "DraftResult",
    "ReviewResult",
    "approve",
    "check_candidate",
    "code_prompt",
    "coverage",
    "draft_prompt",
    "draft_spec",
    "drafted_rules",
    "owner_delta",
    "parse_delta",
    "render_delta",
    "review_messages",
    "review_spec",
    "spec_feedback",
]

MAX_SPEC_REPAIRS = 2  # validation repairs of the draft (not counted as spec errors)
MAX_FINDINGS = 8  # findings shown per repair message
CHARS_PER_TOKEN = 3.5  # as bench.edit_slice.run
SPEC_SECTION = (
    "The requirement as approved spec (scenarios and invariants your change must satisfy):"
)

# ---- the names a draft may use ----------------------------------------------------------------

PREAMBLE: dict[str, Any] = {
    "SpecDelta": SpecDelta,
    "Scenario": Scenario,
    "Step": Step,
    "Invariant": Invariant,
    "Machine": Machine,
    "Unchecked": Unchecked,
    "product_read": product_read,
    "order_read": order_read,
    "line": line,
}
SAFE_BUILTINS = (
    "abs", "bool", "dict", "enumerate", "float", "int", "len", "list", "max", "min",
    "range", "round", "sorted", "str", "sum", "tuple", "zip",
)  # fmt: skip
PREAMBLE_SOURCE = """\
from bench.specs.multivendor_tickets import SpecDelta
from lattice.spec import Invariant, Machine, Scenario, Step, Unchecked
from bench.specs.multivendor import line, order_read, product_read"""


class DraftError(ValueError):
    """A draft that cannot be used: ``feedback`` is the repair message for the model."""

    def __init__(self, feedback: str) -> None:
        super().__init__(feedback)
        self.feedback = feedback


# ---- the draft prompt -------------------------------------------------------------------------


def _seed_table() -> str:
    doc = multivendor.__doc__ or ""
    start = doc.index("Seed data")
    end = doc.index("(The seed does not decrement stock for its orders: stock is as listed.)")
    end += len("(The seed does not decrement stock for its orders: stock is as listed.)")
    return doc[start:end].strip()


DRAFT_SYSTEM = f"""\
You write a ticket's requirement as an addition to an application's executable spec, \
for the app's owner to approve before anyone writes the code. The spec is checked \
against the app: every scenario runs as HTTP requests on a fresh copy of the seed \
database, and every invariant is checked after every request.

## Answer format

Answer with ONE ```python block that defines `DELTA = SpecDelta(...)`. These names are \
already imported (do not import anything; no other module is available):

    {PREAMBLE_SOURCE.replace(chr(10), chr(10) + "    ")}

plus the builtins {", ".join(SAFE_BUILTINS)}. You may define constants and small \
helper functions in the block. Names starting with `_` after a dot, and any name with \
`__`, are refused.

## SpecDelta

    SpecDelta(scenarios=(...), invariants=(...), machines=(...), unchecked=(...))

All four are optional tuples. Do not write `programs`.

- **scenarios**: concrete examples of the ticket's behaviour on the seed data, with the \
expected answers worked out by hand. Cover the main answer, each error the ticket \
names, who may and may not call the endpoint, and what the database holds afterwards \
(including that a refused request writes nothing).
- **invariants**: rules over the whole database that must hold after every request.
- **machines**: state machines over a one_of column (the app already has one for \
`Order.status`; do not repeat it).
- **unchecked**: requirements of the ticket no scenario or invariant can state.

## Scenario and Step

    Scenario(name, steps=(Step(...), ...), given=None)

- `name`: unique, snake_case, starting with the ticket's id prefix (e.g. `t39_`).
- `given`: optional rows inserted before the steps, `{{"products": [{{"id": 9, \
"vendor_id": 3, "name": "Desk lamp", "description": None, "category": "home", \
"price": 30.0, "stock_quantity": 5, "version": 0, "is_active": True}}], ...}}` (a table \
by its SQL or schema name, every column given; one_of columns by value). Given rows \
must keep the invariants (an order needs lines adding up to its total).
- Each scenario starts from a fresh seed database; its steps run in order on it, and \
it stops at its first failing step.

    Step(method, path, user=None, json=None, params=None, expect_status=200,
         expect_body=None, expect_db=None)

- `user`: the handle the request signs in as: admin, vicky, victor, vera, carol, dave, \
frank (erin is inactive and cannot sign in); None sends no credentials (401 on a \
protected endpoint).
- `json`: the JSON body; `params`: query parameters (a dict).
- `expect_status`: the status code (201 for a created resource, 422 for an invalid \
body, ...).
- `expect_body`: None = not checked. Otherwise it must be contained in the answer: a \
dict's keys are compared recursively (keys you leave out are not checked), a list \
element by element with the same length, numbers within 1e-9. Give only what is known: \
never a new row's `created_at`. Datetimes in bodies look like "2026-01-05T10:00:00".
- `expect_db`: None = not checked; else `{{table: check}}` with one check per table:
  - `{{"count": n}}`: the table has n rows;
  - `{{"where": {{col: value}}, "count": n}}`: n rows match;
  - `{{"where": {{col: value}}, "equals": {{col: value}}}}`: at least one row matches \
and every matching row has these values.
  Tables by SQL name (orders, order_items, products, users, vendors) or schema name; \
one_of values as the API shows them ("pending"); datetimes as stored ("2026-01-05 \
10:00:00").

Helpers: `product_read(pid, **changes)` is seed product `pid` as ProductRead shows it \
(with `changes` applied); `order_read(oid, user_id, status, total, created_at, items)` \
an OrderRead; `line(item_id, product_id, quantity, price)` one of its items.

## Invariant

    Invariant(name, sentence, expr)

`sentence` is the rule in plain words; `expr` its checked form, naming tables by schema \
name (`Product`, not `products`). Three forms are allowed:
- a row rule over one table: comparisons of `Table.column`s, literals and arithmetic \
(`+ - * /`), joined by `and` / `or` / `not`, plus `is_null(Table.column)`, \
`Table.column in (...)` / `not in`, and a bare Bool column, e.g. \
`Product.stock_quantity >= 0`. A comparison with NULL counts as broken: write \
`is_null(T.c) or ...` to allow NULL.
- an aggregate equality: `Order.total_amount == sum(OrderItem.quantity * \
OrderItem.price_at_purchase, by="order_id")`, where the child's `by` column points at \
the parent's key; a parent with no child rows sums to 0.
- a count bound: `count(Vendor, by="user_id") <= 1` with an int literal.
one_of columns compare with their values (`Order.status == "pending"`) and cannot be \
ordered.

## Machine

    Machine(table, column, edges, initial=())

`edges`: the legal moves between the column's one_of values, as `{{"from": ("to", \
...)}}` or `(("from", "to"), ...)`; `initial`: the states a new row may start in.

## Unchecked

    Unchecked(text, concerns=())

`concerns` names what the requirement is about: tables ("Order"), columns \
("Order.status") or endpoints ("POST /orders").

## Seed data

{_seed_table()}
"""


@cache
def _spec_source() -> str:
    return Path(multivendor.__file__).read_text()


def _between(source: str, start: str, end: str) -> str:
    a = source.index(start)
    return source[a : source.index(end, a)].rstrip() + "\n"


def _relevant_scenarios(ticket_id: str, n: int = 3, max_steps: int = 3) -> list[Scenario]:
    """Up to ``n`` base scenarios on the ticket's resource (the routes' first path
    segment), each trimmed to ``max_steps`` steps; the first base scenarios otherwise."""
    heads = {r.split()[1].split("/")[1] for r in ticket(ticket_id).routes}
    on = [s for s in SPEC.scenarios if any(st.path.split("/")[1] in heads for st in s.steps)]
    chosen = list(dict.fromkeys([*on, *SPEC.scenarios]))[:n]
    return [replace(s, steps=s.steps[:max_steps]) for s in chosen]


def draft_prompt(ticket_id: str, *, checklist: bool = False) -> tuple[str, str]:
    """(system, user) of arm S's spec step for ``ticket_id``; ``checklist`` (arm S+):
    the edge-case checklist (:data:`bench.edit_slice.spec_checklist.CHECKLIST_SECTION`)
    appended to the system prompt, the user message unchanged."""
    system, user = _draft_prompt(ticket_id)
    if checklist:
        from bench.edit_slice.spec_checklist import CHECKLIST_SECTION

        system = system.rstrip("\n") + "\n\n" + CHECKLIST_SECTION
    return system, user


def _draft_prompt(ticket_id: str) -> tuple[str, str]:
    t = ticket(ticket_id)
    src = _spec_source()
    profile = _between(src, "USER = Schema.from_pairs(", "def profile()")
    rules = _between(src, "ORDER_MACHINE = Machine(", "# ---- users:")
    examples = SpecDelta(scenarios=tuple(_relevant_scenarios(ticket_id)))
    parts = [
        f"## Ticket: {t.title}\n\n{t.text.strip()}",
        "## The app's spec today (your delta is added to it)",
        "### Tables and response models (the reviewed profile)\n\n"
        f"```python\n{profile.rstrip()}\n```",
        f"### The state machine and the invariants\n\n```python\n{rules.rstrip()}\n```",
        "### Some existing scenarios (examples of the format, trimmed)\n\n"
        f"```python\n{render_delta(examples, name='EXAMPLES').rstrip()}\n```",
        "Write the ticket's requirement as `DELTA = SpecDelta(...)` in one ```python block.",
    ]
    return DRAFT_SYSTEM, "\n\n".join(parts)


# ---- parsing a draft ----------------------------------------------------------------------------

_BLOCK_RE = re.compile(r"```(?:python|py)[ \t]*\n(.*?)```", re.DOTALL)


def _code_block(text: str) -> str:
    blocks = _BLOCK_RE.findall(text.replace("\r\n", "\n"))
    if not blocks:
        raise DraftError(
            "Your answer has no ```python block. Answer with one ```python block that "
            "defines DELTA = SpecDelta(...)."
        )
    if len(blocks) > 1:
        raise DraftError(
            f"Your answer has {len(blocks)} ```python blocks; answer with exactly one that "
            "defines DELTA = SpecDelta(...)."
        )
    return blocks[0]


def _refused_syntax(tree: ast.AST) -> str | None:
    """Why ``tree`` may not run (an import, a dunder or private attribute), or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            return f"line {node.lineno}: imports are not allowed (every name is imported)"
        if isinstance(node, ast.Global | ast.Nonlocal):
            return f"line {node.lineno}: global/nonlocal are not allowed"
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return f"line {node.lineno}: the attribute {node.attr!r} is not allowed"
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return f"line {node.lineno}: the name {node.id!r} is not allowed"
    return None


def _payloads(delta: SpecDelta) -> list[str]:
    problems = []
    for s in delta.scenarios:
        try:
            json.dumps(s.given)
        except (TypeError, ValueError) as err:
            problems.append(f"scenario {s.name!r}: given is not JSON ({err})")
        for i, st in enumerate(s.steps, 1):
            if not isinstance(st, Step):
                problems.append(f"scenario {s.name!r}: step {i} is not a Step")
                continue
            for what in ("json", "params", "expect_body", "expect_db"):
                try:
                    json.dumps(getattr(st, what))
                except (TypeError, ValueError) as err:
                    problems.append(f"scenario {s.name!r} step {i}: {what} is not JSON ({err})")
    return problems


def _types(delta: SpecDelta) -> list[str]:
    kinds = (
        ("scenarios", Scenario),
        ("invariants", Invariant),
        ("machines", Machine),
        ("unchecked", Unchecked),
    )
    problems = [
        f"{name} must be a tuple of {cls.__name__}"
        for name, cls in kinds
        if not all(isinstance(x, cls) for x in getattr(delta, name))
    ]
    if delta.approved:
        problems.append("approved is not part of a draft: leave it out")
    if not isinstance(delta.programs, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in delta.programs.items()
    ):
        problems.append("programs must be a dict of name -> source")
    return problems


def _invalid(problems: Sequence[str]) -> DraftError:
    shown = "\n".join(f"- {p}" for p in problems)
    return DraftError(
        f"Your SpecDelta is not valid:\n{shown}\n\nReturn the corrected module in one "
        "```python block defining DELTA = SpecDelta(...)."
    )


def parse_delta(text: str) -> SpecDelta:
    """The ``SpecDelta`` the answer ``text`` defines, validated against the base spec;
    :class:`DraftError` (with the repair message) otherwise."""
    code = _code_block(text)
    try:
        tree = ast.parse(code, "<delta>")
    except SyntaxError as err:
        raise _invalid([f"line {err.lineno}: syntax error: {err.msg}"]) from err
    refused = _refused_syntax(tree)
    if refused:
        raise _invalid([refused])
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS}
    namespace: dict[str, Any] = {"__builtins__": safe, **PREAMBLE}
    try:
        exec(compile(tree, "<delta>", "exec"), namespace)  # noqa: S102 -- restricted above
    except Exception as err:  # noqa: BLE001 -- the model's code failed: fed back
        raise _invalid([f"running the module failed: {type(err).__name__}: {err}"]) from err
    delta = namespace.get("DELTA")
    if not isinstance(delta, SpecDelta):
        raise _invalid(["the module must define DELTA = SpecDelta(...)"])
    problems = _types(delta)
    if problems:
        raise _invalid(problems)
    problems = _payloads(delta)
    try:
        problems += [p.render() for p in validate(apply(SPEC, delta))]
    except Exception as err:  # noqa: BLE001 -- a malformed record the validator trips on
        problems.append(f"the spec cannot be validated: {type(err).__name__}: {err}")
    if problems:
        raise _invalid(problems)
    return delta


# ---- the spec step ------------------------------------------------------------------------------


@dataclass
class DraftResult:
    """Arm S's spec step: the valid delta (empty when the draft ``failed``), the
    generations (one per round), refused calls, and each round's text and feedback."""

    delta: SpecDelta
    generations: list[Generation] = field(default_factory=list)
    refused: list[GenerationRefused] = field(default_factory=list)
    texts: list[str | None] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    failed: bool = False

    @property
    def rounds(self) -> int:
        """Model calls of the spec step (1: valid at once)."""
        return len(self.texts)


def draft_spec(
    ticket_id: str,
    generator: Generator,
    max_repairs: int = MAX_SPEC_REPAIRS,
    *,
    checklist: bool = False,
) -> DraftResult:
    """The draft prompt, then up to ``max_repairs`` validation repairs. A draft still
    invalid after them, or a refusal, gives an empty delta (``failed``). ``checklist``:
    arm S+'s draft prompt (:func:`draft_prompt`)."""
    system, user = draft_prompt(ticket_id, checklist=checklist)
    messages = [{"role": "user", "content": user}]
    result = DraftResult(SpecDelta())
    for attempt in range(max_repairs + 1):
        try:
            generation = generator.generate(system, messages)
        except GenerationRefused as exc:
            result.refused.append(exc)
            result.texts.append(None)
            result.feedback.append(f"refused: {exc}")
            result.failed = True
            return result
        result.generations.append(generation)
        result.texts.append(generation.text)
        try:
            result.delta = parse_delta(generation.text)
            result.feedback.append("")
            return result
        except DraftError as err:
            result.feedback.append(err.feedback)
            if attempt == max_repairs:
                break
            messages.append({"role": "assistant", "content": generation.text})
            messages.append({"role": "user", "content": err.feedback})
    result.failed = True
    return result


def review_messages(ticket_id: str, delta: SpecDelta) -> tuple[str, list[dict[str, str]]]:
    """(system, messages) of arm S+'s self-review call: the S+ draft prompt, the valid
    draft (rendered) as the model's answer, then the review request
    (:data:`bench.edit_slice.spec_checklist.REVIEW_REQUEST`)."""
    from bench.edit_slice.spec_checklist import REVIEW_REQUEST

    system, user = draft_prompt(ticket_id, checklist=True)
    draft = f"```python\n{render_delta(delta)}```"
    return system, [
        {"role": "user", "content": user},
        {"role": "assistant", "content": draft},
        {"role": "user", "content": REVIEW_REQUEST},
    ]


@dataclass
class ReviewResult:
    """Arm S+'s self-review: the delta to approve (the revision, or the first draft when
    the review ``failed``: invalid or refused), the generation, a refused call, the
    answer's text and its validation feedback ("" when valid)."""

    delta: SpecDelta
    generations: list[Generation] = field(default_factory=list)
    refused: list[GenerationRefused] = field(default_factory=list)
    text: str | None = None
    feedback: str = ""
    failed: bool = False

    @property
    def rounds(self) -> int:
        """Model calls of the review (always 1: it is not repaired)."""
        return 1


def review_spec(ticket_id: str, generator: Generator, delta: SpecDelta) -> ReviewResult:
    """One self-review call on the valid draft ``delta``; the answer is parsed and
    validated as a draft (:func:`parse_delta`) with no repair. An invalid answer or a
    refusal keeps ``delta`` (``failed``)."""
    system, messages = review_messages(ticket_id, delta)
    result = ReviewResult(delta)
    try:
        generation = generator.generate(system, messages)
    except GenerationRefused as exc:
        result.refused.append(exc)
        result.feedback, result.failed = f"refused: {exc}", True
        return result
    result.generations.append(generation)
    result.text = generation.text
    try:
        result.delta = parse_delta(generation.text)
    except DraftError as err:
        result.feedback, result.failed = err.feedback, True
    return result


def drafted_rules(delta: SpecDelta) -> int:
    """Rules in ``delta`` as :func:`approve` counts them (scenarios, invariants, programs
    and machines; unchecked entries are not counted)."""
    return len(delta.scenarios) + len(delta.invariants) + len(delta.programs) + len(delta.machines)


# ---- the simulated approval ---------------------------------------------------------------------


@dataclass(frozen=True)
class Approval:
    """The approved part of a draft, the rejected rules (kind, name, why: never shown to
    the model) and the number of drafted rules (scenarios, invariants, programs and
    machines; unchecked entries are not counted)."""

    approved: SpecDelta
    rejected: tuple[tuple[str, str, str], ...]
    drafted: int

    @property
    def error_rate(self) -> float:
        return len(self.rejected) / self.drafted if self.drafted else 0.0


def _why(f: CheckFinding) -> str:
    d = f.details
    text = "; ".join(str(p) for p in d.get("problems", ())) or str(d.get("error", "")) or f.rule
    return text[:300]


def _machine_name(m: Machine) -> str:
    return f"{m.table}.{m.column}"


def approve(ticket_id: str, delta: SpecDelta) -> Approval:
    """Run ``delta``'s scenarios and invariants on ``ticket_id``'s reference and keep the
    ones it satisfies (see the module docstring)."""
    from bench.edit_slice.reference import reference_files

    files = reference_files(ticket_id)
    scenarios = {s.name: s for s in delta.scenarios}
    invariants = {i.name: i for i in delta.invariants}
    drafted = len(scenarios) + len(invariants) + len(delta.programs) + len(delta.machines)
    bad: dict[tuple[str, str], str] = {}
    if scenarios or invariants:
        rules = SpecDelta(scenarios=delta.scenarios, invariants=delta.invariants)
        report = check_candidate(ticket_id, files, rules, layers="BCD" if invariants else "CD")
        for f in report.findings:
            if f.layer == "D" and f.rule.startswith("scenario: "):
                name = f.rule.removeprefix("scenario: ")
                if name in scenarios:
                    bad.setdefault(("scenario", name), _why(f))
            elif f.layer == "C":
                inv = f.details.get("invariant")
                if inv in invariants:
                    bad.setdefault(("invariant", inv), f"broken after {f.case}")
                    continue
                # a base invariant broken by a drafted scenario's request: that scenario
                for name in scenarios:
                    if (f.case or "").startswith(f"{name} / "):
                        bad.setdefault(("scenario", name), f"breaks {inv} at {f.case}")
    kept = SpecDelta(
        scenarios=tuple(s for n, s in scenarios.items() if ("scenario", n) not in bad),
        invariants=tuple(i for n, i in invariants.items() if ("invariant", n) not in bad),
        unchecked=delta.unchecked,
    )
    if delta.programs or delta.machines:
        rules_only = replace(kept, unchecked=())  # unchecked entries may name a program
        before = {f.key for f in check_candidate(ticket_id, files, rules_only).findings}
        both = replace(rules_only, programs=delta.programs, machines=delta.machines)
        new = [f for f in check_candidate(ticket_id, files, both).findings if f.key not in before]
        if new:
            why = f"adds {len(new)} findings on the reference, e.g. {new[0].key}"[:300]
            for name in delta.programs:
                bad[("program", name)] = why
            for m in delta.machines:
                bad[("machine", _machine_name(m))] = why
        else:
            kept = replace(both, unchecked=delta.unchecked)
    if validate(apply(SPEC, kept)):
        # an unchecked entry about a rejected program: dropped with it
        kept = replace(
            kept,
            unchecked=tuple(
                u
                for u in kept.unchecked
                if not validate(apply(SPEC, replace(kept, unchecked=(u,))))
            ),
        )
    rejected = tuple((kind, name, why) for (kind, name), why in bad.items())
    return Approval(kept, rejected, drafted)


def owner_delta(ticket_id: str) -> SpecDelta:
    """Arm S*'s delta: the owner's, written from the ticket before any check ran
    (``BLIND_DELTAS`` for t31-t44; the pivot's ``BLIND_PIVOT_DELTAS`` for t45-t56,
    plan 2026-10-05-pivot-spec-authoring.md, item D)."""
    if ticket_id in BLIND_DELTAS:
        return BLIND_DELTAS[ticket_id]
    from bench.specs.pivot_blind import BLIND_PIVOT_DELTAS

    return BLIND_PIVOT_DELTAS[ticket_id]


def _owner_approved(ticket_id: str) -> tuple[Any, ...]:
    """The ticket's owner-approved layer-A findings, outside any delta: ``DELTAS``'s for
    t31-t44; the pivot's blind delta's own ``approved`` (e.g. t55's public directory) for
    t45-t56 -- there is no non-blind ``DELTAS`` entry for the pivot's tickets."""
    if ticket_id in DELTAS:
        return DELTAS[ticket_id].approved
    return owner_delta(ticket_id).approved


# ---- rendering a delta --------------------------------------------------------------------------


def _value(value: Any, indent: int, width: int = 96) -> str:
    """``value``'s repr, on one line when it fits, else one element (or key) per line."""
    one = repr(value)
    if len(one) + indent <= width or not isinstance(value, list | tuple | dict) or not value:
        return one
    pad = " " * (indent + 4)
    if isinstance(value, dict):
        items = [f"{k!r}: {_value(v, indent + 4, width)}" for k, v in value.items()]
        opening, closing = "{", "}"
    else:
        items = [_value(v, indent + 4, width) for v in value]
        opening, closing = ("[", "]") if isinstance(value, list) else ("(", ")")
    return opening + "\n" + "".join(f"{pad}{x},\n" for x in items) + " " * indent + closing


def _call(name: str, args: Sequence[str], indent: int) -> str:
    one = f"{name}({', '.join(args)})"
    if len(one) + indent <= 96 and "\n" not in one:
        return one
    pad = " " * (indent + 4)
    return f"{name}(\n" + "".join(f"{pad}{a},\n" for a in args) + " " * indent + ")"


def _step(st: Step, indent: int) -> str:
    inner = indent + 4
    args = [repr(st.method), repr(st.path)]
    if st.user is not None:
        args.append(repr(st.user))
    defaults = {"json": None, "params": None, "expect_status": 200}
    for name in ("json", "params", "expect_status", "expect_body", "expect_db"):
        value = getattr(st, name)
        if value is None or (name in defaults and value == defaults[name]):
            continue
        args.append(f"{name}={_value(value, inner)}")
    return _call("Step", args, indent)


def _tuple(items: Sequence[str], indent: int) -> str:
    if not items:
        return "()"
    pad = " " * (indent + 4)
    return "(\n" + "".join(f"{pad}{x},\n" for x in items) + " " * indent + ")"


def _scenario(s: Scenario, indent: int) -> str:
    inner = indent + 4
    args = [repr(s.name)]
    if s.given is not None:
        args.append(f"given={_value(s.given, inner)}")
    args.append("steps=" + _tuple([_step(st, inner + 4) for st in s.steps], inner))
    return _call("Scenario", args, indent)


def _program(source: str) -> str:
    if '"""' not in source and "\\" not in source:
        return '"""' + source + '"""'
    return repr(source)


def render_delta(delta: SpecDelta, name: str = "DELTA") -> str:
    """``delta`` as the Python module that defines it (``name = SpecDelta(...)``), for a
    prompt; :func:`parse_delta` reads it back to an equal delta."""
    parts = []
    if delta.scenarios:
        parts.append("scenarios=" + _tuple([_scenario(s, 8) for s in delta.scenarios], 4))
    if delta.invariants:
        invs = [
            _call("Invariant", [repr(i.name), repr(i.sentence), repr(i.expr)], 8)
            for i in delta.invariants
        ]
        parts.append("invariants=" + _tuple(invs, 4))
    if delta.machines:
        machines = [
            _call(
                "Machine",
                [
                    repr(m.table),
                    repr(m.column),
                    f"edges={_value(m.edges, 12)}",
                    f"initial={m.initial!r}",
                ],
                8,
            )
            for m in delta.machines
        ]
        parts.append("machines=" + _tuple(machines, 4))
    if delta.unchecked:
        unchecked = [
            _call("Unchecked", [_value(u.text, 12), f"concerns={u.concerns!r}"], 8)
            for u in delta.unchecked
        ]
        parts.append("unchecked=" + _tuple(unchecked, 4))
    if delta.programs:
        progs = "".join(f"        {k!r}: {_program(v)},\n" for k, v in delta.programs.items())
        parts.append("programs={\n" + progs + "    }")
    if not parts:
        return f"{name} = SpecDelta()\n"
    return f"{name} = SpecDelta(\n" + "".join(f"    {p},\n" for p in parts) + ")\n"


def is_empty(delta: SpecDelta) -> bool:
    return not (
        delta.scenarios or delta.invariants or delta.machines or delta.unchecked or delta.programs
    )


# ---- the code prompt and the check round --------------------------------------------------------

SPEC_CHECK_NOTE = """\
## The spec check

After the smoke run, the edited app is checked against the app's spec: the static \
checks above (A), the endpoints the spec has programs for (B), the spec's invariants \
after every request (C) and its scenarios (D), including the ticket's approved \
scenarios and invariants when the ticket shows them. Any finding is sent back to you \
to fix."""


def code_prompt(ticket_id: str, delta: SpecDelta) -> tuple[str, str]:
    """(system, user) of the code step of arms S and S*: arm Q's prompt, the spec check
    stated in the system prompt, and ``delta`` after the ticket's files (no section for
    an empty delta)."""
    from bench.edit_slice.run import q_prompt

    system, user = q_prompt(ticket_id)
    system = system.rstrip("\n") + "\n\n" + SPEC_CHECK_NOTE + "\n"
    if is_empty(delta):
        return system, user
    section = f"## {SPEC_SECTION}\n\n```python\n{render_delta(delta).rstrip()}\n```"
    return system, user + "\n\n" + section


def check_candidate(
    ticket_id: str, files: Mapping[str, str], delta: SpecDelta, *, layers: str = "ABCD"
) -> Report:
    """The candidate app (``files`` relative to the app package) checked against
    ``apply(SPEC, delta)`` with the app's baseline. The owner's approvals for the ticket
    (:func:`_owner_approved`, e.g. t33's public aggregate or t55's public directory,
    stated in its text) always apply: a draft cannot write approvals, the simulated
    owner does."""
    from bench.specs.exit import check_ticket

    owner = _owner_approved(ticket_id)
    delta = replace(
        delta, approved=(*delta.approved, *(a for a in owner if a not in delta.approved))
    )
    return check_ticket(ticket_id, files, layers=layers, delta=delta)


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _finding_lines(f: CheckFinding) -> list[str]:
    d = f.details
    if f.layer == "A":
        where = f"{prompts.APP_PREFIX}{f.file}, line {f.line}" if f.file else f.endpoint or ""
        out = [f"  - {f.code} at {where}: {d.get('message', '')}"]
        if d.get("hint"):
            out.append(f"    fix: {d['hint']}")
        return out
    req = f.request or {}
    who = f" as {req['user']}" if req.get("user") else ""
    call = f"{req.get('method', '')} {req.get('path', '')}".strip() or (f.endpoint or "")
    out = [f"  - {call}{who} ({f.case})"]
    extra = {k: req[k] for k in ("params", "json") if req.get(k) is not None}
    if extra:
        out.append(f"    request: {_short(extra)}")
    expected, actual = d.get("expected"), d.get("actual")
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        body = expected.get("body")
        want = f"{expected.get('status')}" + (f" {_short(body)}" if body is not None else "")
        out.append(f"    expected {want}; got {actual.get('status')} {_short(actual.get('body'))}")
    for p in list(d.get("problems", ()))[:3]:
        if not str(p).startswith(("status:", "body:")):
            out.append(f"    {_short(str(p), 200)}")
    for table, diff in f.db_diff.items():
        parts = [f"{k} {_short(v, 120)}" for k, v in diff.items() if v]
        if parts:
            out.append(f"    db {table}: " + "; ".join(parts))
    for v in f.invariant_violations:
        out.append(f"    violates {v.get('invariant')}: rows {_short(v.get('rows'), 200)}")
    if d.get("error"):
        out.append(f"    error: {_short(str(d['error']), 200)}")
    return out


def spec_feedback(report: Report, limit: int = MAX_FINDINGS) -> str:
    """``report``'s findings as repair feedback: each broken rule, then (at most
    ``limit`` findings in all) the requests that break it, with expected vs got, the
    database difference and the invariant's rows."""
    by_rule: dict[tuple[str, str], list[CheckFinding]] = {}
    for f in report.findings:
        by_rule.setdefault((f.layer, f.rule), []).append(f)
    n = len(report.findings)
    lines = [f"The spec check found {n} problem{'s' if n != 1 else ''} in the edited app:"]
    shown = 0
    for (layer, rule), found in by_rule.items():
        if shown >= limit:
            break
        lines.append("")
        lines.append(f"rule ({layer}: {LAYER_NAMES[layer]}): {rule}")
        for f in found:
            if shown >= limit:
                break
            lines.extend(_finding_lines(f))
            shown += 1
    if n > shown:
        lines.append("")
        lines.append(f"({n - shown} more findings not shown.)")
    return "\n".join(lines)


def finding_layers(report: Report) -> list[str]:
    return sorted({f.layer for f in report.findings})


# ---- offline coverage ---------------------------------------------------------------------------


def coverage(ticket_id: str, delta: SpecDelta) -> tuple[int, int]:
    """(traps caught, traps) of ``ticket_id``'s trap variants against ``apply(SPEC,
    delta)``. No model: Phase 0's ``TRAPS`` (tests/bench/test_edit_slice_option1.py) for
    t31-t44, the pivot's (tests/bench/test_edit_slice_pivot.py) for t45-t56."""
    from bench.specs.exit import _traps

    trap_set = "phase0" if ticket_id in DELTAS else "pivot"
    traps = [make for tid, make in _traps(trap_set).values() if tid == ticket_id]
    caught = sum(check_candidate(ticket_id, make(), delta).verdict == "fail" for make in traps)
    return caught, len(traps)


# ---- the prompt-size estimate -------------------------------------------------------------------


def estimate_rows(tickets: Sequence[str]) -> list[dict[str, Any]]:
    """Per ticket, the chars of the S draft prompt, the S+ draft prompt (S's plus the
    checklist), the S+ review call (the S+ draft prompt, the owner's delta standing in
    for the first draft, the review request), the S/S+/S* code prompts (the owner's
    delta standing in for the approved draft) and, for comparison, arm Q's."""
    rows = []
    for tid in tickets:
        d_sys, d_user = draft_prompt(tid)
        p_sys, p_user = draft_prompt(tid, checklist=True)
        r_sys, r_msgs = review_messages(tid, owner_delta(tid))
        c_sys, c_user = code_prompt(tid, owner_delta(tid))
        from bench.edit_slice.run import q_prompt

        q_sys, q_user = q_prompt(tid)
        rows.append(
            {
                "ticket": tid,
                "draft_chars": len(d_sys) + len(d_user),
                "splus_draft_chars": len(p_sys) + len(p_user),
                "review_chars": len(r_sys) + sum(len(m["content"]) for m in r_msgs),
                "code_chars": len(c_sys) + len(c_user),
                "q_chars": len(q_sys) + len(q_user),
            }
        )
    return rows


def render_estimate(rows: Sequence[Mapping[str, Any]]) -> str:
    def tok(chars: int) -> int:
        return round(chars / CHARS_PER_TOKEN)

    keys = ("draft_chars", "splus_draft_chars", "review_chars", "code_chars", "q_chars")
    lines = [
        "| ticket | S draft chars | tok | S+ draft chars | tok | S+ review chars | tok "
        "| S/S+/S* code chars | tok | Q chars | tok |",
        "|---|" + "---:|" * (2 * len(keys)),
    ]
    totals = dict.fromkeys(keys, 0)
    for r in rows:
        cells = " | ".join(f"{r[k]} | {tok(r[k])}" for k in keys)
        lines.append(f"| {r['ticket']} | {cells} |")
        for k in keys:
            totals[k] += r[k]
    cells = " | ".join(f"{totals[k]} | {tok(totals[k])}" for k in keys)
    lines.append(f"| **total** | {cells} |")
    lines.append(
        f"\nfirst-prompt sizes only (chars / {CHARS_PER_TOKEN}); the code prompts and the S+ "
        "review use the owner's blind delta as a stand-in for the drafted one; no model "
        "call was made"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    from bench.edit_slice.tickets import OPTION1, OPTION1B, PIVOT

    p = argparse.ArgumentParser(prog="python -m bench.edit_slice.sdraft", description=__doc__)
    p.add_argument("--estimate", action="store_true", help="print the prompt sizes")
    p.add_argument(
        "--tickets",
        default=",".join((*OPTION1, *OPTION1B)),
        help="comma-separated ticket ids, or pivot (the pivot's t45-t56)",
    )
    args = p.parse_args(argv)
    if not args.estimate:
        p.print_help()
        return 2
    started = time.perf_counter()
    tickets = list(PIVOT) if args.tickets == "pivot" else [
        t.strip() for t in args.tickets.split(",") if t.strip()
    ]  # fmt: skip
    print(render_estimate(estimate_rows(tickets)))
    print(f"({time.perf_counter() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
