"""R4 (gap G3): the rebuild's protected-file helpers delegate to lattice/studio/cleanverify.py,
and the opt-in ``[rebuild] clean_verify`` re-runs a passing build's gates on a clean archive of
its commit with the base's protected files laid over it.

The first half is a differential test: the pre-R4 ``Rebuilder._protected_paths``,
``_restore_protected`` / ``_restore_tree`` and ``_snapshot_tree`` (copied verbatim below as
``legacy_*``) and the delegating ones run on twin worktrees with the same edits, and must give
the same lists and leave the same files; the intended differences (all stricter) are pinned
one by one. The second half drives builds with the FAKE ``claude`` of test_rebuild.py. Temp git
repositories only; no model, no API key."""

# ruff: noqa: F811 -- the fixtures of test_rebuild.py are imported by name
from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from lattice.improve.events import CAUSES
from lattice.improve.signals import build_cause
from lattice.studio import rebuild as RB
from lattice.studio.rebuild import RebuildConfig, Rebuilder, RebuildError, _Run
from tests.studio.test_rebuild import (  # noqa: F401 -- fixtures and helpers
    CHANGE,
    GATE,
    cleanup,
    fake,
    finish,
    git,
    plan,
    rebuilder,
    repo,
)

API_MD = Path(__file__).resolve().parents[2] / "lattice" / "studio" / "API.md"


# ---- the pre-R4 code, verbatim (self -> rb) -------------------------------------------------


def legacy_protected_paths(rb: Rebuilder, run: Any) -> list[str]:
    rel = "" if run.project_rel == "." else run.project_rel + "/"
    names = [*RB.SPEC_FILES, *rb.protected, *RB.grpc_table(rb.project)[0]]  # L3: protos
    for n in rb._notes_names(run.worktree / run.project_rel):
        names.append(n)
    for n in legacy_base_notes_names(run):
        if n not in names:
            names.append(n)
    out = [f"{rel}{n}" for n in names]
    for g in run.gates:  # the gate scripts themselves
        try:
            toks = shlex.split(g)
        except ValueError:
            continue
        while toks and RB.re.fullmatch(r"\w+=.*", toks[0]):
            toks = toks[1:]
        if toks and "/" in toks[0] and not os.path.isabs(toks[0]):
            p = os.path.normpath(toks[0])
            if RB._git(run.repo, "cat-file", "-e", f"{run.base}:{p}").returncode == 0:
                out.append(p)
    return list(dict.fromkeys(out))


def legacy_deny_paths(rb: Rebuilder, run: Any) -> list[str]:
    out = []
    for p in legacy_protected_paths(rb, run):
        d = p.rstrip("/")
        out.append(d + "/" if p.endswith("/") or rb._is_tree(run, d) else p)
    return out


def legacy_base_notes_names(run: Any) -> list[str]:
    rel = "" if run.project_rel == "." else run.project_rel + "/"
    r = RB._git(run.repo, "cat-file", "-p", f"{run.base}:{rel}spec.trace.json")
    if r.returncode != 0:
        return []
    try:
        source = json.loads(r.stdout).get("source")
    except (ValueError, AttributeError):
        return []
    if not isinstance(source, str) or not source:
        return []
    from lattice.spec.rationale import notes_names

    cands = (notes_names(source)[0], notes_names(source)[2])
    return [
        n
        for n in cands
        if RB._git(run.repo, "cat-file", "-e", f"{run.base}:{rel}{n}").returncode == 0
    ]


def legacy_restore_protected(
    rb: Rebuilder, run: Any, paths: Sequence[str] | None = None
) -> list[str]:
    edited = []
    for p in legacy_protected_paths(rb, run) if paths is None else paths:
        if paths is None and Rebuilder._is_tree(run, p):
            edited += legacy_restore_tree(rb, run, p.rstrip("/"))  # L1: a directory entry
            continue
        r = subprocess.run(
            ["git", "-C", str(run.repo), "cat-file", "-p", f"{run.base}:{p}"],
            capture_output=True,
            timeout=60,
        )
        want = r.stdout if r.returncode == 0 else None
        f = run.worktree / p
        have = f.read_bytes() if f.is_file() else None
        if have == want:
            continue
        edited.append(p)
        if want is None:
            f.unlink(missing_ok=True)
        else:
            RB._git_ok(run.worktree, "checkout", run.base, "--", p)
    return edited


