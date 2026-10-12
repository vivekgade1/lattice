# Phase 1 oracle review: Sonnet (arms P, Q, S; S* rescored)

Pre-registration: `docs/archive/superpowers/specs/2026-10-04-phase1-loop-experiment.md` ("Oracle review", decision rule 6),
in the frozen checkout `lattice-phase1-frozen` at `7574876`. Precedent: option 1 main experiment's oracle review,
`docs/results/2026-09-26-bench-smoke.md`. The rule applied is the project's: **the oracle never compares what a
ticket's text doesn't state.**

No model or API calls were made, `.env` was never read, and the oracle was not edited. The correction was
applied in memory, by rebinding `bench.edit_slice.repos.multivendor.STATUS_ONLY`, which `canon` reads at
call time. A corrected copy of the module is in the session scratchpad
(`.../scratchpad/review/oracle_corrected/multivendor.py`).

## 1. Re-run

Every silent run's final candidate was rebuilt from its highest `round<k>/files/` with
`bench.edit_slice.rescore` (`_final_round_files`, `_candidate_factory`), then evaluated with
`bench.edit_slice.oracle.evaluate` under the frozen oracle.

**All 40 reproduce: 40/40 are still silent, on exactly the recorded `failed_cases`.** That is P 16, Q 15
and S 9. S\* has no silent runs. Each candidate's diff against `bench/import_repos/multivendor/app` was read,
along with the expected and actual answers of each failing case.

## 2. Classification of every silent run

Notation: (a) is genuine, (b) is an oracle defect. In each failing case the reference's answer is listed
first.

| arm | ticket | rep | failing case(s) | reference vs candidate | class |
|---|---|---|---|---|---|
| P | t36 | 3 | vendor_someone_elses | 403 "You do not have access to this order" vs 403 "You do not have permission to cancel orders"; database equal | **b** |
| P | t36 | 4 | vendor_someone_elses | same, candidate "...permission to perform this action." | **b** |
| P | t36 | 5 | another_buyers_pending, another_buyers_shipped, vendor_someone_elses | 403 access message vs 403 "You do not have permission to cancel this order"; database equal | **b** |
| P | t38 | 0–5 (6 runs) | not_enough_stock_writes_nothing | 409 in both, but the candidate leaves order 10 (pending, total 0) and the candle's stock at 29 (it should be 30) | a |
| P | t39 | 0–5 (6 runs) | zero_price_anywhere | 422 vs 200 `{"updated": 2}`, candle repriced to 0; body model `price: float`, unbounded | a |
| P | t43 | 5 | zero_price | 422 vs 201, vendor and product at price 0 created; `price: float` | a |
| Q | t36 | 5 | vendor_someone_elses | 403 access message vs 403 "You do not have permission to perform this action."; database equal | **b** |
| Q | t39 | 0–5 (6 runs) | zero_price_anywhere | 422 vs 200; body model `Field(ge=0)` | a |
| Q | t40 | 0, 1, 3 | admin_is_not_the_buyer | 403 "You do not have access to this order" vs 403 "You do not have permission to perform this action." (route guarded by `require_permission(PLACE_ORDER)`) | a |
| Q | t41 | 5 | vicky_closes, victor_closes_multi_vendor_order | the cancelled order 8 (candle ×1 from Vicky, hammer ×2 from Victor) restocks only the closing vendor's lines: hammer 17 vs 15, candle 31 vs 30 | a |
| Q | t43 | 0, 1, 2, 4 | zero_price | 422 vs 201; `Field(ge=0[, le=…])` | a |
| S | t36 | 1, 2, 5 | vendor_someone_elses | 403 access message vs 403 role-permission message; database equal | **b** |
| S | t39 | 4 | zero_price_anywhere | 422 vs 200; `Field(ge=0, le=1_000_000)` | a |
| S | t39 | 5 | vendor_role_without_profile | 403 "You do not have a vendor profile" vs 403 "You do not own this product" | a (see note) |
| S | t43 | 0, 1 | two_products, names_differ_by_case, zero_price | product objects lack `version`; price 0 accepted (`ge=0`) | a |
| S | t43 | 2, 5 | zero_price | 422 vs 201; `Field(ge=0…)` | a |

