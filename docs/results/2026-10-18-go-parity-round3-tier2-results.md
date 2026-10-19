# Go parity round 3, Tier 2 (build node B2, runner completion G3-R): results on the development data

Pre-registration `2026-10-18-go-parity-round3-prereg.md` with Amendments 1-2 (section 5.6 for the rows, 6.1 for LM16 and LM17, section 7
for the budgets). Branch `np-go3-r` (from `np-go-round3`, which holds B1 and B2). Tier 0 is in `...-tier0-results.md`, Tier 1 in
`...-tier1-results.md`. All values are development data (S1, S2, S3, `goparity3`, Streak, job-scheduler); nothing was scored blind and no
model was called. The values below are B2's observations (its scratch script `tier2.py`) re-computed by the runner rows of
`dogfood/go_parity_probes3.py --arm 2` (self-test, see the end); the two agree.

## What was built
K8 (GO805 for Go, PY805's not-owned half): the opt-in policy extract links an owner comparison to the constant 4xx refusal that the next
statement's error test returns (`lattice/gocheck/src/goextract_raise.go`, `goextract_owner.go`), `lattice/gocheck/notowned.py` derives the
module's one not-owned status and reports the routes that answer another status as `GO805|<METHOD path>|not_owned`, `GoPolicy.not_owned`
carries the answer, and GO805 leaves `GO_GATED`. K10 (the matrix J0', `docs/results/2026-10-18-parity-matrix-j0prime.md`). **K9 was not
built** (Amendment 1 Q6: `lattice/spec/check.py` stays frozen). 12 new unit tests of K8 are in `tests/gocheck/test_go805.py`; two existing
tests carry declared edits (below).

## Gated rows that FAIL (stated first)
| row | registered | observed |
|---|---|---|
| P2-805A | S2 404 (3 linked raises); S3 404 (6); Streak `unclear` or none; pass = exact for S2, S3 | status exact (S2 404, S3 404); **linked raises 2 (S2) and 4 (S3)**, not 3 and 6. Registered pass rule "exact" read as status and count: **FAIL** |

That is the only gated Tier 2 row that misses. The cause is confirmed from the code, not guessed: the registered 3 and 6 are the
`http.StatusNotFound` sites of the two apps (S2 `api/api.go` lines 114, 166, 183; S3 lines 149, 211, 228, 253, 283, 293). K8 links only a
refusal that guards a lookup comparing an owned table's owner column, and one site of S2 (line 114, `models.Config`) and two of S3 (line 149,
`models.Queue`; line 293, `models.User`) guard lookups by primary key on tables with no owner column. They are not owner-scoped, so
they are not linked, and the count is 2 and 4. With every column accepted instead of only the owner column the count is the same (2 and 4),
so the owner-column filter did not cause the miss. This is an error of the prediction (it counted refusal sites, not owner-scoped lookups);
the answer (404) it was meant to protect is exact.

## Rows by id: predicted against observed
| row | predicted | observed | result |
|---|---|---|---|
| P2-805A | S2 404 (3); S3 404 (6); Streak `unclear` or none | **S2 404 with 2 linked** (`GET /runs/:rid` line 166, `POST /runs/:rid/cancel` line 183); **S3 404 with 4 linked** (`GET /tickets/:tid` 211, `close` 228, `reopen` 253, `handoffs` 283); Streak: owners `NotificationPrefs`, `Device`, `RefreshToken`, `Habit` (`user_id`), **0 linked, no answer**, note "no owner-scoped lookup refuses with a constant 4xx: GO805 silent"; also reported: S1 and `goparity3` no owned table, 0 linked; job-scheduler (`Membership.user_id`, `JobConfig.created_by`) 0 linked, no answer | status exact; counts miss (above); Streak as predicted (none); **FAIL as registered** |
| P2-805B | `GO805\|GET /tickets/:tid\|not_owned` | answer 404; exactly one GO805, `GO805\|GET /tickets/:tid\|not_owned` at `api/api.go:211`, message "GET /tickets/:tid answers another user's Ticket with 403; this app answers it with 404"; no other code | exact, pass |
| P2-805C | 0 GO805 | 0 GO805 on S1, S2, S3, Streak and job-scheduler under the answer derived from each (S1, Streak and job-scheduler have no answer, so GO805 is silent by design; S2 and S3 answer 404 and their own routes agree). Other layer-A findings with the baseline emptied: S2 `GO802\|POST /login\|User.token`, job-scheduler `GO801\|GET /applications/:id/jobs/:job_id/runs\|JobConfig` (both outside GO805; the originals' baselines hold them) | pass |
| P2-805D | GO805 out of the gated list; GO804 and GO806 text byte-identical | `GO_GATED` was `GO804, GO805, GO806` at 978b468 and is `GO804, GO806`; GO805 is in `GO_CODES`; the GO804 and GO806 texts are identical; `gated_note()` lost only the GO805 clause | exact, pass |
| P2-DEF | K9 (if Q6 yes) | **not built (Q6)**; `lattice/spec/check.py` sha256 `1c1e84e2...ef887691` equals 978b468's and the prereg's; the default `lattice check` on Go is still `GoPolicy.from_spec` | reported, not gated |
| P2-J0 | 100% | J0': 20 rows, every Python and Go cell cites an existing `file:line` and a test or results row, 0 problems; Go grades **15 S / 4 P / 1 A**, Python 15 / 3 / 2 | pass (V's independent re-run on the merged commit is still to come) |

## What K8 refuted, and what it cost
1. **The link cannot live in the two registered files alone.** The registration put the opt-in link in `goextract_owner.go` and
   `goextract_raise.go`. The `xOwner` and `xRaise` types are in the frozen `goextract_closure.go`, and the raises of helpers are resolved
   after the owner scan, so the raise cannot be stored on `xOwner`. The link rides on a side table (`ownerLinks`, a map from the
   `xOwner` value to a closure that is read at marshal time) and a `MarshalJSON` on `xOwner` that adds `raise` only to the policy extract.
   It works, and the default extract stays byte-identical, but it is a workaround for a frozen type, not the clean field the prereg pictured.
2. **Go budget overrun.** Section 7 allows 50 lines for `goextract_owner.go` plus `goextract_raise.go`; B2 wrote **87** (4 + 83). The other
   budgets hold: `gocheck/__init__.py` 26 of 40, `notowned.py` 132 of 140. The overrun is disclosed here, in B2's commit message and
   in the report; it was not hidden behind a re-split.
3. **The K8 answer reaches layer A only through a caller that passes `not_owned`.** With K9 not built, `lattice check` on Go builds
   `GoPolicy.from_spec` (no `not_owned`), so GO805 is silent in the default check. `run_gocheck` reports it only when the policy it is given
   carries the answer (`with_not_owned(policy, original)`), as the probes and `tests/gocheck/test_go805.py` do. This is the gap J0' row 4
   names (`P`, not `S`).
4. **Limits stated in the code and the J0' cell.** GO805 reads a refusal in the handler itself (not in a helper, not an error value
   returned as Streak does), the not-owned half only (a Gin app declares no admin dependency), and the status only.

