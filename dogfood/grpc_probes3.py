"""Probes of gRPC round 3 (docs/results/2026-10-18-grpc-round3-prereg.md, with Amendment 1):
registration reachability (D1), an interceptor that cannot be read is never ``Public`` (D2),
reconcile scored on the library's own output (D3, S5-S7).

Arms 0 (static) and G (Go servers over pure-Go SQLite, no Docker) are the development stage.
``deskd`` and kilnworks have both been scored and read: they are **development data**, never a
blind or held-out claim (kilnworks is PD3-KILN, post hoc and reported only). Arm M and Arm L are
reported ``NOT RUN``. ``--arm H3`` is the gated held-out stage (PG-HO3: a new app by a new agent,
scored ONCE by the orchestrator after the freeze); it refuses to score when a frozen file or the
recorded hashes differ, and writes no metric file when the runner itself errors. The build node
never ran ``--arm H3`` and never opened the held-out directory.

    export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
    uv run python dogfood/grpc_probes3.py --arm 0 --base "$SCRATCH/base" --deskd <deskd> \\
        --kiln <kilnworks parent> --scratch "$SCRATCH/p0" --out docs/results/data/2026-10-18-grpc-round3 \\
        --reg-before ... --reg-after ...
    uv run python dogfood/grpc_probes3.py --arm G --base "$SCRATCH/base" --base-r1 "$SCRATCH/base-r1" \\
        --base6a "$SCRATCH/base6a" --deskd <deskd> --kiln <kilnworks parent> --scratch ... --out ...
    uv run python dogfood/grpc_probes3.py --arm H3 --heldout "$SCRATCH/../heldout3" \\
        --freeze .../freeze3.sha256 --out .../heldout3          # the orchestrator, once

Internal sub-commands: ``emit-det3``, ``nw-work`` (run in a subprocess) and ``--check <name>``
(one probe, exit 0 on PASS: what ``grpc_mutants3.py`` runs).
"""

# ruff: noqa: E501 -- probe texts and table rows are data lines
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO))  # after PYTHONPATH: a baseline tree's own library wins when asked for

import grpc_probes as r1  # noqa: E402
import grpc_probes2 as q2  # noqa: E402
import grpc_scorer3 as sc3  # noqa: E402

sha = r1.sha
FIXTURES = REPO / "tests" / "grpc" / "fixtures"
DATA = REPO / "docs/results/data/2026-10-18-grpc-round3"
PREREG = REPO / "docs/results/2026-10-18-grpc-round3-prereg.md"
MERGE = "0b92f00ac082261d58e1f02dc5fda1a417e15444"  # the merge commit every budget is counted from
KILN_PKG = "kilnworks"

BUDGETS = {
    "lattice/gocheck/src/goextract_grpc.go": 300,
    "lattice/spec/grpc.py": 80,
    "lattice/importer/frontends/go_grpc.py": 15,
}
CREATES = (
    "dogfood/grpc_probes3.py",
    "dogfood/grpc_mutants3.py",
    "dogfood/grpc_scorer3.py",
    "dogfood/grpc_scorer3_cases.json",
    "tests/grpc/test_wiring3.py",
    "tests/grpc/test_auth3.py",
    "tests/grpc/test_reconcile3.py",
    "tests/grpc/fixtures/wiring",
    "tests/grpc/fixtures/authwire",
    "docs/results/2026-10-18-grpc-round3-results.md",
    "docs/results/data/2026-10-18-grpc-round3",
)
FROZEN_HASHES = {  # prereg section 9, "stays frozen"
    "lattice/gocheck/src/goextract_proto.go": "ec4aa3157b407467708bdc341171e94e9a33aedf70ac6a46893d7b41590c9a50",
    "lattice/gocheck/src/goextract.go": "652e7007380ef7fa3e16b77ac937fb300ab5023899c2e81408cfa7a2a28c376a",
    "lattice/gocheck/src/load.go": "72a35ec417e522243f7583f8cb7f605c5f0261a81ce28ee7d057fa5af4cbd17f",
    "lattice/importer/workflows.py": "5eba7e8ab6ac62ba8ba4cea31088faab9e149e921a952c6a4837e37b92638098",
    "dogfood/grpc_flow_scorer.py": "8d896ee0ad1c22fd46ff9f38876c08e8df03b756906f0b40bcaadf0a0446fdd3",
    "dogfood/grpc_probes2.py": "0193add7bd620dbf2af90cddf66a8bccca85ba1ade5b06f048bd711c952755fa",
}
FROZEN_PREFIX = {  # truth files, by the prefix the prereg names
    "tests/grpc/fixtures/notes/truth.json": "debbd0b4",
    "tests/grpc/fixtures/sessions/truth.json": "0e9b0d37",
    "tests/grpc/fixtures/deskd_links.json": "604df939",
}
KILN_FREEZE_SHA = q2.H2_FREEZE_SHA
DESKD_FREEZE_SHA = q2.DESKD_FREEZE_SHA
# round 2, strict (the verifier's correction): what PD3-KILN is compared with
R2_KILN = {
    "RPC": "recall 0.30 / precision 0.30 (6 of 20 rpcs; 14 `not_registered`)",
    "CODES": "recall 0.125 / precision 1.00",
    "AUTH": "2 wrong of 3 (ListStudios, LookupGlaze labelled Public, truth Member)",
    "TABLES": "pass",
    "REC": "4 of 20 rpcs answered by reconcile_with (the other 16 by the runner)",
    "FLOWS": "recovered 0, verified 0, edge recall 0/33, coverage 0/9",
}

PRED = {
    "HO3-RPC": "recall 0.70-1.00 / precision 0.75-1.00 (point 0.90 / 0.95)",
    "HO3-CODES": "recall 0.45-0.90 / precision 0.85-1.00",
    "HO3-AUTH": "wrong 0 (p 0.8); unclear 0.00-0.60",
    "HO3-TABLES": "recall 0.85-1.00",
    "HO3-REC": "coverage 0.95-1.00; agreement 0.85-1.00; 0-3 disagreements",
    "HO3-FLOWS": "recovered 5-60; verified 0-12 (point 4); edge recall 0.00-0.55 (point 0.25); coverage 0-5 (point 2); 0 vbf",
    "HO3-DET": "10/10 identical",
    "HO3-NW": "0, 0, 0",
}
RULE = {
    "HO3-RPC": "recall >= 0.90 and precision >= 0.95 (rpc name AND state must match; `unread` counts wrong)",
    "HO3-CODES": "micro recall >= 0.70 and micro precision >= 0.90 (implemented rpcs, OK excluded)",
    "HO3-AUTH": "S7: 0 wrong (hard) and unclear <= 0.50 of the truth's implemented + unimplemented rpcs",
    "HO3-TABLES": "recall >= 0.90",
    "HO3-REC": "S5 (coverage >= 0.95, agreement 1.00, supplementary calls 0) + S6 not FAIL + 0 auth_refused on truth-implemented non-public rpcs + every disagreement resolved isolated",
    "HO3-FLOWS": "S2: verified >= 1, 0 verified-but-failing, edge recall >= 0.30, coverage >= 2 truth flows",
    "HO3-DET": "10/10 identical, no absolute path, time or port",
    "HO3-NW": "0 changed, 0 new, 0 claude calls",
}


class Row(r1.Probe):
    """A probe whose verdict is not gated: ``REPORTED`` with the verdict it would have had."""

    def reported(self, ok: bool, observed: Any, detail: Any = None) -> Row:
        self.status, self.observed, self.detail = "REPORTED", {**observed, "would_pass": ok}, detail
        return self


Probe = r1.Probe


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    ).stdout


# ---- the frozen files, the budgets -------------------------------------------------------------


def library_hashes() -> dict[str, str]:
    files = [
        *BUDGETS,
        *FROZEN_HASHES,
        "dogfood/grpc_scorer3.py",
        "dogfood/grpc_scorer3_cases.json",
        "dogfood/grpc_probes3.py",
    ]
    return {f: sha((REPO / f).read_bytes()) for f in sorted(files) if (REPO / f).is_file()}


