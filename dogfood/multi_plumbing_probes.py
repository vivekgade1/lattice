"""The probes of the multi-project plumbing change, pre-registered with
``docs/results/2026-10-15-multi-project-plumbing-prereg.md`` (and its Amendment 1).

L-plugin P1-P7 (the plugin's argv, the workspace fallback anchored at the project), L-url U1-U6
(``--studio-url`` with several projects, a fake Studio on loopback; read tools of the other
projects go to ``/p/<name>``, writes stay local), L-guard G01-G30 (the real hook, JSON on stdin,
``rm -rf`` of a parent of a project) with the cost bounds C16 and C29, and L-regress R1 (MP-2's ten
guard cases, with the two ``--project`` and with none). "Old" is ``git archive a4ab4a1 lattice``.

    mkdir -p $S/old-a4ab4a1 && git archive a4ab4a1 lattice | tar -x -C $S/old-a4ab4a1
    python dogfood/multi_plumbing_probes.py --old $S/old-a4ab4a1 --scratch $S/g8 --runs 10 \
        --out docs/results/data/2026-10-15-multi-project-plumbing [--only P2,U1,G13] [--real-hub]

Real subprocesses (``python -m lattice.mcp``; L-plugin P2-P7 through ``lattice.cli.main``).
Deterministic probes: pass = every check holds in every run and the outcomes are identical.
Output: ``id | predicted | observed | pass`` rows, the old column, the cost bounds and
``probes: P/T pass``; raw per-run JSON and ``summary.json`` under ``--out``. No LLM, no API key,
no Claude session, no network beyond loopback. The expected texts below are copied from the
pre-registration, not imported from the code under test.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import socket
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.mcp.plumbing_support import (  # noqa: E402
    GUARD_ROWS,
    INIT,
    FakeStudio,
    build_guard_fixture,
    clean_env,
    rpc,
    run_server,
    tool_call,
    tool_result,
)

BASE_NOTE = (
    "Lattice Studio tools for this project's requirements spec. Read with model_get, "
    "rule_get, spec_ask, spec_lint, code_links, graph_get. Never edit REQUIREMENTS.md, "
    "spec.py or spec.trace.json directly: propose a change with change_propose, poll "
    "change_get, and tell the owner to accept it in Lattice Studio."
)
MULTI_NOTE = (
    " Several projects are known: project_list lists them; every tool takes an optional "
    "`project` (a name or path from project_list; default: the active project, which "
    "project_set_active changes)."
)
WORKSPACE_NOTE = (
    " A workspace manifest links these projects; project_list shows each one's role and links. "
    'For "has anything a provider changed affected this project?": call project_list, then BOTH '
    "contract_check and linked_question_list for the consumer: contract_check shows what changed "
    "since its pin (BROKEN, CHANGED), linked_question_list the notes a provider left when an "
    "accepted change broke it (open, resolved or dismissed). Both take `consumer` and default to "
    "the active project when it is a consumer. workspace_impact lists who relies on a project; "
    "contract_impact previews a provider's candidate version against its consumers. These five "
    "tools are read-only."
)
WS_TOOLS = ["workspace_impact", "contract_check", "contract_impact", "linked_question_list"]
NOT_A_PROJECT = "is not a Lattice project yet"
BASELINE = "a4ab4a1"

JSON_SEP = (",", ":")


def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=JSON_SEP)


# ---- the context of one run --------------------------------------------------------------------


class Ctx:
    def __init__(self, old: Path, scratch: Path) -> None:
        self.old = old
        self.scratch = scratch
        self.fx = scratch / "fx"  # rebuilt for every run, at the same path
        self.registry = scratch / "no-registry.toml"  # does not exist

    def env(self, code: Path, **extra: str) -> dict[str, str]:
        return clean_env(code, LATTICE_PROJECTS_REGISTRY=str(self.registry), **extra)

    def serve(
        self,
        code: Path,
        argv: Sequence[str],
        reqs: Sequence[Mapping[str, Any]],
        cwd: Path,
        *,
        via_cli: bool = False,
        **env_extra: str,
    ) -> tuple[list[str], str]:
        lines, err = run_server(
            argv, reqs, cwd=cwd, env=self.env(code, **env_extra), via_cli=via_cli
        )
        if len(lines) != len(reqs):
            raise RuntimeError(
                f"{len(lines)} replies for {len(reqs)} requests; stderr: {err[-400:]}"
            )
        return lines, err


def copy_studio(dst: Path) -> Path:
    shutil.copytree(REPO / "dogfood" / "studio", dst)
    return dst


def manifest(root: Path, *projects: tuple[str, str]) -> Path:
    body = '[workspace]\nname = "plumbing"\n'
    for name, path in projects:
        body += f'\n[[project]]\nname = "{name}"\npath = "{path}"\n'
    m = root / "lattice.workspace.toml"
    m.write_text(body)
    return m


def build_plugin_fixtures(fx: Path) -> None:
    copy_studio(fx / "studio-copy")  # P2: no manifest above it
    (fx / "elsewhere").mkdir()
    ws = fx / "ws"
    copy_studio(ws / "proj")
    copy_studio(ws / "other")
    manifest(ws, ("proj", "proj"), ("other", "other"))
    ws5 = fx / "ws5"  # a manifest that does not declare ws5/proj
    copy_studio(ws5 / "proj")
    copy_studio(ws5 / "other")
    manifest(ws5, ("other", "other"))
    ws7 = fx / "ws7"  # an unreadable manifest
    copy_studio(ws7 / "proj")
    (ws7 / "lattice.workspace.toml").write_text("[[[")


# ---- L-plugin ----------------------------------------------------------------------------------

STD = [rpc(1, "initialize", INIT), rpc(2, "tools/list"), tool_call(3, "model_get")]
LISTING = [*STD, tool_call(4, "project_list")]


def tools_of(line: str) -> list[str]:
    return [t["name"] for t in json.loads(line)["result"]["tools"]]


def instructions_of(line: str) -> str:
    return json.loads(line)["result"]["instructions"]


def sources_of(line: str) -> dict[str, str]:
    return {p["name"]: p["source"] for p in tool_result(line)[1]["projects"]}


def expand(arg: str, env: Mapping[str, str]) -> str:
    """The host's ``${V}`` / ``${V:-default}`` expansion of one argv item."""
    import re

    return re.sub(
        r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: env.get(m.group(1)) or (m.group(2) or ""), arg
    )


