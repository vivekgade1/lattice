"""Spec sync (API.md v1.12, lattice/studio/rebuild.py + plan.py + server.py): the spec files a
rebuild protects, as committed at the repository's HEAD, are compared with a target branch's
(``GET /api/branches`` ``spec``, a plan's ``spec_drift``) and synced onto it by ONE commit made
in a temporary worktree and landed with a compare-and-swap ``update-ref``
(``POST /api/branches/{name}/sync-spec``); ``[rebuild] sync_spec = "auto"`` syncs before a
plan's start baseline. The seed project (tests/studio/seed.py), the stub planner and stub
agent; no LLM."""

from __future__ import annotations

import glob
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.studio.rebuild import SPEC_FILES, SYNC_PREFIX, Rebuilder, RebuildError, load_table
from lattice.studio.server import StubGenerator, make_server
from tests.studio.seed import cleanup_worktrees, finish, git, manager, seed_project
from tests.studio.test_server import request

WEB = Path(__file__).resolve().parents[2] / "lattice" / "studio" / "web" / "js"
FIELDS = {"branch", "synced_from", "files", "commit", "previous_tip"}


def temp_leftovers() -> set[str]:
    return set(glob.glob(os.path.join(tempfile.gettempdir(), SYNC_PREFIX + "*")))


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The seed with a build branch ``build/x`` one commit ahead of the seed (``built.txt``),
    then a commit on main that changes REQUIREMENTS.md (a spec file) and ``notes.py`` (not
    one): build/x's spec is behind."""
    # this test's own temp dir: a sync's temporary worktree must be gone afterwards (a shared
    # system temp dir would see other processes' syncs)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    root = seed_project(tmp_path)
    seed = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "build/x")
    (root / "built.txt").write_text("built on the branch\n")
    git(root, "add", "built.txt")
    git(root, "commit", "-q", "-m", "build: something")
    git(root, "checkout", "-q", "main")
    assert git(root, "rev-parse", "HEAD") == seed
    with (root / "REQUIREMENTS.md").open("a") as f:
        f.write("- Notes can be starred.\n")
    (root / "notes.py").write_text("# the owner's code\n")
    git(root, "add", "REQUIREMENTS.md", "notes.py")
    git(root, "commit", "-q", "-m", "spec: starred, and some code")
    yield root
    cleanup_worktrees(root)
    assert not temp_leftovers(), "a sync left its temporary worktree behind"


def rebuilder(root: Path, **table: Any) -> Rebuilder:
    return Rebuilder(root, {**load_table(root), **table}, name="notes", model="sonnet",
                     env=dict(os.environ))  # fmt: skip


def tip(root: Path, name: str) -> str:
    return git(root, "rev-parse", f"refs/heads/{name}")


def refused(fn: Any, status: int, code: str | None) -> RebuildError:
    with pytest.raises(RebuildError) as ei:
        fn()
    assert (ei.value.status, ei.value.code) == (status, code), ei.value.detail
    return ei.value


# ---------------------------------------------------------------- drift


def test_drift_counts_committed_spec_files_only(repo: Path) -> None:
    rb = rebuilder(repo)
    head = git(repo, "rev-parse", "HEAD")
    assert rb.spec_names() == list(SPEC_FILES)
    assert rb.spec_drift("build/x") == {"head": head, "files": ["REQUIREMENTS.md"]}
    # an uncommitted edit of a spec file never counts, on either side of the comparison
    (repo / "spec.py").write_text("# uncommitted\n")
    (repo / "spec.trace.json").write_text("{}\n")
    assert rb.spec_drift("build/x") == {"head": head, "files": ["REQUIREMENTS.md"]}
    by = {b["name"]: b for b in rb.branches()["branches"]}
    assert by["build/x"]["spec"] == {"in_sync": False, "files": ["REQUIREMENTS.md"], "head": head}
    assert by["main"]["spec"] is None, "the current branch"
    # a branch at HEAD is in sync, whatever the working copy holds
    git(repo, "branch", "same")
    by = {b["name"]: b for b in rb.branches()["branches"]}
    assert by["same"]["spec"] == {"in_sync": True, "files": [], "head": head}
    assert rb.spec_drift("same") is None and rb.spec_drift("nope") is None
    # a protected branch has no spec view
    git(repo, "branch", "release")
    by = {b["name"]: b for b in rb.branches()["branches"]}
    assert by["release"]["protected"] and by["release"]["spec"] is None