## Byte compatibility (B2's scratch checks; the runner's PB-X and PB-G repeat them)
* The **default** extract is byte-identical on every Go module of the 19 fixture and app directories checked; of the 38 default and policy
  extracts, 33 are identical and the **policy** extract of five differs from 978b468's only by `owner_uses[].raise`: the modules (`clean2`, `goextract`, `goextract_workflow`, `goginapp`,
  `gotickets`). Layer A's findings on 50 module-policy pairs (every fixture, the three apps, the implementation app, empty and derived
  policy) are identical to 978b468's.
* **Two declared test edits**, made by the orchestrator, are the only edits to existing tests from Tier 2: `tests/gocheck/test_go_derive.py`
  (the policy extract may add `owner_uses[].raise`, round 3) and `tests/gocheck/test_gocheck_rules.py` (`set(GO_GATED) == {"GO804", "GO806"}`).
  Tier 1 made a third (`tests/importer/test_go_rules2.py`, `body_model`, Amendment 2 ruling 2). The runner's PB-F lists these three by path
  and sha256 (`DECLARED_TEST_EDITS`) and fails on any other test change.
* `docs/lattice-language-rules.md` gains one row (the target-gate entry for K8), as the file's own rule asks.

## Mutants of K8 (LM16, LM17) and the missing registered killer
Section 6.1 registers LM16 (K8 pairs a refusal to its comparison by line proximity) with the killer "P2-805A (goparity3 decoy)". **That
probe cannot kill it**: `goparity3` has no owned table, so K8 derives nothing there (0 linked, no answer), P2-805A has no decoy to
mis-pair, and P2-805A fails on the unmutated control, so no mutant could flip it. This is a registration error, found by B2 and
confirmed here. What was done: LM16 and LM17 are wired in `dogfood/go_parity_mutants3.py` from `dogfood/g3_standin/k8_mutants.json`
(read at import, a freeze entry), each with a named unit test as its killer, which the prereg's section 6 allows ("or a named unit test
flips"): LM16 by `tests/gocheck/test_go805.py::test_a_refusal_near_a_comparison_is_not_linked`, LM17 (registered with a unit test) by
`::test_disagreeing_raises_are_unclear`. Run through the mutants runner (control first, scratch ledger): both are killed, each by its named
test flipping from pass to fail; the whole library set LM1-LM18 is killed 18/18 and RM1 and RM2 are caught (20 of 20; Tier 1 had 16 of 18
because LM16 and LM17 were NOT RUN without K8). One control note: M-SM1 does not pass on the control (a registered Tier 1 failure), so it
cannot serve as a detector.

## J0' (K10)
The matrix re-grades 20 capabilities for Python, Go and .NET. **Go: 15 supported, 4 partial, 1 absent** (J0: 13 / 6 / 1); Python is
unchanged at 15 / 3 / 2; .NET is absent in all 20 rows by decision. Rows 6 (layer B) and 10 (layer R) move from `P` to `S` on the round 1
and 2 measurements (a Go app over SQLite), not on anything G3 built. Rows 3 (import) and 4 (layer A) stay `P` with their gap named: row 3
has no lifting and no verified rules on server dialects; row 4 has GO805 but not a default policy (K9). The remaining gaps are ranked in
its section 4. Its cells were resolved by script from the tree at the build state (a worktree with B1 and the uncommitted K8), so a line
may have moved on the merged commit: a moved line is a mechanical edit, a vanished symbol is a finding (section 6 of the document).

## Runner rows (completion of the runner, node G3-R)
Arm `2` is in `dogfood/go_parity_probes3.py` with the six rows above (P2-805A, B, C, D, J0 gated; P2-DEF reported). The prerequisites are
the Streak and job-scheduler copies for P2-805A and P2-805C and Go for all; nothing is hard-coded NOT RUN. Self-test, all arms on the
stand-ins: see the report that accompanies this file.

## NOT RUN
M-R1 (Docker, by instruction); the real Arm H (S4 does not exist yet); the scored run and Amendment 3; V's independent re-run of P2-J0
on the merged commit.
