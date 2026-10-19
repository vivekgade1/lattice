"""Round 5, Stage A: the global hash-chained ledger (H4), the run index and provenance read from it
(S10', S11-S14), and the held-out process of the runner (``--process-test`` on a scratch copy, a stand-in
app): refusals consume no run, one re-run after a crash only, a broken chain and a symlinked app refuse."""

from __future__ import annotations

# ruff: noqa: E501 -- data lines
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

DOGFOOD = Path(__file__).resolve().parents[2] / "dogfood"
sys.path.insert(0, str(DOGFOOD))

import grpc_probes5 as p5  # noqa: E402
import grpc_scorer5 as sc5  # noqa: E402
import grpc_truth5 as t5  # noqa: E402

T, T2 = "a" * 64, "b" * 64
KW = {"arm": "H5", "target_freeze_sha256": T, "runner_sha256": "1" * 64}
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)


def ledger(tmp_path: Path) -> t5.Ledger:
    return t5.Ledger(tmp_path / "ledger.jsonl")


def test_the_first_line_chains_to_the_genesis_and_each_line_to_the_bytes_of_the_one_before(
    tmp_path: Path,
) -> None:
    led = ledger(tmp_path)
    e1 = led.append(**KW, status="started", out="a", now=NOW)
    e2 = led.append(**KW, status="scored", out="a", now=NOW)
    assert e1["prev_sha256"] == t5.GENESIS and e1["seq"] == 1 and e2["seq"] == 2
    assert e2["prev_sha256"] == t5.sha256_of(led.raw_lines()[0])
    assert led.verify() == [] and led.tail_sha256() == t5.sha256_of(led.raw_lines()[1])
    assert e1["utc"] == "2026-10-18T12:00:00Z"


