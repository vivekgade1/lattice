"""Thin-slice plan node E3: the edit-slice harness (``bench/edit_slice/run.py``).

* fake-LLM runs of both arms: a correct answer, broken answers then a repair, malformed
  output, a refusal, and a silent answer the visible smoke run cannot catch;
* the dry run: every hand-written reference, fed through its arm, scores correct (the
  tickets current Lattice cannot express are reported as gaps instead);
* no prompt or repair message carries the tickets' trap notes or an oracle case name;
* the metrics (tokens, cost, edit size, request stats), the report and the estimate;
* node N2, endpoint-scoped edits (spec 2026-09-27-lattice-surface-design.md §3): partial
  answers merged by top-level name (replace/append/delete/malformed) for both arms, arm
  L's ``profile.delta``, the ``answer_mode``/``truncated`` metrics and the report's
  truncation-rate column, and the partial dry run.
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
    """owned() for "my items": admins get everyone's stats. The visible smoke run (as a
    normal user) passes, so there is no repair; the hidden oracle finds it."""
    leaky = _l_answer("t6_stats", "own = mine(items, by=user)", "own = owned(items, by=user)")
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
    # round 3's held-out template set (t13-t18): no Lattice reference is written at all
    if arm == "L" and ticket_id in tickets.ROUND3_TEMPLATE:
        assert r.outcome == "no_reference" and r.dry_run
        return
    assert (r.outcome, r.rounds, r.failed_cases) == ("correct", 1, []), r.detail
    assert r.model == "reference" and r.dry_run and r.cost_usd == 0


def test_every_ticket_is_expressible_in_lattice_but_the_recorded_gaps():
    """The baseline six are all expressible; a held-out ticket current Lattice cannot
    express is a recorded gap (with the missing feature), not a written solution. The
    round-2 owner transfer closed the last one (t12_transfer). Round 3's held-out
    template set (t13-t18) is neither: it is held out from the language work on
    purpose, with no reference written and no gap recorded either."""
    assert set(GAPS) <= set(tickets.HELD_OUT)
    assert GAPS == {}
    assert not any(has_lattice_reference(t) or t in GAPS for t in tickets.ROUND3_TEMPLATE)
    assert all(has_lattice_reference(t) for t in tickets.BASELINE)
    assert all(
        has_lattice_reference(t) != (t in GAPS)
        for t in TICKET_IDS
        if t not in tickets.ROUND3_TEMPLATE
    )


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


# ---- artefacts and truncation ---------------------------------------------------------------


class _Stops:
    """A generator whose answers carry a stop reason (``max_tokens``: cut off)."""

    def __init__(self, answers: Sequence[tuple[str, str]]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, list[Mapping[str, str]]]] = []

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        self.calls.append((system, list(messages)))
        text, stop = self.answers.pop(0)
        return Generation(text, Usage(10, 20), 0.1, stop, model="m")


def test_every_round_records_its_stop_reason():
    gen = _Stops([("no code", "end_turn"), (_l_answer("t6_stats"), "end_turn")])
    r = run.run_arm("L", "t6_stats", gen, max_tokens=1234)
    assert (r.outcome, r.stop_reasons, r.max_tokens) == ("correct", ["end_turn", "end_turn"], 1234)


def test_a_truncated_answer_is_not_a_format_error():
    cut = _l_answer("t6_stats")[:400]  # cut off mid-block: no complete file
    gen = _Stops([(cut, "max_tokens"), (_l_answer("t6_stats"), "end_turn")])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.round_problems) == ("correct", ["truncated", None])
    assert r.stop_reasons == ["max_tokens", "end_turn"]
    feedback = gen.calls[1][1][-1]["content"]
    assert "cut off" in feedback and run.NO_FILES not in feedback
    # complete file blocks before the cut are not trusted either
    whole = _p_answer("t1_search") + "\n\nAnd one more thing"
    gen = _Stops([(whole, "max_tokens")] * 3)
    r = run.run_arm("P", "t1_search", gen)
    assert (r.outcome, r.failure, r.round_problems) == ("loud", "truncated", ["truncated"] * 3)


def test_a_refused_round_records_its_stop_reason():
    r = run.run_arm("L", "t1_search", _Priced([], refuse=True), model="claude-haiku-4-5")
    assert r.stop_reasons == ["refusal"]


def test_artefacts_hold_every_rounds_output_and_candidate_files(tmp_path):
    root = tmp_path / "results.jsonl.artefacts"
    broken = _l_answer(
        "t6_stats", 'agg.max(col("created_at"), nulls="skip")', 'agg.max(col("created_at"))'
    )
    gen = ScriptedGenerator(["no code at all", broken, _l_answer("t6_stats")])
    r = run.run_arm("L", "t6_stats", gen, model="claude-x", rep=2, artefact_root=root)
    rep = root / "claude-x" / "L" / "t6_stats" / "rep2"
    assert r.artefacts == str(rep) and r.round_problems == ["no_code", "static", None]
    assert sorted(p.name for p in rep.iterdir()) == ["round1", "round2", "round3"]
    assert (rep / "round1" / "response.md").read_text() == "no code at all"
    assert not (rep / "round1" / "files").exists()  # nothing parsed
    meta = json.loads((rep / "round2" / "round.json").read_text())
    assert (meta["problem"], meta["stop_reason"]) == ("static", "end_turn")
    assert "L421" in meta["feedback"]
    final = rep / "round3" / "files"
    assert (final / "items.lattice.py").read_text() == lattice_reference_files("t6_stats")[
        "items.lattice.py"
    ]
    assert (final / "profile.py").exists()


def test_arm_p_artefacts_are_the_candidate_python_files(tmp_path):
    gen = ScriptedGenerator([_p_answer("t4_restore")])
    r = run.run_arm("P", "t4_restore", gen, model="m", artefact_root=tmp_path)
    files = tmp_path / "m" / "P" / "t4_restore" / "rep0" / "round1" / "files"
    # the start overlay (the t3 reference) plus the answer's files, relative to the app
    assert r.outcome == "correct" and r.artefacts is not None
    for rel, src in reference_files("t4_restore").items():
        assert (files / rel).read_text() == src
    assert (files / "api" / "routes" / "items.py").exists()


def test_no_artefacts_without_a_root():
    r = run.run_arm("L", "t6_stats", ScriptedGenerator([_l_answer("t6_stats")]))
    assert r.artefacts is None


def test_the_cli_writes_artefacts_next_to_the_jsonl(tmp_path, monkeypatch):
    import lattice.llm.client as client

    seen: dict[str, object] = {}

    class _Fake:
        def __init__(self, model: str, max_tokens: int, effort: str) -> None:
            seen["max_tokens"] = max_tokens
            self.inner = ScriptedGenerator([_l_answer("t6_stats")])

        def generate(self, system, messages):
            return self.inner.generate(system, messages)

    monkeypatch.setattr(client, "ClaudeGenerator", _Fake)
    out = tmp_path / "res.jsonl"
    args = ["--model", "fake", "--arm", "L", "--tickets", "t6_stats", "--out", str(out)]
    assert run.main([*args, "--max-tokens", "777"]) == 0
    record = json.loads(out.read_text().splitlines()[0])
    assert seen["max_tokens"] == 777 and record["max_tokens"] == 777
    rep = tmp_path / "res.jsonl.artefacts" / "fake" / "L" / "t6_stats" / "rep0"
    assert record["artefacts"] == str(rep) and (rep / "round1" / "response.md").exists()
    assert run._parser().parse_args([]).max_tokens == run.MAX_TOKENS == 16000


# ---- endpoint-scoped edits (node N2): partial answers merged by top-level name ---------------
#
# spec 2026-09-27-lattice-surface-design.md §3: both arms may answer with only the names
# they add or change, merged by name into the start files (bench.edit_slice.prompts):
# same name -> replace, new name -> appended, ``# delete: <name>`` -> removed. Whole
# files still work. Arm L may also send a ``profile.delta`` instead of the complete
# profile (new columns and response models only).


def test_merge_top_level_replaces_appends_and_deletes_by_name():
    base = (
        '# lattice: v1\n@endpoint("GET", "/a")\ndef a():\n    return 1\n\n\n'
        '@endpoint("GET", "/b")\ndef b():\n    return 2\n'
    )
    replaced = prompts.merge_top_level(base, '@endpoint("GET", "/b")\ndef b():\n    return 22\n')
    assert replaced.partial
    assert "return 22" in replaced.text and "return 1" in replaced.text

    appended = prompts.merge_top_level(base, '@endpoint("GET", "/c")\ndef c():\n    return 3\n')
    assert appended.partial
    for needle in ("def a()", "def b()", "def c()"):
        assert needle in appended.text

    deleted = prompts.merge_top_level(base, "# delete: a\n")
    assert deleted.partial
    assert "def a()" not in deleted.text and "def b()" in deleted.text

    whole = prompts.merge_top_level(base, base)
    assert whole.text == base and not whole.partial  # a whole-file answer is untouched


def test_merge_top_level_reports_a_delete_of_an_unknown_name():
    base = '# lattice: v1\n@endpoint("GET", "/a")\ndef a():\n    return 1\n'
    with pytest.raises(prompts.PartialAnswerError, match="no_such_step"):
        prompts.merge_top_level(base, "# delete: no_such_step\n")


def test_merge_top_level_constants_are_named_only_for_arm_l():
    base = "X = 1\n\n\ndef a():\n    return 1\n"
    l_result = prompts.merge_top_level(base, "X = 2\n", allow_constants=True)
    assert l_result.partial and "X = 2" in l_result.text and "def a()" in l_result.text
    p_result = prompts.merge_top_level(base, "X = 2\n", allow_constants=False)
    assert "X = 1" in p_result.text and "X = 2" not in p_result.text  # unnamed for P: kept as is


_P_BASE = (
    "import uuid\n"
    "from sqlmodel import SQLModel\n\n"
    "LIMIT = 10\n\n\n"
    "class ItemPublic(SQLModel):\n    id: uuid.UUID\n\n\n"
    "def helper():\n    return LIMIT\n\n\n"
    '@router.get("/a")\ndef read_a():\n    return helper()\n'
)


def test_a_whole_p_file_that_adds_an_import_and_drops_a_helper_is_used_verbatim():
    whole = (
        "import uuid\nimport datetime\n"
        "from sqlmodel import SQLModel\n\n"
        "LIMIT = 20\n\n\n"
        "class ItemPublic(SQLModel):\n    id: uuid.UUID\n\n\n"
        '@router.get("/a")\ndef read_a():\n    return datetime.date.today()\n'
    )
    merged = prompts.merge_top_level(_P_BASE, whole, allow_constants=False)
    assert merged.text == whole and not merged.partial


def test_a_whole_file_carrying_base_imports_verbatim_is_whole_even_with_a_changed_import():
    # starts with an import block: whole, even though base's imports changed
    whole = (
        "import uuid, os\nfrom sqlmodel import SQLModel\n\n"
        '@router.get("/a")\ndef read_a():\n    return os.sep\n'
    )
    merged = prompts.merge_top_level(_P_BASE, whole, allow_constants=False)
    assert merged.text == whole and not merged.partial


def test_a_renamed_route_in_a_whole_p_file_leaves_only_the_new_one():
    whole = _P_BASE.replace("def read_a()", "def read_a_renamed()")
    merged = prompts.merge_top_level(_P_BASE, whole, allow_constants=False)
    assert "def read_a_renamed()" in merged.text and "def read_a()" not in merged.text
    assert not merged.partial


def test_a_renamed_route_in_a_whole_l_file_leaves_only_the_new_one():
    base = (
        "# lattice: v1\n"
        '@endpoint("GET", "/a")\ndef a():\n    return 1\n\n\n'
        '@endpoint("GET", "/b")\ndef b():\n    return 2\n'
    )
    whole = base.replace("def a()", "def a2()")
    merged = prompts.merge_top_level(base, whole)
    assert merged.text == whole and not merged.partial


def test_partial_p_and_l_fragments_still_merge():
    p = prompts.merge_top_level(
        _P_BASE, "def helper():\n    return LIMIT + 1\n", allow_constants=False
    )
    assert p.partial and "LIMIT + 1" in p.text and "import uuid" in p.text
    assert "def read_a()" in p.text
    base = '# lattice: v1\nX = 1\n\n\n@endpoint("GET", "/a")\ndef a():\n    return X\n'
    lp = prompts.merge_top_level(base, "X = 2\n")
    assert lp.partial and "X = 2" in lp.text and "def a()" in lp.text


def test_a_new_p_class_is_placed_after_the_base_class_it_references():
    fragment = "class ItemsPublic(SQLModel):\n    data: list[ItemPublic]\n"
    merged = prompts.merge_top_level(_P_BASE, fragment, allow_constants=False)
    assert merged.partial
    assert merged.text.index("class ItemPublic(") < merged.text.index("class ItemsPublic(")
    pytest.importorskip("sqlmodel")
    namespace: dict[str, object] = {"router": _Router()}
    exec(compile(merged.text, "<merged>", "exec"), namespace)  # no NameError at import


def test_a_new_p_route_using_no_base_definition_stays_before_the_parameterised_routes():
    """FastAPI matches routes in declaration order: a new literal route must stay above
    base's ``/{id}`` unless it uses a definition declared below."""
    base = _P_BASE + '\n\n@router.get("/{id}")\ndef read_one(id: int):\n    return id\n'
    fragment = '@router.get("/stats")\ndef read_stats():\n    return LIMIT\n'
    merged = prompts.merge_top_level(base, fragment, allow_constants=False)
    assert merged.partial
    text = merged.text
    assert text.index("LIMIT = 10") < text.index("def read_stats(") < text.index("def read_one(")


