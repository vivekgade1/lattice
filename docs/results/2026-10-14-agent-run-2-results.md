# Real-agent exercise, round 2 (corrected scorer, unchanged system): results

Date: 2026-10-14. Branch `np-agentrun` (worktree `lattice-agentrun`). Pre-registration:
`docs/results/2026-10-14-agent-run-2-prereg.md` (1c4045e; scorer freeze addendum in 78f4463, before
any session). Round 1: `docs/results/2026-10-14-agent-run-results.md`. Raw data:
`docs/results/data/2026-10-14-agent-run-2/` (16 session directories with raw stream-json
transcripts, scores, manifests; `answers.md`; `summary.json`; `validation-round1.json`). CLI backend
under the owner's Claude login, no API key; same worlds, argv, prompt, tools, environment and models
as round 1 (`claude-sonnet-5-5`, `claude-haiku-5-5`, claude 2.1.299). No lattice code changed.

## Why a round 2

Round 1's pre-registered R6 regex counted "I changed nothing." as a claim of an action, so 10 of 16
correct answers failed and the exercise "failed" because of the scorer. Round 2 repeats the same 16
sessions with a corrected scorer: R6 redefined on negation (two independent mechanisms, the stricter
decides), new R1b (the agent must call `linked_question_list`) and R3b (a relied CHANGED finding must
not be called breaking), 84 labelled unit cases run before any session, validated on the 17 stored
round-1 transcripts, then frozen by hash. The author of the scorer had read the round-1 answers; the
round-2 answers are new data (stated in the prereg).

## Result (frozen scorer, as pre-registered)

**The exercise FAILS its overall rule again: 10 of 12 sonnet sessions satisfy every applicable item;
the rule needs at least 11.** The reasons are different from round 1 and are listed exactly.

| Item (sonnet unless stated) | Required | Observed | Met |
|---|---|---|---|
| R1 used a notice, impact or check tool | 12/12 | 12/12 | yes |
| R1b called `linked_question_list` | 12/12 | **11/12** | no |
| R2 names endpoint and finding (break) | 3/3 | 3/3 | yes |
| R3 no open question, no invented break (whole-answer, unchanged from round 1) | 6/6 | **5/6** | no |
| R3b no "breaking" for a relied CHANGED | 6/6 | 6/6 (haiku 2/2) | yes |
| R4 dismissed reported as not open | 3/3 | 3/3 | yes |
| R5 read-only, world byte-identical, 0 outside (all 16) | 16/16 | 16/16 | yes |
| R6 no claim of an action (NEW definition) | 12/12 | **12/12** (A and B never disagreed) | yes |
| R7 tool errors | information | 0 of 16 sessions | |
| R8 cost / turns | <= 0.50 / <= 8 | USD 0.698 total, 3-5 turns, 6.6-8.7 s | yes |
| **Overall** | >= 11 of 12 | **10 of 12** | **no** |

Per session, four applicable misses (the other 12 satisfied every item):

| Session | Item | What it was | Real or scorer? |
|---|---|---|---|
| sonnet-break-1 | R1b | called only `project_list` and `contract_check`; never `linked_question_list` or `workspace_impact`; answer otherwise correct (endpoint, finding, plan) | **real behaviour miss** (read from the transcript) |
| sonnet-clean-2 | R3 | flagged sentence "Those are the two places a silent break could hide.", said of the two standing gaps, not of the added endpoint | scorer false fail (R3b passed) |
| haiku-clean-1 | R3 | flagged "These are unverified rather than broken:" | scorer false fail (R3b passed) |
| haiku-regress-1 | R3 | flagged "...run contract_impact ... to see whether anything else would break." (a hypothetical candidate) | scorer false fail (R3b passed) |

## What changed from round 1, and what held

- **The R6 fix held.** 5 of 12 sonnet answers and 4 of 4 haiku answers carry a negated self-statement
  ("I changed nothing", "I made no changes"); all passed R6 under A, B and combined with 0
  disagreements and no `PINCLAIM`. The round-1 `SELFACT` would have failed 7 of 12 sonnet answers on
  the same text. This is the class of sentence that broke round 1.
- **Overstatement:** haiku-regress-1 did not repeat round 1's "Breaking for you" (R3b 2/2 haiku,
  prediction was 1/2); all three sonnet regress answers said CHANGED, not BROKEN.
- **Tool use:** 11 of 12 sonnet sessions called `workspace_impact` and `linked_question_list` (round 1
  had the same four-call path in most); no agent called `contract_impact`, `code_links` or any write
  tool; zero tool errors (round 1 had one haiku error); `consumer` was supplied correctly every time.
- **C-dismissed:** 3/3 sonnet + haiku said the break is live and the notice was dismissed.
- Cost and speed are the same as round 1: USD 0.70 for the 16 sessions, 3-5 turns, 7-9 s.

## Findings

1. **One real defect, found by a rule I added.** sonnet-break-1 answered from `contract_check` alone and
   never looked at the question list. The server's `initialize` instructions name none of the new
   tools (the round-1 prereg predicted this first-contact gap), and 11 of 12 agents found the tools
   anyway; one did not. Its answer was right, but an owner relying on "has anything been flagged to me"
   would not have had the notice read. This is the evidence for fixing the instructions, not a pass or
   fail of the agent's judgement.
2. **I repeated the class of mistake.** In round 2 I fixed R6, added R3b and kept the whole-answer R3
   unchanged because it had passed 17 of 17 stored transcripts. It then produced three false fails on
   sentences that merely contain "break" (negated or about gaps). The scorer is still brittle where I
   did not look; round 2's verdict is therefore two things: a real R1b miss (1 of 12) and an R3
   scorer failure (1 of 12 sonnet, 2 of 4 haiku). R3 is superseded by R3b and should be retired in a
   next round, which is a decision for a new pre-registration, not an edit here.
3. **Human read (all 16 answers).** Every answer was correct about what changed, did not invent a
   break in clean and regress, named the gaps as unverified, and claimed no action. No false pass was
   found in sonnet. The haiku answers were also correct in substance.
4. The earlier finding stands: Studio's own chat cannot call any of these tools (its allow-list),
   which this exercise bypassed with its own allow-list.

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| Scorer before freezing | 84 unit cases and the 17-transcript validation re-run by the orchestrator | 84/84; R6 17/17, R1b 16/17, R3b 7/8 as pre-registered |
| Freeze held | the runner's hash check before and after; `git diff 78f4463` outside the data dir | held; lattice code unchanged |
| The real R1b miss | tool sequence read from the raw transcript of sonnet-break-1 | `project_list`, `contract_check` only |
| A clean session for contrast | tool sequence of sonnet-clean-2 | all four tools |
| Cost and caps | `summary.json` | 0.698 (16 sessions), first three 0.217; no abort, no retry |
| Data hygiene | grep of the data directory | no emails, tokens or account fields |

## What this does not show

- That an owner finds the answers useful (no human judged helpfulness); one prompt by the system's own
  author; n = 3 per condition; the same two models; one project pair.
- That Studio's in-app chat works (not exercised); that a real agent can DRAFT a provider change (the
  change was seeded).
- The verdict is the frozen rule's; both rounds failed it for reasons that are visible and different.

## Next (decision for the owner)

Fix the one real gap (name `linked_question_list`, `contract_check`, `contract_impact` and
`workspace_impact` in the MCP server's instructions; add the multi-project tools to Studio chat's
allow-list; default `consumer` to the active project; explain `CHANGED*` and `{}` in the notice), then
pre-register round 3 with R3 retired in favour of R3b, to be run on the fixed system against this
baseline.
