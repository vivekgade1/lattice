"""The rules-only import of the Flask toy (``flask_toy/``, post-hoc round 2026-10-05):

* **JSON login**: ``POST /auth/login`` reads ``email`` and ``password`` from
  ``request.get_json()`` and answers ``access_token``; the Flask front end finds it
  (an AuthModel with a JSON login), so layer R runs with callers;
* **the protected routes** sit behind a package decorator the front end cannot read
  (stated ``Public``): their anonymous 401s refute that, and with callers the
  correction to ``Authenticated`` holds -- never "verified" as Public;
* **the enum column** is seeded in its stored spelling (``MANAGER``): no false T4
  refutation from the importer's own seed.

The import runs in a subprocess (one app per process, as the CLI runs it)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("flask_sqlalchemy")
pytest.importorskip("jwt")

REPO_ROOT = Path(__file__).resolve().parents[3]
TOY = Path(__file__).resolve().parent / "flask_toy"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("flasktoy")
    shutil.copytree(TOY, root / "flask_toy", ignore=shutil.ignore_patterns("__pycache__"))
    out = root / "out"
    done = subprocess.run(
        [sys.executable, "-m", "lattice.importer", str(root), "--package", "flask_toy",
         "--rules-only", "--no-llm", "--no-corpus", "--out", str(out)],
        cwd=REPO_ROOT, env=_env(), capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    probe = (
        "import json, sys; sys.path.insert(0, sys.argv[1]);"
        "from lattice.importer.frontends.flask_app import extract;"
        "m = extract(__import__('pathlib').Path(sys.argv[1]), 'flask_toy');"
        "a = m.auth; print(json.dumps({'login_path': a.login_path, 'encoding': a.login_encoding,"
        "'username': a.username_field, 'password': a.password_field,"
        "'token_path': list(a.token_path)}))"
    )
    model = subprocess.run([sys.executable, "-c", probe, str(root)], cwd=REPO_ROOT, env=_env(),
                           capture_output=True, text=True, timeout=300, check=False)  # fmt: skip
    assert model.returncode == 0, model.stderr[-4000:]
    report = json.loads((out / "report.json").read_text())
    return {"rules": report["rules"], "auth": json.loads(model.stdout.strip().splitlines()[-1])}


def test_the_json_login_route_is_found(imported):
    assert imported["auth"] == {"login_path": "/auth/login", "encoding": "json",
                                "username": "email", "password": "password",
                                "token_path": ["access_token"]}  # fmt: skip


def test_layer_r_runs_with_callers(imported):
    notes = imported["rules"]["notes"]
    assert not any("no login given" in n or "cannot log in" in n for n in notes), notes


def test_routes_behind_an_unread_decorator_are_never_verified_public(imported):
    items = imported["rules"]["items"]
    for route in ("GET /me", "GET /notes"):
        v = items[f"R1 {route}"]
        change = (v.get("correction") or {}).get("change")
        assert v["verdict"] == "verified (corrected)", (route, v["verdict"], v["reason"])
        assert change == "auth Public -> Authenticated", change


def test_the_login_route_is_verified_public(imported):
    v = imported["rules"]["items"]["R1 POST /auth/login"]
    assert v["verdict"] == "verified", v["reason"]


def test_the_enum_column_is_seeded_as_the_app_stores_it(imported):
    v = imported["rules"]["items"]["T4 User.kind"]
    assert v["verdict"] != "refuted", v["reason"]
