"""``python -m lattice.modules`` (``lattice module``): install, list, inspect and remove a
project's modules (:mod:`lattice.modules`).

    lattice module install SOURCE [SOURCE ...] [--project DIR] [--index DIR|URL]
                           [--allow-remote] [--yes]
    lattice module list [--project DIR] [--json]
    lattice module info NAME [--project DIR] [--json]
    lattice module uninstall NAME [--project DIR]

SOURCE: a module directory, a ``file://`` URL, a git source pinned to a full commit sha
(``git+file:///abs/path@<sha>``; ``git+https://host/path@<sha>`` with ``--allow-remote``), or
(with ``--index``) a module name looked up as ``<index>/<NAME>.toml``; the index is a directory
or a pinned git source. ``install`` prints each manifest and installs only with ``--yes`` or
an interactive ``y``; several sources are installed together or not at all.
Exit codes: 0 done (``list``: every module ok), 1 ``list``/``info`` found a module that is
not ok, 2 refused or could not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from lattice.modules import (
    LOCK,
    ModuleError,
    Refused,
    install_many,
    module_dir,
    read_lock,
    uninstall,
    verify,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lattice module", description="a project's modules")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("install", help="install a module (prints its manifest, asks first)")
    i.add_argument(
        "source",
        nargs="+",
        help="a module directory, a file:// URL, a pinned git+file:// or git+https:// "
        "source, or a name (--index)",
    )
    i.add_argument(
        "--index",
        metavar="DIR|URL",
        help="a directory (or a pinned git+file:// / git+https:// source) of <name>.toml "
        "index entries",
    )
    i.add_argument(
        "--allow-remote", action="store_true", help="allow git+https:// sources (network)"
    )
    i.add_argument("--yes", action="store_true", help="install without asking")
    for name, text in (
        ("list", "the installed modules, each re-hashed"),
        ("info", "one module's manifest and lock entry"),
        ("uninstall", "remove a module and its lock entry"),
    ):
        s = sub.add_parser(name, help=text)
        if name != "list":
            s.add_argument("name")
        if name != "uninstall":
            s.add_argument("--json", action="store_true")
    for s in sub.choices.values():
        s.add_argument("--project", default=".", help="the project directory (default: cwd)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    project = Path(args.project).resolve()
    try:
        if args.command == "install":
            install_many(
                project,
                args.source,
                index=args.index,
                yes=args.yes,
                allow_remote=args.allow_remote,
            )
            return 0
        if args.command == "uninstall":
            e = uninstall(project, args.name)
            print(
                f"uninstalled {e.name} {e.version} (removed {module_dir(project, e.name)} "
                f"and its {LOCK} entry)"
            )
            return 0
        states = verify(project)
        if args.command == "list":
            if args.json:
                print(json.dumps({"modules": [s.to_dict() for s in states]}))
            elif not states:
                print(f"no modules installed in {project} (no {LOCK})")
            else:
                w = max(len(s.name) for s in states)
                for s in states:
                    e = s.entry
                    print(
                        f"{e.name:<{w}}  {e.version:<10} {e.kind:<7} {s.status:<8} "
                        f"{e.sha256[:12]}  {e.source}"
                    )
                    if s.status != "ok":
                        print(f"{'':<{w}}  {s.detail}")
            return 0 if all(s.status == "ok" for s in states) else 1
        found = [s for s in states if s.name == args.name]
        if not found:
            names = ", ".join(e.name for e in read_lock(project)) or "none"
            print(
                f"error: module {args.name!r} is not installed (installed: {names})",
                file=sys.stderr,
            )
            return 2
        s = found[0]
        if args.json:
            print(
                json.dumps(
                    {
                        **s.to_dict(),
                        "kind": s.entry.kind,
                        "source": s.entry.source,
                        **({"commit": s.entry.commit} if s.entry.commit is not None else {}),
                        "actual": s.actual,
                        "detail": s.detail,
                    }
                )
            )
        else:
            print(f"{s.entry.name} {s.entry.version} ({s.entry.kind}): {s.status}")
            print(f"  sha256 (locked): {s.entry.sha256}")
            if s.actual and s.actual != s.entry.sha256:
                print(f"  sha256 (now):    {s.actual}")
            print(f"  source:          {s.entry.source}")
            if s.entry.commit is not None:
                print(f"  commit:          {s.entry.commit}")
            print(f"  directory:       {module_dir(project, s.name)}")
            if s.detail:
                print(f"  {s.detail}")
            if s.manifest is not None:
                print("\n" + s.manifest.text.rstrip())
        return 0 if s.status == "ok" else 1
    except Refused as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2
    except ModuleError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
