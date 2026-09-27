# ruff: noqa
# Ad-hoc analysis script (run from the repo root after benchmark runs).
"""Code size and runtime metrics: FastAPI+repair (P) vs Lattice+repair (B), correct solutions only."""
import json, glob, os, statistics as st, pathlib
from bench.metrics import program_metrics
from bench.api_datasets import api_profile
from lattice.api.codegen import compile_source_to_fastapi

prof = api_profile()
dirs = [d for d in glob.glob("results/api-*") if os.path.isdir(d) and "smoke" not in d and "merged" not in d]
rows = []  # (dir, outcome)
for d in dirs:
    for l in open(f"{d}/outcomes.jsonl"):
        r = json.loads(l)
        # explicit Opus: arms B/C come from the -bc re-run (the first run lost them to credits)
        if d.startswith("results/api-claude-opus-5-") and not d.endswith("-bc") and r["arm"] in "BC":
            continue
        rows.append((d, r))

def gen_lines(d, r):
    f = sorted(pathlib.Path(d, "work", r["arm"], r["task_id"]).glob("program_round*.py"))
    if not f:
        return None
    _, app = compile_source_to_fastapi(f[-1].read_text(), prof)
    return program_metrics(app).lines if app else None

models = sorted({r["model"].split(",")[0] for _, r in rows if r.get("model")})
hdr = f"{'model':26s} arm  n   lines  AST   chars  gen_app_lines  queries/req  latency_ms  out_tokens  $/correct"
print(hdr)
for model in models:
    for arm in "PB":
        ok = [(d, r) for d, r in rows if r["arm"] == arm and r["correct"] and r.get("model", "").startswith(model)]
        allr = [r for d, r in rows if r["arm"] == arm and r.get("model", "").startswith(model)]
        m = lambda k: st.mean(r[k] for _, r in ok if r.get(k))
        gl = [g for g in (gen_lines(d, r) for d, r in ok) if g] if arm == "B" else []
        cost = sum(r.get("cost_usd", 0) for r in allr) / max(len(ok), 1)
        print(f"{model:26s} {arm}  {len(ok):3d}  {m('program_lines'):5.1f} {m('program_ast_nodes'):5.0f} {m('program_chars'):6.0f}  "
              f"{(st.mean(gl) if gl else 0):13.0f}  {m('mean_queries'):11.2f}  {m('mean_latency_ms'):10.2f}  {m('output_tokens'):10.0f}  {cost:9.4f}")
