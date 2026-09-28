"""Which checker diagnostics survive a repair round? (node C3)

Reads the saved edit-slice artefacts of arm L
(``<run>.jsonl.artefacts/<model>/L/<ticket>/rep<N>/round<k>/round.json``). Each refused
round's ``feedback`` lists its L-codes. For every code refused in round k, the next round
k+1 either:

* ``fixed``    -- the code is gone and no new code appeared (k+1 passed the checker or
                  failed later, at run time);
* ``changed``  -- the code is gone but k+1 is refused for other codes;
* ``repeated`` -- k+1 is refused for the same code again;
* ``unchecked``-- k+1 never reached the checker (truncated, merge error, ...);
* ``last``     -- k was the last round (repairs exhausted).

Persistence = repeated / (fixed + changed + repeated).

Usage (repo root)::

    uv run python -m bench.analysis.diag_persistence [glob] [--examples CODE ...]

The default glob is ``bench/results/edit_slice/round4_*_L.jsonl``.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

CODE_LINE = re.compile(r"^- (L\d{3}) at line (\d+), col (\d+): (.*)$", re.M)
OUTCOMES = ("fixed", "changed", "repeated", "unchecked", "last")


@dataclass(frozen=True)
class Round:
    path: Path
    problem: str | None
    codes: frozenset[str]
    messages: tuple[tuple[str, str], ...]  # (code, message incl. hint)


def load_round(path: Path) -> Round:
    data = json.loads((path / "round.json").read_text())
    feedback = data.get("feedback") or ""
    found = CODE_LINE.findall(feedback) if data.get("problem") == "static" else []
    return Round(
        path,
        data.get("problem"),
        frozenset(c for c, *_ in found),
        tuple((c, m) for c, _l, _c, m in found),
    )


def runs(pattern: str) -> dict[tuple[str, str, str], list[Round]]:
    """(model, ticket, rep) -> its rounds in order."""
    out: dict[tuple[str, str, str], list[Round]] = {}
    for jsonl in sorted(glob.glob(pattern)):
        root = Path(jsonl + ".artefacts")
        for rep in sorted(root.glob("*/L/*/rep*")):
            model, _arm, ticket = rep.parts[-4:-1]
            rounds = sorted(rep.glob("round*"), key=lambda p: int(p.name[5:]))
            out[(model, ticket, rep.name)] = [load_round(r) for r in rounds]
    return out


def transitions(all_runs: dict[tuple[str, str, str], list[Round]]):
    """code -> Counter(outcome), plus code -> [(round, next round)] for examples."""
    table: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[tuple[Round, Round | None]]] = defaultdict(list)
    for rounds in all_runs.values():
        for k, cur in enumerate(rounds):
            if cur.problem != "static":
                continue
            nxt = rounds[k + 1] if k + 1 < len(rounds) else None
            for code in cur.codes:
                if nxt is None:
                    outcome = "last"
                elif nxt.problem not in (None, "static", "smoke"):
                    outcome = "unchecked"
                elif code in nxt.codes:
                    outcome = "repeated"
                elif nxt.codes - cur.codes:
                    outcome = "changed"
                else:
                    outcome = "fixed"
                table[code][outcome] += 1
                examples[code].append((cur, nxt))
    return table, examples


def persistence(c: Counter[str]) -> float | None:
    n = c["fixed"] + c["changed"] + c["repeated"]
    return c["repeated"] / n if n else None


def recheck(all_runs: dict[tuple[str, str, str], list[Round]]) -> None:
    """Today's checker on the saved files of every refused round: per code, how many
    errors it reports and how many carry a machine-applicable fix."""
    import types

    from lattice.check import check_source
    from lattice.diagnostics import Severity

    total: Counter[str] = Counter()
    fixed: Counter[str] = Counter()
    for rounds in all_runs.values():
        for r in rounds:
            if r.problem != "static":
                continue
            files = r.path / "files"
            module = types.ModuleType("_recheck_profile")
            try:
                exec((files / "profile.py").read_text(), module.__dict__)  # noqa: S102
                profile = module.profile()
            except Exception:  # noqa: BLE001 -- a broken profile is not a checker refusal
                continue
            for program in sorted(files.glob("*.lattice.py")):
                for d in check_source(program.read_text(), profile).diagnostics:
                    if d.severity is Severity.ERROR:
                        total[d.code] += 1
                        fixed[d.code] += d.fix is not None
    print("\n| code | errors today | with a fix |\n|---|---:|---:|")
    for code, n in total.most_common():
        print(f"| {code} | {n} | {fixed[code]} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", nargs="?", default="bench/results/edit_slice/round4_*_L.jsonl")
    ap.add_argument("--examples", nargs="*", default=[], help="print refusals of these codes")
    ap.add_argument("--min", type=int, default=3, help="min repairable occurrences to list")
    ap.add_argument(
        "--recheck",
        action="store_true",
        help="re-run today's checker on every refused round's files: fixes offered per code",
    )
    args = ap.parse_args()

    all_runs = runs(args.pattern)
    table, examples = transitions(all_runs)
    first = [r[0] for r in all_runs.values() if r]
    passed_first = sum(r.problem != "static" and r.problem != "truncated" for r in first)
    print(f"runs: {len(all_runs)}  first-round not refused by checker: {passed_first}")
    print()
    print("| code | refused rounds | fixed | changed | repeated | persistence | unchecked | last |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    rows = sorted(
        table.items(),
        key=lambda kv: (-(persistence(kv[1]) or 0), -sum(kv[1].values())),
    )
    for code, c in rows:
        n = c["fixed"] + c["changed"] + c["repeated"]
        if n < args.min:
            continue
        p = persistence(c)
        print(
            f"| {code} | {sum(c.values())} | {c['fixed']} | {c['changed']} | {c['repeated']} "
            f"| {p:.0%} | {c['unchecked']} | {c['last']} |"
        )
    if args.recheck:
        recheck(all_runs)
    for code in args.examples:
        print(f"\n=== {code} ===")
        for cur, nxt in examples[code]:
            msgs = sorted({m for c, m in cur.messages if c == code})
            status = "-" if nxt is None else ("REPEATED" if code in nxt.codes else "gone")
            print(f"[{status}] {cur.path}")
            for m in msgs[:3]:
                print(f"    {m[:300]}")


if __name__ == "__main__":
    main()
