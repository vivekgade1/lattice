"""The plugin's Stop hook (lattice-claude-plugin/hooks/stop-check.sh), run for real in temp git
repos with a stub check.sh: strict by default, a bounded retry counter, HEAD tracking, tamper."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "lattice-claude-plugin" / "hooks" / "stop-check.sh"
SID = "sess-1"


def git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def stub(p: Path, rc: int, note: str = "") -> None:
    """A check.sh that appends to ran.log and exits `rc`."""
    (p / "check.sh").write_text(
        f'#!/bin/sh\necho ran >> "{p}/ran.log"\necho "finding {note}"\nexit {rc}\n'
    )
    (p / "check.sh").chmod(0o755)


def runs(p: Path) -> int:
    f = p / "ran.log"
    return len(f.read_text().split()) if f.exists() else 0


def fire(
    p: Path, active: bool = False, sid: str = SID, **env: str
) -> subprocess.CompletedProcess[str]:
    event = {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": active}
    e = {k: v for k, v in os.environ.items() if not k.startswith("LATTICE_")}
    e.update({"CLAUDE_PROJECT_DIR": str(p), **env})
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(event),
        cwd=p,
        env=e,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A committed Lattice project (spec.py + spec.trace.json) with a passing check.sh; ran.log
    is ignored so the stub's own log never dirties the tree."""
    p = tmp_path / "proj"
    p.mkdir()
    git(p, "init", "-q")
    (p / "spec.py").write_text("x\n")
    (p / "spec.trace.json").write_text("{}\n")
    (p / ".gitignore").write_text("ran.log\n")
    stub(p, 0)
    git(p, "add", ".")
    git(p, "commit", "-qm", "init")
    return p


def dirty(p: Path, n: int = 1) -> None:
    (p / "app.go").write_text(f"v{n}\n")


def test_strict_is_the_default_and_blocks_a_failing_check(proj: Path) -> None:
    stub(proj, 1)
    r = fire(proj)
    assert r.returncode == 2, r.stderr
    assert "FAILED" in r.stderr and "finding" in r.stderr


def test_a_passing_check_exits_0_and_records_head(proj: Path) -> None:
    dirty(proj)
    r = fire(proj)
    assert r.returncode == 0 and "passed" in r.stderr
    rec = (
        Path(git(proj, "rev-parse", "--absolute-git-dir")) / "lattice-gate" / "last-pass"
    ).read_text()
    assert f"head={git(proj, 'rev-parse', 'HEAD')}" in rec
    assert f"tree={git(proj, 'rev-parse', 'HEAD^{tree}')}" in rec


def test_the_state_file_does_not_dirty_the_tree(proj: Path) -> None:
    fire(proj)
    assert git(proj, "status", "--porcelain") == ""


def test_the_counter_caps_after_n_blocks(proj: Path) -> None:
    stub(proj, 1)
    dirty(proj)
    codes = [fire(proj, active=False).returncode]
    codes += [fire(proj, active=True).returncode for _ in range(4)]
    assert codes == [2, 2, 2, 0, 0]
    r = fire(proj, active=True)
    assert r.returncode == 0
    assert "still failing" in r.stderr and "NOT green" in r.stderr


def test_the_cap_is_configurable(proj: Path) -> None:
    stub(proj, 1)
    env = {"LATTICE_GATE_MAX_BLOCKS": "1"}
    assert fire(proj, **env).returncode == 2
    r = fire(proj, active=True, **env)
    assert r.returncode == 0 and "still failing after 1 blocked" in r.stderr


def test_a_bad_cap_falls_back_to_3(proj: Path) -> None:
    stub(proj, 1)
    codes = [fire(proj, active=i > 0, LATTICE_GATE_MAX_BLOCKS="lots").returncode for i in range(4)]
    assert codes == [2, 2, 2, 0]


def test_stop_hook_active_is_not_a_free_pass(proj: Path) -> None:
    stub(proj, 1)
    r = fire(proj, active=True)
    assert r.returncode == 2 and "FAILED" in r.stderr


def test_a_fresh_stop_starts_a_new_count(proj: Path) -> None:
    stub(proj, 1)
    assert [fire(proj, active=a).returncode for a in (False, True, True, True)] == [2, 2, 2, 0]
    assert fire(proj, active=False).returncode == 2  # the next prompt's stop is gated again


def test_a_pass_resets_the_count(proj: Path) -> None:
    stub(proj, 1)
    assert fire(proj).returncode == 2
    stub(proj, 0)
    assert fire(proj, active=True).returncode == 0
    stub(proj, 1)
    assert [fire(proj, active=a).returncode for a in (False, True, True, True)] == [2, 2, 2, 0]


def test_the_count_is_per_session(proj: Path) -> None:
    stub(proj, 1)
    for _ in range(3):
        fire(proj, active=True, sid="a")
    assert fire(proj, active=True, sid="a").returncode == 0
    assert fire(proj, active=True, sid="b").returncode == 2