def test_mcp_protected_files_and_a_file_absent_on_one_side(repo: Path) -> None:
    rb = Rebuilder(repo, load_table(repo), name="notes", model="sonnet",
                   protected=["schema.sql", "./docs/"], env=dict(os.environ))  # fmt: skip
    assert rb.spec_names() == [*SPEC_FILES, "schema.sql", "docs"]
    (repo / "schema.sql").write_text("CREATE TABLE notes (id INTEGER);\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("a\n")
    git(repo, "add", "schema.sql", "docs")
    git(repo, "commit", "-q", "-m", "protected files")
    drift = rb.spec_drift("build/x")
    assert drift is not None and drift["files"] == ["REQUIREMENTS.md", "docs/a.md", "schema.sql"]
    # deleted on HEAD, present on the branch: the sync deletes it there
    out = rb.sync_spec("build/x")
    assert out["files"] == ["REQUIREMENTS.md", "docs/a.md", "schema.sql"]
    git(repo, "rm", "-q", "schema.sql")
    git(repo, "commit", "-q", "-m", "drop the schema")
    assert rb.spec_drift("build/x")["files"] == ["schema.sql"]  # type: ignore[index]
    rb.sync_spec("build/x")
    assert "schema.sql" not in git(repo, "ls-tree", "--name-only", "build/x").split()
    assert rb.spec_drift("build/x") is None


# ---------------------------------------------------------------- the sync commit


def test_the_sync_is_one_commit_of_the_committed_spec_files_only(repo: Path) -> None:
    rb = rebuilder(repo)
    head = git(repo, "rev-parse", "HEAD")
    old = tip(repo, "build/x")
    # uncommitted edits in the owner's working copy: never synced, never touched
    (repo / "REQUIREMENTS.md").write_text("# an uncommitted rewrite\n")
    (repo / "spec.py").write_text("# uncommitted\n")
    status = git(repo, "status", "--porcelain")
    out = rb.sync_spec("build/x")
    new = tip(repo, "build/x")
    assert set(out) == FIELDS
    assert out == {"branch": "build/x", "synced_from": head, "files": ["REQUIREMENTS.md"],
                   "commit": new, "previous_tip": old}  # fmt: skip
    assert git(repo, "rev-list", "--parents", "-n", "1", new).split() == [new, old], "one parent"
    assert git(repo, "rev-list", "--count", f"{old}..{new}") == "1", "one commit"
    assert git(repo, "diff", "--name-only", old, new).split() == ["REQUIREMENTS.md"]
    want = git(repo, "show", f"{head}:REQUIREMENTS.md")
    assert git(repo, "show", f"{new}:REQUIREMENTS.md") == want
    assert git(repo, "show", f"{new}:spec.py") == git(repo, "show", f"{old}:spec.py")
    assert git(repo, "show", f"{new}:built.txt") == "built on the branch", "the branch's work stays"
    assert "notes.py" not in git(repo, "ls-tree", "--name-only", new).split(), "no other history"
    msg = git(repo, "show", "-s", "--format=%B", new)
    assert msg.splitlines()[0] == f"spec: sync from main {head[:7]}"
    assert msg.splitlines()[-1] == f"Lattice-Spec-Sync: {head}"
    assert git(repo, "show", "-s", "--format=%an <%ae>", new) == "Studio Test <studio@example.com>"
    # the owner's branch, checkout and working copy are untouched
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "status", "--porcelain") == status
    assert (repo / "REQUIREMENTS.md").read_text() == "# an uncommitted rewrite\n"
    assert len(git(repo, "worktree", "list", "--porcelain").split("worktree ")) == 2, "removed"
    assert rb.spec_drift("build/x") is None