def test_an_empty_or_missing_ledger_verifies(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    assert led.verify() == [] and led.tail_sha256() == t5.GENESIS and led.run_index("H5", T) == 1


def test_a_tampered_line_breaks_the_chain_and_blocks_appends(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    for s in ("started", "crashed_before_metric", "started"):
        led.append(**KW, status=s, out="o")
    lines = led.raw_lines()
    led.path.write_bytes(b"\n".join([lines[0].replace(b"started", b"scored"), *lines[1:]]) + b"\n")
    assert any("prev_sha256 does not match" in p for p in led.verify())
    with pytest.raises(t5.TruthError, match="chain broken"):
        led.append(**KW, status="scored", out="o")
    led.path.write_bytes(b"\n".join(lines[1:]) + b"\n")  # a dropped first line
    assert led.verify()


def test_the_run_index_counts_started_lines_of_a_target_whatever_the_out_or_runner_hash(
    tmp_path: Path,
) -> None:
    led = ledger(tmp_path)
    led.append(**KW, status="started", out="out-a")
    led.append(**KW, status="crashed_before_metric", out="out-a")
    assert led.run_index("H5", T) == 2
    idx, prior, why = led.may_start("H5", T)
    assert (idx, why) == (2, "") and prior == {"crashed_before_metric": True, "runner": "1" * 64}
    led.append(
        **{**KW, "runner_sha256": "2" * 64}, status="started", out="out-b"
    )  # another out, another runner hash
    assert led.run_index("H5", T) == 3
    assert "third run" in led.may_start("H5", T)[2]
    assert led.run_index("H5", T2) == 1 and led.run_index("SG", T) == 1


def test_a_rerun_is_refused_after_a_scored_run_and_allowed_after_a_dangling_start(
    tmp_path: Path,
) -> None:
    led = ledger(tmp_path)
    led.append(**KW, status="started", out="o")
    assert led.may_start("H5", T)[1:] == (
        {"crashed_before_metric": True, "runner": "1" * 64},
        "",
    )  # the process died: no closing line
    led.append(**KW, status="scored", out="o")
    assert "first run scored" in led.may_start("H5", T)[2]


def test_a_refused_preflight_consumes_no_run(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    led.append(**KW, status="refused_preflight", out="o")
    led.append(**KW, status="refused_preflight", out="o")
    assert led.run_index("H5", T) == 1 and led.may_start("H5", T)[2] == ""


def test_the_real_ledger_path_is_resolved_from_the_runners_location() -> None:
    assert t5.real_ledger_path(DOGFOOD / "grpc_probes5.py") == DOGFOOD.parent / t5.LEDGER_REL
    assert t5.LEDGER_REL == "docs/results/data/2026-10-18-grpc-round5/ledger.jsonl"


# ---- S10' / S12 from the ledger's lines ------------------------------------------------------------------------


def _lines(tmp_path: Path, steps: list[tuple[str, str, str]]) -> list[str]:
    led = ledger(tmp_path)
    for status, target, runner in steps:
        led.append(
            arm="H5",
            status=status,
            target_freeze_sha256=target,
            runner_sha256=runner,
            out="o",
            now=NOW,
        )
    return [x.decode() for x in led.raw_lines()]


def test_the_scorer_reads_the_run_index_from_the_ledger_not_the_runners_claim(
    tmp_path: Path,
) -> None:
    lines = _lines(
        tmp_path, [("started", T, "1" * 64), ("scored", T, "1" * 64), ("started", T, "1" * 64)]
    )
    view = sc5.ledger_view(lines, "H5", T)
    assert view["run_index"] == 2 and view["prior"]["crashed_before_metric"] is False
    rec = {"runner": "1" * 64, "scorer": "3" * 64, "truth5": "4" * 64, "target": T}
    prov = sc5.provenance5(rec, {**rec, "run_index": 1}, view)  # the runner claims run 1
    assert (
        prov["label"] == "post_hoc" and "rerun_without_a_crash_before_any_metric" in prov["reasons"]
    )


def test_a_broken_chain_labels_post_hoc_with_its_reason(tmp_path: Path) -> None:
    lines = _lines(tmp_path, [("started", T, "1" * 64), ("scored", T, "1" * 64)])
    lines[0] = lines[0].replace("started", "scored")
    rec = {"runner": "1" * 64, "scorer": "3" * 64, "truth5": "4" * 64, "target": T}
    prov = sc5.provenance5(rec, rec, sc5.ledger_view(lines, "H5", T))
    assert prov == {
        "label": "post_hoc",
        "reasons": ["ledger_chain_broken"],
        "pass": False,
        "reason": "ledger_chain_broken",
    }


def test_s11_counts_the_codes_that_changed_spelling_and_refuses_an_unknown_one() -> None:
    ok = sc5.score_codes_spelling([["$.a", "NOT_FOUND"], ["$.b", "NotFound"], ["$.c", 5]])
    assert ok["normalised"] == 2 and ok["pass"] and ok["status"] == "scored"
    bad = sc5.score_codes_spelling([["$.a", "NOT_FOUND"], ["$.b", "NOT-FOUND"]])
    assert bad["status"] == "not_run" and not bad["pass"] and "$.b" in bad["refused"][0]


def test_s14_sqlt_is_reported_and_never_gated() -> None:
    row = sc5.score_sqlt(
        {"a/B": {"state": "implemented", "tables": ["users"]}},
        {"a/B": {"state": "implemented", "tables": ["items"]}},
    )
    assert row["recall"] == 0.0 and row["pass"] is True and row["gated"] is False


# ---- the runner: probes that need no model, Go or corpus ---------------------------------------------------------


def test_p5_code_p5_score_p5_arms_pass() -> None:
    for pr in (p5.p5_code(), p5.p5_score(), p5.p5_arms()):
        assert pr.status == "PASS", (pr.id, pr.observed)


def test_scorer_cases_include_round_4s_58_unchanged_and_20_new() -> None:
    new = json.loads((DOGFOOD / "grpc_scorer5_cases.json").read_text())["cases"]
    old = json.loads((DOGFOOD / "grpc_scorer4_cases.json").read_text())["cases"]
    assert new[: len(old)] == old and len(old) == 58 and len(new) - len(old) >= 20


def test_p5_ledger_five_of_five() -> None:
    pr = p5.p5_ledger({"scratch": Path()})
    assert pr.status == "PASS", pr.observed


def test_p5_proc_seven_of_seven() -> None:
    pr = p5.p5_proc()
    assert pr.status == "PASS", pr.observed


def test_the_runner_refuses_the_real_ledger_for_a_process_test(tmp_path: Path) -> None:
    import subprocess

    app = tmp_path / "ho"
    app.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            str(DOGFOOD / "grpc_probes5.py"),
            "--arm",
            "H5",
            "--process-test",
            "run",
            "--ledger",
            str(t5.real_ledger_path(DOGFOOD / "grpc_probes5.py")),
            "--app",
            str(app),
            "--out",
            str(tmp_path / "o"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2 and "never use the real ledger" in proc.stdout
    proc = subprocess.run(
        [
            sys.executable,
            str(DOGFOOD / "grpc_probes5.py"),
            "--arm",
            "H5",
            "--ledger",
            str(tmp_path / "x.jsonl"),
            "--app",
            str(app),
            "--out",
            str(tmp_path / "o"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2 and "only for --process-test" in proc.stdout