def mcp_lines(err: str) -> list[str]:
    """The server's own stderr lines (the Studio engine's timestamped log lines are not facts)."""
    return [ln for ln in err.splitlines() if ln.startswith("lattice mcp:")]


def probe_p1(c: Ctx) -> dict[str, Any]:
    plugin = REPO / "lattice-claude-plugin"
    mcp_path, hooks_path = plugin / ".mcp.json", plugin / "hooks" / "hooks.json"
    cfg = json.loads(mcp_path.read_text())
    json.loads(hooks_path.read_text())
    args = cfg["mcpServers"]["lattice"]["args"]
    diff = subprocess.run(
        ["git", "diff", "--quiet", BASELINE, "--", str(mcp_path), str(hooks_path)],
        cwd=REPO, check=False, capture_output=True,
    )  # fmt: skip
    arg = args[3] if len(args) > 3 else ""
    checks = {
        "both files parse": True,
        f"git diff {BASELINE} of both is empty": diff.returncode == 0,
        "args are mcp serve --project ${CLAUDE_PROJECT_DIR:-.}": args
        == ["mcp", "serve", "--project", "${CLAUDE_PROJECT_DIR:-.}"],
        "expanded without the var: --project .": expand(arg, {}) == ".",
        "expanded with the var: the path": expand(arg, {"CLAUDE_PROJECT_DIR": "/x/y"}) == "/x/y",
        "${V} without the var is empty": expand("${V}", {}) == "",
        "${V:-d} without the var is d": expand("${V:-d}", {}) == "d",
    }
    return {"checks": checks, "facts": {"args": args, "git_diff_rc": diff.returncode}}


