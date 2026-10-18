"""Probes of gRPC round 4 (docs/results/2026-10-18-grpc-round4-prereg.md, with Amendment 1):
registration through generic helpers, option constructors, init registries and interface dispatch
and the module-wide safety rule (D1), interceptors as method values / option fields / constants /
builder chains (D2), codes through a translation layer (D3), tables from the harness database (D5),
login fields read from the proto (D6), and a held-out stage whose process is checked by the runner
itself (section 6.4).

Arms 0 (static) and G (Go servers over pure-Go SQLite, no Docker) are the development stage.
``deskd``, kilnworks and harborline have all been scored and read: they are **development data**,
never a blind or held-out claim (kilnworks and harborline are PD4-KILN / PD4-HARBOR, post hoc,
reported only). Arm M and Arm L are ``NOT RUN``. ``--arm H4`` is the gated held-out stage (PG-HO4: a
new app by a new author, scored ONCE by the orchestrator after Amendment 3); it checks its own
sha256, the scorer's, ``freeze4.sha256`` and the held-out listing against the amendments BEFORE any
metric, keeps a ledger of its runs (one re-run, only after a crash before any metric), and leaves the
``blind`` / ``post_hoc`` label of every row to ``grpc_scorer4.provenance``. The build node never ran
``--arm H4`` on a held-out and never looked for one (``--process-test`` rehearses the process only).

    export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
    uv run python dogfood/grpc_probes4.py --arm 0 --base "$SCRATCH/base" --deskd <deskd> \\
        --kiln <kilnworks parent> --harbor <harborline parent> --scratch "$SCRATCH/p0" --out <dir>
    uv run python dogfood/grpc_probes4.py --arm G ...
    uv run python dogfood/grpc_probes4.py --arm H4 --heldout <heldout4> --freeze <freeze4.sha256> --out <dir>

Internal sub-commands: ``emit-det4``, ``nw-work4`` (subprocesses) and ``--check <name>`` (one probe,
exit 0 on PASS: what ``grpc_mutants4.py`` runs).
"""

# ruff: noqa: E501 -- probe texts and table rows are data lines
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO))

import grpc_probes as r1  # noqa: E402
import grpc_probes2 as q2  # noqa: E402
import grpc_probes3 as p3  # noqa: E402
import grpc_scorer4 as sc4  # noqa: E402

sha = r1.sha
Probe = r1.Probe
Row = p3.Row
FIXTURES = REPO / "tests" / "grpc" / "fixtures"
DATA = REPO / "docs/results/data/2026-10-18-grpc-round4"
PREREG = REPO / "docs/results/2026-10-18-grpc-round4-prereg.md"
BASE = "be26a94"  # the commit every budget is counted from
FAMILIES = {"wiring4": ["main", "unres", "varlost"], "authwire4": None, "codes4": None}
TABLE_CASES = ["gormtables", "harnessdb", "brokendb"]
HARBOR_FREEZE_SHA = "1c51ee0e5a1c4118205445b0f6d5ef882f5180af519f4ed5df285a9133d718b8"
RUNNER = "dogfood/grpc_probes4.py"

BUDGETS = {
    "lattice/gocheck/src/goextract_grpc.go": 400,
    "lattice/importer/frontends/go_grpc.py": 50,
    "lattice/spec/grpc.py": 50,
    "lattice/importer/transport.py": 6,
}
CREATES = (
    "dogfood/grpc_probes4.py",
    "dogfood/grpc_scorer4.py",
    "dogfood/grpc_scorer4_cases.json",
    "dogfood/grpc_mutants4.py",
    "dogfood/grpc_contract4.py",
    "tests/grpc/test_wiring4.py",
    "tests/grpc/test_auth4.py",
    "tests/grpc/test_codes4.py",
    "tests/grpc/test_tables4.py",
    "tests/grpc/test_login4.py",
    "tests/grpc/fixtures/wiring4",
    "tests/grpc/fixtures/authwire4",
    "tests/grpc/fixtures/codes4",
    "tests/grpc/fixtures/tables4",
    "tests/grpc/fixtures/login4",
    "docs/results/2026-10-18-grpc-round4-prereg.md",  # appended amendments only
    "docs/results/2026-10-18-grpc-round4-results.md",
    "docs/results/data/2026-10-18-grpc-round4",
)
FROZEN_PATHSPECS = [  # section 9, "stays frozen": no tracked change against BASE
    "lattice/gocheck",
    ":(exclude)lattice/gocheck/src/goextract_grpc.go",
    "lattice/importer/workflows.py",
    "lattice/importer/outside.py",
    "lattice/grpcbridge",
    "dogfood",
    ":(exclude)dogfood/grpc_probes4.py",
    ":(exclude)dogfood/grpc_scorer4.py",
    ":(exclude)dogfood/grpc_scorer4_cases.json",
    ":(exclude)dogfood/grpc_mutants4.py",
    ":(exclude)dogfood/grpc_contract4.py",
    "tests/grpc",
]
NAMED_FROZEN = (  # the files section 9 names, hashed at BASE and now
    "lattice/gocheck/src/goextract_proto.go",
    "lattice/gocheck/src/goextract.go",
    "lattice/gocheck/src/load.go",
    "lattice/importer/workflows.py",
    "lattice/importer/outside.py",
    "dogfood/grpc_flow_scorer.py",
    "dogfood/grpc_probes3.py",
    "dogfood/grpc_scorer3.py",
    "dogfood/grpc_scorer3_cases.json",
)

PRED = {
    "HO4-RPC": "recall 0.55-1.00 / precision 0.60-1.00 (point 0.85 / 0.88)",
    "HO4-SAFE": "0-4 confident-wrong (point 0; P(0) about 0.7)",
    "HO4-CODES": "recall 0.25-0.85 / precision 0.75-1.00 (point 0.50 / 0.92)",
    "HO4-AUTH": "wrong 0 (P 0.85); unclear 0.00-1.00 (point 0.55)",
    "HO4-TABLES": "recall 0.85-1.00 (point 1.00)",
    "HO4-REC": "coverage 0.95-1.00; agreement 0.60-1.00 (point 0.92); P(login works) about 0.8",
    "HO4-FLOWS": "recovered 0-60; verified 0-10 (point 3); edge recall 0.00-0.50 (point 0.20); coverage 0-5 (point 2); 0 vbf",
    "HO4-DET": "10/10 identical",
    "HO4-NW": "0, 0, 0",
}
RULE = {
    "HO4-RPC": "recall >= 0.90 and precision >= 0.95 (rpc name AND state must match; `unread` counts wrong)",
    "HO4-SAFE": "S8: 0 confident-wrong states or auth labels",
    "HO4-CODES": "S9: micro recall >= 0.70 and micro precision >= 0.90 (implemented rpcs, OK excluded; unread is no extra)",
    "HO4-AUTH": "S7: 0 wrong (hard) and unclear <= 0.50 of the truth's implemented + unimplemented rpcs",
    "HO4-TABLES": "recall >= 0.90 (`tables_source` reported beside it)",
    "HO4-REC": "S5 (coverage >= 0.95, agreement 1.00, supplementary 0) + S6 not FAIL + 0 auth_refused + 0 login_failed on truth-implemented non-public rpcs + every disagreement isolated",
    "HO4-FLOWS": "S2: verified >= 1, 0 verified-but-failing, edge recall >= 0.30, coverage >= 2 truth flows",
    "HO4-DET": "10/10 identical, no absolute path, time or port",
    "HO4-NW": "0 changed, 0 new, 0 claude calls",
}
R3 = {  # round 3 (strict), what PD4-KILN and PD4-HARBOR are read beside
    "KILN": {
        "RPC": "1.0 / 1.0",
        "CODES": "0.8 / 0.941",
        "AUTH": "0 wrong, unclear 17/17",
        "TABLES": "1.0",
        "REC": "1.0 / 1.0",
        "FLOWS": "17/7, edge 11/33, coverage 6/9",
    },
    "HARBOR": {
        "RPC": "0.68 / 0.68",
        "CODES": "0.222 / 0.667",
        "AUTH": "0 wrong, unclear 22/22",
        "TABLES": "0 of 7",
        "REC": "coverage 1.00, agreement 0.04",
        "FLOWS": "recovered 0, truth_not_scorable",
    },
}


def git(*args: str, cwd: Path = REPO) -> str:
    return p3.git(*args, cwd=cwd)


