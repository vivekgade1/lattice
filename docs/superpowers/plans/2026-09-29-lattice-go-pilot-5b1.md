# Go Cost Pilot (5b stage 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two arms of the pre-registered Go cost pilot (G: the model writes Go; B: the model writes Lattice, compiled to Go), run 480 CLI runs, and report the pre-registered verdict.

**Architecture:** Extend the existing API benchmark (`bench/cli.py`, `bench/runners.py`) rather than build a new harness:
- a CLI transport (project policy: the Claude CLI is the only model transport);
- a new arm G (a Go baseline prompt, an auth-only Go platform kit, a build → vet → smoke repair loop);
- a `go` backend for arm B (`build_go_app`);
- a pre-registered analysis script;
- both arms scored by the same hidden cases against a built binary, through `lattice/gogen/proxy.py`.

**Tech Stack:** Python 3.14 (uv, pytest), Go 1.26, Claude CLI (`lattice/llm/cli_client.py`), `lattice.gogen` (build, proxy, rt).

**Spec:** `docs/superpowers/specs/2026-09-29-lattice-go-pilot.md` (pre-registration; its decision rules are binding and may not change after Task 8's freeze). Background: `docs/superpowers/specs/2026-09-28-lattice-go-target-design.md` §5.

## Global Constraints

- Transport: the Claude CLI only (`ClaudeCliClient`); `ClaudeGenerator` refuses API keys by policy (9f1f857).
- Arms: `G` (Go direct) and `B` with backend `go`. Models: `claude-sonnet-5` (primary), `claude-haiku-4-5`. 40 tasks (`bench/api_tasks.py`), explicit condition, hints on. 3 reps. Repair budget 2 in both arms.
- The Go kit provides exactly what `bench/api_kit/platform_kit.py` provides (auth: caller from bearer token, role gate; a DB connection) and nothing more.
- Model-written Go binaries run under the same sandbox rules as model-written Python (`bench/sandbox.py`: no network, writes only in the workdir).
- Cost: the CLI's `total_cost_usd` (`cost_basis="cli_list_estimate"`), identical accounting in both arms.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; commit with `git commit -- <paths>`.
- No live model call before Task 8, and Task 8 runs only on the frozen commit.

## Review Focus

1. **Arm fairness:** both arms get the same task text, schema facts, hints, repair budget and smoke-request feedback. Only the language-specific guide and kit differ. Pinned by a test comparing the two prompts' shared sections (Task 3).
2. **Model-written Go escaping the sandbox** (network, filesystem outside the workdir, fork bombs, infinite loops): the kill timeout and sandbox profile apply to the binary. Pinned by adversarial `handlers.go` tests (Task 4).
3. **Scoring parity:** a correct G solution and a correct B solution for the same task must both score correct. Pinned by the dry run with reference solutions for both arms (Task 7).
4. **Resume safety:** an interrupted run (a usage limit) resumes without duplicating or dropping runs. Pinned by a resume test (Task 1).
5. **Analysis correctness:** the bootstrap is clustered by task, the Newcombe-Wilson interval matches a known reference, and a synthetic "GO" and "STOP" dataset gives the right verdict (Task 6).

---

### Task 1: CLI transport, resume and workers in the API bench

**Files:** Modify `bench/cli.py` (`_run_one_api` and `main`). Test: `tests/bench/test_api_cli_transport.py`.

**Interfaces:**
- `bench.cli._api_generator(model: str, offline_dir: Path | None, arm: str, task_id: str) -> Generator`, which returns `ClaudeCliClient(model=model)` when live.
- New flags: `--reps N` (default 1), `--resume` (skip `(model, arm, task, rep)` already in the outcomes file), `--workers N` (parallel, with a single writer). Follow `bench/edit_slice/run.py`'s implementation and tests of the same flags.
- An outcome record gains `model`, `rep`, `cost_usd`, `cost_basis`.

- [ ] Write failing tests:
  - a live run uses `ClaudeCliClient` (monkeypatched to a fake);
  - `--reps 3` produces 3 records per (arm, task);
  - `--resume` after a simulated `CliUsageLimitError` midway produces exactly the missing records;
  - `--workers 4` gives the same record set as `--workers 1`.
- [ ] Implement.
- [ ] `uv run pytest tests/bench -q`.
- [ ] Commit `feat(bench): API bench on the Claude CLI with reps, resume and workers`.

### Task 2: The Go platform kit (auth only)

**Files:** Create `bench/api_kit_go/` (`go.mod` module `platformkit`, `kit.go`, `kit_test.go`). Test: `tests/bench/test_api_kit_go.py`.

**Interfaces (Go, package `platformkit`):**
- `type Caller struct{ ID any; Role string }`
- `type Kit struct{ DB *sql.DB; ... }` with `func (k *Kit) Caller(r *http.Request) (*Caller, error)` (401 semantics identical to platform_kit.py's `AuthError`) and `func (k *Kit) RequireRole(c *Caller, roles ...string) error`.
- `func Serve(register func(mux *http.ServeMux, k *Kit))`, with the same CLI as generated Lattice apps (`--db`, `--addr`, the `LISTENING` line).
- SQL counting: route all statements through `k.DB`, wrapped to count data statements into `X-Lattice-Sql-Count` when `LATTICE_TRACE_SQL=1`, using the same rule as `bench.api_runner._is_counted_statement`.
- Build offline against the module cache. `modernc.org/sqlite` comes from `lattice/gogen/rt`'s go.sum; reuse rt's pinned versions.

- [ ] Write failing tests. On probe cases (no token, unknown token, wrong role, valid token for each role), the Go kit's answers equal the Python kit's for a tiny handler, in both body and status.
- [ ] Implement.
- [ ] Commit `feat(bench): Go platform kit for the pilot's arm G (auth only)`.

### Task 3: The arm G prompt

**Files:** Modify `lattice/llm/api_prompt.py` (add `api_go_system_prompt(hints: bool = True) -> str`). Create `bench/api_kit_go/README.md` (the kit section is generated from it). Test: `tests/llm/test_api_go_prompt.py`.

- The structure mirrors the FastAPI baseline prompt (`_BASELINE_INTRO`, `_BASELINE_KIT_SECTION`, `_BASELINE_CONVENTIONS_SECTION`, `BASELINE_OWN_ROWS`, the hints and the examples). It is rewritten for Go: a `handlers.go` in `package main` with a `Register(mux, k)` function; the kit API; conventions for JSON errors, pagination and validation, identical in content to the FastAPI arm's.
- The worked examples are the FastAPI baseline's examples, translated to Go by hand and verified. Each example compiles and passes its task's cases in Task 7's dry run.

- [ ] Write failing tests:
  - the shared sections (task conventions, error format, own-rows rule, hints) are textually identical in meaning to the FastAPI prompt's, asserted section by section;
  - the prompt names only the kit's API;
  - no Lattice terms leak into it.
- [ ] Implement.
- [ ] Commit `feat(llm): arm G system prompt (Go against the platform kit)`.

### Task 4: The arm G runner

**Files:** Modify `bench/runners.py` (add `run_api_go(task, generator, workdir, *, db_builder, kit_dir, max_repairs, hints) -> TaskOutcome`). Modify `bench/cli.py` (arm `G`). Create `bench/tools/goast/main.go` (counts lines and AST nodes of a Go file). Test: `tests/bench/test_run_api_go.py`.

**Behaviour:**
1. Extract the model's Go code block.
2. Write `handlers.go` next to a generated `main.go` calling `platformkit.Serve(Register)`.
3. Run `go build`. A failure becomes repair feedback (compiler output, trimmed as the Python arm's traceback is).
4. Run `go vet`. Its findings become repair feedback.
5. Send one smoke request, exactly as arm P does. Its outcome becomes repair feedback.
6. Once the repairs are done, run the task's cases against the binary under the sandbox, using `run_cases_go`-style proxying and SQL counts.
7. Classify with `bench.api_runner.classify`.
8. Record program size via `goast`, plus rounds, tokens and cost.

- [ ] Write failing tests, with a ScriptedGenerator throughout:
  - a correct canned solution scores correct;
  - a compile error triggers a repair round that gets the compiler text;
  - a solution missing an ownership filter scores silent;
  - **adversarial solutions:** one opens a network socket, one writes outside the workdir, one loops forever, one forks. Each must fail loud or be killed without escaping the sandbox.
- [ ] Implement.
- [ ] Commit `feat(bench): arm G runner — Go written by the model, built, vetted, sandboxed, scored`.

### Task 5: The `go` backend for arm B

**Files:** Modify `bench/runners.py` (`run_api_lattice(..., backend="go")`). Test: `tests/bench/test_run_api_lattice_go.py`.

- With backend `go`, the checked program goes through `build_go_app` and the cases run on the binary. A `GoBuildError` carrying check diagnostics is handled like the existing checker feedback for repairs. A Go compile error is a harness error, recorded loudly, because it means a bug in gogen and not in the model's program.
- Program size is the Lattice source, as today.

- [ ] Write failing tests: a reference Lattice program (`tests/fixtures/api/<id>/lattice.py`) scores correct on the go backend; a checker error triggers repair; a program using a native is rejected with L890 feedback.
- [ ] Implement.
- [ ] Commit `feat(bench): arm B on the Go backend`.

### Task 6: The pre-registered analysis

**Files:** Create `bench/analysis/go_pilot.py`. Test: `tests/bench/test_go_pilot_analysis.py`.

**Interfaces:**
- `load(outcomes_path) -> Records`
- `cost_per_correct(records, arm, model) -> float`
- `ratio_bootstrap(records, model, *, n=10_000, seed=0) -> (R, lo, hi)`, resampling tasks with each task's runs kept together.
- `newcombe_wilson_diff(k1, n1, k2, n2) -> (lo, hi)`
- `verdict(records) -> {"P1": "confirmed"|"refuted"|"not confirmed", "P2": bool, "stage1": "GO"|"STOP"}`
- `render(records) -> str`: a markdown table plus the verdict, in the results-doc style.

- [ ] Write failing tests:
  - Newcombe-Wilson against a published worked example;
  - bootstrap determinism with a seed;
  - a synthetic GO dataset (B cost 0.5×, equal correctness) → GO;
  - a synthetic dataset with B at 0.7× → P1 not confirmed → STOP;
  - B at 0.5× but correctness 10 points lower → STOP;
  - B at 1.1× → refuted.
- [ ] Implement.
- [ ] Commit `feat(bench): pre-registered analysis for the Go cost pilot`.

### Task 7: Dry run and estimate

**Files:** Create `bench/offline/go_pilot/`: canned responses for 5 tasks, one per family, for both arms. Arm B uses the reference Lattice programs; arm G uses hand-written reference Go, including one deliberately silent-buggy G answer and one Lattice answer needing a repair. Test: `tests/bench/test_go_pilot_dryrun.py`.

- [ ] Run `python -m bench.cli run --suite api --arms G,B --lattice-backend go --offline bench/offline/go_pilot --reps 1`.
  - Expected: correct and silent classifications as designed.
  - Every record has cost and size fields.
  - The analysis script renders.
- [ ] Estimate the live cost from the prompt sizes (a character-based estimate, per policy) × 480 runs, and write it into the Task 8 prompt to the user.
- [ ] Commit `test(bench): offline dry run of the Go cost pilot`.

### Task 8: Freeze, live run, verdict

- [ ] Change the spec's status line to `PRE-REGISTERED <date>, frozen at <commit>` and commit it. This freezes the code. **Show the user the cost estimate and get their go-ahead before the first live call**, since `bench/cli.py`'s docstring requires human approval for live runs.
- [ ] Run live: `python -m bench.cli run --suite api --arms G,B --lattice-backend go --models claude-sonnet-5,claude-haiku-4-5 --reps 3 --workers 4 --out results/go-pilot`, using `--resume` after any usage-limit stop.
- [ ] Run `python -m bench.analysis.go_pilot results/go-pilot` and append its output, the verdict, and the threats to validity to `docs/results/2026-09-26-bench-smoke.md` under "Go cost pilot (5b stage 1, pre-registered)". Report the outcome exactly as the spec's table says, with no reinterpretation.
- [ ] Commit `docs(results): Go cost pilot — <verdict>`.
