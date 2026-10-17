"""Rationale notes (lattice.spec.rationale): files, anchors, states, metrics, validation,
the owner's approval and its guard, and drafting with a stub generator (never a real model)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.spec import rationale as rt
from lattice.spec.rationale import NotesError
from lattice.spec.rules import Trace, extract_rules
from tests.spec import racy

MD = """\
# Runs

Intro line that states no behaviour.

- A config has at most one live run.
- Runs are fast.
- A cancelled run is never executed.
- The worker drains the oldest queued run first.
"""
TRACE_RULES = extract_rules(MD)  # R1 (intro), R2..R5


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def anchor(text: str) -> str:
    return sha(" ".join(text.split()))


def appr_sha(why: str, tradeoff: str | None, owner: str | None) -> str:
    raw = json.dumps(
        {"why": why, "tradeoff": tradeoff, "owner": owner},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha(raw)


def note(
    rule: str,
    text: str,
    why: str = "Because.",
    tradeoff: str | None = "Costs.",
    owner: str = "ann",
    by: str = "ann",
    status: str = "approved",
    author: str = "owner",
) -> dict[str, Any]:
    approved = status == "approved"
    return {
        "rule": rule,
        "anchor": anchor(text),
        "why": why,
        "tradeoff": tradeoff,
        "owner": owner if approved else None,
        "status": status,
        "author": author,
        "approved_by": by if approved else None,
        "approved_sha": appr_sha(why, tradeoff, owner) if approved else None,
        "basis": [],
        "refs": [],
    }


def rfile(notes: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"schema": "lattice.rationale/1", "source": "REQUIREMENTS.md", "notes": notes}
    )


def text_of(rid: str) -> str:
    return next(r.text for r in TRACE_RULES if r.id == rid)


@pytest.fixture
def trace() -> Trace:
    return Trace("REQUIREMENTS.md", TRACE_RULES, context=["R1"])


@pytest.fixture
def proj(tmp_path: Path, trace: Trace) -> Path:
    (tmp_path / "REQUIREMENTS.md").write_text(MD)
    trace.save(tmp_path / "spec.trace.json")
    return tmp_path


def write(proj: Path, name: str, text: str) -> Path:
    p = proj / name
    p.write_text(text)
    return p


def load(proj: Path, trace: Trace) -> rt.RationaleSet | None:
    return rt.load_set(trace, rt.notes_paths(proj / "spec.trace.json"))


# ---- text, anchors, hashes ---------------------------------------------------------------


def test_norm_text_anchor_and_approved_sha_follow_d2() -> None:
    assert rt.norm_text("  a \t b\n c  ") == "a b c"
    assert rt.anchor_of("A  b\nc") == anchor("A b c")
    assert rt.anchor_of("A b") != rt.anchor_of("a b")  # case is kept
    assert rt.approved_sha("w", None, "o") == appr_sha("w", None, "o")
    assert rt.approved_sha("w", "t", "o") != rt.approved_sha("w", None, "o")


@pytest.mark.parametrize(
    ("n", "d", "want"),
    [
        (0, 146, 0.0),
        (5, 146, 3.4),
        (6, 146, 4.1),
        (5, 147, 3.4),
        (1, 8, 12.5),
        (1, 3, 33.3),
        (2, 3, 66.7),
        (1, 0, 0.0),
        (3, 8, 37.5),
        (1, 16, 6.3),
    ],
)
def test_percentages_round_half_up_in_integers(n: int, d: int, want: float) -> None:
    assert rt.pct(n, d) == want
    assert rt.ratio(n, d) == {"n": n, "d": d, "pct": want}


# ---- where the files live (D1) -----------------------------------------------------------


def test_notes_names_come_from_the_trace_source_stem() -> None:
    assert rt.notes_names("REQUIREMENTS.md") == (
        "REQUIREMENTS.rationale.json",
        "REQUIREMENTS.rationale.proposed.json",
        "REQUIREMENTS.glossary.json",
        "REQUIREMENTS.glossary.proposed.json",
    )
    assert rt.notes_names("docs/spec.v2.md")[0] == "spec.v2.rationale.json"


def test_paths_are_beside_the_trace_and_overrides_imply_the_proposed_sibling(proj: Path) -> None:
    p = rt.notes_paths(proj / "spec.trace.json")
    assert p is not None and p.rationale == proj / "REQUIREMENTS.rationale.json"
    assert p.glossary_proposed == proj / "REQUIREMENTS.glossary.proposed.json"
    assert not p.any and p.existing() == []
    q = rt.notes_paths(proj / "spec.trace.json", rationale=proj / "x" / "why.json")
    assert q is not None
    assert q.rationale == proj / "x" / "why.json"
    assert q.rationale_proposed == proj / "x" / "why.proposed.json"
    assert q.glossary == proj / "REQUIREMENTS.glossary.json"  # the other still derives


def test_no_trace_or_no_source_means_no_paths(tmp_path: Path) -> None:
    assert rt.notes_paths(tmp_path / "missing.trace.json") is None
    (tmp_path / "t.json").write_text("{}")
    assert rt.notes_paths(tmp_path / "t.json") is None
    (tmp_path / "bad.json").write_text("not json")
    assert rt.trace_source(tmp_path / "bad.json") is None
    assert rt.notes_paths(tmp_path / "t.json", rationale=tmp_path / "r.json") is not None


def test_trace_source_cache_follows_the_file(tmp_path: Path) -> None:
    t = tmp_path / "t.json"
    t.write_text(json.dumps({"source": "A.md"}))
    assert rt.trace_source(t) == "A.md"
    t.write_text(json.dumps({"source": "B_longer.md"}))  # a different size: re-read
    assert rt.trace_source(t) == "B_longer.md"


def test_no_file_means_no_set(proj: Path, trace: Trace) -> None:
    assert load(proj, trace) is None
    es = rt.empty_set(trace)
    m = es.metrics()
    assert m["file"] is None and m["RC"] == {"n": 0, "d": 4, "pct": 0.0}  # R1 is context


# ---- states (D3) -------------------------------------------------------------------------


def test_current_note_resolves_to_its_rule(proj: Path, trace: Trace) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R2", text_of("R2"))]))
    rs = load(proj, trace)
    assert rs is not None
    (r,) = rs.resolved
    assert (r.state, r.rule) == ("current", "R2")
    assert rs.rule_state("R2") == "current" and rs.status("R2") == "approved"
    assert rs.approved_for("R2") is r and rs.approved_for("R3") is None
    assert rs.status("R3") == "none"


def test_moved_note_follows_its_anchor_to_the_new_id(proj: Path, trace: Trace) -> None:
    # the note was written for R9, whose wording now sits at R2
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R9", text_of("R2"))]))
    rs = load(proj, trace)
    (r,) = rs.resolved
    assert (r.state, r.rule, r.matches) == ("moved", "R2", ("R2",))
    assert rs.status("R2") == "approved"  # counted under the new id
    assert rs.status("R9") == "none"  # and not under the old one


def test_stale_when_the_wording_changed_or_the_rule_is_gone(proj: Path, trace: Trace) -> None:
    write(
        proj,
        "REQUIREMENTS.rationale.json",
        rfile(
            [
                note("R2", "A config has at most two live runs."),
                note("R9", "A rule that was deleted."),
            ]
        ),
    )
    rs = load(proj, trace)
    assert [r.state for r in rs.resolved] == ["stale", "stale"]
    assert rs.status("R2") == "stale" and rs.approved_for("R2") is None
    assert rs.metrics()["RC"]["n"] == 0 and rs.metrics()["RN"]["stale"] == 2


def test_duplicate_rule_texts_make_a_shifted_note_ambiguous() -> None:
    rules = extract_rules("# A\n\n- Same text.\n- Other.\n- Same text.\n- Fourth.\n")
    t = Trace("REQUIREMENTS.md", rules)
    n = rt.Note(
        "R9",
        anchor("Same text."),
        "w",
        None,
        "o",
        "approved",
        "owner",
        "o",
        appr_sha("w", None, "o"),
    )
    (r,) = rt.resolve(t, [n])
    assert (r.state, r.matches) == ("ambiguous", ("R1", "R3"))
    # a note at an id that still holds one of the two is current, not ambiguous
    n1 = rt.Note(
        "R3",
        anchor("Same text."),
        "w",
        None,
        "o",
        "approved",
        "owner",
        "o",
        appr_sha("w", None, "o"),
    )
    assert rt.resolve(t, [n1])[0].state == "current"


def test_edited_wins_over_every_other_state(proj: Path, trace: Trace) -> None:
    n = note("R2", text_of("R2"))
    n["why"] = "Edited by hand after approval."
    stale = note("R3", "Not the current wording.")
    stale["why"] = "Also edited."
    write(proj, "REQUIREMENTS.rationale.json", rfile([n, stale]))
    rs = load(proj, trace)
    assert [r.state for r in rs.resolved] == ["edited", "edited"]
    assert rs.approved_for("R2") is None and rs.status("R2") == "edited"
    assert rs.metrics()["RC"]["n"] == 0


def test_rule_state_prefers_the_best_note_then_a_proposal(proj: Path, trace: Trace) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R2", "old wording")]))
    write(
        proj,
        "REQUIREMENTS.rationale.proposed.json",
        rfile(
            [
                note("R2", text_of("R2"), status="proposed", author="hand"),
                note("R3", text_of("R3"), status="proposed", author="hand"),
                note("R4", text_of("R4"), status="rejected", author="hand"),
            ]
        ),
    )
    rs = load(proj, trace)
    assert rs.rule_state("R2") == "stale"  # the stale note outranks the proposal
    assert rs.rule_state("R3") == "proposed_only"
    assert rs.rule_state("R4") == "none"  # a rejected proposal is nothing
    assert rs.metrics()["RP"] == 1 and rs.metrics()["proposals"] == 2


def test_metrics_exclude_context_rules_from_rc_but_not_rc_all(proj: Path, trace: Trace) -> None:
    write(
        proj,
        "REQUIREMENTS.rationale.json",
        rfile(
            [
                note("R1", text_of("R1")),
                note("R2", text_of("R2")),
            ]
        ),
    )
    m = load(proj, trace).metrics()
    assert m["RC"] == {"n": 1, "d": 4, "pct": 25.0}  # R1 (context) is not in the scope
    assert m["RC_all"] == {"n": 2, "d": 5, "pct": 40.0}
    assert m["RN"] == {"current": 2, "moved": 0, "edited": 0, "ambiguous": 0, "stale": 0}
    assert m["file"] == "REQUIREMENTS.rationale.json" and m["proposals_file"] is None


def test_note_view_shows_text_only_for_current_or_moved(proj: Path, trace: Trace) -> None:
    write(
        proj,
        "REQUIREMENTS.rationale.json",
        rfile(
            [
                note("R2", text_of("R2")),
                note("R3", "old wording"),
                note("R9", text_of("R4")),
            ]
        ),
    )
    views = {v["rule"]: v for v in (rt.note_view(r) for r in load(proj, trace).resolved)}
    assert views["R2"]["why"] == "Because." and views["R2"]["approved_by"] == "ann"
    assert "why" not in views["R3"] and views["R3"]["state"] == "stale"
    assert views["R9"]["state"] == "moved" and views["R9"]["moved_to"] == "R4"
    assert views["R9"]["why"] == "Because."


# ---- validation (D2, D14) ----------------------------------------------------------------


def bad(proj: Path, trace: Trace, text: str, name: str = "REQUIREMENTS.rationale.json") -> str:
    write(proj, name, text)
    with pytest.raises(NotesError) as e:
        load(proj, trace)
    assert e.value.code == "bad_rationale"
    return str(e.value)


def test_invalid_authoritative_files_answer_bad_rationale(proj: Path, trace: Trace) -> None:
    good = note("R2", text_of("R2"))
    assert "not valid JSON" in bad(proj, trace, "{nope")
    assert "top level" in bad(proj, trace, "[]")
    assert "schema" in bad(proj, trace, json.dumps({"schema": "x", "notes": []}))
    assert "notes must be a list" in bad(
        proj, trace, json.dumps({"schema": "lattice.rationale/1", "notes": {}})
    )
    assert "why is empty" in bad(proj, trace, rfile([{**good, "why": " "}]))
    assert "longer than 600" in bad(proj, trace, rfile([{**good, "why": "x" * 601}]))
    assert "tradeoff is longer" in bad(proj, trace, rfile([{**good, "tradeoff": "x" * 601}]))
    assert "anchor" in bad(proj, trace, rfile([{**good, "anchor": "abc"}]))
    assert "author" in bad(proj, trace, rfile([{**good, "author": "robot"}]))
    assert "status must be" in bad(proj, trace, rfile([{**good, "status": "weird"}]))
    assert "basis" in bad(proj, trace, rfile([{**good, "basis": [{"target": 1}]}]))
    assert "duplicate approved note for R2" in bad(proj, trace, rfile([good, good]))
    assert "only approved" in bad(
        proj, trace, rfile([note("R2", text_of("R2"), status="proposed")])
    )
    for key in ("owner", "approved_by", "approved_sha"):
        assert key in bad(proj, trace, rfile([{**good, key: None}]))


def test_invalid_proposals_files_answer_bad_rationale(proj: Path, trace: Trace) -> None:
    name = "REQUIREMENTS.rationale.proposed.json"
    assert "cannot hold an approved note" in bad(
        proj, trace, rfile([note("R2", text_of("R2"))]), name
    )
    prop = note("R2", text_of("R2"), status="proposed", author="llm:sonnet")
    assert "never names the owner" in bad(proj, trace, rfile([{**prop, "owner": "Ann"}]), name)
    assert "no approved_by" in bad(proj, trace, rfile([{**prop, "approved_by": "Ann"}]), name)
    # several proposals for one rule are fine (a drafter appends)
    write(proj, name, rfile([prop, {**prop, "why": "Another."}]))
    assert load(proj, trace).metrics()["proposals"] == 2


def test_an_unknown_rule_id_is_not_invalid(proj: Path, trace: Trace) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R99", "Some old rule.")]))
    assert load(proj, trace).resolved[0].state == "stale"


# ---- the human guard and atomic writes (D4) ----------------------------------------------


def test_guard_refuses_without_a_terminal_and_with_claudecode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(rt, "_terminal", lambda: False)
    with pytest.raises(NotesError) as e:
        rt.human_guard("rationale approve")
    assert e.value.code == "approve_needs_terminal" and "terminal" in e.value.detail
    monkeypatch.setattr(rt, "_terminal", lambda: True)
    rt.human_guard("rationale approve")  # a person at a terminal
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(NotesError) as e2:
        rt.human_guard("rationale approve")
    assert e2.value.code == "approve_needs_terminal" and "CLAUDECODE" in e2.value.detail


def test_atomic_write_leaves_only_the_target(tmp_path: Path) -> None:
    p = tmp_path / "d" / "f.json"
    rt.atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    rt.atomic_write_json(p, {"a": 2})
    assert sorted(x.name for x in p.parent.iterdir()) == ["f.json"]


def test_atomic_write_failure_cleans_the_temporary_file(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        rt.atomic_write_json(tmp_path / "f.json", {"a": object()})
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def human(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(rt, "_terminal", lambda: True)


def draft_file(proj: Path, rules: list[str], author: str = "llm:sonnet") -> Path:
    props = [
        note(
            r,
            text_of(r),
            why=f"Draft why {r}.",
            tradeoff="Draft trade.",
            status="proposed",
            author=author,
        )
        for r in rules
    ]
    return write(proj, "REQUIREMENTS.rationale.proposed.json", rfile(props))


def cli(*argv: str, **kw: Any) -> int:
    return rt.main(list(argv), **kw)


def test_approve_copies_a_proposal_and_writes_exactly_one_file(
    proj: Path, trace: Trace, human: None, capsys: pytest.CaptureFixture[str]
) -> None:
    prop = draft_file(proj, ["R2", "R3"])
    before = sorted(p.name for p in proj.iterdir())
    rc = cli(
        "approve",
        "--trace",
        str(proj / "spec.trace.json"),
        "--from",
        str(prop),
        "--rule",
        "R2",
        "--approver",
        "ann",
        "--owner",
        "bob",
        "--json",
    )
    assert rc == 0
    after = sorted(p.name for p in proj.iterdir())
    assert set(after) - set(before) == {"REQUIREMENTS.rationale.json"}
    assert prop.read_text() == draft_file(proj, ["R2", "R3"]).read_text()  # proposals untouched
    (n,) = json.loads((proj / "REQUIREMENTS.rationale.json").read_text())["notes"]
    assert n["rule"] == "R2" and n["status"] == "approved" and n["owner"] == "bob"
    assert n["approved_by"] == "ann" and n["author"] == "llm:sonnet"  # unedited: author kept
    assert n["approved_sha"] == appr_sha("Draft why R2.", "Draft trade.", "bob")
    assert n["anchor"] == anchor(text_of("R2"))
    assert load(proj, trace).resolved[0].state == "current"


def test_approving_with_edited_text_makes_the_owner_the_author(
    proj: Path, trace: Trace, human: None
) -> None:
    prop = draft_file(proj, ["R2"])
    assert (
        cli(
            "approve",
            "--trace",
            str(proj / "spec.trace.json"),
            "--from",
            str(prop),
            "--rule",
            "R2",
            "--approver",
            "ann",
            "--owner",
            "ann",
            "--why",
            "My own why.",
        )
        == 0
    )
    (n,) = json.loads((proj / "REQUIREMENTS.rationale.json").read_text())["notes"]
    assert n["why"] == "My own why." and n["tradeoff"] == "Draft trade." and n["author"] == "owner"
    assert n["approved_sha"] == appr_sha("My own why.", "Draft trade.", "ann")


def test_approve_prompts_for_a_missing_owner(proj: Path, human: None) -> None:
    prop = draft_file(proj, ["R2"])
    asked = []

    def fake(prompt: str) -> str:
        asked.append(prompt)
        return "carol"

    assert (
        cli(
            "approve",
            "--trace",
            str(proj / "spec.trace.json"),
            "--from",
            str(prop),
            "--rule",
            "R2",
            "--approver",
            "ann",
            prompt=fake,
        )
        == 0
    )
    assert asked and "owner" in asked[0]
    (n,) = json.loads((proj / "REQUIREMENTS.rationale.json").read_text())["notes"]
    assert n["owner"] == "carol"


def test_approve_refuses_what_d4_lists(
    proj: Path, human: None, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    prop = draft_file(proj, ["R2"])
    tr = str(proj / "spec.trace.json")
    base = ["approve", "--trace", tr, "--from", str(prop), "--approver", "a", "--owner", "o"]
    auth = proj / "REQUIREMENTS.rationale.json"

    def refused(args: list[str], code: str) -> None:
        capsys.readouterr()
        assert cli(*args) == 2
        assert code in capsys.readouterr().err
        assert not auth.exists()

    refused([*base, "--rule", "R9"], "unknown_rule")
    refused([*base, "--rule", "R3"], "no_proposal")  # no proposal for R3
    refused(
        [
            "approve",
            "--trace",
            tr,
            "--from",
            str(auth),
            "--rule",
            "R2",
            "--approver",
            "a",
            "--owner",
            "o",
        ],
        "approve_from_authoritative",
    )
    monkeypatch.setattr(rt, "_terminal", lambda: False)
    refused([*base, "--rule", "R2"], "approve_needs_terminal")
    monkeypatch.setattr(rt, "_terminal", lambda: True)
    monkeypatch.setenv("CLAUDECODE", "1")
    refused([*base, "--rule", "R2"], "approve_needs_terminal")
    monkeypatch.delenv("CLAUDECODE")
    # a proposal for an older wording of the rule is refused
    old = note("R2", "an older wording", status="proposed", author="hand")
    write(proj, "REQUIREMENTS.rationale.proposed.json", rfile([old]))
    refused([*base, "--rule", "R2"], "proposal_stale")


def test_approve_refuses_to_replace_a_current_note_without_replace(proj: Path, human: None) -> None:
    prop = draft_file(proj, ["R2"])
    tr = str(proj / "spec.trace.json")
    args = [
        "approve",
        "--trace",
        tr,
        "--from",
        str(prop),
        "--rule",
        "R2",
        "--approver",
        "a",
        "--owner",
        "o",
    ]
    assert cli(*args) == 0
    assert cli(*args) == 2  # rationale_exists
    assert cli(*args, "--replace") == 0
    assert len(json.loads((proj / "REQUIREMENTS.rationale.json").read_text())["notes"]) == 1


def test_approving_over_a_stale_note_replaces_it(proj: Path, trace: Trace, human: None) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R2", "old wording")]))
    prop = draft_file(proj, ["R2"])
    assert (
        cli(
            "approve",
            "--trace",
            str(proj / "spec.trace.json"),
            "--from",
            str(prop),
            "--rule",
            "R2",
            "--approver",
            "a",
            "--owner",
            "o",
        )
        == 0
    )
    assert [r.state for r in load(proj, trace).resolved] == ["current"]


def test_retire_proposal_marks_it_rejected_and_superseded(proj: Path, human: None) -> None:
    prop = draft_file(proj, ["R2", "R3"])
    assert (
        cli(
            "approve",
            "--trace",
            str(proj / "spec.trace.json"),
            "--from",
            str(prop),
            "--rule",
            "R2",
            "--approver",
            "a",
            "--owner",
            "o",
            "--retire-proposal",
        )
        == 0
    )
    notes = json.loads(prop.read_text())["notes"]
    by = {n["rule"]: n for n in notes}
    assert by["R2"]["status"] == "rejected" and by["R2"]["reason"] == "superseded"
    assert by["R3"]["status"] == "proposed"


def test_add_writes_an_owner_authored_note(proj: Path, trace: Trace, human: None) -> None:
    assert (
        cli(
            "add",
            "--trace",
            str(proj / "spec.trace.json"),
            "--rule",
            "R3",
            "--why",
            "Speed is not a requirement.",
            "--owner",
            "bob",
            "--approver",
            "ann",
        )
        == 0
    )
    (n,) = json.loads((proj / "REQUIREMENTS.rationale.json").read_text())["notes"]
    assert n["author"] == "owner" and n["tradeoff"] is None and n["owner"] == "bob"
    assert n["approved_sha"] == appr_sha("Speed is not a requirement.", None, "bob")
    assert load(proj, trace).status("R3") == "approved"
    assert (
        cli(
            "add",
            "--trace",
            str(proj / "spec.trace.json"),
            "--rule",
            "R9",
            "--why",
            "x",
            "--owner",
            "o",
            "--approver",
            "a",
        )
        == 2
    )
    assert (
        cli(
            "add",
            "--trace",
            str(proj / "spec.trace.json"),
            "--rule",
            "R3",
            "--why",
            "x" * 601,
            "--owner",
            "o",
            "--approver",
            "a",
            "--replace",
        )
        == 2
    )


def test_reject_marks_the_last_proposal_and_never_touches_the_approved_file(
    proj: Path, human: None
) -> None:
    prop = draft_file(proj, ["R2"])
    tr = str(proj / "spec.trace.json")
    assert cli("reject", "--trace", tr, "--from", str(prop), "--rule", "R2", "--reason", "no") == 0
    (n,) = json.loads(prop.read_text())["notes"]
    assert n["status"] == "rejected" and n["reason"] == "no"
    assert not (proj / "REQUIREMENTS.rationale.json").exists()
    assert cli("reject", "--trace", tr, "--from", str(prop), "--rule", "R2") == 2  # nothing left


# ---- drafting (D9): proposals only --------------------------------------------------------


def reply(**kw: Any) -> str:
    body = {"why": "A model's guess.", "tradeoff": "A cost.", "basis": []}
    body.update(kw)
    return json.dumps(body)


def draft_cli(
    proj: Path, replies: list[str], *rules: str, out: str = "draft.json"
) -> tuple[int, Any]:
    gen = ScriptedGenerator(replies)
    args = [
        "draft",
        "--trace",
        str(proj / "spec.trace.json"),
        "--spec",
        "tests.spec.racy:RUNS_SPEC",
        "--out",
        str(proj / out),
        "--model",
        "stub",
        "--json",
    ]
    for r in rules:
        args += ["--rule", r]
    return cli(*args, generator=gen), gen


def test_draft_writes_proposals_with_owner_null_and_never_the_approved_file(
    proj: Path, trace: Trace
) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R2", text_of("R2"))]))
    auth_bytes = (proj / "REQUIREMENTS.rationale.json").read_bytes()
    forged = reply(owner="Alice", status="approved", approved_by="Bob", author="owner")
    rc, gen = draft_cli(proj, [forged], "R3")
    assert rc == 0 and len(gen.calls) == 1
    (n,) = json.loads((proj / "draft.json").read_text())["notes"]
    assert (n["status"], n["owner"], n["approved_by"], n["approved_sha"]) == (
        "proposed",
        None,
        None,
        None,
    )
    assert n["author"] == "llm:stub" and n["anchor"] == anchor(text_of("R3"))
    assert set(n["dropped"]) == {"owner", "status", "approved_by", "author"}
    assert (proj / "REQUIREMENTS.rationale.json").read_bytes() == auth_bytes
    assert (
        load(proj, trace) is not None
        and not (proj / "REQUIREMENTS.rationale.proposed.json").exists()
    )


def test_draft_checks_each_basis_citation_against_the_digest(proj: Path) -> None:
    quote = text_of("R2")[:20]
    r = reply(
        basis=[
            {"target": "rule:R2", "quote": "A config has at most"},
            {"target": "rule:R2", "quote": "words that are not in the rule"},
            {"target": "scenario:nope", "quote": "whatever text"},
        ]
    )
    rc, _ = draft_cli(proj, [r], "R2")
    assert rc == 0 and quote
    (n,) = json.loads((proj / "draft.json").read_text())["notes"]
    assert [b["target"] for b in n["basis"]] == ["rule:R2"]
    assert [b["target"] for b in n["rejected_basis"]] == ["rule:R2", "scenario:nope"]
    assert "not in the element" in n["rejected_basis"][0]["reason"]


def test_draft_merges_by_rule_keeping_earlier_proposals(proj: Path) -> None:
    draft_cli(proj, [reply(why="First.")], "R2")
    draft_cli(proj, [reply(why="Second."), reply(why="Third.")], "R2", "R3")
    notes = json.loads((proj / "draft.json").read_text())["notes"]
    assert sorted(n["why"] for n in notes if n["rule"] == "R2") == ["First.", "Second."]
    assert [n["why"] for n in notes if n["rule"] == "R3"] == ["Third."]


def test_draft_keeps_what_it_drafted_when_the_model_stops_midway(
    proj: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, gen = draft_cli(proj, [reply(why="Only one.")], "R2", "R3")  # the second call raises
    assert rc == 2 and len(gen.calls) == 2
    assert "stopped at R3" in capsys.readouterr().err
    assert [n["why"] for n in json.loads((proj / "draft.json").read_text())["notes"]] == [
        "Only one."
    ]


def test_draft_refuses_the_approved_file_as_out(
    proj: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R2", text_of("R2"))]))
    before = (proj / "REQUIREMENTS.rationale.json").read_bytes()
    rc, gen = draft_cli(proj, [reply()], "R3", out="REQUIREMENTS.rationale.json")
    assert rc == 2 and gen.calls == []
    assert "draft_out_is_authoritative" in capsys.readouterr().err
    assert (proj / "REQUIREMENTS.rationale.json").read_bytes() == before


def test_draft_unexplained_picks_rules_without_notes_and_skips_context(proj: Path) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R2", text_of("R2"))]))
    gen = ScriptedGenerator([reply()] * 5)
    rc = cli(
        "draft",
        "--trace",
        str(proj / "spec.trace.json"),
        "--spec",
        "tests.spec.racy:RUNS_SPEC",
        "--out",
        str(proj / "d.json"),
        "--model",
        "stub",
        "--unexplained",
        "--limit",
        "2",
        generator=gen,
    )
    assert rc == 0 and len(gen.calls) == 2
    assert [n["rule"] for n in json.loads((proj / "d.json").read_text())["notes"]] == ["R3", "R4"]


def test_draft_with_an_unreadable_reply_writes_nothing(proj: Path) -> None:
    rc, _ = draft_cli(proj, ["not json at all"], "R2")
    assert rc == 0 and not (proj / "draft.json").exists()


def test_draft_prompt_says_the_model_does_not_know_the_owner() -> None:
    assert "You do not know the owner's reason" in rt.DRAFT_SYSTEM
    assert "never name an owner" in rt.DRAFT_SYSTEM and "basis []" in rt.DRAFT_SYSTEM


# ---- the read CLI ------------------------------------------------------------------------


def test_show_and_coverage_print_states_and_never_proposal_as_approved(
    proj: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        proj, "REQUIREMENTS.rationale.json", rfile([note("R2", text_of("R2")), note("R3", "old")])
    )
    draft_file(proj, ["R4"], author="hand")
    tr = str(proj / "spec.trace.json")
    assert cli("show", "--trace", tr) == 0
    out = capsys.readouterr().out
    assert "R2 current" in out and "R3 stale" in out and "R4 proposed" in out
    assert "not the owner's reason" in out
    assert cli("coverage", "--trace", tr, "--json") == 0
    m = json.loads(capsys.readouterr().out)["metrics"]
    assert m["RC"]["n"] == 1 and m["RP"] == 1 and m["RN"]["stale"] == 1
    assert cli("show", "--trace", tr, "--json", "--rule", "R2") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["notes"][0]["why"] == "Because."


def test_read_commands_write_nothing(proj: Path) -> None:
    write(proj, "REQUIREMENTS.rationale.json", rfile([note("R2", text_of("R2"))]))
    before = {p.name: p.read_bytes() for p in proj.iterdir()}
    tr = str(proj / "spec.trace.json")
    for argv in (["show"], ["coverage"], ["show", "--json"], ["coverage", "--json"]):
        assert cli(*argv, "--trace", tr) == 0
    assert {p.name: p.read_bytes() for p in proj.iterdir()} == before


def test_invalid_file_makes_the_cli_exit_2(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(proj, "REQUIREMENTS.rationale.json", "{bad")
    assert cli("coverage", "--trace", str(proj / "spec.trace.json")) == 2
    assert "bad_rationale" in capsys.readouterr().err


def test_a_missing_trace_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("show", "--trace", str(tmp_path / "none.json")) == 2
    assert "no_trace" in capsys.readouterr().err


def test_the_command_is_wired_into_the_spec_cli(proj: Path) -> None:
    from lattice.spec import __main__ as m

    assert m.AUTHORING["rationale"] == "lattice.spec.rationale"
    assert m.AUTHORING["glossary"] == "lattice.spec.glossary"
    assert m.main(["rationale", "coverage", "--trace", str(proj / "spec.trace.json")]) == 0


def test_no_api_key_is_read_by_the_default_client_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drafter's default generator is the Claude CLI client, which strips ANTHROPIC_*."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from lattice.llm.cli_client import cli_env

    assert "ANTHROPIC_API_KEY" not in cli_env(dict(os.environ), None)
    assert racy.RUNS_SPEC is not None
