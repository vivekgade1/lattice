"""Rationale notes and the glossary in the chat (lattice.spec.ask, implementation, Studio and its
sessions): the digest sections, the citation check, the grounding rule, the answer's `rationale`
key, retrieval, the CLI, the report keys, API v1.23, and that with no file every output is as
before. Stub generators only: never the Claude CLI or an API key."""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.llm.client import ScriptedGenerator
from lattice.spec import ask as A
from lattice.spec import glossary as gl
from lattice.spec import implementation as impl
from lattice.spec import rationale as rt
from lattice.spec.rules import Trace, extract_rules
from tests.spec import racy
from tests.spec.test_glossary import gfile, term
from tests.spec.test_rationale import MD, TRACE_RULES, anchor, appr_sha, note, rfile, text_of

SPEC = racy.RUNS_SPEC
LINKS = {
    "R2": ["invariant:one_live_run_per_config", "scenario:one_live_run_per_config"],
    "R4": ["scenario:a_cancelled_run_is_never_executed"],
    "R5": ["scenario:the_worker_runs_a_queued_run"],
}
WHY2 = "Two live runs of one config would race for the same worker slot."
TRADE2 = "A second run must wait for the first to finish."
PROPOSAL_TEXT = "SECRET PROPOSAL: runs are fast because the owner likes speed"
STALE_TEXT = "SECRET STALE: an old reason for an old wording"


def make_trace() -> Trace:
    return Trace(
        "REQUIREMENTS.md",
        extract_rules(MD),
        links=LINKS,
        context=["R1"],
        unchecked={"R3": "no timing in the spec"},
    )


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    (tmp_path / "REQUIREMENTS.md").write_text(MD)
    make_trace().save(tmp_path / "spec.trace.json")
    return tmp_path


def fill(
    proj: Path, *, rationale: bool = True, glossary: bool = True, proposals: bool = True
) -> None:
    if rationale:
        (proj / "REQUIREMENTS.rationale.json").write_text(
            rfile(
                [
                    note("R2", text_of("R2"), WHY2, TRADE2, owner="ann", by="bob"),
                    note("R4", text_of("R4"), "A cancelled run has nothing left to do.", None),
                    note("R3", "Runs were slow once.", STALE_TEXT, None),
                ]
            )
        )
    if proposals:
        (proj / "REQUIREMENTS.rationale.proposed.json").write_text(
            rfile(
                [
                    note(
                        "R5", text_of("R5"), PROPOSAL_TEXT, None, status="proposed", author="hand"
                    ),
                ]
            )
        )
    if glossary:
        (proj / "REQUIREMENTS.glossary.json").write_text(
            gfile(
                [
                    term("live run", "A run that is queued or running.", aliases=("active run",)),
                    term("config", "A named job configuration."),
                ]
            )
        )


def notes_of(proj: Path) -> Any:
    trace = Trace.load(proj / "spec.trace.json")
    return gl.discover_notes(trace, proj / "spec.trace.json")


def digest_of(proj: Path) -> A.Digest:
    trace = Trace.load(proj / "spec.trace.json")
    return A.build_digest(SPEC, trace, None, notes_of(proj))


def reply(kind: str = "value", cites: list[dict] | None = None, answer: str = "a") -> str:
    return json.dumps(
        {"kind": kind, "answer": answer, "explanation": "e", "citations": cites or []}
    )


def cite(target: str, quote: str, step: int | None = None) -> dict:
    return {"target": target, "step": step, "quote": quote}


def ask_with(proj: Path, replies: list[str], q: str = "Why?", **kw: Any) -> tuple[A.Answer, Any]:
    trace = Trace.load(proj / "spec.trace.json")
    g = ScriptedGenerator(replies)
    a = A.ask(SPEC, q, trace=trace, generator=g, model="m", notes=notes_of(proj), **kw)
    return a, g


# ---- the digest ---------------------------------------------------------------------------------


