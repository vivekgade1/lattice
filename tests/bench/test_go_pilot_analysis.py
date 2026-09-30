"""Tests for the 5b stage-1 Go cost pilot's pre-registered analysis.

Spec: docs/superpowers/specs/2026-09-29-lattice-go-pilot.md, "Tests and decision"
(binding). Interfaces per the task-6 brief.
"""

from __future__ import annotations

import json

import pytest

from bench.analysis.go_pilot import (
    EXPECTED_RUNS_PER_CELL,
    HAIKU,
    P2_MARGIN,
    P2_Z,
    SONNET,
    _classify_p1,
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
    """rows: iterable of (task_id, arm, model, rep, correct, cost_usd) tuples.

    S2/family fields get fixed placeholder values (family="control", 1 round,
    100 output tokens, 10 lines, 50 AST nodes) -- irrelevant to the P1/P2/
    verdict tests that use this builder; tests that exercise S1/S2/the
    per-family breakdown build their own rows directly.
    """
    out = []
    for task_id, arm, model, rep, correct, cost_usd in rows:
        out.append(
            {
                "task_id": task_id,
                "arm": arm,
                "requested_model": model,
                "model": model,
                "rep": rep,
                "correct": correct,
                "cost_usd": cost_usd,
                "cost_basis": "cli_list_estimate",
                "family": "control",
                "failure": None if correct else "wrong_result",
                "rounds": 1,
                "output_tokens": 100,
                "program_lines": 10,
                "program_ast_nodes": 50,
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
    assert records[0].requested_model == SONNET
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
    "missing",
    [
        "task_id",
        "arm",
        "requested_model",
        "model",
        "rep",
        "correct",
        "cost_usd",
        "cost_basis",
        "family",
        "failure",
        "rounds",
        "output_tokens",
        "program_lines",
        "program_ast_nodes",
    ],
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


def test_newcombe_wilson_diff_default_z_is_two_sided_95():
    # Sanity: the default z (used by the published-example test above) is the
    # standard two-sided-95% critical value, distinct from P2_Z.
    lo_95, _ = newcombe_wilson_diff(56, 70, 48, 80)
    lo_90, _ = newcombe_wilson_diff(56, 70, 48, 80, z=P2_Z)
    # The narrower (two-sided 90%) interval has a higher (less negative) lower bound.
    assert lo_90 > lo_95


# ---------------------------------------------------------------------------
# P2 revision before freeze: margin 10 points, one-sided 95% bound == the
# lower limit of the two-sided 90% Newcombe-Wilson interval (P2_Z, P2_MARGIN).
# The original 5-point/95%-two-sided margin was unattainable at n=120/arm
# even for a true tie (controller ruling, see the spec's "Revision before
# freeze" note); these two cases are its worked examples.
# ---------------------------------------------------------------------------


def test_newcombe_wilson_diff_true_tie_at_p85_n120_passes_revised_p2_margin():
    # 102/120 = 85% correct in both arms -- a true tie.
    lo, hi = newcombe_wilson_diff(102, 120, 102, 120, z=P2_Z)
    assert lo == pytest.approx(-0.0766, abs=1e-3)
    assert lo > -P2_MARGIN  # revised 10-point margin holds


def test_newcombe_wilson_diff_true_tie_at_p85_n120_would_fail_old_5pt_margin():
    # The same true tie fails the original 5-point margin -- this is exactly
    # the unattainability the controller ruling identified.
    lo, hi = newcombe_wilson_diff(102, 120, 102, 120, z=P2_Z)
    assert lo < -0.05


def test_newcombe_wilson_diff_b_12_points_lower_fails_revised_p2_margin():
    # G at 85% (102/120), B at 73% (73/100) -- exactly a 12-point gap.
    lo, hi = newcombe_wilson_diff(73, 100, 102, 120, z=P2_Z)
    assert lo == pytest.approx(-0.2108, abs=1e-3)
    assert lo < -P2_MARGIN  # fails even the revised, wider margin


def test_verdict_p2_true_tie_at_p85_n120_via_full_pipeline():
    # Integration check: the same true tie, run through verdict()'s P2 (which
    # uses P2_Z/P2_MARGIN internally), on a cost ratio that alone would confirm P1.
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10, b_correct_tasks=34, g_correct_tasks=34)
    records = load(_dump(rows))
    v = verdict(records)
    assert v["P2"] is True
    assert v["stage1"] == "GO"


def test_verdict_p2_b_well_over_10_points_lower_fails_via_full_pipeline():
    # B ~12.5 points lower than G (29/40 = 72.5% vs 34/40 = 85%): still fails
    # even the revised, wider 10-point margin.
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10, b_correct_tasks=29, g_correct_tasks=34)
    records = load(_dump(rows))
    v = verdict(records)
    assert v["P2"] is False
    assert v["stage1"] == "STOP"


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


# ---------------------------------------------------------------------------
# render(): S1 (Haiku, reported not decisive), S2 (descriptive), per-family
# breakdown (spec's "Also reported" line).
# ---------------------------------------------------------------------------


def test_render_includes_s1_section_for_haiku():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    rows += _uniform_dataset(cost_b=0.02, cost_g=0.05, model=HAIKU)
    records = load(_dump(rows))
    md = render(records)
    assert "S1 (secondary" in md
    assert "not applied to the stage-1 verdict" in md


def test_render_includes_s2_and_family_sections():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    md = render(records)
    assert "S2 (secondary" in md
    assert "Per-family breakdown" in md
    assert "control" in md  # the only family in _uniform_dataset's rows


def _family_dataset():
    """One row per (family, arm) on Sonnet, exercising every outcome_class
    branch: correct, silent (wrong_result), loud (any other failure label),
    harness (harness_error). Distinct S2 fields per row so the means in the
    S2 table are independently checkable."""
    specs = [
        # task_id, family, arm, correct, failure -- every family present in
        # both arms, so ordering can be checked within a single arm's block.
        ("o01", "ownership", "B", True, None),
        ("o02", "ownership", "G", False, "wrong_result"),  # silent
        ("v01", "validation", "B", False, "assertion_failed"),  # loud
        ("v02", "validation", "G", False, "harness_error"),  # harness
        ("e01", "errors", "B", True, None),
        ("e02", "errors", "G", True, None),
        ("f01", "efficiency", "B", True, None),
        ("f02", "efficiency", "G", True, None),
        ("c01", "control", "B", True, None),
        ("c02", "control", "G", True, None),
    ]
    rows = []
    for task_id, family, arm, correct, failure in specs:
        rows.append(
            {
                "task_id": task_id,
                "arm": arm,
                "requested_model": SONNET,
                "model": SONNET,
                "rep": 0,
                "correct": correct,
                "cost_usd": 0.05,
                "cost_basis": "cli_list_estimate",
                "family": family,
                "failure": failure,
                "rounds": 1,
                "output_tokens": 100,
                "program_lines": 10,
                "program_ast_nodes": 50,
            }
        )
    return rows


def test_render_per_family_breakdown_classifies_every_outcome_class():
    records = load(_dump(_family_dataset()))
    md = render(records)
    for family in ("ownership", "validation", "errors", "efficiency", "control"):
        assert family in md
    # v02 (arm G, validation) is a harness failure: 0 loud, 1 harness.
    assert "(+1 harness)" in md


def test_render_per_family_breakdown_orders_families_as_in_spec():
    records = load(_dump(_family_dataset()))
    md = render(records)
    section = md.split("### Per-family breakdown")[1]
    # spec order: control, ownership, validation, errors, efficiency.
    assert (
        section.index("| control |")
        < section.index("| ownership |")
        < section.index("| validation |")
        < section.index("| errors |")
        < section.index("| efficiency |")
    )


def test_render_s2_table_reports_means_over_the_arm():
    rows = [
        {
            "task_id": "t1",
            "arm": "B",
            "requested_model": SONNET,
            "model": SONNET,
            "rep": 0,
            "correct": True,
            "cost_usd": 0.05,
            "cost_basis": "cli_list_estimate",
            "family": "control",
            "failure": None,
            "rounds": 1,
            "output_tokens": 100,
            "program_lines": 10,
            "program_ast_nodes": 50,
        },
        {
            "task_id": "t2",
            "arm": "B",
            "requested_model": SONNET,
            "model": SONNET,
            "rep": 0,
            "correct": True,
            "cost_usd": 0.05,
            "cost_basis": "cli_list_estimate",
            "family": "control",
            "failure": None,
            "rounds": 3,
            "output_tokens": 300,
            "program_lines": 30,
            "program_ast_nodes": 150,
        },
    ]
    records = load(_dump(rows))
    md = render(records)
    # mean over the 2 B rows: lines (10+30)/2=20.0, AST (50+150)/2=100,
    # rounds (1+3)/2=2.00, output tokens (100+300)/2=200.
    assert "| claude-sonnet-5 | B | 2 | 20.0 | 100 | 2.00 | 200 |" in md


# ---------------------------------------------------------------------------
# Model identity: the cell key is requested_model, not the served model.
# ---------------------------------------------------------------------------


def test_grouping_uses_requested_model_not_served_model():
    # A repair loop's calls can be served by a different model id than the
    # one requested (retry, fallback); the cell key must stay the requested
    # model regardless of what's recorded as served.
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    for r in rows:
        r["model"] = "claude-sonnet-5-20250929,claude-sonnet-5-20251015"
    records = load(_dump(rows))
    assert cost_per_correct(records, "B", SONNET) == pytest.approx(0.05)
    v = verdict(records)
    assert v["stage1"] == "GO"


# ---------------------------------------------------------------------------
# I1-I3: harness dedup/exclusion, the 5%-cell INVALID check, completeness,
# cost_basis -- controller ruling ("Revisions before freeze" #2).
# ---------------------------------------------------------------------------


def test_load_dedup_keeps_last_record_a_duplicated_harness_error_then_success_counts_once():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)  # complete, all correct
    target = next(r for r in rows if r["task_id"] == "t00" and r["arm"] == "B" and r["rep"] == 0)
    idx = rows.index(target)
    harness_dup = dict(target)
    harness_dup["correct"] = False
    harness_dup["failure"] = "harness_error"
    # The harness attempt is written BEFORE the (already-present) successful
    # retry at the same (requested_model, arm, task, rep) key -- exactly the
    # shape a --resume run produces.
    rows.insert(idx, harness_dup)

    records = load(_dump(rows))
    # load() dedups: the harness duplicate collapses into the real record,
    # so the total count is unchanged from the un-duplicated baseline.
    assert len(records) == 2 * EXPECTED_RUNS_PER_CELL
    kept = next(r for r in records if r.task_id == "t00" and r.arm == "B" and r.rep == 0)
    assert kept.correct is True
    assert kept.failure is None
    # And it's scored once, not twice: the verdict matches the un-duplicated
    # 100%-correct baseline (GO).
    v = verdict(records)
    assert v["stage1"] == "GO"


