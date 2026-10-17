"""Probes of the change-impact engine (docs/results/2026-10-16-change-impact-prereg.md, section 3).

The ground truth below was written from the pre-registration, before any output of the engine was
looked at: the independent rerun of the candidate's checks (``python -m lattice.spec check ...
--layers ACD --json``, flags read from studio.toml by this runner, not by the engine), the labels
recomputed with ``label_impact`` from that rerun, the reference edits read by ``go/parser``
(``dogfood/impact_truth/main.go``, independent of the engine's lexical resolver), the verified flows
replayed on the reference build. Nothing here reads the engine's report to decide what is expected.

Stages (the MySQL arms need the driver venv: pymysql and bcrypt)::

    "$DRV" dogfood/impact_probes.py --stage dev --streak-ref a0679e9 --streak-ref2 1d00ede \\
        --scratch "$SCRATCH/p" \\
        --workflows docs/results/data/2026-10-15-workflow-layer/workflows.json \\
        --out "$SCRATCH/p-data"                                  # everything but the C rows
    "$DRV" dogfood/impact_probes.py --stage changeset --changeset "$SCRATCH/changeset" \\
        --scratch "$SCRATCH/c" --out "$SCRATCH/c-data"           # the held-out set, once

The changeset stage starts only when ``<changeset>/READY`` exists. A differing probe is a finding
with its raw output; this runner never edits an expectation to make a probe pass.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import copy
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import implementation_probes as IP  # noqa: E402
import importer_go_probes as G  # noqa: E402

REPO = Path(os.environ.get("IMPACT_PROBES_REPO", HERE.parent)).resolve()
STREAK = G.STREAK
BASE_COMMIT = "bb3ff0c"  # the pre-registration's base: the byte-compat reference (PB)
CONTAINER = "lattice-"
OURS = re.compile(r"^impact(p)?-[0-9a-f]{12}$|^impactp-.+$")
GO = shutil.which("go") or "/opt/homebrew/bin/go"
ISO_RE = IP.ISO_RE
SECRET_RE = re.compile(r"(/Users|/private|/var/folders|127\.0\.0\.1|localhost|lattice-mysql)")
result, not_run, sha = IP.result, IP.not_run, IP.sha


# ---- small helpers ------------------------------------------------------------------------------


def docker_up() -> bool:
    return IP.docker_available()


def all_lattice() -> dict[str, str]:
    """name -> owner label of every ``lattice-`` container."""
    proc = subprocess.run(
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
    for ln in proc.stdout.splitlines():
        name, _, owner = ln.partition("|")
        if name.startswith("lattice-"):
            out[name] = owner
    return out


def ours_now() -> list[str]:
    """Containers this runner or the engine started (owner ``impact-<12 hex>`` or ``impactp-*``).
    Another agent's containers (another owner, or none) are not counted."""
    return sorted(n for n, o in all_lattice().items() if OURS.match(o))


def remove_owner(token: str) -> None:
    from lattice.studio.state import remove_owned_containers  # noqa: PLC0415

    remove_owned_containers(token)


def tpl(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def shim_dir(scratch: Path) -> tuple[Path, Path]:
    """A recording ``claude`` shim, first on PATH: any call is logged (PW expects none)."""
    d = scratch / "shim"
    d.mkdir(parents=True, exist_ok=True)
    log = d / "calls.log"
    sh = d / "claude"
    sh.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    sh.chmod(0o755)
    return d, log


class Run:
    def __init__(self, rc: int, out: str, err: str, seconds: float, data: Any = None) -> None:
        self.rc, self.out, self.err, self.seconds, self.data = rc, out, err, seconds, data


class World:
    """The scratch world: clones, workflows, inventories, the dev changes, the shim."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scratch = Path(args.scratch).resolve()
        self.out = Path(args.out).resolve()
        self.notes: list[str] = []
        self.s = self.scratch / "S"
        self.s4 = self.scratch / "S4"
        self.s2 = self.scratch / "S-root2"
        self.inv: dict[str, Path] = {}
        self.changes: dict[str, dict[str, Any]] = {}
        self.shim, self.shim_log = shim_dir(self.scratch)
        self.seq = 0
        self.base_report: Path | None = None
        self.go_files: list[str] = []

    def tmp(self, label: str) -> Path:
        self.seq += 1
        return IP.fresh_tmp(self.scratch, f"{self.seq:04d}-{label}")

    def env(self, tmp: Path, owner: str | None = None, pythonpath: str | None = None) -> dict:
        env = IP.env_for(tmp, pythonpath or REPO)
        env["PATH"] = f"{self.shim}{os.pathsep}{os.environ['PATH']}"
        if owner:
            env["LATTICE_CONTAINER_OWNER"] = owner
        return env


# ---- setup --------------------------------------------------------------------------------------


def setup(w: World) -> None:
    a = w.args
    w.scratch.mkdir(parents=True, exist_ok=True)
    w.out.mkdir(parents=True, exist_ok=True)
    G.archive(STREAK, a.streak_ref, w.s)
    G.archive(STREAK, a.streak_ref, w.s2)
    w.notes += [IP.rewrite_go_src(w.s), IP.rewrite_go_src(w.s2)]
    G.go_build(w.s)  # the runner's own build of the app: bin/streak-api
    G.go_build(w.s2)
    wf = Path(a.workflows).resolve()
    inv = w.scratch / "inv"
    inv.mkdir(exist_ok=True)

    def capture(name: str, root: Path, workflows: Path | None) -> Path:
        out = inv / name
        shutil.rmtree(out, ignore_errors=True)
        cmd = [
            sys.executable,
            "-m",
            "lattice.cli",
            "import",
            "inventory",
            str(root),
            "--out",
            str(out),
        ]
        if workflows is not None:
            cmd += ["--workflows", str(workflows)]
        r = subprocess.run(
            cmd, capture_output=True, text=True, env=w.env(w.tmp("capture")), check=False
        )
        assert r.returncode == 0, r.stderr
        w.inv[name] = out
        return out

    capture("S", w.s, wf)
    capture("S-root2", w.s2, wf)
    IP.add_implementation_table(w.s, w.inv["S"], w.s, w.scratch / "state-S")
    with (w.s / "studio.toml").open("a") as fh:
        fh.write("\n[impact]\n")
    IP.add_implementation_table(w.s2, w.inv["S-root2"], w.s2, w.scratch / "state-S2")
    files = sorted(
        p.relative_to(w.s).as_posix()
        for p in w.s.rglob("*.go")
        if not p.name.endswith("_test.go") and p.relative_to(w.s).parts[0] in ("cmd", "internal")
    )
    w.go_files = files
    if a.stage == "dev" and not a.no_hnat:
        G.archive(STREAK, a.streak_ref2, w.s4)
        w.notes.append(IP.rewrite_go_src(w.s4))
        G.go_build(w.s4)
        capture("S4", w.s4, None)
        IP.add_implementation_table(w.s4, w.inv["S4"], w.s4, w.scratch / "state-S4")


# ---- the independent truth: the check, run by the checker's own CLI ------------------------------


def check_flags(clone: Path) -> list[str]:
    """The flags of ``python -m lattice.spec check``, read from the clone's studio.toml here (not
    by the engine's ``_fill_check_args``)."""
    data = tomllib.loads((clone / "studio.toml").read_text())
    app, chk = data.get("app", {}), data.get("check", {})
    out = ["--app", app["app"], "--db", app["db"]]
    for key, flag in (
        ("login", "--login"),
        ("runtime", "--runtime"),
        ("services", "--services"),
        ("database", "--database"),
    ):
        if app.get(key):
            out += [flag, str(app[key])]
    if chk.get("go_src"):
        out += ["--go-src", str(chk["go_src"])]
    return out


def independent_check(w: World, clone: Path, cand: Path, layers: str = "ACD") -> Run:
    """``python -m lattice.spec check --spec spec:SPEC ... --layers ACD --json`` on `clone`'s app
    with the spec of the directory `cand`. Containers carry an owner and are removed after."""
    token = f"impactp-t{w.seq + 1:06d}"
    tmp = w.tmp("indep")
    # only the three spec files: a project directory would shadow the clone's harness module
    only = tmp / "spec-only"
    only.mkdir()
    for name in ("REQUIREMENTS.md", "spec.py", "spec.trace.json"):
        if (cand / name).is_file():
            shutil.copy(cand / name, only / name)
    cand = only
    env = w.env(tmp, owner=token, pythonpath=f"{cand}{os.pathsep}{clone}{os.pathsep}{REPO}")
    cmd = [
        sys.executable,
        "-m",
        "lattice.spec",
        "check",
        "--spec",
        "spec:SPEC",
        *check_flags(clone),
        "--layers",
        layers,
        "--json",
    ]
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, env=env, cwd=cand, timeout=1800, check=False
        )
    finally:
        remove_owner(token)
    data = None
    with contextlib.suppress(ValueError):
        data = json.loads(p.stdout)
    return Run(p.returncode, p.stdout, p.stderr, time.perf_counter() - t0, data)


def finding_key(f: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        f["layer"],
        str(f["rule"]),
        str(f.get("case") or ""),
        str(f.get("endpoint") or ""),
        str(f.get("code") or ""),
    )


def item_key(i: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        i["layer"],
        str(i["rule"]),
        str(i.get("case") or ""),
        str(i.get("endpoint") or ""),
        str(i.get("code") or ""),
    )


# ---- the engine, by its CLI ----------------------------------------------------------------------


