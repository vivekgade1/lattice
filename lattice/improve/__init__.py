"""Lattice improves itself from use (spike 3): an event log of usage signals
(:mod:`lattice.improve.events`), a ranker that turns them into proposals by frequency x cost
(:mod:`lattice.improve.analyze`, the I-R method, no model), and an importer for existing plan
records (:mod:`lattice.improve.importer`). Self-proposing, human-merging: nothing here changes
code, merges or schedules anything. CLI: ``python -m lattice.improve`` (``lattice improve``)."""

from lattice.improve.analyze import BLIND_SPOTS, METHOD, PROTECTED, analyze, analyze_dir
from lattice.improve.events import CAUSES, EVENTS_FILE, EventLog, read_events

__all__ = [
    "BLIND_SPOTS",
    "CAUSES",
    "EVENTS_FILE",
    "METHOD",
    "PROTECTED",
    "EventLog",
    "analyze",
    "analyze_dir",
    "read_events",
]
