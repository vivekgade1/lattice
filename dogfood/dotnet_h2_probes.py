"""Probes of the blind held-out .NET round H2 (docs/results/2026-10-23-dotnet-h2-prereg.md, sections 4, 6-8 and 10).

The rows of prereg section 4 on the held-out app H2 only: 13 gated rows (H1 prereg 5.7's Arm H gates) and 7 reported ones. The row code is
the frozen H1 runner's (``dotnet_probes``, imported, never edited) except H2-PI7 (timing fields only are masked) and H2-RAISE (precision
1.00); layer R goes through :class:`H2Lib`, which passes ``relogin`` and ``crash_admits`` as ``lattice check`` and the import do.

    "$PY" dogfood/dotnet_h2_probes.py --kind scored --out docs/results/data/2026-10-23-dotnet-h2/scored --scratch "$SCRATCH"
    "$PY" dogfood/dotnet_h2_probes.py --kind verify --out "$V/verify" --scratch "$V/scratch"      # node V, once
    "$PY" dogfood/dotnet_h2_probes.py --self-test [--out DIR]          # stand-in H2 = H1 (h1.tgz), never the ledger
    "$PY" dogfood/dotnet_h2_probes.py --verify-freeze-only [--inputs FILE]
    "$PY" dogfood/dotnet_h2_probes.py --write-freeze FILE              # the orchestrator, once, after the self-tests

Defaults: ``--freeze docs/results/data/2026-10-23-dotnet-h2/freeze.sha256``, ``--inputs .../h2-inputs.sha256``, ``--h2 .../h2.tgz``,
``--h2-truth .../h2-truth.json``.

What cannot be switched off (prereg sections 7-8): before any metric AND before any ledger line, every entry of the freeze file is re-hashed
and the must-list (the prereg, both H2 runners, the H1 runners they import, the brief and dispatch files, the self-tests' stand-in inputs, the
47 library files changed since c75cb48, and every file under ``lattice/``) must be listed, the running script included; the inputs manifest
is re-hashed and must list the H2 archive and its truth. A refusal is appended to ``refused.jsonl``, never to the ledger. ``lattice.__file__``
must lie inside this tree. The ledger ``docs/results/data/2026-10-23-dotnet-h2/ledger.jsonl`` is fixed here: ``scored`` once, ``rerun`` once
after ``crash_before_metric`` with the same bytes, ``verify`` once (the H1 runner's ``ledger_refusal``). :func:`one_row` is the only producer of
NOT RUN, and only for a named missing prerequisite.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dotnet_probes as G  # noqa: E402  -- the frozen H1 runner: row functions, scorers, targets, import, Lib

REPO = G.REPO
RUNNER_NAME = "dotnet_h2_probes"
RUNNER_REL = "dogfood/dotnet_h2_probes.py"
MUTANTS_REL = "dogfood/dotnet_h2_mutants.py"
PREREG_REL = "docs/results/2026-10-23-dotnet-h2-prereg.md"
DATA2_REL = "docs/results/data/2026-10-23-dotnet-h2"
DATA2 = REPO / DATA2_REL
LEDGER = DATA2 / "ledger.jsonl"  # fixed here, never derived from --out
REFUSED = DATA2 / "refused.jsonl"  # refused launches (no metric, no ledger line)
FREEZE = DATA2 / "freeze.sha256"
INPUTS = DATA2 / "h2-inputs.sha256"
H2_TGZ = DATA2 / "h2.tgz"
H2_TRUTH = DATA2 / "h2-truth.json"
H2_PATCHES = DATA2 / "h2-mutants-run"
H1_DATA_REL = "docs/results/data/2026-10-18-dotnet"
STANDIN_TGZ = REPO / H1_DATA_REL / "h1.tgz"  # the self-tests' stand-in H2: H1, which is dev data now
STANDIN_TRUTH = REPO / H1_DATA_REL / "h1-truth.json"
STANDIN_PATCHES = REPO / H1_DATA_REL / "h1-mutants-run"
ARM = "H2:H"
KINDS = ("scored", "rerun", "verify")

# ---- what the freeze file must list (prereg section 8) --------------------------------------------------------------------------------------

FIXED: tuple[str, ...] = (
    PREREG_REL,
    RUNNER_REL,
    MUTANTS_REL,
    G.RUNNER_REL,  # dogfood/dotnet_probes.py (imported)
    G.MUTANTS_REL,  # dogfood/dotnet_mutants.py (imported by the mutants runner)
    f"{DATA2_REL}/brief/BRIEF.md",
    f"{DATA2_REL}/brief/BRIEF-W.md",
    f"{DATA2_REL}/brief/HARNESS_API_DOTNET.md",
    f"{DATA2_REL}/brief/TRUTH_SCHEMA_DOTNET.md",
    f"{DATA2_REL}/dispatch_author.sh",
    f"{DATA2_REL}/dispatch_mutants.sh",
    f"{DATA2_REL}/audit_transcript.py",
    f"{H1_DATA_REL}/h1.tgz",
    f"{H1_DATA_REL}/h1-truth.json",
    f"{H1_DATA_REL}/h1-mutants-run/MUTANTS.md",
    *[f"{H1_DATA_REL}/h1-mutants-run/m{i:02d}.diff" for i in range(1, 17)],
)
LIB_CHANGED: tuple[str, ...] = (  # prereg section 2: `git diff --name-status c75cb48 4062435` under the four paths (47 files)
    "lattice/dncheck/__init__.py",
    "lattice/dncheck/derive.py",
    "lattice/dncheck/policy.py",
    *[
        f"lattice/dncheck/src/{n}.cs"
        for n in (
            "Assemble", "Auth", "Binding", "CheckBounds", "CheckDatetime", "CheckErrors", "CheckMachine", "CheckOwner", "CheckOwnerCover",
            "CheckPartial", "CheckPrivate", "Closure", "Ef", "Json", "Load", "Model", "Owner", "Program", "Projection", "Raises", "Required",
            "Routes", "State", "Workflow",
        )
    ],
    "lattice/dncheck/src/DnCheck.csproj",
    "lattice/dncheck/src/packages.lock.json",
    "lattice/importer/appmodel.py",
    "lattice/importer/check_invocation.py",
    "lattice/importer/dotnet_rules.py",
    "lattice/importer/emit_spec.py",
    "lattice/importer/extract_source.py",
    "lattice/importer/formats.py",
    "lattice/importer/frontends/__init__.py",
    "lattice/importer/frontends/dotnet_aspnet.py",
    "lattice/importer/frontends/go_grpc.py",
    "lattice/importer/go_rules.py",
    "lattice/importer/inventory.py",
    "lattice/importer/profile_gen.py",
    "lattice/importer/review.py",
    "lattice/importer/rules.py",
    "lattice/importer/stable.py",
    "lattice/importer/workflow_run.py",
    "lattice/spec/check.py",
    "lattice/spec/probes.py",
)  # fmt: skip
JUNK_PARTS = ("__pycache__", ".DS_Store", "bin", "obj")


def lib_files(repo: Path = REPO) -> list[str]:
    """Every file under ``lattice/`` (junk and build output excepted): an added or changed library file is a freeze failure."""
    out = []
    for p in sorted((repo / "lattice").rglob("*")):
        rel = p.relative_to(repo)
        if p.is_file() and p.suffix != ".pyc" and not any(part in JUNK_PARTS for part in rel.parts):
            out.append(rel.as_posix())
    return out


def must_list(repo: Path = REPO) -> list[str]:
    return list(dict.fromkeys([*FIXED, *LIB_CHANGED, *lib_files(repo)]))


def verify_freeze(path: Path, this: Path, repo: Path = REPO) -> dict[str, Any]:
    """The self-hash check (prereg section 8): every entry re-hashed, every must-list path listed, the running script listed."""
    return G.verify_listed(path, root=repo, must=must_list(repo), this=this)


def verify_inputs(path: Path, required: Sequence[Path], repo: Path = REPO) -> dict[str, Any]:
    """The inputs manifest (H2 itself): every entry re-hashed; ``required`` (the archive, the truth, the mutant files) must be listed."""
    return G.verify_listed(path, root=repo, must=[], required_abs=required)


def write_freeze(path: Path) -> int:
    """The freeze file: every must-list path, each of which must exist (an incomplete freeze is refused, not written)."""
    rels = must_list()
    absent = [r for r in rels if not (REPO / r).is_file()]
    if absent:
        raise SystemExit(f"refusing to write the freeze file: {len(absent)} must-list files do not exist: {absent[:8]}")
    path.write_text("".join(f"{G.sha(REPO / r)}  {r}\n" for r in rels))
    return len(rels)


def log_refusal(why: str, **extra: Any) -> None:
    """A refused launch: one line in ``refused.jsonl`` (not the ledger, so it cannot consume the once-only slot)."""
    G.append_ledger({"ts": G.now(), "status": "refused", "why": why[:1200], **extra}, REFUSED)


# ---- layer R as `lattice check` runs it on an ASP.NET Core app ---------------------------------------------------------------------------------


class H2Lib(G.RealLib):
    """``RealLib`` with the H1 runner's known gap fixed (prereg 4.1): layer R re-probes the candidate spec with ``relogin`` and
    ``crash_admits`` exactly as ``dogfood/dotnet_hm_dev.py``'s ``DevLib.r_verdicts`` does (``check.py`` sets both for a dotnet runtime, the
    import for aspnetcore). Every other method is ``RealLib``'s. ``last_r_kwargs`` is read by the self-tests."""

    last_r_kwargs: dict[str, Any] | None = None

    def r_verdicts(self, ctx: G.Ctx, tgt: dict, cand_dir: Path, variant: str, *, spec: object = None) -> dict[str, str]:  # type: ignore[override]
        from dataclasses import replace

        from lattice.spec.probes import run_probes

        m = self.machinery(ctx, tgt)
        cand = spec if spec is not None else G.load_candidate(cand_dir)
        seeder, res, rules = m["seeder"], m["result"], m["rules"]
        who = rules.callers(res, seeder, variant)
        kw = dict(login=who.login, mint_token=who.mint, scheme=who.scheme, refusals=who.refusals, login_route=who.login_route, auth_headers=who.headers, inactive_login=who.inactive_login, unreachable=m["unreachable"], variant=variant, auth_present=who.present, json_login=getattr(who, "json_login", None), relogin=who.relogin, crash_admits=who.crash_admits)  # fmt: skip
        self.last_r_kwargs = {"relogin": kw["relogin"], "crash_admits": kw["crash_admits"], "variant": variant}
        rr = run_probes(replace(cand.SPEC, users=seeder.credentials(variant)), m["sessions"], seeder.make_db, **kw)
        rules.mark_unclear(cand.SPEC, rr, m["unclear"])
        rules.mark_outside(cand.SPEC, rr, m["outside"])
        rules.mark_uploads(rr, m["uploads"])
        return G.verdict_map(rr)


