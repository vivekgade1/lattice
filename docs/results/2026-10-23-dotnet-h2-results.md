# .NET import fixes and the blind held-out round H2: results, failures stated

Date: 2026-10-23. Branch `develop`. Pre-registration: `docs/results/2026-10-23-dotnet-h2-prereg.md` (freeze `docs/results/data/2026-10-23-dotnet-h2/freeze.sha256`,
428 entries, list sha256 `ae6cde4135e6b26ab8264343701aacc1c5a5cef2ad798955557e1743e834b289`; inputs manifest `h2-inputs.sha256`, 19 entries, list sha256
`c9486df64a7f8ccc67f22a66c18a4d74cf930590b672952ebc100752bbb85010`). Scored data `docs/results/data/2026-10-23-dotnet-h2-scored/`, verify data
`docs/results/data/2026-10-23-dotnet-h2-verify/`, ledger `docs/results/data/2026-10-23-dotnet-h2/ledger.jsonl`. Post hoc localisation:
`docs/results/2026-10-23-dotnet-h2-posthoc.md` (post hoc, moves no scored value). The Python and Go paths were not changed in behaviour.

## 1. The short version
- **The fixes did not generalise.** After the six fixes of section 2 (made on the development data D1 and H1), the one scored run on a fresh blind app H2
  (`apiary`, beekeeping, 13 routes, 5 tables, written by a sonnet author who saw only a product brief) passed **5 of 13 gated rows** (prediction in the
  prereg: 8-10). Of those 5, only 2 carry information (ROUTES, and UNREAD at route level); BOUNDS and PI7 pass vacuously and JUDGE is a proxy (section 3).
- **The held-out mutant arm produced no verdicts.** Its control import crashed (`sqlite3.OperationalError: table sessions has no column named UserId`),
  ledger status `crash_after_metric`; the rerun rule allows a rerun only after `crash_before_metric`, so the 16 mutants were not scored and the registered gate
  (13 of 16, class gates 3/2/2/2) is **not met**.
- **Cause (post hoc):** two independent library gaps explain 6 of the 8 gated failures: the extractor does not read column names that an app assigns in one
  loop over the whole model, and it does not follow a custom `RequireSignIn()` endpoint-filter extension as authorization (section 4).
- **Independent verify (probes arm):** 0 of 20 verdicts differ. Mutants arm: verify not eligible.
- **Equal support with Python and Go is still not reached** (section 6). No cell of the .NET column rises.

## 2. What was built before the scored run (nodes; development data D1, H1)
All of this was developed against D1 and H1 (used up as blind sets). Every number here is a development number.
| node | change | commit | measured on dev data |
|---|---|---|---|
| F1 | rules import: helper-checked login found, a fresh login per probe session when tokens do not survive a restart (`relogin`), auth-handler 401/403 mapped, admin role seeded from the role column, `[RegularExpression]` values | 79db520 | H1 verified share 0.039 -> 0.83 on F1's tree; D1 0.255 -> 0.336 |
| F2 | refusals through typed exceptions whose status the constructor chain carries and a handler reads | 628d3e4 | H1 refusal recall 0.405 -> 0.857 at precision 1.00 |
| F3 | stable evidence: minted values become placeholders at the point evidence is written | 5c50742 | H1 and D1: 10 of 10 identical imports, only `rules.timing` differs |
| F4 | `private` derivation: a lambda over a typed `IQueryable<T>` resolves to its entity | 750bf7b | H1 derived private equals the truth exactly; D1 unchanged |
| F5 | owner confirmed by `owner_uses` enters the candidate spec; DN801/DN802 generic fixes; layer R `relogin` in `check.py`; `crash_admits` (an anonymous 5xx on an ASP.NET route is not a refusal) | 7c9fdd2, d39421c, c279a06, af37014, d1f5ead | H1 mutants: 13 of 16 killed, all class gates pass, **zero slack**; HM12-14 survive |
| F6 | the auth handler's forbidden detail named as the role check's answer (an integration regression of F2) | 9ff5a5d | H1 import 85 of 113 verified (0.75), 0 refuted |
| N1 | `test_outside` order dependence fixed (template app created the job-scheduler tables from the global SQLModel metadata); Go probe re-pinned for the declared Q10 edit | 75da3f2 | known failures 2 -> 0 |
Integrated tip (G1, `np-dn2` 5984086): full regression **10600 passed, 0 failed, 45 skipped** (1 h 14 m); H1 mutant dev re-run still 13 of 16.
The F5 dev result is a development number with zero slack; the prereg said so before the freeze.

