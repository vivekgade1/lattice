"""The held-out mutant runner of Go parity round 3 (``dogfood/go_parity_hmut3.py``, prereg section 6.2).

Two parts. The unit tests need neither Docker nor S4: the operator-to-detector mapping, the control logic, the gates,
MUTANTS.md parsing, the freeze refusals, the ledger (a scored run once, a rerun only after ``crash_before_metric``), the
patch step. The stand-in self-test runs the whole CLI on the repository's stand-ins for S4 (S3 = ``gotickets`` and S2 =
``goginapp``, with the truths of ``dogfood/g3_standin``) with sixteen patches written *here*, one per operator of the
table (the same operators as HM1-HM16, on the stand-in's routes), through ``main`` with a generated freeze file and a
ledger in a temporary directory. It needs ``go`` and builds the stand-ins into a temporary directory; it never writes
into the repository. Nothing here calls a model or Docker; S4 and its mutants are not read (the held-out is blind)."""

# ruff: noqa: E501
from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "dogfood"))

import go_parity_hmut3 as H  # noqa: E402
import go_parity_s3r as S3R  # noqa: E402

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")
needs_patch = pytest.mark.skipif(shutil.which("patch") is None, reason="patch is not installed")

TABLE_TEXT = {  # the operator column of prereg 6.2, as W is given it
    1: "drop the owner filter of an owner-scoped read",
    3: "drop the owner filter of an owner-scoped write or state change",
    5: "expose a never-returned column on a route that answers its row",
    7: "remove the login requirement from a Member route",
    9: "answer another status (403) where truth says 404 not_found",
    11: "drop the parent-ownership check of an `owner_via` table",
    12: "drop a state guard (`conflict`) of a transition",
    14: "loosen a validation bound (remove the upper bound)",
    15: "take the owner from the request body on create",
    16: "widen a list route's owner filter to every row",
}


def sha(text: bytes | str) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


# ---- the operator -> detector mapping (prereg 6.2) ---------------------------------------------------------------


@pytest.mark.parametrize(("num", "key"), [(1, "owner_read"), (3, "owner_write"), (5, "expose_column"), (7, "drop_login"), (9, "status_403"),
                                           (11, "drop_parent"), (12, "drop_guard"), (14, "loosen_bound"), (15, "owner_body"), (16, "widen_list")])  # fmt: skip
def test_the_table_text_names_its_operator(num, key):
    assert H.classify_operator(TABLE_TEXT[num]) == key
    assert H.operator_of(num, TABLE_TEXT[num])["shifted"] is False


def test_the_id_is_the_fallback_and_a_shift_is_flagged():
    assert H.classify_operator("something nobody wrote") is None
    got = H.operator_of(6, "no words that name an operator")
    assert got == {
        "key": "expose_column",
        "from_text": None,
        "from_id": "expose_column",
        "shifted": False,
        "recognised": False,
    }
    shifted = H.operator_of(
        6, TABLE_TEXT[7]
    )  # only one route fit "expose": HM6 carries the next operator (the instance rule)
    assert shifted["key"] == "drop_login" and shifted["shifted"] is True
    assert H.operator_of(2, TABLE_TEXT[3])["shifted"] is False  # read and write are one detector


def test_every_hm_has_the_detectors_the_table_registers():
    d = {k: [(x.layer, x.code, x.cause) for x in v.detectors] for k, v in H.OPERATORS.items()}
    assert d["owner_read"] == d["owner_write"] == [("A", "GO801", None)]
    assert d["expose_column"] == [("A", "GO802", None)]
    assert d["drop_login"] == [("R", "R1", None)]
    assert d["status_403"] == [("R", "R3", "not_found")]
    assert d["drop_guard"] == [("R", "R3", "conflict")] and d["loosen_bound"] == [
        ("R", "R3", "invalid")
    ]
    for k in ("drop_parent", "owner_body", "widen_list"):
        assert d[k] == [("A", "GO801", None), ("R", "R2", None)]
    gated = {k for k, v in H.OPERATORS.items() if v.gated}
    assert gated == {"owner_read", "owner_write", "expose_column", "drop_login", "status_403"}
    assert [H.INDEX_OPERATOR[i] for i in H.IDS if H.OPERATORS[H.INDEX_OPERATOR[i]].gated] == [
        H.INDEX_OPERATOR[i] for i in range(1, 11)
    ]
    assert (
        H.CLASS_GATES
        == (
            ("HM1-4", (1, 2, 3, 4), 3),
            ("HM5-6", (5, 6), 2),
            ("HM7-8", (7, 8), 2),
            ("HM9-10", (9, 10), 2),
        )
        and H.GATE_OVERALL == 13
    )


# ---- MUTANTS.md -----------------------------------------------------------------------------------------------------


def test_mutants_md_formats():
    table = "| id | operator | route | file:line | substitutions |\n|---|---|---|---|---|\n| HM1 | drop the owner filter of an owner-scoped read | GET /things/:id | api/a.go:41 | `owner_id = ?` removed |\n"
    bullets = "- m02: drop the owner filter of an owner-scoped read - POST /things/:id/close - api/a.go:77 - 1 substitution\n"
    csv = "HM3, drop the owner filter of an owner-scoped write, PATCH /things/:id, api/a.go:90, owner_id dropped\n"
    rows = H.parse_mutants_md(table + bullets + csv + "\nnotes: nothing here has a route\n")
    assert sorted(rows) == [1, 2, 3]
    r = rows[1]
    assert (r.method, r.path, r.file, r.file_line) == ("GET", "/things/:id", "api/a.go", 41)
    assert (
        r.operator_text == "drop the owner filter of an owner-scoped read"
        and "owner_id" in r.substitutions
    )
    assert (rows[2].method, rows[2].path, rows[2].file_line) == ("POST", "/things/:id/close", 77)
    assert (rows[3].method, rows[3].path) == ("PATCH", "/things/:id")
    with pytest.raises(ValueError, match="twice"):
        H.parse_mutants_md(table + table)


def test_route_forms_compare_equal():
    assert H.route_id("get", "/a/{id}/") == H.route_id("GET", "/a/:id") == ("GET", "/a/:id")
    assert H.key_route("GO801|POST /tickets/:tid/close|Ticket") == ("POST", "/tickets/:tid/close")
    assert H.key_route("GO802|GET /me|User.password_hash") == ("GET", "/me")
    assert H.key_route("GO800|nothing") is None
    assert H.item_parts("R3 POST /queues/{qid}/tickets 404 Not found") == (
        "R3",
        ("POST", "/queues/:qid/tickets"),
        404,
    )
    assert H.item_parts("R1 GET /handoffs") == ("R1", ("GET", "/handoffs"), None)
    assert H.item_parts("T2 User.password_hash") is None


# ---- detectors and the control ---------------------------------------------------------------------------------------

TRUTH: dict[str, Any] = {
    "routes": [{"method": m, "path": p, "auth": a} for m, p, a in [("GET", "/things/:id", "Member"), ("POST", "/things/:id/close", "Member"), ("GET", "/me", "Member"), ("GET", "/things", "Member")]],
    "tables": [{"struct": "User", "table": "users", "columns": [{"name": "id", "json": "id", "private": False}, {"name": "password_hash", "json": "-", "private": True}, {"name": "token", "json": "-", "private": True}]}],
    "refusals": [{"route": "GET /things/:id", "status": 404, "cause": "not_found"}, {"route": "POST /things/:id/close", "status": 409, "cause": "conflict"}, {"route": "POST /things/:id/close", "status": 400, "cause": "invalid"}],
}  # fmt: skip
R = ("GET", "/things/:id")


