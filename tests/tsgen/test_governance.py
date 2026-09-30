"""R14 checks for the client target (spec 2026-09-29 ui-a §6)."""

from __future__ import annotations

from pathlib import Path

from lattice.targets import API_PARAM_RULES, CLIENT_TARGETS, PARAM_FEATURES, TS_REFUSAL

ROOT = Path(__file__).resolve().parents[2]
RULES_DOC = (ROOT / "docs" / "lattice-language-rules.md").read_text()


def test_rule_ids_are_contiguous() -> None:
    assert list(API_PARAM_RULES) == [f"P{i}" for i in range(1, len(API_PARAM_RULES) + 1)]


def test_ts_client_carries_every_param_feature() -> None:
    assert CLIENT_TARGETS["ts"].param_features == frozenset(PARAM_FEATURES)


def test_every_client_gate_has_a_decision_log_row() -> None:
    assert TS_REFUSAL in RULES_DOC
    for row in ("Int typed by declared range", "path `Str`"):
        assert row in RULES_DOC, row