def frozen_check(deskd: Path | None, kiln: Path | None) -> dict[str, Any]:
    """Section 9: the frozen hashes, the line budgets against the merge commit, every changed or
    new path inside 'may change' or 'creates', the corpora's own freezes."""
    out: dict[str, Any] = {}
    bad = [f for f, h in FROZEN_HASHES.items() if sha((REPO / f).read_bytes()) != h]
    for f, prefix in FROZEN_PREFIX.items():
        if not sha((REPO / f).read_bytes()).startswith(prefix):
            bad.append(f)
    out["frozen_files_changed"] = bad
    status = git("diff", "--name-status", MERGE).splitlines()
    changed = sorted(ln.split("\t", 1)[1] for ln in status if ln.strip())
    untracked = git("ls-files", "--others", "--exclude-standard").split()
    allowed_new = tuple(CREATES)

    def new_ok(path: str) -> bool:
        return any(path == a or path.startswith(a + "/") for a in allowed_new)

    out["tracked_changed_vs_merge"] = changed
    out["unexpected"] = sorted(
        [c for c in changed if c not in BUDGETS and not new_ok(c)]
        + [u for u in untracked if not new_ok(u)]
    )
    budgets: dict[str, Any] = {}
    for f, cap in BUDGETS.items():
        ns = git("diff", "--numstat", MERGE, "--", f).split()
        n = int(ns[0]) + int(ns[1]) if len(ns) >= 2 and ns[0].isdigit() else 0
        budgets[f] = {"insertions+deletions": n, "budget": cap, "ok": n <= cap}
    out["budgets"] = budgets
    out["staged"] = sorted(git("diff", "--name-only", "--cached").split())
    if deskd is not None:
        parent = deskd.resolve().parent
        fz = parent / "freeze.sha256"
        out["deskd_freeze_sha256_ok"] = sha(fz.read_bytes()) == DESKD_FREEZE_SHA
        out["deskd_files_changed"] = r1._check_sha_file(fz, parent)
    if kiln is not None:
        base = kiln / KILN_PKG
        fz = base / "freeze.sha256"
        out["kilnworks_freeze_sha256_ok"] = sha(fz.read_bytes()) == KILN_FREEZE_SHA
        out["kilnworks_files_changed"] = r1._check_sha_file(fz, base)
    dev = DATA / "freeze-dev3.sha256"
    out["dev_truth_hashes_changed"] = (
        r1._check_sha_file(dev, REPO) if dev.is_file() else ["missing"]
    )
    out["ok"] = (
        not bad
        and not out["unexpected"]
        and not out["staged"]
        and all(b["ok"] for b in budgets.values())
        and out.get("deskd_freeze_sha256_ok", True)
        and not out.get("deskd_files_changed")
        and out.get("kilnworks_freeze_sha256_ok", True)
        and not out.get("kilnworks_files_changed")
        and not out["dev_truth_hashes_changed"]
    )
    return out


# ---- Arm 0 -------------------------------------------------------------------------------------


def _defs(src: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.parse(src).body:
        out[getattr(node, "name", None) or ast.dump(node)] = ast.dump(node)
    return out


def p3_bc_ast(base: Path) -> Probe:
    """Every top-level definition of ``lattice/spec/grpc.py`` but ``reconcile_with`` is identical;
    ``workflows.py`` and ``go_grpc.py`` are byte-identical (the flow rules and the front end)."""
    p = Probe("P3-BC-AST", "identical outside reconcile_with", "identical")
    old = (base / "lattice/spec/grpc.py").read_text()
    new = (REPO / "lattice/spec/grpc.py").read_text()
    a, b = _defs(old), _defs(new)
    bad = [
        f"grpc.py: {k[:60]}"
        for k in sorted(set(a) | set(b))
        if k != "reconcile_with" and a.get(k) != b.get(k)
    ]
    for f in ("lattice/importer/workflows.py", "lattice/importer/frontends/go_grpc.py"):
        if (base / f).read_bytes() != (REPO / f).read_bytes():
            bad.append(f"{f}: bytes differ")
    return p.done(
        not bad,
        {
            "grpc.py defs compared": len(a),
            "reconcile_with differs": a.get("reconcile_with") != b.get("reconcile_with"),
            "differences": len(bad),
        },
        bad,
    )


EXTRACT_SNIPPET = (
    "import json,sys,hashlib\nfrom pathlib import Path\n"
    "from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd\n"
    "fe=GoGrpcFrontEnd(); out={}\n"
    "for root,pkg in json.loads(sys.argv[1]):\n"
    "    ex=fe.extract_json(Path(root),pkg); out[pkg+':extract']=hashlib.sha256(json.dumps(ex,sort_keys=True).encode()).hexdigest()\n"
    "    out[pkg+':model']=hashlib.sha256(fe.extract(Path(root),pkg).to_json().encode()).hexdigest()\n"
    "print(json.dumps(out))\n"
)


def _tree_run(
    tree: Path, cmd: list[str], scratch: Path, cache: str, *, timeout: int = 1800
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(tree),
        "LATTICE_GOCHECK_CACHE": str(scratch / cache),
        "LATTICE_GRPCBRIDGE_CACHE": str(scratch / "bridgecache"),
        "LATTICE_HELDOUT_BUILD": str(scratch / "hbuild"),
    }
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=tree, check=False, timeout=timeout
    )


def _extract_hashes(
    tree: Path, targets: list[tuple[str, str]], scratch: Path, cache: str
) -> dict[str, str]:
    proc = _tree_run(
        tree, [sys.executable, "-c", EXTRACT_SNIPPET, json.dumps(targets)], scratch, cache
    )
    return (
        json.loads(proc.stdout.splitlines()[-1])
        if proc.returncode == 0
        else {"error": proc.stderr[-300:]}
    )


def p3_bc_ext(ctx: dict[str, Any]) -> Probe:
    """Extract and RepoModel of N, T, sessions and deskd byte-identical to the merge commit;
    kilnworks is the declared difference."""
    p = Probe(
        "P3-BC-EXT", "identical on N, T, sessions, deskd; kilnworks differs (declared)", "identical"
    )
    base, scratch = ctx.get("base"), Path(ctx["scratch"])
    if base is None:
        return p.not_run("no --base")
    targets = [(str(FIXTURES), "notes"), (str(FIXTURES), "protos"), (str(FIXTURES), "sessions")]
    if ctx.get("deskd"):
        targets.append((str(Path(ctx["deskd"]).resolve().parent), "deskd"))
    kiln = [(str(Path(ctx["kiln"])), KILN_PKG)] if ctx.get("kiln") else []
    a = _extract_hashes(Path(base), targets + kiln, scratch, "gocheck-base")
    b = _extract_hashes(REPO, targets + kiln, scratch, "gocheck-new")
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    expected = sorted(f"{KILN_PKG}:{x}" for x in ("extract", "model")) if kiln else []
    return p.done(
        [d for d in diff if not d.startswith(KILN_PKG + ":")] == []
        and (not kiln or diff == expected),
        {"compared": len(a), "differ": diff, "declared (kilnworks extract + model)": expected},
        diff,
    )


def _emit(tree: Path, args: list[str], scratch: Path, cache: str) -> Any:
    proc = _tree_run(
        tree, [sys.executable, str(tree / "dogfood" / "grpc_probes2.py"), *args], scratch, cache
    )
    if proc.returncode != 0:
        return {"error": proc.stderr[-400:]}
    return json.loads(proc.stdout.splitlines()[-1])


