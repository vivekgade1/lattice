"""Build the "common mistakes -> exact fix" tables of arms L+ and Q+ (round-5 spec §7 (b))
from the round-4 artefacts, deterministically, into :data:`bench.edit_slice.aids.TABLE_FILES`.

The generated files are committed: round 5 uses that fixed version, never a rebuild.

* **L+ (Lattice)** -- the C3 repair-persistence analysis
  (:mod:`bench.analysis.diag_persistence`) of arm L's checker refusals in round 4. Codes
  are ranked by how often a refusal *repeated* in the next round (the mistakes repair
  feedback alone did not fix), then by persistence, then by refused rounds. Each code's
  row is today's checker message and hint (the checker re-run on the saved files of the
  refused rounds, so the row matches what L+ is shown): the most frequent exact pair.
* **Q+ (Python)** -- round-4 arm-P runs the oracle scored *silent*, their final candidate
  checked by pycheck (:func:`bench.edit_slice.qcheck.check_files`). Codes are ranked by
  the number of silent runs they flag; each row is the most frequent exact (message,
  hint) pair of that code.

Both tables are capped at the same length, in characters of the rendered section
(:func:`bench.edit_slice.aids.render_table`): the budget is the smaller of the two full
tables (each at most :data:`MAX_ROWS` rows), and each table takes its rows in rank order
while it fits. Every cell is first cut to :data:`CELL_CHARS` characters. Ties are broken
by the shorter pair, then lexicographically.

``uv run python -m bench.edit_slice.build_aids [--check]`` (``--check``: exit 1 if the
committed files differ from a rebuild).
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import types
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bench.edit_slice.aids import TABLE_FILES, MistakeRow, render_table

ROOT = Path(__file__).resolve().parents[2]
RESULTS = "bench/results/edit_slice"
L_PATTERN = f"{RESULTS}/round4_*_L.jsonl"
P_PATTERN = f"{RESULTS}/round4_*_P.jsonl"
MAX_ROWS = 8
CELL_CHARS = 280


def cap(text: str, limit: int = CELL_CHARS) -> str:
    """``text`` on one line, cut at a word boundary to at most ``limit`` characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 2].rsplit(" ", 1)[0]
    return cut + " …"


def _pick(pairs: Counter[tuple[str, str]]) -> tuple[str, str]:
    """The most frequent (message, hint); ties: the shorter, then lexicographic."""
    return min(pairs, key=lambda p: (-pairs[p], len(p[0]) + len(p[1]), p))


# ---- L+: the C3 persistence data -------------------------------------------------------------


def lattice_rows(pattern: str = L_PATTERN) -> list[MistakeRow]:
    from bench.analysis.diag_persistence import persistence, runs, transitions
    from lattice.check import check_source
    from lattice.diagnostics import Severity

    all_runs = runs(str(ROOT / pattern))
    table, _ = transitions(all_runs)
    today: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for key in sorted(all_runs):
        for r in all_runs[key]:
            if r.problem != "static":
                continue
            files = r.path / "files"
            module = types.ModuleType("_aids_profile")
            try:
                exec((files / "profile.py").read_text(), module.__dict__)  # noqa: S102
                profile = module.profile()
            except Exception:  # noqa: BLE001 -- a broken profile is not a checker refusal
                continue
            for program in sorted(files.glob("*.lattice.py")):
                for d in check_source(program.read_text(), profile).diagnostics:
                    if d.severity is Severity.ERROR and d.hint:
                        today[d.code][(d.message, d.hint)] += 1

    def rank(code: str) -> tuple[Any, ...]:
        c = table[code]
        return (-c["repeated"], -(persistence(c) or 0.0), -sum(c.values()), code)

    rows = []
    for code in sorted(table, key=rank):
        if table[code]["repeated"] == 0 or not today.get(code):
            continue  # never persisted, or today's checker no longer reports it
        message, hint = _pick(today[code])
        rows.append(MistakeRow(code, cap(message), cap(hint), table[code]["repeated"]))
    return rows


