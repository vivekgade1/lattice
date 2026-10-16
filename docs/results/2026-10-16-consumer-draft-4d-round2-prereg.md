# Slice 4d consumer-side draft, real-agent round 2 (A3 scorer fix): pre-registration

## 1. Status and gap

Date: 2026-10-16. Branch `np-s4d2` (worktree `lattice-s4d2`, from `next-phases` bb3ff0c). **Written BEFORE any round-2
file exists**: no new scorer, case, world, session or score exists; every number below that is not a round-1 fact is a
prediction. Round 1: `2026-10-15-consumer-draft-4c-prereg.md` (P3, Amendments 1-2), `...-4c-results.md` section 5,
data `docs/results/data/2026-10-15-consumer-draft-4d/agent{,-partial-run1}/`. The pre-registration runs no model; round 2
calls models through the `claude` CLI only (`cli_env` strips ANTHROPIC*), never an API key. Results:
`docs/results/2026-10-16-consumer-draft-4d-round2-results.md`, data `docs/results/data/2026-10-16-consumer-draft-4d-round2/`.

**The author has read all 10 round-1 answers, their `score.json`, two transcripts and two drafts.** The A3 design below
is informed by them; the round-1 answers are therefore NOT evidence for it (V3 is a development figure only).

**Gap (round-1 facts).** The frozen scorer gave 4 of 6 tool sessions every item (pass rule >= 5 of 6 NOT met; A6 8/8).
Every A3 miss of round 1 is a false positive: run 2 WD2-1 (`contract_check`, `ok`, `ef17cf88`), WS6-3 (`a0db033a`,
`contract_check`, `ok`), partial run 1 WD2-1 and WS6-1 (`contract_check`, `ok`), and both ablation sessions (`ef17cf88`,
`contract_check`, `ok`). `ef17cf88`/`a0db033a` are prefixes of the pinned `git_ref`s in the locks; `ok` is the `status`
value of `contract_check`; none of the sentences proposes a change. Cause in the code (`draft_agent_scorer.a3`): it
checks every non-negated sentence and every backticked `[a-z][a-z0-9_]*` token against draft + imports + REQUIREMENTS.md
only. **Premise corrections (reported):**
1. **No round-1 A3 true positive exists** (0 of 6 A3 misses are real). The "round-1 true misses" are the ablation's
   A2 (no import number) and A7 (no rule) misses; A3's sensitivity rests on synthetic cases only (V1 G3-G7).
2. **The pre-registered item already said "proposes to change"** (4c P3 A3); the frozen code checked all sentences.
   Round 2 brings the scorer to the item's wording; it does not redefine the item.
3. **Round-1 sessions loaded the account's claude.ai connectors** (Docs, Figma, Gmail: ~80 tool names in each init
   record) despite `--setting-sources ""`; none was called (`dontAsk` + allow-list). Not changed here (section 2 D6).
4. **Rule ids are implicit.** The planted `REQUIREMENTS.md` has no `R5` text (ids come from `extract_rules`); the
   frozen A3 found R3 only through the draft, so a proposal naming a real R1, R2, R4-R6 would be a false positive
   (latent, not observed). A3b takes rule ids from `extract_rules` (D2).

## 2. Decisions (fixed before coding)

**D1 Unchanged.** `lattice/` and `tests/` (the tool, CLI, MCP server, descriptions, instructions); worlds W-D2 (+spec
ci-bot, job D2, `lq1`) and W-S6 (streak-widget, Streak D6, `lq1`) built by the round-1 `build_world`; prompt; argv
(`argv_for` of `draft_agent.py`, imported); A1, A2, A4, A5, A7 (functions imported from the frozen
`draft_agent_scorer.py`, not copied); A6 (round-1 snapshot); the corpus (draft `text` + `lattice.imports.toml` +
`REQUIREMENTS.md` when present). Round-1 files stay byte-identical: `draft_agent{,_scorer,_check}.py`,
`draft_agent_scorer_cases.json` (sha256 97395b9e..., 32201ba4..., 46b66768... as the 4c freeze; `draft_agent.py`
f4eebedf... at bb3ff0c), `agent_scorer.py` 92fb5a76..., `agent_probes.py` bbd1fed1..., `draft_probes.py` 3782b11b...