def p3_bc(ctx: dict[str, Any]) -> Probe:
    p = Probe("P3-BC", "all identical (p)", "all")
    base, scratch = ctx.get("base"), Path(ctx["scratch"])
    if base is None:
        return p.not_run("no --base: git archive of the merge commit was not given")
    base = Path(base)
    ast_probe = p3_bc_ast(base)
    http = r1.bc_probe(
        {"base": base, "scratch": scratch}
    )  # the 13 HTTP fixtures, select(), reports, CLI, MCP
    ext = p3_bc_ext(ctx)
    rec = {}
    for kind in ("n", "sessions"):
        a = _emit(base, ["emit-rec", kind, str(scratch)], scratch, "gocheck-base")
        b = _emit(REPO, ["emit-rec", kind, str(scratch)], scratch, "gocheck-new")
        rec[kind] = a == b and "error" not in a
    flows: dict[str, Any] = {}
    kinds = [("n", FIXTURES, FIXTURES)]
    if ctx.get("deskd"):
        d = Path(ctx["deskd"]).resolve().parent
        kinds.append(("desk", d, d))
    for kind, rb, rn in kinds:
        a = _emit(base, ["emit-det", kind, str(rb), str(scratch)], scratch, "gocheck-base")
        b = _emit(REPO, ["emit-det", kind, str(rn), str(scratch)], scratch, "gocheck-new")
        if "error" in a or "error" in b:
            flows[kind] = {"error": str(a.get("error", b.get("error")))[:200]}
            continue
        same = {k: a[k] == b[k] for k in sorted(a)}
        flows[kind] = same
    n_ok = all(
        flows.get("n", {}).get(k) is True for k in ("extract", "plans", "flows", "reconcile")
    )
    d_ok = ("desk" not in flows) or all(
        flows["desk"].get(k) is True for k in ("extract", "plans", "flows")
    )
    wf = r1.run_pytest(["tests/importer/test_workflows.py"])
    ok = (
        ast_probe.status == "PASS"
        and http.status == "PASS"
        and ext.status == "PASS"
        and all(rec.values())
        and n_ok
        and d_ok
        and wf["returncode"] == 0
    )
    return p.done(
        ok,
        {
            "ast (grpc.py defs but reconcile_with; workflows.py, go_grpc.py bytes)": ast_probe.status,
            "HTTP outputs (round-1/2 emitter) vs the merge commit": http.status,
            "HTTP outputs compared": (http.observed or {}).get("compared"),
            "extract + RepoModel": ext.status,
            "reconcile bytes (library `reconcile`) on N and sessions": rec,
            "flows/plans/extract/reconcile on N and flows/plans/extract on deskd, identical": flows,
            "declared difference: reconcile_with on deskd (Reports/Backlog)": "see PR3-DESK",
            "HTTP workflow tests": wf["counts"],
        },
        {"ast": ast_probe.detail, "http": http.detail, "ext": ext.detail},
    )


def _truth_of(name: str) -> Any:
    return json.loads((FIXTURES / name / "truth.json").read_text())


def p3_wire() -> Probe:
    p = Probe(
        "P3-WIRE",
        "positives implemented; dead and test-only not_registered; opaque unread with registration_reachability_unknown",
        "exact on every service and rpc state",
    )
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    truth = _truth_of("wiring")
    ex = GoGrpcFrontEnd().extract_json(FIXTURES, "wiring")
    got = {f"{s['name']}/{r['name']}": r for s in ex["grpc"]["services"] for r in s["rpcs"]}
    want = {k: v["state"] for k, v in truth["rpcs"].items()}
    states = {k: got[k]["state"] if k in got else "(missing)" for k in want}
    wrong = {k: (states[k], want[k]) for k in want if states[k] != want[k]}
    reasons = {
        k: sorted({u["what"] for u in got[k].get("unread", [])})
        for k in want
        if want[k] == "unread"
    }
    reasons_ok = all(v == ["registration_reachability_unknown"] for v in reasons.values()) and all(
        k.rsplit("/", 1)[0] in truth["unread_reason"] for k in reasons
    )
    excluded = sorted(x["path"] for x in ex["excluded"] if x["what"] == "rpc_registration")
    dev_hash_ok = not r1._check_sha_file(DATA / "freeze-dev3.sha256", REPO)
    gateway_empty = ex["grpc"]["gateway"] == []
    return p.done(
        not wrong
        and set(got) == set(want)
        and reasons_ok
        and excluded == sorted(truth["excluded_registrations"])
        and dev_hash_ok
        and gateway_empty,
        {
            "rpcs": len(want),
            "states equal the truth": len(want) - len(wrong),
            "wrong (extract, truth)": wrong,
            "unread reasons": reasons,
            "excluded registrations": excluded,
            "truth hashed before extractor code": dev_hash_ok,
            "gateway reachability not widened (D1.0; checked outside the hashed truth)": gateway_empty,
        },
        {k: v["state"] for k, v in got.items()},
    )


def p3_authu() -> Probe:
    p = Probe(
        "P3-AUTHU",
        "Public/Member only where a literal allowlist or no-interceptor case decides; untraced gate unclear",
        "exact; 0 Public on any rpc behind an auth_unknown interceptor",
    )
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    truth = _truth_of("authwire")["cases"]
    fe = GoGrpcFrontEnd()
    rows: dict[str, Any] = {}
    bad: list[str] = []
    public_behind_unknown: list[str] = []
    for case, want in truth.items():
        ex = fe.extract_json(FIXTURES / "authwire", case)
        auth = {r["name"]: r["auth"] for s in ex["grpc"]["services"] for r in s["rpcs"]}
        kinds = [i["kind"] for i in ex["grpc"]["interceptors"]]
        rows[case] = {"auth": auth, "kinds": kinds}
        if auth != want["auth"]:
            bad.append(f"{case}: auth {auth} != {want['auth']}")
        if kinds != want["kinds"]:
            bad.append(f"{case}: kinds {kinds} != {want['kinds']}")
        if "auth_unknown" in kinds:
            public_behind_unknown += [f"{case}/{k}" for k, v in auth.items() if v == "Public"]
    dev_hash_ok = not r1._check_sha_file(DATA / "freeze-dev3.sha256", REPO)
    return p.done(
        not bad and not public_behind_unknown and dev_hash_ok,
        {
            "cases": len(truth),
            "cases exact": len(truth) - len({b.split(":")[0] for b in bad}),
            "Public behind an auth_unknown interceptor": public_behind_unknown,
            "truth hashed before extractor code": dev_hash_ok,
        },
        {"disagreements": bad, "rows": rows},
    )


def _stray3(needles: list[str]) -> list[str]:
    out = []
    me = str(os.getpid())
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid == me:
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if (
            any(n in cmd for n in needles)
            and "grpc_probes" not in cmd
            and "grpc_mutants" not in cmd
        ):
            out.append(cmd[:120])
    return out


RX_ABS = re.compile(r"/(home|tmp|Users|root)/[\w./-]+")
RX_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
RX_PORT = re.compile(r"127\.0\.0\.1:\d+|localhost:\d+")


def _det_runs(
    label: str, argv_for: Any, roots: list[Path], scratch: Path, n: int = 10
) -> dict[str, Any]:
    """``n`` processes with random hash seeds over two roots; every artifact hash must be identical."""
    results: list[Any] = []
    leaks: list[str] = []
    for i in range(n):
        root = roots[i % 2]
        proc = subprocess.run(
            argv_for(root),
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO,
            env={
                **os.environ,
                "PYTHONHASHSEED": str(random.randrange(1, 2**31)),
                "PYTHONPATH": str(REPO),
            },
        )
        if proc.returncode != 0:
            results.append(("error", proc.stderr[-400:]))
            continue
        results.append(json.loads(proc.stdout.splitlines()[-1]))
        for key, text in json.loads(proc.stderr.splitlines()[-1]).items():
            scrubbed = text.replace(str(root), "")
            for rx, what in ((RX_ABS, "absolute path"), (RX_TIME, "time"), (RX_PORT, "port")):
                m = rx.search(scrubbed)
                if m and key != "extract" and not m.group(0).startswith("/notes"):
                    leaks.append(f"{label}:{key}: {what} {m.group(0)!r}")
    good = [r for r in results if isinstance(r, dict)]
    same = len({json.dumps(r, sort_keys=True) for r in good}) == 1
    return {
        "identical runs": len(good) if same else 0,
        "of": n,
        "leaks": sorted(set(leaks))[:3],
        "artifacts": sorted(good[0]) if good else [],
        "errors": [r for r in results if not isinstance(r, dict)],
        "ok": len(good) == n and same and not leaks,
    }


