# 2026-10-12: I-R, ranking Lattice's improvement candidates by hand with spike 3's method

Node I-R of the L0 tranche (`docs/design/next-phases-plan.md`). Read-only: nothing in either repo was
changed, no model was called beyond the author, no network. Exit criterion (fixed there): a ranked list of
the top items with the signal for each; the method written down; a statement of what is missing to
automate it. Spike 3's own exit for I-R adds "the owner agrees the top 3 match their own priority"; that
part cannot be settled by a document and is reported as **not met on this evidence** (see Comparison).

Abbreviations for sources: **PR** = `docs/results/2026-10-11-perf-and-resume-results.md`, **HO** =
`docs/HANDOFF-2026-10-11.md`, **H10b** = `docs/HANDOFF-2026-10-10-part2.md` (Addendum "Streak project",
the Streak backlog the spike names), **GP** = `2026-10-11-gate-profile.md`, **GT** =
`2026-10-11-gate-timings.md`, **P2** = `docs/results/data/streak-plans-2026-10-11/p2-final.json`,
**PH** = `~/Documents/streak/metrics/phases.jsonl` (main working copy, 6 lines), **PH-b** = the same file
on Streak's `build/streak` branch (15 lines, read with `git show`).

## Question

If Lattice ranked its own improvement candidates from the usage signals that exist today, using exactly
the method spike 3 proposes, would the top of the list be the items the owner would pick, and what would
it take to produce that list without a human?

## Method (verbatim from `docs/design/spikes/03-self-improving-lattice.md`)

The spike states the ranking in one place, Design step 2:

> 2. **Weekly or on-demand "improve" analysis** (an epg graph, run by the owner, not scheduled by default):
>    reads logs, clusters recurring signals, ranks by frequency x cost, drafts at most N proposals with
>    evidence links.

and in "What is missing" item 2: "**A ranker** that turns recurring signals into candidate improvements."
The signals the log is meant to hold (Design step 1): "one record per build node, gate run, rebuild
round, check finding class, `unread`/`unchecked` reason, timings by layer, tokens including cache,
outcome." Nothing else about ranking is specified.

**What the method leaves open, and the reading I used (not a new method).** The spike does not define
"cluster", "frequency" or "cost". I applied the sentence as literally as the evidence allows:

- *cluster*: one candidate = one distinct fix; signals that the same fix removes are one candidate.
- *frequency* f = the number of occurrences of the signal event in the evidence set (the three plan
  records p1/p2/p3, the gate runs of GP/GT, the Streak ledger and spec, the Studio self-improvement runs).
- *cost* c = what one occurrence cost, in the units the log fields carry. Wall seconds first
  (`wall_s`, `timings`, gate `seconds`); list-price dollars where only dollars are recorded. This is the
  cost **of the problem**, not the cost to fix (ranking by frequency x cost to fix would prefer expensive
  fixes). The cost-to-fix column below is recorded but is **not an input**: the method does not use it.
- *score* = f x c, with c per occurrence. Three score classes, because the units do not mix and I did not
  invent an exchange rate: **A** (seconds-scored) ranks above **B** (dollars only) ranks above **C** (no
  cost recorded; ordered by f alone, which is not a score). Within a class, descending.

## Items

26 candidates extracted. Status is as of Lattice `roadmap` 2c13b8b per HO/PR. Cost to fix S/M/L is my
judgement from the size of comparable fixes recorded in PR (L012: 6 tests, 2/2 mutants = S-M; spec sync:
13 tests, 6 dogfood rules, 6/6 mutants = M-L). "Auto" = whether the signal could have been logged without a
human: **Y** field exists, **P** partly (named gap), **N** needs a human today.