# ---- the frozen files, the budgets --------------------------------------------------------------


def library_hashes() -> dict[str, str]:
    files = [
        *BUDGETS,
        *NAMED_FROZEN,
        "dogfood/grpc_scorer4.py",
        "dogfood/grpc_scorer4_cases.json",
        RUNNER,
    ]
    files.append("dogfood/grpc_contract4.py")
    return {f: sha((REPO / f).read_bytes()) for f in sorted(files) if (REPO / f).is_file()}


def frozen_check(deskd: Path | None, kiln: Path | None, harbor: Path | None) -> dict[str, Any]:
    """Section 9: no tracked change in the frozen paths, the named frozen hashes against BASE, the
    line budgets, every changed or new path inside 'may change' or 'creates', the corpora's freezes."""
    out: dict[str, Any] = {}
    changed_frozen = sorted(git("diff", "--name-only", BASE, "--", *FROZEN_PATHSPECS).split())
    named = {}
    for f in NAMED_FROZEN:
        was = hashlib.sha256(
            subprocess.run(
                ["git", "show", f"{BASE}:{f}"], cwd=REPO, capture_output=True, check=False
            ).stdout
        ).hexdigest()
        named[f] = {
            "base": was[:16],
            "now": sha((REPO / f).read_bytes())[:16],
            "same": was == sha((REPO / f).read_bytes()),
        }
    out["frozen_files_changed"] = changed_frozen + [f for f, v in named.items() if not v["same"]]
    out["named_frozen"] = named
    status = git("diff", "--name-status", BASE).splitlines()
    changed = sorted(ln.split("\t", 1)[1] for ln in status if ln.strip())
    untracked = git("ls-files", "--others", "--exclude-standard").split()

    def new_ok(path: str) -> bool:
        return any(path == a or path.startswith(a + "/") for a in CREATES)

    out["tracked_changed_vs_base"] = changed
    out["unexpected"] = sorted(
        [c for c in changed if c not in BUDGETS and not new_ok(c)]
        + [u for u in untracked if not new_ok(u)]
    )
    budgets: dict[str, Any] = {}
    for f, cap in BUDGETS.items():
        ns = git("diff", "--numstat", BASE, "--", f).split()
        n = int(ns[0]) + int(ns[1]) if len(ns) >= 2 and ns[0].isdigit() else 0
        budgets[f] = {"insertions+deletions": n, "budget": cap, "ok": n <= cap}
    out["budgets"] = budgets
    out["staged"] = sorted(git("diff", "--name-only", "--cached").split())
    if deskd is not None:
        parent = deskd.resolve().parent
        out["deskd_freeze_ok"] = sha((parent / "freeze.sha256").read_bytes()) == p3.DESKD_FREEZE_SHA
        out["deskd_files_changed"] = r1._check_sha_file(parent / "freeze.sha256", parent)
    if kiln is not None:
        base = kiln / p3.KILN_PKG
        out["kilnworks_freeze_ok"] = (
            sha((base / "freeze.sha256").read_bytes()) == p3.KILN_FREEZE_SHA
        )
        out["kilnworks_files_changed"] = r1._check_sha_file(base / "freeze.sha256", base)
    if harbor is not None:
        base = harbor / "harborline"
        out["harborline_freeze_ok"] = (
            sha((base / "freeze.sha256").read_bytes()) == HARBOR_FREEZE_SHA
        )
        out["harborline_files_changed"] = r1._check_sha_file(base / "freeze.sha256", base)
    dev = DATA / "freeze-dev4.sha256"
    out["dev_truth_hashes_changed"] = (
        r1._check_sha_file(dev, REPO) if dev.is_file() else ["missing"]
    )
    out["ok"] = (
        not out["frozen_files_changed"]
        and not out["unexpected"]
        and not out["staged"]
        and all(b["ok"] for b in budgets.values())
        and out.get("deskd_freeze_ok", True)
        and not out.get("deskd_files_changed")
        and out.get("kilnworks_freeze_ok", True)
        and not out.get("kilnworks_files_changed")
        and out.get("harborline_freeze_ok", True)
        and not out.get("harborline_files_changed")
        and not out["dev_truth_hashes_changed"]
    )
    return out


# ---- Arm 0 --------------------------------------------------------------------------------------


def _name(n: ast.AST) -> str:
    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
        return n.targets[0].id
    return getattr(n, "name", None) or ast.dump(n)


def _top(src: str) -> dict[str, str]:
    return {_name(n): ast.dump(n) for n in ast.parse(src).body}


def _methods(src: str, cls: str) -> dict[str, str]:
    for n in ast.parse(src).body:
        if isinstance(n, ast.ClassDef) and n.name == cls:
            return {getattr(m, "name", None) or ast.dump(m): ast.dump(m) for m in n.body}
    return {}


def _no_defaults(fn_src: str) -> str:
    """A method's AST with its parameter defaults removed (what D6.1 changes)."""
    tree = ast.parse(fn_src)
    for n in ast.walk(tree):
        if isinstance(n, ast.arguments):
            n.kw_defaults = [None] * len(n.kw_defaults)
            n.defaults = []
        if isinstance(n, ast.arg):
            n.annotation = None
    return ast.dump(tree)


def p4_bc_ast(base: Path) -> Probe:
    """``spec/grpc.py``: every top-level definition but ``rpc_login`` (and the one private helper)
    identical; ``transport.py``: only ``AuthDriver.rpc``'s defaults differ; ``go_grpc.py``: only the
    D5 additions; ``workflows.py`` and ``outside.py`` byte-identical."""
    p = Probe(
        "P4-BC-AST",
        "identical outside rpc_login (+ _login_fields), AuthDriver.rpc defaults, D5",
        "identical",
    )
    bad: list[str] = []
    old, new = (
        _top((base / "lattice/spec/grpc.py").read_text()),
        _top((REPO / "lattice/spec/grpc.py").read_text()),
    )
    added = sorted(set(new) - set(old))
    bad += [
        f"grpc.py: {k[:60]}"
        for k in sorted(set(old) | set(new))
        if k not in ("rpc_login", *added) and old.get(k) != new.get(k)
    ]
    if added != ["_login_fields"]:
        bad.append(f"grpc.py: new definitions {added}")
    to, tn = (
        (base / "lattice/importer/transport.py").read_text(),
        (REPO / "lattice/importer/transport.py").read_text(),
    )
    o, n = _top(to), _top(tn)
    bad += [
        f"transport.py: {k[:60]}"
        for k in sorted(set(o) | set(n))
        if k != "AuthDriver" and o.get(k) != n.get(k)
    ]
    om, nm = _methods(to, "AuthDriver"), _methods(tn, "AuthDriver")
    bad += [
        f"AuthDriver.{k}"
        for k in sorted(set(om) | set(nm))
        if k != "rpc" and om.get(k) != nm.get(k)
    ]
    gold, gnew = (
        (base / "lattice/importer/frontends/go_grpc.py").read_text(),
        (REPO / "lattice/importer/frontends/go_grpc.py").read_text(),
    )
    go_, gn = _top(gold), _top(gnew)
    gadd = sorted(set(gn) - set(go_))
    bad += [
        f"go_grpc.py: {k[:60]}"
        for k in sorted(set(go_) | set(gn))
        if k not in ("GoGrpcFrontEnd", *gadd) and go_.get(k) != gn.get(k)
    ]
    if gadd != ["_SQL_TYPES", "_harness_tables"]:
        bad.append(f"go_grpc.py: new definitions {gadd}")
    gom, gnm = _methods(gold, "GoGrpcFrontEnd"), _methods(gnew, "GoGrpcFrontEnd")
    if sorted(set(gnm) - set(gom)) != ["extract_json"]:
        bad.append("GoGrpcFrontEnd: new members are not just extract_json")
    bad += [f"GoGrpcFrontEnd.{k}" for k in sorted(gom) if gom[k] != gnm.get(k)]
    for f in ("lattice/importer/workflows.py", "lattice/importer/outside.py"):
        if (base / f).read_bytes() != (REPO / f).read_bytes():
            bad.append(f"{f}: bytes differ")
    return p.done(
        not bad,
        {
            "AuthDriver.rpc differs only in defaults": _no_defaults(_src_of(to, "rpc"))
            == _no_defaults(_src_of(tn, "rpc")),
            "new in grpc.py": added,
            "new in go_grpc.py": gadd,
            "differences": len(bad),
        },
        bad,
    )


