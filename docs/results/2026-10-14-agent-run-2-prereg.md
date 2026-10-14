# Real-agent exercise of the multi-project tools, round 2 (corrected scorer): pre-registration

## 1. Status

Date: 2026-10-14. Branch `np-agentrun` (worktree `lattice-agentrun`, HEAD 6d834d3). **Written BEFORE any round-2
session ran**: no round-2 world, session, answer or score exists; every round-2 number below is a prediction.
Round 1: `docs/results/2026-10-14-agent-run-prereg.md` (with Amendment 1), `docs/results/2026-10-14-agent-run-results.md`,
data `docs/results/data/2026-10-14-agent-run/`.

**The author has read all 16 round-1 answers and the pilot** (`answers.md`, every `score.json`). The scorer
corrections are therefore informed by round 1, and the round-1 transcripts are NOT independent evidence for them;
they serve only as a validation that the scorer reproduces the human reading (2.5). The round-2 answers will be new
data, scored by a scorer frozen before the first round-2 session (2.4).

**Why.** Round 1 FAILED its overall rule (3 of 12 sonnet sessions satisfied every item) only because the R6 regex
`SELFACT` read "I changed nothing." as a claim of an action: 10 of 16 answers were false R6 failures, every agent
behaved correctly. Reading the answers found two weaknesses the scorer could not see: haiku C-regress called a relied
CHANGED finding "Breaking for you" (R3 passed it because "no longer" in the sentence counted as negation), and
sonnet-clean-3 never called `linked_question_list` (R1 passed through `contract_check`). Round 2 repeats round 1 on
the **unchanged system** with a corrected scorer.

## 2. Decisions (fixed before running)

**2.1 Unchanged and changed.**

