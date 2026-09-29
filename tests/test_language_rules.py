"""Governance (docs/lattice-language-rules.md, R14): CI must fail when a `p8_target`
gate or a language-wide v1.1 rule has no decision-log entry, and Task 8's hook for
"is a semantics case missing on some target" must not be a placeholder."""

from __future__ import annotations

import re
from pathlib import Path

import lattice.check.p8_target as p8_target
from tests.semantics_v11.cases import CASES, semantics_cases_missing_on

ROOT = Path(__file__).resolve().parents[1]
RULES_DOC = ROOT / "docs" / "lattice-language-rules.md"

_CODE = re.compile(r"\b[LR]\d{3}\b")


def _decision_log_codes() -> set[str]:
    """Every diagnostic/rule code mentioned in the §6 decision-log table."""
    text = RULES_DOC.read_text()
    start = text.index("## 6. Decision log")
    end = text.index("## 7.", start)
    table = text[start:end]
    rows = [line for line in table.splitlines() if line.startswith("|")]
    return {code for row in rows for code in _CODE.findall(row)}


def _p8_target_codes() -> set[str]:
    """Every diagnostic code `p8_target.run` can emit (its `ctx.error("Lxxx", ...)` call
    sites): a real AST/source scan, not a hand-copied list, so a new gate is caught."""
    source = Path(p8_target.__file__).read_text()
    return set(re.findall(r'ctx\.error\(\s*"([LR]\d{3})"', source))


def test_p8_target_codes_are_in_the_decision_log():
    p8_codes = _p8_target_codes()
    assert p8_codes, "expected p8_target.py to emit at least one diagnostic code"
    log_codes = _decision_log_codes()
    missing = p8_codes - log_codes
    assert not missing, f"p8_target codes missing a decision-log entry: {sorted(missing)}"


def test_v11_language_wide_rules_are_in_the_decision_log():
    # L913 (xs[a:b] += ys rejected), L926 (RE2 subset / ASCII \d \w \s \b), R623 (Int
    # overflow): the language-wide v1.1 rules folded into this task.
    log_codes = _decision_log_codes()
    missing = {"L913", "L926", "R623"} - log_codes
    assert not missing, f"v1.1 rule codes missing a decision-log entry: {sorted(missing)}"


def test_semantics_cases_missing_on_python_is_empty():
    assert semantics_cases_missing_on("python") == []


def test_semantics_cases_missing_on_go_is_not_a_placeholder():
    # Tasks 8-9 run the core, string and regex cases on Go; the rest are missing there
    # until Task 10.
    # The hook must reflect CASES (a subset of them, some run and some not), not a stub;
    # tests/semantics_v11/test_go.py checks each missing case is an L892 gate.
    missing = semantics_cases_missing_on("go")
    names = {c.name for c in CASES}
    assert missing != []
    assert set(missing) < names
    assert len(names - set(missing)) >= 212