def test_verdict_invalid_when_harness_rate_exceeds_5_percent_of_a_sonnet_cell():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)  # complete, all correct
    b_rows = [r for r in rows if r["arm"] == "B"]
    # 8/120 = 6.67% > 5%, spread over 8 distinct tasks' first rep.
    for r in b_rows[:8]:
        r["correct"] = False
        r["failure"] = "harness_error"
    records = load(_dump(rows))
    v = verdict(records)
    assert v["stage1"] == "INVALID"


def test_verdict_still_go_when_harness_rate_is_under_5_percent():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    b_rows = [r for r in rows if r["arm"] == "B"]
    # 5/120 = 4.17% < 5%: excluded from P1/P2 but does not invalidate.
    for r in b_rows[:5]:
        r["correct"] = False
        r["failure"] = "harness_error"
    records = load(_dump(rows))
    v = verdict(records)
    assert v["stage1"] != "INVALID"
    assert v["stage1"] == "GO"


# ---------------------------------------------------------------------------
# A gogen build failure (bench.runners' _GogenBuildFailure path, task-5 fix round:
# it must count against arm B as "loud", never be dropped like a harness_error) --
# see bench.safety.outcome_class and its use in _cost_and_correct's harness
# exclusion.
# ---------------------------------------------------------------------------


