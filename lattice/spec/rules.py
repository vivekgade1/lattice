"""The owner's requirements as numbered rules, and the trace that links a drafted spec to them.

`extract_rules` splits a markdown requirements document into rule units, deterministically:
every list item (at any depth) is one rule, and every non-empty paragraph line outside a
list is one rule; headings are not rules but name the section of the rules under them.
Ids are ``R1``, ``R2``, ... in document order, so the same document always gives the same ids.

A **trace** (`Trace`, stored as JSON next to a drafted spec, ``<spec module>.trace.json``)
records, for each rule, the spec elements that check it, and the open questions the draft
raised:

    {
      "source": "REQUIREMENTS.md",
      "rules": [{"id": "R1", "text": "...", "section": "Ticket 1 > Login", "line": 12}],
      "links": {"R1": ["scenario:login_answers_a_token", "invariant:one_account_per_email"]},
      "unchecked": {"R7": "why it cannot be checked"},
      "context": ["R3"],
      "questions": [{"id": "Q1", "text": "...", "default": "...", "rules": ["R4"],
                     "answer": null}]
    }

A link target is ``<kind>:<name>`` with kind one of ``scenario``, ``invariant``,
``machine`` (``Table.column``), ``service``, ``profile`` (a schema or column fact, e.g.
``profile:Order.status``). ``unchecked`` maps a rule to the reason it is left to an
`Unchecked` note; ``context`` lists rules that state no checkable behaviour (an
introduction, the stack). Every rule is in exactly one of links, unchecked or context --
`Trace.untraced()` lists the ones that are in none.

An incremental draft (`lattice.spec.incremental.draft_since`: the requirements edited
since a base spec) records what the change did to the base trace: ``replaces`` maps a
new rule id to the old rule id (of the base trace) whose checks it rewrites;
``retired`` maps an old rule id the change removed to the reason; ``retired_targets``
lists the base spec's elements (``kind:name``) the change deleted -- e.g.
``"replaces": {"R5": "R4"}, "retired": {"R6": "removed from the requirements"},
"retired_targets": ["scenario:old_rule_case"]``. All three are empty for a draft from
scratch, and a trace JSON without them (written before they existed) loads with them
empty.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

LINK_KINDS = ("scenario", "invariant", "machine", "service", "profile")

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    section: str
    line: int


def extract_rules(markdown: str) -> list[Rule]:
    """The rule units of `markdown`, in order (see the module docstring)."""
    rules: list[Rule] = []
    heads: list[str] = []
    in_code = False
    last = -1  # the line the previous rule ended on
    prev_item = False  # whether the previous rule is a list item
    for n, raw in enumerate(markdown.splitlines(), start=1):
        if raw.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not raw.strip():
            continue
        h = _HEADING.match(raw)
        if h:
            depth = len(h.group(1))
            heads = heads[: depth - 1] + [h.group(2)]
            last = -1
            continue
        m = _ITEM.match(raw)
        text = m.group(2) if m else raw.strip()
        joins = (not m and rules and last == n - 1
                 and (not prev_item or raw.startswith((" ", "\t"))))
        if joins:
            # a wrapped continuation of the previous item, or the next line of a paragraph
            prev = rules.pop()
            rules.append(Rule(prev.id, f"{prev.text} {text}", prev.section, prev.line))
        else:
            rules.append(Rule(f"R{len(rules) + 1}", text, " > ".join(heads), n))
            prev_item = bool(m)
        last = n
    return rules


@dataclass
class Question:
    id: str
    text: str
    default: str
    rules: list[str] = field(default_factory=list)
    answer: str | None = None


@dataclass
class Trace:
    source: str
    rules: list[Rule]
    links: dict[str, list[str]] = field(default_factory=dict)
    unchecked: dict[str, str] = field(default_factory=dict)
    context: list[str] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    # incremental drafts only (see the module docstring); keyed by new / old rule ids
    replaces: dict[str, str] = field(default_factory=dict)
    retired: dict[str, str] = field(default_factory=dict)
    retired_targets: list[str] = field(default_factory=list)

    def untraced(self) -> list[Rule]:
        """Rules that no spec element checks, no unchecked reason covers, and that are
        not marked as context."""
        covered = {r for r, ts in self.links.items() if ts} | set(self.unchecked) | set(
            self.context
        )
        return [r for r in self.rules if r.id not in covered]

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Trace:
        d = json.loads(text)
        return cls(
            source=d["source"],
            rules=[Rule(**r) for r in d["rules"]],
            links={k: list(v) for k, v in d.get("links", {}).items()},
            unchecked=dict(d.get("unchecked", {})),
            context=list(d.get("context", [])),
            questions=[Question(**q) for q in d.get("questions", [])],
            replaces={str(k): str(v) for k, v in (d.get("replaces") or {}).items()},
            retired={str(k): str(v) for k, v in (d.get("retired") or {}).items()},
            retired_targets=[str(t) for t in d.get("retired_targets") or []],
        )

    def save(self, path: Path | str) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def load(cls, path: Path | str) -> Trace:
        return cls.from_json(Path(path).read_text())


def trace_path(spec_file: Path | str) -> Path:
    """Where the trace of the spec module `spec_file` lives."""
    p = Path(spec_file)
    return p.with_name(p.stem + ".trace.json")