def engine(
    w: World,
    project: Path,
    cand: Path | None,
    *,
    inventory: Path | None = None,
    tiers: str = "123",
    baseline: str = "run",
    change: str | None = None,
    state_dir: Path | None = None,
    extra: tuple[str, ...] = (),
    env_extra: dict[str, str] | None = None,
    hashseed: str | None = None,
    pythonpath: str | None = None,
    timeout: int = 3000,
    label: str = "engine",
) -> Run:
    cmd = [sys.executable, "-m", "lattice.cli", "spec", "impact", "--project", str(project)]
    cmd += ["--change", change] if change else ["--candidate", str(cand)]
    if state_dir:
        cmd += ["--state-dir", str(state_dir)]
    if inventory:
        cmd += ["--inventory", str(inventory)]
    cmd += ["--tiers", tiers, "--baseline", baseline, "--json", *extra]
    tmp = w.tmp(label)
    env = w.env(tmp, pythonpath=pythonpath)
    if hashseed is not None:
        env["PYTHONHASHSEED"] = hashseed
    env.update(env_extra or {})
    t0 = time.perf_counter()
    p = subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=REPO, timeout=timeout, check=False
    )
    data = None
    with contextlib.suppress(ValueError):
        data = json.loads(p.stdout)
    secs = time.perf_counter() - t0
    m = re.search(r"impact: ([\d.]+)s\s*$", p.stderr.strip())
    return Run(p.returncode, p.stdout, p.stderr, float(m.group(1)) if m else secs, data)


# ---- the dev changes (on S, text edits of the clone's own files) ------------------------------


def make_dev_changes(w: World) -> None:
    """Seven changes of Streak a0679e9, written here from the pre-registration's kinds. They are
    not the held-out set."""
    root = w.scratch / "dev"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    md0 = (w.s / "REQUIREMENTS.md").read_text()
    sp0 = (w.s / "spec.py").read_text()
    tr0 = (w.s / "spec.trace.json").read_text()

    def make(cid: str, kind: str, md: str = md0, sp: str = sp0, tr: str = tr0) -> None:
        d = root / cid
        d.mkdir()
        (d / "REQUIREMENTS.md").write_text(md)
        (d / "spec.py").write_text(sp)
        (d / "spec.trace.json").write_text(tr)
        w.changes[cid] = {"id": cid, "dir": d, "kind": kind}

    make("dev0_control", "control")
    rule84 = "anything else answers 422 `validation_failed` and creates no user."
    assert rule84 in md0
    make(
        "dev1_status",
        "K10 a refusal's status",
        md=md0.replace(
            rule84, "anything else answers 400 `validation_failed` and creates no user."
        ),
        sp=sp0.replace(
            'expect_status=422, expect_body=err("validation_failed", "password"),',
            'expect_status=400, expect_body=err("validation_failed", "password"),',
            1,
        ),
    )
    assert w.changes["dev1_status"]["dir"].joinpath("spec.py").read_text() != sp0
    inv_old = '        Invariant("habit_version_positive", "A habit\'s version starts at 1.",\n'
    assert inv_old in sp0
    fail_inv = (
        '        Invariant("every_user_has_change_seq_five", "Every user has change_seq five.",\n'
        '                  "User.change_seq >= 5"),\n'
    )
    make(
        "dev2_invariant_fails",
        "K11 a new invariant the app breaks",
        md=md0 + "\n- Every user has a change_seq of at least five.\n",
        sp=sp0.replace(inv_old, fail_inv + inv_old, 1),
    )
    ok_inv = (
        '        Invariant("every_habit_version_nonnegative", "A habit version is not negative.",\n'
        '                  "Habit.version >= 0"),\n'
    )
    make(
        "dev3_invariant_holds",
        "K11 a new invariant that holds",
        md=md0 + "\n- A habit version is never negative.\n",
        sp=sp0.replace(inv_old, ok_inv + inv_old, 1),
    )
    make(
        "dev4_reword",
        "K15 pure rewording",
        md=md0.replace("anything else answers 422", "anything else is answered with 422", 1),
    )
    make(
        "dev5_invariant_removed",
        "a removed invariant",
        sp=sp0.replace(
            inv_old,
            "        Invariant('_dropped', 'x', 'Habit.version >= 1'),\n" if False else "",
            1,
        ).replace('                  "Habit.version >= 1"),\n', "", 1),
    )
    new_sc = (
        '    Scenario("dev_reads_me", steps=(\n'
        '        Step("GET", "/v1/me", "ann", expect_status=200),\n'
        "    )),\n"
    )
    marker = "SCENARIOS = (\n"
    assert marker in sp0
    make(
        "dev6_new_scenario",
        "K9 an added request",
        md=md0 + "\n- `GET /v1/me` answers 200 to its owner.\n",
        sp=sp0.replace(marker, marker + new_sc, 1),
    )


# ---- PT1, PT1l, PT2, PT2i ------------------------------------------------------------------------


def label_truth(w: World, truth: dict[str, Any], cand: Path, base: Path) -> dict[str, str]:
    """target -> label recomputed with ``label_impact`` from the independent rerun."""
    from lattice.spec.draft import load_spec  # noqa: PLC0415
    from lattice.spec.incremental import diff_rules, label_impact  # noqa: PLC0415
    from lattice.spec.report import CheckFinding  # noqa: PLC0415
    from lattice.spec.rules import Trace  # noqa: PLC0415

    cand_spec, _ = load_spec((cand / "spec.py").read_text())
    base_spec, _ = load_spec((base / "spec.py").read_text())
    fields = set(CheckFinding.__dataclass_fields__)
    found = [CheckFinding(**{k: v for k, v in f.items() if k in fields}) for f in truth["findings"]]
    rep = type("R", (), {"findings": found})()
    diff = diff_rules(
        (base / "REQUIREMENTS.md").read_text(), (cand / "REQUIREMENTS.md").read_text()
    )
    trace = Trace.from_json((cand / "spec.trace.json").read_text())
    items = label_impact(rep, cand_spec, diff, trace, base_spec=base_spec)
    return {i.target: i.label for i in items}


def truth_targets(f: dict[str, Any], spec_names: set[str]) -> str | None:
    if f["layer"] == "D":
        return "scenario:" + str(f["rule"]).removeprefix("scenario: ")
    d = f.get("details") or {}
    if f["layer"] == "C" and d.get("invariant"):
        return f"invariant:{d['invariant']}"
    return None


def run_t1_set(
    w: World, changes: list[dict[str, Any]], *, inventory: Path, project: Path, label: str = "pt1"
) -> list[dict[str, Any]]:
    """For every change: the independent rerun and the engine (full CLI). Cached in the dict."""
    rows = []
    for c in changes:
        truth = independent_check(w, project, c["dir"])
        eng = engine(w, project, c["dir"], inventory=inventory, label=f"{label}-{c['id']}")
        rows.append({"change": c, "truth": truth, "engine": eng})
        c["truth"], c["engine"] = truth, eng
    return rows


def probe_pt1(w: World, changes: list[dict[str, Any]], prefix: str = "") -> list[dict[str, Any]]:
    exact, false_n, missed_n, nonempty = [], 0, 0, 0
    per = {}
    for c in changes:
        t, e = c["truth"], c["engine"]
        if t.data is None or e.data is None or e.rc not in (0,):
            per[c["id"]] = {
                "error": {
                    "truth_rc": t.rc,
                    "engine_rc": e.rc,
                    "truth_err": t.err[-300:],
                    "engine_err": e.err[-300:],
                }
            }
            exact.append(False)
            continue
        tset = {finding_key(f) for f in t.data["findings"]}
        eset = {item_key(i) for i in e.data["tier1"]["failing"]}
        per[c["id"]] = {
            "truth": len(tset),
            "engine": len(eset),
            "missed": sorted(tset - eset),
            "false": sorted(eset - tset),
        }
        exact.append(tset == eset)
        false_n += len(eset - tset)
        missed_n += len(tset - eset)
        nonempty += bool(tset)
    return [
        result(
            "PT1",
            f"{prefix}T1 failing (layer, rule, case) vs the independent rerun, every change",
            "exact on all; the control 0",
            {
                "exact": f"{sum(exact)}/{len(exact)}",
                "false": false_n,
                "missed": missed_n,
                "nonempty": nonempty,
                "per_change": per,
            },
            all(exact) and false_n == 0 and missed_n == 0,
        )
    ]


