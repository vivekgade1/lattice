import json, os, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path[:0] = ["/home/user/wt/i4", "/home/user/wt/i4/dogfood"]
import grpc_probes4 as p4
S = Path(sys.argv[1]); scratch = S/"diag"/"scratch"
os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch/"gocache")); os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch/"bridgecache"))
app = p4.p3.load_app(S/"diag"/"p"/"app", scratch)
g = app.extract["grpc"]
for s in g["services"]:
    for r in s["rpcs"]:
        if r["name"] in ("Login","WhoAmI","Logout"): print(s["name"], r["name"], json.dumps({k:r[k] for k in r if k in ("auth","auth_reason","interceptors","state")}), json.dumps(r.get("auth_detail"))[:600])
print({k: (v if not isinstance(v,(list,dict)) else type(v).__name__) for k,v in g.items()})
print(json.dumps(g.get("interceptors"))[:1500])
print(json.dumps(app.extract.get("tables_source")), [t["table"] for t in app.extract["tables"]])
