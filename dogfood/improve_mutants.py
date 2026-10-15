"""The mutation run of spike 3 (``lattice.improve`` and Studio's improvement signals),
pre-registered with ``docs/results/2026-10-12-spike3-prereg.md``; IM7-IM21 are the second
half's (``lattice improve verify`` and ``draft``, ``docs/results/2026-10-13-improve2-prereg.md``);
IA1-IA16 are the apply step's (``lattice improve apply``,
``docs/results/2026-10-15-improve-apply-prereg.md``, tests/improve/test_apply.py).

Modelled on ``dogfood/security_mutants.py``: for each mutant, ``lattice/``, ``tests/``,
``dogfood/`` and ``docs/results/data/streak-plans-2026-10-11`` are copied to a scratch
directory and the mutant's edit is applied to the copy (its old text must occur exactly once
in the CURRENT source), then the suite runs there with the copy first on ``PYTHONPATH``. A
mutant is CAUGHT when the suite fails. A control run of the unpatched copy comes first and must
pass. The working tree is never patched.

The mutants and the tests predicted to catch each:

* IM1 the ranker converts dollars into seconds (one cost unit)
      -> test_analyze: test_the_i_r_arithmetic_and_class_order,
         test_seconds_are_never_converted_to_dollars
* IM2 closed items are still ranked
      -> test_analyze: test_closed_items_are_left_out...; test_import: test_the_i_r_replay;
         test_cli: test_ledger_closes_an_item; test_events (studio): the mock's shape
* IM3 a protected target is not marked needs_owner
      -> test_analyze: test_i4_a_checker_proposal_needs_the_owner; test_cli: the I-4 run;
         test_events (studio): the mock's shape
* IM4 a torn last line crashes the reader
      -> test_events (improve): test_a_torn_last_line..., test_bad_middle_lines...
* IM5 a node's cause is always ``other``
      -> test_events (studio): test_b_cause_on_a_give_up, test_b_cause_on_an_owner_stop,
         test_the_ranker_reads_the_live_log
* IM6 ``layer_seconds`` copied from the wrong layer
      -> test_events (studio): test_a_layer_seconds_copied_from_the_report
* IM7 an event at the close second counted as after (>=)         -> V1c, V2i
* IM8 an event at the close second counted as before (<=)        -> V1b, V2i
* IM9 not_enough_data reported as improved (exit 0)              -> V1d, V2e, V2f
* IM10 minimum 2 occurrences instead of 3                        -> V1b, V1c, V2d'
* IM11 dollars converted into seconds                            -> V2h (ii), (iv)
* IM12 the error is sign-dependent                               -> V2c
* IM13 recurrence ignored (improved when f_after > 0)            -> V2d
* IM14 the pattern filter dropped (all keys pooled)              -> V2g
* IM15 the prediction from the whole log, not the before window  -> V1a
* IM16 the score used as the prediction without an expected      -> V1e, V2d
* IM17 a needs_owner proposal drafted                             -> D2
* IM18 the draft written into the project; verify appends a line -> D3 hashes
* IM19 closed twice: the last close used                         -> V3e
* IM20 wall-clock time in the draft header                       -> D4 (1.1 s gap)
* IM21 --at ignored (the ledger ts used)                         -> V1g
  (tests/improve/test_verify.py and test_draft.py: one test per probe row)
* IA1 the base branch fast-forwarded to the apply commit       -> A3 (base ref), A5d
* IA2 the edit written to the owner's studio.toml              -> A3 hashes, A1 diff
* IA3 apply appends a closed line to the ledger                -> A3 (improvements.jsonl), A7
* IA4 gate runs logged as gate_run events                      -> A3 (events.jsonl), A2b
* IA5 not_config dropped (a module draft applied)              -> A0d, A0e
* IA6 a failed gate still exits 0                              -> A2b, A2c
* IA7 the line re-derived with compact json.dumps              -> A1 (diff, blob), A6c
* IA8 a replaced line loses its trailing comment               -> A4a, A4f
* IA9 --discard without branch_moved                           -> A5c
* IA10 --discard without merged                                -> A5d, A7d
* IA11 a model call (the claude binary) writes the message     -> A3 static (never executed:
       the call sits behind an environment variable no test sets)
* IA12 already_set dropped                                     -> A4h
* IA13 already_applied dropped (a second branch)               -> A0f, A6a
* IA14 the inserted line always ends \n                        -> A4i
* IA15 the worktree under <repo>/.lattice/rebuilds/            -> A3 (working tree, exclude)
* IA16 dirty_checkout dropped                                  -> A0i
  (tests/improve/test_apply.py: one test per probe row)

Usage: ``uv run python dogfood/improve_mutants.py [--out FILE] [--jobs N] [ID ...]``.
Exit 0 when the control passes and every mutant is caught; the last line is
``mutants: N/N killed``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
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

ROOT = Path(__file__).resolve().parents[1]
SUITE = ["tests/improve", "tests/studio/test_events.py"]
AN, EV, RB = "lattice/improve/analyze.py", "lattice/improve/events.py", "lattice/studio/rebuild.py"
VF, DR, MN = "lattice/improve/verify.py", "lattice/improve/draft.py", "lattice/improve/__main__.py"
AP = "lattice/improve/apply.py"
FILES = [AN, EV, RB, VF, DR, MN, AP, "lattice/improve/signals.py", "lattice/studio/plan.py",
         "dogfood/improve_mutants.py"]  # fmt: skip

# id -> (behaviour, file, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "IM1": (
        "the ranker converts dollars into seconds (one cost unit, $1 = 3600 s)",
        AN,
        "    @property\n"
        "    def score_class(self) -> str:\n"
        '        if self._mean("seconds") is not None:\n'
        '            return "A"\n'
        '        if self._mean("usd") is not None:\n'
        '            return "B"\n'
        '        return "C"\n'
        "\n"
        "    @property\n"
        "    def unit(self) -> str | None:\n"
        '        return {"A": "s", "B": "usd", "C": None}[self.score_class]\n'
        "\n"
        "    @property\n"
        "    def cost_per(self) -> float | None:\n"
        "        cls = self.score_class\n"
        '        return self._mean("seconds") if cls == "A" '
        'else self._mean("usd") if cls == "B" else None\n',
        "    @property\n"
        "    def score_class(self) -> str:  # MUTANT IM1: one unit\n"
        '        if self._mean("seconds") is not None or self._mean("usd") is not None:\n'
        '            return "A"\n'
        '        return "C"\n'
        "\n"
        "    @property\n"
        "    def unit(self) -> str | None:\n"
        '        return {"A": "s", "C": None}[self.score_class]\n'
        "\n"
        "    @property\n"
        "    def cost_per(self) -> float | None:\n"
        '        s, d = self._mean("seconds"), self._mean("usd")\n'
        "        return s if s is not None else (d * 3600 if d is not None else None)\n",
    ),
    "IM2": (
        "closed items are still ranked",
        AN,
        "    open_ = [c for c in cl if c.closed is None]\n",
        "    open_ = list(cl)  # MUTANT IM2: closed items still ranked\n",
    ),
    "IM3": (
        "a protected target is not marked needs_owner",
        AN,
        '    status = "needs_owner" if protected else "open"\n',
        '    status = "open"  # MUTANT IM3: protected targets not refused\n',
    ),
    "IM4": (
        "a torn last line crashes the reader",
        EV,
        "        try:\n"
        '            ev = json.loads(raw.decode("utf-8"))\n'
        "        except (UnicodeDecodeError, json.JSONDecodeError):\n"
        "            ev = None\n",
        '        ev = json.loads(raw.decode("utf-8"))  # MUTANT IM4: no tolerance\n',
    ),
    "IM5": (
        "a node's cause is always other",
        RB,
        "        cause = sig.build_cause(rec, stopped_by=run.stopped_by)\n",
        '        cause = None if rec.get("status") == "passed" else "other"  # MUTANT IM5\n',
    ),
    "IM6": (
        "layer_seconds copied from the wrong layer (each value shifted to the next layer)",
        RB,
        "    out = {str(k): float(v) for k, v in layers.items()\n"
        "           if isinstance(v, int | float) and not isinstance(v, bool)}  # fmt: skip\n",
        "    _ks, _vs = list(layers), list(layers.values())  # MUTANT IM6: shifted layers\n"
        "    out = {str(k): float(v) for k, v in zip(_ks, _vs[1:] + _vs[:1])\n"
        "           if isinstance(v, int | float) and not isinstance(v, bool)}  # fmt: skip\n",
    ),
    "IM7": (
        "an event at the close second counted as after (>=)",
        VF,
        '        return "at_close"\n',
        '        return "after"  # MUTANT IM7: the close second is after\n',
    ),
    "IM8": (
        "an event at the close second counted as before (<=)",
        VF,
        "    if ts == close:\n",
        "    if False:  # MUTANT IM8: the close second is before\n",
    ),
    "IM9": (
        "not_enough_data reported as improved (exit 0)",
        VF,
        '            return "not_enough_data", (\n'
        '                f"needs {MIN_OCCURRENCES} occurrences in each window',
        '            return "improved", (  # MUTANT IM9\n'
        '                f"needs {MIN_OCCURRENCES} occurrences in each window',
    ),
    "IM10": (
        "minimum 2 occurrences instead of 3",
        VF,
        "MIN_OCCURRENCES = 3  #",
        "MIN_OCCURRENCES = 2  # MUTANT IM10:",
    ),
    "IM11": (
        "dollars converted into seconds when a window has no seconds ($1 = 3600 s)",
        VF,
        '    return o.seconds if unit == "s" else o.usd if unit == "usd" else None\n',
        "    if o.seconds is not None:  # MUTANT IM11: one unit\n"
        "        return o.seconds\n"
        "    return o.usd * 3600 if o.usd is not None else None\n",
    ),
    "IM12": (
        "the error is sign-dependent: | |measured| - |predicted| | / |predicted|",
        VF,
        '            error = abs(ch["delta"] - pd) / abs(pd)\n',
        '            error = abs(abs(ch["delta"]) - abs(pd)) / abs(pd)  # MUTANT IM12\n',
    ),
    "IM13": (
        "recurrence ignored (improved when f_after > 0)",
        VF,
        '    if fa > 0:\n        return "recurred", ',
        '    if fa > 0:  # MUTANT IM13: a recurrence is not looked at\n        return "improved", ',
    ),
    "IM14": (
        "the pattern filter dropped (every key of the log pooled)",
        VF,
        "else sorted(k for k in occs if fnmatch.fnmatchcase(k, signal))\n",
        "else sorted(occs)  # MUTANT IM14: every key\n",
    ),
    "IM15": (
        "the prediction rebuilt from the whole log, not the before window",
        VF,
        '    predicted, note = _predict(keys, res["family"], res["before_occ"])\n',
        "    predicted, note = _predict(  # MUTANT IM15: the whole log\n"
        '        keys, res["family"], [o for k in keys for o in occs[k]]\n'
        "    )\n",
    ),
    "IM16": (
        "the score used as the prediction when the proposal has no expected saving",
        VF,
        '    if family != "cost" or not is_x:\n        return None, "no prediction recorded"\n',
        '    if family != "cost" or not is_x:  # MUTANT IM16: f x c as a saving\n'
        '        sc = _proposal(Cluster(keys[0], list(before_occ)))["score"] if keys else None\n'
        "        if sc:\n"
        '            return {"delta": -sc, "unit": "s", "source": "score", "basis": "score"}, ""\n'
        '        return None, "no prediction recorded"\n',
    ),
    "IM17": (
        "a needs_owner proposal is drafted",
        DR,
        '    if p["status"] == "needs_owner":\n',
        "    if False:  # MUTANT IM17: needs_owner drafted\n",
    ),
    "IM18": (
        "draft written into the project; verify appends a ledger line",
        MN,
        '    if args.command == "verify":\n        print(\n',
        '    if args.command == "verify":  # MUTANT IM18: writes where it must not\n'
        '        with lpath.open("a", encoding="utf-8") as _f:\n'
        '            _f.write(json.dumps({"id": args.id, "status": "open"}) + "\\n")\n'
        "    else:\n"
        '        (project / "improve-draft.md").write_text(doc["text"], encoding="utf-8")\n'
        '    if args.command == "verify":\n'
        "        print(\n",
    ),
    "IM19": (
        "closed twice: the last close used, not the first of the run",
        VF,
        "            cur[1] += 1\n",
        "            cur[1] += 1\n"
        "            cur[0] = e  # MUTANT IM19: the last close of the run\n",
    ),
    "IM20": (
        "wall-clock time in the draft header",
        DR,
        '        f"Proposal {ident} ({kind}), signal {key}; status open. As of {as_of}.",\n',
        '        f"Proposal {ident} ({kind}), signal {key}; status open. As of "\n'
        "        f\"{__import__('datetime').datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')}.\",\n",
    ),
    "IM21": (
        "--at ignored (the ledger ts is the closing point)",
        VF,
        '    if at is not None:\n        close, source = at, "--at"\n',
        '    if False:  # MUTANT IM21: --at ignored\n        close, source = at, "--at"\n',
    ),
    "IA1": (
        "the base branch is fast-forwarded to the apply commit",
        AP,
        '        sha = _git_ok(worktree, "rev-parse", "HEAD").strip()\n',
        '        sha = _git_ok(worktree, "rev-parse", "HEAD").strip()\n'
        '        _git_ok(top, "update-ref", f"refs/heads/{base_branch}", sha)  # MUTANT IA1\n',
    ),
    "IA2": (
        "the edit is also written to the owner's studio.toml",
        AP,
        "        target.write_bytes(new_bytes)\n",
        "        target.write_bytes(new_bytes)\n"
        "        toml_path.write_bytes(new_bytes)  # MUTANT IA2: the owner's file\n",
    ),
    "IA3": (
        "apply appends a closed line to the ledger",
        AP,
        '        record_path = applies / f"{ident}.json"\n',
        '        record_path = applies / f"{ident}.json"\n'
        '        with (state / IMPROVEMENTS_FILE).open("a", encoding="utf-8") as _f:'
        "  # MUTANT IA3\n"
        '            _f.write(json.dumps({"id": ident, "signal": key, "status": "closed",\n'
        '                                 "commit": sha, "ts": applied_at}) + "\\n")\n',
    ),
    "IA4": (
        "gate runs are logged as gate_run events (as the rebuild loop's gate_round does)",
        AP,
        '    rec["gates"] = {\n',
        '    with (state / EVENTS_FILE).open("a", encoding="utf-8") as _f:  # MUTANT IA4\n'
        "        for _r in results:\n"
        '            _f.write(json.dumps({"v": 1, "ts": applied_at, "kind": "gate_run",\n'
        '                                 "gate": "level", "seconds": _r["seconds"]}) + "\\n")\n'
        '    rec["gates"] = {\n',
    ),
    "IA5": (
        "not_config dropped (a module draft is applied)",
        AP,
        '    if doc["template"] != "config":\n',
        "    if False:  # MUTANT IA5: not_config dropped\n",
    ),
    "IA6": (
        "a failed gate still exits 0",
        AP,
        '    code = {"failed": 1, "interrupted": 2}.get(status_, 0)\n',
        '    code = {"interrupted": 2}.get(status_, 0)  # MUTANT IA6\n',
    ),
    "IA7": (
        "the line is re-derived with a compact json.dumps instead of the draft's own",
        AP,
        "    line = tl[1]\n",
        "    line = f\"{name} = {json.dumps(want, separators=(',', ':'))}\"  # MUTANT IA7\n",
    ),
    "IA8": (
        "a replaced line loses its trailing comment",
        AP,
        "            m.group(1) + line + value[len(value.rstrip()) :] + comment"
        " + _ending(lines[k])\n",
        "            m.group(1) + line + value[len(value.rstrip()) :] + _ending(lines[k])"
        "  # MUTANT IA8\n",
    ),
    "IA9": (
        "--discard without the branch_moved refusal",
        AP,
        "    if tip.returncode != 0 or tip.stdout.strip() != commit:\n",
        "    if False:  # MUTANT IA9\n",
    ),
    "IA10": (
        "--discard without the merged refusal",
        AP,
        "    if others:\n",
        "    if False:  # MUTANT IA10\n",
    ),
    "IA11": (
        "a model call writes the commit message (the call is behind an environment variable "
        "no test sets: the mutation run never executes it)",
        AP,
        '    message = _message(ident, p, doc["as_of"], line, current, expected)\n',
        '    message = _message(ident, p, doc["as_of"], line, current, expected)\n'
        '    if os.environ.get("LATTICE_MUTANT_IA11"):  # MUTANT IA11: never set\n'
        '        message = subprocess.run(["claude", "-p", message], capture_output=True,\n'
        "                                 text=True).stdout\n",
    ),
    "IA12": (
        "already_set dropped",
        AP,
        '    if key in rb and (cur == literal if op == "=" else _holds_run(cur, literal)):\n',
        "    if False:  # MUTANT IA12\n",
    ),
    "IA13": (
        "already_applied dropped (a second branch is made)",
        AP,
        '    if (state / APPLIES_DIR / f"{ident}.json").exists():\n',
        "    if False:  # MUTANT IA13\n",
    ),
    "IA14": (
        "the inserted line always ends in a bare newline",
        AP,
        "        new_lines.insert(h + 1, line + head_end)\n",
        '        new_lines.insert(h + 1, line + "\\n")  # MUTANT IA14\n',
    ),
    "IA15": (
        "the worktree under <repo>/.lattice/rebuilds/",
        AP,
        '    worktree = top.parent / f"{top.name}.improve" / ident\n',
        '    worktree = top / ".lattice" / "rebuilds" / ident  # MUTANT IA15\n',
    ),
    "IA16": (
        "dirty_checkout dropped",
        AP,
        "    if status.strip():\n",
        "    if False:  # MUTANT IA16\n",
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    for rel in (
        "lattice",
        "tests",
        "dogfood",
        "docs/results/data/streak-plans-2026-10-11",
        "docs/results/data/2026-10-12-spike3",
    ):
        shutil.copytree(ROOT / rel, dst / rel, ignore=ignore)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _env(dst: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    return env


def run_pytest(dst: Path) -> dict[str, object]:
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["uv", "run", "--project", str(ROOT), "pytest", *SUITE, "-q", "-p",
             "no:cacheprovider", "-rf", "--no-header"],
            cwd=str(dst), env=_env(dst), capture_output=True, text=True, timeout=900,
        )  # fmt: skip
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 900s]", 124
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)})
    summary = next((ln for ln in reversed(out.splitlines())
                    if re.search(r"\d+ (passed|failed)", ln)), "").strip()  # fmt: skip
    return {"exit": rc, "failed": failed, "summary": summary,
            "seconds": round(time.monotonic() - t0, 1), "tail": out[-2000:]}  # fmt: skip


def one(mid: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-imp-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res = run_pytest(dst)
    caught = res["exit"] != 0
    if mid == "control":
        verdict = "PASS (control)" if not caught else "CONTROL FAILED"
    else:
        verdict = "caught" if caught else "SURVIVED"
    return {"pytest": res, "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    hashes = {f: sha(ROOT / f) for f in FILES}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory() as d:  # every patch applies cleanly before anything runs
        copy_tree(Path(d))
        for mid in args.ids:
            if mid not in MUTANTS:
                raise SystemExit(f"unknown mutant {mid}")
            apply(Path(d), mid)
    results: dict[str, object] = {"hashes": hashes, "runs": {}}
    control = one("control")
    results["runs"]["control"] = control  # type: ignore[index]
    print(f"control: {control['verdict']} ({control['pytest']['summary']})", flush=True)
    ok = control["verdict"] == "PASS (control)"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            results["runs"][mid] = res  # type: ignore[index]
            failed = res["pytest"]["failed"]  # type: ignore[index]
            print(f"{mid}: {res['verdict']} (pytest failed {len(failed)}: "
                  f"{', '.join(f.split('::')[-1] for f in failed)})", flush=True)  # fmt: skip
            ok = ok and res["verdict"] == "caught"
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    killed = sum(1 for m in args.ids if results["runs"][m]["verdict"] == "caught")  # type: ignore[index]
    print(f"mutants: {killed}/{len(args.ids)} killed", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
