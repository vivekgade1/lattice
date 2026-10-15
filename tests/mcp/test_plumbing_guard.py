"""The guard walks DOWN (lattice/mcp/guard.py ``MultiGuard.project_below``, plumbing D3 as
amended): ``rm -rf`` of a directory that holds a project nobody named is denied, with a depth, a
budget that fails open, pruned names, no symlink following, and none of the widenings the
amendment reverted (``git rm``, ``git mv``, pathless ``git clean``, dry runs)."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from lattice.mcp import guard as G
from lattice.mcp.guard import MultiGuard, main
from tests.mcp.plumbing_support import GUARD_ROWS, build_guard_fixture, make_project

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("g8") / "lg"
    build_guard_fixture(root)
    return root


def event(tool: str, cwd: Path, arg: str) -> dict[str, Any]:
    ti = {"command": arg} if tool == "Bash" else {"file_path": arg}
    return {"tool_name": tool, "tool_input": ti, "cwd": str(cwd), "hook_event_name": "PreToolUse"}


def hook(ev: dict[str, Any], *args: str) -> tuple[int, str, str]:
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(list(args), stdin=io.StringIO(json.dumps(ev)))
    return rc, out.getvalue(), err.getvalue()


def denied_project(out: str) -> str | None:
    if not out.strip():
        return None
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    return reason.split(" of project ", 1)[1].split(" (", 1)[0]


@pytest.mark.parametrize("row", GUARD_ROWS, ids=[f"row{r[0]}" for r in GUARD_ROWS])
def test_the_table(lg: Path, row) -> None:
    n, cwd, tool, arg, project, new, _old = row
    if cwd == "@base":
        cwd_p = lg.parent
    else:
        cwd_p = lg / cwd if cwd else lg
    arg = arg.replace("{W}", str(REPO))
    args = ["--project", str(lg / project)] if project else []
    rc, out, _err = hook(event(tool, cwd_p, arg), *args)
    assert rc == 0
    assert denied_project(out) == new, (n, out)


def test_exit2_mode_denies_with_the_reason_on_stderr(lg: Path) -> None:
    rc, out, err = hook(event("Bash", lg, "rm -rf ws"), "--mode", "exit2")
    assert rc == 2 and out == "" and "of project ws-p1 (" in err


def test_the_deny_has_exactly_the_documented_shape(lg: Path) -> None:
    _rc, out, _ = hook(event("Bash", lg, "rm -rf ws"))
    body = json.loads(out)
    assert list(body) == ["hookSpecificOutput"]
    assert set(body["hookSpecificOutput"]) == {
        "hookEventName", "permissionDecision", "permissionDecisionReason"
    }  # fmt: skip


def test_the_walk_names_the_shallowest_then_first_sorted_project(tmp_path: Path) -> None:
    make_project(tmp_path / "t" / "b" / "p", "deep-b")
    make_project(tmp_path / "t" / "a" / "x" / "p", "deeper-a")
    make_project(tmp_path / "t" / "z", "shallow-z")
    g = MultiGuard()
    assert g.project_below(str(tmp_path / "t")) == tmp_path / "t" / "z"
    assert g.project_below(str(tmp_path / "t" / "a")) == tmp_path / "t" / "a" / "x" / "p"
    assert g.project_below(str(tmp_path / "missing")) is None
    assert g.project_below(str(tmp_path / "t" / "z" / "spec.py")) is None, "a file is not walked"


def test_the_budget_fails_open_and_is_deterministic(lg: Path, monkeypatch) -> None:
    g = MultiGuard()
    target = str(lg / "wide")
    assert g.project_below(target) is None
    monkeypatch.setattr(G, "WALK_BUDGET", 10**6)
    assert g.project_below(target) == lg / "wide" / "zzz" / "p", "with the budget it is found"
    monkeypatch.setattr(G, "WALK_BUDGET", 4000)
    assert g.project_below(target) is None and g.project_below(target) is None


def test_the_depth_is_five_levels_below_the_target(lg: Path) -> None:
    g = MultiGuard()
    assert g.project_below(str(lg / "cap" / "x1")) == lg / "cap/x1/x2/x3/x4/x5/p6"
    assert g.project_below(str(lg / "cap")) is None


def test_pruned_names_are_neither_entered_nor_walked(tmp_path: Path) -> None:
    make_project(tmp_path / "t" / "node_modules" / "p", "inside-pruned")
    make_project(tmp_path / "t" / ".git" / "p", "inside-git")
    g = MultiGuard()
    assert g.project_below(str(tmp_path / "t")) is None
    assert g.project_below(str(tmp_path / "t" / "node_modules")) is None


def test_an_unlistable_directory_is_skipped_not_fatal(tmp_path: Path) -> None:
    locked = tmp_path / "t" / "a"
    locked.mkdir(parents=True)
    make_project(tmp_path / "t" / "b", "reachable")
    locked.chmod(0)
    try:
        assert MultiGuard().project_below(str(tmp_path / "t")) == tmp_path / "t" / "b"
    finally:
        locked.chmod(0o755)


def test_mv_walks_its_sources_and_not_its_destination(lg: Path) -> None:
    g = MultiGuard()
    assert g.decide(event("Bash", lg, "mv ws ws-old")) is not None
    assert g.decide(event("Bash", lg, "mv notes.txt ws/")) is None
    assert g.decide(event("Bash", lg, "mv -f notes.txt ws")) is None
    assert g.decide(event("Bash", lg, "mv ws notes.txt")) is not None, "ws is a source"


def test_the_reverted_widenings_stay_reverted(lg: Path) -> None:
    g = MultiGuard()
    ev = lambda cwd, c: event("Bash", lg / cwd if cwd else lg, c)  # noqa: E731
    assert g.decide(ev("repo2", "git rm -r -q vendor")) is None
    assert g.decide(ev("", "git mv ws ws-old")) is None
    assert g.decide(ev("repo2", "git clean -fdx")) is None
    assert g.decide(ev("repo2", "git clean -n")) is None
    assert g.decide(ev("ws/p1", "git clean -fd")) is None
    assert g.decide(ev("ws/p1", "git clean -fdx .")) is not None, "today: denied in a project"


def test_a_walk_over_the_budget_is_fast(lg: Path) -> None:
    t0 = time.monotonic()
    MultiGuard().decide(event("Bash", lg, "rm -rf wide"))
    assert time.monotonic() - t0 < 2.0


def test_an_explicit_project_below_is_found_without_the_walk(lg: Path) -> None:
    g = MultiGuard([lg / "cap/x1/x2/x3/x4/x5/p6"])
    assert g.decide(event("Bash", lg, "rm -rf cap")) is not None
