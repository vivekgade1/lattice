"""Streak arm and real-agent probe of Spike E (`lattice spec implement`).

Specified by ``docs/results/2026-10-18-implement-verify-streak-arm-results.md`` section 5 and the
pre-registration ``docs/results/2026-10-17-implement-verify-prereg.md`` (rows SO, SO3, SA, SL, SG and
PAg-MS, their pass rules, predictions and truth sources) with its Amendments 1 and 2. This file is
the "new runner file, separate from the frozen ones" of Amendment 2: the frozen core runner
``dogfood/implement_probes.py`` has no code for these rows. It edits no frozen or engine file; the
product CLIs it drives (``lattice spec impact``, ``lattice spec implement``) are unchanged.

The blind pass rules and predictions below were written from the pre-registration before any Streak
run. A differing row is a finding with its raw observation; this runner never edits an expectation
to make a row pass. Rows are scored once, after this file is frozen (sha256 recorded by the
orchestrator before the run). A usage limit of the ``claude`` CLI, or a missing login, is NOT RUN,
never a pass.

Commands (the driver venv has pymysql and bcrypt; ``--dry-run`` touches no Docker and no model)::

    "$DRV" dogfood/implement_probes_streak.py --stage streak --dry-run --agent oracle \\
        --changeset "$D_SCRATCH/changeset" --scratch "$SCRATCH/s" --out "$OUT/dry"
    # SO, SO3, SL, SG, SO-rep: the 21 held-out changes, the oracle agent (no model)
    "$DRV" dogfood/implement_probes_streak.py --stage streak --streak-ref a0679e9 \\
        --changeset "$D_SCRATCH/changeset" --agent oracle --parallel 4 \\
        --scratch "$SCRATCH/s" --out "$OUT/streak-oracle"
    # SA (and SG, SL on its runs): sonnet, max_rounds 3, USD 12.00 total, 1800 s per round
    "$DRV" dogfood/implement_probes_streak.py --stage streak --streak-ref a0679e9 \\
        --changeset "$D_SCRATCH/changeset" --agent claude --subset K1,K2,K5,K9,K10,K15 \\
        --usd-cap 12 --parallel 4 --scratch "$SCRATCH/sa" --out "$OUT/streak-agent"
    # PAg-MS: the mini-shop corpus, i1 / i3's candidate / bad_invariant, USD 3.00 total
    "$DRV" dogfood/implement_probes_streak.py --stage streak --agent pagms --usd-cap 3 \\
        --scratch "$SCRATCH/pm" --out "$OUT/pagms"
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("IMPLEMENT_PROBES_STREAK_REPO", HERE.parent)).resolve()
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

import implementation_probes as IP  # noqa: E402
import importer_go_probes as G  # noqa: E402

from lattice.spec.impact_tier4 import eid_of  # noqa: E402

PY = sys.executable
STREAK = G.STREAK
STREAK_REF = "a0679e9"
GO = shutil.which("go") or "/opt/homebrew/bin/go"

#: the manifest of D's held-out change set (sha256 of ``MANIFEST.sha256``), fixed in the brief
MANIFEST_SHA256 = "52e0e541e2e2f1347f38d0c3f2000d68ecbe94d64e884d6bc02dfff7d523caa3"
WORKFLOWS = "docs/results/data/2026-10-15-workflow-layer/workflows.json"
FREEZE_FILE = "docs/results/data/2026-10-17-implement-verify/freeze.sha256"
TRUTH_TOOL = "dogfood/impact_truth/main.go"

SA_KINDS = ("K1", "K2", "K5", "K9", "K10", "K15")
SA_USD_CAP = 12.0  # the owner's hard cap (Amendment 2)
PAGMS_USD_CAP = 3.0
ROUND_TIMEOUT = 1800  # seconds per rebuild round (the prereg's SA setting; also rebuild's default)
GATE_TIMEOUT = (
    2400  # seconds per gate command (rebuild's default is 900; a full check.sh is longer)
)
MAX_ROUNDS = 3
MODEL = "sonnet"
MAX_PARALLEL = 4
D_MICRO_FILE_RECALL = 0.864  # D's measured micro file recall of T3 (PT3)
TOKEN_PREFIX = "implp-"  # the lattice.owner label value of every container this runner starts
FAULT_AFTER_SCENARIOS = 3  # the fault run's candidate check raises on its third scenario
REPEAT_DEFAULT = ("c01", "c03", "c08")  # SO-rep: whole pipeline twice (see ambiguity A7)
FAULT_CHANGE_DEFAULT = "c14"  # the smallest reference edit among the changes that build

#: the engine and fixture files whose sha256 the pre-registration listed at 6a318e5. Informational:
#: later merged spikes changed some of them (reported by --dry-run, never a reason to stop).
PREREG_ENGINE_HASHES = {
    "lattice/studio/rebuild.py": "ddb005b594e20acbf61fd228d15b73d7b5c331d6eba392999509d0b0de3a7cb7",
    "lattice/studio/areas.py": "e4b11ec2d0a3082f6e5d9325e6a2d44cb72a55c9838510075572cbc7d1d72522",
    "lattice/studio/server.py": "01a8df5522e5498d02c64c7e07a204bfa09149086fedcd67a9329c36edb515c1",
    "lattice/studio/state.py": "428d8d2321afb90209e76af9686ecd720d8cb9a67f8e5bd17df2bff960088ad0",
    "lattice/studio/session.py": "44ea3dad50791af04d04264481a768fc03a9331e1f9f98dfc9aa5a4e6ac3b2c6",
    "lattice/spec/impact_report.py": "a3e11f1c266a813a66e33d8943b8773705a419ce8dde806615203d750ae0137c",
    "lattice/spec/impact_tier2.py": "6f86fc6cfb2b7f2a6e485633d810e55782fdfbcfdce71921bff8f9cff560a90a",
    "lattice/spec/impact_tier3.py": "22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf",
    "lattice/spec/check.py": "68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e",
    "lattice/spec/incremental.py": "29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6",
    "lattice/spec/implementation.py": "7012db6e907e9b853402ed6a433b6690e64fd7b82df37ec96ccd0df647a6d2e6",
    "lattice/mcp/tools.py": "7c0833aa945e2d0a2596e53881c37484001a7ee9592dc5d317efc7b03523423c",
    "lattice/spec/appmap.py": "701f17f333ec918ffadf79904b2c9926a9026a1a1f34c6402ea2083d4172c796",
    "lattice/spec/impact_ask.py": "c6f43564156741afefdb8ac143d658b0ddb0e615d7caa0ca21726772b408797d",
    "tests/spec/minishop.py": "90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd",
}

#: blind predictions (prereg section 4) as data, so the report prints them next to the observation
PRED_SO = {
    "rebuild_round1_builds": "all builds pass in round 1 (19/19)",
    "controls": "c15 and c21 (empty ref.diff) nothing_to_build",
    "flow_changes": {"c03": 2, "c08": 6},
    "flow_reported_as": "all t3_may_fail, 0 unreported, 0 workflow_fails",
    "spec_backed": "2-8 of 8",
    "verdicts": "pass 17-19, needs_owner 0-2, fail 0",
}
PRED_SA = "passed 3-6 of 6; regression 0, unreported 0-1, needs_owner 0-2 on passed; cost USD 3-10"
PRED_PAGMS = "i1 pass (p 0.8); bad_invariant not_built or pass with 0 regressions; 0 unreported"


# ---- results ------------------------------------------------------------------------------------


def row(
    pid: str, probe: str, predicted: Any, observed: Any, passed: bool | None, **extra: Any
) -> dict[str, Any]:
    """A scored row: ``pass`` True (PASS), False (FAIL) or None (REPORT: report-only, no gate)."""
    return {
        "id": pid,
        "probe": probe,
        "ran": True,
        "predicted": predicted,
        "observed": observed,
        "pass": passed,
        **extra,
    }


def not_run(pid: str, probe: str, why: str, **extra: Any) -> dict[str, Any]:
    return {"id": pid, "probe": probe, "ran": False, "reason": why, "pass": None, **extra}


def status_of(r: Mapping[str, Any]) -> str:
    if not r.get("ran", True):
        return "NOT RUN"
    return "REPORT" if r["pass"] is None else ("PASS" if r["pass"] else "FAIL")


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: Path) -> str:
    return sha_bytes(p.read_bytes())


def canon_rep(t4: Mapping[str, Any] | None) -> str | None:
    """A Tier 4 for the SO-rep comparison: the tree shas of ``implementation`` are left out (the
    scratch ``studio.toml`` holds the clone's own paths, so the spec tree differs by clone and so
    does the build tree on top of it); the diff's sha256 and file list stay in."""
    if t4 is None:
        return None
    t = json.loads(canon(t4))
    for k in ("spec_tree", "build_tree"):
        (t.get("implementation") or {}).pop(k, None)
    return canon(t)


def rep_diff_keys(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> list[str] | None:
    """The top-level Tier 4 keys that differ between two runs (after :func:`canon_rep`)."""
    if a is None or b is None:
        return None
    ja, jb = json.loads(canon_rep(a) or "{}"), json.loads(canon_rep(b) or "{}")
    return sorted(k for k in set(ja) | set(jb) if ja.get(k) != jb.get(k))


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


# ---- the change set -----------------------------------------------------------------------------


class ChangesetError(Exception):
    """The change set is not the one the pre-registration names."""


@dataclass
class Change:
    id: str
    kind: str
    dir: Path
    meta: dict[str, Any]
    ref_diff: Path
    empty: bool  # an empty ref.diff: S + ref = S (the coordinator's ruling); nothing to build


def verify_manifest(cs: Path, expected: str | None = MANIFEST_SHA256) -> int:
    """Check ``MANIFEST.sha256`` against the expected hash (None: skip) and every listed file
    against it. Returns the number of files checked."""
    mf = cs / "MANIFEST.sha256"
    if not mf.is_file():
        raise ChangesetError("MANIFEST.sha256 is missing")
    if expected is not None and sha_file(mf) != expected:
        raise ChangesetError(f"MANIFEST.sha256 is {sha_file(mf)}, not the registered {expected}")
    n = 0
    for ln in mf.read_text().splitlines():
        if not ln.strip():
            continue
        digest, _, rel = ln.partition("  ")
        p = cs / rel.strip()
        if not p.is_file():
            raise ChangesetError(f"{rel.strip()} is listed in the manifest and missing")
        if sha_file(p) != digest:
            raise ChangesetError(f"{rel.strip()} differs from the manifest")
        n += 1
    return n


def load_changeset(
    cs: Path, *, expected_manifest: str | None = MANIFEST_SHA256, only: Iterable[str] = ()
) -> list[Change]:
    """The changes of a change-set directory, sorted by id. Needs ``READY`` (as D's runner does)
    and a manifest equal to the registered one (``expected_manifest=None`` skips the hash, never
    the per-file check)."""
    cs = Path(cs)
    if not (cs / "READY").is_file():
        raise ChangesetError("the change set is not READY")
    verify_manifest(cs, expected_manifest)
    keep = set(only)
    out: list[Change] = []
    for d in sorted(p for p in cs.iterdir() if p.is_dir()):
        if keep and d.name not in keep:
            continue
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, ValueError) as err:
            raise ChangesetError(f"{d.name}/meta.json: {err}") from None
        diff = d / "ref.diff"
        if not diff.is_file():
            raise ChangesetError(f"{d.name}/ref.diff is missing")
        for name in ("REQUIREMENTS.md", "spec.py", "spec.trace.json"):
            if not (d / name).is_file():
                raise ChangesetError(f"{d.name}/{name} is missing")
        out.append(
            Change(d.name, str(meta.get("kind") or ""), d, meta, diff, diff.stat().st_size == 0)
        )
    missing = keep - {c.id for c in out}
    if missing:
        raise ChangesetError(f"no such change: {', '.join(sorted(missing))}")
    return out


