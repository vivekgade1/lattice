# Perf budgets: "a constant may rise when the per-item term falls" - pre-registration

## 1. Status

Date: 2026-10-13. Branch `np-perfrule` (worktree `lattice-perfrule`, from `next-phases` 7358be2).
Written BEFORE any code of this change exists. Nothing below about the new rule was run. The only
commands run by this document's author: `pytest tests/perf --collect-only` (baseline: 61 tests) and a
10-line pure-arithmetic enumeration of the J2 sets (no `lattice` import) to check the hand count in
J2. Results go in `docs/results/2026-10-13-perf-rule-results.md`, raw data in
`docs/results/data/2026-10-13-perf-rule/`. No LLM, no API key, no Streak run: cost 0 tokens.

## 2. Decisions

The owner's decision (HANDOFF-2026-10-13 section 7) is fixed: a budget's constant may rise when its
per-item term falls. Budgets stay in `perf.budget.json`; the file format and the spec language do not
change. Notation: budget (c, p); fit (c', p') = the line through the smallest and largest measured size,
each at its largest count (`fit`, unchanged); `allowed(n) = c + p*n`; `within` = for every size with
statements, `max(statements) <= allowed(n)`.

1. **The rule.** `trade := c' > c and p' < p and within`. The budget passes iff
   `p' <= p` and `within` and (`c' <= c` or `trade`) and the latency check passes (unchanged).
   In code: the failure "constant c' > budget c" is recorded only when `not trade`; every other check
   (`per_item`, each size's count, latency) is untouched and still runs in the same order.
2. **Monotonic.** The new pass set is the old one plus the trade cells: a trade needs `c' > c`, which
   failed before. For every input with `c' <= c` or `p' >= p` the report is byte-identical to today's
   (verdict, notes, delta, everything). Nothing that passed can fail.
3. **A consequence the builder must know (decided, not a change).** With >= 2 measured sizes the fit
   goes through (n_min, max count) and (n_max, max count). If `within` holds at n_min and `c' > c`,
   then `p'*n_min < p*n_min`, so n_min > 0 and `p' < p`. Hence `c' > c and within` already implies
   `p' < p`, and the verdict rule is equivalent to `p' <= p and within and latency`. The `p' < p`
   conjunct of `trade` is kept in the code because it states the owner's rule, but mutants of it alone
   are equivalent (PE1, PE2 in section 4). Likewise the "constant" failure line never decides a verdict
   on its own any more: whenever it is recorded, some size is over too. It stays as an explanation.
4. **p' == p with c' > c: fail**, exactly as today ("over budget: constant ..." plus each size's line,
   since every size is then over). No trade note, no "no trade" note.
5. **p' < p but a measured size over its allowed count: fail**, with today's failure lines (constant and
   each size over) plus one new note, appended before the "better" note:
   `no trade: per_item {p'} < budget {p}, but a measured size is over its allowed count, so constant {c'} > budget {c} stays over budget`.
6. **The trade note** (replaces today's "every measured size is within its allowed count: ... (a trade
   the budget's rule counts as over budget)", which is deleted and can no longer occur), appended where
   that note is today (after the failures and latency notes, before "better than the budget"):
   `constant traded for per-item: constant {c'} > budget {c} accepted (per_item {p'} < budget {p}, every measured size within its allowed count); at or under the budget for N >= N* = {N*}, over it for N < {N*}`
   with `N* = (c' - c) / (p - p')` as a `Fraction` (p - p' > 0 in a trade), every number through
   `_show` (an integer prints as an integer, else 4 significant digits). By decision 3,
   `0 < N* <= n_min`. Test substrings: `constant traded for per-item`, `constant 7 > budget 6`, `N* = 1`.
7. **Latency** is judged as today and independently: a trade with a latency failure is `fail` on
   latency only; the constant is not listed as over and the trade note is present.
8. **Non-exact fits** may trade: `within` checks every measured size at its largest count, so a middle
   size off the line or a run that differs blocks the trade when it exceeds its allowed count. The
   "fit not exact" note is unchanged and comes first.
9. **per_item budget 0.** A trade then needs `p' < 0` (a count that falls as N grows); allowed, with
   N* shown (e.g. budget 8 + 0N, counts 8 / 4 at 5 / 25: fit 9 - 0.2N, N* = 5, pass).
10. **Fraction arithmetic** throughout (`c`, `p` from `_frac`, the fit, `allowed`, N*); no float before
    `_show`. `c' == c` with `p' < p` is a plain pass (no trade note).
11. **Sizes with no statements** neither block nor support a trade (same as the per-size check);
    fewer than two measured sizes stays `unmeasured` and no trade is judged.
12. **"better than the budget"** is unchanged (in the S2 case it still says `per_item 0 < 1`).
    **`delta`** is unchanged (fit minus budget): a trade shows `{constant: +, per_item: -}` with
    verdict `pass`; this is the only way a positive delta term can pass.
13. **Schema and exit codes.** `Report.to_dict()` keys, nested keys and order unchanged; no new key
    (N* lives only in the note). `lattice perf check` exit 0 when every budget passes (a trade budget
    passes), 1 when one fails, 2 unmeasured/cannot run: the mapping in `__main__.py` is untouched.
14. **Docs that change:** the module docstring sentence ":func:`judge` fails the budget when ..."
    (budget.py lines 22-25), `Report.delta`'s docstring, and `docs/lattice-reference.md` line 198
    ("`check` fails a budget when the fitted constant **or** per-item exceeds ..."). The spike 1a
    documents are history and are not edited. `__main__.py` text is unchanged.
15. **dogfood/perf_mutants.py**: PM1-PM4's old texts must still occur exactly once in the new code
    (its `apply` refuses otherwise); PR1-PR9 (section 4) are added to its `MUTANTS`.

## 3. Probes (all in `dogfood/perf_rule_probes.py`, except J3)

"Old rule" = `lattice/perf/budget.py` at 7358be2, extracted with `git archive 7358be2 lattice` into a
scratch dir and loaded standalone by `importlib` as `budget_old` (it imports only the stdlib); the
old CLI runs from that copy via `PYTHONPATH`. Every input uses 10 runs per size, latency `[1.0]*runs`
unless stated, no latency budget unless stated. The script runs every probe 10 times and prints a
sha256 of its canonical JSON output per run (J4's measured latencies excluded): **10 identical hashes**
is part of every probe's pass rule.

**J1: replay of the recorded spike-1a numbers.** Source: the tables of
`docs/results/2026-10-12-spike1a-results.md` (C0 and P1, lines ~40-60; P2 line ~83). The raw JSONs are
lost (that document's closing disclosure; `docs/HANDOFF-2026-10-12.md` line 70, untracked in the
main checkout), so the tables are the record.

| id | counts N=5 / N=25 | budget | old (recorded) | new (predicted) | new notes, in order | delta |
|---|---|---|---|---|---|---|
| J1a | 7 / 7 (S2; also the P2 fix) | 6 + 1N | fail | **pass** | trade note with `constant 7 > budget 6`, `per_item 0 < budget 1`, `N* = 1`; `better than the budget: per_item 0 < 1` | {1, -1} |
| J1b | 11 / 31 (clean home) | 6 + 1N | pass | pass | none | {0, 0} |
| J1c | 7 / 7 (S2) | 8 + 0N | pass | pass | `better than the budget: constant 7 < 8` | {-1, 0} |
| J1d | 11 / 31 (clean home) | 8 + 0N | fail | fail | over: `per_item 1 > budget 0`, `N=5: 11 statements > allowed 8`, `N=25: 31 statements > allowed 8`; `better ...: constant 6 < 8` | {-2, 1} |
| J1e | 9 / 29 (S1 habits) | 4 + 0N | fail | fail | over: `per_item 1 > budget 0`, `N=5: 9 ... > allowed 4`, `N=25: 29 ... > allowed 4` | {0, 1} |
| J1f | 4 / 4 (clean habits) | 4 + 0N | pass | pass | none | {0, 0} |

Old-rule J1a notes must be exactly the recorded three: `over budget: constant 7 > budget 6`, the old
trade note, `better than the budget: per_item 0 < 1`. **Pass:** old verdicts = recorded 6/6; new
verdicts and full note lists = the table 6/6; exactly 1 verdict differs (J1a); in J1b-J1f
`to_dict()` old == new byte for byte.

**J2: exhaustive grid.** Budgets c in 0..8, p in 0..4 (45); exact fits c' in 0..12, p' in 0..5 (78),
counts `c' + p'*5` and `c' + p'*25` at sizes 5 and 25: 3510 cells.
- (a) no cell passes old and fails new: **0 cells**.
- (b) newly passing set == {c' > c, p' < p, c' + 5p' <= c + 5p, c' + 25p' <= c + 25p}, compared as sets.
- (c) counts, predicted analytically. Old pass = (c' <= c)(p' <= p): sum_c (c+1) = 45 times
  sum_p (p+1) = 15, so **675**. New-only: let d = c' - c >= 1, e = p - p' >= 1. With e >= 1,
  d <= 5e implies d <= 25e, so the condition is 1 <= d <= 5e (the size 5 binds) and d <= 12 - c.
  Per (c, e) that is min(12 - c, 5e) cells; (p, p') pairs with a given e: 5 - e. Sums over c = 0..8
  (12 - c = 12, 11, ..., 4): e=1: 8x5 + 4 = 44; e=2: 10+10+10+9+8+7+6+5+4 = 69; e=3 and e=4: 72.
  New-only = 4x44 + 3x69 + 2x72 + 1x72 = 176 + 207 + 144 + 72 = **599**. New pass = **1274**
  (old fail 2835 -> new fail 2236). "No trade" cells (c' > c, p' < p, d > 5e): 720 - 599 = **121**
  (e=1: 4 x 28 = 112; e=2: 3 x 3 = 9), each over at N=5 only (d > 25e never holds for d <= 12).
- (d) `to_dict()` old vs new: identical in 3510 - 720 = **2790** cells; in the 599 verdict and notes
  differ; in the 121 only notes differ (the added "no trade" note). The old trade note appears in
  exactly the 599 under the old rule and in **0** cells under the new one. Every one of the 599
  carries `N* = d/e`, with 0 < N* <= 5 (max 5 where d = 5e).
- (e) PE1 and PE2 (section 4) applied to a scratch copy: output identical to the new rule in 3510/3510.
**Pass:** all five hold, numbers exact.

**J3: the existing suite.** Baseline `tests/perf` = 61 tests (collected at 7358be2). Exactly one
changes: `test_trading_constant_for_per_item_fails_on_the_constant_and_says_so` becomes
`test_trading_constant_for_per_item_passes_and_shows_the_crossover` (budget 6 + 1N, counts 7x10 /
7x10: verdict `pass`, delta {1, -1}, notes == [trade note containing `constant 7 > budget 6` and
`N* = 1`, `better than the budget: per_item 0 < 1`], no note starting `over budget`). The other 60
are source-identical (compared per function with `ast` against 7358be2). New tests, named now:
`test_no_trade_when_a_size_is_over_its_allowed_count` (6 + 1N, 12 / 12: fail, `over budget: constant
12 > budget 6`, `over budget: N=5: 12 statements > allowed 11`, `no trade:`, no trade note),
`test_constant_over_with_equal_per_item_still_fails` (6 + 1N, 12 / 32: fail, `constant 7 > budget
6`, neither trade note), `test_reverse_trade_is_refused` (30 + 0N, 5 / 25: fail on `per_item 1 >
budget 0`), `test_trade_shows_a_fractional_crossover` (4 + 3N, 12 / 32: fit 7 + 1N, pass, `N* =
1.5`), `test_trade_at_the_boundary_size_passes` (6 + 1N, 11 / 11: pass, `N* = 5`),
`test_trade_does_not_excuse_latency` (6 + 1N with latency {50, 25, 0.5}, 7 / 7, latencies 100, 120,
110: fail, `over budget: latency: median 110.00 ms`, trade note, no `over budget: constant`),
`test_falling_count_trades_against_a_zero_per_item_budget` (8 + 0N, 8 / 4: pass, `N* = 5`).
**Pass:** `pytest tests/perf` = **68 passed**, 0 failed; 1 rewritten, 60 unchanged, 7 new.

**J4: schema and exit codes.** (i) `list(to_dict())` == `[budget_id, endpoint, sizes, fit, budget,
verdict, delta, notes]` and the recursive key paths equal old vs new for a pass (J1b), a fail (J1d),
an unmeasured report (one size empty) and the trade (J1a): **4/4**. (ii) CLI on a scratch project
built like `tests/perf/test_measure._project` (in-process `perfapp`, SQLite): budget `GET /items`
1 + 1N, sizes [2, 6], `check --json --runs 2`; counts 2 / 2 (as `test_constant_endpoint_counts_exactly`),
fit 2 + 0N, N* = 1. Predicted: new **exit 0**, `verdict: pass`, trade note with `N* = 1`; old
**exit 1**; top-level keys `[version, command, project, budget_file, verdict, reports]` identical.
**Pass:** (i) 4/4 and (ii) both exit codes and the key lists as predicted.

**J5: neighbouring behaviour.** Predicted verdicts (old -> new):

| id | budget | counts per size | old -> new | what is checked |
|---|---|---|---|---|
| J5a | 4 + 0N | 5:[4], 10:[12], 25:[4] | fail -> fail | `N=10: 12 statements > allowed 4`; to_dict identical |
| J5b | 6 + 1N | 5:[7], 15:[30], 25:[7] | fail -> fail | not exact; N=15 30 > 21 blocks the trade; old notes + `no trade:` |
| J5c | the 3 cases of `test_latency_compared_only_when_set` | 11 / 31 | pass, fail, pass (same) | to_dict identical |
| J5d | 6 + 1N, latency {50, 25, 0.5} | 7x3 / 7x3, lat 100, 120, 110 | fail -> fail | latency fails alone; trade note; no constant line |
| J5e | 4 + 0N | 9 / 29 | fail -> fail | `per_item 1 > budget 0`; to_dict identical |
| J5f | 30 + 0N | 5 / 25 | fail -> fail | reverse trade refused; to_dict identical |
| J5g | 6 + 1N | 12 / 32 | fail -> fail | p' == p, constant 7 > 6; to_dict identical |
| J5h | 8 + 0N | 8 / 4 | fail -> pass | p' = -0.2, N* = 5 |
| J5i | 6 + 1N | 5:[7,7,8], 25:[7,8,8] | fail -> pass | not exact, fit 8 + 0N, N* = 2, "fit not exact" note first |

**Pass:** 11/11 verdicts as predicted and every "identical" claim byte-exact.

## 4. Mutation plan (added to `dogfood/perf_mutants.py` as PR1-PR9; pytest only)

Prediction for the whole run: control PASS (68 passed); PM1-PM4 still caught; PR1-PR9 caught.

| id | one-line mutant of the new rule | predicted killers |
|---|---|---|
| PR1 | `trade` drops the `within` conjunct | `test_no_trade_when_a_size_is_over_its_allowed_count` (verdict still fail by decision 3: notes only) |
| PR2 | the constant failure is never recorded | `test_constant_over_budget_fails`, `test_no_trade_...`, `test_constant_over_with_equal_per_item_still_fails` (notes only) |
| PR3 | the per_item check never fires (p' > p allowed) | `test_reverse_trade_is_refused` (verdict), `test_per_item_over_budget_fails` (note) |
| PR4 | the trade note is not appended | the rewritten trade test, `..._fractional_crossover`, `..._boundary_size_passes`, `..._does_not_excuse_latency`, `..._falling_count_...` |
| PR5 | the constant failure ignores `trade` (verdict computed as before the trade) | the rewritten trade test, `..._fractional_...`, `..._boundary_...`, `..._falling_count_...`, `..._does_not_excuse_latency` |
| PR6 | N* denominator `p` instead of `p - p'` | `..._fractional_crossover` (1 not 1.5), `..._falling_count_...` (ZeroDivisionError) |
| PR7 | `within` in `trade` uses `<` instead of `<=` | `..._boundary_size_passes`, `..._falling_count_...` (8 == allowed 8) |
| PR8 | `if failures:` -> `if failures and not trade:` (trade excuses latency) | `test_trade_does_not_excuse_latency` |
| PR9 | the "no trade" note is not appended | `test_no_trade_when_a_size_is_over_its_allowed_count` |

**Equivalent, predicted to survive (not in `MUTANTS`, whose exit rule needs every mutant caught):**
PE1 `p' < p` -> `p' <= p` in `trade`; PE2 the `p' < p` conjunct removed. Proof: decision 3. Checked
by J2(e) instead. **Pass:** runner exit 0, 13/13 caught, the named killers among each mutant's
failures (extra failures are reported, not a miss).

## 5. Named risks

1. **A regression below N* is hidden.** Exactly for N in [0, N*) the request runs more statements
   than the budget allows (S2: N = 0 runs 7 vs 6; N = 1 equal; N >= 2 fewer). N* <= the smallest
   measured size (decision 3), so the hidden range is always below the sizes judged. Mitigation: the
   note prints N*; a budget whose small-N case matters must list a small size.
2. **Two sizes.** The constant no longer decides a verdict (decision 3), so the measured sizes carry
   the whole judgement; a count that is flat on 5..25 and grows beyond is invisible (as today).
3. **J1 rests on a document.** The spike-1a raw JSONs are lost; J1 replays the recorded tables
   (S2's 7 / 7 is the agent's report, not re-measured by the orchestrator; C0's 11 / 31 was). No
   Streak run is part of this change.
4. **The builder reads the predictions.** Mitigation: J2's counts are arithmetic fixed here; the old
   column comes from 7358be2's code, not from the builder; test names and note substrings are fixed
   here; any test or note text changed after this document is listed post hoc.
5. **Falling counts trade (decision 9).** A count that falls with N often means a cache; it passes a
   zero-per-item budget. Disclosed; the "fit not exact" note appears when runs differ.

## 6. Blind vs post hoc, and verification

Everything above is fixed before code. A missed prediction is reported as a finding with its output,
never fixed silently; the blind numbers stay as first measured. Anything learned while building
(note wording, extra tests, timings) goes in a "Post-hoc observations" section of the results.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-perfrule PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-perfrule
uv run pytest tests/perf -q                                                 # J3: 68 passed
uv run python dogfood/perf_rule_probes.py --runs 10 \
  --out docs/results/data/2026-10-13-perf-rule                              # J1 J2 J4 J5, 10 equal hashes
uv run python dogfood/perf_mutants.py                                       # control + PM1-PM4 + PR1-PR9 caught
uv run pytest tests/test_cli.py -q                                          # dispatch unchanged
uv run ruff check lattice/perf tests/perf dogfood/perf_rule_probes.py dogfood/perf_mutants.py
uv run ruff format --check lattice/perf tests/perf dogfood/perf_rule_probes.py
```

**Pass for the change:** J1-J5 as stated, 13/13 mutants caught, ruff clean, and no file outside
`lattice/perf/budget.py`, `tests/perf/test_budget.py`, `dogfood/perf_mutants.py`,
`dogfood/perf_rule_probes.py`, `docs/lattice-reference.md` and the results files changed.