class _Router:
    def get(self, _path):
        return lambda fn: fn


def test_a_new_l_endpoint_is_placed_after_the_step_it_calls():
    base = (
        "# lattice: v1\n"
        '@endpoint("GET", "/a")\ndef a():\n    return 1\n\n\n'
        "@step\ndef s(t: Table[Item]) -> Table:\n    return t\n"
    )
    fragment = '@endpoint("GET", "/c")\ndef c():\n    x = s(fetch(Item))\n    return x\n'
    merged = prompts.merge_top_level(base, fragment)
    assert merged.partial
    assert merged.text.index("def s(") < merged.text.index("def c(")


def test_a_new_l_step_or_constant_used_by_a_kept_definition_comes_before_it():
    base = (
        "# lattice: v1\n"
        '@endpoint("GET", "/a")\ndef a():\n    return 1\n'
    )
    fragment = (
        '@endpoint("GET", "/a")\ndef a():\n    x = s2(fetch(Item))\n    return N\n\n\n'
        "@step\ndef s2(t: Table[Item]) -> Table:\n    return t\n\n\n"
        "N = 3\n"
    )
    merged = prompts.merge_top_level(base, fragment)
    assert merged.partial
    text = merged.text
    assert text.index("def s2(") < text.index("def a(")
    assert text.index("N = 3") < text.index("def a(")


