"""The T1-3 driver (dogfood/t13_unfamiliar_build_run.py) and its shim (dogfood/t13_shim.py), with no model call:
the guard's refusals, the ledger's cap enforcement, and dry runs end to end with the fake ``claude``
(dogfood/t13_fake_claude.py) on the mini positive control and, when the pinned source is on this machine, on the
registered Cyclops target. Prereg: docs/results/2026-10-30-t13-unfamiliar-python-build-prereg.md."""

# ruff: noqa: E501
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dogfood"))

import t13_shim as S  # noqa: E402
import t13_unfamiliar_build_run as R  # noqa: E402

# ---- the shim: caps and the ledger ----------------------------------------------------------------------------------


def _real(tmp: Path, cost: float) -> Path:
    """A stand-in for the real claude: records its argv and env keys, answers one result line costing `cost`."""
    exe = tmp / "real-claude"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(tmp / 'calls.jsonl')!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'env': sorted(os.environ)}}) + '\\n')\n"
        f"print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': False, 'total_cost_usd': {cost}, 'num_turns': 2, 'session_id': 's'}}))\n"
    )
    exe.chmod(0o755)
    return exe


def _shim(tmp: Path, real: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "sk-must-not-pass",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        **R.shim_vars(tmp / "out", real=str(real)),
    }
    return subprocess.run([sys.executable, str(R.SHIM), *args], capture_output=True, text=True, env=env, cwd=tmp)


def _calls(tmp: Path) -> list[dict]:
    p = tmp / "calls.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_shim_sizes_the_budget_and_turns_and_strips_credentials(tmp_path: Path) -> None:
    real = _real(tmp_path, 0.12)
    p = _shim(tmp_path, real, "-p", "--max-budget-usd", "9", "--max-turns", "99", "--model", "sonnet", "--", "hello")
    assert p.returncode == 0, p.stderr
    (call,) = _calls(tmp_path)
    argv = call["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == f"{R.CALL_CAP:.2f}"  # min(0.80 - 0.10 - 0, 0.70)
    assert argv[argv.index("--max-turns") + 1] == str(R.MAX_TURNS)
    assert argv.count("--max-budget-usd") == 1 and argv.count("--max-turns") == 1 and "9" not in argv and "99" not in argv
    assert argv[-2:] == ["--", "hello"]  # our flags go before the prompt separator
    assert not [k for k in call["env"] if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "T13_"))]
    (line,) = S.read_ledger(tmp_path / "out" / "ledger.jsonl")
    assert line["cost_usd"] == 0.12 and line["budget_flag_usd"] == R.CALL_CAP and line["rc"] == 0 and "hello" not in json.dumps(line)


def test_shim_enforces_the_total_cap_across_calls(tmp_path: Path) -> None:
    real = _real(tmp_path, 0.40)
    assert _shim(tmp_path, real, "-p", "--", "one").returncode == 0  # budget 0.70, spends 0.40
    assert _shim(tmp_path, real, "-p", "--", "two").returncode == 0  # budget min(0.80 - 0.10 - 0.40, 0.70) = 0.30
    third = _shim(tmp_path, real, "-p", "--", "three")  # remainder -0.10 < 0.05: refused, no call
    assert third.returncode == 3 and "cap" in third.stdout
    budgets = [c["argv"][c["argv"].index("--max-budget-usd") + 1] for c in _calls(tmp_path)]
    assert budgets == ["0.70", "0.30"]  # the third never reached the binary
    ledger = S.read_ledger(tmp_path / "out" / "ledger.jsonl")
    assert [x.get("refused") for x in ledger] == [None, None, "cap reached"]
    assert S.spent(tmp_path / "out" / "ledger.jsonl") == pytest.approx(0.80)


def test_shim_refuses_when_the_ledger_is_already_at_the_cap(tmp_path: Path) -> None:
    S.append(tmp_path / "out" / "ledger.jsonl", {"n": 1, "cost_usd": 0.66})  # 0.80 - 0.10 - 0.66 = 0.04 < 0.05
    p = _shim(tmp_path, _real(tmp_path, 0.01), "-p", "--", "x")
    assert p.returncode == 3 and _calls(tmp_path) == []


def test_shim_passes_non_model_invocations_through_untouched(tmp_path: Path) -> None:
    real = _real(tmp_path, 0.0)
    assert _shim(tmp_path, real, "--version").returncode == 0
    assert _calls(tmp_path)[0]["argv"] == ["--version"]
    assert not (tmp_path / "out" / "ledger.jsonl").exists()


# ---- the guard ------------------------------------------------------------------------------------------------------


def test_guard_refuses_without_confirm_spend(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--confirm-spend"):
        R.guard(tmp_path / "out", confirm_spend=False)


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], cwd=cwd, check=True, capture_output=True)