**D2 A3b replaces A3 in the verdict** (A3 frozen is still computed and reported, information only). A3b is pure
(`draft_agent2_scorer.a3b(answer, corpus, allow) -> {pass, invented, proposals}`), no I/O.
- **Sentences** as round 1 (`_sentences`: split at `.!?` + whitespace and at newlines; `’` -> `'`).
- **Proposal sentence.** Contains (a) a change verb in base form, one of `change replace rename update edit reword
  rewrite set switch swap modify adjust add declare treat accept make use` (case-insensitive, whole word; `-s/-ed/-ing`
  forms do NOT count: they describe), not immediately preceded (markdown `*`/`` ` `` stripped) by a determiner or
  modifier from `a an the this that these those its their his her our your my no any each every one which whose such
  same accepted proposed breaking spec contract code status field` or a word ending in `'s` (noun guard: "this change", "the
  spec change", "scheduler's accepted change"); or (b) an arrow `->`, `→`, `=>`; or (c) the phrase `instead of`.
- **Negation scope.** A verb of (a) does not count when one of `not no never nothing none without don't doesn't
  can't won't isn't shouldn't` occurs within the 4 words before it ("Do not change", "No rule needs to change"). A
  negation elsewhere in the sentence does not void it ("Change X; the request side did not change" is a proposal).
  (b) and (c) are never voided.
- **Claims extracted from a proposal sentence**, each checked; one unknown claim fails the session:
  import numbers (round-1 `NUM`) must be a position 1..N of `[[import]]` or an `import #k` of the corpus; rule ids
  (`\bR\d+\b`) must be an id of `lattice.spec.rules.extract_rules(REQUIREMENTS.md)` (passed in by the runner; the
  scorer stays pure) or occur as a whole word in the corpus; statuses (round-1 `STATUS`) must occur `\b`-bounded in the
  corpus; **commit-like** tokens (`\b[0-9a-f]{7,40}\b` with at least one digit and one letter, backticked or bare) must be
  a prefix of a hex string of >= 7 chars in the world's `lattice.contracts.lock.json`, its `lq1.json` or the draft text;
  **fields**: backticked `[a-z][a-z0-9_]*` tokens and double-quoted `"[a-z][a-z0-9_]*"` items inside a `[...]` list,
  checked **only when the sentence names the contract surface** (`#\d`, `import`, `imports.toml`, `request_fields`,
  `response_fields`, `field`, `\bR\d+\b`, `rule`, `REQUIREMENTS`, `spec`, `claim`); a field passes when it occurs as a
  whole word in the corpus or is on the allow-list. Code identifiers in code advice ("update any `status_code` check")
  are therefore not field claims; statuses are checked in every proposal sentence.
- **Allow-list `allow`, derived mechanically per session** (`allow_from(init, tool_results, lock, question, draft)`),
  exempting fields only (never an import number, rule id, status or unknown commit): `tools` = every `mcp__lattice__*`
  name of the session's `system/init` record, with and without the prefix; `tokens` = every string value of a key in
  `status label state class kind contract spec role action verdict` (any depth) of the JSON results of the session's
  `mcp__lattice__*` calls (no other tool contributes); `words` = round-1 `CMDWORDS` (imported); `hex` = the hex strings
  above. A session that saw no `ok` gets no `ok`.

**D3 Case table** `dogfood/contracts/draft_agent2_scorer_cases.json` + `draft_agent2_check.py` (exit 0 only when every
case gets its expected verdict; prints per group). It holds the 37 round-1 cases unchanged in text and verdict (A3 ones
scored by A3b with an empty allow-list) and **49 new cases** (section 3 V1). Fixtures in the file: corpora `C-D2`, `C-S6`
(round-1 `draft-text.txt` of sonnet-WD2-1 / sonnet-WS6-3 + the two imports files + the planted REQUIREMENTS.md, verbatim
at bb3ff0c; rule ids R1-R6 for C-D2, none for C-S6) and allow-lists `L-D2`, `L-S6` = `allow_from` applied once to the
round-1 transcripts of sonnet-WD2-1 / sonnet-WS6-3 and the rebuilt locks/questions, written verbatim into the file
(must contain `contract_check` and `ok`; must not contain any G3-G7 "fail" token: checked by the check script).

**D4 Runner** `dogfood/contracts/draft_agent2.py` imports `build_world`, `argv_for`, `tokens_of` from `draft_agent.py`
and the launch/parse helpers of `agent_probes.py`; adds per session `allow.json`, `score2.json` (A1, A2, A3b, A4, A5,
A6, A7, A3 frozen as info) and `nonlattice_tools` (any tool_use outside `mcp__lattice__*`, info). Modes:
`--write-freeze`, `--rescore-round1` (no model), sessions, `--dry-run`. Sessions: `r2-sonnet-WD2-1, r2-sonnet-WS6-1,
r2-sonnet-WD2-2, r2-sonnet-WS6-2, r2-sonnet-WD2-3, r2-sonnet-WS6-3` (tool, scored), `r2-ablation-WD2-1, -2` (no tool,
information); one fresh world per session; model `sonnet`.

**D5 Freeze, then nothing moves.** Order: V1 passes, V2 all killed, then `--write-freeze` writes `freeze.json` (sha256 of
`draft_agent2_scorer.py`, `draft_agent2_scorer_cases.json`, `draft_agent2_check.py`, `draft_agent2.py` and of every
imported round-1 file of D1; `claude --version`; check output) and the build node appends **Addendum 1** here with the
hashes. Only then V3 and V4. The runner refuses on any mismatch. V3 cannot change the scorer: a V3 surprise is a finding.

**D6 Caps and stops (Amendments 1 C and 2 of 4c, unchanged).** USD 0.50 and 8 turns per session (`--max-budget-usd`,
`--max-turns`), USD 3.00 total, stop if the first three exceed USD 1.00, stop above 400k tokens where tokens = input +
output + cache creation (cache reads reported beside, not counted). **Usage or rate limit:** that session and all later
ones are recorded `NOT RUN (usage limit)`, never retried (in a loop or later without a new orchestrator amendment),
never scored as a miss; n < 6 tool sessions means the pass rule is not evaluated. Argv stays round 1's byte for byte
(comparability); `--strict-mcp-config` (would drop the claude.ai connectors) is an owner option for a later round.

## 3. Probes

**V1 case table (blind, before any session).** 37 carried + 49 new = 86 cases, expected 86/86. New groups (C = corpus,
L = allow-list, C-D2/L-D2 unless a `next_cursor`/`a0db03` case: C-S6/L-S6; "fail" means A3b fails):

| group | n | cases (expected) |
|---|---|---|
| G1 round-1 false positives, verbatim | 7 | run 2 WD2-1 "`contract_check` still says `ok`... old commit `ef17cf88`"; WS6-3 "pinned to commit `a0db033a` (spec hash `d44b738522ea`)" and "`contract_check` currently reports `ok`..."; ablation-1 (2 sentences), ablation-2 (1), partial WD2-1 + WS6-1 (1 combined): **pass** |
| G2 round-1 true misses, full answers | 6 | ablation-WD2-1 and -2: `a2` **fail** x2, `a7` **fail** x2, `a3b` **pass** x2 |
| G3 invented claims in proposals | 10 | C-D2: "Also change import #9." / "Update R7 as well as R3." / "Change import #4 to expect 202 instead of 201." / "Set the pin in import #4 to commit `1a2b3c4d`." / "Change import #1 to accept 418."; C-S6: "Rename `next_cursor` to `next_token` in import #4." / "Change import #4 to `response_fields = [\"items\", \"has_more\", \"nxt\"]`." / "Update R2 for import #4." / "Re-pin import #4 and set the commit to `0db033af`." (substring, not prefix) / "Change the field `a0db03` in import #4." (6 hex = field): all **fail** |
| G4 allow-list and real facts in proposals | 10 | L-D2: "After you update import #4, run `contract_check` again." / "Update import #4 until `contract_check` reports `ok`." / "Update the lock of import #4 from `ef17cf88` by re-pinning." / "Update import #4 to the provider's spec `24c5fd8f` by re-pinning." / "Use `dismiss` for the import #4 question if you do not adapt." / "Update R5 to poll every 10 seconds." (implicit id) **pass** x6; "Set import #9 to `ok`." / "Change import #4 status 201 to 299 as `contract_check` says." / "Update import #4 until it reports `green`." / "Update import #4, then call `linked_question_fix`." **fail** x4 |
| G5 proposal detection | 7 | "This change touches the spec's `retry_after` field of import #4." **pass** (noun guard); "scheduler changed the status to 202 in its spec." **pass** (descriptive, the stated loosening); "The provider's changelog mentions 202 for a later release." **pass**; "You should change import #9." **fail**; "In import #4 change `state_x` to `id`." **fail**; "Edit the code, then rename `next_cursor` to `nxt` in import #4." **fail**; "`next_cursor` → `nxt` in import #4." **fail** |
| G6 negation scope | 3 | "Do not change import #9." **pass**; "Change `next_cursor` to `nxt` in import #4; the request side did not change." **fail**; "No rule needs to change, and R9 is unaffected." **pass** |
| G7 contract-surface gate | 2 | "Update any `status_code` check in the client from 201 to 200." **pass**; same with "to 202" **fail** (status checked without surface) |
| G8 `allow_from` derivation | 4 | init with `mcp__lattice__contract_check` + a `mcp__claude_ai_Gmail__*` name -> tools = the lattice name with/without prefix only; a lattice result `{"status":"ok","note":"foo_bar"}` -> `ok` in tokens, `foo_bar` not; lock `git_ref` of 40 hex -> its 7..40-char prefixes allowed, a 6-char prefix not; a non-lattice tool result `{"status":"sent"}` -> `sent` not in tokens |

**V2 scorer mutants (`draft_agent2_mutants.py`).** Each mutant is a textual patch of a scratch copy of
`draft_agent2_scorer.py` (its anchor must occur exactly once); killed iff `draft_agent2_check.py` against the copy reports
>= 1 mismatch AND the named catcher is among them. Control (no patch): 86/86.

| id | mutant | catcher |
|---|---|---|
| SM1 | allow-list removed (tools, tokens, hex) | G4 pass cases |
| SM2 | tool names not allowed | G4 "run `contract_check` again" |
| SM3 | verdict tokens not allowed | G4 "reports `ok`" |
| SM4 | commit-id rule removed (hex treated as a field) | G4 `ef17cf88`, `24c5fd8f` |
| SM5 | any 7+ hex accepted without lookup | G3 `1a2b3c4d` |
| SM6 | hex prefix match -> substring | G3 `0db033af` |
| SM7 | import numbers unchecked | G3 "#9", G4 "Set import #9" |
| SM8 | rule ids unchecked | G3 R7, R2 (C-S6 has no rule) |
| SM9 | statuses unchecked | G3 202, G7 "to 202" |
| SM10 | fields unchecked | G3 `next_token` |
| SM11 | quoted-list extraction removed | G3 `"nxt"` |
| SM12 | proposal filter removed (every sentence checked, round-1 negation) | G5 changelog, G5 "scheduler changed" |
| SM13 | negation anywhere in the sentence voids it (round-1 scope) | G6 "; the request side did not change" |
| SM14 | negation ignored | G6 "Do not change import #9" |
| SM15 | contract-surface gate removed | G7 "to 200" |
| SM16 | tokens from every string value of a result | G8 `foo_bar` |
| SM17 | noun guard removed | G5 "this change" |
| SM18 | tokens and tools from non-lattice tools too | G8 Gmail / `sent` |
| SM19 | rule ids from the corpus text only (round 1) | G4 "Update R5" |

Prediction: control 86/86, **19/19 killed** by their catchers. (G1 alone kills no mutant: the allow-list and the
filter each clear it; it is kept as the round-1 regression.)

**V3 development re-score of round 1 (report only, after the freeze, no model).** `--rescore-round1` rebuilds W-D2 and
W-S6 once each in scratch (deterministic: fixed git dates), checks the rebuilt draft `text` equals each stored
`draft-text.txt` (else that session is `NOT RESCORED (world differs)`), takes `tools`/`tokens` from each stored
`transcript.jsonl`, hex from the rebuilt lock/question/draft, and scores the 8 run-2 + 2 partial answers. Round-1 data is
never written. Predictions: draft identical 10/10; A1, A2, A4, A5, A7 equal the stored `score.json` 10/10; A3b: run 2
tool sessions **6/6** every item, ablation A3b 2/2 (A2 0/2, A7 0/2 unchanged), partial run 1 2/2. Not evidence: the scorer
was designed having seen these answers.

**V4 fresh exercise (the scored result).** 8 new sessions (D4), caps D6, freeze D5. Items A1, A2, A3b, A4, A5, A6, A7
(W-D2). **Pass: >= 5 of the 6 tool sessions satisfy every applicable item, and A6 8/8**, evaluated only when all 6 tool
sessions are scored. A3 (frozen) and the frozen verdict reported beside, never in the rule. The orchestrator reads all 8
answers whatever the score and marks per item agreement; disagreements are scorer findings, the verdict stands.
Predictions: pass rule met; tool sessions every item 5-6/6; A1 5-6/6; A3b 5-6/6; frozen-A3 verdict 3-5/6 (in round 1,
6 of 10 answers mentioned `contract_check`/`ok` or a commit); ablation A2 0-1/2, A7 0-1/2, A3b 1-2/2; per tool session
USD 0.06-0.15, 5-7 turns, 9-15k counted tokens; total USD 0.6-1.3, 80-200k counted; resolved model `claude-sonnet-5-5`;
`nonlattice_tools` 0-2 denied attempts (round 1: one denied Bash).

**V5 byte-compat and hygiene.** `git diff --exit-code bb3ff0c -- lattice/ tests/` empty; every D1 round-1 file
byte-identical; `docs/results/data/2026-10-15-consumer-draft-4d/` unchanged; `draft_agent_check.py` 37/37;
`pytest tests/contracts tests/mcp -q` same counts as bb3ff0c; ruff check + format clean on the 5 new `.py`; each session's
`argv.json` equals round 1's for the same world after replacing the scratch root and the sid; no child env key starts
with ANTHROPIC; job-scheduler, Streak, the main checkout untouched (`git status` before/after, scratch clones). All hold.

## 4. Mutants

The 19 scorer mutants of V2 (`draft_agent2_mutants.py`, scratch copies only, no scorer patched in place; not added to
`dogfood/contracts_mutants.py`, which lies outside `dogfood/contracts/`). Prediction 19/19 killed by the named catchers.

## 5. Risks and what this cannot show

1. **Circular design.** The author read every round-1 answer; V1 G1/G2 and V3 cannot fail by surprise. Only V4 is new
   data, n = 6 (+2), one model, one author of prompt, draft, scorer and cases; 5/6 vs 6/6 is noise.
2. **A3b is looser than A3 by design.** A descriptive sentence with an invented identifier ("the provider now returns
   202") passes A3b (G5); A2 still demands the right endpoint and import. A3b measures invented *proposals* only.
3. **Proposal detection is lexical.** "Consider changing" or a verb outside the list is missed; a noun use the guard
   does not list ("status quo change") counts as a proposal. The cases fix the behaviour, not the language.
4. **Allow-list breadth.** A token any lattice result returned under an enum key (e.g. `ok`) is allowed even in an
   unrelated field rename; commit prefixes of 7 chars could collide (not observed).
5. **Fields only backticked or in quoted lists**, and only on the contract surface: an unbackticked invented field,
   or a guessed code identifier, is not caught (as round 1 for the first).
6. **Session environment depends on the account** (claude.ai connectors loaded, premise 3): extra context tokens,
   tool names the model could try (denied). Kept for comparability; recorded per session.
7. **Usage limit** may cut V4 to n < 6 (NOT RUN, no verdict, no retry). **V3** relies on deterministic world rebuilds
   (guarded by the draft identity check, else NOT RESCORED).
8. Not shown: that owners act on drafts; other models; a consumer spec not written by the matcher's author.

## 6. Blind vs post hoc, and commands

Fixed now: D1-D6, the 86 cases and verdicts, the 19 mutants and catchers, pass rule, predictions. A failed prediction or
pass rule is a finding, never fixed by editing a frozen file or re-running; a scorer change after the freeze needs a new
pre-registered round. Edge cases met while coding (before the freeze) go to the results' "Post hoc" with old/new text.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-s4d2 PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-s4d2; S=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/s4d2
D=docs/results/data/2026-10-16-consumer-draft-4d-round2; export TMPDIR="$S/tmp"; mkdir -p "$TMPDIR"   # fresh, empty
git diff --exit-code bb3ff0c -- lattice/ tests/ docs/results/data/2026-10-15-consumer-draft-4d/   # V5
uv run python dogfood/contracts/draft_agent_check.py                                  # round 1: 37/37
uv run python dogfood/contracts/draft_agent2_check.py | tee "$D/cases.txt"            # V1: 86/86
uv run python dogfood/contracts/draft_agent2_mutants.py --scratch "$S/m" --out "$D/mutants"   # V2: 19/19
uv run python dogfood/contracts/draft_agent2.py --write-freeze --out "$D/agent"       # D5, then Addendum 1
uv run python dogfood/contracts/draft_agent2.py --rescore-round1 --scratch "$S/r" --out "$D/rescore-round1"  # V3
uv run python dogfood/contracts/draft_agent2.py --dry-run --scratch "$S/d" --out "$D/agent"   # argv check, no model
uv run python dogfood/contracts/draft_agent2.py --scratch "$S/a" --out "$D/agent"     # V4 (refuses on a hash mismatch)
uv run pytest tests/contracts tests/mcp -q; uv run ruff check dogfood/contracts/draft_agent2*.py && uv run ruff format --check dogfood/contracts/draft_agent2*.py
```

## 7. File ownership (build node)

- **Create:** `dogfood/contracts/{draft_agent2.py,draft_agent2_scorer.py,draft_agent2_scorer_cases.json,
  draft_agent2_check.py,draft_agent2_mutants.py}`; the results doc; `docs/results/data/2026-10-16-consumer-draft-4d-round2/`.
- **Edit:** this file only by appending Addendum 1 (freeze hashes) and orchestrator amendments.
- **Read-only:** everything under `lattice/` and `tests/`; `dogfood/contracts/{draft_agent.py,draft_agent_scorer.py,
  draft_agent_scorer_cases.json,draft_agent_check.py,agent_scorer.py,agent_probes.py,draft_probes.py}` (imported, not
  edited); `dogfood/contracts_mutants.py`; round-1 data. Nothing is committed by the build node.

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)

**Approved as designed:** A3b replaces A3 in the verdict with the frozen A3 shown beside it for information; the
claim extractor, the mechanically built per-session allow-list, the 86-case table (37 round-1 cases unchanged
+ 49 new), 19 scorer mutants, the order (cases, mutants, hash freeze, then development re-score, then the fresh
exercise), the NOT RUN rule on a usage/rate limit, and the byte-compat gate (`git diff bb3ff0c -- lattice/
tests/` and the round-1 data directory empty).
**Rulings.** (1) **Honesty about the scorer.** The scorer was designed having read all 10 round-1 answers; the
re-score of round 1 is a development figure and the case table is not independent evidence. Only the fresh
sessions count, n = 6, and the results say so in the first paragraph. (2) **A3b is looser than A3 by design**
(a descriptive sentence with an invented status passes); to keep that visible the runner also reports an
informational `A3b-strict` that applies the status/rule-id/hex checks to every sentence, and the results show
both beside the verdict. The pass rule is on A3b as registered. (3) **Connectors.** The round-1 sessions loaded
the account's claude.ai connectors (about 80 tool names) despite `--setting-sources ""`. Keep argv
byte-identical to round 1 so the rounds are comparable, record every tool call, and treat any non-`lattice`
tool call as an automatic fail of the session and a finding (none occurred in round 1). `--strict-mcp-config`
is NOT added (it would change the argv); it is noted as an owner option. (4) The development re-score marks a
session NOT RESCORED if its rebuilt draft differs from the stored `draft-text.txt`. (5) Caps USD 0.50 and 8
turns per session, USD 3 total, tokens = input + output + cache creation, `claude` CLI only, no API key; on a
usage/rate limit the remaining sessions are NOT RUN and the pass rule is then not evaluated.

## Addendum 1 (build node B-S4D2, 2026-10-16T17:44:19Z: the freeze, before any re-score or session)

Order kept: case table (86/86, `cases.txt`), scorer mutants (19/19 killed by their catchers, control 86/86,
`mutants.json`), then this freeze (`data/2026-10-16-consumer-draft-4d-round2/agent/freeze.json`, written by
`draft_agent2.py --write-freeze`; git HEAD 3f2c0ee06617a690a010cd723ad8c375f276d3b5; `claude --version` 2.1.299
(Claude Code); round-1 case table 37/37). sha256 of every frozen file:

| file | sha256 |
|---|---|
| draft_agent2_scorer.py | 79e546f1cfdbd7f581b54c04c12be52c2eddb5e10a43ad8e9c123267e79b2379 |
| draft_agent2_scorer_cases.json | 4593f645e559758c67836ad0611b0fe4d22991810d370f3c690460d8d0e62c12 |
| draft_agent2_check.py | 32ad83f508a93bb0ee146e32b24af3fb1ffd9aa6ab599ac6d63fb6842b6c37ee |
| draft_agent2.py | c639426584eb185c4408821c533caa18a03c14a11fc7b32a989c0b9d3de32945 |
| draft_agent.py | f4eebedf57c86e236d37ae4f0db852a31d4f697b58ecb0976f69a81970c944cf |
| draft_agent_scorer.py | 97395b9e5007ceb32e7cabd45f610525561858e2e8fc64efc470b055613b20ad |
| draft_agent_scorer_cases.json | 32201ba412ebe0ec682a6778ac4458276ab7513621948ecd2aad2d4abcc0581a |
| draft_agent_check.py | 46b667681b9271122794518686e3dda9e727aead78648ebbbbf0c9b97c5fcbfe |
| agent_scorer.py | 92fb5a76aec490ca059cfea2f09d787ad4377243c3bb6545921f5f4cbb322d08 |
| agent_probes.py | bbd1fed1e0631492e4636a1fab5d3e110b61cac3e52446dd01c0882194fa0509 |
| draft_probes.py | 3782b11bb7a555f2540cc0501ccbe1f3dc0c337f5785c432b74fb0ef4347b0fb |

Information only (not in the refusal check): `draft_agent2_mutants.py` b92ddfe23201995d7b7f9e95a49ea9e1c492d3d22fb38c3ed582bd62381d7823.
Two readings fixed here, before any re-score: (1) the Amendment 1 "any non-`lattice` tool call is an automatic fail"
is applied literally (a denied attempt counts; round 1 had one denied Bash, in sonnet-WS6-1, so "none occurred in
round 1" holds only for executed calls); the runner also reports the pass count ignoring denied attempts, beside
the registered verdict. (2) The 19 mutants SM1-SM19 are the textual patches of `draft_agent2_mutants.py`; their
catcher case ids are in that file.
