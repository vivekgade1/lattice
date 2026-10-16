"""Probes of the implementation diff, round 2 (``workflow_find`` ranking): section 3 and Amendment 1
of ``docs/results/2026-10-16-implementation-diff-round2-prereg.md``.

Round 1's runner (``implementation_probes.py``, frozen) builds the world; this one adds the
round-2 probes on top of it:

* PT2A (gated): the top-ranked route is the key route on >= 75% of the single-target rows of G
* PT2B (reported, not gated, predicted to miss): exact label + route on >= 75% of G
* PTDEV: round 1's PT (hint; >= 6/8, round 1's bar) and the 13 round-1 agent queries (report only)
* PTF: the burned set F (report only; a mutant catcher)
* PT3: the orchestrator's held-out queries (report only; never tuned on)
* PD2, PX2, PB2 (byte-compat against bb3ff0c), PR1 (round 1's runner, unchanged, at the new code)

The query files are hash-checked against the pre-registration before anything runs. Run::

    "$DRV" dogfood/implementation_probes2.py --workflows-dyn "$WF" --scratch "$SCRATCH/p2" \\
        --out "$SCRATCH/p2-data"
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import implementation_probes as P1  # noqa: E402

REPO = P1.REPO
BASE2 = "bb3ff0c"  # round 2's byte-compat reference: the pre-registration's parent
R1_DATA = "docs/results/data/2026-10-15-implementation-diff"
R1_FROZEN = (
    "dogfood/implementation_agent.py",
    "dogfood/implementation_agent_scorer.py",
    "dogfood/implementation_agent_check.py",
    "dogfood/implementation_agent_scorer_cases.json",
    "dogfood/contracts/agent_scorer.py",
    "dogfood/implementation_probes.py",
    "dogfood/implementation_mutants.py",
    "lattice/mcp/tools.py",
    "tests/mcp/test_implementation_tools.py",
)
HASHES = {  # section 3 and Amendment 1: the sha256 of the query files, as pre-registered
    "G": "64490f9aa62b05abd271b357df41b352f44376367e53c1b403e9476417641550",
    "F_burned": "2b3f1f5b686210cad25611000f4de48b9314a24005837bc49c9b726333397f8b",
    "agent2_questions": "bf06b9e92defaa7ed38365f056492e400651ba4bd0c359100f79e37e4a21a694",
    "pt3_file": "925d73f549af80020319454d0927561cfa3e9bccb7ed49f6743ca91dcdb83e6a",
}
# round 1's frozen report JSON hashes (section 1: S-dyn 84624894..., S4-stat 80511cbb...)
R1_REPORT_SHA = json.loads((HERE / "implementation_agent_scorer_cases.json").read_text())[
    "report_sha256"
]
assert R1_REPORT_SHA["S-dyn"].startswith("84624894")
assert R1_REPORT_SHA["S4-stat"].startswith("80511cbb")
WORLD_OF = {"S": "S-dyn", "S4": "S4-stat"}
PROBE_ORDER = ("PT2", "PTDEV", "PTF", "PT3", "PD2", "PX2", "PB2", "PR1")

# the 13 round-1 agent queries: (round-1 question id, world, label, target(s))
# labels from round 1's keys: implemented -> exists, REQUIRED-NOT-IMPLEMENTED -> spec_only,
# NOT-SPECIFIED-NOT-IMPLEMENTED -> no_match, AMBIGUOUS -> several; targets are the referents
DEV_AGENT = {
    "Q1": ("S-dyn", "exists", "impl:route:POST /v1/habits/{}/completions"),
    "Q2": ("S-dyn", "exists", "impl:route:DELETE /v1/devices/{}"),
    "Q3": ("S-dyn", "exists", "impl:route:POST /v1/completions/batch"),
    "Q4": ("S-dyn", "exists", "impl:machine:nudges.delivery"),
    "Q5": ("S-dyn", "no_match", None),
    "Q6": ("S-dyn", "no_match", None),
    "Q7": ("S-dyn", "several", None),
    "Q8": ("S-dyn", "several", None),
    "Q9": ("S4-stat", "spec_only", "impl:route:GET /v1/home"),
    "Q10": ("S4-stat", "spec_only", "impl:route:GET /v1/sync"),
    "Q11": ("S4-stat", "spec_only", "impl:route:POST /v1/devices"),
    "Q12": ("S4-stat", "exists", "impl:route:POST /v1/habits/{}/completions"),
    "Q13": ("S4-stat", "no_match", None),
}
result, not_run, sha = P1.result, P1.not_run, P1.sha


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify_query_files(queries: Path, data: Path) -> dict[str, Any]:
    """The sha256 of every query file against the document, before any run (Amendment 1)."""
    q = json.loads(queries.read_text())
    got = {
        "G": sha(canon(q["G"]).encode()),
        "F_burned": sha(canon(q["F_burned"]).encode()),
    }
    for name, fname in (
        ("agent2_questions", "agent2_questions.json"),
        ("pt3_file", "pt3_queries.json"),
    ):
        got[name] = sha((data / fname).read_bytes())
    aq = json.loads((data / "agent2_questions.json").read_text())
    got["agent2_questions"] = sha(
        canon({"questions": aq["questions"], "ablation": aq["ablation"]}).encode()
    )
    # the file's rows are the canonical files' rows (the canonical files are copied to the data dir)
    same = {
        "G": q["G"] == json.loads((data / "pt2_queries.json").read_text()),
        "F_burned": q["F_burned"] == json.loads((data / "f_burned_queries.json").read_text()),
    }
    bad = sorted(k for k in HASHES if got.get(k) != HASHES[k])
    if bad or not all(same.values()):
        raise SystemExit(f"refused: query file hash mismatch {bad} / differing copies {same}")
    return {"verified": got, "rows_equal_canonical_files": same}


# ---- the queries --------------------------------------------------------------------------------


class Finder:
    """workflow_find through the MCP toolbox of each world's clone (the tool the agent calls)."""

    def __init__(self, w: P1.World) -> None:
        self.boxes = {
            "S-dyn": P1.toolbox(w.s),
            "S4-stat": P1.toolbox(w.s4),
            "S-plus": P1.toolbox(w.splus),
        }

    def __call__(self, world: str, question: str) -> dict[str, Any]:
        return self.boxes[world].call("workflow_find", {"question": question})


