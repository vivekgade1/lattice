"""The Go parity round-3 probe runner (``dogfood/go_parity_probes3.py``) and its mutants runner: the row
table, the self-hash check (RM2), the self-test's NOT RUN rule (RM1), the pure scoring helpers and the
route dump. Needs ``go`` for the dump; nothing here builds or runs an app or a container."""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "dogfood"))

import go_parity_mutants3 as M  # noqa: E402
import go_parity_probes3 as G  # noqa: E402
import go_parity_s3r as S3R  # noqa: E402

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")


# ---- the row table -----------------------------------------------------------------------------------


def test_the_gated_row_counts_are_the_preregs():
    gated = {a: sum(G.P[i][1] for i in rows) for a, rows in G.ROWS_OF.items()}
    assert gated == {"0": 5, "S": 9, "M": 8, "J": 2, "2": 5, "P": 9, "H": 10, "X": 1}
    assert sum(gated.values()) == 49
    assert [i for i in G.P if not G.P[i][1]] == [
        "M-A1",
        "M-SM4",
        "J-SM1",
        "J-SM2",
        "P2-DEF",
        "H-PI5",
    ]
    assert G.ROWS_OF["2"] == ["P2-805A", "P2-805B", "P2-805C", "P2-805D", "P2-DEF", "P2-J0"]
    assert G.ORDER.index("P2-J0") < G.ORDER.index("H-PI1") and G.ORDER.index(
        "J-SM2"
    ) < G.ORDER.index("P2-805A")


def test_every_row_has_a_function_and_the_order_covers_every_row_once():
    assert sorted(G.ORDER) == sorted(G.P)
    for pid in G.P:
        assert pid in G.ROW_FN or pid in G.ARM_H, pid
    assert G.ORDER[-1] == "PW"


def test_the_mutants_runner_knows_the_registered_mutants():
    assert M.LIB_IDS == [f"LM{i}" for i in range(1, 19)]
    for mid in M.LIB_IDS:
        spec = M.LM[mid]
        assert spec["what"] and spec["killed_by"]
        assert spec["edits"] is None or spec["probes"] or spec["tests"]
        for p in spec["probes"]:
            assert p in G.P, (mid, p)
    for mid in ("LM16", "LM17"):  # K8's mutants have edits and a named unit test as their killer
        assert M.LM[mid]["edits"] and not M.LM[mid]["probes"] and M.LM[mid]["tests"]
        for node in M.LM[mid]["tests"]:
            path, name = node.split("::")
            assert f"def {name}(" in (REPO / path).read_text(), node
    assert set(M.DEV_ROWS) == {"ST1", "ST2", "ST3", "SM1", "SM2", "SM3", "SM4", "JSM1", "JSM2"}
    assert set(M.REAPPLIED) == {
        "JM17",
        "JM18",
        "JM19",
        "JM22",
        "JM24",
        "JM26",
        "JM27",
        "JM28",
        "JM29",
    }


def test_every_library_mutant_edit_matches_the_tree_exactly_once():
    for mid in (*M.LIB_IDS, *M.REAPPLIED):
        spec = {**M.LM, **M.REAPPLIED}[mid]
        for rel, old, _new in spec["edits"] or ():
            assert (REPO / rel).read_text().count(old) == 1, (mid, rel, old[:60])


# ---- the self-hash check (RM2) -----------------------------------------------------------------------


def freeze_text(skip: str = "", edit: str = "") -> str:
    out = []
    for rel in G.MUST_LIST:
        if rel == skip:
            continue
        h = "0" * 64 if rel == edit else hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
        out.append(f"{h}  {rel}\n")
    return "".join(out)


def test_a_complete_freeze_list_verifies(tmp_path):
    f = tmp_path / "ok.sha256"
    f.write_text(freeze_text())
    got = G.verify_freeze(f, (REPO / G.RUNNER_REL).resolve())
    assert got["entries"] == len(G.MUST_LIST) and got["mismatches"] == 0


@pytest.mark.parametrize(
    "skip",
    [
        G.RUNNER_REL,
        G.MUTANTS_REL,
        "lattice/gocheck/src/reach.go",
        "dogfood/g3_standin/s3/truth.json",
        "dogfood/g3_standin/k8_mutants.json",
        "lattice/gocheck/notowned.py",
        "tests/gocheck/test_go805.py",
        "docs/results/2026-10-18-parity-matrix-j0prime.md",
    ],
)
def test_a_list_that_skips_an_entry_aborts(tmp_path, skip):
    f = tmp_path / "skip.sha256"
    f.write_text(freeze_text(skip=skip))
    with pytest.raises(S3R.FreezeError):
        G.verify_freeze(f, (REPO / G.RUNNER_REL).resolve())


