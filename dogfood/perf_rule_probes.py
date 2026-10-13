"""Probes of the perf budget judging rule "a budget's constant may rise when its per-item term
falls" (``lattice.perf.budget.judge``), pre-registered in
``docs/results/2026-10-13-perf-rule-prereg.md`` (section 3: J1, J2, J4, J5; J3 is the pytest
suite, plus ``J3ast`` here: the unchanged tests compared per function with ``ast``).

The "old rule" is ``lattice/perf/budget.py`` at commit 7358be2, extracted with ``git archive``
into a scratch directory and loaded standalone as ``budget_old``; it is never reimplemented.
The new rule is the working tree's module. Every probe runs ``--runs`` times (default 10) and its
canonical JSON output must hash to the same sha256 every time. The predictions are written in
this file from the pre-registration, before the new code was run; a probe that differs is a
finding, not something to tune.

Usage: ``uv run python dogfood/perf_rule_probes.py [--runs 10] [--out DIR]`` (DIR gets one JSON
per probe and ``summary.json``). Prints ``id | predicted | observed | pass`` per probe and ends
``probes: P/T pass``; exit 0 when every probe passes. No LLM, no API key.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import uuid
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OLD_REV = "7358be2"
BUDGET_PY = "lattice/perf/budget.py"
TRADE_LINE = (
    "    trade = f.constant > budget.constant and f.per_item < budget.per_item and within\n"
)
OLD_TRADE_NOTE = (
    "every measured size is within its allowed count: the constant grew while the per-item cost "
    "fell (a trade the budget's rule counts as over budget)"
)
TOP_KEYS = ["budget_id", "endpoint", "sizes", "fit", "budget", "verdict", "delta", "notes"]
CLI_KEYS = ["version", "command", "project", "budget_file", "verdict", "reports"]
SEED = {"items": "N", "steps": [{"method": "POST", "path": "/x", "user": "ann", "count": "N",
                                 "json": {}}]}  # fmt: skip


# ---- expected texts (the pre-registration's templates, written out independently) -----------


def show(x: int | Fraction) -> str:
    """The note's number format: an integer prints as an integer, else 4 significant digits."""
    x = Fraction(x)
    return str(x.numerator) if x.denominator == 1 else f"{float(x):.4g}"


def n_star(c2, c, p2, p) -> Fraction:
    return Fraction(c2 - c) / (Fraction(p) - Fraction(p2))


def trade_note(c2, c, p2, p) -> str:
    n = show(n_star(c2, c, p2, p))
    return (
        f"constant traded for per-item: constant {show(c2)} > budget {show(c)} accepted "
        f"(per_item {show(p2)} < budget {show(p)}, every measured size within its allowed "
        f"count); at or under the budget for N >= N* = {n}, over it for N < {n}"
    )


def no_trade_note(c2, c, p2, p) -> str:
    return (
        f"no trade: per_item {show(p2)} < budget {show(p)}, but a measured size is over its "
        f"allowed count, so constant {show(c2)} > budget {show(c)} stays over budget"
    )


def better_note(c2, c, p2, p) -> list[str]:
    parts = []
    if Fraction(c2) < c:
        parts.append(f"constant {show(c2)} < {show(c)}")
    if Fraction(p2) < p:
        parts.append(f"per_item {show(p2)} < {show(p)}")
    return ["better than the budget: " + ", ".join(parts)] if parts else []


# ---- the modules --------------------------------------------------------------------------


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolves string annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def extract_old(dst: Path) -> None:
    tar = subprocess.run(["git", "archive", "--format=tar", OLD_REV, "lattice"], cwd=ROOT,
                         capture_output=True, check=True).stdout  # fmt: skip
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dst, filter="data")


class Modules:
    """old (7358be2), new (the working tree), and the two equivalent mutants PE1 / PE2."""

    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.old_root = scratch / "old"
        self.old_root.mkdir()
        extract_old(self.old_root)
        self.old = load_module("budget_old", self.old_root / BUDGET_PY)
        self.new_path = ROOT / BUDGET_PY
        self.new = load_module("budget_new", self.new_path)
        text = self.new_path.read_text(encoding="utf-8")
        if text.count(TRADE_LINE) != 1:
            raise SystemExit(f"the trade line occurs {text.count(TRADE_LINE)} times in {BUDGET_PY}")
        variants = {
            "pe1": TRADE_LINE.replace("f.per_item < budget", "f.per_item <= budget"),
            "pe2": "    trade = f.constant > budget.constant and within\n",
        }
        for name, repl in variants.items():
            path = scratch / f"budget_{name}.py"
            path.write_text(text.replace(TRADE_LINE, repl), encoding="utf-8")
            setattr(self, name, load_module(f"budget_{name}", path))