def _src_of(src: str, method: str) -> str:
    import textwrap

    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == method:
            return textwrap.dedent(ast.get_source_segment(src, n) or "")
    return ""


def p4_bc_ext(ctx: dict[str, Any]) -> Probe:
    """Extract and RepoModel of N, T, sessions and deskd byte-identical to BASE; kilnworks and
    harborline are the declared differences (D2.3's health table; the intended fix)."""
    p = Probe(
        "P4-BC-EXT",
        "identical on N, T, sessions, deskd; kilnworks, harborline differ (declared)",
        "identical",
    )
    base, scratch = ctx.get("base"), Path(ctx["scratch"])
    if base is None:
        return p.not_run("no --base")
    targets = [(str(FIXTURES), "notes"), (str(FIXTURES), "protos"), (str(FIXTURES), "sessions")]
    if ctx.get("deskd"):
        targets.append((str(Path(ctx["deskd"]).resolve().parent), "deskd"))
    declared: list[str] = []
    if ctx.get("kiln"):
        targets.append((str(Path(ctx["kiln"])), p3.KILN_PKG))
        declared += [f"{p3.KILN_PKG}:{x}" for x in ("extract", "model")]
    if ctx.get("harbor"):
        targets.append((str(Path(ctx["harbor"])), "harborline"))
        declared += [f"harborline:{x}" for x in ("extract", "model")]
    a = p3._extract_hashes(Path(base), targets, scratch, "gocheck-base")
    b = p3._extract_hashes(REPO, targets, scratch, "gocheck-new")
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    undeclared = [d for d in diff if d not in declared]
    return p.done(
        not undeclared and sorted(diff) == sorted(declared),
        {"compared": len(a), "differ": diff, "declared": declared, "undeclared": undeclared},
        diff,
    )


def p4_bc(ctx: dict[str, Any]) -> Probe:
    p = Probe("P4-BC", "all identical (p)", "all")
    base, scratch = ctx.get("base"), Path(ctx["scratch"])
    if base is None:
        return p.not_run("no --base: git archive of BASE was not given")
    base = Path(base)
    ast_probe = p4_bc_ast(base)
    http = r1.bc_probe({"base": base, "scratch": scratch})
    ext = p4_bc_ext(ctx)
    rec = {}
    for kind in ("n", "sessions"):
        a = p3._emit(base, ["emit-rec", kind, str(scratch)], scratch, "gocheck-base")
        b = p3._emit(REPO, ["emit-rec", kind, str(scratch)], scratch, "gocheck-new")
        rec[kind] = a == b and "error" not in a
    flows: dict[str, Any] = {}
    kinds = [("n", FIXTURES, FIXTURES)]
    if ctx.get("deskd"):
        d = Path(ctx["deskd"]).resolve().parent
        kinds.append(("desk", d, d))
    for kind, rb, rn in kinds:
        a = p3._emit(base, ["emit-det", kind, str(rb), str(scratch)], scratch, "gocheck-base")
        b = p3._emit(REPO, ["emit-det", kind, str(rn), str(scratch)], scratch, "gocheck-new")
        flows[kind] = (
            {"error": str(a.get("error", b.get("error")))[:200]}
            if "error" in a or "error" in b
            else {k: a[k] == b[k] for k in sorted(a)}
        )
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
            "ast (grpc.py, transport.py, go_grpc.py, workflows.py, outside.py)": ast_probe.status,
            "HTTP outputs (round-1/2 emitter) vs BASE": http.status,
            "HTTP outputs compared": (http.observed or {}).get("compared"),
            "extract + RepoModel": ext.status,
            "reconcile bytes (library `reconcile`) on N and sessions": rec,
            "flows/plans/extract/reconcile on N and flows/plans/extract on deskd, identical": flows,
            "HTTP workflow tests": wf["counts"],
        },
        {"ast": ast_probe.detail, "http": http.detail, "ext": ext.detail},
    )


def _truth4(family: str) -> Any:
    return json.loads((FIXTURES / family / "truth.json").read_text())


def _dev_hash_ok() -> bool:
    return not r1._check_sha_file(DATA / "freeze-dev4.sha256", REPO)


def p4_wire() -> Probe:
    p = Probe(
        "P4-WIRE",
        "positives implemented; the never-built implementer and the declared-never-registered service not_registered; an unresolved registration makes every unregistered service unread; the var read only by unreached code unread",
        "exact on every service and rpc state; 0 S8 wrong",
    )
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    truth = _truth4("wiring4")["cases"]
    fe = GoGrpcFrontEnd()
    wrong: dict[str, Any] = {}
    s8: list[Any] = []
    n = 0
    for case, t in truth.items():
        ex = fe.extract_json(FIXTURES / "wiring4", case)
        got = {f"{s['name']}/{r['name']}": r for s in ex["grpc"]["services"] for r in s["rpcs"]}
        for k, st in t["rpcs"].items():
            n += 1
            have = got[k]["state"] if k in got else "(missing)"
            if have != st:
                wrong[f"{case}:{k}"] = (have, st)
        row = sc4.score_safe(
            {k: {"state": v} for k, v in t["rpcs"].items()},
            {k: {"state": v["state"]} for k, v in got.items()},
        )
        s8 += [f"{case}:{w[0]}" for w in row["wrong"]]
    return p.done(
        not wrong and not s8 and _dev_hash_ok(),
        {
            "rpcs": n,
            "states equal the truth": n - len(wrong),
            "wrong (extract, truth)": wrong,
            "S8 confident-wrong": s8,
            "truth hashed before library code": _dev_hash_ok(),
        },
        wrong,
    )


def p4_authi() -> Probe:
    p = Probe(
        "P4-AUTHI",
        "allowlisted Public, others Member; the == case not prefix-matched; the unresolvable builder unclear",
        "exact; 0 Public behind an unread mechanism",
    )
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    truth = _truth4("authwire4")["cases"]
    fe = GoGrpcFrontEnd()
    bad: list[str] = []
    public_behind_unread: list[str] = []
    for case, want in truth.items():
        ex = fe.extract_json(FIXTURES / "authwire4", case)
        svcs = ex["grpc"]["services"]
        auth = {
            (r["name"] if len(svcs) == 1 else f"{s['name'][-4:]}/{r['name']}"): r["auth"]
            for s in svcs
            for r in s["rpcs"]
        }
        kinds = [i["kind"] for i in ex["grpc"]["interceptors"]]
        if auth != want["auth"]:
            bad.append(f"{case}: auth {auth} != {want['auth']}")
        if kinds != want["kinds"]:
            bad.append(f"{case}: kinds {kinds} != {want['kinds']}")
        if {"auth_unknown", "auth_not_literal"} & set(kinds):
            public_behind_unread += [f"{case}/{k}" for k, v in auth.items() if v == "Public"]
    return p.done(
        not bad and not public_behind_unread and _dev_hash_ok(),
        {
            "cases": len(truth),
            "cases exact": len(truth) - len({b.split(":")[0] for b in bad}),
            "Public behind an unread mechanism": public_behind_unread,
            "truth hashed before library code": _dev_hash_ok(),
        },
        bad,
    )


def p4_codes() -> Probe:
    p = Probe(
        "P4-CODES",
        "each listed code; an untraced error unread; 0 UNKNOWN placeholders",
        "exact; 0 UNKNOWN not in the truth",
    )
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    truth = _truth4("codes4")["cases"]
    fe = GoGrpcFrontEnd()
    bad: list[str] = []
    unknown: list[str] = []
    for case, t in truth.items():
        ex = fe.extract_json(FIXTURES / "codes4", case)
        got = {
            f"{s['name'].rsplit('.', 1)[1]}/{r['name']}": r
            for s in ex["grpc"]["services"]
            for r in s["rpcs"]
        }
        for k, want in t["rpcs"].items():
            r = got[k]
            codes = sorted(
                {c["name"] for c in r.get("codes", []) if c["kind"] != "unimplemented_stub"}
            )
            unread = any(u["what"] == "grpc_code_not_literal" for u in r.get("unread", []))
            if codes != want["codes"] or unread != want["unread"]:
                bad.append(f"{case}:{k}: {codes} unread={unread} != {want}")
            if "UNKNOWN" in codes and "UNKNOWN" not in want["codes"]:
                unknown.append(f"{case}:{k}")
    return p.done(
        not bad and not unknown and _dev_hash_ok(),
        {
            "rpcs": sum(len(t["rpcs"]) for t in truth.values()),
            "disagreements": len(bad),
            "UNKNOWN not in the truth": unknown,
            "truth hashed before library code": _dev_hash_ok(),
        },
        bad,
    )


