"""The Studio rebuild loop (lattice/studio/rebuild.py, API.md v1.7) against a FAKE ``claude``
executable that edits files and replays canned stream-json per round, and a trivial gate
script (passes iff ``built.txt`` exists) in a temporary git repository. The real CLI and
any API key are never involved; the stub agent covers the server routes."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.studio import rebuild as rebuild_mod
from lattice.studio.hub import discover
from lattice.studio.rebuild import (
    Rebuilder,
    RebuildError,
    build_rebuild_argv,
    capability,
    gate_env,
    parse_gate_output,
)
from lattice.studio.server import StubGenerator, make_hub_server
from tests.studio.test_server import make_project, request

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

FAKE = """#!{python}
import json, os, subprocess, sys, time
d = os.environ["FAKE_REBUILD_DIR"]
n_path = os.path.join(d, "count")
n = int(open(n_path).read()) + 1 if os.path.exists(n_path) else 1
open(n_path, "w").write(str(n))
env = {{k: v for k, v in os.environ.items() if k.startswith(("ANTHROPIC", "CLAUDE_CODE"))}}
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps({{"n": n, "argv": sys.argv[1:], "cwd": os.getcwd(), "env": env,
                        "pythonpath": os.environ.get("PYTHONPATH", "")}}) + "\\n")
plan = json.load(open(os.path.join(d, "plan.json")))
step = plan[min(n, len(plan)) - 1]
for rel, content in step.get("files", {{}}).items():
    os.makedirs(os.path.dirname(os.path.join(os.getcwd(), rel)) or ".", exist_ok=True)
    open(os.path.join(os.getcwd(), rel), "w").write(content)
if step.get("child"):
    child = subprocess.Popen(["sleep", "60"])
    open(os.path.join(d, "child.pid"), "w").write(str(child.pid))
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "{sid}"}}), flush=True)
if step.get("sleep"):
    time.sleep(step["sleep"])
if step.get("exit") is not None:
    sys.stderr.write(step.get("stderr", ""))
    sys.exit(step["exit"])
text = step.get("text", "I changed the code.")
print(json.dumps({{"type": "assistant", "message": {{"id": "m%d" % n,
      "content": [{{"type": "text", "text": text}}]}}}}), flush=True)
print(json.dumps({{"type": "result", "subtype": step.get("subtype", "success"),
      "is_error": step.get("is_error", False), "result": text, "session_id": "{sid}",
      "total_cost_usd": 0.25, "duration_ms": 1000, "num_turns": 3,
      "usage": {{"input_tokens": 10, "output_tokens": 20}}}}), flush=True)
"""

GATE = """#!/bin/sh
python -c "import probe_mod; print('probe from', probe_mod.__file__)"
if [ -f built.txt ]; then
  echo "PASS: 0 findings"
  exit 0
