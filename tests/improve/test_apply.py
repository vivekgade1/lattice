"""``lattice improve apply`` (docs/results/2026-10-15-improve-apply-prereg.md): one test per
probe row, on synthetic git repositories with cheap gates. A0 refusals, A1 the I-3 apply (the
blob ids of the dogfood and Streak ``studio.toml`` are the pre-registration's), A2 gates, A3
safety, A4 the edit's edge cases, A5 ``--discard``, A6 idempotence and determinism, A7 the loop
closes. The real dogfood and Streak clones are the probe script's (dogfood/improve_apply_probes)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest

from lattice.improve.__main__ import main
from tests.improve.test_verify import (
    BEFORE_TS,
    ROOT,
    C,
    entry,
    ev,
    i3_lines,
    run,
    timeouts_before,
    v2_events,
)

DOGFOOD = (ROOT / "dogfood/studio/studio.toml").read_bytes()
IMPROVE = ROOT / "lattice/improve"
ID = "imp-3e1cd7f9"
LINE = 'level_gate_args = ["--x-repeats", "2"]'
BASE = '[project]\nname = "p"\n\n[rebuild]\ngates = ["true"]\n'
MASK = ("applied_at", "seconds", "output_tail")


@pytest.fixture(autouse=True)
def _git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.startswith("GIT_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r


class Env:
    """A repository (`top`), a project directory in it and a state directory beside it."""

    def __init__(self, tmp: Path, top: Path, project: Path, state: Path) -> None:
        self.tmp, self.top, self.project, self.state = tmp, top, project, state

    @property
    def worktree(self) -> Path:
        return self.top.parent / f"{self.top.name}.improve" / ID

    @property
    def record(self) -> Path:
        return self.state / "applies" / f"{ID}.json"

    def apply(self, capsys, ident: str = ID, *extra: str) -> tuple[int, dict[str, Any], str]:
        rc, out, err = run(capsys, self.project, self.state, "apply", ident, "--json", *extra)
        return rc, json.loads(out) if out.strip().startswith("{") else {}, err

    def discard(self, capsys, ident: str = ID, *extra: str) -> tuple[int, dict[str, Any], str]:
        rc, out, err = run(capsys, self.project, self.state, "apply", "--discard", ident,
                           "--json", *extra)  # fmt: skip
        return rc, json.loads(out) if out.strip().startswith("{") else {}, err

    def branches(self) -> list[str]:
        return git(self.top, "for-each-ref", "--format=%(refname)").stdout.split()


def make_repo(
    tmp: Path,
    toml: str | bytes | None = BASE,
    *,
    rel: str = "",
    branch: str = "trunk",
    identity: bool = True,
    events: list[Any] | None = None,
    ledger: list[dict[str, Any]] | None = None,
    name: str = "repo",
    files: dict[str, str] | None = None,
) -> Env:
    top = tmp / name
    top.mkdir(parents=True)
    git(top, "init", "-q", "-b", branch)
    git(top, "config", "core.autocrlf", "false")
    if identity:
        git(top, "config", "user.name", "Owner")
        git(top, "config", "user.email", "owner@example.com")
    project = top / rel if rel else top
    project.mkdir(parents=True, exist_ok=True)
    if toml is not None:
        (project / "studio.toml").write_bytes(toml if isinstance(toml, bytes) else toml.encode())
    (top / "README").write_text("readme\n")
    for n, text in (files or {}).items():
        (top / n).write_text(text)
    git(top, "add", "-A")
    git(top, "-c", "user.name=Owner", "-c", "user.email=owner@example.com", "commit", "-qm", "init")
    state = tmp / "state"
    state.mkdir(exist_ok=True)
    lines = i3_lines()[:9] if events is None else events
    (state / "events.jsonl").write_text(
        "".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines)
    )
    if ledger is not None:
        (state / "improvements.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ledger))
    return Env(tmp, top, project, state)


def fs_hash(*roots: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in roots:
        for p in sorted(r.rglob("*")):
            if p.is_symlink():
                out[str(p)] = "link"
            elif p.is_dir():
                out[str(p)] = "<dir>"
            else:
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def owner_state(top: Path) -> dict[str, Any]:
    """Everything of the owner's checkout apply must leave byte-identical."""
    g = top / ".git"
    out: dict[str, Any] = {
        n: hashlib.sha256((g / n).read_bytes()).hexdigest() if (g / n).is_file() else None
        for n in ("index", "HEAD", "config", "packed-refs", "info/exclude")
    }
    refs = git(top, "for-each-ref", "--format=%(refname) %(objectname)").stdout.splitlines()
    out["refs"] = [ln for ln in refs if "/improve/" not in ln]
    out["stash"] = git(top, "stash", "list").stdout
    out["head"] = git(top, "rev-parse", "HEAD").stdout
    out["files"] = {
        str(p.relative_to(top)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(top.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(top).parts
    }
    return out


def masked(doc: dict[str, Any]) -> dict[str, Any]:
    d = json.loads(json.dumps(doc))
    d["applied_at"] = "T"
    sha = d["commit"]["sha"]
    for r in d["gates"]["results"]:
        r["seconds"], r["output_tail"] = 0, ""
    return json.loads(json.dumps(d).replace(sha, "<sha>"))


def gate_events(gate: str, mode: str | None = None, layers: dict | None = None) -> list[dict]:
    f: dict[str, Any] = {"gate": gate, "seconds": 100.0, "x_repeats": 5}
    if mode:
        f["mode"] = mode
    f["layer_seconds"] = layers or {"X": 100.0}
    return [ev("gate_run", ts, **f) for ts in BEFORE_TS]


def toml_after(env: Env, doc: dict[str, Any]) -> bytes:
    spec = f"{doc['commit']['sha']}:{doc['edit']['file']}"
    return subprocess.run(["git", "-C", str(env.top), "show", spec], capture_output=True).stdout


# ---------------------------------------------------------------- A0: refusals


def refused(capsys, env: Env, ident: str, rc_want: int, reason: str, *extra: str) -> dict[str, Any]:
    before = fs_hash(env.tmp)
    rc, doc, err = env.apply(capsys, ident, *extra)
    assert rc == rc_want == 3, (rc, err)
    assert doc["refused"] is True and doc["reason"] == reason and doc["command"] == "apply"
    assert err == f"lattice improve: refused: {doc['detail']}\n" and doc["format"] == 1
    assert fs_hash(env.tmp) == before  # nothing written, not even the state dir or a lock
    return doc


def test_a0a_unknown_id(tmp_path: Path, capsys) -> None:
    refused(capsys, make_repo(tmp_path), "imp-00000000", 3, "unknown_id")


def test_a0b_closed(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, events=i3_lines(), ledger=[entry()])
    d = refused(capsys, env, ID, 3, "closed")
    assert d["detail"] == "closed by I-01 at 2026-10-12T23:08:00Z"


def test_a0c_needs_owner(tmp_path: Path, capsys) -> None:
    d = refused(capsys, make_repo(tmp_path), "imp-56861ff8", 3, "needs_owner")
    assert "lattice/spec/check.py" in d["detail"]


def test_a0d_not_config(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    refused(capsys, env, "imp-7fad29b4", 3, "not_config")  # module
    refused(capsys, env, "imp-a8f0a344", 3, "not_config")  # prereg
    env18 = make_repo(tmp_path / "e18", events=i3_lines())
    refused(capsys, env18, ID, 3, "not_config")  # prereg: mixed x_repeats


def test_a0e_a_cause_knob_is_the_owners(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, events=timeouts_before())
    d = refused(capsys, env, "imp-e5092d05", 3, "not_config")
    assert "change_request" in d["detail"]


def test_a0f_a_second_apply_changes_nothing(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    rc, doc, _ = env.apply(capsys, ID, "--no-gates")
    assert rc == 0
    before = fs_hash(env.tmp)
    d = refused(capsys, env, ID, 3, "already_applied")
    assert fs_hash(env.tmp) == before and d["id"] == ID
    assert git(env.top, "rev-parse", "improve/" + ID).stdout.strip() == doc["commit"]["sha"]


def test_a0g_knob_pending_names_the_unmerged_change(tmp_path: Path, capsys) -> None:
    both = gate_events("level", "regression") + gate_events("level", "full")
    env = make_repo(tmp_path, events=both)
    assert env.apply(capsys, ID, "--no-gates")[0] == 0
    d = refused(capsys, env, "imp-50c7f46a", 3, "knob_pending")
    assert ID in d["detail"] and "level_gate_args" in d["detail"]
    # once merged into the base tip it is no longer pending (the knob is then already set)
    git(env.top, "merge", "-q", "--ff-only", "improve/" + ID)
    refused(capsys, env, "imp-50c7f46a", 3, "already_set")


def test_a0h_detached_head(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    git(env.top, "checkout", "-q", "--detach")
    refused(capsys, env, ID, 3, "detached_head")


def test_a0i_dirty_checkout_modified_and_staged(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    (env.top / "README").write_text("changed\n")
    refused(capsys, env, ID, 3, "dirty_checkout")
    git(env.top, "add", "README")
    refused(capsys, env, ID, 3, "dirty_checkout")


def test_a0j_untracked_files_are_ignored_and_untouched(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    (env.top / "private.txt").write_text("mine\n")
    (env.top / "app.db").write_bytes(b"\x00\x01")
    before = owner_state(env.top)
    rc, doc, _ = env.apply(capsys, ID, "--no-gates")
    assert rc == 0 and owner_state(env.top) == before
    assert (env.top / "private.txt").read_text() == "mine\n"


def test_a0k_studio_toml_untracked(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, None)
    (env.top / "studio.toml").write_text(BASE)
    refused(capsys, env, ID, 3, "studio_toml_untracked")


def test_a0k_studio_toml_differs_from_head(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    git(env.top, "update-index", "--skip-worktree", "studio.toml")
    (env.top / "studio.toml").write_text(BASE + "# local\n")
    refused(capsys, env, ID, 3, "studio_toml_differs")


def test_a0lmn_branch_and_worktree_refusals(tmp_path: Path, capsys) -> None:
    toml = BASE.replace('gates = ["true"]', 'gates = ["true"]\nprotected_branches = ["master"]')
    env = make_repo(tmp_path, toml)
    assert refused(capsys, env, ID, 3, "bad_branch", "--branch", "bad..name")["reason"]
    refused(capsys, env, ID, 3, "bad_branch", "--branch", "master")
    git(env.top, "branch", "main")
    refused(capsys, env, ID, 3, "branch_exists", "--branch", "main")
    git(env.top, "branch", "improve/" + ID)
    refused(capsys, env, ID, 3, "branch_exists")
    git(env.top, "branch", "-D", "improve/" + ID)
    env.worktree.mkdir(parents=True)
    refused(capsys, env, ID, 3, "worktree_exists")


def test_a0_default_protection_makes_main_a_bad_branch(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    git(env.top, "branch", "main")
    refused(capsys, env, ID, 3, "bad_branch", "--branch", "main")  # protected before existing


def test_a0o_no_repository(tmp_path: Path, capsys) -> None:
    proj, state = tmp_path / "plain", tmp_path / "state"
    proj.mkdir()
    state.mkdir()
    (proj / "studio.toml").write_text(BASE)
    (state / "events.jsonl").write_text("".join(x + "\n" for x in i3_lines()[:9]))
    before = fs_hash(tmp_path)
    rc, out, err = run(capsys, proj, state, "apply", ID)
    assert rc == 2 and "not a git repository" in err and out == ""
    assert fs_hash(tmp_path) == before


def test_a0p_no_commits(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    shutil.rmtree(env.top / ".git")
    git(env.top, "init", "-q", "-b", "trunk")
    before = fs_hash(env.tmp)
    rc, _, err = env.apply(capsys)
    assert rc == 2 and "no commit" in err and fs_hash(env.tmp) == before


def test_a0q_no_identity(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, identity=False)
    before = fs_hash(env.tmp)
    rc, _, err = env.apply(capsys)
    assert rc == 2 and "identity" in err and fs_hash(env.tmp) == before


def test_a0r_unparseable_studio_toml(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, "[rebuild\nbroken")
    before = fs_hash(env.tmp)
    rc, _, err = env.apply(capsys)
    assert rc == 2 and "studio.toml" in err and fs_hash(env.tmp) == before


def test_a0r_discard_takes_only_its_id(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    before = fs_hash(env.tmp)
    for extra in (["--no-gates"], ["--branch", "x"]):
        rc, _, err = env.discard(capsys, ID, *extra)
        assert rc == 2 and "--discard takes only" in err
    rc, _, err = run(capsys, env.project, env.state, "apply")
    assert rc == 2 and "needs a proposal ID" in err and fs_hash(env.tmp) == before


# ---------------------------------------------------------------- A1: the I-3 apply


def test_a1_the_i3_draft_on_the_dogfood_studio_toml(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, DOGFOOD, rel="dogfood/studio", branch="next-phases")
    rc, out, err = run(capsys, env.project, env.state, "draft", ID, "--json")
    draft_text = json.loads(out)["text"]
    assert git(env.top, "rev-parse", "HEAD:dogfood/studio/studio.toml").stdout.startswith("642c32d")
    base = git(env.top, "rev-parse", "HEAD").stdout.strip()
    rc, d, err = env.apply(capsys, ID, "--no-gates")
    assert rc == 0 and err == ""
    e = d["edit"]
    assert (e["action"], e["line_no"], e["file"]) == ("inserted", 22, "dogfood/studio/studio.toml")
    assert e["line"] == d["draft"]["toml"].split("\n")[1] == LINE and d["warnings"] == []
    assert d["draft"]["sha256"] == hashlib.sha256(draft_text.encode()).hexdigest()
    assert re.search(r"^index 642c32d\.\.3def11c 100644$", e["diff"], re.M)
    body = e["diff"].split("\n@@", 1)[1]
    assert "\n+" + LINE + "\n" in body and body.count("\n+") == 1 and body.count("\n-") == 0
    assert d["base"] == {"branch": "next-phases", "sha": base}
    assert d["branch"] == "improve/" + ID and d["worktree"] == str(env.worktree)
    assert git(env.top, "log", "--format=%H", f"{base}..improve/{ID}").stdout.split() == [
        d["commit"]["sha"]
    ]
    msg = git(env.top, "log", "-1", "--format=%B", d["commit"]["sha"]).stdout
    assert msg.rstrip("\n") + "\n" == d["commit"]["message"] and msg.startswith(
        f"improve: [rebuild] {LINE}\n\n"
        f"Proposal {ID} (change_set), signal gate:level_regression:X.\n"
        "Draft as of 2026-10-12T23:07:11Z; current: not set.\n"
        "Predicted: expected: X 119.5 s -> saving 71.7 s a gate (60%; x_repeats 5 -> 2)\n"
        "Applied by `lattice improve apply`; not merged. The owner reviews and merges.\n\n"
        f"Lattice-Improve: {ID}\n"
    )
    nxt = {s["step"]: s["command"] for s in d["next"]}
    assert list(nxt) == ["review", "merge", "close", "verify", "tidy", "drop"]
    assert f"--commit {d['commit']['sha']}" in nxt["close"] and f"--id {ID}" in nxt["close"]
    assert nxt["review"] == f"git -C {env.top} diff {base} improve/{ID}"
    assert d["gates"] == {"status": "skipped", "timeout_s": 900.0, "results": [],
                          "untracked_after": []}  # fmt: skip
    assert list(d) == ["format", "command", "proposal", "draft", "edit", "base", "branch",
                       "worktree", "commit", "applied_at", "gates", "integration_branch", "next",
                       "warnings"]  # fmt: skip
    assert json.loads(env.record.read_text()) == d  # the record is the --json output
    assert env.record.read_text().endswith("}\n")


def test_a1_text_output(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, DOGFOOD, rel="dogfood/studio")
    rc, out, err = run(capsys, env.project, env.state, "apply", ID, "--no-gates")
    assert rc == 0 and err == ""
    for label in (f"Lattice improve apply: {ID} (gate:level_regression:X)", "Edit: ", "Branch: ",
                  "Worktree: ", "Gates: skipped", "Record: ", "NOT MERGED: lattice never merges",
                  "  1. review: ", "  6. drop: "):  # fmt: skip
        assert label in out, label


def test_a1_the_message_without_an_expected_section(tmp_path: Path, capsys) -> None:
    ev_ = [ev("human", f"2026-10-01T0{i}:00:00Z", what="spec_sync") for i in range(3)]
    env = make_repo(tmp_path, events=ev_)
    ident = "imp-" + hashlib.sha1(b"human:spec_sync").hexdigest()[:8]
    rc, d, _ = env.apply(capsys, ident, "--no-gates")
    assert rc == 0 and d["edit"]["line"] == 'sync_spec = "auto"' and d["draft"]["expected"] is None
    assert (
        "Predicted: no prediction recorded (the draft has no Expected section)\n"
        in (d["commit"]["message"])
    )


def test_a1_integration_branch_is_a_warning_only(tmp_path: Path, capsys) -> None:
    toml = BASE.replace('gates = ["true"]', 'gates = ["true"]\nintegration_branch = "build/p"')
    env = make_repo(tmp_path, toml)
    rc, d, _ = env.apply(capsys, ID, "--no-gates")
    assert rc == 0 and d["base"]["branch"] == "trunk" and d["integration_branch"] == "build/p"
    assert d["warnings"] == [
        "[rebuild] integration_branch is build/p; this change is based on trunk, the branch "
        "checked out (Studio reads studio.toml there)"
    ]


def test_a1_a_project_below_the_repository_top_and_a_custom_branch(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, BASE.replace('gates = ["true"]', 'gates = ["true"]\nrepo = ".."'),
                    rel="sub")  # fmt: skip
    rc, d, _ = env.apply(capsys, ID, "--no-gates", "--branch", "feature/x")
    assert rc == 0 and d["branch"] == "feature/x" and d["edit"]["file"] == "sub/studio.toml"
    assert git(env.worktree, "branch", "--show-current").stdout.strip() == "feature/x"


# ---------------------------------------------------------------- A2: gates


def gated(tmp_path: Path, gates: str, extra: str = "") -> Env:
    return make_repo(tmp_path, f"[rebuild]\ngates = {gates}\n{extra}")


def test_a2a_passing_gates_run_from_the_worktree_root(tmp_path: Path, capsys) -> None:
    env = gated(tmp_path, '["pwd", "cat studio.toml"]')
    rc, d, _ = env.apply(capsys)
    assert rc == 0 and d["gates"]["status"] == "passed" and d["gates"]["untracked_after"] == []
    out = [r["output_tail"] for r in d["gates"]["results"]]
    assert out[0].strip() == str(env.worktree.resolve())
    assert LINE in out[1]  # the edited file, in the worktree
    for r in d["gates"]["results"]:
        assert r["passed"] is True and r["timed_out"] is False and 0 < r["seconds"] < 900
        assert list(r) == ["command", "exit_code", "passed", "timed_out", "seconds", "output_tail"]


def test_a2b_a_failed_gate_exits_1_and_keeps_everything(tmp_path: Path, capsys) -> None:
    env = gated(tmp_path, '["true", "false"]')
    rc, d, _ = env.apply(capsys)
    assert rc == 1 and d["gates"]["status"] == "failed"
    assert [r["exit_code"] for r in d["gates"]["results"]] == [0, 1]  # both ran
    assert env.worktree.is_dir() and "improve/" + ID in git(env.top, "branch").stdout
    assert json.loads(env.record.read_text())["gates"]["status"] == "failed"


def test_a2b_all_the_gates_run_after_a_failure(tmp_path: Path, capsys) -> None:
    env = gated(tmp_path, '["false", "echo second"]')
    rc, d, _ = env.apply(capsys)
    assert rc == 1 and [r["exit_code"] for r in d["gates"]["results"]] == [1, 0]


def test_a2c_a_timeout_terminates_the_gate(tmp_path: Path, capsys) -> None:
    env = gated(tmp_path, '["sleep 30"]', "gate_timeout = 2\nstop_grace = 1\n")
    t0 = time.monotonic()
    rc, d, _ = env.apply(capsys)
    r = d["gates"]["results"][0]
    assert rc == 1 and r["exit_code"] == 124 and r["timed_out"] is True and r["passed"] is False
    assert 2 <= r["seconds"] <= 9 and time.monotonic() - t0 < 20
    assert d["gates"]["timeout_s"] == 2.0 and d["gates"]["status"] == "failed"


def test_a2d_no_gates_configured(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, "[project]\nname = 'p'\n")
    rc, d, _ = env.apply(capsys)
    assert rc == 0 and d["gates"]["status"] == "none_configured" and d["gates"]["results"] == []
    assert "no gate ran: no [rebuild] gates and no check.sh in the project" in d["warnings"]


def test_a2d_check_sh_is_the_default_gate(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, "[project]\nname = 'p'\n")
    (env.top / "check.sh").write_text("#!/bin/sh\necho checked\n")
    (env.top / "check.sh").chmod(0o755)
    git(env.top, "add", "check.sh")
    git(env.top, "-c", "user.name=O", "-c", "user.email=o@e", "commit", "-qm", "check")
    rc, d, _ = env.apply(capsys)
    r = d["gates"]["results"][0]
    assert rc == 0 and r["command"] == "./check.sh" and r["output_tail"].strip() == "checked"


def test_a2e_no_gates_flag_skips_them(tmp_path: Path, capsys) -> None:
    env = gated(tmp_path, '["false"]')
    rc, d, _ = env.apply(capsys, ID, "--no-gates")
    assert rc == 0 and d["gates"]["status"] == "skipped" and d["gates"]["results"] == []


def _running(marker: str) -> list[str]:
    out = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if marker in ln and "ps -A" not in ln]


def test_a2f_sigterm_interrupts_and_leaves_no_process(tmp_path: Path) -> None:
    marker = f"sleep {100000 + os.getpid()}"  # unique: other runs may be sleeping too
    env = gated(tmp_path, json.dumps([marker]))
    proc = subprocess.Popen(
        [sys.executable, "-m", "lattice.improve", "apply", ID, "--json", "--project",
         str(env.project), "--state", str(env.state)],
        env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    deadline = time.monotonic() + 60
    while not _running(marker) and time.monotonic() < deadline:
        time.sleep(0.1)  # the gate is running
    assert _running(marker)
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 2 and "interrupted" in err
    rec = json.loads(env.record.read_text())
    assert rec["gates"]["status"] == "interrupted" and len(rec["gates"]["results"]) == 1
    assert json.loads(out)["gates"]["status"] == "interrupted"
    deadline = time.monotonic() + 10
    while _running(marker) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _running(marker) == []


def test_a2_gates_log_nothing(tmp_path: Path, capsys) -> None:
    env = gated(tmp_path, '["true", "false"]')
    before = fs_hash(env.state)
    env.apply(capsys)
    after = fs_hash(env.state)
    assert {k: v for k, v in after.items() if "applies" not in k} == before


# ---------------------------------------------------------------- A3: safety


def test_a3_the_owners_checkout_is_byte_identical(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, DOGFOOD, rel="dogfood/studio", branch="next-phases")
    git(env.top, "branch", "other")
    (env.top / "README").write_text("stashed\n")
    git(env.top, "-c", "user.name=O", "-c", "user.email=o@e", "stash", "push", "-q")
    (env.top / "private.txt").write_text("mine\n")
    (env.top / "dogfood/studio/.DS_Store").write_bytes(b"ds")
    before = owner_state(env.top)
    ledger_before = fs_hash(env.state)
    old_files = {p for p in (env.top / ".git").rglob("*") if p.is_file()}
    rc, d, _ = env.apply(capsys, ID, "--no-gates")
    assert rc == 0
    assert owner_state(env.top) == before
    assert fs_hash(env.state) == ledger_before | {
        str(env.state / "applies"): "<dir>",
        str(env.record): hashlib.sha256(env.record.read_bytes()).hexdigest(),
    }
    new = {p for p in (env.top / ".git").rglob("*") if p.is_file()} - old_files
    ok = ("refs/heads/improve", "logs/refs/heads/improve", f"worktrees/{ID}", "objects/")
    for p in new:
        rel = p.relative_to(env.top / ".git").as_posix()
        assert rel.startswith(ok), rel
    assert {p.name for p in env.tmp.iterdir()} == {"repo", "repo.improve", "state"}
    assert git(env.top, "status", "--porcelain").stdout.split("\n")[:-1] == [
        "?? dogfood/studio/.DS_Store",
        "?? private.txt",
    ]
    # the discard returns every hash to the state before the apply
    rc, _, _ = env.discard(capsys)
    assert rc == 0 and owner_state(env.top) == before


def test_a3_the_default_state_dir_only_adds_the_exclude_line(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, DOGFOOD, rel="dogfood/studio")
    default = env.top / ".lattice/state/studio"
    default.mkdir(parents=True)
    (default / "events.jsonl").write_text("".join(x + "\n" for x in i3_lines()[:9]))
    before = owner_state(env.top)
    rc = main(["apply", ID, "--no-gates", "--project", str(env.project)])
    assert rc == 0
    after = owner_state(env.top)
    new_files = set(after["files"]) - set(before["files"])
    assert new_files == {f".lattice/state/studio/applies/{ID}.json"}  # the record, git-ignored
    after["files"] = {k: v for k, v in after["files"].items() if k in before["files"]}
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"info/exclude"}
    assert "/.lattice/state/" in (env.top / ".git/info/exclude").read_text().splitlines()
    assert (env.top / ".lattice/state/studio/applies" / f"{ID}.json").is_file()
    capsys.readouterr()


def test_a3_a_git_shim_sees_no_merge_push_reset(tmp_path: Path, capsys, monkeypatch) -> None:
    log = tmp_path / "git.log"
    shim = tmp_path / "bin"
    shim.mkdir()
    real = shutil.which("git")
    (shim / "git").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec {real} "$@"\n')
    (shim / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    env = make_repo(tmp_path / "w", '[rebuild]\ngates = ["true"]\n')
    log.write_text("")
    assert env.apply(capsys)[0] == 0 and env.discard(capsys)[0] == 0
    seen = {tok for ln in log.read_text().splitlines() for tok in shlex.split(ln)}
    assert log.read_text().count("\n") > 10
    assert not seen & {"merge", "push", "update-ref", "reset", "checkout", "stash", "pull", "fetch"}


def test_a3_static_imports_and_literals() -> None:
    src = (IMPROVE / "apply.py").read_text()
    tree = ast.parse(src)
    top_level: list[str] = []
    for n in tree.body:
        if isinstance(n, ast.Import):
            top_level += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            top_level.append(n.module or "")
    for m in top_level:
        root = m.split(".")[0]
        assert root in sys.stdlib_module_names or m.startswith("lattice.improve"), m
    lazy = {
        n.module
        for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
        for n in ast.walk(f) if isinstance(n, ast.ImportFrom)
    }  # fmt: skip
    assert lazy <= {"lattice.studio.rebuild", "lattice.studio.state"}, lazy
    for word in ("claude", "anthropic", "lattice.llm"):
        assert word not in src, word
    for p in IMPROVE.glob("*.py"):
        for lit in ("3e1cd7f9", "71.7", "ce7f02c"):
            assert lit not in p.read_text(), (p.name, lit)
    for ln in src.splitlines():  # subprocesses: git and /bin/sh only
        assert "os.system" not in ln and "shell=True" not in ln


def test_a3_help_startup_does_not_import_the_studio() -> None:
    code = "import sys, lattice.cli; assert 'lattice.studio.rebuild' not in sys.modules"
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(ROOT)},
                       capture_output=True, text=True)  # fmt: skip
    assert r.returncode == 0, r.stderr
    code = "import sys, lattice.improve.apply; assert 'lattice.studio.rebuild' not in sys.modules"
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(ROOT)},
                       capture_output=True, text=True)  # fmt: skip
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- A4: the edit's edge cases


def edit_case(tmp_path: Path, capsys, toml: str | bytes | None, events=None, ident=ID):
    env = make_repo(tmp_path, toml, events=events)
    rc, d, err = env.apply(capsys, ident, "--no-gates")
    return env, rc, d, err


def only_change(env: Env, d: dict[str, Any], old: bytes, key: str, value: Any) -> None:
    """The new parse = the old one plus the key, and every other byte is the same."""
    new = toml_after(env, d)
    want = tomllib.loads(old.decode()) if old else {}
    want.setdefault("rebuild", {})[key] = value
    assert tomllib.loads(new.decode()) == want
    assert (env.worktree / "studio.toml").read_bytes() == new


def test_a4a_a_present_key_is_replaced_keeping_its_comment(tmp_path: Path, capsys) -> None:
    toml = (
        '[rebuild]\ngates = ["true"]\nlevel_gate_args = ["--x-repeats", "3"]  # tuned\nmore = 1\n'
    )
    env, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 0 and d["edit"]["action"] == "replaced" and d["edit"]["line_no"] == 3
    assert toml_after(env, d).decode() == toml.replace('"3"]', '"2"]')
    assert d["edit"]["old"] == ["--x-repeats", "3"] and d["edit"]["new"] == ["--x-repeats", "2"]
    assert d["draft"]["current"].startswith("current: level_gate_args = ") and (
        "(replaced by the line above)" in d["draft"]["current"]
    )


def test_a4b_insert_after_a_commented_header(tmp_path: Path, capsys) -> None:
    toml = '[project]\nname = "p"\n\n[rebuild]   # build\n# the gates\ngates = ["true"]\n'
    env, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 0 and d["edit"]["action"] == "inserted" and d["edit"]["line_no"] == 5
    assert toml_after(env, d).decode() == toml.replace("# build\n", f"# build\n{LINE}\n")


def test_a4c_a_key_in_another_table_is_untouched(tmp_path: Path, capsys) -> None:
    toml = '[check]\nlevel_gate_args = ["--layers", "D"]\n\n[rebuild]\ngates = ["true"]\n'
    env, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 0 and d["edit"]["action"] == "inserted"
    assert d["warnings"] == [
        "level_gate_args is also set in [check]; the rebuild loop reads [rebuild] only"
    ]
    new = toml_after(env, d).decode()
    assert new.startswith('[check]\nlevel_gate_args = ["--layers", "D"]\n')
    assert tomllib.loads(new)["check"] == {"level_gate_args": ["--layers", "D"]}


def test_a4d_no_rebuild_table_appends_one(tmp_path: Path, capsys) -> None:
    toml = '[project]\nname = "p"\n'
    env, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 0 and d["edit"]["action"] == "created_table" and d["edit"]["line_no"] == 5
    assert toml_after(env, d).decode() == toml + f"\n[rebuild]\n{LINE}\n"
    only_change(env, d, toml.encode(), "level_gate_args", ["--x-repeats", "2"])


def test_a4e_no_studio_toml_creates_it(tmp_path: Path, capsys) -> None:
    env, rc, d, _ = edit_case(tmp_path, capsys, None)
    assert rc == 0 and d["edit"]["action"] == "created_file" and d["edit"]["line_no"] == 2
    assert toml_after(env, d).decode() == f"[rebuild]\n{LINE}\n"
    assert git(env.top, "show", "HEAD:studio.toml", check=False).returncode != 0


def test_a4f_node_gate_extends_the_scoped_list(tmp_path: Path, capsys) -> None:
    toml = '[rebuild]\nscoped_gate_args = ["--layers", "CD"]  # node\n'
    ident = "imp-" + hashlib.sha1(b"gate:node:X").hexdigest()[:8]
    env, rc, d, _ = edit_case(tmp_path, capsys, toml, gate_events("node"), ident)
    assert rc == 0 and d["edit"]["action"] == "replaced" and d["edit"]["op"] == "+="
    assert toml_after(env, d).decode() == (
        '[rebuild]\nscoped_gate_args = ["--layers", "CD", "--x-repeats", "1"]  # node\n'
    )
    assert d["edit"]["old"] == ["--layers", "CD"]
    assert d["edit"]["new"] == ["--layers", "CD", "--x-repeats", "1"]


def test_a4g_spec_sync_is_a_string_literal(tmp_path: Path, capsys) -> None:
    ident = "imp-" + hashlib.sha1(b"human:spec_sync").hexdigest()[:8]
    evs = [ev("human", f"2026-10-01T0{i}:00:00Z", what="spec_sync") for i in range(3)]
    env, rc, d, _ = edit_case(tmp_path, capsys, BASE, evs, ident)
    assert rc == 0 and d["edit"]["action"] == "inserted" and d["edit"]["new"] == "auto"
    assert toml_after(env, d).decode() == BASE.replace(
        "[rebuild]\n", '[rebuild]\nsync_spec = "auto"\n'
    )


def test_a4h_already_set(tmp_path: Path, capsys) -> None:
    toml = '[rebuild]\nlevel_gate_args = ["--x-repeats", "2"]\n'
    _, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 3 and d["reason"] == "already_set"
    ident = "imp-" + hashlib.sha1(b"gate:node:X").hexdigest()[:8]
    toml = '[rebuild]\nscoped_gate_args = ["--layers", "CD", "--x-repeats", "1", "--z"]\n'
    _, rc, d, _ = edit_case(tmp_path / "n", capsys, toml, gate_events("node"), ident)
    assert rc == 3 and d["reason"] == "already_set"
    # the items apart (not a contiguous run) are not "set"
    toml = '[rebuild]\nscoped_gate_args = ["--x-repeats", "--layers", "1"]\n'
    _, rc, d, _ = edit_case(tmp_path / "m", capsys, toml, gate_events("node"), ident)
    assert rc == 0


def test_a4i_crlf_file(tmp_path: Path, capsys) -> None:
    toml = b'[project]\r\nname = "p"\r\n\r\n[rebuild]\r\ngates = ["true"]\r\n'
    env, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 0 and d["edit"]["action"] == "inserted"
    assert toml_after(env, d) == toml.replace(b"[rebuild]\r\n", b"[rebuild]\r\n" + LINE.encode()
                                              + b"\r\n")  # fmt: skip
    assert "committed bytes differ" not in " ".join(d["warnings"])
    only_change(env, d, toml, "level_gate_args", ["--x-repeats", "2"])


def test_a4j_no_final_newline_stays_missing(tmp_path: Path, capsys) -> None:
    toml = '[rebuild]\ngates = ["true"]\n\n[other]\nx = 1'
    env, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 0
    new = toml_after(env, d)
    assert not new.endswith(b"\n") and new.decode() == toml.replace(
        "[rebuild]\n", f"[rebuild]\n{LINE}\n"
    )


def test_a4j_a_header_on_the_last_line_without_an_ending(tmp_path: Path, capsys) -> None:
    env, rc, d, _ = edit_case(tmp_path, capsys, "[project]\nname = 'p'\n[rebuild]")
    assert rc == 0 and tomllib.loads(toml_after(env, d).decode())["rebuild"] == {
        "level_gate_args": ["--x-repeats", "2"]
    }


def test_a4k_a_multi_line_value_is_not_edited(tmp_path: Path, capsys) -> None:
    toml = '[rebuild]\nlevel_gate_args = [\n "--x-repeats", "3",\n]\n'
    _, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 3 and d["reason"] == "unsupported_layout"


def test_a4l_an_inline_table_is_not_edited(tmp_path: Path, capsys) -> None:
    _, rc, d, _ = edit_case(tmp_path, capsys, 'rebuild = { gates = ["true"] }\n')
    assert rc == 3 and d["reason"] == "unsupported_layout"
    _, rc, d, _ = edit_case(tmp_path / "x", capsys, "[rebuild.sub]\na = 1\n")
    assert rc == 3 and d["reason"] == "unsupported_layout"


def test_a4m_a_header_inside_a_string_fails_the_parse_check(tmp_path: Path, capsys) -> None:
    toml = 'note = """\n[rebuild]\n"""\n'
    env, rc, d, _ = edit_case(tmp_path, capsys, toml)
    assert rc == 3 and d["reason"] == "edit_check_failed"
    assert "improve/" + ID not in git(env.top, "branch").stdout  # nothing was created
    assert not env.worktree.parent.exists()


# ---------------------------------------------------------------- A5: discard


def test_a5a_discard_removes_what_apply_made(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    before = owner_state(env.top)
    rc, d, _ = env.apply(capsys, ID, "--no-gates")
    rec_bytes = env.record.read_bytes()
    rc, g, err = env.discard(capsys)
    assert rc == 0 and err == "" and g["command"] == "discard" and g["removed"]["branch"] is True
    assert not env.worktree.exists() and "improve/" + ID not in git(env.top, "branch").stdout
    assert git(env.top, "worktree", "list").stdout.count("\n") == 1
    kept = env.state / "applies" / f"{ID}.discarded-1.json"
    assert kept.read_bytes() == rec_bytes and not env.record.exists()
    assert owner_state(env.top) == before and env.worktree.parent.is_dir()


def test_a5a_text_output(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    env.apply(capsys, ID, "--no-gates")
    rc, out, _ = run(capsys, env.project, env.state, "apply", "--discard", ID)
    assert rc == 0 and out.startswith(f"Lattice improve apply --discard: {ID}\n")
    assert "Removed: branch improve/" in out and "discarded-1.json" in out


def test_a5b_not_applied(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    before = fs_hash(env.tmp)
    rc, d, err = env.discard(capsys)
    assert rc == 3 and d["reason"] == "not_applied" and d["command"] == "discard"
    assert fs_hash(env.tmp) == before


def test_a5c_branch_moved(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    env.apply(capsys, ID, "--no-gates")
    (env.worktree / "extra.txt").write_text("x\n")
    git(env.worktree, "add", "extra.txt")
    git(env.worktree, "-c", "user.name=O", "-c", "user.email=o@e", "commit", "-qm", "more")
    before = fs_hash(env.tmp)
    rc, d, _ = env.discard(capsys)
    assert rc == 3 and d["reason"] == "branch_moved" and fs_hash(env.tmp) == before


def test_a5d_merged(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    rc, doc, _ = env.apply(capsys, ID, "--no-gates")
    git(env.top, "branch", "keep", doc["commit"]["sha"])
    before = fs_hash(env.tmp)
    rc, d, _ = env.discard(capsys)
    assert rc == 3 and d["reason"] == "merged" and "keep" in d["detail"]
    assert fs_hash(env.tmp) == before and env.worktree.is_dir()


def test_a5e_worktree_dirty(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    env.apply(capsys, ID, "--no-gates")
    (env.worktree / "studio.toml").write_text("# edited\n")
    rc, d, _ = env.discard(capsys)
    assert rc == 3 and d["reason"] == "worktree_dirty" and env.worktree.is_dir()
    git(env.worktree, "checkout", "--", "studio.toml")
    (env.worktree / "stray.txt").write_text("x\n")  # an untracked file the gates did not leave
    rc, d, _ = env.discard(capsys)
    assert rc == 3 and d["reason"] == "worktree_dirty"
    (env.worktree / "stray.txt").unlink()
    assert env.discard(capsys)[0] == 0


def test_a5_a_missing_worktree_dir_still_drops_the_branch(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path)
    env.apply(capsys, ID, "--no-gates")
    shutil.rmtree(env.worktree)
    rc, d, _ = env.discard(capsys)
    assert rc == 0 and d["removed"]["worktree"] is False
    assert "improve/" + ID not in git(env.top, "branch").stdout


def test_a5_untracked_files_the_gates_left_do_not_block(tmp_path: Path, capsys) -> None:
    env = gated(tmp_path, '["echo hi > left.txt"]')
    rc, d, _ = env.apply(capsys)
    assert rc == 0 and d["gates"]["untracked_after"] == ["left.txt"]
    assert env.discard(capsys)[0] == 0


# ---------------------------------------------------------------- A6: idempotence, determinism


def test_a6b_apply_discard_apply_is_the_same_change(tmp_path: Path, capsys) -> None:
    env = make_repo(tmp_path, DOGFOOD, rel="dogfood/studio")
    rc, a, _ = env.apply(capsys, ID, "--no-gates")
    assert env.discard(capsys)[0] == 0
    rc, b, _ = env.apply(capsys, ID, "--no-gates")
    assert rc == 0 and a["commit"]["tree"] == b["commit"]["tree"]
    assert (
        a["edit"]["diff"] == b["edit"]["diff"] and a["commit"]["message"] == b["commit"]["message"]
    )
    assert masked(a) == masked(b)


def test_a6c_ten_fresh_repositories_give_one_record(tmp_path: Path, capsys) -> None:
    recs = []
    for i in range(10):
        env = make_repo(tmp_path / str(i), DOGFOOD, rel="dogfood/studio", name="repo")
        rc, d, _ = env.apply(capsys, ID, "--no-gates")
        assert rc == 0
        d = json.loads(json.dumps(d).replace(str(env.tmp), "<t>"))
        d["base"]["sha"] = d["commit"]["sha"] = d["commit"]["tree"] = "<x>"
        d["next"] = [s["step"] for s in d["next"]]
        recs.append(masked_text(d))
    assert len(set(recs)) == 1


def masked_text(d: dict[str, Any]) -> str:
    d = json.loads(json.dumps(d))
    d["applied_at"] = "T"
    d["edit"]["diff"] = re.sub(r"^index .*$", "index", d["edit"]["diff"], flags=re.M)
    return json.dumps(d, sort_keys=True)


def test_a6_the_blob_and_tree_are_functions_of_the_input(tmp_path: Path, capsys) -> None:
    trees = set()
    for i in range(3):
        env = make_repo(tmp_path / str(i), DOGFOOD, rel="dogfood/studio")
        rc, d, _ = env.apply(capsys, ID, "--no-gates")
        trees.add(d["commit"]["tree"])
        assert git(
            env.top, "rev-parse", f"improve/{ID}:dogfood/studio/studio.toml"
        ).stdout.startswith("3def11c")
    assert len(trees) == 1  # no timestamp or path in the tree


# ---------------------------------------------------------------- A7: the loop closes


def a7(tmp_path: Path, capsys, after: tuple[float, ...]):
    evs = v2_events(after=after)
    env = make_repo(tmp_path, BASE, events=evs[:3])
    rc, d, _ = env.apply(capsys, ID, "--no-gates")
    assert rc == 0 and d["draft"]["expected"] == (
        "expected: X 100.0 s -> saving 60.0 s a gate (60%; x_repeats 5 -> 2)"
    )
    git(env.top, "merge", "-q", "--ff-only", "improve/" + ID)  # the owner merges
    close = {s["step"]: s["command"] for s in d["next"]}["close"]
    argv = shlex.split(close)
    assert argv[:2] == ["lattice", "improve"]
    assert main(argv[2:]) == 0  # next.close, verbatim
    with (env.state / "events.jsonl").open("a") as f:
        f.writelines(json.dumps(e) + "\n" for e in evs[3:])
    capsys.readouterr()
    rc, out, _ = run(capsys, env.project, env.state, "verify", ID, "--at", C, "--json")
    return env, d, rc, json.loads(out)


def test_a7a_improved(tmp_path: Path, capsys) -> None:
    env, d, rc, v = a7(tmp_path, capsys, (40, 41, 39))
    assert rc == 0 and v["verdict"] == "improved"
    assert (v["before"]["cost_per"], v["after"]["cost_per"]) == (100.0, 40.0)
    assert v["change"]["delta"] == -60.0 and v["change"]["relative"] == -0.6
    assert v["predicted"]["delta"] == -60.0 and v["error"] == 0.0
    assert v["entry"]["commit"] == d["commit"]["sha"]
    # the apply wrote no ledger line: the only entry is the owner's close
    lines = (env.state / "improvements.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["status"] == "closed"


def test_a7b_not_improved(tmp_path: Path, capsys) -> None:
    _, _, rc, v = a7(tmp_path, capsys, (100, 99, 101))
    assert rc == 1 and v["verdict"] == "not_improved" and v["error"] == 1.0


def test_a7c_apply_again_is_closed(tmp_path: Path, capsys) -> None:
    env, _, _, _ = a7(tmp_path, capsys, (40, 41, 39))
    rc, d, _ = env.apply(capsys, ID)
    assert rc == 3 and d["reason"] == "closed" and d["detail"].startswith(f"closed by {ID} at ")


def test_a7d_discard_after_merge(tmp_path: Path, capsys) -> None:
    env, _, _, _ = a7(tmp_path, capsys, (40, 41, 39))
    rc, d, _ = env.discard(capsys)
    assert rc == 3 and d["reason"] == "merged"


# ---------------------------------------------------------------- the older commands


def test_a8_the_help_lists_apply_and_the_older_commands_keep_theirs(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "apply" in out and "draft" in out
    with pytest.raises(SystemExit):
        main(["apply", "--help"])
    h = capsys.readouterr().out
    for flag in ("--discard", "--branch", "--no-gates", "--project", "--state", "--json"):
        assert flag in h
