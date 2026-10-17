"""Probes of gRPC round 2 (docs/results/2026-10-17-grpc-round2-prereg.md, with Amendment 1):
state-independent reconcile (RC1-RC3), rpc flows that verify (F1-F6, ``lattice.rpc-flows/2``) and
the vacuity-aware flows scorer (``dogfood/grpc_flow_scorer.py``).

Arms 0 (static) and G (Go servers over pure-Go SQLite, no Docker) are the development stage.
``deskd`` -- seen by round 1's author, by round 1's scorer and by this round's author -- is
**development data**, never a blind or held-out claim. Arm M (Streak over MySQL) and Arm L (the
build round) are reported ``NOT RUN``. ``--arm H2`` is the gated held-out stage (PG-HO2: a new app by
a new agent, scored ONCE by the orchestrator after the freeze, section H2 below); it refuses to
score when a frozen file differs, and writes no metric file when the runner itself errors.

    export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree>
    uv run python dogfood/grpc_probes2.py --arm 0 --base "$SCRATCH/base" --scratch "$SCRATCH/p0" \\
        --out docs/results/data/2026-10-17-grpc-round2 --reg-before ... --reg-after ...
    uv run python dogfood/grpc_probes2.py --arm G --base "$SCRATCH/base" --base6a "$SCRATCH/base6a" \\
        --deskd "$SCRATCH/../heldout/deskd" --scratch "$SCRATCH/pg" --out docs/results/data/...
    uv run python dogfood/grpc_probes2.py --arm H2 --heldout "$SCRATCH/heldout2" --out .../heldout2

Internal sub-commands: ``emit-det``, ``emit-rec`` (run in a subprocess, on this tree or the baseline
tree) and ``check <name>`` (one probe, exit 0 on PASS: what ``grpc_mutants2.py`` runs).
"""

# ruff: noqa: E501 -- probe texts and table rows are data lines
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO))  # after PYTHONPATH: a baseline tree's own library wins when asked for

import grpc_flow_scorer as scorer  # noqa: E402
import grpc_probes as r1  # noqa: E402  -- round 1's runner: Probe, sha, run_pytest, emit-bc

Probe = r1.Probe
sha = r1.sha
FIXTURES = REPO / "tests" / "grpc" / "fixtures"
OUT_DEFAULT = REPO / "docs/results/data/2026-10-17-grpc-round2"
ROUND1_FREEZE = REPO / "docs/results/data/2026-10-17-grpc/freeze-final.sha256"
DESKD_FREEZE_SHA = "8670bf84cdcc9175797507f05e55e62f11716659dc9a0e7a6368aa14dab729f6"
N_TRUTH_SHA = "debbd0b4e61297ca94ab1cbd0d7835efc99d67c4b75d6012243ddeb07b22f490"
GRPC_ALLOWED = {"reconcile", "reconcile_with", "_call_fresh", "_with_protos", "run_rpc_flows"}
MARKER = "# ---- rpc flows (Spike I, D6"
ALLOWED_EDITS = {"lattice/spec/grpc.py", "lattice/importer/workflows.py"}
ALLOWED_NEW = {
    "tests/grpc/test_reconcile2.py",
    "tests/grpc/test_flows2.py",
    "tests/grpc/fixtures/sessions",
    "tests/grpc/fixtures/deskd_links.json",
    "dogfood/grpc_probes2.py",
    "dogfood/grpc_mutants2.py",
    "dogfood/grpc_flow_scorer.py",
    "dogfood/grpc_flow_scorer_cases.json",
    "docs/results/2026-10-17-grpc-round2-results.md",
    "docs/results/data/2026-10-17-grpc-round2",
}


# ---- the frozen files ------------------------------------------------------------------------


def frozen_check(deskd: Path | None) -> dict[str, Any]:
    """Round 1's frozen listing (every file but ``lattice/spec/grpc.py``, which round 2 changes),
    N's truth, ``deskd``'s listing, and the set of files changed against ``HEAD``."""
    out: dict[str, Any] = {}
    listing = ROUND1_FREEZE.read_text().splitlines()
    bad = []
    for line in listing:
        digest, _, rel = line.partition("  ")
        if rel.strip() == "lattice/spec/grpc.py":
            continue
        f = REPO / rel.strip()
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            bad.append(rel.strip())
    out["round1_frozen_files_checked"] = len(listing) - 1
    out["round1_frozen_files_changed"] = bad
    truth = FIXTURES / "notes" / "truth.json"
    out["N_truth_sha256_ok"] = sha(truth.read_bytes()) == N_TRUTH_SHA
    if deskd is not None:
        parent = deskd.resolve().parent
        fz = parent / "freeze.sha256"
        out["deskd_freeze_sha256_ok"] = sha(fz.read_bytes()) == DESKD_FREEZE_SHA
        out["deskd_files_changed"] = r1._check_sha_file(fz, parent)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    out["tracked_files_changed"] = sorted(changed)
    out["tracked_unexpected"] = sorted(set(changed) - ALLOWED_EDITS)
    out["untracked_unexpected"] = sorted(
        u for u in untracked if not any(u == a or u.startswith(a + "/") for a in ALLOWED_NEW)
    )
    out["ok"] = (
        not bad
        and out["N_truth_sha256_ok"]
        and not out["tracked_unexpected"]
        and not out["untracked_unexpected"]
        and out.get("deskd_freeze_sha256_ok", True)
        and not out.get("deskd_files_changed")
    )
    return out


# ---- the apps ---------------------------------------------------------------------------------