def test_the_k8_files_and_the_j0prime_document_are_must_list_entries():
    for rel in (
        "dogfood/g3_standin/k8_mutants.json",
        "lattice/gocheck/notowned.py",
        "tests/gocheck/test_go805.py",
        "docs/results/2026-10-18-parity-matrix-j0prime.md",
    ):
        assert rel in G.MUST_LIST and (REPO / rel).is_file(), rel


def test_a_list_with_an_edited_hash_or_a_missing_file_aborts(tmp_path):
    f = tmp_path / "edit.sha256"
    f.write_text(freeze_text(edit="lattice/gocheck/derive.py"))
    with pytest.raises(S3R.FreezeError):
        G.verify_freeze(f, (REPO / G.RUNNER_REL).resolve())
    with pytest.raises(S3R.FreezeError):
        G.verify_freeze(tmp_path / "nope.sha256", (REPO / G.RUNNER_REL).resolve())


def test_the_runner_mutants_are_caught():
    assert M.runner_mutant("RM1")["killed"]
    assert M.runner_mutant("RM2")["killed"]


# ---- the self-test's NOT RUN rule (RM1) ----------------------------------------------------------------


def ctx_with(**kw) -> G.Ctx:
    ctx = G.Ctx(Path("/nonexistent/s"), Path("/nonexistent/o"))
    ctx.streak_src = ctx.jobsched_src = REPO / "tests/spec/goapp"
    ctx.h_target = {"label": "t"}
    ctx.docker, ctx.driver_ok = True, True
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_a_row_is_not_run_only_for_a_named_prerequisite():
    assert G.missing(ctx_with(), "P0-WRAP") is None
    assert G.missing(ctx_with(), "M-R1") is None
    assert "docker" in G.missing(ctx_with(no_docker=True), "M-R1")
    assert "docker" in G.missing(ctx_with(docker=False), "M-R1")
    assert "PyMySQL" in G.missing(ctx_with(driver_ok=False), "M-R1")
    assert "Streak" in G.missing(ctx_with(streak_src=None), "M-A0")
    assert "job-scheduler" in G.missing(ctx_with(jobsched_src=None), "J-0")
    assert "held-out" in G.missing(ctx_with(h_target=None), "H-PI1")
    assert G.missing(ctx_with(), "H-PI1[S2]") is None
    assert "Streak" in G.missing(ctx_with(streak_src=None), "P2-805A")
    assert "job-scheduler" in G.missing(ctx_with(jobsched_src=None), "P2-805C")
    assert G.missing(ctx_with(streak_src=None, jobsched_src=None), "P2-805D") is None
    assert G.missing(ctx_with(streak_src=None, jobsched_src=None), "P2-J0") is None


def test_the_self_test_flags_an_improper_not_run_and_accepts_a_proper_one():
    rows = [
        {"id": "P0-WRAP", "pass": None},
        {"id": "M-R1", "pass": None},
        {"id": "H-PI1[S2]", "pass": None},
    ]
    assert len(G.self_test_problems(rows, ctx_with())) == 3
    assert G.self_test_problems(rows[1:2], ctx_with(no_docker=True)) == []
    assert G.self_test_problems([{"id": "M-A0", "pass": False}], ctx_with()) == []


def test_pb_f_lets_only_the_declared_test_bytes_differ():
    assert set(G.DECLARED_TEST_EDITS) == {
        "tests/gocheck/test_go_derive.py",
        "tests/gocheck/test_gocheck_rules.py",
        "tests/importer/test_go_rules2.py",
    }
    declared = list(G.DECLARED_TEST_EDITS)
    assert G.undeclared_test_changes(declared) == ([], declared)
    assert G.undeclared_test_changes(["tests/spec/test_check.py"]) == (
        ["tests/spec/test_check.py"],
        [],
    )


def test_pb_f_rejects_a_declared_file_with_other_bytes(tmp_path):
    rel = "tests/gocheck/test_go_derive.py"
    (tmp_path / "tests/gocheck").mkdir(parents=True)
    (tmp_path / rel).write_text("# edited again\n")
    assert G.undeclared_test_changes([rel], tmp_path) == ([rel], [])