# ---- the rows (prereg section 4) ------------------------------------------------------------------------------------------------------------


def h2_raise(ctx: G.Ctx) -> dict[str, Any]:
    """H2-RAISE: H1's scorer; pass iff recall >= 0.60 and precision == 1.00 (stricter than H1 prereg 5.7's >= 0.95, reported beside it)."""
    s = G.score_raise(*G.h_ex(ctx))
    ok = s["recall"] >= 0.60 and s["precision"] == 1.0
    return G.result("H2-RAISE", "refusals (route, status)", None, s, ok, h1_gate_pass=s["recall"] >= 0.60 and s["precision"] >= 0.95)


WALL_MD = re.compile(rb"(output tokens, )[0-9][0-9.e+-]*( s)")


def timing_tree(d: Path) -> dict[str, str]:
    """The import directory byte for byte with ONLY the timing fields masked (prereg 4, H2-PI7): ``report.json`` ``rules.timing`` and
    ``cost.wall_s``, and the same wall-clock number at the end of ``report.md``'s cost line. Minted values are NOT masked here."""
    out: dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(d).as_posix()
        data = p.read_bytes()
        if rel == "report.json":
            r = json.loads(data)
            if isinstance(r.get("rules"), dict) and "timing" in r["rules"]:
                r["rules"]["timing"] = "<timing>"
            if isinstance(r.get("cost"), dict) and "wall_s" in r["cost"]:
                r["cost"]["wall_s"] = "<wall_s>"
            data = (json.dumps(r, indent=2, sort_keys=True) + "\n").encode()
        elif rel == "report.md":
            data = WALL_MD.sub(rb"\1<wall_s>\2", data)
        out[rel] = G.sha_bytes(data)
    return out


