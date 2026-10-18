"""M3: normalise (drop `timings`; in notes, container name and durations) and compare the check reports."""
import hashlib, json, re, sys
from pathlib import Path
here = Path(sys.argv[1])  # the m3 data dir
sys.path.insert(0, str(Path(__file__).parent))
from cmp import diffs
def load(name, normalise=True):
    d = json.loads((here / f"{name}.json").read_text())
    if normalise:
        d.pop("timings", None)
        d["notes"] = [re.sub(r"lattice-mysql-[0-9a-f]+", "lattice-mysql-X", re.sub(r"\b\d+(\.\d+)? ?m?s\b", "Ns", n)) for n in d["notes"]]
    return d
runs = ["acd-v0-r1", "acd-v0-r2", "acd-v1-r1", "acd-v2-r1", "acd-v3-r1", "acd-v3-r2"]
out = {"runs": {}, "raw_diffs_vs_acd-v0-r1": {}, "normalised_diffs_vs_acd-v0-r1": {}}
base = load("acd-v0-r1")
for r in runs:
    d = load(r)
    raw = (here / f"{r}.json").read_bytes()
    out["runs"][r] = {"raw_sha256": hashlib.sha256(raw).hexdigest()[:16],
                      "normalised_sha256": hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16],
                      "verdict": d["verdict"], "counts": d["counts"], "findings": d["findings"], "cases": len(d["cases"]),
                      "rc": (here / f"{r}.rc").read_text().strip()}
    out["normalised_diffs_vs_acd-v0-r1"][r] = len(diffs(base, d))
    out["raw_diffs_vs_acd-v0-r1"][r] = len(diffs(load("acd-v0-r1", False), load(r, False))) if r != "acd-v0-r1" else 0
layer_a = {}
for v in ("v0", "v1", "v2", "v3"):
    d = load(f"a-{v}", False); d.pop("timings", None)
    layer_a[v] = {"sha256": hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16], "counts": d["counts"]}
out["layer_A_only"] = layer_a
print(json.dumps(out, indent=1))
