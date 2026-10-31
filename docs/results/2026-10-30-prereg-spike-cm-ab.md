# Spike CM: paid A/B of the context-management work packages (WP2, WP3, WP4, WP6, WP8): pre-registration

**Status: REGISTERED, NOT RUN; needs owner approval of the budget.** Written 2026-10-31 by node T2P of the 2026-10-30 agent graph
(branch `nc/t2p-preregs`, from the integration tip 3cc77f7). No model was called and no code was written for this document. Every dollar
figure below is an estimate or a recommendation; none is approved. Nothing in this file may be launched until the owner approves the
budget in writing and the entry conditions of section 9 hold.

## 1. Sources and what already exists

- Design source: the owner's local, untracked plan `docs/design/spikes/lattice-context-management-spike-plan.md` (sections 2, 5, 6, 8
  and 11), and the two untracked guides `docs/context-management-implementation-guide.md` and `docs/claude-context-management-skill.md`.
  They are private: this file cites them by name and section and summarises them in its own words; it copies none of their text.
- The guides are mostly advice for systems that own the model window (KV-cache eviction, attention sinks, memory tiers). Lattice calls
  models only through `claude -p`, so the plan keeps only the seams Lattice controls: what an agent is handed, what flows back to the next
  agent or round, the shape of tool results, and which text is fenced as untrusted (plan section 1). Window-internal techniques are out of scope.
- **Already shipped, part of the baseline here, not measured by this A/B:** WP1 prompt fencing (`lattice/studio/fencing.py`, wired into
  `lattice/studio/rebuild.py`, default on); WP5 cache-aware cost accounting and per-step caps (`lattice/studio/costing.py`; rebuild steps
  carry `--max-turns` and `--max-budget-usd`, node R2 of the rebuild chain). WP7 (a checked-in harness-facts note) exists as
  `docs/design/harness-facts-for-spike-briefs.md`.
- **Not built yet (checked on 3cc77f7):** WP0 telemetry, WP2 item-boundary MCP payloads (`cap_result` in `lattice/mcp/tools.py` still
  truncates to a preview envelope), WP3 typed node hand-off (`lattice/studio/plan.py`), WP4 fresh-session rounds with a Reflexion block
  (`lattice/studio/rebuild.py`, `lattice/improve/signals.py`), WP6 prompt ordering, WP8 code-grounded context packs (`lattice/spec/ask.py`,
  `lattice/studio/session.py`). Building them is code work with no model spend and is outside this budget; this prereg fixes how they are judged.

## 2. Question and hypotheses

**Question.** Do the five unbuilt packages lower tokens and cost per correct task (and rounds) on real `claude -p` runs without lowering the
number of correct tasks? The plan states this as a hypothesis, never as a measured fact (plan section 1); the harness research in
`docs/HANDOFF-2026-10-24.md` section 4 adds a reason for a low prior: harness components tend to go stale as models improve.

| id | package | hypothesis (own words) | primary metric |
|---|---|---|---|
| H2 | WP2 | Tool results cut on item boundaries, with per-rule-id counts and a "call again with a filter" hint, shrink tool-result bytes and input tokens with no loss of correct tasks | MCP bytes per call; input tokens; correct tasks |
| H3 | WP3 | A small schema-checked hand-off record per graph node (files, contracts, decisions, open issues) cuts exploration tokens in dependent nodes and level repairs | tokens per dependent node; level-repair count |
| H4 | WP4 | After round N (default 2), a fresh session with the brief, the current findings and a short "tried / why it failed" block beats resuming the same session | cost per correct task; rounds to green |
| H6 | WP6 | Stable rules first and the active task last raises the cache-read share of input tokens | cache-read share |
| H8 | WP8 | A bounded pack of `file:line`-carrying items (rule, code links, route-handler-table path, callers/callees) plus a deterministic citation post-check makes answers about code more correct and less invented | correct answers; unsupported claims |

## 3. Data status (binding for every round of the 2026-10-30 graph)

