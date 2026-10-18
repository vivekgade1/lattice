"""Mutants JM31-JM34 of the Go parity gaps, round 2 (docs/results/2026-10-18-go-parity-gaps-round2-prereg.md,
section 6, Amendment 5; Spike J, node J2): edits to scratch copies of ``tests/spec/gotickets``. The frozen
mutants runner (``go_parity_mutants2.py``) reports them NOT RUN; this file does not edit it.

* JM31: ``POST /tickets/:tid/close`` drops ``AND reporter_id = ?``. Detector: PA3-3 (layer A under
  ``go_spec_policy`` of S3's candidate spec finds ``GO801|POST /tickets/:tid/close|Ticket``). Layer R's
  ``R2 POST /tickets/{tid}/close`` refuted is reported.
* JM32: ``password_hash`` loses ``json:"-"``. Detector: PA3-3 (``GO802|GET /me|User.password_hash``).
  Layer R's ``T2 User.password_hash`` refuted is reported.
* JM33: ``GET /tickets/:tid`` answers 403 for another caller's ticket. Detector: layer R on S3, the 404
  item of that route (verified on the control) is refuted.
* JM34: ``POST /tickets/:tid/reopen`` accepts an open ticket (no 409). Detector: layer R on S3, the 409 item
  of that route (verified on the control) is refuted.

The control (the fixture copied and built unchanged, then judged by the same detectors) runs first; if it
does not pass, no mutant is judged. The edits are located structurally in the Go source (the handler
registered for the route; the ``Where`` clause; the status; the ``if`` before the 409), because the
builder never opened the fixture. A mutant whose edit does not apply exactly once is NOT RUN with the
reason, never guessed; ``--edits FILE`` (JSON ``{"JM31": [{"file", "old", "new"}]}``) supplies literal
edits instead, and its hash is recorded.

    "$PY" dogfood/go_parity_s3_mutants.py --base "$SCRATCH/base" --scratch "$SCRATCH/m3" \\
        --out docs/results/data/2026-10-18-go-parity-gaps-round2/mutants-s3 JM31 JM32 JM33 JM34

The Amendment 3 freeze and ``s3runner.sha256`` are verified first and cannot be skipped.
``--fixture`` / ``--harness`` are the stand-in override of ``go_parity_s3.py``.
"""  # noqa: E501

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_probes as P1  # noqa: E402
import go_parity_s3 as S  # noqa: E402

VERIFIED = P1.VERIFIED
IDS = list(S.MUTANTS)

# the R item each mutant is judged by: (a predicate on the item id, why)
R_ITEM = {
    "JM31": (lambda i: i == "R2 POST /tickets/{tid}/close", "R2 POST /tickets/{tid}/close"),
    "JM32": (lambda i: i == "T2 User.password_hash", "T2 User.password_hash"),
    "JM33": (lambda i: "GET /tickets/{tid}" in i and "404" in i,
             "the 404 item of GET /tickets/{tid}"),
    "JM34": (lambda i: "POST /tickets/{tid}/reopen" in i and "409" in i,
             "the 409 item of POST /tickets/{tid}/reopen"),
}  # fmt: skip


def judge_r(mid: str, control: dict[str, str], mutant: dict[str, str]) -> dict[str, Any]:
    """The R item(s) of ``mid``: refuted on the mutant after being verified on the control."""
    pred, why = R_ITEM[mid]
    items = sorted(i for i in {*control, *mutant} if pred(i))
    rows = {i: [control.get(i), mutant.get(i)] for i in items}
    hit = [i for i, (c, m) in rows.items() if c in VERIFIED and m == "refuted"]
    return {"item": why, "items [control, mutant]": rows, "refuted_after_verified": hit,
            "killed": bool(hit)}  # fmt: skip


def judge_a(mid: str, new: list[dict[str, str]], outside: list[str]) -> dict[str, Any]:
    want = S.PA3_EXPECT[mid]
    hit = any((x["code"], x["key"]) == want for x in new)
    return {"expected": list(want), "found": hit, "original_outside_baseline": outside,
            "new_findings": [f"{x['code']} {x['key']}" for x in new],
            "killed": hit and not outside}  # fmt: skip


def build_copy(ctx: S.Ctx, copy: Path, tag: str) -> tuple[Path | None, str]:
    binary = ctx.scratch / "mut" / tag / "fxbin"
    ok, err, _ = P1.go_build(copy, binary)
    return (binary if ok else None), err


