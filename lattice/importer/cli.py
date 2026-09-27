"""``python -m lattice.importer``: import a FastAPI + SQLModel repository into Lattice
(spec 2026-09-27 importer §3.5, plan nodes I4 and I5).

::

    python -m lattice.importer <repo_root> --package P --app-factory mod:fn --out DIR
        [--no-llm] [--method M1..M6] [--model M] [--strong-model M] [--effort E]
        [--strong-effort E] [--max-rounds N] [--max-cost-usd X] [--max-route-cost-usd X]
        [--max-repo-rounds N] [--only KEY,...] [--corpus PATH | --no-corpus]
        [--notes-cache DIR] [--artefacts DIR]

``--app-factory mod:fn`` names a callable building the original app on a SQLite file
(``fn(db_path) -> app``). ``--no-llm`` runs the deterministic lifters only (no API key);
otherwise every route they do not verify goes through the close-the-gap loop
(:mod:`lattice.importer.llm_lift`). Budgets: ``--max-rounds`` and
``--max-route-cost-usd`` cap each route, ``--max-cost-usd`` and ``--max-repo-rounds``
the whole repo; when a cap is reached the remaining routes are islands with reason
"budget" and the output is still complete.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lattice.importer.context import METHODS
from lattice.importer.lift import lift_repo
from lattice.importer.llm_lift import DEFAULT_CORPUS, CorpusLog, LoopConfig, close_gap, no_llm
from lattice.importer.report import load_report, write_import

__all__ = ["main", "parser", "resolve_factory"]

DEFAULT_METHOD = "M2"  # until the method experiment (I6b) picks the winner
DEFAULT_NOTES_CACHE = DEFAULT_CORPUS.parent / "intent-cache"


def resolve_factory(spec: str) -> Callable[[Path], Any]:
    module, sep, name = spec.partition(":")
    if not sep or not module or not name:
        raise SystemExit(f"--app-factory must be module:function, got {spec!r}")
    obj: Any = importlib.import_module(module)
    for part in name.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise SystemExit(f"--app-factory {spec!r} is not callable")
    return obj


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.importer", description=__doc__.split("\n\n")[0]
    )
    p.add_argument("repo_root", type=Path)
    p.add_argument("--package", required=True, help="the app package, dotted (e.g. app)")
    p.add_argument("--app-factory", required=True, help="module:function(db_path) -> app")
    p.add_argument("--out", type=Path, default=Path("lattice_import"))
    p.add_argument("--no-llm", action="store_true", help="deterministic lifters only")
    p.add_argument("--method", choices=sorted(METHODS), default=DEFAULT_METHOD)
    p.add_argument("--model", default="claude-opus-5")
    p.add_argument("--strong-model", default=None, help="R2/R3 model (default: --model)")
    p.add_argument("--effort", default="high")
    p.add_argument("--strong-effort", default="max")
    p.add_argument("--max-tokens", type=int, default=16000)
    p.add_argument("--max-rounds", type=int, default=None, help="LLM rounds per route")
    p.add_argument("--max-route-cost-usd", type=float, default=None, help="USD per route")
    p.add_argument("--max-repo-rounds", type=int, default=None, help="LLM rounds per repo")
    p.add_argument("--max-cost-usd", type=float, default=None, help="USD per repo")
    p.add_argument("--only", default=None, help="comma-separated route keys for the loop")
    p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    p.add_argument("--no-corpus", action="store_true")
    p.add_argument("--notes-cache", type=Path, default=DEFAULT_NOTES_CACHE)
    p.add_argument("--artefacts", type=Path, default=None)
    p.add_argument("--hypothesis-bodies", type=int, default=5)
    return p


def main(argv: Sequence[str] | None = None, *, generators: Any = None) -> int:
    """Run the import. ``generators`` (tests): ``(generator, strong_generator,
    notes_generator)`` used instead of the Claude API."""
    args = parser().parse_args(argv)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    root = args.repo_root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    factory = resolve_factory(args.app_factory)
    result = lift_repo(root, args.package, factory, hypothesis_bodies=args.hypothesis_bodies)
    corpus = None
    if not args.no_corpus:
        corpus = CorpusLog(args.corpus, repo=str(root), package=args.package)
    if args.no_llm:
        close = no_llm(result, corpus=corpus)
    else:
        gen, strong, notes_gen = generators or _claude(args)
        intent = None
        if "S3" in METHODS[args.method]:
            from lattice.importer.intent import IntentNotes

            intent = IntentNotes(root, args.package, notes_gen, cache_dir=args.notes_cache,
                                 model=args.model)  # fmt: skip
        config = LoopConfig(
            method=args.method,
            model=args.model,
            strong_model=args.strong_model,
            max_route_rounds=args.max_rounds,
            max_route_cost_usd=args.max_route_cost_usd,
            max_repo_rounds=args.max_repo_rounds,
            max_repo_cost_usd=args.max_cost_usd,
        )
        only = args.only.split(",") if args.only else None
        close = close_gap(result, gen, config=config, strong_generator=strong, intent=intent,
                          corpus=corpus, artefacts=args.artefacts, only=only)  # fmt: skip
    out = write_import(close, args.out, repo_root=root, package=args.package,
                       app_factory=args.app_factory)  # fmt: skip
    cov = load_report(out)["coverage"]
    print(
        f"{out}: {cov['routes']} routes, {cov['verified']} verified "
        f"(R0 {cov['R0']}, R1 {cov['R1']}, R2 {cov['R2']}, R3 {cov['R3']}), "
        f"{cov['R4']} islands, {cov['needs-llm']} need the LLM; cost ${close.cost_usd:.4f}"
    )
    return 0


def _claude(args: argparse.Namespace) -> tuple[Any, Any, Any]:
    from lattice.llm.client import ClaudeGenerator

    gen = ClaudeGenerator(model=args.model, effort=args.effort, max_tokens=args.max_tokens)
    strong = ClaudeGenerator(
        model=args.strong_model or args.model, effort=args.strong_effort,
        max_tokens=args.max_tokens,
    )  # fmt: skip
    return gen, strong, gen
