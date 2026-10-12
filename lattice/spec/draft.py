"""Draft a Lattice spec from an owner's markdown requirements (``lattice.spec.draft``).

An LLM turns the requirements into a spec module (``SPEC: Spec``) plus its trace
(:class:`lattice.spec.rules.Trace`); the owner reviews the draft instead of writing it.

:func:`draft` numbers the rules (:func:`lattice.spec.rules.extract_rules`), prompts the
model with the numbered rules, the context and the spec API reference
(:func:`api_reference`, rendered from ``draft_reference.md`` and Lattice's own
docstrings), then parses the answer into a module and a trace JSON. The module is
imported in isolation (a temporary directory, a unique module name), ``validate(SPEC)``
runs, and the trace is checked: every rule is in exactly one of links, unchecked or
context; every link names a real element of ``SPEC``; every unchecked rule has an
``Unchecked`` note citing its id. Any problem -- an import error, an S-code, a trace
problem -- goes back to the model in a repair round with the exact errors, at most
``max_repairs`` times. :func:`revise` does the same from an existing draft plus feedback
(e.g. a lint report).

CLI (:func:`main`)::

    draft REQUIREMENTS.md --context CONTEXT.md --out spec_gen.py [--model opus]
        [--max-repairs 3] [--revise --feedback lint.json]
    draft NEW.md --since OLD.md --base BASE.py [--context CTX.md] --out NEW.py
        [--impact --app mod:factory --db mod:build_db ...]

(the second form drafts only the change: :mod:`lattice.spec.incremental`)

writes the module, its trace (``spec_gen.trace.json``, :func:`lattice.spec.rules.
trace_path`) and ``spec_gen.draft.json`` (usage, rounds, problems, transcript). Exit 0
when the draft is valid, 1 otherwise, 2 on bad arguments. The model is called through
the Claude Code CLI (:class:`lattice.llm.cli_client.ClaudeCliClient`), never an API key.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib
import importlib.util
import inspect
import json
import re
import sys
import tempfile
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.llm.client import Generation, GenerationRefused, Generator
from lattice.spec.model import Spec, resolve_table
from lattice.spec.rules import LINK_KINDS, Question, Rule, Trace, extract_rules, trace_path
from lattice.spec.validate import CODES, validate

__all__ = [
    "DRAFTING_RULES",
    "DraftResult",
    "DraftUsage",
    "api_reference",
    "check_trace",
    "draft",
    "evaluate",
    "load_spec",
    "main",
    "parse_answer",
    "revise",
]

REFERENCE_FILE = Path(__file__).with_name("draft_reference.md")
MAX_PROBLEMS_SHOWN = 80

# ---- the API reference ---------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{([a-z-]+)(?::([^}]*))?\}\}")


def _resolve(dotted: str) -> Any:
    """The object a dotted name names: a module, or an attribute path inside one."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj: Any = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        return obj
    raise ImportError(dotted)


def _annotation(text: Any) -> str:
    return text if isinstance(text, str) else getattr(text, "__name__", str(text))


def _fields(cls: type) -> str:
    """A dataclass's constructor fields: ``(name: type, name: type = default, ...)``."""
    shown = []
    for f in dataclasses.fields(cls):
        if not f.init:
            continue
        text = f"{f.name}: {_annotation(f.type)}"
        if f.default is not dataclasses.MISSING:
            text += f" = {f.default!r}"
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            text += f" = {f.default_factory()!r}"  # type: ignore[misc]
        shown.append(text)
    return "(" + ", ".join(shown) + ")"


def _signature(fn: Any) -> str:
    return str(inspect.signature(fn)).replace("'", "")


def _doc(obj: Any) -> str:
    return inspect.getdoc(obj) or ""


def _module_doc(spec: str) -> str:
    """``module|start|end``: the module docstring from `start` up to (not including)
    `end` (empty: to the end)."""
    name, start, end = ([*spec.split("|"), "", ""])[:3]
    doc = _doc(_resolve(name))
    begin = doc.find(start) if start else 0
    if begin < 0:
        raise ValueError(f"reference: {start!r} not in {name}'s docstring")
    stop = doc.find(end, begin) if end else len(doc)
    if stop < 0:
        raise ValueError(f"reference: {end!r} not in {name}'s docstring")
    return doc[begin:stop].strip()


