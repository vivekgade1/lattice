"""Phase 1's pre-registered analysis (bench/edit_slice/phase1_analysis.py), on synthetic
records: every verdict path of H1/H4 (confirmed, guard failed, spec bar failed, not
significant), H2, H3's descriptive line, and the tables' totals."""

from __future__ import annotations

import json

import pytest

from bench.edit_slice import phase1_analysis as pa
from bench.edit_slice.phase1_analysis import (
    HAIKU,
    SONNET,
    TICKETS,
    collect,
    h3,
    hypothesis,
    render,
    verdicts,
)

SPEC_OK = {"spec_drafted": 10, "spec_rejected": 1}  # 10% per run


def _run(model: str, arm: str, ticket: str, rep: int, outcome: str, **extra) -> dict:
    r = {
        "model": model, "arm": arm, "ticket": ticket, "rep": rep, "outcome": outcome,
        "cost_usd": 0.1, "input_tokens": 1000, "output_tokens": 200, "rounds": 1,
        "wall_s": 30.0,
    }  # fmt: skip
    if arm in ("S", "S*"):
        r.update(spec_drafted=0, spec_rejected=0, spec_rejected_rules=[], spec_rounds=0,
                 spec_failed=False, layers_fed_back=[], final_check_findings=0)  # fmt: skip
    r.update(extra)
    return r


def _runs(model: str, arm: str, silent: int, loud: int = 0, **extra) -> list[dict]:
    """6 reps per ticket: ``silent`` silent, then ``loud`` loud, the rest correct."""
    out = []
    for t in TICKETS:
        for rep in range(6):
            kind = "silent" if rep < silent else "loud" if rep < silent + loud else "correct"
            out.append(_run(model, arm, t, rep, kind, **extra))
    return out


def _h1(records, model=SONNET):
    return hypothesis(collect(records), model, "S", "P", full=True)


def test_fewer_silent_more_correct_and_a_clean_spec_is_confirmed():
    v = _h1(_runs(SONNET, "P", 3) + _runs(SONNET, "S", 0, **SPEC_OK))
    assert v["decision"] == "CONFIRMED" and v["failed"] == []
    assert v["fisher_p"] < 0.05
    assert v["sign"][:2] == (14, 0)
    assert v["spec_error_rate"] == pytest.approx(0.1)


def test_fewer_silent_but_fewer_correct_fails_the_guard():
    # P: 3 silent, 3 correct (50%); S: 0 silent, 4 loud, 2 correct (33%)
    v = _h1(_runs(SONNET, "P", 3) + _runs(SONNET, "S", 0, loud=4, **SPEC_OK))
    assert v["fisher_p"] < 0.05
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["correctness guard"]


def test_equal_correct_rate_passes_the_guard():
    v = _h1(_runs(SONNET, "P", 3) + _runs(SONNET, "S", 0, loud=3, **SPEC_OK))
    assert v["decision"] == "CONFIRMED"


def test_a_spec_error_rate_over_25_percent_fails_the_spec_bar():
    v = _h1(_runs(SONNET, "P", 3)
            + _runs(SONNET, "S", 0, spec_drafted=10, spec_rejected=3))  # fmt: skip
    assert v["spec_error_rate"] == pytest.approx(0.3)
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["spec bar"]


def test_the_spec_bar_is_pooled_and_inclusive():
    # per-run rates 0/4 and 4/12 (mean 17%), pooled 4/16 = 25% exactly: the bar holds
    s = _runs(SONNET, "S", 0)
    for i, r in enumerate(s):
        drafted, rejected = (4, 0) if i % 2 else (12, 4)
        r.update(spec_drafted=drafted, spec_rejected=rejected)
    v = _h1(_runs(SONNET, "P", 3) + s)
    assert v["spec_error_rate"] == pytest.approx(0.25)
    assert v["decision"] == "CONFIRMED"


def test_no_drafted_rules_fails_the_spec_bar():
    v = _h1(_runs(SONNET, "P", 3) + _runs(SONNET, "S", 0))
    assert v["spec_error_rate"] is None
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["spec bar"]


def test_a_small_difference_is_not_significant():
    p = _runs(SONNET, "P", 0)
    p[0]["outcome"] = p[6]["outcome"] = "silent"
    v = _h1(p + _runs(SONNET, "S", 0, **SPEC_OK))
    assert v["fisher_p"] >= 0.05
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["fewer silent (Fisher)"]


def test_every_failed_condition_is_listed():
    v = _h1(_runs(SONNET, "P", 0) + _runs(SONNET, "S", 1, loud=2, spec_drafted=2,
                                          spec_rejected=2))  # fmt: skip
    assert v["failed"] == ["fewer silent (Fisher)", "correctness guard", "spec bar"]