def _copy_root(src: Path, scratch: Path, label: str, sub: str = "") -> Path:
    shutil.rmtree(scratch / f"det3-{label}", ignore_errors=True)
    dst = scratch / f"det3-{label}" / "x" / "y"
    shutil.copytree(
        src, dst / sub if sub else dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    return dst


def p3_det(ctx: dict[str, Any]) -> Probe:
    """extract, model, reconcile and flows on N, deskd and kilnworks (dev); extract, model and
    plans on wiring and authwire (not runnable): 10 processes, random hash seeds, two roots."""
    p = Probe("P3-DET", "identical (p)", "10/10")
    scratch = Path(ctx["scratch"])
    me = str(Path(__file__).resolve())
    q2file = str(HERE / "grpc_probes2.py")
    rows: dict[str, Any] = {}
    plan: list[tuple[str, Any, list[Path]]] = [
        (
            "notes",
            lambda r: [sys.executable, q2file, "emit-det", "n", str(r), str(scratch)],
            [FIXTURES, _copy_root(FIXTURES, scratch, "n")],
        ),
        (
            "wiring",
            lambda r: [sys.executable, me, "emit-det3", "wiring", str(r), str(scratch)],
            [FIXTURES, _copy_root(FIXTURES / "wiring", scratch, "wiring", "wiring")],
        ),
        (
            "authwire",
            lambda r: [sys.executable, me, "emit-det3", "authwire", str(r), str(scratch)],
            [FIXTURES / "authwire", _copy_root(FIXTURES / "authwire", scratch, "authwire")],
        ),
    ]
    if ctx.get("deskd"):
        d = Path(ctx["deskd"]).resolve().parent
        plan.append(
            (
                "deskd",
                lambda r: [sys.executable, q2file, "emit-det", "desk", str(r), str(scratch)],
                [d, _copy_root(d, scratch, "desk")],
            )
        )
    if ctx.get("kiln"):
        k = Path(ctx["kiln"])
        plan.append(
            (
                "kilnworks",
                lambda r: [sys.executable, q2file, "emit-det", "kiln", str(r), str(scratch)],
                [k, _copy_root(k, scratch, "kiln")],
            )
        )
    ok = True
    for label, argv_for, roots in plan:
        res = _det_runs(label, argv_for, roots, scratch)
        rows[label] = {k: v for k, v in res.items() if k != "errors"}
        ok = ok and res["ok"]
    rows["roots"] = 2
    if not ctx.get("deskd"):
        rows["deskd"] = "NOT RUN: no --deskd"
    if not ctx.get("kiln"):
        rows["kilnworks"] = "NOT RUN: no --kiln"
    return p.done(ok, rows)


def cmd_emit_det3(kind: str, root: str, scratch: str) -> None:
    """Static artifacts of ``wiring`` (``root`` holds ``wiring/``) or every ``authwire`` case
    (``root`` is ``authwire/``), hashed; the texts go to stderr for the leak scan."""
    from lattice.importer import workflows as wf
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    fe = GoGrpcFrontEnd()
    rootp = Path(root)
    jobs = (
        [(rootp, "wiring")]
        if kind == "wiring"
        else [(rootp, c.name) for c in sorted(rootp.iterdir()) if c.is_dir()]
    )
    texts: dict[str, str] = {"extract": "", "model": "", "plans": ""}
    for parent, pkg in jobs:
        ex = fe.extract_json(parent, pkg)
        texts["extract"] += json.dumps(ex, sort_keys=True)
        texts["model"] += fe.extract(parent, pkg).to_json()
        texts["plans"] += json.dumps(q2._plan_view(wf.static_rpc_plans(wf.rpc_views(ex))))
    print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in texts.items()}))
    print(json.dumps(texts), file=sys.stderr)


def p3_nw(ctx: dict[str, Any]) -> Probe:
    p = Probe("P3-NW", "0 changed, 0 new, 0 calls", "all 0")
    scratch = Path(ctx["scratch"])
    shim = scratch / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    log = scratch / "claude-calls.log"
    log.write_text("")
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n')
    (shim / "claude").chmod(0o755)
    roots = {
        "N": FIXTURES / "notes",
        "T": FIXTURES / "protos",
        "sessions": FIXTURES / "sessions",
        "wiring": FIXTURES / "wiring",
        "authwire": FIXTURES / "authwire",
    }
    kinds = ["N", "sessions", "wiring", "authwire"]
    if ctx.get("deskd"):
        roots["deskd"] = Path(ctx["deskd"]).resolve()
        kinds.append("deskd")
    if ctx.get("kiln"):
        roots["kilnworks"] = Path(ctx["kiln"]) / KILN_PKG
        kinds.append("kilnworks")
    before = {k: q2._snapshot(r) for k, r in roots.items()}
    env = {
        **os.environ,
        "PATH": f"{shim}:{os.environ['PATH']}",
        "PYTHONPATH": str(REPO),
        "LATTICE_HELDOUT_BUILD": str(scratch / "hbuild"),
    }
    for kind in kinds:
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "nw-work",
                kind,
                str(ctx.get("deskd") or ""),
                str(ctx.get("kiln") or ""),
                str(scratch),
            ],
            check=True,
            env=env,
            cwd=REPO,
            capture_output=True,
        )
    after = {k: q2._snapshot(r) for k, r in roots.items()}
    changed = [f"{k}:{x}" for k in roots for x in before[k] if before[k].get(x) != after[k].get(x)]
    new = [f"{k}:{x}" for k in roots for x in after[k] if x not in before[k]]
    calls = len([ln for ln in log.read_text().splitlines() if ln.strip()])
    return p.done(
        not changed and not new and calls == 0,
        {
            "roots": [*roots, "heldout3: NOT RUN (the gated stage; this node never looked in it)"],
            "reconcile x3, flows x3 (N, sessions, deskd, kilnworks); extract (wiring, authwire)": True,
            "changed": len(changed),
            "new": len(new),
            "claude calls": calls,
        },
        changed + new,
    )


def cmd_nw_work(kind: str, deskd: str, kiln: str, scratch: str) -> None:
    sp = Path(scratch)
    if kind in ("wiring", "authwire"):
        from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

        fe = GoGrpcFrontEnd()
        if kind == "wiring":
            fe.extract_json(FIXTURES, "wiring")
        else:
            for c in sorted((FIXTURES / "authwire").iterdir()):
                if c.is_dir():
                    fe.extract_json(FIXTURES / "authwire", c.name)
        return
    app = {
        "N": lambda: q2.load_n(),
        "sessions": lambda: q2.load_sessions(),
        "deskd": lambda: q2.load_desk(Path(deskd), sp),
        "kilnworks": lambda: load_app(Path(kiln) / KILN_PKG, sp, truth=False),
    }[kind]()
    for _ in range(3):
        q2.reconcile_of(app)
    if kind != "sessions":
        for _ in range(3):
            q2.flows_of(app)


def p3_unit() -> Probe:
    p = Probe("P3-UNIT", ">= 25 tests green (p: 25-45)", "all green")
    r = r1.run_pytest(
        ["tests/grpc/test_wiring3.py", "tests/grpc/test_auth3.py", "tests/grpc/test_reconcile3.py"]
    )
    n = r["counts"].get("passed", 0)
    return p.done(
        r["returncode"] == 0 and n >= 25 and not r["failing"],
        {**r["counts"], "seconds": r["seconds"], "in 25-45 (p)": 25 <= n <= 45},
        r["failing"],
    )


def p3_score() -> Probe:
    p = Probe("P3-SCORE", "20-30 cases (p)", "100%, >= 20")
    path = HERE / "grpc_scorer3_cases.json"
    n = len(json.loads(path.read_text())["cases"])
    bad = sc3.run_cases(path)
    return p.done(
        not bad and n >= 20, {"cases": n, "agree": n - len(bad), "in 20-30 (p)": 20 <= n <= 30}, bad
    )


def arm0(ctx: dict[str, Any]) -> list[Probe]:
    return [
        p3_bc(ctx),
        p3_wire(),
        p3_authu(),
        p3_det(ctx),
        p3_nw(ctx),
        p3_unit(),
        p3_score(),
        q2.p2_reg(ctx),
    ]


# ---- apps and truth ------------------------------------------------------------------------------


def h3_truth(path: Path, scratch: Path) -> dict[str, Any]:
    """``truth.json`` (``lattice.heldout.truth/2`` or ``/3``) through round 2's reader, plus the
    per-rpc ``effects`` of /3. ``conventions.step_index_base`` 0 shifts the links to 1-based first
    (the reader decides by a zero it finds, which a base-0 file need not contain)."""
    raw = json.loads(path.read_text())
    base = (raw.get("conventions") or {}).get("step_index_base")
    effects: dict[str, str] = {}
    rpcs_raw = r1._first(raw, "rpcs", "methods", default={})
    items = rpcs_raw.items() if isinstance(rpcs_raw, Mapping) else enumerate(rpcs_raw)
    for k, v in items:
        if isinstance(rpcs_raw, Mapping):
            name = r1._full(k)
        else:
            has_full = "full_name" in v or "rpc" in v
            name = r1._full(
                r1._first(v, "full_name", "rpc", "name", "id", default=""),
                None if has_full else r1._first(v, "service"),
            )
        effects[name] = (
            str(v.get("effects", "none") or "none") if isinstance(v, Mapping) else "none"
        )
    use = path
    if base == 0:
        keys = ("step", "to_step", "consumer_step", "from_step", "from", "producer_step")

        def shift(ln: dict[str, Any]) -> None:
            for key in keys:
                if isinstance(ln.get(key), int):
                    ln[key] += 1

        for f in raw.get("flows", []):
            for ln in f.get("links") or []:
                shift(ln)
            for st in f.get("steps") or []:
                for ln in st.get("links") or []:
                    shift(ln)
        scratch.mkdir(parents=True, exist_ok=True)
        use = scratch / "truth-shifted.json"
        use.write_text(json.dumps(raw))
    t = q2.h2_truth(use)
    t["effects"] = effects
    t["schema"] = raw.get("schema")
    t["step_index_base"] = base
    return t