def mk_budget(mod, constant, per_item, latency=None, sizes=(5, 25)):
    queries = {"constant": constant, "per_item": per_item}
    data = {"version": 1, "budgets": [{"id": "b", "endpoint": "GET /x", "user": "ann",
                                       "seed": SEED, "sizes": list(sizes), "queries": queries,
                                       "latency_ms": latency}]}  # fmt: skip
    return mod.parse(data).budgets[0]


def mk_sizes(mod, counts: dict[int, list[int]], lat: list[float] | None = None):
    return [mod.SizeResult(n, list(c), list(lat or [1.0] * len(c))) for n, c in counts.items()]


def judge_both(mods: Modules, c, p, counts, latency=None, lat=None, which=("old", "new")):
    """``{name: Report.to_dict()}`` of the same inputs under each module in ``which``."""
    out = {}
    for name in which:
        mod = getattr(mods, name)
        r = mod.judge(mk_budget(mod, c, p, latency), mk_sizes(mod, counts, lat))
        out[name] = r.to_dict()
    return out


def flat(n5: int, n25: int, runs: int = 10) -> dict[int, list[int]]:
    return {5: [n5] * runs, 25: [n25] * runs}


def key_paths(obj: Any, prefix: str = "") -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(f"{prefix}.{k}")
            out |= key_paths(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for v in obj:
            out |= key_paths(v, f"{prefix}[]")
    return out


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def matches(pred: Any, obs: Any) -> bool:
    """``pred`` is a structural subset of ``obs`` (dict keys), lists and scalars compare equal."""
    if isinstance(pred, dict):
        return isinstance(obs, dict) and all(
            k in obs and matches(v, obs[k]) for k, v in pred.items()
        )
    return pred == obs


def delta(c, p):
    return {"constant": c, "per_item": p}


# ---- J1: the recorded spike-1a numbers ---------------------------------------------------

# id, budget (constant, per_item), counts at N=5 / N=25, recorded old verdict, new notes, delta
J1_CASES: list[tuple[str, tuple[int, int], tuple[int, int], str, str, list[str], dict]] = [
    ("J1a", (6, 1), (7, 7), "fail", "pass",
     [trade_note(7, 6, 0, 1), "better than the budget: per_item 0 < 1"], delta(1, -1)),
    ("J1b", (6, 1), (11, 31), "pass", "pass", [], delta(0, 0)),
    ("J1c", (8, 0), (7, 7), "pass", "pass",
     ["better than the budget: constant 7 < 8"], delta(-1, 0)),
    ("J1d", (8, 0), (11, 31), "fail", "fail",
     ["over budget: per_item 1 > budget 0", "over budget: N=5: 11 statements > allowed 8",
      "over budget: N=25: 31 statements > allowed 8",
      "better than the budget: constant 6 < 8"], delta(-2, 1)),
    ("J1e", (4, 0), (9, 29), "fail", "fail",
     ["over budget: per_item 1 > budget 0", "over budget: N=5: 9 statements > allowed 4",
      "over budget: N=25: 29 statements > allowed 4"], delta(0, 1)),
    ("J1f", (4, 0), (4, 4), "pass", "pass", [], delta(0, 0)),
]  # fmt: skip
J1_OLD_NOTES_A = ["over budget: constant 7 > budget 6", OLD_TRADE_NOTE,
                  "better than the budget: per_item 0 < 1"]  # fmt: skip


def j1_case(mods: Modules, case) -> dict:
    cid, (c, p), (n5, n25), _old_v, _new_v, _notes, _delta = case
    both = judge_both(mods, c, p, flat(n5, n25))
    old, new = both["old"], both["new"]
    return {
        "old": {"verdict": old["verdict"], "notes": old["notes"]},
        "new": {"verdict": new["verdict"], "notes": new["notes"], "delta": new["delta"]},
        "to_dict_identical": canon(old) == canon(new),
        "summary": f"{cid}: old {old['verdict']} -> new {new['verdict']}; "
        f"{len(new['notes'])} notes; delta {new['delta']}",
    }


def j1_probes() -> list[tuple[str, dict, Callable]]:
    out = []
    for case in J1_CASES:
        cid, _b, _n, old_v, new_v, notes, dlt = case
        old_notes = J1_OLD_NOTES_A if cid == "J1a" else notes
        pred = {
            "old": {"verdict": old_v, "notes": old_notes},
            "new": {"verdict": new_v, "notes": notes, "delta": dlt},
            "to_dict_identical": cid != "J1a",
            "text": f"old {old_v} -> new {new_v}; {len(notes)} notes; delta {dlt}"
            + ("; to_dict old==new" if cid != "J1a" else "; old notes = recorded three"),
        }
        out.append((cid, pred, lambda m, ctx, case=case: j1_case(m, case)))

    def agg(m: Modules, ctx: dict) -> dict:
        rows = [j1_case(m, c) for c in J1_CASES]
        differ = [c[0] for c, r in zip(J1_CASES, rows, strict=True)
                  if r["old"]["verdict"] != r["new"]["verdict"]]  # fmt: skip
        as_recorded = sum(r["old"]["verdict"] == c[3] for c, r in zip(J1_CASES, rows, strict=True))
        return {
            "old_verdicts_as_recorded": as_recorded,
            "new_verdicts_and_notes_as_table": sum(
                r["new"]["verdict"] == c[4] and r["new"]["notes"] == c[5]
                for c, r in zip(J1_CASES, rows, strict=True)),
            "verdicts_differing": differ,
            "to_dict_identical_b_to_f": sum(r["to_dict_identical"] for r in rows[1:]),
            "summary": f"old as recorded {as_recorded}/6, differing {differ}",
        }  # fmt: skip

    out.append(("J1", {"old_verdicts_as_recorded": 6, "new_verdicts_and_notes_as_table": 6,
                       "verdicts_differing": ["J1a"], "to_dict_identical_b_to_f": 5,
                       "text": "old 6/6 as recorded, new 6/6 as table, only J1a differs, "
                       "b-f byte-identical 5/5"}, agg))  # fmt: skip
    return out


# ---- J2: the exhaustive grid --------------------------------------------------------------


def grid(mods: Modules) -> list[dict]:
    budgets = {name: {(c, p): mk_budget(getattr(mods, name), c, p)
                      for c in range(9) for p in range(5)}
               for name in ("old", "new", "pe1", "pe2")}  # fmt: skip
    cells = []
    for c in range(9):
        for p in range(5):
            for c2 in range(13):
                for p2 in range(6):
                    counts = {5: [c2 + p2 * 5] * 10, 25: [c2 + p2 * 25] * 10}
                    d = {}
                    for name in ("old", "new", "pe1", "pe2"):
                        mod = getattr(mods, name)
                        d[name] = mod.judge(budgets[name][(c, p)], mk_sizes(mod, counts)).to_dict()
                    cells.append({"c": c, "p": p, "c2": c2, "p2": p2, **d})
    return cells


def _grid(mods: Modules, ctx: dict) -> list[dict]:
    if "grid" not in ctx:
        ctx["grid"] = grid(mods)
    return ctx["grid"]


def j2a(mods, ctx):
    cells = _grid(mods, ctx)
    n = sum(x["old"]["verdict"] == "pass" and x["new"]["verdict"] != "pass" for x in cells)
    return {"cells": len(cells), "old_pass_new_fail": n, "summary": f"{n} cells of {len(cells)}"}


def j2b(mods, ctx):
    cells = _grid(mods, ctx)
    got = {(x["c"], x["p"], x["c2"], x["p2"]) for x in cells
           if x["old"]["verdict"] != "pass" and x["new"]["verdict"] == "pass"}  # fmt: skip
    want = {(x["c"], x["p"], x["c2"], x["p2"]) for x in cells
            if x["c2"] > x["c"] and x["p2"] < x["p"]
            and x["c2"] + 5 * x["p2"] <= x["c"] + 5 * x["p"]
            and x["c2"] + 25 * x["p2"] <= x["c"] + 25 * x["p"]}  # fmt: skip
    return {"set_equal": got == want, "newly_passing": len(got), "formula": len(want),
            "summary": f"set_equal {got == want}, {len(got)} cells"}  # fmt: skip


def j2c(mods, ctx):
    cells = _grid(mods, ctx)
    old_pass = sum(x["old"]["verdict"] == "pass" for x in cells)
    new_pass = sum(x["new"]["verdict"] == "pass" for x in cells)
    new_only = sum(x["old"]["verdict"] != "pass" and x["new"]["verdict"] == "pass" for x in cells)
    cand = [x for x in cells if x["c2"] > x["c"] and x["p2"] < x["p"]]
    no_trade = [x for x in cand if x["new"]["verdict"] != "pass"]
    only5 = sum(
        any(n.startswith("over budget: N=5:") for n in x["new"]["notes"])
        and not any(n.startswith("over budget: N=25:") for n in x["new"]["notes"])
        for x in no_trade
    )
    return {"old_pass": old_pass, "new_only": new_only, "new_pass": new_pass,
            "old_fail": len(cells) - old_pass, "new_fail": len(cells) - new_pass,
            "trade_candidates": len(cand), "no_trade": len(no_trade), "no_trade_over_at_n5": only5,
            "summary": f"old {old_pass}, new-only {new_only}, new {new_pass}, "
            f"no-trade {len(no_trade)}"}  # fmt: skip


def j2d(mods, ctx):
    cells = _grid(mods, ctx)
    identical = verdict_notes = notes_only = other = 0
    old_note_old = old_note_new = trade_exact = no_trade_exact = star_ok = 0
    star_max = Fraction(0)
    for x in cells:
        o, n = x["old"], x["new"]
        c, p, c2, p2 = x["c"], x["p"], x["c2"], x["p2"]
        rest = [k for k in o if k not in ("verdict", "notes") and o[k] != n[k]]
        if o == n:
            identical += 1
        elif rest:
            other += 1
        elif o["verdict"] != n["verdict"]:
            verdict_notes += 1
        else:
            notes_only += 1
        old_note_old += OLD_TRADE_NOTE in o["notes"]
        old_note_new += OLD_TRADE_NOTE in n["notes"]
        if o["verdict"] != "pass" and n["verdict"] == "pass":
            star = n_star(c2, c, p2, p)
            want = [trade_note(c2, c, p2, p), *better_note(c2, c, p2, p)]
            trade_exact += n["notes"] == want
            star_ok += 0 < star <= 5
            star_max = max(star_max, star)
        elif c2 > c and p2 < p and n["verdict"] != "pass":
            sizes_over = [f"over budget: N={s}: {c2 + p2 * s} statements > allowed {c + p * s}"
                          for s in (5, 25) if c2 + p2 * s > c + p * s]  # fmt: skip
            want = ([f"over budget: constant {c2} > budget {c}", *sizes_over,
                     no_trade_note(c2, c, p2, p), *better_note(c2, c, p2, p)])  # fmt: skip
            no_trade_exact += n["notes"] == want and o["notes"] == [
                w for w in want if not w.startswith("no trade")
            ]
    return {"identical": identical, "verdict_and_notes_differ": verdict_notes,
            "notes_only_differ": notes_only, "other_field_differs": other,
            "old_trade_note_under_old": old_note_old, "old_trade_note_under_new": old_note_new,
            "trade_notes_exact": trade_exact, "nstar_in_0_to_5": star_ok,
            "nstar_max": show(star_max), "no_trade_notes_exact": no_trade_exact,
            "summary": f"identical {identical}, verdict+notes {verdict_notes}, notes-only "
            f"{notes_only}, other {other}, old note old/new {old_note_old}/{old_note_new}, "
            f"N* ok {star_ok} max {show(star_max)}"}  # fmt: skip


def j2e(mods, ctx):
    cells = _grid(mods, ctx)
    p1 = sum(x["pe1"] == x["new"] for x in cells)
    p2 = sum(x["pe2"] == x["new"] for x in cells)
    return {"pe1_identical": p1, "pe2_identical": p2, "cells": len(cells),
            "summary": f"PE1 {p1}/{len(cells)}, PE2 {p2}/{len(cells)}"}  # fmt: skip


J2_PROBES = [
    ("J2a", {"cells": 3510, "old_pass_new_fail": 0, "text": "0 cells of 3510"}, j2a),
    ("J2b", {"set_equal": True, "newly_passing": 599, "formula": 599,
             "text": "set_equal True, 599 cells"}, j2b),  # fmt: skip
    ("J2c", {"old_pass": 675, "new_only": 599, "new_pass": 1274, "old_fail": 2835,
             "new_fail": 2236, "trade_candidates": 720, "no_trade": 121, "no_trade_over_at_n5": 121,
             "text": "old 675, new-only 599, new 1274, no-trade 121 (over at N=5 only)"}, j2c),
    ("J2d", {"identical": 2790, "verdict_and_notes_differ": 599, "notes_only_differ": 121,
             "other_field_differs": 0, "old_trade_note_under_old": 599,
             "old_trade_note_under_new": 0, "trade_notes_exact": 599, "nstar_in_0_to_5": 599,
             "nstar_max": "5", "no_trade_notes_exact": 121,
             "text": "identical 2790, verdict+notes 599, notes-only 121, old note 599/0, "
             "599 N* in (0,5] max 5"}, j2d),  # fmt: skip
    ("J2e", {"pe1_identical": 3510, "pe2_identical": 3510, "text": "PE1 3510/3510, PE2 3510/3510"},
     j2e),
]  # fmt: skip


# ---- J3ast: the unchanged tests, compared per function -----------------------------------

J3_REWRITTEN_OLD = "test_trading_constant_for_per_item_fails_on_the_constant_and_says_so"
J3_REWRITTEN_NEW = "test_trading_constant_for_per_item_passes_and_shows_the_crossover"
J3_NEW = sorted([
    J3_REWRITTEN_NEW,
    "test_no_trade_when_a_size_is_over_its_allowed_count",
    "test_constant_over_with_equal_per_item_still_fails",
    "test_reverse_trade_is_refused",
    "test_trade_shows_a_fractional_crossover",
    "test_trade_at_the_boundary_size_passes",
    "test_trade_does_not_excuse_latency",
    "test_falling_count_trades_against_a_zero_per_item_budget",
])  # fmt: skip


def _functions(source: str) -> dict[str, str]:
    return {n.name: ast.dump(n) for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef)}  # fmt: skip


