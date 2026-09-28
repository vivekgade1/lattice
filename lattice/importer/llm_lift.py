"""The close-the-gap loop (spec 2026-09-27 importer §3.2 2b and §3.4, plan node I5): every
route the deterministic lifters did not verify climbs a ladder of LLM rungs, each gated
by the same verification as the lifters' output (:func:`lattice.importer.lift.
verify_candidate`).

* **R0** (no LLM): the deterministic lifters' output, verified -> Lattice.
* **R1 repair**: the LLM fixes the lifted Lattice from its verification diff and checker
  diagnostics, in one conversation, up to ``repair_rounds`` rounds; a route no lifter
  matched first gets one lift round (a fresh write) -> Lattice.
* **R2 re-lift**: the LLM writes the route again from scratch, given the diff history, on
  the strong generator (a stronger model or higher effort), up to ``relift_attempts``
  fresh conversations -> Lattice.
* **R3 carve-out** (:mod:`lattice.importer.carve`): the strategy's plans, in order --
  by default first **T2**: the route as T0 plus ``@procedure`` functions holding the logic T0
  cannot express, translated by the LLM from the original route or helper code within
  the T2 subset (helpers already in the subset are carved mechanically and added to a
  candidate that calls them); then, only when a helper needs something outside the T2
  subset, a **typed native** over it -> Lattice + procedures (tier T2) or + natives
  (T3); skipped with the reason when the strategy offers no plan.
* **R4 island**: the whole route stays the original code, with the reason and a
  classified language gap (counted as not reaching 100%).

**Soundness.** A candidate is accepted only when it passes the route's differential
(and the checker, and branch coverage) -- exactly the gate the lifters' output passed.
Before verification it must be one ``@endpoint`` named after the route with the route's
method and path, plus constants or ``@step`` helpers -- and at R3 ``@procedure`` functions,
``@record`` classes and imports, which the T2 checker holds to its subset (nothing else is
emitted with it).
After the loop the verified routes are joined per router file and checked again against
the final profile; a route whose file is refused becomes an island. Nothing unverified is
ever emitted as Lattice.

**Short cuts.** A route with no request suite is an island at once (nothing could verify
it). A route whose original has a ``raise`` site no case reaches can never verify (the
coverage gap is the suite's, not the candidate's): the ladder stops there. A route whose
code-graph slice has dynamic-dispatch flags goes straight to R3 (spec §3.6: not guessed).

**Budget.** :class:`Budget` caps LLM rounds and cost per route and per repo; it is checked
before every call (a call's cost is known only after it, so a cap can be overshot by one
call). A route blocked by its own cap is an island with reason ``budget``; when the
repo's cap is reached every remaining route is too, and the output is still complete and
consistent (:mod:`lattice.importer.report`).

**Context.** Each prompt is assembled per method arm (:mod:`lattice.importer.context`):
the system prompt is the Lattice API guide with an importer section and the generated
profile; the user message holds the route's identity, the arm's evidence layers, the
lifter output and the diff history.

Every round is appended to the corpus log (``corpus/imports.jsonl``, gitignored) and,
with ``artefacts``, saved (prompt, response, candidate, verdict).
"""

from __future__ import annotations

import ast
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lattice.catalog import T2_STDLIB
from lattice.importer.carve import (
    CarvedProcedure,
    CarveNative,
    CarveOut,
    CarvePlan,
    PlannedCarveOut,
    ProcedureCarveOut,
    carve_profile_source,
    program_carve,
    used_natives,
    with_procedures,
)
from lattice.importer.context import Evidence, assemble
from lattice.importer.graph import route_slice, slice_source
from lattice.importer.intent import IntentNotes, NotesDisagreement, disagreements
from lattice.importer.lift import (
    HEADER,
    ImportResult,
    LiftResult,
    check_program,
    load_profile,
    program_source,
    verify_candidate,
)
from lattice.importer.profile_gen import LANGUAGE_GAP
from lattice.llm.client import Generation, GenerationRefused, Generator, Usage, extract_code

__all__ = [
    "Attempt",
    "Budget",
    "COMPACT",
    "CloseResult",
    "CorpusLog",
    "LoopConfig",
    "ProcedureInfo",
    "RouteOutcome",
    "close_gap",
    "no_llm",
    "parse_candidate",
    "route_tier",
    "system_prompt",
]

Rung = Literal["R0", "R1", "R2", "R3", "R4", "needs-llm"]
Status = Literal["verified", "island", "needs-llm"]
DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "imports.jsonl"
TRUNCATED = (
    "Your answer was cut off at the output limit before the code block ended. Return the "
    "complete endpoint again, shorter, in one ```python block."
)
# After a truncated answer the next call asks for less (never the same settings again): a
# fresh conversation with this instruction; a truncated answer to it ends the rung.
COMPACT = (
    "Return only the endpoint: one ```python block holding the `@endpoint` function and "
    "only the constants or helpers it calls. No explanation, no alternatives, no "
    "comments; keep your reasoning brief."
)
MAX_MISMATCHES_SHOWN = 8
MAX_DIFF_CHARS = 700
MAX_HISTORY = 6

# ---------------------------------------------------------------------------- prompts --

IMPORT_SECTION = """## Lifting a route of an imported app

You are importing one route of an existing FastAPI + SQLModel app into Lattice. The \
profile module below was generated from the app: its tables (`Schema.from_pairs`: \
columns, key, nullable columns, owner column, private columns), its response models \
(`RESPONSES`), its natives and its conventions. This app's conventions replace the \
generic ones above:

- `@endpoint(..., response="UserPublic")` names the response model: the returned columns \
must be exactly its fields (`select` them). A `Page` answers `{"data": [...], "count": \
n}`; `page(..., total=True)` gives `count`.
- Errors answer `{"detail": "<message>"}` with the message given to `NotFound(...)`, \
`Forbidden(...)`, `Conflict(...)` or `BadRequest(...)`; 401 and 422 are automatic. Use \
the original's exact messages and statuses.
- Parameter types also include `Uuid`, `Email` and `DateTime`.
- `p: Str(...) = UNSET` is an optional body field: not sent, the column keeps its value. \
It may be used only directly as an `update()` value or an `insert()` value, or in \
`sent(p)`.
- `require(t, owner_is(user), else_=Forbidden("..."))`: every row of t belongs to the \
caller (Admin callers pass).
- `native.<name>(...)` calls a function the profile declares in `natives`.

Your endpoint replaces the original route: on every request of a generated suite it \
must answer the same status and JSON body, and leave the same database rows, as the \
original. Declare exactly the errors the original answers.

Answer with one ```python block holding one `@endpoint` function (named as asked, with \
the route's method and path), plus any constants or `@step` functions it needs; no \
imports, no other endpoints, no prose outside the block."""