def test_the_old_digest_is_an_exact_prefix_and_the_sections_are_last(proj: Path) -> None:
    fill(proj)
    trace = Trace.load(proj / "spec.trace.json")
    old = A.build_digest(SPEC, trace)
    new = digest_of(proj)
    assert new.text.startswith(old.text) and len(new.text) > len(old.text)
    tail = new.text[len(old.text) :]
    assert tail.index(
        "## Rationale (owner-approved: why a rule exists; cite rationale:<Rn>)"
    ) < tail.index("## Glossary (owner-approved definitions; cite term:<slug>)")
    assert [ln for ln in old.text.splitlines() if ln.startswith("[rule:")] == [
        ln for ln in new.text.splitlines() if ln.startswith("[rule:")
    ]


def test_elements_and_lines_follow_d10(proj: Path) -> None:
    fill(proj)
    d = digest_of(proj)
    assert f"[rationale:R2] (owner ann; approved by bob) why: {WHY2} trade-off: {TRADE2}" in d.text
    assert (
        "[rationale:R4] (owner ann; approved by ann) why: A cancelled run has nothing left"
        in d.text
    )
    assert "[term:live-run] live run (also: active run): A run that is queued or running." in d.text
    assert "[term:config] config: A named job configuration." in d.text
    assert {e for e in d.elements if e.startswith(("rationale:", "term:"))} == {
        "rationale:R2",
        "rationale:R4",
        "term:live-run",
        "term:config",
    }
    # a proposal and a stale note are never in the digest
    assert PROPOSAL_TEXT not in d.text and STALE_TEXT not in d.text
    assert "rationale:R3" not in d.elements and "rationale:R5" not in d.elements


def test_a_moved_note_is_cited_under_the_new_id(proj: Path) -> None:
    (proj / "REQUIREMENTS.rationale.json").write_text(
        rfile([note("R9", text_of("R4"), "Moved reason text.", None)])
    )
    d = digest_of(proj)
    assert "rationale:R4" in d.elements and "rationale:R9" not in d.elements


def test_only_the_files_that_exist_get_a_section(proj: Path) -> None:
    fill(proj, glossary=False, proposals=False)
    d = digest_of(proj)
    assert "## Rationale" in d.text and "## Glossary" not in d.text
    fill(proj, rationale=False, glossary=True, proposals=False)
    (proj / "REQUIREMENTS.rationale.json").unlink()
    d2 = digest_of(proj)
    assert "## Glossary" in d2.text and "## Rationale" not in d2.text


def test_empty_sections_read_none(proj: Path) -> None:
    (proj / "REQUIREMENTS.rationale.json").write_text(rfile([]))
    d = digest_of(proj)
    assert d.text.rstrip().endswith("(none)")


def test_proposals_alone_add_no_section_but_keep_the_key(proj: Path) -> None:
    fill(proj, rationale=False, glossary=False, proposals=True)
    trace = Trace.load(proj / "spec.trace.json")
    old = A.build_digest(SPEC, trace)
    d = digest_of(proj)
    assert d.text == old.text and d.elements.keys() == old.elements.keys()
    a, g = ask_with(proj, [reply("value", [cite("rule:R5", "The worker drains")])])
    assert a.rationale == [{"rule": "R5", "status": "proposed_only", "via": None}]
    assert "rationale:<Rn>" not in g.calls[0][0] and A.SYSTEM_NOTES not in g.calls[0][0]
    assert PROPOSAL_TEXT not in json.dumps(a.to_dict())


# ---- the citation check --------------------------------------------------------------------------