def test_parse_profile_delta_reads_columns_and_responses():
    d = prompts.parse_profile_delta(
        "# a comment\n\n"
        "column Item.deleted_at: DateTime\n"
        "column User.notes: Str, private\n"
        "response ItemStats: count, latest_created_at\n"
    )
    assert d.columns == (
        prompts.ColumnDelta("Item", "deleted_at", "DateTime"),
        prompts.ColumnDelta("User", "notes", "Str", private=True),
    )
    assert d.responses == (prompts.ResponseDelta("ItemStats", ("count", "latest_created_at")),)


def test_parse_profile_delta_rejects_a_bad_line():
    with pytest.raises(prompts.PartialAnswerError, match="bogus"):
        prompts.parse_profile_delta("bogus line\n")


def test_apply_profile_delta_adds_a_column_and_a_response():
    _, profile_src = run.l_start(tickets.ticket("t6_stats"))
    delta = prompts.ProfileDelta(
        columns=(prompts.ColumnDelta("Item", "deleted_at", "DateTime"),),
        responses=(prompts.ResponseDelta("ItemStats", ("count", "latest_created_at")),),
    )
    merged_src = run.apply_profile_delta(profile_src, delta)
    profile = run.load_profile_source(merged_src)
    schema = profile.api.tables["Item"]
    assert schema.get("deleted_at").value == "DateTime"
    assert "deleted_at" in schema.nullable
    assert profile.api.responses["ItemStats"] == ("count", "latest_created_at")


