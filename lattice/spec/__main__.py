"""``python -m lattice.spec check``: an app against its spec (lattice.spec.check).

    python -m lattice.spec check --spec mod:SPEC --app mod:factory --db mod:build_db \\
        [--app-dir DIR --package PKG | --go-src DIR] [--layers ABCDX] [--login mod:fn] \\
        [--login-path /login] [--mint-token mod:fn] [--json] \\
        [--x-k 12] [--x-repeats 3] [--x-budget 60] [--runtime mod:RUNTIME | process | go]

``--spec`` names a Spec (or a function returning one), ``--app`` an app factory
``(db_path) -> ASGI app``, ``--db`` a database factory (``(path)`` or ``(variant,
path)``). ``--runtime`` serves the app through an ``AppRuntime`` (or a function
returning one), e.g. a ``lattice.spec.process.process_runtime(...)`` for a factory
answering a ``ProcessApp`` (a compiled binary over the SQLite file); ``process`` is
``process_runtime()`` itself, ``go`` is ``process_runtime(framework="go")``, and a
factory annotated ``-> ProcessApp`` selects it with no flag. ``--go-src`` is a Go (Gin +
GORM) app's module root: layer A reads it with ``lattice.gocheck`` (a runtime whose
framework is ``go`` also takes ``--app-dir`` as the root). Exit code: 0 pass, 1 fail, 2
the check could not run.

::

    python -m lattice.spec fidelity --spec DIR/candidate_spec.py --package P \
        [--root .] [--framework auto] [--app mod:fn] --scenarios mod:SCENARIOS \
        --seed mod [--json]

replays an app's recorded fidelity scenarios through the app's runtime (the front end
``--framework`` names, or detects: FastAPI, Django + DRF, Flask), checking each step's
answer and the spec's rule-oracle prediction (``lattice.spec.fidelity``). ``--spec``
is a spec module file (as ``python -m lattice.importer review DIR emit`` writes it) or
``module:SPEC``; ``--seed`` the app's seed module (``build_seed_db``,
``login_headers``, optional ``raw_credentials_headers`` and ``USER_IDS``); ``--app``
defaults to the nearest ``make_app`` of the package. Exit code: 0 pass, 1 fail, 2 the
suite could not run.
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
    c.add_argument(
        "--go-src", help="a Go (Gin + GORM) app's module root (layer A: lattice.gocheck)"
    )
    c.add_argument(
        "--layers",
        default="ABCD",
        help="letters of ABCDRX (default ABCD; R: the rule layer; X: concurrent races)",
    )
    c.add_argument("--x-k", type=int, default=12, help="layer X: copies per self-race")
    c.add_argument("--x-repeats", type=int, default=3, help="layer X: tries per race")
    c.add_argument("--x-budget", type=int, default=60, help="layer X: at most this many races")
    c.add_argument("--login", help="module:fn, (client, handle) -> bearer token")
    c.add_argument("--login-path", help="a form login route taking Spec.users' credentials")
    c.add_argument("--mint-token", help="module:fn, (user key) -> bearer token")
    c.add_argument("--natives", help="module:NATIVES, the spec's natives {name: function}")
    c.add_argument("--baseline", help="module:BASELINE, the app's accepted findings")
    c.add_argument(
        "--runtime",
        help="module:RUNTIME, the app's AppRuntime (or a function returning one); "
        "'process': lattice.spec.process.process_runtime(); 'go': the same with "
        "framework='go'",
    )
    c.add_argument("--json", action="store_true", help="print the report as JSON")
    f = sub.add_parser("fidelity", help="replay an app's fidelity scenarios through its runtime")
    f.add_argument("--spec", required=True, help="a spec module file, or module:SPEC")
    f.add_argument("--package", required=True, help="the app package, dotted")
    f.add_argument("--root", default=".", help="the directory the package is under")
    f.add_argument("--framework", default="auto", help="auto, fastapi, django or flask")
    f.add_argument("--app", default=None, help="module:factory (default: the nearest make_app)")
    f.add_argument("--scenarios", required=True, help="module:SCENARIOS")
    f.add_argument("--seed", required=True, help="the app's seed module")
    f.add_argument(
        "--import",
        dest="import_dir",
        default=None,
        help="the import directory: its report's framework routes are set apart",
    )
    f.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def _framework_routes(import_dir: str | None) -> list[tuple[str, str]]:
    """The framework routes an import's report lists (methods, path)."""
    import json
    from pathlib import Path

    if import_dir is None:
        return []
    rules = json.loads((Path(import_dir) / "report.json").read_text()).get("rules") or {}
    return [
        (e.get("method") or "*", e["path"])
        for e in rules.get("excluded_routes", ())
        if e.get("kind") == "framework"
    ]


