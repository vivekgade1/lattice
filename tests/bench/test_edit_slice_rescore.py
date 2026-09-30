"""``bench/edit_slice/rescore.py``: re-scoring saved rounds under the current oracle
(N4, benchmark hygiene: ``docs/superpowers/specs/2026-09-27-lattice-surface-design.md``
§5).

* a saved arm P candidate that only ever failed t14's now-excluded boundary cases is
  rescored correct;
* a saved candidate with a genuine trap failure stays silent, that failure named;
* a record naming a ticket outside ``--tickets``, with no artefacts on disk, or whose
  final round saved no candidate files, passes through unchanged;
* the CLI writes every record (rescored or not) to ``--out``.
"""

from __future__ import annotations

import json

from bench.edit_slice import rescore, run
from bench.edit_slice.reference import reference_files
from lattice.llm.client import ScriptedGenerator


def _p_answer(ticket_id: str, **replace: str) -> str:
    from bench.edit_slice import prompts

    files = {prompts.APP_PREFIX + rel: src for rel, src in reference_files(ticket_id).items()}
    for old, new in replace.items():
        files = {n: s.replace(old, new) for n, s in files.items()}
    return prompts.render_files(files)


def _run_record(tmp_path, ticket_id: str, answer: str) -> dict:
    """A real arm P run's artefacts on disk, as the harness's own JSONL record (dict)."""
    record = run.run_arm(
        "P", ticket_id, ScriptedGenerator([answer]), model="m", rep=0, artefact_root=tmp_path
    )
    return json.loads(record.to_json())


def test_a_candidate_that_only_failed_excluded_cases_is_rescored_correct(tmp_path):
    # current_password with no length bound: everywhere the ticket touches behaves as
    # specified, but the two boundary probes the oracle no longer scores (below_min,
    # above_max) would have differed under the old oracle -- exactly round 3's failure.
    answer = _p_answer(
        "t14_email_change",
        **{"current_password: str = Field(min_length=8, max_length=128)": "current_password: str"},
    )
    record = _run_record(tmp_path, "t14_email_change", answer)
    assert record["outcome"] == "correct"  # the harness itself already uses the fixed oracle

    # simulate a stale round-3 record: the old oracle's verdict on this same candidate
    stale = dict(record, outcome="silent", failed_cases=[
        "change_email.boundary.current_password.below_min",
        "change_email.boundary.current_password.above_max",
    ])  # fmt: skip
    new, note = rescore.rescore_record(stale, {"t14_email_change"})
    assert note == "silent -> correct"
    assert new["outcome"] == "correct"
    assert new["failed_cases"] == []


def test_a_candidate_with_a_genuine_trap_failure_stays_silent(tmp_path):
    # the caller's own current email is skipped from the "already taken" check by id;
    # dropping that exclusion makes changing your email to itself a spurious 409
    answer = _p_answer(
        "t14_email_change",
        **{"if existing_user and existing_user.id != current_user.id:": "if existing_user:"},
    )
    record = _run_record(tmp_path, "t14_email_change", answer)
    assert record["outcome"] == "silent"
    assert "t14.email_same_as_own" in record["failed_cases"]

    stale = dict(record, failed_cases=[*record["failed_cases"]])
    new, note = rescore.rescore_record(stale, {"t14_email_change"})
    assert note == "silent (unchanged)"
    assert new["outcome"] == "silent"
    assert "t14.email_same_as_own" in new["failed_cases"]


def test_a_ticket_outside_the_selection_passes_through_unchanged():
    record = {"ticket": "t1_search", "arm": "P", "rep": 0, "outcome": "silent",
             "artefacts": "/does/not/matter"}  # fmt: skip
    new, note = rescore.rescore_record(record, {"t14_email_change"})
    assert new == record and note == "not selected"


def test_a_record_with_no_artefacts_passes_through_unchanged():
    record = {"ticket": "t17_page_size_preference", "arm": "L", "rep": 0, "outcome": "silent",
             "artefacts": None}  # fmt: skip
    new, note = rescore.rescore_record(record, {"t17_page_size_preference"})
    assert new == record and note == "no artefacts saved: unchanged"