def h2_pi7(ctx: G.Ctx) -> dict[str, Any]:
    """H2-PI7: the import ``ctx.runs`` times into the SAME directory (so no path of the runner's choosing differs), each tree hashed with
    :func:`timing_tree`; pass iff all are identical."""
    t = G.ht(ctx)
    d = ctx.scratch / "imp7" / t["label"]
    trees: list[dict[str, str]] = []
    for _ in range(ctx.runs):
        r = ctx.lib.import_rules(ctx, t, d)
        trees.append(timing_tree(d) if r["rc"] == 0 else {"error": r["stderr"][-300:]})
    same = sum(1 for x in trees if x == trees[0])
    differing = sorted({k for x in trees for k in set(x) | set(trees[0]) if x.get(k) != trees[0].get(k)})
    return G.result("H2-PI7", f"the import {ctx.runs}x, timing fields masked only", None, {"identical": same, "of": ctx.runs, "files_differing": differing}, same == ctx.runs)


@dataclass(frozen=True)
class Row:
    gated: bool
    prediction: str
    fn: Callable[[G.Ctx], dict[str, Any]]
    h1: str  # the H1 row and section the code and gate come from
    needs: tuple[str, ...]


STATIC = ("dotnet", "h2")
RUN = ("dotnet", "h2", "h2_run", "h2_build")
ROWS: dict[str, Row] = {
    "H2-ROUTES": Row(True, "recall 0.90-1.00, 0 extra; gate recall >= 0.90 and 0 extra", G.h_routes, "H-ROUTES (5.7)", STATIC),
    "H2-AUTH": Row(True, "0.88-1.00; gate >= 0.85", G.h_auth, "H-AUTH (5.7)", STATIC),
    "H2-TABLES": Row(True, "tables 1.00, facts 0.90-1.00; gate both >= 0.90", G.h_tables, "H-TABLES (5.7)", STATIC),
    "H2-WIRE": Row(True, "0.93-1.00; gate >= 0.95", G.h_wire, "H-WIRE (5.7)", STATIC),
    "H2-UNREAD": Row(True, "0 silent drops; gate 0", G.h_unread, "H-UNREAD (5.7)", STATIC),
    "H2-BOUNDS": Row(True, "precision 0.85-1.00, recall 0.5-1.0; gate precision 1.00", G.h_bounds, "H-BOUNDS (5.7)", STATIC),
    "H2-RAISE": Row(True, "recall 0.45-0.90, precision 0.95-1.00; gate recall >= 0.60 and precision 1.00", h2_raise, "H-RAISE (5.7; precision 1.00 here)", STATIC),
    "H2-PI3": Row(True, "verified share 0.25-0.65, refuted 0-4; gate share >= 0.50, every refuted item corrected or baselined, every unexercised item with a reason", G.h_pi3, "H-PI3 (5.7)", RUN),
    "H2-PI7": Row(True, "10/10 identical, timing fields masked only (p 0.6)", h2_pi7, "H-PI7 (5.7; timing-only mask here)", RUN),
    "H2-PA1": Row(True, "0 wrong (p 0.6)", G.h_pa1, "H-PA1 (5.7)", STATIC),
    "H2-IMPL": Row(True, "0 missing/extra route rows (p 0.5)", G.h_impl, "H-IMPL (5.7)", RUN),
    "H2-WF": Row(True, ">= 1 multi-step flow verified, 0 failing on replay (p 0.7)", G.h_wf, "H-WF (5.7)", RUN),
    "H2-JUDGE": Row(True, "0.55-0.95; gate >= 0.75", G.h_judge, "H-JUDGE (5.7)", STATIC),
    # reported, not gated (the H1 gate's verdict is shown as `pass`)
    "H2-OWN": Row(False, "0 confident-wrong owners (p 0.8)", G.h_own, "H-OWN (5.7)", RUN),
    "H2-PI4": Row(False, ">= 0.95 retained on variant2 (p 0.7)", G.h_pi4, "H-PI4 (5.7)", RUN),
    "H2-PI5": Row(False, "the truth machine's first edge proposed (p 0.5)", G.h_pi5, "H-PI5 (5.7)", RUN),
    "H2-PR3": Row(False, "layer R re-run equals the import's verdict map (p 0.7)", G.h_pr3, "H-PR3 (5.7)", RUN),
    "H2-PA0": Row(False, "0 layer A findings outside the baseline under dn_spec_policy (p 0.7)", G.h_pa0, "H-PA0 (5.7)", RUN),
    "H2-DET": Row(False, "extract and layer A 10x identical (p 0.9)", G.h_det, "H-DET (5.7)", STATIC),
    "H2-X": Row(False, "import wall <= 240 s (p 0.9)", G.h_x, "H-X (5.7)", RUN),
}  # fmt: skip
ORDER = list(ROWS)
GATED = [i for i, r in ROWS.items() if r.gated]


