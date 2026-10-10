"""Build plans (API.md v1.9, lattice/studio/plan.py): the validator, the fallback plan (also
on Streak's real trace, read-only), the planner's argv and isolation against a FAKE
``claude``, and the runner's levels, merges, full gates, regressions, blocked nodes,
conflicts, concurrency, stop, resume, branch rules and metrics hook, with the TEST-ONLY stub
planner and stub agent and trivial gate scripts. No real LLM call, no API key."""

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

from lattice.studio import areas as areas_mod
from lattice.studio.plan import (
    PlanError,
    PlanManager,
    build_planner_argv,
    epg_prompt,
    fallback_plan,
    overlap,
    parse_planner_text,
    planner_prompt,
    resolve_plan_scope,
    validate_plan,
)
from lattice.studio.rebuild import Rebuilder, RebuildError
from lattice.studio.server import StubGenerator, make_hub_server
from tests.studio.test_build_scope import git, write_trace
from tests.studio.test_server import make_project, request

STREAK_TRACE = Path("/Users/vivekgade/Documents/streak/spec.trace.json")

# R1 notes-app (preamble), R2-R3 writing, R4-R7 reading (tests/studio/test_build_scope.py)
GATE = """#!/bin/sh
n=0
[ -f w.txt ] || n=$((n+1))
[ -f r.txt ] || n=$((n+1))
if [ -f a.txt ] && [ -f b.txt ]; then n=$((n+1)); fi
if [ "$n" = 0 ]; then echo "PASS: 0 findings (G 0)"; exit 0; fi
echo "FAIL: $n findings (G $n)"
[ -f w.txt ] || echo "FAIL w.txt is missing (scenario:create_a_note)"
[ -f r.txt ] || echo "FAIL r.txt is missing (scenario:read_own_and_other)"
if [ -f a.txt ] && [ -f b.txt ]; then echo "FAIL a.txt and b.txt clash (scenario:no_token)"; fi
exit 1
"""


def _r(files: dict[str, Any], hold: float = 0, summary: str = "") -> dict[str, Any]:
    return {"files": files, "hold": hold, "summary": summary}


STUB = {
    "rounds": [_r({"x.txt": "1"})],
    "scopes": {
        "scaffold": {"rounds": [_r({"scaffold.txt": "s\n"})]},
        "w": {"rounds": [_r({"w.txt": "w\n"})]},
        "r": {"rounds": [_r({"r.txt": "r\n"})]},
        "w-slow": {"rounds": [_r({"w.txt": "w\n"}, hold=1.5)]},
        "r-slow": {"rounds": [_r({"r.txt": "r\n"}, hold=1.5)]},
        "a": {"rounds": [_r({"w.txt": "w\n", "a.txt": "a\n"})]},
        "b": {"rounds": [_r({"r.txt": "r\n", "b.txt": "b\n"})]},
        "x1": {"rounds": [_r({"w.txt": "w\n", "shared.txt": "one\n"})]},
        "x2": {"rounds": [_r({"r.txt": "r\n", "shared.txt": "two\n"})]},
        "broken": {"rounds": [_r({"nothing.txt": "1"}), _r({"nothing.txt": "2"})]},
        "hold": {"rounds": [_r({"r.txt": "r\n"}, hold=30)]},
    },
}

HOOK = """import json, os, sys
args = sys.argv[1:]
llm = json.load(open(args[args.index("--llm-json") + 1]))
os.makedirs("metrics", exist_ok=True)
with open("metrics/phases.jsonl", "a") as f:
    f.write(json.dumps({"phase": args[args.index("--phase") + 1], "llm": llm}) + "\\n")
"""

FAKE_PLANNER = """#!{python}
import json, os, sys
d = os.environ["FAKE_PLAN_DIR"]
n_path = os.path.join(d, "count")
n = int(open(n_path).read()) + 1 if os.path.exists(n_path) else 1
open(n_path, "w").write(str(n))
env = {{k: v for k, v in os.environ.items() if k.startswith(("ANTHROPIC", "CLAUDE_CODE"))}}
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps({{"n": n, "argv": sys.argv[1:], "cwd": os.getcwd(), "env": env}}) + "\\n")
answers = json.load(open(os.path.join(d, "answers.json")))
a = answers[min(n, len(answers)) - 1]
if a.get("exit") is not None:
    sys.stderr.write(a.get("stderr", "boom"))
    sys.exit(a["exit"])
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s-1"}}), flush=True)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
      "result": a["text"], "session_id": "s-1", "total_cost_usd": 0.5, "duration_ms": 900,
      "num_turns": 4, "usage": {{"input_tokens": 1000, "output_tokens": 200,
      "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 50}}}}), flush=True)
"""


def node(
    nid: str,
    kind: str = "area",
    scope: Any = None,
    deps=(),
    tier: str = "mid",
    files=(),
    tables=(),
    packages=(),
) -> dict[str, Any]:
    return {
        "id": nid,
        "title": nid,
        "kind": kind,
        "scope": scope if scope is not None else {"rules": []},
        "depends_on": [{"id": d, "why": f"needs {d}"} for d in deps],
        "tier": tier,
        "touches": {"tables": list(tables), "packages": list(packages), "files": list(files)},
        "why": f"node {nid}",
    }


def integ(*deps: str) -> dict[str, Any]:
    return node("integration", "integration", "all", deps, "frontier")


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
    (root / ".git" / "info" / "exclude").write_text("stub-plan.json\n")
    return root


@pytest.fixture(autouse=True)
def _no_stray_worktrees(repo: Path) -> Iterator[None]:
    yield
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree ") and "/.lattice/rebuilds/" in line:
            subprocess.run(["rm", "-rf", line.removeprefix("worktree ")], check=False)
    git(repo, "worktree", "prune")