def system_prompt(profile_source: str, *, hints: bool = True) -> str:
    """The Lattice API guide with its output section replaced by the importer's, and the
    app's generated profile."""
    from lattice.llm.api_prompt import api_lattice_system_prompt
    from lattice.llm.prompt import _OUTPUT_FORMAT_SECTION

    guide = api_lattice_system_prompt(hints=hints)
    if _OUTPUT_FORMAT_SECTION not in guide:
        raise AssertionError("the Lattice guide's output section moved")
    profile = f"## The app's profile (`profile.py`)\n\n```python\n{profile_source.rstrip()}\n```"
    body = guide.replace(_OUTPUT_FORMAT_SECTION, IMPORT_SECTION).rstrip("\n")
    return f"{body}\n\n{profile}\n"


T2_CARVE = f"""Earlier attempts at this route failed (see above): Lattice's declarative \
operations alone did not reproduce it. Carve it out with T2: keep the route an \
`@endpoint` in T0 and move only the logic T0 cannot express into one or more \
`@procedure` functions, translated from the original route or helper code above \
within the T2 subset.

The T2 subset: ordinary Python with annotated parameters and result (`Int Float Str \
Bool Date DateTime Duration Uuid`, `T | None`, `list[T]`, `dict[K, V]`, `tuple[...]`, \
`Row[Table]` for a row, `Caller` for the caller -- `caller.id`, `caller.is_admin`, \
`caller.role` -- and `@record` classes of annotated fields); locals, `if`/`elif`/`else`, \
`for` over finite values, bounded `while`, comprehensions, f-strings, `try`/`except` \
of specific errors; imports only from {", ".join(sorted(T2_STDLIB))}; no I/O, no \
database access (the endpoint reads with T0 and passes the rows in), no mutation of \
parameters. An endpoint calls a procedure row by row as the whole value of a \
`derive` column, a `where` predicate or a `require` condition \
(`derive(t, x=proc(col("a"), user))`, `require(t, proc(row(), user), else_=...)`), \
whole-value with `x = call(proc, rows=t, ...)` on a bounded table, writes its rows \
with `insert_many(T, rows=x)`, and runs an effectful one with `for_each`. Ownership \
checks stay in T0 (`owner_is`, `accessible`, `mine`)."""
T2_ANSWER = (
    "Answer with one ```python block: the `@endpoint` (named as asked, with the route's "
    "method and path) plus the `@procedure`s, `@record`s, allowlisted imports and "
    "constants it needs; no prose outside the block."
)


def _repair_message(feedback: str) -> str:
    return f"{feedback}\n\nReturn the complete corrected endpoint in one ```python block."


# ------------------------------------------------------------------------- candidates --


def _decorator_name(d: ast.expr) -> str | None:
    f = d.func if isinstance(d, ast.Call) else d
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def parse_candidate(
    text: str, name: str, method: str, path: str, *, t2: bool = False
) -> tuple[str | None, str]:
    """The endpoint source of an answer (header comments dropped), or None and the
    problem: no code block, a syntax error, or anything but one ``@endpoint`` ``name``
    for ``method path`` plus constants and ``@step`` functions -- and, with ``t2`` (the
    R3 carve-out), ``@procedure`` functions, ``@record`` classes and imports (the T2
    checker holds them to the subset and the import allowlist)."""
    code = extract_code(text)
    if code is None:
        return None, "The answer has no ```python code block."
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return None, f"The code does not parse: {exc}"
    endpoints: list[ast.FunctionDef] = []
    problems: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            names = [_decorator_name(d) for d in node.decorator_list]
            if "endpoint" in names:
                endpoints.append(node)
            elif t2 and "procedure" in names:
                continue
            elif "step" not in names:
                problems.append(f"line {node.lineno}: `{node.name}` is neither @endpoint nor @step")
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not all(isinstance(t, ast.Name) for t in targets):
                problems.append(f"line {node.lineno}: only simple constants are allowed")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        elif (
            t2
            and isinstance(node, ast.ClassDef)
            and "record" in [_decorator_name(d) for d in node.decorator_list]
        ):
            continue
        elif t2 and isinstance(node, ast.Import | ast.ImportFrom):
            continue
        else:
            problems.append(f"line {node.lineno}: {type(node).__name__} is not allowed here")
    if len(endpoints) != 1:
        problems.append(f"expected exactly one @endpoint, found {len(endpoints)}")
    else:
        ep = endpoints[0]
        if ep.name != name:
            problems.append(f"the endpoint must be named `{name}`, not `{ep.name}`")
        deco = next(d for d in ep.decorator_list if _decorator_name(d) == "endpoint")
        args = deco.args if isinstance(deco, ast.Call) else []
        got = [a.value if isinstance(a, ast.Constant) else None for a in args[:2]]
        if got != [method, path]:
            problems.append(f'the endpoint must be @endpoint("{method}", "{path}", ...)')
    if problems:
        return None, "The answer is not a single endpoint for this route: " + "; ".join(problems)
    lines = code.replace("\r\n", "\n").split("\n")
    while lines and (lines[0].startswith("# lattice:") or lines[0].startswith("# ruff:")
                     or not lines[0].strip()):  # fmt: skip
        lines.pop(0)
    return "\n".join(lines).strip("\n") + "\n", ""


def _case_text(c: Any) -> str:
    bits = [f"{c.method} {c.path}"]
    if c.user:
        bits.append(f"as {c.user}")
    elif c.token:
        bits.append("with a raw token")
    else:
        bits.append("anonymous")
    if c.params:
        bits.append(f"params={json.dumps(c.params, default=str)}")
    if c.json is not None:
        bits.append(f"json={json.dumps(c.json, default=str)}")
    if c.form:
        bits.append(f"form={json.dumps(c.form, default=str)}")
    return " ".join(bits)


def feedback_for(v: LiftResult, cases: Mapping[str, Any]) -> str:
    """The evidence a failed candidate gives the next round: checker diagnostics, or the
    mismatching cases with their status/body/database diffs, and unreached branches."""
    if v.diagnostics:
        return "The Lattice checker refused the program:\n" + "\n".join(v.diagnostics)
    if v.verify is None:
        return v.reason or "The candidate was not verified."
    lines = [
        "Verification against the original failed: on every case the endpoint must "
        "answer the original's status and JSON body and leave the same database rows."
    ]
    mismatches = list(v.verify.mismatches.items())
    for name, o in mismatches[:MAX_MISMATCHES_SHOWN]:
        c = cases.get(name)
        head = f"- case `{name}`" + (f" ({_case_text(c)})" if c is not None else "")
        diff = o.diff.render() if o.diff is not None else ""
        diff = diff if len(diff) <= MAX_DIFF_CHARS else diff[:MAX_DIFF_CHARS] + " ..."
        diff = diff.replace("\n", "\n    ")
        lines.append(
            f"{head}: {o.reason}" + (f"\n    (original != lattice) {diff}" if diff else "")
        )
    if len(mismatches) > MAX_MISMATCHES_SHOWN:
        lines.append(f"- ... and {len(mismatches) - MAX_MISMATCHES_SHOWN} more mismatching cases")
    lines += [f"- a declared deviation names no case: {d}" for d in v.verify.unknown_deviations]
    lines += [
        f"- the original's branch {s.render()} was reached by no case"
        for _, s in v.verify.unverified
    ]
    return "\n".join(lines)


