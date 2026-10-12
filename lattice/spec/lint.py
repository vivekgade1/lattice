"""Lint an LLM-drafted spec against the owner's requirements: mechanical, no LLM.

``lint(spec, trace)`` reads a drafted `Spec` and its trace (`lattice.spec.rules.Trace`:
the owner's numbered rules and the spec elements linked to each) and answers
`LintFinding`s, each with an exact ``fix`` the drafting model can apply in a repair
round. Every finding costs a round, so each code prefers silence over a guess; the
firing conditions and their known limits:

* **L001 untraced rule** -- a rule in none of ``links`` (with at least one target),
  ``unchecked`` or ``context`` (`Trace.untraced`). Limit: none; it is exact.
* **L002 dangling link** -- a link target that names nothing in the spec:
  ``scenario:<name>`` / ``invariant:<name>`` / ``service:<name>`` not declared,
  ``machine:<Table.column>`` with no machine on that column (schema or SQL table
  name), ``profile:<Table>`` or ``profile:<Table.column>`` not in the profile, a kind
  outside `LINK_KINDS`, or a rule id in ``links`` / ``unchecked`` / ``context`` that
  is not one of the trace's rules. Limit: exact.
* **L003 weak trace** -- only for a linked rule whose text has a signal word (never, at
  most, only, unique, duplicate, exactly, must, every, all). (a) every link is a
  scenario, and no linked scenario has a failing expectation: a step expecting a
  non-2xx status, an ``expect_db``, an ``expect_body``, or an ``Await``. (b) the rule
  says "never" / "at most" / "unique", has no ``invariant:``, ``machine:`` or
  ``profile:`` link, and no linked scenario sets up the forbidden case: a write
  expecting a 4xx, an ``Await``, or -- with at least two writes (``given`` rows count
  as one) -- a failing expectation at or after the second write. Limit: (b) cannot
  read whether the case set up is the one the rule forbids; any such shape satisfies it.
* **L004 refused write without a "nothing written" check** -- a POST/PUT/PATCH/DELETE
  step expecting a 4xx with no ``expect_db``. The fix names the table the path most
  likely writes (a path segment matching a table's SQL or schema name), else asks for
  it. Limit: a refusal the app answers before touching any table still needs the check.
* **L005 write without its effect** -- a write step expecting a 2xx with neither
  ``expect_db`` nor ``expect_body``, and no later step of its scenario with a failing
  expectation (as in L003 (a)). Limit: a later check on another resource counts as
  reading the effect.
* **L006 access coverage** -- per endpoint template (``METHOD /path`` with path
  parameters as ``{}``: a contract's path when one matches, else digit and UUID
  segments), only when the spec has users. (a) no step on it has ``user=None``
  expecting a status in ``LintConfig.anon_statuses`` (401, 403). A template is public,
  and skipped, when a contract says ``auth="Public"``, a ``user=None`` step on it
  expects anything else, or its path matches ``LintConfig.public_paths`` (login,
  register, token, health...), or every step on it expects 405 (a method-not-allowed
  route: the router answers before authentication, so there is no anonymous behaviour to
  cover). (b) only when the trace's rules (not context) mention
  outsiders (``LintConfig.outsider_patterns``: "not a member", "non-member",
  "outsider", or a 404 named with "member"), or ``LintConfig.outsiders`` is True: a
  template with a path parameter, not public, called by a signed-in user somewhere,
  where no signed-in step expects a status in ``outsider_statuses`` (404). Restrict it
  with ``outsider_paths`` (a regex on the template). Limit: lint cannot tell which
  handle is the outsider; any signed-in step expecting 404 covers the template.
* **L007 enum or limit without an invalid case** -- (a) a ``one_of`` column whose name
  some step sends (a ``json`` / ``params`` key at any depth), with no step sending
  that key a value outside ``one_of`` and expecting a status in
  ``LintConfig.invalid_statuses`` (400, 422), and no contract parameter with a
  ``one_of`` for it (layer R probes those). Columns no request sends are skipped.
  (b) a linked rule stating a range -- "1-100 characters", "8 to 72 bytes", "between
  1 and 10" (a dash/"to" range needs a unit: characters, chars, bytes, letters,
  digits, items, elements, entries) -- with no step expecting an invalid status that
  sends a value outside it (a text, by length -- bytes as UTF-8 -- for the text
  units; a list, by length, for the item units; a number, else) and no contract
  parameter with that bound. Limit: the out-of-range value may belong to another field.
* **L008 background rule without an Await** -- a linked rule mentioning background,
  loop, "in production", "every minute" or "once a minute", not in ``unchecked``,
  with no ``service:`` link and no linked scenario holding an ``Await``.
  Limit: keyword-based; a rule saying "loop" about something else still asks.
* **L009 vacuous scenario** -- a scenario with zero layer D findings against a stub app
  that answers every request 200 ``{}`` ("all-200") or 500 ("all-500"), signed in
  with a dummy token (the stub accepts anything) and with every service a no-op
  process (an ``Await`` waits at most ``VacuityConfig.await_cap_s``). Needs a
  `VacuityConfig` (the db factory); skipped with a note without one, or when the
  check cannot run (an invalid spec, a failing db factory). Limit: exact for what it
  runs.
* **L010 question** -- an unanswered question with a blank default, or a question
  whose ``rules`` is empty or names an id that is not one of the trace's rules.
* **L011 conflicting expectations** (`conflicts`) -- two steps, in two scenarios with
  the same ``given`` (compared as canonical JSON; none and empty are the same), that
  send the same request and expect different statuses. Same request: the same caller,
  method and path template (as L006), the same concrete path (query string aside), and
  -- unless the two statuses are an access answer (401/403) against a 2xx -- the same
  ``json`` and ``params``. Each step must be the scenario's first step on that template
  and run on the setup alone: no step before it in its scenario is an ``Await`` or a
  write expecting a 2xx (a write the spec refuses changes nothing). Each pair is one
  finding naming both scenarios, with the owner's question ("Which holds?") as its fix.
  Limits: a write expecting a 2xx on another resource still silences the pair (precision
  first); two requests whose bodies differ only by a value that decides validation
  (422 vs 201) are not compared; a contradiction spread across different setups is not
  seen.

* **L012 unsatisfiable expectation** (`lattice.spec.preconditions.contradictions`) -- a
  step expects *no* row of a table where W after an earlier step of its scenario expected
  one (same table, same ``where``), and nothing in between can remove it (every step after
  the earlier one is a read, or a request the spec says is refused, ``expect_status >=
  400``; an ``Await`` or a write expecting a 2xx ends the pair). No implementation can
  pass it. Limits: only the same ``where`` (equal as written); rests on "a refused request
  changes nothing". ``check`` adds the other half at run time: a failing ``expect_db``
  whose row nothing before it can have created says so ("spec problem: ...").

CLI (``main``): ``lint --spec MOD:SPEC|FILE.py [--trace PATH] [--db MOD:fn]
[--login MOD:fn] [--json]``; prints the findings grouped by code with their fixes (or
JSON: ``{"findings": [...], "notes": [...]}``); exit 0 with no findings, 1 with
some, 2 when the lint cannot run.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from lattice.spec.model import Await, Scenario, Spec, Step, resolve_table, tables_of
from lattice.spec.preconditions import contradictions
from lattice.spec.rules import LINK_KINDS, Trace, trace_path

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CODES: Mapping[str, str] = {
    "L001": "untraced rule",
    "L002": "dangling link",
    "L003": "weak trace",
    "L004": "refused write without a 'nothing written' check",
    "L005": "write without its effect checked",
    "L006": "access coverage",
    "L007": "enum or limit without an invalid case",
    "L008": "background rule without an Await",
    "L009": "vacuous scenario",
    "L010": "question without a default or rules",
    "L011": "conflicting expectations",
    "L012": "unsatisfiable expectation",
}

_SIGNAL = re.compile(r"\b(never|at most|only|unique\w*|duplicat\w*|exactly|must|every|all)\b", re.I)
_FORBID = re.compile(r"\b(never|at most|unique\w*)\b", re.I)
_BACKGROUND = re.compile(r"\b(background|loops?|in production|every minute|once a minute)\b", re.I)
_TEXT_UNITS = ("character", "characters", "chars", "char", "bytes", "byte", "letters",
               "letter", "digits", "digit")  # fmt: skip
_ITEM_UNITS = ("items", "item", "elements", "element", "entries", "entry")
_UNIT = "|".join(sorted(_TEXT_UNITS + _ITEM_UNITS, key=len, reverse=True))
_DASH_RANGE = re.compile(rf"\b(\d+)\s*(?:-|–|—|to)\s*(\d+)\s*({_UNIT})\b", re.I)
_BETWEEN = re.compile(rf"\bbetween\s+(\d+)\s+and\s+(\d+)(?:\s+({_UNIT})\b)?", re.I)
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{12}$")  # fmt: skip

Login = Callable[[Any, str], str]


@dataclass(frozen=True)
class LintFinding:
    """One gap: its `code` (CODES), the rule it concerns (an id of the trace, or None),
    the spec element (``kind:name``, or None), what is wrong, and the exact `fix`."""

    code: str
    rule_id: str | None
    element: str | None
    message: str
    fix: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LintConfig:
    """The heuristics' knobs (see the module docstring, L006 and L007)."""

    anon_statuses: tuple[int, ...] = (401, 403)
    public_paths: str = (
        r"^/(api/)?(v\d+/)?(login|logout|signin|signup|register|token|auth|health|healthz"
        r"|docs|openapi\.json)(/|$)"
    )
    outsiders: bool | None = None  # None: from the rules' text
    outsider_patterns: tuple[str, ...] = (
        r"\bnot an? member\b",
        r"\bisn'?t an? member\b",
        r"\bnon-?members?\b",
        r"\boutsiders?\b",
        r"\b404\b.*\bmember|\bmember.*\b404\b",
    )
    outsider_statuses: tuple[int, ...] = (404,)
    outsider_paths: str | None = None  # a regex the template must match; None: all
    invalid_statuses: tuple[int, ...] = (400, 422)


