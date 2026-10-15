# Five first-contact fixes and real-agent exercise round 3: pre-registration

## 1. Status

Date: 2026-10-14. Branch `np-fixes` (worktree `lattice-fixes`, from `next-phases` 2407eaf). **Written BEFORE any fix
or round-3 session exists**: no fix code, round-3 world, session, answer or score exists; every round-3 number below is
a prediction and every "passes" a gate that has not run. Read with `docs/results/2026-10-14-agent-run-results.md`,
`...-agent-run-2-prereg.md` (frozen scorer, Addendum 1), `...-agent-run-2-results.md`, `2026-10-12-multi-prereg.md` (MP-1).

**The author has read all 33 stored answers (round-1 16 + pilot, round-2 16) and the round-2 tool sequences** (all 16
passed `consumer`/`project` on every call: the round-2 value of R9 below is KNOWN, not predicted). The same author
wrote the fixes' specification, the prompt (unchanged), the scorer and this document.

**Why.** Round 2 failed its overall rule 10 of 12: one real miss (sonnet-break-1 answered from `contract_check` alone,
never reading the linked questions; `initialize` names no workspace tool) and R3 false fails on sentences that merely
contain "break". Rounds 1-2 also left four first-contact defects: Studio chat cannot call any multi-project tool,
`consumer` is required on `linked_question_list`, `CHANGED*`/`{}` are unexplained, the spec-less error tells a contract
consumer to write a spec.

## 2. Fix decisions (fixed before coding)

Common rules: no tool is added, removed or renamed; no argument is added or removed; the only schema change is
`linked_question_list.consumer` becoming optional. Stored question files (`lq<N>.json`) and their `text` are not
changed (round-2 world preconditions and `linkedq_probes.py` pin that text). Each fix also updates the docstrings and
`lattice/studio/API.md` where they describe the changed behaviour.

**F1 Server instructions** (`lattice/mcp/projects.py`). `ProjectRouter.instructions(base)` becomes: one known project:
`base` unchanged (also with a workspace, also `--readonly`); several: `base + MULTI_NOTE` (text unchanged), then
`+ WORKSPACE_NOTE` when a workspace is known, then `+ READONLY_NOTE` when `--readonly`. Sharpening of "a workspace or
more than one project": with exactly one known project a workspace has no link (a link needs two projects,
`self_link` is an error), so naming the contract tools there helps nobody and would break the pinned test
`tests/mcp/test_contract_check.py::test_a_workspace_appends_the_tool_after_the_old_ones` (instructions == base).

```python
WORKSPACE_NOTE = (
    " A workspace manifest links these projects; project_list shows each one's role and links. "
    'For "has anything a provider changed affected this project?": call project_list, then BOTH '
    "contract_check and linked_question_list for the consumer: contract_check shows what changed "
    "since its pin (BROKEN, CHANGED), linked_question_list the notes a provider left when an "
    "accepted change broke it (open, resolved or dismissed). Both take `consumer` and default to "
    "the active project when it is a consumer. workspace_impact lists who relies on a project; "
    "contract_impact previews a provider's candidate version against its consumers. These five "
    "tools are read-only."
)
READONLY_NOTE = (
    " This server is read-only: it has no tool that proposes a spec change or runs the checks; "
    "tell the owner what to change."
)
```

The notes name only listed tools (the five are listed exactly when a workspace is known, also `--readonly`);
READONLY_NOTE names none and corrects the unchanged base's "propose a change with change_propose", a tool `--readonly`
hides. The base cannot change (byte rule), so a read-only **single-project** server still suggests `change_propose`
(residual). The round-3 server answers exactly `INSTRUCTIONS + MULTI_NOTE + WORKSPACE_NOTE + READONLY_NOTE`.
Tests: `test_multi_server.py` (`startswith`, `project_list` named, one project == base) and `test_contract_check.py`
lines 144, 165, 206 pass unchanged. New `tests/mcp/test_instructions.py`: exact strings for one project (with/without
workspace, `--readonly`) and several (without workspace; with; with + `--readonly`); every `snake_case` word of the
appended text is in that router's `names()` (no hidden tool suggested); WORKSPACE_NOTE names the five tools.

