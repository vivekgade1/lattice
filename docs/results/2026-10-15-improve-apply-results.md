# Results: `lattice improve apply` (S1)

Pre-registration: `2026-10-15-improve-apply-prereg.md`. Branch `np-apply`.
Data: `docs/results/data/2026-10-15-improve-apply/`.

## What was built
`lattice improve apply ID [--project] [--state] [--branch] [--no-gates] [--json]` and `--discard`: turns a
verified I-3 draft into a real edit on an `improve/<id>` branch in its own worktree
(`<repo parent>/<repo name>.improve/<id>`), runs the project's gates there with its own runner (which logs
no events), and writes an apply record under `applies/`. It never merges and never calls a model.

## Result: every pre-registered rule held
11/11 probe rows pass (A0-A8), deterministic ones 10 of 10 identical. The I-3 draft on the dogfood
`studio.toml` yields the predicted edit: line 22 `level_gate_args = ["--x-repeats", "2"]`, 1 `+` line,
0 `-`, blob 642c32d -> 3def11c, tree ce7f02cf. A7 closes the loop: improved 100.00 -> 40.00, delta -60.
Mutants: IA1-IA16 plus IM1-IM21 = 37/37 killed, control passes. Tests: `tests/improve tests/test_cli.py`
200 passed (111 existing + 68 new + 21 CLI); improve2 probes 15/15; `dogfood/studio/check.sh` PASS 435.
`apply.py` has no reference to `claude`, `anthropic` or `lattice.llm`.

## Differences
Only the wall time of A2a (real gates): check.sh 69-72 s (predicted ~53 s) and `tests/studio` ~359 s
(predicted ~190 s), with other jobs loading the machine. Not a pass rule.

## Post hoc
Script corrections only (masking of a 7-hex sha, snapshot paths, a vacuous base-tree cwd, a per-process
sleep marker); none changed `lattice/` or an expectation. Recorded in `summary.json`.

## Orchestrator verification
Independent re-run: `tests/improve tests/test_cli.py` 200 passed; probes A0, A1a, A1b, A4, A5, A6, A7
at 2 runs 7/7 pass; ruff check and format clean; `git diff a4ab4a1` touches only apply.py,
`improve/__main__.py`, `cli.py`, `improve_mutants.py` (additions) and the new files. A2a and A8 (real
gates, ~25 min) were not re-run by the orchestrator.