class App:
    """An app of the round: what the library needs to reconcile it and run its flows."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def load_n() -> App:
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from tests.grpc import oracle
    from tests.grpc.fixtures.notes import harness
    from tests.grpc.fixtures.notes import spec as dev

    fe = GoGrpcFrontEnd()
    return App(
        name="N",
        root=oracle.NOTES,
        fixtures=oracle.FIXTURES,
        pkg="notes",
        fe=fe,
        extract=fe.extract_json(oracle.FIXTURES, "notes"),
        model=fe.extract(oracle.FIXTURES, "notes"),
        make=harness.make_app,
        build_db=harness.build_db,
        runtime=harness.RUNTIME,
        users=harness.USERS,
        spec=dev.SPEC,
        harness=harness,
    )


def load_sessions() -> App:
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from tests.grpc import oracle
    from tests.grpc.fixtures.sessions import harness

    fe = GoGrpcFrontEnd()
    return App(
        name="sessions",
        root=oracle.FIXTURES / "sessions",
        fixtures=oracle.FIXTURES,
        pkg="sessions",
        fe=fe,
        extract=fe.extract_json(oracle.FIXTURES, "sessions"),
        model=fe.extract(oracle.FIXTURES, "sessions"),
        make=harness.make_app,
        build_db=harness.build_db,
        runtime=harness.RUNTIME,
        users=harness.USERS,
        harness=harness,
    )


def load_desk(deskd: Path, scratch: Path) -> App:
    """``deskd`` (development data) through its own harness; the contract's protos named by
    absolute path so the library, not a runner copy, reconciles it."""
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from lattice.profile import ApiProfile, Profile
    from lattice.spec import Spec

    parent = deskd.resolve().parent
    h = r1._load_harness(parent, scratch)
    base = parent / "deskd"
    fe = GoGrpcFrontEnd()
    protos = tuple(str(base / p) for p in h.PROTOS)
    paths = tuple(str(base / p) for p in h.PROTO_PATHS)
    profile = Profile(
        schemas={},
        api=ApiProfile(
            tables={}, table_names={}, roles=frozenset({"Public", "Member"}), error_body="fastapi"
        ),
    )
    return App(
        name="deskd",
        root=base,
        fixtures=parent,
        pkg="deskd",
        fe=fe,
        extract=fe.extract_json(parent, "deskd"),
        model=fe.extract(parent, "deskd"),
        make=r1._ho_make_app(h, base),
        raw_make=h.make_app,
        build_db=h.build_db,
        runtime=h.RUNTIME,
        users=h.USERS,
        protos=protos,
        proto_paths=paths,
        spec=Spec(profile=profile, users=h.USERS, protos=protos),
        harness=h,
        truth=r1.heldout_truth(parent),
    )


def reconcile_of(app: App) -> dict[str, Any]:
    from lattice.spec.grpc import reconcile_with

    return reconcile_with(
        app.model,
        make_app=getattr(app, "raw_make", app.make),
        build_db=app.build_db,
        runtime=app.runtime,
        users=app.users,
        protos=getattr(app, "protos", ()),
        proto_paths=getattr(app, "proto_paths", ()),
    )


def flows_of(app: App) -> list[dict[str, Any]]:
    from lattice.spec.grpc import run_rpc_flows

    return run_rpc_flows(app.extract, app.spec, app.make, app.build_db, runtime=app.runtime)


def replay_failures(app: App, flows: list[dict[str, Any]], times: int = 1) -> list[list[str]]:
    """The scenario names that fail layer D, once per replay, when the ``flows`` are replayed."""
    from lattice.spec import check
    from lattice.spec.grpc import _scenario_of

    scen = tuple(_scenario_of(f["id"], f["steps"]) for f in flows)
    out = []
    for _ in range(times):
        rep = check(
            replace(app.spec, scenarios=scen),
            app.make,
            app.build_db,
            layers="D",
            runtime=app.runtime,
        )
        out.append(sorted({f.rule.removeprefix("scenario: ") for f in rep.findings}))
    return out


# ---- internal sub-commands: a subprocess on this tree or on the baseline tree ---------------------


def _plan_view(plans: list[Any]) -> list[Any]:
    return [
        {
            "id": p.id,
            "kind": p.kind,
            "unbound": p.unbound,
            "steps": [
                [s.key, s.caller, sorted(s.binds.items()), s.intent, s.role, sorted(s.nat.items())]
                for s in p.steps
            ],
        }
        for p in plans
    ]


def cmd_emit_det(kind: str, root: str, scratch: str) -> None:
    """Plans, flows (messages, codes, bindings, statuses) and reconcile output of one app, hashed;
    the texts go to stderr for the leak scan. ``root`` is the directory holding the app."""
    from lattice.importer import workflows as wf
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    rootp = Path(root)
    if kind == "n":
        app = load_n()
        fe = GoGrpcFrontEnd()
        extract = fe.extract_json(rootp, "notes")  # the copy: its path differs from the repo's
        model = fe.extract(rootp, "notes")
        app.extract, app.model = extract, model
        app.root = rootp / "notes"
    elif kind == "kiln":
        app = load_kiln(rootp / H2_PKG, Path(scratch), truth=False)
    else:
        os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(Path(scratch) / "hbuild"))
        app = load_desk(rootp / "deskd", Path(scratch))
    texts: dict[str, str] = {}
    texts["extract"] = json.dumps(app.extract, sort_keys=True)
    texts["plans"] = json.dumps(_plan_view(wf.static_rpc_plans(wf.rpc_views(app.extract))))
    texts["flows"] = json.dumps(flows_of(app), sort_keys=True)
    texts["reconcile"] = json.dumps(
        app.fe.reconcile(app.root, app.model) if kind == "n" else reconcile_of(app)
    )
    if kind == "kiln":
        texts["model"] = app.model.to_json()
        texts["describe"] = _describe_text(app)
    print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in texts.items()}))
    print(json.dumps(texts), file=sys.stderr)


def cmd_emit_rec(kind: str, scratch: str) -> None:
    """The reconcile output of ``N`` or ``sessions`` by the library of the tree it runs on (the
    baseline tree has no ``tests/grpc/fixtures/sessions``: the fixtures are this tree's)."""
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    pkg = "notes" if kind == "n" else "sessions"
    fe = GoGrpcFrontEnd()
    model = fe.extract(FIXTURES, pkg)
    print(json.dumps(fe.reconcile(FIXTURES / pkg, model)))


# ---- Arm 0 --------------------------------------------------------------------------------------


def _defs(src: str, below: int | None = None) -> dict[str, str]:
    """``{name or dump: ast.dump}`` of the module's top-level statements (those ending before
    line ``below`` when given)."""
    out: dict[str, str] = {}
    for node in ast.parse(src).body:
        if below is not None and getattr(node, "end_lineno", 0) >= below:
            continue
        name = getattr(node, "name", None)
        out[name or ast.dump(node)] = ast.dump(node)
    return out


def p2_bc_ast(base: Path) -> Probe:
    """The AST half of the byte-compat promise: every top-level definition of ``lattice/spec/grpc.py``
    but the five round 2 may change, and the whole HTTP section of ``workflows.py``, is identical."""
    p = Probe("P2-BC-AST", "identical outside the allowed defs", "identical")
    bad: list[str] = []
    old = (base / "lattice/spec/grpc.py").read_text()
    new = (REPO / "lattice/spec/grpc.py").read_text()
    a, b = _defs(old), _defs(new)
    for k in sorted(set(a) | set(b)):
        if k in GRPC_ALLOWED:
            continue
        if a.get(k) != b.get(k):
            bad.append(f"grpc.py: {k[:60]}")
    wold = (base / "lattice/importer/workflows.py").read_text()
    wnew = (REPO / "lattice/importer/workflows.py").read_text()
    lo = next(i for i, ln in enumerate(wold.splitlines(), 1) if ln.startswith(MARKER))
    ln_ = next(i for i, ln in enumerate(wnew.splitlines(), 1) if ln.startswith(MARKER))
    ha, hb = _defs(wold, lo), _defs(wnew, ln_)
    if ha != hb:
        bad += [
            f"workflows.py (HTTP section): {k[:60]}"
            for k in sorted(set(ha) | set(hb))
            if ha.get(k) != hb.get(k)
        ]
    return p.done(
        not bad,
        {
            "grpc.py defs compared": len(a),
            "workflows.py HTTP defs compared": len(ha),
            "differences": len(bad),
        },
        bad,
    )


def p2_bc(ctx: dict[str, Any]) -> Probe:
    p = Probe("P2-BC", "all identical (p)", "all")
    base = ctx.get("base")
    if base is None:
        return p.not_run("no --base: git archive cea85fc was not given")
    scratch = Path(ctx["scratch"])
    ast_probe = p2_bc_ast(Path(base))
    # the round-1 emitter on both trees (HTTP outputs, gocheck extract, RepoModels, specs, reports)
    sub = r1.bc_probe({"base": base, "scratch": scratch})
    # gocheck extract JSON and the RepoModel of N, T and deskd, base tree vs this one
    snippet = (
        "import json,sys,hashlib\nfrom pathlib import Path\n"
        "from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd\n"
        "fe=GoGrpcFrontEnd(); out={}\n"
        "for root,pkg in json.loads(sys.argv[1]):\n"
        "    ex=fe.extract_json(Path(root),pkg); out[pkg+':extract']=hashlib.sha256(json.dumps(ex,sort_keys=True).encode()).hexdigest()\n"
        "    out[pkg+':model']=hashlib.sha256(fe.extract(Path(root),pkg).to_json().encode()).hexdigest()\n"
        "print(json.dumps(out))\n"
    )
    targets = [(str(FIXTURES), "notes"), (str(FIXTURES), "protos")]
    if ctx.get("deskd"):
        targets.append((str(Path(ctx["deskd"]).resolve().parent), "deskd"))
    outs = []
    for tree, cache in ((Path(base), "gocheck-base"), (REPO, "gocheck-new")):
        env = {
            **os.environ,
            "PYTHONPATH": str(tree),
            "LATTICE_GOCHECK_CACHE": str(scratch / cache),
            "LATTICE_GRPCBRIDGE_CACHE": str(scratch / "bridgecache"),
        }
        proc = subprocess.run(
            [sys.executable, "-c", snippet, json.dumps(targets)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tree,
            check=False,
        )
        outs.append(
            json.loads(proc.stdout.splitlines()[-1])
            if proc.returncode == 0
            else {"error": proc.stderr[-300:]}
        )
    extra_diff = sorted(k for k in set(outs[0]) | set(outs[1]) if outs[0].get(k) != outs[1].get(k))
    wf = r1.run_pytest(["tests/importer/test_workflows.py"])
    ok = (
        ast_probe.status == "PASS"
        and sub.status == "PASS"
        and not extra_diff
        and wf["returncode"] == 0
    )
    return p.done(
        ok,
        {
            "ast (grpc.py defs, workflows.py HTTP section)": ast_probe.status,
            "round-1 byte-compat emitter": sub.status,
            "round-1 emitter outputs compared": (sub.observed or {}).get("compared"),
            "extract + RepoModel of N, T, deskd": f"{len(outs[0])} compared, {len(extra_diff)} differ",
            "HTTP workflow tests": wf["counts"],
        },
        {"ast": ast_probe.detail, "emitter": sub.detail, "extra": extra_diff},
    )


def p2_det(ctx: dict[str, Any]) -> Probe:
    """Plans, messages, statuses and reconcile output on N and deskd over 10 processes (random hash
    seeds), two roots (a byte copy of each app's directory)."""
    p = Probe("P2-DET", "identical (p)", "10/10")
    scratch = Path(ctx["scratch"])
    rx_abs = re.compile(r"/(home|tmp|Users|root)/[\w./-]+")
    rx_time = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
    rx_port = re.compile(r"127\.0\.0\.1:\d+|localhost:\d+")
    rows: dict[str, Any] = {}
    kinds = [("n", FIXTURES, "notes")]
    if ctx.get("deskd"):
        kinds.append(("desk", Path(ctx["deskd"]).resolve().parent, "deskd"))
    ok = True
    detail: dict[str, Any] = {}
    for kind, src, pkg in kinds:
        roots = [src]
        copy = scratch / f"det-b-{kind}" / "x" / "y"
        shutil.rmtree(scratch / f"det-b-{kind}", ignore_errors=True)
        shutil.copytree(src, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        roots.append(copy)
        results: list[Any] = []
        leaks: list[str] = []
        for i in range(10):
            root = roots[i % 2]
            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "emit-det",
                    kind,
                    str(root),
                    str(scratch),
                ],
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
                scrubbed = text.replace(str(root), "").replace(str(src), "")
                for rx, what in ((rx_abs, "absolute path"), (rx_time, "time"), (rx_port, "port")):
                    m = rx.search(scrubbed)
                    if m and not m.group(0).startswith("/notes") and key != "extract":
                        leaks.append(f"{kind}:{key}: {what} {m.group(0)!r}")
        good = [r for r in results if isinstance(r, dict)]
        same = len({json.dumps(r, sort_keys=True) for r in good}) == 1
        rows[pkg] = {
            "identical runs": len(good) if same else 0,
            "of": 10,
            "leaks": sorted(set(leaks))[:3],
        }
        ok = ok and len(good) == 10 and same and not leaks
        detail[pkg] = [r for r in results if not isinstance(r, dict)]
        if good:
            rows[pkg]["artifacts"] = sorted(good[0])
    rows["roots"] = 2
    if not ctx.get("deskd"):
        rows["deskd"] = "NOT RUN: no --deskd"
    return p.done(ok, rows, detail)


def _snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    snap = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files + dirs):
            path = Path(dirpath) / name
            st = path.lstat()
            digest = sha(path.read_bytes()) if path.is_file() else "dir"
            snap[str(path.relative_to(root))] = (digest, st.st_mode, st.st_mtime_ns)
    return snap


def p2_nw(ctx: dict[str, Any]) -> Probe:
    p = Probe("P2-NW", "0 changed, 0 new, 0 calls", "all 0")
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
    }
    if ctx.get("deskd"):
        roots["deskd"] = Path(ctx["deskd"]).resolve()
    before = {k: _snapshot(r) for k, r in roots.items()}
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\nimport grpc_probes2 as q\n"
        "kind, deskd, scratch = sys.argv[2], sys.argv[3], Path(sys.argv[4])\n"
        "app = q.load_n() if kind == 'N' else q.load_sessions() if kind == 'sessions' else q.load_desk(Path(deskd), scratch)\n"
        "for _ in range(3): q.reconcile_of(app)\n"
        "if kind in ('N', 'deskd'):\n"
        "    for _ in range(3): q.flows_of(app)\n"
    )
    env = {
        **os.environ,
        "PATH": f"{shim}:{os.environ['PATH']}",
        "PYTHONPATH": str(REPO),
        "LATTICE_HELDOUT_BUILD": str(scratch / "hbuild"),
    }
    for kind in ("N", "sessions", *(["deskd"] if ctx.get("deskd") else [])):
        subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(HERE),
                kind,
                str(ctx.get("deskd") or ""),
                str(scratch),
            ],
            check=True,
            env=env,
            cwd=REPO,
            capture_output=True,
        )
    after = {k: _snapshot(r) for k, r in roots.items()}
    changed = [f"{k}:{x}" for k in roots for x in before[k] if before[k].get(x) != after[k].get(x)]
    new = [f"{k}:{x}" for k in roots for x in after[k] if x not in before[k]]
    calls = len([ln for ln in log.read_text().splitlines() if ln.strip()])
    return p.done(
        not changed and not new and calls == 0,
        {
            "roots": [*roots, "heldout2: NOT RUN (the gated stage; not given to this node)"],
            "reconcile x3, flows x3": True,
            "changed": len(changed),
            "new": len(new),
            "claude calls": calls,
        },
        changed + new,
    )


