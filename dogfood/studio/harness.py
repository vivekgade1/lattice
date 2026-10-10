"""The Lattice harness of the dogfood check: Lattice Studio's own server, checked against its
spec (``spec.py``) as a black box over HTTP by ``lattice.spec check`` (``check.sh``).

``make_app(db_path)`` answers a :class:`~lattice.spec.process.ProcessApp`: ``python -m
lattice.studio <project> --port $PORT --quiet --stub-llm`` on a fresh copy of
``project_seed/`` (a toy notes spec) made into a git repository, one per scenario. The
checker starts it on a free port, waits for ``GET /api/model`` to answer 200, runs the
scenario and stops it.

* **No database.** Studio keeps its change sets in memory and its project on disk; it
  reads no database. The process runtime still needs a database factory (every scenario
  gets a fresh database file, handed to the app as ``db_env``), so ``build_db`` writes a
  one-table SQLite marker file that Studio never opens. Layers C, B and R and
  ``expect_db`` have nothing to bind to: the spec states everything through answers.
* **No callers.** Studio has no authentication (it binds to 127.0.0.1 only). The spec
  has no users, every step has ``user=None`` and the runtime's auth driver is never
  asked for a token: the process runtime runs anonymous apps as they are.
* **Deterministic drafting.** ``--stub-llm`` replaces Claude with
  ``lattice.studio.server.StubGenerator`` (test only): a change appends its request as
  one rule, and drafting keeps the module and marks the new rule as context.
* **Deterministic commits.** The project's git runs with fixed author, committer and
  dates, and no global or system git config (no signing, no hooks), so the seed commit
  and every commit Studio makes are reproducible: the spec can state commit ids.
* **Mutants.** ``LATTICE_STUDIO_PYTHONPATH``, when set, becomes the server's
  ``PYTHONPATH`` (the checker itself keeps the working tree's ``lattice``): the
  mutation run of docs/results/2026-10-10-dogfood-results.md points it at a patched
  copy of the package.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from lattice.spec.process import ProcessApp, process_runtime

ROOT = Path(__file__).resolve().parent
SEED = ROOT / "project_seed"
PROJECT_NAME = "notes"  # the project directory's name, as GET /api/model reports it

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Studio Dogfood",
    "GIT_AUTHOR_EMAIL": "dogfood@example.com",
    "GIT_AUTHOR_DATE": "2026-10-10T12:00:00+00:00",
    "GIT_COMMITTER_NAME": "Studio Dogfood",
    "GIT_COMMITTER_EMAIL": "dogfood@example.com",
    "GIT_COMMITTER_DATE": "2026-10-10T12:00:00+00:00",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
SERVE = 'exec "$0" -m lattice.studio "$1" --port "$PORT" --quiet --stub-llm'

RUNTIME = process_runtime(framework="studio")  # not a Python web framework: no layer A


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], env={**os.environ, **GIT_ENV},
                   check=True, capture_output=True, timeout=60)  # fmt: skip


def make_project(parent: Path) -> Path:
    """A fresh copy of the seed project as a git repository (one commit), plus an
    untracked file an accept must not commit."""
    root = Path(tempfile.mkdtemp(prefix="studio-", dir=parent)) / PROJECT_NAME
    shutil.copytree(SEED, root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    (root / "SCRATCH.txt").write_text("an untracked file: no accept commits it\n")
    return root


def _seed_agrees() -> None:
    """The spec states the seed's markdown; it must be the seed's file."""
    from spec import SEED_MD

    if (SEED / "REQUIREMENTS.md").read_text(encoding="utf-8") != SEED_MD:
        raise RuntimeError("spec.SEED_MD differs from project_seed/REQUIREMENTS.md")


def make_app(db_path) -> ProcessApp:
    _seed_agrees()
    project = make_project(Path(db_path).resolve().parent)
    env = {**GIT_ENV, "ANTHROPIC_API_KEY": ""}
    if os.environ.get("LATTICE_STUDIO_PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["LATTICE_STUDIO_PYTHONPATH"]
    return ProcessApp(["/bin/sh", "-c", SERVE, sys.executable, str(project)],
                      db_path=db_path, env=env, cwd=project, port_env="PORT",
                      db_env="STUDIO_DOGFOOD_DB", health_path="/api/model",
                      startup_timeout=30)  # fmt: skip


def build_db(path):
    """The database file the process runtime insists on; Studio never opens it."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE dogfood_marker (note TEXT NOT NULL)")
        conn.execute("INSERT INTO dogfood_marker VALUES ('Lattice Studio reads no database')")
        conn.commit()
    finally:
        conn.close()
    return Path(path)