def j3ast(mods, ctx):
    old_src = subprocess.run(["git", "show", f"{OLD_REV}:tests/perf/test_budget.py"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout  # fmt: skip
    new_src = (ROOT / "tests/perf/test_budget.py").read_text(encoding="utf-8")
    old, new = _functions(old_src), _functions(new_src)
    changed = sorted(n for n in old if n in new and old[n] != new[n])
    removed = sorted(n for n in old if n not in new)
    added = sorted(n for n in new if n not in old)
    unchanged = sum(1 for n in old if n in new and old[n] == new[n])
    return {"changed": changed, "removed": removed, "added": added,
            "summary": f"changed {changed}, removed {removed}, added {len(added)}, "
            f"unchanged functions {unchanged}"}  # fmt: skip


# ---- J4: schema and exit codes ------------------------------------------------------------


def j4i(mods, ctx):
    cases = {
        "pass (J1b)": dict(c=6, p=1, counts=flat(11, 31)),
        "fail (J1d)": dict(c=8, p=0, counts=flat(11, 31)),
        "unmeasured": dict(c=6, p=1, counts={5: [11] * 10, 25: []}),
        "trade (J1a)": dict(c=6, p=1, counts=flat(7, 7)),
    }
    rows = {}
    for name, kw in cases.items():
        both = judge_both(mods, kw["c"], kw["p"], kw["counts"])
        old, new = both["old"], both["new"]
        rows[name] = {
            "verdict": new["verdict"],
            "keys_new": list(new),
            "keys_old": list(old),
            "paths_equal": key_paths(old) == key_paths(new),
            "ok": list(new) == TOP_KEYS and list(old) == TOP_KEYS
            and key_paths(old) == key_paths(new),
        }  # fmt: skip
    n_ok = sum(r["ok"] for r in rows.values())
    return {"cases": rows, "n_ok": n_ok, "verdicts": {k: r["verdict"] for k, r in rows.items()},
            "summary": f"{n_ok}/4 same keys and key paths old vs new"}  # fmt: skip


def _cli(pythonpath: Path, project: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(pythonpath)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run(
        [sys.executable, "-m", "lattice.perf", "check", "--project", str(project), "--json",
         "--runs", "2"],
        cwd=str(pythonpath), env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"exit": r.returncode, "error": (r.stdout + r.stderr)[-800:]}
    reports = data.get("reports", [])
    for rep in reports:  # measured latencies are not deterministic
        for s in rep.get("sizes", []):
            s.pop("latency_ms", None)
    return {"exit": r.returncode, "top_keys": list(data), "verdict": data.get("verdict"),
            "reports": reports}  # fmt: skip


def j4ii(mods, ctx):
    with tempfile.TemporaryDirectory(prefix="lattice-perfrule-j4-") as d:
        proj = Path(d) / "proj"
        proj.mkdir()
        mod = f"perfapp_{uuid.uuid4().hex[:8]}"
        (proj / f"{mod}.py").write_text((ROOT / "tests/perf/perfapp.py").read_text())
        (proj / "studio.toml").write_text(
            f'[app]\napp = "{mod}:make_app"\ndb = "{mod}:build_db"\nlogin = "{mod}:login"\n'
        )
        budget = {"id": "b", "endpoint": "GET /items", "user": "ann",
                  "seed": {"items": "N", "steps": [{"method": "POST", "path": "/items",
                                                    "user": "ann", "count": "N",
                                                    "json": {"name": "item-{i}"}}]},
                  "sizes": [2, 6], "queries": {"constant": 1, "per_item": 1},
                  "latency_ms": None}  # fmt: skip
        (proj / "perf.budget.json").write_text(json.dumps({"version": 1, "budgets": [budget]}))
        new = _cli(ROOT, proj)
        old = _cli(mods.old_root, proj)
    new_notes = (new.get("reports") or [{}])[0].get("notes")
    old_notes = (old.get("reports") or [{}])[0].get("notes")
    return {
        "new": {"exit": new["exit"], "verdict": new.get("verdict"), "top_keys": new.get("top_keys"),
                "notes": new_notes, "fit": (new.get("reports") or [{}])[0].get("fit"),
                "trade_note": trade_note(2, 1, 0, 1) in (new_notes or []),
                "crossover_shown": any("N* = 1," in n for n in new_notes or [])},
        "old": {"exit": old["exit"], "verdict": old.get("verdict"), "top_keys": old.get("top_keys"),
                "notes": old_notes},
        "summary": f"new exit {new['exit']} {new.get('verdict')}, old exit {old['exit']} "
        f"{old.get('verdict')}, keys equal {new.get('top_keys') == old.get('top_keys')}",
    }  # fmt: skip


# ---- J5: neighbouring behaviour ----------------------------------------------------------

SLOW = [100.0, 120.0, 110.0]
LAT = {"median": 50, "size": 25, "margin": 0.5}


def _first(notes: list[str], prefix: str) -> bool:
    return any(n.startswith(prefix) for n in notes)


def j5_case(mods, c, p, counts, latency=None, lat=None):
    both = judge_both(mods, c, p, counts, latency, lat)
    return both["old"], both["new"]


def j5a(mods, ctx):
    o, n = j5_case(mods, 4, 0, {5: [4] * 10, 10: [12] * 10, 25: [4] * 10})
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"N=10 over": "over budget: N=10: 12 statements > allowed 4" in n["notes"],
                       "not exact": n["fit"]["exact"] is False,
                       "to_dict identical": canon(o) == canon(n)},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def j5b(mods, ctx):
    o, n = j5_case(mods, 6, 1, {5: [7] * 10, 15: [30] * 10, 25: [7] * 10})
    want = list(o["notes"])
    at = next((i for i, x in enumerate(want) if x.startswith("better than the budget")), len(want))
    want.insert(at, no_trade_note(7, 6, 0, 1))
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"N=15 over": "over budget: N=15: 30 statements > allowed 21" in n["notes"],
                       "not exact": n["fit"]["exact"] is False,
                       "no trade note": no_trade_note(7, 6, 0, 1) in n["notes"],
                       "new notes = old notes + no trade": n["notes"] == want,
                       "no trade accepted note": not _first(n["notes"], "constant traded")},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def j5c(mods, ctx):
    slow = {5: [11] * 3, 25: [31] * 3}
    rows = []
    for latency in (None, LAT, {"median": 80, "size": 25, "margin": 0.5}):
        o, n = j5_case(mods, 6, 1, slow, latency, SLOW)
        rows.append({"old": o["verdict"], "new": n["verdict"], "identical": canon(o) == canon(n)})
    return {
        "old": [r["old"] for r in rows],
        "new": [r["new"] for r in rows],
        "checks": {"to_dict identical": all(r["identical"] for r in rows)},
        "summary": f"old {[r['old'] for r in rows]} new {[r['new'] for r in rows]}",
    }


def j5d(mods, ctx):
    o, n = j5_case(mods, 6, 1, {5: [7] * 3, 25: [7] * 3}, LAT, SLOW)
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"latency fails alone": _first(n["notes"], "over budget: latency: median "
                                                                 "110.00 ms")
                       and len([x for x in n["notes"] if x.startswith("over budget")]) == 1,
                       "no constant line": not _first(n["notes"], "over budget: constant"),
                       "trade note": trade_note(7, 6, 0, 1) in n["notes"],
                       "old had the constant line": "over budget: constant 7 > budget 6"
                       in o["notes"]},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def j5e(mods, ctx):
    o, n = j5_case(mods, 4, 0, flat(9, 29))
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"per_item over": "over budget: per_item 1 > budget 0" in n["notes"],
                       "to_dict identical": canon(o) == canon(n)},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def j5f(mods, ctx):
    o, n = j5_case(mods, 30, 0, flat(5, 25))
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"per_item over": "over budget: per_item 1 > budget 0" in n["notes"],
                       "reverse trade refused": not _first(n["notes"], "constant traded"),
                       "to_dict identical": canon(o) == canon(n)},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def j5g(mods, ctx):
    o, n = j5_case(mods, 6, 1, flat(12, 32))
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"constant over": "over budget: constant 7 > budget 6" in n["notes"],
                       "to_dict identical": canon(o) == canon(n)},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def j5h(mods, ctx):
    o, n = j5_case(mods, 8, 0, flat(8, 4))
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"per_item -0.2": n["fit"]["per_item"] == -0.2,
                       "trade note N* = 5": trade_note(9, 8, Fraction(-1, 5), 0) in n["notes"]},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def j5i(mods, ctx):
    o, n = j5_case(mods, 6, 1, {5: [7, 7, 8], 25: [7, 8, 8]})
    return {"old": o["verdict"], "new": n["verdict"],
            "checks": {"fit 8 + 0N": (n["fit"]["constant"], n["fit"]["per_item"]) == (8, 0),
                       "fit not exact first": n["notes"][:1] != []
                       and n["notes"][0].startswith("fit not exact"),
                       "trade note N* = 2": n["notes"][1:2] == [trade_note(8, 6, 0, 1)]},
            "notes": n["notes"], "summary": f"{o['verdict']} -> {n['verdict']}"}  # fmt: skip


