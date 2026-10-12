"""Build from spec (API.md v1.8): areas from the trace's sections, scoped rebuilds with a
baseline, the regression rule and the integration branch, the metrics hook and
``findings_json``. Temporary git repositories, trivial gate scripts, the TEST-ONLY stub
agent or a FAKE ``claude`` executable; no real LLM call, no API key."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.spec.rules import Question, Trace, extract_rules
from lattice.studio import areas as areas_mod
from lattice.studio.hub import discover
from lattice.studio.rebuild import SCOPE_RULES, Rebuilder, RebuildError
from lattice.studio.server import StubGenerator, make_hub_server
from tests.spec.incremental_toy import NOTES_BASE_TRACE
from tests.studio.test_rebuild import FAKE, SID
from tests.studio.test_server import make_project, request

REQS = """\
# Notes app

- A notes service for signed-in users.

## Writing

- A signed-in user creates a note with POST /notes {"text": ...}; it answers 201 with the note.
- A note's text is 1 to 200 characters; anything else answers 422.

## Reading

- GET /notes/{id} answers the caller's own note; another user's note answers 404.
- A user has at most 3 notes; a fourth answers 409.
- Requests without a token answer 401.
- DELETE /notes/{id} deletes the caller's own note; it answers 204.
"""
# REQS shifts the toy's rule ids by one (R1 is the preamble): relink the toy's checks
LINKS = {f"R{int(k[1:]) + 1}": v for k, v in NOTES_BASE_TRACE["links"].items()}

GATE = """#!/bin/sh
# passes iff w.txt and r.txt exist; u.txt is checked too, by a finding with no target
n=0
for f in w.txt r.txt u.txt; do [ -f "$f" ] || n=$((n+1)); done
if [ "$n" = 0 ]; then echo "PASS: 0 findings (G 0)"; exit 0; fi
echo "FAIL: $n findings (G $n)"
[ -f w.txt ] || echo "FAIL w.txt is missing (scenario:create_a_note)"
[ -f r.txt ] || echo "FAIL r.txt is missing (scenario:read_own_and_other)"
[ -f u.txt ] || echo "FAIL lint: u.txt is missing"
exit 1
"""

STUB = {
    "rounds": [{"files": {"x.txt": "1"}}],
    "scopes": {
        "writing": {"rounds": [{"files": {"draft.txt": "d"}}, {"files": {"w.txt": "w\n"}}]},
        "reading": {
            "rounds": [
                {"files": {"r.txt": "r\n", "w.txt": None}, "summary": "broke writing"},
                {"files": {"r.txt": "r\n", "w.txt": "w\n"}},
            ]
        },
        "all": {"rounds": [{"files": {"w.txt": "w\n", "r.txt": "r\n", "u.txt": "u"}}]},
    },
}

HOOK = """import json, os, sys
args = sys.argv[1:]
llm = json.load(open(args[args.index("--llm-json") + 1]))
os.makedirs("metrics", exist_ok=True)
with open("metrics/phases.jsonl", "a") as f:
    f.write(json.dumps({"argv": args, "llm": llm, "cwd": os.getcwd()}) + "\\n")
