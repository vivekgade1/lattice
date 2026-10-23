"""Held-out mutants HM1-16 of the blind .NET round H2 (docs/results/2026-10-23-dotnet-h2-prereg.md, section 4.1 and sections 6-10).

The operators, detectors, the strict control and the gates are the frozen H1 mutants runner's (``dotnet_mutants``, imported, never edited;
H1 prereg 6.2): >= 13 of 16 killed and the class gates HM1-4 >= 3, HM5-6 2/2, HM7-8 2/2, HM9-10 2/2. What differs from the H1 runner:

* layer R goes through ``dotnet_h2_probes.H2Lib``: ``relogin`` and ``crash_admits`` are passed as ``lattice check`` and the import pass them;
* a ``NONE`` line of MUTANTS.md (W3 found no instance of the operator) is read and counts as NOT killed (the H1 runner refused the input);
* reported beside the gate, not gated: layer A under ``DnPolicy.from_spec`` of the candidate spec (the policy ``lattice check`` builds) and
  whether the pipeline reports the mutant at all (a new layer A finding under that policy, or a layer R item verified on the control and
  refuted on the mutant);
* the freeze file and the inputs manifest are verified before any ledger line (a refusal goes to ``refused.jsonl``).

    "$PY" dogfood/dotnet_h2_mutants.py --kind scored --out docs/results/data/2026-10-23-dotnet-h2/hmut --scratch "$SCRATCH"
    "$PY" dogfood/dotnet_h2_mutants.py --check-inputs [--patches DIR --h2-truth FILE]     # parses MUTANTS.md and the names; runs nothing
    "$PY" dogfood/dotnet_h2_mutants.py --self-test [--out DIR]       # stand-ins: H1 and its 16 real patches; never the ledger
    "$PY" dogfood/dotnet_h2_mutants.py --verify-freeze-only

Defaults: ``--patches docs/results/data/2026-10-23-dotnet-h2/h2-mutants-run`` (``MUTANTS.md``, ``m01.diff`` .. ``m16.diff``), and the probes
runner's ``--freeze``, ``--inputs``, ``--h2``, ``--h2-truth``.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dotnet_h2_probes as P2  # noqa: E402
import dotnet_mutants as M  # noqa: E402  -- the frozen H1 mutants runner: operators, detectors, control, gates
import dotnet_probes as G  # noqa: E402

REPO = G.REPO
RUNNER_NAME = "dotnet_h2_mutants"
ARM = "H2:HM"
IDS = M.IDS
NONE_RE = re.compile(r"^[\s|*\->`#.]*(?:HM|M)?0*(\d{1,2})\s*\|\s*([^|]*)\|\s*`?NONE`?\s*(?:\||$)", re.I)


# ---- MUTANTS.md with NONE lines ----------------------------------------------------------------------------------------------------------------


def read_mutants(patches: Path, truth_path: Path) -> tuple[dict[str, Any], dict[int, M.MutRow], dict[int, dict[str, str]], dict[int, Path]]:
    """Before any metric: MUTANTS.md parses; every id 1-16 has exactly one line, either a route line (``HMn | <operator> | METHOD /path |
    file:line | substitutions``, the route in the truth, the operator registered, ``mNN.diff`` present) or a NONE line (``HMn | <operator> |
    NONE | reason``, the operator registered). NONE lines are removed before the H1 parser reads the rest (a reason naming a route is not a route)."""
    problems: list[str] = []
    truth = json.loads(truth_path.read_text())
    md = patches / "MUTANTS.md"
    if not md.is_file():
        raise SystemExit(f"input check failed: {md} does not exist")
    none: dict[int, dict[str, str]] = {}
    kept = []
    for ln in md.read_text().splitlines():
        m = NONE_RE.match(ln)
        if m and 1 <= int(m.group(1)) <= 16:
            n = int(m.group(1))
            if n in none:
                problems.append(f"HM{n}: two NONE lines")
            none[n] = {"line": ln.strip(), "operator_cell": m.group(2).strip(" `*")}
            if M.classify_operator(none[n]["operator_cell"]) is None:
                problems.append(f"HM{n}: the NONE line's operator cell {none[n]['operator_cell']!r} names no registered operator")
            continue
        kept.append(ln)
    try:
        rows = M.parse_mutants_md("\n".join(kept))
    except ValueError as err:
        raise SystemExit(f"input check failed: {err}") from err
    problems += [f"HM{i}: both a route line and a NONE line" for i in set(rows) & set(none)]
    problems += [f"MUTANTS.md has no line for HM{i}" for i in IDS if i not in rows and i not in none]
    routes = G.truth_route_map(truth) if "routes" in truth else {}
    for i, r in rows.items():
        if G.rkey(r.method, r.path) not in routes:
            problems.append(f"HM{i}: {r.method} {r.path} is not a route of truth.json")
        if r.operator_cell is not None and M.classify_operator(r.operator_cell) is None:
            problems.append(f"HM{i}: the operator cell {r.operator_cell!r} names no registered operator (keys {sorted(M.OPERATORS)})")
    files = {i: patches / M.patch_name(i) for i in IDS if i in rows}
    problems += [f"{p} does not exist" for p in files.values() if not p.is_file()]
    if problems:
        raise SystemExit("input check failed: " + "; ".join(problems))
    return truth, rows, none, files


def none_rows(none: dict[int, dict[str, str]]) -> list[dict[str, Any]]:
    return [{"id": f"HM{i}", "status": "no_instance", "killed": False, "killed_by": "NONE: W3 found no instance of the operator (counts as not killed, prereg 4.1)", "mutants_md_line": v["line"], "operator": v["operator_cell"]} for i, v in sorted(none.items())]


def final_gate(mrows: list[dict[str, Any]]) -> dict[str, Any]:
    """The H1 gate (``dotnet_mutants.gate_report``) over all 16 ids; a NONE row is killed False."""
    killed = {int(r["id"][2:]): r.get("killed") for r in mrows}
    return M.gate_report(killed)


def control_failed_gate() -> dict[str, Any]:
    """The control does not pass (prereg 4.1): no mutant is judged and the gate is FAIL (every mutant counted not killed)."""
    return {**M.gate_report({i: False for i in IDS}), "control_failed": True}


# ---- one run: the control, then the mutants (the H1 runner's run_hmut with NONE rows and the reported from_spec view) ---------------------------------


def run_hmut2(ctx: G.Ctx, tgt: dict[str, Any], patches: Path, truth: dict[str, Any], rows: dict[int, M.MutRow], none: dict[int, dict[str, str]], files: dict[int, Path], out: Path) -> dict[str, Any]:
    t_start = time.perf_counter()
    src = Path(tgt["module_dir"])
    ev = out / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {"h2": str(src), "h2_tree_sha256": G.tree_sha256(src), "mutants_md_sha256": G.sha(patches / "MUTANTS.md"), "truth_sha256": G.sha(Path(tgt["truth_path"])), "patch_sha256": {M.patch_name(i): G.sha(f) for i, f in files.items()}, "none": sorted(f"HM{i}" for i in none)}  # fmt: skip
    nrows = none_rows(none)
    if not ctx.dotnet:
        doc.update(control={"passes": None, "not_run": "no .NET SDK on PATH"}, rows=nrows, gate=M.gate_report({}), not_run="no .NET SDK on PATH")
        return doc
    ctl_dir = ctx.scratch / "mut" / "control" / src.name
    G.copy_module(src, ctl_dir)
    copy_hash = G.tree_sha256(ctl_dir)
    cbin = ctx.scratch / "mut" / "control" / "out"
    ok, err = G.build_copy(ctx, tgt, ctl_dir, cbin)
    if not ok:
        doc.update(control={"passes": False, "build_error": err[-600:]}, rows=nrows, gate=control_failed_gate(), judged=False)
        return doc
    cand_dir = G.import_dir(ctx, tgt)
    cand = G.load_candidate(cand_dir)
    a_control = ctx.lib.layer_a(ctx, src, "spec_policy", spec=cand)["findings"]
    a_control_fs = ctx.lib.layer_a(ctx, src, "from_spec", spec=cand)["findings"]
    pristine = ctx.lib.layer_a(ctx, ctl_dir, "spec_policy", spec=cand)["findings"]
    with G.env_set(tgt.get("build_env"), cbin):
        r1 = ctx.lib.r_verdicts(ctx, tgt, cand_dir, "seed")
        r2 = ctx.lib.r_verdicts(ctx, tgt, cand_dir, "seed")
    injection, why_inj = M.binary_injected(ctx, tgt, cbin)
    unstable = sorted(i for i in {*r1, *r2} if r1.get(i) != r2.get(i))
    plans = {i: {"num": i, "route": G.rkey(rows[i].method, rows[i].path), "operator": M.operator_of(i, rows[i].operator_cell)} for i in sorted(rows)}
    registered: list[str] = []
    for p in plans.values():
        for det in M.OPERATORS[p["operator"]["key"]].detectors:
            if det.layer == "R":
                sts = M.refusal_statuses(truth, p["route"], det.cause)[0] if det.code == "R3" and det.cause else []
                registered += G.r_items(list(r1), det.code, p["route"], sts)
    control = M.judge_control([p["route"] for p in plans.values()], registered, a_control=a_control, r_control=r1, pristine_new=G.new_findings(a_control, pristine), copy_equals_h1=copy_hash == doc["h2_tree_sha256"], injection_works=injection)  # fmt: skip
    control.update({"layer_a_original_findings": [f["key"] for f in a_control], "layer_a_original_findings_from_spec (reported)": [f["key"] for f in a_control_fs], "r_items": len(r1), "r_verified": sum(1 for v in r1.values() if v in G.VERIFIED), "r_items_unstable_between_two_runs": unstable, "binary_injection": why_inj, "r_kwargs": getattr(ctx.lib, "last_r_kwargs", None), "operators": {f"HM{i}": plans[i]["operator"] for i in plans}})  # fmt: skip
    doc["control"] = control
    (ev / "control.json").write_text(json.dumps(control, indent=2, default=str) + "\n")
    mrows: list[dict[str, Any]] = list(nrows)
    if not control["passes"]:
        doc.update(rows=mrows, gate=control_failed_gate(), judged=False, reading="the control does not pass: no mutant is judged (gate FAIL)")
        doc["wall_s"] = round(time.perf_counter() - t_start, 1)
        return doc
    for i in sorted(plans):
        p, row = plans[i], rows[i]
        rec: dict[str, Any] = {"id": f"HM{i}", "patch": M.patch_name(i), "patch_sha256": doc["patch_sha256"][M.patch_name(i)], "mutants_md_line": row.line, "operator": p["operator"], "route": G.rlabel(p["route"]), "file_line": [row.file, row.file_line], "substitutions": row.substitutions}  # fmt: skip
        t0 = time.perf_counter()
        try:
            rec.update(judge_one2(ctx, tgt, truth, i, p, files[i], cand, cand_dir, a_control, a_control_fs, r1, unstable, row.line + "\n" + M.changed_lines(files[i])))
        except Exception as err:  # noqa: BLE001 -- a mutant whose code raised is a FAIL, never NOT RUN
            rec.update(killed=False, status="crashed", killed_by=f"FAIL: {type(err).__name__}: {err}"[:400], traceback=traceback.format_exc()[-1500:])
        rec["wall_s"] = round(time.perf_counter() - t0, 1)
        (ev / f"hm{i:02d}.json").write_text(json.dumps(rec, indent=2, default=str) + "\n")
        print(f"HM{i}: killed={rec.get('killed')} pipeline={rec.get('pipeline_reports_it (reported)')} {str(rec.get('killed_by', ''))[:120]}", flush=True)
        mrows.append(rec)
    mrows.sort(key=lambda r: int(r["id"][2:]))
    doc.update(rows=mrows, judged=True, gate=final_gate(mrows))
    doc["pipeline (reported)"] = {"reports_it": sum(1 for r in mrows if r.get("pipeline_reports_it (reported)")), "of": 16, "rows": {r["id"]: r.get("pipeline_reports_it (reported)") for r in mrows}}
    doc["wall_s"] = round(time.perf_counter() - t_start, 1)
    return doc


def judge_one2(ctx: G.Ctx, tgt: dict[str, Any], truth: dict[str, Any], num: int, plan: dict[str, Any], patch: Path, cand: Any, cand_dir: Path, a_control: list[dict[str, Any]], a_control_fs: list[dict[str, Any]], r_control: dict[str, str], unstable: list[str], text: str) -> dict[str, Any]:
    """Apply, build and judge one mutant on a scratch copy of H2 (the H1 runner's ``judge_one``) plus the reported ``from_spec`` view."""
    src = Path(tgt["module_dir"])
    copy = ctx.scratch / "mut" / f"hm{num:02d}" / src.name
    G.copy_module(src, copy)
    ap = G.apply_patch(copy, patch)
    if not ap["ok"]:
        return {"killed": False, "status": "patch_failed", "killed_by": "FAIL: the patch does not apply: " + ap["output"][-200:], "apply": ap}
    outb = copy.parent / "out"
    ok, err = G.build_copy(ctx, tgt, copy, outb)
    if not ok:
        return {"killed": False, "status": "build_failed", "killed_by": "FAIL: the mutant does not build: " + err[-300:], "apply": ap}
    new = G.new_findings(a_control, ctx.lib.layer_a(ctx, copy, "spec_policy", spec=cand)["findings"])
    new_fs = G.new_findings(a_control_fs, ctx.lib.layer_a(ctx, copy, "from_spec", spec=cand)["findings"])
    with G.env_set(tgt.get("build_env"), outb):
        r_mut = ctx.lib.r_verdicts(ctx, tgt, cand_dir, "seed")
    v = M.judge_mutant(num, plan["operator"]["key"], plan["route"], a_control=a_control, a_new=new, r_control=r_control, r_mutant=r_mut, unstable=unstable, truth=truth, text=text)
    flips = {i: [r_control.get(i), x] for i, x in r_mut.items() if r_control.get(i) != x}
    refuted = sorted(i for i, (a, b) in flips.items() if b == "refuted" and a in G.VERIFIED)
    return {**v, "status": "judged", "apply": ap, "layer_a": {"new_findings": [f"{x['code']} {x['key']}" for x in new]}, "layer_a_from_spec (reported)": {"new_findings": [f"{x['code']} {x['key']}" for x in new_fs]}, "layer_r": {"ran": True, "flipped_items [control, mutant]": flips, "verified_then_refuted": refuted}, "pipeline_reports_it (reported)": bool(new_fs or refuted)}  # fmt: skip


# ---- the command line ------------------------------------------------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kind", default="scored", choices=list(P2.KINDS))
    ap.add_argument("--freeze", default=str(P2.FREEZE))
    ap.add_argument("--inputs", default=str(P2.INPUTS))
    ap.add_argument("--h2", default=str(P2.H2_TGZ))
    ap.add_argument("--h2-truth", default=str(P2.H2_TRUTH))
    ap.add_argument("--patches", default=str(P2.H2_PATCHES), help="MUTANTS.md and m01.diff .. m16.diff")
    ap.add_argument("--out", default="")
    ap.add_argument("--scratch", default="")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--check-inputs", action="store_true", help="parse MUTANTS.md and the patch names against the truth; run nothing")
    ap.add_argument("--verify-freeze-only", action="store_true")
    return ap


def required_inputs(h2: Path, h2_truth: Path, patches: Path) -> list[Path]:
    return [h2, h2_truth, patches / "MUTANTS.md", *[p for p in (patches / M.patch_name(i) for i in IDS) if p.is_file()]]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    this = Path(__file__).resolve()
    if args.self_test:
        return self_test(args, this)
    h2, h2_truth, patches = Path(args.h2).resolve(), Path(args.h2_truth).resolve(), Path(args.patches).resolve()
    freeze_path, inputs_path = Path(args.freeze).resolve(), Path(args.inputs).resolve()
    if args.check_inputs:
        _, rows, none, files = read_mutants(patches, h2_truth)
        print(json.dumps({"route_lines": sorted(f"HM{i}" for i in rows), "none_lines": sorted(f"HM{i}" for i in none), "patches": {f"HM{i}": f.name for i, f in files.items()}, "operators": {f"HM{i}": M.operator_of(i, rows[i].operator_cell)["key"] for i in rows}}, indent=1))
        return 0
    if args.verify_freeze_only:
        try:
            f = P2.verify_freeze(freeze_path, this)
            i = P2.verify_inputs(inputs_path, required_inputs(h2, h2_truth, patches)) if inputs_path.is_file() else {"inputs": "absent (not checked)"}
        except G.FreezeError as err:
            print(err, file=sys.stderr)
            return 2
        print(json.dumps({"freeze": f, "inputs": i}, indent=1, default=str))
        return 0
    if not args.out:
        raise SystemExit("--out is required")
    out = Path(args.out).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else out.parent / "scratch-h2-mut"
    try:  # before any ledger line: the library inside the tree, the freeze, the inputs manifest, MUTANTS.md
        lattice_file = G.lattice_file_inside()
        freeze = P2.verify_freeze(freeze_path, this)
        inputs = P2.verify_inputs(inputs_path, required_inputs(h2, h2_truth, patches))
        truth, rows, none, files = read_mutants(patches, h2_truth)
    except (G.FreezeError, SystemExit) as err:
        P2.log_refusal(str(err), runner=RUNNER_NAME, runner_sha256=G.sha(this), kind=args.kind, arm=ARM)
        print(f"refused before any metric and before any ledger line: {err}", file=sys.stderr)
        return 2
    runner_sha, freeze_sha, inputs_sha = G.sha(this), G.sha(freeze_path), G.sha(inputs_path)
    why = G.ledger_refusal(G.read_ledger(P2.LEDGER), ARM, args.kind, runner_sha, freeze_sha)
    if why:
        P2.log_refusal(f"the ledger refuses: {why}", runner=RUNNER_NAME, runner_sha256=runner_sha, kind=args.kind, arm=ARM)
        raise SystemExit(f"the ledger refuses {ARM} {args.kind}: {why}")
    run_id = uuid.uuid4().hex[:12]

    def entry(status: str, **extra: Any) -> dict[str, Any]:
        return {"ts": G.now(), "run_id": run_id, "runner": RUNNER_NAME, "runner_sha256": runner_sha, "freeze_sha256": freeze_sha, "inputs_sha256": inputs_sha, "arm": ARM, "kind": args.kind, "out": str(out), "lattice_file": lattice_file, "dotnet_sdk": G.sdk_or_none(), "status": status, **extra}  # fmt: skip

    G.append_ledger(entry("started"), P2.LEDGER)
    metric_started = False
    try:
        ctx = P2.new_ctx(scratch, out, args.kind, 1)
        P2.setup(ctx, h2, h2_truth)
        freeze["import_subprocess_lattice_file"] = G.lattice_subprocess_inside(ctx)
        metric_started = True
        hm = run_hmut2(ctx, ctx.h_target, patches, truth, rows, none, files, out)  # type: ignore[arg-type]
    except BaseException as err:  # noqa: BLE001 -- recorded in the ledger, then re-raised
        G.append_ledger(entry("crash_after_metric" if metric_started else "crash_before_metric", error=repr(err)[:400]), P2.LEDGER)
        raise
    doc = {"runner": RUNNER_NAME, "runner_sha256": runner_sha, "kind": args.kind, "run_id": run_id, "arm": ARM, "lattice_file": lattice_file, "freeze": freeze, "inputs": inputs, "shim_calls": G.shim_calls(ctx), "target_unchanged": P2.target_unchanged(ctx), "HM": hm}  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "mutants-h2.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    G.append_ledger(entry("completed"), P2.LEDGER)
    print(json.dumps(hm.get("gate", {}), default=str))
    print(f"ledger sha256: {G.sha(P2.LEDGER)}")
    return 0


# ---- the self-test: the control and 16 mutants on the stand-in (H1 and its real patches), the NONE reader, RM2 ------------------------------------------


def selftest_none() -> dict[str, Any]:
    """A MUTANTS.md whose HM16 line is NONE (its reason naming a route) and whose HM5 line is NONE: read, not judged, counted not killed."""
    tmp = Path(tempfile.mkdtemp(prefix="h2-none-"))
    try:
        lines = [ln for ln in (P2.STANDIN_PATCHES / "MUTANTS.md").read_text().splitlines() if not re.match(r"^\s*HM(5|16)\b", ln)]
        lines += ["HM5 | expose_column | NONE | no route answers a row with a never-returned column", "HM16 | widen_list | NONE | the only list, GET /reports, is admin-only"]
        (tmp / "MUTANTS.md").write_text("\n".join(lines) + "\n")
        for i in IDS:
            if i not in (5, 16):
                shutil.copyfile(P2.STANDIN_PATCHES / M.patch_name(i), tmp / M.patch_name(i))
        _, rows, none, files = read_mutants(tmp, P2.STANDIN_TRUTH)
        fake = [*none_rows(none), *[{"id": f"HM{i}", "killed": True} for i in rows]]
        gate = final_gate(fake)
        ok = sorted(none) == [5, 16] and 5 not in files and 16 not in files and len(rows) == 14 and gate["killed"] == 14 and gate["classes"]["HM5-6"]["killed"] == 1 and gate["verdict"] == "fail" and "HM16" in gate["survivors"]
        bad = tmp / "bad"
        bad.mkdir()
        (bad / "MUTANTS.md").write_text("HM1 | not_an_operator | NONE | x\n")
        try:
            read_mutants(bad, P2.STANDIN_TRUTH)
            refused_bad = False
        except SystemExit:
            refused_bad = True
        return {"caught": ok and refused_bad, "none": sorted(none), "judged": sorted(rows), "gate": gate, "unregistered_operator_refused": refused_bad}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def self_test(args: argparse.Namespace, this: Path) -> int:
    out = Path(args.out or tempfile.mkdtemp(prefix="h2m-selftest-")).resolve()
    if "docs/results/data" in out.as_posix():
        raise SystemExit("--self-test writes to a scratch directory, not to the results data")
    scratch = Path(args.scratch).resolve() if args.scratch else out / "scratch"
    led0 = G.sha(P2.LEDGER) if P2.LEDGER.is_file() else None
    problems: list[str] = []
    nt = selftest_none()
    if not nt["caught"]:
        problems.append(f"the NONE reader: {nt}")
    rm2 = P2.selftest_rm2(this, "dogfood/dotnet_h2_mutants.py")
    if not rm2["caught"]:
        problems.append(f"RM2 not caught: {rm2}")
    ctx = P2.new_ctx(scratch, out, "shakedown", 1)
    ctx.self_test = True
    P2.setup(ctx, P2.STANDIN_TGZ, P2.STANDIN_TRUTH, label="S-H1")
    if not ctx.dotnet:
        problems.append("no .NET SDK: the self-test cannot run the mutants' code paths")
    truth, rows, none, files = read_mutants(P2.STANDIN_PATCHES, P2.STANDIN_TRUTH)
    hm = run_hmut2(ctx, ctx.h_target, P2.STANDIN_PATCHES, truth, rows, none, files, out)  # type: ignore[arg-type]
    if not hm.get("control", {}).get("passes"):
        problems.append(f"HM control did not pass on the stand-in: {json.dumps(hm.get('control'), default=str)[:600]}")
    judged = [r for r in hm.get("rows", ()) if r.get("status") == "judged"]
    if len(judged) != 16:
        problems.append(f"HM: {len(judged)} of 16 mutants judged ({[(r['id'], r.get('status')) for r in hm.get('rows', ()) if r.get('status') != 'judged']})")
    problems += [f"{r['id']}: NOT RUN although layer R was fed the mutant's binary" for r in judged if r.get("killed") is None]
    problems += [f"{r['id']}: {r.get('status')}: {r.get('killed_by')}" for r in hm.get("rows", ()) if r.get("status") in ("crashed", "patch_failed", "build_failed")]
    lk = ctx.lib.last_r_kwargs  # type: ignore[attr-defined]
    if not lk or lk.get("relogin") is not True or lk.get("crash_admits") is not True:
        problems.append(f"layer R did not pass relogin=True and crash_admits=True on the ASP.NET Core stand-in: {lk}")
    if "pipeline (reported)" not in hm and hm.get("judged"):
        problems.append("the reported from_spec view was not computed")
    if (G.sha(P2.LEDGER) if P2.LEDGER.is_file() else None) != led0:
        problems.append("the self-test touched the ledger")
    unchanged = P2.target_unchanged(ctx)
    if not unchanged.get("unchanged"):
        problems.append(f"the stand-in target changed: {unchanged}")
    (out / "selftest-h2-mutants.json").write_text(json.dumps({"HM": hm, "NONE": nt, "RM2": rm2, "r_kwargs": lk, "problems": problems, "runner_sha256": G.sha(this)}, indent=2, default=str) + "\n")
    print("HM gate on the stand-in (H1's own patches; dev data):", json.dumps(hm.get("gate", {}), default=str)[:500])
    print("pipeline (reported):", json.dumps(hm.get("pipeline (reported)", {}), default=str)[:300])
    print("NONE reader:", nt["caught"], "RM2:", rm2["caught"], "layer R kwargs:", lk)
    print("SELF-TEST", "FAILED: " + "; ".join(problems) if problems else "OK: control and 16 mutants judged on the stand-in; NONE read; RM2 caught; layer R passes relogin and crash_admits")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
