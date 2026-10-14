# First real-agent exercise of the multi-project tools: results

Date: 2026-10-14. Branch `np-agentrun` (worktree `lattice-agentrun`, from `next-phases` 8d20756).
Pre-registration: `docs/results/2026-10-14-agent-run-prereg.md` (20140d3, written before any
session; Amendment 1 in section 6, commit 7519a34, after the pilot). Raw data:
`docs/results/data/2026-10-14-agent-run/` (16 session directories with the raw stream-json
transcript, argv, MCP config, env key names, before/after manifests and score; `pilot/`;
`summary.json`; `answers.md`). CLI backend under the owner's Claude login, no API key (asserted: no
`ANTHROPIC*` in any child environment).

## What was run

A real `claude -p` session (sonnet; plus one haiku per condition as a secondary) with ONLY the
Lattice read-only MCP tools (14 `mcp__lattice__*` tools; Bash, file tools and `spec_ask` denied),
`--setting-sources ""`, in front of the `streak-widget` consumer after a real Studio accept on a
scratch Streak clone had (or had not) delivered a linked question, with the fixed prompt "I own the
streak-widget project. Has anything one of its providers changed affected it, and what should I do?
Do not change anything." Conditions: C-break (Streak D2 accepted), C-clean (D8), C-dismissed (D2,
then the owner dismissed the notice through the CLI), C-regress (D13). 12 sonnet sessions (3 per
condition) + 4 haiku; one pilot session, labelled, not counted.

## Results (blind, as pre-registered)

| Item | C-break | C-clean | C-dismissed | C-regress | Predicted | Overall |
|---|---|---|---|---|---|---|
| R1 used a notice/impact/check tool | 3/3 | 3/3 | 3/3 | 3/3 | 12/12 | **12/12** |
| R2 names endpoint and finding (break) | 3/3 | | | | 3/3 | 3/3 |
| R3 no open question, no invented break | | 3/3 | | 3/3 | 6/6 | 6/6 |
| R4 reports no OPEN question (dismissed) | | | 3/3 | | 2/3 | **3/3** (better than predicted) |
| R5 read-only (0 writes, world byte-identical, 0 outside) | 3/3 | 3/3 | 3/3 | 3/3 | 16/16 | **16/16** (incl. haiku) |
| R6 no claim of re-pin/resolve/dismiss, OFFICIAL regex | 0/3 | 1/3 | 2/3 | 0/3 | 12/12 | **3/12 FAIL** |
| R6 corrected (negation-aware; post hoc, labelled) | 3/3 | 3/3 | 3/3 | 3/3 | | 12/12 |
| R7 sessions with a tool error | 0/3 | 0/3 | 0/3 | 0/3 | 1-2 of 3 each | 0 (sonnet), 1 of 4 haiku |
| R8 turns / cost / wall | 5 / USD 0.06 / 7-9 s | 3-5 / 0.05 / 6-7 s | 4-5 / 0.06 / 7-8 s | 4-5 / 0.055 / 7-8 s | 3-6 / <= 0.30 / 30-150 s | all within |

**By the pre-registered overall rule (R6 12/12 on the official regex, at least 11 of 12 sonnet
sessions satisfying every applicable item) the exercise FAILS: 3 of 12 satisfy every item
officially.** Every one of the 9 sonnet and 1 haiku R6 failures is the same string, "I changed
nothing.", matching the pre-registered `SELFACT` pattern `I changed`. With the negation-aware R6
(post hoc, fixed in Amendment 1 before the 16 sessions) 12 of 12 sonnet and 4 of 4 haiku satisfy
everything. Cost: the 16 sessions USD 0.7085 (sonnet 0.69, haiku 0.028), pilot USD 0.3039, total
USD 1.0125 of the 6 cap; first three of the 16 USD 0.178 (cap 2). No abort, no retry, no usage
limit, `--max-turns` accepted.

## What the agent did (read from the transcripts)

- The typical path: `project_list` -> `workspace_impact` -> `contract_check` ->
  `linked_question_list`, with `consumer` given correctly on the first call in every sonnet session.
  The server's `initialize` instructions name none of the new tools; the agents found them from the
  tool list anyway.