def test_apply_profile_delta_rejects_an_existing_column_or_an_unknown_table():
    _, profile_src = run.l_start(tickets.ticket("t6_stats"))
    with pytest.raises(run.ProfileDeltaError, match="already exists"):
        run.apply_profile_delta(
            profile_src,
            prompts.ProfileDelta(columns=(prompts.ColumnDelta("Item", "title", "Str"),)),
        )
    with pytest.raises(run.ProfileDeltaError, match="Nope"):
        run.apply_profile_delta(
            profile_src,
            prompts.ProfileDelta(columns=(prompts.ColumnDelta("Nope", "x", "Str"),)),
        )


def test_partial_answer_option_is_worded_the_same_for_both_arms():
    """Fairness: both arms describe the option with the same template, differing only in
    the units they name and the delete example (spec §3: 'Keep the arms symmetric')."""
    p_note = prompts._partial_note("top-level functions or classes of a module", "old_helper")
    l_units = "endpoints, `@procedure`, `@record`, `@step` and constants of a program"
    l_note = prompts._partial_note(l_units, "old_step")
    assert p_note in prompts.p_system()
    assert l_note in prompts.l_system_prompt()
    p_template = p_note.replace("top-level functions or classes of a module", "X").replace(
        "old_helper", "Y"
    )
    l_template = l_note.replace(l_units, "X").replace("old_step", "Y")
    assert p_template == l_template != ""