def missing(ctx: G.Ctx, pid: str) -> str | None:
    """The named prerequisite a row lacks, else None (the only reasons a row may be NOT RUN; prereg 10.3)."""
    t = ctx.h_target
    for need in ROWS[pid].needs:
        if need == "dotnet" and not ctx.dotnet:
            return "no .NET SDK on PATH"
        if need == "h2" and t is None:
            return "no H2 target (--h2)"
        if need == "h2_run" and t is not None and not t.get("runnable"):
            return f"the target {t['label']} has no harness (parse-only)"
        if need == "h2_build" and t is not None and t.get("build_nuget"):
            return f"the H2 build needs NuGet and could not restore: {str(t.get('build_error'))[-200:]}"
    return None


def one_row(ctx: G.Ctx, pid: str) -> dict[str, Any]:
    """The only producer of a NOT RUN row. A row whose code raised is a FAIL, never NOT RUN."""
    spec = ROWS[pid]
    ctx.set_row(pid)
    why = missing(ctx, pid)
    if why is not None:
        r = G.not_run(pid, "(a prerequisite is missing)", why, spec.prediction)
    else:
        try:
            r = spec.fn(ctx)
        except Exception as err:  # noqa: BLE001
            r = G.result(pid, "(the row raised)", spec.prediction, f"{type(err).__name__}: {err}", False, traceback=traceback.format_exc()[-1800:])
    r.update(id=pid, predicted=spec.prediction, gated=spec.gated, h1_row=spec.h1, arm=ARM)
    if not spec.gated and r["pass"] is not None:
        r["note"] = "reported, not gated: `pass` is the H1 gate's verdict"
    print(f"{pid}: pass={r['pass']}{'' if spec.gated else ' (reported)'}", flush=True)
    return r


