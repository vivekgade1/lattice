# ruff: noqa: E501
"""RUNNER of the typed-conditions Streak arm (PS0, PS1, PS1t, PS2, PS3, PSx).

Registered in ``docs/results/2026-10-17-typed-conditions-prereg.md`` section 3.3, carried by the round-2 and
round-3 preregs, and re-opened by the "Amendment (Streak arm, orchestrator, 2026-10-18)" at the end of
``2026-10-18-typed-conditions-round3-prereg.md``: a separate author wrote the Streak set (23 formed rules, 43
forms, 29 Go mutants, 690 truth cells, 24 pairs; ``streak-set.tgz``); this file is the runner, written from
that set's README.md and the PS rows, **frozen before any Streak run** and run once by a separate node. The
PS rows are report-only as registered ("not part of this round's gate"); the Spike H gate verdict is unchanged
by them. Nothing here edits a frozen runner, the library, the set, Streak or the main checkout.

    export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
    uv run python dogfood/forms_probes_streak.py --stage streak --dry-run [--shapes DIR | --set-tgz FILE]
    uv run python dogfood/forms_probes_streak.py --stage streak --freeze FILE --driver-python PY \\
        --scratch DIR --out DIR [--streak-repo ~/Documents/streak] [--owner LABEL]
    uv run python dogfood/forms_probes_streak.py --stage streak --rescore RAW_DIR ...   # post hoc, no Docker

What a scored run does (``--stage streak`` without ``--dry-run`` / ``--rescore``):

1. **Hash discipline, unconditional.** ``--freeze FILE`` (``sha256sum`` format) must list this very file (its own
   sha256 is computed here and compared), the library files the run depends on (``forms.py``, ``forms_tier2.py``,
   ``check.py``, ``validate.py``, ``invariants.py``, ``model.py``) and every listed file must match. There is no
   option that skips it; it is checked again after the run (PH). The set is checked against constants written in
   this file (the tarball's sha256, the MANIFEST file's sha256, every file's sha256, no unlisted file) and the
   archived Streak against ``source.sha256`` and the full commit id.
2. **A global once-only ledger** (``~/.lattice/spike-h-streak-ledger.json``, not under ``--out``, so a different
   ``--out`` cannot bypass it): a variant is scored once; a re-run is allowed only after a crash *before any
   metric* (``crashed_before_metric``), at most one.
3. **Builds** ``bin/streak-api`` for ``ok`` and each of the 29 mutants (``git archive`` of Streak a0679e9 into
   scratch, ``git apply`` of the patch whose sha256 is checked, ``go build``).
4. **Runs the checker** (``python -m lattice.spec check --layers CD --database mysql``; one throwaway ``mysql:8.4``
   container per run through Lattice's dbserver, labelled ``lattice.owner=<--owner>`` and removed): 3 runs of
   ``ok`` without the generated checks (the PSx baseline), 10 runs of ``ok`` with them (PS3 and PSx), one run per
   mutant with them (PS1, PS1t). "With them" is Streak's own SPEC plus the generated elements of its approved
   forms (the same thing ``forms.check_with_forms`` runs), loaded through a one-file wrapper module in the
   scratch tree; a plain ``lattice spec check`` never reads a forms file.
5. **Scores** every row from those reports and the set, with the definitions of the frozen scorer
   ``forms_probes3.py`` (cells, G / H / T, K6, K7, PG1o, PG1k, PG2, PT2f incl. the ``items_outside_the_pair``
   rule), and the PS definitions below. Nothing is hard-coded NOT RUN: a row is NOT RUN only when its input
   could not be produced, and says why.

G variants: ``strict`` (the default, the registered measurement: G is the library's generated elements as
generated) and ``drop_invalid`` (post hoc, labelled so: the generated invariants that ``validate`` refuses are
left out and recorded; everything else unchanged). A strict G whose effective spec ``validate`` refuses cannot
run on any checker; the preflight says so and refuses to start unless ``--accept-g-unrunnable`` is given, in
which case the G rows are NOT RUN with the diagnostics and only the Docker-free rows (PS0, PS2, PG4, PG5, PNX)
are scored.

Definitions (stated, never silent; each is a resolved ambiguity listed in the build report):

* A **cell** is (formed rule, app): 23 rules x 30 apps (``ok`` and the 29 mutants) = 690. **G** = the findings
  of the rule's generated elements (``form:F<n>:*``), **H** = the findings of the hand-written elements linked to
  the rule in ``handwritten.json`` (an element linked to two rules fails both), both read from the one CD run
  with G; **T** = ``truth.json``. A finding is attributed by its ``rule`` text exactly as ``forms_probes3.rule_of``.
* **PS1** G == H: the share of cells with G == H beside the band 85-100 %; pass rule of PG1: **no cell with
  G != H and H == T**. **PS1t** G == T: pass rule of PG1t: **100 % outside the K6 cells** (the truth file's ``k6``
  flag), **the K7 cells** (a ``db_unchecked:`` note) **and the ``form_gap`` cells** (the truth file's flag: "a
  correct G misses these by construction"); K6, K7 and form_gap cells are listed apart with their G verdict.
* **PS0**: ``forms coverage`` through the library on the set's forms file: formed / not_expressible / english
  counts beside the registered bands (formed 22-45, not_expressible 40-70, ``time`` and ``cross_request`` the
  largest categories), compared rule by rule with the author's classification. REPORTED.
* **PS2**: PT2f on ``pairs.json`` (precision 1.0, recall 1.0 on classes a-d, 0 raises, an item naming a form or
  step outside the pair is not counted; class e reported) **and** the 50-vs-60 pair P9 determined **and** the
  candidates: ``forms contradictions`` on ``candidates/stated_twice`` gives exactly the one class-b item,
  ``limit50`` and ``limit60`` none, and ``lattice spec impact --project limit50 --candidate stated_twice --tiers 23``
  gains exactly that item in ``tier2.determined``.
* **PS3**: ``ok`` + G, CD, 10 times (two scratch roots, random ``PYTHONHASHSEED``): the reports are byte-identical
  after dropping ``timings`` and the container/seconds text of ``notes``; a difference confined to Await timing
  (a path with ``await`` or a value that talks of waiting) is excluded and listed; and ``forms generate`` 10 times
  is byte-identical with no absolute path or instant in it.
* **PSx**: the wall time of CD on ``ok`` with G, every one of the 10 runs (and the cell run of ``ok`` is the first
  of them) <= 180 s; the no-G baseline and the maximum over all apps are reported beside it.

Exit status: 0 the stage completed (rows may be FAIL: the arm is report-only), 1 a row is NOT RUN, 2 refused
(hashes, set, ledger, arguments), 3 the strict G cannot run (preflight), 4 a crash.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
SELF_REL = "dogfood/forms_probes_streak.py"
DATA_REL = "docs/results/data/2026-10-18-typed-conditions-streak-arm"

# ---- constants of the registration (the set, Streak, the gates) --------------------------------------------

STREAK_SHA = "a0679e9044486c98611febcd050a1fd23b6cd756"  # build/streak
SET_TGZ_REL = f"{DATA_REL}/streak-set.tgz"
SET_TGZ_SHA = "5292b05c244ccf70e3af0db96a361c9e243f0926e3745b173c0a512dd73c816b"
SET_MANIFEST_SHA = "f872779237e9b45251410551561de5bde4bd7692eb95f0d2af45913540ec1655"
SET_SIDE_FILES = ("MANIFEST.sha256", "READY")  # in the set, not in its manifest
REQUIRED_FROZEN = (
    SELF_REL,
    "lattice/spec/forms.py",
    "lattice/spec/forms_tier2.py",
    "lattice/spec/check.py",
    "lattice/spec/validate.py",
    "lattice/spec/invariants.py",
    "lattice/spec/model.py",
)
FREEZE3_REL = "docs/results/data/2026-10-18-typed-conditions-round3/freeze3.sha256"
LEDGER = Path.home() / ".lattice" / "spike-h-streak-ledger.json"
PS3_RUNS = 10
BASELINE_RUNS = 3
PSX_LIMIT_S = 180
RUN_TIMEOUT_S = 900
BANDS = {
    "PS0.formed": (22, 45),
    "PS0.not_expressible": (40, 70),
    "PS1.g_eq_h": (0.85, 1.0),
    "PS1t.g_eq_t": (0.90, 1.0),
    "PSx.seconds": (30, 60),
}
GENERATED_RE = re.compile(r"form:(F\d+):|\(form (F\d+)\)")
AWAIT_RE = re.compile(r"await|waited|waiting|polled", re.I)
VARIANTS = ("strict", "drop_invalid")
LABEL_STRICT = "report-only; library frozen at round 3, set development-blind for the library"
LABEL_POST_HOC = "post hoc (labelled): not the registered measurement"
CD_ARGS = [
    "--app", "harness:make_app", "--db", "harness:build_db", "--runtime", "harness:RUNTIME",
    "--login", "harness:login", "--services", "harness:SERVICES", "--database", "mysql",
    "--layers", "CD", "--go-src", ".", "--x-repeats", "5", "--json",
]  # fmt: skip


class RunnerError(Exception):
    """A refusal or a failure of the runner itself (never a score)."""


# ---- small helpers -----------------------------------------------------------------------------------------


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def row(pid: str, what: str, status: str, **kw: Any) -> dict[str, Any]:
    return {"probe": pid, "what": what, "status": status, **kw}


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    e = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "UV_NO_SYNC": "1", **(extra or {})}
    e.pop("ANTHROPIC_API_KEY", None)
    return e


def run(
    argv: Sequence[str], cwd: Path, env: dict[str, str] | None = None, timeout: int = 600
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(  # noqa: S603
            list(argv), cwd=cwd, env=clean_env(env), capture_output=True, text=True,
            timeout=timeout, check=False,
        )  # fmt: skip
        rc, out, err, timed_out = p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired as exc:
        rc, out, err, timed_out = -9, _txt(exc.stdout), _txt(exc.stderr), True
    return {"rc": rc, "out": out, "err": err, "wall_s": round(time.perf_counter() - t0, 3), "timed_out": timed_out}  # fmt: skip


def _txt(x: Any) -> str:
    if x is None:
        return ""
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)


def write_json(path: Path, doc: Any, gz: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text)


def hash_lines(path: Path) -> list[tuple[str, str]]:
    out = []
    for ln in path.read_text().splitlines():
        if ln.strip():
            digest, _, rel = ln.partition("  ")
            out.append((digest.strip(), rel.strip()))
    return out


# ---- hash discipline: this file's own hash, the freeze file, the set, Streak -----------------------------------


def self_sha() -> str:
    return sha256_file(Path(__file__).resolve())


def freeze_check(freeze: Path | None, repo: Path = REPO) -> dict[str, Any]:
    """The freeze file against the tree: it must list this runner (whose sha256 is computed here) and
    ``REQUIRED_FROZEN``, and every listed file must hash as listed. Called unconditionally by every stage that
    scores (and after the run); there is no option that turns it off."""
    problems: list[str] = []
    runner = self_sha()
    entries: dict[str, str] = {}
    if freeze is None or not freeze.is_file():
        problems.append(f"{freeze}: no freeze file")
    else:
        entries = {rel: d for d, rel in hash_lines(freeze)}
    for rel in REQUIRED_FROZEN:
        if entries and rel not in entries:
            problems.append(f"{rel}: not listed in the freeze file")
    for rel, digest in entries.items():
        f = repo / rel
        if not f.is_file():
            problems.append(f"{rel}: missing")
        elif sha256_file(f) != digest:
            problems.append(f"{rel}: hash differs from the freeze file")
    if entries and entries.get(SELF_REL) not in (None, runner):
        problems.append(f"{SELF_REL}: this runner's sha256 {runner} is not the one frozen")
    return {"ok": not problems, "problems": problems, "runner_sha256": runner,
            "freeze_sha256": sha256_file(freeze) if freeze and freeze.is_file() else None,
            "files": len(entries)}  # fmt: skip


def library_vs_freeze3(repo: Path = REPO) -> dict[str, Any]:
    """Informational: is the library still the round-3 freeze (freeze3.sha256)?"""
    p = repo / FREEZE3_REL
    if not p.is_file():
        return {"checked": False}
    moved = [
        rel for d, rel in hash_lines(p)
        if rel.startswith("lattice/") and (not (repo / rel).is_file() or sha256_file(repo / rel) != d)
    ]  # fmt: skip
    return {"checked": True, "library_unchanged_since_round3": not moved, "changed": moved}


def extract_set(tgz: Path, dest: Path) -> Path:
    if sha256_file(tgz) != SET_TGZ_SHA:
        raise RunnerError(f"{tgz}: sha256 is not the registered streak-set.tgz ({SET_TGZ_SHA})")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tgz) as t:
        keep = []
        for m in t.getmembers():
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk():
                raise RunnerError(f"{tgz}: unsafe member {m.name}")
            if p.name.startswith(
                "._"
            ):  # macOS AppleDouble metadata that tar wrote beside each file
                continue
            keep.append(m)
        t.extractall(dest, members=keep, filter="data")
    return dest / "streak"


def verify_set(root: Path) -> dict[str, Any]:
    """MANIFEST.sha256 from inside ``streak/``: its own sha256 is the registered one, every listed file hashes
    as listed, and no file is unlisted (apart from the manifest and READY)."""
    problems: list[str] = []
    mf = root / "MANIFEST.sha256"
    if not mf.is_file():
        return {"ok": False, "problems": ["no MANIFEST.sha256"], "files": 0}
    if sha256_file(mf) != SET_MANIFEST_SHA:
        problems.append(
            f"MANIFEST.sha256: sha256 {sha256_file(mf)} is not the registered {SET_MANIFEST_SHA}"
        )
    listed = hash_lines(mf)
    base = root.resolve()
    for digest, rel in listed:
        f = (root / rel).resolve()
        if base not in f.parents or not f.is_file():
            problems.append(f"{rel}: missing or outside the set")
        elif sha256_file(f) != digest:
            problems.append(f"{rel}: hash mismatch")
    names = {rel for _d, rel in listed}
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(f.relative_to(root))
        if rel not in names and rel not in SET_SIDE_FILES:
            problems.append(f"{rel}: not in the manifest")
    return {"ok": not problems, "problems": problems, "files": len(listed),
            "manifest_sha256": sha256_file(mf)}  # fmt: skip


def read_set(root: Path) -> dict[str, Any]:
    """The files of the set (layout of its README.md) as records. No metric, no verdict."""

    def j(name: str) -> Any:
        return json.loads((root / name).read_text())

    forms_doc, truth, mut, hv = (
        j("forms.json"),
        j("truth.json"),
        j("mutants.json"),
        j("h_verdicts.json"),
    )
    src = {}
    for ln in (root / "source.sha256").read_text().splitlines():
        if ln and not ln.startswith("#"):
            d, _, n = ln.partition("  ")
            src[n.strip()] = d.strip()
    rec = {
        "root": root,
        "forms_json": root / "forms.json",
        "forms": list(forms_doc["forms"]),
        "not_expressible_map": dict(forms_doc.get("not_expressible", {})),
        "classification": j("classification.json"),
        "handwritten": j("handwritten.json")["rules"],
        "mutants": list(mut["mutants"]),
        "rules": list(truth["rules"]),
        "apps": list(truth["apps"]),
        "truth": truth["cells"],
        "h_verdicts": hv,
        "pairs": list(j("pairs.json")["pairs"]),
        "invalid": list(j("invalid.json")["invalid"]),
        "ne": list(j("not_expressible.json")["rules"]),
        "candidates": {n: root / "candidates" / n / "REQUIREMENTS.forms.json" for n in ("limit50", "limit60", "stated_twice")},
        "source_sha": src,
    }  # fmt: skip
    problems = []
    if not rec["apps"] or rec["apps"][0] != "ok":
        problems.append("apps[0] is not 'ok'")
    if set(rec["apps"][1:]) != {m["name"] for m in rec["mutants"]}:
        problems.append("truth apps and mutants.json differ")
    for r in rec["rules"]:
        if r not in rec["handwritten"]:
            problems.append(f"{r}: no hand-written links")
        if r not in rec["truth"] or set(rec["truth"][r]) != set(rec["apps"]):
            problems.append(f"{r}: truth cells do not cover the apps")
    for m in rec["mutants"]:
        if not (root / m["patch"]).is_file():
            problems.append(f"{m['name']}: patch missing")
        elif sha256_file(root / m["patch"]) != m["patch_sha256"]:
            problems.append(f"{m['name']}: patch sha256 differs from mutants.json")
    for n, p in rec["candidates"].items():
        if not p.is_file():
            problems.append(f"candidates/{n}: missing")
    if problems:
        raise RunnerError("the set does not read: " + "; ".join(problems))
    return rec


# ---- the global ledger (once-only scored run) -------------------------------------------------------------


def _ledger_load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    return list(data["runs"])


def ledger_check(path: Path, variant: str) -> str | None:
    """None when a run of ``variant`` may start; else the reason it may not (read-only)."""
    mine = [r for r in _ledger_load(path) if r.get("variant") == variant]
    if not mine:
        return None
    last = mine[-1]
    if last["status"] != "crashed_before_metric":
        return f"the {variant} variant has a ledger entry in status {last['status']!r} (run {last['index']}); a scored run is once only"
    if len(mine) >= 2:
        return f"the {variant} variant has already used its one re-run (after a crash before any metric)"
    return None


def ledger_open(path: Path, variant: str, runner_sha: str, out: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        why = ledger_check(path, variant)
        if why:
            raise RunnerError(why)
        runs = _ledger_load(path)
        index = 1 + sum(1 for r in runs if r.get("variant") == variant)
        runs.append({"variant": variant, "index": index, "runner_sha256": runner_sha, "status": "started",
                     "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "out": str(out)})  # fmt: skip
        _ledger_save(path, runs)
    return index


def ledger_status(path: Path, variant: str, status: str) -> None:
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        runs = _ledger_load(path)
        for r in reversed(runs):
            if r.get("variant") == variant:
                r["status"] = status
                r["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                break
        _ledger_save(path, runs)


def _ledger_save(path: Path, runs: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"schema": "lattice.streak-arm-ledger/1", "runs": runs}, indent=1) + "\n"
    )
    os.replace(tmp, path)


# ---- the Streak tree, the mutants, the build ---------------------------------------------------------------


def archive_tree(repo: Path, ref: str, dest: Path) -> dict[str, Any]:
    """``git archive`` of Streak at ``ref`` (read-only on the repository) into ``dest``."""
    p = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603, S607
    full = p.stdout.strip()
    if p.returncode or full != STREAK_SHA:
        raise RunnerError(
            f"{repo} {ref}: resolves to {full or p.stderr.strip()!r}, not {STREAK_SHA}"
        )
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    tar = subprocess.run(["git", "-C", str(repo), "archive", full], capture_output=True, check=True)  # noqa: S603, S607
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar.stdout, check=True)  # noqa: S603, S607
    return {"commit": full}


def verify_sources(tree: Path, source_sha: dict[str, str]) -> list[str]:
    bad = []
    for name, digest in source_sha.items():
        f = tree / name
        if not f.is_file() or sha256_file(f) != digest:
            bad.append(name)
    return bad


def apply_patch(tree: Path, patch: Path) -> dict[str, Any]:
    chk = run(["git", "apply", "--check", str(patch)], tree, timeout=120)
    if chk["rc"]:
        return {"ok": False, "step": "check", "err": chk["err"][-400:]}
    ap = run(["git", "apply", str(patch)], tree, timeout=120)
    return {"ok": ap["rc"] == 0, "step": "apply", "err": ap["err"][-400:]}


def go_build(go: str, tree: Path, gocache: Path) -> dict[str, Any]:
    env = {"GOCACHE": str(gocache), "PATH": f"{Path(go).parent}:{os.environ.get('PATH', '')}"}
    a = run([go, "build", "./..."], tree, env, timeout=900)
    b = run([go, "build", "-o", "bin/streak-api", "./cmd/server"], tree, env, timeout=900)
    return {"ok": a["rc"] == 0 and b["rc"] == 0, "build_all_rc": a["rc"], "bin_rc": b["rc"],
            "err": (a["err"] + b["err"])[-600:], "wall_s": round(a["wall_s"] + b["wall_s"], 2)}  # fmt: skip


WRAPPER = '''"""Written by dogfood/forms_probes_streak.py (scratch tree only): Streak's SPEC plus the generated elements of
its approved forms (what lattice.spec.forms.check_with_forms runs). A plain `lattice spec check` never reads forms."""
import importlib.util
import json
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("forms_probes_streak", {runner!r})
_m = importlib.util.module_from_spec(_s)
sys.modules["forms_probes_streak"] = _m
_s.loader.exec_module(_m)
from spec import SPEC as _BASE  # noqa: E402

SPEC, _META = _m.effective_for_tree(_BASE, _here, {variant!r})
(_here / "forms_g_meta.json").write_text(json.dumps(_META, sort_keys=True, indent=1, default=str))
'''


def make_tree(
    base: Path, dest: Path, forms_json: Path, patch: Path | None, variant: str
) -> dict[str, Any]:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest)
    shutil.copyfile(forms_json, dest / "REQUIREMENTS.forms.json")
    (dest / "spec_forms.py").write_text(
        WRAPPER.format(runner=str(Path(__file__).resolve()), variant=variant)
    )
    res: dict[str, Any] = {"ok": True}
    if patch is not None:
        res = apply_patch(dest, patch)
    return res


def containers(owner: str) -> list[str]:
    p = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"label=lattice.owner={owner}"],
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603, S607
    return p.stdout.split()


def cleanup_containers(owner: str) -> list[str]:
    """Remove the containers carrying this run's own label (and only those); returns the ids removed."""
    ids = containers(owner)
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, check=False)  # noqa: S603, S607
    return ids