def _code_bullets() -> dict[str, str]:
    """S-code -> its explanation, from lattice.spec.validate's module docstring."""
    doc = _doc(_resolve("lattice.spec.validate"))
    found: dict[str, str] = {}
    current: str | None = None
    for line in doc.splitlines():
        m = re.match(r"^- (S\d{3}): (.*)$", line)
        if m:
            current = m.group(1)
            found[current] = m.group(2).strip()
        elif current and line.startswith("  ") and line.strip():
            found[current] += " " + line.strip()
        else:
            current = None
    return found


def _doc_bullets(names: str) -> str:
    lines = []
    for dotted in names.split(","):
        obj = _resolve(dotted.strip())
        doc = " ".join(_doc(obj).split())
        lines.append(f"  - `{obj.__name__}{_fields(obj)}`: {doc}")
    return "\n".join(lines)


def _placeholder(kind: str, arg: str | None) -> str:
    from lattice.typesys import Scalar

    if kind == "scalars":
        return ", ".join(f"`Scalar.{s.name}` ({s.value})" for s in Scalar)
    if kind == "codes-all":
        return "; ".join(f"{code} {text}" for code, text in CODES.items())
    assert arg is not None, kind
    if kind == "doc":
        return _doc(_resolve(arg))
    if kind == "fields":
        return _fields(_resolve(arg))
    if kind == "sig":
        return _signature(_resolve(arg))
    if kind == "moddoc":
        return _module_doc(arg)
    if kind == "doc-bullets":
        return _doc_bullets(arg)
    if kind == "codes":
        bullets = _code_bullets()
        return "\n".join(f"- {c}: {bullets[c.strip()]}" for c in arg.split(","))
    raise ValueError(f"reference: unknown placeholder {kind!r}")


def api_reference() -> str:
    """The spec API reference the drafting prompt carries: ``draft_reference.md`` with
    its ``{{...}}`` placeholders filled from Lattice's own docstrings (so it follows the
    code)."""
    template = REFERENCE_FILE.read_text()
    return _PLACEHOLDER.sub(lambda m: _placeholder(m.group(1), m.group(2)), template)


# ---- the prompt ----------------------------------------------------------------------------

DRAFTING_RULES = """\
1. **Trace every rule.** Each numbered rule gets a scenario or an invariant that would
   fail on an app that breaks it, or an `Unchecked` note with the reason it cannot be
   checked (and the rule id first in its text), or -- for a rule that states no
   checkable behaviour (an introduction, the stack) -- a place in `context`. A link
   must point at an element that really checks the rule, not a nearby one.
2. **Say what the database looks like after every write.** Every POST/PUT/PATCH/DELETE
   step has an `expect_db`. A refused or invalid request gets an `expect_db` saying
   nothing was written (the row is absent: a `where` on the values sent with
   `"count": 0`, or the touched row still `equals` its old values). A successful write
   gets an `expect_db` of the row it made or changed (`where` + `equals`).
3. **Use the access matrix.** Every endpoint gets a no-token case (`None` caller), an
   outsider case (another user's row) and, where roles exist, a wrong-role case.
   `access_matrix` writes these for you; give refused cells an `expect_db` too.
4. **"At most" and "unique" rules** become an invariant (`count(T, by=..., where=...)
   <= n`) as well as a scenario that tries to break the bound.
5. **Lists of allowed values and limits** get an invalid-value scenario at each
   boundary: just past each end refused, and each end itself accepted (a length 1..100
   gets 0 and 101 refused, 1 and 100 accepted; a one_of gets a value outside it).
6. **Background behaviour** ("a loop", "a worker", "in production", "every minute")
   becomes an `Await` on a declared `Service`, using the service names the context
   gives, with an `expect_db` of what the service brings about.
7. **Ask, don't guess.** Anything the requirements leave open (a status code, an
   order, what happens on a repeat, a limit) becomes a `Question` in the trace, with
   the default the spec uses; the spec then follows that default. Prefer the context's
   conventions for defaults.
8. Take tables, columns, SQL table names, roles, seeded users and service names from
   the context; do not invent what it does not give (ask instead). Use only the
   documented Lattice API; the module must import and `validate(SPEC)` must be empty.
"""

