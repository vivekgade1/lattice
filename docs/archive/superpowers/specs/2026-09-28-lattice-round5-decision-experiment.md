# Round 5 decision experiment: is the value in the language or in the checks?

> Status: PRE-REGISTERED 2026-09-28, before any round-5 run. The decision rule in §5 is fixed now and applied mechanically to the results. Evidence so far: rounds 1–4 in `docs/results/2026-09-26-bench-smoke.md`.

## 1. The question

In round 4, LLM edits made through Lattice (arm **L**) produced about **half the silent bugs** of edits made directly to Python (arm **P**). They were also **less often correct** (loud failures) and cost **2.7–3.7× more per correct answer**.

Does the benefit come from **the language** (writing in Lattice), or from **the guarantee checks** that Lattice enforces? The checks could also run on ordinary Python.

## 2. Hypotheses

- **H-check:** a model that edits ordinary Python, and gets Lattice's guarantee checks as repair feedback, achieves Lattice's silent-bug reduction at Python's correctness and cost.
- **H-language:** writing in Lattice itself is necessary. Python plus the same checks keeps most of Python's silent bugs, because many guarantees can't be detected reliably in free-form Python.

## 3. The three arms

All three use the same tickets, the same models, the same effort and output limit, the same repair budget (2 rounds), and the same transport (the Claude CLI, option A).

| Arm | The model edits | The first prompt contains | Repair feedback |
|---|---|---|---|
| **P** | Python/FastAPI files | the ticket and files (as in rounds 1–4) | start-up traceback and a smoke run of the ticket's main request |
| **L** | Lattice programs | the ticket, programs, profile and Lattice guide | Lattice checker diagnostics, plus the same smoke run |
| **Q (new)** | Python/FastAPI files | the ticket and files, **plus a short statement of the policies that will be checked**, with the same information the Lattice guide gives about those guarantees | **Python guarantee-check findings**, plus the same smoke run |

The **Python guarantee checks** (the new `lattice/pycheck/`) run on the edited Python and reuse the importer's extraction and code graph. They cover:
1. **Ownership:** a route that reads or writes rows of an owned table without filtering by the caller (or an explicit admin branch).
2. **Private fields:** a private column (e.g. a password hash) reaching a response.
3. **Unbounded inputs:** request strings or numbers written to the database or used as limits without bounds.
4. **Route shadowing:** a literal path declared after a parameter path it collides with.
5. **Declared errors:** an `HTTPException` status not handled consistently with the app's conventions (e.g. 403 vs 404 for another user's row), where that can be detected.
6. **Naive/aware datetime mixing** in comparisons.

Each finding has a line, a message and a concrete fix hint, like Lattice's diagnostics. The checks see only the edited code and never the hidden tests.

## 4. Metrics

- **Scope:** measured on the **non-tuned tickets t7–t30**. t1–t6 are reported separately and excluded from the decision.
- **Primary:** silent failures per run.
- **Secondary:** correct rate, cost per correct answer, loud rate, and first-round pass rate. For Q, the first-round pass rate means no check findings.

## 5. Decision rule (applied per model, then combined)

Let S_x be the silent failures, C_x the correct runs, and $_x the cost per correct answer for arm x, over t7–t30.

1. **Checks are enough → pivot the product.** Q wins if, **on both models**, all of the following hold:
   - S_Q ≤ S_L + 2;
   - C_Q ≥ C_L;
   - $_Q ≤ 1.5 × $_P.

   Then the product is a guarantee checker plus verified-edit loop for ordinary code. The Lattice language becomes an internal representation, used only where it is lossless (SQL compilation, verified generation).
2. **The language is needed → keep Lattice as the interface.** L wins if, **on both models**:
   - S_L ≤ 0.7 × S_Q (Lattice has at least 30% fewer silent bugs than Python plus checks);
   - and S_Q is not below S_P by a similar margin that closes the gap.

   Then freeze the language surface and invest in teaching models Lattice: retrieval of verified examples, then fine-tuning.
