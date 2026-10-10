"""The Lattice Studio server: the web UI of ``lattice/studio/web/`` and the JSON API of
``lattice/studio/API.md`` (v1 and its v1.1 additions), over one project directory.

Run it with ``uv run --extra db python -m lattice.studio PROJECT_DIR [--port N]
[--model sonnet]``. It binds to 127.0.0.1 only.

**The project directory** holds ``REQUIREMENTS.md``, ``spec.py`` (defines ``SPEC``),
``spec.trace.json`` and, optionally, ``studio.toml``::

    [app]                       # the harness of lattice.spec.check, as module:attr refs
    app = "harness:make_app"    # the app factory (db_path) -> app
    db = "harness:build_db"     # writes the seeded database
    login = "harness:login"     # optional: (client, handle) -> token
    login_path = "/login"       # optional
    runtime = "process"         # optional: process | go | module:attr
    services = "harness:SERVICES"  # optional
    database = "sqlite"         # optional: sqlite | postgres | mysql | clickhouse
    database_url = ""           # optional
    cwd = "."                   # optional: the working directory of checks
    sys_path = ["src"]          # optional: more importable dirs (relative to the project)

    [check]
    layers = "ACDX"             # the full check's layers (default ACDX)
    # any other key is passed to lattice.spec.check.check (e.g. app_dir = "app")

    [llm]
    model = "sonnet"            # --model wins
    context = "CONTEXT.md"      # optional: context for incremental drafting
    ask_timeout = 600           # seconds
    session_timeout = 900       # seconds one chat turn may run
    claude_bin = "claude"       # the Claude Code binary of the chat sessions

The project directory and the ``sys_path`` entries are put on ``sys.path``. Without
``[app] app`` and ``[app] db`` the app overlay, ``verify`` and the change impact are off.

**Endpoints** (see API.md for the shapes):

- ``GET /api/model``: the rules (from the trace, with their review status), the markdown,
  ``views`` (``lattice.spec.review``'s ``ReviewData`` as JSON, cells keyed ``row|col``),
  the data model (the profile's tables), the app's last check (findings mapped to targets
  as ``lattice.spec.incremental.label_impact`` does) and the change records. Cached
  until the project's files change, a change is accepted or a check finishes.
- ``POST /api/ask``: ``lattice.spec.ask.ask`` in a worker thread with a timeout.
- ``POST /api/changes``: a change record and a background job -- the LLM edits the
  markdown, ``draft_since`` drafts the change, then validate / lint / L011 conflicts /
  impact, the view diff, the markdown diff, the rule diff and the new questions.
  ``GET /api/changes/{id}`` polls it, ``DELETE`` cancels it, ``.../accept`` writes the
  three files and commits them, ``.../reject`` drops it.
- ``POST /api/check``: the full check of the current spec against the app, in the
  background.
- ``POST /api/session``, ``POST /api/session/{id}/message``, ``GET /api/session/{id}/events``,
  ``DELETE /api/session/{id}``, ``GET /api/session``: the chat as a real Claude Code
  session (lattice/studio/session.py; API.md v1.5). Lazy: no ``claude`` call until a message.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import dataclasses
import difflib
import hashlib
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from lattice.llm.client import Generator
from lattice.spec.ask import VerifyConfig, ask, build_digest
from lattice.spec.draft import load_spec
from lattice.spec.incremental import (
    changed_targets,
    diff_rules,
    draft_since,
    impact,
    label_impact,
)
from lattice.spec.lint import conflicts, lint
from lattice.spec.model import Spec, resolve_table, tables_of
from lattice.spec.report import _detail_lines
from lattice.spec.review import AccessGroup, ReviewData, _build
from lattice.spec.rules import Trace, extract_rules
from lattice.studio.session import (
    DEFAULT_SESSION_TIMEOUT,
    SessionError,
    SessionManager,
    claude_available,
)

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"

REQUIREMENTS = "REQUIREMENTS.md"
SPEC_FILE = "spec.py"
TRACE_FILE = "spec.trace.json"
CONFIG_FILE = "studio.toml"
PROJECT_FILES = (REQUIREMENTS, SPEC_FILE, TRACE_FILE)

DEFAULT_MODEL = "sonnet"
DEFAULT_LAYERS = "ACDX"
DEFAULT_ASK_TIMEOUT = 600.0
LAYER_KEYS = ("A", "B", "C", "D", "X")

COMMIT_TRAILER = (
    "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
    "Claude-Session: https://claude.ai/code/session_01DkfBU2ur5VhLPgqfejszsZ"
)

LIVE_STATUSES = ("drafting", "checking")


def log(msg: str) -> None:
    print(f"[studio {datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApiError(Exception):
    """An error answered as ``{"detail": ...}`` with `status`."""

    def __init__(self, status: HTTPStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---- studio.toml ----------------------------------------------------------------------------


@dataclasses.dataclass
class StudioConfig:
    """The parsed ``studio.toml`` (see the module docstring)."""

    app: dict[str, Any] = dataclasses.field(default_factory=dict)
    check: dict[str, Any] = dataclasses.field(default_factory=dict)
    llm: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> StudioConfig:
        path = root / CONFIG_FILE
        if not path.is_file():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(
            dict(data.get("app") or {}), dict(data.get("check") or {}), dict(data.get("llm") or {})
        )

    @property
    def configured(self) -> bool:
        return bool(self.app.get("app") and self.app.get("db"))

    @property
    def layers(self) -> str:
        return str(self.check.get("layers") or DEFAULT_LAYERS)

    @property
    def check_options(self) -> dict[str, Any]:
        return {k: v for k, v in self.check.items() if k != "layers"}


class Harness:
    """The app hookup of ``[app]``, resolved lazily (imports happen on first use)."""

    def __init__(self, root: Path, config: StudioConfig) -> None:
        self.root = root
        self.config = config
        self._resolved: dict[str, Any] | None = None
        cwd = config.app.get("cwd")
        self.cwd = (root / cwd).resolve() if cwd else None

    def resolve(self) -> dict[str, Any]:
        if self._resolved is None:
            from lattice.spec.__main__ import _runtime, _services, load

            a = self.config.app
            self._resolved = {
                "app_factory": load(a["app"]),
                "db_factory": load(a["db"]),
                "login": load(a["login"]) if a.get("login") else None,
                "login_path": a.get("login_path") or None,
                "runtime": _runtime(a.get("runtime") or None),
                "services": _services(a.get("services") or None),
                "database": a.get("database") or "sqlite",
                "database_url": a.get("database_url") or None,
            }
        return self._resolved

    def check_kwargs(self) -> dict[str, Any]:
        r = self.resolve()
        kw = {
            k: r[k]
            for k in ("login", "login_path", "runtime", "services", "database", "database_url")
        }
        return {**kw, **self.config.check_options}

    def verify_config(self) -> VerifyConfig:
        r = self.resolve()
        return VerifyConfig(
            app=r["app_factory"],
            db=r["db_factory"],
            login=r["login"],
            login_path=r["login_path"],
            runtime=r["runtime"],
            services=r["services"],
            database=r["database"],
            database_url=r["database_url"],
            options=self.config.check_options,
        )


# ---- the project on disk --------------------------------------------------------------------


@dataclasses.dataclass
class Snapshot:
    """The project's three files, read together, and the spec they define."""

    markdown: str
    module: str
    trace: Trace
    spec: Spec
    hash: str