def f(code: str, route: str, tail: str = "Thing") -> dict[str, str]:
    return {"code": code, "key": f"{code}|{route}|{tail}", "message": "m"}


def judge(
    num: int,
    key: str,
    route=R,
    *,
    a_control=(),
    a_new=(),
    r_control=None,
    r_mutant=None,
    unstable=(),
    text="",
) -> dict[str, Any]:
    return H.judge_mutant(num, key, route, a_control=list(a_control), a_new=list(a_new), r_control=r_control or {}, r_mutant=r_mutant, unstable=unstable, truth=TRUTH, mutants_md_text=text)  # fmt: skip


def test_go801_on_the_route_kills_an_owner_filter_mutant_and_nothing_else_does():
    assert (
        judge(1, "owner_read", a_new=[f("GO801", "GET /things/:id")], r_mutant={})["killed"] is True
    )
    assert (
        judge(1, "owner_read", a_new=[f("GO801", "GET /things/:other")], r_mutant={})["killed"]
        is False
    )  # another route
    assert (
        judge(1, "owner_read", a_new=[f("GO802", "GET /things/:id")], r_mutant={})["killed"]
        is False
    )  # another code
    # an owner-filter mutant is gated on layer A only: a refuted R item does not kill it
    v = judge(
        1,
        "owner_read",
        r_control={"R2 GET /things/{id}": "verified"},
        r_mutant={"R2 GET /things/{id}": "refuted"},
    )
    assert v["killed"] is False and v["gated"] is True


def test_a_finding_already_on_the_control_cannot_flip():
    v = judge(
        3,
        "owner_write",
        a_control=["GO801|GET /things/:id|Thing"],
        a_new=[f("GO801", "GET /things/:id")],
        r_mutant={},
    )
    assert (
        v["killed"] is False and v["detectors"][0]["armed"] is False and "control" in v["killed_by"]
    )


def test_go802_needs_the_route_and_a_private_column_of_the_truth():
    me = ("GET", "/me")
    assert (
        judge(
            5, "expose_column", me, a_new=[f("GO802", "GET /me", "User.password_hash")], r_mutant={}
        )["killed"]
        is True
    )
    assert (
        judge(5, "expose_column", me, a_new=[f("GO802", "GET /me", "User.id")], r_mutant={})[
            "killed"
        ]
        is False
    )  # not a private column
    assert (
        judge(
            5,
            "expose_column",
            me,
            a_new=[f("GO802", "GET /things", "User.password_hash")],
            r_mutant={},
        )["killed"]
        is False
    )
    # MUTANTS.md names the column: only that one counts
    named = judge(
        5,
        "expose_column",
        me,
        a_new=[f("GO802", "GET /me", "User.token")],
        r_mutant={},
        text="HM5 ... `PasswordHash` json tag changed",
    )
    assert named["killed"] is False and named["expose_column"]["named_by_mutants_md"] is True
    assert (
        judge(
            6,
            "expose_column",
            me,
            a_new=[f("GO802", "GET /me", "users.token")],
            r_mutant={},
            text="no column named",
        )["killed"]
        is True
    )  # table spelling, unresolved -> all private columns


def test_r1_item_refuted_after_verified_kills_a_login_mutant():
    rc = {"R1 GET /things": "verified", "R1 GET /me": "verified"}
    assert (
        judge(
            7,
            "drop_login",
            ("GET", "/things"),
            r_control=rc,
            r_mutant={**rc, "R1 GET /things": "refuted"},
        )["killed"]
        is True
    )
    assert (
        judge(
            7,
            "drop_login",
            ("GET", "/things"),
            r_control=rc,
            r_mutant={**rc, "R1 GET /me": "refuted"},
        )["killed"]
        is False
    )  # another route
    assert judge(7, "drop_login", ("GET", "/things"), r_control=rc, r_mutant=rc)["killed"] is False
    unex = {"R1 GET /things": "unexercised"}  # never verified on the control: nothing can flip
    v = judge(
        7, "drop_login", ("GET", "/things"), r_control=unex, r_mutant={"R1 GET /things": "refuted"}
    )
    assert v["killed"] is False and v["detectors"][0]["armed"] is False
    assert (
        judge(
            7,
            "drop_login",
            ("GET", "/things"),
            r_control=rc,
            r_mutant={**rc, "R1 GET /things": "refuted"},
            unstable=["R1 GET /things"],
        )["killed"]
        is False
    )  # two control runs disagreed


def test_the_404_item_uses_the_truths_status_and_the_conflict_item_the_conflict_status():
    rc = {
        "R3 GET /things/{id} 404 Not found": "verified",
        "R3 GET /things/{id} 403 Nope": "verified",
    }
    assert (
        judge(
            9,
            "status_403",
            r_control=rc,
            r_mutant={**rc, "R3 GET /things/{id} 404 Not found": "refuted"},
        )["killed"]
        is True
    )
    assert (
        judge(
            9,
            "status_403",
            r_control=rc,
            r_mutant={**rc, "R3 GET /things/{id} 403 Nope": "refuted"},
        )["killed"]
        is False
    )  # the 403 item is not the 404 one
    close = ("POST", "/things/:id/close")
    rc2 = {
        "R3 POST /things/{id}/close 409 not open": "verified",
        "R3 POST /things/{id}/close 400 bad id": "verified",
    }
    v = judge(
        12,
        "drop_guard",
        close,
        r_control=rc2,
        r_mutant={**rc2, "R3 POST /things/{id}/close 409 not open": "refuted"},
    )
    assert v["killed"] is True and v["status_of_conflict"] == {
        "statuses": [409],
        "from_truth": True,
    }
    inv = judge(
        14,
        "loosen_bound",
        close,
        r_control=rc2,
        r_mutant={**rc2, "R3 POST /things/{id}/close 400 bad id": "refuted"},
    )
    assert inv["killed"] is True and inv["status_of_invalid"]["statuses"] == [400]
    nothing = judge(
        14, "loosen_bound", R, r_control={}, r_mutant={}
    )  # no refusal in the truth: the default 422/400, no such item
    assert nothing["killed"] is False and nothing["status_of_invalid"]["from_truth"] is False


def test_reported_operators_use_layer_a_or_r2():
    for num, key in ((11, "drop_parent"), (15, "owner_body"), (16, "widen_list")):
        assert judge(num, key, a_new=[f("GO801", "GET /things/:id")], r_mutant={})["killed"] is True
        rc = {"R2 GET /things/{id}": "verified"}
        assert (
            judge(num, key, r_control=rc, r_mutant={"R2 GET /things/{id}": "refuted"})["killed"]
            is True
        )
        assert judge(num, key, r_control=rc, r_mutant=rc)["killed"] is False
        assert judge(num, key, r_mutant={})["gated"] is False


def test_without_layer_r_a_layer_r_mutant_is_not_run_and_a_mixed_one_can_still_be_killed_by_a():
    assert judge(7, "drop_login", r_mutant=None)["killed"] is None
    assert judge(9, "status_403", r_mutant=None)["killed"] is None
    assert (
        judge(16, "widen_list", a_new=[f("GO801", "GET /things/:id")], r_mutant=None)["killed"]
        is True
    )
    assert (
        judge(16, "widen_list", r_mutant=None)["killed"] is False
    )  # one of its detectors (A) ran and did not fire


