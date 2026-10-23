# .NET rules import, held-out round H2 (node H2a): pre-registration

## 1. Status, scope and labels

2026-10-23. **Written before the H2 runners, the H2 brief and the freeze file; before any H2 app exists.** No model was called and
no API key was used to write it. Branch: a worktree of `develop` at `4062435` (the merge of `np-dn2`: the .NET import fixes F1-F6).
This file is committed alone, first. Then, in this order, each in its own commit with explicit `git add` paths: the H2 runners
(`dogfood/dotnet_h2_probes.py`, `dogfood/dotnet_h2_mutants.py`), the brief and the dispatch scripts (`docs/results/data/2026-10-23-dotnet-h2/`),
and last the freeze file and the empty ledger. Nothing is pushed or merged by this node, and this node dispatches no author.

Purpose: the rules-import fixes F1-F6 were developed on dev data D1 (Streak in ASP.NET Core) and H1 (`outlay`, the first held-out app,
which is now used up and is dev data). They are scored **once** on a fresh blind app H2 that nobody developed against.

Read while writing (read only): `docs/HANDOFF-2026-10-21.md`, `docs/results/2026-10-21-dotnet-parity-results.md`,
`docs/results/2026-10-18-dotnet-prereg.md` (the H1 prereg, cited as "H1 prereg" with its section numbers, Amendments 1-6 included),
`dogfood/dotnet_probes.py` and `dogfood/dotnet_mutants.py` (the frozen H1 runners, sha256 `da6316ce...` and `410ff028...`, identical to
their entries in `freeze-h1-hm.sha256`), `dogfood/dotnet_hm_dev.py` (node F5's dev driver), the commit messages of F1-F6, and the files
in `~/Documents/dn-h1-author` and `~/Documents/dn-h1-w` (read only).

Labels: **(m)** measured by an earlier node and restated (source named); **(r)** read from code, data or git while writing this;
everything else is a **prediction (p)**.

## 2. Disclosure: what was fitted to H1 and D1, and is now frozen library state

Every fix below was developed while looking at D1 and at H1 **after** H1's scored run, so H1 and D1 are development data for all of
them. The values measured on them are dev values and support no generalisation claim; only H2 can.

| fix | commit (r) | what it does | dev value on H1/D1 (m, from the commit message or the node's report) |
|---|---|---|---|
| F1 | `79db520` | login-helper detection (a project method taking the stored password column is the login's verify call); `run_probes(relogin=True)` for aspnetcore (log in again in each probe session when a token minted in one is refused in the next); an aspnetcore auth handler's 401/403 read as unauthenticated/forbidden; the role column (`dotnet.auth.role_column`) seeds an admin; `[RegularExpression]` patterns give regex example values | H1 rules import 3/77 -> 64/77 verified; D1 0.255 -> 0.336 |
| F2 | `628d3e4` | a typed project exception carries the constant status its constructor chain hands to the base constructor, when an exception handler reads that status | H1 refusal recall 0.405 -> 0.857 at precision 1.00; D1 and fixtures unchanged |
| F3 | `5c50742` | stable evidence: `lattice/importer/stable.py` replaces minted values (trace ids, timestamps, bearer and refresh tokens, hashes, salts, GUIDs) with placeholders where the import writes its evidence (aspnetcore only) | H1 and D1 10/10 identical without any dogfood normaliser except `rules.timing` |
| F4 | `750bf7b` | `private` derived by projection only where every read of the entity can be seen (a projection over a typed `IQueryable<T>` parameter resolves its lambda) | H1 derives exactly its truth's 4 private columns (H-PA1); D1 unchanged |
| F5 | `7c9fdd2`, `c279a06`, `d1f5ead`, `d39421c`, `af37014` | `owner_uses` -> the profile's owner when a table has several users keys and the routes compare one (owner confirmed on the model copy); `json_dash` private columns; DN801 generic fixes (a row handed to a DbSet mutator, a query handed to a project method, a write by key covered by an earlier scoped load of the same key, an owner set from the request) and DN802 generic fixes (`StatusCode(code, value)`, an answer that a project method returns, rows typed by the entity they hold); layer R `crash_admits` for aspnetcore (an anonymous 5xx is no refusal); `check.py` layer R logs in again per probe for a dotnet runtime; the dev driver `dotnet_hm_dev.py` | unmutated H1 and D1: 0 layer A findings under every policy; **HM1-16 on H1: 13 of 16 killed, with zero slack against the gate of 13** (F5's dev run, as reported by the orchestrator) |
| F6 | `9ff5a5d` | the ASP.NET auth handler's forbidden detail becomes the role scheme's `forbidden_detail` | H1 69 -> 73 of 100 items verified, refuted 0 (verified share 0.73; the orchestrator's summary gives 0.75) |

Values fixed by these fixes and now part of the frozen library: the login-helper rule, `relogin` per probe session, the 401/403 mapping
of auth-handler answers, role-column seeding, regex example values, the typed-exception status rule, the placeholder catalogue of
`stable.py`, the projection-privacy rule, the owner confirmation and `json_dash` rules, the DN801/DN802 coverage rules, `crash_admits`,
and the forbidden-detail role scheme.

**What this means for H2.** F5's dev result was 13 of 16 with zero slack, and every dev number above was measured on the apps the fixes
were written against. **A miss on H2 is expected and is a result**: it localises an idiom the import does not read. It is reported,
never tuned (section 10).

**The library under test (r: `git diff --name-status c75cb48 4062435`), 47 files, every one in the freeze file (section 8):**
added `lattice/dncheck/__init__.py`, `derive.py`, `policy.py`, `lattice/dncheck/src/{Assemble, Auth, Binding, CheckBounds, CheckDatetime,
CheckErrors, CheckMachine, CheckOwner, CheckOwnerCover, CheckPartial, CheckPrivate, Closure, Ef, Json, Load, Model, Owner, Program,
Projection, Raises, Required, Routes, State, Workflow}.cs`, `DnCheck.csproj`, `packages.lock.json`, `lattice/importer/dotnet_rules.py`,
`extract_source.py`, `stable.py`, `frontends/dotnet_aspnet.py`; modified `lattice/importer/{appmodel, check_invocation, emit_spec, formats,
go_rules, inventory, profile_gen, review, rules, workflow_run}.py`, `lattice/importer/frontends/{__init__, go_grpc}.py`,
`lattice/spec/check.py`, `lattice/spec/probes.py`. The freeze file additionally lists **every other tracked file under `lattice/`**
(397 tracked files at `4062435`, r), so any library change after the freeze is caught, not only one of these 47.

## 3. The H2 app contract

### 3.1 Brief rules (binding; H1 prereg 4.3 rules 1-5, with the additions marked "new")
1. Product terms only: a small multi-user HTTP JSON service in C# on ASP.NET Core (.NET 10 SDK 10.0.401) with EF Core over SQLite
   (`Microsoft.EntityFrameworkCore.Sqlite`); **10-16 routes**; a login; per-user data on **at least two tables**; **at least one table
   whose rows belong to a user through a parent row**; **a column that is never returned**; **a status column with at least two
   transitions** (new: some transitions refused from some states, so a state guard exists); **request validation**; at least two seed
   users of different roles (new: with at least one route that only one role may use).
2. **No idiom is named** (H1 prereg 4.3 rule 2, unchanged): no controllers, minimal APIs, route groups, filters, middleware, policies,
   claims, token formats, hashing, validation libraries or attributes, JSON attributes or naming policies, DI, repositories, exception
   handling style, query filters, migrations versus `EnsureCreated`, nor anything from H1 prereg section 2. No Lattice internals.
3. **Domain (new list):** the author chooses it, and it must not be habits, streaks, check-ins or reminders (D1), tickets, runs, jobs,
   notes, nor expenses, expense reports, reimbursements or payouts (H1). The brief names only what is forbidden, never a suggestion.
4. **Organisation (new):** the brief says explicitly that the structure of the code and the way each requirement is met are the
   author's own choice, and that nothing in the documents it receives is a design to imitate. It must not repeat H1's sentence about
   how a team would organise a production service.
5. **Wording (new):** the brief is written fresh, not edited from `BRIEF-H1.md`. Measured once after it is written, before the freeze:
   the share of the brief's word 5-grams (lowercased, punctuation removed) that also occur in `BRIEF-H1.md` must be **<= 0.20**; the
   value is stated in the freeze commit message. Fixed contract phrases (the package name, the SDK version, file names) count against it.
6. `HARNESS_API_DOTNET.md` and `TRUTH_SCHEMA_DOTNET.md` are given **byte-identical** to H1's (sha256 `d5cf9fb4...` and `1b489f92...`,
   r); the copies live in the brief directory and the H1 originals are not touched.
7. Deliverables (as H1): `tests/spec/<x>/` with its `truth.json`, `tests/spec/<x>_harness.py` (dotted module form), `studio.toml`,
   `FILES.txt`, `freeze.sha256`, `IDIOMS.md`.
8. One fixed directory (`~/Documents/dn-h2-author`); the transcript is audited (section 9): 0 reads of the repository (any checkout or
   worktree of Lattice), 0 accesses outside the fixed directory beyond the toolchain allowlist.

### 3.2 Contract check (before any Lattice run on H2; H1 prereg 4.3, with the additions marked "new")
Run by the node after the author, never by the author's own judgement: clean-copy `dotnet build -c Release -p:RestoreLockedMode=true`
0 errors; the harness smoke (`smoke.py` of `HARNESS_API_DOTNET.md` section 12, byte-identical to the doc's: every line PASS, `ALL CHECKS
PASSED`); every truth route answers neither 404 nor 405 to an authenticated call; `truth.json` validates against 3.3 and every file:line
exists; no `bin/`/`obj/`. New: the contract counts are checked from `truth.json`: 10-16 routes, `owners` >= 2 entries, `owner_via` >= 1
entry, at least one never-returned column (`not_returned` or a `private` column), a machine with >= 2 edges, >= 1 enforced bound, seed
users of >= 2 roles, and the domain is not in the forbidden list. The result is written to
`docs/results/data/2026-10-23-dotnet-h2/author/contract-check.json` (`{"pass": true|false, ...}`). **One** author fix round is allowed
(section 9.2) and logged. **Nothing of Lattice runs on H2 before the scored run** (smoke.py uses its own stand-ins, not Lattice). A
contract that still fails after the fix round voids H2: stop, report; the owner decides.

### 3.3 `truth.json` schema and matching rules
Exactly H1 prereg 4.5 (the copy `TRUTH_SCHEMA_DOTNET.md` the author gets is byte-identical): routes, tables/columns, owners, owner_via,
machines, refusals, bounds, bind_status, json_naming, login, seed_users, not_returned; route matching after lowercasing literal
segments, `{...}` -> `{}`, trailing `/` dropped; tables by SQL name (exact on SQLite); column facts by (table, column, fact); owners by
(entity, column); refusals by (route, status); bounds by (route, field, bound kind, value). A truth defect is reported as such, never
fixed after the score.

## 4. Gated rows, gates and predictions

Every row runs on H2 only. The row code is the frozen H1 runner's own row function (`dotnet_probes.h_*`) unless the "code" column says
otherwise; the gate is the H1 gate of the cited section unless marked. P(pass) is my probability that the row passes (p).

| id | probe | code | source (H1 prereg) | gate | prediction (p) | P(pass) |
|---|---|---|---|---|---|---|
| H2-ROUTES | routes vs truth | `h_routes` | 5.7 H-ROUTES | recall >= **0.90** and **0** extra | recall 0.90-1.00, 0 extra (H1 16/16 m) | 0.85 |
| H2-AUTH | auth exact on matched routes | `h_auth` | 5.7 H-AUTH | >= **0.85** | 0.88-1.00 | 0.85 |
| H2-TABLES | table recall; column-fact accuracy | `h_tables` | 5.7 H-TABLES | tables >= **0.90** and facts >= **0.90** | tables 1.00; facts 0.90-1.00 | 0.80 |
| H2-WIRE | wire names | `h_wire` | 5.7 H-WIRE | >= **0.95** | 0.93-1.00 | 0.80 |
| H2-UNREAD | silent drops (N11) | `h_unread` | 5.7 H-UNREAD | **0** | 0 | 0.85 |
| H2-BOUNDS | bounds precision (recall reported) | `h_bounds` | 5.7 H-BOUNDS | precision **1.00** | precision 0.85-1.00; recall 0.5-1.0 | 0.65 |
| H2-RAISE | refusals (route, status) | H2's own (same scorer `score_raise`) | 5.7 H-RAISE | recall >= **0.60** and precision **1.00** (stricter than H1's >= 0.95, as the orchestrator instructed; the H1-gate verdict is reported beside it) | recall 0.45-0.90, precision 0.95-1.00 | 0.45 |
| H2-PI3 | rule items of the import on the seed | `h_pi3` | 5.7 H-PI3 | verified share >= **0.50**; every refuted item corrected or baselined; every unexercised item with a reason | share **0.25-0.65** (drop from H1 dev 0.73 and D1 dev 0.336 expected); refuted 0-4 | 0.35 |
| H2-PI7 | the import 10x | H2's own normaliser | 5.7 H-PI7 | **10/10** identical | 10/10 (p 0.6); a minted form `stable.py` does not know breaks it | 0.60 |
| H2-PA1 | `derive_dn_policy` within the truth | `h_pa1` | 5.7 H-PA1 | **0** wrong (private within truth private, owners within truth owners) | 0 wrong (p 0.6) | 0.60 |
| H2-IMPL | implementation report with the imported spec | `h_impl` | 5.7 H-IMPL | **0** missing/extra route rows (health excepted) | 0 (p 0.5): needs a candidate spec whose rules cover every route | 0.50 |
| H2-WF | workflows, static + observed on SQLite | `h_wf` | 5.7 H-WF | >= 1 multi-step flow verified and **0** verified flows failing on replay | as gated | 0.70 |
| H2-JUDGE | share of the truth's owner-scoped routes judged by DN801 | `h_judge` | 5.7 H-JUDGE | >= **0.75** | 0.55-0.95 | 0.60 |

**13 gated rows.** Expected: **8-11 of 13 pass** (point estimate 9-10). The H1 runner's gate code is reused byte for byte, so the
numeric gates are H1's; the two rows with H2 code:
- **H2-PI7** (H1 prereg 5.7, Amendment 3 item 6 for the H1 normaliser): every file of the import directory is compared byte for byte
  across the 10 runs with **only timing fields masked**: `report.json` `rules.timing` and the wall-clock of the import (`report.json`
  `cost.wall_s` and the same number at the end of `report.md`'s cost line). Unlike H1, **minted values are not masked by the runner**:
  F3's claim is that the library itself writes placeholders, and this row tests that claim.
- **H2-RAISE**: `score_raise` of the H1 runner; pass iff recall >= 0.60 and precision == 1.00.

**Reported, not gated** (the rest of H1's Arm H, kept for comparison; the H1 gate is shown in the output, it is not an H2 gate):
H2-OWN (`h_own`, 0 confident-wrong owners), H2-PI4 (`h_pi4`, >= 0.95 retained on variant2), H2-PI5 (`h_pi5`, the truth machine's first
edge proposed), H2-PR3 (`h_pr3`, layer R re-run equals the import's verdict map), H2-PA0 (`h_pa0`, 0 layer A findings on the original
under `dn_spec_policy` of its candidate), H2-DET (`h_det`, extract and layer A 10x), H2-X (`h_x`, import wall <= 240 s). Predictions: OWN
pass (p 0.8), PI4 pass (p 0.7), PI5 (p 0.5), PR3 pass (p 0.7: the re-run now passes `relogin`/`crash_admits` as the import does), PA0 pass
(p 0.7), DET pass (p 0.9), X pass (p 0.9).

### 4.1 Held-out mutants HM1-16 (H1 prereg 6.2, gates unchanged)

Written by W3 from H2 and its `truth.json` only (section 9.3). Instance rule of H1 prereg 6.2: in `truth.json` order the first and the
last route that fits the operator; when only one fits it is used once and the next operator gets three instances; every substitution
recorded. Operators, detectors and gates exactly as the H1 mutants runner registers them:

| ids | operator | gated detector | class gate | prediction (p) |
|---|---|---|---|---|
| HM1-2 | `owner_read`: drop the owner condition of an owner-scoped read | layer A DN801 on that route | >= **3 of HM1-4** | 3-4 of 4 (P(class pass) 0.6) |
| HM3-4 | `owner_write`: the same for a write or state change | layer A DN801 on that route | (with HM1-2) | |
| HM5-6 | `expose_column`: expose a never-returned column on a route answering its row | layer A DN802 on that route and a private/never-returned column | **2/2** | 1-2 (P 0.45) |
| HM7-8 | `drop_login`: remove the login requirement from a Member route | layer R `R1 <route>` verified on the control, refuted on the mutant | **2/2** | 1-2 (P 0.6) |
| HM9-10 | `status_403`: answer 403 where truth says 404 `not_found` | layer R the route's `R3` 404 item refuted | **2/2** | 1-2 (P 0.4) |
| HM11 | `drop_parent` | DN801 or `R2` refuted | reported | 0-1 |
| HM12-13 | `drop_guard` | `R3` conflict item refuted | reported | 0-2 |
| HM14 | `loosen_bound` | `R3` invalid item refuted | reported | 0-1 |
| HM15 | `owner_body` | DN801 or `R2` refuted | reported | 0-1 |
| HM16 | `widen_list` | DN801 or `R2` refuted | reported | 0-1 |

**Overall gate: >= 13 of 16 killed and all four class gates** (H1 prereg 6.2, unchanged). Prediction: **9-13 killed** (point 11);
P(overall gate passes) **0.25**. The control comes first and is strict (the H1 mutants runner's `judge_control`: no DN801/DN802 on any HM
route of the original, no registered R item refuted on it, the pristine copy adds no finding, the copy is H2 byte for byte, the harness
starts the binary it is handed); if it fails, no mutant is judged and the gate is FAIL.

**Pipeline under test.** Layer A: the H1 runner's registered path, `dn_spec_policy(candidate SPEC, H2, derive_dn_policy(H2))`
(`kind="spec_policy"`). Layer R: the imported candidate spec's items re-probed **exactly as `lattice check` and the import probe them**,
i.e. `run_probes(..., relogin=who.relogin, crash_admits=who.crash_admits)` as `dogfood/dotnet_hm_dev.py`'s `DevLib.r_verdicts` does
(r: `check.py:1985-1987` sets both for a dotnet runtime; `rules.py:679-680` sets both for aspnetcore). **This fixes a known gap of the
frozen H1 runner**, whose `RealLib.r_verdicts` passed neither, so on H1 every token minted in one probe's process was refused in the
next. Reported beside the gate (not gated): layer A under `DnPolicy.from_spec(candidate SPEC)` (the policy `lattice check`'s
`_layer_a_dotnet` builds) and the count of mutants that "the pipeline reports at all" (a new layer A finding under that policy, or a
layer R item verified on the control and refuted on the mutant), as `dotnet_hm_dev.pipeline_view` computes it.

**A `NONE` line** (W3 found no instance of an operator) counts as **not killed** for its class and the overall gate; no substitute
operator is accepted after the freeze. (The H1 runner would have refused its whole input on such a line; the H2 runner reads it.)

## 5. Predictions in one place (p)
- Gated rows: 8-11 of 13 pass. Most likely failures: H2-PI3 (P fail 0.65), H2-RAISE (0.55), H2-IMPL (0.5), H2-PA1 (0.4), H2-PI7 (0.4),
  H2-JUDGE (0.4), H2-BOUNDS (0.35). Extractor rows (ROUTES, AUTH, TABLES, WIRE, UNREAD) pass with P 0.8-0.85 each.
- Rules import: verified share 0.25-0.65, below H1 dev's 0.73 (fitted) and above D1 dev's 0.336 if H2 is as plain as H1.
- Mutants: 9-13 of 16 killed; P(gate) 0.25. F5's dev 13/16 had zero slack on the app the fixes were written against.
- P(H2-PI3 and the mutant gate both pass) about 0.12.
- Spend: A3 USD 1.5-3.0 (P(cap hit) 0.35: a whole .NET service with truth on sonnet is near the cap); W3 USD 0.4-1.2 (P(cap hit) 0.2).

## 6. The runners

`dogfood/dotnet_h2_probes.py` and `dogfood/dotnet_h2_mutants.py` are new files derived from the H1 runners and scoped to the rows of
section 4 only. They import the frozen H1 runners as modules (`dotnet_probes as G`, `dotnet_mutants as M`) for the row functions,
scorers, target materialisation, build, import, patch and judging helpers; the H1 runners are not edited (their bytes stay at the H1
freeze values above, and they are in the H2 freeze file). What the H2 runners add or change:
1. `H2Lib(RealLib)`: `r_verdicts` passes `relogin` and `crash_admits` exactly as `DevLib.r_verdicts` does (section 4.1). Every other
   method is `RealLib`'s.
2. Their own row table, `missing()`, `one_row()` (the only producer of NOT RUN), setup of the H2 target only (no D1, no base tree, no
   Docker: H2 is SQLite), H2-PI7's normaliser, H2-RAISE's gate, the mutants' `NONE` handling and the reported `from_spec` view.
3. Fixed paths: data `docs/results/data/2026-10-23-dotnet-h2/`, ledger `.../ledger.jsonl`, refusal log `.../refused.jsonl`, default
   inputs `h2.tgz`, `h2-truth.json`, `h2-mutants-run/`, `h2-inputs.sha256`.
4. **The freeze and the inputs manifest are verified before the ledger is touched**: a refused launch writes one line to
   `refused.jsonl` and nothing to the ledger, so it cannot consume the once-only slot (the lesson of H1 Amendment 5).
5. Kinds: `scored`, `rerun`, `verify` only on H2 (no shakedown of H2: nothing of Lattice runs on H2 before its scored run);
   `--self-test` runs on stand-ins; `--verify-freeze-only` verifies and exits; `--check-inputs` (mutants) parses `MUTANTS.md` and the patch
   names without running anything of Lattice; `--write-freeze` writes the freeze file.
6. **Self-tests** (never write the ledger, refuse an output path under `docs/results/data`): the stand-in H2 is H1 from
   `docs/results/data/2026-10-18-dotnet/h1.tgz` with `h1-truth.json`, and the stand-in mutants are H1's 16 real patches
   (`h1-mutants-run/`). The probes self-test runs every H2 row's code path (gated and reported) with `runs = 2`; the mutants self-test runs
   the control and all 16 mutants. Both fail loudly on: a row or mutant NOT RUN while its named prerequisites are present (RM1, including
   a live check that a hard-coded NOT RUN injected into one row is caught, and an AST check that NOT RUN is built only in `one_row`); a
   row that raised; an undetected freeze defect (RM2: a dropped entry for the running script, the H1 runner, a library file, the prereg or
   the brief; an edited hash; a listed file that does not exist; an inputs manifest missing the H2 archive); `r_verdicts` not passing
   `relogin=True` and `crash_admits=True` on the stand-in; the mutants control failing; fewer than 16 mutants judged; the `NONE` reader
   not counting a `NONE` line as not killed.

## 7. Ledger
One append-only ledger, **`docs/results/data/2026-10-23-dotnet-h2/ledger.jsonl`**, committed **empty** with the freeze file. Arms:
`H2:H` (probes) and `H2:HM` (mutants). Each run appends `{ts, run_id, runner, runner_sha256, freeze_sha256, inputs_sha256, arm, kind,
out, lattice_file, dotnet_sdk, status}` with `status` `started`, then `completed`, `crash_before_metric` or `crash_after_metric` (H1
prereg 11.3). The refusal rules are the H1 runner's `ledger_refusal`, unchanged: `scored` once per arm; `rerun` once, only after
`crash_before_metric` with the same runner and freeze bytes; `verify` once, after a completed scored (or rerun) run with the same runner
bytes. The ledger is not in the freeze file (it changes with every run).

## 8. Freeze file and self-hash rule
**`docs/results/data/2026-10-23-dotnet-h2/freeze.sha256`** (sha256sum format, paths relative to the repository root), written **after
this prereg and any amendment to it, after both self-tests pass and after the refusal test**, and committed last. It lists:
(a) this prereg; (b) `dogfood/dotnet_h2_probes.py`, `dogfood/dotnet_h2_mutants.py` and the frozen H1 runners they import
(`dogfood/dotnet_probes.py`, `dogfood/dotnet_mutants.py`); (c) the brief directory `brief/BRIEF.md`, `brief/BRIEF-W.md`,
`brief/HARNESS_API_DOTNET.md`, `brief/TRUTH_SCHEMA_DOTNET.md`, and `dispatch_author.sh`, `dispatch_mutants.sh`, `audit_transcript.py`;
(d) the self-tests' stand-in inputs `docs/results/data/2026-10-18-dotnet/h1.tgz`, `h1-truth.json`, `h1-mutants-run/MUTANTS.md` and
`m01.diff`-`m16.diff`; (e) the 47 library files of section 2; (f) every other tracked file under `lattice/`.

**Self-hash rule (H1 prereg 11.2, unchanged in spirit):** before any metric and before any ledger line, each runner re-hashes every entry
of the freeze file and refuses on a mismatch, a listed file that does not exist, or a must-list path that is not listed. The must-list is
(a)-(d) above, the 47 files of (e), and every file present under `lattice/` at run time (`__pycache__`, `*.pyc`, `.DS_Store`, `bin/`,
`obj/` excepted), so an added library file is refused too. The running script must be listed. No flag skips or narrows the check.

**H2 itself is frozen in a second file, the inputs manifest `docs/results/data/2026-10-23-dotnet-h2/h2-inputs.sha256`**, written by the
node that archives H2 after the contract check and W3 (it cannot exist now): `h2.tgz` (the author's `tests/` tree: module and harness),
`h2-truth.json`, and `h2-mutants-run/MUTANTS.md` + `m01.diff`-`m16.diff` (byte-identical copies of W3's files, checked with `cmp`). Each
runner verifies it the same way (every entry re-hashed; the probes runner requires the archive and the truth to be listed, the mutants
runner also the 17 mutant files) and writes its sha256 into every ledger line. It is committed before any scored run; its sha256 and
commit go into the results document.

**Order lesson (H1 Amendments 5-6), binding:** this prereg is in the freeze file, so **no amendment is appended to it after the freeze
file is written**. Anything learned later (contract-check notes, author interpretations, W3's substitutions) goes to the results
document or to files beside the inputs manifest, never into a frozen file. A needed change to any frozen file voids the freeze: stop,
report; the owner decides whether a new freeze is written, and that is disclosed as a departure.

## 9. Author protocol (dispatched by a later node; this node does not dispatch)

### 9.1 A3 writes H2
- `docs/results/data/2026-10-23-dotnet-h2/dispatch_author.sh`, run from the repository root at the freeze commit. It refuses unless the
  freeze file verifies (`shasum -a 256 -c`), the fixed directory **`~/Documents/dn-h2-author`** is absent or empty, and the spend log
  leaves room. It copies `BRIEF.md`, `HARNESS_API_DOTNET.md`, `TRUTH_SCHEMA_DOTNET.md` into the directory, then runs, in it:
  `env -u ANTHROPIC_API_KEY <other ANTHROPIC_*/CLAUDE_CODE_USE_*/LATTICE_*/nested-session variables unset> claude -p "<prompt>" --model
  sonnet --max-budget-usd 3.00 --max-turns 150 --output-format json --session-id <fixed uuid> --setting-sources "" --strict-mcp-config
  --disable-slash-commands --tools Bash,Read,Write,Edit,Glob,Grep --permission-mode dontAsk --allowedTools Bash,Read,Write,Edit,Glob,Grep`
  (the flag set of `dogfood/implementation_agent2.py`'s invocation plus a fixed session id so the transcript can be found, and no
  MCP server, so no Lattice tool is reachable). **No API key anywhere; the `claude` CLI only.**
- Model **sonnet**, cap **USD 3.00** for A3 in total (the dispatch and the fix round together). H1's author A2 was opus (H1 prereg
  Amendment 4); the change of model is a confound, disclosed here.
- Outputs (in `docs/results/data/2026-10-23-dotnet-h2/author/`): the CLI's JSON result, the session transcript copied from
  `~/.claude/projects/<slug>/<session id>.jsonl`, and the audit. One line per call goes to `spend.jsonl` (cost from the CLI's own
  `total_cost_usd`).

### 9.2 One fix round
`dispatch_author.sh fix <file>` resumes the same session (`--resume <session id>`) with the contract-check failure text, cap = USD 3.00
minus A3's spend so far (refused below USD 0.05), once only (refused if a fix line is already in `spend.jsonl`).

### 9.3 W3 writes HM1-16
`dispatch_mutants.sh`, after the contract check passed and `h2.tgz`/`h2-truth.json` exist: refuses unless `author/contract-check.json`
says `"pass": true` and the fixed directory **`~/Documents/dn-h2-w`** is absent or empty; extracts `h2.tgz` there with `BRIEF-W.md` as
`BRIEF.md`; runs a separate session (its own fixed session id) of **sonnet** with the same flags and **`--max-budget-usd` = min(1.50,
4.50 - spend so far)** (refused below USD 0.05), `--max-turns 80`. W3 delivers `mutants/MUTANTS.md` and `mutants/m01.diff` ...
`mutants/m16.diff` (**exactly the names the mutants runner reads**: the lesson of H1 Amendment 6), each applying with `patch -p1` at the
module root and building with 0 errors. The script copies them byte for byte into `h2-mutants-run/` and checks each with `cmp`.

### 9.4 Spend caps and what happens at a cap
**Total `claude` CLI spend for A3 + W3 <= USD 4.50** (A3 <= 3.00 including the fix round; W3 <= 1.50 and <= the remainder). A call whose
result is not `success` (a budget or turn cap hit, an error, no result) or whose cost reaches its cap **stops the round**: the script
exits 4 and prints the state; the deliverable is not contract-checked or used; nothing is re-dispatched; **the owner decides**. No API
key is used at any point; a usage limit of the CLI is a stop of the same kind.

### 9.5 Transcript audit
`audit_transcript.py` reads the session transcript (JSONL) and lists every tool call. Classes: **forbidden** (a path under any Lattice
checkout or worktree `~/Documents/lattice*`, under `~/.claude`, or under another author's or Streak's directory), **outside** (any
other real path outside the fixed directory and not on the toolchain allowlist `/opt/homebrew`, `/usr`, `/bin`, `/sbin`, `/System`,
`/dev/null` and the standard streams; a tool outside the allowed set; a session `cwd` outside the fixed directory), and **review**
(the shared temporary directories, `/etc`, and path tokens built from shell variables the audit cannot resolve; listed, not voiding).
Every Bash command is printed for a human to read; path extraction from shell text is heuristic and stated as such. **One forbidden or
outside item voids the deliverable** (H1 prereg 11.7) after the dispatching node confirms it by reading the call; the round then stops
and the owner decides (the caps do not cover a second author).

### 9.6 Independent verification
After both scored runs, an independent verifier V (opus) re-runs both runners with `--kind verify` from the archived inputs in a fresh
scratch directory; 0 verdicts may differ, and any difference is reported (H1 prereg 3, stage 6b; results section 7).

## 10. Process rules (binding)
1. **Order:** this prereg (committed alone) -> runners -> brief and dispatch scripts -> self-tests pass and the refusal test passes ->
   freeze file + empty ledger (last commit of this node) -> A3 -> contract check (one fix round) -> W3 -> inputs manifest committed ->
   one scored run of each runner -> V -> results.
2. **Once only.** One scored run per arm; a rerun only after `crash_before_metric` with byte-identical frozen files; no re-score after a
   number is seen. A miss is reported, its cause localised as far as possible, and **not tuned**; anything learned after a number goes to
   a separate post hoc section with its own commit, never moving a gated value.
3. **NOT RUN** only when a named prerequisite is missing (no .NET SDK; no H2; a NuGet restore failure of the H2 build); anything that
   raises is a FAIL (H1 prereg 11.5). A NOT RUN is never a pass; the gate is then incomplete.
4. **Never:** an API key; a model call outside the A3/W3 dispatch; Lattice on H2 before the scored run; an edit to a frozen file after
   the freeze; `git add -A` or `git add .`.

## 11. Risks, and what this cannot show
1. **One app.** A pass shows the fixes reach one more blind code base; a failure names an idiom. The brief cannot force variety without
   naming idioms.
2. **Sonnet author.** H2 may be plainer (or less consistent) than opus-written H1; its truth may hold more errors. A truth defect is
   reported, not fixed.
3. **Cap.** A sonnet author may hit USD 3.00 before the truth is complete; then the round stops (9.4) and nothing is measured.
4. **Audit heuristics.** Shell text is parsed heuristically; a deliberately hidden read (a computed path) could evade it. The audit
   prints every command so a human can read them.
5. **Layer A policy.** The gated mutant path keeps H1's registered `spec_policy`; `lattice check` builds `DnPolicy.from_spec`. The
   reported view shows the difference; it cannot move the gate.
6. **Cannot show:** MySQL behaviour (H2 is SQLite), gRPC, Razor/Blazor, behaviour lifting, a real-agent build.

## Amendment 1 (node H2a, 2026-10-23, before the freeze file; predictions only, no gate changes)
Both runners' self-tests ran on the stand-in H2, which is H1 (dev data), and both passed: `SELF-TEST OK`; RM1 and RM2 caught; the NONE
reader is correct; layer R passed `relogin=True, crash_admits=True`. These are the dev values the H2 code path measures on H1. They are
**(m) dev measurements on H1**. The probes self-test ran with `runs = 2` while the mutants self-test ran in parallel.
- Gated rows on H1: 12 of 13 pass. ROUTES 16/16, AUTH 1.00, TABLES 4/4 with facts 0.990, WIRE 28/28, UNREAD 0, BOUNDS precision 1.00
  and recall 1.00, RAISE precision 1.00 and recall 0.857, PI3 **0.752** (85 of 113 verified, 0 refuted), PI7 2/2 identical with the
  timing-only mask, PA1 0 wrong, WF 16 verified with 0 failing replay, JUDGE 9/9. JUDGE uses the H1 runner's own fallback, "proxy: the
  extract", because the library hands no judged list. **H2-IMPL fails:** the implementation report reads **0 requirements** in the
  imported candidate spec, so every route is `implemented_not_required`. This is the same structural cause as on H1 before F1-F6, and
  F1-F6 did not change it.
- Reported rows on H1: OWN, PI4, PA0 and DET pass. PI5 fails (no machine edge proposed). PR3 differs on one item
  (`R3 PATCH /reports/{reportId} 400 #2`: absent from the import's map, verified on the re-run). X is 250 s against the shown 240 s,
  measured under the parallel load.
- Mutants on H1 through the H2 path: **13 of 16 killed**, and all four class gates pass. HM12 and HM13 survive because their 409 item
  is not verified on the control. HM14 survives because its 400 item is not refuted. The pipeline view also reports 13 of 16. This
  repeats F5's dev result, again with zero slack.

**Updated predictions (p), written before H2 exists. The gates of sections 4 and 4.1 are unchanged:**
- H2-IMPL P(pass) 0.50 -> **0.05**. The cause is structural and was seen on the app the fixes were written against.
- Gated rows expected to pass on H2: **8-10 of 13** (was 8-11).
- Reported rows: PI5 0.5 -> 0.2, PR3 0.7 -> 0.3, X 0.9 -> 0.6.
- Mutants unchanged at 9-13 killed with P(gate) 0.25. Even H1 has zero slack, and the drop_guard and loosen_bound classes do not fire.
- H2-PI3 unchanged at 0.25-0.65 with P 0.35. H1 dev through the H2 path is 0.752, and that is the app the fixes were fitted to.
