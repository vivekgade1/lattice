"""Layer A on a Go app through ``lattice.spec.check`` and the CLI: gocheck reads the Gin +
GORM module root (``go_src``, ``--go-src``) with a policy built from the spec. Skipped
when ``go`` is not on PATH."""

from __future__ import annotations

import json
import shutil

import pytest

from lattice.gocheck import GO_CODES, GoPolicy
from lattice.spec import check
from lattice.spec.__main__ import main
from lattice.spec.process import live_processes, process_runtime
from tests.gocheck import tasks_spec as ts

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

GO = process_runtime(framework="go")


def test_the_policy_comes_from_the_spec():
    pol = GoPolicy.from_spec(ts.SPEC)
    assert pol.tables == {"User": "users", "Task": "tasks", "Comment": "comments"}
    assert pol.owners == {"Task": "owner_id"}
    assert pol.owner_via == {"Comment": ("task_id", "Task")}
    assert pol.private == {"User": ("password_hash",)}
    col, edges = pol.machines["Task"]
    assert col == "state" and ("doing", "done") in edges and ("done", "todo") not in edges
    assert pol.users_table == "User"


def test_layer_a_through_check():
    report = check(ts.SPEC, ts.make_app, ts.build_db, layers="A", runtime=GO, go_src=ts.APP_DIR)
    found = sorted((f.code, f.file, f.line) for f in report.findings)
    assert found == [
        ("GO802", "internal/handlers/handlers.go", 121),
        ("GO807", "internal/handlers/handlers.go", 106),
        ("GO808", "internal/handlers/handlers.go", 88),
    ], report.render()
    assert all(f.layer == "A" and f.rule == GO_CODES[f.code] for f in report.findings)
    partial = next(f for f in report.findings if f.code == "GO807")
    assert partial.endpoint == "POST /tasks/:id/comments"
    assert report.verdict == "fail"
    assert any(n.startswith("layer A (gocheck): not available for Go: GO804") for n in report.notes)
    text = report.render()
    assert "GO807" in text and "fix: check everything that can refuse" in text
    assert live_processes() == []


def test_app_dir_is_the_go_root_for_a_go_runtime():
    report = check(ts.SPEC, ts.make_app, ts.build_db, layers="A", runtime=GO, app_dir=ts.APP_DIR)
    assert {f.code for f in report.findings} == {"GO802", "GO807", "GO808"}


def test_go_src_alone_selects_gocheck():
    report = check(ts.SPEC, ts.make_app, ts.build_db, layers="A", go_src=ts.APP_DIR)
    assert {f.code for f in report.findings} == {"GO802", "GO807", "GO808"}


def test_a_go_app_without_source_is_skipped_with_a_note():
    report = check(ts.SPEC, ts.make_app, ts.build_db, layers="A", runtime=GO)
    assert report.findings == []
    assert "layer A skipped: no Go source (go_src, or --go-src)" in report.notes


def test_the_cli(capsys):
    code = main(["check", "--spec", "tests.gocheck.tasks_spec:SPEC",
                 "--app", "tests.gocheck.tasks_spec:make_app",
                 "--db", "tests.gocheck.tasks_spec:build_db",
                 "--runtime", "go", "--go-src", str(ts.APP_DIR),
                 "--layers", "A", "--json"])  # fmt: skip
    out = capsys.readouterr()
    assert code == 1, out.err
    data = json.loads(out.out)
    assert sorted(f["code"] for f in data["findings"]) == ["GO802", "GO807", "GO808"]