@dataclass(frozen=True)
class VacuityConfig:
    """How L009 builds the stub runs: `db_factory` (as `check`'s), `login` -- the
    stub's login hook, ``(client, handle) -> token``; it runs against the stub, so it
    must not need the real app (default: a dummy token) -- and `await_cap_s`, the
    longest an ``Await`` waits on the stub's no-op service."""

    db_factory: Callable[..., Any]
    login: Login | None = None
    await_cap_s: float = 1.0


# ---- the spec, walked --------------------------------------------------------------------


def _steps(scenario: Scenario) -> list[tuple[int, Step | Await]]:
    return list(enumerate(scenario.steps, 1))


def _is_write(step: Step | Await) -> bool:
    return isinstance(step, Step) and step.method.upper() in WRITE_METHODS


def _ok(status: int) -> bool:
    return 200 <= status < 300


def _failing(step: Step | Await) -> bool:
    """Whether a do-nothing app could fail this step's expectation."""
    if isinstance(step, Await):
        return True
    return not _ok(step.expect_status) or bool(step.expect_db) or step.expect_body is not None


def _has_failing(scenario: Scenario) -> bool:
    return any(_failing(s) for s in scenario.steps)


def _sets_up_forbidden(scenario: Scenario) -> bool:
    """A refused write, an Await, or a failing expectation at or after the second
    write (given rows count as one)."""
    writes = 1 if scenario.given else 0
    for step in scenario.steps:
        if isinstance(step, Await):
            return True
        if _is_write(step):
            if 400 <= step.expect_status < 500:
                return True
            writes += 1
        if writes >= 2 and _failing(step):
            return True
    return False