def fidelity(args: argparse.Namespace) -> int:
    """The ``fidelity`` subcommand (see the module docstring)."""
    import json
    from pathlib import Path

    from lattice.importer.frontends import select
    from lattice.importer.rules import find_factory
    from lattice.spec.fidelity import run_fidelity

    try:
        root = Path(args.root).resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        if args.spec.endswith(".py"):
            from lattice.importer.emit_spec import load_spec

            spec = load_spec(args.spec).spec
        else:
            spec = load(args.spec)
            spec = spec() if not isinstance(spec, Spec) and callable(spec) else spec
        fe = select(None if args.framework == "auto" else args.framework, root, args.package)
        factory, _ = find_factory(args.package, args.app)
        seed = importlib.import_module(args.seed)
        report = run_fidelity(
            spec,
            load(args.scenarios),
            factory,
            seed.build_seed_db,
            seed.login_headers,
            runtime=fe.runtime(root, args.package),
            raw_headers=getattr(seed, "raw_credentials_headers", None),
            user_ids=getattr(seed, "USER_IDS", None),
            framework=_framework_routes(args.import_dir),
        )
    except Exception as err:  # noqa: BLE001 -- any failure to run the suite is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    data = report.to_dict()
    if args.json:
        print(json.dumps(data, default=str))
    else:
        print(f"fidelity: {'pass' if report.passed else 'fail'} -- {data['steps']} steps of "
              f"{data['scenarios']} scenarios; answers {data['answers_ok']}, rule oracle "
              f"{data['oracle_ok']}")  # fmt: skip
        for s in data["failed"]:
            print(f"  {s['scenario']} / step {s['step']}: {'; '.join(s['problems'])}")
    return 0 if report.passed else 1


def _runtime(ref: str | None) -> Any:
    """The ``--runtime``: None, ``process`` or ``module:attr`` (an AppRuntime or a
    function answering one)."""
    from lattice.importer.transport import AppRuntime

    if ref is None:
        return None
    if ref in ("process", "go"):
        from lattice.spec.process import process_runtime

        return process_runtime() if ref == "process" else process_runtime(framework="go")
    runtime = load(ref)
    if not isinstance(runtime, AppRuntime) and callable(runtime):
        runtime = runtime()
    if not isinstance(runtime, AppRuntime):
        raise CheckError(f"{ref} is not an AppRuntime")
    return runtime


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "fidelity":
        return fidelity(args)
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
            go_src=args.go_src,
            login=load(args.login) if args.login else None,
            login_path=args.login_path,
            mint_token=load(args.mint_token) if args.mint_token else None,
            natives=load(args.natives) if args.natives else None,
            baseline=load(args.baseline) if args.baseline else (),
            x_k=args.x_k,
            x_repeats=args.x_repeats,
            x_budget=args.x_budget,
            runtime=_runtime(args.runtime),
        )
    except Exception as err:  # noqa: BLE001 -- any failure to run the check is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    print(report.to_json() if args.json else report.render())
    return 0 if report.verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