## 3. H2 scored run (probes arm; `dogfood/dotnet_h2_probes.py --kind scored`, ledger run 2f1badee6114)
| row | gate | observed | verdict | note |
|---|---|---|---|---|
| H2-ROUTES | recall >= 0.90, 0 extra | 13 of 13, 0 extra | pass | the extract holds 24 route records for 13 routes (duplicates) |
| H2-AUTH | >= 0.85 | exact 2 of 13; 11 Member/Admin routes read as Public | **fail** | |
| H2-TABLES | tables and facts >= 0.90 | tables 5 of 5; column facts 0 of 168 | **fail** | |
| H2-WIRE | >= 0.95 | 0 of 20 (every wire name `None`) | **fail** | |
| H2-UNREAD | 0 silent drops | 0 | pass | true but narrow (implied by ROUTES) |
| H2-BOUNDS | precision 1.00 | 0 claims (recall 0 of 34) | pass | **vacuous**: an empty claim set |
| H2-RAISE | recall >= 0.60 and precision 1.00 | recall 0.88, precision 0.68 (7 false 400s on GET/DELETE routes) | **fail** | |
| H2-PI3 | verified share >= 0.50 | the import crashed | **fail** | |
| H2-PI7 | 10 of 10 identical | 10 of 10 | pass | **vacuous**: the ten imports crashed identically and the row compared the error text |
| H2-PA1 | 0 wrong | private named `Id`, `Name`, `Password`, `UserId`, `OwnerId` (property names, not columns) | **fail** | |
| H2-IMPL | 0 missing or extra | the import crashed | **fail** | |
| H2-WF | >= 1 multi-step flow verified, 0 failing on replay | 0 verified | **fail** | |
| H2-JUDGE | >= 0.75 | 6 of 6 | pass | **hollow**: a proxy that does not run the owner check |
Gated: **5 passed, 8 failed, 0 not run.** Reported rows: DET passes (10 of 10 extract and layer A identical); OWN, PI4, PI5, PR3, PA0, X fail with the import crash.
Predictions (prereg): 8-10 of 13; P(PI3 and mutants both pass) about 0.12. The result is below the prediction range.

## 4. Mutants arm (HM1-16, `dogfood/dotnet_h2_mutants.py --kind scored`, ledger run 0fce5704643a)
The control import of H2 raised the same `OperationalError` as the probes rows; no mutant was judged. Status `crash_after_metric`. Under prereg 10.2 a rerun
needs `crash_before_metric`, and anything that raises is a FAIL (10.3): the arm is **FAIL, 0 of 16 judged, gate 13 not met**. W3 delivered 16 patches and a
`MUTANTS.md` (HM12-13 substitutions and the HM5 route choice are disclosed there); they were never run against Lattice.

## 5. Independent verification (node V, opus, `--kind verify`)
Probes arm re-run from the archived inputs in a fresh worktree: **0 of 20 verdicts differ** (13 gated, 7 reported); the observed values differ only in run id, timings, absolute
path prefixes, and in 8 rows the 500-character error tail (the worktree path is 3 characters longer). Mutants arm: verify refused by the ledger rule (no completed scored run),
not launched. Ledger: 6 lines as expected, no `refused.jsonl`.

## 6. Post hoc causes (full detail in the post hoc document; labelled post hoc, p = the diagnosing node's confidence)
1. **C-COL** the app names columns in one loop (`ApiaryDb.cs:56-57`, `SetColumnName(Snake(prop.Name))`); `Ef.cs:473` reads only per-column `HasColumnName`. Explains TABLES, WIRE, the import crash and so PI3, IMPL, OWN, PI4, PI5, PR3, PA0, X, the whole mutants arm and the vacuous PI7. A scratch variant that only renamed columns gave TABLES 162/168 and WIRE 19/20 (counterfactual).
2. **C-AUTH** `RequireSignIn()` is a project extension adding an endpoint filter (`Caller.cs:24-45`); `Routes.cs:277-298` does not walk it, `Auth.cs:186-201` does not count a filter as authorization, `Auth.cs:97-118` does not find where the caller is stored. Explains AUTH, WF, empty derived owners (so the HM1-4 mutants would stay unkilled) and the duplicate route records. Even with C-COL fixed, PI3 stayed at 0.22 on a scratch variant (11 of 12 refutations follow from C-AUTH).
3. **C-MW** a body-parsing middleware's 400 is attached to every route (`Auth.cs:367-381`): all 7 RAISE false positives. (Fixing C-AUTH raises recall and would put precision below the gate: the H2 truth lists the 401 on one route only; this is a truth gap, not a library fault.)
4. **C-PROJ** `derive.py:111-119` names every never-serialised column private (the failure H1 had); **C-BND** a hand-written `Check` validator is not read (`Binding.cs:317-358`); **C-FK** a `HasOne<T>().WithMany().HasForeignKey` principal/dependent swap (`Ef.cs:282`, about 85% sure); **C-OVL** ambiguous `View` overloads.
Every H2 idiom is ordinary ASP.NET Core / EF Core code that H1 did not use. This is the generalisation gap the handoff warned about, now measured on a fourth app.