| ID | Candidate | Status | Signal (number or event) | Source | f | c | Fix | Benefit | Auto |
|---|---|---|---|---|---|---|---|---|---|
| I-01 | Level gates: enable `level_gate_args` (layer X is ~120 s of every level gate) | partly: code built 9f3f6e6, not set in Streak's `studio.toml` (uncommitted diff there sets `scoped_gate_args`, `level_gate`, `level_repair_attempts`, `node_timeout`, not `level_gate_args`) | 5 regression level gates took 137.5, 138.9, 143.7, 145.3, 150.9 s; X 120.0 s of 148.9 s at `--x-repeats 5`, 58.9 s at 2 | P2 `level_runs[].gate.gates[0].seconds`; PR follow-up table; `git diff studio.toml` in Streak | 5 | 120.0 s | S: one config line | measured saving 61.8 s per level gate (about 309 s per 5-gate plan); coverage trade at level gates, final full gate unchanged | P: gate seconds yes, per-layer seconds not in the plan record |
| I-02 | Reuse a stored per-tip result for an imported plan's first baseline | open | first start baseline after import ran a full 416 s gate; PR: "save 416 s, once" | PR "The real run"; P2 `timeline` (resume 2 started 23:33:35Z, baseline event 23:40:31Z) | 1 | 416 s | S: the record already holds the tip's gate (`level_runs[1].gate.sha` 941735104c, 40 failing); the importer can seed `results/<sha>.json` | one-off: only for pre-v1.10 records (p2) | Y: timeline events |
| I-03 | One MySQL server across the gates of a build instead of a container per gate | open (GT "belongs to Studio", not built) | container start 5.1 s of a 5.9 s narrowed check | GT "Where a scenario's ~1.9 s goes", "What this changes" 3 | 22 (derived: 14 rounds + 3 start baselines + 5 regression level gates; excludes agents' own runs) | 5.1 s | M: Studio owns the server lifecycle; isolation above 2 parallel gates untested (perf-phase doc) | about 3 to 4 s per gate (to 1-2 s), about 66-90 s per plan | P: `timings.setup` is in the check JSON, not in the plan record |
| I-04 | Scenario-level change set (edit a named scenario; no requirement rewrite) | open | change set c1 rejected, about $2.28: rewrote requirement text, added a rule that locked the bug in, touched an unrelated scenario; fix was two lines in `spec.py` | PR "What went wrong" 2; HO "Things that are not obvious" | 1 | $2.28 (no seconds recorded) | M-L: new change kind in the drafter, API, UI, mutants | spec fixes inside Studio, no hand edit of `spec.py` (HO calls the hand edit "owner's decision") | P: change-set cost exists; rejection reason is prose |
| I-05 | Lint unsatisfiable `expect_db` preconditions (L012) and "spec problem" line | **closed** e93faea | nodes gave up or stopped on spec defects: p2 `account` 3 rounds 309 s; p2 `sync` 3 rounds 325 s, $1.83; p3 `account` stopped after 2709 s, 3 rounds, $1.48 | P2 `timeline`; `p3.json` nodes[0]; PR "What went wrong" 1 (p3 attribution is "very likely") | 3 | 1114.3 s mean (309+325+2709 = 3343 s) | S-M | removes a class of full-plan stops | N: cause not recorded (see Gaps) |
| I-06 | Cap `Await` waits in node gates (`--await-cap`) | **closed** v1.11 | 9 scenarios x 30.03 s = 270.2 s of 283.5 s layer D, waiting on an unbuilt nudger | GT breakdown | 1 (one tip, measured repeatedly) | 270.2 s | S | D 290 s to 15 s at the node gate | Y: per-scenario `await_s` in the check report |
| I-07 | Scoped node gates and regression level gate instead of full ABCDX everywhere | **closed** v1.11 (S2a) | full ABCDX gates at level 0 and 1 took 422.5 s and 423.9 s; node gate later 21.5 s | P2 `level_runs[0..1].gate.gates[0].seconds`; PR "Gate time" | 2 | 423.2 s | M-L | node gate 412.7 s to 21.5 s | Y |
| I-08 | Spec fixes must reach the build branch (spec sync) | **closed** v1.12 13d6533 | both spec fixes had to be committed on `main` and merged into `build/streak` by hand (Streak `93fca5e`, `a0db033`); human gaps `partial` to next resume: 5166 s and 158 s (upper bounds, include diagnosis) | PR "What went wrong" 3; HO; P2 `timeline` | 2 | not attributable | M-L | no hand merge | N: no spec-edit or merge event in the plan record |
| I-09 | Stop leaves `streak-api`, `streak-api nudger` and `lattice-mysql-*` containers | **closed** v1.11 (stop hygiene) | 2 processes 17 h old, 2 exited containers after stopping p3 | GP "Cleanup observation" | 1 | none recorded | S-M | no orphans | P: pids field exists (`pids: 0` in P2) |
| I-10 | Level numbering: server prose 0-based, UI 1-based, UI patches prose with a regex | open | observation; `serverLevels` in `plan.js` | PR Improvements 6; HO | 1 | none | S | consistency | N |
| I-11 | Stale target labels in a gave-up build record | open | "failing before; not re-run by the regression gate" for scenarios that now pass | PR Improvements 7; HO | 1 | none | S | accurate record | N |
| I-12 | Accepted change sets do not mark answered questions answered | open | 17 questions: owner answered 17 on the page (342 s, 4 non-default); all 17 still counted open in the trace after the decisions change set and at setup | PH `owner_review_questions`, `apply_decisions_studio_change_set` (`questions: 17`), `setup` (`questions: 17`); H10b backlog 3 | 17 | none | M: answer-recording path in Studio | trace shows what is decided | Y: `spec.questions` in the ledger |
| I-13 | MySQL adapter: ISO `Z` instants rejected by strict MySQL 8.4 (error 1292) | open (HO "Still open from before") | Streak's harness drops `STRICT_TRANS_TABLES` from the global `sql_mode` of the throwaway container to work around it; 36 lines of Streak `spec.py` contain a literal instant ending in `Z"` (exposure, not failures) | Streak `harness.py` lines 27-33, 122-146; `grep` on `spec.py`; H10b backlog 1 | 36 (exposure) | none | S-M: convert ISO-Z for DATE/DATETIME columns in the adapter | strict mode back on in `given` rows | N: no error count logged because the workaround hides it |
| I-14 | DSL cannot express request headers, arbitrary Authorization, carried values, response-header reads, pattern matches | open | 6 of the 13 `Unchecked(...)` entries in Streak `spec.py` (R6, R16, R22, R29, R43, R93) are exactly these | Streak `spec.py` lines 1797-1827; H10b backlog 4 | 6 | none | L: language change under `docs/lattice-language-rules.md` (all targets or gated) | more rules checked | P: `unchecked` entries are in the trace, reasons are free text |
| I-15 | Service stubs record nothing, so pushes cannot be asserted | open (owner of fix unclear: Streak harness or a Lattice services convention) | 3 of 13 `Unchecked` entries (R136, R140, R142) say "the PushSender stub records nothing" | Streak `spec.py` lines 1819-1826 | 3 | none | M | 3 rules become checkable | P: same as I-14 |
| I-16 | A missing spec table aborts the whole check (DatabaseError 1146) instead of a finding | status not stated in HO/PR | Streak skeleton creates every table up front to avoid it | H10b backlog 2; Streak `harness.py` ("Seeding is strict ... must create all of them") | 1 | none | S | a finding instead of an abort | N |
| I-17 | A rebuild can pass with zero tests; `expect_body` is `subset()`, extra fields pass | open | run 2 of "Studio improves Lattice": code +10, no test | `docs/results/2026-10-10-studio-improves-lattice-run2.md`; H10b open item 2 | 1 of 2 runs | none | M | regression test exists, exact-keys option | Y: diff stat |
| I-18 | Stale rule ids in free-text `Unchecked` notes after a draft renumbers rules | status not stated since H10b | run 1 and run 2 both renumbered ("R7..R40 one too low", duplicate R23) | H10b open item 1; run 1/run 2 results | 2 | none | S-M: lint | notes follow rule ids | P |
| I-19 | Ledger `wall_s` for parallel nodes is the level's wall, not the node's | open (found here) | habits-core 309.06 s in the ledger vs 98.77 s in the plan record; nudger 141.61 s vs 113.98 s (its sibling `account`, `home` finished at 309 s / 141 s) | PH-b `build_p2_habits-core`, `build_p2_nudger`; P2 `nodes[].wall_s` | 2 | none (it corrupts other signals) | S | trustworthy timing signal | Y: both sources exist, a diff finds it |
| I-20 | Declared `touches` are incomplete; agents write outside them (scope fencing) | open (perf-phase doc item 4, not in any delivered list) | 18 of 47 changed files lie outside the node's declared `touches.files`/`touches.packages`; scaffold changed `internal/auth/tokens.go` and `internal/progress/compute.go`; `cmd/server/main.go` undeclared in 3 nodes. No cost realised: every `merge_conflicts` list is empty | P2 `nodes[].touches` vs `nodes[].files_changed` (my computation) | 18 | none realised | M | the validator's overlap serialisation can trust the declaration | Y: both fields exist |
| I-21 | Build branch checked out in the owner's worktree: level 1 could not land, plan stopped partial | mitigated (Land via resume, v1.10) | `land_error.kind = branch_checked_out`; level 1 landed by resume 21.5 h later (human availability, not attributable) | P2 `level_runs[1].land_error`, `landed_by: resume` | 1 | not attributable | S: pre-flight warning at plan start | no late surprise | Y |
| I-22 | Multi-project / contracts phase | planned, not started | no usage signal; decided 2026-10-10 "the owner starts a new project first and takes this later" | `docs/design/lattice-multi-project-phase.md`; HO "Still open from before" | 0 | none | L | cross-project impact | N |
| I-23 | Rebuild cost accounting ignores cache tokens | appears closed: node `usage` carries `cache_read_input_tokens` and `cache_creation_input_tokens`, totals 9,728,745 and 650,531 | run 2 undercounted by about 75x | H10b open item 5; P2 `nodes[].usage`, `totals` | 1 | none | S | trustworthy cost | Y |
| I-24 | Studio's in-process mysql check needs `lattice[db]` in Studio's venv | status not stated | backlog note only | H10b backlog 5 | 1 | none | S | no hidden dependency | N |
| I-25 | Studio commit message is the full request text and carries its own co-author trailer | open | Streak commit `9019656` message is the whole requirement paragraph | H10b backlog 6; Streak `git log` | 1 | none | S | readable history | Y: commit message length |
| I-26 | `check.sh` leaves an empty `metrics/last-check.json` on exit 2; `gate_env` JSON report is 190 KB | open | observation | PR Improvements 9; GT Notes | 1 | none | S | Studio reads exit code, smaller record | P |

Excluded as not improvements to Lattice: "push" (nothing pushed) and "the owner's uncommitted
`studio.toml`" (the owner's file). Also not scored: the baselines at resume 3 and 4 (146 s and 150 s) ran
on a new tip after a spec sync, so a stored result could not apply; I did not count them as avoidable.

## Ranking

Applying f x c with the arithmetic shown. Closed items are included to show where the method would have
put them; the **open-item ranking** (what to do next) skips them.

### Class A: seconds-scored

| Rank (all) | ID | f | x c (s) | = score (s) | Status |
|---|---|---|---|---|---|
| 1 | I-05 spec-defect stops | 3 | x 1114.3 (3343 / 3) | **3343.0** | closed |
| 2 | I-07 full gate everywhere | 2 | x 423.2 | **846.3** | closed |
| 3 | I-01 layer X at level gates | 5 | x 120.0 | **600.0** | partly |
| 4 | I-02 first-baseline reuse | 1 | x 416 | **416.0** | open |
| 5 | I-06 Await waits | 1 | x 270.2 | **270.2** | closed |
| 6 | I-03 container per gate | 22 | x 5.1 | **112.2** | open |

### Class B: dollars only (no seconds recorded)

| Rank | ID | f | x c ($) | = score ($) | Status |
|---|---|---|---|---|---|
| 7 | I-04 scenario-level change set | 1 | x 2.28 | **2.28** | open |

### Class C: no cost recorded, ordered by f (not a score)

I-13 (36, exposure not failures), I-20 (18), I-12 (17), I-14 (6), I-15 (3), I-08 (2, closed), I-18 (2),
I-19 (2), then f = 1: I-17, I-10, I-11, I-16, I-21, I-24, I-25, I-26, I-09 (closed), I-23 (closed),
I-22 (0).

### Open items only, the list the method would hand the owner

| # | ID | Score | One line |
|---|---|---|---|
| 1 | I-01 | 5 x 120.0 = 600.0 s | set `level_gate_args = ["--x-repeats","2"]` in Streak's `studio.toml` (S) |
| 2 | I-02 | 1 x 416 = 416 s | seed the per-tip result when importing a pre-v1.10 plan (S, one-off) |
| 3 | I-03 | 22 x 5.1 = 112.2 s | reuse one MySQL server across a build's gates (M) |
| 4 | I-04 | 1 x $2.28 = $2.28 | scenario-level change set (M-L) |
| 5 | I-13 | f = 36, no cost | convert ISO-Z in the MySQL adapter (S-M) |

Then I-20 (18), I-12 (17), I-14 (6), I-15 (3).

### Replay: does the method reproduce what was actually built?

At the first write-up of PR, the open items were I-05, I-01, I-02, I-03, I-04, I-08, and others. The method
orders them I-05 (3343 s), I-01 (600 s), I-02 (416 s), I-03 (112 s), I-04 ($2.28), I-08 (class C). The work
that was then done, per HO "Open items": L012 (I-05), spec sync (I-08) and `level_gate_args` (I-01).
Overlap 2 of 3 (I-05, I-01). The method put the spec-sync item sixth because its cost was human time and
was not recorded in seconds. It put I-02 second where nothing was built.

## Comparison with the owner's priorities

The handoff documents hold very few verbatim owner statements. What follows is derived, with the source of
each inference; it is a prediction, not an answer from the owner.

Evidence of the owner's priorities:

1. PR "What was asked": the asks in order were improve build performance; durable state and resume; run
   p2 as the test; and, added, "a failing level gate must be corrected, not stop the plan". The pattern is
   that a plan should finish without the owner stepping in.
2. HO "Open items" (the list handed to the owner): scenario-level change set first; then level numbering,
   stale labels, first-baseline reuse, push, the owner's `studio.toml`; then "Still open from before": the
   multi-project phase, the MySQL ISO-`Z` bug, DSL gaps, answered questions. The same document says of the
   hand fix of `spec.py`: "(owner's decision)".
3. `docs/design/lattice-multi-project-phase.md` status line: "decided 2026-10-10; the owner starts a new
   project first and takes this later". Streak's p2 passed on 2026-10-12, so by the owner's stated
   sequence this phase is next.
4. The owner's uncommitted `studio.toml` in Streak enables `scoped_gate_args`, `level_gate = "regression"`,
   `level_repair_attempts`, `node_timeout`, and leaves `max_parallel = 1`; it does not set
   `level_gate_args`. The owner took up the performance work but did not take up I-01, and HO documents the
   option only as a footnote ("~62 s less per level on Streak").
5. `docs/design/next-phases-plan.md` and `spikes/00-index.md`: the owner's current tranche is research on
   spikes 4, 1a, 2, 1b, 3, not these leftovers. "Parked by the owner" (HO 10-10): marketplace, Go/DB import.

**Predicted owner top 3 among today's open items** (confidence low to medium):

| # | Item | Why (source) |
|---|---|---|
| 1 | I-04 scenario-level change set | First on HO's open list; the one open item that still forces a hand edit of `spec.py` (PR "What went wrong" 2, $2.28 rejected change); fits the ask "must be corrected, not stop the plan" |
| 2 | I-22 multi-project / contracts phase | The owner's own stated sequence (item 3); HO lists it as still open; L-sized |
| 3 | I-01 cheaper level gates | The owner's first ask was build performance and the document gives a measured saving; cheap; but the owner did not set it in `studio.toml`, so lower confidence. Alternates at this slot: I-12 (the owner's own 17 answers stay "open"; the owner reads that page) and I-14 (6 of 13 Unchecked rules) |

