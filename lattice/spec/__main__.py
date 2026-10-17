"""``python -m lattice.spec check``: an app against its spec (lattice.spec.check).

    python -m lattice.spec check --spec mod:SPEC --app mod:factory --db mod:build_db \\
        [--app-dir DIR --package PKG | --go-src DIR] [--layers ABCDX] [--login mod:fn] \\
        [--login-path /login] [--mint-token mod:fn] [--json] \\
        [--x-k 12] [--x-repeats 3] [--x-budget 60] [--runtime mod:RUNTIME | process | go] \\
        [--services mod:SERVICES] [--database sqlite|postgres|mysql|clickhouse|ENGINE] \\
        [--database-url URL] [--engine-file FILE ...] [--modules DIR] \\
        [--only SELECTOR ...] [--only-file PATH] [--trace PATH] [--await-cap SECONDS]

``--spec`` names a Spec (or a function returning one), ``--app`` an app factory
``(db_path) -> ASGI app``, ``--db`` a database factory (``(path)`` or ``(variant,
path)``). ``--runtime`` serves the app through an ``AppRuntime`` (or a function
returning one), e.g. a ``lattice.spec.process.process_runtime(...)`` for a factory
answering a ``ProcessApp`` (a compiled binary over the SQLite file); ``process`` is
``process_runtime()`` itself, ``go`` is ``process_runtime(framework="go")``, and a
factory annotated ``-> ProcessApp`` selects it with no flag. ``--go-src`` is a Go (Gin +
GORM) app's module root: layer A reads it with ``lattice.gocheck`` (a runtime whose
framework is ``go`` also takes ``--app-dir`` as the root). ``--services`` names the
app's background services, ``{name: (db_path) -> ServiceCmd}``
(``lattice.spec.services``; or a function returning it): how the spec's ``Await`` steps
start them. ``--database`` (default ``sqlite``) runs layers C, D and X on a server
database: ``--db``, ``--app`` and the services then get a database's canonical URL
instead of a file path (``lattice.spec.db``), on a throwaway Docker container, or on the
server ``--database-url`` (or ``$LATTICE_<DIALECT>_URL``) names, where only databases
the check creates are touched (``lattice.spec.dbserver``). ``--engine-file FILE``
(repeatable) registers a project's engine descriptor (``lattice.spec.engines``; e.g.
``docs/examples/engines/mariadb.toml``) before ``--database`` is read, so its name is a
``--database`` value; ``--modules DIR`` loads the modules installed in project ``DIR``
(``lattice module install``: ``DIR/lattice.modules.lock``), each tree re-hashed first, and
refuses (exit 2) a module whose tree changed since it was installed (``ask --verify`` and
``draft --impact`` take the same two flags). ``--only`` (repeatable, one
or more selectors each) and ``--only-file`` (a JSON array of selectors, or one per line)
restrict layers D and X to the scenarios the selectors name: ``scenario:NAME``, a bare
``NAME``, a glob on names (``auth_*``), or ``rule:ID`` (the scenarios the trace links to
the rule; the trace is ``--trace``, default ``<spec module>.trace.json`` next to the spec
module) -- ``lattice.spec.selection``; a selector matching no scenario is exit 2. The
report (text and ``--json``) carries ``timings``: seconds per layer, and per scenario of
layer D where they went (``lattice.spec.check.ScenarioClock``). ``--await-cap SECONDS``
caps every ``Await``'s ``within_s`` (opt-in, for in-loop gates: a scenario waiting on a
service that is not built yet fails after SECONDS instead of the spec's wait). Exit code:
0 pass, 1 fail, 2 the check could not run.

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
import contextlib
import importlib
import sys
from collections.abc import Iterator, Sequence
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
    c.add_argument(
        "--services",
        help="module:SERVICES, the app's background services {name: (db_path) -> "
        "ServiceCmd} (or a function returning it), started by the spec's Await steps",
    )
    from lattice.spec.engines import database_names

    c.add_argument(
        "--database",
        default="sqlite",
        choices=database_names(),
        help="the database the app runs on (default sqlite; the others: a throwaway Docker "
        "container, or --database-url)",
    )
    c.add_argument(
        "--engine-file",
        action="append",
        default=[],
        metavar="FILE",
        help="a project's engine descriptor (TOML, lattice.spec.engines), registered before "
        "--database is read; repeatable",
    )
    c.add_argument(
        "--modules",
        metavar="DIR",
        help="load the modules installed in project DIR (DIR/lattice.modules.lock), each "
        "re-hashed; a changed module is refused",
    )
    c.add_argument(
        "--database-url",
        help="an existing server to create the check's databases on (an account that may "
        "create databases), instead of a container; default $LATTICE_<DIALECT>_URL",
    )
    c.add_argument(
        "--only",
        nargs="+",
        action="extend",
        metavar="SELECTOR",
        help="run layers D and X on these scenarios only: scenario:NAME, NAME, a glob "
        "(auth_*), or rule:ID (scenarios the trace links to the rule); repeatable",
    )
    c.add_argument(
        "--only-file",
        metavar="PATH",
        help="selectors as for --only: a JSON array of strings, or one per line",
    )
    c.add_argument(
        "--trace",
        metavar="PATH",
        help="the project trace rule:ID selectors read (default: <spec module>.trace.json "
        "next to the spec module)",
    )
    c.add_argument(
        "--await-cap",
        type=float,
        metavar="SECONDS",
        help="every Await step waits at most SECONDS (its within_s capped; a note in the "
        "report); an effect that does not show within the cap still fails the scenario",
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


def _only(args: argparse.Namespace) -> tuple[list[str] | None, Any]:
    """The ``--only`` / ``--only-file`` selectors (None: neither given) and the trace
    ``rule:`` selectors read (``--trace``, else the one next to the spec module)."""
    from lattice.spec.selection import find_trace, read_only_file

    if args.only is None and args.only_file is None:
        return None, None
    only = list(args.only or [])
    if args.only_file is not None:
        only += read_only_file(args.only_file)
    if args.trace is not None:
        return only, args.trace
    module = sys.modules.get(args.spec.partition(":")[0])
    return only, find_trace(getattr(module, "__file__", None))


def _services(ref: str | None) -> Any:
    """The ``--services``: None, or ``module:attr`` (a mapping, or a function answering
    one)."""
    from collections.abc import Mapping

    if ref is None:
        return None
    services = load(ref)
    if not isinstance(services, Mapping) and callable(services):
        services = services()
    if not isinstance(services, Mapping):
        raise CheckError(f"{ref} is not a mapping of service name -> (db_path) -> ServiceCmd")
    return services


# the spec-authoring tools (each module has its own argument parser)
AUTHORING = {
    "draft": "lattice.spec.draft",
    "lint": "lattice.spec.lint",
    "probe": "lattice.spec.probe",
    "review": "lattice.spec.review",
    "ask": "lattice.spec.ask",
    "implementation": "lattice.spec.implementation",
    "impact": "lattice.spec.impact_report",
}


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in AUTHORING:
        import importlib

        return importlib.import_module(AUTHORING[argv[0]]).main(argv[1:])
    if argv and argv[0] == "check":
        problem = _preload_engines(argv[1:])
        if problem is not None:
            print(f"error: {problem}", file=sys.stderr)
            return 2
    args = _parser().parse_args(argv)
    if args.command == "fidelity":
        return fidelity(args)
    with _cleanup_on_sigterm():
        return _check_main(args)


def _flag_values(argv: Sequence[str], flag: str) -> list[str]:
    """The values of ``flag`` in ``argv`` (``--flag V`` and ``--flag=V``), in order."""
    out: list[str] = []
    it = iter(argv)
    for a in it:
        if a == flag:
            value = next(it, None)
            if value is not None:
                out.append(value)
        elif a.startswith(flag + "="):
            out.append(a.split("=", 1)[1])
    return out


def _preload_engines(argv: Sequence[str]) -> str | None:
    """Register the engines of ``--modules DIR`` and ``--engine-file FILE`` (before the
    parser is built, so their names are ``--database`` choices); an error message, or
    None."""
    from lattice.spec.engines import EngineError, load_engine_file

    try:
        for project in _flag_values(argv, "--modules"):
            from lattice.modules import ModuleError, register_modules

            try:
                register_modules(project, strict=True)
            except ModuleError as err:
                return f"ModuleError: {err}"
        for path in _flag_values(argv, "--engine-file"):
            load_engine_file(path, "project")
    except EngineError as err:
        return f"EngineError: {err}"
    return None


@contextlib.contextmanager
def _cleanup_on_sigterm() -> Iterator[None]:
    """While the check runs, SIGTERM exits through SystemExit, so ``finally`` blocks and
    the ``atexit`` guards stop the app processes and services and remove the database
    containers (Python's default SIGTERM ends the process without them: a stopped gate
    left its apps and containers behind). The previous handler is restored after."""
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def on_term(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    prev = signal.signal(signal.SIGTERM, on_term)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, prev)


def _check_main(args: argparse.Namespace) -> int:
    try:
        spec = load(args.spec)
        if not isinstance(spec, Spec) and callable(spec):
            spec = spec()
        if not isinstance(spec, Spec):
            raise CheckError(f"{args.spec} is not a Spec")
        only, trace = _only(args)
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
            services=_services(args.services),
            database=args.database,
            database_url=args.database_url,
            only=only,
            trace=trace,
            await_cap=args.await_cap,
        )
    except Exception as err:  # noqa: BLE001 -- any failure to run the check is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    print(report.to_json() if args.json else report.render())
    return 0 if report.verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
