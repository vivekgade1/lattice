"""Probes of the Go parity gaps, ranks 2 to 5 of J0 (docs/results/2026-10-17-go-parity-gaps-prereg.md,
Amendment 1; Spike J, node J1). Arm S (Docker-free, SQLite); arm M is NOT RUN here.

The pass rules are the pre-registration's section 4, written before any scored run. Where a
rule is ambiguous this file fixes one reading *before* the scored run and says so in the
probe's ``reading`` field; the results document lists every such reading as a deviation.

Run (see the pre-registration, section 7)::

    "$PY" dogfood/go_parity_probes.py --arm S --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"

``--only PI1,PA1`` restricts the probes (the mutants runner uses it). Nothing here edits a
fixture or the target directories: imports write to the scratch, the Go apps are built to the
scratch, and recording ``docker`` / ``claude`` shims come first on ``PATH``.
"""  # noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
PREREG = REPO / "docs/results/2026-10-17-go-parity-gaps-prereg.md"
SPEC_DIR = REPO / "tests/spec"
GOAPP = SPEC_DIR / "goapp"
GOGINAPP = SPEC_DIR / "goginapp"
GOEXTRACT = REPO / "tests/gocheck/fixtures/goextract"

# ---- the ground truth T-S2 (pre-registration, section 2; fixed before the build) ---------------

T_S2_ROUTES = {
    ("GET", "/health"): "Public",
    ("POST", "/login"): "Public",
    ("POST", "/configs/{cid}/runs"): "Member",
    ("GET", "/runs"): "Member",
    ("GET", "/runs/{rid}"): "Member",
    ("POST", "/runs/{rid}/cancel"): "Member",
    ("GET", "/configs"): "Member",
}
T_S2_TABLES = {"users", "configs", "runs"}

# the 7 module dirs of PB-X and the 16-hex prefixes the pre-registration recorded (section 4)
PB_X_DIRS = [
    "tests/gocheck/fixtures/goextract",
    "tests/gocheck/fixtures/app",
    "tests/gocheck/fixtures/partial",
    "tests/gocheck/fixtures/goextract_workflow",
    "tests/spec/fixtures/implementation/app",
    "lattice/studio/mock/appmap/app",
    "tests/spec/goapp",
]
PB_X_PREREG = ["00f49cb06e6b3c33", "a8841a6f3fb1b58b", "b02496cd2c3a5e19", "81532e404272d09e",
               "d30a96cc735d8df5", "d30a96cc735d8df5", "e95d65e81ca79469"]  # fmt: skip

SNAPSHOT_ROOTS = ["tests/spec/goapp", "tests/spec/goginapp", "tests/gocheck/fixtures",
                  "tests/spec/fixtures/implementation"]  # fmt: skip


def result(pid: str, probe: str, predicted: Any, observed: Any, passed: bool | None, **ev: Any):
    return {"id": pid, "probe": probe, "ran": passed is not None, "predicted": predicted,
            "observed": observed, "pass": passed, "evidence": ev}  # fmt: skip


def not_run(pid: str, probe: str, why: str, predicted: Any = None) -> dict[str, Any]:
    return {"id": pid, "probe": probe, "ran": False, "predicted": predicted, "observed": why,
            "pass": None, "evidence": {}}  # fmt: skip


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd: list[str], *, cwd: Path | str = REPO, env: Mapping[str, str] | None = None,
        timeout: float = 900, stdin: bytes | None = None) -> tuple[int, str, str, float]:  # fmt: skip  # noqa: E501
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(cwd), env=dict(env or os.environ), input=stdin,
                          capture_output=True, timeout=timeout, check=False)  # fmt: skip
    return (proc.returncode, proc.stdout.decode(errors="replace"),
            proc.stderr.decode(errors="replace"), time.perf_counter() - t0)  # fmt: skip


# ---- the context --------------------------------------------------------------------------------


class Ctx:
    def __init__(self, scratch: Path, out: Path, base_ref: str) -> None:
        self.scratch, self.out, self.base_ref = scratch, out, base_ref
        self.base_dir: Path | None = None
        self.py = sys.executable
        self.shims = scratch / "shims"
        self.shim_log = scratch / "shim.log"
        self.goapp_bin: Path | None = None
        self.goginapp_bin: Path | None = None
        self.s2_error: str | None = None
        self.s1_error: str | None = None
        self.import_dirs: list[Path] = []
        self.timings: dict[str, float] = {}
        self.snap0: dict[str, Any] = {}
        self.cache: dict[str, Any] = {}

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(PYTHONDONTWRITEBYTECODE="1", GOTOOLCHAIN="auto",
                   LATTICE_SHIM_LOG=str(self.shim_log))  # fmt: skip
        if self.goapp_bin:
            env["LATTICE_GOAPP_BIN"] = str(self.goapp_bin)
        if self.goginapp_bin:
            env["LATTICE_GOGINAPP_BIN"] = str(self.goginapp_bin)
        env.update(extra)
        return env


def make_shims(ctx: Ctx) -> None:
    ctx.shims.mkdir(parents=True, exist_ok=True)
    for name in ("docker", "claude"):
        p = ctx.shims / name
        p.write_text(f'#!/bin/sh\necho "{name} $*" >> "$LATTICE_SHIM_LOG"\nexit 127\n')
        p.chmod(0o755)
    ctx.shim_log.write_text("")
    os.environ["PATH"] = f"{ctx.shims}:{os.environ['PATH']}"
    os.environ["LATTICE_SHIM_LOG"] = str(ctx.shim_log)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["GOTOOLCHAIN"] = "auto"


def snapshot(root: Path) -> dict[str, Any]:
    """sha256, mode, mtime and the listing (hidden files and directories included)."""
    out: dict[str, Any] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_symlink():
            out[rel] = ["symlink", os.readlink(p), st.st_mode]
        elif p.is_dir():
            out[rel] = ["dir", st.st_mode, st.st_mtime_ns]
        else:
            out[rel] = [sha(p), st.st_mode, st.st_mtime_ns]
    return out


def snapshots() -> dict[str, Any]:
    return {r: snapshot(REPO / r) for r in SNAPSHOT_ROOTS}


def snapshot_diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    changed = []
    for root in sorted(set(a) | set(b)):
        x, y = a.get(root, {}), b.get(root, {})
        for rel in sorted(set(x) | set(y)):
            if x.get(rel) != y.get(rel):
                changed.append(f"{root}/{rel}")
    return changed


def archive(ref: str, dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    tar = subprocess.run(["git", "-C", str(REPO), "archive", ref], capture_output=True,
                         check=True).stdout  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)


def go_build(src: Path, out: Path, *, flags: str = "-mod=readonly") -> tuple[bool, str, float]:
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GOFLAGS=flags, GOTOOLCHAIN="auto")
    rc, _, err, wall = run(["go", "build", "-trimpath", "-buildvcs=false", "-o", str(out), "."],
                           cwd=src, env=env)  # fmt: skip
    return rc == 0, err[-1500:], wall


# ---- S2: import ----------------------------------------------------------------------------------