def p4_tables(ctx: dict[str, Any] | None = None) -> Probe:
    p = Probe(
        "P4-TABLES",
        "Go DDL: as round 3, no tables_source; harness only: all tables, harness_db; temp dir removed",
        "exact; 0 writes under either root",
    )
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    truth = _truth4("tables4")["cases"]
    fe = GoGrpcFrontEnd()
    roots = {c: FIXTURES / "tables4" / c for c in TABLE_CASES}
    before = {c: q2._snapshot(r) for c, r in roots.items()}
    scratch = Path(tempfile.mkdtemp(prefix="p4-tables-tmp-"))
    old = tempfile.tempdir
    tempfile.tempdir = str(scratch)
    bad: list[str] = []
    try:
        for c, want in truth.items():
            d = fe.extract_json(FIXTURES / "tables4", c)
            names = sorted(t["table"] for t in d["tables"])
            if names != want["tables"]:
                bad.append(f"{c}: tables {names} != {want['tables']}")
            if d.get("tables_source") != want["tables_source"]:
                bad.append(
                    f"{c}: tables_source {d.get('tables_source')!r} != {want['tables_source']!r}"
                )
            if want.get("unread") and not any(u["what"] == want["unread"] for u in d["unread"]):
                bad.append(f"{c}: no {want['unread']}")
    finally:
        tempfile.tempdir = old
    left = sorted(x.name for x in scratch.iterdir())
    shutil.rmtree(scratch, ignore_errors=True)
    after = {c: q2._snapshot(r) for c, r in roots.items()}
    writes = [
        f"{c}:{x}"
        for c in roots
        for x in set(before[c]) | set(after[c])
        if before[c].get(x) != after[c].get(x)
    ]
    return p.done(
        not bad and not writes and not left and _dev_hash_ok(),
        {
            "cases": len(truth),
            "disagreements": len(bad),
            "temp dirs left": left,
            "writes under the roots": len(writes),
        },
        bad + writes,
    )


def p4_login() -> Probe:
    p = Probe(
        "P4-LOGIN",
        "discovered username / name / email+passcode / user; ambiguous refused; explicit wins",
        "exact",
    )
    from dataclasses import dataclass

    from lattice.spec.grpc import GrpcAnswer, GrpcTransport, rpc_login

    @dataclass(eq=False)
    class Rec(GrpcTransport):
        sent: Any = None

        def call(
            self, service: str, method: str, message: Any = None, metadata: Any = None
        ) -> GrpcAnswer:
            self.sent = message
            return GrpcAnswer("OK", 0, "", {"token": "t"})

    class Client:
        def __init__(self) -> None:
            self.rpc = Rec(
                target="127.0.0.1:1",
                protos=["proto/login4/v1/login4.proto"],
                root=FIXTURES / "login4",
            )

        def _lattice_rpc(self) -> Any:
            return self.rpc

    truth = _truth4("login4")
    svc, creds = truth["service"], {"ada": ("ada", "pw")}
    bad: list[str] = []
    rows: dict[str, Any] = {}
    for method, want in truth["discovered"].items():
        c = Client()
        try:
            rpc_login(svc, method, creds)(c, "ada")
            rows[method] = c.rpc.sent
            if c.rpc.sent != want:
                bad.append(f"{method}: {c.rpc.sent} != {want}")
        finally:
            c.rpc.close()
    for method in truth["ambiguous"]:
        c = Client()
        try:
            rpc_login(svc, method, creds)(c, "ada")
            bad.append(f"{method}: no error")
        except RuntimeError as err:
            rows[method] = str(err).split(":")[0]
            if rows[method] != "login_fields_ambiguous" or c.rpc.sent is not None:
                bad.append(f"{method}: {err}")
        finally:
            c.rpc.close()
    e = truth["explicit"]
    c = Client()
    try:
        rpc_login(svc, e["method"], creds, username_field=e["username_field"])(c, "ada")
        rows["explicit"] = c.rpc.sent
        if c.rpc.sent != e["sent"]:
            bad.append(f"explicit: {c.rpc.sent} != {e['sent']}")
    finally:
        c.rpc.close()
    return p.done(not bad and _dev_hash_ok(), {"methods": rows, "disagreements": len(bad)}, bad)


EMIT4 = ("wiring4", "authwire4", "codes4", "tables4")


def cmd_emit_det4(kind: str, root: str, scratch: str) -> None:
    """Static artifacts of every case of one round-4 family (``root`` is the family directory)."""
    from lattice.importer import workflows as wf
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    fe = GoGrpcFrontEnd()
    rootp = Path(root)
    texts: dict[str, str] = {"extract": "", "model": "", "plans": ""}
    for c in sorted(d for d in rootp.iterdir() if d.is_dir()):
        parent, pkg = rootp, c.name
        ex = fe.extract_json(parent, pkg)
        texts["extract"] += json.dumps(ex, sort_keys=True)
        texts["model"] += fe.extract(parent, pkg).to_json()
        if "grpc" in ex and ex["grpc"]["services"]:
            texts["plans"] += json.dumps(q2._plan_view(wf.static_rpc_plans(wf.rpc_views(ex))))
    print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in texts.items()}))
    print(json.dumps(texts), file=sys.stderr)


def p4_det(ctx: dict[str, Any]) -> Probe:
    p = Probe("P4-DET", "identical (p)", "10/10")
    scratch = Path(ctx["scratch"])
    me, q2file = str(Path(__file__).resolve()), str(HERE / "grpc_probes2.py")
    plan: list[tuple[str, Any, list[Path]]] = [
        (
            "notes",
            lambda r: [sys.executable, q2file, "emit-det", "n", str(r), str(scratch)],
            [FIXTURES, p3._copy_root(FIXTURES, scratch, "n")],
        ),
    ]
    for fam in EMIT4:
        plan.append(
            (
                fam,
                lambda r, fam=fam: [sys.executable, me, "emit-det4", fam, str(r), str(scratch)],
                [FIXTURES / fam, p3._copy_root(FIXTURES / fam, scratch, fam)],
            )
        )
    if ctx.get("deskd"):
        d = Path(ctx["deskd"]).resolve().parent
        plan.append(
            (
                "deskd",
                lambda r: [sys.executable, q2file, "emit-det", "desk", str(r), str(scratch)],
                [d, p3._copy_root(d, scratch, "desk")],
            )
        )
    if ctx.get("kiln"):
        k = Path(ctx["kiln"])
        plan.append(
            (
                "kilnworks",
                lambda r: [sys.executable, q2file, "emit-det", "kiln", str(r), str(scratch)],
                [k, p3._copy_root(k, scratch, "kiln")],
            )
        )
    ok, rows = True, {}
    for label, argv_for, roots in plan:
        res = p3._det_runs(label, argv_for, roots, scratch)
        rows[label] = {k: v for k, v in res.items() if k != "errors"}
        ok = ok and res["ok"]
    if ctx.get("harbor"):
        h = Path(ctx["harbor"]) / "harborline"
        pr = p3.det_row(h, scratch, "P4-HARBOR", "harborline")
        rows["harborline"] = pr.observed
        ok = ok and pr.status == "PASS"
    rows["roots"] = 2
    for k in ("deskd", "kiln", "harbor"):
        if not ctx.get(k):
            rows[k] = f"NOT RUN: no --{k}"
    return p.done(ok, rows)


def cmd_nw_work4(kind: str, deskd: str, kiln: str, harbor: str, scratch: str) -> None:
    sp = Path(scratch)
    if kind == "fx4":
        from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

        fe = GoGrpcFrontEnd()
        for fam in ("wiring4", "authwire4", "codes4"):
            for c in sorted((FIXTURES / fam).iterdir()):
                if c.is_dir():
                    fe.extract_json(FIXTURES / fam, c.name)
        for c in TABLE_CASES:
            fe.extract_json(FIXTURES / "tables4", c)
        return
    if kind == "harborline":
        app = load_dev_app(Path(harbor) / "harborline", sp)
        for _ in range(3):
            q2.reconcile_of(app)
        for _ in range(3):
            q2.flows_of(app)
        return
    p3.cmd_nw_work(kind, deskd, kiln, scratch)


