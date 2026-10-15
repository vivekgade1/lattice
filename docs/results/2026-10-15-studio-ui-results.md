# Studio UI for contracts and the improvement loop (API v1.21): results

Pre-registration: `docs/results/2026-10-15-studio-ui-prereg.md` with Amendment 1 (base `2a8ac57`, rules
R99-R103, gate prediction 481 cases / 57 scenarios). Branch `np-studioui`. Built and run 2026-10-15.
No LLM, no API key, 0 tokens. Data: `docs/results/data/2026-10-15-studio-ui/` (`probes/`, `mutants/`,
`regression/`). Every prediction was written before the code; numbers below are as first measured on
the reported runs.

## 1. What was built

| File | Change |
|---|---|
| `lattice/improve/apply.py` | keyword-only `stop` hook (`apply(..., stop=)`, read where `interrupt.hit` is read): 18 changed lines; `None` keeps the CLI byte-identical |
| `lattice/studio/server.py` | routes 1-6, `Studio.get_improve_draft/verify/applies/apply`, `improve_apply`, `improve_discard`, the job and `ApplyGate` (opt-in, per-server lock, stop event), guards (b)-(j), `--allow-apply`, `server_close` stop and join |
| `lattice/studio/mock_server.py` | routes 1-6 with the real shapes and the same ladder; default project `enabled: true`, the second `false` |
| `lattice/studio/API.md` | v1.21 section, `--allow-apply` in the first line |
| `lattice/studio/web/js/api.js`, `app.js`, `css/views.css` | the calls, the two nav entries (Contracts gated on the workspace), appended CSS |
| `lattice/studio/web/js/views/improve.js`, `contracts.js` | the two views (new) |
| `dogfood/studio/{spec.py, REQUIREMENTS.md, spec.trace.json, harness.py}` | R99-R103, 3 scenarios, 2 `Unchecked`, `SERVE` gains `--allow-apply` |
| `tests/studio/test_improve_api.py` (23), `tests/studio/test_studio_ui.py` (8) | new |
| `dogfood/studio_ui_probes.py`, `dogfood/studio_ui_mutants.py` | new |

## 2. Probes (P0-P8: 10 runs each, all runs identical after masking)

| Probe | Predicted | Observed | Result |
|---|---|---|---|
| P0 guard ladder | 14 of 14 exact status and code; refused rows leave state, refs, tree unchanged | 14 of 14 (a-n) in 10 of 10 runs | PASS |
| P1 read routes = CLI | 12 of 12 equal; same under `/p/<name>/`; state bytes unchanged | 12 of 12 bare, 12 of 12 under the prefix; a no-state project creates nothing | PASS |
| P2 apply = CLI apply | exit 0 both; masked records equal; tree id equal; `inserted`; route 4 within 30 s; route 3 lists; P2c `--state` | all as predicted; P2c: `next.close` has `--state <store root>` and, run verbatim, writes `<store root>/improvements.jsonl` | PASS |
| P3 gates and close | a failed/2 results/branch kept; b 124 timed_out 2-9 s; c exits within stop_grace+10 s, `interrupted`, 0 `sleep` left; d record, stale false/true | 4 of 4 | PASS |
| P4 discard | 200 exit 0, branch/worktree gone, record renamed byte-identical; again exit 3; merged exit 3 | 3 of 3 | PASS |
| P5 safety | 0 changes to the owner's .git files, refs but the new one, tree, events, ledger; 0 forbidden git argv; `GET /api/improve` identical; static 0 | 0 changes, 0 forbidden argv (git shim logged the calls), identical, static 0 hits | PASS |
| P6 hub routing | B only; A's route 3 empty; `/p/nobody` 404; same repo: one 202, one 409 | 3 of 3 | PASS |
| P7 byte-compat | 15 of 15 identical (base `2a8ac57` vs new, with and without `--allow-apply`); web files; `--help` | 15 of 15; web files differ only in `app.js`, `api.js`, `views.css` + the two new views; `--help` differs only by `--allow-apply`; removed lines in server.py: 3, mock_server.py: 2 (signatures) | PASS |
| P8 mock parity | 17 of 17 | 17 of 17 | PASS |
| P9 gate | PASS, 449 + 32 = 481 cases, 57 scenarios, `Unchecked` base + 2 | PASS, 481 cases, 57 scenarios, 0 findings, 58 s; `Unchecked` 37 -> 39 | PASS |
| P10 UI facts | all pass; `test_web_mock.py` 38 unchanged | 8 of 8 in `test_studio_ui.py` (including a fake-DOM walk of both views against the mock); `test_web_mock.py` 38 pass | PASS |
| Browser walk | evidence only | **NOT RUN**: no browser is connected (Amendment D). Layout, focus order, a11y, colour and the polling feel are unchecked; only the fake-DOM walk (no layout) ran | NOT RUN |