3. **Otherwise the result is mixed.** Report it as mixed, and name which guarantees Q misses and which ones L gets right. The likely outcome is a hybrid: Lattice for data-access logic, checks for everything else.

## 6. Run size and cost

30 tickets × 3 arms × 2 models × 3 reps = **540 runs**, through the CLI, with resume. Plan usage limits may pause the run; it resumes where it stopped.

## 7. Amendment (2026-09-28, before any round-5 run): context-aid arms L+ and Q+

**Question:** is Lattice's low first-round pass rate (27–37%) mostly a *knowledge* problem that in-context aids can fix?

| Arm | Starts from | Adds |
|---|---|---|
| **L+** | arm L | (a) **2 retrieved worked examples**; (b) a **"common mistakes → exact fix" table** |
| **Q+** | arm Q | the same two aids, in Python form |

**(a) The retrieved worked examples**
- They are verified Lattice programs for the most similar other tickets, by TF-IDF over the ticket text.
- The corpus is the Lattice references of t1–t12 only, and **never the current ticket's own reference**.
- t13–t30 references don't exist in Lattice, so nothing held-out can leak.

**(b) The "common mistakes → exact fix" table**
- It is built from the round-4 repair-persistence analysis (node C3).
- It is capped at the same length for both arms.

**What Q+ gets:** retrieved Python references from the same t1–t12 corpus, with the same retrieval, the same exclusion and the same count. Its mistakes table is built from round-4 arm-P silent failures, as classified by the Python checks, at equal length.

**Decision use:**
- The §5 rule is still applied to **P, L and Q**, unchanged.
- L+ and Q+ answer a secondary question. If L+ closes at least half of L's correctness gap to P on t7–t30 on both models, the knowledge problem is fixable in context. That strengthens the "language is needed" branch, if it wins.
- Q+ vs L+ repeats the §5 comparison with aids on both sides.

**Run size:** 30 tickets × 5 arms × 2 models × 3 reps = **900 runs**, through the CLI with resume.

## 8. Run notes (implementation, 2026-09-28, before any round-5 run)

These record how §3 and §7 are wired in `bench/edit_slice/run.py`. They change no hypothesis, metric or decision rule.

- **Arm Q.** It is arm P's flow: the same start state, answer format and user message, with `q_system_prompt` as the system prompt. After the smoke run, pycheck checks the candidate. Its findings (code, file, line, message, fix hint) are added to the repair feedback in the format of L's checker diagnostics (`- PY801 at app/…, line N: … . Fix: …`). A round that starts and passes the smoke run but has findings is repaired, with problem `check`. Findings on the final round do not change scoring: the oracle decides. `round_findings` records each round's codes, which gives Q's first-round pass rate (§4).
- **Arm L shows the checker's exact fixes.** When a diagnostic carries a machine fix (`Diagnostic.fix`, from C3), L's and L+'s repair feedback adds an `Exact fix: replace line … with: …` line under it. Arm Q's hints already name their fix, so this keeps the two arms' feedback symmetric. Arm L in rounds 1–4 did not have it.
- **Aid (a), the worked examples.** Retrieval is TF-IDF cosine over the ticket title and body, from the corpus t1–t12, excluding the current ticket; ties are broken by corpus order. Each example is the ticket and the verified answer that the dry run feeds through the arm (`reference_answer`): Lattice for L+, Python for Q+. Both arms get the same tickets in the same order. Answers of the same edit are shorter in Lattice: over t1–t30, the examples average 10.4k characters for L+ and 16.1k for Q+ (0.65×). They are not trimmed to equal size, because §7 asks for verified examples, with the same retrieval and count.
- **Aid (b), the mistakes tables.** `python -m bench.edit_slice.build_aids` builds both tables from the round-4 artefacts and writes them to `bench/edit_slice/aids/mistakes_{lattice,python}.json`. Those committed files are the fixed version round 5 uses.
  - **L+:** from C3 persistence. Codes are ranked by repeated refusals, then by persistence. Each row is today's checker message and hint.
  - **Q+:** from the 45 round-4 arm-P silent runs. pycheck flags 18 of them. Codes are ranked by the number of runs they flag.
  - **"The same length":** both tables have the same character budget. The budget is the smaller full table; each table takes its rows in rank order while it fits.
  - **Result:** L+ has 4 rows in 1084 characters (L404, L302, L401, L411). Q+ has 3 rows in 1119 characters (PY801, PY805, PY806).
