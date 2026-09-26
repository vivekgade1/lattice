# Lattice Phase 0–3 — verification results (2026-09-26)

Branch `lattice-design`. Default model `claude-opus-5`.

## What was verified

| Check | Result |
|---|---|
| Unit + integration suite (`uv run pytest -q`) | 649 passed, 1 deselected (live) |
| Lint (`uv run ruff check .`) | clean |
| Offline end-to-end (`tests/test_e2e_offline.py`): CLI → scripted model → check/repair → execute → compare → report, all 30 seed tasks, arms B and C | 30/30 correct in both arms; B repairs a shadowing error in round 2 on every task; C = 1 round |
| Offline baseline arm A (3 tasks, hand-written pandas through the sandbox) | 3/3 correct |
| Checker soundness fuzzing (review probes, ~4,900 random accepted programs at HEAD~1 plus 1,225 more at HEAD) | every accepted program executed: 0 internal errors, 0 schema-assertion failures, 0 leaked exceptions |
| Hand-written Lattice solutions vs polars references | all 30 seed tasks + 9 of the harder added tasks match |
| Independent review (2 rounds) | round 1: 1 blocker + 5 majors, all fixed; round 2: 1 new major (engine panic from out-of-range dates), fixed and re-verified via the reviewer's reproduction |

## Not yet run: live benchmark

No Anthropic credentials were available in this environment, so the live runs were not executed:

- Live smoke test: `ANTHROPIC_API_KEY=… uv run pytest -m live`
- Smoke benchmark (12 tasks × 3 arms, ≈36 calls + repairs):
  `uv run python -m bench.cli run --arms A,B,C --suite all --tasks s01,s05,s09,s20,m02,m03,m07,m13,m25,j02,j05,j12 --out results/smoke`
- Phase-3 gate (100 tasks × 3 arms ≈ 300+ calls) — run only with explicit approval of the cost:
  `uv run python -m bench.cli run --arms A,B,C --suite all --out results/gate`

The gate in `report.md` checks: multi-step accuracy B − A ≥ +15 points; single-step no regression (2-point tolerance); arm B check-pass rate ≥ 99% (harness errors count as failures); arm B ≤ 2 repairs.

## Known open minors (accepted for now)

- Unordered float comparison sorts by 6-decimal-rounded keys; values straddling a rounding boundary can misorder (rare false negative).
- Int64 arithmetic overflow wraps silently (spec doesn't define it yet).
- `L003` (nesting too deep) reports at 1:1 instead of the offending line.
- Arm A output with string-typed dates where a Date is expected now scores as a mismatch (arm A is told to use `parse_dates`).
- Sandbox (macOS `sandbox-exec`) blocks network and writes outside the workdir but allows reads; on Linux run the harness in a container with `--network=none`.
- System prompt (~1.8k tokens) is below Opus's minimum cacheable prefix, so prompt caching likely won't engage.
