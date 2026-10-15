"""The improvement event log (lattice/improve/events.py): append, read, a torn last line,
unknown fields and kinds kept, a write failure is a warning, imports are idempotent."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from dogfood.improve_hardening_probes import SETUP, field_build
from lattice.improve import events as events_mod
from lattice.improve.events import (
    MAX_LINE_BYTES,
    SCHEMA,
    EventLog,
    append_lines,
    event_id,
    read_events,
)


def test_append_and_read_round_trip(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "s" / "events.jsonl", "notes")
    a = log.append("gate_run", gate="level", seconds=12.5, layer_seconds={"X": 10.0}, sha=None)
    b = log.append("human", what="approve", actor="owner", plan="p1")
    assert a is not None and b is not None
    assert a["v"] == SCHEMA and a["project"] == "notes" and a["kind"] == "gate_run"
    assert "sha" not in a, "None fields are left out"
    res = read_events(tmp_path / "s" / "events.jsonl")
    assert res.warnings == []
    assert [e["kind"] for e in res.events] == ["gate_run", "human"]
    assert res.events[0]["layer_seconds"] == {"X": 10.0} and res.events[0]["line"] == 1
    assert res.events[1]["what"] == "approve" and res.events[1]["id"].startswith("e-")
    # one object per line, newline-terminated
    raw = (tmp_path / "s" / "events.jsonl").read_text()
    assert raw.endswith("\n") and len(raw.splitlines()) == 2


def test_a_torn_last_line_is_skipped_with_a_warning(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    log = EventLog(p, "notes")
    log.append("human", what="run", actor="owner")
    log.append("human", what="stop", actor="owner")
    with p.open("ab") as f:
        f.write(b'{"v": 1, "kind": "gate_run", "seconds": 4')  # a crash mid-write
    res = read_events(p)
    assert [e["what"] for e in res.events] == ["run", "stop"]
    assert len(res.warnings) == 1 and "torn last line" in res.warnings[0]
    # appending after the torn line still works: the torn bytes stay one bad line
    log.append("human", what="land", actor="owner")
    res = read_events(p)
    assert [e.get("what") for e in res.events][:2] == ["run", "stop"]


def test_bad_middle_lines_unknown_fields_and_kinds_are_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    lines = [
        json.dumps({"v": 1, "kind": "gate_run", "seconds": 1, "future_field": {"a": 1}}),
        "not json at all",
        json.dumps(["a list"]),
        json.dumps({"v": 9, "kind": "a_kind_from_the_future", "x": 1}),
        "",
    ]
    p.write_text("\n".join(lines) + "\n")
    res = read_events(p)
    assert [e["kind"] for e in res.events] == ["gate_run", "a_kind_from_the_future"]
    assert res.events[0]["future_field"] == {"a": 1}
    assert len(res.warnings) == 2 and all("not an event" in w for w in res.warnings)


def test_a_missing_log_is_empty_and_no_store_records_nothing(tmp_path: Path) -> None:
    assert read_events(tmp_path / "nope.jsonl").events == []
    log = EventLog(None, "notes")
    assert not log.enabled and log.append("human", what="run") is None


def test_a_write_failure_is_a_warning_never_an_exception(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").mkdir()  # the path is a directory: open fails
    seen: list[str] = []
    log = EventLog(tmp_path / "events.jsonl", "notes", log=seen.append)
    assert log.append("gate_run", seconds=1.0) is None
    assert log.warnings and seen and "could not append" in seen[0]


def test_extend_skips_known_ids(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    log = EventLog(p, "notes")
    evs = [
        {"kind": "human", "id": event_id("import", "p2", 1), "what": "approve"},
        {"kind": "human", "id": event_id("import", "p2", 2), "what": "resume"},
    ]
    assert log.extend(evs) == 2
    known = {e["id"] for e in read_events(p).events}
    assert log.extend(evs, known) == 0
    assert len(read_events(p).events) == 2
    assert event_id("a", 1) == event_id("a", 1) != event_id("a", 2)
    assert event_id() != event_id()


# ---------------------------------------------------------------- hardening (G10): the lock,
# the torn tail, the line cap, concurrent imports, and the `setup` field of a gate run
# (docs/results/2026-10-15-improve-hardening-prereg.md)

ROOT = Path(__file__).resolve().parents[2]
SPIKE3 = ROOT / "docs" / "results" / "data" / "2026-10-12-spike3"


def _bytes(path: Path) -> bytes:
    return path.read_bytes()


def _line(ev: dict[str, Any]) -> bytes:
    return (json.dumps(ev, separators=(",", ":")) + "\n").encode()


def _hold(path: Path, seconds: float, line: bytes | None = None) -> threading.Thread:
    """Another open file description holds the log's flock for `seconds` (it then writes
    `line`, when given, and lets go); answers once it holds it."""
    held = threading.Event()

    def run() -> None:
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            held.set()
            time.sleep(seconds)
            if line is not None:
                os.write(fd, line)
        finally:
            os.close(fd)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert held.wait(5)
    return t


def test_e_heal_a_torn_tail_does_not_swallow_the_next_event(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    log = EventLog(p, "notes")
    for w in ("a", "b", "c"):
        log.append("human", what=w, actor="owner")
    before = _bytes(p)
    full = json.dumps({"v": 1, "kind": "gate_run", "id": "e-torn", "seconds": 4}).encode()
    with p.open("ab") as f:
        f.write(full[:40])  # a crash mid-write: no newline
    torn = _bytes(p)
    ev = log.append("human", what="d", actor="owner")
    assert ev is not None
    line = (json.dumps(ev, separators=(",", ":")) + "\n").encode()
    assert _bytes(p) == torn + b"\n" + line
    res = read_events(p)
    assert [e["what"] for e in res.events if e["kind"] == "human"] == ["a", "b", "c", "d"]
    assert len(res.events) == 4 and len(res.warnings) == 1
    assert res.warnings[0] == "events.jsonl line 4: not an event (skipped)"
    # healthy tail: a second append adds exactly its line (no blank line)
    size = len(_bytes(p))
    ev2 = log.append("human", what="e", actor="owner")
    assert ev2 is not None
    line2 = (json.dumps(ev2, separators=(",", ":")) + "\n").encode()
    assert _bytes(p)[size:] == line2
    assert before in _bytes(p)


def test_e_short_a_short_write_is_completed_and_a_failure_heals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "events.jsonl"
    log = EventLog(p, "notes")
    real = os.write
    calls = {"n": 0}

    def short(fd: int, data: Any) -> int:
        calls["n"] += 1
        return real(fd, bytes(data[:7]))

    with monkeypatch.context() as m:
        m.setattr(os, "write", short)
        ev = log.append("human", what="long", actor="owner", note="x" * 60)
    assert ev is not None and calls["n"] > 5
    res = read_events(p)
    assert res.warnings == [] and res.events[0]["note"] == "x" * 60
    assert _bytes(p).endswith(b"\n") and _bytes(p).count(b"\n") == 1

    n = {"n": 0}

    def fail_third(fd: int, data: Any) -> int:
        n["n"] += 1
        if n["n"] == 3:
            raise OSError(errno.EIO, "disk")
        return real(fd, bytes(data[:7]))

    with monkeypatch.context() as m:
        m.setattr(os, "write", fail_third)
        assert log.append("human", what="lost", actor="owner", note="y" * 60) is None
    assert log.warnings and "could not append a human event" in log.warnings[-1]
    assert not _bytes(p).endswith(b"\n")  # a fragment of the lost event
    assert log.append("human", what="kept", actor="owner") is not None
    res = read_events(p)
    assert [e["what"] for e in res.events] == ["long", "kept"]
    assert len(res.warnings) == 1 and "not an event" in res.warnings[0]


def test_e_cap_a_line_over_65536_bytes_is_dropped(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    log = EventLog(p, "notes")
    fixed = {"id": "e-cap", "ts": "2026-10-15T00:00:00Z"}
    base = log._line("human", {**fixed, "pad": ""})[1]
    assert base is not None

    def pad(total: int) -> dict[str, Any]:
        return {**fixed, "pad": "p" * (total - len(base))}

    assert log.append("human", **pad(MAX_LINE_BYTES)) is not None
    assert len(_bytes(p)) == MAX_LINE_BYTES  # exactly the cap, newline included: written
    assert log.append("human", **pad(MAX_LINE_BYTES + 1)) is None
    assert len(_bytes(p)) == MAX_LINE_BYTES
    assert log.warnings == [
        f"events: a human event of {MAX_LINE_BYTES + 1} bytes is over {MAX_LINE_BYTES}; not written"
    ]


def test_e_lock_an_append_waits_for_the_holder_and_comes_after_it(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    holder = _line({"v": 1, "kind": "human", "id": "e-holder", "what": "first"})
    t = _hold(p, 0.5, holder)
    time.sleep(0.1)
    log = EventLog(p, "notes")
    t0 = time.monotonic()
    ev = log.append("human", what="second", actor="owner")
    took = time.monotonic() - t0
    t.join(5)
    assert ev is not None and log.warnings == []
    assert 0.25 < took < 1.5, took
    assert [e["what"] for e in read_events(p).events] == ["first", "second"]


def test_e_lock_a_timeout_writes_without_the_lock_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events_mod, "LOCK_TIMEOUT_S", 0.2)
    p = tmp_path / "events.jsonl"
    holder = _line({"v": 1, "kind": "human", "id": "e-holder", "what": "holder"})
    t = _hold(p, 1.5, holder)
    seen: list[str] = []
    log = EventLog(p, "notes", log=seen.append)
    t0 = time.monotonic()
    ev = log.append("human", what="mine", actor="owner")
    took = time.monotonic() - t0
    assert ev is not None, "a timeout never drops the event"
    assert 0.15 < took < 1.0, f"the append must not wait for the holder: {took}"
    assert log.warnings == [f"events: waited 0.2 s for the lock on {p}; appended without it"]
    assert seen == log.warnings
    t.join(5)
    assert [e["what"] for e in read_events(p).events] == ["mine", "holder"]


def test_e_lock_extend_on_a_timeout_writes_nothing_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events_mod, "LOCK_TIMEOUT_S", 0.2)
    p = tmp_path / "events.jsonl"
    t = _hold(p, 1.0)
    log = EventLog(p, "notes")
    t0 = time.monotonic()
    n = log.extend([{"kind": "human", "id": event_id("i", 1), "what": "approve"}])
    assert time.monotonic() - t0 < 0.9
    t.join(5)
    assert n == 0 and read_events(p).events == []
    assert len(log.warnings) == 1 and "waited 0.2 s for the lock" in log.warnings[0]


def test_a_filesystem_without_flock_appends_with_one_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_flock(fd: int, op: int) -> None:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(events_mod.fcntl, "flock", no_flock)
    t0 = time.monotonic()
    log = EventLog(tmp_path / "events.jsonl", "notes")
    assert log.append("human", what="a", actor="owner") is not None
    assert log.append("human", what="b", actor="owner") is not None
    assert time.monotonic() - t0 < 1.0, "no polling for a lock that cannot be had"
    assert len(log.warnings) == 1 and log.warnings[0].startswith("events: no file lock on ")
    assert [e["what"] for e in read_events(tmp_path / "events.jsonl").events] == ["a", "b"]


def test_append_lines_takes_lines_or_a_callable_and_heals(tmp_path: Path) -> None:
    p = tmp_path / "x" / "log.jsonl"
    assert append_lines(p, ["a", b"b\n"]) == (2, [])
    assert _bytes(p) == b"a\nb\n"
    seen: list[Path] = []

    def pick(path: Path) -> list[str]:
        seen.append(path)
        return ["c"]

    assert append_lines(p, pick) == (1, [])
    assert seen == [p] and _bytes(p) == b"a\nb\nc\n"
    with p.open("ab") as f:
        f.write(b"torn")
    assert append_lines(p, ["d"]) == (1, [])
    assert _bytes(p) == b"a\nb\nc\ntorn\nd\n"
    assert append_lines(p, []) == (0, [])
    assert append_lines(tmp_path, ["x"])[0] == 0  # a directory: a warning, no exception


_IMPORT_WORKER = """
import sys, time
from pathlib import Path
from lattice.improve.events import EventLog, event_id
path, go, n = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
evs = [{"kind": "human", "id": event_id("import", "p2", i), "ts": "2026-10-01T00:00:00Z",
        "what": "approve", "i": i} for i in range(n)]