def test_the_branch_moved_during_the_sync_is_refused_by_the_compare_and_swap(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rb = rebuilder(repo)
    real = rb._sync_commit
    moved: dict[str, str] = {}

    def racing(*a: Any, **k: Any) -> str:
        sha = real(*a, **k)
        other = git(repo, "commit-tree", "-m", "someone else", "-p", tip(repo, "build/x"),
                    f"{tip(repo, 'build/x')}^{{tree}}")  # fmt: skip
        git(repo, "update-ref", "refs/heads/build/x", other)
        moved["to"] = other
        return sha

    monkeypatch.setattr(rb, "_sync_commit", racing)
    e = refused(lambda: rb.sync_spec("build/x"), 409, "branch_moved")
    assert "does not descend" in e.detail or "moved" in e.detail
    assert tip(repo, "build/x") == moved["to"], "nothing overwritten"


# ---------------------------------------------------------------- refusals


def test_each_refusal_changes_nothing(repo: Path) -> None:
    rb = rebuilder(repo)
    old = tip(repo, "build/x")
    count = git(repo, "rev-list", "--all", "--count")
    refused(lambda: rb.sync_spec("build/x", {"force": True}), 422, None)
    refused(lambda: rb.sync_spec("bad..name"), 422, "branch_name_invalid")
    refused(lambda: rb.sync_spec("-x"), 422, "branch_name_invalid")
    refused(lambda: rb.sync_spec("release"), 409, "branch_protected")  # protected, even unborn
    git(repo, "branch", "release", old)
    refused(lambda: rb.sync_spec("release"), 409, "branch_protected")
    refused(lambda: rb.sync_spec("nope"), 404, "no_branch")
    e = refused(lambda: rb.sync_spec("main"), 409, "branch_checked_out")
    assert e.detail.startswith("branch main is checked out in ")
    # checked out in a linked worktree: refused before anything is made
    wt = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", str(wt), "build/x")
    e = refused(lambda: rb.sync_spec("build/x"), 409, "branch_checked_out")
    assert e.detail.startswith(f"branch build/x is checked out in {wt.resolve()}"), e.detail
    git(repo, "worktree", "remove", "--force", str(wt))
    # the build slot: a plan, or a running build
    rb.plan_active = "p7"
    e = refused(lambda: rb.sync_spec("build/x"), 409, "build_running")
    assert "(p7)" in e.detail
    rb.plan_active = None
    assert tip(repo, "build/x") == old
    assert git(repo, "rev-list", "--all", "--count") == count, "no commit was made"
    # nothing to sync: no (empty) commit
    rb.sync_spec("build/x")
    synced = tip(repo, "build/x")
    count = git(repo, "rev-list", "--all", "--count")
    e = refused(lambda: rb.sync_spec("build/x"), 409, "nothing_to_sync")
    head7 = git(repo, "rev-parse", "HEAD")[:7]
    assert e.detail == f"the spec on build/x is the same as on main at {head7}: nothing to sync"
    assert tip(repo, "build/x") == synced
    assert git(repo, "rev-list", "--all", "--count") == count


def test_a_running_plan_holds_the_slot(repo: Path) -> None:
    pm = manager(repo)  # the seed's plan for rules R3 holds its node 30 seconds
    pid = pm.create({"scope": {"rules": ["R3"]}})["id"]
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    pm.run(pid)
    try:
        refused(lambda: pm.rb.sync_spec("build/x"), 409, "build_running")
    finally:
        pm.stop(pid)
        finish(pm, pid)
    assert tip(repo, "build/x") != git(repo, "rev-parse", "HEAD")
    assert pm.rb.sync_spec("build/x")["branch"] == "build/x", "free again once it stopped"


# ---------------------------------------------------------------- plans


def test_a_plan_carries_spec_drift_and_a_warning(repo: Path) -> None:
    pm = manager(repo, sync_spec="off")
    head = git(repo, "rev-parse", "HEAD")
    pid = pm.create({"scope": {"areas": ["writing-notes"]}, "planner": "fallback",
                     "branch": {"name": "build/x"}})["id"]  # fmt: skip
    rec = pm.get(pid, wait=30)
    assert rec["status"] == "ready"
    assert rec["spec_drift"] == {"head": head, "files": ["REQUIREMENTS.md"]}
    warning = "The spec on main differs from build/x in REQUIREMENTS.md: sync it first"
    assert rec["warnings"] == [warning]
    pm.rb.sync_spec("build/x")
    rec = pm.get(pid)
    assert rec["spec_drift"] is None and rec["warnings"] == [], "refreshed when read"
    # a new target is compared with the commit it starts from
    branch = {"name": "fresh", "create": True, "from": "build/x~1"}
    pid2 = pm.create({"scope": {"areas": ["writing-notes"]}, "planner": "fallback",
                      "branch": branch})["id"]  # fmt: skip
    assert pm.get(pid2, wait=30)["spec_drift"] == {"head": head, "files": ["REQUIREMENTS.md"]}
    pid3 = pm.create({"scope": {"areas": ["writing-notes"]}, "planner": "fallback",
                      "branch": {"name": "fresh2", "create": True}})["id"]  # fmt: skip
    assert pm.get(pid3, wait=30)["spec_drift"] is None


def _plan(pm: Any) -> str:
    pid = pm.create({"scope": {"areas": ["writing-notes"]}, "planner": "fallback",
                     "branch": {"name": "build/x"}})["id"]  # fmt: skip
    assert pm.get(pid, wait=30)["status"] == "ready"
    pm.approve(pid)
    return pid


def test_auto_syncs_before_the_start_baseline(repo: Path) -> None:
    pm = manager(repo, sync_spec="auto")
    head = git(repo, "rev-parse", "HEAD")
    old = tip(repo, "build/x")
    pid = _plan(pm)
    pm.run(pid)
    rec = finish(pm, pid)
    synced = git(repo, "rev-list", "--first-parent", "--reverse", f"{old}..build/x").split()[0]
    assert git(repo, "rev-list", "--parents", "-n", "1", synced).split() == [synced, old]
    assert git(repo, "show", "-s", "--format=%s", synced) == f"spec: sync from main {head[:7]}"
    assert rec["baseline"]["sha"] == synced, "the start baseline ran on the sync commit"
    events = [e["event"] for e in rec["timeline"]]
    sync_at = events.index(f"spec synced from main at {head[:7]} (REQUIREMENTS.md)")
    base_at = next(i for i, e in enumerate(events) if e.startswith("start baseline on build/x"))
    assert sync_at < base_at
    assert rec["spec_drift"] is None and rec["warnings"] == []


def test_off_syncs_nothing_and_keeps_the_warning(repo: Path) -> None:
    pm = manager(repo, sync_spec="off")
    old = tip(repo, "build/x")
    pid = _plan(pm)
    pm.run(pid)
    rec = finish(pm, pid)
    assert rec["baseline"]["sha"] == old
    assert not any(e["event"].startswith("spec synced") for e in rec["timeline"])
    assert rec["spec_drift"] is not None and rec["warnings"][-1].startswith("The spec on main")


def test_an_auto_sync_refusal_is_the_runs_409(repo: Path) -> None:
    pm = manager(repo, sync_spec="auto")
    pid = _plan(pm)
    wt = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", str(wt), "build/x")
    try:
        with pytest.raises(Exception) as ei:
            pm.run(pid)
        assert (ei.value.status, ei.value.code) == (409, "branch_checked_out")  # type: ignore[attr-defined]
        assert pm.get(pid)["status"] == "approved" and pm.rb.plan_active is None
    finally:
        git(repo, "worktree", "remove", "--force", str(wt))


# ---------------------------------------------------------------- HTTP and the mock


@pytest.fixture()
def served(repo: Path) -> Iterator[str]:
    srv = make_server(repo, 0, generator=StubGenerator())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_the_routes(served: str, repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    status, br = request("GET", served + "/api/branches")
    assert status == 200
    by = {b["name"]: b for b in br["branches"]}
    assert by["build/x"]["spec"] == {"in_sync": False, "files": ["REQUIREMENTS.md"], "head": head}
    assert by["main"]["spec"] is None
    assert request("POST", served + "/api/branches/build/x/sync-spec", {"x": 1}) == (
        422, {"detail": "unknown field(s): x"})  # fmt: skip
    status, out = request("POST", served + "/api/branches/build%2Fx/sync-spec", {})
    assert status == 200 and set(out) == FIELDS and out["files"] == ["REQUIREMENTS.md"]
    by = {b["name"]: b for b in request("GET", served + "/api/branches")[1]["branches"]}
    assert by["build/x"]["head"] == out["commit"] and by["build/x"]["spec"]["in_sync"] is True
    status, body = request("POST", served + "/api/branches/build/x/sync-spec")
    assert status == 409 and body["code"] == "nothing_to_sync"
    cases = (("main", "branch_checked_out", 409), ("release", "branch_protected", 409),
             ("nope", "no_branch", 404), ("bad..x", "branch_name_invalid", 422))  # fmt: skip
    for name, code, st in cases:
        status, body = request("POST", served + f"/api/branches/{name}/sync-spec", {})
        assert (status, body.get("code")) == (st, code), (name, body)


def test_the_mock_serves_the_same_fields() -> None:
    from lattice.studio.mock_server import SPEC_BEHIND_BRANCH
    from lattice.studio.mock_server import make_server as make_mock

    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    b = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        br = request("GET", b + "/api/branches")[1]
        by = {x["name"]: x for x in br["branches"]}
        assert by["main"]["spec"] is None
        assert set(by[SPEC_BEHIND_BRANCH]["spec"]) == {"in_sync", "files", "head"}
        assert by[SPEC_BEHIND_BRANCH]["spec"]["in_sync"] is False
        body = {"scope": "all", "branch": {"name": SPEC_BEHIND_BRANCH}}
        pid = request("POST", b + "/api/plan", body)[1]["id"]
        rec = request("GET", f"{b}/api/plan/{pid}")[1]
        assert set(rec["spec_drift"]) == {"head", "files"}
        assert rec["warnings"][-1].startswith(f"The spec on main differs from {SPEC_BEHIND_BRANCH}")
        for name, code in (("main", "branch_protected"), ("nope", "no_branch")):
            assert request("POST", f"{b}/api/branches/{name}/sync-spec", {})[1]["code"] == code
        status, out = request("POST", b + "/api/branches/build%2Fspec-behind/sync-spec", {})
        assert status == 200 and set(out) == FIELDS
        by = {x["name"]: x for x in request("GET", b + "/api/branches")[1]["branches"]}
        assert by[SPEC_BEHIND_BRANCH]["spec"]["in_sync"] is True
        assert by[SPEC_BEHIND_BRANCH]["head"] == out["commit"]
        assert request("GET", f"{b}/api/plan/{pid}")[1]["spec_drift"] is None
        status, body = request("POST", b + "/api/branches/build/spec-behind/sync-spec", {})
        assert (status, body["code"]) == (409, "nothing_to_sync")
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_ui_offers_one_sync_button_and_no_confirm() -> None:
    api = (WEB / "api.js").read_text()
    plan = (WEB / "views" / "plan.js").read_text()
    areas = (WEB / "views" / "areas.js").read_text()
    assert "/sync-spec`" in api and "syncSpec:" in api
    start = plan.index("export function specSyncBanner")
    banner = plan[start : plan.index("\n}\n", start)]
    assert '"Sync spec"' in banner and "api.syncSpec(target)" in banner
    assert "is newer than" in banner and 'role: "alert"' in banner
    assert not re.search(r"\bconfirm\(", banner)
    assert "specSyncBanner(" in areas and "planRunning()" in areas
    assert 'r.spec_drift && r.status !== "running"' in plan
