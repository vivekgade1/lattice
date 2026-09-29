"""Tests for the 5b stage-1 Go cost pilot's pre-registered analysis.

Spec: docs/superpowers/specs/2026-09-29-lattice-go-pilot.md, "Tests and decision"
(binding). Interfaces per the task-6 brief.
"""

from __future__ import annotations

import json

import pytest

from bench.analysis.go_pilot import (
    SONNET,
    cost_per_correct,
    load,
    newcombe_wilson_diff,
    ratio_bootstrap,
    render,
    verdict,
)

# ---------------------------------------------------------------------------
# Synthetic record builders
# ---------------------------------------------------------------------------


def _records(rows):
    """rows: iterable of (task_id, arm, model, rep, correct, cost_usd) tuples."""
    out = []
    for task_id, arm, model, rep, correct, cost_usd in rows:
        out.append(
            {
                "task_id": task_id,
                "arm": arm,
                "model": model,
                "rep": rep,
                "correct": correct,
                "cost_usd": cost_usd,
                "cost_basis": "cli_list_estimate",
            }
        )
    return out


def _write_jsonl(path, rows):
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _flat(rows_dicts):
    """Turn dicts (as _records produces) into Record objects via load(), round-tripping
    through a real JSONL file so the loader is exercised by every test."""
    return rows_dicts


# 40 tasks x 3 reps = 120 runs/arm/model, matching the real pilot's design
# (spec: 40 API tasks, 3 reps). At this sample size two arms tied at 100%
# correct clear the P2 non-inferiority bound; a smaller synthetic n would not
# (Wilson's asymmetry near p=1 makes even a true tie's interval dip below
# -0.05 lower bound at n=60/arm).
N_TASKS = 40
REPS = 3


def _uniform_dataset(
    *,
    cost_b: float,
    cost_g: float,
    b_correct_tasks: int = N_TASKS,
    g_correct_tasks: int = N_TASKS,
    model: str = SONNET,
):
    """N_TASKS tasks, REPS reps per arm. The first b_correct_tasks (resp.
    g_correct_tasks) tasks are correct in every rep for that arm; the rest are
    incorrect in every rep. Cost per run is fixed per arm (no variance)."""
    rows = []
    for i in range(N_TASKS):
        task_id = f"t{i:02d}"
        b_ok = i < b_correct_tasks
        g_ok = i < g_correct_tasks
        for rep in range(REPS):
            rows.append((task_id, "B", model, rep, b_ok, cost_b))
            rows.append((task_id, "G", model, rep, g_ok, cost_g))
    return _records(rows)


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


def test_load_reads_jsonl_records(tmp_path):
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    path = tmp_path / "outcomes.jsonl"
    _write_jsonl(path, rows)
    records = load(path)
    assert len(records) == len(rows)
    assert records[0].task_id == "t00"
    assert records[0].arm == "B"
    assert records[0].model == SONNET
    assert isinstance(records[0].correct, bool)
    assert records[0].cost_basis == "cli_list_estimate"


def test_load_skips_blank_lines(tmp_path):
    path = tmp_path / "outcomes.jsonl"
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)[:2]
    with path.open("w") as f:
        f.write("\n")
        for row in rows:
            f.write(json.dumps(row) + "\n")
        f.write("   \n")
    records = load(path)
    assert len(records) == 2


@pytest.mark.parametrize(
    "missing", ["task_id", "arm", "model", "rep", "correct", "cost_usd", "cost_basis"]
)
def test_load_raises_clearly_on_missing_field(tmp_path, missing):
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)[:1]
    row = dict(rows[0])
    del row[missing]
    path = tmp_path / "outcomes.jsonl"
    with path.open("w") as f:
        f.write(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match=missing):
        load(path)


# ---------------------------------------------------------------------------
# cost_per_correct()
# ---------------------------------------------------------------------------


def test_cost_per_correct_basic():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    # every run correct: cost per correct task == cost per run.
    assert cost_per_correct(records, "B", SONNET) == pytest.approx(0.05)
    assert cost_per_correct(records, "G", SONNET) == pytest.approx(0.10)


def test_cost_per_correct_raises_when_no_correct_runs():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10, b_correct_tasks=0)
    records = load(_dump(rows))
    with pytest.raises(ValueError):
        cost_per_correct(records, "B", SONNET)


