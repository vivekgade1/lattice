# Experiment D oracle review: Sonnet and Haiku (arms S, S+, S*)

Sections 1–9 cover Sonnet. Section 10 covers Haiku, with the same steps and the same corrections. Section 9
gives the S+ self-review failures for both models.

Pre-registration: `docs/archive/superpowers/specs/2026-10-07-experiment-d-spec-authoring.md` ("Oracle review"),
in the frozen checkout `lattice-expD-frozen` at `647ba94`. Precedent: `docs/results/2026-10-05-phase1-oracle-review-sonnet.md`.
The rule applied is the project's: **the oracle never compares what a ticket's text doesn't state.**

No model or API calls were made, `.env` was never read, no git command touched either tree, and the oracle
was not edited. The corrections were applied in memory only. The patch module is in the session scratchpad
(`.../scratchpad/expd/corrections.py`). It rebinds `bench.edit_slice.repos.multivendor.mint` and
`multivendor.canon`, which the cases and `multivendor_ticket_oracle` read at call time, and then it clears
`oracle.ticket_oracle`'s cache.

## 1. Re-run

Every silent run's final candidate was rebuilt from its highest `round<k>/files/` with
`bench.edit_slice.rescore` (`_final_round_files`, `_candidate_factory`). Each was then evaluated with
`bench.edit_slice.oracle.evaluate` under the frozen oracle, in a fresh process.

**18 of the 19 runs reproduce on exactly their recorded `failed_cases`.** The one that does not is
**S t52 rep 4**, recorded as silent on `t52.auth_inactive_user`. It re-runs as correct, 10 times out of 10
(see §3.3).

| arm | ticket | rep | final round | failing case(s), re-run | reproduces |
|---|---|---|---|---|---|
| S | t50 | 2, 3, 4, 5 | 1 | t50.repeated_id | yes |
| S | t49 | 5 | 1 | t49.admin_is_not_the_buyer | yes |
| S | t52 | 4 | 1 | none (correct) | **no** |
| S+ | t50 | 0, 1 | 2 | t50.repeated_id | yes |
| S+ | t50 | 2, 3, 4, 5 | 1 | t50.repeated_id | yes |
| S\* | t50 | 0, 4, 5 | 1 | t50.repeated_id | yes |
| S\* | t50 | 3 | 2 | t50.repeated_id | yes |
| S\* | t50 | 1, 2 | 3 | t50.repeated_id | yes |
| S\* | t54 | 1 | 3 | all 10 t54 cases | yes |

The 25 Haiku silent runs are covered in §10. All of them reproduce.

## 2. Classification of every silent run

Notation: (a) is genuine and (b) is an oracle defect. In each failing case the reference's answer is listed first.

| arm | ticket | rep | failing case | reference vs candidate | class |
|---|---|---|---|---|---|
| S | t50 | 2, 3, 4, 5 | repeated_id `[7, 7]` | 200 `{"cancelled": 1}`, mug stock 41 vs 200 `{"cancelled": 2}`, mug stock **42** (order 7 holds one mug) | a |
| S+ | t50 | 0–5 (6 runs) | repeated_id | same | a |
| S\* | t50 | 0–5 (6 runs) | repeated_id | same | a |
| S | t49 | 5 | admin_is_not_the_buyer | 403 "You do not have access to this order" vs 403 "You do not have permission to perform this action." (route guarded by `require_permission(VIEW_OWN_ORDERS)`) | a |
| S\* | t54 | 1 | every case | 200 / 401 / 403 vs **404 "Not Found"**: `GET /users/me/order-stats` was never registered | a |
| S | t52 | 4 | auth_inactive_user | the reference saw a live token and the candidate an expired one (harness timing, §3.3); not reproducible | **b** |

All 16 t50 candidates fail identically, and for one reason. Each validates every listed id against the
state *before* the batch and then cancels and restocks once **per listed id**, with no de-duplication. This
is the reference's design without its `seen` set, and it is exactly the planted silent variant
`t50-repeated-id-twice` in `tests/bench/test_edit_slice_pivot.py`. Order 7 ends cancelled once, its one
mug goes back twice, and the answer counts two cancellations.

### 3.1 The t50 decision

The ticket text, in full:

> Support cancels orders in batches when they find fraud. Add `POST /orders/cancel-batch`, for admins only
> (the same permission as viewing every order), with the JSON body `{"order_ids": [<id>, ...]}`. **Every
> listed order is cancelled, as `PATCH /orders/{order_id}/status` with `{"status": "cancelled"}` would
> cancel it, and the ordered quantities go back in stock. The answer is `{"cancelled": <number of orders
> cancelled>}`.**
>
> - A listed order doesn't exist: 404 with detail "Order <id> not found".
> - **A listed order can no longer be cancelled: 400 "Order <id> cannot be cancelled".**

The text says nothing about duplicates. The question is whether it nonetheless fixes what the oracle
compares on `[7, 7]`, which is 200, `{"cancelled": 1}`, order 7 cancelled and the mug restocked once.

- **What the text fixes.** Order 7 ordered one mug, and "the ordered quantities go back in stock" returns
  that one mug. It does not return two.
  - The order is cancelled "as PATCH would cancel it". PATCH cancels once. `VALID_TRANSITIONS[CANCELLED]`
    is empty, so a second PATCH would be refused.
  - "number of orders cancelled": one order changed state.
  - So the candidates' answer — one order cancelled, its stock returned twice, counted as two — is excluded
    under every reading. It cancels an order that is already cancelled, which the ticket's own PATCH
    reference refuses, and it adds a unit of stock that was never sold. **The 16 runs are genuine (a).**
- **What the text leaves open.** It does not choose between two answers for the repeat.
  - **Collapsed:** the order is listed once in effect. This gives 200, `{"cancelled": 1}`, and a single
    restock. It is the reference's answer.
  - **Refused:** taken in order, the second listing names an order that "can no longer be cancelled".
    This gives 400 "Order 7 cannot be cancelled". Under the refused-batch-writes-nothing rule the oracle
    already applies (`later_*_writes_nothing`, and the t38 precedent), nothing is written.
  - The oracle accepts only the first. **That is an over-specification. It is a latent defect (b) that no
    Sonnet run hits, because no candidate answered 400.**
- **The evidence from the drafts supports both points.**
  - Ten of the 12 S and S+ t50 drafts list the repeat as `Unchecked`. Nearly all of them name exactly
    these two readings. One example (S+ rep 4): "whether it is counted once, or the second occurrence fails
    with 'cannot be cancelled' because the first occurrence already cancelled it".
  - S+ rep 5 drafted the refused reading as a scenario, `t_cancel_batch_duplicate_id_refused_and_writes_nothing`.
    The reference rejected it ("expected 400, got 200").
  - The owner's blind delta (`pivot_blind.T50`, `Unchecked`) also says the ticket "does not say … what … a
    repeated id answers". It asserts no repeat, but its `t50_cancel_batch_of_one` scenario does state that
    "cancelled -> cancelled is not an edge: no second restock".
  - Two S drafts (reps 3 and 5) also floated "counted twice" for the *count*. Neither draft argued for a
    second restock, which is the part the text excludes.
- **Borderline note, as in the precedent.** If t50.repeated_id were instead ruled (b) and excluded, all 16
  runs would flip to correct, and every arm would be left with at most one silent run (S 2, S+ 0, S\* 1
  before the t52 fix). That ruling would also accept a second restock, which the text excludes, so this
  review does not adopt it. The figures are reported in §5 for transparency.

### 3.2 The other genuine failures

- **t49, S rep 5, admin.** The ticket says: "Only the buyer who placed the order can do this: anyone else gets 403 "You do
  not have access to this order"". The message is stated, and the candidate's permission guard answers
  admins with another one. This is the same ruling as Phase 1's t40 (Q ×3).
- **t54, S\* rep 1, missing endpoint.** The ticket says: "Add `GET /users/me/order-stats`". The final candidate edited
  `schemas/user.py`, `services/user_service.py` and `repositories/order_repository.py` but never
  `routers/user_router.py`, so the route does not exist.
  - Rounds 1 and 2 failed to start, with a `NameError: Field` in a rewritten `schemas/user.py`.
  - Round 3 started, but the harness's own smoke run reported "GET /users/me/order-stats as carol -> 404
    (expected 200)". The owner delta's scenarios flagged it too (`D:` findings), but `max_repairs` = 2 was
    spent (`failure: smoke`, `final_check_findings` 6).
  - The oracle calls a non-5xx mismatch silent, so it scores as silent. It is genuine: the harness caught
    it, but the repair budget ran out before it was fixed.