while not go.exists():
    time.sleep(0.0005)
print(EventLog(path, "notes").extend(evs))
"""


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}


def test_e_import_concurrent_imports_write_each_event_once(tmp_path: Path) -> None:
    p, go = tmp_path / "events.jsonl", tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _IMPORT_WORKER, str(p), str(go), "60"],
            stdout=subprocess.PIPE,
            text=True,
            env=_env(),
        )  # fmt: skip
        for _ in range(4)
    ]
    time.sleep(1.0)
    go.write_text("go")
    written = [int(pr.communicate(timeout=60)[0].strip()) for pr in procs]
    evs = read_events(p).events
    assert sorted(written) == [0, 0, 0, 60], written
    assert len(evs) == 60 and len({e["id"] for e in evs}) == 60
    assert read_events(p).warnings == []


def test_e_import_extend_rereads_the_ids_under_the_lock(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    evs = [{"kind": "human", "id": event_id("import", "p2", i), "what": "approve"} for i in (1, 2)]
    assert EventLog(p, "notes").extend(evs) == 2
    # another EventLog (another process, in effect) with an empty `known`: nothing is added
    assert EventLog(p, "notes").extend(evs) == 0
    assert EventLog(p, "notes").extend([*evs, {"kind": "human", "id": "e-new"}]) == 1
    assert [e["id"] for e in read_events(p).events][-1] == "e-new"
    assert len(read_events(p).events) == 3


_STRESS_WORKER = """
import sys, time
from pathlib import Path
from lattice.improve.events import EventLog
path, go, w, n = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
log = EventLog(path, "notes")
while not go.exists():
    time.sleep(0.0005)