def probe_pt1l(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    ok = total = 0
    bad = []
    for c in changes:
        t, e = c["truth"], c["engine"]
        if t.data is None or e.data is None:
            continue
        labels = label_truth(w, t.data, c["dir"], w.s)
        specs = set()
        for i in e.data["tier1"]["failing"]:
            total += 1
            want = labels.get(i["target"]) if i["target"] else "unlinked"
            if want is None:
                want = "unlinked"
            if i["label"] == want:
                ok += 1
            else:
                bad.append(
                    {"change": c["id"], "target": i["target"], "got": i["label"], "want": want}
                )
        del specs
    return result(
        "PT1l",
        "T1 labels vs label_impact recomputed from the independent rerun",
        "100%",
        {"equal": f"{ok}/{total}", "differ": bad},
        ok == total,
    )


def probe_pt2(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    claims = correct = 0
    truth_total = covered = 0
    agree = 0
    wrong = []
    detail = {}
    for c in changes:
        t, e = c["truth"], c["engine"]
        if t.data is None or e.data is None:
            continue
        fails: dict[str, int] = {}
        for f in t.data["findings"]:
            if f["layer"] == "D" and not str(f["rule"]).startswith("scenario: flow_"):
                m = re.match(r"^(.*) / step (\d+)$", str(f.get("case") or ""))
                name = str(f["rule"]).removeprefix("scenario: ")
                fails[name] = int(m.group(2)) if m else 0
        got = [d for d in e.data["tier2"]["determined"] if d["kind"] == "scenario_fails"]
        contra = [d for d in e.data["tier2"]["determined"] if d["kind"] == "spec_contradiction"]
        claimed = set()
        for d in got:
            claims += 1
            name = d["target"].removeprefix("scenario:")
            claimed.add(name)
            if name in fails and fails[name] <= d["step"]:
                correct += 1
                agree += fails[name] == d["step"]
            else:
                wrong.append(
                    {"change": c["id"], "claim": d["claim"], "truth_step": fails.get(name)}
                )
        for d in contra:
            claims += 1
            a, b = (x.removeprefix("scenario:") for x in d["pair"])
            if a in fails or b in fails:
                correct += 1
            else:
                wrong.append({"change": c["id"], "claim": d["claim"], "truth_step": None})
        truth_total += len(fails)
        covered += len(set(fails) & claimed)
        detail[c["id"]] = {"claims": len(got) + len(contra), "truth_failing_scenarios": len(fails)}
    recall = round(covered / truth_total, 3) if truth_total else None
    return result(
        "PT2",
        "T2 determined scenario claims vs the T1 truth",
        "precision 1.0; recall 0.10-0.40 (p)",
        {
            "claims": claims,
            "correct": correct,
            "precision": (correct / claims) if claims else None,
            "recall": recall,
            "recall_n": f"{covered}/{truth_total}",
            "step_agreement": f"{agree}/{correct}",
            "wrong": wrong,
            "per_change": detail,
        },
        not wrong,
    )


def probe_pt2i(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Spec-only mode (``--tiers 23 --baseline FILE``) against the full mode."""
    if w.base_report is None:
        r = independent_check(w, w.s, w.s)
        assert r.data is not None
        w.base_report = w.scratch / "base-report.json"
        w.base_report.write_text(r.out)
    rows, same = {}, True
    for c in changes:
        e = c.get("engine")
        if e is None or e.data is None:
            continue
        spec_only = engine(
            w,
            w.s,
            c["dir"],
            inventory=w.inv["S"],
            tiers="23",
            baseline=str(w.base_report),
            label=f"pt2i-{c['id']}",
        )
        if spec_only.data is None:
            rows[c["id"]] = {"error": spec_only.err[-300:]}
            same = False
            continue

        def key(d: dict[str, Any]) -> list:
            return sorted((x["claim"], x.get("why") or "") for x in d["tier2"]["determined"])

        a, b = key(e.data), key(spec_only.data)
        st = sorted({x["status"] for x in spec_only.data["tier2"]["determined"]})
        rows[c["id"]] = {
            "full": len(a),
            "spec_only": len(b),
            "same": a == b,
            "spec_only_status": st,
        }
        same = same and a == b
    return result(
        "PT2i",
        "spec-only mode (--tiers 23 --baseline FILE) vs full mode: same determined set",
        "identical",
        {"per_change": rows},
        same,
    )


def probe_pt2d(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    from lattice.spec.draft import load_spec  # noqa: PLC0415
    from lattice.spec.incremental import changed_targets, diff_rules  # noqa: PLC0415

    reasons = {
        "new_request",
        "compatible_expectation",
        "base_prefix_failed",
        "profile_or_users_changed",
        "await_in_prefix",
        "invariant_or_machine",
        "no_linked_check",
        "removed_only",
    }
    ok = total = 0
    bad = []
    for c in changes:
        e = c.get("engine")
        if e is None or e.data is None:
            continue
        base_spec, _ = load_spec((w.s / "spec.py").read_text())
        cand_spec, _ = load_spec((c["dir"] / "spec.py").read_text())
        diff = diff_rules(
            (w.s / "REQUIREMENTS.md").read_text(), (c["dir"] / "REQUIREMENTS.md").read_text()
        )
        targets = changed_targets(base_spec, cand_spec)
        want = (
            len(diff.added)
            + len(diff.changed)
            + len(diff.removed)
            + sum(len(v) for v in targets.values())
        )
        got = e.data["tier2"]["decidability"]
        total += 1
        good = len(got) == want and all(
            d["state"] == "determined" or d.get("reason") in reasons for d in got
        )
        ok += good
        if not good:
            bad.append({"change": c["id"], "want": want, "got": len(got)})
    return result(
        "PT2d",
        "decidability: one entry per changed rule and target, reasons from D4's list",
        "complete",
        {"complete": f"{ok}/{total}", "incomplete": bad},
        ok == total,
    )


# ---- PT1x, PT1f ----------------------------------------------------------------------------------


def probe_pt1x(w: World, changes: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    first = sorted(changes, key=lambda c: c["id"])[:3]

    def repeat(c: dict[str, Any]) -> list[str]:
        outs = []
        for i in range(runs):
            r = engine(w, w.s, c["dir"], inventory=w.inv["S"], label=f"pt1x-{c['id']}-{i}")
            outs.append(sha(r.out.encode()) if r.rc == 0 else f"rc{r.rc}")
        return outs

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        res = list(pool.map(repeat, first))
    rows = {
        c["id"]: {"distinct": len(set(r)), "n": len(r), "sha": sorted(set(r))[:2]}
        for c, r in zip(first, res, strict=True)
    }
    return result(
        "PT1x",
        f"T1 run {runs}x on the first 3 changes (by sorted id): byte-identical",
        f"{runs}/{runs}",
        rows,
        all(v["distinct"] == 1 for v in rows.values()),
    )


def probe_pt1f(w: World, control: Run | None) -> dict[str, Any]:
    ctrl = control or engine(w, w.s, w.s, inventory=w.inv["S"], label="pt1f-control")
    if ctrl.data is None:
        return result(
            "PT1f",
            "flows on base and a planted drift",
            "0 workflow_fails; drift exact",
            {"error": ctrl.err[-400:]},
            False,
        )
    base_fails = [i for i in ctrl.data["tier1"]["flows"] if i["kind"] == "workflow_fails"]
    base_drift = [i["flow"] for i in ctrl.data["tier1"]["flows"] if i["kind"] == "baseline_drift"]
    # a planted copy: one verified flow whose first request expects another status
    inv = w.inv["S"]
    plant = w.scratch / "inv-planted"
    shutil.rmtree(plant, ignore_errors=True)
    shutil.copytree(inv, plant)
    wf = json.loads((plant / "workflows.json").read_text())
    victim = next(
        f for f in wf["flows"] if f["status"] == "verified" and f["steps"][0]["kind"] == "request"
    )
    step = victim["steps"][0]
    old = step["expect_status"]
    step["expect_status"] = 418 if old != 418 else 417
    raw = (json.dumps(wf, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()
    (plant / "workflows.json").write_bytes(raw)
    meta = json.loads((plant / "inventory.json").read_text())
    meta["workflows_sha256"] = sha(raw)
    (plant / "inventory.json").write_text(json.dumps(meta, sort_keys=True, indent=1) + "\n")
    planted = engine(w, w.s, w.s, inventory=plant, label="pt1f-planted")
    drift = sorted(
        i["flow"]
        for i in (planted.data or {}).get("tier1", {}).get("flows", [])
        if i["kind"] == "baseline_drift"
    )
    fails = [
        i
        for i in (planted.data or {}).get("tier1", {}).get("flows", [])
        if i["kind"] == "workflow_fails"
    ]
    ok = not base_fails and drift == sorted([victim["id"], *base_drift]) and not fails
    return result(
        "PT1f",
        "flows on base: 0 workflow_fails; a planted copy (one wrong expect_status): baseline_drift",
        "0; the copy is baseline_drift, exact",
        {
            "base_workflow_fails": len(base_fails),
            "base_drift": base_drift,
            "planted_flow": victim["id"],
            "planted_expect": [old, step["expect_status"]],
            "drift_on_planted": drift,
            "workflow_fails_on_planted": len(fails),
            "flows_run": ctrl.data["tier1"]["counts"]["flows_run"],
        },
        ok,
    )


# ---- PT2r: D4c on the minishop fixture --------------------------------------------------------


def probe_pt2r(w: World) -> dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from dataclasses import replace  # noqa: PLC0415

    from lattice.spec import impact_tier2 as t2  # noqa: PLC0415
    from lattice.spec.draft import load_spec  # noqa: PLC0415
    from tests.spec import minishop  # noqa: PLC0415

    base = minishop.SPEC
    root = Path(tempfile.mkdtemp(dir=str(w.scratch)))
    _dir, package = minishop.write_app(root, "ok")
    env = {
        "make_db": lambda variant, path: minishop.build_db(path),
        "app_factory": minishop.app_factory(package),
        "login": minishop.login,
    }
    passed = {(s.name, k) for s in base.scenarios for k, _ in enumerate(s.steps, 1)}
    prog = base.programs["orders"]
    changes = {
        "cancel_refuses_404": (
            prog.replace('else_=Conflict("Order cannot be cancelled")', 'else_=NotFound("gone")'),
            ["runtime_disagreement"],
        ),
        "list_limit_50": (prog.replace("n=100", "n=50"), []),
        "read_order_ok": (
            prog.replace('NotFound("Order not found")', 'NotFound("No such order")'),
            [],
        ),
    }
    rows, ok = {}, True
    for name, (text, want) in changes.items():
        cand = replace(base, programs={"orders": text})
        got = t2.runtime_replay(base, cand, passed, env, t2.SpecLines(""))
        kinds = [i["kind"] for i in got["items"]]
        rows[name] = {"want": want, "got": kinds, "status": got["status"]}
        ok = ok and kinds == want and got["status"] == "run"
    s_spec, _ = load_spec((w.s / "spec.py").read_text())
    s_out = t2.runtime_replay(s_spec, s_spec, passed, None, t2.SpecLines(""))
    rows["streak"] = {"status": s_out["status"]}
    ok = ok and s_out["status"] == "not_applicable: 0 programs"
    return result(
        "PT2r",
        "D4c on 3 minishop fixture changes (unit level); Streak not_applicable",
        "as designed",
        rows,
        ok,
    )


# ---- T3: PT3, PT3w, PU ---------------------------------------------------------------------------


def go_truth(w: World, old_root: Path, diff_file: Path) -> dict[str, Any]:
    env = dict(os.environ, **G.GO_ENV)
    p = subprocess.run(
        [
            GO,
            "run",
            str(REPO / "dogfood/impact_truth/main.go"),
            "diff",
            str(old_root),
            str(diff_file),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-600:])
    return json.loads(p.stdout)


def go_decls(w: World, root: Path, files: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not files:
        return {}
    env = dict(os.environ, **G.GO_ENV)
    p = subprocess.run(
        [GO, "run", str(REPO / "dogfood/impact_truth/main.go"), "decls", str(root), *files],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-600:])
    out: dict[str, list[dict[str, Any]]] = {}
    for d in json.loads(p.stdout)["decls"]:
        out.setdefault(d["file"], []).append(d)
    return out


def predicted(w: World, root: Path, t3: dict[str, Any]) -> tuple[set[str], set[tuple[str, str]]]:
    """(files, declarations) the engine's T3 locations point at; a declaration is the top-level
    func or type (go/parser) enclosing the location's line."""
    files = {i["file"] for i in t3["locations"]}
    decls = go_decls(w, root, sorted(files))
    out = set()
    for i in t3["locations"]:
        for d in decls.get(i["file"], []):
            if d["start"] <= i["line"] <= d["end"]:
                out.add((i["file"], d["name"]))
    return files, out


def score_t3(w: World, changes: list[dict[str, Any]], total_files: int) -> dict[str, Any]:
    tp_f = p_f = e_f = tp_d = p_d = e_d = 0
    per = {}
    sizes = []
    zero = 0
    for c in changes:
        e, tr = c.get("engine"), c.get("t3_truth")
        if e is None or e.data is None or tr is None:
            continue
        pf, pd = predicted(w, w.s, e.data["tier3"])
        ef = set(tr["edited_files"])
        ed = {(d["file"], d["name"]) for d in tr["edited_decls"]}
        tp_f += len(pf & ef)
        p_f += len(pf)
        e_f += len(ef)
        tp_d += len(pd & ed)
        p_d += len(pd)
        e_d += len(ed)
        sizes.append(len(pf))
        if ef and not (pf & ef):
            zero += 1
        per[c["id"]] = {
            "files_pred": len(pf),
            "files_true": len(ef),
            "files_hit": len(pf & ef),
            "decls_pred": len(pd),
            "decls_true": len(ed),
            "decls_hit": len(pd & ed),
            "missed_files": sorted(ef - pf),
            "added_files": tr["added_files"],
        }
    base_prec = e_f / (total_files * len(per)) if per else 0.0
    return {
        "changes": len(per),
        "file_recall": round(tp_f / e_f, 3) if e_f else None,
        "file_precision": round(tp_f / p_f, 3) if p_f else None,
        "decl_recall": round(tp_d / e_d, 3) if e_d else None,
        "decl_precision": round(tp_d / p_d, 3) if p_d else None,
        "all_files_baseline_precision": round(base_prec, 3),
        "median_files_per_change": statistics.median(sizes) if sizes else None,
        "changes_with_zero_true_files": zero,
        "total_files": total_files,
        "per_change": per,
    }


def probe_pt3(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    s = score_t3(w, changes, len(w.go_files))
    ok = (
        s["file_recall"] is not None
        and s["file_recall"] >= 0.80
        and (s["decl_recall"] or 0) >= 0.60
        and (s["file_precision"] or 0) >= 2 * s["all_files_baseline_precision"]
        and (s["median_files_per_change"] or 99) <= 12
        and s["changes_with_zero_true_files"] <= 1
    )
    return result(
        "PT3",
        "T3 vs the reference edits, micro-averaged",
        "file recall 0.85-1.0, declaration recall 0.65-0.90; precision files 0.20-0.50, "
        "declarations 0.05-0.25 (p); pass: files >= 0.80, declarations >= 0.60, precision >= "
        "2x the all-files baseline, median files <= 12, <= 1 change with zero true files",
        s,
        ok,
    )


def probe_pu(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    from lattice.spec.implementation import check_code, load_inventory  # noqa: PLC0415

    inv = load_inventory(w.inv["S"], w.s)
    ext = json.loads((w.inv["S"] / "extract.json").read_text())
    n = bad = short = 0
    bads = []
    unread_missing = []
    cache: dict[str, list[str]] = {}
    for c in changes:
        e = c.get("engine")
        if e is None or e.data is None:
            continue
        t3 = e.data["tier3"]
        listed = {(u.get("file"), u.get("line")) for u in t3["unread"]}
        for i in t3["locations"]:
            n += 1
            lines = cache.setdefault(i["file"], (w.s / i["file"]).read_text().split("\n"))
            quote = lines[i["line"] - 1].strip() if i["line"] <= len(lines) else ""
            if len("".join(quote.split())) < 8:
                short += 1
                continue
            cite = {"target": f"code:{i['file']}:{i['line']}", "quote": quote}
            if i.get("symbol"):
                cite["symbol"] = i["symbol"]
            why = check_code(w.s, inv, cite)
            if why:
                bad += 1
                bads.append({"change": c["id"], "file": i["file"], "line": i["line"], "why": why})
            hi = i.get("end_line") or i["line"]
            for u in ext.get("unread") or []:
                if (
                    u["file"] == i["file"]
                    and i["line"] <= int(u["line"]) <= hi
                    and (u["file"], u["line"]) not in listed
                ):
                    unread_missing.append({"change": c["id"], "unread": [u["file"], u["line"]]})
    # the two function literals map to their enclosing declarations
    from lattice.spec import impact_tier3 as t3m  # noqa: PLC0415

    res = t3m.Resolver(inv, t3m.GoTree(inv))
    decls = go_decls(w, w.s, ["internal/httpapi/router.go"])["internal/httpapi/router.go"]
    lit = {}
    for call, want in (("NewRouter.func4", "NewRouter"), ("setTestClock.func1", "setTestClock")):
        got = res.resolve("streak/internal/httpapi", call)
        dd = next((d for d in decls if d["name"] == want), None)
        lit[call] = (
            isinstance(got, t3m.Resolved)
            and dd is not None
            and dd["start"] <= got.line <= dd["end"]
            and got.symbol == want
        )
    ok = bad == 0 and not unread_missing and all(lit.values())
    return result(
        "PU",
        "every T3 location passes Spike C's check_code; every extract unread in a reported range "
        "listed; the 2 literals map to their enclosing declarations",
        "100%",
        {
            "locations": n,
            "checked": n - short,
            "unquotable": short,
            "failing": bad,
            "failures": bads[:10],
            "unread_missing": unread_missing[:10],
            "literals": lit,
        },
        ok,
    )


# ---- PD, PW, LEAK, PX ----------------------------------------------------------------------------


def probe_pd(w: World, changes: list[dict[str, Any]], runs: int = 10) -> dict[str, Any]:
    """T2+T3 mode: `runs` processes per change with a random PYTHONHASHSEED, plus a second root."""
    rows, ok = {}, True
    rnd = random.Random(0)

    def one(c: dict[str, Any]) -> dict[str, Any]:
        outs = []
        for _ in range(runs):
            r = engine(
                w,
                w.s,
                c["dir"],
                inventory=w.inv["S"],
                tiers="23",
                baseline="none",
                hashseed=str(rnd.randrange(1, 4_000_000_000)),
                label=f"pd-{c['id']}",
            )
            outs.append(r.out if r.rc == 0 else f"rc{r.rc}:{r.err[-200:]}")
        cand2 = w.scratch / "root2" / c["id"]
        shutil.rmtree(cand2, ignore_errors=True)
        shutil.copytree(c["dir"], cand2, ignore=shutil.ignore_patterns("meta.json", "ref.diff"))
        r2 = engine(
            w,
            w.s2,
            cand2,
            inventory=w.inv["S-root2"],
            tiers="23",
            baseline="none",
            hashseed="7",
            label=f"pd2-{c['id']}",
        )
        same = len({sha(o.encode()) for o in outs}) == 1
        same2 = r2.rc == 0 and r2.out == outs[0]
        leaks = [
            m.group(0)
            for o in [*outs, r2.out]
            for m in [SECRET_RE.search(o) or ISO_RE.search(o)]
            if m
        ]
        return {
            "identical": same,
            "second_root_identical": same2,
            "leaks": leaks[:3],
            "bytes": len(outs[0]),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for c, r in zip(changes, pool.map(one, changes), strict=True):
            rows[c["id"]] = r
            ok = ok and r["identical"] and r["second_root_identical"] and not r["leaks"]
    n = sum(
        1 for r in rows.values() if r["identical"] and r["second_root_identical"] and not r["leaks"]
    )
    return result(
        "PD",
        f"T2+T3 mode: {runs} processes per change with random PYTHONHASHSEED, plus a second "
        "scratch root; scan for /Users, /private, ISO times",
        "identical; none",
        {"clean": f"{n}/{len(rows)}", "per_change": rows},
        ok,
    )


def probe_px(w: World, control: Run | None, sample: Run | None) -> dict[str, Any]:
    t23 = engine(
        w,
        w.s,
        w.changes["dev1_status"]["dir"],
        inventory=w.inv["S"],
        tiers="23",
        baseline="none",
        label="px-23",
    )
    t1 = sample or engine(
        w, w.s, w.changes["dev1_status"]["dir"], inventory=w.inv["S"], label="px-t1"
    )
    wall23 = round(t23.seconds, 2)
    wall1 = round(t1.seconds, 1)
    return result(
        "PX",
        "wall time on S: T2+T3; T1 (2 ACD runs + 59 flows)",
        "0.3-1.5 s; 65-90 s (p); <= 3 s; <= 180 s",
        {"t2_t3_s": wall23, "t1_full_s": wall1},
        wall23 <= 3 and wall1 <= 180,
    )


def probe_pw(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Snapshot (sha256, mode, mtime, listing incl. hidden) of the clone, the base and candidate
    dirs, the inventory and the state dir around: the CLI x10, the MCP tool x10 over stdio
    (readonly, observe on and off, and a non-readonly observe), and the `--change` read."""
    cand = w.changes["dev1_status"]["dir"]
    state = w.scratch / "state-S"
    state.mkdir(exist_ok=True)
    roots = [w.s, cand, w.inv["S"], state]
    snap0 = {str(r): IP.snapshot(r) for r in roots}
    w.shim_log.write_text("")
    rcs = []
    for i in range(10):
        r = engine(
            w,
            w.s,
            cand,
            inventory=w.inv["S"],
            tiers="23",
            baseline="none",
            extra=(["--section", "tier3"] if i % 2 else []),
            label=f"pw-cli-{i}",
        )
        rcs.append(r.rc)
    full = engine(w, w.s, cand, inventory=w.inv["S"], label="pw-cli-full")
    rcs.append(full.rc)
    # the MCP tool reads a project-relative candidate: it is placed inside the clone for this window
    proj_cand = "dev-cand"
    mcp_errors = {"observe_refused": 0, "ok": 0}
    # the candidate lives inside the clone for the MCP (project-relative dirs); the snapshot
    # below is taken with it in place (and removed by the runner afterwards, outside the window)
    inside = w.s / proj_cand
    shutil.copytree(cand, inside)
    snap0[str(w.s)] = IP.snapshot(w.s)
    try:
        calls = [
            {"candidate": proj_cand},
            {"candidate": proj_cand, "section": "tier3", "limit": 20},
            {"candidate": proj_cand, "observe": True},
        ]
        for _ in range(10):
            body = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
            for j, a in enumerate(calls, 2):
                body.append(
                    {
                        "jsonrpc": "2.0",
                        "id": j,
                        "method": "tools/call",
                        "params": {"name": "change_impact", "arguments": a},
                    }
                )
            p = subprocess.run(
                [sys.executable, "-m", "lattice.mcp", "serve", "--project", str(w.s), "--readonly"],
                input="\n".join(json.dumps(m) for m in body) + "\n",
                capture_output=True,
                text=True,
                env=w.env(w.tmp("pw-mcp")),
                cwd=REPO,
                timeout=300,
                check=False,
            )
            rcs.append(p.returncode)
            for ln in p.stdout.splitlines():
                res = json.loads(ln).get("result", {})
                if res.get("isError"):
                    text = json.dumps(res)
                    mcp_errors["observe_refused"] += "readonly:" in text
                elif res.get("content"):
                    mcp_errors["ok"] += 1
        # a server that is not read-only: observe=true really runs the app (once)
        body = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "change_impact",
                    "arguments": {"candidate": proj_cand, "observe": True, "section": "tier1"},
                },
            },
        ]
        p = subprocess.run(
            [sys.executable, "-m", "lattice.mcp", "serve", "--project", str(w.s)],
            input="\n".join(json.dumps(m) for m in body) + "\n",
            capture_output=True,
            text=True,
            env=w.env(w.tmp("pw-mcp2")),
            cwd=REPO,
            timeout=1200,
            check=False,
        )
        rcs.append(p.returncode)
        observed = [
            json.loads(ln) for ln in p.stdout.splitlines() if '"id": 2' in ln or '"id":2' in ln
        ]
        observed_ok = bool(observed) and not observed[0].get("result", {}).get("isError")
        snap1 = {str(r): IP.snapshot(r) for r in roots}
    finally:
        shutil.rmtree(inside, ignore_errors=True)
    diffs = {r: IP.diff_snapshots(snap0[r], snap1[r]) for r in snap0}
    changed = sum(len(d["changed"]) for d in diffs.values())
    new = sum(len(d["new"]) + len(d["gone"]) for d in diffs.values())
    shim_calls = [ln for ln in w.shim_log.read_text().splitlines() if ln.strip()]
    ok = (
        changed == 0
        and new == 0
        and not shim_calls
        and all(rc in (0, 3) for rc in rcs)
        and observed_ok
        and mcp_errors["observe_refused"] == 10
    )
    return result(
        "PW",
        "no write: snapshots around the CLI x10, the MCP tool x10 (stdio, --readonly, observe,"
        " plus one non-readonly observe), with a recording claude shim",
        "0 changed, 0 new, 0 shim calls",
        {
            "changed": changed,
            "new": new,
            "diffs": {k: v for k, v in diffs.items() if any(v.values())},
            "shim_calls": shim_calls,
            "rcs": rcs,
            "mcp": mcp_errors,
            "observe_ok": observed_ok,
        },
        ok,
    )


FAULT = r"""
import importlib
import sys
C = importlib.import_module("lattice.spec.check")
orig = C._run_scenario
state = {"n": 0}
def boom(*a, **k):
    state["n"] += 1
    if state["n"] >= 3:
        raise RuntimeError("injected fault in the candidate's check")
    return orig(*a, **k)
C._run_scenario = boom
from lattice.spec.impact_report import main
raise SystemExit(main(sys.argv[1:]))
"""

TIMEOUT_SERVER = r"""
import sys
import lattice.mcp.tools as T
T.IMPACT_OBSERVE_TIMEOUT = float(sys.argv[1])
from lattice.mcp.__main__ import main
raise SystemExit(main(sys.argv[2:]))
"""


def probe_leak(w: World, before: list[str]) -> dict[str, Any]:
    cand = w.changes["dev1_status"]["dir"]
    # a fault run: the candidate's check raises mid-run
    tmp = w.tmp("leak-fault")
    script = tmp / "fault.py"
    script.write_text(FAULT)
    p = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project",
            str(w.s),
            "--candidate",
            str(cand),
            "--inventory",
            str(w.inv["S"]),
            "--tiers",
            "1",
            "--json",
        ],
        capture_output=True,
        text=True,
        env=w.env(tmp),
        cwd=REPO,
        timeout=900,
        check=False,
    )
    after_fault = ours_now()
    fault = {
        "rc": p.returncode,
        "error": (json.loads(p.stdout).get("error") if p.stdout.strip().startswith("{") else None),
        "left": after_fault,
    }
    # an MCP timeout: the observed run is killed at 25 s while its container is up
    tmp2 = w.tmp("leak-timeout")
    script2 = tmp2 / "server.py"
    script2.write_text(TIMEOUT_SERVER)
    inside = w.s / "leak-cand"
    shutil.copytree(cand, inside)
    try:
        body = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "change_impact",
                    "arguments": {"candidate": "leak-cand", "observe": True},
                },
            },
        ]
        q = subprocess.run(
            [sys.executable, str(script2), "25", "serve", "--project", str(w.s)],
            input="\n".join(json.dumps(m) for m in body) + "\n",
            capture_output=True,
            text=True,
            env=w.env(tmp2),
            cwd=REPO,
            timeout=600,
            check=False,
        )
    finally:
        shutil.rmtree(inside, ignore_errors=True)
    time.sleep(2)
    after_timeout = ours_now()
    timeout_text = "timeout:" in q.stdout
    left = sorted(set(after_timeout) - set(before))
    return result(
        "LEAK",
        "containers owned by this work after a fault run and an MCP timeout",
        "0",
        {
            "fault": fault,
            "mcp_timeout_reported": timeout_text,
            "left_after_timeout": after_timeout,
            "before": before,
        },
        not left and not after_fault and timeout_text,
    )


# ---- PB: byte-compat against bb3ff0c ----------------------------------------------------------

FROZEN_PATHS = [
    "lattice/spec/incremental.py",
    "lattice/studio/server.py",
    "lattice/spec/check.py",
    "lattice/spec/implementation.py",
    "lattice/gocheck",
    "lattice/studio/session.py",
    "lattice/cli.py",
    "lattice/importer",
]

RECORD_SCRIPT = r"""
import json, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lattice.llm.client import ScriptedGenerator
from lattice.studio.server import make_server
from tests.studio.test_server import V2_DRAFT, NOTES_V2, edit_reply, make_project, request
root = make_project(Path(tempfile.mkdtemp()) / "p", app_limit=3, repo=False)
srv = make_server(root, 0, generator=ScriptedGenerator([edit_reply(NOTES_V2), V2_DRAFT]))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
_, out = request("POST", base + "/api/changes", {"request": "allow 5 notes", "selected": []})
for _ in range(600):
    st, c = request("GET", f"{base}/api/changes/{out['id']}")
    if c["status"] not in ("drafting", "checking"):
        break
    time.sleep(0.1)
c.pop("timings", None); c.pop("usage", None)
print(json.dumps(c, sort_keys=True, default=str))
srv.shutdown()
"""


TOOLS_SCRIPT = r"""
import json, sys
from pathlib import Path
from lattice.mcp.tools import PendingBackend, Toolbox
out = {}
for arg in sys.argv[1:]:
    name, _, d = arg.partition("=")
    tb = Toolbox(PendingBackend(Path(d)))
    out[name] = {"names": tb.names(), "defs": tb.list_tools()}
print(json.dumps(out, sort_keys=True))
"""


def probe_pb(w: World, pytest_log: Path | None, check_log: Path | None) -> dict[str, Any]:
    base = w.scratch / "base-tree"
    if not base.is_dir():
        base.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", BASE_COMMIT], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(base)], input=tar, check=True)
    checks: dict[str, Any] = {}

    # help texts
    def helps(code: Path) -> dict[str, str]:
        out = {}
        for key, argv in (
            ("lattice --help", ["-m", "lattice.cli", "--help"]),
            ("lattice spec", ["-m", "lattice.cli", "spec"]),
            ("check --help", ["-m", "lattice.spec", "check", "--help"]),
            ("draft --help", ["-m", "lattice.spec", "draft", "--help"]),
        ):
            p = IP.run_py(argv, w.tmp("help"), pythonpath=code, cwd=code)
            out[key] = p.stdout + "\n--stderr--\n" + p.stderr + f"\nrc={p.returncode}"
        return out

    new_h, old_h = helps(REPO), helps(base)
    checks["help_identical"] = {k: new_h[k] == old_h[k] for k in new_h}
    # PB_SCRIPT: initialize instructions and tools/list in 8 configs, ask --json, the route table
    script = w.scratch / "pb_script.py"
    script.write_text(IP.PB_SCRIPT)
    n = IP.run_py([str(script), str(REPO)], w.tmp("pb-new"), pythonpath=REPO)
    o = IP.run_py([str(script), str(base)], w.tmp("pb-old"), pythonpath=base, cwd=base)
    assert n.returncode == 0 and o.returncode == 0, (n.stderr[-600:], o.stderr[-600:])
    nj, oj = json.loads(n.stdout), json.loads(o.stdout)
    differing = sorted(k for k in set(nj) | set(oj) if nj.get(k) != oj.get(k))
    checks["script_keys"] = len(nj)
    checks["script_differing"] = differing
    # /api/model and /api/ask on the dogfood project, from the base and from this tree
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "dogfood_studio_harness", REPO / "dogfood/studio/harness.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    proj_root = w.scratch / "pb-project"
    shutil.rmtree(proj_root, ignore_errors=True)
    (proj_root / "old").mkdir(parents=True)
    proj_old = mod.make_project(proj_root / "old")
    got = {}
    if proj_old is not None:
        for name, code in (("base", base), ("new", REPO)):
            shutil.rmtree(proj_root / "run", ignore_errors=True)
            shutil.copytree(proj_old, proj_root / "run", symlinks=True)
            got[name] = IP.serve_and_get(code, proj_root / "run", w.tmp(f"pb-api-{name}"))
        checks["api_model_ask_identical"] = json.dumps(got["base"], sort_keys=True) == json.dumps(
            got["new"], sort_keys=True
        )
    # a change record (stub generator, notes project with an app): identical across the trees
    rn = IP.run_py(
        ["-c", RECORD_SCRIPT, str(REPO)], w.tmp("rec-new"), pythonpath=REPO, cwd=REPO, timeout=600
    )
    ro = IP.run_py(
        ["-c", RECORD_SCRIPT, str(base)], w.tmp("rec-old"), pythonpath=base, cwd=base, timeout=600
    )
    checks["change_record_identical"] = (
        rn.returncode == 0 and ro.returncode == 0 and rn.stdout == ro.stdout
    )
    if rn.returncode or ro.returncode:
        checks["change_record_error"] = [rn.stderr[-300:], ro.stderr[-300:]]
    # no diff of the frozen files
    d = subprocess.run(
        ["git", "-C", str(REPO), "diff", BASE_COMMIT, "--", *FROZEN_PATHS],
        capture_output=True,
        text=True,
        check=False,
    )
    checks["frozen_diff_empty"] = d.stdout == "" and d.returncode == 0
    checks["frozen_paths"] = FROZEN_PATHS
    # goextract and its output bytes: the Go sources are in lattice/gocheck (FROZEN_PATHS); the
    # extract of S taken by the base tree and by this tree is byte-identical
    ext = {}
    for name, code in (("base", base), ("new", REPO)):
        out_dir = w.tmp(f"pb-ext-{name}") / "inv"
        p = IP.run_py(
            ["-m", "lattice.cli", "import", "inventory", str(w.s), "--out", str(out_dir)],
            w.tmp(f"pb-ext2-{name}"),
            pythonpath=code,
            cwd=code,
        )
        ext[name] = (out_dir / "extract.json").read_bytes() if p.returncode == 0 else None
    checks["goextract_output_identical"] = ext["base"] is not None and ext["base"] == ext["new"]
    # tools/list: without [impact] it is the base tree's list (with or without [implementation]);
    # with [impact] it is that list plus change_impact
    cfgs = {
        "plain": '[llm]\nbackend = "stub"\n',
        "impl": (
            f'[implementation]\ninventory = "{w.inv["S"]}"\napp_root = "{w.s}"\n'
            '[llm]\nbackend = "stub"\n'
        ),
    }
    cfg_dirs = {}
    for name, text in cfgs.items():
        for suffix, extra in (("", ""), ("+impact", "\n[impact]\n")):
            d = w.scratch / f"pb-tools-{name}{suffix}"
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir()
            (d / "studio.toml").write_text(text + extra)
            cfg_dirs[name + suffix] = d
    tools_script = w.scratch / "pb_tools.py"
    tools_script.write_text(TOOLS_SCRIPT)
    listing = {}
    for tree_name, code in (("base", base), ("new", REPO)):
        p = IP.run_py(
            [str(tools_script), *[f"{k}={v}" for k, v in cfg_dirs.items()]],
            w.tmp(f"pb-tools-{tree_name}"),
            pythonpath=code,
            cwd=code,
        )
        listing[tree_name] = (
            json.loads(p.stdout) if p.returncode == 0 else {"error": p.stderr[-300:]}
        )
    nl, bl = listing["new"], listing["base"]
    checks["tools_without_table_equal_base"] = (
        all(nl.get(k) == bl.get(k) for k in ("plain", "impl")) and "error" not in nl
    )
    checks["tools_with_table_is_base_plus_one"] = all(
        nl[k + "+impact"]["names"] == [*nl[k]["names"], "change_impact"]
        and nl[k + "+impact"]["defs"][:-1] == nl[k]["defs"]
        for k in ("plain", "impl")
    )
    checks["tool_names_with_table"] = {
        k: nl[k + "+impact"]["names"][-2:] for k in ("plain", "impl")
    }
    # the regression runs, if given
    if pytest_log is not None and pytest_log.is_file():
        checks["pytest"] = compare_pytest(
            pytest_log, Path(w.args.baseline_ids) if w.args.baseline_ids else None
        )
    if check_log is not None and check_log.is_file():
        text = check_log.read_text()
        checks["studio_check_sh"] = text.strip().splitlines()[-1] if text.strip() else ""
    ok = (
        all(checks["help_identical"].values())
        and not differing
        and checks.get("api_model_ask_identical") is True
        and checks["change_record_identical"]
        and checks["frozen_diff_empty"]
        and checks["goextract_output_identical"]
        and checks["tools_without_table_equal_base"]
        and checks["tools_with_table_is_base_plus_one"]
        and checks.get("pytest", {}).get("same_ids", True)
    )
    return result(
        "PB",
        "byte-compat against bb3ff0c",
        "all identical; with [impact]: tools/list = base + 1",
        checks,
        ok,
    )


def compare_pytest(log: Path, baseline_ids: Path | None) -> dict[str, Any]:
    ids = sorted(
        {
            re.sub(r" - .*", "", ln)
            for ln in log.read_text().splitlines()
            if ln.startswith(("FAILED ", "ERROR "))
        }
    )
    out: dict[str, Any] = {"ids": len(ids), "tail": log.read_text().strip().splitlines()[-1]}
    if baseline_ids is not None and baseline_ids.is_file():
        base = sorted(ln.strip() for ln in baseline_ids.read_text().splitlines() if ln.strip())
        out["baseline_ids"] = len(base)
        out["same_ids"] = base == ids
        out["extra"] = sorted(set(ids) - set(base))
        out["missing"] = sorted(set(base) - set(ids))
    return out


# ---- PS, PI: Studio's own pipeline (scripted generator) vs the report -------------------------


def probe_ps_pi(w: World) -> list[dict[str, Any]]:
    """A `ready` record from Studio's pipeline on the S clone (scripted generator): checks.impact
    against the report's T1 restricted to C/D (PS); `--change cN` against `--candidate` (PI)."""
    from lattice.llm.client import ScriptedGenerator  # noqa: PLC0415
    from lattice.spec.rules import Trace  # noqa: PLC0415
    from lattice.studio.server import IMPACT_LABEL, make_server  # noqa: PLC0415

    cdir = w.changes["dev1_status"]["dir"]
    proj = w.scratch / "ps-project"
    shutil.rmtree(proj, ignore_errors=True)
    shutil.copytree(w.s, proj, symlinks=True)
    state = w.scratch / "state-ps"
    shutil.rmtree(state, ignore_errors=True)
    IP.rewrite_go_src(proj)
    toml = (proj / "studio.toml").read_text()
    for table in ("implementation", "state", "impact"):
        toml = re.sub(rf"(?ms)\n\[{table}\].*?(?=\n\[|\Z)", "", toml)
    (proj / "studio.toml").write_text(toml.rstrip("\n") + f'\n\n[state]\ndir = "{state}"\n')
    new_md = (cdir / "REQUIREMENTS.md").read_text()
    new_module = (cdir / "spec.py").read_text()
    tr = json.loads((cdir / "spec.trace.json").read_text())
    trace_obj = {k: tr[k] for k in ("links", "unchecked", "context", "questions") if k in tr}
    trace_obj["questions"] = [
        {k: v for k, v in q.items() if k != "answer"} for q in tr.get("questions", [])
    ]
    from lattice.studio.server import BEGIN, END  # noqa: PLC0415

    gen = ScriptedGenerator(
        [
            f"Here it is.\n{BEGIN}\n{new_md}{END}\n",
            f"```python\n{new_module}```\n\n```json\n{json.dumps(trace_obj, indent=2)}\n```\n",
        ]
    )
    sys_path = list(sys.path)
    cwd = os.getcwd()
    token = f"impactp-ps{w.seq:06d}"
    os.environ["LATTICE_CONTAINER_OWNER"] = token
    srv = None
    try:
        import threading  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        srv = make_server(proj, 0, generator=gen)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(method: str, path: str, body: Any = None) -> Any:
            r = urllib.request.Request(
                base + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers={"Content-Type": "application/json"},
            )
            return json.loads(urllib.request.urlopen(r, timeout=300).read())

        out = req(
            "POST", "/api/changes", {"request": "answer 400 for a bad password", "selected": []}
        )
        cid = out["id"]
        c = {}
        for _ in range(3000):
            c = req("GET", f"/api/changes/{cid}")
            if c["status"] not in ("drafting", "checking"):
                break
            time.sleep(0.2)
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        os.environ.pop("LATTICE_CONTAINER_OWNER", None)
        remove_owner(token)
        os.chdir(cwd)
        sys.path[:] = sys_path
    studio_failing = sorted(
        (f["target"], f["label"], tuple(f["rules"]))
        for f in (c.get("checks", {}).get("impact") or {}).get("failing", [])
    )
    snap = IP.snapshot(proj)
    run = engine(
        w,
        proj,
        None,
        change=cid,
        state_dir=state,
        inventory=w.inv["S"],
        tiers="1",
        label="ps-change",
    )
    if run.data is None:
        return [
            result(
                "PS",
                "Studio's checks.impact vs the report's T1",
                "same",
                {"error": run.err[-400:], "status": c.get("status")},
                False,
            ),
            not_run("PI", "--change vs --candidate", "PS produced no report"),
        ]
    mine = sorted(
        (i["target"], IMPACT_LABEL[i["label"]], tuple(i["rules"]))
        for i in run.data["tier1"]["failing"]
        if i["layer"] in ("C", "D") and i["target"]
    )
    ps = result(
        "PS",
        "a ready record from Studio's pipeline (scripted stub generator): checks.impact.failing vs "
        "report's T1 restricted to C/D",
        "same (target, label, rules)",
        {
            "status": c.get("status"),
            "studio": [list(x) for x in studio_failing],
            "report": [list(x) for x in mine],
        },
        c.get("status") == "ready" and studio_failing == mine,
    )
    # PI: the same files by --candidate: the record's own new_* fields written out as a directory
    rec = json.loads((state / "changes" / f"{cid}.json").read_text())["private"]
    rec_dir = w.scratch / "ps-record-files"
    shutil.rmtree(rec_dir, ignore_errors=True)
    rec_dir.mkdir()
    (rec_dir / "REQUIREMENTS.md").write_text(rec["new_md"])
    (rec_dir / "spec.py").write_text(rec["new_module"])
    (rec_dir / "spec.trace.json").write_text(rec["new_trace"])
    cand_run = engine(w, proj, rec_dir, inventory=w.inv["S"], tiers="1", label="pi-cand")
    a, b = copy.deepcopy(run.data), copy.deepcopy(cand_run.data)
    for d in (a, b):
        if d:
            d["input"].pop("kind", None)
            d["input"].pop("change", None)
    pi = result(
        "PI",
        "--change cN vs --candidate on the same files",
        "identical except input.kind and change",
        {"equal": a == b, "rc": [run.rc, cand_run.rc]},
        a == b and a is not None,
    )
    del snap, Trace
    return [ps, pi]


# ---- PN: H-nat ---------------------------------------------------------------------------


def probe_pn(w: World) -> dict[str, Any]:
    if not w.s4.exists():
        return not_run("PN", "H-nat", "S4 was not set up (--no-hnat)")
    t_cand = independent_check(w, w.s4, w.s)  # S's spec on the S4 app
    t_base = independent_check(w, w.s4, w.s4)  # S4's own spec on the S4 app
    eng = engine(w, w.s4, w.s, inventory=w.inv["S4"], label="pn")
    diff_file = w.scratch / "hnat.diff"
    d = subprocess.run(
        [
            "git",
            "-C",
            str(STREAK),
            "--no-optional-locks",
            "diff",
            "-U0",
            w.args.streak_ref2,
            w.args.streak_ref,
            "--",
            "*.go",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    diff_file.write_text(d.stdout)
    truth3 = go_truth(w, w.s4, diff_file)
    if eng.data is None or t_cand.data is None or t_base.data is None:
        return result(
            "PN", "H-nat", "T1 35 failing", {"error": [eng.err[-300:], t_cand.err[-300:]]}, False
        )
    tset = {finding_key(f) for f in t_cand.data["findings"]}
    bset = {finding_key(f) for f in t_base.data["findings"]}
    eset = {item_key(i) for i in eng.data["tier1"]["failing"]}
    resolved_truth = sorted(bset - tset)
    resolved_eng = sorted(item_key(i) for i in eng.data["tier1"]["resolved"])
    pf, pd = predicted(w, w.s4, eng.data["tier3"])
    ef = set(truth3["edited_files"])
    ed = {(x["file"], x["name"]) for x in truth3["edited_decls"]}
    t2n = len(eng.data["tier2"]["determined"])
    return result(
        "PN",
        "H-nat (report-only): base = S4's spec, candidate = S's spec, app = S4",
        "T1 35 failing; resolved = {access__logout__as_anon}; T2 0 claims; T3 3-5/5 files (p)",
        {
            "t1_failing_engine": len(eset),
            "t1_failing_truth": len(tset),
            "t1_exact": eset == tset,
            "missed": sorted(tset - eset)[:5],
            "false": sorted(eset - tset)[:5],
            "resolved_engine": [list(x) for x in resolved_eng],
            "resolved_truth": [list(x) for x in resolved_truth],
            "resolved_equal": resolved_eng == resolved_truth,
            "t2_claims": t2n,
            "t3_files_predicted": len(pf),
            "t3_files_true": sorted(ef),
            "t3_files_hit": sorted(pf & ef),
            "t3_file_recall": f"{len(pf & ef)}/{len(ef)}",
            "t3_decl_recall": f"{len(pd & ed)}/{len(ed)}",
            "t3_added_files": truth3["added_files"],
            "engine_seconds": round(eng.seconds, 1),
        },
        eset == tset and resolved_eng == resolved_truth,
    )


# ---- the stages --------------------------------------------------------------------------


def run_dev(w: World, args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    only = {x for x in args.only.split(",") if x}

    def want(pid: str) -> bool:
        return not only or pid in only

    def stage(pid: str, fn: Callable[[], Any]) -> Any:
        if not want(pid):
            return None
        t0 = time.perf_counter()
        try:
            r = fn()
        except Exception as err:  # noqa: BLE001 -- a probe that crashes is a result
            import traceback  # noqa: PLC0415

            r = result(
                pid,
                "crashed",
                None,
                {"error": f"{type(err).__name__}: {err}", "trace": traceback.format_exc()[-1800:]},
                False,
            )
        items = r if isinstance(r, list) else [r]
        for it in items:
            it["seconds"] = round(time.perf_counter() - t0, 1)
            rows.append(it)
        return r

    make_dev_changes(w)
    before = ours_now()
    changes = list(w.changes.values())
    if any(want(p) for p in ("PT1", "PT1l", "PT2", "PT2i", "PT2d", "PU", "PX", "PT1f")):
        run_t1_set(w, changes, inventory=w.inv["S"], project=w.s)
    control = w.changes["dev0_control"].get("engine")
    stage("PT1", lambda: probe_pt1(w, changes, "dev set: "))
    stage("PT1l", lambda: probe_pt1l(w, changes))
    stage("PT1f", lambda: probe_pt1f(w, control))
    stage("PT2", lambda: probe_pt2(w, changes))
    stage("PT2i", lambda: probe_pt2i(w, changes))
    stage("PT2d", lambda: probe_pt2d(w, changes))
    stage("PT2r", lambda: probe_pt2r(w))
    if want("PT2w"):
        rows.append(
            result(
                "PT2w",
                "T2 fails_after_change vs the flow truth on S+ref",
                "0 claims on Streak (r): vacuous",
                {
                    "claims": sum(
                        len(c["engine"].data["tier2"]["flows"])
                        for c in changes
                        if c.get("engine") and c["engine"].data
                    ),
                    "note": "vacuous: the dev set has no reference build; the C stage scores it",
                },
                None,
            )
        )
    stage("PU", lambda: probe_pu(w, changes))
    stage("PD", lambda: probe_pd(w, changes))
    stage("PX", lambda: probe_px(w, control, changes[1].get("engine")))
    stage("PW", lambda: probe_pw(w, changes))
    stage("PS", lambda: probe_ps_pi(w))
    stage(
        "PB",
        lambda: probe_pb(
            w,
            Path(args.pytest_log) if args.pytest_log else None,
            Path(args.check_log) if args.check_log else None,
        ),
    )
    stage("PT1x", lambda: probe_pt1x(w, changes, args.runs))
    stage("PN", lambda: probe_pn(w))
    stage("LEAK", lambda: probe_leak(w, before))
    return rows


def parse_meta(d: Path) -> dict[str, Any]:
    try:
        return json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        return {}


def run_changeset(w: World, args: argparse.Namespace) -> list[dict[str, Any]]:
    """The held-out stage. Reads only what the pre-registration says: each change's files, ref.diff
    and meta.json. The change set is scored once."""
    cs = Path(args.changeset).resolve()
    if not (cs / "READY").is_file():
        raise SystemExit("the change set is not READY")
    rows: list[dict[str, Any]] = []
    ids = sorted(p.name for p in cs.iterdir() if p.is_dir())
    changes: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for cid in ids:
        d = cs / cid
        changes.append(
            {"id": cid, "dir": d, "kind": parse_meta(d).get("kind"), "meta": parse_meta(d)}
        )
    w.changes = {c["id"]: c for c in changes}
    before = ours_now()
    t0 = time.perf_counter()
    # the reference builds: S + ref.diff, checked with the candidate spec (must pass, else excluded)
    for c in changes:
        diff = c["dir"] / "ref.diff"
        if diff.is_file() and diff.stat().st_size == 0:
            # the coordinator's ruling: an empty ref.diff means S+ref = S (no patch is run)
            c["ref_applied"] = True
            c["ref_root"] = w.s
            c["ref_empty"] = True
            c["ref_check"] = independent_check(w, w.s, c["dir"])
            rc = c["ref_check"]
            if rc.data is None or rc.data.get("verdict") != "pass":
                excluded.append(
                    {
                        "change": c["id"],
                        "why": "the ACD check on S+ref is not a pass",
                        "findings": [finding_key(f) for f in (rc.data or {}).get("findings", [])][
                            :5
                        ],
                    }
                )
                c["excluded"] = True
            c["t3_truth"] = {
                "edited_files": [],
                "added_files": [],
                "removed_files": [],
                "edited_decls": [],
            }
            c["flow_truth"] = flow_truth(w, c)
            continue
        ref = w.scratch / "ref" / c["id"]
        G.archive(STREAK, args.streak_ref, ref)
        IP.rewrite_go_src(ref)
        pr = subprocess.run(
            ["patch", "-p1", "-s", "-d", str(ref), "-i", str(diff)],
            capture_output=True,
            text=True,
            check=False,
        )
        c["ref_applied"] = pr.returncode == 0
        if pr.returncode != 0:
            c["ref_error"] = (pr.stdout + pr.stderr)[-300:]
            excluded.append(
                {"change": c["id"], "why": "ref.diff does not apply", "detail": c["ref_error"]}
            )
            continue
        try:
            G.go_build(ref)
        except subprocess.CalledProcessError as err:
            c["ref_error"] = err.stderr.decode()[-300:] if err.stderr else "build failed"
            excluded.append(
                {"change": c["id"], "why": "ref does not build", "detail": c["ref_error"]}
            )
            continue
        c["ref_root"] = ref
        rc = independent_check(w, ref, c["dir"])
        c["ref_check"] = rc
        if rc.data is None or rc.data.get("verdict") != "pass":
            excluded.append(
                {
                    "change": c["id"],
                    "why": "the ACD check on S+ref is not a pass",
                    "findings": [finding_key(f) for f in (rc.data or {}).get("findings", [])][:5],
                }
            )
            c["excluded"] = True
        c["t3_truth"] = go_truth(w, w.s, c["dir"] / "ref.diff")
        # the verified flows replayed on S+ref: the observed failures
        c["flow_truth"] = flow_truth(w, c)
    control = {"id": "control", "dir": w.s, "kind": "control"}
    w.changes["control"] = control
    run_t1_set(w, [control, *changes], inventory=w.inv["S"], project=w.s, label="c")
    live = [c for c in changes if not c.get("excluded") and c.get("ref_applied")]
    rows.append(
        result(
            "C-set",
            "the change set",
            ">= 18 changes, >= 15 survive",
            {
                "changes": len(changes),
                "survive": len(live),
                "excluded": excluded,
                "kinds": sorted({str(c.get("kind")) for c in changes}),
            },
            len(changes) >= 18 and len(live) >= 15,
        )
    )
    rows += probe_pt1(w, [control, *live], "C: ")
    rows.append(probe_pt1l(w, live))
    rows.append(probe_pt2(w, live))
    rows.append(probe_pt2i(w, live))
    rows.append(probe_pt2d(w, live))
    rows.append(probe_pt2w(w, live))
    rows.append(probe_pt3(w, live))
    rows.append(probe_pt3w(w, live))
    rows.append(probe_pu(w, live))
    rows.append(probe_pd(w, live))
    rows.append(probe_pt1x(w, live, args.runs))
    ours = ours_now()
    rows.append(
        result(
            "LEAK",
            "containers owned by this work after the stage",
            "0",
            {"before": before, "after": ours},
            not sorted(set(ours) - set(before)),
        )
    )
    rows.append(
        result(
            "C-wall",
            "wall time of the stage",
            "-",
            {"seconds": round(time.perf_counter() - t0)},
            None,
        )
    )
    return rows


def flow_truth(w: World, c: dict[str, Any]) -> dict[str, Any]:
    """The verified flows replayed on S+ref with the candidate's spec: the observed failures."""
    sys.path.insert(0, str(REPO))
    cmd = [sys.executable, "-c", FLOW_SCRIPT, str(c["ref_root"]), str(c["dir"]), str(w.inv["S"])]
    token = f"impactp-f{w.seq + 1:06d}"
    tmp = w.tmp("flows")
    env = w.env(
        tmp, owner=token, pythonpath=f"{c['dir']}{os.pathsep}{c['ref_root']}{os.pathsep}{REPO}"
    )
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=c["ref_root"],
            timeout=1800,
            check=False,
        )
    finally:
        remove_owner(token)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": p.stderr[-400:], "failing_flows": None}


FLOW_SCRIPT = r"""
import dataclasses, json, sys, tomllib
from pathlib import Path
ref, cand, inv = (Path(a) for a in sys.argv[1:4])
from lattice.spec.__main__ import _runtime, _services, load
from lattice.spec.check import check
from lattice.spec.draft import load_spec
from lattice.spec.implementation import flow_scenario, load_inventory
cfg = tomllib.loads((ref / "studio.toml").read_text())
app, chk = cfg["app"], cfg["check"]
spec, _ = load_spec((cand / "spec.py").read_text())
i = load_inventory(inv, ref)
flows = [f for f in i.workflows["flows"] if f["status"] == "verified"]
spec2 = dataclasses.replace(spec, scenarios=tuple(flow_scenario(f) for f in flows))
rep = check(
    spec2, load(app["app"]), load(app["db"]), layers="CD", login=load(app["login"]),
    runtime=_runtime(app["runtime"]), services=_services(app["services"]),
    database=app["database"],
)


def flow_of(f):
    if f.layer == "D":
        return str(f.rule).removeprefix("scenario: flow_")
    return str(f.case).split(" / ")[0].removeprefix("flow_")


bad = sorted({flow_of(f) for f in rep.findings})
print(json.dumps({"failing_flows": bad, "flows": len(flows),
                  "findings": [[f.layer, f.rule, f.case] for f in rep.findings]}))
"""


def probe_pt2w(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    claims = correct = 0
    rows = {}
    for c in changes:
        e, ft = c.get("engine"), c.get("flow_truth")
        if e is None or e.data is None or not ft or ft.get("failing_flows") is None:
            continue
        got = [
            f["flow"] if "flow" in f else f["target"].removeprefix("flow:")
            for f in e.data["tier2"]["flows"]
        ]
        got = [g.removeprefix("flow:") for g in got]
        claims += len(got)
        correct += len(set(got) & set(ft["failing_flows"]))
        rows[c["id"]] = {"claims": got, "truth": ft["failing_flows"]}
    return result(
        "PT2w",
        "T2 fails_after_change vs the flow truth on S+ref",
        "0 claims on Streak (r): vacuous",
        {"claims": claims, "correct": correct, "vacuous": claims == 0, "per_change": rows},
        claims == correct,
    )


def probe_pt3w(w: World, changes: list[dict[str, Any]]) -> dict[str, Any]:
    tp = tr = pr = 0
    rows = {}
    for c in changes:
        e, ft = c.get("engine"), c.get("flow_truth")
        if e is None or e.data is None or not ft or ft.get("failing_flows") is None:
            continue
        may = {f["target"].removeprefix("flow:") for f in e.data["tier3"]["flows"]}
        truth = set(ft["failing_flows"])
        tp += len(may & truth)
        tr += len(truth)
        pr += len(may)
        rows[c["id"]] = {"may_fail": len(may), "truth": len(truth), "hit": len(may & truth)}
    recall = round(tp / tr, 3) if tr else None
    return result(
        "PT3w",
        "T3 may_fail flows vs the flow truth",
        "recall >= 0.8 (p)",
        {"recall": recall, "recall_n": f"{tp}/{tr}", "may_fail_total": pr, "per_change": rows},
        None if recall is None else recall >= 0.8,
    )


def write_outputs(w: World, rows: list[dict[str, Any]], title: str) -> int:
    out = w.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes.json").write_text(json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n")
    lines = [f"# {title}", ""]
    for r in rows:
        if not r.get("ran", True):
            lines.append(f"- NOT RUN {r['id']}: {r['probe']} -- {r['reason']}")
            continue
        verdict = "PASS" if r["pass"] else ("REPORT" if r["pass"] is None else "FAIL")
        obs = json.dumps(r["observed"], sort_keys=True, default=str)
        lines.append(f"- RAN {r['id']}: {verdict} -- {r['probe']}: observed {obs[:700]}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if all(r.get("pass") in (True, None) for r in rows) else 1


def raw_dump(w: World, tag: str) -> None:
    """Raw engine output and independent truth of every change, for the record."""
    raw = w.out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for cid, c in w.changes.items():
        e, t = c.get("engine"), c.get("truth")
        if e is not None:
            (raw / f"{tag}-{cid}-engine.json").write_text(e.out)
        if t is not None:
            (raw / f"{tag}-{cid}-truth.json").write_text(t.out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", choices=("dev", "changeset"), required=True)
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument(
        "--workflows", default="docs/results/data/2026-10-15-workflow-layer/workflows.json"
    )
    ap.add_argument("--changeset", default="")
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-hnat", action="store_true")
    ap.add_argument("--pytest-log", default="")
    ap.add_argument("--check-log", default="")
    ap.add_argument("--baseline-ids", default="")
    args = ap.parse_args(argv)
    if not docker_up():
        raise SystemExit("Docker is not running")
    w = World(args)
    t0 = time.perf_counter()
    setup(w)
    (w.out / "setup.json").write_text(
        json.dumps(
            {
                "notes": w.notes,
                "streak_ref": args.streak_ref,
                "streak_ref2": args.streak_ref2,
                "inventory_sha256": {
                    k: sha((v / "inventory.json").read_bytes()) for k, v in w.inv.items()
                },
                "workflows_sha256": sha(Path(args.workflows).read_bytes()),
                "go_files": len(w.go_files),
                "setup_seconds": round(time.perf_counter() - t0, 1),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if args.stage == "dev":
        rows = run_dev(w, args)
        raw_dump(w, "dev")
        return write_outputs(w, rows, "Change impact probes (Streak, dev stage)")
    rows = run_changeset(w, args)
    raw_dump(w, "c")
    return write_outputs(w, rows, "Change impact probes (held-out change set)")


if __name__ == "__main__":
    raise SystemExit(main())