def test_control_logic():
    routes = [R, ("GET", "/me")]
    base = dict(
        routes=routes,
        registered_r=["R1 GET /me"],
        a_control=["GO801|GET /unrelated|Thing"],
        r_control={"R1 GET /me": "verified"},
        pristine_new=[],
        copy_equals_s4=True,
    )
    assert H.judge_control(**base)["passes"] is True
    assert (
        H.judge_control(**{**base, "a_control": ["GO801|GET /things/:id|Thing"]})["passes"] is False
    )  # GO801 on an HM route
    assert (
        H.judge_control(**{**base, "a_control": ["GO802|GET /me|User.password_hash"]})["passes"]
        is False
    )  # GO802 too
    assert (
        H.judge_control(**{**base, "a_control": ["GO804|GET /me|x"]})["passes"] is True
    )  # other codes do not count
    assert (
        H.judge_control(**{**base, "r_control": {"R1 GET /me": "refuted"}})["passes"] is False
    )  # a registered item refuted
    assert (
        H.judge_control(**{**base, "r_control": {"R1 GET /me": "unexercised"}})["passes"] is True
    )  # unexercised is not refuted (the detector is then unarmed)
    assert H.judge_control(**{**base, "pristine_new": [f("GO801", "GET /me")]})["passes"] is False
    assert H.judge_control(**{**base, "copy_equals_s4": False})["passes"] is False


def test_gates():
    k = dict.fromkeys(range(1, 17), True)
    assert H.gate_report(k)["verdict"] == "pass" and H.gate_report(k)["killed"] == 16
    only13 = {**k, 11: False, 14: False, 15: False}
    assert H.gate_report(only13)["verdict"] == "pass" and H.gate_report(only13)["killed"] == 13
    assert H.gate_report({**only13, 16: False})["verdict"] == "fail"  # 12 of 16
    assert (
        H.gate_report({**k, 1: False, 2: False})["classes"]["HM1-4"]["pass"] is False
    )  # 2 of 4 < 3
    assert H.gate_report({**k, 1: False})["classes"]["HM1-4"]["pass"] is True  # 3 of 4
    assert H.gate_report({**k, 5: False})["verdict"] == "fail"  # HM5-6 needs 2/2 though 15 >= 13
    assert (
        H.gate_report({**k, 8: False})["verdict"] == "fail"
        and H.gate_report({**k, 10: False})["verdict"] == "fail"
    )
    inc = H.gate_report({**k, 7: None})
    assert inc["verdict"] == "incomplete" and inc["not_run"] == ["HM7"]


# ---- the self-hash check ------------------------------------------------------------------------------------------------


def make_root(tmp_path: Path) -> tuple[Path, Path, Path, list[Path]]:
    """A fake repository holding every MUST_LIST file, a fake S4 and a patch set; returns (root, freeze, this, required)."""
    root = tmp_path / "repo"
    for rel in H.MUST_LIST:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(f"content of {rel}\n")
    s4 = root / "tests/spec/svc"
    s4.mkdir(parents=True)
    (s4 / "go.mod").write_text("module example.com/svc\n")
    (s4 / "main.go").write_text("package main\n")
    (s4 / "studio.toml").write_text('[app]\napp = "tests.spec.svc_harness:make_app"\n')
    (root / "tests/spec/svc_harness.py").write_text("x = 1\n")
    truth = root / "truth.json"
    truth.write_text("{}")
    patches = tmp_path / "patches"  # outside the root: listed by absolute path
    patches.mkdir()
    for i in H.IDS:
        (patches / H.patch_name(i)).write_text(f"diff {i}\n")
    (patches / "MUTANTS.md").write_text("m\n")
    req = H.required_paths(s4, patches, truth)
    this = root / H.RUNNER_REL
    return root, root / "freeze.sha256", this, req


def write_freeze(root: Path, freeze: Path, files: list[Path], *, rel_to_root: bool = True) -> None:
    lines = []
    for p in files:
        name = p.relative_to(root).as_posix() if rel_to_root and p.is_relative_to(root) else str(p)
        lines.append(f"{sha(p.read_bytes())}  {name}")
    freeze.write_text("\n".join(lines) + "\n")


def full_list(root: Path, req: list[Path]) -> list[Path]:
    return [root / r for r in H.MUST_LIST] + req


def test_the_freeze_check_accepts_a_complete_list(tmp_path):
    root, freeze, this, req = make_root(tmp_path)
    write_freeze(root, freeze, full_list(root, req))
    info = H.verify_freeze(freeze, this, req, root=root)
    assert (
        info["entries"] == len(H.MUST_LIST) + len(req)
        and info["mismatches"] == 0
        and info["required_files_listed"] == len(req)
    )
    assert (
        len(req) == 1 + 1 + 16 + 3 + 1
    )  # truth, MUTANTS.md, 16 patches, S4's three sources, the harness


@pytest.mark.parametrize(
    "what",
    [
        "byte_changed",
        "must_entry_dropped",
        "runner_unlisted",
        "patch_unlisted",
        "truth_unlisted",
        "s4_source_unlisted",
        "harness_unlisted",
        "listed_file_missing",
        "no_file",
    ],
)
def test_the_freeze_check_refuses(tmp_path, what):
    root, freeze, this, req = make_root(tmp_path)
    files = full_list(root, req)
    drop = {"must_entry_dropped": root / H.MUST_LIST[4], "runner_unlisted": this, "patch_unlisted": req[7], "truth_unlisted": req[0],
            "s4_source_unlisted": next(p for p in req if p.name == "main.go"), "harness_unlisted": req[-1]}  # fmt: skip
    if what in drop:
        files = [p for p in files if p != drop[what]]
    write_freeze(root, freeze, files)
    if what == "byte_changed":
        (root / H.MUST_LIST[7]).write_text("edited after the freeze\n")
    if what == "listed_file_missing":
        (root / H.MUST_LIST[7]).unlink()
    if what == "no_file":
        freeze.unlink()
    with pytest.raises(S3R.FreezeError):
        H.verify_freeze(freeze, this, req, root=root)


def test_no_option_skips_or_narrows_the_freeze():
    opts = {a.dest for a in H.parser()._actions}
    assert {"s4", "patches", "truth", "freeze", "kind", "out", "scratch", "base", "only"} <= opts
    assert not [o for o in opts if "skip" in o or "no_freeze" in o or "ledger" in o or "force" in o]
    freeze = next(a for a in H.parser()._actions if a.dest == "freeze")
    assert (
        freeze.required is True
        and next(a for a in H.parser()._actions if a.dest == "kind").required is True
    )
    assert (
        H.RUNNER_REL in H.MUST_LIST
        and H.TEST_REL in H.MUST_LIST
        and set(H.P3.MUST_LIST) <= set(H.MUST_LIST)
    )


def test_the_ledger_path_is_fixed_and_distinct():
    assert (
        H.LEDGER == REPO / "docs/results/data/2026-10-18-go-parity-round3/ledger-hmut.jsonl"
        and H.LEDGER != S3R.LEDGER
    )
    assert H.LEDGER.parent == S3R.R3_DATA


def test_main_refuses_when_lattice_is_outside_the_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "REPO", tmp_path)
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
        H.main(args)


def test_only_is_for_shakedowns(tmp_path):
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
        H.main(args)


