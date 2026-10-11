"""The dogfood seed project (``dogfood/studio/project_seed``: a toy notes spec, R1..R4 in
two areas, the TEST-ONLY stub planner and stub agent, and ``gate.sh`` shaped like ``lattice
spec check``: ``--layers``, ``--only-file``, a race-only failure and a cross-area clash) as a
fresh git repository, for the API v1.11 tests (scoped gates, the level repair loop, stop
cleanup). No LLM, no API key, no Docker."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from lattice.studio.plan import PlanManager
from lattice.studio.rebuild import Rebuilder, load_table

SEED = Path(__file__).resolve().parents[2] / "dogfood" / "studio" / "project_seed"
W, T, R, N = (
    "scenario:create_a_note",
    "scenario:text_bounds",
    "scenario:read_own_and_other",
    "scenario:no_token",
)


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def seed_project(
    tmp: Path,
    *,
    plans: dict[str, Any] | None = None,
    stub: dict[str, Any] | None = None,
    gate: str | None = None,
) -> Path:
    """A copy of the seed as a git repository (`plans` replaces stub-plan.json's scopes,
    `stub` is merged into stub-rebuild.json's scopes, `gate` replaces gate.sh)."""
    root = tmp / "notes"
    shutil.copytree(SEED, root)
    if plans is not None:
        (root / "stub-plan.json").write_text(json.dumps({"scopes": plans}))
    if stub is not None:
        data = json.loads((root / "stub-rebuild.json").read_text())
        data["scopes"].update(stub)
        (root / "stub-rebuild.json").write_text(json.dumps(data))
    if gate is not None:
        (root / "gate.sh").write_text(gate)
        (root / "gate.sh").chmod(0o755)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "studio@example.com")
    git(root, "config", "user.name", "Studio Test")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


def manager(root: Path, env: dict[str, str] | None = None, **table: Any) -> PlanManager:
    t = {**load_table(root), **table}
    rb = Rebuilder(root, t, name="notes", model="sonnet",
                   env=env if env is not None else dict(os.environ))  # fmt: skip
    return PlanManager(rb)


def run_plan(pm: PlanManager, scope: Any, wait: float = 90) -> dict[str, Any]:
    pid = pm.create({"scope": scope})["id"]
    rec = pm.get(pid, wait=30)
    assert rec["status"] == "ready", rec
    pm.approve(pid)
    pm.run(pid)
    return finish(pm, pid, wait)


def finish(pm: PlanManager, pid: str, wait: float = 90) -> dict[str, Any]:
    t0 = time.time()
    rec = pm.get(pid, wait=min(60, wait))
    while rec["status"] in ("running", "planning") and time.time() - t0 < wait:
        rec = pm.get(pid, wait=min(60, wait))
    assert rec["status"] not in ("running", "planning"), rec
    return rec


def node(nid: str, scope: Any, deps=(), kind: str = "area", files=()) -> dict[str, Any]:
    return {"id": nid, "title": nid, "kind": kind, "scope": scope,
            "depends_on": [{"id": d, "why": "x"} for d in deps], "tier": "mid",
            "touches": {"tables": [], "packages": [], "files": list(files)}, "why": ""}  # fmt: skip


def integ(*deps: str) -> dict[str, Any]:
    return node("integration", "all", deps, kind="integration")


def cleanup_worktrees(root: Path) -> None:
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree ") and "/.lattice/rebuilds/" in line:
            shutil.rmtree(line.removeprefix("worktree "), ignore_errors=True)
    git(root, "worktree", "prune")