def probe_p2(c: Ctx) -> dict[str, Any]:
    cwd = c.fx / "studio-copy"
    facts: dict[str, Any] = {}
    checks: dict[str, bool] = {}
    for label, arg in (("dot", "."), ("abs", str(cwd))):
        new, _ = c.serve(REPO, ["--project", arg], STD, cwd, via_cli=True)
        old, _ = c.serve(c.old, ["--project", arg], STD, cwd, via_cli=True)
        for i, what in enumerate(("initialize", "tools/list", "model_get")):
            checks[f"{label}: {what} byte-identical to old"] = new[i] == old[i]
        checks[f"{label}: the instructions are the base text"] = (
            instructions_of(new[0]) == BASE_NOTE
        )
        facts[label] = {"tools": tools_of(new[1])}
    checks["both expansions give the same tools/list"] = facts["dot"] == facts["abs"]
    return {"checks": checks, "facts": facts}


def _ws_checks(
    c: Ctx, lines: list[str], base_tools: list[str], label: str, *, workspace: bool
) -> dict[str, bool]:
    names = tools_of(lines[1])
    out: dict[str, bool] = {}
    if workspace:
        out[f"{label}: tools = single-project tools + the 4 workspace tools"] = names == [
            *base_tools,
            *WS_TOOLS,
        ]
        out[f"{label}: instructions = base + MULTI + WORKSPACE"] = (
            instructions_of(lines[0]) == BASE_NOTE + MULTI_NOTE + WORKSPACE_NOTE
        )
        out[f"{label}: proj explicit, other workspace"] = sources_of(lines[3]) == {
            "proj": "explicit", "other": "workspace",
        }  # fmt: skip
    else:
        out[f"{label}: single-project tools, no workspace tools"] = names == base_tools
        out[f"{label}: instructions are the base text"] = instructions_of(lines[0]) == BASE_NOTE
        out[f"{label}: one project"] = len(sources_of(lines[3])) == 1
    return out


def _single_tools(c: Ctx) -> list[str]:
    lines, _ = c.serve(REPO, ["--project", "."], STD, c.fx / "studio-copy", via_cli=True)
    return tools_of(lines[1])


def probe_p3(c: Ctx) -> dict[str, Any]:
    cwd, base = c.fx / "ws" / "proj", _single_tools(c)
    new, _ = c.serve(REPO, ["--project", "."], LISTING, cwd, via_cli=True)
    old, _ = c.serve(c.old, ["--project", "."], LISTING, cwd, via_cli=True)
    checks = _ws_checks(c, new, base, "new", workspace=True)
    checks.update(_ws_checks(c, old, base, "old", workspace=True))
    checks["old: initialize and tools/list byte-identical"] = new[:2] == old[:2]
    return {"checks": checks, "facts": {"sources": sources_of(new[3])}}


def probe_p4(c: Ctx) -> dict[str, Any]:
    cwd, proj, base = c.fx / "elsewhere", c.fx / "ws" / "proj", _single_tools(c)
    new, nerr = c.serve(REPO, ["--project", str(proj)], LISTING, cwd)
    old, _ = c.serve(c.old, ["--project", str(proj)], LISTING, cwd)
    checks = _ws_checks(c, new, base, "new", workspace=True)
    checks.update(_ws_checks(c, old, base, "old", workspace=False))
    return {"checks": checks, "facts": {"sources": sources_of(new[3]), "stderr": mcp_lines(nerr)}}


def probe_p5(c: Ctx) -> dict[str, Any]:
    cwd, proj = c.fx / "elsewhere", c.fx / "ws5" / "proj"
    new, nerr = c.serve(REPO, ["--project", str(proj)], LISTING, cwd)
    old, _ = c.serve(c.old, ["--project", str(proj)], LISTING, cwd)
    base = _single_tools(c)
    checks = {
        "byte-identical to old": new == old,
        "single project, no workspace tools": tools_of(new[1]) == base
        and len(sources_of(new[3])) == 1,
        "stderr names the manifest as not used": "not used" in nerr
        and str(c.fx / "ws5" / "lattice.workspace.toml") in nerr
        and "does not declare" in nerr,
    }
    return {"checks": checks, "facts": {"stderr": mcp_lines(nerr)}}


