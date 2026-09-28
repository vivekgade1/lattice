"""N1 on real model output: round-3 arm-L artefacts the checker refused for an ordinary
Python spelling (the last round's files, copied verbatim into tests/fixtures/normalise/).

The spelling refusal is gone in each. N1b (named column expressions, row values in
writes, `otherwise=None`) made two of them check clean; the others also have an error
normalisation does not address (an insert reading `col(...)` with no input table, an
optional parameter used in a computation, `accessible()` on a table the imported
profile gives no owner, ...). Those are pinned here, so a later change that fixes them
shows up as a failure to update.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from lattice.check import check_source
from lattice.diagnostics import Severity

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "normalise"

# fixture dir -> (program, the refusal at HEAD that is gone, normalisation kinds,
#                 remaining error codes)
CASES = {
    "haiku_t11_title_trim_rep0": (
        "items.lattice.py",
        ("L404", 47, 52),  # when(sent(title), then_=..., otherwise_=...)
        {"when_keywords"},
        ["L404", "L834"],
    ),
    "haiku_t22_copy_note_rep0": (
        "notes.lattice.py",
        ("L402", 10, 20),  # col("title") + " (copy)"
        {"concat"},
        ["L401", "L401"],
    ),
    "sonnet_t15_duplicate_item_rep2": (
        "items.lattice.py",
        ("L402", 65, 41),  # col("title") + " (copy)"
        {"concat"},
        ["L401", "L401"],
    ),
    "sonnet_t21_completed_at_rep1": (
        "routes.lattice.py",
        ("L404", 62, 22),  # when(status == "done", fn.now(), otherwise=None)
        {"when_keywords"},
        ["L404"] * 7,  # N1b: the L410 of otherwise=None is gone (a typed null)
    ),
    # ---- N1b
    "sonnet_t15_duplicate_item_rep0": (
        "items.lattice.py",
        ("L106", 41, 15),  # insert(Item, title=allowed.title + " (copy)", ...): a row value
        {"concat"},
        [],
    ),
    "haiku_t19_task_summary_rep2": (
        "routes.lattice.py",
        ("L410", 74, 80),  # agg.count(when(..., then=col("id"), otherwise=None))
        set(),
        [],
    ),
    "haiku_t21_completed_at_rep0": (
        "routes.lattice.py",
        ("L302", 18, 18),  # old_status = col("status"): a named column expression
        {"column_expr"},
        ["L404"] * 12,  # accessible()/owner= on Task (no owner column), sent() on `| None`
    ),
}


def _profile(directory: Path):
    module = types.ModuleType(f"_normalise_fixture_{directory.name}")
    source = (directory / "profile.py").read_text()
    exec(compile(source, str(directory / "profile.py"), "exec"), module.__dict__)  # noqa: S102
    return module.profile()


@pytest.mark.parametrize("name", sorted(CASES))
def test_round3_spelling_refusal_is_gone(name: str) -> None:
    program, (code, line, col), kinds, remaining = CASES[name]
    directory = FIXTURES / name
    r = check_source((directory / program).read_text(), _profile(directory))
    errors = [d for d in r.diagnostics if d.severity is Severity.ERROR]
    assert not [d for d in errors if (d.code, d.span.line, d.span.col) == (code, line, col)]
    assert {kind for kind, _ in r.normalisations} == kinds
    assert sorted(d.code for d in errors) == remaining, [d.render() for d in errors]


@pytest.mark.parametrize("name", sorted(CASES))
def test_round3_other_programs_are_unchanged(name: str) -> None:
    """The untouched programs of each fixture still check without normalisation."""
    program = CASES[name][0]
    directory = FIXTURES / name
    profile = _profile(directory)
    for path in sorted(directory.glob("*.lattice.py")):
        if path.name == program:
            continue
        r = check_source(path.read_text(), profile)
        assert r.ok, (path.name, [d.render() for d in r.diagnostics])
        assert not r.normalisations
