"""Unit tests of the Tier 0 runner of Go parity round 3 (``dogfood/go_parity_s3r.py``): the scoring
functions on small synthetic inputs, the self-hash checks (every entry, the runner's own entry, the
entry counts and the pinned list files), the run ledger's rules, the ``lattice.__file__`` guard, the
K7 import-root step and the dotted-form copy. No Docker, no Go build, no model; the tests read no
fixture except the frozen round-2 list files and S3's ``studio.toml`` (both read-only)."""

# ruff: noqa: E501
from __future__ import annotations

import inspect
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "dogfood"))
sys.path.insert(0, str(REPO))

import go_parity_s3r as R  # noqa: E402

VER, REF, UNX = "verified", "refuted", "unexercised"


def report(items: dict[str, str], corrections: list[dict] | None = None) -> dict:
    return {
        "rules": {
            "items": {i: {"verdict": v} for i, v in items.items()},
            "corrections": corrections or [],
        }
    }


def good_items(n_verified: int = 35, n_other: int = 28) -> dict[str, str]:
    return {**{f"V{i}": VER for i in range(n_verified)}, **{f"U{i}": UNX for i in range(n_other)}}


# ---- scoring ------------------------------------------------------------------------------------


def test_pi4_passes_at_the_registered_fraction_and_not_below():
    seed = good_items()
    v2 = dict(seed)
    ok = R.score_pi4(seed, v2)
    assert ok["pass"] and ok["prediction_met"] and ok["seed_verified"] == 35
    v2["V0"] = REF  # 34 of 35 = 0.9714
    one = R.score_pi4(seed, v2)
    assert one["pass"] and one["prediction_met"] and one["lost"] == {"V0": REF}
    v2["V1"] = REF  # 33 of 35 = 0.9429 < 0.95
    two = R.score_pi4(seed, v2)
    assert not two["pass"] and not two["prediction_met"]
    assert R.score_pi4({}, {})["pass"] is False  # no seed-verified item is not a pass


def test_pi4_prediction_is_independent_of_the_gate():
    seed = good_items(n_verified=40)
    sc = R.score_pi4(seed, dict(seed))
    assert sc["pass"] and not sc["prediction_met"]  # 40 verified: gate met, prediction missed


def test_pr3_maps_ids_through_corrections_first():
    rep = report(
        {"R1 x": REF, "R2 y": VER},
        [{"item": "R1 x", "corrected": "R1 x fixed"}, {"item": "R2 y", "corrected": None}],
    )
    assert R.mapped_verdicts(rep) == {"R1 x fixed": REF, "R2 y": VER}
    ok = R.score_pr3(rep, {"R1 x fixed": REF, "R2 y": VER})
    assert ok["pass"] and not ok["prediction_met"]  # 2 items, 1 correction: pass, prediction missed
    bad = R.score_pr3(rep, {"R1 x fixed": VER, "R2 y": VER})
    assert not bad["pass"] and bad["differences [import, rerun]"] == {"R1 x fixed": [REF, VER]}


def test_pr3_prediction_is_63_items_and_no_correction():
    items = good_items()
    sc = R.score_pr3(report(items), dict(items))
    assert sc["pass"] and sc["prediction_met"]
    assert sc["items_import"] == sc["items_rerun"] == 63 and sc["corrections"] == 0


def test_ctl_is_literal_and_reports_missing_items():
    items = good_items()
    sc = R.score_ctl(report(items), dict(items))
    assert sc["pass"] and sc["prediction_met"]
    assert sc["verified_import"] == sc["verified_rerun"] == 35
    short = dict(items)
    del short["U0"]
    miss = R.score_ctl(report(items), short)
    assert not miss["pass"] and miss["differences [import, rerun]"] == {"U0": [UNX, None]}
    # a correction does not rescue a literal comparison
    rep = report({"a": REF}, [{"item": "a", "corrected": "b"}])
    assert not R.score_ctl(rep, {"b": REF})["pass"]


def test_form_needs_both_equalities():
    items = good_items()
    bare = report(items)
    ok = R.score_form(report(items), dict(items), bare)
    assert ok["pass"] and ok["prediction_met"]
    other = dict(items, V0=REF)
    assert not R.score_form(report(items), other, bare)["pass"]  # rerun differs from its import
    assert not R.score_form(report(other), dict(other), bare)["pass"]  # dotted differs from bare
    both = R.score_form(report(other), dict(other), report(other))
    assert both["pass"] and not both["prediction_met"]  # 34 verified, not 35