def _suite_gap(v: LiftResult) -> bool:
    """Only unreached branches fail: no candidate can fix that."""
    return (
        v.verify is not None
        and not v.diagnostics
        and not v.verify.mismatches
        and not v.verify.unknown_deviations
        and bool(v.verify.unverified)
    )


# ---------------------------------------------------------------------------- records --


@dataclass
class Attempt:
    """One LLM round. ``outcome``: verified | checker | mismatch | no_code | invalid |
    truncated | refused | error | suite_gap | skipped."""

    route: str
    rung: str
    round: int
    kind: str  # lift | repair | relift | carve-procedure | carve-native
    model: str
    outcome: str
    stop_reason: str | None = None
    reason: str = ""
    feedback: str = ""
    source: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    tally: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "rung": self.rung,
            "round": self.round,
            "kind": self.kind,
            "model": self.model,
            "outcome": self.outcome,
            "stop_reason": self.stop_reason,
            "reason": self.reason,
            "cost_usd": round(self.cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_s": round(self.latency_s, 3),
            "tally": list(self.tally) if self.tally else None,
        }


@dataclass(frozen=True)
class ProcedureInfo:
    """A ``@procedure`` of a verified route: ``origin`` is the helper it was carved from
    mechanically, None when the LLM wrote it."""

    name: str
    lines: int
    origin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "lines": self.lines, "origin": self.origin}


def procedure_infos(
    source: str, carved: Sequence[CarvedProcedure] = ()
) -> tuple[ProcedureInfo, ...]:
    """The procedures of a Lattice source (a mechanically carved one keeps its
    origin when its text is unchanged)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    mech = {p.name: p for p in carved}
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and "procedure" in [
            _decorator_name(d) for d in node.decorator_list
        ]:
            first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            lines = (node.end_lineno or node.lineno) - first + 1
            p = mech.get(node.name)
            origin = p.origin if p is not None and ast.dump(ast.parse(p.source).body[0]) == (
                ast.dump(node)
            ) else None  # fmt: skip
            out.append(ProcedureInfo(node.name, lines, origin))
    return tuple(out)


T1_OPS = frozenset({"window", "traverse", "iterate", "for_each"})


def route_tier(o: RouteOutcome) -> str | None:
    """The route's tier (spec 1c §1): ``T3`` when it calls carve-out natives, ``T2``
    with procedures, ``T1`` with a T1 op, else ``T0``; ``island`` for an island (R4);
    None when not attempted."""
    if o.status == "island":
        return "island"
    if o.status != "verified" or o.source is None:
        return None
    if o.natives:
        return "T3"
    if o.procedures or program_carve(o.source)[0]:
        return "T2"
    try:
        tree = ast.parse(o.source)
    except SyntaxError:
        return "T0"
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in T1_OPS
        ):
            return "T1"
    return "T0"


@dataclass
class RouteOutcome:
    """Where a route ended: ``status`` verified (``rung`` R0-R3), island (R4, with
    ``reason`` and ``gap``) or needs-llm (not attempted: ``--no-llm``). An R3 route's
    ``carve`` is ``procedure`` (T2: ``procedures``) or ``native`` (T3: ``natives``)."""

    key: str
    result: LiftResult
    status: Status
    rung: Rung
    source: str | None = None
    verified: LiftResult | None = None
    reason: str = ""
    gap: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    natives: tuple[CarveNative, ...] = ()
    notes: list[str] = field(default_factory=list)  # rung skips and other remarks
    carve: str = "none"  # procedure | native | none
    procedures: tuple[ProcedureInfo, ...] = ()

    @property
    def tier(self) -> str | None:
        return route_tier(self)

    @property
    def by(self) -> str | None:
        if self.status != "verified":
            return None
        return "lifter" if self.rung == "R0" else "llm"

    @property
    def route(self) -> Any:
        return self.result.route

    @property
    def cost_usd(self) -> float:
        return sum(a.cost_usd for a in self.attempts)

    @property
    def rounds(self) -> int:
        return sum(1 for a in self.attempts if a.outcome != "skipped")


class Budget:
    """Caps on LLM rounds and USD, per route and per repo (None: no cap)."""

    def __init__(
        self,
        *,
        max_rounds: int | None = None,
        max_cost_usd: float | None = None,
        max_route_rounds: int | None = None,
        max_route_cost_usd: float | None = None,
    ) -> None:
        self.max_rounds = max_rounds
        self.max_cost_usd = max_cost_usd
        self.max_route_rounds = max_route_rounds
        self.max_route_cost_usd = max_route_cost_usd
        self.rounds = 0
        self.cost_usd = 0.0
        self.route_rounds: dict[str, int] = {}
        self.route_cost: dict[str, float] = {}
        self.route_share: dict[str, tuple[float | None, int | None]] = {}
        self.exhausted_by: str | None = None

    def allot(self, key: str, remaining: int) -> None:
        """Give route ``key`` its share of the repo caps: what is left of them over the
        ``remaining`` routes still to run (this one included) -- so the repo budget is
        not spent first-come, and what a route leaves goes to the routes after it."""
        n = max(remaining, 1)
        cost = None
        if self.max_cost_usd is not None:
            cost = max(self.max_cost_usd - self.cost_usd, 0.0) / n
        rounds = None
        if self.max_rounds is not None:
            rounds = max((self.max_rounds - self.rounds) // n, 1)
        self.route_share[key] = (cost, rounds)

    def charge(self, key: str | None, cost: float) -> None:
        self.rounds += 1
        self.cost_usd += cost
        if key is not None:
            self.route_rounds[key] = self.route_rounds.get(key, 0) + 1
            self.route_cost[key] = self.route_cost.get(key, 0.0) + cost

    def repo_blocked(self) -> str | None:
        why = None
        if self.max_rounds is not None and self.rounds >= self.max_rounds:
            why = f"the repo's round cap ({self.max_rounds}) is reached"
        elif self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd:
            why = f"the repo's cost cap (${self.max_cost_usd:.2f}) is reached"
        if why is not None and self.exhausted_by is None:
            self.exhausted_by = why
        return why

    def blocked(self, key: str) -> str | None:
        repo = self.repo_blocked()
        if repo is not None:
            return repo
        if self.max_route_rounds is not None and self.route_rounds.get(key, 0) >= (
            self.max_route_rounds
        ):
            return f"the route's round cap ({self.max_route_rounds}) is reached"
        if self.max_route_cost_usd is not None and self.route_cost.get(key, 0.0) >= (
            self.max_route_cost_usd
        ):
            return f"the route's cost cap (${self.max_route_cost_usd:.2f}) is reached"
        cost, rounds = self.route_share.get(key, (None, None))
        if rounds is not None and self.route_rounds.get(key, 0) >= rounds:
            return f"the route's share of the repo budget ({rounds} rounds) is reached"
        if cost is not None and self.route_cost.get(key, 0.0) >= cost:
            return f"the route's share of the repo budget (${cost:.2f}) is reached"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_rounds": self.max_rounds,
            "max_cost_usd": self.max_cost_usd,
            "max_route_rounds": self.max_route_rounds,
            "max_route_cost_usd": self.max_route_cost_usd,
            "rounds": self.rounds,
            "cost_usd": round(self.cost_usd, 6),
            "exhausted": self.exhausted_by is not None,
            "exhausted_by": self.exhausted_by,
        }


class CorpusLog:
    """Appends JSON lines to ``path`` (spec §5: the raw material for later learning)."""

    def __init__(self, path: Path | str = DEFAULT_CORPUS, **common: Any) -> None:
        self.path = Path(path)
        self.common = common
        self.run = uuid.uuid4().hex[:12]

    def append(self, record: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "run": self.run,
                **self.common, **record}  # fmt: skip
        with self.path.open("a") as f:
            f.write(json.dumps(line, default=str) + "\n")


@dataclass
class LoopConfig:
    """``method``: the evidence arm (M1-M6); ``model``/``strong_model``: recorded names
    of the generators (R2 and R3 use the strong one); the caps (None: none) bound LLM
    rounds and USD per route and per repo."""

    method: str = "M2"
    model: str = "claude-opus-5"
    strong_model: str | None = None
    repair_rounds: int = 3
    relift_attempts: int = 2
    carve_attempts: int = 1
    max_route_rounds: int | None = None
    max_route_cost_usd: float | None = None
    max_repo_rounds: int | None = None
    max_repo_cost_usd: float | None = None
    hints: bool = True

    def budget(self) -> Budget:
        return Budget(
            max_rounds=self.max_repo_rounds,
            max_cost_usd=self.max_repo_cost_usd,
            max_route_rounds=self.max_route_rounds,
            max_route_cost_usd=self.max_route_cost_usd,
        )


@dataclass
class CloseResult:
    """The loop's outcome over every route of an import (``outcomes`` in route order),
    the emitted programs (router -> source, verified routes only), the profile source
    they check against, and the run's accounting."""

    result: ImportResult
    outcomes: list[RouteOutcome]
    programs: dict[str, str]
    profile_source: str
    llm: bool
    method: str | None = None
    model: str | None = None
    budget: Budget | None = None
    disagreements: list[NotesDisagreement] = field(default_factory=list)
    carve_natives: tuple[CarveNative, ...] = ()
    notes_cost_usd: float = 0.0
    wall_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def outcome(self, key: str) -> RouteOutcome:
        return next(o for o in self.outcomes if o.key == key)

    @property
    def verified(self) -> list[RouteOutcome]:
        return [o for o in self.outcomes if o.status == "verified"]

    @property
    def islands(self) -> list[RouteOutcome]:
        return [o for o in self.outcomes if o.status == "island"]

    @property
    def cost_usd(self) -> float:
        return sum(o.cost_usd for o in self.outcomes) + self.notes_cost_usd