def p4_nw(ctx: dict[str, Any]) -> Probe:
    p = Probe("P4-NW", "0 changed, 0 new, 0 calls", "all 0")
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
        "fx4": FIXTURES,
    }
    kinds = ["N", "sessions", "fx4"]
    if ctx.get("deskd"):
        roots["deskd"] = Path(ctx["deskd"]).resolve()
        kinds.append("deskd")
    if ctx.get("kiln"):
        roots["kilnworks"] = Path(ctx["kiln"]) / p3.KILN_PKG
        kinds.append("kilnworks")
    if ctx.get("harbor"):
        roots["harborline"] = Path(ctx["harbor"]) / "harborline"
        kinds.append("harborline")
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
                "nw-work4",
                kind,
                str(ctx.get("deskd") or ""),
                str(ctx.get("kiln") or ""),
                str(ctx.get("harbor") or ""),
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
            "roots": [*roots, "heldout4: NOT RUN (the gated stage; this node never looked for it)"],
            "changed": len(changed),
            "new": len(new),
            "claude calls": calls,
        },
        changed + new,
    )


def p4_unit() -> Probe:
    p = Probe(
        "P4-UNIT", "40-90 tests green (round 3 predicted 25-45 and wrote 73)", "all green, >= 40"
    )
    r = r1.run_pytest(
        [f"tests/grpc/test_{n}4.py" for n in ("wiring", "auth", "codes", "tables", "login")]
    )
    n = r["counts"].get("passed", 0)
    return p.done(
        r["returncode"] == 0 and n >= 40 and not r["failing"],
        {**r["counts"], "seconds": r["seconds"], "in 40-90 (p)": 40 <= n <= 90},
        r["failing"],
    )


def p4_score() -> Probe:
    p = Probe("P4-SCORE", "30-45 cases (p)", "100%, >= 24")
    path = HERE / "grpc_scorer4_cases.json"
    n = len(json.loads(path.read_text())["cases"])
    bad = sc4.run_cases(path)
    return p.done(
        not bad and n >= 24, {"cases": n, "agree": n - len(bad), "in 30-45 (p)": 30 <= n <= 45}, bad
    )


def p4_reg(ctx: dict[str, Any]) -> Probe:
    pr = q2.p2_reg(ctx)
    pr.id = "REG"
    return pr


# ---- the held-out process (section 6.4) -----------------------------------------------------------


class RunnerError(Exception):
    """A problem of this runner (or of the held-out layout): raised BEFORE any metric exists."""


def amendment(text: str, n: int) -> str:
    m = re.search(rf"^## Amendment {n}\b.*?(?=^## Amendment \d|\Z)", text, re.M | re.S)
    return m.group(0) if m else ""


def h4_recorded(text: str) -> dict[str, dict[str, str]]:
    return {f"a{n}": sc4.recorded_hashes(amendment(text, n)) for n in (2, 3, 4)}


def preflight(heldout: Path, freeze4: Path, prereg: Path, repo: Path) -> dict[str, Any]:
    """The runner's own sha256 (never special-cased), the scorer's, ``freeze4.sha256`` and the
    held-out listing, against the amendments. Any problem refuses the run before any metric."""
    text = prereg.read_text()
    rec = h4_recorded(text)
    own = sha(Path(__file__).resolve().read_bytes())
    scorer = sha((repo / "dogfood/grpc_scorer4.py").read_bytes())
    fz = heldout / "freeze.sha256"
    out: dict[str, Any] = {"runner_sha256": own, "scorer_sha256": scorer, "problems": []}
    prob = out["problems"]
    runners = {h for a in ("a2", "a4") if (h := rec[a].get("runner"))}
    scorers = {h for a in ("a2", "a4") if (h := rec[a].get("scorer"))}
    if own not in runners:
        prob.append("runner_hash_not_recorded")
    if scorer not in scorers:
        prob.append("scorer_hash_not_recorded")
    contract = repo / "dogfood/grpc_contract4.py"
    if rec["a2"].get("contract") and (
        not contract.is_file() or sha(contract.read_bytes()) != rec["a2"]["contract"]
    ):
        prob.append("contract_hash_differs")
    out["freeze4_sha256"] = sha(freeze4.read_bytes()) if freeze4.is_file() else ""
    if out["freeze4_sha256"] != rec["a2"].get("freeze4"):
        prob.append("freeze4_hash_not_recorded")
    listed = (
        {
            ln.partition("  ")[2].strip(): ln.partition("  ")[0]
            for ln in freeze4.read_text().splitlines()
            if ln.strip()
        }
        if freeze4.is_file()
        else {}
    )
    if listed.get(RUNNER) != own and own not in {rec["a4"].get("runner")}:
        prob.append(
            "own_entry_missing_or_differs_in_freeze4"
        )  # the runner's own entry is checked like any other
    exempt = {f"changed {RUNNER}"} if own == rec["a4"].get("runner") else set()
    prob += (
        [f"freeze4: {x}" for x in r1._check_sha_file(freeze4, repo) if x not in exempt]
        if freeze4.is_file()
        else ["freeze4_missing"]
    )
    out["heldout_freeze_sha256"] = sha(fz.read_bytes()) if fz.is_file() else ""
    if not fz.is_file():
        prob.append("heldout_freeze_missing")
    else:
        if out["heldout_freeze_sha256"] != rec["a3"].get("heldout"):
            prob.append("heldout_freeze_hash_not_recorded")
        prob += [f"heldout: {x}" for x in r1._check_sha_file(fz, heldout)]
        out["heldout_files"] = sum(1 for ln in fz.read_text().splitlines() if ln.strip())
    out["staged"] = (
        sorted(git("diff", "--name-only", "--cached", cwd=repo).split())
        if (repo / ".git").exists()
        else []
    )
    if out["staged"]:
        prob.append("something is staged")
    out["recorded"] = rec
    out["ok"] = not prob
    return out


def ledger_open(out: Path, runner_sha: str) -> tuple[int, dict[str, Any], Path]:
    """The run index and the previous run, from ``h4-runs.json`` in ``out``; refuses a third run, and a
    second one unless the first crashed before any metric."""
    path = out / "h4-runs.json"
    runs = json.loads(path.read_text()) if path.is_file() else []
    if len(runs) >= 2:
        raise RunnerError(
            "a third run is refused: one re-run is permitted, after a crash before any metric"
        )
    if runs and runs[-1]["status"] == "scored":
        raise RunnerError(
            "a re-run is permitted only after a crash before any metric; the first run scored"
        )
    prior = {"crashed_before_metric": True, "runner": runs[-1]["runner"]} if runs else {}
    runs.append({"index": len(runs) + 1, "runner": runner_sha, "status": "started"})
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs, indent=1) + "\n")
    return len(runs), prior, path


def ledger_close(path: Path, status: str) -> None:
    runs = json.loads(path.read_text())
    runs[-1]["status"] = status
    path.write_text(json.dumps(runs, indent=1) + "\n")


# ---- the rows both the dev apps and the held-out are scored by -------------------------------------


def rpc_rows4(app: q2.App, label: str) -> list[Probe]:
    """RPC, SAFE, CODES, AUTH, TABLES."""
    base = p3.rpc_rows(app, label)  # RPC, CODES, AUTH, TABLES by round 3's rules
    by = {pr.id.removeprefix(f"{label}-"): pr for pr in base}
    got = {
        f"{s['name']}/{r['name']}": r for s in app.extract["grpc"]["services"] for r in s["rpcs"]
    }
    truth = app.truth["rpcs"]
    s8 = sc4.score_safe(
        {k: {"state": v["state"], "auth": v.get("auth", "")} for k, v in truth.items()},
        {k: {"state": r.get("state"), "auth": r.get("auth")} for k, r in got.items()},
    )
    safe = Probe(f"{label}-SAFE", PRED["HO4-SAFE"], RULE["HO4-SAFE"]).done(
        s8["pass"],
        {"confident-wrong": s8["count"], "checked": s8["checked"]},
        {"wrong": s8["wrong"]},
    )
    s9 = sc4.score_codes(
        {k: {"state": v["state"], "codes": v.get("codes", [])} for k, v in truth.items()},
        {
            k: {
                "state": r.get("state"),
                "codes": [
                    c["name"] for c in r.get("codes", []) if c["kind"] != "unimplemented_stub"
                ],
                "unread": any(u["what"] == "grpc_code_not_literal" for u in r.get("unread", [])),
            }
            for k, r in got.items()
        },
    )
    codes = Probe(f"{label}-CODES", PRED["HO4-CODES"], RULE["HO4-CODES"]).done(
        s9["pass"],
        {
            k: s9[k]
            for k in (
                "recall",
                "precision",
                "truth",
                "extracted",
                "matched",
                "unknown_extras",
                "unread_rpcs",
            )
        },
        {"missed": s9["missed"], "extra": s9["extra"]},
    )
    tables = by["TABLES"]
    tables.observed = {**tables.observed, "tables_source": app.extract.get("tables_source", "go")}
    for pr, key in ((by["RPC"], "RPC"), (by["AUTH"], "AUTH"), (tables, "TABLES")):
        pr.prediction, pr.rule = PRED[f"HO4-{key}"], RULE[f"HO4-{key}"]
    return [by["RPC"], safe, codes, by["AUTH"], tables]