### The requirement each genuine failure breaks

- **t38, partial write.** "If a product doesn't have enough stock, answer 409 as `POST /orders` does." A 409
  refuses the reorder, yet the candidate leaves a new order and a decremented stock behind. All six candidates
  route through upstream's committing helpers (`create_order` and `decrement_stock_optimistic`, called
  directly or through a refactored `_place_order_lines`).
  - Caveat, as in the pilot: upstream `POST /orders` itself has this partial write.
  - The precedent ruled it genuine, and this review keeps that ruling.
- **t39, price 0.** "Each listed product gets its new price, exactly as if it had been changed with
  `PATCH /products/{product_id}`". `PATCH`'s `ProductUpdate.price` is `Field(gt=0)`.
- **t43, price 0.** "creates the listed products for the new shop, exactly as `POST /products` would". There,
  `ProductCreate.price` is `Field(gt=0)`.
- **t43, missing `version`.** "products: [<each product, as GET /products/{product_id} shows it>]".
  `ProductRead` includes `version`, and both candidates declared their own response model without it. The
  precedent ruled the same way.
- **t40, admin.** "Only the buyer who placed the order can change it (anyone else gets 403 \"You do not have
  access to this order\" …)". The message is stated, and the candidate gives another one.
- **t41, restock.** "every pending order containing their products is cancelled (its stock goes back, as when
  an order is cancelled)". The order's stock means every line, the other vendor's included. The candidate
  restocks only the closing vendor's own lines; its comment reads "Restock only this vendor's own products".
- **t39 S rep 5, vendor role without a profile.** "Same access rules and errors as `PATCH /products/{product_id}`".
  - `PATCH` (`update_own_product`) answers 403 "You do not have a vendor profile" to this caller. The candidate
    replaced that answer with "You do not own this product". Its comment reads "A caller without a vendor
    profile owns no products".
  - **Note:** the message is stated by reference ("errors as PATCH"), not literally. The text then lists only the
    404 and the "own this product" 403. I count it as genuine because "same … errors as PATCH" states the whole
    error set. It is the only borderline call. If it were ruled (b), S would drop by one more silent run, to 5.

### The defect: t36's error bodies

The t36 text reads, in full on access:

> "A buyer can cancel their own order while it is pending or confirmed; admins can cancel any order."

It states no message for a caller who may not cancel. The cases `t36.another_buyers_pending`,
`t36.another_buyers_shipped` and `t36.vendor_someone_elses` still compare the reference's body, "You do not
have access to this order". The ticket notes call this "the usual 403", but that is held-out knowledge and
not in the text.

- In all 7 runs the candidate answers **403 with the database unchanged**, as the reference does. Only the
  body differs, and every one is a role or permission message.
- The pilot fixed the same over-specification on the same ticket: `t36.own_shipped`, `own_delivered` and
  `own_already_cancelled` were made status-only because "the ticket never gives the message". The 403 cases
  were missed then because no pilot run failed them.
- S rep 2's drafted spec shows the ambiguity directly. Its scenario expecting the role-permission 403 body for
  a vendor was rejected by the reference.

## 3. Classification counts

| arm | silent | (a) genuine | (b) oracle defect |
|---|---:|---:|---:|
| P | 16 | 13 | 3 (t36) |
| Q | 15 | 14 | 1 (t36) |
| S | 9 | 6 | 3 (t36) |
| total | 40 | 33 | 7 |

## 4. Proposed oracle correction (not applied to the repository)

**Required.** Add these 3 cases to `STATUS_ONLY` in `bench/edit_slice/repos/multivendor.py`:

```python
"t36.another_buyers_pending", "t36.another_buyers_shipped", "t36.vendor_someone_elses",
```

Justification: t36's text gives no message for a caller who is neither the order's buyer nor an admin. Status
and database effects stay compared. This is the pilot's own rule for t36's other refusals.

