"""The report schema: JSON shape, per-class tallies, and the human text."""

from __future__ import annotations

import json

from lattice.security import run as runmod
from lattice.security.plan import generate
from lattice.security.report import to_json, to_text


def test_json_schema_shape(make_target, attack_spec):
    rep = runmod.run(make_target("authz"), generate(attack_spec), scope="system")
    d = json.loads(to_json(rep))
    assert d["schema"] == "lattice.security/report/1"
    for key in (
        "target",
        "host",
        "scope",
        "classes",
        "attacks_planned",
        "classes_report",
        "confirmed_findings",
        "unreproduced_claims",
        "cannot_see",
        "wall_seconds",
    ):
        assert key in d, key
    for cr in d["classes_report"]:
        assert set(cr) >= {"class", "severity", "run", "refuted", "findings", "not_refuted"}
    assert any(f["class"] == "authz" for f in d["confirmed_findings"])


def test_not_refuted_is_findings_plus_unreproduced(make_target, attack_spec):
    rep = runmod.run(make_target("auth"), generate(attack_spec), scope="system")
    for cr in rep.classreports:
        assert cr.not_refuted == cr.findings + cr.unreproduced


def test_text_report_carries_banner_and_blindspots(make_target, attack_spec):
    rep = runmod.run(make_target("ok"), generate(attack_spec), scope="system")
    text = to_text(rep)
    assert "for the owner's own systems only" in text.lower()
    assert "cannot see" in text.lower()
    assert "not refuted" in text.lower()
