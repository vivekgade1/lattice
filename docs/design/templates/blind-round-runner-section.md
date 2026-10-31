# Template: the "Runners and launch" section of a blind-round prereg

Copy this section into the prereg of a blind round (replace the `<...>` fields) so that the launch is mechanical and the two process
failures of the .NET round H2 (`docs/results/2026-10-23-dotnet-h2-results.md`, section 7, items 2 and 3) cannot recur.

## N. Runners and launch (exact command lines)

Every command is run from the repository root, with the repo venv python and a clean worktree (N.2). `PY=/Users/vivekgade/Documents/lattice/.venv/bin/python`.

### N.1 Required flags: `--out` and `--scratch`

The scored and verify runners take their paths from two flags. Both are written on every command line below; none is left to a default.

* `--out DIR` is **required** for `--kind scored`, `--kind rerun` and `--kind verify` (the runner exits 1 with `--out is required` before it touches
  the ledger; the H2 scored launch failed once exactly that way). It is a **directory** the runner creates and fills (`probes-<round>.json`,
  `evidence/<row>.json`). It must be new or empty and lie under `<results data directory>`.
* `--scratch DIR` is where the runner materialises targets, caches and temporary builds. If omitted it defaults to `<out parent>/scratch-<round>`;
  name it anyway, outside the repository, and write it into the prereg. A verify run uses its own scratch directory, never the scored run's.
* The transcript auditor's `--out` is different: a **file** (`--out AUDIT.json`), optional; its `--scratch DIR` (optional) names scratch space
  outside the author's `--root` so that its paths count as inside.

Scored run, one launch per arm (a failed launch that wrote no ledger line may be relaunched with the same line; one that did is a run):

```bash
PY=/Users/vivekgade/Documents/lattice/.venv/bin/python
SCRATCH=<an absolute directory outside the repository, e.g. ~/Documents/<round>-scratch>
"$PY" dogfood/<round>_probes.py  --kind scored --out docs/results/data/<round>/scored --scratch "$SCRATCH"
"$PY" dogfood/<round>_mutants.py --kind scored --out docs/results/data/<round>/hmut   --scratch "$SCRATCH"
```

Independent verification (node V, once, from a second clean worktree of the same commit):

```bash
V=<an absolute directory outside the repository>
"$PY" dogfood/<round>_probes.py  --kind verify --out "$V/verify" --scratch "$V/scratch"
"$PY" dogfood/<round>_mutants.py --kind verify --out "$V/hmut-verify" --scratch "$V/scratch-mutants"
```

Checks that run nothing and write no ledger line (use them first, they need no `--out`):

```bash
"$PY" dogfood/<round>_probes.py  --verify-freeze-only
"$PY" dogfood/<round>_mutants.py --check-inputs
"$PY" dogfood/<round>_probes.py  --self-test --out "$V/selftest"      # stand-ins only, never the ledger
```

### N.2 Run from a clean worktree

The freeze file's must-list names every file under `lattice/`, so the freeze check refuses to run in a checkout that holds files the freeze
worktree did not (H2: an ignored `lattice/studio/graph/node_modules/`, 1313 files, in the main checkout; the refusal wrote no ledger line, and
the scored and verify runs had to be made from a clean worktree). Therefore:

1. Make a fresh worktree of the exact commit named in the freeze: `git worktree add ../<round>-run <freeze commit>` (detached or on a new branch).
2. In it, `git status --ignored --short` must print nothing before the launch: no `node_modules/`, no build output, no `.venv`, no caches under
   `lattice/`. Use `PYTHONDONTWRITEBYTECODE=1`; take the venv from the main checkout by absolute path (`PY=` above), do not create one inside.
3. Run `--verify-freeze-only` there; it must print the freeze and inputs summary. Only then run the lines of N.1.
4. Do not edit any frozen file in the run worktree; do not delete the worktree before the results are committed.

### N.3 After the author and mutant-writer sessions: the transcript audit

```bash
"$PY" scripts/audit_transcript_generic.py <transcript.jsonl> --root <the author's directory> [--scratch <its scratch>] --out <author>/<tag>-audit.json
```

Exit 0 pass, 1 void (a forbidden or outside item: confirm by reading the call before voiding, as prereg 9.5 says), 2 unreadable. The generic
auditor treats here-document bodies as data and resolves `../` hops and loop variables against the tracked working directory
(`docs/results/data/2026-10-23-dotnet-h2/audit_transcript.py` is frozen for H2 and keeps its false positives); a `review` item is listed, not voiding.
