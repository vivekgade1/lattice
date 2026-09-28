"""The edit slice's report (plan 2026-09-27 thin slice, node E3).

``python -m bench.edit_slice.report results.jsonl [...]`` prints one table per model x
arm: runs, correct / loud / silent (and harness errors), cost per correct run, mean
rounds, mean edit size (lines changed in the edited artefact: Python files for P,
Lattice programs and profile for L), mean emitted/edited code size, mean prompt size,
tokens and wall time; then the per-ticket outcomes.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

KINDS = ("correct", "loud", "silent")


def load(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _fmt(v: float | None, spec: str = ".1f") -> str:
    return "-" if v is None else format(v, spec)


def summary(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One summary per (model, arm), in first-seen order."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["arm"])].append(r)
    out = []
    for (model, arm), rs in groups.items():
        scored = [r for r in rs if r["outcome"] in KINDS]
        correct = [r for r in scored if r["outcome"] == "correct"]
        cost = sum(r["cost_usd"] for r in rs)
        out.append(
            {
                "model": model,
                "arm": arm,
                "runs": len(rs),
                **{k: sum(r["outcome"] == k for r in rs) for k in KINDS},
                "error": sum(r["outcome"] == "error" for r in rs),
                "not_expressible": sum(r["outcome"] == "not_expressible" for r in rs),
                "cost_usd": cost,
                "cost_per_correct": cost / len(correct) if correct else None,
                "truncation_rate": sum(r.get("truncated", False) for r in rs) / len(rs)
                if rs
                else None,
                "mean_rounds": _mean([r["rounds"] for r in scored]),
                "mean_lines_changed": _mean([r["lines_changed"] for r in scored]),
                "mean_code_lines": _mean([r["code_lines"] for r in scored if r["code_lines"]]),
                "mean_prompt_tokens": _mean([r["prompt_tokens_est"] for r in scored]),
                "mean_input_tokens": _mean([r["input_tokens"] for r in scored]),
                "mean_output_tokens": _mean([r["output_tokens"] for r in scored]),
                "mean_wall_s": _mean([r["wall_s"] for r in scored]),
                "mean_latency_ms": _mean(
                    [r["mean_latency_ms"] for r in correct if r.get("mean_latency_ms") is not None]
                ),
                "mean_queries": _mean(
                    [r["mean_queries"] for r in correct if r.get("mean_queries") is not None]
                ),
            }
        )
    return out


def render(rows: Sequence[dict[str, Any]]) -> str:
    lines = [
        "| model | arm | runs | correct | loud | silent | error | $ total | $/correct "
        "| trunc% | rounds | lines changed | code lines | prompt tok | in tok | out tok "
        "| wall s | ms/req | queries/req |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        "---:|---:|---:|",
    ]
    for s in summary(rows):
        err = s["error"] + s["not_expressible"]
        trunc_pct = None if s["truncation_rate"] is None else s["truncation_rate"] * 100
        lines.append(
            f"| {s['model']} | {s['arm']} | {s['runs']} | {s['correct']} | {s['loud']} "
            f"| {s['silent']} | {err} | {s['cost_usd']:.4f} | {_fmt(s['cost_per_correct'], '.4f')} "
            f"| {_fmt(trunc_pct, '.0f')} | {_fmt(s['mean_rounds'], '.2f')} "
            f"| {_fmt(s['mean_lines_changed'])} "
            f"| {_fmt(s['mean_code_lines'], '.0f')} | {_fmt(s['mean_prompt_tokens'], '.0f')} "
            f"| {_fmt(s['mean_input_tokens'], '.0f')} | {_fmt(s['mean_output_tokens'], '.0f')} "
            f"| {_fmt(s['mean_wall_s'])} | {_fmt(s['mean_latency_ms'])} "
            f"| {_fmt(s['mean_queries'], '.2f')} |"
        )
    tickets = list(dict.fromkeys(r["ticket"] for r in rows))
    cols = list(dict.fromkeys((r["model"], r["arm"]) for r in rows))
    lines += [
        "",
        "| ticket | " + " | ".join(f"{m} {a}" for m, a in cols) + " |",
        "|---|" + "---|" * len(cols),
    ]
    for t in tickets:
        cells = []
        for m, a in cols:
            outs = [r["outcome"] for r in rows if (r["ticket"], r["model"], r["arm"]) == (t, m, a)]
            cells.append(" ".join(o.replace("not_expressible", "gap") for o in outs) or "-")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    paths = [Path(p) for p in (argv if argv is not None else sys.argv[1:])]
    if not paths:
        print("usage: python -m bench.edit_slice.report results.jsonl [...]", file=sys.stderr)
        return 2
    print(render(load(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