# ------------------------------------------------------------------------------- gaps --

SCALAR_PARAM_TYPES = frozenset({"Str", "Email", "Int", "Float", "Bool", "Uuid", "DateTime", "Date"})


def classify_gap(o: RouteOutcome, dynamic: bool = False) -> str:
    """The language gap an island points at (spec §3.4 "Language gaps")."""
    reason = o.reason
    route = o.route
    if reason.startswith("budget"):
        return "budget"
    gap = re.search(LANGUAGE_GAP + r" \(([^)]*)\)", reason)
    if gap is not None:
        return f"{LANGUAGE_GAP}: {gap.group(1)}"
    if "no request suite" in reason:
        if "auth unclear" in reason or "no users table" in reason:
            return "unverifiable: auth unclear"
        if "cannot be seeded" in reason:
            return "unverifiable: a table cannot be seeded"
        if "defined inside" in reason:
            return "unsupported framework feature: a route defined inside an app factory"
        return "unverifiable: no request suite"
    if "reached by no case" in reason or "unverified branch" in reason:
        return "unverifiable: a raise site no case reaches"
    if dynamic:
        return "unsupported framework feature: dynamic dispatch"
    if route.response_class or route.status_code not in (None, 200, 201):
        return "unsupported framework feature: response class or status code"
    request = [p for p in route.params if p.source in ("path", "query", "body", "form")]
    if any(p.type is None for p in request):
        return "missing type"
    if "joined router" in reason:
        return "join: the router program is refused"
    last = next((a for a in reversed(o.attempts) if a.outcome != "skipped"), None)
    if last is not None and last.outcome in ("checker", "invalid", "no_code", "truncated"):
        return "missing construct"
    if last is not None and last.outcome == "mismatch":
        return "behaviour not reproduced"
    return "unclassified"


# ------------------------------------------------------------------------------- loop --


def _cost(model: str, usage: Usage) -> float:
    from bench.metrics import cost_usd

    return cost_usd(
        model,
        input_tokens=usage.total_input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        cache_creation_tokens=usage.cache_creation_input_tokens,
    )