def p2_unit() -> Probe:
    p = Probe("P2-UNIT", ">= 30 tests green (p: 30-45)", "all green")
    r = r1.run_pytest(["tests/grpc/test_reconcile2.py", "tests/grpc/test_flows2.py"])
    n = r["counts"].get("passed", 0)
    return p.done(
        r["returncode"] == 0 and n >= 30 and not r["failing"],
        {**r["counts"], "seconds": r["seconds"]},
        r["failing"],
    )


def p2_score() -> Probe:
    p = Probe("P2-SCORE", ">= 30 cases (p: 32-40)", "100%")
    path = HERE / "grpc_flow_scorer_cases.json"
    n = len(json.loads(path.read_text())["cases"])
    bad = scorer.run_cases(path)
    return p.done(not bad and n >= 30, {"cases": n, "agree": n - len(bad)}, bad)


def p2_reg(ctx: dict[str, Any]) -> Probe:
    p = Probe("REG", "the same failing ids before and after", "same set")
    b, a = ctx.get("reg_before"), ctx.get("reg_after")
    if not (b and a):
        return p.not_run("--reg-before / --reg-after (pytest logs) were not given")

    def ids(path: Path) -> tuple[set[str], str]:
        text = Path(path).read_text()
        last = text.strip().splitlines()[-1] if text.strip() else ""
        return set(re.findall(r"^(?:FAILED|ERROR) (\S+)", text, re.M)), last

    ib, sb = ids(b)
    ia, sa = ids(a)
    return p.done(
        ib == ia and bool(ib),
        {"before": sb, "after": sa, "failing ids before": len(ib), "failing ids after": len(ia)},
        {"only before": sorted(ib - ia), "only after": sorted(ia - ib)},
    )


def arm0(ctx: dict[str, Any]) -> list[Probe]:
    deskd = Path(ctx["deskd"]) if ctx.get("deskd") else None
    probes = [p2_bc(ctx), p2_det(ctx), p2_nw(ctx), p2_unit(), p2_score(), p2_reg(ctx)]
    fz = frozen_check(deskd)
    ctx["frozen"] = fz
    return probes


# ---- Arm G --------------------------------------------------------------------------------------


def _classes(app: App, got: dict[str, Any]) -> dict[str, list[tuple[str, Any]]]:
    out: dict[str, list[tuple[str, Any]]] = {"implemented": [], "unimplemented": [], "absent": []}
    states = {
        f"{s['name']}/{r['name']}": r.get("state")
        for s in app.extract["grpc"]["services"]
        for r in s["rpcs"]
    }
    for k, v in got.items():
        if k in ("order", "auth_refused"):
            continue
        if k == "(absent)":
            out["absent"].append((k, v["code"]))
        else:
            out["implemented" if states.get(k) == "implemented" else "unimplemented"].append(
                (k, v["code"])
            )
    return out


def pr2_n(ctx: dict[str, Any]) -> Probe:
    p = Probe("PR2-N", "identical to round 1: 10/10 answered; bytes equal", "exact")
    base = ctx.get("base")
    app = load_n()
    got = app.fe.reconcile(app.root, app.model)
    new_text = json.dumps(got)
    cls = _classes(app, got)
    implemented_ok = all(v not in (None, "UNIMPLEMENTED") for _, v in cls["implemented"])
    shape = len(cls["implemented"]) == 10 and implemented_ok
    shape = shape and got["notes.v1.Notes/ShareNote"]["code"] == "UNIMPLEMENTED"
    shape = shape and got["(absent)"]["code"] == "UNIMPLEMENTED"
    same: Any = "NOT RUN: no --base"
    if base is not None:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "emit-rec", "n", str(ctx["scratch"])],
            capture_output=True,
            text=True,
            check=False,
            cwd=base,
            env={**os.environ, "PYTHONPATH": str(base)},
        )
        same = (
            proc.stdout.strip().splitlines()[-1] == new_text
            if proc.returncode == 0
            else proc.stderr[-300:]
        )
    return p.done(
        shape and same is True,
        {
            "implemented answered": f"{sum(v not in (None, 'UNIMPLEMENTED') for _, v in cls['implemented'])}/{len(cls['implemented'])}",
            "bytes equal round 1": same,
            "extra keys": sorted(k for k in got if k in ("order", "auth_refused")),
        },
        got,
    )


def pr2_desk(ctx: dict[str, Any], desk: App) -> Probe:
    p = Probe(
        "PR2-DESK (dev)",
        "implemented 17/17 answered, no auth_refused; MergeTickets, Backlog 1/1 UNIMPLEMENTED; absent 1/1",
        "100%",
    )
    t0 = time.perf_counter()
    got = reconcile_of(desk)
    ctx["t_rec_desk"] = time.perf_counter() - t0
    ctx["rec_desk"] = got
    cls = _classes(desk, got)
    impl = [(k, c) for k, c in cls["implemented"]]
    answered = [k for k, c in impl if c not in (None, "UNIMPLEMENTED")]
    unimpl_ok = all(c == "UNIMPLEMENTED" for _, c in cls["unimplemented"])
    absent_ok = [c for _, c in cls["absent"]] == ["UNIMPLEMENTED"]
    # not_registered rpcs are not routes of the model: the library does not call them
    backlog = None
    try:
        from lattice.spec.grpc import _call_fresh

        with tempfile.TemporaryDirectory() as tmp:
            db = desk.build_db(Path(tmp) / "b.db")
            app = desk.make(db)
            try:
                with desk.runtime.open(app) as tr:
                    first = next(iter(desk.users))
                    backlog = _call_fresh(
                        tr.rpc(), tr, desk.runtime, first, None, "desk.v1.Reports", "Backlog", False
                    )
            finally:
                desk.runtime.close(app)
    except Exception as err:  # noqa: BLE001
        backlog = {"error": f"{type(err).__name__}: {err}"}
    ok = (
        len(impl) == 17
        and len(answered) == 17
        and "auth_refused" not in got
        and len(cls["unimplemented"]) == 1
        and unimpl_ok
        and absent_ok
    )
    return p.done(
        ok,
        {
            "implemented answered": f"{len(answered)}/{len(impl)}",
            "unimplemented (model routes)": f"{sum(c == 'UNIMPLEMENTED' for _, c in cls['unimplemented'])}/{len(cls['unimplemented'])}",
            "absent": f"{sum(c == 'UNIMPLEMENTED' for _, c in cls['absent'])}/{len(cls['absent'])}",
            "auth_refused": got.get("auth_refused", []),
            "order": got.get("order", {}),
            "Reports/Backlog in the library output": "desk.v1.Reports/Backlog" in got,
            "Reports/Backlog by one supplementary _call_fresh (not library output)": backlog,
            "seconds": round(ctx["t_rec_desk"], 2),
        },
        got,
    )


