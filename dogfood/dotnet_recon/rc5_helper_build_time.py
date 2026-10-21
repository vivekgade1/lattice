"""RC5: helper build wall time, cold and warm (docs/results/2026-10-18-dotnet-prereg.md, 5.1 and N1; expected
20-60 s cold -- the restore of Roslyn from the NuGet cache -- and < 1 s warm).

    python dogfood/dotnet_recon/rc5_helper_build_time.py <helper-src-dir> [--scratch DIR] [--out FILE]

``<helper-src-dir>`` holds the helper's ``*.csproj`` (for D1's round: ``lattice/dncheck/src``). N1's procedure is
timed on a *copy* in the scratch directory (the source directory is never built in place):

* ``cold``: a fresh copy with no ``obj/`` or ``bin/``: ``dotnet build -c Release --disable-build-servers
  -nodeReuse:false`` (with ``--locked-mode`` when a ``packages.lock.json`` is present). The NuGet cache is
  NOT cleared (it is the owner's), so "cold" is a cold build, not a cold download.
* ``incremental``: the same command again on the unchanged copy.
* ``cache_hit``: N1's warm path: the key (sha256 of the sources, the lock file and ``dotnet --version``) is computed
  and the built ``<key>/bin/Release/net10.0/*.dll`` is looked up; no build runs.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def cache_key(src: Path) -> str:
    parts = [
        p
        for p in sorted(src.rglob("*"), key=lambda q: q.relative_to(src).as_posix())
        if p.is_file()
        and "obj" not in p.relative_to(src).parts
        and "bin" not in p.relative_to(src).parts
    ]
    blob = b"".join(p.relative_to(src).as_posix().encode() + b"\0" + p.read_bytes() for p in parts)
    return C.sha256_bytes(blob + C.sdk_version().encode())[:16]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("src")
    ap.add_argument("--scratch")
    ap.add_argument("--out")
    args = ap.parse_args()
    src = Path(args.src).resolve()
    csprojs = sorted(src.glob("*.csproj"))
    if not csprojs:
        sys.exit(f"RC5: no *.csproj in {src}")
    scratch = C.scratch_dir(args.scratch, "rc5")
    work = scratch / "helper-copy"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns("bin", "obj"))
    cmd = [
        C.dotnet_exe(),
        "build",
        "-c",
        "Release",
        "--disable-build-servers",
        "-nodeReuse:false",
        "-v",
        "q",
    ]
    if (work / "packages.lock.json").exists():
        cmd.append("--locked-mode")
    rc1, out1, err1, cold = C.run(cmd, cwd=work)
    rc2, _o2, _e2, inc = C.run(cmd, cwd=work)
    dlls = sorted((work / "bin" / "Release").glob("*/*.dll"))
    t0 = time.perf_counter()
    key = cache_key(src)
    hit = (scratch / key).exists() or bool(dlls)
    lookup = time.perf_counter() - t0
    result = {
        "rc": "RC5",
        "helper": csprojs[0].name,
        "sdk": C.sdk_version(),
        "locked_mode": "--locked-mode" in cmd,
        "cold_seconds": round(cold, 2),
        "cold_exit": rc1,
        "incremental_seconds": round(inc, 2),
        "incremental_exit": rc2,
        "cache_key": key,
        "cache_hit": hit,
        "cache_hit_lookup_seconds": round(lookup, 4),
        "built_dlls": [d.name for d in dlls],
        "predicted": {"cold": "20-60 s", "warm": "< 1 s"},
        "within_prediction": {"cold": 20 <= cold <= 60, "warm": lookup < 1.0},
    }
    if rc1 != 0:
        result["build_error_tail"] = (out1 + err1)[-1500:]
    C.emit(result, args.out)
    return 0 if rc1 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
