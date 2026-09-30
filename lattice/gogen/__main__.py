"""`python -m lattice.gogen build <file.lattice.py> --profile <module:attr> --out <dir>`:
build a Lattice API program into one Go binary and print its path (plan 5a Task 12).

The profile is `module:attr`, a Profile or a function returning one (e.g.
`bench.api_datasets:api_profile`). A program rejected for Go prints its diagnostics
and exits 1; a failed `go build` prints its errors and exits 1.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from lattice.gogen.build import GoBuildError, build_go_app
from lattice.profile import Profile


def load_profile(spec: str) -> Profile:
    module, sep, attr = spec.partition(":")
    if not sep or not module or not attr:
        raise SystemExit(f"--profile must be module:attr, not {spec!r}")
    value = getattr(importlib.import_module(module), attr)
    if callable(value) and not isinstance(value, Profile):
        value = value()
    if not isinstance(value, Profile):
        raise SystemExit(f"{spec} is not a Profile")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lattice.gogen")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build a Lattice API program into a Go binary")
    build.add_argument("source", type=Path, help="the Lattice program (.lattice.py)")
    build.add_argument("--profile", required=True, help="module:attr of its Profile")
    build.add_argument("--out", type=Path, required=True, help="the build directory")
    args = parser.parse_args(argv)
    profile = load_profile(args.profile)
    try:
        built = build_go_app(args.source.read_text(), profile, args.out)
    except GoBuildError as err:
        print(err, file=sys.stderr)
        return 1
    print(built.binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