def test_the_profile_delta_format_is_documented_in_both_lattice_guides():
    assert prompts._PROFILE_DELTA_DOC in prompts.l_system_prompt()
    assert prompts._PROFILE_DELTA_DOC in prompts.l_system_prompt(repo="taskflow")


# ---- partial answers through the full harness (build, smoke, oracle) -------------------------


@pytest.mark.parametrize(
    "arm,ticket_id",
    [
        ("L", "t6_stats"),  # append: a new endpoint
        ("L", "t9_item_quota"),  # replace + a new constant the replaced endpoint needs
        ("P", "t8_user_filter"),  # replace: one function's signature and body change
        ("P", "t18_duplicate_title_guard"),  # append a helper, replace two callers
    ],
)
def test_a_partial_reference_answer_scores_correct(arm, ticket_id):
    """:func:`run.partial_reference_answer` (the hand-written reference, diffed against
    the start down to top-level names) through the real harness: build, smoke, oracle."""
    answer = run.partial_reference_answer(arm, ticket_id)
    r = run.run_arm(arm, ticket_id, ScriptedGenerator([answer]))
    assert (r.outcome, r.rounds, r.answer_mode, r.truncated) == ("correct", 1, "partial", False)


def test_arm_l_profile_delta_adds_a_response_model_alongside_a_partial_program():
    programs, _ = run.l_start(tickets.ticket("t6_stats"))
    after = lattice_reference_files("t6_stats")["items.lattice.py"]
    fragment = prompts.diff_top_level(programs["items.lattice.py"], after, allow_constants=True)
    answer = prompts.render_files(
        {
            "items.lattice.py": fragment,
            prompts.PROFILE_DELTA_FILE: "response ItemStats: count, latest_created_at\n",
        }
    )
    r = run.run_arm("L", "t6_stats", ScriptedGenerator([answer]))
    assert (r.outcome, r.rounds, r.answer_mode) == ("correct", 1, "partial")


def test_arm_l_profile_delta_adds_a_column_alongside_a_partial_program():
    programs, _ = run.l_start(tickets.ticket("t3_soft_delete"))
    after = lattice_reference_files("t3_soft_delete")["items.lattice.py"]
    fragment = prompts.diff_top_level(programs["items.lattice.py"], after, allow_constants=True)
    answer = prompts.render_files(
        {
            "items.lattice.py": fragment,
            prompts.PROFILE_DELTA_FILE: "column Item.deleted_at: DateTime\n",
        }
    )
    r = run.run_arm("L", "t3_soft_delete", ScriptedGenerator([answer]))
    assert (r.outcome, r.rounds, r.answer_mode) == ("correct", 1, "partial")