**F2 Studio chat** (`lattice/studio/session.py`). New `_MULTI_TOOLS = ("project_list", "workspace_impact",
"contract_check", "contract_impact", "linked_question_list")` as `mcp__lattice__*`, added to the `allowed` of **ask,
explain and change** (change stays "ask's tools + the change tools"); not to rebuild (disabled) and **not** to
`_MODEL_TOOLS`, which `lattice/studio/rebuild.py` also uses (the rebuild agent's list is unchanged). Excluded:
`project_set_active` (a chat works on its one project; every tool already takes `project`) and every write tool.
The tools appear in a chat only when its `serve --project DIR` finds a workspace (walking up from the project or
`$LATTICE_WORKSPACE`); passing Studio's own `--workspace` to the session server is NOT part of F2 (a later slice).
`_BASE_RULES` stays **byte-identical** (every existing prompt stays); `system_prompt` appends, only when one of
REQUIREMENTS.md, spec.py, spec.trace.json is missing (a hub-served `studio.toml`-only project): `"This project has no
Lattice spec yet (missing: <names>): the spec files named above do not exist; say so instead of answering from the
spec."` Tests: none pins the literal list (`test_session.py:160-162`, `test_mcp_intake.py:259/274/396` derive it from
`MODE_PROFILES`, pass unchanged; disclosed). New: the five in ask/explain/change; `project_set_active`, `change_propose`,
`check_run` absent from ask/explain; `build_rebuild_argv` unchanged; the sentence iff a spec file is missing.

**F3 `consumer` defaults to the active project** (`lattice/mcp/projects.py`). One helper, used by `contract_check` and
`linked_question_list`: `consumer` absent, null or `""` = not given (the router's rule for `project`); a non-string is
the error `argument 'consumer' must be a string`. When not given, the active project is resolved in the manifest read
for this call (`ws.by_path(root) or ws.project(name)`, as `workspace_impact`); if it is the `from` of at least one link
with a `contract` key, it is the consumer. Otherwise `contract_check` keeps today's default (every opted-in consumer)
and `linked_question_list` errors `argument 'consumer' is required: the active project NAME is not a contract consumer
of the workspace (consumers: A, B)`. An explicit value always wins. The result is exactly the CLI's for that consumer
(`contracts check CONSUMER --static-only --json`, `contracts questions list CONSUMER --json`); no result field is added.
Schema: `linked_question_list` `required: []`; property descriptions unchanged; tool descriptions change only in the
`consumer` clause: contract_check "(default: the active project when it is a consumer with an opted-in link, else
every consumer with one)", linked_question_list "a workspace project name (default: the active project when it is a
consumer with an opted-in link)". Tests that must change (disclosed): `test_linked_questions.py:69` (`required` ->
`[]`) and `test_the_consumer_is_a_required_string` (renamed; with the fixture's active `prov`, not a consumer, `{}`,
`{"consumer": ""}`, `{"status": "open"}` still raise "is required"; `{"consumer": 3}` now raises "must be a string").
New: active `cons` -> `{}` = `{"consumer": "cons"}` = the CLI, both tools, in-process and over stdio. Unaffected by
construction: every existing `{}` call runs with a non-consumer active project (`probes.py`/`exclude_probes.py`
`--project solo`; test routers list `prov` first; stdio tests launch in the workspace root).

**F4 Legend** (`lattice/contracts/linked.py`). `list_body` gains ONE last key `legend`, always present (also with no
question or a status filter), a copy of the constant below; the CLI `--json`, MCP and `GET /api/linked-questions` all
build their body with `list_body`, so parity holds by construction; the mock imports `LEGEND` (one source). Not
changed: `lq<N>.json`, `text`, `finding_line`, `get_question`, the CLI's human output. API.md: a v1.20 section.

