# Spike 3: self-improving Lattice (it gets better the more it is used)

Status: research and plan. No code.

## Question

Can Lattice improve itself from use: add modules/packages as new systems are built with it, and find where
performance and reliability are weak, then fix them?

## What exists

| Piece | Where | Relevance |
|---|---|---|
| Studio improves Lattice, twice | `docs/results/2026-10-10-studio-improves-lattice.md`, `...-run2.md` (run 2: draft 161 s, passed round 1, about $0.63) | Proof the loop request -> change set -> rebuild worktree -> gates works on Lattice itself. n=2, one change type. |
| Lattice's own spec for Studio | `dogfood/studio` (159 cases, 6/6 + state mutants) | Acceptance test for changes to Studio. |
| Pre-registered mutants | `docs/results/*-prereg.md` | The project's method for proving a gate can catch a regression. |
| Durable state and resume | `lattice/studio/state.py` (API v1.10) | A place where per-run events could be recorded. |
| Phase metrics | `metrics/phases.jsonl` in the Streak repo (`~/Documents/streak`) | Existing per-phase measurements, per project, not in Lattice. |
| Backlogs written by hand | handoff open items; Streak backlog (MySQL `Z` instants, missing table aborts check, DSL gaps, answers not recorded) | The "signals" today, found by a human reading output. |
| Gate profile | `docs/results/2026-10-11-gate-profile.md` | Example of a performance finding that took a manual experiment. |
| Marketplace design | spike 2 | The place improvements of the "new technology" kind land. |

## What is missing

1. **A signal store.** Nothing records, across projects and runs: which rule kinds could not be expressed
   (`Unchecked`), which code was `unread`, which layer was slow, how many rounds a node needed, which
   gate failed and why, tokens and cost per node.
2. **A ranker** that turns recurring signals into candidate improvements.
3. **A proposal path** from candidate to either a Lattice change (via `change_propose` on Lattice's own
   spec) or a module (spike 2).
4. Handoff open item 5: rebuild cost accounting ignores cache tokens (understates by ~75x in run 2), so
   cost signals would be wrong today.

## Findings

- "Self-improving" here should mean **self-proposing, human-merging**. The existing rule (never merge a
  rebuild automatically) stays. The loop's value is finding and drafting improvements, not shipping them.
- The existing acceptance machinery (dogfood spec + mutants + full gates) is what makes self-change
  trustworthy. Improvements to the checker itself must not weaken a gate or edit a spec to make a check
  pass (already a rule of the rebuild skill).
- Three families of improvement, with different evidence needs:
  1. **Capability gaps** (language cannot express X; no recogniser for library Y) -> language change
     (follows `docs/lattice-language-rules.md`, all targets or gated) or a module.
  2. **Performance** (slow layer, slow node) -> measured experiment, as the gate profile was.
  3. **Reliability** (flaky gate, orphan processes, wrong status, cache bugs) -> bug found by mutants or
     real runs; fix plus a new mutant.
- n is small (one real new project, Streak). Anything learned from one project risks overfitting.

## Design (recommended)

1. **Event log** (append-only, per project, local; optional shared export): one record per build node,
   gate run, rebuild round, check finding class, `unread`/`unchecked` reason, timings by layer, tokens
   including cache, outcome. Plain JSONL, schema versioned.
2. **Weekly or on-demand "improve" analysis** (an epg graph, run by the owner, not scheduled by default):
   reads logs, clusters recurring signals, ranks by frequency x cost, drafts at most N proposals with
   evidence links.
3. **Each proposal** is one of: Lattice change set (own spec) / module draft (spike 2) / experiment to
   run (pre-registration draft). Owner accepts or rejects in Studio.
4. **Implementation** by the existing rebuild loop in a worktree; acceptance by dogfood spec + mutants.
5. **Report**: before/after on the signal that triggered it.

## Plan

| Phase | Work | Exit (objective) |
|---|---|---|
| I-R | Hand analysis: take Streak backlog + gate profile + handoff open items, rank by hand using the proposed method | The owner agrees the top 3 match their own priority; method is written down |
| I-1 | Define the event schema from the fields already available in reports and state (no code) | Schema doc reviewed; list of fields not currently available (cache tokens etc.) |
| I-2 | Close the accounting gap (open item 5) and G1 timings (spike 1a) so events are trustworthy | Separate small change set, pre-registered |
| I-3 | One improvement end to end from a logged signal, human merged | Before/after shown; n=1, labeled as such |
| I-4 | Second, different improvement, including one that should be refused | Beyond n=1 before any general claim |

## Risks

- Garbage-in: wrong cost or timing signals produce wrong priorities (see missing item 4).
- Goodhart: an improvement loop optimizing a metric can weaken checks. Guard: gates and mutants are
  outside the loop's write scope; improvements cannot touch their own acceptance tests without owner
  approval.
- Privacy: logs from user projects may contain business data. Keep local by default; export is opt-in and
  redacted.
- Over-automation: the owner's trust model is human review. Do not schedule unattended merges.

## Open decisions for the owner

1. Log location and retention (per project vs one global store).
2. Should proposals be allowed to target the checker (core), or only modules and docs at first?
3. Cost ceiling per improve run.