Development data now, never the basis of a blind claim: **HO5** (gRPC round-5 held-out `harborlight`), **SG** (Streak as grpc-go),
**S4** (Streak 1d00ede), the six **J** job-scheduler Go apps, the **chat round-2** questions (and round 1's committed set), **D1**, **H1**, **H2**
(.NET), **Streak** at every revision, **Cyclops** (T1-3), and T1-4's six questions. For this A/B also: the 2026-10-10 dogfood, build-graph
and rebuild fixtures, the chat round-1 fixture (25/44 correct, 72 unsupported claims, `docs/results/2026-10-16-goto-place-results.md`) and the
rationale fixture (10/13, `docs/results/2026-10-16-rationale-glossary-results.md`). Rows on these are labelled **dev** in the results.
Only the held-out tasks of section 4.3, written after the freeze, back a blind row. If chat round 3
(`docs/results/2026-10-30-prereg-chat-round3.md`) is scored first, its questions become dev data for this A/B, and the other way round.

## 4. Design

### 4.1 Arms (each package behind its own flag; flags default off)

| arm | flags on | family |
|---|---|---|
| A0 | none (WP1 fencing and WP5 caps as shipped; telemetry on, no behaviour effect) | build and question |
| A1 | WP2 + WP6 (the low-risk bundle; WP1 is already in A0) | build |
| A2 | A1 + WP3 | build |
| A3 | A2 + WP4 | build |
| A4 | WP8 | question |
| A4+ | WP8 + WP2 + WP6 | question |
| AS-x | single package x (conditional) | the family where a bundle passed or failed a gate |

AS arms run only if a bundle passes or fails G-COST or G-CORR, to attribute the cause; at most two AS arms, chosen by the rule "the
package added last in the bundle that changed verdict first".

### 4.2 Tasks

| set | tasks | status | correct means |
|---|---|---|---|
| BD (build, dev) | the dogfood/studio build (`docs/results/2026-10-10-dogfood-results.md`), the build-graph fixture (`docs/results/2026-10-10-build-graph-results.md`), the rebuild fixture (`docs/results/2026-10-10-rebuild-results.md`) | dev | Lattice's gate re-run on a clean `git archive` of the tip passes, protected files byte-identical |
| BH (build, held-out) | three change tasks HB1-HB3 on a new small app (section 6) | blind | as BD, plus the author's hidden tests all pass |
| QD (questions, dev) | a fixed 12-question subset of round 1's committed set `docs/results/data/2026-10-18-goto-chat-round2/frozen-r1/qset.json` (drawn by `random.sample` over the sorted ids with seed 20261030; the id list is written into the freeze before any run) | dev | the frozen round-1 scorer `docs/results/data/2026-10-18-goto-chat-round2/frozen-r1/goto_score.py` |
| QH (questions, held-out) | 16 new questions with keys over the new app (section 6), at least 8 designed to tempt unsupported claims | blind | the key, scored by the committed scorer (T1-4's `dogfood/t14_casko_audit.py` functions) |

Question runs use the Studio session path in ask mode, one fresh session per question, as chat round 2's driver `dogfood/chat2_agent.py`
does (WP8 plugs into that path first). Build runs use the Studio rebuild loop or build graph that each fixture names, with its caps as shipped.

### 4.3 Repeats and run count

3 repeats per arm per task (model nondeterminism). A **build run** is one task built once; a **question run** is one pass over a whole set.
Build family: 4 arms x 6 tasks x 3 = 72. Question family: 3 arms (A0, A4, A4+) x 2 sets x 3 = 18. Conditional AS arms: at most
2 x 6 x 2 = 24. Total at most 114 runs (the plan's rough estimate was about 120). A task counts as correct in an arm when at least 2 of its
3 repeats are correct; comparisons are paired per task.

### 4.4 Metrics (the owner's benchmark-metrics rule: all reported together, per run and per arm)

Correct tasks; **cost per correct task** (sum of `total_cost_usd` over the arm's runs / correct tasks; killed steps priced by
`lattice/studio/costing.py` as `stream_estimate`); tokens split input / cache read / cache write / output (from the stream, `scripts/stream_metrics.py`);
**program size** (non-test lines added plus changed in the delivered tree, `git diff --numstat` against the task base, tests excluded);
**rounds** to green; level repairs; **wall time**; MCP response bytes per call and the truncation flag; brief size per section; cache-read
share; for questions also unsupported claims (citation post-check count plus `claim_check.scorer_claims`, `lattice/spec/claim_check.py`) and pack size.

## 5. Gates (fixed now; the 15 % and one-task rules are the owner's, plan section 11)

| id | rule | applies to |
|---|---|---|
| G-CORR | correct-task count of the arm >= A0's minus 1, **per set** (BD, BH, QD, QH separately) | every arm |
| G-COST | cost per correct task <= 0.85 x A0's, pooled over the arm's family, **and** the paired per-task cost ratio's 90 % bootstrap interval (10,000 resamples over tasks, seed 20261030) lies below 1.00 | the arm claiming a win |
| G-JSON | 0 capped MCP payloads that fail `json.loads`; 0 rule ids present uncapped but missing from the capped per-rule counts (deterministic replay of every recorded `check_run`) | A1-A4+ (WP2) |
| G-HAND | 0 nodes failed because of a malformed hand-off record (fallback count reported) | A2, A3 |
| G-ROUND | median rounds to green <= A2's median + 1 | A3 |
| G-CACHE | cache-read share of input tokens >= A0's + 10 percentage points | WP6 shipped alone |
| G-PACK | QH: correct >= A0's + 2 (of 16) **and** unsupported claims <= 0.5 x A0's; every pack item's `file:line` resolves (0 dangling) | A4, A4+ |
| G-INERT | a package whose mechanism fired in fewer than 3 runs of its family (e.g. WP4 never reached round N, WP3 met only single-node plans) gets **no verdict**: "inert, not measured" | all |

A package ships default-on only if its arm passes G-CORR, its own safety gate and G-COST (or G-CACHE / G-PACK for WP6 / WP8). A point
reduction of 15 % or more whose interval crosses 1.00 is reported as **not separated**, and the flag stays off. Blind (BH, QH) and dev (BD, QD)
rows are reported side by side, never pooled into one headline; a win on dev sets alone is dev evidence only.

## 6. Held-out author and isolation

One new agent (not a builder, verifier, earlier author or the orchestrator), `claude` CLI only, **sonnet**, dispatched **after** the freeze
commit, in a fixed empty directory `~/Documents/cm-heldout-author` (new). It receives only `BRIEF.md`, a harness API note and the task
schema (all new, written into the freeze). It writes: a small Python (FastAPI over SQLite, no Docker) app of 10-15 routes with a Lattice
spec, trace and `check.sh`; three change tasks HB1-HB3 (a spec edit each, plus hidden tests the builder never sees); and 16 questions with
keys (QH), at least 8 of them false-premise, not-specified, "where in the code" or "what would fail if" questions. The brief names no
package, no metric and nothing from this file. The author must not read `~/Documents/lattice*`, `~/.claude`, Streak, the job scheduler or any
other author directory (`--forbid` for `~/Documents/g5-`, `~/Documents/cyclops`, `~/Documents/job-scheduler`); enforcement is
`scripts/audit_transcript_generic.py` on the full transcript (one confirmed forbidden or outside item
voids the deliverable; a review item is listed, not voiding). One contract-check fix round, disclosed. Cap: section 8.

## 7. Predictions (stated before any build; honest ranges)

| item | prediction | p |
|---|---|---|
| G-CORR holds for A1 / A2 / A3 / A4+ | yes for A1 and A4+; A3 the most at risk (lost context) | 0.85 / 0.8 / 0.65 / 0.8 |
| A1 cost per correct task vs A0 | -15 % to +5 % (point -5 %) | G-COST pass 0.2 |
| A2 (WP3) | inert on BD's single-node tasks; on the graph task -25 % to +5 % tokens per dependent node | G-INERT triggered 0.4; G-COST 0.15 |
| A3 (WP4) | rounds to green -1 to +2; cost -10 % to +25 % (point +5 %); most BD/BH tasks pass in round 1, so WP4 may be inert | G-INERT 0.5; G-COST 0.1 |
| WP6 cache-read share | +0 to +15 points (point +5) | G-CACHE 0.3 |
| A4 on QH | correct +0 to +4 of 16 (point +2); unsupported claims -70 % to +10 % (point -40 %) | G-PACK 0.3 |
| any arm passes G-COST with the interval | | 0.25 |
| pilot cost per build run | USD 0.10-1.50 (T1-3's one-round build was 0.28; graph builds cost more) | n/a |

## 8. Budget (estimates and recommendations only; **the owner decides; nothing here is approved**)

| item | runs | per-run cap | item cap | basis |
|---|---|---|---|---|
| held-out author (section 6) | 1 session + 1 fix | n/a | USD 3.00 | H2's author cap; estimate, not approved |
| pilot | A0 and A1 on BD (6 runs) plus A0 and A4 on QD (2 runs), 1 repeat = 8 runs | USD 2.00 | **USD 5.00** | plan section 6 recommendation, not approved |
| main A/B | up to 90 + 24 conditional | USD 2.00 | **USD 40.00** | plan section 6 recommendation, not approved |
| verification | re-score from stored streams, no model call | 0 | 0 | |
| ceiling if all approved | | | USD 48.00 | |

**Stop rules.** Every `claude` call goes through a capped shim (pattern of `dogfood/t14_casko_shim.py`): ANTHROPIC_* stripped, never an API
key, `--max-budget-usd` per call, one append-only ledger line per call. Before a run starts: refuse if spent + 1.5 x the largest run cost so
far would pass the item cap. When projected spend passes 80 % of a cap before the arms finish, stop and ask the owner; never extend
silently. A usage limit stops the stage; the missing cells are NOT RUN, never a pass. **Cut order if the pilot projects the main stage above
its cap** (fixed now): drop the AS arms; then BD to 2 repeats; then QD to 2 repeats; BH and QH keep 3 repeats always. The pilot never touches BH or QH.

## 9. Freeze list and ordering

**Entry conditions** (plan section 3, restated): the owner has closed the spikes whose prompts or runners this work changes; a baseline test
run of the branch is recorded with its known failures; the owner approves the budget in writing.

**Order (binding):** this prereg committed alone -> WP0 telemetry -> WP2, WP3, WP4, WP6, WP8 behind flags, test-first -> **flags-off check**
(every brief, system prompt and MCP payload byte-identical to the pre-change tree on the BD fixtures, offline) -> runner `dogfood/cm_ab_run.py` (new),
scorer wrapper `dogfood/cm_ab_score.py` (new) and their self-tests on a fake `claude` -> freeze file and empty ledger committed -> held-out
author dispatched -> audit -> inputs manifest committed -> pilot -> owner sets the main cap from the pilot (a dated amendment, before any main
run) -> main -> independent re-score -> results. No amendment to this file after the freeze file exists; later notes go to the results.

**Freeze file** `docs/results/data/<run date>-context-mgmt/freeze.sha256` (new) lists: this prereg; the runner, scorer, shim and fake model;
`scripts/stream_metrics.py`, `scripts/audit_transcript_generic.py`, `dogfood/t14_casko_audit.py`; the brief directory; the QD id list; the round-1
scorer and set it reads; every tracked file under `lattice/` (must-list as in `docs/results/2026-10-23-dotnet-h2-prereg.md` section 8, so an added
library file is refused). The **inputs manifest** (after the author) hashes the app archive, HB1-HB3 with their hidden tests, and QH with its key.
Every entry must be a tracked, committed file (`git ls-files --error-unmatch`); the runner re-hashes all of them before any ledger line.

## 10. Risks, and what this cannot show

1. **Noise.** Three repeats on six tasks may not separate a 15 % effect; the interval rule reports that instead of claiming a win.
2. **Inert mechanisms.** Tasks that pass in one round never exercise WP4; single-node plans never exercise WP3 (G-INERT).
3. **A cap that hides findings** (WP2): G-JSON's per-rule-id replay is the guard; a miss kills the cap and keeps the item-boundary cut.
4. **Packs that are stale or wrong** make answers confidently wrong (WP8): the citation post-check and the dangling-item rule are the guard.
5. **Build cost unknown.** No measured per-run cost exists for the 2026-10-10 fixtures under today's CLI; the pilot exists to measure it.
6. **One model, one CLI version.** Results hold for sonnet on the recorded `claude --version` only.
7. **Author cap.** A sonnet author may hit USD 3.00 before the app and keys are complete; then BH/QH are NOT RUN and only dev rows exist.

## 11. Launch checklist (each line must be true, in order)

1. Owner approval of the author cap, the pilot cap and (later, after the pilot) the main cap is recorded in a dated amendment committed before the freeze.
2. Entry conditions of section 9 hold; WP0-WP8 code is merged to the A/B branch with its tests green; the flags-off check passes.
3. Runner and scorer self-tests pass on the fake model; a refused launch writes nothing to the ledger.
4. Freeze file and empty ledger committed; `--verify-freeze-only` passes from a **clean worktree** (`git status --ignored --short` empty).
5. Author dispatched after the freeze commit; transcript audited; inputs manifest committed.
6. Pilot run once (ledger-guarded); pilot costs written to the results draft; owner confirms or changes the main cap by amendment.
7. Main run once per cell; no re-run except after a crash before any metric with unchanged bytes.
8. Independent opus verifier re-scores from the stored streams; its counts replace the runner's if they differ.
9. Results doc: every prediction scored, blind and dev rows apart, misses and NOT RUN cells listed, flags left off where a gate missed.
