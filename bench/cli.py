"""``python -m bench.cli run [...]`` — the benchmark driver.

``python -m bench.cli report <out_dir> [--merge other.jsonl ...]`` re-renders
``<out_dir>/report.md`` from existing outcomes (no model calls), so metrics
added to the report later can be recomputed for past runs.

Live model runs cost money: don't invoke this without ``--offline`` unless a
human has approved a live run against the real API (see the plan's global
constraints).
"""

from __future__ import annotations

import argparse
import datetime
import importlib
import re
import shutil
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bench.api_model import API_TIER, ApiTask
from bench.datasets import load_fixtures, write_fixtures
from bench.envfile import load_env_file
from bench.report import (
    append_outcome,
    evaluate_gate,
    read_outcomes,
    render_markdown,
    render_markdown_api,
    render_markdown_step2,
    summarize,
)
from bench.runners import (
    Arm,
    TaskOutcome,
    harness_error_outcome,
    prepare_workdir,
    run_api_baseline,
    run_api_lattice,
    run_baseline,
    run_baseline_repair,
    run_lattice,
)
from bench.tasks import Task, suite
from lattice.llm.client import ClaudeGenerator, Generator, ScriptedGenerator
from lattice.runtime import is_panic

_SEPARATOR_RE = re.compile(r"(?m)^=====$")
_KNOWN_ARMS = ("A", "P", "B", "C")
# FastAPI scaffolding copied into every arm A/P workdir of the api suite.
API_KIT_PATH = Path(__file__).resolve().parent / "api_kit" / "platform_kit.py"
API_DB_NAME = "app.db"


def _offline_generator(offline_dir: Path, arm: str, task_id: str) -> Generator:
    path = offline_dir / arm / f"{task_id}.txt"
    text = path.read_text()
    texts = [chunk.strip("\n") for chunk in _SEPARATOR_RE.split(text)]
    return ScriptedGenerator(texts)


def _default_out_dir() -> Path:
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("results") / timestamp


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bench.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the benchmark and write a report")
    run_parser.add_argument("--arms", default="A", help="comma-separated arms, e.g. A,P,B,C")
    run_parser.add_argument(
        "--suite", default="seed", choices=("seed", "deep", "all", "bench2", "api")
    )
    run_parser.add_argument("--tasks", default=None, help="comma-separated task ids to restrict to")
    run_parser.add_argument("--model", default="claude-opus-5")
    run_parser.add_argument(
        "--out", default=None, help="output directory (default results/<UTC timestamp>)"
    )
    run_parser.add_argument("--offline", default=None, help="directory of scripted responses")
    run_parser.add_argument("--max-repairs", type=int, default=2)
    run_parser.add_argument(
        "--no-hints",
        action="store_true",
        help="drop pitfall advice from both arms' system prompts (language reference stays)",
    )
    run_parser.add_argument(
        "--workers", type=int, default=1, help="tasks to run concurrently (default 1)"
    )
    run_parser.add_argument(
        "--lattice-backend",
        default="runtime",
        choices=("runtime", "generated"),
        help="api suite: serve Lattice arms (B/C) with the runtime or the generated FastAPI app",
    )

    report_parser = subparsers.add_parser(
        "report", help="re-render report.md from an existing outcomes.jsonl"
    )
    report_parser.add_argument("out_dir", help="run directory holding outcomes.jsonl")
    report_parser.add_argument(
        "--merge",
        nargs="+",
        default=[],
        metavar="OUTCOMES_JSONL",
        help="extra outcomes.jsonl files to merge into the report",
    )

    return parser


def _render_report(outcomes: Sequence[TaskOutcome], *, bench2: bool, api: bool = False) -> str:
    if api:
        return render_markdown_api(outcomes)
    if bench2:
        return render_markdown_step2(outcomes)
    summary = summarize(outcomes)
    gate = evaluate_gate(summary) if outcomes else None
    return render_markdown(summary, gate)


def _report(out_dir: Path, merge: Sequence[str]) -> int:
    outcomes = read_outcomes(out_dir / "outcomes.jsonl")
    for extra in merge:
        outcomes.extend(read_outcomes(Path(extra)))
    # bench2 outcomes carry a family; the other suites never do.
    bench2 = any(o.family for o in outcomes)
    api = any(o.tier == API_TIER for o in outcomes)
    report_path = out_dir / "report.md"
    report_path.write_text(_render_report(outcomes, bench2=bench2, api=api))
    print(str(report_path))
    return 0


def _run_one(
    arm: Arm,
    task: Task,
    *,
    work_root: Path,
    fixtures_dir: Path,
    fixtures_frames: dict,
    offline_dir: Path | None,
    model: str,
    max_repairs: int,
    hints: bool = True,
) -> TaskOutcome:
    workdir = prepare_workdir(task, work_root / arm, fixtures_dir)

    if offline_dir is not None:
        generator: Generator = _offline_generator(offline_dir, arm, task.id)
    else:
        generator = ClaudeGenerator(model=model)

    if arm == "A":
        return run_baseline(task, generator, workdir, fixtures_frames, hints=hints)
    if arm == "P":
        return run_baseline_repair(
            task, generator, workdir, fixtures_frames, max_repairs=max_repairs, hints=hints
        )
    if arm in ("B", "C"):
        return run_lattice(
            task,
            generator,
            workdir,
            fixtures_frames,
            arm=arm,
            max_repairs=max_repairs if arm == "B" else 0,
            hints=hints,
        )
    raise ValueError(f"unknown arm: {arm!r}")