def files_hash(root: Path) -> str:
    h = hashlib.sha256()
    for name in PROJECT_FILES:
        p = root / name
        h.update(name.encode() + b"\0")
        h.update(p.read_bytes() if p.is_file() else b"<missing>")
        h.update(b"\0")
    return h.hexdigest()


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60
    )


def git_head(root: Path) -> str:
    try:
        r = git(root, "rev-parse", "--short", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def is_git_repo(root: Path) -> bool:
    try:
        r = git(root, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


# ---- JSON views -----------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """`value` as JSON data: dataclasses as objects, an AccessGroup's cells keyed
    ``row|col``, tuples as lists."""
    if isinstance(value, AccessGroup):
        return {
            "prefix": value.prefix,
            "columns": list(value.columns),
            "rows": list(value.rows),
            "cells": {f"{r}|{c}": v for (r, c), v in value.cells.items()},
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def review_data(
    spec: Spec, trace: Trace, title: str, lint_items: list[dict] | None = None
) -> ReviewData:
    return _build(spec, trace, lint_items, None, title)


def _bounds(p: Any) -> str | None:
    bits = []
    if p.min_len is not None and p.max_len is not None:
        bits.append(f"{p.min_len}-{p.max_len} chars")
    elif p.min_len is not None:
        bits.append(f">= {p.min_len} chars")
    elif p.max_len is not None:
        bits.append(f"<= {p.max_len} chars")
    for op, v in ((">=", p.ge), (">", p.gt), ("<=", p.le), ("<", p.lt)):
        if v is not None:
            bits.append(f"{op} {v:g}" if isinstance(v, float) else f"{op} {v}")
    return ", ".join(bits) or None


def data_model(spec: Spec) -> dict[str, Any]:
    """The profile's tables: columns with type, nullable, one_of, unique, private and
    bounds (from the contracts' parameters stored in that column)."""
    tables, sql_names = tables_of(spec.profile)
    bounds: dict[tuple[str, str], str] = {}
    for c in spec.contracts:
        for p in c.params:
            if not p.column or "." not in p.column:
                continue
            table, _, col = p.column.partition(".")
            found = resolve_table(spec.profile, table)
            b = _bounds(p)
            if found and b:
                bounds.setdefault((found[0], col), b)
    out = []
    for name, schema in tables.items():
        single = {k[0] for k in schema.keys if len(k) == 1}
        one_of = dict(schema.one_of)
        cols = []
        for col, scalar in schema.columns:
            nullable = col in schema.nullable
            b = bounds.get((name, col))
            if b and nullable:
                b += " or null"
            cols.append(
                {
                    "name": col,
                    "type": str(getattr(scalar, "value", scalar)).lower(),
                    "nullable": nullable,
                    "one_of": list(one_of[col]) if col in one_of else None,
                    "unique": col in single,
                    "private": col in schema.private,
                    "bounds": b,
                }
            )
        out.append({"name": name, "sql": sql_names.get(name, name), "columns": cols})
    return {"tables": out}


def rules_json(trace: Trace, data: ReviewData) -> list[dict[str, Any]]:
    rows = {r.id: r for r in data.rule_rows}
    qs: dict[str, list[str]] = {}
    for q in trace.questions:
        for rid in q.rules:
            qs.setdefault(rid, []).append(q.id)
    out = []
    for r in trace.rules:
        row = rows.get(r.id)
        status = row.status if row else "untraced"
        out.append(
            {
                "id": r.id,
                "text": r.text,
                "section": r.section,
                "line": r.line,
                "status": status,
                "links": list(trace.links.get(r.id, [])),
                "questions": qs.get(r.id, []),
                "unchecked_reason": (row.note or None) if row else None,
            }
        )
    return out


def element_targets(spec: Spec) -> list[str]:
    """The scenario, invariant, machine and service targets of `spec`, as the views
    name them."""
    out = [f"scenario:{s.name}" for s in spec.scenarios]
    out += [f"invariant:{i.name}" for i in spec.invariants]
    out += [f"machine:{m.table}.{m.column}" for m in spec.machines]
    out += [f"service:{s.name}" for s in spec.services]
    return out


def _resolved(spec: Spec, target: str) -> str:
    kind, _, name = target.partition(":")
    if kind not in ("machine", "profile"):
        return target
    table, dot, col = name.partition(".")
    found = resolve_table(spec.profile, table)
    return f"{kind}:{found[0]}{dot}{col}" if found else target


def finding_message(f: Any) -> str:
    details = "; ".join(_detail_lines(f)[:3])
    return f"{f.summary()}: {details}" if details else f.summary()


def summarize_check(report: Any, spec: Spec, trace: Trace, markdown: str) -> dict[str, Any]:
    """``app.last_check`` from a check `report`: per-target pass / fail (findings mapped
    to targets by `label_impact`, on the identity rule diff; targets without findings
    pass) and the findings with their targets."""
    items = label_impact(report, spec, diff_rules(markdown, markdown), trace)
    view_name = {_resolved(spec, t): t for t in element_targets(spec)}
    targets = {t: "pass" for t in element_targets(spec)}
    by_key: dict[tuple[Any, ...], str] = {}
    for item in items:
        t = view_name.get(item.target, item.target)
        targets[t] = "fail"
        for f in item.findings:
            by_key[(f["layer"], f["case"], f["summary"], f["rule"])] = t
    findings = [
        {
            "layer": f.layer,
            "target": by_key.get((f.layer, f.case, f.summary(), f.rule), ""),
            "message": finding_message(f),
        }
        for f in report.findings
    ]
    counts = report.counts
    return {
        "at": now_iso(),
        "verdict": report.verdict,
        "layers": report.layers,
        "counts": {k: int(counts.get(k, 0)) for k in LAYER_KEYS},
        "targets": targets,
        "findings": findings,
        "notes": list(report.notes),
        "error": None,
    }


# ---- the markdown edit ----------------------------------------------------------------------

BEGIN = "<<<BEGIN REQUIREMENTS.md"
END = "END REQUIREMENTS.md>>>"
_EDITED = re.compile(
    r"^<<<BEGIN REQUIREMENTS\.md[ \t]*\n(.*?)^END REQUIREMENTS\.md>>>[ \t]*$", re.S | re.M
)

EDIT_SYSTEM = f"""\
You edit an application's requirements document (markdown) to make ONE requested change.

The document is a list of rules: every list item and every paragraph line is one rule;
headings name sections. Make the smallest edit that carries out the request:
- change, add or remove only the rule sentences the request needs; write a new rule in the
  document's own style, in the section it belongs to;
- keep every other line byte-identical: headings, order, wording, line wrapping, blank
  lines, code blocks;
- state behaviour concretely, the way the document does (endpoints, callers, statuses,
  limits), so a spec can check it;
- never add commentary, notes, TODOs or change markers to the document.

Reply with the full new document between these two marker lines, and nothing else:
{BEGIN}
...the whole new document...
{END}
"""


def edit_message(
    request: str, markdown: str, selected_lines: Sequence[str], parent: Mapping[str, Any] | None
) -> str:
    parts = ["## The request", "", request.strip(), ""]
    if selected_lines:
        parts += [
            "## What the user pointed at (from the spec, for context)",
            "",
            *selected_lines,
            "",
        ]
    if parent is not None:
        parts += [
            "## A follow-up",
            "",
            f"This request follows up an earlier proposal ({parent['request']!r}); the "
            "document below already includes that proposal. Its open points:",
        ]
        checks = parent.get("checks") or {}
        for c in checks.get("conflicts") or []:
            parts.append(f"- conflict: {c['message']} -- {c['question']}")
        for q in parent.get("questions") or []:
            parts.append(f"- question {q['id']}: {q['text']} (default: {q['default']})")
        parts.append("")
    parts += ["## The current document", "", BEGIN, markdown.rstrip("\n"), END, ""]
    return "\n".join(parts)


def parse_edit(text: str, old_md: str) -> tuple[str | None, list[str]]:
    m = _EDITED.search(text.replace("\r\n", "\n"))
    if m is None:
        return None, [f"the reply has no document between the lines {BEGIN!r} and {END!r}"]
    new_md = m.group(1)
    if old_md.endswith("\n") and not new_md.endswith("\n"):
        new_md += "\n"
    problems = []
    if not extract_rules(new_md):
        problems.append("the document has no rules (list items or paragraph lines)")
    elif new_md == old_md or diff_rules(old_md, new_md).empty:
        problems.append("no rule changed; make the change the request asks for")
    return (new_md if not problems else None), problems


def edit_markdown(
    generator: Generator,
    request: str,
    old_md: str,
    selected_lines: Sequence[str],
    parent: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """One model call (and at most one repair) that answers the full new markdown."""
    messages = [{"role": "user", "content": edit_message(request, old_md, selected_lines, parent)}]
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    problems: list[str] = []
    for _ in range(2):
        gen = generator.generate(EDIT_SYSTEM, messages)
        usage["calls"] += 1
        usage["input_tokens"] += gen.usage.total_input_tokens
        usage["output_tokens"] += gen.usage.output_tokens
        new_md, problems = parse_edit(gen.text, old_md)
        if new_md is not None:
            return new_md, usage
        messages += [
            {"role": "assistant", "content": gen.text},
            {
                "role": "user",
                "content": "Your reply cannot be used: "
                + "; ".join(problems)
                + f". Reply with the full new document between {BEGIN!r} and {END!r} only.",
            },
        ]
    raise RuntimeError("the markdown edit failed: " + "; ".join(problems))


# ---- the change's view diff -----------------------------------------------------------------


def _cells(data: ReviewData) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for g in data.access_groups:
        out.update(g.cells)
    return out


def _machine_moves(spec: Spec, data: ReviewData) -> dict[str, tuple[str, str]]:
    """Resolved machine target -> (view target, moves)."""
    out = {}
    for m, (target, _label, moves) in zip(spec.machines, data.section6.machines, strict=False):
        out[_resolved(spec, f"machine:{m.table}.{m.column}")] = (target, moves)
    return out


def view_diff(
    old_spec: Spec,
    old: ReviewData,
    new_spec: Spec,
    new: ReviewData,
    changed: Mapping[str, list[str]],
) -> dict[str, Any]:
    old_cells, new_cells = _cells(old), _cells(new)
    cells = []
    for key in list(old_cells) + [k for k in new_cells if k not in old_cells]:
        a, b = old_cells.get(key, "·"), new_cells.get(key, "·")
        if a != b:
            cells.append({"row": key[0], "col": key[1], "old": a, "new": b})

    def invariants(data: ReviewData, wanted: list[str]) -> list[dict[str, str]]:
        by = {t: (n, s) for t, n, s in data.section6.invariants}
        return [{"target": t, "name": by[t][0], "sentence": by[t][1]} for t in wanted if t in by]

    def scenarios(data: ReviewData, wanted: list[str]) -> list[dict[str, Any]]:
        by = {s.target: s for s in data.section6.scenarios}
        return [
            {"target": t, "name": by[t].name, "steps": list(by[t].steps)} for t in wanted if t in by
        ]

    def kind(k: str, group: str) -> list[str]:
        return [t for t in changed.get(group, []) if t.startswith(k + ":")]

    old_m, new_m = _machine_moves(old_spec, old), _machine_moves(new_spec, new)
    machines = []
    for t in kind("machine", "added") + kind("machine", "removed") + kind("machine", "modified"):
        o, n = old_m.get(t), new_m.get(t)
        if (o and o[1]) == (n and n[1]):
            continue
        machines.append({"target": (n or o)[0], "old": o[1] if o else "", "new": n[1] if n else ""})
    return {
        "cells": cells,
        "invariants": {
            "added": invariants(new, kind("invariant", "added")),
            "removed": invariants(old, kind("invariant", "removed")),
        },
        "machines": {"changed": machines},
        "scenarios": {
            "added": scenarios(new, kind("scenario", "added")),
            "removed": scenarios(old, kind("scenario", "removed")),
            "modified": scenarios(new, kind("scenario", "modified")),
        },
    }


def conflicts_json(spec: Spec, trace: Trace) -> list[dict[str, str]]:
    by_target: dict[str, list[str]] = {}
    for rid, ts in trace.links.items():
        for t in ts:
            by_target.setdefault(t, []).append(rid)
    out = []
    for c in conflicts(spec):
        ta, tb = c.targets
        who = c.user or "no token"
        ra = f" ({', '.join(by_target[ta])})" if by_target.get(ta) else ""
        rb = f" ({', '.join(by_target[tb])})" if by_target.get(tb) else ""
        out.append(
            {
                "a": ta,
                "b": tb,
                "message": f"{who} {c.endpoint}: {c.status_a} vs {c.status_b}",
                "question": (
                    f"{ta.removeprefix('scenario:')}{ra} step {c.step_a} expects {c.status_a} but "
                    f"{tb.removeprefix('scenario:')}{rb} step {c.step_b} expects {c.status_b} for "
                    f"{c.endpoint} as {who} after the same setup. Which one holds?"
                ),
            }
        )
    return out


IMPACT_LABEL = {
    "contradiction": "contradiction",
    "not_built_yet": "not_built_yet",
    "regression": "regression",
    "unlinked": "regression",
}


def impact_json(items: Sequence[Any], last_check: Mapping[str, Any] | None) -> dict[str, Any]:
    was_failing = {t for t, r in ((last_check or {}).get("targets") or {}).items() if r == "fail"}
    failing = []
    for i in items:
        msg = "; ".join(f["summary"] for f in i.findings[:2])
        if i.label == "unlinked":
            msg = f"no rule links this target; {msg}"
        if i.target in was_failing:
            msg += " (it failed in the last check too)"
        failing.append(
            {
                "target": i.target,
                "label": IMPACT_LABEL[i.label],
                "message": msg,
                "rules": list(i.rules),
            }
        )
    return {"verdict": "fail" if failing else "pass", "failing": failing}


def empty_change(cid: str, request: str, parent: str | None) -> dict[str, Any]:
    return {
        "id": cid,
        "request": request,
        "parent": parent,
        "status": "drafting",
        "error": None,
        "markdown_diff": "",
        "rules": {"added": [], "changed": [], "removed": []},
        "targets": {"added": [], "removed": [], "modified": []},
        "view_diff": {
            "cells": [],
            "invariants": {"added": [], "removed": []},
            "machines": {"changed": []},
            "scenarios": {"added": [], "removed": [], "modified": []},
        },
        "checks": {"valid": True, "problems": [], "lint": [], "conflicts": [], "impact": None},
        "questions": [],
        "notes": [],
        "timings": {},
        "usage": {},
        "superseded_by": None,
    }


# ---- the state ------------------------------------------------------------------------------


class Studio:
    """Everything behind the API: the project, the model cache, the change records, the
    check run."""

    def __init__(
        self,
        root: Path,
        *,
        model: str | None = None,
        generator: Generator | None = None,
        generator_factory: Callable[[str], Generator] | None = None,
        claude_bin: str | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = StudioConfig.load(self.root)
        self.model = model or self.config.llm.get("model") or DEFAULT_MODEL
        self._generator = generator
        self._generator_factory = generator_factory
        self.harness = Harness(self.root, self.config) if self.config.configured else None
        self.lock = threading.RLock()
        self.app_lock = threading.Lock()
        self.changes: dict[str, dict[str, Any]] = {}
        self.private: dict[str, dict[str, Any]] = {}  # proposals and bases, per change id
        self.next_id = 1
        self.check_running = False
        self.last_check: dict[str, Any] | None = None
        self._cache: tuple[str, str, dict[str, Any], Snapshot, list[dict]] | None = None
        self.ask_timeout = float(self.config.llm.get("ask_timeout") or DEFAULT_ASK_TIMEOUT)
        self.claude_bin = str(claude_bin or self.config.llm.get("claude_bin") or "claude")
        self.session_timeout = float(
            self.config.llm.get("session_timeout") or DEFAULT_SESSION_TIMEOUT
        )
        self._sessions: SessionManager | None = None
        self.base_url: str | None = None  # set by StudioServer once it is bound
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="studio-ask"
        )
        for p in [self.root, *(self.root / s for s in self.config.app.get("sys_path") or [])]:
            s = str(Path(p).resolve())
            if s not in sys.path:
                sys.path.insert(0, s)

    # -- the generator
    @property
    def generator(self) -> Generator:
        if self._generator is None:
            if self._generator_factory is not None:
                self._generator = self._generator_factory(self.model)
            else:
                from lattice.llm.cli_client import ClaudeCliClient

                self._generator = ClaudeCliClient(
                    model=self.model, max_tokens=32000, effort="", timeout=900, full_text=True
                )
        return self._generator

    @property
    def sessions(self) -> SessionManager:
        """The chat sessions, built on first use (nothing is spawned before a message)."""
        with self.lock:
            if self._sessions is None:
                self._sessions = SessionManager(
                    self.root,
                    model=self.model,
                    claude_bin=self.claude_bin,
                    timeout=self.session_timeout,
                    studio_url=self.base_url,
                )
            return self._sessions

    def close_sessions(self) -> None:
        with self.lock:
            mgr, self._sessions = self._sessions, None
        if mgr is not None:
            mgr.close()

    @contextlib.contextmanager
    def app_context(self) -> Iterator[None]:
        """Runs against the app, one at a time, in ``[app] cwd``."""
        with self.app_lock:
            old = os.getcwd()
            if self.harness and self.harness.cwd:
                os.chdir(self.harness.cwd)
            try:
                yield
            finally:
                os.chdir(old)

    # -- the project
    def snapshot(self) -> Snapshot:
        h = files_hash(self.root)
        with self.lock:
            if self._cache is not None and self._cache[0] == h:
                return self._cache[3]
        return self._read(h)

    def _read(self, h: str) -> Snapshot:
        missing = [n for n in PROJECT_FILES if not (self.root / n).is_file()]
        if missing:
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR, f"the project has no {', '.join(missing)}"
            )
        md = (self.root / REQUIREMENTS).read_text(encoding="utf-8")
        module = (self.root / SPEC_FILE).read_text(encoding="utf-8")
        trace = Trace.from_json((self.root / TRACE_FILE).read_text(encoding="utf-8"))
        spec, errors = load_spec(module)
        if spec is None:
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR, "spec.py does not load: " + "; ".join(errors)
            )
        return Snapshot(md, module, trace, spec, h)

    def invalidate(self) -> None:
        with self.lock:
            self._cache = None

    def get_model(self) -> dict[str, Any]:
        h = files_hash(self.root)
        with self.lock:
            cache = self._cache
        if cache is None or cache[0] != h:
            t0 = time.perf_counter()
            snap = self._read(h)
            lint_items = [f.to_dict() for f in lint(snap.spec, snap.trace)]
            data = review_data(snap.spec, snap.trace, f"{self.root.name} spec review", lint_items)
            views = jsonable(data)
            core = {
                "project": {"name": self.root.name, "root": str(self.root), "git_head": ""},
                "rules": rules_json(snap.trace, data),
                "markdown": snap.markdown,
                "views": views,
                "data_model": data_model(snap.spec),
            }
            with self.lock:
                self._cache = cache = (h, git_head(self.root), core, snap, lint_items)
            log(f"model built in {time.perf_counter() - t0:.2f}s")
        h, head, core, _snap, _lint = cache
        with self.lock:
            out = dict(core)
            out["project"] = {**core["project"], "git_head": head}
            out["app"] = {
                "configured": self.harness is not None,
                "check_running": self.check_running,
                "last_check": self.last_check,
                "session": claude_available(self.claude_bin),
            }
            out["changes"] = [
                {"id": c["id"], "request": c["request"], "status": c["status"]}
                for c in self.changes.values()
            ]
        return out

    def _base_lint(self) -> list[dict]:
        self.get_model()
        with self.lock:
            return list(self._cache[4]) if self._cache else []

    # -- ask
    def ask(self, body: Mapping[str, Any]) -> dict[str, Any]:
        question = str(body.get("question") or "").strip()
        if not question:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "question is empty")
        selected = [str(s) for s in body.get("selected") or []]
        snap = self.snapshot()
        verify = bool(body.get("verify")) and self.harness is not None

        def run() -> dict[str, Any]:
            if verify:
                with self.app_context():
                    cfg = self.harness.verify_config()  # type: ignore[union-attr]
                    a = ask(
                        snap.spec,
                        question,
                        trace=snap.trace,
                        selected=selected,
                        generator=self.generator,
                        model=self.model,
                        verify=cfg,
                    )
            else:
                a = ask(
                    snap.spec,
                    question,
                    trace=snap.trace,
                    selected=selected,
                    generator=self.generator,
                    model=self.model,
                )
            return a.to_dict()

        fut = self.pool.submit(run)
        try:
            out = fut.result(timeout=self.ask_timeout)
        except concurrent.futures.TimeoutError:
            raise ApiError(
                HTTPStatus.GATEWAY_TIMEOUT, f"no answer within {self.ask_timeout:g}s"
            ) from None
        log(f"ask: {out['kind']} in {out['usage'].get('seconds')}s ({question[:60]!r})")
        return out

    # -- changes
    def new_change(self, body: Mapping[str, Any]) -> dict[str, str]:
        request = str(body.get("request") or "").strip()
        if not request:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "request is empty")
        selected = [str(s) for s in body.get("selected") or []]
        parent_id = body.get("parent")
        snap = self.snapshot()
        with self.lock:
            if parent_id is not None:
                parent_id = str(parent_id)
                parent = self.changes.get(parent_id)
                if parent is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, f"no change {parent_id}")
                if parent["status"] != "ready":
                    raise ApiError(
                        HTTPStatus.CONFLICT, f"change {parent_id} is {parent['status']}, not ready"
                    )
                pp = self.private[parent_id]
                base = {
                    "markdown": pp["new_md"],
                    "module": pp["new_module"],
                    "trace": pp["new_trace"],
                }
            else:
                base = {"markdown": snap.markdown, "module": snap.module, "trace": snap.trace}
            cid = f"c{self.next_id}"
            self.next_id += 1
            self.changes[cid] = empty_change(cid, request, parent_id)
            self.private[cid] = {"project_hash": snap.hash, "base": base, "selected": selected}
        threading.Thread(
            target=self._run_change, args=(cid,), name=f"studio-{cid}", daemon=True
        ).start()
        log(f"{cid}: started ({request[:60]!r}{f', parent {parent_id}' if parent_id else ''})")
        return {"id": cid}

    def get_change(self, cid: str) -> dict[str, Any]:
        with self.lock:
            c = self.changes.get(cid)
            if c is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"no change {cid}")
            return json.loads(json.dumps(c))

    def _set(self, cid: str, **fields: Any) -> bool:
        """Update change `cid`; False (and nothing done) when it was cancelled."""
        with self.lock:
            c = self.changes[cid]
            if c["status"] == "cancelled":
                return False
            c.update(fields)
            return True

    def _run_change(self, cid: str) -> None:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        try:
            self._pipeline(cid, timings)
        except Exception as exc:  # noqa: BLE001 -- the record carries any failure
            traceback.print_exc(file=sys.stderr)
            timings["total_s"] = round(time.perf_counter() - t0, 2)
            self._set(cid, status="failed", error=f"{type(exc).__name__}: {exc}", timings=timings)
            log(f"{cid}: failed: {exc}")
            return
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        with self.lock:
            c = self.changes[cid]
            c["timings"] = timings
            if c["status"] == "cancelled":
                log(f"{cid}: finished after it was cancelled; result dropped")
                return
            c["status"] = "ready"
            parent = self.changes.get(c["parent"]) if c["parent"] else None
            if parent is not None and parent["status"] == "ready":
                parent["status"] = "rejected"
                parent["superseded_by"] = cid
        log(f"{cid}: ready in {timings['total_s']}s {timings}")

    def _pipeline(self, cid: str, timings: dict[str, float]) -> None:
        with self.lock:
            change = self.changes[cid]
            priv = self.private[cid]
            parent = self.changes.get(change["parent"]) if change["parent"] else None
            parent = json.loads(json.dumps(parent)) if parent else None
        base = priv["base"]
        old_md: str = base["markdown"]
        base_module: str = base["module"]
        base_trace: Trace = base["trace"]
        base_spec, errors = load_spec(base_module)
        if base_spec is None:
            raise RuntimeError("the base spec does not load: " + "; ".join(errors))

        # 1. edit the markdown
        t = time.perf_counter()
        digest = build_digest(base_spec, base_trace)
        lines = digest.lines_for(priv["selected"])
        new_md, edit_usage = edit_markdown(self.generator, change["request"], old_md, lines, parent)
        timings["edit_s"] = round(time.perf_counter() - t, 2)
        diff = diff_rules(old_md, new_md)
        md_diff = "".join(
            difflib.unified_diff(
                old_md.splitlines(keepends=True),
                new_md.splitlines(keepends=True),
                f"a/{REQUIREMENTS}",
                f"b/{REQUIREMENTS}",
            )
        )
        rules = {
            "added": list(diff.added),
            "changed": [{"old": o, "new": n} for o, n in diff.changed],
            "removed": list(diff.removed),
        }
        if not self._set(cid, markdown_diff=md_diff, rules=rules):
            return
        log(f"{cid}: markdown edited in {timings['edit_s']}s; drafting")

        # 2. draft the change
        t = time.perf_counter()
        ctx_name = self.config.llm.get("context")
        context = (self.root / ctx_name).read_text(encoding="utf-8") if ctx_name else ""
        result = draft_since(
            old_md,
            new_md,
            base_module=base_module,
            base_trace=base_trace,
            context_md=context,
            generator=self.generator,
            model=self.model,
            source=base_trace.source or REQUIREMENTS,
        )
        timings["draft_s"] = round(time.perf_counter() - t, 2)
        usage = {"edit": edit_usage, "draft": result.usage.as_dict(), "draft_rounds": result.rounds}
        if result.spec is None:
            raise RuntimeError("the drafted spec does not load: " + "; ".join(result.problems))
        new_spec, new_trace = result.spec, result.trace
        with self.lock:
            priv["new_md"] = new_md
            priv["new_module"] = result.module_source.rstrip() + "\n"
            priv["new_trace"] = new_trace
        if not self._set(cid, status="checking", usage=usage, notes=list(result.notes)):
            return
        log(
            f"{cid}: drafted in {timings['draft_s']}s ({result.rounds} round(s), "
            f"{len(result.problems)} problem(s)); checking"
        )

        # 3. check it
        t = time.perf_counter()
        title = f"{self.root.name} spec review"
        base_lint = {(x["code"], x.get("element"), x["message"]) for x in self._base_lint()}
        new_lint = [f.to_dict() for f in lint(new_spec, new_trace)]
        lint_out = [
            {
                "code": x["code"],
                "message": x["message"],
                "fix": x["fix"],
                "rule_id": x.get("rule_id"),
                "element": x.get("element"),
            }
            for x in new_lint
            if (x["code"], x.get("element"), x["message"]) not in base_lint
        ]
        checks: dict[str, Any] = {
            "valid": result.valid,
            "problems": list(result.problems),
            "lint": lint_out,
            "conflicts": conflicts_json(new_spec, new_trace),
            "impact": None,
        }
        timings["lint_s"] = round(time.perf_counter() - t, 2)
        if self.harness is not None and result.valid:
            t = time.perf_counter()
            try:
                with self.app_context():
                    r = self.harness.resolve()
                    items = impact(
                        new_spec,
                        diff,
                        new_trace,
                        app_factory=r["app_factory"],
                        db_factory=r["db_factory"],
                        base_spec=base_spec,
                        **self.harness.check_kwargs(),
                    )
                checks["impact"] = impact_json(items, self.last_check)
            except Exception as exc:  # noqa: BLE001 -- reported, the change stays usable
                traceback.print_exc(file=sys.stderr)
                checks["impact"] = {
                    "verdict": "fail",
                    "failing": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            timings["impact_s"] = round(time.perf_counter() - t, 2)

        # 4. the view diff, the targets and the questions
        old_data = review_data(base_spec, base_trace, title)
        new_data = review_data(new_spec, new_trace, title, new_lint)
        changed = result.changed_targets or changed_targets(base_spec, new_spec)
        targets = {k: [t for t in v if not t.startswith("contract:")] for k, v in changed.items()}
        old_q = {q.id for q in base_trace.questions}
        questions = [
            {"id": q.id, "text": q.text, "default": q.default}
            for q in new_trace.questions
            if q.id not in old_q
        ]
        self._set(
            cid,
            checks=checks,
            targets=targets,
            view_diff=view_diff(base_spec, old_data, new_spec, new_data, changed),
            questions=questions,
        )

    def cancel(self, cid: str) -> dict[str, Any]:
        with self.lock:
            c = self.changes.get(cid)
            if c is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"no change {cid}")
            if c["status"] not in LIVE_STATUSES:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    f"change {cid} is {c['status']}; only a "
                    "change still drafting or checking can be cancelled",
                )
            c["status"] = "cancelled"
        log(f"{cid}: cancelled")
        return {"ok": True, "status": "cancelled"}

    def reject(self, cid: str) -> dict[str, Any]:
        with self.lock:
            c = self.changes.get(cid)
            if c is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"no change {cid}")
            if c["status"] == "accepted":
                raise ApiError(HTTPStatus.CONFLICT, f"change {cid} is already accepted")
            c["status"] = "rejected"
        log(f"{cid}: rejected")
        return {"ok": True}

    def accept(self, cid: str) -> dict[str, Any]:
        with self.lock:
            c = self.changes.get(cid)
            if c is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"no change {cid}")
            if c["status"] != "ready":
                raise ApiError(HTTPStatus.CONFLICT, f"change {cid} is {c['status']}, not ready")
            if not c["checks"]["valid"]:
                raise ApiError(HTTPStatus.CONFLICT, f"change {cid} does not validate")
            priv = self.private[cid]
            if files_hash(self.root) != priv["project_hash"]:
                raise ApiError(HTTPStatus.CONFLICT, "the spec changed since this proposal")
            (self.root / REQUIREMENTS).write_text(priv["new_md"], encoding="utf-8")
            (self.root / SPEC_FILE).write_text(priv["new_module"], encoding="utf-8")
            (self.root / TRACE_FILE).write_text(priv["new_trace"].to_json(), encoding="utf-8")
            c["status"] = "accepted"
            self._cache = None
            request = " ".join(c["request"].split())
        message = f"spec: {request}\n\n{COMMIT_TRAILER}\n"
        if not is_git_repo(self.root):
            log(f"{cid}: accepted; files written (not a git repository)")
            return {
                "commit": None,
                "message": message,
                "note": "the project is not a git repository: the files are written, "
                "nothing is committed",
            }
        r = git(self.root, "add", "--", *PROJECT_FILES)
        if r.returncode == 0:
            r = git(self.root, "commit", "-q", "-m", message, "--", *PROJECT_FILES)
        if r.returncode != 0:
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"the files are written but git failed: {r.stderr.strip()}",
            )
        sha = git(self.root, "rev-parse", "HEAD").stdout.strip()
        self.invalidate()
        log(f"{cid}: accepted as {sha[:10]}")
        return {"commit": sha, "message": message}

    # -- the full check
    def start_check(self) -> dict[str, Any]:
        with self.lock:
            if self.check_running:
                return {"started": False, "detail": "a check is already running"}
            self.check_running = True
        threading.Thread(target=self._run_check, name="studio-check", daemon=True).start()
        return {"started": True}

    def _run_check(self) -> None:
        t0 = time.perf_counter()
        try:
            snap = self.snapshot()
            if self.harness is None:
                raise RuntimeError("no app is configured (studio.toml [app] app and db)")
            from lattice.spec.check import check

            with self.app_context():
                r = self.harness.resolve()
                report = check(
                    snap.spec,
                    r["app_factory"],
                    r["db_factory"],
                    layers=self.config.layers,
                    **self.harness.check_kwargs(),
                )
            out = summarize_check(report, snap.spec, snap.trace, snap.markdown)
        except Exception as exc:  # noqa: BLE001 -- the check could not run
            traceback.print_exc(file=sys.stderr)
            out = {
                "at": now_iso(),
                "verdict": "fail",
                "counts": dict.fromkeys(LAYER_KEYS, 0),
                "targets": {},
                "findings": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        out["seconds"] = round(time.perf_counter() - t0, 2)
        with self.lock:
            self.last_check = out
            self.check_running = False
            self._cache = None
        log(
            f"check: {out['verdict']} in {out['seconds']}s"
            + (f" ({out['error']})" if out.get("error") else "")
        )


# ---- HTTP -----------------------------------------------------------------------------------


class Reply:
    """A route's answer with a status other than 200."""

    def __init__(self, body: Any, status: int) -> None:
        self.body = body
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server: StudioServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if self.server.verbose:
            super().log_message(format, *args)

    def _json(self, obj: Any, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "the body must be a JSON object")
        return body

    def _dispatch(self, fn: Callable[[], Any]) -> None:
        try:
            out = fn()
        except SessionError as e:
            self._json({"detail": e.detail}, e.status)
            return
        except ApiError as e:
            if e.status >= 500:
                log(f"{self.command} {self.path}: {e.status} {e.detail}")
            self._json({"detail": e.detail}, e.status)
            return
        except Exception as exc:  # noqa: BLE001 -- any failure answers 500
            traceback.print_exc(file=sys.stderr)
            self._json({"detail": f"{type(exc).__name__}: {exc}"}, 500)
            return
        if isinstance(out, Reply):
            self._json(out.body, out.status)
        elif out is not None:
            self._json(out)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        st = self.server.studio
        if path == "/api/model":
            return self._dispatch(st.get_model)
        if path.startswith("/api/changes/") and "/" not in path.removeprefix("/api/changes/"):
            cid = path.removeprefix("/api/changes/")
            return self._dispatch(lambda: st.get_change(cid))
        if path == "/api/session":
            return self._dispatch(lambda: st.sessions.list())
        if path.startswith("/api/session/"):
            parts = path.removeprefix("/api/session/").split("/")
            if len(parts) == 2 and parts[1] == "events":
                query = parse_qs(urlparse(self.path).query)
                raw = (query.get("after") or ["0"])[-1]
                try:
                    after = int(raw)
                except ValueError:
                    after = -1
                if after < 0:
                    return self._json({"detail": "after must be an integer >= 0"}, 422)
                return self._dispatch(lambda: st.sessions.events(parts[0], after))
        if path.startswith("/api/"):
            return self._json({"detail": "no such route"}, 404)
        return self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        st = self.server.studio

        def route() -> Any:
            body = self._body()
            if path == "/api/ask":
                return st.ask(body)
            if path == "/api/changes":
                return st.new_change(body)
            if path == "/api/check":
                return st.start_check()
            if path == "/api/session":
                return Reply(st.sessions.create(body), 201)
            if path.startswith("/api/session/"):
                parts = path.removeprefix("/api/session/").split("/")
                if len(parts) == 2 and parts[1] == "message":
                    return Reply(st.sessions.message(parts[0], body), 202)
            if path.startswith("/api/changes/"):
                parts = path.removeprefix("/api/changes/").split("/")
                if len(parts) == 2 and parts[1] == "accept":
                    return st.accept(parts[0])
                if len(parts) == 2 and parts[1] == "reject":
                    return st.reject(parts[0])
            raise ApiError(HTTPStatus.NOT_FOUND, "no such route")

        self._dispatch(route)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        st = self.server.studio
        if path.startswith("/api/changes/") and "/" not in path.removeprefix("/api/changes/"):
            cid = path.removeprefix("/api/changes/")
            return self._dispatch(lambda: st.cancel(cid))
        if path.startswith("/api/session/") and "/" not in path.removeprefix("/api/session/"):
            sid = path.removeprefix("/api/session/")
            return self._dispatch(lambda: st.sessions.interrupt(sid))
        return self._json({"detail": "no such route"}, 404)

    def _static(self, path: str) -> None:
        rel = unquote(path).lstrip("/") or "index.html"
        root = self.server.web_dir.resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            return self._json({"detail": "not found"}, 404)
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return self._json({"detail": "not found"}, 404)
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    # The browser fetches every ES module at once; the default backlog of 5 resets some.
    request_queue_size = 128

    def __init__(
        self, addr: tuple[str, int], studio: Studio, web_dir: Path = WEB_DIR, verbose: bool = False
    ) -> None:
        super().__init__(addr, Handler)
        self.studio = studio
        studio.base_url = f"http://{self.server_address[0]}:{self.server_address[1]}"
        self.web_dir = web_dir
        self.verbose = verbose

    def server_close(self) -> None:
        self.studio.close_sessions()
        super().server_close()


def make_server(
    project: Path | str,
    port: int = 0,
    *,
    model: str | None = None,
    generator: Generator | None = None,
    generator_factory: Callable[[str], Generator] | None = None,
    claude_bin: str | None = None,
    web_dir: Path = WEB_DIR,
    verbose: bool = False,
) -> StudioServer:
    """A Studio server for `project` on 127.0.0.1:`port` (0 picks a free one); not yet
    serving. `generator` (tests: a ScriptedGenerator) replaces the Claude CLI client."""
    studio = Studio(
        Path(project),
        model=model,
        generator=generator,
        generator_factory=generator_factory,
        claude_bin=claude_bin,
    )
    return StudioServer(("127.0.0.1", port), studio, web_dir, verbose)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m lattice.studio", description="Lattice Studio over a project directory"
    )
    ap.add_argument("project", type=Path)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--model", default=None, help="the LLM (default: studio.toml, else sonnet)")
    ap.add_argument("--quiet", action="store_true", help="no request log")
    args = ap.parse_args(argv)
    root = args.project.resolve()
    missing = [n for n in PROJECT_FILES if not (root / n).is_file()]
    if missing:
        print(f"studio: {root} has no {', '.join(missing)}", file=sys.stderr)
        return 2
    srv = make_server(root, args.port, model=args.model, verbose=not args.quiet)
    st = srv.studio
    print(
        f"Lattice Studio on http://127.0.0.1:{srv.server_address[1]}/ -- {root} "
        f"(model {st.model}; app {'configured' if st.harness else 'not configured'})",
        file=sys.stderr,
        flush=True,
    )

    def on_term(_sig: int, _frame: object) -> None:
        # chat turns run `claude` in their own process groups: a plain SIGTERM would skip
        # server_close() and leave them (and their MCP servers) running as orphans
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
