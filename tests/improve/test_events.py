"""The improvement event log (lattice/improve/events.py): append, read, a torn last line,
unknown fields and kinds kept, a write failure is a warning, imports are idempotent."""

from __future__ import annotations

import json
from pathlib import Path

from lattice.improve.events import SCHEMA, EventLog, event_id, read_events


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