def judge(row: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    """G / F rows (section 3): a row is right iff the hint equals the label and, for
    exists/spec_only, the first candidate is the target; for several, the first two candidates
    are both in the key set; no_match needs nothing more."""
    tg = [c["target"] for c in out["candidates"]]
    label = row["label"]
    if label in ("exists", "spec_only"):
        exact = out["hint"] == label and bool(tg) and tg[0] == row["target"]
    elif label == "several":
        exact = out["hint"] == "several" and len(tg) >= 2 and set(tg[:2]) <= set(row["key_set"])
    else:
        exact = out["hint"] == "no_match"
    single = "target" in row
    return {
        "id": row["id"],
        "world": row["world"],
        "query": row["query"],
        "expected": [label, row.get("target") or row.get("key_set")],
        "got": [out["hint"], tg[:3]],
        "coverage": out["coverage"],
        "exact": exact,
        "hint_ok": out["hint"] == label,
        "top1": (bool(tg) and tg[0] == row["target"]) if single else None,
        "top3": (row["target"] in tg[:3]) if single else None,
    }


def run_set(find: Finder, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [judge(r, find(r["world"], r["query"])) for r in rows]


def tally(rows: list[dict[str, Any]]) -> dict[str, Any]:
    single = [r for r in rows if r["top1"] is not None]
    return {
        "exact": f"{sum(r['exact'] for r in rows)}/{len(rows)}",
        "hint": f"{sum(r['hint_ok'] for r in rows)}/{len(rows)}",
        "target@1": f"{sum(bool(r['top1']) for r in single)}/{len(single)}",
        "target@3": f"{sum(bool(r['top3']) for r in single)}/{len(single)}",
    }


# ---- a child that runs queries in another process (PD2, the round-1 ranker) ---------------------

QSCRIPT = r"""
import json, sys
from pathlib import Path
from lattice.spec.draft import load_spec
from lattice.spec.rules import Trace
from lattice.spec.implementation import ImplContext, find_candidates, load_inventory
cfg = json.load(sys.stdin)
ctxs = {}
for name, (clone, inv, root) in cfg["worlds"].items():
    spec, errs = load_spec((Path(clone) / "spec.py").read_text())
    tr = Trace.from_json((Path(clone) / "spec.trace.json").read_text())
    ctxs[name] = ImplContext.of(spec, tr, load_inventory(Path(inv), Path(root)))
out = [find_candidates(ctxs[w], q, 5) for w, q in cfg["queries"]]
print(json.dumps(out, sort_keys=True))
"""


def worlds_cfg(w: P1.World, second_root: bool = False) -> dict[str, list[str]]:
    cfg = {
        "S-dyn": [str(w.s), str(w.inv["S-dyn"]), str(w.s)],
        "S4-stat": [str(w.s4), str(w.inv["S4-stat"]), str(w.s4)],
        "S-plus": [str(w.splus), str(w.inv["S-plus"]), str(w.splus)],
    }
    if second_root:
        cfg["S-dyn-r2"] = [str(w.s2), str(w.inv["S-dyn-r2"]), str(w.s2)]
    return cfg


def run_queries_in_child(
    w: P1.World, queries: list[list[str]], code: Path, label: str, *, seed: int | None = None,
    second_root: bool = False,
) -> list[dict[str, Any]]:  # fmt: skip
    script = w.scratch / "qscript.py"
    script.write_text(QSCRIPT)
    extra = {"PYTHONHASHSEED": str(seed)} if seed is not None else {}
    p = P1.run_py(
        [str(script)],
        P1.fresh_tmp(w.scratch, label),
        pythonpath=code,
        cwd=code,
        stdin=json.dumps({"worlds": worlds_cfg(w, second_root), "queries": queries}),
        extra_env=extra,
    )
    assert p.returncode == 0, p.stderr[-800:]
    return json.loads(p.stdout)


def base2_tree(w: P1.World, args: argparse.Namespace) -> Path:
    if args.base2:
        return Path(args.base2).resolve()
    base = w.scratch / "base2"
    if not base.is_dir():
        base.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(args.git_repo), "archive", BASE2], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(base)], input=tar, check=True)
    return base


