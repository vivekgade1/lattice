"""Probes of Spike I, gRPC in import and build (docs/results/2026-10-17-grpc-prereg.md).

Arm 0 (static) and Arm G (a grpc-go server over pure-Go SQLite, no Docker) run here. Arm M
(Streak over MySQL), Arm L (the build round, recommended as node I2) and the held-out stage
(a separate agent writes the app) are reported ``NOT RUN`` with the reason, never skipped
silently. The ground truth is the hand-written ``truth.json`` / ``expected_*.json`` of the
fixtures (hashed before the extractor existed) and protocompile; nothing here reads the
extractor to decide what is expected.

Run (see the pre-registration, section 10)::

    export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree>
    uv run python dogfood/grpc_probes.py --arm 0 --scratch "$SCRATCH/p0" --base "$SCRATCH/base" \\
        --out docs/results/data/2026-10-17-grpc
    uv run python dogfood/grpc_probes.py --arm G --scratch "$SCRATCH/pg" --out docs/results/data/...
    uv run python dogfood/grpc_probes.py --make-baseline --base "$SCRATCH/base" --out ...

``emit-bc <tree>`` (internal) prints the hashes of every HTTP output the byte-compat promise
names, computed by the tree it is run against: the runner runs it on the baseline tree
(``git archive 6a318e5``) and on this one and compares.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
GOCHECK_FIXTURES = (
    "app",
    "bounds",
    "boundsets",
    "clean",
    "clean2",
    "goextract",
    "goextract_workflow",
    "machine",
    "owner",
    "partial",
    "private",
    "runs",
    "txflag",
)
MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"},
    },
}


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


# ---- the byte-compat emitter (run once per tree, in a subprocess) -------------------------


def _norm_report(report: Any, *replace: tuple[str, str]) -> str:
    data = report.to_dict()
    data.pop("timings", None)
    text = json.dumps(data, indent=2, default=str, sort_keys=True)
    for old, new in replace:
        text = text.replace(old, new)
    # a note says how long a layer took ("in 8.6s"): a time, not an output
    return re.sub(r"\bin \d+(?:\.\d+)?s\b", "in <T>s", text)


def emit_bc(tree: Path, parts: set[str] | None = None) -> dict[str, str]:
    """The sha256 of every output the byte-compat promise names, computed by ``tree``'s own
    code (the process must run with ``PYTHONPATH=<tree>`` and the tree as working directory).
    ``parts`` narrows it (extract, model, select, cli, mcp, specs, report, check)."""
    out: dict[str, str] = {}

    def want(p: str) -> bool:
        return parts is None or p in parts

    sys.path.insert(0, str(tree))
    os.chdir(tree)
    fixtures = tree / "tests" / "gocheck" / "fixtures"
    dirs = [fixtures / n for n in GOCHECK_FIXTURES]
    goapp = tree / "tests" / "spec" / "goapp"
    # a Gin module that imports a gRPC client only (the fixture is this spike's: copy it in)
    client_only = REPO / "tests" / "grpc" / "fixtures" / "gin_client_only"
    scratch = Path(tempfile.mkdtemp(prefix="lattice-bc-"))
    shutil.copytree(client_only, scratch / "gin_client_only")
    if want("extract"):
        from lattice.gocheck import build_gocheck

        binary = str(build_gocheck())
        for d in [*dirs, goapp, scratch / "gin_client_only"]:
            proc = subprocess.run(
                [binary, "extract"],
                input=json.dumps({"root": str(d)}),
                capture_output=True,
                text=True,
                check=True,
            )
            out[f"extract:{d.name}"] = sha(proc.stdout)
    if want("model") or want("select"):
        from lattice.importer.frontends import select
        from lattice.importer.frontends.go_gin import GoGinFrontEnd

        for d in [*dirs, scratch / "gin_client_only"]:
            if want("model"):
                try:
                    out[f"model:{d.name}"] = sha(
                        GoGinFrontEnd().extract(d.parent, d.name).to_json()
                    )
                except Exception as err:  # noqa: BLE001 -- an error is an output too
                    out[f"model:{d.name}"] = f"error:{type(err).__name__}"
            if want("select"):
                try:
                    out[f"select:{d.name}"] = select(None, d.parent, d.name).name
                except LookupError:
                    out[f"select:{d.name}"] = "none"
    env = {**os.environ, "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1", "COLUMNS": "100"}
    if want("cli"):
        for name, argv in (
            ("lattice --help", ["-m", "lattice.cli", "--help"]),
            ("spec check --help", ["-m", "lattice.spec", "check", "--help"]),
            ("spec lint --help", ["-m", "lattice.spec", "lint", "--help"]),
            ("importer --help", ["-m", "lattice.importer", "--help"]),
        ):
            p = subprocess.run(
                [sys.executable, *argv],
                capture_output=True,
                text=True,
                env=env,
                cwd=tree,
                check=False,
            )
            out[f"cli:{name}"] = sha(f"{p.returncode}\n{p.stdout}\n{p.stderr}")
    if want("mcp"):
        msgs = [MCP_INIT, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "lattice.mcp",
                "serve",
                "--project",
                str(tree / "dogfood" / "studio"),
            ],
            input="\n".join(json.dumps(m) for m in msgs) + "\n",
            capture_output=True,
            text=True,
            env=env,
            cwd=tree,
            check=False,
        )
        out["mcp:initialize+tools/list"] = sha(p.stdout)
        out["mcp:returncode"] = str(p.returncode)
    if want("specs"):
        out.update(_spec_outputs(tree))
    if want("report"):
        out.update(_report_outputs(tree, scratch))
    if want("check"):
        out.update(_check_outputs(tree, scratch))
    shutil.rmtree(scratch, ignore_errors=True)
    return out


def _spec_outputs(tree: Path) -> dict[str, str]:
    import importlib
    import importlib.util

    from lattice.spec import validate
    from lattice.spec.authoring import requirements_markdown
    from lattice.spec.lint import lint, render
    from lattice.spec.review import _build_access, review_markdown
    from lattice.spec.rules import Trace

    out: dict[str, str] = {}
    specs: list[tuple[str, Any, Trace]] = []
    empty = Trace(source="", rules=[])
    for mod in (
        "minishop",
        "go_harness",
        "services_http",
        "services_worker",
        "racy",
        "multidb_app",
        "incremental_toy",
        "shop",
    ):
        try:
            m = importlib.import_module(f"tests.spec.{mod}")
        except Exception as err:  # noqa: BLE001
            out[f"spec:{mod}"] = f"import-error:{type(err).__name__}"
            continue
        obj = getattr(m, "SPEC", None)
        if obj is not None and not callable(obj):
            specs.append((mod, obj, empty))
    for name, path in (
        ("studio", tree / "dogfood/studio/spec.py"),
        ("impact_base", tree / "tests/spec/fixtures/impact/base/spec.py"),
        ("implementation", tree / "tests/spec/fixtures/implementation/fixture_spec.py"),
    ):
        trace_file = path.with_name("spec.trace.json") if path.name == "spec.py" else None
        if path.name == "fixture_spec.py":
            trace_file = None
        try:
            spec_ = importlib.util.spec_from_file_location(f"bc_{name}", path)
            module = importlib.util.module_from_spec(spec_)  # type: ignore[arg-type]
            sys.path.insert(0, str(path.parent))
            try:
                spec_.loader.exec_module(module)  # type: ignore[union-attr]
            finally:
                sys.path.remove(str(path.parent))
            obj = module.SPEC
            trace = (
                Trace.from_json(trace_file.read_text())
                if trace_file and trace_file.exists()
                else empty
            )
            specs.append((name, obj, trace))
        except Exception as err:  # noqa: BLE001
            out[f"spec:{name}"] = f"import-error:{type(err).__name__}"
    for name, spec, trace in specs:
        parts = [
            "\n".join(p.render() for p in validate(spec)),
            requirements_markdown(spec, title="t"),
            review_markdown(spec, trace, title="t"),
            repr(
                [
                    (g.prefix, g.columns, g.rows, sorted(g.cells.items()))
                    for g in _build_access(spec)
                ]
            ),
        ]
        try:
            parts.append(render(lint(spec, trace)))
        except Exception as err:  # noqa: BLE001
            parts.append(f"lint-error:{type(err).__name__}")
        out[f"spec:{name}"] = sha("\x00".join(parts))
    return out


def _report_outputs(tree: Path, scratch: Path) -> dict[str, str]:
    import copy

    from lattice.importer.inventory import capture
    from lattice.spec import appmap
    from lattice.spec import impact_report as ir
    from lattice.spec import implementation as impl
    from lattice.spec.draft import load_spec  # noqa: F401 -- imported by the impact tests

    out: dict[str, str] = {}
    fx = tree / "tests/spec/fixtures/implementation"
    app = scratch / "implapp"
    shutil.copytree(fx / "app", app)
    extract = json.loads((fx / "extract.json").read_text())
    wf = json.loads((fx / "workflows.json").read_text())
    inv_dir = scratch / "inv"
    wfp = scratch / "wf.json"
    for mode, label in ((None, "static"), ("observed", "observed")):
        doc = copy.deepcopy(wf)
        if mode:
            doc["run"]["mode"] = mode
            for f in doc["flows"]:
                f["status"] = "verified"
        wfp.write_text(json.dumps(doc))
        capture(app, inv_dir, extract=extract, workflows=wfp)
        inv = impl.load_inventory(inv_dir, app)
        text = (fx / "fixture_spec.py").read_text()
        spec_mod = {}
        exec(compile(text, "fixture_spec", "exec"), spec_mod)  # noqa: S102 -- the fixture's own
        spec, trace = spec_mod["SPEC"], spec_mod["trace"]()
        out[f"report:{label}"] = sha(impl.report_json(impl.build_report(spec, trace, inv)))
        out[f"report:{label}:no-trace"] = sha(impl.report_json(impl.build_report(spec, None, inv)))
        out[f"appmap:{label}"] = sha(appmap.map_json(appmap.build_appmap(spec, trace, inv)))
    impact = tree / "tests/spec/fixtures/impact"
    for change in (
        "reword",
        "status_changed",
        "new_request",
        "bad_invariant",
        "removed_rule",
        "conflict",
    ):
        try:
            inp = ir.build_inputs(
                project=impact / "base", candidate=impact / change, inventory=None
            )
            out[f"impact:{change}"] = sha(
                ir.report_json(ir.build_report(inp, tiers="2", baseline="none"))
            )
        except Exception as err:  # noqa: BLE001
            out[f"impact:{change}"] = f"error:{type(err).__name__}"
    return out


def _check_outputs(tree: Path, scratch: Path) -> dict[str, str]:
    from lattice.spec import check
    from tests.spec import go_harness, minishop

    out: dict[str, str] = {}
    root = scratch / "shops"
    root.mkdir()
    for variant in ("ok", *minishop.BROKEN):
        app_dir, package = minishop.write_app(root, variant)
        report = check(
            minishop.SPEC,
            minishop.app_factory(package),
            minishop.build_db,
            layers="CD",
            app_dir=app_dir,
            package=package,
            login=minishop.login,
        )
        out[f"check:minishop:{variant}"] = sha(
            _norm_report(report, (str(app_dir), "<APP>"), (package, "<PKG>"))
        )
    goapp = tree / "tests/spec/goapp"
    binary = scratch / "goapp"
    proc = subprocess.run(
        ["go", "build", "-o", str(binary), "."],
        cwd=goapp,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GOTOOLCHAIN": "auto"},
    )
    if proc.returncode != 0:
        out["check:go_harness"] = "build-error"
        return out
    os.environ[go_harness.BINARY_ENV] = str(binary)
    report = check(
        go_harness.SPEC,
        go_harness.make_app,
        go_harness.build_db,
        layers="DCX",
        runtime=go_harness.RUNTIME,
    )
    out["check:go_harness"] = sha(_norm_report(report))
    return out


# ---- the runner -----------------------------------------------------------------------------


class Probe:
    """One probe's record: id, prediction, observation, verdict."""

    def __init__(self, pid: str, prediction: str, rule: str) -> None:
        self.id, self.prediction, self.rule = pid, prediction, rule
        self.observed: Any = None
        self.status = "NOT RUN"  # PASS | FAIL | NOT RUN
        self.detail: Any = None

    def done(self, ok: bool, observed: Any, detail: Any = None) -> Probe:
        self.status, self.observed, self.detail = ("PASS" if ok else "FAIL"), observed, detail
        return self

    def not_run(self, reason: str) -> Probe:
        self.status, self.observed = "NOT RUN", reason
        return self

    def row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prediction": self.prediction,
            "pass_rule": self.rule,
            "status": self.status,
            "observed": self.observed,
            "detail": self.detail,
        }