def run_rows(ctx: G.Ctx, ids: Sequence[str]) -> list[dict[str, Any]]:
    return [one_row(ctx, pid) for pid in ORDER if pid in ids]


def self_test_problems(rows: Sequence[Mapping[str, Any]], ctx: G.Ctx) -> list[str]:
    """RM1: a row NOT RUN although its named prerequisites are present."""
    return [f"{r['id']}: NOT RUN although its prerequisites are present" for r in rows if r["pass"] is None and missing(ctx, r["id"]) is None]


# ---- setup: the H2 target only (SQLite: no D1, no base tree, no Docker) ---------------------------------------------------------------------------


def setup(ctx: G.Ctx, src: Path, truth: Path, label: str = "h2") -> None:
    ctx.scratch.mkdir(parents=True, exist_ok=True)
    ctx.out.mkdir(parents=True, exist_ok=True)
    G.make_shims(ctx)  # recording docker/claude shims (exit 127) first on PATH: no model and no container can be reached
    os.environ["LATTICE_DNCHECK_CACHE"] = str(ctx.scratch / "dncheck-cache")
    ctx.d1 = None
    ctx.no_docker, ctx.docker, ctx.driver_ok = True, False, False
    ctx.dotnet = shutil.which("dotnet", path=G.real_path()) is not None
    ctx.h_target = G.materialise(label, src, ctx.scratch / "targets", truth)
    ctx.extra_snaps[str(ctx.h_target["module_dir"])] = G.snapshot(Path(ctx.h_target["module_dir"]))
    if ctx.dotnet:
        G.build_target(ctx, ctx.h_target)


def new_ctx(scratch: Path, out: Path, kind: str, runs: int) -> G.Ctx:
    ctx = G.Ctx(scratch, out, kind)
    ctx.runs = runs
    ctx.owner = "dn-h2"
    ctx.lib = H2Lib()
    return ctx