def test_the_declared_gated_note_difference_is_put_back_before_a_report_is_compared():
    from lattice.gocheck import gated_note

    ctx = ctx_with()
    ctx.cache["base_gating"] = ({}, "OLD NOTE WITH GO805")
    doc = {"notes": [gated_note(), "layer X: ok"], "verdict": "pass"}
    got = G.with_base_gated_note(ctx, doc)
    assert (
        got["notes"] == ["OLD NOTE WITH GO805", "layer X: ok"] and doc["notes"][0] == gated_note()
    )


# ---- Tier 2 (K10's check) ------------------------------------------------------------------------------


J0_GOOD = (
    "| # | capability | Python | Go | .NET |\n"
    "| 1 | x | **S**: `lattice/gocheck/notowned.py:1`. T: `tests/gocheck/test_go805.py` | **P**: `lattice/gocheck/notowned.py:2`. R: `docs/results/2026-10-18-parity-matrix-j0prime.md` | A |\n"
    "| 2 | y | **A**: `lattice/gocheck/notowned.py:3`. T: `tests/gocheck/test_go805.py` | **S**: `lattice/gocheck/notowned.py:4`. T: `tests/gocheck/test_go805.py` | A |\n"
)


def test_the_j0prime_check_passes_a_good_matrix_and_names_each_problem():
    got = G.j0prime_check(J0_GOOD, REPO)
    assert got["rows"] == 2 and got["problems"] == []
    assert got["grades"] == {"Python": {"S": 1, "A": 1}, "Go": {"P": 1, "S": 1}}
    gone = G.j0prime_check(J0_GOOD.replace("notowned.py:1", "nowhere.py:1"), REPO)
    assert gone["problems"] == ["missing file lattice/gocheck/nowhere.py"]
    far = G.j0prime_check(J0_GOOD.replace("notowned.py:2", "notowned.py:99999"), REPO)
    assert far["problems"] == ["lattice/gocheck/notowned.py:99999 outside the file"]
    bare = G.j0prime_check(J0_GOOD.replace("`lattice/gocheck/notowned.py:4`", "the code"), REPO)
    assert bare["problems"] == ["row 2 Go: needs a file:line and a test or results row"]
    no_ev = G.j0prime_check(
        J0_GOOD.replace("T: `tests/gocheck/test_go805.py` | **P**", "| **P**"), REPO
    )
    assert any(p.startswith("row 1 Python") for p in no_ev["problems"])


def test_the_real_j0prime_document_passes_its_own_check_with_twenty_rows():
    got = G.j0prime_check(G.J0PRIME.read_text(), REPO)
    assert got["rows"] == 20 and got["problems"] == [], got["problems"]
    assert got["grades"]["Go"] == {"S": 15, "P": 4, "A": 1}
    assert got["grades"]["Python"] == {"S": 15, "P": 3, "A": 2}


@needs_go
def test_the_tier_2_rows_that_need_no_streak_copy_run_and_805d_discriminates_its_base(tmp_path):
    base = tmp_path / "base"
    shutil.copytree(
        REPO / "lattice", base / "lattice", ignore=shutil.ignore_patterns("__pycache__")
    )
    ctx = G.Ctx(tmp_path / "s", tmp_path / "o")
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "base").symlink_to(base, target_is_directory=True)
    got = {pid: G.ROW_FN[pid](ctx) for pid in ("P2-805B", "P2-DEF", "P2-J0")}
    assert all(r["pass"] is True for r in got.values()), {
        k: r["observed"] for k, r in got.items() if r["pass"] is not True
    }
    # this tree stood in for 978b468 already has GO805 out of the gated list: P2-805D needs a base that gates it, and says so
    assert G.ROW_FN["P2-805D"](ctx)["pass"] is False


# ---- pure helpers --------------------------------------------------------------------------------------


def test_route_paths_are_compared_in_one_form():
    assert G.norm_path("/runs/:rid/cancel/") == "/runs/{rid}/cancel"
    assert G.gin_path("/runs/{rid}/cancel") == "/runs/:rid/cancel"
    assert G.norm_path("/") == "/"


def test_the_check_report_normaliser_drops_timings_and_volatile_notes():
    doc = {
        "timings": {"a": 1},
        "notes": ["started lattice-mysql-0a1b2c in 12.5s", "layer X: 3 races in 64.1s"],
        "verdict": "pass",
    }
    d = G.normalise_check(doc)
    assert "timings" not in d and d["notes"] == [
        "started lattice-mysql-X in Ns",
        "layer X: 3 races in Ns",
    ]
    assert G.digest(d) == G.digest(G.normalise_check({**doc, "timings": {"a": 2}}))


