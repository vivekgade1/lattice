"""Arm L driver: the Studio rebuild loop (lattice.studio.rebuild.Rebuilder, agent=claude via the shim) on the
toybuild fixture. Usage: armL_driver.py --repo REPO --state DIR --out OUT.json
Sonnet, max 3 rounds, --max-turns 40 per step, USD 5.00 total cap on the CLI's list estimate."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from pathlib import Path

from lattice.studio.rebuild import FINISHED, Rebuilder


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--shim", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=2700)
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    cfg = tomllib.loads((repo / "studio.toml").read_text())
    protected = cfg.get("mcp", {}).get("protected", [])
    log_lines: list[str] = []
    rb = Rebuilder(
        repo, cfg["rebuild"], name="toybuild", model="sonnet", claude_bin=a.shim,
        protected=protected, log=log_lines.append, trailer="Lattice-Rebuild: arm-l",
    )
    ok, why = rb.available()
    print("available:", ok, why, flush=True)
    if not ok:
        Path(a.out).write_text(json.dumps({"available": False, "why": why}))
        return 2
    started = rb.start([], scope="all", model="sonnet", max_rounds=3, baseline_wait=300)
    rid = started["id"] if isinstance(started, dict) and "id" in started else str(started)
    print("started", started, flush=True)
    t0 = time.time()
    rec = {}
    while time.time() - t0 < a.timeout:
        rec = rb.get(rid, wait=10.0)
        st = rec.get("status")
        print(f"[{int(time.time()-t0):5d}s] {st} rounds={len(rec.get('rounds') or [])}", flush=True)
        if st in FINISHED:
            break
        time.sleep(5)
    else:
        rb.stop(rid)
        rec = rb.get(rid)
        rec["driver_timeout"] = True
    Path(a.out).write_text(json.dumps({"record": rec, "log": log_lines[-200:]}, indent=1, default=str))
    print("final status:", rec.get("status"))
    rb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
