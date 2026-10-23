#!/usr/bin/env python3
"""The two .NET rows the parity round left NOT RUN, with the ``claude`` CLI as the model (never an API key).

Pre-registration: docs/results/2026-10-23-dotnet-real-agent-prereg.md (committed before the first model call; the driver
refuses to make one otherwise).

    uv run python dogfood/dn_real_agent_run.py calibrate --out OUT            # no model: reference passes, mutants fail
    uv run python dogfood/dn_real_agent_run.py build     --out OUT            # (a) real-agent toy build, ONCE
    uv run python dogfood/dn_real_agent_run.py ask       --out OUT            # (b) real-model C-ASK, ONCE
    uv run python dogfood/dn_real_agent_run.py {build,ask} --self-test --out SCRATCH   # scripted stand-ins, no model

Every ``claude`` call goes through ``dn_real_agent_shim.py``: ``--max-budget-usd`` sized from the remaining budget, one
ledger line per invocation (``OUT/ledger.jsonl``, append-only), ANTHROPIC_* stripped. TOTAL cap USD 5.00 for (a)+(b).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (str(REPO), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FIXTURE = HERE / "dn_real_agent" / "fixture"
REFERENCE = HERE / "dn_real_agent" / "reference" / "Program.cs"
SHIM = HERE / "dn_real_agent_shim.py"
PREREG = REPO / "docs/results/2026-10-23-dotnet-real-agent-prereg.md"
STREAK = Path.home() / "Documents" / "streak"
STREAK_REV = "a0679e9"

TOTAL_CAP = 5.00
BUILD_CAP = 3.00  # the build phase may spend at most this much; the ask phase gets the rest of the total
BUILD_CALL_CAP = 1.50  # ceiling of one agent step
ASK_CALL_CAP = 1.00  # ceiling of one ask call
MODEL = "sonnet"
MAX_ROUNDS = 3
PROTECTED_EXTRA = ("spec.py", "spec.trace.json", "REQUIREMENTS.md", "studio.toml")

MUTANTS = {
    "list order reversed": ("OrderBy(i => i.Id)", "OrderByDescending(i => i.Id)"),
    "negative quantity allowed": ("qty < 0", "qty < -1000"),
    "duplicate answers 400, not 409": ('Detail(409,', 'Detail(400,'),
}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(cwd: Path, *args: str, check: bool = False) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)
    return p.stdout.strip()


def gate_env() -> dict[str, str]:
    """The environment of a gate run outside the loop (the loop builds its own, ``rebuild.gate_env``)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))}
    env.update(
        PYTHONPATH=os.pathsep.join(dict.fromkeys([str(REPO), env.get("PYTHONPATH", "")])).strip(os.pathsep),
        UV_PROJECT_ENVIRONMENT=str(Path(sys.prefix)),
        UV_NO_SYNC="1",
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
        DOTNET_NOLOGO="1",
    )
    return env


def run_gate(d: Path, *, json_out: bool = False) -> dict[str, Any]:
    env = gate_env()
    if json_out:
        env["DN_CHECK_JSON"] = "1"
    shutil.rmtree(d / "bin", ignore_errors=True)
    shutil.rmtree(d / "obj", ignore_errors=True)
    shutil.rmtree(d / "metrics", ignore_errors=True)
    t0 = time.time()
    p = subprocess.run(["sh", "./check.sh"], cwd=d, capture_output=True, text=True, env=env, timeout=900)
    out: dict[str, Any] = {"rc": p.returncode, "tail": (p.stdout + p.stderr)[-500:], "seconds": round(time.time() - t0, 1)}
    f = d / "metrics" / "last-check.json"
    if json_out and f.is_file():
        doc = json.loads(f.read_text())
        out["verdict"] = doc.get("verdict")
        out["counts"] = doc.get("counts")
        out["findings"] = len(doc.get("findings") or [])
    return out


def dotnet_build(d: Path) -> dict[str, Any]:
    env = gate_env()
    shutil.rmtree(d / "bin", ignore_errors=True)
    shutil.rmtree(d / "obj", ignore_errors=True)
    p = subprocess.run(
        ["dotnet", "build", "dnbuild.csproj", "-c", "Release", "-o", "bin/out", "--nologo", "-v", "q"],
        cwd=d, capture_output=True, text=True, env=env, timeout=900,
    )
    return {"rc": p.returncode, "tail": (p.stdout + p.stderr)[-400:]}


