"""The MP-1 and MP-2 probes of docs/results/2026-10-12-multi-prereg.md (run by hand; not a test).

    uv run python docs/results/data/2026-10-12-multi/mp_probes.py SCRATCH OUTDIR

SCRATCH holds ``streak`` (a git clone of /Users/vivekgade/Documents/streak), ``studio`` (a copy of
dogfood/studio) and ``launch/lattice.workspace.toml`` naming both. MP-1 talks to ONE real
``python -m lattice.mcp serve`` (no --project) over its stdio pipe, a message at a time, three
times; then the byte-compatibility comparison against the old code in
/Users/vivekgade/Documents/lattice. MP-2 runs the real guard hook on the 10 pre-registered cases.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

NEW = Path(__file__).resolve().parents[4]
OLD = Path("/Users/vivekgade/Documents/lattice")
STREAK_REAL = Path("/Users/vivekgade/Documents/streak")


def env(pythonpath: Path) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e["PYTHONPATH"] = str(pythonpath)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e.pop("LATTICE_WORKSPACE", None)
    return e


class Server:
    """One MCP server process; `ask` sends one request and reads its one reply."""

    def __init__(self, args: list[str], cwd: Path, code: Path) -> None:
        self.p = subprocess.Popen(
            [sys.executable, "-m", "lattice.mcp", "serve", *args],
            cwd=cwd, env=env(code), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )  # fmt: skip
        self.i = 0
        self.log: list[dict[str, Any]] = []

    def raw(self, method: str, params: dict[str, Any] | None = None) -> str:
        self.i += 1
        msg = {"jsonrpc": "2.0", "id": self.i, "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        self.log.append({"request": msg, "reply": json.loads(line)})
        return line

    def ask(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return json.loads(self.raw(method, params))

    def tool(self, tool_name: str, **args: Any) -> tuple[bool, Any]:
        r = self.ask("tools/call", {"name": tool_name, "arguments": args})["result"]
        text = r["content"][0]["text"]
        return r["isError"], (text if r["isError"] else json.loads(text))

    def close(self) -> str:
        self.p.stdin.close()
        self.p.wait(timeout=60)
        return self.p.stderr.read()


INIT = {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mp1"}}


def mp1_run(scratch: Path) -> dict[str, Any]:
    streak_r1 = json.loads((scratch / "streak" / "spec.trace.json").read_text())["rules"]
    studio_r1 = json.loads((scratch / "studio" / "spec.trace.json").read_text())["rules"]
    s = Server([], scratch / "launch", NEW)
    checks: dict[str, bool] = {}
    s.ask("initialize", INIT)
    err, lst = s.tool("project_list")
    rows = {p["name"]: p for p in lst["projects"]}
    checks["1 project_list shows both"] = (
        not err
        and rows.get("streak", {}).get("spec") == "present"
        and rows.get("studio", {}).get("spec") == "present"
        and rows["streak"]["source"] == rows["studio"]["source"] == "workspace"
        and lst["projects"][0]["source"] == "launch"
    )
    e1, ms = s.tool("model_get", project="streak")
    e2, md = s.tool("model_get", project="studio")
    clip = lambda t: t if len(t) <= 160 else t[:160] + "..."  # noqa: E731 -- tools._clip

    def overview_is(res: Any, project: str, rules: list[dict[str, Any]]) -> bool:
        """A model_get overview of `project` with these rules; a result over the 20 000-char cap
        comes as the truncation envelope, and its preview (a prefix of the same JSON) is read."""
        want = {"project": project, "rule_count": len(rules)}
        r1 = clip(rules[0]["text"])
        if isinstance(res, dict) and res.get("truncated"):
            pv = res["preview"]
            return (pv.startswith(json.dumps(want, separators=(",", ":"))[:-1] + ",")
                    or (f'"project":"{project}"' in pv and f'"rule_count":{len(rules)}' in pv)) \
                and json.dumps(r1, ensure_ascii=False) in pv  # fmt: skip
        return (isinstance(res, dict) and res.get("project") == project
                and res.get("rule_count") == len(rules) and res["rules"][0]["text"] == r1)  # fmt: skip

    checks["2 model_get per project"] = (
        not e1 and not e2 and overview_is(ms, "streak", streak_r1)
        and overview_is(md, "studio", studio_r1)
    )  # fmt: skip
    e3, _ = s.tool("project_set_active", name="streak")
    e4, m_def = s.tool("model_get")
    e5, _ = s.tool("project_set_active", name="studio")
    e6, r1 = s.tool("rule_get", id="R1")
    checks["3 project_set_active changes the default"] = (
        not (e3 or e4 or e5 or e6)
        and overview_is(m_def, "streak", streak_r1)
        and r1["project"] == "studio" and r1["text"] == studio_r1[0]["text"]
    )  # fmt: skip
    e7, t7 = s.tool("model_get", project="nope")
    e8, t8 = s.tool("project_set_active", name="nope")
    checks["4 unknown project is a tool error listing names"] = (
        e7 and e8 and all("streak" in t and "studio" in t for t in (t7, t8))
    )
    stderr = s.close()
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "facts": {
            "streak_overview_truncated": bool(ms.get("truncated")),
            "streak_preview_head": (ms.get("preview") or json.dumps(ms))[:240],
            "studio_rule_count": md.get("rule_count"),
            "studio_R1": md["rules"][0]["text"][:80],
            "unknown_error": t7,
        },
        "transcript": s.log,
        "stderr_tail": stderr[-2000:],
    }


def compat(scratch: Path) -> dict[str, Any]:
    proj = scratch / "studio"
    out: dict[str, Any] = {}
    for label, code in (("new", NEW), ("old", OLD)):
        s = Server(["--project", str(proj)], scratch, code)
        out[label] = {
            "initialize": s.raw("initialize", INIT),
            "tools/list": s.raw("tools/list"),
            "model_get": s.raw("tools/call", {"name": "model_get", "arguments": {}}),
            "project_status": s.raw("tools/call", {"name": "project_status", "arguments": {}}),
        }
        s.close()
    new, old = out["new"], out["old"]
    old_tools = json.loads(old["tools/list"])["result"]["tools"]
    new_tools = json.loads(new["tools/list"])["result"]["tools"]
    dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    trimmed = [t for t in new_tools if t["name"] not in ("project_list", "project_set_active")]
    res = {
        "a initialize identical": new["initialize"] == old["initialize"],
        "b tools/list identical minus the two appended tools": dump(trimmed) == dump(old_tools)
        and dump(old_tools)[:-1] == dump(new_tools)[: len(dump(old_tools)) - 1]
        and [t["name"] for t in new_tools[len(old_tools) :]] == ["project_list", "project_set_active"],
        "c model_get identical": new["model_get"] == old["model_get"],
        "c project_status identical": new["project_status"] == old["project_status"],
    }
    return {"checks": res, "pass": all(res.values()), "raw": out}


def hook(event: dict[str, Any], args: list[str]) -> dict[str, Any]:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.mcp", "guard", *args],
        input=json.dumps(event), capture_output=True, text=True, env=env(NEW), check=False,
    )  # fmt: skip
    if r.stdout.strip():
        out = json.loads(r.stdout)["hookSpecificOutput"]
        return {"decision": out["permissionDecision"], "reason": out["permissionDecisionReason"],
                "exit": r.returncode}  # fmt: skip
    return {"decision": "allow", "reason": "", "exit": r.returncode}


def mp2(scratch: Path) -> dict[str, Any]:
    S, D = STREAK_REAL, NEW / "dogfood" / "studio"
    N = scratch / "mp2" / "nospec"
    C = scratch / "mp2" / "c"
    N.mkdir(parents=True, exist_ok=True)
    C.mkdir(parents=True, exist_ok=True)
    alias, dalias = C / "alias.py", C / "dalias"
    if not alias.is_symlink():
        alias.symlink_to(S / "spec.py")
    if not dalias.is_symlink():
        dalias.symlink_to(D, target_is_directory=True)

    def e(tool: str, cwd: Path, **ti: Any) -> dict[str, Any]:
        return {"tool_name": tool, "tool_input": ti, "cwd": str(cwd), "hook_event_name": "PreToolUse"}

    cases = [
        (1, e("Edit", D, file_path=str(S / "spec.py")), "streak"),
        (2, e("Bash", D, command=f"echo x > {S / 'spec.py'}"), "streak"),
        (3, e("Edit", D, file_path=str(D / "spec.py")), "lattice-studio"),
        (4, e("Bash", D, command="echo x >> spec.trace.json"), "lattice-studio"),
        (5, e("Edit", D, file_path=str(S / "harness.py")), None),
        (6, e("Bash", D, command="echo x > harness.py"), None),
        (7, e("Edit", N, file_path=str(N / "spec.py")), None),
        (8, e("Bash", N, command="echo x > spec.py"), None),
        (9, e("Write", C, file_path=str(alias)), "streak"),
        (10, e("Edit", C, file_path=str(dalias / "spec.trace.json")), "lattice-studio"),
    ]
    out: dict[str, Any] = {}
    for label, args in (("both --project", ["--project", str(S), "--project", str(D)]),
                        ("no --project", [])):  # fmt: skip
        rows = []
        for n, ev, want in cases:
            got = hook(ev, args)
            ok = (got["decision"] == "allow") if want is None else (
                got["decision"] == "deny" and f"of project {want} (" in got["reason"]
            )  # fmt: skip
            rows.append({"case": n, "event": ev, "expected": want or "allow", **got, "ok": ok})
        out[label] = {"rows": rows, "passed": sum(r["ok"] for r in rows), "of": len(rows)}
    return out


def main() -> int:
    scratch, outdir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    runs = [mp1_run(scratch) for _ in range(3)]
    for i, r in enumerate(runs, 1):
        (outdir / f"mp1-run{i}.json").write_text(json.dumps(r, indent=1))
        print(f"MP-1 run {i}: {'PASS' if r['pass'] else 'FAIL'} {r['checks']} {r['facts']}")
    c = compat(scratch)
    (outdir / "mp1-compat.json").write_text(json.dumps(c, indent=1))
    print(f"MP-1 compat: {'PASS' if c['pass'] else 'FAIL'} {c['checks']}")
    g = mp2(scratch)
    (outdir / "mp2-guard.json").write_text(json.dumps(g, indent=1))
    for label, r in g.items():
        print(f"MP-2 {label}: {r['passed']}/{r['of']}")
        for row in r["rows"]:
            if not row["ok"]:
                print("   FAIL", row["case"], row["decision"], row["reason"][:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