def stub_plans(root: Path, plans: dict[str, Any]) -> None:
    (root / "stub-plan.json").write_text(json.dumps({"scopes": plans}))


def manager(
    repo: Path, claude_bin: str = "claude", env: dict | None = None, **table: Any
) -> PlanManager:
    t = {"gates": ["./gate.sh"], "max_rounds": 2, "agent": "stub", "planner": "stub", **table}
    rb = Rebuilder(
        repo,
        t,
        name="notes",
        model="sonnet",
        claude_bin=claude_bin,
        env=env if env is not None else dict(os.environ),
    )
    return PlanManager(rb)


def ready(pm: PlanManager, body: dict[str, Any]) -> dict[str, Any]:
    pid = pm.create(body)["id"]
    rec = pm.get(pid, wait=30)
    assert rec["status"] == "ready", rec
    return rec


def run(pm: PlanManager, pid: str) -> dict[str, Any]:
    pm.approve(pid)
    pm.run(pid)
    rec = pm.get(pid, wait=60)
    assert rec["status"] not in ("running", "planning"), rec
    return rec


def owner(repo: Path) -> tuple[str, str, str]:
    return (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "symbolic-ref", "HEAD"),
        git(repo, "status", "--porcelain", "--untracked-files=all"),
    )


def areas_of(repo: Path) -> list[dict[str, Any]]:
    return areas_mod.derive_areas(json.loads((repo / "spec.trace.json").read_text()))


def check(doc: Any, repo: Path, scope: Any = "all", max_parallel: int = 2, **kw: Any) -> dict:
    areas = areas_of(repo)
    trace = json.loads((repo / "spec.trace.json").read_text())
    return validate_plan(
        doc,
        scope=resolve_plan_scope(scope, areas),
        areas=areas,
        max_parallel=max_parallel,
        trace_links=trace["links"],
        **kw,
    )


# ---------------------------------------------------------------- the validator matrix


def test_scope_resolution(repo: Path) -> None:
    areas = areas_of(repo)
    assert [a["id"] for a in areas] == ["notes-app", "writing", "reading", "all"]
    s = resolve_plan_scope("all", areas)
    assert s["rules"] == ["R1", "R2", "R3", "R4", "R5", "R6", "R7"] and s["slug"] == "all"
    s = resolve_plan_scope({"areas": ["reading", "writing"]}, areas)
    assert s["areas"] == ["writing", "reading"] and s["slug"] == "areas-reading-writing"
    s = resolve_plan_scope({"rules": ["R4", "R2"]}, areas)
    assert s["rules"] == ["R2", "R4"] and s["areas"] == ["writing", "reading"]
    for bad, status in (
        ("x", 422),
        ({"area": "writing"}, 422),
        ({"areas": []}, 422),
        ({"areas": ["nope"]}, 404),
        ({"rules": ["R9"]}, 404),
        ({"rules": ["R1", "R1"]}, 422),
    ):
        with pytest.raises(PlanError) as e:
            resolve_plan_scope(bad, areas)
        assert e.value.status == status, bad


def test_a_valid_plan_levels_and_studio_gates(repo: Path) -> None:
    doc = {
        "nodes": [
            node("scaffold", "scaffold", files=["app/router.py"]),
            node("pre", scope={"area": "notes-app"}, deps=["scaffold"]),
            node("w", scope={"area": "writing"}, deps=["scaffold"], tier="mid", packages=["app/w"]),
            node("r", scope={"area": "reading"}, deps=["w"], tier="frontier", packages=["app/r"]),
            {**integ("pre", "r"), "gate": {"cmd": "true"}},
        ]
    }
    v = check(doc, repo)
    assert v["ok"], v
    assert v["levels"] == [["scaffold"], ["pre", "w"], ["r"], ["integration"]]
    by = {n["id"]: n for n in v["nodes"]}
    assert by["r"]["model"] == "opus" and by["w"]["model"] == "sonnet"
    assert by["w"]["gate"]["kind"] == "scoped" and by["w"]["gate"]["decided_by"] == "studio"
    assert by["w"]["gate"]["rules"] == ["R2", "R3"]
    assert by["integration"]["gate"]["kind"] == "full"
    assert by["integration"]["scope"]["rules"] == ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
    assert {
        "kind": "gate",
        "node": "integration",
        "detail": "the planner's gate was ignored: Studio sets every gate",
    } in v["auto_edits"]
    assert all(not e["auto"] for e in v["edges"])


@pytest.mark.parametrize(
    ("doc", "issue"),
    [
        (
            {
                "nodes": [
                    node("a", scope={"area": "notes-app"}, deps=["c"]),
                    node("b", scope={"area": "writing"}, deps=["a"]),
                    node("c", scope={"area": "reading"}, deps=["b"]),
                ]
            },
            "the dependencies have a cycle: a -> c -> b -> a",
        ),
        (
            {
                "nodes": [
                    node("a", scope={"area": "notes-app"}),
                    node("b", scope={"area": "writing"}),
                    node("c", scope={"rules": ["R2", "R4", "R5", "R6", "R7"]}),
                ]
            },
            "R2 is covered by 2 nodes (b, c)",
        ),
        (
            {
                "nodes": [
                    node("a", scope={"area": "notes-app"}),
                    node("b", scope={"area": "writing"}),
                    node("c", scope={"rules": ["R4", "R5", "R6"]}),
                ]
            },
            "R7 is covered by no node",
        ),
        (
            {
                "nodes": [
                    node("a", scope={"area": "notes-app"}),
                    node("a", scope={"area": "writing"}),
                ]
            },
            "node id a is not unique",
        ),
        (
            {"nodes": [node("a", scope={"area": "notes-app"}, deps=["zz"])]},
            "node a depends on zz, which is not a node",
        ),
        (
            {"nodes": [node("s", "scaffold", scope={"rules": ["R2"]})]},
            "the scaffold s covers rules that are not infrastructure: R2",
        ),
        (
            {"nodes": [node("a", scope={"rules": ["R1", "R99"]})]},
            "node a covers R99, outside the plan scope",
        ),
        ({"nodes": [node("Bad Id")]}, "node 1: id must match [a-z0-9][a-z0-9_-]* (got 'Bad Id')"),
        (
            {"nodes": [integ(), node("a", scope={"area": "notes-app"}, deps=["integration"])]},
            "node a depends on the integration node integration",
        ),
        ({"nodes": []}, 'the plan must be an object with a non-empty "nodes" list'),
    ],
)
def test_invalid_plans(repo: Path, doc: dict, issue: str) -> None:
    v = check(doc, repo)
    assert not v["ok"]
    assert issue in v["issues"], v["issues"]


