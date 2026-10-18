"""POST HOC measurements for the Go parity gaps (Spike J, node J1). Nothing here is a gated probe:
each row is run *after* the scored probe run, on scratch copies, to explain a miss. The
fixtures and the library stay as frozen; a row that would "fix" a miss reports the delta only.

* PH1: why GO801 is silent on goginapp: ``r.Use(gin.Recovery())`` (an outside-package
  middleware) makes the router's middleware unreadable, and gocheck prefers silence.
* PH2: PA3's JM5 on a copy of goginapp without that line (the delta a fixture change would give).
* PH4: why JM6 cannot be seen on goginapp even with an empty baseline: the original already has a GO802 for
  ``User.token`` on ``POST /login`` (``access_token: u.Token`` reads a private column from a row); a mutant that
  serializes the struct gets a different message but the same key (route and column), and a struct held inside
  ``gin.H`` adds nothing the checker reads.
* PH3: JM6 under the *derived* policy (go_policy.json) instead of the imported spec's: the spec's
  profile declares no private column for User (the profile generator reads response models, and a
  Go extract has none), and the spec's declaration wins for a declared table.

    "$PY" dogfood/go_parity_posthoc.py --scratch "$SCRATCH/ph" --out "$SCRATCH/ph-data" --import-dir DIR
"""  # noqa: E501

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import go_parity_probes as P  # noqa: E402


def variant(scratch: Path, name: str, edits: list[tuple[str, str, str]]) -> Path:
    dest = scratch / name / "goginapp"
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(P.GOGINAPP, dest)
    for rel, old, new in edits:
        f = dest / rel
        text = f.read_text()
        assert text.count(old) == 1, (name, rel, old[:40])
        f.write_text(text.replace(old, new))
    return dest


NO_RECOVERY = ("main.go", "\tr.Use(gin.Recovery())\n", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--import-dir", required=True, help="an S2 import directory (candidate_spec.py)"
    )
    args = ap.parse_args(argv)
    scratch, out = Path(args.scratch).resolve(), Path(args.out).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    from lattice.gocheck.derive import derive_go_policy, go_spec_policy

    cand = P.load_candidate(Path(args.import_dir))
    derived = derive_go_policy(P.GOGINAPP)
    spec_pol = go_spec_policy(cand.SPEC, P.GOGINAPP, derived)
    rows: dict[str, Any] = {}

    def new_findings(module: Path, pol: Any, base_pol: Any, base_module: Path) -> list[str]:
        known = {(f.code, f.message) for f in P.findings_of(base_module, base_pol)}
        return [f"{f.code} {f.key}" for f in P.findings_of(module, pol)
                if (f.code, f.message) not in known]  # fmt: skip

    # PH1/PH2: GO801 with and without gin.Recovery()
    jm5 = P.mutate_goginapp(P.Ctx(scratch / "c", out, "HEAD"), "JM5")
    rows["PH1"] = {"GO801 on JM5 with r.Use(gin.Recovery())":
                   new_findings(jm5, spec_pol, spec_pol, P.GOGINAPP)}  # fmt: skip
    plain = variant(scratch, "norecovery", [NO_RECOVERY])
    jm5b = variant(scratch, "norecovery-jm5", [NO_RECOVERY, P.MUT_EDITS["JM5"]])
    rows["PH2"] = {
        "original without Recovery, findings outside the baseline":
        [f"{f.code} {f.key}" for f in P.findings_of(plain, spec_pol) if f.key not in derived.baseline],  # noqa: E501
        "JM5 without Recovery, new findings": new_findings(jm5b, spec_pol, spec_pol, plain),
    }  # fmt: skip
    # PH3: JM6 under the derived policy
    jm6 = P.mutate_goginapp(P.Ctx(scratch / "c6", out, "HEAD"), "JM6")
    rows["PH3"] = {
        "spec private columns for User": list(spec_pol.private.get("User", ())),
        "derived private columns for User": list(derived.private.get("User", ())),
        "JM6 new findings under the imported spec's policy": new_findings(jm6, spec_pol, spec_pol, P.GOGINAPP),  # noqa: E501
        "JM6 new findings under the derived policy": new_findings(jm6, derived, derived, P.GOGINAPP),  # noqa: E501
    }  # fmt: skip
    # PH4: the keys of GO802 on the original and on JM6 variants, derived policy, empty baseline
    import dataclasses

    empty = dataclasses.replace(derived, baseline=frozenset())
    direct = ("api/api.go",
              'c.JSON(http.StatusOK, gin.H{"access_token": u.Token, "token_type": "bearer", "user": u})',  # noqa: E501
              "c.JSON(http.StatusOK, u)")  # fmt: skip
    ph4: dict[str, Any] = {}
    for name, edits in (("original", []), ("JM6", [P.MUT_EDITS["JM6"]]),
                        ("JM6 + the answer is the struct itself", [P.MUT_EDITS["JM6"], direct])):  # fmt: skip  # noqa: E501
        m = variant(scratch, "ph4-" + name.split()[0].lower() + str(len(edits)), edits)
        ph4[name] = [f"{f.code} {f.key} :: {f.message[:70]}" for f in P.findings_of(m, empty)]
    rows["PH4"] = ph4
    (out / "posthoc.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
