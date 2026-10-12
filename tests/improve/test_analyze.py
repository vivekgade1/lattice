"""The ranker (lattice/improve/analyze.py): the I-R arithmetic on a hand-made event set,
seconds never converted to dollars, closed items left out, protected targets needs_owner
(the I-4 refusal), and the report text."""

from __future__ import annotations

from typing import Any

import pytest

from lattice.improve.analyze import (
    BLIND_SPOTS,
    METHOD,
    PROTECTED,
    analyze,
    clusters,
    is_protected,
    report_text,
)


def ev(kind: str, ts: str, **f: Any) -> dict[str, Any]:
    return {"v": 1, "ts": ts, "project": "t", "kind": kind, "id": f.pop("id", f"e-{ts}"), **f}


def hand_set() -> list[dict[str, Any]]:
    out = []
    for i in range(3):  # 3 level gates, X 100 s and D 20 s each, 5 X tries per race
        out.append(
            ev(
                "gate_run",
                f"2026-10-01T00:0{i}:00Z",
                gate="level",
                mode="regression",
                seconds=125.0,
                layer_seconds={"X": 100.0, "D": 20.0},
                x_repeats=5,
            )
        )
    out.append(
        ev(
            "node_ended",
            "2026-10-01T01:00:00Z",
            status="gave_up",
            cause="no_progress",
            wall_s=260.0,
            usage={"cost_usd_list_estimate": 1.5},
        )
    )
    out.append(
        ev(
            "node_ended",
            "2026-10-01T02:00:00Z",
            status="gave_up",
            cause="no_progress",
            wall_s=100.0,
            usage={"cost_usd_list_estimate": 0.5},
        )
    )
    out.append(ev("node_ended", "2026-10-01T03:00:00Z", status="passed", wall_s=999.0))
    out.append(
        ev(
            "node_ended",
            "2026-10-01T03:30:00Z",
            status="gave_up",
            cause="token_budget",
            usage={"cost_usd_list_estimate": 4.0},
        )
    )
    out.append(ev("change", "2026-10-01T04:00:00Z", decision="rejected", cost_usd=9999.0))
    out.append(ev("change", "2026-10-01T04:10:00Z", decision="accepted", cost_usd=5.0))
    out.append(
        ev(
            "finding_class",
            "2026-10-01T05:00:00Z",
            signal_key="unchecked:request_header",
            count=6,
            snapshot=True,
        )
    )
    out.append(
        ev(
            "finding_class",
            "2026-10-01T06:00:00Z",
            signal_key="unchecked:request_header",
            count=4,
            snapshot=True,
        )
    )
    out.append(ev("finding_class", "2026-10-01T05:30:00Z", signal_key="D:status", count=3))
    out.append(ev("finding_class", "2026-10-01T05:40:00Z", signal_key="D:status", count=2))
    out.append(ev("human", "2026-10-01T07:00:00Z", what="resume", actor="owner"))
    out.append(ev("human", "2026-10-01T07:10:00Z", what="resume", actor="owner"))
    out.append(ev("human", "2026-10-01T07:20:00Z", what="approve", actor="owner"))  # routine
    return out


def test_the_i_r_arithmetic_and_class_order() -> None:
    cl = clusters(hand_set())
    got = [(c.key, c.score_class, c.frequency, c.cost_per, c.score) for c in cl]
    assert got == [
        ("cause:no_progress", "A", 2, 180.0, 360.0),  # (260 + 100) / 2 = 180; 2 x 180
        ("gate:level_regression:X", "A", 3, 100.0, 300.0),
        ("gate:level_regression:D", "A", 3, 20.0, 60.0),
        ("change:rejected", "B", 1, 9999.0, 9999.0),  # dollars rank below every second
        ("cause:token_budget", "B", 1, 4.0, 4.0),
        ("D:status", "C", 5, None, None),  # occurrences add up
        ("unchecked:request_header", "C", 4, None, None),  # a snapshot: the latest level
        ("human:resume", "C", 2, None, None),  # approve is routine: no cluster
    ]