def test_overlapping_siblings_are_serialised(repo: Path) -> None:
    doc = {
        "nodes": [
            node("pre", scope={"area": "notes-app"}, tables=["Notes"]),
            node("w", scope={"area": "writing"}, packages=["app/w"]),
            node("r", scope={"area": "reading"}, tables=["notes"]),
            integ("pre", "w", "r"),
        ]
    }
    v = check(doc, repo, max_parallel=8)
    assert v["ok"], v
    # r and pre write one table (case-insensitive): r runs after pre (document order)
    assert v["levels"] == [["pre", "w"], ["r"], ["integration"]]
    by = {n["id"]: n for n in v["nodes"]}
    assert by["r"]["depends_on"] == ["pre"]
    assert by["r"]["serialised_because"] == {
        "node": "pre",
        "overlap": {"tables": ["Notes"], "packages": [], "files": []},
    }
    ser = [e for e in v["auto_edits"] if e["kind"] == "serialise"]
    assert [(e["from"], e["to"]) for e in ser] == [("pre", "r")]
    assert [e["kind"] for e in v["edges"] if e["to"] == "r"] == ["serialised"]
    # a file inside a sibling's package is shared code too
    doc["nodes"][0]["touches"]["tables"] = []
    doc["nodes"][2]["touches"] = {"tables": [], "packages": [], "files": ["app/w/read.py"]}
    v = check(doc, repo, max_parallel=8)
    by = {n["id"]: n for n in v["nodes"]}
    assert by["r"]["serialised_because"] == {
        "node": "w",
        "overlap": {"tables": [], "packages": [], "files": ["app/w/read.py"]},
    }
    # with no overlap they stay siblings
    doc["nodes"][2]["touches"]["files"] = ["app/r/read.py"]
    assert check(doc, repo, max_parallel=8)["levels"] == [["pre", "w", "r"], ["integration"]]


def test_path_overlap_rules() -> None:
    t = {"tables": [], "packages": [], "files": []}
    assert overlap({**t, "packages": ["internal/habits"]}, {**t, "packages": ["internal"]})[
        "packages"
    ] == ["internal"]
    assert (
        overlap({**t, "packages": ["internal/habit"]}, {**t, "packages": ["internal/habits"]})[
            "packages"
        ]
        == []
    )
    assert overlap({**t, "files": ["a/b.go"]}, {**t, "files": ["a/b.go"]})["files"] == ["a/b.go"]


def test_max_parallel_caps_a_level(repo: Path) -> None:
    doc = {
        "nodes": [
            node("pre", scope={"area": "notes-app"}),
            node("w", scope={"area": "writing"}),
            node("r", scope={"area": "reading"}),
            integ("pre", "w", "r"),
        ]
    }
    assert check(doc, repo, max_parallel=3)["levels"] == [["pre", "w", "r"], ["integration"]]
    v = check(doc, repo, max_parallel=1)
    assert v["levels"] == [["pre"], ["w"], ["r"], ["integration"]]
    moved = [(e["node"], e["from"], e["to"]) for e in v["auto_edits"] if e["kind"] == "level"]
    assert moved[0] == ("w", 0, 1) and ("r", 1, 2) in moved


def test_unknown_tier_structure_edges_and_touches(repo: Path) -> None:
    doc = {
        "nodes": [
            node("s", "scaffold"),
            node("pre", scope={"area": "notes-app"}, tier="ultra"),
            node("w", scope={"area": "writing"}, files=["/etc/passwd", "../x", "./app/w.py", 7]),
            node("r", scope={"area": "reading"}),
        ]
    }
    v = check(doc, repo, models={"mid": "sonnet-x"}, model_overrides={"r": "my-model"})
    assert v["ok"], v
    by = {n["id"]: n for n in v["nodes"]}
    assert by["pre"]["tier"] == "mid" and by["pre"]["model"] == "sonnet-x"
    assert by["r"]["model"] == "my-model"
    assert by["w"]["touches"]["files"] == ["app/w.py"]
    kinds = [(e["kind"], e.get("node"), e.get("from"), e.get("to")) for e in v["auto_edits"]]
    assert ("tier", "pre", "ultra", "mid") in kinds
    assert ("node", "integration", None, None) in kinds  # added: every plan ends with it
    assert ("edge", None, "s", "pre") in kinds  # the scaffold runs first
    assert ("edge", None, "r", "integration") in kinds
    assert any(e["kind"] == "touches" and e["node"] == "w" for e in v["auto_edits"])
    assert v["levels"] == [["s"], ["pre", "w"], ["r"], ["integration"]]  # cap 2


def test_parse_planner_text() -> None:
    assert parse_planner_text('```json\n{"nodes": []}\n```') == {"nodes": []}
    assert parse_planner_text('Here: {"nodes": [1]} done') == {"nodes": [1]}
    for bad in ("no json", "{oops}", "[1, 2]"):
        with pytest.raises(ValueError):
            parse_planner_text(bad)


# ---------------------------------------------------------------- the fallback plan