def legacy_restore_tree(rb: Rebuilder, run: Any, d: str) -> list[str]:
    ls = RB._git(run.repo, "ls-tree", "-r", "--name-only", run.base, "--", d).stdout
    base = set(ls.split("\n")) - {""}
    edited = legacy_restore_protected(rb, run, sorted(base))
    files = (f for f in (run.worktree / d).rglob("*") if f.is_file())
    for p in sorted({f.relative_to(run.worktree).as_posix() for f in files} - base):
        if RB._git(run.worktree, "check-ignore", "-q", "--", p).returncode != 0:
            (run.worktree / p).unlink(missing_ok=True)
            edited.append(p)
    return edited


def legacy_snapshot_tree(wt: Path) -> str:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="lattice-rebuild-idx-") as d:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(d) / "index")}
        RB._git_ok(wt, "read-tree", "HEAD", env=env)
        RB._git_ok(wt, "add", "-A", "--", ".", *RB._junk_pathspecs(), env=env)
        return RB._git_ok(wt, "write-tree", env=env)


# ---- twin worktrees -------------------------------------------------------------------------

TRACE = json.dumps({"source": "REQUIREMENTS.md", "links": {}})


def put(root: Path, rel: str, text: str, *, x: bool = False) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if x:
        p.chmod(0o755)


def make_base(tmp: Path, *, notes: bool = True) -> Path:
    """A project with the spec files, a protected directory with a git-ignored generated
    subdirectory, a proto, gate scripts, and (`notes`) the owner's committed notes."""
    root = tmp / "repo"
    root.mkdir()
    put(root, "REQUIREMENTS.md", "# R\n")
    put(root, "spec.py", "RULES = []\n")
    put(root, "spec.trace.json", TRACE)
    put(root, "studio.toml", '[grpc]\nprotos = ["api/a.proto"]\n')
    put(root, "api/a.proto", "syntax = 'proto3';\n")
    put(root, "proto/a.proto", "A\n")
    put(root, "proto/sub/b.proto", "B\n")
    put(root, "docs/x.md", "x\n")
    put(root, "gate.sh", "#!/bin/sh\nexit 0\n", x=True)
    put(root, "scripts/g.sh", "#!/bin/sh\nexit 0\n", x=True)
    put(root, "main.go", "package main\n")
    put(root, ".gitignore", "proto/gen/\n")
    if notes:
        put(root, "REQUIREMENTS.rationale.json", '{"notes": []}\n')
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


GATES = ["./gate.sh", "FOO=1 scripts/g.sh", "make test"]


def twins(tmp: Path, protected: Sequence[str], gates: Sequence[str] = GATES, **kw: Any):
    """(rebuilder, legacy run, new run): two worktrees of one base commit."""
    root = make_base(tmp, **kw)
    base = git(root, "rev-parse", "HEAD")
    runs = []
    for name in ("old", "new"):
        wt = tmp / name
        git(root, "worktree", "add", "-q", "-b", name, str(wt))
        runs.append(_Run({"id": name}, [], root, wt, ".", base, name, list(gates), [], ""))
    rb = Rebuilder(root, {}, name="t", model="sonnet", protected=list(protected))
    return rb, runs[0], runs[1]


def edit(wt: Path, edits: dict[str, str | None]) -> None:
    for rel, text in edits.items():
        if text is None:
            p = wt / rel
            if p.is_dir():
                import shutil

                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        else:
            put(wt, rel, text)


def files(wt: Path) -> dict[str, tuple[str, bool]]:
    """Every file of the worktree (not .git): bytes and the exec bit."""
    out = {}
    for f in sorted(wt.rglob("*")):
        rel = f.relative_to(wt).as_posix()
        if rel == ".git" or not f.is_file():
            continue
        out[rel] = (f.read_text(errors="replace"), os.access(f, os.X_OK))
    return out


SCENARIOS: dict[str, dict[str, str | None]] = {
    "nothing": {},
    "ordinary edits only": {"main.go": "package main // x\n", "new.go": "package main\n"},
    "spec files edited and deleted": {"spec.py": "RULES = [1]\n", "REQUIREMENTS.md": None},
    "directory: changed, deleted, added": {
        "proto/sub/b.proto": "EDITED\n",
        "proto/a.proto": None,
        "proto/sub/new.proto": "N\n",
    },
    "directory: ignored file kept": {"proto/gen/stub.pb.go": "// generated\n"},
    "notes: edited, and a note the base lacks": {
        "REQUIREMENTS.rationale.json": "{}\n",
        "REQUIREMENTS.glossary.json": "{}\n",
    },
    "notes: deleted": {"REQUIREMENTS.rationale.json": None},
    "gate scripts": {"gate.sh": "#!/bin/sh\necho tampered\n", "scripts/g.sh": "exit 0\n"},
    "proto of [grpc]": {"api/a.proto": "tampered\n"},
    "many entries (the order)": {
        "gate.sh": "x\n",
        "spec.trace.json": "{}\n",
        "proto/sub/new.proto": "N\n",
        "proto/a.proto": "E\n",
        "api/a.proto": "t\n",
        "REQUIREMENTS.md": "# changed\n",
        "docs/y.md": "y\n",
    },
    "a protected directory deleted": {"docs": None},
}


