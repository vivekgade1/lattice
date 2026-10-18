# gRPC round 4 (Spike I4), build and development stage: results

**Status line.** Dev stage only. Arm M (Streak/MySQL): **NOT RUN** (no Streak checkout, no Docker daemon). Arm L (build round):
**NOT RUN** (its own node). The gated held-out stage PG-HO4 (HO4-RPC, -SAFE, -CODES, -AUTH, -TABLES, -REC, -FLOWS, -DET, -NW):
**NOT RUN, by design**: no `heldout4` was looked for, created or scored by this node. Nothing is committed or staged. Gates missed
are listed in section 1, plainly; two are missed on purpose-built consequences of the pre-registration itself (1.2, 1.3), one is a
size budget I exceeded by 87 % (1.1).

Branch `spike-i-grpc-round4` at `/home/user/wt/i4`, from `be26a94` (`next-phases` with Spikes E, H, I merged) plus the
pre-registration. Environment re-measured: go1.24.7, uv 0.11.32, Python 3.13.16; no Docker. Data: `docs/results/data/2026-10-18-grpc-round4/`.

## 1. Summary

| what | result |
|---|---|
| Arm 0, gated rows (12) | **11 PASS, 1 FAIL (REG)** |
| Arm G, gated rows (8) | **7 PASS, 1 FAIL (PR3-ALL)**; PD4-KILN (6 rows) and PD4-HARBOR (6 rows) REPORTED, never gated |
| NOT RUN | Arm M, Arm L, PG-HO4 (the 9 held-out rows) |
| mutants | **61 of 62 killed after three re-expressions** (first run 58 of 62). New 26 of 27 (GW4-9 survived); re-applied 35 of 35 (GR17 after re-expression) |
| line budgets (`git diff --numstat be26a94`, ins+del) | `goextract_grpc.go` **749 of 400 (MISSED)**; `go_grpc.py` 49 of 50; `grpc.py` 40 of 50; `transport.py` 4 of 6 |
| frozen files | no tracked change in any frozen path; the 9 named frozen files hash-equal to `be26a94`; deskd, kilnworks, harborline freezes intact |
| tests | `tests/grpc tests/importer tests/gocheck`: before 7 failed / 937 passed / 96 errors; after 11 failed / 1024 passed / 96 errors; the **same failing ids plus four** (1.2) |
| ruff, gofmt | `ruff check` and `ruff format --check` clean on all touched/created `.py`; `gofmt -l` clean on `lattice/gocheck/src` and the four Go fixture families |

### 1.1 Gate missed: the Go line budget (749 of 400)

Q10 accepted 400 lines for D1-D3 (D3.4 optional, not built). I used **712 insertions + 37 deletions**. By decision (hunks,
rounded): D3 translation table, translators, `codesFrom`, `translated`, sentinel/`%w` edits about 245; D2 (method values, option
fields, constants, builder chains) about 185; shared call-site/parameter/field-flow helpers 107; D1.4 walk (init wiring, built
types, interface dispatch, unwalked variables) about 90; D1.1/D1.3 registrations about 75; 28 comment lines and 25 blank lines are
inside that. Cause: the pre-registration costed each decision as a rule, not as the parameter/field/call-site machinery three of
them share, and D2.4's chain evaluation (about 90 lines alone) and D3's three table forms were each larger than a line-budget
guess. I did not trade a decision away to meet the number (every P4 fixture exercises the code that is there). **A reviewer should
read this as "the budget was wrong by 1.9x", and decide whether the decisions or the budget move.**

### 1.2 Gate missed: REG (same failing ids), by the pre-registered D1.3

Four ids fail after and not before, all in the frozen `tests/grpc/test_wiring3.py`:
`test_every_state_equals_the_truth[Dead|Gone|Never]` and `test_r6_dead_and_test_only_registrations_are_not_registered`.
Round 3's fixture has the referenced-but-unresolvable `Opaque` registration, so under D1.3 (accepted in Q3: *every* declared service
with no resolved registration is `unread` while any registration call is unresolved) the three never-registered services beside it
become `unread` instead of `not_registered`; those tests encode exactly the behaviour D1.3 forbids. I implemented D1.3 as written and
documented the change with `test_d1_3_changes_round_3s_wiring_fixture_by_design`; I did not edit a frozen test. REG here runs
`tests/grpc tests/importer tests/gocheck` as the task text states (the prereg lists also `tests/spec tests/mcp tests/studio`; not run).
Failing ids: before 93 distinct, after 97; the diff is the four above and nothing else (`pytest-{before,after}-ids.txt`).