def run_pytest(
    nodes: list[str],
    extra: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path = REPO,
    timeout: int = 1800,
) -> dict[str, Any]:
    """Run pytest on ``nodes``; the passed / failed counts and the failing ids."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *nodes,
        "-q",
        "-p",
        "no:cacheprovider",
        "-rfE",
        *(extra or []),
    ]
    t0 = time.perf_counter()
    p = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
        timeout=timeout,
    )
    text = p.stdout + p.stderr
    summary = re.findall(
        r"(\d+) (passed|failed|error|errors|skipped|deselected)",
        text.splitlines()[-1] if text.strip() else "",
    )
    counts = {k.rstrip("s") if k.startswith("error") else k: int(n) for n, k in summary}
    failing = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", text, re.M)))
    return {
        "returncode": p.returncode,
        "counts": counts,
        "failing": failing,
        "seconds": round(time.perf_counter() - t0, 1),
        "tail": text[-1500:],
    }


def arm0(ctx: dict[str, Any]) -> list[Probe]:
    sys.path.insert(0, str(REPO))
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from lattice.spec.grpc import Bridge
    from tests.grpc import oracle, test_sites

    fe = GoGrpcFrontEnd()
    n = fe.extract_json(oracle.FIXTURES, "notes")
    t = fe.extract_json(oracle.FIXTURES, "protos")
    truth = oracle.load(oracle.NOTES / "truth.json")
    probes: list[Probe] = []

    # P0-PARSE
    p = Probe("P0-PARSE", "supported constructs 100% equal; 0 disagreements", "100% equal")
    bad: list[str] = []
    compared = {"services": 0, "rpcs": 0, "messages": 0, "fields": 0, "enums": 0}
    with Bridge() as bridge:
        for label, ex, root, paths in (
            ("N", n, oracle.NOTES, [oracle.NOTES / "proto"]),
            ("T", t, oracle.PROTOS, [oracle.PROTOS]),
        ):
            files = [root / f["file"] for f in ex["grpc"]["protos"]]
            desc = bridge.describe(files, [root, *paths])["grpc"]
            bad += [f"{label}: {d}" for d in oracle.compare_parse(ex["grpc"], desc)]
            view = oracle.contract_view(ex["grpc"], oracle.UNTRUSTED)
            compared["services"] += len(view["services"])
            compared["rpcs"] += sum(len(v) for v in view["services"].values())
            compared["messages"] += len(view["messages"])
            compared["fields"] += sum(len(v) for v in view["messages"].values())
            compared["enums"] += len(view["enums"])
    probes.append(p.done(not bad, {"disagreements": len(bad), "compared": compared}, bad))

    # P0-UNREAD
    expected = oracle.load(oracle.PROTOS / "expected_unread.json")
    bad = oracle.compare_unread(t, expected)
    total = len(expected["unread"]) + len(expected["listed"])
    probes.append(
        Probe("P0-UNREAD", "10/10 (p)", "exact").done(not bad, f"{total - len(bad)}/{total}", bad)
    )

    # P0-VAL
    bad = oracle.compare_bounds(t, oracle.load(oracle.PROTOS / "expected_bounds.json"))
    bad += [d for d in oracle.compare_extract(n, truth) if "bounds" in d]
    probes.append(
        Probe("P0-VAL", "exact", "exact").done(
            not bad, {"T fields": 18, "N fields": 4, "mismatches": len(bad)}, bad
        )
    )

    # P0-EXT
    frozen = ctx.get("truth_sha")
    now = sha((oracle.NOTES / "truth.json").read_bytes())
    bad = oracle.compare_extract(n, truth)
    if frozen and frozen != now:
        bad.append(f"truth.json changed after the freeze: {frozen} -> {now}")
    probes.append(
        Probe("P0-EXT", "exact on N", "exact").done(
            not bad, {"rpcs": len(truth["rpcs"]), "mismatches": len(bad), "truth_sha256": now}, bad
        )
    )

    # P0-MODEL
    model = fe.extract(oracle.FIXTURES, "notes")
    rpcs = [r for r in model.routes if r.transport == "grpc"]
    obs = {
        "routes": len(model.routes),
        "rpc routes": len(rpcs),
        "bindings on rpcs": sum(len(r.http_bindings) for r in rpcs),
        "all RPC /pkg.Svc/Method": all(
            r.method == "RPC" and r.path.startswith("/notes.v1.") for r in rpcs
        ),
        "would be, with bindings as routes": len(model.routes) + 2,
    }
    ok = (
        obs["routes"] == truth["counts"]["model_endpoints"]
        and obs["bindings on rpcs"] == 2
        and obs["all RPC /pkg.Svc/Method"]
        and obs["would be, with bindings as routes"]
        == truth["counts"]["routes_if_bindings_were_routes"]
    )  # noqa: E501
    probes.append(
        Probe(
            "P0-MODEL",
            "9 endpoints, not 11 (pre-registration; its own shape gives 11, not 13)",
            "exact",
        ).done(ok, obs)
    )

    # pytest-backed probes
    for pid, pred, nodes in (
        ("P0-SPEC", "100%", ["tests/grpc/test_spec_rpc.py"]),
        ("P0-EMIT", "as stated", ["tests/grpc/test_extract_notes.py", "-k", "candidate_spec"]),
    ):
        r = run_pytest(nodes[:1], nodes[1:])
        probes.append(
            Probe(pid, pred, "all pass").done(
                r["returncode"] == 0, {**r["counts"], "seconds": r["seconds"]}, r["failing"]
            )
        )

    # P0-SITES: the baseline tree's sites equal the registered ones, and each has a passing test
    sites = _base_sites(ctx.get("base"))
    registered = set(test_sites.SITE_IDS)
    r = run_pytest(["tests/grpc/test_sites.py"])
    passed = r["counts"].get("passed", 0)
    ok = (
        r["returncode"] == 0
        and (sites is None or sites == registered)
        and passed >= len(registered)
    )
    obs = {
        "sites at 6a318e5": None if sites is None else len(sites),
        "registered": len(registered),
        "tests passed": passed,
        "unregistered": None if sites is None else sorted(sites - registered),
        "stale": None if sites is None else sorted(registered - sites),
    }
    probes.append(
        Probe("P0-SITES", "49/49 (pre-registration count; 51 found)", "all sites").done(
            ok, obs, r["failing"]
        )
    )

    # P0-DET
    probes.append(det_probe(ctx))
    # P0-NW
    probes.append(nw_probe(ctx))
    # P0-BC
    probes.append(bc_probe(ctx))
    return probes


def _base_sites(base: Path | None) -> set[str] | None:
    if base is None:
        return None
    rx = re.compile(r"isinstance\([^)]*\b(Step|Await)\b")
    out = set()
    for p in sorted((base / "lattice").rglob("*.py")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if rx.search(line):
                out.add(f"{p.relative_to(base / 'lattice').as_posix()}:{i}")
    return out


_DET_SNIPPET = r"""
import hashlib, json, sys
from pathlib import Path
from lattice.importer import emit_spec
from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
from lattice.spec.grpc import Bridge
root = Path(sys.argv[1])
fe = GoGrpcFrontEnd()
out = {}
for pkg, paths in (("notes", ["proto"]), ("protos", ["."])):
    base = root / pkg
    ex = fe.extract_json(root, pkg)
    out[pkg + ":extract"] = json.dumps(ex, sort_keys=True)
    files = [base / p["file"] for p in ex["grpc"]["protos"]]
    with Bridge() as b:
        d = b.describe(files, [base, *[base / x for x in paths]])
    out[pkg + ":describe"] = json.dumps(d["grpc"], sort_keys=True) + d["descriptor_set"]