# ---- PT2: the query set G -----------------------------------------------------------------------


def probe_pt2(
    w: P1.World, find: Finder, q: dict[str, Any], args: argparse.Namespace
) -> list[dict[str, Any]]:
    rows = run_set(find, q["G"])
    single = [r for r in rows if r["top1"] is not None]
    a_hits = sum(bool(r["top1"]) for r in single)
    b_hits = sum(r["exact"] for r in rows)
    # round 1's ranker on G and F, for the comparison (report only)
    old: dict[str, Any] = {}
    try:
        base = base2_tree(w, args)
        for name, key in (("G", "G"), ("F", "F_burned")):
            qs = [[r["world"], r["query"]] for r in q[key]]
            outs = run_queries_in_child(w, qs, base, f"pt2-r1-{name}")
            r1 = []
            for r, out in zip(q[key], outs, strict=True):
                out.setdefault("coverage", 0.0)
                r1.append(judge(r, out))
            old[name] = tally(r1)
    except Exception as err:  # noqa: BLE001 -- the comparison is report-only
        old = {"error": f"{type(err).__name__}: {err}"}
    gated = result(
        "PT2A",
        "G: the top-ranked route is the key route on >= 75% of the single-target rows (gated)",
        "13/16 (81%) (r)",
        {"top1": f"{a_hits}/{len(single)}", "gate": ">= 12/16", "rows": rows},
        a_hits * 4 >= 3 * len(single),
    )
    exact = result(
        "PT2B",
        "G: exact label + route on >= 75% of the rows (reported, NOT gated, predicted to miss)",
        "13/24 (54%) (r): predicted to miss",
        {
            "exact": f"{b_hits}/{len(rows)}",
            "gate_met": b_hits * 4 >= 3 * len(rows),
            "report_only": tally(rows),
            "round1_ranker": old,
            "rows": [r for r in rows if not r["exact"]],
        },
        None,
    )
    return [gated, exact]


# ---- PTDEV, PTF, PT3 ----------------------------------------------------------------------------


def probe_ptdev(w: P1.World, find: Finder, args: argparse.Namespace) -> dict[str, Any]:
    pt = []
    for pid, proj, question, want in P1.PT_QUERIES:
        out = find(WORLD_OF[proj], question)
        pt.append(
            {
                "id": pid, "project": proj, "question": question, "want": want, "got": out["hint"],
                "top": [(c["target"], c["status"], c["score"]) for c in out["candidates"][:3]],
            }
        )  # fmt: skip
    hits = sum(r["want"] == r["got"] for r in pt)
    r1 = Path(args.r1_data)
    own, texts = [], []
    cases = json.loads((HERE / "implementation_agent_scorer_cases.json").read_text())
    for qid, (world, label, target) in DEV_AGENT.items():
        sess = json.loads((r1 / "agent" / qid / "session.json").read_text())
        asked = [
            t["input"]["question"] for t in sess["tools"] if t["name"].endswith("workflow_find")
        ]
        qtext = next(x["text"] for x in cases["questions"] if x["id"] == qid)
        for bucket, text in ((own, asked[0]), (texts, qtext)):
            out = find(world, text)
            tg = [c["target"] for c in out["candidates"]]
            ok = out["hint"] == label and (target is None or (bool(tg) and tg[0] == target))
            bucket.append({
                "id": qid, "query": text, "label": label, "target": target,
                "got": [out["hint"], tg[:3]], "ok": ok,
            })  # fmt: skip
    return result(
        "PTDEV",
        "round 1's PT (8, hint as round 1; >= 6/8) and the 13 round-1 agent queries (report only)",
        "PT 8/8, agent queries 13/13 (r)",
        {
            "PT": f"{hits}/8",
            "PT_rows": pt,
            "agent_own_queries": f"{sum(r['ok'] for r in own)}/13",
            "agent_own_rows": own,
            "agent_question_texts": f"{sum(r['ok'] for r in texts)}/13",
            "agent_question_rows": texts,
        },
        hits >= 6,
        round1_PT_hits="2/8",
    )


