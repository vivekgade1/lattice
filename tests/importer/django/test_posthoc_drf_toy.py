"""The rules-only import of the DRF toy (``drf_toy/``, post-hoc round 2026-10-05): the
shapes the held-out run showed the importer mishandled, on a toy written for the test.

* **Seed order**: the custom users model sits in an app listed after the app whose
  tables reference it; the seed must insert the users table first (it crashed with a
  foreign-key error).
* **ORM calls in plain ViewSet / APIView methods**: ``Model.objects``,
  ``get_object_or_404``, a model serializer's ``save()``, related managers and a module
  helper -- every table is read or written by some route, so none is "outside the API".
* **simplejwt's token routes** are public: the answers to invented credentials refuse
  no caller, and each caller presenting its own credentials is admitted.
* **Owner-or-staff** (``IsAdminOrOwner`` added in ``get_permissions``): never corrected
  to Admin.

The import runs in a subprocess (Django: one project per process)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("django")
pytest.importorskip("rest_framework_simplejwt")

from _django_fe import REPO_ROOT, env, routes, run_module  # noqa: E402

TOY = Path(__file__).resolve().parent / "drf_toy"


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("toyroot")
    shutil.copytree(TOY, root / "drf_toy", ignore=shutil.ignore_patterns("__pycache__"))
    out = root / "out"
    done = subprocess.run(
        [sys.executable, "-m", "lattice.importer", str(root), "--package", "drf_toy",
         "--rules-only", "--no-llm", "--no-corpus", "--out", str(out)],
        cwd=REPO_ROOT, env=env(), capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    report = json.loads((out / "report.json").read_text())
    model = run_module(["lattice.importer.frontends.django_drf", str(root), "drf_toy",
                        "--compact"], root)  # fmt: skip
    return {"report": report, "rules": report["rules"], "out": out, "model": model}


def _items(imported: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return imported["rules"]["items"]


def test_the_seed_inserts_the_custom_users_table_before_the_tables_referencing_it(imported):
    assert imported["rules"]["summary"]["tables"]["total"] >= 4


def test_no_table_is_outside_the_api_and_an_unread_handler_says_why(imported):
    """``GET /api/stats/`` calls ``Stats.count()`` (package code through a class, not
    followed): the front end marks it unread, which vetoes every exclusion."""
    rules = imported["rules"]
    assert rules["outside_api"] == {}
    assert "outside_api" not in rules["summary"]["tables"]
    assert any(n.startswith("no table is set outside the API") and "GET /api/stats/" in n
               and "Stats.count" in n for n in rules["notes"]), rules["notes"]  # fmt: skip


def test_orm_calls_in_plain_view_methods_are_route_tables(imported):
    found = routes(imported["model"])
    assert {"Topic", "Tag", "Topic_tags"} <= set(found[("POST", "/api/topics/")]["tables"])
    assert "Topic" in found[("GET", "/api/topics/")]["tables"]
    assert "Comment" in found[("GET", "/api/topics/{pk}/comments/")]["tables"]
    assert {"Topic", "Comment"} <= set(found[("POST", "/api/topics/{pk}/comments/")]["tables"])
    unread = {k for k, r in found.items() if r.get("unread")}
    assert unread == {("GET", "/api/stats/")}, unread


def test_the_json_login_is_found_and_every_role_logs_in(imported):
    auth = imported["model"]["auth"]
    assert (auth["login_path"], auth["login_encoding"], auth["token_path"]) == (
        "/api/token/", "json", ["access"])  # fmt: skip
    rules = imported["rules"]
    assert not any("no login given" in n or "cannot log in" in n for n in rules["notes"])
    assert _items(imported)["T1 User"]["verdict"] == "verified"


def test_the_token_routes_are_public_and_verified(imported):
    items = _items(imported)
    for route in ("POST /api/token/", "POST /api/token/refresh/"):
        v = items[f"R1 {route}"]
        assert v["verdict"] == "verified", (route, v["reason"])


def test_owner_or_staff_routes_state_owner_or_admin_and_verify(imported):
    items = _items(imported)
    for route in ("PUT /api/topics/{pk}/", "DELETE /api/topics/{pk}/"):
        v = items[f"R2 {route}"]
        assert v["predicted"]["scope"] == "owner_or_admin"
        assert v["verdict"] == "verified", (route, v["reason"])


def test_a_create_names_the_owner_of_the_model_it_creates(imported):
    """``Comment.objects.create(topic=topic, user=request.user)`` sets ``Comment``'s
    owner, not the ``Topic`` the view fetched first."""
    found = routes(imported["model"])
    uses = found[("POST", "/api/topics/{pk}/comments/")]["owner_uses"]
    assert [(u["table"], u["column"]) for u in uses] == [("Comment", "user_id")]


def test_a_lookup_field_path_is_filled_with_that_columns_value(imported):
    """``GET /api/tags/{name}/`` looks a tag up by ``name``: probes address a row by its
    name, never by its key (which answers 404 and refuted the held-out H1's routes)."""
    param = next(p for p in routes(imported["model"])[("GET", "/api/tags/{name}/")]["params"]
                 if p["source"] == "path")  # fmt: skip
    assert param["lookup"] == "name"
    items = _items(imported)
    v = items["T1 Tag"]
    assert v["verdict"] == "verified", v["reason"]
    probes = imported["rules"]["probes"]
    paths = [probes[n]["request"]["path"] for n in items["R1 GET /api/tags/{name}/"]["probes"]]
    assert paths and all(not p.rstrip("/").rsplit("/", 1)[1].isdigit() for p in paths), paths


def test_the_front_end_reads_the_fields_formats(imported):
    """Post-hoc round 2: ``SlugField`` / ``URLField`` columns and serializer fields, and
    the slug a ``lookup_field`` route takes, carry their format."""
    model = imported["model"]
    page = next(t for t in model["tables"] if t["name"] == "Page")
    formats = {c["name"]: c.get("format") for c in page["columns"]}
    assert (formats["slug"], formats["link"], formats["title"]) == ("slug", "url", None)
    param = next(p for p in routes(model)[("GET", "/api/pages/{slug}/")]["params"]
                 if p["source"] == "path")  # fmt: skip
    assert (param["lookup"], param["format"]) == ("slug", "slug")


def test_slug_routes_are_reached_and_verified_not_falsely_refuted(imported):
    """H1 drf_blog's ``{slug}`` routes: the seed's slugs (and the probes' values) are
    slugs, so every probe reaches the route -- no answer is the router's 404, and the
    routes' auth verifies (never "anonymous admitted" on a 404)."""
    import re

    rules = imported["rules"]
    items = _items(imported)
    for route in ("GET /api/pages/{slug}/", "PUT /api/pages/{slug}/",
                  "DELETE /api/pages/{slug}/", "POST /api/pages/"):  # fmt: skip
        v = items[f"R1 {route}"]
        assert v["verdict"] == "verified", (route, v["reason"])
    paths = [p["request"]["path"] for p in rules["probes"].values()
             if p["route"].startswith(("GET /api/pages/{", "PUT /api/pages/{"))]  # fmt: skip
    assert paths and all(re.fullmatch(r"/api/pages/[-a-zA-Z0-9_]+/", p) for p in paths), paths
    assert not [n for n, p in rules["probes"].items() if p["route"].startswith(
        ("GET /api/pages/{", "PUT /api/pages/{", "DELETE /api/pages/{"))
        and p["status"] == 404 and isinstance(p["body"], str)]  # fmt: skip
    for item in ("T3 Page.slug max_len 40", "T3 Page.link max_len 200"):
        assert items[item]["verdict"] == "verified", (item, items[item]["reason"])


def test_owner_or_staff_is_never_corrected_to_admin(imported):
    items = _items(imported)
    for route in ("PUT /api/topics/{pk}/", "DELETE /api/topics/{pk}/"):
        v = items[f"R1 {route}"]
        change = (v.get("correction") or {}).get("change", "")
        assert "-> Admin" not in change, (route, change)
        assert v["verdict"] in ("verified", "verified (corrected)"), (route, v["reason"])
