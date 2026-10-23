# .NET real-agent rows: results (real-agent toy build, real-model C-ASK)

**Status line.** 2026-10-23, node R1, branch `worktree-agent-aeb116c3459db6c3f` (cut from `develop` ad23cc4; nothing pushed). Pre-registration:
`docs/results/2026-10-23-dotnet-real-agent-prereg.md` (commit dad40c6, before any model call). Raw data: `docs/results/data/2026-10-23-dotnet-real-agent/`.
Model: `claude-sonnet-5-5` (alias `sonnet`), through the `claude` CLI only (no API key; `ANTHROPIC_*` stripped by the driver and the shim).

**(a) Real-agent toy build: B-BUILD PASS, B-CHECK PASS** (first round, 9 turns, USD 0.1248).
**(b) Real-model C-ASK: A-CITED FAIL by the registered definition** (2 uncited references of 3 checked; 0 rejected citations, 0 unresolved code citations; the cause is a gap in the
registered definition, post-hoc count 0, section 4). **Spend: USD 0.4568 of the 5.00 cap** (build 0.1248, ask 0.3320; 6 CLI invocations, 6 ledger lines).

## 1. Verdicts per gate

| row | gate | verdict | observed |
|---|---|---|---|
| **B-BUILD** | `dotnet build` exit 0 on a clean `git archive` of the loop's tip | **PASS** | exit 0, 0 warnings, 0 errors |
| **B-CHECK** | Lattice check (layer D of `spec.py`), 3 runs on that archive: exit 0, verdict `pass`, 0 findings | **PASS** | 3 of 3 runs `pass`, counts `{D: 0}` |
| B-LOOP | loop `passed` within 3 rounds | PASS | round 1 of 3 |
| B-PROTECT | spec, requirements, trace, harness, check script, csproj, `studio.toml`, `CONTEXT.md` unchanged base to tip | PASS | 8 of 8 byte-identical |
| B-EF | tip C# uses EF Core | PASS | `DbContext`, `UseSqlite`, `using Microsoft.EntityFrameworkCore` |
| B-COST / A-COST | ledger total <= USD 5.00 | PASS | **0.4568** (see irregularity 2: the build result file prints 0.0, the ledger holds the real figure) |
| A-ANSWERED | 3 of 3 questions answered by the model | PASS | 3 of 3 |
| **A-CITED** | 0 uncited references, 0 rejected, all code citations resolve, every answer has an accepted citation | **FAIL (registered definition)** | 2 uncited of 3 references; 0 rejected; 0 unresolved; 3 of 3 answers carry accepted citations (5, 8, 6 citations) |
| A-KINDS-REGISTERED | `not_implemented`, `implemented`, `implemented` | FAIL (reported) | `implemented`, `implemented`, `implemented` |
| A-KINDS-TRUTH | D1's truth (`GET /v1/sync` exists) | PASS (reported) | 3 of 3 |
| A-AUTH | the dismiss answer names the auth | PASS (reported) | "signed-in user ... own nudges ... 401 / 404"; the impl citation says `auth Member` |

Predictions of the prereg against the outcome: B-BUILD/B-CHECK/B-LOOP/B-EF passed as predicted; build spend was below the predicted range (0.125 vs 0.30-1.50). A-ANSWERED as predicted;
**A-CITED was predicted to pass at p 0.5 and failed**; A-KINDS-REGISTERED failed on Q1 as predicted (p 0.7); A-KINDS-TRUTH passed as predicted; ask spend 0.33 inside 0.20-0.90.

## 2. (a) The build

- **Fixture.** New (no toy .NET spec existed; C-REBUILD used a scratch copy of D1 with a stub agent): `dogfood/dn_real_agent/fixture/`, the .NET twin of Arm L's gRPC Shelf. 8 requirements, 7 scenarios and one
  invariant (layer D), ASP.NET Core minimal APIs + `Microsoft.EntityFrameworkCore.Sqlite` 10.0.13, scaffold `Program.cs` answering `/health` only. Calibration (no model): the reference passes; the scaffold and 3 mutants
  (list order, negative quantity allowed, duplicate 400 instead of 409) fail (`calibration.json`).
- **The run.** The Studio `Rebuilder` with the `claude` CLI as agent: baseline 6 findings in scope, **round 1 passed (0 findings)**, merged as a commit on `rebuild/all-1`; 52 s wall, one agent step: 9 turns, 4,329 output tokens,
  USD 0.1248 (list estimate). The agent read `CONTEXT.md` and the scaffold, wrote a 103-line `Program.cs` (a `ShelfDb : DbContext`, entity `Item` mapped to `items`, four routes plus `/health`, 422/409/404 as specified), and ran
  `./check.sh` itself until it exited 0. It did not touch the protected files and did not read the reference solution (0 mentions of the reference path in its stream).
- **Independent of the loop's verdict.** B-BUILD and B-CHECK were run by the driver on a clean `git archive` of the tip (no `bin/`, `obj/`, `metrics/`), not read from the loop record.

## 3. (b) The C-ASK answers