```python
LEGEND = {
    "CHANGED*": "CHANGED on an endpoint this consumer imports (the finding has relied: true): the "
    "provider's contract changed there since the pin, but none of the consumer's imported claims "
    "stopped holding (that would be BROKEN)",
    "{}": "a templated path parameter, any id or name: POST /invoices/{}/pay is the pay endpoint "
    "of every invoice",
}
```

Must change (disclosed): `tests/studio/test_linked_api.py` `LIST_KEYS` gains `legend`. New: legend equal in CLI, MCP,
real API and mock; question files byte-identical across a list. `dogfood/studio` `LINKED_EMPTY` is a subset match.

**F5 Spec-less consumer error** (`lattice/mcp/tools.py`, `PendingBackend._resolve`). The text is unchanged for every
directory, then, when the directory holds `lattice.imports.toml` and/or `lattice.contracts.lock.json` (names read
from `lattice.contracts.check` by a lazy import), ONE sentence is appended (`FILES` = the present names joined by " and "):

```python
f" This directory is a contract consumer (it holds {FILES}), which needs no spec for its contracts: "
"with a workspace, contract_check and linked_question_list show what its providers changed and the "
"notes they left (otherwise `lattice contracts check` and `lattice contracts questions list` with "
"--workspace)."
```

Not changed: `project_status`'s `next_steps` (a residual). Tests: `tests/mcp/test_server.py:295-307` match the old
text and pass unchanged; new: the sentence iff a file exists, the original text a prefix.

**2.6 No-regression gates** (all before the scorer freeze, 3.3; a gate whose prerequisite is missing is NOT RUN, never
a pass). Environment of section 6.

| Gate | Command / check | Pass |
|---|---|---|
| G1 | `uv run pytest tests/contracts tests/workspace tests/mcp tests/studio -q` | 0 failures |
| G2 | `uv run ruff check` and `ruff format --check` on every changed file | clean |
| G3 | `dogfood/studio/check.sh` | PASS |
| G4 | `dogfood/contracts/probes.py` (needs `lattice-contracts-base` detached at c0cdefc) | 35/35 |
| G5 | `streak_probes.py --allow-code-diff` | 32/32 |
| G6 | `exclude_probes.py` | 12/12 |
| G7 | `impact_probes.py --runs 10`, I8 included | 12/12 |
| G8 | `linkedq_probes.py` | Q0-Q7 all pass |
| G9 | new byte probe `dogfood/contracts/fixes_probes.py`; old code = `git archive 2407eaf lattice` | B1-B6 |

G9 serves `python -m lattice.mcp serve` from both trees over stdio and compares JSON bytes. B1 one `--project` (copy
of `dogfood/studio`), no workspace, with/without `--readonly`: `initialize`, `tools/list` identical. B2 a spec-less one
without imports: also the `model_get` error identical. B3 with `lattice.imports.toml`: error = old + F5's sentence.
B4 a one-project workspace: `initialize` identical; `tools/list` differs ONLY in contract_check's and
linked_question_list's `description` and linked_question_list's `required`. B5 the round-3 shape (provider + consumer,
`--project` consumer, `--readonly`): instructions = old + WORKSPACE_NOTE + READONLY_NOTE; `tools/list` same names,
order and property names, B4's differences only. B6 two `--project`, no workspace: identical (+ READONLY_NOTE if `--readonly`).

## 3. Round 3 design

**3.1 Identical to round 2** (its 2.1): worlds D1 (steps 1-6, preconditions D1.5, `linkedq_probes.streak_accept`),
conditions D2 (C-break D2, C-clean D8, C-dismissed D2 + dismiss, C-regress D13), a fresh world per session; argv D3
(`--setting-sources ""`, `--tools ""`, the 14-tool allow-list, the disallow list, `dontAsk`, `--max-turns 8`,
`--max-budget-usd 0.50`, `--no-session-persistence`), `mcp.json`, child environment (round-1 Amendment 1), prompt D4
byte for byte; 12 sonnet (3 per condition) + 4 haiku, D5 order, no pilot; timeout 300 s, retry D9, abort D8 (b), (c);
caps USD 3 total, 0.50 per session, abort if the first three exceed USD 1. **No variant sessions, no new condition.**
The only difference is the system (`lattice/` at the freeze commit). Data `docs/results/data/2026-10-14-agent-run-3/`.