def make_repo(dest: Path, overlay: dict[str, str | None] | None = None) -> Path:
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(FIXTURE, dest, ignore=shutil.ignore_patterns("__pycache__", "bin", "obj", "metrics", ".lattice"))
    for rel, text in (overlay or {}).items():
        f = dest / rel
        if text is None:
            f.unlink(missing_ok=True)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
    env = {**os.environ, "GIT_AUTHOR_NAME": "dn-real-agent", "GIT_AUTHOR_EMAIL": "dn@example.invalid",
           "GIT_COMMITTER_NAME": "dn-real-agent", "GIT_COMMITTER_EMAIL": "dn@example.invalid"}
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=dest, check=True, env=env, capture_output=True)
    return dest


# ---- the pre-call guard: the prereg and the code are committed, the run happens once ----------------------------------------------------------------------


def frozen_files() -> list[Path]:
    fx = sorted(p for p in FIXTURE.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return [PREREG, Path(__file__).resolve(), SHIM, REFERENCE, *fx, HERE / "dotnet_probes.py"]


def guard(out: Path, what: str, self_test: bool) -> dict[str, Any]:
    info = {"head": git(REPO, "rev-parse", "HEAD"), "files": {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in frozen_files()}}
    if self_test:
        return info
    dirty = [f for f in info["files"] if git(REPO, "status", "--porcelain", "--", f)]
    if dirty:
        raise SystemExit(f"refusing to call the model: not committed (prereg first): {dirty}")
    if not git(REPO, "log", "-1", "--format=%H", "--", str(PREREG.relative_to(REPO))):
        raise SystemExit("refusing to call the model: the prereg is not in a commit")
    done = out / f"{what}-result.json"
    if done.exists():
        raise SystemExit(f"{done} exists: the {what} run happens once (a failure is a result)")
    for k in list(os.environ):
        if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_")) or k == "AWS_BEARER_TOKEN_BEDROCK":
            del os.environ[k]
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{what}-freeze.json").write_text(json.dumps({**info, "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(), "at": time.time()}, indent=1))
    return info


def shim_env(out: Path, phase: str, phase_cap: float, call_cap: float) -> dict[str, str]:
    claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    return {
        "DNRA_REAL": claude,
        "DNRA_LEDGER": str(out / "ledger.jsonl"),
        "DNRA_CAP": f"{TOTAL_CAP:.2f}",
        "DNRA_PHASE": phase,
        "DNRA_PHASE_CAP": f"{phase_cap:.2f}",
        "DNRA_CALL_CAP": f"{call_cap:.2f}",
        "DNRA_LOGDIR": str(out / f"{phase}-raw"),
    }


def ledger_total(out: Path, phase: str | None = None) -> float:
    import dn_real_agent_shim as S

    return S.spent(out / "ledger.jsonl", phase)


# ---- calibrate (no model) ---------------------------------------------------------------------------------------------------------------------------------