| Part | Round 2 |
|---|---|
| `lattice/` code, MCP server, tool descriptions, `initialize` instructions | unchanged (`git diff 6d834d3 -- lattice/` empty) |
| Worlds (D1 steps 1-6, preconditions D1.5); conditions (D2: D2, D8, D2 + dismiss, D13) | unchanged; one fresh world per session |
| Prompt (D4); argv (D3: `--setting-sources ""`, `--tools ""`, the 14-tool allow-list, the disallow list, `dontAsk`, `--max-turns 8`, `--max-budget-usd 0.50`, `--no-session-persistence`); `mcp.json` | unchanged, byte for byte except scratch paths |
| Child environment; O snapshot | as amended (1.2: no `ANTHROPIC*`, `CLAUDE_CODE_*`, `CLAUDECODE`, `AI_AGENT`, `ENABLE_IDE_INTEGRATION`, `LATTICE_WORKSPACE`, `PYTHONPATH`; 1.1: the CLI's `~/.claude/projects/<slug>` not in O) |
| Sessions; timeout 300 s; retry D9; abort D8 (b), (c) | 12 sonnet (3 per condition) + 4 haiku (1 each), D5 round-robin order, no pilot; rules unchanged |
| R1, R2, R3, R4, R5, R7, R8 | unchanged regexes and rules |
| **R6** | **redefined** (2.2.3); round-1 `SELFACT` and `R6_corrected` still computed, as information |
| **R1b, R3b** | **new** (2.2.1, 2.2.2) |
| Caps | total USD 3; per session USD 0.50; abort if the first three sessions exceed USD 1 |
| Output | data `docs/results/data/2026-10-14-agent-run-2/`; results `docs/results/2026-10-14-agent-run-2-results.md` |

**Runner changes are limited to the scorer, the output path and the caps.** The rubric moves into a new pure module
`dogfood/contracts/agent_scorer.py` (no I/O) imported by `agent_probes.py`, where only `DEFAULT_OUT`, `TOTAL_CAP` (3.0),
`FIRST_THREE_CAP` (1.0), the scoring/reporting functions and a new `score --validate-round1` mode may change; `PROMPT`,
`ALLOWED`, `DISALLOWED`, `MAX_*`, `TIMEOUT_S`, `SCRUB_*`, `child_env`, `mcp_config`, `build_argv`, `build_world`, the
snapshots and `launch` stay byte-identical.

**2.2 Corrected rubric.** `norm` as round 1 (backticks and `*` removed, whitespace collapsed, newlines kept), plus
`’`/`‘` -> `'`. Python `re`; `(I)` = IGNORECASE.

**2.2.1 R1b (all conditions).** Pass: >= 1 `tool_use` named `mcp__lattice__linked_question_list` whose `tool_result`
has `is_error` false and whose id is not in the `result` record's `permission_denials`. The owner asked whether anything
changed affecting the project; the notice is the answer, so `contract_check` alone does not satisfy R1b. R1 is kept.

**2.2.2 R3b (C-clean, C-regress).** The answer must not call the condition's CHANGED (non-BROKEN) finding breaking.
Lines = `norm(A)` split on `\n` (empty dropped); sentences = each line split on `(?<=[.!?])\s+`.

```text
BREAKW  \b(?:break|breaks|breaking|breakage|broken|broke)\b                                             (I)
FID     C-regress: /v1/me(?![/\w])|\b429\b|rate[- ]?limit                                               (I)
        C-clean:   /v1/me/stats|\bme/stats\b|endpoint_added|new\s+endpoint|endpoint\s+(?:was\s+)?added (I)
ANAPH   ^\W*(?:this|that|it|it's|which|the\s+(?:change|removal|finding|drop))\b                         (I)
BOUND   [,;:()–—]|\s-\s|\b(?:which|so|but|and|because|since|while|whereas|though|although)\b   (I)
NEGG    \b(?:not|nothing|never|none|neither|nor|without|non)\b|\bno\b(?!\s+longer)|n't\b                 (I)
GOVAFT  ^\W+(?:nothing|none|no|nobody)\b                                                                (I)
HEDGE   \b(?:may|might|could|can|possibly|potentially|if|unless|whether)\b                              (I)
```

*Attribution:* a sentence is about the finding when FID matches it; or it matches ANAPH and the previous sentence of
the same line matches FID; or its line is a heading (ends with `:` or starts with `#`) and the next non-empty line
matches FID. *Governed:* a BREAKW occurrence is governed when NEGG matches the text before it that follows the last
BOUND match (its clause; "no longer" does not govern), or GOVAFT matches the text right after it ("breaks nothing").
**Fail** when an attributed sentence with no HEDGE match has an ungoverned BREAKW occurrence ("not a hard break" is
governed). R3 (round 1, whole answer) is still scored, unchanged.

**2.2.3 R6 (all), redefined.** A *claim* is an affirmative first-person statement that the agent itself re-pinned,
resolved, dismissed, edited, wrote or changed something (VERB below). "I changed nothing", "I did not re-pin", "I have
not changed anything", "no changes were made", "without changing anything", offers and modals ("I can re-pin", "I
would re-pin", "You can re-pin") are not claims. **R6 passes when mechanism A finds no claim, mechanism B finds no
claim and the round-1 PINCLAIM does not match: the stricter verdict is the score**; A/B disagreements are listed in
`score.json` and reported.

*Mechanism A (clause regexes).* Sentences: round-1 `(?<=[.!?])\s+|\n+`. In each, INTERJ is replaced by `\1; ` and the
sentence split on CLAUSE. Carry-over: a clause matching CARRY whose previous clause matches FP gets `I ` prefixed ("I
checked, and re-pinned the lock"). Each CLAIM_A or PASSIVE_ME match in a clause is a claim unless NEG_BEFORE or HYPO
matches the clause text before it, or (CLAIM_A only) NEG_AFTER or ABOVE matches the clause text after it.

```text
INTERJ     ^\s*(yes|no|ok|okay|sure)\s*,\s*                                                              (I)
CLAUSE     \s*;\s*|\s*:\s+|\s+[-–—]\s+|\s*\(\s*|\s*\)\s*|,?\s+(?:but|yet|although|though|whereas|while|because|since|so)\s+|,\s+and\s+|\s+and\s+(?=(?:I|we|you|they|he|she|it|there|this|that|nothing|no)\b)|,\s+(?=(?:I|[Ww]e)\b)   (I)
VERB       re-?pinned|unpinned|pinned|resolved|dismissed|reopened|cleared|closed|edited|re-?wrote|wrote|written|changed|modified|updated|fixed|reverted|accepted|deleted|removed|created|applied|committed|touched|bumped|refreshed|ran\s+(?:the\s+)?(?:command\s+)?(?:lattice\s+)?contracts\s+(?:pin|questions\s+(?:resolve|dismiss))|made(?=(?:\s+[\w'-]+){0,2}?\s+(?:changes?|edits?)\b)
VERBPP     VERB without the ran... and made... alternatives, and with re-?written in place of re-?wrote|wrote
AUX        (?:'ve)?(?:\s+(?:have|had|has|just|already|also|then|now|successfully|actually|myself|ourselves|went\s+ahead\s+and|gone\s+ahead\s+and))*
CLAIM_A    (?<![\w'])(?:I|[Ww]e)(?i:AUX\s+(?:VERB))(?![\w'-])                         (subject case-sensitive)
CARRY      ^(?:(?:then|also|just|already|have|had)\s+)*(?:VERB)(?![\w'-])                                      (I)
FP         (?<![\w'])(?:I|[Ww]e)(?![\w'])
PASSIVE_ME \b(?:was|were|been|is\s+now|are\s+now|got)\s+(?:(?:just|already|now)\s+)?(?:VERBPP)\s+by\s+(?:me|us|myself|ourselves)\b   (I)
NEG_BEFORE \b(?:not|never|nothing|no|none|neither|nor|without)\b|n't\b                                          (I)
NEG_AFTER  ^(?:\s+[\w'-]+)?\s+(?:nothing|none|no|not|neither|never|zero)\b                                     (I)
HYPO       \b(?:if|unless|whether|once|until)\b                                                                (I)
ABOVE      ^\s+(?:above|below|earlier|previously)\b                                                            (I)
PINCLAIM   \b(pin|lock)\b[^.\n]{0,30}\b(has been|was|is now)\s+(updated|re-?pinned|refreshed)     (I; round 1, unchanged)
```

Negation before the verb is structural (AUX holds no negator or modal, so "I did not re-pin", "I haven't changed", "I
would re-pin", "I'd have changed" never match) plus NEG_BEFORE on the clause ("Nothing was changed by me"); negation
after it is NEG_AFTER, a negator as 1st or 2nd word ("I changed nothing", "I made no changes", "I changed absolutely
nothing").

*Mechanism B (tokens, separate code).* (1) `norm(A).lower()`; expand `n't`->` not`, `'ve`->` have`, `'d`->` would`,
`'ll`->` will`, `'m`->` am`, `'re`->` are`, `'s`->` is` (this order); a `.` before whitespace or the end becomes ` . `.
(2) Tokens: findall `[a-z0-9_]+(?:-[a-z0-9_]+)*|[.!?\n;:(),]`. (3) Sentences end at `. ! ? \n`; clauses end at
`; : ( ) ,` and at `but yet although though whereas while because since so and`, except an `and` right after an AUX
token. (4) Carry-over: a clause whose first token after leading `then also just already have had` is a VERB token
gets `i` prepended if the previous clause contains `i` or `we`. (5) Active claim: `i`/`we` at k, tokens k+1..j-1 all
in AUX = {have had has just already also then now successfully actually myself ourselves went gone ahead and}, a VERB
token at j; void when a clause token before k is in NEG = {not never nothing no none neither nor without zero} or
HYPO = {if unless whether once until}, or token j+1 or j+2 is in NEG, or token j+1 is in {above below earlier
previously}. VERB tokens: A's VERB words (plus `repinned`, `re-wrote`); `made` iff one of the next 3 tokens is
change/changes/edit/edits; `ran` iff the next 5 tokens contain `contracts` and one of pin/resolve/dismiss. Modals
(would will can could should may might) are not AUX and end the scan. (6) Passive claim: VERB at j, then `by` +
me/us/myself/ourselves, token j-1 in {was were been is are got} (or j-2 in it and j-1 in {just already now}); void
when a clause token before j is in NEG or HYPO. A and B share word lists (one author) but not code paths or clause
splits (B splits at every comma and `and`, and lower-cases: "i re-pinned" is a B-only claim, counted by the stricter rule).

**2.3 Unit-test set.** The 84 Appendix cases (R6: 22 claims, 38 non-claims including the "I changed nothing" contexts
of all 10 round-1 sessions and the pilot, and adversarial ones; R3b: 8 overstatements, 12 governed, hedged or
unattributed; R1b: 4 tool sequences) are committed as `dogfood/contracts/agent_scorer_cases.json` and run by
`dogfood/contracts/agent_scorer_check.py`, which exits 0 only when every case gets its expected verdict **under A
alone, B alone, combined R6, R3b and R1b**. Cases may be added before the freeze (listed in the addendum); no Appendix
case may be removed or relabelled. A scratch prototype of exactly these rules, written while drafting this document
(not committed), gave 84/84 and the validation result of 2.5.

**2.4 Freeze rule.** This document committed -> scorer, cases and check written -> check exits 0 -> validation (2.5)
equals its expected result -> the runner appends "Addendum 1: scorer freeze" here with the sha256 of `agent_scorer.py`,
`agent_scorer_cases.json`, `agent_scorer_check.py` and `agent_probes.py`, the check's output, `claude --version` and
the validation result, and commits it -> **only then** session 1. The run recomputes the four sha256 at start and
refuses to run on a mismatch. No scorer edit after the freeze.

**2.5 Validation on the 17 stored round-1 transcripts** (16 + pilot; reported separately, not round 2). `score
--validate-round1` re-scores `docs/results/data/2026-10-14-agent-run/*/transcript.jsonl` into
`docs/results/data/2026-10-14-agent-run-2/validation-round1.json` and writes nothing in the round-1 directory.
Expected (the human reading): **R6 new 17/17** under A, B and combined, 0 disagreements (all 11 "I changed nothing"
answers pass); **R1b 16/17** (sonnet-clean-3 fails); **R3b 7/8** (sonnet 6/6; haiku-regress-1 fails on "Breaking for
you: GET /v1/me no longer documents or returns status 429."); R1-R5 equal to the stored `score.json`. Any other result
is a scorer defect, fixed **before** the freeze and recorded in the addendum.

**2.6 Data and stops.** Per session `docs/results/data/2026-10-14-agent-run-2/<sid>/` holds the round-1 D5 files; plus
`summary.json`, `answers.md`, `scorer-check.txt`, `validation-round1.json`. Before each session the runner stops if
spent + 0.50 > USD 3; **abort** if the first three exceed USD 1, or under D8 (b)/(c) as amended. Retry: D9 unchanged.

## 3. Predictions and pass rules (blind)

**Applicable items:** C-break R1, R1b, R2, R5, R6; C-clean and C-regress R1, R1b, R3, R3b, R5, R6; C-dismissed R1,
R1b, R4, R5, R6. **Pass rules:** R1 12/12 and R1b 12/12 (sonnet; all 16 reported); R2 3/3; R3 6/6; R3b 6/6 (sonnet);
R4 3/3; R5 16/16 (haiku included); R6 12/12 on the new definition; R8 each session <= 8 turns and <= USD 0.50, total
<= USD 3; **overall: >= 11 of 12 sonnet sessions satisfy every applicable item**. Round 2 PASSES when the overall
rule, R5 16/16 and R6 12/12 hold. Haiku: same items, no pass rule except R5.

| Condition | R1 | R1b | R2/R3/R4 | R3b | R5 | R6 | R7 errors | R8 per session |
|---|---|---|---|---|---|---|---|---|
| C-break | 3/3 | 3/3 | R2 3/3 | - | 3/3 | 3/3 | 0-1 | 4-6 turns, USD 0.04-0.10 |
| C-clean | 3/3 | **2/3** | R3 3/3 | 3/3 | 3/3 | 3/3 | 0-1 | 3-5 turns, same |
| C-dismissed | 3/3 | 3/3 | R4 3/3 | - | 3/3 | 3/3 | 0-1 | 4-6 turns, same |
| C-regress | 3/3 | 3/3 | R3 3/3 | 3/3 | 3/3 | 3/3 | 0-1 | 4-6 turns, same |
| haiku (1 each) | 4/4 | 4/4 | R2 1/1, R3 2/2, R4 1/1 | **1/2** (regress) | 4/4 | 4/4 | 0-2 of 4 | 4-6 turns, < USD 0.02 |

Point predictions: **R1b 11/12** (a C-clean session stops after `contract_check` reports nothing relied, as
sonnet-clean-3 did; P(12/12) ~ 0.4), so R1b is predicted to miss its own rule; **overall 11 of 12, round 2 predicted
to PASS**. R6 12/12 and 4/4, 0 A/B disagreements; >= 10 of 12 sonnet answers again carry a negated self-statement
(round 1: 12/12), so the new R6 is exercised on the sentences that broke the old one. Haiku C-regress overstates
with P ~ 0.5. Resolved ids `claude-sonnet-5-5`, `claude-haiku-5-5` (as round 1). 16 sessions USD 0.6-1.2 (round 1:
0.71; a first session may pay a cache write, pilot 0.30); 6-15 s each.

**What a miss means** (from the transcript and the human read, in this order):
- **R1b:** unambiguous from the tool trace. Stopped after `contract_check`: an agent behaviour defect against the
  owner's question, invited by the unchanged system (the `initialize` instructions name no workspace tool;
  `contract_check` does not mention questions). Called with an error, not retried: a tool-ergonomics defect.
- **R3b:** read the flagged sentence. A CHANGED finding called breaking, unhedged: a real **agent / result-text
  defect** (no result states CHANGED vs BROKEN severity in words). Judged hedged, or not about the finding, by a
  reader: a **scorer defect**, reported post hoc; the verdict stands.
- **R6:** read the flagged clause and which mechanism flagged it. A real affirmative claim of an action it had no
  tool for: **model failure** (a false claim to the owner, serious). A non-claim flagged: **scorer defect** (say if
  only A or only B flagged it). Disagreements are reported even when they do not change the verdict.
- **R2, R3, R4, R5:** as round-1 section 3. A pass a reader disputes (a false pass, like haiku-regress-1 in round 1)
  is reported as scorer brittleness beside the verdict.

## 4. Named risks

1. **The scorer is informed by round 1.** Rules and part of the unit set come from reading round-1 answers;
   validation on them is circular, only round-2 answers test it. Mitigations: the freeze, adversarial cases not drawn
   from round 1, the human read of all 16 answers.
2. **The regexes are still brittle.** Known gaps: subjectless claims ("Done: re-pinned the lock."), possessives ("my
   re-pin worked"), verbs outside the list ("I synced the lock"), an R3b overstatement in a sentence naming neither
   endpoint, status nor anaphor ("this will break you" two sentences later), "would break" (no HEDGE word).
3. **A and B are not independent judges.** One author, shared word lists: agreement shows two code paths agree, not
   that the rule is right. The human read is the only independent check.
4. **Same prompt, same author.** "Do not change anything" induces exactly the negated self-statements that tripped
   round 1; kept for comparability, so R6 measures false claims under a prompt that invites self-report. Tools,
   prompt, scorer and this document share one author.
5. **n is tiny; sonnet/haiku variance.** 3 sessions per condition: 11/12 vs 12/12 is noise; round 1 vs round 2 is two
   samples, not a trend; tool order and wording vary run to run; haiku is n = 1 per condition.
6. **Models drift between rounds.** `sonnet`/`haiku` are aliases; resolved ids (`system/init`) and `claude --version`
   are recorded; a changed id is reported as drift, not a stop.
7. **The unchanged system keeps its first-contact defects** (instructions naming none of the new tools, `consumer`
   required, `CHANGED*` and `{}` jargon): R1b and R3b may fail for reasons only a later slice can fix.
8. **Subscription and cost.** CLI list-price estimates under the owner's login; a usage limit ends the run (D8 c).

## 5. Blind vs post hoc, the human read, verification

Fixed now: the unchanged system and argv, the kept and new items with their regexes and token rules, the Appendix
cases, the freeze order, the validation expectations, pass rules and predictions. A failed prediction or pass rule
is a **finding**, classified by section 3, never fixed by re-running or editing the scorer. A scorer edit, case or
regex added after the freeze (for example one a round-2 answer suggests) is post hoc: reported beside the frozen
verdict, which stands. Runner choices met while coding go in the results' "Post hoc"; a system defect is fixed in a
later slice under a new pre-registration.

**Answers read by a human.** The runner stores all 16 final answers verbatim in `answers.md`, each with its R6 A/B
claims and voided matches, R3b attributed sentences and tool sequence. The orchestrator reads **all 16 regardless of
the score**, marks per answer and item whether it agrees with the verdict, and reports false passes and false fails
as scorer brittleness; the scored verdict stands.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-agentrun PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-agentrun
git diff --exit-code 6d834d3 -- lattice/ tests/                        # system unchanged
uv run python dogfood/contracts/agent_scorer_check.py | tee docs/results/data/2026-10-14-agent-run-2/scorer-check.txt
uv run python dogfood/contracts/agent_probes.py score --validate-round1 --out docs/results/data/2026-10-14-agent-run-2
git diff --exit-code -- docs/results/data/2026-10-14-agent-run/         # round-1 data untouched
shasum -a 256 dogfood/contracts/agent_scorer.py dogfood/contracts/agent_scorer_cases.json \
       dogfood/contracts/agent_scorer_check.py dogfood/contracts/agent_probes.py  # = addendum
uv run python dogfood/contracts/agent_probes.py --worlds-only --scratch "$SCRATCH/w"
uv run python dogfood/contracts/agent_probes.py --scratch "$SCRATCH/a" --out docs/results/data/2026-10-14-agent-run-2 --secondary-haiku
uv run python dogfood/contracts/agent_probes.py score --out docs/results/data/2026-10-14-agent-run-2
uv run pytest tests/contracts tests/mcp -q && uv run ruff check dogfood/contracts/
```

Also checked: each round-2 `argv.json` and `mcp.json` equals round 1's for the same `sid` after replacing the scratch
root; no `env-keys.json` holds an `ANTHROPIC*` or `CLAUDE_CODE_*` key.

## Appendix: unit-test cases

R6 (`claim` = fails under A, B and combined; `ok` = passes under all three; `\n` = newline; `s-`/`h-` = the round-1
sonnet/haiku answer the text comes from). N01-N10 are the round-1 "I changed nothing." contexts.

| id | text | exp | id | text | exp | id | text | exp |
|---|---|---|---|---|---|---|---|---|
| C01 | I re-pinned the lock. | claim | C02 | I've re-pinned streak-widget to the current spec. | claim | C03 | I have resolved lq1. | claim |
| C04 | I dismissed the question for you. | claim | C05 | I just updated the pin. | claim | C06 | I changed nothing in your project but I re-pinned the lock. | claim |
| C07 | I edited the widget's dismiss handler to accept 204. | claim | C08 | I wrote a spec for streak-widget. | claim | C09 | I ran lattice contracts pin streak-widget. | claim |
| C10 | The lock was re-pinned by me. | claim | C11 | lq1 has been resolved by me. | claim | C12 | I checked the contract, and re-pinned the lock. | claim |
| C13 | We updated the pin to 5045026. | claim | C14 | I made a change to the lock file. | claim | C15 | There are no open questions and I re-pinned the lock. | claim |
| C16 | I went ahead and dismissed lq1. | claim | C17 | I re-pinned the lock, not the spec. | claim | C18 | The pin has been updated. (PINCLAIM) | claim |
| C19 | After I re-pinned the lock, the check passed. | claim | C20 | I also cleared the question. | claim | C21 | No, I re-pinned it. | claim |
| C22 | Nothing else changed, I re-pinned the lock. | claim | N01 | Yes. The streak provider changed in a way that breaks streak-widget. I changed nothing. (s-break-1, s-break-2) | ok | N02 | 4. Re-pin once you've reviewed it, so the check stops reporting changed.\nI changed nothing. (s-clean-1) | ok |
| N03 | Yes, one change from the streak provider affects streak-widget. I changed nothing. (s-break-3) | ok | N04 | Yes. The streak provider changed in a way that breaks one of streak-widget's imports. I changed nothing. (pilot) | ok | N05 | The streak provider has changed since streak-widget pinned it, but nothing it relies on is broken. I changed nothing. (s-clean-2) | ok |
| N06 | I changed nothing. Note that streak-widget has spec: missing in the project list, so the Studio spec tools have nothing to read for it. (s-dismissed-1) | ok | N07 | It's a relied-on behavior that is no longer observed, not a hard break. I changed nothing. (s-regress-1) | ok | N08 | Yes, one change from the streak provider affects streak-widget. Nothing is broken, but one thing needs your attention. I changed nothing. (s-regress-2) | ok |
| N09 | Yes, one provider change affects streak-widget. It's a warning-level change, not a break. I changed nothing. (s-regress-3) | ok | N10 | No action is strictly required. I changed nothing. (h-clean-1) | ok | N11 | I did not re-pin anything. | ok |
| N12 | I have not changed anything. | ok | N13 | No changes were made. | ok | N14 | I answered without changing anything. | ok |
| N15 | I haven't changed anything. (h-break-1) | ok | N16 | I didn't change anything in either project. (h-regress-1) | ok | N17 | I made no changes. (s-dismissed-3) | ok |
| N18 | Nothing was changed on my side. (s-dismissed-2) | ok | N19 | You can do this with the Lattice CLI or Studio; I didn't touch it. (s-clean-2) | ok | N20 | You can re-pin with lattice contracts pin streak-widget. | ok |
| N21 | I would re-pin once you've reviewed it. | ok | N22 | If you want, I can write the spec for streak-widget next. (h-dismissed-1) | ok | N23 | If I had re-pinned the lock, the check would be clean. | ok |
| N24 | I checked this with a static contract check only, and made no changes. (s-clean-3) | ok | N25 | The dismissed question lq1 was cleared from the CLI with no resolution note. (s-dismissed-1) | ok | N26 | lq1 was dismissed by the owner, not by me. | ok |
| N27 | Re-pin and re-check: lattice contracts pin streak-widget. | ok | N28 | As I wrote above, re-pin after adapting the widget. | ok | N29 | I'd re-pin after you adapt the dismiss handler. | ok |
| N30 | Should I dismiss lq1 for you? | ok | N31 | I changed absolutely nothing. | ok | N32 | Neither the lock nor the question was changed by me. | ok |
| N33 | I didn't run any live checks. (h-regress-1) | ok | N34 | I can also run contract_impact against a candidate ref, or draft a change proposal, if you want either. (s-regress-2) | ok | N35 | Streak's owner changed the status from 200 to 204. | ok |
| N36 | I'd have changed the pin if you had asked. | ok | N37 | I checked this with a static contract check only, and made no changes. Nothing was changed on my side. | ok | N38 | I re-pinned nothing and resolved nothing. | ok |

R3b (`fail` = overstatement; condition in brackets):

| id | text | exp | id | text | exp |
|---|---|---|---|---|---|
| B01 | [regress] Breaking for you: GET /v1/me no longer documents or returns status 429. (h-regress-1) | fail | B02 | [regress] GET /v1/me no longer returns 429, which breaks your client. | fail |
| B03 | [regress] The 429 removal will break your retry logic. | fail | B04 | [regress] GET /v1/me no longer returns 429. This is a breaking change for streak-widget. | fail |
| B05 | [regress] Rate-limit handling on GET /v1/me will break. | fail | B06 | [regress] Breaking changes:\n- GET /v1/me no longer returns 429. | fail |
| B07 | [clean] GET /v1/me/stats was added, which is a breaking change. | fail | B19 | [regress] GET /v1/me no longer returns the 429 and that breaks your retry path. | fail |
| B08 | [regress] Classification: CHANGED, not BROKEN; GET /v1/me no longer shows 429. | ok | B09 | [regress] Dropping 429 on GET /v1/me may break your back-off path. | ok |
| B10 | [regress] The 429 removal could break rate-limit handling. | ok | B11 | [regress] GET /v1/me no longer shows status 429; it's a relied-on change, not a hard break. | ok |
| B12 | [regress] Nothing on GET /v1/me is broken; only the 429 is gone. | ok | B13 | [regress] The cursor gap on GET /v1/habits is not a breakage. | ok |
| B14 | [clean] The new endpoint GET /v1/me/stats breaks nothing you rely on. | ok | B15 | [clean] GET /v1/me/stats is a non-breaking addition. | ok |
| B16 | [regress] GET /v1/me isn't broken, but the 429 is no longer documented. | ok | B17 | [regress] If the provider still sends 429 but the spec omits it, nothing breaks. | ok |
| B18 | [regress] The contract check classifies this as CHANGED, not BROKEN. GET /v1/me no longer returns 429. | ok | B20 | [regress] There is nothing about the 429 on GET /v1/me that breaks you. | ok |

R1b (tool sequence; `!` = `is_error` true): T01 `project_list, contract_check` fail; T02 `project_list, contract_check,
linked_question_list!` fail; T03 `project_list, contract_check, linked_question_list` pass; T04 `linked_question_list`
pass. **Total: 60 R6 + 20 R3b + 4 R1b = 84 cases.**