def test_gogen_build_failure_counts_as_loud_not_excluded_like_harness():
    """bench.runners records a gogen build failure with ``failure="loud"``
    (correct=False). Unlike ``failure="harness_error"``, ``outcome_class``
    classifies it "loud", so it is counted toward cost and n (not excluded) --
    excluding a real B failure from cost/n, as a harness error would be, would
    bias the pilot's cost comparison toward B."""
    from bench.analysis.go_pilot import Record, _cost_and_correct
    from bench.safety import outcome_class

    def rec(task_id: str, correct: bool, failure: str | None) -> Record:
        return Record(
            task_id=task_id,
            arm="B",
            requested_model=SONNET,
            model=SONNET,
            rep=0,
            correct=correct,
            cost_usd=0.05,
            cost_basis="cli_list_estimate",
            family="control",
            failure=failure,
            rounds=1,
            output_tokens=100,
            program_lines=10,
            program_ast_nodes=50,
        )

    correct_run = rec("t1", True, None)
    gogen_failure = rec("t2", False, "loud")
    harness_failure = rec("t3", False, "harness_error")

    assert outcome_class(gogen_failure) == "loud"
    assert outcome_class(harness_failure) == "harness"

    group = (correct_run, gogen_failure, harness_failure)
    counted = [r for r in group if outcome_class(r) != "harness"]
    assert {r.task_id for r in counted} == {"t1", "t2"}  # t3 (harness) excluded

    cost, correct, n = _cost_and_correct(counted, "B", SONNET)
    assert n == 2  # t1 and t2 both counted; t3 is not
    assert correct == 1  # the gogen failure is not correct, but it is counted
    assert cost == pytest.approx(0.10)  # t1 + t2's cost only