class _Loop:
    def __init__(
        self,
        result: ImportResult,
        generator: Generator,
        config: LoopConfig,
        *,
        strong_generator: Generator | None,
        relift_generator: Generator | None = None,
        evidence: Evidence,
        carve: CarveOut | PlannedCarveOut | None,
        corpus: CorpusLog | None,
        artefacts: Path | None,
    ) -> None:
        assert result.context is not None
        self.result = result
        self.ctx = result.context
        self.generator = generator
        self.strong = strong_generator or generator
        self.relift = relift_generator or self.strong
        self.config = config
        self.evidence = evidence
        self.carve = carve
        self.corpus = corpus
        self.artefacts = artefacts
        self.budget = config.budget()
        self.system = system_prompt(result.profile.source, hints=config.hints)
        self.tokens = [0, 0]
        self.compact: set[str] = set()  # routes with a truncated answer: ask for less

    def _compact(self, key: str, ask: str) -> str:
        return f"{ask}\n\n{COMPACT}" if key in self.compact else ask

    def _cut_off(self, o: RouteOutcome, att: Attempt, compact: bool) -> bool:
        """After a truncated answer: True when the rung ends (it was already asked for
        less); else the route's next calls ask for only the endpoint."""
        if att.outcome != "truncated":
            return False
        if compact:
            return True
        self.compact.add(o.key)
        return False

    # -- one round --

    def round(
        self,
        o: RouteOutcome,
        rung: str,
        index: int,
        kind: str,
        generator: Generator,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        *,
        profile: Any = None,
        natives: dict[str, Callable[..., Any]] | None = None,
        t2: bool = False,
        procedures: Sequence[CarvedProcedure] = (),
    ) -> tuple[Attempt, Generation | None, LiftResult | None]:
        r = o.result
        route = r.route
        started = time.perf_counter()
        att = Attempt(r.key, rung, index, kind, model, "error")
        gen: Generation | None = None
        verdict: LiftResult | None = None
        try:
            gen = generator.generate(system, messages)
        except GenerationRefused as exc:
            att.outcome, att.stop_reason = "refused", "refusal"
            att.reason = att.feedback = f"The model refused: {exc}"
            self._charge(att, exc.model or model, exc.usage)
        except Exception as exc:  # an API failure ends the route's ladder, not the run
            att.outcome, att.reason = "error", f"LLM call failed: {type(exc).__name__}: {exc}"
            self.budget.charge(r.key, 0.0)
        if gen is not None:
            self._charge(att, gen.model or model, gen.usage)
            att.stop_reason = gen.stop_reason
            if gen.stop_reason == "max_tokens":
                att.outcome, att.reason, att.feedback = "truncated", "cut off", TRUNCATED
            else:
                source, problem = parse_candidate(gen.text, route.name, route.method,
                                                  route.path, t2=t2)  # fmt: skip
                if source is not None and procedures:
                    source = with_procedures(source, procedures)
                att.source = source
                if source is None:
                    att.outcome = "no_code" if "no ```python" in problem else "invalid"
                    att.reason = att.feedback = problem
                else:
                    verdict = verify_candidate(self.ctx, r, source, profile=profile,
                                               natives=natives)  # fmt: skip
                    att.tally = verdict.tally()
                    if verdict.status == "lifted-verified":
                        att.outcome = "verified"
                    else:
                        att.outcome = (
                            "checker" if verdict.diagnostics
                            else "suite_gap" if _suite_gap(verdict)
                            else "mismatch"
                        )  # fmt: skip
                        att.reason = verdict.reason
                        att.feedback = feedback_for(verdict, self._cases(r.key))
        att.latency_s = time.perf_counter() - started
        o.attempts.append(att)
        self._log_round(o, att, messages, gen)
        return att, gen, verdict

    def _charge(self, att: Attempt, model: str, usage: Usage) -> None:
        att.model = model or att.model
        att.cost_usd = _cost(att.model, usage)
        att.input_tokens, att.output_tokens = usage.total_input_tokens, usage.output_tokens
        self.tokens[0] += att.input_tokens
        self.tokens[1] += att.output_tokens
        self.budget.charge(att.route, att.cost_usd)

    def _cases(self, key: str) -> dict[str, Any]:
        suite = self.ctx.suite_for(key)
        return {c.name: c for c in suite.cases if c.route == key} if suite else {}

    def _log_round(
        self,
        o: RouteOutcome,
        att: Attempt,
        messages: Sequence[Mapping[str, str]],
        gen: Generation | None,
    ) -> None:
        if self.corpus is not None:
            record = {"type": "round", "method": self.config.method, **att.to_dict()}
            record.update(lattice=att.source, feedback=att.feedback[:4000])
            self.corpus.append(record)
        if self.artefacts is None:
            return
        d = self.artefacts / _part(o.key) / f"{att.rung}-{att.kind}-{att.round}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "prompt.md").write_text(messages[-1]["content"])
        if gen is not None:
            (d / "response.md").write_text(gen.text)
        if att.source is not None:
            (d / "candidate.lattice.py").write_text(att.source)
        meta = {**att.to_dict(), "feedback": att.feedback}
        (d / "round.json").write_text(json.dumps(meta, indent=2) + "\n")

    # -- the ladder --

    def context(self, r: LiftResult, history: Sequence[str], *, partial: bool) -> str:
        return assemble(
            self.evidence, r, self.config.method, history=history[-MAX_HISTORY:],
            include_partial=partial,
        ).render()  # fmt: skip

    def ladder(self, r: LiftResult) -> RouteOutcome:
        o = RouteOutcome(r.key, r, "island", "R4")
        if r.key not in self.ctx.inputs.routes:
            reason = self.ctx.inputs.skipped.get(r.key, "the route has no spec")
            return self._island(o, f"no request suite: {reason}")
        history: list[str] = []
        if r.lift.source is not None:
            if _suite_gap(r):
                return self._island(o, "unverified branch: " + feedback_for(r, {}))
            history.append(
                "The deterministic lifters' Lattice (above) failed verification:\n"
                + feedback_for(r, self._cases(r.key))
            )
        dynamic = self._dynamic(r)
        if dynamic:
            o.notes.append(f"R1/R2 skipped: dynamic dispatch in the slice ({dynamic})")
        else:
            for step in (self.r1, self.r2):
                done = step(o, history)
                if done is not None:
                    return done
        done = self.r3(o, history)
        if done is not None:
            return done
        last = next((a for a in reversed(o.attempts) if a.outcome != "skipped"), None)
        reason = last.reason if last is not None else "no rung produced a candidate"
        return self._island(o, reason or "no verified candidate", dynamic=bool(dynamic))

    def _dynamic(self, r: LiftResult) -> str:
        try:
            s = route_slice(self.evidence.graph, f"{r.route.module}:{r.route.name}")
        except KeyError:
            return ""
        return "; ".join(f"{f.function}: {f.kind}" for f in s.dynamic)

    def _blocked(self, o: RouteOutcome) -> RouteOutcome | None:
        why = self.budget.blocked(o.key)
        return None if why is None else self._island(o, f"budget: {why}")

    def _accept(
        self, o: RouteOutcome, rung: Rung, att: Attempt, verdict: LiftResult
    ) -> RouteOutcome:
        o.status, o.rung, o.source, o.verified, o.reason = "verified", rung, att.source, verdict, ""
        return o

    def _island(self, o: RouteOutcome, reason: str, *, dynamic: bool = False) -> RouteOutcome:
        o.status, o.rung, o.reason = "island", "R4", reason
        o.gap = classify_gap(o, dynamic)
        return o

    def r1(self, o: RouteOutcome, history: list[str]) -> RouteOutcome | None:
        r = o.result
        has_lift = r.lift.source is not None
        if has_lift:
            ask = (
                "Fix the lifters' Lattice (above) so that it passes verification:\n\n" + history[-1]
            )
        else:
            ask = "No deterministic lifter lifted this route. Write its Lattice endpoint."
        messages = [{"role": "user",
                     "content": self.context(r, [], partial=True) + "\n"
                     + self._compact(o.key, ask)}]  # fmt: skip
        rounds = self.config.repair_rounds + (0 if has_lift else 1)
        for i in range(rounds):
            blocked = self._blocked(o)
            if blocked is not None:
                return blocked
            kind = "repair" if has_lift or i > 0 else "lift"
            compact = o.key in self.compact
            att, gen, verdict = self.round(
                o, "R1", i + 1, kind, self.generator, self.config.model, self.system, messages
            )
            if att.outcome == "verified" and verdict is not None:
                return self._accept(o, "R1", att, verdict)
            history.append(self._history(att))
            if att.outcome == "suite_gap":
                return self._island(o, "unverified branch: " + att.feedback)
            if att.outcome in ("refused", "error"):
                return None  # R2 may still succeed (another model or effort)
            if att.outcome == "truncated":
                if self._cut_off(o, att, compact):
                    return None  # never the same settings again: on to R2
                # a fresh conversation asking for less (not the cut-off answer back)
                content = self.context(r, history, partial=True) + "\n" + ask
                messages = [{"role": "user", "content": self._compact(o.key, content)}]
                continue
            messages.append({"role": "assistant", "content": gen.text if gen else ""})
            repair = _repair_message(att.feedback)
            messages.append({"role": "user", "content": self._compact(o.key, repair)})
        return None

    def r2(self, o: RouteOutcome, history: list[str]) -> RouteOutcome | None:
        r = o.result
        model = self.config.strong_model or self.config.model
        ask = (
            "Earlier attempts at this route failed (see above). Write the route's Lattice "
            "endpoint again from scratch; do not repeat their mistakes."
        )
        for j in range(self.config.relift_attempts):
            blocked = self._blocked(o)
            if blocked is not None:
                return blocked
            compact = o.key in self.compact
            content = self.context(r, history, partial=False) + "\n" + self._compact(o.key, ask)
            att, _, verdict = self.round(
                o, "R2", j + 1, "relift", self.relift, model, self.system,
                [{"role": "user", "content": content}],
            )  # fmt: skip
            if att.outcome == "verified" and verdict is not None:
                return self._accept(o, "R2", att, verdict)
            history.append(self._history(att))
            if att.outcome == "suite_gap":
                return self._island(o, "unverified branch: " + att.feedback)
            if self._cut_off(o, att, compact):
                return None
        return None

    def r3(self, o: RouteOutcome, history: list[str]) -> RouteOutcome | None:
        r = o.result
        if self.carve is None:
            o.notes.append("R3 skipped: no carve-out strategy")
            return None
        known = {n.origin for n in self.result.model.natives}
        try:
            plans, why = self._plans(f"{r.route.module}:{r.route.name}", known)
        except KeyError as exc:
            plans, why = (), f"the route is not in the code graph ({exc})"
        if not plans:
            o.notes.append(f"R3 skipped: {why}")
            o.attempts.append(Attempt(r.key, "R3", 0, "carve", "", "skipped", reason=why))
            if self.corpus is not None:
                self.corpus.append({"type": "round", "route": r.key, "rung": "R3",
                                    "outcome": "skipped", "reason": why})  # fmt: skip
            return None
        if why:
            o.notes.append(f"R3: {why}")
        for plan in plans:
            done = self._carve(o, history, plan)
            if done is not None:
                return done
        return None

    def _plans(self, route: str, known: set[str]) -> tuple[Sequence[CarvePlan], str]:
        """The strategy's plans; a plain :class:`CarveOut` offers one native plan."""
        carve = self.carve
        if hasattr(carve, "plans"):
            planned: PlannedCarveOut = carve  # type: ignore[assignment]
            return planned.plans(self.evidence.graph, route, known, self.result.lattice_profile)
        natives, why = carve.natives(self.evidence.graph, route, known)  # type: ignore[union-attr]
        return ((CarvePlan("native", natives=tuple(natives)),) if natives else ()), why

    def _carve(self, o: RouteOutcome, history: list[str], plan: CarvePlan) -> RouteOutcome | None:
        """The attempts of one plan: a verified candidate is accepted (with the natives
        and procedures it uses); otherwise None (the next plan, or R4)."""
        r = o.result
        profile = None
        natives: dict[str, Callable[..., Any]] | None = None
        system = self.system
        if plan.kind == "native":
            profile_source = carve_profile_source(self.result.profile.source, plan.natives)
            module = load_profile_source(profile_source)
            profile, natives = module.profile(), dict(module.NATIVES)
            system = system_prompt(profile_source, hints=self.config.hints)
            listed = "\n".join(f"- `{n.signature()}` wraps `{n.target}`" for n in plan.natives)
            ask = (
                "Earlier attempts at this route failed (see above). Carve it out: keep the "
                "route in Lattice and call these natives (typed wrappers over the original's "
                f"own helpers, added to the profile) for what Lattice cannot express:\n{listed}"
            )
            if plan.why:
                ask += f"\n\nNatives are offered because {plan.why}."
        else:
            ask = self._procedure_ask(plan)
        for k in range(self.config.carve_attempts):
            blocked = self._blocked(o)
            if blocked is not None:
                return blocked
            compact = o.key in self.compact
            content = self.context(r, history, partial=False) + "\n" + self._compact(o.key, ask)
            att, _, verdict = self.round(
                o, "R3", k + 1, f"carve-{plan.kind}", self.strong,
                self.config.strong_model or self.config.model, system,
                [{"role": "user", "content": content}], profile=profile, natives=natives,
                t2=True, procedures=plan.procedures,
            )  # fmt: skip
            if att.outcome == "verified" and verdict is not None and att.source is not None:
                o.natives = used_natives(att.source, plan.natives)
                o.procedures = procedure_infos(att.source, plan.procedures)
                o.carve = "native" if o.natives else "procedure" if o.procedures else "none"
                return self._accept(o, "R3", att, verdict)
            history.append(self._history(att))
            if att.outcome == "suite_gap":
                return self._island(o, "unverified branch: " + att.feedback)
            if self._cut_off(o, att, compact):
                return None
        return None

    def _procedure_ask(self, plan: CarvePlan) -> str:
        parts = [T2_CARVE]
        if plan.procedures:
            code = "\n\n\n".join(
                "\n".join([*p.support, "", p.source.strip()]) if p.support else p.source.strip()
                for p in plan.procedures
            )  # fmt: skip
            parts.append(
                "These procedures were carved mechanically from the original's helpers "
                "(already in the T2 subset; each keeps its helper's name). Call them by name: "
                "one you call without defining it is added to your answer with the imports "
                f"and constants it needs.\n\n```python\n{code}\n```"
            )
        if plan.translate:
            helpers = "\n\n".join(
                f"- `{h.origin}` (not carved mechanically: {h.why})\n\n```python\n"
                f"{h.source.rstrip()}\n```"
                for h in plan.translate
            )
            parts.append(f"These helpers of the original need translating by you:\n\n{helpers}")
        parts.append(T2_ANSWER)
        return "\n\n".join(parts)

    def _history(self, att: Attempt) -> str:
        head = f"### Attempt {att.rung} round {att.round} ({att.kind}): {att.outcome}"
        body = f"```python\n{att.source.rstrip()}\n```\n" if att.source else ""
        return f"{head}\n{body}{att.feedback or att.reason}"


