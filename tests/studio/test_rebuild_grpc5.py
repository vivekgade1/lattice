"""gRPC round 5, build loop: L1 directory entries in the protected paths, L2 ``[grpc] generate``
run by the loop before the gates, L3 the brief names the protos and the generator. Against the
FAKE ``claude`` of test_rebuild.py in temporary git repositories; no model, no API key."""

# ruff: noqa: F811 -- the fixtures of test_rebuild.py are imported by name
from __future__ import annotations

from pathlib import Path

import pytest

from lattice.studio.rebuild import Rebuilder, _Run, brief, grpc_table, scope_brief
from tests.studio.test_rebuild import (  # noqa: F401 -- fixtures and helpers
    CHANGE,
    cleanup,
    fake,
    finish,
    git,
    plan,
    rebuilder,
    repo,
)

# ---------------------------------------------------------------- L1: directory entries


@pytest.fixture()
def tree(tmp_path: Path) -> tuple[Rebuilder, _Run]:
    """A repo whose ``proto`` directory is protected, a linked worktree, and a bare ``_Run``."""
    root = tmp_path / "r"
    (root / "proto" / "sub").mkdir(parents=True)
    (root / "proto" / "a.proto").write_text("A\n")
    (root / "proto" / "sub" / "b.proto").write_text("B\n")
    (root / ".gitignore").write_text("proto/gen/\n")
    (root / "main.go").write_text("package main\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    wt = tmp_path / "wt"
    git(root, "worktree", "add", "-q", "-b", "b1", str(wt))
    base = git(root, "rev-parse", "HEAD")
    run = _Run(
        record={"id": "b1"},
        changes=[],
        repo=root,
        worktree=wt,
        project_rel=".",
        base=base,
        branch="b1",
        gates=[],
        targets=[],
        trace_json="",
    )
    rb = Rebuilder(root, {}, name="t", model="sonnet", protected=["proto"])
    return rb, run


def test_prot_directory_unchanged_reports_no_edit(tree: tuple[Rebuilder, _Run]) -> None:
    rb, run = tree
    (run.worktree / "main.go").write_text("package main // an ordinary edit\n")
    assert rb._restore_protected(run) == []


def test_prot_file_changed_under_directory_is_named_and_restored(
    tree: tuple[Rebuilder, _Run],
) -> None:
    rb, run = tree
    (run.worktree / "proto" / "sub" / "b.proto").write_text("EDITED\n")
    (run.worktree / "proto" / "a.proto").unlink()
    assert rb._restore_protected(run) == ["proto/a.proto", "proto/sub/b.proto"]
    assert (run.worktree / "proto" / "sub" / "b.proto").read_text() == "B\n"
    assert (run.worktree / "proto" / "a.proto").read_text() == "A\n"
    assert rb._restore_protected(run) == []


def test_prot_file_added_under_directory_is_removed_and_counted(
    tree: tuple[Rebuilder, _Run],
) -> None:
    rb, run = tree
    (run.worktree / "proto" / "sub" / "new.proto").write_text("N\n")
    assert rb._restore_protected(run) == ["proto/sub/new.proto"]
    assert not (run.worktree / "proto" / "sub" / "new.proto").exists()


def test_prot_ignored_file_under_directory_is_kept(tree: tuple[Rebuilder, _Run]) -> None:
    rb, run = tree
    (run.worktree / "proto" / "gen").mkdir()
    (run.worktree / "proto" / "gen" / "stub.pb.go").write_text("// generated\n")
    assert rb._restore_protected(run) == []
    assert (run.worktree / "proto" / "gen" / "stub.pb.go").exists()


# ---------------------------------------------------------------- L2: [grpc] generate


def with_grpc(repo: Path, generate: str, script: str) -> None:
    (repo / "regen.sh").write_text(script)
    (repo / "regen.sh").chmod(0o755)
    toml = (repo / "studio.toml").read_text()
    (repo / "studio.toml").write_text(
        toml + f'\n[grpc]\nprotos = ["proto/a.proto"]\ngenerate = "{generate}"\n'
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "grpc")


def test_generate_runs_after_the_round_and_before_the_gates(repo: Path, fake: Path) -> None:
    # the gate wants built.txt; only the generator makes it, from what the agent wrote
    with_grpc(repo, "./regen.sh", "#!/bin/sh\ncp src.txt built.txt\n")
    plan(fake, {"files": {"src.txt": "from the agent\n"}})
    rb = rebuilder(repo, fake, max_rounds=1)
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "passed", rec
    (r1,) = rec["rounds"]
    assert r1["generate"]["exit_code"] == 0 and r1["generate"]["command"] == "./regen.sh"
    assert (Path(rec["worktree"]) / "built.txt").read_text() == "from the agent\n"
    cleanup(repo, rec)


def test_a_failing_generate_is_a_named_failure_even_when_the_gates_pass(
    repo: Path, fake: Path
) -> None:
    with_grpc(repo, "./regen.sh", "#!/bin/sh\necho no protoc here\nexit 3\n")
    plan(fake, {"files": {"built.txt": "x\n"}})
    rb = rebuilder(repo, fake, max_rounds=1)
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "gave_up", rec
    (r1,) = rec["rounds"]
    assert r1["gates"][0]["exit_code"] == 0  # the gate itself passed
    assert r1["check"]["verdict"] == "fail"
    (f,) = [f for f in r1["check"]["findings"] if f.get("signal_key")]
    assert f["signal_key"] == "studio:grpc_generate_failed"
    assert (
        "./regen.sh" in f["title"] and "exited 3" in f["title"] and "no protoc here" in f["title"]
    )
    cleanup(repo, rec)


def test_no_grpc_section_means_no_generator_and_the_same_text(repo: Path, fake: Path) -> None:
    assert grpc_table(repo) == ([], "")
    kw = {"trace_json": "{}", "gates": ["./gate.sh"], "targets": [], "project_rel": "."}
    assert brief([CHANGE], **kw) == brief([CHANGE], grpc=([], ""), **kw)
    assert "proto" not in brief([CHANGE], **kw)
    scope = {"id": "s", "title": "T", "rules": []}
    sk = {**kw, "out_of_scope": 0}
    assert scope_brief(scope, **sk) == scope_brief(scope, grpc=((), ""), **sk)
    plan(fake, {"files": {"built.txt": "x\n"}})
    rb = rebuilder(repo, fake, max_rounds=1)
    rec = finish(rb, rb.start([CHANGE])["id"])
    assert rec["status"] == "passed" and "generate" not in rec["rounds"][0]
    cleanup(repo, rec)


# ---------------------------------------------------------------- L3: the brief


def test_brief_names_the_contract_files_and_the_stub_command(repo: Path, fake: Path) -> None:
    kw = {"trace_json": "{}", "gates": ["./gate.sh"], "targets": [], "project_rel": "."}
    g = (["proto/toy/v1/shelf.proto", "proto/x.proto"], "./generate.sh")
    scope = {"id": "s", "title": "T", "rules": []}
    for text in (
        brief([CHANGE], grpc=g, **kw),
        scope_brief(scope, out_of_scope=0, grpc=g, **kw),
    ):
        assert "never edit them): proto/toy/v1/shelf.proto, proto/x.proto." in text
        assert "only from `./generate.sh`, which Studio runs before the gates" in text
        assert text.index("these .proto files") < text.index("## Gates")
    # the listed files are protected implicitly (L1 then applies to them)
    with_grpc(repo, "./regen.sh", "#!/bin/sh\n:\n")
    rb = Rebuilder(repo, {"gates": ["./gate.sh"]}, name="t", model="sonnet")
    assert grpc_table(repo) == (["proto/a.proto"], "./regen.sh")
    base = git(repo, "rev-parse", "HEAD")
    run = _Run({"id": "b1"}, [], repo, repo, ".", base, "b", [], [], "")
    assert "proto/a.proto" in rb._protected_paths(run)