class _ApiContext:
    """Per-run inputs of the api suite: the DB built once into the fixtures dir
    (each case run copies it), the api profile, and the platform kit path."""

    def __init__(self, fixtures_dir: Path) -> None:
        # Lazily imported: nothing outside an api run touches this module.
        api_datasets = importlib.import_module("bench.api_datasets")

        fixtures_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = fixtures_dir / API_DB_NAME
        self.db_path.unlink(missing_ok=True)
        api_datasets.build_db(self.db_path, seed=21)
        self.profile = api_datasets.api_profile()
        self.kit_path = API_KIT_PATH

    def db_builder(self, path: Path) -> None:
        shutil.copyfile(self.db_path, path)


def _prepare_api_workdir(root: Path, task_id: str) -> Path:
    workdir = root / task_id
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    return workdir


def _run_one_api(
    arm: Arm,
    task: ApiTask,
    *,
    work_root: Path,
    context: _ApiContext,
    offline_dir: Path | None,
    model: str,
    max_repairs: int,
    hints: bool = True,
    lattice_backend: str = "runtime",
) -> TaskOutcome:
    workdir = _prepare_api_workdir(work_root / arm, task.id)
    if offline_dir is not None:
        generator: Generator = _offline_generator(offline_dir, arm, task.id)
    else:
        generator = ClaudeGenerator(model=model)
    if arm in ("A", "P"):
        return run_api_baseline(
            task,
            generator,
            workdir,
            db_builder=context.db_builder,
            kit_path=context.kit_path,
            arm=arm,
            max_repairs=max_repairs if arm == "P" else 0,
            hints=hints,
        )
    if arm in ("B", "C"):
        return run_api_lattice(
            task,
            generator,
            workdir,
            db_builder=context.db_builder,
            profile=context.profile,
            arm=arm,
            max_repairs=max_repairs if arm == "B" else 0,
            hints=hints,
            backend=lattice_backend,
            kit_path=context.kit_path,
        )
    raise ValueError(f"unknown arm: {arm!r}")


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "report":
        return _report(Path(args.out_dir), args.merge)

    arms: tuple[Arm, ...] = tuple(a.strip() for a in args.arms.split(",") if a.strip())  # type: ignore[assignment]

    tasks = suite(args.suite)
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        tasks = tuple(t for t in tasks if t.id in wanted)

    out_dir = Path(args.out) if args.out else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    fixtures_dir = out_dir / "fixtures"
    api_context: _ApiContext | None = None
    fixtures_frames: dict = {}
    if args.suite == "api":
        api_context = _ApiContext(fixtures_dir)
    elif args.suite == "bench2":
        # Lazily imported: nothing outside a bench2 run touches this module.
        import bench.bench2_datasets as bench2

        bench2.write_fixtures2(fixtures_dir)
        fixtures_frames = bench2.load_fixtures2(fixtures_dir)
    else:
        write_fixtures(fixtures_dir)
        fixtures_frames = load_fixtures(fixtures_dir)

    work_root = out_dir / "work"
    offline_dir = Path(args.offline) if args.offline else None

    for arm in arms:
        if arm not in _KNOWN_ARMS:
            parser.error(f"unknown arm: {arm!r}")

    outcomes_path = out_dir / "outcomes.jsonl"
    outcomes_path.write_text("")  # start fresh; each outcome is appended as it completes

    append_lock = threading.Lock()

    def run_task(arm: Arm, task: Task) -> TaskOutcome:
        # Per-task isolation: a crash inside the harness for one task must
        # not lose the others. (BaseExceptions such as KeyboardInterrupt
        # still abort, but every completed outcome is already on disk.)
        try:
            if api_context is not None:
                outcome = _run_one_api(
                    arm,
                    task,  # type: ignore[arg-type]
                    work_root=work_root,
                    context=api_context,
                    offline_dir=offline_dir,
                    model=args.model,
                    max_repairs=args.max_repairs,
                    hints=not args.no_hints,
                    lattice_backend=args.lattice_backend,
                )
            else:
                outcome = _run_one(
                    arm,
                    task,
                    work_root=work_root,
                    fixtures_dir=fixtures_dir,
                    fixtures_frames=fixtures_frames,
                    offline_dir=offline_dir,
                    model=args.model,
                    max_repairs=args.max_repairs,
                    hints=not args.no_hints,
                )
        except BaseException as exc:
            if not isinstance(exc, Exception) and not is_panic(exc):
                raise
            outcome = harness_error_outcome(task, arm, exc)
        with append_lock:
            append_outcome(outcomes_path, outcome)
        return outcome

    jobs = [(arm, task) for arm in arms for task in tasks]
    if args.workers <= 1:
        outcomes = [run_task(arm, task) for arm, task in jobs]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            outcomes = list(pool.map(lambda job: run_task(*job), jobs))

    report_path = out_dir / "report.md"
    report_path.write_text(
        _render_report(outcomes, bench2=args.suite == "bench2", api=args.suite == "api")
    )

    print(str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