def test_citations_of_notes_check_the_note_text(proj: Path) -> None:
    fill(proj)
    d = digest_of(proj)
    ok = A.check_citation
    assert ok(d, cite("rationale:R2", "would race for the same worker")) is None
    assert ok(d, cite("rationale:R2", "A second run must wait")) is None  # the trade-off counts
    assert ok(d, cite("rationale:R2", "WOULD  race for the same")) is None  # normalised
    assert "shorter than 8" in ok(d, cite("rationale:R2", "race"))
    assert "shorter than 8" in ok(d, cite("rationale:R2", "a  b  c  d"))
    assert "not in the note's text" in ok(d, cite("rationale:R2", "because the owner said so"))
    assert "no element" in ok(d, cite("rationale:R3", "Runs were slow once."))  # stale
    assert "no element" in ok(d, cite("rationale:R5", PROPOSAL_TEXT[:30]))  # proposal
    assert "step number" in ok(d, cite("rationale:R2", "would race for the same", 1))
    assert ok(d, cite("term:live-run", "A run that is queued or running.")) is None
    assert ok(d, cite("term:live-run", "queued or running")) is None
    assert "no element" in ok(d, cite("term:daily-pass", "a daily pass is"))
    assert "not in the note's text" in ok(d, cite("term:live-run", "A run that is finished"))
    assert ok(d, cite("term:live-run", "")) == "the quote is empty"


def test_without_notes_these_targets_do_not_exist(proj: Path) -> None:
    trace = Trace.load(proj / "spec.trace.json")
    d = A.build_digest(SPEC, trace)
    assert "no element 'rationale:R2'" in A.check_citation(d, cite("rationale:R2", "would race"))


# ---- grounding -----------------------------------------------------------------------------------


def test_rationale_citations_never_ground_a_yes_no_value_answer(proj: Path) -> None:
    fill(proj)
    only = reply("yes", [cite("rationale:R2", "would race for the same worker")])
    a, g = ask_with(proj, [only, only])
    assert a.kind == "not_specified" and a.answer == A.NOT_GROUNDED
    assert "only the owner's rationale or glossary" in a.explanation
    assert len(g.calls) == 2  # one repair round, as for any ungrounded answer
    assert "do not ground a yes / no / value answer" in g.calls[1][1][-1]["content"]
    # the same with a term, and with a mixed answer
    t = reply("yes", [cite("term:live-run", "queued or running")])
    assert ask_with(proj, [t, t])[0].kind == "not_specified"
    mixed = reply(
        "value",
        [
            cite("rationale:R2", "would race for the same worker"),
            cite("rule:R2", "A config has at most one"),
        ],
    )
    b, g2 = ask_with(proj, [mixed])
    assert b.kind == "value" and len(b.citations) == 2 and len(g2.calls) == 1


def test_a_not_specified_answer_may_cite_the_rationale(proj: Path) -> None:
    fill(proj)
    a, g = ask_with(
        proj, [reply("not_specified", [cite("rationale:R2", "would race for the same")])]
    )
    assert a.kind == "not_specified" and len(a.citations) == 1 and len(g.calls) == 1


def test_repair_message_names_the_bad_note_citation(proj: Path) -> None:
    fill(proj)
    bad = reply(
        "value",
        [cite("rationale:R2", "the owner wanted it"), cite("rule:R2", "A config has at most one")],
    )
    a, g = ask_with(proj, [bad, bad])
    assert [r["target"] for r in a.rejected_citations] == ["rationale:R2"]
    assert "not in the note's text" in g.calls[1][1][-1]["content"]


# ---- the answer's rationale key ------------------------------------------------------------------


def att(a: A.Answer) -> list[dict]:
    return a.rationale or []


def test_attachment_statuses_and_text_follow_d10(proj: Path) -> None:
    fill(proj)
    a, _ = ask_with(proj, [reply("value", [cite("rule:R2", "A config has at most one")])])
    assert att(a) == [
        {
            "rule": "R2",
            "status": "approved",
            "via": None,
            "why": WHY2,
            "tradeoff": TRADE2,
            "owner": "ann",
            "approved_by": "bob",
        }
    ]
    for rid, status in (("R3", "stale"), ("R5", "proposed_only"), ("R1", "none")):
        b, _ = ask_with(proj, [reply("value", [cite(f"rule:{rid}", text_of(rid)[:12])])])
        assert att(b) == [{"rule": rid, "status": status, "via": None}]
        dumped = json.dumps(b.to_dict())
        assert PROPOSAL_TEXT not in dumped and STALE_TEXT not in dumped


