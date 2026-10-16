# Results: hardening of the open G9/G3 findings (HD-1 to HD-4)

Pre-registration: `2026-10-16-hardening-findings-prereg.md` (with Amendment 1). Branch `np-hd`, from `next-phases`
`bb3ff0c`. Data: `docs/results/data/2026-10-16-hardening-findings/` (`summary.json` has RAN/NOT RUN per probe). No
API key, no model call, no real connector. The real `claude` was run only as `claude auth status --json` (P3).
Nothing was committed by the build node.

## What was built
- `lattice/llm/cli_client.py` (434 lines, +17/-8): `_SCRUBBED_ENV = ("AWS_BEARER_TOKEN_BEDROCK",)`,
  `_SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")`, filter `not k.startswith(_SCRUBBED_PREFIXES)`; comment and
  two docstrings name the rule (D1).
- `lattice/modules/__init__.py` (881): `_ENTRY_NAME` and one `entry:` line after `requires:` (D2).
- `lattice/studio/mcp_intake.py` (486): `McpIntake(..., warn=None)`, `discovery()` calls the new `_warn_unmatched`,
  `view()` `writes_allowed` = connected and listed (D3, D4); `lattice/studio/server.py` (3017): `warn=log` only.
- `lattice/studio/API.md` v1.13 (two sentences, no header bump), `docs/lattice-reference.md:190` (one clause),
  `dogfood/module_market_probes.py` (`KNOWN_PROBE_FAILURES = ()` and its comment).
- Tests: `tests/studio/test_mcp_intake_http.py` (1321 lines; 48 tests, was 46), new `tests/llm/test_cli_env_scrub.py`
  (151; 7 tests), `tests/modules/test_entry_summary.py` (125; 17), `tests/studio/test_mcp_allow_writes_warn.py` (223;
  26), `tests/studio/test_hardening_mutants.py` (198; runner, collects nothing).

## Blind baseline (before any code)
The new HTTP harness, copied over `bb3ff0c`'s `lattice/` in scratch, 1 run, 5.3 s: 223 checks, **exactly** the 7 named
checks fail (`I1.3.body`, `I3.1.body`, `I5.warn`, `I11.warn`, `env.wide_absent`, `env.extra_absent`, `env.no_g9wide`);
every other check passes (data `baseline-bb3ff0c/`). Prediction held.

## Probes: predicted vs observed
| probe | predicted | observed |
|---|---|---|
| P1 H-env, I11, 10 runs | pass 10/10 | 223 checks, 0 failing, one canonical sha256 `5e9fe9a6...5dd8` in all 10 runs; 18 turns, 18 discoveries, 36 calls; 0 of 6 CORE/3 WIDE/5 EXTRA names, no `G9PROBE`/`G9WIDE` value, 3 KEEP names byte-equal in 36/36 calls |
| P2 `test_cli_env_scrub.py` | 4/4 | 7 tests pass (the table through `cli_env`, `ClaudeCliClient` with a fake binary, `discovery_env`, `rebuild.gate_env`, plus case/prefix and cap tests) |
| P3 `claude auth status --json` | 3/3 arms | RAN: (a) `loggedIn true, claude.ai, firstParty`; (b) `apiProvider bedrock` (`authMethod third_party`) != firstParty; (c) `authMethod oauth_token`; 10x identical |
| P4 install summary | pass | (a)-(e) byte-identical; (f) security, (g) `evilentry`, (h) `"a\nb"`: old + exactly the D2 line after `requires:`; 10x identical; install(confirm=no) for (a), (c) byte-identical. Probes `tamper,regress --skip-heavy`: M-tamper-T12 PASS (adapter and entry both say CODE), M-regress-R1 15/15 byte-identical to a4ab4a1; `git diff bb3ff0c -- tests/modules/test_modules.py` empty, tests/modules 124 pass (was 107) |
| P5 bodies and warnings | pass | I3.1 = A0, I1.3 = B0; warning lines I5 = 1 (the D4 line exactly), I11 = 5 in the predicted order, I0-I4, I6-I10 = 0; 10 runs identical |
| P6 `test_mcp_allow_writes_warn.py` | 6/6 | 26 tests pass |
| P7 regression | no failures beyond the named pinned changes | see below |

