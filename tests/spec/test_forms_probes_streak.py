# ruff: noqa: E501
"""Typed rule conditions, the Streak arm: unit tests of ``dogfood/forms_probes_streak.py`` (no Docker, no Go build,
no model; the registered set is read from the committed ``streak-set.tgz``).

What is tested: the hash discipline cannot be turned off, the once-only ledger, the set's verification and reading,
the scoring definitions of PS0-PSx on synthetic and mini-shop data, the generated wrapper, and the dry run.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lattice.spec import forms as F
from lattice.spec.rules import Trace
from tests.spec import minishop as M

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "dogfood" / "forms_probes_streak.py"
FIX = Path(__file__).parent / "fixtures" / "forms" / "minishop"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("forms_probes_streak", RUNNER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["forms_probes_streak"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def R() -> Any:
    return _load()


@pytest.fixture(scope="module")
def setdir(R: Any, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return R.extract_set(REPO / R.SET_TGZ_REL, tmp_path_factory.mktemp("set"))


@pytest.fixture(scope="module")
def rec(R: Any, setdir: Path) -> dict[str, Any]:
    return R.read_set(setdir)


def freeze_file(R: Any, tmp: Path, **override: str) -> Path:
    lines = []
    for rel in R.REQUIRED_FROZEN:
        digest = override.get(rel, R.sha256_file(REPO / rel))
        lines.append(f"{digest}  {rel}")
    p = tmp / "freeze.sha256"
    p.write_text("\n".join(lines) + "\n")
    return p


# ---- hash discipline ----------------------------------------------------------------------------------------


def test_freeze_accepts_the_tree_as_it_is(R: Any, tmp_path: Path) -> None:
    fz = R.freeze_check(freeze_file(R, tmp_path))
    assert fz["ok"], fz["problems"]
    assert fz["runner_sha256"] == R.sha256_file(RUNNER)


def test_freeze_refuses_a_runner_that_is_not_the_frozen_one(R: Any, tmp_path: Path) -> None:
    fz = R.freeze_check(freeze_file(R, tmp_path, **{R.SELF_REL: "0" * 64}))
    assert not fz["ok"] and any(R.SELF_REL in p for p in fz["problems"])


def test_freeze_refuses_a_changed_library_file(R: Any, tmp_path: Path) -> None:
    fz = R.freeze_check(freeze_file(R, tmp_path, **{"lattice/spec/forms.py": "1" * 64}))
    assert not fz["ok"] and any("lattice/spec/forms.py" in p for p in fz["problems"])


def test_freeze_must_list_the_runner_and_the_library(R: Any, tmp_path: Path) -> None:
    p = tmp_path / "f.sha256"
    p.write_text(f"{R.sha256_file(REPO / 'lattice/spec/forms.py')}  lattice/spec/forms.py\n")
    fz = R.freeze_check(p)
    assert not fz["ok"]
    assert any(R.SELF_REL in x and "not listed" in x for x in fz["problems"])
    assert not R.freeze_check(None)["ok"]
    assert not R.freeze_check(tmp_path / "missing")["ok"]


def test_there_is_no_option_that_turns_the_hash_check_off(R: Any) -> None:
    opts = {o for a in R.build_parser()._actions for o in a.option_strings}
    assert not [
        o
        for o in opts
        if any(w in o for w in ("skip", "no-", "disable", "force", "unsafe", "ignore"))
    ]
    # the only stage is the Streak stage, and every scoring entry point calls the check before anything else
    assert "--freeze" in opts and "--no-freeze" not in opts


@pytest.mark.parametrize("extra", [[], ["--rescore", "/nonexistent"]])
def test_main_refuses_to_score_without_a_valid_freeze(
    R: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    base = [
        "--stage",
        "streak",
        "--scratch",
        str(tmp_path / "s"),
        "--out",
        str(tmp_path / "o"),
        "--driver-python",
        "x",
        *extra,
    ]
    assert R.main(base) == 2
    bad = freeze_file(R, tmp_path, **{R.SELF_REL: "0" * 64})
    assert R.main([*base, "--freeze", str(bad)]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "o").exists() or not any((tmp_path / "o").iterdir())


# ---- the ledger ----------------------------------------------------------------------------------------------


def test_ledger_is_once_only_with_one_rerun_after_a_pre_metric_crash(
    R: Any, tmp_path: Path
) -> None:
    led = tmp_path / "x" / "ledger.json"
    assert R.ledger_check(led, "strict") is None
    assert R.ledger_open(led, "strict", "a" * 64, tmp_path) == 1
    assert "once only" in R.ledger_check(led, "strict")  # started: refused
    assert R.ledger_check(led, "drop_invalid") is None  # another variant is its own measurement
    R.ledger_status(led, "strict", "crashed_before_metric")
    assert R.ledger_check(led, "strict") is None
    assert R.ledger_open(led, "strict", "a" * 64, tmp_path) == 2
    R.ledger_status(led, "strict", "crashed_before_metric")
    assert "one re-run" in R.ledger_check(led, "strict")
    with pytest.raises(R.RunnerError):
        R.ledger_open(led, "strict", "a" * 64, tmp_path)


@pytest.mark.parametrize(
    "status", ["scored", "metric_started", "crashed_after_metric_start", "started"]
)
def test_ledger_refuses_after_any_status_but_a_pre_metric_crash(
    R: Any, tmp_path: Path, status: str
) -> None:
    led = tmp_path / "ledger.json"
    R.ledger_open(led, "strict", "b" * 64, tmp_path)
    R.ledger_status(led, "strict", status)
    assert R.ledger_check(led, "strict")


def test_the_ledger_is_global_not_under_out(R: Any) -> None:
    assert R.LEDGER.is_absolute() and "ledger" in R.LEDGER.name
    assert not any(o == "--ledger" for a in R.build_parser()._actions for o in a.option_strings)


# ---- the set --------------------------------------------------------------------------------------------------


def test_the_registered_set_verifies_and_reads(R: Any, setdir: Path, rec: dict[str, Any]) -> None:
    vs = R.verify_set(setdir)
    assert vs["ok"], vs["problems"]
    assert vs["files"] == 58
    assert len(rec["forms"]) == 43 and len({f["rule"] for f in rec["forms"]}) == 23
    assert len(rec["rules"]) == 23 and len(rec["apps"]) == 30 and rec["apps"][0] == "ok"
    assert len(rec["mutants"]) == 29 and len(rec["pairs"]) == 24 and len(rec["invalid"]) == 27
    assert sum(len(rec["truth"][r]) for r in rec["rules"]) == 690
    assert len(rec["ne"]) == 123 and len(rec["not_expressible_map"]) == 123
    assert len(rec["classification"]["rules"]) == 146
    assert set(rec["candidates"]) == {"limit50", "limit60", "stated_twice"}
    assert set(rec["source_sha"]) >= {"REQUIREMENTS.md", "spec.py", "spec.trace.json"}


def test_the_set_is_refused_when_a_file_is_edited_or_added(
    R: Any, setdir: Path, tmp_path: Path
) -> None:
    copy_ = tmp_path / "streak"
    shutil.copytree(setdir, copy_)
    (copy_ / "truth.json").write_text(
        (copy_ / "truth.json").read_text().replace('"violates": false', '"violates": true', 1)
    )
    vs = R.verify_set(copy_)
    assert not vs["ok"] and any("truth.json" in p for p in vs["problems"])
    shutil.rmtree(copy_)
    shutil.copytree(setdir, copy_)
    (copy_ / "extra.json").write_text("{}")
    assert any("extra.json" in p for p in R.verify_set(copy_)["problems"])
    shutil.rmtree(copy_)
    shutil.copytree(setdir, copy_)
    (copy_ / "MANIFEST.sha256").write_text((copy_ / "MANIFEST.sha256").read_text() + "\n")
    assert any("MANIFEST.sha256" in p for p in R.verify_set(copy_)["problems"])


def test_a_different_tarball_is_refused(R: Any, tmp_path: Path) -> None:
    bad = tmp_path / "x.tgz"
    bad.write_bytes(b"not the set")
    with pytest.raises(R.RunnerError):
        R.extract_set(bad, tmp_path / "d")


def test_a_patch_edited_after_the_set_was_frozen_is_refused(
    R: Any, setdir: Path, tmp_path: Path
) -> None:
    copy_ = tmp_path / "streak"
    shutil.copytree(setdir, copy_)
    p = copy_ / "mutants" / "m_habit_limit_gt.diff"
    p.write_text(p.read_text() + "\n")
    with pytest.raises(R.RunnerError, match="patch sha256"):
        R.read_set(copy_)


# ---- the generated wrapper, the patch, the build inputs -----------------------------------------------------------


def test_make_tree_writes_the_wrapper_and_the_forms_beside_the_trace(
    R: Any, tmp_path: Path
) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "a.txt").write_text("one\ntwo\nthree\n")
    forms = tmp_path / "forms.json"
    forms.write_text('{"forms": []}')
    patch = tmp_path / "p.diff"
    patch.write_text("--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n")
    out = R.make_tree(base, tmp_path / "t", forms, patch, "drop_invalid")
    assert out["ok"]
    assert (tmp_path / "t" / "a.txt").read_text() == "one\nTWO\nthree\n"
    assert (base / "a.txt").read_text() == "one\ntwo\nthree\n"  # the pristine tree is not touched
    assert (tmp_path / "t" / "REQUIREMENTS.forms.json").read_text() == '{"forms": []}'
    wrapper = (tmp_path / "t" / "spec_forms.py").read_text()
    assert (
        str(RUNNER) in wrapper and "'drop_invalid'" in wrapper and "effective_for_tree" in wrapper
    )
    bad = tmp_path / "bad.diff"
    bad.write_text("--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n nope\n-two\n+TWO\n three\n")
    assert not R.make_tree(base, tmp_path / "t2", forms, bad, "strict")["ok"]


def test_the_checker_command_runs_layers_cd_on_mysql(R: Any) -> None:
    argv = R.cd_argv("PY", "spec_forms:SPEC")
    assert (
        argv[:5] == ["PY", "-m", "lattice.spec", "check", "--spec"] and argv[5] == "spec_forms:SPEC"
    )
    s = " ".join(argv)
    assert (
        "--layers CD" in s
        and "--database mysql" in s
        and "--go-src ." in s
        and s.endswith("--json")
    )
    assert "--layers ABCDX" not in s


def test_build_g_strict_and_the_post_hoc_drop(R: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    trace = Trace.load(FIX / "spec.trace.json")
    paths = F.forms_paths(FIX / "spec.trace.json")
    eff, gen, meta = R.build_g(M.SPEC, trace, paths, "strict")
    assert meta["validate_problems"] == [] and meta["dropped_invariants"] == {}
    assert meta["forms_usable"] and len(eff.scenarios) == len(M.SPEC.scenarios) + len(gen.scenarios)
    n_inv = len(gen.invariants)
    assert n_inv >= 1
    import lattice.spec.validate  # noqa: F401  (the package also exports a function of this name)

    V = sys.modules["lattice.spec.validate"]
    real = V.validate
    victim = gen.invariants[0].name

    def fake(spec: Any) -> list[Any]:
        extra = [
            SimpleNamespace(code="S008", message=f"invariant {i.name!r} refused")
            for i in spec.invariants
            if i.name == victim
        ]
        return [*real(spec), *extra]

    monkeypatch.setattr(V, "validate", fake)
    eff2, gen2, meta2 = R.build_g(M.SPEC, trace, paths, "strict")
    assert (
        meta2["validate_problems"] and len(gen2.invariants) == n_inv
    )  # strict keeps it and says so
    eff3, gen3, meta3 = R.build_g(M.SPEC, trace, paths, "drop_invalid")
    assert list(meta3["dropped_invariants"]) == [victim] and meta3["validate_problems"] == []
    assert len(gen3.invariants) == n_inv - 1 and len(gen3.scenarios) == len(gen2.scenarios)
    assert eff3 is not eff2


# ---- attribution and the cells ---------------------------------------------------------------------------------


CTX = {
    "sent2name": {
        "one account per email": "one_account_per_email",
        "at most 1 User rows per email (form F3)": "form:F3:invariant",
    },
    "form_rule": {"F3": "R10", "F4": "R14"},
    "elem_rules": {
        "scenario:dup_email": ["R10"],
        "invariant:one_account_per_email": ["R10"],
        "scenario:bad_login": ["R14"],
    },
}


def fnd(
    layer: str, rule: str, case: str = "x / step 1", violations: list[str] | None = None
) -> dict[str, Any]:
    return {
        "layer": layer,
        "rule": rule,
        "case": case,
        "verdict": "silent",
        "violations": violations or [],
    }


def test_attribution_follows_the_rule_text_and_the_linked_elements(R: Any) -> None:
    at = R.attribute(
        [
            fnd("D", "scenario: form:F3:at_limit", "form:F3:at_limit / step 1"),
            fnd("C", "at most 1 User rows per email (form F3)"),
            fnd("C", "one account per email", "form:F4:below / step 1", ["one_account_per_email"]),
            fnd("D", "scenario: bad_login"),
            fnd("D", "scenario: unlinked"),
            fnd("D", "scenario: form:F77:x"),
        ],
        CTX,
    )
    assert at["g"] == {"R10": ["C", "D"]}
    assert at["h"] == {"R10": ["C"], "R14": ["D"]}
    assert at["h_elements_seen_in_generated_cases"] == ["invariant:one_account_per_email"]
    assert at["unattributed"] == ["scenario: form:F77:x"]


def mini_rec() -> dict[str, Any]:
    apps = ["ok", "m1", "m2", "m3"]

    def cell(v: bool, **flags: bool) -> dict[str, Any]:
        return {"violates": v, **flags}

    truth = {
        "R10": {
            "ok": cell(False),
            "m1": cell(True),
            "m2": cell(False),
            "m3": cell(True, form_gap="outside the form"),
        },
        "R14": {"ok": cell(False), "m1": cell(False), "m2": cell(False), "m3": cell(False)},
        "R37": {
            "ok": cell(False, k6=True),
            "m1": cell(False, k6=True),
            "m2": cell(True, k6=True),
            "m3": cell(False, k6=True),
        },
    }
    hv = {r: {a: {"verdict": "pass"} for a in apps} for r in truth}
    return {
        "rules": ["R10", "R14", "R37"],
        "apps": apps,
        "truth": truth,
        "h_verdicts": {"cells": hv},
        "handwritten": {r: {"elements": []} for r in truth},
    }


def run_of(*findings: dict[str, Any]) -> dict[str, Any]:
    return {
        "build": {"ok": True},
        "run": {"ok": True, "rc": 1 if findings else 0, "wall_s": 40.0, "findings": list(findings)},
    }


def raw_of(apps: dict[str, Any], **kw: Any) -> dict[str, Any]:
    ctx = copy.deepcopy(CTX)
    ctx["form_rule"]["F37"] = "R37"
    ctx["elem_rules"]["scenario:dev_reg"] = ["R37"]
    return {
        "variant": "strict",
        "g_runnable": True,
        "g_meta": {"notes": [], "validate_problems": []},
        "ctx": ctx,
        "apps": apps,
        **kw,
    }


def test_ps1_fails_on_a_cell_where_g_differs_from_h_and_h_is_right(R: Any) -> None:
    rec = mini_rec()
    raw = raw_of(
        {
            "ok": run_of(
                fnd("D", "scenario: form:F3:at_limit")
            ),  # G fails R10 on ok, H passes, T passes
            "m1": run_of(fnd("D", "scenario: form:F3:at_limit"), fnd("D", "scenario: dup_email")),
            "m2": run_of(),
            "m3": run_of(),
        }
    )
    rows = {r["probe"]: r for r in R.score_cells(rec, raw)}
    assert rows["PS1"]["status"] == "FAIL"
    assert [(c["rule"], c["app"]) for c in rows["PS1"]["bad_gne_h_and_h_eq_t"]] == [("R10", "ok")]
    assert rows["PS1o"]["status"] == "FAIL" and rows["PS1o"]["g_fail_on_ok"] == ["R10"]
    assert rows["PS1t"]["status"] == "FAIL"


def test_ps1t_passes_with_only_k6_and_form_gap_misses(R: Any) -> None:
    rec = mini_rec()
    raw = raw_of(
        {
            "ok": run_of(),
            "m1": run_of(fnd("D", "scenario: form:F3:at_limit"), fnd("D", "scenario: dup_email")),
            "m2": run_of(),  # R37 x m2: violated, K6, nobody sees it
            "m3": run_of(),  # R10 x m3: violated, form_gap, nobody sees it
        }
    )
    rows = {r["probe"]: r for r in R.score_cells(rec, raw)}
    assert rows["PS1t"]["status"] == "PASS", rows["PS1t"]["bad_outside_exempt"]
    assert [(c["rule"], c["app"]) for c in rows["PS1t"]["k6_misses"]] == [("R37", "m2")]
    assert [(c["rule"], c["app"]) for c in rows["PS1t"]["form_gap_cells"]] == [("R10", "m3")]
    assert rows["PS1"]["status"] == "PASS" and rows["PS1"]["g_eq_h"] == 12
    assert (
        rows["PS1k"]["status"] == "PASS"
        and rows["PS1k"]["g_kills"] == ["m1"]
        and rows["PS1k"]["h_kills"] == ["m1"]
    )
    assert rows["PS1layer"]["layer_d_agreement"] == 1.0


def test_pg1k_fails_when_h_kills_something_g_does_not(R: Any) -> None:
    rec = mini_rec()
    raw = raw_of(
        {
            "ok": run_of(),
            "m1": run_of(fnd("D", "scenario: dup_email")),
            "m2": run_of(),
            "m3": run_of(),
        }
    )
    rows = {r["probe"]: r for r in R.score_cells(rec, raw)}
    assert rows["PS1k"]["status"] == "FAIL" and rows["PS1k"]["h_only"] == ["m1"]


def test_cells_are_not_run_when_an_app_run_is_missing_or_g_cannot_run(R: Any) -> None:
    rec = mini_rec()
    raw = raw_of(
        {"ok": run_of(), "m1": run_of(), "m2": run_of(), "m3": {"build": {}, "run": {"ok": False}}}
    )
    rows = {r["probe"]: r for r in R.score_cells(rec, raw)}
    assert rows["PS1"]["status"] == "NOT RUN" and rows["PS1"]["apps_missing"] == ["m3"]
    dead = raw_of(
        {},
        g_runnable=False,
        g_meta={"notes": [], "validate_problems": ["S008: invariant 'form:F29:invariant' refused"]},
    )
    rows = R.score_cells(rec, dead)
    assert {r["probe"] for r in rows} >= {"PS1", "PS1t"} and all(
        r["status"] == "NOT RUN" and "S008" in r["reason"] for r in rows
    )


def test_k7_cells_are_the_db_unchecked_notes_with_a_violated_truth_and_a_silent_g(R: Any) -> None:
    rec = mini_rec()
    raw = raw_of(
        {
            "ok": run_of(),
            "m1": run_of(fnd("D", "scenario: dup_email")),
            "m2": run_of(),
            "m3": run_of(),
        },
        g_meta={"notes": ["F3: db_unchecked:other_x"], "validate_problems": []},
    )
    rows = {r["probe"]: r for r in R.score_cells(rec, raw)}
    assert [(c["rule"], c["app"]) for c in rows["PS1t"]["k7_cells"]] == [
        ("R10", "m1"),
        ("R10", "m3"),
    ]
    assert rows["PS1t"]["status"] == "PASS"


# ---- PS3 and PSx ----------------------------------------------------------------------------------------------------


def good_ps3(n: int = 10, wall: float = 40.0) -> dict[str, Any]:
    runs = [
        {
            "i": i + 1,
            "ok": True,
            "wall_s": wall,
            "total_s": wall - 1,
            "verdict": "pass",
            "findings": [],
        }
        for i in range(n)
    ]
    for r in runs[1:]:
        r["diff_vs_first"] = {"differences": 0, "await_timing": 0, "other": [], "await_only": True}
    return {
        "g_runnable": True, "ps3_runs": runs, "ps3_generate": {"runs": 10, "distinct": ["a" * 64], "leaks": []},
        "baseline": [{"ok": True, "wall_s": 29.0} for _ in range(3)], "apps": {"ok": {"run": {"ok": True, "wall_s": wall}}, "m1": {"run": {"ok": True, "wall_s": 90.0}}},
    }  # fmt: skip


def test_ps3_and_psx_pass_on_ten_identical_runs_within_the_gate(R: Any) -> None:
    rows = {r["probe"]: r for r in R.score_ps3_psx(good_ps3())}
    assert rows["PS3"]["status"] == "PASS" and rows["PSx"]["status"] == "PASS"
    assert rows["PSx"]["median_no_g_s"] == 29.0 and rows["PSx"]["apps_over_gate"] == []


def test_ps3_excludes_await_timing_only_differences_and_fails_on_others(R: Any) -> None:
    raw = good_ps3()
    raw["ps3_runs"][3]["diff_vs_first"] = {
        "differences": 2,
        "await_timing": 2,
        "other": [],
        "await_only": True,
    }
    assert {r["probe"]: r for r in R.score_ps3_psx(raw)}["PS3"]["status"] == "PASS"
    raw["ps3_runs"][4]["diff_vs_first"] = {
        "differences": 1,
        "await_timing": 0,
        "other": [["/findings", "a", "b"]],
        "await_only": False,
    }
    assert {r["probe"]: r for r in R.score_ps3_psx(raw)}["PS3"]["status"] == "FAIL"
    raw = good_ps3()
    raw["ps3_generate"]["distinct"] = ["a" * 64, "b" * 64]
    assert {r["probe"]: r for r in R.score_ps3_psx(raw)}["PS3"]["status"] == "FAIL"


def test_psx_fails_over_the_gate_and_is_not_run_when_a_run_is_missing(R: Any) -> None:
    rows = {r["probe"]: r for r in R.score_ps3_psx(good_ps3(wall=181.0))}
    assert rows["PSx"]["status"] == "FAIL" and rows["PSx"]["max_s"] == 181.0
    raw = good_ps3()
    raw["ps3_runs"][2] = {"i": 3, "ok": False, "wall_s": 0}
    rows = {r["probe"]: r for r in R.score_ps3_psx(raw)}
    assert rows["PS3"]["status"] == "NOT RUN" and rows["PSx"]["status"] == "NOT RUN"
    dead = {
        r["probe"]: r
        for r in R.score_ps3_psx({"g_runnable": False, "g_meta": {"validate_problems": ["S008 x"]}})
    }
    assert (
        dead["PS3"]["status"] == dead["PSx"]["status"] == "NOT RUN"
        and "S008" in dead["PS3"]["reason"]
    )


def test_canon_report_drops_timings_and_masks_the_container_and_seconds(R: Any) -> None:
    a = {
        "verdict": "pass",
        "timings": {"total_s": 1},
        "notes": [
            "database: mysql (container lattice-mysql-aaaa); seed built once (seed 0.35s), 157 clone(s), 26 ms each"
        ],
        "findings": [],
    }
    b = {
        "verdict": "pass",
        "timings": {"total_s": 2},
        "notes": [
            "database: mysql (container lattice-mysql-bbbb); seed built once (seed 0.41s), 157 clone(s), 31 ms each"
        ],
        "findings": [],
    }
    assert R.canon_report(a) == R.canon_report(b)
    assert "timings" not in R.canon_report(a)
    assert a["timings"] == {"total_s": 1}  # not modified in place


def test_json_diff_and_the_await_classification(R: Any) -> None:
    x = {
        "cases": {"s / step 1": {"reason": "ok"}},
        "findings": [{"rule": "r", "details": {"await_s": 1}}],
    }
    y = {
        "cases": {"s / step 1": {"reason": "ok"}},
        "findings": [{"rule": "r", "details": {"await_s": 2}}],
    }
    d = R.classify_diff(R.json_diff(x, y))
    assert d["differences"] == 1 and d["await_only"] and d["await_timing"] == 1
    z = {
        "cases": {"s / step 1": {"reason": "changed"}},
        "findings": [{"rule": "r", "details": {"await_s": 1}}],
    }
    d2 = R.classify_diff(R.json_diff(x, z))
    assert not d2["await_only"] and d2["other"][0][0].endswith("reason")
    assert R.json_diff(x, copy.deepcopy(x)) == []
    w = R.classify_diff(
        R.json_diff({"reason": "waited 3 s for the push"}, {"reason": "waited 4 s for the push"})
    )
    assert w["await_only"]


# ---- PS0, PS2, PG5, PNX (library, on the mini-shop fixture) ---------------------------------------------------------


@pytest.fixture(scope="module")
def mini_trace() -> Any:
    return Trace.load(FIX / "spec.trace.json"), F.forms_paths(FIX / "spec.trace.json")


def test_ps0_counts_formed_and_not_expressible_against_the_bands_and_the_authors_classification(
    R: Any, mini_trace: Any
) -> None:
    trace, paths = mini_trace
    fs = F.load_set(trace, paths)
    cov = F.coverage(trace, fs)
    author = [
        {
            "rule": x["rule"],
            "status": x["state"],
            "coverage": "partial" if x["state"] == "formed" else None,
        }
        for x in cov["rules"]
    ]
    author[0]["status"] = (
        "formed" if author[0]["status"] != "formed" else "not_expressible:other"
    )  # one disagreement
    r = R.score_coverage({"classification": {"rules": author}}, trace, paths)
    assert r["probe"] == "PS0" and r["status"] == "REPORTED"
    assert r["scope"] == cov["scope"] and r["formed"] == cov["counts"]["formed"]
    assert r["formed_in_band"] is False and r["formed_band"] == [22, 45]
    assert r["library_vs_author_classification_mismatches"] == [author[0]["rule"]]
    assert r["strict_reading_formed"] == 0
    assert r["formed_with_partial_coverage"] == sum(
        1 for a in author if a["status"] == "formed" and a["coverage"] == "partial"
    )


def bound(fid: str, op: str, value: int) -> dict[str, Any]:
    form = {"field": {"column": "Product.stock"}, "cond": {"node": "Cmp", "op": op, "value": value}}
    return {
        "id": fid,
        "rule": "R4",
        "anchor": "0" * 64,
        "kind": "bound",
        "form": form,
        "status": "approved",
        "author": "hand",
        "approved_by": "t",
        "approved_sha": F.form_sha("bound", form),
    }


def test_ps2_scores_precision_and_recall_the_way_pt2f_does(R: Any) -> None:
    pairs = {
        "pairs": [
            {
                "id": "P1",
                "class": "a",
                "expect": "contradictory",
                "forms": [bound("F1", "ge", 5), bound("F2", "le", 3)],
            },
            {
                "id": "P2",
                "class": "a",
                "expect": "compatible",
                "forms": [bound("F1", "ge", 1), bound("F2", "le", 1)],
            },
            {
                "id": "P9",
                "class": "a",
                "expect": "contradictory",
                "forms": [bound("F3", "ge", 5), bound("F4", "le", 3)],
            },
        ]
    }
    got = R.score_pairs(pairs, M.SPEC)
    assert (
        got["precision"] == 1.0
        and got["recall_abcd"] == "2/2"
        and got["recall_abcd_all"]
        and got["false_pos"] == []
        and got["errors"] == []
    )
    assert got["p9"]["found"] and got["p9"]["items"][0]["class"] == "a"
    # a missed contradiction and a false claim are both visible
    pairs["pairs"][0] = {
        "id": "P1",
        "class": "a",
        "expect": "compatible",
        "forms": [bound("F1", "ge", 5), bound("F2", "le", 3)],
    }
    bad = R.score_pairs(pairs, M.SPEC)
    assert bad["precision"] < 1.0 and bad["false_pos"][0]["id"] == "P1"


def test_ps2_needs_p9_and_the_candidates_and_the_impact_item(R: Any) -> None:
    pairs = {"precision": 1.0, "recall_abcd_all": True, "errors": [], "p9": {"found": True}}
    item = {"class": "b", "forms": ["F29", "F44"], "witness": {"counted_rows": 50}, "tier": 2}
    cand = {
        "stated_twice": {"items": [item]},
        "limit50": {"items": []},
        "limit60": {"items": []},
        "impact": {"form_items": [item]},
    }
    assert R.score_ps2(pairs, cand)["status"] == "PASS"
    assert R.score_ps2({**pairs, "p9": {"found": False}}, cand)["status"] == "FAIL"
    assert R.score_ps2(pairs, {**cand, "limit60": {"items": [item]}})["status"] == "FAIL"
    assert R.score_ps2(pairs, {**cand, "impact": {"form_items": []}})["status"] == "FAIL"
    assert R.score_ps2({**pairs, "precision": 0.9}, cand)["status"] == "FAIL"
    assert R.score_ps2(pairs, {**cand, "stated_twice": {"items": None}})["status"] == "FAIL"


def test_invalid_forms_and_not_expressible_attempts(R: Any, mini_trace: Any) -> None:
    trace, _paths = mini_trace
    fs = F.load_set(trace, F.forms_paths(FIX / "spec.trace.json"))
    assert fs is not None
    good = [e.to_dict() for e in fs.approved]
    unknown_key = bound("F50", "ge", 0)
    unknown_key["form"]["between"] = [0, 5]
    now_value = {
        "kind": "bound",
        "form": {
            "field": {"column": "Product.stock"},
            "cond": {"node": "Cmp", "op": "ge", "value": {"node": "now"}},
        },
    }
    rec = {
        "forms": good,
        "invalid": [
            {"id": "I1", "code": "F001", "forms": [unknown_key]},
            {"id": "I2", "code": "F001", "forms": [bound("F51", "ge", 0)]},
        ],
        "ne": [
            {"id": "N1", "attempt": now_value},
            {"id": "N2", "attempt": None},
            {"id": "N3", "attempt": {"kind": "bound", "form": bound("F52", "ge", 1)["form"]}},
        ],
    }
    rows = {r["probe"]: r for r in R.check_set_forms(rec, M.SPEC, trace)}
    assert rows["PS-PG5"]["accepted_invalid"] == ["I2"] and rows["PS-PG5"]["status"] == "FAIL"
    assert rows["PS-PG5"]["exact_code"] == 1
    assert (
        rows["PS-PNX"]["with_attempt"] == 2
        and rows["PS-PNX"]["accepted"] == ["N3"]
        and rows["PS-PNX"]["status"] == "FAIL"
    )


# ---- the dry run ------------------------------------------------------------------------------------------------------


def test_the_plan_lists_every_ps_row_and_its_inputs(R: Any, rec: dict[str, Any]) -> None:
    rows = R.plan(rec, SimpleNamespace(driver_python="PY"))
    ids = [r["probe"] for r in rows]
    assert ids[:6] == ["PS0", "PS1", "PS1t", "PS2", "PS3", "PSx"]
    by = {r["probe"]: r for r in rows}
    assert by["PS1"]["inputs"]["cells"].endswith("= 690") and len(by["PS1"]["inputs"]["apps"]) == 30
    assert len(by["PS1"]["inputs"]["mutant_patches"]) == 29
    assert (
        by["PS1t"]["inputs"]["k6_cells"] == 90 and len(by["PS1t"]["inputs"]["form_gap_cells"]) == 3
    )
    assert by["PS2"]["inputs"]["pairs"] == 24 and by["PS2"]["inputs"]["p9"] == ["F29", "F111"]
    assert (
        by["PS3"]["inputs"]["runs"] == 10
        and "spec_forms:SPEC" in by["PS3"]["command"]
        and "--layers CD" in by["PS3"]["command"]
    )
    assert "spec:SPEC" in by["PSx"]["command_no_g"]
    assert by["PS0"]["inputs"]["scope"] == 146 and by["PS0"]["inputs"]["forms"] == 43


def test_dry_run_writes_no_ledger_and_needs_no_docker_or_freeze(
    R: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    led = tmp_path / "ledger.json"
    monkeypatch.setattr(R, "LEDGER", led)
    rc = R.main(
        [
            "--stage",
            "streak",
            "--dry-run",
            "--scratch",
            str(tmp_path / "s"),
            "--streak-repo",
            str(tmp_path / "no-such-repo"),
        ]
    )
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert [r["probe"] for r in doc["rows"]][:6] == ["PS0", "PS1", "PS1t", "PS2", "PS3", "PSx"]
    assert doc["set"]["ok"] and doc["ledger_says"] == "a run may start" and not led.exists()
    assert doc["docker_runs"]["total"] == 42 and doc["runner_sha256"] == R.sha256_file(RUNNER)
    assert "streak_archive_error" in doc["environment"]


def test_a_scored_run_refuses_a_used_ledger_before_touching_anything(
    R: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    led = tmp_path / "ledger.json"
    monkeypatch.setattr(R, "LEDGER", led)
    R.ledger_open(led, "strict", "c" * 64, tmp_path)
    R.ledger_status(led, "strict", "scored")
    fz = freeze_file(R, tmp_path)
    rc = R.main(
        [
            "--stage",
            "streak",
            "--freeze",
            str(fz),
            "--driver-python",
            "x",
            "--scratch",
            str(tmp_path / "s"),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert rc == 2 and "once only" in capsys.readouterr().out
    assert not (tmp_path / "s" / "set").exists()  # nothing was extracted, nothing run


def test_a_scored_run_refuses_an_edited_set_before_any_ledger_entry(
    R: Any,
    setdir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    led = tmp_path / "ledger.json"
    monkeypatch.setattr(R, "LEDGER", led)
    copy_ = tmp_path / "streak"
    shutil.copytree(setdir, copy_)
    (copy_ / "pairs.json").write_text(
        (copy_ / "pairs.json").read_text().replace("contradictory", "compatible", 1)
    )
    fz = freeze_file(R, tmp_path)
    rc = R.main(
        [
            "--stage",
            "streak",
            "--freeze",
            str(fz),
            "--driver-python",
            "x",
            "--shapes",
            str(copy_),
            "--scratch",
            str(tmp_path / "s"),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert rc == 2 and "does not verify" in capsys.readouterr().out and not led.exists()