def rec_row4(app: q2.App, label: str, ctx: dict[str, Any]) -> Probe:
    pr = p3.rec_row(app, label, ctx)
    got = ctx["rec"]
    truth = app.truth["rpcs"]
    failed = sorted(
        k
        for k, v in got.items()
        if isinstance(v, dict)
        and v.get("reason") == "login_failed"
        and truth.get(k, {}).get("state") == "implemented"
        and truth[k].get("auth") != "Public"
    )
    pr.id, pr.prediction, pr.rule = f"{label}-REC", PRED["HO4-REC"], RULE["HO4-REC"]
    if isinstance(pr.observed, dict):
        pr.observed = {**pr.observed, "login_failed on truth-implemented non-public rpcs": failed}
        if failed:
            pr.status = "FAIL"
    return pr


def flows_row4(app: q2.App, label: str, ctx: dict[str, Any]) -> Probe:
    pr = q2._h2_flows(app, ctx)
    pr.id, pr.prediction, pr.rule = f"{label}-FLOWS", PRED["HO4-FLOWS"], RULE["HO4-FLOWS"]
    return pr


def det_row4(root: Path, scratch: Path, label: str, pkg: str) -> Probe:
    pr = p3.det_row(root, scratch, label, pkg)
    pr.prediction, pr.rule = PRED["HO4-DET"], RULE["HO4-DET"]
    return pr


def load_dev_app(base: Path, scratch: Path) -> q2.App:
    """``p3.load_app`` with round 3's positional-token reader shim held over the whole load (the
    extract imports the harness too, D5.2): harborline's harness passes ``token_field`` positionally.
    The development path only -- never used on the held-out (Amendment 1, Q12)."""
    from lattice.importer.transport import AuthDriver

    orig = AuthDriver.rpc.__func__

    def tolerant(cls, service, method, credentials, *rest, **kw):  # noqa: ANN001, ANN202
        if len(rest) == 1 and "token_field" not in kw:
            kw["token_field"], rest = rest[0], ()
        return orig(cls, service, method, credentials, *rest, **kw)

    AuthDriver.rpc = classmethod(tolerant)  # type: ignore[method-assign]
    try:
        return p3.load_app(base, scratch)
    finally:
        AuthDriver.rpc = classmethod(orig)  # type: ignore[method-assign]


def dev_rows(app: q2.App, name: str, ctx: dict[str, Any]) -> list[Row]:
    """PD4-KILN / PD4-HARBOR: a development app scored by the held-out scorers. Post hoc, tuned-on:
    reported, never gated, each row beside round 3's value."""
    label = f"PD4-{name}"
    kctx: dict[str, Any] = {}
    probes = [*rpc_rows4(app, label), rec_row4(app, label, kctx), flows_row4(app, label, kctx)]
    rows = []
    for pr in probes:
        key = pr.id.removeprefix(f"{label}-")
        r = Row(f"{label}/{key} (post hoc, tuned-on, never gated)", pr.prediction, "reported only")
        rows.append(
            r.reported(
                pr.status == "PASS",
                {
                    **(pr.observed if isinstance(pr.observed, dict) else {"observed": pr.observed}),
                    "round 3 strict": R3[name].get(key),
                },
                pr.detail,
            )
        )
    ctx[f"rec_{name}"], ctx[f"flows_{name}"] = kctx.get("rec"), kctx.get("flows")
    return rows


# ---- Arm G ----------------------------------------------------------------------------------------


def pr4_all3(ctx: dict[str, Any]) -> Probe:
    """Round 3's runner, unchanged (244e5e68...), run un-nested at round-4 code; every row it passed at
    round 3 still passes (a declared byte change, if any, is listed in Amendment 1 before running)."""
    p = Probe(
        "PR3-ALL",
        "every round-3 PASS row still PASS",
        "all PASS (a declared byte change listed in Amendment 1 first)",
    )
    from_dir = ctx.get("pr3all_from")
    if not from_dir:
        return p.not_run("--pr3all-from (round 3's runner outputs, run un-nested) was not given")
    out = Path(from_dir)
    rows: dict[str, Any] = {"source": f"round 3's runner run un-nested into {out}"}
    ok = True
    for arm, name in (("0", "probes3-arm0.json"), ("G", "probes3-armg.json")):
        try:
            now = json.loads((out / arm / name).read_text())
        except OSError:
            rows[arm] = {"error": f"{out / arm / name} was not written"}
            ok = False
            continue
        was = json.loads((REPO / f"docs/results/data/2026-10-18-grpc-round3/{name}").read_text())
        was_s = {r["id"]: r["status"] for r in was["probes"]}
        now_s = {r["id"]: r["status"] for r in now["probes"]}
        regress = sorted(i for i, s in was_s.items() if s == "PASS" and now_s.get(i) != "PASS")
        rows[arm] = {
            "rows": len(now_s),
            "round-3 PASS rows still PASS": f"{sum(s == 'PASS' for s in was_s.values()) - len(regress)}/{sum(s == 'PASS' for s in was_s.values())}",
            "regressed": regress,
            "status now": now_s,
        }
        ok = ok and not regress
    return p.done(ok, rows)


def px4(ctx: dict[str, Any]) -> Probe:
    p = Probe("PX4", "0.03-2 s; 1-10 s; 5-30 s; 0.2-5 s (p)", "<= 30; <= 30; <= 180; <= 30 s")
    t = {k: ctx.get(k) for k in ("t_extract_harbor", "t_rec_desk", "t_flows_desk", "t_tables")}
    if any(v is None for v in t.values()):
        return p.not_run("harborline or deskd was not run")
    a, b, c, d = t["t_extract_harbor"], t["t_rec_desk"], t["t_flows_desk"], t["t_tables"]
    return p.done(
        a <= 30 and b <= 30 and c <= 180 and d <= 30,
        {
            "extract on harborline (s)": round(a, 2),
            "reconcile on deskd (s)": round(b, 2),
            "flows on deskd (s)": round(c, 1),
            "D5.2 on the harness fixture (s)": round(d, 2),
        },
    )


def armg(ctx: dict[str, Any]) -> list[Probe]:
    scratch = Path(ctx["scratch"])
    desk = q2.load_desk(Path(ctx["deskd"]), scratch) if ctx.get("deskd") else None
    probes: list[Probe] = [_ren(p3.pr3_n(ctx), "PR4-N")]
    probes.append(
        _ren(p3.pr3_desk(ctx, desk), "PR4-DESK")
        if desk
        else Probe("PR4-DESK", "", "").not_run("no --deskd")
    )
    probes.append(_ren(p3.pr3_sess(ctx), "PR4-SESS"))
    probes.append(_ren(p3.pf3_n(ctx), "PF4-N"))
    probes.append(
        _ren(p3.pf3_desk(ctx, desk), "PF4-DESK")
        if desk
        else Probe("PF4-DESK", "", "").not_run("no --deskd")
    )
    probes.append(_ren(p3.pf3_leak(ctx, desk), "PF4-LEAK"))
    probes.append(pr4_all3(ctx))
    t0 = time.perf_counter()
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    GoGrpcFrontEnd().extract_json(FIXTURES / "tables4", "harnessdb")
    ctx["t_tables"] = time.perf_counter() - t0
    if ctx.get("kiln"):
        kiln = p3.load_app(Path(ctx["kiln"]) / p3.KILN_PKG, scratch)
        probes += dev_rows(kiln, "KILN", ctx)
    else:
        probes.append(Probe("PD4-KILN", "", "").not_run("no --kiln"))
    if ctx.get("harbor"):
        hroot = Path(ctx["harbor"]) / "harborline"
        t0 = time.perf_counter()
        GoGrpcFrontEnd().extract_json(Path(ctx["harbor"]), "harborline")
        ctx["t_extract_harbor"] = time.perf_counter() - t0
        harbor = load_dev_app(
            hroot, scratch
        )  # the round-3 positional-token reader shim, dev path only
        probes += dev_rows(harbor, "HARBOR", ctx)
    else:
        probes.append(Probe("PD4-HARBOR", "", "").not_run("no --harbor"))
    probes.append(px4(ctx))
    return probes


