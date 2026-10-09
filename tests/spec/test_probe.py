"""lattice.spec.probe: the adversarial gap-finder (tests/spec/test_probe.py).

The ``app_dir`` fixture below is a tiny fake app: one file, ``app.py``, plus a
``check.py`` that stands in for a real spec check: it exits 2 if ``app.py`` fails to
compile (the patch "doesn't build"), 1 if ``app.py`` contains the literal ``BUG`` (the
check "noticed"), else 0 (the check passed -- a gap if the patch actually broke
something the check never looks at, like the LIMIT rule below)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.spec.probe import _select_files, _signal_rules, probe
from lattice.spec.rules import Rule

APP_PY = 'GREETING = "hello"\nLIMIT = 10\n'

CHECK_PY = '''\
import sys
from pathlib import Path

text = Path("app.py").read_text()
try:
    compile(text, "app.py", "exec")
except SyntaxError as exc:
    print(f"compile error: {exc}")
    sys.exit(2)

if "BUG" in text:
    print("found BUG marker")
    sys.exit(1)

print("ok")
sys.exit(0)
'''

RULE = Rule(id="R1", text="LIMIT must never exceed 10.", section="Limits", line=1)
CHECK_CMD = [sys.executable, "check.py"]


@pytest.fixture()
def app_dir(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text(APP_PY)
    (app / "check.py").write_text(CHECK_PY)
    return app


def _patch_response(path: str, body: str) -> str:
    """A model answer with one FILE block, in probe.py's expected shape."""
    return f"FILE: {path}\n```\n{body}\n```\n"


def _probe_one(app_dir: Path, rule: Rule, answer: str):
    gen = ScriptedGenerator([answer])
    [result] = probe(
        [rule],
        app_dir=app_dir,
        check_cmd=CHECK_CMD,
        files=["*.py"],
        generator=gen,
        model="scripted",
    )
    return result, gen


# ---- the four classifications -----------------------------------------------------------------


def test_gap_when_the_check_does_not_look_at_the_broken_behaviour(app_dir: Path) -> None:
    new_app = 'GREETING = "hello"\nLIMIT = 999\n'  # breaks the rule; check.py never looks at LIMIT
    result, _ = _probe_one(app_dir, RULE, _patch_response("app.py", new_app))

    assert result.status == "gap"
    assert result.rule_id == "R1"
    assert result.changed_files == ["app.py"]
    assert "LIMIT = 999" in result.patch_summary
    assert "-LIMIT = 10" in result.patch_summary  # a unified diff, not just the new text
    assert "exit=0" in result.check_tail


def test_caught_when_the_patch_trips_the_check(app_dir: Path) -> None:
    new_app = 'GREETING = "hello"\nLIMIT = 10  # BUG\n'
    result, _ = _probe_one(app_dir, RULE, _patch_response("app.py", new_app))

    assert result.status == "caught"
    assert "exit=1" in result.check_tail


def test_broken_when_the_patch_does_not_build(app_dir: Path) -> None:
    new_app = "def (:\n"  # invalid syntax: check.py's compile() fails before it can decide
    result, _ = _probe_one(app_dir, RULE, _patch_response("app.py", new_app))

    assert result.status == "broken"
    assert "exit=2" in result.check_tail


def test_refused_on_explicit_impossible(app_dir: Path) -> None:
    answer = "IMPOSSIBLE: LIMIT cannot be changed without the compiler noticing"
    result, _ = _probe_one(app_dir, RULE, answer)

    assert result.status == "refused"
    assert result.patch_summary.startswith("IMPOSSIBLE")
    assert result.changed_files == []


def test_refused_on_malformed_output(app_dir: Path) -> None:
    answer = "I think you should just trust me on this one."
    result, _ = _probe_one(app_dir, RULE, answer)

    assert result.status == "refused"
    assert "malformed" in result.patch_summary.lower()
    assert result.changed_files == []