OUTPUT_FORMAT = """\
Answer with exactly two fenced blocks and nothing else of substance:

1. ```python -- the complete spec module. It imports only from `lattice.profile`,
   `lattice.spec` and `lattice.typesys` (plus the standard library) and defines
   `SPEC = Spec(...)`.
2. ```json -- the trace: an object with `links` (rule id -> ["kind:name", ...]),
   `unchecked` (rule id -> reason), `context` (rule ids) and `questions` (a list of
   {"id": "Q1", "text": ..., "default": ..., "rules": [rule ids], "answer": null}).
   Do not repeat the rules themselves; they are filled in from the numbered list.
"""


def system_prompt() -> str:
    """The drafting system prompt: the role, the drafting rules, the output format and
    the API reference (stable across calls, so a transport may cache it)."""
    return (
        "You draft Lattice specs. An application owner wrote requirements in markdown; "
        "you turn them into a Lattice spec module that the owner reviews instead of "
        "writing it by hand, plus a trace that links every numbered rule to the spec "
        "elements that check it. A checker later runs the spec against the real app, so "
        "every scenario and invariant must state exactly what the requirements say.\n\n"
        "## Drafting rules\n\n"
        f"{DRAFTING_RULES}\n"
        "## Output format\n\n"
        f"{OUTPUT_FORMAT}\n"
        f"{api_reference()}"
    )


def _rules_block(rules: Sequence[Rule]) -> str:
    lines = []
    for r in rules:
        section = f" [{r.section}]" if r.section else ""
        lines.append(f"{r.id}{section}: {r.text}")
    return "\n".join(lines) if lines else "(no rules)"


def _trace_json(trace: Trace) -> str:
    body: dict[str, Any] = {
        "links": trace.links,
        "unchecked": trace.unchecked,
        "context": trace.context,
        "questions": [dataclasses.asdict(q) for q in trace.questions],
    }
    # an incremental draft's record of the change (empty, and left out, otherwise)
    for key in ("replaces", "retired", "retired_targets"):
        if getattr(trace, key):
            body[key] = getattr(trace, key)
    return json.dumps(body, indent=2, ensure_ascii=False)


def draft_message(rules: Sequence[Rule], context_md: str) -> str:
    """The first user message of a draft."""
    return (
        "# Requirements, as numbered rules\n\n"
        f"{_rules_block(rules)}\n\n"
        "# Context\n\n"
        f"{context_md.strip() or '(none given)'}\n\n"
        "# Task\n\n"
        "Draft the spec module for these requirements and its trace, following the "
        "drafting rules. Every rule id above must appear in the trace.\n"
    )


def revise_message(
    rules: Sequence[Rule], context_md: str, module_source: str, trace: Trace, feedback: str
) -> str:
    """The first user message of a revision."""
    return (
        "# Requirements, as numbered rules\n\n"
        f"{_rules_block(rules)}\n\n"
        "# Context\n\n"
        f"{context_md.strip() or '(none given)'}\n\n"
        "# The current spec module\n\n"
        f"```python\n{module_source.rstrip()}\n```\n\n"
        "# The current trace\n\n"
        f"```json\n{_trace_json(trace)}\n```\n\n"
        "# Feedback on this draft\n\n"
        f"{feedback.strip() or '(none)'}\n\n"
        "# Task\n\n"
        "Revise the spec and its trace to address the feedback, following the drafting "
        "rules. The rule ids are fixed by the requirements: keep them, and keep every "
        "question id (set its `answer` when the feedback settles it). Change only what "
        "the feedback needs; keep every other scenario, invariant and name as it is. "
        "Answer with the complete revised module and the complete trace.\n"
    )


def repair_message(problems: Sequence[str]) -> str:
    """The user message of a repair round: the exact problems of the last answer."""
    shown = list(problems[:MAX_PROBLEMS_SHOWN])
    if len(problems) > len(shown):
        shown.append(f"... and {len(problems) - len(shown)} more")
    listed = "\n".join(f"- {p}" for p in shown)
    return (
        f"Your draft has {len(problems)} problem(s):\n\n{listed}\n\n"
        "Answer again with the complete corrected module (```python) and the complete "
        "trace (```json). Fix exactly these problems; keep everything else, the rule ids "
        "and the question ids as they are.\n"
    )


