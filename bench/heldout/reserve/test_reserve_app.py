"""The Phase 2 held-out reserve app (``bench/import_repos/realworld``, plan
``docs/superpowers/plans/2026-10-05-phase2-python-import.md`` Wave 4) run under test.

A copy of ``bench/heldout/test_heldout_apps.py`` (itself a copy of
``tests/bench/test_phase2_apps.py``) with the reserve app and its worker,
``bench/heldout/reserve/_reserve_probe.py``.

The app runs in its own subprocess (``python -m bench.heldout.reserve._reserve_probe``).
In that process the app is seeded, booted under ``httpx.WSGITransport``, logged into as
every role through its own login, sent one authenticated route (anonymously, as every
role and as every refused user), and its ``fidelity.SCENARIOS`` are replayed, each on a
fresh copy of the seed.

The process runs with ``tmp_path`` as its working directory and must leave no file
behind outside it (the vendored tree and the repository root are compared before and
after).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_sqlalchemy")
pytest.importorskip("flask_jwt_extended")
pytest.importorskip("flask_apispec")
pytest.importorskip("flask_bcrypt")
pytest.importorskip("flask_caching")
pytest.importorskip("flask_migrate")
pytest.importorskip("flask_cors")
pytest.importorskip("slugify")

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPORT_REPOS = REPO_ROOT / "bench" / "import_repos"

# app -> (framework, roles, refused users)
APPS: dict[str, tuple[str, set[str], set[str]]] = {
    "realworld": ("flask", {"user"}, set()),
}


def _tree(root: Path) -> dict[str, int]:
    """Every file under ``root`` (bytecode caches aside) with its size."""
    return {
        str(p.relative_to(root)): p.stat().st_size
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def _top(root: Path) -> set[str]:
    return {p.name for p in root.iterdir()}


@pytest.mark.parametrize("app", sorted(APPS))
def test_app_boots_seeds_logs_in_and_replays_fidelity(app: str, tmp_path: Path) -> None:
    _, roles, refused = APPS[app]
    vendored_before = _tree(IMPORT_REPOS)
    root_before = _top(REPO_ROOT)

    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    out = tmp_path / "out"
    run = subprocess.run(
        [sys.executable, "-m", "bench.heldout.reserve._reserve_probe", app, str(out)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, run.stderr[-4000:]
    result = json.loads(run.stdout.strip().splitlines()[-1])

    # boots and seeds
    assert (out / "template.db").stat().st_size > 0
    # an anonymous caller is refused the authenticated route
    assert result["anonymous"] in (401, 403)
    # every role logs in through the app's own login and passes it
    assert set(result["roles"]) == roles
    for role, answer in result["roles"].items():
        assert 200 <= answer["status"] < 300, (role, answer)
    # refused users (inactive / unconfirmed) cannot log in or are refused
    assert set(result["refused"]) == refused
    for handle, answer in result["refused"].items():
        assert "login" in answer or answer["status"] in (401, 403), (handle, answer)
    # the fidelity scenarios replay as recorded
    scenarios = result["fidelity"]
    assert len(scenarios) >= 15  # plan §1.4: 15-25
    failing = {s["scenario"]: s["problems"] for s in scenarios if s["problems"]}
    assert not failing, json.dumps(failing, indent=2)

    # nothing written outside tmp_path
    assert _tree(IMPORT_REPOS) == vendored_before
    assert _top(REPO_ROOT) == root_before


@pytest.mark.parametrize("app", sorted(APPS))
def test_vendored_tree_is_documented(app: str) -> None:
    """LICENSE and PROVENANCE.md are present, every file carrying a ``LATTICE MOD`` line
    is named in PROVENANCE.md, and no vendored module can read a ``.env`` file."""
    root = IMPORT_REPOS / app
    assert (root / "LICENSE").read_text().strip()
    provenance = (root / "PROVENANCE.md").read_text()
    for field in ("Upstream", "Commit", "License", "Date vendored", "Not vendored"):
        assert field in provenance, field
    assert re.search(r"\b[0-9a-f]{40}\b", provenance), "no pinned commit SHA"
    for path in root.rglob("*.py"):
        text = path.read_text()
        if "LATTICE MOD" in text:
            rel = path.relative_to(root).as_posix()
            assert rel in provenance, f"{rel} has LATTICE MOD lines but is not in PROVENANCE.md"
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert "load_dotenv" not in code and "env_file" not in code, (path, line)
    for name in ("__init__.py", "seed.py", "fidelity.py"):
        assert (root / name).is_file(), name
    if APPS[app][0] == "django":
        assert (root / "lattice_settings.py").is_file()