def _ren(pr: Probe, pid: str) -> Probe:
    pr.id = pid
    return pr


def arm0(ctx: dict[str, Any]) -> list[Probe]:
    return [
        p4_bc(ctx),
        p4_wire(),
        p4_authi(),
        p4_codes(),
        p4_tables(),
        p4_login(),
        p4_det(ctx),
        p4_nw(ctx),
        p4_unit(),
        p4_score(),
        p4_proc(ctx),
        p4_reg(ctx),
    ]


def not_run_rows() -> list[Probe]:
    return [
        Probe("PM-S / PM-SG (Arm M)", "", "").not_run(
            "Arm M needs a Streak checkout and a Docker MySQL: out of scope; no docker daemon here"
        ),
        Probe("Arm L", "", "").not_run("the build round is its own node; out of scope"),
        Probe("PG-HO4 (HO4-*)", "", "").not_run(
            "the gated held-out stage: a new app by a new author, scored once by the orchestrator after Amendment 3; this node did not look for heldout4"
        ),
    ]


# ---- Arm H4: the gated held-out app (prereg sections 6.4, 7.3 and 12; scored ONCE) --------------------


def h4_app_dir(heldout: Path) -> Path:
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


def arm_h4(
    heldout: Path,
    freeze4: Path,
    scratch: Path,
    out: Path,
    prereg: Path,
    repo: Path,
    process_test: str | None,
) -> int:
    heldout = Path(heldout).resolve()
    pf = preflight(heldout, freeze4, prereg, repo)
    print("preflight:", json.dumps({k: pf[k] for k in ("ok", "problems")}))
    if not pf["ok"]:
        print("REFUSED: a recorded hash or a frozen file differs; nothing was scored")
        return 2
    try:
        index, prior, ledger = ledger_open(out, pf["runner_sha256"])
    except RunnerError as err:
        print(f"REFUSED: {err}")
        return 4
    rec = pf["recorded"]
    provenance = sc4.provenance(
        {
            "runner": rec["a2"].get("runner", ""),
            "scorer": rec["a2"].get("scorer", ""),
            "heldout": rec["a3"].get("heldout", ""),
        },
        {
            "runner": pf["runner_sha256"],
            "scorer": pf["scorer_sha256"],
            "heldout": pf["heldout_freeze_sha256"],
            "run_index": index,
            "prior": prior,
        },
    )
    if process_test == "crash":
        os._exit(
            137
        )  # a forced crash after the ledger entry and before any metric (--process-test only)
    os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch / "gocache"))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch / "bridgecache"))
    ctx: dict[str, Any] = {}
    probes: list[Probe] = []
    try:
        if process_test:
            probes.append(Probe("PROC-STUB", "", "").done(True, {"run_index": index}))
        else:
            root = h4_app_dir(heldout)
            before = q2._snapshot(heldout)
            app = p3.load_app(root, scratch)
            if app.runtime is None:
                raise RunnerError("harness RUNTIME is None")
            shim = scratch / "shim"
            shim.mkdir(parents=True, exist_ok=True)
            log = scratch / "claude-calls.log"
            log.write_text("")
            (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n')
            (shim / "claude").chmod(0o755)
            os.environ["PATH"] = f"{shim}:{os.environ['PATH']}"
            probes += rpc_rows4(app, "HO4")
            probes.append(rec_row4(app, "HO4", ctx))
            probes.append(flows_row4(app, "HO4", ctx))
            probes.append(det_row4(root, scratch, "HO4", root.name))
            after = q2._snapshot(heldout)
            changed = [x for x in before if before[x] != after.get(x)]
            new = [x for x in after if x not in before]
            calls = len([ln for ln in log.read_text().splitlines() if ln.strip()])
            probes.append(
                Probe("HO4-NW", PRED["HO4-NW"], RULE["HO4-NW"]).done(
                    not changed and not new and calls == 0,
                    {"changed": len(changed), "new": len(new), "claude calls": calls},
                    changed + new,
                )
            )
    except Exception as err:  # noqa: BLE001 -- a runner failure is never a score
        ledger_close(ledger, "crashed")
        print(f"RUNNER ERROR (no metric file written): {type(err).__name__}: {str(err)[:800]}")
        return 3
    rows = [
        {**pr.row(), "label": provenance["label"], "label_reasons": provenance["reasons"]}
        for pr in probes
    ]
    doc = {
        "schema": "lattice.grpc-probes4-h4/1",
        "arm": "H4",
        "preflight": pf,
        "run_index": index,
        "provenance": provenance,
        "tables_source": (ctx.get("tables_source")),
        "library_sha256": library_hashes() if repo == REPO else {},
        "probes": rows,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes4-armh4.json").write_text(json.dumps(doc, indent=1, default=str) + "\n")
    if ctx.get("rec") is not None:
        (out / "reconcile-heldout4.json").write_text(json.dumps(ctx["rec"], indent=1) + "\n")
    if ctx.get("flows") is not None:
        (out / "flows-heldout4.json").write_text(
            json.dumps(ctx["flows"], indent=1, default=str) + "\n"
        )
    ledger_close(ledger, "scored")
    for pr, row in zip(probes, rows, strict=True):
        print(
            f"{pr.status:8} {pr.id:20} [{row['label']}] {json.dumps(pr.observed, default=str)[:220]}"
        )
    failed = [pr for pr in probes if pr.status == "FAIL"]
    print(f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed")
    return 1 if failed else 0


def p4_proc(ctx: dict[str, Any] | None = None) -> Probe:
    """The held-out process, rehearsed on a scratch copy of the runner with a stand-in directory that is
    no app (``--process-test``): five scenarios of prereg 6.4."""
    p = Probe("P4-PROC", "each as stated", "5/5")
    root = Path(tempfile.mkdtemp(prefix="p4-proc-"))
    files = [
        f
        for f in os.listdir(HERE)
        if f.startswith("grpc_") and (f.endswith(".py") or f.endswith(".json"))
    ]
    results: dict[str, Any] = {}

    def setup(name: str) -> dict[str, Path]:
        d = root / name
        shutil.rmtree(d, ignore_errors=True)
        (d / "dogfood").mkdir(parents=True)
        for f in files:
            shutil.copy(HERE / f, d / "dogfood" / f)
        held = d / "ho"
        held.mkdir()
        (held / "x.txt").write_text("stand-in: not an app\n")
        (held / "freeze.sha256").write_text(f"{sha((held / 'x.txt').read_bytes())}  x.txt\n")
        return {
            "d": d,
            "held": held,
            "fz": d / "freeze4.sha256",
            "pre": d / "prereg.md",
            "out": d / "out",
        }

    def record(w: dict[str, Path], runner_a4: str | None = None, drop_own: bool = False) -> None:
        d = w["d"]
        listing = [
            f"{sha((d / f).read_bytes())}  {f}"
            for f in (
                "dogfood/grpc_probes4.py",
                "dogfood/grpc_scorer4.py",
                "dogfood/grpc_scorer4_cases.json",
            )
            if not (drop_own and f == RUNNER)
        ]
        w["fz"].write_text("\n".join(listing) + "\n")
        text = (
            "# prereg stand-in\n\n## Amendment 2\n"
            f"- dogfood/grpc_probes4.py sha256 {sha((d / RUNNER).read_bytes()) if runner_a4 is None else runner_a4}\n"
            f"- dogfood/grpc_scorer4.py sha256 {sha((d / 'dogfood/grpc_scorer4.py').read_bytes())}\n"
            f"- freeze4.sha256 sha256 {sha(w['fz'].read_bytes())}\n\n## Amendment 3\n- heldout4/freeze.sha256 {sha((w['held'] / 'freeze.sha256').read_bytes())}\n"
        )
        w["pre"].write_text(text)

    def run(w: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(w["d"] / RUNNER),
                "--arm",
                "H4",
                "--heldout",
                str(w["held"]),
                "--freeze",
                str(w["fz"]),
                "--prereg",
                str(w["pre"]),
                "--repo-root",
                str(w["d"]),
                "--out",
                str(w["out"]),
                "--scratch",
                str(w["d"] / "scr"),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=w["d"],
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )

    def labels(w: dict[str, Path]) -> set[str]:
        return {
            r["label"] for r in json.loads((w["out"] / "probes4-armh4.json").read_text())["probes"]
        }

    try:
        w = setup("s1")  # a runner byte changed after the hashes were recorded
        record(w)
        (w["d"] / RUNNER).write_text((w["d"] / RUNNER).read_text() + "\n# one byte more\n")
        r = run(w, "--process-test", "run")
        results["1 runner byte changed: refused before any metric"] = (
            r.returncode == 2
            and not (w["out"] / "probes4-armh4.json").exists()
            and not (w["out"] / "h4-runs.json").exists()
        )
        w = setup("s2")  # the runner's own entry removed from its check
        record(w, drop_own=True)
        r = run(w, "--process-test", "run")
        results["2 own entry removed from freeze4: refused"] = (
            r.returncode == 2
            and "own_entry" in r.stdout
            and not (w["out"] / "probes4-armh4.json").exists()
        )
        w = setup("s3")  # a forced crash, then a re-run with equal bytes
        record(w)
        r1_ = run(w, "--process-test", "crash")
        r2_ = run(w, "--process-test", "run")
        results["3 crash then re-run with equal bytes: blind"] = (
            r1_.returncode == 137 and r2_.returncode == 0 and labels(w) == {"blind"}
        )
        w = setup("s4")  # a crash, then a re-run with changed bytes (recorded in Amendment 4)
        record(w)
        run(w, "--process-test", "crash")
        (w["d"] / RUNNER).write_text((w["d"] / RUNNER).read_text() + "\n# a fix for the crash\n")
        record_a4 = (
            w["pre"].read_text()
            + f"\n## Amendment 4\n- dogfood/grpc_probes4.py sha256 {sha((w['d'] / RUNNER).read_bytes())}\n"
        )
        w["pre"].write_text(record_a4)
        r = run(w, "--process-test", "run")
        results["4 re-run with changed bytes: post_hoc"] = r.returncode == 0 and labels(w) == {
            "post_hoc"
        }
        r5 = run(w, "--process-test", "run")  # a third run
        results["5 a third run: refused"] = r5.returncode == 4 and "third run" in r5.stdout
    except Exception as err:  # noqa: BLE001
        results["error"] = f"{type(err).__name__}: {err}"
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return p.done(all(v is True for v in results.values()) and len(results) == 5, results)


# ---- main -----------------------------------------------------------------------------------------


def run_check(name: str, ctx: dict[str, Any]) -> int:
    table = {
        "p4-wire": p4_wire,
        "p4-authi": p4_authi,
        "p4-codes": p4_codes,
        "p4-tables": p4_tables,
        "p4-login": p4_login,
        "p4-score": p4_score,
        "p4-proc": p4_proc,
        "p4-bc-ast": lambda: p4_bc_ast(Path(ctx["base"])),
        "p4-bc-ext": lambda: p4_bc_ext(ctx),
        "pr4-sess": lambda: p3.pr3_sess(ctx),
        "pr4-n": lambda: p3.pr3_n(ctx),
        "pr4-desk": lambda: p3.pr3_desk(
            ctx, q2.load_desk(Path(ctx["deskd"]), Path(ctx["scratch"]))
        ),
    }
    pr = table[name]()
    print(f"{pr.status} {pr.id} {json.dumps(pr.observed, default=str)[:400]}")
    return 0 if pr.status == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["emit-det4"]:
        cmd_emit_det4(args_in[1], args_in[2], args_in[3])
        return 0
    if args_in[:1] == ["nw-work4"]:
        cmd_nw_work4(*args_in[1:6])
        return 0
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--arm", choices=("0", "G", "H4"))
    ap.add_argument("--check", default=None, help="run one probe by name (what the mutants run)")
    ap.add_argument("--base", type=Path, default=None, help="git archive of BASE, extracted")
    ap.add_argument(
        "--deskd", type=Path, default=None, help="development data: the deskd directory"
    )
    ap.add_argument(
        "--kiln", type=Path, default=None, help="development data: the directory holding kilnworks/"
    )
    ap.add_argument(
        "--harbor",
        type=Path,
        default=None,
        help="development data: the directory holding harborline/",
    )
    ap.add_argument("--heldout", type=Path, default=None, help="--arm H4: the held-out directory")
    ap.add_argument("--freeze", type=Path, default=None, help="--arm H4: freeze4.sha256")
    ap.add_argument(
        "--prereg", type=Path, default=PREREG, help="the file whose amendments record the hashes"
    )
    ap.add_argument("--repo-root", type=Path, default=REPO)
    ap.add_argument(
        "--process-test",
        choices=("run", "crash"),
        default=None,
        help="rehearse the process; scores nothing",
    )
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DATA)
    ap.add_argument("--reg-before", type=Path, default=None)
    ap.add_argument("--reg-after", type=Path, default=None)
    ap.add_argument(
        "--pr3all-from",
        type=Path,
        default=None,
        help="round 3's runner outputs (arm dirs 0/ and G/), run un-nested",
    )
    args = ap.parse_args(args_in)
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="grpc4-probes-"))
    scratch.mkdir(parents=True, exist_ok=True)
    if args.arm == "H4":
        if args.heldout is None or args.freeze is None:
            ap.error("--arm H4 needs --heldout and --freeze")
        return arm_h4(
            args.heldout,
            args.freeze,
            scratch,
            args.out,
            args.prereg,
            args.repo_root,
            args.process_test,
        )
    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    ctx: dict[str, Any] = {
        "scratch": scratch,
        "base": args.base,
        "deskd": args.deskd,
        "kiln": args.kiln,
        "harbor": args.harbor,
        "reg_before": args.reg_before,
        "reg_after": args.reg_after,
        "pr3all_from": args.pr3all_from,
    }
    if args.check:
        return run_check(args.check, ctx)
    if args.arm is None:
        ap.error("--arm or --check is required")
    args.out.mkdir(parents=True, exist_ok=True)
    before = frozen_check(args.deskd, args.kiln, args.harbor)
    probes = arm0(ctx) if args.arm == "0" else armg(ctx)
    after = frozen_check(args.deskd, args.kiln, args.harbor)
    probes += not_run_rows()
    doc = {
        "schema": "lattice.grpc-probes4/1",
        "arm": args.arm,
        "library_sha256": library_hashes(),
        "frozen_before": before,
        "frozen_after": after,
        "probes": [pr.row() for pr in probes],
    }
    name = {"0": "probes4-arm0.json", "G": "probes4-armg.json"}[args.arm]
    (args.out / name).write_text(json.dumps(doc, indent=1, default=str) + "\n")
    for key, fname in (
        ("rec_desk", "reconcile-deskd.json"),
        ("flows_desk", "flows-deskd.json"),
        ("flows_n", "flows-n.json"),
        ("rec_KILN", "reconcile-kilnworks.json"),
        ("flows_KILN", "flows-kilnworks.json"),
        ("rec_HARBOR", "reconcile-harborline.json"),
        ("flows_HARBOR", "flows-harborline.json"),
    ):
        if ctx.get(key) is not None:
            (args.out / fname).write_text(json.dumps(ctx[key], indent=1, default=str) + "\n")
    for pr in probes:
        print(f"{pr.status:8} {pr.id:44} {json.dumps(pr.observed, default=str)[:200]}")
    print("frozen files ok before:", before["ok"], "after:", after["ok"])
    failed = [pr for pr in probes if pr.status == "FAIL"]
    print(
        f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed, {sum(pr.status == 'NOT RUN' for pr in probes)} not run, {sum(pr.status == 'REPORTED' for pr in probes)} reported"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