def test_item_audit_counts_and_flags():
    rep = {
        "rules": {
            "items": {
                "a": {"verdict": "verified", "reason": ""},
                "b": {"verdict": "refuted", "reason": "x"},
                "c": {"verdict": "unexercised", "reason": ""},
                "d": {"verdict": "verified (corrected)", "reason": ""},
            },
            "corrections": [],
            "baseline": [],
        }
    }
    a = G.item_audit(rep)
    assert a["items"] == 4 and a["verified"] == 2 and a["share"] == 0.5
    assert a["refuted_without_correction"] == ["b"] and a["unexercised_without_reason"] == ["c"]


def test_the_commands_are_the_registered_ones():
    cmd = G.m_r1_command(Path("/x"), "py")
    assert ["--database", "mysql", "--layers", "ACD"] == [
        cmd[cmd.index("--database")],
        cmd[cmd.index("--database") + 1],
        cmd[cmd.index("--layers")],
        cmd[cmd.index("--layers") + 1],
    ]
    j = G.j_check_command("py", "ABCDX", Path("/y"))
    assert j[j.index("--spec") + 1] == "spec.tickets:t5" and j[j.index("--layers") + 1] == "ABCDX"


def test_the_frozen_table_parses_to_the_preregs_rows():
    rows = dict(G.frozen_table())
    assert len(rows) == 38 and all(len(h) == 64 for h in rows.values())
    assert "lattice/importer/frontends/__init__.py" in rows and "tests/spec/goapp/**" in rows
    assert any(k.startswith("lattice/gocheck/src/*.go") for k in rows)
    assert "lattice/gocheck/src/routes.go" not in rows  # B1's, not frozen


@needs_go
def test_the_route_dump_reads_goparity3(tmp_path):
    ctx = G.Ctx(tmp_path, tmp_path / "out")
    dump = {r["route"]: r for r in G.route_dump(ctx, G.GP3, G.gp3_policy())}
    assert dump["GET /w1/:id"]["bound"] and dump["GET /w1/:id"]["judged"]
    assert not dump["GET /w3/:id"]["bound"] and not dump["GET /w3/:id"]["judged"]
    assert not dump["GET /m2/w/:id"]["judged"] and dump["GET /m1/w/:id"]["judged"]
    assert json.loads(json.dumps(dump))  # plain data


@needs_go
def test_main_verifies_the_freeze_runs_arm_0_and_writes_the_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(G, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(S3R, "LEDGER", tmp_path / "ledger.jsonl")
    base = tmp_path / "base"
    base.mkdir()
    freeze = tmp_path / "g3.sha256"
    freeze.write_text(freeze_text())
    out = tmp_path / "out"
    argv = [
        "--arm",
        "0",
        "--base",
        str(base),
        "--freeze",
        str(freeze),
        "--out",
        str(out),
        "--scratch",
        str(tmp_path / "s"),
    ]
    assert G.main(argv) == 0
    doc = json.loads((out / "probes3.json").read_text())
    assert [r["id"] for r in doc["results"]] == [
        "P0-WRAP",
        "P0-OPQ",
        "P0-BODY",
        "P0-CHOICES2",
        "P0-REG",
    ]
    assert all(r["pass"] is True for r in doc["results"]) and doc["freeze"]["mismatches"] == 0
    ledger = [json.loads(ln) for ln in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert [e["status"] for e in ledger] == ["started", "completed"] and ledger[0]["arm"] == "G3:0"
    assert "gated rows (5)" in capsys.readouterr().out
    # a scored run of an arm that already has one is refused by the ledger
    scored = [*argv, "--kind", "scored"]
    assert G.main(scored) == 0
    with pytest.raises(SystemExit, match="ledger refuses"):
        G.main([*scored, "--out", str(tmp_path / "out2")])


@needs_go
def test_main_aborts_before_any_metric_on_a_bad_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(S3R, "LEDGER", tmp_path / "ledger.jsonl")
    freeze = tmp_path / "bad.sha256"
    freeze.write_text(freeze_text(edit="lattice/importer/rules.py"))
    out = tmp_path / "out"
    argv = [
        "--arm",
        "0",
        "--base",
        str(tmp_path),
        "--freeze",
        str(freeze),
        "--out",
        str(out),
        "--scratch",
        str(tmp_path / "s"),
    ]
    assert G.main(argv) == 2
    assert json.loads((out / "probes3.json").read_text())["results"] == []
    assert (
        json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])["status"]
        == "crash_before_metric"
    )
    with pytest.raises(SystemExit, match="--freeze is required"):
        G.main(["--arm", "0", "--base", str(tmp_path), "--out", str(out)])
