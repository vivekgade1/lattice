"""Probe runner for the layer 3 route exclusion (a provider's ``exclude`` list in the manifest).

Implements the probes of ``docs/results/2026-10-13-contracts-exclude-prereg.md`` (section 3): X0,
X1, X2, X2b, X3, X4, X5, X6, X7, X8, X9 and S. Predictions and pass rules are copied from that
document; a probe that does not match is reported as a finding with its raw output, never adapted.

    python dogfood/contracts/exclude_probes.py --runs 10 --scratch DIR \
        --out docs/results/data/2026-10-13-contracts-exclude [--only X1,X2,...] [--skip-regression]

Every probe works on ``git clone --no-hardlinks`` scratch copies of job-scheduler at ef17cf8 and of
Streak at a0db033 under ``DIR``; the real repositories are only read (S proves it). No LLM and no
API key are used. Every deterministic probe runs ``--runs`` times (default 10) and all canonical
outputs (``elapsed_s`` excluded) must be identical. Raw per-run outputs go to ``OUT/<ID>.json`` and
``OUT/summary.json``. The helpers (Ctx, Result, canonical outputs, MCP and API access) are those of
``probes.py`` and ``streak_probes.py``, imported from the files next to this one. X0 (d) runs both
of them as subprocesses (``--runs`` each); ``--skip-regression`` skips that part (development).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


streak = _load("lattice_contracts_streak_probes", HERE / "streak_probes.py")
base = streak.base  # probes.py, as streak_probes loaded it
Result = base.Result
canon_call = base.canon_call
canon_json = base.canon_json
sha = base.sha
strip_elapsed = base.strip_elapsed

OLD_REF = "7358be2"  # next-phases before this change: the reference of X8
HASH_J, HASH_S = "9bcae0e24da9", "d44b738522ea"  # the golden spec_hash prefixes
FILE_J, FILE_S = "29983cf89b5d", "36f89f671c58"  # the golden lock files' sha256 prefixes
GOLDEN_J = base.GOLDEN
GOLDEN_S = streak.GOLDEN
LOCK = base.LOCK
MANIFEST = base.MANIFEST
IMPORTS = "lattice.imports.toml"
EX = "the provider excludes it from its contract (exclude in the workspace manifest)"
WORKER = ["POST /worker/run-once", "POST /worker/schedule-tick"]
HARNESS = ["GET /health", "PUT /test/clock"]
CANCEL_DECL = "POST /applications/{app}/runs/{run}/cancel"
CANCEL = base.CANCEL
LOGIN_J = base.LOGIN
LOGIN_S = streak.LOGIN
HEALTH, CLOCK = streak.HEALTH, streak.CLOCK
STREAK_GAPS = [
    ("request_field_unobserved", "GET /v1/habits", "cursor"),
    ("response_field_unobserved", "POST /v1/auth/login", "access_token"),
]
SCHEDULER_GAPS = [("response_field_unobserved", LOGIN_J, "access_token")]
CLOCK_IMPORT = (
    '\n[[import]]\nprovider = "streak"\nendpoint = "PUT /test/clock"\nstatus = [200]\n'
    'auth = "public"\nrequest_fields = ["now"]\n'
)
COMMITTED = REPO / "docs" / "results" / "data"

POST_HOC_SCRIPT_CORRECTIONS: list[str] = []
POST_HOC_PROCEDURE: list[str] = [
    "Contexts: the job-scheduler Ctx and the Streak SCtx run in separate scratch roots (`j`, "
    "`s`); the Streak manifest line comes from `SCtx.workspace(exclude=...)` (the only edit of "
    "streak_probes.py besides --allow-code-diff); the job-scheduler manifest is written by the "
    "subclass here, probes.py is untouched.",
    "One clean clone per provider is shared by the probes that do not touch the provider (X0-X8); "
    "X9 uses its own clones (a live replay runs the provider's app).",
    "The golden lock of a probe's pinned-before state is the repository's golden file, written "
    "into the consumer directory at the start of each run (a check never writes a lock).",
    "X0 (c) compares the check record (exit code, stderr, JSON document) with `runs[0].check` of "
    "the committed B0 data of experiments 1 and 2.",
    "X0 (d) runs probes.py and streak_probes.py --allow-code-diff as subprocesses with the same "
    "--runs, and reads their last line `probes: P/T pass`; their summary.json is copied under "
    "X0-regression/.",
    "X5 values are written with json.dumps, which is TOML-valid for every row (a string, an "
    "integer, a list of strings or integers).",
    "X7 starts the real Studio server (`lattice.studio SOLO --stub-llm --workspace M`) and the "
    "stdio MCP server per manifest, and compares both with `contracts check --static-only "
    "--json` without a consumer argument (the API and the tool check every link).",
    "X8's reference code is `git archive 7358be2 lattice` extracted under the scratch root; "
    "`impact` names the provider project (scheduler, streak) or shared-auth (the good manifest).",
    "X9 (b) pins with --force after an unforced pin is refused; the check is then against that "
    "lock (the findings are the same as against a lock pinned under (a)'s list).",
]


# ---- contexts ------------------------------------------------------------------------------


class JCtx(base.Ctx):
    """The job-scheduler context (provider `scheduler`, consumer `ci-bot`) with `exclude`."""

    consumer = "ci-bot"
    provider = "scheduler"
    spec = "spec.tickets:t5"
    golden = GOLDEN_J.read_bytes()

    def workspace(
        self,
        name: str,
        clone: Path,
        *,
        contract: str | None = "static",
        live_url: str | None = None,
        lock: bytes | None | str = "clean",
        variant: str = "m",
        exclude: Any = None,
    ) -> Path:
        d = self.work / name
        bot = d / "ci-bot"
        if not bot.exists():
            shutil.copytree(
                base.CI_BOT, bot, ignore=shutil.ignore_patterns("__pycache__", LOCK)
            )  # fmt: skip
        data = self.clean_lock() if lock == "clean" else lock
        if isinstance(data, bytes):
            (bot / LOCK).write_bytes(data)
        md = d / variant
        md.mkdir(exist_ok=True)
        link = 'from = "ci-bot"\nto = "scheduler"\nkind = "uses"\n'
        if contract is not None:
            link += f"contract = {json.dumps(contract)}\n"
        if live_url is not None:
            link += f"live_url = {json.dumps(live_url)}\n"
        # the route exclusion: one line, only when asked for
        excl = f"exclude = {json.dumps(exclude)}\n" if exclude is not None else ""
        (md / MANIFEST).write_text(
            '[workspace]\nname = "probe"\n\n'
            f'[[project]]\nname = "scheduler"\npath = "{clone}"\nrole = "provider"\n'
            'spec = "spec.tickets:t5"\napp = "job_scheduler:make_app"\n'
            'db = "spec.seed:build_seed_db"\nlogin = "spec.seed:login"\nsrc = "src"\n'
            'env = { JOB_SCHEDULER_REPO_ROOT = "$PROJECT" }\n'
            f"{excl}\n"
            f'[[project]]\nname = "ci-bot"\npath = "{bot}"\nrole = "consumer"\n\n'
            f"[[link]]\n{link}"
        )
        return md / MANIFEST

    def lock_path(self, manifest: Path) -> Path:
        return manifest.parent.parent / self.consumer / LOCK

    def pin(self, manifest: Path, *args: str) -> Any:
        return self.contracts(manifest, "pin", self.consumer, "--json", *args)

    def export(self, manifest: Path) -> Any:
        return self.contracts(manifest, "export", self.provider)


class SX(streak.SCtx):
    """The Streak context (provider `streak`, consumer `streak-widget`)."""

    consumer = streak.CONSUMER
    provider = streak.PROVIDER_NAME
    spec = "spec:SPEC"
    golden = GOLDEN_S.read_bytes()


def shared(ctx: Any) -> Path:
    """One clean clone per provider, for the probes that do not modify it."""
    if not hasattr(ctx, "_shared_clone"):
        ctx._shared_clone = ctx.clone("shared")
    return ctx._shared_clone  # type: ignore[no-any-return]


# ---- small readers over canonical records ---------------------------------------------------


def lk0(rec: dict[str, Any]) -> dict[str, Any]:
    return rec["doc"]["links"][0]  # type: ignore[no-any-return]


def brk(lk: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    return [
        (f["kind"], f["endpoint"], f["item"], f["detail"])
        for f in lk["findings"]
        if f["class"] == "BROKEN"
    ]


def chg(lk: dict[str, Any]) -> list[tuple[str, str, str, Any]]:
    return [
        (f["kind"], f["endpoint"], f["item"], f.get("relied"))
        for f in lk["findings"]
        if f["class"] == "CHANGED"
    ]


def gaps(lk: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(g["kind"], g["endpoint"], g["item"]) for g in lk["gaps"]]


def counts(exp_doc: dict[str, Any]) -> dict[str, int]:
    eps = exp_doc["export"]["endpoints"]
    out = {
        a: sum(1 for v in eps.values() if v["auth"] == a) for a in ("public", "token", "unknown")
    }
    out["endpoints"] = len(eps)
    return out


def jdoc(c: Any) -> Any:
    return json.loads(c.out)


def rec_of(c: Any) -> dict[str, Any]:
    return {"rc": c.rc, "out": c.out, "err": c.err}  # type: ignore[no-any-return]


def committed_b0(experiment: str) -> dict[str, Any]:
    j = json.loads((COMMITTED / experiment / "B0.json").read_text())
    return j["runs"][0]["check"]  # type: ignore[no-any-return]


def mk(pid: str, pred: str, obs: str, runs: list[Any], ident: bool, checks: dict[str, Any],
       extra: dict[str, Any] | None = None) -> Result:  # fmt: skip
    ok = ident and all(checks.values())
    return Result(pid, pred, obs, ok, runs, ident, checks, extra or {})


def yn(v: Any) -> str:
    return "yes" if v else "NO"


# ---- X0: no exclude key -> nothing moves ------------------------------------------------------

REGRESSION: dict[str, Any] = {}


def run_regression(jc: JCtx, runs: int, scratch: Path, out: Path) -> dict[str, Any]:
    """X0 (d): the two earlier runners, unchanged, as subprocesses."""
    res: dict[str, Any] = {}
    jobs = {
        "probes": [str(HERE / "probes.py"), "--runs", str(runs)],
        "streak_probes": [str(HERE / "streak_probes.py"), "--runs", str(runs), "--allow-code-diff"],
    }
    dst = out / "X0-regression"
    dst.mkdir(parents=True, exist_ok=True)
    for name, argv in jobs.items():
        work, data = scratch / f"reg-{name}", scratch / f"reg-{name}-data"
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, *argv, "--scratch", str(work), "--out", str(data)],
            cwd=REPO,
            env=jc.env,
            capture_output=True,
            text=True,
            check=False,
        )
        tail = [ln for ln in r.stdout.splitlines() if ln.startswith("probes: ")]
        m = re.fullmatch(r"probes: (\d+)/(\d+) pass", tail[-1]) if tail else None
        res[name] = {
            "rc": r.returncode,
            "passed": int(m.group(1)) if m else None,
            "total": int(m.group(2)) if m else None,
            "last_line": tail[-1] if tail else None,
            "wall_s": round(time.time() - t0, 1),
            "stderr_tail": r.stderr.strip().splitlines()[-3:],
        }
        if (data / "summary.json").exists():
            shutil.copy(data / "summary.json", dst / f"{name}-summary.json")
        (dst / f"{name}-stdout.txt").write_text(r.stdout)
    return res


def x0(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "no key / exclude = []: both exports byte-identical to the goldens (hash 9bcae0e24da9, "
        "d44b738522ea; lock files 29983cf89b5d, 36f89f671c58); check equals the committed B0 "
        "docs; probes.py 35/35 and streak_probes.py 32/32"
    )
    provs = [
        ("job-scheduler", jc, HASH_J, FILE_J, "2026-10-13-contracts"),
        ("streak", sc, HASH_S, FILE_S, "2026-10-13-contracts-streak"),
    ]
    setups = []
    for tag, ctx, _h, _f, _e in provs:
        clone = shared(ctx)
        ma = ctx.workspace("X0", clone, lock=None, variant="nokey")
        mb = ctx.workspace("X0", clone, lock=None, variant="empty", exclude=[])
        setups.append((tag, ctx, ma, mb))

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for tag, ctx, ma, mb in setups:
            lock = ctx.lock_path(ma)
            lock.unlink(missing_ok=True)
            ea = ctx.export(ma)
            pa = ctx.pin(ma)
            lock_a = lock.read_bytes()
            ca = canon_call(ctx.check(ma))
            lock.unlink()
            eb = ctx.export(mb)
            pb = ctx.pin(mb)
            lock_b = lock.read_bytes()
            cb = canon_call(ctx.check(mb))
            doc = jdoc(ea)
            rec[tag] = {
                "export_rc": [ea.rc, eb.rc],
                "pin_rc": [pa.rc, pb.rc],
                "top_keys": sorted(doc),
                "export_keys": sorted(doc["export"]),
                "export_sha12": sha(canon_json(doc["export"]).encode())[:12],
                "spec_hash12": doc["version"]["spec_hash"][:12],
                "export_a_eq_b": ea.out == eb.out,
                "lock_a_sha12": sha(lock_a)[:12],
                "lock_b_sha12": sha(lock_b)[:12],
                "lock_a_eq_golden": lock_a == ctx.golden,
                "lock_b_eq_golden": lock_b == ctx.golden,
                "check_a": ca,
                "check_b": cb,
            }
        return rec

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    for tag, _ctx, want_hash, want_file, exp in provs:
        r = r0[tag]
        committed = committed_b0(exp)
        short = tag.split("-")[0]
        checks[f"{short}_a_export_hash"] = r["export_sha12"] == want_hash == r["spec_hash12"]
        checks[f"{short}_a_lock_bytes_golden"] = (
            r["lock_a_eq_golden"] and r["lock_a_sha12"] == want_file
        )
        checks[f"{short}_b_export_bytes_equal_a"] = r["export_a_eq_b"]
        checks[f"{short}_b_lock_bytes_golden"] = r["lock_b_eq_golden"]
        checks[f"{short}_top_keys"] = r["top_keys"] == ["export", "spec_ref", "version"]
        checks[f"{short}_export_keys"] = r["export_keys"] == [
            "basis", "endpoints", "error_body", "format"]  # fmt: skip
        checks[f"{short}_exit_codes"] = r["export_rc"] == [0, 0] and r["pin_rc"] == [0, 0]
        checks[f"{short}_c_check_equals_committed_B0"] = r["check_a"] == committed
        checks[f"{short}_c_empty_list_check_equals_committed_B0"] = r["check_b"] == committed
        parts.append(
            f"{short}: hash {r['export_sha12']}, lock {r['lock_a_sha12']} "
            f"(golden {yn(r['lock_a_eq_golden'])}), b==a {yn(r['export_a_eq_b'])}, "
            f"check==B0 {yn(r['check_a'] == committed)}"
        )
    extra: dict[str, Any] = {"byte_comparisons": 8, "regression": None}
    if ns.skip_regression:
        parts.append("(d) SKIPPED (--skip-regression)")
        checks["d_regression_run"] = False
    else:
        reg = run_regression(jc, ns.runs, ns.scratch.resolve(), ns.out)
        extra["regression"] = reg
        p, s = reg["probes"], reg["streak_probes"]
        checks["d_probes_35_of_35"] = (p["passed"], p["total"], p["rc"]) == (35, 35, 0)
        checks["d_streak_probes_32_of_32"] = (s["passed"], s["total"], s["rc"]) == (32, 32, 0)
        parts.append(f"(d) probes.py {p['passed']}/{p['total']}, streak_probes.py "
                     f"{s['passed']}/{s['total']}")  # fmt: skip
    return mk("X0", pred, "; ".join(parts), runs, ident, checks, extra)


# ---- X1, X2: the exclusion on each provider ---------------------------------------------------


def x_export_probe(pid: str, ctx: Any, exclude: list[str], want: dict[str, Any]) -> Result:
    """X1 and X2: export, check against the golden lock, re-pin, check again."""
    clone = shared(ctx)
    m = ctx.workspace(pid, clone, lock=None, exclude=exclude)
    lock = ctx.lock_path(m)
    flags = [a for k in exclude for a in ("--exclude", k)]

    def one(_: int) -> dict[str, Any]:
        lock.write_bytes(ctx.golden)  # the golden lock: pinned with no exclusion
        e = ctx.export(m)
        direct = ctx.run([sys.executable, "-m", "lattice.contracts", "export", "--project",
                          str(clone), "--spec", ctx.spec, *flags])  # fmt: skip
        c1 = canon_call(ctx.check(m))
        p = canon_call(ctx.pin(m))
        c2 = canon_call(ctx.check(m))
        doc = jdoc(e)
        return {
            "export_rc": e.rc,
            "export_doc": doc,
            "direct_rc": direct.rc,
            "export_eq_direct": e.out == direct.out,
            "spec_hash_recomputed": sha(canon_json(doc["export"]).encode()),
            "check_golden": c1,
            "pin": p,
            "check_after_pin": c2,
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    doc = r0["export_doc"]
    c = counts(doc)
    eps = doc["export"]["endpoints"]
    l1, l2 = lk0(r0["check_golden"]), lk0(r0["check_after_pin"])
    keys = sorted(exclude)
    checks: dict[str, Any] = {
        "export_exit_0": r0["export_rc"] == 0 and r0["direct_rc"] == 0,
        f"endpoints_{want['endpoints']}": c["endpoints"] == want["endpoints"],
        f"public_{want['public']}": c["public"] == want["public"],
        f"token_{want['token']}": c["token"] == want["token"],
        f"unknown_{want['unknown']}": c["unknown"] == want["unknown"],
        "excluded_sorted_keys": doc["export"].get("excluded") == keys,
        "excluded_not_in_endpoints": not any(k in eps for k in keys),
        "exclude_unmatched_empty": doc.get("exclude_unmatched") == [],
        "spec_hash_moved": not doc["version"]["spec_hash"].startswith(want["golden_hash"]),
        "spec_hash_is_sha256_of_export": doc["version"]["spec_hash"] == r0["spec_hash_recomputed"],
        "export_name_eq_project_flags": r0["export_eq_direct"],
        "golden_exit_1": r0["check_golden"]["rc"] == 1,
        "golden_0_broken": brk(l1) == [],
        "golden_2_changed_exclusion_added_unrelied": sorted(chg(l1))
        == sorted(("exclusion_added", k, "", False) for k in keys),
        "golden_spec_hash_changed": l1["spec_hash_changed"] is True,
        "golden_version_unchanged": l1["version_changed"] is False,
        "golden_gaps": gaps(l1) == want["gaps"],
        "pin_exit_0": r0["pin"]["rc"] == 0,
        "pin_endpoints": r0["pin"]["doc"]["links"][0]["endpoints"] == want["endpoints"],
        "after_pin_exit_0": r0["check_after_pin"]["rc"] == 0,
        "after_pin_0_findings": l2["findings"] == [],
        "after_pin_gaps": gaps(l2) == want["gaps"],
    }
    if "public_keys" in want:
        pub = sorted(k for k, v in eps.items() if v["auth"] == "public")
        checks["public_endpoints"] = pub == sorted(want["public_keys"])
    obs = (
        f"{c['endpoints']} endpoints, public {c['public']} token {c['token']} unknown "
        f"{c['unknown']}; excluded {doc['export'].get('excluded')}; unmatched "
        f"{doc.get('exclude_unmatched')}; spec_hash {doc['version']['spec_hash'][:12]}; golden "
        f"lock: exit {r0['check_golden']['rc']}, {len(brk(l1))} BROKEN, "
        f"{len(chg(l1))} CHANGED {sorted({k for k, *_ in chg(l1)})}, hash_changed "
        f"{l1['spec_hash_changed']}, version_changed {l1['version_changed']}, "
        f"gaps {len(l1['gaps'])}"
        f"; pin exit {r0['pin']['rc']} ({r0['pin']['doc']['links'][0]['endpoints']} endpoints); "
        f"re-check exit {r0['check_after_pin']['rc']}, {len(l2['findings'])} findings, "
        f"{len(l2['gaps'])} gaps"
    )
    pred = want["pred"]
    return mk(pid, pred, obs, runs, ident, checks, {"export_summary": c})


def x1(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    want = {
        "pred": (
            "18 endpoints (public 1, token 4, unknown 13); excluded = the 2 keys; unmatched []; "
            "spec_hash != 9bcae0e24da9 and = sha256 of the export; export NAME == --project "
            "--exclude; vs golden lock exit 1, 0 BROKEN, 2 CHANGED exclusion_added unrelied, "
            "hash changed, version unchanged, 1 gap; pin 0 (18); re-check exit 0, 0 findings, "
            "1 gap"
        ),
        "endpoints": 18, "public": 1, "token": 4, "unknown": 13,
        "golden_hash": HASH_J, "gaps": SCHEDULER_GAPS,
    }  # fmt: skip
    return x_export_probe("X1", jc, WORKER, want)


def x2(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    want = {
        "pred": (
            "24 endpoints (public 3 login/refresh/register, token 21, unknown 0); vs golden lock "
            "exit 1, 2 CHANGED exclusion_added unrelied, 0 BROKEN; after re-pin exit 0, 0 "
            "findings, the 2 B0 gaps in order"
        ),
        "endpoints": 24, "public": 3, "token": 21, "unknown": 0,
        "golden_hash": HASH_S, "gaps": STREAK_GAPS,
        "public_keys": ["POST /v1/auth/login", "POST /v1/auth/refresh", "POST /v1/auth/register"],
    }  # fmt: skip
    return x_export_probe("X2", sc, HARNESS, want)


def x2b(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "['get /health/', ' PUT  /test/clock '] exports byte-identical to X2's; "
        "['GET /v1/habits/{id}/progress'] and ['GET /v1/habits/7/progress'] byte-identical "
        "(25 endpoints, excluded ['GET /v1/habits/{}/progress'])"
    )
    clone = shared(sc)
    lists = {
        "x2": HARNESS,
        "spelt": ["get /health/", " PUT  /test/clock "],
        "named": ["GET /v1/habits/{id}/progress"],
        "numeric": ["GET /v1/habits/7/progress"],
    }
    ms = {k: sc.workspace("X2b", clone, lock=None, variant=k, exclude=v) for k, v in lists.items()}

    def one(_: int) -> dict[str, Any]:
        out = {k: sc.export(m) for k, m in ms.items()}
        return {
            "rc": {k: c.rc for k, c in out.items()},
            "same_spelt_x2": out["spelt"].out == out["x2"].out,
            "same_named_numeric": out["named"].out == out["numeric"].out,
            "named_doc": jdoc(out["named"]),
            "spelt_excluded": jdoc(out["spelt"])["export"].get("excluded"),
        }

    runs, ident = sc.repeat(one)
    r0 = runs[0]
    named = r0["named_doc"]
    checks = {
        "exit_0": all(v == 0 for v in r0["rc"].values()),
        "spelt_equals_x2_bytes": r0["same_spelt_x2"],
        "named_equals_numeric_bytes": r0["same_named_numeric"],
        "named_25_endpoints": len(named["export"]["endpoints"]) == 25,
        "named_excluded": named["export"].get("excluded") == ["GET /v1/habits/{}/progress"],
        "spelt_excluded_normalised": r0["spelt_excluded"] == sorted(HARNESS),
    }
    obs = (
        f"spelt==X2 {yn(r0['same_spelt_x2'])}; named==numeric {yn(r0['same_named_numeric'])}; "
        f"{len(named['export']['endpoints'])} endpoints, excluded {named['export'].get('excluded')}"
    )
    return mk("X2b", pred, obs, runs, ident, checks)


# ---- X3: a consumer that imports an excluded endpoint -----------------------------------------


def x3(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "no key: exit 0, 0 findings, 2 gaps; pin = golden bytes; pinned BEFORE + list: exit 1, "
        "BROKEN endpoint_missing @ PUT /test/clock (EX), 1 CHANGED exclusion_added @ GET /health "
        "unrelied, 2 gaps; pin REFUSED, lock unchanged; --force then check: still BROKEN, 2 "
        "gaps; pinned AFTER: pin REFUSED, no lock; --force writes same bytes; check the same"
    )
    clone = shared(sc)
    m_no = sc.workspace("X3", clone, lock=None, variant="nokey")
    imports = m_no.parent.parent / streak.CONSUMER / IMPORTS
    imports.write_text(imports.read_text() + CLOCK_IMPORT)
    m_ex = sc.workspace("X3", clone, lock=None, variant="excl", exclude=HARNESS)
    lock = sc.lock_path(m_no)

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        lock.write_bytes(sc.golden)
        rec["r1_check_nokey"] = canon_call(sc.check(m_no))
        lock.unlink()
        rec["r2_pin_nokey"] = canon_call(sc.pin(m_no))
        rec["r2_lock_eq_golden"] = lock.read_bytes() == sc.golden
        lock.write_bytes(sc.golden)
        rec["r3_check_excl"] = canon_call(sc.check(m_ex))
        rec["r4_pin"] = canon_call(sc.pin(m_ex))
        rec["r4_lock_unchanged"] = lock.read_bytes() == sc.golden
        rec["r5_pin_force"] = canon_call(sc.pin(m_ex, "--force"))
        forced = lock.read_bytes()
        rec["r5_lock_differs_from_golden"] = forced != sc.golden
        rec["r5_check"] = canon_call(sc.check(m_ex))
        lock.unlink()
        rec["r6_pin"] = canon_call(sc.pin(m_ex))
        rec["r6_lock_exists_after_refusal"] = lock.exists()
        rec["r6_pin_force"] = canon_call(sc.pin(m_ex, "--force"))
        rec["r6_lock_eq_forced"] = lock.exists() and lock.read_bytes() == forced
        rec["r6_check"] = canon_call(sc.check(m_ex))
        return rec

    runs, ident = sc.repeat(one)
    r = runs[0]
    want_broken = [("endpoint_missing", CLOCK, "", EX)]
    l1, l3 = lk0(r["r1_check_nokey"]), lk0(r["r3_check_excl"])
    l4, l5, l6 = lk0(r["r4_pin"]), lk0(r["r5_check"]), lk0(r["r6_check"])
    checks = {
        "row1_nokey_golden_exit0_0findings_2gaps": r["r1_check_nokey"]["rc"] == 0
        and l1["findings"] == []
        and len(l1["gaps"]) == 2,  # fmt: skip
        "row2_pin_nokey_lock_eq_golden": r["r2_pin_nokey"]["rc"] == 0 and r["r2_lock_eq_golden"],
        "row3_pinned_before": r["r3_check_excl"]["rc"] == 1
        and brk(l3) == want_broken
        and chg(l3) == [("exclusion_added", HEALTH, "", False)]
        and gaps(l3) == STREAK_GAPS
        and l3["spec_hash_changed"] is True,  # fmt: skip
        "row4_pin_refused_lock_unchanged": r["r4_pin"]["rc"] == 1
        and l4["status"] == "refused"
        and r["r4_lock_unchanged"],  # fmt: skip
        "row5_force_then_still_broken": r["r5_pin_force"]["rc"] == 0
        and r["r5_lock_differs_from_golden"]
        and r["r5_check"]["rc"] == 1
        and brk(l5) == want_broken
        and chg(l5) == []
        and gaps(l5) == STREAK_GAPS,  # fmt: skip
        "row6_pinned_after": r["r6_pin"]["rc"] == 1
        and not r["r6_lock_exists_after_refusal"]
        and r["r6_pin_force"]["rc"] == 0
        and r["r6_lock_eq_forced"]
        and r["r6_check"]["rc"] == 1
        and brk(l6) == want_broken
        and len(l6["gaps"]) == 2,  # fmt: skip
    }
    obs = (
        f"1: exit {r['r1_check_nokey']['rc']}, {len(l1['findings'])} findings, {len(l1['gaps'])} "
        f"gaps; 2: pin {r['r2_pin_nokey']['rc']}, lock==golden {yn(r['r2_lock_eq_golden'])}; "
        f"3: exit {r['r3_check_excl']['rc']}, BROKEN {[(k, e) for k, e, _, _ in brk(l3)]}, "
        f"CHANGED {[(k, e, rl) for k, e, _, rl in chg(l3)]}, gaps {len(l3['gaps'])}; "
        f"4: pin exit {r['r4_pin']['rc']} {l4['status']}, lock unchanged "
        f"{yn(r['r4_lock_unchanged'])}; 5: force exit {r['r5_pin_force']['rc']}, check exit "
        f"{r['r5_check']['rc']}, BROKEN {len(brk(l5))}, CHANGED {len(chg(l5))}, gaps "
        f"{len(l5['gaps'])}; 6: pin exit {r['r6_pin']['rc']}, "
        f"lock {r['r6_lock_exists_after_refusal']}, force exit {r['r6_pin_force']['rc']}, "
        f"lock==forced {yn(r['r6_lock_eq_forced'])}, check "
        f"exit {r['r6_check']['rc']}, BROKEN {len(brk(l6))}, gaps {len(l6['gaps'])}"
    )
    return mk("X3", pred, obs, runs, ident, checks)


# ---- X4: changing the list on a pinned consumer -----------------------------------------------


def x4(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "pinned with ['GET /health']; version unchanged, git_ref a0db033 in all rows. a: add "
        "PUT /test/clock -> exit 1, 1 CHANGED exclusion_added unrelied (no endpoint_removed), "
        "hash changed; b: [] -> exit 1, 1 CHANGED exclusion_removed @ GET /health (no "
        "endpoint_added); c: ['get /health/'] -> exit 0, hash unchanged; d: add GET /v1/nope -> "
        "exit 1, 1 CHANGED exclusion_added @ GET /v1/nope, 1 warning exclude_unmatched"
    )
    clone = shared(sc)
    rows = {
        "pin": ["GET /health"],
        "a": ["GET /health", "PUT /test/clock"],
        "b": [],
        "c": ["get /health/"],
        "d": ["GET /health", "GET /v1/nope"],
    }
    ms = {k: sc.workspace("X4", clone, lock=None, variant=k, exclude=v) for k, v in rows.items()}
    lock = sc.lock_path(ms["pin"])

    def one(_: int) -> dict[str, Any]:
        lock.unlink(missing_ok=True)
        rec: dict[str, Any] = {"pin": canon_call(sc.pin(ms["pin"]))}
        for k in "abcd":
            rec[k] = canon_call(sc.check(ms[k]))
        return rec

    runs, ident = sc.repeat(one)
    r = runs[0]
    L = {k: lk0(r[k]) for k in "abcd"}

    def versions_ok(lk: dict[str, Any]) -> bool:
        return (
            lk["version_changed"] is False
            and lk["pinned"]["git_ref"].startswith(streak.REF)
            and lk["current"]["git_ref"].startswith(streak.REF)
        )

    def warn_codes(k: str) -> list[str]:
        return [p["code"] for p in r[k]["doc"]["problems"]]

    checks = {
        "pin_exit_0": r["pin"]["rc"] == 0,
        "a": r["a"]["rc"] == 1
        and chg(L["a"]) == [("exclusion_added", CLOCK, "", False)]
        and brk(L["a"]) == []
        and L["a"]["spec_hash_changed"] is True
        and versions_ok(L["a"]),  # fmt: skip
        "b": r["b"]["rc"] == 1
        and chg(L["b"]) == [("exclusion_removed", HEALTH, "", False)]
        and brk(L["b"]) == []
        and versions_ok(L["b"]),  # fmt: skip
        "c": r["c"]["rc"] == 0
        and L["c"]["findings"] == []
        and L["c"]["spec_hash_changed"] is False
        and versions_ok(L["c"]),  # fmt: skip
        "d": r["d"]["rc"] == 1
        and chg(L["d"]) == [("exclusion_added", "GET /v1/nope", "", False)]
        and warn_codes("d") == ["exclude_unmatched"]
        and versions_ok(L["d"]),  # fmt: skip
        "no_warning_in_a_b_c": warn_codes("a") == warn_codes("b") == warn_codes("c") == [],
    }
    obs = "; ".join(
        f"{k}: exit {r[k]['rc']}, {[(x, e) for x, e, _, _ in chg(L[k])]}, hash_changed "
        f"{L[k]['spec_hash_changed']}, warnings {warn_codes(k)}"
        for k in "abcd"
    )
    return mk("X4", pred, obs, runs, ident, checks)


# ---- X5: validation ---------------------------------------------------------------------------

X5_ROWS: list[tuple[Any, list[str]]] = [
    ("GET /health", ["must be a list"]),
    (42, ["must be a list"]),
    ([42], ["entry #1", "must be a string"]),
    (["GET"], ["entry #1", "must be 'METHOD /path'"]),
    (["GET /health", "get /health/"], ["names GET /health twice"]),
    (["GET /v1/habits?limit=1"], ["a query string is not part of an endpoint"]),
    (["FETCH /health"], ["not one of GET, POST"]),
    (["GET health"], ["the path must start with '/'"]),
]


def x5(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "each of 8 bad values: exactly 1 bad_exclude error naming the reason; workspace validate "
        "exit 1 (ok false); contracts check exit 2 with links []; the project stays listed "
        "without exclude. export streak --exclude and export --project --exclude GET: exit 2"
    )
    clone = shared(sc)
    ms = [
        sc.workspace("X5", clone, variant=f"v{i}", exclude=v)
        for i, (v, _r) in enumerate(X5_ROWS, 1)
    ]
    ok = sc.workspace("X5", clone, variant="ok")
    py = sys.executable

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {"rows": []}
        for m in ms:
            v = sc.run([py, "-m", "lattice.workspace", "validate", "--json", "--workspace", str(m)])
            s = sc.run([py, "-m", "lattice.workspace", "show", "--json", "--workspace", str(m)])
            c = sc.check(m)
            rec["rows"].append({"validate": rec_of(v), "show": rec_of(s), "check": rec_of(c)})
        e1 = sc.contracts(ok, "export", "streak", "--exclude", "GET /health")
        e2 = sc.run([py, "-m", "lattice.contracts", "export", "--project", str(clone),
                     "--exclude", "GET"])  # fmt: skip
        rec["cli_name_flag"] = {"rc": e1.rc, "out": e1.out, "err": e1.err}
        rec["cli_bad_flag"] = {"rc": e2.rc, "out": e2.out, "err": e2.err}
        return rec

    runs, ident = sc.repeat(one)
    r = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    for i, ((_val, reasons), row) in enumerate(zip(X5_ROWS, r["rows"], strict=True), 1):
        vdoc = json.loads(row["validate"]["out"])
        probs = vdoc["problems"]
        proj = next(p for p in json.loads(row["show"]["out"])["projects"] if p["name"] == "streak")
        cdoc = json.loads(row["check"]["out"])
        msg = probs[0]["message"] if probs else ""
        ok_row = (
            [(p["code"], p["severity"]) for p in probs] == [("bad_exclude", "error")]
            and all(x in msg for x in reasons)
            and row["validate"]["rc"] == 1
            and vdoc["ok"] is False
            and row["check"]["rc"] == 2
            and cdoc["links"] == []
            and [p["code"] for p in cdoc["problems"]] == ["bad_exclude"]
            and "exclude" not in proj
            and len(vdoc["projects"]) == 2
            and len(vdoc["links"]) == 1
        )
        checks[f"row{i}"] = ok_row
        parts.append(
            f"{i}: validate {row['validate']['rc']}, check {row['check']['rc']}, {msg[:70]}"
        )
    checks["cli_name_flag_exit_2"] = (
        r["cli_name_flag"]["rc"] == 2 and r["cli_name_flag"]["out"] == ""
    )
    checks["cli_bad_flag_exit_2"] = r["cli_bad_flag"]["rc"] == 2 and r["cli_bad_flag"]["out"] == ""
    parts.append(f"export streak --exclude: exit {r['cli_name_flag']['rc']}; "
                 f"--project --exclude GET: exit {r['cli_bad_flag']['rc']}")  # fmt: skip
    return mk("X5", pred, "; ".join(parts), runs, ident, checks)


# ---- X6: an exclusion the spec does not hold --------------------------------------------------


def x6(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "(a) Streak 3 keys incl. GET /v1/nope, pinned with them: 24 endpoints, excluded the 3 "
        "sorted, unmatched [GET /v1/nope], check exit 0, 0 findings, 2 gaps, 1 warning "
        "exclude_unmatched; validate exit 0, no problem. (b) job-scheduler PATCH "
        "/applications/{id}/jobs/{id}: 20 endpoints, 1 warning, hash != golden; vs golden lock "
        "exit 1, 1 CHANGED exclusion_added @ PATCH /applications/{}/jobs/{} unrelied"
    )
    keys_a = [*HARNESS, "GET /v1/nope"]
    ma = sc.workspace("X6a", shared(sc), lock=None, exclude=keys_a)
    mb = jc.workspace("X6b", shared(jc), lock=None, exclude=["PATCH /applications/{id}/jobs/{id}"])
    lock_a, lock_b = sc.lock_path(ma), jc.lock_path(mb)
    py = sys.executable

    def one(_: int) -> dict[str, Any]:
        lock_a.unlink(missing_ok=True)
        rec: dict[str, Any] = {"a_export": jdoc(sc.export(ma)), "a_pin": canon_call(sc.pin(ma))}
        rec["a_check"] = canon_call(sc.check(ma))
        v = sc.run([py, "-m", "lattice.workspace", "validate", "--json", "--workspace", str(ma)])
        rec["a_validate"] = rec_of(v)
        lock_b.write_bytes(jc.golden)
        rec["b_export"] = jdoc(jc.export(mb))
        rec["b_check"] = canon_call(jc.check(mb))
        return rec

    runs, ident = jc.repeat(one)
    r = runs[0]
    la, lb = lk0(r["a_check"]), lk0(r["b_check"])
    ea, eb = r["a_export"], r["b_export"]
    va = json.loads(r["a_validate"]["out"])
    wa = [p["code"] for p in r["a_check"]["doc"]["problems"]]
    pa = r["a_pin"]["doc"]["links"][0]
    checks = {
        "a_24_endpoints": len(ea["export"]["endpoints"]) == 24,
        "a_excluded_3_sorted": ea["export"]["excluded"] == sorted(keys_a),
        "a_unmatched": ea["exclude_unmatched"] == ["GET /v1/nope"],
        "a_pin_exit_0": r["a_pin"]["rc"] == 0 and pa["endpoints"] == 24,
        "a_check_exit_0_0_findings_2_gaps": r["a_check"]["rc"] == 0
        and la["findings"] == []
        and len(la["gaps"]) == 2,  # fmt: skip
        "a_1_warning": wa == ["exclude_unmatched"]
        and r["a_check"]["doc"]["problems"][0]["severity"] == "warning",  # fmt: skip
        "a_validate_exit_0_no_problem": r["a_validate"]["rc"] == 0 and va["problems"] == [],
        "b_20_endpoints": len(eb["export"]["endpoints"]) == 20,
        "b_unmatched": eb["exclude_unmatched"] == ["PATCH /applications/{}/jobs/{}"],
        "b_hash_moved": not eb["version"]["spec_hash"].startswith(HASH_J),
        "b_exit_1": r["b_check"]["rc"] == 1,
        "b_1_changed": chg(lb) == [("exclusion_added", "PATCH /applications/{}/jobs/{}", "", False)]
        and brk(lb) == [],  # fmt: skip
        "b_1_warning": [p["code"] for p in r["b_check"]["doc"]["problems"]]
        == ["exclude_unmatched"],
    }
    obs = (
        f"a: {len(ea['export']['endpoints'])} endpoints, unmatched {ea['exclude_unmatched']}, "
        f"pin {r['a_pin']['rc']}, check exit {r['a_check']['rc']}, {len(la['findings'])} findings,"
        f" {len(la['gaps'])} gaps, warnings {wa}, validate exit {r['a_validate']['rc']}; b: "
        f"{len(eb['export']['endpoints'])} endpoints, unmatched {eb['exclude_unmatched']}, "
        f"check exit {r['b_check']['rc']}, CHANGED {[(k, e) for k, e, _, _ in chg(lb)]}"
    )
    return mk("X6", pred, obs, runs, ident, checks)


# ---- X7: parity (MCP stdio and the Studio API) -------------------------------------------------


def x7(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "on X2 (pinned after), X3 (pinned before) and X6a: MCP contract_check and GET "
        "/api/contracts each equal `contracts check --static-only --json` (6 of 6)"
    )
    clone = shared(sc)
    solo = base.solo_project(sc.work / "X7-solo")
    setups: dict[str, tuple[Path, str]] = {}
    m = sc.workspace("X7x2", clone, lock=None, exclude=HARNESS)
    setups["X2_after"] = (m, "pin")
    m = sc.workspace("X7x3", clone, lock=None, exclude=HARNESS)
    imp = m.parent.parent / streak.CONSUMER / IMPORTS
    imp.write_text(imp.read_text() + CLOCK_IMPORT)
    setups["X3_before"] = (m, "golden")
    m = sc.workspace("X7x6", clone, lock=None, exclude=[*HARNESS, "GET /v1/nope"])
    setups["X6a"] = (m, "pin")

    def api(manifest: Path) -> dict[str, Any]:
        port = base.free_port()
        argv = [sys.executable, "-m", "lattice.studio", str(solo), "--port", str(port), "--quiet",
                "--stub-llm", "--workspace", str(manifest)]  # fmt: skip
        with base.background(sc, argv, f"http://127.0.0.1:{port}/api/projects"):
            doc: dict[str, Any] = base.http_get(f"http://127.0.0.1:{port}/api/contracts")
            return doc

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for name, (m, how) in setups.items():
            lock = sc.lock_path(m)
            lock.unlink(missing_ok=True)
            if how == "golden":
                lock.write_bytes(sc.golden)
            else:
                p = sc.pin(m)
                assert p.rc == 0, p.out + p.err
            cli = canon_call(sc.contracts(m, "check", "--static-only", "--json"))
            mcp = base.mcp_contract_check(sc, solo, m)
            rec[name] = {
                "cli": cli,
                "mcp": strip_elapsed(mcp["doc"]),
                "api": strip_elapsed(api(m)),
                "mcp_lists_tool": mcp["tools_have_contract_check"],
            }
        return rec

    runs, ident = sc.repeat(one)
    r = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    for name in setups:
        t = r[name]
        checks[f"{name}_mcp_equals_cli"] = t["mcp"] == t["cli"]["doc"]
        checks[f"{name}_api_equals_cli"] = t["api"] == t["cli"]["doc"]
        l0 = t["cli"]["doc"]["links"][0]
        parts.append(
            f"{name}: cli exit {t['cli']['rc']} {l0['status']}, mcp==cli "
            f"{yn(checks[f'{name}_mcp_equals_cli'])}, api==cli "
            f"{yn(checks[f'{name}_api_equals_cli'])}"
        )
    checks["scenarios_are_not_vacuous"] = (
        r["X2_after"]["cli"]["rc"] == 0
        and r["X3_before"]["cli"]["rc"] == 1
        and r["X6a"]["cli"]["doc"]["problems"] != []
    )
    return mk("X7", pred, "; ".join(parts), runs, ident, checks)


# ---- X8: manifests without the key are untouched ----------------------------------------------

OLD_ROOTS: dict[str, Path] = {}


def old_code(jc: JCtx) -> Path:
    """`git archive 7358be2 lattice` extracted under the scratch root (once)."""
    if "old" not in OLD_ROOTS:
        dst = jc.root / "old-7358be2"
        dst.mkdir()
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", OLD_REF, "lattice"], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(dst)], input=tar, check=True)
        OLD_ROOTS["old"] = dst
    return OLD_ROOTS["old"]


def x8(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "3 manifests without the key x 6 commands (show, show --json, validate, validate --json, "
        "impact NAME, impact NAME --json): stdout, stderr and exit byte-identical to the code at "
        "7358be2 (18 of 18); reported: 7358be2 validates the X2 manifest with exclude at exit 0 "
        "with no problem"
    )
    old = old_code(jc)
    d = jc.work / "X8g"
    for x in ("billing", "web", "auth"):
        (d / x).mkdir(parents=True, exist_ok=True)
    good = d / MANIFEST
    good.write_text(base.GOOD_MANIFEST)
    manifests = {
        "X1_no_key": (jc.workspace("X8j", shared(jc), lock=None), "scheduler"),
        "X2_no_key": (sc.workspace("X8s", shared(sc), lock=None), "streak"),
        "GOOD_MANIFEST": (good, "shared-auth"),
    }
    with_key = sc.workspace("X8s", shared(sc), lock=None, variant="withkey", exclude=HARNESS)
    py = sys.executable

    def cli(root: Path, m: Path, *cmd: str) -> dict[str, Any]:
        env = dict(jc.env)
        env["PYTHONPATH"] = str(root)
        c = jc.run([py, "-m", "lattice.cli", "workspace", "--workspace", str(m), *cmd], env=env)
        return rec_of(c)

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for label, (m, name) in manifests.items():
            for cmd in (["show"], ["show", "--json"], ["validate"], ["validate", "--json"],
                        ["impact", name], ["impact", name, "--json"]):  # fmt: skip
                rec[f"{label}: {' '.join(cmd)}"] = {
                    "new": cli(REPO, m, *cmd),
                    "old": cli(old, m, *cmd),
                }
        rec["with_key: validate"] = {
            "new": cli(REPO, with_key, "validate"),
            "old": cli(old, with_key, "validate"),
        }
        return rec

    runs, ident = jc.repeat(one)
    r = runs[0]
    cmp_rows = {k: v for k, v in r.items() if not k.startswith("with_key")}
    same = {k: v["new"] == v["old"] for k, v in cmp_rows.items()}
    wk = r["with_key: validate"]
    old_export = (old / "lattice" / "contracts" / "export.py").read_text()
    checks = {
        "18_of_18_identical": len(same) == 18 and all(same.values()),
        "old_code_is_old": "apply_exclude" not in old_export and "check_exclude" not in old_export,
        "new_code_is_new": "apply_exclude" in (REPO / "lattice/contracts/export.py").read_text(),
    }
    old_out = wk["old"]["out"]
    old_clean = wk["old"]["rc"] == 0 and "error" not in old_out and "warning" not in old_out
    obs = (
        f"{sum(same.values())}/{len(same)} byte-identical; reported: 7358be2 on the X2 manifest "
        f"with exclude: validate exit {wk['old']['rc']}, no problem {yn(old_clean)}; new code "
        f"exit {wk['new']['rc']}"
    )
    extra = {"old_validates_with_key": {"rc": wk["old"]["rc"], "out": wk["old"]["out"]}}
    return mk("X8", pred, obs, runs, ident, checks, extra)


# ---- X9: live ---------------------------------------------------------------------------------


def l1_replays() -> list[dict[str, Any]]:
    j = json.loads((COMMITTED / "2026-10-13-contracts" / "L1.json").read_text())
    return j["runs"][0]["full"]["doc"]["links"][0]["live"]["replays"]  # type: ignore[no-any-return]


def x9(jc: JCtx, sc: SX, ns: argparse.Namespace) -> Result:
    pred = (
        "(a) worker keys: static 0 findings, 1 gap; live replayed 7, not_replayed [], 0 "
        "findings, verified_live [access_token], replays = L1's; exit 0. (b) plus the cancel key: "
        "17 endpoints, 1 BROKEN endpoint_missing @ cancel (EX), 1 gap; pin needs --force; exit 1; "
        "live replayed 6, not_replayed exactly the cancel endpoint "
        "'excluded by the provider (exclude)', 0 live findings, verified_live [access_token], "
        "replays = L1's minus cancel"
    )
    want = l1_replays()
    ca = jc.clone("X9a")
    cb = jc.clone("X9b")
    ma = jc.workspace("X9a", ca, lock=None, contract="live", exclude=WORKER)
    mb = jc.workspace("X9b", cb, lock=None, contract="live", exclude=[*WORKER, CANCEL_DECL])
    la_, lb_ = jc.lock_path(ma), jc.lock_path(mb)

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        la_.unlink(missing_ok=True)
        rec["a_pin"] = canon_call(jc.pin(ma))
        rec["a_check"] = canon_call(jc.check(ma))
        lb_.unlink(missing_ok=True)
        rec["b_pin"] = canon_call(jc.pin(mb))
        rec["b_lock_after_refusal"] = lb_.exists()
        rec["b_pin_force"] = canon_call(jc.pin(mb, "--force"))
        rec["b_check"] = canon_call(jc.check(mb))
        rec["b_export_endpoints"] = len(jdoc(jc.export(mb))["export"]["endpoints"])
        return rec

    runs, ident = jc.repeat(one)
    r = runs[0]
    a, b = lk0(r["a_check"]), lk0(r["b_check"])
    al, bl = a["live"], b["live"]

    def verified(lv: dict[str, Any]) -> list[tuple[str, str, str]]:
        return [(v["kind"], v["endpoint"], v["item"]) for v in lv["verified_live"]]

    checks = {
        "a_pin_exit_0": r["a_pin"]["rc"] == 0,
        "a_exit_0": r["a_check"]["rc"] == 0,
        "a_static_0_findings_1_gap": a["findings"] == [] and gaps(a) == SCHEDULER_GAPS,
        "a_live_ok_replayed_7_none_skipped": al["status"] == "ok"
        and al["replayed"] == 7
        and al["not_replayed"] == [],  # fmt: skip
        "a_live_0_findings": al["findings"] == [],
        "a_verified_live_access_token": verified(al) == SCHEDULER_GAPS,
        "a_replays_equal_L1": al["replays"] == want,
        "b_pin_refused_without_force": r["b_pin"]["rc"] == 1
        and lk0(r["b_pin"])["status"] == "refused"
        and not r["b_lock_after_refusal"],  # fmt: skip
        "b_pin_force_exit_0": r["b_pin_force"]["rc"] == 0,
        "b_17_endpoints": r["b_export_endpoints"] == 17,
        "b_static_1_broken_EX": brk(b) == [("endpoint_missing", CANCEL, "", EX)]
        and chg(b) == []
        and gaps(b) == SCHEDULER_GAPS,  # fmt: skip
        "b_exit_1": r["b_check"]["rc"] == 1,
        "b_live_replayed_6": bl["status"] == "ok" and bl["replayed"] == 6,
        "b_not_replayed_exactly_cancel": bl["not_replayed"]
        == [{"endpoint": CANCEL, "reason": "excluded by the provider (exclude)"}],
        "b_live_0_findings": bl["findings"] == [],
        "b_verified_live_access_token": verified(bl) == SCHEDULER_GAPS,
        "b_replays_equal_L1_minus_cancel": bl["replays"]
        == [x for x in want if x["endpoint"] != CANCEL],
    }
    obs = (
        f"a: pin {r['a_pin']['rc']}, exit {r['a_check']['rc']}, static findings "
        f"{len(a['findings'])}, gaps {len(a['gaps'])}, live {al['status']} replayed "
        f"{al['replayed']} not_replayed {len(al['not_replayed'])} findings {len(al['findings'])} "
        f"verified {len(al['verified_live'])}, replays==L1 {yn(checks['a_replays_equal_L1'])}; b: "
        f"pin {r['b_pin']['rc']} {lk0(r['b_pin'])['status']}, force {r['b_pin_force']['rc']}, "
        f"{r['b_export_endpoints']} endpoints, exit {r['b_check']['rc']}, BROKEN "
        f"{[(k, e) for k, e, _, _ in brk(b)]}, gaps {len(b['gaps'])}, live replayed "
        f"{bl['replayed']} not_replayed {bl['not_replayed']} findings {len(bl['findings'])} "
        f"verified {len(bl['verified_live'])}, replays==L1-cancel "
        f"{yn(checks['b_replays_equal_L1_minus_cancel'])}"
    )
    return mk("X9", pred, obs, runs, ident, checks)


# ---- S: safety ---------------------------------------------------------------------------------


class RealRepo:
    """The safety record of a real checkout, taken before the whole run."""

    def __init__(self, ctx: Any, path: Path, ref: str, status: str, skip: list[str]) -> None:
        self.ctx, self.path, self.ref, self.want_status, self.skip = ctx, path, ref, status, skip
        self.marker = ctx.root / f"s-marker-{path.name}"
        self.marker.write_text("start\n")
        time.sleep(1.1)  # a clear mtime gap for find -newer
        self.before = self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        # --no-optional-locks: `git status` must not even refresh the real repository's index
        head = self.ctx.git(self.path, "rev-parse", "HEAD").strip()
        status = self.ctx.git(self.path, "--no-optional-locks", "status", "--short").rstrip("\n")
        return {"head": head, "status": status}

    def newer(self) -> list[str]:
        argv = ["find", str(self.path), "-newer", str(self.marker), "-not", "-path", "*/.git/*",
                "-not", "-path", "*/.git"]  # fmt: skip
        for s in self.skip:
            argv += ["-not", "-path", f"*/{s}/*", "-not", "-path", f"*/{s}"]
        return sorted(ln for ln in self.ctx.run(argv).out.splitlines() if ln)


def s_probe(repos: dict[str, RealRepo]) -> Result:
    pred = (
        "the real job-scheduler (HEAD ef17cf8, ' M uv.lock') and Streak (HEAD a0db033, ' M "
        "studio.toml') have the same HEAD and git status before and after the run, and 0 files "
        "newer than the start marker"
    )
    checks: dict[str, Any] = {}
    parts = []
    runs = []
    for name, rr in repos.items():
        after = rr.snapshot()
        newer = rr.newer()
        checks[f"{name}_head"] = rr.before["head"].startswith(rr.ref) and after == rr.before
        checks[f"{name}_status_expected"] = (
            rr.before["status"] == rr.want_status and after["status"] == rr.want_status
        )
        checks[f"{name}_no_newer_file"] = newer == []
        parts.append(
            f"{name}: {rr.before['head'][:7]} {rr.before['status']!r} -> {after['head'][:7]} "
            f"{after['status']!r}, {len(newer)} newer files {newer[:3]}"
        )
        runs.append({"repo": name, "before": rr.before, "after": after, "newer": newer})
    return Result("S", pred, "; ".join(parts), all(checks.values()), runs, True, checks, {})


# ---- main -------------------------------------------------------------------------------------

PROBES = {"X0": x0, "X1": x1, "X2": x2, "X2b": x2b, "X3": x3, "X4": x4, "X5": x5, "X6": x6,
          "X7": x7, "X8": x8, "X9": x9}  # fmt: skip
ORDER = [*PROBES, "S"]


def scrub(x: Any, roots: dict[str, Path]) -> Any:
    if isinstance(x, str):
        for tag, root in roots.items():
            x = x.replace(str(root), tag)
        return x
    if isinstance(x, (list, tuple)):
        return [scrub(v, roots) for v in x]
    if isinstance(x, dict):
        return {k: scrub(v, roots) for k, v in x.items()}
    return x


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--keep", action="store_true", help="keep the scratch clones (default: delete)")
    ap.add_argument("--skip-regression", action="store_true",
                    help="skip X0 (d), the two earlier runners (development only)")  # fmt: skip
    ns = ap.parse_args(argv)
    ids = [i for i in ns.only.split(",") if i] or ORDER
    unknown = [i for i in ids if i not in ORDER]
    if unknown:
        print(f"unknown probe ids: {unknown}", file=sys.stderr)
        return 2
    ids = [i for i in ORDER if i in ids]
    ns.out.mkdir(parents=True, exist_ok=True)
    scratch = ns.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    ns.scratch = scratch
    jc = JCtx(ns.runs, scratch / "j", ns.out)
    sc = SX(ns.runs, scratch / "s", ns.out)
    roots = {"$WORK_J": jc.root, "$WORK_S": sc.root, "$SCRATCH": scratch}
    repos = {
        "job-scheduler": RealRepo(jc, base.PROVIDER, base.REF, " M uv.lock", [".venv"]),
        "streak": RealRepo(sc, streak.STREAK, streak.REF, " M studio.toml", []),
    }
    t_start = time.time()
    results: list[Result] = []

    def write(res: Result) -> None:
        doc = {"id": res.id, "prediction": res.prediction, "observed": res.observed,
               "pass": res.passed, "identical": res.identical, "n_runs": len(res.runs),
               "checks": res.checks, "extra": res.extra, "runs": res.runs}  # fmt: skip
        (ns.out / f"{res.id}.json").write_text(json.dumps(scrub(doc, roots), indent=1) + "\n")

    try:
        for pid in ids:
            if pid == "S":
                continue
            print(f"... {pid}", file=sys.stderr, flush=True)
            res = PROBES[pid](jc, sc, ns)
            results.append(res)
            write(res)
        if "S" in ids:
            res = s_probe(repos)
            results.append(res)
            write(res)
    finally:
        procs = base.leftover_processes(jc) + base.leftover_processes(sc)
        tmp_left = sorted(
            f"{t}/{p.name}"
            for t, c in (("j", jc), ("s", sc))
            for p in (c.tmp.iterdir() if c.tmp.exists() else [])
        )
        if not ns.keep:
            for c in (jc, sc):
                shutil.rmtree(c.root, ignore_errors=True)
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<4} | {'predicted':<60} | {'observed':<80} | pass")
    for r in results:
        print(f"{r.id:<4} | {r.prediction} | {r.observed} | {'PASS' if r.passed else 'FAIL'}")
    print(f"probes: {passed}/{len(results)} pass")
    stat = subprocess.run(["git", "-C", str(REPO), "diff", "--stat", OLD_REF, "--", "lattice/"],
                          text=True, capture_output=True, check=False).stdout.strip()  # fmt: skip
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True,
                          capture_output=True, check=False).stdout.strip()  # fmt: skip
    summary = {
        "experiment": "2026-10-13-contracts-exclude",
        "command": "python dogfood/contracts/exclude_probes.py " + " ".join(sys.argv[1:]),
        "runs": ns.runs,
        "code_root": str(REPO),
        "code_head": head,
        "lattice_diff_stat_vs_7358be2": stat,
        "job_scheduler_ref": base.REF,
        "streak_ref": streak.REF,
        "wall_s": round(time.time() - t_start, 1),
        "probes": [
            {
                "id": r.id,
                "prediction": r.prediction,
                "observed": r.observed,
                "pass": r.passed,
                "runs": len(r.runs),
                "identical": r.identical,
                "checks": r.checks,
            }
            for r in results
        ],  # fmt: skip
        "totals": {"pass": passed, "total": len(results)},
        "regression_skipped": ns.skip_regression,
        "safety_before": {k: v.before for k, v in repos.items()},
        "leftover_processes": procs,
        "leftover_tmp_entries": tmp_left,
        "scratch_removed": not ns.keep,
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },
    }
    (ns.out / "summary.json").write_text(json.dumps(scrub(summary, roots), indent=1) + "\n")
    return 0 if passed == len(results) and not procs else 1


if __name__ == "__main__":
    sys.exit(main())