def test_linked_rules_follow_direct_ones_by_rule_number_with_their_via(proj: Path) -> None:
    fill(proj)
    cites = [
        cite("scenario:a_cancelled_run_is_never_executed", "a_cancelled_run_is_never_executed"),
        cite("rule:R5", "The worker drains"),
        cite("invariant:one_live_run_per_config", "A config has at most one"),
    ]
    a, _ = ask_with(proj, [reply("value", cites)])
    assert [(e["rule"], e["via"]) for e in att(a)] == [
        ("R5", None),
        ("R2", "invariant:one_live_run_per_config"),
        ("R4", "scenario:a_cancelled_run_is_never_executed"),
    ]
    assert att(a)[1]["status"] == "approved" and "why" in att(a)[1]
    assert att(a)[0]["status"] == "proposed_only"


def test_the_attachment_holds_at_most_five_entries() -> None:
    rules = extract_rules("# T\n\n" + "\n".join(f"- Rule number {i} is here." for i in range(9)))
    trace = Trace("REQUIREMENTS.md", rules, links={r.id: ["scenario:s"] for r in rules})
    d = A.build_digest(SPEC, trace, None, None)

    class FakeNotes:
        paths = None
        rationale = rt.empty_set(trace)
        glossary = None

    d = A.replace(d, notes=FakeNotes)  # type: ignore[attr-defined]
    out = A.rationale_attachment(d, [cite("scenario:s", "x")])
    assert len(out) == 5 and [e["rule"] for e in out] == ["R1", "R2", "R3", "R4", "R5"]


def test_only_valid_citations_are_used(proj: Path) -> None:
    fill(proj)
    bad = reply("not_specified", [cite("rule:R2", "words that are not in the rule at all")])
    a, _ = ask_with(proj, [bad, bad])
    assert att(a) == [] and a.rationale is not None  # the key is there, empty


def test_the_key_is_absent_without_a_rationale_or_proposals_file(proj: Path) -> None:
    fill(proj, rationale=False, proposals=False, glossary=True)
    a, _ = ask_with(proj, [reply("value", [cite("rule:R2", "A config has at most one")])])
    assert a.rationale is None and "rationale" not in a.to_dict()


def test_render_prints_the_approved_reason_and_marks_the_others(proj: Path) -> None:
    fill(proj)
    a, _ = ask_with(
        proj,
        [
            reply(
                "value",
                [cite("rule:R2", "A config has at most one"), cite("rule:R5", "The worker drains")],
            )
        ],
    )
    text = a.render()
    assert f"why (R2, approved by bob): {WHY2}" in text
    assert "why (R5): no approved rationale (proposed_only)" in text
    assert PROPOSAL_TEXT not in text


# ---- the prompt and retrieval --------------------------------------------------------------------


def test_the_prompt_paragraph_is_added_only_with_sections(proj: Path) -> None:
    fill(proj)
    _, g = ask_with(proj, [reply("not_specified")])
    system = g.calls[0][0]
    assert A.SYSTEM_NOTES in system
    for phrase in (
        "When asked why, cite the rationale",
        "do not offer one as the owner's",
        "never ground a yes/no/value answer by themselves",
    ):
        assert phrase in A.SYSTEM_NOTES.replace("\n", " ")
    trace = Trace.load(proj / "spec.trace.json")
    plain = A.retrieve(A.build_digest(SPEC, trace), "Why?")
    assert A.SYSTEM_NOTES not in plain.system


def test_the_notes_go_in_the_core_when_they_fit(proj: Path) -> None:
    fill(proj)
    d = digest_of(proj)
    r = A.retrieve(d, "How does the worker run things?")
    assert "[rationale:R2]" in r.system and "[term:live-run]" in r.system
    assert "rationale and glossary relevant" not in r.context
    assert {"rationale:R2", "term:config"} <= r.sent


