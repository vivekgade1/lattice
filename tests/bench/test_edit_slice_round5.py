"""Round 5 (spec 2026-09-28 and its §7 amendment): arms Q, L+ and Q+ in the edit-slice
harness.

* arm Q: arm P's flow, arm Q's system prompt, and pycheck's findings (code, line,
  message, fix hint) as repair feedback after the smoke run; findings on the final round
  do not change scoring (the oracle decides);
* arm L shows the checker's machine fix (``Diagnostic.fix``) in its repair feedback;
* arms L+ and Q+: the base arm's prompt plus the two aids, and nothing else; the aids
  are fair (same retrieval, same exclusion, same count; tables of the same length);
* the dry run feeds P's references through Q and Q+ (correct, no findings) and the
  Lattice references through L+; ``--estimate`` and ``--arm`` take all five arms.
"""

from __future__ import annotations

import glob
import json

import pytest

from bench.edit_slice import aids, build_aids, prompts, run
from bench.edit_slice.lattice_reference import lattice_reference_files
from bench.edit_slice.reference import reference_files
from bench.edit_slice.tickets import ROUND4_TICKETS, ticket
from lattice.diagnostics import Diagnostic, Fix, Severity, Span, render_for_llm
from lattice.llm.client import ScriptedGenerator

ROUND4_IDS = [t.id for t in ROUND4_TICKETS]
HELD_OUT = [tid for tid in ROUND4_IDS if tid not in aids.CORPUS]  # t13-t30

OWNER_BRANCH = """    if not current_user.is_superuser:
        count_statement = count_statement.where(Item.owner_id == current_user.id)
        statement = statement.where(Item.owner_id == current_user.id)
"""


def _p_answer(ticket_id: str, old: str = "", new: str = "") -> str:
    files = {prompts.APP_PREFIX + rel: src for rel, src in reference_files(ticket_id).items()}
    if old:
        (name,) = [n for n, s in files.items() if old in s]
        files[name] = files[name].replace(old, new, 1)
    return prompts.render_files(files)


def _repairs(gen: ScriptedGenerator) -> list[str]:
    return [m["content"] for _, messages in gen.calls for m in messages[1:] if m["role"] == "user"]


# ---- the arms ------------------------------------------------------------------------------


def test_the_round5_arms_are_known_and_rounds_1_to_4_are_unchanged():
    assert run.ARMS == ("P", "L")
    assert run.ROUND5_ARMS == ("P", "L", "Q", "L+", "Q+")
    assert [run.base_arm(a) for a in run.ROUND5_ARMS] == ["P", "L", "Q", "L", "Q"]
    assert [run.language(a) for a in run.ROUND5_ARMS] == ["P", "L", "P", "L", "P"]


def test_arm_q_prompt_is_arm_p_user_message_with_the_q_system_prompt():
    system, user = run.arm_prompt("Q", "t1_search")
    p_system, p_user = run.p_prompt("t1_search")
    assert user == p_user
    assert system == prompts.q_system_prompt(ticket("t1_search"))
    assert system.startswith(p_system.rstrip("\n")) and len(system) > len(p_system)


def test_arm_q_scores_a_correct_answer_with_no_findings():
    gen = ScriptedGenerator([_p_answer("t1_search")])
    record = run.run_arm("Q", "t1_search", gen)
    assert record.outcome == "correct", record.detail
    assert record.rounds == 1 and record.round_findings == [[]]
    assert gen.calls[0][0] == prompts.q_system_prompt(ticket("t1_search"))


def test_arm_q_repairs_on_pycheck_findings():
    gen = ScriptedGenerator([_p_answer("t1_search", OWNER_BRANCH), _p_answer("t1_search")])
    record = run.run_arm("Q", "t1_search", gen)
    assert record.outcome == "correct", record.detail
    assert record.rounds == 2
    assert record.round_findings == [["PY801"], []]
    assert record.round_problems[0] in ("check", "smoke")
    (feedback,) = _repairs(gen)
    assert "The guarantee checks found problems" in feedback
    # the same format family as arm L's checker diagnostics: code, place, message, fix
    assert "- PY801 at app/api/routes/items.py, line " in feedback
    assert ". Fix: filter by the caller" in feedback


