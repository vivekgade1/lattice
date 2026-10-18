import json, os, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path[:0] = ["/home/user/wt/i4", "/home/user/wt/i4/dogfood"]
import grpc_probes4 as p4
S = Path(sys.argv[1]); scratch = S/"diag"/"scratch"
os.environ.setdefault("LATTICE_GOCHECK_CACHE", str(scratch/"gocache")); os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(scratch/"bridgecache"))
app = p4.p3.load_app(S/"diag"/"p"/"app", scratch)
names = "OK CANCELLED UNKNOWN INVALID_ARGUMENT DEADLINE_EXCEEDED NOT_FOUND ALREADY_EXISTS PERMISSION_DENIED RESOURCE_EXHAUSTED FAILED_PRECONDITION ABORTED OUT_OF_RANGE UNIMPLEMENTED INTERNAL UNAVAILABLE DATA_LOSS UNAUTHENTICATED".split()
m = {n.replace("_", ""): n for n in names}
for k, v in app.truth["rpcs"].items():
    v["codes"] = [m.get(c.upper().replace("_",""), c) for c in v["codes"]]
pr = [x for x in p4.rpc_rows4(app, "HO4") if x.id == "HO4-CODES"][0].row()
print(json.dumps(pr["observed"])); 
d = pr["detail"]; print("missed", len(d.get("missed", [])), "extra", d.get("extra")); print(json.dumps(d)[:6000])
ex = {f"{s['name']}/{r['name']}": r for s in app.extract["grpc"]["services"] for r in s["rpcs"]}
for k, r in ex.items(): print(k, r.get("state"), r.get("auth"), [c if isinstance(c,str) else c.get("code") for c in r.get("codes", [])][:8], "unread:", r.get("unread"))
print(json.dumps(app.extract["grpc"].get("unread", app.extract.get("unread")))[:3000])
