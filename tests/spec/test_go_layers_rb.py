"""Layers R and B on the compiled Go app of ``tests/spec/goapp`` (Spike J, node J1, arm S1),
through ``tests/spec/go_harness_rb``. The whole file is skipped when ``go`` is missing or
the binary cannot be built. ``go_harness`` and the app are unchanged; the harness adds a
role-aware seed, contracts, programs and the explicit ``login`` R and B need."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import Report, check
from lattice.spec.process import live_processes
from tests.spec import go_harness_rb as rb

GOAPP = Path(__file__).parent / "goapp"


@pytest.fixture(scope="module")
def binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    go = shutil.which("go")
    if go is None:
        pytest.skip("go is not installed")
    out = tmp_path_factory.mktemp("goapp-rb") / "goapp"
    try:
        done = subprocess.run([go, "build", "-o", str(out), "."], cwd=GOAPP, capture_output=True,
                              text=True, timeout=600, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as err:
        pytest.skip(f"cannot build the Go app: {err}")
    if done.returncode != 0 or not out.exists():
        pytest.skip(f"cannot build the Go app (module download?): {done.stderr[-500:]}")
    return out


@pytest.fixture
def app(binary: Path, monkeypatch: pytest.MonkeyPatch):
    """``app(bug=...)``: a factory of the Go app's variant."""
    monkeypatch.setenv(rb.BINARY_ENV, str(binary))

    def variant(bug: str = "") -> Any:
        monkeypatch.setenv("GOAPP_BUG", bug)
        return rb.make_app

    yield variant
    assert live_processes() == [], "a process outlived its check"


def run(factory: Any, layers: str, **kw: Any) -> Report:
    kw.setdefault("runtime", rb.RUNTIME)
    kw.setdefault("login", rb.LOGIN)
    kw.setdefault("rules", rb.RULES)
    return check(rb.SPEC, factory, rb.build_db, layers=layers, **kw)


def test_the_spec_is_valid_and_the_seed_has_member_callers(tmp_path):
    from lattice.spec import validate

    assert validate(rb.SPEC) == []
    for variant in rb.VARIANTS:
        import sqlite3

        db = rb.build_db(variant, tmp_path / f"{variant}.db")
        rows = sqlite3.connect(db).execute("SELECT name, role FROM users ORDER BY id").fetchall()
        assert rows == [("ann", "Member"), ("ben", "Member")]


def test_layer_r_verifies_the_safe_app_and_refutes_nothing(app):
    report = run(app(), "R")
    s = report.rules.summary["items"]
    assert s["refuted"] == 0 and s["unclear"] == 0, report.render()
    total = sum(s.values())
    assert s["verified"] / total >= 0.5
    for v in report.rules.items.values():
        if v.verdict == "unexercised":
            assert v.reason  # every unexercised item says why
    assert report.rules.summary["routes"] == {"verified": 4, "total": 4}


def test_layer_r_needs_an_explicit_login_finding_f_login(app):
    """premise 3: without ``login=`` R ignores ``runtime.auth``: the probes run without
    callers and almost nothing is exercised (the fix is one line in check.py, not made here)."""
    report = run(app(), "R", login=None)
    assert any("no login given" in n for n in report.notes)
    s = report.rules.summary["items"]
    assert s["verified"] < 5 and s["unexercised"] > 15


def test_layer_r_reads_answers_not_effects(app):
    # nocancel lies in the answer and writes nothing: R's items are about the answers' shape
    # (auth, status, owner), which still hold; B reads the effect (the next test file's job)
    report = run(app(bug="nocancel"), "R")
    assert report.rules.summary["items"]["refuted"] == 0


def test_layer_b_is_quiet_on_the_safe_app(app):
    report = run(app(), "B")
    assert report.findings == [] and len(report.cases) >= 6, report.render()


def test_layer_b_default_rules_flag_the_go_422_body_finding_f_b422(app):
    from lattice.importer.verify import ValidationEnvelope

    report = run(app(), "B", rules=(ValidationEnvelope(),))  # the check's default rule
    found = [f for f in report.findings if f.layer == "B"]
    assert found and all("malformed" in (f.case or "") for f in found)


@pytest.mark.parametrize(
    ("bug", "endpoint"),
    [("nocheck", "POST /configs/{cid}/runs"), ("nocancel", "POST /runs/{rid}/cancel")],
)
def test_layer_b_kills_the_existing_variants(app, bug, endpoint):
    report = run(app(bug=bug), "B")
    assert report.findings, report.render()
    assert {f.endpoint for f in report.findings} == {endpoint}


def test_layer_b_and_r_are_repeatable(app):
    first = run(app(), "RB")
    second = run(app(), "RB")
    verdicts = {i: v.verdict for i, v in first.rules.items.items()}
    assert verdicts == {i: v.verdict for i, v in second.rules.items.items()}
    assert [f.case for f in first.findings] == [f.case for f in second.findings]


def test_the_cli_takes_the_login(app, binary, monkeypatch, capsys):
    from lattice.spec.__main__ import main

    monkeypatch.setenv(rb.BINARY_ENV, str(binary))
    code = main([
        "check", "--spec", "tests.spec.go_harness_rb:SPEC", "--app",
        "tests.spec.go_harness_rb:make_app", "--db", "tests.spec.go_harness_rb:build_db",
        "--runtime", "tests.spec.go_harness_rb:RUNTIME", "--login",
        "tests.spec.go_harness_rb:LOGIN", "--layers", "R",
    ])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "rule items" in out