def test_arm_l_partial_answer_with_an_unknown_delete_gets_named_repair_feedback():
    bad = prompts.render_files({"items.lattice.py": "# delete: no_such_endpoint\n"})
    gen = ScriptedGenerator([bad, _l_answer("t6_stats")])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.round_problems) == ("correct", ["merge", None])
    feedback = gen.calls[1][1][-1]["content"]
    assert "no_such_endpoint" in feedback and "no such top-level name" in feedback
    _assert_no_oracle_text([feedback], "t6_stats")


def test_arm_p_partial_answer_with_an_unknown_delete_gets_named_repair_feedback():
    bad_file = {prompts.APP_PREFIX + "api/routes/items.py": "# delete: no_such_fn\n"}
    gen = ScriptedGenerator([prompts.render_files(bad_file), _p_answer("t1_search")])
    r = run.run_arm("P", "t1_search", gen)
    assert (r.outcome, r.round_problems) == ("correct", ["merge", None])
    feedback = gen.calls[1][1][-1]["content"]
    assert "no_such_fn" in feedback and "no such top-level name" in feedback


# ---- answer_mode / truncated metrics -----------------------------------------------------


def test_answer_mode_is_whole_for_a_plain_whole_file_answer():
    r = run.run_arm("L", "t6_stats", ScriptedGenerator([_l_answer("t6_stats")]))
    assert (r.answer_mode, r.truncated) == ("whole", False)


def test_truncated_is_recorded_even_after_a_successful_repair():
    cut = _l_answer("t6_stats")[:400]  # cut off mid-block: no complete file
    gen = _Stops([(cut, "max_tokens"), (_l_answer("t6_stats"), "end_turn")])
    r = run.run_arm("L", "t6_stats", gen)
    assert (r.outcome, r.truncated, r.answer_mode) == ("correct", True, "whole")


def test_the_report_shows_a_truncation_rate_column():
    rows = [
        _row("L", "correct", 0.03, rounds=1, truncated=True),
        _row("L", "correct", 0.03, rounds=1, truncated=False),
    ]
    s = {x["arm"]: x for x in summary(rows)}
    assert s["L"]["truncation_rate"] == pytest.approx(0.5)
    assert "trunc%" in render(rows)


# ---- the partial dry run ----------------------------------------------------------------------


@pytest.mark.parametrize("arm", run.ARMS)
@pytest.mark.parametrize("ticket_id", TICKET_IDS)
def test_the_partial_dry_run_scores_every_reference_correct(arm, ticket_id):
    """As ``test_the_dry_run_scores_every_reference_correct``, but each reference fed
    through the arm as a partial answer (diffed against the start): both should score
    every ticket the same way."""
    r = run.dry_run(arm, ticket_id, partial=True)
    if arm == "L" and ticket_id in GAPS:
        assert r.outcome == "not_expressible" and r.detail == GAPS[ticket_id]
        return
    if arm == "L" and ticket_id in tickets.ROUND3_TEMPLATE:
        assert r.outcome == "no_reference" and r.dry_run
        return
    assert (r.outcome, r.rounds, r.failed_cases) == ("correct", 1, []), r.detail
    assert r.model == "reference" and r.dry_run and r.cost_usd == 0


def test_the_cli_dry_run_partial_flag_is_wired(tmp_path):
    out = tmp_path / "res.jsonl"
    args = [
        "--model", "reference", "--arm", "L", "--tickets", "t6_stats",
        "--out", str(out), "--dry-run-partial",
    ]  # fmt: skip
    assert run.main(args) == 0
    record = json.loads(out.read_text().splitlines()[0])
    assert (record["dry_run"], record["answer_mode"], record["outcome"]) == (
        True,
        "partial",
        "correct",
    )


# ---- the CLI transport (--backend cli), --resume and --workers ---------------------------


