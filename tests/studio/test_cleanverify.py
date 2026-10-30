"""Clean-checkout verification (lattice/studio/cleanverify.py): the protected set hashed at a
base commit and at a candidate, a clean ``git archive`` tree of the candidate with the base's
protected files laid over it, and the gate verdict made there. Temp git repositories only; no
model, no Docker, no network."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from lattice.studio import cleanverify as cv

GOOD, BAD = "GOOD\n", "BAD\n"
# the gate: the app must say GOOD, and nothing the agent left around may be in the tree
CHECK = """#!/bin/sh
grep -q GOOD app.py || { echo "app.py is not GOOD"; exit 1; }
test ! -e leaked.txt || { echo "leaked.txt is in the tree"; exit 1; }
test ! -e junk.log || { echo "junk.log is in the tree"; exit 1; }
exit 0
"""
TAMPERED_CHECK = "#!/bin/sh\necho tampered; exit 0\n"
TRACE = json.dumps({"source": "REQUIREMENTS.md", "links": {}})
TOML = '[mcp]\nprotected = ["tests/harness", "extra.cfg"]\n\n[grpc]\nprotos = ["api/shelf.proto"]\n'


def git(root: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def put(root: Path, rel: str, text: str, *, x: bool = False) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(0o755 if x else 0o644)


def commit(root: Path, msg: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", msg)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A project at the repo root: spec files, studio.toml, check.sh, a harness directory, a
    proto, and an app that is BAD. Returns (repo, base commit)."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    put(root, "REQUIREMENTS.md", "# reqs\n")
    put(root, "spec.py", "RULES = []\n")
    put(root, "spec.trace.json", TRACE)
    put(root, "studio.toml", TOML)
    put(root, "check.sh", CHECK, x=True)
    put(root, "tests/harness/run.py", "print('harness')\n")
    put(root, "tests/harness/data/a.json", "{}\n")
    put(root, "api/shelf.proto", "syntax = 'proto3';\n")
    put(root, "extra.cfg", "k=v\n")
    put(root, "app.py", BAD)
    put(root, ".gitignore", "junk.log\n__pycache__/\n")
    return root, commit(root, "base")


def tip_with(root: Path, base: str, edits: dict[str, str | None]) -> str:
    """A commit on top of `base` with `edits` (None deletes), the working tree left at it."""
    git(root, "checkout", "-q", "--detach", base)
    for rel, text in edits.items():
        if text is None:
            (root / rel).unlink()
        else:
            put(root, rel, text, x=rel.endswith(".sh"))
    return commit(root, "tip")


# ---- the protected set --------------------------------------------------------------------


def test_protected_set_is_read_from_the_base(repo):
    root, base = repo
    names = cv.protected_paths(root, base)
    for want in (
        "REQUIREMENTS.md", "spec.py", "spec.trace.json", "studio.toml", "check.sh",
        "tests/harness", "extra.cfg", "api/shelf.proto",
        "REQUIREMENTS.rationale.json", "REQUIREMENTS.glossary.json",
    ):  # fmt: skip
        assert want in names
    assert "REQUIREMENTS.rationale.proposed.json" not in names  # the drafts are free
    # a tip that drops the [mcp] names from studio.toml does not shrink the set
    tip = tip_with(root, base, {"studio.toml": "", "tests/harness/run.py": "print('evil')\n"})
    assert "tests/harness" in cv.protected_paths(root, base)
    d = cv.diff_protected(root, base, tip)
    assert d.changed == ("studio.toml", "tests/harness/run.py")


def test_gate_scripts_are_protected(repo):
    root, base = repo
    put(root, "scripts/gate.sh", "#!/bin/sh\nexit 0\n", x=True)
    base = commit(root, "gate script")
    for gate in ("./scripts/gate.sh", "FOO=1 scripts/gate.sh", "bash scripts/gate.sh"):
        assert "scripts/gate.sh" in cv.protected_paths(root, base, gates=[gate])
    assert "scripts/gate.sh" not in cv.protected_paths(root, base, gates=["pytest -q"])
    assert "scripts/new.sh" not in cv.protected_paths(root, base, gates=["./scripts/new.sh"])
    assert "scripts/gate.sh" not in cv.protected_paths(root, base)


def test_extra_and_escaping_names(repo):
    root, base = repo
    names = cv.protected_paths(root, base, extra=["conftest.py", "../outside", "/abs", "a/./b/"])
    assert "conftest.py" in names and "a/b" in names
    assert "../outside" not in names and "/abs" not in names


def test_subdirectory_project(tmp_path: Path):
    root = tmp_path / "mono"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    put(root, "proj/REQUIREMENTS.md", "# r\n")
    put(root, "proj/spec.py", "x = 1\n")
    put(root, "proj/spec.trace.json", TRACE)
    put(root, "proj/studio.toml", '[mcp]\nprotected = ["h.py"]\n')
    put(root, "proj/h.py", "1\n")
    put(root, "proj/app.py", BAD)
    base = commit(root, "base")
    names = cv.protected_paths(root, base, project_rel="proj")
    assert "proj/h.py" in names and "proj/spec.py" in names and "spec.py" not in names
    tip = tip_with(root, base, {"proj/h.py": "2\n"})
    assert cv.diff_protected(root, base, tip, project_rel="proj").changed == ("proj/h.py",)


# ---- the protected diff -------------------------------------------------------------------


def test_untouched_protected_set_is_clean(repo):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD, "newmodule.py": "x = 1\n"})
    d = cv.diff_protected(root, base, tip)
    assert d.clean and d.edited == []
    assert d.checked >= 8  # the files of the set were hashed, not an empty comparison
    # the same answer from a directory holding the tip
    with cv.clean_tree(root, tip) as tree:
        assert cv.diff_protected(root, base, root=tree).clean


def test_tampered_check_script_is_detected(repo):
    root, base = repo
    tip = tip_with(root, base, {"check.sh": TAMPERED_CHECK})
    d = cv.diff_protected(root, base, tip)
    assert d.changed == ("check.sh",) and not d.clean
    assert d.deleted == () and d.added == ()


def test_mode_change_is_a_change(repo):
    root, base = repo
    git(root, "checkout", "-q", "--detach", base)
    (root / "check.sh").chmod(0o644)
    tip = commit(root, "chmod")
    assert cv.diff_protected(root, base, tip).changed == ("check.sh",)


def test_deleted_and_added_protected_files(repo):
    root, base = repo
    tip = tip_with(
        root,
        base,
        {
            "spec.py": None,  # deleted
            "tests/harness/data/a.json": None,  # deleted, inside a protected directory
            "tests/harness/sneaky.py": "pass\n",  # added inside a protected directory
            "REQUIREMENTS.glossary.json": "{}\n",  # a protected name the base never had
            "app.py": GOOD,  # not protected
        },
    )
    d = cv.diff_protected(root, base, tip)
    assert d.deleted == ("spec.py", "tests/harness/data/a.json")
    assert d.added == ("REQUIREMENTS.glossary.json", "tests/harness/sneaky.py")
    assert d.changed == ()
    assert not d.clean and "app.py" not in d.edited
    assert json.loads(json.dumps(d.as_dict()))["deleted"] == list(d.deleted)


def test_directory_candidate_agrees_with_commit_candidate(repo):
    root, base = repo
    tip = tip_with(
        root,
        base,
        {"spec.py": None, "tests/harness/sneaky.py": "pass\n", "check.sh": "#!/bin/sh\n"},
    )
    with cv.clean_tree(root, tip) as tree:
        assert cv.diff_protected(root, base, root=tree) == cv.diff_protected(root, base, tip)


def test_bad_refs_are_errors_not_verdicts(repo):
    root, base = repo
    with pytest.raises(cv.CleanVerifyError):
        cv.diff_protected(root, base, "no-such-ref")
    with pytest.raises(cv.CleanVerifyError):
        cv.verify(root, "no-such-ref", base, "true")
    with pytest.raises(ValueError):
        cv.diff_protected(root, base)


# ---- the clean tree -----------------------------------------------------------------------


def test_untracked_and_ignored_files_do_not_leak(repo):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD})
    (root / "leaked.txt").write_text("agent scratch\n")  # untracked
    (root / "junk.log").write_text("ignored\n")  # git-ignored
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert (root / "leaked.txt").exists()
    for mode in ("archive", "worktree"):
        with cv.clean_tree(root, tip, mode=mode) as tree:
            assert not (tree / "leaked.txt").exists()
            assert not (tree / "junk.log").exists()
            assert not (tree / "__pycache__").exists()
            assert (tree / "app.py").read_text() == GOOD
            assert ((tree / "check.sh").stat().st_mode & 0o100) != 0  # the exec bit survives
    # the gate itself (which fails on either file) passes on the clean tree
    v = cv.verify(root, base, tip, "./check.sh")
    assert v.passed and v.gate_passed and v.exit_code == 0, v.output_tail


def test_archive_is_reproducible(repo, tmp_path: Path):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD})
    other = tip_with(root, base, {"app.py": GOOD + "# more\n"})
    digests = []
    for _ in range(2):
        with cv.clean_tree(root, tip) as tree:
            digests.append(cv.dir_digest(tree))
    with cv.clean_tree(root, tip, mode="worktree") as tree:  # a worktree of it agrees too
        digests.append(cv.dir_digest(tree))
    with cv.clean_tree(root, other) as tree:
        different = cv.dir_digest(tree)
    assert digests[0] == digests[1] == digests[2]
    assert different != digests[0]
    v1, v2 = (cv.verify(root, base, tip, "true") for _ in range(2))
    assert v1.tree_digest == v2.tree_digest != ""
    # the tree is the commit's tree: same files as git ls-tree says
    listed = set(git(root, "ls-tree", "-r", "--name-only", tip).splitlines())
    with cv.clean_tree(root, tip) as tree:
        on_disk = {p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()}
    assert on_disk == listed


def test_temp_dirs_are_removed_on_exception(repo, tmp_path: Path):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD})
    scratch = tmp_path / "scratch"
    seen: list[Path] = []
    for mode in ("archive", "worktree"):
        with (
            pytest.raises(RuntimeError, match="boom"),
            cv.clean_tree(root, tip, mode=mode, parent=scratch) as tree,
        ):
            seen.append(tree)
            assert tree.is_dir()
            raise RuntimeError("boom")
    assert not seen[0].exists() and not seen[1].exists()
    assert list(scratch.iterdir()) == []
    assert "lattice-cleanverify" not in git(root, "worktree", "list")
    assert len(git(root, "worktree", "list").splitlines()) == 1

    # an error inside verify() (the overlay failing) leaves nothing behind either
    def boom(*a, **k):
        raise RuntimeError("overlay exploded")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cv, "overlay_protected", boom)
        with pytest.raises(RuntimeError, match="overlay exploded"):
            cv.verify(root, base, tip, "true", parent=scratch)
    # and a gate that fails or times out
    assert not cv.verify(root, base, tip, "exit 3", parent=scratch).gate_passed
    assert list(scratch.iterdir()) == []


def test_a_bad_ref_leaves_nothing_behind(repo, tmp_path: Path):
    root, base = repo
    scratch = tmp_path / "scratch"
    with pytest.raises(cv.CleanVerifyError), cv.clean_tree(root, "nope", parent=scratch):
        pass
    assert not scratch.exists() or list(scratch.iterdir()) == []


# ---- the overlay --------------------------------------------------------------------------


def test_overlay_restores_deleted_changed_and_removes_added(repo):
    root, base = repo
    tip = tip_with(
        root,
        base,
        {
            "spec.py": None,
            "check.sh": TAMPERED_CHECK,
            "tests/harness/run.py": "print('evil')\n",
            "tests/harness/sneaky.py": "pass\n",
            "REQUIREMENTS.glossary.json": "{}\n",
            "app.py": GOOD,
        },
    )
    with cv.clean_tree(root, tip) as tree:
        changed = cv.overlay_protected(root, base, tree)
        assert changed == [
            "REQUIREMENTS.glossary.json", "check.sh", "spec.py",
            "tests/harness/run.py", "tests/harness/sneaky.py",
        ]  # fmt: skip
        assert (tree / "check.sh").read_text() == CHECK
        assert os.access(tree / "check.sh", os.X_OK)
        assert (tree / "spec.py").read_text() == "RULES = []\n"
        assert (tree / "tests/harness/run.py").read_text() == "print('harness')\n"
        assert not (tree / "tests/harness/sneaky.py").exists()
        assert not (tree / "REQUIREMENTS.glossary.json").exists()
        assert (tree / "app.py").read_text() == GOOD  # the agent's own work is kept
        assert cv.diff_protected(root, base, root=tree).clean
        assert cv.overlay_protected(root, base, tree) == []  # idempotent


def test_overlay_does_not_write_through_planted_symlinks(repo, tmp_path: Path):
    root, base = repo
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("precious\n")
    git(root, "checkout", "-q", "--detach", base)
    (root / "check.sh").unlink()
    os.symlink(outside / "victim.txt", root / "check.sh")  # check.sh -> outside file
    for f in (root / "tests/harness").rglob("*"):
        if f.is_file():
            f.unlink()
    for d in sorted((root / "tests/harness").rglob("*"), reverse=True):
        d.rmdir()
    (root / "tests/harness").rmdir()
    os.symlink(outside, root / "tests/harness")  # the harness directory -> outside directory
    tip = commit(root, "symlinks")
    with cv.clean_tree(root, tip) as tree:
        assert (tree / "check.sh").is_symlink()
        cv.overlay_protected(root, base, tree)
        assert not (tree / "check.sh").is_symlink() and (tree / "check.sh").read_text() == CHECK
        assert not (tree / "tests/harness").is_symlink()
        assert (tree / "tests/harness/run.py").read_text() == "print('harness')\n"
        assert cv.diff_protected(root, base, root=tree).clean
    assert (outside / "victim.txt").read_text() == "precious\n"
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]


# ---- the verdict --------------------------------------------------------------------------


def test_clean_candidate_passes(repo):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD})
    v = cv.verify(root, base, tip, "./check.sh")
    assert v.passed and v.gate_passed and v.exit_code == 0
    assert v.protected_diff.clean and v.overlaid == []
    assert v.base == git(root, "rev-parse", base) and v.tip == tip
    assert v.runs == 1 and v.exit_codes == [0] and not v.timed_out
    assert v.tree_cleaned and not Path(v.tree_path).exists()
    assert json.loads(json.dumps(v.as_dict()))["protected_diff"]["clean"] is True


def test_failing_candidate_reports_the_tail(repo):
    root, base = repo  # app.py stays BAD
    tip = tip_with(root, base, {"notes.txt": "x\n"})
    v = cv.verify(root, base, tip, "./check.sh", tail_chars=12)
    assert not v.passed and not v.gate_passed and v.exit_code == 1
    assert v.output_tail == "app.py is not GOOD\n"[-12:]
    assert v.protected_diff.clean and v.tree_cleaned


def test_tampered_check_sh_is_detected_and_the_base_check_decides(repo):
    root, base = repo
    # the agent leaves app.py BAD and makes check.sh say yes: its own worktree gate passes ...
    tip = tip_with(root, base, {"check.sh": TAMPERED_CHECK})
    assert subprocess.run(["sh", "check.sh"], cwd=root).returncode == 0
    # ... the clean verdict uses the base's check.sh and fails
    v = cv.verify(root, base, tip, "./check.sh")
    assert v.protected_diff.changed == ("check.sh",)
    assert "check.sh" in v.overlaid
    assert not v.gate_passed and v.exit_code == 1
    assert "app.py is not GOOD" in v.output_tail and "tampered" not in v.output_tail
    assert not v.passed and v.tree_cleaned
    # tamper plus a real fix: the gate passes on the base check, the tamper still fails strict
    tip2 = tip_with(root, base, {"check.sh": TAMPERED_CHECK, "app.py": GOOD})
    v2 = cv.verify(root, base, tip2, "./check.sh")
    assert v2.gate_passed and not v2.passed and v2.protected_diff.changed == ("check.sh",)
    v3 = cv.verify(root, base, tip2, "./check.sh", strict=False)
    assert v3.gate_passed and v3.passed and not v3.protected_diff.clean


def test_tampered_harness_cannot_change_the_verdict(repo):
    root, base = repo
    put(root, "check.sh", "#!/bin/sh\npython3 tests/harness/run.py | grep -q harness\n", x=True)
    base = commit(root, "check runs the harness")
    tip = tip_with(root, base, {"tests/harness/run.py": "print('always passes')\n"})
    v = cv.verify(root, base, tip, "./check.sh")
    assert v.gate_passed  # the base harness prints 'harness'
    assert v.protected_diff.changed == ("tests/harness/run.py",) and not v.passed
    # the harness deleted and a replacement added elsewhere in the protected directory
    tip2 = tip_with(root, base, {"tests/harness/run.py": None, "tests/harness/other.py": "1\n"})
    v2 = cv.verify(root, base, tip2, "./check.sh")
    assert v2.gate_passed and not v2.passed
    assert v2.protected_diff.deleted == ("tests/harness/run.py",)
    assert v2.protected_diff.added == ("tests/harness/other.py",)


def test_runs_repeat_in_one_tree_and_all_must_pass(repo):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD})
    flaky = "if test -f .seen; then exit 0; fi; touch .seen; exit 7"
    v = cv.verify(root, base, tip, flaky, runs=3)
    assert v.runs == 3 and v.exit_codes == [7, 0, 0]
    assert not v.passed and v.exit_code == 7
    assert cv.verify(root, base, tip, "true", runs=3).exit_codes == [0, 0, 0]


def test_gate_timeout_kills_the_tree(repo, tmp_path: Path):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD})
    pidfile = tmp_path / "child.pid"
    cmd = f"sleep 60 & echo $! > {pidfile}; wait"
    v = cv.verify(root, base, tip, cmd, timeout=0.5, grace=0.5)
    assert v.timed_out and v.exit_code == 124 and not v.passed and v.tree_cleaned
    pid = int(pidfile.read_text())
    gone = False
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            gone = True
            break
        subprocess.run(["sleep", "0.1"])
    assert gone


def test_gate_environment_is_scrubbed(repo, tmp_path: Path):
    root, base = repo
    tip = tip_with(root, base, {"app.py": GOOD})
    out = tmp_path / "env.json"
    script = (
        "python3 -c \"import json,os,sys; json.dump(dict(os.environ), open(sys.argv[1],'w'))\" "
        f"{out}"
    )
    secret = {
        "ANTHROPIC_API_KEY": "sk-ant-nope",
        "ANTHROPIC_AUTH_TOKEN": "t",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_BEARER_TOKEN_BEDROCK": "b",
        "PYTHONHOME": "/nowhere",
        "KEEP": "me",
    }
    base_env = {**os.environ, **secret}
    v = cv.verify(root, base, tip, script, env=base_env, env_extra={"EXTRA": "1"})
    assert v.gate_passed, v.output_tail
    env = json.loads(out.read_text())
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
              "AWS_BEARER_TOKEN_BEDROCK", "PYTHONHOME"):  # fmt: skip
        assert k not in env
    assert env["KEEP"] == "me" and env["EXTRA"] == "1"
    assert env["UV_NO_SYNC"] == "1" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert str(cv.LATTICE_ROOT) in env["PYTHONPATH"].split(os.pathsep)
    assert str(cv.LATTICE_ROOT) != str(root)


def test_gate_env_puts_the_tree_first(tmp_path: Path):
    tree = tmp_path / "t"
    (tree / "proj").mkdir(parents=True)
    env = cv.gate_env(
        {"PYTHONPATH": "/keep"}, repo=tmp_path, worktree=tree, project_rel="proj", sys_path=["src"]
    )
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts[:3] == [
        str(tree),
        str((tree / "proj").resolve()),
        str((tree / "proj/src").resolve()),
    ]
    assert parts[-1] == "/keep"


def test_worktree_mode_verifies_the_same(repo):
    root, base = repo
    tip = tip_with(root, base, {"check.sh": TAMPERED_CHECK, "app.py": GOOD})
    a = cv.verify(root, base, tip, "./check.sh")
    w = cv.verify(root, base, tip, "./check.sh", mode="worktree")
    assert (a.passed, a.gate_passed, a.protected_diff) == (
        w.passed,
        w.gate_passed,
        w.protected_diff,
    )
    assert a.tree_digest == w.tree_digest
    assert w.tree_cleaned and len(git(root, "worktree", "list").splitlines()) == 1


def test_scripts_named_by_the_gate_are_protected_and_overlaid(repo):
    root, base = repo
    put(root, "scripts/gate.sh", "#!/bin/sh\ngrep -q GOOD app.py\n", x=True)
    base = commit(root, "gate script")
    tip = tip_with(root, base, {"scripts/gate.sh": "#!/bin/sh\nexit 0\n"})
    v = cv.verify(root, base, tip, "sh scripts/gate.sh")
    assert v.protected_diff.changed == ("scripts/gate.sh",)
    assert not v.gate_passed  # the base's gate script ran: app.py is BAD