def cmd_calibrate(a: argparse.Namespace) -> int:
    out, scratch = Path(a.out), Path(a.scratch)
    out.mkdir(parents=True, exist_ok=True)
    ref = REFERENCE.read_text()
    res: dict[str, Any] = {}
    res["scaffold (must fail)"] = run_gate(make_repo(scratch / "cal-scaffold"))
    res["reference (must pass)"] = run_gate(make_repo(scratch / "cal-ref", {"Program.cs": ref}), json_out=True)
    for name, (x, y) in MUTANTS.items():
        if x not in ref:
            res[f"mutant: {name}"] = {"error": "mutation site not found"}
            continue
        res[f"mutant: {name}"] = run_gate(make_repo(scratch / ("cal-" + re.sub(r"\W+", "-", name)), {"Program.cs": ref.replace(x, y, 1)}))
    ok = (
        res["scaffold (must fail)"]["rc"] != 0
        and res["reference (must pass)"]["rc"] == 0
        and res["reference (must pass)"].get("findings") == 0
        and all(res[f"mutant: {n}"].get("rc", 0) != 0 and "error" not in res[f"mutant: {n}"] for n in MUTANTS)
    )
    res["ok"] = ok
    (out / "calibration.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: (v if k == "ok" else v.get("rc", v)) for k, v in res.items()}, indent=1))
    return 0 if ok else 1


# ---- (a) the real-agent build ------------------------------------------------------------------------------------------------------------------------------


def cmd_build(a: argparse.Namespace) -> int:
    from lattice.studio.rebuild import FINISHED, Rebuilder

    out, scratch = Path(a.out), Path(a.scratch)
    stub = bool(a.self_test)
    info = guard(out, "build", stub)
    out.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    overlay: dict[str, str | None] = {}
    repo = make_repo(scratch / "build-repo", overlay)
    cfg = tomllib.loads((repo / "studio.toml").read_text())
    protected = list(cfg.get("mcp", {}).get("protected", []))
    table = dict(cfg["rebuild"])
    if stub:
        table["agent"] = "stub"
        (repo / "stub-rebuild.json").write_text(json.dumps({"rounds": [{"files": {"Program.cs": REFERENCE.read_text()}, "summary": "self-test: scripted stub agent"}]}))
        git(repo, "add", "stub-rebuild.json")
        subprocess.run(["git", "-c", "user.name=dn", "-c", "user.email=dn@example.invalid", "commit", "-q", "-m", "stub script"], cwd=repo, check=True)
        claude_bin = "claude"
    else:
        os.environ.update(shim_env(out, "build", BUILD_CAP, BUILD_CALL_CAP))
        claude_bin = str(SHIM)
    log_lines: list[str] = []
    rb = Rebuilder(repo, table, name="dnbuild", model=MODEL, claude_bin=claude_bin, protected=protected, log=log_lines.append, trailer="Lattice-Rebuild: dn-real-agent")
    ok_av, why = rb.available()
    if not ok_av:
        (out / "build-result.json").write_text(json.dumps({"status": "NOT RUN", "why": f"the rebuild loop is not available: {why}"}, indent=1))
        print("NOT RUN:", why)
        return 2
    t0 = time.time()
    started = rb.start([], scope="all", model=MODEL, max_rounds=MAX_ROUNDS, baseline_wait=300)
    rid = started["id"] if isinstance(started, dict) and "id" in started else str(started)
    rec: dict[str, Any] = {}
    while time.time() - t0 < float(a.timeout):
        rec = rb.get(rid, wait=10.0)
        if rec.get("status") in FINISHED:
            break
        time.sleep(2 if stub else 5)
    else:
        rb.stop(rid)
        rec = rb.get(rid)
    rb.close()
    wall = round(time.time() - t0, 1)
    rounds = rec.get("rounds") or []
    base, branch = rec.get("base"), rec.get("branch")
    tip = git(repo, "rev-parse", branch) if branch else ""
    changed = git(repo, "diff", "--name-only", base, tip).split() if base and tip else []
    spent = ledger_total(out, "build") if not stub else 0.0
    usage_limit = any("usage limit" in json.dumps(r).lower() for r in rounds) or "usage limit" in json.dumps(rec.get("error") or "").lower()
    result: dict[str, Any] = {
        "self_test": stub, "head": info["head"], "model": MODEL, "loop_status": rec.get("status"), "rounds": len(rounds), "round_summaries": [
            {"n": r.get("n"), "outcome": r.get("outcome") or r.get("status"), "summary": str(r.get("summary") or "")[:300]} for r in rounds],
        "wall_s": wall, "build_usd": spent, "total_usd": ledger_total(out) if not stub else 0.0, "base": base, "tip": tip, "files_changed": changed,
        "record_error": rec.get("error"), "usage_limit_or_cap_hit": usage_limit,
    }
    # gates on a clean archive of the tip, three times (the loop's own verdict is not trusted)
    clean = scratch / "build-clean"
    shutil.rmtree(clean, ignore_errors=True)
    clean.mkdir()
    if tip:
        arch = subprocess.run(["git", "archive", tip], cwd=repo, capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(clean)], input=arch.stdout, check=True)
        result["gate_build"] = dotnet_build(clean)
        runs = [run_gate(clean, json_out=True) for _ in range(3)]
        result["gate_check_runs"] = runs
        sources = [p.read_text(errors="replace") for p in clean.rglob("*.cs") if "obj" not in p.parts and "bin" not in p.parts]
        blob = "\n".join(sources)
        result["uses_ef_core"] = {"DbContext": "DbContext" in blob, "UseSqlite": "UseSqlite" in blob, "using Microsoft.EntityFrameworkCore": "Microsoft.EntityFrameworkCore" in blob}
        result["cs_files"] = sorted(str(p.relative_to(clean)) for p in clean.rglob("*.cs") if "obj" not in p.parts and "bin" not in p.parts)
        same = {}
        for pth in sorted(set(protected) | set(PROTECTED_EXTRA)):
            if base:
                same[pth] = git(repo, "rev-parse", f"{base}:{pth}") == git(repo, "rev-parse", f"{tip}:{pth}")
        result["protected_unchanged"] = same
    g_build = bool(tip) and result["gate_build"]["rc"] == 0
    g_check = bool(tip) and all(r["rc"] == 0 and r.get("verdict") == "pass" and r.get("findings") == 0 for r in result["gate_check_runs"])
    result["verdicts"] = {
        "B-BUILD (dotnet build exit 0 on a clean archive of the tip)": g_build,
        "B-CHECK (the Lattice check, layer D: verdict pass, 0 findings, 3 of 3 runs)": g_check,
        "B-LOOP (loop status passed in <= 3 rounds)": rec.get("status") == "passed" and len(rounds) <= MAX_ROUNDS,
        "B-PROTECT (spec, requirements, harness, check script, csproj unchanged)": bool(result.get("protected_unchanged")) and all(result["protected_unchanged"].values()),
        "B-EF (the source uses EF Core: DbContext and UseSqlite)": bool(tip) and result["uses_ef_core"]["DbContext"] and result["uses_ef_core"]["UseSqlite"],
        "B-COST (total ledger <= USD 5.00)": result["total_usd"] <= TOTAL_CAP,
    }
    (out / "build-loop-log.txt").write_text("\n".join(log_lines))
    (out / "build-record.json").write_text(json.dumps(rec, indent=1, default=str))
    if tip:
        (out / "build-tip.tar").write_bytes(subprocess.run(["git", "archive", tip], cwd=repo, capture_output=True, check=True).stdout)
    (out / "build-result.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({"verdicts": result["verdicts"], "rounds": len(rounds), "usd": result["total_usd"]}, indent=1))
    return 0


# ---- (b) real-model C-ASK ----------------------------------------------------------------------------------------------------------------------------------

REF_RX = {
    "route": re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}.\-:]*)"),
    "rule": re.compile(r"\bR(\d{1,3})\b"),
}