## 7. Process irregularities (all disclosed; none moved a number)
1. **Transcript audit false positives (twice).** `audit_transcript.py` returned `void` for A3 (4 outside items, all the C# comment marker `//` inside heredocs) and for W3 (1 outside item: `../../../mutants/m$n.diff` from `scratch/build/mNN`, which resolves inside W3's directory). Prereg 9.5 voids a deliverable only after the dispatching node confirms the finding by reading the call; both were read and not confirmed (`author/a3-audit-review.md`, `w3/w3-audit-review.md`). The script exited 5 before copying W3's files; the copy to `h2-mutants-run/` was done by hand with the script's own `cp -p` and `cmp`. The auditor's heuristic path extraction is a defect to fix.
2. **Freeze refusal in the main checkout.** The must-list rule (every file under `lattice/`) refused to start because the main checkout holds an ignored `lattice/studio/graph/node_modules/` (1313 files) that the clean worktree where the freeze was written does not. The refusal wrote to no ledger. The scored and verify runs were made from a clean worktree of the exact committed tree (`/Users/vivekgade/Documents/lattice-h2-run`, branch `np-h2-run`). No frozen file was edited.
3. **A usage error before the scored run.** My first launch omitted `--out`; both runners exited 1 (`--out is required`) before touching the ledger (0 bytes); the scored run was then launched once per arm.
4. **Runner weakness found afterwards.** H2-PI7 compares the importer's error text when the import fails, so ten identical crashes read as a pass. Not corrected (frozen); prereg rule 10.3 would class a raising row as FAIL.
5. **Not a departure:** the A3 and W3 spends and the CLI pre-flight (section 8).

## 8. Real-agent rows (node R1, `docs/results/2026-10-23-dotnet-real-agent-results.md`)
Toy ASP.NET Core + EF Core (SQLite) Shelf service built from its Lattice spec by a real agent (`claude` CLI, sonnet) through the rebuild loop: **B-BUILD pass** (`dotnet build` exit 0), **B-CHECK pass** (0 findings, 3 runs), loop finished in round 1 of 3, 9 turns, USD 0.1248. Real-model C-ASK on D1: **A-ANSWERED 3 of 3**, **A-CITED fail by the registered definition** (2 of 3 route references counted as uncited; the model cited them through inventory elements the library accepts; post hoc count 0 uncited), registered Q1 `not_implemented` contradicts D1's truth (reported). n = 1 each; the chat path ran in process.
**Spend (all `claude` CLI, sonnet, no API key):** R1 USD 0.4568; CLI flag pre-flight USD 0.0100; A3 USD 0.5955; W3 USD 0.2296; **total USD 1.2919** of the USD 5 approved.

## 9. J0-dn: the parity matrix, .NET column, after this round
No cell changes: **S 9, P 9, A 2** (Python 15/3/2, Go 15/4/1). Row 19 (build-from-spec) now has one real-agent build on a toy app (pass, n = 1) but not on an unfamiliar real app, so it stays P. Row 13 (chat) now has a real-model measurement whose citation gate failed by definition, so it stays P. Rows 3, 4, 10 (the imported spec and its checks on an unfamiliar app) are worse supported than the H1 round suggested: the blind H2 import did not run to completion. Rows 2 and 20 stay absent by decision. **Equal support is not reached.**

## 10. Honest assessment
The development-data numbers (import 0.83 on H1, recall 0.857, mutants 13 of 16) were real but fitted to two apps; the blind app shows the extractor still depends on idioms (per-property `HasColumnName`, `AuthenticationHandler`, DataAnnotations) that H1 happened to use. What held: routes (13 of 13), layer A determinism (10 of 10), the byte-compat of the Python and Go suites (10600 passed). Next (see HANDOFF-2026-10-23): fix C-COL and C-AUTH first (they explain most rows), then a new blind app H3 with the budget left (about USD 3.70 of the 5).
