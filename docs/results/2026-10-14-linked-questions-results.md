# Multi-project slice 4b (linked pending questions in consumers): results

Date: 2026-10-14. Branch `np-linkedq` (worktree `lattice-linkedq`, from `next-phases` 33d8de1).
Pre-registration: `docs/results/2026-10-14-linked-questions-prereg.md` (bfb59d6, written before any
code; two orchestrator amendments before the commit, see Findings 1-2). Raw data:
`docs/results/data/2026-10-14-linked-questions/` (`summary.json`, one JSON per probe, `mutants.json`,
`Q7-regression/`). No LLM, no API key, no Docker.

## What was added

- **Trigger, one engine.** After a successful Studio `accept`, one notification is queued on a
  per-Studio background worker (the accept response and the provider's files are unchanged); the CLI
  `lattice contracts notify PROVIDER --candidate REF|PATH [--dry-run]` does the same for spec changes
  made outside Studio and for re-delivery. Both compute slice 4a's impact against the accepted
  version with each consumer's pin unchanged. No MCP or API route notifies.
- **A question is created only for a `contradiction`.** It is a NEW state kind in the consumer's own
  state directory (`linked_questions/lqN.json`), keyed by a hash of provider, consumer, contract, the
  pin's spec hash and the sorted BROKEN set (not the candidate), so a stale pin gets one question,
  not one per accept. Creation rule: nothing is created while an `open` question with the key exists
  or the link's `current` already points at it; a resolved or dismissed question is never reopened,
  and a break that returns after another state is a new question. `superseded` is computed at read
  time; no notification ever changes a status.
- **The first cross-project write is minimal.** The provider's process writes only
  `linked_questions/{.lock, lqN.json, links/*.json}` in a consumer's state directory, with atomic
  writes under an `fcntl.flock`, never through `StateStore`, never creating a missing state
  directory (the owner opts in with `lattice contracts questions init CONSUMER`), and a failed
  delivery is a warning kept in memory (`GET /api/linked-notifications`), never an error of the
  accept.
- **Clearing is the consumer's owner's:** `lattice contracts questions list|resolve|dismiss|init`
  and the consumer Studio's routes (API v1.19); MCP has `linked_question_list` only, no resolve or
  dismiss tool exists. Advisory: no gate, build, pin, check or accept ever reads a question.
- Files: `lattice/contracts/linked.py`, `lattice/contracts/__main__.py`,
  `lattice/studio/{server,state,mock_server}.py` (new side-effect-free `state_dir_path`),
  `lattice/studio/API.md`, `lattice/mcp/projects.py`, `lattice/cli.py`, dogfood rules R96 and R97.

## Blind results

| Probe | Predicted | Observed | Pass |
|---|---|---|---|
| Q0 no-op | 17 no-change rows create nothing and write nothing; `--dry-run` writes nothing | 18/18 | yes |
| Q1 labels to questions | job-scheduler D1-D5 -> 5 of 10, Streak D1-D7 -> 7 of 13; regression and unaffected rows none; keys distinct; the Studio-accept trigger equals the CLI's for the same commit except `source` | exact; the Studio trigger 3/3 | yes |
| Q2 idempotence and supersede (10 steps) | s1-s10 as tabled, incl. s9: a dismissed key's break returning after a clean state is a new question | 10/10 | yes |
| Q3 write safety | only `linked_questions/` files change; a read-only or missing state dir is a warning; whole files only for a concurrent reader; 200 concurrent ids contiguous and distinct | all (a)-(h) | yes |
| Q4 the accept is never altered | body, tree, commit, log, events and provider state identical to 33d8de1 in 4 conditions, medians within 0.3 s; a second accept succeeds while the lock is held | all 4 conditions; 0.05 s vs 0.05 s | yes |
| Q5 clearing parity | CLI == API for resolve/dismiss; list equal on CLI, MCP, API; error codes in all | 12/12 checks + mock | yes |
| Q6 durability | a restarted Studio lists the same questions in order; 50 SIGKILLs leave 0 torn files | exact | yes |
| T cost | median <= 10 s (pred. <= 1.5 s) | 0.70 s (job-scheduler), 0.68 s (Streak) | yes |
| Q7 no regression | layer-3 runners, impact, tests, dogfood; 12 workspace/contracts outputs byte-identical to 33d8de1 | all | yes |
| S safety | real repos untouched | untouched | yes |

**10 of 10 probes** in the node's run. **Mutants: 18 of 18 LQ killed** (the two race mutants LQ14
and LQ15 were killed, not survivors), 55 of 55 with the earlier families.