# ---- the library under test (lazy, after sys.path) -------------------------------------------------------------


def lib() -> Any:
    sys.path.insert(0, str(REPO))
    from lattice.spec import forms as F
    from lattice.spec import forms_tier2 as T2

    return F, T2


def load_streak_spec(tree: Path) -> Any:
    """Streak's own SPEC from the archived tree (its spec.py), without leaving the tree on sys.path."""
    sys.path.insert(0, str(tree))
    sys.modules.pop("spec", None)
    try:
        mod_spec = importlib.util.spec_from_file_location("streak_spec", tree / "spec.py")
        assert mod_spec is not None and mod_spec.loader is not None
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(tree))
    return mod.SPEC


def build_g(spec: Any, trace: Any, paths: Any, variant: str) -> tuple[Any, Any, dict[str, Any]]:
    """(effective spec, Generated, meta): the usable forms generated as a whole set (a form that raises on its
    own is recorded and left out, as the frozen scorer does); ``drop_invalid`` also leaves out the generated
    invariants that ``validate`` refuses and records them."""
    from lattice.spec.validate import validate

    F, _T2 = lib()
    fs = F.load_set(trace, paths)
    resolved = F.usable(fs.resolved) if fs is not None else []
    errors: dict[str, str] = {}
    whole_error: str | None = None
    keep = list(resolved)
    try:
        gen = F.generate(spec, keep)
    except Exception as err:  # noqa: BLE001 -- a raise on a form that passed is itself a finding
        whole_error = f"{type(err).__name__}: {err}"
        for r in list(keep):
            try:
                F.generate(spec, [r])
            except Exception as e2:  # noqa: BLE001
                errors[r.form.id] = f"{type(e2).__name__}: {e2}"
                keep.remove(r)
        gen = F.generate(spec, keep)
    base_problems = {(p.code, p.message) for p in validate(spec)}

    def new_problems(sp: Any) -> list[str]:
        return [
            f"{p.code}: {p.message}"
            for p in validate(sp)
            if (p.code, p.message) not in base_problems
        ]

    dropped: dict[str, list[str]] = {}
    if variant == "drop_invalid":
        kept_inv = []
        for inv in gen.invariants:
            bad = new_problems(dataclasses.replace(spec, invariants=(*spec.invariants, inv)))
            if bad:
                dropped[inv.name] = bad
            else:
                kept_inv.append(inv)
        gen = dataclasses.replace(gen, invariants=tuple(kept_inv))
    eff = F.effective_spec(spec, gen)
    meta = {
        "variant": variant,
        "forms_usable": sorted(r.form.id for r in resolved),
        "form_states": {r.form.id: r.state for r in (fs.resolved if fs else [])},
        "forms_that_raise": errors,
        "whole_set_error": whole_error,
        "scenarios": sorted(s.name for s in gen.scenarios),
        "invariants": sorted(i.name for i in gen.invariants),
        "notes": list(gen.notes),
        "dropped_invariants": dropped,
        "validate_problems": new_problems(eff),
        "self_check": F.self_check(spec, gen),
    }
    return eff, gen, meta


