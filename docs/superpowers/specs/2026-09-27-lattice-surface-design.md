# Lattice — familiar surface, endpoint-scoped edits, import readiness (design)

> Status: APPROVED direction 2026-09-27 (the user: "use agentic graphs to design the recommendations and then do the rerun"). Evidence: thin-slice round 3 (`docs/results/2026-09-26-bench-smoke.md`). On held-out tickets, Lattice's losses were mostly **loud checker refusals of ordinary Python spellings**, plus truncation and import defects on unfamiliar apps.

## 1. Principle

"Familiar surface, strict core" becomes concrete: **the checker canonicalises, it does not reject, whenever a Python spelling has exactly one Lattice meaning.**
- Rejection stays only where a spelling is ambiguous or would weaken a guarantee.
- Normalisation happens before P1 on the parsed AST, as a pure AST→AST rewrite.
- Diagnostics keep the user's original spans.
- `canon` can print the canonical form for storage and diffs.

## 2. Normalisation table (T0 API and tabular programs)

| Python spelling | Canonical Lattice | Guarantee kept because |
|---|---|---|
| `row.title` / `t.title` where `t` names a table or row value, inside an op argument | `col("title")` | still resolved against the schema (L401) |
| `user.id` (already allowed) | unchanged | — |
| `a + b` where both are `Str` columns/params/literals | `fn.concat(a, b)` (left-folded) | typed after rewrite (L402 if not Str) |
| `a and b`, `a or b`, `not a` on Bool column expressions | `a & b`, `a \| b`, `~a` | Bool-only check (L403) unchanged |
| `x if c else y` | `when(c, then=x, otherwise=y)` | the same `when` typing |
| `s.lower()`, `.upper()`, `.strip()`, `.startswith(p)`, `len(s)`, `s in t` (substring), `abs(x)`, `round(x, n)` | `fn.lower(s)`, … `fn.contains(t, sub=s)` | typed signatures |
| `x is None` / `x is not None` | `is_null(x)` / `~is_null(x)` | — |
| `x == None` | `is_null(x)` (with an L-warning hint) | — |
| **several ops in one statement**: `page(sort(where(fetch(T), …), by=…), limit=n)` | split into single-assignment steps with fresh names (`_s1`, `_s2`, …) | P2/P3 run on the split form; the chaining ban was about readability, not soundness |
| `when(c, x, y)` positional | `when(c, then=x, otherwise=y)` | — |
| chained comparison `a < x < b` | `(a < x) & (x < b)` | — |

Still rejected, with hints: loops outside `@procedure` (hint → T1/T2), statement-level `if`, rebinding (L201), comprehensions in T0, method calls with no single meaning. Tabular programs get the same table where the ops exist.

## 3. Endpoint-scoped edits (arm L of the edit slice)

- Arm L may answer with **only the endpoints, procedures, records and steps it adds or changes**, plus an optional profile delta (new columns and response models as a small `profile` block). Everything is merged by name into the start programs: same name → replace, new name → append, and an explicit `# delete: <name>` directive removes one.
- Whole-file answers remain accepted.
- Arm P keeps whole files: its files are small and it never truncated. The prompt states the option for both arms: P may also return only changed functions, merged by name at module level. Keep the arms symmetric in capability.
- Truncation is the failure mode this targets. It gets reported per arm.

## 4. Import readiness

1. **Auth policy fidelity:** the imported app's answers to bad, expired or claimless tokens and unknown users must equal the original's. The policy is derived from the decode helper's actual raise sites, and the verifier's auth cases pin it. This fixes the round-3 t24 silent failure.
2. **Coverage:**
   - use a larger `max_tokens` (32k) with `effort=medium` for R1/R2 importer calls;
   - use per-route budgets instead of spending the repo budget first-come;
   - put cheaper routes first (by slice size);
   - retry only on non-truncated failures.
   - The live re-import of both held-out apps has a hard cap of $10.
3. The report states per ticket (t19–t30) whether its touched routes are lifted.

## 5. Benchmark hygiene

- **t14 and t17:** remove the over-specified cases (boundary cases on `current_password` bounds the ticket never stated; the missing-field case where the ticket is silent) or declare them as accepted alternatives. Record the change and re-score round 3 for those two tickets in the write-up.
- **Fresh held-out tickets t25–t30** (3 on the template, 3 on the held-out apps), written before any normalisation work is visible to their author, with the usual references, oracles and trap variants.

## 6. Rerun (round 4)

- Tickets t1–t30, 2 arms, 2 models, 3 reps: 360 runs. The estimate is shown first; the run only proceeds automatically at ≤ $30.
- Results are split four ways: tuned t1–t6, held-out template t7–t18, held-out apps t19–t24, fresh t25–t30.
- Reported: correct/loud/silent, cost per correct, truncation rate, first-round checker pass rate, and normalisations applied per run (how often the familiar surface was used).