def _j5(fn, old, new, checks, text=None):
    return (
        {
            "old": old,
            "new": new,
            "checks": {k: True for k in checks},
            "text": text or f"{old} -> {new}; " + ", ".join(checks),
        },
        fn,
    )


J5_PROBES = [
    ("J5a", *_j5(j5a, "fail", "fail", ["N=10 over", "not exact", "to_dict identical"])),
    ("J5b", *_j5(j5b, "fail", "fail", ["N=15 over", "not exact", "no trade note",
                                       "new notes = old notes + no trade",
                                       "no trade accepted note"])),
    ("J5c", {"old": ["pass", "fail", "pass"], "new": ["pass", "fail", "pass"],
             "checks": {"to_dict identical": True},
             "text": "pass, fail, pass (same); to_dict identical"}, j5c),
    ("J5d", *_j5(j5d, "fail", "fail", ["latency fails alone", "no constant line", "trade note",
                                       "old had the constant line"])),
    ("J5e", *_j5(j5e, "fail", "fail", ["per_item over", "to_dict identical"])),
    ("J5f", *_j5(j5f, "fail", "fail", ["per_item over", "reverse trade refused",
                                       "to_dict identical"])),
    ("J5g", *_j5(j5g, "fail", "fail", ["constant over", "to_dict identical"])),
    ("J5h", *_j5(j5h, "fail", "pass", ["per_item -0.2", "trade note N* = 5"])),
    ("J5i", *_j5(j5i, "fail", "pass", ["fit 8 + 0N", "fit not exact first",
                                       "trade note N* = 2"])),
]  # fmt: skip


