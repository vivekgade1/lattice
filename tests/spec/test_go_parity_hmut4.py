"""The corrected operator classifier of the Go round-3 held-out mutant runner (``dogfood/go_parity_hmut4.py``).

``dogfood/go_parity_hmut3.py`` is frozen and was scored once. Its ``classify_operator`` read "(removes the 403 owner check)"
in the description of HM1-4 as the HM9-10 operator (``status_403``), so HM1-4 were scored as survivors although layer A had
found GO801 on their routes (docs/results/2026-10-19-go-parity-round3-posthoc.md). The tests here feed the classifiers the
*real* descriptions of the 16 S4 mutants (the archived MUTANTS.md, read only), show that the old classifier mislabels them,
and that the new one labels every mutant with the operator of the class table of prereg section 6.2 (HM1-2 owner read,
HM3-4 owner write, HM5-6 column, HM7-8 login, HM9-10 status 403, ...). The S4 data is development data: nothing here scores
or re-scores anything, calls a model or Docker, or builds S4."""

# ruff: noqa: E501
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "dogfood"))

import go_parity_hmut3 as H3  # noqa: E402
import go_parity_hmut4 as H4  # noqa: E402
import go_parity_s3r as S3R  # noqa: E402

H3_ORIGINAL = H3.classify_operator
S4_MUTANTS = REPO / "docs/results/data/2026-10-18-go-parity-round3/s4-mutants/MUTANTS.md"

# the registered class table (prereg 6.2: HM1-2 read, HM3-4 write, HM5-6 column, HM7-8 login, HM9-10 status 403, then the reported ones)
REGISTERED = {1: "owner_read", 2: "owner_read", 3: "owner_write", 4: "owner_write", 5: "expose_column", 6: "expose_column",
              7: "drop_login", 8: "drop_login", 9: "status_403", 10: "status_403", 11: "drop_parent", 12: "drop_guard",
              13: "drop_guard", 14: "loosen_bound", 15: "owner_body", 16: "widen_list"}  # fmt: skip
# what the frozen classifier answers for the real description of each mutant (observed, pinned)
OLD_ANSWER = {**REGISTERED, 1: "status_403", 2: "status_403", 3: "status_403", 4: "status_403"}


def rows() -> dict[int, H3.MutRow]:
    parsed = H3.parse_mutants_md(S4_MUTANTS.read_text())
    assert sorted(parsed) == list(range(1, 17))
    return parsed


# ---- the defect, reproduced on the real descriptions ---------------------------------------------------------------------------


def test_the_registered_table_is_the_frozen_runners_own():
    assert H4.INDEX_OPERATOR == H3.INDEX_OPERATOR == REGISTERED
    assert H4.CLASS_GATES == H3.CLASS_GATES and H4.OPERATORS is H3.OPERATORS


@pytest.mark.parametrize("num", [1, 2, 3, 4])
def test_the_frozen_classifier_reads_the_403_owner_check_as_the_status_operator(num):
    text = rows()[num].operator_text
    assert "403 owner check" in text or "403 book-owner check" in text
    assert H3.classify_operator(text) == "status_403"  # the defect: HM1-4 are owner-filter mutants
    op = H3.operator_of(num, text)
    assert (
        op["key"] == "status_403" and op["shifted"] is True
    )  # so the frozen run judged them with the R3 404 detector


def test_the_frozen_classifier_is_right_on_the_other_twelve():
    got = {n: H3.classify_operator(r.operator_text) for n, r in rows().items()}
    assert got == OLD_ANSWER


# ---- the correction -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("num", range(1, 17))
def test_the_corrected_classifier_labels_every_s4_mutant_with_its_registered_class(num):
    text = rows()[num].operator_text
    assert H4.classify_operator(text) == REGISTERED[num]
    op = H4.operator_of(num, text)
    assert op == {
        "key": REGISTERED[num],
        "from_text": REGISTERED[num],
        "from_id": REGISTERED[num],
        "shifted": False,
        "recognised": True,
    }


def test_hm1_to_4_are_owner_scoped_gated_on_go801_and_hm9_10_are_status_403_gated_on_r3():
    for num in (1, 2, 3, 4):
        det = H4.OPERATORS[H4.operator_of(num, rows()[num].operator_text)["key"]].detectors
        assert [(d.layer, d.code, d.cause) for d in det] == [("A", "GO801", None)]
    for num in (9, 10):
        det = H4.OPERATORS[H4.operator_of(num, rows()[num].operator_text)["key"]].detectors
        assert [(d.layer, d.code, d.cause) for d in det] == [("R", "R3", "not_found")]


