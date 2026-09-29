"""``python -m bench.cli run [...]`` — the benchmark driver.

``python -m bench.cli report <out_dir> [--merge other.jsonl ...]`` re-renders
``<out_dir>/report.md`` from existing outcomes (no model calls), so metrics
added to the report later can be recomputed for past runs.

Live model runs cost money: don't invoke this without ``--offline`` unless a
human has approved a live run (see the plan's global constraints). The ``api``
suite's live transport is the Claude CLI (``lattice.llm.cli_client.ClaudeCliClient``,
project policy: the Anthropic API-key transport is disabled); ``--reps``, ``--resume``
and ``--workers`` mirror ``bench.edit_slice.run``'s flags of the same name.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import importlib
import json
import re
import shutil
import sys
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
    api_condition,
    harness_error_outcome,
    prepare_workdir,
    run_api_baseline,
    run_api_go,
    run_api_lattice,
    run_baseline,
    run_baseline_repair,
    run_lattice,
)
from bench.tasks import Task, suite
from lattice.llm import cli_client
from lattice.llm.cli_client import CliError, CliUsageLimitError
from lattice.llm.client import ClaudeGenerator, Generator, ScriptedGenerator
from lattice.runtime import is_panic

_SEPARATOR_RE = re.compile(r"(?m)^=====$")
_KNOWN_ARMS = ("A", "P", "B", "C", "G")
# Arm G (the 5b Go pilot: Go written by the model) exists on the api suite only.
_API_ONLY_ARMS = ("G",)
# This many CliError records in a row stop the run (exit 4): a logged-out or broken CLI
# must not burn the rest of the job list into harness_error records one at a time.
# Mirrors bench.edit_slice.run.MAX_CONSECUTIVE_CLI_ERRORS.
MAX_CONSECUTIVE_CLI_ERRORS = 3
# FastAPI scaffolding copied into every arm A/P workdir of the api suite.
API_KIT_PATH = Path(__file__).resolve().parent / "api_kit" / "platform_kit.py"
# The Go platform kit arm G's handlers.go is built against (bench.go_arm).
API_KIT_GO_DIR = Path(__file__).resolve().parent / "api_kit_go"
API_DB_NAME = "app.db"


def _offline_generator(offline_dir: Path, arm: str, task_id: str) -> Generator:
    path = offline_dir / arm / f"{task_id}.txt"
    text = path.read_text()
    texts = [chunk.strip("\n") for chunk in _SEPARATOR_RE.split(text)]
    return ScriptedGenerator(texts)


def _api_generator(model: str, offline_dir: Path | None, arm: str, task_id: str) -> Generator:
    """The api suite's generator: the scripted offline replay when ``offline_dir`` is
    given, else the Claude CLI transport (project policy: the API-key transport,
    ``ClaudeGenerator``, is disabled -- see ``lattice.llm.client.ApiKeyTransportDisabled``).
    Looked up as ``cli_client.ClaudeCliClient`` (module attribute, not a bound import) so
    tests can monkeypatch it."""
    if offline_dir is not None:
        return _offline_generator(offline_dir, arm, task_id)
    return cli_client.ClaudeCliClient(model=model)


def _default_out_dir() -> Path:
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("results") / timestamp


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bench.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the benchmark and write a report")
    run_parser.add_argument(
        "--arms",
        default="A",
        help="comma-separated arms, e.g. A,P,B,C (api suite: also G, Go written directly)",
    )
    run_parser.add_argument(
        "--suite", default="seed", choices=("seed", "deep", "all", "bench2", "api")
    )
    run_parser.add_argument("--tasks", default=None, help="comma-separated task ids to restrict to")
    run_parser.add_argument(
        "--model", default="claude-opus-5", help="a single model; alias for --models with one value"
    )
    run_parser.add_argument(
        "--models",
        default=None,
        help="comma-separated models, run in one invocation with one writer (overrides "
        "--model), e.g. claude-sonnet-5,claude-haiku-4-5",
    )
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
        "--reps", type=int, default=1, help="repetitions of each (arm, task) (default 1)"
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="continue a non-empty --out outcomes.jsonl, skipping (model, arm, task, rep) "
        "tuples already scored in it -- required whenever that file already has runs in "
        "it (never overwritten automatically), not just after an interruption",
    )
    run_parser.add_argument(
        "--lattice-backend",
        default="runtime",
        choices=("runtime", "generated", "go"),
        help="api suite: serve Lattice arms (B/C) with the runtime, the generated FastAPI "
        "app, or a Go binary built by lattice.gogen.build.build_go_app",
    )
    run_parser.add_argument(
        "--implicit",
        action="store_true",
        help="api suite: implicit-requirements condition -- both arms get the task specs "
        "without the org-policy sentences; the hidden cases still expect the policy",
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


_UNSAFE_PATH_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_path_part(text: str) -> str:
    """``text`` (a model name) as a single safe path component: no ``/`` or other
    characters that could escape the intended directory."""
    return _UNSAFE_PATH_CHARS_RE.sub("_", text) or "_"


def _prepare_api_workdir(root: Path, task_id: str, rep: int = 0) -> Path:
    # Every rep -- including rep 0 -- gets its own SIBLING directory under task_id, never
    # one nested inside another rep's. Nesting rep >= 1 inside rep 0's directory (an
    # earlier version of this function) meant preparing rep 0 (shutil.rmtree(root/
    # task_id) below) could delete an in-flight rep 1/2 directory out from under a
    # concurrent worker -- a real, reproduced race under --workers > 1 with --reps > 1
    # (FileExistsError harness errors, or worse, a vanished workdir silently scored as a
    # model failure). Sibling paths mean no two reps -- or, since `root` already carries
    # the model (see _run_one_api), no two (model, rep) pairs -- ever share one directory.
    workdir = root / task_id / f"rep{rep}"
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
    implicit: bool = False,
    rep: int = 0,
) -> TaskOutcome:
    # model is part of the workdir root: --models running several models in one
    # invocation must not let two models' runs of the same (arm, task, rep) share --
    # and race on -- one directory (bench.cli._safe_path_part sanitizes it for the
    # filesystem, though every current model name is already a safe path component).
    workdir = _prepare_api_workdir(work_root / _safe_path_part(model) / arm, task.id, rep)
    generator = _api_generator(model, offline_dir, arm, task.id)
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
            implicit=implicit,
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
            implicit=implicit,
        )
    if arm == "G":
        return run_api_go(
            task,
            generator,
            workdir,
            db_builder=context.db_builder,
            kit_dir=API_KIT_GO_DIR,
            max_repairs=max_repairs,
            hints=hints,
            implicit=implicit,
        )
    raise ValueError(f"unknown arm: {arm!r}")


def _completed(out: Path) -> set[tuple[str, str, str, int]]:
    """(requested_model, arm, task_id, rep) tuples already scored in ``out`` -- what
    ``--resume`` skips, so a run interrupted (e.g. by a CLI usage limit) can continue
    without redoing work already on disk. Keyed on the CLI's ``--model`` argument
    (``requested_model``), never the served model (``model``, which can differ across a
    repair loop's calls) -- the same key ``bench.analysis.go_pilot.load`` dedups on.
    Mirrors ``bench.edit_slice.run._completed``: a line that fails to parse is ignored
    (not counted as done -- likely a partially-written line from an interrupted run),
    and a ``harness_error`` outcome is not done either (the harness itself failed, not a
    scored model run) -- it is redone."""
    done: set[tuple[str, str, str, int]] = set()
    if not out.exists():
        return done
    with out.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("failure") == "harness_error":
                continue
            model = d.get("requested_model") or ""
            arm = d.get("arm") or ""
            task_id = d.get("task_id") or ""
            rep = d.get("rep") or 0
            done.add((model, arm, task_id, rep))
    return done


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "report":
        return _report(Path(args.out_dir), args.merge)

    if args.implicit and args.suite != "api":
        parser.error("--implicit applies to --suite api only")

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
        if arm in _API_ONLY_ARMS and args.suite != "api":
            parser.error(f"arm {arm} applies to --suite api only")

    models: tuple[str, ...] = (
        tuple(m.strip() for m in args.models.split(",") if m.strip())
        if args.models
        else (args.model,)
    )

    outcomes_path = out_dir / "outcomes.jsonl"
    has_runs = outcomes_path.exists() and outcomes_path.stat().st_size > 0
    if has_runs and not args.resume:
        parser.error(
            f"{outcomes_path} already has recorded runs; pass --resume to continue it "
            "(an outcomes file is never overwritten automatically, so paid runs are "
            "never silently lost)"
        )
    if args.resume and has_runs:
        done = _completed(outcomes_path)
    else:
        outcomes_path.write_text("")  # fresh: no file yet, or an empty one
        done = set()

    def run_task(model: str, arm: Arm, task: Task, rep: int) -> tuple[TaskOutcome, bool]:
        # Per-task isolation: a crash inside the harness for one task must
        # not lose the others. (CliUsageLimitError and other BaseExceptions such as
        # KeyboardInterrupt still abort the whole run, but every completed outcome is
        # already on disk.) was_cli_error (2nd return value, never persisted) is this
        # task's contribution to the consecutive-CliError count below -- true only for a
        # CliError that isn't a usage limit (that always aborts the run itself, above).
        was_cli_error = False
        try:
            if api_context is not None:
                outcome = _run_one_api(
                    arm,
                    task,  # type: ignore[arg-type]
                    work_root=work_root,
                    context=api_context,
                    offline_dir=offline_dir,
                    model=model,
                    max_repairs=args.max_repairs,
                    hints=not args.no_hints,
                    lattice_backend=args.lattice_backend,
                    implicit=args.implicit,
                    rep=rep,
                )
            else:
                outcome = _run_one(
                    arm,
                    task,
                    work_root=work_root,
                    fixtures_dir=fixtures_dir,
                    fixtures_frames=fixtures_frames,
                    offline_dir=offline_dir,
                    model=model,
                    max_repairs=args.max_repairs,
                    hints=not args.no_hints,
                )
        except CliUsageLimitError:
            # Not one run's failure: the whole run must stop cleanly (see below), never
            # be recorded or retried as a harness error.
            raise
        except BaseException as exc:
            if not isinstance(exc, Exception) and not is_panic(exc):
                raise
            was_cli_error = isinstance(exc, CliError)  # CliUsageLimitError never reaches here
            outcome = harness_error_outcome(task, arm, exc)
            if api_context is not None:
                outcome = dataclasses.replace(outcome, condition=api_condition(args.implicit))
        outcome = dataclasses.replace(
            outcome, requested_model=model, model=outcome.model or model, rep=rep
        )
        return outcome, was_cli_error

    jobs = [
        (model, arm, task, rep)
        for model in models
        for arm in arms
        for task in tasks
        for rep in range(args.reps)
        if (model, arm, task.id, rep) not in done
    ]

    # A single writer -- called only from this (the main) thread, on each job's result in
    # submission order -- so the JSONL is appended in the same deterministic order a
    # --workers 1 run would produce, regardless of --workers, --models or completion
    # order.
    def write(outcome: TaskOutcome) -> None:
        append_outcome(outcomes_path, outcome)

    def usage_limit_message(exc: CliUsageLimitError) -> None:
        print(f"usage limit reached, stopping cleanly: {exc}", file=sys.stderr)

    def consecutive_errors_message(n: int, detail: str) -> None:
        print(
            f"{n} consecutive CLI errors (last: {detail[:300]}); stopping the run: check "
            "the `claude` CLI (login, plan, network), then rerun with --resume (error "
            "records are redone)",
            file=sys.stderr,
        )

    stopped_early = False
    status = 0
    cli_errors = 0  # consecutive CliError (not usage-limit) records, in submission order
    if args.workers <= 1:
        for model, arm, task, rep in jobs:
            try:
                outcome, was_cli_error = run_task(model, arm, task, rep)
            except CliUsageLimitError as exc:
                usage_limit_message(exc)
                stopped_early, status = True, 3
                break
            write(outcome)
            cli_errors = cli_errors + 1 if was_cli_error else 0
            if cli_errors >= MAX_CONSECUTIVE_CLI_ERRORS:
                consecutive_errors_message(cli_errors, outcome.detail)
                stopped_early, status = True, 4
                break
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            # submit every job up front, then collect results in submission order:
            # workers run concurrently, but the JSONL stays in the same deterministic
            # order a --workers 1 run would produce.
            futures = [
                pool.submit(run_task, model, arm, task, rep) for model, arm, task, rep in jobs
            ]
            for fut in futures:
                try:
                    outcome, was_cli_error = fut.result()
                except CliUsageLimitError as exc:
                    usage_limit_message(exc)
                    pool.shutdown(wait=False, cancel_futures=True)
                    stopped_early, status = True, 3
                    break
                write(outcome)
                cli_errors = cli_errors + 1 if was_cli_error else 0
                if cli_errors >= MAX_CONSECUTIVE_CLI_ERRORS:
                    consecutive_errors_message(cli_errors, outcome.detail)
                    pool.shutdown(wait=False, cancel_futures=True)
                    stopped_early, status = True, 4
                    break

    # Re-read from disk rather than use the in-memory results: with --resume, that's
    # this run's newly-written records plus the ones already on disk it skipped.
    all_outcomes = read_outcomes(outcomes_path) if outcomes_path.exists() else []
    report_path = out_dir / "report.md"
    report_path.write_text(
        _render_report(all_outcomes, bench2=args.suite == "bench2", api=args.suite == "api")
    )

    print(str(report_path))
    return status if stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