def test_guard_refuses_until_prereg_and_code_are_committed_and_runs_once(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    prereg, code = repo / "prereg.md", repo / "driver.py"
    prereg.write_text("prereg\n")
    code.write_text("code\n")
    _git(repo, "add", "driver.py")
    _git(repo, "commit", "-q", "-m", "code only")
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="not committed"):  # the prereg is untracked
        R.guard(out, confirm_spend=True, repo=repo, frozen=[prereg, code], prereg=prereg)
    _git(repo, "add", "prereg.md")
    _git(repo, "commit", "-q", "-m", "prereg")
    code.write_text("edited after the prereg\n")
    with pytest.raises(SystemExit, match="not committed"):
        R.guard(out, confirm_spend=True, repo=repo, frozen=[prereg, code], prereg=prereg)
    _git(repo, "commit", "-q", "-am", "code")
    info = R.guard(out, confirm_spend=True, repo=repo, frozen=[prereg, code], prereg=prereg, claude="true")
    assert set(info["files"]) == {"prereg.md", "driver.py"} and (out / "freeze.json").is_file()
    (out / "result.json").write_text("{}")
    with pytest.raises(SystemExit, match="happens once"):
        R.guard(out, confirm_spend=True, repo=repo, frozen=[prereg, code], prereg=prereg, claude="true")


def test_main_build_without_confirm_spend_makes_no_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[object] = []
    monkeypatch.setattr(R, "run", lambda *a, **k: called.append(a) or {})
    with pytest.raises(SystemExit, match="--confirm-spend"):
        R.main(["build", "--target", "mini", "--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "s")])
    assert called == []


# ---- dry runs end to end (fake claude, through the shim and the ledger) ---------------------------------------------


def _stages(res: dict) -> dict[str, str]:
    return {s["id"]: s["status"] for s in res["stages"]}


def test_dry_run_mini_reference_agent_is_built(tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = R.run("mini", out, tmp_path / "scratch", dry_run=True, agent="reference")
    assert _stages(res) == {"S1": "pass", "S2": "pass", "S3": "pass", "S4": "pass", "S5": "pass"}, res
    assert res["built"] is True and res["total_usd"] == 0.0 and res["dry_run"] is True
    ledger = S.read_ledger(out / "ledger.jsonl")
    assert len(ledger) == 1 and ledger[0]["cost_usd"] == 0.0 and ledger[0]["budget_flag_usd"] == R.CALL_CAP
    (call,) = [json.loads(x) for x in (out / "fake-claude.jsonl").read_text().splitlines()]
    assert "--max-budget-usd" in call["argv"] and call["argv"][call["argv"].index("--max-turns") + 1] == str(R.MAX_TURNS)
    assert not [k for k in call["env"] if k.startswith("ANTHROPIC_")]
    assert res["stages"][2]["fake_calls"] == {"n": 1, "max_budget_usd": ["0.70"], "max_turns": [str(R.MAX_TURNS)], "credential_env_names": []}
    s4, s5 = res["stages"][3], res["stages"][4]
    assert s4["verdict"]["passed"] and s4["verdict"]["tree_cleaned"] and s4["verdict"]["protected_diff"]["clean"]
    assert s5["counts"]["passed"] == 6 and s5["counts"]["failed"] == 0
    saved = json.loads((out / "result.json").read_text())
    assert saved["built"] is True and saved["stages"] == res["stages"]


def test_dry_run_mini_noop_agent_is_not_built_and_stops_at_the_held_out_tests(tmp_path: Path) -> None:
    res = R.run("mini", tmp_path / "out", tmp_path / "scratch", dry_run=True, agent="noop")
    st = _stages(res)
    assert res["built"] is False and res["first_failure"] == "S5", res
    assert st["S5"] == "fail" and res["stages"][4]["counts"]["failed"] >= 1
    calib = res["stages"][1]["calibration"]
    assert calib["reference"]["held_out"]["passed"] and not calib["scaffold"]["held_out"]["passed"]


def test_dry_run_mini_protected_edit_fails_the_clean_gate(tmp_path: Path) -> None:
    res = R.run("mini", tmp_path / "out", tmp_path / "scratch", dry_run=True, agent="tamper")
    assert res["built"] is False and res["first_failure"] in ("S3", "S4"), res
    assert all(s["status"] == "not run" for s in res["stages"] if s["id"] > res["first_failure"])


@pytest.mark.skipif(not R.TARGETS["cyclops"].available()[0], reason="the pinned Cyclops source (and its venv) is not on this machine")
def test_dry_run_cyclops_stops_at_the_import(tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = R.run("cyclops", out, tmp_path / "scratch", dry_run=True, agent="reference")
    assert res["first_failure"] == "S1" and res["built"] is False
    assert "S020" in res["stages"][0]["why"]
    assert [s["status"] for s in res["stages"][1:]] == ["not run"] * 4
    assert res["total_usd"] == 0.0 and not (out / "ledger.jsonl").exists()
