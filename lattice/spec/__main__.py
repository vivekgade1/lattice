"""``python -m lattice.spec check``: an app against its spec (lattice.spec.check).

    python -m lattice.spec check --spec mod:SPEC --app mod:factory --db mod:build_db \\
        [--app-dir DIR --package PKG] [--layers ABCD] [--login mod:fn] \\
        [--login-path /login] [--mint-token mod:fn] [--json]

``--spec`` names a Spec (or a function returning one), ``--app`` an app factory
``(db_path) -> ASGI app``, ``--db`` a database factory (``(path)`` or ``(variant,
path)``). Exit code: 0 pass, 1 fail, 2 the check could not run.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from typing import Any

from lattice.spec.check import CheckError, check
from lattice.spec.model import Spec


def load(ref: str) -> Any:
    """The object ``module:attr`` (``attr`` may be dotted)."""
    module, sep, attr = ref.partition(":")
    if not sep or not module or not attr:
        raise CheckError(f"{ref!r}: expected module:attribute")
    obj: Any = importlib.import_module(module)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lattice.spec")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check an app against its spec (layers A-D)")
    c.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")
    c.add_argument("--app", required=True, help="module:factory, (db_path) -> app")
    c.add_argument("--db", required=True, help="module:build_db, (path) or (variant, path)")
    c.add_argument("--app-dir", help="the app package's directory (layer A)")
    c.add_argument("--package", help="the app package, dotted (layer A)")
    c.add_argument("--layers", default="ABCD", help="letters of ABCD (default all)")
    c.add_argument("--login", help="module:fn, (client, handle) -> bearer token")
    c.add_argument("--login-path", help="a form login route taking Spec.users' credentials")
    c.add_argument("--mint-token", help="module:fn, (user key) -> bearer token")
    c.add_argument("--natives", help="module:NATIVES, the spec's natives {name: function}")
    c.add_argument("--baseline", help="module:BASELINE, the app's accepted findings")
    c.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        spec = load(args.spec)
        if not isinstance(spec, Spec) and callable(spec):
            spec = spec()
        if not isinstance(spec, Spec):
            raise CheckError(f"{args.spec} is not a Spec")
        report = check(
            spec,
            load(args.app),
            load(args.db),
            layers=args.layers,
            app_dir=args.app_dir,
            package=args.package,
            login=load(args.login) if args.login else None,
            login_path=args.login_path,
            mint_token=load(args.mint_token) if args.mint_token else None,
            natives=load(args.natives) if args.natives else None,
            baseline=load(args.baseline) if args.baseline else (),
        )
    except Exception as err:  # noqa: BLE001 -- any failure to run the check is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    print(report.to_json() if args.json else report.render())
    return 0 if report.verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
