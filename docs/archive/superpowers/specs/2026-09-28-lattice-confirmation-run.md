# Confirmation run: Lattice vs Python on silent bugs (pre-registered)

> Status: PRE-REGISTERED 2026-09-28, before any run. This follows round 5 (`docs/results/2026-09-26-bench-smoke.md`, "Round 5"), whose rule gave MIXED. That round was confounded by the unfaithful JustNotes import and had only 3 reps.

## Questions

- **Q1 (primary):** with a strong model (claude-sonnet-5), do LLM edits made through Lattice (arm L) produce **fewer silent failures** than direct Python edits (arm P)?
- **Q2 (secondary):** with a weaker model (claude-haiku-4-5), does editing Python with Lattice's checks plus aids (arm Q+) beat plain Python (arm P) on both correctness and silent failures?

## Scope

- **Tickets:** the 15 template tickets that were not tuned on and whose start states are verified faithful: t7–t18 and t25–t27. No held-out-app tickets, so the JustNotes confound is excluded.
- **Arms:**
  - Sonnet: P, L, Q, Q+ (Q and Q+ for context).
  - Haiku: P, L, Q+.
- **Reps:** 6 per ticket per arm, which gives 90 runs per cell.
- **Total:** 15 × (4 + 3) × 6 = **630 runs**.
- **Setup:** everything is identical to round 5: the Claude CLI transport, the same prompts, the same aids, a repair budget of 2, and the code at the commit that this spec is committed with.
- **Data:** only this run's data is used. Round 5 data is not pooled in.

## Tests and decision (fixed now)

- **Unit:** a run, scored correct/silent/loud by the hidden oracle, as in every round.
- **Primary test (Q1):** a one-sided Fisher exact test on silent vs not-silent runs, arm L vs arm P, Sonnet. It is backed by a per-ticket paired sign test on silent counts (ties dropped), which guards against ticket clustering.
  - **Confirmed "Lattice reduces silent bugs"** only if the Fisher p < 0.05 AND the sign test agrees in direction.
  - **Refuted** if L's silent rate ≥ P's silent rate.
  - Otherwise, **not confirmed**.
- **Secondary test (Q2):**
  - one-sided Fisher on silent runs, Q+ < P, on Haiku;
  - and one-sided Fisher on correct runs, Q+ > P, on Haiku.
  - **Confirmed** only if both p < 0.05.
- **Also reported:** correct rate, loud rate, cost per correct, and Wilson 95% intervals for each rate.

## What each outcome means for the direction

| Outcome | Meaning |
|---|---|
| Q1 confirmed | Lattice keeps a real safety advantage on strong models. Revisit the "language vs checks" decision with that in hand. |
| Q1 refuted or not confirmed, and Q2 confirmed | Reposition around the checker plus verified-edit loop on ordinary code, with Lattice's IR internal. |
| Neither confirmed | The measured value of both is small at this scale. Report that honestly before investing further. |