def target_unchanged(ctx: G.Ctx) -> dict[str, Any]:
    t = ctx.h_target
    if t is None:
        return {"checked": False}
    key = str(t["module_dir"])
    diff = G.diff_snaps(ctx.extra_snaps.get(key, {}), G.snapshot(Path(key)))
    return {"checked": True, "changed": diff[:20], "unchanged": not diff}


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    gated = [r for r in rows if r.get("gated")]
    s = {
        "gated_passed": [r["id"] for r in gated if r["pass"] is True],
        "gated_failed": [r["id"] for r in gated if r["pass"] is False],
        "not_run": [r["id"] for r in rows if r["pass"] is None],
        "reported": {r["id"]: r["pass"] for r in rows if not r.get("gated")},
    }
    print(f"gated passed {len(s['gated_passed'])}/{len(gated)}: {s['gated_passed']}\ngated failed: {s['gated_failed']}\nnot run: {s['not_run']}\nreported: {s['reported']}")
    return s


# ---- the command line ------------------------------------------------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kind", default="scored", choices=list(KINDS))
    ap.add_argument("--freeze", default=str(FREEZE))
    ap.add_argument("--inputs", default=str(INPUTS), help="the inputs manifest (H2's archive and truth)")
    ap.add_argument("--h2", default=str(H2_TGZ), help="H2: the archive of its tests/ tree (or <root>/tests/spec/<name>)")
    ap.add_argument("--h2-truth", default=str(H2_TRUTH))
    ap.add_argument("--out", default="")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--self-test", action="store_true", help="every row's code path on the stand-in H2 (= H1); RM1, RM2; never the ledger")
    ap.add_argument("--verify-freeze-only", action="store_true", help="verify the freeze file (and the inputs manifest if it exists) and exit")
    ap.add_argument("--write-freeze", default="", help="write the freeze file of prereg section 8 and exit")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    this = Path(__file__).resolve()
    if args.write_freeze:
        n = write_freeze(Path(args.write_freeze))
        print(f"{n} entries written to {args.write_freeze}")
        return 0
    if args.self_test:
        return self_test(args, this)
    freeze_path, inputs_path = Path(args.freeze).resolve(), Path(args.inputs).resolve()
    h2, h2_truth = Path(args.h2).resolve(), Path(args.h2_truth).resolve()
    if args.verify_freeze_only:
        try:
            f = verify_freeze(freeze_path, this)
            i = verify_inputs(inputs_path, [h2, h2_truth]) if inputs_path.is_file() else {"inputs": "absent (not checked)"}
        except G.FreezeError as err:
            print(err, file=sys.stderr)
            return 2
        print(json.dumps({"freeze": f, "inputs": i}, indent=1, default=str))
        return 0
    if args.runs != 10:
        raise SystemExit("a scored, rerun or verify run imports 10 times (H2-PI7, H2-DET): --runs is for the self-test only")
    if not args.out:
        raise SystemExit("--out is required")
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch-h2"
    # 1. the self-hash check and the inputs manifest, BEFORE any ledger line (prereg 6.4, 8)
    try:
        lattice_file = G.lattice_file_inside()
        freeze = verify_freeze(freeze_path, this)
        inputs = verify_inputs(inputs_path, [h2, h2_truth])
    except (G.FreezeError, SystemExit) as err:
        log_refusal(str(err), runner=RUNNER_NAME, runner_sha256=G.sha(this), kind=args.kind, arm=ARM)
        print(f"refused before any metric and before any ledger line: {err}", file=sys.stderr)
        return 2
    runner_sha, freeze_sha, inputs_sha = G.sha(this), G.sha(freeze_path), G.sha(inputs_path)
    # 2. the ledger
    why = G.ledger_refusal(G.read_ledger(LEDGER), ARM, args.kind, runner_sha, freeze_sha)
    if why:
        log_refusal(f"the ledger refuses: {why}", runner=RUNNER_NAME, runner_sha256=runner_sha, kind=args.kind, arm=ARM)
        raise SystemExit(f"the ledger refuses {ARM} {args.kind}: {why}")
    run_id = uuid.uuid4().hex[:12]

    def entry(status: str, **extra: Any) -> dict[str, Any]:
        return {"ts": G.now(), "run_id": run_id, "runner": RUNNER_NAME, "runner_sha256": runner_sha, "freeze_sha256": freeze_sha, "inputs_sha256": inputs_sha, "arm": ARM, "kind": args.kind, "out": str(out), "lattice_file": lattice_file, "dotnet_sdk": G.sdk_or_none(), "status": status, **extra}  # fmt: skip

    G.append_ledger(entry("started"), LEDGER)
    metric_started = False
    try:
        print(f"gated rows ({len(GATED)}): {GATED}\nreported rows: {[i for i in ORDER if i not in GATED]}", flush=True)
        ctx = new_ctx(scratch, out, args.kind, 10)
        setup(ctx, h2, h2_truth)
        freeze["import_subprocess_lattice_file"] = G.lattice_subprocess_inside(ctx)
        metric_started = True
        rows = run_rows(ctx, ORDER)
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        G.append_ledger(entry("crash_after_metric" if metric_started else "crash_before_metric", error=repr(err)[:400]), LEDGER)
        raise
    doc = {"runner": RUNNER_NAME, "runner_sha256": runner_sha, "kind": args.kind, "run_id": run_id, "arm": ARM, "lattice_file": lattice_file, "freeze": freeze, "inputs": inputs, "h2": str(h2), "h2_truth": str(h2_truth), "timings": ctx.timings, "shim_calls": G.shim_calls(ctx), "target_unchanged": target_unchanged(ctx), "r_kwargs": ctx.lib.last_r_kwargs, "gated": GATED, "results": rows}  # type: ignore[attr-defined]  # fmt: skip
    doc["summary"] = summarise(rows)
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes-h2.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    (out / "evidence").mkdir(exist_ok=True)
    for r in rows:
        (out / "evidence" / f"{r['id']}.json").write_text(json.dumps(r, indent=2, default=str) + "\n")
    G.append_ledger(entry("completed"), LEDGER)
    print(f"ledger sha256: {G.sha(LEDGER)}")
    return 0


