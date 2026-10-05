"""The Phase 2 Wave 3 gate (plan 2026-10-05): the rules-first import of the four dev apps
(snippets, recipes: Django + DRF; microblog, flasky: Flask + SQLAlchemy) and the four
FastAPI apps (template, TaskFlow, JustNotes, multi-vendor), measured by
``bench/phase2_exit.py``, one subprocess per app (Django keeps one project per process).

Per app the gate asserts:

* at least 90% of its tables AND at least 90% of its routes have a verified rule layer
  (``verified`` or ``verified (corrected)`` -- unexercised and unreachable items never
  count; the bar and the verdict rules are not weakened for any app). A table outside
  the API -- no route reads or writes it, by the front end's evidence -- is listed and
  left out of the table denominator (plan 2026-10-05, measurement decision 1);
* its fidelity suite passes (the corrected spec validates, layer R of it on seed variant
  2 has no finding, the recorded scenarios replay with the rule oracle predicting every
  step);
* the exit script's recomputation of the verdicts from the per-item records agrees with
  the report.

An app that falls short for a reason that is the app's own is marked ``xfail`` (strict: a
pass is reported) with the reason, for the user to review -- never by lowering the bar.
Each import's wall time is printed and recorded (``record_property``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bench.phase2_exit import APPS, agreement, recompute

REPO_ROOT = Path(__file__).resolve().parents[2]

# apps short of the gate for a reason of the app's own (see the module docstring). W3b
# (plan 2026-10-05, measurement decisions): microblog's users-to-users link and JustNotes'
# owner-or-public notes are imported (their xfails are gone with them); flasky's `roles`
# and `follows` are outside the API (no route reads or writes them: listed, not counted)
SHORT: dict[str, str] = {
    "justnotes": (
        "routes: 22/30 (73.3%) -- 8 routes are unreachable-by-probe and stay in the "
        "denominator (plan 2026-10-05, measurement decision 3): 7 call a local LLM server "
        "(requests.post to localhost:1234, not part of the vendored app: GET "
        "/notes/{id}/summarize, /extend, /translate/{target_language}, /auto-tag, "
        "/keywords, /sentiment, /related) and GET /notes/{note_id}/keywords is shadowed by "
        "GET /notes/{id}/keywords (the same path, matched first, so no request reaches "
        "it); every other route verifies, so 22/30 is the ceiling. Tables 6/6, fidelity "
        "passes"
    ),
}


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _measure(app: str, out: Path) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, "-m", "bench.phase2_exit", app, "--out", str(out)],
        cwd=REPO_ROOT, env=_env(), capture_output=True, text=True, timeout=1800, check=False,
    )  # fmt: skip
    path = out / f"{app}.json"
    assert path.exists(), done.stderr[-4000:]
    return json.loads(path.read_text())


@pytest.mark.parametrize(
    "app",
    [
        pytest.param(a, marks=pytest.mark.xfail(reason=SHORT[a], strict=True)) if a in SHORT else a
        for a in APPS
    ],  # fmt: skip
)
def test_the_rules_import_gate(app, tmp_path, record_property):
    pytest.importorskip("django")
    pytest.importorskip("flask")
    r = _measure(app, tmp_path)
    assert "error" not in r, r.get("error")
    record_property("import_s", r["import_s"])
    print(f"{app}: tables {r['tables']}, routes {r['routes']}, import {r['import_s']}s, "
          f"stages {r['stage_s']}")  # fmt: skip
    # the exit script's recomputation agrees with the report, whatever else holds
    assert r["agrees"], r["disagreements"]
    assert r["fidelity"]["passed"], r["fidelity"]
    assert r["tables"]["pct"] >= 90, r["unverified_tables"]
    assert r["routes"]["pct"] >= 90, r["unverified_routes"]


# ---- the recomputation (fast) ------------------------------------------------------------------


def _item(item_id: str, verdict: str, reason: str = "") -> dict[str, Any]:
    kind, _, rest = item_id.partition(" ")
    subject = rest if kind.startswith("R") else rest.split(".", 1)[0].split(" ", 1)[0]
    if kind == "R3":
        subject = " ".join(rest.split(" ")[:2])
    return {"id": item_id, "kind": kind, "subject": subject, "verdict": verdict,
            "reason": reason}  # fmt: skip


def test_recompute_follows_the_verdict_rules():
    items = {i["id"]: i for i in [
        _item("T1 Note", "verified"),
        _item("T2 Note.secret", "unexercised"),
        _item("T3 Note.title max_len 9", "verified (corrected)"),
        _item("T1 Tag", "unexercised"),
        _item("T1 Link", "verified"),
        _item("T4 Link.kind", "refuted"),
        _item("R1 GET /notes", "verified"),
        _item("R2 GET /notes", "verified (corrected)"),
        _item("R3 GET /notes 5xx", "verified"),
        _item("R1 GET /tags", "unexercised", "unreachable-by-probe: needs the network"),
        _item("R1 POST /notes", "verified"),
        _item("R3 POST /notes 400", "unexercised"),
        _item("R3 POST /notes 5xx", "refuted"),
    ]}  # fmt: skip
    got = recompute(items)
    assert {k: v["verdict"] for k, v in got["tables"].items()} == {
        "Note": "verified", "Tag": "unverified", "Link": "unverified"}  # fmt: skip
    assert got["tables"]["Tag"]["reason"] == "unreached"
    assert {k: v["verdict"] for k, v in got["routes"].items()} == {
        "GET /notes": "verified",
        "GET /tags": "unverified",
        "POST /notes": "unverified",
    }


def test_a_report_disagreeing_with_its_items_is_caught():
    items = {i["id"]: i for i in [_item("T1 Note", "unexercised"), _item("R1 GET /n", "verified")]}
    rules = {
        "items": items,
        "tables": {"Note": {"verdict": "verified"}},
        "routes": {"GET /n": {"verdict": "verified"}},
        "summary": {"tables": {"verified": 1, "total": 1}, "routes": {"verified": 1, "total": 1}},
    }
    found = agreement(rules)
    assert any(f.startswith("tables:") for f in found)
    assert any(f.startswith("summary tables") for f in found)
    rules["tables"]["Note"]["verdict"] = "unverified"
    rules["summary"]["tables"]["verified"] = 0
    assert agreement(rules) == []