fi
echo "FAIL: built.txt is missing (scenario:built)"
exit 1
"""

CHANGE = {
    "id": "c1",
    "request": "Notes can be starred.",
    "status": "accepted",
    "rules": {"added": ["R1"], "changed": [], "removed": []},
    "markdown_diff": "+- Notes can be starred.\n",
    "checks": {
        "impact": {
            "verdict": "fail",
            "failing": [
                {"target": "scenario:built", "label": "not_built_yet", "message": "built.txt"},
                {"target": "scenario:other", "label": "regression", "message": "no"},
            ],
        }
    },
}


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def wait_for(pred, timeout: float = 20.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """The toy notes project as a git repo, plus the gate and a module the gate imports."""
    root = make_project(tmp_path / "notes")
    (root / "gate.sh").write_text(GATE)
    (root / "gate.sh").chmod(0o755)
    (root / "probe_mod.py").write_text("X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "gate")
    return root


@pytest.fixture()
def fake(tmp_path: Path) -> Path:
    d = tmp_path / "fake"
    d.mkdir()
    exe = d / "claude"
    exe.write_text(FAKE.format(python=sys.executable, sid=SID))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return d


def plan(fake: Path, *steps: dict[str, Any]) -> None:
    (fake / "plan.json").write_text(json.dumps(list(steps)))


def log(fake: Path) -> list[dict[str, Any]]:
    p = fake / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def rebuilder(repo: Path, fake: Path, **table: Any) -> Rebuilder:
    env = {
        **os.environ,
        "FAKE_REBUILD_DIR": str(fake),
        "ANTHROPIC_API_KEY": "sk-secret",
        "ANTHROPIC_BASE_URL": "http://evil",
        "ANTHROPIC_MODEL": "x",
        "CLAUDE_CODE_USE_BEDROCK": "1",
    }
    return Rebuilder(
        repo,
        {"gates": ["./gate.sh"], "max_rounds": 3, **table},
        name="notes",
        model="sonnet",
        claude_bin=str(fake / "claude"),
        env=env,
    )


def state(repo: Path) -> tuple[str, str]:
    return git(repo, "rev-parse", "HEAD"), git(
        repo, "status", "--porcelain", "--untracked-files=all"
    )


def finish(rb: Rebuilder, rid: str) -> dict[str, Any]:
    rec = rb.get(rid, wait=30)
    assert rec["status"] in ("passed", "gave_up", "stopped"), rec
    return rec


def cleanup(repo: Path, rec: dict[str, Any]) -> None:
    if rec.get("worktree"):
        subprocess.run(["rm", "-rf", rec["worktree"]], check=False)
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=False)


# ---------------------------------------------------------------- the loop


def test_passes_in_round_2_with_findings_fed_forward(repo: Path, fake: Path) -> None:
    before = state(repo)
    plan(
        fake,
        {"files": {"notes.txt": "half way\n"}, "text": "Round one."},
        {"files": {"built.txt": "done\n"}, "text": "Built it."},
    )
    rb = rebuilder(repo, fake)
    rid = rb.start([CHANGE])["id"]
    assert rid == "b1"
    assert state(repo) == before  # the worktree is outside the owner's view
    rec = finish(rb, rid)
    assert rec["status"] == "passed", rec
    r1, r2 = rec["rounds"]
    assert r1["check"]["verdict"] == "fail" and r2["check"]["verdict"] == "pass"
    assert r1["check"]["findings"] == [
        {
            "id": "f1",
            "title": "FAIL: built.txt is missing (scenario:built)",
            "target": "scenario:built",
            "signal_key": "gate:finding",  # spike 3: a text finding with no layer named
        }
    ]
    assert r1["files_changed"] == ["notes.txt"] and r2["files_changed"] == ["built.txt"]
    assert r1["agent_summary"] == "Round one." and r2["agent_summary"] == "Built it."
    assert r1["gates"][0]["exit_code"] == 1 and r2["gates"][0]["exit_code"] == 0
    assert "built.txt is missing" in r1["gates"][0]["output_tail"]
    # the findings are round 2's prompt, in the same session
    argv1, argv2 = (e["argv"] for e in log(fake))
    assert "--resume" not in argv1
    assert argv2[argv2.index("--resume") + 1] == SID
    assert "built.txt is missing" in argv2[-1] and "Round 1 did not pass" in argv2[-1]
    assert "Notes can be starred." in argv1[-1] and "./gate.sh" in argv1[-1]
    assert "scenario:built: built.txt" in argv1[-1] and "scenario:other" not in argv1[-1]
    # the commit is on the rebuild branch only
    sha = rec["commit"]["sha"]
    assert rec["branch"] == "rebuild/c1" and git(repo, "rev-parse", "rebuild/c1") == sha
    assert rec["base"] == before[0] and git(repo, "rev-parse", f"{sha}^") == before[0]
    assert sorted(rec["commit"]["files_changed"]) == ["built.txt", "notes.txt"]
    assert rec["commit"]["message"].startswith("build: Notes can be starred.\n")
    assert "Lattice-Rebuild: b1 (notes)" in rec["commit"]["message"]
    assert "git merge rebuild/c1" in rec["merge_hint"]
    assert state(repo) == before, "the owner's checkout never moves"
    merged = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, "HEAD"])
    assert merged.returncode == 1, "never merged"
    assert Path(rec["worktree"]).is_dir() and Path(rec["worktree"]).is_relative_to(repo)
    assert "/.lattice/rebuilds/" in (repo / ".git" / "info" / "exclude").read_text()
    assert rec["targets"] == [{"id": "scenario:built", "label": "built.txt", "done": True}]
    assert rec["usage"]["cost_usd"] == 0.5 and rec["usage"]["output_tokens"] == 40
    assert rb.build_of("c1") == {
        "state": "built",
        "rebuild_id": "b1",
        "branch": "rebuild/c1",
        "commit": sha,
    }
    cleanup(repo, rec)


def test_gives_up_at_max_rounds(repo: Path, fake: Path) -> None:
    before = state(repo)
    plan(fake, {"files": {"notes.txt": "nope\n"}})
    rb = rebuilder(repo, fake)
    rid = rb.start([CHANGE], max_rounds=2)["id"]
    rec = finish(rb, rid)
    assert rec["status"] == "gave_up" and len(rec["rounds"]) == 2 and "commit" not in rec
    assert rec["rounds"][-1]["check"]["findings"][0]["title"].startswith("FAIL: built.txt")
    assert git(repo, "rev-parse", "rebuild/c1") == before[0]  # the branch stays, no commit
    assert (Path(rec["worktree"]) / "notes.txt").is_file()  # the edits stay for review
    assert rec["merge_hint"] is None and not any(t["done"] for t in rec["targets"])
    assert rb.build_of("c1")["state"] == "gave_up"
    assert state(repo) == before
    # a gave-up change can be rebuilt again, on a new branch
    plan(fake, {"files": {"built.txt": "x\n"}})
    (fake / "count").unlink()
    rec2 = finish(rb, rb.start([CHANGE])["id"])
    assert rec2["status"] == "passed" and rec2["branch"] == "rebuild/c1-2"
    cleanup(repo, rec)
    cleanup(repo, rec2)


def test_delete_mid_agent_kills_the_group_and_removes_worktree_and_branch(
    repo: Path, fake: Path
) -> None:
    before = state(repo)
    plan(fake, {"child": True, "sleep": 60})
    rb = rebuilder(repo, fake)
    rid = rb.start([CHANGE])["id"]
    wait_for(lambda: (fake / "child.pid").exists())
    child = int((fake / "child.pid").read_text())
    wt = Path(rb.get(rid)["worktree"])
    assert wt.is_dir()
    assert rb.stop(rid) == {"id": rid, "status": "stopped"}
    rec = finish(rb, rid)
    assert rec["status"] == "stopped" and rec["rounds"] == []
    wait_for(lambda: not _alive(child), 10)
    assert not wt.exists() and rec["worktree"] is None and rec["branch"] is None
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "-q", "refs/heads/rebuild/c1"]
        ).returncode
        == 1
    )
    assert str(wt) not in git(repo, "worktree", "list")
    assert rb.build_of("c1") == {"state": "not_built", "rebuild_id": rid}
    with pytest.raises(RebuildError) as e:
        rb.stop(rid)
    assert e.value.status == 409
    assert state(repo) == before


def _alive(pid: int) -> bool:
    r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() != "" and not r.stdout.strip().startswith("Z")


def test_a_spec_edit_fails_the_round_and_is_reverted(repo: Path, fake: Path) -> None:
    before = state(repo)
    spec = (repo / "spec.py").read_text()
    plan(
        fake,
        {"files": {"spec.py": "SPEC = None\n", "built.txt": "x\n", "gate.sh": "exit 0\n"}},
        {"files": {}},
    )
    rb = rebuilder(repo, fake)
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "passed"
    r1, r2 = rec["rounds"]
    assert r1["check"]["verdict"] == "fail" and r1["spec_edited"] == ["spec.py", "gate.sh"]
    assert r1["check"]["findings"][0]["title"].startswith("spec files were edited")
    assert r1["gates"][0]["exit_code"] == 0  # the restored gate ran, and passed
    assert r2["check"]["verdict"] == "pass"
    assert (Path(rec["worktree"]) / "spec.py").read_text() == spec
    assert rec["commit"]["files_changed"] == ["built.txt"]
    assert state(repo) == before
    cleanup(repo, rec)


def test_one_rebuild_at_a_time_per_project(repo: Path, fake: Path) -> None:
    plan(fake, {"sleep": 60})
    rb = rebuilder(repo, fake)
    rid = rb.start([CHANGE])["id"]
    with pytest.raises(RebuildError) as e:
        rb.start([{**CHANGE, "id": "c2"}])
    assert e.value.status == 409 and "already running" in e.value.detail
    rb.stop(rid)


def test_the_agent_argv_and_environment(repo: Path, fake: Path) -> None:
    plan(fake, {}, {"files": {"built.txt": "x\n"}})
    rb = rebuilder(repo, fake)
    rid = rb.start([CHANGE], model="opus")["id"]
    rec = finish(rb, rid)
    entries = log(fake)
    a = entries[0]["argv"]
    wt = Path(rec["worktree"])
    assert entries[0]["cwd"] == os.path.realpath(wt)

    def opt(argv: list[str], flag: str) -> str:
        return argv[argv.index(flag) + 1]

    assert a[:4] == ["-p", "--output-format", "stream-json", "--verbose"]
    assert opt(a, "--permission-mode") == "acceptEdits"
    assert opt(a, "--tools") == "Read,Grep,Glob,Edit,Write,Bash"
    allowed = opt(a, "--allowedTools").split(",")
    assert "Bash(./gate.sh:*)" in allowed and "Bash" not in allowed
    assert [t for t in allowed if t.startswith("Bash")] == ["Bash(./gate.sh:*)"]
    assert {"Read", "Grep", "Glob", "Edit", "Write", "mcp__lattice__spec_ask"} <= set(allowed)
    assert "mcp__lattice__change_propose" not in allowed
    assert opt(a, "--disallowedTools") == "NotebookEdit"
    assert opt(a, "--setting-sources") == "" and "--disable-slash-commands" in a
    assert "--strict-mcp-config" in a and opt(a, "--model") == "opus"
    assert "never the contract" in opt(a, "--append-system-prompt")
    assert a[-2] == "--"
    mcp = (
        json.loads(Path(opt(a, "--mcp-config")).read_text())
        if Path(opt(a, "--mcp-config")).exists()
        else None
    )
    assert mcp is None, "the config files are removed once the rebuild ends"
    b = entries[1]["argv"]
    assert opt(b, "--resume") == SID
    # API credentials never reach the agent; the output cap is set
    for e in entries:
        assert not any(k.startswith("ANTHROPIC") for k in e["env"]), e["env"]
        assert "CLAUDE_CODE_USE_BEDROCK" not in e["env"]
        assert e["env"].get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
        assert e["pythonpath"].split(os.pathsep)[0] == str(wt)
    cleanup(repo, rec)


def test_the_mcp_server_and_guard_point_at_the_worktree(repo: Path, tmp_path: Path) -> None:
    mcp_path = tmp_path / "cfg" / "mcp.json"
    from lattice.studio.session import write_configs

    wt = tmp_path / "wt" / "proj"
    write_configs(mcp_path.parent, "ask", wt, sys.executable)
    argv = build_rebuild_argv(
        claude_bin="claude",
        text="go",
        model="sonnet",
        mcp_config=mcp_path,
        settings=mcp_path.parent / "settings.json",
        gates=["./gate.sh", "uv run pytest -q"],
    )
    mcp = json.loads(mcp_path.read_text())["mcpServers"]["lattice"]["args"]
    assert "--readonly" in mcp and mcp[mcp.index("--project") + 1] == str(wt)
    guard = json.loads((mcp_path.parent / "settings.json").read_text())
    cmd = guard["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert f"--project {wt}" in cmd and "lattice.mcp.guard" in cmd
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert "Bash(uv run pytest -q:*)" in allowed


def test_the_gate_runs_in_the_worktree_with_it_first_on_the_import_path(
    repo: Path, fake: Path
) -> None:
    (repo / "probe_mod.py").write_text("X = 'main'\n")  # an uncommitted edit in the owner's copy
    plan(fake, {"files": {"built.txt": "x\n"}})
    rb = rebuilder(repo, fake)
    rec = finish(rb, rb.start([CHANGE])["id"])
    out = rec["rounds"][0]["gates"][0]["output_tail"]
    wt = os.path.realpath(rec["worktree"])
    assert f"probe from {wt}/probe_mod.py" in out.replace(rec["worktree"], wt)
    env = gate_env(
        {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/somewhere",
            "ANTHROPIC_X": "1",
            "ANTHROPIC_API_KEY": "k",
        },
        repo=repo,
        worktree=Path(rec["worktree"]),
        project_rel=".",
        sys_path=["src"],
    )
    pp = env["PYTHONPATH"].split(os.pathsep)
    assert pp[0] == rec["worktree"] and pp[-1] == "/somewhere"
    assert str(Path(rec["worktree"], "src").resolve()) in pp
    assert env["UV_NO_SYNC"] == "1" and "UV_PROJECT_ENVIRONMENT" in env
    assert not any(k.startswith("ANTHROPIC") for k in env)
    (repo / "probe_mod.py").write_text("X = 1\n")
    cleanup(repo, rec)


@pytest.mark.skipif(not __import__("shutil").which("uv"), reason="uv is not installed")
def test_a_uv_run_gate_imports_the_worktree_and_never_syncs(
    tmp_path: Path, fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uv project: ``uv run`` in the worktree uses the main checkout's .venv (no new venv,
    no sync: UV_NO_SYNC) and imports the rebuilt module from the worktree.

    Isolated from the ambient environment: when Studio runs this suite as a rebuild gate
    (Studio rebuilding itself) the gate env already carries UV_PROJECT_ENVIRONMENT and
    VIRTUAL_ENV, which would otherwise win over the throwaway project's own venv."""
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    root = tmp_path / "uvproj"
    make_project(root, repo=False)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0"\nrequires-python = ">=3.10"\n'
        'dependencies = ["six"]\n'
    )
    (root / "probe_mod.py").write_text("X = 1\n")
    (root / ".gitignore").write_text(".venv/\n")
    subprocess.run(["uv", "venv", "-q", str(root / ".venv")], check=True)
    site = sorted(p.name for p in root.glob(".venv/lib/python*/site-packages/*"))
    gate = root / "gate.sh"
    gate.write_text(
        '#!/bin/sh\nuv run python -c "import probe_mod, sys; '
        "print('probe from', probe_mod.__file__); print('exe', sys.executable)\"\n"
    )
    gate.chmod(0o755)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["add", "-A"],
        ["commit", "-q", "-m", "i"],
    ):
        git(root, *args)
    plan(fake, {"files": {"probe_mod.py": "X = 2\n"}})
    rb = rebuilder(root, fake, max_rounds=1)
    rec = finish(rb, rb.start([CHANGE])["id"])
    out = rec["rounds"][0]["gates"][0]["output_tail"]
    wt = os.path.realpath(rec["worktree"])
    assert rec["status"] == "passed", out
    assert f"probe from {wt}/probe_mod.py" in out.replace(rec["worktree"], wt), out
    assert f"exe {os.path.realpath(root / '.venv')}" in out.replace(
        str(root), os.path.realpath(root)
    )
    assert not (Path(rec["worktree"]) / ".venv").exists(), "no venv per worktree"
    assert sorted(p.name for p in root.glob(".venv/lib/python*/site-packages/*")) in (
        site,
        sorted([*site, "__pycache__"]),
    ), "never synced"
    cleanup(root, rec)