def effective_for_tree(base_spec: Any, tree: Path, variant: str) -> tuple[Any, dict[str, Any]]:
    """Used by the wrapper module inside the checker process."""
    from lattice.spec.rules import Trace

    F, _T2 = lib()
    tp = Path(tree) / "spec.trace.json"
    trace = Trace.load(tp)
    eff, _gen, meta = build_g(base_spec, trace, F.forms_paths(tp), variant)
    return eff, meta


# ---- running the checker ------------------------------------------------------------------------------------


def cd_argv(driver: str, spec_ref: str) -> list[str]:
    return [driver, "-m", "lattice.spec", "check", "--spec", spec_ref, *CD_ARGS]


def run_cd(
    driver: str,
    tree: Path,
    with_g: bool,
    owner: str,
    hashseed: str = "0",
    timeout: int = RUN_TIMEOUT_S,
) -> dict[str, Any]:
    """One CD run of a tree: the report (parsed), the wall time, the exit code."""
    env = {
        "PYTHONPATH": f"{tree}:{REPO}", "LATTICE_CONTAINER_OWNER": owner, "PYTHONHASHSEED": hashseed,
    }  # fmt: skip
    r = run(cd_argv(driver, "spec_forms:SPEC" if with_g else "spec:SPEC"), tree, env, timeout)
    rep = None
    try:
        rep = json.loads(r["out"])
    except ValueError:
        rep = None
    return {"rc": r["rc"], "wall_s": r["wall_s"], "timed_out": r["timed_out"], "report": rep, "err_tail": r["err"][-600:]}  # fmt: skip


def slim_run(res: dict[str, Any]) -> dict[str, Any]:
    """What scoring needs of a run: no timings, no cases."""
    rep = res["report"]
    out: dict[str, Any] = {"rc": res["rc"], "wall_s": res["wall_s"], "timed_out": res["timed_out"], "err_tail": res["err_tail"]}  # fmt: skip
    if rep is None:
        return {**out, "ok": False}
    out.update(
        ok=True, verdict=rep.get("verdict"), counts=rep.get("counts"), cases=len(rep.get("cases") or {}),
        total_s=(rep.get("timings") or {}).get("total_s"),
        findings=[
            {"layer": f.get("layer"), "rule": f.get("rule"), "case": f.get("case"), "verdict": f.get("verdict"),
             "violations": [v.get("invariant") for v in (f.get("invariant_violations") or []) if isinstance(v, dict)]}
            for f in rep.get("findings", [])
        ],
    )  # fmt: skip
    return out


_NOTE_MASKS = (
    (re.compile(r"lattice-mysql-[0-9a-f]+"), "lattice-mysql-X"),
    (re.compile(r"\d+(?:\.\d+)? ?(?:ms|s)\b"), "N s"),
)


def canon_report(rep: dict[str, Any]) -> dict[str, Any]:
    """The report without wall-clock fields: ``timings`` dropped, container name and seconds masked in ``notes``."""
    d = copy.deepcopy(rep)
    d.pop("timings", None)
    notes = []
    for n in d.get("notes", []):
        for rx, to in _NOTE_MASKS:
            n = rx.sub(to, str(n))
        notes.append(n)
    d["notes"] = notes
    return d