def load_profile_source(source: str) -> Any:
    from lattice.importer.profile_gen import ProfileResult

    return load_profile(ProfileResult(source, []), f"lattice_import_carve_{uuid.uuid4().hex}")


def _part(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "_"


# ------------------------------------------------------------------------------- join --


def _top_names(source: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            out[node.name] = ast.unparse(node)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    out[t.id] = ast.unparse(node)
    return out


def _dedupe(sources: Sequence[str]) -> list[str]:
    """``sources`` with a top-level statement identical to one of an earlier source
    removed (two routes carrying the same procedure, record, import or constant)."""
    seen: set[str] = set()
    out: list[str] = []
    for source in sources:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            out.append(source)
            continue
        lines = source.split("\n")
        drop: set[int] = set()
        for node in tree.body:
            is_endpoint = isinstance(node, ast.FunctionDef) and "endpoint" in [
                _decorator_name(d) for d in node.decorator_list
            ]
            text = ast.unparse(node)
            if not is_endpoint and text in seen:
                first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
                drop |= set(range(first - 1, node.end_lineno or node.lineno))
            seen.add(text)
        if drop:
            kept = [line for i, line in enumerate(lines) if i not in drop]
            source = re.sub(r"\n{4,}", "\n\n\n", "\n".join(kept)).strip("\n") + "\n"
        out.append(source)
    return out


def _router_source(sources: Sequence[str], module: str, llm: Sequence[str]) -> str:
    sources = _dedupe(sources)
    if not llm:
        return program_source(sources, module)
    comment = (
        f"# Lifted from {module} by lattice.importer (deterministic lifters and the LLM "
        f"loop: {', '.join(llm)}; every route verified).\n"
    )
    return HEADER + comment + "\n\n\n".join(s.strip("\n") for s in sources) + "\n"


def _join(
    outcomes: list[RouteOutcome],
    base_profile: str,
    order: Mapping[str, int],
) -> tuple[dict[str, str], str, tuple[CarveNative, ...]]:
    """The programs per router and the final profile (with the carve-out natives the
    verified R3 routes use). A route whose joined router program is refused, or whose
    top-level names clash with another route's, becomes an island."""
    natives: dict[str, CarveNative] = {}
    for o in sorted(outcomes, key=lambda x: order.get(x.key, 0)):
        if o.status == "verified" and o.rung == "R3":
            taken = [n for n in o.natives if natives.get(n.name, n) != n]
            if taken:
                # the joined profile holds one native per name: this route was verified
                # against another implementation than the one it would ship with
                shown = "; ".join(
                    f"{n.name} is {natives[n.name].target} there, {n.target} here"
                    for n in taken
                )
                _demote(o, f"joined profile: a carve-out native name is taken ({shown})")
                continue
            for n in o.natives:
                natives.setdefault(n.name, n)
    profile_source = carve_profile_source(base_profile, tuple(natives.values()))
    profile = load_profile_source(profile_source).profile()
    by_router: dict[str, list[RouteOutcome]] = {}
    for o in outcomes:
        if o.status == "verified":
            by_router.setdefault(o.result.router, []).append(o)
    programs: dict[str, str] = {}
    for router, rs in by_router.items():
        rs.sort(key=lambda o: (o.rung != "R0", order.get(o.key, 0)))
        module = rs[0].route.module
        accepted: list[RouteOutcome] = []
        names: dict[str, str] = {}
        for o in rs:
            assert o.source is not None
            mine = _top_names(o.source)
            clash = [n for n, t in mine.items() if n in names and names[n] != t]
            if clash:
                _demote(o, f"joined router program: {', '.join(clash)} is defined differently "
                           "by another route")  # fmt: skip
                continue
            trial = sorted([*accepted, o], key=lambda x: order.get(x.key, 0))
            source = _router_source([x.source or "" for x in trial], module,
                                    [x.key for x in trial if x.rung != "R0"])  # fmt: skip
            plans, errors = check_program(source, profile)
            if plans is None:
                _demote(o, "the joined router program is refused: " + "; ".join(errors)[:500])
                continue
            accepted.append(o)
            names.update(mine)
        if accepted:
            final = sorted(accepted, key=lambda x: order.get(x.key, 0))
            programs[router] = _router_source([x.source or "" for x in final], module,
                                              [x.key for x in final if x.rung != "R0"])  # fmt: skip
    used = {n.name for o in outcomes if o.status == "verified" for n in o.natives}
    kept = tuple(n for n in natives.values() if n.name in used)
    if len(kept) != len(natives):  # a demoted R3 route: drop its natives
        profile_source = carve_profile_source(base_profile, kept)
    return programs, profile_source, kept


def _demote(o: RouteOutcome, reason: str) -> None:
    o.status, o.rung, o.reason = "island", "R4", reason
    o.natives, o.procedures, o.carve = (), (), "none"
    o.gap = classify_gap(o)


# -------------------------------------------------------------------------------- api --


def _route_order(result: ImportResult) -> dict[str, int]:
    assert result.context is not None
    keys = result.context.inputs.keys
    return {keys[(r.module, r.name)]: i for i, r in enumerate(result.model.routes)}


def _r0(r: LiftResult) -> RouteOutcome:
    return RouteOutcome(r.key, r, "verified", "R0", source=r.lift.source, verified=r)


def no_llm(result: ImportResult, *, corpus: CorpusLog | None = None) -> CloseResult:
    """The import without the loop: the lifters' verified routes (R0); a route with no
    request suite is an island at once (R4, with the classified gap: nothing could
    verify it); every other route is reported as needing the LLM (never emitted)."""
    started = time.perf_counter()
    outcomes = []
    skipped = result.context.inputs.skipped if result.context is not None else {}
    for r in result.results:
        if r.status == "lifted-verified":
            outcomes.append(_r0(r))
        elif r.key in skipped:
            # nothing could verify the route (no request suite): an island at once, as
            # in the loop, with the classified gap
            o = RouteOutcome(r.key, r, "island", "R4")
            o.reason = f"no request suite: {skipped[r.key]}"
            o.gap = classify_gap(o)
            outcomes.append(o)
        else:
            o = RouteOutcome(r.key, r, "needs-llm", "needs-llm", reason=r.reason)
            outcomes.append(o)
    close = CloseResult(result, outcomes, dict(result.programs), result.profile.source, False)
    _log_routes(close, corpus)
    close.wall_s = time.perf_counter() - started
    return close


def close_gap(
    result: ImportResult,
    generator: Generator,
    *,
    config: LoopConfig | None = None,
    strong_generator: Generator | None = None,
    relift_generator: Generator | None = None,
    intent: IntentNotes | None = None,
    carve: CarveOut | PlannedCarveOut | None = None,
    corpus: CorpusLog | None = None,
    artefacts: Path | None = None,
    evidence: Evidence | None = None,
    only: Sequence[str] | None = None,
) -> CloseResult:
    """Run the ladder over every route of ``result`` the lifters did not verify (``only``
    limits them) and join the verified routes (see the module docstring). The routes
    run cheapest first (by the size of their code slice), each with its share of the
    repo budget (:meth:`Budget.allot`); the outcomes are in route order. R1 runs on
    ``generator``, R2 on ``relift_generator`` (default: ``strong_generator``), R3 on
    ``strong_generator`` (default: ``generator``). ``carve`` defaults to
    :class:`~lattice.importer.carve.ProcedureCarveOut`
    (a T2 procedure, then a typed native only for logic outside the T2 subset);
    ``intent`` supplies S3 for the arms that use it. ``result`` is not modified."""
    started = time.perf_counter()
    config = config or LoopConfig()
    ev = evidence or Evidence(result, intent=intent)
    loop = _Loop(
        result,
        generator,
        config,
        strong_generator=strong_generator,
        relift_generator=relift_generator,
        evidence=ev,
        carve=carve if carve is not None else ProcedureCarveOut(),
        corpus=corpus,
        artefacts=artefacts,
    )
    notes_spent: list[float] = []
    if intent is not None and intent.charge is None:

        def charge(gen: Generation) -> None:
            cost = _cost(gen.model or config.model, gen.usage)
            notes_spent.append(cost)
            loop.budget.charge(None, cost)
            loop.tokens[0] += gen.usage.total_input_tokens
            loop.tokens[1] += gen.usage.output_tokens

        intent.charge = charge
    done: dict[str, RouteOutcome] = {}
    try:
        laddered: list[LiftResult] = []
        for r in result.results:
            if r.status == "lifted-verified":
                done[r.key] = _r0(r)
            elif only is not None and r.key not in only:
                done[r.key] = RouteOutcome(r.key, r, "needs-llm", "needs-llm", reason=r.reason)
            else:
                laddered.append(r)
        laddered.sort(key=lambda r: _slice_size(ev, r))  # stable: route order on ties
        for i, r in enumerate(laddered):
            if (why := loop.budget.repo_blocked()) is not None:
                o = RouteOutcome(r.key, r, "island", "R4")
                done[r.key] = loop._island(o, f"budget: {why}")
                continue
            loop.budget.allot(r.key, len(laddered) - i)
            done[r.key] = loop.ladder(r)
    finally:
        if evidence is None:
            ev.close()
    outcomes = [done[r.key] for r in result.results]
    programs, profile_source, natives = _join(outcomes, result.profile.source, _route_order(result))
    close = CloseResult(
        result,
        outcomes,
        programs,
        profile_source,
        True,
        method=config.method,
        model=config.model,
        budget=loop.budget,
        carve_natives=natives,
        notes_cost_usd=sum(notes_spent),
        input_tokens=loop.tokens[0],
        output_tokens=loop.tokens[1],
    )
    if intent is not None:
        close.disagreements = _disagreements(close, intent)
    _log_routes(close, corpus)
    close.wall_s = time.perf_counter() - started
    return close


def _slice_size(ev: Evidence, r: LiftResult) -> int:
    """The size of the route's code slice (characters of its source): the cheaper
    routes run first. A route missing from the code graph counts as the largest."""
    try:
        return len(slice_source(route_slice(ev.graph, f"{r.route.module}:{r.route.name}")))
    except Exception:  # noqa: BLE001 -- never fail the run over the order
        return 1 << 30


def _observed(o: RouteOutcome) -> set[int] | None:
    for v in (o.verified, o.result):
        if v is not None and v.verify is not None:
            return {rec.status for rec in v.verify.original.values()}
    return None


def _disagreements(close: CloseResult, intent: IntentNotes) -> list[NotesDisagreement]:
    """Notes vs code and verified behaviour, for the features whose notes were made."""
    out: list[NotesDisagreement] = []
    for feature, note in intent.made().items():
        routes = [o for o in close.outcomes if o.route.module == feature]
        auth = {o.route.name: o.route.auth for o in routes}
        observed = {o.route.name: s for o in routes if (s := _observed(o)) is not None}
        out += disagreements(note, auth=auth, observed=observed)
    return out


def _function_source(result: ImportResult, module: str, name: str) -> str | None:
    ctx = result.context
    if ctx is None or module not in ctx.index.modules:
        return None
    fn = ctx.index.modules[module].defs.get(name)
    if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    return ast.unparse(fn)


def _log_routes(close: CloseResult, corpus: CorpusLog | None) -> None:
    if corpus is None:
        return
    for o in close.outcomes:
        r = o.result
        corpus.append({
            "type": "route",
            "route": o.key,
            "route_source": _function_source(close.result, r.route.module, r.route.name),
            "module": r.route.module,
            "status": o.status,
            "rung": o.rung,
            "by": o.by,
            "reason": o.reason,
            "gap": o.gap,
            "lifter_miss": r.reason if r.status == "no-lifter" else None,
            "lifters": list(r.lift.lifters),
            "lattice": o.source,
            "tally": list(o.verified.tally() or ()) if o.verified is not None else None,
            "rounds": o.rounds,
            "cost_usd": round(o.cost_usd, 6),
            "method": close.method,
            "tier": o.tier,
            "carve": o.carve,
            "procedures": len(o.procedures),
            "procedure_lines": sum(p.lines for p in o.procedures),
        })  # fmt: skip