def probe_p6(c: Ctx) -> dict[str, Any]:
    cwd, proj, base = c.fx / "elsewhere", c.fx / "ws" / "proj", _single_tools(c)
    ws = str(c.fx / "ws")
    new, _ = c.serve(REPO, ["--project", str(proj)], LISTING, cwd, LATTICE_WORKSPACE=ws)
    old, _ = c.serve(c.old, ["--project", str(proj)], LISTING, cwd, LATTICE_WORKSPACE=ws)
    checks = _ws_checks(c, new, base, "new", workspace=True)
    checks.update(_ws_checks(c, old, base, "old", workspace=True))
    checks["byte-identical to old"] = new == old
    return {"checks": checks, "facts": {"sources": sources_of(new[3])}}


def probe_p7(c: Ctx) -> dict[str, Any]:
    cwd, proj = c.fx / "elsewhere", c.fx / "ws7" / "proj"
    new, nerr = c.serve(REPO, ["--project", str(proj)], LISTING, cwd)
    old, _ = c.serve(c.old, ["--project", str(proj)], LISTING, cwd)
    checks = {
        "byte-identical to old": new == old,
        "no workspace row in project_list": "workspace" not in tool_result(new[3])[1],
        "one project": len(sources_of(new[3])) == 1,
    }
    return {"checks": checks, "facts": {"stderr": mcp_lines(nerr)}}


# ---- L-url -------------------------------------------------------------------------------------


class UrlFx:
    """Three bare directories a, b, c and a fake Studio serving a (alpha) and b (beta)."""

    def __init__(self, c: Ctx, **kw: Any) -> None:
        self.c = c
        root = c.fx / "url"
        self.dirs = {n: root / n for n in "abc"}
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.studio = FakeStudio(self.dirs["a"], self.dirs["b"], **kw).start()
        self.cwd = root

    def run(
        self, code: Path, projects: Sequence[Path | str], url: str, calls: Sequence[Any],
        *, init: bool = False,
    ) -> list[str]:  # fmt: skip
        argv: list[str] = []
        for p in projects:
            argv += ["--project", str(p)]
        argv += ["--studio-url", url]
        reqs = [rpc(1, "initialize", INIT)]
        if init:
            reqs.append(rpc(2, "tools/list"))
        reqs += [tool_call(10 + i, name, **args) for i, (name, args) in enumerate(calls)]
        lines, _ = self.c.serve(code, argv, reqs, self.cwd)
        return lines

    def norm(self, s: str) -> str:
        return s.replace(self.studio.url, "<STUDIO>")

    def close(self) -> None:
        self.studio.stop()


def rule_text(line: str) -> str | None:
    err, data = tool_result(line)
    return None if err else data["rules"][0]["text"]


def _results(u: UrlFx, lines: Sequence[str]) -> list[Any]:
    out = []
    for ln in lines:
        err, data = tool_result(ln)
        out.append(
            {"error": u.norm(data)} if err else {"ok": u.norm(json.dumps(data, sort_keys=True))}
        )
    return out


def probe_u1(c: Ctx) -> dict[str, Any]:
    u = UrlFx(c)
    try:
        d = u.dirs
        calls = [("model_get", {"project": p}) for p in ("a", "b", "c", "b")] + [
            ("change_propose", {"project": "b", "request": "make notes public"}),
            ("spec_ask", {"project": "b", "question": "who owns notes?"}),
            ("check_run", {"project": "b"}),
        ]
        url = u.studio.url + "/p/alpha"
        new = u.run(REPO, [d["a"], d["b"], d["c"]], url, calls)[1:]
        log_new = list(u.studio.log)
        u.studio.log.clear()
        old = u.run(c.old, [d["a"], d["b"], d["c"]], url, calls)[1:]
        _e, ctext = tool_result(new[2])
        listing = [r for r in log_new if r[1] == "/api/projects"]
        writes = (4, 5, 6)
        checks = {
            "a = ALPHA via studio": rule_text(new[0]) == "ALPHA via studio",
            "b = BETA via studio": rule_text(new[1]) == "BETA via studio",
            "c is a local error with C's path": tool_result(new[2])[0]
            and NOT_A_PROJECT in ctext
            and str(d["c"].resolve()) in ctext,
            "the second b adds no listing request": rule_text(new[3]) == "BETA via studio",
            "exactly 2 GET /api/projects": len(listing) == 2,
            "0 requests naming C": not [
                r for r in log_new if "/p/c" in r[1] or str(d["c"]) in r[1]
            ],
            "b's change_propose, spec_ask, check_run are local errors": all(
                tool_result(new[i])[0] and NOT_A_PROJECT in tool_result(new[i])[1] for i in writes
            ),
            "b's writes are served as at a4ab4a1": all(
                new[i] == old[i] or u.norm(new[i]) == u.norm(old[i]) for i in writes
            ),
            "no POST reached the Studio": not [r for r in log_new if r[0] == "POST"],
        }
        return {
            "checks": checks,
            "facts": {
                "results": _results(u, new),
                "old_b_model_get": _results(u, old[1:2]),
                "log": [f"{m} {p}" for m, p in log_new],
            },
        }
    finally:
        u.close()