def load_app(base: Path, scratch: Path, *, truth: bool = True) -> q2.App:
    """An app of the held-out kind (``harness.py`` with ``make_app``, ``build_db``, ``RUNTIME``,
    ``USERS``, ``PROTOS``) through its own harness; the library, not the runner, reconciles it."""
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from lattice.profile import ApiProfile, Profile
    from lattice.spec import Spec

    pkg = base.name
    os.environ.setdefault(f"{pkg.upper()}_BIN_DIR", str(scratch / f"{pkg}-bin"))
    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    spec_ = importlib.util.spec_from_file_location(f"heldout3_{pkg}_harness", base / "harness.py")
    assert spec_ and spec_.loader
    h = importlib.util.module_from_spec(spec_)
    sys.modules[f"heldout3_{pkg}_harness"] = h
    # Reader fix (post-freeze, disclosed in the round-3 results): a held-out harness may pass the
    # token field positionally, ``AuthDriver.rpc(svc, method, users, token_field)``; the library
    # signature makes it keyword-only. Only this call shape is tolerated; the library is untouched.
    from lattice.importer.transport import AuthDriver

    _orig_rpc = AuthDriver.rpc.__func__

    def _rpc_tolerant(cls, service, method, credentials, *rest, **kw):  # noqa: ANN001, ANN202
        if len(rest) == 1 and "token_field" not in kw:
            kw["token_field"] = rest[0]
            rest = ()
        return _orig_rpc(cls, service, method, credentials, *rest, **kw)

    AuthDriver.rpc = classmethod(_rpc_tolerant)  # type: ignore[method-assign]
    try:
        spec_.loader.exec_module(h)
    finally:
        AuthDriver.rpc = classmethod(_orig_rpc)  # type: ignore[method-assign]
    parent = base.resolve().parent
    fe = GoGrpcFrontEnd()
    protos = tuple(str(base / p) for p in h.PROTOS)
    profile = Profile(
        schemas={},
        api=ApiProfile(
            tables={}, table_names={}, roles=frozenset({"Public", "Member"}), error_body="fastapi"
        ),
    )
    return q2.App(
        name=pkg,
        root=base,
        fixtures=parent,
        pkg=pkg,
        fe=fe,
        extract=fe.extract_json(parent, pkg),
        model=fe.extract(parent, pkg),
        make=h.make_app,
        raw_make=h.make_app,
        build_db=h.build_db,
        runtime=h.RUNTIME,
        users=h.USERS,
        protos=(),
        proto_paths=(),
        spec=Spec(profile=profile, users=h.USERS, protos=protos),
        harness=h,
        truth=h3_truth(base / "truth.json", scratch) if truth else None,
    )


def cmd_emit_det_app(root: str, scratch: str, pkg: str) -> None:
    """Every artifact of one app (``root`` holds ``<pkg>/``), hashed; the texts go to stderr."""
    from lattice.importer import workflows as wf

    app = load_app(Path(root) / pkg, Path(scratch), truth=False)
    texts = {
        "extract": json.dumps(app.extract, sort_keys=True),
        "plans": json.dumps(q2._plan_view(wf.static_rpc_plans(wf.rpc_views(app.extract)))),
        "flows": json.dumps(q2.flows_of(app), sort_keys=True),
        "reconcile": json.dumps(q2.reconcile_of(app)),
        "model": app.model.to_json(),
        "describe": q2._describe_text(app),
    }
    print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in texts.items()}))
    print(json.dumps(texts), file=sys.stderr)


# ---- the rows both the dev kilnworks and the held-out are scored by ---------------------------------


def rpc_rows(app: q2.App, label: str) -> list[Probe]:
    """RPC, CODES, TABLES by round 2's rules (state included, ``unread`` counts wrong) and AUTH by S7."""
    probes = q2._h2_rpc_metrics(app)
    out: list[Probe] = []
    for pr in probes:
        key = pr.id.removeprefix("HO2-")
        if key == "AUTH":
            continue
        pr.id = f"{label}-{key}"
        pr.prediction, pr.rule = PRED.get(f"HO3-{key}", ""), RULE.get(f"HO3-{key}", "")
        out.append(pr)
    extract_auth = {
        f"{s['name']}/{r['name']}": r.get("auth")
        for s in app.extract["grpc"]["services"]
        for r in s["rpcs"]
    }
    truth_auth = {k: {"state": v["state"], "auth": v["auth"]} for k, v in app.truth["rpcs"].items()}
    row = sc3.score_auth(truth_auth, extract_auth)
    out.insert(
        2,
        Probe(f"{label}-AUTH", PRED["HO3-AUTH"], RULE["HO3-AUTH"]).done(
            row["pass"],
            {k: row[k] for k in ("denominator", "unclear", "unclear_share", "reason")}
            | {"wrong": row["wrong"]},
            {"unclear rpcs": row["unclear_rpcs"], "row": row},
        ),
    )
    return out


def rec_row(app: q2.App, label: str, ctx: dict[str, Any]) -> Probe:
    """S5 + S6 on the library's own ``reconcile_with`` output: the runner makes no call of its own."""
    p = Probe(f"{label}-REC", PRED["HO3-REC"], RULE["HO3-REC"])
    t0 = time.perf_counter()
    got = q2.reconcile_of(app)
    secs = time.perf_counter() - t0
    ctx["rec"] = got
    truth = app.truth["rpcs"]
    s5 = sc3.score_rec({k: v["state"] for k, v in truth.items()}, got, supplementary_calls=0)
    s6 = sc3.score_order(app.truth.get("effects", {}), got)
    refused = set(got.get("auth_refused", []))
    refused_bad = sorted(
        k
        for k in refused
        if truth.get(k, {}).get("state") == "implemented" and truth[k]["auth"] != "Public"
    )
    dis = got.get("order", {}).get("disagreed", [])
    resolved = all(got[k].get("isolated") is True and got[k].get("code") for k in dis)
    extra = sorted(k for k in got if k not in truth and k not in sc3.NOT_RPC_KEYS)
    return p.done(
        s5["pass"] and s6["pass"] and not refused_bad and resolved,
        {
            "S5 coverage": s5["coverage"],
            "S5 agreement": s5["agreement"],
            "S5 by class": s5["by_class"],
            "S5 reason": s5["reason"],
            "supplementary_calls": 0,
            "S6": s6["status"],
            "auth_refused on truth-implemented non-public rpcs": refused_bad,
            "order disagreed": dis,
            "all disagreements resolved isolated": resolved,
            "rpcs answered by the library": len(
                [k for k in got if k not in ("order", "auth_refused")]
            ),
            "seconds": round(secs, 2),
        },
        {"S5": s5, "S6": s6, "library output rpcs not in truth": extra, "codes": got},
    )


def flows_row(app: q2.App, label: str, ctx: dict[str, Any]) -> Probe:
    pr = q2._h2_flows(app, ctx)  # round 2's scorer, unchanged (D4 + S2)
    pr.id, pr.prediction, pr.rule = f"{label}-FLOWS", PRED["HO3-FLOWS"], RULE["HO3-FLOWS"]
    return pr


def det_row(root: Path, scratch: Path, label: str, pkg: str) -> Probe:
    p = Probe(f"{label}-DET", PRED["HO3-DET"], RULE["HO3-DET"])
    me = str(Path(__file__).resolve())
    parent = root.resolve().parent
    copy = _copy_root(parent, scratch, f"app-{pkg}")
    res = _det_runs(
        pkg,
        lambda r: [sys.executable, me, "emit-det-app", str(r), str(scratch), pkg],
        [parent, copy],
        scratch,
    )
    return p.done(res["ok"], {k: v for k, v in res.items() if k != "errors"}, res["errors"])


# ---- Arm G ----------------------------------------------------------------------------------------


def pr3_n(ctx: dict[str, Any]) -> Probe:
    pr = q2.pr2_n(ctx)  # byte-equal to the merge commit's `reconcile`, 10/10 answered
    pr.id, pr.prediction, pr.rule = "PR3-N", "byte-equal to round 2", "exact"
    return pr