def test_fallback_shape(repo: Path) -> None:
    areas = areas_of(repo)
    doc = fallback_plan(resolve_plan_scope("all", areas), areas)
    assert [(n["id"], [d["id"] for d in n["depends_on"]]) for n in doc["nodes"]] == [
        ("notes-app", []),
        ("writing", ["notes-app"]),
        ("reading", ["writing"]),
        ("integration", ["reading"]),
    ]
    doc = fallback_plan(resolve_plan_scope({"rules": ["R2", "R4"]}, areas), areas)
    assert [n["scope"] for n in doc["nodes"]] == [{"rules": ["R2"]}, {"rules": ["R4"]}, "all"]


@pytest.mark.skipif(not STREAK_TRACE.is_file(), reason="the Streak project is not on this host")
def test_fallback_plan_on_streaks_real_trace() -> None:
    trace = json.loads(STREAK_TRACE.read_text())
    areas = areas_mod.derive_areas(trace)
    scope = resolve_plan_scope("all", areas)
    assert len(scope["rules"]) == 148
    v = validate_plan(
        fallback_plan(scope, areas),
        scope=scope,
        areas=areas,
        max_parallel=2,
        trace_links=trace["links"],
    )
    assert v["ok"], v["issues"]
    nodes = v["nodes"]
    print("\nfallback plan for Streak (148 rules):")
    for n in nodes:
        print(
            f"  L{n['level']:<2} {n['id']:<44} {n['kind']:<11} {n['tier']:<8} "
            f"{len(n['scope']['rules']):>3} rules  <- {', '.join(n['depends_on']) or '-'}"
        )
    assert nodes[0]["kind"] == "scaffold" and nodes[0]["level"] == 0
    titles = {a["id"]: a["title"] for a in areas}
    infra = {
        r
        for a in areas
        if a["id"] != "all"
        and areas_mod.slug(a["title"])
        in (
            "data-model-table-and-column-names-are-fixed",
            "errors-and-versioning",
            "time-timezones-and-periods",
            "lists-and-pagination",
        )
        for r in a["rules"]
    }
    assert set(nodes[0]["scope"]["rules"]) == infra and len(infra) == 22
    order = [n["id"] for n in nodes]
    acc = next(i for i in order if "account" in titles.get(i, "").lower())
    assert order.index(acc) == 1
    assert order.index(acc) < order.index("habits") < order.index("check-ins")
    assert nodes[-1]["id"] == "integration" and nodes[-1]["level"] == len(nodes) - 1
    covered = [r for n in nodes if n["kind"] != "integration" for r in n["scope"]["rules"]]
    assert sorted(covered) == sorted(scope["rules"]) and len(covered) == 148
    assert [len(x) for x in v["levels"]] == [1] * len(nodes)  # sequential: safe
    assert {n["id"]: n["tier"] for n in nodes}[acc] == "frontier"


# ---------------------------------------------------------------- the planner (fake claude)