# ---- the self-test: every row on the stand-in H2 (= H1, dev data), RM1, RM2 ---------------------------------------------------------------------------


def selftest_rm1(ctx: G.Ctx) -> dict[str, Any]:
    """RM1: (a) NOT RUN is built only in :func:`one_row` (AST); (b) fake NOT RUN rows with every prerequisite present are caught; (c) live: a
    row function hard-coded to answer NOT RUN, run through :func:`one_row`, is caught by :func:`self_test_problems`."""
    tree = ast.parse(Path(__file__).read_text())
    calls = sorted({f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name != "selftest_rm1" for n in ast.walk(f) if isinstance(n, ast.Call) and (getattr(n.func, "id", "") == "not_run" or getattr(n.func, "attr", "") == "not_run")})
    present = [i for i in ROWS if missing(ctx, i) is None]
    fake = [{"id": i, "pass": None} for i in present[:6]]
    caught_fake = len(self_test_problems(fake, ctx)) == len(fake) and bool(fake)
    pid = present[0] if present else "H2-ROUTES"
    keep = ROWS[pid]
    ROWS[pid] = Row(keep.gated, keep.prediction, lambda c: G.not_run(pid, "hard-coded", "hard-coded NOT RUN (RM1 live)"), keep.h1, keep.needs)
    try:
        live = one_row(ctx, pid)
    finally:
        ROWS[pid] = keep
    caught_live = bool(self_test_problems([live], ctx))
    return {"caught": caught_fake and caught_live and calls == ["one_row"], "not_run_built_in": calls, "fake_rows": len(fake), "fake_caught": caught_fake, "live_row": pid, "live_caught": caught_live}