def r_map(ctx: S.Ctx, cand: Any, binary: Path) -> dict[str, str]:
    """Layer R of the candidate spec on the app ``binary`` (the harness' variable, then restored)."""
    keep = os.environ.get(ctx.fx_env)
    os.environ[ctx.fx_env] = str(binary)
    try:
        return P1.verdict_map(S.r_on_spec(ctx, cand.SPEC, "seed"))
    finally:
        if keep is None:
            os.environ.pop(ctx.fx_env, None)
        else:
            os.environ[ctx.fx_env] = keep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True, help="an existing `git archive 193f7f6`")
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", default=S.DEFAULT_PACKAGE, help="stand-in only")
    ap.add_argument("--harness", default=S.DEFAULT_HARNESS, help="stand-in only")
    ap.add_argument(
        "--edits", default="", help="literal edits (JSON), instead of the structural ones"
    )
    ap.add_argument("ids", nargs="*", default=IDS)
    args = ap.parse_args(argv)
    freeze = S.verify_all(Path(__file__))  # before anything, and not optional
    fx = S.Fx(args.fixture, args.harness)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    edits_sha = None
    if args.edits:
        p = Path(args.edits)
        S.EDIT_OVERRIDE.update(json.loads(p.read_text()))
        edits_sha = P1.sha(p)
    ids = [i for i in args.ids if i in S.MUTANTS]
    ctx = S.Ctx(Path(args.scratch).resolve(), out, "193f7f6")
    S.setup(ctx, fx, Path(args.base).resolve())
    rows: list[dict[str, Any]] = []

    def finish(extra: dict[str, Any] | None = None) -> int:
        killed = sum(1 for r in rows[1:] if r.get("killed"))
        nr = [r["id"] for r in rows[1:] if r.get("killed") is None]
        doc = {"runner": "go_parity_s3_mutants", "fixture": fx.package, "stand_in": fx.stand_in,
               "freeze": freeze, "edits_file_sha256": edits_sha, "rows": rows, "killed": killed,
               "not_run": nr, "total": len(rows) - 1, **(extra or {})}  # fmt: skip
        (out / "mutants-s3.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
        print(f"killed {killed}/{len(rows) - 1} (not run: {nr})")
        return 0

    def all_not_run(why: str) -> int:
        rows.append({"id": "control", "passes": None, "not_run": why})
        for mid in ids:
            rows.append(
                {"id": mid, "mutant": S.MUTANTS[mid]["what"], "killed": None, "not_run": why}
            )
            print(f"{mid}: NOT RUN ({why[:100]})", flush=True)
        return finish()

    if not fx.module_dir.is_dir() or ctx.fx_error:
        return all_not_run(f"S3 absent or does not build: {ctx.fx_error}")
    imp = ctx.scratch / "imp" / "r0"
    res = S.import_fx(ctx, imp)
    if res["rc"] != 0:
        return all_not_run("the S3 import failed: " + res["stderr"][-300:])
    cand = P1.load_candidate(imp)

    # ---- the control: the unmutated copy, judged by every detector ----
    t0 = time.perf_counter()
    ccopy = ctx.scratch / "mut" / "control" / fx.package
    shutil.rmtree(ccopy.parent, ignore_errors=True)
    shutil.copytree(fx.module_dir, ccopy)
    cbin, cerr = build_copy(ctx, ccopy, "control")
    control: dict[str, Any] = {"id": "control", "build_error": cerr or None}
    if cbin is not None:
        la = S.layer_a_new(cand, fx, ccopy)
        control_map = r_map(ctx, cand, cbin)
        control["layer_a_new_findings"] = la["new"]
        control["original_outside_baseline"] = la["outside"]
        control["r_items"] = len(control_map)
        control["r_verified"] = sum(1 for v in control_map.values() if v in VERIFIED)
        control["r_items_of_mutants"] = {m: judge_r(m, control_map, control_map)["items [control, mutant]"]
                                         for m in ids}  # fmt: skip
        control["passes"] = not la["new"] and not la["outside"]
    else:
        control_map = {}
        control["passes"] = False
    control["wall_s"] = round(time.perf_counter() - t0, 1)
    rows.append(control)
    print(
        "control:",
        json.dumps({k: v for k, v in control.items() if k != "r_items_of_mutants"}),
        flush=True,
    )
    if not control["passes"]:
        print("the control does not pass: no mutant is judged", file=sys.stderr)
        return finish() or 1

    for mid in ids:
        row: dict[str, Any] = {"id": mid, "mutant": S.MUTANTS[mid]["what"]}
        try:
            copy = S.mutate_fixture(ctx, mid)
        except Exception as err:  # noqa: BLE001 -- an edit that does not apply is reported, not guessed
            row.update(
                killed=None, not_run=f"the edit does not apply: {type(err).__name__}: {err}"[:500]
            )
            rows.append(row)
            print(f"{mid}: NOT RUN ({row['not_run'][:120]})", flush=True)
            continue
        try:
            binary, err = build_copy(ctx, copy, mid.lower())
            if binary is None:
                row.update(killed=None, not_run=f"the mutant does not build: {err[-300:]}")
            else:
                la = S.layer_a_new(cand, fx, copy)
                row["layer_a"] = (
                    judge_a(mid, la["new"], la["outside"])
                    if mid in S.PA3_EXPECT
                    else {"new_findings": [f"{x['code']} {x['key']}" for x in la["new"]],
                          "role": "reported only"}
                )  # fmt: skip
                mut_map = r_map(ctx, cand, binary)
                row["layer_r"] = judge_r(mid, control_map, mut_map)
                row["r_flipped"] = {i: [control_map.get(i), v] for i, v in mut_map.items()
                                    if control_map.get(i) != v}  # fmt: skip
                if mid in S.PA3_EXPECT:
                    row["killed"] = row["layer_a"]["killed"]
                    row["killed_by"] = "PA3-3 (layer A)"
                else:
                    row["killed"] = row["layer_r"]["killed"]
                    row["killed_by"] = "layer R on S3: " + row["layer_r"]["item"]
        except Exception as err:  # noqa: BLE001 -- a mutant that cannot run is reported
            row.update(killed=None, not_run=f"{type(err).__name__}: {err}"[:500])
        print(
            f"{mid}: killed={row.get('killed')} {row.get('killed_by', row.get('not_run', ''))}",
            flush=True,
        )
        rows.append(row)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
