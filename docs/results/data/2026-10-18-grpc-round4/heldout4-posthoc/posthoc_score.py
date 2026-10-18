import json, os, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path[:0] = ["/home/user/wt/i4", "/home/user/wt/i4/dogfood"]
import grpc_probes4 as p4
S = Path(sys.argv[1]); scratch = S/"diag"/"scratch"
os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch/"gocache")); os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch/"bridgecache"))
root = S/"diag"/"p"/"app"
app = p4.p3.load_app(root, scratch)
ctx = {}
rows = p4.rpc_rows4(app, "HO4")
for stage in ("rec", "flows"):
    try:
        rows.append(p4.rec_row4(app, "HO4", ctx) if stage == "rec" else p4.flows_row4(app, "HO4", ctx))
    except Exception as e:
        print(stage, "CRASH", type(e).__name__, str(e)[:300])
out = [r.row() for r in rows]
print(json.dumps(out, indent=1, default=str))
print("tables_source", ctx.get("tables_source"))