def pr2_order(ctx: dict[str, Any], desk: App | None) -> Probe:
    p = Probe(
        "PR2-ORDER",
        "0 disagreements on N; 0-2 on deskd (p); >= 1 on sessions; all resolved; final = truth",
        "every disagreement resolved isolated; final codes = truth",
    )
    truth = json.loads((FIXTURES / "sessions" / "truth.json").read_text())
    n, ses = load_n(), load_sessions()
    got_n = n.fe.reconcile(n.root, n.model)
    got_s = reconcile_of(ses)
    obs: dict[str, Any] = {}
    ok = True
    obs["N"] = {"disagreed": got_n.get("order", {}).get("disagreed", [])}
    ok = ok and not obs["N"]["disagreed"]
    dis_s = got_s.get("order", {}).get("disagreed", [])
    final_ok = (
        all(got_s[k]["code"] == v["alone"] for k, v in truth["rpcs"].items())
        and got_s["(absent)"]["code"] == truth["absent"]
    )
    resolved_s = all(got_s[k].get("isolated") is True and got_s[k]["code"] for k in dis_s)
    obs["sessions"] = {
        "disagreed": dis_s,
        "all isolated": resolved_s,
        "final codes equal the truth (alone)": final_ok,
        "login_failed": sorted(
            k for k, v in got_s.items() if isinstance(v, dict) and v.get("reason")
        ),
    }
    ok = ok and bool(dis_s) and resolved_s and final_ok
    if desk is not None:
        got_d = ctx.get("rec_desk") or reconcile_of(desk)
        dis_d = got_d.get("order", {}).get("disagreed", [])
        resolved_d = all(got_d[k].get("isolated") is True and got_d[k]["code"] for k in dis_d)
        obs["deskd"] = {
            "disagreed": dis_d,
            "all isolated": resolved_d,
            "count in 0-2 (p)": len(dis_d) <= 2,
        }
        ok = ok and resolved_d
    else:
        obs["deskd"] = "NOT RUN: no --deskd"
    return p.done(ok, obs)


def pr2_sess(ctx: dict[str, Any]) -> Probe:
    p = Probe(
        "PR2-SESS",
        "round 1: Stub answered:true with UNAUTHENTICATED (the defect); round 2: Stub UNIMPLEMENTED, Secret answered, 0 login_failed",
        "round 1 reproduces the defect AND round 2 exact",
    )
    base = ctx.get("base")
    truth = json.loads((FIXTURES / "sessions" / "truth.json").read_text())
    ses = load_sessions()
    got = reconcile_of(ses)
    stub, secret = "sessions.v1.Sessions/Stub", "sessions.v1.Sessions/Secret"
    r2 = (
        got[stub]["code"] == "UNIMPLEMENTED"
        and got[stub]["answered"] is False
        and got[secret]["answered"] is True
        and not any(v.get("reason") for v in got.values() if isinstance(v, dict))
        and all(got[k]["code"] == v["alone"] for k, v in truth["rpcs"].items())
    )
    control: Any = "NOT RUN: no --base"
    reproduced = True
    if base is not None:
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "emit-rec",
                "sessions",
                str(ctx["scratch"]),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=base,
            env={**os.environ, "PYTHONPATH": str(base)},
        )
        if proc.returncode != 0:
            control, reproduced = proc.stderr[-300:], False
        else:
            c1 = json.loads(proc.stdout.splitlines()[-1])
            control = {k.rsplit("/", 1)[-1]: c1[k] for k in c1 if k != "(absent)"}
            reproduced = c1[stub]["answered"] is True and c1[stub]["code"] == "UNAUTHENTICATED"
    return p.done(
        r2 and reproduced,
        {
            "round 1 (control, cea85fc library)": control,
            "round 1 defect reproduced": reproduced,
            "round 2": {k.rsplit("/", 1)[-1]: v for k, v in got.items() if k != "(absent)"},
        },
        None,
    )


def _truth_for_scorer(desk: App) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    flows = [
        {
            "name": f["id"],
            "steps": [
                {
                    "rpc": s["rpc"],
                    "as": s["user"],
                    "message": s["message"],
                    "expect_code": s["code"],
                }
                for s in f["steps"]
            ],
        }
        for f in desk.truth["flows"]
    ]
    links = json.loads((FIXTURES / "deskd_links.json").read_text())["flows"]
    return flows, links


def _truth_replay(desk: App, truth_flows: list[dict[str, Any]]) -> dict[str, bool]:
    from lattice.spec import Rpc, Scenario, check

    def rpc_of(s: dict[str, Any]) -> Any:
        svc, _, m = s["rpc"].partition("/")
        return Rpc(svc, m, s["as"], s["message"], expect_code=s["expect_code"])

    scen = tuple(
        Scenario(f["name"], steps=tuple(rpc_of(s) for s in f["steps"])) for f in truth_flows
    )
    rep = check(
        replace(desk.spec, scenarios=scen),
        desk.make,
        desk.build_db,
        layers="D",
        runtime=desk.runtime,
    )
    failed = {f.rule.removeprefix("scenario: ") for f in rep.findings}
    return {f["name"]: f["name"] not in failed for f in truth_flows}


def pf2_n(ctx: dict[str, Any]) -> Probe:
    p = Probe(
        "PF2-N",
        "verified 15-20 (p; round 1: 17), verified-but-failing 0, unbound 0",
        "S2 with 0 vbf, verified >= 3; edge recall and coverage reported (N truth has no links)",
    )
    app = load_n()
    t0 = time.perf_counter()
    flows = flows_of(app)
    secs = time.perf_counter() - t0
    ctx["flows_n"] = flows
    verified = [f for f in flows if f["status"] == "verified"]
    vbf = replay_failures(app, verified)[0]
    row = scorer.score_flows(
        flows,
        [],
        {},
        verified_but_failing=len(vbf),
        min_verified=3,
        edge_recall_bound=None,
        coverage_bound=None,
    )
    return p.done(
        row["pass"],
        {
            **{
                k: row[k]
                for k in (
                    "recovered",
                    "recovered_by_status",
                    "verified",
                    "verified_but_failing",
                    "failing_codes",
                    "unbound_reasons",
                )
            },
            "in 15-20 (p)": 15 <= len(verified) <= 20,
            "seconds": round(secs, 1),
        },
        {"score": row, "vbf ids": vbf},
    )


def pf2_desk(ctx: dict[str, Any], desk: App) -> Probe:
    p = Probe(
        "PF2-DESK (dev, tuned-on)",
        "recovered 30-55, verified 8-25, unbound 0-6; edge recall 0.35-0.70; coverage 3-6 of 7; exact 0-1 of 7 (p)",
        "S2: verified >= 1, 0 vbf, edge recall >= 0.30, coverage >= 2",
    )
    truth_flows, links = _truth_for_scorer(desk)
    t_ok = _truth_replay(desk, truth_flows)
    t0 = time.perf_counter()
    flows = flows_of(desk)
    secs = time.perf_counter() - t0
    ctx["t_flows_desk"], ctx["flows_desk"] = secs, flows
    verified = [f for f in flows if f["status"] == "verified"]
    vbf = replay_failures(desk, verified)[0]
    row = scorer.score_flows(
        flows,
        truth_flows,
        links,
        t_ok,
        verified_but_failing=len(vbf),
        min_verified=1,
        edge_recall_bound=0.30,
        coverage_bound=2,
    )
    row["truth_flows_passing"] = sum(t_ok.values())
    row["recovered_by_kind_status"] = {}
    for f in flows:
        k = row["recovered_by_kind_status"].setdefault(f["kind"], {})
        k[f["status"]] = k.get(f["status"], 0) + 1
    ctx["row_desk"] = row
    return p.done(
        row["pass"],
        {
            **{
                k: row[k]
                for k in (
                    "recovered",
                    "recovered_by_status",
                    "verified",
                    "verified_but_failing",
                    "edge_recall",
                    "coverage",
                    "exact",
                    "failing_codes",
                    "unbound_reasons",
                    "truth_flows_passing",
                )
            },
            "seconds": round(secs, 1),
        },
        {
            "score": row,
            "vbf ids": vbf,
            "truth flow failures": [k for k, v in t_ok.items() if not v],
        },
    )


def pf2_replay(ctx: dict[str, Any], n: App, desk: App | None) -> Probe:
    p = Probe("PF2-REPLAY", "all pass (p)", "100%")
    obs: dict[str, Any] = {}
    ok = True
    for app, flows in ((n, ctx.get("flows_n")), (desk, ctx.get("flows_desk"))):
        if app is None:
            continue
        flows = flows if flows is not None else flows_of(app)
        verified = [f for f in flows if f["status"] == "verified"]
        fails = replay_failures(app, verified, times=3)
        obs[app.name] = {
            "verified": len(verified),
            "replays": 3,
            "failing per replay": [len(x) for x in fails],
        }
        ok = ok and bool(verified) and not any(fails)
    if desk is None:
        obs["deskd"] = "NOT RUN: no --deskd"
    return p.done(ok, obs)


def _stray(needles: list[str]) -> list[str]:
    out = []
    me = str(os.getpid())
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid == me:
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if any(n in cmd for n in needles) and "grpc_probes2" not in cmd:
            out.append(cmd[:120])
    return out