**Comparison with the method's top 3 (I-01, I-02, I-03).**

| | Method | Owner (derived) | Agreement |
|---|---|---|---|
| 1 | I-01 | I-04 | no |
| 2 | I-02 | I-22 | no |
| 3 | I-03 | I-01 | I-01 is in both, at different ranks |

One of three items overlaps (I-01); I-04 is the method's fourth. The I-R exit ("the owner agrees the top 3
match their own priority") is therefore **not met** on a hand analysis of this evidence. Why they differ:

- The method sees only what has a recorded cost in seconds. The owner's likely first choices are chosen
  for what they unblock (a hand edit, a phase), and I-22 has no usage signal at all.
- The method counts past occurrences, not future recurrence: I-02 scores 416 s because it was large, but
  it can happen once (pre-v1.10 records), and I-03 scores through the number of gates, 3 to 4 s each.
- Frequency x cost ignores the cost to fix. The recorded S/M/L column would reorder the list (I-01 is S,
  I-04 M-L). I did not add a fix-cost term, because the task is to apply the spike's method as written.
- Where the method did agree with what was built (replay: I-05 first, I-01 in the top three), the
  signals were ones the plan record already carries (`wall_s`, gate `seconds`, rounds).
- The method has no place for correctness or risk items (I-13, I-14, I-18), where the cost is a wrong
  answer, not time.