Raw data: `probes/P0.json` ... `P8.json`, `probes/summary.json`; per-run digests are in each file.

## 3. Mutants (control + U1-U17)

Control PASS (every catcher passes on the unpatched copy). 17 of 17 caught by a predicted catcher.

| Mutant | Predicted catcher | Caught by |
|---|---|---|
| U1 `--allow-apply` ignored | P0a, P0n | P0 |
| U2 Origin check removed | P0b | P0 |
| U3 Host check removed | P0c | P0 |
| U4 Content-Type check removed | P0e | P0 |
| U5 id regex skipped on GET apply | P0g/h, gate scenario 3 | P0 only (see finding F1) |
| U6 `draft_sha256` comparison removed | P0m, gate scenario 2 | P0, gate |
| U7 discard appends a human event | gate 1-2, P5 | gate, P5 |
| U8 `no_gates=gates` | P2, P3a | P2, P3 |
| U9 `stop` not passed | P3c | P3 |
| U10 `state_flag` always None | P2c | P2 |
| U11 lock per project | P6 | P6 |
| U12 listing globs discarded records | P4 | P4 |
| U13 verify drops `at` | P1 | P1 |
| U14 mock route 3 drops `running` | P8 | P8 |
| U15 `canApply` true for prereg | P10 | P10 |
| U16 `GET /api/improve` gains `applies` | P7 | P7 |
| U17 one byte of `rebuilds.js` | P7 | P7 |

Existing mutant sets, run against this tree, all with control PASS and every mutant caught:
`perf_mutants` 12 of 12, `sync_mutants` 6 of 6, `mcp_mutants` 6 of 6, `state_mutants` 6 of 6,
`improve_mutants` 37 of 37 (IM and IA). Logs: `mutants/existing/`. (`state_mutants.py` takes no `--jobs`:
its first invocation with the flag exited 2 and was rerun without it; the first log is kept.)

## 4. Findings and deviations (never tuned)

* **F1 (U5, gate).** The prereg predicted gate scenario 3 would also catch U5. It does not: scenario 3
  walks `a%20b` on routes 1, 2 and 5, and U5 skips the check only on `GET .../apply`. P0 g/h (which
  reads `a%2Fb` on GET 4) and `test_ids_are_validated` catch it. The scenario stays as registered.
* **F2 (extra suites under contention).** The mutant runner also runs `tests/studio/test_improve_api.py`
  and `test_studio_ui.py` against every mutant, with 3 mutants at a time. In that column
  `test_closing_the_server_interrupts_the_gates` failed in U9, U11, U12 and U13 (U9 is a real catch; the
  others are unrelated mutants): a timing test (1 s `stop_grace`, a gate that must start within 20 s)
  under CPU contention. It passes alone and in the full regression runs. Not a catcher.
* **F3 (pre-existing flake).** `tests/studio/test_projects.py::test_prefix_routing_and_the_bare_default`
  failed once in the first regression run (`git_head` of two projects built in different seconds); 3 of 3
  alone and 761 passed, 1 skipped in the second full run. Not touched by this change (log kept:
  `regression/pytest-studio-improve-cli-run1-1-flake.txt`).