def pf2_leak(ctx: dict[str, Any], desk: App | None) -> Probe:
    from lattice.spec.grpc import live_bridges, reconcile_with, run_rpc_flows
    from lattice.spec.process import ProcessApp, ProcessStartError, live_processes

    p = Probe("PF2-LEAK", "0", "0")
    ses, n = load_sessions(), load_n()
    reconcile_of(ses)  # includes RC3 isolated runs
    reconcile_of(n)
    flows_of(n)
    if desk is not None:
        reconcile_of(desk)
        flows_of(desk)
    after_runs = len(live_processes()) + len(live_bridges())

    def broken(db: Path) -> Any:
        return ProcessApp(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            db_path=db,
            env={},
            port_env="PORT",
            db_env="DB_PATH",
            ready="grpc",
            protos=ses.harness.PROTOS,
            proto_paths=ses.harness.PROTO_PATHS,
            startup_timeout=5.0,
        )

    raised = None
    try:
        reconcile_with(
            ses.model,
            make_app=broken,
            build_db=ses.build_db,
            runtime=ses.runtime,
            users=ses.users,
        )
    except ProcessStartError as err:
        raised = type(err).__name__
    flows = run_rpc_flows(n.extract, n.spec, broken, n.build_db, runtime=n.runtime)
    after_fail = len(live_processes()) + len(live_bridges())
    needles = ["sessionsapp", "notesapp", "/deskd", "grpcbridge"]
    stray = _stray(needles)
    return p.done(
        after_runs == 0 and after_fail == 0 and raised == "ProcessStartError" and not stray,
        {
            "live processes+bridges after reconcile and flows": after_runs,
            "after a forced startup failure (reconcile raises, flows fail)": after_fail,
            "reconcile raised": raised,
            "flows of the broken app": sorted({f["status"] for f in flows}),
            "stray app or bridge processes in /proc": len(stray),
        },
        stray,
    )


def pr1(ctx: dict[str, Any]) -> Probe:
    p = Probe("PR1", "every row as round 1's final results", "all PASS rows still PASS")
    base6a = ctx.get("base6a")
    if base6a is None:
        return p.not_run("no --base6a (git archive 6a318e5)")
    scratch = Path(ctx["scratch"])
    out = scratch / "pr1"
    out.mkdir(parents=True, exist_ok=True)
    final = {
        "0": json.loads((REPO / "docs/results/data/2026-10-17-grpc/probes-arm0.json").read_text()),
        "G": json.loads((REPO / "docs/results/data/2026-10-17-grpc/probes-armg.json").read_text()),
    }
    rows: dict[str, Any] = {}
    ok = True
    for arm in ("0", "G"):
        cmd = [
            sys.executable,
            str(HERE / "grpc_probes.py"),
            "--arm",
            arm,
            "--scratch",
            str(scratch / f"pr1-{arm}"),
            "--out",
            str(out / arm),
            "--base",
            str(base6a),
            "--truth-sha",
            str(REPO / "docs/results/data/2026-10-17-grpc/freeze-truth.sha256"),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, cwd=REPO, env=os.environ
        )
        name = {"0": "probes-arm0.json", "G": "probes-armg.json"}[arm]
        try:
            now = json.loads((out / arm / name).read_text())
        except OSError:
            rows[arm] = {"error": (proc.stdout + proc.stderr)[-500:]}
            ok = False
            continue
        was = {r["id"]: r["status"] for r in final[arm]["probes"]}
        is_ = {r["id"]: r for r in now["probes"]}
        regress = sorted(
            i for i, s in was.items() if s == "PASS" and is_.get(i, {}).get("status") != "PASS"
        )
        rows[arm] = {
            "rows": len(is_),
            "round-1 PASS rows still PASS": f"{sum(s == 'PASS' for s in was.values()) - len(regress)}/{sum(s == 'PASS' for s in was.values())}",
            "regressed": regress,
            "status now": {i: r["status"] for i, r in is_.items()},
        }
        if arm == "G":
            wf = is_.get("PG-WF", {}).get("observed")
            rows[arm]["PG-WF (counts may differ; reported)"] = wf
        ok = ok and not regress
    return p.done(ok, rows)


def px2(ctx: dict[str, Any]) -> Probe:
    p = Probe("PX2", "1.5-8 s; 15-90 s (p)", "<= 30 s; <= 180 s")
    a, b = ctx.get("t_rec_desk"), ctx.get("t_flows_desk")
    if a is None or b is None:
        return p.not_run("deskd was not run")
    return p.done(
        a <= 30 and b <= 180,
        {"reconcile on deskd (s)": round(a, 2), "flows on deskd (s)": round(b, 1)},
    )


def armg(ctx: dict[str, Any]) -> list[Probe]:
    probes: list[Probe] = []
    desk = load_desk(Path(ctx["deskd"]), Path(ctx["scratch"])) if ctx.get("deskd") else None
    n = load_n()
    probes.append(pr2_n(ctx))
    probes.append(
        pr2_desk(ctx, desk) if desk else Probe("PR2-DESK (dev)", "", "").not_run("no --deskd")
    )
    probes.append(pr2_order(ctx, desk))
    probes.append(pr2_sess(ctx))
    probes.append(pf2_n(ctx))
    probes.append(
        pf2_desk(ctx, desk) if desk else Probe("PF2-DESK (dev)", "", "").not_run("no --deskd")
    )
    probes.append(pf2_replay(ctx, n, desk))
    probes.append(pf2_leak(ctx, desk))
    probes.append(pr1(ctx) if not ctx.get("no_pr1") else Probe("PR1", "", "").not_run("--no-pr1"))
    probes.append(px2(ctx))
    return probes


def not_run_rows() -> list[Probe]:
    return [
        Probe("PM-S / PM-SG (Arm M)", "", "").not_run(
            "Arm M needs a Streak checkout and a Docker MySQL: out of scope (prereg 1); no docker daemon here"
        ),
        Probe("Arm L", "", "").not_run("the build round is its own node; out of scope"),
        Probe("PG-HO2 (HO2-*)", "", "").not_run(
            "the gated held-out stage: a new app by a new agent, scored once by the orchestrator after "
            "the freeze; it does not exist in this node's view and heldout2/ was not opened"
        ),
    ]


# ---- Arm H2: the gated held-out app (prereg sections 6, 7 and 12; scored ONCE) ----------------
#
# Written and hashed BEFORE ``heldout2/kilnworks/truth.json`` was opened (the hash is in the
# results doc). The interface comes from the pre-registration, the ``.proto`` files and the app's
# ``harness.py`` only. The truth is read tolerantly (the prereg names its fields: rpcs with state,
# codes, auth, tables, flows with ``as`` / ``message`` / ``expect_code`` and ``links``); a shape the
# reader cannot read raises ``ReaderMismatch`` BEFORE any metric exists, which is a runner problem
# (fixed in this file only, every run kept), never a score. Nothing here is tuned to a number.

H2_PKG = "kilnworks"
H2_FREEZE_SHA = "d7a5ea3c642e06b4ccb18983ad185896afec3cae8b15361b7d5941479803868e"
H2_DATA = REPO / "docs/results/data/2026-10-17-grpc-round2"
H2_PRED = {
    "HO2-RPC": "recall 0.90-1.00 / precision 0.95-1.00 (p)",
    "HO2-CODES": "recall 0.70-0.95 / precision 0.90-1.00 (p)",
    "HO2-AUTH": "0 wrong; unclear 0-0.3 (p)",
    "HO2-TABLES": "recall 0.9-1.0 (p)",
    "HO2-REC": "100%; 1-3 order disagreements, all resolved (p)",
    "HO2-FLOWS": "verified 3-12; edge recall 0.30-0.65 (point 0.45); coverage 2-5 of >= 6; exact 0-1; 0 vbf (p)",
    "HO2-DET": "10/10 identical (p)",
}
H2_RULE = {
    "HO2-RPC": "recall >= 0.90 and precision >= 0.95 (rpc name AND state must match)",
    "HO2-CODES": "micro recall >= 0.70 and micro precision >= 0.90 (implemented rpcs, OK excluded)",
    "HO2-AUTH": "0 rpcs labelled Public/Member that contradict truth (hard); unclear reported",
    "HO2-TABLES": "recall >= 0.9",
    "HO2-REC": "100% of truth states, 0 auth_refused on implemented non-public rpcs, every order disagreement resolved isolated",
    "HO2-FLOWS": "S2: verified >= 1, 0 verified-but-failing, edge recall >= 0.30, coverage >= 2 truth flows",
    "HO2-DET": "10/10 identical, no absolute path, time or port",
}
PUBLIC_LABELS = {"public", "none", "anonymous", "anon", "unauthenticated", "open", "no"}


class ReaderMismatch(Exception):
    """The held-out truth (or harness) is not in a shape this runner reads: raised before any metric."""