def _canonical(module_source: str | None, trace: Trace | None) -> str:
    """The current draft as one assistant answer (what a repair round shows back)."""
    parts = []
    if module_source is not None:
        parts.append(f"```python\n{module_source.rstrip()}\n```")
    if trace is not None:
        parts.append(f"```json\n{_trace_json(trace)}\n```")
    return "\n\n".join(parts) or "(no answer)"


# ---- parsing the answer --------------------------------------------------------------------

_FENCE = re.compile(r"^[ \t]*```[ \t]*([^\n`]*)\n(.*?)^[ \t]*```[ \t]*$", re.DOTALL | re.MULTILINE)
_TRACE_KEYS = frozenset({"links", "unchecked", "context", "questions"})
_DEFINES_SPEC = re.compile(r"^SPEC\s*(?::[^=\n]*)?=", re.MULTILINE)


def _json_trace(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) and set(value) & _TRACE_KEYS else None


def _bare_json(text: str) -> dict | None:
    """A trace object written outside any fence: the first decodable ``{...}`` with a
    trace key."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(value, dict) and set(value) & _TRACE_KEYS:
            return value
    return None


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def parse_answer(text: str) -> tuple[str | None, dict | None]:
    """(module source, trace object) of a model answer; None for a part not found.

    The module is the fenced Python block defining ``SPEC`` (```python / ```py, or an
    untagged block), else the first Python block. The trace is a fenced block holding a
    JSON object with a trace key (``links``, ``unchecked``, ``context``, ``questions``),
    else such an object written outside the fences. Either may come first."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [(info.strip().lower(), body) for info, body in _FENCE.findall(normalized)]
    python = [b for i, b in blocks if i in ("python", "py", "python3")]
    untagged_python = [b for i, b in blocks if i == "" and _DEFINES_SPEC.search(b) and _parses(b)]
    candidates = python + untagged_python
    module = next((b for b in candidates if _DEFINES_SPEC.search(b)), None)
    if module is None and python:
        module = python[0]
    trace = None
    for info, body in blocks:
        if info in ("json", "") or info.startswith("json"):
            trace = _json_trace(body)
            if trace is not None:
                break
    if trace is None:
        outside = _FENCE.sub("", normalized)
        trace = _bare_json(outside)
    return module, trace


# ---- importing and checking ----------------------------------------------------------------


def load_spec(module_source: str) -> tuple[Spec | None, list[str]]:
    """Import `module_source` in isolation (a temporary directory, a unique module
    name) and answer its ``SPEC``, or None and the problems that stopped it."""
    name = f"lattice_spec_draft_{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="lattice-draft-") as tmp:
        path = Path(tmp) / f"{name}.py"
        path.write_text(module_source)
        loader_spec = importlib.util.spec_from_file_location(name, path)
        assert loader_spec is not None and loader_spec.loader is not None
        module = importlib.util.module_from_spec(loader_spec)
        sys.modules[name] = module
        try:
            loader_spec.loader.exec_module(module)
        except SyntaxError as exc:
            return None, [f"import: SyntaxError: {exc.msg} (line {exc.lineno})"]
        except (Exception, SystemExit) as exc:  # noqa: BLE001 -- any failure is a problem
            line = next(
                (
                    f.lineno
                    for f in reversed(traceback.extract_tb(exc.__traceback__))
                    if f.filename == str(path)
                ),
                None,
            )
            where = f" (line {line})" if line else ""
            return None, [f"import: {type(exc).__name__}: {exc}{where}"]
        finally:
            sys.modules.pop(name, None)
    spec = getattr(module, "SPEC", None)
    if spec is None:
        return None, ["import: the module defines no SPEC"]
    if not isinstance(spec, Spec):
        return None, [f"import: SPEC is a {type(spec).__name__}, not a lattice.spec.Spec"]
    return spec, []