* **F4 (ruff format of server.py).** `ruff format --check lattice/studio/server.py` already fails at base
  `2a8ac57` (two hunks around `# fmt: skip`); the failure is identical here (the same 2 hunks); every added
  line is clean. `ruff check` passes on every changed file.

## 5. Regression

* `uv run pytest tests/studio tests/improve tests/test_cli.py -q -p no:cacheprovider`: **761 passed, 1
  skipped** (second run, 399 s; the first run had the one flake above). Collected: tests/studio 544 (513
  at base + 31 new), tests/improve 197, tests/test_cli.py 21.
* `dogfood/studio/check.sh`: PASS, 481 cases, 57 scenarios, 0 findings, 58 s.
* `dogfood/improve_apply_probes.py --only A0,A1a,A1b,A4,A5,A7 --runs 2`: 6 of 6 pass (28 s).
* Existing mutant sets: 67 of 67 caught (section 3), 60 to 1054 s per set.

## 6. Decisions the prereg did not fix

1. Route 1 and 2 bodies are exactly the CLI's document (no extra key); an unreadable log, ledger or
   `studio.toml` answers 200 `{"error": ...}` (the CLI's exit 2).
2. Route 3 `applies` items: `{id, title, signal_key, branch, worktree, commit, base, applied_at, line,
   gates, stale}`; `running` is this project's running job `{id, started_at}` (not another project's);
   `problems` items `{file, detail}`; `enabled` is also false for a project without durable state.
3. `api.js` has seven improve calls (the list `GET /api/improve` was not in it) and six contract calls.
4. `app.js`: besides two entries and imports, a `when` filter for the Contracts entry, a nav redraw when
   `workspace` changes, and one `loadAttention` call per project load (the "N open" count).
5. Synthetic repositories also commit the three spec files (single-project mode refuses a directory
   without them).
6. `Check contracts` is always offered to a project in the manifest; `Preview impact` only to a project
   that some link points at.
7. A refused apply (exit 3) is a done job with `record` null; `record` for a finished job is the on-disk
   record only when the result is one.

## 7. Post hoc corrections (script bugs, none changed a prediction)

* P5/P8 (development run, before any reported run): P5's static scan applied the subprocess/git
  patterns to the JavaScript too (matched the word "subprocesses" in a comment); P8 counted its own
  202 sanity check as an 18th item. Scope and count fixed.
* `tests/studio/test_studio_ui.py::test_api_js_has_every_route_of_the_loop`: one needle expected a quoted
  string where `api.js` has a template literal; fixed before the reported run.
* `dogfood/studio_ui_mutants.py`: the first run's extra-suite column failed in the control because the
  copied tree lacked `docs/results/data/2026-10-12-spike3` (the tests' event log); the predicted-catcher
  column was already all-caught and unaffected. The data directory is now copied and the run repeated
  (first run kept as `mutants/*-1-control-suite-misconfigured.*`).

## 8. Commands

```
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-studioui PYTHONDONTWRITEBYTECODE=1
python dogfood/studio_ui_probes.py --runs 10        # 289 s
dogfood/studio/check.sh --json                      # 58 s
python dogfood/studio_ui_mutants.py --jobs 3        # 462 s
python dogfood/improve_apply_probes.py --only A0,A1a,A1b,A4,A5,A7 --runs 2   # 28 s
uv run pytest tests/studio tests/improve tests/test_cli.py -q -p no:cacheprovider  # 399 s
```

## Orchestrator verification (independent re-run)
`tests/studio/test_improve_api.py test_studio_ui.py test_web_mock.py` 69 passed; `studio_ui_probes.py
--runs 2` P0-P8 all PASS; `dogfood/studio/check.sh` PASS, 481 cases; `node --check` on both new views;
ruff check clean on every changed .py. The browser walk was NOT RUN (no browser connected): layout, focus,
accessibility and polling feel are unchecked; only the fake-DOM walk ran. Open reading of F1: scenario 3
does not catch mutant U5 (a P0 row and a unit test do); recorded, not changed.
