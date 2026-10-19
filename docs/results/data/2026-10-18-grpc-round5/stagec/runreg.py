import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/vivekgade/Documents/lattice-grb/dogfood")
import grpc_probes5 as g

pr = g.p5_reg({"reg_before": Path(sys.argv[1]), "reg_after": Path(sys.argv[2])})
Path(sys.argv[3]).write_text(json.dumps({"row": pr.row()}, indent=1, default=str) + "\n")
print(pr.status, json.dumps(pr.observed))