def test_arm_q_findings_on_the_final_round_do_not_change_scoring():
    gen = ScriptedGenerator([_p_answer("t1_search", OWNER_BRANCH)])
    record = run.run_arm("Q", "t1_search", gen, max_repairs=0)
    p = run.run_arm("P", "t1_search", ScriptedGenerator([_p_answer("t1_search", OWNER_BRANCH)]),
                    max_repairs=0)  # fmt: skip
    assert record.round_findings == [["PY801"]]
    assert record.outcome == p.outcome and record.outcome in ("silent", "loud")
    assert record.cases == p.cases


def test_arm_p_runs_no_checks():
    gen = ScriptedGenerator([_p_answer("t1_search", OWNER_BRANCH)])
    record = run.run_arm("P", "t1_search", gen, max_repairs=0)
    assert record.round_findings == []


def test_arm_q_artefacts_record_the_findings(tmp_path):
    gen = ScriptedGenerator([_p_answer("t1_search", OWNER_BRANCH), _p_answer("t1_search")])
    record = run.run_arm("Q", "t1_search", gen, model="m", artefact_root=tmp_path)
    meta = json.loads((tmp_path / "m" / "Q" / "t1_search" / "rep0" / "round1" / "round.json")
                      .read_text())  # fmt: skip
    assert meta["findings"] == ["PY801"] and record.artefacts


# ---- arm L's exact fixes ---------------------------------------------------------------------


def test_render_for_llm_can_show_the_exact_fix():
    fix = Fix(Span(3, 5), Span(3, 9), "row.title")
    d = Diagnostic("L401", Severity.ERROR, Span(3, 5), "no input rows", "read the row", fix)
    assert render_for_llm([d]) == "- L401 at line 3, col 5: no input rows. Fix: read the row"
    shown = render_for_llm([d], fixes=True).splitlines()
    assert shown[0] == "- L401 at line 3, col 5: no input rows. Fix: read the row"
    assert shown[1] == "  Exact fix: replace line 3 col 5 to line 3 col 9 with: row.title"


def test_arm_l_repair_feedback_shows_the_checkers_exact_fix():
    good = lattice_reference_files("t11_title_trim")
    old = "updated = update(allowed, title=title, description=description)"
    bad = dict(good)
    bad["items.lattice.py"] = good["items.lattice.py"].replace(
        old,
        old + '\n    copy = insert(Item, owner=user, title=col("title"), '
        "description=description, created_at=fn.now())",
        1,
    )
    assert bad != good
    gen = ScriptedGenerator([prompts.render_files(bad), prompts.render_files(good)])
    record = run.run_arm("L", "t11_title_trim", gen)
    assert record.round_problems[0] == "static"
    (feedback,) = _repairs(gen)
    assert "L401 at line" in feedback
    assert "  Exact fix: replace line" in feedback and "with: updated.title" in feedback


# ---- the aids: retrieval -----------------------------------------------------------------------


@pytest.mark.parametrize("ticket_id", ROUND4_IDS)
def test_retrieval_never_returns_the_current_ticket_or_a_held_out_one(ticket_id):
    got = aids.retrieve(ticket_id)
    assert len(got) == aids.EXAMPLES == 2
    assert ticket_id not in got
    assert set(got) <= set(aids.CORPUS)
    assert not set(got) & set(HELD_OUT)


def test_the_corpus_is_t1_to_t12_only():
    assert aids.CORPUS == tuple(ROUND4_IDS[:12])
    assert all(tid.split("_")[0] in {f"t{i}" for i in range(1, 13)} for tid in aids.CORPUS)


def test_retrieval_is_tf_idf_similarity_and_deterministic():
    # t4 (restore a soft-deleted item) is closest to t3 (soft delete)
    assert aids.retrieve("t4_restore")[0] == "t3_soft_delete"
    assert aids.retrieve("t3_soft_delete")[0] == "t4_restore"
    scores = [s for _, s in aids.similarities("t25_bulk_delete_items")]
    assert scores == sorted(scores, reverse=True)
    assert aids.retrieve("t25_bulk_delete_items") == aids.retrieve("t25_bulk_delete_items")