def build_trace(
    raw: Mapping[str, Any] | None, rules: Sequence[Rule], source: str
) -> tuple[Trace, list[str]]:
    """A `Trace` of `rules` from the model's trace object, and its shape problems. The
    rules (ids, texts) are always `rules`, whatever the object says."""
    trace = Trace(source=source, rules=list(rules))
    if raw is None:
        return trace, [
            "trace: no trace JSON object found (a ```json block with links, "
            "unchecked, context and questions)"
        ]
    problems: list[str] = []
    links = raw.get("links", {})
    if isinstance(links, Mapping):
        for rule, targets in links.items():
            if isinstance(targets, str):
                targets = [targets]
            if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
                problems.append(f'trace: links[{rule!r}] is not a list of "kind:name" texts')
                continue
            trace.links[str(rule)] = list(targets)
    else:
        problems.append("trace: links is not an object (rule id -> [targets])")
    unchecked = raw.get("unchecked", {})
    if isinstance(unchecked, Mapping):
        trace.unchecked = {str(k): str(v) for k, v in unchecked.items()}
    else:
        problems.append("trace: unchecked is not an object (rule id -> reason)")
    context = raw.get("context", [])
    if isinstance(context, list):
        trace.context = [str(r) for r in context]
    else:
        problems.append("trace: context is not a list of rule ids")
    questions = raw.get("questions", [])
    if not isinstance(questions, list):
        problems.append("trace: questions is not a list")
        questions = []
    for n, q in enumerate(questions, 1):
        if not isinstance(q, Mapping) or not all(
            isinstance(q.get(k), str) and q.get(k) for k in ("id", "text", "default")
        ):
            problems.append(f"trace: question {n} needs an id, a text and a default (texts)")
            continue
        q_rules = q.get("rules") or []
        answer = q.get("answer")
        trace.questions.append(
            Question(
                q["id"],
                q["text"],
                q["default"],
                [str(r) for r in q_rules] if isinstance(q_rules, list) else [],
                answer if isinstance(answer, str) else None,
            )
        )
    return trace, problems


def _link_problem(target: str, spec: Spec) -> str | None:
    """Why `target` names no element of `spec`, or None."""
    kind, sep, name = target.partition(":")
    if not sep or kind not in LINK_KINDS or not name:
        return f"is not kind:name with kind one of {', '.join(LINK_KINDS)}"
    if kind == "scenario":
        names = [s.name for s in spec.scenarios]
        if name not in names:
            return "names no scenario of SPEC"
    elif kind == "invariant":
        if name not in [i.name for i in spec.invariants]:
            known = ", ".join(i.name for i in spec.invariants) or "none"
            return f"names no invariant of SPEC (invariants: {known})"
    elif kind == "service":
        if name not in spec.service_names():
            known = ", ".join(spec.service_names()) or "none"
            return f"names no service of SPEC (services: {known})"
    elif kind == "machine":
        table, _, column = name.partition(".")
        found = resolve_table(spec.profile, table)
        for m in spec.machines:
            m_found = resolve_table(spec.profile, m.table)
            if found and m_found and m_found[0] == found[0] and m.column == column:
                return None
        known = ", ".join(f"{m.table}.{m.column}" for m in spec.machines) or "none"
        return f"names no machine of SPEC (machines: {known})"
    elif kind == "profile":
        table, _, column = name.partition(".")
        found = resolve_table(spec.profile, table)
        if found is None:
            return "names no table of the profile"
        if column and column not in found[2]:
            return f"names no column of {found[0]}"
    return None


