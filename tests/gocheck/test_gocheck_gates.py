"""gocheck's gates and wiring that need no Go toolchain: the PY rules with no Go
counterpart are explicit and logged (docs/lattice-language-rules.md §6, R11), and a
missing toolchain skips layer A with a note instead of failing the check."""

from __future__ import annotations

from pathlib import Path

import pytest

import lattice.gocheck as gocheck
from lattice.gocheck import GO_CODES, GO_GATED, GoPolicy, GoToolchainMissing
from lattice.pycheck import CODES
from lattice.spec import check
from lattice.spec.process import process_runtime
from tests.gocheck import tasks_spec as ts

RULES_DOC = Path(__file__).resolve().parents[2] / "docs" / "lattice-language-rules.md"


def test_every_py_rule_is_carried_over_or_gated():
    go = {c.replace("PY", "GO", 1) for c in CODES}
    assert go == set(GO_CODES) | set(GO_GATED)
    assert not set(GO_CODES) & set(GO_GATED)
    assert all(why for why in GO_GATED.values())


def test_the_gates_are_in_the_decision_log():
    text = RULES_DOC.read_text()
    log = text[text.index("## 6. Decision log") : text.index("## 7.")]
    for code in GO_GATED:
        assert code in log, f"{code} has no decision-log entry"


def test_the_gated_rules_are_named_in_a_note():
    note = gocheck.gated_note()
    assert all(code in note for code in GO_GATED)


def test_no_toolchain_skips_layer_a_with_a_note(monkeypatch: pytest.MonkeyPatch):
    def missing() -> Path:
        raise GoToolchainMissing("gocheck needs the Go toolchain: `go` is not on PATH")

    monkeypatch.setattr(gocheck, "build_gocheck", missing)
    report = check(ts.SPEC, ts.make_app, ts.build_db, layers="A",
                   runtime=process_runtime(framework="go"), go_src=ts.APP_DIR)  # fmt: skip
    assert report.findings == []
    assert any(
        n.startswith("layer A skipped: gocheck needs the Go toolchain") for n in report.notes
    )


def test_python_apps_keep_pycheck():
    """A Python runtime never reaches gocheck (its layer A is pycheck's)."""
    from lattice.spec.check import PYTHON_FRAMEWORKS

    assert "go" not in PYTHON_FRAMEWORKS


def test_route_scopes_come_from_contracts_and_programs():
    from dataclasses import replace

    from lattice.spec.model import Contract

    programs = {
        "tasks": (
            '@endpoint("GET", "/tasks/{task_id}", auth=Member)\n'
            "def get_task(task_id: Int) -> Task:\n"
            "    return owned(Task).get(task_id)\n\n"
            '@endpoint("GET", "/feed", auth=Member)\n'
            "def feed() -> Page[Task]:\n"
            "    return page(Task.all())\n\n"
            '@endpoint("DELETE", "/admin/tasks/{task_id}", auth=Admin)\n'
            "def purge(task_id: Int) -> None:\n"
            "    owned(Task).get(task_id).delete()\n"
        )
    }
    contracts = (
        Contract("POST", "/worker/claim", "Member", table="Task", owner_scope="any"),
        Contract("GET", "/admin/users", "Admin", table="User"),
        Contract("GET", "/mine", "Member", table="Task", owner_scope="owner"),
    )
    pol = GoPolicy.from_spec(replace(ts.SPEC, programs=programs, contracts=contracts))
    assert set(pol.admin_routes) == {("GET", "/admin/users"), ("DELETE", "/admin/tasks/{task_id}")}
    assert set(pol.open_routes) == {("POST", "/worker/claim"), ("GET", "/feed")}


def test_old_builds_are_pruned(tmp_path: Path):
    import os

    for i in range(6):
        d = tmp_path / f"b{i}"
        d.mkdir()
        (d / "gocheck").write_text("")
        os.utime(d, (i, i))
    gocheck.prune_cache(tmp_path, keep=2)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b4", "b5"]