def test_refused_on_a_path_escaping_the_app_dir(app_dir: Path) -> None:
    answer = _patch_response("../outside.py", "x = 1\n")
    result, _ = _probe_one(app_dir, RULE, answer)

    assert result.status == "refused"
    assert "escaped" in result.patch_summary.lower()


# ---- safety: app_dir and temp dirs ------------------------------------------------------------


def test_app_dir_is_never_modified(app_dir: Path) -> None:
    before_app = (app_dir / "app.py").read_text()
    before_check = (app_dir / "check.py").read_text()
    gen = ScriptedGenerator(
        [
            _patch_response("app.py", 'GREETING = "hello"\nLIMIT = 999\n'),
            _patch_response("app.py", 'GREETING = "hello"\nLIMIT = 10  # BUG\n'),
            "IMPOSSIBLE: nope",
        ]
    )
    rule2 = Rule(id="R2", text="Never say BUG.", section="Limits", line=2)
    rule3 = Rule(id="R3", text="Greeting stays hello.", section="Limits", line=3)

    probe(
        [RULE, rule2, rule3],
        app_dir=app_dir,
        check_cmd=CHECK_CMD,
        files=["*.py"],
        generator=gen,
        model="scripted",
    )

    assert (app_dir / "app.py").read_text() == before_app
    assert (app_dir / "check.py").read_text() == before_check


def test_temp_dirs_are_cleaned_up(app_dir: Path) -> None:
    before = set(Path(tempfile.gettempdir()).glob("lattice-probe-*"))
    gen = ScriptedGenerator([_patch_response("app.py", 'GREETING = "hello"\nLIMIT = 999\n')])

    probe(
        [RULE],
        app_dir=app_dir,
        check_cmd=CHECK_CMD,
        files=["*.py"],
        generator=gen,
        model="scripted",
    )

    after = set(Path(tempfile.gettempdir()).glob("lattice-probe-*"))
    assert after == before


def test_jobs_greater_than_one_still_cleans_up_and_keeps_rule_order(app_dir: Path) -> None:
    before = set(Path(tempfile.gettempdir()).glob("lattice-probe-*"))
    rule2 = Rule(id="R2", text="Never say BUG.", section="Limits", line=2)
    gen = ScriptedGenerator(
        [
            _patch_response("app.py", 'GREETING = "hello"\nLIMIT = 999\n'),  # gap
            _patch_response("app.py", 'GREETING = "hello"\nLIMIT = 10  # BUG\n'),  # caught
        ]
    )

    results = probe(
        [RULE, rule2],
        app_dir=app_dir,
        check_cmd=CHECK_CMD,
        files=["*.py"],
        generator=gen,
        model="scripted",
        jobs=2,
    )

    assert [r.rule_id for r in results] == ["R1", "R2"]
    assert {r.status for r in results} == {"gap", "caught"}
    assert set(Path(tempfile.gettempdir()).glob("lattice-probe-*")) == before


# ---- small helpers, tested directly -------------------------------------------------------------


def test_signal_rules_picks_rules_with_a_signal_word() -> None:
    rules = [
        Rule(id="R1", text="The job runs at most once per minute.", section="", line=1),
        Rule(id="R2", text="A friendly welcome message is shown.", section="", line=2),
        Rule(id="R3", text="Each email is unique across accounts.", section="", line=3),
    ]
    assert [r.id for r in _signal_rules(rules)] == ["R1", "R3"]


def test_select_files_trims_largest_first_when_over_budget(tmp_path: Path) -> None:
    import lattice.spec.probe as probe_mod

    big = tmp_path / "big.py"
    small = tmp_path / "small.py"
    big.write_text("x" * 100)
    small.write_text("y" * 10)

    old_cap = probe_mod.MAX_PROMPT_CHARS
    probe_mod.MAX_PROMPT_CHARS = 50
    try:
        selected, dropped = _select_files(tmp_path, ["*.py"])
    finally:
        probe_mod.MAX_PROMPT_CHARS = old_cap

    assert dropped == ["big.py"]
    assert selected == {"small.py": "y" * 10}