### 1.3 Gate missed: PR3-ALL (round 3's runner, un-nested, at round-4 code)

Round 3's runner, unchanged, run arms 0 and G at round-4 code: arm 0 **4 of 8** round-3 PASS rows still PASS (regressed P3-BC,
P3-UNIT, P3-WIRE, REG), arm G **7 of 8** (regressed PR2-ALL). No byte change was declared in Amendment 1, so this row FAILs.
Attribution: P3-WIRE and P3-UNIT and REG are the four D1.3 ids (1.2); P3-BC's AST rule forbids any change to `grpc.py` outside
`reconcile_with` and to `go_grpc.py` (D6 and D5 change them) and its extract rule flags kilnworks (the declared D2.3 difference);
PR2-ALL (round 2's runner at round-4 code) regresses P2-BC (same AST reason), REG, and PR2-DESK (dev). Every other round-3 row
(P3-AUTHU, P3-DET, P3-NW, P3-SCORE, PR3-N/DESK/SESS, PF3-N/DESK/LEAK) still PASS.

## 2. Hashes (for Amendment 2; the orchestrator commits and records them)

```
3f181be7e7c50204bdadbc0c1a22a0988f4c68fd610817c9782011d540aa4354  lattice/gocheck/src/goextract_grpc.go
7543c9fabc0e7a9369f760e18d6f2220a712ea010df821bcd496ea81927db799  lattice/importer/frontends/go_grpc.py
1bd489dffd545aaedd923756a52aaebb84ab89e434f14a37aa048f707a204c80  lattice/spec/grpc.py
04979019468e5d4fd9dc33c7d98ef62c32c5ae41fd5211421ec43ca4cca9ae4a  lattice/importer/transport.py
e963747ae1545e68a31b8b740fd259b687a6121a4712667d239287683a2bae90  dogfood/grpc_scorer4.py
dabdd4591adb8f15dc08ef6b24da031beb3dd528688ae285cfef6d0b8178cef1  dogfood/grpc_scorer4_cases.json
82aca97b4d508d0537a567914be537b2529cc219a6d9effb630c0d1bfdf1350b  dogfood/grpc_probes4.py
6666ac9bedc8dfb49edd18177091c40ece5dcf3b149a097e82bdf004a4a04610  dogfood/grpc_contract4.py
c6039e8554765eedcb72e49162d7c8cdc1b7de6318185d92c6aa40d3ae7f3165  docs/results/data/2026-10-18-grpc-round4/freeze4.sha256
```
The first eight lines are `freeze4.sha256`. Runner = `grpc_probes4.py`, scorer = `grpc_scorer4.py`, contract = `grpc_contract4.py`.
`freeze-dev4.sha256` (the five fixture `truth.json`, hashed before any library code) is in the same directory; every dev probe
and unit test re-checks it. `grpc_mutants4.py` is not in the freeze list.

## 3. Arm 0 (gated), predicted against observed

| id | prediction (p) | observed | verdict |
|---|---|---|---|
| P4-BC | all identical | HTTP outputs 73/73; `reconcile` bytes N and sessions equal; flows/plans/extract/reconcile on N and flows/plans/extract on deskd equal; extract + RepoModel of N, T, sessions, **deskd** identical; kilnworks and harborline differ (declared); AST: only `rpc_login` + `_login_fields`, `AuthDriver.rpc` defaults, D5 additions | PASS |
| P4-WIRE | exact, 0 S8 | 24 of 24 states equal the truth, 0 S8 | PASS |
| P4-AUTHI | exact | 7 of 7 cases (auth and kinds), 0 Public behind an unread mechanism | PASS |
| P4-CODES | exact | 9 of 9 rpcs, 0 UNKNOWN not in the truth | PASS |
| P4-TABLES | exact | 3 of 3; `tables_source` absent / `harness_db` / absent; no temp dir left; 0 writes under either root | PASS |
| P4-LOGIN | exact | username; name; email+passcode; user; `login_fields_ambiguous` x2 (no call made); explicit wins | PASS |
| P4-DET | 10/10 | 10 of 10 identical on N, wiring4, authwire4, codes4, tables4, deskd, kilnworks, harborline; two roots; no leak | PASS |
| P4-NW | all 0 | 0 changed, 0 new, 0 claude calls over N, T, sessions, fixtures, deskd, kilnworks, harborline | PASS |
| P4-UNIT | 40-90 tests, >= 40 | **91** green (wiring4 20, auth4 27, codes4 19, tables4 10, login4 15) | PASS; **prediction range missed by 1** |
| P4-SCORE | 30-45 cases, >= 24 | **58** (30 round-3 cases unchanged + 28 new: S8 12, S9 6, S10 10) all agree | PASS; range missed (I kept round 3's 30 verbatim) |
| P4-PROC | 5/5 | 5 of 5 (section 6) | PASS |
| REG | same failing ids | four more (1.2) | **FAIL** |

## 4. Arm G (dev)

| id | prediction | observed | verdict |
|---|---|---|---|
| PR4-N | byte-equal to round 3 | 10 of 10 answered, bytes equal round 1 | PASS (N names no login field: D6.2 discovered `name`/`password`, same request) |
| PR4-DESK | S5 1.00/1.00, 0 auth_refused | coverage 1.0, agreement 1.0 (20/20), Backlog UNIMPLEMENTED | PASS |
| PR4-SESS | as round 3 | codes equal the truth, 0 login_failed, 4 disagreements isolated | PASS |
| PF4-N / PF4-DESK | N 17/17; deskd 41/20, edge 21/34, coverage 4/7 | 17/17 verified; 41 recovered, 20 verified, edge 21/34, coverage 4/7, 0 vbf | PASS, counts identical |
| PF4-LEAK | 0 | 0 after reconcile, flows and a forced startup failure | PASS (see 8.12 for a failed intermediate run) |
| PR3-ALL | all PASS | 4/8 and 7/8 (1.3) | **FAIL** |
| PX4 | <= 30; 30; 180; 30 s | harborline extract 0.08 s; deskd reconcile 1.23 s; deskd flows 17.6 s; D5.2 on the harness fixture 0.05 s | PASS |

### 4.1 PD4-KILN (post hoc, tuned-on, reported only; round 3 strict in brackets)

RPC 1.0/1.0 (1.0/1.0); SAFE 0; CODES 0.80/0.941 (0.8/0.941; 2 `UNKNOWN` extras unchanged, 8 rpcs with some unread code); AUTH 0 wrong,
**unclear 0/17** (17/17: D2.3's health table made `openDoors` literal; predicted 0.0-0.2); TABLES 1.0, source `go`; REC 1.0/1.0, 1 disagreement
isolated; FLOWS 17 recovered / 7 verified, edge 11/33, coverage 6/9 (as round 3). Every row inside its predicted range.

### 4.2 PD4-HARBOR (post hoc, tuned-on, reported only; round 3 in brackets)

| row | predicted | observed |
|---|---|---|
| RPC | 0.92-1.00 / 0.92-1.00 | **1.00 / 1.00** (0.68 / 0.68): Vessels, Berths found; Fuel `not_registered` correct |
| SAFE | 0 | **0** |
| CODES | 0.55-0.90 / 0.85-1.00 | **recall 0.407, precision 1.00** (0.222 / 0.667): recall **below** the range, 0 `UNKNOWN` extras, 13 of 21 implemented rpcs have some unread code |
| AUTH | 0 wrong, unclear 0.0-0.40 | 0 wrong, **unclear 2 of 22 = 0.09** (22/22) |
| TABLES | 1.0, `harness_db` | **1.0, `tables_source: harness_db`** (0 of 7) |
| REC | coverage 1.00, agreement 0.85-1.00 | **1.00 / 1.00** (0.04): D6 found `username`; 0 login_failed; S6 exercised |
| FLOWS | recovered 5-60, verified 0-8, edge 0.00-0.50, coverage 0-5 | **recovered 0** (below the range), verified 0, edge 0/23, coverage 0/8; the 8 truth flows all replay (HO3: 0 of 8) |

Attribution of the misses (read after the fact from the extract, `extract-harborline-round4.json`; nothing was changed for them):
- **CODES.** Berths (4 rpcs): the `*problem` error type with a `GRPCStatus()` method is D3.4, optional, not built (`grpc_error_not_traced`).
  Vessels (4): the local `fail(c, ...)` wrapper whose code is a parameter is D3.4's second form, not built; D3.2 reads a parameter that
  indexes a map, not a parameter that is the code. Yard (3): `codeFor[r]` indexes by the **receiver**; D3.2 reads parameters only.
  Bookings (5): the codes found are all right, but the translator's default `INTERNAL` is missing: `gate.Keeper.admit`, also
  reachable from an interceptor, calls `errors.Is` and does not end in a literal status return, so the default is withdrawn for the
  whole module (`nodef`). All four are shapes outside the built part of D3, as Q8/risk 2 said to expect.
- **FLOWS.** Route-level tables come only from the extractor's reading of SQL in Go (D5.3, as written); harborline's handlers use
  `database/sql`, so no rpc has a table and the frozen flow rules bind nothing. TABLES 1.0 from the harness database does not help.
- **AUTH unclear 2.** `Accounts/Login` and `Tides/GetTideTable` sit behind two interceptors that may refuse (the literal
  `Keeper.Unary` and `fault.Translate`, classed `auth_unknown`: `Wire` returns an `err` that is not assigned in the function), the
  round-3 rule for "several interceptors" gives `unclear`: safe, not wrong.
- Not a miss: before the two fixes in 8.4/8.5 this row read TABLES 0 of 7 (`tables_source` `go`) and CODES 0.407 / 0.759 with 6 `UNKNOWN` extras.

## 5. Mutants

Run on a scratch copy; the control (unmodified copy) first, green for every named probe, and for `test_wiring3.py` red on the same four
ids (kill = a different return code **or a different set of failing ids**). Table: `mutants4.json` (first run, 62 ids),
`mutants4-rerun.json` (the four re-run after changes to the mutants or a fixture harness, 8.7), `mutants4-first-run.log`.

| id | mutant | result | killed by |
|---|---|---|---|
| GW4-1 | D1.1 off | KILLED | P4-WIRE, test_wiring4 |
| GW4-2 | D1.1 first call site only | KILLED | P4-WIRE, test_wiring4 |
| GW4-3 | D1.2 off | KILLED | P4-WIRE, test_wiring4 (same fault as round 3's GW6: D1.2 is realised by round 3's constructor rule) |
| GW4-4 | D1.3 off | KILLED | P4-WIRE (S8), test_wiring4 |
| GW4-5 | D1.3 too broad (dead call blocks) | KILLED | P4-WIRE, test_wiring4 |
| GW4-6 | deviation 8.7 left | KILLED | P4-WIRE, test_wiring4 |
| GW4-7 | D1.4 off | KILLED | P4-WIRE, test_wiring4 |
| GW4-8 | D1.4 too broad | KILLED | P4-WIRE, test_wiring4 |
| **GW4-9** | D1.4 func-type adapter: a conversion does not count as building the type | **SURVIVED** (twice) | none. The function value in `registry.InstallFunc(install)` is wired by round 3's own value rule (any function named as a value in a walked function is followed), so the adapter clause of D1.4 changes no state. The prereg's predicted killer was wrong. A finding: this clause is dead weight on every shape I could build |
| GU4-1..6 | D2.1, D2.2, D2.3, D2.4 (x3) | KILLED | P4-AUTHI, test_auth4 each |
| GK4-1..3 | D3.1, `%w`, D3.2 | KILLED | P4-CODES, test_codes4 each |
| GK4-4 | D3.3 off | first run SURVIVED, re-expressed, **KILLED** | test_codes4 (not P4-CODES: its fixtures hold no plain error beside a translator). The first text sat in `evalSentinel`; `translated()` makes it equivalent |
| GT4-1 | D5 override | KILLED | P4-BC-EXT, test_tables4 |
| GT4-2 | D5.2 under the target root | first run SURVIVED, fixture harness changed, **KILLED** | P4-TABLES, test_tables4. The harness now refuses a path under its own directory (the check D5.2's "outside the target" implies) |
| GL4-1..3 | D6 (x3) | KILLED | P4-LOGIN, test_login4 |
| GS4-1, GS4-2 | scorer S10, S8 | KILLED | P4-SCORE |
| GS4-3 | runner: own entry excluded from its check | KILLED | P4-PROC |
| GW1-GW11, GU1-GU3, GC1-GC2, GS1-GS2 | round 3's 18, re-applied | 18 of 18 KILLED | see `mutants4.json`; GW1, GW8, GW9, GW11 and the check lists were re-expressed (round 3's text moved; `note` in the table); GW3, GW5, GW10, GW11, GU1, GU3, GC2 are killed by unit tests only |
| GR1-GR17 | round 2's 17 | 17 of 17 KILLED | GR17 first run SURVIVED (round 2's `p2-bc-ast` is red at control and cannot see it); re-expressed with P4-BC-AST added, **KILLED** |

New mutants: **26 of 27**. Re-applied: **35 of 35** (one after re-expression). Total **61 of 62**; the pre-registered 62 of 62 is **missed by GW4-9**.

## 6. The held-out process (6.4), rehearsed

`grpc_probes4.py --arm H4` checks, **before any metric**: its own sha256 and the scorer's against Amendments 2/4, the contract
check's, `freeze4.sha256` (hash and listing, with the runner's own entry required: its absence refuses), the held-out
`freeze.sha256` against Amendment 3 and its listing; nothing staged. It keeps a ledger (`h4-runs.json` in `--out`): run 1 normal,
run 2 only after a run that crashed before any metric, run 3 refused. Rows are labelled by `grpc_scorer4.provenance` (S10), never
by the runner. P4-PROC runs a scratch copy with a stand-in directory that is **no app** (`--process-test`, stub rows):
(1) a runner byte changed after recording: refused, exit 2, no ledger; (2) the runner's own entry missing from freeze4: refused;
(3) a forced crash then a re-run, equal bytes: `blind`; (4) a crash, a runner fix recorded in Amendment 4, a re-run: `post_hoc`;
(5) a third run: refused, exit 4. 5 of 5. `--process-test` and its forced-crash hook live in the hashed runner bytes and are only
reachable with that flag. The orchestrator must write Amendment 2 with the hashes of section 2 using these file names beside them
(`grpc_probes4.py`, `grpc_scorer4.py`, `grpc_contract4.py`, `freeze4.sha256`), Amendment 3 with `heldout4/freeze.sha256`.

`grpc_contract4.py` was exercised on **development data only**: kilnworks: import ok, build ok, 3 logins ok, 9 flows ok;
harborline: `import failed: TypeError` (its positional `AuthDriver.rpc(...)`, the round-3 crash class, caught before a manifest).

## 7. Predictions I was asked to check, and where they missed

Missed: P4-UNIT range (91 > 90); P4-SCORE range (58 > 30-45); PD4-HARBOR CODES recall (0.407 < 0.55) and FLOWS recovered (0 < 5);
mutants 62 of 62 (61); the Go budget; REG; PR3-ALL. Held in range: every PD4-KILN row, PD4-HARBOR RPC/AUTH/TABLES/REC/SAFE, PX4,
P4-BC (including deskd byte-identical, which a first version of my own D3.3 broke, 8.5), every dev fixture probe.

## 8. Deviations and defects, in the order they happened

1. **Go budget exceeded**, 749 of 400 (1.1).
2. **REG and PR3-ALL** miss through D1.3, as the pre-registration itself predicts for any app with one opaque registration (1.2, 1.3).
3. **D1.4 `new(T)`** is not recorded as building `T` (a literal, a conversion `T(f)`, `pkg.T(f)` are). Not exercised by any fixture.
4. **D5.2 looked for `studio.toml` at the extract root**; every harness lives in the module directory (`extract_json(parent, "<app>")`).
   My tables4 fixtures called `(case_dir, ".")` and hid it; PD4-HARBOR's first run showed `tables_source: go` and TABLES 0 of 7.
   Fixed to the module directory (49 of 50 lines), test added, probes re-pointed to the `(parent, app)` layout. Not a score-driven change:
   the pre-registration says the `db` harness is read when the Go tree has none.
5. **D3.3 was narrower than decided, then too broad.** First: only sentinel errors took the table (round-3 byte-compat); PD4-HARBOR showed
   6 `UNKNOWN` extras remained, so `translated()` now gives a handler's plain error the translation's literal default, else `unread`.
   That change first made **deskd** differ from `be26a94` (`SignUp`, `ListTickets`, `DeleteTicket` lost their true `UNKNOWN`: deskd's
   truth lists them): my translator detection took any `errors.Is` guard in the module for a translation layer. Fixed by restricting
   translators to the unary interceptors and the functions they call (three deep), as D3.1 says ("an interceptor, or a function every
   handler result passes through"); deskd is byte-identical again. The intermediate state never reached a final run.
6. **Declared differences are exactly two**: kilnworks (D2.3: `openDoors` literal; 17 rpcs' auth/auth_reason/unread, and the interceptor)
   and harborline (the intended fix). Verified field by field; nothing else in kilnworks changes.
7. **Mutants re-expressed after seeing survivors** (disclosed, hygiene not tuning): GK4-4 (equivalent first text), GT4-2 (the fixture harness now
   enforces "outside the target"; harness files are not hashed), GR17 (detector red at control). Also, before the first run: GU4-2's
   text indentation, GT4-1's anchor (after fix 4), and round 3's GW1/GW8/GW9/GW11 (moved text). GW4-9 was left as a survivor.
8. **A `ruff format` pass** reflowed my own new files only; no frozen file was touched.
9. **Unresolved-registration reason text** (D1.3) lists every unresolved call as `file:line`; the marker is the first.
10. **Fixtures are multi-module directories** (`wiring4/{main,unres,varlost}`, `authwire4/<7 cases>`, `codes4/{tbl,nodefault,keys}`,
    `tables4/{gormtables,harnessdb,brokendb}`, `login4` = protos only), because D1.3 is module-wide: the "unresolved descriptor" and
    "8.7" cases cannot share a module with the `not_registered` negatives. `login4`'s messages are named `...Req` (a method and a
    message of one name do not compile in proto3).
11. **A generic fixture helper** `genpb.py` wrote the `protoc-gen-go-grpc`-shaped files (no protoc here); 14 of the 16 modules `go vet` clean (the two with a database or gorm import were parsed, not vetted).
12. **Orphan process.** I killed a background chain mid-run; its notes server survived and made PF4-LEAK and round 3's PF3-LEAK FAIL in
    one intermediate run. Killed, both chains re-run clean; the files here are the clean runs.
13. **Ordering.** I wrote `freeze-dev4.sha256` (five truth files) before any library code, as required; the five proto/Go fixture files
    other than `truth.json` were not hashed, and `login4`'s proto was renamed (`...Req`) after the hash, which does not touch `truth.json`.
14. **D3.4 not built; D3.2 reads parameters, not receivers; D2.4's per-instance chain** evaluates only the chain that reaches the gate's
    field (a second policy value elsewhere does not leak in: `test_d2_4_a_second_policy_value_does_not_leak...`).

## 9. What these results cannot show

n = 1 held-out is not here at all. The dev rows are tuned-on or fixture rows I wrote after reading harborline; P4-WIRE/AUTHI/CODES
passing says the code does what my fixtures spell out, not that a new author's idioms are covered (three of harborline's five error
idioms are not). The 749-line change is larger than any earlier round's and was reviewed only by its tests and mutants.
