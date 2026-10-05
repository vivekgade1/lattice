"""The Django front end's ``AppRuntime`` (plan W2-A, §3): the WSGI transport through
Django boots each dev app, an ``AuthDriver`` built from the extracted ``AuthModel`` logs
in as every role (HTTP Basic for snippets; recipes' own ``POST /api/user/token/`` JSON
login, then ``Authorization: Token``), and the app answers its authenticated route --
refusing anonymous and inactive callers with the status the AuthModel predicts.

One subprocess per app (``_runtime_worker.py``): one Django project per process."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("django")
pytest.importorskip("rest_framework")

from _django_fe import REPO_ROOT, env  # noqa: E402

WORKER = Path(__file__).resolve().parent / "_runtime_worker.py"


def _run(app: str, tmp_path: Path) -> dict[str, Any]:
    import json

    done = subprocess.run(
        [sys.executable, str(WORKER), app, str(tmp_path / "out")],
        cwd=tmp_path, env=env(), capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _model_predictions_hold(r: dict[str, Any]) -> None:
    """What the extracted model predicts, the app answers: a bad credential gets the
    AuthModel's ``invalid_answer``, a missing row the implicit 404's detail; the seed
    hook writes rows through the Django models."""
    status, detail = r["model"]["invalid_answer"]
    assert r["calls"]["bad-token"] == {"status": status, "body": {"detail": detail}}
    assert r["calls"]["not-found"] == {"status": 404, "body": {"detail": r["model"]["not_found"]}}
    assert set(r["seeded"].values()) == {1}


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if "__pycache__" not in p.parts}


def test_snippets_runtime(tmp_path: Path) -> None:
    before = _files(REPO_ROOT / "bench" / "import_repos" / "snippets")
    r = _run("snippets", tmp_path)
    assert r["front_end"] == "django" and r["framework"] == "django"
    assert not r["openapi"]
    assert r["scheme"] == "basic" and r["runtime_scheme"] == "basic"
    calls = r["calls"]
    # POST /snippets/: SessionAuthentication first, so anonymous is 403, not 401
    assert calls["anonymous"]["status"] == 403
    assert calls["admin"]["status"] == 201
    assert calls["user"]["status"] == 201
    assert calls["user"]["body"]["owner"] == "alice"
    # carol is inactive: Basic credentials are refused (403 behind Session)
    assert calls["carol"]["status"] == 403
    assert calls["carol"]["body"] == {"detail": "Invalid username/password."}
    assert r["disposed"]
    assert not r["minter"]
    _model_predictions_hold(r)
    assert _files(REPO_ROOT / "bench" / "import_repos" / "snippets") == before


def test_recipes_runtime(tmp_path: Path) -> None:
    r = _run("recipes", tmp_path)
    assert r["front_end"] == "django"
    assert r["scheme"] == "token" and r["runtime_scheme"] == "token"
    calls = r["calls"]
    assert calls["anonymous"]["status"] == 401
    assert calls["admin"]["status"] == 200
    assert calls["admin"]["body"]["email"] == "admin@example.com"
    assert calls["user"]["status"] == 200
    assert calls["user"]["body"] == {"email": "alice@example.com", "name": "Alice Archer"}
    # the login route refuses the inactive user (400); a minted token is refused 401
    assert "400" in calls["carol"]["login_failed"]
    assert r["minter"]
    assert calls["carol-minted"]["status"] == 401
    assert calls["carol-minted"]["body"] == {"detail": "User inactive or deleted."}
    assert r["disposed"]
    _model_predictions_hold(r)
