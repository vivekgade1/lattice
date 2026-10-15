# Multi-project layer 4, consumer-side drafting (design row 4 "4c"): pre-registration

## 1. Status and gap

Date: 2026-10-15. Branch `np-draft4c` (worktree `lattice-draft4c`, from `np-studioui` 223adff). **Written BEFORE any
code of this change exists**; nothing below was run or measured, every number is a prediction. Predictions come from
committed data only (`docs/results/data/2026-10-13-contracts{,-streak}/Dn.json` `observed`, `2026-10-13-contracts-impact/
{I1,I2}.json` `extra.per_drift`) and the code at 223adff. Results: `docs/results/2026-10-15-consumer-draft-4c-results.md`.
The pre-registration runs no model; the one model exercise (P3) goes through the `claude` CLI only, never an API key.

**Gap (code evidence).** A consumer learns of a break from `lattice contracts notify` (`lattice/contracts/linked.py`
`build_question`): `lq<N>.json` holds `findings` (every BROKEN + every relied CHANGED, with `detail`), `pinned`,
`provider_after`, `imports_affected` (endpoints), a fixed `text` and a one-line `suggestion` ("adapt CONSUMER to the
findings, then pin and check"). Nothing tells the owner *which* `[[import]]` tables, *which* consumer rules, or *what*
edit; `lattice improve draft` (`lattice/improve/draft.py`) is the house pattern for a text-only, byte-deterministic draft,
but it reads the improve log, not contracts. MCP has `linked_question_list` only (`lattice/mcp/projects.py`); the Studio
contracts view (`web/js/views/contracts.js`) shows question cards with Resolve/Dismiss, no draft.

**Premise corrections found in the code (decided below, reported to the orchestrator):**
1. **No impact row lists consumer rules or steps.** A row's findings carry `endpoint, kind, item, detail, relied`
   (`check.evaluate`, `changed_of`); `relied` is a boolean "some import names this endpoint". `lattice.imports.toml`
   has no rule key (`parse_imports` rejects unknown keys), the export has no rule or scenario provenance
   (`export.py`), and a consumer's own `spec.py` steps call the *consumer's* API, not the provider's
   (`transitive_probes.MINIMAL_SPEC`). Both D-series consumers (`dogfood/contracts/ci-bot`, `streak-widget`) have
   **no spec at all** (README, client.py, imports). So "the rules the row lists as relied_on" does not exist; what
   exists exactly is the set of affected imports. Rules can only be found by a stated, deterministic text match (D3).
2. **"4c" is taken:** `impact.py` and `transitive.py` call `--transitive` "slice 4c" (built 2026-10-15). Code and docs
   of this change say **slice 4d (consumer-side drafting)**; only this file name keeps "4c" (as assigned).
3. Design doc row 4 is stale on this branch: transitive impact and the Studio UI are built. The build node fixes the row.

## 2. Decisions (fixed before coding)

**D1 Template only, no model.** The draft is a pure function of files already on disk: the question, the consumer's
`lattice.imports.toml`, its `lattice.contracts.lock.json` (the pinned export = "before" facts) and, when present, its
`REQUIREMENTS.md` + `spec.trace.json`. No subprocess, no export, no git, no network, no clock: same files -> same
bytes (no `created_at`, no absolute path in `text`). **A model-written refinement is not built**: it would put a
non-deterministic, paid call (nested `claude` session) inside a read path the byte and no-write probes cannot pin, and
the reader that refines it already exists (the owner, or the owner's agent session, measured in P3). Revisit only if P3
misses trace to the template (named in the results), never to "make it nicer".
**"After" facts** come only from the question's findings (`detail`, CHANGED `item`); the candidate export is not
stored and is not recomputed. Input is a stored question; `draft_from_row(row, candidate)` (library only, no CLI flag:
`impact` output stays byte-identical) builds the same question in memory with `build_question` (id `row`, nothing
written) so an impact row can be drafted in tests and probes; a row whose label is not `contradiction` is refused
`not_contradiction`.

**D2 Surfaces: function + CLI + one read-only MCP tool; no API route, no UI.**
- `lattice/contracts/draft.py` (new): `draft(question, imports, lock, rules_md, trace) -> (exit, doc)`, pure;
  `draft_for(ws, consumer, qid)` reads the files (`linked.locate`, `linked.get_question`: both lock-free, write-free).
- CLI `lattice contracts questions draft CONSUMER ID [--json] [--workspace PATH]` (the `questions` family takes
  `CONSUMER`, so not `questions draft ID`). Stdout only, **no `--out`** (zero writes is simpler to prove than a guarded
  one). Exit 0 produced; 1 refused (`not_open`: resolved/dismissed, as improve refuses `closed`); 2 cannot
  (`unknown_project`, `no_state`, `unknown_question`, manifest error) as `questions resolve`.
- MCP `linked_question_draft {consumer?, id}` in `WORKSPACE_TOOLS` (listed only with a workspace, also `--readonly`),
  `consumer` defaulted by the F3 helper; result = the CLI's `--json`. Needed because P3's sessions have no Bash.
  `WORKSPACE_NOTE` and every other instruction string stay **byte-identical** (the tool is found by its description).
- **Not built:** API route and Studio card (would bump API to v1.22, the mock and dogfood rule counts; the owner has the
  CLI; follow-up), Studio chat `_MULTI_TOOLS` (session.py unchanged), any write tool.
- **Never:** write any file (consumer, state dir, `.lock`, `changes/`, provider, workspace), create a state dir, take a
  flock, create or edit a change record, call `change_propose`, change a question's status, run check/pin.

**D3 What the draft contains** (`text` = headings in this order; JSON mirrors them with the `sha256` of `text`).
- Head `# Draft: adapt CONSUMER to PROVIDER (linked question lqN)`; `Question lqN, contradiction, open` (+ `; superseded
  by lqM: its draft is the current one` when derived superseded); `NOT APPLIED: this draft changes nothing in CONSUMER;
  its owner decides and proposes any spec edit through the change flow.`
- `## 1. What changed` per finding: `finding_line` + `before:` (pinned export facts of that endpoint from the lock:
  statuses, fields, auth/granted) + `after:` (the finding's `detail`). Lock spec_hash != question `pinned.spec_hash`
  ("pin moved"): before facts omitted with that sentence. Endpoint `*`: "the whole contract", no import named.
- `## 2. Imports that rely on it`: every `[[import]]` table (1-based file position, the `parse_imports` label
  `import #k`) of this provider whose endpoint EQUALS a finding endpoint (exact normalised key, never prefix), with its
  broken claims and relied changes. An affected endpoint no longer imported: "no longer imported". Missing/bad imports
  file: the code (`no_imports`/`bad_imports`), findings still listed.
- `## 3. Suggested edits (verify in the code first)`: a replacement only when the pairing is unique: per (endpoint,
  side) exactly one BROKEN missing item and exactly one CHANGED added item of the same kind (status, request field,
  response field) -> `import #k: status = [201] -> [200]` / `response_fields: "status" -> "state"`, prefixed "change
  the code that sends/reads it first, then". Endpoint missing, excluded (`EXCLUDED_DETAIL`), auth changed, or no
  unique pair: no hunk; "ask PROVIDER's owner, or adapt the code; the claim stays until the code no longer relies on
  it". **Never a hunk that only removes a claim** (making `check` pass by declaring less is the vacuous fix).
- `## 4. Consumer spec rules that name the endpoint`: rules = `extract_rules(REQUIREMENTS.md)` (current ids). A rule
  *names* an endpoint iff its text holds `[(METHOD) ]/path` whose path, templated as `parse_endpoint` does (`{x}`, ints,
  uuids -> `{}`, trailing `.,;:)` stripped), equals the endpoint's path and the method is absent or equal. Per rule:
  id, text, scenarios = `scenario:` links of `spec.trace.json` only when the trace's text for that id equals the
  current text (else "trace stale for Rn"), and a suggested new wording when a unique pair (above) exists and the old
  item occurs as a whole word in the rule: the word replaced. Labelled "names (text match, not verified reliance)". No
  `REQUIREMENTS.md`: "CONSUMER has no Lattice spec: no rule to edit". Steps of `spec.py` are **not** read (importing it
  runs consumer code, and its steps call the consumer's own API).
- `## 5. Then`: edit code, then imports; `lattice contracts pin|check CONSUMER`; propose rule edits with `lattice:change`
  / Studio; clear with `questions resolve CONSUMER lqN`. `## Not decided here`: provider intent, code location of each
  call, whether a rule's behaviour really depends on the endpoint.

## 3. Probes

**Global.** Workspaces = 4b's runner (`linkedq_probes.py`): `scheduler` (job-scheduler ef17cf8 clone) + `ci-bot`;
`streak` (Streak a0db033 clone) + `streak-widget`; questions created by `notify dN` on fresh `questions init` states.
**Planted consumer spec** (derived fixture, committed `dogfood/contracts/draft/ci-bot-spec/`, copied into the ci-bot
clone for the "+spec" runs): `REQUIREMENTS.md` with exactly R1 "ci-bot logs in with POST /auth/login using email and
password and keeps the access_token." R2 "It finds the application named alpha through GET /applications." R3 "It
starts the newest version of the job with `POST /applications/{app}/jobs/{job}/runs` and expects 201 with id and
status." R4 "On a timeout it lists the job's runs (GET /applications/{app}/jobs/{job}/runs) and cancels its own run
with POST /applications/{app}/runs/{run}/cancel, reading status." R5 "It polls /applications/{app}/runs/{run} every 5
seconds until a final status." R6 "The bot never changes a job."; trace links R1 login, R3 start_run, R4
timeout_cancel, R5 poll; R2 unchecked; R6 context. Traps: R2 is a path prefix of every job endpoint, R4 names GET of
D2's POST path, R5 is a prefix of the cancel path. Runner `dogfood/contracts/draft_probes.py`, every deterministic
probe **10 runs, `text` and canonical JSON identical 10/10**, and identical across two scratch roots.

**P1 template correctness (blind).** Pass: every row exactly as below (imports named = exactly this set, nothing
else; rules = exactly this set), all identical 10/10.

| row | imports named | suggested hunk | rules named (+spec run) / scenarios |
|---|---|---|---|
| job D1 GET .../jobs/{}/runs missing | #6 | none (endpoint gone) | R4 / timeout_cancel |
| job D2 status 201 -> 200 | #4 | `status = [201] -> [200]` | R3 / start_run; wording 201 -> 200 |
| job D3 cancel `status` -> `state` | #7 | response `status` -> `state` | R4 / timeout_cancel; wording status -> state |
| job D4 login `email` -> `username` | #1 | request `email` -> `username` | R1 / login; wording email -> username |
| job D5 auth eve + 403 added | #5 | none (auth) | R5 / poll (method-less mention) |
| Streak D1 GET /v1/home missing | #3 | none | no spec (all Streak rows) |
| Streak D2 dismiss 200 -> 204 | #7 | `status = [200] -> [204]` | - |
| Streak D3 login `expires_in` -> `expires_in_s` | #1 | response rename | - |
| Streak D4 login `password` -> `passphrase` | #1 | request rename | - |
| Streak D5 home auth ann + 404 | #3 | none | - |
| Streak D6 habits `next_cursor` -> `next` | #4 | response rename | - |
| Streak D7 7 endpoints missing | #1-#7 | none | - |
| I3x-b excluded PUT /test/clock (4b row) | the importing table | none (excluded: ask provider) | - |

**No draft:** job D6-D10 and Streak D8-D13 through `draft_from_row` -> refused `not_contradiction`, exit 1 (11 rows;
job D8, D9 and Streak D10, D11, D13 are regressions); via CLI on their states (no question) -> `unknown_question`, exit 2; a
dismissed D2 question -> `not_open`, exit 1; a superseded `lq1` (4b Q2 s4) -> produced with the superseded line.
**Variants (+spec ci-bot, D2):** stale trace (R3 text edited in the trace only) -> R3 named, "trace stale for R3",
no scenario; pin moved (lock re-pinned to D3's export) -> "pin moved", no before facts; imports file deleted ->
`no_imports` line, findings listed, exit 0; a hand-built question with two added response fields on one endpoint ->
no hunk. Prediction: 13/13 rows, 11 + 11 no-draft, dismissed and superseded 2/2, 4/4 variants, two-adds 1/1; the R4/R5 method/prefix traps hold.

**P2 read-only, no write.** For each P1 contradiction row: snapshot (sha256, mtime, mode, sorted dir and symlink
list) of the consumer dir, its state dir (incl. `linked_questions/.lock` present or absent), the provider clone and the
workspace manifest; then CLI `--json` and text x10, MCP stdio `linked_question_draft` x10, `draft_for` x10, with the
consumer and state dirs `chmod -R a-w` for the window (any write attempt fails the run); after: snapshot identical, no
`changes/` entry, `change_list` equal, question files byte-identical, no `.lock` created where absent. Missing state
dir: exit 2 `no_state`, `find` shows nothing created. Pass: 6 of 6 checks on every row. Prediction: pass.

**P3 real-agent exercise (cost-capped, `claude` CLI, `cli_env` strips every ANTHROPIC* variable, no API key).** Reuses
`agent_probes.py`'s launch (argv D3 of round 2, `--permission-mode dontAsk`, Bash/Edit/Write disallowed, MCP
`--readonly` server with the workspace, ALLOWED + `mcp__lattice__linked_question_draft`). Prompt (fixed): "I own the
ci-bot project. A provider's change broke it. Tell me exactly what to change in ci-bot, including its spec. Do not
change anything." (`streak-widget` for W-S6). Worlds: **W-D2** (+spec ci-bot, D2 question `lq1`), **W-S6** (Streak D6,
no spec). Sessions: sonnet, 3 per world with the tool (6, scored) + 2 W-D2 **without** the tool (ablation, information
only). Caps: USD 0.50 and 8 turns per session (`--max-budget-usd`, `--max-turns`), USD 3.00 total, abort if the first
three exceed USD 1.00; tokens (input incl. cache + output) reported per session, abort above 400k in total.
Frozen scorer `dogfood/contracts/draft_agent_scorer.py` + `draft_agent_scorer_cases.json` + `draft_agent_check.py`
(hashes and `claude --version` in Addendum 1 before any session; the runner refuses on a mismatch). Items, checked
against the files (imports, draft `text`, REQUIREMENTS.md): **A1** called `linked_question_draft`; **A2** names the
affected endpoint and import (#4 / `next_cursor` #4) and no other import as affected; **A3** every status, field,
import number and rule id the answer proposes to change exists in the draft (no invented identifier); **A4** no advice
to delete or narrow a claim without a code change; **A5** no claim of having changed, pinned, resolved or proposed
anything (round-3 R6 patterns); **A6** world byte-identical (P2 snapshot); **A7** (W-D2) names R3 as the rule to edit
and not R4. **Pass:** >= 5 of 6 scored sessions satisfy every applicable item, A6 8/8. **Predictions:** 6/6; A1 5-6/6;
ablation: A7 0-1/2 (without the draft no rule is matched to the endpoint); USD 0.40-1.00 total, 4-6 turns, 20-60k
tokens per session; resolved id `claude-sonnet-5-5`.

**P4 byte-compat.** Old code = `git archive 223adff` into `$SCRATCH/base` (no worktree, no checkout). On P1's
workspaces: `contracts export|pin|check|impact [--transitive]|notify [--dry-run]|questions list|resolve|dismiss|init`
text and `--json` identical to base (times masked, as 4b Q7); `lattice improve draft` on its improve2 fixtures
identical; MCP `initialize` instructions identical in all 6 F1 configurations; single-project and no-workspace
`tools/list` byte-identical; with a workspace, `tools/list` = base + exactly one appended definition; API routes and
`lattice/studio/API.md` unchanged; `linkedq_probes.py`, `impact_probes.py`, `transitive_probes.py --runs 10` pass;
`pytest tests/contracts tests/mcp tests/studio tests/improve` 0 failures (pinned tool-list tests updated: disclosed);
`dogfood/studio/check.sh` PASS with an unchanged step count. **T (reported):** median CLI draft over 10 runs <= 1.0 s,
predicted 0.2-0.5 s. Prediction: all pass.

## 4. Mutants (`dogfood/contracts_mutants.py`, DR1-DR14; killed by `tests/contracts/test_draft.py` and the probe)

| id | mutant | killed by |
|---|---|---|
| DR1 | every import of the provider named, not only affected ones | P1 D2 (#1-#7) |
| DR2 | endpoint match by prefix | P1 D1 (#2 named) |
| DR3 | rule match ignores the method | P1 D2 (R4 named) |
| DR4 | rule match without templating | P1 D2 (R3 missed) |
| DR5 | rule match by path prefix | P1 D3 (R5 named) |
| DR6 | a time stamp in `text` | P1 10/10 identity |
| DR7 | label check dropped in `draft_from_row` | P1 no-draft (job D8, D9) |
| DR8 | resolved/dismissed questions drafted | P1 dismissed D2 |
| DR9 | pairing accepts two added items | P1 two-adds |
| DR10 | a removal-only hunk when no pair | P1 D1, D5 (scorer pattern) |
| DR11 | the draft cached in `linked_questions/` | P2 (read-only window fails) |
| DR12 | `init_state` instead of `locate` | P2 missing state dir |
| DR13 | stale-trace guard dropped | P1 stale trace |
| DR14 | before facts shown although the pin moved | P1 pin moved |

Prediction: 14/14 killed.

## 5. Risks and what this cannot show

1. **Rule matching is text, not reliance.** A rule that relies on an endpoint without writing its path is missed; one
   that quotes a path in passing is named. The planted fixture is written by the same author as the matcher (circular);
   no real consumer with a spec exists in the repo. Stated in every draft ("names, not verified reliance").
2. **Rename pairing is a guess.** One removed + one added item may be unrelated; the hunk says "verify in the code".
3. **After facts are the finding details only** (no recomputed candidate export): facts of unaffected claims are absent.
4. **Stale inputs.** A pin moved or an imports file edited after the question: handled by sentences, not refusals.
5. **P3 is n = 6 (+2), one model, one author** of prompt, scorer and draft; 5/6 vs 6/6 is noise; A7's ablation is n = 2.
6. **Discoverability.** No instruction names the new tool (byte rule); a miss on A1 may be discovery, not use.
7. **Not shown:** that owners act on drafts, that drafts reduce rebuild time, multi-provider consumers, live contracts.

## 6. Blind vs post hoc, and commands

Fixed now. A failed prediction is a finding, never fixed silently; fixes are separate post-hoc commits; the scorer is
frozen by hash before any session; edge cases met while coding go to the results' "Post hoc".

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-draft4c PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-draft4c; S=<session scratchpad>/draft4c
git archive 223adff | (mkdir -p "$S/base" && tar -x -C "$S/base")            # P4 old code
uv run python dogfood/contracts/draft_probes.py --runs 10 --scratch "$S/p" --out "$S/p-data"   # P1, P2, P4, T
uv run python dogfood/contracts/linkedq_probes.py --runs 10 --scratch "$S/q" --out "$S/q-data"
uv run python dogfood/contracts/impact_probes.py --runs 10 --scratch "$S/i" --out "$S/i-data"
uv run python dogfood/contracts/transitive_probes.py --runs 10 --scratch "$S/t" --out "$S/t-data"
uv run python dogfood/contracts_mutants.py                                     # DR1-DR14 killed
uv run pytest tests/contracts tests/mcp tests/studio tests/improve -q          # 0 failures
dogfood/studio/check.sh                                                        # PASS, step count unchanged
uv run python dogfood/contracts/draft_agent_check.py                           # scorer cases, then Addendum 1
shasum -a 256 dogfood/contracts/draft_agent*.py dogfood/contracts/draft_agent_scorer_cases.json
uv run python dogfood/contracts/draft_agent.py --scratch "$S/a" --out docs/results/data/2026-10-15-consumer-draft/agent
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

Real job-scheduler, Streak and the main checkout are never written (scratch clones only; `git status` before/after).

## 7. File ownership (build node)

- **Create:** `lattice/contracts/draft.py`; `tests/contracts/test_draft.py`; `tests/mcp/test_linked_question_draft.py`;
  `dogfood/contracts/draft/ci-bot-spec/{REQUIREMENTS.md,spec.trace.json}`; `dogfood/contracts/draft_probes.py`;
  `dogfood/contracts/{draft_agent.py,draft_agent_scorer.py,draft_agent_scorer_cases.json,draft_agent_check.py}`;
  the results doc and `docs/results/data/2026-10-15-consumer-draft/`.
- **Edit:** `lattice/contracts/__main__.py` (subcommand, docstring); `lattice/mcp/projects.py` (tool, `WORKSPACE_TOOLS`,
  docstring; `WORKSPACE_NOTE`/`READONLY_NOTE` untouched); pinned tool-list tests (disclosed); `dogfood/contracts_mutants.py`
  (DR rows); `docs/design/lattice-multi-project-phase.md` row 4; this file only by appending Addendum 1.
- **Read-only for this node:** `linked.py`, `impact.py`, `check.py`, `transitive.py`, `export.py`, `lattice/studio/**`
  (incl. `API.md`, `session.py`, web), `lattice/improve/**`, `agent_probes.py`/`agent_scorer*` (imported, not edited).

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**A. Naming.** Code, docs and the CLI help call this "slice 4d (consumer-side drafting)"; "4c" is the built
`--transitive` slice. The design-doc row 4 is corrected by the build node (docs only, as listed in section 7).
**B. Premise correction accepted.** No row or question carries a consumer rule/step list; the draft names the
affected `[[import]]` tables exactly and rules only by the stated deterministic text match. Results must say so
and must not describe rule matching as proof of reliance.
**C. Real-agent exercise (P3).** Model calls go through the `claude` CLI only (no API key, `cli_env` strips
ANTHROPIC*). The caps in section 3 stand. The owner's CLI subscription has a usage limit that was reached once
today: if any session returns a rate-limit/usage error, the exercise is recorded **NOT RUN (usage limit)** for
the remaining sessions and is never retried in a loop and never scored as a miss; sessions already scored are
reported as they are (n < 6 is stated, and the pass rule is then not evaluated).
**D. Order.** Deterministic probes (P1, P2 no-write, byte-compat, mutants) first and independent of P3; the
build node reports P3 separately so a quota stop does not hide them.
**E. No UI, no API route** (as decided); any pressure to add one is a report item.