def test_h2_is_fisher_only():
    # S beats Q on silent runs though S has fewer correct and a bad spec: H2 holds
    recs = _runs(SONNET, "Q", 3) + _runs(SONNET, "S", 0, loud=5, spec_drafted=1,
                                         spec_rejected=1)  # fmt: skip
    v = hypothesis(collect(recs), SONNET, "S", "Q", full=False)
    assert v["decision"] == "CONFIRMED" and v["sign"][:2] == (14, 0)
    v = hypothesis(collect(_runs(SONNET, "Q", 0) + _runs(SONNET, "S", 0)), SONNET, "S", "Q",
                   full=False)  # fmt: skip
    assert v["decision"] == "NOT CONFIRMED" and v["fisher_p"] == 1.0


def test_h4_uses_the_haiku_runs_only():
    recs = (_runs(HAIKU, "P", 3) + _runs(HAIKU, "S", 0, **SPEC_OK)
            + _runs(SONNET, "P", 0) + _runs(SONNET, "S", 3, **SPEC_OK))  # fmt: skip
    vs = verdicts(recs)
    assert vs["H4"]["decision"] == "CONFIRMED" and vs["H4"]["model"] == HAIKU
    assert vs["H1"]["decision"] == "NOT CONFIRMED"


def test_a_missing_arm_is_no_data():
    v = _h1(_runs(SONNET, "P", 3))
    assert v["decision"] == "NO DATA"


def test_per_ticket_sign_test():
    p = _runs(SONNET, "P", 2)
    s = _runs(SONNET, "S", 0, **SPEC_OK)
    # S worse than P on one ticket: 13 wins, 1 loss
    for r in s:
        if r["ticket"] == TICKETS[0] and r["rep"] < 3:
            r["outcome"] = "silent"
    v = _h1(p + s)
    wins, losses, sp = v["sign"]
    assert (wins, losses) == (13, 1)
    assert sp == pytest.approx(15 / 2**14)


def test_h3_is_descriptive():
    recs = _runs(SONNET, "S", 2, **SPEC_OK) + _runs(SONNET, "S*", 1)
    d = h3(collect(recs))
    assert d["S"].silent == 28 and d["S*"].silent == 14
    assert "fisher_p" not in d


def test_duplicates_are_refused():
    recs = _runs(SONNET, "P", 0)
    with pytest.raises(ValueError, match="duplicate"):
        collect(recs + [recs[0]])


def test_tables_totals():
    p = _runs(SONNET, "P", 2, loud=1)
    s = _runs(SONNET, "S", 1, **SPEC_OK)
    s[0].update(spec_failed=True, spec_rounds=2)
    s[1].update(layers_fed_back=[["A", "D"], ["D"]], rounds=3)
    s[2].update(layers_fed_back=[["B"]], rounds=2)
    s[3].update(final_check_findings=2)  # rep 3: correct, flagged -> a false finding
    s[0].update(final_check_findings=5)  # rep 0: silent, flagged -> not a false finding
    sstar = _runs(SONNET, "S*", 0, final_check_findings=1)  # all correct, all flagged
    stats = collect(p + s + sstar)

    sp = stats[(SONNET, "P")]
    assert (sp.runs, sp.correct, sp.silent, sp.loud) == (84, 42, 28, 14)
    ss = stats[(SONNET, "S")]
    assert ss.correct + ss.silent + ss.loud == ss.runs == 84
    assert ss.spec_failed == 1 and ss.spec_rounds == 2
    assert ss.spec_error_rate() == pytest.approx(84 / 840)
    assert ss.repair_rounds == 3
    assert ss.layer_rounds == {"A": 1, "B": 1, "D": 2}
    assert ss.false_findings == 1
    assert stats[(SONNET, "S*")].false_findings == 84
    assert ss.cost == pytest.approx(8.4) and ss.rounds == 84 + 2 + 1

    per_ticket = pa.per_ticket_silent(p + s + sstar)
    assert sum(per_ticket[SONNET][t]["P"] for t in TICKETS) == 28
    assert sum(per_ticket[SONNET][t]["S"] for t in TICKETS) == 14
    assert sum(per_ticket[SONNET][t]["S*"] for t in TICKETS) == 0

    md = render(p + s + sstar)
    assert "| Sonnet | P | 84 | 42/84 [" in md
    assert "| Sonnet | S | 84 | 70/84 [" in md
    assert "| Sonnet | S | 2 | 3 | 1 | 1 | 0 | 2 |" in md  # catches by layer
    assert "| Sonnet | S | 84 | 10.0% (84/840) | 1 | 0.02 |" in md  # spec table
    assert "| Sonnet | S* | 84 | 84 |" in md  # false findings
    assert "H1 (Sonnet, S vs P)" in md and "H4 (Haiku, S vs P)" in md


def test_cli_prints_the_report(tmp_path, capsys):
    f = tmp_path / "outcomes.jsonl"
    recs = _runs(SONNET, "P", 3) + _runs(SONNET, "S", 0, **SPEC_OK)
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    pa.main([str(f)])
    out = capsys.readouterr().out
    assert "H1 (Sonnet, S vs P): CONFIRMED" in out