def _leaves(value: Any, key: str | None = None) -> Iterator[tuple[str | None, Any]]:
    """(key, value) of every value in a JSON payload, containers included."""
    yield key, value
    if isinstance(value, Mapping):
        for k, v in value.items():
            yield from _leaves(v, str(k))
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _leaves(v, key)


def _payload(step: Step) -> list[tuple[str | None, Any]]:
    out = list(_leaves(step.json)) if step.json is not None else []
    if step.params:
        out += list(_leaves(dict(step.params)))
    return out


def _parse_target(target: str) -> tuple[str, str]:
    kind, _, name = target.partition(":")
    return kind.strip(), name.strip()


def _rules_by_element(trace: Trace) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rid, targets in trace.links.items():
        for t in targets:
            out.setdefault(t, []).append(rid)
    return out


# ---- L001, L002 --------------------------------------------------------------------------


def _l001(trace: Trace) -> list[LintFinding]:
    return [
        LintFinding(
            "L001",
            r.id,
            None,
            f"{r.id} ({_quote(r.text)}) is not linked to any spec element, not "
            "unchecked and not context",
            f'Link {r.id} in the trace: add "links": {{"{r.id}": ["scenario:<name>"]}} '
            "naming the scenario/invariant/machine/service/profile element that checks "
            f'it, or "unchecked": {{"{r.id}": "<why it cannot be checked>"}}, or add '
            f'"{r.id}" to "context" if it states no checkable behaviour.',
        )
        for r in trace.untraced()
    ]


def _element_exists(spec: Spec, kind: str, name: str) -> bool:
    if kind == "scenario":
        return any(s.name == name for s in spec.scenarios)
    if kind == "invariant":
        return any(i.name == name for i in spec.invariants)
    if kind == "service":
        return name in spec.service_names()
    if kind == "machine":
        table, _, column = name.partition(".")
        found = resolve_table(spec.profile, table)
        return found is not None and any(
            resolve_table(spec.profile, m.table) is not None
            and resolve_table(spec.profile, m.table)[0] == found[0]  # type: ignore[index]
            and m.column == column
            for m in spec.machines
        )
    if kind == "profile":
        table, _, column = name.partition(".")
        found = resolve_table(spec.profile, table)
        if found is None:
            return False
        return not column or found[2].get(column) is not None
    return False


def _known_names(spec: Spec, kind: str) -> list[str]:
    if kind == "scenario":
        return [s.name for s in spec.scenarios]
    if kind == "invariant":
        return [i.name for i in spec.invariants]
    if kind == "service":
        return list(spec.service_names())
    if kind == "machine":
        return [f"{m.table}.{m.column}" for m in spec.machines]
    if kind == "profile":
        return list(tables_of(spec.profile)[0])
    return []


def _l002(spec: Spec, trace: Trace) -> list[LintFinding]:
    out: list[LintFinding] = []
    ids = {r.id for r in trace.rules}
    for rid, targets in trace.links.items():
        if rid not in ids:
            out.append(_unknown_rule(rid, "links"))
            continue
        for target in targets:
            kind, name = _parse_target(target)
            if kind not in LINK_KINDS:
                out.append(LintFinding(
                    "L002", rid, target,
                    f"{rid} links to {target!r}, whose kind {kind!r} is not one of "
                    f"{', '.join(LINK_KINDS)}",
                    f"In the trace's links for {rid}, write the target as <kind>:<name> "
                    f"with kind one of {', '.join(LINK_KINDS)}.",
                ))  # fmt: skip
            elif not _element_exists(spec, kind, name):
                known = _known_names(spec, kind)
                have = f" (SPEC has: {', '.join(known[:12])})" if known else ""
                out.append(LintFinding(
                    "L002", rid, target,
                    f"{rid} links to {target}, which SPEC does not define{have}",
                    f"Either add the {kind} {name!r} to SPEC, or change {rid}'s link "
                    f"{target!r} in the trace to an element SPEC has.",
                ))  # fmt: skip
    for rid in trace.unchecked:
        if rid not in ids:
            out.append(_unknown_rule(rid, "unchecked"))
    for rid in trace.context:
        if rid not in ids:
            out.append(_unknown_rule(rid, "context"))
    return out


def _unknown_rule(rid: str, where: str) -> LintFinding:
    return LintFinding(
        "L002", rid, None,
        f"the trace's {where} names {rid}, which is not one of its rules",
        f'Remove {rid!r} from the trace\'s "{where}", or use the id of the rule meant '
        '(the ids are those of "rules").',
    )  # fmt: skip


# ---- L003 --------------------------------------------------------------------------------


