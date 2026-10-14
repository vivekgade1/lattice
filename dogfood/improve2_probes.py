"""Probe runner for spike 3, second half: ``lattice improve verify`` and ``lattice improve draft``.

Implements the probes of ``docs/results/2026-10-13-improve2-prereg.md`` (section 3): V1a-V1h
(the real I-3 data of spike 3), V2 (synthetic, known truth), V3 (ledger edge cases), D1-D2
(drafts and refusals), D3 (safety), D4 (determinism), D5 (the old commands). The predictions
and pass rules are copied from that document; a probe that does not match is reported as a
finding with its raw output, never adapted.

    uv run python dogfood/improve2_probes.py [--runs 10] [--scratch DIR] \
        [--out docs/results/data/2026-10-13-improve2]

Every probe calls the real CLI (``python -m lattice.improve``) in a subprocess on scratch
copies; ``--project`` is a scratch directory and the ledger lines V1 needs are written into a
scratch state directory, never into the committed data. No LLM, no API key, no network. Every
deterministic call runs ``--runs`` times (text and ``--json``) and all runs must be identical.
The first run's raw output and the per-run sha256 go to ``OUT/<probe>.json``;
``OUT/summary.json`` holds the table, the guard (D3) and the disclosed post hoc notes.
"""

from __future__ import annotations

import argparse
import ast
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/results/data/2026-10-12-spike3"
PLANS = ROOT / "docs/results/data/streak-plans-2026-10-11"
DEFAULT_OUT = ROOT / "docs/results/data/2026-10-13-improve2"
BASE_REF = "7358be2"
X = "gate:level_regression:X"
CLOSE = "2026-10-12T23:08:00Z"
REAL_COMMIT = "scratch-clone studio.toml (not merged)"
OVERRIDE = "closing point overridden by --at (ledger ts 2026-10-12T23:12:00Z)"
FIXTURE = '[rebuild]\ngates = ["./check.sh"]\n'
LEVEL_X = 'level_gate_args = ["--x-repeats", "2"]'

# Disclosed notes, written to summary.json (and copied to the results by the orchestrator).
POST_HOC_SCRIPT_CORRECTIONS: list[str] = [
    "V1g: the first complete run (14/15) flagged V1g because the script compared the V1a/V1g "
    "documents field by field and named the differing warnings list 'warnings.0'; when the two "
    "lists differ in length the differing path is 'warnings'. A script bug (the numbers, source "
    "and warning were as predicted); the comparison now lists leaf paths and allows "
    "closing_point.source, warnings and entry.ts (see findings).",
]
FINDINGS: list[str] = []  # differences from a prediction that are not a failed pass rule
DECISIONS: list[str] = [
    "Observed/predicted strings are compact renderings of the fields the prereg's tables name; "
    "the per-field comparison is exact (numbers as printed: costs 2 decimals, relative and "
    "error 3).",
    "A '-' in a prereg cost column (V1b-V1d) is checked as change.delta null; the window means "
    "the command also prints are recorded in the raw data, not asserted.",
    "D1/D2 key lines are checked as substrings or per-line regexes on the draft text; the "
    "full bytes are recorded as observed (the prereg did not predict them).",
]

RUNS = 10
GUARD: dict[str, Any] = {"calls": 0, "changed": []}
SUMMARY_ROWS: list[dict[str, Any]] = []
STATE: dict[str, Any] = {"scratch": None, "n": 0}


# ------------------------------------------------------------------ plumbing


def env_for(tree: Path) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", COLUMNS="100")
    return e


def cli(argv: list[str], *, cwd: Path | None = None, tree: Path = ROOT) -> dict[str, Any]:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.improve", *argv],
        cwd=str(cwd or tree),
        env=env_for(tree),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}


def tree_hash(*dirs: Path) -> dict[str, str]:
    out = {}
    for d in dirs:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def sha(s: str | bytes) -> str:
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()[:16]


class Rig:
    """A scratch project directory and state directory holding a log, a ledger and a toml."""

    def __init__(
        self,
        name: str,
        lines: list[str] | None = None,
        ledger: list[dict[str, Any]] | None = None,
        toml: str | None = None,
    ) -> None:
        STATE["n"] += 1
        self.root = Path(STATE["scratch"]) / f"{STATE['n']:03d}-{name}"
        self.project, self.state = self.root / "proj", self.root / "state"
        self.project.mkdir(parents=True)
        self.state.mkdir()
        evs = i3_lines() if lines is None else lines
        (self.state / "events.jsonl").write_text("".join(ln + "\n" for ln in evs))
        if ledger is not None:
            self.set_ledger(ledger)
        if toml is not None:
            (self.project / "studio.toml").write_text(toml)

    def set_ledger(self, ledger: list[dict[str, Any]]) -> None:
        (self.state / "improvements.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in ledger)
        )

    def argv(self, *a: str) -> list[str]:
        return [*a, "--project", str(self.project), "--state", str(self.state)]

    def call(self, *a: str, cwd: Path | None = None) -> dict[str, Any]:
        """One guarded call: the project and state trees must be byte-identical afterwards."""
        before = tree_hash(self.project, self.state)
        r = cli(self.argv(*a), cwd=cwd)
        after = tree_hash(self.project, self.state)
        GUARD["calls"] += 1
        if before != after:
            GUARD["changed"].append({"argv": list(a), "root": str(self.root)})
        return r


def i3_lines() -> list[str]:
    return (DATA / "i3-events.jsonl").read_text().splitlines()


def entry(
    ts: str | None = CLOSE,
    status: str = "closed",
    id: str | None = "I-01",
    signal: str = X,
    commit: str | None = REAL_COMMIT,
) -> dict[str, Any]:
    e: dict[str, Any] = {"id": id, "signal": signal, "status": status, "commit": commit}
    if ts is not None:
        e["ts"] = ts
    return e