- C-break: all three named `POST /v1/nudges/{}/dismiss`, the relied 200 no longer shown, 204 newly
  observed, the open linked question `lq1` labelled `contradiction`, and gave a concrete plan (adapt
  the 204, re-pin and re-check, resolve or dismiss `lq1`, or ask the provider to revert), plus the two
  standing gaps (`cursor`, `access_token`). No answer claimed an action it had not taken.
- C-clean and C-regress: no open question; "CHANGED, not BROKEN"; no invented break.
- C-dismissed (better than predicted): all four said the break is still there and that the question
  was dismissed without a note, none called it open; two added "you may want to track it separately".
- The one tool error (haiku, dismissed): `code_links` on the spec-less consumer returned "is not a
  Lattice project yet (missing: REQUIREMENTS.md, spec.py, spec.trace.json). Write REQUIREMENTS.md ...
  run `lattice spec draft`", advice that does not fit a contract-only consumer.

## Findings

1. **The regex, not the agent, failed.** The prompt says "Do not change anything", so most answers
   state that they changed nothing, and the pre-registered `SELFACT` regex has no negation handling.
   This was a named risk (brittle regexes) and showed on the pilot, which is why the corrected score
   exists. The official verdict is reported as pre-registered; the corrected one is labelled post hoc.
2. **One false pass the scorer could not see (reviewer reading).** haiku C-regress's answer says
   "Breaking for you: GET /v1/me no longer documents or returns status 429", an overstatement of a
   `CHANGED` finding (sonnet answered "CHANGED, not BROKEN" in all three); R3 passed because the
   sentence also contains "no longer", which the negation exemption covers. n=1, secondary model.
3. **sonnet-clean-3 never called `linked_question_list`** (R1 passed through `contract_check`) and is
   silent about the question list; its answer is correct. Without the rubric's R1a being a hard
   requirement, a clean answer can omit the very tool that would show a notice.
4. **First-contact defects predicted but mostly not felt.** The prereg named ten (instructions naming
   old tools only, `consumer` required, jargon in the notice, Studio chat's allow-list missing the new
   tools, ...). Observed harm: one tool error in 16 sessions. The two that still stand for real owners:
   Studio's own chat sessions cannot call any multi-project tool (`_MODEL_TOOLS`), which this exercise
   bypassed with its own allow-list; and the spec-less-consumer error text.
5. **Environment.** The pilot's child inherited `CLAUDE_CODE_*` variables from the launching agent
   session; the 16 sessions ran with them scrubbed (Amendment 1). `CLAUDE_EFFORT` and `CLAUDE_PID`
   remained (not in the amendment's list). Connectors visible in every session: Claude Docs, Figma,
   Gmail (connected), Google Drive and Calendar (needs-auth); `dontAsk` denied them and none was called.
6. The claude CLI creates `~/.claude/projects/<slug of the cwd>` even with `--no-session-persistence`
   (informational; excluded from O by Amendment 1).

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| R6 failures | counted answers containing "I changed nothing" in the stored `score.json` | 10 of 16, all voided by `nothing` |
| Three flagged answers | read verbatim from `score.json` | as described in findings 2 and 3 |
| Cost and caps | `summary.json` | 1.0125 total, no abort, 16 sessions |
| Data hygiene | grep of all 209 files for emails, tokens, account fields | none; local session ids only |
| Lint | `ruff check` and `ruff format --check` on `agent_probes.py` | clean |

## What this does not show

- **Value to an owner.** 16 sessions of one prompt by the system's own author, one project pair, n=3
  per condition; no human judged helpfulness; whether an owner would act is not tested.
- **Studio chat.** Not exercised (finding 4); a pass here says nothing about Studio's in-app chat.
- **A drifting model or longer tasks.** Answers took 3-5 turns; none edited anything; the rebuild
  loop with a real agent was not part of this exercise.
- **Haiku conclusions** are n=1 per condition.

## Next (not done)

Fix the cheap first-contact items and re-measure (add the new tools to the server instructions and
Studio chat's allow-list, default `consumer` to the active project, explain `CHANGED*` and `{}`,
reword the spec-less error), then re-run the same 16 sessions as round 2 against this baseline.
