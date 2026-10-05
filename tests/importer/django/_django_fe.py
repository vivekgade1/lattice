"""Subprocess helpers for the Django front-end tests (plan W2-A): Django's settings, app
registry and connections are process-global, so every test that boots a Django project
does it in its own ``python`` process, one project per process.

:func:`extract_json` runs ``python -m lattice.importer.frontends.django_drf ROOT PACKAGE``
(the front end's extract worker) and returns the AppModel as a dict (``to_dict()``
form: fields still at an omitted default are absent).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


def env() -> dict[str, str]:
    out = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    out["PYTHONDONTWRITEBYTECODE"] = "1"
    out["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + out.get("PYTHONPATH", "")
    return out


def run_module(args: list[str], cwd: Path, timeout: int = 300) -> Any:
    """``python -m <args>`` in ``cwd``; its last stdout line, as JSON."""
    done = subprocess.run(
        [sys.executable, "-m", *args],
        cwd=cwd,
        env=env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def extract_json(package: str, cwd: Path) -> dict[str, Any]:
    return run_module(
        ["lattice.importer.frontends.django_drf", str(REPO_ROOT), package, "--compact"], cwd
    )


def routes(model: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["method"], r["path"]): r for r in model["routes"]}


def tables(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in model["tables"]}


def columns(table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in table["columns"]}


def guards(route: dict[str, Any]) -> list[tuple[str, str, int | None]]:
    return [
        (g["kind"], g["source"].rsplit(":", 1)[-1], g.get("status"))
        for g in route.get("guards", [])
    ]


def owner_uses(route: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(u["table"], u["column"], u["how"]) for u in route["owner_uses"]}
