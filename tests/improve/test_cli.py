"""``python -m lattice.improve`` / ``lattice improve``: exit codes (0 report, 2 cannot read),
import-plan, the ledger, and the I-4 refusal in one CLI run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice.cli import main as lattice_main
from lattice.improve.__main__ import main
from lattice.improve.events import EventLog
from tests.improve.test_analyze import i4_checker, i4_lint_noise

DATA = Path(__file__).resolve().parents[2] / "docs/results/data/streak-plans-2026-10-11"


def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "studio.toml").write_text('[state]\ndir = "state"\n')
    return p


def test_report_on_an_empty_log_exits_zero(tmp_path: Path, capsys) -> None:
    p = project(tmp_path)
    assert main(["report", "--project", str(p)]) == 0
    out = capsys.readouterr().out
    assert "Method:" in out and "Blind spots:" in out and "No proposals" in out


def test_cannot_read_exits_two(tmp_path: Path, capsys) -> None:
    assert main(["report", "--project", str(tmp_path / "nope")]) == 2
    p = project(tmp_path)
    (p / "state" / "events.jsonl").mkdir(parents=True)  # a directory: not readable as a log
    assert main(["report", "--project", str(p)]) == 2
    assert "cannot read" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["import-plan", str(bad), "--project", str(p)]) == 2
    bad.write_text(json.dumps({"hello": 1}))
    assert main(["import-plan", str(bad), "--project", str(p)]) == 2
    assert main(["ledger", "--project", str(p), "--signal", "x"]) == 2  # needs --status


def test_import_plan_is_idempotent_and_report_json(tmp_path: Path, capsys) -> None:
    p = project(tmp_path)
    rec = str(DATA / "p2-final.json")
    assert main(["import-plan", rec, "--project", str(p), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["plan"] == "p2" and first["written"] == first["events"] > 20
    assert main(["import-plan", rec, "--project", str(p), "--json"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["written"] == 0 and again["skipped"] == first["events"]
    assert main(["report", "--project", str(p), "--json", "--top", "3"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["events"] == first["events"] and len(rep["proposals"]) == 3
    assert set(rep) >= {"proposals", "events", "method", "blind_spots", "closed"}


def test_ledger_closes_an_item(tmp_path: Path, capsys) -> None:
    p = project(tmp_path)
    assert main(["import-plan", str(DATA / "p3.json"), "--project", str(p)]) == 0
    assert (
        main(
            [
                "ledger",
                "--project",
                str(p),
                "--signal",
                "cause:stopped_by_owner",
                "--status",
                "closed",
                "--id",
                "I-05",
                "--commit",
                "e93faea",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["ledger", "--project", str(p)]) == 0
    assert "closed  cause:stopped_by_owner  I-05 e93faea" in capsys.readouterr().out
    assert main(["report", "--project", str(p), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert "cause:stopped_by_owner" not in [x["signal_key"] for x in rep["proposals"]]
    assert rep["closed"][0]["signal_key"] == "cause:stopped_by_owner"


@pytest.mark.parametrize(
    ("events", "status"), [(i4_lint_noise, "open"), (i4_checker, "needs_owner")]
)
def test_i4_refusal_in_the_cli(tmp_path: Path, capsys, events, status) -> None:
    p = project(tmp_path)
    log = EventLog(p / "state" / "events.jsonl", "proj")
    log.extend(events())
    assert lattice_main(["improve", "report", "--project", str(p), "--json"]) == 0
    top = json.loads(capsys.readouterr().out)["proposals"][0]
    assert top["status"] == status
    assert lattice_main(["improve", "report", "--project", str(p)]) == 0
    assert f"status {status}" in capsys.readouterr().out
