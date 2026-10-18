"""M2: derived GoPolicy vs GoPolicy.from_spec on Streak's spec (layer A policy compat)."""
import json, sys, dataclasses
from pathlib import Path
from lattice.gocheck import GoPolicy, run_gocheck
from lattice.gocheck.derive import derive_go_policy, go_spec_policy
from spec import SPEC
mod = Path(sys.argv[1])
stated = GoPolicy.from_spec(SPEC)
derived = derive_go_policy(mod)
overlay = go_spec_policy(SPEC, mod)
out = {"lattice": __import__("lattice").__file__}
out["stated"] = stated.to_json()
out["derived"] = derived.to_json()
out["derived_baseline"] = sorted(derived.baseline)
out["overlay"] = overlay.to_json()
out["overlay_baseline"] = sorted(overlay.baseline)
def diff(a, b):
    return {k: {"a": a[k], "b": b[k]} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
out["stated_vs_overlay_to_json_diff"] = diff(out["stated"], out["overlay"])
out["stated_vs_derived_to_json_diff"] = diff(out["stated"], out["derived"])
# field-level (beyond to_json)
for name, p in (("overlay", overlay), ("derived", derived)):
    fa, fb = dataclasses.asdict(stated), dataclasses.asdict(p)
    out[f"stated_vs_{name}_fields"] = {k: {"stated": str(fa[k]), name: str(fb[k])} for k in fa if fa[k] != fb[k]}
def keys(pol):
    return [(f.code, f.key, f.file, f.line, f.message) for f in run_gocheck(mod, pol, strict=True)]
out["findings_stated"] = keys(stated)
out["findings_overlay"] = keys(overlay)
out["findings_derived_with_baseline"] = keys(derived)
out["findings_equal_stated_overlay"] = out["findings_stated"] == out["findings_overlay"]
print(json.dumps(out, indent=1, default=str))
