"""Relationship writes are table evidence (pivot plan 2026-10-05, A.2), Flask +
SQLAlchemy front end, on a toy (``flask_rel_toy/``).

The reserve app's three link tables were set "outside the API" although routes write
them -- through ORM relationship collections (``article.favoriters.append(profile)``
inside a model method, ``self.follows.remove(...)``) the front end did not see. Now:

* a write through a relationship collection -- ``obj.rel.append/remove/extend/...``,
  ``obj.rel = [...]``, a relationship keyword of a model's constructor, in the view or
  in the package functions and model methods it calls -- puts the table holding the
  link (a ``secondary`` table; the target's for a one-to-many) in ``Route.rel_tables``,
  and :func:`outside_api` counts it;
* a relationship write the front end cannot name -- ``getattr(obj, name).append(...)``,
  ``setattr(obj, <name from the request>, ...)`` on a model with relationship
  collections -- marks the route ``unread``, which vetoes every exclusion.

The extraction runs in a subprocess (one app per process)."""

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

REPO_ROOT = Path(__file__).resolve().parents[3]
TOY = Path(__file__).resolve().parent / "flask_rel_toy"

PROBE = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lattice.importer.frontends.flask_app import extract
from lattice.importer.rules import outside_api, outside_gaps
m = extract(Path(sys.argv[1]), "flask_rel_toy")
print(json.dumps({
    "routes": {f"{r.method} {r.path}": {"rel_tables": list(r.rel_tables), "unread": r.unread,
                                        "tables": list(r.tables)} for r in m.routes},
    "outside": outside_api(m),
    "gaps": outside_gaps(m),
}))
"""


def _extract(root: Path, unresolved: bool) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["REL_TOY_UNRESOLVED"] = "1" if unresolved else "0"
    done = subprocess.run([sys.executable, "-c", PROBE, str(root)], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=300, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("relroot")
    shutil.copytree(TOY, root / "flask_rel_toy", ignore=shutil.ignore_patterns("__pycache__"))
    return root


@pytest.fixture(scope="module")
def resolved(root: Path) -> dict[str, Any]:
    return _extract(root, unresolved=False)


@pytest.fixture(scope="module")
def unresolved(root: Path) -> dict[str, Any]:
    return _extract(root, unresolved=True)


def test_relationship_writes_are_route_tables(resolved):
    routes = resolved["routes"]
    assert "post_tags" in routes["POST /posts/{id}/tags/{tag_id}"]["rel_tables"]  # .append
    assert "likes" in routes["POST /posts/{id}/like/{user_id}"]["rel_tables"]  # rel = [...]
    # a model method the view calls: self.starrers.append(user)
    assert "stars" in routes["POST /posts/{id}/star/{user_id}"]["rel_tables"]
    assert routes["GET /posts"]["rel_tables"] == []


def test_a_link_table_written_only_through_a_relationship_is_not_outside_the_api(resolved):
    assert resolved["gaps"] == [], resolved["gaps"]
    # the one table no route touches is still excluded; the link tables are not
    assert set(resolved["outside"]) == {"Archive"}, resolved["outside"]


def test_setattr_over_literal_names_is_resolved(resolved):
    assert resolved["routes"]["PUT /tags/{id}"]["unread"] is None


def test_an_unresolved_relationship_call_vetoes_every_exclusion(unresolved):
    routes = unresolved["routes"]
    attach = routes["POST /posts/{id}/attach/{field}"]["unread"]
    assert attach and "getattr" in attach, attach
    edit = routes["PUT /posts/{id}"]["unread"]
    assert edit and "setattr" in edit and "Post" in edit, edit
    assert unresolved["outside"] == {}
    assert any(g.startswith("POST /posts/{id}/attach/{field}") for g in unresolved["gaps"])