def _l003(spec: Spec, trace: Trace) -> list[LintFinding]:
    scenarios = {s.name: s for s in spec.scenarios}
    out: list[LintFinding] = []
    for rule in trace.rules:
        targets = trace.links.get(rule.id) or []
        if not targets or not _SIGNAL.search(rule.text):
            continue
        parsed = [_parse_target(t) for t in targets]
        linked = [scenarios[n] for k, n in parsed if k == "scenario" and n in scenarios]
        if any(k == "scenario" and n not in scenarios for k, n in parsed):
            continue  # a dangling scenario link: L002 says so
        if (
            all(k == "scenario" for k, _ in parsed)
            and linked
            and not any(_has_failing(s) for s in linked)
        ):
            names = ", ".join(s.name for s in linked)
            out.append(LintFinding(
                "L003", rule.id, f"scenario:{linked[0].name}",
                f"{rule.id} ({_quote(rule.text)}) is linked only to scenario(s) {names}, "
                "whose steps expect only 2xx with no expect_body, expect_db or Await: an "
                "app that ignores the rule passes them",
                f"In scenario {linked[0].name!r}, add a step that would fail if {rule.id} "
                "were broken -- the request the rule forbids, expecting its refusal "
                "(e.g. expect_status=409 or 422) with expect_db={<table>: {\"count\": "
                "<rows before the request>}}, or an expect_body/expect_db stating the "
                f"effect the rule requires; or link {rule.id} to an invariant that states "
                "it (invariant:<name>).",
            ))  # fmt: skip
            continue
        word = _FORBID.search(rule.text)
        if word is None or any(k in ("invariant", "machine", "profile") for k, _ in parsed):
            continue
        if any(_sets_up_forbidden(s) for s in linked):
            continue
        where = (f"scenario {linked[0].name!r}" if linked else "a new scenario linked to "
                 f"{rule.id}")  # fmt: skip
        out.append(LintFinding(
            "L003", rule.id, f"scenario:{linked[0].name}" if linked else None,
            f"{rule.id} ({_quote(rule.text)}) says {word.group(1)!r}, but no invariant, "
            "machine or profile fact checks it and no linked scenario sets up the "
            "forbidden case (a refused write, or a second write followed by a check)",
            f"Either add an Invariant stating {rule.id} (e.g. count(<Table>, "
            "by=\"<column>\") <= <n>) and link it as invariant:<name>, or in "
            f"{where} perform the allowed write, then attempt the forbidden one and "
            "expect its refusal (a 4xx) with expect_db={<table>: {\"count\": <rows after "
            "the allowed write>}}.",
        ))  # fmt: skip
    return out


# ---- L004, L005 --------------------------------------------------------------------------


def _guess_table(spec: Spec, path: str) -> str | None:
    tables, sql_names = tables_of(spec.profile)
    by_name = {}
    for schema_name in tables:
        sql = sql_names.get(schema_name, schema_name)
        for spelling in (sql, schema_name, schema_name.lower(), schema_name.lower() + "s"):
            by_name.setdefault(spelling.lower(), sql)
    for seg in reversed([s for s in path.split("?")[0].split("/") if s]):
        hit = by_name.get(seg.lower()) or by_name.get(seg.lower().replace("-", "_"))
        if hit:
            return hit
    return None


def _table_word(spec: Spec, path: str) -> str:
    table = _guess_table(spec, path)
    return repr(table) if table else "<the table this request writes>"


def _scenario_rule(by_element: Mapping[str, list[str]], name: str) -> str | None:
    rules = by_element.get(f"scenario:{name}", [])
    return rules[0] if len(rules) == 1 else None


def _l004_l005(spec: Spec, trace: Trace) -> list[LintFinding]:
    by_element = _rules_by_element(trace)
    out: list[LintFinding] = []
    for scenario in spec.scenarios:
        steps = _steps(scenario)
        rid = _scenario_rule(by_element, scenario.name)
        for i, step in steps:
            if not _is_write(step):
                continue
            assert isinstance(step, Step)
            table = _guess_table(spec, step.path)
            tname = repr(table) if table else '"<the table this request writes>"'
            here = f"step {i} ({step.method} {step.path} as {step.user or 'anon'})"
            if 400 <= step.expect_status < 500 and not step.expect_db:
                out.append(LintFinding(
                    "L004", rid, f"scenario:{scenario.name}",
                    f"{here} of scenario {scenario.name!r} expects {step.expect_status} but "
                    "does not check that nothing was written",
                    f"Give step {i} of scenario {scenario.name!r} expect_db={{{tname}: "
                    "{\"count\": <its row count before this step>}} so a write that "
                    "answers the refusal but still changes the table fails.",
                ))  # fmt: skip
            elif _ok(step.expect_status) and not step.expect_db and step.expect_body is None:
                later = [s for j, s in steps if j > i]
                if any(_failing(s) for s in later):
                    continue
                out.append(LintFinding(
                    "L005", rid, f"scenario:{scenario.name}",
                    f"{here} of scenario {scenario.name!r} expects {step.expect_status} "
                    "with no expect_db or expect_body, and no later step checks its effect",
                    f"Give step {i} of scenario {scenario.name!r} expect_db={{{tname}: "
                    "{\"where\": {...}, \"equals\": {...}}} (or {\"count\": n}) stating the "
                    "row it writes, or an expect_body with the fields it answers, or add "
                    "a later GET step whose expect_body reads the change.",
                ))  # fmt: skip
    return out


# ---- L006 --------------------------------------------------------------------------------


def _contract_templates(spec: Spec) -> list[tuple[re.Pattern[str], str, str, str | None]]:
    out = []
    for c in spec.contracts:
        template = re.sub(r"\{[^}/]*\}", "{}", c.path)
        rx = re.compile("^" + re.escape(template).replace(r"\{\}", "[^/]+") + "/?$")
        out.append((rx, template, c.method, c.auth))
    return out


def template_of(path: str, contracts: Sequence[tuple[re.Pattern[str], str, str, Any]] = ()
                ) -> str:  # fmt: skip
    """`path` with its parameters as ``{}``: a matching contract template, else every
    digit or UUID segment (``/applications/1/jobs/2`` -> ``/applications/{}/jobs/{}``)."""
    bare = path.split("?")[0]
    for rx, template, _, _ in contracts:
        if rx.match(bare):
            return template
    segs = ["{}" if s.isdigit() or _UUID.match(s) else s for s in bare.split("/")]
    return "/".join(segs) or "/"


def _outsiders(trace: Trace, config: LintConfig) -> bool:
    if config.outsiders is not None:
        return config.outsiders
    context = set(trace.context)
    pats = [re.compile(p, re.I) for p in config.outsider_patterns]
    return any(p.search(r.text) for r in trace.rules if r.id not in context for p in pats)