@pytest.fixture()
def fake(tmp_path: Path) -> Path:
    d = tmp_path / "fakeplanner"
    d.mkdir()
    exe = d / "claude"
    exe.write_text(FAKE_PLANNER.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return d


def answers(fake: Path, *items: dict[str, Any]) -> None:
    (fake / "answers.json").write_text(json.dumps(list(items)))


def fake_log(fake: Path) -> list[dict[str, Any]]:
    p = fake / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_planner_argv_is_read_only_and_isolated() -> None:
    argv = build_planner_argv(claude_bin="claude", text="PLAN", model="opus", system="SYS")
    assert argv[:2] == ["claude", "-p"] and argv[-2:] == ["--", "PLAN"]

    def val(flag: str) -> str:
        return argv[argv.index(flag) + 1]

    assert val("--tools") == "Read,Grep,Glob" and val("--allowedTools") == "Read,Grep,Glob"
    assert set(val("--disallowedTools").split(",")) >= {"Bash", "Edit", "Write", "MultiEdit"}
    assert val("--setting-sources") == "" and val("--mcp-config") == '{"mcpServers":{}}'
    assert val("--permission-mode") == "dontAsk" and val("--model") == "opus"
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert val("--append-system-prompt") == "SYS"
    assert "acceptEdits" not in argv and "Bash" not in val("--allowedTools")


def test_the_epg_prompt_ships_with_attribution() -> None:
    from lattice.studio.plan import PROMPT_FILE

    raw = PROMPT_FILE.read_text()
    assert "epg" in raw.split("-->")[0] and "Building Effective Agents" in raw.split("-->")[0]
    text = epg_prompt()
    assert not text.startswith("<!--") and "ONE JSON object" in text
    for must in ("Scaffold first", "Integration last", "touches", "frontier", "25 rules"):
        assert must in text


def test_agent_planner_end_to_end_with_a_reask(repo: Path, fake: Path) -> None:
    bad = {"nodes": [node("a", scope={"area": "notes-app"}), node("b", scope={"area": "writing"})]}
    good = {
        "nodes": [
            node("pre", scope={"area": "notes-app"}),
            node("w", scope={"area": "writing"}, packages=["app/w"]),
            node("r", scope={"area": "reading"}, packages=["app/r"], deps=["w"]),
            integ("pre", "r"),
        ]
    }
    answers(fake, {"text": "```json\n" + json.dumps(bad) + "\n```"}, {"text": json.dumps(good)})
    env = {
        **os.environ,
        "FAKE_PLAN_DIR": str(fake),
        "ANTHROPIC_API_KEY": "sk-test-never",
        "ANTHROPIC_BASE_URL": "http://x",
    }
    pm = manager(
        repo, claude_bin=str(fake / "claude"), env=env, planner="agent", planner_model="opus-test"
    )
    rec = ready(pm, {"scope": "all"})
    assert rec["source"] == "agent" and rec["fallback_reason"] is None
    assert rec["levels"] == [["pre", "w"], ["r"], ["integration"]]
    assert rec["planner"]["attempts"] == 2 and rec["planner"]["model"] == "opus-test"
    assert rec["planner"]["usage"]["tokens_in"] == 2 * 4050
    log = fake_log(fake)
    assert len(log) == 2
    for entry in log:
        assert entry["env"].get("ANTHROPIC_API_KEY") is None
        assert entry["env"].get("ANTHROPIC_BASE_URL") is None
        assert Path(entry["cwd"]).resolve() == repo.resolve()
        a = entry["argv"]
        assert a[a.index("--tools") + 1] == "Read,Grep,Glob" and a[a.index("--model") + 1] == (
            "opus-test"
        )
    first, second = log[0]["argv"][-1], log[1]["argv"][-1]
    assert '"id": "writing"' in first
    assert "gate.sh" in first.split("## Repository files")[1]  # the file tree
    assert "R4 is covered by no node" in second and "Your previous plan was invalid" in second
    assert [a[1:] for a in pm.argvs(rec["id"])] == [x["argv"] for x in log]


def test_planner_failure_and_double_invalid_fall_back(repo: Path, fake: Path) -> None:
    env = {**os.environ, "FAKE_PLAN_DIR": str(fake)}
    answers(fake, {"exit": 1, "stderr": "Claude AI usage limit reached"})
    pm = manager(repo, claude_bin=str(fake / "claude"), env=env, planner="agent")
    rec = ready(pm, {"scope": {"areas": ["writing", "reading"]}})
    assert rec["source"] == "fallback"
    assert rec["fallback_reason"] == "the planner failed: Claude AI usage limit reached"
    assert [n["id"] for n in rec["nodes"]] == ["writing", "reading", "integration"]
    (fake / "count").unlink()
    answers(fake, {"text": json.dumps({"nodes": [node("x", scope={"rules": ["R2"]})]})})
    rec = ready(pm, {"scope": {"areas": ["writing"]}})
    assert rec["source"] == "fallback"
    assert (
        rec["fallback_reason"] == "the planner's plan was invalid twice: R3 is covered by no node"
    )
    answers(fake, {"text": "I would rather not."})
    rec = ready(pm, {"scope": {"areas": ["reading"]}})
    assert rec["fallback_reason"] == "the planner failed: the planner answered no JSON object"
    rec = ready(pm, {"scope": "all", "planner": "fallback"})
    assert rec["fallback_reason"] == "requested: the request asked for the fallback planner"


def test_planner_prompt_content(repo: Path) -> None:
    areas = areas_of(repo)
    trace = json.loads((repo / "spec.trace.json").read_text())
    dm = {"tables": [{"name": "Note", "sql": "notes", "columns": [{"name": "text"}]}]}
    text = planner_prompt(
        project="notes",
        scope=resolve_plan_scope("all", areas),
        areas=areas,
        trace=trace,
        data_model=dm,
        file_tree=["app/main.py"],
        max_parallel=3,
        failing={"writing": 2},
        feedback="split reading",
    )
    info = json.loads(text.split("## Areas of the scope (JSON)\n\n")[1].split("\n\n## Data")[0])
    w = next(a for a in info if a["id"] == "writing")
    assert w["failing"] == 2 and w["endpoints"] == ["POST /notes"] and w["tables"] == ["Note"]
    assert [r["id"] for r in w["rules"]] == ["R2", "R3"]
    assert '"columns": ["text"]' in text and "app/main.py" in text and "split reading" in text
    assert "at most 3 node(s)" in text


# ---------------------------------------------------------------- the runner


ALL_PLAN = {
    "nodes": [
        node("scaffold", "scaffold", files=["scaffold.txt"]),
        node("pre", scope={"area": "notes-app"}, deps=["scaffold"]),
        node("w-slow", scope={"area": "writing"}, deps=["scaffold"], files=["w.txt"]),
        node("r-slow", scope={"area": "reading"}, deps=["scaffold"], files=["r.txt"]),
        integ("pre", "w-slow", "r-slow"),
    ]
}


def test_levels_merge_in_order_and_run_concurrently(repo: Path) -> None:
    stub_plans(repo, {"all": ALL_PLAN})
    pm = manager(repo, metrics_hook=f"{sys.executable} hook.py")
    before = owner(repo)
    rec = ready(pm, {"scope": "all", "max_parallel": 3})
    assert rec["levels"] == [["scaffold"], ["pre", "w-slow", "r-slow"], ["integration"]]
    with pytest.raises(PlanError) as e:
        pm.run(rec["id"])
    assert e.value.status == 409 and e.value.detail == (
        "plan p1 is ready, not approved: approve it first"
    )
    rec = run(pm, rec["id"])
    assert rec["status"] == "passed", rec
    by = {n["id"]: n for n in rec["nodes"]}
    assert [by[i]["status"] for i in ("scaffold", "pre", "w-slow", "r-slow", "integration")] == [
        "passed"
    ] * 5
    assert by["pre"]["nothing_to_build"] and by["integration"]["nothing_to_build"]
    assert not by["scaffold"]["nothing_to_build"]  # the scaffold always runs
    # the two siblings really ran at the same time
    w, r = by["w-slow"], by["r-slow"]
    assert w["started_ts"] < r["finished_ts"] and r["started_ts"] < w["finished_ts"]
    # both started from the target tip at the start of level 1 (after the scaffold merge)
    lv1 = rec["level_runs"][1]
    assert lv1["merged"] == ["w-slow", "r-slow"] and lv1["status"] == "passed"
    assert pm.rb.get(w["build_id"])["base"] == lv1["base"] == rec["level_runs"][0]["tip"]
    assert pm.rb.get(r["build_id"])["base"] == lv1["base"]
    # merges in plan node order on the target branch, one --no-ff merge commit per node
    tip = git(repo, "rev-parse", "build/notes")
    assert tip == rec["target_tip"] == lv1["tip"]
    subjects = git(repo, "log", "--first-parent", "--format=%s", "build/notes").splitlines()
    assert subjects[:3] == [
        "metrics: plan p1 level 1",
        "plan p1 level 1: merge node r-slow (rebuild/p1-r-slow)",
        "plan p1 level 1: merge node w-slow (rebuild/p1-w-slow)",
    ]
    assert "plan p1 level 0: merge node scaffold (rebuild/p1-scaffold)" in subjects
    assert (
        git(repo, "log", "-1", "--format=%B", w["commit"])
        .strip()
        .endswith("Lattice-Plan: p1/w-slow")
    )
    assert lv1["gate"]["regressions"] == [] and lv1["gate"]["failing"] == []
    assert rec["baseline"]["failing"] == ["scenario:create_a_note", "scenario:read_own_and_other"]
    # the owner's checkout never moved; node branches kept, node worktrees removed
    assert owner(repo) == before
    assert git(repo, "branch", "--list", "rebuild/p1-*", "--format=%(refname:short)").split() == [
        "rebuild/p1-metrics",
        "rebuild/p1-r-slow",
        "rebuild/p1-scaffold",
        "rebuild/p1-w-slow",
    ]
    assert [ln for ln in git(repo, "worktree", "list").splitlines() if ".lattice" in ln] == []
    # metrics: per node at the level end (committed with the merges), once per plan
    phases = [
        json.loads(x)["phase"]
        for x in git(repo, "show", "build/notes:metrics/phases.jsonl").splitlines()
    ]
    assert phases == ["build_p1_scaffold", "build_p1_w-slow", "build_p1_r-slow"]
    plan_lines = git(repo, "show", "rebuild/p1-metrics:metrics/phases.jsonl").splitlines()
    last = json.loads(plan_lines[-1])
    assert last["phase"] == "build_plan_p1" and rec["metrics"]["branch"] == "rebuild/p1-metrics"
    assert last["llm"]["plan"] == {
        "id": "p1",
        "status": "passed",
        "source": "agent",
        "nodes": 5,
        "levels": 3,
        "max_level_width": 3,
    }
    assert rec["totals"]["shape"] == {"nodes": 5, "levels": 3, "max_level_width": 3}
    assert rec["totals"]["rounds"] == 3 and rec["totals"]["lines_changed"] == 3
    assert w["lines_changed"] == 1 and w["files_changed"] == ["w.txt"] and w["rounds"] == 1
    # areas on the target branch: built
    assert {a["id"]: a["status"] for a in pm.rb.areas(None)} == {
        "notes-app": "built",
        "writing": "built",
        "reading": "built",
        "all": "built",
    }


def test_a_regression_at_the_full_gate_fails_the_level(repo: Path) -> None:
    stub_plans(
        repo,
        {
            "areas-writing-reading": {
                "nodes": [
                    node("a", scope={"area": "writing"}, files=["a.txt"]),
                    node("b", scope={"area": "reading"}, files=["b.txt"]),
                    integ("a", "b"),
                ]
            }
        },
    )
    pm = manager(repo)
    rec = run(pm, ready(pm, {"scope": {"areas": ["writing", "reading"]}})["id"])
    assert rec["status"] == "failed"
    assert [n["status"] for n in rec["nodes"]] == ["regressed", "regressed", "pending"]
    lr = rec["level_runs"][0]
    assert lr["status"] == "failed" and lr["regressions"] == ["scenario:no_token"]
    assert lr["blame"] == [{"target": "scenario:no_token", "nodes": ["b"], "by": "rules"}]
    assert lr["error"]["kind"] == "regression"
    # the target never moved to the regressed tip
    assert (
        git(repo, "rev-parse", "build/notes") == rec["target_tip"] == git(repo, "rev-parse", "main")
    )
    # each node passed its own gate (the clash only shows once merged)
    for n in rec["nodes"][:2]:
        assert pm.rb.get(n["build_id"])["status"] == "passed"


def test_a_failed_node_blocks_dependents_not_siblings_and_resume(repo: Path) -> None:
    stub_plans(
        repo,
        {
            "rules-r2-r4-r5": {
                "nodes": [
                    node("broken", scope={"rules": ["R2"]}),
                    node("r", scope={"rules": ["R4"]}),
                    node("dep", scope={"rules": ["R5"]}, deps=["broken"]),
                    integ("r", "dep"),
                ]
            }
        },
    )
    pm = manager(repo)
    pid = ready(pm, {"scope": {"rules": ["R2", "R4", "R5"]}})["id"]
    rec = run(pm, pid)
    assert rec["status"] == "partial"
    by = {n["id"]: n for n in rec["nodes"]}
    assert by["broken"]["status"] == "gave_up" and by["r"]["status"] == "passed"
    assert by["dep"]["status"] == "blocked" and by["dep"]["blocked_by"] == ["broken"]
    assert by["integration"]["status"] == "blocked"
    assert by["r"]["merged"] and git(repo, "rev-parse", "build/notes") == rec["target_tip"]
    assert git(repo, "rev-parse", "build/notes") != git(repo, "rev-parse", "main")
    gave_up_wt = pm.rb.get(by["broken"]["build_id"])["worktree"]
    assert gave_up_wt and Path(gave_up_wt).is_dir()  # kept for review
    # resume: only the nodes that did not pass run again
    pm.run(pid)
    rec = pm.get(pid, wait=60)
    by = {n["id"]: n for n in rec["nodes"]}
    assert rec["status"] == "partial" and rec["runs"] == 2
    assert len(by["broken"]["builds"]) == 2 and len(by["r"]["builds"]) == 1
    assert by["broken"]["rounds"] == 4  # 2 + 2: usage and rounds accumulate


def test_a_merge_conflict_blocks_dependents(repo: Path) -> None:
    stub_plans(
        repo,
        {
            "areas-writing-reading": {
                "nodes": [
                    node("x1", scope={"area": "writing"}, files=["x1.txt"]),
                    node("x2", scope={"area": "reading"}, files=["x2.txt"]),
                    integ("x1", "x2"),
                ]
            }
        },
    )
    pm = manager(repo)
    rec = run(pm, ready(pm, {"scope": {"areas": ["writing", "reading"]}})["id"])
    by = {n["id"]: n for n in rec["nodes"]}
    assert by["x1"]["status"] == "passed" and by["x1"]["merged"]
    assert by["x2"]["status"] == "merge_conflict"
    assert by["x2"]["error"]["kind"] == "merge_conflict"
    assert by["integration"]["status"] == "blocked" and rec["status"] == "partial"
    assert rec["level_runs"][0]["merge_conflicts"] == ["x2"]
    assert git(repo, "show", "build/notes:shared.txt") == "one"


def test_stop_kills_running_nodes_and_keeps_branches(repo: Path) -> None:
    stub_plans(
        repo, {"rules-r4": {"nodes": [node("hold", scope={"rules": ["R4"]}), integ("hold")]}}
    )
    pm = manager(repo)
    pid = ready(pm, {"scope": {"rules": ["R4"]}})["id"]
    pm.approve(pid)
    pm.run(pid)
    with pytest.raises(RebuildError) as e:
        pm.rb.start([], scope="all")
    assert (
        e.value.status == 409 and e.value.detail == "a build plan is running in this project (p1)"
    )
    t0 = time.time()
    while time.time() - t0 < 20 and pm.get(pid)["nodes"][0]["status"] != "running":
        time.sleep(0.05)
    t0 = time.time()
    assert pm.stop(pid) == {"id": pid, "status": "stopped"}
    assert time.time() - t0 < 10
    rec = pm.get(pid)
    assert rec["status"] == "stopped" and rec["nodes"][0]["status"] == "stopped"
    assert pm.rb.get(rec["nodes"][0]["build_id"])["status"] == "stopped"
    assert pm.rb.plan_active is None
    with pytest.raises(PlanError) as e2:
        pm.stop(pid)
    assert e2.value.detail == "cannot stop a plan that is stopped"
    # nothing landed; a stand-alone build may run again
    assert git(repo, "rev-parse", "build/notes") == git(repo, "rev-parse", "main")


def test_approval_revision_and_refusals(repo: Path) -> None:
    stub_plans(
        repo,
        {
            "all": ALL_PLAN,
            "areas-writing-reading": {
                "nodes": [
                    node("w", scope={"area": "writing"}, files=["notes.py"]),
                    node("r", scope={"area": "reading"}, files=["notes.py"]),
                    integ("w", "r"),
                ]
            },
        },
    )
    pm = manager(repo)
    rec = ready(pm, {"scope": {"areas": ["writing", "reading"]}})
    pid = rec["id"]
    assert rec["levels"] == [["w"], ["r"], ["integration"]]
    assert pm.approve(pid) == {"id": pid, "status": "approved", "version": 1}
    with pytest.raises(PlanError) as e:
        pm.approve(pid)
    assert e.value.detail == "plan p1 is approved, not ready"
    # removing the overlap edge does not stick: the validator puts it back
    out = pm.revise(
        pid,
        {
            "edits": {
                "remove_edges": [["w", "r"]],
                "nodes": {"r": {"tier": "frontier"}, "w": {"model": "m1"}},
            }
        },
    )
    assert out["status"] == "ready" and out["version"] == 2 and out["approved"] is None
    by = {n["id"]: n for n in out["nodes"]}
    assert by["r"]["depends_on"] == ["w"] and by["r"]["serialised_because"]["node"] == "w"
    assert by["r"]["model"] == "opus" and by["w"]["model"] == "m1"
    with pytest.raises(PlanError) as e:
        pm.run(pid)  # the approval was cleared
    assert e.value.status == 409
    with pytest.raises(PlanError) as e:
        pm.revise(
            pid, {"edits": {"add_edges": [["r", "w"]], "nodes": {"r": {"depends_on": ["w"]}}}}
        )
    assert e.value.status == 422 and "cycle" in e.value.detail and e.value.code == "plan_invalid"
    assert pm.get(pid)["version"] == 2
    # the owner may reverse the order: an explicit edge, no overlap left on one level
    out = pm.revise(pid, {"edits": {"add_edges": [["r", "w"]]}})
    assert out["levels"] == [["r"], ["w"], ["integration"]] and out["version"] == 3
    for body, status in (
        ({"feedback": "x", "edits": {}}, 422),
        ({"edits": {"nodes": {"zz": {}}}}, 404),
        ({"edits": {"max_parallel": 9}}, 422),
    ):
        with pytest.raises(PlanError) as e:
            pm.revise(pid, body)
        assert e.value.status == status, body
    with pytest.raises(PlanError) as e:
        pm.get("p9")
    assert (e.value.status, e.value.detail) == (404, "no plan p9")
    for body, status, msg in (
        ({"scope": "all", "max_parallel": 0}, 422, "max_parallel must be 1 to 8"),
        ({"scope": "all", "planner": "x"}, 422, 'planner must be "agent" or "fallback"'),
        ({}, 422, "scope is required"),
        ({"scope": {"areas": ["nope"]}}, 404, "no area nope"),
        (
            {"scope": "all", "models": {"huge": "x"}},
            422,
            "models must map fast, mid and frontier to model names",
        ),
    ):
        with pytest.raises(PlanError) as e:
            pm.create(body)
        assert (e.value.status, e.value.detail) == (status, msg)
    # one plan at a time; and no plan while a stand-alone build runs
    pm2 = ready(pm, {"scope": "all"})
    pm.approve(pm2["id"])
    pm.approve(pid)
    pm.run(pm2["id"])
    with pytest.raises(PlanError) as e:
        pm.run(pid)
    assert e.value.detail == "a build plan is running in this project (p2)"
    assert pm.get(pm2["id"], wait=60)["status"] == "passed"
    assert [p["id"] for p in pm.list()] == ["p1", "p2"]


def test_branch_rules_are_v18s(repo: Path) -> None:
    stub_plans(repo, {"rules-r4": {"nodes": [node("r", scope={"rules": ["R4"]}), integ("r")]}})
    pm = manager(repo, protected_branches=["release"])
    git(repo, "branch", "release")
    for branch, code in (
        ({"name": "main"}, "branch_checked_out"),
        ({"name": "release"}, "branch_protected"),
        ({"name": "nope"}, "no_branch"),
        ({"name": "release", "create": True}, "branch_exists"),
        ({"name": "bad..x", "create": True}, "branch_name_invalid"),
        ({"name": "n", "create": True, "from": "zz"}, "no_ref"),
    ):
        with pytest.raises(PlanError) as e:
            pm.create({"scope": {"rules": ["R4"]}, "branch": branch})
        assert e.value.code == code, branch
    rec = ready(pm, {"scope": {"rules": ["R4"]}, "branch": {"name": "feat/x", "create": True}})
    assert rec["target_branch"] == "feat/x"
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "-q", "--verify", "feat/x"], capture_output=True
        ).returncode
        != 0
    )
    rec = run(pm, rec["id"])
    assert rec["status"] == "passed" and rec["created_branch"] is True
    assert git(repo, "rev-parse", "feat/x") == rec["target_tip"] != git(repo, "rev-parse", "main")