@pytest.mark.parametrize("name", list(SCENARIOS))
@pytest.mark.parametrize("notes", [True, False])
def test_differential_protected_set_and_restore(tmp_path: Path, name: str, notes: bool) -> None:
    rb, old, new = twins(tmp_path, ["proto", "docs/"], notes=notes)
    for run in (old, new):
        edit(run.worktree, SCENARIOS[name])
    assert rb._protected_paths(new) == legacy_protected_paths(rb, old)
    assert rb._deny_paths(new) == legacy_deny_paths(rb, old)
    before = legacy_snapshot_tree(old.worktree)
    assert RB._snapshot_tree(old.worktree) == before
    got_old = legacy_restore_protected(rb, old)
    got_new = rb._restore_protected(new)
    assert got_new == got_old, name
    assert files(new.worktree) == files(old.worktree)
    assert rb._restore_protected(new) == [] == legacy_restore_protected(rb, old)
    # the snapshot of the restored trees is the same tree too
    assert RB._snapshot_tree(new.worktree) == legacy_snapshot_tree(old.worktree)


def test_differential_restore_tree_of_one_directory(tmp_path: Path) -> None:
    rb, old, new = twins(tmp_path, ["proto"])
    for run in (old, new):
        edit(run.worktree, SCENARIOS["directory: changed, deleted, added"])
        edit(run.worktree, SCENARIOS["directory: ignored file kept"])
    assert rb._restore_tree(new, "proto") == legacy_restore_tree(rb, old, "proto")
    assert files(new.worktree) == files(old.worktree)