def probe_u2(c: Ctx) -> dict[str, Any]:
    u = UrlFx(c)
    try:
        d = u.dirs
        out = u.run(REPO, [d["a"], d["b"], d["c"]], u.studio.url,
                    [("model_get", {"project": p}) for p in ("a", "b", "c")])[1:]  # fmt: skip
        paths = [p for m, p in u.studio.log if p.endswith("/api/model")]
        checks = {
            "a via /api/model": rule_text(out[0]) == "ALPHA via studio" and "/api/model" in paths,
            "b via /p/beta/api/model": rule_text(out[1]) == "BETA via studio"
            and "/p/beta/api/model" in paths,
            "c is the local error": tool_result(out[2])[0]
            and NOT_A_PROJECT in tool_result(out[2])[1],
        }
        return {"checks": checks, "facts": {"paths": paths, "results": _results(u, out)}}
    finally:
        u.close()


def probe_u3(c: Ctx) -> dict[str, Any]:
    u = UrlFx(c, listing_status=404)
    try:
        d = u.dirs
        calls = [("model_get", {"project": "a"}), ("model_get", {"project": "b"})]
        out = u.run(REPO, [d["a"], d["b"]], u.studio.url + "/p/alpha", calls)[1:]
        err, text = tool_result(out[1])
        checks = {
            "a via the Studio": rule_text(out[0]) == "ALPHA via studio",
            "b is the local error (fallback)": err and NOT_A_PROJECT in text,
            "nothing asked of /p/beta": not [r for r in u.studio.log if "/p/beta" in r[1]],
        }
        return {"checks": checks, "facts": {"results": _results(u, out)}}
    finally:
        u.close()


def probe_u4(c: Ctx) -> dict[str, Any]:
    u = UrlFx(c, listing_status=500)
    try:
        d = u.dirs
        calls = [("model_get", {"project": "b"}), ("model_get", {"project": "b"})]
        out = u.run(REPO, [d["a"], d["b"]], u.studio.url + "/p/alpha", calls)[1:]
        res = [tool_result(x) for x in out]
        checks = {
            "both calls are errors with `Studio answered 500` and `(project: b)`": all(
                e and "Studio answered 500" in t and t.endswith("(project: b)") for e, t in res
            ),
            "2 listing requests (a failure is not cached)": len(
                u.studio.requests("GET", "/api/projects")
            )
            == 2,
        }
        return {"checks": checks, "facts": {"results": _results(u, out)}}
    finally:
        u.close()


def probe_u5(c: Ctx) -> dict[str, Any]:
    u = UrlFx(c)
    try:
        d = u.dirs
        url = u.studio.url + "/p/alpha"
        new = u.run(REPO, [d["a"]], url, [("model_get", {})], init=True)
        log_new = list(u.studio.log)
        u.studio.log.clear()
        old = u.run(c.old, [d["a"]], url, [("model_get", {})], init=True)
        checks = {
            "0 /api/projects requests": not [r for r in log_new if "/api/projects" in r[1]],
            "initialize byte-identical to old": new[0] == old[0],
            "tools/list byte-identical to old": new[1] == old[1],
            "model_get byte-identical to old": new[2] == old[2],
            "model_get is the Studio's": rule_text(new[2]) == "ALPHA via studio",
        }
        return {"checks": checks, "facts": {"log": [f"{m} {p}" for m, p in log_new]}}
    finally:
        u.close()