## Gaps for automation

Fields that the method needs and the files do not record today, each checked against the file I read:

| # | Missing | Where it should be | What exists instead |
|---|---|---|---|
| 1 | Per-layer seconds for each gate run | `level_runs[].gate` in P2 | only `gates[].{command, exit_code, seconds}`; the check's own `timings` (GT) is not copied. I-01's "X is 120 s" came from a hand run (PR follow-up table), not the plan record |
| 2 | Cause class of a give-up or stop | `nodes[].builds` / `timeline[]` in P2 | `timeline` says "node account gave_up (b4, 3 round(s))"; `nodes[].error` is `None`; "unsatisfiable `expect_db`" exists only in PR prose. The L012 item's f=3 needed a human to read the agent's final message and run a control |
| 3 | Human interventions | `timeline[]` | `partial` and "resume N started (was partial)" only; the 5166 s and 158 s gaps have no spec-edit, sync or merge event (`spec_drift` is absent from P2). I-08's cost is therefore not attributable |
| 4 | Ledger completeness | `metrics/phases.jsonl` on `main` | 6 lines; the 9 `build_p2_*` lines are only on `build/streak` (PH-b). The hook ran inside rebuild worktrees (P2 `metrics.branch: rebuild/p2-metrics-4`) |
| 5 | Correct per-node wall time | ledger `wall_s` | parallel nodes get the level's wall (I-19), so a ledger-fed ranker would mis-cost them |
| 6 | Cost of the change-set edit step and the draft's input tokens | change-set record; PH `apply_decisions_studio_change_set` | PH: "tokens_in is the edit step only (the draft step's input tokens were not reported)"; run 1/2 results: "Studio records no cost for [the edit step]"; the $2.28 of rejected c1 appears only in PR prose |
| 7 | A signal class key to cluster on | every record | free-text `Unchecked(...)` reasons in Streak `spec.py`, no finding-class or reason code; clustering I-14 / I-15 needed me to read 13 sentences |
| 8 | Status of an improvement (open, closed with commit) | none | HO/PR prose only; I read it to know that I-05, I-07, I-08 are closed and I-01 is partly closed |
| 9 | `unread` reasons | none | the spike lists `unread`/`unchecked` reasons as logged signals; no `unread` signal appears in any source I read |
| 10 | Questions answered | trace | `spec.questions` stays 17 after 17 answers (I-12) |
| 11 | Cost of a failure that never happened (workarounds) | none | I-13's workaround hides the error, so there is no count of failures, only exposure |

