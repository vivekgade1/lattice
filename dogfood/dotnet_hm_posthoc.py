"""POST HOC (after the scored HM run, not a gate): the 16 held-out mutants judged by layer A under the policy DERIVED FROM THE
CODE (derive_dn_policy on the pristine H1) instead of the policy of the imported candidate spec the scored run used.
Usage: python dogfood/dotnet_hm_posthoc.py H1_TGZ PATCH_DIR OUT_JSON SCRATCH"""

import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from lattice.dncheck.derive import derive_dn_policy
from lattice.dncheck.policy import all_dn_findings

DETECTOR = {
    1: "DN801",
    2: "DN801",
    3: "DN801",
    4: "DN801",
    5: "DN802",
    6: "DN802",
    11: "DN801",
    15: "DN801",
    16: "DN801",
}


def main(tgz: str, patches: str, out: str, scratch: str) -> None:
    scr = Path(scratch)
    shutil.rmtree(scr, ignore_errors=True)
    scr.mkdir(parents=True)
    with tarfile.open(tgz) as t:
        t.extractall(scr / "h1", filter="data")
    pristine = scr / "h1" / "tests" / "spec" / "outlay"
    policy = derive_dn_policy(pristine)
    base = {f.key for f in all_dn_findings(pristine, policy)}
    rows = {"policy": policy.to_json(), "pristine_findings": sorted(base), "mutants": {}}
    for i in range(1, 17):
        copy = scr / f"m{i:02d}"
        shutil.copytree(pristine, copy)
        r = subprocess.run(
            ["patch", "-p1", "-s", "-i", str((Path(patches) / f"m{i:02d}.diff").resolve())],
            cwd=copy,
            capture_output=True,
            text=True,
        )
        found = {f.key: f"{f.code} {f.file}:{f.line}" for f in all_dn_findings(copy, policy)}
        new = {k: v for k, v in found.items() if k not in base}
        rows["mutants"][f"HM{i}"] = {
            "applied": r.returncode == 0,
            "new_findings": new,
            "detector": DETECTOR.get(i),
            "flagged_by_detector": any(v.startswith(DETECTOR.get(i, "-")) for v in new.values()),
        }
        print(f"HM{i}: applied={r.returncode == 0} new={sorted(new.values())}", flush=True)
    Path(out).write_text(json.dumps(rows, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:5])