def test_differential_subdirectory_project(tmp_path: Path) -> None:
    root = tmp_path / "mono"
    for rel, text in {"app/REQUIREMENTS.md": "# R\n", "app/spec.py": "x\n",
                      "app/spec.trace.json": TRACE, "app/h/a.txt": "1\n",
                      "app/REQUIREMENTS.glossary.json": "{}\n",
                      "gate.sh": "exit 0\n"}.items():  # fmt: skip
        put(root, rel, text)
    git(root, "init", "-q")
    git(root, "-c", "user.email=t@e", "-c", "user.name=T", "add", "-A")
    git(root, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-qm", "b")
    base = git(root, "rev-parse", "HEAD")
    runs = []
    for name in ("old", "new"):
        wt = tmp_path / name
        git(root, "worktree", "add", "-q", "-b", name, str(wt))
        edit(wt, {"app/h/a.txt": "2\n", "app/h/b.txt": "3\n", "app/spec.py": None,
                  "app/REQUIREMENTS.glossary.json": "[]\n", "gate.sh": "x\n"})  # fmt: skip
        runs.append(_Run({"id": name}, [], root, wt, "app", base, name, ["./gate.sh"], [], ""))
    rb = Rebuilder(root / "app", {}, name="t", model="sonnet", protected=["h"])
    old, new = runs
    assert rb._protected_paths(new) == legacy_protected_paths(rb, old)
    assert rb._restore_protected(new) == legacy_restore_protected(rb, old)
    assert files(new.worktree) == files(old.worktree)


# ---- the intended differences (each one stricter), pinned ------------------------------------


def test_difference_a_gate_script_behind_a_runner_is_protected(tmp_path: Path) -> None:
    rb, old, new = twins(tmp_path, [], gates=["bash scripts/g.sh", "python3 ./gate.sh"])
    assert not {"scripts/g.sh", "gate.sh"} & set(legacy_protected_paths(rb, old))
    assert rb._protected_paths(new)[-2:] == ["scripts/g.sh", "gate.sh"]
    edit(new.worktree, {"scripts/g.sh": "exit 0\n"})
    assert rb._restore_protected(new) == ["scripts/g.sh"]


def test_difference_names_are_normalised_and_escaping_ones_dropped(tmp_path: Path) -> None:
    rb, old, new = twins(tmp_path, ["./docs/", "../outside", "/etc/passwd", "proto/"])
    assert legacy_protected_paths(rb, old)[3:7] == ["./docs/", "../outside", "/etc/passwd",
                                                    "proto/"]  # fmt: skip
    assert rb._protected_paths(new)[3:5] == ["docs/", "proto/"]
    assert rb._deny_paths(new)[3:5] == ["docs/", "proto/"]


def test_difference_a_mode_change_is_an_edit(tmp_path: Path) -> None:
    rb, old, new = twins(tmp_path, [])
    for run in (old, new):
        (run.worktree / "gate.sh").chmod(0o644)  # the gate can no longer run
    assert legacy_restore_protected(rb, old) == []
    assert rb._restore_protected(new) == ["gate.sh"]
    assert os.access(new.worktree / "gate.sh", os.X_OK)


def test_difference_a_symlink_with_the_same_bytes_is_an_edit(tmp_path: Path) -> None:
    rb, old, new = twins(tmp_path, [])
    for run in (old, new):
        outside = tmp_path / f"{run.branch}-spec.py"
        outside.write_bytes((run.worktree / "spec.py").read_bytes())
        (run.worktree / "spec.py").unlink()
        (run.worktree / "spec.py").symlink_to(outside)
    assert legacy_restore_protected(rb, old) == []  # it read through the link
    assert rb._restore_protected(new) == ["spec.py"]
    assert not (new.worktree / "spec.py").is_symlink()


def test_difference_a_directory_where_the_base_has_none_is_cleared(tmp_path: Path) -> None:
    rb, old, new = twins(tmp_path, ["gen"])
    for run in (old, new):
        put(run.worktree, "gen/x.txt", "x\n")
    assert legacy_restore_protected(rb, old) == []
    assert rb._restore_protected(new) == ["gen/x.txt"]


# ---- the other delegations ------------------------------------------------------------------


def test_integration_worktree_delegates_and_keeps_its_error(repo: Path, fake: Path) -> None:
    rb = rebuilder(repo, fake)
    sha = git(repo, "rev-parse", "HEAD")
    run = rb.integration_worktree(sha, "plan-p1-integration", RB.threading.Event())
    try:
        assert git(run.worktree, "rev-parse", "HEAD") == sha and run.base == sha
        assert run.gates == ["./gate.sh"] and run.branch == "" and run.project_rel == "."
        assert run.record == {"id": "plan-p1-integration", "warnings": []}
        assert run.trace_json == (repo / "spec.trace.json").read_text()
    finally:
        rb.remove_worktree(run)
    with pytest.raises(RebuildError) as e:
        rb.integration_worktree("0" * 40, "bad", RB.threading.Event())
    assert e.value.status == 500 and str(e.value).startswith("git worktree add failed: fatal:")


# ---- [rebuild] clean_verify (opt-in) ----------------------------------------------------------


def test_clean_verify_is_opt_in_and_documented(tmp_path: Path) -> None:
    assert RebuildConfig(tmp_path, {}).clean_verify is False
    assert RebuildConfig(tmp_path, {"clean_verify": True}).clean_verify is True
    for v in (False, "true", 1, "yes", None):
        assert RebuildConfig(tmp_path, {"clean_verify": v}).clean_verify is False
    assert "clean_verify" not in RebuildConfig(tmp_path, {"clean_verify": True}).plan_defaults()
    doc = API_MD.read_text(encoding="utf-8")
    assert "clean_verify = false" in doc and "`clean_verify_failed`" in doc


def test_the_cause_is_known_to_the_improvement_loop() -> None:
    import importlib

    analyze = importlib.import_module("lattice.improve.analyze")
    assert "clean_verify_failed" in CAUSES
    rec = {
        "status": "gave_up",
        "max_rounds": 3,
        "rounds": [{"n": 1, "check": {"verdict": "pass", "findings": []}}],
        "error": {"kind": "clean_verify", "reason": "clean_verify_failed", "message": "m"},
    }
    assert build_cause(rec) == "clean_verify_failed"
    t = analyze._template(analyze.Cluster(key="cause:clean_verify_failed"))
    assert t["title"] != "Cause clean_verify_failed" and t["kind"] == "experiment"


def commit_all(repo: Path, msg: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-qm", msg)


def build(repo: Path, fake: Path, on: bool | None, **table: Any) -> dict[str, Any]:
    if on is not None:
        table["clean_verify"] = on
    rb = rebuilder(repo, fake, **table)
    return finish(rb, rb.start([CHANGE])["id"])


@pytest.mark.parametrize("on", [None, False, True])
def test_an_ignored_file_passes_in_the_worktree_but_not_on_a_clean_checkout(
    repo: Path, fake: Path, on: bool | None
) -> None:
    gi = repo / ".gitignore"
    gi.write_text((gi.read_text() if gi.exists() else "") + "built.txt\n")
    commit_all(repo, "built.txt is ignored")
    plan(fake, {"files": {"built.txt": "done\n"}, "text": "Built it."})
    rec = build(repo, fake, on)
    try:
        rnd = rec["rounds"][-1]
        assert rnd["check"]["verdict"] == "pass"  # in the agent's own tree
        if not on:  # off (the default, or false): as before
            assert rec["status"] == "passed" and rec.get("error") is None
            assert "clean_verify" not in rnd and build_cause(rec) is None
            return
        assert rec["status"] == "gave_up" and "commit" not in rec
        assert rec["error"]["kind"] == "clean_verify"
        assert rec["error"]["reason"] == "clean_verify_failed"
        assert build_cause(rec) == "clean_verify_failed"
        cvr = rnd["clean_verify"]
        assert cvr["passed"] is False and cvr["skipped"] == []
        (g,) = cvr["gates"]
        assert g["command"] == "./gate.sh" and g["exit_code"] == 1 and not g["gate_passed"]
        assert "built.txt is missing" in g["output_tail"]
        assert g["protected_edited"] == [] and g["overlaid"] == []
        # the commit is on the build branch for review; nothing advanced
        assert git(repo, "rev-parse", rec["branch"]) == cvr["tip"]
    finally:
        cleanup(repo, rec)


@pytest.mark.parametrize("on", [False, True])
def test_a_gate_script_the_rebuild_leaves_unprotected_is_overlaid(
    repo: Path, fake: Path, on: bool
) -> None:
    # `sh check.sh` names no path, so the rebuild's own set leaves check.sh to the agent
    # (as before R4); the clean verify always protects it, and the base's copy decides
    (repo / "check.sh").write_text(GATE)
    commit_all(repo, "check.sh")
    plan(fake, {"files": {"check.sh": "#!/bin/sh\necho 'PASS: 0 findings'\nexit 0\n"}})
    rec = build(repo, fake, on, gates=["sh check.sh"])
    try:
        assert rec["rounds"][-1]["check"]["verdict"] == "pass"
        if not on:
            assert rec["status"] == "passed"  # the hole the opt-in closes
            return
        assert rec["status"] == "gave_up" and build_cause(rec) == "clean_verify_failed"
        (g,) = rec["rounds"][-1]["clean_verify"]["gates"]
        assert g["protected_edited"] == ["check.sh"] and "check.sh" in g["overlaid"]
        assert g["exit_code"] == 1 and "built.txt is missing" in g["output_tail"]
        assert "check.sh" in rec["error"]["message"]
    finally:
        cleanup(repo, rec)


def test_an_honest_build_passes_the_clean_verify(repo: Path, fake: Path) -> None:
    plan(
        fake,
        {"files": {"notes.txt": "half way\n"}, "text": "Round one."},
        {"files": {"built.txt": "done\n"}, "text": "Built it."},
    )
    rec = build(repo, fake, True)
    try:
        assert rec["status"] == "passed", rec.get("error")
        r1, r2 = rec["rounds"]
        assert "clean_verify" not in r1  # only after the passing round's commit
        cvr = r2["clean_verify"]
        assert cvr["passed"] and cvr["tip"] == rec["commit"]["sha"]
        (g,) = cvr["gates"]
        assert g["passed"] and g["exit_code"] == 0 and g["tree_digest"]
    finally:
        cleanup(repo, rec)


def test_a_scoped_plan_passes_with_clean_verify(tmp_path: Path) -> None:
    from tests.studio.seed import cleanup_worktrees, manager, run_plan, seed_project

    root = seed_project(tmp_path)
    try:
        pm = manager(root, clean_verify=True)
        rec = run_plan(pm, "all")
        assert rec["status"] == "passed", rec
        built = [n for n in rec["nodes"] if n.get("build_id") and not n.get("nothing_to_build")]
        assert built
        for n in built:
            cvr = pm.rb.get(n["build_id"])["rounds"][-1]["clean_verify"]
            assert cvr["passed"], cvr
            assert all(g["passed"] for g in cvr["gates"])
            assert len(cvr["gates"]) + len(cvr["skipped"]) >= 1
    finally:
        cleanup_worktrees(root)
