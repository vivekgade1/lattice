import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/Users/vivekgade/Documents/lattice-grb/dogfood")
import grpc_probes5 as g

which, out = sys.argv[1], Path(sys.argv[2])
S = Path(os.environ["S"])
ctx = {
    "scratch": S / ("scratch-" + which),
    "corpora": S / "corpora",
    "base": S / "base",
    "self_test": False,
}
ctx["scratch"].mkdir(parents=True, exist_ok=True)
if which == "hapi":
    pr = g.p5_hapi(ctx)
elif which == "reh":
    pr = g.p5_reh(ctx)
elif which == "leak":
    c = g.corpora_paths(ctx["corpora"])
    desk = g.q2.load_desk(Path(c["deskd"]), ctx["scratch"]) if c["deskd"] else None
    pr = g.pf5_leak(ctx, desk)
else:
    raise SystemExit(which)
out.write_text(
    json.dumps(
        {
            "schema": "lattice.grpc-probes5-row/1",
            "row": pr.row(),
            "library_sha256": g.library_hashes(),
        },
        indent=1,
        default=str,
    )
    + "\n"
)
print(pr.status, pr.id)