def test_over_budget_the_user_message_carries_the_notes_of_the_retrieved_rules(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(proj)
    monkeypatch.setattr(A, "NOTES_CORE_BUDGET", 10)
    d = digest_of(proj)
    r = A.retrieve(d, "Can a cancelled run be executed? What is a live run?")
    assert "[rationale:R2]" not in r.system and "[term:live-run]" not in r.system
    assert "[rationale:R4]" in r.context  # the rule that links the retrieved scenario
    assert "[term:live-run]" in r.context  # the question uses the term
    assert "[term:config]" not in r.context  # the question does not
    assert "rationale:R4" in r.sent
    assert A.SYSTEM_NOTES in r.system  # the paragraph still says how to read them
    # a question that retrieves nothing carries no rationale, and a selected rule brings its own
    quiet = A.retrieve(d, "Is there a delete endpoint?")
    assert "[rationale:" not in quiet.context and "[term:" not in quiet.context
    assert "(none matched the question)" in quiet.context.split("## Owner-approved")[1]
    sel = A.retrieve(d, "Is there a delete endpoint?", ["rule:R2"])
    assert "[rationale:R2]" in sel.context


def test_the_notes_budget_caps_the_user_message(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(proj)
    monkeypatch.setattr(A, "NOTES_CORE_BUDGET", 10)
    monkeypatch.setattr(A, "NOTES_BUDGET", 5)
    r = A.retrieve(digest_of(proj), "Can a cancelled run be executed? What is a live run?")
    assert "[rationale:R4]" not in r.context and "[term:live-run]" not in r.context


def test_a_need_more_request_can_name_a_note(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(proj)
    monkeypatch.setattr(A, "NOTES_CORE_BUDGET", 10)
    need = json.dumps({"need": ["rationale:R2"], "search": []})
    a, g = ask_with(
        proj,
        [need, reply("not_specified", [cite("rationale:R2", "would race for the")])],
        q="Anything else?",
    )
    assert "[rationale:R2] (owner ann; approved by bob)" in g.calls[1][1][-1]["content"]
    assert a.citations and not a.rejected_citations


# ---- with no file everything is as before --------------------------------------------------------


def snapshot(a: A.Answer, g: Any) -> tuple[str, str]:
    d = a.to_dict()
    d["usage"] = {k: v for k, v in d["usage"].items() if k not in ("seconds", "calls")}
    return json.dumps(d, sort_keys=True), json.dumps([[c[0], c[1]] for c in g.calls])


def test_no_files_means_the_old_digest_prompts_and_answers(proj: Path) -> None:
    trace = Trace.load(proj / "spec.trace.json")
    assert notes_of(proj) is None
    r0 = reply("value", [cite("rule:R2", "A config has at most one")])
    g0, g1 = ScriptedGenerator([r0]), ScriptedGenerator([r0])
    a0 = A.ask(SPEC, "Why?", trace=trace, generator=g0, model="m")
    a1 = A.ask(SPEC, "Why?", trace=trace, generator=g1, model="m", notes=None)
    assert snapshot(a0, g0) == snapshot(a1, g1)
    assert "rationale" not in a0.to_dict()
    d0, d1 = A.build_digest(SPEC, trace), A.build_digest(SPEC, trace, None, None)
    assert d0.text == d1.text and A.core_text(d0) == A.core_text(d1)


# ---- the ask CLI ---------------------------------------------------------------------------------


def run_ask(proj: Path, replies: list[str], *extra: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = A.main(
            [
                "--spec",
                "tests.spec.racy:RUNS_SPEC",
                "--trace",
                str(proj / "spec.trace.json"),
                "--json",
                "Why?",
                *extra,
            ],
            generator=ScriptedGenerator(replies),
        )
    return rc, buf.getvalue()


def test_the_cli_finds_the_files_beside_the_trace(proj: Path) -> None:
    fill(proj)
    rc, out = run_ask(proj, [reply("value", [cite("rule:R2", "A config has at most one")])])
    assert rc == 0 and json.loads(out)["rationale"][0]["status"] == "approved"


def test_the_cli_flags_override_the_discovery(
    proj: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    fill(proj)
    other = tmp_path_factory.mktemp("other")
    (other / "mine.json").write_text(
        rfile([note("R5", text_of("R5"), "My own override reason.", None)])
    )
    r = reply("value", [cite("rule:R5", "The worker drains")])
    rc, out = run_ask(
        proj, [r], "--rationale", str(other / "mine.json"), "--glossary", str(other / "g.json")
    )
    body = json.loads(out)
    assert rc == 0 and body["rationale"] == [
        {
            "rule": "R5",
            "status": "approved",
            "via": None,
            "why": "My own override reason.",
            "tradeoff": None,
            "owner": "ann",
            "approved_by": "ann",
        }
    ]


def test_the_cli_exits_2_on_an_invalid_file(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (proj / "REQUIREMENTS.glossary.json").write_text("{nope")
    rc, _ = run_ask(proj, [reply("not_specified")])
    assert rc == 2 and "bad_glossary" in capsys.readouterr().err


def test_without_trace_nothing_applies(proj: Path) -> None:
    fill(proj)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = A.main(
            ["--spec", "tests.spec.racy:RUNS_SPEC", "--json", "Why?"],
            generator=ScriptedGenerator([reply("not_specified")]),
        )
    assert rc == 0 and "rationale" not in json.loads(buf.getvalue())


# ---- the implementation report (D11) -------------------------------------------------------------


FIX = Path(__file__).resolve().parent / "fixtures" / "implementation"


@pytest.fixture
def impl_world(tmp_path: Path):
    import importlib.util
    import sys

    sp = importlib.util.spec_from_file_location("impl_fixture_spec_g", FIX / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["impl_fixture_spec_g"] = mod
    sp.loader.exec_module(mod)
    app = tmp_path / "app"
    shutil.copytree(FIX / "app", app)
    wf = tmp_path / "wf.json"
    wf.write_text((FIX / "workflows.json").read_text())
    capture(
        app, tmp_path / "inv", extract=json.loads((FIX / "extract.json").read_text()), workflows=wf
    )
    return mod, impl.load_inventory(tmp_path / "inv", app), tmp_path


def test_the_report_gains_terms_rationale_and_glossary_only_with_notes(impl_world) -> None:
    mod, inv, tmp = impl_world
    trace = mod.trace()
    plain = impl.build_report(mod.SPEC, trace, inv)
    assert impl.build_report(mod.SPEC, trace, inv, notes=None) == plain
    r2 = next(r for r in trace.rules if r.id == "R2")
    names = rt.notes_names(trace.source)  # FIXTURE.rationale.json ...
    (tmp / names[0]).write_text(rfile([note("R2", r2.text, "Because.", None)]))
    (tmp / names[2]).write_text(gfile([term("project", "A unit of work."), term("unlisted word")]))
    (tmp / "spec.trace.json").write_text(trace.to_json())
    notes = gl.discover_notes(trace, tmp / "spec.trace.json")
    rich = impl.build_report(mod.SPEC, trace, inv, notes=notes)
    rules = {e["id"]: e for e in rich["rules"]}
    assert rules["R2"]["rationale"] == "current" and rules["R1"]["rationale"] == "none"
    assert all("terms" in e and "rationale" in e for e in rich["rules"])
    assert (
        "project" in rich["glossary"]
        and rich["glossary"]["project"]["definition"] == "A unit of work."
    )
    assert "unlisted-word" not in rich["glossary"]  # only terms a reported rule uses
    for slug in rich["glossary"]:
        assert any(slug in e["terms"] for e in rich["rules"])
    stripped = json.loads(json.dumps(rich))
    for e in stripped["rules"]:
        e.pop("terms")
        e.pop("rationale")
    stripped.pop("glossary")
    assert stripped == plain
    assert impl.report_json(stripped) == impl.report_json(plain)  # byte for byte


def test_the_implementation_cli_reads_the_files_and_reports_bad_ones(
    impl_world, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    mod, inv, tmp = impl_world
    (tmp / "spec.trace.json").write_text(mod.trace().to_json())
    monkeypatch.syspath_prepend(str(FIX))
    base = [
        "--spec",
        "fixture_spec:SPEC",
        "--trace",
        str(tmp / "spec.trace.json"),
        "--inventory",
        str(tmp / "inv"),
        "--app-root",
        str(tmp / "app"),
        "--json",
    ]
    assert impl.main(base) == 0
    assert "glossary" not in json.loads(capsys.readouterr().out)
    gname = rt.notes_names(mod.trace().source)[2]
    (tmp / gname).write_text(gfile([term("project")]))
    assert impl.main(base) == 0
    assert "glossary" in json.loads(capsys.readouterr().out)
    (tmp / gname).write_text("{")
    assert impl.main(base) == 2
    assert "bad_glossary" in capsys.readouterr().err


# ---- Studio: API v1.23 ---------------------------------------------------------------------------


def studio_project(tmp_path: Path):
    from tests.studio.test_server import make_project

    root = make_project(tmp_path / "p", repo=False)
    trace = Trace.load(root / "spec.trace.json")
    return root, trace


def test_files_hash_counts_the_files_only_while_they_exist(tmp_path: Path) -> None:
    from lattice.studio.server import files_hash

    root, trace = studio_project(tmp_path)
    h0 = files_hash(root)
    assert files_hash(root) == h0
    r2 = trace.rules[1]
    (root / "REQUIREMENTS.rationale.json").write_text(rfile([note("R2", r2.text, "Why.", None)]))
    h1 = files_hash(root)
    assert h1 != h0
    (root / "REQUIREMENTS.rationale.json").write_text(rfile([note("R2", r2.text, "Why 2.", None)]))
    assert files_hash(root) not in (h0, h1)
    (root / "REQUIREMENTS.rationale.json").unlink()
    assert files_hash(root) == h0  # absent again: what it always was
    (root / "REQUIREMENTS.glossary.proposed.json").write_text(gfile([]))
    assert files_hash(root) != h0


def test_the_model_has_the_keys_only_with_files(tmp_path: Path) -> None:
    from lattice.studio.server import Studio

    root, trace = studio_project(tmp_path)
    st = Studio(root, model="stub", generator=ScriptedGenerator([]))
    m0 = st.get_model()
    assert "rationale" not in m0 and "glossary" not in m0
    assert all("rationale_status" not in r for r in m0["rules"])
    r2, r3 = trace.rules[1], trace.rules[2]
    (root / "REQUIREMENTS.rationale.json").write_text(
        rfile(
            [
                note("R2", r2.text, "Why R2.", "Cost.", owner="ann", by="bob"),
                note("R9", "gone", "Old.", None),
            ]
        )
    )
    (root / "REQUIREMENTS.rationale.proposed.json").write_text(
        rfile(
            [
                note("R3", r3.text, "A proposal.", None, status="proposed", author="hand"),
            ]
        )
    )
    (root / "REQUIREMENTS.glossary.json").write_text(gfile([term("note", "A saved text.")]))
    m = st.get_model()
    assert m["rationale"]["file"] == "REQUIREMENTS.rationale.json"
    assert m["rationale"]["metrics"]["RC"]["n"] == 1 and m["rationale"]["metrics"]["RP"] == 1
    states = {n["rule"]: n for n in m["rationale"]["notes"]}
    assert states["R2"]["why"] == "Why R2." and states["R9"]["state"] == "stale"
    assert "why" not in states["R9"] and "A proposal." not in json.dumps(m["rationale"])
    assert {r["id"]: r["rationale_status"] for r in m["rules"]}["R2"] == "approved"
    assert {r["id"]: r["rationale_status"] for r in m["rules"]}["R3"] == "proposed_only"
    g = m["glossary"]
    assert g["file"] == "REQUIREMENTS.glossary.json" and g["metrics"]["GA"] == 1
    assert [t["slug"] for t in g["terms"]] == ["note"]
    assert any(f["code"] == "G001" for f in g["findings"])
    # the lint views are untouched by the glossary
    assert all(not str(x.get("code", "")).startswith("G") for x in m["views"]["lint_gaps"])


def test_an_invalid_file_is_an_error_in_the_model_and_422_on_ask(tmp_path: Path) -> None:
    from lattice.studio.server import ApiError, Studio

    root, _ = studio_project(tmp_path)
    (root / "REQUIREMENTS.rationale.json").write_text("{nope")
    st = Studio(root, model="stub", generator=ScriptedGenerator([]))
    m = st.get_model()
    assert m["rationale"]["error"].startswith("bad_rationale") and "metrics" not in m["rationale"]
    assert all("rationale_status" not in r for r in m["rules"])
    with pytest.raises(ApiError) as e:
        st.ask({"question": "Why?"})
    assert int(e.value.status) == 422 and str(e.value.detail).startswith("bad_rationale")


def test_ask_carries_the_rationale_key_and_the_digest_sections(tmp_path: Path) -> None:
    from lattice.studio.server import Studio

    root, trace = studio_project(tmp_path)
    r2 = trace.rules[1]
    (root / "REQUIREMENTS.rationale.json").write_text(
        rfile(
            [
                note(
                    "R2",
                    r2.text,
                    "Notes are short so lists stay readable.",
                    "Long text is refused.",
                )
            ]
        )
    )
    ans = reply("value", [cite("rule:R2", r2.text[:25])])
    gen = ScriptedGenerator([ans])
    st = Studio(root, model="stub", generator=gen)
    out = st.ask({"question": "Why is a note limited?"})
    assert out["rationale"][0]["rule"] == "R2" and out["rationale"][0]["status"] == "approved"
    assert "[rationale:R2]" in gen.calls[0][0]
    # no files: no key
    (root / "REQUIREMENTS.rationale.json").unlink()
    gen2 = ScriptedGenerator([ans])
    out2 = Studio(root, model="stub", generator=gen2).ask({"question": "Why?"})
    assert "rationale" not in out2 and "[rationale:" not in gen2.calls[0][0]


# ---- the chat sessions ---------------------------------------------------------------------------


def test_sessions_allow_the_tools_and_add_the_sentence_only_with_files(tmp_path: Path) -> None:
    from lattice.studio import session as S

    root, _ = studio_project(tmp_path)
    for mode in ("ask", "explain", "change", "rebuild"):
        assert S.notes_tools(root, mode) == ()
    base_prompt = S.system_prompt("ask", root)
    assert "rationale_get" not in base_prompt
    (root / "REQUIREMENTS.rationale.proposed.json").write_text(rfile([]))
    for mode in ("ask", "explain", "change"):
        assert S.notes_tools(root, mode) == ("mcp__lattice__rationale_get",)
        assert "do not offer one as the owner's" in S.system_prompt(mode, root)
    assert S.notes_tools(root, "rebuild") == ()
    (root / "REQUIREMENTS.glossary.json").write_text(gfile([]))
    assert S.notes_tools(root, "ask") == (
        "mcp__lattice__rationale_get",
        "mcp__lattice__glossary_get",
    )
    argv = S.build_argv(
        claude_bin="c",
        mode="ask",
        text="hi",
        model="m",
        mcp_config=Path("m"),
        settings=Path("s"),
        prompt="p",
        extra_allowed=S.notes_tools(root, "ask"),
    )
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert "mcp__lattice__rationale_get" in allowed and "mcp__lattice__glossary_get" in allowed
    plain = S.build_argv(
        claude_bin="c",
        mode="ask",
        text="hi",
        model="m",
        mcp_config=Path("m"),
        settings=Path("s"),
        prompt="p",
    )
    assert "rationale_get" not in " ".join(plain)
    # the profiles themselves never change
    assert not any("rationale" in t for p in S.MODE_PROFILES.values() for t in p.allowed)


def test_the_labels_name_the_tools() -> None:
    from lattice.studio import session as S

    assert S.tool_label("mcp__lattice__rationale_get", {}) == "Read the rationale"
    assert S.tool_label("mcp__lattice__glossary_get", {}) == "Read the glossary"


# ---- unused helpers kept for the readers of this file --------------------------------------------


def test_helper_hashes_agree_with_the_library() -> None:
    assert anchor(text_of("R2")) == rt.anchor_of(text_of("R2"))
    assert appr_sha("a", None, "o") == rt.approved_sha("a", None, "o")
    assert TRACE_RULES[1].id == "R2"