PROBES: list[tuple[str, dict, Callable]] = [
    *j1_probes(),
    *J2_PROBES,
    ("J3ast", {"changed": [], "removed": [J3_REWRITTEN_OLD], "added": J3_NEW,
               "text": f"0 changed, 1 removed (the rewritten), {len(J3_NEW)} added"}, j3ast),
    ("J4i", {"n_ok": 4, "verdicts": {"pass (J1b)": "pass", "fail (J1d)": "fail",
                                     "unmeasured": "unmeasured", "trade (J1a)": "pass"},
             "text": "4/4 same keys and key paths old vs new"}, j4i),
    ("J4ii", {"new": {"exit": 0, "verdict": "pass", "top_keys": CLI_KEYS,
                      "trade_note": True, "crossover_shown": True,
                      "fit": {"constant": 2, "per_item": 0, "exact": True}},
              "old": {"exit": 1, "verdict": "fail", "top_keys": CLI_KEYS},
              "text": "new exit 0 pass with trade note N* = 1, old exit 1, same top-level keys"},
     j4ii),
    *J5_PROBES,
]  # fmt: skip


# ---- post-hoc observations (not probes; reported, not counted) -----------------------------


def post_hoc(mods: Modules) -> dict:
    """Decision 2 says old == new for every c' <= c; decision 6 deletes the old trade note. They
    differ in one corner: c' <= c, p' < p, every size within, and a latency failure (the old note
    was appended then, although the constant had not grown)."""
    both = judge_both(mods, 6, 1, {5: [6] * 3, 25: [6] * 3}, LAT, SLOW)
    return {"c_le_c_p_lt_p_within_latency_fail": {
        "old_notes": both["old"]["notes"], "new_notes": both["new"]["notes"],
        "old_verdict": both["old"]["verdict"], "new_verdict": both["new"]["verdict"],
        "identical": canon(both["old"]) == canon(both["new"]),
    }}  # fmt: skip


