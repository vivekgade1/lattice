"""``python -m bench.cli run [...]`` — the benchmark driver.

Live model runs cost money: don't invoke this without ``--offline`` unless a
human has approved a live run against the real API (see the plan's global
constraints).
"""

from __future__ import annotations

import argparse
import datetime
import re
from collections.abc import Sequence
from pathlib import Path

from bench.datasets import load_fixtures, write_fixtures
from bench.report import evaluate_gate, render_markdown, summarize, write_outcomes
from bench.runners import Arm, TaskOutcome, prepare_workdir, run_baseline, run_lattice
from bench.tasks import suite
from lattice.llm.client import ClaudeGenerator, Generator, ScriptedGenerator

_SEPARATOR_RE = re.compile(r"(?m)^=====$")


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
    run_parser.add_argument("--arms", default="A", help="comma-separated arms, e.g. A,B,C")
    run_parser.add_argument("--suite", default="seed", choices=("seed", "all"))
    run_parser.add_argument(
        "--tasks", default=None, help="comma-separated task ids to restrict to"
    )
    run_parser.add_argument("--model", default="claude-opus-5")
    run_parser.add_argument(
        "--out", default=None, help="output directory (default results/<UTC timestamp>)"
    )
    run_parser.add_argument("--offline", default=None, help="directory of scripted responses")
    run_parser.add_argument("--max-repairs", type=int, default=2)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    arms: tuple[Arm, ...] = tuple(a.strip() for a in args.arms.split(",") if a.strip())  # type: ignore[assignment]

    tasks = suite(args.suite)
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        tasks = tuple(t for t in tasks if t.id in wanted)

    out_dir = Path(args.out) if args.out else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    fixtures_dir = out_dir / "fixtures"
    write_fixtures(fixtures_dir)
    fixtures_frames = load_fixtures(fixtures_dir)

    work_root = out_dir / "work"
    offline_dir = Path(args.offline) if args.offline else None

    outcomes: list[TaskOutcome] = []
    for arm in arms:
        for task in tasks:
            workdir = prepare_workdir(task, work_root / arm, fixtures_dir)

            if offline_dir is not None:
                generator: Generator = _offline_generator(offline_dir, arm, task.id)
            else:
                generator = ClaudeGenerator(model=args.model)

            if arm == "A":
                outcome = run_baseline(task, generator, workdir, fixtures_frames)
            elif arm in ("B", "C"):
                max_repairs = args.max_repairs if arm == "B" else 0
                outcome = run_lattice(
                    task,
                    generator,
                    workdir,
                    fixtures_frames,
                    arm=arm,
                    max_repairs=max_repairs,
                )
            else:
                raise ValueError(f"unknown arm: {arm!r}")

            outcomes.append(outcome)

    summary = summarize(outcomes)
    gate = evaluate_gate(summary) if outcomes else None

    write_outcomes(out_dir / "outcomes.jsonl", outcomes)
    report_path = out_dir / "report.md"
    report_path.write_text(render_markdown(summary, gate))

    print(str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