- **Placement.** The table is appended to the system prompt, and the examples come before the first user message. Nothing else of the base arm's prompt changes; a test pins this.
- **Dry run.** Fed through Q and Q+, P's references score correct on t1–t30 with 0 pycheck findings. Fed through L+, the Lattice references score correct on t1–t12.
- **Harness fix (`--workers`).** Building an emitted app used to swap the template's module-global `api_router`. Parallel workers could interleave those swaps and leave the router empty. App construction no longer mutates shared state, and `load_variant` is atomic. Round 5 must run on this fixed harness.

### 8.1 Review fixes (2026-09-28, before any live round-5 run)

A review found problems that would bias the experiment. Each is fixed, with a test, before any live run. They change no hypothesis, metric or decision rule (§1–§7). Where a bullet above says otherwise, this subsection supersedes it.

- **C1. The mistakes tables are ticket-neutral and oracle-blind.** The Q+ table used to name t29's route and its exact fix, and t4's and t2's traps. Its rows were selected by the oracle's silent verdict and shown to every Q+ run, including those tickets. The L+ table named `Note`. One rule now builds both tables, and they are regenerated (`python -m bench.edit_slice.build_aids`):
  - **Neutral rows.** The evidence only ranks codes. Each row is its code's generic text (`build_aids.GENERIC_ROWS`): the checker's or pycheck's message and hint, with every app-specific identifier (route paths, function, table and column names, program variables) replaced by placeholders (`<Table>`, `<column>`, `<caller>`, …).
  - **No evidence selected by the hidden oracle.** L+ ranks codes by C3 persistence of checker refusals, as before. Q+ now ranks pycheck codes by how often they fire on the final round of *every* round-4 arm-P run, whatever the oracle said. The build reads only the saved artefacts, never the results JSONL that holds the verdicts. The pycheck policy is arm Q's (C2).
  - **Budget.** The equal character budget and the longest-prefix rule are unchanged.
  - **Tests** (`tests/bench/test_edit_slice_aids_neutral.py`): no row contains a route path, function name, table or column name, a string the references add, an oracle trap case name or a trap name. The scan checks both tables and every generic row, and it catches each pre-fix leak. Every row is its code's generic row. The Q+ build counts every final round from artefacts whose JSONL is not even JSON. The builders' source reads no `outcome`.
- **C2. Q's ownership facts come from L's start profile.** Q's policy used to take owners from today's importer inference, while L started from the committed import. That meant Q was told JustNotes' `Note`/`Tag`/`Keyword` and TaskFlow's `Task` were owned when L's profile declared them shared. Q's owners, `owner_via` and shared tables now come from arm L's start profile: `bench/template_profile.py` for the template, and `bench/imports/<repo>/profile.py` for a held-out repo. That file is read each time the policy is requested, never cached across a change. Link tables, not-owned answers and examples follow from those owners. pycheck's own derivation (`qcheck.derived_policy`) remains the fallback when there is no import. The statement names shared tables ("… rows declare no owner: they are shared …").
  - **Tests** (`tests/pycheck/test_pycheck_qprompt.py`, `test_pycheck_bench.py`): for every repo, Q's owners, `owner_via` and shared tables equal L's start profile. On the template, the template profile and pycheck's derivation agree. Rewriting the import changes Q's policy and statement, with no restart. pycheck's accuracy tests (trap recall, the round-4 ceiling) use `derived_policy`, so they don't depend on the import's state.
