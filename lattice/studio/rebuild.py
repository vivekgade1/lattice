"""The Studio rebuild loop (API.md v1.7): after a spec change is accepted, a coding agent
edits the code in an isolated git worktree, and the project's gate commands -- run by
Studio itself, never trusted from the agent -- decide when it is done.

**Configuration** (``studio.toml``)::

    [rebuild]
    repo = "."              # the git repository, relative to the project dir (default ".")
    gates = ["./check.sh"]  # shell commands run from the repository root (default:
                            # <project>/check.sh when the project dir holds one)
    max_rounds = 3          # rounds before the rebuild gives up (POST may lower or raise it)
    round_timeout = 1800    # seconds one agent step may run
    gate_timeout = 900      # seconds one gate command may run
    model = "sonnet"        # optional: the agent's model (default: the Studio's model)
    worktrees = "repo"      # "repo": <repo>/.lattice/rebuilds/ (excluded in .git/info/exclude)
                            # "temp": the system temp directory
    agent = "claude"        # "claude" | "stub" (TEST ONLY: a scripted edit, see below)

A project can rebuild (``capabilities.rebuild``) when the repository is a git work tree
holding the project, every gate's command resolves (a file in the repository or a program
on PATH), and the ``claude`` binary runs (or the agent is the stub).

**One rebuild** (``POST /api/rebuild``): ``git worktree add -b rebuild/<change>[-n] <dir>
HEAD`` (the owner's checkout is never modified: no checkout, no merge, no index change),
then rounds in a background thread, one rebuild at a time per project:

1. *agent*: ``claude -p`` with ``cwd`` = the worktree, the environment of
   :func:`gate_env` (API credentials stripped), ``--permission-mode acceptEdits``,
   ``--tools Read,Grep,Glob,Edit,Write,Bash``, ``--allowedTools`` limiting Bash to the
   exact gate commands (``Bash(<gate>:*)``) plus the read-only ``lattice`` MCP tools, the
   ``lattice.mcp.guard`` hook on the *worktree's* project dir (spec files are off limits),
   ``--setting-sources ""``, ``--strict-mcp-config``, its own process group. Round 1's
   prompt is the brief (:func:`brief`); round n > 1 resumes the session (``--resume``)
   with the previous round's gate findings.
2. *integrity*: the project's spec files (``REQUIREMENTS.md``, ``spec.py``,
   ``spec.trace.json``, ``[mcp] protected``) and the gate scripts must be byte-identical
   to the base commit; an edited one fails the round ("spec files were edited") and is
   restored with ``git checkout <base> -- <file>`` inside the worktree.
3. *gates*: each gate command in the worktree (``/bin/sh -c``, own process group), with
   exit code and an output tail (20k chars) kept in the round's ``gates``. All exit 0 (and
   the integrity check clean) = ``pass``. ``counts`` come from ``PASS|FAIL: N findings
   (D n, X n)`` lines (else ``{gate: n}``), ``findings`` from the lattice report's finding
   lines and from lines starting ``FAIL``, ``finding`` or ``✗``.

**End states.** ``passed``: one commit on the rebuild branch (``build: <requests>`` plus a
``Lattice-Rebuild:`` trailer; author = the repository's identity); the worktree is kept for
review and the record carries ``commit``, ``branch``, ``base`` and a ``merge_hint`` --
Studio never merges. ``gave_up`` (``max_rounds`` rounds without a pass, or the agent hit
its usage limit): branch and worktree kept, nothing committed. ``stopped`` (``DELETE``):
the running process group is killed, the worktree removed, and the branch deleted when it
has no commit of its own.

**Gate environment** (:func:`gate_env`): the server's environment without API
credentials (:func:`lattice.llm.cli_client.cli_env`), ``PYTHONPATH`` = the worktree's
repository root, its project dir and ``[app] sys_path`` entries (inside the worktree)
first, then the directory holding the ``lattice`` package, then any inherited
``PYTHONPATH``. For ``uv run`` gates: ``UV_NO_SYNC=1`` (uv never re-syncs, so the main
checkout's editable install is never repointed at the worktree) and
``UV_PROJECT_ENVIRONMENT`` = the main checkout's ``.venv`` (else the running venv), with
its ``bin`` first on ``PATH`` -- no venv is re-created per worktree, and ``PYTHONPATH``
makes the *rebuilt* code the one imported.

**TEST ONLY: the stub agent** (``[rebuild] agent = "stub"``): round k writes the files of
``rounds[k-1]`` of ``stub-rebuild.json`` in the worktree's project dir
(``{"rounds": [{"files": {"path": "content"}, "hold": 0, "summary": "...",
"error": "..."}]}``; paths relative to the project dir; ``hold`` sleeps that many seconds
first, interruptible by DELETE; ``error`` makes it an error round). Never the default.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.llm.cli_client import cli_env
from lattice.studio.session import (
    _MODEL_TOOLS,
    DEFAULT_MAX_TOKENS,
    LATTICE_ROOT,
    Transcript,
    _kill_group,
    claude_available,
    error_kind,
    write_configs,
)

__all__ = [
    "RebuildConfig",
    "RebuildError",
    "Rebuilder",
    "brief",
    "build_rebuild_argv",
    "capability",
    "gate_env",
    "parse_gate_output",
]

DEFAULT_MAX_ROUNDS = 3
MAX_ROUNDS_LIMIT = 50
DEFAULT_ROUND_TIMEOUT = 1800.0
DEFAULT_GATE_TIMEOUT = 900.0
OUTPUT_TAIL = 20_000
MAX_WAIT = 60.0
STUB_FILE = "stub-rebuild.json"
STUB_HOLD_MAX = 60.0
FINISHED = ("passed", "gave_up", "stopped")
SPEC_FILES = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")
WORKTREE_DIR = ".lattice/rebuilds"
EXCLUDE_LINE = "/.lattice/rebuilds/"
AGENT_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write", "Bash")
# files the gates or the agent's tooling leave behind; never part of a round's diff or commit
JUNK = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", ".lattice")

RULES = """\
You are the coding agent of a Lattice Studio rebuild. The owner accepted a change to the \
spec of this project; your job is to change the CODE so the project's gate commands pass.