**Recommended for consistency (latent: no Sonnet run fails them).** These cases compare a body their ticket
never states either:

```python
"t36.unknown_order",                      # t36 states no 404 message
"t38.another_buyers_order", "t38.unknown_order",
"t38.vendor_forbidden", "t38.admin_forbidden",   # t38: "Only buyers can reorder, and only their own orders" -- no messages
```

The rescoring was run with both sets. The latent additions change no Sonnet outcome, because they are
identical to the required set alone on every run. **Haiku was not rescored.** The correction applies to
Haiku's t36 runs as well, so H4 needs the same rescoring.

No other case qualifies. t37, t39 and t41 inherit their errors by reference ("same access rules as PATCH",
"same access as GET /vendors/me"); t40, t42 and t44 state theirs; and t43 states its own and `POST /vendors`'.

## 5. Rescored outcomes (offline, no model calls)

A STATUS_ONLY entry changes only the cases it names. Every t36 and t38 run of all four arms, 48 runs, was
re-evaluated under the original oracle, under the required correction (C1) and under C1 plus the latent
cases (C2). Runs on other tickets cannot change.

- **The original oracle reproduces all 48 recorded outcomes.**
- **C1 and C2 give the same result:** exactly the 7 (b) runs flip from silent to correct. No correct or loud
  run changes. S\*'s one t36 loud run (rep 2) stays loud.

| arm | correct (pre-reg → corrected) | silent | loud |
|---|---|---|---|
| P | 67 → **70** | 16 → **13** | 1 |
| Q | 67 → **68** | 15 → **14** | 2 |
| S | 74 → **77** | 9 → **6** | 1 |
| S\* | 80 → 80 | 0 → 0 | 4 |

`phase1_analysis` was re-run on the rescored copies, which are in the scratchpad under `review/rescored/`.
As the pre-registration requires, both verdicts are reported:

| verdict | pre-registered | corrected |
|---|---|---|
| H1, S vs P | NOT CONFIRMED (Fisher p = 0.0963; guard 88.1% ≥ 79.8%; spec bar 5.1%) | NOT CONFIRMED (p = 0.0712; guard 91.7% ≥ 83.3%; spec bar 5.1%) |
| H2, S vs Q | NOT CONFIRMED (p = 0.1351; sign 3/1) | **CONFIRMED** (p = 0.0467; sign 3/0) |
| H3, S\* vs S | descriptive: S\* 80 correct / 0 silent, S 74 / 9 | S\* 80 / 0, S 77 / 6 |

**The correction flips H2.** The precedent is option 1b, where a post-hoc correction also flipped the
decision. The flip rests on a post-hoc change and on a p-value just under 0.05, so the pre-registered NOT
CONFIRMED has to be reported alongside it.

## 6. Failure mechanisms

The table covers genuine runs only, after the correction. A run is counted under every mechanism it shows,
so S t43 reps 0 and 1 appear twice.

| mechanism | P | Q | S | S\* |
|---|---:|---:|---:|---:|
| partial write on a refused request (committing helper reused) | 6 (t38) | 0 | 0 | 0 |
| price ≤ 0 accepted: bound missing (`price: float`) | 7 (t39 ×6, t43 ×1) | 0 | 0 | 0 |
| price ≤ 0 accepted: wrong bound (`ge=0` instead of `gt=0`) | 0 | 10 (t39 ×6, t43 ×4) | 5 (t39 ×1, t43 ×4) | 0 |
| response shape: product `version` missing | 0 | 0 | 2 (t43) | 0 |
| stated error body replaced by a role or permission check | 0 | 3 (t40 admin) | 1 (t39 vendor without profile) | 0 |
| incomplete side effect: only the vendor's own lines restocked | 0 | 1 (t41) | 0 | 0 |
| *(oracle defect) unstated t36 error body* | *3* | *1* | *3* | 0 |
| **silent runs, genuine** | **13** | **14** | **6** | **0** |

Per ticket, all silent runs as pre-registered, with (b) runs in brackets:

| ticket | P | Q | S | S\* |
|---|---:|---:|---:|---:|
| t36 | 3 [3b] | 1 [1b] | 3 [3b] | 0 |
| t38 | 6 | 0 | 0 | 0 |
| t39 | 6 | 6 | 2 | 0 |
| t40 | 0 | 3 | 0 | 0 |
| t41 | 0 | 1 | 0 | 0 |
| t43 | 1 | 4 | 4 | 0 |
| t31–t35, t37, t42, t44 | 0 | 0 | 0 | 0 |

Observations:

1. **Partial writes are P-only**, as in option 1: 6/6 runs on t38, against 0 in Q, S and S\*.
2. **Price 0 is the dominant genuine failure in every arm,** but its form differs by arm:
   - every failing P candidate left `price` unbounded;
   - every failing Q and S candidate declared a bound, and chose `ge=0`.
   - The arms still differ in count: S has 5 such runs, against 10 in Q and 7 in P.
3. **The remaining genuine failures are a stated message overridden by a role check** (Q t40 ×3, S t39 ×1),
   **one missing field** (S t43 ×2) **and one incomplete cascade** (Q t41).

## 7. Arm S: did the approved draft contain a rule that would have caught the defect?

**No, in all 9 runs.** Every final candidate passed its own spec check (`final_check_findings` = 0, no
repair round fed back spec findings), so by construction no approved rule caught the defect. Every draft
was approved in full except t36 rep 2.

| run | defect | what the draft had | what it missed |
|---|---|---|---|
| t36 rep 1 | (b) | refusals of non-owners and vendors left `Unchecked` ("The ticket does not name the status code") | nothing genuine to miss |
| t36 rep 2 | (b) | drafted "vendor cannot cancel" with the role-permission body, **rejected** by the reference (1 of 5 rules) | nothing genuine to miss; the rejected rule matched the candidate, which shows the ticket does not determine the body |
| t36 rep 5 | (b) | `buyer_cannot_cancel_others_order` and `vendor_cannot_cancel_order`, both `expect_status=403` with no body | nothing genuine to miss |
| t39 rep 4 | price 0 | an invalid-body scenario with the wrong key only (`{"items": []}` → 422); `Unchecked`: "Whether a price outside the bounds PATCH accepts (for example a zero or negative price) is refused with the same 422 as PATCH." | the price-0 boundary, which it named and deferred |
| t39 rep 5 | vendor role without profile | `vendor_with_no_products_cannot_reprice` for vera **with** a profile, expecting "You do not own this product" (correct on the seed); a zero-price scenario, which the candidate passes (`gt=0`) | the vendorless state. No route deletes a vendor profile or sets the role alone, so no HTTP scenario from the seed can reach it, and the oracle needs a database variant. The draft's vera scenario plausibly invited the candidate's "no profile owns nothing" generalisation. |
| t43 rep 0 | price 0 + `version` | an invalid product at price **−1** → 422 (`ge=0` passes it); response scenarios list product fields without `version` (subset match); `Unchecked`: "this delta only tests a negative price" | the boundary (0, not −1); the full product shape |
| t43 rep 1 | price 0 + `version` | an invalid product with missing fields only → 422; `Unchecked`: "price and stock bounds follow POST /products; the spec checks only a missing field", and "checks only the fields named above" | both, explicitly deferred |
| t43 rep 2 | price 0 | no invalid-product scenario; `Unchecked` defers "price and stock bounds" to `POST /products` | the price bound |
| t43 rep 5 | price 0 | invalid body = missing `business_name` → 422 only | the price bound |

The pattern: the drafts **knew the field rules were inherited**, and said so in `Unchecked`, but wrote
no boundary scenario for them.

- In the 3 S runs whose draft had a price-0 scenario (t39 reps 2, 3 and 5; t43 rep 3), the candidate got the
  bound right.
- Of the 8 runs without one, 5 shipped `ge=0`.
- Expected bodies in scenarios use a subset match, so a response-shape omission such as `version` cannot be
  caught unless the draft lists the field.