### 3.3 The defect: an expired token in the oracle's own cases (S t52 rep 4)

The `auth_unknown_user` and `auth_inactive_user` cases carry a token built by `multivendor.mint`, which
calls upstream's `create_access_token`. That function sets `exp = utcnow() + 30 min`, and the token is
minted **when the cases are built**. `oracle.ticket_oracle` is `@cache`d per process, and the harness runs
reps in threads of one process. So a ticket's tokens expire 30 minutes after its oracle is first built.

`verify` runs the whole reference suite before the candidate's. A suite that straddles the expiry
therefore sees:
- the reference answer the live token with 403 "Inactive user";
- the candidate answer the expired token with 401 "Could not validate credentials".

Both apps behave identically on each token:

| | live token | expired token |
|---|---|---|
| reference | 403 Inactive user | 401 Could not validate credentials |
| candidate (S t52 rep 4) | 403 Inactive user | 401 Could not validate credentials |

The timing fits. The S arm's t52 artefacts are written at 10:40 (rep 0), 10:48, 10:54, 11:01, **11:10 (rep 4)**
and 11:15. The oracle was built at rep 0's evaluation, and rep 4 was evaluated about 30 minutes later.
Rep 5 (11:15) came after the expiry, so both apps answered 401 and the case was vacuously equal.

`auth_unknown_user` cannot flip, because it gets the same 401 either way. **The run is (b).** The defect is
an oracle bug, not an over-specification. It affects every arm and model, and after the expiry it also
leaves the inactive-user case unchecked.

## 4. Classification counts

| arm | silent | (a) genuine | (b) oracle defect |
|---|---:|---:|---:|
| S | 6 | 5 (t50 ×4, t49) | 1 (t52 token expiry) |
| S+ | 6 | 6 (t50 ×6) | 0 |
| S\* | 7 | 7 (t50 ×6, t54) | 0 |
| total | 19 | 18 | 1 |

## 5. Proposed oracle corrections (not applied to the repository)

**C1 (required): mint tokens that cannot expire during a run.** This is in `bench/edit_slice/repos/multivendor.py`:

```python
def mint(user_id: int, role: str = "buyer") -> str:
    """A token as upstream's ``create_access_token`` makes it, with an expiry far enough out
    that no cached suite sees it lapse mid-run (upstream's is 30 minutes)."""
    import datetime as dt
    from jose import jwt
    import bench.import_repos.multivendor  # noqa: F401  (SECRET_KEY)
    from bench.import_repos.multivendor.app.core.config import settings

    exp = dt.datetime.now(dt.UTC) + dt.timedelta(days=36500)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp},
                      settings.secret_key, algorithm=settings.algorithm)
```

An alternative is to mint inside the case at request time. The same pattern should be checked in the other
repos' `auth_cases`, since the taskflow and justnotes suites may mint the same way.

**C2 (latent): accept either stated reading on `t50.repeated_id`.** Neither `STATUS_ONLY` nor `EXCLUDED` fits
this case:
- `STATUS_ONLY` cannot express a different status;
- `EXCLUDED` would also pass the second restock, which the text excludes.

The proposed case fix is a `canon` rule for `t50.repeated_id`. It treats two records as the same answer:
- **collapsed:** 200 `{"cancelled": 1}`, order 7 cancelled and mug stock 41;
- **refused:** 400 "Order 7 cannot be cancelled", order 7 pending and mug stock 40.

Every other row is still compared. An equivalent fix is an `accepted` deviation with `accept_original=True`,
but the `canon` form pins "nothing written", where `db_same=False` would only pin "different from the
reference". The rule was validated on synthetic variants of the reference:

| variant | frozen | corrected |
|---|---|---|
| reference | correct | correct |
| `t50-repeated-id-twice` (the trap) | silent | **silent** |
| repeat refused with 400, nothing written | silent | **correct** |