def audit_claims(ans: dict[str, Any], routes: dict[str, dict[str, Any]], module_dir: Path) -> dict[str, Any]:
    """The references the answer's text makes to the spec or the code, and which of them an accepted citation backs.

    A reference is (a) a ``code:<path>:<line>`` token, (b) a rule id ``R<n>``, (c) ``METHOD /path`` of a route in the code.
    A route string that is not a route of the code is a negative or unknown mention: counted apart, not as uncited."""
    import dotnet_probes as P

    text = f"{ans.get('answer') or ''}\n{ans.get('explanation') or ''}"
    cites = ans.get("citations") or []
    targets = [str(c.get("target") or "") for c in cites]
    code_cited = {m.groups() for t in targets if (m := P.CITE_RX.fullmatch(t))}
    code_cited = {(p, int(n)) for p, n in code_cited}
    refs: list[dict[str, Any]] = []
    for p, n in sorted(P.scan_citations(text)):
        refs.append({"ref": f"code:{p}:{n}", "cited": (p, n) in code_cited})
    for rid in sorted({f"R{m.group(1)}" for m in REF_RX["rule"].finditer(text)}):
        refs.append({"ref": rid, "cited": any(t == rid or t.endswith(":" + rid) or t.endswith("/" + rid) for t in targets)})
    unknown: list[str] = []
    for m in sorted({(m.group(1), m.group(2).rstrip(".,;:)")) for m in REF_RX["route"].finditer(text)}):
        key = f"{m[0]} {m[1]}"
        r = routes.get(key)
        if r is None:
            unknown.append(key)
            continue
        loc = (r["handler"]["file"], r["handler"]["line"])
        refs.append({"ref": key, "cited": loc in code_cited or any(m[1] in str(c.get("quote") or "") for c in cites if str(c.get("target") or "").startswith("code:"))})
    return {"references": refs, "uncited": sum(1 for r in refs if not r["cited"]), "negative_or_unknown_route_mentions": unknown}