def check_trace(trace: Trace, spec: Spec | None) -> list[str]:
    """The trace's problems against the rules and `spec` (links are checked only when
    the spec imported)."""
    problems: list[str] = []
    ids = [r.id for r in trace.rules]
    known = set(ids)
    places: dict[str, list[str]] = {}
    for rule, targets in trace.links.items():
        if targets:
            places.setdefault(rule, []).append("links")
    for rule in trace.unchecked:
        places.setdefault(rule, []).append("unchecked")
    for rule in trace.context:
        places.setdefault(rule, []).append("context")
    for rule in places:
        if rule not in known:
            problems.append(f"trace: {rule} is not a rule id (rules: R1..R{len(ids)})")
    for r in trace.untraced():
        problems.append(
            f"trace: {r.id} is not traced (no link, unchecked reason or context): {r.text!r}"
        )
    for rule, where in places.items():
        if rule in known and len(where) > 1:
            problems.append(f"trace: {rule} is in {' and '.join(where)}; put it in exactly one")
    seen_q: set[str] = set()
    for q in trace.questions:
        if q.id in seen_q:
            problems.append(f"trace: two questions are numbered {q.id}")
        seen_q.add(q.id)
        for rule in q.rules:
            if rule not in known:
                problems.append(f"trace: question {q.id} names {rule}, which is not a rule id")
    if spec is None:
        return problems
    for rule, targets in trace.links.items():
        for target in targets:
            why = _link_problem(target, spec)
            if why is not None:
                problems.append(f"trace: {rule} links {target!r}, which {why}")
    notes = " ".join(u.text for u in spec.unchecked)
    for rule in trace.unchecked:
        if rule in known and not re.search(rf"\b{re.escape(rule)}\b", notes):
            problems.append(
                f"trace: {rule} is unchecked but SPEC has no Unchecked note citing it "
                f'(add Unchecked("{rule}: <what is not checked and why>", ...))'
            )
    return problems


def evaluate(
    module_source: str | None,
    raw_trace: Mapping[str, Any] | None,
    rules: Sequence[Rule],
    source: str,
) -> tuple[Spec | None, Trace, list[str]]:
    """Import the module, validate its SPEC and check the trace: (SPEC or None, the
    trace, every problem; empty when the draft is valid)."""
    problems: list[str] = []
    spec: Spec | None = None
    if module_source is None:
        problems.append("module: no ```python block defining SPEC found")
    else:
        spec, problems = load_spec(module_source)
    if spec is not None:
        try:
            problems += [p.render() for p in validate(spec)]
        except Exception as exc:  # noqa: BLE001 -- a malformed spec can crash validate
            problems.append(f"validate: {type(exc).__name__}: {exc}")
    trace, shape = build_trace(raw_trace, rules, source)
    problems += shape
    problems += check_trace(trace, spec)
    return spec, trace, problems


# ---- the loop ------------------------------------------------------------------------------


@dataclass
class DraftUsage:
    """Tokens, cost and time of every generator call of a draft."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float | None = None
    cost_basis: str | None = None
    latency_s: float = 0.0

    def add(self, gen: Generation | GenerationRefused) -> None:
        self.calls += 1
        self.input_tokens += gen.usage.input_tokens
        self.output_tokens += gen.usage.output_tokens
        self.cache_read_input_tokens += gen.usage.cache_read_input_tokens
        self.cache_creation_input_tokens += gen.usage.cache_creation_input_tokens
        self.latency_s += gen.latency_s
        if gen.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + gen.cost_usd
            self.cost_basis = gen.cost_basis

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    def as_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "total_input_tokens": self.total_input_tokens}


@dataclass
class DraftResult:
    """A drafted (or revised) spec: the module, its trace, the problems left (empty when
    valid), the number of model calls, their usage, and the conversation."""

    module_source: str
    trace: Trace
    problems: list[str]
    rounds: int
    usage: DraftUsage
    transcript: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    spec: Spec | None = field(default=None, repr=False)
    # an incremental draft (lattice.spec.incremental.draft_since) only: the rule diff
    # (a RuleDiff), the base spec's elements added / removed / modified, and notes
    diff: Any = None
    changed_targets: dict[str, list[str]] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems


def _loop(
    first: str,
    *,
    rules: Sequence[Rule],
    source: str,
    generator: Generator,
    model: str,
    max_repairs: int,
    module_source: str | None = None,
    trace: Trace | None = None,
    keep_questions: Sequence[str] = (),
    post: Callable[[Spec | None, Trace, Mapping[str, Any] | None], list[str]] | None = None,
) -> DraftResult:
    """The draft / repair loop. `post` (optional) checks each answer further: it gets
    the imported SPEC (or None), the trace and the raw trace object, may complete the
    trace in place, and answers more problems."""
    system = system_prompt()
    usage = DraftUsage()
    transcript: list[dict[str, Any]] = []
    messages = [{"role": "user", "content": first}]
    spec: Spec | None = None
    current_trace = trace or Trace(source=source, rules=list(rules))
    problems: list[str] = []
    rounds = 0
    while True:
        rounds += 1
        transcript.append({"round": rounds, "role": "user", "content": messages[-1]["content"]})
        try:
            gen = generator.generate(system, messages)
        except GenerationRefused as refused:
            usage.add(refused)
            problems = [f"model: refused to answer ({refused})"]
            transcript.append({"round": rounds, "role": "error", "content": str(refused)})
            break
        usage.add(gen)
        transcript.append(
            {
                "round": rounds,
                "role": "assistant",
                "content": gen.text,
                "stop_reason": gen.stop_reason,
            }
        )
        new_module, raw = parse_answer(gen.text)
        notes = []
        if new_module is None and module_source is not None:
            new_module = module_source  # a repair answer may leave the module as it was
            notes.append("module")
        if raw is None and rounds > 1:
            raw = json.loads(_trace_json(current_trace))
            notes.append("trace")
        module_source = new_module
        spec, current_trace, problems = evaluate(module_source, raw, rules, source)
        if post is not None:
            problems += post(spec, current_trace, raw)
        kept = {q.id for q in current_trace.questions}
        problems += [
            f"trace: question {q} was dropped; keep it (set its answer if settled)"
            for q in keep_questions
            if q not in kept
        ]
        transcript.append(
            {"round": rounds, "role": "check", "problems": list(problems), "kept_unchanged": notes}
        )
        if not problems or rounds > max_repairs:
            break
        messages = [
            messages[0],
            {"role": "assistant", "content": _canonical(module_source, current_trace)},
            {"role": "user", "content": repair_message(problems)},
        ]
    return DraftResult(
        module_source=module_source or "",
        trace=current_trace,
        problems=problems,
        rounds=rounds,
        usage=usage,
        transcript=transcript,
        model=model,
        spec=spec,
    )


def draft(
    requirements_md: str,
    *,
    context_md: str = "",
    generator: Generator,
    model: str,
    max_repairs: int = 3,
    source: str = "REQUIREMENTS.md",
) -> DraftResult:
    """Draft a spec module and its trace from `requirements_md` (see the module
    docstring). `model` is recorded in the result (the generator is already bound to
    one); `source` is the trace's ``source``."""
    rules = extract_rules(requirements_md)
    return _loop(
        draft_message(rules, context_md),
        rules=rules,
        source=source,
        generator=generator,
        model=model,
        max_repairs=max_repairs,
    )


