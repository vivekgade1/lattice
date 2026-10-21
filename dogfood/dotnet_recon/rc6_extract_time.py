"""RC6: extract wall time on a project (docs/results/2026-10-18-dotnet-prereg.md, 5.1 and N1; expected 2-8 s on D1
with the helper prebuilt).

    python dogfood/dotnet_recon/rc6_extract_time.py --helper <dncheck.dll> --root <project-dir> [--runs 5] [--out FILE]
    python dogfood/dotnet_recon/rc6_extract_time.py --self-test

Runs ``dotnet <dncheck.dll> extract`` with ``{"root": "<project-dir>"}`` on stdin ``--runs`` times, records the wall
time of each run (a Python subprocess timeout, never ``timeout``), the sha256 of every answer (the runs must be
byte-identical: DET-10) and the top-level keys and ``schema`` of the document. The helper does not exist until
stage B1; ``--self-test`` exercises the timing/hash code against a stand-in command and needs no helper.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

STAND_IN = (
    "import json,sys\n"
    "req = json.loads(sys.stdin.read())\n"
    "print(json.dumps({'schema': 'standin/1', 'root': req['root'], 'routes': [], 'tables': []}, sort_keys=True))\n"
)


def measure(cmd: list[str], root: Path, runs: int, timeout: float) -> dict:
    times: list[float] = []
    digests: list[str] = []
    doc: dict = {}
    for _ in range(runs):
        rc, out, err, secs = C.run(cmd, stdin=json.dumps({"root": str(root)}), timeout=timeout)
        if rc != 0:
            return {"error": f"exit {rc}: {err[-800:]}", "times": times}
        times.append(secs)
        digests.append(C.sha256_bytes(out.encode()))
        doc = json.loads(out)
    return {
        "wall": C.summarize(times),
        "identical_runs": len(set(digests)) == 1,
        "sha256": digests[0],
        "schema": doc.get("schema"),
        "top_level_keys": sorted(doc),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--helper")
    ap.add_argument("--root")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    if args.self_test:
        with tempfile.TemporaryDirectory(prefix="rc6-selftest-") as d:
            script = Path(d) / "standin.py"
            script.write_text(STAND_IN)
            res = measure([sys.executable, str(script)], Path(d), 3, 30)
        ok = "error" not in res and res["identical_runs"] and res["schema"] == "standin/1"
        C.emit({"rc": "RC6", "self_test": "ok" if ok else "FAILED", **res}, args.out)
        return 0 if ok else 1
    if not args.helper or not args.root:
        sys.exit("RC6: --helper and --root are required (or --self-test)")
    helper = Path(args.helper)
    if not helper.is_file():
        sys.exit(f"RC6: NOT RUN: the helper {helper} does not exist (stage B1 builds it)")
    res = measure(
        [C.dotnet_exe(), str(helper), "extract"], Path(args.root).resolve(), args.runs, args.timeout
    )
    res.update(
        {
            "rc": "RC6",
            "sdk": C.sdk_version(),
            "predicted": "2-8 s",
            "within_prediction": "wall" in res and 2 <= res["wall"]["median"] <= 8,
        }
    )
    C.emit(res, args.out)
    return 0 if "error" not in res else 1


if __name__ == "__main__":
    raise SystemExit(main())