# ---- Q+: round-4 arm-P silent failures, classified by pycheck --------------------------------


def _silent_finals(pattern: str) -> Iterable[tuple[str, Path]]:
    """(ticket, the final round's ``files/`` directory) of every silent arm-P run."""
    for jsonl in sorted(glob.glob(str(ROOT / pattern))):
        root = Path(jsonl + ".artefacts")
        for line in Path(jsonl).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r["outcome"] != "silent":
                continue
            rep = root / r["model"] / "P" / r["ticket"] / f"rep{r['rep']}"
            rounds = sorted(rep.glob("round*"), key=lambda p: int(p.name[5:]))
            with_files = [d / "files" for d in rounds if (d / "files").is_dir()]
            if with_files:
                yield r["ticket"], with_files[-1]


def python_rows(pattern: str = P_PATTERN) -> tuple[list[MistakeRow], dict[str, int]]:
    from bench.edit_slice.qcheck import check_files
    from bench.edit_slice.tickets import ticket

    flagged: Counter[str] = Counter()
    pairs: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    silent = with_findings = 0
    for tid, files_dir in _silent_finals(pattern):
        silent += 1
        files = {
            p.relative_to(files_dir).as_posix(): p.read_text()
            for p in sorted(files_dir.rglob("*.py"))
        }
        findings = check_files(ticket(tid).repo, files)
        with_findings += bool(findings)
        for code in {f.code for f in findings}:
            flagged[code] += 1
        for f in findings:
            pairs[f.code][(f.message, f.hint)] += 1
    rows = []
    for code in sorted(flagged, key=lambda c: (-flagged[c], c)):
        message, hint = _pick(pairs[code])
        rows.append(MistakeRow(code, cap(message), cap(hint), flagged[code]))
    return rows, {"silent_runs": silent, "silent_runs_flagged": with_findings}


# ---- build -----------------------------------------------------------------------------------


def fit(rows: list[MistakeRow], budget: int) -> list[MistakeRow]:
    """The longest prefix of ``rows`` whose rendered table is at most ``budget`` chars."""
    kept: list[MistakeRow] = []
    for row in rows:
        if len(render_table([*kept, row])) > budget:
            break
        kept.append(row)
    return kept


def build() -> dict[str, dict[str, Any]]:
    """{lang: the table file's content}."""
    l_rows = lattice_rows()[:MAX_ROWS]
    q_rows, q_stats = python_rows()
    q_rows = q_rows[:MAX_ROWS]
    budget = min(len(render_table(l_rows)), len(render_table(q_rows)))
    l_kept, q_kept = fit(l_rows, budget), fit(q_rows, budget)
    common = {"chars_budget": budget, "cell_chars": CELL_CHARS, "max_rows": MAX_ROWS}
    return {
        "lattice": {
            "source": L_PATTERN,
            "method": "C3 repair persistence of arm-L checker refusals; codes ranked by "
            "repeated refusals, then persistence, then refused rounds; row text: today's "
            "checker message and hint, the most frequent pair",
            "candidates": len(l_rows),
            **common,
            "chars": len(render_table(l_kept)),
            "rows": [vars(r) for r in l_kept],
        },
        "python": {
            "source": P_PATTERN,
            "method": "round-4 arm-P silent runs' final candidates checked by pycheck; codes "
            "ranked by silent runs flagged; row text: the most frequent message and hint",
            "candidates": len(q_rows),
            **q_stats,
            **common,
            "chars": len(render_table(q_kept)),
            "rows": [vars(r) for r in q_kept],
        },
    }


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bench.edit_slice.build_aids")
    ap.add_argument("--check", action="store_true", help="compare with the committed files")
    args = ap.parse_args(argv)
    tables = build()
    stale = []
    for lang, data in tables.items():
        path = TABLE_FILES[lang]  # type: ignore[index]
        text = _dump(data)
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"{path.relative_to(ROOT)}: {len(data['rows'])} rows", file=sys.stderr)
    if stale:
        print("stale: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
