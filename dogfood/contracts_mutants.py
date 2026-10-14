"""The mutation run of layer 3 (contracts between projects), pre-registered with
``docs/results/2026-10-13-contracts-prereg.md`` section 6.

Modelled on ``dogfood/multi_mutants.py``: for each mutant the sources and the suite are copied to
a scratch directory and the mutant's one-line edit is applied to the COPY (its old text must
occur exactly once in the CURRENT source: a stale patch fails loudly, it is never skipped), then
the suite runs there with the copy first on ``PYTHONPATH``. A mutant is KILLED when pytest runs
and reports a failing test; a mutant that does not compile, or a run that ends in a collection or
import error (no ``FAILED`` line), is an ERROR and never counts as a kill. A control run of the
unpatched copy comes first and must pass. The real tree is never written to (the hashes of every
mutated file are checked again at the end).

The eleven mutants and the probes the prereg predicted to kill each (D, L, W, C, V, B: the
prereg's probe series; the tests are in ``tests/contracts``):

* CM1  status ignored: ``status_not_observed`` never emitted             (D2, L2)
* CM2  auth ignored: ``auth``/``as_user`` never compared                 (D5, L3)
* CM3  ``endpoint_missing`` treated as satisfied                         (D1)
* CM4  pin ignored: P := E (every unsatisfied reliance is a gap)         (D1-D5, D6, D9, C4)
* CM5  opt-in ignored: every link is checked whatever ``contract`` says  (W1)
* CM6  field reliance ignored (request and response)                     (D3, D4, L4)
* CM7  ids not templated (concrete paths kept)                           (B0)
* CM8  ``spec_hash`` over the spec file bytes, not the canonical export  (C2, C3)
* CM9  ``pin`` writes despite BROKEN                                     (V4)
* CM10 live: the ``ensure_safe_target`` call removed                     (L5)
* CM11 ``Link.to_dict`` always emits ``contract``                        (W5)

The twelve mutants of the route exclusion (``docs/results/2026-10-13-contracts-exclude-prereg.md``
section 4; the tests are ``tests/contracts/test_exclude.py``, the probes named are
``dogfood/contracts/exclude_probes.py``):

* XM1  filter not applied: every endpoint kept, ``excluded`` still set   (X1, X2, X9b)
* XM2  filter after hashing: ``spec_hash`` over the unfiltered export    (X1)
* XM3  normalisation skipped: raw strings stored and compared            (X2b, X4c)
* XM4  validation off: bad entries dropped, no problem                   (X5)
* XM5  duplicate check removed                                           (X5 row 5)
* XM6  excluded imports treated as satisfied (``evaluate`` skips them)   (X3)
* XM7  ``excluded`` not recorded in the export                           (X4d, X6b)
* XM8  diff ignores ``excluded``: ``endpoint_removed``, no ``exclusion_added``  (X4a, X1)
* XM9  live replays excluded imports                                     (X9b)
* XM10 ``exclude_unmatched`` never emitted                               (X6a, X4d)
* XM11 ``WsProject.to_dict`` always emits ``exclude``                    (X8)
* XM12 an excluded import is BROKEN only when the pin held it            (X3 pinned after, X9b)

(XM12 is the prereg's draft rule, amended before the build; it is listed after XM11.) The
prereg's table says "11 of 11" and lists twelve rows: all twelve are run, 11 + 12 = 23.

The fourteen mutants of the cross-project impact preview
(``docs/results/2026-10-13-contracts-impact-prereg.md`` section 4; every patch is a change of
``lattice/contracts/impact.py``, the tests are ``tests/contracts/test_impact.py``, the probes
named are ``dogfood/contracts/impact_probes.py``):

* IMP1  candidate ignored: the current export used as the candidate        (I1 D1-D5)
* IMP2  BROKEN mapped to ``regression``                                    (I1 D1-D5)
* IMP3  a relied CHANGED mapped to ``contradiction``                       (I1 D8, D9)
* IMP4  ``relied`` ignored: any CHANGED is a ``regression``                (I1 D6, I3)
* IMP5  baseline = the current export, not the pin                         (I0c)
* IMP6  ``exclude`` not passed for the candidate                           (I3x)
* IMP7  the manifest's ``spec`` ignored for the candidate                  (I0a)
* IMP8  links without ``contract`` listed                                  (I4-f)
* IMP9  rows shuffled                                                      (I4, 10-run identity)
* IMP10 the candidate's entry written into the consumer's lock             (I6, I1 run 2)
* IMP11 exit 1 on any ``regression``                                       (I1 D8, D9 exit)
* IMP12 the ref materialised by ``git checkout`` in the provider and back  (I5-h, I6)
* IMP13 a ``no_pin`` row reported ``unaffected``                           (I4-g)
* IMP14 the temporary directory not removed                                (I6 (2))

Test selection: ``tests/contracts tests/workspace -q -x`` (about a minute); a mutant that
touches only ``live.py`` runs ``tests/contracts/test_live.py`` first and the rest only when it
survives that; an XM mutant runs ``tests/contracts/test_exclude.py`` first and the rest only when
it survives that; an IMP mutant runs ``tests/contracts/test_impact.py`` first and the rest only
when it survives that. ``--all-failures`` drops ``-x`` to list every killing test.

Usage: ``python dogfood/contracts_mutants.py [--out FILE] [--jobs N] [--all-failures] [ID ...]``.
Exit 0 when the control passes and every mutant is killed; the last line is
``mutants: N/N killed``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
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

ROOT = Path(__file__).resolve().parents[1]
SUITE = ["tests/contracts", "tests/workspace"]
LIVE_ONLY = ["tests/contracts/test_live.py"]
EXCLUDE_ONLY = ["tests/contracts/test_exclude.py"]
IMPACT_ONLY = ["tests/contracts/test_impact.py"]
CK, EX, LV, WS, IM = (
    "lattice/contracts/check.py",
    "lattice/contracts/export.py",
    "lattice/contracts/live.py",
    "lattice/workspace.py",
    "lattice/contracts/impact.py",
)
# copied into the scratch tree (dogfood/contracts only: the fixtures, not another node's probes)
COPY = [
    "lattice",
    "pyproject.toml",
    "tests/conftest.py",
    "tests/contracts",
    "tests/workspace",
    "dogfood/contracts/ci-bot",
    "dogfood/contracts/golden",
    "dogfood/contracts/golden-streak",
    "dogfood/contracts/streak-widget",
]

# id -> (behaviour, file, old text, new text, first-stage tests or None)
MUTANTS: dict[str, tuple[str, str, str, str, list[str] | None]] = {
    "CM1": (
        "status ignored: status_not_observed is never emitted",
        CK,
        '        return int(item) in ep["statuses"]\n',
        "        return True  # MUTANT CM1: a status claim always holds\n",
        None,
    ),
    "CM2": (
        "auth ignored: auth / as_user never compared",
        CK,
        '    if kind == "auth":\n        if item == "public":\n',
        '    if kind == "auth":\n'
        "        return True  # MUTANT CM2: an auth claim always holds\n"
        '        if item == "public":\n',
        None,
    ),
    "CM3": (
        "endpoint_missing treated as satisfied",
        CK,
        '    if ep is None:\n        return False\n    if kind == "endpoint":\n',
        "    if ep is None:\n"
        '        return kind == "endpoint"  # MUTANT CM3: missing is satisfied\n'
        '    if kind == "endpoint":\n',
        None,
    ),
    "CM4": (
        "pin ignored: P := E (every unsatisfied reliance is a gap, no CHANGED)",
        CK,
        "    pinned_v = version_of(entry)\n",
        "    pinned_v = version_of(entry)\n"
        '    entry = {**entry, "export": cur["export"]}  # MUTANT CM4: P := E\n',
        None,
    ),
    "CM5": (
        "opt-in ignored: every link is checked whatever contract says",
        CK,
        "    return [lk for lk in ws.links if lk.contract and (consumer is None or "
        "lk.source == consumer)]\n",
        "    return [lk for lk in ws.links if (consumer is None or lk.source == consumer)]"
        "  # MUTANT CM5\n",
        None,
    ),
    "CM6": (
        "field reliance ignored (request and response)",
        CK,
        "        for kind, item in claims_of(imp):\n",
        "        for kind, item in (c for c in claims_of(imp) if not c[0].endswith('_field')):"
        "  # MUTANT CM6\n",
        None,
    ),
    "CM7": (
        "ids not templated (concrete paths kept)",
        EX,
        "        if _INT.fullmatch(seg) or _UUID.fullmatch(seg) or _NAMED.fullmatch(seg):\n",
        "        if _NAMED.fullmatch(seg):  # MUTANT CM7: digits and UUIDs are kept\n",
        None,
    ),
    "CM8": (
        "spec_hash over the spec file bytes, not the canonical export",
        EX,
        '        "version": {"dirty": dirty, "git_ref": git_ref, '
        '"spec_hash": spec_hash(export)},\n',
        '        "version": {"dirty": dirty, "git_ref": git_ref, "spec_hash": hashlib.sha256('
        'Path(sys.modules[ref.split(":", 1)[0]].__file__).read_bytes()).hexdigest()},'
        "  # MUTANT CM8\n",
        None,
    ),
    "CM9": (
        "pin writes despite BROKEN",
        CK,
        '            "status": "refused" if broken and not force else "pinned", "error": None,\n',
        '            "status": "pinned", "error": None,  # MUTANT CM9: BROKEN does not refuse\n',
        None,
    ),
    "CM10": (
        "live: the ensure_safe_target call removed",
        LV,
        "    try:\n        ensure_safe_target(host)\n    except UnsafeTargetError as e:\n",
        "    try:\n        pass  # MUTANT CM10: no loopback guard\n"
        "    except UnsafeTargetError as e:\n",
        LIVE_ONLY,
    ),
    "CM11": (
        "Link.to_dict always emits contract",
        WS,
        '        if self.contract is not None:\n            out["contract"] = self.contract\n',
        '        out["contract"] = self.contract  # MUTANT CM11: emitted even when None\n',
        None,
    ),
    "XM1": (
        "exclude: the filter is not applied (every endpoint kept, excluded still set)",
        EX,
        '    out["endpoints"] = {k: v for k, v in held.items() if k not in keys}\n',
        '    out["endpoints"] = dict(held)  # MUTANT XM1: nothing is dropped\n',
        EXCLUDE_ONLY,
    ),
    "XM2": (
        "exclude: spec_hash over the unfiltered export (filter after hashing)",
        EX,
        '        "version": {"dirty": dirty, "git_ref": git_ref, '
        '"spec_hash": spec_hash(export)},\n',
        '        "version": {"dirty": dirty, "git_ref": git_ref, "spec_hash": spec_hash('
        "export_spec(load_spec(project, ref)))},  # MUTANT XM2: hashed before the filter\n",
        EXCLUDE_ONLY,
    ),
    "XM3": (
        "exclude: normalisation skipped (raw strings stored and compared)",
        EX,
        "            keys.append(parse_endpoint(entry))\n",
        "            parse_endpoint(entry)\n"
        "            keys.append(entry)  # MUTANT XM3: the raw string is kept\n",
        EXCLUDE_ONLY,
    ),
    "XM4": (
        "exclude: validation off (bad entries dropped, no problem)",
        WS,
        '            keys, reasons = check_exclude(item["exclude"])\n',
        '            keys, reasons = check_exclude(item["exclude"])[0], []  # MUTANT XM4\n',
        EXCLUDE_ONLY,
    ),
    "XM5": (
        "exclude: the duplicate check removed",
        EX,
        "    for key in dict.fromkeys(k for k in keys if keys.count(k) > 1):\n",
        "    for key in ():  # MUTANT XM5: a key named twice is accepted\n",
        EXCLUDE_ONLY,
    ),
    "XM6": (
        "exclude: an import of an excluded endpoint is treated as satisfied",
        CK,
        "        if imp.endpoint in excluded:\n",
        "        if imp.endpoint in excluded:\n"
        "            continue  # MUTANT XM6: evaluate skips it\n",
        EXCLUDE_ONLY,
    ),
    "XM7": (
        "exclude: excluded is not recorded in the export",
        EX,
        '    out["excluded"] = keys\n',
        "    pass  # MUTANT XM7: the declaration is not kept\n",
        EXCLUDE_ONLY,
    ),
    "XM8": (
        "exclude: the diff ignores excluded (endpoint_removed, no exclusion_added)",
        CK,
        '    p_ex, c_ex = set(pinned.get("excluded", ())), set(current.get("excluded", ()))\n',
        "    p_ex, c_ex = set(), set()  # MUTANT XM8: excluded is not diffed\n",
        EXCLUDE_ONLY,
    ),
    "XM9": (
        "exclude: live replays an excluded import",
        LV,
        "    replayable = [imp for imp in imports if imp.endpoint not in excluded]\n",
        "    replayable = list(imports)  # MUTANT XM9: the excluded are replayed too\n",
        EXCLUDE_ONLY,
    ),
    "XM10": (
        "exclude: exclude_unmatched is never emitted",
        CK,
        '        self.unmatched.update((name, k) for k in got.get("exclude_unmatched", ()))\n',
        "        pass  # MUTANT XM10: no warning\n",
        EXCLUDE_ONLY,
    ),
    "XM11": (
        "exclude: WsProject.to_dict always emits exclude ([] when None)",
        WS,
        '        out["exclude"] = None if self.exclude is None else list(self.exclude)\n',
        '        out["exclude"] = [] if self.exclude is None else list(self.exclude)'
        "  # MUTANT XM11\n",
        EXCLUDE_ONLY,
    ),
    "XM12": (
        "exclude: an excluded import is BROKEN only when the pin held it (the draft rule)",
        CK,
        "        if imp.endpoint in excluded:\n",
        '        if imp.endpoint in excluded and holds(seen, imp, "endpoint", ""):'
        "  # MUTANT XM12\n",
        EXCLUDE_ONLY,
    ),
    "IMP1": (
        "impact: the candidate is ignored (the current export is the candidate)",
        IM,
        '    c_exp, c_ver = cand["export"], cand["version"]\n',
        '    c_exp, c_ver = (cur or cand)["export"], cand["version"]  # MUTANT IMP1\n',
        IMPACT_ONLY,
    ),
    "IMP2": (
        "impact: a BROKEN reliance is labelled regression",
        IM,
        "    if broken:\n        return CONTRADICTION\n",
        "    if broken:\n        return REGRESSION  # MUTANT IMP2\n",
        IMPACT_ONLY,
    ),
    "IMP3": (
        "impact: a relied CHANGED is labelled contradiction",
        IM,
        '    if any(f.get("relied") for f in changed):\n        return REGRESSION\n',
        '    if any(f.get("relied") for f in changed):\n'
        "        return CONTRADICTION  # MUTANT IMP3\n",
        IMPACT_ONLY,
    ),
    "IMP4": (
        "impact: relied is ignored (any CHANGED is a regression)",
        IM,
        '    if any(f.get("relied") for f in changed):\n',
        "    if changed:  # MUTANT IMP4\n",
        IMPACT_ONLY,
    ),
    "IMP5": (
        "impact: the baseline is the current export, not the pin",
        IM,
        "    pinned_v = ck.version_of(entry)\n",
        "    pinned_v = ck.version_of(entry)\n"
        '    entry = {**entry, "export": (cur or cand)["export"]}  # MUTANT IMP5\n',
        IMPACT_ONLY,
    ),
    "IMP6": (
        "impact: exclude is not passed for the candidate",
        IM,
        "                got = compute_export(temp, spec_ref, exclude)\n",
        "                got = compute_export(temp, spec_ref, ())  # MUTANT IMP6\n",
        IMPACT_ONLY,
    ),
    "IMP7": (
        "impact: the manifest's spec is ignored for the candidate",
        IM,
        "                got = compute_export(temp, spec_ref, exclude)\n",
        "                got = compute_export(temp, DEFAULT_SPEC, exclude)  # MUTANT IMP7\n",
        IMPACT_ONLY,
    ),
    "IMP8": (
        "impact: links without a contract key are listed",
        IM,
        "if lk.target == provider and lk.contract),",
        "if lk.target == provider),  # MUTANT IMP8",
        IMPACT_ONLY,
    ),
    "IMP9": (
        "impact: the rows are shuffled",
        IM,
        "        session = ck._Session(ws)\n",
        "        session = ck._Session(ws)\n"
        "        __import__('random').shuffle(links)  # MUTANT IMP9\n",
        IMPACT_ONLY,
    ),
    "IMP10": (
        "impact: the candidate's entry is written into the consumer's lock",
        IM,
        "    pinned_v = ck.version_of(entry)\n",
        "    pinned_v = ck.version_of(entry)\n"
        '    new = {"spec_ref": cand["spec_ref"], "version": cand["version"],\n'
        '           "export": cand["export"]}  # MUTANT IMP10\n'
        '    ck.write_lock(lock_path, {**lock, "providers": {**lock["providers"],\n'
        "                                                   link.target: new}})\n",
        IMPACT_ONLY,
    ),
    "IMP11": (
        "impact: exit 1 on any regression",
        IM,
        '        if any(row["label"] == CONTRADICTION for row in self.consumers):\n',
        '        if any(row["label"] in (CONTRADICTION, REGRESSION) for row in self.consumers):'
        "  # MUTANT IMP11\n",
        IMPACT_ONLY,
    ),
    "IMP12": (
        "impact: the ref is checked out in the provider's tree and back",
        IM,
        "            materialise(project, sha, temp)\n",
        '            for to in (sha, "-"):  # MUTANT IMP12\n'
        '                subprocess.run(["git", "-C", str(project), "checkout", "-q", to],\n'
        "                               capture_output=True, check=False)\n"
        "            materialise(project, sha, temp)\n",
        IMPACT_ONLY,
    ),
    "IMP13": (
        "impact: a no_pin row is reported unaffected",
        IM,
        "                out.consumers.append(_error_row(link, e))\n",
        "                row = _error_row(link, e)  # MUTANT IMP13\n"
        '                if e.code == "no_pin":\n'
        "                    row.update(label=UNAFFECTED, error=None)\n"
        "                out.consumers.append(row)\n",
        IMPACT_ONLY,
    ),
    "IMP14": (
        "impact: the temporary directory is not removed",
        IM,
        "            shutil.rmtree(temp, ignore_errors=True)\n",
        "            pass  # MUTANT IMP14: left behind\n",
        IMPACT_ONLY,
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache", "node_modules", "_tmp_*"
    )
    for rel in COPY:
        src = ROOT / rel
        if not src.exists():
            raise SystemExit(f"cannot copy {rel}: it is not in {ROOT}")
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, out, ignore=ignore)
        else:
            shutil.copy2(src, out)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new, _first = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    if old == new:
        raise SystemExit(f"{mid}: the patch changes nothing")
    mutated = text.replace(old, new)
    try:
        compile(mutated, rel, "exec")
    except SyntaxError as e:
        raise SystemExit(
            f"{mid}: the mutant does not compile ({e}); a syntax error is no kill"
        ) from e
    path.write_text(mutated, encoding="utf-8")


def _env(dst: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _pytest_cmd() -> list[str]:
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    return ["uv", "run", "--no-sync", "--project", str(ROOT), "pytest"]


def run_pytest(dst: Path, tests: list[str], all_failures: bool) -> dict[str, Any]:
    t0 = time.monotonic()
    cmd = [*_pytest_cmd(), *tests, "-q", "-p", "no:cacheprovider", "-rf", "--no-header"]
    if not all_failures:
        cmd.append("-x")
    try:
        r = subprocess.run(
            cmd, cwd=str(dst), env=_env(dst), capture_output=True, text=True, timeout=1200
        )
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 1200s]", 124
    failed = [m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)]
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", ln)),
        "",
    ).strip()
    return {
        "exit": rc,
        "failed": failed,
        "summary": summary,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": out[-2000:],
    }


def one(mid: str, all_failures: bool) -> dict[str, Any]:
    """Run the control (`mid` == "control") or one mutant: the first stage, then the full suite
    only when a first stage exists and the mutant survived it."""
    t0 = time.monotonic()
    first = None if mid == "control" else MUTANTS[mid][4]
    stages: list[list[str]] = [first, SUITE] if first else [SUITE]
    with tempfile.TemporaryDirectory(prefix=f"lattice-contracts-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res: dict[str, Any] = {}
        for tests in stages:
            res = run_pytest(dst, tests, all_failures)
            res["tests"] = " ".join(tests)
            if res["exit"] != 0:
                break
    if mid == "control":
        verdict = "PASS (control)" if res["exit"] == 0 else "CONTROL FAILED"
    elif res["exit"] == 0:
        verdict = "SURVIVED"
    elif res["failed"]:
        verdict = "killed"
    else:
        verdict = "ERROR"  # a collection/import error or a timeout: not a kill
    return {"pytest": res, "verdict": verdict, "seconds": round(time.monotonic() - t0, 1)}


def _order(mid: str) -> tuple[int, int]:
    prefix = mid.rstrip("0123456789")
    return (("CM", "XM", "IMP").index(prefix), int(mid[len(prefix) :]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=sorted(MUTANTS, key=_order))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--all-failures", action="store_true", help="run without -x")
    args = ap.parse_args(argv)
    for mid in args.ids:
        if mid not in MUTANTS:
            raise SystemExit(f"unknown mutant {mid}")
    files = sorted({MUTANTS[m][1] for m in MUTANTS})
    hashes = {f: sha(ROOT / f) for f in files}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory() as d:  # every patch applies cleanly before anything runs
        copy_tree(Path(d))
        for mid in args.ids:
            apply(Path(d), mid)
            (Path(d) / MUTANTS[mid][1]).write_text(  # undo: each patch is checked on its own
                (ROOT / MUTANTS[mid][1]).read_text(encoding="utf-8"), encoding="utf-8"
            )
    t0 = time.monotonic()
    runs: dict[str, dict[str, Any]] = {}
    control = one("control", args.all_failures)
    runs["control"] = control
    print(
        f"control: {control['verdict']} ({control['pytest']['summary']}) {control['seconds']}s",
        flush=True,
    )
    ok = control["verdict"] == "PASS (control)"
    if ok:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(one, mid, args.all_failures): mid for mid in args.ids}
            for fut in concurrent.futures.as_completed(futs):
                mid = futs[fut]
                runs[mid] = res = fut.result()
                failed = res["pytest"]["failed"]
                print(
                    f"{mid}: {res['verdict']} ({res['seconds']}s, pytest failed {len(failed)}: "
                    f"{', '.join(f.split('::', 1)[-1] for f in failed[:3])})",
                    flush=True,
                )
    else:
        print(control["pytest"]["tail"], flush=True)
    changed = [f for f in files if sha(ROOT / f) != hashes[f]]
    print(f"\n{'id':<8} {'verdict':<14} {'sec':>6}  {'change':<62} killed by (first failing test)")
    for mid in sorted(runs, key=lambda m: (-1, -1) if m == "control" else _order(m)):
        res = runs[mid]
        if mid == "control":
            what, by = "unmutated copy", res["pytest"]["summary"]
        else:
            what = MUTANTS[mid][0]
            failed = res["pytest"]["failed"]
            by = failed[0] if failed else res["verdict"]
        print(f"{mid:<8} {res['verdict']:<14} {res['seconds']:>6}  {what[:62]:<62} {by}")
    killed = sum(1 for m in args.ids if runs.get(m, {}).get("verdict") == "killed")
    if changed:
        print(f"the real tree was modified: {changed}", file=sys.stderr)
    print(f"total wall time: {round(time.monotonic() - t0, 1)}s")
    if args.out:
        args.out.write_text(json.dumps({"hashes": hashes, "runs": runs}, indent=1))
    print(f"mutants: {killed}/{len(args.ids)} killed")
    return 0 if ok and killed == len(args.ids) and not changed else 1


if __name__ == "__main__":
    sys.exit(main())