def _l006(spec: Spec, trace: Trace, config: LintConfig) -> list[LintFinding]:
    if not spec.users:
        return []
    contracts = _contract_templates(spec)
    public_contract = {(m, t) for _, t, m, auth in contracts if auth == "Public"}
    by_ep: dict[tuple[str, str], list[tuple[Scenario, int, Step]]] = {}
    for scenario in spec.scenarios:
        for i, step in _steps(scenario):
            if isinstance(step, Step):
                key = (step.method.upper(), template_of(step.path, contracts))
                by_ep.setdefault(key, []).append((scenario, i, step))
    public_rx = re.compile(config.public_paths, re.I)
    outsider_rx = re.compile(config.outsider_paths) if config.outsider_paths else None
    outsiders = _outsiders(trace, config)
    out: list[LintFinding] = []
    for (method, template), uses in by_ep.items():
        anon = [s for _, _, s in uses if s.user is None]
        if all(s.expect_status == 405 for _, _, s in uses):
            continue  # a 405-only route: the router refuses it before any auth check
        if ((method, template) in public_contract or public_rx.search(template)
                or any(s.expect_status not in config.anon_statuses for s in anon)):  # fmt: skip
            continue
        scenario, _, example = uses[0]
        ep = f"{method} {template}"
        if not anon:
            out.append(LintFinding(
                "L006", None, f"endpoint:{ep}",
                f"no step calls {ep} without a user (user=None) expecting "
                f"{config.anon_statuses[0]}",
                f"Add a scenario with Step({method!r}, {example.path!r}, None, "
                f"json={example.json!r}, expect_status={config.anon_statuses[0]}"
                + (f", expect_db={{{_table_word(spec, example.path)}: "
                   "{\"count\": <rows before>}}" if method in
                   WRITE_METHODS else "") + ") so an endpoint that forgets "
                "authentication fails.",
            ))  # fmt: skip
        signed = [(sc, s) for sc, _, s in uses if s.user is not None]
        if (not outsiders or "{}" not in template or not signed
                or (outsider_rx is not None and not outsider_rx.search(template))):  # fmt: skip
            continue
        if any(s.expect_status in config.outsider_statuses for _, s in signed):
            continue
        status = config.outsider_statuses[0]
        sc, s = signed[0]
        out.append(LintFinding(
            "L006", None, f"endpoint:{ep}",
            f"the rules name users who are not members, but no signed-in step on {ep} "
            f"expects {status}",
            f"Add a scenario where a user who is not a member of the resource calls "
            f"Step({method!r}, {s.path!r}, <outsider handle>, json={s.json!r}, "
            f"expect_status={status}"
            + (f", expect_db={{{_table_word(spec, s.path)}: "
               "{\"count\": <rows before>}}" if method in
               WRITE_METHODS else "")
            + "); give the outsider a handle in Spec.users if it has none.",
        ))  # fmt: skip
    return out


# ---- L007 --------------------------------------------------------------------------------


def _invalid_steps(spec: Spec, config: LintConfig) -> list[Step]:
    return [
        s
        for sc in spec.scenarios
        for s in sc.steps
        if isinstance(s, Step) and s.expect_status in config.invalid_statuses
    ]


def _number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _l007(spec: Spec, trace: Trace, config: LintConfig) -> list[LintFinding]:
    out: list[LintFinding] = []
    tables, _ = tables_of(spec.profile)
    all_steps = [s for sc in spec.scenarios for s in sc.steps if isinstance(s, Step)]
    sent: dict[str, Step] = {}
    for s in all_steps:
        for k, _ in _payload(s):
            if k is not None:
                sent.setdefault(k, s)
    invalid = _invalid_steps(spec, config)
    params = [p for c in spec.contracts for p in c.params]
    status = config.invalid_statuses[-1]
    for tname, schema in tables.items():
        for column, values in schema.one_of:
            if column not in sent:
                continue
            if any(p.one_of and (p.name.split(".")[-1] == column
                                 or (p.column or "").endswith(f".{column}"))
                   for p in params):  # fmt: skip
                continue
            covered = any(
                k == column and (not isinstance(v, str) or v not in values)
                for s in invalid
                for k, v in _payload(s)
            )
            if covered:
                continue
            ex = sent[column]
            out.append(LintFinding(
                "L007", None, f"profile:{tname}.{column}",
                f"{tname}.{column} is one of {', '.join(values)}, and requests send it, "
                "but no step sends a value outside it expecting "
                f"{' or '.join(map(str, config.invalid_statuses))}",
                f"Add a step like Step({ex.method!r}, {ex.path!r}, {ex.user!r}, json=<the "
                f"same body with {column!r}: \"not-a-{column}\">, expect_status={status}) "
                "with expect_db={<table>: {\"count\": <rows before>}}.",
            ))  # fmt: skip
    context = set(trace.context) | set(trace.unchecked)
    for rule in trace.rules:
        if rule.id in context or not trace.links.get(rule.id):
            continue
        for lo, hi, unit in _ranges(rule.text):
            if _range_covered(lo, hi, unit, invalid, params):
                continue
            what = f"{lo}-{hi}{' ' + unit if unit else ''}"
            kind = _unit_kind(unit)
            sample = {"text": f"a text of {hi + 1} {unit or 'characters'}",
                      "items": f"a list of {hi + 1} items",
                      "number": f"{hi + 1}"}[kind]  # fmt: skip
            out.append(LintFinding(
                "L007", rule.id, None,
                f"{rule.id} ({_quote(rule.text)}) states the range {what}, but no step "
                "sends a value outside it expecting "
                f"{' or '.join(map(str, config.invalid_statuses))}",
                f"Add a step for {rule.id} that sends {sample}"
                + (f" (or {lo - 1})" if kind == "number" else
                   f" (or one of {lo - 1})" if lo > 0 else "")
                + f" in the field it limits, expecting expect_status={status} with "
                "expect_db={<table>: {\"count\": <rows before>}}, and link that scenario "
                f"to {rule.id}.",
            ))  # fmt: skip
    return out