def test_backend_cli_is_wired_instead_of_the_api_client(tmp_path, monkeypatch):
    import lattice.llm.cli_client as cli_client

    seen: dict[str, object] = {}

    class _FakeCli:
        def __init__(self, model: str, max_tokens: int, effort: str) -> None:
            seen["model"], seen["max_tokens"], seen["effort"] = model, max_tokens, effort

        def generate(self, system, messages):
            return Generation(
                text=_l_answer("t6_stats"),
                usage=Usage(10, 5),
                latency_s=0.001,
                stop_reason="end_turn",
                model="claude-haiku-4-5",
                cost_usd=0.0042,
                cost_basis="cli_list_estimate",
            )

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _FakeCli)
    out = tmp_path / "res.jsonl"
    args = [
        "--model", "fake-cli-model", "--arm", "L", "--tickets", "t6_stats",
        "--out", str(out), "--backend", "cli",
    ]  # fmt: skip
    assert run.main(args) == 0
    record = json.loads(out.read_text().splitlines()[0])
    assert record["backend"] == "cli"
    assert record["cost_basis"] == "cli_list_estimate"
    assert record["cost_usd"] == pytest.approx(0.0042)
    assert seen == {"model": "fake-cli-model", "max_tokens": run.MAX_TOKENS, "effort": "high"}


def test_backend_defaults_to_api(tmp_path):
    assert run._parser().parse_args([]).backend == "api"


def test_backend_cli_usage_limit_stops_cleanly_without_recording_a_failure(tmp_path, monkeypatch):
    import lattice.llm.cli_client as cli_client

    class _FakeCli:
        def __init__(self, model, max_tokens, effort):
            pass

        def generate(self, system, messages):
            raise cli_client.CliUsageLimitError("plan usage limit reached")

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _FakeCli)
    out = tmp_path / "res.jsonl"
    args = [
        "--model", "m", "--arm", "P", "--tickets", "t6_stats,t1_search",
        "--reps", "1", "--out", str(out), "--backend", "cli",
    ]  # fmt: skip

    assert run.main(args) == 3
    assert not out.exists() or out.read_text() == ""


def test_resume_skips_already_completed_tuples(tmp_path):
    out = tmp_path / "res.jsonl"
    pre = run.dry_run("P", "t6_stats", partial=True)
    pre.rep = 0
    out.write_text(pre.to_json() + "\n")

    args = [
        "--model", "reference", "--arm", "P", "--tickets", "t6_stats,t1_search",
        "--reps", "1", "--out", str(out), "--dry-run-partial", "--resume",
    ]  # fmt: skip
    assert run.main(args) == 0

    lines = out.read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["ticket"] for line in lines] == ["t6_stats", "t1_search"]


def test_completed_ignores_malformed_lines():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "res.jsonl"
        out.write_text('not json\n{"model": "m", "arm": "P", "ticket": "t1", "rep": 0}\n')
        assert run._completed(out) == {("m", "P", "t1", 0)}


def test_workers_parallel_output_matches_sequential_order(tmp_path):
    common = [
        "--model", "reference", "--arm", "P,L", "--tickets", "t6_stats,t1_search,t4_restore",
        "--reps", "1", "--dry-run-partial",
    ]  # fmt: skip
    out1 = tmp_path / "seq.jsonl"
    out4 = tmp_path / "par.jsonl"
    assert run.main([*common, "--out", str(out1)]) == 0
    assert run.main([*common, "--out", str(out4), "--workers", "4"]) == 0

    lines1 = out1.read_text().splitlines()
    lines4 = out4.read_text().splitlines()
    assert len(lines1) == len(lines4) == 6
    keys1 = [(json.loads(ln)["ticket"], json.loads(ln)["arm"]) for ln in lines1]
    keys4 = [(json.loads(ln)["ticket"], json.loads(ln)["arm"]) for ln in lines4]
    assert keys1 == keys4


def test_workers_run_concurrently_and_write_safely(tmp_path, monkeypatch):
    import threading
    import time as time_mod

    import lattice.llm.cli_client as cli_client

    # Measure overlap of the generate() calls themselves, not run.main()'s total wall
    # time: each round also runs a real smoke test and oracle evaluation (ASGI + SQLite,
    # CPU-bound), which the GIL serialises across threads regardless of --workers and
    # would swamp a total-time budget with unrelated cost. Concurrent dispatch of the
    # (I/O-bound, in production) model calls is what --workers buys, so that's what this
    # asserts: every fake call starts within one sleep-window of the first.
    lock = threading.Lock()
    starts: list[float] = []

    class _SlowFakeCli:
        def __init__(self, model, max_tokens, effort):
            pass

        def generate(self, system, messages):
            with lock:
                starts.append(time_mod.perf_counter())
            time_mod.sleep(0.2)
            return Generation(
                text=_l_answer("t6_stats"),
                usage=Usage(1, 1),
                latency_s=0.0,
                stop_reason="end_turn",
                model="m",
                cost_usd=0.001,
                cost_basis="cli_list_estimate",
            )

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _SlowFakeCli)
    out = tmp_path / "res.jsonl"
    args = [
        "--model", "m", "--arm", "L", "--tickets", "t6_stats", "--reps", "4",
        "--out", str(out), "--backend", "cli", "--workers", "4",
    ]  # fmt: skip

    assert run.main(args) == 0

    lines = out.read_text().splitlines()
    assert len(lines) == 4
    for line in lines:
        json.loads(line)  # every line parses whole: no interleaved/corrupted writes
    assert len(starts) == 4
    assert max(starts) - min(starts) < 0.2  # all 4 dispatched within one sleep-window