The spike's missing item 4 (cache tokens) looks closed: node `usage` in P2 has the cache fields. Whether
`cost_usd_list_estimate` prices them correctly was not verified here.

**Judgements a human had to make in this analysis** (so a machine would need a rule or a model call):

1. Choosing the unit of "cost" (seconds, then dollars) and refusing to convert between them.
2. Deciding which items are closed (reading HO/PR prose and commit ids).
3. Attributing the p3 `account` stop to the same spec defect (PR: "very likely").
4. Classifying the 13 `Unchecked` reasons into DSL gap / stub observability / production-only.
5. Deciding that the resume-3 and resume-4 baselines are not avoidable (new tip after a spec sync).
6. Splitting a human gap into diagnosis vs merge (I did not; marked not attributable).
7. Clustering: that "L012" and the "spec problem" line are one candidate, and that I-14 and I-15 are not.
8. Cost to fix (S/M/L) and benefit.
9. Judging an exposure count (I-13's 36) against an event count (I-12's 17).

## What could not be determined

- Whether the owner agrees with either list. The comparison is derived from documents; the I-R exit needs
  the owner to be asked.
- The owner's real order for I-22, I-14 and I-12, and whether the "owner's decision" in HO about hand-editing
  `spec.py` lowers I-04.
- Whether I-16, I-18, I-24 and the in-memory change sets (H10b open item 3) are still open: neither HO
  nor PR says.
- The human share of the 5166 s and 158 s gaps and of the 21.5 h gap at level 1 (I-08, I-21).
- Whether the p3 `account` stop (2709 s) was the spec defect; it is counted in I-05 on PR's "very likely".
- The out-of-scope share of the scaffold node's 1902 s and 77,047 output tokens: the log has files
  changed, not lines per file, so I-20 has no seconds.
- Frequency over any window other than this evidence set: it is one project (Streak), one real plan (p2)
  plus one stopped plan (p3). Spike 3 itself notes "n is small"; every f here is 1 to 5 except exposures.
- Data consistency: PR lists planner usage for p2 as "shows 0" under "Not verified", but P2 carries
  `planner.usage` with $0.845 and `totals` $10.9726 = nodes $10.1275 + planner $0.8451. The record was
  evidently completed after the write-up; I used P2 as given.
- The ledger and the plan record disagree on node wall time (I-19), so every seconds figure for a node
  that ran in parallel was taken from P2, not from PH-b.
