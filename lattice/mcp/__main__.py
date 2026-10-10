"""``python -m lattice.mcp serve --project DIR [--readonly] [--studio-url URL] [--model M]``
and ``python -m lattice.mcp guard --project DIR`` (the same as ``python -m
lattice.mcp.guard``)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "guard":
        from lattice.mcp import guard

        return guard.main(args[1:])
    ap = argparse.ArgumentParser(prog="python -m lattice.mcp", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the stdio MCP server")
    sv.add_argument("--project", type=Path, required=True)
    sv.add_argument("--readonly", action="store_true", help="hide change_propose and check_run")
    sv.add_argument("--model", default=None, help="the LLM (default: studio.toml, else sonnet)")
    sv.add_argument(
        "--studio-url",
        default=None,
        help="proxy the model, ask, change and check tools to a running Studio "
        "(http://127.0.0.1:8765) so change sets appear in its UI, where the owner accepts them",
    )
    sub.add_parser("guard", help="the PreToolUse hook (see lattice.mcp.guard)")
    ns = ap.parse_args(args)
    from lattice.mcp.server import serve

    return serve(ns.project, readonly=ns.readonly, model=ns.model, studio_url=ns.studio_url)


if __name__ == "__main__":
    sys.exit(main())