def revise(
    module_source: str,
    trace: Trace,
    feedback: str,
    *,
    requirements_md: str,
    context_md: str = "",
    generator: Generator,
    model: str,
    max_repairs: int = 3,
    source: str | None = None,
) -> DraftResult:
    """Revise an existing draft with `feedback` (e.g. a lint report): the same loop as
    :func:`draft`, starting from `module_source` and `trace`. The rule ids are those of
    `requirements_md`; every question id of `trace` must survive."""
    rules = extract_rules(requirements_md)
    src = source or trace.source
    start = Trace(
        src,
        list(rules),
        dict(trace.links),
        dict(trace.unchecked),
        list(trace.context),
        list(trace.questions),
    )
    first = revise_message(rules, context_md, module_source, start, feedback)
    return _loop(
        first,
        rules=rules,
        source=src,
        generator=generator,
        model=model,
        max_repairs=max_repairs,
        module_source=module_source,
        trace=start,
        keep_questions=[q.id for q in trace.questions],
    )


# ---- the CLI -------------------------------------------------------------------------------


def draft_json_path(out: Path | str) -> Path:
    """Where the draft record of the spec module `out` lives (``<stem>.draft.json``)."""
    p = Path(out)
    return p.with_name(p.stem + ".draft.json")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec draft",
        description="Draft a Lattice spec module from markdown requirements.",
    )
    p.add_argument("requirements", type=Path, help="the owner's requirements (markdown)")
    p.add_argument("--context", type=Path, help="context markdown: stack, tables, seed, services")
    p.add_argument("--out", type=Path, required=True, help="the spec module to write")
    p.add_argument("--model", default="opus", help="Claude model (alias or id); default opus")
    p.add_argument("--max-repairs", type=int, default=3)
    p.add_argument("--revise", action="store_true", help="revise the existing --out draft")
    p.add_argument("--feedback", type=Path, help="feedback for --revise (e.g. lint.json)")
    p.add_argument("--effort", default="high", help="CLI effort level ('' for none)")
    p.add_argument("--max-tokens", type=int, default=64000)
    p.add_argument("--timeout", type=float, default=1200.0, help="seconds per model call")
    inc = p.add_argument_group(
        "incremental", "draft only the change since OLD.md (lattice.spec.incremental)"
    )
    inc.add_argument("--since", type=Path, help="the old requirements the base spec drafts")
    inc.add_argument("--base", type=Path, help="the base spec module (its trace beside it)")
    inc.add_argument("--impact", action="store_true", help="run the new spec on the app")
    inc.add_argument("--app", help="--impact: module:factory, (db_path) -> app")
    inc.add_argument("--db", help="--impact: module:build_db, (path) or (variant, path)")
    inc.add_argument("--login", help="--impact: module:fn, (client, handle) -> token")
    inc.add_argument("--runtime", help="--impact: module:RUNTIME, 'process' or 'go'")
    inc.add_argument("--services", help="--impact: module:SERVICES")
    from lattice.spec.engines import database_names

    inc.add_argument("--database", default="sqlite", choices=database_names())
    inc.add_argument("--database-url", help="--impact: an existing database server")
    inc.add_argument("--race", action="store_true", help="--impact: layer X too")
    return p