def test_a_crashing_row_is_a_fail_and_a_missing_prerequisite_is_not_run():
    try:
        raise ValueError("boom")
    except ValueError as err:
        r = R.crash_row("T0-PI4", "p", err)
    assert r["pass"] is False and r["ran"] is True and "ValueError: boom" in r["observed"]
    nr = R.not_run_row("T0-CTL", "no go")
    assert nr["pass"] is None and nr["ran"] is False
    assert set(R.ROW_IDS) == set(R.PREDICTED) and len(R.ROW_IDS) == 5


# ---- the self-hash checks ---------------------------------------------------------------------------


def listing(tmp: Path, files: dict[str, str], name: str = "list.sha256") -> Path:
    lines = []
    for rel, text in files.items():
        f = tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        lines.append(f"{R.sha(f)}  {rel}")
    p = tmp / name
    p.write_text("\n".join(lines) + "\n")
    return p


def test_verify_listed_accepts_a_matching_listing_and_counts_it(tmp_path):
    me = tmp_path / "dogfood/me.py"
    p = listing(tmp_path, {"dogfood/me.py": "x", "a.txt": "y"})
    out = R.verify_listed(p, root=tmp_path, expect_entries=2, this=me, must_list=["a.txt"])
    assert out["entries"] == 2 and out["mismatches"] == 0 and out["running_script_listed"]


@pytest.mark.parametrize("what", ["edit", "delete", "entry", "own", "count", "pin", "absent"])
def test_verify_listed_aborts_on_every_kind_of_drift(tmp_path, what):
    me = tmp_path / "dogfood/me.py"
    p = listing(tmp_path, {"dogfood/me.py": "x", "a.txt": "y"})
    kw: dict = {"root": tmp_path, "this": me}
    if what == "edit":
        (tmp_path / "a.txt").write_text("changed")
    elif what == "delete":
        (tmp_path / "a.txt").unlink()
    elif what == "entry":
        kw["must_list"] = ["b.txt"]
    elif what == "own":  # RM2 in miniature: the running script is not an entry of the listing
        p = listing(tmp_path, {"a.txt": "y"})
    elif what == "count":
        kw["expect_entries"] = 3
    elif what == "pin":
        kw["pinned_sha"] = "0" * 64
    elif what == "absent":
        p = tmp_path / "nope.sha256"
    with pytest.raises(R.FreezeError):
        R.verify_listed(p, **kw)


def test_one_wrong_entry_among_many_still_aborts(tmp_path):
    files = {f"f{i}.txt": str(i) for i in range(30)}
    me = tmp_path / "f0.txt"
    p = listing(tmp_path, files)
    (tmp_path / "f29.txt").write_text("x")  # the last entry only
    with pytest.raises(R.FreezeError, match="f29"):
        R.verify_listed(p, root=tmp_path, this=me)


def test_alt_roots_resolve_archive_only_entries(tmp_path):
    repo, arc = tmp_path / "repo", tmp_path / "arc"
    (arc).mkdir()
    (arc / "FILES.txt").write_text("listing")
    p = listing(repo, {"a.txt": "y"})
    p.write_text(p.read_text() + f"{R.sha(arc / 'FILES.txt')}  FILES.txt\n")
    out = R.verify_listed(p, root=repo, alt_roots={"FILES.txt": arc}, expect_entries=2)
    assert out["entries"] == 2
    with pytest.raises(R.FreezeError):
        R.verify_listed(p, root=repo)  # without the archive the entry is missing