for seq in range(n):
    log.append("finding_class", signal_key="D:x", count=1, w=w, seq=seq,
               pad="p" * (200, 600, 4200, 9000)[seq % 4])
"""


def test_e_stress_processes_append_whole_lines_in_program_order(tmp_path: Path) -> None:
    p, go = tmp_path / "events.jsonl", tmp_path / "go"
    n, k = 40, 4
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _STRESS_WORKER, str(p), str(go), str(w), str(n)], env=_env()
        )  # fmt: skip
        for w in range(k)
    ]
    time.sleep(1.0)
    go.write_text("go")
    assert [pr.wait(timeout=120) for pr in procs] == [0] * k
    res = read_events(p)
    assert res.warnings == [] and len(res.events) == n * k
    assert len({e["id"] for e in res.events}) == n * k
    for w in range(k):
        assert [e["seq"] for e in res.events if e["w"] == w] == list(range(n))


# ---- the field: one real Rebuilder build (the seed, a stub agent, scripted check reports);
# the rig is the probe runner's (dogfood/improve_hardening_probes.py)


def _keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_f1_setup_is_copied_in_order_to_every_gate_run_of_the_build(tmp_path: Path) -> None:
    timings = {"total_s": 1.0, "layers": {"C": 0.1}, "setup": SETUP}
    rec, evs = field_build(tmp_path, [(timings, True)])
    assert [(e["gate"], e["round"]) for e in evs] == [
        ("build_baseline", 0),
        ("build", 1),
        ("build", 2),
    ]
    for e in evs:
        assert e["setup"] == SETUP and list(e["setup"]) == list(SETUP)
        assert type(e["setup"]["db_clones"]) is int
        assert e["layer_seconds"] == {"C": 0.1}
    assert "setup" not in _keys(rec), "F7: the build record does not carry it"


def test_f2_an_empty_setup_is_left_out(tmp_path: Path) -> None:
    _, evs = field_build(tmp_path, [({"total_s": 1.0, "layers": {"C": 0.1}, "setup": {}}, True)])
    assert evs and all("setup" not in e and e["layer_seconds"] == {"C": 0.1} for e in evs)


def test_f3_no_timings_and_f4_no_report_written_leave_no_field(tmp_path: Path) -> None:
    _, evs = field_build(tmp_path / "a", [(None, True)])
    assert evs and not any("setup" in e or "layer_seconds" in e for e in evs)
    (tmp_path / "b").mkdir()
    _, evs = field_build(tmp_path / "b", [({"layers": {"C": 0.1}, "setup": SETUP}, False)])
    assert evs and not any("setup" in e or "layer_seconds" in e for e in evs)


def test_f5_only_finite_numbers_are_kept(tmp_path: Path) -> None:
    bad = {"database_server_s": 2.5, "flag": True, "note": "x", "big": float("inf"),
           "nan": float("nan"), "none": None, "nested": {"a": 1}}  # fmt: skip
    _, evs = field_build(tmp_path, [({"layers": {"C": 0.1}, "setup": bad}, True)])
    assert evs and all(e["setup"] == {"database_server_s": 2.5} for e in evs)


def test_f6_only_the_gate_that_wrote_the_report_carries_the_setup(tmp_path: Path) -> None:
    timings = {"layers": {"C": 0.1}, "setup": SETUP}
    _, evs = field_build(tmp_path, [(timings, False), (timings, True)])
    assert {e["command"] for e in evs} == {"./gate.sh", "./gate1.sh"}
    assert all(("setup" in e) == (e["command"] == "./gate1.sh") for e in evs)


def test_f8_setup_is_the_only_difference_to_a_build_without_it(tmp_path: Path) -> None:
    with_ = {"total_s": 1.0, "layers": {"C": 0.1}, "setup": SETUP}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, a = field_build(tmp_path / "a", [(with_, True)])
    _, b = field_build(tmp_path / "b", [({**with_, "setup": {}}, True)])

    def mask(e: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in e.items() if k not in ("setup", "id", "ts", "seconds", "line")}

    assert [mask(e) for e in a] == [mask(e) for e in b]


def test_the_ranker_ignores_setup_and_readers_are_unchanged() -> None:
    """E-compat, forward: the spike-3 I-3 log with a `setup` on every gate run gives the
    unmodified log's report, text and JSON."""
    from lattice.improve.analyze import analyze, report_text

    evs = read_events(SPIKE3 / "i3-events.jsonl").events
    gates = [e for e in evs if e["kind"] == "gate_run"]
    assert gates
    plus = [{**e, "setup": dict(SETUP)} if e["kind"] == "gate_run" else dict(e) for e in evs]
    for top in (5, 10):
        a, b = analyze(evs, [], top), analyze(plus, [], top)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        assert report_text(a) == report_text(b)
    assert not any("setup" in p["signal_key"] for p in analyze(plus, [], 50)["proposals"])