def _client(args: argparse.Namespace) -> Generator:
    from lattice.llm.cli_client import ClaudeCliClient

    return ClaudeCliClient(
        model=args.model, max_tokens=args.max_tokens, effort=args.effort, timeout=args.timeout,
        full_text=True,
    )


def main(argv: list[str], *, generator: Generator | None = None) -> int:
    """``draft REQUIREMENTS.md --out spec_gen.py ...`` (see the module docstring). A
    leading ``draft`` word is skipped. `generator` replaces the CLI client (tests)."""
    argv = list(argv)
    if argv and argv[0] == "draft":
        argv = argv[1:]
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if args.revise and args.feedback is None:
        print("draft: --revise needs --feedback FILE", file=sys.stderr)
        return 2
    if args.since is not None or args.base is not None:
        if args.since is None or args.base is None or args.revise:
            print("draft: --since OLD.md needs --base BASE.py (and no --revise)", file=sys.stderr)
            return 2
        from lattice.spec.incremental import run_cli

        return run_cli(args, generator if generator is not None else _client(args))
    try:
        requirements = args.requirements.read_text()
        context = args.context.read_text() if args.context else ""
        old_module = old_trace = feedback = None
        if args.revise:
            old_module = args.out.read_text()
            old_trace = Trace.load(trace_path(args.out))
            feedback = args.feedback.read_text()
    except OSError as exc:
        print(f"draft: {exc}", file=sys.stderr)
        return 2
    gen = generator if generator is not None else _client(args)
    source = str(args.requirements)
    if args.revise:
        assert old_module is not None and old_trace is not None and feedback is not None
        result = revise(
            old_module,
            old_trace,
            feedback,
            requirements_md=requirements,
            context_md=context,
            generator=gen,
            model=args.model,
            max_repairs=args.max_repairs,
            source=source,
        )
    else:
        result = draft(
            requirements,
            context_md=context,
            generator=gen,
            model=args.model,
            max_repairs=args.max_repairs,
            source=source,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result.module_source.rstrip() + "\n")
    result.trace.save(trace_path(args.out))
    record = {
        "mode": "revise" if args.revise else "draft",
        "model": args.model,
        "valid": result.valid,
        "rounds": result.rounds,
        "problems": result.problems,
        "usage": result.usage.as_dict(),
        "rules": len(result.trace.rules),
        "questions": [dataclasses.asdict(q) for q in result.trace.questions],
        "transcript": result.transcript,
    }
    draft_json_path(args.out).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    state = "valid" if result.valid else f"{len(result.problems)} problem(s)"
    cost = result.usage.cost_usd
    print(
        f"draft: {args.out} -- {state}, {result.rounds} round(s), "
        f"{len(result.trace.questions)} question(s), "
        f"{result.usage.total_input_tokens} in / {result.usage.output_tokens} out tokens"
        + (f", ${cost:.4f}" if cost is not None else "")
    )
    for p in result.problems:
        print(f"  {p}")
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