def dig(doc: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(doc, list):
            idx = int(part)
            doc = doc[idx] if idx < len(doc) else "<absent>"
        elif isinstance(doc, dict):
            doc = doc.get(part, "<absent>")
        else:
            return "<absent>"
    return doc


def leaf_diff(a: Any, b: Any, prefix: str = "") -> list[str]:
    """Dotted paths at which two JSON documents differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            out += leaf_diff(a.get(k, "<absent>"), b.get(k, "<absent>"), f"{prefix}{k}.")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += leaf_diff(x, y, f"{prefix}{i}.")
        return out
    return [] if a == b else [prefix.rstrip(".")]


def short(rc: int, d: dict[str, Any] | None) -> str:
    """A compact line for the printed table, from a verify or draft document."""
    if d is None:
        return f"rc{rc} (no json)"
    if d.get("refused"):
        return f"rc{rc} refused:{d['reason']}"
    if d.get("command") == "draft":
        return f"rc{rc} {d['template']} toml={'yes' if d['toml'] else 'no'}"
    c = d["change"]
    p = d["predicted"]["delta"] if d["predicted"] else None
    return (
        f"rc{rc} {d['verdict']} n{d['before']['n']}/{d['after']['n']} "
        f"{d['before']['cost_per']}->{d['after']['cost_per']} d{c['delta']} "
        f"p{p} e{d['error']}"
    )


def sub_check(
    rig: Rig,
    name: str,
    argv: list[str],
    expect: dict[str, Any],
    runs: int,
    texts: list[str] | None = None,
) -> dict[str, Any]:
    """Run `argv --json` and `argv` (text) `runs` times; every run must be identical; compare
    the first run with `expect` (dotted paths into the JSON, plus ``rc``). `texts`: substrings
    the text output must hold."""
    jr = [rig.call(*argv, "--json") for _ in range(runs)]
    tr = [rig.call(*argv) for _ in range(runs)]
    identical = (
        len({(r["rc"], r["out"], r["err"]) for r in jr}) == 1
        and len({(r["rc"], r["out"], r["err"]) for r in tr}) == 1
    )
    first = jr[0]
    try:
        doc = json.loads(first["out"]) if first["out"].strip() else None
    except json.JSONDecodeError:
        doc = None
    mism: list[str] = []
    observed: dict[str, Any] = {}
    for k, want in expect.items():
        got = first["rc"] if k == "rc" else dig(doc, k) if doc is not None else "<no json>"
        observed[k] = got
        if got != want:
            mism.append(f"{k}: predicted {want!r}, observed {got!r}")
    for t in texts or []:
        if t not in tr[0]["out"] + tr[0]["err"]:
            mism.append(f"text lacks {t!r}")
    ok = identical and not mism
    return {
        "name": name,
        "argv": argv,
        "ok": ok,
        "identical": identical,
        "runs": runs,
        "mismatches": mism,
        "expected": expect,
        "observed": observed,
        "short": short(first["rc"], doc),
        "json_stdout": first["out"],
        "json_stderr": first["err"],
        "text_stdout": tr[0]["out"],
        "text_stderr": tr[0]["err"],
        "run_digests": [sha(r["out"] + r["err"] + str(r["rc"])) for r in jr],
        "doc": doc,
    }


def record(
    pid: str,
    predicted: str,
    checks: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
    ok: bool | None = None,
    observed: str | None = None,
) -> dict[str, Any]:
    ok_all = all(c["ok"] for c in checks) if ok is None else ok
    row = {
        "id": pid,
        "predicted": predicted,
        "observed": observed if observed is not None else " ; ".join(c["short"] for c in checks),
        "pass": bool(ok_all),
        "checks": [{k: v for k, v in c.items() if k != "doc"} for c in checks],
        **(extra or {}),
    }
    return row


# ------------------------------------------------------------------ V1: the real I-3 data


def v1_rows(runs: int) -> list[dict[str, Any]]:
    rows = []
    a_exp = {
        "rc": 0,
        "verdict": "improved",
        "before.n": 3,
        "after.n": 3,
        "before.cost_per": 119.51,
        "after.cost_per": 58.5,
        "before.min": 119.31,
        "before.max": 119.75,
        "after.min": 58.44,
        "after.max": 58.56,
        "change.delta": -61.01,
        "change.relative": -0.51,
        "change.separated": True,
        "predicted.delta": -71.7,
        "error": 0.149,
        "closing_point.source": "ledger",
        "context.per_window.before.x_repeats": [5],
        "context.per_window.after.x_repeats": [2],
        "context.per_window.before.selected": [152],
        "context.per_window.before.sha": ["a0679e9"],
    }
    ra = Rig("V1a", ledger=[entry(CLOSE)])
    ca = sub_check(
        ra,
        "V1a",
        ["verify", "I-01"],
        a_exp,
        runs,
        texts=["Verdict: improved", "Error: |measured - predicted| / |predicted| = 14.9%"],
    )
    rows.append(
        record(
            "V1a",
            "improved rc0, 3/3, 119.51->58.50 s, delta -61.01 (-0.510, "
            "separated), predicted -71.7, error 0.149",
            [ca],
        )
    )
    # V1b/V1c: a close on a boundary second
    for pid, ts, nb, na in (
        ("V1b", "2026-10-12T23:07:11Z", 2, 3),
        ("V1c", "2026-10-12T23:09:06Z", 3, 2),
    ):
        r = Rig(pid, ledger=[entry(ts)])
        c = sub_check(
            r,
            pid,
            ["verify", "I-01"],
            {
                "rc": 1,
                "verdict": "not_enough_data",
                "before.n": nb,
                "after.n": na,
                "excluded.at_close": 1,
                "predicted.delta": -71.7,
                "error": None,
                "change.delta": None,
            },
            runs,
        )
        rows.append(
            record(
                pid, f"not_enough_data rc1, {nb}/{na}, at_close 1, predicted -71.7, error null", [c]
            )
        )
    rd = Rig("V1d", ledger=[entry("2026-10-12T23:12:00Z")])
    cd = sub_check(
        rd,
        "V1d",
        ["verify", "I-01"],
        {
            "rc": 1,
            "verdict": "not_enough_data",
            "before.n": 6,
            "after.n": 0,
            "predicted": None,
            "error": None,
            "change.delta": None,
        },
        runs,
    )
    rows.append(
        record(
            "V1d", "not_enough_data rc1, 6/0, predicted null (repeats tie 5/2), error null", [cd]
        )
    )
    re_ = Rig("V1e", ledger=[entry(CLOSE, signal="gate:level_regression:D")])
    ce = sub_check(
        re_,
        "V1e",
        ["verify", "I-01"],
        {
            "rc": 1,
            "verdict": "not_improved",
            "reason": "no material change",
            "before.n": 3,
            "after.n": 3,
            "before.cost_per": 22.52,
            "after.cost_per": 22.31,
            "change.delta": -0.21,
            "change.relative": -0.009,
            "direction": "flat",
            "change.separated": True,
            "predicted": None,
            "error": None,
        },
        runs,
    )
    rows.append(
        record(
            "V1e",
            "not_improved (no material change) rc1, 3/3, 22.52->22.31, "
            "delta -0.21 (-0.009, flat, separated), predicted null",
            [ce],
        )
    )
    rf = Rig("V1f", ledger=[entry(CLOSE, signal="gate:level_regression:*")])
    cf = sub_check(
        rf,
        "V1f",
        ["verify", "I-01"],
        {
            "rc": 0,
            "verdict": "improved",
            "before.n": 3,
            "after.n": 3,
            "keys.3": X,
            "before.cost_per": 142.46,
            "after.cost_per": 81.19,
            "change.delta": -61.27,
            "change.relative": -0.43,
            "predicted": None,
            "error": None,
        },
        runs,
    )
    rows.append(
        record(
            "V1f",
            "improved rc0, 3/3, 4 keys summed, 142.46->81.19, delta -61.27 "
            "(-0.430), predicted null",
            [cf],
        )
    )
    # V1g: V1d's ledger with --at: equal to V1a except closing_point.source and the warning
    rg = Rig("V1g", ledger=[entry("2026-10-12T23:12:00Z")])
    cg = sub_check(
        rg,
        "V1g",
        ["verify", "I-01", "--at", CLOSE],
        {
            **a_exp,
            "closing_point.source": "--at",
            "closing_point.ts": CLOSE,
            "warnings.0": OVERRIDE,
            "entry.ts": "2026-10-12T23:12:00Z",
        },
        runs,
        texts=[OVERRIDE],
    )
    same = None
    if ca["doc"] and cg["doc"]:
        same = sorted(leaf_diff(ca["doc"], cg["doc"]))
        allowed = {"closing_point.source", "warnings", "entry.ts"}
        if not {"closing_point.source", "warnings"} <= set(same) or set(same) - allowed:
            cg["ok"] = False
            cg["mismatches"].append(f"fields differing from V1a: {same}")
        elif set(same) != {"closing_point.source", "warnings"}:
            FINDINGS.append(
                "V1g: the prereg says the output differs from V1a in two fields only "
                "(closing_point.source and the override warning); observed leaf differences: "
                f"{same}. entry.ts echoes the ledger line's own ts, and V1g's ledger line "
                "(ts 23:12:00) is V1d's, not V1a's (23:08:00); every number is V1a's."
            )
    rows.append(
        record(
            "V1g",
            "= V1a: improved rc0, 3/3, 119.51->58.50, error 0.149; source --at + override warning",
            [cg],
            {"fields_differing_from_V1a": same},
        )
    )
    # V1h: a malformed --at, and --at on an open entry
    r1 = Rig("V1h1", ledger=[entry("2026-10-12T23:12:00Z")])
    c1 = sub_check(
        r1,
        "V1h malformed",
        ["verify", "I-01", "--at", "2026-10-12 23:08"],
        {"rc": 3, "refused": True, "reason": "bad_at"},
        runs,
    )
    r2 = Rig("V1h2", ledger=[entry(status="open")])
    c2 = sub_check(
        r2,
        "V1h open",
        ["verify", "I-01", "--at", CLOSE],
        {"rc": 3, "refused": True, "reason": "not_closed"},
        runs,
    )
    rows.append(record("V1h", "rc3 bad_at; rc3 not_closed", [c1, c2]))
    return rows


# ------------------------------------------------------------------ V2: synthetic

C = "2026-10-01T12:00:00Z"
B_TS = ("2026-10-01T10:00:00Z", "2026-10-01T10:30:00Z", "2026-10-01T11:00:00Z")
A_TS = ("2026-10-01T12:30:00Z", "2026-10-01T13:00:00Z", "2026-10-01T13:30:00Z")
_n = [0]


def ev(kind: str, ts: str, **f: Any) -> str:
    _n[0] += 1
    return json.dumps({"v": 1, "ts": ts, "project": "t", "kind": kind, "id": f"e-{_n[0]:05d}", **f})


def gate(ts: str, x: float, repeats: int, d: float | None = None) -> str:
    layers = {"X": x} if d is None else {"X": x, "D": d}
    return ev(
        "gate_run",
        ts,
        gate="level",
        mode="regression",
        seconds=sum(layers.values()),
        layer_seconds=layers,
        x_repeats=repeats,
    )


def node(
    ts: str,
    status: str,
    cause: str | None = None,
    wall: float | None = None,
    usd: float | None = None,
) -> str:
    f: dict[str, Any] = {"status": status}
    if cause:
        f["cause"] = cause
    if wall is not None:
        f["wall_s"] = wall
    if usd is not None:
        f["usage"] = {"cost_usd_list_estimate": usd}
    return ev("node_ended", ts, **f)


def v2_lines(before=(100, 101, 99), after=(40, 41, 39), d_b=None, d_a=None) -> list[str]:
    return [gate(t, x, 5, d_b) for t, x in zip(B_TS, before, strict=True)] + [
        gate(t, x, 2, d_a) for t, x in zip(A_TS, after, strict=True)
    ]


def timeouts_before() -> list[str]:
    return [node(t, "gave_up", "timeout", 600.0) for t in B_TS] + [
        node("2026-10-01T11:10:00Z", "passed"),
        node("2026-10-01T11:20:00Z", "passed"),
    ]


def report_recurred(rig: Rig, key: str) -> Any:
    r = rig.call("report", "--json")
    try:
        rows = json.loads(r["out"])["closed"]
    except (json.JSONDecodeError, KeyError):
        return "<no report>"
    return next((c["recurred"] for c in rows if c["signal_key"] == key), "<not closed>")


def v2_rows(runs: int) -> list[dict[str, Any]]:
    sub: list[dict[str, Any]] = []
    a_nums = {
        "before.cost_per": 100.0,
        "after.cost_per": 40.0,
        "change.delta": -60.0,
        "change.relative": -0.6,
        "predicted.delta": -60.0,
        "error": 0.0,
    }

    def row(
        rid: str,
        pred: str,
        lines: list[str],
        expect: dict[str, Any],
        *,
        signal: str = X,
        canonical: bool = True,
        texts: list[str] | None = None,
    ) -> None:
        rig = Rig(rid, lines, [entry(C, signal=signal)])
        c = sub_check(rig, rid, ["verify", "I-01"], expect, runs, texts)
        extra: dict[str, Any] = {}
        if canonical and c["doc"]:
            rec = report_recurred(rig, signal) if "*" not in signal else None
            extra["report_recurred"] = rec
            extra["after_frequency"] = c["doc"]["after"]["frequency"]
            if rec != c["doc"]["after"]["frequency"]:
                c["ok"] = False
                c["mismatches"].append(f"after.frequency != report closed[].recurred ({rec})")
        sub.append(record(rid, pred, [c], extra))

    row(
        "V2a",
        "improved rc0, 100.00->40.00, delta -60.00 (-0.600), predicted -60.0, error 0.000",
        v2_lines(),
        {"rc": 0, "verdict": "improved", **a_nums},
    )
    row(
        "V2b",
        "not_improved rc1, delta 0.00, flat, error 1.000",
        v2_lines(after=(100, 99, 101)),
        {
            "rc": 1,
            "verdict": "not_improved",
            "change.delta": 0.0,
            "direction": "flat",
            "error": 1.0,
        },
    )
    row(
        "V2c",
        "not_improved (cost rose) rc1, delta +10.00 (+0.100, up), error 1.167",
        v2_lines(after=(110, 111, 109)),
        {
            "rc": 1,
            "verdict": "not_improved",
            "reason": "cost rose",
            "change.delta": 10.0,
            "change.relative": 0.1,
            "direction": "up",
            "error": 1.167,
        },
    )
    after_d = [node(t, "passed") for t in A_TS] + [
        node("2026-10-01T13:40:00Z", "gave_up", "timeout", 600.0)
    ]
    row(
        "V2d",
        "recurred rc1, f 3->1, exposure 5->4",
        timeouts_before() + after_d,
        {
            "rc": 1,
            "verdict": "recurred",
            "before.frequency": 3,
            "after.frequency": 1,
            "before.exposure": 5,
            "after.exposure": 4,
        },
        signal="cause:timeout",
    )
    three = [node(t, "passed") for t in A_TS]
    row(
        "V2d'a",
        "improved rc0, exposure_after 3",
        timeouts_before() + three,
        {"rc": 0, "verdict": "improved", "after.exposure": 3},
        signal="cause:timeout",
    )
    row(
        "V2d'b",
        "not_enough_data rc1, exposure_after 2",
        timeouts_before() + three[:2],
        {"rc": 1, "verdict": "not_enough_data", "after.exposure": 2},
        signal="cause:timeout",
    )
    row(
        "V2e",
        "not_enough_data rc1, n_after 0, predicted -60.0, error null",
        v2_lines()[:3],
        {
            "rc": 1,
            "verdict": "not_enough_data",
            "after.n": 0,
            "predicted.delta": -60.0,
            "error": None,
        },
    )
    row(
        "V2f",
        "not_enough_data rc1, n_after 1",
        v2_lines()[:4],
        {"rc": 1, "verdict": "not_enough_data", "after.n": 1},
    )
    interleaved = v2_lines(d_b=20.0, d_a=30.0) + [
        ev("finding_class", f"2026-10-01T1{i}:15:00Z", signal_key="lint:L012", count=1, cost_s=60.0)
        for i in range(4)
    ]
    row(
        "V2g",
        "as V2a; keys [X]; D and lint absent",
        interleaved,
        {
            "rc": 0,
            "verdict": "improved",
            **a_nums,
            "keys": [X],
            "per_key.0.key": X,
            "per_key.1": "<absent>",
        },
    )
    # V2h (ii), (iii), (iv)
    sub.append(_v2h(runs))
    row(
        "V2i",
        "as V2a; at_close 1, means unchanged",
        [*v2_lines(), gate(C, 1000.0, 5)],
        {"rc": 0, "verdict": "improved", **a_nums, "excluded.at_close": 1},
    )
    row(
        "V2j",
        "as V2a; undated 1",
        [*v2_lines(), gate("2026-10-01 13:00", 1000.0, 5)],
        {"rc": 0, "verdict": "improved", **a_nums, "excluded.undated": 1},
        canonical=False,
    )
    return sub


def _v2h(runs: int) -> dict[str, Any]:
    cs = []
    before = [node(t, "gave_up", "token_budget", usd=0.5) for t in B_TS]
    r = Rig(
        "V2h-ii",
        before + [node(t, "passed") for t in A_TS],
        [entry(C, signal="cause:token_budget")],
    )
    cs.append(
        sub_check(
            r,
            "V2h (ii)",
            ["verify", "I-01"],
            {
                "rc": 0,
                "verdict": "improved",
                "unit": "usd",
                "before.cost_per": 0.5,
                "after.cost_per": None,
            },
            runs,
        )
    )
    before = [ev("human", t, what="resume", actor="owner") for t in B_TS]
    after = [ev("plan_started", t, plan="p") for t in A_TS]
    r = Rig("V2h-iii", before + after, [entry(C, signal="human:resume")])
    cs.append(
        sub_check(
            r,
            "V2h (iii)",
            ["verify", "I-01"],
            {
                "rc": 0,
                "verdict": "improved",
                "unit": None,
                "before.cost_per": None,
                "after.exposure": 3,
            },
            runs,
        )
    )
    before = [node(t, "gave_up", "timeout", 600.0) for t in B_TS]
    after = [node("2026-10-01T12:30:00Z", "gave_up", "timeout", usd=0.5)]
    r = Rig("V2h-iv", before + after, [entry(C, signal="cause:timeout")])
    cs.append(
        sub_check(
            r,
            "V2h (iv)",
            ["verify", "I-01"],
            {
                "rc": 1,
                "verdict": "recurred",
                "unit": "s",
                "before.cost_per": 600.0,
                "after.cost_per": None,
            },
            runs,
        )
    )
    return record(
        "V2h",
        "(ii) improved usd 0.50->null; (iii) improved unit null; (iv) recurred unit s, after null",
        cs,
    )


# ------------------------------------------------------------------ V3: ledger edge cases


def v3_rows(runs: int) -> list[dict[str, Any]]:
    out = []
    a_nums = {
        "rc": 0,
        "verdict": "improved",
        "before.cost_per": 119.51,
        "after.cost_per": 58.5,
        "change.delta": -61.01,
        "error": 0.149,
    }

    def one(
        rid: str,
        pred: str,
        ledger: list[dict[str, Any]],
        expect: dict[str, Any],
        ident: str = "I-01",
    ) -> None:
        r = Rig(rid, ledger=ledger)
        out.append(record(rid, pred, [sub_check(r, rid, ["verify", ident], expect, runs)]))

    one(
        "V3a",
        "rc3 not_closed",
        [entry(status="open")],
        {"rc": 3, "refused": True, "reason": "not_closed"},
    )
    one(
        "V3b",
        "rc3 not_closed",
        [entry(), entry("2026-10-12T23:10:00Z", status="open")],
        {"rc": 3, "refused": True, "reason": "not_closed"},
    )
    r = Rig("V3c", ledger=[entry()])
    c1 = sub_check(
        r, "V3c I-99", ["verify", "I-99"], {"rc": 3, "refused": True, "reason": "unknown_id"}, runs
    )
    c2 = sub_check(
        r,
        "V3c imp-00000000",
        ["verify", "imp-00000000"],
        {"rc": 3, "refused": True, "reason": "unknown_id"},
        runs,
    )
    out.append(record("V3c", "rc3 unknown_id (I-99, imp-00000000)", [c1, c2]))
    one(
        "V3d",
        "rc0, V1a's numbers, warning no commit recorded",
        [entry(commit=None)],
        {**a_nums, "warnings.0": "no commit recorded"},
    )
    one(
        "V3e",
        "rc0, closing point 23:08:00, V1a's numbers, warning closed 2 times in a row",
        [entry(CLOSE), entry("2026-10-12T23:12:00Z")],
        {**a_nums, "closing_point.ts": CLOSE, "warnings.0": "closed 2 times in a row"},
    )
    one(
        "V3f",
        "rc3 no_closing_point",
        [entry(ts=None)],
        {"rc": 3, "refused": True, "reason": "no_closing_point"},
    )
    one(
        "V3g",
        "rc0, V1a's numbers (id null, by imp-3e1cd7f9)",
        [entry(id=None)],
        {**a_nums, "entry.id": None},
        ident="imp-3e1cd7f9",
    )
    return out


# ------------------------------------------------------------------ D1/D2: drafts


def headings(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, re.M)


CR = [
    "What to change",
    "Why",
    "Evidence",
    "Acceptance gate",
    "Measure afterwards",
    "Not decided here",
]
CFG = ["What to change", "Expected", *CR[1:]]
PRE = [
    "1. Status",
    "2. Question",
    "3. Signal and evidence",
    "4. Probe",
    "5. Pass rule and blind prediction",
    "6. Measure afterwards",
    "7. Blind spots",
]
MOD = [
    "Module skeleton",
    "What it must provide",
    "Evidence",
    "Acceptance gate",
    "Measure afterwards",
    "Not decided here",
]


def draft_check(
    rig: Rig,
    pid: str,
    ident: str,
    runs: int,
    *,
    template: str,
    head: list[str],
    lines: list[str],
    regexes: list[str],
    toml: bool,
    title: str,
) -> dict[str, Any]:
    c = sub_check(
        rig, pid, ["draft", ident], {"rc": 0, "template": template, "proposal.id": ident}, runs
    )
    doc = c["doc"] or {}
    text = doc.get("text", "")
    if headings(text) != head:
        c["mismatches"].append(f"headings {headings(text)} != {head}")
    if not text.startswith(title + "\n"):
        c["mismatches"].append(f"title line is not {title!r}")
    for ln in lines:
        if ln not in text:
            c["mismatches"].append(f"missing key line {ln!r}")
    for rx in regexes:
        if not re.search(rx, text, re.M):
            c["mismatches"].append(f"missing pattern {rx!r}")
    if bool(doc.get("toml")) != toml:
        c["mismatches"].append(f"toml present {bool(doc.get('toml'))} != {toml}")
    if "NOT APPLIED: this draft changes nothing; the owner decides." not in text:
        c["mismatches"].append("missing NOT APPLIED line")
    if c["text_stdout"] != text:
        c["mismatches"].append("text output differs from the json text")
    c["ok"] = c["identical"] and not c["mismatches"]
    c["draft_bytes"] = len(text.encode())
    return c


def d1_rows(runs: int) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from tests.improve.test_analyze import i4_lint_noise  # the I-4 fixture of spike 3

    cs = []
    lint_lines = [json.dumps(e) for e in i4_lint_noise()]
    r = Rig("D1a", lint_lines)
    cs.append(
        draft_check(
            r,
            "D1a",
            "imp-f377ca42",
            runs,
            template="change_request",
            head=CR,
            title="# Draft: Lint L012: tune it (it fired 6 time(s) at a cost)",
            lines=[
                "lattice/spec/lint.py, lattice/spec/preconditions.py",
                "signal lint:L012: 6 x 180.0 s = 1080.0 s [A]",
                "As of 2026-10-01T01:00:00Z",
                "the owner names the gate",
                "minimum 3 exposures (change events)",
            ],
            regexes=[rf"^e-2026-10-01T00:0{n}:00Z \S+ 180\.0 s$" for n in range(6)],
            toml=False,
        )
    )
    r = Rig("D1b", i3_lines()[:9], toml=FIXTURE)
    cs.append(
        draft_check(
            r,
            "D1b",
            "imp-3e1cd7f9",
            runs,
            template="config",
            head=CFG,
            title="# Draft: Fewer layer X repeats at level gates (regression): level_gate_args = "
            '["--x-repeats", "2"]',
            lines=[
                "[rebuild]",
                LEVEL_X,
                "current: not set",
                "expected: X 119.5 s -> saving 71.7 s a gate (60%; x_repeats 5 -> 2)",
                "signal gate:level_regression:X: 3 x 119.5 s = 358.5 s [A]",
                "`./check.sh`",
                "As of 2026-10-12T23:07:11Z",
                "--id imp-3e1cd7f9 --commit <SHA>",
                "lattice improve verify imp-3e1cd7f9",
            ],
            regexes=[
                r"^e-e8d9c91c15964512 \S+ 119\.8 s$",
                r"^e-327c71cd641f49a0 \S+ 119\.3 s$",
                r"^e-f6061c7895644d98 \S+ 119\.5 s$",
            ],
            toml=True,
        )
    )
    r = Rig("D1c", i3_lines()[:9])
    cs.append(
        draft_check(
            r,
            "D1c",
            "imp-7fad29b4",
            runs,
            template="module",
            head=MOD,
            title="# Draft: Service stubs that record what they were sent",
            lines=[
                'name = "stub_observability"',
                'kind = "custom"',
                'description = "Service stubs that record what they were sent"',
                "signal unchecked:stub_observability: f = 3 (no cost recorded) [C]",
                "e-ddf0ec95bc824d83",
                "minimum 1 snapshot",
            ],
            regexes=[],
            toml=True,
        )
    )
    r = Rig("D1d", i3_lines()[:9])
    cs.append(
        draft_check(
            r,
            "D1d",
            "imp-a8f0a344",
            runs,
            template="prereg",
            head=PRE,
            title="# Unchecked rules (free text): pre-registration (stub)",
            lines=["written BEFORE any probe", "f = 5"],
            regexes=[r"^PREDICT:$"],
            toml=False,
        )
    )
    r = Rig("D1e", i3_lines())
    c = draft_check(
        r,
        "D1e",
        "imp-3e1cd7f9",
        runs,
        template="prereg",
        head=PRE,
        title="# Fewer layer X repeats at level gates (regression): level_gate_args = "
        '["--x-repeats", "2"]: pre-registration (stub)',
        lines=["6 x 89.0 s = 534.0 s [A]"],
        regexes=[],
        toml=False,
    )
    if "expected:" in (c["doc"] or {}).get("text", ""):
        c["mismatches"].append("an expected line is present")
        c["ok"] = False
    cs.append(c)
    r = Rig("D1f", i3_lines()[:9], toml=FIXTURE)
    cs.append(
        draft_check(
            r,
            "D1f",
            "imp-256fef6a",
            runs,
            template="config",
            head=CR,
            title="# Draft: Cap Await waits at level gates (regression): level_gate_args += "
            '["--await-cap", "5"]',
            lines=['level_gate_args = ["--await-cap", "5"]', "3 x 22.5 s = 67.5 s [A]"],
            regexes=[],
            toml=True,
        )
    )
    return record(
        "D1",
        "6 of 6 drafts hold every required heading in order and every key line",
        cs,
        observed=f"{sum(c['ok'] for c in cs)} of 6: "
        + ", ".join(f"{c['name']} {c['doc']['template'] if c['doc'] else '-'}" for c in cs),
    )


def d2_rows(runs: int) -> dict[str, Any]:
    from tests.improve.test_analyze import i4_checker

    cs = []
    outdir = Path(STATE["scratch"]) / "d2-out"
    outdir.mkdir()

    def refusal(
        rig: Rig, pid: str, ident: str, reason: str, detail_has: list[str], out: Path | None = None
    ) -> dict[str, Any]:
        out = out or (outdir / f"{pid.replace(' ', '_')}.md")
        c = sub_check(
            rig,
            pid,
            ["draft", ident, "--out", str(out)],
            {"rc": 3, "refused": True, "reason": reason, "command": "draft"},
            runs,
        )
        detail = (c["doc"] or {}).get("detail", "")
        for h in detail_has:
            if h not in detail:
                c["mismatches"].append(f"detail lacks {h!r}: {detail!r}")
        c["out_exists"] = out.exists()
        if out.exists():
            c["mismatches"].append(f"{out} exists after a refusal")
        if c["json_stderr"] != f"lattice improve: refused: {detail}\n":
            c["mismatches"].append("stderr is not 'lattice improve: refused: <detail>'")
        c["ok"] = c["identical"] and not c["mismatches"]
        return c

    r = Rig("D2-checker-i4", [json.dumps(e) for e in i4_checker()])
    cs.append(
        refusal(
            r,
            "D2 checker (I-4)",
            "imp-56861ff8",
            "needs_owner",
            ["lattice/spec/check.py", "never drafted automatically"],
        )
    )
    r = Rig("D2-checker-i3", i3_lines()[:9])
    cs.append(
        refusal(
            r,
            "D2 checker (I-3)",
            "imp-56861ff8",
            "needs_owner",
            ["lattice/spec/check.py", "never drafted automatically"],
        )
    )
    r = Rig("D2-model", i3_lines()[:9])
    cs.append(
        refusal(
            r,
            "D2 model.py",
            "imp-adeea5ef",
            "needs_owner",
            ["lattice/spec/model.py", "never drafted automatically"],
        )
    )
    r = Rig("D2-closed", i3_lines(), [entry()])
    cs.append(refusal(r, "D2 closed", "imp-3e1cd7f9", "closed", ["closed by I-01 at " + CLOSE]))
    r = Rig("D2-unknown", i3_lines()[:9])
    cs.append(refusal(r, "D2 unknown", "imp-00000000", "unknown_id", []))
    r = Rig("D2-out", i3_lines()[:9], toml=FIXTURE)
    cs.append(
        refusal(
            r, "D2 out in project", "imp-3e1cd7f9", "out_in_project", [], out=r.project / "draft.md"
        )
    )
    # 5 rows: checker (two logs), model, closed, unknown, out_in_project
    r_ok = [cs[0]["ok"] and cs[1]["ok"], *[c["ok"] for c in cs[2:]]]
    return record(
        "D2",
        "5 of 5 refusals (rc3, reasons needs_owner x2 logs, needs_owner model.py, "
        "closed, unknown_id, out_in_project); no --out file after any",
        cs,
        observed=f"{sum(r_ok)} of 5 rows as predicted, "
        f"--out files left: {sum(1 for c in cs if c.get('out_exists'))}",
    )


# ------------------------------------------------------------------ D3: safety


def d3_row(runs: int) -> dict[str, Any]:
    issues: list[str] = []
    # an --out run must add exactly one file to the parent of --out
    r = Rig("D3-out", i3_lines()[:9], [entry(signal="cause:timeout")], toml=FIXTURE)
    o = Path(STATE["scratch"]) / "d3-out"
    o.mkdir()
    adds = []
    for i, ident in enumerate(("imp-3e1cd7f9", "imp-7fad29b4", "imp-a8f0a344")):
        before = {p.name for p in o.iterdir()}
        res = r.call("draft", ident, "--out", str(o / f"d{i}.md"))
        now = {p.name for p in o.iterdir()}
        adds.append(sorted(now - before))
        if res["rc"] != 0 or now - before != {f"d{i}.md"} or before - now:
            issues.append(f"--out {ident}: rc {res['rc']}, added {sorted(now - before)}")
    # imports: stdlib and lattice.improve only
    allowed = {
        "argparse",
        "fnmatch",
        "hashlib",
        "json",
        "re",
        "datetime",
        "pathlib",
        "tomllib",
        "collections",
        "dataclasses",
        "typing",
        "sys",
        "__future__",
    }
    audit: dict[str, Any] = {}
    forbidden = re.compile(
        r"subprocess|socket|urllib|http|requests|httpx|anthropic|lattice\.llm|claude"
    )
    for name in ("verify.py", "draft.py"):
        src = (ROOT / "lattice/improve" / name).read_text()
        mods = []
        for node_ in ast.walk(ast.parse(src)):
            if isinstance(node_, ast.Import):
                mods += [a.name for a in node_.names]
            elif isinstance(node_, ast.ImportFrom):
                mods.append(node_.module or "")
        bad = [
            m
            for m in mods
            if m.split(".")[0] not in allowed and not m.startswith("lattice.improve")
        ]
        lines = [ln for ln in src.splitlines() if re.match(r"\s*(import|from) ", ln)]
        hits = [ln for ln in lines if forbidden.search(ln)]
        audit[name] = {"imports": sorted(set(mods)), "not_allowed": bad, "forbidden_lines": hits}
        if bad or hits:
            issues.append(f"{name}: imports {bad} {hits}")
    literals = {}
    for lit in ("71.7", "119.5", "61.01", "3e1cd7f9"):
        n = sum(p.read_text().count(lit) for p in (ROOT / "lattice/improve").glob("*.py"))
        literals[lit] = n
        if n:
            issues.append(f"literal {lit} occurs {n} times in lattice/improve/")
    # at run time: nothing LLM-shaped is loaded by the two commands
    probe = (
        "import sys, lattice.improve.__main__; "
        "print(sorted(m for m in sys.modules if m.startswith(('lattice.llm', 'anthropic'))))"
    )
    rt = subprocess.run(
        [sys.executable, "-c", probe],
        env=env_for(ROOT),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    loaded = rt.stdout.strip()
    if loaded != "[]":
        issues.append(f"LLM modules loaded at import: {loaded}")
    changed = GUARD["changed"]
    ok = not changed and not issues
    return record(
        "D3",
        "0 file changes over every V and D call (refusals included); --out adds "
        "exactly one file; imports stdlib + lattice.improve only; 0 literals",
        [],
        {
            "guard_calls": GUARD["calls"],
            "guard_changed": changed,
            "out_additions": adds,
            "import_audit": audit,
            "literal_hits": literals,
            "runtime_llm_modules": loaded,
            "issues": issues,
        },
        ok=ok,
        observed=f"{len(changed)} changes over {GUARD['calls']} guarded calls; "
        f"{len(issues)} issues; literals {literals}",
    )


# ------------------------------------------------------------------ D4: determinism


def d4_row(runs: int) -> dict[str, Any]:
    scratch = Path(STATE["scratch"])
    cwd_a, cwd_b = scratch / "cwd-a", scratch / "cwd-b"
    cwd_a.mkdir()
    cwd_b.mkdir()
    cases = {
        "V1a": (Rig("D4-v1a", ledger=[entry(CLOSE)]), ["verify", "I-01"]),
        "V2a": (Rig("D4-v2a", v2_lines(), [entry(C, signal=X)]), ["verify", "I-01"]),
        "D1b": (Rig("D4-d1b", i3_lines()[:9], toml=FIXTURE), ["draft", "imp-3e1cd7f9"]),
        "D2-checker": (Rig("D4-d2", i3_lines()[:9]), ["draft", "imp-56861ff8"]),
    }
    detail: dict[str, Any] = {}
    all_ok = True
    for name, (rig, argv) in cases.items():
        copy = rig.root / "state-copy"
        shutil.copytree(rig.state, copy)
        for mode in ("text", "json", "out"):
            outs: list[bytes] = []
            for i in range(runs):
                if i == 1:
                    time.sleep(1.1)  # the gap between runs 1 and 2
                cwd = cwd_a if i % 2 == 0 else cwd_b
                state = rig.state if i < runs // 2 else copy  # the second half: a byte copy
                extra = ["--json"] if mode == "json" else []
                outf = scratch / f"d4-{name}-{mode}-{i}.md"
                if mode == "out" and argv[0] == "draft":
                    extra = ["--out", str(outf)]
                before = tree_hash(rig.project, state)
                r = cli(
                    [*argv, *extra, "--project", str(rig.project), "--state", str(state)], cwd=cwd
                )
                GUARD["calls"] += 1
                if tree_hash(rig.project, state) != before:
                    GUARD["changed"].append({"argv": argv, "root": str(rig.root)})
                blob = (f"{r['rc']}\n{r['out']}\n{r['err']}\n").encode()
                if outf.exists():
                    blob += outf.read_bytes()
                outs.append(blob)
            differing = sum(1 for b in outs if b != outs[0])
            diff_bytes = sum(
                sum(x != y for x, y in zip(b, outs[0], strict=False)) + abs(len(b) - len(outs[0]))
                for b in outs
            )
            detail[f"{name}/{mode}"] = {
                "runs": len(outs),
                "differing_runs": differing,
                "differing_bytes": diff_bytes,
                "digest": sha(outs[0]),
            }
            all_ok = all_ok and differing == 0
    total = sum(v["differing_bytes"] for v in detail.values())
    return record(
        "D4",
        "0 differing bytes over 10 runs (1.1 s gap, two cwds, a state copy) for "
        "V1a, V2a, D1b, D2-checker x text/json/out",
        [],
        {"detail": detail},
        ok=all_ok,
        observed=f"{total} differing bytes over {len(detail)} case/mode x {runs} runs",
    )


# ------------------------------------------------------------------ D5: the old commands


def d5_row(runs: int) -> dict[str, Any]:
    scratch = Path(STATE["scratch"])
    base = scratch / "base-tree"
    base.mkdir()
    git = subprocess.run(
        ["git", "-C", str(ROOT), "archive", BASE_REF, "lattice"], capture_output=True
    )
    subprocess.run(["tar", "-x", "-C", str(base)], input=git.stdout, check=True)
    issues: list[str] = []
    cmp_rows: list[dict[str, Any]] = []
    w = scratch / "d5"
    proj, state = w / "proj", w / "state"
    proj.mkdir(parents=True)
    ir_events = (DATA / "ir-replay-events.jsonl").read_text()
    ir_ledger = (DATA / "ir-replay-improvements.jsonl").read_text()

    def reset(events: str | None, ledger: str | None = None) -> None:
        shutil.rmtree(state, ignore_errors=True)
        state.mkdir()
        if events is not None:
            (state / "events.jsonl").write_text(events)
        if ledger is not None:
            (state / "improvements.jsonl").write_text(ledger)

    def both(label: str, argv: list[str], *, committed: Path | None = None) -> None:
        full = [*argv, "--project", str(proj), "--state", str(state)]
        old = cli(full, tree=base)
        new = cli(full, tree=ROOT)
        same = old == new
        row = {
            "label": label,
            "rc": new["rc"],
            "identical_to_base": same,
            "digest": sha(new["out"] + new["err"]),
        }
        if committed is not None:
            row["equals_committed"] = new["out"] == committed.read_text()
            if not row["equals_committed"]:
                issues.append(f"{label}: differs from {committed.name}")
        if not same:
            issues.append(f"{label}: differs between {BASE_REF} and the branch")
        cmp_rows.append(row)

    nine = "".join(ln + "\n" for ln in i3_lines()[:9])
    eighteen = "".join(ln + "\n" for ln in i3_lines())
    reset(nine)
    both("report text, I-3 9 events", ["report"], committed=DATA / "i3-report-before.txt")
    both("report --json, I-3 9 events", ["report", "--json"])
    reset(eighteen)
    both("report text, I-3 18 events", ["report"])
    both("report --json, I-3 18 events", ["report", "--json"])
    reset(ir_events, ir_ledger)
    both("report text, I-R", ["report"], committed=DATA / "ir-replay-report.txt")
    both("report --json, I-R", ["report", "--json"])
    both("ledger text, I-R", ["ledger"])
    both("ledger --json, I-R", ["ledger", "--json"])
    for sub_ in ("report", "import-plan", "ledger"):
        old = cli([sub_, "--help"], tree=base)
        new = cli([sub_, "--help"], tree=ROOT)
        cmp_rows.append(
            {
                "label": f"{sub_} --help",
                "rc": new["rc"],
                "identical_to_base": old == new,
                "digest": sha(new["out"]),
            }
        )
        if old != new:
            issues.append(f"{sub_} --help differs")
    for sub_ in ("verify", "draft"):
        new = cli([sub_, "--help"])
        opts = ["--project", "--state", "--json"] + (["--at"] if sub_ == "verify" else ["--out"])
        miss = [o for o in opts if o not in new["out"]]
        cmp_rows.append({"label": f"{sub_} --help", "rc": new["rc"], "options_missing": miss})
        if new["rc"] != 0 or miss:
            issues.append(f"{sub_} --help: rc {new['rc']}, missing {miss}")
    # import-plan twice on a fresh state, per tree, same paths
    rec = str(PLANS / "p2-final.json")
    for label, tree in (("base", base), ("branch", ROOT)):
        reset(None)
        r1 = cli(
            ["import-plan", rec, "--json", "--project", str(proj), "--state", str(state)], tree=tree
        )
        r2 = cli(
            ["import-plan", rec, "--json", "--project", str(proj), "--state", str(state)], tree=tree
        )
        cmp_rows.append({"label": f"import-plan x2 ({label})", "r1": r1, "r2": r2})
    a, b = cmp_rows[-2], cmp_rows[-1]
    same_ip = a["r1"] == b["r1"] and a["r2"] == b["r2"]
    cmp_rows.append({"label": "import-plan base == branch", "identical_to_base": same_ip})
    if not same_ip:
        issues.append("import-plan output differs between the trees")
    ok = not issues
    n_same = sum(1 for r in cmp_rows if r.get("identical_to_base") is True)
    return record(
        "D5",
        "verify/draft --help rc0 listing every option; report/ledger/import-plan/"
        "--help byte-identical to 7358be2; report = committed reports",
        [],
        {"comparisons": cmp_rows, "issues": issues},
        ok=ok,
        observed=f"{n_same} byte-identical comparisons; {len(issues)} issues",
    )


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    own = args.scratch is None
    scratch = Path(tempfile.mkdtemp(prefix="improve2-")) if own else args.scratch
    scratch.mkdir(parents=True, exist_ok=True)
    STATE["scratch"] = str(scratch.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    v1 = v1_rows(args.runs)
    v2 = v2_rows(args.runs)
    v3 = v3_rows(args.runs)

    def group(pid: str, rows: list[dict[str, Any]], pred: str) -> dict[str, Any]:
        ok = all(r["pass"] for r in rows)
        return {
            "id": pid,
            "predicted": pred,
            "observed": f"{sum(r['pass'] for r in rows)} of {len(rows)} sub-rows exact",
            "pass": ok,
            "rows": rows,
        }

    top: list[dict[str, Any]] = [*v1]
    top.append(
        group(
            "V2",
            v2,
            "11 of 11 rows exact (a-j; d' is two sub-rows, h three checks)",
        )
    )
    top.append(group("V3", v3, "7 of 7 rows exact"))
    top.append(d1_rows(args.runs))
    top.append(d2_rows(args.runs))
    d3_pos = len(top)
    top.append({})  # D3 after every guarded call; D4 adds calls, so D3 is filled at the end
    top.append(d4_row(args.runs))
    top.append(d5_row(args.runs))
    top[d3_pos] = d3_row(args.runs)
    top.sort(
        key=lambda r: [
            "V1a",
            "V1b",
            "V1c",
            "V1d",
            "V1e",
            "V1f",
            "V1g",
            "V1h",
            "V2",
            "V3",
            "D1",
            "D2",
            "D3",
            "D4",
            "D5",
        ].index(r["id"])
    )
    # the V2 rows carry d'a/d'b: one row of the prereg, two sub-rows
    print("id | predicted | observed | pass")
    for r in top:
        print(f"{r['id']} | {r['predicted']} | {r['observed']} | {'pass' if r['pass'] else 'FAIL'}")
        for s in r.get("rows", []):
            print(
                f"  {s['id']} | {s['predicted']} | {s['observed']} | "
                f"{'pass' if s['pass'] else 'FAIL'}"
            )
        for c in r.get("checks", []):
            for m in c["mismatches"]:
                print(f"    ! {c['name']}: {m}")
        for s in r.get("rows", []):
            for c in s.get("checks", []):
                for m in c["mismatches"]:
                    print(f"    ! {c['name']}: {m}")
    passed = sum(1 for r in top if r["pass"])
    for f in FINDINGS:
        print(f"finding: {f}")
    print(f"probes: {passed}/{len(top)} pass")
    for r in top:
        (args.out / f"{r['id']}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    summary = {
        "runs": args.runs,
        "base_ref": BASE_REF,
        "table": [
            {
                "id": r["id"],
                "predicted": r["predicted"],
                "observed": r["observed"],
                "pass": r["pass"],
            }
            for r in top
        ],
        "sub_rows": [
            {
                "id": s["id"],
                "predicted": s["predicted"],
                "observed": s["observed"],
                "pass": s["pass"],
            }
            for r in top
            for s in r.get("rows", [])
        ],
        "probes_passed": passed,
        "probes_total": len(top),
        "guard": {"calls": GUARD["calls"], "changed": GUARD["changed"]},
        "elapsed_s": round(time.monotonic() - t0, 1),
        "findings": FINDINGS,
        "post_hoc_script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
        "decisions": DECISIONS,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    if own:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if passed == len(top) else 1


if __name__ == "__main__":
    sys.exit(main())