def _unit_kind(unit: str | None) -> str:
    if unit and unit.lower() in _TEXT_UNITS:
        return "text"
    if unit and unit.lower() in _ITEM_UNITS:
        return "items"
    return "number"


def _ranges(text: str) -> list[tuple[int, int, str | None]]:
    out = []
    for rx in (_DASH_RANGE, _BETWEEN):
        for m in rx.finditer(text):
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo < hi:
                out.append((lo, hi, m.group(3).lower() if m.group(3) else None))
    return list(dict.fromkeys(out))


def _range_covered(lo: int, hi: int, unit: str | None, invalid: Sequence[Step],
                   params: Sequence[Any]) -> bool:  # fmt: skip
    kind = _unit_kind(unit)
    for p in params:
        if kind == "text" and (p.min_len == lo or p.max_len == hi):
            return True
        if kind == "number" and (lo in (p.ge, p.gt) or hi in (p.le, p.lt)):
            return True
    for s in invalid:
        for _, v in _payload(s):
            if kind == "text" and isinstance(v, str):
                n = len(v.encode()) if unit and unit.startswith("byte") else len(v)
                if n < lo or n > hi:
                    return True
            elif kind == "items" and isinstance(v, list) and (len(v) < lo or len(v) > hi):
                return True
            elif kind == "number" and _number(v) and (v < lo or v > hi):
                return True
    return False


# ---- L008, L010 --------------------------------------------------------------------------


def _l008(spec: Spec, trace: Trace) -> list[LintFinding]:
    scenarios = {s.name: s for s in spec.scenarios}
    out = []
    for rule in trace.rules:
        targets = trace.links.get(rule.id) or []
        if rule.id in trace.unchecked or rule.id in trace.context or not targets:
            continue
        m = _BACKGROUND.search(rule.text)
        if m is None:
            continue
        parsed = [_parse_target(t) for t in targets]
        if any(k == "service" for k, _ in parsed):
            continue
        if any(isinstance(st, Await) for k, n in parsed if k == "scenario" and n in scenarios
               for st in scenarios[n].steps):  # fmt: skip
            continue
        svc = spec.service_names()
        name = svc[0] if svc else "<service>"
        out.append(LintFinding(
            "L008", rule.id, None,
            f"{rule.id} ({_quote(rule.text)}) mentions {m.group(1)!r} but is linked to no "
            "Await, no service and no unchecked note",
            f"Add to a scenario linked to {rule.id} an Await({name!r}, expect_db={{<table>: "
            "{\"where\": {...}, \"equals\": {...}}}, within_s=<seconds>) after the steps "
            "that set it up"
            + ("" if svc else ", declaring the service in Spec(services=...)")
            + f"; or, if it cannot be checked, move {rule.id} to the trace's \"unchecked\" "
            "with the reason.",
        ))  # fmt: skip
    return out


def _l010(trace: Trace) -> list[LintFinding]:
    ids = {r.id for r in trace.rules}
    out = []
    for q in trace.questions:
        if q.answer is None and not (q.default or "").strip():
            out.append(LintFinding(
                "L010", q.rules[0] if q.rules else None, f"question:{q.id}",
                f"question {q.id} ({_quote(q.text)}) has no default",
                f"Give question {q.id} a \"default\": the answer the spec assumes until the "
                "owner answers, and make the spec follow it.",
            ))  # fmt: skip
        unknown = [r for r in q.rules if r not in ids]
        if not q.rules or unknown:
            what = (f"names {', '.join(unknown)}, not rules of the trace" if unknown
                    else "names no rules")  # fmt: skip
            out.append(LintFinding(
                "L010", None, f"question:{q.id}",
                f"question {q.id} ({_quote(q.text)}) {what}",
                f"Set question {q.id}'s \"rules\" to the ids (from the trace's \"rules\") "
                "of the rules it asks about.",
            ))  # fmt: skip
    return out


# ---- L011 ----------------------------------------------------------------------------------

ACCESS_STATUSES = frozenset({401, 403})


@dataclass(frozen=True)
class Conflict:
    """Two steps (``scenario``, 1-based ``step``) expecting different statuses for the
    same request after the same setup (L011)."""

    scenario_a: str
    step_a: int
    status_a: int
    scenario_b: str
    step_b: int
    status_b: int
    endpoint: str  # METHOD /template
    user: str | None

    @property
    def targets(self) -> tuple[str, str]:
        return f"scenario:{self.scenario_a}", f"scenario:{self.scenario_b}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value or None, sort_keys=True, default=str)


def _setup_steps(spec: Spec) -> list[tuple[Scenario, int, Step, str]]:
    """Every step that runs on its scenario's setup alone and is the first on its
    template there: (scenario, step number, step, template)."""
    contracts = _contract_templates(spec)
    out = []
    for scenario in spec.scenarios:
        seen: set[tuple[Any, ...]] = set()
        for i, step in _steps(scenario):
            if isinstance(step, Await):
                break
            key = (step.user, step.method.upper(), template_of(step.path, contracts))
            if key not in seen:
                seen.add(key)
                out.append((scenario, i, step, key[2]))
            if _is_write(step) and _ok(step.expect_status):
                break  # every later step runs on a changed state
    return out