**Differences from the predictions (findings, no expectation was changed):**
1. `M-regress-R4` (`--help` of `module` and `lattice` differs from a4ab4a1 only by new options) FAILS in the P4
   probe run. It fails with the byte-identical observation at `bb3ff0c` (run with `--code-root` on the base tree), so
   it is not caused by HD: `lattice --help` gained `improve ... apply` text after a4ab4a1 (5 unexplained lines). The
   module-market mutant control is therefore "NOT CLEAN" (exit 1) through R4 alone; with `KNOWN_PROBE_FAILURES = ()`
   as pre-registered this stays until R4's prediction is refreshed (not an owned file).
2. `MH-13`'s old text no longer occurs in `lattice/studio/session.py` at `bb3ff0c` (Spike C added `*_IMPL_TOOLS`), so
   the unchanged runner `tests/studio/test_mcp_intake_mutants.py` aborts before any run: "the patched text occurs 0
   times" (`mutants/mh-as-registered-aborts-on-mh13.log`). Stale before HD; the file is not owned.
3. `tests/studio/test_projects.py::test_prefix_routing_and_the_bare_default` failed once in the full post run (two
   fixture repos got different `git_head` across a commit-second boundary; the flake already documented in
   `2026-10-15-mcp-http-results.md`); 3 reruns of the file pass (24/24).

## Mutants HD-M1..HD-M16 (runner `tests/studio/test_hardening_mutants.py`, 44 s, `--jobs 3`)
Control passes (98 passed). 16/16 caught; the catcher is the pytest SUITE (HTTP module + 3 unit modules).
| id | caught by (failures in the mutant run) |
|---|---|
| HD-M1 | test_cli_env_scrub (5), HTTP `test_h_env_*`, `test_findings_evidence_is_recorded` |
| HD-M2 | test_cli_env_scrub (5), HTTP `test_h_env_*` |
| HD-M3 | test_cli_env_scrub (5), HTTP `test_h_env_*` (env.keep_present) |
| HD-M4 | test_cli_env_scrub (5), HTTP `test_h_env_*` (env.keep_present) |
| HD-M5 | test_cli_env_scrub (5) only (as predicted) |
| HD-M6 | test_entry_summary (15) |
| HD-M7 | test_entry_summary (9) |
| HD-M8 | test_entry_summary (2: position) |
| HD-M9 | test_entry_summary (8: JSON quoting) |
| HD-M10 | test_mcp_allow_writes_warn (3), HTTP I1, I3, findings |
| HD-M11 | test_mcp_allow_writes_warn (2: needs-auth, pending), HTTP I1 |
| HD-M12 | HTTP I5, I11, findings |
| HD-M13 | test_mcp_allow_writes_warn (1), HTTP I2 |
| HD-M14 | test_mcp_allow_writes_warn (4), HTTP I5, I11 |
| HD-M15 | test_mcp_allow_writes_warn `a-pending-server` only (as predicted) |
| HD-M16 | test_mcp_allow_writes_warn (4), HTTP I5, I11, `I5.change`, findings |

## Pinned tests changed (old -> new), all pre-registered
In `tests/studio/test_mcp_intake_http.py`: `R_NOTION_W`, `R_LINEAR_AUTH_W`, `A3`, `B1` deleted (I3.1 expects `A0`, I1.3
expects `B0`); `test_findings_evidence_is_recorded`: `(32, 32)` -> `(0, 36)`, `F3_notion_writes_allowed is True` -> `is
False`, `F5_stderr_mentions_allow_writes_or_linear is False` -> `is True`; `GROUPS` `range(11)` -> `range(12)`;
`len(TURNS)` 17 -> 18 (new `I11.change`); counts 32/15/17 -> 36/18/18 (and the per-turn 17 -> 18, discovery 15 -> 18);
`test_h_env` needs >= 7 -> >= 11 checks; docstring "32 calls (15 discoveries, 17 turns)" -> 36 (18, 18). Added:
EXTRA/KEEP environments, `env.wide_absent`, `env.extra_absent`, `env.no_g9wide`, `env.keep_present`, a `<instance>.warn`
check for all 12 instances, `probe_i11`, `G9KEEP` forbidden in the canonical text, `HD4_unmatched_allow_writes_warnings`
in the findings. `test_modules.py`, `test_cli_client.py`, `test_mcp_intake.py`, `test_mcp_intake_mutants.py`,
`dogfood/studio/*`: unchanged.