# ---- the runner ----------------------------------------------------------------------------


def run_all(runs: int, out: Path | None) -> int:
    with tempfile.TemporaryDirectory(prefix="lattice-perfrule-probes-") as d:
        mods = Modules(Path(d))
        results: dict[str, dict] = {}
        hashes: dict[str, list[str]] = {pid: [] for pid, _p, _f in PROBES}
        first: dict[str, dict] = {}
        for _ in range(runs):
            ctx: dict = {}
            for pid, _pred, fn in PROBES:
                obs = fn(mods, ctx)
                hashes[pid].append(hashlib.sha256(canon(obs).encode()).hexdigest())
                first.setdefault(pid, obs)
        observed_extra = post_hoc(mods)
    rows = []
    for pid, pred, _fn in PROBES:
        obs = first[pid]
        identical = len(set(hashes[pid])) == 1
        want = {k: v for k, v in pred.items() if k != "text"}
        ok = matches(want, obs) and identical
        results[pid] = {"id": pid, "prediction": pred, "observed": obs, "pass": ok,
                        "runs": runs, "identical": identical, "sha256": hashes[pid]}  # fmt: skip
        rows.append((pid, pred["text"], obs.get("summary", ""), ok))
    print("id | predicted | observed | pass")
    for pid, text, summary, ok in rows:
        print(f"{pid} | {text} | {summary} | {'PASS' if ok else 'FAIL'}")
    print(f"sha256 of each probe's canonical JSON ({runs} runs):")
    for pid in hashes:
        hs = set(hashes[pid])
        print(f"  {pid}: {hashes[pid][0]} x{runs} identical" if len(hs) == 1
              else f"  {pid}: DIFFERENT {sorted(hs)}")  # fmt: skip
    passed = sum(r["pass"] for r in results.values())
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        for pid, res in results.items():
            (out / f"{pid}.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        summary = {"old_rev": OLD_REV, "runs": runs, "passed": passed, "total": len(results),
                   "probes": {pid: {"pass": r["pass"], "identical": r["identical"],
                                    "predicted": r["prediction"]["text"],
                                    "observed": r["observed"].get("summary", "")}
                              for pid, r in results.items()},
                   "post_hoc_observations": observed_extra}  # fmt: skip
        (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"probes: {passed}/{len(results)} pass")
    return 0 if passed == len(results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=10, help="repetitions of every probe")
    ap.add_argument("--out", type=Path, default=None, help="write the raw JSON here")
    args = ap.parse_args(argv)
    return run_all(max(1, args.runs), args.out)


if __name__ == "__main__":
    sys.exit(main())
