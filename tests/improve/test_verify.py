"""``lattice improve verify`` (docs/results/2026-10-13-improve2-prereg.md): one test per probe
row. V1 replays the real I-3 data of spike 3 (docs/results/data/2026-10-12-spike3), V2 is
synthetic with a known truth, V3 is the ledger edge cases. The predictions are the
pre-registration's, written before the code."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from lattice.improve.__main__ import main
from lattice.improve.analyze import analyze
from lattice.improve.verify import canonical_ts

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs/results/data/2026-10-12-spike3"
X = "gate:level_regression:X"
CLOSE = "2026-10-12T23:08:00Z"
REAL_COMMIT = "scratch-clone studio.toml (not merged)"
_ids = itertools.count()


def sub(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    return d


def i3_lines() -> list[str]:
    return (DATA / "i3-events.jsonl").read_text().splitlines()


def entry(
    ts: str | None = CLOSE,
    status: str = "closed",
    id: str | None = "I-01",
    signal: str = X,
    commit: str | None = REAL_COMMIT,
) -> dict[str, Any]:
    e: dict[str, Any] = {"id": id, "signal": signal, "status": status, "commit": commit}
    if ts is not None:
        e["ts"] = ts
    return e


def make_env(
    tmp_path: Path,
    events: list[dict[str, Any]] | list[str] | None = None,
    ledger: list[dict[str, Any]] | None = None,
    toml: str | None = None,
) -> tuple[Path, Path]:
    """(project, state): an empty project (or one with studio.toml) and a state directory
    holding the log and the ledger. `events` None: the 18 events of I-3."""
    project, state = tmp_path / "proj", tmp_path / "state"
    project.mkdir()
    state.mkdir()
    lines = i3_lines() if events is None else events
    (state / "events.jsonl").write_text(
        "".join((ln if isinstance(ln, str) else json.dumps(ln)) + "\n" for ln in lines)
    )
    if ledger is not None:
        (state / "improvements.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ledger))
    if toml is not None:
        (project / "studio.toml").write_text(toml)
    return project, state


def run(capsys, project: Path, state: Path, *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    rc = main([*argv, "--project", str(project), "--state", str(state)])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def verify(capsys, project: Path, state: Path, ident: str, *extra: str) -> tuple[int, dict]:
    rc, out, _ = run(capsys, project, state, "verify", ident, "--json", *extra)
    return rc, json.loads(out)


def ev(kind: str, ts: str, **f: Any) -> dict[str, Any]:
    return {"v": 1, "ts": ts, "project": "t", "kind": kind, "id": f"e-{next(_ids):04d}", **f}


# ---------------------------------------------------------------- V1: the real I-3 data


def v1(tmp_path: Path, capsys, ts: str | None = CLOSE, signal: str = X, *extra: str):
    project, state = make_env(tmp_path, ledger=[entry(ts, signal=signal)])
    return verify(capsys, project, state, "I-01", *extra)


def test_v1a_the_real_i3_data_reproduces_the_hand_numbers(tmp_path: Path, capsys) -> None:
    rc, d = v1(tmp_path, capsys)
    assert rc == 0 and d["verdict"] == "improved" and d["direction"] == "down"
    assert (d["before"]["n"], d["after"]["n"]) == (3, 3)
    assert (d["before"]["cost_per"], d["after"]["cost_per"]) == (119.51, 58.5)
    assert (d["before"]["min"], d["before"]["max"]) == (119.31, 119.75)
    assert (d["after"]["min"], d["after"]["max"]) == (58.44, 58.56)
    assert d["change"] == {"delta": -61.01, "relative": -0.51, "separated": True}
    assert d["predicted"]["delta"] == -71.7 and d["predicted"]["unit"] == "s"
    assert d["error"] == 0.149  # I-3's 14.9%
    assert d["closing_point"] == {"ts": CLOSE, "source": "ledger"}
    assert d["keys"] == [X] and d["family"] == "cost" and d["unit"] == "s"
    assert d["excluded"] == {"at_close": 0, "undated": 0}
    pw = d["context"]["per_window"]
    assert pw["before"] == {"x_repeats": [5], "selected": [152], "mode": ["regression"],
                            "sha": ["a0679e9"]}  # fmt: skip
    assert pw["after"]["x_repeats"] == [2] and pw["after"]["sha"] == ["a0679e9"]
    assert d["entry"]["commit"] == REAL_COMMIT and d["warnings"] == []
    assert list(d) == ["format", "command", "id", "entry", "closing_point", "keys", "family",
                       "unit", "verdict", "reason", "direction", "before", "after", "change",
                       "predicted", "prediction_note", "error", "excluded", "context",
                       "per_key", "blind_spots", "warnings"]  # fmt: skip


def test_v1a_text_output_names_every_part(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry()])
    rc, out, err = run(capsys, project, state, "verify", "I-01")
    assert rc == 0 and err == ""
    assert out.startswith(
        f"Lattice improve verify: I-01 ({X}), closed {CLOSE}, commit {REAL_COMMIT}"
    )
    for label in (
        "Verdict: improved",
        "Family: cost",
        "Before (ts < close):",
        "After (ts > close):",
        "Change: -61.01 s",
        "every after below every before: yes",
        "Predicted: -71.7 s",
        "Error: |measured - predicted| / |predicted| = 14.9%",
        "Excluded:",
        "Context:",
        "Evidence (before):",
        "Blind spots:",
    ):
        assert label in out, label
    assert str(tmp_path) not in out  # no absolute path


def test_v1b_a_close_at_the_third_before_run(tmp_path: Path, capsys) -> None:
    rc, d = v1(tmp_path, capsys, "2026-10-12T23:07:11Z")
    assert rc == 1 and d["verdict"] == "not_enough_data"
    assert (d["before"]["n"], d["after"]["n"]) == (2, 3) and d["excluded"]["at_close"] == 1
    assert d["predicted"]["delta"] == -71.7 and d["error"] is None
    assert d["change"]["delta"] is None


def test_v1c_a_close_at_the_first_after_run(tmp_path: Path, capsys) -> None:
    rc, d = v1(tmp_path, capsys, "2026-10-12T23:09:06Z")
    assert rc == 1 and d["verdict"] == "not_enough_data"
    assert (d["before"]["n"], d["after"]["n"]) == (3, 2) and d["excluded"]["at_close"] == 1
    assert d["predicted"]["delta"] == -71.7 and d["error"] is None


def test_v1d_closed_as_really_written_there_is_no_after_window(tmp_path: Path, capsys) -> None:
    rc, d = v1(tmp_path, capsys, "2026-10-12T23:12:00Z")
    assert rc == 1 and d["verdict"] == "not_enough_data"
    assert (d["before"]["n"], d["after"]["n"]) == (6, 0)
    assert d["predicted"] is None and d["error"] is None  # repeats tie 5/2
    assert "mixed x_repeats" in d["prediction_note"]


def test_v1e_the_d_layer_is_the_control(tmp_path: Path, capsys) -> None:
    rc, d = v1(tmp_path, capsys, signal="gate:level_regression:D")
    assert rc == 1 and d["verdict"] == "not_improved" and d["reason"] == "no material change"
    assert (d["before"]["n"], d["after"]["n"]) == (3, 3)
    assert (d["before"]["cost_per"], d["after"]["cost_per"]) == (22.52, 22.31)
    assert d["change"] == {"delta": -0.21, "relative": -0.009, "separated": True}
    assert d["direction"] == "flat" and d["predicted"] is None and d["error"] is None


def test_v1f_a_pattern_sums_the_keys_of_one_gate_run(tmp_path: Path, capsys) -> None:
    rc, d = v1(tmp_path, capsys, signal="gate:level_regression:*")
    assert rc == 0 and d["verdict"] == "improved"
    assert len(d["keys"]) == 4 and (d["before"]["n"], d["after"]["n"]) == (3, 3)
    assert (d["before"]["cost_per"], d["after"]["cost_per"]) == (142.46, 81.19)
    assert d["change"]["delta"] == -61.27 and d["change"]["relative"] == -0.43
    assert d["predicted"] is None and d["error"] is None
    assert {r["key"]: r["verdict"] for r in d["per_key"]} == {
        "gate:level_regression:A": "not_improved",
        "gate:level_regression:C": "improved",  # 0.40 -> 0.35 s, fully separated
        "gate:level_regression:D": "not_improved",
        X: "improved",
    }


def test_v1g_at_supplies_the_landing_time_of_the_real_ledger_line(tmp_path: Path, capsys) -> None:
    a_rc, a = v1(sub(tmp_path, "a"), capsys)
    rc, g = v1(sub(tmp_path, "g"), capsys, "2026-10-12T23:12:00Z", X, "--at", CLOSE)
    assert rc == a_rc == 0 and g["verdict"] == "improved"
    assert g["closing_point"] == {"ts": CLOSE, "source": "--at"}
    assert g["warnings"] == ["closing point overridden by --at (ledger ts 2026-10-12T23:12:00Z)"]
    for d in (a, g):  # the output differs in the closing point source, the ledger ts, the warning
        d["closing_point"], d["entry"], d["warnings"] = None, None, []
    assert a == g  # the numbers are V1a's exactly


def test_v1h_a_malformed_at_and_at_on_an_open_entry(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry("2026-10-12T23:12:00Z")])
    rc, out, err = run(
        capsys, project, state, "verify", "I-01", "--json", "--at", "2026-10-12 23:08"
    )
    d = json.loads(out)
    assert rc == 3 and d["refused"] is True and d["reason"] == "bad_at"
    assert err.startswith("lattice improve: refused: ") and out.startswith("{")
    for bad in ("2026-10-12T23:08:00", "2026-10-12T23:8:00Z", "2026-10-12T25:08:00Z", ""):
        rc, d = verify(capsys, project, state, "I-01", "--at", bad)
        assert rc == 3 and d["reason"] == "bad_at", bad
    (state / "improvements.jsonl").write_text(json.dumps(entry(status="open")) + "\n")
    rc, d = verify(capsys, project, state, "I-01", "--at", CLOSE)
    assert rc == 3 and d["reason"] == "not_closed"


# ---------------------------------------------------------------- V2: synthetic, known truth

C = "2026-10-01T12:00:00Z"
BEFORE_TS = ("2026-10-01T10:00:00Z", "2026-10-01T10:30:00Z", "2026-10-01T11:00:00Z")
AFTER_TS = ("2026-10-01T12:30:00Z", "2026-10-01T13:00:00Z", "2026-10-01T13:30:00Z")


def gate(ts: str, x: float, repeats: int, d: float | None = None, **f: Any) -> dict[str, Any]:
    layers = {"X": x} if d is None else {"X": x, "D": d}
    return ev("gate_run", ts, gate="level", mode="regression", seconds=sum(layers.values()),
              layer_seconds=layers, x_repeats=repeats, **f)  # fmt: skip


def v2_events(before=(100, 101, 99), after=(40, 41, 39), **kw: Any) -> list[dict[str, Any]]:
    out = [gate(t, x, 5, **kw) for t, x in zip(BEFORE_TS, before, strict=True)]
    out += [gate(t, x, 2, **kw) for t, x in zip(AFTER_TS, after, strict=True)]
    return out


def v2(tmp_path: Path, capsys, events, signal: str = X, ts: str = C, ident: str = "I-01"):
    project, state = make_env(tmp_path, events, [entry(ts, signal=signal)])
    rc, d = verify(capsys, project, state, ident)
    return rc, d, project, state


def recurred_in_report(capsys, project: Path, state: Path, key: str) -> int:
    _, out, _ = run(capsys, project, state, "report", "--json")
    return next(c["recurred"] for c in json.loads(out)["closed"] if c["signal_key"] == key)


def test_v2a_improved(tmp_path: Path, capsys) -> None:
    rc, d, project, state = v2(tmp_path, capsys, v2_events())
    assert rc == 0 and d["verdict"] == "improved"
    assert (d["before"]["cost_per"], d["after"]["cost_per"]) == (100.0, 40.0)
    assert d["change"] == {"delta": -60.0, "relative": -0.6, "separated": True}
    assert d["predicted"]["delta"] == -60.0 and d["error"] == 0.0
    assert d["after"]["frequency"] == recurred_in_report(capsys, project, state, X)


def test_v2b_no_change(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, v2_events(after=(100, 99, 101)))
    assert rc == 1 and d["verdict"] == "not_improved" and d["reason"] == "no material change"
    assert d["change"]["delta"] == 0.0 and d["direction"] == "flat" and d["error"] == 1.0


def test_v2c_worse(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, v2_events(after=(110, 111, 109)))
    assert rc == 1 and d["verdict"] == "not_improved" and d["reason"] == "cost rose"
    assert d["change"]["delta"] == 10.0 and d["change"]["relative"] == 0.1
    assert d["direction"] == "up" and d["error"] == 1.167  # both signed: |10 - (-60)| / 60


def test_v2_overlap_is_not_improved(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, v2_events(after=(50, 100, 20)))  # mean 56.7, one at 100
    assert rc == 1 and d["verdict"] == "not_improved" and d["reason"] == "overlap"
    assert d["direction"] == "down" and d["change"]["separated"] is False


def node(ts: str, status: str, cause: str | None = None, wall: float | None = None,
         usd: float | None = None) -> dict[str, Any]:  # fmt: skip
    f: dict[str, Any] = {"status": status}
    if cause:
        f["cause"] = cause
    if wall is not None:
        f["wall_s"] = wall
    if usd is not None:
        f["usage"] = {"cost_usd_list_estimate": usd}
    return ev("node_ended", ts, **f)


def timeouts_before() -> list[dict[str, Any]]:
    out = [node(t, "gave_up", "timeout", 600.0) for t in BEFORE_TS]
    return out + [node("2026-10-01T11:10:00Z", "passed"), node("2026-10-01T11:20:00Z", "passed")]


def test_v2d_recurred(tmp_path: Path, capsys) -> None:
    after = [node(t, "passed") for t in AFTER_TS] + [node("2026-10-01T13:40:00Z", "gave_up",
                                                         "timeout", 600.0)]  # fmt: skip
    rc, d, project, state = v2(tmp_path, capsys, timeouts_before() + after, "cause:timeout")
    assert rc == 1 and d["verdict"] == "recurred" and d["family"] == "occurrence"
    assert (d["before"]["frequency"], d["after"]["frequency"]) == (3, 1)
    assert (d["before"]["exposure"], d["after"]["exposure"]) == (5, 4)
    assert d["predicted"] is None and d["error"] is None
    assert d["after"]["frequency"] == recurred_in_report(capsys, project, state, "cause:timeout")


def test_v2d_prime_exposure_decides_improved_or_not_enough(tmp_path: Path, capsys) -> None:
    three = [node(t, "passed") for t in AFTER_TS]
    rc, d, _, _ = v2(sub(tmp_path, "a"), capsys, timeouts_before() + three, "cause:timeout")
    assert rc == 0 and d["verdict"] == "improved" and d["after"]["exposure"] == 3
    rc, d, _, _ = v2(sub(tmp_path, "b"), capsys, timeouts_before() + three[:2], "cause:timeout")
    assert rc == 1 and d["verdict"] == "not_enough_data" and d["after"]["exposure"] == 2


def test_v2e_no_run_after_the_close(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, v2_events()[:3])
    assert rc == 1 and d["verdict"] == "not_enough_data" and d["after"]["n"] == 0
    assert d["predicted"]["delta"] == -60.0 and d["error"] is None


def test_v2f_one_run_after_the_close(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, v2_events()[:4])
    assert rc == 1 and d["verdict"] == "not_enough_data" and d["after"]["n"] == 1
    assert d["after"]["cost_per"] == 40.0 and d["change"]["delta"] is None


def test_v2g_other_signals_interleaved_are_not_this_signal(tmp_path: Path, capsys) -> None:
    evs = v2_events(d=None)
    evs = [gate(t, x, r, d=20.0 if t in BEFORE_TS else 30.0) for t, x, r in
           [(e["ts"], e["layer_seconds"]["X"], e["x_repeats"]) for e in evs]]  # fmt: skip
    evs += [ev("finding_class", f"2026-10-01T1{i}:15:00Z", signal_key="lint:L012", count=1,
               cost_s=60.0) for i in range(4)]  # fmt: skip
    rc, d, _, _ = v2(tmp_path, capsys, evs)
    assert rc == 0 and d["verdict"] == "improved" and d["keys"] == [X]
    assert [r["key"] for r in d["per_key"]] == [X]
    assert (d["before"]["cost_per"], d["after"]["cost_per"]) == (100.0, 40.0)


def test_v2h_units_are_never_converted(tmp_path: Path, capsys) -> None:
    # (ii) dollars only: the unit is usd, the after window has no value
    before = [node(t, "gave_up", "token_budget", usd=0.5) for t in BEFORE_TS]
    after = [node(t, "passed") for t in AFTER_TS]
    rc, d, _, _ = v2(sub(tmp_path, "a"), capsys, before + after, "cause:token_budget")
    assert rc == 0 and d["verdict"] == "improved" and d["unit"] == "usd"
    assert (d["before"]["cost_per"], d["after"]["cost_per"]) == (0.5, None)
    # (iii) no cost at all: unit none
    before = [ev("human", t, what="resume", actor="owner") for t in BEFORE_TS]
    after = [ev("plan_started", t, plan="p") for t in AFTER_TS]
    rc, d, _, _ = v2(sub(tmp_path, "b"), capsys, before + after, "human:resume")
    assert rc == 0 and d["verdict"] == "improved" and d["unit"] is None
    assert d["before"]["cost_per"] is None and d["after"]["exposure"] == 3
    # (iv) seconds before, dollars only after: unit s, the after cost is null, not converted
    before = [node(t, "gave_up", "timeout", 600.0) for t in BEFORE_TS]
    after = [node("2026-10-01T12:30:00Z", "gave_up", "timeout", usd=0.5)]
    rc, d, _, _ = v2(sub(tmp_path, "c"), capsys, before + after, "cause:timeout")
    assert rc == 1 and d["verdict"] == "recurred" and d["unit"] == "s"
    assert d["before"]["cost_per"] == 600.0 and d["after"]["cost_per"] is None


def test_v2i_an_event_at_the_close_second_is_in_neither_window(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, [*v2_events(), gate(C, 1000.0, 5)])
    assert rc == 0 and d["verdict"] == "improved" and d["excluded"]["at_close"] == 1
    assert (d["before"]["n"], d["after"]["n"]) == (3, 3)
    assert (d["before"]["cost_per"], d["after"]["cost_per"]) == (100.0, 40.0)


def test_v2j_an_undated_event_is_excluded_and_counted(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, [*v2_events(), gate("2026-10-01 13:00", 1000.0, 5)])
    assert rc == 0 and d["verdict"] == "improved" and d["excluded"]["undated"] == 1
    assert (d["before"]["n"], d["after"]["n"]) == (3, 3)


def test_v2_file_order_is_irrelevant(tmp_path: Path, capsys) -> None:
    evs = v2_events()
    rc, d, _, _ = v2(tmp_path, capsys, list(reversed(evs)))
    assert rc == 0 and d["change"]["delta"] == -60.0 and d["predicted"]["delta"] == -60.0


def test_v2_level_family_compares_the_latest_snapshot(tmp_path: Path, capsys) -> None:
    key = "unchecked:request_header"
    evs = [
        ev("finding_class", ts, signal_key=key, count=n, snapshot=True)
        for ts, n in (
            ("2026-10-01T10:00:00Z", 6),
            ("2026-10-01T11:00:00Z", 4),
            ("2026-10-01T13:00:00Z", 2),
        )
    ]
    rc, d, _, _ = v2(tmp_path, capsys, evs, key)
    assert rc == 0 and d["verdict"] == "improved" and d["family"] == "level"
    assert (d["before"]["frequency"], d["after"]["frequency"]) == (4, 2)
    assert d["change"]["delta"] == -2 and d["predicted"] is None
    (tmp_path / "state" / "events.jsonl").write_text(
        "".join(json.dumps({**e, "count": 4 if e["ts"] > C else e["count"]}) + "\n" for e in evs)
    )
    rc, d = verify(capsys, tmp_path / "proj", tmp_path / "state", "I-01")
    assert rc == 1 and d["verdict"] == "not_improved"


def test_v2_two_families_under_one_pattern_are_not_enough_data(tmp_path: Path, capsys) -> None:
    lint = [ev("finding_class", t, signal_key="lint:L012", count=1, cost_s=60.0) for t in AFTER_TS]
    rc, d, _, _ = v2(tmp_path, capsys, [*v2_events(), *lint], "*")
    assert rc == 1 and d["verdict"] == "not_enough_data" and d["family"] == "mixed"
    assert {r["key"]: r["family"] for r in d["per_key"]} == {
        X: "cost",
        "lint:L012": "occurrence",
    }


def test_v2_no_such_signal_in_the_log_is_not_enough_data(tmp_path: Path, capsys) -> None:
    rc, d, _, _ = v2(tmp_path, capsys, v2_events(), "cause:nothing")
    assert rc == 1 and d["verdict"] == "not_enough_data" and d["keys"] == []
    assert d["family"] is None


# ---------------------------------------------------------------- V3: ledger edge cases


def test_v3a_only_an_open_entry(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry(status="open")])
    rc, d = verify(capsys, project, state, "I-01")
    assert rc == 3 and d["refused"] is True and d["reason"] == "not_closed"
    assert d["format"] == 1 and d["command"] == "verify" and d["id"] == "I-01"


def test_v3b_closed_then_reopened(tmp_path: Path, capsys) -> None:
    project, state = make_env(
        tmp_path, ledger=[entry(), entry("2026-10-12T23:10:00Z", status="open")]
    )
    rc, d = verify(capsys, project, state, "I-01")
    assert rc == 3 and d["reason"] == "not_closed"


def test_v3c_unknown_ids(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry()])
    for ident in ("I-99", "imp-00000000"):
        rc, d = verify(capsys, project, state, ident)
        assert rc == 3 and d["reason"] == "unknown_id", ident


def test_v3d_no_commit_is_verified_with_a_warning(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry(commit=None)])
    rc, d = verify(capsys, project, state, "I-01")
    assert rc == 0 and d["change"]["delta"] == -61.01 and d["warnings"] == ["no commit recorded"]


def test_v3e_closed_twice_uses_the_earlier_close(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry(), entry("2026-10-12T23:12:00Z")])
    rc, d = verify(capsys, project, state, "I-01")
    assert rc == 0 and d["closing_point"]["ts"] == CLOSE
    assert d["change"]["delta"] == -61.01 and d["warnings"] == ["closed 2 times in a row"]


def test_v3f_closed_without_a_ts(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry(ts=None)])
    rc, d = verify(capsys, project, state, "I-01")
    assert rc == 3 and d["reason"] == "no_closing_point"
    rc, d = verify(capsys, project, state, "I-01", "--at", CLOSE)  # --at supplies it
    assert rc == 0 and d["closing_point"]["source"] == "--at"


def test_v3g_a_ledger_without_an_id_resolves_by_the_proposal_id(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry(id=None)])
    imp = analyze(
        [json.loads(ln) for ln in i3_lines()[:9]]
    )["proposals"][0]["id"]  # fmt: skip
    rc, d = verify(capsys, project, state, imp)
    assert rc == 0 and d["change"]["delta"] == -61.01 and d["keys"] == [X]
    assert d["entry"]["id"] is None


def test_v3_closed_reopened_closed_again_starts_the_window_after_the_earlier_close(
    tmp_path: Path, capsys
) -> None:
    ledger = [entry("2026-10-01T10:45:00Z", id="I-01"), entry("2026-10-01T11:15:00Z", "open"),
              entry(C)]  # fmt: skip
    project, state = make_env(tmp_path, v2_events(), ledger)
    rc, d = verify(capsys, project, state, "I-01")
    assert d["closing_point"]["ts"] == C
    assert d["before"]["n"] == 1  # only 11:00 lies after the earlier close (10:45)


def test_v3_an_imp_id_the_ledger_stores_is_found_by_the_ledger_id_first(
    tmp_path: Path, capsys
) -> None:
    project, state = make_env(tmp_path, ledger=[entry(id="imp-3e1cd7f9")])
    rc, d = verify(capsys, project, state, "imp-3e1cd7f9")
    assert rc == 0 and d["entry"]["id"] == "imp-3e1cd7f9"


def test_other_closes_between_the_windows_are_listed(tmp_path: Path, capsys) -> None:
    other = entry("2026-10-12T23:05:00Z", id="I-02", signal="cause:timeout")
    project, state = make_env(tmp_path, ledger=[entry(), other])
    rc, d = verify(capsys, project, state, "I-01")
    assert rc == 0
    assert d["context"]["other_closes"] == [
        {
            "id": "I-02",
            "signal": "cause:timeout",
            "ts": "2026-10-12T23:05:00Z",
            "commit": REAL_COMMIT,
        }  # fmt: skip
    ]


def test_verify_never_creates_the_state_dir_and_exit_two_on_unreadable(
    tmp_path: Path, capsys
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    missing = tmp_path / "nostate"
    rc, out, err = run(capsys, project, missing, "verify", "I-01")
    assert rc == 3 and "refused" in err and not missing.exists()
    assert main(["verify", "I-01", "--project", str(tmp_path / "nope")]) == 2
    state = tmp_path / "s"
    (state / "events.jsonl").mkdir(parents=True)
    assert main(["verify", "I-01", "--project", str(project), "--state", str(state)]) == 2


def test_canonical_ts_is_exactly_the_event_log_format() -> None:
    assert canonical_ts("2026-10-12T23:08:00Z")
    for bad in ("2026-10-12 23:08:00Z", "2026-10-12T23:08:00", "2026-10-12T23:8:00Z",
                "2026-13-12T23:08:00Z", "", None, 5):  # fmt: skip
        assert not canonical_ts(bad), bad


@pytest.mark.parametrize("cmd", ["verify", "draft"])
def test_help_lists_every_option(cmd: str, capsys) -> None:
    with pytest.raises(SystemExit) as ei:
        main([cmd, "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    opts = ["--project", "--state", "--json"] + (["--at"] if cmd == "verify" else ["--out"])
    assert all(o in out for o in opts)
