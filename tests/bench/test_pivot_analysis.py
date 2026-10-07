"""Experiment D's pre-registered analysis (bench/edit_slice/pivot_analysis.py), on
synthetic records: every verdict path of H1'/H2' (confirmed, guard failed, spec bar
failed, not significant), H3's descriptive line, the tables' totals, and the
keep-the-last-record-per-key handling of a resumed run's duplicates."""

from __future__ import annotations

import json

import pytest

from bench.edit_slice import pivot_analysis as pa
from bench.edit_slice.pivot_analysis import (
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
    if arm in ("S", "S+", "S*"):
        r.update(spec_drafted=0, spec_rejected=0, spec_rejected_rules=[], spec_rounds=0,
                 spec_failed=False, layers_fed_back=[], final_check_findings=0,
                 spec_review_rounds=0, spec_review_failed=False,
                 spec_drafted_before_review=0, spec_drafted_after_review=0)  # fmt: skip
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
    return hypothesis(collect(records), model, "S+", "S", full=True)


def test_fewer_silent_more_correct_and_a_clean_spec_is_confirmed():
    v = _h1(_runs(SONNET, "S", 3) + _runs(SONNET, "S+", 0, **SPEC_OK))
    assert v["decision"] == "CONFIRMED" and v["failed"] == []
    assert v["fisher_p"] < 0.05
    assert v["sign"][:2] == (12, 0)
    assert v["spec_error_rate"] == pytest.approx(0.1)


def test_fewer_silent_but_fewer_correct_fails_the_guard():
    # S: 3 silent, 3 correct (50%); S+: 0 silent, 4 loud, 2 correct (33%)
    v = _h1(_runs(SONNET, "S", 3) + _runs(SONNET, "S+", 0, loud=4, **SPEC_OK))
    assert v["fisher_p"] < 0.05
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["correctness guard"]


def test_equal_correct_rate_passes_the_guard():
    v = _h1(_runs(SONNET, "S", 3) + _runs(SONNET, "S+", 0, loud=3, **SPEC_OK))
    assert v["decision"] == "CONFIRMED"


def test_a_spec_error_rate_over_25_percent_fails_the_spec_bar():
    v = _h1(_runs(SONNET, "S", 3)
            + _runs(SONNET, "S+", 0, spec_drafted=10, spec_rejected=3))  # fmt: skip
    assert v["spec_error_rate"] == pytest.approx(0.3)
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["spec bar"]


def test_no_drafted_rules_fails_the_spec_bar():
    v = _h1(_runs(SONNET, "S", 3) + _runs(SONNET, "S+", 0))
    assert v["spec_error_rate"] is None
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["spec bar"]


def test_a_small_difference_is_not_significant():
    s = _runs(SONNET, "S", 0)
    s[0]["outcome"] = s[6]["outcome"] = "silent"
    v = _h1(s + _runs(SONNET, "S+", 0, **SPEC_OK))
    assert v["fisher_p"] >= 0.05
    assert v["decision"] == "NOT CONFIRMED" and v["failed"] == ["fewer silent (Fisher)"]


def test_every_failed_condition_is_listed():
    v = _h1(_runs(SONNET, "S", 0) + _runs(SONNET, "S+", 1, loud=2, spec_drafted=2,
                                          spec_rejected=2))  # fmt: skip
    assert v["failed"] == ["fewer silent (Fisher)", "correctness guard", "spec bar"]


def test_h2_is_the_same_test_on_haiku():
    recs = _runs(HAIKU, "S", 3) + _runs(HAIKU, "S+", 0, **SPEC_OK)
    v = hypothesis(collect(recs), HAIKU, "S+", "S", full=True)
    assert v["decision"] == "CONFIRMED" and v["model"] == HAIKU


def test_h1_and_h2_use_their_own_model_only():
    recs = (_runs(HAIKU, "S", 3) + _runs(HAIKU, "S+", 0, **SPEC_OK)
            + _runs(SONNET, "S", 0) + _runs(SONNET, "S+", 3, **SPEC_OK))  # fmt: skip
    vs = verdicts(recs)
    assert vs["H2"]["decision"] == "CONFIRMED" and vs["H2"]["model"] == HAIKU
    assert vs["H1"]["decision"] == "NOT CONFIRMED" and vs["H1"]["model"] == SONNET


def test_a_missing_arm_is_no_data():
    v = _h1(_runs(SONNET, "S", 3))
    assert v["decision"] == "NO DATA"


def test_per_ticket_sign_test():
    s = _runs(SONNET, "S", 2)
    sp = _runs(SONNET, "S+", 0, **SPEC_OK)
    # S+ worse than S on one ticket: 11 wins, 1 loss (12 tickets)
    for r in sp:
        if r["ticket"] == TICKETS[0] and r["rep"] < 3:
            r["outcome"] = "silent"
    v = _h1(s + sp)
    wins, losses, _ = v["sign"]
    assert (wins, losses) == (11, 1)


def test_h3_is_descriptive():
    recs = (
        _runs(SONNET, "S", 2, **SPEC_OK)
        + _runs(SONNET, "S+", 1, **SPEC_OK)
        + _runs(SONNET, "S*", 1)
    )
    d = h3(collect(recs), SONNET)
    assert d["S"].silent == 24 and d["S+"].silent == 12 and d["S*"].silent == 12
    assert "fisher_p" not in d


def test_duplicate_keys_keep_the_last_record():
    recs = _runs(SONNET, "S", 0)
    dup = dict(recs[0])
    dup["outcome"] = "silent"  # the resumed, later line for the same key
    stats = collect([*recs, dup])
    a = stats[(SONNET, "S")]
    assert a.runs == len(TICKETS) * 6  # not len(recs) + 1: the first line is replaced
    assert a.silent == 1 and a.correct == len(TICKETS) * 6 - 1


def test_duplicate_keys_are_order_sensitive_last_wins():
    recs = _runs(SONNET, "S", 0)
    # the later (resumed) line for the same key keeps its own outcome even when it
    # comes first in the list passed in -- "last" means last in the input order
    later_first = dict(recs[0])
    later_first["outcome"] = "silent"
    stats = collect([later_first, *recs])
    a = stats[(SONNET, "S")]
    assert a.silent == 0  # recs[0] (correct) is the LAST occurrence of its key
    assert a.correct == len(TICKETS) * 6


def test_tables_totals():
    s = _runs(SONNET, "S", 2, loud=1)
    splus = _runs(SONNET, "S+", 1, **SPEC_OK)
    splus[0].update(spec_failed=True, spec_rounds=2)
    splus[1].update(layers_fed_back=[["A", "D"], ["D"]], rounds=3)
    splus[2].update(layers_fed_back=[["B"]], rounds=2)
    splus[3].update(final_check_findings=2)  # rep 3: correct, flagged -> a false finding
    splus[0].update(final_check_findings=5)  # rep 0: silent, flagged -> not a false finding
    splus[4].update(spec_review_rounds=1, spec_drafted_before_review=3,
                    spec_drafted_after_review=4)  # fmt: skip
    splus[5].update(spec_review_rounds=1, spec_review_failed=True,
                    spec_drafted_before_review=2, spec_drafted_after_review=2)  # fmt: skip
    sstar = _runs(SONNET, "S*", 0, final_check_findings=1)  # all correct, all flagged
    stats = collect(s + splus + sstar)

    ss = stats[(SONNET, "S")]
    assert (ss.runs, ss.correct, ss.silent, ss.loud) == (72, 36, 24, 12)
    sp = stats[(SONNET, "S+")]
    assert sp.correct + sp.silent + sp.loud == sp.runs == 72
    assert sp.spec_failed == 1 and sp.spec_rounds == 2
    assert sp.spec_error_rate() == pytest.approx(72 / 720)
    assert sp.repair_rounds == 3
    assert sp.layer_rounds == {"A": 1, "B": 1, "D": 2}
    assert sp.false_findings == 1
    assert sp.spec_review_rounds == 2 and sp.spec_review_failed == 1
    assert sp.spec_drafted_before_review == 5 and sp.spec_drafted_after_review == 6
    assert stats[(SONNET, "S*")].false_findings == 72

    per_ticket = pa.per_ticket_silent(s + splus + sstar)
    assert sum(per_ticket[SONNET][t]["S"] for t in TICKETS) == 24
    assert sum(per_ticket[SONNET][t]["S+"] for t in TICKETS) == 12
    assert sum(per_ticket[SONNET][t]["S*"] for t in TICKETS) == 0

    md = render(s + splus + sstar)
    assert "| Sonnet | S | 72 | 36/72 [" in md
    assert "| Sonnet | S+ | 72 | 60/72 [" in md
    assert "| Sonnet | S+ | 2 | 3 | 1 | 1 | 0 | 2 |" in md  # catches by layer
    assert "| Sonnet | S+ | 72 | 10.0% (72/720) | 1 | 0.03 |" in md  # spec table
    assert "| Sonnet | 2 | 5 | 6 | 1 |" in md  # S+ review table
    assert "| Sonnet | S* | 72 | 72 |" in md  # false findings
    assert "H1' (Sonnet, S+ vs S)" in md and "H2' (Haiku, S+ vs S)" in md
    assert "H3' (Sonnet): descriptive, not tested" in md
    assert "H3' (Haiku): descriptive, not tested" in md


def test_cli_prints_the_report(tmp_path, capsys):
    f = tmp_path / "outcomes.jsonl"
    recs = _runs(SONNET, "S", 3) + _runs(SONNET, "S+", 0, **SPEC_OK)
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    pa.main([str(f)])
    out = capsys.readouterr().out
    assert "H1' (Sonnet, S+ vs S): CONFIRMED" in out
