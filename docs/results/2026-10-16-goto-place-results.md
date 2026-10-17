# The go-to place (Spike F): results

**F passes WITHOUT its browser gate (PBR NOT RUN by the build node), and WITHOUT its real-agent gate: the agent exercise ran in full and MISSED all three of its sub-gates (0 unsupported failure claims, >= 36/44 correct, I verbatim-id >= 10/12). Everything else registered passed: the map, the router and the 22/22 gates on the question set, 16/16 mutants, all dev probes, the gate (`check.sh`), the byte-compat row.** Orchestrator note: `list_connected_browsers` returned one browser (Browser 1, macOS) at build time, so PBR can be run now; the build node did not run the protocol (section 4 gives it to the orchestrator) and claims no browser result.

2026-10-16, branch `np-gotoplace` (worktree `lattice-f`, merged `next-phases` 789cedd + prereg commits; no commit was made). Pre-registration:
`2026-10-16-goto-place-prereg.md` with Amendments 1 and 2. Data: `docs/results/data/2026-10-16-goto-place/` (`probes.json`, `mutants.json`,
`qset/`, `agent/`, `agent-observe/`, `agent-n-on-s4/`, `freeze.sha256` (the orchestrator's, untouched), `freeze-build*.sha256`, `pytest-before.txt`,
`pytest-after.txt`, `studio-check-sh.json`, `dev-candidates/`, `probes-run1.json`, `qset-stop-run1.json`). No Anthropic API key anywhere: the only
model use was the `claude` CLI in the agent exercise (`cli_env` strips every credential variable; USD 2.15 + 0.37 + 0.32 + a USD 0.31 smoke run = USD 3.15
list estimate of the registered USD 6). **(m)** = measured. A missed prediction is a finding; nothing was tuned after the question set was scored.

## What was built

* **The app map `lattice.appmap/1`** (`lattice/spec/appmap.py`): a pure join of Spike C's report and `ReviewData` plus the caller of each request step; no extraction,
  no process, no write; CLI `python -m lattice.spec appmap`; routes `GET /api/appmap[?section=]`, `/api/appmap/element?key=` (13 key forms, `touches` labelled static,
  404 with 5 nearest), `GET /api/impact?change=cN[&section=]` (`lattice.studio-impact/1`: D's report built in process with `change_impact`'s default mode,
  the change's own `checks.impact`, `tier1_saved` shown only on matching hashes). `[impact] reports = DIR` is read-only. API.md header **v1.25** with a `## v1.25`
  section (fenced JSON shapes pinned by `test_appmap_api.py`).
* **The router** (`lattice/spec/impact_ask.py`): model-free, only with `[impact]`, the frozen case-insensitive patterns; (A) a change id + an impact word answers
  `value` with the fixed template and the report's tier 1 and 2 items verbatim (tier 3 capped at 20 with a total); (B) a hypothetical answers `not_specified` with
  "Lattice does not predict failures. ..." and the static `touches` of the elements it names. The session chat adds `change_impact` to the ask, explain and change
  allowlists and the frozen paragraph, gated on `[impact]`.
* **The Studio view**: navigator group **App** (always listed) with `#appmap` (strip, stale banner, tabs Rules / Workflows / Lifecycles / Access, Not read, drawer)
  and `#impact` (picker, the four fixed tier headings, items verbatim, tier 1 saved or "Not run here" + CLI command, the Studio check in its own box). Words fixed; the
  forbidden-word scan passes (only the negative sentence "Lattice does not predict failures" contains "predict").
* **Gate rules R104-R108** in `dogfood/studio` (not-configured answer, bad_section/bad_key/unknown_key, bad_change/impact_not_configured, no write route, prefix routing).

## Probe table (predicted vs observed)

| id | prediction | observed (m) | result |
|---|---|---|---|
| PM1 | S: 108/34/0/6, flows 59/8/1, 1 machine with 3 edges, 78 cells (12 `.`); observed cells 15-30 (p); 100% equal | exactly those; observed cells **30** (the edge of the range); 2685 fields compared, 0 differences (also S4 2479 and S-stale 2685, 0) | PASS |
| PM2 | 100% of `code:` locations pass `check_code` | 266 locations on S and S4, 0 bad | PASS |
| PM3 | S4: 29 not built, 6 spec_only = 18 null-route cells, `no_workflows_capture`, 0 machines | exact | PASS |
| PM4 | S-stale: changed 1, 0 verified, stale banner state | changed 1 (`internal/habits/routes.go`), 142 unverified + 6 no_element, 0 observed cells, `stale` note | PASS |
| PM5 | 4/4 states | 4/4: 200, keys `inputs, schema, state, why` only | PASS |
| PM6 | the 4 unread items with `at` | 4, each `code:<file>:<line>` | PASS |
| PE | 12 keys + 404 with 5 nearest | 13 keys (3 per kind + the machine), `touches` equal to the join recomputed from the report, `ReviewData`, trace and raw workflows; `rule:R999` -> 404, 5 nearest | PASS |
| PI | 6/6 identical to D's CLI | 6/6 canonical-byte identical on the six dev candidates | PASS |
| PIS | 4/4 | `not_configured`, `absent`, `shown`, `mismatch` (names `candidate_sha256`) | PASS |
| PIC | 6/6 equal | 6/6 (4 non-null Studio checks from the Docker-run pipeline, 2 null) | PASS |
| PR | 22/22 exact, 0 claims, 0 model calls (frozen set) | **22/22**, 0 literal and 0 lenient claims, `model_calls_on_routed` 0, `model_calls_unknown` 0, C_control byte-identical to the base tree, c1..c6 `verified` | PASS |
| PS | identical to base in 4 configs; with `[impact]` +1 tool, +1 paragraph, rebuild unchanged | 4/4 identical (session temp dir normalised); 12/12 mode deltas exact; paragraph == frozen text | PASS |
| PU | all | `test_appmap_ui.py` 10 passed: node --check, navigator, api.js strings, headings, forbidden words, existing views byte-unchanged, a fake-DOM walk of the 9 map and 8 impact states with every tier item's string leaves found verbatim | PASS |
| PA | all | in the same walks: 0 unnamed buttons/selects, 0 `th` without scope, 0 unnamed tables, `aria-selected` on every tab, the loading region `aria-live="polite"`, the drawer closes on Escape (own listener and the root's) | PASS (fake DOM) |
| PBR | NOT RUN if no browser | **NOT RUN by the build node** (a browser was connected; the walk is the orchestrator's) | NOT RUN |
| PD | 10/10 identical, none leaked | CLI, `GET /api/appmap`, `GET /api/impact`: 1 distinct body of 10 per corpus (S, S4, S-stale), two roots, random hash seeds; CLI == HTTP; no path, port or run time (see D4) | PASS |
| PW | 0 changed, 0 new, 0 shim calls, 0 new containers | project, inventory, state, reports: 0/0/0 around 70 GETs, 10 CLI runs and 6 router questions; shim log empty; `docker ps -a` equal | PASS |
| PB | all | protected files empty diff; no existing test/golden/fixture edited; web files byte-identical except `app.js` (+7/-0), `api.js` (+7/-0), `views.css` (+78/-0); real server and mock: every existing route identical to the base tree (only the three new routes differ); `tools/list` and instructions identical in C's and D's configs (readonly or not; F adds no tool); `lattice --help` and `spec` usage identical; pytest: same failure ids as the base (2 failed, 29 errors; 3095 passed vs 3006) | PASS |
| PP | equal | mock == real server key for key (appmap, sections, element, errors, impact report); second mock project `not_configured` | PASS |
| PAPI | pass | header v1.25 (see D1); 5 fenced JSON shapes match the real server, the mock and the routed answers recursively | PASS |
| PX | CLI <= 0.6 s; GET appmap p50 <= 0.5 s; impact <= 3 s (p); gate 2 / 1.5 / 5 s | CLI p50 **0.179 s**; GET `/api/appmap` p50 **0.018 s**; GET `/api/impact` p50 **0.098 s** | PASS |
| PSZ | summary < 4 kB; whole map 120-350 kB (p) | summary 1,239 B; whole map **347,286 B** (inside the range by 2.7 kB) | PASS |
| GATE | PASS, 481 + 10-20 cases, 57 + 4 scenarios (p) | PASS, **509 cases** (+28), **62 scenarios** (+5), 0 findings | PASS (both counts missed my prediction, not the gate) |
| mutants | 16/16, control first | control passes; **16/16 killed** (`mutants.json`) | PASS |
| tests | baseline 2 failed + 29 errors | `uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/llm tests/modules`: 2 failed, 3095 passed, 29 errors, same ids (`pytest-after.txt`); `ruff check` and `ruff format --check` clean on the new files; `ruff check` clean on the edited files | PASS |

## The question set (scored once, after the freeze)

Map surface (22: M_rules 5, M_workflows 5, M_machines 3, M_access 5, N 4): **22/22 exact**, 0 claims. Router surface (22: I 12, H 6, C 4): **22/22**, **0** unsupported
failure claims (literal and lenient), 0 model calls on routed questions, 6/6 changes `verified` (the scorer's own check of the report hashes). The map answers came from the
real HTTP route on a Studio over S and over S4, the router answers from `POST /api/ask` with a counting `StubGenerator` and, for the 4 controls, the base tree's server.

## The real-agent exercise (claude CLI, sonnet, ask mode, `--readonly`, max 12 turns, 600 s)

All 44 questions ran (no usage limit, no timeout; 146 turns, 500 s of wall time, 7.1 M cache-read tokens, 30 k output tokens, USD 2.15 list estimate = USD 0.086 per correct
answer). Because the cap could not have covered 44 sessions at the smoke run's price, they ran in the order I, H, C, then M and N; all ran.

| gate (registered) | prediction | observed | result |
|---|---|---|---|
| 0 unsupported failure claims (literal, hard) | 0 | **72** (lenient, report-only: 49) | MISS |
| >= 36/44 correct | 37-42 | **25/44** (27/44 with the N questions asked on S4, a reported variant below) | MISS |
| I verbatim-id compliance >= 10/12 | >= 10 | **8/12** | MISS |

By set (correct/n, literal claims): I 8/12, 22 claims; H **0/6**, 19; C 4/4, 1; M_rules 5/5, 0; M_workflows 1/5, 9; M_machines 3/3, 11; M_access 4/5, 5; N 0/4, 5.
What the misses are, honestly: **(1)** several are real violations of the binding rule: H-6 says three flows "would fail with or without this invariant" (a prediction from recorded
failed replays), I-c4-1 states "`scenario:deleting_a_device_is_no_longer_offered` fails at step <= 2" with no tier item behind it, and 6/6 H answers contain a failure sentence the
scorer finds unsupported (H 0/6 correct: the agent offered to draft a change, as the frozen paragraph says, but also reasoned about failures); **(2)** many of the 72 are the
literal regex firing on the user's own words or a state value (`failed` as a delivery state in M_machines: 11 claims on 3 correct answers; C-1 "A check-in that fails validation
is refused"; I-c2-1 quoting the "not decidable" sentence), which the lenient count exempts only in part (49 remain); **(3)** I-c4 and I-c5 (4 questions) did not state the tier 2
ids the report holds, so `tier2_ids_equal` fails; I-c4-2 also called a tier 3 item a failure; **(4)** M_workflows 1/5 and M_access 1 miss: id-set answers and a status the agent
did not give; **(5)** N 0/4 because the registered setup runs the agent on the S project while the N questions are about S4 (static): see the variant. The router, which is model-free,
passed the same I, H and C questions 22/22 with 0 claims, so the failure is the chat's prose, not the data. **These are the hard-gate results; no prompt, scorer or code was changed
after seeing them.**

Variants, reported as variants and not as a re-score: **N on the S4 project** (`agent-n-on-s4/`, USD 0.32): 2/4 correct, 0 claims; merged with the S run the scorer gives 27/44, 67
literal / 44 lenient claims, I 8/12 (still MISS on all three). **Two extra tier-1 questions** in change mode with `observe: true` (`agent-observe/`, USD 0.37, not frozen, 163 s and 170 s):
both called `change_impact` with `observe: true` and stated the single tier-1 item id of D's `--tiers 123` run (c1 `t1-8ab799b855`, c2 `t1-9d387130c4`, `expected-observe.json`); 2/2.

## Mutants (control first; killers disclosed)

The control (18 named tests + probes PM1, PM2, PM4, PM6 on the Streak scratch world, no Docker) passes on the unmutated copy. Each mutant is one asserted text replacement in a scratch copy:

| mutant | killed by |
|---|---|
| GM1 static reported observed | test `test_basis_words_are_fixed`, probe PM1 |
| GM2 unread dropped | test `test_unread_items_are_listed_with_their_code_location`, probe PM6 |
| GM3 absolute evidence paths | test `test_determinism_and_purity`, probe PM2 |
| GM4 auth from a path heuristic | test `test_auth_and_refusals_are_the_route_rows_not_a_guess`, probe PM1 |
| GM5 staleness ignored | test `test_stale_downgrades_and_says_so`, probe PM4 |
| GM6 null caller not joined to `no token` | test `test_a_step_without_a_caller_joins_the_no_token_row`, probe PM1 |
| GM7 tier 3 flows merged into tier 1 | test `test_the_report_is_the_cli_report_verbatim` (prereg: PI, PU) |
| GM8 hypothetical answered with failures | test `test_a_hypothetical_is_never_a_failure_claim` (prereg: PR H rows) |
| GM9 tier1_saved shown on mismatch | test `test_tier1_saved_is_shown_only_when_the_hashes_match` (prereg: PIS) |
| GM10 mock drift | test `test_the_mock_is_the_real_server_over_the_same_fixture` (prereg: PP) |
| GM11 GET writes a cache file | test `test_get_never_runs_tier_one_or_a_process_and_writes_nothing` (prereg: PW) |
| GM12 `/api/model` gains `appmap` | test `test_existing_routes_gain_no_key` (prereg: PB) |
| GM13 UI shows "Verified" for unverified | test `test_the_fixed_headings_and_words` (prereg: PU) |
| GM14 `change_impact` listed without `[impact]` | test `test_with_impact_the_tool_and_the_paragraph_are_added_to_three_modes` (prereg: PS) |
| GM15 router calls the generator | test `test_a_routed_question_makes_no_model_call` (prereg: PR call count) |
| GM16 GET runs tier 1 | test `test_get_never_runs_tier_one_or_a_process_and_writes_nothing` (prereg: PW containers, PI) |

Disclosure: GM7-GM16 are killed by named unit tests, not by the registered probe (a probe on the six Docker-recorded changes or on the once-scored question set would have been needed).
The GM1-GM6 probe kills ran the probe runner against the mutated tree.

## Deviations and findings

* **D1 API version.** The prereg says v1.24; D took v1.24 (it did not bump the header), so this spike is **v1.25** (task instruction). The header now reads v1.25; no test pinned it.
* **D2 Records are not byte-equal through Studio's pipeline.** FORMATS step 4 stops on any difference, and the first qset run did stop (`qset-stop-run1.json`): for all six candidates the md and
  spec.py bytes equal the candidate's but Studio's pipeline adds `replaces` and `retired_targets` to the drafted trace when rules changed. Nothing was scored. The rerun
  (`--patch-records`) wrote the candidate's own trace bytes into each record's `new_trace` after drafting, then verified all three sha256 per record against `files_sha256` (6/6 equal,
  `trace_patched: true` in `qset/qset-scored.json`), and D's reports passed the scorer's candidate-hash check (`verified` 6/6). The records' `checks.impact` came from the pipeline's draft.
* **D3 Router template.** The prereg template says "Tier 1 (observed): n failing"; with no saved report matching, tier 1 was not run, so the answer says "Tier 1 (observed): not run here"
  (a count only from a hash-matched saved report). 22/22 passed with it.
* **D4 First dev run missed 3 rows, all probe bugs, fixed before the freeze** (`probes-run1.json`): PS compared argv containing the session's random temp directory; PD's time scan flagged
  `2026-03-04T12:00` (Streak's test clock, in the rule texts) so the scan now requires a time in the map to occur in the sources; PB compared two copies of a project whose directory
  names differed. A fourth run issue (a `make_project` reuse) crashed PB once. None of these touched the question set.
* **D5 Agent exercise setup.** Registered: the S project for all 44 questions. The N questions (S4) cannot be answered there; the S4 variant is reported. Question order was I, H, C, M, N (cap
  insurance; all ran). `--observe 0` on the main run, the two observe questions ran separately (USD 0.37). A one-question smoke run (I-c1-1, USD 0.31) preceded it.
* **D6 GATE counts.** 509 cases and 62 scenarios against my prediction of 481 + 10-20 and +4: more steps per scenario than predicted, and one scenario more.
* **D7 AUTHORING order.** `appmap` is inserted after `ask` so `test_impact_report`'s pin on the last four entries still holds.
* **D8 Mock fixture.** `lattice/studio/mock/appmap/` is a copy of `tests/spec/fixtures/implementation` plus one candidate whose trace carries `replaces {"R3": "R3"}` so the real pipeline
  and the mock agree byte for byte; the mock writes a `tempfile` state directory of its own per request for D's plain-file reader.
* **D9 Dev candidate d4 (removed endpoint)** is a text edit: deleting a rule bullet renumbers the positional rule ids, so its drafted spec carries 173 trace problems (the record is still `ready`).
* **D10 pytest log.** The full-suite log (`pytest-after.txt`) was taken before the last non-behavioural edits (the mock's docstring, probe-runner fixes); the four new test files and `test_web_mock.py` were rerun after them and pass.
* **Not run:** PBR (browser); nothing else registered was skipped. The real-browser items (layout, focus visibility, contrast, console and network cleanliness at 1280 and 375 px) have only the fake-DOM
  evidence above. An informal look at both views on the mock in the connected browser showed them drawing and the Impact picker working; it is not the B2 protocol.

## Freeze

`freeze-build-before-qset.sha256` was taken before the question set was scored (runner, mutants, agent, engine, server, session, mock, API.md, the two views, `api.js`, `app.js`, `views.css`, the four test files);
`freeze-build.sha256` is the same list after two edits to `goto_probes.py` (`--patch-records`, D2) and `goto_agent.py` (question order, `--observe-only`) that were made before the agent exercise and do not touch what
the question set scored. The orchestrator's `freeze.sha256` is unchanged.

## Files changed (explicit)

New: `lattice/spec/appmap.py`, `lattice/spec/impact_ask.py`, `lattice/studio/web/js/views/appmap.js`, `lattice/studio/web/js/views/impact.js`, `lattice/studio/mock/appmap/` (project, app, inventory, candidate),
`tests/spec/test_appmap.py` (28), `tests/studio/test_appmap_api.py` (16), `tests/studio/test_appmap_ui.py` (10), `tests/studio/test_impact_ask.py` (37), `dogfood/goto_probes.py`, `dogfood/goto_mutants.py`,
`dogfood/goto_agent.py`, `docs/results/data/2026-10-16-goto-place/*` (except `freeze.sha256`).
Edited (additive): `lattice/studio/server.py`, `lattice/studio/mock_server.py`, `lattice/studio/session.py`, `lattice/studio/API.md`, `lattice/studio/web/js/app.js`, `lattice/studio/web/js/api.js`,
`lattice/studio/web/css/views.css`, `lattice/spec/__main__.py` (one `AUTHORING` line), `dogfood/studio/REQUIREMENTS.md`, `dogfood/studio/spec.py`, `dogfood/studio/spec.trace.json` (R104-R108).

## Orchestrator: real-browser walk (PBR), partial, run on the mock server, 2026-10-16
A Chrome browser was connected. The registered PBR protocol (section 4) is **not completed**: I did not run the 17-state
walk on the real server over a scratch Streak project, and the 375 px width check could not be made (the
`resize_window` call reported success, but `innerWidth` stayed 1728 in the page, so no narrow-width result exists).
What was observed, in a real browser, against `python -m lattice.studio.mock_server` (the mock is built by the same
modules over a fixture, probe PP shows it key-equal to the real server):
- The **App** nav group lists App map and Impact. `#appmap` renders; its tabs Rules, Workflows, Lifecycles and Access
  each switch the panel (text hashes differ per panel; state `not_built` for the fixture). The Rules tab shows status
  chips and evidence counts; a Not built yet row shows `0 locations`. No horizontal overflow at 1728 px
  (`scrollWidth == innerWidth`) on either view.
- `#impact`: with no change chosen the state is `no_change_selected`; choosing `c0` gives `tier1_not_run`, with the four tier
  headings verbatim (Tier 1 observed, Tier 2 determined, Tier 3 static "may fail ... not a claim", Tier 4 "not run
  (implementation step, Spike E)"), the sentence "Which workflows fail is mostly not decidable before the change exists",
  and the terminal command for Tier 1. The view fetched only `GET /api/appmap` and `GET /api/impact?change=c0` besides
  the usual project routes (from the page's resource timing: GET-only is not proven for methods).
- Keyboard: 50 focusable controls in the main area; the first has a 2 px outline. Not done: the 40-Tab log, Escape on
  the drawer, per-state console and network capture (the console tool had no messages because it started after load).
- My first accessibility count flagged three `section role=tabpanel` elements as unnamed; they carry `aria-labelledby`
  (named by their tab), so it was a flaw in my check, not in the view.
- The forbidden-word scan applied literally to `#impact` matches the word "predict" in the explanatory sentence
  "Lattice does not predict failures"; it is a negation, not a prediction. Reported as a literal match.
**Verdict: PBR NOT completed.** The partial walk found no defect; it is not the registered pass.

## Orchestrator verification and rulings
Independent re-run from this worktree in my own scratch (dev stage, full counts): PM1 (S, S4, S-stale), PM2-PM6, PE, PI,
PIC, PIS, PX, PSZ, PW, PS, PD, GATE, PU, PP, PR*, PM*, CONTAINERS all pass; PB passes every sub-check except
"pytest: same failure ids as the base", which I did not supply a log for (so it reads null and the row reads false in
my re-run: an input I omitted, not a difference). `pytest tests/spec tests/mcp tests/studio tests/importer tests/llm
tests/modules`: 3097 passed, 2 failed, 29 errors (the known baseline ids). Freeze hashes of the question set, scorer and
candidates were verified before the re-run. The question set was scored once by the builder after its freeze.
**Gates not met, stated plainly:** the real-agent exercise (chat prose) missed all three sub-gates: 25/44 correct (27/44
with N on S4) against 37-42 predicted, 72 literal (49 lenient) unsupported failure claims against the required 0, and I
verbatim-id compliance 8/12 against 10/12. The deterministic surfaces pass: the map 22/22 and the router 22/22 with 0
literal and 0 lenient claims and 0 model calls on routed questions. So the dependable path for "what breaks if" is the
model-free router and the app map; free-form chat prose still over-claims and needs a new registered round. **PBR:** a
partial walk on the mock, section above; not the registered pass.