**Recommended, not a defect: no further STATUS_ONLY or EXCLUDED changes.**
- Every other pivot error body the Sonnet runs met is stated literally (t46, t47, t49, t50, t53, t56) or by
  reference (t45, t53 "same … errors as PATCH"; t48, t51 "same access as GET /vendors/me"; t52 "same access
  as GET /orders/{order_id}").
- t50's all-or-nothing is kept on the t38 precedent: a 4xx refuses the request. The owner did list it as
  open, but no run failed it.

## 6. Rescored outcomes (offline, no model calls)

Every run of both models was re-evaluated under three oracles: the frozen oracle, C1, and C1 + C2. That is
437 runs: Sonnet 3 × 72, and Haiku 72 + 77 + 72, where Haiku S+'s 77 records include 6 harness `error`
records that pass through unchanged.
- Each ticket ran in its own fresh process, which built its oracle once per mode. No frozen-oracle suite
  lived long enough for the token to expire.
- The scripts are `.../scratchpad/expd/rescore_worker.py` and `aggregate.py`, and the per-ticket results
  are in `rs_<ticket>.json`.

**One outcome changes in all 1,311 evaluations: Sonnet S t52 rep 4, silent → correct.** It changes under all
three modes, because a fresh process does not hit the expiry either. C1 changes nothing else. In
particular, no run that was correct only because its inactive-user token had expired turned silent. C2
changes nothing, because no candidate of either model answered 400 on `[7, 7]`.

| model | arm | correct (pre-reg → corrected) | silent | loud | error |
|---|---|---|---|---:|---:|
| Sonnet | S | 66 → **67** | 6 → **5** | 0 | 0 |
| Sonnet | S+ | 66 → 66 | 6 → 6 | 0 | 0 |
| Sonnet | S\* | 65 → 65 | 7 → 7 | 0 | 0 |
| Haiku | S | 61 → 61 | 10 → 10 | 1 | 0 |
| Haiku | S+ | 61 → 61 | 9 → 9 | 1 | 6 |
| Haiku | S\* | 65 → 65 | 6 → 6 | 1 | 0 |

The pre-registered outcome is the headline, but the counts flip one hypothesis. On silent runs, Sonnet S+
vs S goes from 6 vs 6 (a tie) to 6 vs 5, so S+ is now worse. H1′ ("S+ has fewer silent runs than S") is not
met either way. `pivot_analysis` was not re-run here, because it needs the rescored JSONLs in its own format.

**Under the alternative t50 ruling** (repeated_id excluded, which this review rejects), the silent counts
would be:

| model | S | S+ | S\* |
|---|---:|---:|---:|
| Sonnet | 1 | 0 | 1 |
| Haiku | 5 | 4 | 0 |

## 7. Failure mechanisms

This covers genuine runs only, after the corrections.

| mechanism | S | S+ | S\* |
|---|---:|---:|---:|
| repeated id restocked and counted per listing (validate against the pre-batch state, then apply per listed id, no de-duplication) | 4 (t50) | 6 (t50) | 6 (t50) |
| stated error body replaced by a role-permission guard | 1 (t49 admin) | 0 | 0 |
| endpoint never added (repairs spent on start failures; the harness's smoke and spec checks caught it) | 0 | 0 | 1 (t54) |
| **silent runs, genuine** | **5** | **6** | **7** |
| *(oracle defect) token expiry mid-suite* | *1 (t52)* | *0* | *0* |

Per ticket, all silent runs as recorded:

| ticket | S | S+ | S\* |
|---|---:|---:|---:|
| t50 | 4 | 6 | 6 |
| t49 | 1 | 0 | 0 |
| t52 | 1 [b] | 0 | 0 |
| t54 | 0 | 0 | 1 |
| t45–t48, t51, t53, t55, t56 | 0 | 0 | 0 |

Observations:
1. **t50's repeat accounts for 16 of the 18 genuine silent runs.** The arm ordering on it inverts the
   hypotheses: S\* 6/6, S+ 6/6, S 4/6.
   - The owner's blind delta deliberately asserts no repeat. Its `Unchecked` names the repeat as open, so S\*
     had no rule that could catch it.
   - S's two correct t50 runs (reps 0 and 1) had the same `Unchecked` note but de-duplicated anyway.
   - The model's default is the trap; the spec step neither pushes it there nor away.
2. Apart from t50, the arms have one genuine silent run each at most: S t49 and S\* t54.

## 8. Arms S and S+: did the approved draft contain a rule that would have caught it?

**No, in all 11 genuine S and S+ runs.** In each one the rule that would have caught the defect was written
as prose or left `Unchecked`. It was never written as a scenario.

| run | defect | what the approved draft had | what it missed |
|---|---|---|---|
| S t50 rep 2 | repeat | `Unchecked`: "Behaviour when order_ids repeats the same id … is unspecified." | any repeat scenario |
| S t50 rep 3 | repeat | `Unchecked`: "Whether a duplicated order id … is counted once or twice in the returned `cancelled` count is not specified." | same; it also entertained "twice" |
| S t50 rep 4 | repeat | nothing on repeats | same |
| S t50 rep 5 | repeat | `Unchecked`: "…whether the second occurrence is rejected as already cancelled, or counted again…" | same |
| S t49 rep 5 | admin 403 body | scenarios for another buyer (dave) and a vendor (vicky), both with the stated body; `Unchecked`: "Admins are not granted special access … only the buyer … may remove one of its lines." | an admin step. The rule was written, but only as prose; vicky holds `VIEW_OWN_ORDERS`, so the vendor scenario passed. |
| S+ t50 reps 0–4 | repeat | `Unchecked` naming both readings (counted once, or the second listing refused) | any repeat scenario |
| S+ t50 rep 5 | repeat | a scenario for the refused reading (400, writes nothing), **rejected** by the reference; the approved remainder has nothing on repeats | the collapsed reading. The only rule drafted for it was the other valid reading, so the ticket does not decide between them. |

S t52 rep 4 is (b), so there was nothing for its draft to catch.

The pattern repeats Phase 1's: **the drafts recognised the open edge and deferred it.** That deferral is
correct by the drafting rules, since the text leaves the answer open. But the deferral also drops the part
the text does fix, which is no second restock. A step `[7, 7]` → `expect_db` mug 41, with no assertion on
the status, would have caught all 16 runs under either reading. None of the 18 drafts or the owner's delta
wrote it.

## 9. Arm S+: why the self-review failed in 42 of 72 runs

`spec_review_failed` is set when the review's revision does not validate. The review gets no repair round
(pre-registered), so the first draft is kept. All 42 `review/feedback.txt` files read "Your SpecDelta is not
valid:", followed by:

| cause | runs | entries |
|---|---:|---:|
| **S016:** an `Unchecked.concerns` entry names something the spec does not know | **39** | 60 |
| of which the concern is the new endpoint's `"METHOD /path"` | 39 | 59 |
| of which the concern is a schema class (`VendorRead`) | 1 | 1 |
| Python syntax error in the revision | **3** | 3 |
| of which "unexpected indent", line 1 | 1 (t48 rep 1) | |
| of which "unterminated string literal" | 2 (t47 rep 3, t50 rep 5) | |

The S016 mechanism works as follows.
- `lattice/spec/validate.py::_unchecked` knows tables, `Table.column`s, programs, programs' endpoints and
  contracts' endpoints.
- The pivot deltas have no programs or contracts (`pivot_blind` docstring: "Programs: none"), so a new
  endpoint such as `POST /orders/cancel-batch` is unknown.
- The validator's hint, "name a table, a Table.column, a program or an endpoint", invites exactly that
  string.

The first drafts make the same mistake almost every time. Their round-1 feedback contains S016 in 69 of 72
S+ runs and in 71 of 72 S runs, and the repair round fixes it.

The review then reintroduces it:
- in 15 of the 39 runs, it only re-added the endpoint to entries the repaired draft already had. One example
  is S+ t50 rep 0: `concerns=('Order.status',)` became `('Order.status', 'POST /orders/cancel-batch')`;
- in 24 runs, it added at least one new `Unchecked` entry with the endpoint as its concern. These were
  mostly the checklist's edge cases: an empty list, repeats and error precedence.

Of the 60 offending entries, 29 were carried over from the draft and 31 were new.

So the review fails for a mechanical reason, not for its content.
- Its revisions mostly added `Unchecked` prose, not scenarios. On t50, `drafted_after` equals
  `drafted_before` in every run, and no review added a repeat scenario.
- A one-line validator fix would have saved 39 of the 42 reviews: accept a delta's own endpoint, or make
  the hint say "an existing endpoint". So would a single repair round on the review.
- Per t50, a valid review would not have changed the outcome, because the revisions carried the same
  deferral.

### Haiku: 21 of 71 reviews failed

Haiku S+ has 71 runs that reached the spec step; the other 6 records are harness errors. All 21 failures
are invalid revisions, and **every one has an S016 on the new endpoint**:

| cause | runs | entries |
|---|---:|---:|
| S016: the concern is the new endpoint's `"METHOD /path"` | **21** | 29 |
| S016: the concern is `'admin role'`, alongside an endpoint S016 (t45 rep 4) | 1 | 1 |
| S008: the invariant `stock_stays_conserved` is the bare `True`, alongside an endpoint S016 (t53 rep 5) | 1 | 1 |
| Python syntax error | 0 | 0 |

- Of the 30 S016 entries, 9 were carried over from the draft and 21 were new. 15 runs added at least one new
  entry, and 6 runs only re-added the endpoint to entries the draft already had.
- The contrast with Sonnet is in the drafts. Only 19 of Haiku's 71 first drafts hit S016 (Sonnet: 69 of
  72). So for Haiku, the review itself mostly introduces the endpoint-as-concern mistake. Its failure rate
  is lower, 30% against Sonnet's 58%, but the cause is the same.

**Both models together: 63 of 143 reviews failed, and 60 of those 63 failures carry an endpoint S016.**
The remaining 3 are Sonnet's syntax errors. The validator fix or a review repair round, as proposed above,
would address 60 of the 63.

## 10. Haiku (arms S, S+, S*)

The results are in `bench/results/pivot_haiku-4-5_{S,S+,S*}.jsonl`. The steps and corrections are the same
as for Sonnet.

### 10.1 Re-run

**All 25 silent runs reproduce on exactly their recorded `failed_cases`:** S 10, S+ 9 and S\* 6. The log is
`.../scratchpad/expd/rerun_haiku.txt`. There are no t52-style surprises: no Haiku run fails an `auth_*` case.

### 10.2 Classification

| arm | ticket | rep | failing case(s) | reference vs candidate | class |
|---|---|---|---|---|---|
| S | t50 | 1–5 (5 runs) | repeated_id | 200 `{"cancelled": 1}`, mug 41 vs 200 `{"cancelled": 2}`, mug 42 | a |
| S+ | t50 | 0–4 (5 runs) | repeated_id | same | a |
| S\* | t50 | 0–5 (6 runs) | repeated_id | same | a |
| S | t47 | 0, 1, 4 | repeated_beyond_stock, empty_items, zero_quantity | hammer ×10 + ×6 (stock 15): 409 vs 200 with one line of 16; `items: []`: 422 vs 200 `{"lines": [], "items": 0, "total": 0.0}`; mug ×0: 422 vs 200 with a 0-unit line | a |
| S+ | t47 | 0 | repeated_product, repeated_beyond_stock, empty_items | candle ×2 + ×3: line quantity 5 vs **2**; hammer: 409 vs 200 with quantity **10** (only the first entry kept); `items: []`: 422 vs 200 | a |
| S+ | t47 | 1, 4 | repeated_beyond_stock | 409 vs 200 with one line of 16 | a |
| S+ | t47 | 2 | empty_items | 422 vs 200 | a |
| S | t52 | 2 | vendor_with_a_line | vicky on order 1: 403 "You do not have access to this order" vs 200 with the order | a |
| S | t52 | 4 | admin_any_order | the admin's vendor entries carry `lines` vs `"lines": null` (router comment: "Admins get summary view without lines") | a |

The requirements the genuine failures break:
- **t50:** as in §3.1. The Haiku candidates fail in exactly the same way, with the same diff.
- **t47, summed stock.** The ticket says "one line per product …" and "Not enough stock for the quantity asked: 409". The
  candidates' own answer is one hammer line of 16 units, which is more than the stock of 15. The quantity
  asked is the line's quantity, so the line contradicts the 200. This is the trap the ticket notes
  pre-registered ("its stock checked against the summed quantity").
- **t47, first entry only (S+ rep 0).** The ticket says "`items`: <total number of units>" and "one line per product, in the
  order the products first appear". The request asks for 5 candles, but the answer prices 2 and reports
  `items` 3 where the request holds 6 units.
- **t47, empty list and zero quantity.** The ticket says "taking the same body as `POST /orders`". Upstream's `OrderCreate`
  has `items: List[...] = Field(min_length=1)` and `quantity: int = Field(gt=0)`, so both are 422 by
  reference.
  - These cases are already `STATUS_ONLY`, so only status and database effects are compared.
  - Unlike t50's empty list, t47's empty list is determined by the body the ticket names, so it is not a
    candidate for `EXCLUDED`.
- **t52, vendor access.** The ticket says "with the same access as `GET /orders/{order_id}`". That route gives a vendor the
  403 on an order they did not place, even with a line in it.
- **t52, admin shape.** The ticket gives one answer shape for every caller, with `lines` in each vendor entry.

**Counts: Haiku S 10 (a) / 0 (b), S+ 9 / 0, S\* 6 / 0.** No further correction is needed, and C1 and C2
change no Haiku outcome (§6).

### 10.3 Failure mechanisms (Haiku, genuine)

A run is counted under every mechanism it shows.

| mechanism | S | S+ | S\* |
|---|---:|---:|---:|
| t50: repeated id restocked and counted per listing | 5 | 5 | 6 |
| t47: stock checked per entry, not against the summed quantity | 3 | 2 (reps 1, 4) | 0 |
| t47: repeated product keeps only the first entry's quantity | 0 | 1 (rep 0) | 0 |
| t47: body looser than `POST /orders` (empty list, or 0 quantity, accepted) | 3 (both) | 2 (empty: reps 0, 2) | 0 |
| t52: access widened to a vendor with a line in the order | 1 | 0 | 0 |
| t52: answer shape differs by role (admin without `lines`) | 1 | 0 | 0 |
| **silent runs** | **10** | **9** | **6** |

Per ticket: t50 accounts for 5/5/6, t47 for 3/4/0 and t52 for 2/0/0. S\* clears t47 and t52 in all 12 runs.
- The owner's delta asserts the summed-stock and body rules there, so the spec check repairs them.
- Every S and S+ t47/t52 silent run ended on its third round with `failure: check`. The leftover findings
  are all on base-spec endpoints (`B:GET /orders/me …`, `D:POST /orders:only_buyers_place_orders`), not on
  the ticket's own rules, so the repair budget went on collateral regressions.

### 10.4 S and S+: did the approved draft contain a rule that would have caught it?

**No, in all 19 S and S+ silent runs.** Each failing case lacks a scenario in the approved draft:

| run(s) | what the draft had | what it missed |
|---|---|---|
| S t50 reps 1–5 | 9–13 rules; nothing on repeats, not even `Unchecked` | any repeat rule |
| S+ t50 reps 0–4 | `Unchecked` on duplicates in all 5; rep 3 names `[7, 7]` ("counts the order once or rejects the request") | a scenario, with the same deferral as Sonnet |
| S t47 reps 0, 1, 4 | a de-duplication scenario (`t_quote_product_deduplication`, `…_duplicate_products_merged`, `…_deduplicates_products`), which the candidates pass | the summed-stock 409, the empty list and the 0 quantity. Rep 1's draft has no 422 scenario at all. |
| S+ t47 rep 0 | 422 for quantity 0, which passes; `Unchecked`: "Whether the same product appearing twice … should be combined into one line or listed separately" | the merge (deferred), the summed stock and the empty list |
| S+ t47 rep 1 | 422 for `items: []` and for quantity 0, both of which pass | summed stock |
| S+ t47 rep 2 | 422 for quantity 0, which passes; `Unchecked` on a repeated product | the empty list |
| S+ t47 rep 4 | 422 for the empty list and for quantity 0, plus `quote_duplicate_product_combines_and_preserves_first_appearance_order`, all of which pass | summed stock **beyond** the stock |
| S t52 rep 2 | `t_by_vendor_vendor_sees_order_with_their_product` (vendor → 200), **rejected** by the reference | nothing approved. The draft's wrong reading of "same access" was refused, and the candidate implemented it anyway. |
| S t52 rep 4 | an admin scenario whose vendor entries list `vendor_id`, `business_name` and `subtotal` but no `lines` (a subset match); two 403-body rules rejected | the full shape. The draft plausibly invited the "summary view". |

The pattern matches Sonnet's, with one addition. Where Haiku's S+ draft did write the scenario (empty list,
0 quantity), its candidate passed it: 6 such rules across reps 0, 1, 2 and 4, and every one held. Every
failing t47 case is one the draft left out.

## Files

- Re-run log: `.../scratchpad/expd/rerun.txt`.
- Corrections: `.../scratchpad/expd/corrections.py`; validation: `.../scratchpad/expd/validate.py`.
- Haiku re-run log: `.../scratchpad/expd/rerun_haiku.txt`.
- Rescoring: `.../scratchpad/expd/rescore_worker.py` (one process per ticket), `aggregate.py`; per-ticket results `rs_<ticket>.json` (all three modes, both models).

The scratchpad is `/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/b968f5d9-1ef1-48a9-8a2e-c878b4606ec7/scratchpad/expd/`.