Rules:
- Fix the code (and its tests), never the contract: do not edit REQUIREMENTS.md, spec.py, \
spec.trace.json or the gate scripts. Edits to them are blocked, and Studio restores them \
and fails the round if they change.
- Run the gates with Bash exactly as given; you have no other shell commands. Do not use \
git: Studio commits the result on its own branch.
- Stop when the gates pass. Studio runs the gates itself after you finish and decides \
from their exit codes; what you say about them is not trusted.
- Finish with one or two sentences saying what you changed and why."""


class RebuildError(Exception):
    """A rebuild request refused: ``status`` is the HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(text: str, n: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _git(
    root: Path, *args: str, env: Mapping[str, str] | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(env) if env is not None else None,
    )


def _git_ok(root: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    r = _git(root, *args, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _toplevel(path: Path) -> Path | None:
    try:
        r = _git(path, "rev-parse", "--show-toplevel", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip()).resolve()


# ---- configuration ------------------------------------------------------------------------


@dataclass
class RebuildConfig:
    """``[rebuild]`` of studio.toml, resolved against the project directory."""

    project: Path
    table: dict[str, Any] = field(default_factory=dict)

    @property
    def repo_dir(self) -> Path:
        return (self.project / str(self.table.get("repo") or ".")).resolve()

    @property
    def gates(self) -> list[str]:
        gates = self.table.get("gates")
        if isinstance(gates, list) and gates:
            return [str(g) for g in gates if str(g).strip()]
        if (self.project / "check.sh").is_file():
            top = _toplevel(self.repo_dir)
            if top is not None and self.project.is_relative_to(top):
                rel = self.project.relative_to(top).as_posix()
                return ["./check.sh" if rel == "." else f"./{rel}/check.sh"]
            return ["./check.sh"]
        return []

    @property
    def agent(self) -> str:
        return str(self.table.get("agent") or "claude")

    @property
    def max_rounds(self) -> int:
        return int(self.table.get("max_rounds") or DEFAULT_MAX_ROUNDS)

    @property
    def round_timeout(self) -> float:
        return float(self.table.get("round_timeout") or DEFAULT_ROUND_TIMEOUT)

    @property
    def gate_timeout(self) -> float:
        return float(self.table.get("gate_timeout") or DEFAULT_GATE_TIMEOUT)

    @property
    def model(self) -> str | None:
        m = self.table.get("model")
        return str(m) if m else None

    @property
    def worktrees(self) -> str:
        return str(self.table.get("worktrees") or "repo")


def _gate_resolves(cmd: str, repo: Path) -> bool:
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return False
    while toks and re.fullmatch(r"\w+=.*", toks[0]):  # VAR=value prefixes
        toks = toks[1:]
    if not toks:
        return False
    head = toks[0]
    if "/" in head:
        p = Path(head) if os.path.isabs(head) else repo / head
        return p.is_file()
    return shutil.which(head) is not None or head in ("sh", "bash", "true", "test")


def capability(
    project: Path, table: Mapping[str, Any] | None, claude_bin: str = "claude"
) -> tuple[bool, str]:
    """Whether the project can rebuild, and why not (``""`` when it can)."""
    cfg = RebuildConfig(Path(project).resolve(), dict(table or {}))
    if cfg.agent not in ("claude", "stub"):
        return False, f"[rebuild] agent must be claude or stub, not {cfg.agent!r}"
    top = _toplevel(cfg.repo_dir)
    if top is None:
        return False, f"{cfg.repo_dir} is not a git repository"
    if not cfg.project.is_relative_to(top):
        return False, f"the project is not inside the repository {top}"
    if _git(top, "rev-parse", "--verify", "-q", "HEAD", timeout=5).returncode != 0:
        return False, "the repository has no commit yet"
    gates = cfg.gates
    if not gates:
        return False, "no gate commands ([rebuild] gates, or a check.sh in the project)"
    bad = [g for g in gates if not _gate_resolves(g, top)]
    if bad:
        return False, f"gate commands that do not resolve: {', '.join(bad)}"
    if cfg.agent != "stub" and not claude_available(claude_bin):
        return False, f"the claude binary ({claude_bin}) was not found"
    return True, ""


# ---- the environment of the agent and the gates --------------------------------------------


def _main_venv(repo: Path) -> Path | None:
    v = repo / ".venv"
    if (v / "bin").is_dir():
        return v
    if sys.prefix != sys.base_prefix:
        return Path(sys.prefix)
    return None


def gate_env(
    base: Mapping[str, str],
    *,
    repo: Path,
    worktree: Path,
    project_rel: str,
    sys_path: Sequence[str] = (),
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment of the gate commands (and of the agent): see the module docstring."""
    env = {k: v for k, v in cli_env(base, None).items() if not k.startswith("ANTHROPIC_")}
    wt_project = (worktree / project_rel).resolve()
    paths = [str(worktree), str(wt_project), *(str((wt_project / s).resolve()) for s in sys_path)]
    paths.append(str(LATTICE_ROOT))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(p for p in paths if p))
    env["UV_NO_SYNC"] = "1"
    venv = _main_venv(repo)
    if venv is not None:
        env.setdefault("UV_PROJECT_ENVIRONMENT", str(venv))
        env["VIRTUAL_ENV"] = env["UV_PROJECT_ENVIRONMENT"]
        env["PATH"] = os.pathsep.join(
            p for p in (str(Path(env["UV_PROJECT_ENVIRONMENT"]) / "bin"), env.get("PATH")) if p
        )
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # no __pycache__ in the worktree
    env.update(extra or {})
    return env


# ---- gate output ---------------------------------------------------------------------------

_SUMMARY = re.compile(r"^\s*(PASS|FAIL):\s*(\d+)\s+findings?\s*\(([^)]*)\)", re.M)
_RULE = re.compile(r"^rule \((\w): [^)]*\): (.*)$")
_LATTICE_FINDING = re.compile(r"^\s+\[([A-Z]) (\w+)\] (.+)$")
_TARGET = re.compile(r"\b((?:scenario|invariant|machine|service|cell|contract|file):[^\s,;)]+)")
_GENERIC = re.compile(r"^\s*(FAIL\b|finding\b|✗)", re.I)


def parse_gate_output(cmd: str, output: str, rc: int) -> tuple[dict[str, int], list[dict]]:
    """(counts, findings) of one gate run. Findings have ``title`` and ``target`` (ids are
    added by the caller)."""
    counts: dict[str, int] = {}
    for m in _SUMMARY.finditer(output):
        for part in m.group(3).split(","):
            bits = part.split()
            if len(bits) == 2 and bits[1].isdigit():
                counts[bits[0]] = counts.get(bits[0], 0) + int(bits[1])
    findings: list[dict[str, str]] = []
    rule_target = ""
    for line in output.splitlines():
        if _SUMMARY.match(line):
            continue
        m = _RULE.match(line)
        if m:
            rule = m.group(2).strip()
            rule_target = (
                "scenario:" + rule.removeprefix("scenario:").strip()
                if rule.startswith("scenario:")
                else ""
            )
            continue
        m = _LATTICE_FINDING.match(line)
        if m:
            t = _TARGET.search(line)
            findings.append(
                {"title": _short(line.strip()), "target": t.group(1) if t else rule_target}
            )
            continue
        if _GENERIC.match(line):
            t = _TARGET.search(line)
            findings.append({"title": _short(line.strip()), "target": t.group(1) if t else ""})
    if rc != 0 and not findings:
        last = next((x for x in reversed(output.splitlines()) if x.strip()), "")
        findings.append(
            {
                "title": _short(f"{cmd} exited {rc}" + (f": {last.strip()}" if last else "")),
                "target": "",
            }
        )
    if not counts:
        counts = {cmd: len(findings) if rc != 0 else 0}
    return counts, findings


# ---- the brief -----------------------------------------------------------------------------


def _rule_texts(trace_json: str) -> dict[str, str]:
    try:
        data = json.loads(trace_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    return {str(r.get("id")): str(r.get("text") or "") for r in data.get("rules") or []}


def _links(trace_json: str) -> dict[str, list[str]]:
    try:
        return {k: list(v) for k, v in (json.loads(trace_json).get("links") or {}).items()}
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {}


def brief(
    changes: Sequence[Mapping[str, Any]],
    *,
    trace_json: str,
    gates: Sequence[str],
    targets: Sequence[Mapping[str, Any]],
    project_rel: str,
) -> str:
    """Round 1's prompt: the accepted changes, their rules and requirements diff, the
    targets (checks labelled not built yet), the gates and the rules."""
    texts, links = _rule_texts(trace_json), _links(trace_json)
    where = "the repository root" if project_rel == "." else f"`{project_rel}/`"
    out = [
        "# Rebuild brief",
        "",
        f"The project's spec lives in {where} (REQUIREMENTS.md, spec.py, spec.trace.json); "
        "it already contains the accepted change(s) below. Change the code so the gates pass.",
        "",
    ]
    for c in changes:
        out += [f"## Change {c['id']}: {' '.join(str(c.get('request') or '').split())}", ""]
        rules = c.get("rules") or {}

        def line(rid: str, label: str) -> str:
            link = f" (checked by {', '.join(links[rid])})" if links.get(rid) else ""
            return f"- {label} {rid}: {texts.get(rid, '(see REQUIREMENTS.md)')}{link}"

        for rid in rules.get("added") or []:
            out.append(line(str(rid), "added"))
        for ch in rules.get("changed") or []:
            rid = str(ch.get("new") if isinstance(ch, Mapping) else ch)
            out.append(line(rid, "changed"))
        for rid in rules.get("removed") or []:
            out.append(f"- retired {rid}")
        diff = str(c.get("markdown_diff") or "").strip()
        if diff:
            out += ["", "Requirements diff:", "```diff", diff, "```"]
        out.append("")
    if targets:
        out += ["## Checks not built yet (these must turn green)", ""]
        out += [f"- {t['id']}: {t['label']}" for t in targets]
        out.append("")
    out += ["## Gates (run from the repository root; all must exit 0)", ""]
    out += [f"- `{g}`" for g in gates]
    out += ["", "Fix the code, never the spec, requirements or trace. Stop when the gates pass."]
    return "\n".join(out)


def findings_prompt(n: int, check: Mapping[str, Any], gates: Sequence[Mapping[str, Any]]) -> str:
    """Round n's prompt (n > 1): the previous round's gate findings."""
    out = [f"Round {n - 1} did not pass. Studio ran the gates; these are the findings:", ""]
    for f in check.get("findings") or []:
        tgt = f" [{f['target']}]" if f.get("target") else ""
        out.append(f"- {f['id']}{tgt}: {f['title']}")
    for g in gates:
        if g.get("exit_code") != 0:
            tail = str(g.get("output_tail") or "")[-3000:]
            out += [
                "",
                f"`{g['command']}` exited {g.get('exit_code')}; the end of its output:",
                "```",
                tail.rstrip(),
                "```",
            ]
    out += ["", "Fix the code (never the spec files) and run the gates again."]
    return "\n".join(out)


# ---- argv ----------------------------------------------------------------------------------


def allowed_tools(gates: Sequence[str]) -> list[str]:
    return [
        "Read",
        "Grep",
        "Glob",
        "Edit",
        "Write",
        *(f"Bash({g}:*)" for g in gates),
        *_MODEL_TOOLS,
    ]


def build_rebuild_argv(
    *,
    claude_bin: str,
    text: str,
    model: str,
    mcp_config: Path,
    settings: Path,
    gates: Sequence[str],
    resume: str | None = None,
    rules: str = RULES,
) -> list[str]:
    """The ``claude`` argv of one agent step. The prompt goes last behind ``--``."""
    argv = [claude_bin, "-p", "--output-format", "stream-json", "--verbose"]
    if resume:
        argv += ["--resume", resume]
    argv += [
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config),
        "--settings",
        str(settings),
        "--tools",
        ",".join(AGENT_TOOLS),
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        ",".join(allowed_tools(gates)),
        "--disallowedTools",
        "NotebookEdit",
        "--append-system-prompt",
        rules,
        "--model",
        model,
        "--",
        text,
    ]
    return argv


# ---- the record ----------------------------------------------------------------------------


@dataclass
class _Run:
    """One rebuild: the public record plus what the thread needs."""

    record: dict[str, Any]
    changes: list[dict[str, Any]]
    repo: Path
    worktree: Path
    project_rel: str
    base: str
    branch: str
    gates: list[str]
    targets: list[dict[str, Any]]
    trace_json: str
    stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    proc: subprocess.Popen[str] | None = None
    thread: threading.Thread | None = None
    tmp: Path | None = None
    session_id: str | None = None
    argvs: list[list[str]] = field(default_factory=list)


class Rebuilder:
    """Every rebuild of one project (one Studio). One runs at a time."""

    def __init__(
        self,
        project: Path,
        table: Mapping[str, Any] | None,
        *,
        name: str,
        model: str,
        claude_bin: str = "claude",
        sys_path: Sequence[str] = (),
        protected: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        python: str | None = None,
        log: Callable[[str], None] | None = None,
        trailer: str = "",
    ) -> None:
        self.project = Path(project).resolve()
        self.cfg = RebuildConfig(self.project, dict(table or {}))
        self.name = name
        self.model = self.cfg.model or model
        self.claude_bin = claude_bin
        self.sys_path = list(sys_path)
        self.protected = list(protected)
        self._env = env
        self.python = python or sys.executable
        self.log = log or (lambda _m: None)
        self.trailer = trailer
        self.lock = threading.RLock()
        self.runs: dict[str, _Run] = {}
        self.next_id = 1
        self.builds: dict[str, dict[str, Any]] = {}  # change id -> build

    # -- capability
    def available(self) -> tuple[bool, str]:
        return capability(self.project, self.cfg.table, self.claude_bin)

    def build_of(self, cid: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.builds.get(cid) or {"state": "not_built"})

    def running(self) -> str | None:
        with self.lock:
            for rid, run in self.runs.items():
                if run.record["status"] not in FINISHED:
                    return rid
        return None

    # -- POST /api/rebuild
    def start(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        model: str | None = None,
        max_rounds: int | None = None,
    ) -> dict[str, str]:
        """Start a rebuild of `changes` (accepted change records, already validated by
        the caller). Raises RebuildError (409 running, 503 unavailable)."""
        ok, why = self.available()
        if not ok:
            raise RebuildError(503, f"rebuild is not available: {why}")
        with self.lock:
            busy = self.running()
            if busy:
                raise RebuildError(409, f"a rebuild is already running in this project ({busy})")
            rid = f"b{self.next_id}"
            self.next_id += 1
            repo = _toplevel(self.cfg.repo_dir)
            assert repo is not None
            project_rel = self.project.relative_to(repo).as_posix()
            base = _git_ok(repo, "rev-parse", "HEAD")
            branch = self._branch_name(repo, str(changes[0]["id"]))
            worktree = self._worktree_dir(repo, rid)
            r = _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), base)
            if r.returncode != 0:
                raise RebuildError(500, f"git worktree add failed: {r.stderr.strip()}")
            trace_path = worktree / project_rel / "spec.trace.json"
            trace_json = trace_path.read_text(encoding="utf-8") if trace_path.is_file() else ""
            targets = _targets(changes)
            rounds = int(max_rounds or self.cfg.max_rounds)
            record = {
                "id": rid,
                "project": self.name,
                "changes": [str(c["id"]) for c in changes],
                "status": "queued",
                "model": model or self.model,
                "agent": self.cfg.agent,
                "max_rounds": rounds,
                "started": _now(),
                "finished": None,
                "rounds": [],
                "targets": [{"id": t["id"], "label": t["label"], "done": False} for t in targets],
                "gates": list(self.cfg.gates),
                "base": base,
                "branch": branch,
                "worktree": str(worktree),
                "merge_hint": None,
                "usage": {"cost_usd": 0.0, "duration_ms": 0, "input_tokens": 0, "output_tokens": 0},
                "error": None,
            }
            run = _Run(
                record=record,
                changes=[copy.deepcopy(dict(c)) for c in changes],
                repo=repo,
                worktree=worktree,
                project_rel=project_rel,
                base=base,
                branch=branch,
                gates=list(self.cfg.gates),
                targets=targets,
                trace_json=trace_json,
            )
            self.runs[rid] = run
            for cid in record["changes"]:
                self.builds[cid] = {"state": "rebuilding", "rebuild_id": rid, "branch": branch}
            run.thread = threading.Thread(
                target=self._run, args=(run,), daemon=True, name=f"studio-rebuild-{rid}"
            )
            run.thread.start()
        self.log(f"rebuild {rid}: started on {branch} at {base[:10]} ({worktree})")
        return {"id": rid}

    def _branch_name(self, repo: Path, cid: str) -> str:
        base = f"rebuild/{re.sub(r'[^A-Za-z0-9._-]', '-', cid)}"
        name, n = base, 2
        while _git(repo, "show-ref", "--verify", "-q", f"refs/heads/{name}").returncode == 0:
            name, n = f"{base}-{n}", n + 1
        return name

    def _worktree_dir(self, repo: Path, rid: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9._-]", "-", self.name)
        leaf = f"{slug}-{rid}-{uuid.uuid4().hex[:6]}"
        if self.cfg.worktrees == "temp":
            # the empty temp dir itself (git accepts it): nothing is left once it is removed
            return Path(tempfile.mkdtemp(prefix=f"lattice-rebuild-{leaf}-"))
        parent = repo / WORKTREE_DIR
        parent.mkdir(parents=True, exist_ok=True)
        _ensure_excluded(repo)
        return parent / leaf

    # -- GET /api/rebuild/{id}
    def get(self, rid: str, wait: float = 0.0) -> dict[str, Any]:
        with self.lock:
            run = self.runs.get(rid)
        if run is None:
            raise RebuildError(404, f"no rebuild {rid}")
        if wait > 0:
            run.done.wait(min(wait, MAX_WAIT))
        with self.lock:
            return copy.deepcopy(run.record)

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [copy.deepcopy(r.record) for r in self.runs.values()]

    def last_argv(self, rid: str) -> list[list[str]]:
        with self.lock:
            return [list(a) for a in self.runs[rid].argvs]

    # -- DELETE /api/rebuild/{id}
    def stop(self, rid: str, join: float = 15.0) -> dict[str, Any]:
        with self.lock:
            run = self.runs.get(rid)
            if run is None:
                raise RebuildError(404, f"no rebuild {rid}")
            status = run.record["status"]
            if status in FINISHED:
                raise RebuildError(409, f"cannot stop a rebuild that is {status}")
            run.stop.set()
            run.record["status"] = "stopped"
            for cid in run.record["changes"]:
                self.builds[cid] = {"state": "not_built", "rebuild_id": rid}
            proc = run.proc
        if proc is not None:
            _kill_group(proc, signal.SIGTERM)
        if run.thread is not None and run.thread is not threading.current_thread():
            run.thread.join(join)
        self.log(f"rebuild {rid}: stopped")
        return {"id": rid, "status": "stopped"}

    def close(self) -> None:
        """Stop every running rebuild (the server is shutting down)."""
        with self.lock:
            live = [rid for rid, r in self.runs.items() if r.record["status"] not in FINISHED]
        for rid in live:
            with contextlib.suppress(RebuildError):
                self.stop(rid)

    # -- the thread
    def _child_env(self, run: _Run, n: int) -> dict[str, str]:
        base = self._env if self._env is not None else os.environ
        return gate_env(
            base,
            repo=run.repo,
            worktree=run.worktree,
            project_rel=run.project_rel,
            sys_path=self.sys_path,
            extra={
                "LATTICE_REBUILD": run.record["id"],
                "LATTICE_REBUILD_ROUND": str(n),
                "LATTICE_REBUILD_WORKTREE": str(run.worktree),
            },
        )

    def _run(self, run: _Run) -> None:
        try:
            self._loop(run)
        except Exception as exc:  # noqa: BLE001 -- the record carries any failure
            import traceback

            traceback.print_exc(file=sys.stderr)
            with self.lock:
                if run.record["status"] not in FINISHED:
                    run.record["error"] = {
                        "kind": "internal",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    self._finish(run, "gave_up")
        finally:
            if run.stop.is_set():
                self._cleanup_stopped(run)
            if run.tmp is not None:
                shutil.rmtree(run.tmp, ignore_errors=True)
            with self.lock:
                if run.record["finished"] is None:
                    run.record["finished"] = _now()
            run.done.set()

    def _loop(self, run: _Run) -> None:
        rec = run.record
        with self.lock:
            if run.stop.is_set():
                return
            rec["status"] = "running"
        prev_tree = self._tree(run)
        last_check: dict[str, Any] | None = None
        last_gates: list[dict[str, Any]] = []
        for n in range(1, int(rec["max_rounds"]) + 1):
            if run.stop.is_set():
                return
            rnd: dict[str, Any] = {
                "n": n,
                "started": _now(),
                "finished": None,
                "agent_summary": "",
                "files_changed": [],
                "check": None,
            }
            with self.lock:
                rec["rounds"].append(rnd)
            if n == 1 or last_check is None or not run.session_id:
                prompt = brief(
                    run.changes,
                    trace_json=run.trace_json,
                    gates=run.gates,
                    targets=run.targets,
                    project_rel=run.project_rel,
                )
                if n > 1 and last_check is not None:
                    prompt += "\n\n" + findings_prompt(n, last_check, last_gates)
            else:
                prompt = findings_prompt(n, last_check, last_gates)
            agent = self._agent_step(run, n, prompt)
            if run.stop.is_set():
                return
            # integrity: the contract must be the base commit's, byte for byte
            edited = self._restore_protected(run)
            gates = [] if run.stop.is_set() else self._gates(run, n)
            if run.stop.is_set():
                return
            tree = self._tree(run)
            files = _tree_diff(run.worktree, prev_tree, tree)
            prev_tree = tree
            counts: dict[str, int] = {}
            findings: list[dict[str, str]] = []
            if edited:
                findings.append(
                    {
                        "title": "spec files were edited (restored from the base "
                        f"commit): {', '.join(edited)}",
                        "target": "spec",
                    }
                )
            if agent.get("error"):
                findings.append(
                    {"title": f"the agent failed: {agent['error']['message']}", "target": "agent"}
                )
            for g in gates:
                c, fs = parse_gate_output(g["command"], g["output_tail"], g["exit_code"])
                for k, v in c.items():
                    counts[k] = counts.get(k, 0) + v
                findings += fs
            passed = bool(gates) and all(g["exit_code"] == 0 for g in gates) and not edited
            if passed:
                findings = []  # a passing gate's informational FAIL-looking lines are no findings
            for i, f in enumerate(findings, 1):
                f["id"] = f"f{i}"
            check = {
                "verdict": "pass" if passed else "fail",
                "counts": counts,
                "findings": [
                    {"id": f["id"], "title": f["title"], "target": f["target"]} for f in findings
                ],
            }
            with self.lock:
                rnd.update(
                    finished=_now(),
                    agent_summary=agent.get("summary") or "",
                    files_changed=files,
                    check=check,
                    gates=gates,
                    spec_edited=edited,
                    agent=agent.get("usage") or {},
                    error=agent.get("error"),
                )
                u = agent.get("usage") or {}
                for k in ("cost_usd", "duration_ms", "input_tokens", "output_tokens"):
                    rec["usage"][k] = rec["usage"].get(k, 0) + (u.get(k) or 0)
                rec["usage"]["cost_usd"] = round(rec["usage"]["cost_usd"], 6)
            self.log(
                f"rebuild {rec['id']}: round {n} {check['verdict']} "
                f"({len(findings)} finding(s), {len(files)} file(s) changed)"
            )
            if passed:
                self._commit(run, n)
                return
            last_check, last_gates = check, gates
            if (agent.get("error") or {}).get("kind") == "usage_limit":
                with self.lock:
                    rec["error"] = agent["error"]
                    self._finish(run, "gave_up")
                return
        with self.lock:
            if not run.stop.is_set():
                self._finish(run, "gave_up")

    def _finish(self, run: _Run, status: str) -> None:
        """With the lock held: the end state and the changes' build."""
        rec = run.record
        if run.stop.is_set() and status != "stopped":
            return  # DELETE won the race: the record stays stopped
        rec["status"] = status
        rec["finished"] = _now()
        state = {"passed": "built", "gave_up": "gave_up", "stopped": "not_built"}[status]
        for cid in rec["changes"]:
            b: dict[str, Any] = {"state": state, "rebuild_id": rec["id"]}
            if status != "stopped":
                b["branch"] = run.branch
            if status == "passed" and rec.get("commit"):
                b["commit"] = rec["commit"]["sha"]
            self.builds[cid] = b
        if status == "passed":
            for t in rec["targets"]:
                t["done"] = True

    # -- steps
    def _agent_step(self, run: _Run, n: int, prompt: str) -> dict[str, Any]:
        if self.cfg.agent == "stub":
            return self._stub_step(run, n)
        return self._claude_step(run, n, prompt)

    def _stub_step(self, run: _Run, n: int) -> dict[str, Any]:
        """TEST ONLY: round n of stub-rebuild.json (see the module docstring)."""
        path = run.worktree / run.project_rel / STUB_FILE
        try:
            script = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"summary": "", "error": {"kind": "cli", "message": f"{STUB_FILE}: {exc}"}}
        rounds = script.get("rounds") or []
        step = rounds[n - 1] if n - 1 < len(rounds) else {}
        hold = min(float(step.get("hold") or 0), STUB_HOLD_MAX)
        if hold and run.stop.wait(hold):
            return {"summary": ""}
        written = []
        for rel, content in (step.get("files") or {}).items():
            p = run.worktree / run.project_rel / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(content), encoding="utf-8")
            written.append(rel)
        out: dict[str, Any] = {
            "summary": step.get("summary")
            or (f"stub: wrote {', '.join(written)}" if written else "stub: no edit"),
            "usage": {
                "cost_usd": 0.0,
                "duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "num_turns": 0,
                "session_id": None,
            },
        }
        if step.get("error"):
            msg = str(step["error"])
            out["error"] = {"kind": error_kind(msg), "message": msg}
        return out

    def _claude_step(self, run: _Run, n: int, prompt: str) -> dict[str, Any]:
        if run.tmp is None:
            run.tmp = Path(tempfile.mkdtemp(prefix="lattice-rebuild-cfg-"))
            write_configs(run.tmp, "ask", run.worktree / run.project_rel, self.python)
        resume = run.session_id if n > 1 else None
        argv = build_rebuild_argv(
            claude_bin=self.claude_bin,
            text=prompt,
            model=str(run.record["model"]),
            mcp_config=run.tmp / "mcp.json",
            settings=run.tmp / "settings.json",
            gates=run.gates,
            resume=resume,
        )
        with self.lock:
            run.argvs.append(argv)
        env = self._child_env(run, n)
        env.update(cli_env({}, DEFAULT_MAX_TOKENS))  # the output cap of `claude -p`
        t = Transcript()
        t.add_user(prompt, [])
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(run.worktree),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            return {
                "summary": "",
                "error": {"kind": "cli", "message": f"could not start {self.claude_bin}: {exc}"},
            }
        with self.lock:
            run.proc = proc
        if run.stop.is_set():
            _kill_group(proc, signal.SIGTERM)
        stderr: list[str] = []
        drainer = threading.Thread(target=lambda: stderr.extend(proc.stderr or ()), daemon=True)
        drainer.start()
        timed_out = threading.Event()

        def on_timeout() -> None:
            timed_out.set()
            _kill_group(proc, signal.SIGTERM)

        timer = threading.Timer(self.cfg.round_timeout, on_timeout)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout or ():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    t.feed(rec)
            proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                _kill_group(proc, signal.SIGKILL)
            drainer.join(2)
            with self.lock:
                run.proc = None
        if t.claude_session_id:
            run.session_id = t.claude_session_id
        events = t.events_after(0)
        texts = [e["text"] for e in events if e["type"] == "text" and not e.get("partial")]
        result = next((e for e in reversed(events) if e["type"] == "result"), None)
        errors = [e for e in events if e["type"] == "error"]
        out: dict[str, Any] = {"summary": _short(texts[-1], 600) if texts else ""}
        out["usage"] = {
            "cost_usd": (result or {}).get("cost_usd", 0.0),
            "duration_ms": (result or {}).get("duration_ms", 0),
            "input_tokens": (result or {}).get("input_tokens", 0),
            "output_tokens": (result or {}).get("output_tokens", 0),
            "num_turns": (result or {}).get("num_turns", 0),
            "session_id": t.claude_session_id,
        }
        if run.stop.is_set():
            return out
        if timed_out.is_set():
            out["error"] = {
                "kind": "timeout",
                "message": f"the agent did not finish within {self.cfg.round_timeout:g}s",
            }
        elif errors:
            out["error"] = {
                "kind": errors[-1].get("kind") or "cli",
                "message": errors[-1].get("message") or "claude failed",
            }
        elif result is None:
            tail = "".join(stderr).strip()
            msg = _short(tail, 500) or f"claude exited with status {proc.returncode}"
            out["error"] = {"kind": error_kind(tail), "message": msg}
        return out

    def _protected_paths(self, run: _Run) -> list[str]:
        rel = "" if run.project_rel == "." else run.project_rel + "/"
        names = [*SPEC_FILES, *self.protected]
        out = [f"{rel}{n}" for n in names]
        for g in run.gates:  # the gate scripts themselves
            try:
                toks = shlex.split(g)
            except ValueError:
                continue
            while toks and re.fullmatch(r"\w+=.*", toks[0]):
                toks = toks[1:]
            if toks and "/" in toks[0] and not os.path.isabs(toks[0]):
                p = os.path.normpath(toks[0])
                if _git(run.repo, "cat-file", "-e", f"{run.base}:{p}").returncode == 0:
                    out.append(p)
        return list(dict.fromkeys(out))

    def _restore_protected(self, run: _Run) -> list[str]:
        """The protected files the round changed (restored to the base commit's)."""
        edited = []
        for p in self._protected_paths(run):
            r = subprocess.run(
                ["git", "-C", str(run.repo), "cat-file", "-p", f"{run.base}:{p}"],
                capture_output=True,
                timeout=60,
            )
            want = r.stdout if r.returncode == 0 else None
            f = run.worktree / p
            have = f.read_bytes() if f.is_file() else None
            if have == want:
                continue
            edited.append(p)
            if want is None:
                f.unlink(missing_ok=True)
            else:
                _git_ok(run.worktree, "checkout", run.base, "--", p)
        return edited

    def _gates(self, run: _Run, n: int) -> list[dict[str, Any]]:
        env = self._child_env(run, n)
        out = []
        for cmd in run.gates:
            if run.stop.is_set():
                break
            t0 = time.perf_counter()
            proc = subprocess.Popen(
                ["/bin/sh", "-c", cmd],
                cwd=str(run.worktree),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                start_new_session=True,
            )
            with self.lock:
                run.proc = proc
            try:
                output, _ = proc.communicate(timeout=self.cfg.gate_timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                _kill_group(proc, signal.SIGKILL)
                output, _ = proc.communicate()
                output = (
                    output or ""
                ) + f"\n[studio] the gate timed out after {self.cfg.gate_timeout:g}s\n"
                rc = 124
            finally:
                with self.lock:
                    run.proc = None
            out.append(
                {
                    "command": cmd,
                    "exit_code": rc,
                    "seconds": round(time.perf_counter() - t0, 2),
                    "output_tail": (output or "")[-OUTPUT_TAIL:],
                }
            )
        return out

    def _tree(self, run: _Run) -> str:
        return _snapshot_tree(run.worktree)

    def _commit(self, run: _Run, n: int) -> None:
        rec = run.record
        requests = "; ".join(" ".join(str(c.get("request") or "").split()) for c in run.changes)
        lines = [
            f"build: {requests}",
            "",
            f"Rebuilt by Lattice Studio from the accepted change(s) {', '.join(rec['changes'])}; "
            f"the gates passed in round {n} of {rec['max_rounds']}.",
            "",
            f"Lattice-Rebuild: {rec['id']} ({self.name})",
        ]
        if self.cfg.agent == "claude" and self.trailer:
            lines.append(self.trailer)
        message = "\n".join(lines) + "\n"
        wt = run.worktree
        try:
            _git_ok(wt, "add", "-A", "--", ".", *_junk_pathspecs())
            _git_ok(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
            sha = _git_ok(wt, "rev-parse", "HEAD")
            ns = _git_ok(wt, "diff", "--name-status", f"{run.base}..{sha}")
        except RuntimeError as exc:
            with self.lock:
                rec["error"] = {"kind": "git", "message": str(exc)}
                self._finish(run, "gave_up")
            return
        name_status = [" ".join(x.split("\t")) for x in ns.splitlines() if x.strip()]
        files = [x.split("\t")[-1] for x in ns.splitlines() if x.strip()]
        with self.lock:
            rec["commit"] = {
                "sha": sha,
                "message": message,
                "files_changed": files,
                "name_status": name_status,
            }
            rec["merge_hint"] = (
                f"Studio never merges. To take the rebuilt code, run `git merge {run.branch}` "
                f"in {run.repo}; the worktree {wt} stays for review."
            )
            self._finish(run, "passed")
        self.log(f"rebuild {rec['id']}: passed as {sha[:10]} on {run.branch}")

    def _cleanup_stopped(self, run: _Run) -> None:
        """A stopped rebuild: remove its worktree, and its branch when it has no commit."""
        shutil.rmtree(run.worktree, ignore_errors=True)
        _git(run.repo, "worktree", "prune")
        tip = _git(run.repo, "rev-parse", "-q", "--verify", f"refs/heads/{run.branch}")
        branch_gone = True
        if tip.returncode == 0:
            if tip.stdout.strip() == run.base:
                r = _git(run.repo, "update-ref", "-d", f"refs/heads/{run.branch}", run.base)
                branch_gone = r.returncode == 0
            else:
                branch_gone = False
        with self.lock:
            run.record["rounds"] = [r for r in run.record["rounds"] if r.get("check") is not None]
            run.record["worktree"] = None
            if branch_gone:
                run.record["branch"] = None


def _targets(changes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in changes:
        impact = ((c.get("checks") or {}).get("impact")) or {}
        for item in impact.get("failing") or []:
            if item.get("label") != "not_built_yet" or item.get("target") in seen:
                continue
            seen.add(item["target"])
            out.append({"id": item["target"], "label": item.get("message") or item["target"]})
    return out


def _junk_pathspecs() -> list[str]:
    # Globs only: a literal ``:(exclude).pytest_cache`` that names an existing, gitignored
    # path makes ``git add`` exit 1 ("paths are ignored"), which killed a real rebuild after
    # the agent had run pytest in the worktree. ``**/`` also matches the root.
    return [f":(exclude,glob)**/{j}/**" for j in JUNK] + [f":(exclude,glob)**/{j}" for j in JUNK]


def _snapshot_tree(wt: Path) -> str:
    """The tree of the worktree's files as they are (a throwaway index: the worktree's own
    index is untouched)."""
    with tempfile.TemporaryDirectory(prefix="lattice-rebuild-idx-") as d:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(d) / "index")}
        _git_ok(wt, "read-tree", "HEAD", env=env)
        _git_ok(wt, "add", "-A", "--", ".", *_junk_pathspecs(), env=env)
        return _git_ok(wt, "write-tree", env=env)


def _tree_diff(wt: Path, a: str, b: str) -> list[str]:
    if a == b:
        return []
    out = _git_ok(wt, "diff-tree", "-r", "--name-only", a, b)
    return [x for x in out.splitlines() if x.strip()]


def _ensure_excluded(repo: Path) -> None:
    """``/.lattice/rebuilds/`` in the repository's ``info/exclude`` (not .gitignore)."""
    common = _git_ok(repo, "rev-parse", "--git-common-dir")
    path = Path(common) if os.path.isabs(common) else repo / common
    exclude = path / "info" / "exclude"
    try:
        text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    except OSError:
        text = ""
    if EXCLUDE_LINE in text.splitlines():
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(f"# Lattice Studio rebuild worktrees\n{EXCLUDE_LINE}\n")


def load_table(project: Path) -> dict[str, Any]:
    """``[rebuild]`` of the project's studio.toml (missing or broken: empty)."""
    try:
        data = tomllib.loads((project / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    t = data.get("rebuild")
    return dict(t) if isinstance(t, dict) else {}
