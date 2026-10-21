"""RC1: does a Roslyn compilation of a target project with only the two reference packs bind every
project-internal call and leave EF Core / Pomelo / FluentValidation types as named error types?
(docs/results/2026-10-18-dotnet-prereg.md, 5.1; expected: yes, 0 project symbols unbound.)

    python dogfood/dotnet_recon/rc1_bind.py <project-dir> [--scratch DIR] [--out FILE]

The helper (``rc1_src/``: a ``net10.0`` console program, one ``PackageReference`` to
``Microsoft.CodeAnalysis.CSharp`` 5.9.0) is copied to the scratch directory and built there; the repository's
``rc1_src/`` and the target are never written (no ``obj/``, no ``bin/``). The target is never restored or built.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import rc2_implicit_usings as RC2  # noqa: E402

SRC = C.HERE / "rc1_src"


def packs() -> tuple[Path, Path]:
    root = C.dotnet_root() / "packs"
    aspnet = sorted((root / "Microsoft.AspNetCore.App.Ref").glob("*/ref/net10.0"))
    netcore = sorted((root / "Microsoft.NETCore.App.Ref").glob("*/ref/net10.0"))
    if not aspnet or not netcore:
        sys.exit("RC1: the reference packs are not installed under " + str(root / "packs"))
    return aspnet[-1], netcore[-1]


def build_helper(scratch: Path) -> tuple[Path, float]:
    key = C.sha256_bytes(b"".join(p.read_bytes() for p in sorted(SRC.rglob("*")) if p.is_file()))[
        :12
    ]
    work = scratch / f"rc1-{key}"
    dll = work / "bin" / "Release" / "net10.0" / "Rc1Bind.dll"
    if dll.exists():
        return dll, 0.0
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(SRC, work, ignore=shutil.ignore_patterns("bin", "obj"))
    rc, out, err, secs = C.run(
        [
            C.dotnet_exe(),
            "build",
            "-c",
            "Release",
            "--disable-build-servers",
            "-nodeReuse:false",
            "-v",
            "q",
        ],
        cwd=work,
    )
    if rc != 0 or not dll.exists():
        sys.exit("RC1: helper build failed (NOT RUN):\n" + (out + err)[-2000:])
    return dll, secs


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root", help="the target project directory (read only)")
    ap.add_argument("--scratch")
    ap.add_argument("--out")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    csprojs = sorted(root.glob("*.csproj"))
    usings = RC2.implicit_usings(csprojs[0]) if csprojs else RC2.implicit_usings(None, web=True)
    scratch = C.scratch_dir(args.scratch, "rc1")
    dll, build_secs = build_helper(scratch)
    aspnet, netcore = packs()
    rc, out, err, secs = C.run(
        [C.dotnet_exe(), str(dll), str(root), str(aspnet), str(netcore), ",".join(usings)]
    )
    if rc != 0:
        sys.exit("RC1: helper failed:\n" + err[-2000:])
    import json

    result = json.loads(out)
    result.update(
        {
            "sdk": C.sdk_version(),
            "reference_packs": [
                str(aspnet.parent.parent.name + "/" + aspnet.parent.parent.parent.name),
                str(netcore.parent.parent.name),
            ],
            "helper_build_seconds": round(build_secs, 2),
            "run_seconds": round(secs, 2),
            "verdict": "yes" if result["project_symbols_unbound"] == 0 else "NO",
        }
    )
    C.emit(result, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
