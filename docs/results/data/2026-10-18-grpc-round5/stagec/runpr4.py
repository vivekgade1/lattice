import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/vivekgade/Documents/lattice-grb/dogfood")
import grpc_probes5 as g

src = Path(sys.argv[1])
pr = g.pr4_all({"pr4all_from": src})
Path(sys.argv[2]).write_text(json.dumps({"row": pr.row()}, indent=1, default=str) + "\n")
print(pr.status, json.dumps(pr.observed))