def json_diff(
    a: Any, b: Any, path: str = "", out: list[tuple[str, Any, Any]] | None = None
) -> list[tuple[str, Any, Any]]:
    out = [] if out is None else out
    if len(out) >= 200:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}/{k}", a.get(k), b.get(k)))
            else:
                json_diff(a[k], b[k], f"{path}/{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((f"{path}[len]", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            json_diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append((path, a, b))
    return out


def classify_diff(diffs: list[tuple[str, Any, Any]]) -> dict[str, Any]:
    """A difference is Await timing when its path names an await or either value talks of waiting."""

    def is_await(d: tuple[str, Any, Any]) -> bool:
        return bool(
            AWAIT_RE.search(d[0]) or AWAIT_RE.search(str(d[1])) or AWAIT_RE.search(str(d[2]))
        )

    other = [d for d in diffs if not is_await(d)]
    return {"differences": len(diffs), "await_timing": len(diffs) - len(other),
            "other": [[p, str(x)[:120], str(y)[:120]] for p, x, y in other[:20]], "await_only": not other}  # fmt: skip


# ---- attribution of findings: the frozen scorer's rule_of, plus the author's element names -------------------


def finding_elements(f: dict[str, Any], sent2name: dict[str, str]) -> set[str]:
    """``scenario:<name>`` / ``invariant:<name>`` elements a finding names (the set author's convention for
    ``h_verdicts.json``): the rule text, a layer-C invariant sentence, and every invariant violation."""
    out: set[str] = set()
    r = f.get("rule") or ""
    if r.startswith("scenario: "):
        out.add("scenario:" + r[len("scenario: ") :])
    elif r.startswith("invariant: "):
        out.add("invariant:" + r[len("invariant: ") :])
    elif f.get("layer") == "C" and r in sent2name:
        out.add("invariant:" + sent2name[r])
    for v in f.get("violations") or []:
        if v:
            out.add("invariant:" + v)
    return out


def attribute(findings: list[dict[str, Any]], ctx: dict[str, Any]) -> dict[str, Any]:
    """Per rule: the layers of the failing generated elements (G) and of the failing hand-written elements (H)."""
    g: dict[str, set[str]] = {}
    h: dict[str, set[str]] = {}
    h_in_g: set[str] = set()
    unattributed: list[str] = []
    elements: set[str] = set()
    for f in findings:
        text = str(f.get("rule") or "")
        case_generated = str(f.get("case") or "").startswith("form:")
        els = finding_elements(f, ctx["sent2name"])
        elements |= els
        g_ids = set()
        for e in els | {text}:
            mm = GENERATED_RE.search(e)
            if mm:
                g_ids.add(mm.group(1) or mm.group(2))
        for fid in g_ids:
            rule = ctx["form_rule"].get(fid)
            if rule:
                g.setdefault(rule, set()).add(str(f.get("layer")))
            else:
                unattributed.append(text)
        for e in els:
            if GENERATED_RE.search(e):
                continue
            for rule in ctx["elem_rules"].get(e, ()):
                h.setdefault(rule, set()).add(str(f.get("layer")))
                if case_generated:
                    h_in_g.add(e)
    return {"g": {k: sorted(v) for k, v in g.items()}, "h": {k: sorted(v) for k, v in h.items()},
            "elements": sorted(elements), "h_elements_seen_in_generated_cases": sorted(h_in_g),
            "unattributed": sorted(set(unattributed))}  # fmt: skip


def make_ctx(rec: dict[str, Any], eff_spec: Any, form_rule: dict[str, str]) -> dict[str, Any]:
    elem_rules: dict[str, list[str]] = {}
    for rule, hw in rec["handwritten"].items():
        for e in hw["elements"]:
            elem_rules.setdefault(e, []).append(rule)
    return {
        "sent2name": {i.sentence: i.name for i in eff_spec.invariants},
        "form_rule": form_rule,
        "elem_rules": elem_rules,
    }


# ---- scoring (pure functions of the raw record and the set) --------------------------------------------------


def score_cells(rec: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    """PS1, PS1t, PS1o, PS1k, PS1layer, PS-H from the per-app runs with G."""
    if not raw.get("g_runnable", False):
        why = (
            "; ".join(raw.get("g_meta", {}).get("validate_problems", [])[:4])
            or "the strict G cannot run"
        )
        names = ("PS1", "PS1t", "PS1o", "PS1k", "PS1layer", "PS-H")
        return [
            row(n, "needs the CD runs with G", "NOT RUN", reason=f"G cannot run: {why}")
            for n in names
        ]
    ctx = raw["ctx"]
    apps = rec["apps"]
    runs = raw["apps"]
    missing = [a for a in apps if not runs.get(a, {}).get("run", {}).get("ok")]
    k6_truth = {r for r in rec["rules"] if any(rec["truth"][r][a].get("k6") for a in apps)}
    k7_rules = {
        ctx["form_rule"][m.group(1)] for n in raw["g_meta"]["notes"]
        if (m := re.match(r"(F\d+): db_unchecked:", n)) and m.group(1) in ctx["form_rule"]
    }  # fmt: skip
    cells = []
    attr_by_app = {a: attribute(runs[a]["run"]["findings"], ctx) for a in apps if a not in missing}
    for a in apps:
        if a in missing:
            continue
        at = attr_by_app[a]
        for r in rec["rules"]:
            t = rec["truth"][r][a]
            hv = rec["h_verdicts"]["cells"].get(r, {}).get(a, {}).get("verdict")
            g, h = r in at["g"], r in at["h"]
            cells.append({
                "rule": r, "app": a, "G": g, "H": h, "T": bool(t["violates"]),
                "k6": bool(t.get("k6")), "form_gap": bool(t.get("form_gap")),
                "k7": r in k7_rules and bool(t["violates"]) and not g,
                "Glayers": at["g"].get(r, []), "Hlayers": at["h"].get(r, []),
                "H_author": None if hv is None else hv == "fail",
            })  # fmt: skip
    unattributed = sorted({u for at in attr_by_app.values() for u in at["unattributed"]})

    def slim(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {k: c[k] for k in ("rule", "app", "G", "H", "T", "k6", "k7", "form_gap")} for c in cs
        ]

    n = len(cells)
    expect = len(rec["rules"]) * len(apps)
    status_missing = "NOT RUN" if missing else None
    pg1_bad = [c for c in cells if c["G"] != c["H"] and c["H"] == c["T"]]
    ghe = sum(1 for c in cells if c["G"] == c["H"])
    exempt = lambda c: c["k6"] or c["k7"] or c["form_gap"]  # noqa: E731
    pg1t_bad = [c for c in cells if c["G"] != c["T"] and not exempt(c)]
    ok_cells = [c for c in cells if c["app"] == "ok"]
    ok_bad = [c for c in ok_cells if c["G"] != c["T"]]
    ok_g = {c["rule"] for c in ok_cells if c["G"]}
    ok_h = {c["rule"] for c in ok_cells if c["H"]}
    g_k = {c["app"] for c in cells if c["app"] != "ok" and c["G"] and c["rule"] not in ok_g}
    h_k = {c["app"] for c in cells if c["app"] != "ok" and c["H"] and c["rule"] not in ok_h}
    both = [c for c in cells if c["G"] and c["H"]]
    h_d = [c for c in both if "D" in c["Hlayers"]]
    rule_flags = {"k6_rules_truth": sorted(k6_truth), "k7_rules": sorted(k7_rules)}
    common = {
        "cells": n,
        "cells_expected": expect,
        "apps_missing": missing,
        "variant": raw["variant"],
    }
    out = [
        row("PS1", "G == H per cell (PG1): no cell with G != H and H == T", status_missing or ("PASS" if not pg1_bad else "FAIL"),
            g_eq_h=ghe, g_eq_h_share=(ghe / n) if n else None, predicted_band=list(BANDS["PS1.g_eq_h"]),
            in_band=bool(n) and BANDS["PS1.g_eq_h"][0] <= ghe / n <= BANDS["PS1.g_eq_h"][1],
            g_ne_h=slim([c for c in cells if c["G"] != c["H"]]), bad_gne_h_and_h_eq_t=slim(pg1_bad),
            generated_findings_not_attributed=unattributed, **common),
        row("PS1t", "G == T per cell (PG1t): 100 % outside the K6, K7 and form_gap cells", status_missing or ("PASS" if not pg1t_bad else "FAIL"),
            g_eq_t=sum(1 for c in cells if c["G"] == c["T"]), g_eq_t_share=(sum(1 for c in cells if c["G"] == c["T"]) / n) if n else None,
            predicted_band=list(BANDS["PS1t.g_eq_t"]), bad_outside_exempt=slim(pg1t_bad),
            k6_cells=len([c for c in cells if c["k6"]]), k6_cells_g_fail=slim([c for c in cells if c["k6"] and c["G"]]),
            k6_misses=slim([c for c in cells if c["k6"] and c["G"] != c["T"]]),
            form_gap_cells=slim([c for c in cells if c["form_gap"]]),
            k7_cells=slim([c for c in cells if c["k7"]]), **rule_flags, **common),
        row("PS1o", "on ok, G fails exactly the rules T marks violated (PG1o)", status_missing or ("PASS" if not ok_bad else "FAIL"),
            g_fail_on_ok=sorted(ok_g), h_fail_on_ok=sorted(ok_h), t_violated_on_ok=sorted(c["rule"] for c in ok_cells if c["T"]),
            bad=slim(ok_bad), **common),
        row("PS1k", "G kills (flips vs ok, formed rules) a superset of H's (PG1k)", status_missing or ("PASS" if h_k <= g_k else "FAIL"),
            g_kills=sorted(g_k), h_kills=sorted(h_k), g_only=sorted(g_k - h_k), h_only=sorted(h_k - g_k), apps_non_ok=len(apps) - 1, **common),
        row("PS1layer", "layer of the failing check where both fail (PG2); layer-D agreement", "REPORTED",
            cells_both_fail=len(both), same_layer_sets=sum(1 for c in both if c["Glayers"] == c["Hlayers"]),
            h_includes_d=len(h_d), both_include_d=sum(1 for c in h_d if "D" in c["Glayers"]),
            layer_d_agreement=(sum(1 for c in h_d if "D" in c["Glayers"]) / len(h_d)) if h_d else None, **common),
    ]  # fmt: skip
    hdiff = [c for c in cells if c["H_author"] is not None and c["H"] != c["H_author"]]
    out.append(
        row("PS-H", "H as run here (CD, with G in the same run) against the author's h_verdicts.json (ACD, no G)", "REPORTED",
            cells_compared=len([c for c in cells if c["H_author"] is not None]), differ=slim(hdiff),
            h_elements_seen_in_generated_cases={a: at["h_elements_seen_in_generated_cases"] for a, at in attr_by_app.items() if at["h_elements_seen_in_generated_cases"]},
            h_vs_truth_disagree=len([c for c in cells if c["H"] != c["T"]]), **common)
    )  # fmt: skip
    return out


def check_set_forms(rec: dict[str, Any], spec: Any, trace: Any) -> list[dict[str, Any]]:
    """PS-PG5 (invalid forms refused, valid forms accepted), PS-PNX (not-expressible attempts refused)."""
    F, _T2 = lib()
    forms = [F._parse_form(e, i) for i, e in enumerate(rec["forms"])]  # noqa: SLF001
    diags = F.check_forms(spec, trace, forms)
    accepted, exact, wrong = [], [], []
    for x in rec["invalid"]:
        fs = [F._parse_form(e, 0) for e in x["forms"]]  # noqa: SLF001
        got = sorted({d.code for d in F.check_forms(spec, trace, fs)})
        if not got:
            accepted.append(x["id"])
        elif x["code"] in got:
            exact.append(x["id"])
        else:
            wrong.append({"id": x["id"], "want": x["code"], "got": got})
    acc, wrong_code, tried = [], [], 0
    for x in rec["ne"]:
        a = x.get("attempt")
        if not a:
            continue
        tried += 1
        f = F._parse_form(
            {
                "id": "F901",
                "rule": "R1",
                "anchor": "0" * 64,
                "kind": a["kind"],
                "form": a["form"],
                "status": "approved",
                "author": "hand",
            },
            0,
        )  # noqa: SLF001
        got = sorted({d.code for d in F.check_forms(spec, None, [f])})
        if not got:
            acc.append(x["id"])
        elif not set(got) & {"F001", "F017"}:
            wrong_code.append(
                {"id": x["id"], "got": got, "nonliteral_outcome": x.get("outcome_db_nonliteral")}
            )
    valid_refused = [d.render() for d in diags]
    return [
        row("PS-PG5", "invalid.json refused; the set's valid forms accepted (PG5)", "PASS" if not accepted and not valid_refused else "FAIL",
            invalid=len(rec["invalid"]), accepted_invalid=accepted, exact_code=len(exact), codes_not_exact=wrong,
            valid_forms=len(forms), valid_refused=valid_refused),
        row("PS-PNX", "not-expressible attempts refused with F001 or F017 (PNX)", "PASS" if not acc and not wrong_code else "FAIL",
            rules=len(rec["ne"]), with_attempt=tried, accepted=acc, refused_with_other_code=wrong_code),
    ]  # fmt: skip


def score_coverage(rec: dict[str, Any], trace: Any, paths: Any) -> dict[str, Any]:
    """PS0: ``forms coverage`` through the library against the author's classification and the bands."""
    F, _T2 = lib()
    fs = F.load_set(trace, paths)
    cov = F.coverage(trace, fs)
    counts = cov["counts"]
    scope = cov["scope"]
    ne_total = sum(v for k, v in counts.items() if k == "not_expressible")
    formed = counts.get("formed", 0)
    by_cat: dict[str, int] = {}
    for x in cov["rules"]:
        if x["state"].startswith("not_expressible:"):
            c = x["state"].split(":", 1)[1]
            by_cat[c] = by_cat.get(c, 0) + 1
    author = {c["rule"]: c["status"] for c in rec["classification"]["rules"]}
    lib_state = {x["rule"]: x["state"] for x in cov["rules"]}
    mismatch = sorted(r for r in set(author) | set(lib_state) if author.get(r) != lib_state.get(r))
    top2 = [k for k, _v in sorted(by_cat.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
    partial = [
        c["rule"]
        for c in rec["classification"]["rules"]
        if c.get("status") == "formed" and c.get("coverage") == "partial"
    ]
    full = [
        c["rule"]
        for c in rec["classification"]["rules"]
        if c.get("status") == "formed" and c.get("coverage") == "full"
    ]
    lo, hi = BANDS["PS0.formed"]
    nlo, nhi = BANDS["PS0.not_expressible"]
    return row("PS0", "forms coverage on Streak's in-scope rules (146): formed / not_expressible / english", "REPORTED",
               scope=scope, counts=counts, formed=formed, formed_share=(formed / scope) if scope else None,
               formed_band=[lo, hi], formed_in_band=lo <= formed <= hi, not_expressible=ne_total, not_expressible_band=[nlo, nhi],
               not_expressible_in_band=nlo <= ne_total <= nhi, not_expressible_by_category=dict(sorted(by_cat.items())),
               largest_two_categories=top2, time_and_cross_request_are_the_largest=set(top2) == {"time", "cross_request"},
               english=counts.get("english", 0), formed_with_full_coverage=full, formed_with_partial_coverage=len(partial),
               strict_reading_formed=len(full), library_vs_author_classification_mismatches=mismatch,
               forms_usable=len(F.usable(fs.resolved)) if fs else 0, form_states=sorted({r.state for r in fs.resolved}) if fs else [])  # fmt: skip


def score_pairs(rec: dict[str, Any], spec: Any) -> dict[str, Any]:
    """PT2f on pairs.json (the frozen scorer's counting, incl. the items_outside_the_pair rule), then P9."""
    from lattice.spec.model import Scenario, Step

    F, T2 = lib()
    res = []
    for p in rec["pairs"]:
        fs = [F._parse_form(e, 0) for e in p["forms"]]  # noqa: SLF001
        extra: list[Any] = []
        named: set[str] = set()
        hw = p.get("handwritten")
        if hw:
            s = {"name": hw["name"], **hw["scenario"]}
            extra.append(Scenario(s["name"], s.get("given"), tuple(Step(**t) for t in s["steps"])))
            named.add(f"scenario:{hw['name']}")
        sp = dataclasses.replace(spec, scenarios=(*spec.scenarios, *extra)) if extra else spec
        if len({f.id for f in fs}) < len(fs):
            fs[1] = dataclasses.replace(fs[1], id="F9999")
        err, notes, items, out_of_pair = None, [], [], 0
        try:
            got = T2.contradictions(sp, fs, notes)
            allowed = {f.id for f in fs} | named
            items = [i for i in got if all(x in allowed for x in i["forms"])]
            out_of_pair = len(got) - len(items)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        res.append({"id": p["id"], "class": p["class"], "expect": {"contradictory": "contradict"}.get(p["expect"], p["expect"]),
                    "items": items, "error": err, "notes": notes, "out_of_pair": out_of_pair, "forms": [f.id for f in fs]})  # fmt: skip
    tp = [r for r in res if r["expect"] == "contradict" and r["items"]]
    fn = [r for r in res if r["expect"] == "contradict" and not r["items"]]
    fp = [r for r in res if r["expect"] == "compatible" and r["items"]]
    tn = [r for r in res if r["expect"] == "compatible" and not r["items"]]

    def by_class(cs: str) -> dict[str, int]:
        sub = [r for r in res if r["class"] in cs]
        return {"contradict": sum(r["expect"] == "contradict" for r in sub),
                "found": sum(r["expect"] == "contradict" and bool(r["items"]) for r in sub),
                "compatible": sum(r["expect"] == "compatible" for r in sub),
                "false_pos": sum(r["expect"] == "compatible" and bool(r["items"]) for r in sub)}  # fmt: skip

    rabcd = by_class("abcd")
    p9 = next((r for r in res if r["id"] == "P9"), None)
    return {
        "pairs": len(res), "true_pos": len(tp), "false_neg": [r["id"] for r in fn],
        "false_pos": [{"id": r["id"], "items": [i["id"] for i in r["items"]]} for r in fp], "true_neg": len(tn),
        "errors": [{"id": r["id"], "error": r["error"]} for r in res if r["error"]],
        "undecided": [{"id": r["id"], "notes": r["notes"]} for r in res if r["notes"]],
        "items_outside_the_pair": [{"id": r["id"], "n": r["out_of_pair"]} for r in res if r["out_of_pair"]],
        "precision": (len(tp) / (len(tp) + len(fp))) if tp or fp else None,
        "recall_abcd": f"{rabcd['found']}/{rabcd['contradict']}", "recall_abcd_all": rabcd["found"] == rabcd["contradict"],
        "per_class": {c: by_class(c) for c in "abcde"},
        "p9": None if p9 is None else {"found": bool(p9["items"]), "items": [{"id": i["id"], "class": i["class"], "forms": i["forms"], "witness": i["witness"]} for i in p9["items"]], "forms": p9["forms"]},
    }  # fmt: skip


def candidate_cli(rec: dict[str, Any], base_tree: Path, scratch: Path) -> dict[str, Any]:
    """The 50 / 60 candidates through the CLI: ``forms contradictions`` on each, and ``impact`` limit50 -> stated_twice."""
    dirs = {}
    for n, p in rec["candidates"].items():
        d = scratch / "cand" / n
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(base_tree, d)
        shutil.copyfile(p, d / "REQUIREMENTS.forms.json")
        dirs[n] = d
    out: dict[str, Any] = {}
    for n, d in dirs.items():
        r = run([sys.executable, "-m", "lattice.spec", "forms", "contradictions", "--trace", str(d / "spec.trace.json"), "--spec", "spec:SPEC"],
                d, {"PYTHONPATH": f"{d}:{REPO}"}, 300)  # fmt: skip
        try:
            items = json.loads(r["out"])["items"]
        except (ValueError, KeyError):
            items = None
        out[n] = {"rc": r["rc"], "items": None if items is None else [{"id": i["id"], "class": i["class"], "forms": i["forms"], "witness": i["witness"]} for i in items], "err": r["err"][-300:]}  # fmt: skip
    imp = run([sys.executable, "-m", "lattice.spec", "impact", "--project", str(dirs["limit50"]), "--candidate", str(dirs["stated_twice"]),
               "--tiers", "23", "--json", "--no-flows", "--baseline", "none"], REPO, {"PYTHONPATH": str(REPO)}, 300)  # fmt: skip
    try:
        det = json.loads(imp["out"])["tier2"]["determined"]
        forms_items = [d for d in det if d.get("kind") == "form_contradiction"]
        out["impact"] = {"rc": imp["rc"], "form_items": [{"class": d["class"], "forms": d["forms"], "witness": d["witness"], "tier": d.get("tier")} for d in forms_items], "determined_total": len(det)}  # fmt: skip
    except (ValueError, KeyError):
        out["impact"] = {"rc": imp["rc"], "form_items": None, "err": imp["err"][-300:]}
    return out


def score_ps2(pairs: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    p9_ok = bool(pairs["p9"] and pairs["p9"]["found"])
    twice = cand.get("stated_twice", {}).get("items")
    twice_ok = (
        twice is not None
        and len(twice) == 1
        and twice[0]["class"] == "b"
        and sorted(twice[0]["forms"]) == ["F29", "F44"]
    )
    single_ok = all(cand.get(n, {}).get("items") == [] for n in ("limit50", "limit60"))
    imp = cand.get("impact", {}).get("form_items")
    impact_ok = (
        imp is not None
        and len(imp) == 1
        and imp[0]["class"] == "b"
        and sorted(imp[0]["forms"]) == ["F29", "F44"]
        and imp[0].get("tier") == 2
    )
    pt2f = pairs["precision"] == 1.0 and pairs["recall_abcd_all"] and not pairs["errors"]
    ok = pt2f and p9_ok and twice_ok and single_ok and impact_ok
    return row("PS2", "PT2f on Streak pairs; the 50-active-habit limit stated twice (50 and 60) is determined", "PASS" if ok else "FAIL",
               pt2f_pass=pt2f, p9_determined=p9_ok, stated_twice_exactly_the_50_60_item=twice_ok, single_limit_candidates_clean=single_ok,
               impact_cli_gains_exactly_that_item_in_tier2=impact_ok, pairs=pairs, candidates=cand)  # fmt: skip


def score_ps3_psx(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if not raw.get("g_runnable", False):
        why = (
            "; ".join(raw.get("g_meta", {}).get("validate_problems", [])[:4])
            or "the strict G cannot run"
        )
        return [
            row(n, "needs the CD runs with G", "NOT RUN", reason=f"G cannot run: {why}")
            for n in ("PS3", "PSx")
        ]
    runs = raw["ps3_runs"]
    bad_runs = [r["i"] for r in runs if not r.get("ok")]
    gen = raw["ps3_generate"]
    diffs = [r["diff_vs_first"] for r in runs[1:] if r.get("ok") and r.get("diff_vs_first")]
    identical = sum(1 for d in diffs if d["differences"] == 0)
    await_only = sum(1 for d in diffs if d["differences"] and d["await_only"])
    other = [d for d in diffs if d["differences"] and not d["await_only"]]
    n_ok = len(runs) - len(bad_runs)
    gen_ok = len(gen["distinct"]) == 1 and "ERR" not in gen["distinct"] and not gen["leaks"]
    ps3 = (
        "NOT RUN" if bad_runs else ("PASS" if not other and n_ok == PS3_RUNS and gen_ok else "FAIL")
    )
    walls = [r["wall_s"] for r in runs if r.get("ok")]
    base = [b["wall_s"] for b in raw["baseline"] if b.get("ok")]
    all_walls = {
        a: v["run"]["wall_s"] for a, v in raw["apps"].items() if v.get("run", {}).get("ok")
    }
    over = sorted(a for a, w in all_walls.items() if w > PSX_LIMIT_S)
    totals = [r.get("total_s") for r in runs if r.get("total_s") is not None]
    return [
        row("PS3", "CD with G 10x, byte-identical except Await timing (PG3)", ps3,
            runs=len(runs), runs_ok=n_ok, runs_not_ok=bad_runs, identical_to_first=identical, differ_await_timing_only=await_only,
            differ_other=other[:5], generate_distinct_outputs=len(gen["distinct"]), generate_runs=gen["runs"], path_or_time_leaks=gen["leaks"],
            verdicts=sorted({r.get("verdict") for r in runs if r.get("ok")}), findings_per_run=[len(r.get("findings", [])) for r in runs if r.get("ok")]),
        row("PSx", "wall time of CD on S with G; gate <= 180 s", "NOT RUN" if bad_runs else ("PASS" if walls and max(walls) <= PSX_LIMIT_S else "FAIL"),
            wall_s_with_g=walls, max_s=max(walls) if walls else None, median_s=statistics.median(walls) if walls else None,
            checker_total_s_with_g=totals, wall_s_no_g_baseline=base, median_no_g_s=statistics.median(base) if base else None,
            predicted_band_s=list(BANDS["PSx.seconds"]), in_predicted_band=bool(walls) and BANDS["PSx.seconds"][0] <= statistics.median(walls) <= BANDS["PSx.seconds"][1],
            gate_s=PSX_LIMIT_S, all_apps_wall_s_max=max(all_walls.values()) if all_walls else None, apps_over_gate=over,
            note="gated on the clean-app (ok) runs; mutant runs are reported (a failing Await waits out its 30 s window)"),
    ]  # fmt: skip


# ---- the stage ------------------------------------------------------------------------------------------------


def load_trace_paths(tree: Path) -> tuple[Any, Any]:
    from lattice.spec.rules import Trace

    F, _T2 = lib()
    tp = tree / "spec.trace.json"
    return Trace.load(tp), F.forms_paths(tp)


def preflight(rec: dict[str, Any], base_tree: Path, variant: str) -> dict[str, Any]:
    """Docker-free: Streak's spec, the usable forms, G, and whether the effective spec validates."""
    spec = load_streak_spec(base_tree)
    shutil.copyfile(rec["forms_json"], base_tree / "REQUIREMENTS.forms.json")
    trace, paths = load_trace_paths(base_tree)
    eff, gen, meta = build_g(spec, trace, paths, variant)
    form_rule = {}
    F, _T2 = lib()
    fs = F.load_set(trace, paths)
    for r in F.usable(fs.resolved) if fs else []:
        if r.form.id not in meta["forms_that_raise"]:
            form_rule[r.form.id] = r.rule
    return {"spec": spec, "trace": trace, "paths": paths, "eff": eff, "gen": gen, "meta": meta,
            "g_runnable": not meta["validate_problems"], "form_rule": form_rule}  # fmt: skip


def collect_raw(
    args: argparse.Namespace,
    rec: dict[str, Any],
    pre: dict[str, Any],
    base_tree: Path,
    scratch: Path,
    out: Path,
) -> dict[str, Any]:
    """All the Docker work: builds and CD runs. No metric is computed here."""
    variant = args.g_variant
    raw: dict[str, Any] = {
        "schema": "lattice.streak-arm-raw/1", "variant": variant, "g_runnable": pre["g_runnable"],
        "g_meta": pre["meta"], "ctx": make_ctx(rec, pre["eff"], pre["form_rule"]),
        "forms_sha256": sha256_file(rec["forms_json"]), "streak": STREAK_SHA, "apps": {}, "baseline": [], "ps3_runs": [],
    }  # fmt: skip
    if not pre["g_runnable"]:
        return raw
    rawdir = out / "raw"
    gocache = scratch / "gocache"
    trees = scratch / "trees"
    owner = args.owner
    patches = {m["name"]: rec["root"] / m["patch"] for m in rec["mutants"]}

    def build(name: str, root: str) -> tuple[Path, dict[str, Any]]:
        tree = trees / f"{name}.{root}"
        st = make_tree(base_tree, tree, rec["forms_json"], patches.get(name), variant)
        info: dict[str, Any] = {"patch_applied": st}
        if st["ok"]:
            info["build"] = go_build(args.go, tree, gocache)
        return tree, info

    def drop(tree: Path) -> None:
        if not args.keep_trees and tree.parent == trees and tree.exists():
            shutil.rmtree(tree)

    def one(tree: Path, with_g: bool, hashseed: str, tag: str) -> dict[str, Any]:
        res = run_cd(args.driver_python, tree, with_g, owner, hashseed)
        left = cleanup_containers(owner)
        if res["report"] is not None:
            write_json(rawdir / f"{tag}.json.gz", res["report"], gz=True)
        s = slim_run(res)
        s["containers_removed_after"] = left
        print(
            f"  {tag}: rc={res['rc']} wall={res['wall_s']}s findings={len(s.get('findings', []))}",
            flush=True,
        )
        return s

    print("ok trees (two roots) and the no-G baseline", flush=True)
    ok_a, info_a = build("ok", "a")
    ok_b, info_b = build("ok", "b")
    raw["ok_builds"] = {"a": info_a, "b": info_b}
    if not (info_a.get("build", {}).get("ok") and info_b.get("build", {}).get("ok")):
        raise RunnerError(f"the clean app does not build: {info_a} {info_b}")
    for i in range(BASELINE_RUNS):
        s = one(ok_a, False, "0", f"baseline-nog-{i + 1}")
        raw["baseline"].append(s)
    rng = random.Random(20261018)
    first: dict[str, Any] | None = None
    for i in range(PS3_RUNS):
        tree = ok_a if i % 2 == 0 else ok_b
        seed = str(rng.randrange(1, 2**31))
        res = run_cd(args.driver_python, tree, True, owner, seed)
        left = cleanup_containers(owner)
        s = slim_run(res)
        s.update(
            i=i + 1, root="a" if i % 2 == 0 else "b", hashseed=seed, containers_removed_after=left
        )
        if res["report"] is not None:
            canon = canon_report(res["report"])
            s["canon_sha256"] = sha256_text(json.dumps(canon, sort_keys=True))
            write_json(rawdir / f"ps3-{i + 1}.json.gz", res["report"], gz=True)
            if first is None:
                first = canon
            else:
                s["diff_vs_first"] = classify_diff(json_diff(first, canon))
        raw["ps3_runs"].append(s)
        print(
            f"  ps3-{i + 1}: rc={res['rc']} wall={res['wall_s']}s findings={len(s.get('findings', []))}",
            flush=True,
        )
    raw["apps"]["ok"] = {"build": info_a, "run": raw["ps3_runs"][0]}
    # forms generate x10 (random hash seed, two roots)
    gens: set[str] = set()
    leaks = []
    for i in range(PS3_RUNS):
        tree = ok_a if i % 2 == 0 else ok_b
        r = run([sys.executable, "-m", "lattice.spec", "forms", "generate", "--trace", str(tree / "spec.trace.json"), "--spec", "spec:SPEC"],
                tree, {"PYTHONPATH": f"{tree}:{REPO}", "PYTHONHASHSEED": str(rng.randrange(1, 2**31))}, 300)  # fmt: skip
        gens.add(r["out"] if r["rc"] == 0 else f"ERR {r['err'][-200:]}")
        if re.search(
            r"/(tmp|home|Users|private|var)/|\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?!Z)", r["out"]
        ):
            leaks.append(i + 1)
    distinct = sorted({"ERR" if g.startswith("ERR") else sha256_text(g) for g in gens})
    raw["ps3_generate"] = {"runs": PS3_RUNS, "distinct": distinct, "leaks": leaks}
    drop(ok_a)
    drop(ok_b)
    for m in rec["mutants"]:
        name = m["name"]
        print(f"mutant {name}", flush=True)
        tree, info = build(name, "a")
        entry: dict[str, Any] = {"build": info}
        if info.get("build", {}).get("ok"):
            entry["run"] = one(tree, True, "0", f"cd-g-{name}")
        else:
            entry["run"] = {
                "ok": False,
                "rc": None,
                "wall_s": 0,
                "err_tail": "did not build or apply",
            }
        raw["apps"][name] = entry
        drop(tree)
    raw["containers_left"] = containers(owner)
    return raw


def score_all(
    rec: dict[str, Any],
    raw: dict[str, Any],
    base_tree: Path,
    scratch: Path,
    spec: Any,
    trace: Any,
    paths: Any,
) -> list[dict[str, Any]]:
    rows = [score_coverage(rec, trace, paths)]
    rows += score_cells(rec, raw)
    pairs = score_pairs(rec, spec)
    rows.append(score_ps2(pairs, candidate_cli(rec, base_tree, scratch)))
    rows += score_ps3_psx(raw)
    rows += check_set_forms(rec, spec, trace)
    g = raw.get("g_meta", {})
    rows.append(row("PS-PG4", "generated invariants compile (sqlite, mysql), scenarios validate, 0 L011/L012, the effective spec validates (PG4)",
                    "PASS" if not g.get("self_check") and not g.get("validate_problems") and not g.get("forms_that_raise") and not g.get("dropped_invariants") else "FAIL",
                    variant=raw.get("variant"), self_check=g.get("self_check"), validate_problems=g.get("validate_problems"),
                    forms_that_raise=g.get("forms_that_raise"), whole_set_error=g.get("whole_set_error"),
                    generated_scenarios=len(g.get("scenarios", [])), generated_invariants=len(g.get("invariants", [])),
                    dropped_invariants=g.get("dropped_invariants"), notes=g.get("notes")))  # fmt: skip
    return rows


def label_rows(
    rows: list[dict[str, Any]], variant: str, post_hoc: bool, extra: dict[str, Any]
) -> list[dict[str, Any]]:
    label = LABEL_POST_HOC if (post_hoc or variant != "strict") else LABEL_STRICT
    for r in rows:
        r["label"] = label
        r.update(extra)
    return rows


def plan(rec: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    """The inputs of every row (the dry run)."""
    drv = args.driver_python or "<--driver-python>"
    n_rules, apps = len(rec["rules"]), rec["apps"]
    cdg = " ".join(cd_argv(drv, "spec_forms:SPEC"))
    cd = " ".join(cd_argv(drv, "spec:SPEC"))
    classes = {c: sum(1 for p in rec["pairs"] if p["class"] == c) for c in "abcde"}
    return [
        row(
            "PS0",
            "coverage on the in-scope rules",
            "PLANNED",
            inputs={
                "forms_file": "forms.json -> REQUIREMENTS.forms.json beside spec.trace.json",
                "forms": len(rec["forms"]),
                "formed_rules": len({f["rule"] for f in rec["forms"]}),
                "not_expressible_map": len(rec["not_expressible_map"]),
                "classification_rules": len(rec["classification"]["rules"]),
                "scope": rec["classification"]["scope"],
            },
            compares=[
                "formed 22-45",
                "not_expressible 40-70",
                "time and cross_request the largest",
            ],
            docker=False,
        ),
        row(
            "PS1",
            "G == H per cell",
            "PLANNED",
            inputs={
                "cells": f"{n_rules} rules x {len(apps)} apps = {n_rules * len(apps)}",
                "apps": apps,
                "H": f"handwritten.json ({len(rec['handwritten'])} rules' linked elements)",
                "G": "form:F<n>:* findings of the CD run with G",
                "T": "truth.json",
                "H_author": "h_verdicts.json (cross-check)",
                "mutant_patches": {
                    m["name"]: {
                        "patch": m["patch"],
                        "sha256": m["patch_sha256"],
                        "rules": m["rules"],
                    }
                    for m in rec["mutants"]
                },
            },
            command=f"cd <tree>; {cdg}",
            docker=f"{len(apps) - 1} runs (+ the ok run shared with PS3)",
        ),
        row(
            "PS1t",
            "G == T per cell",
            "PLANNED",
            inputs={
                "truth": "truth.json",
                "k6_cells": sum(
                    1 for r in rec["rules"] for a in apps if rec["truth"][r][a].get("k6")
                ),
                "form_gap_cells": [
                    [r, a] for r in rec["rules"] for a in apps if rec["truth"][r][a].get("form_gap")
                ],
                "violated_cells": sum(
                    1 for r in rec["rules"] for a in apps if rec["truth"][r][a]["violates"]
                ),
            },
            docker="same runs as PS1",
        ),
        row(
            "PS2",
            "contradictions on pairs; the 50-vs-60 pair",
            "PLANNED",
            inputs={
                "pairs": len(rec["pairs"]),
                "by_class": classes,
                "contradictory": sum(1 for p in rec["pairs"] if p["expect"] == "contradictory"),
                "compatible": sum(1 for p in rec["pairs"] if p["expect"] == "compatible"),
                "p9": [f["id"] for p in rec["pairs"] if p["id"] == "P9" for f in p["forms"]],
                "candidates": sorted(rec["candidates"]),
            },
            commands=[
                "forms contradictions (per candidate)",
                "lattice.spec impact --project limit50 --candidate stated_twice --tiers 23 --json --no-flows --baseline none",
            ],
            docker=False,
        ),
        row(
            "PS3",
            "CD with G 10x byte-identical except Await timing",
            "PLANNED",
            inputs={
                "app": "ok",
                "runs": PS3_RUNS,
                "roots": 2,
                "hashseed": "random",
                "plus": f"forms generate x{PS3_RUNS}",
            },
            command=f"cd <ok tree>; {cdg}",
            docker=f"{PS3_RUNS} runs",
        ),
        row(
            "PSx",
            "wall time of CD with G <= 180 s",
            "PLANNED",
            inputs={
                "with_g": f"the {PS3_RUNS} PS3 runs",
                "baseline_no_g": f"{BASELINE_RUNS} runs of ok",
                "gate_s": PSX_LIMIT_S,
            },
            command_no_g=f"cd <ok tree>; {cd}",
            docker=f"{BASELINE_RUNS} runs",
        ),
        row(
            "PS-extras",
            "reported rows (PS1o, PS1k, PS1layer, PS-H, PS-PG4, PS-PG5, PS-PNX)",
            "PLANNED",
            inputs={
                "invalid": len(rec["invalid"]),
                "not_expressible_rules": len(rec["ne"]),
                "attempts": sum(1 for x in rec["ne"] if x.get("attempt")),
            },
            docker=False,
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--stage", choices=("streak",), required=True)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="list every row's inputs; no build, no Docker, no ledger entry (writes only under --scratch)",
    )
    ap.add_argument(
        "--rescore",
        type=Path,
        help="post hoc: score a raw record from an earlier run (no Docker, ledger untouched)",
    )
    ap.add_argument("--set-tgz", type=Path, default=REPO / SET_TGZ_REL)
    ap.add_argument(
        "--shapes",
        type=Path,
        help="an already extracted streak/ directory (still checked against the registered hashes)",
    )
    ap.add_argument("--streak-repo", type=Path, default=Path.home() / "Documents" / "streak")
    ap.add_argument("--streak-ref", default=STREAK_SHA)
    ap.add_argument(
        "--freeze",
        type=Path,
        help="sha256sum file: this runner and the library files (required to score)",
    )
    ap.add_argument("--driver-python", help="python with pymysql and bcrypt (runs the checker)")
    ap.add_argument("--go", default=shutil.which("go") or "/opt/homebrew/bin/go")
    ap.add_argument(
        "--owner", default="h-streak-arm", help="lattice.owner label of the throwaway containers"
    )
    ap.add_argument("--g-variant", choices=VARIANTS, default="strict")
    ap.add_argument(
        "--accept-g-unrunnable",
        action="store_true",
        help="score the Docker-free rows when the strict G cannot run",
    )
    ap.add_argument("--keep-trees", action="store_true")
    ap.add_argument("--scratch", type=Path)
    ap.add_argument("--out", type=Path)
    return ap


def get_set(args: argparse.Namespace, scratch: Path) -> tuple[Path, dict[str, Any]]:
    root = args.shapes if args.shapes else extract_set(args.set_tgz, scratch / "set")
    vs = verify_set(root)
    if not vs["ok"]:
        raise RunnerError("the set does not verify: " + "; ".join(vs["problems"][:6]))
    return root, vs


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    scoring = not args.dry_run
    fz = freeze_check(args.freeze)
    if scoring and not fz["ok"]:
        print(
            "REFUSED: the freeze check failed; nothing was run:\n  " + "\n  ".join(fz["problems"])
        )
        return 2
    if (
        scoring
        and args.rescore is None
        and (not args.driver_python or not args.scratch or not args.out)
    ):
        ap.error("--driver-python, --scratch and --out are required to score")
    if args.rescore is not None and (not args.scratch or not args.out):
        ap.error("--rescore needs --scratch and --out")
    scratch = args.scratch or Path(os.environ.get("TMPDIR", "/tmp")) / "forms-probes-streak-dry"
    try:
        if args.dry_run:
            return dry_run(args, fz)
        scratch.mkdir(parents=True, exist_ok=True)
        args.out.mkdir(parents=True, exist_ok=True)
        if args.rescore is not None:
            return rescore(args, fz, scratch)
        return scored_run(args, fz, scratch, t0)
    except RunnerError as err:
        print(f"REFUSED: {err}")
        return 2


def dry_run(args: argparse.Namespace, fz: dict[str, Any]) -> int:
    scratch = args.scratch or Path(os.environ.get("TMPDIR", "/tmp")) / "forms-probes-streak-dry"
    root, vs = get_set(args, scratch)
    rec = read_set(root)
    doc: dict[str, Any] = {"runner_sha256": self_sha(), "freeze": fz if args.freeze else {"checked": False, "note": "--freeze is required to score; a dry run does not need it"}, "set": {"root": str(root), **vs},
                           "variant": args.g_variant, "ledger": str(LEDGER), "ledger_says": ledger_check(LEDGER, args.g_variant) or "a run may start",
                           "library_vs_freeze3": library_vs_freeze3()}  # fmt: skip
    checks: dict[str, Any] = {"go": args.go if Path(args.go).exists() else None}
    if args.streak_repo.is_dir():
        base = scratch / "base"
        try:
            archive_tree(args.streak_repo, args.streak_ref, base)
            checks["streak_sources_differing"] = verify_sources(base, rec["source_sha"])
            checks["patches_apply"] = {}
            for m in rec["mutants"]:
                t = scratch / "dry_apply"
                if t.exists():
                    shutil.rmtree(t)
                shutil.copytree(base, t)
                chk = run(["git", "apply", "--check", str(rec["root"] / m["patch"])], t, timeout=60)
                checks["patches_apply"][m["name"]] = chk["rc"] == 0
                shutil.rmtree(t)
            pre = preflight(rec, base, args.g_variant)
            checks["g_runnable"] = pre["g_runnable"]
            checks["g_validate_problems"] = pre["meta"]["validate_problems"]
            checks["g_generated"] = {"scenarios": len(pre["meta"]["scenarios"]), "invariants": len(pre["meta"]["invariants"]),
                                     "forms_usable": len(pre["meta"]["forms_usable"]), "dropped_invariants": sorted(pre["meta"]["dropped_invariants"])}  # fmt: skip
        except (RunnerError, subprocess.CalledProcessError, OSError) as err:
            checks["streak_archive_error"] = str(err)
    else:
        checks["streak_archive_error"] = f"{args.streak_repo}: not a directory"
    d = subprocess.run(
        ["docker", "image", "inspect", "mysql:8.4", "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603, S607
    checks["docker_image_mysql_8_4"] = d.returncode == 0
    doc["environment"] = checks
    doc["rows"] = plan(rec, args)
    doc["docker_runs"] = {"no_g_baseline": BASELINE_RUNS, "ps3_ok_with_g": PS3_RUNS, "mutants_with_g": len(rec["mutants"]), "total": BASELINE_RUNS + PS3_RUNS + len(rec["mutants"]),
                          "one_throwaway_mysql_8_4_container_per_run": True, "estimated_wall": "35-75 min (30-65 s a run: about 30 s without G, about 60 s with it where a generated scenario fails and waits out its window)"}  # fmt: skip
    print(json.dumps(doc, indent=1, sort_keys=True, default=str))
    return 0


def scored_run(args: argparse.Namespace, fz: dict[str, Any], scratch: Path, t0: float) -> int:
    variant = args.g_variant
    why = ledger_check(LEDGER, variant)
    if why:
        print(f"REFUSED: {why}")
        return 2
    root, vs = get_set(args, scratch)
    rec = read_set(root)
    base = scratch / "base"
    archive_tree(args.streak_repo, args.streak_ref, base)
    bad = verify_sources(base, rec["source_sha"])
    if bad:
        raise RunnerError(f"the archived Streak differs from source.sha256 in {bad}")
    pre = preflight(rec, base, variant)
    if not pre["g_runnable"] and variant == "strict" and not args.accept_g_unrunnable:
        print(
            "REFUSED (preflight, ledger untouched): the strict G's effective spec does not validate, so no checker can run it:"
        )
        for p in pre["meta"]["validate_problems"]:
            print("  " + p[:240])
        print(
            "Run with --accept-g-unrunnable to score the Docker-free rows with the G rows NOT RUN, or use --g-variant drop_invalid (post hoc)."
        )
        return 3
    index = ledger_open(LEDGER, variant, fz["runner_sha256"], args.out)
    print(f"ledger: run {index} of variant {variant} opened ({LEDGER})", flush=True)
    try:
        raw = collect_raw(args, rec, pre, base, scratch, args.out)
        raw["set_verification"] = vs
        raw["runner_sha256"] = fz["runner_sha256"]
        write_json(args.out / "raw.json", raw)
    except Exception as err:  # noqa: BLE001 -- a runner failure is never a score
        cleanup_containers(args.owner)
        ledger_status(LEDGER, variant, "crashed_before_metric")
        print(
            f"RUNNER ERROR before any metric (ledger: crashed_before_metric, one re-run allowed): {type(err).__name__}: {str(err)[:600]}"
        )
        return 4
    ledger_status(LEDGER, variant, "metric_started")
    try:
        rows = score_all(rec, raw, base, scratch, pre["spec"], pre["trace"], pre["paths"])
    except Exception as err:  # noqa: BLE001
        ledger_status(LEDGER, variant, "crashed_after_metric_start")
        print(
            f"RUNNER ERROR after the metric started (no re-run; raw.json is kept, --rescore reads it): {type(err).__name__}: {str(err)[:600]}"
        )
        return 4
    return finish(args, rows, variant, fz, raw, t0, post_hoc=False)


def finish(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    variant: str,
    fz: dict[str, Any],
    raw: dict[str, Any],
    t0: float,
    post_hoc: bool,
) -> int:
    after = freeze_check(args.freeze)
    rows.append(row("PH", "hash discipline: freeze file, this runner, library (before and after the run)", "PASS" if fz["ok"] and after["ok"] else "FAIL",
                    before=fz, after=after, runner_sha256=fz["runner_sha256"], library_vs_freeze3=library_vs_freeze3(),
                    set_manifest_sha256=SET_MANIFEST_SHA, streak=STREAK_SHA))  # fmt: skip
    rows.append(row("containers", "throwaway containers left carrying this run's label", "PASS" if not raw.get("containers_left") else "FAIL",
                    owner=args.owner, left=raw.get("containers_left", [])))  # fmt: skip
    rows.append(row("wall", "total", "REPORTED", seconds=round(time.perf_counter() - t0, 1)))
    label_rows(rows, variant, post_hoc, {})
    write_json(args.out / f"streak-{variant}{'-rescore' if post_hoc else ''}.json", rows)
    for r in rows:
        print(f"{r['probe']}: {r['status']}" + (f" ({r['reason']})" if "reason" in r else ""))
    if variant == "strict" and not post_hoc:
        ledger_status(LEDGER, variant, "scored")
    return 1 if any(r["status"] == "NOT RUN" for r in rows) else 0


def rescore(args: argparse.Namespace, fz: dict[str, Any], scratch: Path) -> int:
    """Post hoc: the rows again from a raw record (and the set, and Streak's spec). Ledger untouched; labelled."""
    t0 = time.perf_counter()
    raw = json.loads((args.rescore / "raw.json").read_text())
    root, _vs = get_set(args, scratch)
    rec = read_set(root)
    base = scratch / "base"
    archive_tree(args.streak_repo, args.streak_ref, base)
    pre = preflight(rec, base, raw["variant"])
    rows = score_all(rec, raw, base, scratch, pre["spec"], pre["trace"], pre["paths"])
    return finish(args, rows, raw["variant"], fz, raw, t0, post_hoc=True)


if __name__ == "__main__":
    raise SystemExit(main())