def test_plan_routes_under_the_project_prefix(repo: Path) -> None:
    (repo / "studio.toml").write_text(
        (repo / "studio.toml").read_text()
        + '\n[rebuild]\nagent = "stub"\nplanner = "stub"\ngates = ["./gate.sh"]\nmax_rounds = 2\n'
    )
    git(repo, "commit", "-q", "-am", "rebuild config")
    stub_plans(repo, {"rules-r4": {"nodes": [node("r", scope={"rules": ["R4"]}), integ("r")]}})
    srv = make_hub_server([("notes", repo)], generator=StubGenerator())
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/p/notes"
    try:
        assert request("POST", base + "/api/plan", {"scope": {"rules": ["R4"]}}) == (
            200,
            {"id": "p1", "status": "planning"},
        )
        status, rec = request("GET", base + "/api/plan/p1?wait=30")
        assert status == 200 and rec["status"] == "ready" and rec["source"] == "agent"
        assert request("POST", base + "/api/plan/p1/run", {})[0] == 409
        assert request("POST", base + "/api/plan/p1/approve", {})[1]["status"] == "approved"
        assert request("POST", base + "/api/plan/p1/run", {}) == (
            200,
            {"id": "p1", "status": "running"},
        )
        status, rec = request("GET", base + "/api/plan/p1?wait=60")
        assert rec["status"] == "passed"
        assert request("GET", base + "/api/plans")[1][0]["nodes"] == [
            {"id": "r", "status": "passed"},
            {"id": "integration", "status": "passed"},
        ]
        assert request("GET", base + "/api/plan/p9") == (404, {"detail": "no plan p9"})
        assert request("GET", base + "/api/plan/p1?wait=99")[0] == 422
        assert request("DELETE", base + "/api/plan/p1") == (
            409,
            {"detail": "cannot stop a plan that is passed"},
        )
        assert request("POST", base + "/api/plan", {"scope": "nope"})[0] == 422
        caps = request("GET", f"http://127.0.0.1:{srv.server_address[1]}/api/projects")[1]
        assert caps[0]["capabilities"]["build_plan"] is True
        assert request("GET", base + f"/api/rebuild/{rec['nodes'][0]['build_id']}")[1]["plan"] == {
            "id": "p1",
            "node": "r",
        }
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_mock_serves_plans() -> None:
    import threading

    from lattice.studio.mock_server import make_server

    srv = make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert request("POST", base + "/api/plan", {"scope": "nope"})[0] == 422
        assert request("POST", base + "/api/plan", {"scope": "all", "max_parallel": 3}) == (
            200,
            {"id": "p1", "status": "planning"},
        )
        status, rec = request("GET", base + "/api/plan/p1")
        assert status == 200 and rec["status"] == "ready" and rec["source"] == "agent"
        assert rec["validation"]["ok"] and rec["levels"][-1] == ["integration"]
        assert max(len(x) for x in rec["levels"]) <= 3 and len(rec["levels"]) >= 3
        assert request("POST", base + "/api/plan/p1/run", {})[0] == 409
        assert request("POST", base + "/api/plan/p1/approve", {})[1]["status"] == "approved"
        assert request("POST", base + "/api/plan/p1/run", {})[1]["status"] == "running"
        for _ in range(3 * len(rec["levels"]) + 2):
            rec = request("GET", base + "/api/plan/p1")[1]
        assert rec["status"] == "passed" and all(n["status"] == "passed" for n in rec["nodes"])
        assert rec["totals"]["shape"]["nodes"] == len(rec["nodes"])
        assert request("GET", base + "/api/plans")[1][0]["id"] == "p1"
        _, fb = request(
            "POST", base + "/p/lattice-studio/api/plan", {"scope": "all", "planner": "fallback"}
        )
        rec = request("GET", base + "/p/lattice-studio/api/plan/" + fb["id"])[1]
        assert rec["source"] == "fallback" and all(len(x) == 1 for x in rec["levels"])
        assert request("DELETE", base + "/api/plan/p1") == (
            409,
            {"detail": "cannot stop a plan that is passed"},
        )
    finally:
        srv.shutdown()
        srv.server_close()