def select_subset(changes: Sequence[Change], kinds: Sequence[str]) -> list[Change]:
    """SA: the first change of each kind in `kinds` by sorted id (K1, K2, K5, K9, K10 and K15 give
    c01, c02, c05, c09, c10, c15 on D's set). A kind with no change is skipped (the caller
    reports it)."""
    out = []
    for kind in kinds:
        match = sorted((c for c in changes if c.kind == kind), key=lambda c: c.id)
        if match:
            out.append(match[0])
    return out


# ---- scorers (pure) -----------------------------------------------------------------------------


def key4(i: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """The identity two reports share: (layer, rule, case, endpoint)."""
    return (
        str(i.get("layer")),
        str(i.get("rule")),
        str(i.get("case") or ""),
        str(i.get("endpoint") or ""),
    )


def _t4(out: Mapping[str, Any] | None) -> dict[str, Any] | None:
    t = (out or {}).get("tier4")
    return t if isinstance(t, dict) else None


def cost_of(out: Mapping[str, Any] | None) -> float:
    t = _t4(out) or {}
    usage = (t.get("rebuild") or {}).get("usage") or {}
    try:
        return float(usage.get("cost_usd_list_estimate") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def flow_changes(t4: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "flow": i.get("flow"),
            "reported_as": i.get("reported_as"),
            "spec_backed": i.get("spec_backed"),
        }
        for i in t4.get("flows") or []
        if i.get("kind") == "flow_changed"
    ]


def score_so_change(
    ch: Change,
    d_report: Mapping[str, Any] | None,
    out: Mapping[str, Any] | None,
    rc: int | None,
) -> dict[str, Any]:
    """One change of SO. The gated items: ``ok_unreported`` (0 unreported flow changes),
    ``ok_regression`` (0 regressions), ``ok_matches`` (``before.matches_report`` is true) and, for a
    change that builds, ``ok_resolved`` (``resolved`` equals the report's Tier 1 failing set
    exactly). A change with no usable report fails every item; nothing is vacuous."""
    base: dict[str, Any] = {"change": ch.id, "kind": ch.kind, "empty_ref": ch.empty, "rc": rc}
    t4 = _t4(out)
    if d_report is None or out is None or t4 is None or rc != 0:
        why = "no D report" if d_report is None else f"no usable Tier 4 (exit {rc})"
        return {
            **base,
            "error": why,
            "ok_unreported": False,
            "ok_regression": False,
            "ok_matches": False,
            "ok_resolved": False,
            "ok_expected": False,
        }
    counts = t4.get("counts") or {}
    rebuild = t4.get("rebuild") or {}
    fcs = flow_changes(t4)
    unreported = sum(1 for f in fcs if f["reported_as"] == "unreported")
    regressions = len(t4.get("regressions") or [])
    wf_fails = sum(1 for i in t4.get("flows") or [] if i.get("kind") == "workflow_fails")
    t1 = [key4(i) for i in (d_report.get("tier1") or {}).get("failing") or []]
    resolved = [key4(i) for i in t4.get("resolved") or []]
    meta_failing = [key4(i) for i in (ch.meta.get("acd_on_S") or {}).get("failing") or []]
    matches = (t4.get("before") or {}).get("matches_report")
    verdict = t4.get("verdict")
    resolved_exact = Counter(resolved) == Counter(t1)
    if ch.empty:
        ok_resolved = None  # controls are outside the 19
        expected = verdict == "nothing_to_build"
    else:
        ok_resolved = resolved_exact and not t4.get("still_failing")
        expected = (
            rebuild.get("status") == "passed"
            and rebuild.get("rounds") == 1
            and bool(t1)
            and verdict in ("pass", "needs_owner")
        )
    return {
        **base,
        "state": (out.get("tiers") or {}).get("4"),
        "verdict": verdict,
        "rebuild_status": rebuild.get("status"),
        "rounds": rebuild.get("rounds"),
        "t1_failing": len(t1),
        "resolved": len(resolved),
        "resolved_exact": resolved_exact,
        "resolved_matches_meta": Counter(resolved) == Counter(meta_failing),
        "still_failing": len(t4.get("still_failing") or []),
        "failing_after": len(t4.get("failing") or []),
        "regressions": regressions,
        "unreported": unreported,
        "workflow_fails": wf_fails,
        "counts_consistent": (counts.get("unreported", 0) == unreported)
        and (counts.get("regressions", 0) == regressions),
        "matches_report": matches,
        "owner_untouched": (t4.get("owner") or {}).get("untouched"),
        "flow_changes": fcs,
        "ok_unreported": unreported == 0,
        "ok_regression": regressions == 0,
        "ok_matches": matches is True,
        "ok_resolved": ok_resolved,
        "ok_expected": expected,
    }


def aggregate_so(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """SO: the pass rule (``unreported`` = 0 and ``regression`` = 0 on every change, ``resolved``
    exact on every change that builds, ``before.matches_report`` on every change) and the
    report-only predictions beside it."""
    n = len(rows)
    builds = [r for r in rows if not r["empty_ref"]]
    controls = [r for r in rows if r["empty_ref"]]
    n_unrep = sum(1 for r in rows if r["ok_unreported"])
    n_reg = sum(1 for r in rows if r["ok_regression"])
    n_match = sum(1 for r in rows if r["ok_matches"])
    n_res = sum(1 for r in builds if r["ok_resolved"] is True)
    gate = {
        "unreported_zero": f"{n_unrep}/{n}",
        "regression_zero": f"{n_reg}/{n}",
        "resolved_exact": f"{n_res}/{len(builds)}",
        "matches_report": f"{n_match}/{n}",
    }
    passed = bool(rows) and n_unrep == n and n_reg == n and n_res == len(builds) and n_match == n
    fcs = [(r["change"], f) for r in rows for f in r.get("flow_changes") or []]
    by_change = Counter(c for c, _ in fcs)
    backed = sum(1 for _, f in fcs if f["spec_backed"] is True)
    verdicts = Counter(str(r.get("verdict")) for r in rows)
    round1 = sum(1 for r in builds if r.get("rebuild_status") == "passed" and r.get("rounds") == 1)
    ctrl_ok = sum(1 for r in controls if r.get("verdict") == "nothing_to_build")
    pred_hits = {
        "rebuild_round1_builds": round1 == len(builds),
        "controls_nothing_to_build": ctrl_ok == len(controls),
        "flow_changes_by_change": dict(by_change) == PRED_SO["flow_changes"],
        "flow_reported_as_all_t3_may_fail": all(f["reported_as"] == "t3_may_fail" for _, f in fcs),
        "spec_backed_2_to_8_of_8": len(fcs) == 8 and 2 <= backed <= 8,
        "verdict_pass_17_to_19": 17 <= verdicts.get("pass", 0) <= 19,
        "verdict_needs_owner_0_to_2": verdicts.get("needs_owner", 0) <= 2,
        "verdict_fail_0": verdicts.get("fail", 0) == 0,
    }
    return {
        "passed": passed,
        "gate": gate,
        "changes": n,
        "builds": len(builds),
        "controls": len(controls),
        "round1_builds": f"{round1}/{len(builds)}",
        "controls_nothing_to_build": f"{ctrl_ok}/{len(controls)}",
        "flow_changes": dict(by_change),
        "flow_changes_total": len(fcs),
        "spec_backed": f"{backed}/{len(fcs)}",
        "verdicts": dict(verdicts),
        "prediction_hits": pred_hits,
        "errors": sorted(r["change"] for r in rows if r.get("error")),
        "per_change": list(rows),
    }


def relevant_go(path: str) -> bool:
    """``dogfood/impact_truth/main.go``'s ``relevant``: a non-test .go file under cmd/ or
    internal/."""
    return (
        path.endswith(".go")
        and not path.endswith("_test.go")
        and (path.startswith("cmd/") or path.startswith("internal/"))
    )


def decl_compare(decl: Any, truth: Mapping[str, Any] | None) -> Any:
    """Tier 4's touched declarations against a truth's ``edited_decls`` (report-only)."""
    if not isinstance(decl, dict) or truth is None:
        return decl
    got_d = set(decl.get("touched") or [])
    want_d = {f"{d['file']}:{d['name']}" for d in truth.get("edited_decls") or []}
    return {
        "equal": got_d == want_d,
        "only_in_tier4": sorted(got_d - want_d),
        "only_in_truth": sorted(want_d - got_d),
        "recall": decl.get("recall"),
        "precision": decl.get("precision"),
    }


def score_so3_change(
    ch: Change,
    out: Mapping[str, Any] | None,
    truth: Mapping[str, Any] | None,
    truth_u0: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """SO3 for one change: the files of ``t3_vs_edit`` (the edit's touched files restricted to the
    relevant Go files) against D's go/parser truth (``edited_files`` plus ``added_files``). The
    declarations are reported, not gated."""
    t4 = _t4(out)
    base: dict[str, Any] = {"change": ch.id, "empty_ref": ch.empty}
    if t4 is None:
        return {**base, "error": "no Tier 4", "file_exact": False}
    t3 = t4.get("t3_vs_edit") or {}
    if ch.empty:
        applicable = str(t3.get("status", "")).startswith("not_applicable")
        return {
            **base,
            "status": t3.get("status"),
            "file_exact": applicable and not truth_files(truth),
        }
    if truth is None:
        return {**base, "error": "no truth", "file_exact": False}
    files = t3.get("files")
    if t3.get("status") != "run" or not isinstance(files, dict):
        return {**base, "error": f"t3_vs_edit is {t3.get('status')!r}", "file_exact": False}
    touched = set(files.get("touched") or [])
    relevant = {f for f in touched if relevant_go(f)}
    want = truth_files(truth)
    listed = set(files.get("listed") or [])
    edited = set(truth.get("edited_files") or [])
    decl = t3.get("declarations")
    return {
        **base,
        "file_exact": relevant == want,
        "touched": sorted(touched),
        "truth": sorted(want),
        "only_in_tier4": sorted(relevant - want),
        "only_in_truth": sorted(want - relevant),
        "other_files_touched": sorted(touched - relevant),
        "t3_listed": len(listed),
        "hit_edited": len(listed & edited),
        "edited": len(edited),
        "recall": files.get("recall"),
        "precision": files.get("precision"),
        "declarations": decl_compare(decl, truth),
        "declarations_u0": decl_compare(decl, truth_u0),
    }


def truth_files(truth: Mapping[str, Any] | None) -> set[str]:
    t = truth or {}
    return set(t.get("edited_files") or []) | set(t.get("added_files") or [])


def aggregate_so3(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    exact = sum(1 for r in rows if r["file_exact"])
    builds = [r for r in rows if not r["empty_ref"]]
    hit = sum(r.get("hit_edited", 0) for r in builds)
    den = sum(r.get("edited", 0) for r in builds)
    micro = round(hit / den, 3) if den else None
    decl_eq = sum(
        1
        for r in builds
        if isinstance(r.get("declarations"), dict) and r["declarations"].get("equal")
    )
    decl_eq_u0 = sum(
        1
        for r in builds
        if isinstance(r.get("declarations_u0"), dict) and r["declarations_u0"].get("equal")
    )
    return {
        "passed": bool(rows) and exact == len(rows),
        "file_exact": f"{exact}/{len(rows)}",
        "micro_file_recall": micro,
        "micro_file_recall_n": f"{hit}/{den}",
        "micro_file_recall_predicted": D_MICRO_FILE_RECALL,
        "micro_file_recall_matches_D": micro == D_MICRO_FILE_RECALL,
        "declarations_equal": f"{decl_eq}/{len(builds)}",
        "declarations_equal_u0": f"{decl_eq_u0}/{len(builds)}",
        "per_change": list(rows),
    }


def score_sl(
    runs: Sequence[Mapping[str, Any]],
    fault: Mapping[str, Any] | None,
    stage_before: Iterable[str],
    stage_after: Iterable[str],
    *,
    fault_required: bool = True,
) -> dict[str, Any]:
    """SL: ``lattice-`` containers carrying one of this runner's owner tokens after each change
    (counted before the runner's own cleanup), after the whole stage, and after the fault run (the
    after run raises while a container exists). A fault that never exercised a container proves
    nothing: that is reported as not run, not as a pass."""
    leaks = {r["job"]: list(r.get("leaked_containers") or []) for r in runs}
    leaks = {k: v for k, v in leaks.items() if v}
    stage_left = sorted(set(stage_after) - set(stage_before))
    fault_obs: dict[str, Any] | None = None
    exercised = True
    if fault is not None:
        out = fault.get("out") or {}
        state = str((out.get("tiers") or {}).get("4", ""))
        fault_obs = {
            "rc": fault.get("rc"),
            "state": state,
            "containers_at_fault": fault.get("seen_at_fault"),
            "leaked": list(fault.get("leaked_containers") or []),
        }
        exercised = (
            fault.get("rc") == 3
            and state.startswith("not_run:error")
            and bool(fault.get("seen_at_fault"))
        )
        if fault.get("leaked_containers"):
            leaks[str(fault.get("job"))] = list(fault["leaked_containers"])
    elif fault_required:
        exercised = False
    obs = {
        "runs": len(runs),
        "leaked_per_run": leaks,
        "leaked_after_stage": stage_left,
        "fault": fault_obs,
    }
    if leaks or stage_left:
        return {"pass": False, "observed": obs, "reason": "leaked containers"}
    if not exercised:
        return {
            "pass": None,
            "observed": obs,
            "reason": "the fault run did not exercise a live container and a raised after run",
        }
    return {"pass": True, "observed": obs, "reason": ""}


def snapshot_changes(
    a: Mapping[str, Any], b: Mapping[str, Any], allowed_new: Iterable[str]
) -> dict[str, list[str]]:
    """What differs between two owner file snapshots: changed, removed, and new entries beyond the
    allowed ones (``.git/refs/heads/implement/<eid>`` and ``.../rebuild/<eid>``)."""
    allowed = set(allowed_new)
    return {
        "changed": sorted(k for k in a if k in b and a[k] != b[k]),
        "removed": sorted(k for k in a if k not in b),
        "new": sorted(k for k in b if k not in a and k not in allowed),
    }


def score_sg_run(rec: Mapping[str, Any]) -> dict[str, Any]:
    """PG and PNM for one run: the driver's own owner guard said untouched, the file snapshot of the
    scratch clone has 0 changed / removed / new entries beyond the allowed refs, and (after a
    pass) the no-merge facts of :func:`check_pnm` hold exactly."""
    t4 = _t4(rec.get("out"))
    drift = rec.get("drift") or {}
    untouched = (t4.get("owner") or {}).get("untouched") if t4 else None
    pnm = rec.get("pnm") or {}
    ok_pg = (
        untouched is True
        and rec.get("drift") is not None
        and not (drift.get("changed") or drift.get("removed") or drift.get("new"))
    )
    ok_pnm = pnm.get("ok") is True
    return {
        "job": rec.get("job"),
        "owner_untouched": untouched,
        "drift": drift,
        "pnm": pnm,
        "worktrees_left": rec.get("worktrees_left"),
        "ok_pg": ok_pg,
        "ok_pnm": ok_pnm,
        "ok": ok_pg and ok_pnm,
    }


def aggregate_sg(
    runs: Sequence[Mapping[str, Any]],
    streak_before: Mapping[str, Any],
    streak_after: Mapping[str, Any],
) -> dict[str, Any]:
    scored = [score_sg_run(r) for r in runs]
    ok_runs = sum(1 for s in scored if s["ok"])
    unchanged = streak_before == streak_after and bool(streak_before)
    return {
        "passed": bool(scored) and ok_runs == len(scored) and unchanged,
        "runs": f"{ok_runs}/{len(scored)}",
        "streak_checkout_unchanged": unchanged,
        "streak_before": dict(streak_before),
        "streak_after": dict(streak_after),
        "failing": [s for s in scored if not s["ok"]],
    }


def score_sa_change(
    ch: Change, out: Mapping[str, Any] | None, rc: int | None, why_not_run: str | None = None
) -> dict[str, Any]:
    base: dict[str, Any] = {"change": ch.id, "kind": ch.kind, "empty_ref": ch.empty, "rc": rc}
    if why_not_run:
        return {**base, "not_run": why_not_run}
    t4 = _t4(out)
    if t4 is None or rc != 0:
        return {**base, "error": f"no usable Tier 4 (exit {rc})", "cost_usd": cost_of(out)}
    rebuild = t4.get("rebuild") or {}
    counts = t4.get("counts") or {}
    fcs = flow_changes(t4)
    return {
        **base,
        "verdict": t4.get("verdict"),
        "state": (out or {}).get("tiers", {}).get("4"),
        "rebuild_status": rebuild.get("status"),
        "rounds": rebuild.get("rounds"),
        "regressions": len(t4.get("regressions") or []),
        "unreported": sum(1 for f in fcs if f["reported_as"] == "unreported"),
        "needs_owner": t4.get("verdict") == "needs_owner",
        "flow_changes": len(fcs),
        "counts": dict(counts),
        "cost_usd": cost_of(out),
    }


def sa_not_run_reason(rec: Mapping[str, Any]) -> str | None:
    """Why an SA change counts as not run: it never started (usage limit of an earlier change,
    the USD cap), or its own agent calls ended on a usage limit or a missing login. A run that
    failed for any other reason is an error row, not a hidden not-run."""
    if rec.get("not_run"):
        return str(rec["not_run"])
    if rec.get("usage_limit"):
        return "the claude CLI hit its usage limit"
    if rec.get("not_logged_in"):
        return "the claude CLI is not logged in"
    return None


def aggregate_sa(rows: Sequence[Mapping[str, Any]], n_planned: int) -> dict[str, Any]:
    """SA is report-only. ``passed_builds``: the rebuild passed; ``ok_controls``: a control
    (empty reference edit) correctly ended ``nothing_to_build``. A not-run change (usage limit,
    budget) is counted as not run, so a partial run reads k of n."""
    ran = [r for r in rows if not r.get("not_run")]
    built = [r for r in ran if r.get("rebuild_status") == "passed"]
    ctrl = [r for r in ran if r.get("empty_ref") and r.get("verdict") == "nothing_to_build"]
    cost = round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 4)
    reg = sum(r.get("regressions", 0) for r in built)
    unrep = sum(r.get("unreported", 0) for r in built)
    needs = sum(1 for r in built if r.get("needs_owner"))
    k = len(built) + len(ctrl)
    return {
        "planned": n_planned,
        "ran": len(ran),
        "not_run": sorted(r["change"] for r in rows if r.get("not_run")),
        "passed_builds": len(built),
        "ok_controls": len(ctrl),
        "passed_incl_controls": f"{k}/{n_planned}",
        "regressions_on_passed": reg,
        "unreported_on_passed": unrep,
        "needs_owner_on_passed": needs,
        "cost_usd": cost,
        "prediction_hits": {
            "passed_3_to_6_of_6": 3 <= k <= 6,
            "regression_0": reg == 0,
            "unreported_0_to_1": unrep <= 1,
            "needs_owner_0_to_2": needs <= 2,
            "cost_3_to_10": 3.0 <= cost <= 10.0,
        },
        "per_change": list(rows),
    }


def score_pagms_run(
    name: str, out: Mapping[str, Any] | None, refusal: str | None
) -> dict[str, Any]:
    t4 = _t4(out)
    if t4 is None:
        return {"run": name, "error": refusal or "no Tier 4"}
    rebuild = t4.get("rebuild") or {}
    fcs = flow_changes(t4)
    return {
        "run": name,
        "verdict": t4.get("verdict"),
        "state": (out or {}).get("tiers", {}).get("4"),
        "rebuild_status": rebuild.get("status"),
        "rounds": rebuild.get("rounds"),
        "regressions": len(t4.get("regressions") or []),
        "unreported": sum(1 for f in fcs if f["reported_as"] == "unreported"),
        "owner_untouched": (t4.get("owner") or {}).get("untouched"),
        "cost_usd": cost_of(out),
    }


def aggregate_pagms(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by = {r["run"]: r for r in runs if not r.get("not_run")}
    i1, bad = by.get("i1"), by.get("bad_invariant")
    hits = {
        "i1_pass": bool(i1) and i1.get("verdict") == "pass",
        "bad_invariant_not_built_or_pass_0_regressions": bool(bad)
        and (
            bad.get("verdict") == "not_built"
            or (bad.get("verdict") == "pass" and bad.get("regressions") == 0)
        ),
        "unreported_0": all(r.get("unreported", 0) == 0 for r in by.values()) and bool(by),
    }
    return {
        "ran": sorted(by),
        "not_run": sorted(r["run"] for r in runs if r.get("not_run")),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in runs), 4),
        "prediction_hits": hits,
        "per_run": list(runs),
    }


# ---- the claude CLI: a usage limit is NOT RUN ---------------------------------------------------

# the same patterns lattice/llm/cli_client.py classifies a plan/usage-limit message with
USAGE_LIMIT_RE = re.compile(
    r"usage limit|usage cap|hit your limit|plan limit|weekly limit|5-hour limit|"
    r"session limit|out of (usage|credits)|credit balance|\bresets\b|"
    r"(?<!rate )(?<!rate-)(?<!rate_)limit reached",
    re.IGNORECASE,
)


def usage_limit_in(out_text: str, err_text: str) -> bool:
    """Whether a ``claude`` CLI call's output says the account is out of usage. stderr is scanned
    whole; stdout (stream-json) only in its error results, so a model's prose that happens to say
    "resets" is not a usage limit."""
    if USAGE_LIMIT_RE.search(err_text or ""):
        return True
    for ln in (out_text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            if USAGE_LIMIT_RE.search(ln):
                return True
            continue
        if isinstance(ev, dict) and (
            ev.get("is_error") or str(ev.get("subtype") or "").startswith("error")
        ):
            text = " ".join(str(ev.get(k) or "") for k in ("result", "error", "message"))
            if USAGE_LIMIT_RE.search(text):
                return True
    return False


LOGIN_RE = re.compile(
    r"not logged in|please run /login|invalid api key|authentication_error|"
    r"oauth token (has )?expired|run `?claude login",
    re.IGNORECASE,
)


def login_failure_in(out_text: str, err_text: str) -> bool:
    """Whether a ``claude`` CLI call failed because it is not logged in (NOT RUN, like a limit)."""
    if LOGIN_RE.search(err_text or ""):
        return True
    for ln in (out_text or "").splitlines():
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("is_error"):
            if LOGIN_RE.search(
                " ".join(str(ev.get(k) or "") for k in ("result", "error", "message"))
            ):
                return True
    return False


@dataclass
class Budget:
    """A hard USD cap shared by the jobs that run at once: a job reserves its fair share of what
    is left (not reserved, not spent) before it starts and settles with what it spent."""

    total: float
    spent: float = 0.0
    reserved: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self, unstarted: int) -> float:
        """The cap of the job that starts now (`unstarted` counts it); 0.0: nothing is left."""
        with self.lock:
            left = self.total - self.spent - self.reserved
            cap = round(left / max(1, unstarted), 2)
            if cap < 0.05:
                return 0.0
            self.reserved += cap
            return cap

    def release(self, cap: float, spent: float) -> None:
        with self.lock:
            self.reserved = max(0.0, self.reserved - cap)
            self.spent += spent


# ---- git and files of the scratch owner clone ---------------------------------------------------


def git(
    root: Path | str, *args: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else None,
        check=False,
    )


def git_out(root: Path | str, *args: str) -> str:
    r = git(root, *args)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def owner_files(repo: Path) -> dict[str, tuple[Any, ...]]:
    """sha256, mode and mtime of every file and directory of the owner's worktree (hidden files
    included, ``.git`` excluded), and of ``.git/{HEAD,index,packed-refs,config}`` and the files
    under ``.git/info`` and ``.git/refs``. No git command runs here (none may refresh the index)."""
    out: dict[str, tuple[Any, ...]] = {}
    for p in sorted(repo.rglob("*")):
        rel = p.relative_to(repo).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        st = p.lstat()
        digest = sha_file(p) if p.is_file() and not p.is_symlink() else "-"
        out[rel] = (digest, st.st_mode, st.st_mtime_ns)
    g = repo / ".git"
    for name in ("HEAD", "index", "packed-refs", "config"):
        p = g / name
        if p.is_file():
            st = p.stat()
            out[f".git/{name}"] = (sha_file(p), st.st_mode, st.st_mtime_ns)
    for sub in ("info", "refs"):
        for p in sorted((g / sub).rglob("*")):
            if p.is_file():
                st = p.stat()
                out[f".git/{p.relative_to(g).as_posix()}"] = (
                    sha_file(p),
                    st.st_mode,
                    st.st_mtime_ns,
                )
    return out


def check_pnm(repo: Path, eid_branches: tuple[str, str] | None, head_before: str) -> dict[str, Any]:
    """PNM: the owner's HEAD is unchanged; after a pass, the build commit ``B`` (``rebuild/<eid>``)
    is not an ancestor of it, B's only new commit has parent ``S`` (``implement/<eid>``) and S's
    parent is the owner's HEAD. `eid_branches` is None when the run made no build."""
    try:
        return _check_pnm(repo, eid_branches, head_before)
    except RuntimeError as err:
        return {"ok": False, "error": str(err)}


def _check_pnm(
    repo: Path, eid_branches: tuple[str, str] | None, head_before: str
) -> dict[str, Any]:
    head_now = git_out(repo, "rev-parse", "HEAD")
    res: dict[str, Any] = {"head_unchanged": head_now == head_before}
    if eid_branches is None:
        res["applicable"] = False
        res["ok"] = res["head_unchanged"]
        return res
    spec_b, build_b = eid_branches
    s = git_out(repo, "rev-parse", f"refs/heads/{spec_b}")
    b = git_out(repo, "rev-parse", f"refs/heads/{build_b}")
    anc = git(repo, "merge-base", "--is-ancestor", b, head_now).returncode
    res.update(
        applicable=True,
        build_not_in_head=anc == 1,
        one_new_commit=git_out(repo, "rev-list", "--count", f"{s}..{b}") == "1",
        build_parent_is_spec=git_out(repo, "rev-parse", f"{b}^") == s,
        spec_parent_is_head=git_out(repo, "rev-parse", f"{s}^") == head_before,
    )
    res["ok"] = all(
        res[k]
        for k in (
            "head_unchanged",
            "build_not_in_head",
            "one_new_commit",
            "build_parent_is_spec",
            "spec_parent_is_head",
        )
    )
    return res


def streak_state(streak: Path) -> dict[str, Any]:
    """The owner's Streak checkout as the brief guards it: HEAD, branch and ``git status
    --short``, read with ``GIT_OPTIONAL_LOCKS=0`` so nothing is written into it."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def g(*a: str) -> str:
        r = git(streak, *a, env=env)
        # only the trailing newline goes: a leading space is part of the status (" M studio.toml")
        return r.stdout.rstrip("\n") if r.returncode == 0 else f"[exit {r.returncode}]"

    return {
        "head": g("rev-parse", "HEAD").strip(),
        "branch": g("symbolic-ref", "-q", "--short", "HEAD").strip(),
        "status": g("status", "--short").splitlines(),
    }


# ---- the scratch clone of Streak ----------------------------------------------------------------

POST_CHECKOUT = """#!/bin/sh
# Scratch-runner scaffolding (dogfood/implement_probes_streak.py), not part of the product:
# bin/ is gitignored, but the harness runs ROOT/bin/streak-api, so every worktree the driver
# creates (spec, before, after, the rebuild's own) builds the binary of the commit it holds.
[ -f cmd/server/main.go ] || exit 0
mkdir -p bin
go build -o bin/streak-api ./cmd/server > bin/.build.log 2>&1 || true
exit 0
"""

FAULT_SITECUSTOMIZE = '''"""Fault injection for the SL fault run (dogfood/implement_probes_streak.py): the second
``lattice.studio.implement _side`` process of a run (the after run) raises on its
FAULT_AFTER scenario, once a throwaway database container is up."""
import os
import subprocess
import sys

if "_side" in getattr(sys, "orig_argv", []) and os.environ.get("FAULT_COUNTER"):
    counter = os.environ["FAULT_COUNTER"]
    try:
        n = int(open(counter).read() or "0") + 1
    except OSError:
        n = 1
    open(counter, "w").write(str(n))
    if n == 2:
        import importlib

        C = importlib.import_module("lattice.spec.check")
        orig = C._run_scenario
        state = {"n": 0}

        def boom(*a, **k):
            state["n"] += 1
            if state["n"] >= int(os.environ.get("FAULT_AFTER", "3")):
                names = subprocess.run(
                    ["docker", "ps", "-a", "--filter",
                     "label=lattice.owner=" + os.environ.get("LATTICE_CONTAINER_OWNER", "-"),
                     "--format", "{{.Names}}"],
                    capture_output=True, text=True, check=False,
                ).stdout.split()
                open(os.environ["FAULT_SEEN"], "w").write("\\n".join(names))
                raise RuntimeError("injected fault in the after run")
            return orig(*a, **k)

        C._run_scenario = boom
'''


def scratch_studio_toml(
    text: str,
    *,
    clone: Path,
    inventory: Path,
    gate: str = "project",
    round_timeout: int = ROUND_TIMEOUT,
    gate_timeout: int = GATE_TIMEOUT,
) -> str:
    """The scratch clone's ``studio.toml``: ``go_src`` at the clone (Streak's own points at the
    owner's checkout), the ``[implementation]`` and ``[impact]`` tables D's runner adds, and the
    rebuild timeouts. ``gate="acd"`` narrows the gate to ``./check.sh --layers ACD`` (the layers D
    validated the reference edits on); the default keeps the owner's gate ``./check.sh``."""
    text = re.sub(r'(?m)^go_src\s*=\s*"[^"]*"', lambda _m: f'go_src = "{clone}"', text)
    if gate == "acd":
        new = text.replace('gates = ["./check.sh"]', 'gates = ["./check.sh --layers ACD"]')
        if new == text:
            raise ValueError('studio.toml has no gates = ["./check.sh"] line to narrow')
        text = new
    elif gate != "project":
        raise ValueError(f"gate must be project or acd, not {gate!r}")
    if not re.search(r"(?m)^\[rebuild\]\s*$", text):
        raise ValueError("studio.toml has no [rebuild] table")
    text = re.sub(
        r"(?m)^\[rebuild\]\s*\n",
        f"[rebuild]\nround_timeout = {round_timeout}\ngate_timeout = {gate_timeout}\n",
        text,
        count=1,
    )
    return (
        text.rstrip("\n")
        + f'\n\n[implementation]\ninventory = "{inventory}"\napp_root = "{clone}"\n\n[impact]\n'
    )


# ---- the environment of every child -------------------------------------------------------------


def child_env(
    tmp: Path,
    *,
    owner: str | None = None,
    shim: Path | None = None,
    pythonpath: str | None = None,
) -> dict[str, str]:
    """One child's environment: no API key (ever), the driver venv for ``uv run`` (the main
    checkout's venv has no pymysql), Go offline and read-only, an owner token for containers, a
    private TMPDIR, and an optional shim directory first on PATH."""
    env = IP.env_for(tmp, pythonpath or REPO)
    for k in list(env):
        if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_")) or k == "AWS_BEARER_TOKEN_BEDROCK":
            env.pop(k)
    env.update(GOFLAGS="-mod=readonly", GOPROXY="off", GOTOOLCHAIN="local", GOWORK="off")
    env["UV_NO_SYNC"] = "1"
    paths = []
    if sys.prefix != sys.base_prefix:
        env["UV_PROJECT_ENVIRONMENT"] = sys.prefix
        env["VIRTUAL_ENV"] = sys.prefix
        paths.append(str(Path(sys.prefix) / "bin"))
    if shim is not None:
        paths.insert(0, str(shim))
    go_dir = str(Path(GO).parent)
    env["PATH"] = os.pathsep.join(
        dict.fromkeys([*paths, go_dir, *env.get("PATH", "").split(os.pathsep)])
    )
    if owner:
        env["LATTICE_CONTAINER_OWNER"] = owner
    return env


@dataclass
class Cmd:
    rc: int | None
    out: str
    err: str
    seconds: float
    timed_out: bool = False


def run_cmd(argv: Sequence[str], env: Mapping[str, str], cwd: Path, timeout: float) -> Cmd:
    """Run a child in its own process group; a timeout kills the whole group."""
    t0 = time.perf_counter()
    p = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = p.communicate(timeout=timeout)
        return Cmd(p.returncode, out, err, time.perf_counter() - t0)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
        return Cmd(None, out or "", err or "", time.perf_counter() - t0, True)


# ---- Docker -------------------------------------------------------------------------------------


def lattice_containers() -> dict[str, str]:
    """name -> ``lattice.owner`` label of every ``lattice-`` container."""
    p = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=lattice-",
            "--format",
            '{{.Names}}|{{.Label "lattice.owner"}}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    out = {}
    for ln in p.stdout.splitlines():
        name, _, owner = ln.partition("|")
        if name.startswith("lattice-"):
            out[name] = owner
    return out


def owned_by(token: str) -> list[str]:
    p = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=lattice.owner={token}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(p.stdout.split())


def ours_now(prefix: str | None = None) -> list[str]:
    """``lattice-`` containers carrying one of this runner's owner tokens."""
    return sorted(
        n for n, o in lattice_containers().items() if o.startswith(prefix or TOKEN_PREFIX)
    )


def remove_owner(token: str) -> None:
    from lattice.studio.state import remove_owned_containers  # noqa: PLC0415

    remove_owned_containers(token)


# ---- shims for the claude CLI -------------------------------------------------------------------


def write_shim(d: Path, mode: str, real: str | None) -> Path:
    """A ``claude`` shim directory. ``fail``: logs the call and exits 1 (no model, ever; for the
    oracle arm). ``tee``: runs the real CLI and tees its stdout and stderr into logs (the only way
    to see a usage limit: the report holds none), counting calls."""
    d.mkdir(parents=True, exist_ok=True)
    sh = d / "claude"
    log = d / "calls.log"
    if mode == "fail":
        sh.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    elif mode == "tee":
        if not real:
            raise ValueError("the tee shim needs the real claude binary")
        sh.write_text(
            "#!/bin/bash\n"
            f'echo "$@" >> "{log}"\n'
            "set -o pipefail\n"
            f'"{real}" "$@" 2> >(tee -a "{d}/err.log" >&2) | tee -a "{d}/out.log"\n'
            "exit ${PIPESTATUS[0]}\n"
        )
    else:
        raise ValueError(mode)
    sh.chmod(0o755)
    return d


def shim_report(d: Path) -> dict[str, Any]:
    def read(name: str) -> str:
        p = d / name
        return p.read_text(errors="replace") if p.is_file() else ""

    calls = [ln for ln in read("calls.log").splitlines() if ln.strip()]
    return {
        "calls": len(calls),
        "usage_limit": usage_limit_in(read("out.log"), read("err.log")),
        "not_logged_in": login_failure_in(read("out.log"), read("err.log")),
    }


# ---- the context and the jobs -------------------------------------------------------------------


@dataclass
class Ctx:
    args: argparse.Namespace
    scratch: Path
    out: Path
    streak: Path
    changes: list[Change]
    real_claude: str | None
    budget: Budget | None = None
    stop_agent: threading.Event = field(default_factory=threading.Event)
    pristine: Path | None = None  # S archived once: the truth tool's old side


@dataclass
class Job:
    key: str
    arm: str  # so | sorep | sa | fault
    change: Change
    agent: str  # oracle | claude
    shim: str  # fail | tee
    fault: bool = False
    unstarted: Callable[[], int] = lambda: 1


def prepare_clone(ctx: Ctx, base: Path, env: Mapping[str, str]) -> dict[str, Path]:
    """A scratch owner of Streak at ``a0679e9``: ``git archive`` (never a write into the owner's
    checkout), ``go_src`` rewritten to the clone, the binary built, the inventory captured as D's
    runner does, then ``studio.toml`` completed, a fresh git repository on ``build/streak`` with
    one commit, and the post-checkout hook that builds ``bin/streak-api`` in every worktree."""
    a = ctx.args
    clone = base / "S"
    inv = base / "inv"
    G.archive(ctx.streak, a.streak_ref, clone)
    toml = clone / "studio.toml"
    toml.write_text(
        re.sub(r'(?m)^go_src\s*=\s*"[^"]*"', lambda _m: f'go_src = "{clone}"', toml.read_text())
    )
    built = run_cmd(["go", "build", "-o", "bin/streak-api", "./cmd/server"], env, clone, 600)
    if built.rc != 0:
        raise RuntimeError(f"go build of the clone failed: {built.err[-400:]}")
    cap = run_cmd(
        [
            PY,
            "-m",
            "lattice.cli",
            "import",
            "inventory",
            str(clone),
            "--out",
            str(inv),
            "--workflows",
            str(Path(a.workflows).resolve()),
        ],
        env,
        REPO,
        900,
    )
    if cap.rc != 0:
        raise RuntimeError(f"inventory capture failed: {cap.err[-400:]}")
    toml.write_text(scratch_studio_toml(toml.read_text(), clone=clone, inventory=inv, gate=a.gate))
    for cmd in (
        ["init", "-q", "-b", "build/streak"],
        ["config", "user.email", "implement-streak@example.com"],
        ["config", "user.name", "Implement Streak Arm"],
        ["add", "--", "."],
        ["commit", "-q", "-m", f"scratch base: Streak {a.streak_ref} with the scratch studio.toml"],
    ):
        git_out(clone, *cmd)
    hook = clone / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(POST_CHECKOUT)
    hook.chmod(0o755)
    return {"clone": clone, "inv": inv}


def d_argv(clone: Path, cdir: Path, inv: Path, out: Path) -> list[str]:
    return [
        PY,
        "-m",
        "lattice.cli",
        "spec",
        "impact",
        "--project",
        str(clone),
        "--candidate",
        str(cdir),
        "--inventory",
        str(inv),
        "--tiers",
        "123",
        "--baseline",
        "run",
        "--json",
        "--out",
        str(out),
    ]


def implement_argv(
    *,
    clone: Path,
    report: Path,
    cdir: Path,
    inv: Path,
    work: Path,
    out: Path,
    agent: str,
    ref_diff: Path | None,
    usd_cap: float | None,
    model: str = MODEL,
    max_rounds: int = MAX_ROUNDS,
) -> list[str]:
    argv = [
        PY,
        "-m",
        "lattice.cli",
        "spec",
        "implement",
        "--project",
        str(clone),
        "--impact",
        str(report),
        "--candidate",
        str(cdir),
        "--inventory",
        str(inv),
        "--agent",
        agent,
        "--work",
        str(work),
        "--json",
        "--out",
        str(out),
    ]
    if agent == "oracle" and ref_diff is not None:
        argv += ["--ref-diff", str(ref_diff)]
    if agent == "claude":
        argv += ["--max-rounds", str(max_rounds), "--model", model]
        if usd_cap is not None:
            argv += ["--usd-cap", f"{usd_cap:g}"]
    return argv


def read_json(p: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_job(ctx: Ctx, job: Job) -> dict[str, Any]:  # noqa: PLR0915 -- one linear pipeline
    """One change, end to end: a clone, D's report from D's engine, ``lattice spec implement``,
    then the guard facts (file drift, no-merge, leaked containers, leftover worktrees). Never
    raises: a failure is a record with ``error``."""
    a = ctx.args
    ch = job.change
    token = f"{a.owner_prefix}{job.arm}-{ch.id}-{secrets.token_hex(3)}"
    base = ctx.scratch / "jobs" / job.key
    shutil.rmtree(base, ignore_errors=True)
    tmp = base / "tmp"
    tmp.mkdir(parents=True)
    outd = ctx.out / "runs" / job.key
    outd.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {
        "job": job.key,
        "arm": job.arm,
        "change": ch.id,
        "agent": job.agent,
        "token": token,
        "empty_ref": ch.empty,
    }
    t0 = time.perf_counter()
    shim_dir = write_shim(base / "shim", job.shim, ctx.real_claude)
    cap = 0.0
    clone: Path | None = None
    try:
        if job.agent == "claude" and job.arm == "sa":
            assert ctx.budget is not None
            cap = ctx.budget.acquire(job.unstarted())
            if ctx.stop_agent.is_set() or cap <= 0:
                rec["not_run"] = (
                    "usage limit hit by an earlier change"
                    if ctx.stop_agent.is_set()
                    else "the USD cap is spent"
                )
                return rec
            rec["usd_cap"] = cap
        env = child_env(tmp, owner=token, shim=shim_dir)
        if job.fault:
            fdir = base / "fault"
            fdir.mkdir()
            (fdir / "sitecustomize.py").write_text(FAULT_SITECUSTOMIZE)
            env.update(
                PYTHONPATH=f"{fdir}{os.pathsep}{REPO}",
                FAULT_COUNTER=str(fdir / "counter"),
                FAULT_SEEN=str(fdir / "seen"),
                FAULT_AFTER=str(FAULT_AFTER_SCENARIOS),
            )
        # D's report is made without the fault: the fault dir is only on the implement CLI's path
        d_env = child_env(tmp, owner=token, shim=shim_dir)
        c = prepare_clone(ctx, base, d_env)
        clone, inv = c["clone"], c["inv"]
        report = outd / "impact.json"
        d = run_cmd(d_argv(clone, ch.dir, inv, report), d_env, REPO, a.engine_timeout)
        (outd / "impact.stderr.txt").write_text(d.err)
        rec["d"] = {"rc": d.rc, "seconds": round(d.seconds, 1), "timed_out": d.timed_out}
        d_report = read_json(report)
        rec["d_report"] = d_report
        if d.rc != 0 or d_report is None:
            rec["error"] = f"D's engine exited {d.rc}: {d.err.strip()[-300:]}"
            return rec
        head_before = git_out(clone, "rev-parse", "HEAD")
        files_before = owner_files(clone)
        work = base / "work"
        out_json = outd / "implement.json"
        agent = job.agent
        argv = implement_argv(
            clone=clone,
            report=report,
            cdir=ch.dir,
            inv=inv,
            work=work,
            out=out_json,
            agent=agent,
            ref_diff=ch.ref_diff if agent == "oracle" else None,
            usd_cap=cap if agent == "claude" and job.arm == "sa" else None,
        )
        r = run_cmd(argv, env, REPO, a.job_timeout)
        (outd / "implement.stderr.txt").write_text(r.err)
        rec["implement"] = {"rc": r.rc, "seconds": round(r.seconds, 1), "timed_out": r.timed_out}
        rec["rc"] = r.rc
        out = read_json(out_json)
        if out is None:
            with contextlib.suppress(ValueError):
                out = json.loads(r.out)
        rec["out"] = out
        files_after = owner_files(clone)
        shim = shim_report(shim_dir)
        rec["claude"] = shim
        rec["cost_usd"] = cost_of(out)
        if shim["usage_limit"] or shim["not_logged_in"]:
            rec["usage_limit"] = bool(shim["usage_limit"])
            rec["not_logged_in"] = bool(shim["not_logged_in"])
            ctx.stop_agent.set()
        t4 = _t4(out)
        impl = (t4 or {}).get("implementation") or {}
        # the spec branch exists from the spec commit on (also for nothing_to_build); the build
        # branch only when a rebuild ran
        eid = eid_of(d_report["input"]["base"], d_report["input"]["candidate"])
        rec["eid"] = eid
        allowed = {f".git/refs/heads/implement/{eid}", f".git/refs/heads/rebuild/{eid}"}
        rec["drift"] = snapshot_changes(files_before, files_after, allowed)
        built = ((t4 or {}).get("rebuild") or {}).get("status") == "passed"
        branches = (
            (str(impl["branch_spec"]), str(impl["branch_build"]))
            if built and impl.get("branch_spec") and impl.get("branch_build")
            else None
        )
        rec["pnm"] = check_pnm(clone, branches, head_before)
        if job.fault:
            seen = base / "fault" / "seen"
            rec["seen_at_fault"] = len(seen.read_text().split()) if seen.is_file() else 0
        return rec
    except Exception as err:  # noqa: BLE001 -- a job never takes the stage down
        rec["error"] = f"{type(err).__name__}: {err}"
        return rec
    finally:
        # containers first (counted, then removed), then the review worktrees and the clone
        try:
            left = owned_by(token)
            rec["leaked_containers"] = left
            if left:
                remove_owner(token)
        except Exception as err:  # noqa: BLE001
            rec["leak_check_error"] = f"{type(err).__name__}: {err}"
        if clone is not None and (clone / ".git").is_dir():
            try:
                for wt in review_worktrees(clone):
                    git(clone, "worktree", "remove", "--force", str(wt))
                    shutil.rmtree(wt, ignore_errors=True)
                git(clone, "worktree", "prune")
                rec["worktrees_left"] = len(review_worktrees(clone))
            except Exception as err:  # noqa: BLE001
                rec["worktree_cleanup_error"] = f"{type(err).__name__}: {err}"
        if ctx.budget is not None and job.arm == "sa" and cap > 0:
            ctx.budget.release(cap, float(rec.get("cost_usd") or 0.0))
        rec["seconds"] = round(time.perf_counter() - t0, 1)
        if not a.keep:
            shutil.rmtree(base / "S", ignore_errors=True)
        slim = {k: v for k, v in rec.items() if k not in ("d_report", "out")}
        (outd / "record.json").write_text(
            json.dumps(slim, indent=1, sort_keys=True, default=str) + "\n"
        )


def review_worktrees(repo: Path) -> list[Path]:
    """Linked worktrees of the scratch clone (the kept review worktree of a rebuild)."""
    r = git(repo, "worktree", "list", "--porcelain")
    paths = [
        Path(ln.removeprefix("worktree "))
        for ln in r.stdout.splitlines()
        if ln.startswith("worktree ")
    ]
    return [p for p in paths if p.resolve() != Path(repo).resolve()]


# ---- the truth of SO3 ---------------------------------------------------------------------------


def go_truth(old_root: Path, diff_file: Path) -> dict[str, Any]:
    """D's go/parser truth for one reference edit (``dogfood/impact_truth/main.go diff``)."""
    env = dict(os.environ, **G.GO_ENV)
    p = subprocess.run(
        [GO, "run", str(REPO / TRUTH_TOOL), "diff", str(old_root), str(diff_file)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-400:])
    return json.loads(p.stdout)  # type: ignore[no-any-return]


def u0_diff(pristine: Path, ref_diff: Path, work: Path) -> Path:
    """The reference edit as ``git diff -U0`` (Tier 4's hunks have no context lines, D's ref.diff
    has three): applied to a scratch copy of the pristine tree. Report-only (SO3 declarations)."""
    work.mkdir(parents=True, exist_ok=True)
    tree = work / "tree"
    shutil.rmtree(tree, ignore_errors=True)
    shutil.copytree(pristine, tree, symlinks=True)
    for cmd in (["init", "-q"], ["add", "--", "."]):
        git_out(tree, *cmd)
    git_out(
        tree, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "base"
    )
    git_out(tree, "apply", str(ref_diff))
    out = work / f"{ref_diff.parent.name}.u0.diff"
    out.write_text(git_out(tree, "diff", "-U0", "--no-color", "--no-ext-diff") + "\n")
    shutil.rmtree(tree, ignore_errors=True)
    return out


# ---- the stage ----------------------------------------------------------------------------------


def jobs_for_oracle(ctx: Ctx) -> list[Job]:
    a = ctx.args
    jobs: list[Job] = []
    for ch in ctx.changes:
        # an empty reference edit cannot be replayed by the oracle (`git apply` refuses an empty
        # patch); it needs no agent either (nothing_to_build). The claude agent behind a shim that
        # fails every call keeps the CLI's agent check satisfied and makes a model call impossible.
        jobs.append(Job(f"so-{ch.id}", "so", ch, "claude" if ch.empty else "oracle", "fail"))
    by_id = {c.id: c for c in ctx.changes}
    rep_ids = [i for i in a.repeat_changes if i in by_id][: a.repeat]
    if a.repeat and not rep_ids:
        rep_ids = [c.id for c in ctx.changes if not c.empty][: a.repeat]
    for i in rep_ids:
        jobs.append(Job(f"sorep-{i}", "sorep", by_id[i], "oracle", "fail"))
    if a.fault_change and a.fault_change in by_id and not by_id[a.fault_change].empty:
        jobs.append(
            Job(f"fault-{a.fault_change}", "fault", by_id[a.fault_change], "oracle", "fail", True)
        )
    return jobs


def run_pool(ctx: Ctx, jobs: Sequence[Job], parallel: int) -> dict[str, dict[str, Any]]:
    left = {"n": len(jobs)}
    lock = threading.Lock()

    def unstarted() -> int:
        with lock:
            n = left["n"]
            left["n"] -= 1
            return n

    for j in jobs:
        j.unstarted = unstarted
    results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futs = {pool.submit(run_job, ctx, j): j for j in jobs}
        for fut in concurrent.futures.as_completed(futs):
            j = futs[fut]
            try:
                results[j.key] = fut.result()
            except Exception as err:  # noqa: BLE001
                results[j.key] = {"job": j.key, "change": j.change.id, "error": repr(err)}
            print(f"{j.key}: done ({results[j.key].get('seconds')} s)", flush=True)
    return results


def stage_oracle(ctx: Ctx, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SO, SO3, SO-rep, SL, SG. Returns the job records."""
    a = ctx.args
    before_names = sorted(lattice_containers())
    st_before = streak_state(ctx.streak)
    jobs = jobs_for_oracle(ctx)
    res = run_pool(ctx, jobs, a.parallel)
    st_after = streak_state(ctx.streak)
    after_ours = ours_now(a.owner_prefix)
    so_rows = [
        score_so_change(
            c,
            res[f"so-{c.id}"].get("d_report"),
            res[f"so-{c.id}"].get("out"),
            res[f"so-{c.id}"].get("rc"),
        )
        for c in ctx.changes
        if f"so-{c.id}" in res
    ]
    agg = aggregate_so(so_rows)
    model_calls = sum((r.get("claude") or {}).get("calls", 0) for r in res.values())
    agg["claude_cli_calls"] = model_calls
    rows.append(
        row(
            "SO",
            "Streak, oracle: 21 held-out changes, the frozen D engine for the report, "
            "--agent oracle --ref-diff <c>/ref.diff",
            PRED_SO,
            agg,
            agg["passed"] and model_calls == 0,
        )
    )
    # SO3: D's go/parser truth, same edits
    so3_rows = []
    for c in ctx.changes:
        r = res.get(f"so-{c.id}")
        if r is None:
            continue
        truth = truth_u0 = None
        if not c.empty:
            try:
                assert ctx.pristine is not None
                truth = go_truth(ctx.pristine, c.ref_diff)
                with contextlib.suppress(Exception):  # report-only: a failure leaves it None
                    truth_u0 = go_truth(
                        ctx.pristine, u0_diff(ctx.pristine, c.ref_diff, ctx.scratch / "u0" / c.id)
                    )
            except Exception as err:  # noqa: BLE001
                so3_rows.append(
                    {"change": c.id, "empty_ref": False, "error": str(err), "file_exact": False}
                )
                continue
        so3_rows.append(score_so3_change(c, r.get("out"), truth, truth_u0))
    a3 = aggregate_so3(so3_rows)
    rows.append(
        row(
            "SO3",
            "t3_vs_edit vs D's PT3 truth (same edits, D's impact_truth go/parser tool)",
            f"file sets equal per change; micro file recall {D_MICRO_FILE_RECALL} (D measured)",
            a3,
            a3["passed"],
        )
    )
    # SO-rep (report-only): the whole pipeline twice; the canonical Tier 4 bytes must be equal
    reps = []
    for key, r in sorted(res.items()):
        if not key.startswith("sorep-"):
            continue
        m = res.get(key.replace("sorep-", "so-", 1))
        t_a, t_b = _t4(m.get("out") if m else None), _t4(r.get("out"))
        reps.append(
            {
                "change": r["change"],
                "equal": t_a is not None and canon_rep(t_a) == canon_rep(t_b),
                "differing_keys": rep_diff_keys(t_a, t_b),
            }
        )
    rows.append(
        row(
            "SO-rep",
            "determinism of the before/after runs: the same change twice, canonical "
            "Tier 4 bytes (report-only; prereg risk 4)",
            "equal",
            reps,
            None,
        )
    )
    # SL
    fault = next((r for k, r in res.items() if k.startswith("fault-")), None)
    sl = score_sl(
        [r for k, r in res.items() if not k.startswith("fault-")],
        fault,
        before_names,
        after_ours,  # after_ours holds only owner-tokened containers
        fault_required=bool(a.fault_change),
    )
    rows.append(
        row_or_not_run(
            "SL",
            "LEAK on the Streak arm: lattice- containers per change and "
            "after the stage, plus a fault run (the after run raises)",
            "0",
            sl,
        )
    )
    # SG: PG and PNM on every run (the fault run is a run too), the owner's Streak checkout
    sg_runs = [r for r in res.values() if r.get("rc") is not None or r.get("error")]
    sg = aggregate_sg(sg_runs, st_before, st_after)
    rows.append(
        row(
            "SG",
            "PG and PNM on every Streak run (scratch clone); the owner's Streak checkout "
            "git status --short unchanged",
            "all 0",
            sg,
            sg["passed"],
        )
    )
    return list(res.values())


def row_or_not_run(pid: str, probe: str, predicted: Any, sl: Mapping[str, Any]) -> dict[str, Any]:
    if sl["pass"] is None:
        return not_run(pid, probe, sl["reason"], observed=sl["observed"])
    return row(pid, probe, predicted, sl["observed"], sl["pass"])


def stage_claude(ctx: Ctx, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SA (and SG, SL on its runs)."""
    a = ctx.args
    subset = select_subset(ctx.changes, [k.strip() for k in a.subset.split(",") if k.strip()])
    ctx.budget = Budget(a.usd_cap)
    st_before = streak_state(ctx.streak)
    before_names = sorted(lattice_containers())
    jobs = [Job(f"sa-{c.id}", "sa", c, "claude", "tee") for c in subset]
    # the control (empty reference edit) goes last: it spends nothing
    jobs.sort(key=lambda j: (j.change.empty, j.change.id))
    res = run_pool(ctx, jobs, a.parallel)
    st_after = streak_state(ctx.streak)
    sa_rows = []
    for c in subset:
        r = res.get(f"sa-{c.id}", {})
        sa_rows.append(
            score_sa_change(
                c,
                r.get("out"),
                r.get("rc"),
                sa_not_run_reason(r),
            )
        )
    agg = aggregate_sa(sa_rows, len(subset))
    limit = any(r.get("usage_limit") for r in res.values())
    agg["usage_limit"] = limit
    agg["usd_cap"] = a.usd_cap
    probe = "Streak, real agent: 6 changes, sonnet, max_rounds 3, USD 12.00 total cap, 1800 s/round"
    if limit and not agg["ran"]:
        rows.append(not_run("SA", probe, "the claude CLI hit its usage limit", observed=agg))
    else:
        rows.append(row("SA", probe, PRED_SA, agg, None))
    sg = aggregate_sg(list(res.values()), st_before, st_after)
    rows.append(
        row(
            "SG",
            "PG and PNM on every SA run; the owner's Streak checkout unchanged",
            "all 0",
            sg,
            sg["passed"],
        )
    )
    sl = score_sl(
        list(res.values()), None, before_names, ours_now(a.owner_prefix), fault_required=False
    )
    rows.append(
        row_or_not_run("SL", "LEAK on the SA runs: lattice- containers per change", "0", sl)
    )
    return list(res.values())


def load_ms() -> Any:
    spec = importlib.util.spec_from_file_location(
        "implement_ms", REPO / "tests/spec/fixtures/implement/ms.py"
    )
    ms = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["implement_ms"] = ms
    spec.loader.exec_module(ms)  # type: ignore[union-attr]
    return ms


PAGMS_RUNS = (  # (name, candidate)
    ("i1", "status_changed"),
    ("i3", "status_changed"),  # i3's candidate with no planted edit: the agent decides
    ("bad_invariant", "bad_invariant"),
)


def stage_pagms(ctx: Ctx, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PAg-MS: the real agent on the mini-shop corpus, through the Python API (as the core runner
    does), each run's claude CLI behind the tee shim."""
    a = ctx.args
    probe = (
        "real claude agent (sonnet) on i1, i3's candidate (no planted edit) and bad_invariant; "
        "max_rounds 3, USD 3.00 total cap"
    )
    if not ctx.real_claude:
        rows.append(not_run("PAg-MS", probe, "the claude CLI is not installed"))
        return []
    ms = load_ms()
    from lattice.spec.impact_tier4 import Refusal  # noqa: PLC0415

    budget = Budget(a.usd_cap)
    results: list[dict[str, Any]] = []
    cache: dict[str, Any] = {}
    recs: list[dict[str, Any]] = []
    for n, (name, cand) in enumerate(PAGMS_RUNS):
        key = f"pagms_{name}"
        if any(r.get("usage_limit") for r in recs):
            results.append({"run": name, "not_run": "usage limit hit by an earlier run"})
            continue
        cap = budget.acquire(len(PAGMS_RUNS) - n)
        if cap <= 0:
            results.append({"run": name, "not_run": "the USD cap is spent"})
            continue
        ms.CHANGES[key] = {"candidate": cand, "stub": None, "max_rounds": MAX_ROUNDS}
        shim = write_shim(ctx.scratch / "pagms-shims" / name, "tee", ctx.real_claude)

        def hook(opts: Any, cap: float = cap, shim: Path = shim) -> None:
            opts.agent = "claude"
            opts.model = MODEL
            opts.max_rounds = MAX_ROUNDS
            opts.usd_cap = cap
            opts.claude_bin = str(shim / "claude")

        run = None
        refusal = None
        try:
            run = ms.run_change(
                ctx.scratch / "pagms", key, agent="claude", d_cache=cache, opts_hook=hook
            )
            refusal = str(run.refusal) if run.refusal else None
        except Refusal as err:
            refusal = str(err)
        except Exception as err:  # noqa: BLE001
            refusal = f"{type(err).__name__}: {err}"
        out = run.out.report if run is not None and run.out is not None else None
        sh = shim_report(shim)
        spent = cost_of(out)
        budget.release(cap, spent)
        r = score_pagms_run(name, out, refusal)
        r["claude"] = sh
        if run is not None:
            r["drift"] = run.drift()
            with contextlib.suppress(Exception):
                run.cleanup()
        if sh["usage_limit"]:
            r = {"run": name, "not_run": "the claude CLI hit its usage limit", "partial": r}
        recs.append({"usage_limit": sh["usage_limit"]})
        results.append(r)
    agg = aggregate_pagms(results)
    agg["usd_cap"] = a.usd_cap
    if not agg["ran"]:
        why = next((r["not_run"] for r in results if r.get("not_run")), "no run completed")
        rows.append(not_run("PAg-MS", probe, why, observed=agg))
    else:
        rows.append(row("PAg-MS", probe, PRED_PAGMS, agg, None))
    return results


# ---- dry run ------------------------------------------------------------------------------------


def freeze_status(repo: Path = REPO, freeze_file: str = FREEZE_FILE) -> dict[str, Any]:
    """Check the 7-file freeze of the core stage against the files in `repo`."""
    path = repo / freeze_file
    res: dict[str, Any] = {"file": freeze_file, "ok": False, "files": {}}
    if not path.is_file():
        res["error"] = "freeze file missing"
        return res
    ok = True
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        digest, _, rel = ln.partition("  ")
        p = repo / rel.strip()
        good = p.is_file() and sha_file(p) == digest
        res["files"][rel.strip()] = "OK" if good else "MISMATCH"
        ok &= good
    res["ok"] = ok and bool(res["files"])
    return res


def engine_drift(repo: Path = REPO) -> dict[str, str]:
    """Files of the pre-registration's hash list that differ now (informational)."""
    out = {}
    for rel, digest in PREREG_ENGINE_HASHES.items():
        p = repo / rel
        out[rel] = "OK" if p.is_file() and sha_file(p) == digest else "DIFFERS"
    return {k: v for k, v in out.items() if v != "OK"}


def validate_args(args: argparse.Namespace, streak: Path = STREAK) -> str | None:
    """A message when the arguments break a registered limit, else None."""
    cap = {"claude": SA_USD_CAP, "pagms": PAGMS_USD_CAP}.get(args.agent)
    if cap is not None and args.usd_cap is not None and args.usd_cap > cap:
        return f"--usd-cap {args.usd_cap:g} is above the registered hard cap USD {cap:g}"
    if not 1 <= args.parallel <= MAX_PARALLEL:
        return f"--parallel must be 1..{MAX_PARALLEL}"
    if args.agent != "pagms" and not args.changeset:
        return "--changeset is required"
    for label, p in (("--scratch", args.scratch), ("--out", args.out)):
        pr = Path(p).resolve()
        for guarded in (Path(streak).resolve(),):
            if pr == guarded or guarded in pr.parents:
                return f"{label} is inside the owner's Streak checkout"
    return None


def command_lines(args: argparse.Namespace, changes: Sequence[Change]) -> dict[str, Any]:
    """The exact argv templates the stage runs, per row."""
    clone, inv = Path("<scratch>/jobs/<job>/S"), Path("<scratch>/jobs/<job>/inv")
    base = Path("<scratch>/jobs/<job>")
    res: dict[str, Any] = {
        "clone": [
            "git archive <streak-ref> | tar -x  (into <clone>)",
            "go build -o bin/streak-api ./cmd/server  (in <clone>)",
            f"{PY} -m lattice.cli import inventory <clone> --out <inv> --workflows {args.workflows}",
            "git init -b build/streak; git add -- .; git commit  (scratch base, post-checkout hook "
            "builds bin/streak-api in every worktree)",
        ],
        "d_report": d_argv(clone, Path("<change dir>"), inv, base / "impact.json"),
    }
    ch0 = next((c for c in changes if not c.empty), None)
    res["implement_oracle"] = implement_argv(
        clone=clone,
        report=base / "impact.json",
        cdir=Path("<change dir>"),
        inv=inv,
        work=base / "work",
        out=base / "implement.json",
        agent="oracle",
        ref_diff=Path("<change dir>/ref.diff"),
        usd_cap=None,
    )
    res["implement_claude"] = implement_argv(
        clone=clone,
        report=base / "impact.json",
        cdir=Path("<change dir>"),
        inv=inv,
        work=base / "work",
        out=base / "implement.json",
        agent="claude",
        ref_diff=None,
        usd_cap=SA_USD_CAP / 6,
    )
    res["truth"] = [GO, "run", TRUTH_TOOL, "diff", "<pristine S>", "<change dir>/ref.diff"]
    res["example_change"] = ch0.id if ch0 else None
    return res


def dry_run(args: argparse.Namespace, changes: Sequence[Change], streak: Path) -> dict[str, Any]:
    """Every row's inputs and the commands, without Docker, a model or a write outside --out."""
    subset = select_subset(changes, [k.strip() for k in args.subset.split(",") if k.strip()])
    by_id = {c.id: c for c in changes}
    repeat = [i for i in args.repeat_changes if i in by_id][: args.repeat]

    def desc(c: Change, agent: str) -> dict[str, Any]:
        return {
            "id": c.id,
            "kind": c.kind,
            "ref_diff_bytes": c.ref_diff.stat().st_size,
            "ref_diff_files": c.meta.get("ref_diff_files"),
            "t1_failing_on_S": (c.meta.get("acd_on_S") or {}).get("failing_count"),
            "agent": agent,
            "expected": "nothing_to_build" if c.empty else "rebuild passes in round 1",
        }

    def agent_of(c: Change) -> str:
        return "claude (behind a failing shim: no model call)" if c.empty else "oracle"

    ref_ok = git(streak, "rev-parse", "--verify", "-q", f"{args.streak_ref}^{{commit}}")
    tools = {t: shutil.which(t) for t in ("docker", "go", "uv", "claude", "git")}
    return {
        "stage": args.stage,
        "agent": args.agent,
        "runner_sha256": sha_file(Path(__file__).resolve()),
        "freeze": freeze_status(),
        "engine_files_differing_from_prereg_hashes": engine_drift(),
        "streak": {
            "path": str(streak),
            "ref": args.streak_ref,
            "ref_resolves": ref_ok.returncode == 0,
            "state": streak_state(streak),
        },
        "tools": tools,
        "changeset": {
            "dir": str(args.changeset),
            "manifest_sha256": MANIFEST_SHA256,
            "changes": len(changes),
            "ids": [c.id for c in changes],
        },
        "workflows": {"path": args.workflows, "exists": Path(args.workflows).is_file()},
        "caps": {
            "sa_usd": SA_USD_CAP,
            "pagms_usd": PAGMS_USD_CAP,
            "round_timeout_s": ROUND_TIMEOUT,
            "gate_timeout_s": GATE_TIMEOUT,
            "max_rounds": MAX_ROUNDS,
            "model": MODEL,
            "parallel": args.parallel,
            "gate": args.gate,
        },
        "rows": {
            "SO": {
                "prediction": PRED_SO,
                "pass_rule": "unreported 0 and regression 0 on every change; resolved exact on "
                "every change that builds; before.matches_report true on every change",
                "inputs": [desc(c, agent_of(c)) for c in changes],
            },
            "SO3": {
                "truth": f"go run {TRUTH_TOOL} diff <pristine S> <ref.diff>",
                "truth_tool_sha256": sha_file(REPO / TRUTH_TOOL),
                "pass_rule": "t3_vs_edit touched files (relevant Go files) == edited_files + "
                "added_files per change; declarations and micro recall reported",
                "builds": [c.id for c in changes if not c.empty],
                "controls": [c.id for c in changes if c.empty],
            },
            "SO-rep": {"changes": repeat, "report_only": True},
            "SA": {
                "prediction": PRED_SA,
                "subset": [{"id": c.id, "kind": c.kind, "empty_ref": c.empty} for c in subset],
                "kinds": args.subset,
                "usd_cap_total": SA_USD_CAP,
                "report_only": True,
            },
            "SL": {
                "fault_change": args.fault_change or None,
                "fault": f"the after run's candidate check raises on scenario "
                f"{FAULT_AFTER_SCENARIOS} (sitecustomize on the implement CLI's path)",
                "pass_rule": "0 containers with this runner's owner tokens after each change, "
                "after the stage and after the fault run",
            },
            "SG": {
                "pass_rule": "owner.untouched, 0 changed/removed/new owner files beyond the "
                "allowed refs, PNM exact after a pass, Streak git status --short unchanged"
            },
            "PAg-MS": {
                "prediction": PRED_PAGMS,
                "runs": [
                    {
                        "name": n,
                        "candidate": c,
                        "candidate_dir_exists": (
                            REPO / "tests/spec/fixtures/implement" / c
                        ).is_dir(),
                    }
                    for n, c in PAGMS_RUNS
                ],
                "usd_cap_total": PAGMS_USD_CAP,
                "max_rounds": MAX_ROUNDS,
                "report_only": True,
            },
        },
        "commands": command_lines(args, changes),
    }


# ---- outputs and main ---------------------------------------------------------------------------


def write_outputs(
    out: Path, rows: Sequence[Mapping[str, Any]], title: str, meta: Mapping[str, Any]
) -> int:
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes.json").write_text(
        json.dumps(list(rows), indent=2, sort_keys=True, default=str) + "\n"
    )
    (out / "meta.json").write_text(
        json.dumps(dict(meta), indent=2, sort_keys=True, default=str) + "\n"
    )
    lines = [f"# {title}", ""]
    for r in rows:
        st = status_of(r)
        if st == "NOT RUN":
            lines.append(f"- NOT RUN {r['id']}: {r['probe']} -- {r['reason']}")
            continue
        obs = json.dumps(r["observed"], sort_keys=True, default=str)
        lines.append(f"- RAN {r['id']}: {st} -- {r['probe']}: observed {obs[:900]}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if any(status_of(r) == "FAIL" for r in rows) else 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", choices=("streak",), default="streak")
    ap.add_argument("--agent", choices=("oracle", "claude", "pagms"), required=True)
    ap.add_argument(
        "--streak", type=Path, default=STREAK, help="the owner's Streak checkout (read)"
    )
    ap.add_argument("--streak-ref", default=STREAK_REF)
    ap.add_argument("--changeset", type=Path, default=None)
    ap.add_argument("--workflows", default=str(REPO / WORKFLOWS))
    ap.add_argument("--subset", default=",".join(SA_KINDS), help="SA: kinds, first change each")
    ap.add_argument("--only", default="", help="comma list of change ids (smoke runs)")
    ap.add_argument("--usd-cap", type=float, default=None, help="total hard cap (SA 12, PAg-MS 3)")
    ap.add_argument("--parallel", type=int, default=MAX_PARALLEL)
    ap.add_argument("--gate", choices=("project", "acd"), default="project")
    ap.add_argument("--repeat", type=int, default=3, help="SO-rep: changes run twice (0: off)")
    ap.add_argument("--repeat-changes", default=",".join(REPEAT_DEFAULT))
    ap.add_argument("--fault-change", default=FAULT_CHANGE_DEFAULT, help="SL fault run ('' : off)")
    ap.add_argument("--engine-timeout", type=int, default=3000)
    ap.add_argument("--job-timeout", type=int, default=10800)
    ap.add_argument("--owner-prefix", default=TOKEN_PREFIX, help="lattice.owner label prefix")
    ap.add_argument("--keep", action="store_true", help="keep each job's scratch clone")
    ap.add_argument("--dry-run", action="store_true", help="list every row's inputs; no Docker")
    ap.add_argument(
        "--allow-other-changeset",
        action="store_true",
        help="smoke only: accept a manifest other than the registered one",
    )
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    for k in list(os.environ):
        if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_")):
            os.environ.pop(k)
    args.repeat_changes = [x for x in args.repeat_changes.split(",") if x]
    if args.usd_cap is None:
        args.usd_cap = {"claude": SA_USD_CAP, "pagms": PAGMS_USD_CAP}.get(args.agent)
    problem = validate_args(args, args.streak)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    changes: list[Change] = []
    if args.agent != "pagms":
        try:
            changes = load_changeset(
                args.changeset,
                expected_manifest=None if args.allow_other_changeset else MANIFEST_SHA256,
                only=[x for x in args.only.split(",") if x],
            )
        except ChangesetError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
    if args.dry_run:
        rep = dry_run(args, changes, args.streak)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "dry-run.json").write_text(
            json.dumps(rep, indent=2, sort_keys=True, default=str) + "\n"
        )
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
        return 0
    fr = freeze_status()
    if not fr["ok"]:
        print(f"error: the core freeze does not match: {fr}", file=sys.stderr)
        return 2
    args.scratch.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    real = shutil.which("claude")
    if args.agent != "pagms" and not IP.docker_available():
        why = "Docker is not running"
        ids = ("SO", "SO3", "SL", "SG") if args.agent == "oracle" else ("SA", "SL", "SG")
        for pid in ids:
            rows.append(not_run(pid, "Streak arm", why))
        return write_outputs(args.out, rows, "Spike E Streak arm", {"error": why})
    ctx = Ctx(args, args.scratch.resolve(), args.out.resolve(), args.streak, changes, real)
    t0 = time.perf_counter()
    meta: dict[str, Any] = {
        "runner_sha256": sha_file(Path(__file__).resolve()),
        "streak_ref": args.streak_ref,
        "agent": args.agent,
        "args": {k: str(v) for k, v in vars(args).items()},
        "engine_files_differing_from_prereg_hashes": engine_drift(),
        "manifest_sha256": MANIFEST_SHA256 if not args.allow_other_changeset else "other (smoke)",
    }
    if args.agent == "oracle":
        ctx.pristine = args.scratch.resolve() / "pristine-S"
        G.archive(args.streak, args.streak_ref, ctx.pristine)
        stage_oracle(ctx, rows)
    elif args.agent == "claude":
        stage_claude(ctx, rows)
    else:
        stage_pagms(ctx, rows)
    meta["seconds"] = round(time.perf_counter() - t0)
    leftovers = ours_now(args.owner_prefix) if args.agent != "pagms" else []
    meta["containers_with_our_tokens_at_end"] = leftovers
    return write_outputs(args.out, rows, f"Spike E Streak arm ({args.agent})", meta)


if __name__ == "__main__":
    raise SystemExit(main())