@pytest.mark.parametrize("ticket_id", ["t1_search", "t4_restore", "t15_duplicate_item",
                                       "t19_task_summary", "t30_bulk_delete_notes"])  # fmt: skip
def test_both_arms_get_the_same_examples_in_their_own_language(ticket_id):
    lattice = aids.worked_examples(ticket_id, "lattice")
    python = aids.worked_examples(ticket_id, "python")
    for tid in aids.retrieve(ticket_id):
        assert ticket(tid).title in lattice and ticket(tid).title in python
    assert lattice.startswith(aids.EXAMPLES_INTRO.format(n=2))
    assert python.startswith(aids.EXAMPLES_INTRO.format(n=2))
    assert lattice.count("### Example ") == python.count("### Example ") == 2
    assert "# lattice: v1" in lattice and "### app/" not in lattice
    assert "### app/" in python and "# lattice: v1" not in python
    # every example is the verified reference answer the dry run feeds through the arm
    for tid in aids.retrieve(ticket_id):
        assert run.reference_answer("L", tid).strip() in lattice
        assert run.reference_answer("P", tid).strip() in python


# ---- the aids: the mistakes tables -------------------------------------------------------------


def test_the_mistakes_tables_are_committed_and_the_same_size():
    lattice, python = aids.mistakes_section("lattice"), aids.mistakes_section("python")
    assert abs(len(lattice) - len(python)) <= 0.15 * max(len(lattice), len(python))
    for lang, text in (("lattice", lattice), ("python", python)):
        data = json.loads(aids.TABLE_FILES[lang].read_text())
        assert data["chars"] == len(text) <= data["chars_budget"]
        assert 1 <= len(data["rows"]) <= build_aids.MAX_ROWS
        for row in data["rows"]:
            assert len(row["mistake"]) <= build_aids.CELL_CHARS
            assert len(row["fix"]) <= build_aids.CELL_CHARS
    assert all(r.code.startswith("L") for r in aids.load_table("lattice"))
    assert all(r.code.startswith("PY") for r in aids.load_table("python"))


def test_the_tables_share_their_wording_but_the_rows():
    lattice, python = aids.mistakes_section("lattice"), aids.mistakes_section("python")
    assert lattice.startswith(aids.MISTAKES_INTRO) and python.startswith(aids.MISTAKES_INTRO)


def test_fit_keeps_the_longest_prefix_within_the_budget():
    rows = [aids.MistakeRow(f"L{i}", "m" * 50, "f" * 50, 1) for i in range(5)]
    budget = len(aids.render_table(rows[:3]))
    assert build_aids.fit(rows, budget) == rows[:3]
    assert build_aids.fit(rows, budget - 1) == rows[:2]


def test_cap_cuts_at_a_word_boundary():
    assert build_aids.cap("a  b\nc") == "a b c"
    text = "word " * 100
    out = build_aids.cap(text, 40)
    assert len(out) <= 40 and out.endswith(" …")


@pytest.mark.skipif(
    not glob.glob("bench/results/edit_slice/round4_*_L.jsonl.artefacts"),
    reason="the round-4 artefacts are not checked in",
)
def test_the_committed_tables_are_what_the_script_builds():
    assert build_aids.main(["--check"]) == 0


# ---- the aided prompts: only the intended components differ ------------------------------------


@pytest.mark.parametrize("ticket_id", ["t1_search", "t12_transfer", "t16_admin_audit",
                                       "t21_completed_at", "t29_tag_summary"])  # fmt: skip
