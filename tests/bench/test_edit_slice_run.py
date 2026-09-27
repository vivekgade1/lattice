"""Thin-slice plan node E3: the edit-slice harness (``bench/edit_slice/run.py``).

* fake-LLM runs of both arms: a correct answer, broken answers then a repair, malformed
  output, a refusal, and a silent answer the visible smoke run cannot catch;
* the dry run: every hand-written reference, fed through its arm, scores correct (the
  tickets current Lattice cannot express are reported as gaps instead);
* no prompt or repair message carries the tickets' trap notes or an oracle case name;
* the metrics (tokens, cost, edit size, request stats), the report and the estimate.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from bench.edit_slice import prompts, run, tickets
from bench.edit_slice.lattice_reference import (
    GAPS,
    has_lattice_reference,
    lattice_reference_files,
)
from bench.edit_slice.oracle import ticket_oracle
from bench.edit_slice.reference import reference_files
from bench.edit_slice.report import render, summary
from bench.edit_slice.tickets import TICKETS
from lattice.llm.client import Generation, GenerationRefused, ScriptedGenerator, Usage

TICKET_IDS = [t.id for t in TICKETS]


def _p_answer(ticket_id: str, **replace: str) -> str:
    files = {prompts.APP_PREFIX + rel: src for rel, src in reference_files(ticket_id).items()}
    for old, new in replace.items():
        files = {n: s.replace(old, new) for n, s in files.items()}
    return prompts.render_files(files)


def _l_answer(ticket_id: str, old: str = "", new: str = "") -> str:
    files = lattice_reference_files(ticket_id)
    if old:
        assert old in files["items.lattice.py"]
        files["items.lattice.py"] = files["items.lattice.py"].replace(old, new)
    return prompts.render_files(files)


def _sent(gen: ScriptedGenerator) -> list[str]:
    """Every text the harness sent: system prompts and user turns."""
    out = []
    for system, messages in gen.calls:
        out.append(system)
        out += [m["content"] for m in messages if m["role"] == "user"]
    return out


# ---- the answer format ----------------------------------------------------------------------


def test_parse_files_reads_every_heading_and_block():
    text = (
        "Here you go.\n\n### app/models.py\n```python\nA = 1\n```\n\n"
        "### `app/api/routes/items.py`\n\n```py\nB = 2\n\n```\nThanks"
    )
    assert prompts.parse_files(text) == {
        "app/models.py": "A = 1\n",
        "app/api/routes/items.py": "B = 2\n\n",
    }
    assert prompts.parse_files("```python\nA = 1\n```") == {}
    files = {"items.lattice.py": "# lattice: v1\n", "profile.py": "X = 1\n"}
    assert prompts.parse_files(prompts.render_files(files)) == files


# ---- arm P ----------------------------------------------------------------------------------


def test_arm_p_scores_a_correct_answer():
    gen = ScriptedGenerator([_p_answer("t6_stats")])
    r = run.run_arm("P", "t6_stats", gen)
    assert (r.outcome, r.rounds, r.failure, r.round_problems) == ("correct", 1, None, [None])
    assert r.edit_files == ["api/routes/items.py", "models.py"]
    assert r.lines_added > 0 and r.lines_changed == r.lines_added + r.lines_removed
    assert r.code_lines > 0 and r.mean_queries is not None and r.mean_queries > 0
    assert r.cases["equal"] > 0 and r.cases_specific == 6


def test_arm_p_repairs_on_a_startup_traceback_then_a_smoke_500():
    broken_import = _p_answer(
        "t6_stats", **{"class ItemStats(SQLModel):": "class ItemStats(SQLModel)"}
    )
    crashing = _p_answer(
        "t6_stats",
        **{"return ItemStats(count=count": "return ItemStats(count=count // 0 + count"},
    )
    gen = ScriptedGenerator([broken_import, crashing, _p_answer("t6_stats")])
    r = run.run_arm("P", "t6_stats", gen)
    assert (r.outcome, r.rounds, r.round_problems) == ("correct", 3, ["start", "smoke", None])
    first, second = (m[-1]["content"] for m in (gen.calls[1][1], gen.calls[2][1]))
    assert "SyntaxError" in first and "failed to start" in first
    assert "ZeroDivisionError" in second and "GET /api/v1/items/stats as alice -> 500" in second
    _assert_no_oracle_text(_sent(gen), "t6_stats")


def test_arm_p_malformed_output_is_loud_after_the_repair_budget():
    gen = ScriptedGenerator(["Sure! Here is the change:\n```python\nprint(1)\n```"] * 3)
    r = run.run_arm("P", "t1_search", gen)
    assert (r.outcome, r.rounds, r.failure) == ("loud", 3, "no_code")
    assert r.round_problems == ["no_code"] * 3 and r.cases == {}
    assert run.NO_FILES in gen.calls[1][1][-1]["content"]


def test_arm_p_refuses_a_path_outside_the_app():
    gen = ScriptedGenerator(["### ../evil.py\n```python\nX = 1\n```", _p_answer("t1_search")])
    r = run.run_arm("P", "t1_search", gen)
    assert (r.outcome, r.round_problems) == ("correct", ["bad_file", None])


def test_arm_p_start_state_of_a_follow_up_ticket_is_the_base_reference():
    _, user = run.p_prompt("t4_restore")
    assert "NOT_DELETED" in user  # the soft-delete reference's items.py
    assert "deleted_at: datetime | None" in user  # ... and its models.py


# ---- arm L ----------------------------------------------------------------------------------


def test_arm_l_scores_a_correct_answer():
    gen = ScriptedGenerator([_l_answer("t6_stats")])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.rounds, r.failure) == ("correct", 1, None)
    assert r.edit_files == ["items.lattice.py", "profile.py"]
    assert r.code_lines > 0 and r.mean_queries is not None


def test_arm_l_repairs_on_checker_diagnostics():
    broken = _l_answer(
        "t6_stats", 'agg.max(col("created_at"), nulls="skip")', 'agg.max(col("created_at"))'
    )
    gen = ScriptedGenerator([broken, _l_answer("t6_stats")])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.rounds, r.round_problems) == ("correct", 2, ["static", None])
    feedback = gen.calls[1][1][-1]["content"]
    assert "items.lattice.py:" in feedback and "L421" in feedback
    assert "L823" not in feedback  # warnings on untouched code are not repair feedback
    _assert_no_oracle_text(_sent(gen), "t6_stats")


def test_arm_l_malformed_output_and_bad_file_names_are_loud():
    gen = ScriptedGenerator(["no code", "### items.py\n```python\nX = 1\n```", "nothing"])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.failure, r.round_problems) == (
        "loud",
        "no_code",
        ["no_code", "bad_file", "no_code"],
    )


def test_arm_l_keeps_earlier_files_across_rounds():
    """A repair that returns only the program keeps the profile the model sent before."""
    profile_only = prompts.render_files(
        {"profile.py": lattice_reference_files("t6_stats")["profile.py"]}
    )
    program_only = prompts.render_files(
        {"items.lattice.py": lattice_reference_files("t6_stats")["items.lattice.py"]}
    )
    gen = ScriptedGenerator([profile_only, program_only])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.round_problems) == ("correct", ["smoke", None])
    assert "GET /api/v1/items/stats as alice ->" in gen.calls[1][1][-1]["content"]


def test_a_silent_answer_passes_the_smoke_run_and_is_scored_silent():
    """Admins get everyone's stats: the visible smoke run (as a normal user) passes, so
    there is no repair; the hidden oracle finds it."""
    leaky = _l_answer(
        "t6_stats",
        '    mine = join(visible, caller, on=["owner_id"])\n    stats = group(\n        mine,',
        "    stats = group(\n        visible,",
    )
    gen = ScriptedGenerator([leaky])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.rounds) == ("silent", 1)
    assert "t6.admin_own_only" in r.failed_cases
    assert r.mean_queries is None  # request stats are kept for correct runs only


def test_the_follow_up_tickets_lattice_start_state_is_the_verified_t3_solution():
    programs, profile = run.l_start(tickets.ticket("t4_restore"))
    t3 = lattice_reference_files("t3_soft_delete")
    assert programs["items.lattice.py"] == t3["items.lattice.py"] and profile == t3["profile.py"]


# ---- refusals, tokens and cost --------------------------------------------------------------


class _Priced:
    """A generator that answers like the API: usage, latency and the served model."""

    def __init__(self, texts: Sequence[str], refuse: bool = False) -> None:
        self.texts, self.refuse = list(texts), refuse
        self.calls: list[tuple[str, list[Mapping[str, str]]]] = []

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        self.calls.append((system, list(messages)))
        usage = Usage(1000, 200, cache_read_input_tokens=400)
        if self.refuse:
            raise GenerationRefused("refused", usage=usage, latency_s=1.5, model="claude-haiku-4-5")
        return Generation(
            self.texts.pop(0), usage, 2.0, "end_turn", model="claude-haiku-4-5", refusals=1
        )


def test_tokens_and_cost_use_the_bench_pricing():
    r = run.run_arm("P", "t1_search", _Priced([_p_answer("t1_search")]), model="claude-haiku-4-5")
    assert r.outcome == "correct"
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens) == (1400, 200, 400)
    # 1000 uncached x $1 + 400 cached x $0.1 + 200 out x $5, per million
    assert r.cost_usd == pytest.approx((1000 + 40 + 1000) / 1e6)
    assert (r.served_model, r.refused_attempts, r.llm_s) == ("claude-haiku-4-5", 1, 2.0)
    assert r.wall_s >= r.eval_s > 0


def test_a_refusal_is_loud_and_its_tokens_are_counted():
    r = run.run_arm("L", "t1_search", _Priced([], refuse=True), model="claude-haiku-4-5")
    assert (r.outcome, r.failure, r.rounds, r.round_problems) == ("loud", "refused", 1, ["refused"])
    assert r.input_tokens == 1400 and r.cost_usd > 0


# ---- the dry run ----------------------------------------------------------------------------


@pytest.mark.parametrize("arm", run.ARMS)
@pytest.mark.parametrize("ticket_id", TICKET_IDS)
def test_the_dry_run_scores_every_reference_correct(arm, ticket_id):
    r = run.dry_run(arm, ticket_id)
    if arm == "L" and ticket_id in GAPS:
        assert r.outcome == "not_expressible" and r.detail == GAPS[ticket_id]
        return
    assert (r.outcome, r.rounds, r.failed_cases) == ("correct", 1, []), r.detail
    assert r.model == "reference" and r.dry_run and r.cost_usd == 0


def test_every_ticket_is_expressible_in_lattice():
    assert GAPS == {}
    assert all(has_lattice_reference(t) for t in TICKET_IDS)


# ---- nothing hidden reaches a prompt --------------------------------------------------------


def _trap_notes() -> list[str]:
    doc = tickets.__doc__ or ""
    return [line.strip() for line in doc.splitlines() if len(line.strip()) >= 20]


def _assert_no_oracle_text(texts: Sequence[str], ticket_id: str) -> None:
    doc = (tickets.__doc__ or "").strip()
    names = [c.name for c in ticket_oracle(ticket_id).cases]
    for text in texts:
        assert doc not in text
        for line in _trap_notes():
            assert line not in text, line
        for name in names:
            assert name not in text, name


@pytest.mark.parametrize("ticket_id", TICKET_IDS)
def test_no_prompt_carries_the_trap_notes_or_a_case_name(ticket_id):
    texts = [
        *run.p_prompt(ticket_id),
        *run.l_prompt(ticket_id),
        *run.l_prompt(ticket_id, hints=True),
    ]
    _assert_no_oracle_text(texts, ticket_id)
    t = tickets.ticket(ticket_id)
    for text in texts[1::2]:
        assert t.text.strip() in text  # the same ticket text in both arms


def test_the_smoke_requests_come_from_the_ticket_text():
    """Each smoke request's query values are quoted from its ticket; a request that
    answers as the ticket says shows only its status (never a body to compare)."""
    for ticket_id, requests in run.smoke.SMOKE.items():
        text = tickets.ticket(ticket_id).text
        for req in requests:
            for value in (req.params or {}).values():
                assert str(value) in text, (ticket_id, value)
    gen = ScriptedGenerator([_l_answer("t6_stats")])
    r = run.run_arm("L", "t6_stats", gen)
    assert r.outcome == "correct" and r.detail == ""


# ---- the report and the estimate ------------------------------------------------------------


def _row(arm: str, outcome: str, cost: float, **kw: object) -> dict:
    return {
        **json.loads(
            run.RunRecord("t1_search", arm, "m", outcome=outcome, cost_usd=cost).to_json()
        ),
        **kw,
    }


def test_the_report_tabulates_model_by_arm():
    rows = [
        _row("P", "correct", 0.02, rounds=1, lines_changed=10),
        _row("P", "silent", 0.02, rounds=1, lines_changed=4),
        _row("L", "loud", 0.03, rounds=3),
        _row("L", "correct", 0.03, rounds=1),
    ]
    s = {x["arm"]: x for x in summary(rows)}
    assert (s["P"]["correct"], s["P"]["silent"], s["P"]["cost_per_correct"]) == (
        1,
        1,
        pytest.approx(0.04),
    )
    assert (s["L"]["loud"], s["L"]["mean_rounds"]) == (1, 2.0)
    table = render(rows)
    assert "| m | P | 2 | 1 | 0 | 1 |" in table and "| t1_search | silent correct" not in table


def test_the_estimate_counts_characters_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    est = run.estimate(["claude-haiku-4-5"], ["t6_stats"], reps=3)
    rows = {r["arm"]: r for r in est["rows"]}
    system, user = run.l_prompt("t6_stats")
    assert rows["L"]["prompt_tokens"] == round((len(system) + len(user)) / run.CHARS_PER_TOKEN)
    assert est["runs"] == 6 and est["total_usd"] > 0 and not est["counted"]
    assert "6 runs, expected total $" in run.render_estimate(est)