def h2_verify_freeze(root: Path) -> dict[str, Any]:
    """The held-out files against its ``freeze.sha256`` (itself against the hash recorded in the
    prereg stage and the committed copy), and the frozen library files against the build node's
    listings. ``dogfood/grpc_probes2.py`` is the one listed file this stage changes (it adds this
    arm): its listed hash is reported, not enforced, and the new hash is recorded."""
    out: dict[str, Any] = {}
    fz = root / "freeze.sha256"
    out["heldout_freeze_file_sha256"] = sha(fz.read_bytes())
    out["heldout_freeze_sha256_ok"] = out["heldout_freeze_file_sha256"] == H2_FREEZE_SHA
    committed = H2_DATA / "heldout2.freeze.sha256"
    out["heldout_freeze_equals_committed_copy"] = committed.read_bytes() == fz.read_bytes()
    out["heldout_problems"] = r1._check_sha_file(fz, root)
    out["heldout_files"] = sum(1 for ln in fz.read_text().splitlines() if ln.strip())
    problems: list[str] = []
    checked = 0
    for name in ("freeze-lib.sha256", "freeze-dev.sha256", "freeze2.sha256"):
        lines = (H2_DATA / name).read_text().splitlines()
        for line in lines:
            if not line.strip():
                continue
            digest, _, rel = line.partition("  ")
            rel = rel.strip()
            if rel == "dogfood/grpc_probes2.py":
                out["runner_listed_sha256 (dev stage, not enforced)"] = digest
                continue
            checked += 1
            f = REPO / rel
            if not f.is_file():
                problems.append(f"{name}: missing {rel}")
            elif sha(f.read_bytes()) != digest:
                problems.append(f"{name}: changed {rel}")
    out["library_files_checked"] = checked
    out["library_problems"] = problems
    out["runner_sha256 (H2 stage)"] = sha((HERE / "grpc_probes2.py").read_bytes())
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    staged = subprocess.run(
        ["git", "diff", "--name-only", "--cached"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    out["tracked_files_changed"] = sorted(changed)
    out["staged"] = sorted(staged)
    out["tracked_unexpected"] = sorted(
        set(changed) - {"dogfood/grpc_probes2.py", "docs/results/2026-10-17-grpc-round2-results.md"}
    )
    out["ok"] = (
        out["heldout_freeze_sha256_ok"]
        and out["heldout_freeze_equals_committed_copy"]
        and not out["heldout_problems"]
        and not problems
        and not out["tracked_unexpected"]
        and not staged
    )
    return out


def _auth2(value: Any) -> str:
    """``Public`` / ``Member`` from a truth or extract label: anything that is not a public-like
    word is an authenticated rpc (the app has roles, the labels are Public and Member)."""
    v = str(value or "").strip()
    low = v.lower()
    if low == "public" or low in PUBLIC_LABELS:
        return "Public"
    if low == "member":
        return "Member"
    return v


def _truth_auth(value: Any) -> str:
    v = _auth2(value)
    return v if v in ("Public", "Member", "") else "Member"


def h2_truth(path: Path) -> dict[str, Any]:
    """``truth.json`` (``lattice.heldout.truth/2``) normalised: rpcs {full: {state, codes, auth}},
    tables, flows with 1-based ``links``. Raises ReaderMismatch on a shape it cannot read."""
    raw = json.loads(path.read_text())
    rpcs_raw = r1._first(raw, "rpcs", "methods", default={})
    entries: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(rpcs_raw, Mapping):
        entries = [(r1._full(k), v) for k, v in rpcs_raw.items()]
    else:
        for v in rpcs_raw:
            has_full = "full_name" in v or "rpc" in v
            entries.append(
                (
                    r1._full(
                        r1._first(v, "full_name", "rpc", "name", "id", default=""),
                        None if has_full else r1._first(v, "service"),
                    ),
                    v,
                )
            )
    rpcs = {
        k: {
            "state": r1._state(r1._first(v, "state", "status", default="")),
            "codes": r1._code_names(r1._first(v, "codes", "grpc_codes", default=())),
            "auth": _truth_auth(r1._first(v, "auth", default="")),
            "auth_raw": str(r1._first(v, "auth", default="")),
        }
        for k, v in entries
    }
    if not rpcs or any(not v["state"] for v in rpcs.values()):
        raise ReaderMismatch("truth rpcs: none read, or a rpc without a state")
    tables_raw = r1._first(raw, "tables", default=[])
    if isinstance(tables_raw, Mapping):
        tables_raw = list(tables_raw)
    tables = {
        str(t if isinstance(t, str) else r1._first(t, "table", "name", default="")).lower()
        for t in tables_raw
    }
    if not tables:
        raise ReaderMismatch("truth tables: none read")
    flows: list[dict[str, Any]] = []
    raw_links: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for i, f in enumerate(r1._first(raw, "flows", default=[])):
        steps = []
        flow_links = list(f.get("links") or []) if isinstance(f, Mapping) else []
        for j, st in enumerate(r1._first(f, "steps", "rpcs", "calls", default=[]), 1):
            if not isinstance(st, Mapping):
                raise ReaderMismatch(f"flow {i + 1} step {j} is not an object")
            rpc = r1._first(st, "rpc", "call", "method", "name", default="")
            svc = r1._first(st, "service") if "/" not in str(rpc) else None
            code = r1._first(st, "expect_code", "expect", "code", "expected_code", default="OK")
            if isinstance(code, Mapping):
                code = r1._first(code, "code", default="OK")
            steps.append(
                {
                    "rpc": r1._full(rpc, svc),
                    "as": r1._first(st, "as", "user", "caller", "actor", "who", default=None),
                    "message": r1._first(
                        st, "message", "request", "req", "body", "input", "args", default={}
                    ),
                    "expect_code": str(code).upper(),
                }
            )
            for ln in st.get("links") or []:
                raw_links.append((i, {"step": j, **ln} if "step" not in ln else dict(ln), ln))
        for ln in flow_links:
            raw_links.append((i, dict(ln), ln))
        flows.append(
            {"name": str(r1._first(f, "id", "name", default=f"flow{i + 1}")), "steps": steps}
        )
    if not flows:
        raise ReaderMismatch("truth flows: none read")
    zero_based = any(
        r1._first(ln, "step", default=1) == 0 or r1._first(ln, "from_step", default=1) == 0
        for _, ln, _ in raw_links
    )
    links: dict[str, list[dict[str, Any]]] = {f["name"]: [] for f in flows}
    for i, ln, _ in raw_links:
        try:
            step = int(r1._first(ln, "step", "to_step", "consumer_step"))
            frm = int(r1._first(ln, "from_step", "from", "producer_step"))
            field = str(r1._first(ln, "field", "request_field", "to_field"))
            ffield = str(r1._first(ln, "from_field", "response_field", "producer_field"))
        except (TypeError, ValueError) as err:
            raise ReaderMismatch(
                f"flow {i + 1}: a link is not {{step, field, from_step, from_field}}"
            ) from err
        step, frm = (step + 1, frm + 1) if zero_based else (step, frm)
        n = len(flows[i]["steps"])
        if not (1 <= frm < step <= n):
            raise ReaderMismatch(
                f"flow {i + 1}: link step {step} from {frm} outside 1..{n} or not earlier"
            )
        links[flows[i]["name"]].append(
            {"step": step, "field": field, "from_step": frm, "from_field": ffield}
        )
    return {
        "rpcs": rpcs,
        "tables": tables,
        "flows": flows,
        "links": links,
        "links_zero_based": zero_based,
        "keys": sorted(raw),
    }


def load_kiln(base: Path, scratch: Path, *, truth: bool = True) -> App:
    """``kilnworks`` through its own harness (``make_app`` already names protos, proto paths and
    proto_root; ``RUNTIME`` is built from the library's ``AuthDriver.rpc``)."""
    import importlib.util

    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from lattice.profile import ApiProfile, Profile
    from lattice.spec import Spec

    os.environ.setdefault("KILNWORKS_BIN_DIR", str(scratch / "kiln-bin"))
    path = base / "harness.py"
    spec_ = importlib.util.spec_from_file_location("heldout2_kilnworks_harness", path)
    assert spec_ and spec_.loader
    h = importlib.util.module_from_spec(spec_)
    sys.modules["heldout2_kilnworks_harness"] = h
    spec_.loader.exec_module(h)
    parent = base.resolve().parent
    fe = GoGrpcFrontEnd()
    protos = tuple(str(base / p) for p in h.PROTOS)
    profile = Profile(
        schemas={},
        api=ApiProfile(
            tables={}, table_names={}, roles=frozenset({"Public", "Member"}), error_body="fastapi"
        ),
    )
    return App(
        name=H2_PKG,
        root=base,
        fixtures=parent,
        pkg=H2_PKG,
        fe=fe,
        extract=fe.extract_json(parent, H2_PKG),
        model=fe.extract(parent, H2_PKG),
        make=h.make_app,
        raw_make=h.make_app,
        build_db=h.build_db,
        runtime=h.RUNTIME,
        users=h.USERS,
        protos=(),
        proto_paths=(),
        spec=Spec(profile=profile, users=h.USERS, protos=protos),
        harness=h,
        truth=h2_truth(base / "truth.json") if truth else None,
    )


def _describe_text(app: App) -> str:
    from lattice.spec.grpc import Bridge

    base = Path(app.root)
    files = [base / p["file"] for p in app.extract["grpc"]["protos"]]
    with Bridge() as b:
        d = b.describe(files, [base, base / "proto"])
    return json.dumps(d["grpc"], sort_keys=True) + d["descriptor_set"]


def _h2_rpc_metrics(app: App) -> list[Probe]:
    truth, extract = app.truth, app.extract
    got = {f"{s['name']}/{r['name']}": r for s in extract["grpc"]["services"] for r in s["rpcs"]}
    ex_state = {k: r1._state(r.get("state")) for k, r in got.items()}
    probes: list[Probe] = []
    t_pairs = {(k, v["state"]) for k, v in truth["rpcs"].items()}
    e_pairs = set(ex_state.items())
    hit = t_pairs & e_pairs
    rec, prec = r1._prf(len(hit), len(t_pairs), len(e_pairs))
    streams = {k for k, v in truth["rpcs"].items() if v["state"] == "streaming"}
    excluded = {x["path"].lstrip("/") for x in extract["excluded"] if x.get("kind") == "streaming"}
    route_keys = {f"{r.rpc.service}/{r.rpc.method}" for r in app.model.routes if r.rpc}
    probes.append(
        Probe("HO2-RPC", H2_PRED["HO2-RPC"], H2_RULE["HO2-RPC"]).done(
            rec >= 0.90 and prec >= 0.95,
            {
                "recall": round(rec, 3),
                "precision": round(prec, 3),
                "truth rpcs": len(t_pairs),
                "extracted rpcs": len(e_pairs),
                "matched": len(hit),
                "truth states": dict(
                    sorted(Counter(v["state"] for v in truth["rpcs"].values()).items())
                ),
                "streaming reported not supported": sorted(streams & excluded),
                "streaming not supported (all)": streams <= excluded and not (streams & route_keys),
                "model endpoints": len(route_keys),
            },
            {
                "missed (truth, state)": sorted(t_pairs - e_pairs),
                "extra (extract, state)": sorted(e_pairs - t_pairs),
            },
        )
    )
    tc = {
        (k, c) for k, v in truth["rpcs"].items() if v["state"] == "implemented" for c in v["codes"]
    }
    ec = {
        (k, c)
        for k, r in got.items()
        if ex_state[k] == "implemented"
        for c in r1._code_names(r.get("codes"))
    }
    crec, cprec = r1._prf(len(tc & ec), len(tc), len(ec))
    probes.append(
        Probe("HO2-CODES", H2_PRED["HO2-CODES"], H2_RULE["HO2-CODES"]).done(
            crec >= 0.70 and cprec >= 0.90,
            {
                "recall": round(crec, 3),
                "precision": round(cprec, 3),
                "truth codes": len(tc),
                "extracted codes": len(ec),
                "matched": len(tc & ec),
            },
            {"missed": sorted(tc - ec), "extra": sorted(ec - tc)},
        )
    )
    wrong, unclear, checked = [], [], 0
    for k, v in truth["rpcs"].items():
        r = got.get(k)
        if r is None or ex_state[k] != "implemented" or v["state"] != "implemented":
            continue
        checked += 1
        label = _auth2(r.get("auth"))
        if label not in ("Public", "Member"):
            unclear.append((k, label or "(none)", v["auth_raw"]))
        elif label != v["auth"]:
            wrong.append((k, label, v["auth_raw"]))
    probes.append(
        Probe("HO2-AUTH", H2_PRED["HO2-AUTH"], H2_RULE["HO2-AUTH"]).done(
            not wrong and checked > 0,
            {
                "wrong": len(wrong),
                "unclear": len(unclear),
                "checked": checked,
                "unclear share": round(len(unclear) / checked, 3) if checked else None,
            },
            {"wrong (rpc, extract, truth)": wrong, "unclear (rpc, extract, truth)": unclear},
        )
    )
    et = {t["table"].lower() for t in extract["tables"]}
    trec, tprec = r1._prf(len(truth["tables"] & et), len(truth["tables"]), len(et))
    probes.append(
        Probe("HO2-TABLES", H2_PRED["HO2-TABLES"], H2_RULE["HO2-TABLES"]).done(
            trec >= 0.9,
            {
                "recall": round(trec, 3),
                "precision": round(tprec, 3),
                "truth": len(truth["tables"]),
                "extracted": len(et),
            },
            {"missed": sorted(truth["tables"] - et), "extra": sorted(et - truth["tables"])},
        )
    )
    return probes


def _h2_rec(app: App, ctx: dict[str, Any]) -> Probe:
    """The library's ``reconcile_with`` (no runner re-implementation) against the truth states."""
    from lattice.spec.grpc import _call_fresh

    p = Probe("HO2-REC", H2_PRED["HO2-REC"], H2_RULE["HO2-REC"])
    t0 = time.perf_counter()
    got = reconcile_of(app)
    secs = time.perf_counter() - t0
    ctx["rec"] = got
    truth = app.truth["rpcs"]
    ex = {f"{s['name']}/{r['name']}": r for s in app.extract["grpc"]["services"] for r in s["rpcs"]}
    classes: dict[str, list[bool]] = {
        "implemented": [],
        "unimplemented": [],
        "not_registered": [],
        "absent": [],
    }
    bad: list[Any] = []
    unknown = sorted(
        {v["state"] for v in truth.values()}
        - {"implemented", "unimplemented", "not_registered", "streaming"}
    )
    refused = set(got.get("auth_refused", []))
    # truth rpcs the library did not call (a not-registered rpc is not a route): one supplementary
    # fresh-login call each, reported as such
    supp: dict[str, Any] = {}
    missing = [k for k, v in truth.items() if v["state"] != "streaming" and k not in got]
    if missing:
        db = app.build_db(Path(tempfile.mkdtemp(prefix="lattice-h2-supp-")) / "s.db")
        sapp = app.make(db)
        try:
            with app.runtime.open(sapp) as tr:
                first = next(iter(app.users))
                for k in missing:
                    svc, _, m = k.partition("/")
                    pub = _auth2(ex.get(k, {}).get("auth")) == "Public"
                    supp[k] = _call_fresh(tr.rpc(), tr, app.runtime, first, None, svc, m, pub)
        finally:
            app.runtime.close(sapp)
    uncalled = []
    for k, v in truth.items():
        if v["state"] == "streaming":
            continue
        res = got.get(k) or supp.get(k)
        cls = v["state"] if v["state"] in classes else "implemented"
        if res is None or res.get("code") is None:
            uncalled.append((k, (res or {}).get("reason")))
            classes[cls].append(False)
            continue
        if cls == "implemented":
            ok = res["code"] != "UNIMPLEMENTED" and k not in refused
        else:
            ok = res["code"] == "UNIMPLEMENTED"
        classes[cls].append(ok)
        if not ok:
            bad.append((k, res["code"], cls, "auth_refused" if k in refused else ""))
    ab = got.get("(absent)", {}).get("code")
    classes["absent"].append(ab == "UNIMPLEMENTED")
    if ab != "UNIMPLEMENTED":
        bad.append(("(absent)", ab, "absent", ""))
    dis = got.get("order", {}).get("disagreed", [])
    resolved = all(got[k].get("isolated") is True and got[k].get("code") for k in dis)
    extra = sorted(
        k for k in got if k not in truth and k not in ("(absent)", "order", "auth_refused")
    )
    return p.done(
        not bad
        and not uncalled
        and resolved
        and not refused
        and all(classes[c] for c in ("implemented", "absent")),
        {
            **{c: f"{sum(v)}/{len(v)}" for c, v in classes.items()},
            "auth_refused": sorted(refused),
            "order disagreed": dis,
            "all disagreements resolved isolated": resolved,
            "rpcs answered by the library": len(
                [k for k in got if k not in ("order", "auth_refused")]
            ),
            "supplementary calls (not library output)": sorted(supp),
            "seconds": round(secs, 2),
        },
        {
            "wrong (rpc, code, class, note)": bad,
            "uncalled or login_failed": uncalled,
            "library output rpcs not in truth": extra,
            "unknown truth states": unknown,
            "codes": {k: v for k, v in got.items()},
            "supplementary": supp,
        },
    )


def _h2_flows(app: App, ctx: dict[str, Any]) -> Probe:
    p = Probe("HO2-FLOWS", H2_PRED["HO2-FLOWS"], H2_RULE["HO2-FLOWS"])
    truth_flows = app.truth["flows"]
    links = app.truth["links"]
    t_ok = _truth_replay(app, truth_flows)
    t0 = time.perf_counter()
    flows = flows_of(app)
    secs = time.perf_counter() - t0
    ctx["flows"] = flows
    verified = [f for f in flows if f["status"] == "verified"]
    vbf = replay_failures(app, verified)[0] if verified else []
    row = scorer.score_flows(
        flows,
        truth_flows,
        links,
        t_ok,
        verified_but_failing=len(vbf),
        min_verified=1,
        edge_recall_bound=0.30,
        coverage_bound=2,
    )
    row["truth_flows"] = len(truth_flows)
    row["truth_flows_passing"] = sum(t_ok.values())
    row["truth_edges_total"] = sum(len(v) for v in links.values())
    row["recovered_by_kind_status"] = {}
    for f in flows:
        k = row["recovered_by_kind_status"].setdefault(f["kind"], {})
        k[f["status"]] = k.get(f["status"], 0) + 1
    ctx["row_flows"] = row
    keys = (
        "truth_flows",
        "truth_flows_passing",
        "truth_edges_total",
        "recovered",
        "recovered_by_status",
        "recovered_by_kind_status",
        "verified",
        "verified_but_failing",
        "edge_recall",
        "coverage",
        "exact",
        "failing_codes",
        "unbound_reasons",
        "reasons",
    )
    return p.done(
        row["pass"] and len(truth_flows) >= 1,
        {**{k: row[k] for k in keys}, "seconds": round(secs, 1)},
        {
            "score": row,
            "vbf ids": vbf,
            "truth flow failures": [k for k, v in t_ok.items() if not v],
            "not verified": [
                {"id": f["id"], "kind": f["kind"], "status": f["status"], "reason": f.get("reason")}
                for f in flows
                if f["status"] != "verified"
            ],
        },
    )


def _h2_det(root: Path, scratch: Path) -> Probe:
    """extract, describe, model, plans, flows and reconcile over 10 processes (random hash seeds),
    two roots (a byte copy of the app directory for the second)."""
    p = Probe("HO2-DET", H2_PRED["HO2-DET"], H2_RULE["HO2-DET"])
    parent = root.resolve().parent
    rx_abs = re.compile(r"/(home|tmp|Users|root)/[\w./-]+")
    rx_time = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
    rx_port = re.compile(r"127\.0\.0\.1:\d+|localhost:\d+")
    shutil.rmtree(scratch / "det-b-kiln", ignore_errors=True)
    copy = scratch / "det-b-kiln" / "x" / "y"
    shutil.copytree(parent, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    roots = [parent, copy]
    results: list[Any] = []
    leaks: list[str] = []
    for i in range(10):
        r = roots[i % 2]
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "emit-det",
                "kiln",
                str(r),
                str(scratch),
            ],
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
            scrubbed = text.replace(str(r), "").replace(str(parent), "")
            for rx, what in ((rx_abs, "absolute path"), (rx_time, "time"), (rx_port, "port")):
                m = rx.search(scrubbed)
                if m and key != "extract":
                    leaks.append(f"{key}: {what} {m.group(0)!r}")
    ok_runs = [r for r in results if isinstance(r, dict)]
    same = len({json.dumps(r, sort_keys=True) for r in ok_runs}) == 1
    differing = (
        sorted({k for r in ok_runs for k in r if len({x.get(k) for x in ok_runs}) > 1})
        if ok_runs
        else []
    )
    return p.done(
        len(ok_runs) == 10 and same and not leaks,
        {
            "identical runs": len(ok_runs) if same else 0,
            "of": 10,
            "roots": 2,
            "artifacts": sorted(ok_runs[0]) if ok_runs else [],
            "differing artifacts": differing,
            "leaks": sorted(set(leaks))[:5],
        },
        [r for r in results if not isinstance(r, dict)],
    )


def arm_h2(heldout: Path, scratch: Path, out: Path) -> int:
    root = Path(heldout).resolve() / H2_PKG
    fz = h2_verify_freeze(root)
    print(
        "freeze:", json.dumps({k: fz[k] for k in ("ok", "heldout_files", "library_files_checked")})
    )
    if not fz["ok"]:
        print("REFUSED: a frozen file differs; nothing was scored")
        print(json.dumps(fz, indent=1))
        return 2
    os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch / "gocache"))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch / "bridgecache"))
    before = _snapshot(root)
    ctx: dict[str, Any] = {}
    errors: list[str] = []
    probes: list[Probe] = []
    try:
        app = load_kiln(root, scratch)
    except ReaderMismatch as err:
        print(f"RUNNER ERROR (reader mismatch, no metric written): {err}")
        return 3
    except Exception as err:  # noqa: BLE001
        print(f"RUNNER ERROR (no metric written): {type(err).__name__}: {str(err)[:800]}")
        return 3
    if app.runtime is None:
        print(
            "RUNNER ERROR (no metric written): harness RUNTIME is None (no AuthDriver.rpc in the library)"
        )
        return 3
    steps = (
        ("rpc", lambda: _h2_rpc_metrics(app)),
        ("HO2-REC", lambda: _h2_rec(app, ctx)),
        ("HO2-FLOWS", lambda: _h2_flows(app, ctx)),
        ("HO2-DET", lambda: _h2_det(root, scratch)),
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
    after = _snapshot(root)
    changed = [x for x in before if before[x] != after.get(x)]
    new = [x for x in after if x not in before]
    nw = Probe("P2-NW (heldout2 root)", "0 changed, 0 new", "all 0").done(
        not changed and not new, {"changed": len(changed), "new": len(new)}, changed + new
    )
    fz_after = h2_verify_freeze(root)
    probes += [nw]
    probes += [p for p in not_run_rows() if not p.id.startswith("PG-HO2")]
    doc = {
        "schema": "lattice.grpc-probes2-h2/1",
        "arm": "H2",
        "heldout_freeze_before": fz,
        "heldout_freeze_after_ok": fz_after["ok"],
        "library_sha256": {
            f: sha((REPO / f).read_bytes())
            for f in (
                "lattice/spec/grpc.py",
                "lattice/importer/workflows.py",
                "dogfood/grpc_flow_scorer.py",
                "dogfood/grpc_probes2.py",
            )
        },
        "truth_reader": {
            "keys": app.truth["keys"],
            "links_zero_based": app.truth["links_zero_based"],
            "rpcs": len(app.truth["rpcs"]),
            "tables": len(app.truth["tables"]),
            "flows": len(app.truth["flows"]),
        },
        "probes": [pr.row() for pr in probes],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes2-armh2.json").write_text(json.dumps(doc, indent=1, default=str) + "\n")
    (out / "reconcile-kilnworks.json").write_text(json.dumps(ctx["rec"], indent=1) + "\n")
    (out / "flows-kilnworks.json").write_text(
        json.dumps(ctx["flows"], indent=1, default=str) + "\n"
    )
    for pr in probes:
        print(f"{pr.status:8} {pr.id:24} {json.dumps(pr.observed, default=str)[:260]}")
    failed = [pr for pr in probes if pr.status == "FAIL"]
    print(
        f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed, "
        f"{sum(pr.status == 'NOT RUN' for pr in probes)} not run"
    )
    return 1 if failed else 0


# ---- main -----------------------------------------------------------------------------------------


def run_check(name: str, ctx: dict[str, Any]) -> int:
    scratch = Path(ctx["scratch"])
    deskd = Path(ctx["deskd"]) if ctx.get("deskd") else None
    table = {
        "pr2-sess": lambda: pr2_sess(ctx),
        "pf2-n": lambda: pf2_n(ctx),
        "pf2-desk": lambda: pf2_desk(ctx, load_desk(deskd, scratch)),  # type: ignore[arg-type]
        "pr2-desk": lambda: pr2_desk(ctx, load_desk(deskd, scratch)),  # type: ignore[arg-type]
        "pr2-order": lambda: pr2_order(ctx, None),
        "p2-bc-ast": lambda: p2_bc_ast(Path(ctx["base"])),
        "p2-score": p2_score,
        "pf2-replay": lambda: pf2_replay(ctx, load_n(), None),
    }
    pr = table[name]()
    print(f"{pr.status} {pr.id} {json.dumps(pr.observed, default=str)[:400]}")
    return 0 if pr.status == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["emit-det"]:
        cmd_emit_det(args_in[1], args_in[2], args_in[3])
        return 0
    if args_in[:1] == ["emit-rec"]:
        cmd_emit_rec(args_in[1], args_in[2])
        return 0
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--arm", choices=("0", "G", "H2"))
    ap.add_argument("--check", default=None, help="run one probe by name (what the mutants run)")
    ap.add_argument("--base", type=Path, default=None, help="git archive cea85fc, extracted")
    ap.add_argument(
        "--base6a", type=Path, default=None, help="git archive 6a318e5, extracted (PR1)"
    )
    ap.add_argument(
        "--deskd", type=Path, default=None, help="development data: the deskd directory"
    )
    ap.add_argument(
        "--heldout", type=Path, default=None, help="--arm H2: the directory holding kilnworks/"
    )
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--reg-before", type=Path, default=None)
    ap.add_argument("--reg-after", type=Path, default=None)
    ap.add_argument("--no-pr1", action="store_true")
    args = ap.parse_args(args_in)
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="grpc2-probes-"))
    scratch.mkdir(parents=True, exist_ok=True)
    if args.arm == "H2":
        if args.heldout is None:
            ap.error("--arm H2 needs --heldout (the directory holding kilnworks/)")
        return arm_h2(args.heldout, scratch, args.out)
    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    ctx: dict[str, Any] = {
        "scratch": scratch,
        "base": args.base,
        "base6a": args.base6a,
        "deskd": args.deskd,
        "reg_before": args.reg_before,
        "reg_after": args.reg_after,
        "no_pr1": args.no_pr1,
    }
    if args.check:
        return run_check(args.check, ctx)
    if args.arm is None:
        ap.error("--arm or --check is required")
    args.out.mkdir(parents=True, exist_ok=True)
    before = frozen_check(args.deskd)
    probes = arm0(ctx) if args.arm == "0" else armg(ctx)
    after = frozen_check(args.deskd)
    probes += not_run_rows()
    doc: dict[str, Any] = {
        "schema": "lattice.grpc-probes2/1",
        "arm": args.arm,
        "library_sha256": {
            f: sha((REPO / f).read_bytes())
            for f in (
                "lattice/spec/grpc.py",
                "lattice/importer/workflows.py",
                "dogfood/grpc_flow_scorer.py",
                "dogfood/grpc_probes2.py",
            )
        },
        "frozen_before": before,
        "frozen_after": after,
        "probes": [pr.row() for pr in probes],
    }
    name = {"0": "probes2-arm0.json", "G": "probes2-armg.json"}[args.arm]
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
    for pr in probes:
        print(f"{pr.status:8} {pr.id:28} {json.dumps(pr.observed, default=str)[:220]}")
    print("frozen files ok before:", before["ok"], "after:", after["ok"])
    failed = [pr for pr in probes if pr.status == "FAIL"]
    print(
        f"{sum(pr.status == 'PASS' for pr in probes)} passed, {len(failed)} failed, "
        f"{sum(pr.status == 'NOT RUN' for pr in probes)} not run"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
