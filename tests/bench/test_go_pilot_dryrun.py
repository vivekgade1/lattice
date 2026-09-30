"""The offline dry run of the 5b Go cost pilot (Task 7): the whole pipeline -- both arms,
scoring, records, the harness report and the pre-registered analysis -- end to end, on
canned model responses (``bench/offline/go_pilot``, ``bench.cli``'s ``--offline`` layout:
``<arm>/<task_id>.txt``, rounds separated by a ``=====`` line).

One task per family, each answer's designed classification:

| task | family     | arm G (hand-written Go)            | arm B (reference Lattice)        |
|------|------------|------------------------------------|----------------------------------|
| c01  | control    | correct, 1 round                   | correct, 1 round                 |
| o01  | ownership  | SILENT (no ownership filter)       | correct, 1 round                 |
| v01  | validation | build error, repaired: correct, 2  | correct, 1 round                 |
| e02  | errors     | correct, 1 round                   | L401 checker error, repaired: 2  |
| f01  | efficiency | correct, 1 round                   | correct, 1 round                 |
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bench import cli
from bench.analysis import go_pilot
from bench.api_datasets import build_db
from bench.api_tasks import API_TASKS
from bench.runners import run_api_go
from lattice.llm.client import ScriptedGenerator

REPO = Path(__file__).resolve().parents[2]
OFFLINE = REPO / "bench" / "offline" / "go_pilot"
TASKS = ("c01", "o01", "v01", "e02", "f01")
MODEL = "claude-sonnet-5"

# (arm, task) -> (outcome class, rounds, per-round diagnostics)
DESIGNED = {
    ("G", "c01"): ("correct", 1, [[]]),
    ("G", "o01"): ("silent", 1, [[]]),
    ("G", "v01"): ("correct", 2, [["build_error"], []]),
    ("G", "e02"): ("correct", 1, [[]]),
    ("G", "f01"): ("correct", 1, [[]]),
    ("B", "c01"): ("correct", 1, [[]]),
    ("B", "o01"): ("correct", 1, [[]]),
    ("B", "v01"): ("correct", 1, [[]]),
    ("B", "e02"): ("correct", 2, [["L401"], []]),
    ("B", "f01"): ("correct", 1, [[]]),
}
FAMILIES = {
    "c01": "control",
    "o01": "ownership",
    "v01": "validation",
    "e02": "errors",
    "f01": "efficiency",
}

pytestmark = pytest.mark.go


def _class(record: dict) -> str:
    if record["correct"]:
        return "correct"
    return {"wrong_result": "silent", "harness_error": "harness"}.get(record["failure"], "loud")


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("go_pilot_dry") / "out"
    status = cli.main(
        [
            "run",
            "--suite", "api",
            "--arms", "G,B",
            "--lattice-backend", "go",
            "--offline", str(OFFLINE),
            "--models", MODEL,
            "--reps", "1",
            "--tasks", ",".join(TASKS),
            "--out", str(out),
        ]
    )  # fmt: skip
    assert status == 0
    return out


def _records(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / "outcomes.jsonl").read_text().splitlines()]


def test_every_answer_is_classified_as_designed(dry_run: Path) -> None:
    records = _records(dry_run)
    got = {
        (r["arm"], r["task_id"]): (_class(r), r["rounds"], r["diagnostics_per_round"])
        for r in records
    }
    assert got == DESIGNED
    assert len(records) == len(DESIGNED)  # one record per (arm, task): no duplicates
    assert all(r["family"] == FAMILIES[r["task_id"]] for r in records)
    silent = next(r for r in records if (r["arm"], r["task_id"]) == ("G", "o01"))
    assert set(silent["failed_cases"]) == {"alice_reads_bobs", "bob_reads_alices"}


def test_no_harness_errors_and_every_record_is_complete(dry_run: Path) -> None:
    for r in _records(dry_run):
        assert r["failure"] != "harness_error", r["detail"]
        assert r["requested_model"] == MODEL and r["model"] == MODEL and r["rep"] == 0
        assert r["condition"] == ""
        # cost fields are present; scripted offline calls cost nothing and name no basis
        # (a live CLI call reports cost_basis "cli_list_estimate")
        assert r["cost_usd"] == 0.0 and r["cost_basis"] == ""
        assert r["program_lines"] > 0 and r["program_ast_nodes"] > 0 and r["program_chars"] > 0
        if r["arm"] == "B":
            assert r["detail"].startswith("backend=go")


def test_go_programs_are_larger_than_lattice(dry_run: Path) -> None:
    by = {(r["arm"], r["task_id"]): r for r in _records(dry_run)}
    for task in TASKS:
        assert by[("G", task)]["program_ast_nodes"] > by[("B", task)]["program_ast_nodes"]


def test_the_analysis_and_the_harness_report_render(dry_run: Path) -> None:
    assert go_pilot.main([str(dry_run)]) == 0
    report = (dry_run / "go_pilot_report.md").read_text()
    assert f"| {MODEL} | B | 5 | 5/5 " in report
    assert f"| {MODEL} | G | 5 | 4/5 " in report
    # 5 tasks x 1 rep is not the pre-registered 40 x 3: the verdict is refused, not faked
    assert "Verdict not computable" in report and "expected 40 tasks x 3 reps" in report
    assert "Stage-1 verdict" not in report
    harness = (dry_run / "report.md").read_text()
    assert "| G | all | 5 | 80.00% |" in harness and "| B | all | 5 | 100.00% |" in harness


def _o01_with_ownership_filter() -> str:
    text = (OFFLINE / "G" / "o01.txt").read_text()
    fixed = text.replace(
        '_, ok := k.Authenticate(w, r, "Customer")', 'user, ok := k.Authenticate(w, r, "Customer")'
    ).replace(
        '"SELECT id, status, created_at, total FROM orders WHERE id = ?", id,',
        '"SELECT id, status, created_at, total FROM orders"+\n'
        "\t\t\t\t\" WHERE id = ? AND (customer_id = ? OR ? = 'Admin')\",\n"
        "\t\t\tid, user.ID, user.Role,",
    )
    assert fixed.count("customer_id = ?") == 1
    return fixed


def test_the_silent_g_answer_differs_from_a_correct_one_only_by_the_ownership_filter(
    tmp_path: Path,
) -> None:
    pristine = tmp_path / "pristine.db"
    build_db(pristine, seed=21)
    task = next(t for t in API_TASKS if t.id == "o01")
    outcome = run_api_go(
        task,
        ScriptedGenerator([_o01_with_ownership_filter()]),
        tmp_path / "w",
        db_builder=lambda path: shutil.copyfile(pristine, path),
    )
    assert outcome.correct, outcome.detail
