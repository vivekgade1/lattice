"""Relationship writes are table evidence (pivot plan 2026-10-05, A.2), Django + DRF
front end, on a toy (``drf_rel_toy/``).

A many-to-many's link table written only through relationships -- ``obj.m2m.add /
remove / set / clear``, a related manager held in a local, a model method the view
calls (``post.star(user)``: ``self.starrers.add(user)``), a model serializer writing
the many-to-many field -- is in the route's ``rel_tables`` (and ``tables``), so it is
never "outside the API". A relationship write the front end cannot name
(``getattr(post, field).add(...)``) marks the route ``unread``, which vetoes every
exclusion.

Django boots once per process: each extraction runs in a subprocess."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("django")
pytest.importorskip("rest_framework")

from _django_fe import env  # noqa: E402

TOY = Path(__file__).resolve().parent / "drf_rel_toy"

PROBE = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lattice.importer.frontends.django_drf import extract
from lattice.importer.rules import outside_api, outside_gaps
m = extract(Path(sys.argv[1]), "drf_rel_toy")
print(json.dumps({
    "routes": {f"{r.method} {r.path}": {"rel_tables": list(r.rel_tables), "unread": r.unread,
                                        "tables": list(r.tables)} for r in m.routes},
    "outside": outside_api(m),
    "gaps": outside_gaps(m),
}))
"""


def _extract(root: Path, unresolved: bool) -> dict[str, Any]:
    e = env()
    e["REL_TOY_UNRESOLVED"] = "1" if unresolved else "0"
    done = subprocess.run([sys.executable, "-c", PROBE, str(root)], cwd=root, env=e,
                          capture_output=True, text=True, timeout=300, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("drfrel")
    shutil.copytree(TOY, root / "drf_rel_toy", ignore=shutil.ignore_patterns("__pycache__"))
    return root


@pytest.fixture(scope="module")
def resolved(root: Path) -> dict[str, Any]:
    return _extract(root, unresolved=False)


@pytest.fixture(scope="module")
def unresolved(root: Path) -> dict[str, Any]:
    return _extract(root, unresolved=True)


def test_relationship_writes_are_route_tables(resolved):
    routes = resolved["routes"]
    assert "Post_tags" in routes["POST /api/posts/{pk}/tag/"]["rel_tables"]  # a local manager
    assert "Star" in routes["POST /api/posts/{pk}/star/"]["rel_tables"]  # a model method
    assert "Post_likers" in routes["POST /api/posts/{pk}/like/"]["rel_tables"]  # m2m.add
    # a model serializer writing the many-to-many field
    assert "Post_categories" in routes["PUT /api/posts/{pk}/"]["rel_tables"]
    for r in routes.values():
        assert set(r["rel_tables"]) <= set(r["tables"]), r


def test_a_link_table_written_only_through_a_relationship_is_not_outside_the_api(resolved):
    assert resolved["gaps"] == [], resolved["gaps"]
    outside = set(resolved["outside"])
    assert "Archive" in outside, resolved["outside"]
    assert not outside & {"Post_tags", "Star", "Post_likers", "Post_categories"}, outside


def test_an_unresolved_relationship_call_vetoes_every_exclusion(unresolved):
    attach = unresolved["routes"]["POST /api/posts/{pk}/attach/{field}/"]["unread"]
    assert attach and "getattr" in attach, attach
    assert unresolved["outside"] == {}
    assert any(g.startswith("POST /api/posts/{pk}/attach/{field}/") for g in unresolved["gaps"])
