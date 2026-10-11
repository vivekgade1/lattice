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

**Build from spec (API.md v1.8)**: ``start(..., scope=...)`` builds an area, a list of
rules or ``all`` with no accepted change: a baseline gate run picks the scope's failing
checks (``lattice.studio.areas``), each round is judged by Studio (every in-scope target
passes, no regression of a check that passed in the baseline), and the build lands on a
target branch (default ``build/<project>``; ``branch={name, create, from}``), fast-forwarded
from a separate worktree, never touching the owner's branch unless confirmed (and then only
by ``git merge --ff-only`` in a clean worktree). Extra ``[rebuild]`` keys: ``areas``,
``metrics_hook``, ``findings_json``, ``integration_branch``, ``protected_branches``.

**Build performance (API.md v1.11)**: ``[rebuild] scoped_gate_args`` (a plan node's gate on
its scope plus the regression set, :func:`build_selection`), ``level_gate``,
``level_repair_attempts`` (:meth:`Rebuilder.repair_build`), ``node_timeout``,
``node_max_tokens`` (the usage seen so far is kept, :class:`StreamUsage`) and
``stop_grace``; per-tip gate results (:meth:`Rebuilder.tip_result`); a gate's whole process
tree and its labelled containers go when it is stopped (:meth:`Rebuilder._run_gate`).

**TEST ONLY: the stub agent** (``[rebuild] agent = "stub"``): round k writes the files of
``rounds[k-1]`` of ``stub-rebuild.json`` in the worktree's project dir
(``{"rounds": [{"files": {"path": "content"}, "hold": 0, "summary": "...",
"error": "..."}]}``; paths relative to the project dir; ``hold`` sleeps that many seconds
first, interruptible by DELETE; ``error`` makes it an error round; a ``null`` content
deletes the file; a scoped build plays ``scopes[<scope-slug>].rounds`` when present).
Never the default.
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
from lattice.studio import areas as areas_mod
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
from lattice.studio.state import (
    OWNER_ENV,
    StateStore,
    descendants,
    kill_orphan,
    proc_info,
    ps_table,
    remove_owned_containers,
    terminate_tree,
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
FINISHED = ("passed", "gave_up", "stopped", "interrupted")
LIVE = ("queued", "baseline", "running")  # a record found in one of these after a restart
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEVEL_REPAIRS = 2
DEFAULT_STOP_GRACE = 5.0
SCOPE_DIR = ".lattice-scope"  # a node's selection file, in its worktree (git-ignored)
SCOPE_EXCLUDE = "/.lattice-scope/"
GATE_SCAN_S = 1.0  # how often a running gate's descendants are recorded
STUB_LOG_ENV = "LATTICE_STUB_AGENT_LOG"  # TEST ONLY: the stub agent logs each invocation
SPEC_FILES = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")
WORKTREE_DIR = ".lattice/rebuilds"
EXCLUDE_LINE = "/.lattice/rebuilds/"
AGENT_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write", "Bash")
# files the gates or the agent's tooling leave behind; never part of a round's diff or commit
JUNK = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", ".lattice",
        ".lattice-scope")  # fmt: skip

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


SCOPE_RULES = """\
You are the coding agent of a Lattice Studio build from the spec. The project's spec is \
committed; the app does not do all of it yet. Your job is to change the CODE so the checks \
of ONE part of the spec (the scope in the brief) pass, without breaking checks that pass now.

Rules:
- Fix the code (and its tests), never the contract: do not edit REQUIREMENTS.md, spec.py, \
spec.trace.json, the gate scripts or the test harness the gates use, and do not weaken \
them. Edits to the spec files are blocked, and Studio restores them and fails the round.
- Keep behaviour outside the scope as it is: a check that passed before the build and \
fails after it fails the round, even if the scope's checks pass.
- Add tests for the new code where the project has a test convention.
- Run the gates with Bash exactly as given; you have no other shell commands. Do not use \
git: Studio commits the result on its own branch.
- Stop when the scope's checks pass. Studio runs the gates itself after you finish and \
decides from their findings; what you say about them is not trusted.
- Finish with one or two sentences saying what you changed and why."""
SCOPE_BRIEF_FAILURES = 40  # baseline failures listed in the brief