**3.2 Scorer and runner changes** (the whole list). In `agent_probes.py` only `DEFAULT_OUT`, the prereg pointer the
freeze check reads (this document), the overall rule, R9 and its reporting, and a `score --validate-round2` mode may
change; `PROMPT` through `launch` (round 2's list) stay byte-identical. In `agent_scorer.py` only a pure `r9(trace)`
is added; `norm`, `r1b`, `r3b`, `r6_a`, `r6_b`, `r6` stay byte-identical.

- **R3 is retired from the pass rule. This is a post hoc change of the scorer, made after seeing R3 fail.** Record
  over 16 R3 judgements (rounds 1-2): 3 false fails (round 2), 1 false pass (round-1 haiku-regress, "Breaking for
  you"), 0 failures the human read confirmed; R3b, frozen before round 2 with unit cases, covers the overstatement.
  R3 is still computed, stored and shown in every table as "information". The 84-case set has no R3 case (it never
  had one) and stays unchanged. **Coverage kept (orchestrator amendment, 2026-10-14, before any fix or session):** the
  part of R3 that checks that the agent does not CLAIM an open question when there is none is not what misfired (it is
  the same `CLAIMOPEN` pattern R4 uses, and it fired in none of the 3 false fails), so it stays in the pass rule as
  **R3c: in C-clean and C-regress `CLAIMOPEN` is empty**. Only the rest of R3 leaves the pass rule: the "break"
  sentence check (replaced by R3b) and the `NOOPEN` "states there is none" check (brittle: it missed
  "Open questions: None. Nothing has been raised" in round 2), both still computed and shown as information. R3c is
  computed by the frozen scorer's existing `CLAIMOPEN` pattern and is reported on the 17 round-1 and 16 round-2
  stored transcripts in V1/V2 before the freeze (expected: empty in every C-clean and C-regress session). Under the
  round-3 rule round 2 would have scored 11 of 12 (only sonnet-break-1's R1b): a re-scoring, never reported as a
  round-2 pass.
- **R9 NEW (information, all conditions; tests F3).** A *default call* is a `tool_use` of
  `mcp__lattice__contract_check` or `mcp__lattice__linked_question_list` whose input has no non-empty string
  `consumer`, or of `mcp__lattice__workspace_impact` with no non-empty string `project`. Per session: the default calls
  per tool and how many returned `is_error` true. No pass rule. A default call that errors is also an R7 error and an
  F3 defect. Unit cases W01-W06 (`kind: r9`): contract_check `{}` default; contract_check `{consumer:
  "streak-widget"}` not; linked_question_list `{status: "open"}` default; workspace_impact `{}` default;
  workspace_impact `{project: "streak-widget"}` not; contract_check `{consumer: ""}` default. Total 90 cases.
- `satisfies_all` = every applicable item except R3; `satisfies_all_round2_rule` (with R3) is stored beside it.

**3.3 Freeze order.** (1) This document committed. (2) F1-F5 and tests; G1-G9 pass; committed. (3) The 3.2 edits.
(4) `agent_scorer_check.py` exits 0 on 90/90 (A alone, B alone, combined R6, R3b, R1b, R9). (5) V1: the 17 round-1
transcripts give round-2 Addendum 1's result exactly (R6 17/17, 0 A/B disagreements; R1b 16/17; R3b 7/8). V2: the 16
round-2 transcripts give per session R1-R6 (R1b, R3, R3b included) equal to the stored round-2 `score.json`, R9 0
default calls, 11 of 12 sonnet under the new rule; nothing written in the round-1/2 directories; any other result is a
scorer defect fixed before (6). (6) `--worlds-only` on the fixed system: every D1.5 precondition holds. (7) "Addendum
1: scorer freeze (round 3)" appended here (sha256 of the four frozen files, `claude --version`, check output, V1, V2,
freeze commit id) and committed. (8) Only then session 1; the run refuses a hash mismatch; `lattice/`, `tests/` and
the four files equal the freeze commit until the last session.

**3.4 Comparison.** Rounds 1, 2 and 3 side by side per item: each round's official verdict under its own rule, the
round-3 rule on all three (rounds 1-2 from V1/V2, labelled re-scoring), tool sequences, R7-R9, model ids, CLI version.

## 4. Predictions and pass rules (blind)

**Applicable items:** C-break R1, R1b, R2, R5, R6; C-clean and C-regress R1, R1b, R3b, R3c, R5, R6 (R3 information);
C-dismissed R1, R1b, R4, R5, R6. **Pass rules (sonnet unless stated):** R1 12/12; **R1b 12/12**; R2 3/3; **R3b 6/6**;
**R3c 6/6**; R4 3/3; **R5 16/16** (haiku included); **R6 12/12**; R8 each session <= 8 turns and <= USD 0.50, total <= USD 3;
**overall: >= 11 of 12 sonnet sessions satisfy every applicable item, R3 excluded.** Round 3 PASSES when the overall
rule, R1b 12/12, R5 16/16 and R6 12/12 hold. Haiku: same items, reported, no pass rule except R5.

| Condition | R1 | R1b | R2 / R4 | R3b | R3 (info) | R5 | R6 | R7 errors | R9 default calls | R8 per session |
|---|---|---|---|---|---|---|---|---|---|---|
| C-break | 3/3 | 3/3 | R2 3/3 | - | - | 3/3 | 3/3 | 0 | 1-2 of 3 sessions | 4-5 turns, USD 0.04-0.10 |
| C-clean | 3/3 | 3/3 | - | 3/3 | 2-3/3 | 3/3 | 3/3 | 0 | 1-2 of 3 | 4-5 turns |
| C-dismissed | 3/3 | 3/3 | R4 3/3 | - | - | 3/3 | 3/3 | 0 | 1-2 of 3 | 4-5 turns |
| C-regress | 3/3 | 3/3 | - | 3/3 | 2-3/3 | 3/3 | 3/3 | 0 | 1-2 of 3 | 4-5 turns |
| haiku | 4/4 | 4/4 | 1/1, 1/1 | 2/2 | 1-2/2 | 4/4 | 4/4 | 0-1 of 4 | 1-3 of 4 | < USD 0.03 |

Point predictions: **overall 12 of 12 (round 3 PASSES)**, R1b 12/12 with P ~ 0.8; R3 (information) 5/6 sonnet;
R7 0 errors in 16 (round 2: 0); R9: 5 of 12 sonnet sessions make >= 1 default call (range 2-10; round 2: 0 of 16,
known), every default call without error; `project_list` the first call in >= 11/12; contract_check and
linked_question_list both called in 12/12; `workspace_impact` in 4-10 of 12 (round 2: 11/12; the note does not put it
on the path); 16 sessions USD 0.6-1.2, 3-6 turns, 6-15 s each; resolved ids `claude-sonnet-5-5`, `claude-haiku-5-5`.

**What F1 can and cannot show (R1b).** Rounds 1-2 had one sonnet R1b miss each (round 1's from V1): 2 of 24. With no
effect, P(0 misses in 12) = (22/24)^12 ~ 0.35: **a 12/12 cannot prove the fix, only fail to reproduce the miss** (weak
evidence, likelihood ratio ~ 3). A miss with the instruction in front of the agent says more (F1 insufficient), n = 1.

**What a miss means** (transcript first, then the human read):
- **R1b:** stopped after contract_check with the note in `initialize` -> **fix did not help** (agent variance against an
  explicit instruction); linked_question_list errored -> **F3 defect**; a successful call missed -> **scorer defect**.
- **R2:** an endpoint written in a new form (for example "any id", from the legend) that `ENDPOINT` misses -> **scorer
  defect induced by F4**; endpoint or finding absent -> agent.
- **R3b:** read the flagged sentence: the legend's words misused ("relied ... breaks you") -> **F4 harmful**; an
  unhedged overstatement unrelated to the legend -> agent variance; hedged, governed or not about the finding ->
  scorer defect, reported post hoc, verdict stands.
- **R4:** the dismissed note called open -> agent; else scorer.
- **R5:** any write or world/outside change -> serious: the system is investigated first (a fix bug), D8 (b) stops.
- **R6:** as round 2 (model failure vs scorer defect; which mechanism flagged).
- **R7 / R9:** an error on a default call -> F3 defect; an error from a spec tool on the consumer that lacks F5's
  sentence -> F5 defect; with the sentence -> F5 working, the agent's choice.
- An overall fail whose every miss reads as a scorer defect is still a FAIL, reported with the classification.

## 5. Named risks

1. **n is tiny.** 3 per condition, haiku n = 1; 11/12 vs 12/12 is noise; three rounds are three samples, not a trend.
2. **One author for everything.** Fixes, instructions, legend, prompt, scorer and this document, by one author who
   has read every earlier answer and tool sequence. Mitigations: the freeze, V1/V2, the human read; none is independent.
3. **Goodhart: the instructions are optimised to the exercise.** WORKSPACE_NOTE quotes almost the exercise's question
   and prescribes the very path R1b scores, and the legend states "not BROKEN", the distinction R3b scores. Round 3 then
   measures whether agents follow an instruction, not whether they discover the tools; an R1b/R3b pass does not carry
   to other phrasings, projects or owners.
4. **Fixes change the answer text in ways the frozen scorer may mishandle.** Agents may quote the legend ("relied",
   "none of the imported claims stopped holding", "would be BROKEN" without a HEDGE word -> R3b), write the endpoint as
   "any id" (R2 `ENDPOINT`), echo READONLY_NOTE ("I have no tool to change it" -> R6 relies on negation handling), or
   restate the note's question. No case is added for these (it would be tuned to a guess); misses go by section 4.
5. **R3 retired after seeing it fail: a post hoc scorer change**, in the direction of passing, justified by 3 false
   fails and 0 confirmed true fails, not hidden: R3 is computed and reported, and round 2 is never re-labelled a pass.
   The `CLAIMOPEN` check stays in the pass rule as R3c (3.2); only the "break" sentence check and `NOOPEN` leave it,
   and the human read still reads every answer.
6. **Model drift between rounds.** `sonnet`/`haiku` are aliases; resolved ids and `claude --version` (round 2:
   2.1.299) are recorded; a change is reported as drift, not a stop, and weakens the round-to-round comparison.
7. **Studio chat is not exercised.** F2 is tested by unit tests only (and in a real Studio its tools appear only when
   the session's server finds a workspace). F5 shows only if an agent calls a spec tool on the consumer (1 call in 32
   earlier sessions); F3 only through R9, which no rule judges.
8. **Residuals left on purpose and cost.** A read-only single-project server still suggests `change_propose`;
   `project_status` still advises a spec for a consumer; Studio does not pass its workspace to chat sessions. Costs
   are CLI list-price estimates under the owner's login (no API key); a usage limit ends the run.

## 6. Blind vs post hoc, the human read, verification

Fixed now: F1-F5, the tests that change, G1-G9, the round-2 design, the R3 retirement, R9, the freeze order, V1/V2,
pass rules and predictions. A gate failing before the freeze is fixed and reported in the results' "Post hoc"; a failed
prediction or rule after it is a **finding** (section 4), never fixed by re-running, re-prompting or editing scorer or
system. A rule, regex or case added after the freeze is post hoc, reported beside the frozen verdict, which stands.

**Human read.** `answers.md` holds all 16 answers verbatim with tool sequence, R6 A/B claims, R3b and R3 sentences
and R9 calls. The orchestrator reads **all 16 regardless of score**, marks agreement per answer and item, checks
C-clean/C-regress for a claimed open question, notes quoted instruction or legend text, and reports false passes and
false fails as scorer brittleness; the scored verdict stands.

**Verification** (nothing below has run; the results report each output):

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-fixes PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-fixes
git -C /Users/vivekgade/Documents/lattice-contracts-base rev-parse HEAD                # c0cdefc... (G4)
uv run pytest tests/contracts tests/workspace tests/mcp tests/studio -q                # G1: 0 failures
uv run ruff check lattice tests dogfood/contracts && uv run ruff format --check lattice tests dogfood/contracts
dogfood/studio/check.sh                                                                # G3: PASS
uv run python dogfood/contracts/probes.py --runs 10                                    # G4: 35/35
uv run python dogfood/contracts/streak_probes.py --allow-code-diff && uv run python dogfood/contracts/exclude_probes.py
uv run python dogfood/contracts/impact_probes.py --runs 10                       # G5 32/32, G6 12/12, G7 12/12 incl. I8
uv run python dogfood/contracts/linkedq_probes.py --scratch "$SCRATCH/lq" --out "$SCRATCH/lq-out"   # G8: Q0-Q7
mkdir -p "$SCRATCH/old-2407eaf" && git archive 2407eaf lattice | tar -x -C "$SCRATCH/old-2407eaf"
uv run python dogfood/contracts/fixes_probes.py --old "$SCRATCH/old-2407eaf" --scratch "$SCRATCH/b"  # G9: B1-B6
uv run python dogfood/contracts/agent_scorer_check.py | tee docs/results/data/2026-10-14-agent-run-3/scorer-check.txt
for v in --validate-round1 --validate-round2; do
  uv run python dogfood/contracts/agent_probes.py score $v --out docs/results/data/2026-10-14-agent-run-3; done
git diff --exit-code -- docs/results/data/2026-10-14-agent-run/ docs/results/data/2026-10-14-agent-run-2/
uv run python dogfood/contracts/agent_probes.py --worlds-only --scratch "$SCRATCH/w"
shasum -a 256 dogfood/contracts/agent_scorer.py dogfood/contracts/agent_scorer_cases.json \
       dogfood/contracts/agent_scorer_check.py dogfood/contracts/agent_probes.py      # = Addendum 1
uv run python dogfood/contracts/agent_probes.py --scratch "$SCRATCH/a" \
       --out docs/results/data/2026-10-14-agent-run-3 --secondary-haiku
uv run python dogfood/contracts/agent_probes.py score --out docs/results/data/2026-10-14-agent-run-3
git diff --exit-code <freeze commit> -- lattice/ tests/ dogfood/contracts/agent_*      # system and scorer unchanged
```

Also checked: each `argv.json`/`mcp.json` equals round 2's for the same `sid` (scratch root replaced); no
`env-keys.json` holds `ANTHROPIC*` or `CLAUDE_CODE_*`; each `system/init` lists round 2's 14 `mcp__lattice__*` tools.

## Addendum 1: scorer freeze

Written by the orchestrator on 2026-10-14, after the fixes F1-F5 and the round-3 scorer were built and
validated and BEFORE any round-3 session ran. From this commit on, any edit to any of these four files
invalidates the freeze (the runner refuses to start sessions when a hash does not match).
`claude --version`: 2.1.299 (Claude Code). Gates passed before freezing, re-run independently by the
orchestrator: 90/90 unit cases; V1 (17 round-1 transcripts) and V2 (16 round-2 transcripts) as the
prereg fixes them (round 2 re-scored 11 of 12, a re-scoring and never a round-2 pass); byte-compat
B1-B6 6/6; 1010 tests, dogfood 435 cases; layer-3 runners 35/35, 32/32, 12/12, impact 11/11 (I8
skipped by flag), linked questions 9/9; the two stale runner references were fixed post hoc in a
separate commit; ruff clean.

```
92fb5a76aec490ca059cfea2f09d787ad4377243c3bb6545921f5f4cbb322d08  dogfood/contracts/agent_scorer.py
e6bce4f1c738aff46430908b03d97427caf2c58b84689e1d3ef718b5480394ca  dogfood/contracts/agent_scorer_cases.json
cdb61453a231f4c52db80a9a593744b809448913770ec5bf765a24aa8122da7a  dogfood/contracts/agent_scorer_check.py
bbd1fed1e0631492e4636a1fab5d3e110b61cac3e52446dd01c0882194fa0509  dogfood/contracts/agent_probes.py
```
