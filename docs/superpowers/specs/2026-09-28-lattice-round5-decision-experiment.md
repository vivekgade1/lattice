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