def test_a_usage_limit_becomes_an_error_round_and_ends_the_rebuild(repo: Path, fake: Path) -> None:
    plan(
        fake,
        {
            "is_error": True,
            "subtype": "error_during_execution",
            "text": "Claude AI usage limit reached|1760000000",
        },
    )
    rb = rebuilder(repo, fake)
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "gave_up" and len(rec["rounds"]) == 1
    rnd = rec["rounds"][0]
    assert rnd["error"]["kind"] == "usage_limit"
    assert rec["error"]["kind"] == "usage_limit"
    assert rnd["check"]["verdict"] == "fail"
    assert any(f["target"] == "agent" for f in rnd["check"]["findings"])
    cleanup(repo, rec)


def test_a_crashing_agent_is_an_error_round_and_the_loop_goes_on(repo: Path, fake: Path) -> None:
    plan(fake, {"exit": 3, "stderr": "boom"}, {"files": {"built.txt": "x\n"}})
    rb = rebuilder(repo, fake)
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "passed"
    assert rec["rounds"][0]["error"] == {"kind": "cli", "message": "boom"}
    cleanup(repo, rec)


def test_a_crash_after_the_agent_keeps_its_usage_and_marks_the_round(
    repo: Path, fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real run: the snapshot crashed after a 6.5 minute agent turn; usage showed $0.
    plan(fake, {"files": {"notes.txt": "half\n"}, "text": "Edited."})
    calls = {"n": 0}
    real = Rebuilder._tree

    def boom(self: Rebuilder, run: Any) -> str:
        calls["n"] += 1
        if calls["n"] >= 2:  # the base snapshot works; the post-agent one raises
            raise RuntimeError("git add failed: paths are ignored")
        return real(self, run)

    monkeypatch.setattr(Rebuilder, "_tree", boom)
    rb = rebuilder(repo, fake)
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "gave_up"
    assert rec["error"]["kind"] == "internal" and "paths are ignored" in rec["error"]["message"]
    assert rec["usage"]["cost_usd"] == 0.25 and rec["usage"]["output_tokens"] == 20
    assert rec["usage"]["duration_ms"] == 1000
    (rnd,) = rec["rounds"]
    assert rnd["error"] == rec["error"] and rnd["finished"] is not None
    assert rnd["agent"]["cost_usd"] == 0.25 and rnd["agent"]["session_id"] == SID
    assert rnd["files_changed"] == ["notes.txt"]
    assert "gates" not in rnd and rnd.get("check") is None  # no gate ran
    assert rb.build_of("c1")["state"] == "gave_up"
    cleanup(repo, rec)


def test_the_stub_agent_and_temp_worktrees(repo: Path, fake: Path) -> None:
    (repo / "stub-rebuild.json").write_text(
        json.dumps(
            {"rounds": [{"files": {"a.txt": "1"}}, {"files": {"built.txt": "x"}, "summary": "ok"}]}
        )
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "stub")
    rb = rebuilder(repo, fake, agent="stub", worktrees="temp")
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "passed" and rec["agent"] == "stub"
    assert [r["agent_summary"] for r in rec["rounds"]] == ["stub: wrote a.txt", "ok"]
    assert not Path(rec["worktree"]).is_relative_to(repo)
    assert log(fake) == [], "the stub never runs claude"
    cleanup(repo, rec)


# ---------------------------------------------------------------- pieces


def test_parse_gate_output() -> None:
    lattice_out = (
        "FAIL: 2 findings (D 2, X 0); 10 cases run\n\n"
        "rule (D: scenarios): scenario: accept_commits_once\n"
        "  [D fail] POST /api/changes/c1/accept (accept_commits_once step 3)\n"
        "    expected 200\n"
        "rule (D: scenarios): scenario: bad_input\n"
        "  [D fail] POST /api/changes (bad_input step 1)\n"
    )
    counts, fs = parse_gate_output("./check.sh", lattice_out, 1)
    assert counts == {"D": 2, "X": 0}
    assert [f["target"] for f in fs] == ["scenario:accept_commits_once", "scenario:bad_input"]
    counts, fs = parse_gate_output("pytest", "....\n✗ test_x failed\n", 1)
    assert counts == {"pytest": 1} and fs[0]["title"] == "✗ test_x failed"
    counts, fs = parse_gate_output("make", "error: nope\n", 2)
    assert fs == [{"title": "make exited 2: error: nope", "target": ""}]
    assert parse_gate_output("ok", "PASS: 0 findings (D 0)\n", 0) == ({"D": 0}, [])


def test_snapshot_ignores_gitignored_junk_dirs(tmp_path: Path) -> None:
    # A real rebuild died here: the agent ran pytest, `.pytest_cache` (gitignored) appeared at
    # the worktree root, and `git add -A -- . :(exclude).pytest_cache` exited 1.
    r = tmp_path / "r"
    r.mkdir()
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "init", "-q"], check=True)
    (r / ".gitignore").write_text(".pytest_cache\n__pycache__\n")
    (r / "a.py").write_text("x = 1\n")
    subprocess.run([*g, "add", "."], check=True)
    subprocess.run([*g, "commit", "-qm", "init"], check=True)
    for junk in (".pytest_cache", "__pycache__", "pkg/.pytest_cache", ".venv", ".lattice"):
        (r / junk).mkdir(parents=True)
        (r / junk / "f").write_text("junk\n")
    (r / "a.py").write_text("x = 2\n")
    (r / "b.py").write_text("y = 1\n")
    tree = rebuild_mod._snapshot_tree(r)
    files = subprocess.run(
        [*g, "ls-tree", "-r", "--name-only", tree], check=True, capture_output=True, text=True
    ).stdout.split()
    assert sorted(files) == [".gitignore", "a.py", "b.py"]