def selftest_rm2(this: Path, this_rel: str = RUNNER_REL) -> dict[str, Any]:
    """RM2: the self-hash check must refuse a list that drops the running script, the H1 runner, a library file, the prereg or the brief; an
    edited hash; a listed file that does not exist; and an inputs manifest that does not list the H2 archive."""
    tmp = Path(tempfile.mkdtemp(prefix="h2-rm2-"))
    entries = [(rel, G.sha(REPO / rel)) for rel in must_list() if (REPO / rel).is_file()]
    checks: dict[str, str] = {}

    def attempt(name: str, lines: list[tuple[str, str]], inputs: bool = False) -> None:
        fp = tmp / f"{uuid.uuid4().hex}.sha256"
        fp.write_text("".join(f"{h}  {rel}\n" for rel, h in lines))
        try:
            if inputs:
                verify_inputs(fp, [STANDIN_TGZ, STANDIN_TRUTH])
            else:
                verify_freeze(fp, this)
            checks[name] = "NOT caught"
        except G.FreezeError as err:
            checks[name] = f"caught: {str(err)[:80]}"

    attempt("control", entries)
    ctl = checks.pop("control")
    for name, drop in (("drops the running script", this_rel), ("drops the H1 runner", G.RUNNER_REL), ("drops a library file", "lattice/importer/dotnet_rules.py"), ("drops an unchanged library file", "lattice/spec/process.py"), ("drops the prereg", PREREG_REL), ("drops the brief", f"{DATA2_REL}/brief/BRIEF.md")):  # fmt: skip
        attempt(name, [(r, h) for r, h in entries if r != drop])
    attempt("one edited hash", [(r, "0" * 64 if r == "lattice/importer/stable.py" else h) for r, h in entries])
    attempt("a listed file that does not exist", [*entries, ("docs/no/such/file", "0" * 64)])
    attempt("inputs manifest without the archive", [(str(STANDIN_TRUTH), G.sha(STANDIN_TRUTH))], inputs=True)
    shutil.rmtree(tmp, ignore_errors=True)
    control_ok = ctl == "NOT caught"
    return {"control_passes": control_ok, "control": ctl, "checks": checks, "caught": control_ok and all(v.startswith("caught") for v in checks.values())}


def self_test(args: argparse.Namespace, this: Path) -> int:
    out = Path(args.out or tempfile.mkdtemp(prefix="h2-selftest-")).resolve()
    if "docs/results/data" in out.as_posix():
        raise SystemExit("--self-test writes to a scratch directory, not to the results data")
    scratch = Path(args.scratch).resolve() if args.scratch else out / "scratch"
    ctx = new_ctx(scratch, out, "shakedown", min(args.runs, 2))
    ctx.self_test = True
    led0 = G.sha(LEDGER) if LEDGER.is_file() else None
    setup(ctx, STANDIN_TGZ, STANDIN_TRUTH, label="S-H1")
    rows = run_rows(ctx, ORDER)
    rm1, rm2 = selftest_rm1(ctx), selftest_rm2(this)
    problems = self_test_problems(rows, ctx)
    problems += [f"{r['id']}: the row raised: {r['observed']}" for r in rows if "traceback" in r.get("evidence", {})]
    problems += [f"{i}: no row was produced" for i in ORDER if i not in {r["id"] for r in rows}]
    if not ctx.dotnet:
        problems.append("no .NET SDK: the self-test cannot run the rows' code paths")
    lk = ctx.lib.last_r_kwargs  # type: ignore[attr-defined]
    if not lk or lk.get("relogin") is not True or lk.get("crash_admits") is not True:
        problems.append(f"layer R did not pass relogin=True and crash_admits=True on the ASP.NET Core stand-in: {lk}")
    if not rm1["caught"]:
        problems.append(f"RM1 not caught: {rm1}")
    if not rm2["caught"]:
        problems.append(f"RM2 not caught: {rm2}")
    unchanged = target_unchanged(ctx)
    if not unchanged.get("unchanged"):
        problems.append(f"the stand-in target changed: {unchanged}")
    if (G.sha(LEDGER) if LEDGER.is_file() else None) != led0:
        problems.append("the self-test touched the ledger")
    doc = {"rows": rows, "problems": problems, "RM1": rm1, "RM2": rm2, "r_kwargs": lk, "target_unchanged": unchanged, "shim_calls": G.shim_calls(ctx), "timings": ctx.timings, "runner_sha256": G.sha(this)}
    doc["summary"] = summarise(rows)
    out.mkdir(parents=True, exist_ok=True)
    (out / "selftest-h2.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print("RM1:", rm1["caught"], "RM2:", rm2["caught"], "layer R kwargs:", lk)
    print("SELF-TEST", "FAILED: " + "; ".join(problems) if problems else "OK: every H2 row's code path ran on the stand-in; no improper NOT RUN; RM1 and RM2 caught; layer R passes relogin and crash_admits")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