class RebuildError(Exception):
    """A rebuild request refused: ``status`` is the HTTP status, ``code`` an optional
    machine-readable reason (``branch_protected``, ``branch_checked_out``, ...)."""

    def __init__(self, status: int, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


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


def _git_show(repo: Path, sha: str, project_rel: str, name: str) -> str | None:
    """The project file `name` as of commit `sha` (None: no such file)."""
    path = name if project_rel == "." else f"{project_rel}/{name}"
    r = _git(repo, "show", f"{sha}:{path}")
    return r.stdout if r.returncode == 0 else None


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
    def max_attempts(self) -> int:
        """``[rebuild] max_attempts`` (API v1.10): how many times one build, or one plan
        node, may be started (resumed, continued or retried) before a resume answers 409."""
        try:
            return max(1, int(self.table.get("max_attempts") or DEFAULT_MAX_ATTEMPTS))
        except (TypeError, ValueError):
            return DEFAULT_MAX_ATTEMPTS

    @property
    def model(self) -> str | None:
        m = self.table.get("model")
        return str(m) if m else None

    @property
    def worktrees(self) -> str:
        return str(self.table.get("worktrees") or "repo")

    @property
    def areas(self) -> str:
        return "none" if str(self.table.get("areas") or "sections") == "none" else "sections"

    @property
    def metrics_hook(self) -> str | None:
        h = self.table.get("metrics_hook")
        return str(h) if isinstance(h, str) and h.strip() else None

    @property
    def findings_json(self) -> str | None:
        f = self.table.get("findings_json")
        return str(f) if isinstance(f, str) and f.strip() else None

    @property
    def protected_branches(self) -> list[str]:
        b = self.table.get("protected_branches")
        if isinstance(b, list):
            return [str(x) for x in b if str(x).strip()]
        return ["main", "master"]

    @property
    def planner(self) -> str:
        """``[rebuild] planner``: ``"agent"`` (``claude -p``, the default) or ``"stub"``
        (TEST ONLY: ``stub-plan.json`` in the project dir, API.md v1.9)."""
        return "stub" if str(self.table.get("planner") or "agent") == "stub" else "agent"

    @property
    def planner_model(self) -> str:
        m = self.table.get("planner_model")
        return str(m) if isinstance(m, str) and m.strip() else "opus"

    @property
    def planner_timeout(self) -> float:
        return float(self.table.get("planner_timeout") or 900)

    @property
    def models(self) -> dict[str, str]:
        """``[rebuild] models``: the model of each plan tier (fast, mid, frontier)."""
        out = {"fast": "haiku", "mid": "sonnet", "frontier": "opus"}
        m = self.table.get("models")
        if isinstance(m, dict):
            out.update({str(k): str(v) for k, v in m.items() if k in out and str(v).strip()})
        return out

    @property
    def max_parallel(self) -> int:
        try:
            n = int(self.table.get("max_parallel") or 2)
        except (TypeError, ValueError):
            n = 2
        return min(8, max(1, n))

    # -- build performance (API v1.11)
    @property
    def scoped_gate_args(self) -> list[str] | None:
        """``[rebuild] scoped_gate_args`` (e.g. ``["--layers", "CD", "--x-repeats", "1",
        "--await-cap", "5"]``): appended to the first gate command (the Lattice check,
        which forwards ``"$@"`` last) for a plan node's gate runs, with ``--only-file
        <selection>``. None (the default): every gate runs as configured, as before."""
        a = self.table.get("scoped_gate_args")
        if isinstance(a, list) and all(isinstance(x, str) for x in a):
            return [str(x) for x in a]
        return None

    @property
    def level_gate(self) -> str:
        """``[rebuild] level_gate``: ``"full"`` (default: the full gate after every level)
        or ``"regression"`` (the first gate on the scenarios that passed before the level
        and those of every scope landed so far, full layers; the last level, and the
        integration node, always run the unscoped full gate)."""
        return "regression" if self.table.get("level_gate") == "regression" else "full"

    @property
    def level_repair_attempts(self) -> int:
        """``[rebuild] level_repair_attempts`` (default 2; 0 turns the repair loop off):
        repair rounds after a failing level gate before the plan stops ``partial``."""
        v = self.table.get("level_repair_attempts")
        if v is None:
            return DEFAULT_LEVEL_REPAIRS
        try:
            return min(10, max(0, int(v)))
        except (TypeError, ValueError):
            return DEFAULT_LEVEL_REPAIRS

    @property
    def node_timeout(self) -> float:
        """``[rebuild] node_timeout``: seconds one plan node's agent step may run (default
        ``round_timeout``, 1800)."""
        v = self.table.get("node_timeout")
        try:
            return float(v) if v else self.round_timeout
        except (TypeError, ValueError):
            return self.round_timeout

    @property
    def node_max_tokens(self) -> int | None:
        """``[rebuild] node_max_tokens``: output tokens one node's agent may spend in all
        (its rounds and repairs); reached, the step is stopped like a timeout. None: no
        cap."""
        v = self.table.get("node_max_tokens")
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    @property
    def stop_grace(self) -> float:
        """``[rebuild] stop_grace``: seconds a stopped gate's process group gets after
        SIGTERM before SIGKILL (the Lattice check's cleanup runs then; default 5)."""
        try:
            return max(0.0, float(self.table.get("stop_grace", DEFAULT_STOP_GRACE)))
        except (TypeError, ValueError):
            return DEFAULT_STOP_GRACE

    def plan_defaults(self) -> dict[str, Any]:
        """``plan_defaults`` of ``GET /api/projects`` (API v1.11)."""
        return {
            "max_parallel": self.max_parallel,
            "models": self.models,
            "scoped_gate": self.scoped_gate_args,
            "level_gate": self.level_gate,
            "node_timeout": self.node_timeout,
            "level_repair_attempts": self.level_repair_attempts,
        }

    def integration_branch(self, name: str) -> str:
        b = self.table.get("integration_branch")
        if isinstance(b, str) and b.strip():
            return b.strip()
        return f"build/{areas_mod.slug(name, 60)}"


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


# ---- scoped gates and per-tip results (API v1.11) -------------------------------------------


def _scenario(t: str) -> bool:
    return str(t).startswith("scenario:")


def trace_scenarios(trace: Mapping[str, Any]) -> list[str]:
    """Every ``scenario:<name>`` the trace links to a rule, in order."""
    out: dict[str, None] = {}
    for targets in (trace.get("links") or {}).values():
        for t in targets or []:
            if _scenario(t):
                out[str(t)] = None
    return list(out)


def report_scenarios(data: Mapping[str, Any] | None) -> list[str]:
    """The scenarios a ``lattice.spec check --json`` report ran in layer D (its
    ``timings.scenarios[].name``, else its ``selection.scenarios``)."""
    if not isinstance(data, Mapping):
        return []
    tim = data.get("timings") if isinstance(data.get("timings"), Mapping) else {}
    names = [str(x.get("name")) for x in tim.get("scenarios") or [] if isinstance(x, Mapping)]
    if not names and isinstance(data.get("selection"), Mapping):
        names = [str(x) for x in data["selection"].get("scenarios") or []]
    return [f"scenario:{n}" for n in names if n]


def passing_scenarios(failing: Sequence[str], universe: Sequence[str]) -> list[str]:
    """The scenarios of `universe` that did not fail (the regression set)."""
    bad = set(failing)
    return [t for t in dict.fromkeys(universe) if _scenario(t) and t not in bad]


def build_selection(
    scope_targets: Sequence[str], failing_in_scope: Sequence[str], passing: Sequence[str]
) -> dict[str, Any]:
    """A node's scoped-gate selection: the scenarios of its scope (every scenario target its
    rules link to) UNION the scenarios that passed at the start of its level (the
    regression set). An in-scope failing scenario missing from it is a bug."""
    scope = [t for t in dict.fromkeys(scope_targets) if _scenario(t)]
    reg = [t for t in dict.fromkeys(passing) if _scenario(t)]
    chosen = list(dict.fromkeys([*scope, *reg]))
    missing = [t for t in failing_in_scope if _scenario(t) and t not in chosen]
    assert not missing, f"in-scope targets missing from the selection: {missing}"
    return {"scenarios": chosen, "in_scope": scope, "regression_set": reg}


def result_doc(
    *,
    kind: str,
    gates: Sequence[Mapping[str, Any]],
    counts: Mapping[str, int],
    findings: Sequence[Mapping[str, Any]],
    report: Sequence[str],
    trace_json: str | Mapping[str, Any],
    gate_cmds: Sequence[str],
    source: str,
    selected: Sequence[str] | None = None,
    last_failing: Sequence[str] = (),
    prev_passing: Sequence[str] = (),
) -> dict[str, Any]:
    """A per-tip result (API v1.11): ``kind`` ``full`` (every scenario ran: what failed is
    ``failing``) or ``regression`` (the first gate on the `selected` scenarios only; a
    scenario not selected keeps its verdict of `last_failing`, i.e. it is carried as
    failing). ``passing`` is every known scenario that does not fail (the trace's, the
    report's, the earlier passing ones and the selected ones): the next level's regression
    set."""
    found = {areas_mod.finding_identity(f) for f in findings}
    carried: set[str] = set()
    if kind == "regression":
        sel = set(selected or ())
        carried = {t for t in last_failing if _scenario(t) and t not in sel}
    failing = found | carried
    if isinstance(trace_json, Mapping):
        trace = trace_json
    else:
        try:
            trace = json.loads(trace_json) if trace_json else {}
        except json.JSONDecodeError:
            trace = {}
    universe = [*trace_scenarios(trace), *report, *prev_passing, *(selected or ()),
                *(t for t in failing if _scenario(t))]  # fmt: skip
    return {
        "kind": kind,
        "failing": sorted(failing),
        "passing": passing_scenarios(sorted(failing), universe),
        "carried": sorted(carried),
        "selected": list(selected) if selected is not None else None,
        "findings": [
            {
                "title": f.get("title") or "",
                "target": f.get("target") or "",
                **({"key": f["key"]} if f.get("key") else {}),
                **({"file": f["file"]} if f.get("file") else {}),
            }
            for f in findings
        ],  # fmt: skip
        "counts": dict(counts),
        "gates": [
            {
                "command": g.get("command"),
                "exit_code": g.get("exit_code"),
                "seconds": g.get("seconds"),
            }
            for g in gates
        ],  # fmt: skip
        "gate_cmds": list(gate_cmds),
        "source": source,
    }


def full_result(
    gates: Sequence[Mapping[str, Any]],
    counts: Mapping[str, int],
    findings: Sequence[Mapping[str, Any]],
    report: Sequence[str],
    trace_json: str,
    gate_cmds: Sequence[str],
    source: str,
) -> dict[str, Any]:
    return result_doc(kind="full", gates=gates, counts=counts, findings=findings, report=report,
                      trace_json=trace_json, gate_cmds=gate_cmds, source=source)  # fmt: skip


def scoped_command(gate: str, args: Sequence[str], selection_file: Path | str) -> str:
    """The first gate's command with the scoped args and ``--only-file`` appended."""
    extra = [*args, "--only-file", str(selection_file)]
    return f"{gate} {' '.join(shlex.quote(a) for a in extra)}"


def write_selection(worktree: Path, scenarios: Sequence[str], name: str = "selection.json") -> Path:
    """The selection file (a JSON array of ``scenario:<name>`` selectors) in the worktree's
    ``.lattice-scope/`` (git-ignored through ``info/exclude``)."""
    d = worktree / SCOPE_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(json.dumps(list(scenarios), indent=1) + "\n", encoding="utf-8")
    return path


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


def scope_brief(
    scope: Mapping[str, Any],
    *,
    trace_json: str,
    gates: Sequence[str],
    targets: Sequence[Mapping[str, Any]],
    out_of_scope: int,
    project_rel: str,
    context_files: Sequence[str] = (),
    changes: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Round 1's prompt of a scoped build: the rules in scope grouped by section, their
    checks, the baseline failures in scope, the context files, the gates and the rules."""
    try:
        data = json.loads(trace_json) if trace_json else {}
    except json.JSONDecodeError:
        data = {}
    rules = {str(r.get("id")): r for r in data.get("rules") or [] if isinstance(r, Mapping)}
    links = _links(trace_json)
    where = "the repository root" if project_rel == "." else f"`{project_rel}/`"
    out = [
        "# Build brief",
        "",
        f"The project's spec lives in {where} (REQUIREMENTS.md, spec.py, spec.trace.json). "
        f"Build the scope below: **{scope.get('title')}** "
        f"({len(scope.get('rules') or [])} rule(s)). Change the code so the scope's failing "
        "checks pass and every check that passes now still passes.",
        "",
    ]
    if context_files:
        out += ["Read first: " + ", ".join(f"`{c}`" for c in context_files), ""]
    for c in changes:
        out += [f"Also built: accepted change {c['id']}: "
                f"{' '.join(str(c.get('request') or '').split())}", ""]  # fmt: skip
    out += ["## Rules in scope", ""]
    section = None
    for rid in scope.get("rules") or []:
        r = rules.get(str(rid)) or {}
        sec = str(r.get("section") or "")
        if sec != section:
            section = sec
            if sec:
                out += [f"### {sec}", ""]
        link = f" (checked by {', '.join(links[rid])})" if links.get(rid) else " (no check)"
        out.append(f"- {rid}: {' '.join(str(r.get('text') or '').split())}{link}")
    out.append("")
    scen = [t for t in scope.get("targets") or [] if str(t).startswith("scenario:")]
    if scen:
        out += [
            "## Scenarios of the scope",
            "",
            ", ".join(s.removeprefix("scenario:") for s in scen),
            "",
        ]
    out += [
        f"## Failing now in scope ({len(targets)}; these must pass)",
        "",
    ]
    for t in list(targets)[:SCOPE_BRIEF_FAILURES]:
        out.append(f"- {t['id']}: {t['label']}")
    if len(targets) > SCOPE_BRIEF_FAILURES:
        out.append(f"- ... and {len(targets) - SCOPE_BRIEF_FAILURES} more (run the gates)")
    out.append("")
    if out_of_scope:
        out += [
            f"{out_of_scope} other check(s) fail now outside the scope: leave them to later "
            "builds (they may stay failing), but do not break any check that passes.",
            "",
        ]
    out += ["## Gates (run from the repository root)", ""]
    out += [f"- `{g}`" for g in gates]
    out += [
        "",
        "Fix the code, never the spec, requirements, trace, gate scripts or test harness; "
        "add tests for new code where the project has a test convention. Stop when the "
        "scope's checks pass and nothing else broke.",
    ]
    return "\n".join(out)


def scope_findings_prompt(n: int, check: Mapping[str, Any], rnd: Mapping[str, Any]) -> str:
    """Round n's prompt (n > 1) of a scoped build: what still fails in scope and what
    regressed."""
    fs = check.get("findings") or []
    scope = [f for f in fs if f.get("in_scope")]
    reg = [f for f in fs if f.get("regression") and not f.get("in_scope")]
    other = [f for f in fs if f.get("target") in ("spec", "agent")]
    out = [
        f"Round {n - 1} did not pass ({rnd.get('scope_passing')}/{rnd.get('scope_total')} "
        "in-scope checks pass). Studio ran the gates; the findings:",
        "",
    ]
    for title, items in (
        ("Still failing in scope", scope),
        ("Regressions (passed before this build, fail now): fix these too", reg),
        ("Other", other),
    ):
        if items:
            out += [f"{title}:"]
            out += [f"- {f.get('target') or f['id']}: {f['title']}" for f in items[:60]]
            if len(items) > 60:
                out.append(f"- ... and {len(items) - 60} more")
            out.append("")
    out += ["Fix the code (never the spec files) and run the gates again."]
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
    # a scoped build (API v1.8); None for a rebuild of accepted changes only
    scope: dict[str, Any] | None = None
    baseline_ids: dict[str, None] = field(default_factory=dict)  # ordered, as a set
    integration: str | None = None
    integration_tip: str | None = None
    created_integration: bool = False
    target_main: bool = False  # the target is checked out in the owner's main worktree
    merge_owner_head: bool = False  # the default target: the owner's new commits merge in
    owner_head: str | None = None
    baseline_done: threading.Event = field(default_factory=threading.Event)
    nothing_to_build: bool = False
    t0: float = field(default_factory=time.monotonic)
    json_warned: bool = False
    # a node of a build plan (API v1.9, lattice/studio/plan.py); None for a stand-alone build
    plan_id: str | None = None
    plan_node: str | None = None
    stub_key: str | None = None  # TEST ONLY: the stub's scopes key (the node id)
    plan_note: str = ""  # appended to round 1's brief
    always_run: bool = False  # a scaffold node runs even with no in-scope failing check
    # durable state and resume (API v1.10)
    start_round: int = 1  # a continued build starts at the round after its last completed one
    wall_offset: float = 0.0  # wall time of the earlier attempts of a continued build
    attempt: int = 1  # which start of this build (or plan node) this is
    skip_prelude: bool = False  # a continued scoped build whose baseline is already done
    worktree_path: str = ""  # where the worktree is (the record says null once removed)
    pid_sink: Callable[[dict[str, Any], bool], None] | None = None  # a plan's integration run
    # build performance (API v1.11)
    selection: dict[str, Any] | None = None  # a node's scoped-gate selection
    repair_brief: str = ""  # a repair round's prompt (the level gate's findings)
    node_kind: str = ""  # the plan node's kind (scaffold, area, integration, repair)
    proc_role: str = ""  # "agent" | "gate": what run.proc is
    report_scenarios: list[str] = field(default_factory=list)  # the last JSON report's D run


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
        store: StateStore | None = None,
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
        # per target branch, the latest gate results: {sha, tip, failing, source, at}
        self.results: dict[str, dict[str, Any]] = {}
        # per commit, the gate results (API v1.11; durable in the store's results/)
        self.tip_results: dict[str, dict[str, Any]] = {}
        # the build plan holding the project's build slot while it runs (API v1.9)
        self.plan_active: str | None = None
        # durable state (API v1.10): records written through, loaded and reconciled here
        self.store = store
        if store is not None:
            self._load()

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

    # -- durable state (API v1.10, lattice/studio/state.py)
    def _alloc(self) -> str:
        """A new build id; with a store, persisted before it is handed out (ids never
        repeat after a restart)."""
        if self.store is not None:
            return f"b{self.store.allocate('builds')}"
        rid = f"b{self.next_id}"
        self.next_id += 1
        return rid

    def _release(self, rid: str) -> None:
        """A refused build leaves no record and no id (as before v1.10)."""
        n = int(rid[1:])
        if self.store is not None:
            self.store.delete("builds", rid)
            self.store.release("builds", n)
        elif self.next_id == n + 1:
            self.next_id -= 1

    def _private(self, run: _Run) -> dict[str, Any]:
        """What a build needs to go on after a restart, beyond its public record."""
        return {
            "changes": run.changes,
            "repo": str(run.repo),
            "worktree": run.worktree_path or str(run.worktree),
            "project_rel": run.project_rel,
            "base": run.base,
            "branch": run.branch,
            "gates": run.gates,
            "targets": run.targets,
            "scope": run.scope,
            "baseline_ids": list(run.baseline_ids),
            "integration": run.integration,
            "integration_tip": run.integration_tip,
            "created_integration": run.created_integration,
            "target_main": run.target_main,
            "merge_owner_head": run.merge_owner_head,
            "owner_head": run.owner_head,
            "session_id": run.session_id,
            "plan_id": run.plan_id,
            "plan_node": run.plan_node,
            "stub_key": run.stub_key,
            "plan_note": run.plan_note,
            "always_run": run.always_run,
            "attempt": run.attempt,
            "selection": run.selection,
            "node_kind": run.node_kind,
        }

    def _save(self, run: _Run) -> None:
        """Write the build's record through to the store (every status transition, round
        and process change). A plan's integration runs are not records."""
        if self.store is None:
            return
        with self.lock:
            rid = run.record.get("id")
            if not rid or self.runs.get(rid) is not run:
                return
            try:
                self.store.save("builds", rid, run.record, self._private(run))
            except OSError as exc:
                self.log(f"rebuild {rid}: the state file could not be written: {exc}")

    def _save_results(self) -> None:
        if self.store is not None:
            with self.lock:
                with contextlib.suppress(OSError):
                    self.store.meta_set("results", self.results)

    # -- per-tip gate results (API v1.11)
    def tip_result(self, tip: str | None) -> dict[str, Any] | None:
        """The gate results recorded for commit `tip` (a full or regression gate), or None."""
        if not tip:
            return None
        with self.lock:
            res = self.tip_results.get(tip)
            if res is None and self.store is not None:
                res = self.store.result_get(tip)
                if res is not None:
                    self.tip_results[tip] = res
            return copy.deepcopy(res) if res is not None else None

    def put_tip_result(self, tip: str, data: Mapping[str, Any]) -> None:
        """Record a gate's results for commit `tip` (never recomputed afterwards)."""
        doc = {**copy.deepcopy(dict(data)), "tip": tip, "at": _now()}
        with self.lock:
            self.tip_results[tip] = doc
            if self.store is not None:
                try:
                    self.store.result_put(tip, doc)
                except OSError as exc:
                    self.log(f"rebuild: the result file of {tip[:10]} could not be written: {exc}")

    def _load(self) -> None:
        """Every build record of the store; a record that was live when Studio stopped is
        reconciled (``interrupted``, its orphan process groups terminated)."""
        assert self.store is not None
        repo = _toplevel(self.cfg.repo_dir) or self.cfg.repo_dir
        for rec, priv in self.store.load("builds"):
            run = self._run_from(rec, priv, repo)
            self.runs[str(rec["id"])] = run
            if rec.get("status") in LIVE:
                self._reconcile(run)
        for run in self.runs.values():
            self._derive_builds(run.record)
        res = self.store.meta_get("results", {})
        self.results = dict(res) if isinstance(res, dict) else {}

    def _run_from(self, rec: dict[str, Any], priv: Mapping[str, Any], repo: Path) -> _Run:
        for k, v in (("pids", []), ("attempts", 1), ("timeline", []), ("interrupted", False),
                     ("interrupted_at", None), ("interrupted_reason", None),
                     ("session_id", None), ("rounds", []), ("warnings", []),
                     ("changes", []), ("usage", _zero_usage())):  # fmt: skip
            rec.setdefault(k, copy.deepcopy(v))
        wt = str(priv.get("worktree") or rec.get("worktree") or "")
        run = _Run(
            record=rec,
            changes=list(priv.get("changes") or []),
            repo=Path(str(priv.get("repo") or repo)),
            worktree=Path(wt) if wt else self.project / ".lattice-missing-worktree",
            project_rel=str(priv.get("project_rel") or "."),
            base=str(priv.get("base") or rec.get("base") or ""),
            branch=str(priv.get("branch") or rec.get("branch") or ""),
            gates=list(priv.get("gates") or rec.get("gates") or []),
            targets=list(
                priv.get("targets")
                or [
                    {"id": t["id"], "label": t.get("label", "")}
                    for t in rec["targets"]
                    if isinstance(t, dict) and "id" in t
                ]
                if isinstance(rec.get("targets"), list)
                else []
            ),
            trace_json="",
            scope=priv.get("scope"),
            baseline_ids=dict.fromkeys(priv.get("baseline_ids") or []),
            integration=priv.get("integration"),
            integration_tip=priv.get("integration_tip"),
            created_integration=bool(priv.get("created_integration")),
            target_main=bool(priv.get("target_main")),
            merge_owner_head=bool(priv.get("merge_owner_head")),
            owner_head=priv.get("owner_head"),
            session_id=priv.get("session_id") or rec.get("session_id"),
            plan_id=priv.get("plan_id") or (rec.get("plan") or {}).get("id"),
            plan_node=priv.get("plan_node") or (rec.get("plan") or {}).get("node"),
            stub_key=priv.get("stub_key"),
            plan_note=str(priv.get("plan_note") or ""),
            always_run=bool(priv.get("always_run")),
            attempt=int(priv.get("attempt") or rec.get("attempts") or 1),
            worktree_path=wt,
            selection=priv.get("selection"),
            node_kind=str(priv.get("node_kind") or ""),
        )
        run.done.set()
        run.baseline_done.set()
        return run

    def _reconcile(self, run: _Run) -> None:
        """A record that was queued, baseline or running when Studio stopped: it becomes
        ``interrupted`` (its completed rounds kept; an unfinished round dropped, its usage
        stays in the record's total), and the process groups it recorded are terminated
        when they still run (pid AND start time AND command line, see state.kill_orphan)."""
        rec = run.record
        killed = reap_orphans(rec.get("pids") or [], self.cfg.stop_grace, self.log)
        now = _now()
        rec["status"] = "interrupted"
        rec["interrupted"] = True
        rec["interrupted_at"] = now
        rec["interrupted_reason"] = "studio_restarted"
        rec["finished"] = rec.get("finished") or now
        rec["rounds"] = [r for r in rec.get("rounds") or [] if r.get("check") is not None]
        rec["pids"] = []
        if killed:
            rec["orphans_killed"] = killed
        msg = "interrupted: Studio restarted while it ran"
        if killed:
            msg += f" ({len(killed)} orphan process group(s) terminated)"
        rec["timeline"].append({"at": now, "event": msg})
        self.log(f"rebuild {rec['id']}: {msg}")
        self._save(run)

    def _derive_builds(self, rec: Mapping[str, Any]) -> None:
        """``model.changes[].build`` from the records (the latest record of a change wins)."""
        state = {"passed": "built", "gave_up": "gave_up", "stopped": "not_built",
                 "interrupted": "interrupted"}.get(str(rec.get("status")))  # fmt: skip
        if state is None:
            return
        for cid in rec.get("changes") or []:
            b: dict[str, Any] = {"state": state, "rebuild_id": rec["id"]}
            if state != "not_built" and rec.get("branch"):
                b["branch"] = rec["branch"]
            if state == "built" and rec.get("commit"):
                b["commit"] = rec["commit"]["sha"]
            self.builds[str(cid)] = b

    def _event(self, run: _Run, event: str) -> None:
        with self.lock:
            run.record.setdefault("timeline", []).append({"at": _now(), "event": event})

    def _track(
        self,
        run: _Run,
        proc: subprocess.Popen[Any],
        role: str,
        match: str,
        containers: str | None = None,
    ) -> None:
        """Record a child process group (its start time and command line) so a Studio
        that restarts after a crash can recognise and terminate it (a gate's also carries
        its container owner token, API v1.11)."""
        info = proc_info(proc.pid, role, match)
        if containers:
            info["containers"] = containers
        self._track_info(run, info)

    def _track_info(self, run: _Run, info: dict[str, Any]) -> None:
        with self.lock:
            run.record.setdefault("pids", []).append(info)
            if run.pid_sink is not None:
                run.pid_sink(info, True)
            self._save(run)

    def _untrack(self, run: _Run, pid: int) -> None:
        with self.lock:
            run.record["pids"] = [p for p in run.record.get("pids") or [] if p.get("pid") != pid]
            if run.pid_sink is not None:
                run.pid_sink({"pid": pid}, False)
            self._save(run)

    # -- areas (API v1.8)
    def trace_data(self) -> dict[str, Any]:
        """The owner's current trace (the project's spec.trace.json)."""
        try:
            data = json.loads((self.project / "spec.trace.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def area_list(self, trace: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        return areas_mod.derive_areas(
            trace if trace is not None else self.trace_data(), self.cfg.areas
        )

    def default_target(self) -> str:
        return self.cfg.integration_branch(self.name)

    def areas(
        self, last_check: Mapping[str, Any] | None = None, branch: str | None = None
    ) -> list[dict[str, Any]]:
        """``GET /api/areas[?branch=B]``: every area with its counts and status on the
        target branch B (default: the default target). ``failing`` comes from the latest
        gate results Studio recorded for B while B's tip is still the commit they describe
        (a scoped build's baseline, or its passing round); for the branch checked out in
        the owner's worktree, else from `last_check` (the Studio check's per-target
        verdicts); else null (unknown)."""
        target = branch or self.default_target()
        repo = _toplevel(self.cfg.repo_dir)
        tip = current = None
        if repo is not None:
            r = _git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{target}")
            tip = r.stdout.strip() if r.returncode == 0 else None
            current = _current_branch(repo)
        tres = self.tip_result(tip)
        with self.lock:
            res = self.results.get(target)
            failing = set(res["failing"]) if res and tip and res["tip"] == tip else None
            if tres is not None and isinstance(tres.get("failing"), list):
                failing = set(tres["failing"])  # the tip's own gate results win (API v1.11)
            building = {
                str(r.scope["id"])
                for r in self.runs.values()
                if r.scope is not None
                and r.integration == target
                and r.record["status"] not in FINISHED
            }
        if (
            failing is None
            and target == current
            and last_check
            and isinstance(last_check.get("targets"), Mapping)
        ):
            failing = {t for t, v in last_check["targets"].items() if v == "fail"}
        out = []
        for a in self.area_list():
            v = areas_mod.area_view(a, failing, building=str(a["id"]) in building)
            v["target_branch"] = target
            out.append(v)
        return out

    def branches(self) -> dict[str, Any]:
        """``GET /api/branches``: the local branches a scoped build can target."""
        repo = _toplevel(self.cfg.repo_dir)
        default = self.default_target()
        protected = self.cfg.protected_branches
        if repo is None:
            return {"branches": [], "current_branch": None, "protected": protected,
                    "default_target": default}  # fmt: skip
        current = _current_branch(repo)
        checked = _checked_out(repo)
        with self.lock:
            used = {r.integration for r in self.runs.values() if r.integration}
        listing = _git(
            repo, "for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/heads"
        ).stdout
        out = []
        for line in listing.splitlines():
            name, _, head = line.partition("\t")
            if not name:
                continue
            ahead = _git(repo, "rev-list", "--count", f"HEAD..{head}")
            out.append(
                {
                    "name": name,
                    "head": head,
                    "current": name == current,
                    "protected": name in protected,
                    "checked_out": checked.get(name),
                    "ahead_of_base": int(ahead.stdout.strip() or 0)
                    if ahead.returncode == 0
                    else None,
                    "is_integration": name == default or name in used,
                }
            )
        return {
            "branches": out,
            "current_branch": current,
            "protected": protected,
            "default_target": default,
        }

    # -- POST /api/rebuild
    def start(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        model: str | None = None,
        max_rounds: int | None = None,
        scope: Any = None,
        baseline_wait: float | None = None,
        branch: Any = None,
        confirm_unsafe_branch: bool = False,
    ) -> dict[str, str]:
        """Start a rebuild of `changes` (accepted change records, already validated by
        the caller) and/or of a spec `scope` (API v1.8: ``"all"``, ``{"area": id}``,
        ``{"rules": [ids]}``). Raises RebuildError (422/404 scope, 409 running or nothing
        to build, 503 unavailable). A scoped start answers once its baseline gate run is
        done (or after `baseline_wait` seconds)."""
        resolved = None
        if scope is not None:
            trace = self.trace_data()
            try:
                resolved = areas_mod.resolve_scope(scope, self.area_list(trace), trace)
            except areas_mod.ScopeError as e:
                raise RebuildError(e.status, e.detail) from None
        elif not changes:
            raise RebuildError(422, "changes must be a non-empty list of ids")
        ok, why = self.available()
        if not ok:
            raise RebuildError(503, f"rebuild is not available: {why}")
        with self.lock:
            if self.plan_active:
                raise RebuildError(
                    409, f"a build plan is running in this project ({self.plan_active})"
                )
            busy = self.running()
            if busy:
                raise RebuildError(409, f"a rebuild is already running in this project ({busy})")
            repo = _toplevel(self.cfg.repo_dir)
            assert repo is not None
            project_rel = self.project.relative_to(repo).as_posix()
            owner_head = _git_ok(repo, "rev-parse", "HEAD")
            integration = tip = None
            created = False
            target: dict[str, Any] = {}
            if resolved is not None:
                target = self._target(repo, branch, confirm_unsafe_branch)
                integration, tip, created = target["name"], target["tip"], target["created"]
                branch = self._branch_name(repo, areas_mod.scope_slug(resolved), numbered=True)
                start_at = tip
            else:
                branch = self._branch_name(repo, str(changes[0]["id"]))
                start_at = owner_head
            rid = self._alloc()
            worktree = self._worktree_dir(repo, rid)
            r = _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), start_at)
            if r.returncode != 0:
                self._release(rid)
                if created:
                    _git(repo, "update-ref", "-d", f"refs/heads/{integration}", str(tip))
                raise RebuildError(500, f"git worktree add failed: {r.stderr.strip()}")
            trace_path = worktree / project_rel / "spec.trace.json"
            trace_json = trace_path.read_text(encoding="utf-8") if trace_path.is_file() else ""
            targets = _targets(changes) if resolved is None else []
            rounds = int(max_rounds or self.cfg.max_rounds)
            record: dict[str, Any] = {
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
                "base": start_at,
                "branch": branch,
                "worktree": str(worktree),
                "merge_hint": None,
                "usage": _zero_usage(),
                "warnings": [],
                "error": None,
                "pids": [],
                "attempts": 1,
                "timeline": [{"at": _now(), "event": "started"}],
                "interrupted": False,
                "interrupted_at": None,
                "interrupted_reason": None,
                "session_id": None,
            }
            if resolved is not None:
                record.update(
                    scope={k: resolved[k] for k in ("kind", "id", "title", "rules")},
                    baseline=None,
                    target_branch=integration,
                    created_branch=created,
                    base_ref=target["base_ref"],
                    commit_range={"from": tip, "to": None},
                    integration_branch=integration,
                    integration_base=tip,
                    owner_head=owner_head,
                    merged_head=None,
                    metrics=None,
                )
            run = _Run(
                record=record,
                changes=[copy.deepcopy(dict(c)) for c in changes],
                repo=repo,
                worktree=worktree,
                project_rel=project_rel,
                base=start_at,
                branch=branch,
                gates=list(self.cfg.gates),
                targets=targets,
                trace_json=trace_json,
                scope=resolved,
                integration=integration,
                integration_tip=tip,
                created_integration=created,
                owner_head=owner_head,
                target_main=bool(target.get("checked_out_main")),
                merge_owner_head=bool(target.get("merge_owner_head")),
                worktree_path=str(worktree),
            )
            if resolved is None:
                run.baseline_done.set()
            self.runs[rid] = run
            for cid in record["changes"]:
                self.builds[cid] = {"state": "rebuilding", "rebuild_id": rid, "branch": branch}
            self._save(run)
            run.thread = threading.Thread(
                target=self._run, args=(run,), daemon=True, name=f"studio-rebuild-{rid}"
            )
            run.thread.start()
        self.log(f"rebuild {rid}: started on {branch} at {start_at[:10]} ({worktree})")
        if resolved is not None:
            limit = baseline_wait
            if limit is None:
                limit = self.cfg.gate_timeout * max(1, len(run.gates)) + 120
            run.baseline_done.wait(limit)
            if run.nothing_to_build:
                if run.thread is not None:
                    run.thread.join(30)
                with self.lock:
                    self.runs.pop(rid, None)
                    for cid in record["changes"]:
                        self.builds.pop(cid, None)
                    self._release(rid)  # the refused build leaves no record and no id
                raise RebuildError(409, "nothing to build: the checks in this scope already pass")
        return {"id": rid}

    def _target(self, repo: Path, spec: Any, confirm: bool, dry: bool = False) -> dict[str, Any]:
        """The target branch of a scoped build (API v1.8): ``{name, tip, created,
        base_ref, checked_out_main, merge_owner_head}``; creates it when asked (never over
        an existing branch). Refuses: 422 a malformed spec or branch name; 404 an unknown
        branch (create=false) or ``from`` ref; 409 ``branch_exists`` (create=true),
        ``branch_checked_out`` (in a linked worktree, always; in the owner's main
        worktree, without ``confirm_unsafe_branch``), ``branch_protected`` (without
        the flag), ``tree_dirty`` (the flag on a main worktree with changes)."""
        default = self.default_target()
        bad = 'branch must be {"name": "<branch>", "create": true|false, "from": "<ref>"}'
        if spec is None:
            name, create, frm = default, None, "HEAD"
        else:
            if not isinstance(spec, Mapping) or set(spec) - {"name", "create", "from"}:
                raise RebuildError(422, bad)
            name, create, frm = spec.get("name"), spec.get("create", False), spec.get("from")
            if not isinstance(name, str) or not name or not isinstance(create, bool):
                raise RebuildError(422, bad)
            if frm is None:
                frm = "HEAD"
            elif not isinstance(frm, str) or not frm or not create:
                raise RebuildError(422, bad + ' ("from" only with "create": true)')
        if _git(repo, "check-ref-format", "--branch", name).returncode != 0 or name.startswith("-"):
            raise RebuildError(422, f"{name!r} is not a valid branch name", "branch_name_invalid")
        ref = f"refs/heads/{name}"
        r = _git(repo, "rev-parse", "-q", "--verify", ref)
        exists = r.returncode == 0
        if create is None:
            create = not exists
        if create and exists:
            raise RebuildError(409, f"branch {name} already exists", "branch_exists")
        if not create and not exists:
            raise RebuildError(404, f"no branch {name}", "no_branch")
        from_sha = None
        if create:
            fr = _git(repo, "rev-parse", "-q", "--verify", f"{frm}^{{commit}}")
            if fr.returncode != 0 or frm.startswith("-"):
                raise RebuildError(404, f"no ref {frm}", "no_ref")
            from_sha = fr.stdout.strip()
        checked = _checked_out(repo)
        current = _current_branch(repo)
        where = checked.get(name)
        if where is not None and name != current:
            raise RebuildError(
                409,
                f"branch {name} is checked out in {where}; Studio only moves a branch that "
                "is checked out nowhere, or in your own worktree with confirmation",
                "branch_checked_out",
            )
        unsafe = None
        if name in self.cfg.protected_branches:
            unsafe = ("branch_protected", f"branch {name} is protected")
        elif name == current:
            unsafe = ("branch_checked_out", f"branch {name} is checked out in your worktree {repo}")
        if unsafe is not None:
            if not confirm:
                raise RebuildError(
                    409,
                    f"{unsafe[1]}; send confirm_unsafe_branch: true to build onto it "
                    "(fast-forward only, with a clean worktree)",
                    unsafe[0],
                )
            dirty = _git(repo, "status", "--porcelain").stdout.strip()
            if dirty:
                raise RebuildError(
                    409,
                    f"your worktree {repo} has uncommitted changes; building onto {name} "
                    "needs a clean worktree",
                    "tree_dirty",
                )
        if dry:  # a build plan validates its target when it is created (API v1.9)
            return {
                "name": name,
                "tip": from_sha if create else r.stdout.strip(),
                "created": False,
                "create": bool(create),
                "from": frm if create else None,
                "base_ref": frm if create else name,
                "checked_out_main": name == current,
                "merge_owner_head": name == default,
            }
        if create:
            assert from_sha is not None
            # the empty old value: git refuses if the branch appeared meanwhile
            if _git(repo, "update-ref", ref, from_sha, "").returncode != 0:
                raise RebuildError(409, f"branch {name} already exists", "branch_exists")
            tip = from_sha
        else:
            tip = r.stdout.strip()
        return {
            "name": name,
            "tip": tip,
            "created": bool(create),
            "base_ref": frm if create else name,
            "checked_out_main": name == current,
            "merge_owner_head": name == default,
        }

    def _branch_name(self, repo: Path, cid: str, numbered: bool = False) -> str:
        base = f"rebuild/{re.sub(r'[^A-Za-z0-9._-]', '-', cid)}"
        if numbered:
            name, n = f"{base}-1", 2
        else:
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

    # -- build plans (API v1.9): node builds that run concurrently, and integration worktrees
    def repo(self) -> Path:
        repo = _toplevel(self.cfg.repo_dir)
        if repo is None:
            raise RebuildError(503, f"{self.cfg.repo_dir} is not a git repository")
        return repo

    def start_node(
        self,
        *,
        plan_id: str,
        node_id: str,
        scope: Mapping[str, Any],
        tip: str,
        target_branch: str,
        baseline: Mapping[str, Any],
        model: str,
        max_rounds: int | None = None,
        always_run: bool = False,
        note: str = "",
        attempt: int = 1,
        passing: Sequence[str] | None = None,
        node_kind: str = "area",
    ) -> str | None:
        """Start one node of build plan `plan_id` as a scoped build on its own branch
        (``rebuild/<plan>-<node>``) in its own worktree made from `tip`, concurrently with
        the plan's other nodes: the one-build-at-a-time rule is the plan's (it holds
        ``plan_active``), not checked here. `baseline` is the full gate on `tip` that the
        plan ran (``{"findings": [{title, target, ...}], "gates": [...], "counts": {}}``);
        the node's targets are its in-scope failing checks. None: nothing to build (no
        in-scope failing check and not `always_run`); nothing was created. The node's
        commit is left on its branch: the plan merges it, the target is not advanced here.
        With ``[rebuild] scoped_gate_args`` (API v1.11) and `passing` (the scenarios that
        passed at the level start), a node other than the integration node runs its gates
        scoped: the first gate with the args and ``--only-file`` of its selection (its
        scope's scenarios and the regression set), written to the worktree's
        ``.lattice-scope/selection.json``; the brief and the agent's allowlist name that
        exact command."""
        with self.lock:
            if self.plan_active != plan_id:
                raise RebuildError(409, f"plan {plan_id} does not hold the build slot")
        repo = self.repo()
        project_rel = self.project.relative_to(repo).as_posix()
        trace = json.loads(_git_show(repo, tip, project_rel, "spec.trace.json") or "{}")
        owners = areas_mod.target_rules(trace)
        resolved = {k: copy.deepcopy(scope[k]) for k in ("kind", "id", "title", "rules", "targets")}
        probe = _Run(record={}, changes=[], repo=repo, worktree=repo, project_rel=project_rel,
                     base=tip, branch="", gates=[], targets=[], trace_json="",
                     scope=resolved)  # fmt: skip
        in_scope = self._in_scope_fn(probe)
        findings, seen = [], {}
        for i, f in enumerate(baseline.get("findings") or [], 1):
            ident = areas_mod.finding_identity(f)
            item = {
                "id": f"f{i}",
                "title": f["title"],
                "target": f.get("target") or "",
                "rules": owners.get(f.get("target") or "", [])
                or ([f["target"]] if re.fullmatch(r"R\d+", f.get("target") or "") else []),
                "in_scope": in_scope(ident),
            }
            findings.append(item)
            seen.setdefault(ident, item)
        targets = [{"id": i, "label": it["title"]} for i, it in seen.items() if it["in_scope"]]
        if not targets and not always_run:
            return None
        with self.lock:
            branch = self._branch_name(repo, f"{plan_id}-{node_id}")
            rid = self._alloc()
            worktree = self._worktree_dir(repo, rid)
            r = _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), tip)
            if r.returncode != 0:
                self._release(rid)
                raise RebuildError(500, f"git worktree add failed: {r.stderr.strip()}")
            trace_path = worktree / project_rel / "spec.trace.json"
            trace_json = trace_path.read_text(encoding="utf-8") if trace_path.is_file() else ""
            rounds = int(max_rounds or self.cfg.max_rounds)
            gates = list(self.cfg.gates)
            selection = None
            scoped_info = None
            args = self.cfg.scoped_gate_args
            if args is not None and passing is not None and node_kind != "integration" and gates:
                selection = build_selection(resolved.get("targets") or [],
                                            [t["id"] for t in targets], passing)  # fmt: skip
                _ensure_excluded(repo, SCOPE_EXCLUDE, "Lattice Studio scoped-gate selections")
                path = write_selection(worktree, selection["scenarios"])
                gates[0] = scoped_command(gates[0], args, path)
                scoped_info = {"command": gates[0], "selection_file": str(path),
                               "args": list(args), "scenarios": len(selection["scenarios"]),
                               "in_scope": selection["in_scope"],
                               "regression_set": selection["regression_set"]}  # fmt: skip
            base = {
                "at": _now(),
                "seconds": 0.0,
                "verdict": "pass" if not findings else "fail",
                "counts": dict(baseline.get("counts") or {}),
                "findings": findings,
                "unmapped": [x["id"] for x in findings if not x["rules"]],
                "gates": list(baseline.get("gates") or []),
                "source": f"plan {plan_id} full gate on {tip[:10]}",
            }
            record: dict[str, Any] = {
                "id": rid,
                "project": self.name,
                "changes": [],
                "status": "queued",
                "model": model,
                "agent": self.cfg.agent,
                "max_rounds": rounds,
                "started": _now(),
                "finished": None,
                "rounds": [],
                "targets": [{**t, "done": False} for t in targets],
                "gates": list(gates),
                "scoped_gate": scoped_info,
                "base": tip,
                "branch": branch,
                "worktree": str(worktree),
                "merge_hint": None,
                "usage": _zero_usage(),
                "warnings": [],
                "error": None,
                "scope": {k: resolved[k] for k in ("kind", "id", "title", "rules")},
                "baseline": base,
                "target_branch": target_branch,
                "created_branch": False,
                "base_ref": target_branch,
                "commit_range": {"from": tip, "to": None},
                "integration_branch": target_branch,
                "integration_base": tip,
                "owner_head": None,
                "merged_head": None,
                "metrics": None,
                "plan": {"id": plan_id, "node": node_id},
                "pids": [],
                "attempts": attempt,
                "timeline": [
                    {
                        "at": _now(),
                        "event": f"started (plan {plan_id}, node {node_id}, attempt {attempt})",
                    }
                ],
                "interrupted": False,
                "interrupted_at": None,
                "interrupted_reason": None,
                "session_id": None,
            }
            run = _Run(
                record=record,
                changes=[],
                repo=repo,
                worktree=worktree,
                project_rel=project_rel,
                base=tip,
                branch=branch,
                gates=gates,
                targets=targets,
                trace_json=trace_json,
                scope=resolved,
                baseline_ids=dict.fromkeys(seen),
                integration=None,  # the plan merges and advances, never the node
                integration_tip=tip,
                plan_id=plan_id,
                plan_node=node_id,
                stub_key=node_id,
                plan_note=note,
                always_run=always_run,
                attempt=attempt,
                worktree_path=str(worktree),
                selection=selection,
                node_kind=node_kind,
            )
            run.baseline_done.set()
            self.runs[rid] = run
            self._save(run)
            run.thread = threading.Thread(
                target=self._run, args=(run,), daemon=True, name=f"studio-plan-{plan_id}-{rid}"
            )
            run.thread.start()
        self.log(f"plan {plan_id}: node {node_id} started as {rid} on {branch} at {tip[:10]}")
        return rid

    def node_run(self, rid: str) -> _Run:
        with self.lock:
            return self.runs[rid]

    def integration_worktree(self, sha: str, label: str, stop: threading.Event) -> _Run:
        """A detached worktree at `sha` for a plan's merges and full gate runs (a light
        run record: the gates run through :meth:`gate_round` with its environment)."""
        repo = self.repo()
        project_rel = self.project.relative_to(repo).as_posix()
        worktree = self._worktree_dir(repo, label)
        r = _git(repo, "worktree", "add", "-q", "--detach", str(worktree), sha)
        if r.returncode != 0:
            raise RebuildError(500, f"git worktree add failed: {r.stderr.strip()}")
        trace_path = worktree / project_rel / "spec.trace.json"
        return _Run(
            record={"id": label, "warnings": []},
            changes=[],
            repo=repo,
            worktree=worktree,
            project_rel=project_rel,
            base=sha,
            branch="",
            gates=list(self.cfg.gates),
            targets=[],
            trace_json=trace_path.read_text(encoding="utf-8") if trace_path.is_file() else "",
            stop=stop,
        )

    def gate_round(
        self, run: _Run, n: int
    ) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
        return self._gate_round(run, n)

    def remove_worktree(self, run: _Run) -> None:
        """Remove a worktree Studio made (its branch, if any, stays)."""
        shutil.rmtree(run.worktree, ignore_errors=True)
        _git(run.repo, "worktree", "prune")
        with self.lock:
            if "worktree" in run.record:
                run.record["worktree"] = None
            self._save(run)

    def node_hook(self, rid: str, cwd: Path) -> None:
        """A plan node's deferred metrics hook, run in the plan's integration worktree
        (so sibling nodes never both append to the hook's files on their own branches)."""
        run = self.node_run(rid)
        status = run.record["status"]
        if status in ("passed", "gave_up"):
            self._end_hook(run, status, cwd=cwd)

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
            run.record.setdefault("timeline", []).append({"at": _now(), "event": "stopped"})
            self._save(run)
            # a gate is stopped by its own loop (its whole tree, API v1.11); an agent here
            proc = run.proc if run.proc_role != "gate" else None
        if proc is not None:
            _kill_group(proc, signal.SIGTERM)
        if run.thread is not None and run.thread is not threading.current_thread():
            run.thread.join(join)
        self.log(f"rebuild {rid}: stopped")
        return {"id": rid, "status": "stopped"}

    # -- POST /api/rebuild/{id}/continue (API v1.10)
    def continue_build(
        self, rid: str, *, plan_id: str | None = None, attempt: int | None = None
    ) -> dict[str, Any]:
        """Continue an ``interrupted`` build from where it stopped: in its worktree (made
        again on its branch when it is gone), from the round after its last completed one
        -- the agent's session resumed (``--resume``) when the record has its id, else a
        fresh session whose brief carries the last round's findings; a build interrupted
        before any round (or during its baseline) starts over. Raises RebuildError: 404,
        409 (not interrupted; a plan node outside its plan; the build slot taken; the
        attempts cap ``[rebuild] max_attempts``), 503 unavailable."""
        ok, why = self.available()
        if not ok:
            raise RebuildError(503, f"rebuild is not available: {why}")
        with self.lock:
            run = self.runs.get(rid)
            if run is None:
                raise RebuildError(404, f"no rebuild {rid}")
            rec = run.record
            if rec["status"] != "interrupted":
                raise RebuildError(409, f"rebuild {rid} is {rec['status']}; only an interrupted "
                                   "rebuild can be continued")  # fmt: skip
            node_of = (rec.get("plan") or {}).get("id")
            if plan_id is None:
                if node_of:
                    raise RebuildError(409, f"rebuild {rid} is a node of plan {node_of}: resume "
                                       f"the plan (POST /api/plan/{node_of}/run)")  # fmt: skip
                if self.plan_active:
                    raise RebuildError(
                        409, f"a build plan is running in this project ({self.plan_active})"
                    )
                busy = self.running()
                if busy:
                    raise RebuildError(
                        409, f"a rebuild is already running in this project ({busy})"
                    )
                done = int(rec.get("attempts") or 1)
                cap = self.cfg.max_attempts
                if done >= cap:
                    raise RebuildError(409, f"rebuild {rid} already had {done} attempts (the cap "
                                       f"is [rebuild] max_attempts = {cap}); start a new build "
                                       "instead")  # fmt: skip
            elif self.plan_active != plan_id:
                raise RebuildError(409, f"plan {plan_id} does not hold the build slot")
            repo = run.repo
            wt = run.worktree
            if not (wt.is_dir() and (wt / ".git").exists()):
                has_branch = (
                    bool(run.branch)
                    and _git(
                        repo, "rev-parse", "-q", "--verify", f"refs/heads/{run.branch}"
                    ).returncode
                    == 0
                )
                wt = self._worktree_dir(repo, rid)
                if has_branch:
                    r = _git(repo, "worktree", "add", "-q", str(wt), run.branch)
                else:
                    run.branch = run.branch or self._branch_name(repo, rid)
                    r = _git(repo, "worktree", "add", "-q", "-b", run.branch, str(wt), run.base)
                if r.returncode != 0:
                    raise RebuildError(500, f"git worktree add failed: {r.stderr.strip()}")
                run.worktree = wt
                run.worktree_path = str(wt)
                rec["worktree"] = str(wt)
                rec["branch"] = run.branch
            trace_path = wt / run.project_rel / "spec.trace.json"
            run.trace_json = trace_path.read_text(encoding="utf-8") if trace_path.is_file() else ""
            completed = [r for r in rec.get("rounds") or [] if r.get("check") is not None]
            rec["rounds"] = completed
            scoped_alone = run.scope is not None and run.plan_node is None
            if scoped_alone and not rec.get("baseline"):
                run.skip_prelude, start = False, 1  # interrupted during its baseline
            else:
                run.skip_prelude, start = scoped_alone, len(completed) + 1
            rec["max_rounds"] = max(int(rec.get("max_rounds") or 1), start)
            run.start_round = start
            run.session_id = rec.get("session_id") or run.session_id
            run.attempt = attempt if attempt is not None else int(rec.get("attempts") or 1) + 1
            rec["attempts"] = run.attempt
            run.wall_offset = float((rec.get("usage") or {}).get("wall_s") or 0.0)
            run.t0 = time.monotonic()
            run.stop = threading.Event()
            run.done = threading.Event()
            run.baseline_done = threading.Event()
            run.baseline_done.set()
            run.tmp = None
            rec.update(status="queued", interrupted=False, finished=None, error=None, pids=[])
            how = (f"from round {start}" + (" (session resumed)" if start > 1 and run.session_id
                                            else " (fresh session)"))  # fmt: skip
            rec.setdefault("timeline", []).append(
                {"at": _now(), "event": f"continued {how}, attempt {run.attempt}"}
            )
            for cid in rec.get("changes") or []:
                self.builds[cid] = {"state": "rebuilding", "rebuild_id": rid, "branch": run.branch}
            self._save(run)
            run.thread = threading.Thread(target=self._run, args=(run,), daemon=True,
                                          name=f"studio-rebuild-{rid}-continue")  # fmt: skip
            run.thread.start()
        self.log(f"rebuild {rid}: continued {how} in {wt}")
        return {"id": rid, "status": "running", "from_round": start, "attempts": run.attempt}

    # -- the level repair loop of a build plan (API v1.11)
    def repair_build(
        self,
        rid: str,
        *,
        plan_id: str,
        brief: str,
        targets: Sequence[Mapping[str, Any]],
        baseline_ids: Sequence[str],
        passing: Sequence[str] | None,
        attempt: int,
    ) -> dict[str, Any]:
        """One repair round of plan node build `rid` after its level's gate failed on the
        merged tip: in its worktree on its branch (made again from the branch when it is
        gone), continuing the agent's session (``--resume``) when the record has one (else
        a fresh session whose brief is the node's brief plus `brief`), judged on `targets`
        (the failing checks attributed to the node plus its own targets) with no
        regression against `baseline_ids` (the level start's failing checks), through the
        node's scoped gate on its scope and `passing` (the regression set) when
        ``scoped_gate_args`` is set. A pass commits on top of the node's commit; the plan
        then merges again and re-runs the level gate. Answers at once; the round runs in
        the background (``node_run(rid).done``)."""
        with self.lock:
            run = self.runs.get(rid)
            if run is None:
                raise RebuildError(404, f"no rebuild {rid}")
            rec = run.record
            if self.plan_active != plan_id or (rec.get("plan") or {}).get("id") != plan_id:
                raise RebuildError(409, f"plan {plan_id} does not hold the build slot")
            if rec["status"] not in ("passed", "gave_up"):
                raise RebuildError(409, f"rebuild {rid} is {rec['status']}; only a passed or "
                                   "gave-up node build can be repaired")  # fmt: skip
            repo = run.repo
            wt = run.worktree
            if not (wt.is_dir() and (wt / ".git").exists()):
                wt = self._worktree_dir(repo, rid)
                r = _git(repo, "worktree", "add", "-q", str(wt), run.branch)
                if r.returncode != 0:
                    raise RebuildError(500, f"git worktree add failed: {r.stderr.strip()}")
                run.worktree = wt
                run.worktree_path = str(wt)
                rec["worktree"] = str(wt)
            trace_path = wt / run.project_rel / "spec.trace.json"
            run.trace_json = trace_path.read_text(encoding="utf-8") if trace_path.is_file() else ""
            run.targets = [{"id": str(t["id"]), "label": str(t.get("label") or t["id"])}
                           for t in targets]  # fmt: skip
            run.baseline_ids = dict.fromkeys(str(b) for b in baseline_ids)
            gates = list(self.cfg.gates)
            run.selection = None
            args = self.cfg.scoped_gate_args
            scoped = args is not None and passing is not None and run.node_kind != "integration"
            if scoped and gates:
                assert args is not None and passing is not None
                ids = [t["id"] for t in run.targets]
                scope_targets = list((run.scope or {}).get("targets") or [])
                run.selection = build_selection([*scope_targets, *ids], ids, passing)
                _ensure_excluded(repo, SCOPE_EXCLUDE, "Lattice Studio scoped-gate selections")
                path = write_selection(wt, run.selection["scenarios"])
                gates[0] = scoped_command(gates[0], args, path)
            run.gates = gates
            run.repair_brief = brief
            completed = [r for r in rec.get("rounds") or [] if r.get("check") is not None]
            rec["rounds"] = completed
            start = len(completed) + 1
            run.start_round = start
            rec["max_rounds"] = start  # one round per repair attempt
            run.session_id = rec.get("session_id") or run.session_id
            run.wall_offset = float((rec.get("usage") or {}).get("wall_s") or 0.0)
            run.t0 = time.monotonic()
            run.stop = threading.Event()
            run.done = threading.Event()
            run.baseline_done = threading.Event()
            run.baseline_done.set()
            run.tmp = None
            rec["gates"] = list(gates)
            rec["repairs"] = int(rec.get("repairs") or 0) + 1
            rec.update(status="queued", finished=None, error=None, pids=[])
            rec["targets"] = [{**t, "done": False} for t in run.targets]
            how = "session resumed" if run.session_id else "fresh session"
            rec.setdefault("timeline", []).append({"at": _now(), "repair": True, "event":
                f"repair attempt {attempt}: round {start} ({how}) on "
                f"{len(run.targets)} target(s)"})  # fmt: skip
            self._save(run)
            run.thread = threading.Thread(target=self._run, args=(run,), daemon=True,
                                          name=f"studio-repair-{rid}-{attempt}")  # fmt: skip
            run.thread.start()
        self.log(f"rebuild {rid}: repair attempt {attempt} from round {start} ({how})")
        return {"id": rid, "status": "running", "round": start}

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
            err = {"kind": "internal", "message": f"{type(exc).__name__}: {exc}"}
            self._fail_round(run, err)
            with self.lock:
                if run.record["status"] not in FINISHED:
                    run.record["error"] = err
                    self._finish(run, "gave_up")
        finally:
            if run.stop.is_set():
                self._cleanup_stopped(run)
            if run.tmp is not None:
                shutil.rmtree(run.tmp, ignore_errors=True)
            with self.lock:
                if run.record["finished"] is None:
                    run.record["finished"] = _now()
                run.record["usage"]["wall_s"] = round(
                    run.wall_offset + time.monotonic() - run.t0, 2
                )
                self._save(run)
            run.baseline_done.set()
            run.done.set()

    def _loop(self, run: _Run) -> None:
        rec = run.record
        prelude = run.scope is not None and run.plan_node is None and not run.skip_prelude
        with self.lock:
            if run.stop.is_set():
                return
            rec["status"] = "baseline" if prelude else "running"
            self._save(run)
        if prelude:
            go_on = self._prelude(run)
            run.baseline_done.set()
            if not go_on or run.stop.is_set():
                return
            with self.lock:
                if run.stop.is_set():
                    return
                rec["status"] = "running"
                self._save(run)
        prev_tree = self._tree(run)
        last_check: dict[str, Any] | None = None
        last_gates: list[dict[str, Any]] = []
        last_rnd: dict[str, Any] = {}
        if run.start_round > 1 and rec["rounds"]:
            # a continued build (API v1.10): the next round starts from the last completed
            # one, its findings in the brief (and the agent's session resumed when known)
            last_rnd = rec["rounds"][-1]
            last_check = last_rnd.get("check")
            last_gates = list(last_rnd.get("gates") or [])
        for n in range(run.start_round, int(rec["max_rounds"]) + 1):
            if run.stop.is_set():
                return
            t_round = time.monotonic()
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
                self._save(run)
            prompt = self._prompt(run, n, last_check, last_gates, last_rnd)
            agent = self._agent_step(run, n, prompt)
            # record what the agent step cost BEFORE any later step can raise (B3)
            u = agent.get("usage") or {}
            with self.lock:
                rnd["agent"] = u
                _add_usage(rec["usage"], u)
            if run.stop.is_set():
                return
            # integrity: the contract must be the base commit's, byte for byte
            edited = self._restore_protected(run)
            if run.stop.is_set():
                return
            gates, counts, gate_findings = self._gate_round(run, n)
            if run.stop.is_set():
                return
            tree = self._tree(run)
            files = _tree_diff(run.worktree, prev_tree, tree)
            prev_tree = tree
            findings: list[dict[str, Any]] = []
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
            extra: dict[str, Any] = {}
            if run.scope is None:
                findings += gate_findings
                passed = bool(gates) and all(g["exit_code"] == 0 for g in gates) and not edited
                if passed:
                    findings = []  # a passing gate's informational FAIL-looking lines
            else:
                passed, scoped, extra = self._judge(run, gates, gate_findings)
                passed = passed and not edited
                findings += scoped
            failing_now = extra.pop("_failing", None)
            for i, f in enumerate(findings, 1):
                f["id"] = f"f{i}"
            check = {
                "verdict": "pass" if passed else "fail",
                "counts": counts,
                "findings": [_public_finding(f, run.scope is not None) for f in findings],
            }
            if (agent.get("error") or {}).get("kind") == "timeout":
                extra["status"] = "timeout"  # its partial usage is in `agent`, its files kept
            with self.lock:
                rnd.update(
                    finished=_now(),
                    agent_summary=agent.get("summary") or "",
                    files_changed=files,
                    check=check,
                    gates=gates,
                    spec_edited=edited,
                    agent=u,
                    error=agent.get("error"),
                    wall_s=round(time.monotonic() - t_round, 2),
                    **extra,
                )
                rec["usage"]["wall_s"] = round(run.wall_offset + time.monotonic() - run.t0, 2)
                self._save(run)
            self.log(
                f"rebuild {rec['id']}: round {n} {check['verdict']} "
                f"({len(findings)} finding(s), {len(files)} file(s) changed)"
            )
            if passed:
                self._commit(run, n, failing_now)
                return
            last_check, last_gates, last_rnd = check, gates, rnd
            if (agent.get("error") or {}).get("kind") == "usage_limit":
                self._end_hook(run, "gave_up")
                with self.lock:
                    rec["error"] = agent["error"]
                    self._finish(run, "gave_up")
                return
        if not run.stop.is_set():
            self._end_hook(run, "gave_up")
        with self.lock:
            if not run.stop.is_set():
                self._finish(run, "gave_up")

    def _prompt(
        self,
        run: _Run,
        n: int,
        last_check: Mapping[str, Any] | None,
        last_gates: Sequence[Mapping[str, Any]],
        last_rnd: Mapping[str, Any],
    ) -> str:
        def first() -> str:
            if run.scope is None:
                return brief(
                    run.changes,
                    trace_json=run.trace_json,
                    gates=run.gates,
                    targets=run.targets,
                    project_rel=run.project_rel,
                )
            base = run.record.get("baseline") or {}
            out_of = sum(1 for f in base.get("findings") or [] if not f.get("in_scope"))
            text = scope_brief(
                run.scope,
                trace_json=run.trace_json,
                gates=run.gates,
                targets=run.targets,
                out_of_scope=out_of,
                project_rel=run.project_rel,
                context_files=self._context_files(run),
                changes=run.changes,
            )
            text += "\n\n" + run.plan_note if run.plan_note else ""
            budget = self._budget_note(run)
            return text + ("\n\n" + budget if budget else "")

        def again() -> str:
            assert last_check is not None
            if run.scope is None:
                return findings_prompt(n, last_check, last_gates)
            text = scope_findings_prompt(n, last_check, last_rnd)
            for g in last_gates:
                if g.get("exit_code") not in (0, 1):
                    tail = str(g.get("output_tail") or "")[-3000:]
                    text += (
                        f"\n\n`{g['command']}` exited {g.get('exit_code')}; the end of its "
                        f"output:\n```\n{tail.rstrip()}\n```"
                    )
            return text

        if run.repair_brief:  # a repair round after a failing level gate (API v1.11)
            return run.repair_brief if run.session_id else first() + "\n\n" + run.repair_brief
        if n == 1 or last_check is None:
            return first()
        if not run.session_id:
            return first() + "\n\n" + again()
        return again()

    def _budget_note(self, run: _Run) -> str:
        """A plan node's turn budget (API v1.11): its minutes, and the gate to run."""
        if run.plan_node is None:
            return ""
        minutes = max(1, round(self.cfg.node_timeout / 60))
        out = ["## Budget", "", f"You have {minutes} minutes for this node."]
        if run.selection is not None and run.gates:
            out.append(
                f"Run the scoped gate `{run.gates[0]}`, not the full one: it checks this "
                f"node's scope and the {len(run.selection['regression_set'])} scenario(s) "
                "that pass now. Studio runs the full gate itself when the level ends."
            )
        else:
            out.append("Run the gates above, as given; do not start other long-running commands.")
        cap = self.cfg.node_max_tokens
        if cap:
            out.append(f"Your output budget for this node is {cap} tokens in all.")
        return "\n".join(out)

    def _context_files(self, run: _Run) -> list[str]:
        rel = "" if run.project_rel == "." else run.project_rel + "/"
        return [
            f"{rel}{n}"
            for n in ("CONTEXT.md", "README.md")
            if (run.worktree / run.project_rel / n).is_file()
        ]

    # -- a scoped build: the integration branch, the baseline and the judgement
    def _prelude(self, run: _Run) -> bool:
        """Merge the owner's HEAD into the worktree when the integration branch lacks it,
        run the baseline, and pick the scope's targets. False: the build ends here (merge
        conflict, gates that cannot run, nothing to build, stopped)."""
        rec = run.record
        assert run.scope is not None and run.owner_head and run.integration_tip
        wt = run.worktree
        if (
            run.merge_owner_head
            and _git(wt, "merge-base", "--is-ancestor", run.owner_head, "HEAD").returncode != 0
        ):
            r = _git(wt, "merge", "--no-edit", "-q", run.owner_head, timeout=300)
            if r.returncode != 0:
                _git(wt, "merge", "--abort")
                msg = (
                    f"merging the owner's HEAD {run.owner_head[:10]} into "
                    f"{run.integration} conflicts: "
                    + _short(r.stdout.strip() or r.stderr.strip(), 500)
                )
                with self.lock:
                    rec["error"] = {"kind": "merge_conflict", "message": msg}
                    self._finish(run, "gave_up")
                return False
            with self.lock:
                rec["merged_head"] = run.owner_head
        base = _git_ok(wt, "rev-parse", "HEAD")
        run.base = base
        run.skip_prelude = False
        trace_path = wt / run.project_rel / "spec.trace.json"
        run.trace_json = trace_path.read_text(encoding="utf-8") if trace_path.is_file() else ""
        with self.lock:
            rec["base"] = base
        t0 = time.monotonic()
        gates, counts, findings = self._gate_round(run, 0)
        if run.stop.is_set():
            return False
        trace = json.loads(run.trace_json) if run.trace_json else {}
        owners = areas_mod.target_rules(trace)
        in_scope = self._in_scope_fn(run)
        baseline_findings = []
        seen: dict[str, dict[str, Any]] = {}
        for i, f in enumerate(findings, 1):
            ident = areas_mod.finding_identity(f)
            item = {
                "id": f"f{i}",
                "title": f["title"],
                "target": f.get("target") or "",
                "rules": owners.get(f.get("target") or "", [])
                or ([f["target"]] if re.fullmatch(r"R\d+", f.get("target") or "") else []),
                "in_scope": in_scope(ident),
            }
            baseline_findings.append(item)
            seen.setdefault(ident, item)
        unmapped = [x["id"] for x in baseline_findings if not x["rules"]]
        broken = [
            f"`{g['command']}` exited {g['exit_code']}"
            for g in gates
            if g["exit_code"] not in (0, 1)
        ]
        baseline = {
            "at": _now(),
            "seconds": round(time.monotonic() - t0, 2),
            "verdict": "pass" if all(g["exit_code"] == 0 for g in gates) else "fail",
            "counts": counts,
            "findings": baseline_findings,
            "unmapped": unmapped,
            "gates": gates,
        }
        run.baseline_ids = dict.fromkeys(seen)
        run.targets = [
            {"id": ident, "label": item["title"]}
            for ident, item in seen.items()
            if item["in_scope"]
        ]
        with self.lock:
            rec["baseline"] = baseline
            rec["targets"] = [{**t, "done": False} for t in run.targets]
            rec["usage"]["wall_s"] = round(run.wall_offset + time.monotonic() - run.t0, 2)
            self._save(run)
        if broken or not gates:
            what = "; ".join(broken) or "no gate ran"
            with self.lock:
                rec["error"] = {
                    "kind": "baseline",
                    "message": f"the gates could not run on the base commit: {what}",
                }
                self._finish(run, "gave_up")
            return False
        self._record_results(run, base, set(seen), f"{rec['id']} baseline", tip=run.integration_tip)
        self.put_tip_result(base, full_result(gates, counts, findings, run.report_scenarios,
                                              run.trace_json, self.cfg.gates,
                                              f"{rec['id']} baseline"))  # fmt: skip
        if not run.targets:
            run.nothing_to_build = True
            self._discard(run)
            return False
        self.log(
            f"rebuild {rec['id']}: baseline {len(findings)} finding(s), "
            f"{len(run.targets)} in scope {run.scope['id']}"
        )
        return True

    def _in_scope_fn(self, run: _Run) -> Callable[[str], bool]:
        assert run.scope is not None
        if run.scope["kind"] == areas_mod.ALL:
            return lambda _ident: True
        targets = set(run.scope["targets"]) | set(run.scope["rules"])
        return lambda ident: ident in targets

    def _judge(
        self, run: _Run, gates: Sequence[Mapping[str, Any]], findings: Sequence[dict[str, Any]]
    ) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
        """A scoped round's verdict, decided from the gate findings alone: every in-scope
        target of the baseline passes, and no check that passed in the baseline fails now
        (a regression). Out-of-scope baseline failures may stay. A gate that did not
        complete (exit code other than 0 and 1) fails the round."""
        in_scope = self._in_scope_fn(run)
        cur: dict[str, dict[str, Any]] = {}
        out: list[dict[str, Any]] = []
        for f in findings:
            ident = areas_mod.finding_identity(f)
            g = dict(f)
            g["in_scope"] = in_scope(ident)
            g["regression"] = ident not in run.baseline_ids
            out.append(g)
            cur.setdefault(ident, g)
        target_ids = [t["id"] for t in run.targets]
        still = [t for t in target_ids if t in cur]
        regressions = [i for i in cur if i not in run.baseline_ids]
        fixed = [i for i in run.baseline_ids if i not in cur]
        if run.selection is not None:  # a scoped gate says nothing of unselected scenarios
            chosen = set(run.selection["scenarios"])
            fixed = [i for i in fixed if not i.startswith("scenario:") or i in chosen]
        complete = bool(gates) and all(g["exit_code"] in (0, 1) for g in gates)
        passed = complete and not still and not regressions
        extra = {
            "scope_passing": len(target_ids) - len(still),
            "scope_total": len(target_ids),
            "regressions": regressions,
            "newly_fixed": fixed,
            "_failing": set(cur),
        }
        return passed, out, extra

    def _record_results(
        self, run: _Run, sha: str, failing: set[str], source: str, tip: str | None = None
    ) -> None:
        """The gate results of `sha`, valid for the target branch while its tip is `tip`
        (the baseline: the tip the build started from; a pass: the new tip)."""
        if not run.integration:
            return
        with self.lock:
            self.results[run.integration] = {
                "sha": sha,
                "tip": tip or sha,
                "failing": sorted(failing),
                "source": source,
                "at": _now(),
            }
            self._save_results()

    def _discard(self, run: _Run) -> None:
        """Nothing to build: remove the worktree, the build branch, and an integration
        branch this build created."""
        shutil.rmtree(run.worktree, ignore_errors=True)
        _git(run.repo, "worktree", "prune")
        _git(run.repo, "update-ref", "-d", f"refs/heads/{run.branch}")
        if run.created_integration and run.integration and run.integration_tip:
            _git(run.repo, "update-ref", "-d", f"refs/heads/{run.integration}", run.integration_tip)
        with self.lock:
            run.record["status"] = "stopped"
            run.record["finished"] = _now()
            self._save(run)

    def _fail_round(self, run: _Run, err: dict[str, str]) -> None:
        """A step crashed (Studio's own bug): the open round gets ``error`` and the agent's
        changed files from the worktree status. It never gets ``gates``/``check`` it did not
        produce. The usage was already summed when the agent step ended."""
        with self.lock:
            rounds = run.record["rounds"]
            rnd = rounds[-1] if rounds else None
            if rnd is None or rnd.get("error"):
                return
        files: list[str] = []
        try:
            out = _git_ok(
                run.worktree, "status", "--porcelain", "-uall", "--", ".", *_junk_pathspecs()
            )
            files = sorted({ln[3:].split(" -> ")[-1].strip('"') for ln in out.splitlines() if ln})
        except Exception:  # noqa: BLE001 -- best effort; the error is already being recorded
            pass
        with self.lock:
            rnd["error"] = err
            rnd["finished"] = rnd.get("finished") or _now()
            if not rnd.get("files_changed"):
                rnd["files_changed"] = files
            self._save(run)

    def _finish(self, run: _Run, status: str) -> None:
        """With the lock held: the end state and the changes' build."""
        rec = run.record
        if run.stop.is_set() and status != "stopped":
            return  # DELETE won the race: the record stays stopped
        rec["status"] = status
        rec["finished"] = _now()
        state = {"passed": "built", "gave_up": "gave_up", "stopped": "not_built",
                 "interrupted": "interrupted"}[status]  # fmt: skip
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
        rec.setdefault("timeline", []).append({"at": rec["finished"], "event": status})
        self._save(run)

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
        if run.scope is not None:
            scoped = (script.get("scopes") or {}).get(
                run.stub_key or areas_mod.scope_slug(run.scope)
            )
            if isinstance(scoped, dict):
                rounds = scoped.get("rounds") or []
        step = rounds[n - 1] if n - 1 < len(rounds) else {}
        log_path = (self._env if self._env is not None else os.environ).get(STUB_LOG_ENV)
        if log_path:  # TEST ONLY: which builds ran the agent, so tests can count them
            line = {"build": run.record["id"], "round": n, "plan": run.plan_id,
                    "node": run.plan_node, "key": run.stub_key or (
                        areas_mod.scope_slug(run.scope) if run.scope else None)}  # fmt: skip
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
        hold = min(float(step.get("hold") or 0), STUB_HOLD_MAX)
        if hold and run.stop.wait(hold):
            return {"summary": ""}
        if step.get("run"):  # TEST ONLY: a child process group, as the real agent is one
            cmd = str(step["run"])
            proc = subprocess.Popen(["/bin/sh", "-c", cmd], cwd=str(run.worktree / run.project_rel),
                                    env=self._child_env(run, n), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)  # fmt: skip
            with self.lock:
                run.proc = proc  # type: ignore[assignment]
                run.proc_role = "agent"
            self._track(run, proc, "agent", cmd)
            try:
                while proc.poll() is None:
                    if run.stop.wait(0.1):
                        _kill_group(proc, signal.SIGTERM)  # type: ignore[arg-type]
                        proc.wait(10)
                        break
            finally:
                with self.lock:
                    run.proc = None
                self._untrack(run, proc.pid)
            if run.stop.is_set():
                return {"summary": ""}
        written = []
        for rel, content in (step.get("files") or {}).items():
            p = run.worktree / run.project_rel / rel
            if content is None:  # null deletes the file
                p.unlink(missing_ok=True)
                written.append(f"-{rel}")
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(content), encoding="utf-8")
            written.append(rel)
        out: dict[str, Any] = {
            "summary": step.get("summary")
            or (f"stub: wrote {', '.join(written)}" if written else "stub: no edit"),
            "usage": {
                **_zero_usage(),
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
            rules=RULES if run.scope is None else SCOPE_RULES,
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
            run.proc_role = "agent"
        self._track(run, proc, "agent", str(argv[0]))
        if run.stop.is_set():
            _kill_group(proc, signal.SIGTERM)
        stderr: list[str] = []
        drainer = threading.Thread(target=lambda: stderr.extend(proc.stderr or ()), daemon=True)
        drainer.start()
        timed_out = threading.Event()
        over_budget = threading.Event()
        limit = self.cfg.node_timeout if run.plan_node is not None else self.cfg.round_timeout
        seen = StreamUsage()
        cap = self.cfg.node_max_tokens if run.plan_node is not None else None
        spent = int(run.record.get("usage", {}).get("output_tokens") or 0)

        def on_timeout() -> None:
            timed_out.set()
            _kill_group(proc, signal.SIGTERM)

        timer = threading.Timer(limit, on_timeout)
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
                    seen.feed(rec)
                    if cap and not over_budget.is_set() and spent + seen.output_tokens() > cap:
                        over_budget.set()
                        _kill_group(proc, signal.SIGTERM)
            proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                _kill_group(proc, signal.SIGKILL)
            drainer.join(2)
            with self.lock:
                run.proc = None
            self._untrack(run, proc.pid)
        if t.claude_session_id:
            run.session_id = t.claude_session_id
            with self.lock:
                run.record["session_id"] = t.claude_session_id
        events = t.events_after(0)
        texts = [e["text"] for e in events if e["type"] == "text" and not e.get("partial")]
        result = next((e for e in reversed(events) if e["type"] == "result"), None)
        errors = [e for e in events if e["type"] == "error"]
        out: dict[str, Any] = {"summary": _short(texts[-1], 600) if texts else ""}
        res = result or {}
        out["usage"] = {
            "cost_usd": res.get("cost_usd", 0.0),
            "cost_usd_list_estimate": res.get("cost_usd", 0.0),
            "duration_ms": res.get("duration_ms", 0),
            "input_tokens": res.get("input_tokens", 0),
            "output_tokens": res.get("output_tokens", 0),
            "cache_read_input_tokens": res.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": res.get("cache_creation_input_tokens", 0),
            "num_turns": res.get("num_turns", 0),
            "session_id": t.claude_session_id,
        }
        if result is None and seen.messages:
            # no result event (a timeout, the token cap, a crash): the usage the stream
            # showed so far, from the assistant messages, instead of 0 (API v1.11)
            out["usage"].update(seen.totals())
            out["usage"]["partial"] = True
        if run.stop.is_set():
            return out
        if over_budget.is_set():
            out["error"] = {
                "kind": "timeout",
                "reason": "node_max_tokens",
                "message": f"the agent passed [rebuild] node_max_tokens = {cap} output tokens",
            }
        elif timed_out.is_set():
            out["error"] = {
                "kind": "timeout",
                "reason": "node_timeout" if run.plan_node is not None else "round_timeout",
                "message": f"the agent did not finish within {limit:g}s",
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
            out.append(self._run_gate(run, cmd, env))
        return out

    def _run_gate(self, run: _Run, cmd: str, env: Mapping[str, str]) -> dict[str, Any]:
        """One gate command in its own session (API v1.11): its descendants are recorded
        while it runs (``pids``, role ``gate-child``); a stop or a timeout terminates the
        whole tree (:func:`lattice.studio.state.terminate_tree`: SIGTERM to the group,
        ``[rebuild] stop_grace`` seconds for the check's own cleanup, SIGKILL, then every
        recorded descendant still alive), and the containers the gate's check started
        (labelled with this run's owner token) are removed; so are any it left after
        exiting on its own."""
        token = f"studio-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            ["/bin/sh", "-c", cmd],
            cwd=str(run.worktree),
            env={**env, OWNER_ENV: token},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        with self.lock:
            run.proc = proc
            run.proc_role = "gate"
        self._track(run, proc, "gate", cmd, containers=token)
        chunks: list[str] = []
        drainer = threading.Thread(target=lambda: chunks.extend(proc.stdout or ()), daemon=True)
        drainer.start()
        children: dict[int, dict[str, Any]] = {}

        def scan() -> None:
            table = ps_table()
            for pid, c in list(children.items()):  # exited ones are no longer anyone's
                live = table.get(pid)
                if live is None or live[2] != c.get("started"):
                    del children[pid]
                    self._untrack(run, pid)
            for c in descendants(proc.pid, table):
                if c["pid"] not in children:
                    children[c["pid"]] = c
                    self._track_info(run, c)

        deadline = time.monotonic() + self.cfg.gate_timeout
        next_scan = time.monotonic() + 0.3
        why = None
        rc: int | None = None
        try:
            while True:
                try:
                    rc = proc.wait(0.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = time.monotonic()
                if run.stop.is_set():
                    why = "stopped"
                elif now >= deadline:
                    why = "timeout"
                if why:
                    scan()
                    terminate_tree(proc, list(children.values()), grace=self.cfg.stop_grace,
                                   log=self.log)  # fmt: skip
                    rc = proc.poll()
                    break
                if now >= next_scan:
                    scan()
                    next_scan = now + GATE_SCAN_S
            if why is None and children:
                # the gate exited on its own: whatever it started and left running goes too
                terminate_tree(None, list(children.values()), grace=self.cfg.stop_grace,
                               log=self.log)  # fmt: skip
        finally:
            drainer.join(10)
            output = "".join(chunks)
            if why == "timeout":
                output += f"\n[studio] the gate timed out after {self.cfg.gate_timeout:g}s\n"
                rc = 124
            elif rc is None:
                rc = proc.poll() if proc.poll() is not None else -signal.SIGKILL
            removed: list[str] = []
            if why is not None or rc not in (0, 1):
                removed = remove_owned_containers(token, self.log)
            with self.lock:
                run.proc = None
                run.proc_role = ""
            for pid in list(children):
                self._untrack(run, pid)
            self._untrack(run, proc.pid)
        res: dict[str, Any] = {
            "command": cmd,
            "exit_code": rc,
            "seconds": round(time.perf_counter() - t0, 2),
            "output_tail": (output or "")[-OUTPUT_TAIL:],
        }
        if why == "stopped":
            res["stopped"] = True
        if removed:
            res["containers_removed"] = removed
        return res

    def _gate_round(
        self, run: _Run, n: int
    ) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
        """Run the gates (round `n`; 0 = the baseline): (gates, counts, findings). With
        ``[rebuild] findings_json`` the findings come from that JSON report when a gate
        (re)wrote it during this run; else from the gates' text output. A finding marked
        ``_fallback`` is a gate that failed without a parsable finding."""
        jpath = (
            run.worktree / run.project_rel / self.cfg.findings_json
            if self.cfg.findings_json
            else None
        )
        before = _stat(jpath) if jpath is not None else None
        run.report_scenarios = []
        gates = self._gates(run, n)
        counts: dict[str, int] = {}
        findings: list[dict[str, Any]] = []
        broken: list[dict[str, Any]] = []
        for g in gates:
            c, fs = parse_gate_output(g["command"], g["output_tail"], g["exit_code"])
            if g["exit_code"] == 0:
                fs = []  # a passing gate's informational FAIL-looking lines are no findings
            elif len(fs) == 1 and fs[0]["title"].startswith(f"{g['command']} exited "):
                fs[0]["_fallback"] = True
                if g["exit_code"] != 1:
                    fs[0]["_broken"] = True
                    broken.append(fs[0])
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + v
            findings += fs
        if jpath is not None and gates:
            after = _stat(jpath)
            data = None
            if after is not None and after != before:
                try:
                    data = json.loads(jpath.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
            run.report_scenarios = report_scenarios(data) if isinstance(data, dict) else []
            if isinstance(data, dict) and isinstance(data.get("findings"), list):
                counts, jf = areas_mod.findings_from_json(data)
                # keep the gates that did not complete: the report alone would hide them
                findings = [dict(f) for f in jf] + broken
            elif not run.json_warned:
                run.json_warned = True
                with self.lock:
                    run.record["warnings"].append(
                        f"[rebuild] findings_json {self.cfg.findings_json} was not written "
                        "by the gates; findings were parsed from their text output"
                    )
        return gates, counts, findings

    def _tree(self, run: _Run) -> str:
        return _snapshot_tree(run.worktree)

    def _commit(self, run: _Run, n: int, failing: set[str] | None = None) -> None:
        rec = run.record
        requests = "; ".join(" ".join(str(c.get("request") or "").split()) for c in run.changes)
        if run.scope is None:
            lines = [
                f"build: {requests}",
                "",
                "Rebuilt by Lattice Studio from the accepted change(s) "
                f"{', '.join(rec['changes'])}; the gates passed in round {n} of "
                f"{rec['max_rounds']}.",
            ]
        else:
            last = rec["rounds"][-1] if rec["rounds"] else {}
            title = f"build: {run.scope['title']}" + (f"; {requests}" if requests else "")
            if run.repair_brief:  # a repair round after a failing level gate (API v1.11)
                title += f" (repair {rec.get('repairs') or 1} after the level gate)"
            lines = [
                title,
                "",
                f"Built by Lattice Studio from the spec, scope {run.scope['id']} "
                f"({len(run.scope['rules'])} rule(s)"
                + (f", and the accepted change(s) {', '.join(rec['changes'])}" if rec["changes"]
                   else "")
                + f"): {last.get('scope_passing')}/{last.get('scope_total')} in-scope "
                f"checks pass in round {n} of {rec['max_rounds']}, no regressions.",
            ]  # fmt: skip
        lines += ["", f"Lattice-Rebuild: {rec['id']} ({self.name})"]
        if run.plan_id:
            lines.append(f"Lattice-Plan: {run.plan_id}/{run.plan_node}")
        if self.cfg.agent == "claude" and self.trailer:
            lines.append(self.trailer)
        message = "\n".join(lines) + "\n"
        wt = run.worktree
        if run.scope is not None:
            self._end_hook(run, "passed")
        try:
            _git_ok(wt, "add", "-A", "--", ".", *_junk_pathspecs())
            _git_ok(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
            sha = _git_ok(wt, "rev-parse", "HEAD")
            ns = _git_ok(wt, "diff", "--name-status", f"{run.base}..{sha}")
        except RuntimeError as exc:
            err = {"kind": "git", "message": str(exc)}
            self._fail_round(run, err)
            with self.lock:
                rec["error"] = err
                self._finish(run, "gave_up")
            return
        name_status = [" ".join(x.split("\t")) for x in ns.splitlines() if x.strip()]
        files = [x.split("\t")[-1] for x in ns.splitlines() if x.strip()]
        hint = (
            f"Studio never merges. To take the rebuilt code, run `git merge {run.branch}` "
            f"in {run.repo}; the worktree {wt} stays for review."
        )
        if run.plan_id:
            hint = (
                f"Plan {run.plan_id} merges {run.branch} into {rec.get('target_branch')} at "
                "the end of its level, after the full gate."
            )
        moved: dict[str, str] | None = None
        if run.scope is not None and run.integration:
            moved = self._advance(run, sha)
            if moved is None:
                self._record_results(run, sha, set(failing or ()), f"{rec['id']} round {n}")
            last = rec["rounds"][-1] if rec["rounds"] else {}
            if failing is not None:
                fs = [{"title": f["title"], "target": f.get("target") or ""}
                      for f in (last.get("check") or {}).get("findings") or []
                      if f.get("target") not in ("spec", "agent")]  # fmt: skip
                self.put_tip_result(sha, full_result(last.get("gates") or [], last.get("check",
                                    {}).get("counts") or {}, fs, run.report_scenarios,
                                    run.trace_json, self.cfg.gates,
                                    f"{rec['id']} round {n}"))  # fmt: skip
            hint = f"The build is on {run.integration} (now at {sha[:10]}) and {run.branch}; " + (
                "your checkout was fast-forwarded to it."
                if run.target_main
                else f"Studio never merges into your branch: to take it, run "
                f"`git merge {run.integration}` in {run.repo}."
            )
            if moved is not None:
                hint = (
                    f"{run.integration} was NOT advanced ({moved['message']}); nothing is "
                    f"lost: the build commit {sha[:10]} is on {run.branch}."
                )
        with self.lock:
            rec["commit"] = {
                "sha": sha,
                "message": message,
                "files_changed": files,
                "name_status": name_status,
            }
            rec["merge_hint"] = hint
            if "commit_range" in rec:
                rec["commit_range"] = {"from": run.integration_tip, "to": sha}
            if moved is not None:
                rec["error"] = moved
                self._finish(run, "gave_up")
            else:
                self._finish(run, "passed")
        self.log(f"rebuild {rec['id']}: {'passed' if moved is None else 'gave up'} as "
                 f"{sha[:10]} on {run.branch}")  # fmt: skip

    def _advance(self, run: _Run, sha: str) -> dict[str, str] | None:
        """Fast-forward the target branch to `sha`: ``update-ref`` with the tip at start
        as the old value when no worktree has it checked out; ``git merge --ff-only`` in
        the owner's (clean) main worktree when the build was confirmed onto the branch
        checked out there. Never a reset or a forced checkout. None, or the error."""
        return self._advance_to(
            run.repo, str(run.integration), str(run.integration_tip), sha, run.target_main
        )

    def _advance_to(
        self, repo: Path, name: str, old: str, sha: str, target_main: bool
    ) -> dict[str, str] | None:
        """Fast-forward branch `name` from `old` to `sha` (see :meth:`_advance`); also the
        build plan's level end (API v1.9)."""
        if target_main:
            if _current_branch(repo) != name:
                return {"kind": "branch_moved",
                        "message": f"your worktree is no longer on {name}"}  # fmt: skip
            if _git(repo, "status", "--porcelain").stdout.strip():
                return {"kind": "tree_dirty",
                        "message": f"your worktree {repo} has uncommitted changes"}  # fmt: skip
            if _git(repo, "rev-parse", "HEAD").stdout.strip() != old:
                return {"kind": "branch_moved", "message": f"{name} moved during the build"}
            r = _git(repo, "merge", "--ff-only", "-q", sha, timeout=300)
            if r.returncode != 0:
                msg = f"git merge --ff-only failed: {_short(r.stderr, 300)}"
                return {"kind": "branch_moved", "message": msg}
            return None
        where = _checked_out(repo).get(name)
        if where is not None:
            return {"kind": "branch_checked_out",
                    "message": f"{name} was checked out in {where} during the build"}  # fmt: skip
        if _git(repo, "merge-base", "--is-ancestor", old, sha).returncode != 0:
            return {
                "kind": "branch_moved",
                "message": f"{sha[:10]} does not descend from {old[:10]}",
            }
        r = _git(repo, "update-ref", f"refs/heads/{name}", sha, old)
        if r.returncode != 0:
            msg = f"{name} moved during the build: {_short(r.stderr, 300)}"
            return {"kind": "branch_moved", "message": msg}
        return None

    def _end_hook(self, run: _Run, status: str, cwd: Path | None = None) -> None:
        """``[rebuild] metrics_hook`` after a scoped build ends (passed: before the commit,
        so its output is part of it). A failing hook is a warning, never a failed build.
        A plan node's hook is deferred to the plan's level end (`cwd`: the integration
        worktree's project dir; phase ``build_<plan>_<node>``)."""
        hook = self.cfg.metrics_hook
        if run.scope is None or not hook or run.stop.is_set():
            return
        if run.plan_node is not None and cwd is None:
            return
        rec = run.record
        u = rec["usage"]
        slug = areas_mod.scope_slug(run.scope)
        if run.plan_node is not None:
            slug = f"{run.plan_id}_{run.plan_node}"
        rounds = len(rec["rounds"])
        last = rec["rounds"][-1] if rec["rounds"] else {}
        llm = {
            "model": rec["model"],
            "tokens_in": int(u.get("input_tokens") or 0)
            + int(u.get("cache_read_input_tokens") or 0)
            + int(u.get("cache_creation_input_tokens") or 0),
            "tokens_out": int(u.get("output_tokens") or 0),
            "input_tokens_uncached": int(u.get("input_tokens") or 0),
            "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
            "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
            "cost_usd_list_estimate": float(u.get("cost_usd_list_estimate") or 0.0),
            "wall_s": round(run.wall_offset + time.monotonic() - run.t0, 2),
            "rounds": rounds,
            "llm_calls": rounds if self.cfg.agent == "claude" else 0,
            "target_branch": rec.get("target_branch") or run.integration,
        }
        note = (
            f"Lattice Studio build {rec['id']} ({run.scope['title']}): {status} in round "
            f"{rounds} of {rec['max_rounds']}; scope {last.get('scope_passing')}/"
            f"{last.get('scope_total')}, regressions {len(last.get('regressions') or [])}; "
            f"agent {self.cfg.agent}, model {rec['model']}; target branch "
            f"{rec.get('target_branch') or run.integration}"
        )
        if run.attempt > 1:
            note = f"resumed (attempt {run.attempt}); " + note
        if cwd is None:
            cwd = run.worktree / run.project_rel
        fd, tmp = tempfile.mkstemp(prefix="lattice-build-llm-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(llm, f)
            args = ["--phase", f"build_{slug}", "--llm-json", tmp, "--note", note]
            if self.cfg.findings_json and (cwd / self.cfg.findings_json).is_file():
                args += ["--check-json", str(cwd / self.cfg.findings_json)]
            cmd = f"{hook} {' '.join(shlex.quote(a) for a in args)}"
            t0 = time.perf_counter()
            try:
                r = subprocess.run(
                    ["/bin/sh", "-c", cmd],
                    cwd=str(cwd),
                    env=self._child_env(run, rounds),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=self.cfg.gate_timeout,
                    start_new_session=True,
                )
                rc, output = r.returncode, (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired as exc:
                rc = 124
                output = (
                    str(exc.stdout or "")
                    + f"\n[studio] the hook timed out after {self.cfg.gate_timeout:g}s"
                )
            except OSError as exc:
                rc, output = 127, str(exc)
            with self.lock:
                rec["metrics"] = {
                    "command": cmd,
                    "phase": f"build_{slug}",
                    "exit_code": rc,
                    "seconds": round(time.perf_counter() - t0, 2),
                    "output_tail": output[-4000:],
                    "llm": llm,
                }
                if rc != 0:
                    rec["warnings"].append(f"the metrics hook exited {rc}: {_short(output, 300)}")
                self._save(run)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)

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
            self._save(run)


def reap_orphans(
    pids: Sequence[Mapping[str, Any]], grace: float, log: Callable[[str], None]
) -> list[dict[str, Any]]:
    """A restart's reconciliation (API v1.10, extended in v1.11): terminate the recorded
    process groups still running (pid AND start time AND command line), gates first with
    `grace` seconds for the check's own cleanup, then their recorded descendants, then
    remove the containers labelled with the gates' owner tokens. The killed ones."""
    killed = []
    order = sorted(pids, key=lambda i: {"gate": 0, "agent": 1}.get(str(i.get("role")), 2))
    for info in order:
        g = grace if info.get("role") == "gate" else 3.0
        if kill_orphan(info, log, grace=g):
            killed.append({k: info.get(k) for k in ("pid", "pgid", "role", "command")})
    for info in pids:
        for name in remove_owned_containers(info.get("containers"), log):
            killed.append({"container": name, "role": "container"})
    return killed


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


class StreamUsage:
    """The usage a ``claude -p --output-format stream-json`` stream shows before its
    result event: each assistant message's ``usage`` (the last one seen per message id:
    the CLI repeats a message once per content block), summed."""

    def __init__(self) -> None:
        self.messages: dict[str, dict[str, int]] = {}
        self._anon = 0

    def feed(self, rec: Mapping[str, Any]) -> None:
        if rec.get("type") != "assistant" or rec.get("parent_tool_use_id"):
            return
        msg = rec.get("message") if isinstance(rec.get("message"), Mapping) else {}
        u = msg.get("usage") if isinstance(msg.get("usage"), Mapping) else None
        if not u:
            return
        mid = str(msg.get("id") or "")
        if not mid:
            self._anon += 1
            mid = f"anon-{self._anon}"
        self.messages[mid] = {
            k: int(u.get(k) or 0)
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                      "cache_creation_input_tokens")
        }  # fmt: skip

    def output_tokens(self) -> int:
        return sum(m["output_tokens"] for m in self.messages.values())

    def totals(self) -> dict[str, int]:
        out = dict.fromkeys(("input_tokens", "output_tokens", "cache_read_input_tokens",
                             "cache_creation_input_tokens"), 0)  # fmt: skip
        for m in self.messages.values():
            for k in out:
                out[k] += m[k]
        out["num_turns"] = len(self.messages)
        return out


USAGE_KEYS = (
    "cost_usd",
    "cost_usd_list_estimate",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _zero_usage() -> dict[str, Any]:
    """The record's ``usage``: ``cost_usd`` is kept for v1.7 clients; the same figure is
    ``cost_usd_list_estimate`` (the CLI's list-price estimate, not a charge)."""
    return {**dict.fromkeys(USAGE_KEYS, 0), "cost_usd": 0.0, "cost_usd_list_estimate": 0.0,
            "wall_s": 0.0}  # fmt: skip


def _add_usage(total: dict[str, Any], u: Mapping[str, Any]) -> None:
    for k in USAGE_KEYS:
        v = u.get(k)
        if k == "cost_usd_list_estimate" and v is None:
            v = u.get("cost_usd")
        total[k] = total.get(k, 0) + (v or 0)
    for k in ("cost_usd", "cost_usd_list_estimate"):
        total[k] = round(float(total[k]), 6)


def _public_finding(f: Mapping[str, Any], scoped: bool) -> dict[str, Any]:
    out = {"id": f["id"], "title": f["title"], "target": f.get("target") or ""}
    if scoped:
        out["in_scope"] = bool(f.get("in_scope"))
        out["regression"] = bool(f.get("regression"))
    return out


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _junk_pathspecs() -> list[str]:
    # Globs only: a literal ``:(exclude).pytest_cache`` that names an existing, gitignored
    # path makes ``git add`` exit 1 ("paths are ignored"), which killed a real rebuild after
    # the agent had run pytest in the worktree. ``**/`` also matches the root.
    return [f":(exclude,glob)**/{j}/**" for j in JUNK] + [f":(exclude,glob)**/{j}" for j in JUNK]


def _current_branch(repo: Path) -> str | None:
    """The branch checked out in the owner's main worktree (None: detached)."""
    r = _git(repo, "symbolic-ref", "-q", "--short", "HEAD")
    return r.stdout.strip() or None if r.returncode == 0 else None


def _checked_out(repo: Path) -> dict[str, str]:
    """branch -> the worktree it is checked out in (every worktree of the repository)."""
    out: dict[str, str] = {}
    path = ""
    for line in _git(repo, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line.startswith("branch refs/heads/"):
            out[line.removeprefix("branch refs/heads/")] = path
    return out


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


def _ensure_excluded(
    repo: Path, line: str = EXCLUDE_LINE, comment: str = "Lattice Studio rebuild worktrees"
) -> None:
    """`line` (default ``/.lattice/rebuilds/``) in the repository's ``info/exclude`` (not
    .gitignore; shared by every worktree)."""
    common = _git_ok(repo, "rev-parse", "--git-common-dir")
    path = Path(common) if os.path.isabs(common) else repo / common
    exclude = path / "info" / "exclude"
    try:
        text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    except OSError:
        text = ""
    if line in text.splitlines():
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(f"# {comment}\n{line}\n")


def load_table(project: Path) -> dict[str, Any]:
    """``[rebuild]`` of the project's studio.toml (missing or broken: empty)."""
    try:
        data = tomllib.loads((project / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    t = data.get("rebuild")
    return dict(t) if isinstance(t, dict) else {}
