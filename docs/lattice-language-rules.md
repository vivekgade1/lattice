# Lattice — Language Rules

> Status: binding · 2026-09-28 · Applies to every change to the Lattice language, its checker, its IRs and its code targets. Companion to `docs/lattice-vision.md` (§3 design principles) and the semantics table in `lattice/targets.py`.

Lattice has more than one code target (Python today, Go next, others later). The risk with several targets is a slow slide towards the lowest common denominator: each new feature is cut down to what every target can do, until the language can express less than any of its targets. These rules prevent that. They also keep the guarantees, which are the reason Lattice exists.

## 1. The meaning comes first, then the targets

**R1. Lattice has one meaning, defined once.** Semantics are written in `lattice/targets.py` (the semantics table) and pinned by the shared semantics suite (`tests/semantics_v11/`). No target defines them. Python's behaviour is a reference for spelling and error text, never the definition.

**R2. Every target implements the full meaning, or rejects the program before code is generated.** A target may say "not available here yet" (a check-time diagnostic from `p8_target`). It may never run a program with a different result, whether that is a different value, a different error, a different order or a different fuel point.

## 2. When a restriction is allowed

**R3. A rule may apply to every target only when the targets would otherwise give different answers.** Examples: RE2-only regular expressions, insertion-ordered sets, 64-bit `Int`. A language-wide restriction therefore needs:
- a concrete input on which the targets diverge;
- proof that a portable meaning is impossible or unreasonably costly;
- a documented rewrite for programs that used the removed form.

**R4. Everything else is target-gated, never removed.** If only one target lacks a feature, reject it for that target (L890/L891/L892 style) and keep it everywhere else. A gate is a temporary debt, tracked in §5, not a language decision.

**R5. Never remove to simplify an emitter.** "It's hard to generate on target X" is a reason to gate for X or to implement it in X's runtime, not to take it out of the language.

## 3. How expressiveness grows

**R6. Grow with explicit, portable types and constructs, not host-language quirks.** When a restriction removes capability (for example, integers beyond 64 bits), the answer is a new portable construct (`BigInt`, `Decimal`) with one meaning on every target. The answer is never to expose one host language's behaviour.

**R7. Every new construct ships with, in the same change:**
1. its checker rule and diagnostic (with an exact fix hint);
2. its lowering into the IR;
3. semantics cases in the shared suite, run on **every** target;
4. either an implementation on every target or an explicit target gate with a tracking entry (§5);
5. differential tests at the gate level (Python ≡ Go on the same program).

A construct missing any of these is not part of the language.

**R8. Evidence decides what is added.** Constructs are added when benchmarks, imports or repair loops show that models or real code need them (vision §3). The same applies in reverse: a restriction must show the divergence it prevents (R3).

## 4. Guarantees

**R9. Guarantees are never traded for portability or expressiveness.** Types, ownership, bounds, declared errors, fuel and single-statement reads hold on every target. A target that cannot uphold a guarantee rejects the program; it doesn't run it with a weaker one.

**R10. Determinism is part of the meaning.** Iteration orders, float text, error messages and fuel exhaustion points are specified, so the same program gives the same answer on every run and every target. Anything left unspecified is a bug in the semantics table.

## 5. Governance

**R11. Every language-wide restriction and every target gate has an entry in the decision log below.** The entry records the rule, the divergence (R3) or the missing target work (R4), the rewrite, the removal condition, and the date.

**R12. Gates have exit conditions.** Each gate names what would lift it (for example, "Go natives: when a Go native interface exists"). A gate that has lasted more than two sub-projects is reviewed: implement it, or promote it to a documented language rule under R3.

**R13. Semantics changes are versioned.** A change to meaning bumps the semantics version (`SEMANTICS_VERSION`, the `# lattice: vX.Y` header), is listed in the version notes, and must be stricter-only (a program either behaves the same or is rejected at check time) unless the change fixes a divergence under R3.

**R14. The checks are automated.** CI must fail when:
- a construct has no semantics case on some target;
- a semantics case's result differs between targets;
- a `p8_target` gate has no decision-log entry;
- a new language-wide diagnostic appears without a decision-log entry.

## 6. Decision log

| Date | Kind | Rule | Why (divergence or missing work) | Rewrite | Lifted when |
|---|---|---|---|---|---|
| 2026-09-28 | Language rule (R3) | `Int` is signed 64-bit; overflow → R623 | Python ints are unbounded, Go's are 64-bit | a portable `BigInt` type (roadmap) | never; superseded by `BigInt` for large values |
| 2026-09-28 | Language rule (R3) | Regex is the RE2 subset (L926) | Go's regexp has no lookaround, backreferences, conditionals, possessive or atomic groups | two matches, a split, or a loop in a procedure | never |
| 2026-09-28 | Language rule (R3) | `\d \w \s \b` are ASCII | Python's `re` is Unicode-aware, RE2's is ASCII | an explicit character class | never |
| 2026-09-28 | Language rule (R3, R10) | Sets iterate in insertion order; set algebra is left operand first; `Counter` ties keep insertion order | Python's string hashing differs per process | — | never (determinism) |
| 2026-09-28 | Language rule (R3) | `xs[a:b] += ys` rejected (L913) | Slice assignment is already rejected; the augmented form had no portable meaning | `xs = xs[:a] + (xs[a:b] + ys) + xs[b:]` | never |
| 2026-09-28 | Target gate (R4) | Natives on Go (L890) | No Go native interface yet | compile for Python | a Go native interface exists |
| 2026-09-28 | Target gate (R4) | Tabular profile on Go (L891) | Tabular plan holds Python AST; no polars in Go | compile for Python | tabular plan lowered via SQL/DuckDB |
| 2026-09-28 | Target gate (R4) | T2 calls not yet on Go (L892), incl. `str()` of containers, non-literal `re.sub` replacements, non-literal `strftime` formats | Go runtime work outstanding | compile for Python | Go implements the call (tracked in `GO_T2_CALLS`) |
| 2026-09-28 | Known limitation | Unicode 15 (Go, x/text) vs 16 (Python 3.14) | Library versions | — | Go's x/text reaches Unicode 16 |

## 7. Recommendations (not yet in place)

1. **Add `BigInt` and `Decimal` as portable types** (R6) before any user needs integers beyond 64 bits or exact decimal money. Go has `math/big`; Python has `int` and `decimal`.
2. **Implement the R14 CI checks** as part of plan 5a's semantics suite (Task 6) and gate test (Task 13): one test that fails when any `p8_target` code has no decision-log row, and one that fails when a semantics case is missing on a target.
3. **Treat `lattice/targets.py` as the single source of truth** for both the semantics table and the gates. Generate the decision-log rows for gates from it, so the log can't drift from the code.
4. **Review gates at the end of each sub-project** (R12). Each review either lifts a gate or turns it into a language rule, and writes down which.
5. **Choose the next target by what it adds, not by its reach.** Before adding a third target, check its gaps against this log. A target that would need many new language-wide rules is a sign it should be gated or deferred, not accommodated.