def test_capability(repo: Path, fake: Path, tmp_path: Path) -> None:
    claude = str(fake / "claude")
    assert capability(repo, {"gates": ["./gate.sh"]}, claude) == (True, "")
    assert capability(repo, {}, claude)[0] is False  # no gates, no check.sh
    (repo / "check.sh").write_text("#!/bin/sh\n")
    assert rebuild_mod.RebuildConfig(repo, {}).gates == ["./check.sh"]
    assert capability(repo, {"gates": ["./nope.sh"]}, claude)[0] is False
    assert capability(repo, {"gates": ["./gate.sh"]}, str(tmp_path / "none"))[0] is False
    assert capability(repo, {"gates": ["./gate.sh"], "agent": "stub"}, str(tmp_path / "none"))[0]
    plain = make_project(tmp_path / "plain", repo=False)
    assert capability(plain, {"gates": ["true"], "agent": "stub"})[0] is False  # no git


# ---------------------------------------------------------------- the routes


def _serve(srv: Any) -> str:
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture()
def hub(repo: Path, fake: Path, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    (repo / "stub-rebuild.json").write_text(
        json.dumps(
            {"rounds": [{"files": {"a.txt": "1"}, "hold": 1}, {"files": {"built.txt": "x"}}]}
        )
    )
    with (repo / "studio.toml").open("a") as f:
        f.write('\n[rebuild]\ngates = ["./gate.sh"]\nagent = "stub"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rebuild config")
    other = make_project(tmp_path / "other")
    srv = make_hub_server(discover([repo, other]), 0, generator_factory=lambda _m: StubGenerator())
    base = _serve(srv)
    yield base, repo
    srv.shutdown()
    srv.server_close()


def test_routes_under_the_project_prefix(hub) -> None:
    base, repo = hub
    p = base + "/p/notes/api"
    projects = request("GET", base + "/api/projects")[1]
    assert [(x["name"], x["capabilities"]["rebuild"]) for x in projects] == [
        ("notes", True),
        ("other", False),
    ]
    assert request("POST", p + "/rebuild", {"changes": ["c1"]}) == (404, {"detail": "no change c1"})
    assert request("POST", p + "/changes", {"request": "Notes can be starred."})[1] == {"id": "c1"}
    assert request("GET", p + "/changes/c1?wait=30")[1]["status"] == "ready"
    assert request("POST", p + "/rebuild", {"changes": ["c1"]}) == (
        409,
        {"detail": "change c1 is ready, not accepted"},
    )
    assert request("POST", p + "/changes/c1/accept")[0] == 200
    head = git(repo, "rev-parse", "HEAD")
    m = request("GET", p + "/model")[1]
    assert m["changes"][0]["build"] == {"state": "not_built"}
    for bad in (
        {},
        {"changes": []},
        {"changes": ["c1"], "max_rounds": 0},
        {"changes": ["c1"], "model": 3},
        {"changes": ["c1", "c1"]},
    ):
        assert request("POST", p + "/rebuild", bad)[0] == 422, bad
    assert request("POST", p + "/rebuild", {"changes": ["c1"]})[1] == {"id": "b1"}
    assert request("POST", p + "/rebuild", {"changes": ["c1"]}) == (
        409,
        {"detail": "change c1 is already rebuilding"},
    )
    assert request("GET", p + "/model")[1]["changes"][0]["build"]["state"] == "rebuilding"
    assert request("GET", p + "/rebuild/b1?wait=soon")[0] == 422
    rec = request("GET", p + "/rebuild/b1?wait=30")[1]
    assert rec["status"] == "passed" and len(rec["rounds"]) == 2
    assert request("GET", p + "/rebuild")[1][0]["id"] == "b1"
    m = request("GET", p + "/model")[1]
    assert m["changes"][0]["build"] == {
        "state": "built",
        "rebuild_id": "b1",
        "branch": "rebuild/c1",
        "commit": rec["commit"]["sha"],
    }
    assert m["project"]["git_head"] == head[:7], "the owner's HEAD does not move"
    assert request("DELETE", p + "/rebuild/b1") == (
        409,
        {"detail": "cannot stop a rebuild that is passed"},
    )
    assert request("GET", p + "/rebuild/b9")[0] == 404
    assert request("DELETE", p + "/rebuild/b9")[0] == 404
    assert request("GET", base + "/p/other/api/rebuild/b1")[0] == 404, "per project"
    cleanup(repo, rec)


def test_no_claude_and_no_stub_is_503(repo: Path, tmp_path: Path) -> None:
    rb = Rebuilder(
        repo,
        {"gates": ["./gate.sh"]},
        name="notes",
        model="sonnet",
        claude_bin=str(tmp_path / "no-claude"),
    )
    with pytest.raises(RebuildError) as e:
        rb.start([CHANGE])
    assert e.value.status == 503 and "claude" in e.value.detail


def test_the_mock_has_the_real_record_shape() -> None:
    """Mock parity (API v1.7): branch, base, worktree, merge_hint; capabilities.rebuild."""
    import threading

    from lattice.studio.mock_server import make_server as make_mock

    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert all(p["capabilities"]["rebuild"] for p in request("GET", base + "/api/projects")[1])
        assert request("POST", base + "/api/changes/c0/accept")[0] == 200
        rid = request("POST", base + "/api/rebuild", {"changes": ["c0"]})[1]["id"]
        rec = request("GET", f"{base}/api/rebuild/{rid}")[1]
        assert rec["branch"] == "rebuild/c0" and rec["merge_hint"] is None
        assert {"base", "worktree"} <= set(rec)
    finally:
        srv.shutdown()
        srv.server_close()