def import_s2(ctx: Ctx, out_dir: Path, *, lazy_build: bool = False) -> dict[str, Any]:
    """The rules-first import of S2 by the CLI, as the pre-registration's PI1 does by hand."""
    shutil.rmtree(out_dir, ignore_errors=True)
    env = ctx.env()
    if lazy_build:
        env.pop("LATTICE_GOGINAPP_BIN", None)
    rc, out, err, wall = run([ctx.py, "-m", "lattice.importer", "tests/spec", "--package",
                              "goginapp", "--rules-only", "--out", str(out_dir)], env=env)  # fmt: skip  # noqa: E501
    return {"rc": rc, "stdout": out[-600:], "stderr": err[-1500:], "wall_s": wall, "dir": out_dir}


def load_report(d: Path) -> dict[str, Any]:
    return json.loads((d / "report.json").read_text())  # type: ignore[no-any-return]


def load_candidate(d: Path) -> Any:
    import importlib.util

    path = d / "candidate_spec.py"
    spec = importlib.util.spec_from_file_location(f"cand_{abs(hash(str(d)))}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VERIFIED = ("verified", "verified (corrected)")


def probe_pi1(ctx: Ctx, d: Path) -> dict[str, Any]:
    rep, cand = load_report(d), load_candidate(d)
    routes = {(c.method, c.path): c.auth for c in cand.CONTRACTS}
    observed_roles = sorted({a for a in routes.values() if a != "Public"})
    mapped = {k: ("Public" if a == "Public" else "Member") for k, a in routes.items()}
    tables = set(cand.PROFILE.api.table_names.values())
    ok = (rep["rules"]["framework"] == "gin" and mapped == T_S2_ROUTES
          and len(observed_roles) == 1 and tables == T_S2_TABLES)  # fmt: skip
    return result("PI1", "rules-first import of S2: framework, routes, contracts, tables",
                  {"framework": "gin", "routes": 7, "contracts": 7, "tables": 3,
                   "public": ["GET /health", "POST /login"]},
                  {"framework": rep["rules"]["framework"], "routes": len(routes),
                   "contracts": len(cand.CONTRACTS), "tables": sorted(tables),
                   "auth": {f"{m} {p}": a for (m, p), a in routes.items()}},
                  ok, reading="Member = the one non-Public role the extractor names for an "
                  f"authenticated route (observed: {observed_roles})")  # fmt: skip


def s2_extract(ctx: Ctx) -> dict[str, Any]:
    if "s2x" not in ctx.cache:
        from lattice.gocheck.derive import policy_extract

        ctx.cache["s2x"] = policy_extract(GOGINAPP)
    return ctx.cache["s2x"]  # type: ignore[no-any-return]


def probe_pi2(ctx: Ctx, d: Path) -> dict[str, Any]:
    rep, cand = load_report(d), load_candidate(d)
    inf = rep["rules"].get("inferences", [])
    x = s2_extract(ctx)
    hows: dict[tuple[str, int], str] = {}
    for r in x["routes"]:
        for o in r["owner_uses"]:
            if (o["model"], o["column"]) == ("Run", "user_id"):
                hows[(o["file"], o["line"])] = o["how"]
    ev = inf[0]["evidence"] if len(inf) == 1 else []
    got = sorted(hows[(f, int(n))] for f, n in (e.removeprefix("code:").rsplit(":", 1) for e in ev)
                 if (f, int(n)) in hows)  # fmt: skip
    tables = cand.PROFILE.api.tables
    owners = {t: tables[t].owner for t in tables}
    ok = (len(inf) == 1 and inf[0]["table"] == "Run" and inf[0]["column"] == "user_id"
          and len(ev) == 4 and got == ["assign", "compare", "compare", "compare"]
          and owners == {"User": None, "Config": None, "Run": "user_id"})  # fmt: skip
    return result(
        "PI2",
        "owners and inferences[] of the S2 import",
        {
            "inferences": 1,
            "evidence_lines": 4,
            "how": "3 compare, 1 assign",
            "owners": {"Run": "user_id", "Config": None, "User": None},
        },
        {"inferences": inf, "how": got, "owners": owners},
        ok,
    )


def probe_pi3(ctx: Ctx, d: Path) -> dict[str, Any]:
    rep = load_report(d)
    items = rep["rules"]["items"]
    n = len(items)
    count: dict[str, int] = {}
    for v in items.values():
        count[v["verdict"]] = count.get(v["verdict"], 0) + 1
    verified = sum(count.get(k, 0) for k in VERIFIED)
    share = verified / n if n else 0.0
    corrections = {c["item"] for c in rep["rules"]["corrections"]}
    baseline = {b["item"] for b in rep["rules"]["baseline"]}
    refuted = [i for i, v in items.items() if v["verdict"] == "refuted"]
    refuted_bad = [i for i in refuted
                   if not ((items[i].get("correction") or i in corrections or i in baseline)
                           and (items[i].get("reason") or items[i].get("correction", {}).get("reason")))]  # fmt: skip  # noqa: E501
    unex_bad = [i for i, v in items.items() if v["verdict"] == "unexercised" and not v["reason"]]
    ok = share >= 0.60 and not refuted_bad and not unex_bad
    return result("PI3", "rule items of the S2 import on the original (seed)",
                  {"items": "28-48", "verified_share": "0.65-0.90", "refuted": "0-3",
                   "gate": "share >= 0.60; every refuted item has a correction or baseline entry "
                   "with its reason; every unexercised item has a reason"},
                  {"items": n, "verdicts": count, "verified_share": round(share, 4),
                   "refuted": len(refuted), "refuted_without_correction": refuted_bad,
                   "unexercised_without_reason": unex_bad,
                   "refuted_items": {i: items[i]["reason"] for i in refuted}},
                  ok, in_predicted_range={"items": 28 <= n <= 48,
                                          "share": 0.65 <= share <= 0.90,
                                          "refuted": len(refuted) <= 3})  # fmt: skip


# ---- the replicator: layer R of the imported spec on the original, per seed variant -------------


def s2_machinery(ctx: Ctx) -> dict[str, Any]:
    """What ``rules.import_rules`` builds around the probes (front end, factory, runtime, seed,
    callers, unreachable routes), rebuilt here so the imported candidate spec can be probed
    again. It repeats import_rules' wiring and is a probe-side copy by design."""
    if "m" in ctx.cache:
        return ctx.cache["m"]  # type: ignore[no-any-return]
    from lattice.importer import go_rules, rules
    from lattice.importer.frontends import select
    from lattice.importer.lift import lift_repo

    sys.path.insert(0, str(REPO))
    root, package = SPEC_DIR, "goginapp"
    fe = go_rules.InferringFrontEnd(select("gin", root, package))
    factory, name = go_rules.harness_factory(root, package)  # type: ignore[misc]
    model = fe.extract(root, package)
    runtime = rules._runtime(fe, root, package, model)
    result_ = lift_repo(root, package, factory, only=(), frontend=fe, runtime=runtime)
    c = result_.context
    api = result_.lattice_profile.api
    seeder = rules.Seeder(result_.model, c.inputs.tables, c.inputs.auth, c.inputs.insert_hook,
                          runtime, factory, api.uuid_storage, api.datetime_storage)  # fmt: skip
    unreachable = {f"{e.method} {e.path}": e.reason for e in result_.model.excluded_routes
                   if e.kind == "unreachable"}  # fmt: skip
    for ep, why in rules.network_routes(root, package, result_.model).items():
        unreachable.setdefault(ep, why)
    for ep, why in rules.shadowed_routes(result_.model).items():
        unreachable.setdefault(ep, why)
    m = {"result": result_, "seeder": seeder, "sessions": rules._sessions(runtime, factory),
         "unreachable": unreachable, "rules": rules}  # fmt: skip
    ctx.cache["m"] = m
    return m


def probe_r_on_spec(ctx: Ctx, spec: Any, variant: str, only: list[str] | None = None) -> Any:
    from lattice.spec.probes import run_probes

    m = s2_machinery(ctx)
    seeder, result_ = m["seeder"], m["result"]
    who = m["rules"].callers(result_, seeder, variant)
    kw = dict(login=who.login, mint_token=who.mint, scheme=who.scheme, refusals=who.refusals,
              login_route=who.login_route, auth_headers=who.headers,
              inactive_login=who.inactive_login, unreachable=m["unreachable"], variant=variant,
              auth_present=who.present)  # fmt: skip
    if only:
        kw["only"] = only
    return run_probes(replace(spec, users=seeder.credentials(variant)), m["sessions"],
                      seeder.make_db, **kw)  # fmt: skip


def verdict_map(rr: Any) -> dict[str, str]:
    return {i: v.verdict for i, v in rr.items.items()}


def probe_pi4(ctx: Ctx, d: Path) -> dict[str, Any]:
    cand = load_candidate(d)
    seed = verdict_map(probe_r_on_spec(ctx, cand.SPEC, "seed"))
    v2 = verdict_map(probe_r_on_spec(ctx, cand.SPEC, "variant2"))
    ctx.cache["pi4_seed"] = seed
    base = [i for i, v in seed.items() if v in VERIFIED]
    still = [i for i in base if v2.get(i) in VERIFIED]
    frac = len(still) / len(base) if base else 0.0
    lost = {i: v2.get(i) for i in base if i not in still}
    return result("PI4", "the corrected spec re-probed on variant2",
                  "every seed-verified item still verified (>= 0.95)",
                  {"seed_verified": len(base), "still_verified": len(still),
                   "fraction": round(frac, 4), "lost": lost}, frac >= 0.95)  # fmt: skip


def probe_pi5(ctx: Ctx, d: Path) -> dict[str, Any]:
    props = json.loads((d / "proposals.json").read_text())
    machine = [p for p in props if p.get("kind") == "machine"
               or "machine" in json.dumps(p.get("spec_object", {})).lower()]  # fmt: skip
    pending = all(p.get("status") == "pending" for p in props)
    targeted = [p for p in machine if "runs" in json.dumps(p).lower()
                and "queued" in json.dumps(p) and "cancelled" in json.dumps(p)]  # fmt: skip
    ok = bool(targeted) and pending
    return result("PI5", "proposals.json (mining)",
                  "1-6 proposals including the machine runs.status queued -> cancelled",
                  {"count": len(props), "kinds": sorted({p.get("kind") for p in props}),
                   "machine_runs_status": bool(targeted), "all_pending": pending,
                   "sentences": [p.get("sentence") for p in props]}, ok,
                  in_predicted_range=1 <= len(props) <= 6)  # fmt: skip


def probe_pi6(ctx: Ctx, d: Path) -> dict[str, Any]:
    rev = ctx.scratch / "review"
    shutil.rmtree(rev, ignore_errors=True)
    shutil.copytree(d, rev)
    rc, out, err, _ = run([ctx.py, "-m", "lattice.importer", "review", str(rev), "list"],
                          env=ctx.env())  # fmt: skip
    listed = {ln.split("\t")[0] for ln in out.splitlines() if "\trule\t" in ln}
    rep = load_report(d)
    unverified = {i for i, v in rep["rules"]["items"].items() if v["verdict"] not in VERIFIED}
    ok = rc == 0 and listed == unverified
    return result("PI6", "owner review: python -m lattice.importer review <dir> list",
                  "exit 0; item ids equal report.json's",
                  {"exit": rc, "listed": len(listed), "report_unverified": len(unverified),
                   "listed_not_in_report": sorted(listed - unverified),
                   "report_not_listed": sorted(unverified - listed), "stderr": err[-300:]}, ok,
                  reading="`review list` shows the items that are not verified; equality is "
                  "checked against the report's not-verified items, exactly")  # fmt: skip


NORMALISED = ("report.md",)


def normalised_tree(d: Path) -> dict[str, str]:
    """The import directory, byte-for-byte except the timing fields (report.json's
    ``cost.wall_s`` and ``rules.timing``) and report.md (it renders the wall time)."""
    out: dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(d).as_posix()
        if rel in NORMALISED:
            continue
        data = p.read_bytes()
        if rel == "report.json":
            r = json.loads(data)
            r["cost"]["wall_s"] = "<wall_s>"
            if r.get("rules"):
                r["rules"]["timing"] = "<timing>"
            data = (json.dumps(r, indent=2) + "\n").encode()
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


def probe_pi7(ctx: Ctx, n: int = 10) -> dict[str, Any]:
    trees, walls = [], []
    for i in range(n):
        d = ctx.scratch / "imp7" / f"r{i}"
        r = import_s2(ctx, d)
        walls.append(round(r["wall_s"], 2))
        trees.append(normalised_tree(d) if r["rc"] == 0 else {"error": r["stderr"][-200:]})
    first = trees[0]
    same = sum(1 for t in trees if t == first)
    diff = sorted({k for t in trees for k in set(t) | set(first) if t.get(k) != first.get(k)})
    ctx.timings["pi7_import_walls"] = statistics.mean(walls)
    return result("PI7", "the S2 import 10x into separate scratch dirs",
                  "byte-identical after the timing normaliser", {"identical": same, "of": n,
                  "differing_files": diff, "walls_s": walls}, same == n,
                  reading="normaliser: report.json cost.wall_s and rules.timing replaced; "
                  "report.md left out (it renders the wall time)")  # fmt: skip


# ---- arm S analysis: layer A -----------------------------------------------------------------


def run_gocheck_keys(module_dir: Path, policy: Any) -> set[str]:
    from lattice.gocheck import run_gocheck

    return {f.key for f in run_gocheck(module_dir, replace(policy, baseline=frozenset()),
                                       strict=True)}  # fmt: skip


def probe_pa1(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy

    p = derive_go_policy(GOGINAPP)
    keys = run_gocheck_keys(GOGINAPP, p)
    got = {"tables": len(p.tables), "users_table": p.users_table, "owners": dict(p.owners),
           "private": {k: list(v) for k, v in p.private.items()},
           "admin_routes": [list(r) for r in p.admin_routes], "machines": dict(p.machines),
           "baseline": sorted(p.baseline)}  # fmt: skip
    exact = (got["tables"] == 3 and got["users_table"] == "users"
             and got["owners"] == {"Run": "user_id"}
             and got["private"] == {"User": ["password", "token"]}
             and got["admin_routes"] == [] and got["machines"] == {})  # fmt: skip
    base_ok = set(p.baseline) == keys
    return result("PA1", "derive_go_policy on S2",
                  {"tables": 3, "users_table": "users", "owners": {"Run": "user_id"},
                   "private": {"User": ["password", "token"]}, "admin_routes": [],
                   "machines": {}, "baseline": "0-2 keys"}, got, exact and base_ok,
                  baseline_equals_separate_run=base_ok,
                  baseline_in_predicted_range=len(p.baseline) <= 2,
                  note="users_table is compared with the pre-registration's `users` (the SQL "
                  "name); D3 says the extract's auth users table, a struct name")  # fmt: skip


def spec_for(run_schema_owned: bool, machine: bool) -> Any:
    from lattice.profile import ApiProfile, Profile, RoleColumn
    from lattice.spec import Machine, Spec
    from lattice.typesys import Scalar, Schema

    S = Scalar
    user = Schema.from_pairs([("id", S.INT), ("name", S.STR), ("password", S.STR),
                              ("token", S.STR)], keys=[("id",), ("token",)],
                             private=["password", "token"])  # fmt: skip
    config = Schema.from_pairs([("id", S.INT), ("name", S.STR)], keys=[("id",)])
    cols = [("id", S.INT), ("config_id", S.INT), ("user_id", S.INT), ("status", S.STR)]
    run_ = (Schema.from_pairs(cols, keys=[("id",)], owner="user_id") if run_schema_owned
            else Schema.from_pairs(cols, keys=[("id",)]))  # fmt: skip
    api = ApiProfile(tables={"User": user, "Config": config, "Run": run_},
                     table_names={"User": "users", "Config": "configs", "Run": "runs"},
                     roles=frozenset({"Public", "Member"}), users_table="User",
                     roles_from=RoleColumn("name"))  # fmt: skip
    machines = (Machine("Run", "status", {"queued": ("cancelled",)}, initial=("queued",)),)
    return Spec(profile=Profile(schemas={}, api=api), machines=machines if machine else ())


def probe_pa2(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy, go_spec_policy

    derived = derive_go_policy(GOGINAPP)
    p = go_spec_policy(spec_for(False, True), GOGINAPP, derived)
    got = {
        "run_owned": "Run" in p.owners,
        "machine": sorted(p.machines),
        "baseline": sorted(p.baseline),
        "derived_baseline": sorted(derived.baseline),
    }
    ok = (not got["run_owned"] and got["machine"] == ["Run"] and got["baseline"] == []
          and bool(got["derived_baseline"]))  # fmt: skip
    return result(
        "PA2",
        "go_spec_policy with a spec that declares Run with no owner + the machine",
        {"run_owned": False, "machine": ["Run"], "baseline": []},
        got,
        ok,
    )


MUT_EDITS = {
    "JM5": (
        "api/api.go",
        'Where("id = ? AND user_id = ?", rid, c.GetUint("userID")).First(&run).Error; err != nil {\n\t\t\tdetail(c, http.StatusNotFound, "Not found")\n\t\t\treturn\n\t\t}\n\t\tif run.Status != "queued"',  # noqa: E501
        'Where("id = ?", rid).First(&run).Error; err != nil {\n\t\t\tdetail(c, http.StatusNotFound, "Not found")\n\t\t\treturn\n\t\t}\n\t\tif run.Status != "queued"',  # noqa: E501
    ),  # noqa: E501
    "JM6": (
        "models/models.go",
        'Token    string `gorm:"size:64;not null;uniqueIndex" json:"-"`',
        'Token    string `gorm:"size:64;not null;uniqueIndex"`',
    ),
}


def mutate_goginapp(ctx: Ctx, mid: str) -> Path:
    dest = ctx.scratch / "mut" / mid.lower() / "goginapp"
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(GOGINAPP, dest)
    rel, old, new = MUT_EDITS[mid]
    f = dest / rel
    text = f.read_text()
    assert text.count(old) == 1, (mid, text.count(old))
    f.write_text(text.replace(old, new))
    return dest


def findings_of(module_dir: Path, policy: Any) -> list[Any]:
    from lattice.gocheck import run_gocheck

    return run_gocheck(module_dir, policy, strict=True)


def probe_pa3(ctx: Ctx, d: Path) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy, go_spec_policy

    cand = load_candidate(d)
    derived = derive_go_policy(GOGINAPP)
    pol = go_spec_policy(cand.SPEC, GOGINAPP, derived)
    orig = findings_of(GOGINAPP, pol)
    outside = [f for f in orig if f.key not in derived.baseline]
    sig = {(f.code, f.message) for f in orig}
    rows: dict[str, Any] = {}
    for mid in ("JM5", "JM6"):
        copy = mutate_goginapp(ctx, mid)
        found = findings_of(copy, pol)
        new = [f for f in found if (f.code, f.message) not in sig]
        rows[mid] = [{"code": f.code, "key": f.key, "message": f.message[:160]} for f in new]
    jm5 = [x for x in rows["JM5"] if x["code"] == "GO801" and "/runs/{rid}/cancel" in x["key"]]
    jm6 = [x for x in rows["JM6"] if x["code"] == "GO802"]
    ok = not outside and bool(jm5) and bool(jm6)
    return result("PA3", "edits on a scratch copy of S2 under go_spec_policy of its candidate spec",
                  {"JM5": "GO801 on POST /runs/{rid}/cancel", "JM6": "GO802 on the users response",
                   "original_outside_baseline": 0},
                  {"original_total": len(orig), "original_outside_baseline": len(outside),
                   "original_outside": [f.key for f in outside], "new_findings": rows}, ok,
                  reading="a mutant's finding is new when its (code, message) is not among the "
                  "original's: the baseline's key (GO802|POST /login|User.token) is shared with "
                  "the mutant's own GO802 on the same route")  # fmt: skip


def probe_pa4(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck.derive import derive_go_policy

    p = derive_go_policy(GOEXTRACT)
    got = {"owners": dict(p.owners), "private": {k: list(v) for k, v in p.private.items()}}
    ok = got["owners"] == {"Item": "owner_id", "Audit": "actor"} and got["private"] == {
        "User": ["password_hash"]}  # fmt: skip
    return result(
        "PA4",
        "static: derive_go_policy on tests/gocheck/fixtures/goextract",
        {"owners": {"Item": "owner_id", "Audit": "actor"}, "private": {"User": ["password_hash"]}},
        got,
        ok,
    )


def pairing_count(extract: Mapping[str, Any]) -> dict[str, int]:
    """Informational (never used by a check): owner `compare` uses with a raises[] entry of
    the same route and file within 3 lines."""
    total = paired = 0
    for r in extract["routes"]:
        for o in r["owner_uses"]:
            if o["how"] != "compare":
                continue
            total += 1
            if any(x.get("file", o["file"]) == o["file"] and abs(x["line"] - o["line"]) <= 3
                   for x in r["raises"]):  # fmt: skip
                paired += 1
    return {"compare_uses": total, "with_a_raise_within_3_lines": paired}


def probe_pg805(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck import GO_GATED, gated_note
    from lattice.gocheck.derive import policy_extract

    base = ctx.scratch / "base"
    rc, out, err, _ = run([ctx.py, "-c", "import json;from lattice.gocheck import GO_GATED,"
                           "gated_note;print(json.dumps([gated_note(),GO_GATED],sort_keys=True))"],
                          cwd=base, env=ctx.env(PYTHONPATH=str(base)))  # fmt: skip
    mine = json.dumps([gated_note(), GO_GATED], sort_keys=True)
    same = rc == 0 and out.strip() == mine
    count = {"goginapp": pairing_count(s2_extract(ctx)),
             "goextract": pairing_count(policy_extract(GOEXTRACT))}  # fmt: skip
    return result(
        "PG805",
        "GO805 gated: gated_note() and GO_GATED",
        "byte-identical to base",
        {"identical": same, "pairing_count_informational": count},
        same,
    )


# ---- arm S: layer R and layer B on S1 -------------------------------------------------------------  # noqa: E501


def s1_check(ctx: Ctx, layers: str, *, binary: Path | None = None, bug: str = "") -> Any:
    from lattice.spec import check
    from tests.spec import go_harness_rb as rb

    os.environ["LATTICE_GOAPP_BIN"] = str(binary or ctx.goapp_bin)
    if bug:
        os.environ["GOAPP_BUG"] = bug
    else:
        os.environ.pop("GOAPP_BUG", None)
    try:
        return check(rb.SPEC, rb.make_app, rb.build_db, layers=layers, runtime=rb.RUNTIME,
                     login=rb.LOGIN, rules=rb.RULES)  # fmt: skip
    finally:
        os.environ.pop("GOAPP_BUG", None)


def r_summary(report: Any) -> dict[str, Any]:
    s = report.rules.summary["items"]
    n = sum(s.values())
    return {"items": n, "verdicts": dict(s), "verified_share": round(s["verified"] / n, 4),
            "routes": report.rules.summary["routes"]}  # fmt: skip


def finding_set(report: Any) -> list[str]:
    return sorted(f"{f.layer}|{f.endpoint}|{f.case}" for f in report.findings)


def probe_pr1(ctx: Ctx) -> dict[str, Any]:
    t0 = time.perf_counter()
    rep = s1_check(ctx, "R")
    ctx.timings["r_s1"] = time.perf_counter() - t0
    ctx.cache["pr1"] = rep
    s = r_summary(rep)
    no_reason = [
        i for i, v in rep.rules.items.items() if v.verdict == "unexercised" and not v.reason
    ]
    refuted = s["verdicts"].get("refuted", 0)
    ok = refuted == 0 and s["verified_share"] >= 0.50 and not no_reason
    return result("PR1", "layer R on S1 (go_harness_rb, the safe app)",
                  {"items": "18-30", "verified_share": "0.55-0.85", "refuted": 0}, s, ok,
                  unexercised_without_reason=no_reason,
                  in_predicted_range={"items": 18 <= s["items"] <= 30,
                                      "share": 0.55 <= s["verified_share"] <= 0.85},
                  unexercised={i: v.reason for i, v in rep.rules.items.items()
                               if v.verdict == "unexercised"})  # fmt: skip


def build_goapp_mutant(ctx: Ctx, mid: str) -> Path:
    dest = ctx.scratch / "mut" / mid.lower() / "goapp"
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(GOAPP, dest)
    f = dest / "main.go"
    text = f.read_text()
    head, sep, rest = text.partition("func (s *server) getRun(")
    body, sep2, tail = rest.partition("func main()")
    if mid == "JM1":  # getRun skips caller()
        old = '\tif _, ok := s.caller(r); !ok {\n\t\tdetail(w, 401, "Not authenticated")\n\t\treturn\n\t}\n'  # noqa: E501
        assert body.count(old) == 1
        body = body.replace(old, "")
    elif mid == "JM2":  # getRun answers 403 for a missing run
        old = 'detail(w, 404, "Not found")'
        assert body.count(old) == 1
        body = body.replace(old, 'detail(w, 403, "Not found")')
    f.write_text(head + sep + body + sep2 + tail)
    ok, err, _ = go_build(dest, dest / "goapp", flags="-mod=readonly")
    if not ok:
        raise RuntimeError(f"{mid} does not build: {err}")
    return dest / "goapp"


def probe_pr2(ctx: Ctx) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for mid in ("JM1", "JM2"):
        binary = build_goapp_mutant(ctx, mid)
        rep = s1_check(ctx, "R", binary=binary)
        refuted = [(i, v.reason) for i, v in rep.rules.items.items() if v.verdict == "refuted"]
        rows[mid] = {"refuted": [i for i, _ in refuted],
                     "on_route": [i for i, _ in refuted if "GET /runs/{rid}" in i]}  # fmt: skip
    ok = all(r["on_route"] for r in rows.values())
    return result(
        "PR2",
        "layer R on S1, app mutants JM1 and JM2",
        "each refutes >= 1 item on GET /runs/{rid}",
        rows,
        ok,
    )


def probe_pr3(ctx: Ctx, d: Path) -> dict[str, Any]:
    cand = load_candidate(d)
    rep = load_report(d)
    mine = verdict_map(probe_r_on_spec(ctx, cand.SPEC, "seed"))
    theirs = {i: v["verdict"] for i, v in rep["rules"]["items"].items()}
    diff = {i: [theirs.get(i), mine.get(i)] for i in sorted(set(theirs) | set(mine))
            if theirs.get(i) != mine.get(i)}  # fmt: skip
    return result("PR3", "layer R on S2 with the imported candidate_spec.py",
                  "the same item verdicts as the import's seed run",
                  {"items_import": len(theirs), "items_rerun": len(mine),
                   "differences [import, rerun]": diff}, not diff,
                  reading="literal: report.json's final item verdicts (corrections included) "
                  "against layer R of the corrected candidate_spec.py on the same seed")  # fmt: skip  # noqa: E501


def probe_pr4(ctx: Ctx, n: int = 10) -> dict[str, Any]:
    maps, finds = [], []
    for _ in range(n):
        rep = s1_check(ctx, "R")
        maps.append({i: v.verdict for i, v in rep.rules.items.items()})
        finds.append(finding_set(rep))
    same = sum(1 for m, f in zip(maps, finds, strict=True) if m == maps[0] and f == finds[0])
    return result(
        "PR4",
        "layer R on S1, 10x",
        "identical item verdicts and findings",
        {"identical": same, "of": n},
        same == n,
    )


def probe_pb1(ctx: Ctx) -> dict[str, Any]:
    t0 = time.perf_counter()
    rep = s1_check(ctx, "B")
    ctx.timings["b_s1"] = time.perf_counter() - t0
    cases = len(rep.cases)
    ok = not rep.findings and cases >= 6
    return result("PB1", "layer B on S1, safe app", {"cases": "6-30", "findings": 0},
                  {"cases": cases, "findings": finding_set(rep)}, ok,
                  in_predicted_range=6 <= cases <= 30,
                  reading="B takes rules=go_harness_rb.RULES (a 422 is a 422): the CLI has no "
                  "--rules, and the default rule flags the Go app's 422 body (finding F-B422)")  # fmt: skip  # noqa: E501


def probe_pb2(ctx: Ctx) -> dict[str, Any]:
    want = {"nocheck": "POST /configs/{cid}/runs", "nocancel": "POST /runs/{rid}/cancel"}
    rows, ok = {}, True
    for bug, endpoint in want.items():
        rep = s1_check(ctx, "B", bug=bug)
        eps = sorted({f.endpoint for f in rep.findings})
        rows[bug] = {"endpoints": eps, "findings": len(rep.findings)}
        ok = ok and endpoint in eps
    return result(
        "PB2",
        "layer B on S1, GOAPP_BUG=nocheck / nocancel",
        {k: f">= 1 B finding on {v}" for k, v in want.items()},
        rows,
        ok,
    )


def probe_pb3(ctx: Ctx, n: int = 10) -> dict[str, Any]:
    sets = [finding_set(s1_check(ctx, "B")) for _ in range(n)]
    sets_bug = [finding_set(s1_check(ctx, "B", bug="nocheck")) for _ in range(n)]
    same = sum(1 for s in sets if s == sets[0]) + sum(1 for s in sets_bug if s == sets_bug[0])
    return result("PB3", "layer B on S1, 10x (safe app and nocheck)",
                  "identical finding sets", {"identical": same, "of": 2 * n,
                  "safe": sets[0], "nocheck": sets_bug[0]}, same == 2 * n)  # fmt: skip


# ---- PW, PX ---------------------------------------------------------------------------------------  # noqa: E501


def probe_pw(ctx: Ctx) -> dict[str, Any]:
    changed = snapshot_diff(ctx.snap0, snapshots())
    calls = [ln for ln in ctx.shim_log.read_text().splitlines() if ln.strip()]
    return result("PW", "snapshot of the fixtures around every run; docker/claude shims",
                  {"changed": 0, "shim_calls": 0}, {"changed": changed, "shim_calls": calls},
                  not changed and not calls)  # fmt: skip


def probe_px(ctx: Ctx) -> dict[str, Any]:
    t = ctx.timings
    lazy = t.get("s2_import_lazy_build_s")
    got = {"s2_import_with_go_build_s": lazy, "s2_import_prebuilt_mean_s": t.get("pi7_import_walls"),  # noqa: E501
           "r_s1_s": t.get("r_s1"), "b_s1_s": t.get("b_s1")}  # fmt: skip
    ok = (lazy is not None and lazy <= 180 and t.get("r_s1", 1e9) <= 30
          and t.get("b_s1", 1e9) <= 60)  # fmt: skip
    return result(
        "PX",
        "wall time",
        {
            "s2_import": "15-90 s",
            "r_s1": "2-15 s",
            "b_s1": "3-20 s",
            "gate": "<= 180 s; <= 30 s; <= 60 s",
        },
        {k: (round(v, 2) if v is not None else None) for k, v in got.items()},
        ok,
    )


# ---- PB: byte compatibility -----------------------------------------------------------------------  # noqa: E501


def frozen_rows() -> list[tuple[str, str]]:
    text = PREREG.read_text()
    sec = text.split("**Frozen (sha256")[1].split("The two edited Go files")[0]
    return [
        (f.replace("\\", ""), s)
        for f, s in re.findall(r"^\| (\S.*?) \| ([0-9a-f]{64}) \|", sec, re.M)
    ]


def group_hash(root: Path) -> tuple[str, int]:
    src = root / "lattice/gocheck/src"
    names = sorted(x.name for x in src.iterdir() if x.suffix == ".go"
                   and x.name not in ("goextract.go", "goextract_tables.go"))  # fmt: skip
    return hashlib.sha256(b"".join((src / n).read_bytes() for n in names)).hexdigest(), len(names)


def file_hash(root: Path, rel: str) -> str:
    if rel.startswith("lattice/gocheck/src/*.go"):
        return group_hash(root)[0]
    return sha(root / rel)


def probe_pb_f(ctx: Ctx) -> dict[str, Any]:
    base = ctx.scratch / "base"
    rows, stale, touched = [], [], []
    for rel, listed in frozen_rows():
        mine, theirs = file_hash(REPO, rel), file_hash(base, rel)
        rows.append({"file": rel, "prereg": listed[:12], "base": theirs[:12], "now": mine[:12]})
        if mine != listed:
            stale.append(rel)
        if mine != theirs:
            touched.append(rel)
    moved = [r for r in stale if r not in touched]
    return result("PB-F", "every file of section 7's frozen table has its listed sha256",
                  "all identical", {"files": len(rows), "differ_from_prereg": stale,
                  "changed_by_this_node_since_base": touched,
                  "moved_by_next_phases_since_prereg": moved}, not stale,
                  rebaselined_pass=not touched, rows=rows,
                  reading="literal gate: the prereg's hashes; the re-baselined check compares "
                  f"the working tree with `{ctx.base_ref}` (next-phases now holds Spikes E, H, I)")  # fmt: skip  # noqa: E501


def extract_digest(binary: Path, rel: str) -> str:
    rc, out, err, _ = run(
        [str(binary), "extract"], stdin=json.dumps({"root": str(REPO / rel)}).encode()
    )
    return hashlib.sha256(out.encode()).hexdigest() if rc == 0 else f"error:{err[-100:]}"


def probe_pb_x(ctx: Ctx) -> dict[str, Any]:
    from lattice.gocheck import build_gocheck

    base_bin = ctx.scratch / "base_gocheck" / "gocheck"
    ok, err, _ = go_build(ctx.scratch / "base" / "lattice/gocheck/src", base_bin, flags="-mod=mod")
    if not ok:
        return not_run(
            "PB-X", "default gocheck extract byte-identical", f"base build failed: {err}"
        )
    new_bin = build_gocheck()
    rows = []
    for rel, pre in zip(PB_X_DIRS, PB_X_PREREG, strict=True):
        a, b = extract_digest(base_bin, rel), extract_digest(new_bin, rel)
        rows.append({"dir": rel, "base": a[:16], "now": b[:16], "prereg_prefix": pre,
                     "same_as_base": a == b, "same_as_prereg": a[:16] == pre})  # fmt: skip
    return result("PB-X", "default gocheck extract byte-identical on the 7 module dirs",
                  "identical to base", {"identical_to_base": sum(r["same_as_base"] for r in rows),
                  "of": len(rows)}, all(r["same_as_base"] for r in rows), rows=rows)  # fmt: skip


def both_trees(ctx: Ctx, cmd: list[str], *, cwd: str = ".", env_extra: dict[str, str] | None = None,
               stdin: bytes | None = None) -> tuple[tuple, tuple]:  # fmt: skip
    out = []
    for tree in (ctx.scratch / "base", REPO):
        env = ctx.env(PYTHONPATH=str(tree), **(env_extra or {}))
        rc, so, se, _ = run(cmd, cwd=tree / cwd, env=env, stdin=stdin)
        out.append((rc, so, se))
    return out[0], out[1]


def tree_files(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}  # fmt: skip


def probe_pb_g(ctx: Ctx) -> dict[str, Any]:
    """Go outputs in both trees: the workflows static run and the inventory of
    goextract_workflow, and layer A's findings and notes on the implementation fixture."""
    rows: dict[str, Any] = {}
    fx = "tests/gocheck/fixtures"
    for name, args_ in (("workflows --static", ["workflows", fx, "--package", "goextract_workflow",
                                                "--static"]),
                        ("inventory", ["inventory", fx, "--package", "goextract_workflow"])):  # fmt: skip  # noqa: E501
        outs = []
        for tree in (ctx.scratch / "base", REPO):
            d = ctx.scratch / "pbg" / name.split()[0] / ("base" if tree != REPO else "now")
            shutil.rmtree(d, ignore_errors=True)
            rc, so, se, _ = run([ctx.py, "-m", "lattice.importer", *args_, "--out", str(d)],
                                cwd=tree, env=ctx.env(PYTHONPATH=str(tree)), timeout=600)  # fmt: skip  # noqa: E501
            outs.append(
                (rc, tree_files(d) if d.exists() else {}, so.replace(str(d), "<out>"), se[-200:])
            )
        rows[name] = {"same": outs[0][:3] == outs[1][:3] and outs[0][0] == 0,
                      "rc": [outs[0][0], outs[1][0]], "files": sorted(outs[1][1]),
                      "stderr": outs[1][3]}  # fmt: skip
    impl = "tests/spec/fixtures/implementation"
    code = ("import json,sys;sys.path.insert(0,'%s');import importlib;"  # noqa: UP031
            "from lattice.gocheck import run_gocheck,GoPolicy,gated_note;"
            "m=importlib.import_module('fixture_spec');"
            "f=run_gocheck('%s/app',GoPolicy.from_spec(m.SPEC),strict=True);"
            "print(json.dumps([[x.code,x.file,x.line,x.message,x.key] for x in f]));"
            "print(gated_note())" % (impl, impl))  # fmt: skip  # noqa: UP031
    a, b = both_trees(ctx, [ctx.py, "-c", code])
    rows["layer A findings and notes (implementation/app + its spec)"] = {
        "same": a == b and a[0] == 0, "rc": [a[0], b[0]], "err": (a[2] or b[2])[-300:]}  # fmt: skip
    ok = all(v["same"] for v in rows.values())
    return result(
        "PB-G",
        "Go outputs byte-identical to base",
        "all identical",
        rows,
        ok,
        reading="test_process_runtime.py's reports are PB-T's (same pass/fail ids)",
    )


PY_TOY = "tests/importer/flask"  # a Python rules-first import of a repo fixture, see below


VOLATILE = [
    (re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(\.\d+)?"), "<timestamp>"),
    (re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}"), "<bcrypt>"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<jwt>"),
]


def python_tree(d: Path, repo: Path) -> dict[str, str]:
    """A Python rules import directory with its volatile fields replaced: the target path
    (``repo``), timestamps, bcrypt salts, the wall and phase timings; report.md is left out."""
    out: dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d).as_posix()
        if not p.is_file() or rel == "report.md":
            continue
        text = p.read_text()
        if rel == "report.json":
            r = json.loads(text)
            r["cost"]["wall_s"], r["repo"] = "<wall_s>", "<repo>"
            if r.get("rules"):
                r["rules"]["timing"] = "<timing>"
            text = json.dumps(r, indent=2)
        text = text.replace(str(repo), "<repo>")
        for rx, to in VOLATILE:
            text = rx.sub(to, text)
        out[rel] = hashlib.sha256(text.encode()).hexdigest()
    return out


def probe_pb_p(ctx: Ctx) -> dict[str, Any]:
    """A Python rules-first import (multi-vendor, the app test_rules.py names) in both trees
    (the base twice, as a control of the normaliser); report.json has no ``inferences``;
    pycheck's ``derive_policy`` is the same."""
    rows: dict[str, Any] = {}
    pkg, fac = "bench.import_repos.multivendor.app", "bench.import_repos.multivendor:make_app"
    runs = [("base1", ctx.scratch / "base"), ("base2", ctx.scratch / "base"), ("now", REPO)]
    outs = {}
    for name, tree in runs:
        d = ctx.scratch / "pbp" / name
        shutil.rmtree(d, ignore_errors=True)
        rc, so, se, _ = run([ctx.py, "-m", "lattice.importer", ".", "--package", pkg,
                             "--app-factory", fac, "--rules-only", "--out", str(d)],
                            cwd=tree, env=ctx.env(PYTHONPATH=str(tree)), timeout=1500)  # fmt: skip
        outs[name] = (rc, d, se[-300:], tree)
    if any(o[0] != 0 for o in outs.values()):
        return not_run(
            "PB-P",
            "Python rules-first import byte-identical",
            f"the Python import did not run: {[o[2] for o in outs.values()]}",
        )
    t = {n: python_tree(o[1], o[3]) for n, o in outs.items()}
    diff = sorted(
        k for k in set(t["base1"]) | set(t["now"]) if t["base1"].get(k) != t["now"].get(k)
    )
    ctl = sorted(
        k for k in set(t["base1"]) | set(t["base2"]) if t["base1"].get(k) != t["base2"].get(k)
    )
    rows["control: the base twice, after the normaliser, is identical"] = not ctl
    rows["control differing files"] = ctl
    rows["rules import (multivendor) identical to base after the normaliser"] = not diff
    rows["differing files"] = diff
    rep = load_report(outs["now"][1])
    rows["report.json has no inferences key"] = "inferences" not in (rep.get("rules") or {})
    rows["no go_policy.json for a Python import"] = not (outs["now"][1] / "go_policy.json").exists()
    code = ("import dataclasses;from pathlib import Path;from lattice.pycheck import derive_policy;"
            "p=derive_policy(Path('bench/import_repos/multivendor/app'),"
            "'bench.import_repos.multivendor.app');d=dataclasses.asdict(p);"
            "d['baseline']=sorted(d['baseline']);print(d)")  # fmt: skip
    a2, b2 = both_trees(ctx, [ctx.py, "-c", code])
    rows["pycheck derive_policy(multivendor) identical"] = a2 == b2 and a2[0] == 0
    ok = (not diff and not ctl and rows["report.json has no inferences key"]
          and rows["pycheck derive_policy(multivendor) identical"]
          and rows["no go_policy.json for a Python import"])  # fmt: skip
    return result("PB-P", "Python outputs byte-identical to base", "all identical", rows, ok,
                  reading="the pre-registration's `spec_policy on the pycheck fixtures` names a "
                  "function that does not exist at this commit; derive_policy is its nearest "
                  "(layer A's Python policy)")  # fmt: skip


def probe_pb_h(ctx: Ctx) -> dict[str, Any]:
    rows = {}
    for name, cmd in {
        "lattice --help": [
            ctx.py,
            "-c",
            "import sys;from lattice.cli import main;sys.argv=['lattice','--help'];sys.exit(main())",  # noqa: E501
        ],
        "python -m lattice.importer --help": [ctx.py, "-m", "lattice.importer", "--help"],
        "python -m lattice.spec check --help": [ctx.py, "-m", "lattice.spec", "check", "--help"],
    }.items():
        a, b = both_trees(ctx, cmd)
        rows[name] = {"same": a == b, "rc": [a[0], b[0]]}
    proj = ctx.scratch / "mcp_empty"
    proj.mkdir(exist_ok=True)
    msgs = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},  # noqa: E501
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]) + "\n"  # fmt: skip
    outs = []
    for tree in (ctx.scratch / "base", REPO):
        rc, so, se, _ = run([ctx.py, "-m", "lattice.mcp", "serve", "--project", str(proj)],
                            cwd=proj, env=ctx.env(PYTHONPATH=str(tree)), stdin=msgs.encode(), timeout=120)  # fmt: skip  # noqa: E501
        outs.append((rc, so))
    rows["MCP tools/list"] = {
        "same": outs[0] == outs[1] and "tools" in outs[0][1],
        "rc": [outs[0][0], outs[1][0]],
    }
    ok = all(v["same"] for v in rows.values())
    return result(
        "PB-H", "help texts and MCP tools/list identical to base", "all identical", rows, ok
    )


# ---- main -----------------------------------------------------------------------------------------  # noqa: E501


def setup(ctx: Ctx, arm: str) -> None:
    make_shims(ctx)
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    ctx.out.mkdir(parents=True, exist_ok=True)
    if ctx.base_dir is None:
        archive(ctx.base_ref, ctx.scratch / "base")
    else:  # the mutants runner shares one `git archive` of the base
        link = ctx.scratch / "base"
        if link.is_symlink():
            link.unlink()
        shutil.rmtree(link, ignore_errors=True)
        link.symlink_to(ctx.base_dir, target_is_directory=True)
    ctx.snap0 = snapshots()
    ok, err, wall = go_build(GOAPP, ctx.scratch / "goapp")
    ctx.timings["build_goapp_s"] = wall
    if ok:
        ctx.goapp_bin = ctx.scratch / "goapp"
    else:
        ctx.s1_error = err
    ok, err, wall = go_build(GOGINAPP, ctx.scratch / "goginapp")
    ctx.timings["build_goginapp_s"] = wall
    if ok:
        ctx.goginapp_bin = ctx.scratch / "goginapp"
    else:
        ctx.s2_error = err


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm", default="S", choices=["S", "M"])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-ref", default="HEAD")
    ap.add_argument("--base-dir", default="", help="an existing `git archive` of the base")
    ap.add_argument("--only", default="")
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args(argv)
    if args.arm == "M":
        print("arm M (Streak a0679e9 on MySQL 8.4): NOT RUN here (no Streak checkout, no Docker)")
        return 0
    ctx = Ctx(Path(args.scratch).resolve(), Path(args.out).resolve(), args.base_ref)
    if args.base_dir:
        ctx.base_dir = Path(args.base_dir).resolve()
    setup(ctx, args.arm)
    only = {x for x in args.only.split(",") if x}
    want = lambda pid: not only or pid in only  # noqa: E731
    results: list[dict[str, Any]] = []

    def add(pid: str, fn: Callable[[], dict[str, Any]], *, needs_s2: bool = False,
            needs_s1: bool = False) -> None:  # fmt: skip
        if not want(pid):
            return
        if needs_s2 and ctx.s2_error:
            results.append(not_run(pid, "S2", f"S2 does not build: {ctx.s2_error[:400]}"))
        elif needs_s1 and ctx.s1_error:
            results.append(not_run(pid, "S1", f"S1 does not build: {ctx.s1_error[:400]}"))
        else:
            try:
                results.append(fn())
            except Exception as err:  # noqa: BLE001 -- a probe that cannot run is reported
                import traceback

                results.append(
                    result(
                        pid,
                        "(the probe raised)",
                        None,
                        f"{type(err).__name__}: {err}",
                        False,
                        traceback=traceback.format_exc()[-1500:],
                    )
                )
        print(f"{pid}: pass={results[-1]['pass']}", flush=True)

    imp = ctx.scratch / "imp" / "r0"
    ran_import: dict[str, Any] = {}
    s2_wanted = any(want(p) for p in ("PI1", "PI2", "PI3", "PI4", "PI5", "PI6", "PA3", "PR3", "PX"))
    if s2_wanted and not ctx.s2_error:
        t = import_s2(ctx, ctx.scratch / "imp" / "lazy", lazy_build=True)
        ctx.timings["s2_import_lazy_build_s"] = t["wall_s"]
        ran_import = import_s2(ctx, imp)
        if ran_import["rc"] != 0:
            print("the S2 import failed:", ran_import["stderr"], file=sys.stderr)
    imp_ok = bool(ran_import) and ran_import["rc"] == 0
    s2 = {"needs_s2": True}
    for pid, fn in (
        ("PI1", probe_pi1),
        ("PI2", probe_pi2),
        ("PI3", probe_pi3),
        ("PI4", probe_pi4),
        ("PI5", probe_pi5),
        ("PI6", probe_pi6),
        ("PR3", probe_pr3),
        ("PA3", probe_pa3),
    ):
        if imp_ok:
            add(pid, lambda fn=fn: fn(ctx, imp), **s2)
        else:
            add(pid, lambda pid=pid: not_run(pid, "S2 import", "the S2 import did not run"), **s2)
    add("PI7", lambda: probe_pi7(ctx, args.runs), **s2)
    add("PA1", lambda: probe_pa1(ctx))
    add("PA2", lambda: probe_pa2(ctx))
    add("PA4", lambda: probe_pa4(ctx))
    add("PG805", lambda: probe_pg805(ctx))
    add("PR1", lambda: probe_pr1(ctx), needs_s1=True)
    add("PR2", lambda: probe_pr2(ctx), needs_s1=True)
    add("PR4", lambda: probe_pr4(ctx, args.runs), needs_s1=True)
    add("PB1", lambda: probe_pb1(ctx), needs_s1=True)
    add("PB2", lambda: probe_pb2(ctx), needs_s1=True)
    add("PB3", lambda: probe_pb3(ctx, args.runs), needs_s1=True)
    add("PB-F", lambda: probe_pb_f(ctx))
    add("PB-X", lambda: probe_pb_x(ctx))
    add("PB-G", lambda: probe_pb_g(ctx))
    add("PB-P", lambda: probe_pb_p(ctx))
    add("PB-H", lambda: probe_pb_h(ctx))
    add("PW", lambda: probe_pw(ctx))
    add("PX", lambda: probe_px(ctx))
    doc = {"arm": "S", "base_ref": args.base_ref, "timings": ctx.timings,
           "s1_error": ctx.s1_error, "s2_error": ctx.s2_error, "results": results}  # fmt: skip
    (ctx.out / "probes.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    if imp_ok:
        for name in (
            "report.json",
            "candidate_spec.py",
            "profile.py",
            "proposals.json",
            "go_policy.json",
        ):
            shutil.copyfile(imp / name, ctx.out / f"s2_import_{name}")
    passed = [r["id"] for r in results if r["pass"] is True]
    failed = [r["id"] for r in results if r["pass"] is False]
    nr = [r["id"] for r in results if r["pass"] is None]
    print(
        f"passed {len(passed)}: {passed}\nfailed {len(failed)}: {failed}\nnot run {len(nr)}: {nr}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