def test_main_aborts_on_a_freeze_mismatch_before_any_metric_and_writes_the_ledger(
    tmp_path, monkeypatch
):
    root, freeze, this, req = make_root(tmp_path)
    write_freeze(root, freeze, full_list(root, req))
    (root / H.MUST_LIST[5]).write_text("edited\n")
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(H, "LEDGER", ledger)
    s4 = root / "tests/spec/svc"
    rc = H.main(["--s4", str(s4), "--patches", str(tmp_path / "patches"), "--truth", str(root / "truth.json"), "--freeze", str(freeze), "--kind", "scored", "--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "scr"), "--base", str(tmp_path)])  # fmt: skip
    assert rc == 2
    assert json.loads((tmp_path / "out/hmut3.json").read_text())["rows"] == []
    entries = S3R.read_ledger(ledger)
    assert [e["status"] for e in entries] == ["started", "crash_before_metric"] and {
        e["arm"] for e in entries
    } == {H.ARM}
    assert not (tmp_path / "scr").exists()  # nothing was set up


# ---- the inputs, checked before any metric ----------------------------------------------------------------------------------------


def fake_inputs(tmp_path: Path, *, route: str = "GET /things/:id") -> tuple[Path, Path, Path]:
    s4 = tmp_path / "s4"
    s4.mkdir()
    (s4 / "go.mod").write_text("module example.com/x\n")
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps(TRUTH))
    patches = tmp_path / "p"
    patches.mkdir()
    for i in H.IDS:
        (patches / H.patch_name(i)).write_text("d\n")
    (patches / "MUTANTS.md").write_text(
        "\n".join(f"| HM{i} | {TABLE_TEXT.get(i, 'x')} | {route} | a.go:{i} | s |" for i in H.IDS)
        + "\n"
    )
    return s4, patches, truth


def test_validate_inputs_accepts_a_complete_set_and_refuses_each_defect(tmp_path):
    s4, patches, truth = fake_inputs(tmp_path)
    t, rows, files = H.validate_inputs(s4, patches, truth)
    assert sorted(rows) == list(range(1, 17)) and files[16].name == "m16.diff" and t["routes"]
    (patches / "m16.diff").unlink()
    with pytest.raises(SystemExit, match="m16.diff does not exist"):
        H.validate_inputs(s4, patches, truth)
    (patches / "m16.diff").write_text("d\n")
    md = patches / "MUTANTS.md"
    md.write_text("\n".join(ln for ln in md.read_text().splitlines() if "HM9 " not in ln) + "\n")
    with pytest.raises(SystemExit, match="no line with a route for id 9"):
        H.validate_inputs(s4, patches, truth)
    other = tmp_path / "other"
    other.mkdir()
    s4b, patchesb, truthb = fake_inputs(other, route="GET /nowhere")
    with pytest.raises(SystemExit, match="is not a route of truth.json"):
        H.validate_inputs(s4b, patchesb, truthb)
    (s4 / "go.mod").unlink()
    with pytest.raises(SystemExit, match="no go.mod"):
        H.validate_inputs(s4, patches, truth)


def isolate_environment(monkeypatch) -> None:
    """``main`` puts recording shims on PATH and sets a few variables: restore them (and the snapshot roots) afterwards."""
    for k in ("PATH", "PYTHONDONTWRITEBYTECODE", "GOTOOLCHAIN", "G3_REAL_PATH", "LATTICE_SHIM_LOG"):
        was = os.environ.get(k)
        monkeypatch.setenv(k, was or "x")  # records the old state for the undo
        if was is None:
            monkeypatch.delenv(k)  # absent now; the undo removes whatever main sets
    monkeypatch.setattr(H.P1, "SNAPSHOT_ROOTS", list(H.P1.SNAPSHOT_ROOTS))


@needs_patch
@pytest.mark.parametrize("when", ["before", "after"])
def test_a_crash_is_recorded_before_or_after_the_first_metric(tmp_path, monkeypatch, when):
    isolate_environment(monkeypatch)
    patches = tmp_path / "patches"
    write_patch_set(S3, S3_PATCHES, patches)
    truth = REPO / "dogfood/g3_standin/s3/truth.json"
    freeze, ledger = tmp_path / "freeze.sha256", tmp_path / "ledger.jsonl"
    freeze_for(freeze, S3, patches, truth)

    def boom(ctx, tgt, patches, truth, rows, files, out, *, only=H.IDS, on_control=None):
        if when == "after":
            on_control({"passes": True})  # the control verdict was written: the first metric exists
        raise RuntimeError("boom")

    monkeypatch.setattr(H, "run_hmut", boom)
    monkeypatch.setattr(H, "LEDGER", ledger)
    argv = ["--s4", str(S3), "--patches", str(patches), "--truth", str(truth), "--freeze", str(freeze), "--out", str(tmp_path / "o"), "--scratch", str(tmp_path / "s"), "--base", str(tmp_path)]  # fmt: skip
    with pytest.raises(RuntimeError, match="boom"):
        H.main([*argv, "--kind", "scored"])
    got = S3R.read_ledger(ledger)
    assert [e["status"] for e in got] == [
        "started",
        "crash_after_metric" if when == "after" else "crash_before_metric",
    ]
    assert all(e["lattice_file"].startswith(str(REPO)) for e in got)
    why = H.ledger_gate("rerun", got[0]["runner_sha256"], got[0]["freeze_sha256"], ledger)
    assert (why is None) == (when == "before")  # a rerun only after crash_before_metric
    if when == "after":
        assert "not crash_before_metric" in why


def test_the_binary_injection_check(tmp_path, monkeypatch):
    monkeypatch.delenv("HMUT_TEST_BIN", raising=False)

    class Honours:
        BINARY_ENV = "HMUT_TEST_BIN"

        @staticmethod
        def make_app(db):
            return type(
                "App", (), {"argv": [os.environ.get("HMUT_TEST_BIN", "built-from-source")]}
            )()

    class Ignores(Honours):
        @staticmethod
        def make_app(db):
            return type("App", (), {"argv": ["built-from-source"]})()

    class Raises(Honours):
        @staticmethod
        def make_app(db):
            raise RuntimeError("no go")

    binary = tmp_path / "bin"
    ok, detail = H.binary_injected(Honours, H.binary_env_name(Honours), binary, tmp_path)
    assert (
        ok is True and str(binary) in detail and "HMUT_TEST_BIN" not in os.environ
    )  # restored afterwards
    assert H.binary_injected(Ignores, "HMUT_TEST_BIN", binary, tmp_path)[0] is False
    assert "RuntimeError" in H.binary_injected(Raises, "HMUT_TEST_BIN", binary, tmp_path)[1]
    assert H.binary_injected(Honours, None, binary, tmp_path) == (
        False,
        "the harness defines no BINARY_ENV",
    )
    assert (
        H.binary_env_name(type("M", (), {})) is None
        and H.binary_env_name(type("M", (), {"BINARY_ENV": ""})) is None
    )


def test_s4_sources_and_the_harness_file(tmp_path):
    s4 = tmp_path / "tests/spec/svc"
    (s4 / "api").mkdir(parents=True)
    for rel in (
        "go.mod",
        "go.sum",
        "main.go",
        "api/api.go",
        "studio.toml",
        "truth.json",
        "README.md",
        ".DS_Store",
    ):
        (s4 / rel).write_text("x\n")
    (s4 / "studio.toml").write_text('[app]\napp = "tests.spec.svc_harness:make_app"\n')
    got = sorted(p.relative_to(s4).as_posix() for p in H.s4_sources(s4))
    assert got == ["api/api.go", "go.mod", "go.sum", "main.go", "studio.toml", "truth.json"]
    assert H.harness_file(s4) == tmp_path / "tests/spec/svc_harness.py"
    (s4 / "studio.toml").write_text('[app]\napp = "svc_harness:make_app"\n')  # the bare form
    assert H.harness_file(s4) == tmp_path / "tests/spec/svc_harness.py"
    assert H.harness_file(tmp_path) is None


# ---- the ledger -----------------------------------------------------------------------------------------------------------


def led(ledger: Path, kind: str, status: str, run: str, runner="r1", freeze="f1") -> None:
    S3R.append_ledger(
        {
            "run_id": run,
            "arm": H.ARM,
            "kind": kind,
            "status": status,
            "runner_sha256": runner,
            "freeze_sha256": freeze,
        },
        ledger,
    )


def test_a_scored_run_once_a_rerun_only_after_crash_before_metric(tmp_path):
    L = tmp_path / "ledger.jsonl"
    assert H.ledger_gate("scored", "r1", "f1", L) is None  # empty ledger
    led(L, "shakedown", "started", "s1")
    led(L, "shakedown", "completed", "s1")
    assert H.ledger_gate("scored", "r1", "f1", L) is None  # a shakedown is not a scored run
    assert H.ledger_gate("shakedown", "r1", "f1", L) is None
    assert "no scored run" in H.ledger_gate("rerun", "r1", "f1", L)
    led(L, "scored", "started", "a")
    assert "already has a scored run" in H.ledger_gate(
        "scored", "r1", "f1", L
    )  # started is enough: no second scored run
    led(L, "scored", "crash_before_metric", "a")
    assert "already has a scored run" in H.ledger_gate("scored", "r1", "f1", L)
    assert H.ledger_gate("rerun", "r1", "f1", L) is None  # the one rerun
    assert "differ" in H.ledger_gate("rerun", "r2", "f1", L) and "differ" in H.ledger_gate(
        "rerun", "r1", "f2", L
    )  # frozen bytes changed
    led(L, "rerun", "started", "b")
    led(L, "rerun", "crash_after_metric", "b")
    assert "already had its one rerun" in H.ledger_gate("rerun", "r1", "f1", L)
    assert "no completed" in H.ledger_gate("verify", "r1", "f1", L)


def test_a_rerun_is_refused_after_a_completed_or_a_crash_after_metric_scored_run(tmp_path):
    L = tmp_path / "ledger.jsonl"
    led(L, "scored", "started", "a")
    led(L, "scored", "crash_after_metric", "a")
    assert "not crash_before_metric" in H.ledger_gate("rerun", "r1", "f1", L)
    L2 = tmp_path / "l2.jsonl"
    led(L2, "scored", "started", "a")
    led(L2, "scored", "completed", "a")
    assert "not crash_before_metric" in H.ledger_gate("rerun", "r1", "f1", L2)
    assert H.ledger_gate(
        "verify", "r1", "f1", L2
    ) is None and "runner bytes differ" in H.ledger_gate("verify", "r9", "f1", L2)
    led(L2, "verify", "started", "v")
    assert "already had its one verify" in H.ledger_gate("verify", "r1", "f1", L2)


def test_main_writes_started_and_refuses_a_second_scored_run(tmp_path, monkeypatch):
    root, freeze, this, req = make_root(tmp_path)
    write_freeze(root, freeze, full_list(root, req))
    (root / H.MUST_LIST[5]).write_text(
        "edited\n"
    )  # every run aborts at the freeze, which is enough to use the ledger
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(H, "LEDGER", ledger)
    argv = ["--s4", str(root / "tests/spec/svc"), "--patches", str(tmp_path / "patches"), "--truth", str(root / "truth.json"), "--freeze", str(freeze), "--out", str(tmp_path / "o"), "--scratch", str(tmp_path / "s"), "--base", str(tmp_path)]  # fmt: skip
    assert H.main([*argv, "--kind", "scored"]) == 2
    with pytest.raises(SystemExit, match="ledger refuses"):
        H.main([*argv, "--kind", "scored"])  # crash_before_metric: only --kind rerun may repeat it
    assert (
        H.main([*argv, "--kind", "rerun"]) == 2
    )  # the one rerun is allowed (same frozen bytes) and aborts again
    with pytest.raises(SystemExit, match="already had its one rerun"):
        H.main([*argv, "--kind", "rerun"])
    assert [e["status"] for e in S3R.read_ledger(ledger)] == ["started", "crash_before_metric"] * 2


# ---- the patch step, the copies and the trees -------------------------------------------------------------------------------


def unified(rel: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{rel}", f"b/{rel}")
    )


@needs_patch
def test_apply_patch_applies_and_reports_the_files(tmp_path):
    (tmp_path / "m").mkdir()
    (tmp_path / "m/a.go").write_text("package a\nvar x = 1\n")
    patch = tmp_path / "p.diff"
    patch.write_text(unified("a.go", "package a\nvar x = 1\n", "package a\nvar x = 2\n"))
    got = H.apply_patch(tmp_path / "m", patch)
    assert (
        got["ok"]
        and got["files_changed"] == ["a.go"]
        and (tmp_path / "m/a.go").read_text().endswith("= 2\n")
    )


@needs_patch
def test_apply_patch_refuses_a_reversed_or_already_applied_patch_and_a_reject(tmp_path):
    m = tmp_path / "m"
    m.mkdir()
    patch = tmp_path / "p.diff"
    patch.write_text(unified("a.go", "package a\nvar x = 1\n", "package a\nvar x = 2\n"))
    (m / "a.go").write_text(
        "package a\nvar x = 2\n"
    )  # already applied: Apple's patch would silently reverse it
    got = H.apply_patch(m, patch)
    assert got["ok"] is False and (m / "a.go").read_text().endswith("= 2\n")
    (m / "a.go").write_text("package a\nvar x = 9\n")  # does not match
    assert H.apply_patch(m, patch)["ok"] is False
    assert not list(m.glob("*.orig"))


def test_tree_helpers(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        (d / "pkg").mkdir(parents=True)
        (d / "pkg/x.go").write_text("x")
        (d / ".DS_Store").write_text("junk")
    assert H.tree_sha256(a) == H.tree_sha256(b)
    (b / "pkg/x.go").write_text("y")
    assert H.tree_sha256(a) != H.tree_sha256(b)
    assert H.diff_trees(H.tree_files(a), H.tree_files(b)) == ["pkg/x.go"]


def test_expected_columns_and_refusal_statuses():
    assert H.expected_columns(TRUTH, "nothing")[1] is False
    cols, named = H.expected_columns(TRUTH, "+  Token string")
    assert named is True and cols == {
        "User.token",
        "users.token",
    }  # the camel spelling of `token` is `Token`
    cols, named = H.expected_columns(TRUTH, '- PasswordHash string `json:"-"`')
    assert named is True and cols == {"User.password_hash", "users.password_hash"}
    assert H.refusal_statuses(TRUTH, ("POST", "/things/:id/close"), "conflict") == ([409], True)
    assert H.refusal_statuses(TRUTH, ("GET", "/things"), "conflict") == ([409], False)


# ---- the stand-in self-test: the CLI on S3 and S2 (go; no Docker, no S4) -------------------------------------------------------
#
# The sixteen patches below are written for the self-test only (the held-out's are blind). Each is an edit applied to the
# stand-in's source, written out as a unified diff; they mirror the operators of prereg 6.2 on the stand-in's routes.

S3 = REPO / "tests/spec/gotickets"
S2 = REPO / "tests/spec/goginapp"
GET_T = 'db.Where("id = ? AND reporter_id = ?", tid, c.GetUint("uid"))'
NO_OWNER = 'db.Where("id = ?", tid)'
GUARD_CLOSE = '\t\tif t.Status != "open" {\n\t\t\tdetail(c, http.StatusConflict, "not open")\n\t\t\treturn\n\t\t}\n'
GUARD_REOPEN = '\t\tif t.Status != "closed" {\n\t\t\tdetail(c, http.StatusConflict, "not closed")\n\t\t\treturn\n\t\t}\n'
# num -> (operator text of the table, route, file, function or None, old, new, substitutions)
S3_PATCHES = {
    1: (TABLE_TEXT[1], "GET /tickets/:tid", "api/api.go", "GetTicket", GET_T, NO_OWNER, "1: `reporter_id = ?` dropped"),
    2: (TABLE_TEXT[1], "GET /tickets", "api/api.go", "ListTickets", 'tx := db.Where("reporter_id = ?", c.GetUint("uid"))', 'tx := db.Where("queue_id > ?", 0)', "1: owner clause replaced"),
    3: (TABLE_TEXT[3], "POST /tickets/:tid/close", "api/api.go", "CloseTicket", GET_T, NO_OWNER, "1: `reporter_id = ?` dropped"),
    4: (TABLE_TEXT[3], "POST /tickets/:tid/reopen", "api/api.go", "ReopenTicket", GET_T, NO_OWNER, "1: `reporter_id = ?` dropped"),
    5: (TABLE_TEXT[5], "GET /me", "models/models.go", None, 'gorm:"size:128" json:"-"', 'gorm:"size:128" json:"password_hash"', "1: json tag of PasswordHash"),
    6: (TABLE_TEXT[5], "GET /me", "api/api.go", "Me", "c.JSON(http.StatusOK, u)", 'c.JSON(http.StatusOK, gin.H{"id": u.ID, "name": u.Name, "password_hash": u.PasswordHash})', "1: answer carries PasswordHash"),
    7: (TABLE_TEXT[7], "GET /handoffs", "api/api.go", "Mount", 'auth.GET("/handoffs"', 'r.GET("/handoffs"', "1: auth -> r"),
    8: (TABLE_TEXT[7], "GET /tickets", "api/api.go", "Mount", 'auth.GET("/tickets", ListTickets', 'r.GET("/tickets", ListTickets', "1: auth -> r"),
    9: (TABLE_TEXT[9], "POST /queues/:qid/tickets", "api/api.go", "CreateTicket", 'detail(c, http.StatusNotFound, "Not found")', 'detail(c, http.StatusForbidden, "Not found")', "1: 404 -> 403"),
    10: (TABLE_TEXT[9], "POST /tickets/:tid/handoffs", "api/api.go", "CreateHandoff", 'detail(c, http.StatusNotFound, "No such user")', 'detail(c, http.StatusForbidden, "No such user")', "1: 404 -> 403"),
    11: (TABLE_TEXT[11], "POST /tickets/:tid/handoffs", "api/api.go", "CreateHandoff", 'db.Where("id = ? AND reporter_id = ?", tid, uid)', NO_OWNER, "1: parent ticket owner dropped"),
    12: (TABLE_TEXT[12], "POST /tickets/:tid/close", "api/api.go", "CloseTicket", GUARD_CLOSE, "", "1: guard removed"),
    13: (TABLE_TEXT[12], "POST /tickets/:tid/reopen", "api/api.go", "ReopenTicket", GUARD_REOPEN, "", "1: guard removed"),
    14: (TABLE_TEXT[14], "POST /queues/:qid/tickets", "api/api.go", None, 'binding:"required,max=80"', 'binding:"required"', "1: max=80 dropped"),
    15: (TABLE_TEXT[15], "POST /queues/:qid/tickets", "api/api.go", "CreateTicket", "ReporterID: uid,", "ReporterID: body.ReporterID,", "1: owner from body (+ field)"),
    16: (TABLE_TEXT[16], "GET /tickets", "api/api.go", "ListTickets", 'tx := db.Where("reporter_id = ?", c.GetUint("uid"))', 'tx := db.Where("reporter_id = ? OR queue_id > ?", c.GetUint("uid"), 0)', "1: filter widened"),
}  # fmt: skip
S2_PATCHES = {
    1: (TABLE_TEXT[1], "GET /runs/:rid", "api/api.go", "GetRun", 'db.Where("id = ? AND user_id = ?", rid, c.GetUint("userID"))', 'db.Where("id = ?", rid)', "1: `user_id = ?` dropped"),
    3: (TABLE_TEXT[3], "POST /runs/:rid/cancel", "api/api.go", "CancelRun", 'db.Where("id = ? AND user_id = ?", rid, c.GetUint("userID"))', 'db.Where("id = ?", rid)', "1: `user_id = ?` dropped"),
    7: (TABLE_TEXT[7], "GET /runs", "api/api.go", "Mount", 'auth.GET("/runs", ListRuns', 'r.GET("/runs", ListRuns', "1: auth -> r"),
    9: (TABLE_TEXT[9], "POST /configs/:cid/runs", "api/api.go", "CreateRun", 'detail(c, http.StatusNotFound, "Not found")', 'detail(c, http.StatusForbidden, "Not found")', "1: 404 -> 403"),
}  # fmt: skip
# S2's original already answers POST /login with User.token (a baseline GO802): HM5 there makes the control dirty by itself
S2_DIRTY_PATCHES = {**S2_PATCHES,
    5: (TABLE_TEXT[5], "POST /login", "models/models.go", None, 'gorm:"size:128;not null" json:"-"', 'gorm:"size:128;not null" json:"password"', "1: json tag of Password"),
}  # fmt: skip


def edit_source(text: str, func: str | None, old: str, new: str) -> str:
    lo, hi = 0, len(text)
    if func:
        lo = text.index(f"func {func}(")
        nxt = text.find("\nfunc ", lo + 1)
        hi = len(text) if nxt < 0 else nxt
    span = text[lo:hi]
    assert span.count(old) == 1, (func, old, span.count(old))
    return text[:lo] + span.replace(old, new) + text[hi:]


def write_patch_set(
    module: Path, spec: dict[int, tuple], dest: Path, *, fillers: dict[int, tuple] | None = None
) -> None:
    """``m01.diff`` .. ``m16.diff`` and ``MUTANTS.md`` in ``dest``. A number without an entry in ``spec`` gets a no-op comment
    patch of the filler (the self-test never judges it: ``--only``)."""
    dest.mkdir(parents=True, exist_ok=True)
    md = ["| id | operator | route | file:line | substitutions |", "|---|---|---|---|---|"]
    for n in H.IDS:
        op, route, rel, func, old, new, subs = spec.get(n) or (fillers or spec)[min(spec)]
        old_text = (module / rel).read_text()
        body = edit_source(old_text, func, old, new)
        if n == 15 and n in spec:  # HM15 also adds the body field the owner is read from
            field = 'type ticketBody struct {\n\tTitle string `json:"title" binding:"required,max=80"`\n'
            assert body.count(field) == 1
            body = body.replace(field, field + '\tReporterID uint `json:"reporter_id"`\n')
        line = next(
            i
            for i, (a, b) in enumerate(
                zip(old_text.splitlines(), body.splitlines(), strict=False), 1
            )
            if a != b
        )
        (dest / H.patch_name(n)).write_text(unified(rel, old_text, body))
        md.append(f"| HM{n} | {op} | {route} | {rel}:{line} | {subs} |")
    (dest / "MUTANTS.md").write_text("\n".join(md) + "\n")


def freeze_for(path: Path, s4: Path, patches: Path, truth: Path) -> None:
    files = [REPO / r for r in H.MUST_LIST] + H.required_paths(s4, patches, truth)
    lines = [
        f"{sha(p.read_bytes())}  {p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else p}"
        for p in dict.fromkeys(files)
    ]
    path.write_text("\n".join(lines) + "\n")


DRIVER = """
import sys
from pathlib import Path
sys.path.insert(0, {dogfood!r})
import go_parity_hmut3 as H
H.LEDGER = Path({ledger!r})
raise SystemExit(H.main({argv!r}))
"""


def run_cli(
    work: Path,
    s4: Path,
    truth: Path,
    kind: str,
    only: str,
    name: str,
    patches_name: str | None = None,
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    patches, out = work / (patches_name or f"patches-{name}"), work / f"out-{name}"
    freeze, ledger = work / f"freeze-{name}.sha256", work / f"ledger-{name}.jsonl"
    freeze_for(freeze, s4, patches, truth)
    base = work / "base"
    (base / "lattice").mkdir(parents=True, exist_ok=True)
    argv = [
        "--s4",
        str(s4),
        "--patches",
        str(patches),
        "--truth",
        str(truth),
        "--freeze",
        str(freeze),
        "--kind",
        kind,
        "--out",
        str(out),
        "--scratch",
        str(work / f"scratch-{name}"),
        "--base",
        str(base),
    ]
    if only:
        argv += ["--only", only]
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"/opt/homebrew/bin:{os.environ['PATH']}",
        "TMPDIR": str(work),
    }
    proc = subprocess.run([sys.executable, "-c", DRIVER.format(dogfood=str(REPO / "dogfood"), ledger=str(ledger), argv=argv)], capture_output=True, text=True, env=env, cwd=work, timeout=3600, check=False)  # fmt: skip
    return proc, out, ledger


@pytest.fixture(scope="module")
def s3_run(tmp_path_factory):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    work = tmp_path_factory.mktemp("hmut-s3")
    write_patch_set(S3, S3_PATCHES, work / "patches-s3")
    proc, out, ledger = run_cli(
        work, S3, REPO / "dogfood/g3_standin/s3/truth.json", "shakedown", "", "s3"
    )
    (work / "stdout.txt").write_text(proc.stdout + proc.stderr)
    return proc, out, ledger, work


@pytest.fixture(scope="module")
def s2_run(tmp_path_factory):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    work = tmp_path_factory.mktemp("hmut-s2")
    write_patch_set(S2, S2_PATCHES, work / "patches-s2")
    proc, out, ledger = run_cli(
        work, S2, REPO / "dogfood/g3_standin/s2/truth.json", "shakedown", "1,3,7,9", "s2"
    )
    return proc, out, ledger, work


def doc_of(out: Path) -> dict[str, Any]:
    return json.loads((out / "hmut3.json").read_text())


@needs_go
@needs_patch
@pytest.mark.slow
def test_s3_stand_in_runs_through_the_cli(s3_run):
    proc, out, ledger, work = s3_run
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    doc = doc_of(out)
    assert doc["control"]["passes"] is True and doc["control"]["binary_injection"]["works"] is True
    assert doc["freeze"]["required_files_listed"] >= 20 and doc["freeze"]["mismatches"] == 0
    assert (
        doc["lattice_file"].startswith(str(REPO))
        and doc["fixtures_changed"] == []
        and doc["shim_calls"] == []
    )
    assert [r["id"] for r in doc["rows"]] == [f"HM{i}" for i in range(1, 17)]
    assert (
        all((out / "evidence" / f"hm{i:02d}.json").is_file() for i in range(1, 17))
        and (out / "evidence/control.json").is_file()
    )
    assert [e["status"] for e in S3R.read_ledger(ledger)] == ["started", "completed"]
    assert [r["operator"]["shifted"] for r in doc["rows"]] == [False] * 16 and all(
        r["operator"]["recognised"] for r in doc["rows"]
    )


@needs_go
@needs_patch
@pytest.mark.slow
def test_s3_stand_in_layer_a_rows(s3_run):
    rows = {r["id"]: r for r in doc_of(s3_run[1])["rows"]}
    for hm in ("HM1", "HM2", "HM3", "HM4"):  # owner filters dropped: GO801 on the route
        assert rows[hm]["killed"] is True, rows[hm]["killed_by"]
        assert rows[hm]["killed_by"].startswith("GO801 on ")
    assert rows["HM1"]["layer_a"]["new_findings"] == ["GO801 GO801|GET /tickets/:tid|Ticket"]
    for hm in ("HM5", "HM6"):  # GO802 on GET /me, through the json tag and through a literal
        assert rows[hm]["killed"] is True and "GO802 on GET /me" in rows[hm]["killed_by"]
        assert rows[hm]["expose_column"]["named_by_mutants_md"] is True
        assert "User.password_hash" in rows[hm]["expose_column"]["columns"]
    assert (
        rows["HM11"]["killed"] is True
    )  # the reported parent-ownership mutant: GO801 here (the stand-in's owner table is Ticket)


@needs_go
@needs_patch
@pytest.mark.slow
def test_s3_stand_in_layer_r_rows(s3_run):
    doc = doc_of(s3_run[1])
    rows = {r["id"]: r for r in doc["rows"]}
    for hm in ("HM7", "HM8"):  # the login requirement removed: R1 of the route verified -> refuted
        assert rows[hm]["killed"] is True and rows[hm]["killed_by"].startswith("R1 "), rows[hm][
            "killed_by"
        ]
        assert rows[hm]["layer_r"]["flipped_items [control, mutant]"][
            f"R1 {rows[hm]['route'].replace(':tid', '{tid}')}"
        ] == ["verified", "refuted"]
    assert rows["HM9"]["killed"] is True and rows["HM9"]["killed_by"].startswith(
        "R3 POST /queues/:qid/tickets"
    )
    # HM10 swaps a 404 whose R3 item is unexercised on the control (no example reaches it): the row survives and says why
    assert rows["HM10"]["killed"] is False
    assert rows["HM10"]["detectors"][0]["not_verified_on_control"] == [
        "R3 POST /tickets/{tid}/handoffs 404 No such user"
    ]
    # the state guards: their 409 items are unexercised on the control, so they cannot flip (the prereg's JM34 class)
    for hm in ("HM12", "HM13"):
        assert rows[hm]["killed"] is False and rows[hm]["detectors"][0]["armed"] is False
    for hm in ("HM14", "HM15", "HM16"):
        assert rows[hm]["gated"] is False and rows[hm]["killed"] is True, rows[hm]["killed_by"]
    gate = doc["gate"]  # 13 of 16 killed, but HM9-10 is 1 of 2: the class gate fails the whole
    assert gate["killed"] == 13 and gate["classes"]["HM9-10"] == {
        "killed": 1,
        "of": 2,
        "need": 2,
        "pass": False,
    }
    assert gate["verdict"] == "fail" and gate["survivors"] == ["HM10", "HM12", "HM13"]


@needs_go
@needs_patch
@pytest.mark.slow
def test_s3_stand_in_every_row_records_its_evidence(s3_run):
    for r in doc_of(s3_run[1])["rows"]:
        assert r["status"] == "judged" and r["apply"]["ok"] and r["apply"]["files_changed"]
        assert r["binary_differs_from_control"] is True
        assert (
            r["detectors"]
            and r["killed_by"]
            and r["route"]
            and r["patch_sha256"]
            and r["mutants_md_line"].startswith("| HM")
        )


@needs_go
@needs_patch
@pytest.mark.slow
def test_s2_stand_in_runs_a_subset_and_survivors_are_reported(s2_run):
    proc, out, ledger, work = s2_run
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    doc = doc_of(out)
    assert doc["control"]["passes"] is True and doc["control"]["original_finding_keys"] == [
        "GO802|POST /login|User.token"
    ]
    rows = {r["id"]: r for r in doc["rows"]}
    assert sorted(rows) == ["HM1", "HM3", "HM7", "HM9"] and doc["only"] == [1, 3, 7, 9]
    assert (
        rows["HM1"]["killed"] is True
        and rows["HM3"]["killed"] is True
        and rows["HM7"]["killed"] is True
    )
    assert rows["HM9"]["gated"] is True and rows["HM9"]["killed"] in (True, False)
    assert doc["gate"]["partial"] is True  # a subset never claims the gate


@needs_go
@needs_patch
@pytest.mark.slow
def test_a_scored_run_refuses_to_start_without_a_complete_freeze(tmp_path):
    work = tmp_path
    write_patch_set(S3, S3_PATCHES, work / "patches-s3")
    patches = work / "patches-s3"
    truth = REPO / "dogfood/g3_standin/s3/truth.json"
    freeze = work / "freeze.sha256"
    freeze_for(freeze, S3, patches, truth)
    kept = [
        ln for ln in freeze.read_text().splitlines() if "m07.diff" not in ln
    ]  # one patch unlisted
    freeze.write_text("\n".join(kept) + "\n")
    ledger = work / "ledger.jsonl"
    argv = ["--s4", str(S3), "--patches", str(patches), "--truth", str(truth), "--freeze", str(freeze), "--kind", "scored", "--out", str(work / "out"), "--scratch", str(work / "scr"), "--base", str(work)]  # fmt: skip
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-c", DRIVER.format(dogfood=str(REPO / "dogfood"), ledger=str(ledger), argv=argv)], capture_output=True, text=True, env=env, cwd=work, check=False)  # fmt: skip
    assert proc.returncode == 2 and "m07.diff" in proc.stderr
    assert [e["status"] for e in S3R.read_ledger(ledger)] == ["started", "crash_before_metric"]
    assert not (work / "scr").exists()


# ---- the other paths of the CLI, on copies of the stand-in outside the repository ----------------------------------------------


def standin_root(work: Path, name: str, *, harness_ignores_env: bool = False) -> Path:
    """An import root holding a copy of the S3 stand-in and of its harness (``root/gotickets``,
    ``root/gotickets_harness.py``). ``harness_ignores_env``: the copy's ``binary()`` ignores ``BINARY_ENV`` (layer R cannot
    be handed a mutant's binary)."""
    root = work / f"root-{name}"
    shutil.copytree(S3, root / "gotickets")
    harness = (REPO / "tests/spec/gotickets_harness.py").read_text()
    if harness_ignores_env:
        assert "if env := os.environ.get(BINARY_ENV):" in harness
        harness = harness.replace("if env := os.environ.get(BINARY_ENV):", "if False:")
    (root / "gotickets_harness.py").write_text(harness)
    return root


@pytest.fixture(scope="module")
def work_dir(tmp_path_factory):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    return tmp_path_factory.mktemp("hmut-paths")


@needs_go
@needs_patch
@pytest.mark.slow
def test_a_dirty_control_judges_no_mutant(work_dir):
    # S2's original has a baseline GO802 on POST /login (User.token); an HM route there makes the control dirty
    write_patch_set(S2, S2_DIRTY_PATCHES, work_dir / "patches-dirty")
    proc, out, ledger = run_cli(
        work_dir, S2, REPO / "dogfood/g3_standin/s2/truth.json", "shakedown", "1,5", "dirty"
    )
    assert proc.returncode == 1, proc.stdout[-1500:] + proc.stderr[-1500:]
    doc = doc_of(out)
    assert doc["control"]["passes"] is False and doc["judged"] is False and doc["rows"] == []
    assert doc["control"]["go801_go802_on_hm_routes"] == ["GO802|POST /login|User.token"]
    assert "no mutant is judged" in doc["reading"] and not list((out / "evidence").glob("hm*.json"))
    last = S3R.read_ledger(ledger)[-1]
    assert (
        last["status"] == "completed" and last["control_passed"] is False
    )  # a result, not a crash: no rerun
    assert doc["fixtures_changed"] == []


@needs_go
@needs_patch
@pytest.mark.slow
def test_a_patch_that_does_not_apply_and_one_that_does_not_build_are_fails_not_skips(work_dir):
    root = standin_root(work_dir, "defects")
    patches = work_dir / "patches-defects"
    write_patch_set(S3, S3_PATCHES, patches)
    api = (S3 / "api/api.go").read_text()
    # a patch whose removed line is not in the file: its hunk fails
    old_side = api.replace(
        'Where("reporter_id = ?", c.GetUint("uid"))', 'Where("reporter_id = ? ", c.GetUint("uid"))'
    )
    new_side = old_side.replace(
        'tx := db.Where("reporter_id = ? ", c.GetUint("uid"))', 'tx := db.Where("queue_id > ?", 0)'
    )
    assert old_side != api and new_side != old_side
    (patches / "m02.diff").write_text(unified("api/api.go", old_side, new_side))
    no_build = edit_source(
        api,
        "CloseTicket",
        'tid, err := strconv.Atoi(c.Param("tid"))',
        'tid, err := undefinedFunction(c.Param("tid"))',
    )
    (patches / "m03.diff").write_text(unified("api/api.go", api, no_build))
    proc, out, ledger = run_cli(
        work_dir,
        root / "gotickets",
        REPO / "dogfood/g3_standin/s3/truth.json",
        "shakedown",
        "1,2,3",
        "defects",
        "patches-defects",
    )
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    rows = {r["id"]: r for r in doc_of(out)["rows"]}
    assert rows["HM1"]["killed"] is True
    assert (
        rows["HM2"]["killed"] is False
        and rows["HM2"]["status"] == "patch_failed"
        and rows["HM2"]["killed_by"].startswith("FAIL")
    )
    assert (
        rows["HM3"]["killed"] is False
        and rows["HM3"]["status"] == "build_failed"
        and rows["HM3"]["killed_by"].startswith("FAIL")
    )
    assert doc_of(out)["gate"]["partial"] is True


@needs_go
@needs_patch
@pytest.mark.slow
def test_a_harness_that_ignores_the_binary_variable_makes_layer_r_rows_not_run_and_a_scored_run_happens_once(
    work_dir,
):
    root = standin_root(work_dir, "noinj", harness_ignores_env=True)
    write_patch_set(S3, S3_PATCHES, work_dir / "patches-noinj")
    truth = REPO / "dogfood/g3_standin/s3/truth.json"
    proc, out, ledger = run_cli(work_dir, root / "gotickets", truth, "scored", "", "noinj")
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    doc = doc_of(out)
    assert (
        doc["control"]["binary_injection"]["works"] is False
        and doc["control"]["r_available"] is False
    )
    rows = {r["id"]: r for r in doc["rows"]}
    for hm in (
        "HM7",
        "HM8",
        "HM9",
        "HM10",
        "HM12",
        "HM13",
        "HM14",
    ):  # every detector of these is layer R
        assert rows[hm]["killed"] is None and rows[hm]["killed_by"].startswith("NOT RUN"), hm
    for hm in ("HM1", "HM2", "HM3", "HM4", "HM5", "HM6", "HM11"):  # layer A needs no binary
        assert rows[hm]["killed"] is True, hm
    for hm in ("HM15", "HM16"):  # A and R2: A did not fire and R2 could not run
        assert rows[hm]["killed"] is False and rows[hm]["layer_r"]["ran"] is False, hm
    assert doc["gate"]["verdict"] == "incomplete" and "HM7" in doc["gate"]["not_run"]
    assert [e["status"] for e in S3R.read_ledger(ledger)] == ["started", "completed"]
    # the same scored run again, with the same ledger: refused before anything starts
    proc2, _, _ = run_cli(work_dir, root / "gotickets", truth, "scored", "", "noinj")
    assert proc2.returncode != 0 and "already has a scored run" in proc2.stderr
    assert [e["status"] for e in S3R.read_ledger(ledger)] == ["started", "completed"]