sys.exit(3 if os.path.exists("hook-fail.flag") else 0)
"""


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def git_rc(root: Path, *args: str) -> int:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True).returncode


def write_trace(root: Path, markdown: str = REQS, links: dict | None = None) -> None:
    (root / "REQUIREMENTS.md").write_text(markdown)
    trace = Trace(
        "REQUIREMENTS.md",
        extract_rules(markdown),
        {k: list(v) for k, v in (LINKS if links is None else links).items()},
        questions=[Question("Q1", "Is there a limit?", "no", ["R5"])],
    )
    (root / "spec.trace.json").write_text(trace.to_json())


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = make_project(tmp_path / "notes", repo=False)
    write_trace(root)
    (root / "gate.sh").write_text(GATE)
    (root / "gate.sh").chmod(0o755)
    (root / "stub-rebuild.json").write_text(json.dumps(STUB))
    (root / "hook.py").write_text(HOOK)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "studio@example.com")
    git(root, "config", "user.name", "Studio Test")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture()
def fake(tmp_path: Path) -> Path:
    d = tmp_path / "fake"
    d.mkdir()
    exe = d / "claude"
    body = FAKE.format(python=sys.executable, sid=SID).replace(
        '"usage": {"input_tokens": 10, "output_tokens": 20}',
        '"usage": {"input_tokens": 10, "output_tokens": 20, '
        '"cache_read_input_tokens": 300, "cache_creation_input_tokens": 40}',
    )
    assert "cache_read_input_tokens" in body
    exe.write_text(body)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return d


def plan(fake: Path, *steps: dict[str, Any]) -> None:
    (fake / "plan.json").write_text(json.dumps(list(steps)))


def fake_log(fake: Path) -> list[dict[str, Any]]:
    p = fake / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def rebuilder(repo: Path, fake: Path | None = None, **table: Any) -> Rebuilder:
    env = {**os.environ}
    if fake is not None:
        env["FAKE_REBUILD_DIR"] = str(fake)
    return Rebuilder(
        repo,
        {"gates": ["./gate.sh"], "max_rounds": 3, "agent": "stub", **table},
        name="notes",
        model="sonnet",
        claude_bin=str(fake / "claude") if fake is not None else "claude",
        env=env,
    )


def finish(rb: Rebuilder, rid: str) -> dict[str, Any]:
    rec = rb.get(rid, wait=30)
    assert rec["status"] in ("passed", "gave_up", "stopped"), rec
    return rec


def owner(repo: Path) -> tuple[str, str, str]:
    return (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "symbolic-ref", "HEAD"),
        git(repo, "status", "--porcelain", "--untracked-files=all"),
    )


@pytest.fixture(autouse=True)
def _no_stray_worktrees(repo: Path) -> Iterator[None]:
    yield
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree ") and "/.lattice/rebuilds/" in line:
            subprocess.run(["rm", "-rf", line.removeprefix("worktree ")], check=False)
    git(repo, "worktree", "prune")


# ---------------------------------------------------------------- areas


def test_areas_from_sections_in_document_order() -> None:
    trace = json.loads(_trace_json())
    got = areas_mod.derive_areas(trace)
    assert [(a["id"], a["title"], a["rules"]) for a in got] == [
        ("notes-app", "Notes app", ["R1"]),
        ("writing", "Writing", ["R2", "R3"]),
        ("reading", "Reading", ["R4", "R5", "R6", "R7"]),
        ("all", "All", ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]),
    ]
    views = [areas_mod.area_view(a, None) for a in got]
    assert [(v["scenarios"], v["checks"], v["failing"], v["status"]) for v in views] == [
        (0, 0, None, "not_built"),
        (2, 2, None, "not_built"),
        (4, 5, None, "not_built"),  # R5 also links invariant:notes_at_most_3
        (6, 7, None, "not_built"),
    ]
    failing = {"scenario:create_a_note", "scenario:read_own_and_other", "title:x"}
    v = [areas_mod.area_view(a, failing) for a in got]
    assert [(x["failing"], x["status"]) for x in v] == [
        (0, "built"),
        (1, "partial"),
        (1, "partial"),
        (2, "partial"),
    ]
    assert areas_mod.area_view(got[1], {"scenario:create_a_note", "scenario:text_bounds"})[
        "status"
    ] == ("not_built")
    assert areas_mod.area_view(got[1], set(), building=True)["status"] == "building"


def test_areas_none_and_missing_sections() -> None:
    trace = json.loads(_trace_json())
    assert [a["id"] for a in areas_mod.derive_areas(trace, "none")] == ["all"]
    flat = {"rules": [{"id": "R1", "text": "a"}, {"id": "R2", "text": "b", "section": ""}]}
    assert [(a["id"], a["rules"]) for a in areas_mod.derive_areas(flat)] == [
        ("requirements", ["R1", "R2"]),
        ("all", ["R1", "R2"]),
    ]
    # first-level sections only (no common title): one area per heading; slugs unique
    two = {
        "rules": [
            {"id": "R1", "section": "Accounts"},
            {"id": "R2", "section": "Accounts!"},
            {"id": "R3", "section": "All"},
        ]
    }
    assert [a["id"] for a in areas_mod.derive_areas(two)] == [
        "accounts",
        "accounts-2",
        "all-2",
        "all",
    ]
    assert areas_mod.derive_areas({}) == [{"id": "all", "title": "All", "rules": [], "targets": []}]


def test_findings_json_shape_and_identity() -> None:
    report = {
        "verdict": "fail",
        "layers": "ABCDX",
        "counts": {"A": 0, "D": 2},
        "findings": [
            {
                "layer": "D",
                "verdict": "silent",
                "rule": "scenario: access__me__as_anon",
                "case": "access__me__as_anon / step 1",
                "details": {"problems": ["status: expected 401, got 404"]},
                "key": "D:GET /v1/me:access__me__as_anon / step 1",
            },
            {"layer": "A", "verdict": "loud", "rule": "go vet", "key": "A:main.go:3"},
        ],
        "cases": {},
        "notes": [],
    }
    counts, fs = areas_mod.findings_from_json(report)
    assert counts == {"A": 0, "D": 2}
    assert fs[0] == {
        "title": "D silent access__me__as_anon / step 1: status: expected 401, got 404",
        "target": "scenario:access__me__as_anon",
        "key": "D:GET /v1/me:access__me__as_anon / step 1",
    }
    assert fs[1]["target"] == "" and areas_mod.finding_identity(fs[1]) == "key:A:main.go:3"
    assert areas_mod.finding_identity(fs[0]) == "scenario:access__me__as_anon"
    assert areas_mod.finding_identity({"title": "boom"}) == "title:boom"
    assert areas_mod.normalize_target("R12: something") == "R12"
    assert areas_mod.normalize_target("invariant:  x_y") == "invariant:x_y"


def test_resolve_scope_errors() -> None:
    trace = json.loads(_trace_json())
    areas = areas_mod.derive_areas(trace)
    ok = areas_mod.resolve_scope({"rules": ["R2", "R5"]}, areas, trace)
    assert ok["kind"] == "rules" and ok["id"] == "rules-r2-r5"
    assert ok["targets"] == [
        "scenario:create_a_note",
        "scenario:the_fourth_note_is_refused",
        "invariant:notes_at_most_3",
    ]
    assert areas_mod.resolve_scope("all", areas, trace)["kind"] == "all"
    for bad in (5, "writing", {"area": 3}, {"area": "writing", "x": 1}, {"rules": []},
                {"rules": ["R1", "R1"]}, {"zone": "x"}, ["writing"]):  # fmt: skip
        with pytest.raises(areas_mod.ScopeError) as e:
            areas_mod.resolve_scope(bad, areas, trace)
        assert e.value.status == 422, bad
    for bad, detail in (({"area": "nope"}, "no area nope"), ({"rules": ["R99"]}, "no rule R99")):
        with pytest.raises(areas_mod.ScopeError) as e:
            areas_mod.resolve_scope(bad, areas, trace)
        assert (e.value.status, e.value.detail) == (404, detail)


def _trace_json() -> str:
    trace = Trace("REQUIREMENTS.md", extract_rules(REQS), LINKS)
    return trace.to_json()


# ---------------------------------------------------------------- scoped builds


def test_a_scoped_build_passes_in_round_2_on_the_integration_branch(repo: Path, fake: Path) -> None:
    before = owner(repo)
    plan(
        fake,
        {"files": {"draft.txt": "half\n"}, "text": "Round one."},
        {"files": {"w.txt": "w\n"}, "text": "Built writing."},
    )
    rb = rebuilder(repo, fake, agent="claude")
    assert rb.start([], scope={"area": "writing"}) == {"id": "b1"}
    rec = finish(rb, "b1")
    assert rec["status"] == "passed", rec
    assert rec["scope"] == {"kind": "area", "id": "writing", "title": "Writing",
                            "rules": ["R2", "R3"]}  # fmt: skip
    assert rec["changes"] == [] and rec["integration_branch"] == "build/notes"
    assert rec["branch"] == "rebuild/writing-1"
    assert rec["base"] == rec["integration_base"] == rec["owner_head"] == before[0]
    assert rec["merged_head"] is None
    # the baseline: every finding, its rules and scope; the unmapped one is out of scope
    b = rec["baseline"]
    assert [(f["target"], f["rules"], f["in_scope"]) for f in b["findings"]] == [
        ("scenario:create_a_note", ["R2"], True),
        ("scenario:read_own_and_other", ["R4"], False),
        ("", [], False),
    ]
    assert b["unmapped"] == ["f3"] and b["verdict"] == "fail" and b["counts"] == {"G": 3}
    assert b["gates"][0]["exit_code"] == 1 and "w.txt is missing" in b["gates"][0]["output_tail"]
    assert rec["targets"] == [
        {
            "id": "scenario:create_a_note",
            "label": "FAIL w.txt is missing (scenario:create_a_note)",
            "done": True,
        }
    ]
    r1, r2 = rec["rounds"]
    json.dumps(rec)  # plain JSON, nothing internal leaks into the record
    assert r1["check"]["verdict"] == "fail" and r2["check"]["verdict"] == "pass"
    assert (r1["scope_passing"], r1["scope_total"], r1["regressions"], r1["newly_fixed"]) == (
        0,
        1,
        [],
        [],
    )
    assert (r2["scope_passing"], r2["scope_total"], r2["regressions"]) == (1, 1, [])
    assert r2["newly_fixed"] == ["scenario:create_a_note"]
    # a passing round keeps the out-of-scope failures, marked
    assert [(f["target"], f["in_scope"], f["regression"]) for f in r2["check"]["findings"]] == [
        ("scenario:read_own_and_other", False, False),
        ("", False, False),
    ]
    # the commit is on the build branch and the integration branch; the owner's is untouched
    sha = rec["commit"]["sha"]
    assert git(repo, "rev-parse", "build/notes") == sha == git(repo, "rev-parse", rec["branch"])
    assert git(repo, "rev-parse", f"{sha}^") == before[0]
    assert sorted(rec["commit"]["files_changed"]) == ["draft.txt", "w.txt"]
    assert rec["commit"]["message"].startswith("build: Writing\n")
    assert "1/1 in-scope checks pass in round 2 of 3, no regressions" in rec["commit"]["message"]
    assert "git merge build/notes" in rec["merge_hint"]
    assert owner(repo) == before
    assert git_rc(repo, "merge-base", "--is-ancestor", sha, "HEAD") == 1
    # the brief: the scope's rules and checks, its failures, the context file, the gate
    argv1, argv2 = (e["argv"] for e in fake_log(fake))
    brief = argv1[-1]
    assert "**Writing** (2 rule(s))" in brief and "R2: A signed-in user creates" in brief
    assert "R3: A note's text is 1 to 200" in brief and "R4:" not in brief
    assert "create_a_note, text_bounds" in brief
    assert "- scenario:create_a_note: FAIL w.txt is missing" in brief
    assert "2 other check(s) fail now outside the scope" in brief
    assert "`CONTEXT.md`" in brief and "`./gate.sh`" in brief
    assert argv1[argv1.index("--append-system-prompt") + 1] == SCOPE_RULES
    assert argv2[argv2.index("--resume") + 1] == SID
    assert "Still failing in scope:" in argv2[-1] and "w.txt is missing" in argv2[-1]
    # usage: tokens incl. cache, the list-price estimate under both names, wall time
    u = rec["usage"]
    assert (u["input_tokens"], u["output_tokens"]) == (20, 40)
    assert (u["cache_read_input_tokens"], u["cache_creation_input_tokens"]) == (600, 80)
    assert u["cost_usd"] == u["cost_usd_list_estimate"] == 0.5 and u["wall_s"] > 0
    assert r1["agent"]["cache_read_input_tokens"] == 300 and r1["wall_s"] >= 0
    assert rec["warnings"] == [] and rec["metrics"] is None


def test_a_regression_fails_the_round_and_areas_accumulate(repo: Path) -> None:
    before = owner(repo)
    rb = rebuilder(repo)
    first = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert first["status"] == "passed"
    tip1 = first["commit"]["sha"]
    rec = finish(rb, rb.start([], scope={"area": "reading"})["id"])
    assert rec["status"] == "passed", rec
    # the second build starts from the first's result
    assert rec["base"] == rec["integration_base"] == tip1
    assert [f["target"] for f in rec["baseline"]["findings"]] == [
        "scenario:read_own_and_other",
        "",
    ]
    r1, r2 = rec["rounds"]
    assert r1["agent_summary"] == "broke writing"
    assert r1["check"]["verdict"] == "fail" and r1["regressions"] == ["scenario:create_a_note"]
    assert (r1["scope_passing"], r1["scope_total"]) == (1, 1), "the scope passed, still a fail"
    assert [(f["target"], f["in_scope"], f["regression"]) for f in r1["check"]["findings"]] == [
        ("scenario:create_a_note", False, True),
        ("", False, False),
    ]
    assert r2["check"]["verdict"] == "pass" and r2["regressions"] == []
    sha = rec["commit"]["sha"]
    assert (
        git(repo, "rev-parse", "build/notes") == sha and git(repo, "rev-parse", f"{sha}^") == tip1
    )
    assert rec["commit"]["files_changed"] == ["r.txt"]
    assert git(repo, "rev-parse", "rebuild/writing-1") == tip1, "build branches are kept"
    assert owner(repo) == before
    areas = {a["id"]: a for a in rb.areas()}
    assert [(a["failing"], a["status"]) for a in areas.values()] == [
        (0, "built"),
        (0, "built"),
        (0, "built"),
        (0, "built"),
    ]


def test_a_regression_with_one_round_gives_up(repo: Path) -> None:
    rb = rebuilder(repo, max_rounds=1)
    tip1 = finish(rb, rb.start([], scope={"area": "writing"}, max_rounds=2)["id"])["commit"]["sha"]
    rec = finish(rb, rb.start([], scope={"area": "reading"})["id"])
    assert rec["status"] == "gave_up" and "commit" not in rec
    assert rec["rounds"][0]["regressions"] == ["scenario:create_a_note"]
    assert git(repo, "rev-parse", "build/notes") == tip1, "a failed build never moves it"
    areas = {a["id"]: (a["failing"], a["status"]) for a in rb.areas()}
    assert areas["writing"] == (0, "built") and areas["reading"] == (1, "partial")


def test_nothing_to_build_is_409_and_leaves_nothing(repo: Path) -> None:
    rb = rebuilder(repo)
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    worktrees = git(repo, "worktree", "list")
    with pytest.raises(RebuildError) as e:
        rb.start([], scope={"area": "writing"})
    assert (e.value.status, e.value.detail) == (
        409,
        "nothing to build: the checks in this scope already pass",
    )
    assert [r["id"] for r in rb.list()] == ["b1"] and rb.next_id == 2
    assert git_rc(repo, "show-ref", "--verify", "-q", "refs/heads/rebuild/writing-2") == 1
    assert git(repo, "worktree", "list") == worktrees
    assert git(repo, "rev-parse", "build/notes") == rec["commit"]["sha"]
    # an area with no failing check at all (the preamble) is 409 too
    with pytest.raises(RebuildError) as e:
        rb.start([], scope={"area": "notes-app"})
    assert e.value.status == 409
    # the next build gets the next id
    assert rb.start([], scope={"area": "reading"})["id"] == "b2"
    finish(rb, "b2")


def test_nothing_to_build_on_first_use_creates_no_integration_branch(repo: Path) -> None:
    for f in ("w.txt", "r.txt", "u.txt"):
        (repo / f).write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "all built")
    rb = rebuilder(repo)
    with pytest.raises(RebuildError) as e:
        rb.start([], scope="all")
    assert e.value.status == 409
    assert git_rc(repo, "show-ref", "--verify", "-q", "refs/heads/build/notes") == 1
    assert rb.list() == []


def test_scope_all_includes_unmapped_findings(repo: Path) -> None:
    rb = rebuilder(repo)
    rec = finish(rb, rb.start([], scope="all")["id"])
    assert rec["status"] == "passed" and rec["branch"] == "rebuild/all-1"
    assert [t["id"] for t in rec["targets"]] == [
        "scenario:create_a_note",
        "scenario:read_own_and_other",
        "title:FAIL lint: u.txt is missing",
    ]


def test_scope_validation(repo: Path) -> None:
    rb = rebuilder(repo)
    for scope, status, detail in (
        ({"area": "nope"}, 404, "no area nope"),
        ({"rules": ["R2", "R99"]}, 404, "no rule R99"),
        ({"area": 1}, 422, None),
        ("everything", 422, None),
    ):
        with pytest.raises(RebuildError) as e:
            rb.start([], scope=scope)
        assert e.value.status == status and (detail is None or e.value.detail == detail)
    with pytest.raises(RebuildError) as e:
        rb.start([])
    assert e.value.status == 422
    assert rb.list() == []


def test_one_build_at_a_time_and_building_status(repo: Path) -> None:
    stub = json.loads((repo / "stub-rebuild.json").read_text())
    stub["scopes"]["writing"]["rounds"][0]["hold"] = 3
    (repo / "stub-rebuild.json").write_text(json.dumps(stub))
    git(repo, "commit", "-qam", "hold")
    rb = rebuilder(repo)
    rid = rb.start([], scope={"area": "writing"})["id"]
    with pytest.raises(RebuildError) as e:
        rb.start([], scope={"area": "reading"})
    assert e.value.status == 409 and "already running" in e.value.detail
    assert {a["id"]: a["status"] for a in rb.areas()}["writing"] == "building"
    rb.stop(rid)
    rec = finish(rb, rid)
    assert rec["status"] == "stopped" and rec["worktree"] is None and rec["branch"] is None
    # the integration branch stays where it was (created at HEAD)
    assert git(repo, "rev-parse", "build/notes") == git(repo, "rev-parse", "HEAD")


def test_the_owners_new_commits_are_merged_into_the_integration_branch(repo: Path) -> None:
    rb = rebuilder(repo)
    tip1 = finish(rb, rb.start([], scope={"area": "writing"})["id"])["commit"]["sha"]
    (repo / "owner.txt").write_text("the owner's own work\n")
    git(repo, "add", "owner.txt")
    git(repo, "commit", "-q", "-m", "owner work")
    before = owner(repo)
    rec = finish(rb, rb.start([], scope={"area": "reading"})["id"])
    assert rec["status"] == "passed", rec
    assert rec["integration_base"] == tip1 and rec["merged_head"] == before[0]
    merge = rec["base"]
    assert git(repo, "rev-parse", f"{merge}^1") == tip1
    assert git(repo, "rev-parse", f"{merge}^2") == before[0]
    assert git(repo, "rev-parse", f"{rec['commit']['sha']}^") == merge
    assert git(repo, "rev-parse", "build/notes") == rec["commit"]["sha"]
    assert owner(repo) == before


def test_a_merge_conflict_gives_up(repo: Path) -> None:
    rb = rebuilder(repo)
    finish(rb, rb.start([], scope={"area": "writing"})["id"])
    tip = git(repo, "rev-parse", "build/notes")
    (repo / "w.txt").write_text("the owner wrote this differently\n")
    git(repo, "add", "w.txt")
    git(repo, "commit", "-q", "-m", "owner w")
    before = owner(repo)
    rec = finish(rb, rb.start([], scope={"area": "reading"})["id"])
    assert rec["status"] == "gave_up" and rec["error"]["kind"] == "merge_conflict"
    assert rec["rounds"] == [] and rec["baseline"] is None
    assert git(repo, "rev-parse", "build/notes") == tip
    assert owner(repo) == before
    wt = Path(rec["worktree"])
    assert git(wt, "status", "--porcelain") == "", "the merge was aborted"


def test_gates_that_cannot_run_at_the_baseline_give_up(repo: Path) -> None:
    (repo / "broken.sh").write_text("#!/bin/sh\necho kaputt\nexit 2\n")
    (repo / "broken.sh").chmod(0o755)
    git(repo, "add", "broken.sh")
    git(repo, "commit", "-q", "-m", "broken")
    rb = rebuilder(repo, gates=["./gate.sh", "./broken.sh"])
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert rec["status"] == "gave_up" and rec["error"]["kind"] == "baseline"
    assert "`./broken.sh` exited 2" in rec["error"]["message"] and rec["rounds"] == []


def test_a_spec_edit_in_a_scoped_build_is_reverted_and_fails_the_round(repo: Path) -> None:
    stub = json.loads((repo / "stub-rebuild.json").read_text())
    stub["scopes"]["writing"]["rounds"][0]["files"]["REQUIREMENTS.md"] = "# tampered\n"
    stub["scopes"]["writing"]["rounds"][0]["files"]["w.txt"] = "w\n"
    (repo / "stub-rebuild.json").write_text(json.dumps(stub))
    git(repo, "commit", "-qam", "tamper")
    rb = rebuilder(repo)
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    r1 = rec["rounds"][0]
    assert r1["check"]["verdict"] == "fail" and r1["spec_edited"] == ["REQUIREMENTS.md"]
    assert r1["scope_passing"] == 1, "the scope passed, but the spec was edited"
    assert r1["check"]["findings"][0]["target"] == "spec"
    assert rec["status"] == "passed" and len(rec["rounds"]) == 2
    assert "REQUIREMENTS.md" not in rec["commit"]["files_changed"]
    assert (Path(rec["worktree"]) / "REQUIREMENTS.md").read_text() == REQS


def test_the_metrics_hook_runs_with_its_arguments_and_is_committed(repo: Path) -> None:
    rb = rebuilder(repo, metrics_hook=f"{sys.executable} hook.py")
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert rec["status"] == "passed" and rec["warnings"] == []
    assert "metrics/phases.jsonl" in rec["commit"]["files_changed"]
    line = json.loads(git(repo, "show", f"{rec['commit']['sha']}:metrics/phases.jsonl"))
    argv = line["argv"]
    assert argv[argv.index("--phase") + 1] == "build_writing"
    assert "--check-json" not in argv  # no findings_json configured
    note = argv[argv.index("--note") + 1]
    assert note.startswith("Lattice Studio build b1 (Writing): passed in round 2 of 3")
    assert line["llm"] == {
        "model": "sonnet",
        "tokens_in": 0,
        "tokens_out": 0,
        "input_tokens_uncached": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cost_usd_list_estimate": 0.0,
        "wall_s": line["llm"]["wall_s"],
        "rounds": 2,
        "llm_calls": 0,
        "target_branch": "build/notes",
    }
    assert note.endswith("; target branch build/notes")
    assert Path(line["cwd"]).resolve() == Path(rec["worktree"]).resolve()
    assert rec["metrics"]["exit_code"] == 0 and rec["metrics"]["phase"] == "build_writing"
    assert not list(Path(rec["worktree"]).glob("lattice-build-llm-*"))


def test_a_failing_metrics_hook_is_a_warning(repo: Path) -> None:
    (repo / "hook-fail.flag").write_text("1")
    git(repo, "add", "hook-fail.flag")
    git(repo, "commit", "-q", "-m", "fail the hook")
    rb = rebuilder(repo, metrics_hook=f"{sys.executable} hook.py")
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert rec["status"] == "passed"
    assert rec["metrics"]["exit_code"] == 3
    assert rec["warnings"] == ["the metrics hook exited 3: "]
    # and a hook that does not exist at all
    rb2 = rebuilder(repo, metrics_hook="./no-such-hook --x")
    rec2 = finish(rb2, rb2.start([], scope={"area": "reading"})["id"])
    assert rec2["status"] == "passed" and rec2["warnings"][0].startswith(
        "the metrics hook exited 127"
    )


def test_a_gave_up_scoped_build_runs_the_hook_too(repo: Path) -> None:
    rb = rebuilder(repo, metrics_hook=f"{sys.executable} hook.py")
    rec = finish(rb, rb.start([], scope={"area": "writing"}, max_rounds=1)["id"])
    assert rec["status"] == "gave_up"
    argv = rec["metrics"]["command"]
    assert "--phase build_writing" in argv and "gave_up in round 1 of 1" in argv
    assert (Path(rec["worktree"]) / "metrics" / "phases.jsonl").is_file()


FAIL_REPORT = {
    "verdict": "fail",
    "layers": "D",
    "counts": {"D": 2},
    "findings": [
        {
            "layer": "D",
            "verdict": "silent",
            "rule": "scenario: create_a_note",
            "case": "create_a_note / step 1",
            "details": {"problems": ["status: expected 201, got 404"]},
            "key": "D:POST /notes:create_a_note / step 1",
        },
        {
            "layer": "D",
            "verdict": "loud",
            "rule": "scenario: no_token",
            "case": "no_token / step 1",
            "details": {"problems": ["status: expected 401, got 500"]},
            "key": "D:GET /notes/1:no_token / step 1",
        },
    ],
    "cases": {},
    "notes": [],
}
PASS_REPORT = {**FAIL_REPORT, "verdict": "pass", "counts": {"D": 0}, "findings": []}
JSON_GATE = f"""#!/bin/sh
# no finding lines on stdout: the findings are in metrics/last-check.json
mkdir -p metrics
if [ -f w.txt ]; then
  echo '{json.dumps(PASS_REPORT)}' > metrics/last-check.json
  echo '{{"verdict": "pass"}}'
  exit 0
