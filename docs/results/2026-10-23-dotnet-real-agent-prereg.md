# .NET real-agent rows: pre-registration (build-from-spec of a toy ASP.NET Core + EF Core app, and real-model C-ASK)

**Status.** Written and committed before any model call. Node R1, branch `worktree-agent-aeb116c3459db6c3f` (cut from `develop` ad23cc4).
The driver (`dogfood/dn_real_agent_run.py`) refuses to call the model unless this file and every file it lists are committed, and refuses a second run of either row.

## 1. What this closes

`docs/results/2026-10-21-dotnet-parity-results.md` section 6 lists, as NOT RUN: "a real-agent toy build (claude CLI, USD 5 cap); real-agent model answers for C-ASK".
They are the evidence the parity matrix lacks for row 19 (build-from-spec, `P`: "C-REBUILD with a stub agent and a `dotnet build` gate; no real-agent build") and row 13
(chat grounded in spec and code, `P`: "C-ASK measured with a scripted model only"). The owner approved a real-agent build of a toy .NET spec with a USD 5 cap (prereg
`2026-10-18-dotnet-prereg.md` Q12, Amendment 1).

## 2. Two facts about the existing fixtures (stated before the run)

1. **There is no toy .NET spec in the repository.** `tool_rebuild` (C-REBUILD) runs the Studio rebuild loop on a scratch copy of **D1** (the Streak .NET app, thousands of lines of C#)
   with a scripted agent that writes `built.txt`; its gate is `dotnet build` plus a file test. The toy fixture for this run is therefore new:
   `dogfood/dn_real_agent/fixture/` is the .NET twin of the Arm L toy (`tests/grpc/fixtures/toybuild5`, the gRPC Shelf): a **Shelf** HTTP service, ASP.NET Core minimal APIs and
   EF Core (`Microsoft.EntityFrameworkCore.Sqlite` 10.0.13, in the local NuGet cache; no network in the gate) over a SQLite file, 8 requirements (`REQUIREMENTS.md`),
   7 scenarios and one invariant (`spec.py`, layer D), a process harness (`harness.py`), a gate (`check.sh`: `dotnet build` then `lattice.spec check ... --layers D`), a scaffold
   `Program.cs` that only answers `/health`. The agent sees `REQUIREMENTS.md`, `spec.py`, `CONTEXT.md`, `harness.py`, `check.sh`; it never sees `reference/Program.cs`.
2. **C-ASK's registered "sync is not_implemented" contradicts D1's truth.** Prereg `2026-10-18-dotnet-prereg.md` section "Prediction errors recorded" item 7: D1 implements `GET /v1/sync`.
   The registered texts stay as written; this run reports the kinds against both (the registered kinds, and D1's truth) and gates neither on a kind (section 4).

## 3. Calibration (no model, done before the run; data `docs/results/data/2026-10-23-dotnet-real-agent/calibration.json`)

The reference solution (`dogfood/dn_real_agent/reference/Program.cs`, EF Core) passes the gate; the scaffold fails; three mutants of the reference fail (list order reversed; a negative
quantity allowed; duplicate answers 400 instead of 409). The loop and the driver were exercised end to end with the scripted stub agent (`--self-test`, no model call).

## 4. Rows and gates

### (a) Real-agent toy build (one run, the Studio rebuild loop `Rebuilder`, agent = the `claude` CLI, model `sonnet`, at most 3 rounds)

| id | what | gate (pass) | status |
|---|---|---|---|
| **B-BUILD** | `dotnet build dnbuild.csproj -c Release` on a clean `git archive` of the loop's tip | exit 0 | **gated** |
| **B-CHECK** | the Lattice check (`check.sh`, layer D of `spec.py`) on that clean archive, three times | each run: exit 0, verdict `pass`, **0 findings** (0 errors) | **gated** |
| B-LOOP | the loop's own record | status `passed` within 3 rounds | reported |
| B-PROTECT | spec, requirements, trace, harness, check script, csproj, `studio.toml`, `CONTEXT.md` at base vs tip | byte-identical | reported (a change is a finding) |
| B-EF | the tip's C# uses EF Core | `DbContext` and `UseSqlite` appear | reported |
| B-COST | the ledger | total <= USD 5.00 | gated (a node failure if not) |

### (b) Real-model C-ASK (one run)

The three registered questions (`ASK_QUESTIONS` of `dogfood/dotnet_probes.py`, written before any code in prereg 5.6): "is there a sync endpoint?", "can I check in a habit?",
"who can dismiss a nudge?", asked of **D1** (`docs/results/data/2026-10-18-dotnet/d1.tgz`) with **Streak's spec at a0679e9** (`git archive`, read only) and D1's **static** inventory
(`lattice.importer inventory`; no observed workflows: Docker is not used in this run, so the labels are `*_unverified`, the families are unchanged).

The path is the chat path of `Server.ask` (`lattice/studio/server.py`), in process: `lattice.spec.ask.ask(...)` with the digest that has the `impl:` section and the generator
`ClaudeCliClient` (`--backend cli` transport: `claude -p`, no tools, no MCP, empty cwd, model `sonnet`, effort `high`, one transient retry), whose citation check and its single repair round
are the library's; then `claim_check.check_answer` (`lattice.chatcheck/1`). The model-free map router (`chat_route.route`) is **not** applied (it would answer without the model);
whether it would have routed each question is recorded.

| id | what | gate (pass) | status |
|---|---|---|---|
| **A-CITED** | every claim of the three answers cites spec or code | **0 uncited references**, 0 rejected citations, every `code:<path>:<line>` citation resolves (file and line exist, the quote is on the line: `check_citation_lines`), and every one of the 3 answers has at least one accepted citation (an answer the library downgraded to `not_specified` for lack of grounding has none, so it fails; an uncited or unanswered question cannot pass vacuously) | **gated** |
| A-ANSWERED | the model answered | 3 of 3 | reported |
| A-KINDS-REGISTERED | implementation family per question | `not_implemented`, `implemented`, `implemented` | reported (Q1 is expected to differ, section 2) |
| A-KINDS-TRUTH | the same against D1's truth | `implemented` x3 (D1 has `GET /v1/sync`) | reported |
| A-AUTH | the dismiss answer names the auth | one of Member / Admin / auth / login in the answer | reported |
| A-COST | the ledger | total <= USD 5.00 | gated |

**Definitions (fixed now).** A *reference* in an answer's `answer` or `explanation` text (after the claim checker) is (i) a `code:<path>:<line>` token, (ii) a rule id `R<n>`, or (iii) `METHOD /path` of a
route in D1's extract. It is *cited* when an **accepted** citation of the answer backs it: the same `code:` target; a target equal to or ending in the rule id; for a route, a `code:` citation of the route's
handler file and line, or whose quote holds the route path. A route string that is not a route of the code is a negative or unknown mention, counted apart and not as uncited. **The count of uncited references is
reported whatever the verdict.**

## 5. Procedure and caps

- **No API key.** The driver deletes every `ANTHROPIC_*` variable from its own environment, the shim (`dogfood/dn_real_agent_shim.py`) strips them (and `CLAUDE_CODE_USE_*`) from every child, and the launch is
  `env -u ANTHROPIC_API_KEY`. Only the `claude` CLI is used; the cost is the CLI's own `total_cost_usd` (a list-price estimate).
- **Spend.** Total cap for (a)+(b): **USD 5.00**. The shim passes `--max-budget-usd min(remaining total, remaining of the phase, per-call ceiling)` to every `claude -p`: the build phase may spend at most USD 3.00
  (per agent step at most 1.50; `--max-turns 40`), the ask phase the rest of the total (per call at most 1.00). USD 0.10 is kept back from the total (a call can overshoot its flag slightly), and a call is refused (no model call) when under USD 0.05 remains. The ledger
  `docs/results/data/2026-10-23-dotnet-real-agent/ledger.jsonl` is append-only, one line per invocation (cost, turns, exit code, session id, cap given, never the prompt). At the cap the run stops and the partial result
  is reported. A usage limit or the cap is **NOT RUN** for what it prevented, never a miss.
- **Once.** Each of (a) and (b) is run once, in that order. A failed gate is a result. Nothing in the fixture, the prompt, the driver, the questions or the definitions changes after the first call; a miss is reported, not tuned.
  A crash before the first model call (not the model's failure) may be re-launched; the driver's `*-freeze.json` records the head and the file hashes of every launch.
- **Model.** `sonnet` (alias; the served model id is read from the CLI's `modelUsage` and archived).
- **Raw outputs** archived under `docs/results/data/2026-10-23-dotnet-real-agent/`: the ledger, the raw stdout of every CLI call, the loop record and log, the tip archive, the freeze files, `build-result.json`, `ask-result.json`.
- **Not in scope.** `lattice/` library code is not edited; observed workflows for the inventory; the Studio HTTP server; the MCP path.

## 6. Predictions (written before the run; recorded so the errors are visible)

- B-BUILD passes (p 0.85); B-CHECK passes (p 0.75: the fixture is small, the gate prints the failing scenarios, the loop has 3 rounds); the loop passes in round 1 (p 0.5), within 3 rounds (p 0.8); B-EF passes (p 0.9);
  build spend USD 0.30-1.50.
- A-ANSWERED 3 of 3 (p 0.9). A-CITED passes (p 0.5): the main risks are a route or rule id named in the text without its own citation, and a negative answer (Q1) that cites nothing and is downgraded to `not_specified`.
  A-KINDS-REGISTERED: Q1 `implemented` (the model reads `GET /v1/sync` in the impl section), so the registered kinds fail on Q1 (p 0.7); A-KINDS-TRUTH passes (p 0.65). Ask spend USD 0.20-0.90.