def conflicts(spec: Spec, config: LintConfig | None = None) -> list[Conflict]:
    """The L011 pairs of `spec` (see the module docstring), in scenario order."""
    public_rx = re.compile((config or LintConfig()).public_paths, re.I)
    groups: dict[tuple[Any, ...], list[tuple[Scenario, int, Step]]] = {}
    for scenario, i, step, template in _setup_steps(spec):
        key = (_canonical_json(scenario.given), step.user, step.method.upper(), template,
               step.path.split("?")[0])  # fmt: skip
        groups.setdefault(key, []).append((scenario, i, step))
    out: list[Conflict] = []
    for key, steps in groups.items():
        for n, (sa, ia, a) in enumerate(steps):
            for sb, ib, b in steps[n + 1:]:
                if sa.name == sb.name or a.expect_status == b.expect_status:
                    continue
                statuses = {a.expect_status, b.expect_status}
                # an access answer (401/403 vs 2xx) conflicts whatever the bodies -- except
                # on a public endpoint (a login answers good and bad credentials differently)
                access = (not public_rx.search(key[3]) and bool(statuses & ACCESS_STATUSES)
                          and any(_ok(x) for x in statuses))
                same_body = (_canonical_json(a.json) == _canonical_json(b.json)
                             and _canonical_json(a.params) == _canonical_json(b.params))
                if not (same_body or access):
                    continue
                out.append(Conflict(sa.name, ia, a.expect_status, sb.name, ib, b.expect_status,
                                    f"{key[2]} {key[3]}", a.user))  # fmt: skip
    return out


def _l011(spec: Spec, trace: Trace) -> list[LintFinding]:
    by_element = _rules_by_element(trace)
    out = []
    for c in conflicts(spec):
        ta, tb = c.targets
        rules_a = by_element.get(ta, [])
        rules_b = by_element.get(tb, [])
        who = c.user or "no token"
        says_a = f" ({', '.join(rules_a)})" if rules_a else ""
        says_b = f" ({', '.join(rules_b)})" if rules_b else ""
        out.append(LintFinding(
            "L011", (rules_a or rules_b or [None])[0], ta,
            f"{ta} step {c.step_a} expects {c.status_a} but {tb} step {c.step_b} expects "
            f"{c.status_b} for the same request ({c.endpoint} as {who}) after the same "
            "setup: both cannot hold",
            f"Which holds? {c.endpoint} as {who} answers {c.status_a}{says_a}, or "
            f"{c.status_b}{says_b}? Keep the scenario that states the answer the owner "
            f"confirms and delete or rewrite the other ({ta} or {tb}), and fix the rule "
            "that says otherwise.",
        ))  # fmt: skip
    return out


# ---- L012 ----------------------------------------------------------------------------------


def _l012(spec: Spec, trace: Trace) -> list[LintFinding]:
    """Steps expecting no row of a table where W after an earlier step expected one (L012)."""
    by_element = _rules_by_element(trace)
    out = []
    for sc in spec.scenarios:
        target = f"scenario:{sc.name}"
        rules = by_element.get(target, [])
        for c in contradictions(sc):
            where = f" where {c.where}" if c.where else ""
            out.append(LintFinding(
                "L012", rules[0] if len(rules) == 1 else None, target,
                f"scenario {sc.name!r}: step {c.step_b} expects no row of {c.table}{where}, "
                f"but step {c.step_a} expected one and nothing in between can remove it",
                f"Make step {c.step_b} look at a different row (a fresh value in its "
                f"request and in its where), or expect the row of step {c.step_a} to be "
                "unchanged: a refused request leaves what was there.",
            ))  # fmt: skip
    return out


# ---- L009: vacuity -------------------------------------------------------------------------