D1 (the archived Streak .NET app) + Streak's spec at a0679e9 + D1's static inventory; `lattice.spec.ask.ask` with `ClaudeCliClient` (no tools, no MCP, empty cwd), then `claim_check.check_answer`
(it changed nothing: no `check` key on any answer). The model-free router would not have routed any of the three questions.

| question | kind / label | answer (abridged) | citations (accepted / rejected) | rounds, USD |
|---|---|---|---|---|
| is there a sync endpoint? | yes / `implemented_unverified` | "Yes. GET /v1/sync exists ... requires a signed-in user." | 5 / 0: `rule:R89`, two access cells, `scenario:sync_changes_in_order` step 3, `impl:route:GET /v1/sync` (quote holds `code:Api/Endpoints/FeedEndpoints.cs:16`) | 2, 0.200 |
| can I check in a habit? | yes / `implemented_unverified` | "Yes ... POST /v1/habits/{habit_id}/completions ... 201 ... 409 habit_archived / already_checked_in" | 8 / 0: cells, `rule:R72`, scenarios, `impl:route:POST /v1/habits/{}/completions` | 2, 0.090 |
| who can dismiss a nudge? | value / `implemented_unverified` | "Only a signed-in user, and only for their own nudges ... 401 / 404" | 6 / 0: cells, `rule:R28`, `rule:R27`, `impl:route:POST /v1/nudges/{}/dismiss` ("auth Member") | 1, 0.042 |

Q1 differs from the registered kind because the registered text is wrong about D1 (prereg 2026-10-18 section "Prediction errors recorded", item 7: D1 implements `GET /v1/sync`); the model's `implemented` matches the truth and the code citation.

## 4. The A-CITED miss, and a post-hoc reading (labelled as such)

The registered audit (prereg section 4) counted a route mention as cited only by a `code:` citation of the route's handler file and line (or a `code:` quote holding the route path). The model cited every route
through the inventory element `impl:route:<METHOD path>`, which the library accepted; none of its 19 citations is a bare `code:` target, so "uncited references" = 2 (`GET /v1/sync`, `POST /v1/habits/{habit_id}/completions`)
and the registered gate is **FAIL**. The verdict stands; the count of uncited references, **2 of 3**, is the reported number.

Post-hoc (`posthoc_ask.py`, `ask-posthoc.json`; written after seeing the result, not a gate): if an `impl:route:` element citation counts as a citation of its route, uncited = **0**, and the one `code:<file>:<line>` token inside
a citation quote (`Api/Endpoints/FeedEndpoints.cs:16`) resolves (file and line exist). So the model did cite every claim with spec or code evidence the library accepts, 0 citations were rejected, and the miss is the
registered definition not anticipating the element form of a code citation. This is a measurement-definition gap, not evidence of an unsupported claim; it is not corrected into a pass.

## 5. Process irregularities (all disclosed; none changes a verdict)

1. **Launch 1 of the build stopped before any model call.** The shim was committed without the executable bit; `rb.available()` answered "claude binary not found" (`build-result-launch1-not-run.json`, `build-freeze-launch1-refused.json`).
   Fixed (chmod, commit 6e809a0) and re-launched; the prereg allows a re-launch after a crash before the first model call. Launch 2 is the run reported.
2. **Ledger path bug in the build run.** `--out` was given relative; the shim runs with the agent's cwd (its worktree), so its ledger line and the raw CLI output of the build call were written inside the agent's worktree and
   committed into the tip, not into `OUT/`. The driver therefore printed `build_usd: 0.0` / `total_usd: 0.0` in `build-result.json` (so its B-COST `true` was vacuous). I copied `ledger.jsonl` and `build-raw/call-001.*` verbatim from the worktree
   into the data directory (the line is unchanged: `n 1`, phase build, cost 0.1248387), fixed the driver to resolve `--out` to an absolute path (commit 6a0f9d3), and ran (b) after the fix, so the ask phase's cap used the real ledger. The
   tip archive (`build-tip.tar`) carries those stray ledger and raw files; the gates ran on a clean archive of that tip and are unaffected. The true total is the ledger's: 0.4568.
3. **The driver changed between (a) and (b)** (the absolute-path line only); both launches' file hashes are in `build-freeze.json` and `ask-freeze.json`.
4. **Inventory without observed workflows** (no Docker in this run): labels are `*_unverified`; families are unchanged. The chat path is the in-process sequence of `Server.ask` (ask, then claim check), not the HTTP server; the model-free
   router was recorded, not applied.

## 6. What this supports, and its limits

- Row 19 (build-from-spec): a real model, through the repo's own rebuild loop and `--backend cli` transport, built a small ASP.NET Core + EF Core service from its spec and passed `dotnet build` and the Lattice check on a clean
  archive in one round for USD 0.12. Limits: one run, one toy of 8 requirements (not D1), layer D only; the model needed no repair round, so the loop's repair behaviour on .NET findings is untested here.
- Row 13 (chat grounded in spec and code): a real model answered the three registered questions with only accepted citations (0 rejected) and kinds equal to D1's truth. Limits: one run, three questions, static inventory,
  in-process path; under the registered definition the citation gate fails (section 4). This node does not re-grade the parity matrix.