fi
echo '{json.dumps(FAIL_REPORT)}' > metrics/last-check.json
echo '{{"verdict": "fail"}}'
exit 1
"""


def test_findings_json_is_parsed_instead_of_text(repo: Path) -> None:
    (repo / "jgate.sh").write_text(JSON_GATE)
    (repo / "jgate.sh").chmod(0o755)
    git(repo, "add", "jgate.sh")
    git(repo, "commit", "-q", "-m", "json gate")
    rb = rebuilder(
        repo,
        gates=["./jgate.sh"],
        findings_json="metrics/last-check.json",
        metrics_hook=f"{sys.executable} hook.py",
    )
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert rec["status"] == "passed", rec
    b = rec["baseline"]
    assert b["counts"] == {"D": 2}
    assert [(f["target"], f["in_scope"], f["rules"]) for f in b["findings"]] == [
        ("scenario:create_a_note", True, ["R2"]),
        ("scenario:no_token", False, ["R6"]),
    ]
    assert b["findings"][0]["title"] == (
        "D silent create_a_note / step 1: status: expected 201, got 404"
    )
    argv = json.loads(git(repo, "show", "build/notes:metrics/phases.jsonl"))["argv"]
    check_json = argv[argv.index("--check-json") + 1]
    assert check_json.endswith("metrics/last-check.json")
    assert rec["warnings"] == []


def test_findings_json_not_written_falls_back_to_text_with_a_warning(repo: Path) -> None:
    rb = rebuilder(repo, findings_json="metrics/last-check.json")
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert rec["status"] == "passed"
    assert rec["warnings"] == [
        "[rebuild] findings_json metrics/last-check.json was not written by the gates; "
        "findings were parsed from their text output"
    ]


def test_usage_is_kept_when_a_scoped_round_crashes(
    repo: Path, fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan(fake, {"files": {"w.txt": "w\n"}})

    def boom(self: Rebuilder, *_a: Any) -> Any:
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(Rebuilder, "_judge", boom)
    rb = rebuilder(repo, fake, agent="claude")
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert rec["status"] == "gave_up" and rec["error"]["kind"] == "internal"
    (r1,) = rec["rounds"]
    assert r1["error"]["message"] == "RuntimeError: judge exploded" and r1["check"] is None
    assert r1["agent"]["cache_read_input_tokens"] == 300
    u = rec["usage"]
    assert u["cost_usd_list_estimate"] == 0.25 and u["cache_creation_input_tokens"] == 40
    assert u["input_tokens"] == 10 and u["output_tokens"] == 20


def test_changes_and_scope_together(repo: Path) -> None:
    change = {"id": "c1", "request": "Notes can be starred.", "status": "accepted",
              "rules": {"added": [], "changed": [], "removed": []}}  # fmt: skip
    rb = rebuilder(repo)
    rec = finish(rb, rb.start([change], scope={"area": "writing"})["id"])
    assert rec["status"] == "passed" and rec["changes"] == ["c1"]
    assert rec["commit"]["message"].startswith("build: Writing; Notes can be starred.\n")
    assert rb.build_of("c1")["state"] == "built"


def test_an_integration_branch_checked_out_is_409(repo: Path) -> None:
    git(repo, "branch", "build/notes")
    git(repo, "checkout", "-q", "build/notes")
    with pytest.raises(RebuildError) as e:
        rebuilder(repo).start([], scope={"area": "writing"})
    assert e.value.status == 409 and "is checked out in" in e.value.detail
    git(repo, "checkout", "-q", "main")
    rb = rebuilder(repo, integration_branch="build/custom")
    rec = finish(rb, rb.start([], scope={"area": "writing"})["id"])
    assert rec["integration_branch"] == "build/custom"
    assert git(repo, "rev-parse", "build/custom") == rec["commit"]["sha"]


# ---------------------------------------------------------------- target branches


def _hold_writing(repo: Path, seconds: float) -> None:
    stub = json.loads((repo / "stub-rebuild.json").read_text())
    stub["scopes"]["writing"]["rounds"][0]["hold"] = seconds
    (repo / "stub-rebuild.json").write_text(json.dumps(stub))
    git(repo, "commit", "-qam", "hold")


def test_branches_listing(repo: Path) -> None:
    git(repo, "branch", "feature")
    rb = rebuilder(repo)
    got = rb.branches()
    head = git(repo, "rev-parse", "HEAD")
    assert got["current_branch"] == "main" and got["protected"] == ["main", "master"]
    assert got["default_target"] == "build/notes"
    assert got["branches"] == [
        {"name": "feature", "head": head, "current": False, "protected": False,
         "checked_out": None, "ahead_of_base": 0, "is_integration": False,
         "spec": {"in_sync": True, "files": [], "head": head}},  # API v1.12
        {"name": "main", "head": head, "current": True, "protected": True,
         "checked_out": str(repo.resolve()), "ahead_of_base": 0, "is_integration": False,
         "spec": None},
    ]  # fmt: skip
    finish(rb, rb.start([], scope={"area": "writing"})["id"])
    by = {b["name"]: b for b in rb.branches()["branches"]}
    assert by["build/notes"]["is_integration"] and by["build/notes"]["ahead_of_base"] == 1
    assert "rebuild/writing-1" in by
    assert rebuilder(repo, protected_branches=["release"]).branches()["protected"] == ["release"]


def test_target_branch_refusals_leave_branches_alone(repo: Path) -> None:
    git(repo, "branch", "feature")
    head = git(repo, "rev-parse", "HEAD")
    refs = git(repo, "for-each-ref")
    rb = rebuilder(repo)
    w = {"area": "writing"}
    for branch, status, code in (
        ({"name": "nope"}, 404, "no_branch"),
        ({"name": "feature", "create": True}, 409, "branch_exists"),
        ({"name": "new", "create": True, "from": "nosuchref"}, 404, "no_ref"),
        ({"name": "bad..name", "create": True}, 422, "branch_name_invalid"),
        ({"name": "main"}, 409, "branch_protected"),
        ({"name": "x", "create": "yes"}, 422, None),
        ({"name": "feature", "from": "HEAD"}, 422, None),
        ("feature", 422, None),
    ):
        with pytest.raises(RebuildError) as e:
            rb.start([], scope=w, branch=branch)
        assert (e.value.status, e.value.code) == (status, code), branch
    assert git(repo, "for-each-ref") == refs and git(repo, "rev-parse", "feature") == head
    assert rb.list() == []


def test_a_new_branch_from_a_ref_and_an_existing_branch(repo: Path) -> None:
    first = git(repo, "rev-parse", "HEAD")
    (repo / "later.txt").write_text("later\n")
    git(repo, "add", "later.txt")
    git(repo, "commit", "-q", "-m", "later")
    rb = rebuilder(repo)
    new = {"name": "feat/a", "create": True, "from": first}
    rec = finish(rb, rb.start([], scope={"area": "writing"}, branch=new)["id"])
    assert rec["status"] == "passed"
    assert (rec["target_branch"], rec["created_branch"], rec["base_ref"]) == ("feat/a", True, first)
    assert rec["commit_range"] == {"from": first, "to": rec["commit"]["sha"]}
    assert git(repo, "rev-parse", "feat/a") == rec["commit"]["sha"]
    assert git(repo, "rev-parse", f"{rec['commit']['sha']}^") == first
    assert rec["merged_head"] is None, "an explicit target never gets the owner's HEAD merged"
    assert git_rc(repo, "show-ref", "--verify", "-q", "refs/heads/build/notes") == 1
    # the same branch again, existing now: its areas are its own
    rec2 = finish(rb, rb.start([], scope={"area": "reading"}, branch={"name": "feat/a"})["id"])
    assert rec2["status"] == "passed" and rec2["created_branch"] is False
    assert rec2["base_ref"] == "feat/a" and rec2["integration_base"] == rec["commit"]["sha"]
    feat = {a["id"]: (a["failing"], a["status"]) for a in rb.areas(branch="feat/a")}
    assert feat["writing"] == (0, "built") and feat["reading"] == (0, "built")
    default = {a["id"]: (a["failing"], a["status"]) for a in rb.areas()}
    assert default["writing"] == (None, "not_built"), "build/notes has no results"
    assert rb.areas(branch="feat/a")[0]["target_branch"] == "feat/a"
    # a new commit on the branch makes its recorded results stale: unknown
    wt = Path(rec2["worktree"])
    (wt / "extra.txt").write_text("x")
    git(wt, "add", "extra.txt")
    git(wt, "commit", "-q", "-m", "extra")
    git(repo, "update-ref", "refs/heads/feat/a", git(wt, "rev-parse", "HEAD"))
    assert {a["id"]: a["failing"] for a in rb.areas(branch="feat/a")}["writing"] is None


def test_the_checked_out_branch_needs_confirmation_and_a_clean_tree(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "work")
    rb = rebuilder(repo)
    w = {"area": "writing"}
    with pytest.raises(RebuildError) as e:
        rb.start([], scope=w, branch={"name": "work"})
    assert (e.value.status, e.value.code) == (409, "branch_checked_out")
    (repo / "dirty.txt").write_text("uncommitted\n")
    with pytest.raises(RebuildError) as e:
        rb.start([], scope=w, branch={"name": "work"}, confirm_unsafe_branch=True)
    assert (e.value.status, e.value.code) == (409, "tree_dirty")
    (repo / "dirty.txt").unlink()
    head = git(repo, "rev-parse", "HEAD")
    rec = finish(
        rb, rb.start([], scope=w, branch={"name": "work"}, confirm_unsafe_branch=True)["id"]
    )
    assert rec["status"] == "passed", rec
    sha = rec["commit"]["sha"]
    # fast-forwarded in the owner's worktree: HEAD, index and files follow, nothing else
    assert git(repo, "rev-parse", "HEAD") == sha and git(repo, "rev-parse", f"{sha}^") == head
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "work"
    assert git(repo, "status", "--porcelain") == "" and (repo / "w.txt").read_text() == "w\n"
    assert "fast-forwarded" in rec["merge_hint"]


def test_a_protected_branch_with_confirmation_moves_by_update_ref(repo: Path) -> None:
    git(repo, "branch", "master")
    rb = rebuilder(repo)
    rec = finish(rb, rb.start([], scope={"area": "writing"}, branch={"name": "master"},
                              confirm_unsafe_branch=True)["id"])  # fmt: skip
    assert rec["status"] == "passed" and git(repo, "rev-parse", "master") == rec["commit"]["sha"]
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"


def test_a_branch_checked_out_in_another_worktree_is_always_refused(
    repo: Path, tmp_path: Path
) -> None:
    git(repo, "worktree", "add", "-q", "-b", "elsewhere", str(tmp_path / "wt2"))
    try:
        with pytest.raises(RebuildError) as e:
            rebuilder(repo).start([], scope={"area": "writing"}, branch={"name": "elsewhere"},
                                  confirm_unsafe_branch=True)  # fmt: skip
        assert (e.value.status, e.value.code) == (409, "branch_checked_out")
    finally:
        git(repo, "worktree", "remove", "--force", str(tmp_path / "wt2"))


def test_a_target_that_moves_during_the_build_gives_up_and_keeps_the_commit(repo: Path) -> None:
    _hold_writing(repo, 2)
    git(repo, "branch", "feature")
    rb = rebuilder(repo)
    rid = rb.start([], scope={"area": "writing"}, branch={"name": "feature"})["id"]
    (repo / "meanwhile.txt").write_text("m")
    git(repo, "add", "meanwhile.txt")
    git(repo, "commit", "-q", "-m", "meanwhile")
    moved = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/heads/feature", moved)
    rec = finish(rb, rid)
    assert rec["status"] == "gave_up" and rec["error"]["kind"] == "branch_moved"
    assert git(repo, "rev-parse", "feature") == moved, "nothing overwritten"
    assert git(repo, "rev-parse", rec["branch"]) == rec["commit"]["sha"], "nothing lost"
    assert "NOT advanced" in rec["merge_hint"]


# ---------------------------------------------------------------- the routes


@pytest.fixture()
def hub(repo: Path, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    with (repo / "studio.toml").open("a") as f:
        f.write('\n[rebuild]\ngates = ["./gate.sh"]\nagent = "stub"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rebuild config")
    other = make_project(tmp_path / "other")
    srv = make_hub_server(discover([repo, other]), 0, generator_factory=lambda _m: StubGenerator())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", repo
    srv.shutdown()
    srv.server_close()


def test_routes_under_the_project_prefix(hub) -> None:
    base, repo = hub
    p = base + "/p/notes/api"
    projects = request("GET", base + "/api/projects")[1]
    assert [(x["name"], x["capabilities"]["build_from_spec"]) for x in projects] == [
        ("notes", True),
        ("other", False),
    ]
    head = git(repo, "rev-parse", "HEAD")
    status, areas = request("GET", p + "/areas")
    assert status == 200
    assert [(a["id"], a["rules"], a["scenarios"], a["failing"], a["status"]) for a in areas] == [
        ("notes-app", ["R1"], 0, None, "not_built"),
        ("writing", ["R2", "R3"], 2, None, "not_built"),
        ("reading", ["R4", "R5", "R6", "R7"], 4, None, "not_built"),
        ("all", ["R1", "R2", "R3", "R4", "R5", "R6", "R7"], 6, None, "not_built"),
    ]
    assert request("GET", base + "/api/areas")[1] == areas, "the default project"
    for bad in ({"scope": "writing"}, {"scope": {"area": 3}}, {"scope": {}},
                {"scope": "all", "changes": []}, {"scope": "all", "max_rounds": 0}):  # fmt: skip
        assert request("POST", p + "/rebuild", bad)[0] == 422, bad
    assert request("POST", p + "/rebuild", {"scope": {"area": "nope"}}) == (
        404,
        {"detail": "no area nope"},
    )
    assert request("POST", p + "/rebuild", {"scope": {"rules": ["R42"]}}) == (
        404,
        {"detail": "no rule R42"},
    )
    assert request("POST", p + "/rebuild", {"scope": {"area": "writing"}}) == (200, {"id": "b1"})
    rec = request("GET", p + "/rebuild/b1?wait=30")[1]
    assert rec["status"] == "passed" and rec["integration_branch"] == "build/notes"
    assert rec["scope"]["id"] == "writing" and len(rec["rounds"]) == 2
    areas = request("GET", p + "/areas")[1]
    assert [(a["id"], a["failing"], a["status"]) for a in areas] == [
        ("notes-app", 0, "built"),
        ("writing", 0, "built"),
        ("reading", 1, "partial"),
        ("all", 1, "partial"),
    ]
    assert request("POST", p + "/rebuild", {"scope": {"area": "writing"}}) == (
        409,
        {"detail": "nothing to build: the checks in this scope already pass"},
    )
    assert request("GET", p + "/model")[1]["project"]["git_head"] == head[:7]
    assert git(repo, "rev-parse", "build/notes") == rec["commit"]["sha"]
    assert request("GET", base + "/p/other/api/areas")[1][-1]["id"] == "all"
    assert request("GET", base + "/p/other/api/rebuild/b1")[0] == 404, "per project"


def test_branch_routes_and_error_codes(hub) -> None:
    base, repo = hub
    p = base + "/p/notes/api"
    got = request("GET", p + "/branches")[1]
    assert got["current_branch"] == "main" and got["default_target"] == "build/notes"
    assert [b["name"] for b in got["branches"]] == ["main"]
    assert request("POST", p + "/rebuild", {"scope": "all", "branch": {"name": "main"}}) == (
        409,
        {
            "detail": "branch main is protected; send confirm_unsafe_branch: true to build "
            "onto it (fast-forward only, with a clean worktree)",
            "code": "branch_protected",
        },
    )
    assert request("POST", p + "/rebuild", {"changes": ["c1"], "branch": {"name": "x"}}) == (
        422,
        {"detail": "branch needs a scope"},
    )
    assert request("POST", p + "/rebuild", {"scope": "all", "confirm_unsafe_branch": 1})[0] == 422
    body = {"scope": {"area": "writing"}, "branch": {"name": "feat", "create": True}}
    assert request("POST", p + "/rebuild", body)[1] == {"id": "b1"}
    rec = request("GET", p + "/rebuild/b1?wait=30")[1]
    assert rec["status"] == "passed" and rec["target_branch"] == "feat"
    by = {a["id"]: a["status"] for a in request("GET", p + "/areas?branch=feat")[1]}
    assert by["writing"] == "built"
    assert request("GET", p + "/areas")[1][1]["failing"] is None
    assert [b["name"] for b in request("GET", p + "/branches")[1]["branches"]] == [
        "feat",
        "main",
        "rebuild/writing-1",
    ]


def test_the_mock_serves_areas_branches_and_scoped_records() -> None:
    """Mock parity (API v1.8): the record and area shapes of the real server."""
    from lattice.studio.mock_server import make_server as make_mock

    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    b = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert all(
            p["capabilities"]["build_from_spec"] for p in request("GET", b + "/api/projects")[1]
        )
        areas = request("GET", b + "/api/areas")[1]
        assert areas[-1]["id"] == "all" and len(areas) == 9
        assert set(areas[1]) == {"id", "title", "rules", "scenarios", "checks", "targets",
                                 "failing", "status", "target_branch"}  # fmt: skip
        br = request("GET", b + "/api/branches")[1]
        assert set(br) == {"branches", "current_branch", "protected", "default_target"}
        assert request("POST", b + "/api/rebuild", {"scope": {"area": "nope"}})[0] == 404
        no = request("POST", b + "/api/rebuild", {"scope": "all", "branch": {"name": "main"}})
        assert no[0] == 409 and no[1]["code"] == "branch_protected"
        rid = request("POST", b + "/api/rebuild", {"scope": {"area": "roles"}})[1]["id"]
        for _ in range(60):
            rec = request("GET", f"{b}/api/rebuild/{rid}")[1]
            if rec["status"] == "passed":
                break
        assert rec["status"] == "passed"
        assert {"scope", "baseline", "target_branch", "created_branch", "base_ref",
                "commit_range", "integration_branch", "warnings", "usage"} <= set(rec)  # fmt: skip
        assert [r["scope_passing"] for r in rec["rounds"]] == [0, rec["rounds"][0]["scope_total"]]
        by = {a["id"]: a["status"] for a in request("GET", b + "/api/areas")[1]}
        assert by["roles"] == "built" and by["ticket-2-job-configs"] != "built"
        assert request("POST", b + "/api/rebuild", {"scope": {"area": "roles"}})[0] == 409
    finally:
        srv.shutdown()
        srv.server_close()