def test_seconds_are_never_converted_to_dollars() -> None:
    """A cluster with seconds is scored in seconds even when it also has dollars; a dollar
    cluster never outranks a seconds cluster, however large."""
    evs = [
        ev(
            "node_ended",
            "2026-10-01T00:00:00Z",
            status="gave_up",
            cause="timeout",
            wall_s=1.0,
            usage={"cost_usd_list_estimate": 500.0},
        ),
        ev("change", "2026-10-01T00:01:00Z", decision="rejected", cost_usd=10_000.0),
    ]
    rep = analyze(evs)
    a, b = rep["proposals"]
    assert (a["signal_key"], a["unit"], a["score"]) == ("cause:timeout", "s", 1.0)
    assert (b["signal_key"], b["unit"], b["score"]) == ("change:rejected", "usd", 10_000.0)


def test_closed_items_are_left_out_and_recurrence_is_counted() -> None:
    ledger = [
        {
            "id": "I-x",
            "signal": "cause:no_progress",
            "status": "closed",
            "commit": "abc1234",
            "ts": "2026-10-01T01:30:00Z",
        }
    ]
    rep = analyze(hand_set(), ledger, top=10)
    keys = [p["signal_key"] for p in rep["proposals"]]
    assert "cause:no_progress" not in keys and keys[0] == "gate:level_regression:X"
    (closed,) = rep["closed"]
    assert closed["signal_key"] == "cause:no_progress" and closed["recurred"] == 1
    # a later "open" entry for the same signal re-opens it; a pattern closes many keys
    ledger += [
        {
            "id": "I-x",
            "signal": "cause:no_progress",
            "status": "open",
            "ts": "2026-10-02T00:00:00Z",
        },
        {
            "id": "I-y",
            "signal": "gate:level_regression:*",
            "status": "closed",
            "ts": "2026-10-02T00:00:00Z",
        },
    ]
    keys = [p["signal_key"] for p in analyze(hand_set(), ledger, top=10)["proposals"]]
    assert keys[0] == "cause:no_progress" and not any(k.startswith("gate:level") for k in keys)


def test_at_most_n_proposals_with_evidence() -> None:
    rep = analyze(hand_set(), top=2)
    assert len(rep["proposals"]) == 2
    p = rep["proposals"][1]
    assert p["evidence"] == [
        "e-2026-10-01T00:00:00Z",
        "e-2026-10-01T00:01:00Z",
        "e-2026-10-01T00:02:00Z",
    ]
    assert set(p) >= {
        "id",
        "title",
        "signal_key",
        "evidence",
        "score",
        "kind",
        "target_paths",
        "why",
        "status",
    }
    assert (
        p["id"].startswith("imp-") and p["id"] == analyze(hand_set(), top=2)["proposals"][1]["id"]
    )
    assert rep["events"] == len(hand_set())
    assert rep["method"] == METHOD and rep["blind_spots"] == BLIND_SPOTS
    assert analyze(hand_set(), top=0)["proposals"] == []


def test_layer_x_at_level_gates_names_the_config_change_and_its_expected_saving() -> None:
    p = analyze(hand_set(), top=2)["proposals"][1]
    assert p["signal_key"] == "gate:level_regression:X" and p["kind"] == "change_set"
    assert p["target_paths"] == ["studio.toml"] and "--x-repeats" in p["title"]
    assert p["status"] == "open" and p["draftable"] is True
    assert p["expected"]["x_repeats_from"] == 5 and p["expected"]["x_repeats_to"] == 2
    assert p["expected"]["saving_s_per_gate"] == 60.0  # 100 s x (1 - 2/5)