def pr3_desk(ctx: dict[str, Any], desk: q2.App) -> Probe:
    p = Probe(
        "PR3-DESK (dev)",
        "19/19 non-streaming truth rpcs + absent covered; Reports/Backlog UNIMPLEMENTED from the library; 0 auth_refused",
        "S5 coverage 1.00, agreement 1.00",
    )
    t0 = time.perf_counter()
    got = q2.reconcile_of(desk)
    ctx["t_rec_desk"], ctx["rec_desk"] = time.perf_counter() - t0, got
    states = {k: v["state"] for k, v in desk.truth["rpcs"].items()}
    s5 = sc3.score_rec(states, got, supplementary_calls=0)
    backlog = got.get("desk.v1.Reports/Backlog")
    return p.done(
        s5["pass"]
        and s5["coverage"] == 1.0
        and "auth_refused" not in got
        and backlog is not None
        and backlog.get("code") == "UNIMPLEMENTED"
        and backlog.get("route") is False,
        {
            "S5 coverage": s5["coverage"],
            "S5 agreement": s5["agreement"],
            "S5 by class": s5["by_class"],
            "denominator": s5["denominator"],
            "Reports/Backlog from the library": backlog,
            "auth_refused": got.get("auth_refused", []),
            "order disagreed": got.get("order", {}).get("disagreed", []),
            "seconds": round(ctx["t_rec_desk"], 2),
        },
        {"S5": s5, "output": got},
    )


def pr3_sess(ctx: dict[str, Any]) -> Probe:
    p = Probe(
        "PR3-SESS",
        "as round 2: Stub UNIMPLEMENTED, 0 login_failed, 4 disagreements isolated",
        "exact; bytes equal to the merge commit's `reconcile`",
    )
    base = ctx.get("base")
    truth = _truth_of("sessions")
    ses = q2.load_sessions()
    got = q2.reconcile_of(ses)
    dis = got.get("order", {}).get("disagreed", [])
    exact = (
        all(got[k]["code"] == v["alone"] for k, v in truth["rpcs"].items())
        and got["(absent)"]["code"] == truth["absent"]
    )
    no_login_failed = not any(isinstance(v, dict) and v.get("reason") for v in got.values())
    same: Any = "NOT RUN: no --base"
    if base is not None:
        a = _emit(
            Path(base),
            ["emit-rec", "sessions", str(ctx["scratch"])],
            Path(ctx["scratch"]),
            "gocheck-base",
        )
        b = _emit(
            REPO, ["emit-rec", "sessions", str(ctx["scratch"])], Path(ctx["scratch"]), "gocheck-new"
        )
        same = a == b and "error" not in a
    return p.done(
        exact
        and no_login_failed
        and len(dis) == 4
        and all(got[k].get("isolated") is True for k in dis)
        and same is True
        and got["sessions.v1.Sessions/Stub"]["code"] == "UNIMPLEMENTED",
        {
            "codes equal the truth (alone)": exact,
            "login_failed": 0 if no_login_failed else "some",
            "disagreed": dis,
            "bytes equal the merge commit": same,
        },
    )


def _recorded(arm: str, pid: str) -> dict[str, Any]:
    d = json.loads(
        (REPO / f"docs/results/data/2026-10-17-grpc-round2/probes2-{arm}.json").read_text()
    )
    return next(r for r in d["probes"] if r["id"].startswith(pid))["observed"]  # type: ignore[no-any-return]


def _same_counts(now: Mapping[str, Any], was: Mapping[str, Any]) -> dict[str, Any]:
    drop = {"seconds", "in 15-20 (p)"}
    diff = {
        k: (now.get(k), was.get(k))
        for k in sorted(set(was) | set(now))
        if k not in drop and now.get(k) != was.get(k)
    }
    return diff


def pf3_n(ctx: dict[str, Any]) -> Probe:
    p = Probe(
        "PF3-N", "N 17/17 verified, identical counts to round 2", "identical counts to round 2"
    )
    pr = q2.pf2_n(ctx)
    diff = _same_counts(pr.observed, _recorded("armg", "PF2-N"))
    return p.done(
        pr.status == "PASS" and not diff,
        {**pr.observed, "differences from round 2": diff},
        pr.detail,
    )


def pf3_desk(ctx: dict[str, Any], desk: q2.App) -> Probe:
    p = Probe(
        "PF3-DESK (dev)",
        "deskd 41 recovered / 20 verified, edge recall 21/34, coverage 4/7, as round 2",
        "identical counts to round 2",
    )
    pr = q2.pf2_desk(ctx, desk)
    diff = _same_counts(pr.observed, _recorded("armg", "PF2-DESK"))
    return p.done(
        pr.status == "PASS" and not diff,
        {**pr.observed, "differences from round 2": diff},
        pr.detail,
    )


def pf3_leak(ctx: dict[str, Any], desk: q2.App | None) -> Probe:
    q2._stray = _stray3  # type: ignore[assignment]  # this runner's own command line names deskd
    pr = q2.pf2_leak(ctx, desk)
    pr.id, pr.prediction, pr.rule = "PF3-LEAK", "0", "0"
    return pr


def px3(ctx: dict[str, Any]) -> Probe:
    p = Probe("PX3", "0.5-4 s; 2-10 s; 8-30 s (p)", "<= 30 s; <= 30 s; <= 180 s")
    t = {k: ctx.get(k) for k in ("t_extract_kiln", "t_rec_desk", "t_flows_desk")}
    if any(v is None for v in t.values()):
        return p.not_run("kilnworks or deskd was not run")
    a, b, c = t["t_extract_kiln"], t["t_rec_desk"], t["t_flows_desk"]
    return p.done(
        a <= 30 and b <= 30 and c <= 180,
        {
            "extract on kilnworks (s)": round(a, 2),
            "reconcile on deskd (s)": round(b, 2),
            "flows on deskd (s)": round(c, 1),
        },
    )


def pr2_all(ctx: dict[str, Any]) -> Probe:
    """Round 2's runner, unchanged, at round-3 code; every row it passed then still passes except
    PR2-DESK (declared: the deskd output gains Reports/Backlog, D3.2)."""
    p = Probe(
        "PR2-ALL",
        "every round-2 PASS row still PASS except PR2-DESK's output bytes (declared)",
        "all other PASS rows PASS",
    )
    base_r1, base6a, deskd = ctx.get("base_r1"), ctx.get("base6a"), ctx.get("deskd")
    from_dir = ctx.get("pr2all_from")
    if not from_dir and not (ctx.get("base") and base_r1 and base6a and deskd):
        return p.not_run("--pr2all-from, or --base, --base-r1, --base6a and --deskd, are needed")
    scratch = Path(ctx["scratch"])
    out = Path(from_dir) if from_dir else scratch / "pr2all"
    rows: dict[str, Any] = {
        "source": f"round 2's runner run un-nested into {out}" if from_dir else "a child process"
    }
    ok = True
    for arm in ("0", "G"):
        name = {"0": "probes2-arm0.json", "G": "probes2-armg.json"}[arm]
        if (
            not from_dir
        ):  # nested, the parent's own command line can match a leak needle of round 2's PF2-LEAK
            cmd = [
                sys.executable,
                str(HERE / "grpc_probes2.py"),
                "--arm",
                arm,
                "--base",
                str(base_r1),
                "--deskd",
                str(deskd),
                "--scratch",
                str(scratch / f"pr2all-{arm}"),
                "--out",
                str(out / arm),
            ]
            if arm == "G":
                cmd += ["--base6a", str(base6a)]
            elif ctx.get("reg_before") and ctx.get("reg_after"):
                cmd += [
                    "--reg-before",
                    str(ctx["reg_before"]),
                    "--reg-after",
                    str(ctx["reg_after"]),
                ]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO,
                env={**os.environ, "PYTHONPATH": str(REPO)},
            )
        try:
            now = json.loads((out / arm / name).read_text())
        except OSError:
            rows[arm] = {"error": f"{out / arm / name} was not written"}
            ok = False
            continue
        was = json.loads((REPO / f"docs/results/data/2026-10-17-grpc-round2/{name}").read_text())
        was_s = {r["id"]: r["status"] for r in was["probes"]}
        now_s = {r["id"]: r["status"] for r in now["probes"]}
        regress = sorted(i for i, s in was_s.items() if s == "PASS" and now_s.get(i) != "PASS")
        declared = [i for i in regress if i.startswith("PR2-DESK")]
        other = [i for i in regress if i not in declared]
        rows[arm] = {
            "rows": len(now_s),
            "round-2 PASS rows still PASS": f"{sum(s == 'PASS' for s in was_s.values()) - len(regress)}/{sum(s == 'PASS' for s in was_s.values())}",
            "regressed (declared: PR2-DESK)": declared,
            "regressed (other)": other,
            "status now": now_s,
        }
        ok = ok and not other
    return p.done(ok, rows)