def _dump(rows, tmp_dir=None):
    import tempfile
    from pathlib import Path

    d = Path(tmp_dir) if tmp_dir else Path(tempfile.mkdtemp())
    path = d / "outcomes.jsonl"
    _write_jsonl(path, rows)
    return path


# ---------------------------------------------------------------------------
# newcombe_wilson_diff() -- Newcombe (1998) worked example, method 10.
# ---------------------------------------------------------------------------


def test_newcombe_wilson_diff_published_worked_example():
    # Newcombe (1998), Statistics in Medicine 17:873-890, Table II / method 10
    # worked example: r1/n1 = 56/70, r2/n2 = 48/80 -> 95% CI approx (0.0524, 0.3339).
    lo, hi = newcombe_wilson_diff(56, 70, 48, 80)
    assert lo == pytest.approx(0.0524, abs=1e-3)
    assert hi == pytest.approx(0.3339, abs=1e-3)


def test_newcombe_wilson_diff_zero_when_identical():
    lo, hi = newcombe_wilson_diff(30, 60, 30, 60)
    assert lo < 0 < hi


# ---------------------------------------------------------------------------
# ratio_bootstrap()
# ---------------------------------------------------------------------------


def test_ratio_bootstrap_deterministic_with_seed():
    rows = _uniform_dataset(cost_b=0.045, cost_g=0.10, b_correct_tasks=18)
    records = load(_dump(rows))
    r1 = ratio_bootstrap(records, SONNET, n=500, seed=0)
    r2 = ratio_bootstrap(records, SONNET, n=500, seed=0)
    assert r1 == r2


def test_ratio_bootstrap_point_estimate_matches_cost_per_correct_ratio():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    R, lo, hi = ratio_bootstrap(records, SONNET, n=200, seed=0)
    expected = cost_per_correct(records, "B", SONNET) / cost_per_correct(records, "G", SONNET)
    assert R == pytest.approx(expected)
    assert lo <= R <= hi


def test_ratio_bootstrap_no_variance_gives_point_interval():
    # Every run has identical cost and correctness, so every resample gives
    # exactly the same ratio: the interval collapses to a point.
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    R, lo, hi = ratio_bootstrap(records, SONNET, n=200, seed=0)
    assert R == pytest.approx(0.5)
    assert lo == pytest.approx(0.5)
    assert hi == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# verdict() -- the branches enumerated in the task-6 brief.
# ---------------------------------------------------------------------------


def test_verdict_go_when_b_half_cost_and_equal_correctness():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    v = verdict(records)
    assert v["P1"] == "confirmed"
    assert v["P2"] is True
    assert v["stage1"] == "GO"


def test_verdict_stop_when_b_at_0_7x_not_confirmed():
    rows = _uniform_dataset(cost_b=0.07, cost_g=0.10)
    records = load(_dump(rows))
    v = verdict(records)
    assert v["P1"] == "not confirmed"
    assert v["stage1"] == "STOP"


def test_verdict_stop_when_b_half_cost_but_correctness_10_points_lower():
    # B: 36/40 tasks correct (90%); G: 40/40 (100%) -- a 10-point gap.
    rows = _uniform_dataset(cost_b=0.045, cost_g=0.10, b_correct_tasks=36)
    records = load(_dump(rows))
    v = verdict(records)
    assert v["P1"] == "confirmed"  # cost ratio alone still looks good
    assert v["P2"] is False  # but non-inferiority fails
    assert v["stage1"] == "STOP"


def test_verdict_refuted_when_b_at_1_1x():
    rows = _uniform_dataset(cost_b=0.11, cost_g=0.10)
    records = load(_dump(rows))
    v = verdict(records)
    assert v["P1"] == "refuted"
    assert v["stage1"] == "STOP"


def test_verdict_only_considers_sonnet():
    # Haiku data alone (no sonnet records) should raise, since P1/P2 are defined on Sonnet.
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10, model="claude-haiku-4-5")
    records = load(_dump(rows))
    with pytest.raises(ValueError):
        verdict(records)


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


def test_render_includes_table_and_verdict():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    md = render(records)
    assert "|" in md  # a markdown table
    assert "GO" in md or "STOP" in md
    assert "P1" in md
    assert "P2" in md


def test_render_reports_both_models_when_present():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    rows += _uniform_dataset(cost_b=0.02, cost_g=0.05, model="claude-haiku-4-5")
    records = load(_dump(rows))
    md = render(records)
    assert SONNET in md
    assert "claude-haiku-4-5" in md