# ---- harness errors (review I2) ------------------------------------------------------------------


def test_completed_does_not_count_error_records(tmp_path):
    out = tmp_path / "res.jsonl"
    ok = {"model": "m", "arm": "P", "ticket": "t1", "rep": 0, "outcome": "correct"}
    err = {"model": "m", "arm": "P", "ticket": "t2", "rep": 0, "outcome": "error"}
    out.write_text(json.dumps(ok) + "\n" + json.dumps(err) + "\n")
    assert run._completed(out) == {("m", "P", "t1", 0)}


def _cli_failing(monkeypatch, fail_on: set[int]):
    import lattice.llm.cli_client as cli_client

    calls = {"n": 0}

    class _FakeCli:
        def __init__(self, model, max_tokens, effort):
            pass

        def generate(self, system, messages):
            calls["n"] += 1
            if calls["n"] in fail_on:
                raise cli_client.CliError("claude -p exited 1: model not found")
            return Generation(
                text="no files", usage=Usage(1, 1), latency_s=0.0, stop_reason="end_turn",
                model="m", cost_usd=0.0, cost_basis="cli_list_estimate",
            )  # fmt: skip

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _FakeCli)
    return calls


FIVE = "t1_search,t2_since,t5_title_window,t6_stats,t7_deactivate"


def test_three_consecutive_cli_errors_stop_the_run(tmp_path, monkeypatch, capsys):
    _cli_failing(monkeypatch, fail_on=set(range(1, 100)))
    out = tmp_path / "res.jsonl"
    args = ["--model", "m", "--arm", "P", "--tickets", FIVE, "--out", str(out),
            "--backend", "cli", "--max-repairs", "0"]  # fmt: skip
    assert run.main(args) == 4
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(records) == 3
    assert all(r["outcome"] == "error" and r["failure"] == "cli_error" for r in records)
    err = capsys.readouterr().err
    assert "3 consecutive CLI errors" in err and "stopping" in err
    # --resume redoes them: error records are not completed
    assert run._completed(out) == set()


def test_cli_errors_that_are_not_consecutive_do_not_stop_the_run(tmp_path, monkeypatch):
    _cli_failing(monkeypatch, fail_on={1, 2, 4, 5})
    out = tmp_path / "res.jsonl"
    args = ["--model", "m", "--arm", "P", "--tickets", FIVE, "--out", str(out),
            "--backend", "cli", "--max-repairs", "0"]  # fmt: skip
    assert run.main(args) == 0
    outcomes = [json.loads(line)["outcome"] for line in out.read_text().splitlines()]
    assert outcomes.count("error") == 4 and len(outcomes) == 5


def test_the_report_prints_an_error_rate_column():
    rows = [
        {"model": "m", "arm": "P", "ticket": f"t{i}", "outcome": o, "cost_usd": 0.0,
         "rounds": 1, "lines_changed": 0, "code_lines": 0, "prompt_tokens_est": 0,
         "input_tokens": 0, "output_tokens": 0, "wall_s": 0.0}
        for i, o in enumerate(["correct", "error", "error", "silent"])
    ]  # fmt: skip
    text = render(rows)
    header = text.splitlines()[0]
    assert "| err% |" in header
    row = text.splitlines()[2]
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[header.strip("|").split("|").index(" err% ")] == "50"
    assert summary(rows)[0]["error_rate"] == pytest.approx(0.5)
