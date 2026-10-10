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

**Citation check** (deterministic, `check_citation`): a cited target must exist in the
digest; a step number must exist in that scenario; the quote must match the element --
a cell's statuses equal the quote's, a step's method, path and status appear in the
quote (and its caller, when the quote names one), a machine's quoted ``a -> b`` moves
are edges, anything else a normalised substring of the element's text. A failing
citation moves to `Answer.rejected_citations` with its reason. After one repair round
(the model is told which citations failed), a yes / no / value answer left with no
valid citation is downgraded to ``not_specified``, whatever the model insists.

**Verify** (`VerifyConfig`): the model writes a minimal scenario (Lattice steps as JSON)
that would demonstrate the answer on the seeded app; it runs alone through
`lattice.spec.check.check(..., layers="D")` on a spec that reuses the base spec's
profile, users and services. ``agrees``: it passed; ``disagrees``: a layer D finding
(expected vs actual); ``error``: the scenario was invalid or could not run. For a
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
    (the scenario's steps, a cell's status set, a machine's edges)."""

    target: str
    kind: str
    content: str
    steps: tuple[str, ...] = ()
    raw: Any = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class Digest:
    """The spec digest: `text` (what the model reads) and `elements` (target ->
    Element, what citations are checked against)."""

    text: str
    elements: Mapping[str, Element]

    def lines_for(self, targets: Iterable[str]) -> list[str]:
        """The digest lines of the given targets (unknown ones skipped)."""
        out = []
        for t in targets:
            e = self.elements.get(t)
            if e is None:
                continue
            out.append(f"[{t}] {e.content}")
            out += [f"  {i}. {s}" for i, s in enumerate(e.steps, 1)]
        return out


def _cell_statuses(text: str) -> frozenset[int]:
    return frozenset(int(s) for s in re.findall(r"\b[1-5]\d\d\b", text))


def build_digest(spec: Spec, trace: Trace | None = None) -> Digest:
    """The spec digest (see the module docstring). Deterministic: the same spec and
    trace always give the same text, in the spec's own order."""
    data = _build(spec, trace or Trace(source="", rules=[]), None, None, "spec")
    elements: dict[str, Element] = {}
    lines = [
        "SPEC DIGEST -- every citable element, under its exact target in [brackets].",
        "",
    ]

    def add(e: Element, line: str | None = None) -> None:
        elements[e.target] = e
        lines.append(line if line is not None else f"[{e.target}] {e.content}")
        lines.extend(f"  {i}. {s}" for i, s in enumerate(e.steps, 1))

    lines += ["## Seed users (handle: login) -- scenarios sign in by handle", ""]
    if spec.users:
        for handle, cred in spec.users.items():
            login = cred[0] if isinstance(cred, tuple | list) and cred else cred
            lines.append(f"- {handle}: {login}")
    else:
        lines.append("(none)")
    lines.append("")

    lines += ["## Tables", ""]
    tables, sql_names = tables_of(spec.profile)
    for name, schema in tables.items():
        sql = sql_names.get(name, name)
        cols = "; ".join(
            ln[2:] for ln in _table_block(name, sql, schema)[2:] if ln.startswith("- ")
        )
        head = f"{name} ({sql})" if sql != name else name
        add(Element(f"profile:{name}", "profile", f"{head}: {cols}"))
    if not tables:
        lines.append("(none)")
    lines.append("")

    lines += [
        "## Access matrix (caller | METHOD /template -> the status(es) the scenarios "
        "expect; a cell not listed is not exercised by any scenario)",
        "",
    ]
    n_cells = 0
    for group in data.access_groups:
        for row in group.rows:
            for col in group.columns:
                status = group.cells.get((row, col), "·")
                if status == "·":
                    continue
                n_cells += 1
                add(Element(f"cell:{row}|{col}", "cell", status, raw=_cell_statuses(status)))
    if not n_cells:
        lines.append("(none)")
    lines.append("")

    lines += ["## Scenarios (each runs on a fresh copy of the seeded database)", ""]
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
            ),
        )
    if not data.section6.scenarios:
        lines.append("(none)")
    lines.append("")

    lines += ["## Invariants (hold after every request)", ""]
    for target, _name, sentence in data.section6.invariants:
        add(Element(target, "invariant", sentence))
    if not data.section6.invariants:
        lines.append("(none)")
    lines.append("")

    lines += ["## State machines (the only allowed moves)", ""]
    machines = {f"machine:{m.table}.{m.column}": m for m in spec.machines}
    for target, _label, moves in data.section6.machines:
        m = machines.get(target)
        add(Element(target, "machine", moves, raw=frozenset(m.edges) if m else frozenset()))
    if not data.section6.machines:
        lines.append("(none)")
    lines.append("")

    lines += ["## Background services", ""]
    for target, name, doc in data.section6.services:
        add(Element(target, "service", f"{name}: {doc}" if doc else name))
    if not data.section6.services:
        lines.append("(none)")
    lines.append("")

    lines += ["## Not checked (stated in the spec, not checked by it)", ""]
    for i, u in enumerate(data.unchecked_notes, 1):
        concerns = f" (concerns: {', '.join(u.concerns)})" if u.concerns else ""
        add(Element(f"unchecked:{i}", "unchecked", u.text + concerns))
    if not data.unchecked_notes:
        lines.append("(none)")
    lines.append("")

    if trace is not None:
        lines += ["## The owner's requirements (rules), with their status", ""]
        for row in data.rule_rows:
            how = f"; checked by {', '.join(row.links)}" if row.links else ""
            note = f"; unchecked because: {row.note}" if row.note else ""
            add(
                Element(f"rule:{row.id}", "rule", row.text),
                f"[rule:{row.id}] ({row.status}{how}{note}) {row.text}",
            )
        if not data.rule_rows:
            lines.append("(none)")
        lines.append("")

    return Digest("\n".join(lines).rstrip() + "\n", elements)


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