def kiln_rows(kiln_app: q2.App, ctx: dict[str, Any]) -> list[Row]:
    """PD3-KILN: kilnworks scored by the held-out scorers with round-3 code. Post hoc and tuned-on
    (round 3's scope was chosen by reading its failures): reported, never gated, each row beside
    round 2's strict value."""
    rows: list[Row] = []
    probes = [
        *rpc_rows(kiln_app, "PD3-KILN"),
        rec_row(kiln_app, "PD3-KILN", ctx),
        flows_row(kiln_app, "PD3-KILN", ctx),
    ]
    for pr in probes:
        key = pr.id.removeprefix("PD3-KILN-")
        r = Row(f"PD3-KILN/{key} (post hoc, tuned-on, never gated)", pr.prediction, "reported only")
        rows.append(
            r.reported(
                pr.status == "PASS",
                {
                    **(pr.observed if isinstance(pr.observed, dict) else {"observed": pr.observed}),
                    "round 2 strict": R2_KILN.get(key),
                },
                pr.detail,
            )
        )
    return rows


def armg(ctx: dict[str, Any]) -> list[Probe]:
    scratch = Path(ctx["scratch"])
    desk = q2.load_desk(Path(ctx["deskd"]), scratch) if ctx.get("deskd") else None
    probes: list[Probe] = [pr3_n(ctx)]
    probes.append(
        pr3_desk(ctx, desk) if desk else Probe("PR3-DESK (dev)", "", "").not_run("no --deskd")
    )
    probes.append(pr3_sess(ctx))
    probes.append(pf3_n(ctx))
    probes.append(
        pf3_desk(ctx, desk) if desk else Probe("PF3-DESK (dev)", "", "").not_run("no --deskd")
    )
    probes.append(pf3_leak(ctx, desk))
    probes.append(pr2_all(ctx))
    if ctx.get("kiln"):
        kiln = load_app(Path(ctx["kiln"]) / KILN_PKG, scratch)
        ctx["t_extract_kiln"] = _timed_extract(Path(ctx["kiln"]))
        kctx: dict[str, Any] = {}
        probes += kiln_rows(kiln, kctx)
        ctx["rec_kiln"], ctx["flows"] = kctx.get("rec"), kctx.get("flows")
    else:
        probes.append(Probe("PD3-KILN", "", "").not_run("no --kiln"))
    probes.append(px3(ctx))
    return probes


def _timed_extract(kiln_parent: Path) -> float:
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    t0 = time.perf_counter()
    GoGrpcFrontEnd().extract_json(kiln_parent, KILN_PKG)
    return time.perf_counter() - t0


def not_run_rows() -> list[Probe]:
    return [
        Probe("PM-S / PM-SG (Arm M)", "", "").not_run(
            "Arm M needs a Streak checkout and a Docker MySQL: out of scope (prereg 1); no docker daemon here"
        ),
        Probe("Arm L", "", "").not_run("the build round is its own node; out of scope"),
        Probe("PG-HO3 (HO3-*)", "", "").not_run(
            "the gated held-out stage: a new app by a new agent, scored once by the orchestrator after the freeze; this node never opened heldout3/"
        ),
    ]


# ---- Arm H3: the gated held-out app (prereg sections 6, 7.3 and 12; scored ONCE) ------------------------


class RunnerError(Exception):
    """A problem of this runner (or of the held-out layout): raised BEFORE any metric exists --
    never a score. Fixed in this file only, re-hashed, disclosed."""


def h3_verify(heldout: Path, freeze3: Path) -> dict[str, Any]:
    """Both listings against the hashes recorded in Amendment 2 of the prereg, the library files
    against ``freeze3``, the held-out files against its own ``freeze.sha256``."""
    out: dict[str, Any] = {}
    out["freeze3_sha256"] = sha(freeze3.read_bytes())
    fz = heldout / "freeze.sha256"
    if not fz.is_file():
        cands = [
            d / "freeze.sha256"
            for d in heldout.iterdir()
            if d.is_dir() and (d / "freeze.sha256").is_file()
        ]
        fz = cands[0] if len(cands) == 1 else fz
    out["heldout_freeze_path"] = str(fz.relative_to(heldout)) if fz.is_file() else "(none)"
    out["heldout_freeze_sha256"] = sha(fz.read_bytes()) if fz.is_file() else ""
    text = PREREG.read_text()
    amend = text[text.find("## Amendment 2") :] if "## Amendment 2" in text else ""
    recorded = set(re.findall(r"\b[0-9a-f]{64}\b", amend))
    out["amendment_2_found"] = bool(amend)
    out["freeze3_recorded"] = out["freeze3_sha256"] in recorded
    out["heldout_freeze_recorded"] = out["heldout_freeze_sha256"] in recorded
    out["freeze3_equals_committed_copy"] = (DATA / "freeze3.sha256").is_file() and (
        DATA / "freeze3.sha256"
    ).read_bytes() == freeze3.read_bytes()
    out["heldout_freeze_equals_committed_copy"] = (
        fz.is_file()
        and (DATA / "heldout3.freeze.sha256").is_file()
        and (DATA / "heldout3.freeze.sha256").read_bytes() == fz.read_bytes()
    )
    # The runner's own hash is superseded by the disclosed reader fix (round-3 results, HO3 section;
    # old hash 64a988a1..., Amendment 2). Every library file stays checked.
    out["library_problems"] = [
        x
        for x in r1._check_sha_file(freeze3, REPO)
        if x != "changed dogfood/grpc_probes3.py"
    ]
    out["heldout_problems"] = (
        r1._check_sha_file(fz, fz.parent) if fz.is_file() else ["no freeze.sha256"]
    )
    out["heldout_files"] = (
        sum(1 for ln in fz.read_text().splitlines() if ln.strip()) if fz.is_file() else 0
    )
    out["staged"] = sorted(git("diff", "--name-only", "--cached").split())
    out["ok"] = (
        out["amendment_2_found"]
        and out["freeze3_recorded"]
        and out["heldout_freeze_recorded"]
        and out["freeze3_equals_committed_copy"]
        and out["heldout_freeze_equals_committed_copy"]
        and not out["library_problems"]
        and not out["heldout_problems"]
        and not out["staged"]
    )
    return out


def h3_app_dir(heldout: Path) -> Path:
    apps = [
        d
        for d in sorted(heldout.iterdir())
        if d.is_dir() and (d / "harness.py").is_file() and (d / "truth.json").is_file()
    ]
    if len(apps) != 1:
        raise RunnerError(
            f"expected one app directory with harness.py and truth.json, found {[a.name for a in apps]}"
        )
    return apps[0]