def test_the_prereg_table_texts_still_classify_as_before():
    # the operator column of prereg 6.2 (the frozen runner's own test pins the same strings against the old classifier)
    table = {1: "drop the owner filter of an owner-scoped read", 3: "drop the owner filter of an owner-scoped write or state change",
             5: "expose a never-returned column on a route that answers its row", 7: "remove the login requirement from a Member route",
             9: "answer another status (403) where truth says 404 not_found", 11: "drop the parent-ownership check of an `owner_via` table",
             12: "drop a state guard (`conflict`) of a transition", 14: "loosen a validation bound (remove the upper bound)",
             15: "take the owner from the request body on create", 16: "widen a list route's owner filter to every row"}  # fmt: skip
    for num, text in table.items():
        assert H4.classify_operator(text) == REGISTERED[num] == H3.classify_operator(text)
    assert H4.classify_operator("something nobody wrote") is None


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (
            "(removes the 403 owner check)",
            "owner_read",
        ),  # nothing but the parenthetical: the check is the owner filter
        ("drop owner filter of an owner-scoped WRITE (removes the 403 owner check)", "owner_write"),
        ("drop owner filter of an owner-scoped READ (answers 404 instead of 403)", "owner_read"),
        ("answer 403 where truth.json says 404 not_found (the owner check stays)", "status_403"),
        ("remove the login requirement (also removes the 403 owner check)", "drop_login"),
    ],
)
def test_the_head_clause_decides_and_a_parenthetical_only_describes_the_edit(text, key):
    assert H4.classify_operator(text) == key


# ---- the glue: the corrected classifier is the one the frozen machinery uses ----------------------------------------------------


def test_the_frozen_machinery_uses_the_corrected_classifier_only_inside_the_runner():
    text = rows()[1].operator_text
    assert H3.operator_of(1, text)["key"] == "status_403"
    with H4.corrected_classifier():
        assert H3.operator_of(1, text)["key"] == "owner_read"
        assert H3.OPERATORS is H4.OPERATORS
    assert (
        H3.operator_of(1, text)["key"] == "status_403"
    )  # restored: the frozen runner is unchanged in this process


def test_the_classifier_is_restored_when_the_run_raises():
    with pytest.raises(RuntimeError), H4.corrected_classifier():
        raise RuntimeError("boom")
    assert H3.classify_operator is H3_ORIGINAL


def test_the_runner_has_its_own_names_ledger_and_freeze_entries():
    assert (H4.RUNNER_NAME, H4.RUNNER_REL, H4.TEST_REL) == (
        "go_parity_hmut4",
        "dogfood/go_parity_hmut4.py",
        "tests/spec/test_go_parity_hmut4.py",
    )
    assert H4.LEDGER == REPO / "docs/results/data/2026-10-18-go-parity-round3/ledger-hmut4.jsonl"
    assert H4.LEDGER not in (H3.LEDGER, S3R.LEDGER) and H4.ARM != H3.ARM
    assert {H4.RUNNER_REL, H4.TEST_REL, H3.RUNNER_REL} <= set(H4.MUST_LIST)
    assert set(H3.MUST_LIST) <= set(H4.MUST_LIST)
    opts = {a.dest for a in H4.parser()._actions}
    assert not [o for o in opts if "skip" in o or "no_freeze" in o or "ledger" in o or "force" in o]


def test_main_refuses_when_lattice_is_outside_the_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(H4, "REPO", tmp_path)
    args = [
        "--s4",
        "a",
        "--patches",
        "b",
        "--truth",
        "c",
        "--freeze",
        "d",
        "--kind",
        "shakedown",
        "--out",
        "o",
        "--scratch",
        "s",
        "--base",
        "b",
    ]
    with pytest.raises(SystemExit, match="refusing to run"):
        H4.main(args)


def test_only_is_for_shakedowns():
    args = [
        "--s4",
        "a",
        "--patches",
        "b",
        "--truth",
        "c",
        "--freeze",
        "d",
        "--kind",
        "scored",
        "--out",
        "o",
        "--scratch",
        "s",
        "--base",
        "b",
        "--only",
        "1",
    ]
    with pytest.raises(SystemExit, match="shakedown"):
        H4.main(args)


def test_main_aborts_on_a_missing_freeze_before_any_metric_and_writes_its_own_ledger(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(H4, "LEDGER", ledger)
    s4 = tmp_path / "s4"
    s4.mkdir()
    rc = H4.main(["--s4", str(s4), "--patches", str(tmp_path), "--truth", str(tmp_path / "t.json"), "--freeze", str(tmp_path / "freeze.sha256"), "--kind", "scored", "--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "scr"), "--base", str(tmp_path)])  # fmt: skip
    assert rc == 2
    assert json.loads((tmp_path / "out/hmut4.json").read_text())["runner"] == "go_parity_hmut4"
    entries = S3R.read_ledger(ledger)
    assert [e["status"] for e in entries] == ["started", "crash_before_metric"] and {
        e["arm"] for e in entries
    } == {H4.ARM}
    assert {e["runner"] for e in entries} == {"go_parity_hmut4"}
    assert not (tmp_path / "scr").exists()
    assert H3.classify_operator is H3_ORIGINAL  # main restored the frozen runner's classifier
