# Results: Go bare-brief 2×2 (2026-10-09)

Pre-registration: `2026-10-09-go-bare-brief-prereg.md` (`fd4612d`). One build per cell. The blind judge is the unchanged spec, BCDX at 11a7032 with a deep X run (`--x-budget 200 --x-repeats 6`), plus the 4 HTTP race probes. gocheck (bbb6185) and a same-minute probe are reported separately.

## Primary measure: blind judge findings after ticket 5

| | prose `REQUIREMENTS.md`, no checker | Lattice spec + gate |
|---|---|---|
| **Opus** | ❌ **4** (X: 500s on concurrent duplicate `POST /users`, `POST /applications`, `PUT members` ×2) | ✅ **0** |
| **Sonnet** | ❌ **8** (X: 500s on concurrent `PUT members` ×2, `POST runs` ×3, `schedule-tick` ×2; D: `cron_expr "61 * * * *"` accepted) | ✅ **0** |

The race probes held 4/4 in every cell. They test the "at most one" outcomes, which every build held; the X findings are server errors on the losing requests.

**The pre-registered expectation held in direction:** for each model, the spec arm ends with fewer findings, and the gap is larger for Sonnet (8 vs 0) than for Opus (4 vs 0). With n = 1 per cell, this is descriptive only.

**In the build loop, the gate caught real bugs only for Sonnet.**
- Ticket 1: X 500s on concurrent member PUT, plus GO803 unbounded email and role.
- Ticket 3: X 500s on concurrent run creation.
- Both were fixed in the next round.
- Opus spec never failed a check round; it wrote concurrency-safe code from the start, after reading the spec (invariants, races in the checker output). Opus prose did not.

## Secondary measures

| arm | agent tokens | tool calls | agent time | rounds t1–t5 | src / test lines |
|---|---|---|---|---|---|
| Opus prose | 386k | 62 | 22 min | 1,2,1,1,4 | 1,661 / 2,062 |
| Opus spec | 439k (+14%) | 86 | 29 min | 2,2,2,1,1 | 1,811 / 1,144 |
| Sonnet prose | 537k | 162 | 29 min | 2,1,1,1,1 | 1,746 / 2,100 |
| Sonnet spec | 630k (+17%) | 224 | 48 min | 3,2,2,1,1 | 1,547 / 1,465 |

There were no orchestrator interventions in any arm.

## Layer A (gocheck) on the bare builds: held out, since gocheck was fixed before these existed

| arm | findings | true | false |
|---|---|---|---|
| Opus prose | 1 | 0 | 1: GO807 on a flag set inside the transaction that wrote nothing (a correlated condition, which gocheck documents it cannot see) |
| Sonnet prose | 5 | 3: GO803 email (regex with no length limit), `code_path`, `cron_expr` | 2: GO803 role (a hand `!=` check against the enum), `job_name` (a regex with `{1,100}`) |
| Opus spec / Sonnet spec | 0 (gated on A) | — | — |

Held-out precision is 3 of 6, so gocheck still needs work on hand-written bounds (enum comparisons, regex quantifiers) and on flags correlated across a transaction.

## Post hoc: what the spec does not check, and the bare spec arms skipped

| | worker loop (`server worker`) | scheduler loop | same minute after the run finished → no second cron run |
|---|---|---|---|
| Opus prose / Sonnet prose | yes / yes | yes / yes | holds / holds |
| Opus spec | **no** | env-var only | holds |
| Sonnet spec | **no** | **no** | **broken** (2 cron runs) |
| Earlier matched-brief Go builds (both) | yes | yes | holds |

These rules live only in the spec's `Unchecked` notes (the loops), or in prose the spec never turned into a scenario (the same-minute rule after a finish). Agents gated only on the spec built what the spec checks. **The spec needs a scenario for the same-minute rule, and a checkable form for the background loops.** Until then, the spec arm is safer on what the spec states but incomplete on what it leaves out.

## Reading

- **What Lattice adds:** with bare briefs, the spec plus the gate removed every blind judge finding in both models (12 vs 0), at +14–17% tokens. The matched-brief tie earlier was the instructions carrying Lattice's lessons, not the absence of an effect.
- **What it costs:** whatever the spec omits, the spec arm can omit too. The prose arms kept rules the spec drops.
