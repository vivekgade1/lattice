# Real-agent exercise, round 3 (five fixes, R3 retired): results

Date: 2026-10-14. Branch `np-fixes` (worktree `lattice-fixes`, from `next-phases` 2407eaf). Pre-registration:
`docs/results/2026-10-14-agent-run-3-prereg.md` (84f5110; scorer freeze addendum c85eaaa, before any
session). Rounds 1 and 2: `docs/results/2026-10-14-agent-run-results.md`, `...-run-2-results.md`. Raw data:
`docs/results/data/2026-10-14-agent-run-3/` (16 session directories with raw stream-json transcripts,
scores and manifests; `answers.md`; `summary.json`; the round-1 and round-2 re-score validations).
CLI backend under the owner's Claude login, no API key; same worlds, argv, prompt, caps, models
(`claude-sonnet-5-5`, `claude-haiku-5-5`, claude 2.1.299) and 12 sonnet + 4 haiku sessions as round 2.

## What was changed (the system under test)

Five fixes found by rounds 1-2 (commit 0dc3624; texts exactly as pre-registered): **F1** the server's
`initialize` instructions name `project_list`, `workspace_impact`, `contract_check`, `contract_impact` and
`linked_question_list` and say to call `project_list`, then BOTH `contract_check` and
`linked_question_list` for "has anything a provider changed affected this project?" (appended only with
more than one project; single-project servers byte-identical, proven by probes B1-B6); **F2** Studio chat
allow-lists the read-only multi-project tools (the rebuild list is unchanged); **F3** `consumer` defaults to
the active project; **F4** a `legend` key in the question list explaining `CHANGED*` and `{}`; **F5** the
spec-less error recognises a contract consumer. Scorer: the whole-answer R3 leaves the pass rule (post hoc,
stated in the prereg), R3c keeps "no claim of an open question", R9 reports default-consumer calls.

## Result (frozen scorer, as pre-registered)

**Round 3 PASSES its overall rule: 11 of 12 sonnet sessions satisfy every applicable item (needs >= 11).**
R1b 12/12, R5 16/16, R6 12/12 (A and B never disagreed), 0 tool errors, USD 1.22 total (cap 3), 4 turns
and 7-10 s per session.

| Item (sonnet unless stated) | Round 1 | Round 2 | Round 3 | Required |
|---|---|---|---|---|
| R1 used a notice/impact/check tool | 12/12 | 12/12 | 12/12 | 12/12 |
| R1b called `linked_question_list` | not scored | 11/12 | **12/12** | 12/12 |
| R2 names endpoint and finding (break) | 3/3 | 3/3 | 3/3 | 3/3 |
| R3b no "breaking" for a relied CHANGED | n/a | 6/6 | 6/6 | 6/6 |
| R3c no claim of an open question (clean, regress) | n/a | 6/6 (re-scored) | 6/6 | 6/6 |
| R4 dismissed reported as not open | 3/3 | 3/3 | **2/3** | 3/3 |
| R5 read-only, world byte-identical (all 16) | 16/16 | 16/16 | 16/16 | 16/16 |
| R6 no claim of an action | 3/12 (old regex) | 12/12 | 12/12 | 12/12 |
| R3 whole-answer (information from round 3) | 6/6 | 5/6 | 6/6 | not a pass item |
| R9 default-consumer calls (information) | | 0 | **0** | |
| Sonnet sessions satisfying everything | 3/12 | 10/12 (11/12 on the round-3 rule) | **11/12** | >= 11 |
| Cost / turns | 0.71 / 3-5 | 0.70 / 3-5 | 1.22 / 4 | caps |

Two applicable misses, both read as scorer brittleness on a human read of the answers:

| Session | Item | What it was |
|---|---|---|
| sonnet-dismissed-3 | R4 | the answer says the break is live and that `lq1` "has status `dismissed`" and "someone dismissed the note"; it never calls it open. The `NOTOPEN` regex needs "(was/been/is/already) dismissed", "not open" or "no open", so it did not match |
| haiku-break-1 (haiku, no pass rule) | R2 | endpoint, 200 -> 204 and the open `lq1` are all stated; `KIND` needs "200" and "204" within 60 characters and they are in different sentences |

## What changed in behaviour, and what did not

- **All 16 sessions took the identical path:** `project_list` -> `contract_check` -> `linked_question_list`
  (three sonnet dismissed sessions also passed `status: "all"`). Round 2's one real miss (skipping the
  question list) did not recur: R1b 12/12. The instruction F1 literally prescribes that sequence.
- **`workspace_impact` use fell from 11 of 12 (round 2) to 0 of 16.** The agents followed the instruction
  text closely. That is the fix working as written, and also a change nobody asked for: an agent now
  answers "has anything changed affecting me" without asking who else relies on the provider.
- **F3's default path was never exercised by a real session** (R9: 0 default calls in 16; every agent
  passed `consumer` explicitly), and F5 was not exercised (no agent called a spec tool on the consumer).
  F3 and F5 are covered by unit tests and the byte-compatibility probes only.
- **F4's legend caused no harm** (R3b 8/8 over clean and regress; no misuse of "any id").
- C-dismissed: every answer said the break is still live; all four called the note dismissed.
- Zero tool errors (round 1: one haiku error), no tool called that does not exist, nothing written.
- Two sessions cost USD 0.30 against USD 0.06 for the rest with the same four turns and tool sequence: a
  cache effect of the CLI, not behaviour.

## Findings

1. **The exercise passes, and the pass is not a clean improvement.** Under the round-3 rule round 2 also
   re-scores 11 of 12, so the headline number did not move. What moved is the real defect: round 2's R1b
   miss (1 of 12) is 0 of 12 in round 3, and tool errors went 0 -> 0. With 12 sessions per round that
   difference is not statistically distinguishable; the exercise can show a miss did not recur, not
   that the fix caused it.
2. **The prescribed path produced uniform behaviour.** The instructions name the sequence and sixteen
   agents took it: a strength (reliable) and the Goodhart risk the prereg named (instructions shaped to
   this prompt; different owner questions are untested).
3. **A pass item went the wrong way for a scorer reason a second time.** R4's `NOTOPEN` regex failed a
   correct answer on wording ("has status dismissed"). R3 was retired for this class of reason; `NOTOPEN`
   inside R4 shows the same brittleness. Not fixed here (a frozen scorer); a decision for any further round.
4. R3 retirement was post hoc and in the direction of passing; it did not matter this time (R3 6/6).
5. Studio's in-app chat was not exercised (F2 is tested by unit tests only); the change set was seeded.

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| Build before the freeze | 90 unit cases, V1/V2 on 33 stored transcripts, byte-compat B1-B6, 1010 tests, dogfood 435, the four layer-3 runners, two stale-reference fixes | all as pre-registered (re-run by the orchestrator) |
| Freeze held | hashes of the four frozen files before and after; runner's `verify_freeze` | identical |
| The 16 sessions | tool sequence and misses read from the raw transcripts; three answers read verbatim; the two misses read in full | as above |
| Cost and caps | `summary.json` | 1.2227; first three 0.654 (cap 1); no abort, no retry |
| Data hygiene | grep of 202 files for emails, tokens, account fields | none |

## What this does not show

- That an owner finds the answers useful; one prompt by the system's author; n = 3 per condition; the same
  two models; one project pair; a seeded change.
- That the fixes help in Studio's chat, or for questions phrased differently from this prompt.
- That F3 (defaulting) works with a real agent (never exercised).