## Regression (P7)
| check | before the first edit | after |
|---|---|---|
| `pytest tests/studio tests/mcp tests/llm tests/modules tests/security` | 1186 passed, 1 skipped, 4 deselected (457 s) | 1237 passed, 1 failed (the test_projects flake, above), 1 skipped, 4 deselected (457 s); +50 new unit tests, +2 HTTP |
| `dogfood/studio/check.sh` | PASS, 481 cases (68 s) | PASS, 481 cases (59 s) |
| MH-1..MH-14 | n/a | control PASS; 14/14 caught with MH-13's text corrected post hoc (396 s) |
| module-market MG1-22 + MM1-5 | n/a | 27/27 killed relative to the control (1264 s); control NOT CLEAN through R4 only |
| `ruff check` on every changed/created .py | | clean; `ruff format --check` clean on created files and on `mcp_intake.py`, `modules/__init__.py`, the HTTP test; `cli_client.py` and `server.py` were already unformatted at `bb3ff0c` (same diffs, none in HD lines) |

## Post hoc (corrections and additions, none changes an expectation)
- `MH-13` old text `(*_READ_TOOLS, *_MODEL_TOOLS, *_MULTI_TOOLS, *_CHANGE_TOOLS),` -> `(*_READ_TOOLS, *_MODEL_TOOLS,
  *_MULTI_TOOLS, *_IMPL_TOOLS, *_CHANGE_TOOLS),` (patched in memory by `mutants/mh_run_posthoc.py`; the mutation is the
  same: `*_MULTI_TOOLS` dropped from change).
- P4 gained one case beyond the pre-registration, (i) `entry = ""`: shown as `""`, same position.
- No script bug was found; no expected value was edited after a run.

## Decisions the pre-registration left open
- The warning check runs in `Instance.stop()` for every instance (expected list, empty by default), reading stderr after
  the process exits; lines are found by the substring `allow_writes names "` and the `[studio hh:mm:ss] ` prefix removed.
- Hint: the first listed server, in discovery order, whose display name or prefix form (`server_prefix(n)[5:-2]`)
  equals the name under `casefold()`. Dedup key: (name, sorted tuple of listed names): statuses and order are ignored.
  The line is emitted while the lock is held (one line per key under concurrency; tested with 8 threads).
- `entry` is quoted with `json.dumps` default (non-ASCII escaped as `\uXXXX`); the warning's name/hint use
  `ensure_ascii=False` as pre-registered.
- The HD mutants run pytest only (as section 4's "catcher = the pytest SUITE"), copies `docs/examples` too (the entry
  test reads the mariadb descriptor), and records the dogfood slot as "not run (not a catcher)".
- `rebuild.gate_env` test asserts the kept/dropped names only (it adds `PYTHONPATH`, `UV_NO_SYNC`, ...).
- The redundant `ANTHROPIC_` filters in `plan.py`/`rebuild.py` are untouched (section 7).

## Observations outside scope
`CLAUDECODE`, `CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDE_CODE_SESSION_ID`, ... are present in a Studio started from inside a
Claude Code session and pass `cli_env` (Amendment ruling 3: reported, not fixed). `ANTHROPIC_MODEL` and
`ANTHROPIC_DEFAULT_*_MODEL` no longer reach the child (ruling 4: deliberate; callers pass `--model`). The original G3 F2
finding ("imported into the checker") was partly mis-stated: nothing reads a custom module's `entry` today; the new line
says "not imported by install".

## Commands (wall time)
`dogfood/studio/check.sh` (68 s); pytest dirs (457 s); blind baseline `python -m tests.studio.test_mcp_intake_http --runs 1`
from the base tree (5.3 s); `--runs 10` (49 s); `p4_summary.py --runs 10` (3 s); `p3_auth.py --runs 10` (2.8 s);
`module_market_probes.py --groups tamper,regress --runs 2 --skip-heavy --base-tree <a4ab4a1>` (42 s, and 42 s with
`--code-root <bb3ff0c>`); `test_mcp_intake_mutants.py` via `mutants/mh_run_posthoc.py` (396 s);
`test_hardening_mutants.py --jobs 3` (44 s); `module_market_probes.py --mutants --with-mm --jobs 3` (1264 s).

## Orchestrator verification note
Independent re-run from this worktree: `pytest` 1183 passed (the one `test_projects` commit-second flake is the
documented one) and `dogfood/studio/check.sh` PASS (481 checks). Findings 1 and 2 above (R4 prediction stale,
`MH-13` runner stale) are left untouched and need a follow-up; neither is caused by HD.