## Findings and post hoc

1. **Orchestrator amendment, creation rule.** The draft made any key present in any status silent
   forever, so a break that came back after a clean interval would never be announced again. The
   amended rule (above) makes it a new question; mutant LQ18 and step s9 guard it.
2. **Orchestrator amendment, MCP read-only.** The draft let MCP resolve and dismiss as "the owner's
   configured agent". The owner's default was "cleared by the consumer's owner"; an agent clearing a
   notice is not that, so only listing is exposed (LQ17).
3. **Regressions are not announced.** Only a `contradiction` creates a question: of 4a's five
   regressions, three are test-edit noise and two are real breaks the layer cannot classify
   (job-scheduler D8, Streak D10: a new required field). Contradiction-only gave 12 questions with 0
   false and 2 real breaks unannounced; including regressions gives 14 with 3 false. The notify
   result lists every row's label, so the provider's owner still sees them.
4. **The Studio trigger is a background worker; its outcome is in memory only.** A Studio killed
   between accept and delivery delivers nothing and records nothing; re-running `notify` repairs it.
5. **The consumer's state directory depends on how its Studio was launched** (project name vs
   manifest name, without `[state] dir`). Delivery goes to the first that exists and warns
   `consumer_state_ambiguous` if both do. The experiment's consumers (`ci-bot`, `streak-widget`) are
   not Studio projects, so the probes run `questions init` first.
6. **`impact_probes.py` I8 could no longer pass** (it compared with the main checkout's HEAD, which
   moved on): the same fragility as W5 earlier. Post hoc, the orchestrator changed it to materialise
   `lattice/` at 67189af with `git archive` from the repository itself; I8's byte-identical and
   golden-lock checks pass (`regression_run` fails only when `--skip-regression` is given, by
   design). The prereg's rule 5 text is not edited.
7. **Probe notes from the builder** (all in `summary.json` `post_hoc`): the prereg's "8 of 8" and
   "11 of 11" were 10 steps and 12 checks; the Studio-trigger comparison uses the CLI notify of the
   accepted commit's own sha (a different commit with the same tree differs in `candidate`,
   `git_ref` and `text`); the read-only state dir probe makes the tree read-only recursively; the
   stress parts (Q3 f/g/h, Q6's 50 kills) ran once; two script bugs fixed in the runner; the first
   invocation's nested impact run was stopped and Q7 re-run with `--merge` (code unchanged).
8. `tests/mcp/test_contract_check.py` tool-list assertions now end `... contract_impact,
   linked_question_list` (disclosed).

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| Whole verification re-run from scratch, sequential | pytest `tests/contracts tests/workspace tests/mcp tests/studio`; `dogfood/studio/check.sh`; LQ1-LQ18; `probes.py`; `streak_probes.py --allow-code-diff`; `exclude_probes.py`; `impact_probes.py --only` (I0-I5, I7, T, I6); `linkedq_probes.py --only Q0-Q6,T,S` | 967 passed, 1 skipped; PASS 435 cases; 18/18; 35/35; 32/32; 12/12; 11/11; 9/9 |
| Real repos | `git status --short` before and after | ` M uv.lock`, ` M studio.toml` |
| Lint | `ruff check` on every changed or new `.py` | clean (after the node's own 14 files plus the I8 edit) |
| Design amendments | read against the prereg before the commit | creation rule and MCP read-only in the committed document |

## What this does not show

- **A live two-Studio setup.** The consumer side was exercised through a hub Studio and the CLI; a
  running provider Studio delivering into a separate running consumer Studio on another machine was
  not tried, and consumer Studio UI does not show linked questions.
- **Notification value.** Whether owners act on these is unmeasured; fatigue (risk 4) is bounded by
  the contradiction-only rule and the key, not observed.
- **Static, spec-level signal** (as layers 3 and 4a); consumers outside the workspace are invisible.
- Builder bias: the builder read the predictions; Q1 maps 4a's committed labels, so it shows the
  rule and the plumbing, not new detection.

## Not done (layer 4 remainder)

4c consumer-side drafting (LLM); transitive contract impact; Studio change-set integration beyond
the accept hook; any web UI for linked questions; cross-machine delivery.