def probe_ptf(find: Finder, q: dict[str, Any]) -> dict[str, Any]:
    rows = run_set(find, q["F_burned"])
    return result(
        "PTF",
        "the burned set F (development data; report only; a mutant catcher)",
        "20/27 (r, tuned-on)",
        {**tally(rows), "rows": rows},
        None,
    )


def probe_pt3(find: Finder, pt3: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for r in pt3:
        out = find(WORLD_OF[r["project"]], r["question"])
        tg = [c["target"] for c in out["candidates"]]
        want_hint = {"implemented": "exists"}.get(r["label"], r["label"])
        want_targets = ["impl:route:" + x for x in r["routes"]]
        top1 = bool(tg) and tg[0] in want_targets if want_targets else None
        hit = out["hint"] == want_hint and (top1 if want_targets else True)
        rows.append({
            "id": r["id"], "project": r["project"], "query": r["question"],
            "expected": [want_hint, want_targets or None],
            "got": [out["hint"], tg[:3]], "coverage": out["coverage"],
            "top1": top1, "hint_ok": out["hint"] == want_hint, "hit": bool(hit),
        })  # fmt: skip
    single = [r for r in rows if r["top1"] is not None]
    return result(
        "PT3",
        "the orchestrator's held-out queries (14; top-ranked route and label; report only; never "
        "tuned on)",
        "45-70% exact (p)",
        {
            "exact": f"{sum(r['hit'] for r in rows)}/{len(rows)}",
            "top1": f"{sum(bool(r['top1']) for r in single)}/{len(single)}",
            "hint": f"{sum(r['hint_ok'] for r in rows)}/{len(rows)}",
            "rows": rows,
        },
        None,
    )


# ---- PD2, PX2 -----------------------------------------------------------------------------------


def all_queries(
    q: dict[str, Any], pt3: list[dict[str, Any]], args: argparse.Namespace
) -> list[list[str]]:
    qs = [[r["world"], r["query"]] for r in q["G"] + q["F_burned"]]
    qs += [[WORLD_OF[p], question] for _, p, question, _ in P1.PT_QUERIES]
    cases = json.loads((HERE / "implementation_agent_scorer_cases.json").read_text())
    for qid, (world, _, _) in DEV_AGENT.items():
        sess = json.loads((Path(args.r1_data) / "agent" / qid / "session.json").read_text())
        qs.append(
            [
                world,
                next(
                    t["input"]["question"]
                    for t in sess["tools"]
                    if t["name"].endswith("workflow_find")
                ),
            ]
        )
        qs.append([world, next(x["text"] for x in cases["questions"] if x["id"] == qid)])
    qs += [[WORLD_OF[r["project"]], r["question"]] for r in pt3]
    return qs


def probe_pd2(
    w: P1.World, q: dict[str, Any], pt3: list[dict[str, Any]], args: argparse.Namespace
) -> dict[str, Any]:
    qs = all_queries(q, pt3, args)
    outs = []
    for i in range(args.runs):
        outs.append(
            run_queries_in_child(w, qs, REPO, f"pd2-{i}", seed=i * 7919 + 1, second_root=True)
        )
    blobs = [json.dumps(o, sort_keys=True) for o in outs]
    same = len(set(blobs)) == 1
    # a second archive root: the same S-dyn queries on the same app archived elsewhere
    qs_r2 = [["S-dyn-r2" if wd == "S-dyn" else wd, text] for wd, text in qs]
    outs_r2 = run_queries_in_child(w, qs_r2, REPO, "pd2-r2", seed=3, second_root=True)
    root_same = all(
        a == b for (wd, _), a, b in zip(qs, outs[0], outs_r2, strict=True) if wd == "S-dyn"
    )
    n_s = sum(1 for wd, _ in qs if wd == "S-dyn")
    return result(
        "PD2",
        "workflow_find on G, PT-dev, PT-F, PT3: 10 processes (random hash seeds) and a second "
        "archive root identical",
        "10/10 identical, second root identical",
        {
            "queries": len(qs),
            "processes": args.runs,
            "identical": same,
            "distinct_outputs": len(set(blobs)),
            "output_sha256": sha(blobs[0].encode()),
            "second_root_identical": root_same,
            "second_root_rows_compared": n_s,
        },
        same and root_same and args.runs >= 10,
    )


def probe_px2(w: P1.World) -> dict[str, Any]:
    tb = P1.toolbox(w.s)
    tb.call("implementation_get", {})  # the load
    qs = ["check in a habit", "see the details of one habit", "register a device for push", "list"]
    times: list[float] = []
    for _ in range(5):
        for question in qs:
            t0 = time.perf_counter()
            tb.call("workflow_find", {"question": question})
            times.append(round((time.perf_counter() - t0) * 1000, 2))
    med = sorted(times)[len(times) // 2]
    return result(
        "PX2",
        "one workflow_find call after load",
        "5-50 ms (p)",
        {"median_ms": med, "max_ms": max(times), "min_ms": min(times), "calls": len(times)},
        max(times) <= 1000,
    )


# ---- PB2: byte-compat against bb3ff0c -----------------------------------------------------------

PB2_SCRIPT = r"""
import hashlib, json, sys
from pathlib import Path
from lattice.llm.client import ScriptedGenerator
from lattice.mcp.server import INSTRUCTIONS
from lattice.mcp.tools import Toolbox
from lattice.spec import ask as A
from lattice.spec.draft import load_spec
from lattice.spec.implementation import build_report, load_inventory, report_json
from lattice.spec.rules import Trace


def h(x):
    return hashlib.sha256(x.encode() if isinstance(x, str) else x).hexdigest()


class B:
    kind = "probe"
    configured = False

    def __init__(self, root):
        self.root = root

    def model(self):
        raise AssertionError("never")


cfg = json.load(sys.stdin)
out = {"instructions": h(INSTRUCTIONS)}
for name, (clone, inv, root) in cfg["worlds"].items():
    clone, inv, root = Path(clone), Path(inv), Path(root)
    spec, errs = load_spec((clone / "spec.py").read_text())
    tr = Trace.from_json((clone / "spec.trace.json").read_text())
    im = load_inventory(inv, root)
    out["report:" + name] = h(report_json(build_report(spec, tr, im)))
    d = A.build_digest(spec, tr, im)
    out[name + ":digest"] = h(d.text)
    for q in ("Can a user check in a habit?", "Is there a delete endpoint?", "Can ben cancel?"):
        r = A.retrieve(d, q, [])
        out[f"{name}:{q}"] = [h(r.system), h(r.context), h(json.dumps(sorted(r.sent)))]
    cite = []
    for el in d.elements.values():
        if el.kind == "rule":
            cite = [{"target": el.target, "step": None, "quote": el.content[:30]}]
            break
    reply = json.dumps({"kind": "yes", "implementation": "implemented_unverified", "answer": "a",
                        "explanation": "e", "citations": cite})
    g = ScriptedGenerator([reply, reply])
    a = A.ask(spec, "Can a user check in a habit?", trace=tr, generator=g, model="m",
              inventory=load_inventory(inv, root))
    dd = a.to_dict()
    dd["usage"] = {k: v for k, v in dd["usage"].items() if k not in ("seconds", "calls")}
    out[name + ":answer"] = h(json.dumps(dd, sort_keys=True))
    out[name + ":prompts"] = h(json.dumps([[c[0], c[1]] for c in g.calls], sort_keys=True))
    for ro in (False, True):
        tb = Toolbox(B(clone), readonly=ro)
        listing = h(json.dumps(tb.list_tools(), sort_keys=True))
        out[f"{name}:tools:ro={int(ro)}"] = [tb.names(), listing]
print(json.dumps(out, sort_keys=True))
"""


def top_level_asts(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text())
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = node.name
        elif isinstance(node, ast.Assign):
            name = ",".join(ast.unparse(t) for t in node.targets)
        elif isinstance(node, ast.AnnAssign):
            name = ast.unparse(node.target)
        else:
            name = f"{type(node).__name__}:{ast.unparse(node)[:80]}"
        out[name] = ast.dump(node)
    return out


D7_CHANGED = {"_find_docs", "find_candidates", "FIND_THRESHOLD", "FIND_TIE", "_KIND_RANK"}


def git_show(repo: Path, rev: str, rel: str) -> bytes | None:
    p = subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{rel}"], capture_output=True, check=False
    )
    return p.stdout if p.returncode == 0 else None


def probe_pb2(w: P1.World, args: argparse.Namespace) -> dict[str, Any]:
    base = base2_tree(w, args)
    checks: dict[str, Any] = {}
    # 1. MCP instructions and tools/list: round 1's PB script (8 configs + single toolbox + ask CLI)
    script = w.scratch / "pb_script.py"
    script.write_text(P1.PB_SCRIPT)
    new = P1.run_py([str(script), str(REPO)], P1.fresh_tmp(w.scratch, "pb2-new"), pythonpath=REPO)
    old = P1.run_py(
        [str(script), str(base)], P1.fresh_tmp(w.scratch, "pb2-old"), pythonpath=base, cwd=base
    )
    assert new.returncode == 0 and old.returncode == 0, (new.stderr[-600:], old.stderr[-600:])
    n, o = json.loads(new.stdout), json.loads(old.stdout)
    pb_diff = sorted(k for k in set(n) | set(o) if n.get(k) != o.get(k))
    checks["pb_script_identical"] = not pb_diff
    # 2. report JSON of the four inventories, the PA hashes with the inventory, the tool lists
    script2 = w.scratch / "pb2_script.py"
    script2.write_text(PB2_SCRIPT)
    cfg = json.dumps(
        {"worlds": worlds_cfg(w) | {"S-stat": [str(w.s), str(w.inv["S-stat"]), str(w.s)]}}
    )
    rn = P1.run_py([str(script2)], P1.fresh_tmp(w.scratch, "pb2b-new"), pythonpath=REPO, stdin=cfg)
    ro = P1.run_py(
        [str(script2)], P1.fresh_tmp(w.scratch, "pb2b-old"), pythonpath=base, cwd=base, stdin=cfg
    )
    assert rn.returncode == 0 and ro.returncode == 0, (rn.stderr[-600:], ro.stderr[-600:])
    hn, ho = json.loads(rn.stdout), json.loads(ro.stdout)
    diff2 = sorted(k for k in set(hn) | set(ho) if hn.get(k) != ho.get(k))
    checks["report_pa_tools_identical"] = not diff2
    checks["round1_report_hashes"] = (
        hn.get("report:S-dyn") == R1_REPORT_SHA["S-dyn"]
        and hn.get("report:S4-stat") == R1_REPORT_SHA["S4-stat"]
    )
    # 3. PA without an inventory (round 1's script, new vs bb3ff0c)
    pa = w.scratch / "pa_base_script.py"
    pa.write_text(P1.PA_BASE_SCRIPT)
    an = P1.run_py([str(pa), str(w.s)], P1.fresh_tmp(w.scratch, "pb2c-new"), pythonpath=REPO)
    ao = P1.run_py(
        [str(pa), str(w.s)], P1.fresh_tmp(w.scratch, "pb2c-old"), pythonpath=base, cwd=base
    )
    assert an.returncode == 0 and ao.returncode == 0, (an.stderr[-600:], ao.stderr[-600:])
    checks["pa_without_inventory_identical"] = json.loads(an.stdout) == json.loads(ao.stdout)
    # 4. the AST of every top-level definition of implementation.py except D7's
    new_ast = top_level_asts(REPO / "lattice/spec/implementation.py")
    old_ast = top_level_asts(base / "lattice/spec/implementation.py")
    changed = sorted(k for k in old_ast if k in new_ast and old_ast[k] != new_ast[k])
    removed = sorted(k for k in old_ast if k not in new_ast)
    added = sorted(k for k in new_ast if k not in old_ast)
    checks["ast_only_d7_changed"] = set(changed) | set(removed) <= D7_CHANGED and all(
        a.startswith(("FIND_", "_find_")) or a in D7_CHANGED for a in added
    )
    # 5. round-1 frozen files: byte-identical to bb3ff0c, and to the round-1 freeze list
    froz = {}
    for rel in R1_FROZEN:
        old_bytes = git_show(args.git_repo, BASE2, rel)
        froz[rel] = old_bytes is not None and (REPO / rel).read_bytes() == old_bytes
    freeze_list = {}
    fz = args.git_repo / R1_DATA / "freeze.sha256"
    for line in fz.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        digest, rel = line.split(None, 1)
        freeze_list[rel.strip()] = digest
    fz_ok = {rel: sha((REPO / rel).read_bytes()) == d for rel, d in freeze_list.items()}
    fz_bad = sorted(rel for rel, ok in fz_ok.items() if not ok)
    checks["round1_frozen_files_identical"] = all(froz.values())
    checks["round1_freeze_list_only_implementation_py_differs"] = fz_bad == [
        "lattice/spec/implementation.py"
    ]
    # 6. round 1's data is untouched
    gd = subprocess.run(
        ["git", "-C", str(args.git_repo), "diff", BASE2, "--stat", "--", R1_DATA],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    gs = subprocess.run(
        ["git", "-C", str(args.git_repo), "status", "--porcelain", "--", R1_DATA],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    checks["round1_data_untouched"] = not gd.stdout.strip() and not gs.stdout.strip()
    # 7. round 1's four workflow_find tests, unedited, pass
    t = P1.run_py(
        ["-m", "pytest", "tests/mcp/test_implementation_tools.py", "-k", "test_workflow_find",
         "-q", "-p", "no:cacheprovider"],
        P1.fresh_tmp(w.scratch, "pb2-pytest"),
    )  # fmt: skip
    tail = t.stdout.strip().splitlines()[-1] if t.stdout.strip() else t.stderr[-200:]
    checks["round1_workflow_find_tests_pass"] = t.returncode == 0 and tail.startswith("4 passed")
    return result(
        "PB2",
        "byte-compat vs bb3ff0c: MCP instructions and tools/list, report JSON (S-dyn 84624894, "
        "S4-stat 80511cbb), PA hashes, the AST of implementation.py outside D7, round-1 frozen "
        "files, round-1 data, round-1 workflow_find tests",
        "all identical",
        {
            "checks": checks,
            "pb_script_differing": pb_diff,
            "report_pa_tools_differing": diff2,
            "report_sha256": {
                k.removeprefix("report:"): v for k, v in hn.items() if k.startswith("report:")
            },
            "ast_changed": changed,
            "ast_removed": removed,
            "ast_added": added,
            "frozen_vs_bb3ff0c": froz,
            "freeze_list_differing": fz_bad,
            "git_diff_round1_data": gd.stdout.strip(),
            "git_status_round1_data": gs.stdout.strip(),
            "pytest_tail": tail,
        },
        all(checks.values()),
    )


# ---- PR1: round 1's probes, unchanged, at the new code ------------------------------------------


def probe_pr1(w: P1.World, args: argparse.Namespace) -> dict[str, Any]:
    out = w.out / "r1"
    scratch = w.scratch / "r1"
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    cmd = [
        args.driver_python, str(HERE / "implementation_probes.py"),
        "--streak-ref", args.streak_ref, "--streak-ref2", args.streak_ref2, "--runs", "3",
        "--workflows-dyn", str(args.workflows_dyn), "--scratch", str(scratch), "--out", str(out),
    ]  # fmt: skip
    if args.no_docker:
        cmd.append("--no-docker")
    t0 = time.perf_counter()
    p = subprocess.run(
        cmd, capture_output=True, text=True, env=P1.env_for(P1.fresh_tmp(w.scratch, "pr1")),
        cwd=REPO, check=False,
    )  # fmt: skip
    new_rows = {r["id"]: r for r in json.loads((out / "probes.json").read_text())}
    old_rows = {r["id"]: r for r in json.loads((Path(args.r1_data) / "probes.json").read_text())}
    cmp_ = {}
    for pid, o in old_rows.items():
        n = new_rows.get(pid)
        cmp_[pid] = {
            "round1": o.get("pass"), "now": None if n is None else n.get("pass"),
            "ran": None if n is None else n.get("ran", True),
        }  # fmt: skip
    pd_hash = {
        k: v.get("json_sha256")
        for k, v in ((new_rows.get("PD") or {}).get("observed") or {}).get("rows", {}).items()
    }
    old_pd_hash = {k: v.get("json_sha256") for k, v in old_rows["PD"]["observed"]["rows"].items()}
    except_pt = {pid: c for pid, c in cmp_.items() if pid != "PT"}
    same_except_pt = all(c["now"] == c["round1"] for c in except_pt.values())
    pt = new_rows.get("PT")
    return result(
        "PR1",
        "round 1's implementation_probes.py, unchanged, at the new code (--runs 3, PF with "
        "Docker); round 2 supersedes PT",
        "every row as round 1; PT now 8/8 (r)",
        {
            "rc": p.returncode,
            "seconds": round(time.perf_counter() - t0, 1),
            "rows": cmp_,
            "all_rows_but_PT_as_round1": same_except_pt,
            "PT_now": (pt or {}).get("observed", {}).get("hits"),
            "PT_round1": "2/8",
            "PD_report_hashes_equal_round1": pd_hash == old_pd_hash,
            "PF": new_rows.get("PF", {}).get("pass", "NOT RUN"),
            "stderr_tail": p.stderr.strip()[-300:],
        },
        same_except_pt and pd_hash == old_pd_hash,
    )


# ---- main ---------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--workflows-dyn", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-docker", action="store_true")
    ap.add_argument("--driver-python", default=sys.executable)
    ap.add_argument("--queries", default=str(HERE / "implementation_find2_queries.json"))
    ap.add_argument(
        "--data", default=str(REPO / "docs/results/data/2026-10-16-implementation-diff-round2")
    )
    ap.add_argument(
        "--git-repo", default=str(REPO), help="the repository to ask git (a mutant copy has none)"
    )
    ap.add_argument("--r1-data", default="")
    ap.add_argument(
        "--base2", default="", help="a bb3ff0c archive (default: git archive into scratch)"
    )
    args = ap.parse_args(argv)
    args.git_repo = Path(args.git_repo).resolve()
    args.r1_data = args.r1_data or str(args.git_repo / R1_DATA)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    q = json.loads(Path(args.queries).read_text())
    verified = verify_query_files(Path(args.queries), Path(args.data))
    pt3 = json.loads((Path(args.data) / "pt3_queries.json").read_text())
    only = {x for x in args.only.split(",") if x}
    w = P1.World(args)
    t0 = time.perf_counter()
    P1.setup(w, False)
    setup_s = round(time.perf_counter() - t0, 1)
    (out / "setup.json").write_text(
        json.dumps(
            {
                "streak_ref": args.streak_ref, "streak_ref2": args.streak_ref2,
                "query_files": verified,
                "workflows_sha256": {k: sha(v.read_bytes()) for k, v in w.wf.items()},
                "inventory_sha256": {
                    k: sha((v / "inventory.json").read_bytes()) for k, v in w.inv.items()
                },
                "setup_seconds": setup_s,
            },
            indent=2, sort_keys=True,
        ) + "\n"
    )  # fmt: skip
    find = Finder(w)
    table: dict[str, Callable[[], Any]] = {
        "PT2": lambda: probe_pt2(w, find, q, args),
        "PTDEV": lambda: probe_ptdev(w, find, args),
        "PTF": lambda: probe_ptf(find, q),
        "PT3": lambda: probe_pt3(find, pt3),
        "PD2": lambda: probe_pd2(w, q, pt3, args),
        "PX2": lambda: probe_px2(w),
        "PB2": lambda: probe_pb2(w, args),
        "PR1": lambda: probe_pr1(w, args),
    }
    rows: list[dict[str, Any]] = []
    for pid in PROBE_ORDER:
        if only and pid not in only:
            rows.append(
                not_run(pid, "see the pre-registration, section 3", "not selected (--only)")
            )
            continue
        t1 = time.perf_counter()
        try:
            r = table[pid]()
        except Exception as err:  # noqa: BLE001 -- a probe that crashes is a result
            import traceback  # noqa: PLC0415

            r = result(
                pid,
                "crashed",
                None,
                {
                    "error": f"{type(err).__name__}: {err}",
                    "trace": traceback.format_exc()[-1500:],
                },
                False,
            )
        secs = round(time.perf_counter() - t1, 1)
        for x in r if isinstance(r, list) else [r]:
            x["seconds"] = secs
            rows.append(x)
    (out / "probes.json").write_text(json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n")
    lines = ["# Implementation diff, round 2: probes (workflow_find ranking)", ""]
    for r in rows:
        if not r.get("ran", True):
            lines.append(f"- NOT RUN {r['id']}: {r['probe']} -- {r['reason']}")
            continue
        verdict = "PASS" if r["pass"] else ("REPORT" if r["pass"] is None else "FAIL")
        obs = r["observed"]
        brief = (
            {
                k: v
                for k, v in obs.items()
                if k not in ("rows", "PT_rows", "agent_own_rows", "agent_question_rows")
            }
            if isinstance(obs, dict)
            else obs
        )
        shown = json.dumps(brief, sort_keys=True, default=str)[:700]
        lines.append(f"- RAN {r['id']}: {verdict} -- {r['probe']}: observed {shown}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if all(r.get("pass") in (True, None) for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