def probe_u6(c: Ctx) -> dict[str, Any]:
    u = UrlFx(c)
    try:
        link = u.cwd / "b-link"
        if link.is_symlink():
            link.unlink()
        link.symlink_to(u.dirs["b"], target_is_directory=True)
        out = u.run(REPO, [u.dirs["a"], link], u.studio.url + "/p/alpha",
                    [("model_get", {"project": "b-link"})])[1:]  # fmt: skip
        checks = {
            "b via /p/beta (both sides realpath'd)": rule_text(out[0]) == "BETA via studio",
            "the request went to /p/beta/api/model": bool(
                u.studio.requests("GET", "/p/beta/api/model")
            ),
        }
        return {"checks": checks, "facts": {"results": _results(u, out)}}
    finally:
        u.close()


# ---- L-guard -----------------------------------------------------------------------------------


def hook(code: Path, event: Mapping[str, Any], args: Sequence[str], c: Ctx) -> dict[str, Any]:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.mcp", "guard", *args],
        input=json.dumps(event), capture_output=True, text=True, env=c.env(code),
        cwd=c.scratch, check=False,
    )  # fmt: skip
    out: dict[str, Any] = {"exit": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    if r.stdout.strip():
        body = json.loads(r.stdout)
        hso = body.get("hookSpecificOutput", {})
        reason = hso.get("permissionDecisionReason", "")
        out["shape_ok"] = list(body) == ["hookSpecificOutput"] and sorted(hso) == [
            "hookEventName", "permissionDecision", "permissionDecisionReason",
        ]  # fmt: skip
        out["decision"] = hso.get("permissionDecision")
        out["project"] = (
            reason.split(" of project ", 1)[1].split(" (", 1)[0]
            if " of project " in reason
            else None
        )
    else:
        out.update(decision="allow", project=None, shape_ok=True)
    return out


def guard_event(row: tuple, lg: Path) -> tuple[dict[str, Any], list[str]]:
    _n, cwd, tool, arg, project, _new, _old = row
    cwd_p = lg.parent if cwd == "@base" else (lg / cwd if cwd else lg)
    arg = arg.replace("{W}", str(REPO))
    ti = {"command": arg} if tool == "Bash" else {"file_path": str(lg / arg)}
    ev = {"tool_name": tool, "tool_input": ti, "cwd": str(cwd_p), "hook_event_name": "PreToolUse"}
    return ev, (["--project", str(lg / project)] if project else [])


def describe(project: str | None) -> str:
    return f"deny {project}" if project else "allow"


def probe_guard_row(c: Ctx, row: tuple, which: str = "new") -> dict[str, Any]:
    n, *_rest = row
    lg = c.fx / "lg"
    ev, args = guard_event(row, lg)
    want = row[5] if which == "new" else row[6]
    got = hook(REPO if which == "new" else c.old, ev, args, c)
    silent_ok = got["decision"] == "allow" and got["exit"] == 0 and got["stdout"] == ""
    deny_ok = got["decision"] == "deny" and got["shape_ok"] and got["exit"] == 0
    checks = {
        f"{which}: {describe(want)}": (silent_ok if want is None else deny_ok)
        and got["project"] == want,
    }
    if which == "new" and n in (1, 13):
        x = hook(REPO, ev, [*args, "--mode", "exit2"], c)
        checks["--mode exit2: exit 2 with the reason on stderr"] = (
            x["exit"] == 2 and x["stdout"] == "" and f"of project {want} (" in x["stderr"]
        )
    return {
        "checks": checks,
        "observed": describe(got["project"]) if got["decision"] == "deny" else "allow",
        "facts": {"exit": got["exit"], "decision": got["decision"], "project": got["project"]},
    }


def decide_time(c: Ctx, row_no: int) -> float:
    from lattice.mcp.guard import MultiGuard  # the tree under test (REPO first on sys.path)

    row = next(r for r in GUARD_ROWS if r[0] == row_no)
    ev, args = guard_event(row, c.fx / "lg")
    g = MultiGuard([Path(a) for a in args[1::2]])
    t0 = time.perf_counter()
    g.decide(ev)
    return time.perf_counter() - t0


# ---- L-regress R1 ------------------------------------------------------------------------------


def probe_r1(c: Ctx) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location(
        "mp_probes", REPO / "docs/results/data/2026-10-12-multi/mp_probes.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.mp2(c.scratch / "mp")
    checks = {f"MP-2 {label}: {r['passed']}/{r['of']}": r["passed"] == r["of"] == 10
              for label, r in out.items()}  # fmt: skip
    return {
        "checks": checks,
        "facts": {
            k: [(r["case"], r["decision"], r["ok"]) for r in v["rows"]] for k, v in out.items()
        },
    }


# ---- the table ---------------------------------------------------------------------------------

Probe = Callable[[Ctx], dict[str, Any]]


def build_probes() -> list[tuple[str, str, Probe]]:
    ps: list[tuple[str, str, Probe]] = [
        ("P1", "holds", probe_p1),
        ("P2", "byte-identical to old", probe_p2),
        ("P3", "workspace server (old: the same)", probe_p3),
        ("P4", "workspace server (old: single project)", probe_p4),
        ("P5", "byte-identical to old; stderr: not used", probe_p5),
        ("P6", "workspace server (old: the same)", probe_p6),
        ("P7", "byte-identical to old", probe_p7),
        ("U1", "a, b via Studio; c local; 2 listings; writes local", probe_u1),
        ("U2", "a via /api/model, b via /p/beta, c local", probe_u2),
        ("U3", "a via Studio; b local (fallback)", probe_u3),
        ("U4", "b: Studio answered 500 twice; 2 listings", probe_u4),
        ("U5", "0 listings; byte-identical to old", probe_u5),
        ("U6", "b via /p/beta", probe_u6),
    ]
    for row in GUARD_ROWS:
        n = row[0]
        ps.append((f"G{n:02d}", describe(row[5]), lambda c, r=row: probe_guard_row(c, r, "new")))
    for row in GUARD_ROWS:
        n = row[0]
        ps.append(
            (f"G{n:02d}.old", describe(row[6]), lambda c, r=row: probe_guard_row(c, r, "old"))
        )
    ps.append(("R1", "10/10 and 10/10", probe_r1))
    return ps


def real_hub(c: Ctx) -> dict[str, Any]:
    """Reported only: one real hub (``python -m lattice.studio``, no DIR) over two copies of
    dogfood/studio as children of ``fx/hub``; each copy's model_get arrives through it."""
    hub = c.fx / "hub"
    names = {"one": "plumb-one", "two": "plumb-two"}
    for d, name in names.items():
        copy_studio(hub / d)
        toml = hub / d / "studio.toml"
        toml.write_text(toml.read_text().replace('name = "lattice-studio"', f'name = "{name}"'))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    errlog = (c.scratch / "hub.stderr").open("w")
    p = subprocess.Popen(
        [sys.executable, "-m", "lattice.studio", "--stub-llm", "--quiet", "--port", str(port)],
        cwd=hub, env=c.env(REPO), stdout=errlog, stderr=errlog,
    )  # fmt: skip
    try:
        import urllib.request

        for _ in range(100):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/projects", timeout=2
                ) as r:
                    listing = json.loads(r.read())
                break
            except OSError:
                time.sleep(0.2)
        else:
            return {"error": "the hub did not start"}
        url = f"http://127.0.0.1:{port}"
        reqs = [rpc(1, "initialize", INIT)]
        for i, name in enumerate(names.values()):
            reqs.append(tool_call(2 + i, "model_get", project=name))
            reqs.append(tool_call(10 + i, "project_status", project=name))
        argv = ["--studio-url", url]
        for d in names:
            argv = ["--project", str(hub / d), *argv]
        lines, err = c.serve(REPO, argv, reqs, c.fx)
        got = [tool_result(x) for x in lines[1:]]
        return {
            "hub_listing": [{"name": r["name"], "root": r["root"]} for r in listing],
            "replies": [
                {
                    "error": e,
                    "project": d.get("project"),
                    "backend": d.get("backend"),
                    "rule_count": d.get("rule_count"),
                }
                if not e
                else {"error": d}
                for e, d in got
            ],
            "stderr": err,
        }
    finally:
        p.terminate()
        p.wait(timeout=30)
        errlog.close()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--old", type=Path, required=True, help="git archive a4ab4a1 lattice, extracted"
    )
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--real-hub", action="store_true", help="also the reported-only real hub run")
    ns = ap.parse_args(argv)
    c = Ctx(ns.old.resolve(), ns.scratch.resolve())
    only = {x for x in ns.only.split(",") if x}
    probes = [p for p in build_probes() if not only or p[0] in only]
    if ns.out:
        ns.out.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    times: dict[str, list[float]] = {"C16": [], "C29": []}
    t_start = time.monotonic()
    for k in range(1, ns.runs + 1):
        if c.fx.exists():
            shutil.rmtree(c.fx)
        c.fx.mkdir(parents=True)
        build_plugin_fixtures(c.fx)
        build_guard_fixture(c.fx / "lg")
        shutil.rmtree(c.scratch / "mp", ignore_errors=True)
        res: dict[str, Any] = {}
        for pid, _pred, fn in probes:
            try:
                res[pid] = fn(c)
            except Exception as e:  # noqa: BLE001 -- a probe bug is reported, not hidden
                res[pid] = {"checks": {f"probe raised {type(e).__name__}: {e}": False}, "facts": {}}
        if not only or {"C16", "C29"} & only:
            times["C16"].append(decide_time(c, 16))
            times["C29"].append(decide_time(c, 29))
        runs.append(res)
        if ns.out:
            (ns.out / f"run-{k:02d}.json").write_text(json.dumps(res, indent=1, sort_keys=True))
        print(
            f"run {k}/{ns.runs} done ({time.monotonic() - t_start:.0f}s)",
            file=sys.stderr,
            flush=True,
        )

    rows: list[dict[str, Any]] = []
    print("id | predicted | observed | pass")
    for pid, pred, _fn in probes:
        per = [r[pid] for r in runs]
        all_checks = all(all(x["checks"].values()) for x in per)
        outcomes = {
            canon({"c": x["checks"], "f": x.get("facts"), "o": x.get("observed")}) for x in per
        }
        identical = len(outcomes) == 1
        failed = sorted({k for x in per for k, v in x["checks"].items() if not v})
        observed = per[0].get("observed")
        if observed is None:
            observed = "holds" if all_checks else "FAILED: " + "; ".join(failed)
        if all_checks and not identical:
            observed += " (outcomes differ between runs)"
        ok = all_checks and identical
        rows.append({"id": pid, "predicted": pred, "observed": observed, "pass": ok,
                     "failed_checks": failed, "identical_runs": identical})  # fmt: skip
        print(f"{pid} | {pred} | {observed} | {'pass' if ok else 'FAIL'}")
        for f in failed:
            print(f"    failed check: {f}")
    cost: dict[str, Any] = {}
    cost_ok = True
    if times["C16"]:
        m16, x16, m29 = (
            statistics.median(times["C16"]),
            max(times["C16"]),
            statistics.median(times["C29"]),
        )
        cost = {
            "C16_median_s": m16, "C16_max_s": x16, "C29_median_s": m29,
            "C16_samples": times["C16"], "C29_samples": times["C29"],
        }  # fmt: skip
        ok16, ok29 = m16 <= 1.0 and x16 <= 2.0, m29 <= 0.5
        cost_ok = ok16 and ok29
        print(f"C16 | median <= 1.0 s, max <= 2.0 s | median {m16:.4f} s, max {x16:.4f} s | "
              f"{'pass' if ok16 else 'FAIL'}")  # fmt: skip
        print(f"C29 | median <= 0.5 s | median {m29:.4f} s | {'pass' if ok29 else 'FAIL'}")
        rows.append({"id": "C16", "pass": ok16})
        rows.append({"id": "C29", "pass": ok29})
    summary: dict[str, Any] = {"runs": ns.runs, "rows": rows, "cost": cost,
                               "wall_s": round(time.monotonic() - t_start, 1)}  # fmt: skip
    if ns.real_hub:
        summary["real_hub"] = real_hub(c)
        print("real hub (reported only):", json.dumps(summary["real_hub"], sort_keys=True)[:1500])
    passed = sum(1 for r in rows if r["pass"])
    if ns.out:
        (ns.out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(f"probes: {passed}/{len(rows)} pass")
    return 0 if passed == len(rows) and cost_ok else 1


if __name__ == "__main__":
    sys.exit(main())