def test_verdict_not_invalid_when_loud_gogen_failures_exceed_harness_threshold():
    """Loud gogen-build failures never trip the harness-rate INVALID gate, even
    well above the 5% threshold that would invalidate a run with that many
    ``harness_error`` records -- the gate is specifically about harness_error."""
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)  # 100%-correct baseline
    b_rows = [r for r in rows if r["arm"] == "B"]
    for r in b_rows[:20]:  # 20/120 = 16.7%, far over the 5% harness threshold
        r["correct"] = False
        r["failure"] = "loud"
    records = load(_dump(rows))
    v = verdict(records)
    assert v["stage1"] != "INVALID"


def test_verdict_raises_on_wrong_cost_basis():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    rows[0]["cost_basis"] = "cli_key_transport"  # a counted (non-harness) run
    records = load(_dump(rows))
    with pytest.raises(ValueError, match="cost_basis"):
        verdict(records)


def test_verdict_raises_on_missing_run():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    victim = next(r for r in rows if r["task_id"] == "t00" and r["arm"] == "B" and r["rep"] == 0)
    rows.remove(victim)
    records = load(_dump(rows))
    with pytest.raises(ValueError, match="expected"):
        verdict(records)


# ---------------------------------------------------------------------------
# M1: P1 boundary conditions, and task pairing.
# ---------------------------------------------------------------------------


def _wide_interval_dataset():
    """6 tasks: 5 "good" (B always correct, $0.05/run), 1 "bad" (B $0.05/run
    but 0/3 reps correct); G is $0.10/run and always correct throughout. The
    full-dataset point estimate R is exactly 0.6 (0.05*6*3 / (5*3) / 0.10),
    but because a single bad task dominates a 6-task resample, the
    bootstrap's 97.5th percentile reaches 1.0: R<=0.6 with an interval too
    wide to trust -- "not confirmed", not "confirmed"."""
    rows = []
    for i in range(5):
        tid = f"g{i}"
        for rep in range(3):
            rows.append((tid, "B", SONNET, rep, True, 0.05))
            rows.append((tid, "G", SONNET, rep, True, 0.10))
    for rep in range(3):
        rows.append(("bad", "B", SONNET, rep, False, 0.05))
        rows.append(("bad", "G", SONNET, rep, True, 0.10))
    return _records(rows)


def test_p1_not_confirmed_when_r_le_0_6_but_hi_ge_1_0():
    records = load(_dump(_wide_interval_dataset()))
    R, lo, hi = ratio_bootstrap(records, SONNET, n=10_000, seed=0)
    assert R == pytest.approx(0.6)
    assert hi >= 1.0
    assert _classify_p1(R, hi) == "not confirmed"


def test_p1_confirmed_when_r_exactly_0_6_and_hi_below_1():
    # Zero-variance dataset: R = lo = hi = 0.6 exactly.
    rows = _uniform_dataset(cost_b=0.06, cost_g=0.10)
    records = load(_dump(rows))
    R, lo, hi = ratio_bootstrap(records, SONNET, n=200, seed=0)
    assert R == pytest.approx(0.6)
    assert hi < 1.0
    assert _classify_p1(R, hi) == "confirmed"
    assert verdict(records)["P1"] == "confirmed"


def test_p1_refuted_when_r_exactly_1_0():
    rows = _uniform_dataset(cost_b=0.10, cost_g=0.10)
    records = load(_dump(rows))
    R, lo, hi = ratio_bootstrap(records, SONNET, n=200, seed=0)
    assert R == pytest.approx(1.0)
    assert _classify_p1(R, hi) == "refuted"
    assert verdict(records)["P1"] == "refuted"


def _correlated_pairing_dataset():
    """5 "easy" + 5 "hard" tasks. Within a task, arm B and arm G share the
    EXACT same per-rep correctness pattern (perfectly correlated difficulty:
    a hard task is hard for both arms at once). Costs are fixed ($0.05/$0.10
    per run) regardless of difficulty, so every task's own cost/correct
    ratio is exactly 0.5 no matter how many of its reps are correct. Task-
    paired resampling should therefore collapse to a point interval; a
    resampling implementation that decorrelates B's and G's task draws
    should not."""
    rows = []
    for i in range(5):
        tid = f"e{i}"
        for rep in range(3):
            rows.append((tid, "B", SONNET, rep, True, 0.05))
            rows.append((tid, "G", SONNET, rep, True, 0.10))
    for i in range(5):
        tid = f"h{i}"
        for rep in range(3):
            correct = rep == 0  # 1/3 reps correct -- same reps, both arms
            rows.append((tid, "B", SONNET, rep, correct, 0.05))
            rows.append((tid, "G", SONNET, rep, correct, 0.10))
    return _records(rows)


