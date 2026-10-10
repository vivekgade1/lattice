# Results: multi-database checking, spec questions, incremental drafting (2026-10-09)

## Postgres, MySQL and ClickHouse

Lattice `e008353` (checker), `a5268fe` (raw API-key header). Apps: job-scheduler-go `multidb` (`a24aaa4`, harness `00dd1de`) and event-analytics-ch (`d6dc9ba`).

| | SQLite | Postgres | MySQL | ClickHouse (demo app) |
|---|---|---|---|---|
| Go job scheduler, spec v2 ABCDX + services | ✅ 154 cases, 64 s | ✅ 154 cases, 157 s (B skipped) | ✅ 154 cases, 131 s (B skipped) | n/a (not a CRUD fit) |
| Race mutant (row lock and live-run guard removed) | — | ❌ caught: C 1, X 1 | ❌ caught: C 8, X 2 | — |
| Event Analytics, CDX | — | — | — | ✅ 12 cases, 0.7 s; X notes the uniqueness bound as not applicable |
| Dedup-off mutant | — | — | — | ❌ caught: C 1, D 1 |
| Clone per scenario | file copy | 5 ms | 17 ms | 7 ms |

## `lattice spec ask`, pre-registered (`ed0b80b`)

20 questions on spec v2, Sonnet through the CLI, `--verify` against the spec-first Go build.

| | result | pre-registered bar |
|---|---|---|
| Specified questions answered correctly | **15/15** | ≥ 14/15 ✅ |
| Unspecified questions answered "not specified" | **5/5** | ≥ 4/5 ✅ |
| Made-up citations accepted | **0** (0 rejected, none made up) | 0 ✅ |
| Verified on the app | 13 agree, **2 "disagree" that are false**, 5 observed | every verification agrees ❌ |

**The 2 false "disagrees" were faults in the generated test scenarios, not in the app:**
- **Q06:** the setup job had no runnable command, so the run failed instead of succeeding before the cancel step.
- **Q13:** a `schedule-tick` call was sent without its `now` body and got 422.

In both, a **setup step** failed, not the step testing the claim. **Fix (follow-up):** count a failure as "disagrees" only when the claim step fails; a setup failure means "inconclusive".

**Cost:** 162 s and about $1.89 at list price (CLI) for all 20 questions with verification.

## `lattice spec draft --since`, pre-registered (`ed0b80b`)

**Base:** the drafted spec (`spec_gen.py`, after Q7). **Change:** `REQUIREMENTS` v1 → v2, with 4 planted edits. Opus through the CLI.

| Criterion | Result |
|---|---|
| Rule diff exact (2 changed, 1 added, 1 removed) | ✅ 62 unchanged, 2 changed (R44→R43, R46→R45), 1 added (R46, retry), 1 removed (R40, PATCH) |
| The combined spec validates | ✅ in 1 round, 253 s, about $1.08 |
| The cancel contradiction is reported | ✅ as question Q13: "R4 still says an executor may 'run and cancel jobs', but R45 now restricts cancel… Which holds?", with the newer rule as default |
| No checks of the removed rule remain | ✅ `patch_answers_405_and_changes_nothing` retired; old one-live-run checks replaced |
| Impact on the v1 app: failures only from changed or added rules, labelled | ❌ **blind**: the run crashed (given rows refused by the v1 app's unique index) |

- L011 also raised **2 false conflicts** (a public login answering good and bad credentials differently).
- **Both were fixed in `05f0348`:** refused given rows become a loud finding, and L011 skips public endpoints.
- **Post hoc** (same draft, impact re-run): **13 not_built_yet** (all linked to the changed or added rules), **1 contradiction** (`access__cancel__as_eve`, linked to R4 and R45: the planted contradiction), **0 regressions**, 0 conflicts.

## Reading

- **Databases:** server databases work end to end, and the race layer catches races on Postgres and MySQL.
- **`ask`:** answers were fully grounded, with 0 wrong answers on these 20 questions. The weak link is test-scenario generation for verification; the fix is simple and known.
- **Incremental drafting:** it did the hard part right, in one round: the exact diff, replaced and retired checks, and the contradiction raised as a question. The tool's robustness (the impact crash, the L011 precision) needed two fixes, now committed.