def test_a_final_round_with_no_saved_candidate_passes_through_unchanged(tmp_path):
    rep_dir = tmp_path / "rep0"
    (rep_dir / "round1").mkdir(parents=True)
    (rep_dir / "round1" / "round.json").write_text("{}")
    record = {"ticket": "t14_email_change", "arm": "P", "rep": 0, "outcome": "loud",
             "artefacts": str(rep_dir)}  # fmt: skip
    new, note = rescore.rescore_record(record, {"t14_email_change"})
    assert new == record and note == "final round has no candidate: unchanged"


def test_the_final_round_is_the_highest_numbered_one(tmp_path):
    """A repaired run's earlier rounds are ignored: only the last one was scored."""
    rep_dir = tmp_path / "rep0"
    for k in (1, 2):
        (rep_dir / f"round{k}").mkdir(parents=True)
    files_dir = rep_dir / "round10" / "files"  # lexicographically before round2, not numerically
    files_dir.mkdir(parents=True)
    assert rescore._final_round_files(rep_dir) == files_dir


def test_cli_writes_every_record_to_out(tmp_path, capsys):
    answer = _p_answer(
        "t14_email_change",
        **{"current_password: str = Field(min_length=8, max_length=128)": "current_password: str"},
    )
    record = _run_record(tmp_path, "t14_email_change", answer)
    stale = dict(record, outcome="silent", failed_cases=["change_email.boundary."
                                                         "current_password.below_min"])  # fmt: skip
    other = {"ticket": "t1_search", "arm": "P", "rep": 0, "outcome": "correct", "artefacts": None}
    jsonl = tmp_path / "in.jsonl"
    jsonl.write_text(json.dumps(stale) + "\n" + json.dumps(other) + "\n")
    out = tmp_path / "out.jsonl"
    rc = rescore.main([str(jsonl), "--tickets", "t14_email_change", "--out", str(out)])
    assert rc == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["ticket"] == "t14_email_change" and lines[0]["outcome"] == "correct"
    assert lines[1] == other  # not selected: passed through byte for byte
    err = capsys.readouterr().err
    assert "1 record(s) considered, 1 outcome(s) changed" in err


def test_default_tickets_are_the_ones_the_oracle_currently_excludes_cases_for():
    from bench.edit_slice.oracle import EXCLUDED_CASES

    assert set(rescore._parser().parse_args(["x.jsonl"]).tickets.split(",")) == set(
        EXCLUDED_CASES
    )


def test_arm_l_candidates_are_rebuilt_through_the_emitter(tmp_path):
    """A saved arm L candidate (programs + profile.py) is re-emitted, not loaded as
    Python files (t14/t17 have no Lattice reference; t1_search does, and every
    template ticket shares the same rebuild path)."""
    answer = run.reference_answer("L", "t1_search")
    assert answer is not None
    record = run.run_arm(
        "L", "t1_search", ScriptedGenerator([answer]), model="m", rep=0, artefact_root=tmp_path
    )
    record = json.loads(record.to_json())
    assert record["outcome"] == "correct" and record["artefacts"]

    stale = dict(record, outcome="silent", failed_cases=["read_items.happy"])
    new, note = rescore.rescore_record(stale, {"t1_search"})
    assert note == "silent -> correct"
    assert new["outcome"] == "correct"
    assert new["failed_cases"] == []


def test_arm_q_candidates_are_rebuilt_as_python(tmp_path):
    """Arm Q edits the Python app (with pycheck's findings); only L / L+ are Lattice."""
    answer = _p_answer("t14_email_change")
    record = _run_record(tmp_path, "t14_email_change", answer)
    stale = dict(record, arm="Q", outcome="silent", failed_cases=["x"])
    new, note = rescore.rescore_record(stale, {"t14_email_change"})
    assert note == "silent -> correct" and new["arm"] == "Q"
    assert rescore.PYTHON_ARMS == {"P", "Q", "Q+"}