- **I1. Retrieval excludes a ticket's base chain both ways.** t4 starts from t3's reference, so t4's verified answer contains t3's solution. A ticket never retrieves a ticket in its base chain, and never a ticket whose base chain contains it (`aids.related`). t3 no longer retrieves t4, and t4 no longer retrieves t3.
- **I2. Harness errors are neither dropped nor skipped.**
  - `--resume` does not count `error` records as completed.
  - The CLI client scans stderr as well as stdout and the JSON `result`, with broader plan-limit patterns ("hit your limit", "usage limit", "limit reached" unless it is a rate limit's, "resets", "credit balance", …). A 429 or 529 rate limit is still transient and retried.
  - A run stops after 3 consecutive `CliError` records, with exit code 4 and a clear message.
  - The report has an `err%` column.
  - Tested with the fake `claude` and fake clients.
- **I3. The CLI never bills the API account.** Every `claude -p` call runs with a copy of the environment that drops `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` and every `CLAUDE_CODE_USE_*` provider switch (`cli_client.cli_env`). The fake `claude` echoes back which of those variables it sees.
- **I4. No race on `sys.modules`.** Building an emitted app drops its package's modules through an atomic snapshot (`thin._forget_package`, `list(sys.modules)`). It no longer iterates the live dict while other workers import. The concurrency test adds threads that mutate `sys.modules` during the builds.
- **Minor fixes.**
  - A refused CLI attempt's reported cost is added to the run's cost.
  - `round_findings` aligns with `round_problems`, with `None` for a round never built.
  - Diagnostics of the joined program show no `Exact fix`, because their line and column are the joined program's, not the model's file.
  - A missing CLI `stop_reason` is recorded as `unknown` and logged, not taken as `end_turn`.
  - `CLAUDE_CODE_MAX_OUTPUT_TOKENS` is set from `--max-tokens`, so both transports have the same cap.
- **Real-CLI canaries** (Claude Code 2.1.283, `claude-haiku-4-5`; `tests/llm/test_cli_client.py::TestLiveCanaries`, `-m live`):
  - **Auth without the key.** It authenticates through the logged-in CLI with `ANTHROPIC_API_KEY` removed, and with an invalid key set and then scrubbed.
  - **`stop_reason`.** The CLI's JSON carries `stop_reason` (`end_turn`).
  - **The output cap.** With `CLAUDE_CODE_MAX_OUTPUT_TOKENS=50` the CLI enforces the cap, but not as a `max_tokens` stop. It makes up to three continuation attempts (4 turns, 4×50 output tokens, all billed), then answers `is_error` ("exceeded the 50 output token maximum"), `stop_reason: stop_sequence`, exit 1, and the answer text is lost. The client maps this to `stop_reason="max_tokens"` with empty text: the harness's truncation, as on the API. It is not retried. A truncated CLI round therefore costs up to 4× the cap in output tokens. The API arm pays once.
  - **CLAUDE.md exclusion.** The model says no when asked whether its context mentions "CodeGraph", which the user-level `~/.claude/CLAUDE.md` names. Input was 467 tokens.
- **Regenerated tables.** Both tables have a 772-character budget.
  - **L+:** 1 row, 605 characters: L404, the generic row. The next row, L302, does not fit.
  - **Q+:** 2 rows, 772 characters: PY805 and PY806. Of 180 arm-P final rounds, 12 are flagged. PY801 no longer ranks: under the committed imports' ownership, t29's link-table reads are not a violation.
  - **Rebuild after the re-import.** The Q+ ranking depends on the committed imports through arm Q's policy (C2). The tables must be rebuilt (`build_aids`) after the round-5 re-import of `bench/imports/*` finishes. `test_the_committed_tables_are_what_the_script_builds` fails until they are.
- **Dry run after the fixes.** Fed through Q and Q+, P's references score correct on t1–t30 (60/60) with 0 pycheck findings. Fed through L and L+, the Lattice references score correct on t1–t12 (24/24).
- **Test fix.** `test_the_committed_import_emits_exactly_its_verified_routes` counts `@endpoint` functions only. The re-imported TaskFlow program has a `@step` procedure (`search_tasks`), which is not a route.