def _independent_bootstrap(records, model, *, n=10_000, seed=0):
    """A deliberately WRONG bootstrap that resamples arm B's and arm G's
    tasks INDEPENDENTLY -- breaking the task pairing the real
    ``ratio_bootstrap`` relies on. Reimplemented here (not imported from
    go_pilot) to show what an incorrect implementation would produce on the
    exact same data."""
    import math as _math
    import random as _random
    from collections import defaultdict as _defaultdict

    group = [r for r in records if r.requested_model == model]
    by_task = _defaultdict(list)
    for r in group:
        by_task[r.task_id].append(r)
    task_ids = sorted(by_task)
    rng = _random.Random(seed)
    boots = []
    for _ in range(n):
        sampled_b = rng.choices(task_ids, k=len(task_ids))
        sampled_g = rng.choices(task_ids, k=len(task_ids))
        b_recs = [r for t in sampled_b for r in by_task[t] if r.arm == "B"]
        g_recs = [r for t in sampled_g for r in by_task[t] if r.arm == "G"]
        cb, corb = sum(r.cost_usd for r in b_recs), sum(1 for r in b_recs if r.correct)
        cg, corg = sum(r.cost_usd for r in g_recs), sum(1 for r in g_recs if r.correct)
        if corb == 0 or corg == 0:
            continue
        boots.append((cb / corb) / (cg / corg))
    boots.sort()

    def pct(q):
        idx = q * (len(boots) - 1)
        lo_i, hi_i = _math.floor(idx), _math.ceil(idx)
        if lo_i == hi_i:
            return boots[int(idx)]
        frac = idx - lo_i
        return boots[lo_i] + frac * (boots[hi_i] - boots[lo_i])

    return pct(0.025), pct(0.975)


def test_task_pairing_matters_an_independent_implementation_gives_a_different_interval():
    records = load(_dump(_correlated_pairing_dataset()))
    R, lo, hi = ratio_bootstrap(records, SONNET, n=10_000, seed=0)
    # Paired (the real algorithm): every task's own B/G ratio is exactly 0.5
    # regardless of that task's difficulty, since difficulty is perfectly
    # correlated between arms -- the interval collapses to a point.
    assert R == pytest.approx(0.5)
    assert lo == pytest.approx(0.5)
    assert hi == pytest.approx(0.5)
    # Resampling each arm's tasks independently decorrelates that shared
    # difficulty and gets a materially different -- much wider -- interval
    # on the exact same data.
    ind_lo, ind_hi = _independent_bootstrap(records, SONNET, n=10_000, seed=0)
    assert ind_lo < lo - 0.05 or ind_hi > hi + 0.05
    assert (ind_hi - ind_lo) > (hi - lo) + 0.1


# ---------------------------------------------------------------------------
# M3: Wilson interval for the loud rate; dropped-resample reporting; the P2
# margin printed as "10 points".
# ---------------------------------------------------------------------------


def test_render_loud_column_has_a_wilson_interval():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    md = render(records)
    assert "loud [95% CI]" in md


def test_render_reports_dropped_bootstrap_resamples():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    md = render(records)
    assert "dropped for an undefined ratio" in md


def test_render_prints_p2_margin_as_points_not_percent():
    rows = _uniform_dataset(cost_b=0.05, cost_g=0.10)
    records = load(_dump(rows))
    md = render(records)
    assert "margin 10 points" in md
    assert "margin 10%" not in md


def test_zero_cost_arm_g_makes_the_ratio_undefined_not_a_crash(tmp_path) -> None:
    """An offline dry run's scripted calls cost nothing: a zero arm-G cost is an
    undefined ratio (reported as not computable), never a ZeroDivisionError."""
    rows = []
    for t in range(3):
        for arm in ("B", "G"):
            rows += _records([(f"t{t}", arm, HAIKU, 0, True, 0.0)])
    path = tmp_path / "outcomes.jsonl"
    _write_jsonl(path, rows)
    records = load(path)
    with pytest.raises(ValueError, match="cost ratio undefined"):
        ratio_bootstrap(records, HAIKU, n=10)
    report = render(records)
    assert "Not computable: cost ratio undefined" in report
