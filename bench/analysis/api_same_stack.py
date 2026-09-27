# ruff: noqa
# Ad-hoc analysis script (run from the repo root after benchmark runs).
"""Same-stack performance: model-written FastAPI (arm P) vs Lattice-generated FastAPI (arm B's program
compiled by codegen), both run by the same sandboxed driver on the same tasks, repeated for stable timing."""
import json, pathlib, shutil, statistics as st, sys, tempfile
from bench.api_datasets import api_profile, build_db
from bench.api_runner import run_cases_fastapi
from bench.api_tasks import API_TASKS
from bench.cli import API_KIT_PATH
from lattice.api.codegen import compile_source_to_fastapi

run_dir = pathlib.Path(sys.argv[1]); reps = int(sys.argv[2]) if len(sys.argv) > 2 else 5
prof = api_profile(); tasks = {t.id: t for t in API_TASKS}
outc = {(r["arm"], r["task_id"]): r for r in map(json.loads, open(run_dir / "outcomes.jsonl"))}
tmp = pathlib.Path(tempfile.mkdtemp()); pristine = tmp / "pristine.db"; build_db(pristine)
db_builder = lambda p: shutil.copyfile(pristine, p)
rows = []
for tid, task in tasks.items():
    p, b = outc.get(("P", tid)), outc.get(("B", tid))
    if not (p and b and p["correct"] and b["correct"]): continue
    p_code = (run_dir / "work" / "P" / tid / "solution.py").read_text()
    b_src = sorted((run_dir / "work" / "B" / tid).glob("program_round*.py"))[-1].read_text()
    _, g_code = compile_source_to_fastapi(b_src, prof)
    lat = {"P": [], "G": []}; q = {}
    for _ in range(reps):
        for arm, code in (("P", p_code), ("G", g_code)):
            res = run_cases_fastapi(code, tmp / f"{arm}_{tid}", task, db_builder=db_builder, kit_path=API_KIT_PATH)
            assert res.started and all(c.passed for c in res.cases), (arm, tid, [c.reason for c in res.cases if not c.passed])
            lat[arm] += [c.latency_ms for c in res.cases]; q[arm] = st.mean(c.queries for c in res.cases)
    rows.append((tid, st.median(lat["P"]), st.median(lat["G"]), q["P"], q["G"]))
print(f"tasks compared: {len(rows)} (both arms correct), reps={reps}")
print(f"median latency ms  P (model FastAPI) {st.median(r[1] for r in rows):.2f}  |  G (Lattice-generated FastAPI) {st.median(r[2] for r in rows):.2f}")
print(f"mean queries/req   P {st.mean(r[3] for r in rows):.2f}  |  G {st.mean(r[4] for r in rows):.2f}")
fam = {}
for tid, lp, lg, qp, qg in rows:
    fam.setdefault(tasks[tid].family, []).append((lp, lg, qp, qg))
for f, v in sorted(fam.items()):
    print(f"  {f:10s} n={len(v):2d}  latency P {st.median(x[0] for x in v):5.2f} G {st.median(x[1] for x in v):5.2f} ms | queries P {st.mean(x[2] for x in v):.2f} G {st.mean(x[3] for x in v):.2f}")