def cmd_ask(a: argparse.Namespace) -> int:
    import dotnet_probes as P
    from lattice.llm.cli_client import CliUsageLimitError, ClaudeCliClient
    from lattice.llm.client import ScriptedGenerator
    from lattice.spec import appmap as appmap_mod
    from lattice.spec import chat_route, claim_check
    from lattice.spec.ask import ask, build_digest
    from lattice.spec.implementation import load_inventory

    out, scratch = Path(a.out), Path(a.scratch)
    stub = bool(a.self_test)
    info = guard(out, "ask", stub)
    out.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    # D1 and Streak's spec, as the C-ASK row has them
    tgt = P.materialise("d1", P.D1_TGZ, scratch / "targets", P.D1_TRUTH)
    proj = scratch / "proj"
    shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", str(STREAK), "archive", STREAK_REV, "spec.py", "spec.trace.json", "REQUIREMENTS.md", "CONTEXT.md"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=__import__("io").BytesIO(arch)) as tf:
        tf.extractall(proj, filter="data")
    inv_dir = scratch / "inventory"
    shutil.rmtree(inv_dir, ignore_errors=True)
    env = {**os.environ, **P.DOTNET_ENV, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join([str(tgt["overlay"]), str(REPO)])}
    env = {k: v for k, v in env.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))}
    p = subprocess.run([sys.executable, "-m", "lattice.importer", "inventory", str(tgt["module_dir"]), "--out", str(inv_dir), "--package", "."], cwd=tgt["overlay"], capture_output=True, text=True, env=env, timeout=1800)
    if p.returncode != 0 or not (inv_dir / "inventory.json").is_file():
        (out / "ask-result.json").write_text(json.dumps({"status": "NOT RUN", "why": "inventory failed", "stderr": p.stderr[-1500:]}, indent=1))
        print("NOT RUN: inventory failed", p.stderr[-300:])
        return 2
    spec, trace = P.load_proj_spec(proj)
    inv = load_inventory(inv_dir, tgt["module_dir"])
    digest = build_digest(spec, trace, inv)
    from lattice import dncheck

    ex = dncheck.extract_json(tgt["module_dir"])
    routes = {f"{r['method']} {r['path']}": r for r in ex["routes"]}
    truth = json.loads(P.D1_TRUTH.read_text())
    sync_in_truth = any("/sync" in x["template"] for x in truth["routes"])
    try:
        am = appmap_mod.build(spec, trace, inv)
    except Exception as err:  # noqa: BLE001 -- no map: the checker still runs, the router is reported as unavailable
        am = None
        am_err = f"{type(err).__name__}: {err}"
    else:
        am_err = None
    if stub:
        body = lambda q: json.dumps({"kind": "yes", "answer": "scripted", "explanation": "scripted", "citations": [], "implementation": "unsettled"})  # noqa: E731
        gens = {q: ScriptedGenerator([body(q)] * 2) for q, _, _ in P.ASK_QUESTIONS}
        gen_for = lambda q: gens[q]  # noqa: E731
    else:
        os.environ.update(shim_env(out, "ask", TOTAL_CAP - ledger_total(out, "build"), ASK_CALL_CAP))
        empty = scratch / "ask-cwd"
        empty.mkdir(exist_ok=True)
        client = ClaudeCliClient(model=MODEL, binary=str(SHIM), cwd=empty, max_retries=1, refusal_retries=0, timeout=600.0)
        gen_for = lambda q: client  # noqa: E731
    rows: list[dict[str, Any]] = []
    not_run_rest = False
    for question, registered, route in P.ASK_QUESTIONS:
        if not_run_rest:
            rows.append({"question": question, "status": "NOT RUN", "why": "the spend cap or a usage limit was reached"})
            continue
        routed = None
        if am is not None:
            try:
                r = chat_route.route(question, appmap=am)
                routed = r.route if r is not None else None
            except Exception as err:  # noqa: BLE001
                routed = f"error: {type(err).__name__}"
        t0 = time.time()
        try:
            ans = ask(spec, question, trace=trace, generator=gen_for(question), model=MODEL, inventory=inv)
        except CliUsageLimitError as err:
            rows.append({"question": question, "status": "NOT RUN", "why": f"usage limit or cap: {err}"})
            not_run_rest = True
            continue
        except Exception as err:  # noqa: BLE001 -- a failure is a result
            rows.append({"question": question, "status": "ERROR", "error": f"{type(err).__name__}: {err}"})
            continue
        d = ans.to_dict()
        ctx = claim_check.CheckContext(question, appmap=am, digest=digest)
        checked = claim_check.check_answer(d, ctx)
        family = P.impl_family(ans.implementation)
        truth_kind = "implemented" if (route is not None or sync_in_truth) else "not_implemented"
        quotes = {}
        for c in d.get("citations", ()):
            m = P.CITE_RX.fullmatch(str(c.get("target", "")))
            if m and c.get("quote"):
                quotes[(m.group(1), int(m.group(2)))] = c["quote"]
        code_cites = sorted({(m.group(1), int(m.group(2))) for c in d.get("citations", ()) if (m := P.CITE_RX.fullmatch(str(c.get("target", ""))))})
        resolve = P.check_citation_lines(tgt["module_dir"], code_cites, quotes)
        audit = audit_claims(checked, routes, Path(tgt["module_dir"]))
        grounded = bool(d.get("citations"))  # an answer with no accepted citation (including one the library downgraded to not_specified) is not cited
        text_blob = json.dumps(checked)
        rows.append(
            {
                "question": question, "status": "ANSWERED", "seconds": round(time.time() - t0, 1), "would_be_routed_model_free_by": routed,
                "kind": d.get("kind"), "implementation": ans.implementation, "implementation_basis": ans.implementation_basis, "family": family,
                "registered_family": registered, "truth_family": truth_kind,
                "answer": d.get("answer"), "explanation": d.get("explanation"), "citations": d.get("citations"), "rejected_citations": d.get("rejected_citations"),
                "usage": d.get("usage"), "check_counts": checked.get("check"), "answer_after_claim_check": {k: checked.get(k) for k in ("kind", "answer", "explanation")},
                "code_citations_resolve": resolve, "claim_audit": audit, "has_grounded_citation": grounded,
                "names_the_auth": any(w in text_blob for w in ("Member", "Admin", "auth", "login")) if "dismiss" in question else None,
            }
        )
    answered = [r for r in rows if r.get("status") == "ANSWERED"]
    result: dict[str, Any] = {
        "self_test": stub, "head": info["head"], "model": MODEL, "streak_rev": STREAK_REV, "inventory": "static (no observed workflows: Docker not used)",
        "appmap_error": am_err, "sync_route_in_d1_truth": sync_in_truth, "total_usd": ledger_total(out) if not stub else 0.0, "ask_usd": ledger_total(out, "ask") if not stub else 0.0, "rows": rows,
    }
    uncited = sum(r["claim_audit"]["uncited"] for r in answered)
    bad_res = sum(len(r["code_citations_resolve"]["bad"]) for r in answered)
    rejected = sum(len(r["rejected_citations"] or []) for r in answered)
    result["verdicts"] = {
        "A-ANSWERED (3 of 3 questions answered by the model)": len(answered) == 3,
        "A-CITED (every claim cites spec or code: 0 uncited references, 0 rejected citations, every code citation resolves with its quote, every answer has at least one accepted citation)": len(answered) == 3 and uncited == 0 and rejected == 0 and bad_res == 0 and all(r["has_grounded_citation"] for r in answered),
        "A-KINDS-REGISTERED (kinds equal the registered not_implemented / implemented / implemented)": len(answered) == 3 and [r["family"] for r in answered] == [k for _, k, _ in P.ASK_QUESTIONS],
        "A-KINDS-TRUTH (kinds equal D1's truth: sync is implemented, check-in and dismiss are implemented)": len(answered) == 3 and all(r["family"] == r["truth_family"] for r in answered),
        "A-AUTH (the dismiss answer names the auth)": any(r.get("names_the_auth") for r in answered),
        "A-COST (total ledger <= USD 5.00)": result["total_usd"] <= TOTAL_CAP,
    }
    result["counts"] = {"uncited_references": uncited, "rejected_citations": rejected, "unresolved_code_citations": bad_res,
                        "references_checked": sum(len(r["claim_audit"]["references"]) for r in answered)}
    (out / "ask-result.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({"verdicts": result["verdicts"], "counts": result["counts"], "usd": result["total_usd"]}, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("calibrate", "build", "ask"))
    ap.add_argument("--out", required=True, help="the data directory (ledger, raw outputs, results)")
    ap.add_argument("--scratch", default="", help="scratch directory (default: a temporary one under --out/../scratch)")
    ap.add_argument("--self-test", action="store_true", help="scripted stand-ins: no model call, no cap bookkeeping")
    ap.add_argument("--timeout", type=float, default=2700.0)
    a = ap.parse_args()
    if not a.scratch:
        import tempfile

        a.scratch = tempfile.mkdtemp(prefix="dn-real-agent-")
    return {"calibrate": cmd_calibrate, "build": cmd_build, "ask": cmd_ask}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