def stub_app(status: int) -> Any:
    """A Starlette app answering every method and path ``status`` with ``{}``."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def answer(request: Any) -> Any:
        return JSONResponse({} if status < 500 else {"detail": "stub"}, status_code=status)

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    return Starlette(routes=[Route("/{path:path}", answer, methods=methods)])


def _stub_login(client: Any, handle: str) -> str:
    return f"lint-stub-token-{handle}"


STUBS = (("all-200", 200, "every request answers 200 {}"),
         ("all-500", 500, "every request answers 500"))  # fmt: skip


def _l009(spec: Spec, trace: Trace, vacuity: VacuityConfig, notes: list[str]
          ) -> list[LintFinding]:  # fmt: skip
    from lattice.spec.check import CheckError, check
    from lattice.spec.services import ServiceCmd

    if not spec.scenarios:
        return []
    cap = vacuity.await_cap_s
    capped = replace(spec, scenarios=tuple(
        replace(sc, steps=tuple(
            replace(st, within_s=min(st.within_s, cap)) if isinstance(st, Await) else st
            for st in sc.steps))
        for sc in spec.scenarios))  # fmt: skip
    names = set(spec.service_names()) | {
        st.service for sc in spec.scenarios for st in sc.steps if isinstance(st, Await)
    }
    noop = {n: (lambda db: ServiceCmd([sys.executable, "-c", ""])) for n in names}
    passed: dict[str, list[str]] = {}
    for label, status, _ in STUBS:
        app = stub_app(status)
        try:
            report = check(capped, lambda db, app=app: app, vacuity.db_factory, layers="D",
                           login=vacuity.login or _stub_login, services=noop)  # fmt: skip
        except CheckError as err:
            notes.append(f"L009 skipped: the check cannot run against the stubs: {err}")
            return []
        except Exception as err:  # noqa: BLE001 -- the lint must still answer
            notes.append(f"L009 skipped: the stub run failed: {type(err).__name__}: {err}")
            return []
        failed = {f.rule.removeprefix("scenario: ") for f in report.findings if f.layer == "D"}
        for sc in spec.scenarios:
            if sc.name not in failed:
                passed.setdefault(sc.name, []).append(label)
    by_element = _rules_by_element(trace)
    out = []
    for sc in spec.scenarios:
        stubs = passed.get(sc.name)
        if not stubs:
            continue
        how = "; ".join(f"{lab} ({d})" for lab, _, d in STUBS if lab in stubs)
        rules = by_element.get(f"scenario:{sc.name}", [])
        tail = f" -- it is what {', '.join(rules)} rest on" if rules else ""
        out.append(LintFinding(
            "L009", rules[0] if len(rules) == 1 else None, f"scenario:{sc.name}",
            f"scenario {sc.name!r} passes against a stub app that implements nothing: "
            f"{how}{tail}",
            f"Give scenario {sc.name!r} a step a do-nothing app fails: an expect_body with "
            "the fields the real answer carries (e.g. {\"id\": 1, ...}), an expect_db the "
            "request changes, or the non-200 status the rule requires"
            + (" (and an expect_status that is not 500)" if "all-500" in stubs else "")
            + ".",
        ))  # fmt: skip
    return out


# ---- lint ----------------------------------------------------------------------------------


def lint(
    spec: Spec,
    trace: Trace,
    *,
    vacuity: VacuityConfig | None = None,
    config: LintConfig | None = None,
    notes: list[str] | None = None,
) -> list[LintFinding]:
    """The gaps between `spec` and the owner's rules in `trace` (see the module
    docstring), ordered by code. `vacuity`: run L009 against the stub apps (skipped,
    with a note, without it). `notes` (optional): a list the lint appends its notes to."""
    config = config or LintConfig()
    notes = notes if notes is not None else []
    found: list[LintFinding] = []
    found += _l001(trace)
    found += _l002(spec, trace)
    found += _l003(spec, trace)
    found += _l004_l005(spec, trace)
    found += _l006(spec, trace, config)
    found += _l007(spec, trace, config)
    found += _l008(spec, trace)
    if vacuity is None:
        notes.append("L009 skipped: no VacuityConfig (give the db factory: --db MOD:fn)")
    else:
        found += _l009(spec, trace, vacuity, notes)
    found += _l010(trace)
    found += _l011(spec, trace)
    found += _l012(spec, trace)
    return sorted(found, key=lambda f: f.code)


def _quote(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return repr(text if len(text) <= limit else text[: limit - 3] + "...")


# ---- CLI -----------------------------------------------------------------------------------


class LintError(Exception):
    """The lint cannot run (exit code 2)."""


def _load_ref(ref: str) -> Any:
    module, sep, attr = ref.partition(":")
    if not sep or not module or not attr:
        raise LintError(f"{ref!r}: expected module:attribute")
    obj: Any = importlib.import_module(module)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _load_spec(ref: str) -> tuple[Spec, Path | None]:
    """A Spec from ``module:ATTR`` or a module file (its ``SPEC``), and the file."""
    path = Path(ref.split(":", 1)[0]) if ref.split(":", 1)[0].endswith(".py") else None
    if path is not None:
        attr = ref.split(":", 1)[1] if ":" in ref else "SPEC"
        spec_ = importlib.util.spec_from_file_location(path.stem, path)
        if spec_ is None or spec_.loader is None or not path.exists():
            raise LintError(f"{path}: cannot load the spec module")
        module = importlib.util.module_from_spec(spec_)
        sys.modules.setdefault(path.stem, module)
        spec_.loader.exec_module(module)
        obj: Any = getattr(module, attr)
        file = path
    else:
        obj = _load_ref(ref)
        mod = sys.modules.get(ref.partition(":")[0])
        file = Path(mod.__file__) if mod is not None and getattr(mod, "__file__", None) else None
    if callable(obj) and not isinstance(obj, Spec):
        obj = obj()
    if not isinstance(obj, Spec):
        raise LintError(f"{ref!r} is not a Spec (got {type(obj).__name__})")
    return obj, file


def render(findings: Sequence[LintFinding], notes: Sequence[str] = ()) -> str:
    """The findings grouped by code, each with its fix; then the notes."""
    lines: list[str] = []
    for code in CODES:
        group = [f for f in findings if f.code == code]
        if not group:
            continue
        lines.append(f"{code} {CODES[code]} ({len(group)})")
        for f in group:
            where = " ".join(x for x in (f.rule_id, f.element) if x)
            lines.append(f"  - [{where}] {f.message}" if where else f"  - {f.message}")
            lines.append(f"    fix: {f.fix}")
    lines += [f"note: {n}" for n in notes]
    lines.append(f"{len(findings)} finding(s)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """``lint --spec MOD:SPEC --trace PATH [--db MOD:fn --login MOD:fn] [--json]``."""
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["lint"]:
        args_in = args_in[1:]
    parser = argparse.ArgumentParser(prog="python -m lattice.spec lint")
    parser.add_argument("--spec", required=True, help="module:SPEC, or a spec module file")
    parser.add_argument("--trace", help="the trace JSON (default: <spec module>.trace.json)")
    parser.add_argument("--db", help="module:fn, the seeded db factory (enables L009)")
    parser.add_argument("--login", help="module:fn, the stub's login hook (default: dummy)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(args_in)
    try:
        spec, file = _load_spec(args.spec)
        tpath = Path(args.trace) if args.trace else (trace_path(file) if file else None)
        if tpath is None or not tpath.exists():
            raise LintError(f"no trace: give --trace (looked for {tpath})")
        trace = Trace.load(tpath)
        vacuity = None
        if args.db:
            login = _load_ref(args.login) if args.login else None
            vacuity = VacuityConfig(_load_ref(args.db), login)
        notes: list[str] = []
        findings = lint(spec, trace, vacuity=vacuity, notes=notes)
    except (LintError, ImportError, AttributeError, OSError, ValueError, KeyError) as err:
        print(f"lint: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"findings": [f.to_dict() for f in findings], "notes": notes},
                         indent=2, ensure_ascii=False))  # fmt: skip
    else:
        print(render(findings, notes))
    return 1 if findings else 0


__all__ = [
    "CODES",
    "Conflict",
    "LintConfig",
    "LintError",
    "LintFinding",
    "VacuityConfig",
    "conflicts",
    "lint",
    "main",
    "render",
    "stub_app",
    "template_of",
]