model = fe.extract(root, "notes")
out["notes:model"] = model.to_json()
from tests.grpc.fixtures.notes import spec as dev
spec = emit_spec.build_spec(model, dev.PROFILE, users=dev.USERS)
out["notes:spec"] = emit_spec.spec_source(spec, package="notes")
print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in out.items()}))
print(json.dumps(out), file=sys.stderr)
"""


def det_probe(ctx: dict[str, Any]) -> Probe:
    p = Probe("P0-DET", "identical; none; 10/10", "10/10")
    scratch = Path(ctx["scratch"])
    roots = []
    for name in ("det-a", "det-b"):
        root = scratch / name / "x" / "y"
        shutil.rmtree(scratch / name, ignore_errors=True)
        shutil.copytree(
            REPO / "tests/grpc/fixtures",
            root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        roots.append(root)
    results = []
    leaks: list[str] = []
    rx_abs = re.compile(r"/(home|tmp|Users|root)/[\w./-]+")
    rx_time = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
    rx_port = re.compile(r"127\.0\.0\.1:\d+|localhost:\d+")
    for i in range(10):
        root = roots[i % 2]
        seed = str(random.randrange(1, 2**31))
        proc = subprocess.run(
            [sys.executable, "-c", _DET_SNIPPET, str(root), str(root / "notes" / "spec.py")],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO)},
        )
        if proc.returncode != 0:
            results.append(("error", proc.stderr[-300:]))
            continue
        hashes = json.loads(proc.stdout.splitlines()[-1])
        results.append(hashes)
        texts = json.loads(proc.stderr.splitlines()[-1])
        for key, text in texts.items():
            scrubbed = text.replace(str(root), "")
            for rx, what in ((rx_abs, "absolute path"), (rx_time, "time"), (rx_port, "port")):
                m = rx.search(scrubbed)
                # a path in the spec's own protos or a package import path is not a leak
                if m and not (what == "absolute path" and m.group(0).startswith("/notes")):
                    leaks.append(f"{key}: {what} {m.group(0)!r}")
    ok_runs = [r for r in results if isinstance(r, dict)]
    same = len({json.dumps(r, sort_keys=True) for r in ok_runs}) == 1
    return p.done(
        len(ok_runs) == 10 and same and not leaks,
        {
            "identical runs": len(ok_runs) if same else 0,
            "of": 10,
            "roots": 2,
            "artifacts": sorted(ok_runs[0]) if ok_runs else [],
            "leaks": sorted(set(leaks))[:5],
        },
        [r for r in results if not isinstance(r, dict)],
    )


def _snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    snap = {}
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files + dirs):
            path = Path(dirpath) / name
            st = path.lstat()
            digest = sha(path.read_bytes()) if path.is_file() else "dir"
            snap[str(path.relative_to(root))] = (digest, st.st_mode, st.st_mtime_ns)
    return snap


def nw_probe(ctx: dict[str, Any]) -> Probe:
    p = Probe("P0-NW", "0 changed, 0 new, 0 calls", "all 0")
    scratch = Path(ctx["scratch"])
    shim = scratch / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    log = scratch / "claude-calls.log"
    log.write_text("")
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n')
    (shim / "claude").chmod(0o755)
    roots = [REPO / "tests/grpc/fixtures/notes", REPO / "tests/grpc/fixtures/protos"]
    before = {str(r): _snapshot(r) for r in roots}
    script = (
        "import sys; from pathlib import Path\n"
        "from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd\n"
        "from lattice.spec.grpc import Bridge\n"
        "fe = GoGrpcFrontEnd(); root = Path(sys.argv[1]); pkg = sys.argv[2]\n"
        "ex = fe.extract_json(root, pkg); m = fe.extract(root, pkg)\n"
        "files = [root / pkg / f['file'] for f in ex['grpc']['protos']]\n"
        "with Bridge() as b: b.describe(files, [root / pkg, root / pkg / 'proto'])\n"
    )
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "PYTHONPATH": str(REPO)}
    for _ in range(10):
        for pkg in ("notes", "protos"):
            subprocess.run(
                [sys.executable, "-c", script, str(REPO / "tests/grpc/fixtures"), pkg],
                check=True,
                env=env,
                cwd=REPO,
                capture_output=True,
            )
    after = {str(r): _snapshot(r) for r in roots}
    changed = [
        k for r in roots for k in before[str(r)] if before[str(r)].get(k) != after[str(r)].get(k)
    ]
    new = [k for r in roots for k in after[str(r)] if k not in before[str(r)]]
    calls = len([ln for ln in log.read_text().splitlines() if ln.strip()])
    return p.done(
        not changed and not new and calls == 0,
        {
            "roots": ["notes (N)", "protos (T)", "held-out: NOT RUN"],
            "changed": len(changed),
            "new": len(new),
            "claude calls": calls,
            "import x10 / describe x10": True,
        },
    )


def bc_probe(ctx: dict[str, Any]) -> Probe:
    p = Probe("P0-BC", "all identical to git archive 6a318e5", "all")
    base = ctx.get("base")
    if base is None:
        return p.not_run("no --base: the baseline tree (git archive 6a318e5) was not given")
    scratch = Path(ctx["scratch"])

    def emit(tree: Path, cache: str) -> dict[str, str]:
        env = {
            **os.environ,
            "PYTHONPATH": str(tree),
            "LATTICE_GOCHECK_CACHE": str(scratch / cache),
            "LATTICE_GRPCBRIDGE_CACHE": str(scratch / "bridgecache"),
            "GOTOOLCHAIN": "auto",
        }
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "emit-bc", str(tree)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tree,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"emit-bc on {tree} failed:\n{proc.stderr[-2000:]}")
        return json.loads(proc.stdout.splitlines()[-1])  # type: ignore[no-any-return]

    a, b = emit(Path(base), "gocheck-base"), emit(REPO, "gocheck-new")
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    groups: dict[str, int] = {}
    for k in a:
        groups[k.split(":")[0]] = groups.get(k.split(":")[0], 0) + 1
    ctx["bc_hashes"] = a
    return p.done(not diff, {"compared": len(a), "by kind": groups, "differences": len(diff)}, diff)


# ---- Arm G ------------------------------------------------------------------------------------


def armg(ctx: dict[str, Any]) -> list[Probe]:
    sys.path.insert(0, str(REPO))
    probes: list[Probe] = []
    env = {"LATTICE_GRPCBRIDGE_CACHE": str(Path(ctx["scratch"]) / "bridgecache")}
    # one pytest run per test group, so a probe's failure names its tests
    groups = (
        ("PG-BR", "all", ["tests/grpc/test_runtime.py", "-k", "bridge"]),
        (
            "PG-REC",
            "8/8, 1/1, 1/1 (10/10 on the fixed shape)",
            ["tests/grpc/test_runtime.py", "-k", "reconcile"],
        ),
        (
            "PG-D",
            "0; 8/8 caught",
            [
                "tests/grpc/test_runtime.py",
                "-k",
                "dev_spec or deliberate_fault or code_expectations",
            ],
        ),
        (
            "PG-C",
            "caught; quiet",
            ["tests/grpc/test_runtime.py", "-k", "layer_c or streaming_scenario"],
        ),
        ("PG-J", "exact", ["tests/grpc/test_runtime.py", "-k", "message_form or expect_message"]),
        ("PG-MIX", "agree", ["tests/grpc/test_runtime.py", "-k", "gateway or mixes"]),
        (
            "PG-AUTH",
            "100%",
            ["tests/grpc/test_runtime.py", "-k", "access_matrix or extracted_auth"],
        ),
        ("PG-WF", "3-6 flows (p); 0", ["tests/grpc/test_runtime.py", "-k", "flows"]),
        ("PG-LEAK", "0", ["tests/grpc/test_runtime.py", "-k", "nothing_is_left"]),
    )
    for pid, pred, args in groups:
        r = run_pytest(args[:1], args[1:], env=env)
        probes.append(
            Probe(pid, pred, "all pass").done(
                r["returncode"] == 0, {**r["counts"], "seconds": r["seconds"]}, r["failing"]
            )
        )
    probes[2].observed = {**probes[2].observed, **_pg_d_table()}
    probes[7].observed = {**probes[7].observed, **_pg_wf_table()}
    probes.append(pg_x(ctx))
    probes.append(
        Probe("PG-HO", "section 5", "section 5").not_run(
            "the held-out app is authored by a separate agent (Amendment 1, Q7) and scored once "
            "by the orchestrator after the freeze; none was given to this node"
        )
    )
    return probes


def _pg_d_table() -> dict[str, Any]:
    from lattice.spec import check
    from tests.grpc.fixtures.notes import harness
    from tests.grpc.fixtures.notes import spec as dev

    rows = {}
    ok = check(dev.SPEC, harness.make_app, harness.build_db, layers="CD", runtime=harness.RUNTIME)
    rows["ok findings"] = len(ok.findings)
    caught = {}
    for bug, scenario in dev.BUG_SCENARIO.items():
        rep = check(
            dev.SPEC, harness.bugged(bug), harness.build_db, layers="CD", runtime=harness.RUNTIME
        )
        d = sorted({f.rule.removeprefix("scenario: ") for f in rep.findings if f.layer == "D"})
        c = sorted({f.case for f in rep.findings if f.layer == "C"})
        caught[bug] = {
            "layer D scenarios": d,
            "named": scenario,
            "localised": d == [scenario],
            "layer C": c,
        }
    rows["bugs"] = caught
    rows["localised"] = f"{sum(v['localised'] for v in caught.values())}/{len(caught)}"
    rows["scenarios in the dev spec"] = len(dev.SPEC.scenarios)
    return rows


def _pg_wf_table() -> dict[str, Any]:
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from lattice.spec.grpc import run_rpc_flows
    from tests.grpc import oracle
    from tests.grpc.fixtures.notes import harness
    from tests.grpc.fixtures.notes import spec as dev

    ex = GoGrpcFrontEnd().extract_json(oracle.FIXTURES, "notes")
    flows = run_rpc_flows(ex, dev.SPEC, harness.make_app, harness.build_db, runtime=harness.RUNTIME)
    kinds: dict[str, dict[str, int]] = {}
    for f in flows:
        kinds.setdefault(f["kind"], {}).setdefault(f["status"], 0)
        kinds[f["kind"]][f["status"]] += 1
    return {
        "flows recovered": len(flows),
        "by kind and status": kinds,
        "verified": sum(f["status"] == "verified" for f in flows),
        "predicted range 3-6": "missed" if not 3 <= len(flows) <= 6 else "held",
    }


def pg_x(ctx: dict[str, Any]) -> Probe:
    from lattice.spec import check
    from lattice.spec.grpc import GrpcTransport, bearer
    from tests.grpc.fixtures.notes import harness
    from tests.grpc.fixtures.notes import spec as dev

    p = Probe("PG-X", "0.5-3 ms; 3-12 s; 20-90 s (p)", "<= 5 ms; <= 30 s; <= 180 s")
    # a cold bridge build: an empty bridge cache with Go's build cache warm, then with Go's build
    # cache empty too (the module cache stays: the proxy is reachable, -mod=readonly)
    from lattice.spec import grpc as g

    def timed_build(cache: Path, gocache: Path | None) -> float:
        shutil.rmtree(cache, ignore_errors=True)
        saved = {k: os.environ.get(k) for k in ("LATTICE_GRPCBRIDGE_CACHE", "GOCACHE")}
        os.environ["LATTICE_GRPCBRIDGE_CACHE"] = str(cache)
        if gocache is not None:
            shutil.rmtree(gocache, ignore_errors=True)
            os.environ["GOCACHE"] = str(gocache)
        try:
            t0 = time.perf_counter()
            g.build_bridge()
            return time.perf_counter() - t0
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    cold = timed_build(Path(ctx["scratch"]) / "cold-bridge", None)
    cold_all = timed_build(
        Path(ctx["scratch"]) / "cold-bridge2", Path(ctx["scratch"]) / "cold-gocache"
    )
    harness.binary()
    with tempfile.TemporaryDirectory() as tmp:
        app = harness.make_app(harness.build_db(Path(tmp) / "x.db"))
        with harness.RUNTIME.open(app) as t:
            rpc: GrpcTransport = t.rpc()
            rpc.call("notes.v1.Notes", "ListNotes", {}, bearer("tok-ann"))
            times = []
            for _ in range(200):
                t1 = time.perf_counter()
                rpc.call("notes.v1.Notes", "ListNotes", {}, bearer("tok-ann"))
                times.append((time.perf_counter() - t1) * 1000)
        harness.RUNTIME.close(app)
    spec_times = []
    for _ in range(3):
        t1 = time.perf_counter()
        check(dev.SPEC, harness.make_app, harness.build_db, layers="CD", runtime=harness.RUNTIME)
        spec_times.append(time.perf_counter() - t1)
    obs = {
        "bridge call p50 (ms)": round(statistics.median(times), 3),
        "bridge call p95 (ms)": round(sorted(times)[int(len(times) * 0.95)], 3),
        "dev spec, layers CD (s, median of 3)": round(statistics.median(spec_times), 2),
        "cold bridge build, Go build cache warm (s)": round(cold, 1),
        "cold bridge build, Go build cache empty (s)": round(cold_all, 1),
    }
    ok = (
        obs["bridge call p50 (ms)"] <= 5
        and obs["dev spec, layers CD (s, median of 3)"] <= 30
        and cold_all <= 180
    )
    return p.done(ok, obs)


# ---- Arm H: the held-out app (prereg section 5; scored once) ---------------------------------
#
# The scorer is written and hashed BEFORE the held-out ``truth.json`` is opened. It reads the
# truth tolerantly (field names are normalised; a shape it cannot read is a runner problem,
# reported as such, never a score), applies the section 5 rules, and tunes nothing. The truth
# is loaded only by ``heldout_truth`` below, after ``verify_freeze`` passed.

HO_PRED = {
    "HO-RPC": "recall 0.90-1.00 / precision 0.95-1.00 (p)",
    "HO-CODES": "recall 0.70-0.90 / precision 0.90-1.00 (p)",
    "HO-AUTH": "0 wrong; unclear share 0-0.3 (p)",
    "HO-TABLES": "recall 1.0 (p)",
    "HO-REC": "100% (p)",
    "HO-FLOWS": "all truth flows pass; 0 verified-but-failing (p)",
    "HO-DET": "10/10 identical (p)",
}
HO_RULE = {
    "HO-RPC": "recall >= 0.90 and precision >= 0.95 (rpc name AND state must match)",
    "HO-CODES": "micro recall >= 0.70 and micro precision >= 0.90",
    "HO-AUTH": "0 rpcs labelled Public/Member that contradict truth (hard); unclear reported",
    "HO-TABLES": "recall >= 0.9",
    "HO-REC": (
        "100%: implemented answered, unimplemented / unregistered / absent give UNIMPLEMENTED"
    ),
    "HO-FLOWS": (
        "0 verified-but-failing recovered flows; every truth flow replayed as Rpc scenarios "
        "passes; recall reported"
    ),
    "HO-DET": "10/10 identical, no absolute path, time or port",
}


def _check_sha_file(listing: Path, base: Path) -> list[str]:
    """Problems found checking a ``sha256sum`` listing (paths relative to ``base``)."""
    bad: list[str] = []
    for line in listing.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        f = base / rel.strip()
        if not f.is_file():
            bad.append(f"missing {rel}")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            bad.append(f"changed {rel}")
    return bad


def verify_freeze(heldout: Path, lib_freeze: Path | None) -> dict[str, Any]:
    """The held-out files against its ``freeze.sha256``, and the frozen library files against
    the build node's listing. Scoring is refused when either differs."""
    out: dict[str, Any] = {}
    fz = heldout / "freeze.sha256"
    out["heldout_freeze_file_sha256"] = sha(fz.read_bytes())
    out["heldout_problems"] = _check_sha_file(fz, heldout)
    out["heldout_files"] = sum(1 for ln in fz.read_text().splitlines() if ln.strip())
    if lib_freeze is not None:
        out["library_problems"] = _check_sha_file(lib_freeze, REPO)
        out["library_files"] = sum(1 for ln in lib_freeze.read_text().splitlines() if ln.strip())
    out["ok"] = not out["heldout_problems"] and not out.get("library_problems")
    return out