def test_a_baseline_on_an_already_gated_tip_is_a_rerun() -> None:
    evs = [
        ev(
            "gate_run",
            "2026-10-01T00:00:00Z",
            gate="level",
            mode="full",
            seconds=400.0,
            sha="941735104cf670beb7f0905cd8d14b6022eda586",
        ),
        ev(
            "gate_run",
            "2026-10-01T01:00:00Z",
            gate="baseline",
            mode="full",
            seconds=416.0,
            sha="941735104c",
        ),
        ev(
            "gate_run",
            "2026-10-01T02:00:00Z",
            gate="baseline",
            mode="full",
            seconds=146.0,
            sha="fcdb35440e",
        ),
        ev(
            "gate_run",
            "2026-10-01T03:00:00Z",
            gate="baseline",
            mode="full",
            seconds=0.0,
            reused=True,
            sha="941735104c",
        ),
    ]
    keys = {c.key: (c.frequency, c.score) for c in clusters(evs)}
    assert keys == {
        "gate:level_full:total": (1, 400.0),
        "gate:baseline_rerun:total": (1, 416.0),
        "gate:baseline:total": (1, 146.0),
    }


# ---------------------------------------------------------------- I-4: the refusal


def i4_lint_noise() -> list[dict[str, Any]]:
    """Top cluster: the L012 lint is too noisy (each firing cost a redraft)."""
    evs = [
        ev(
            "finding_class",
            f"2026-10-01T00:0{i}:00Z",
            signal_key="lint:L012",
            count=1,
            cost_s=180.0,
        )
        for i in range(6)
    ]
    evs.append(ev("gate_run", "2026-10-01T01:00:00Z", gate="node", seconds=30.0))
    return evs


def i4_checker() -> list[dict[str, Any]]:
    """Top cluster: layer C (invariants) at level gates; no knob, so the checker."""
    evs = [
        ev(
            "gate_run",
            f"2026-10-01T00:0{i}:00Z",
            gate="level",
            mode="regression",
            seconds=200.0,
            layer_seconds={"C": 150.0, "D": 50.0},
        )
        for i in range(4)
    ]
    return evs


def test_i4_a_lint_proposal_is_normal() -> None:
    top = analyze(i4_lint_noise())["proposals"][0]
    assert top["signal_key"] == "lint:L012" and top["score"] == 1080.0
    assert top["target_paths"] == ["lattice/spec/lint.py", "lattice/spec/preconditions.py"]
    assert top["status"] == "open" and top["draftable"] is True and top["kind"] == "change_set"


def test_i4_a_checker_proposal_needs_the_owner() -> None:
    top = analyze(i4_checker())["proposals"][0]
    assert top["signal_key"] == "gate:level_regression:C"
    assert "lattice/spec/check.py" in top["target_paths"]
    assert top["status"] == "needs_owner" and top["draftable"] is False
    assert "never drafted" in top["why"]


@pytest.mark.parametrize(
    ("path", "protected"),
    [
        ("lattice/spec/check.py", True),
        ("./lattice/spec/process.py", True),
        ("lattice/spec/engines.py", True),
        ("lattice/spec/engines/mysql.toml", True),
        ("lattice/spec/model.py", True),
        ("docs/lattice-language-rules.md", True),
        ("lattice/spec/lint.py", False),
        ("lattice/spec/preconditions.py", False),
        ("studio.toml", False),
        ("lattice/studio/plan.py", False),
    ],
)
def test_the_protected_set(path: str, protected: bool) -> None:
    assert is_protected(path) is protected
    assert "lattice/spec/check.py" in PROTECTED


def test_report_text_states_the_method_blind_spots_and_the_owner() -> None:
    text = report_text(
        {**analyze(hand_set(), [{"signal": "D:*", "status": "closed"}]), "warnings": ["w1"]}
    )
    assert "Method: Frequency x cost" in text and "Blind spots:" in text
    for word in ("human time", "risk", "recurrence"):
        assert word in text
    assert "the owner decides" in text
    assert "1. [A]" in text and "expected: X 100.0 s" in text
    assert "Closed (left out):" in text and "warning: w1" in text
    assert "No proposals" in report_text(analyze([]))