def test_warn_mode_never_blocks(proj: Path) -> None:
    stub(proj, 1)
    r = fire(proj, LATTICE_GATE="warn")
    assert r.returncode == 0 and "FAILED" in r.stderr


def test_off_mode_skips_everything(proj: Path) -> None:
    stub(proj, 1)
    r = fire(proj, LATTICE_GATE="off")
    assert r.returncode == 0 and runs(proj) == 0 and r.stderr == ""


def test_a_committed_clean_tree_with_a_new_head_still_runs_the_check(proj: Path) -> None:
    assert fire(proj).returncode == 0  # the first stop: no record, so it runs and records
    assert runs(proj) == 1
    stub(proj, 1)
    git(proj, "commit", "-aqm", "break the gate")  # committed: the tree is clean, HEAD moved
    assert git(proj, "status", "--porcelain") == ""
    r = fire(proj)
    assert r.returncode == 2 and "FAILED" in r.stderr
    assert runs(proj) == 2


def test_an_unchanged_head_after_a_pass_does_not_rerun(proj: Path) -> None:
    assert fire(proj).returncode == 0
    assert runs(proj) == 1
    assert fire(proj).returncode == 0 and runs(proj) == 1
    dirty(proj)  # an edit brings the check back
    assert fire(proj).returncode == 0 and runs(proj) == 2


def test_a_failure_forgets_the_last_pass(proj: Path) -> None:
    assert fire(proj).returncode == 0 and runs(proj) == 1
    stub(proj, 1)
    dirty(proj)
    assert fire(proj).returncode == 2 and runs(proj) == 2
    (proj / "app.go").unlink()
    git(proj, "checkout", "-q", "--", "check.sh")  # clean, at the HEAD of the old pass
    assert git(proj, "status", "--porcelain") == ""
    assert fire(proj).returncode == 0 and runs(proj) == 3  # the record was dropped: it reruns


def test_missing_check_sh_in_a_lattice_project_blocks(proj: Path) -> None:
    (proj / "check.sh").unlink()
    r = fire(proj)
    assert r.returncode == 2 and "missing or not executable" in r.stderr


def test_a_non_executable_check_sh_blocks(proj: Path) -> None:
    (proj / "check.sh").chmod(0o644)
    r = fire(proj)
    assert r.returncode == 2 and "missing or not executable" in r.stderr


def test_a_deleted_check_sh_is_tamper_even_after_a_pass(proj: Path) -> None:
    assert fire(proj).returncode == 0
    (proj / "check.sh").unlink()
    git(proj, "commit", "-aqm", "drop the gate")  # clean tree; the old pass record is stale
    r = fire(proj)
    assert r.returncode == 2 and "missing or not executable" in r.stderr


def test_a_missing_check_sh_block_is_bounded_too(proj: Path) -> None:
    (proj / "check.sh").unlink()
    codes = [fire(proj, active=i > 0).returncode for i in range(4)]
    assert codes == [2, 2, 2, 0]


def test_a_missing_check_sh_only_warns_in_warn_mode(proj: Path) -> None:
    (proj / "check.sh").unlink()
    r = fire(proj, LATTICE_GATE="warn")
    assert r.returncode == 0 and "missing or not executable" in r.stderr


def test_a_non_lattice_project_without_check_sh_is_a_no_op(tmp_path: Path) -> None:
    p = tmp_path / "plain"
    p.mkdir()
    git(p, "init", "-q")
    (p / "a.txt").write_text("x\n")
    r = fire(p)
    assert r.returncode == 0 and r.stderr == ""


def test_a_non_git_lattice_project_still_runs_and_blocks(tmp_path: Path) -> None:
    p = tmp_path / "nogit"
    p.mkdir()
    (p / "spec.py").write_text("x\n")
    (p / "spec.trace.json").write_text("{}\n")
    stub(p, 1)
    r = fire(p, TMPDIR=str(tmp_path))
    assert r.returncode == 2 and "FAILED" in r.stderr


def test_only_the_last_25_lines_of_output_are_shown(proj: Path) -> None:
    (proj / "check.sh").write_text(
        "#!/bin/sh\nfor i in $(seq 1 60); do echo line$i; done\nexit 1\n"
    )
    (proj / "check.sh").chmod(0o755)
    r = fire(proj)
    assert r.returncode == 2
    assert "line60" in r.stderr and "line36" in r.stderr and "line35" not in r.stderr


def test_unreadable_stdin_does_not_crash(proj: Path) -> None:
    stub(proj, 1)
    e = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
    r = subprocess.run(
        ["bash", str(HOOK)], input="not json", cwd=proj, env=e, capture_output=True, text=True
    )
    assert r.returncode == 2
