"""``import-plan`` (lattice/improve/importer.py) on Streak's real p2 record, and the I-R
replay of spike 3 (docs/results/2026-10-12-spike3-prereg.md) as a regression test: the
ranker's top 5 on p2-final + p1 + p3 with I-R's closed items in the ledger."""

from __future__ import annotations

import json
from pathlib import Path

from lattice.improve.analyze import analyze
from lattice.improve.importer import plan_events

DATA = Path(__file__).resolve().parents[2] / "docs/results/data/streak-plans-2026-10-11"

# I-R's closed items under the signal keys their fixes address (the prereg's ledger)
IR_LEDGER = [
    {
        "id": "I-05",
        "signal": "cause:spec_unsatisfiable",
        "status": "closed",
        "commit": "e93faea",
        "ts": "2026-10-12T03:00:00Z",
    },
    {
        "id": "I-06",
        "signal": "gate:node*:D",
        "status": "closed",
        "commit": "v1.11",
        "ts": "2026-10-12T03:00:00Z",
    },
    {
        "id": "I-07",
        "signal": "gate:level_full:*",
        "status": "closed",
        "commit": "v1.11",
        "ts": "2026-10-12T03:00:00Z",
    },
    {
        "id": "I-08",
        "signal": "human:spec_sync",
        "status": "closed",
        "commit": "13d6533",
        "ts": "2026-10-12T03:00:00Z",
    },
]


def load(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text())


def test_p2_final_becomes_the_events_it_holds() -> None:
    evs = plan_events(load("p2-final"), source="p2-final.json")
    gates = [e for e in evs if e["kind"] == "gate_run"]
    level = [(e["mode"], e["seconds"]) for e in gates if e["gate"] == "level"]
    assert level == [
        ("full", 422.47),
        ("full", 423.87),
        ("regression", 137.5),
        ("regression", 138.92),
        ("regression", 143.71),
        ("regression", 145.32),
        ("regression", 150.89),
    ]
    assert not any("layer_seconds" in e and e["layer_seconds"] for e in gates), "never recorded"
    base = [
        (e["sha"], e["seconds"], bool(e.get("derived"))) for e in gates if e["gate"] == "baseline"
    ]
    # the first two survive only as timeline lines (gap from "resume N started")
    assert base == [
        ("941735104c", 416.0, True),
        ("fcdb35440e", 146.0, True),
        ("a0679e9044", 150.8, False),
    ]
    ends = {
        (e["node"], e["build"]): (e["status"], e.get("cause"), e["wall_s"])
        for e in evs
        if e["kind"] == "node_ended"
    }
    assert ends[("account", "b4")] == ("gave_up", "other", 309.0)  # cause not in the record
    assert ends[("sync", "b11")] == ("gave_up", "other", 325.0)
    assert ends[("account", "b10")][:2] == ("passed", None)
    assert ends[("scaffold", "b1")] == ("passed", None, 1901.74)
    humans = [e["what"] for e in evs if e["kind"] == "human"]
    assert humans == ["approve", "resume", "resume", "resume"]
    assert [e["signal_key"] for e in evs if e["kind"] == "finding_class"] == [
        "land:branch_checked_out"
    ]
    assert len({e["id"] for e in evs}) == len(evs), "ids are unique"
    assert plan_events(load("p2-final"), source="x")[0]["id"] == evs[0]["id"], "deterministic"


def test_p3_stopped_node_is_stopped_by_owner() -> None:
    evs = plan_events(load("p3"))
    (end,) = [e for e in evs if e["kind"] == "node_ended"]
    assert (end["node"], end["status"], end["cause"], end["wall_s"]) == (
        "account",
        "stopped",
        "stopped_by_owner",
        2709.23,
    )
    (base,) = [e for e in evs if e["kind"] == "gate_run"]
    assert base["seconds"] == 426.35 and base["sha"].startswith("941735104c")


def test_the_i_r_replay() -> None:
    """The ranker's top 5 on the real records (prereg: predicted exactly this order)."""
    evs = [e for name in ("p2-final", "p1", "p3") for e in plan_events(load(name))]
    rep = analyze(evs, IR_LEDGER, top=5)
    got = [(p["signal_key"], p["frequency"], p["score"]) for p in rep["proposals"]]
    assert got == [
        ("cause:stopped_by_owner", 1, 2709.23),
        ("gate:baseline_rerun:total", 2, 842.35),
        ("gate:level_regression:total", 5, 716.34),
        ("cause:other", 2, 634.0),
        ("gate:baseline:total", 2, 296.8),
    ]
    assert [c["signal_key"] for c in rep["closed"]] == ["gate:level_full:total"]