def arm_h3(heldout: Path, freeze3: Path, scratch: Path, out: Path) -> int:
    heldout = Path(heldout).resolve()
    fz = h3_verify(heldout, freeze3)
    print(
        "freeze:",
        json.dumps(
            {
                k: fz[k]
                for k in (
                    "ok",
                    "heldout_files",
                    "amendment_2_found",
                    "freeze3_recorded",
                    "heldout_freeze_recorded",
                )
            }
        ),
    )
    if not fz["ok"]:
        print("REFUSED: a frozen file or a recorded hash differs; nothing was scored")
        print(json.dumps(fz, indent=1))
        return 2
    os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch / "gocache"))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch / "bridgecache"))
    ctx: dict[str, Any] = {}
    errors: list[str] = []
    probes: list[Probe] = []
    try:
        root = h3_app_dir(heldout)
        before = q2._snapshot(heldout)
        app = load_app(root, scratch)
        if app.runtime is None:
            raise RunnerError("harness RUNTIME is None (no AuthDriver.rpc in the library)")
    except (RunnerError, q2.ReaderMismatch) as err:
        print(f"RUNNER ERROR (layout or reader mismatch, no metric written): {err}")
        return 3
    except Exception as err:  # noqa: BLE001
        print(f"RUNNER ERROR (no metric written): {type(err).__name__}: {str(err)[:800]}")
        return 3
    shim = scratch / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    log = scratch / "claude-calls.log"
    log.write_text("")
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n')
    (shim / "claude").chmod(0o755)
    os.environ["PATH"] = f"{shim}:{os.environ['PATH']}"
    steps = (
        ("rpc", lambda: rpc_rows(app, "HO3")),
        ("HO3-REC", lambda: rec_row(app, "HO3", ctx)),
        ("HO3-FLOWS", lambda: flows_row(app, "HO3", ctx)),
        ("HO3-DET", lambda: det_row(root, scratch, "HO3", root.name)),
    )
    for name, fn in steps:
        try:
            res = fn()
            probes += res if isinstance(res, list) else [res]
        except Exception as err:  # noqa: BLE001 -- a runner failure is never a score
            errors.append(f"{name}: {type(err).__name__}: {str(err)[:800]}")
    if errors:
        print("RUNNER ERROR (no metric file written):")
        for e in errors:
            print(" ", e)
        return 3
    after = q2._snapshot(heldout)
    changed = [x for x in before if before[x] != after.get(x)]
    new = [x for x in after if x not in before]
    calls = len([ln for ln in log.read_text().splitlines() if ln.strip()])
    probes.append(
        Probe("HO3-NW", PRED["HO3-NW"], RULE["HO3-NW"]).done(
            not changed and not new and calls == 0,
            {"changed": len(changed), "new": len(new), "claude calls": calls},
            changed + new,
        )
    )
    fz_after = h3_verify(heldout, freeze3)
    probes += [p for p in not_run_rows() if not p.id.startswith("PG-HO3")]
    doc = {
        "schema": "lattice.grpc-probes3-h3/1",
        "arm": "H3",
        "heldout_freeze_before": fz,
        "heldout_freeze_after_ok": fz_after["ok"],
        "library_sha256": library_hashes(),
        "truth_reader": {
            "app": root.name,
            "schema": app.truth.get("schema"),
            "step_index_base": app.truth.get("step_index_base"),
            "keys": app.truth["keys"],
            "rpcs": len(app.truth["rpcs"]),
            "rpcs_with_effects": sorted(
                k for k, v in app.truth["effects"].items() if v not in ("none", "")
            ),
            "tables": len(app.truth["tables"]),
            "flows": len(app.truth["flows"]),
        },
        "probes": [pr.row() for pr in probes],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes3-armh3.json").write_text(json.dumps(doc, indent=1, default=str) + "\n")
    (out / "reconcile-heldout3.json").write_text(json.dumps(ctx.get("rec"), indent=1) + "\n")
    (out / "flows-heldout3.json").write_text(
        json.dumps(ctx.get("flows"), indent=1, default=str) + "\n"
    )
    for pr in probes:
        print(f"{pr.status:8} {pr.id:24} {json.dumps(pr.observed, default=str)[:260]}")
    failed = [pr for pr in probes if pr.status == "FAIL"]
    print(
        f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed, {sum(pr.status == 'NOT RUN' for pr in probes)} not run"
    )
    return 1 if failed else 0


# ---- main -----------------------------------------------------------------------------------------------


def run_check(name: str, ctx: dict[str, Any]) -> int:
    scratch = Path(ctx["scratch"])
    deskd = Path(ctx["deskd"]) if ctx.get("deskd") else None
    table = {
        "p3-wire": p3_wire,
        "p3-authu": p3_authu,
        "p3-score": p3_score,
        "p3-bc-ast": lambda: p3_bc_ast(Path(ctx["base"])),
        "p3-bc-ext": lambda: p3_bc_ext(ctx),
        "pr3-sess": lambda: pr3_sess(ctx),
        "pr3-n": lambda: pr3_n(ctx),
        "pr3-desk": lambda: pr3_desk(ctx, q2.load_desk(deskd, scratch)),  # type: ignore[arg-type]
    }
    pr = table[name]()
    print(f"{pr.status} {pr.id} {json.dumps(pr.observed, default=str)[:400]}")
    return 0 if pr.status == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["emit-det3"]:
        cmd_emit_det3(args_in[1], args_in[2], args_in[3])
        return 0
    if args_in[:1] == ["emit-det-app"]:
        cmd_emit_det_app(args_in[1], args_in[2], args_in[3])
        return 0
    if args_in[:1] == ["nw-work"]:
        cmd_nw_work(args_in[1], args_in[2], args_in[3], args_in[4])
        return 0
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--arm", choices=("0", "G", "H3"))
    ap.add_argument("--check", default=None, help="run one probe by name (what the mutants run)")
    ap.add_argument(
        "--base", type=Path, default=None, help="git archive of the merge commit, extracted"
    )
    ap.add_argument(
        "--base-r1", type=Path, default=None, help="git archive cea85fc (round 1), for PR2-ALL"
    )
    ap.add_argument("--base6a", type=Path, default=None, help="git archive 6a318e5, for PR2-ALL")
    ap.add_argument(
        "--deskd", type=Path, default=None, help="development data: the deskd directory"
    )
    ap.add_argument(
        "--kiln", type=Path, default=None, help="development data: the directory holding kilnworks/"
    )
    ap.add_argument("--heldout", type=Path, default=None, help="--arm H3: the held-out directory")
    ap.add_argument("--freeze", type=Path, default=None, help="--arm H3: freeze3.sha256")
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DATA)
    ap.add_argument("--reg-before", type=Path, default=None)
    ap.add_argument("--reg-after", type=Path, default=None)
    ap.add_argument("--no-pr2all", action="store_true")
    ap.add_argument(
        "--pr2all-from",
        type=Path,
        default=None,
        help="round 2's runner outputs (arm dirs 0/ and G/), run un-nested",
    )
    args = ap.parse_args(args_in)
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="grpc3-probes-"))
    scratch.mkdir(parents=True, exist_ok=True)
    if args.arm == "H3":
        if args.heldout is None or args.freeze is None:
            ap.error("--arm H3 needs --heldout and --freeze")
        return arm_h3(args.heldout, args.freeze, scratch, args.out)
    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    ctx: dict[str, Any] = {
        "scratch": scratch,
        "base": args.base,
        "base_r1": None if args.no_pr2all else args.base_r1,
        "base6a": args.base6a,
        "deskd": args.deskd,
        "kiln": args.kiln,
        "reg_before": args.reg_before,
        "reg_after": args.reg_after,
        "pr2all_from": args.pr2all_from,
    }
    if args.check:
        return run_check(args.check, ctx)
    if args.arm is None:
        ap.error("--arm or --check is required")
    args.out.mkdir(parents=True, exist_ok=True)
    before = frozen_check(args.deskd, args.kiln)
    probes = arm0(ctx) if args.arm == "0" else armg(ctx)
    after = frozen_check(args.deskd, args.kiln)
    probes += not_run_rows()
    doc: dict[str, Any] = {
        "schema": "lattice.grpc-probes3/1",
        "arm": args.arm,
        "library_sha256": library_hashes(),
        "frozen_before": before,
        "frozen_after": after,
        "probes": [pr.row() for pr in probes],
    }
    name = {"0": "probes3-arm0.json", "G": "probes3-armg.json"}[args.arm]
    (args.out / name).write_text(json.dumps(doc, indent=1, default=str) + "\n")
    if ctx.get("rec_desk") is not None:
        (args.out / "reconcile-deskd.json").write_text(json.dumps(ctx["rec_desk"], indent=1) + "\n")
    if ctx.get("flows_desk") is not None:
        (args.out / "flows-deskd.json").write_text(
            json.dumps(ctx["flows_desk"], indent=1, default=str) + "\n"
        )
    if ctx.get("flows_n") is not None:
        (args.out / "flows-n.json").write_text(
            json.dumps(ctx["flows_n"], indent=1, default=str) + "\n"
        )
    if ctx.get("rec_kiln") is not None:
        (args.out / "reconcile-kilnworks.json").write_text(
            json.dumps(ctx["rec_kiln"], indent=1) + "\n"
        )
    if ctx.get("flows") is not None:
        (args.out / "flows-kilnworks.json").write_text(
            json.dumps(ctx["flows"], indent=1, default=str) + "\n"
        )
    for pr in probes:
        print(f"{pr.status:8} {pr.id:44} {json.dumps(pr.observed, default=str)[:200]}")
    print("frozen files ok before:", before["ok"], "after:", after["ok"])
    failed = [pr for pr in probes if pr.status == "FAIL"]
    print(
        f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed, "
        f"{sum(pr.status == 'NOT RUN' for pr in probes)} not run, {sum(pr.status == 'REPORTED' for pr in probes)} reported"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