def _full(name: Any, service: Any = None) -> str:
    """``pkg.Service/Method`` from ``/pkg.Service/Method``, ``pkg.Service.Method`` or a
    (service, method) pair."""
    text = str(name).strip()
    if service:
        text = f"{str(service).strip().strip('/')}/{text}"
    text = text.lstrip("/")
    if "/" not in text and "." in text:
        head, _, tail = text.rpartition(".")
        text = f"{head}/{tail}"
    return text


def _state(value: Any) -> str:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("unregistered", "not_registered", "notregistered", "not_served", "unserved"):
        return "not_registered"
    if "stream" in v:
        return "streaming"
    return v


def _code_names(value: Any) -> set[str]:
    names = set()
    for c in value or ():
        raw = c if isinstance(c, str) else (c.get("code") or c.get("name") or "")
        name = str(raw).strip().upper().replace("CODES.", "")
        if name and name != "OK":
            names.add(name)
    return names


def _auth(value: Any) -> str:
    v = str(value or "").strip()
    return {"public": "Public", "member": "Member"}.get(v.lower(), v)


def _first(d: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def heldout_truth(heldout: Path) -> dict[str, Any]:
    """``truth.json`` normalised: rpcs {full: {state, codes, auth}}, tables, flows."""
    raw = json.loads((heldout / "deskd" / "truth.json").read_text())
    rpcs_raw = _first(raw, "rpcs", "methods", default={})
    entries: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(rpcs_raw, Mapping):
        entries = [(_full(k), v) for k, v in rpcs_raw.items()]
    else:
        for v in rpcs_raw:
            entries.append(
                (
                    _full(
                        _first(v, "full_name", "rpc", "name", "id", default=""),
                        _first(v, "service") if "full_name" not in v and "rpc" not in v else None,
                    ),
                    v,
                )
            )
    rpcs = {
        k: {
            "state": _state(_first(v, "state", "status", default="")),
            "codes": _code_names(_first(v, "codes", "grpc_codes", default=())),
            "auth": _auth(_first(v, "auth", default="")),
        }
        for k, v in entries
    }
    tables_raw = _first(raw, "tables", default=[])
    if isinstance(tables_raw, Mapping):
        tables_raw = list(tables_raw)
    tables = {
        str(t if isinstance(t, str) else _first(t, "table", "name", default="")).lower()
        for t in tables_raw
    }
    flows = []
    for i, f in enumerate(_first(raw, "flows", default=[])):
        steps = []
        for st in _first(f, "steps", "rpcs", "calls", default=[]):
            if isinstance(st, str):
                steps.append({"rpc": _full(st), "user": None, "message": {}, "code": "OK"})
                continue
            rpc = _first(st, "rpc", "call", "method", "name", default="")
            svc = _first(st, "service") if "/" not in str(rpc) else None
            msg = _first(st, "request", "message", "req", "body", "input", "args", default={})
            code = _first(
                st, "expect", "code", "expect_code", "expected_code", "expected", default="OK"
            )
            if isinstance(code, Mapping):
                code = _first(code, "code", default="OK")
            steps.append(
                {
                    "rpc": _full(rpc, svc),
                    "user": _first(st, "as", "user", "caller", "actor", "who", default=None),
                    "message": msg,
                    "code": str(code).upper(),
                }
            )
        flows.append({"id": str(_first(f, "id", "name", default=f"flow{i + 1}")), "steps": steps})
    return {"rpcs": rpcs, "tables": tables, "flows": flows, "keys": sorted(raw)}


def _load_harness(heldout: Path, scratch: Path) -> Any:
    import importlib.util

    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    os.environ["DESKD_READY"] = "grpc"
    path = heldout / "deskd" / "harness.py"
    spec = importlib.util.spec_from_file_location("heldout_deskd_harness", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["heldout_deskd_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ho_make_app(harness: Any, root: Path) -> Any:
    """The harness's ``make_app`` with the proto fields its constants name (it leaves them
    unset: the rpc transport needs them to describe the contract)."""
    import dataclasses

    def make(db: Path) -> Any:
        app = harness.make_app(db)
        return dataclasses.replace(
            app,
            protos=tuple(harness.PROTOS),
            proto_paths=tuple(harness.PROTO_PATHS),
            proto_root=root,
        )

    return make


def _prf(hit: int, truth_n: int, pred_n: int) -> tuple[float, float]:
    return (hit / truth_n if truth_n else 1.0, hit / pred_n if pred_n else 1.0)


def _ho_det(heldout: Path, scratch: Path) -> Probe:
    p = Probe("HO-DET", HO_PRED["HO-DET"], HO_RULE["HO-DET"])
    roots = [heldout]
    copy = scratch / "det-b" / "x" / "y"
    shutil.rmtree(scratch / "det-b", ignore_errors=True)
    shutil.copytree(heldout, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    roots.append(copy)
    snippet = r"""
import hashlib, json, sys
from pathlib import Path
from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
from lattice.spec.grpc import Bridge
root = Path(sys.argv[1])
fe = GoGrpcFrontEnd()
ex = fe.extract_json(root, "deskd")
out = {"extract": json.dumps(ex, sort_keys=True)}
base = root / "deskd"
files = [base / p["file"] for p in ex["grpc"]["protos"]]
with Bridge() as b:
    d = b.describe(files, [base, base / "proto"])
out["describe"] = json.dumps(d["grpc"], sort_keys=True) + d["descriptor_set"]
out["model"] = fe.extract(root, "deskd").to_json()
print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in out.items()}))
print(json.dumps(out), file=sys.stderr)
"""
    rx_abs = re.compile(r"/(home|tmp|Users|root)/[\w./-]+")
    rx_time = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
    rx_port = re.compile(r"127\.0\.0\.1:\d+|localhost:\d+")
    results: list[Any] = []
    leaks: list[str] = []
    for i in range(10):
        root = roots[i % 2]
        proc = subprocess.run(
            [sys.executable, "-c", snippet, str(root)],
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
            for rx, what in ((rx_abs, "absolute path"), (rx_time, "time"), (rx_port, "port")):
                m = rx.search(scrubbed)
                if m:
                    leaks.append(f"{key}: {what} {m.group(0)!r}")
    ok_runs = [r for r in results if isinstance(r, dict)]
    same = len({json.dumps(r, sort_keys=True) for r in ok_runs}) == 1
    return p.done(
        len(ok_runs) == 10 and same and not leaks,
        {
            "identical runs": len(ok_runs) if same else 0,
            "of": 10,
            "roots": 2,
            "leaks": sorted(set(leaks))[:5],
        },
        [r for r in results if not isinstance(r, dict)],
    )


def heldout(ctx: dict[str, Any]) -> list[Probe]:
    """Arm H: the seven section 5 metrics on the held-out app, scored once."""
    sys.path.insert(0, str(REPO))
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    root: Path = Path(ctx["heldout"]).resolve()
    scratch = Path(ctx["scratch"])
    os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch / "gocache"))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch / "bridgecache"))
    fe = GoGrpcFrontEnd()
    extract = fe.extract_json(root, "deskd")
    model = fe.extract(root, "deskd")
    truth = heldout_truth(root)
    grpc = extract["grpc"]
    got = {f"{s['name']}/{r['name']}": r for s in grpc["services"] for r in s["rpcs"]}
    ex_state = {k: _state(r.get("state")) for k, r in got.items()}
    probes: list[Probe] = []

    # 1. rpc recall / precision, state included
    t_pairs = {(k, v["state"]) for k, v in truth["rpcs"].items()}
    e_pairs = {(k, s) for k, s in ex_state.items()}
    hit = t_pairs & e_pairs
    rec, prec = _prf(len(hit), len(t_pairs), len(e_pairs))
    streams = {k for k, v in truth["rpcs"].items() if v["state"] == "streaming"}
    excluded = {x["path"].lstrip("/") for x in extract["excluded"] if x.get("kind") == "streaming"}
    route_keys = {f"{r.rpc.service}/{r.rpc.method}" for r in model.routes if r.rpc}
    probes.append(
        Probe("HO-RPC", HO_PRED["HO-RPC"], HO_RULE["HO-RPC"]).done(
            rec >= 0.90 and prec >= 0.95,
            {
                "recall": round(rec, 3),
                "precision": round(prec, 3),
                "truth rpcs": len(t_pairs),
                "extracted rpcs": len(e_pairs),
                "matched": len(hit),
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

    # 2. codes per rpc, micro-averaged
    tc = {
        (k, c) for k, v in truth["rpcs"].items() if v["state"] == "implemented" for c in v["codes"]
    }
    ec = {
        (k, c)
        for k, r in got.items()
        if ex_state[k] == "implemented"
        for c in _code_names(r.get("codes"))
    }
    crec, cprec = _prf(len(tc & ec), len(tc), len(ec))
    probes.append(
        Probe("HO-CODES", HO_PRED["HO-CODES"], HO_RULE["HO-CODES"]).done(
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

    # 3. auth: a Public/Member label that contradicts truth is a hard failure
    wrong, unclear, checked = [], [], 0
    for k, v in truth["rpcs"].items():
        r = got.get(k)
        if r is None or ex_state[k] != "implemented" or v["state"] != "implemented":
            continue
        checked += 1
        label = _auth(r.get("auth"))
        if label not in ("Public", "Member"):
            unclear.append((k, label or "(none)", v["auth"]))
        elif label != v["auth"]:
            wrong.append((k, label, v["auth"]))
    probes.append(
        Probe("HO-AUTH", HO_PRED["HO-AUTH"], HO_RULE["HO-AUTH"]).done(
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

    # 4. tables recall
    et = {t["table"].lower() for t in extract["tables"]}
    trec, tprec = _prf(len(truth["tables"] & et), len(truth["tables"]), len(et))
    probes.append(
        Probe("HO-TABLES", HO_PRED["HO-TABLES"], HO_RULE["HO-TABLES"]).done(
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

    # 5. reconcile (PG-REC on H): the app's own binary, called with an empty message
    for pid, fn in (
        ("HO-REC", lambda: _ho_reconcile(root, scratch, extract, got, ex_state, truth)),
        ("HO-FLOWS", lambda: _ho_flows(root, scratch, extract, truth)),
        ("HO-DET", lambda: _ho_det(root, scratch)),
    ):
        try:
            probes.append(fn())
        except Exception as err:  # a runner failure is reported, never silently a pass
            probes.append(
                Probe(pid, HO_PRED[pid], HO_RULE[pid]).done(
                    False, "runner error", f"{type(err).__name__}: {str(err)[:600]}"
                )
            )
    return probes


def _ho_reconcile(
    root: Path, scratch: Path, extract: Any, got: Any, ex_state: Any, truth: Any
) -> Probe:
    from lattice.spec.grpc import bearer

    p = Probe("HO-REC", HO_PRED["HO-REC"], HO_RULE["HO-REC"])
    harness = _load_harness(root, scratch)
    make = _ho_make_app(harness, root / "deskd")
    answers: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="lattice-ho-rec-") as tmp:
        db = harness.build_db(Path(tmp) / "rec.db")
        app = make(db)
        try:
            with harness.RUNTIME.open(app) as transport:
                rpc = transport.rpc()
                first = next(iter(harness.USERS), None)
                can = harness.RUNTIME.auth.can_login and first is not None
                for k, r in sorted(got.items()):
                    if ex_state[k] == "streaming":
                        continue
                    svc, _, method = k.partition("/")
                    # a fresh login per call: SignOut (or any rpc) must not revoke the next one's
                    token = harness.RUNTIME.auth.token(transport, first) if can else None
                    caller = token if _auth(r.get("auth")) != "Public" else None
                    answers[k] = rpc.call(svc, method, {}, bearer(caller)).code
                absent = rpc.bridge.request(
                    {
                        "op": "probe",
                        "target": rpc.target,
                        "path": "/lattice.reconcile.v1.Absent/NoSuchRpc",
                    }
                )
                answers["(absent)"] = absent["code"]
        finally:
            harness.RUNTIME.close(app)
    classes: dict[str, list[bool]] = {
        "implemented": [],
        "unimplemented": [],
        "not_registered": [],
        "absent": [],
    }
    bad = []
    for k, code in answers.items():
        if k == "(absent)":
            ok, cls = code == "UNIMPLEMENTED", "absent"
        else:
            st = truth["rpcs"].get(k, {}).get("state", ex_state[k])
            cls = st if st in classes else "implemented"
            ok = (code != "UNIMPLEMENTED") if cls == "implemented" else code == "UNIMPLEMENTED"
        classes[cls].append(ok)
        if not ok:
            bad.append((k, code, cls))
    obs = {c: f"{sum(v)}/{len(v)}" for c, v in classes.items()}
    # an rpc of the truth never called (the extract lacks it) fails reconcile too
    uncalled = sorted(
        k for k, v in truth["rpcs"].items() if v["state"] != "streaming" and k not in answers
    )
    return p.done(
        not bad and not uncalled and all(classes[c] for c in ("implemented", "absent")),
        obs,
        {"wrong": bad, "truth rpcs not called (not in the extract)": uncalled, "codes": answers},
    )


def _ho_flows(root: Path, scratch: Path, extract: Any, truth: Any) -> Probe:
    from dataclasses import replace

    from lattice.profile import ApiProfile, Profile
    from lattice.spec import Rpc, Scenario, Spec, check
    from lattice.spec.grpc import _scenario_of, run_rpc_flows

    p = Probe("HO-FLOWS", HO_PRED["HO-FLOWS"], HO_RULE["HO-FLOWS"])
    harness = _load_harness(root, scratch)
    make = _ho_make_app(harness, root / "deskd")
    profile = Profile(
        schemas={},
        api=ApiProfile(
            tables={}, table_names={}, roles=frozenset({"Public", "Member"}), error_body="fastapi"
        ),
    )
    # validate() reads the protos relative to the working directory: name them absolutely
    protos = tuple(str(root / "deskd" / x) for x in harness.PROTOS)
    base = Spec(profile=profile, users=harness.USERS, protos=protos)

    def rpc_of(step: dict[str, Any]) -> Any:
        svc, _, method = step["rpc"].partition("/")
        return Rpc(svc, method, step["user"], step["message"], expect_code=step["code"])

    # truth flows, replayed one scenario each on a fresh database
    t_ok: dict[str, Any] = {}
    scenarios = tuple(
        Scenario(f["id"], steps=tuple(rpc_of(s) for s in f["steps"])) for f in truth["flows"]
    )
    rep = check(
        replace(base, scenarios=scenarios),
        make,
        harness.build_db,
        layers="D",
        runtime=harness.RUNTIME,
    )
    failed = {}
    for f in rep.findings:
        failed.setdefault(f.rule.removeprefix("scenario: "), []).append(f.details.get("problems"))
    for f in truth["flows"]:
        t_ok[f["id"]] = f["id"] not in failed
    # flows the importer recovers, verified and then replayed independently
    flows = run_rpc_flows(extract, base, make, harness.build_db, runtime=harness.RUNTIME)
    verified = [f for f in flows if f["status"] == "verified"]
    vscen = tuple(_scenario_of(f["id"], f["steps"]) for f in verified)
    vrep = check(
        replace(base, scenarios=vscen), make, harness.build_db, layers="D", runtime=harness.RUNTIME
    )
    vbad = sorted({f.rule.removeprefix("scenario: ") for f in vrep.findings})

    def names(steps: Any) -> list[str]:
        return [str(s["rpc"]).lstrip("/") if "/" in str(s["rpc"]) else str(s["rpc"]) for s in steps]

    def tail(x: str) -> str:
        return x.rsplit("/", 1)[-1]

    rec_seqs = [[tail(n) for n in names(f["steps"])] for f in verified]

    def subseq(a: list[str], b: list[str]) -> bool:
        it = iter(b)
        return all(x in it for x in a)

    exact, covered = 0, 0
    for f in truth["flows"]:
        t = [tail(s["rpc"]) for s in f["steps"]]
        exact += any(r == t for r in rec_seqs)
        covered += any(subseq(t, r) for r in rec_seqs)
    n = len(truth["flows"])
    return p.done(
        n > 0 and all(t_ok.values()) and not vbad,
        {
            "truth flows": n,
            "truth flows passing": sum(t_ok.values()),
            "recovered flows": len(flows),
            "recovered by status": {
                s: sum(f["status"] == s for f in flows)
                for s in sorted({f["status"] for f in flows})
            },
            "verified": len(verified),
            "verified-but-failing": len(vbad),
            "truth flows recovered exactly (rpc sequence equal)": f"{exact}/{n}",
            "truth flows covered (subsequence of a verified flow)": f"{covered}/{n}",
        },
        {
            "truth flow failures": failed,
            "verified-but-failing ids": vbad,
            "recovered kinds": sorted({f["kind"] for f in flows}),
        },
    )


def not_run_arms() -> list[Probe]:
    return [
        Probe("PM-S", "identical", "identical").not_run(
            "Arm M needs a Streak checkout (a0679e9, 1d00ede) and a Docker MySQL: neither is "
            "in this container"
        ),
        Probe("PM-SG", "as PG-HO's rules", "as PG-HO's rules").not_run(
            "Arm M: no Streak checkout, no Docker, and Streak's spec is not in this container"
        ),
        Probe("Arm L", "within <= 3 rounds", "CD check passes").not_run(
            "the build round is its own node I2 (Amendment 1, Q2); not started here"
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["emit-bc"]:
        parts = set(args_in[2].split(",")) if len(args_in) > 2 else None
        print(json.dumps(emit_bc(Path(args_in[1]).resolve(), parts), sort_keys=True))
        return 0
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--arm", choices=("0", "G", "H", "all"), default="all")
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--heldout", type=Path, default=None, help="Arm H: the held-out directory")
    ap.add_argument(
        "--lib-freeze",
        type=Path,
        default=REPO / "docs/results/data/2026-10-17-grpc/freeze-final.sha256",
        help="Arm H: the frozen library listing, checked before scoring",
    )
    ap.add_argument("--base", type=Path, default=None, help="git archive 6a318e5, extracted")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--truth-sha", type=Path, default=None, help="the frozen sha256sum of truth.json"
    )
    ap.add_argument("--make-baseline", action="store_true")
    args = ap.parse_args(args_in)
    if args.scratch is None:
        args.scratch = Path(tempfile.mkdtemp(prefix="grpc-probes-"))
    args.scratch.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.make_baseline:
        if args.base is None:
            ap.error("--make-baseline needs --base")
        env = {
            **os.environ,
            "PYTHONPATH": str(args.base),
            "LATTICE_GOCHECK_CACHE": str(args.scratch / "gocheck-base"),
            "GOTOOLCHAIN": "auto",
        }
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "emit-bc",
                str(args.base),
                "extract,model,select",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=args.base,
            check=True,
        )
        (args.out / "baseline-hashes.json").write_text(
            json.dumps(
                {"tree": "git archive 6a318e5", **json.loads(proc.stdout.splitlines()[-1])},
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        print(f"wrote {args.out / 'baseline-hashes.json'}")
        return 0
    ctx: dict[str, Any] = {"scratch": args.scratch, "base": args.base}
    if args.truth_sha:
        ctx["truth_sha"] = args.truth_sha.read_text().split()[0]
    probes: list[Probe] = []
    if args.arm == "H":
        if args.heldout is None:
            ap.error("--arm H needs --heldout")
        ctx["heldout"] = args.heldout
        frozen = verify_freeze(args.heldout.resolve(), args.lib_freeze)
        print("freeze:", json.dumps({k: v for k, v in frozen.items()}, default=str)[:600])
        if not frozen["ok"]:
            print("REFUSED: a frozen file differs; nothing is scored")
            return 2
        probes = heldout(ctx)
        doc = {
            "schema": "lattice.grpc-probes/1",
            "arm": "H",
            "scorer_sha256": sha(Path(__file__).read_bytes()),
            "freeze": frozen,
            "probes": [pr.row() for pr in probes],
        }
        (args.out / "probes-armh.json").write_text(json.dumps(doc, indent=1, default=str) + "\n")
        for pr in probes:
            print(f"{pr.status:8} {pr.id:10} {json.dumps(pr.observed, default=str)[:300]}")
        nfail = sum(pr.status == "FAIL" for pr in probes)
        print(f"{sum(pr.status == 'PASS' for pr in probes)} passed, {nfail} failed")
        return 1 if nfail else 0
    if args.arm in ("0", "all"):
        probes += arm0(ctx)
    if args.arm in ("G", "all"):
        probes += armg(ctx)
    if args.arm == "all":
        probes += not_run_arms()
    doc = {"schema": "lattice.grpc-probes/1", "arm": args.arm, "probes": [p.row() for p in probes]}
    name = {"0": "arm0", "G": "armg", "all": "all"}[args.arm]
    (args.out / f"probes-{name}.json").write_text(json.dumps(doc, indent=1, default=str) + "\n")
    for p in probes:
        print(f"{p.status:8} {p.id:10} {json.dumps(p.observed, default=str)[:200]}")
    failed = [p for p in probes if p.status == "FAIL"]
    print(
        f"{sum(p.status == 'PASS' for p in probes)} passed, {len(failed)} failed, "
        f"{sum(p.status == 'NOT RUN' for p in probes)} not run"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