def test_the_aided_prompts_add_exactly_the_two_aids(ticket_id):
    l_system, l_user = run.arm_prompt("L", ticket_id)
    q_system, q_user = run.arm_prompt("Q", ticket_id)
    lp_system, lp_user = run.arm_prompt("L+", ticket_id)
    qp_system, qp_user = run.arm_prompt("Q+", ticket_id)
    assert lp_system == run.with_section(l_system, aids.mistakes_section("lattice"))
    assert qp_system == run.with_section(q_system, aids.mistakes_section("python"))
    assert lp_user == aids.worked_examples(ticket_id, "lattice") + "\n\n" + l_user
    assert qp_user == aids.worked_examples(ticket_id, "python") + "\n\n" + q_user
    # the base prompts are untouched by the aids
    assert lp_system.startswith(l_system.rstrip("\n")) and qp_system.startswith(
        q_system.rstrip("\n")
    )
    assert lp_user.endswith(l_user) and qp_user.endswith(q_user)


def test_the_aided_arms_record_their_examples():
    gen = ScriptedGenerator([_p_answer("t1_search")])
    record = run.run_arm("Q+", "t1_search", gen)
    assert record.examples == aids.retrieve("t1_search")
    assert record.outcome == "correct" and record.round_findings == [[]]
    assert gen.calls[0][0] == run.arm_prompt("Q+", "t1_search")[0]
    assert run.run_arm("Q", "t1_search", ScriptedGenerator([_p_answer("t1_search")])).examples == []


# ---- the dry run -------------------------------------------------------------------------------


@pytest.mark.parametrize("ticket_id", ROUND4_IDS)
def test_the_python_references_pass_arm_q_with_no_findings(ticket_id):
    record = run.dry_run("Q", ticket_id)
    assert record.outcome == "correct", (record.failure, record.detail)
    assert record.round_findings == [[]], record.detail


@pytest.mark.parametrize("ticket_id", aids.CORPUS)
def test_the_references_pass_the_aided_arms(ticket_id):
    for arm in ("Q+", "L+"):
        record = run.dry_run(arm, ticket_id)
        assert record.outcome == "correct", (arm, record.failure, record.detail)
        assert record.examples == aids.retrieve(ticket_id)
        assert ticket_id not in record.examples


def test_arm_l_plus_on_a_held_out_ticket_has_no_reference_like_arm_l():
    assert run.dry_run("L+", "t13_password_reset").outcome == "no_reference"


# ---- the CLI and the estimate ------------------------------------------------------------------


def test_the_estimate_covers_all_five_arms(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    est = run.estimate(["claude-sonnet-5"], ["t1_search", "t19_task_summary"], 3,
                       arms=run.ROUND5_ARMS)  # fmt: skip
    arms = {(r["ticket"], r["arm"]) for r in est["rows"]}
    assert arms == {(t, a) for t in ("t1_search", "t19_task_summary") for a in run.ROUND5_ARMS}
    by = {(r["ticket"], r["arm"]): r for r in est["rows"]}
    for t in ("t1_search", "t19_task_summary"):
        assert by[(t, "L+")]["prompt_tokens"] > by[(t, "L")]["prompt_tokens"]
        assert by[(t, "Q+")]["prompt_tokens"] > by[(t, "Q")]["prompt_tokens"]
        assert by[(t, "Q")]["prompt_tokens"] > by[(t, "P")]["prompt_tokens"]
        assert by[(t, "Q")]["answer_tokens"] == by[(t, "P")]["answer_tokens"]
    assert est["runs"] == 10 * 3 and est["total_usd"] > 0


def test_the_cli_accepts_the_five_arms(tmp_path, capsys):
    out = tmp_path / "r.jsonl"
    code = run.main(["--arm", "Q,Q+,L+", "--tickets", "t1_search", "--dry-run-reference",
                     "--out", str(out)])  # fmt: skip
    assert code == 0
    rows = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert [(r["arm"], r["outcome"]) for r in rows] == [
        ("Q", "correct"),
        ("Q+", "correct"),
        ("L+", "correct"),
    ]
    with pytest.raises(SystemExit):
        run.main(["--arm", "X", "--tickets", "t1_search", "--dry-run-reference",
                  "--out", str(out)])  # fmt: skip
    assert run.main(["--arm", "P,L,Q,L+,Q+", "--tickets", "t1_search", "--estimate",
                     "--estimate-models", "claude-sonnet-5"]) == 0  # fmt: skip
    assert "| L+ |" in capsys.readouterr().out
