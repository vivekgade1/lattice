"""Probes of the implementation diff (docs/results/2026-10-15-implementation-diff-prereg.md,
section 3).

The ground truth below was written from the pre-registration, before any output of the report was
looked at: the 25 endpoints Streak's rules reach (read from the Go registrations, not from the
extract), the rules that have nothing to link, the 29 rules S4 requires but lacks, the queries and
their expected hints, the scripted replies and their expected labels. The independent recomputation
of PV re-derives the verified flags from the frozen ``workflows.json`` with its own code (it shares
nothing with ``lattice.spec.implementation`` but the spec objects). Nothing here reads the report to
decide what is expected.

Run (see the pre-registration, section 6; the MySQL arm needs the driver venv)::

    "$DRV" dogfood/implementation_probes.py --streak-ref a0679e9 --streak-ref2 1d00ede --runs 10 \\
        --workflows-dyn "$SCRATCH/wf/S-dyn.json" --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"

The held-out stage (``--held-out``) takes the app directories, the spec and the route truth as
arguments and is run by the orchestrator, once, never by the builder.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import importer_go_probes as G  # noqa: E402

REPO = Path(os.environ.get("IMPLEMENTATION_PROBES_REPO", HERE.parent)).resolve()
STREAK = G.STREAK
GO_ENV = G.GO_ENV
BASE_COMMIT = "454a8e6"  # the base of Spike C: the byte-compat reference (PB)
CONTAINER = "lattice-wf-"

# ---- ground truth (pre-registration, sections 1 and 3) ------------------------------------

H = "/v1/habits/{}"
# the 25 endpoints Streak's rules reach: every route Streak registers except PUT /test/clock,
# read from the Go registrations (internal/*/routes.go, internal/httpapi/router.go)
S_ENDPOINTS = frozenset(
    {
        "GET /health",
        "GET /v1/me",
        "PUT /v1/me",
        "DELETE /v1/me",
        "POST /v1/devices",
        "DELETE /v1/devices/{}",
        "POST /v1/auth/register",
        "POST /v1/auth/login",
        "POST /v1/auth/refresh",
        "POST /v1/auth/logout",
        f"POST {H}/completions",
        f"GET {H}/completions",
        f"DELETE {H}/completions/{{}}",
        "POST /v1/completions/batch",
        "POST /v1/habits",
        "GET /v1/habits",
        f"GET {H}",
        f"PATCH {H}",
        f"DELETE {H}",
        "GET /v1/home",
        "GET /v1/nudges",
        "POST /v1/nudges/{}/dismiss",
        f"GET {H}/progress",
        f"GET {H}/history",
        "GET /v1/sync",
    }
)
CLOCK = "PUT /test/clock"
# the six endpoints S4 (1d00ede) lacks (the pre-registration, section 1, premise 1)
S4_SPEC_ONLY = frozenset(
    {
        "GET /v1/home",
        "GET /v1/sync",
        "GET /v1/nudges",
        "POST /v1/nudges/{}/dismiss",
        "POST /v1/devices",
        "DELETE /v1/devices/{}",
    }
)
S_NO_ELEMENT = frozenset({"R1", "R2", "R6", "R15", "R136", "R142"})
S_TABLE_ONLY = frozenset({f"R{n}" for n in (35, 36, 37, 38, 40, 41, 42)})
S4_REQUIRED = frozenset(
    {f"R{n}" for n in (7, 21, 27, 28, 31, 32, 33, 34, 89, 90, 91, 92, 93, 94, 114, 125)}
    | {f"R{n}" for n in (127, 128, 129, 132, 133, 134, 135, 137, 138, 139, 140, 141, 143)}
)
S_MACHINE_RULES = frozenset({"R123", "R127"})

# PT: (id, project, question, expected hint)
PT_QUERIES = [
    ("PT1", "S", "check in a habit", "exists"),
    ("PT2", "S", "the home screen", "exists"),
    ("PT3", "S", "register a device", "exists"),
    ("PT4", "S4", "the home screen", "spec_only"),
    ("PT5", "S4", "register a device", "spec_only"),
    ("PT6", "S", "password reset", "no_match"),
    ("PT7", "S", "export CSV", "no_match"),
    ("PT8", "S", "list", "several"),
]

# PA (b): the ten scripted replies: (name, expected label, expected basis)
PA_EXPECT = {
    "verified_ok": ("implemented_verified", "observed"),
    "verified_without_code": ("implemented_unverified", "static"),
    "verified_code_not_evidence": ("implemented_unverified", "static"),
    "verified_cites_unverified_flow": ("implemented_unverified", "static"),
    "unverified_without_element": ("unsettled", None),
    "required_ok": ("required_not_implemented", "inferred"),
    "required_endpoints_present": ("unsettled", None),
    "ambiguous_two": ("ambiguous", "static"),
    "ambiguous_one": ("unsettled", None),
    "not_specified_not_implemented": ("not_specified_not_implemented", "inferred"),
}

# the PM-like mutations are in dogfood/implementation_mutants.py; the agent exercise's files:
AGENT_FILES = (
    "dogfood/implementation_agent.py",
    "dogfood/implementation_agent_scorer.py",
    "dogfood/implementation_agent_check.py",
    "dogfood/implementation_agent_scorer_cases.json",
)

SECRET_RE = re.compile(r"(/Users|/private|127\.0\.0\.1|localhost|lattice-wf-)")
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def result(pid: str, desc: str, predicted: Any, observed: Any, passed: bool | None, **ev: Any):
    return {
        "id": pid,
        "probe": desc,
        "ran": True,
        "predicted": predicted,
        "observed": observed,
        "pass": passed,
        **ev,
    }


def not_run(pid: str, desc: str, why: str) -> dict[str, Any]:
    return {"id": pid, "probe": desc, "ran": False, "reason": why, "pass": None}


# ---- small helpers ------------------------------------------------------------------------


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fresh_tmp(scratch: Path, label: str) -> Path:
    tmp = scratch / "tmp" / label
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    return tmp


def env_for(tmp: Path, pythonpath: Path | str | None = None) -> dict[str, str]:
    env = dict(os.environ, **GO_ENV)
    env.update(
        TMPDIR=str(tmp),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(pythonpath or REPO),
        NO_COLOR="1",
    )
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def snapshot(root: Path) -> dict[str, tuple]:
    """(kind, sha256, mode, mtime_ns) of every path under `root`, hidden files included."""
    out: dict[str, tuple] = {}
    for dp, dns, fns in os.walk(root):
        for name in sorted([*dns, *fns]):
            p = Path(dp) / name
            rel = p.relative_to(root).as_posix()
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", os.readlink(p), st.st_mode, st.st_mtime_ns)
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ("dir", "", st.st_mode, st.st_mtime_ns)
            else:
                out[rel] = ("file", sha(p.read_bytes()), st.st_mode, st.st_mtime_ns)
    return out


def diff_snapshots(a: dict[str, tuple], b: dict[str, tuple]) -> dict[str, list[str]]:
    changed = sorted(k for k in a if k in b and a[k] != b[k])
    return {
        "changed": changed,
        "new": sorted(k for k in b if k not in a),
        "gone": sorted(k for k in a if k not in b),
    }


def chmod_tree(root: Path, writable: bool) -> None:
    for dp, dns, fns in os.walk(root):
        for name in [*dns, *fns]:
            p = Path(dp) / name
            if p.is_symlink():
                continue
            mode = p.stat().st_mode
            os.chmod(p, (mode | 0o200) if writable else (mode & ~0o222))
    mode = root.stat().st_mode
    os.chmod(root, (mode | 0o200) if writable else (mode & ~0o222))


def wf_containers() -> list[str]:
    proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={CONTAINER}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(n for n in proc.stdout.split() if n)


def docker_available() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def run_py(
    args: Sequence[str],
    tmp: Path,
    *,
    cwd: Path | None = None,
    pythonpath: Path | str | None = None,
    stdin: str | None = None,
    timeout: int = 900,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = env_for(tmp, pythonpath)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or REPO,
        input=stdin,
        timeout=timeout,
        check=False,
    )


def tpl_key(route: str) -> str:
    """A ``METHOD /path`` with every ``{x}`` as ``{}`` (the runner's own, independent)."""
    return re.sub(r"\{[^}]*\}", "{}", route)


def load_clone_spec(clone: Path, name: str):
    """(Spec, Trace) of a project directory, as Studio loads them."""
    from lattice.spec.draft import load_spec  # noqa: PLC0415
    from lattice.spec.rules import Trace  # noqa: PLC0415

    spec, errs = load_spec((clone / "spec.py").read_text())
    assert spec is not None, errs
    return spec, Trace.from_json((clone / "spec.trace.json").read_text())


class World:
    """The scratch world: clones, workflows, inventories, specs."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scratch = Path(args.scratch).resolve()
        self.out = Path(args.out).resolve()
        self.setup_notes: list[str] = []
        self.s = self.scratch / "S"
        self.s4 = self.scratch / "S4"
        self.splus = self.scratch / "Splus"
        self.s2 = self.scratch / "S-root2"
        self.inv: dict[str, Path] = {}
        self.wf: dict[str, Path] = {}
        self.spec: dict[str, Any] = {}
        self.trace: dict[str, Any] = {}


# ---- setup ----------------------------------------------------------------------------------


def run_cli(args: Sequence[str], scratch: Path, label: str, timeout: int = 900):
    tmp = fresh_tmp(scratch, label)
    return run_py(["-m", "lattice.cli", *args], tmp, timeout=timeout)


def rewrite_go_src(clone: Path) -> str:
    """Premise 6: S's committed studio.toml points go_src at the owner's checkout. The probes
    point it at the clone before any snapshot; the rewrite is recorded."""
    p = clone / "studio.toml"
    text = p.read_text()
    new = re.sub(r'(?m)^go_src\s*=\s*"[^"]*"', f'go_src = "{clone}"', text)
    p.write_text(new)
    return (
        f"{clone.name}: studio.toml go_src rewritten to the clone" if new != text else "unchanged"
    )


def add_implementation_table(
    clone: Path, inventory: Path, app_root: Path, state_dir: Path | None = None
) -> None:
    """The [implementation] table (and, for a clone a Studio is built on, a [state] dir outside
    it: Studio's default state directory is inside the repository, and creating a Studio there
    would write `.lattice/state/` into the target -- not the read path under test)."""
    p = clone / "studio.toml"
    text = (
        p.read_text().rstrip("\n")
        + f'\n\n[implementation]\ninventory = "{inventory}"\napp_root = "{app_root}"\n'
    )
    if state_dir is not None:
        text += f'\n[state]\ndir = "{state_dir}"\n'
    p.write_text(text)


def capture(w: World, name: str, root: Path, workflows: Path | None) -> Path:
    out = w.scratch / "inv" / name
    shutil.rmtree(out, ignore_errors=True)
    cmd = ["import", "inventory", str(root), "--out", str(out)]
    if workflows is not None:
        cmd += ["--workflows", str(workflows)]
    r = run_cli(cmd, w.scratch, f"capture-{name}")
    assert r.returncode == 0, r.stderr
    w.inv[name] = out
    return out


def plant(clone: Path) -> None:
    """S+ (R4): a route no rule requires, in habits/routes.go, and a plumbing route on the
    engine in router.go (the pre-registration's `r.Public.GET("/metrics")` is written on the
    engine, as /health is: `r.Public` is the /v1 group and is not in scope in NewRouter)."""
    p = clone / "internal/habits/routes.go"
    t = p.read_text()
    old = '\tr.Member.DELETE("/habits/:id", httpapi.Handle(h.remove))\n'
    assert old in t
    p.write_text(t.replace(old, old + '\tr.Member.GET("/admin/stats", httpapi.Handle(h.list))\n'))
    p = clone / "internal/httpapi/router.go"
    t = p.read_text()
    old = (
        '\tr.GET("/health", func(c *gin.Context) '
        '{ c.JSON(http.StatusOK, gin.H{"status": "ok"}) })\n'
    )
    assert old in t
    p.write_text(
        t.replace(
            old,
            old + '\tr.GET("/metrics", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{}) })\n',
        )
    )


def setup(w: World, need_docker: bool) -> None:
    a = w.args
    w.scratch.mkdir(parents=True, exist_ok=True)
    w.out.mkdir(parents=True, exist_ok=True)
    (w.scratch / "wf").mkdir(exist_ok=True)
    G.archive(STREAK, a.streak_ref, w.s)
    G.archive(STREAK, a.streak_ref2, w.s4)
    G.archive(STREAK, a.streak_ref, w.splus)
    G.archive(STREAK, a.streak_ref, w.s2)
    w.setup_notes += [rewrite_go_src(w.s), rewrite_go_src(w.s4)]
    rewrite_go_src(w.splus)
    rewrite_go_src(w.s2)
    G.go_build(w.s)  # the runner's own build of the app: bin/streak-api (PF), as in Spike A
    plant(w.splus)
    # the workflows: S-dyn is Spike B's dynamic run, frozen with its sha256
    dyn = Path(a.workflows_dyn) if a.workflows_dyn else w.scratch / "wf" / "S-dyn.json"
    if not dyn.is_file():
        if not need_docker:
            raise SystemExit("no --workflows-dyn file and no Docker: S-dyn cannot be produced")
        r = run_cli(
            [
                "import",
                "workflows",
                str(w.s),
                "--out",
                str(w.scratch / "wf" / "S-dyn-run"),
                "--user",
                "ann",
                "--user",
                "ben",
            ],
            w.scratch,
            "wf-dyn",
            timeout=1800,
        )
        assert r.returncode == 0, r.stderr
        dyn.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(w.scratch / "wf" / "S-dyn-run" / "workflows.json", dyn)
        w.setup_notes.append("S-dyn produced by `import workflows` (one run) and frozen")
    w.wf["S-dyn"] = dyn
    for name, clone in (("S-stat", w.s), ("S4-stat", w.s4)):
        out = w.scratch / "wf" / f"{name}-run"
        shutil.rmtree(out, ignore_errors=True)
        r = run_cli(
            [
                "import",
                "workflows",
                str(clone),
                "--out",
                str(out),
                "--static",
                "--user",
                "ann",
                "--user",
                "ben",
            ],
            w.scratch,
            f"wf-{name}",
        )
        assert r.returncode == 0, r.stderr
        w.wf[name] = out / "workflows.json"
    capture(w, "S-dyn", w.s, w.wf["S-dyn"])
    capture(w, "S-stat", w.s, w.wf["S-stat"])
    capture(w, "S4-stat", w.s4, w.wf["S4-stat"])
    capture(w, "S-plus", w.splus, None)
    capture(w, "S-dyn-r2", w.s2, w.wf["S-dyn"])
    capture(w, "S-stat-r2", w.s2, w.wf["S-stat"])
    add_implementation_table(w.s, w.inv["S-dyn"], w.s, w.scratch / "state-S")
    add_implementation_table(w.s4, w.inv["S4-stat"], w.s4)
    add_implementation_table(w.splus, w.inv["S-plus"], w.splus)
    for key, clone in (("S", w.s), ("S4", w.s4)):
        w.spec[key], w.trace[key] = load_clone_spec(clone, key)
    w.spec["Splus"], w.trace["Splus"] = w.spec["S"], w.trace["S"]
    w.spec["S2"], w.trace["S2"] = load_clone_spec(w.s2, "S2")


def report(w: World, inv: str, root: Path, project: str, **kw: Any) -> dict[str, Any]:
    from lattice.spec.implementation import build_report, load_inventory  # noqa: PLC0415

    return build_report(w.spec[project], w.trace[project], load_inventory(w.inv[inv], root), **kw)


def rules_of(rep: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in rep["rules"]}


def ids(rep: dict[str, Any], status: str) -> set[str]:
    return {e["id"] for e in rep["rules"] if e["status"] == status}


def endpoints_by_status(rep: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for e in rep["endpoints"]:
        out.setdefault(e["status"], set()).add(e["key"])
    return out


# ---- PE: endpoint statuses against the hand-built truth ---------------------------------------


def probe_pe(w: World) -> dict[str, Any]:
    s = endpoints_by_status(report(w, "S-stat", w.s, "S"))
    s4 = endpoints_by_status(report(w, "S4-stat", w.s4, "S4"))
    want_s = {"implemented": set(S_ENDPOINTS), "test_only": {CLOCK}}
    want_s4 = {
        "implemented": set(S_ENDPOINTS) - S4_SPEC_ONLY,
        "spec_only": set(S4_SPEC_ONLY),
        "test_only": {CLOCK},
    }
    ok = s == want_s and s4 == want_s4
    obs = {
        "S": {k: len(v) for k, v in sorted(s.items())},
        "S4": {k: len(v) for k, v in sorted(s4.items())},
        "S_differs": {
            k: sorted(s.get(k, set()) ^ want_s.get(k, set()))
            for k in set(s) | set(want_s)
            if s.get(k, set()) != want_s.get(k, set())
        },
        "S4_differs": {
            k: sorted(s4.get(k, set()) ^ want_s4.get(k, set()))
            for k in set(s4) | set(want_s4)
            if s4.get(k, set()) != want_s4.get(k, set())
        },
    }
    return result(
        "PE",
        "endpoint statuses vs the hand-built truth (S and S4)",
        {
            "S": {"implemented": 25, "test_only": 1},
            "S4": {"implemented": 19, "spec_only": 6, "test_only": 1},
        },
        obs,
        ok,
    )


# ---- PR: rule statuses --------------------------------------------------------------------


def probe_pr(w: World) -> dict[str, Any]:
    s = report(w, "S-stat", w.s, "S")
    s4 = report(w, "S4-stat", w.s4, "S4")
    impl = {"implemented_verified", "implemented_unverified"}
    s_rules, s4_rules = rules_of(s), rules_of(s4)
    s_impl = {k for k, v in s_rules.items() if v["status"] in impl}
    s4_impl = {k for k, v in s4_rules.items() if v["status"] in impl}
    checks = {
        "S_required_empty": ids(s, "required_not_implemented") == set(),
        "S_no_element": ids(s, "no_element") == set(S_NO_ELEMENT),
        "S_implemented_142": len(s_impl) == 142,
        "S_table_only_unverified": all(
            s_rules[r]["status"] == "implemented_unverified" for r in S_TABLE_ONLY
        ),
        "S4_required_set": ids(s4, "required_not_implemented") == set(S4_REQUIRED),
        "S4_no_element": ids(s4, "no_element") == set(S_NO_ELEMENT),
        "S4_implemented_113": len(s4_impl) == 113,
        "S4_table_only_unverified": all(
            s4_rules[r]["status"] in impl for r in S_TABLE_ONLY if r in s4_rules
        ),
    }
    obs = {
        "S": {
            k: len(ids(s, k))
            for k in (
                "implemented_verified",
                "implemented_unverified",
                "required_not_implemented",
                "no_element",
            )
        },
        "S4": {
            k: len(ids(s4, k))
            for k in (
                "implemented_verified",
                "implemented_unverified",
                "required_not_implemented",
                "no_element",
            )
        },
        "S4_required_missing_from_prediction": sorted(
            ids(s4, "required_not_implemented") - S4_REQUIRED
        ),
        "S4_prediction_missing_from_report": sorted(
            S4_REQUIRED - ids(s4, "required_not_implemented")
        ),
        "S_no_element": sorted(ids(s, "no_element")),
        "S4_partial": sorted(
            r for r in ids(s4, "required_not_implemented") if s4_rules[r]["partial"]
        ),
        "checks": checks,
    }
    return result(
        "PR",
        "rule statuses (S and S4) as exact sets",
        {
            "S": "142 implemented, 0 required, no_element {R1,R2,R6,R15,R136,R142}",
            "S4": "29 required (listed), 113 implemented, same no_element",
        },
        obs,
        all(checks.values()),
    )


# ---- PV: verified flags against an independent recomputation --------------------------------


def indep_rule_routes(spec: Any, trace: Any, conditional: set[str]) -> dict[str, dict[str, Any]]:
    """The runner's own D4: per rule, the route keys, the machines, whether anything links."""
    from lattice.spec.model import Step  # noqa: PLC0415

    route_re = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`'\"),;]*)")

    def key(m: str, p: str) -> str:
        p = p.split("?")[0].rstrip(".:!?")
        p = re.sub(
            r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            "/{}",
            p,
        )
        p = re.sub(r"/\d+(?=/|$)", "/{}", p)
        p = re.sub(r"/:[^/]+", "/{}", p)
        p = re.sub(r"/\{[^}]*\}", "/{}", p)
        return f"{m} {p.rstrip('/') or '/'}"

    by_name = {s.name: s for s in spec.scenarios}
    out: dict[str, dict[str, Any]] = {}
    for rule in trace.rules:
        routes: set[str] = {key(m, p) for m, p in route_re.findall(rule.text)}
        machines: set[str] = set()
        for link in trace.links.get(rule.id, []):
            kind, _, rest = link.partition(":")
            if kind == "scenario" and rest in by_name:
                routes |= {
                    key(st.method, st.path)
                    for st in by_name[rest].steps
                    if isinstance(st, Step) and key(st.method, st.path) not in conditional
                }
            elif kind == "cell":
                routes |= {key(m, p) for m, p in route_re.findall(rest.split("|", 1)[-1])}
            elif kind == "machine":
                machines.add(rest)
        out[rule.id] = {"routes": routes, "machines": machines}
    return out


def probe_pv(w: World) -> dict[str, Any]:
    from lattice.spec.implementation import build_report, load_inventory  # noqa: PLC0415

    doc = json.loads(w.wf["S-dyn"].read_text())
    verified_routes = {
        tpl_key(s["route"])
        for f in doc["flows"]
        if f["status"] == "verified"
        for s in f["steps"]
        if s["kind"] == "request"
    }
    machine_ok = {
        (m["spec_object"]["table"], m["spec_object"]["column"])
        for m in doc["machines"]
        if m["status"] == "confirmed" and m.get("spec_object")
    }
    links = indep_rule_routes(w.spec["S"], w.trace["S"], {CLOCK})
    rep = report(w, "S-dyn", w.s, "S")
    # routes: the report's verified flag against the recomputation, over the 25
    rep_ver = {r["key"] for r in rep["routes"] if r["status"] == "implemented_verified"}
    route_ok = (rep_ver & S_ENDPOINTS) == (verified_routes & S_ENDPOINTS)
    # rules: verified iff every route is verified and every machine confirmed, with something
    want_ver = set()
    for rid, lk in links.items():
        if not lk["routes"] and not lk["machines"]:
            continue
        if lk["routes"] <= verified_routes and all(
            tuple(m.split(".", 1)) in machine_ok for m in lk["machines"]
        ):
            want_ver.add(rid)
    rules = rules_of(rep)
    got_ver = {k for k, v in rules.items() if v["status"] == "implemented_verified"}
    # static runs verify nothing
    stat_s = report(w, "S-stat", w.s, "S")
    stat_s4 = report(w, "S4-stat", w.s4, "S4")
    zero = {
        "S-stat": [
            sum(1 for r in stat_s["rules"] if r["status"] == "implemented_verified"),
            sum(1 for r in stat_s["routes"] if r["status"] == "implemented_verified"),
            stat_s["summary"]["flows"].get("verified", 0),
        ],
        "S4-stat": [
            sum(1 for r in stat_s4["rules"] if r["status"] == "implemented_verified"),
            sum(1 for r in stat_s4["routes"] if r["status"] == "implemented_verified"),
            stat_s4["summary"]["flows"].get("verified", 0),
        ],
    }
    # after one .go edit in a copy of the source: stale with exactly that path, nothing verified
    edit = w.scratch / "S-edit"
    shutil.rmtree(edit, ignore_errors=True)
    shutil.copytree(w.s, edit, ignore=shutil.ignore_patterns("bin"))
    target = edit / "internal/habits/routes.go"
    target.write_text(target.read_text() + "\n// edited\n")
    inv_e = load_inventory(w.inv["S-dyn"], edit)
    rep_e = build_report(w.spec["S"], w.trace["S"], inv_e)
    stale_ok = (
        rep_e["inventory"]["stale"]["changed"] == ["internal/habits/routes.go"]
        and rep_e["inventory"]["stale"]["added"] == []
        and rep_e["inventory"]["stale"]["removed"] == []
        and not any(r["status"] == "implemented_verified" for r in rep_e["rules"])
        and not any(r["status"] == "implemented_verified" for r in rep_e["routes"])
        and rep_e["summary"]["flows"].get("verified", 0) == 0
    )
    obs = {
        "routes_verified": f"{len(rep_ver & S_ENDPOINTS)}/25",
        "routes_agree": route_ok,
        "rules_implemented_verified": f"{len(got_ver)}/142",
        "rules_agree": got_ver == want_ver,
        "rules_only_in_report": sorted(got_ver - want_ver),
        "rules_only_in_recompute": sorted(want_ver - got_ver),
        "static_verified_counts [rules, routes, flows]": zero,
        "stale_after_one_edit": stale_ok,
        "stale_changed": rep_e["inventory"]["stale"]["changed"],
        "machine_rules_unverified": all(
            rules[r]["status"] == "implemented_unverified" for r in S_MACHINE_RULES
        ),
    }
    in_range = 17 <= len(rep_ver & S_ENDPOINTS) <= 24 and 50 <= len(got_ver) <= 120
    ok = (
        route_ok and got_ver == want_ver and all(v == [0, 0, 0] for v in zero.values()) and stale_ok
    )
    return result(
        "PV",
        "verified flags vs an independent recomputation from the frozen workflows.json",
        {
            "S-dyn": "routes verified 17-24/25, rules implemented_verified 50-120/142",
            "static": "0 verified",
            "stale": "exactly the edited path, 0 verified",
        },
        obs,
        ok,
        in_predicted_range=in_range,
        workflows_sha256=sha(w.wf["S-dyn"].read_bytes()),
    )


# ---- PO: orphans and exclusions -------------------------------------------------------------


def probe_po(w: World) -> dict[str, Any]:
    s = report(w, "S-stat", w.s, "S")
    plus = report(w, "S-plus", w.splus, "Splus")

    def excl(rep: dict[str, Any]) -> list[tuple[str, str, str]]:
        return sorted((x["kind"], x["key"], x["reason"]) for x in rep["excluded"])

    s_orph = sorted(x["key"] for x in s["implemented_not_required"] if x["kind"] == "route")
    p_orph = sorted(x["key"] for x in plus["implemented_not_required"] if x["kind"] == "route")
    s_ex = excl(s)
    p_ex = excl(plus)
    want_s_ex = {("route", CLOCK, "test_only")}
    framework = [x for x in s_ex if x[0] == "framework"]
    checks = {
        "S_no_orphans": s_orph == [],
        "S_test_only": want_s_ex <= set(s_ex),
        "S_two_framework": len(framework) == 2,
        "S_health_stays_required": not any(x[1] == "GET /health" for x in s_ex),
        "Splus_orphans": p_orph == ["GET /v1/admin/stats"],
        "Splus_metrics_plumbing": ("route", "GET /metrics", "plumbing") in p_ex,
        "Splus_metrics_not_orphan": "GET /metrics" not in p_orph,
    }
    return result(
        "PO",
        "orphans and exclusions on S and the planted S+",
        {
            "S": "0 orphans; excluded test_only PUT /test/clock (+2 framework)",
            "S+": "orphans exactly {GET /v1/admin/stats}; /metrics plumbing",
        },
        {
            "S_orphans": s_orph,
            "S_excluded": s_ex,
            "Splus_orphans": p_orph,
            "Splus_excluded": p_ex,
            "checks": checks,
        },
        all(checks.values()),
    )


# ---- PC: the code: citation table -----------------------------------------------------------


def find_line(root: Path, rel: str, needle: str) -> int:
    for i, ln in enumerate((root / rel).read_text().split("\n"), 1):
        if needle in ln:
            return i
    raise AssertionError((rel, needle))


def pc_cases(w: World) -> list[dict[str, Any]]:
    """(name, expected 'hold'|'reject', citation, optional work): >= 20 cases, written from the
    Go source (independent of the extract)."""
    r = w.s
    reg = find_line(r, "internal/habits/routes.go", 'r.Member.POST("/habits"')
    reg_q = 'r.Member.POST("/habits", httpapi.Handle(h.create))'
    hand = find_line(r, "internal/habits/routes.go", "func (h handler) create(")
    hand_q = "func (h handler) create("
    nxt = (r / "internal/habits/routes.go").read_text().split("\n")[reg]  # the line after
    n_lines = len((r / "internal/habits/routes.go").read_text().rstrip("\n").split("\n"))

    def c(rel: str, line: Any, quote: str, **kw: Any) -> dict[str, Any]:
        return {"target": f"code:{rel}:{line}", "step": None, "quote": quote, **kw}

    R = "internal/habits/routes.go"
    return [
        {"n": "registration line", "want": "hold", "c": c(R, reg, reg_q)},
        {"n": "handler line", "want": "hold", "c": c(R, hand, hand_q)},
        {
            "n": "whitespace variant",
            "want": "hold",
            "c": c(R, reg, '  r.Member.POST("/habits",   httpapi.Handle(h.create))  '),
        },
        {"n": "symbol on the line", "want": "hold", "c": c(R, reg, reg_q, symbol="create")},
        {
            "n": "inventory symbol (not a word on the line)",
            "want": "hold",
            "c": c(R, hand, hand_q, symbol="habits"),
        },
        {"n": "go.mod in the source set", "want": "hold", "c": c("go.mod", 1, "module streak")},
        {"n": "line 0", "want": "reject", "c": c(R, 0, reg_q)},
        {"n": f"line n+1 ({n_lines + 1})", "want": "reject", "c": c(R, n_lines + 1, reg_q)},
        {
            "n": "missing file",
            "want": "reject",
            "c": c("internal/habits/nope.go", 1, "package habits"),
        },
        {"n": "empty quote", "want": "reject", "c": c(R, reg, "")},
        {"n": "5-character quote", "want": "reject", "c": c(R, reg, "r.Mem")},
        {"n": "quote from line +1", "want": "reject", "c": c(R, reg, nxt.strip())},
        {"n": "wrong symbol", "want": "reject", "c": c(R, reg, reg_q, symbol="remove")},
        {
            "n": "../ escape",
            "want": "reject",
            "c": c("../S4/internal/habits/routes.go", 1, "package habits"),
        },
        {"n": "absolute path", "want": "reject", "c": c(str(r / R), reg, reg_q)},
        {
            "n": "symlink out of root",
            "want": "reject",
            "c": c("evil/link.go", 1, "package evil // secret"),
            "work": "symlink",
        },
        {"n": "case variant", "want": "reject", "c": c("Internal/habits/routes.go", reg, reg_q)},
        {
            "n": "vendor file",
            "want": "reject",
            "c": c("vendor/x/x.go", 1, "package x // vendored"),
            "work": "vendor",
        },
        {
            "n": ".git file",
            "want": "reject",
            "c": c(".git/config", 1, "[core] repositoryformatversion"),
            "work": "git",
        },
        {
            "n": ".env file",
            "want": "reject",
            "c": c(".env", 1, "SECRET_TOKEN=abcdefghij"),
            "work": "env",
        },
        {
            "n": "spec text quoted as code",
            "want": "reject",
            "c": c("REQUIREMENTS.md", 3, "Streak is a mobile habit tracker"),
        },
        {
            "n": "a.go:12:3",
            "want": "reject",
            "c": {"target": f"code:{R}:12:3", "step": None, "quote": "package habits"},
        },
        {"n": "NUL in path", "want": "reject", "c": c(R + "\x00.txt", 1, "package habits")},
    ]


def probe_pc(w: World) -> dict[str, Any]:
    from lattice.spec.implementation import check_code, load_inventory  # noqa: PLC0415

    # forged files live in a copy so the clone stays untouched
    work = w.scratch / "S-pc"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(w.s, work, ignore=shutil.ignore_patterns("bin"))
    outside = w.scratch / "outside-secret.go"
    outside.write_text("package evil // secret\n")
    (work / "evil").mkdir()
    (work / "evil" / "link.go").symlink_to(outside)
    (work / "vendor" / "x").mkdir(parents=True)
    (work / "vendor" / "x" / "x.go").write_text("package x // vendored\n")
    (work / ".git").mkdir(exist_ok=True)
    (work / ".git" / "config").write_text("[core] repositoryformatversion = 0\n")
    (work / ".env").write_text("SECRET_TOKEN=abcdefghij\n")
    inv = load_inventory(w.inv["S-dyn"], work)
    rows = []
    for case in pc_cases(w):
        reason = check_code(work, inv, case["c"])
        got = "hold" if reason is None else "reject"
        rows.append({"case": case["n"], "want": case["want"], "got": got, "reason": reason})
    runs = [[check_code(work, inv, c["c"]) for c in pc_cases(w)] for _ in range(10)]
    identical = all(r == runs[0] for r in runs)
    ok = all(r["want"] == r["got"] for r in rows) and identical and len(rows) >= 20
    return result(
        "PC",
        "the code: citation table (valid, invalid, forged), 10 runs identical",
        "100%, 10/10 identical",
        {
            "cases": len(rows),
            "agree": sum(r["want"] == r["got"] for r in rows),
            "identical_10": identical,
            "rows": rows,
        },
        ok,
    )


# ---- PA: the ask path with a stub generator -------------------------------------------------

PA_BASE_SCRIPT = r"""
import hashlib, json, sys
from pathlib import Path
from lattice.llm.client import ScriptedGenerator
from lattice.spec import ask as A
from lattice.spec.draft import load_spec
from lattice.spec.rules import Trace, extract_rules
from tests.spec import racy

def h(x):
    return hashlib.sha256(x.encode()).hexdigest()

def masked(a):
    d = a.to_dict()
    d["usage"] = {k: v for k, v in d["usage"].items() if k not in ("seconds", "calls")}
    return d

out = {}
md = "# Runs\n\n- A config has at most one live run.\n- Runs are fast.\n"
trace = Trace("REQ.md", extract_rules(md), links={"R1": ["invariant:one_live_run_per_config"]},
              unchecked={"R2": "no timing in the spec"})
specs = [("racy", racy.RUNS_SPEC, trace), ("racy-notrace", racy.RUNS_SPEC, None)]
clone = Path(sys.argv[1])
spec = load_spec((clone / "spec.py").read_text())[0]
tr = Trace.from_json((clone / "spec.trace.json").read_text())
specs.append(("streak", spec, tr))
qs = ["Can ben cancel ann's run?", "Can a user check in a habit?", "Is there a delete endpoint?"]
for name, sp, t in specs:
    d = A.build_digest(sp, t)
    out[name + ":digest"] = h(d.text)
    for q in qs:
        r = A.retrieve(d, q, [])
        out[f"{name}:{q}"] = [h(r.system), h(r.context), h(json.dumps(sorted(r.sent)))]
    cite = []
    for el in d.elements.values():
        if el.kind == "rule":
            cite = [{"target": el.target, "step": None, "quote": el.content[:30]}]
            break
    if not cite:
        for el in d.elements.values():
            if el.kind == "invariant":
                cite = [{"target": el.target, "step": None, "quote": el.content[:30]}]
                break
    forged = {"target": "code:x/y.go:3", "step": None, "quote": "func nothing()"}
    reply = json.dumps({"kind": "yes", "implementation": "implemented_verified", "answer": "a",
                        "explanation": "e", "citations": cite + [forged]})
    g = ScriptedGenerator([reply, reply])
    a = A.ask(sp, qs[0], trace=t, generator=g, model="m")
    out[name + ":answer"] = h(json.dumps(masked(a), sort_keys=True))
    out[name + ":prompts"] = h(json.dumps([[c[0], c[1]] for c in g.calls], sort_keys=True))
print(json.dumps(out, sort_keys=True))
"""


def element_quote(el: Any, n: int = 40) -> str:
    return el.content[:n]


def probe_pa(w: World) -> dict[str, Any]:
    from lattice.llm.client import ScriptedGenerator  # noqa: PLC0415
    from lattice.spec import ask as A  # noqa: PLC0415
    from lattice.spec.implementation import load_inventory  # noqa: PLC0415

    # (a) no inventory: identical to the base
    base = w.scratch / "base"
    if not base.is_dir():
        base.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", BASE_COMMIT], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(base)], input=tar, check=True)
    script = w.scratch / "pa_base_script.py"
    script.write_text(PA_BASE_SCRIPT)
    new = run_py([str(script), str(w.s)], fresh_tmp(w.scratch, "pa-new"), pythonpath=REPO)
    old = run_py([str(script), str(w.s)], fresh_tmp(w.scratch, "pa-old"), pythonpath=base, cwd=base)
    assert new.returncode == 0 and old.returncode == 0, (new.stderr[-800:], old.stderr[-800:])
    a_new, a_old = json.loads(new.stdout), json.loads(old.stdout)
    a_diff = sorted(k for k in a_old if a_old[k] != a_new.get(k))
    a_ok = a_new == a_old and len(a_old) == 18  # 3 specs x (digest, 3 questions, answer, prompts)

    # (b) with an inventory: the budget and ten scripted replies
    out_b: dict[str, Any] = {}

    def digest_for(inv_key: str, root: Path, project: str):
        inv = load_inventory(w.inv[inv_key], root)
        return A.build_digest(w.spec[project], w.trace[project], inv), inv

    tokens = {}
    for key, root, proj in (("S-dyn", w.s, "S"), ("S-stat", w.s, "S"), ("S4-stat", w.s4, "S4")):
        d, _ = digest_for(key, root, proj)
        tokens[key] = A.approx_tokens("\n".join(A.impl_core(d)))
    out_b["core_impl_tokens"] = tokens
    d, inv = digest_for("S-dyn", w.s, "S")
    flows = inv.workflows["flows"]

    def flow(skel: list[str], kind: str | None = None, status: str | None = None):
        for f in flows:
            if (
                f["skeleton"] == skel
                and (kind is None or f["kind"] == kind)
                and (status is None or f["status"] == status)
            ):
                return f
        raise AssertionError(skel)

    checkin = flow(["POST /v1/habits", "POST /v1/habits/{}/completions"], "pair", status="verified")
    failed = next(f for f in flows if f["status"] == "failed")
    reg_line = find_line(
        w.s, "internal/completions/routes.go", 'r.Member.POST("/habits/:id/completions"'
    )
    reg_cite = {
        "target": f"code:internal/completions/routes.go:{reg_line}",
        "step": None,
        "quote": 'r.Member.POST("/habits/:id/completions"',
    }
    plain_cite = {
        "target": "code:internal/completions/routes.go:1",
        "step": None,
        "quote": "package completions",
    }

    def cite(t: str, q: str) -> dict[str, Any]:
        return {"target": t, "step": None, "quote": q}

    def rule_cite(proj: str, rid: str) -> dict[str, Any]:
        text = next(r.text for r in w.trace[proj].rules if r.id == rid)
        return cite(f"rule:{rid}", text[:40])

    def reply(kind: str, label: str, cites: list[dict[str, Any]]) -> str:
        return json.dumps(
            {
                "kind": kind,
                "implementation": label,
                "answer": "a",
                "explanation": "e",
                "citations": cites,
            }
        )

    ok_rule = next(
        r
        for r, v in rules_of(report(w, "S-dyn", w.s, "S")).items()
        if v["status"].startswith("implemented")
    )
    s4_rep = report(w, "S4-stat", w.s4, "S4")
    s4_req = sorted(ids(s4_rep, "required_not_implemented"))[0]
    route_a, route_b = "impl:route:GET /v1/habits", "impl:route:POST /v1/habits"
    quote_a = d.elements[route_a].content[:30]
    quote_b = d.elements[route_b].content[:30]
    cases = {
        "verified_ok": (
            "S",
            "S-dyn",
            w.s,
            reply(
                "yes",
                "implemented_verified",
                [cite(f"impl:flow:{checkin['id']}", "status verified"), reg_cite],
            ),
        ),
        "verified_without_code": (
            "S",
            "S-dyn",
            w.s,
            reply(
                "yes",
                "implemented_verified",
                [cite(f"impl:flow:{checkin['id']}", "status verified")],
            ),
        ),
        "verified_code_not_evidence": (
            "S",
            "S-dyn",
            w.s,
            reply(
                "yes",
                "implemented_verified",
                [cite(f"impl:flow:{checkin['id']}", "status verified"), plain_cite],
            ),
        ),
        "verified_cites_unverified_flow": (
            "S",
            "S-dyn",
            w.s,
            reply(
                "yes",
                "implemented_verified",
                [cite(f"impl:flow:{failed['id']}", "status failed"), reg_cite],
            ),
        ),
        "unverified_without_element": (
            "S",
            "S-dyn",
            w.s,
            reply("yes", "implemented_unverified", [rule_cite("S", ok_rule)]),
        ),
        "required_ok": (
            "S4",
            "S4-stat",
            w.s4,
            reply("no", "required_not_implemented", [rule_cite("S4", s4_req)]),
        ),
        "required_endpoints_present": (
            "S",
            "S-dyn",
            w.s,
            reply("yes", "required_not_implemented", [rule_cite("S", ok_rule)]),
        ),
        "ambiguous_two": (
            "S",
            "S-dyn",
            w.s,
            reply("yes", "ambiguous", [cite(route_a, quote_a), cite(route_b, quote_b)]),
        ),
        "ambiguous_one": ("S", "S-dyn", w.s, reply("yes", "ambiguous", [cite(route_a, quote_a)])),
        "not_specified_not_implemented": (
            "S",
            "S-dyn",
            w.s,
            reply("not_specified", "not_specified_not_implemented", []),
        ),
    }
    rows = {}
    for name, (proj, inv_key, root, text) in cases.items():
        invx = load_inventory(w.inv[inv_key], root)
        g = ScriptedGenerator([text])
        a = A.ask(
            w.spec[proj], "q?", trace=w.trace[proj], generator=g, model="stub", inventory=invx
        )
        want = PA_EXPECT[name]
        rows[name] = {
            "got": [a.implementation, a.implementation_basis],
            "want": list(want),
            "rounds": a.usage["rounds"],
            "rejected": a.rejected_citations,
        }
    b_ok = all(r["got"] == r["want"] for r in rows.values()) and all(
        t <= A.IMPL_CORE_BUDGET for t in tokens.values()
    )
    out_b["replies"] = rows
    in_range = 5000 <= tokens["S-dyn"] <= 7000
    return result(
        "PA",
        "ask with a stub generator: (a) no inventory identical to the base; (b) with one, the "
        "section within budget and ten scripted replies",
        {"a": "identical to 454a8e6", "b": "5-7k tokens on S, <= 8000; 10/10"},
        {"a_identical": a_ok, "a_keys": len(a_old), "a_differing": a_diff, "b": out_b},
        a_ok and b_ok,
        core_tokens_in_predicted_range=in_range,
    )


# ---- PT: workflow_find ----------------------------------------------------------------------


class _Backend:
    kind = "probe"
    configured = False

    def __init__(self, root: Path) -> None:
        self.root = root

    def model(self) -> dict[str, Any]:
        raise AssertionError("the implementation tools never call the model endpoint")


def toolbox(root: Path, readonly: bool = True):
    from lattice.mcp.tools import Toolbox  # noqa: PLC0415

    return Toolbox(_Backend(root), readonly=readonly)


def probe_pt(w: World) -> dict[str, Any]:
    boxes = {"S": toolbox(w.s), "S4": toolbox(w.s4)}
    rows = []
    for pid, proj, q, want in PT_QUERIES:
        out = boxes[proj].call("workflow_find", {"question": q})
        rows.append(
            {
                "id": pid,
                "project": proj,
                "question": q,
                "want": want,
                "got": out["hint"],
                "top": [(c["target"], c["status"], c["score"]) for c in out["candidates"][:3]],
            }
        )
    hits = sum(r["want"] == r["got"] for r in rows)
    return result(
        "PT",
        "workflow_find, 8 fixed queries",
        "7/8 (6-8)",
        {"hits": f"{hits}/8", "misses": [r for r in rows if r["want"] != r["got"]], "rows": rows},
        hits >= 6,
    )


# ---- PF: verify by replay, on Docker ---------------------------------------------------------


def all_containers() -> list[str]:
    proc = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(proc.stdout.split())


def probe_pf(w: World) -> dict[str, Any]:
    import dataclasses  # noqa: PLC0415

    from lattice.llm.client import ScriptedGenerator  # noqa: PLC0415
    from lattice.spec import ask as A  # noqa: PLC0415
    from lattice.spec.implementation import load_inventory  # noqa: PLC0415
    from lattice.studio.server import Studio  # noqa: PLC0415

    before = all_containers()
    inv = load_inventory(w.inv["S-dyn"], w.s)
    flows = inv.workflows["flows"]

    def pick(skel: list[str] | None = None, kind: str | None = None) -> dict[str, Any]:
        return next(
            f
            for f in flows
            if f["status"] == "verified"
            and (skel is None or f["skeleton"] == skel)
            and (kind is None or f["kind"] == kind)
        )

    chosen = {
        "habit check-in": pick(["POST /v1/habits", "POST /v1/habits/{}/completions"], "pair"),
        "device delete": pick(["POST /v1/devices", "DELETE /v1/devices/{}"], "pair"),
        "owner guard": pick(kind="owner_guard"),
    }
    rows = {}

    def reply_for(fid: str) -> str:
        return json.dumps(
            {
                "kind": "yes",
                "implementation": "implemented_unverified",
                "answer": "a",
                "explanation": "e",
                "citations": [
                    {"target": f"impl:flow:{fid}", "step": None, "quote": "status verified"}
                ],
            }
        )

    gen = ScriptedGenerator([reply_for(f["id"]) for f in chosen.values()])
    studio = Studio(w.s, model="stub", generator=gen)
    for name, f in chosen.items():
        t0 = time.perf_counter()
        out = studio.ask({"question": f"Can a user do the {name}?", "verify": True})
        rows[name] = {
            "flow": f["id"],
            "verified": out["verified"]["status"] if out.get("verified") else None,
            "source": (out.get("verified") or {}).get("source"),
            "detail": (out.get("verified") or {}).get("detail"),
            "rounds": out["usage"]["rounds"],
            "seconds": round(time.perf_counter() - t0, 1),
        }
    # an in-memory copy of a verified flow with a wrong expect_status
    wrong = copy.deepcopy(chosen["habit check-in"])
    wrong["id"] = wrong["id"] + "-wrong"
    last = [s for s in wrong["steps"] if s["kind"] == "request"][-1]
    last["expect_status"] = 418
    wf2 = copy.deepcopy(inv.workflows)
    wf2["flows"] = [wrong]
    inv2 = dataclasses.replace(inv, workflows=wf2)
    gen2 = ScriptedGenerator([reply_for(wrong["id"])])
    with studio.app_context():
        cfg = studio.harness.verify_config()
        snap = studio.snapshot()
        a = A.ask(
            snap.spec,
            "Can a user check in a habit?",
            trace=snap.trace,
            generator=gen2,
            model="stub",
            verify=cfg,
            inventory=inv2,
        )
    rows["wrong expect_status"] = {
        "flow": wrong["id"],
        "verified": (a.verified or {}).get("status"),
        "detail": (a.verified or {}).get("detail"),
        "source": (a.verified or {}).get("source"),
        "rounds": a.usage["rounds"],
    }
    calls = len(gen.calls) + len(gen2.calls)
    after = all_containers()
    ok = (
        all(rows[n]["verified"] == "agrees" for n in chosen)
        and rows["wrong expect_status"]["verified"] == "disagrees"
        and "418" in (rows["wrong expect_status"]["detail"] or "")
        and calls == 4  # one ask each, no scenario written by a model
        and all(r["rounds"] == 1 for r in rows.values())
        and all((r["source"] or "").startswith("flow:") for r in rows.values())
        and after == before
    )
    return result(
        "PF",
        "verify by replay, S-dyn on Docker: 3 verified flows and a wrong-status copy",
        "3/3 agree, 1/1 disagree, 0 generator calls for verify",
        {
            "rows": rows,
            "generator_calls": calls,
            "containers_before": before,
            "containers_after": after,
        },
        ok,
    )


# ---- PP: a finding's proposal to change_propose ----------------------------------------------


def probe_pp(w: World) -> dict[str, Any]:
    from lattice.llm.client import ScriptedGenerator  # noqa: PLC0415
    from lattice.mcp.tools import LocalBackend, Toolbox  # noqa: PLC0415

    proj = w.scratch / "S-pp"
    state = w.scratch / "pp-state"
    for p in (proj, state):
        shutil.rmtree(p, ignore_errors=True)
    shutil.copytree(w.splus, proj, ignore=shutil.ignore_patterns("bin"))
    toml = proj / "studio.toml"
    toml.write_text(
        toml.read_text().replace(str(w.splus), str(proj))
        + f'\n[state]\ndir = "{state}"\n'  # the change records live outside the clone
    )
    before = snapshot(proj)
    gen = ScriptedGenerator(["this is not an edit"] * 4)
    tb = Toolbox(LocalBackend(proj, generator=gen))  # change mode: not read-only
    orphans = tb.call("implementation_get", {"section": "orphans"})
    finding = next(f for f in orphans["items"] if f["target"] == "GET /v1/admin/stats")
    out = tb.call("change_propose", finding["proposal"])
    cid = out["id"]
    deadline = time.monotonic() + 90
    rec: dict[str, Any] = {}
    while time.monotonic() < deadline:
        rec = tb.call("change_get", {"id": cid})
        if rec.get("status") not in ("drafting", "checking"):
            break
        time.sleep(0.3)
    saved = state / "changes" / f"{cid}.json"
    stored = json.loads(saved.read_text()) if saved.is_file() else {}
    source = (stored.get("record") or {}).get("source") or rec.get("source")
    after = snapshot(proj)
    state_files = sorted(p.relative_to(state).as_posix() for p in state.rglob("*") if p.is_file())
    accept_tools = [n for n in tb.names() if "accept" in n or "reject" in n]
    checks = {
        "source_recorded": source is not None
        and source.get("server") == "extractor"
        and source.get("item") == finding["proposal"]["source"]["item"],
        "not_accepted": rec.get("status") not in ("accepted",),
        "project_identical": not any(diff_snapshots(before, after).values()),
        "only_changes_grew": all(
            f in ("meta.json",) or f.startswith("changes/") for f in state_files
        )
        and any(f.startswith("changes/") for f in state_files),
        "no_accept_tool": accept_tools == [],
    }
    return result(
        "PP",
        "change_propose with an S+ orphan's proposal (LocalBackend, stub LLM, change-mode Toolbox)",
        "source {server: extractor, item}, not accepted; code and spec byte-identical; "
        "only changes/ grows",
        {
            "change": cid,
            "status": rec.get("status"),
            "source": source,
            "state_files": state_files,
            "project_diff": diff_snapshots(before, after),
            "checks": checks,
        },
        all(checks.values()),
    )


# ---- PW: nothing is written ----------------------------------------------------------------


def probe_pw(w: World, with_docker: bool) -> dict[str, Any]:
    from lattice.llm.client import ScriptedGenerator  # noqa: PLC0415
    from lattice.spec import ask as A  # noqa: PLC0415
    from lattice.spec.implementation import load_inventory  # noqa: PLC0415

    inv_dirs = [w.inv["S-dyn"], w.inv["S-stat"]]
    roots = [w.s, *inv_dirs, w.wf["S-dyn"].parent]
    snap0 = {str(r): snapshot(r) for r in roots}
    chmod_tree(w.s, False)
    for d in inv_dirs:
        chmod_tree(d, False)
    window: dict[str, Any] = {}
    try:
        env_path = f"{REPO}{os.pathsep}{w.s}"
        # the report CLI x10 (json and text alternate)
        rcs = []
        for i in range(10):
            r = run_py(
                [
                    "-m",
                    "lattice.spec",
                    "implementation",
                    "--spec",
                    "spec:SPEC",
                    "--trace",
                    str(w.s / "spec.trace.json"),
                    "--inventory",
                    str(w.inv["S-dyn"]),
                    "--app-root",
                    str(w.s),
                    *(["--json"] if i % 2 else []),
                ],
                fresh_tmp(w.scratch, f"pw-cli-{i}"),
                pythonpath=env_path,
            )
            rcs.append(r.returncode)
        window["report_cli_rcs"] = rcs
        # the three tools x10 over stdio --readonly
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "implementation_get", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "workflow_find", "arguments": {"question": "check in a habit"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "workflow_get", "arguments": {"id": "nudges.delivery"}},
            },
        ]
        tool_rcs, errors = [], 0
        for i in range(10):
            r = run_py(
                ["-m", "lattice.mcp", "serve", "--project", str(w.s), "--readonly"],
                fresh_tmp(w.scratch, f"pw-mcp-{i}"),
                stdin="\n".join(json.dumps(m) for m in msgs) + "\n",
            )
            tool_rcs.append(r.returncode)
            errors += sum(
                1 for ln in r.stdout.splitlines() if json.loads(ln).get("result", {}).get("isError")
            )
        window["tools_rcs"] = tool_rcs
        window["tool_errors"] = errors
        # ask (stub) x10, with an inventory
        os.environ["TMPDIR"] = str(fresh_tmp(w.scratch, "pw-ask"))
        for _ in range(10):
            inv = load_inventory(w.inv["S-dyn"], w.s)
            g = ScriptedGenerator(
                [
                    json.dumps(
                        {
                            "kind": "not_specified",
                            "implementation": "not_specified_not_implemented",
                            "answer": "n",
                            "explanation": "e",
                            "citations": [],
                        }
                    )
                ]
            )
            A.ask(
                w.spec["S"],
                "Can a user export a CSV?",
                trace=w.trace["S"],
                generator=g,
                model="stub",
                inventory=inv,
            )
        window["ask_runs"] = 10
        # a capture with --out inside the clone is refused (exit 2), nothing written
        r = run_cli(
            ["import", "inventory", str(w.s), "--out", str(w.s / "inv-out")],
            w.scratch,
            "pw-capture",
        )
        window["capture_inside_rc"] = r.returncode
        window["capture_inside_err"] = r.stderr.strip()[-200:]
    finally:
        pass
    # PF inside the window, with the clone writable for its duration only
    pf = None
    if with_docker:
        chmod_tree(w.s, True)
        try:
            pf = probe_pf(w)
        finally:
            pass
        chmod_tree(w.s, False)
    chmod_tree(w.s, True)
    for d in inv_dirs:
        chmod_tree(d, True)
    snap1 = {str(r): snapshot(r) for r in roots}
    diffs = {r: diff_snapshots(snap0[r], snap1[r]) for r in snap0}
    n_changed = sum(len(d["changed"]) for d in diffs.values())
    n_new = sum(len(d["new"]) + len(d["gone"]) for d in diffs.values())
    ok = (
        n_changed == 0
        and n_new == 0
        and all(rc == 0 for rc in window["report_cli_rcs"] + window["tools_rcs"])
        and window["tool_errors"] == 0
        and window["capture_inside_rc"] == 2
        and (pf is None or pf["pass"])
    )
    return result(
        "PW",
        "no write: clone, spec files and inventories snapshot before/after report x10, tools x10 "
        "(stdio --readonly), ask x10, PF; capture with --out in the clone refused",
        "0 changed, 0 new; capture exit 2",
        {
            "changed": n_changed,
            "new": n_new,
            "diffs": {k: v for k, v in diffs.items() if v["changed"] or v["new"] or v["gone"]},
            "window": window,
            "pf_inside": None if pf is None else {"pass": pf["pass"]},
        },
        ok,
        pf=pf,
    )


# ---- PD: determinism ------------------------------------------------------------------------


def probe_pd(w: World, runs: int) -> dict[str, Any]:
    cases = {
        "S-dyn": (w.inv["S-dyn"], w.s, w.s),
        "S-stat": (w.inv["S-stat"], w.s, w.s),
        "S4-stat": (w.inv["S4-stat"], w.s4, w.s4),
        "S-plus": (w.inv["S-plus"], w.splus, w.s),
    }
    rows = {}
    leaks: list[str] = []
    for name, (inv, root, spec_root) in cases.items():
        outs = {"json": [], "text": []}
        for i in range(runs):
            for mode in ("json", "text"):
                r = run_py(
                    [
                        "-m",
                        "lattice.spec",
                        "implementation",
                        "--spec",
                        "spec:SPEC",
                        "--trace",
                        str(spec_root / "spec.trace.json"),
                        "--inventory",
                        str(inv),
                        "--app-root",
                        str(root),
                        *(["--json"] if mode == "json" else []),
                    ],
                    fresh_tmp(w.scratch, f"pd-{name}-{mode}-{i}"),
                    pythonpath=f"{REPO}{os.pathsep}{spec_root}",
                    extra_env={"PYTHONHASHSEED": str(i * 7919 + 1)},
                )
                assert r.returncode == 0, r.stderr[-400:]
                outs[mode].append(r.stdout)
        for mode, lst in outs.items():
            for blob in lst[:1]:
                leaks += [f"{name}/{mode}: {m.group(0)}" for m in SECRET_RE.finditer(blob)]
                leaks += [f"{name}/{mode}: {m.group(0)}" for m in ISO_RE.finditer(blob)]
        rows[name] = {
            "json_identical": len(set(outs["json"])) == 1,
            "text_identical": len(set(outs["text"])) == 1,
            "json_sha256": sha(outs["json"][0].encode()),
            "bytes": len(outs["json"][0]),
        }
    # a second root: the same app archived elsewhere gives the same inventory and report
    second: dict[str, Any] = {}
    for key, k2 in (("S-dyn", "S-dyn-r2"), ("S-stat", "S-stat-r2")):
        a = (w.inv[key] / "inventory.json").read_bytes()
        b = (w.inv[k2] / "inventory.json").read_bytes()
        ra = run_py(
            [
                "-m",
                "lattice.spec",
                "implementation",
                "--spec",
                "spec:SPEC",
                "--trace",
                str(w.s / "spec.trace.json"),
                "--inventory",
                str(w.inv[key]),
                "--app-root",
                str(w.s),
                "--json",
            ],
            fresh_tmp(w.scratch, f"pd2a-{key}"),
            pythonpath=f"{REPO}{os.pathsep}{w.s}",
        )
        rb = run_py(
            [
                "-m",
                "lattice.spec",
                "implementation",
                "--spec",
                "spec:SPEC",
                "--trace",
                str(w.s2 / "spec.trace.json"),
                "--inventory",
                str(w.inv[k2]),
                "--app-root",
                str(w.s2),
                "--json",
            ],
            fresh_tmp(w.scratch, f"pd2b-{key}"),
            pythonpath=f"{REPO}{os.pathsep}{w.s2}",
        )
        second[key] = {
            "inventory_json_identical": a == b,
            "report_identical": ra.stdout == rb.stdout and ra.returncode == 0,
        }
    ok = (
        all(r["json_identical"] and r["text_identical"] for r in rows.values())
        and all(v["inventory_json_identical"] and v["report_identical"] for v in second.values())
        and not leaks
    )
    return result(
        "PD",
        "report JSON and text identical over separate processes (random hash seeds) x {S-dyn, "
        "S-stat, S4-stat, S+} and a second root; no absolute path or time",
        "10/10 identical, second root identical, no leak",
        {"runs": runs, "rows": rows, "second_root": second, "leaks": leaks},
        ok,
    )


# ---- PX: wall ------------------------------------------------------------------------------


def probe_px(w: World) -> dict[str, Any]:
    from lattice.spec import ask as A  # noqa: PLC0415
    from lattice.spec.implementation import build_report, load_inventory  # noqa: PLC0415

    def timed(fn: Callable[[], Any], n: int = 5) -> list[float]:
        out = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            out.append(round(time.perf_counter() - t0, 3))
        return out

    inv = load_inventory(w.inv["S-dyn"], w.s)
    report_t = timed(
        lambda: build_report(w.spec["S"], w.trace["S"], load_inventory(w.inv["S-dyn"], w.s))
    )
    tb = toolbox(w.s)
    tb.call("implementation_get", {})  # load
    tools = {
        "implementation_get": timed(lambda: tb.call("implementation_get", {})),
        "workflow_get": timed(lambda: tb.call("workflow_get", {"id": "nudges.delivery"})),
        "workflow_find": timed(lambda: tb.call("workflow_find", {"question": "check in a habit"})),
    }
    base_d = timed(lambda: A.build_digest(w.spec["S"], w.trace["S"]))
    impl_d = timed(lambda: A.build_digest(w.spec["S"], w.trace["S"], inv))
    overhead = round(sorted(impl_d)[2] - sorted(base_d)[2], 3)
    cli = []
    for i in range(3):
        t0 = time.perf_counter()
        r = run_py(
            [
                "-m",
                "lattice.spec",
                "implementation",
                "--spec",
                "spec:SPEC",
                "--trace",
                str(w.s / "spec.trace.json"),
                "--inventory",
                str(w.inv["S-dyn"]),
                "--app-root",
                str(w.s),
                "--json",
            ],
            fresh_tmp(w.scratch, f"px-{i}"),
            pythonpath=f"{REPO}{os.pathsep}{w.s}",
        )
        cli.append(round(time.perf_counter() - t0, 3))
        assert r.returncode == 0
    med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
    ok = med(report_t) <= 2 and all(med(v) <= 1 for v in tools.values()) and overhead <= 0.5
    return result(
        "PX",
        "wall: the report on S; each tool after load; the digest overhead",
        {"report": "0.1-0.6 s", "tool": "< 0.3 s", "digest": "< 0.2 s"},
        {
            "report_in_process_s": report_t,
            "report_cli_process_s": cli,
            "tools_s": tools,
            "digest_base_s": base_d,
            "digest_with_inventory_s": impl_d,
            "digest_overhead_median_s": overhead,
        },
        ok,
    )


# ---- PB: byte-compat against the base ---------------------------------------------------------

PB_SCRIPT = r"""
import ast, hashlib, json, sys, tempfile
from pathlib import Path

def h(x):
    return hashlib.sha256(x.encode() if isinstance(x, str) else x).hexdigest()

out = {}
tmp = Path(tempfile.mkdtemp())
# --- MCP initialize instructions: projects x workspace x readonly (the F1 configs and the rest)
from lattice import workspace as ws
from lattice.mcp.projects import KnownProject, ProjectRouter
from lattice.mcp.server import INSTRUCTIONS
from lattice.mcp.tools import PendingBackend, Toolbox
from tests.studio.contract_world import make_world
w = make_world(tmp / "w")
roots = {"prov": w.provider, "cons": w.consumer}
def router(projects, workspace, readonly):
    known = [KnownProject(n, roots[n].resolve(), "workspace") for n in projects]
    loaded = ws.load(w.root / ws.MANIFEST) if workspace else None
    return ProjectRouter(known, lambda p: PendingBackend(p.root), readonly=readonly,
                         workspace=loaded)
for projects in (("cons",), ("prov", "cons")):
    for workspace in (False, True):
        for readonly in (False, True):
            r = router(projects, workspace, readonly)
            key = f"{len(projects)}p/ws={int(workspace)}/ro={int(readonly)}"
            out["instructions:" + key] = h(r.instructions(INSTRUCTIONS))
            out["tools:" + key] = h(json.dumps(r.list_tools(), sort_keys=True))
single = Toolbox(PendingBackend(w.consumer))
out["tools:single"] = h(json.dumps(single.list_tools(), sort_keys=True))
ro = Toolbox(PendingBackend(w.consumer), readonly=True)
out["tools:single-readonly"] = h(json.dumps(ro.list_tools(), sort_keys=True))
# --- the ask CLI with a stub generator
from lattice.llm.client import ScriptedGenerator
from lattice.spec import ask as A
import io, contextlib
sys.path.insert(0, str(Path(sys.argv[1])))
from tests.spec import racy
mod = tmp / "m"; mod.mkdir()
(mod / "racy_cli.py").write_text("from tests.spec.racy import RUNS_SPEC as SPEC\n")
sys.path.insert(0, str(mod))
reply = json.dumps({"kind": "yes", "answer": "a", "explanation": "e", "citations": [
    {"target": "machine:Run.status", "step": None, "quote": "queued"}]})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = A.main(["--spec", "racy_cli:SPEC", "--json", "Can ben cancel ann's run?"],
                generator=ScriptedGenerator([reply]))
d = json.loads(buf.getvalue())
d["usage"] = {k: v for k, v in d["usage"].items() if k not in ("seconds", "calls")}
out["ask_cli"] = [rc, h(json.dumps(d, sort_keys=True))]
# --- the route table: the AST of the handler methods and dispatch helpers of the server
src = Path(A.__file__).parents[1] / "studio" / "server.py"
tree = ast.parse(src.read_text())
names = {"do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH", "_route", "_dispatch"}
parts = {}
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and (node.name in names or node.name.startswith("route")):
        parts[node.name + ":" + str(len(parts))] = h(ast.dump(node))
out["route_table"] = parts
print(json.dumps(out, sort_keys=True))
"""


def free_port() -> int:
    import socket  # noqa: PLC0415

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_and_get(code_root: Path, project: Path, tmp: Path) -> dict[str, Any]:
    """Serve the dogfood project (stub LLM) from `code_root` and read /api/model and /api/ask."""
    import urllib.request  # noqa: PLC0415

    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "lattice.studio",
            str(project),
            "--port",
            str(port),
            "--quiet",
            "--stub-llm",
        ],
        env=env_for(tmp, code_root),
        cwd=code_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/model", timeout=2).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        model = json.loads(urllib.request.urlopen(base + "/api/model", timeout=60).read())
        req = urllib.request.Request(
            base + "/api/ask",
            data=json.dumps(
                {"question": "How many notes can a user have?", "selected": []}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ask = json.loads(urllib.request.urlopen(req, timeout=120).read())
        ask["usage"] = {k: v for k, v in ask["usage"].items() if k not in ("seconds", "calls")}
        return {"model": model, "ask": ask}
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)


def probe_pb(w: World, args: argparse.Namespace) -> dict[str, Any]:
    base = w.scratch / "base"
    if not base.is_dir():
        base.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", BASE_COMMIT], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(base)], input=tar, check=True)
    script = w.scratch / "pb_script.py"
    script.write_text(PB_SCRIPT)
    new = run_py([str(script), str(REPO)], fresh_tmp(w.scratch, "pb-new"), pythonpath=REPO)
    old = run_py(
        [str(script), str(base)], fresh_tmp(w.scratch, "pb-old"), pythonpath=base, cwd=base
    )
    assert new.returncode == 0 and old.returncode == 0, (new.stderr[-800:], old.stderr[-800:])
    n, o = json.loads(new.stdout), json.loads(old.stdout)
    script_diff = sorted(k for k in set(n) | set(o) if n.get(k) != o.get(k))
    # /api/ask and /api/model on the dogfood project, served from the base and from this tree
    api: dict[str, Any] = {}
    proj_root = w.scratch / "pb-project"
    shutil.rmtree(proj_root, ignore_errors=True)
    import importlib  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "dogfood_studio_harness", REPO / "dogfood/studio/harness.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (proj_root / "old").mkdir(parents=True)
    proj_old = mod.make_project(proj_root / "old")
    api["via_harness"] = True
    results = {}
    if proj_old is not None:
        for name, code in (("base", base), ("new", REPO)):
            shutil.rmtree(proj_root / "run", ignore_errors=True)
            shutil.copytree(proj_old, proj_root / "run", symlinks=True)
            results[name] = serve_and_get(
                code, proj_root / "run", fresh_tmp(w.scratch, f"pb-api-{name}")
            )
        masked = {k: json.dumps(v, sort_keys=True) for k, v in results.items()}
        api["identical"] = masked["base"] == masked["new"]
    # with [implementation]: the tool list is the base + 3 after change_list; the sessions
    from lattice.mcp.tools import Toolbox  # noqa: PLC0415
    from lattice.studio import session as sess  # noqa: PLC0415

    base_names = Toolbox(_Backend(w.scratch)).names()  # no studio.toml here
    with_names = toolbox(w.s, readonly=False).names()
    no_impl = w.scratch / "pb-no-impl"
    shutil.rmtree(no_impl, ignore_errors=True)
    no_impl.mkdir()
    (no_impl / "studio.toml").write_text('[llm]\nbackend = "stub"\n')
    off_names = Toolbox(_Backend(no_impl)).names()
    impl3 = ["implementation_get", "workflow_get", "workflow_find"]
    i = off_names.index("change_list")
    tools_ok = (
        with_names == off_names[: i + 1] + impl3 + off_names[i + 1 :]
        and base_names == off_names
        and with_names[i + 1 : i + 4] == impl3
    )
    old_sess = json.loads(
        run_py(
            [
                "-c",
                "import json; from lattice.studio import session as s; "
                "print(json.dumps({m: [list(p.allowed), p.prompt, "
                "list(p.builtin), list(p.disallowed), p.readonly, p.enabled] "
                "for m, p in s.MODE_PROFILES.items()} | "
                "{'_BASE_RULES': s._BASE_RULES, '_EXTERNAL_RULES': s._EXTERNAL_RULES}))",
            ],
            fresh_tmp(w.scratch, "pb-sess-old"),
            pythonpath=base,
            cwd=base,
        ).stdout
    )
    sess_ok = True
    sess_rows = {}
    for mode, prof in sess.MODE_PROFILES.items():
        o_allowed = old_sess[mode][0]
        n_allowed = list(prof.allowed)
        if mode in ("ask", "explain", "change"):
            j = o_allowed.index(sess._MULTI_TOOLS[-1])
            expected = o_allowed[: j + 1] + list(sess._IMPL_TOOLS) + o_allowed[j + 1 :]
        else:
            expected = o_allowed
        same_rest = [
            old_sess[mode][1],
            old_sess[mode][2],
            old_sess[mode][3],
            old_sess[mode][4],
            old_sess[mode][5],
        ] == [prof.prompt, list(prof.builtin), list(prof.disallowed), prof.readonly, prof.enabled]
        sess_rows[mode] = {
            "allowed_is_base_plus_3": n_allowed == expected,
            "rest_identical": same_rest,
        }
        sess_ok = sess_ok and n_allowed == expected and same_rest
    sess_ok = (
        sess_ok
        and old_sess["_BASE_RULES"] == sess._BASE_RULES
        and old_sess["_EXTERNAL_RULES"] == sess._EXTERNAL_RULES
    )
    checks = {
        "script_identical": not script_diff,
        "api_identical": api.get("identical") is True,
        "tools_with_table": tools_ok,
        "sessions": sess_ok,
    }
    return result(
        "PB",
        "byte-compat against 454a8e6: initialize instructions, tools/list, /api/ask and /api/model "
        "(dogfood), ask --json, the route table; with [implementation]: base + 3 tools and "
        "allowlists",
        "all identical; base + 3 after change_list; allowlists base + 3 (ask/explain/change)",
        {
            "script_keys": len(n),
            "script_differing": script_diff,
            "api": {k: v for k, v in api.items()},
            "tools_off": off_names[-4:],
            "tools_on": with_names[-5:],
            "sessions": sess_rows,
        },
        all(checks.values()),
        checks=checks,
    )


def run_pytest_and_check(w: World) -> list[dict[str, Any]]:
    """The regression gates (optional here: the builder runs them directly too)."""
    rows = []
    t0 = time.perf_counter()
    p = subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "tests/spec",
            "tests/mcp",
            "tests/studio",
            "tests/importer",
            "tests/gocheck",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=env_for(fresh_tmp(w.scratch, "pytest")),
        cwd=REPO,
        check=False,
    )
    tail = p.stdout.strip().splitlines()[-3:]
    rows.append(
        result(
            "PB-pytest",
            "pytest by directory",
            "0 failures",
            {"tail": tail, "seconds": round(time.perf_counter() - t0)},
            p.returncode == 0,
        )
    )
    return rows


# ---- PH: the held-out stage (run by the orchestrator, once; report-only, never tuned) ---------


def load_spec_ref(spec_ref: str) -> Any:
    """``DIR:MODULE:CALL`` -> the Spec (``MODULE.CALL()``, or the attribute ``CALL``)."""
    d, _, rest = spec_ref.partition(":")
    mod, _, call = rest.partition(":")
    sys.path.insert(0, d)
    try:
        for name in [m for m in sys.modules if m == mod.split(".")[0]]:
            sys.modules.pop(name, None)
        obj = getattr(importlib.import_module(mod), call)
        return obj() if callable(obj) else obj
    finally:
        sys.path.remove(d)


def tpl_path(path: str) -> str:
    """The runner's own template of a path (ids, :x, *x and {x} all become {})."""
    path = path.split("?")[0]
    segs = []
    for s in path.split("/"):
        if (
            s[:1] in (":", "*")
            or (s.startswith("{") and s.endswith("}"))
            or re.fullmatch(
                r"-?\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                s,
            )
        ):
            segs.append("{}")
        else:
            segs.append(s)
    out = "/".join(segs)
    return (out.rstrip("/") or "/") if len(out) > 1 else out


def spec_endpoint_keys(spec: Any) -> set[str]:
    from lattice.spec.model import Step  # noqa: PLC0415

    return {
        f"{st.method.upper()} {tpl_path(st.path)}"
        for sc in spec.scenarios
        for st in sc.steps
        if isinstance(st, Step)
    }


def suffix_match(code: str, spec_path: str) -> bool:
    a, b = code.strip("/").split("/"), spec_path.strip("/").split("/")
    return len(a) >= len(b) and a[len(a) - len(b) :] == b


def score_endpoints(
    rep: dict[str, Any], truth_routes: Sequence[Sequence[str]], spec: Any
) -> dict[str, Any]:
    """Classify every route of the truth file and every endpoint of the spec; compare with the
    report's endpoint statuses. Exact keys (primary) and the suffix join Spike A used (secondary,
    labelled)."""
    code = {f"{m.upper()} {tpl_path(p)}" for m, p in truth_routes}
    spec_keys = spec_endpoint_keys(spec)
    excluded = {x["key"] for x in rep["excluded"] if x["kind"] == "route"}
    got = {e["key"]: e["status"] for e in rep["endpoints"]}

    def classify(keys_code: set[str], keys_spec: set[str]) -> dict[str, str]:
        out = {}
        for k in keys_code | keys_spec:
            if k in keys_code and k in keys_spec:
                out[k] = "implemented"
            elif k in keys_code:
                out[k] = "implemented_not_required"
            else:
                out[k] = "spec_only"
        return out

    want = classify(code, spec_keys)
    scored = {k: v for k, v in want.items() if k not in excluded}
    agree = sum(1 for k, v in scored.items() if got.get(k) == v)
    primary = {
        "keys": len(scored),
        "agree": agree,
        "accuracy": round(agree / len(scored), 3) if scored else None,
        "spec_only": sum(1 for v in scored.values() if v == "spec_only"),
        "implemented_not_specified_after_exclusions": sum(
            1 for v in scored.values() if v == "implemented_not_required"
        ),
        "report_statuses": {
            s: sum(1 for v in got.values() if v == s) for s in sorted(set(got.values()))
        },
        "disagreements": sorted(
            f"{k}: want {v}, report {got.get(k)}" for k, v in scored.items() if got.get(k) != v
        )[:40],
    }
    # secondary: a spec endpoint matches a code route when its path is a suffix of the code's
    spec_match = {}
    for sk in spec_keys:
        m, _, sp = sk.partition(" ")
        spec_match[sk] = any(
            c.startswith(m + " ") and suffix_match(c.partition(" ")[2], sp) for c in code
        )
    code_match = {
        c: any(
            c.startswith(sk.partition(" ")[0] + " ")
            and suffix_match(c.partition(" ")[2], sk.partition(" ")[2])
            for sk in spec_keys
        )
        for c in code
    }
    secondary = {
        "label": "suffix join (Spike A's rule: the spec path is a suffix of the code path); "
        "informational",
        "spec_endpoints_matching_a_code_route": f"{sum(spec_match.values())}/{len(spec_match)}",
        "code_routes_matching_a_spec_endpoint": f"{sum(code_match.values())}/{len(code_match)}",
        "spec_only_by_suffix": sorted(k for k, v in spec_match.items() if not v)[:20],
    }
    return {"primary_exact": primary, "secondary": secondary}


def run_held_out(args: argparse.Namespace) -> int:
    """``--app NAME=DIR@REF`` ... ``--spec DIR:MODULE:CALL`` ``--truth NAME=FILE`` (Spike A's
    route truth: ``{"routes": [[method, path], ...]}``) [``--workflows NAME=FILE``]: freeze the
    hashes, archive each app into scratch, capture its inventory (the given workflows, else B's
    static run), build the report with no trace (``requirement_basis: spec``), score the endpoint
    classification, run the report once more for determinism. Report-only. Never tunes."""
    from lattice.spec.implementation import (  # noqa: PLC0415
        build_report,
        load_inventory,
        report_json,
    )

    scratch, out = Path(args.scratch).resolve(), Path(args.out).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    files = [
        "lattice/spec/implementation.py",
        "lattice/importer/inventory.py",
        "lattice/spec/ask.py",
        "lattice/mcp/tools.py",
        "lattice/importer/frontends/go_gin.py",
        "lattice/importer/workflows.py",
        "lattice/importer/workflow_run.py",
        "dogfood/implementation_probes.py",
    ] + sorted(
        p.relative_to(REPO).as_posix() for p in (REPO / "lattice/gocheck/src").glob("goextract*.go")
    )
    freeze = {f: sha((REPO / f).read_bytes()) for f in files}
    (out / "freeze.sha256").write_text("".join(f"{h}  {n}\n" for n, h in freeze.items()))
    spec = load_spec_ref(args.spec)
    users = args.users.split(",") if args.users else list(spec.users)
    truths: dict[str, tuple[dict[str, Any], str]] = {}
    for item in args.truth:
        name, _, path = item.partition("=")
        raw = Path(path).read_bytes()
        truths[name] = (json.loads(raw), sha(raw))
    given_wf = dict(i.partition("=")[::2] for i in args.workflows)
    apps: dict[str, Any] = {}
    for item in args.app:
        name, _, rest = item.partition("=")
        src, _, ref = rest.partition("@")
        dest = scratch / f"h-{name}"
        row: dict[str, Any] = {}
        try:
            G.archive(Path(src), ref or "HEAD", dest)
            wf_file = given_wf.get(name)
            if wf_file is None:
                wf_out = scratch / "h-wf" / name
                shutil.rmtree(wf_out, ignore_errors=True)
                r = run_cli(
                    [
                        "import",
                        "workflows",
                        str(dest),
                        "--out",
                        str(wf_out),
                        "--static",
                        *[a for u in users for a in ("--user", u)],
                    ],
                    scratch,
                    f"h-wf-{name}",
                )
                row["workflows"] = (
                    "static run"
                    if r.returncode == 0
                    else f"static run failed: {r.stderr.strip()[-200:]}"
                )
                wf_file = str(wf_out / "workflows.json") if r.returncode == 0 else None
            else:
                row["workflows"] = "given"
            inv_dir = scratch / "h-inv" / name
            shutil.rmtree(inv_dir, ignore_errors=True)
            cmd = ["import", "inventory", str(dest), "--out", str(inv_dir)] + (
                ["--workflows", wf_file] if wf_file else []
            )
            r = run_cli(cmd, scratch, f"h-inv-{name}")
            if r.returncode != 0:
                row.update(crashed=True, error=f"inventory: {r.stderr.strip()[-300:]}")
                apps[name] = row
                continue
            inv = load_inventory(inv_dir, dest)
            rep = build_report(spec, None, inv)
            first = report_json(rep)
            second = report_json(build_report(spec, None, load_inventory(inv_dir, dest)))
            (out / f"h-{name}.report.json").write_text(first)
            truth, digest = truths[name]
            row.update(
                crashed=False,
                truth_sha256=digest,
                requirement_basis=rep["requirement_basis"],
                workflows_mismatch=rep["inventory"]["workflows_mismatch"],
                run_mode=rep["inventory"]["run_mode"],
                summary=rep["summary"],
                score=score_endpoints(rep, truth["routes"], spec),
                deterministic=first == second,
                report_sha256=sha(first.encode()),
            )
        except Exception as err:  # noqa: BLE001 -- a crash is a result
            row.update(crashed=True, error=f"{type(err).__name__}: {err}"[:400])
        apps[name] = row
    doc = {
        "freeze": freeze,
        "apps": apps,
        "crashes": sum(1 for r in apps.values() if r.get("crashed")),
        "deterministic": all(r.get("deterministic") for r in apps.values() if not r.get("crashed")),
        "predicted": "accuracy 0.90-1.00 per app; spec-only 0-2 and implemented-not-specified "
        "0-3 per app; "
        "0/6 crashes; identical repeats",
    }
    (out / "held-out.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n"
    )
    print(json.dumps(doc, indent=2, sort_keys=True, default=str))
    return 0


# ---- the Streak stage -----------------------------------------------------------------------

PROBE_ORDER = ("PE", "PR", "PV", "PO", "PC", "PA", "PT", "PW", "PP", "PD", "PX", "PB")


def run_streak(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    only = {x for x in args.only.split(",") if x}
    docker = not args.no_docker and docker_available()
    w = World(args)
    containers_before = all_containers() if docker_available() else []
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    setup(w, docker)
    (out / "setup.json").write_text(
        json.dumps(
            {
                "notes": w.setup_notes,
                "streak_ref": args.streak_ref,
                "streak_ref2": args.streak_ref2,
                "workflows_sha256": {k: sha(v.read_bytes()) for k, v in w.wf.items()},
                "inventory_sha256": {
                    k: sha((v / "inventory.json").read_bytes()) for k, v in w.inv.items()
                },
                "setup_seconds": round(time.perf_counter() - t0, 1),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    table: dict[str, Callable[[], Any]] = {
        "PE": lambda: probe_pe(w),
        "PR": lambda: probe_pr(w),
        "PV": lambda: probe_pv(w),
        "PO": lambda: probe_po(w),
        "PC": lambda: probe_pc(w),
        "PA": lambda: probe_pa(w),
        "PT": lambda: probe_pt(w),
        "PW": lambda: probe_pw(w, docker),
        "PP": lambda: probe_pp(w),
        "PD": lambda: probe_pd(w, args.runs),
        "PX": lambda: probe_px(w),
        "PB": lambda: probe_pb(w, args),
    }
    for pid in PROBE_ORDER:
        if only and pid not in only:
            continue
        t1 = time.perf_counter()
        try:
            r = table[pid]()
        except Exception as err:  # noqa: BLE001 -- a probe that crashes is a result
            import traceback  # noqa: PLC0415

            r = result(
                pid,
                "crashed",
                None,
                {"error": f"{type(err).__name__}: {err}", "trace": traceback.format_exc()[-1500:]},
                False,
            )
        r["seconds"] = round(time.perf_counter() - t1, 1)
        if pid == "PW":
            pf = r.pop("pf", None)
            if pf is not None:
                pf["seconds"] = r["seconds"]
                rows.append(r)
                rows.append(pf)
                continue
            rows.append(r)
            rows.append(
                not_run(
                    "PF",
                    "verify by replay on Docker",
                    "Docker not used (--no-docker or not running)",
                )
            )
            continue
        rows.append(r)
    if args.pytest:
        rows += run_pytest_and_check(w)
    containers_after = all_containers() if docker_available() else []
    leaked = sorted(set(containers_after) - set(containers_before))
    rows.append(result("LEAK", "containers left behind by the stage", [], leaked, not leaked))
    (out / "probes.json").write_text(json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n")
    lines = ["# Implementation diff probes (Streak stage)", ""]
    for r in rows:
        if not r.get("ran", True):
            lines.append(f"- NOT RUN {r['id']}: {r['probe']} -- {r['reason']}")
            continue
        verdict = "PASS" if r["pass"] else ("REPORT" if r["pass"] is None else "FAIL")
        obs = json.dumps(r["observed"], sort_keys=True, default=str)
        lines.append(f"- RAN {r['id']}: {verdict} -- {r['probe']}: observed {obs[:600]}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if all(r.get("pass") in (True, None) for r in rows) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--workflows-dyn", default="")
    ap.add_argument("--scratch")
    ap.add_argument("--out")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-docker", action="store_true")
    ap.add_argument("--pytest", action="store_true", help="also run pytest by directory")
    ap.add_argument("--held-out", action="store_true")
    ap.add_argument("--app", action="append", default=[], help="NAME=DIR@REF (held out)")
    ap.add_argument("--truth", action="append", default=[], help="NAME=FILE route truth (held out)")
    ap.add_argument("--workflows", action="append", default=[], help="NAME=FILE (held out)")
    ap.add_argument("--spec", default="", help="DIR:MODULE:CALL of the spec (held out)")
    ap.add_argument("--users", default="")
    args = ap.parse_args(argv)
    if not args.scratch or not args.out:
        ap.error("--scratch and --out are required")
    return run_held_out(args) if args.held_out else run_streak(args)


if __name__ == "__main__":
    raise SystemExit(main())