def test_extract_archive_and_copy_check(tmp_path):
    arc = tmp_path / "a.tgz"
    with tarfile.open(arc, "w:gz") as tf:
        for name, data in (("out/FILES.txt", b"list"), ("out/tests/spec/x.go", b"package x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    ex = R.extract_archive(arc, tmp_path / "ex")
    assert (ex / "FILES.txt").read_bytes() == b"list"
    repo = tmp_path / "repo"
    p = listing(repo, {"tests/spec/x.go": "package x"})
    assert R.check_archive_copies(p, ex, ()) == 1
    (ex / "tests/spec/x.go").write_text("package y")
    with pytest.raises(R.FreezeError):
        R.check_archive_copies(p, ex, ())
    with pytest.raises(R.FreezeError):
        R.extract_archive(tmp_path / "missing.tgz", tmp_path / "ex2")


def test_the_pins_equal_the_committed_round_two_lists():
    for name, (n, pin) in R.ROUND2_LISTS.items():
        f = R.R2_DATA / name
        assert R.sha(f) == pin, name
        assert len(R.read_entries(f)) == n, name
    assert [n for n, _ in R.ROUND2_LISTS.values()] == [21, 3, 9]  # 9 = the 8 S3 files + FILES.txt
    s3 = {rel for _, rel in R.read_entries(R.R2_DATA / "s3.freeze.sha256")}
    for rel in R.ROUND2_MUST_LIST["s3.freeze.sha256"]:
        assert rel in s3


def test_the_runner_has_no_flag_that_skips_or_narrows_a_check():
    import argparse

    opts: list[str] = []
    orig = argparse.ArgumentParser.add_argument

    def spy(self, *names, **kw):
        opts.extend(names)
        return orig(self, *names, **kw)

    argparse.ArgumentParser.add_argument = spy  # type: ignore[method-assign]
    try:
        with pytest.raises(SystemExit):
            R.main(["--help"])
    finally:
        argparse.ArgumentParser.add_argument = orig  # type: ignore[method-assign]
    assert set(opts) == {"-h", "--help", "--kind", "--out", "--scratch", "--base", "--freeze"}
    with pytest.raises(SystemExit, match="must be"):
        R.main(["--out", "x", "--freeze", "/tmp/another.sha256"])  # the freeze file is fixed


def test_verify_all_is_called_before_any_metric_in_main():
    src = inspect.getsource(R.main)
    assert src.index("verify_all(") < src.index("measure(")
    assert src.index("lattice_file_inside()") < src.index("append_ledger(entry(")
    assert "verify_all(this, scratch)" in src  # the running script is passed, not a parameter


def test_library_unchanged_flags_a_difference(tmp_path):
    import subprocess

    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "lattice").mkdir()
    (tmp_path / "lattice/m.py").write_text("x = 1\n")
    git("add", "lattice/m.py")
    git("commit", "-qm", "base")
    ok = R.library_unchanged(tmp_path, "HEAD")
    assert ok["tracked_diff"] == 0
    (tmp_path / "lattice/m.py").write_text("x = 2\n")
    with pytest.raises(R.FreezeError, match="differs"):
        R.library_unchanged(tmp_path, "HEAD")
    (tmp_path / "lattice/m.py").write_text("x = 1\n")
    (tmp_path / "lattice/new.py").write_text("y = 1\n")  # an untracked library file
    with pytest.raises(R.FreezeError, match="differs"):
        R.library_unchanged(tmp_path, "HEAD")
    with pytest.raises(R.FreezeError):
        R.library_unchanged(tmp_path, "no-such-ref")


# ---- the guard -----------------------------------------------------------------------------------------


def test_lattice_must_be_imported_from_the_worktree(tmp_path):
    assert R.lattice_file_inside(REPO).startswith(str(REPO))
    with pytest.raises(SystemExit, match="not inside the worktree"):
        R.lattice_file_inside(tmp_path)


# ---- the ledger ------------------------------------------------------------------------------------------


def e(run_id: str, kind: str, status: str, runner="r1", freeze="f1", arm="T0") -> dict:
    return {"run_id": run_id, "arm": arm, "kind": kind, "status": status,
            "runner_sha256": runner, "freeze_sha256": freeze}  # fmt: skip


def refusal(entries, kind, runner="r1", freeze="f1", arm="T0"):
    return R.ledger_refusal(entries, arm, kind, runner, freeze)


def test_ledger_path_is_fixed_and_entries_append(tmp_path):
    assert R.LEDGER == REPO / "docs/results/data/2026-10-18-go-parity-round3/ledger.jsonl"
    p = tmp_path / "l.jsonl"
    R.append_ledger({"a": 1}, p)
    R.append_ledger({"b": 2}, p)
    assert R.read_ledger(p) == [{"a": 1}, {"b": 2}]
    assert R.read_ledger(tmp_path / "none.jsonl") == []
    assert p.read_text().count("\n") == 2


def test_shakedown_is_always_allowed():
    done = [e("1", "scored", "started"), e("1", "scored", "completed")]
    assert refusal([], "shakedown") is None and refusal(done, "shakedown") is None


def test_scored_runs_once_per_arm():
    assert refusal([], "scored") is None
    assert refusal([e("1", "scored", "started")], "scored")  # an unfinished scored run
    assert refusal([e("1", "scored", "started"), e("1", "scored", "completed")], "scored")
    crashed = [e("1", "scored", "started"), e("1", "scored", "crash_before_metric")]
    assert refusal(crashed, "scored")  # a crash is repeated as a rerun, not as a second scored run
    assert refusal([e("1", "scored", "completed", arm="S")], "scored") is None  # another arm


def test_rerun_only_after_a_pre_metric_crash_once_with_identical_bytes():
    crashed = [e("1", "scored", "started"), e("1", "scored", "crash_before_metric")]
    assert refusal([], "rerun")
    assert refusal(crashed, "rerun") is None
    assert refusal(crashed, "rerun", runner="r2")  # the runner changed
    assert refusal(crashed, "rerun", freeze="f2")  # the freeze file changed
    after = [e("1", "scored", "started"), e("1", "scored", "crash_after_metric")]
    assert refusal(after, "rerun")
    done = [e("1", "scored", "started"), e("1", "scored", "completed")]
    assert refusal(done, "rerun")
    used = [*crashed, e("2", "rerun", "started"), e("2", "rerun", "crash_before_metric")]
    assert refusal(used, "rerun")  # no third run


def test_verify_once_after_a_completed_run_with_the_same_runner():
    assert refusal([], "verify")
    done = [e("1", "scored", "started"), e("1", "scored", "completed")]
    assert refusal(done, "verify") is None
    assert refusal(done, "verify", runner="r2")
    assert refusal([*done, e("2", "verify", "started")], "verify")
    started = [e("1", "scored", "started")]
    assert refusal(started, "verify")


def test_unknown_kind_is_refused():
    assert refusal([], "scoredd")


def test_runs_of_pairs_start_and_end_by_run_id():
    es = [
        e("1", "scored", "started"),
        e("2", "shakedown", "started"),
        e("1", "scored", "completed"),
    ]
    (run,) = R.runs_of(es, "T0", "scored")
    assert run["status"] == "completed" and run["run_id"] == "1"


# ---- K7 and the dotted form -------------------------------------------------------------------------------


def test_add_import_root_is_what_the_cli_does(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "path", ["x"])
    R.add_import_root(tmp_path)
    assert sys.path[0] == str(tmp_path)
    R.add_import_root(tmp_path)
    assert sys.path.count(str(tmp_path)) == 1  # not inserted twice


def test_machinery_puts_the_root_on_the_path_before_the_harness_is_imported():
    src = inspect.getsource(R.machinery)
    assert src.index("add_import_root(root)") < src.index("harness_factory(")
    assert src.index("add_import_root(REPO)") < src.index("harness_factory(")


def test_dotted_form_conversion_on_the_real_studio_toml_and_the_copy(tmp_path):
    src = REPO / "tests/spec/gotickets"
    before = (src / "studio.toml").read_bytes()
    text = before.decode()
    new = R.dotted_studio_toml(text)
    assert text.count('"gotickets_harness:') == 3
    assert new.count('"tests.spec.gotickets_harness:') == 3
    assert '"gotickets_harness:' not in new
    dest = R.make_form_copy(src, tmp_path / "root")
    assert dest == tmp_path / "root/gotickets"
    assert "tests.spec.gotickets_harness:make_app" in (dest / "studio.toml").read_text()
    assert (dest / "go.mod").read_bytes() == (src / "go.mod").read_bytes()
    assert (src / "studio.toml").read_bytes() == before  # the fixture is never edited


def test_make_form_copy_refuses_a_studio_toml_without_the_bare_form(tmp_path):
    src = tmp_path / "src/gotickets"
    src.mkdir(parents=True)
    (src / "studio.toml").write_text('[app]\napp = "tests.spec.gotickets_harness:make_app"\n')
    with pytest.raises(ValueError):
        R.make_form_copy(src, tmp_path / "root")


def test_outputs_serialise():
    r = R.row("T0-CTL", "p", {"x": 1}, {"pass": True, "prediction_met": False})
    assert r["pass"] is True and r["evidence"]["prediction_met"] is False
    json.dumps(r)