SYSTEM = """\
You answer business questions about one application, using ONLY its spec digest.

The digest lists every element of the spec under an exact target in [brackets]:
access-matrix cells (cell:<caller>|<METHOD /template> -> expected status), scenarios
with numbered steps ("METHOD /path as <caller> -> <status>"), invariants, state machines,
background services, tables (profile:<Table>), notes the spec does not check
(unchecked:<n>) and, when present, the owner's rules (rule:<Rn>).

Rules:
- Answer only from the digest. Never answer from general knowledge of how apps usually
  work, and never guess. If the digest does not settle the question, answer with kind
  "not_specified" and say what is missing.
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


def _prompt(digest: Digest, question: str, selected: Sequence[str]) -> str:
    parts = [digest.text]
    sel = digest.lines_for(selected)
    if sel:
        parts += [
            "The user selected these elements (the question is probably about them):",
            *sel,
            "",
        ]
    parts.append(f"Question: {question}")
    return "\n".join(parts)


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
    """Model calls' usage, summed."""

    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.rounds = 0
        self.input = self.output = 0
        self.cost: float | None = None

    def call(self, generator: Generator, system: str, messages: list[dict[str, str]]) -> str:
        gen = generator.generate(system, messages)
        self.rounds += 1
        self.input += gen.usage.total_input_tokens
        self.output += gen.usage.output_tokens
        if gen.cost_usd is not None:
            self.cost = (self.cost or 0.0) + gen.cost_usd
        return gen.text

    def as_dict(self, model: str) -> dict[str, Any]:
        out = {
            "seconds": round(time.perf_counter() - self.start, 2),
            "tokens": self.input + self.output,
            "input_tokens": self.input,
            "output_tokens": self.output,
            "rounds": self.rounds,
            "model": model,
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
) -> Answer:
    """Answer `question` from `spec` (see the module docstring). `digest`: a prebuilt
    `build_digest(spec, trace)` (`ask_many` reuses one)."""
    digest = digest or build_digest(spec, trace)
    meter = _Meter()
    messages = [{"role": "user", "content": _prompt(digest, question, selected)}]
    text = meter.call(generator, SYSTEM, messages)
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
        text = meter.call(generator, SYSTEM, messages)
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
        answer.verified = _verify(spec, digest, question, answer, verify, generator, meter)
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
    """`ask` each question in turn, on one digest."""
    digest = build_digest(spec, trace)
    return [
        ask(spec, q, trace=trace, generator=generator, model=model, verify=verify, digest=digest)
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
earlier steps -- copy an existing scenario's setup steps. Use concrete paths (/runs/1,
never /runs/{}).

Reply with ONE JSON object and nothing else:
{"steps": [{"method": "POST", "path": "/...", "user": "<handle or null>",
            "json": <request body or null>, "expect_status": 200,
            "expect_db": <null or {"<table>": {"where": {...}, "count": n}}>}]}
"""


def _verify_prompt(digest: Digest, question: str, answer: Answer, observe: bool) -> str:
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
    return f"{digest.text}\nQuestion: {question}\n\n{goal}"


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


def _verify(
    spec: Spec,
    digest: Digest,
    question: str,
    answer: Answer,
    cfg: VerifyConfig,
    generator: Generator,
    meter: _Meter,
) -> dict[str, Any]:
    observe = answer.kind == "not_specified"
    if observe and not cfg.observe:
        return {
            "status": "error",
            "detail": "not run: the answer is not_specified and observation is off",
            "scenario": "",
        }
    messages = [{"role": "user", "content": _verify_prompt(digest, question, answer, observe)}]
    try:
        text = meter.call(generator, VERIFY_SYSTEM, messages)
        scenario = _scenario_from(_json_object(text), "ask_verify")
    except Exception as err:  # noqa: BLE001 -- a bad scenario is an error result
        return {
            "status": "error",
            "detail": f"no valid scenario: {type(err).__name__}: {err}",
            "scenario": "",
        }
    if observe:
        return _observe(spec, scenario, cfg)
    status, out = _run(spec, scenario, cfg)
    if status == "pass":
        return {
            "status": "agrees",
            "detail": "the scenario passed against the app",
            "scenario": _readable(scenario),
        }
    if status == "error":
        return {"status": "error", "detail": out, "scenario": _readable(scenario)}
    return {
        "status": "disagrees",
        "detail": _finding_text(scenario, out),
        "scenario": _readable(scenario),
    }


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
    p.add_argument(
        "--database", default="sqlite", choices=("sqlite", "postgres", "mysql", "clickhouse")
    )
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
    "VerifyConfig",
    "ask",
    "ask_many",
    "build_digest",
    "check_citation",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
