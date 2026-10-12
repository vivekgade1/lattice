"""``python -m lattice.security`` (``lattice attack``): plan, run and report a spec-driven
security attack against the OWNER'S OWN app, which this module starts itself on a loopback
throwaway database.

    lattice attack plan   --project DIR [--scope ...] [--classes ...] [--json]
    lattice attack run    --project DIR [--scope ...] [--classes ...] [--json] [--out DIR]
    lattice attack report --project DIR [...]            # alias of run (human report)

Scope: --scope system (default) | --scope rules R12,R28 | --scope endpoints "GET /v1/habits/{id}".
Classes: --classes authz,auth,bounds,mass,state,leak (default: all).

FOR THE OWNER'S OWN SYSTEMS ONLY. There is no --url and no host argument: the only target is
the app this module starts on 127.0.0.1 (or in-process). Exit codes: 0 no confirmed findings,
1 confirmed findings, 2 cannot run / target refused.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from lattice.security import BANNER
from lattice.security.plan import generate, scope_endpoints
from lattice.security.report import to_json, to_text
from lattice.security.schema import CLASSES
from lattice.security.target import UnsafeTargetError

_URL_FLAGS = ("--url", "--host", "--target", "--base-url")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lattice attack",
        description=f"{BANNER}\nspec-driven security attacks against the app this module "
        "starts itself on a loopback throwaway database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("plan", "list the attacks that would be sent (no traffic)"),
        ("run", "run the attacks and judge the findings"),
        ("report", "run and print a human report (alias of run)"),
    ):
        s = sub.add_parser(name, help=help_text, description=f"{BANNER}\n{help_text}")
        s.add_argument("--project", required=True, metavar="DIR", help="the project directory")
        s.add_argument(
            "--scope",
            default="system",
            help='system | rules R12,R28 | endpoints "GET /v1/habits/{id}"',
        )
        s.add_argument("--classes", default="", help=f"comma list of {', '.join(CLASSES)}")
        s.add_argument("--json", action="store_true", help="JSON instead of a text table")
        s.add_argument(
            "--out", metavar="DIR", help="write report + logs here (default: a temp dir)"
        )
        s.add_argument("--workers", type=int, default=4, help="bounded attack pool size")
    return p


def _classes(value: str) -> tuple[str, ...]:
    if not value:
        return CLASSES
    chosen = tuple(c.strip() for c in value.split(",") if c.strip())
    bad = [c for c in chosen if c not in CLASSES]
    if bad:
        raise SystemExit(
            f"lattice attack: unknown class(es) {', '.join(bad)}; known: {', '.join(CLASSES)}"
        )
    return chosen


def _scope_parts(scope: str) -> tuple[str, str | None]:
    scope = scope.strip()
    if scope == "system" or not scope:
        return "system", None
    for kind in ("rules", "endpoints"):
        if scope.startswith(kind):
            return kind, scope[len(kind) :].strip() or None
    return "endpoints", scope  # a bare "GET /path" is an endpoints scope


def load_spec(project: Path):
    """The project's ``Spec`` (puts the project on ``sys.path`` as ``lattice check`` does).
    The ``spec`` module is loaded fresh from THIS project, never a cached one another project
    (or test) left in ``sys.modules``."""
    import sys

    from lattice.cli import _fill_check_args
    from lattice.spec.__main__ import load

    filled = _fill_check_args(project.resolve(), [])
    if isinstance(filled, str):
        raise UnsafeTargetError(filled.replace("lattice check:", "lattice attack:"))
    sys.modules.pop("spec", None)  # force a fresh import from this project's directory
    spec = load("spec:SPEC")
    if callable(spec) and not hasattr(spec, "scenarios"):
        spec = spec()
    return spec


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    for flag in _URL_FLAGS:
        if any(a == flag or a.startswith(flag + "=") for a in args_list):
            print(
                f"lattice attack: {flag} is refused. This module attacks only the app it starts "
                "itself on a loopback throwaway database, never a URL or host you pass it.",
                file=sys.stderr,
            )
            return 2
    args = _parser().parse_args(args_list)
    project = Path(args.project)
    if not project.is_dir():
        print(f"lattice attack: no project directory {project}", file=sys.stderr)
        return 2
    classes = _classes(args.classes)
    scope_kind, scope_value = _scope_parts(args.scope)
    try:
        spec = load_spec(project)
    except Exception as err:  # noqa: BLE001
        print(f"lattice attack: cannot load the spec of {project}: {err}", file=sys.stderr)
        return 2
    spec = scope_endpoints(spec, scope_kind, scope_value)
    attacks = generate(spec, classes)

    if args.command == "plan":
        if args.json:
            import json

            print(
                json.dumps(
                    [
                        {
                            "id": a.id,
                            "class": a.cls,
                            "title": a.title,
                            "endpoint": a.endpoint,
                            "rule": a.rule,
                            "expected": a.expected,
                        }
                        for a in attacks
                    ],
                    indent=2,
                )
            )
        else:
            print(BANNER)
            print(f"{len(attacks)} attacks planned ({args.scope!r}); {', '.join(classes)}:")
            for a in attacks:
                print(f"  {a.cls:<6} {a.id}  ->  {a.title}")
        return 0

    out_dir = args.out or tempfile.mkdtemp(prefix="lattice-sec-out-")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    from lattice.security.run import make_seed_hook
    from lattice.security.run import run as run_attacks
    from lattice.security.target import start_target

    seed_hook = make_seed_hook(spec)
    try:
        report = run_attacks(
            lambda: start_target(project, seed_hook=seed_hook),
            attacks,
            scope=args.scope,
            max_workers=args.workers,
            out_dir=out_dir,
            notes=[f"spec scope: {scope_kind}" + (f" {scope_value}" if scope_value else "")],
        )
    except UnsafeTargetError as err:
        print(f"lattice attack: target refused: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001
        print(f"lattice attack: could not run: {type(err).__name__}: {err}", file=sys.stderr)
        return 2

    text, js = to_text(report), to_json(report)
    (Path(out_dir) / "report.json").write_text(js, encoding="utf-8")
    (Path(out_dir) / "report.txt").write_text(text, encoding="utf-8")
    print(js if args.json else text)
    print(f"\n(reports written under {out_dir})", file=sys.stderr)
    return 1 if report.confirmed else 0


if __name__ == "__main__":
    sys.exit(main())
