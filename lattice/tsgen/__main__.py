"""python -m lattice.tsgen SOURCE --profile MODULE:ATTR --out DIR"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from lattice.tsgen import compile_source_to_ts
from lattice.tsgen.emit import TsGenRefusal


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m lattice.tsgen")
    p.add_argument("source", type=Path)
    p.add_argument(
        "--profile", required=True, help="module:attr (a Profile or a function returning one)"
    )
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    module, _, attr = args.profile.partition(":")
    profile = getattr(importlib.import_module(module), attr)
    if callable(profile):
        profile = profile()
    try:
        result, files = compile_source_to_ts(args.source.read_text(), profile)
    except TsGenRefusal as err:
        print(f"{args.source}:{err}", file=sys.stderr)
        return 1
    if files is None:
        for d in result.diagnostics:
            print(f"{args.source}:{d.render()}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (args.out / name).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
