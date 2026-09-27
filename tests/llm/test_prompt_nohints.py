"""The no-hints condition removes pitfall advice from both arms, and nothing else."""

from lattice.llm.prompt import BASELINE_SYSTEM, baseline_system_prompt, lattice_system_prompt

LATTICE_ADVICE = ("must not silently repeat rows", "must say how to treat missing values",
                  "keeps rows with a missing key")
PANDAS_ADVICE = ("validate=", "dropna=False", "must not silently repeat", "skip missing values",
                 "must be read as text", "fillna(0.0)` only when told")


def test_default_prompts_keep_their_advice() -> None:
    assert baseline_system_prompt() == BASELINE_SYSTEM
    assert all(a in lattice_system_prompt() for a in LATTICE_ADVICE)
    assert all(a in BASELINE_SYSTEM for a in PANDAS_ADVICE)


def test_no_hints_prompts_drop_all_pitfall_advice() -> None:
    lattice, pandas = lattice_system_prompt(hints=False), baseline_system_prompt(hints=False)
    assert not [a for a in LATTICE_ADVICE if a in lattice]
    assert not [a for a in PANDAS_ADVICE if a in pandas]


def test_no_hints_keeps_language_reference_and_safety_rule() -> None:
    lattice, pandas = lattice_system_prompt(hints=False), baseline_system_prompt(hints=False)
    for kept in ("fanout", "agg.at_max", "Write only the output file", "## Examples"):
        assert kept in lattice, kept
    for kept in ("Write only `out.parquet`", "parse_dates", "## Examples"):
        assert kept in pandas, kept


def test_no_hints_prompts_are_deterministic() -> None:
    assert lattice_system_prompt(hints=False) == lattice_system_prompt(hints=False)
    assert baseline_system_prompt(hints=False) == baseline_system_prompt(hints=False)
